"""Bounded, append-only model output with resumable reads."""

import json
from pathlib import Path

MAX_BYTES = 8_000_000
PAGE_BYTES = 65_536


class OutputLog:
    def __init__(self, directory: Path, attempt: int):
        self.path = directory / "output.jsonl"
        self.attempt = attempt
        self.file = None
        self.limited = False

    async def __aenter__(self):
        try:
            self.file = self.path.open("ab")
            self.size = self.path.stat().st_size
            self.limited = self.size >= MAX_BYTES
            self.append({"kind": "attempt", "attempt": self.attempt})
        except OSError:
            self.close()
        return self

    def append(self, event):
        if not self.file or self.limited:
            return
        data = (json.dumps(event, ensure_ascii=False) + "\n").encode()
        if self.size + len(data) > MAX_BYTES:
            data = (
                json.dumps(
                    {
                        "kind": "notice",
                        "text": "Output recording reached its 8 MB limit. Model generation continues.",
                    }
                )
                + "\n"
            ).encode()
            self.limited = True
        try:
            self.file.write(data)
            self.file.flush()
            self.size += len(data)
        except OSError:
            # Recording is optional and must not fail model generation.
            self.close()

    def text(self, kind: str, text: str):
        for start in range(0, len(text), 4096):
            self.append({"kind": kind, "text": text[start : start + 4096]})

    def close(self):
        if self.file:
            try:
                self.file.close()
            except OSError:
                pass
            self.file = None

    async def __aexit__(self, exc_type, *_):
        self.append(
            {
                "kind": "end",
                "status": "completed" if exc_type is None else "interrupted",
            }
        )
        self.close()


def read_output(directory: Path, offset: int) -> dict:
    with (directory / "output.jsonl").open("rb") as source:
        source.seek(0, 2)
        size = source.tell()
        if offset > size:
            raise ValueError("Output cursor exceeds the file size")
        if offset:
            source.seek(offset - 1)
            if source.read(1) != b"\n":
                raise ValueError("Output cursor is not a record boundary")
        source.seek(offset)
        data = source.read(PAGE_BYTES)
    # A concurrent write may leave the last record unfinished until the next poll.
    complete = data[: data.rfind(b"\n") + 1]
    cursor = offset + len(complete)
    return {
        "entries": [json.loads(line) for line in complete.splitlines()],
        "offset": cursor,
        "has_more": cursor < size,
    }
