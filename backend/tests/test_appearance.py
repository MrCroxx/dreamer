import asyncio
import json
import time

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
from dreamer.service import Service
from dreamer.store import Store


def completed_model(store):
    job = store.create("A small planter", None, False)
    plan = example_plan("planter")
    plan.color = "#76967a"
    directory = store.directory(job["id"])
    geometry = build(plan, directory)
    geometry["fits_build_volume"] = True
    (directory / "plan.json").write_text(plan.model_dump_json())
    return store.transition(
        job["id"],
        {"queued"},
        "preview",
        "Ready",
        plan=plan.model_dump(),
        geometry=geometry,
        model_sha256=printer.digest(directory / "model.stl"),
    )


def wait(client, job_id):
    for _ in range(100):
        job = client.get(f"/api/jobs/{job_id}").json()
        if job["state"] in {"preview", "failed"}:
            return job
        time.sleep(0.02)
    raise AssertionError("Color update did not finish")


def mock_provider(monkeypatch, payload, streaming):
    text = json.dumps(payload)
    if streaming:
        chunks = [
            {"choices": [{"delta": {"content": text[:20]}, "finish_reason": None}]},
            {"choices": [{"delta": {"content": text[20:]}, "finish_reason": "stop"}]},
        ]
        response = httpx.Response(
            200,
            headers={"content-type": "text/event-stream"},
            content="".join("data: " + json.dumps(chunk) + "\n\n" for chunk in chunks)
            + "data: [DONE]\n\n",
        )
    else:
        response = httpx.Response(
            200, json={"choices": [{"message": {"content": text}}]}
        )
    original = httpx.AsyncClient
    monkeypatch.setattr(
        llm.httpx,
        "AsyncClient",
        lambda **kw: original(transport=httpx.MockTransport(lambda _: response), **kw),
    )


@pytest.mark.parametrize("streaming", [True, False])
def test_prompt_color_update_reuses_exact_geometry_without_cad_build(
    tmp_path, monkeypatch, streaming
):
    store = Store(tmp_path)
    parent = completed_model(store)
    patch = {
        "kind": "appearance",
        "color": "#2563eb",
        "description": "Changed the model to blue.",
    }
    mock_provider(monkeypatch, patch, streaming)

    async def no_build(*args):
        raise AssertionError("An appearance update must not rebuild geometry")

    monkeypatch.setattr(LiveCAD, "operation", no_build)
    monkeypatch.setattr(LiveCAD, "finish", no_build)
    child = store.create("Make it blue", parent["id"], False)
    settings = Settings(
        _env_file=None,
        data_dir=tmp_path,
        llm_api_key="test-key",
        llm_model="test-model",
        llm_stream=streaming,
    )
    asyncio.run(
        Service(settings, store).generate(
            child["id"], GenerateRequest(prompt=child["prompt"], parent_id=parent["id"])
        )
    )
    child = store.get(child["id"])
    assert child["state"] == "preview", child["error"]
    assert child["plan"]["color"] == "#2563eb"
    assert child["model_sha256"] == parent["model_sha256"]
    for filename in ("model.stl", "model.step"):
        assert (store.directory(child["id"]) / filename).read_bytes() == (
            store.directory(parent["id"]) / filename
        ).read_bytes()
    assert child["geometry"] == parent["geometry"]
    assert store.get(parent["id"])["plan"]["color"] == "#76967a"


def test_color_picker_creates_persistent_version_without_provider_or_reusing_slice(
    tmp_path,
):
    settings = Settings(_env_file=None, data_dir=tmp_path, llm_api_key="", llm_model="")
    app = create_app(settings)
    with TestClient(app) as client:
        parent = completed_model(app.state.store)
        app.state.store.transition(
            parent["id"], {"preview"}, "sliced", "Sliced", slice_sha256="a" * 64
        )
        response = client.post(
            f"/api/jobs/{parent['id']}/appearance", json={"color": "#343941"}
        )
        assert response.status_code == 202
        child = wait(client, response.json()["id"])
        assert child["state"] == "preview", child["error"]
        assert child["slice_sha256"] is None
        assert child["model_sha256"] == parent["model_sha256"]
        assert child["parent_id"] == parent["id"]
        assert child["plan"]["color"] == "#343941"
        assert (
            client.get(f"/api/jobs/{child['id']}/files/print.gcode.3mf").status_code
            == 404
        )
        turns = client.get(f"/api/jobs/{child['id']}/conversation").json()
        assert [turn["color"] for turn in turns] == ["#76967a", "#343941"]
    with TestClient(create_app(settings)) as restored:
        child = restored.get(f"/api/jobs/{child['id']}").json()
        assert child["state"] == "preview"
        assert child["plan"]["color"] == "#343941"


@pytest.mark.parametrize("color", ["blue", "#fff", "#12345678", "url(example)"])
def test_invalid_colors_cannot_create_jobs(tmp_path, color):
    app = create_app(Settings(_env_file=None, data_dir=tmp_path))
    with TestClient(app) as client:
        assert (
            client.post(
                "/api/jobs/" + "a" * 32 + "/appearance", json={"color": color}
            ).status_code
            == 422
        )
        assert app.state.store.list() == []


def test_active_or_unbuilt_parent_cannot_be_recolored(tmp_path):
    app = create_app(Settings(_env_file=None, data_dir=tmp_path))
    with TestClient(app) as client:
        parent = app.state.store.create("pending", None, False)
        endpoint = f"/api/jobs/{parent['id']}/appearance"
        assert client.post(endpoint, json={"color": "#2563eb"}).status_code == 409
        app.state.store.transition(parent["id"], {"queued"}, "failed", "Failed")
        assert client.post(endpoint, json={"color": "#2563eb"}).status_code == 409
        assert len(app.state.store.list()) == 1


def test_tampered_geometry_is_not_reused(tmp_path):
    app = create_app(Settings(_env_file=None, data_dir=tmp_path))
    with TestClient(app) as client:
        parent = completed_model(app.state.store)
        (app.state.store.directory(parent["id"]) / "model.stl").write_bytes(b"changed")
        response = client.post(
            f"/api/jobs/{parent['id']}/appearance", json={"color": "#2563eb"}
        )
        child = wait(client, response.json()["id"])
        assert child["state"] == "failed"
        assert child["model_sha256"] is None


def test_geometry_revisions_inherit_color_when_provider_omits_it(monkeypatch):
    plan = example_plan("planter").model_dump()
    previous = {**plan, "color": "#2563eb"}
    plan.pop("color")
    mock_provider(monkeypatch, plan, False)
    result = asyncio.run(
        llm.generate_plan(
            Settings(
                _env_file=None, llm_api_key="test", llm_model="test", llm_stream=False
            ),
            GenerateRequest(prompt="Keep the same shape"),
            previous,
        )
    )
    assert result.color == "#2563eb"


def test_appearance_patch_without_previous_geometry_fails(monkeypatch):
    mock_provider(
        monkeypatch,
        {"kind": "appearance", "color": "#2563eb", "description": "Blue"},
        True,
    )
    with pytest.raises(ValueError, match="existing valid model"):
        asyncio.run(
            llm.generate_plan(
                Settings(_env_file=None, llm_api_key="test", llm_model="test"),
                GenerateRequest(prompt="Blue"),
                None,
            )
        )
