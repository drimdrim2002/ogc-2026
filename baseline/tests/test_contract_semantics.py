"""Checker-authoritative semantic and instance-parsing contracts for S0-01."""

from __future__ import annotations

import math
import unittest

from solver.checker_adapter import official_check
from solver.instance import ProblemInstance, UnsolvableInstanceError
from tests.fixtures import (
    TWO_SQUARE,
    block,
    instance,
    interlock_pair_instance,
    nested_interlock_placements,
    placement,
    solution,
)


class CheckerContractTests(unittest.TestCase):
    def assertFeasible(self, prob_info, candidate):
        result = official_check(prob_info, candidate)
        self.assertTrue(result.feasible, result.violations)
        self.assertEqual(5, result.stage)
        return result

    def assertInfeasibleAt(self, stage, prob_info, candidate):
        result = official_check(prob_info, candidate)
        self.assertFalse(result.feasible)
        self.assertEqual(stage, result.stage, result.violations)
        return result

    def test_half_open_intervals_allow_endpoint_contact(self):
        prob_info = instance([block(layers=(TWO_SQUARE,)), block(layers=(TWO_SQUARE,))])
        candidate = solution(
            [placement(0, entry=0, exit=3), placement(1, entry=3, exit=6)]
        )
        self.assertFeasible(prob_info, candidate)

    def test_same_day_handoff_requires_exit_before_entry(self):
        prob_info = instance([block(layers=(TWO_SQUARE,)), block(layers=(TWO_SQUARE,))])
        placements = [placement(0, entry=0, exit=3), placement(1, entry=3, exit=6)]
        self.assertFeasible(prob_info, solution(placements))
        reversed_day = solution(
            placements,
            operation_order={3: [("ENTRY", 1), ("EXIT", 0)]},
        )
        self.assertInfeasibleAt(5, prob_info, reversed_day)

    def test_same_day_union_overlap_is_rejected_at_entry(self):
        prob_info = instance([block(layers=(TWO_SQUARE,)), block(layers=(TWO_SQUARE,))])
        candidate = solution(
            [placement(0, entry=0, exit=3), placement(1, entry=0, exit=3)]
        )
        self.assertInfeasibleAt(2, prob_info, candidate)

    def test_obs_nested_guest_may_enter_and_exit_while_host_exists(self):
        prob_info = interlock_pair_instance()
        self.assertFeasible(prob_info, solution(nested_interlock_placements()))

    def test_obs_host_cannot_enter_while_guest_exists(self):
        prob_info = interlock_pair_instance()
        candidate = solution(
            [placement(0, entry=1, exit=5, x=2), placement(1, entry=0, exit=10)]
        )
        self.assertInfeasibleAt(2, prob_info, candidate)

    def test_obs_host_cannot_exit_while_guest_exists(self):
        prob_info = interlock_pair_instance()
        candidate = solution(
            [placement(0, entry=0, exit=5, x=2), placement(1, entry=1, exit=10)]
        )
        self.assertInfeasibleAt(3, prob_info, candidate)

    def test_obs_interlock_pair_cannot_enter_on_same_day(self):
        prob_info = interlock_pair_instance()
        candidate = solution(
            [placement(0, entry=0, exit=10, x=2), placement(1, entry=0, exit=5)]
        )
        self.assertInfeasibleAt(2, prob_info, candidate)

    def test_obs_equal_day_exit_is_order_sensitive(self):
        prob_info = interlock_pair_instance()
        placements = nested_interlock_placements(host_exit=5, guest_exit=5)
        guest_first = solution(
            placements,
            operation_order={5: [("EXIT", 1), ("EXIT", 0)]},
        )
        self.assertFeasible(prob_info, guest_first)
        host_first = solution(
            placements,
            operation_order={5: [("EXIT", 0), ("EXIT", 1)]},
        )
        self.assertInfeasibleAt(5, prob_info, host_first)

    def test_polygon_and_bay_boundary_contact_are_legal(self):
        prob_info = instance([block(), block()], bays=((2, 1),))
        candidate = solution(
            [placement(0, entry=0, exit=2), placement(1, entry=0, exit=2, x=1)]
        )
        self.assertFeasible(prob_info, candidate)

    def test_zero_processing_time_still_requires_one_day_residence(self):
        prob_info = instance([block(processing=0)])
        same_day = solution(
            [placement(0, entry=0, exit=0)],
            operation_order={0: [("ENTRY", 0), ("EXIT", 0)]},
        )
        self.assertInfeasibleAt(5, prob_info, same_day)
        one_day = solution([placement(0, entry=0, exit=1)])
        self.assertFeasible(prob_info, one_day)

    def test_exit_extension_is_legal_and_changes_z1(self):
        prob_info = instance([block(due=2, processing=1)])
        on_time = self.assertFeasible(
            prob_info, solution([placement(0, entry=0, exit=2)])
        )
        extended = self.assertFeasible(
            prob_info, solution([placement(0, entry=0, exit=4)])
        )
        self.assertEqual(0.0, on_time.obj1)
        self.assertEqual(2.0, extended.obj1)

    def test_z2_remains_float_without_flooring(self):
        prob_info = instance(
            [
                block(workload=1, preferences=(50, 50)),
                block(workload=1, preferences=(50, 50)),
            ],
            bays=((3, 2), (5, 2)),
            weights={"w1": 0.0, "w2": 1.0, "w3": 0.0},
        )
        result = self.assertFeasible(
            prob_info,
            solution(
                [
                    placement(0, entry=0, exit=1, bay=0),
                    placement(1, entry=0, exit=1, bay=1),
                ]
            ),
        )
        self.assertTrue(math.isclose(8.0 / 15.0, result.obj2, rel_tol=1e-12))
        self.assertEqual(result.obj2, result.objective)


class InstanceParsingTests(unittest.TestCase):
    def test_parse_preserves_exact_vertices_and_derives_time_contract(self):
        exact_layer = (
            (0.0, 0.0),
            (1.00004, 0.0),
            (1.00004, 1.0),
            (0.0, 1.0),
        )
        parsed = ProblemInstance.parse(
            instance([block(layers=(exact_layer,), release=2, due=7, processing=0)])
        )
        spec = parsed.blocks[0]
        self.assertEqual(1, spec.dwell)
        self.assertEqual(4, spec.slack)
        self.assertEqual(tuple(exact_layer), spec.orientation_keys[0][0])

    def test_fit_matrix_handles_negative_local_minima_and_orientation_choice(self):
        negative_min = ((0.0, 0.0), (-2.0, 0.0), (-2.0, 1.0), (0.0, 1.0))
        too_wide = ((0.0, 0.0), (4.0, 0.0), (4.0, 1.0), (0.0, 1.0))
        prob_info = instance(
            [block(orientations=((negative_min,), (too_wide,)), preferences=(100,))],
            bays=((3, 2),),
        )
        parsed = ProblemInstance.parse(prob_info)
        self.assertEqual((((True, False),),), parsed.fit_matrix)
        parsed.assert_solvable_fit()

    def test_fit_preflight_names_an_unfittable_block(self):
        too_wide = ((0.0, 0.0), (4.0, 0.0), (4.0, 1.0), (0.0, 1.0))
        parsed = ProblemInstance.parse(
            instance([block(layers=(too_wide,))], bays=((3, 2),))
        )
        with self.assertRaisesRegex(UnsolvableInstanceError, r"block 0"):
            parsed.assert_solvable_fit()


if __name__ == "__main__":
    unittest.main()
