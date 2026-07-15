"""Behavioral contracts for S4 checker-float assignment refinement."""

from __future__ import annotations

import math
from dataclasses import replace
import hashlib
import json
import unittest

from numpy.random import Generator, PCG64

from harness import cli as harness_cli
from harness import runner as harness_runner
from solver.alns import (
    CrossBayRegistry,
    OperatorRegistry,
    rank_cross_bay_candidates,
    run_cross_bay_candidate,
)
from solver.assign import (
    AssignmentVerification,
    AssignmentV1,
    assignment_from_solution,
    assignment_request,
    assignment_v2,
    choose_assignment_candidate,
)
from solver.checker_adapter import official_check
from solver.config import SolverConfig
from solver.construct import construct_profile
from solver.cpsat_backend import (
    ASSIGNMENT_SCALE,
    assign_cpsat,
    build_cpsat_assignment_spec,
)
from solver.exact import (
    AssignmentRequest,
    AssignmentResult as ExactAssignmentResult,
    SOLUTION_STATUSES,
)
from solver.gurobi_backend import build_gurobi_assignment_spec
from solver.incumbent import VerifiedIncumbent
from solver.instance import ProblemInstance
from solver.serialize import serialize_non_interlock
from solver.state import Placement, SolutionState
from tests.fixtures import block, instance


class CrossBayTests(unittest.TestCase):
    def _fixture(self):
        prob_info = instance(
            [
                block(workload=0.10, preferences=(0, 10)),
                block(workload=0.20, preferences=(10, 0)),
                block(workload=0.30, preferences=(10, 0)),
                block(workload=0.40, preferences=(0, 10)),
            ],
            bays=((4, 4), (4, 4)),
            weights={"w1": 1.0, "w2": 2.5, "w3": 7.25},
        )
        parsed = ProblemInstance.parse(prob_info)
        state = SolutionState(parsed)
        for placement in (
            Placement(0, 0, 0, 0, 0, 0, 1),
            Placement(1, 0, 0, 0, 0, 1, 2),
            Placement(2, 1, 0, 0, 0, 0, 1),
            Placement(3, 1, 0, 0, 0, 1, 2),
        ):
            state.place(placement)
        state.assert_invariants()
        incumbent = VerifiedIncumbent(parsed)
        incumbent.register_initial(state)
        return prob_info, state, incumbent

    @staticmethod
    def _solution_sha(solution):
        payload = json.dumps(solution, sort_keys=True, separators=(",", ":")).encode()
        return hashlib.sha256(payload).hexdigest()

    @staticmethod
    def _copy_state(state):
        copied = SolutionState(
            state.instance,
            geom=state.geom,
            shape_catalog=state.shape_catalog,
        )
        for placement in state.placements.values():
            copied.place(placement)
        return copied

    @staticmethod
    def _expected_assignment_delta(state, candidate):
        loads = list(state.objective_diagnostics.bay_loads)
        z3 = state.z3
        for block_id, source_bay, target_bay in zip(
            candidate.block_ids,
            candidate.source_bays,
            candidate.target_bays,
            strict=True,
        ):
            block_info = state.instance.blocks[block_id]
            loads[source_bay] -= block_info.workload
            loads[target_bay] += block_info.workload
            preference_best = max(block_info.bay_preferences)
            z3 += (
                preference_best
                - block_info.bay_preferences[target_bay]
                - (preference_best - block_info.bay_preferences[source_bay])
            )
        average_area = sum(bay.area for bay in state.instance.bays) / len(
            state.instance.bays
        )
        normalized = tuple(
            average_area / bay.area * load
            for bay, load in zip(state.instance.bays, loads, strict=True)
        )
        z2 = 0.0 if len(normalized) < 2 else max(normalized) - min(normalized)
        return z2 - state.z2, z3 - state.z3

    def test_move_swap_undo_and_float_delta(self):
        _prob_info, state, _incumbent = self._fixture()
        s3_registry = OperatorRegistry()
        registry = CrossBayRegistry()
        self.assertEqual(("d1", "d2", "d3", "d4", "d5"), s3_registry.destroy_names)
        self.assertEqual(("move", "swap", "d6"), registry.operator_names)
        self.assertTrue(
            all(operator.changes_assignment for operator in registry.operators.values())
        )

        candidates = rank_cross_bay_candidates(state, registry=registry)
        self.assertTrue(candidates)
        by_kind = {
            kind: next(candidate for candidate in candidates if candidate.kind == kind)
            for kind in ("move", "swap")
        }
        self.assertEqual(
            tuple(candidate.priority for candidate in candidates),
            tuple(sorted(candidate.priority for candidate in candidates)),
        )
        for candidate in by_kind.values():
            expected_z2, expected_z3 = self._expected_assignment_delta(
                state, candidate
            )
            self.assertTrue(
                math.isclose(candidate.delta_z2, expected_z2, rel_tol=1e-12, abs_tol=1e-12)
            )
            self.assertTrue(
                math.isclose(candidate.delta_z3, expected_z3, rel_tol=1e-12, abs_tol=1e-12)
            )
            self.assertTrue(
                math.isclose(
                    candidate.exact_weighted_delta,
                    state.instance.weights[1] * expected_z2
                    + state.instance.weights[2] * expected_z3,
                    rel_tol=1e-12,
                    abs_tol=1e-12,
                )
            )

        for fault in ("repair", "retime", "full_check"):
            prob_info, state, incumbent = self._fixture()
            before = state.capture_undo_token()
            before_sha = self._solution_sha(incumbent.solution)
            candidate = next(
                item
                for item in rank_cross_bay_candidates(state, registry=registry)
                if item.kind == "swap"
            )

            def inject(point, *, selected=fault):
                if point == selected:
                    raise RuntimeError(f"injected {selected} fault")

            result = run_cross_bay_candidate(
                state,
                incumbent,
                candidate,
                Generator(PCG64(20260710)),
                retime=lambda current, _bay_id, _budget: self._copy_state(current),
                fault_hook=inject,
            )

            self.assertFalse(result.committed)
            self.assertEqual(f"fault:{fault}", result.reason)
            self.assertEqual(before, state.capture_undo_token())
            self.assertEqual(before_sha, result.incumbent_sha256)
            self.assertEqual(before_sha, self._solution_sha(incumbent.solution))
            checked = official_check(
                prob_info, serialize_non_interlock(state.placements.values())
            )
            self.assertTrue(checked.feasible, checked.violations)
            self.assertEqual(5, checked.stage)

    def test_successful_move_and_swap_are_fully_checked(self):
        for kind in ("move", "swap"):
            prob_info, state, incumbent = self._fixture()
            before = state.capture_undo_token()
            before_sha = self._solution_sha(incumbent.solution)
            candidate = next(
                item
                for item in rank_cross_bay_candidates(state)
                if item.kind == kind and item.exact_weighted_delta < 0.0
            )
            retimed_bays = []

            def retime(current, bay_id, _budget):
                retimed_bays.append(bay_id)
                return self._copy_state(current)

            result = run_cross_bay_candidate(
                state,
                incumbent,
                candidate,
                Generator(PCG64(20260710)),
                retime=retime,
            )

            self.assertTrue(result.committed, (kind, result.reason))
            self.assertEqual((0, 1), result.affected_bays)
            self.assertEqual([0, 1], retimed_bays)
            self.assertTrue(result.checker_feasible)
            self.assertEqual(5, result.checker_stage)
            self.assertNotEqual(before.bay_members, state.bay_members)
            self.assertTrue(
                math.isclose(
                    state.z2 - before.z2,
                    candidate.delta_z2,
                    rel_tol=1e-12,
                    abs_tol=1e-12,
                )
            )
            self.assertTrue(
                math.isclose(
                    state.z3 - before.z3,
                    candidate.delta_z3,
                    rel_tol=1e-12,
                    abs_tol=1e-12,
                )
            )
            self.assertNotEqual(before_sha, result.incumbent_sha256)
            self.assertEqual(
                result.incumbent_sha256, self._solution_sha(incumbent.solution)
            )
            checked = official_check(
                prob_info, serialize_non_interlock(state.placements.values())
            )
            self.assertTrue(checked.feasible, checked.violations)
            self.assertEqual(5, checked.stage)

    def test_cross_bay_summary_requires_move_swap_counters(self):
        record = {
            "status": "passed",
            "checker": {"feasible": True, "stage": 5},
            "prior_checker": {"feasible": True, "stage": 5},
            "move_attempts": 1,
            "move_accepted": 0,
            "swap_attempts": 1,
            "swap_accepted": 1,
            "cross_bay_attempts": 2,
            "d6_attempts": 2,
            "retime_backend_attempts": 4,
            "unverified_return_count": 0,
            "wall_seconds": 1.0,
            "timelimit": 60.0,
        }
        summary, exit_code = harness_cli._cross_bay_summary(
            [record],
            expected_record_count=1,
            selector="high-w23",
            timelimits=(60.0,),
            seeds=(20260710,),
            features={"cross_bay": "true"},
        )
        self.assertEqual(harness_cli.EXIT_PASS, exit_code)
        self.assertEqual(1, summary["move_attempts"])
        self.assertEqual(1, summary["swap_attempts"])

        missing = dict(record)
        del missing["swap_attempts"]
        _summary, failed_exit = harness_cli._cross_bay_summary(
            [missing],
            expected_record_count=1,
            selector="high-w23",
            timelimits=(60.0,),
            seeds=(20260710,),
            features={"cross_bay": "true"},
        )
        self.assertEqual(harness_cli.EXIT_CHECKER_FAILURE, failed_exit)


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
        self.assertEqual(
            tuple(item.load_factor for item in spec.bays),
            tuple(average_area / bay.area for bay in parsed.bays),
        )
        self.assertEqual(0.0, spec.range_variable.lower_bound)
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

        v1_constructed = construct_profile(parsed, v1, "PF3").state
        self.assertEqual(
            tuple(sorted((block_id, item.bay_id) for block_id, item in v1.assignments.items())),
            tuple(
                sorted(
                    (placement.block_id, placement.bay_id)
                    for placement in v1_constructed.placements.values()
                )
            ),
        )
        v1_checked = official_check(
            prob_info,
            serialize_non_interlock(v1_constructed.placements.values()),
        )
        self.assertTrue(v1_checked.feasible, v1_checked.violations)
        self.assertEqual(5, v1_checked.stage)
        self.assertTrue(math.isclose(v1_checked.obj2, v1.z2, rel_tol=1e-6, abs_tol=1e-9))
        self.assertTrue(math.isclose(v1_checked.obj3, v1.z3, rel_tol=1e-6, abs_tol=1e-9))

        solved = assignment_v2(parsed, v1, config=config)
        if solved.status not in SOLUTION_STATUSES:
            self.assertIsNone(solved.solution)
            self.assertIn(solved.status, {"unavailable", "time_limit", "no_solution"})
            return

        self.assertIsInstance(solved.solution, tuple)
        proposed = assignment_from_solution(parsed, solved.solution, order=v1.order)
        constructed = construct_profile(parsed, proposed, "PF3").state
        self.assertEqual(
            tuple(sorted(solved.solution)),
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

    def test_fixed_assignment_proof_never_changes_requested_bay(self):
        prob_info = instance(
            [block(processing=1, workload=1, preferences=(100, 0)) for _ in range(18)],
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
            "fixture must exercise the bounded constructor's assignment fallback",
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
        self.assertTrue(math.isclose(checked.obj2, proposed.z2, rel_tol=1e-6))
        self.assertTrue(math.isclose(checked.obj3, proposed.z3, rel_tol=1e-6))

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


class AssignmentFallbackTests(unittest.TestCase):
    def test_scaled_candidate_rechecked_as_float(self):
        prob_info = instance(
            [
                block(workload=1.0000004, preferences=(1, 1)),
                block(workload=1.0000004, preferences=(1, 1)),
            ],
            bays=((8, 8), (8, 8)),
            weights={"w1": 1.0, "w2": 100.0, "w3": 1.0},
        )
        parsed = ProblemInstance.parse(prob_info)
        incumbent = assignment_from_solution(
            parsed,
            ((0, 0), (1, 1)),
            order=(0, 1),
        )

        def failed_gurobi(request, timebox):
            del request, timebox
            return ExactAssignmentResult(
                backend="gurobi", status="error", solution=None,
                objective=None, bound=None, z2=None, z3=None, overload=None,
                build_s=0.0, solve_s=0.0, first_solution_s=None,
                reason="injected Gurobi fault",
            )

        def scaled_cpsat(request, timebox):
            del request, timebox
            return ExactAssignmentResult(
                backend="cpsat", status="optimal", solution=((0, 0), (1, 0)),
                objective=-1.0, bound=-1.0, z2=0.0, z3=0.0, overload=0.0,
                build_s=0.0, solve_s=0.0, first_solution_s=0.0, reason=None,
            )

        selected = choose_assignment_candidate(
            parsed,
            incumbent,
            config=SolverConfig(
                assignment_timebox_seconds=2.0,
                assignment_threads=1,
            ),
            backend_calls={"gurobi": failed_gurobi, "cpsat": scaled_cpsat},
        )

        self.assertEqual("greedy", selected.backend)
        self.assertEqual(incumbent, selected.assignment)
        self.assertEqual(0.0, selected.evaluation.z2)
        self.assertIn("gurobi:error", selected.fallback_reason)
        self.assertIn("cpsat:float_z2_regression", selected.fallback_reason)


    def test_gurobi_fault_uses_cpsat_and_both_faults_keep_greedy(self):
        prob_info = instance(
            [
                block(workload=1.0, preferences=(1, 1)),
                block(workload=1.0, preferences=(1, 1)),
            ],
            bays=((8, 8), (8, 8)),
            weights={"w1": 1.0, "w2": 100.0, "w3": 1.0},
        )
        parsed = ProblemInstance.parse(prob_info)
        incumbent = assignment_from_solution(
            parsed,
            ((0, 0), (1, 0)),
            order=(0, 1),
        )

        def verify(candidate):
            constructed = harness_runner.construct_fixed_assignment(parsed, candidate)
            checked = official_check(
                prob_info,
                serialize_non_interlock(constructed.placements.values()),
            )
            return AssignmentVerification(
                feasible=checked.feasible and checked.stage == 5,
                objective=checked.objective,
                reason=None if checked.feasible else "; ".join(checked.violations),
            )

        def fault(backend, reason):
            def call(request, timebox):
                del request, timebox
                return ExactAssignmentResult(
                    backend=backend,
                    status="error",
                    solution=None,
                    objective=None,
                    bound=None,
                    z2=None,
                    z3=None,
                    overload=None,
                    build_s=0.0,
                    solve_s=0.0,
                    first_solution_s=None,
                    reason=reason,
                )

            return call

        def improving_cpsat(request, timebox):
            del request, timebox
            return ExactAssignmentResult(
                backend="cpsat",
                status="optimal",
                solution=((0, 0), (1, 1)),
                objective=0.0,
                bound=0.0,
                z2=0.0,
                z3=0.0,
                overload=0.0,
                build_s=0.0,
                solve_s=0.0,
                first_solution_s=0.0,
                reason=None,
            )

        cpsat_selected = choose_assignment_candidate(
            parsed,
            incumbent,
            config=SolverConfig(assignment_threads=1),
            backend_calls={
                "gurobi": fault("gurobi", "injected Gurobi fault"),
                "cpsat": improving_cpsat,
            },
            verify=verify,
        )
        self.assertEqual("cpsat", cpsat_selected.backend)
        self.assertTrue(cpsat_selected.verified)
        self.assertEqual(0.0, cpsat_selected.evaluation.z2)
        self.assertIn("gurobi:error", cpsat_selected.fallback_reason)

        greedy_selected = choose_assignment_candidate(
            parsed,
            incumbent,
            config=SolverConfig(assignment_threads=1),
            backend_calls={
                "gurobi": fault("gurobi", "injected Gurobi fault"),
                "cpsat": fault("cpsat", "injected CP-SAT fault"),
            },
            verify=verify,
        )
        self.assertEqual("greedy", greedy_selected.backend)
        self.assertEqual(incumbent, greedy_selected.assignment)
        self.assertTrue(greedy_selected.verified)
        self.assertIn("gurobi:error", greedy_selected.fallback_reason)
        self.assertIn("cpsat:error", greedy_selected.fallback_reason)

    def test_cpsat_assignment_model_and_unsafe_scaling_fallback(self):
        prob_info = instance(
            [
                block(workload=1.0, preferences=(1, 1)),
                block(workload=1.0, preferences=(1, 1)),
            ],
            bays=((8, 8), (8, 8)),
            weights={"w1": 1.0, "w2": 100.0, "w3": 1.0},
        )
        parsed = ProblemInstance.parse(prob_info)
        incumbent = assignment_from_solution(
            parsed,
            ((0, 0), (1, 0)),
            order=(0, 1),
        )
        request = assignment_request(
            parsed,
            incumbent,
            config=SolverConfig(assignment_threads=1),
        )
        spec = build_cpsat_assignment_spec(request, timebox=2.0)
        self.assertEqual(ASSIGNMENT_SCALE, spec.scale)
        self.assertLess(spec.objective_upper_bound, 1 << 62)
        self.assertTrue(
            all(
                pair.start == int(dict(request.current_assignment)[pair.block_id] == pair.bay_id)
                for pair in spec.pairs
            )
        )

        solved = assign_cpsat(request, 2.0)
        self.assertIn(solved.status, SOLUTION_STATUSES)
        self.assertIsNotNone(solved.solution)
        self.assertEqual({0, 1}, {bay_id for _block_id, bay_id in solved.solution})

        unsafe_prob = instance(
            [block(workload=1.0, preferences=(1, 1))],
            bays=((8, 8), (8, 8)),
            weights={"w1": 1.0, "w2": 1e20, "w3": 1.0},
        )
        unsafe_parsed = ProblemInstance.parse(unsafe_prob)
        unsafe_incumbent = assignment_from_solution(
            unsafe_parsed,
            ((0, 0),),
            order=(0,),
        )

        def failed_gurobi(request, timebox):
            del request, timebox
            return ExactAssignmentResult(
                backend="gurobi",
                status="error",
                solution=None,
                objective=None,
                bound=None,
                z2=None,
                z3=None,
                overload=None,
                build_s=0.0,
                solve_s=0.0,
                first_solution_s=None,
                reason="injected Gurobi fault",
            )

        unsafe_selected = choose_assignment_candidate(
            unsafe_parsed,
            unsafe_incumbent,
            config=SolverConfig(assignment_threads=1),
            backend_calls={"gurobi": failed_gurobi, "cpsat": assign_cpsat},
        )
        self.assertEqual("greedy", unsafe_selected.backend)
        self.assertEqual(unsafe_incumbent, unsafe_selected.assignment)
        self.assertIn("cpsat:unavailable", unsafe_selected.fallback_reason)

        unsafe_demand_request = replace(
            request,
            congestion_demands=tuple(
                (block_id, bay_id, 3e12)
                for block_id, bay_id in request.fit_pairs
            ),
            congestion_capacities=tuple(
                (bay_id, 4e12) for bay_id in request.bay_ids
            ),
            congestion_weight=0.0,
        )
        unsafe_demand = assign_cpsat(unsafe_demand_request, 2.0)
        self.assertEqual("unavailable", unsafe_demand.status)
        self.assertIn("unsafe CP-SAT assignment scaling", unsafe_demand.reason)

if __name__ == "__main__":
    unittest.main()
