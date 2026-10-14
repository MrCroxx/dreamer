import math

import cadquery as cq
import pytest
from pydantic import ValidationError

from dreamer.cad_worker import apply_operation, build
from dreamer.schema import CADPlan, Operation
from dreamer.streaming import PlanStream


def solid(nodes, **values):
    result = apply_operation(nodes, Operation(**values))
    solids = result.solids().vals()
    assert len(solids) == 1
    assert solids[0].isValid()
    assert solids[0].Volume() > 0
    return solids[0]


@pytest.mark.parametrize("angle", [90, 180, 360])
def test_revolve_preserves_annular_volume_and_axis(angle):
    shape = solid(
        {},
        id="ring",
        op="revolve",
        angle=angle,
        points=[(5, 0), (10, 0), (10, 20), (5, 20)],
    )
    assert shape.Volume() == pytest.approx(math.pi * (100 - 25) * 20 * angle / 360)
    assert shape.BoundingBox().zlen == pytest.approx(20)
    assert shape.isInside(cq.Vector(7, 1, 10))
    assert not shape.isInside(cq.Vector(1, 1, 10))


def test_ruled_loft_matches_a_conical_frustum():
    shape = solid(
        {},
        id="taper",
        op="loft",
        ruled=True,
        sections=[{"z": 0, "radius": 10}, {"z": 30, "radius": 5}],
    )
    assert shape.Volume() == pytest.approx(math.pi * 30 / 3 * (100 + 50 + 25))
    assert shape.BoundingBox().zlen == pytest.approx(30)


def test_loft_supports_offset_rectangular_sections():
    shape = solid(
        {},
        id="offset",
        op="loft",
        ruled=True,
        sections=[
            {"z": 0, "shape": "rectangle", "size": [20, 10]},
            {"z": 30, "center": [5, 0], "shape": "rectangle", "size": [20, 10]},
        ],
    )
    assert shape.Volume() == pytest.approx(6000)
    assert shape.BoundingBox().xlen == pytest.approx(25, abs=1e-5)


def test_loft_can_transition_from_rectangle_to_circle():
    shape = solid(
        {},
        id="transition",
        op="loft",
        sections=[
            {"z": 0, "shape": "rectangle", "size": [30, 30]},
            {"z": 40, "radius": 10},
        ],
    )
    assert shape.BoundingBox().zlen == pytest.approx(40, abs=1e-5)


@pytest.mark.parametrize(
    "path",
    [
        [(0, 0, 0), (0, 0, 20)],
        [(0, 0, 0), (20, 0, 0)],
        [(3, 5, 7), (13, 15, 17)],
    ],
)
def test_sweep_profile_is_perpendicular_to_path(path):
    shape = solid({}, id="rod", op="sweep", radius=2, path=path)
    length = math.dist(*path)
    assert shape.Volume() == pytest.approx(math.pi * 4 * length)


def test_sweep_follows_corners_in_three_dimensions():
    shape = solid(
        {},
        id="routed",
        op="sweep",
        radius=2,
        path=[(0, 0, 0), (0, 0, 20), (20, 0, 20), (20, 20, 20)],
    )
    for point in [(0, 0, 10), (10, 0, 20), (20, 10, 20)]:
        assert shape.isInside(cq.Vector(*point))
    assert not shape.isInside(cq.Vector(10, 10, 10))


@pytest.mark.parametrize(
    "faces,cavity,base",
    [
        (">Z", (0, 0, 29), (0, 0, 1)),
        ("<Z", (0, 0, 1), (0, 0, 29)),
        (">X", (19, 0, 15), (-19, 0, 15)),
    ],
)
def test_shell_removes_selected_face_and_preserves_outer_dimensions(
    faces, cavity, base
):
    nodes = {}
    solid(nodes, id="box", op="box", size=(40, 40, 30))
    shape = solid(
        nodes, id="hollow", op="shell", inputs=["box"], thickness=2, faces=faces
    )
    bounds = shape.BoundingBox()
    assert [bounds.xlen, bounds.ylen, bounds.zlen] == pytest.approx([40, 40, 30])
    assert not shape.isInside(cq.Vector(*cavity))
    assert shape.isInside(cq.Vector(*base))


@pytest.mark.parametrize(
    "operation",
    [
        {"op": "loft", "sections": [{"z": 0, "radius": 5}]},
        {"op": "loft", "sections": [{"z": 0, "radius": 5}, {"z": 0, "radius": 8}]},
        {"op": "loft", "sections": [{"z": 10, "radius": 5}, {"z": 0, "radius": 8}]},
        {"op": "loft", "sections": [{"z": 0}, {"z": 10, "radius": 8}]},
        {
            "op": "loft",
            "sections": [
                {"z": 0, "radius": 5, "size": [10, 10]},
                {"z": 10, "radius": 8},
            ],
        },
        {"op": "loft", "sections": [{"z": i, "radius": 5} for i in range(13)]},
        {"op": "revolve", "angle": 361, "points": [[5, 0], [10, 0], [10, 10]]},
        {"op": "revolve", "angle": 0, "points": [[5, 0], [10, 0], [10, 10]]},
        {"op": "revolve", "angle": 360, "points": [[-5, 0], [10, 0], [10, 10]]},
        {"op": "revolve", "angle": 360, "points": [[0, 0], [5, 5], [10, 10]]},
        {"op": "sweep", "radius": 2, "path": [[0, 0, 0]]},
        {"op": "sweep", "radius": 2, "path": [[0, 0, 0], [0, 0, 0]]},
        {"op": "sweep", "radius": 2, "path": [[0, 0, 0], [0, 0, float("nan")]]},
        {"op": "sweep", "radius": 2, "path": [[0, 0, i] for i in range(33)]},
        {"op": "shell", "inputs": ["body"], "thickness": 0},
        {"op": "shell", "inputs": ["body"], "thickness": 2, "faces": "all"},
        {"op": "shell", "thickness": 2},
    ],
)
def test_invalid_advanced_operations_are_rejected_before_execution(operation):
    with pytest.raises(ValidationError):
        Operation(id="invalid", **operation)


def test_nested_profiles_and_paths_survive_incremental_json_and_export(tmp_path):
    plan = CADPlan(
        name="Routed handle",
        description="A revolved base with a swept handle.",
        operations=[
            {
                "id": "base",
                "op": "revolve",
                "angle": 360,
                "points": [[0, 0], [15, 0], [15, 5], [0, 5]],
            },
            {
                "id": "handle",
                "op": "sweep",
                "radius": 2,
                "path": [[-10, 0, 3], [-10, 0, 25], [10, 0, 25], [10, 0, 3]],
            },
            {"id": "result", "op": "union", "inputs": ["base", "handle"]},
        ],
        result="result",
    )
    parser, operations = PlanStream(), []
    for char in plan.model_dump_json():
        operations.extend(parser.feed(char))
    assert operations == plan.operations
    metadata = build(plan, tmp_path)
    assert metadata["dimensions"] == pytest.approx([30, 30, 27], abs=1e-3)
    assert b"ISO-10303-21" in (tmp_path / "model.step").read_bytes()
