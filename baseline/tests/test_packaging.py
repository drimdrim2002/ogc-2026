"""Audited, reproducible submission-package contracts for S6-05."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import tempfile
import unittest
import zipfile

from harness import package as submission_package
from harness.runner import run_s6_subprocess_case
from harness.selectors import select_s6_stress_instances


REPO_ROOT = Path(__file__).resolve().parents[2]


class PackagingTests(unittest.TestCase):
    def test_audited_isolated_package(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            first = submission_package.build_submission_package(
                root / "first",
                source_root=REPO_ROOT,
            )
            second = submission_package.build_submission_package(
                root / "second",
                source_root=REPO_ROOT,
            )

            self.assertEqual(first.archive_sha256, second.archive_sha256)
            self.assertEqual(first.archive_path.read_bytes(), second.archive_path.read_bytes())
            self.assertLessEqual(first.size_bytes, 15 * 1024 * 1024)
            self.assertTrue(first.audit.import_smoke_passed)
            self.assertEqual((), first.audit.prohibited_members)
            self.assertEqual((), first.audit.prohibited_text_hits)
            self.assertEqual((), first.audit.path_violations)

            with zipfile.ZipFile(first.archive_path) as archive:
                infos = archive.infolist()
                names = [info.filename for info in infos]
                self.assertEqual(sorted(names), names)
                self.assertEqual("myalgorithm.py", names[0])
                self.assertIn("solver/__init__.py", names)
                self.assertTrue(all(name.endswith(".py") for name in names))
                self.assertTrue(all(info.date_time == submission_package.ZIP_TIMESTAMP for info in infos))
                self.assertTrue(all(not name.startswith("/") and ".." not in Path(name).parts for name in names))
                self.assertFalse(any("tests" in Path(name).parts for name in names))
                self.assertFalse(any("harness" in Path(name).parts for name in names))
                extracted_hashes = {
                    info.filename: hashlib.sha256(archive.read(info)).hexdigest()
                    for info in infos
                }

            manifest = json.loads(first.manifest_path.read_text(encoding="utf-8"))
            self.assertEqual(first.archive_sha256, manifest["archive_sha256"])
            self.assertEqual(first.size_bytes, manifest["archive_size_bytes"])
            self.assertEqual(
                extracted_hashes,
                {row["path"]: row["sha256"] for row in manifest["entries"]},
            )
            self.assertEqual(
                manifest["protected_files"]["baseline/utils.py"]["head_object"],
                manifest["protected_files"]["baseline/utils.py"]["worktree_object"],
            )
            self.assertEqual(
                manifest["protected_files"]["baseline/baseline_greedy.py"]["head_object"],
                manifest["protected_files"]["baseline/baseline_greedy.py"]["worktree_object"],
            )

    def test_s6_stress_corpus_and_fault_worker(self):
        with tempfile.TemporaryDirectory() as directory:
            refs = select_s6_stress_instances(fixture_dir=Path(directory))
            by_id = {ref.instance_id: ref for ref in refs}
            self.assertEqual(
                {
                    "s6-one-bay",
                    "s6-one-layer",
                    "s6-p-zero",
                    "s6-contact",
                    "s6-preference-fallback",
                    "s6-dense",
                    "s6-cache-pressure",
                    "s6-max-training-n",
                },
                set(by_id),
            )
            self.assertEqual(300, len(by_id["s6-max-training-n"].prob_info["blocks"]))
            record = run_s6_subprocess_case(
                by_id["s6-one-bay"],
                timelimit=0.5,
                seed=20260710,
                features={
                    "alns": "true",
                    "acceptor": "sa",
                    "adaptive": "false",
                    "assignment_refinement": "false",
                    "parallel_portfolio": "false",
                    "interlock": "false",
                    "fault": "backend,after_incumbent",
                },
                fault="after_incumbent",
            )
            self.assertEqual("passed", record["status"])
            self.assertTrue(record["fault_applied"])
            self.assertTrue(record["checker"]["feasible"])
            self.assertEqual(5, record["checker"]["stage"])
            self.assertFalse(record["group_leak_detected"])
            self.assertFalse(record["group_alive_after_cleanup"])

    def test_s6_worker_emits_unmutated_same_day_solution(self):
        with tempfile.TemporaryDirectory() as directory:
            refs = select_s6_stress_instances(fixture_dir=Path(directory))
            dense = next(ref for ref in refs if ref.instance_id == "s6-dense")
            record = run_s6_subprocess_case(
                dense,
                timelimit=0.5,
                seed=20260710,
                features={
                    "alns": "true",
                    "acceptor": "sa",
                    "adaptive": "false",
                    "assignment_refinement": "false",
                    "parallel_portfolio": "false",
                    "interlock": "false",
                    "fault": "backend,after_incumbent",
                },
            )
            self.assertTrue(record["worker_checker"]["feasible"])
            self.assertEqual("passed", record["status"])
            self.assertTrue(record["checker"]["feasible"])
            self.assertEqual(5, record["checker"]["stage"])

    def test_s6_cache_pressure_forces_bounded_eviction(self):
        with tempfile.TemporaryDirectory() as directory:
            refs = select_s6_stress_instances(fixture_dir=Path(directory))
            pressure = next(
                ref for ref in refs if ref.instance_id == "s6-cache-pressure"
            )
            record = run_s6_subprocess_case(
                pressure,
                timelimit=0.5,
                seed=20260710,
                features={
                    "alns": "true",
                    "acceptor": "sa",
                    "adaptive": "false",
                    "assignment_refinement": "false",
                    "parallel_portfolio": "false",
                    "interlock": "false",
                    "fault": "backend,after_incumbent",
                },
            )
            self.assertEqual("passed", record["status"])
            self.assertGreater(record["cache"]["misses"], 32)
            self.assertGreater(record["cache"]["evictions"], 0)


if __name__ == "__main__":
    unittest.main()
