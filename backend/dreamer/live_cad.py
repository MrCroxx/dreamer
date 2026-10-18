"""A single CAD worker per attempt, fed while the model response arrives."""

import asyncio
import json
import os
import signal
import sys
from pathlib import Path


class LiveCAD:
    def __init__(self, directory: Path, settings):
        self.directory, self.settings = directory, settings
        self.process = None
        self.reader = None
        self.tail = bytearray()
        self.operations = []

    async def __aenter__(self):
        self.clear()
        env = os.environ.copy()
        env["PYTHONPATH"] = str(Path(__file__).resolve().parents[1])
        self.process = await asyncio.create_subprocess_exec(
            sys.executable,
            "-m",
            "dreamer.cad_worker",
            str(self.directory),
            "--stream",
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            env=env,
            start_new_session=True,
        )
        self.reader = asyncio.create_task(self.collect())
        return self

    async def collect(self):
        while block := await self.process.stdout.read(8192):
            self.tail.extend(block)
            del self.tail[:-32768]
        await self.process.wait()

    async def write(self, message):
        try:
            self.process.stdin.write((json.dumps(message) + "\n").encode())
            await self.process.stdin.drain()
        except (BrokenPipeError, ConnectionResetError):
            await self.reader
            raise ValueError(
                self.tail.decode(errors="replace")[-3000:] or "CAD worker stopped"
            )

    async def operation(self, op):
        self.operations.append(op.model_dump(exclude_defaults=True, exclude_none=True))
        await self.write(
            {"operation": op.model_dump(), "preview": self.settings.live_preview}
        )

    async def finish(self, plan):
        async with asyncio.timeout(self.settings.cad_timeout):
            await self.write({"plan": plan.model_dump()})
            self.process.stdin.close()
            await asyncio.shield(self.reader)
            if self.process.returncode:
                raise ValueError(
                    self.tail.decode(errors="replace")[-3000:]
                    or "CAD construction failed"
                )
        return json.loads((self.directory / "geometry.json").read_text())

    def clear(self):
        for path in self.directory.glob("preview-*.stl"):
            path.unlink(missing_ok=True)
        (self.directory / "preview.json").unlink(missing_ok=True)
        (self.directory / "preview.json.tmp").unlink(missing_ok=True)

    async def __aexit__(self, *_):
        if self.process and self.process.returncode is None:
            try:
                os.killpg(self.process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
        if self.reader:
            await self.reader
        self.clear()
