from __future__ import annotations

import copy
import itertools
import unittest

from solver.alns import AlnsConfig, AlnsContext, run_lns
from solver.budget import Budget
from solver.geometry import GeometryKernel
from solver.instance import parse_instance
from solver.neighborhoods import NeighborhoodContext, RepairResult
from solver.repair_mip import MipBackendResult, repair_with_mip
from solver.serialize import serialize
from solver.state import IncumbentStore, Placement, SolutionSnapshot, compute_objective
from tests.helpers import block, checker, instance
from tests.test_repair_mip import EnumeratingBackend, candidate, fixture


class FixedDestroy:
    name = "fixed"

    def __init__(self, ids):
        self.ids = tuple(ids)

    def select(self, current, k, rng, context):
        del current, k, rng, context
        return self.ids


def installed(raw, parsed, kernel, snapshot):
    snapshot = snapshot.with_objective(compute_objective(parsed, snapshot))
    operations = serialize(snapshot, kernel)
    store = IncumbentStore(parsed)
    assert store.install_if_valid(snapshot, operations, checker(raw, operations))
    return snapshot, store


class MipRepairIntegrationTests(unittest.TestCase):
    def test_mip_matches_candidate_enumeration(self):
        _, parsed, kernel, _, context = fixture(
            3, bays=((12, 10), (18, 10)), weights=(2, 5, 3)
        )
        current = SolutionSnapshot(
            tuple(
                Placement(block_id, 0, 0, block_id * 3, 0, block_id, block_id + 2)
                for block_id in range(3)
            )
        ).with_objective(
            compute_objective(
                parsed,
                SolutionSnapshot(
                    tuple(
                        Placement(
                            block_id, 0, 0, block_id * 3, 0, block_id, block_id + 2
                        )
                        for block_id in range(3)
                    )
                ),
            )
        )
        rows = []
        for block_id, old in enumerate(current.placements):
            choices = []
            for choice in range(3):
                bay_id = choice % 2
                choices.append(
                    candidate(
                        Placement(
                            block_id,
                            bay_id,
                            0,
                            block_id * 3,
                            0,
                            choice,
                            choice + 2,
                        ),
                        context,
                        incumbent=(
                            bay_id == old.bay_id
                            and choice == old.entry
                            and choice + 2 == old.exit
                        ),
                    )
                )
            rows.append((block_id, tuple(choices)))
        result = repair_with_mip(
            current,
            (0, 1, 2),
            tuple(rows),
            context,
            Budget.start(10),
        )
        self.assertTrue(result.feasible, result)
        exact = min(
            compute_objective(
                parsed,
                SolutionSnapshot(
                    tuple(
                        rows[block_id][1][choice[block_id]].placement
                        for block_id in range(3)
                    )
                ),
            ).total
            for choice in itertools.product(range(3), repeat=3)
        )
        self.assertAlmostEqual(exact, result.snapshot.objective.total)
        self.assertTrue(any("status=OPTIMAL" in item for item in result.diagnostics))

    def test_late_conflict_cut_converges_and_checker_stage_five(self):
        raw, _, _, current, context = fixture(2)
        overlap = candidate(Placement(1, 0, 0, 0, 0, 0, 2), context)
        rows = (
            (0, (candidate(current.placements[0], context, incumbent=True),)),
            (
                1,
                (
                    overlap,
                    candidate(current.placements[1], context, incumbent=True),
                ),
            ),
        )
        backend = EnumeratingBackend()
        result = repair_with_mip(
            current,
            (0, 1),
            rows,
            context,
            Budget.start(10),
            lambda: backend,
        )
        checked = checker(raw, serialize(result.snapshot, context.kernel))
        self.assertTrue(result.feasible)
        self.assertGreaterEqual(len(backend.requests), 2)
        self.assertTrue(checked["feasible"])
        self.assertEqual(5, checked["stage"])
        self.assertAlmostEqual(result.snapshot.objective.total, checked["objective"])

    def test_timeout_uses_feasible_selection_or_one_heuristic_fallback(self):
        raw, _, _, current, context = fixture(1)
        rows = ((0, (candidate(current.placements[0], context, incumbent=True),)),)
        feasible_backend = EnumeratingBackend(status="TIME_LIMIT")
        feasible = repair_with_mip(
            current,
            (0,),
            rows,
            context,
            Budget.start(10),
            lambda: feasible_backend,
        )
        self.assertEqual("mip", feasible.engine)
        self.assertEqual(5, checker(raw, serialize(feasible.snapshot, context.kernel))["stage"])

        fallback_calls = 0

        class EmptyBackend:
            def solve(self, request):
                del request
                return MipBackendResult("TIME_LIMIT")

        def fallback(current, destroyed, context, budget):
            nonlocal fallback_calls
            del context, budget
            fallback_calls += 1
            return RepairResult(current, "FEASIBLE", destroyed, frozenset(), 0, 0.0)

        empty = repair_with_mip(
            current,
            (0,),
            rows,
            context,
            Budget.start(10),
            lambda: EmptyBackend(),
            fallback_engine=fallback,
        )
        self.assertEqual(1, fallback_calls)
        self.assertEqual("mip_fallback", empty.engine)
        self.assertEqual(5, checker(raw, serialize(empty.snapshot, context.kernel))["stage"])

    def test_import_license_and_model_failure_each_fallback_once(self):
        raw, _, _, current, context = fixture(1)
        rows = ((0, (candidate(current.placements[0], context, incumbent=True),)),)

        class CrashBackend:
            def solve(self, request):
                del request
                raise RuntimeError("model failure")

        factories = (
            lambda: (_ for _ in ()).throw(ImportError("gurobi import")),
            lambda: (_ for _ in ()).throw(RuntimeError("license failure")),
            lambda: CrashBackend(),
        )
        for factory in factories:
            with self.subTest(factory=factory):
                fallback_calls = 0

                def fallback(current, destroyed, context, budget):
                    nonlocal fallback_calls
                    del context, budget
                    fallback_calls += 1
                    return RepairResult(
                        current, "FEASIBLE", destroyed, frozenset(), 0, 0.0
                    )

                result = repair_with_mip(
                    current,
                    (0,),
                    rows,
                    context,
                    Budget.start(10),
                    factory,
                    fallback_engine=fallback,
                )
                self.assertEqual(1, fallback_calls)
                self.assertEqual("mip_fallback", result.engine)
                checked = checker(raw, serialize(result.snapshot, context.kernel))
                self.assertTrue(checked["feasible"])
                self.assertEqual(5, checked["stage"])


class AlnsMipTransactionTests(unittest.TestCase):
    def one_block(self):
        raw = instance([block(due=1, processing=1)], weights=(1, 0, 0))
        parsed = parse_instance(raw)
        kernel = GeometryKernel.from_instance(parsed)
        initial = SolutionSnapshot((Placement(0, 0, 0, 0, 0, 5, 6),))
        initial, store = installed(raw, parsed, kernel, initial)
        better = SolutionSnapshot((Placement(0, 0, 0, 0, 0, 0, 1),))
        better = better.with_objective(compute_objective(parsed, better))
        return raw, parsed, kernel, initial, store, better

    def mip_engine(self, better):
        def engine(current, destroyed, context, budget):
            del context, budget
            return RepairResult(
                better,
                "FEASIBLE",
                destroyed,
                frozenset({0}),
                1,
                better.objective.total - current.objective.total,
                engine="mip",
            )

        engine.__name__ = "mip_repair"
        return engine

    def test_mip_result_retime_error_keeps_current_and_best_immutable(self):
        raw, parsed, kernel, initial, store, better = self.one_block()

        class ErrorResult:
            status = "ERROR"
            snapshot = better

        def failed_retime(*args, **kwargs):
            del args, kwargs
            return ErrorResult()

        result = run_lns(
            initial,
            store,
            AlnsContext(
                parsed,
                kernel,
                raw,
                checker,
                destroy_operators=(FixedDestroy((0,)),),
                repair_engines=(self.mip_engine(better),),
            ),
            Budget.start(5),
            AlnsConfig(warmup_iterations=0, max_iterations=1),
            retime_hook=failed_retime,
        )
        self.assertEqual(initial.placements, result.current.placements)
        self.assertEqual(initial.placements, result.snapshot.placements)
        self.assertEqual(5, checker(raw, result.serialized_operations)["stage"])

    def test_mip_checker_reject_keeps_current_and_best_immutable(self):
        raw, parsed, kernel, initial, store, better = self.one_block()

        def rejecting_checker(prob_info, operations):
            checked = checker(prob_info, operations)
            checked["feasible"] = False
            checked["stage"] = 4
            return checked

        result = run_lns(
            initial,
            store,
            AlnsContext(
                parsed,
                kernel,
                raw,
                rejecting_checker,
                destroy_operators=(FixedDestroy((0,)),),
                repair_engines=(self.mip_engine(better),),
            ),
            Budget.start(5),
            AlnsConfig(warmup_iterations=0, max_iterations=1),
        )
        self.assertEqual(initial.placements, result.current.placements)
        self.assertEqual(initial.placements, result.snapshot.placements)

    def test_mip_candidate_full_check_installs_strict_improvement(self):
        raw, parsed, kernel, initial, store, better = self.one_block()
        result = run_lns(
            initial,
            store,
            AlnsContext(
                parsed,
                kernel,
                raw,
                checker,
                destroy_operators=(FixedDestroy((0,)),),
                repair_engines=(self.mip_engine(better),),
            ),
            Budget.start(5),
            AlnsConfig(warmup_iterations=0, max_iterations=1),
        )
        checked = checker(copy.deepcopy(raw), copy.deepcopy(result.serialized_operations))
        self.assertEqual(better.placements, result.snapshot.placements)
        self.assertTrue(checked["feasible"])
        self.assertEqual(5, checked["stage"])
        self.assertAlmostEqual(result.snapshot.objective.total, checked["objective"])

    def test_periodic_and_stall_portfolio_selects_mip_engine(self):
        raw, parsed, kernel, initial, store, _ = self.one_block()
        calls = []

        def heuristic(current, destroyed, context, budget):
            del context, budget
            calls.append("heuristic")
            return RepairResult(current, "FEASIBLE", destroyed, frozenset(), 0, 0.0)

        def mip(current, destroyed, context, budget):
            del context, budget
            calls.append("mip")
            return RepairResult(
                current,
                "FEASIBLE",
                destroyed,
                frozenset(),
                0,
                0.0,
                engine="mip",
            )

        heuristic.__name__ = "heuristic"
        mip.__name__ = "mip"
        result = run_lns(
            initial,
            store,
            AlnsContext(
                parsed,
                kernel,
                raw,
                checker,
                destroy_operators=(FixedDestroy((0,)),),
                repair_engines=(heuristic, mip),
            ),
            Budget.start(5),
            AlnsConfig(
                warmup_iterations=0,
                max_iterations=3,
                mip_period=2,
                stall_iterations=1,
                stall_time_fraction=0.0,
            ),
        )
        self.assertEqual(("heuristic", "mip", "mip"), tuple(calls))
        metrics = {name: values for name, *values in result.metrics.repair_engines}
        self.assertEqual(1, metrics["heuristic"][0])
        self.assertEqual(2, metrics["mip"][0])


if __name__ == "__main__":
    unittest.main()
