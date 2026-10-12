"""Execute only validated CAD operations in a disposable, time-bounded process."""

import json
import sys
from pathlib import Path

import cadquery as cq

from .schema import CADPlan


def build(plan: CADPlan, directory: Path) -> dict:
    nodes = {}
    for op in plan.operations:
        inputs = [nodes[key] for key in op.inputs]
        match op.op:
            case "box":
                result = cq.Workplane("XY").box(*op.size, centered=(True, True, False))
            case "cylinder":
                result = cq.Workplane("XY").circle(op.radius).extrude(op.height)
            case "sphere":
                result = cq.Workplane("XY").sphere(op.radius)
            case "polygon":
                result = (
                    cq.Workplane("XY").polyline(op.points).close().extrude(op.height)
                )
            case "union":
                result = inputs[0]
                for other in inputs[1:]:
                    result = result.union(other)
            case "cut":
                result = inputs[0].cut(inputs[1])
            case "intersect":
                result = inputs[0].intersect(inputs[1])
            case "translate":
                result = inputs[0].translate(op.vector)
            case "rotate":
                result = inputs[0].rotate((0, 0, 0), op.vector, op.angle)
            case "fillet" | "chamfer":
                edges = (
                    inputs[0].edges()
                    if op.edges == "all"
                    else inputs[0].edges(op.edges)
                )
                result = (
                    edges.fillet(op.radius)
                    if op.op == "fillet"
                    else edges.chamfer(op.radius)
                )
        nodes[op.id] = result
    model = nodes[plan.result]
    solids = model.solids().vals()
    if len(solids) != 1 or not solids[0].isValid() or solids[0].Volume() <= 0:
        raise ValueError(
            "Model must contain exactly one connected, valid solid with positive volume"
        )
    bounds = solids[0].BoundingBox()
    model = model.translate(
        (
            -(bounds.xmin + bounds.xmax) / 2,
            -(bounds.ymin + bounds.ymax) / 2,
            -bounds.zmin,
        )
    )
    dimensions = [bounds.xlen, bounds.ylen, bounds.zlen]
    if max(dimensions) > 1000:
        raise ValueError("Model exceeds the 1000 mm modeling limit")
    cq.exporters.export(
        model, str(directory / "model.stl"), tolerance=0.05, angularTolerance=0.1
    )
    cq.exporters.export(model, str(directory / "model.step"))
    return {
        "dimensions": [round(n, 3) for n in dimensions],
        "volume_mm3": round(solids[0].Volume(), 3),
        "solids": 1,
    }


if __name__ == "__main__":
    directory = Path(sys.argv[1])
    try:
        plan = CADPlan.model_validate_json((directory / "plan.json").read_text())
        metadata = build(plan, directory)
        (directory / "geometry.json").write_text(json.dumps(metadata))
    except Exception as exc:
        print(f"CAD construction failed: {str(exc)[:1000]}", file=sys.stderr)
        sys.exit(1)
