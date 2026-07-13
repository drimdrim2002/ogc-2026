"""Behavioral contracts for S4 checker-float assignment refinement."""

from __future__ import annotations

import math
import unittest

from harness import cli as harness_cli
from harness import runner as harness_runner
from solver.exact import AssignmentRequest, SOLUTION_STATUSES
from solver.assign import (
    AssignmentV1,
    assignment_from_solution,
    assignment_request,
    assignment_v2,
)
from solver.checker_adapter import official_check
from solver.config import SolverConfig
from solver.construct import construct_profile
from solver.gurobi_backend import build_gurobi_assignment_spec
from solver.instance import ProblemInstance
from solver.serialize import serialize_non_interlock
from solver.state import Placement, SolutionState
from solver.validate import validate_pair
from tests.fixtures import block, instance


class AssignmentV2Tests(unittest.TestCase):
    def test_gurobi_float_z2_matches_checker(self):
        prob_info = instance(
            [
                block(workload=0.10, preferences=(9, 2, 1)),
                block(workload=0.20, preferences=(8, 3, 1)),
                block(workload=0.30, preferences=(2, 9, 1)),
                block(workload=0.40, preferences=(1, 8, 3)),
                block(workload=0.55, preferences=(1, 2, 9)),
                block(workload=0.65, preferences=(3, 1, 8)),
            ],
            bays=((8, 8), (10, 8), (12, 8)),
            weights={"w1": 5.0, "w2": 17.25, "w3": 0.75},
        )
        parsed = ProblemInstance.parse(prob_info)
        v1 = AssignmentV1(parsed).assign()
        config = SolverConfig(
            assignment_timebox_seconds=2.0,
            assignment_threads=1,
        )

        request = assignment_request(parsed, v1, config=config)
        self.assertIsInstance(request, AssignmentRequest)
        spec = build_gurobi_assignment_spec(
            request,
            timebox=config.assignment_timebox_seconds,
        )

        average_area = sum(bay.area for bay in parsed.bays) / len(parsed.bays)
        expected_factors = tuple(average_area / bay.area for bay in parsed.bays)
        self.assertEqual(
            tuple(item.load_factor for item in spec.bays),
            expected_factors,
        )
        self.assertEqual(spec.range_variable.lower_bound, 0.0)
        self.assertGreater(spec.range_variable.upper_bound, 0.0)
        self.assertTrue(
            all(
                item.start
                == int(v1.assignments[item.block_id].bay_id == item.bay_id)
                for item in spec.binary_variables
            )
        )
        self.assertTrue(
            all(
                isinstance(value, float)
                for value in (
                    spec.w2,
                    spec.w3,
                    spec.congestion_weight,
                    *(item.load_factor for item in spec.bays),
                    *(item.workload for item in spec.binary_variables),
                )
            )
        )

        v1_constructed = harness_runner.construct_fixed_assignment(parsed, v1)
        v1_checked = official_check(
            prob_info,
            serialize_non_interlock(v1_constructed.placements.values()),
        )
        self.assertTrue(v1_checked.feasible, v1_checked.violations)
        self.assertEqual(v1_checked.stage, 5)
        self.assertTrue(
            math.isclose(v1_checked.obj2, v1.z2, rel_tol=1e-6, abs_tol=1e-9)
        )
        self.assertTrue(
            math.isclose(v1_checked.obj3, v1.z3, rel_tol=1e-6, abs_tol=1e-9)
        )

        solved = assignment_v2(parsed, v1, config=config)
        if solved.status not in SOLUTION_STATUSES:
            self.assertIsNone(solved.solution)
            self.assertIn(solved.status, {"unavailable", "time_limit", "no_solution"})
            return

        self.assertIsInstance(solved.solution, tuple)
        proposed = assignment_from_solution(
            parsed,
            solved.solution,
            order=v1.order,
        )
        constructed = harness_runner.construct_fixed_assignment(parsed, proposed)
        checked = official_check(
            prob_info,
            serialize_non_interlock(constructed.placements.values()),
        )

        self.assertTrue(checked.feasible, checked.violations)
        self.assertEqual(checked.stage, 5)
        self.assertIsNotNone(solved.z2)
        self.assertIsNotNone(solved.z3)
        self.assertTrue(
            math.isclose(checked.obj2, solved.z2, rel_tol=1e-6, abs_tol=1e-9),
            (checked.obj2, solved.z2),
        )
        self.assertTrue(
            math.isclose(checked.obj3, solved.z3, rel_tol=1e-6, abs_tol=1e-9),
            (checked.obj3, solved.z3),
        )

    def test_fixed_assignment_construction_never_falls_back_to_another_bay(self):
        prob_info = instance(
            [
                block(
                    processing=1,
                    workload=1,
                    preferences=(100, 0),
                )
                for _ in range(18)
            ],
            bays=((1, 1), (1, 1)),
        )
        parsed = ProblemInstance.parse(prob_info)
        proposed = assignment_from_solution(
            parsed,
            tuple((block_id, 0) for block_id in range(18)),
            order=tuple(range(18)),
        )

        fallback_constructed = construct_profile(parsed, proposed, "PF3")
        self.assertTrue(
            any(
                placement.bay_id != proposed.assignments[placement.block_id].bay_id
                for placement in fallback_constructed.state.placements.values()
            ),
            "fixture must exercise the recorded constructor fallback",
        )

        constructed = harness_runner.construct_fixed_assignment(parsed, proposed)
        self.assertEqual(
            tuple((block_id, 0) for block_id in range(18)),
            tuple(
                sorted(
                    (placement.block_id, placement.bay_id)
                    for placement in constructed.placements.values()
                )
            ),
        )
        checked = official_check(
            prob_info,
            serialize_non_interlock(constructed.placements.values()),
        )
        self.assertTrue(checked.feasible, checked.violations)
        self.assertEqual(5, checked.stage)
        self.assertTrue(
            math.isclose(checked.obj2, proposed.z2, rel_tol=1e-6, abs_tol=1e-9)
        )
        self.assertTrue(
            math.isclose(checked.obj3, proposed.z3, rel_tol=1e-6, abs_tol=1e-9)
        )

    def test_checker_roundoff_contact_is_avoided_by_fixed_construction(self):
        first = (
            (0.0, 0.0),
            (-3.0889, -3.0889),
            (-6.1777, -6.1777),
            (-7.7222, -7.7222),
            (-3.9991, -11.4452),
            (-2.4547, -9.9008),
            (0.6342, -6.8119),
            (3.0421, -7.0361),
            (3.1135, -3.1135),
        )
        second_lower = (
            (0.0, 0.0),
            (-3.9335, -3.9335),
            (-7.8669, -7.8669),
            (-7.084, -0.783),
            (-3.1505, 3.1505),
        )
        second_upper = (
            (0.0, 0.0),
            (-3.9335, -3.9335),
            (-7.8669, -7.8669),
            (-7.084, -0.783),
            (-10.2345, 2.3675),
            (-6.301, 6.301),
            (-3.1505, 3.1505),
        )
        prob_info = instance(
            [
                block(
                    layers=(first,),
                    release=52,
                    due=100,
                    processing=15,
                ),
                block(
                    layers=(second_lower, second_upper),
                    release=52,
                    due=100,
                    processing=12,
                ),
            ],
            bays=((80, 80),),
        )
        parsed = ProblemInstance.parse(prob_info)
        unsafe = (
            Placement(0, 0, 46, 12, 0, 52, 67),
            Placement(1, 0, 50, 16, 0, 52, 64),
        )
        unsafe_state = SolutionState(parsed)
        self.assertTrue(validate_pair(unsafe_state, unsafe[0], unsafe[1]))
        rejected = official_check(prob_info, serialize_non_interlock(unsafe))
        self.assertFalse(rejected.feasible)
        self.assertEqual(2, rejected.stage)

        proposed = assignment_from_solution(parsed, ((0, 0), (1, 0)), order=(0, 1))
        constructed = harness_runner.construct_fixed_assignment(parsed, proposed)
        checked = official_check(
            prob_info,
            serialize_non_interlock(constructed.placements.values()),
        )
        self.assertTrue(checked.feasible, checked.violations)
        self.assertEqual(5, checked.stage)

    def test_failed_checker_summary_is_null_safe_and_nonzero(self):
        failed = {
            "status": "checker_failed",
            "checker": {"feasible": False, "stage": 2},
            "v1_checker": {"feasible": False, "stage": 2},
            "backend": {"name": "gurobi", "status": "optimal"},
            "assigned": 2,
            "block_count": 2,
            "v1_membership_preserved": True,
            "v2_membership_preserved": True,
            "v1_float_z2_relative_error": None,
            "v1_float_z3_relative_error": None,
            "v2_float_z2_relative_error": None,
            "v2_float_z3_relative_error": None,
            "wall_seconds": 0.1,
            "timelimit": 60.0,
        }
        summary, exit_code = harness_cli._assignment_v2_summary(
            [failed],
            expected_record_count=1,
            selector="high-w23",
            timelimits=(60.0,),
            seeds=(20260710,),
            features={"assignment_backend": "gurobi"},
        )

        self.assertEqual("failed", summary["status"])
        self.assertIsNone(summary["max_v1_float_z2_relative_error"])
        self.assertIsNone(summary["max_v2_float_z2_relative_error"])
        self.assertEqual(harness_cli.EXIT_CHECKER_FAILURE, exit_code)


if __name__ == "__main__":
    unittest.main()
