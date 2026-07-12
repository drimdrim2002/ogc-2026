"""Incremental state and checker-objective parity contracts for S0-03."""

from __future__ import annotations

import unittest
import random

from solver.checker_adapter import official_check
from solver.instance import ProblemInstance
from solver.state import Placement, SolutionState
from tests.fixtures import block, instance, placement, solution


def as_fixture(item: Placement) -> dict[str, int]:
    return placement(
        item.block_id,
        entry=item.entry,
        exit=item.exit,
        bay=item.bay_id,
        x=item.x,
        y=item.y,
        orient=item.orient_idx,
    )


class StateParityTests(unittest.TestCase):
    def test_incremental_objective_matches_recompute_and_checker(self):
        prob_info = instance(
            [
                block(due=2, processing=2, workload=3, preferences=(10, 7)),
                block(due=5, processing=2, workload=5, preferences=(2, 9)),
                block(due=7, processing=1, workload=2, preferences=(6, 4)),
            ],
            bays=((4, 4), (8, 4)),
            weights={"w1": 1.25, "w2": 0.75, "w3": 2.5},
        )
        parsed = ProblemInstance.parse(prob_info)
        state = SolutionState(parsed)
        values = [
            Placement(0, bay_id=0, x=0, y=0, orient_idx=0, entry=0, exit=3),
            Placement(1, bay_id=1, x=0, y=0, orient_idx=0, entry=3, exit=5),
            Placement(2, bay_id=1, x=2, y=0, orient_idx=0, entry=5, exit=8),
        ]
        for item in values:
            self.assertIsNone(state.place(item))

        recomputed = state.recompute_objective()
        self.assertEqual(recomputed, state.objective_diagnostics)
        checked = official_check(prob_info, solution(as_fixture(p) for p in values))
        self.assertTrue(checked.feasible, checked.violations)
        self.assertAlmostEqual(checked.obj1, state.z1)
        self.assertAlmostEqual(checked.obj2, state.z2)
        self.assertAlmostEqual(checked.obj3, state.z3)
        self.assertAlmostEqual(checked.objective, state.objective)

        removed = state.remove(1)
        self.assertEqual(values[1], removed)
        self.assertEqual(state.recompute_objective(), state.objective_diagnostics)
        self.assertIsNone(state.place(removed))
        self.assertEqual(recomputed, state.objective_diagnostics)
        state.assert_invariants()

    def test_seeded_100_objective_cases_match_checker(self):
        prob_info = instance(
            [
                block(due=2 + index, processing=index % 3, workload=index + 0.25, preferences=(index + 1, 7 - index, 2 * index + 0.5))
                for index in range(5)
            ],
            bays=((4, 4), (8, 4), (5, 10)),
            weights={"w1": 1.1, "w2": 2.3, "w3": 0.7},
        )
        parsed = ProblemInstance.parse(prob_info)
        rng = random.Random(20260710)

        for case_index in range(100):
            state = SolutionState(parsed)
            placements: list[Placement] = []
            for block_spec in parsed.blocks:
                bay_id = rng.randrange(len(parsed.bays))
                orient_idx = parsed.fitting_orientations(block_spec.block_id, bay_id)[0]
                ranges = block_spec.orientations[orient_idx].integer_position_ranges(parsed.bays[bay_id])
                assert ranges is not None
                entry = block_spec.block_id * 8
                item = Placement(
                    block_id=block_spec.block_id,
                    bay_id=bay_id,
                    x=rng.choice(tuple(ranges[0])),
                    y=rng.choice(tuple(ranges[1])),
                    orient_idx=orient_idx,
                    entry=entry,
                    exit=entry + block_spec.dwell + rng.randrange(4),
                )
                placements.append(item)
                state.place(item)

            checked = official_check(
                prob_info, solution(as_fixture(item) for item in placements)
            )
            self.assertTrue(checked.feasible, (case_index, checked.violations))
            self.assertAlmostEqual(checked.obj1, state.z1, places=9)
            self.assertAlmostEqual(checked.obj2, state.z2, places=9)
            self.assertAlmostEqual(checked.obj3, state.z3, places=9)
            self.assertAlmostEqual(checked.objective, state.objective, places=9)
            state.assert_invariants()

    def test_place_returns_previous_for_undo_neutral_replacement(self):
        parsed = ProblemInstance.parse(instance([block()], bays=((3, 3),)))
        state = SolutionState(parsed)
        initial = Placement(0, bay_id=0, x=0, y=0, orient_idx=0, entry=0, exit=1)
        changed = Placement(0, bay_id=0, x=1, y=1, orient_idx=0, entry=2, exit=4)

        self.assertIsNone(state.place(initial))
        self.assertEqual(initial, state.place(changed))
        self.assertEqual(changed, state.remove(0))
        self.assertIsNone(state.place(initial))
        state.assert_invariants()


if __name__ == "__main__":
    unittest.main()
