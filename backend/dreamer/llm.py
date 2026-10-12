import json

import httpx

from .config import Settings
from .schema import CADPlan, GenerateRequest


async def generate_plan(
    settings: Settings,
    request: GenerateRequest,
    previous: dict | None,
    repair: str | None = None,
) -> CADPlan:
    if not settings.llm_api_key or not settings.llm_model:
        raise ValueError(
            "Configure DREAMER_LLM_API_KEY and DREAMER_LLM_MODEL, or choose a demo template"
        )
    instructions = """You are a CAD engineer. Convert the user's request and optional reference images into a JSON CADPlan.
Use only the supplied schema; return one JSON object, no Markdown. All dimensions are millimeters.
Primitives are made on XY: box is centered in X/Y with bottom Z=0, cylinder extends from Z=0,
sphere is centered at the origin, polygon is an XY outline extruded along +Z.
translate applies vector; rotate uses vector as axis through origin and angle in degrees.
Inputs reference earlier node IDs. cut subtracts the second solid from the first.
Output exactly one connected solid. Use overlapping geometry for unions, sensible wall thickness,
and conservative fillets. Result will be centered on the build plate and lowered to Z=0.
Respect requested dimensions. Record inferred dimensions and design limitations in assumptions.
Images are references, not exact dimensional measurements. Do not pretend to know missing measurements.
Prefer simple functional printable parts. Do not output executable code or file paths.
"""
    instructions += f"\nUsable build volume: {settings.build_volume}.\nJSON schema: {json.dumps(CADPlan.model_json_schema())}"
    content = [{"type": "text", "text": request.prompt}]
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
    async with httpx.AsyncClient(
        timeout=httpx.Timeout(180, connect=10), trust_env=False
    ) as client:
        response = await client.post(
            settings.llm_base_url.rstrip("/") + "/chat/completions",
            headers={"Authorization": f"Bearer {settings.llm_api_key}"},
            json={
                "model": settings.llm_model,
                "messages": [
                    {"role": "system", "content": instructions},
                    {"role": "user", "content": content},
                ],
                "response_format": {"type": "json_object"},
            },
        )
        if response.is_error:
            raise ValueError(
                f"LLM returned HTTP {response.status_code}. Check the model alias, API credentials and vision/JSON support."
            )
        try:
            text = response.json()["choices"][0]["message"]["content"]
            if not isinstance(text, str):
                raise ValueError("Model returned no text")
            return CADPlan.model_validate_json(text)
        except (KeyError, IndexError, TypeError) as exc:
            raise ValueError("LLM response did not contain a CAD JSON plan") from exc
