"""Deadline and verified-fallback contracts for S0-05."""

from __future__ import annotations

import unittest
from unittest.mock import patch

from solver import alns
from solver.budget import Budget, BudgetExpired, deadline_reserve
from solver.checker_adapter import official_check
from solver.entry import solve
from solver.incumbent import VerifiedIncumbent
from solver.instance import ProblemInstance, UnsolvableInstanceError
from solver.state import Placement, SolutionState
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

    def test_s3_extension_uses_parent_bounded_segments_and_repeats_batches(self):
        prob_info = instance(
            [block(layers=(TWO_SQUARE,), due=0, processing=1, preferences=(0,))],
            bays=((2, 2),),
            weights={"w1": 1.0, "w2": 0.0, "w3": 0.0},
        )
        parsed = ProblemInstance.parse(prob_info)
        state = SolutionState(parsed)
        state.place(Placement(0, 0, 0, 0, 0, 99, 100))
        incumbent = VerifiedIncumbent(parsed)
        incumbent.register_initial(state)
        checkpoint = incumbent.export_checkpoint()

        class FakeClock:
            def __init__(self):
                self.now = 100.0

            def __call__(self):
                return self.now

        def exercise(limit):
            clock = FakeClock()
            parent = Budget(limit, clock=clock, reserve=0.0)
            child_deadlines = []
            batch_starts = []
            destroy_scales = []

            def fake_batch(_state, active_incumbent, _rng, **kwargs):
                child_deadlines.append(kwargs["budget"].deadline)
                batch_starts.append(clock.now)
                destroy_scales.append(kwargs["initial_destroy_scale"])
                self.assertEqual(24, kwargs["max_iterations"])
                clock.now += min(2.0, kwargs["budget"].remaining)
                return alns.ALNSRunResult(
                    solution=active_incumbent.solution,
                    metrics=alns.ALNSMetrics(iterations=24),
                    incumbent_trace=(),
                    stopped_reason="max_iterations",
                )

            with (
                patch("solver.alns.run_alns", side_effect=fake_batch),
                patch("solver.alns.same_bay_restart", return_value=False),
            ):
                result = alns.run_s3_extension(
                    checkpoint,
                    parent,
                    alns.S3ExtensionProfile(
                        segment_seconds=8.0,
                        iterations_per_batch=24,
                        remove_count=1,
                        acceptor_name="strict",
                        adaptive=False,
                    ),
                    17,
                    {},
                    prob_info=prob_info,
                )
            return parent, child_deadlines, batch_starts, destroy_scales, result

        parent, deadlines, starts, scales, result = exercise(24.0)
        self.assertEqual(3, result.metrics.segments_started)
        self.assertEqual(12, result.metrics.batches_started)
        self.assertEqual(288, result.metrics.iterations)
        self.assertEqual("work_deadline", result.stopped_reason)
        self.assertEqual([108.0] * 4 + [116.0] * 4 + [124.0] * 4, deadlines)
        self.assertEqual([1.0] * 4 + [1.5] * 4 + [2.0] * 4, scales)
        self.assertEqual(
            (
                (0, 17, 1.0, "continue"),
                (1, 104746, 1.5, "verified_restart"),
                (2, 209475, 2.0, "same_bay_restart"),
            ),
            tuple(
                (
                    item.segment_index,
                    item.seed,
                    item.destroy_scale,
                    item.restart_policy,
                )
                for item in result.segment_profiles
            ),
        )
        self.assertGreater(starts.count(100.0), 0)
        self.assertEqual(parent.deadline, max(deadlines))

        short_parent, short_deadlines, _, _, short = exercise(5.0)
        self.assertEqual(1, short.metrics.segments_started)
        self.assertGreater(short.metrics.batches_started, 1)
        self.assertTrue(all(item == short_parent.deadline for item in short_deadlines))
        self.assertEqual("work_deadline", short.stopped_reason)


class EntryArmorTests(unittest.TestCase):
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
