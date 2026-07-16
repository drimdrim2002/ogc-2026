from __future__ import annotations

import io
import unittest
from contextlib import redirect_stderr, redirect_stdout
from unittest.mock import patch

from solver.budget import Budget
from solver.fallback import build_safe_candidate
from solver.instance import parse_instance
from solver.native_repair import empty_kernel_info, load_native_module, with_python_fallback
from solver.serialize import serialize
from tests.helpers import checker, load_example


class NativeImportFallbackTests(unittest.TestCase):
    def test_absent_or_abi_failure_is_silent_and_uses_python_reference(self):
        raw = load_example()
        parsed = parse_instance(raw)
        expected = build_safe_candidate(parsed, Budget.start(2.0))
        stdout, stderr = io.StringIO(), io.StringIO()
        with (
            patch("solver.native_repair.importlib.import_module", side_effect=OSError("bad ABI")),
            redirect_stdout(stdout),
            redirect_stderr(stderr),
        ):
            actual = with_python_fallback(
                lambda _: self.fail("native call must not run"),
                lambda: build_safe_candidate(parsed, Budget.start(2.0)),
            )
            self.assertIsNone(load_native_module())
            self.assertIsNone(empty_kernel_info())
        self.assertEqual("", stdout.getvalue())
        self.assertEqual("", stderr.getvalue())
        self.assertEqual(expected, actual)
        result = checker(raw, serialize(actual))
        self.assertTrue(result["feasible"], result)
        self.assertEqual(5, result["stage"])

    def test_native_runtime_failure_preserves_python_result(self):
        raw = load_example()
        parsed = parse_instance(raw)
        marker = object()
        with patch("solver.native_repair.load_native_module", return_value=marker):
            result = with_python_fallback(
                lambda _: (_ for _ in ()).throw(RuntimeError("kernel failure")),
                lambda: build_safe_candidate(parsed, Budget.start(2.0)),
            )
        checked = checker(raw, serialize(result))
        self.assertTrue(checked["feasible"], checked)
        self.assertEqual(5, checked["stage"])


if __name__ == "__main__":
    unittest.main()
