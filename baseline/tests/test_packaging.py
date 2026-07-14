from __future__ import annotations

import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
import zipfile
from contextlib import redirect_stderr, redirect_stdout
from dataclasses import FrozenInstanceError
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from experiments.ogc_sage.benchmark_ogc_sage import (  # noqa: E402
    build_archive,
    validate_archive_members,
)
from myalgorithm import algorithm  # noqa: E402
from solver.runtime import SubmissionConfig  # noqa: E402
from tests.helpers import checker, load_example  # noqa: E402


class PackagingContractTests(unittest.TestCase):
    def test_submission_config_is_immutable_and_environment_independent(self):
        before = SubmissionConfig.from_defaults()
        os.environ["OGC_LNS_ENABLED"] = "1"
        try:
            after = SubmissionConfig.from_defaults()
        finally:
            os.environ.pop("OGC_LNS_ENABLED", None)
        self.assertEqual(before, after)
        self.assertTrue(after.lns_enabled)
        self.assertFalse(after.interlock_enabled)
        with self.assertRaises(FrozenInstanceError):
            after.lns_enabled = False

    def test_benchmark_variants_are_explicit_and_dependency_safe(self):
        self.assertFalse(SubmissionConfig.for_benchmark("constructor_retime", seed=1).lns_enabled)
        self.assertTrue(SubmissionConfig.for_benchmark("candidate_mip", seed=1).mip_enabled)
        self.assertTrue(SubmissionConfig.for_benchmark("interlock", seed=1).interlock_enabled)
        with self.assertRaises(ValueError):
            SubmissionConfig(lns_enabled=False, mip_enabled=True)

    def test_public_algorithm_stdout_and_stderr_are_empty(self):
        stdout, stderr = io.StringIO(), io.StringIO()
        raw = load_example()
        with redirect_stdout(stdout), redirect_stderr(stderr):
            result = algorithm(raw, 2.0)
        self.assertEqual("", stdout.getvalue())
        self.assertEqual("", stderr.getvalue())
        self.assertEqual(5, checker(raw, result)["stage"])

    def test_archive_allowlist_size_and_clean_extraction_smoke(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            archive_path = root / "submission.zip"
            evidence = build_archive(archive_path)
            self.assertLessEqual(evidence["size_bytes"], 15 * 1024 * 1024)
            with zipfile.ZipFile(archive_path) as archive:
                names = archive.namelist()
                validate_archive_members(names)
                archive.extractall(root / "clean")
            clean = root / "clean"
            self.assertEqual(
                (ROOT / "baseline/utils.py").read_bytes(),
                (clean / "utils.py").read_bytes(),
            )
            (clean / "instance.json").write_text(json.dumps(load_example()), encoding="utf-8")
            script = (
                "import builtins, importlib.util, json, pathlib, sys\n"
                "root=pathlib.Path.cwd(); sys.path.insert(0,str(root))\n"
                "real=builtins.__import__\n"
                "def blocked(name,*a,**k):\n"
                "  if name=='gurobipy': raise ImportError('absent')\n"
                "  return real(name,*a,**k)\n"
                "builtins.__import__=blocked\n"
                "spec=importlib.util.spec_from_file_location('submission',root/'myalgorithm.py')\n"
                "mod=importlib.util.module_from_spec(spec); spec.loader.exec_module(mod)\n"
                "raw=json.loads((root/'instance.json').read_text())\n"
                "out=mod.algorithm(raw,2.0)\n"
                "from utils import check_feasibility\n"
                "checked=check_feasibility(raw,out)\n"
                "assert checked['feasible'] and checked['stage']==5, checked\n"
            )
            completed = subprocess.run(
                [sys.executable, "-I", "-c", script], cwd=clean,
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, check=False,
            )
            self.assertEqual(0, completed.returncode, completed.stderr)
            self.assertEqual("", completed.stdout)
            self.assertEqual("", completed.stderr)

    def test_forbidden_archive_member_is_rejected(self):
        for name in ("solver/test_x.py", "../myalgorithm.py", "data/prob_1.json"):
            with self.subTest(name=name), self.assertRaises(Exception):
                validate_archive_members(("myalgorithm.py", "utils.py", name))

    def test_archive_requires_root_utils_py(self):
        with self.assertRaises(Exception):
            validate_archive_members(("myalgorithm.py", "solver/entry.py"))


if __name__ == "__main__":
    unittest.main()
