"""Deadline and verified-fallback contracts for S0-05."""

from __future__ import annotations

import hashlib
import json
import math
import unittest
from unittest.mock import patch

from solver import alns
from solver.budget import Budget, BudgetExpired, deadline_reserve
from solver.checker_adapter import official_check
from solver.config import DEFAULT_CONFIG
from solver.entry import solve
from solver.incumbent import VerifiedIncumbent
from solver.instance import ProblemInstance, UnsolvableInstanceError
from solver.state import Placement, SolutionState
from tests.fixtures import TWO_SQUARE, block, instance


EXPECTED_FEATURE_OFF_SHA256 = (
    "57dbb1d360f47dbe9365e8d68bef5529562b6c423711e68e1c09b0860c243a73"
)


class _FakeClock:
    def __init__(self) -> None:
        self.now = 100.0

    def __call__(self) -> float:
        return self.now


class _FakeParentBudget:
    def __init__(self, timelimit: float, clock: _FakeClock) -> None:
        self.timelimit = float(timelimit)
        self.reserve = deadline_reserve(self.timelimit)
        self.started_at = clock.now
        self.deadline = self.started_at + self.timelimit - self.reserve
        self.hard_deadline = self.started_at + self.timelimit
        self._clock = clock

    @property
    def remaining(self) -> float:
        return max(0.0, self.deadline - self._clock.now)

    @property
    def hard_remaining(self) -> float:
        return max(0.0, self.hard_deadline - self._clock.now)

    def checkpoint(self, label: str = "solver") -> None:
        if self.remaining <= 0.0:
            raise BudgetExpired(f"{label} reached the protected work deadline")


def _anchor_result(incumbent, *, iterations: int, stopped_reason: str = "max_iterations"):
    metrics = alns.ALNSMetrics(iterations=iterations)
    return alns.AnytimeRunResult(
        solution=incumbent.solution,
        metrics=metrics,
        incumbent_trace=(),
        stopped_reason=stopped_reason,
        epoch_count=1,
        first_epoch_trace=((0, "d1", "r3", 1.0, 1.0, "current_equal", False, False),),
        prefix_consistent=True,
        operator_metrics=(),
        epoch_solutions=(incumbent.solution,),
    )


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


class EntryAnytimeFillTests(unittest.TestCase):
    @staticmethod
    def _prob_info():
        return instance(
            [block(due=0, processing=1)],
            weights={"w1": 1.0, "w2": 0.0, "w3": 0.0},
        )

    def _feature_off_snapshot(self, explicit):
        prob_info = self._prob_info()
        clock = _FakeClock()
        parent = _FakeParentBudget(300.0, clock)
        anchor_calls = []
        telemetry = {}

        def fake_anchor(_state, incumbent, _rng, **kwargs):
            anchor_calls.append(
                {
                    "timelimit_seconds": kwargs["timelimit_seconds"],
                    "epoch_seconds": kwargs["epoch_seconds"],
                    "iterations_per_epoch": kwargs["iterations_per_epoch"],
                    "continuation_iterations_per_epoch": kwargs[
                        "continuation_iterations_per_epoch"
                    ],
                    "acceptor_name": kwargs["acceptor_name"],
                    "adaptive": kwargs["adaptive"],
                }
            )
            allowance = kwargs["timelimit_seconds"]
            legacy_iterations = 300 + max(0, math.ceil(allowance / 60.0) - 1)
            clock.now += 1.0
            return _anchor_result(incumbent, iterations=legacy_iterations)

        solve_kwargs = {
            "_constructor": False,
            "_retime": False,
            "_telemetry": telemetry,
        }
        if explicit is not None:
            solve_kwargs["_s3_anytime_fill"] = explicit
        with (
            patch("solver.entry.Budget", return_value=parent),
            patch("solver.entry.time.monotonic", side_effect=clock),
            patch("solver.entry.run_anytime_epochs", side_effect=fake_anchor),
            patch("solver.entry.run_s3_extension", create=True) as extension,
        ):
            solution = solve(prob_info, 300.0, **solve_kwargs)
        self.assertFalse(extension.called)
        payload = {
            "anchor_calls": anchor_calls,
            "solution": solution,
            "telemetry": telemetry,
        }
        digest = hashlib.sha256(
            json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
        return solution, telemetry, anchor_calls, digest

    def _run_fill_with_fake_clock(self, timelimit):
        prob_info = self._prob_info()
        clock = _FakeClock()
        parent = _FakeParentBudget(timelimit, clock)
        anchor_calls = []
        extension_calls = []
        extension_results = []
        telemetry = {}

        def fake_anchor(_state, incumbent, _rng, **kwargs):
            anchor_calls.append(dict(kwargs))
            clock.now += min(1.0, kwargs["timelimit_seconds"])
            return _anchor_result(incumbent, iterations=300)

        def fake_batch(_state, active_incumbent, _rng, **kwargs):
            objective = active_incumbent.checker_result.objective
            self.assertIsNotNone(objective)
            for iteration in range(kwargs["max_iterations"]):
                kwargs["on_event"](
                    alns.ALNSIterationEvent(
                        iteration=iteration,
                        destroy_name="d1",
                        repair_name="r3",
                        previous_cur_obj=objective,
                        new_obj=objective,
                        outcome="current_equal",
                        accepted=False,
                        potential_incumbent=False,
                    )
                )
            clock.now += min(2.0, kwargs["budget"].remaining)
            return alns.ALNSRunResult(
                solution=active_incumbent.solution,
                metrics=alns.ALNSMetrics(iterations=kwargs["max_iterations"]),
                incumbent_trace=(),
                stopped_reason="max_iterations",
            )

        def capture_extension(checkpoint, active_budget, *args, **kwargs):
            extension_calls.append(
                {
                    "budget_is_parent": active_budget is parent,
                    "deadline": active_budget.deadline,
                    "anchor_sha256": checkpoint.solution_sha256,
                }
            )
            result = alns.run_s3_extension(
                checkpoint,
                active_budget,
                *args,
                **kwargs,
            )
            extension_results.append(result)
            return result

        with (
            patch("solver.entry.Budget", return_value=parent),
            patch("solver.entry.time.monotonic", side_effect=clock),
            patch("solver.entry.run_anytime_epochs", side_effect=fake_anchor),
            patch("solver.entry.run_s3_extension", side_effect=capture_extension),
            patch("solver.alns.run_alns", side_effect=fake_batch),
            patch("solver.alns.same_bay_restart", return_value=False),
        ):
            solution = solve(
                prob_info,
                timelimit,
                _constructor=False,
                _retime=False,
                _s3_anytime_fill=True,
                _telemetry=telemetry,
            )
        return {
            "anchor_calls": anchor_calls,
            "extension_calls": extension_calls,
            "extension_result": extension_results[0],
            "parent": parent,
            "solution": solution,
            "telemetry": telemetry,
        }

    def _run_with_stubbed_extension(self, effect):
        prob_info = self._prob_info()
        clock = _FakeClock()
        parent = _FakeParentBudget(60.0, clock)
        anchors = []
        telemetry = {}

        def fake_anchor(_state, incumbent, _rng, **kwargs):
            clock.now += 1.0
            anchors.append(incumbent.export_checkpoint())
            return _anchor_result(incumbent, iterations=300)

        def fake_extension(checkpoint, active_budget, *args, **kwargs):
            self.assertIs(active_budget, parent)
            return effect(checkpoint)

        with (
            patch("solver.entry.Budget", return_value=parent),
            patch("solver.entry.time.monotonic", side_effect=clock),
            patch("solver.entry.run_anytime_epochs", side_effect=fake_anchor),
            patch("solver.entry.run_s3_extension", side_effect=fake_extension),
        ):
            solution = solve(
                prob_info,
                60.0,
                _constructor=False,
                _retime=False,
                _s3_anytime_fill=True,
                _telemetry=telemetry,
            )
        return prob_info, anchors[0], solution, telemetry

    def test_s3_anytime_fill_defaults_false(self):
        self.assertIs(False, DEFAULT_CONFIG.s3_anytime_fill)

    def test_feature_off_result_event_and_telemetry_sha_is_unchanged(self):
        default = self._feature_off_snapshot(None)
        explicit_false = self._feature_off_snapshot(False)

        self.assertEqual(default, explicit_false)
        self.assertEqual(EXPECTED_FEATURE_OFF_SHA256, default[3])
        self.assertEqual(304, default[1]["alns_metrics"]["iterations"])
        self.assertEqual(1, default[2][0]["continuation_iterations_per_epoch"])

    def test_feature_on_uses_one_qualified_anchor_and_same_parent_deadline(self):
        observed = self._run_fill_with_fake_clock(300.0)

        self.assertEqual(1, len(observed["anchor_calls"]))
        anchor_call = observed["anchor_calls"][0]
        self.assertEqual(300, anchor_call["iterations_per_epoch"])
        self.assertEqual(1, anchor_call["continuation_iterations_per_epoch"])
        self.assertLessEqual(anchor_call["timelimit_seconds"], 60.0)
        self.assertEqual(
            [{
                "budget_is_parent": True,
                "deadline": observed["parent"].deadline,
                "anchor_sha256": observed["extension_calls"][0]["anchor_sha256"],
            }],
            observed["extension_calls"],
        )
        self.assertGreater(observed["extension_result"].metrics.batches_started, 1)
        self.assertEqual(
            observed["extension_result"].checkpoint.solution_copy(),
            observed["solution"],
        )

    def test_fake_clock_300_scales_beyond_60_with_logical_prefix(self):
        short = self._run_fill_with_fake_clock(60.0)
        long = self._run_fill_with_fake_clock(300.0)
        short_result = short["extension_result"]
        long_result = long["extension_result"]

        self.assertGreater(
            long_result.metrics.segments_started,
            short_result.metrics.segments_started,
        )
        self.assertGreater(
            long_result.metrics.batches_started,
            short_result.metrics.batches_started,
        )
        self.assertGreater(long_result.metrics.iterations, short_result.metrics.iterations)
        self.assertEqual(short_result.trace, long_result.trace[: len(short_result.trace)])
        self.assertEqual(300, short["telemetry"]["alns_metrics"]["iterations"])
        self.assertGreater(long_result.metrics.iterations, 304)

    def test_extension_improvement_is_final_with_public_schema_unchanged(self):
        prob_info = instance(
            [
                block(due=100, processing=1),
                block(due=0, processing=1),
            ],
            weights={"w1": 1.0, "w2": 0.0, "w3": 0.0},
        )
        parsed = ProblemInstance.parse(prob_info)
        improved_state = SolutionState(parsed)
        improved_state.place(Placement(1, 0, 0, 0, 0, 0, 1))
        improved_state.place(Placement(0, 0, 0, 0, 0, 1, 2))
        improved = VerifiedIncumbent(parsed)
        improved.register_initial(improved_state)
        improved_checkpoint = improved.export_checkpoint()
        clock = _FakeClock()
        parent = _FakeParentBudget(60.0, clock)
        anchor_checkpoints = []

        def fake_anchor(_state, incumbent, _rng, **kwargs):
            anchor_checkpoints.append(incumbent.export_checkpoint())
            clock.now += 1.0
            return _anchor_result(incumbent, iterations=300)

        def fake_extension(checkpoint, active_budget, *args, **kwargs):
            self.assertIs(active_budget, parent)
            self.assertLess(
                improved_checkpoint.checker_result.objective,
                checkpoint.checker_result.objective,
            )
            return alns.S3ExtensionResult(
                checkpoint=improved_checkpoint,
                metrics=alns.S3ExtensionMetrics(0, 0, 0, 0, 1, 0, 0, 0, 0),
                trace=(),
                stopped_reason="work_deadline",
            )

        with (
            patch("solver.entry.Budget", return_value=parent),
            patch("solver.entry.time.monotonic", side_effect=clock),
            patch("solver.entry.run_anytime_epochs", side_effect=fake_anchor),
            patch("solver.entry.run_s3_extension", side_effect=fake_extension),
        ):
            solution = solve(
                prob_info,
                60.0,
                _constructor=False,
                _retime=False,
                _s3_anytime_fill=True,
            )

        self.assertEqual(improved_checkpoint.solution_copy(), solution)
        self.assertEqual(set(anchor_checkpoints[0].solution_copy()), set(solution))
        checked = official_check(prob_info, solution)
        self.assertTrue(checked.feasible, checked.violations)
        self.assertEqual(5, checked.stage)

    def test_no_gain_deadline_and_entry_fault_return_verified_anchor(self):
        zero_metrics = alns.S3ExtensionMetrics(0, 0, 0, 0, 0, 0, 0, 0, 0)
        for stopped_reason in ("work_deadline", "deadline_fault", "fault:RuntimeError:x"):
            with self.subTest(stopped_reason=stopped_reason):
                prob_info, anchor, solution, _telemetry = self._run_with_stubbed_extension(
                    lambda checkpoint: alns.S3ExtensionResult(
                        checkpoint=checkpoint,
                        metrics=zero_metrics,
                        trace=(),
                        stopped_reason=stopped_reason,
                    )
                )
                self.assertEqual(anchor.solution_copy(), solution)
                checked = official_check(prob_info, solution)
                self.assertTrue(checked.feasible, checked.violations)
                self.assertEqual(5, checked.stage)

        def fail_entry(_checkpoint):
            raise RuntimeError("entry extension fault")

        prob_info, anchor, solution, telemetry = self._run_with_stubbed_extension(
            fail_entry
        )
        self.assertEqual(anchor.solution_copy(), solution)
        self.assertIn("RuntimeError: entry extension fault", telemetry["fallback_reason"])
        checked = official_check(prob_info, solution)
        self.assertTrue(checked.feasible, checked.violations)
        self.assertEqual(5, checked.stage)


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
