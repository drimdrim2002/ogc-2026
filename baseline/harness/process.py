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
    pid: int
    argv: tuple[str, ...]
    exit_code: int | None
    signal: int | None
    stdout: str
    stderr: str
    wall_seconds: float
    timed_out: bool
    term_sent: bool
    kill_sent: bool
    group_leak_detected: bool
    group_alive_after_cleanup: bool


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
    group_leak_detected = _group_exists(process.pid)
    if group_leak_detected:
        term_sent = True
        _signal_group(process.pid, signal.SIGTERM)
        deadline = time.monotonic() + max(0.0, terminate_grace)
        while _group_exists(process.pid) and time.monotonic() < deadline:
            time.sleep(0.01)
        if _group_exists(process.pid):
            kill_sent = True
            _signal_group(process.pid, signal.SIGKILL)
    group_alive_after_cleanup = _group_exists(process.pid)
    return_code = process.returncode
    return ProcessResult(
        pid=process.pid,
        argv=tuple(str(item) for item in argv),
        exit_code=return_code if return_code is not None and return_code >= 0 else None,
        signal=-return_code if return_code is not None and return_code < 0 else None,
        stdout=stdout,
        stderr=stderr,
        wall_seconds=time.monotonic() - started,
        timed_out=timed_out,
        term_sent=term_sent,
        kill_sent=kill_sent,
        group_leak_detected=group_leak_detected,
        group_alive_after_cleanup=group_alive_after_cleanup,
    )


def _signal_group(pid: int, signum: signal.Signals) -> None:
    try:
        os.killpg(pid, signum)
    except ProcessLookupError:
        pass


def _group_exists(pid: int) -> bool:
    try:
        os.killpg(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True
