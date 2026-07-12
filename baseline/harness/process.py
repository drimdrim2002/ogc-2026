"""POSIX process-group execution with bounded termination and captured proof."""

from __future__ import annotations

from dataclasses import dataclass
import os
import signal
import subprocess
import time
from typing import Mapping, Sequence


@dataclass(frozen=True, slots=True)
class ProcessResult:
    argv: tuple[str, ...]
    exit_code: int | None
    signal: int | None
    stdout: str
    stderr: str
    wall_seconds: float
    timed_out: bool
    term_sent: bool
    kill_sent: bool


def run_process(
    argv: Sequence[str],
    *,
    timeout: float,
    terminate_grace: float = 2.0,
    cwd: str | None = None,
    env: Mapping[str, str] | None = None,
) -> ProcessResult:
    """Run argv in a new session and reap its entire group on timeout."""
    started = time.monotonic()
    process = subprocess.Popen(
        list(argv),
        cwd=cwd,
        env=None if env is None else dict(env),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        start_new_session=True,
    )
    timed_out = False
    term_sent = False
    kill_sent = False
    try:
        stdout, stderr = process.communicate(timeout=max(0.0, timeout))
    except subprocess.TimeoutExpired:
        timed_out = True
        term_sent = True
        _signal_group(process.pid, signal.SIGTERM)
        try:
            stdout, stderr = process.communicate(timeout=max(0.0, terminate_grace))
        except subprocess.TimeoutExpired:
            kill_sent = True
            _signal_group(process.pid, signal.SIGKILL)
            stdout, stderr = process.communicate()
    return_code = process.returncode
    return ProcessResult(
        argv=tuple(str(item) for item in argv),
        exit_code=return_code if return_code is not None and return_code >= 0 else None,
        signal=-return_code if return_code is not None and return_code < 0 else None,
        stdout=stdout,
        stderr=stderr,
        wall_seconds=time.monotonic() - started,
        timed_out=timed_out,
        term_sent=term_sent,
        kill_sent=kill_sent,
    )


def _signal_group(pid: int, signum: signal.Signals) -> None:
    try:
        os.killpg(pid, signum)
    except ProcessLookupError:
        pass
