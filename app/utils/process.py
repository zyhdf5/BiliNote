import asyncio
import os
import signal
from collections import deque
from pathlib import Path
from typing import Sequence

from .logging import append_task_log, redact_text, redact_url


class ProcessExecutionError(RuntimeError):
    pass


async def _terminate_process(proc: asyncio.subprocess.Process) -> None:
    if proc.returncode is not None:
        return
    try:
        if os.name == "posix":
            os.killpg(proc.pid, signal.SIGTERM)
        else:
            proc.terminate()
    except ProcessLookupError:
        return
    try:
        await asyncio.wait_for(proc.wait(), timeout=3)
        return
    except TimeoutError:
        pass
    try:
        if os.name == "posix":
            os.killpg(proc.pid, signal.SIGKILL)
        else:
            proc.kill()
    except ProcessLookupError:
        pass
    await proc.wait()


async def run_logged_process(
    cmd: Sequence[str],
    *,
    timeout: float,
    log_path: Path,
    label: str,
    capture_stdout: bool = False,
    log_stdout: bool = True,
) -> str:
    """Run a child process with streamed per-task logs, timeout and cancellation cleanup."""
    safe_cmd = [redact_url(str(x)) for x in cmd]
    append_task_log(log_path, "exec: " + " ".join(safe_cmd), source=label)
    kwargs = {}
    if os.name == "posix":
        kwargs["start_new_session"] = True
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        **kwargs,
    )
    tail: deque[str] = deque(maxlen=120)
    captured: list[str] = []

    async def pump(stream, stream_name: str):
        while True:
            line = await stream.readline()
            if not line:
                break
            text = line.decode("utf-8", errors="replace").rstrip()
            if not text:
                continue
            if stream_name != "stdout" or log_stdout:
                append_task_log(log_path, text, source=f"{label}:{stream_name}")
            tail.append(redact_text(text))
            if capture_stdout and stream_name == "stdout":
                captured.append(text)

    stdout_task = asyncio.create_task(pump(proc.stdout, "stdout"))
    stderr_task = asyncio.create_task(pump(proc.stderr, "stderr"))
    try:
        try:
            await asyncio.wait_for(proc.wait(), timeout=timeout)
        except TimeoutError as exc:
            await _terminate_process(proc)
            raise ProcessExecutionError(f"{label} timed out after {int(timeout)}s") from exc
        await asyncio.gather(stdout_task, stderr_task)
        if proc.returncode != 0:
            detail = "\n".join(tail)[-8000:]
            raise ProcessExecutionError(f"{label} failed with exit code {proc.returncode}: {detail}")
        return "\n".join(captured)
    except asyncio.CancelledError:
        append_task_log(log_path, "cancellation requested; terminating child process", source=label)
        await _terminate_process(proc)
        raise
    finally:
        for t in (stdout_task, stderr_task):
            if not t.done():
                t.cancel()
        await asyncio.gather(stdout_task, stderr_task, return_exceptions=True)
