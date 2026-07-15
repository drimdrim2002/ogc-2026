from __future__ import annotations

import math
import random
import unittest
from dataclasses import replace

from solver.alns import (
    AlnsConfig,
    AlnsContext,
    accept_candidate,
    budget_aware_destroy_size,
    destroy_size_targets,
    grow_destroy_size,
    initial_destroy_size,
    portfolio_destroy_size,
    run_lns,
    update_weights,
)
from solver.budget import Budget
from solver.geometry import GeometryKernel
from solver.instance import parse_instance
from solver.neighborhoods import RepairResult
from solver.serialize import serialize
from solver.state import IncumbentStore, Placement, SolutionSnapshot, compute_objective
from tests.helpers import block, checker, instance


class FixedDestroy:
    name = "fixed"

    def __init__(self, ids=(0,)):
        self.ids = tuple(ids)

    def select(self, current, k, rng, context):
        del current, k, rng, context
        return self.ids


class FakeRandom:
    def __init__(self, value):
        self.value = value

    def random(self):
        return self.value


class FakeClock:
    def __init__(self) -> None:
        self.value = 0.0

    def __call__(self) -> float:
        return self.value


def one_block_fixture():
    raw = instance([block(release=0, due=0, processing=1)], weights=(1, 0, 0))
    parsed = parse_instance(raw)
    kernel = GeometryKernel.from_instance(parsed)
    initial = SolutionSnapshot((Placement(0, 0, 0, 0, 0, 5, 6),))
    initial = initial.with_objective(compute_objective(parsed, initial))
    store = IncumbentStore(parsed)
    operations = serialize(initial, kernel)
    checked = checker(raw, operations)
    assert store.install_if_valid(initial, operations, checked)
    return raw, parsed, kernel, initial, store


class AlnsUnitTests(unittest.TestCase):
    def test_deadline_led_search_uses_sixty_second_window_past_sixteen_iterations(self):
        raw, parsed, kernel, initial, store = one_block_fixture()
        clock = FakeClock()

        def engine(current, destroyed, context, budget):
            del context, budget
            clock.value += 1.0
            return RepairResult(
                current,
                "FEASIBLE",
                destroyed,
                frozenset(),
                1,
                0.0,
            )

        result = run_lns(
            initial,
            store,
            AlnsContext(
                parsed,
                kernel,
                raw,
                checker,
                destroy_operators=(FixedDestroy(),),
                repair_engines=(engine,),
                clock=clock,
            ),
            Budget.start(60.0, clock=clock),
            AlnsConfig(),
        )

        self.assertGreater(result.metrics.iterations, 16)
        self.assertEqual("DEADLINE", result.metrics.exit_reason)
        self.assertAlmostEqual(3.0, result.metrics.remaining_seconds, places=6)

    def test_removing_iteration_cap_preserves_legacy_sixteen_iteration_prefix(self):
        def run(max_iterations):
            raw = instance(
                [block(release=0, due=0, processing=1)],
                weights=(1, 0, 0),
            )
            parsed = parse_instance(raw)
            kernel = GeometryKernel.from_instance(parsed)
            initial = SolutionSnapshot((Placement(0, 0, 0, 0, 0, 30, 31),))
            initial = initial.with_objective(compute_objective(parsed, initial))
            store = IncumbentStore(parsed)
            operations = serialize(initial, kernel)
            self.assertTrue(
                store.install_if_valid(initial, operations, checker(raw, operations))
            )
            clock = FakeClock()
            selections = []

            class RecordingDestroy(FixedDestroy):
                def __init__(self, name):
                    super().__init__()
                    self.name = name

                def select(self, current, k, rng, context):
                    selections.append(self.name)
                    return super().select(current, k, rng, context)

            def engine(current, destroyed, context, budget):
                del context, budget
                clock.value += 1.0
                placement = current.placements[0]
                candidate = SolutionSnapshot(
                    (replace(placement, entry=placement.entry - 1, exit=placement.exit - 1),)
                )
                candidate = candidate.with_objective(
                    compute_objective(parsed, candidate)
                )
                return RepairResult(
                    candidate,
                    "FEASIBLE",
                    destroyed,
                    frozenset({0}),
                    1,
                    candidate.objective.total - current.objective.total,
                )

            result = run_lns(
                initial,
                store,
                AlnsContext(
                    parsed,
                    kernel,
                    raw,
                    checker,
                    destroy_operators=(
                        RecordingDestroy("left"),
                        RecordingDestroy("right"),
                    ),
                    repair_engines=(engine,),
                    clock=clock,
                ),
                Budget.start(20.0, clock=clock),
                AlnsConfig(seed=20260710, max_iterations=max_iterations),
            )
            return selections, result

        capped_selections, capped = run(16)
        continued_selections, continued = run(None)

        self.assertEqual("ITERATION_LIMIT", capped.metrics.exit_reason)
        self.assertEqual(16, capped.metrics.iterations)
        self.assertEqual("DEADLINE", continued.metrics.exit_reason)
        self.assertGreater(continued.metrics.iterations, 16)
        self.assertEqual(capped_selections, continued_selections[:16])
        self.assertEqual(
            capped.metrics.accepted_trace,
            continued.metrics.accepted_trace[: len(capped.metrics.accepted_trace)],
        )
        self.assertEqual(
            capped.metrics.best_trace,
            continued.metrics.best_trace[: len(capped.metrics.best_trace)],
        )
        self.assertLessEqual(
            continued.snapshot.objective.total,
            capped.snapshot.objective.total,
        )

    def test_adaptation_activity_is_reported_when_thresholds_are_reached(self):
        raw, parsed, kernel, initial, store = one_block_fixture()

        def engine(current, destroyed, context, budget):
            del context, budget
            return RepairResult(
                current,
                "FEASIBLE",
                destroyed,
                frozenset(),
                1,
                0.0,
            )

        result = run_lns(
            initial,
            store,
            AlnsContext(
                parsed,
                kernel,
                raw,
                checker,
                destroy_operators=(FixedDestroy(),),
                repair_engines=(engine,),
            ),
            Budget.start(5.0),
            AlnsConfig(
                warmup_iterations=2,
                segment=2,
                stall_iterations=2,
                stall_time_fraction=0.0,
                max_iterations=4,
            ),
        )

        self.assertTrue(result.metrics.warmup_completed)
        self.assertEqual(2, result.metrics.weight_updates)
        self.assertEqual(2, result.metrics.stall_events)

    def test_split_search_reuses_rng_adaptation_and_current_state(self):
        def run(split: bool):
            raw, parsed, kernel, initial, store = one_block_fixture()
            entries = iter((4, 3, 4, 2, 3, 1))

            def engine(current, destroyed, context, budget):
                del context, budget
                entry = next(entries)
                candidate = SolutionSnapshot(
                    (Placement(0, 0, 0, 0, 0, entry, entry + 1),)
                )
                candidate = candidate.with_objective(
                    compute_objective(parsed, candidate)
                )
                return RepairResult(
                    candidate,
                    "FEASIBLE",
                    destroyed,
                    frozenset({0}),
                    1,
                    candidate.objective.total - current.objective.total,
                )

            context = AlnsContext(
                parsed,
                kernel,
                raw,
                checker,
                destroy_operators=(FixedDestroy(),),
                repair_engines=(engine,),
            )
            if not split:
                return run_lns(
                    initial,
                    store,
                    context,
                    Budget.start(5.0),
                    AlnsConfig(
                        seed=77,
                        warmup_iterations=2,
                        segment=2,
                        stall_iterations=3,
                        stall_time_fraction=0.0,
                        max_iterations=6,
                    ),
                )
            first = run_lns(
                initial,
                store,
                context,
                Budget.start(5.0),
                AlnsConfig(
                    seed=77,
                    warmup_iterations=2,
                    segment=2,
                    stall_iterations=3,
                    stall_time_fraction=0.0,
                    max_iterations=3,
                ),
            )
            return run_lns(
                first.snapshot,
                store,
                context,
                Budget.start(5.0),
                AlnsConfig(
                    seed=77,
                    warmup_iterations=2,
                    segment=2,
                    stall_iterations=3,
                    stall_time_fraction=0.0,
                    max_iterations=3,
                ),
                state=first.state,
            )

        continuous = run(False)
        split = run(True)

        self.assertEqual(continuous.current, split.current)
        self.assertEqual(continuous.state, split.state)
        self.assertEqual(6, split.state.total_iterations)
        self.assertIsNotNone(split.state.temperature)

    def test_acceptance_uses_weighted_delta_and_exact_probability_boundary(self):
        self.assertTrue(accept_candidate(-1.0, None, FakeRandom(1.0)))
        self.assertTrue(accept_candidate(0.0, None, FakeRandom(1.0)))
        temperature = 2.0
        boundary = math.exp(-1.0 / temperature)
        self.assertTrue(accept_candidate(1.0, temperature, FakeRandom(boundary - 1e-9)))
        self.assertFalse(accept_candidate(1.0, temperature, FakeRandom(boundary)))
        self.assertFalse(
            accept_candidate(1.0, temperature, FakeRandom(0.0), allow_worse=False)
        )

    def test_segment_weight_formula_and_floor(self):
        updated = update_weights(
            (1.0, 0.2), (2, 1), (10.0, 0.0), reaction=0.2, floor=0.1
        )
        self.assertAlmostEqual(1.8, updated[0])
        self.assertAlmostEqual(0.16, updated[1])
        floored = update_weights((0.1,), (1,), (0.0,), 1.0, 0.1)
        self.assertEqual((0.1,), floored)

    def test_destroy_size_initial_growth_and_cap(self):
        self.assertEqual(4, initial_destroy_size(100))
        self.assertEqual(6, grow_destroy_size(4, 100))
        self.assertEqual(15, grow_destroy_size(14, 100))
        self.assertEqual(3, initial_destroy_size(3))
        self.assertEqual(4, grow_destroy_size(4, 10))

    def test_portfolio_schedules_medium_and_large_deterministically(self):
        self.assertEqual((4, 6, 10), destroy_size_targets(100))
        self.assertEqual(("small", 4), portfolio_destroy_size(0, 100))
        self.assertEqual(("medium", 6), portfolio_destroy_size(4, 100))
        self.assertEqual(("large", 10), portfolio_destroy_size(10, 100))
        self.assertEqual(("medium", 6), portfolio_destroy_size(16, 100))

    def test_budget_aware_cap_uses_observed_repair_throughput(self):
        self.assertEqual(
            7,
            budget_aware_destroy_size(
                10,
                4,
                remaining_seconds=22.0,
                checker_reserve=2.0,
                repair_seconds_per_block=(1.0, 1.0, 1.0),
            ),
        )
        self.assertEqual(
            4,
            budget_aware_destroy_size(
                10,
                4,
                remaining_seconds=3.0,
                checker_reserve=2.0,
                repair_seconds_per_block=(1.0,),
            ),
        )
        self.assertEqual(
            10,
            budget_aware_destroy_size(
                10,
                4,
                remaining_seconds=3.0,
                checker_reserve=2.0,
                repair_seconds_per_block=(),
            ),
        )

    def test_destroy_size_telemetry_is_reported_without_changing_result(self):
        raw, parsed, kernel, initial, store = one_block_fixture()

        def engine(current, destroyed, context, budget):
            del context, budget
            return RepairResult(
                current,
                "FEASIBLE",
                destroyed,
                frozenset(),
                1,
                0.0,
            )

        result = run_lns(
            initial,
            store,
            AlnsContext(
                parsed,
                kernel,
                raw,
                checker,
                destroy_operators=(FixedDestroy(),),
                repair_engines=(engine,),
            ),
            Budget.start(5.0),
            AlnsConfig(max_iterations=2, neighborhood_policy="portfolio"),
        )

        self.assertEqual(initial.placements, result.snapshot.placements)
        self.assertEqual(1, len(result.metrics.per_destroy_size))
        size = result.metrics.per_destroy_size[0]
        self.assertEqual(("small", 1, 2, 2, 2, 0), (
            size.level,
            size.size,
            size.attempts,
            size.feasible,
            size.accepted,
            size.new_best,
        ))

    def test_worse_current_acceptance_never_leaks_from_validated_best(self):
        raw, parsed, kernel, initial, store = one_block_fixture()
        candidates = [
            SolutionSnapshot((Placement(0, 0, 0, 0, 0, 4, 5),)),
            SolutionSnapshot((Placement(0, 0, 0, 0, 0, 5, 6),)),
        ]
        calls = 0

        def engine(current, destroyed, context, budget):
            nonlocal calls
            del context, budget
            candidate = candidates[calls]
            calls += 1
            candidate = candidate.with_objective(compute_objective(parsed, candidate))
            return RepairResult(
                candidate,
                "FEASIBLE",
                destroyed,
                frozenset({0}),
                1,
                candidate.objective.total - current.objective.total,
            )

        context = AlnsContext(
            parsed,
            kernel,
            raw,
            checker,
            destroy_operators=(FixedDestroy(),),
            repair_engines=(engine,),
        )
        result = run_lns(
            initial,
            store,
            context,
            Budget.start(5),
            AlnsConfig(seed=2, warmup_iterations=1, max_iterations=2),
        )

        self.assertEqual(5, result.snapshot.placements[0].exit)
        self.assertEqual(6, result.current.placements[0].exit)
        self.assertEqual((6.0, 5.0), result.metrics.best_trace)
        self.assertEqual("ITERATION_LIMIT", result.metrics.exit_reason)

    def test_best_trace_installs_strict_improvement_only(self):
        raw, parsed, kernel, initial, store = one_block_fixture()
        improved = SolutionSnapshot((Placement(0, 0, 0, 0, 0, 4, 5),))
        improved = improved.with_objective(compute_objective(parsed, improved))

        def engine(current, destroyed, context, budget):
            del context, budget
            return RepairResult(
                improved,
                "FEASIBLE",
                destroyed,
                frozenset({0}),
                1,
                improved.objective.total - current.objective.total,
            )

        context = AlnsContext(
            parsed,
            kernel,
            raw,
            checker,
            destroy_operators=(FixedDestroy(),),
            repair_engines=(engine,),
        )
        result = run_lns(
            initial,
            store,
            context,
            Budget.start(5),
            AlnsConfig(warmup_iterations=0, max_iterations=3),
        )
        self.assertEqual((6.0, 5.0), result.metrics.best_trace)
        self.assertTrue(
            all(
                right < left
                for left, right in zip(
                    result.metrics.best_trace, result.metrics.best_trace[1:]
                )
            )
        )

    def test_repeated_objective_delta_invariant_error_stops_search(self):
        raw, parsed, kernel, initial, store = one_block_fixture()

        def invalid_engine(current, destroyed, context, budget):
            del context, budget
            candidate = SolutionSnapshot(tuple(current.placements)).with_objective(
                current.objective
            )
            return RepairResult(
                candidate,
                "FEASIBLE",
                destroyed,
                frozenset(),
                1,
                99.0,
            )

        context = AlnsContext(
            parsed,
            kernel,
            raw,
            checker,
            destroy_operators=(FixedDestroy(),),
            repair_engines=(invalid_engine,),
        )
        result = run_lns(
            initial,
            store,
            context,
            Budget.start(5),
            AlnsConfig(
                warmup_iterations=0,
                max_iterations=10,
                max_invariant_errors=3,
            ),
        )
        self.assertEqual("INVARIANT_ERROR", result.metrics.exit_reason)
        self.assertEqual(3, result.metrics.iterations)
        self.assertEqual(3, result.metrics.per_operator[0][1].exceptions)
        self.assertEqual(initial.placements, result.snapshot.placements)


if __name__ == "__main__":
    unittest.main()
