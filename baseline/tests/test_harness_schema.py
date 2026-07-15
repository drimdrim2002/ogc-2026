"""Proof-of-run, interruption, resume, and identity contracts for S0-06."""

from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest

from harness.schema import EvidenceRun, record_identity
from harness.cli import build_parser


class HarnessSchemaTests(unittest.TestCase):
    def test_all_eight_public_commands_parse(self):
        parser = build_parser()
        commands = (
            ["contract", "--suite", "semantic", "--instances", "synthetic"],
            ["parity", "--kind", "geometry", "--cases", "1000", "--instances", "synthetic"],
            ["benchmark", "--stage", "s0", "--instances", "training", "--timelimits", "5", "--seeds", "20260710"],
            ["stress", "--stage", "s0", "--instances", "stress", "--timelimits", "0.5", "--seeds", "20260710"],
            ["ab", "--stage", "s1", "--instances", "training", "--timelimits", "5", "--seeds", "20260710", "--a", "off", "--b", "on"],
            ["gate", "--stage", "s0", "--latest-complete", "--commit", "HEAD"],
            ["report", "--stage", "s0", "--latest-complete"],
            ["submission-rehearsal", "--instances", "training,stress", "--timelimits", "5,60,300", "--seed", "20260710", "--isolated"],
        )

        self.assertEqual(
            [items[0] for items in commands],
            [parser.parse_args(items).command for items in commands],
        )

    def test_interrupted_run_is_not_complete(self):
        with tempfile.TemporaryDirectory() as directory:
            run_dir = Path(directory) / "s0" / "contract" / "run-1"
            run = EvidenceRun.start(
                run_dir,
                command=["python", "-m", "baseline.harness.cli", "contract"],
                expected_record_ids=("first", "second"),
                metadata={"stage": "s0", "kind": "contract"},
            )
            run.append_record(
                {"record_id": "first", "status": "passed", "complete": True}
            )
            run.interrupt()

            self.assertTrue((run_dir / "run.json").is_file())
            self.assertFalse((run_dir / "COMPLETE").exists())
            self.assertEqual(("second",), EvidenceRun.resume(run_dir).pending_record_ids)
            self.assertTrue(json.loads((run_dir / "run.json").read_text())["started"])
            self.assertTrue(json.loads((run_dir / "run.json").read_text())["interrupted"])

    def test_finalize_requires_every_terminal_record_and_indexes_identities(self):
        with tempfile.TemporaryDirectory() as directory:
            run_dir = Path(directory) / "s0" / "benchmark" / "run-2"
            run = EvidenceRun.start(
                run_dir,
                command=["solver"],
                expected_record_ids=("one", "two"),
                metadata={"stage": "s0"},
            )
            run.append_record(
                {
                    "record_id": "one",
                    "identity": "identity-a",
                    "status": "passed",
                    "complete": True,
                }
            )
            with self.assertRaisesRegex(RuntimeError, "missing terminal records"):
                run.finalize({"status": "passed"})

            resumed = EvidenceRun.resume(run_dir)
            resumed.append_record(
                {
                    "record_id": "two",
                    "identity": "identity-b",
                    "status": "passed",
                    "complete": True,
                }
            )
            resumed.finalize({"status": "passed"})

            self.assertTrue((run_dir / "COMPLETE").is_file())
            self.assertEqual(
                frozenset({"identity-a", "identity-b"}),
                EvidenceRun.completed_identities(run_dir),
            )

    def test_record_identity_is_order_stable_and_content_sensitive(self):
        common = {
            "commit": "abc",
            "dirty_diff_hash": "clean",
            "instance_sha": "instance",
            "solver": "native",
            "timelimit": 5,
            "seed": 20260710,
        }
        left = record_identity(**common, features={"b": "2", "a": "1"})
        right = record_identity(**common, features={"a": "1", "b": "2"})
        changed = record_identity(**common, features={"a": "1", "b": "3"})

        self.assertEqual(left, right)
        self.assertNotEqual(left, changed)
        self.assertEqual(64, len(left))


if __name__ == "__main__":
    unittest.main()
