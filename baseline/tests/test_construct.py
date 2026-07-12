from __future__ import annotations

import dataclasses
import math
import unittest

from solver.budget import Budget
from solver.construct import (
    CandidateScore,
    ConstructionSeed,
    ConstructorConfig,
    construct_complete,
    evaluate_insert,
    generate_position_candidates,
    generate_time_candidates,
    select_regret,
)
from solver.geometry import GeometryKernel
from solver.instance import parse_instance
from solver.state import IndexedSolutionState, Placement, compute_objective
from tests.helpers import block, instance, rectangle


class ConstructorUnitTests(unittest.TestCase):
    def test_event_boundaries_are_half_open_deduplicated_and_cost_sorted(self):
        raw = instance(
            [
                block(release=0, processing=4),
                block(release=0, processing=2),
                block(release=3, due=20, processing=2),
            ]
        )
        parsed = parse_instance(raw)
        state = IndexedSolutionState(
            parsed,
            (
                Placement(0, 0, 0, 0, 0, 1, 5),
                Placement(1, 0, 0, 3, 0, 8, 10),
            ),
            time_cap=12,
        )

        candidates = generate_time_candidates(parsed.block(2), state, None)

        self.assertEqual(len(candidates), len(set(candidates)))
        self.assertTrue({3, 5, 6, 10}.issubset(candidates))

    def test_position_anchors_ignore_non_copresent_blocks(self):
        raw = instance([block(), block(), block()], bays=((14, 10),))
        parsed = parse_instance(raw)
        kernel = GeometryKernel.from_instance(parsed)
        active = Placement(0, 0, 0, 4, 4, 4, 8)
        past = Placement(1, 0, 0, 10, 0, 0, 2)
        active_only = IndexedSolutionState(parsed, (active,), anchor_cap=48)
        active_and_past = IndexedSolutionState(parsed, (active, past), anchor_cap=48)
        active_only.kernel = kernel
        active_and_past.kernel = kernel
        block_info = parsed.block(2)
        orient = block_info.orientations[0]

        expected = generate_position_candidates(
            block_info, parsed.bay(0), orient, (4, 6), active_only
        )
        actual = generate_position_candidates(
            block_info, parsed.bay(0), orient, (4, 6), active_and_past
        )

        self.assertEqual(expected, actual)

    def test_four_aabb_contacts_are_generated(self):
        raw = instance([block(), block()], bays=((12, 12),))
        parsed = parse_instance(raw)
        kernel = GeometryKernel.from_instance(parsed)
        state = IndexedSolutionState(
            parsed, (Placement(0, 0, 0, 4, 4, 0, 5),), anchor_cap=48
        )
        state.kernel = kernel
        candidate = parsed.block(1)

        anchors = generate_position_candidates(
            candidate, parsed.bay(0), candidate.orientations[0], (1, 3), state
        )

        self.assertTrue(any(x == 2 for x, _ in anchors), anchors)  # candidate left
        self.assertTrue(any(x == 6 for x, _ in anchors), anchors)  # candidate right
        self.assertTrue(any(y == 2 for _, y in anchors), anchors)  # candidate below
        self.assertTrue(any(y == 6 for _, y in anchors), anchors)  # candidate above

    def test_negative_local_bbox_anchors_stay_in_integer_fit_range(self):
        layers = [[[0, 0], [-3.49, 0], [-3.49, 2], [7.62, 2], [7.62, 0]]]
        raw = instance([block(), block(orientations=[layers])], bays=((12, 5),))
        parsed = parse_instance(raw)
        kernel = GeometryKernel.from_instance(parsed)
        state = IndexedSolutionState(parsed, anchor_cap=48)
        state.kernel = kernel
        candidate = parsed.block(1)
        orient = candidate.orientations[0]
        reference_range = orient.integer_range(parsed.bay(0))
        self.assertIsNotNone(reference_range)

        anchors = generate_position_candidates(
            candidate, parsed.bay(0), orient, (0, 2), state
        )

        self.assertTrue(anchors)
        for x, y in anchors:
            self.assertGreaterEqual(x, reference_range.x.lower)
            self.assertLessEqual(x, reference_range.x.upper)
            self.assertGreaterEqual(y, reference_range.y.lower)
            self.assertLessEqual(y, reference_range.y.upper)

    def test_symmetric_exact_validation_rejects_future_operation_obstruction(self):
        raw = instance([block(), block()], bays=((10, 10),))
        parsed = parse_instance(raw)
        kernel = GeometryKernel.from_instance(parsed)
        state = IndexedSolutionState(
            parsed, (Placement(0, 0, 0, 0, 0, 2, 4),)
        )
        proposed = Placement(1, 0, 0, 0, 0, 0, 5)

        self.assertIsNone(evaluate_insert(state, proposed, kernel))

    def test_exact_assignment_delta_telescopes_to_full_recompute(self):
        raw = instance(
            [
                block(workload=1.25, preferences=(10, 2)),
                block(workload=2.5, preferences=(4, 9)),
                block(workload=3.75, preferences=(7, 7)),
            ],
            bays=((10, 10), (20, 10)),
            weights=(3, 5, 7),
        )
        parsed = parse_instance(raw)
        placements = (
            Placement(0, 0, 0, 0, 0, 0, 2),
            Placement(1, 1, 0, 0, 0, 0, 2),
            Placement(2, 1, 0, 0, 0, 2, 4),
        )
        state = IndexedSolutionState(parsed)
        z2 = z3 = weighted = 0.0
        for placement in placements:
            delta = state.assignment_delta(placement.block_id, placement.bay_id)
            z2 += delta.z2
            z3 += delta.z3
            weighted += delta.weighted
            self.assertTrue(state.transactional_insert(placement))
        objective = compute_objective(parsed, state.freeze())

        self.assertTrue(math.isclose(z2, objective.z2, rel_tol=1e-6, abs_tol=1e-9))
        self.assertTrue(math.isclose(z3, objective.z3, rel_tol=1e-6, abs_tol=1e-9))
        expected = parsed.weights.w2 * objective.z2 + parsed.weights.w3 * objective.z3
        self.assertTrue(math.isclose(weighted, expected, rel_tol=1e-6, abs_tol=1e-9))

    def test_regret_two_selects_wider_cost_gap(self):
        def score(block_id, value):
            placement = Placement(block_id, 0, 0, block_id * 3, 0, 0, 2)
            return CandidateScore(
                placement, value, 0.0, value, 0, (value, block_id), 0
            )

        selected = select_regret(
            {0: (score(0, 1), score(0, 10)), 1: (score(1, 2), score(1, 3))}
        )

        self.assertEqual(0, selected)

    def test_earliest_empty_window_handles_unsorted_input(self):
        raw = instance([block(), block(), block()])
        parsed = parse_instance(raw)
        state = IndexedSolutionState(
            parsed,
            (
                Placement(0, 0, 0, 0, 0, 8, 10),
                Placement(1, 0, 0, 0, 0, 1, 5),
            ),
        )

        self.assertEqual(5, state.earliest_empty_window(0, 3, 2))
        self.assertEqual(10, state.earliest_empty_window(0, 7, 2))

    def test_seeded_mixture_is_reproducible(self):
        raw = instance(
            [
                block(
                    release=i % 2,
                    due=6 + i,
                    workload=1 + i,
                    preferences=(10, 8),
                )
                for i in range(5)
            ],
            bays=((8, 8), (10, 8)),
        )
        parsed = parse_instance(raw)
        kernel = GeometryKernel.from_instance(parsed)
        seed = ConstructionSeed(None, "seeded_mixture", 77)
        config = ConstructorConfig(seed=77, time_cap=12, anchor_cap=48)

        first = construct_complete(parsed, kernel, seed, Budget.start(5), config)
        second = construct_complete(parsed, kernel, seed, Budget.start(5), config)

        self.assertEqual(first.snapshot.placements, second.snapshot.placements)
        first_metrics = dataclasses.asdict(first.metrics)
        second_metrics = dataclasses.asdict(second.metrics)
        first_metrics.pop("construction_time")
        second_metrics.pop("construction_time")
        self.assertEqual(first_metrics, second_metrics)

    def test_transaction_rolls_back_indexes_load_and_version_on_exception(self):
        raw = instance(
            [block(preferences=(10, 8)), block(preferences=(10, 8))],
            bays=((10, 10), (12, 10)),
        )
        parsed = parse_instance(raw)
        initial = Placement(0, 0, 0, 0, 0, 0, 2)
        state = IndexedSolutionState(parsed, (initial,))
        before = (
            state.placements,
            state.bay_intervals(0),
            state.raw_bay_loads,
            state.version,
        )

        def explode(*_):
            raise RuntimeError("exact geometry failed")

        with self.assertRaisesRegex(RuntimeError, "exact geometry failed"):
            state.transactional_insert(
                Placement(1, 1, 0, 0, 0, 0, 2), explode
            )

        after = (
            state.placements,
            state.bay_intervals(0),
            state.raw_bay_loads,
            state.version,
        )
        self.assertEqual(before, after)


if __name__ == "__main__":
    unittest.main()
