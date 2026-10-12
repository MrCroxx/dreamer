import json
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


class Conflict(Exception):
    pass


class Store:
    def __init__(self, root: Path):
        self.root = root.resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.database = self.root / "dreamer.sqlite3"
        with self.connect() as db:
            db.execute("PRAGMA journal_mode=WAL")
            db.execute(
                "CREATE TABLE IF NOT EXISTS jobs (id TEXT PRIMARY KEY, state TEXT NOT NULL, body TEXT NOT NULL)"
            )

    def connect(self):
        return sqlite3.connect(self.database, timeout=10)

    def create(self, prompt: str, parent_id: str | None, demo: bool):
        job_id = uuid.uuid4().hex
        job = {
            "id": job_id,
            "name": "Untitled model",
            "prompt": prompt,
            "parent_id": parent_id,
            "demo": demo,
            "state": "queued",
            "created_at": now(),
            "updated_at": now(),
            "events": [{"at": now(), "message": "Modeling request queued"}],
            "error": None,
            "plan": None,
            "geometry": None,
            "model_sha256": None,
            "slice_sha256": None,
        }
        self.directory(job_id).mkdir()
        with self.connect() as db:
            db.execute(
                "INSERT INTO jobs VALUES (?, ?, ?)", (job_id, "queued", json.dumps(job))
            )
        return job

    def directory(self, job_id: str) -> Path:
        if len(job_id) != 32 or any(c not in "0123456789abcdef" for c in job_id):
            raise KeyError(job_id)
        return self.root / job_id

    def get(self, job_id: str):
        with self.connect() as db:
            row = db.execute("SELECT body FROM jobs WHERE id=?", (job_id,)).fetchone()
        if row is None:
            raise KeyError(job_id)
        return json.loads(row[0])

    def list(self):
        with self.connect() as db:
            rows = db.execute(
                "SELECT body FROM jobs ORDER BY rowid DESC LIMIT 100"
            ).fetchall()
        return [json.loads(row[0]) for row in rows]

    def transition(
        self, job_id: str, expected: set[str], state: str, message: str, **values
    ):
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT body FROM jobs WHERE id=?", (job_id,)).fetchone()
            if row is None:
                raise KeyError(job_id)
            job = json.loads(row[0])
            if job["state"] not in expected:
                raise Conflict(
                    f"Cannot perform this action while the job is {job['state']}"
                )
            if state == "sending":
                occupied = db.execute(
                    "SELECT 1 FROM jobs WHERE state IN ('sending', 'send_unknown') LIMIT 1"
                ).fetchone()
                if occupied:
                    raise Conflict(
                        "Another dispatch is active or uncertain. Inspect the printer before continuing."
                    )
            job.update(values, state=state, updated_at=now())
            job["events"].append({"at": now(), "message": message})
            db.execute(
                "UPDATE jobs SET state=?, body=? WHERE id=?",
                (state, json.dumps(job), job_id),
            )
        return job

    def recover(self):
        with self.connect() as db:
            rows = db.execute(
                "SELECT id, state FROM jobs WHERE state IN ('queued', 'generating', 'building', 'slicing', 'sending')"
            ).fetchall()
        for job_id, state in rows:
            next_state = "send_unknown" if state == "sending" else "failed"
            message = "Server restarted during the operation. Dispatches are never automatically retried."
            self.transition(job_id, {state}, next_state, message, error=message)
