import asyncio
import os
import signal


async def run_command(argv: list[str], timeout: int, *, env: dict | None = None) -> str:
    """Never invoke a shell. Drain bounded output and terminate the process group on timeout."""
    process = await asyncio.create_subprocess_exec(
        *argv,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
        env=env,
        start_new_session=True,
    )
    tail = bytearray()

    async def collect():
        while chunk := await process.stdout.read(8192):
            tail.extend(chunk)
            if len(tail) > 32_768:
                del tail[:-32_768]
        await process.wait()

    try:
        await asyncio.wait_for(collect(), timeout)
    except (asyncio.TimeoutError, asyncio.CancelledError):
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        await process.wait()
        raise
    output = tail.decode(errors="replace")
    if process.returncode:
        raise ValueError(
            f"Command exited with code {process.returncode}: {output[-3000:]}"
        )
    return output
