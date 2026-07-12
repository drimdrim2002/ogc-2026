"""Deadline and verified-fallback contracts for S0-05."""

from __future__ import annotations

import unittest

from solver.budget import Budget, BudgetExpired, deadline_reserve
from solver.checker_adapter import official_check
from solver.entry import solve
from solver.instance import UnsolvableInstanceError
from tests.fixtures import TWO_SQUARE, block, instance


class BudgetTests(unittest.TestCase):
    def test_reserve_matrix_and_cooperative_allowance(self):
        self.assertEqual(0.05, deadline_reserve(0.5))
        self.assertEqual(0.1, deadline_reserve(2))
        self.assertEqual(3.0, deadline_reserve(5))
        self.assertEqual(3.0, deadline_reserve(12))

        now = [100.0]
        budget = Budget(2, clock=lambda: now[0])
        self.assertAlmostEqual(1.9, budget.remaining)
        self.assertAlmostEqual(0.475, budget.stage_allowance(0.5, cap=0.475))
        budget.checkpoint("test stage")
        now[0] = 101.9
        with self.assertRaisesRegex(BudgetExpired, "test stage"):
            budget.checkpoint("test stage")


class EntryArmorTests(unittest.TestCase):
    def test_exception_returns_verified_serial(self):
        prob_info = instance(
            [
                block(release=0, processing=2),
                block(release=1, processing=1),
            ]
        )

        solution = solve(prob_info, 0.5, _fault="after_incumbent")

        checked = official_check(prob_info, solution)
        self.assertTrue(checked.feasible, checked.violations)
        self.assertEqual(5, checked.stage)

    def test_tiny_budget_and_ordinary_faults_keep_verified_t0(self):
        prob_info = instance([block(), block(release=1)])
        for timelimit in (0.0, 0.5, 2.0, 5.0, 12.0):
            with self.subTest(timelimit=timelimit):
                normal = solve(prob_info, timelimit)
                faulted = solve(prob_info, timelimit, _fault="after_incumbent")
                self.assertEqual(normal, faulted)
                self.assertTrue(official_check(prob_info, faulted).feasible)

    def test_base_exceptions_are_not_swallowed(self):
        prob_info = instance([block()])

        def interrupt(_point):
            raise KeyboardInterrupt

        with self.assertRaises(KeyboardInterrupt):
            solve(prob_info, 1.0, _fault=interrupt)

        def terminate(_point):
            raise SystemExit(17)

        with self.assertRaisesRegex(SystemExit, "17"):
            solve(prob_info, 1.0, _fault=terminate)

    def test_preverification_failure_has_no_unverified_fallback(self):
        no_fit = instance([block(layers=(TWO_SQUARE,))], bays=((1, 1),))

        with self.assertRaisesRegex(UnsolvableInstanceError, "block 0"):
            solve(no_fit, 1.0)


if __name__ == "__main__":
    unittest.main()
