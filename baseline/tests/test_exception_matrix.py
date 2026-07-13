from __future__ import annotations

import builtins
import copy
import unittest
from unittest.mock import patch

from solver.entry import OptionalPhaseResult, solve
from solver.state import Placement, SolutionSnapshot
from tests.helpers import checker, load_example


class ExceptionMatrixTests(unittest.TestCase):
    def test_optional_import_phase_and_shapely_style_errors_preserve_stage_five(self):
        raw = load_example()
        failures = (
            ImportError("gurobipy absent"),
            RuntimeError("license error"),
            RuntimeError("size limit"),
            RuntimeError("model error"),
            ArithmeticError("numeric"),
            TimeoutError("timeout"),
            RuntimeError("SolCount unavailable"),
            RuntimeError("Shapely candidate error"),
        )
        for failure in failures:
            with self.subTest(failure=str(failure)):
                def loader(error=failure):
                    raise error
                output = solve(raw, 5.0, optional_phase_loader=loader)
                checked = checker(raw, output)
                self.assertTrue(checked["feasible"], checked)
                self.assertEqual(5, checked["stage"])

    def test_checker_rejection_rolls_back_to_validated_best(self):
        raw = load_example()
        calls = 0

        def rejecting_checker(prob, operations):
            nonlocal calls
            calls += 1
            if calls == 1:
                return checker(prob, operations)
            return {"feasible": False, "stage": 4, "violations": ["forced"], "objective": None}

        def loader():
            def phase(instance, snapshot, budget):
                placement = snapshot.placements[0]
                candidate = list(snapshot.placements)
                candidate[0] = Placement(
                    placement.block_id, placement.bay_id, placement.orient_idx,
                    placement.x, placement.y, placement.entry, placement.exit + 1,
                )
                return OptionalPhaseResult(candidates=(SolutionSnapshot(tuple(candidate)),))
            return phase

        output = solve(raw, 5.0, checker=rejecting_checker, optional_phase_loader=loader)
        self.assertEqual(5, checker(raw, output)["stage"])

    def test_real_gurobipy_import_absence_keeps_pure_python_stage_five(self):
        raw = load_example()
        real_import = builtins.__import__

        def blocked(name, *args, **kwargs):
            if name == "gurobipy":
                raise ImportError("forced absent")
            return real_import(name, *args, **kwargs)

        with patch("builtins.__import__", side_effect=blocked):
            output = solve(raw, 2.0)
        self.assertEqual(5, checker(raw, output)["stage"])


if __name__ == "__main__":
    unittest.main()
