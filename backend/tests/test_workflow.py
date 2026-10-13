import asyncio
import base64
import io
import json
import time
import zipfile
from concurrent.futures import ThreadPoolExecutor

import pytest
from fastapi.testclient import TestClient
from PIL import Image
from pydantic import ValidationError

from dreamer import printer
from dreamer.commands import run_command
from dreamer.cad_worker import build
from dreamer.config import Settings
from dreamer.examples import example_plan
from dreamer.main import create_app
from dreamer.schema import CADPlan, GenerateRequest
from dreamer.store import Conflict, Store


@pytest.fixture
def settings(tmp_path):
    return Settings(_env_file=None, data_dir=tmp_path, llm_api_key="", llm_model="")


@pytest.fixture
def client(settings):
    with TestClient(create_app(settings)) as value:
        yield value


def wait_for_job(client, job_id, state):
    deadline = time.monotonic() + 40
    while time.monotonic() < deadline:
        job = client.get(f"/api/jobs/{job_id}").json()
        if job["state"] == state:
            return job
        assert job["state"] != "failed", job["error"]
        time.sleep(0.05)
    raise AssertionError(f"Job did not reach {state}")


@pytest.mark.parametrize("template", ["organizer", "bracket", "planter"])
def test_real_cad_exports_connected_solids(template, tmp_path):
    geometry = build(example_plan(template), tmp_path)
    assert geometry["solids"] == 1
    assert geometry["volume_mm3"] > 0
    assert max(geometry["dimensions"]) < 180
    assert (tmp_path / "model.stl").stat().st_size > 100
    assert b"ISO-10303-21" in (tmp_path / "model.step").read_bytes()


def test_demo_api_generates_downloadable_model_and_rejects_unapproved_actions(client):
    response = client.post(
        "/api/jobs", json={"prompt": "A planter", "demo_template": "planter"}
    )
    assert response.status_code == 202
    job = wait_for_job(client, response.json()["id"], "preview")
    assert job["geometry"]["dimensions"] == [72, 72, 65]
    assert client.get(f"/api/jobs/{job['id']}/files/model.stl").status_code == 200
    assert client.get(f"/api/jobs/{job['id']}/files/dreamer.sqlite3").status_code == 404
    assert (
        client.post(
            f"/api/jobs/{job['id']}/slice",
            json={"confirmed": False, "artifact_sha256": job["model_sha256"]},
        ).status_code
        == 422
    )
    assert (
        client.post(
            f"/api/jobs/{job['id']}/slice",
            json={"confirmed": True, "artifact_sha256": "0" * 64},
        ).status_code
        == 409
    )
    assert (
        client.post(
            f"/api/jobs/{job['id']}/print",
            json={"confirmed": True, "artifact_sha256": job["model_sha256"]},
        ).status_code
        == 409
    )
    assert (
        client.post(
            f"/api/jobs/{job['id']}/slice",
            json={"confirmed": True, "artifact_sha256": job["model_sha256"]},
        ).status_code
        == 503
    )


def test_unconfigured_llm_and_bad_inputs_fail_explicitly(client):
    assert client.post("/api/jobs", json={"prompt": "A gear"}).status_code == 503
    assert (
        client.post(
            "/api/jobs", json={"prompt": "   ", "demo_template": "planter"}
        ).status_code
        == 422
    )
    assert (
        client.post(
            "/api/jobs", json={"prompt": "A gear", "images": ["file:///etc/passwd"]}
        ).status_code
        == 422
    )
    assert client.get("/api/jobs/does-not-exist").status_code == 404


def test_image_validation_and_demo_restrictions():
    buffer = io.BytesIO()
    Image.new("RGB", (4, 4)).save(buffer, format="PNG")
    image = "data:image/png;base64," + base64.b64encode(buffer.getvalue()).decode()
    assert len(GenerateRequest(prompt="A box", images=[image]).images) == 1
    with pytest.raises(ValidationError):
        GenerateRequest(prompt="A box", images=[image], demo_template="planter")
    with pytest.raises(ValidationError):
        GenerateRequest(
            prompt="A box", images=[image.replace("image/png", "image/jpeg")]
        )


def test_cad_graph_rejects_arbitrary_code_and_forward_references():
    plan = example_plan("planter").model_dump()
    plan["operations"][0]["op"] = "exec"
    with pytest.raises(ValidationError):
        CADPlan.model_validate(plan)
    plan = example_plan("planter").model_dump()
    plan["operations"][2]["inputs"] = ["result"]
    with pytest.raises(ValidationError):
        CADPlan.model_validate(plan)


def test_atomic_dispatch_and_restart_recovery(tmp_path):
    store = Store(tmp_path)
    job = store.create("test", None, False)
    store.transition(job["id"], {"queued"}, "sliced", "ready")

    def send_once(_):
        try:
            store.transition(job["id"], {"sliced"}, "sending", "sending")
            return True
        except Conflict:
            return False

    with ThreadPoolExecutor(max_workers=5) as pool:
        assert sum(pool.map(send_once, range(5))) == 1
    store.recover()
    assert store.get(job["id"])["state"] == "send_unknown"
    other = store.create("another", None, False)
    store.transition(other["id"], {"queued"}, "sliced", "ready")
    with pytest.raises(Conflict):
        store.transition(other["id"], {"sliced"}, "sending", "send")


def test_api_auth_and_origin_guard(settings):
    with TestClient(create_app(settings)) as client:
        response = client.get("/api/status")
        assert response.status_code == 200
        assert response.json()["auth_required"] is False
    settings.api_token = "test-secret"
    with TestClient(create_app(settings)) as client:
        assert client.get("/api/status").status_code == 401
        assert client.get("/healthz").json() == {"status": "ok"}
        response = client.get(
            "/api/status", headers={"Authorization": "Bearer test-secret"}
        )
        assert response.status_code == 200
        assert response.json()["auth_required"] is True
        assert "test-secret" not in response.text
        assert (
            client.post(
                "/api/jobs",
                json={"prompt": "hello"},
                headers={
                    "Authorization": "Bearer test-secret",
                    "Origin": "https://untrusted.example",
                },
            ).status_code
            == 403
        )


def test_slice_and_print_contract_with_fake_cli(settings, monkeypatch, tmp_path):
    calls = []
    for name in ["machine", "process", "filament"]:
        path = tmp_path / f"{name}.json"
        path.write_text("{}")
        setattr(settings, f"{name}_profile", str(path))
    monkeypatch.setattr(printer.shutil, "which", lambda _: "/test/bin")

    async def command(argv, timeout):
        calls.append(argv)
        with zipfile.ZipFile(tmp_path / "print.gcode.3mf", "w") as archive:
            archive.writestr("Metadata/plate_1.gcode", "G28\n")
        return "ok"

    monkeypatch.setattr(printer, "run_command", command)
    sha = asyncio.run(printer.slice_model(settings, tmp_path))
    assert len(sha) == 64
    assert calls[0][-1].endswith("model.stl")
    assert "--slice" in calls[0]
    settings.print_enabled, settings.printer_name = True, "test-printer"
    asyncio.run(printer.send_print(settings, tmp_path / "print.gcode.3mf"))
    assert calls[1][-1] == "--confirm"
    assert calls[1][1:3] == ["--printer", "test-printer"]
    (tmp_path / "machine.json").write_text(json.dumps({"inherits": "base"}))
    with pytest.raises(ValueError, match="flattened"):
        printer.validate_profiles(settings)


def test_command_timeout_terminates_process():
    import sys

    with pytest.raises(asyncio.TimeoutError):
        asyncio.run(
            run_command([sys.executable, "-c", "import time; time.sleep(10)"], 0.05)
        )


def test_print_api_is_single_use_and_timeout_is_not_retried(settings, monkeypatch):
    monkeypatch.setattr(printer, "print_ready", lambda _: True)
    calls = []

    async def send(config, path):
        calls.append(path)
        raise TimeoutError("Printer response timed out")

    monkeypatch.setattr(printer, "send_print", send)
    app = create_app(settings)
    with TestClient(app) as client:
        store = app.state.store
        job = store.create("test print", None, False)
        artifact = store.directory(job["id"]) / "print.gcode.3mf"
        artifact.write_bytes(b"test-sliced-file")
        sha = printer.digest(artifact)
        store.transition(
            job["id"], {"queued"}, "sliced", "Test slice ready", slice_sha256=sha
        )
        body = {"confirmed": True, "artifact_sha256": sha}
        endpoint = f"/api/jobs/{job['id']}/print"
        assert client.post(endpoint, json=body).status_code == 202
        wait_for_job(client, job["id"], "send_unknown")
        assert client.post(endpoint, json=body).status_code == 409
        assert len(calls) == 1


def test_modified_artifact_cannot_be_dispatched(settings, monkeypatch):
    monkeypatch.setattr(printer, "print_ready", lambda _: True)
    app = create_app(settings)
    with TestClient(app) as client:
        store = app.state.store
        job = store.create("test", None, False)
        artifact = store.directory(job["id"]) / "print.gcode.3mf"
        artifact.write_bytes(b"original")
        sha = printer.digest(artifact)
        store.transition(job["id"], {"queued"}, "sliced", "ready", slice_sha256=sha)
        artifact.write_bytes(b"changed")
        response = client.post(
            f"/api/jobs/{job['id']}/print",
            json={"confirmed": True, "artifact_sha256": sha},
        )
        assert response.status_code == 409
        assert store.get(job["id"])["state"] == "sliced"


def test_oversized_models_are_blocked_before_slicing(settings):
    app = create_app(settings)
    with TestClient(app) as client:
        store = app.state.store
        job = store.create("large model", None, False)
        artifact = store.directory(job["id"]) / "model.stl"
        artifact.write_bytes(b"large model")
        sha = printer.digest(artifact)
        store.transition(
            job["id"],
            {"queued"},
            "preview",
            "ready",
            model_sha256=sha,
            geometry={"fits_build_volume": False},
        )
        response = client.post(
            f"/api/jobs/{job['id']}/slice",
            json={"confirmed": True, "artifact_sha256": sha},
        )
        assert response.status_code == 422


def test_model_only_archive_is_not_a_valid_slice(settings, tmp_path, monkeypatch):
    monkeypatch.setattr(printer, "validate_profiles", lambda _: None)
    settings.machine_profile = settings.process_profile = settings.filament_profile = (
        "unused.json"
    )

    async def command(argv, timeout):
        with zipfile.ZipFile(tmp_path / "print.gcode.3mf", "w") as archive:
            archive.writestr("3D/model.model", "<model />")

    monkeypatch.setattr(printer, "run_command", command)
    with pytest.raises(ValueError, match="no sliced G-code"):
        asyncio.run(printer.slice_model(settings, tmp_path))
