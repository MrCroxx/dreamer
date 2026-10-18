import asyncio
import math

import cadquery as cq
import pytest
from pydantic import ValidationError

from dreamer import llm
from dreamer.cad_worker import apply_operation, build
from dreamer.config import Settings
from dreamer.examples import example_plan
from dreamer.live_cad import LiveCAD
from dreamer.schema import CADPlan, Frame, GenerateRequest, Operation, MAX_OPERATIONS
from dreamer.service import Service
from dreamer.store import Store
from dreamer.streaming import PlanStream


def apply(nodes, **kwargs):
    return apply_operation(nodes, Operation(**kwargs))


def volume(shape):
    return sum(s.Volume() for s in shape.solids().vals())


def rectangle(width=6, height=4):
    return [{"shape": "rectangle", "size": [width, height]}]


@pytest.mark.parametrize(
    "face,normal,xaxis",
    [
        (">Z", (0, 0, 1), (1, 0, 0)),
        ("<Z", (0, 0, -1), (1, 0, 0)),
        (">X", (1, 0, 0), (0, 1, 0)),
        ("<X", (-1, 0, 0), (0, -1, 0)),
        (">Y", (0, 1, 0), (-1, 0, 0)),
        ("<Y", (0, -1, 0), (1, 0, 0)),
    ],
)
def test_engraving_depth_offset_and_orientation_on_every_side(face, normal, xaxis):
    nodes = {}
    body = apply(nodes, id="body", op="box", size=[40, 30, 20])
    shape = apply(
        nodes,
        id="marked",
        op="engrave",
        inputs=["body"],
        profiles=rectangle(),
        depth=2,
        faces=face,
        offset=[3, 2],
    )
    n, x = cq.Vector(*normal), cq.Vector(*xaxis)
    y = n.cross(x)
    extent = abs(normal[0]) * 20 + abs(normal[1]) * 15 + abs(normal[2]) * 10
    center = cq.Vector(0, 0, 10) + n * extent + x * 3 + y * 2
    assert volume(body) - volume(shape) == pytest.approx(48, abs=1e-6)
    assert not shape.val().isInside(center - n)
    assert shape.val().isInside(center - n * 3)


def test_engraved_ring_preserves_island_and_emboss_touches_body():
    nodes = {}
    apply(nodes, id="body", op="box", size=[40, 30, 10])
    profiles = [{"shape": "circle", "radius": 5}, {"shape": "circle", "radius": 3}]
    cut = apply(
        nodes, id="cut", op="engrave", inputs=["body"], profiles=profiles, depth=2
    )
    assert cut.val().isInside(cq.Vector(0, 0, 9))
    assert not cut.val().isInside(cq.Vector(4, 0, 9))
    assert 12000 - volume(cut) == pytest.approx(math.pi * 16 * 2)
    raised = apply(
        nodes, id="raised", op="emboss", inputs=["body"], profiles=profiles, depth=2
    )
    assert volume(raised) - 12000 == pytest.approx(math.pi * 16 * 2)
    assert raised.val().isInside(cq.Vector(4, 0, 11))
    assert len(raised.solids().vals()) == 1


@pytest.mark.parametrize(
    "text,font",
    [
        ("CAD 2026", "sans"),
        ("Label", "serif"),
        ("M3", "mono"),
        ("\u68a6\u60f3", "auto"),
    ],
)
def test_real_text_glyphs_cut_and_raise(text, font):
    nodes = {}
    apply(nodes, id="body", op="box", size=[100, 40, 4])
    letters = apply(
        nodes, id="letters", op="text", text=text, text_size=8, height=1, font=font
    )
    cut = apply(
        nodes,
        id="cut",
        op="engrave",
        inputs=["body"],
        text=text,
        text_size=8,
        depth=1,
        font=font,
    )
    raised = apply(
        nodes,
        id="raised",
        op="emboss",
        inputs=["body"],
        text=text,
        text_size=8,
        depth=1,
        font=font,
    )
    assert volume(letters) > 1
    assert 16000 - volume(cut) == pytest.approx(volume(letters), rel=1e-5)
    assert volume(raised) - 16000 == pytest.approx(volume(letters), rel=1e-5)
    assert len(raised.solids().vals()) == 1


def test_marking_reports_missing_face_disconnected_letters_and_no_contact():
    nodes = {}
    apply(nodes, id="body", op="box", size=[20, 20, 4])
    for op in ["engrave", "emboss"]:
        with pytest.raises(ValueError, match="mark.*" + op):
            apply(
                nodes,
                id="mark",
                op=op,
                inputs=["body"],
                profiles=rectangle(),
                depth=1,
                offset=[100, 0],
            )
    with pytest.raises(ValueError, match="No planar face"):
        apply(
            nodes,
            id="face",
            op="engrave",
            inputs=["body"],
            profiles=rectangle(),
            depth=1,
            face_index=2,
        )
    with pytest.raises(ValueError, match="Font does not support"):
        apply(nodes, id="missing", op="text", text="\U0010ffff", text_size=8, height=1)


def test_arcs_splines_holes_and_oriented_extrusion():
    nodes = {}
    # Two semicircles form a precise circle, with a concentric circular hole.
    outer = {
        "shape": "path",
        "start": [5, 0],
        "segments": [
            {"kind": "arc", "through": [[0, 5]], "end": [-5, 0]},
            {"kind": "arc", "through": [[0, -5]], "end": [5, 0]},
        ],
    }
    ring = apply(
        nodes,
        id="ring",
        op="extrude",
        profiles=[outer, {"shape": "circle", "radius": 3}],
        height=10,
        frame={"origin": [4, 5, 6], "normal": [1, 0, 0], "x_direction": [0, 1, 0]},
    )
    assert volume(ring) == pytest.approx(math.pi * 16 * 10)
    assert ring.val().BoundingBox().xmin == pytest.approx(4)
    assert ring.val().BoundingBox().xmax == pytest.approx(14)
    assert not ring.val().isInside(cq.Vector(9, 5, 6))
    profile = {
        "shape": "path",
        "start": [-5, 0],
        "segments": [
            {"kind": "spline", "through": [[-3, 3], [0, 4], [3, 3]], "end": [5, 0]},
            {"end": [5, -3]},
            {"end": [-5, -3]},
        ],
    }
    curved = apply(nodes, id="curve", op="extrude", profiles=[profile], height=3)
    assert curved.val().isInside(cq.Vector(0, 2, 1))
    assert not curved.val().isInside(cq.Vector(0, 6, 1))


def test_twist_taper_and_smooth_sweep():
    nodes = {}
    twisted = apply(
        nodes, id="twist", op="twist", profiles=rectangle(10, 5), height=30, angle=120
    )
    assert volume(twisted) == pytest.approx(1500, rel=1e-4)
    tapered = apply(
        nodes, id="taper", op="extrude", profiles=rectangle(20, 20), height=10, taper=10
    )
    assert volume(tapered) < 4000
    swept = apply(
        nodes,
        id="rod",
        op="sweep",
        radius=1,
        smooth=True,
        path=[[0, 0, 0], [0, 0, 10], [10, 0, 20], [20, 0, 20]],
    )
    assert swept.val().isValid()
    assert swept.val().BoundingBox().xlen > 20


def test_patterns_cut_repeated_holes_and_avoid_duplicate_polar_endpoint():
    nodes = {}
    apply(nodes, id="body", op="box", size=[60, 60, 5])
    apply(nodes, id="hole", op="cylinder", radius=2, height=7)
    apply(nodes, id="first", op="translate", inputs=["hole"], vector=[15, 0, -1])
    tools = apply(
        nodes,
        id="tools",
        op="polar_pattern",
        inputs=["first"],
        count=4,
        vector=[0, 0, 1],
        angle=360,
    )
    assert len(tools.solids().vals()) == 4
    cut = apply(nodes, id="cut", op="cut", inputs=["body", "tools"])
    assert volume(cut) == pytest.approx(18000 - 4 * math.pi * 4 * 5)
    for x, y in [(15, 0), (0, 15), (-15, 0), (0, -15)]:
        assert not cut.val().isInside(cq.Vector(x, y, 2))
    partial = apply(
        nodes,
        id="partial",
        op="polar_pattern",
        inputs=["first"],
        count=3,
        vector=[0, 0, 1],
        angle=180,
    )
    centers = [s.Center() for s in partial.solids().vals()]
    assert centers[-1].x == pytest.approx(-15)
    linear = apply(
        nodes,
        id="linear",
        op="linear_pattern",
        inputs=["hole"],
        vector=[6, 0, 0],
        count=64,
    )
    assert len(linear.solids().vals()) == 64
    with pytest.raises(ValueError, match="instance limit"):
        apply(
            nodes,
            id="explosion",
            op="linear_pattern",
            inputs=["linear"],
            vector=[0, 6, 0],
            count=64,
        )


def test_mirror_scale_rotate_and_split_around_explicit_origins():
    nodes = {}
    apply(nodes, id="box", op="box", size=[4, 6, 8])
    mirrored = apply(
        nodes,
        id="mirror",
        op="mirror",
        inputs=["box"],
        frame={"origin": [10, 0, 0], "normal": [1, 0, 0]},
    )
    assert mirrored.val().Center().x == pytest.approx(20)
    scaled = apply(
        nodes, id="scale", op="scale", inputs=["mirror"], factor=2, origin=[20, 0, 4]
    )
    assert volume(scaled) == pytest.approx(4 * 6 * 8 * 8)
    assert scaled.val().Center().x == pytest.approx(20)
    rotated = apply(
        nodes,
        id="rotate",
        op="rotate",
        inputs=["box"],
        origin=[10, 0, 0],
        vector=[0, 0, 1],
        angle=180,
    )
    assert rotated.val().Center().x == pytest.approx(20)
    clipped = apply(
        nodes,
        id="split",
        op="split",
        inputs=["box"],
        frame={"origin": [0, 0, 4]},
        keep="top",
    )
    assert volume(clipped) == pytest.approx(4 * 6 * 4)
    assert clipped.val().BoundingBox().zmin == pytest.approx(4)


def test_helix_round_coil_and_profile_thread():
    nodes = {}
    coil = apply(
        nodes, id="coil", op="helix", radius=10, wire_radius=1, pitch=5, height=20
    )
    length = 4 * math.hypot(2 * math.pi * 10, 5)
    assert volume(coil) == pytest.approx(math.pi * length, rel=0.01)
    apply(nodes, id="shaft", op="cylinder", radius=9.5, height=20)
    profile = {
        "shape": "path",
        "start": [-1, -0.8],
        "segments": [{"end": [1, 0]}, {"end": [-1, 0.8]}],
    }
    apply(
        nodes,
        id="thread",
        op="helix",
        radius=10,
        pitch=4,
        height=16,
        profiles=[profile],
    )
    screw = apply(nodes, id="screw", op="union", inputs=["shaft", "thread"])
    assert len(screw.solids().vals()) == 1
    assert volume(screw) > math.pi * 9.5**2 * 20


@pytest.mark.parametrize(
    "params",
    [
        {"op": "extrude", "height": 2},
        {"op": "extrude", "height": 2, "profiles": [{"shape": "path"}]},
        {
            "op": "extrude",
            "height": 2,
            "profiles": [{"shape": "circle", "radius": 2}],
            "taper": 90,
        },
        {"op": "twist", "height": 2, "profiles": rectangle(), "angle": 0},
        {"op": "text", "text": "", "text_size": 4, "height": 1},
        {"op": "text", "text": "one\ntwo", "text_size": 4, "height": 1},
        {
            "op": "text",
            "text": "word",
            "text_size": 4,
            "height": 1,
            "font": "/tmp/font.ttf",
        },
        {"op": "engrave", "inputs": ["body"], "depth": 1},
        {
            "op": "engrave",
            "inputs": ["body"],
            "depth": 1,
            "text": "A",
            "profiles": rectangle(),
        },
        {"op": "engrave", "inputs": ["body"], "depth": 1, "text": "A"},
        {"op": "linear_pattern", "inputs": ["body"], "count": 65, "vector": [1, 0, 0]},
        {"op": "linear_pattern", "inputs": ["body"], "count": 2},
        {"op": "polar_pattern", "inputs": ["body"], "count": 3, "angle": 360},
        {"op": "scale", "inputs": ["body"], "factor": 0},
        {"op": "helix", "radius": 10, "height": 100, "pitch": 1, "wire_radius": 0.1},
        {"op": "helix", "radius": 10, "height": 10, "pitch": 2, "wire_radius": 1},
        {"op": "helix", "radius": 10, "height": 10, "pitch": 2},
    ],
)
def test_invalid_features_rejected_before_kernel(params):
    with pytest.raises(ValidationError):
        Operation(id="bad", **params)


def test_invalid_frames_and_operation_budget():
    for frame in [
        {"normal": [0, 0, 0]},
        {"normal": [1, 0, 0], "x_direction": [1, 0, 0]},
    ]:
        with pytest.raises(ValidationError):
            Frame(**frame)
    payload = dict(
        name="Budget",
        description="Budget test",
        operations=[
            {"id": f"b{i}", "op": "box", "size": [1, 1, 1]}
            for i in range(MAX_OPERATIONS)
        ],
        result="b0",
    )
    plan = CADPlan(**payload)
    parser = PlanStream()
    assert len(parser.feed(plan.model_dump_json())) == MAX_OPERATIONS
    payload["operations"].append({"id": "extra", "op": "box", "size": [1, 1, 1]})
    with pytest.raises(ValidationError):
        CADPlan(**payload)


def test_nameplate_streaming_matches_batch_and_exports(tmp_path):
    plan = example_plan("nameplate")
    parser, streamed = PlanStream(), []
    for char in plan.model_dump_json():
        streamed.extend(parser.feed(char))
    assert streamed == plan.operations
    batch, live = tmp_path / "batch", tmp_path / "live"
    batch.mkdir()
    live.mkdir()
    expected = build(plan, batch)

    async def execute():
        async with LiveCAD(live, Settings(_env_file=None, cad_timeout=30)) as worker:
            for op in plan.operations:
                await worker.operation(op)
            return await worker.finish(plan)

    actual = asyncio.run(execute())
    assert actual == expected
    assert actual["dimensions"] == [90, 36, 4.8]
    assert (batch / "model.stl").read_bytes() == (live / "model.stl").read_bytes()
    assert b"ISO-10303-21" in (live / "model.step").read_bytes()


def test_repair_receives_failed_step_and_can_use_third_attempt(tmp_path, monkeypatch):
    store = Store(tmp_path)
    service = Service(Settings(_env_file=None, data_dir=tmp_path), store)
    job = store.create("Engrave a plate", None, False)
    calls = []

    async def generate(
        settings, request, previous, repair, *, on_operation, on_output, history
    ):
        calls.append(repair)
        plan = example_plan("nameplate")
        if len(calls) < 3:
            plan = plan.model_copy(deep=True)
            plan.operations[6].offset = (500, 0)
        for op in plan.operations:
            await on_operation(op)
        return plan

    monkeypatch.setattr(llm, "generate_plan", generate)
    asyncio.run(service.generate(job["id"], GenerateRequest(prompt="Engrave a plate")))
    assert store.get(job["id"])["state"] == "preview"
    assert len(calls) == 3
    assert "Operation label (engrave) failed" in calls[1]
    assert "Failed plan" in calls[2]


def test_partial_stream_context_and_attempt_limit(tmp_path, monkeypatch):
    store = Store(tmp_path)
    service = Service(
        Settings(_env_file=None, data_dir=tmp_path, model_attempts=2), store
    )
    job = store.create("Repair a partial design", None, False)
    calls = []

    async def generate(
        settings, request, previous, repair, *, on_operation, on_output, history
    ):
        calls.append(repair)
        await on_operation(Operation(id="body", op="box", size=[20, 20, 4]))
        raise ValueError("Incomplete response")

    monkeypatch.setattr(llm, "generate_plan", generate)
    asyncio.run(
        service.generate(job["id"], GenerateRequest(prompt="Repair a partial design"))
    )
    assert len(calls) == 2
    assert "Partial attempted operations" in calls[1]
    assert '"id": "body"' in calls[1]
    assert store.get(job["id"])["state"] == "failed"
    assert store.get(job["id"])["model_sha256"] is None


def test_local_edge_treatment_and_invalid_profile_feedback():
    nodes = {}
    apply(nodes, id="body", op="box", size=[20, 20, 10])
    rounded = apply(
        nodes,
        id="corner",
        op="fillet",
        inputs=["body"],
        radius=2,
        edge_point=[10, 10, 5],
    )
    assert volume(rounded) == pytest.approx(4000 - (4 - math.pi) * 4 * 10 / 4)
    with pytest.raises(ValueError, match="Operation outline.*failed"):
        apply(
            nodes,
            id="outline",
            op="extrude",
            height=2,
            profiles=[
                {"shape": "circle", "radius": 3},
                {"shape": "circle", "radius": 2, "center": [10, 0]},
            ],
        )
