from __future__ import annotations

import copy
import random
import unittest
from unittest.mock import patch

from solver import entry
from solver.alns import AlnsConfig, AlnsContext, run_lns
from solver.budget import Budget
from solver.construct import ConstructionSeed, ConstructorConfig, construct_complete
from solver.fallback import build_safe_candidate
from solver.geometry import GeometryKernel
from solver.instance import parse_instance
from solver.neighborhoods import (
    DEFAULT_DESTROY_OPERATORS,
    RandomKDestroy,
    RepairResult,
    locally_feasible,
)
from solver.serialize import serialize
from solver.state import IncumbentStore, Placement, SolutionSnapshot, compute_objective
from tests.helpers import block, checker, instance, load_example


class FixedDestroy:
    name = "fixed"

    def __init__(self, ids):
        self.ids = tuple(ids)

    def select(self, current, k, rng, context):
        del current, k, rng, context
        return self.ids


class FakeClock:
    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now

    def advance(self, amount):
        self.now += amount


def installed_store(raw, parsed, kernel, snapshot):
    snapshot = snapshot.with_objective(compute_objective(parsed, snapshot))
    operations = serialize(snapshot, kernel)
    store = IncumbentStore(parsed)
    assert store.install_if_valid(snapshot, operations, checker(raw, operations))
    return snapshot, store


class AlnsIntegrationTests(unittest.TestCase):
    def test_tracked_example_constructor_snapshot_runs_seeded_pure_python_lns(self):
        raw = load_example()
        parsed = parse_instance(raw)
        kernel = GeometryKernel.from_instance(parsed)
        construction = construct_complete(
            parsed,
            kernel,
            ConstructionSeed(None, "slack_due", 20260710),
            Budget.start(5),
            ConstructorConfig(max_profiles=1),
        )
        self.assertTrue(construction.complete)
        initial, store = installed_store(
            raw, parsed, kernel, construction.snapshot
        )
        result = run_lns(
            initial,
            store,
            AlnsContext(parsed, kernel, raw, checker),
            Budget.start(5),
            AlnsConfig(seed=20260710, max_iterations=6),
        )
        checked = checker(raw, result.serialized_operations)
        self.assertEqual(6, result.metrics.iterations)
        self.assertEqual(5, checked["stage"])
        self.assertTrue(checked["feasible"])
        self.assertTrue(
            all(
                right < left
                for left, right in zip(
                    result.metrics.best_trace, result.metrics.best_trace[1:]
                )
            )
        )

    def test_seeded_lns_preserves_feasibility_and_final_checker_stage_five(self):
        raw = instance([block(release=0, due=0, processing=1)], weights=(1, 0, 0))
        parsed = parse_instance(raw)
        kernel = GeometryKernel.from_instance(parsed)
        initial, store = installed_store(
            raw,
            parsed,
            kernel,
            SolutionSnapshot((Placement(0, 0, 0, 0, 0, 5, 6),)),
        )
        better = SolutionSnapshot((Placement(0, 0, 0, 0, 0, 0, 1),))
        better = better.with_objective(compute_objective(parsed, better))

        def engine(current, destroyed, context, budget):
            del context, budget
            return RepairResult(
                better,
                "FEASIBLE",
                destroyed,
                frozenset({0}),
                1,
                better.objective.total - current.objective.total,
            )

        context = AlnsContext(
            parsed,
            kernel,
            raw,
            checker,
            destroy_operators=(FixedDestroy((0,)),),
            repair_engines=(engine,),
        )
        result = run_lns(
            initial,
            store,
            context,
            Budget.start(5),
            AlnsConfig(warmup_iterations=0, max_iterations=2),
        )
        checked = checker(copy.deepcopy(raw), copy.deepcopy(result.serialized_operations))
        self.assertTrue(locally_feasible(result.current, context.neighborhood))
        self.assertTrue(checked["feasible"])
        self.assertEqual(5, checked["stage"])
        self.assertAlmostEqual(result.snapshot.objective.total, checked["objective"])

    def test_retime_failure_is_counted_and_search_returns_best(self):
        raw = instance(
            [block(due=10) for _ in range(4)],
            bays=((20, 10),),
            weights=(1, 0, 0),
        )
        parsed = parse_instance(raw)
        kernel = GeometryKernel.from_instance(parsed)
        placements = tuple(
            Placement(block_id, 0, 0, block_id * 3, 0, 0, 2)
            for block_id in range(4)
        )
        initial, store = installed_store(
            raw, parsed, kernel, SolutionSnapshot(placements)
        )

        def engine(current, destroyed, context, budget):
            del context, budget
            candidate = SolutionSnapshot(tuple(current.placements)).with_objective(
                current.objective
            )
            return RepairResult(
                candidate,
                "FEASIBLE",
                destroyed,
                frozenset(destroyed),
                len(destroyed),
                0.0,
            )

        def crash(*args, **kwargs):
            raise RuntimeError("synthetic retimer failure")

        context = AlnsContext(
            parsed,
            kernel,
            raw,
            checker,
            destroy_operators=(FixedDestroy(range(4)),),
            repair_engines=(engine,),
        )
        result = run_lns(
            initial,
            store,
            context,
            Budget.start(5),
            AlnsConfig(warmup_iterations=0, max_iterations=1),
            retime_hook=crash,
        )
        self.assertEqual(1, result.metrics.retime_triggers)
        self.assertGreaterEqual(result.metrics.per_operator[0][1].exceptions, 1)
        self.assertEqual(initial.placements, result.snapshot.placements)

    def test_stall_forces_affected_retime_below_batch_threshold(self):
        raw = instance([block(due=10)], weights=(1, 0, 0))
        parsed = parse_instance(raw)
        kernel = GeometryKernel.from_instance(parsed)
        initial, store = installed_store(
            raw,
            parsed,
            kernel,
            SolutionSnapshot((Placement(0, 0, 0, 0, 0, 0, 2),)),
        )
        hook_calls = 0

        def engine(current, destroyed, context, budget):
            del context, budget
            candidate = SolutionSnapshot(tuple(current.placements)).with_objective(
                current.objective
            )
            return RepairResult(
                candidate,
                "FEASIBLE",
                destroyed,
                frozenset({0}),
                1,
                0.0,
            )

        class IdentityRetime:
            snapshot = initial
            operations = None
            checker_result = None

        def retime_hook(*args, **kwargs):
            nonlocal hook_calls
            hook_calls += 1
            return IdentityRetime()

        context = AlnsContext(
            parsed,
            kernel,
            raw,
            checker,
            destroy_operators=(FixedDestroy((0,)),),
            repair_engines=(engine,),
        )
        result = run_lns(
            initial,
            store,
            context,
            Budget.start(5),
            AlnsConfig(
                warmup_iterations=0,
                stall_iterations=1,
                stall_time_fraction=0.0,
                max_iterations=1,
            ),
            retime_hook=retime_hook,
        )
        self.assertEqual(1, hook_calls)
        self.assertEqual(1, result.metrics.retime_triggers)

    def test_deadline_after_destroy_starts_no_repair_or_checker(self):
        raw = instance([block()])
        parsed = parse_instance(raw)
        kernel = GeometryKernel.from_instance(parsed)
        initial, store = installed_store(
            raw,
            parsed,
            kernel,
            SolutionSnapshot((Placement(0, 0, 0, 0, 0, 0, 2),)),
        )
        clock = FakeClock()
        repair_calls = 0
        checker_calls = 0

        class ExpiringDestroy:
            name = "expiring"

            def select(self, current, k, rng, context):
                del current, k, rng, context
                clock.advance(2.0)
                return (0,)

        def engine(*args):
            nonlocal repair_calls
            repair_calls += 1
            raise AssertionError("repair started after deadline")

        def recording_checker(*args):
            nonlocal checker_calls
            checker_calls += 1
            return checker(*args)

        context = AlnsContext(
            parsed,
            kernel,
            raw,
            recording_checker,
            destroy_operators=(ExpiringDestroy(),),
            repair_engines=(engine,),
            clock=clock,
        )
        result = run_lns(
            initial,
            store,
            context,
            Budget.start(1, clock=clock),
            AlnsConfig(max_iterations=5),
        )
        self.assertEqual(0, repair_calls)
        self.assertEqual(0, checker_calls)
        self.assertEqual("DEADLINE", result.metrics.exit_reason)
        self.assertEqual(initial.placements, result.snapshot.placements)

    def test_fixed_seed_replays_selection_metrics_and_best(self):
        raw = instance(
            [block(due=10) for _ in range(5)], bays=((20, 10),), weights=(1, 0, 0)
        )
        parsed = parse_instance(raw)
        placements = tuple(
            Placement(block_id, 0, 0, block_id * 3, 0, 0, 2)
            for block_id in range(5)
        )

        def run_once():
            kernel = GeometryKernel.from_instance(parsed)
            initial, store = installed_store(
                raw, parsed, kernel, SolutionSnapshot(placements)
            )

            def engine(current, destroyed, context, budget):
                del context, budget
                candidate = SolutionSnapshot(tuple(current.placements)).with_objective(
                    current.objective
                )
                return RepairResult(
                    candidate,
                    "FEASIBLE",
                    destroyed,
                    frozenset(),
                    len(destroyed),
                    0.0,
                )

            fixed_clock = FakeClock()
            context = AlnsContext(
                parsed,
                kernel,
                raw,
                checker,
                destroy_operators=(RandomKDestroy(),),
                repair_engines=(engine,),
                clock=fixed_clock,
            )
            return run_lns(
                initial,
                store,
                context,
                Budget.start(5),
                AlnsConfig(seed=77, warmup_iterations=0, max_iterations=8),
            )

        first = run_once()
        second = run_once()
        self.assertEqual(first.snapshot.placements, second.snapshot.placements)
        self.assertEqual(first.serialized_operations, second.serialized_operations)
        self.assertEqual(first.metrics, second.metrics)

    def test_each_operator_has_attempt_feasible_and_accepted_activity(self):
        raw = instance(
            [block(due=10, preferences=(10, 9)) for _ in range(6)],
            bays=((24, 10), (24, 10)),
            weights=(1, 1, 1),
        )
        parsed = parse_instance(raw)
        placements = tuple(
            Placement(block_id, block_id % 2, 0, (block_id // 2) * 3, 0, 0, 2)
            for block_id in range(6)
        )
        for operator in DEFAULT_DESTROY_OPERATORS:
            with self.subTest(operator=operator.name):
                kernel = GeometryKernel.from_instance(parsed)
                initial, store = installed_store(
                    raw, parsed, kernel, SolutionSnapshot(placements)
                )

                def engine(current, destroyed, context, budget):
                    del context, budget
                    candidate = SolutionSnapshot(tuple(current.placements)).with_objective(
                        current.objective
                    )
                    return RepairResult(
                        candidate,
                        "FEASIBLE",
                        destroyed,
                        frozenset(),
                        len(destroyed),
                        0.0,
                    )

                context = AlnsContext(
                    parsed,
                    kernel,
                    raw,
                    checker,
                    destroy_operators=(operator,),
                    repair_engines=(engine,),
                )
                result = run_lns(
                    initial,
                    store,
                    context,
                    Budget.start(5),
                    AlnsConfig(warmup_iterations=0, max_iterations=1),
                )
                metric = result.metrics.per_operator[0][1]
                self.assertEqual(1, metric.attempts)
                self.assertEqual(1, metric.feasible)
                self.assertEqual(1, metric.accepted)

    def test_submission_path_enables_heuristic_lns_after_feature_gate(self):
        raw = instance([block()])
        parsed = parse_instance(raw)
        kernel = GeometryKernel.from_instance(parsed)
        snapshot = build_safe_candidate(parsed, Budget.start(1))
        with (
            patch("solver.entry.LNS_ENABLED", True),
            patch("solver.assignment.try_assignment_portfolio", return_value=None),
            patch("solver.construct.construct_portfolio", return_value=()),
            patch("solver.geometry.GeometryKernel.from_instance", return_value=kernel),
        ):
            phase = entry.load_optional_phase()
            result = phase(parsed, snapshot, Budget.start(12))
        self.assertTrue(entry.LNS_ENABLED)
        self.assertIsNotNone(result.lns_runner)


if __name__ == "__main__":
    unittest.main()
