from __future__ import annotations

import itertools
import unittest

from solver.budget import Budget
from solver.geometry import GeometryKernel, PairRelation
from solver.instance import parse_instance
from solver.neighborhoods import (
    CandidateCostParts,
    CompleteCandidate,
    NeighborhoodContext,
    RepairResult,
    export_complete_candidates,
)
from solver.repair_mip import (
    ConflictIndex,
    MipBackendResult,
    MipRepairConfig,
    canonicalize_candidates,
    inspect_selection,
    repair_with_mip,
)
from solver.state import Placement, SolutionSnapshot, compute_objective
from tests.helpers import block, instance


class EnumeratingBackend:
    def __init__(self, *, status="OPTIMAL"):
        self.status = status
        self.requests = []

    @staticmethod
    def objective(request, refs):
        rows = dict(request.rows)
        loads = list(request.unchanged_loads)
        known = 0.0
        for block_id, index in refs:
            candidate = rows[block_id][index]
            loads[candidate.bay_id] += request.workloads[block_id]
            known += (
                request.w1 * candidate.cost_parts.tardiness
                + request.w3 * candidate.cost_parts.preference
            )
        normalized = [
            request.load_factors[bay_id] * value
            for bay_id, value in enumerate(loads)
        ]
        z2 = max(normalized) - min(normalized) if len(normalized) > 1 else 0.0
        return known + request.w2 * z2

    def solve(self, request):
        self.requests.append(request)
        choices = []
        for block_id, row in request.rows:
            available = [
                (block_id, index)
                for index in range(len(row))
                if (block_id, index) not in request.excluded
            ]
            choices.append(available)
        feasible = []
        for refs in itertools.product(*choices):
            selected = frozenset(refs)
            if any(frozenset(cut) <= selected for cut in request.pair_cuts):
                continue
            if any(frozenset(cut) <= selected for cut in request.no_good_cuts):
                continue
            feasible.append((self.objective(request, refs), tuple(refs)))
        if not feasible:
            return MipBackendResult(self.status)
        objective, refs = min(feasible, key=lambda item: (item[0], item[1]))
        return MipBackendResult(
            self.status,
            refs,
            objective=objective,
            bound=objective,
            gap=0.0,
            variables=sum(len(row) for _, row in request.rows) + 2,
            constraints=len(request.rows)
            + len(request.pair_cuts)
            + len(request.no_good_cuts),
            diagnostics=(f"selected={refs}",),
        )


def fixture(count=3, *, bays=((20, 10),), weights=(3, 5, 7)):
    raw = instance(
        [
            block(
                due=2 + index,
                workload=index + 1,
                preferences=tuple(10 - bay for bay in range(len(bays))),
            )
            for index in range(count)
        ],
        bays=bays,
        weights=weights,
    )
    parsed = parse_instance(raw)
    kernel = GeometryKernel.from_instance(parsed)
    placements = tuple(
        Placement(index, 0, 0, 0, 0, index * 2, index * 2 + 2)
        for index in range(count)
    )
    snapshot = SolutionSnapshot(placements)
    snapshot = snapshot.with_objective(compute_objective(parsed, snapshot))
    return raw, parsed, kernel, snapshot, NeighborhoodContext(parsed, kernel)


def candidate(placement, context, *, incumbent=False):
    return CompleteCandidate.from_placement(
        placement, context, is_incumbent=incumbent
    )


class CandidatePreparationTests(unittest.TestCase):
    def test_config_cannot_raise_documented_hard_caps(self):
        for kwargs in (
            {"max_blocks": 17, "max_per_block": 1},
            {"max_blocks": 1, "max_per_block": 33},
            {"max_blocks": 1, "max_per_block": 1, "max_product": 513},
        ):
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                MipRepairConfig(**kwargs)

    def test_cap_blocks_dispatches_without_backend(self):
        _, _, _, current, context = fixture(17, bays=((20, 10),))
        backend_calls = 0
        fallback_calls = 0

        def backend_factory():
            nonlocal backend_calls
            backend_calls += 1
            return EnumeratingBackend()

        def fallback(current, destroyed, context, budget):
            nonlocal fallback_calls
            del context, budget
            fallback_calls += 1
            return RepairResult(
                current, "FEASIBLE", destroyed, frozenset(), 0, 0.0
            )

        result = repair_with_mip(
            current,
            tuple(range(17)),
            (),
            context,
            Budget.start(10),
            backend_factory,
            fallback_engine=fallback,
        )
        self.assertEqual(0, backend_calls)
        self.assertEqual(1, fallback_calls)
        self.assertEqual("mip_fallback", result.engine)
        self.assertIs(current, result.snapshot)

    def test_product_trims_sixteen_by_thirty_three_to_hard_cap(self):
        _, _, _, current, context = fixture(16, bays=((100, 10),))
        rows = []
        for block_id in range(16):
            placements = tuple(
                candidate(
                    Placement(block_id, 0, 0, x, 0, block_id * 2, block_id * 2 + 2),
                    context,
                )
                for x in range(33)
            )
            rows.append((block_id, placements))
        table = canonicalize_candidates(
            current, tuple(range(16)), tuple(rows), context
        )
        self.assertEqual(512, table.product)
        self.assertEqual(512, table.after_count)
        self.assertTrue(
            all(ref[1] < 32 for ref in table.incumbent_choice),
            table.incumbent_choice,
        )

    def test_incumbent_is_added_duplicate_collapsed_and_cost_recomputed(self):
        _, _, _, current, context = fixture(1)
        placement = current.placements[0]
        wrong = CompleteCandidate(
            placement.block_id,
            placement.bay_id,
            placement.orient_idx,
            placement.x,
            placement.y,
            placement.entry,
            placement.exit,
            CandidateCostParts(999.0, 999.0),
            False,
        )
        table = canonicalize_candidates(
            current, (0,), ((0, (wrong, wrong)),), context
        )
        self.assertEqual(1, table.after_count)
        normalized = table.rows[0][1][0]
        self.assertTrue(normalized.is_incumbent)
        self.assertEqual(0.0, normalized.cost_parts.tardiness)
        self.assertEqual(((0, 0),), table.incumbent_choice)

    def test_export_is_bounded_and_preserves_current_column(self):
        _, _, _, current, context = fixture(2, bays=((40, 10),))
        rows = export_complete_candidates(
            current, (0, 1), context, Budget.start(10), max_per_block=4
        )
        current_by_id = {item.block_id: item for item in current.placements}
        self.assertEqual((0, 1), tuple(block_id for block_id, _ in rows))
        for block_id, row in rows:
            self.assertLessEqual(len(row), 4)
            self.assertTrue(any(item.is_incumbent for item in row))
            self.assertIn(current_by_id[block_id], tuple(item.placement for item in row))


class ConflictAndObjectiveTests(unittest.TestCase):
    def test_unchanged_overlap_is_prefiltered(self):
        _, _, kernel, current, context = fixture(2)
        bad = candidate(Placement(0, 0, 0, 0, 0, 2, 4), context)
        table = canonicalize_candidates(
            current, (0,), ((0, (bad,)),), context
        )
        unchanged = SolutionSnapshot((current.placements[1],))
        index = ConflictIndex.build(table, unchanged, kernel)
        bad_index = next(
            index
            for index, item in enumerate(table.rows[0][1])
            if item.placement == bad.placement
        )
        self.assertIn((0, bad_index), index.excluded)
        self.assertNotIn(table.incumbent_choice[0], index.excluded)

    def test_selected_pair_conflict_adds_cut_and_resolves(self):
        _, _, _, current, context = fixture(2)
        cheap_overlap = candidate(Placement(1, 0, 0, 0, 0, 0, 2), context)
        rows = (
            (0, (candidate(current.placements[0], context, incumbent=True),)),
            (
                1,
                (
                    cheap_overlap,
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
        self.assertTrue(result.feasible, result)
        self.assertGreaterEqual(len(backend.requests), 2)
        self.assertGreaterEqual(len(backend.requests[-1].pair_cuts), 1)
        self.assertTrue(any(item == "conflict_cuts=1" for item in result.diagnostics))

    def test_aabb_skip_avoids_exact_relation(self):
        _, parsed, kernel, _, context = fixture(2)

        class CountingKernel:
            def __init__(self, delegate):
                self.delegate = delegate
                self.calls = 0

            def shape(self, *args):
                return self.delegate.shape(*args)

            def relation(self, *args):
                self.calls += 1
                return self.delegate.relation(*args)

        counting = CountingKernel(kernel)
        left = candidate(Placement(0, 0, 0, 0, 0, 0, 2), context)
        right = candidate(Placement(1, 0, 0, 10, 0, 0, 2), context)
        conflicts, cycles = inspect_selection((left, right), counting)
        self.assertEqual((), conflicts)
        self.assertEqual((), cycles)
        self.assertEqual(0, counting.calls)

    def test_backend_objective_matches_exact_z2_enumeration(self):
        _, parsed, _, current, context = fixture(
            3, bays=((10, 10), (20, 10)), weights=(1, 4, 2)
        )
        rows = []
        for block_id, old in enumerate(current.placements):
            rows.append(
                (
                    block_id,
                    tuple(
                        candidate(
                            Placement(block_id, bay_id, 0, block_id * 3, 0, 0, 2),
                            context,
                            incumbent=bay_id == old.bay_id,
                        )
                        for bay_id in range(2)
                    ),
                )
            )
        backend = EnumeratingBackend()
        result = repair_with_mip(
            current,
            (0, 1, 2),
            tuple(rows),
            context,
            Budget.start(10),
            lambda: backend,
        )
        self.assertTrue(result.feasible, result)
        exact = min(
            compute_objective(
                parsed,
                SolutionSnapshot(
                    tuple(
                        rows[block_id][1][bay_id].placement
                        for block_id, bay_id in enumerate(choice)
                    )
                ),
            ).total
            for choice in itertools.product(range(2), repeat=3)
        )
        self.assertAlmostEqual(exact, result.snapshot.objective.total)

    def test_exit_cycle_is_reported_for_no_good_cut(self):
        _, _, real_kernel, _, context = fixture(3)

        class Shape:
            full_aabb = (0.0, 0.0, 2.0, 2.0)

        class Relation:
            def __init__(self, g_i_k, g_k_i):
                self.g_i_k = g_i_k
                self.g_k_i = g_k_i
                self.state = None

            def allows(self, left, right):
                del left, right
                return True

        class CycleKernel:
            def shape(self, *args):
                return Shape()

            def relation(self, left, right):
                pair = (left.block_id, right.block_id)
                bits = {(0, 1): (True, False), (1, 2): (True, False), (0, 2): (False, True)}
                return Relation(*bits[pair])

        selected = tuple(
            candidate(Placement(block_id, 0, 0, 0, 0, block_id, 5), context)
            for block_id in range(3)
        )
        _, cycles = inspect_selection(selected, CycleKernel())
        self.assertEqual(((0, 1, 2),), cycles)

    def test_exit_cycle_selection_adds_no_good_before_extraction(self):
        raw = instance(
            [block(due=5, processing=2) for _ in range(3)],
            bays=((20, 10),),
            weights=(1, 0, 0),
        )
        parsed = parse_instance(raw)
        delegate = GeometryKernel.from_instance(parsed)

        class CycleRelation:
            def __init__(self, g_i_k, g_k_i):
                self.g_i_k = g_i_k
                self.g_k_i = g_k_i
                self.state = PairRelation.from_bits(g_i_k, g_k_i).state

            def allows(self, left, right):
                del left, right
                return True

        class CycleKernel:
            def fits(self, placement):
                return delegate.fits(placement)

            def shape(self, *args):
                return delegate.shape(*args)

            def cache_info(self):
                return delegate.cache_info()

            def relation(self, left, right):
                if left.exit == right.exit == 5 and left.x == right.x == 0:
                    bits = {
                        (0, 1): (True, False),
                        (1, 2): (True, False),
                        (0, 2): (False, True),
                    }
                    return CycleRelation(*bits[(left.block_id, right.block_id)])
                return delegate.relation(left, right)

        kernel = CycleKernel()
        context = NeighborhoodContext(parsed, kernel)
        current = SolutionSnapshot(
            (
                Placement(0, 0, 0, 0, 0, 5, 7),
                Placement(1, 0, 0, 0, 0, 7, 9),
                Placement(2, 0, 0, 0, 0, 9, 11),
            )
        ).with_objective(
            compute_objective(
                parsed,
                SolutionSnapshot(
                    (
                        Placement(0, 0, 0, 0, 0, 5, 7),
                        Placement(1, 0, 0, 0, 0, 7, 9),
                        Placement(2, 0, 0, 0, 0, 9, 11),
                    )
                ),
            )
        )
        rows = tuple(
            (
                block_id,
                (
                    candidate(
                        Placement(block_id, 0, 0, 0, 0, block_id, 5), context
                    ),
                    candidate(current.placements[block_id], context, incumbent=True),
                ),
            )
            for block_id in range(3)
        )
        backend = EnumeratingBackend()
        result = repair_with_mip(
            current,
            (0, 1, 2),
            rows,
            context,
            Budget.start(10),
            lambda: backend,
        )
        self.assertTrue(result.feasible, result)
        self.assertGreaterEqual(len(backend.requests), 2)
        self.assertGreaterEqual(len(backend.requests[-1].no_good_cuts), 1)

    def test_incumbent_start_is_feasible_in_first_request(self):
        _, _, _, current, context = fixture(2)
        rows = tuple(
            (
                item.block_id,
                (candidate(item, context, incumbent=True),),
            )
            for item in current.placements
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
        request = backend.requests[0]
        self.assertEqual(((0, 0), (1, 0)), request.incumbent_choice)
        self.assertTrue(all(ref not in request.excluded for ref in request.incumbent_choice))
        self.assertTrue(result.feasible)


if __name__ == "__main__":
    unittest.main()
