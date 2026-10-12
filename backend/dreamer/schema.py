import base64
import io
from typing import Annotated, Literal

from PIL import Image
from pydantic import BaseModel, ConfigDict, Field, model_validator

Number = Annotated[float, Field(allow_inf_nan=False, ge=-2000, le=2000)]
Positive = Annotated[float, Field(gt=0, le=1000, allow_inf_nan=False)]
Vector = tuple[Number, Number, Number]


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Operation(StrictModel):
    id: str = Field(pattern=r"^[a-zA-Z][a-zA-Z0-9_]{0,39}$")
    op: Literal[
        "box",
        "cylinder",
        "sphere",
        "polygon",
        "union",
        "cut",
        "intersect",
        "translate",
        "rotate",
        "fillet",
        "chamfer",
    ]
    inputs: list[str] = Field(default_factory=list, max_length=16)
    size: tuple[Positive, Positive, Positive] | None = None
    radius: Positive | None = None
    height: Positive | None = None
    points: list[tuple[Number, Number]] = Field(default_factory=list, max_length=64)
    vector: Vector = (0, 0, 0)
    angle: Number = 0
    edges: Literal["all", "|Z", "|X", "|Y", ">Z", "<Z"] = "all"

    @model_validator(mode="after")
    def check_parameters(self):
        counts = {
            "box": 0,
            "cylinder": 0,
            "sphere": 0,
            "polygon": 0,
            "translate": 1,
            "rotate": 1,
            "fillet": 1,
            "chamfer": 1,
            "cut": 2,
            "intersect": 2,
        }
        if self.op == "union":
            if len(self.inputs) < 2:
                raise ValueError("union needs at least two inputs")
        elif len(self.inputs) != counts[self.op]:
            raise ValueError(f"{self.op} needs {counts[self.op]} inputs")
        required = {
            "box": ["size"],
            "cylinder": ["radius", "height"],
            "sphere": ["radius"],
            "polygon": ["height"],
            "fillet": ["radius"],
            "chamfer": ["radius"],
        }
        for key in required.get(self.op, []):
            if getattr(self, key) is None:
                raise ValueError(f"{self.op} requires {key}")
        if self.op == "polygon" and len(self.points) < 3:
            raise ValueError("polygon needs at least three points")
        if self.op == "rotate" and self.vector == (0, 0, 0):
            raise ValueError("rotation axis must be nonzero")
        return self


class CADPlan(StrictModel):
    name: str = Field(min_length=1, max_length=100)
    description: str = Field(max_length=2000)
    assumptions: list[str] = Field(default_factory=list, max_length=20)
    operations: list[Operation] = Field(min_length=1, max_length=80)
    result: str

    @model_validator(mode="after")
    def check_graph(self):
        seen = set()
        for op in self.operations:
            if op.id in seen or any(ref not in seen for ref in op.inputs):
                raise ValueError(
                    "Operation IDs must be unique and inputs must refer to earlier operations"
                )
            seen.add(op.id)
        if self.result not in seen:
            raise ValueError("Result must reference an operation")
        return self


class GenerateRequest(StrictModel):
    prompt: str = Field(min_length=1, max_length=8000)
    images: list[str] = Field(default_factory=list, max_length=3)
    parent_id: str | None = Field(default=None, pattern=r"^[a-f0-9]{32}$")
    demo_template: Literal["organizer", "bracket", "planter"] | None = None

    @model_validator(mode="after")
    def validate_images(self):
        if not self.prompt.strip():
            raise ValueError("Prompt cannot be blank")
        for image in self.images:
            if len(image) > 7_000_000:
                raise ValueError("Each image must be smaller than 5 MB")
            try:
                header, data = image.split(",", 1)
                expected = {
                    "data:image/png;base64": "PNG",
                    "data:image/jpeg;base64": "JPEG",
                    "data:image/webp;base64": "WEBP",
                }[header]
                raw = base64.b64decode(data, validate=True)
                if len(raw) > 5_000_000:
                    raise ValueError("Image too large")
                with Image.open(io.BytesIO(raw)) as decoded:
                    if (
                        decoded.format != expected
                        or decoded.width * decoded.height > 16_000_000
                    ):
                        raise ValueError("Invalid image format or dimensions")
                    decoded.verify()
            except Exception as exc:
                raise ValueError(
                    "Provide a valid PNG, JPEG or WebP image under 5 MB and 16 megapixels"
                ) from exc
        if self.demo_template and (self.images or self.parent_id):
            raise ValueError(
                "Demo templates do not interpret images or revisions; configure an LLM"
            )
        return self


class Approval(StrictModel):
    confirmed: Literal[True]
    artifact_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
