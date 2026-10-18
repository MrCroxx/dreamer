"""Execute only validated CAD operations in a disposable, time-bounded process."""

import json
import sys
import time
import uuid
from pathlib import Path

import cadquery as cq

from .schema import MAX_INSTANCES, MAX_OPERATIONS, CADPlan, Operation
from .features import apply_feature


def _construct(nodes: dict, op: Operation):
    inputs = [nodes[key] for key in op.inputs]
    match op.op:
        case "box":
            result = cq.Workplane("XY").box(*op.size, centered=(True, True, False))
        case "cylinder":
            result = cq.Workplane("XY").circle(op.radius).extrude(op.height)
        case "sphere":
            result = cq.Workplane("XY").sphere(op.radius)
        case "polygon":
            result = cq.Workplane("XY").polyline(op.points).close().extrude(op.height)
        case "loft":
            wires = []
            for section in op.sections:
                plane = cq.Workplane("XY", origin=(*section.center, section.z))
                profile = (
                    plane.circle(section.radius)
                    if section.shape == "circle"
                    else plane.rect(*section.size)
                )
                wires.append(profile.val())
            result = cq.Workplane("XY").newObject(
                [cq.Solid.makeLoft(wires, ruled=op.ruled)]
            )
        case "revolve":
            result = (
                cq.Workplane("XZ")
                .polyline(op.points)
                .close()
                .revolve(op.angle, (0, 0), (0, 1))
            )
        case "sweep":
            points = [cq.Vector(*point) for point in op.path]
            path = (
                cq.Wire.assembleEdges([cq.Edge.makeSpline(points)])
                if op.smooth
                else cq.Wire.makePolygon(points)
            )
            plane = cq.Plane(origin=points[0], normal=path.tangentAt(0))
            result = (
                cq.Workplane(plane).circle(op.radius).sweep(path, transition="round")
            )
        case "shell":
            result = inputs[0].faces(op.faces).shell(-op.thickness)
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
            result = inputs[0].rotate(
                op.origin, tuple(a + b for a, b in zip(op.origin, op.vector)), op.angle
            )
        case "fillet" | "chamfer":
            edges = (
                inputs[0].edges(cq.selectors.NearestToPointSelector(op.edge_point))
                if op.edge_point is not None
                else (
                    inputs[0].edges()
                    if op.edges == "all"
                    else inputs[0].edges(op.edges)
                )
            )
            result = (
                edges.fillet(op.radius)
                if op.op == "fillet"
                else edges.chamfer(op.radius)
            )
        case _:
            result = apply_feature(inputs, op)
    solids = result.solids().vals()
    if not solids or len(solids) > MAX_INSTANCES:
        raise ValueError(f"Operation must produce 1-{MAX_INSTANCES} solids")
    if any(not solid.isValid() or solid.Volume() <= 1e-9 for solid in solids):
        raise ValueError("Operation produced invalid or empty geometry")
    bounds = result.val().BoundingBox()
    if max(bounds.xlen, bounds.ylen, bounds.zlen) > 2000:
        raise ValueError("Intermediate geometry exceeds the 2000 mm limit")
    if sum(len(node.solids().vals()) for node in nodes.values()) + len(solids) > 2048:
        raise ValueError("Plan exceeds the 2048 intermediate-solid budget")
    nodes[op.id] = result
    return result


def apply_operation(nodes: dict, op: Operation):
    try:
        return _construct(nodes, op)
    except Exception as exc:
        raise ValueError(
            f"Operation {op.id} ({op.op}) failed: {str(exc)[:1500]}"
        ) from exc


def build(plan: CADPlan, directory: Path) -> dict:
    nodes = {}
    for op in plan.operations:
        apply_operation(nodes, op)
    return finish(plan, nodes, directory)


def finish(plan: CADPlan, nodes: dict, directory: Path) -> dict:
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


def stream_build(directory: Path):
    nodes, operations = {}, []
    epoch = uuid.uuid4().hex
    last_preview, previews = 0.0, 0
    for line in sys.stdin:
        message = json.loads(line)
        if "plan" in message:
            plan = CADPlan.model_validate(message["plan"])
            if plan.operations != operations:
                raise ValueError("Streamed operations differ from the final plan")
            return finish(plan, nodes, directory)
        op = Operation.model_validate(message["operation"])
        if (
            len(operations) >= MAX_OPERATIONS
            or op.id in nodes
            or any(ref not in nodes for ref in op.inputs)
        ):
            raise ValueError("Invalid streamed operation graph")
        result = apply_operation(nodes, op)
        operations.append(op)
        if (
            message.get("preview", True)
            and previews < 8
            and time.monotonic() - last_preview >= 1.5
        ):
            last_preview = time.monotonic()
            previews += 1
            try:
                # Draft tessellation is bounded and never substitutes for final validation.
                bounds = result.val().BoundingBox()
                if max(bounds.xlen, bounds.ylen, bounds.zlen) > 1000:
                    continue
                # Copy geometry so draft tessellation cannot populate the final shape's mesh cache.
                model = (
                    result.val()
                    .copy()
                    .translate(
                        (
                            -(bounds.xmin + bounds.xmax) / 2,
                            -(bounds.ymin + bounds.ymax) / 2,
                            -bounds.zmin,
                        )
                    )
                )
                revision = f"{epoch}-{len(operations)}"
                target = directory / f"preview-{revision}.stl"
                cq.exporters.export(
                    model, str(target), tolerance=0.6, angularTolerance=0.35
                )
                if target.stat().st_size > 4_000_000:
                    target.unlink()
                    continue
                temporary = directory / "preview.json.tmp"
                temporary.write_text(
                    json.dumps({"revision": revision, "operations": len(operations)})
                )
                temporary.replace(directory / "preview.json")
            except Exception:
                # A failed draft export must not change the final CAD result.
                continue
    raise ValueError("CAD stream closed before receiving the final plan")


if __name__ == "__main__":
    directory = Path(sys.argv[1])
    try:
        if "--stream" in sys.argv:
            metadata = stream_build(directory)
        else:
            plan = CADPlan.model_validate_json((directory / "plan.json").read_text())
            metadata = build(plan, directory)
        (directory / "geometry.json").write_text(json.dumps(metadata))
    except Exception as exc:
        print(f"CAD construction failed: {str(exc)[:1000]}", file=sys.stderr)
        sys.exit(1)
