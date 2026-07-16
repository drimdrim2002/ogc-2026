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
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from experiments.ogc_sage.benchmark_ogc_sage import (  # noqa: E402
    build_archive,
    validate_archive_members,
)
from scripts import build_submission_zip as submission_zip  # noqa: E402
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
        self.assertEqual("eager_regret", after.constructor_selection_policy)
        self.assertEqual("legacy", after.neighborhood_policy)
        self.assertEqual("native", after.repair_backend)
        self.assertEqual("native", after.native_exact_mode)
        self.assertFalse(after.native_prefilter_enabled)
        self.assertFalse(after.mip_enabled)
        self.assertFalse(after.interlock_enabled)
        with self.assertRaises(FrozenInstanceError):
            after.lns_enabled = False

    def test_benchmark_variants_are_explicit_and_dependency_safe(self):
        self.assertFalse(SubmissionConfig.for_benchmark("constructor_retime", seed=1).lns_enabled)
        self.assertEqual(
            "eager_regret",
            SubmissionConfig.for_benchmark(
                "heuristic_lns", seed=1
            ).constructor_selection_policy,
        )
        self.assertEqual(
            "profile_priority",
            SubmissionConfig.for_benchmark(
                "candidate_constructor", seed=1
            ).constructor_selection_policy,
        )
        self.assertTrue(SubmissionConfig.for_benchmark("candidate_mip", seed=1).mip_enabled)
        interlock = SubmissionConfig.for_benchmark("interlock", seed=1)
        self.assertTrue(interlock.interlock_enabled)
        self.assertFalse(interlock.mip_enabled)
        with self.assertRaises(ValueError):
            SubmissionConfig(lns_enabled=False, mip_enabled=True)
        with self.assertRaises(ValueError):
            SubmissionConfig(neighborhood_policy="unknown")

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


class SubmissionZipBuilderTests(unittest.TestCase):
    @staticmethod
    def _write_fake_amd64_elf(path: Path) -> None:
        header = bytearray(64)
        header[:7] = b"\x7fELF\x02\x01\x01"
        header[18:20] = (62).to_bytes(2, "little")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(header)

    def test_default_mode_requires_native_package(self):
        with self.assertRaisesRegex(
            submission_zip.SubmissionArchiveError, "native submission is the default"
        ):
            submission_zip.submission_members()

    def test_default_output_uses_seoul_build_date(self):
        instant = datetime(2026, 7, 15, 15, 30, tzinfo=timezone.utc)
        self.assertEqual(
            "ogc2026_submission_20260716.zip",
            submission_zip.default_output_path(instant).name,
        )

    def test_explicit_python_only_archive_keeps_legacy_allowlist(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "python-only.zip"
            evidence = submission_zip.build_archive(output, python_only=True)
            with zipfile.ZipFile(output) as archive:
                names = archive.namelist()
                self.assertEqual(evidence["members"], names)
                self.assertEqual(("myalgorithm.py", "utils.py"), tuple(names[:2]))
                self.assertTrue(all(name.endswith(".py") for name in names))
                self.assertIsNone(archive.testzip())
            self.assertEqual("python-only", evidence["validation"]["mode"])
            self.assertLessEqual(evidence["size_bytes"], submission_zip.MAX_ARCHIVE_BYTES)

    def test_native_package_maps_only_runtime_files_to_solver(self):
        with tempfile.TemporaryDirectory() as directory:
            package = Path(directory) / "package"
            extension = package / "_ogc_native.cpython-312-x86_64-linux-gnu.so"
            geos_c = package / "lib/libgeos_c.so.1"
            geos = package / "lib/libgeos.so.3.13.1"
            extra = package / "lib/libunused.so.1"
            for path in (extension, geos_c, geos, extra):
                self._write_fake_amd64_elf(path)
            (package / "SHA256SUMS").write_text("not submitted\n", encoding="utf-8")
            (package / "THIRD_PARTY_LICENSES").mkdir()
            (package / "THIRD_PARTY_LICENSES/GEOS.txt").write_text(
                "not submitted\n", encoding="utf-8"
            )

            def dynamic(path: Path, unused_tool: str):
                del unused_tool
                if path.name == extension.name:
                    return {"libgeos_c.so.1", "libstdc++.so.6", "libc.so.6"}, ("$ORIGIN/lib",)
                if path.name == geos_c.name:
                    return {"libgeos.so.3.13.1", "libc.so.6"}, ("$ORIGIN",)
                return {"libstdc++.so.6", "libm.so.6", "libc.so.6"}, ()

            with mock.patch.object(
                submission_zip.shutil,
                "which",
                side_effect=lambda name: "/usr/bin/readelf" if name == "readelf" else None,
            ), mock.patch.object(submission_zip, "_readelf_dynamic", side_effect=dynamic):
                members = submission_zip.submission_members(package)
                output = Path(directory) / "native.zip"
                archive_evidence = submission_zip.build_archive(output, package)

            self.assertIn(f"solver/{extension.name}", members)
            self.assertIn("solver/lib/libgeos_c.so.1", members)
            self.assertIn("solver/lib/libgeos.so.3.13.1", members)
            self.assertNotIn("solver/lib/libunused.so.1", members)
            self.assertNotIn("SHA256SUMS", members)
            self.assertFalse(any("LICENSE" in name for name in members))
            self.assertEqual("native", archive_evidence["validation"]["mode"])
            submission_zip.validate_member_names(members, members)
            with zipfile.ZipFile(output) as archive:
                self.assertEqual(archive_evidence["members"], archive.namelist())
                self.assertIsNone(archive.testzip())

    def test_native_package_rejects_missing_dependency_and_absolute_rpath(self):
        with tempfile.TemporaryDirectory() as directory:
            package = Path(directory) / "package"
            extension = package / "_ogc_native.cpython-312-x86_64-linux-gnu.so"
            library = package / "lib/libgeos_c.so.1"
            self._write_fake_amd64_elf(extension)
            self._write_fake_amd64_elf(library)
            with mock.patch.object(
                submission_zip.shutil,
                "which",
                side_effect=lambda name: "/usr/bin/readelf" if name == "readelf" else None,
            ), mock.patch.object(
                submission_zip,
                "_readelf_dynamic",
                return_value=({"libmissing.so.1"}, ("$ORIGIN/lib",)),
            ):
                with self.assertRaisesRegex(
                    submission_zip.SubmissionArchiveError, "shared library is missing"
                ):
                    submission_zip.submission_members(package)

        with self.assertRaisesRegex(
            submission_zip.SubmissionArchiveError, "absolute RPATH/RUNPATH"
        ):
            submission_zip._validate_runtime_paths(Path("extension.so"), ("/tmp/lib",))


if __name__ == "__main__":
    unittest.main()
