"""Deadline and verified-fallback contracts for S0-05."""

from __future__ import annotations

import unittest
from unittest.mock import patch

from solver import entry as solver_entry
from solver.alns import ALNSMetrics, AnytimeRunResult
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
    @staticmethod
    def _no_op_anytime(_state, incumbent, _rng, **_kwargs):
        return AnytimeRunResult(
            solution=incumbent.solution,
            metrics=ALNSMetrics(),
            incumbent_trace=(),
            stopped_reason="max_epochs",
            epoch_count=0,
            first_epoch_trace=(),
            prefix_consistent=True,
            operator_metrics=(),
            epoch_solutions=(),
        )

    @staticmethod
    def _s4_fixture():
        return instance(
            [
                block(release=0, due=2, processing=2),
                block(release=0, due=3, processing=2),
                block(release=1, due=4, processing=1),
                block(release=2, due=5, processing=1),
            ]
        )

    def test_assignment_refinement_insufficient_work_skips_explicitly(self):
        prob_info = self._s4_fixture()
        telemetry = {}
        with patch(
            "solver.entry.run_anytime_epochs",
            side_effect=self._no_op_anytime,
        ):
            s3 = solve(
                prob_info,
                7.0,
                _retime=False,
                _alns=True,
                _assignment_refinement=False,
            )
            skipped = solve(
                prob_info,
                7.0,
                _retime=False,
                _alns=True,
                _assignment_refinement=True,
                _assignment_v2=True,
                _cross_bay=False,
                _telemetry=telemetry,
            )

        self.assertEqual(s3, skipped)
        self.assertEqual("skipped_insufficient_work", telemetry["s4_worker_status"])
        self.assertEqual("minimum_useful_work_not_available", telemetry["s4_skip_reason"])
        self.assertFalse(telemetry["s4_worker_launched"])
        self.assertNotIn("assignment_seed_attempts", telemetry)
        checked = official_check(prob_info, skipped)
        self.assertTrue(checked.feasible, checked.violations)
        self.assertEqual(5, checked.stage)

    def test_assignment_refinement_sufficient_work_launches_and_attempts(self):
        prob_info = self._s4_fixture()
        telemetry = {}
        with patch(
            "solver.entry.run_anytime_epochs",
            side_effect=self._no_op_anytime,
        ):
            s3 = solve(
                prob_info,
                12.0,
                _retime=False,
                _alns=True,
                _assignment_refinement=False,
            )
            refined = solve(
                prob_info,
                12.0,
                _retime=False,
                _alns=True,
                _assignment_refinement=True,
                _assignment_v2=True,
                _cross_bay=False,
                _telemetry=telemetry,
            )

        self.assertTrue(telemetry["s4_worker_launched"])
        self.assertIn(telemetry["s4_worker_status"], {"completed", "completed_after_budget"})
        self.assertIn("assignment_seed_attempts", telemetry)
        self.assertGreaterEqual(len(telemetry["assignment_seed_attempts"]), 1)
        self.assertLessEqual(
            official_check(prob_info, refined).objective,
            official_check(prob_info, s3).objective,
        )

    def test_assignment_refinement_failure_keeps_s3(self):
        prob_info = self._s4_fixture()
        with patch(
            "solver.entry.run_anytime_epochs",
            side_effect=self._no_op_anytime,
        ):
            s3 = solve(
                prob_info,
                12.0,
                _retime=False,
                _alns=True,
                _assignment_refinement=False,
            )
            faulted = solve(
                prob_info,
                12.0,
                _retime=False,
                _alns=True,
                _assignment_refinement=True,
                _fault="during_assignment_refinement",
            )

        self.assertEqual(s3, faulted)
        checked = official_check(prob_info, faulted)
        self.assertTrue(checked.feasible, checked.violations)
        self.assertEqual(5, checked.stage)

    def test_selected_s3_default_runs_alns_without_override(self):
        prob_info = instance(
            [
                block(release=0, due=2, processing=2),
                block(release=0, due=3, processing=2),
                block(release=1, due=4, processing=1),
                block(release=2, due=5, processing=1),
            ]
        )
        telemetry = {}

        solution = solve(
            prob_info,
            12.0,
            _retime=False,
            _telemetry=telemetry,
        )

        checked = official_check(prob_info, solution)
        self.assertTrue(checked.feasible, checked.violations)
        self.assertEqual(5, checked.stage)
        self.assertGreater(telemetry["alns_metrics"]["iterations"], 0)
        self.assertTrue(telemetry["alns_prefix_consistent"])
        self.assertEqual("sa", telemetry["alns_acceptor"])
        self.assertFalse(telemetry["alns_adaptive"])

    def test_alns_entry_and_failure_keep_verified_s2_incumbent(self):
        prob_info = instance(
            [
                block(release=0, due=2, processing=2),
                block(release=0, due=3, processing=2),
                block(release=1, due=4, processing=1),
                block(release=2, due=5, processing=1),
            ]
        )
        s2 = solve(prob_info, 12.0, _alns=False)
        faulted = solve(
            prob_info,
            12.0,
            _alns=True,
            _fault="during_alns",
        )
        self.assertEqual(s2, faulted)

        telemetry = {}
        integrated = solve(
            prob_info,
            12.0,
            _retime=False,
            _alns=True,
            _telemetry=telemetry,
        )
        checked = official_check(prob_info, integrated)
        self.assertTrue(checked.feasible, checked.violations)
        self.assertEqual(5, checked.stage)
        self.assertGreater(telemetry["alns_metrics"]["iterations"], 0)
        self.assertTrue(telemetry["alns_prefix_consistent"])
        self.assertEqual("sa", telemetry["alns_acceptor"])
        self.assertFalse(telemetry["alns_adaptive"])

    def test_retime_failure_keeps_constructor(self):
        prob_info = instance(
            [
                block(release=0, due=2, processing=2),
                block(release=0, due=3, processing=2),
                block(release=1, due=4, processing=1),
            ]
        )
        constructor = solve(prob_info, 12.0, _retime=False)

        faulted = solve(
            prob_info,
            12.0,
            _retime=True,
            _fault="during_retime",
        )

        self.assertEqual(constructor, faulted)
        checked = official_check(prob_info, faulted)
        self.assertTrue(checked.feasible, checked.violations)
        self.assertEqual(5, checked.stage)

    def test_constructor_failure_keeps_t0(self):
        prob_info = instance(
            [
                block(release=0, due=8, processing=2),
                block(release=0, due=8, processing=2),
                block(release=1, due=9, processing=1),
            ]
        )
        t0 = solve(prob_info, 5.0, _constructor=False)

        faulted = solve(
            prob_info,
            5.0,
            _constructor=True,
            _fault="during_constructor",
        )

        self.assertEqual(t0, faulted)
        checked = official_check(prob_info, faulted)
        self.assertTrue(checked.feasible, checked.violations)
        self.assertEqual(5, checked.stage)

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
                normal = solve(prob_info, timelimit, _constructor=False)
                faulted = solve(
                    prob_info,
                    timelimit,
                    _constructor=False,
                    _fault="after_incumbent",
                )
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
