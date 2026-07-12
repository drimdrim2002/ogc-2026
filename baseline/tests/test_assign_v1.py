"""Behavioral contracts for the deterministic S1 assignment-v1 heuristic."""

from __future__ import annotations

import math
import json
from pathlib import Path
import unittest

from solver.assign import AssignmentV1
from solver.checker_adapter import official_check
from solver.instance import ProblemInstance
from solver.serialize import serialize_non_interlock
from solver.state import Placement
from tests.fixtures import block, instance


def _sequential_placements(parsed: ProblemInstance, assignment) -> list[Placement]:
    available = [0 for _ in parsed.bays]
    placements: list[Placement] = []
    for block_id in assignment.order:
        selected = assignment.assignments[block_id]
        spec = parsed.blocks[block_id]
        orientation = spec.orientations[selected.orient_idx]
        ranges = orientation.integer_position_ranges(parsed.bays[selected.bay_id])
        assert ranges is not None
        entry = max(spec.release_time, available[selected.bay_id])
        exit_time = entry + spec.dwell
        available[selected.bay_id] = exit_time
        placements.append(
            Placement(
                block_id=block_id,
                bay_id=selected.bay_id,
                x=ranges[0].start,
                y=ranges[1].start,
                orient_idx=selected.orient_idx,
                entry=entry,
                exit=exit_time,
            )
        )
    return placements


class AssignmentV1Tests(unittest.TestCase):
    def test_all_blocks_fit_and_replay(self):
        prob_info = instance(
            [
                block(
                    layers=(((0, 0), (5, 0), (5, 4), (0, 4)),),
                    workload=4,
                    preferences=(100, 1),
                ),
                block(workload=8, preferences=(8, 9)),
                block(workload=2, preferences=(7, 7)),
            ],
            bays=((4, 4), (6, 5)),
            weights={"w1": 3.0, "w2": 1.25, "w3": 2.5},
        )
        parsed = ProblemInstance.parse(prob_info)

        first = AssignmentV1(parsed).assign()
        second = AssignmentV1(parsed).assign()

        self.assertEqual(first, second)
        self.assertEqual(set(first.assignments), {0, 1, 2})
        self.assertEqual(first.order[0], 0)
        self.assertTrue(math.isinf(first.assignments[0].regret))
        self.assertEqual(first.assignments[0].bay_id, 1)
        for block_id, selected in first.assignments.items():
            self.assertTrue(
                parsed.fit_matrix[block_id][selected.bay_id][selected.orient_idx]
            )
        self.assertEqual(first.metrics.assigned, 3)
        self.assertEqual(first.metrics.fallback, 0)
        self.assertEqual(first.metrics.fit_failures, 0)

    def test_checker_float_assignment_terms_match_sequential_solution(self):
        prob_info = instance(
            [
                block(workload=1.25, preferences=(9, 2)),
                block(workload=2.75, preferences=(1, 8)),
                block(workload=0.5, preferences=(4, 4)),
            ],
            bays=((3, 4), (7, 5)),
            weights={"w1": 100.0, "w2": 1.7, "w3": 2.3},
        )
        parsed = ProblemInstance.parse(prob_info)
        assigned = AssignmentV1(parsed).assign()
        placements = _sequential_placements(parsed, assigned)

        checked = official_check(
            prob_info,
            serialize_non_interlock(placements),
        )

        self.assertTrue(checked.feasible, checked.violations)
        self.assertEqual(checked.stage, 5)
        self.assertAlmostEqual(checked.obj2, assigned.z2, places=12)
        self.assertAlmostEqual(checked.obj3, assigned.z3, places=12)

    def test_one_bay_has_zero_z2_and_no_fallback(self):
        prob_info = instance(
            [block(workload=1.5), block(workload=3.25)],
            bays=((8, 8),),
        )
        assigned = AssignmentV1(ProblemInstance.parse(prob_info)).assign()
        parsed = ProblemInstance.parse(prob_info)
        checked = official_check(
            prob_info,
            serialize_non_interlock(_sequential_placements(parsed, assigned)),
        )

        self.assertEqual(assigned.z2, 0.0)
        self.assertTrue(checked.feasible, checked.violations)
        self.assertEqual(checked.stage, 5)
        self.assertEqual(assigned.metrics.assigned, 2)
        self.assertEqual(assigned.metrics.fallback, 0)
        self.assertEqual(assigned.metrics.fit_failures, 0)

    def test_tracked_example_sequential_assignment_full_checks(self):
        path = (
            Path(__file__).resolve().parents[2]
            / "alg_tester"
            / "example"
            / "example_B2_b10.json"
        )
        prob_info = json.loads(path.read_text(encoding="utf-8"))
        parsed = ProblemInstance.parse(prob_info)
        assigned = AssignmentV1(parsed).assign()
        checked = official_check(
            prob_info,
            serialize_non_interlock(_sequential_placements(parsed, assigned)),
        )

        self.assertEqual(assigned.metrics.assigned, len(parsed.blocks))
        self.assertTrue(checked.feasible, checked.violations)
        self.assertEqual(checked.stage, 5)
        self.assertAlmostEqual(checked.obj2, assigned.z2, places=9)
        self.assertAlmostEqual(checked.obj3, assigned.z3, places=9)


if __name__ == "__main__":
    unittest.main()
