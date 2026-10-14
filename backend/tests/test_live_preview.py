import asyncio
import json

import httpx
import pytest
from fastapi.testclient import TestClient

from dreamer import llm, printer
from dreamer.cad_worker import build
from dreamer.config import Settings
from dreamer.examples import example_plan
from dreamer.live_cad import LiveCAD
from dreamer.main import create_app
from dreamer.schema import GenerateRequest
from dreamer.streaming import PlanStream


def test_partial_json_emits_only_complete_operations_in_order():
    plan = example_plan("planter")
    plan.description = (
        'Literal "operations": [ inside a string, with escaped \\ and Unicode: café'
    )
    parser = PlanStream()
    operations = []
    for char in plan.model_dump_json():
        operations.extend(parser.feed(char))
    assert operations == plan.operations
    assert parser.text == plan.model_dump_json()


@pytest.mark.parametrize(
    "operation",
    [
        {"id": "cut", "op": "cut", "inputs": ["missing", "other"]},
        {"id": "bad", "op": "exec"},
    ],
)
def test_invalid_partial_graph_is_never_sent_to_cad(operation):
    parser = PlanStream()
    with pytest.raises(ValueError):
        parser.feed('{"operations": [' + json.dumps(operation))


def test_streaming_response_delivers_an_operation_before_completion(monkeypatch):
    plan = example_plan("planter")
    text = plan.model_dump_json()
    split = text.index("},{", text.index('"operations"')) + 1
    emitted = []
    request_payloads = []

    class Body(httpx.AsyncByteStream):
        async def __aiter__(self):
            yield b": keepalive\n\n"
            first = {
                "choices": [{"delta": {"content": text[:split]}, "finish_reason": None}]
            }
            yield ("data: " + json.dumps(first) + "\n\n").encode()
            assert len(emitted) == 1
            rest = {
                "choices": [
                    {"delta": {"content": text[split:]}, "finish_reason": "stop"}
                ]
            }
            yield ("data: " + json.dumps(rest) + "\n\ndata: [DONE]\n\n").encode()

    def respond(request):
        request_payloads.append(json.loads(request.content))
        return httpx.Response(
            200, headers={"content-type": "text/event-stream"}, stream=Body()
        )

    original = httpx.AsyncClient
    monkeypatch.setattr(
        llm.httpx,
        "AsyncClient",
        lambda **kw: original(transport=httpx.MockTransport(respond), **kw),
    )

    async def operation(op):
        emitted.append(op)

    settings = Settings(_env_file=None, llm_model="test-model", llm_api_key="test-key")
    result = asyncio.run(
        llm.generate_plan(
            settings, GenerateRequest(prompt="A planter"), None, on_operation=operation
        )
    )
    assert result == plan
    assert emitted == plan.operations
    assert request_payloads[0]["stream"] is True


@pytest.mark.parametrize("finish", [None, "length", "content_filter"])
def test_truncated_or_refused_stream_cannot_produce_a_final_model(monkeypatch, finish):
    chunk = {
        "choices": [
            {
                "delta": {"content": example_plan("planter").model_dump_json()},
                "finish_reason": finish,
            }
        ]
    }
    response = httpx.Response(
        200,
        headers={"content-type": "text/event-stream"},
        content=("data: " + json.dumps(chunk) + "\n\n").encode(),
    )
    original = httpx.AsyncClient
    monkeypatch.setattr(
        llm.httpx,
        "AsyncClient",
        lambda **kw: original(transport=httpx.MockTransport(lambda _: response), **kw),
    )
    with pytest.raises(ValueError, match="incomplete|before completion"):
        asyncio.run(
            llm.generate_plan(
                Settings(_env_file=None, llm_model="test", llm_api_key="test"),
                GenerateRequest(prompt="test"),
                None,
            )
        )


def test_nonstreaming_json_provider_still_builds(monkeypatch):
    plan = example_plan("bracket")
    response = httpx.Response(
        200, json={"choices": [{"message": {"content": plan.model_dump_json()}}]}
    )
    original = httpx.AsyncClient

    def respond(request):
        assert json.loads(request.content)["stream"] is False
        return response

    monkeypatch.setattr(
        llm.httpx,
        "AsyncClient",
        lambda **kw: original(transport=httpx.MockTransport(respond), **kw),
    )
    assert (
        asyncio.run(
            llm.generate_plan(
                Settings(
                    _env_file=None,
                    llm_model="test",
                    llm_api_key="test",
                    llm_stream=False,
                ),
                GenerateRequest(prompt="test"),
                None,
            )
        )
        == plan
    )


@pytest.mark.parametrize("template", ["planter", "vase"])
def test_live_geometry_matches_final_export_and_drafts_are_cleaned(tmp_path, template):
    plan = example_plan(template)
    baseline = tmp_path / "baseline"
    baseline.mkdir()
    expected = build(plan, baseline)
    directory = tmp_path / "stream"
    directory.mkdir()
    settings = Settings(_env_file=None, data_dir=tmp_path)

    async def run():
        async with LiveCAD(directory, settings) as cad:
            await cad.operation(plan.operations[0])
            async with asyncio.timeout(15):
                while not (directory / "preview.json").exists():
                    await asyncio.sleep(0.02)
            metadata = json.loads((directory / "preview.json").read_text())
            assert metadata["operations"] == 1
            assert (
                directory / f"preview-{metadata['revision']}.stl"
            ).stat().st_size > 100
            assert not (directory / "model.stl").exists()
            for op in plan.operations[1:]:
                await cad.operation(op)
            return await cad.finish(plan)

    assert asyncio.run(run()) == expected
    assert printer.digest(directory / "model.stl") == printer.digest(
        baseline / "model.stl"
    )
    assert not list(directory.glob("preview*"))


def test_cancelled_stream_reaps_worker_and_clears_drafts(tmp_path):
    async def run():
        cad = LiveCAD(tmp_path, Settings(_env_file=None))
        async with cad:
            await cad.operation(example_plan("planter").operations[0])
        assert cad.process.returncode is not None
        assert cad.reader.done()
        assert not list(tmp_path.glob("preview*"))
        assert not (tmp_path / "model.stl").exists()

    asyncio.run(run())


def test_preview_export_can_be_disabled_without_changing_final_model(tmp_path):
    async def run():
        plan = example_plan("bracket")
        async with LiveCAD(
            tmp_path, Settings(_env_file=None, live_preview=False)
        ) as cad:
            for op in plan.operations:
                await cad.operation(op)
            metadata = await cad.finish(plan)
            assert metadata["solids"] == 1
            assert (tmp_path / "model.stl").exists()
            assert not list(tmp_path.glob("preview*"))

    asyncio.run(run())


def test_preview_api_requires_auth_and_cannot_be_approved(tmp_path):
    app = create_app(
        Settings(_env_file=None, data_dir=tmp_path, api_token="test-secret")
    )
    with TestClient(app) as client:
        job = app.state.store.create("test", None, False)
        job_id = job["id"]
        app.state.store.transition(job_id, {"queued"}, "generating", "test")
        directory = app.state.store.directory(job_id)
        revision = "a" * 32 + "-1"
        (directory / "preview.json").write_text(
            json.dumps({"revision": revision, "operations": 1})
        )
        (directory / f"preview-{revision}.stl").write_bytes(b"draft")
        endpoint = f"/api/jobs/{job_id}/preview"
        assert client.get(endpoint).status_code == 401
        client.headers["Authorization"] = "Bearer test-secret"
        assert client.get(endpoint).json()["preview"]["operations"] == 1
        response = client.get(endpoint + "/" + revision)
        assert response.content == b"draft"
        assert response.headers["cache-control"] == "no-store"
        assert client.get(endpoint + "/bad").status_code == 404
        assert client.get(f"/api/jobs/{job_id}/files/model.stl").status_code == 404
        assert (
            client.post(
                f"/api/jobs/{job_id}/slice",
                json={"confirmed": True, "artifact_sha256": "0" * 64},
            ).status_code
            == 409
        )
        app.state.store.transition(job_id, {"generating"}, "failed", "test")
        assert client.get(endpoint).json()["preview"] is None
        assert client.get(endpoint + "/" + revision).status_code == 404
