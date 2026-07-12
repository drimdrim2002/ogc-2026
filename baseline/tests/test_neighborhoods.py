from __future__ import annotations

import random
import unittest
from unittest.mock import patch

from solver.budget import Budget
from solver.geometry import GeometryKernel
from solver.instance import parse_instance
from solver.neighborhoods import (
    CongestedWindowDestroy,
    NeighborhoodContext,
    PreferenceAlternativeDestroy,
    RandomKDestroy,
    ShawDestroy,
    TardyChainDestroy,
    Z2ContributorDestroy,
    destroy_snapshot,
    heuristic_repair,
    locally_feasible,
)
from solver.state import Placement, SolutionSnapshot, compute_objective
from tests.helpers import block, instance


def neighborhood_fixture():
    raw = instance(
        [
            block(due=10, workload=5, preferences=(10, 10)),
            block(due=2, workload=1, preferences=(10, 10)),
            block(due=10, workload=1, preferences=(1, 10)),
            block(due=10, workload=1, preferences=(10, 9)),
            block(due=10, workload=1, preferences=(10, 10)),
            block(due=10, workload=1, preferences=(10, 10)),
        ],
        bays=((12, 12), (12, 12)),
    )
    parsed = parse_instance(raw)
    kernel = GeometryKernel.from_instance(parsed)
    snapshot = SolutionSnapshot(
        (
            Placement(0, 0, 0, 0, 0, 0, 4),
            Placement(1, 0, 0, 0, 0, 4, 8),
            Placement(2, 0, 0, 4, 0, 0, 4),
            Placement(3, 0, 0, 8, 0, 0, 4),
            Placement(4, 1, 0, 0, 0, 0, 4),
            Placement(5, 1, 0, 4, 0, 0, 4),
        )
    )
    snapshot = snapshot.with_objective(compute_objective(parsed, snapshot))
    return raw, parsed, kernel, snapshot, NeighborhoodContext(parsed, kernel)


class NeighborhoodTests(unittest.TestCase):
    def test_tardy_chain_selects_root_and_active_relation_chain(self):
        _, _, _, snapshot, context = neighborhood_fixture()
        selected = TardyChainDestroy().select(snapshot, 2, random.Random(1), context)
        self.assertEqual((1, 0), selected)

    def test_congested_window_prefers_highest_union_occupancy(self):
        _, _, _, snapshot, context = neighborhood_fixture()
        selected = CongestedWindowDestroy().select(
            snapshot, 2, random.Random(1), context
        )
        self.assertEqual((0, 2), selected)

    def test_shaw_selection_is_seed_replayable_and_related(self):
        _, _, _, snapshot, context = neighborhood_fixture()
        first = ShawDestroy().select(snapshot, 4, random.Random(77), context)
        second = ShawDestroy().select(snapshot, 4, random.Random(77), context)
        self.assertEqual(first, second)
        self.assertEqual(4, len(set(first)))

    def test_z2_contributor_prefers_range_reducing_heavy_block(self):
        _, _, _, snapshot, context = neighborhood_fixture()
        selected = Z2ContributorDestroy().select(
            snapshot, 1, random.Random(1), context
        )
        self.assertEqual((0,), selected)

    def test_preference_operator_prefers_high_loss_with_fit_alternative(self):
        _, _, _, snapshot, context = neighborhood_fixture()
        selected = PreferenceAlternativeDestroy().select(
            snapshot, 1, random.Random(1), context
        )
        self.assertEqual((2,), selected)

    def test_random_k_is_unique_exact_and_seed_replayable(self):
        _, _, _, snapshot, context = neighborhood_fixture()
        first = RandomKDestroy().select(snapshot, 4, random.Random(2026), context)
        second = RandomKDestroy().select(snapshot, 4, random.Random(2026), context)
        self.assertEqual(first, second)
        self.assertEqual(4, len(first))
        self.assertEqual(4, len(set(first)))

    def test_destroy_draft_preserves_original_and_records_boundary(self):
        _, _, _, snapshot, _ = neighborhood_fixture()
        draft = destroy_snapshot(snapshot, (0, 1))
        self.assertIs(snapshot, draft.original)
        self.assertEqual((0, 1), tuple(item.block_id for item in draft.destroyed))
        self.assertEqual(frozenset({2, 3}), draft.boundary_ids)
        self.assertEqual(snapshot.placements, draft.original.placements)

    def test_heuristic_repair_is_transactional_and_locally_exact(self):
        _, _, _, snapshot, context = neighborhood_fixture()
        before = snapshot.placements
        result = heuristic_repair(snapshot, (0, 1, 2), context, Budget.start(5))
        self.assertTrue(result.feasible, result)
        self.assertTrue(locally_feasible(result.snapshot, context))
        self.assertEqual(before, snapshot.placements)
        self.assertGreater(result.candidates_generated, 0)
        self.assertAlmostEqual(
            result.objective_delta,
            result.snapshot.objective.total - snapshot.objective.total,
        )

    def test_repair_no_candidate_returns_input_identity_without_index_mutation(self):
        _, _, _, snapshot, context = neighborhood_fixture()
        before = snapshot.placements
        with patch(
            "solver.neighborhoods.generate_insertion_candidates", return_value=()
        ):
            result = heuristic_repair(snapshot, (5,), context, Budget.start(5))
        self.assertEqual("NO_CANDIDATE", result.status)
        self.assertIs(snapshot, result.snapshot)
        self.assertEqual(before, snapshot.placements)
        self.assertEqual(frozenset(), result.changed_ids)


if __name__ == "__main__":
    unittest.main()
