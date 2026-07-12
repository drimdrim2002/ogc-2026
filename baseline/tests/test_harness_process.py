"""Subprocess proof and process-group cleanup contracts for S0-06."""

from __future__ import annotations

import os
from pathlib import Path
import tempfile
import time
import unittest

from harness.process import run_process


PYTHON = "/opt/homebrew/Caskroom/miniforge/base/envs/ogc-2026/bin/python"


class HarnessProcessTests(unittest.TestCase):
    def test_runner_captures_successful_proof_of_run(self):
        result = run_process(
            [PYTHON, "-c", "print('proof-of-run')"],
            timeout=2.0,
        )

        self.assertEqual(0, result.exit_code)
        self.assertFalse(result.timed_out)
        self.assertIn("proof-of-run", result.stdout)
        self.assertGreaterEqual(result.wall_seconds, 0.0)
        self.assertIsNone(result.signal)

    @unittest.skipUnless(hasattr(os, "killpg"), "requires POSIX process groups")
    def test_timeout_kills_the_whole_process_group(self):
        with tempfile.TemporaryDirectory() as directory:
            pid_path = Path(directory) / "child.pid"
            program = (
                "import pathlib,subprocess,sys,time; "
                "child=subprocess.Popen([sys.executable,'-c','import time; time.sleep(60)']); "
                "pathlib.Path(sys.argv[1]).write_text(str(child.pid)); "
                "time.sleep(60)"
            )
            result = run_process(
                [PYTHON, "-c", program, str(pid_path)],
                timeout=0.5,
                terminate_grace=0.1,
            )

            self.assertTrue(result.timed_out)
            self.assertTrue(result.term_sent)
            self.assertIsNotNone(result.signal)
            child_pid = int(pid_path.read_text())
            deadline = time.monotonic() + 2.0
            while time.monotonic() < deadline and _process_exists(child_pid):
                time.sleep(0.02)
            self.assertFalse(_process_exists(child_pid), f"child {child_pid} survived")


def _process_exists(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    try:
        status = Path(f"/proc/{pid}/stat").read_text().split()[2]
    except (FileNotFoundError, IndexError):
        return True
    return status != "Z"


if __name__ == "__main__":
    unittest.main()
