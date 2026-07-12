"""Targeted revalidation parity contracts for S0-03."""

from __future__ import annotations

import unittest

from solver.checker_adapter import official_check
from solver.instance import ProblemInstance
from solver.state import Placement, SolutionState
from solver.validate import ValidationTelemetry, validate_changed, validate_insertion
from tests.fixtures import (
    UNIT_SQUARE,
    block,
    instance,
    placement,
    seeded_placement_mutations,
    solution,
)


def to_internal(item: dict[str, int]) -> Placement:
    return Placement(
        block_id=item["block_id"],
        bay_id=item["bay"],
        x=item["x"],
        y=item["y"],
        orient_idx=item["orient"],
        entry=item["entry"],
        exit=item["exit"],
    )


class ValidateParityTests(unittest.TestCase):
    def test_seeded_1000_candidates(self):
        alternate = (((0.0, 0.0), (2.0, 0.0), (2.0, 1.0), (0.0, 1.0)),)
        prob_info = instance(
            [
                block(release=0, due=20, processing=2, preferences=(8, 3), orientations=((UNIT_SQUARE,), alternate)),
                block(release=1, due=20, processing=1, preferences=(2, 9), orientations=((UNIT_SQUARE,), alternate)),
                block(release=0, due=20, processing=3, preferences=(7, 4), orientations=((UNIT_SQUARE,), alternate)),
                block(release=2, due=20, processing=0, preferences=(1, 6), orientations=((UNIT_SQUARE,), alternate)),
            ],
            bays=((6, 5), (5, 4)),
        )
        parsed = ProblemInstance.parse(prob_info)
        base = [
            placement(0, entry=0, exit=2, bay=0, x=0, y=0),
            placement(1, entry=2, exit=3, bay=0, x=0, y=0),
            placement(2, entry=0, exit=3, bay=1, x=0, y=0),
            placement(3, entry=3, exit=4, bay=1, x=0, y=0),
        ]
        baseline_check = official_check(prob_info, solution(base))
        self.assertTrue(baseline_check.feasible, baseline_check.violations)

        mismatches: list[tuple[int, bool, bool]] = []
        telemetry = ValidationTelemetry()
        for case_index, (block_id, candidate_raw) in enumerate(
            seeded_placement_mutations(base, cases=1000, seed=20260710)
        ):
            candidate = to_internal(candidate_raw)
            if case_index % 2 == 0:
                partial = SolutionState(parsed)
                for item in base:
                    if item["block_id"] != block_id:
                        partial.place(to_internal(item))
                targeted = validate_insertion(partial, candidate)
            else:
                changed = SolutionState(parsed)
                for item in base:
                    changed.place(candidate if item["block_id"] == block_id else to_internal(item))
                targeted = validate_changed(changed, {block_id})

            full_raw = [dict(item) for item in base]
            full_raw[block_id] = candidate_raw
            full = official_check(prob_info, solution(full_raw)).feasible
            telemetry.record_parity(targeted, full)
            if targeted != full:
                mismatches.append((case_index, targeted, full))

        self.assertEqual(1000, telemetry.cases)
        self.assertEqual(0, telemetry.mismatches)
        self.assertEqual([], mismatches[:10], f"{len(mismatches)} parity mismatches")

    def test_changed_checks_only_changed_unary_and_involving_pairs(self):
        parsed = ProblemInstance.parse(instance([block(), block(), block()]))
        state = SolutionState(parsed)
        state.place(Placement(0, 0, 0, 0, 0, 0, 2))
        state.place(Placement(1, 0, 2, 0, 0, 0, 2))
        state.place(Placement(2, 0, 0, 0, 0, 0, 2))

        self.assertTrue(validate_changed(state, {1}))
        self.assertFalse(validate_changed(state, {2}))


if __name__ == "__main__":
    unittest.main()
