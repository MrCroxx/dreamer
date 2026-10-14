import asyncio
import json

import httpx
import pytest
from fastapi.testclient import TestClient

from dreamer import llm, output
from dreamer.config import Settings
from dreamer.examples import example_plan
from dreamer.main import create_app
from dreamer.output import OutputLog, read_output
from dreamer.schema import GenerateRequest
from dreamer.service import Service
from dreamer.store import Store


@pytest.mark.parametrize("stream", [True, False])
def test_output_captures_provider_reasoning_and_invalid_content(monkeypatch, stream):
    captured = []
    text = '{"unfinished":'
    reasoning = "Choose a wall thickness."
    if stream:
        chunks = [
            {"choices": [{"delta": {"reasoning_content": reasoning}}]},
            {"choices": [{"delta": {"content": text}, "finish_reason": "stop"}]},
        ]
        response = httpx.Response(
            200,
            headers={"content-type": "text/event-stream"},
            content="".join("data: " + json.dumps(chunk) + "\n\n" for chunk in chunks),
        )
    else:
        response = httpx.Response(
            200,
            json={"choices": [{"message": {"content": text, "reasoning": reasoning}}]},
        )
    original = httpx.AsyncClient
    monkeypatch.setattr(
        llm.httpx,
        "AsyncClient",
        lambda **kw: original(transport=httpx.MockTransport(lambda _: response), **kw),
    )
    with pytest.raises(ValueError):
        asyncio.run(
            llm.generate_plan(
                Settings(
                    _env_file=None,
                    llm_api_key="test",
                    llm_model="test",
                    llm_stream=stream,
                ),
                GenerateRequest(prompt="test"),
                None,
                on_output=lambda kind, value: captured.append((kind, value)),
            )
        )
    assert captured == [("reasoning", reasoning), ("content", text)]


def test_unicode_output_pages_replay_exactly_and_preserve_attempts(tmp_path):
    text = "café 🌿\\\n" * 12_000

    async def write():
        async with OutputLog(tmp_path, 1) as log:
            log.text("reasoning", "First attempt")
        async with OutputLog(tmp_path, 2) as log:
            log.text("content", text)

    asyncio.run(write())
    cursor, events, pages = 0, [], 0
    while True:
        page = read_output(tmp_path, cursor)
        assert page["offset"] > cursor
        cursor = page["offset"]
        pages += 1
        events.extend(page["entries"])
        if not page["has_more"]:
            break
    assert pages > 1
    assert "".join(e["text"] for e in events if e["kind"] == "content") == text
    assert [e["attempt"] for e in events if e["kind"] == "attempt"] == [1, 2]
    assert read_output(tmp_path, cursor)["entries"] == []


def test_reader_waits_for_incomplete_record_and_rejects_bad_cursor(tmp_path):
    path = tmp_path / "output.jsonl"
    path.write_bytes(b'{"kind":"content","text":"partial')
    assert read_output(tmp_path, 0)["offset"] == 0
    with path.open("ab") as file:
        file.write(b'"}\n')
    assert read_output(tmp_path, 0)["entries"][0]["text"] == "partial"
    with pytest.raises(ValueError):
        read_output(tmp_path, 2)
    with pytest.raises(ValueError):
        read_output(tmp_path, 9999)


def test_limit_and_recording_failure_do_not_stop_generation(tmp_path, monkeypatch):
    monkeypatch.setattr(output, "MAX_BYTES", 1000)

    async def write():
        async with OutputLog(tmp_path, 1) as log:
            log.text("content", "x" * 2000)
            log.text("content", "Still generating")
        async with OutputLog(tmp_path / "missing", 1) as log:
            log.text("content", "Recording unavailable")

    asyncio.run(write())
    events = read_output(tmp_path, 0)["entries"]
    assert events[-1]["kind"] == "notice"
    assert (tmp_path / "output.jsonl").stat().st_size < 1200


def test_service_keeps_failed_and_successful_attempt_output(tmp_path, monkeypatch):
    settings = Settings(_env_file=None, data_dir=tmp_path)
    store = Store(tmp_path)
    service = Service(settings, store)
    job = store.create("test", None, False)
    calls = 0

    async def generate(*args, on_operation, on_output, history):
        nonlocal calls
        calls += 1
        on_output("reasoning", f"Attempt {calls} reasoning")
        if calls == 1:
            on_output("content", "An incomplete response")
            raise ValueError("Invalid response")
        plan = example_plan("planter")
        on_output("content", plan.model_dump_json())
        for operation in plan.operations:
            await on_operation(operation)
        return plan

    monkeypatch.setattr(llm, "generate_plan", generate)
    asyncio.run(service.generate(job["id"], GenerateRequest(prompt="test")))
    assert store.get(job["id"])["state"] == "preview"
    entries = read_output(store.directory(job["id"]), 0)["entries"]
    assert [e["status"] for e in entries if e["kind"] == "end"] == [
        "interrupted",
        "completed",
    ]
    assert [e["text"] for e in entries if e["kind"] == "reasoning"] == [
        "Attempt 1 reasoning",
        "Attempt 2 reasoning",
    ]


def test_output_api_auth_persistence_and_historical_plan_fallback(tmp_path):
    settings = Settings(_env_file=None, data_dir=tmp_path, api_token="test-token")
    app = create_app(settings)
    job = app.state.store.create("test", None, False)
    endpoint = f"/api/jobs/{job['id']}/output"

    async def write():
        async with OutputLog(app.state.store.directory(job["id"]), 1) as log:
            log.text("content", "Saved provider text")

    asyncio.run(write())
    with TestClient(app) as client:
        assert client.get(endpoint).status_code == 401
        client.headers["Authorization"] = "Bearer test-token"
        response = client.get(endpoint)
        assert response.headers["cache-control"] == "no-store"
        assert response.json()["source"] == "recorded"
        assert client.get(endpoint + "?offset=-1").status_code == 422
        assert client.get(endpoint + "?offset=1").status_code == 400
    with TestClient(create_app(settings)) as client:
        client.headers["Authorization"] = "Bearer test-token"
        assert client.get(endpoint).json()["entries"] == response.json()["entries"]
        cursor = response.json()["offset"]
        with (app.state.store.directory(job["id"]) / "output.jsonl").open("ab") as file:
            file.write(b'{"kind":"content","text":"interrupted')
        tail = client.get(endpoint + f"?offset={cursor}").json()
        assert tail["has_more"] is False
        assert tail["entries"][0]["kind"] == "notice"
    old = app.state.store.create("old", None, False)
    app.state.store.transition(
        old["id"],
        {"queued"},
        "preview",
        "Saved plan",
        plan=example_plan("bracket").model_dump(),
    )
    with TestClient(create_app(settings)) as client:
        client.headers["Authorization"] = "Bearer test-token"
        saved = client.get(f"/api/jobs/{old['id']}/output").json()
        assert saved["source"] == "saved_plan"
        assert json.loads(saved["entries"][0]["text"])["operations"]
