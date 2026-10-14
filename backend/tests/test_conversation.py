import asyncio
import json

from fastapi.testclient import TestClient

from dreamer import llm
from dreamer.config import Settings
from dreamer.examples import example_plan
from dreamer.main import create_app
from dreamer.schema import GenerateRequest
from dreamer.service import Service
from dreamer.store import Store


def test_lineage_preserves_branches_and_survives_reopen(tmp_path):
    store = Store(tmp_path)
    root = store.create("Original brief", None, False)
    first = store.create("Thicker walls", root["id"], False)
    second = store.create("Add a handle", first["id"], False)
    branch = store.create("Make it shorter instead", root["id"], False)
    restored = Store(tmp_path)
    assert [j["id"] for j in restored.lineage(second["id"])] == [
        root["id"],
        first["id"],
        second["id"],
    ]
    assert [j["id"] for j in restored.lineage(branch["id"])] == [
        root["id"],
        branch["id"],
    ]


def test_followup_uses_last_valid_model_and_keeps_failed_intent(tmp_path, monkeypatch):
    store = Store(tmp_path)
    plan = example_plan("planter")
    root = store.create("Make a planter", None, False)
    store.transition(
        root["id"],
        {"queued"},
        "preview",
        "Ready",
        plan=plan.model_dump(),
        model_sha256="a" * 64,
    )
    failed = store.create("Add a drain hole", root["id"], False)
    bad_plan = plan.model_dump()
    bad_plan["description"] = "Unvalidated geometry"
    store.transition(failed["id"], {"queued"}, "failed", "Failed", plan=bad_plan)
    revision = store.create("Use a smaller hole", failed["id"], False)

    async def generate(
        settings, request, previous, repair, *, history, on_operation, on_output
    ):
        assert previous == plan.model_dump(mode="json")
        assert [turn["prompt"] for turn in history] == [
            "Make a planter",
            "Add a drain hole",
        ]
        for operation in plan.operations:
            await on_operation(operation)
        return plan

    monkeypatch.setattr(llm, "generate_plan", generate)
    asyncio.run(
        Service(Settings(_env_file=None, data_dir=tmp_path), store).generate(
            revision["id"],
            GenerateRequest(prompt="Use a smaller hole", parent_id=failed["id"]),
        )
    )
    assert store.get(revision["id"])["state"] == "preview"
    assert store.get(root["id"])["model_sha256"] == "a" * 64
    assert store.get(failed["id"])["state"] == "failed"


def test_context_keeps_original_and_latest_requests_with_bounded_size():
    history = [
        {"prompt": f"Turn {i}: " + "é" * 7900, "model_sha256": "hash"}
        for i in range(40)
    ]
    context = llm.design_context(history)
    assert context[0]["request"] == history[0]["prompt"]
    assert context[-1]["request"] == history[-1]["prompt"]
    assert len(context) < len(history)
    assert len(json.dumps(context, ensure_ascii=False)) < 33_000


def test_conversation_api_rejects_busy_parent_and_allows_failed_followup(
    tmp_path, monkeypatch
):
    app = create_app(
        Settings(
            _env_file=None,
            data_dir=tmp_path,
            llm_api_key="test",
            llm_model="test",
            api_token="test-token",
        )
    )
    monkeypatch.setattr(
        app.state.service, "launch", lambda coroutine: coroutine.close()
    )
    with TestClient(app) as client:
        root = app.state.store.create("Make a bracket", None, False)
        endpoint = f"/api/jobs/{root['id']}/conversation"
        assert client.get(endpoint).status_code == 401
        client.headers["Authorization"] = "Bearer test-token"
        assert (
            client.post(
                "/api/jobs", json={"prompt": "Make it wider", "parent_id": root["id"]}
            ).status_code
            == 409
        )
        app.state.store.transition(root["id"], {"queued"}, "failed", "Failed")
        response = client.post(
            "/api/jobs",
            json={"prompt": "Try simpler geometry", "parent_id": root["id"]},
        )
        assert response.status_code == 202
        turns = client.get(f"/api/jobs/{response.json()['id']}/conversation").json()
        assert [turn["prompt"] for turn in turns] == [
            "Make a bracket",
            "Try simpler geometry",
        ]
        assert all(turn["summary"] is None for turn in turns)
