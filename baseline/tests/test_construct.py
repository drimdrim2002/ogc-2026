"""Behavioral contracts for S1 shared event-time insertion."""

from __future__ import annotations

import json
from pathlib import Path
import random
import unittest

from solver.assign import AssignmentV1
from solver.checker_adapter import official_check
from solver.construct import (
    anchor_candidates,
    escalate_insert,
    insert_block,
    time_candidates,
)
from solver.instance import ProblemInstance
from solver.serialize import serialize_non_interlock
from solver.state import Placement, SolutionState
from solver.validate import validate_insertion
from tests.fixtures import TWO_SQUARE, block, instance


class ConstructorTests(unittest.TestCase):
    def test_anchor_escalation_and_solo_fallback(self):
        negative_reference_square = (
            ((1.0, 1.0), (-1.0, 1.0), (-1.0, -1.0), (1.0, -1.0)),
        )
        parsed = ProblemInstance.parse(
            instance(
                [
                    block(layers=(TWO_SQUARE,), processing=10),
                    block(
                        orientations=(negative_reference_square,),
                        processing=1,
                    ),
                ],
                bays=((6, 6),),
            )
        )
        state = SolutionState(parsed)
        state.place(Placement(0, 0, 0, 0, 0, 0, 10))

        anchors = anchor_candidates(state, 1, 0, 0, cap=None)

        self.assertEqual((2, 2), anchors[0])
        self.assertIn((4, 2), anchors, "right contact must use the negative local AABB")
        self.assertIn((2, 4), anchors, "top contact must use the negative local AABB")
        x_range, y_range = parsed.blocks[1].orientations[0].integer_position_ranges(
            parsed.bays[0]
        ) or (range(0), range(0))
        self.assertTrue(all(x in x_range and y in y_range for x, y in anchors))

        blocks = [
            block(layers=(TWO_SQUARE,), release=index, due=200, processing=1)
            for index in range(17)
        ]
        blocks.append(block(layers=(TWO_SQUARE,), release=0, due=1, processing=1))
        fallback_parsed = ProblemInstance.parse(instance(blocks, bays=((2, 2),)))
        fallback_state = SolutionState(fallback_parsed)
        for block_id in range(16):
            fallback_state.place(
                Placement(block_id, 0, 0, 0, 0, block_id, block_id + 1)
            )
        fallback_state.place(Placement(16, 0, 0, 0, 0, 16, 100))

        escalated = escalate_insert(
            fallback_state,
            17,
            preferred_bay_id=0,
            preferred_orient_idx=0,
        )

        self.assertEqual("solo_window", escalated.fallback_reason)
        self.assertEqual(100, escalated.candidate.placement.entry)
        fallback_state.place(escalated.candidate.placement)
        self.assertEqual(18, len(fallback_state.placements))
        checked = official_check(
            fallback_parsed.raw,
            serialize_non_interlock(fallback_state.placements.values()),
        )
        self.assertTrue(checked.feasible, checked.violations)
        self.assertEqual(5, checked.stage)

    def test_escalation_places_tracked_example_once(self):
        path = (
            Path(__file__).resolve().parents[2]
            / "alg_tester"
            / "example"
            / "example_B2_b10.json"
        )
        prob_info = json.loads(path.read_text(encoding="utf-8"))
        parsed = ProblemInstance.parse(prob_info)
        assignment = AssignmentV1(parsed).assign()
        state = SolutionState(parsed)

        for block_id in assignment.order:
            preferred = assignment.assignments[block_id]
            escalated = escalate_insert(
                state,
                block_id,
                preferred_bay_id=preferred.bay_id,
                preferred_orient_idx=preferred.orient_idx,
            )
            self.assertIsNone(state.place(escalated.candidate.placement))

        self.assertEqual(set(range(len(parsed.blocks))), set(state.placements))
        self.assertEqual(len(parsed.blocks), len(state.placements))
        checked = official_check(
            prob_info,
            serialize_non_interlock(state.placements.values()),
        )
        self.assertTrue(checked.feasible, checked.violations)
        self.assertEqual(5, checked.stage)

    def test_same_day_handoff_candidate(self):
        parsed = ProblemInstance.parse(
            instance(
                [
                    block(layers=(TWO_SQUARE,), processing=3),
                    block(layers=(TWO_SQUARE,), processing=2),
                ]
            )
        )
        state = SolutionState(parsed)
        state.place(Placement(0, 0, 0, 0, 0, 0, 3))

        simultaneous = Placement(1, 0, 0, 0, 0, 0, 2)
        self.assertFalse(validate_insertion(state, simultaneous))

        selected = insert_block(state, 1, 0, 0, x=0, y=0)

        self.assertIsNotNone(selected)
        assert selected is not None
        self.assertEqual(3, selected.placement.entry)
        self.assertEqual(5, selected.placement.exit)
        state.place(selected.placement)
        checked = official_check(
            parsed.raw,
            serialize_non_interlock(state.placements.values()),
        )
        self.assertTrue(checked.feasible, checked.violations)
        self.assertEqual(5, checked.stage)

    def test_event_boundaries_are_integer_filtered_sorted_deduplicated_and_capped(self):
        parsed = ProblemInstance.parse(
            instance(
                [
                    block(release=3, processing=2),
                    block(),
                    block(),
                    block(),
                ]
            )
        )
        state = SolutionState(parsed)
        state.place(Placement(1, 0, 0, 0, 0, 5, 9))
        state.place(Placement(2, 0, 2, 0, 0, 5, 11))
        state.place(Placement(3, 0, 4, 0, 0, 13, 15))

        self.assertEqual((3, 9, 11, 15), time_candidates(state, 0, 0, cap=None))
        self.assertEqual((3, 9), time_candidates(state, 0, 0, cap=2))

    def test_finish_before_entry_event_is_considered(self):
        parsed = ProblemInstance.parse(
            instance(
                [
                    block(release=0, processing=2),
                    block(layers=(TWO_SQUARE,), release=3, processing=2),
                ]
            )
        )
        state = SolutionState(parsed)
        state.place(Placement(0, 0, 0, 0, 0, 5, 9))

        selected = insert_block(state, 1, 0, 0, x=0, y=0)

        self.assertIsNotNone(selected)
        assert selected is not None
        self.assertEqual((3, 9), time_candidates(state, 1, 0))
        self.assertEqual(3, selected.placement.entry)
        self.assertEqual(5, selected.placement.exit)

    def test_seeded_1000_event_candidates_match_checker(self):
        prob_info = instance(
            [
                block(layers=(TWO_SQUARE,), processing=2),
                block(layers=(TWO_SQUARE,), processing=2),
            ],
            bays=((6, 6),),
        )
        parsed = ProblemInstance.parse(prob_info)
        rng = random.Random(20260710)
        mismatches: list[tuple[int, bool, bool]] = []

        for case_index in range(1000):
            state = SolutionState(parsed)
            first_entry = rng.randrange(0, 7)
            first = Placement(
                0,
                0,
                rng.randrange(0, 5),
                rng.randrange(0, 5),
                0,
                first_entry,
                first_entry + rng.randrange(2, 6),
            )
            state.place(first)
            events = time_candidates(state, 1, 0, cap=None)
            entry = events[case_index % len(events)]
            candidate = Placement(
                1,
                0,
                rng.randrange(0, 5),
                rng.randrange(0, 5),
                0,
                entry,
                entry + parsed.blocks[1].dwell,
            )
            targeted = validate_insertion(state, candidate)
            checked = official_check(
                prob_info,
                serialize_non_interlock((first, candidate)),
            )
            full = checked.feasible
            if targeted != full:
                mismatches.append((case_index, targeted, full))

        self.assertEqual([], mismatches[:10], f"{len(mismatches)} parity mismatches")


if __name__ == "__main__":
    unittest.main()
