import asyncio
import json
from collections.abc import Awaitable, Callable

import httpx

from .config import Settings
from .schema import AppearanceUpdate, CADPlan, GenerateRequest, Operation
from .streaming import PlanStream


def design_context(history: list[dict]) -> list[dict]:
    """Keep the original brief and recent requests within a bounded context budget."""
    if not history:
        return []

    def compact(turn):
        return {
            "request": turn["prompt"],
            "model_completed": bool(turn.get("model_sha256")),
        }

    root = compact(history[0])
    budget = 32_000 - len(json.dumps(root, ensure_ascii=False))
    recent = []
    for turn in reversed(history[1:]):
        entry = compact(turn)
        size = len(json.dumps(entry, ensure_ascii=False))
        if size > budget:
            break
        budget -= size
        recent.append(entry)
    return [root, *reversed(recent)]


async def generate_plan(
    settings: Settings,
    request: GenerateRequest,
    previous: dict | None,
    repair: str | None = None,
    on_operation: Callable[[Operation], Awaitable[None]] | None = None,
    on_output: Callable[[str, str], None] | None = None,
    history: list[dict] | None = None,
) -> CADPlan:
    if not settings.llm_api_key or not settings.llm_model:
        raise ValueError(
            "Configure DREAMER_LLM_API_KEY and DREAMER_LLM_MODEL, or choose a demo template"
        )
    instructions = """You are a CAD engineer. Convert the user's request and optional reference images into a JSON CADPlan.
Use only the supplied schema; return one JSON object, no Markdown. Omit unused optional fields. All dimensions are millimeters.
Primitives are made on XY: box is centered in X/Y with bottom Z=0, cylinder extends from Z=0,
sphere is centered at the origin, polygon is an XY outline extruded along +Z.
loft joins 2-12 closed XY sections at strictly increasing absolute z heights. Each section has
shape circle with radius OR rectangle with size [width, depth], and an optional XY center.
Use ruled=false for smooth transitions, true for straight transitions between sections.
revolve rotates a closed polygon of points [radius, z] around the global Z axis. All radii
must be nonnegative; set angle=360 for a full revolution, or 0<angle<=360 for a sector.
Use revolve for bowls, stepped shafts, flanges, and axisymmetric hollow parts. A wall profile
can include its inner and outer boundary in one closed outline; do not cross the rotation axis.
sweep creates a solid circular rod of radius along a polyline path of 2-32 absolute [x,y,z]
points, with round corner transitions. Avoid sharp reversals and self-intersections; a pipe
can be made by subtracting a smaller coaxial sweep with cutters extended beyond the ends.
shell removes faces selected by >Z, <Z, >X, <X, >Y, or <Y and offsets the remaining faces
inward by positive thickness. It has exactly one input. Start with >Z for an open-top vessel.
Wall thickness must be smaller than the local curvature radius and feature clearance.
extrude builds a solid from profiles on frame: origin, normal, x_direction (default XY).
profiles[0] is the outer boundary; subsequent profiles are holes strictly inside it.
A profile is circle(radius, center), rectangle(size=[width,depth], center), or path(start,
segments, optional center). Path segments have kind=line with end, kind=arc with exactly
one through point and end, or kind=spline with through points and end. The path closes
automatically to start. Use curves for logos, decorative outlines, routed slots and relief.
extrude uses height along frame.normal and optional taper in degrees. twist uses the same
profiles and height, twisting by angle (nonzero, up to +/-1440 degrees).
text creates separate solid glyphs from text, text_size, height, font (auto/sans/serif/mono/cjk),
and frame. Text is centered at the frame origin. auto supports Latin and CJK; avoid emoji.
Text or patterned tools can be disconnected intermediates; the FINAL result must be connected.
engrave and emboss each take one body input and EITHER text + text_size OR profiles, and depth.
They select a planar face facing faces (>Z/<Z/>X/<X/>Y/<Y), ordered outermost first, then largest
area; face_index selects from that order, default 0. Local coordinates start at that face's
bounding-box center. offset=[u,v] positions the mark, angle rotates it on the face.
Face axes (local X, local Y): >Z=(+X,+Y), <Z=(+X,-Y), >X=(+Y,+Z), <X=(-Y,+Z),
>Y=(-X,+Z), <Y=(+X,+Z). Depth is positive: engrave cuts inward, emboss grows outward.
All embossed features must touch the body. For a curved surface use explicitly positioned
text/extrude solids with cut/union; planar marks do not wrap or project onto curved surfaces.
linear_pattern repeats one input count times, including the original, spaced by vector.
polar_pattern repeats one input about the axis origin + vector over angle degrees. A full
360-degree pattern avoids a duplicate endpoint; partial arcs include both endpoints.
Patterns produce compound tools; cut a hole pattern from a base or union ribs into a body.
Use at most 64 instances per pattern and 256 intermediate solids. Avoid nested explosive arrays.
mirror reflects one input across the plane defined by frame.origin and frame.normal.
scale uniformly scales one input by factor around origin. split clips one input by frame,
keeping top (positive normal) or bottom (negative normal).
helix creates a helical feature around local Z using radius, pitch, height (up to 64 turns),
lefthand, and frame. Supply wire_radius for a round coil OR profiles for a thread/ridge section.
Thread profiles use radial/axial coordinates centered at [radius,0,0]; their axial size must be
less than pitch. Union a ridge into a shaft or cut a helical groove. These are geometric threads,
not certified standard fasteners: specify clearance, pitch, and end treatments explicitly.
sweep supports smooth=true for an interpolated 3D spline instead of a polyline.
translate applies vector; rotate uses vector as an axis direction through origin and angle
in degrees. fillet/chamfer support edge direction/type selectors or edge_point to choose the
edge nearest an absolute point. Apply local edge treatment before decorative patterns.
Inputs reference earlier node IDs. cut subtracts the second solid from the first.
Output exactly one connected solid. Use up to 160 operations, overlapping geometry for unions, sensible wall thickness,
and conservative fillets. Result will be centered on the build plate and lowered to Z=0.
Respect requested dimensions. Record inferred dimensions and design limitations in assumptions.
Images are references, not exact dimensional measurements. Do not pretend to know missing measurements.
Choose operations that express the intended shape directly: loft for changing cross-sections,
revolve for axial symmetry, sweep for routed rods, shell for open housings. Prefer fewer
meaningful operations over stacks of primitives. Preserve the chosen construction and
unaffected dimensions when revising a previous plan. If shell fails on a curved body,
use a separately dimensioned inner loft or revolved wall profile rather than thinning blindly.
For complex work, establish the primary body, subtract cavities and holes, then add details
and markings. Use reusable cutters and patterns instead of spelling out repeated primitives.
Use errors naming an operation to correct that feature and its dependents; preserve the brief
and unrelated geometry. Do not drop requested details just to make validation pass; explain
any necessary simplification in assumptions. Organic sculptures and photo reconstruction
cannot be reproduced exactly with these parametric operations.
Do not output executable code or file paths.
Set color to a six-digit hex RGB value (for example #2563eb) when the user requests
a color. This is a single preview color, not a filament or multi-material setting.
Preserve the previous color when changing geometry unless the user requests another.
When ONLY the preview color changes and a previous valid plan exists, return an
AppearanceUpdate object instead of repeating CAD operations. Use kind=appearance,
color, and a short description of the change. Never use an appearance update when
the user also requests geometry changes. Respond to the user in the description;
explain what changed concisely, without repeating the full brief.
"""
    instructions += """
Feature examples (input IDs must already exist):
{"id":"label","op":"engrave","inputs":["plate"],"text":"DREAMER","text_size":8,"depth":0.8,"faces":">Z","offset":[0,4]}
{"id":"slot","op":"extrude","height":6,"profiles":[{"shape":"path","start":[-5,0],"segments":[{"kind":"arc","through":[[0,5]],"end":[5,0]},{"end":[5,-3]},{"end":[-5,-3]}]}]}
{"id":"holes","op":"polar_pattern","inputs":["offset_hole"],"count":6,"vector":[0,0,1],"angle":360}
"""
    instructions += f"\nUsable build volume: {settings.build_volume}.\nJSON schema: {json.dumps(CADPlan.model_json_schema())}"
    instructions += "\nAlternative appearance-only JSON schema: " + json.dumps(
        AppearanceUpdate.model_json_schema()
    )

    def parse_plan(text: str) -> tuple[CADPlan, bool]:
        data = json.loads(text)
        if isinstance(data, dict) and data.get("kind") == "appearance":
            change = AppearanceUpdate.model_validate(data)
            if previous is None:
                raise ValueError(
                    "An appearance update requires an existing valid model"
                )
            return CADPlan.model_validate(previous).model_copy(
                update={"color": change.color, "description": change.description}
            ), True
        plan = CADPlan.model_validate(data)
        if plan.color is None and previous:
            plan.color = previous.get("color")
        return plan, False

    content = [{"type": "text", "text": request.prompt}]
    if history:
        content.append(
            {
                "type": "text",
                "text": "Earlier design requests, oldest first (some middle turns may be omitted). The current request overrides conflicting earlier instructions. Unbuilt requests are intent, not existing geometry:\n"
                + json.dumps(design_context(history), ensure_ascii=False),
            }
        )
    if previous:
        content.append(
            {
                "type": "text",
                "text": "Revise this previous plan, preserving unspecified details:\n"
                + CADPlan.model_validate(previous).model_dump_json(
                    exclude_defaults=True, exclude_none=True
                ),
            }
        )
    if repair:
        content.append(
            {
                "type": "text",
                "text": "The previous attempt failed validation or construction. Correct it:\n"
                + repair[:30000],
            }
        )
    content.extend(
        {"type": "image_url", "image_url": {"url": image}} for image in request.images
    )
    async with (
        asyncio.timeout(180),
        httpx.AsyncClient(
            timeout=httpx.Timeout(180, connect=10), trust_env=False
        ) as client,
    ):
        async with client.stream(
            "POST",
            settings.llm_base_url.rstrip("/") + "/chat/completions",
            headers={"Authorization": f"Bearer {settings.llm_api_key}"},
            json={
                "model": settings.llm_model,
                "messages": [
                    {"role": "system", "content": instructions},
                    {"role": "user", "content": content},
                ],
                "response_format": {"type": "json_object"},
                "stream": settings.llm_stream,
            },
        ) as response:
            if response.is_error:
                raise ValueError(
                    f"LLM returned HTTP {response.status_code}. Check model credentials and JSON/streaming support."
                )
            parser = PlanStream()
            if "text/event-stream" in response.headers.get("content-type", ""):
                completed = False
                async for line in response.aiter_lines():
                    if not line.startswith("data:"):
                        continue
                    data = line[5:].strip()
                    if data == "[DONE]":
                        completed = True
                        break
                    chunk = json.loads(data)
                    if chunk.get("error"):
                        raise ValueError("The model stream reported an error")
                    choices = chunk.get("choices") or []
                    if not choices:
                        continue
                    choice = choices[0]
                    delta = choice.get("delta") or {}
                    if on_output:
                        reasoning = delta.get("reasoning_content") or delta.get(
                            "reasoning"
                        )
                        if isinstance(reasoning, str) and reasoning:
                            on_output("reasoning", reasoning)
                        if isinstance(delta.get("content"), str) and delta["content"]:
                            on_output("content", delta["content"])
                    reason = choice.get("finish_reason")
                    if reason and reason != "stop":
                        raise ValueError("The model response was incomplete or refused")
                    completed = completed or reason == "stop"
                    fragment = delta.get("content") or ""
                    if not isinstance(fragment, str):
                        raise ValueError("Invalid model content chunk")
                    for operation in parser.feed(fragment):
                        if on_operation:
                            await on_operation(operation)
                if not completed:
                    raise ValueError("The model stream ended before completion")
                return parse_plan(parser.text)[0]
            await response.aread()
            try:
                message = response.json()["choices"][0]["message"]
                text = message["content"]
                if on_output:
                    reasoning = message.get("reasoning_content") or message.get(
                        "reasoning"
                    )
                    if isinstance(reasoning, str) and reasoning:
                        on_output("reasoning", reasoning)
                    if isinstance(text, str):
                        on_output("content", text)
                plan, appearance_only = parse_plan(text)
            except (KeyError, IndexError, TypeError) as exc:
                raise ValueError(
                    "LLM response did not contain a CAD JSON plan"
                ) from exc
            if on_operation and not appearance_only:
                for operation in plan.operations:
                    await on_operation(operation)
            return plan
