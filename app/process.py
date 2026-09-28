from __future__ import annotations

import logging
import os
import signal
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path

from app.security.secrets import redact_secrets, sanitized_env

logger = logging.getLogger(__name__)


@dataclass
class ProcessResult:
    args: list[str]
    returncode: int
    stdout: str
    stderr: str
    duration_seconds: float
    timed_out: bool = False


class ProcessTimeout(Exception):
    def __init__(self, result: ProcessResult) -> None:
        self.result = result
        super().__init__(f"Process timed out after {result.duration_seconds:.1f}s")


def run_process(
    args: list[str],
    *,
    cwd: Path | None = None,
    env: dict[str, str] | None = None,
    timeout: int = 60,
) -> ProcessResult:
    start = time.monotonic()
    popen_kwargs: dict = {
        "args": args,
        "cwd": str(cwd) if cwd else None,
        "stdout": subprocess.PIPE,
        "stderr": subprocess.PIPE,
        "text": True,
        "env": sanitized_env(os.environ, env or {}),
        "shell": False,
    }
    if sys.platform == "win32":
        popen_kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
    else:
        popen_kwargs["start_new_session"] = True

    logger.info("process_start", extra={"argv0": args[0] if args else None, "cwd": str(cwd) if cwd else None})
    proc = subprocess.Popen(**popen_kwargs)
    timed_out = False
    try:
        stdout, stderr = proc.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        timed_out = True
        _terminate_process(proc)
        stdout, stderr = proc.communicate(timeout=10)
    duration = time.monotonic() - start
    result = ProcessResult(
        args=args,
        returncode=proc.returncode if proc.returncode is not None else -1,
        stdout=redact_secrets(stdout or ""),
        stderr=redact_secrets(stderr or ""),
        duration_seconds=duration,
        timed_out=timed_out,
    )
    if timed_out:
        raise ProcessTimeout(result)
    return result


def _terminate_process(proc: subprocess.Popen[str]) -> None:
    try:
        if sys.platform == "win32":
            proc.send_signal(signal.CTRL_BREAK_EVENT)
            try:
                proc.wait(timeout=5)
                return
            except subprocess.TimeoutExpired:
                proc.kill()
        else:
            os.killpg(proc.pid, signal.SIGTERM)
            try:
                proc.wait(timeout=5)
                return
            except subprocess.TimeoutExpired:
                os.killpg(proc.pid, signal.SIGKILL)
    except ProcessLookupError:
        return
    except OSError:
        proc.kill()
