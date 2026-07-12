"""Fit-qualified T0 and verified-incumbent contracts for S0-04."""

from __future__ import annotations

import json
from pathlib import Path
import unittest

from solver.trivial import build_t0
from solver.checker_adapter import official_check
from solver.incumbent import IncumbentVerificationError, VerifiedIncumbent
from solver.instance import ProblemInstance, UnsolvableInstanceError
from solver.serialize import serialize_non_interlock
from solver.state import Placement, SolutionState
from tests.fixtures import TWO_SQUARE, block, instance


EXAMPLE_PATH = (
    Path(__file__).resolve().parents[2]
    / "alg_tester"
    / "example"
    / "example_B2_b10.json"
)


class TrivialTests(unittest.TestCase):
    def test_example_registers_verified_incumbent(self):
        with EXAMPLE_PATH.open(encoding="utf-8") as handle:
            prob_info = json.load(handle)
        parsed = ProblemInstance.parse(prob_info)

        state = build_t0(parsed)

        self.assertEqual(len(parsed.blocks), len(state.placements))
        for block_spec in parsed.blocks:
            placed = state.placements[block_spec.block_id]
            self.assertGreaterEqual(placed.entry, block_spec.release_time)
            self.assertEqual(block_spec.dwell, placed.exit - placed.entry)
            ranges = block_spec.orientations[placed.orient_idx].integer_position_ranges(
                parsed.bays[placed.bay_id]
            )
            self.assertIsNotNone(ranges)
            assert ranges is not None
            self.assertEqual(ranges[0].start, placed.x)
            self.assertEqual(ranges[1].start, placed.y)

        for bay in parsed.bays:
            windows = sorted(
                (placed.entry, placed.exit)
                for placed in state.placements.values()
                if placed.bay_id == bay.bay_id
            )
            self.assertTrue(
                all(left[1] <= right[0] for left, right in zip(windows, windows[1:]))
            )

        serialized = serialize_non_interlock(state.placements.values())
        checked = official_check(prob_info, serialized)
        self.assertTrue(checked.feasible, checked.violations)
        self.assertEqual(5, checked.stage)

        incumbent = VerifiedIncumbent(prob_info)
        registered = incumbent.register_initial(state)
        self.assertEqual(checked.objective, registered.objective)
        self.assertEqual(1, incumbent.verification_count)
        self.assertIs(incumbent.solution, incumbent.solution)
        self.assertEqual(serialized, incumbent.solution)

    def test_prefers_highest_preference_then_lowest_ids_and_sequences_per_bay(self):
        prob_info = instance(
            [
                block(
                    layers=(TWO_SQUARE,),
                    release=5,
                    processing=0,
                    preferences=(10, 50),
                    orientations=(
                        (((0.0, 0.0), (20.0, 0.0), (20.0, 1.0), (0.0, 1.0)),),
                        (TWO_SQUARE,),
                    ),
                ),
                block(release=-2, processing=3, preferences=(7, 7)),
                block(release=1, processing=2, preferences=(7, 7)),
            ],
            bays=((4, 4), (5, 5)),
        )
        parsed = ProblemInstance.parse(prob_info)

        state = build_t0(parsed)

        first = state.placements[0]
        self.assertEqual((1, 1), (first.bay_id, first.orient_idx))
        self.assertEqual((5, 6), (first.entry, first.exit))
        second = state.placements[1]
        third = state.placements[2]
        self.assertEqual(0, second.bay_id)
        self.assertEqual(0, third.bay_id)
        self.assertEqual((-2, 1), (second.entry, second.exit))
        self.assertEqual((1, 3), (third.entry, third.exit))

    def test_no_fit_names_block_and_never_registers_unverified_state(self):
        no_fit = instance([block(layers=(TWO_SQUARE,))], bays=((1, 1),))
        with self.assertRaisesRegex(UnsolvableInstanceError, "block 0"):
            build_t0(ProblemInstance.parse(no_fit))

        valid = instance([block()], bays=((2, 2),))
        parsed = ProblemInstance.parse(valid)
        invalid_state = SolutionState(parsed)
        invalid_state.place(
            Placement(0, bay_id=0, x=99, y=99, orient_idx=0, entry=0, exit=1)
        )
        incumbent = VerifiedIncumbent(valid)
        with self.assertRaises(IncumbentVerificationError):
            incumbent.register_initial(invalid_state)
        self.assertEqual(1, incumbent.verification_count)
        self.assertFalse(incumbent.has_incumbent)

    def test_try_update_requires_checker_feasibility_and_strict_improvement(self):
        prob_info = instance(
            [block(preferences=(10, 0))],
            bays=((2, 2), (2, 2)),
            weights={"w1": 0.0, "w2": 0.0, "w3": 1.0},
        )
        parsed = ProblemInstance.parse(prob_info)
        worse = SolutionState(parsed)
        worse.place(
            Placement(0, bay_id=1, x=0, y=0, orient_idx=0, entry=0, exit=1)
        )
        invalid = SolutionState(parsed)
        invalid.place(
            Placement(0, bay_id=0, x=99, y=99, orient_idx=0, entry=0, exit=1)
        )
        better = SolutionState(parsed)
        better.place(
            Placement(0, bay_id=0, x=0, y=0, orient_idx=0, entry=0, exit=1)
        )
        incumbent = VerifiedIncumbent(prob_info)

        incumbent.register_initial(worse)
        initial_solution = incumbent.solution
        self.assertFalse(incumbent.try_update(invalid))
        self.assertIs(initial_solution, incumbent.solution)
        self.assertTrue(incumbent.try_update(better))
        self.assertEqual(0.0, incumbent.checker_result.objective)
        self.assertEqual(3, incumbent.verification_count)


if __name__ == "__main__":
    unittest.main()
