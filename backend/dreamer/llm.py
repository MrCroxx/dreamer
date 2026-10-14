import asyncio
import json
from collections.abc import Awaitable, Callable

import httpx

from .config import Settings
from .schema import CADPlan, GenerateRequest, Operation
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
Use only the supplied schema; return one JSON object, no Markdown. All dimensions are millimeters.
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
translate applies vector; rotate uses vector as axis through origin and angle in degrees.
Inputs reference earlier node IDs. cut subtracts the second solid from the first.
Output exactly one connected solid. Use overlapping geometry for unions, sensible wall thickness,
and conservative fillets. Result will be centered on the build plate and lowered to Z=0.
Respect requested dimensions. Record inferred dimensions and design limitations in assumptions.
Images are references, not exact dimensional measurements. Do not pretend to know missing measurements.
Choose operations that express the intended shape directly: loft for changing cross-sections,
revolve for axial symmetry, sweep for routed rods, shell for open housings. Prefer fewer
meaningful operations over stacks of primitives. Preserve the chosen construction and
unaffected dimensions when revising a previous plan. If shell fails on a curved body,
use a separately dimensioned inner loft or revolved wall profile rather than thinning blindly.
Do not claim this bounded CAD vocabulary can reproduce organic sculptures exactly.
Do not output executable code or file paths.
"""
    instructions += f"\nUsable build volume: {settings.build_volume}.\nJSON schema: {json.dumps(CADPlan.model_json_schema())}"
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
                + json.dumps(previous),
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
                return CADPlan.model_validate_json(parser.text)
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
                plan = CADPlan.model_validate_json(text)
            except (KeyError, IndexError, TypeError) as exc:
                raise ValueError(
                    "LLM response did not contain a CAD JSON plan"
                ) from exc
            if on_operation:
                for operation in plan.operations:
                    await on_operation(operation)
            return plan
