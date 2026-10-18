"""Reusable CAD features built from validated geometric data."""

import math
import subprocess
from functools import lru_cache

import cadquery as cq
from fontTools.ttLib import TTFont

from .schema import MAX_INSTANCES, Frame, Operation, Profile


def plane(frame: Frame) -> cq.Plane:
    return cq.Plane(frame.origin, xDir=frame.x_direction, normal=frame.normal)


def profile_wire(profile: Profile, workplane: cq.Plane) -> cq.Wire:
    wp = cq.Workplane(workplane).center(*profile.center)
    if profile.shape == "circle":
        return wp.circle(profile.radius).val()
    if profile.shape == "rectangle":
        return wp.rect(*profile.size).val()
    wp = wp.moveTo(*profile.start)
    for segment in profile.segments:
        if segment.kind == "line":
            wp = wp.lineTo(*segment.end)
        elif segment.kind == "arc":
            wp = wp.threePointArc(segment.through[0], segment.end)
        else:
            wp = wp.spline([*segment.through, segment.end], includeCurrent=True)
    return wp.close().val()


def sketch(profiles: list[Profile], workplane: cq.Plane) -> cq.Workplane:
    wires = [profile_wire(profile, workplane) for profile in profiles]
    faces = [cq.Face.makeFromWires(wire) for wire in wires]
    if any(not face.isValid() or face.Area() <= 1e-8 for face in faces):
        raise ValueError("Profile boundaries must be valid and enclose positive area")
    for index, hole in enumerate(faces[1:], 1):
        if (
            abs(faces[0].intersect(hole).Area() - hole.Area())
            > max(1e-7, hole.Area() * 1e-7)
            or wires[0].distance(wires[index]) < 1e-7
        ):
            raise ValueError("Profile holes must be strictly inside the outer boundary")
        for previous in range(1, index):
            if (
                faces[previous].intersect(hole).Area() > 1e-8
                or wires[previous].distance(wires[index]) < 1e-7
            ):
                raise ValueError("Profile holes must not overlap, touch, or nest")
    face = cq.Face.makeFromWires(wires[0], wires[1:])
    if not face.isValid() or face.Area() <= 1e-8:
        raise ValueError(
            "Profile must have a nonintersecting outer boundary with holes inside"
        )
    return cq.Workplane(workplane).newObject(wires).toPending()


@lru_cache(maxsize=4)
def font_file(family: str) -> tuple[str, frozenset[int]]:
    try:
        resolved = subprocess.check_output(
            ["fc-match", "-f", "%{family}\n%{file}", family], text=True, timeout=5
        ).splitlines()
        if len(resolved) != 2 or family not in resolved[0].split(","):
            raise ValueError("Requested CAD font is not installed")
        with TTFont(resolved[1], fontNumber=0) as font:
            supported = frozenset(font.getBestCmap() or {})
        return resolved[1], supported
    except (OSError, subprocess.SubprocessError) as exc:
        raise ValueError(
            "Install fontconfig, fonts-dejavu-core, and fonts-noto-cjk for text features"
        ) from exc


def text_shape(op: Operation, workplane: cq.Plane, height: float) -> cq.Workplane:
    choice = op.font
    if choice == "auto":
        choice = "cjk" if any(ord(char) >= 0x2E80 for char in op.text) else "sans"
    family = {
        "sans": "DejaVu Sans",
        "serif": "DejaVu Serif",
        "mono": "DejaVu Sans Mono",
        "cjk": "Noto Sans CJK JP",
    }[choice]
    path, supported = font_file(family)
    missing = sorted(
        {
            ord(char)
            for char in op.text
            if not char.isspace() and ord(char) not in supported
        }
    )
    if missing:
        raise ValueError(
            "Font does not support these characters: "
            + ", ".join(f"U+{char:04X}" for char in missing)
        )
    shape = cq.Workplane(workplane).text(
        op.text, op.text_size, height, fontPath=path, combine=False
    )
    if len(shape.solids().vals()) > MAX_INSTANCES:
        raise ValueError("Text exceeds the intermediate-solid limit; shorten the label")
    return shape


def marking(body: cq.Workplane, op: Operation) -> cq.Workplane:
    normal, x_direction = {
        ">Z": ((0, 0, 1), (1, 0, 0)),
        "<Z": ((0, 0, -1), (1, 0, 0)),
        ">X": ((1, 0, 0), (0, 1, 0)),
        "<X": ((-1, 0, 0), (0, -1, 0)),
        ">Y": ((0, 1, 0), (-1, 0, 0)),
        "<Y": ((0, -1, 0), (1, 0, 0)),
    }[op.faces]
    direction = cq.Vector(*normal)
    candidates = [
        face
        for face in body.faces().vals()
        if face.geomType() == "PLANE" and face.normalAt().dot(direction) > 0.999999
    ]
    candidates.sort(
        key=lambda face: (-round(face.Center().dot(direction), 6), -face.Area())
    )
    if op.face_index >= len(candidates):
        raise ValueError(
            f"No planar face {op.face_index} facing {op.faces}; use an oriented text/extrude cutter for curved surfaces"
        )
    face = candidates[op.face_index]
    workplane = cq.Plane(face.BoundingBox().center, xDir=x_direction, normal=normal)
    workplane = (
        cq.Workplane(workplane)
        .transformed(offset=(*op.offset, 0), rotate=(0, 0, op.angle))
        .plane
    )
    # Overlap the selected surface to avoid coplanar boolean ambiguity.
    overlap = min(op.depth, 1) * 0.001
    inward = op.op == "engrave"
    workplane.origin += direction * (overlap if inward else -overlap)
    height = -(op.depth + overlap) if inward else op.depth + overlap
    tool = (
        text_shape(op, workplane, height)
        if op.text
        else sketch(op.profiles, workplane).extrude(height)
    )
    result = body.cut(tool) if inward else body.union(tool)
    before = sum(s.Volume() for s in body.solids().vals())
    after = sum(s.Volume() for s in result.solids().vals())
    if (before - after if inward else after - before) <= 1e-7:
        raise ValueError(
            "Marking did not change the body; check face, offset, size, and depth"
        )
    if not inward and len(result.solids().vals()) != len(body.solids().vals()):
        raise ValueError("Embossed features must all touch the selected body")
    return result


def pattern(body: cq.Workplane, op: Operation) -> cq.Workplane:
    solids = body.solids().vals()
    if len(solids) * op.count > MAX_INSTANCES:
        raise ValueError(f"Pattern exceeds the {MAX_INSTANCES}-solid instance limit")
    copies = []
    for index in range(op.count):
        if op.op == "linear_pattern":
            copies.extend(
                s.translate(tuple(index * v for v in op.vector)) for s in solids
            )
        else:
            step = op.angle / (op.count if op.angle == 360 else op.count - 1)
            end = tuple(a + b for a, b in zip(op.origin, op.vector))
            copies.extend(s.rotate(op.origin, end, step * index) for s in solids)
    return cq.Workplane("XY").newObject([cq.Compound.makeCompound(copies)])


def apply_feature(inputs: list[cq.Workplane], op: Operation) -> cq.Workplane:
    match op.op:
        case "extrude":
            return sketch(op.profiles, plane(op.frame)).extrude(
                op.height, taper=op.taper
            )
        case "twist":
            return sketch(op.profiles, plane(op.frame)).twistExtrude(
                op.height, op.angle
            )
        case "text":
            return text_shape(op, plane(op.frame), op.height)
        case "engrave" | "emboss":
            return marking(inputs[0], op)
        case "linear_pattern" | "polar_pattern":
            return pattern(inputs[0], op)
        case "mirror":
            return inputs[0].mirror(
                mirrorPlane=op.frame.normal, basePointVector=op.frame.origin
            )
        case "scale":
            shape = (
                inputs[0]
                .val()
                .translate(tuple(-v for v in op.origin))
                .scale(op.factor)
                .translate(op.origin)
            )
            return cq.Workplane("XY").newObject([shape])
        case "split":
            return (
                cq.Workplane(plane(op.frame))
                .newObject(inputs[0].vals())
                .split(keepTop=op.keep == "top", keepBottom=op.keep == "bottom")
            )
        case "helix":
            path = cq.Wire.makeHelix(
                op.pitch, op.height, op.radius, lefthand=op.lefthand
            )
            section = cq.Plane((op.radius, 0, 0), xDir=(1, 0, 0), normal=(0, -1, 0))
            if op.profiles:
                wp = sketch(op.profiles, section)
            else:
                # Use a normal section for a round wire and a radial/axial section for a thread profile.
                tangent = (
                    0,
                    -1 if op.lefthand else 1,
                    op.pitch / (2 * math.pi * op.radius),
                )
                wp = cq.Workplane(
                    cq.Plane((op.radius, 0, 0), xDir=(1, 0, 0), normal=tangent)
                ).circle(op.wire_radius)
            shape = (
                wp.sweep(path, isFrenet=True).val().transformShape(plane(op.frame).rG)
            )
            return cq.Workplane("XY").newObject([shape])
    raise ValueError(f"Unsupported CAD operation: {op.op}")
