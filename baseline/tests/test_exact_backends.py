"""Common exact-retiming contract and backend-isolation tests for S2."""

from __future__ import annotations

import unittest
from unittest.mock import patch

from solver.budget import Budget
from solver.checker_adapter import official_check
from solver.exact import (
    BackendProbe,
    ExactBackend,
    ExactResult,
    RetimeRequest,
    deadline_bounded_call,
    normalize_result,
    probe_backends,
)
from solver.incumbent import VerifiedIncumbent
from solver.instance import ProblemInstance
from solver.serialize import serialize_non_interlock
from solver.state import Placement, SolutionState
from solver.trivial import build_t0
from tests.fixtures import block, instance


class ExactContractTests(unittest.TestCase):
    def test_probe_after_incumbent_and_exception_isolation(self):
        parsed = ProblemInstance.parse(instance([block(), block(release=1)]))
        incumbent = VerifiedIncumbent(parsed)
        calls: list[str] = []

        def step(label: str, *, fault: bool = False):
            def run():
                calls.append(label)
                if fault:
                    raise RuntimeError(f"fault at {label}")
                return label

            return run

        probes = (
            BackendProbe(
                backend="gurobi",
                import_module=step("gurobi:import"),
                create_environment=step("gurobi:env", fault=True),
                check_license=step("gurobi:license"),
                build_model=step("gurobi:model"),
                optimize=step("gurobi:optimize"),
                extract=step("gurobi:extract"),
            ),
            BackendProbe(
                backend="cpsat",
                import_module=step("cpsat:import"),
                create_environment=step("cpsat:env"),
                check_license=step("cpsat:license"),
                build_model=step("cpsat:model"),
                optimize=step("cpsat:optimize", fault=True),
                extract=step("cpsat:extract"),
            ),
        )

        deferred = probe_backends(incumbent, probes, budget=Budget(2, reserve=0))
        self.assertEqual([], calls)
        self.assertTrue(all(item.failure_stage == "incumbent" for item in deferred))

        incumbent.register_initial(build_t0(parsed))
        health = probe_backends(incumbent, probes, budget=Budget(2, reserve=0))

        self.assertEqual("environment", health[0].failure_stage)
        self.assertEqual("optimize", health[1].failure_stage)
        self.assertFalse(any(item.available for item in health))
        self.assertNotIn("gurobi:license", calls)
        self.assertNotIn("cpsat:extract", calls)
        self.assertTrue(incumbent.checker_result.feasible)
        self.assertEqual(5, incumbent.checker_result.stage)
        calls_after_first_probe = list(calls)
        self.assertEqual(
            health,
            probe_backends(incumbent, probes, budget=Budget(2, reserve=0)),
        )
        self.assertEqual(calls_after_first_probe, calls)

    def test_result_validation_normalizes_malformed_and_out_of_budget(self):
        request = RetimeRequest(
            block_ids=(0, 1),
            releases=((0, 0), (1, 0)),
            dues=((0, 10), (1, 10)),
            dwells=((0, 2), (1, 2)),
            current_entries=((0, 0), (1, 2)),
            conflict_pairs=((0, 1),),
            seed=20260710,
            threads=4,
        )
        valid = ExactResult(
            backend="gurobi",
            status="feasible",
            solution=((0, 0, 2), (1, 2, 4)),
            objective=0.0,
            bound=0.0,
            build_s=0.01,
            solve_s=0.02,
            first_solution_s=0.02,
        )
        self.assertEqual(valid, normalize_result(request, valid, timebox=1.0))

        malformed = ExactResult(
            backend="gurobi",
            status="feasible",
            solution=((0, 0, 3), (1, 2, 4)),
            objective=0.0,
            bound=0.0,
            build_s=0.01,
            solve_s=0.02,
            first_solution_s=0.02,
        )
        normalized = normalize_result(request, malformed, timebox=1.0)
        self.assertEqual("invalid", normalized.status)
        self.assertIsNone(normalized.solution)

        overtime = normalize_result(request, valid, timebox=0.01)
        self.assertEqual("time_limit", overtime.status)
        self.assertIsNone(overtime.solution)

    def test_protocol_is_retime_only(self):
        self.assertIn("retime", ExactBackend.__dict__)
        self.assertNotIn("assign", ExactBackend.__dict__)

    def test_deadline_wrapper_isolates_faults_and_overtime(self):
        request = RetimeRequest(
            block_ids=(0,),
            releases=((0, 0),),
            dues=((0, 10),),
            dwells=((0, 2),),
            current_entries=((0, 0),),
            conflict_pairs=(),
            seed=20260710,
            threads=1,
        )
        calls: list[float] = []
        no_budget = Budget(0, reserve=0)
        result = deadline_bounded_call(
            "gurobi",
            request,
            lambda _request, allowance: calls.append(allowance),
            timebox=1,
            budget=no_budget,
        )
        self.assertEqual("time_limit", result.status)
        self.assertEqual([], calls)

        faulted = deadline_bounded_call(
            "cpsat",
            request,
            lambda _request, _allowance: (_ for _ in ()).throw(RuntimeError("boom")),
            timebox=1,
            budget=Budget(2, reserve=0),
        )
        self.assertEqual("error", faulted.status)
        self.assertIn("RuntimeError: boom", faulted.reason or "")

        mismatch = deadline_bounded_call(
            "cpsat",
            request,
            lambda _request, _allowance: ExactResult(
                backend="gurobi",
                status="no_solution",
                solution=None,
                objective=None,
                bound=None,
                build_s=0.0,
                solve_s=0.0,
                first_solution_s=None,
            ),
            timebox=1,
            budget=Budget(2, reserve=0),
        )
        self.assertEqual("invalid", mismatch.status)
        self.assertIn("identity mismatch", mismatch.reason or "")

        clock_values = iter((10.0, 10.5))
        overtime = deadline_bounded_call(
            "gurobi",
            request,
            lambda _request, _allowance: ExactResult(
                backend="gurobi",
                status="feasible",
                solution=((0, 0, 2),),
                objective=0.0,
                bound=0.0,
                build_s=0.01,
                solve_s=0.01,
                first_solution_s=0.01,
            ),
            timebox=0.25,
            budget=Budget(2, reserve=0),
            clock=lambda: next(clock_values),
        )
        self.assertEqual("time_limit", overtime.status)
        self.assertIsNone(overtime.solution)


class GurobiRetimeTests(unittest.TestCase):
    def test_equality_handoff_optimum(self):
        from solver.gurobi_backend import build_gurobi_model_spec, retime_gurobi

        request = RetimeRequest(
            block_ids=(0, 1),
            releases=((0, 0), (1, 0)),
            dues=((0, 2), (1, 3)),
            dwells=((0, 2), (1, 2)),
            current_entries=((0, 0), (1, 2)),
            conflict_pairs=((0, 1),),
            seed=20260710,
            threads=4,
        )

        spec = build_gurobi_model_spec(request, timebox=2.0)
        self.assertEqual(4, spec.horizon)
        self.assertEqual(
            ((0, 0, 4, 0), (1, 0, 4, 2)),
            tuple(
                (item.block_id, item.lower_bound, item.upper_bound, item.start)
                for item in spec.entries
            ),
        )
        self.assertTrue(all(item.variable_type == "integer" for item in spec.entries))
        self.assertTrue(all(item.variable_type == "integer" for item in spec.tardiness))
        self.assertEqual((0, 1), spec.objective_blocks)
        self.assertEqual(1, len(spec.disjunctions))
        disjunction = spec.disjunctions[0]
        self.assertEqual("binary", disjunction.variable_type)
        self.assertEqual((0, 1), (disjunction.left, disjunction.right))
        self.assertEqual(
            ((1, 0, 1), (0, 1, 0)),
            (
                (
                    disjunction.left_value,
                    disjunction.first_before,
                    disjunction.second_after,
                ),
                (
                    disjunction.right_value,
                    disjunction.second_before,
                    disjunction.first_after,
                ),
            ),
        )
        self.assertEqual(0, spec.output_flag)
        self.assertEqual(4, spec.threads)
        self.assertEqual(20260710, spec.seed)
        self.assertEqual(2.0, spec.time_limit)
        self.assertEqual(1, spec.mip_focus)
        self.assertEqual(0.0, spec.mip_gap)

        prob_info = instance(
            [
                block(due=2, processing=2),
                block(due=3, processing=2),
            ]
        )
        parsed = ProblemInstance.parse(prob_info)
        copied = SolutionState(parsed)
        copied.place(Placement(0, 0, 0, 0, 0, 0, 2))
        copied.place(Placement(1, 0, 0, 0, 0, 2, 4))
        checked = official_check(
            prob_info,
            serialize_non_interlock(copied.placements.values()),
        )
        self.assertTrue(checked.feasible, checked.violations)
        self.assertEqual(5, checked.stage)
        self.assertEqual(1.0, checked.obj1)

        result = retime_gurobi(request, 2.0)
        self.assertEqual("gurobi", result.backend)
        if result.status == "unavailable":
            self.assertIsNone(result.solution)
            self.assertIsNone(result.objective)
            self.assertIsNotNone(result.reason)
            self.assertIn("unavailable", (result.reason or "").lower())
            return

        self.assertEqual("optimal", result.status)
        self.assertEqual(1.0, result.objective)
        self.assertEqual(1.0, result.bound)
        self.assertEqual(((0, 0, 2), (1, 2, 4)), result.solution)
        self.assertEqual(
            result,
            normalize_result(request, result, timebox=2.0),
        )

    def test_license_unavailability_is_a_normalized_record(self):
        from solver.gurobi_backend import retime_gurobi

        request = RetimeRequest(
            block_ids=(0,),
            releases=((0, 0),),
            dues=((0, 2),),
            dwells=((0, 2),),
            current_entries=((0, 0),),
            conflict_pairs=(),
            seed=20260710,
            threads=1,
        )

        class UnlicensedGurobi:
            @staticmethod
            def Env(*, empty):
                self.assertTrue(empty)
                raise RuntimeError("license unavailable")

        with patch(
            "solver.gurobi_backend.importlib.import_module",
            return_value=UnlicensedGurobi,
        ):
            result = retime_gurobi(request, 2.0)
        self.assertEqual("gurobi", result.backend)
        self.assertEqual("unavailable", result.status)
        self.assertIsNone(result.solution)
        self.assertIsNone(result.objective)
        self.assertIn("license", result.reason or "")
        self.assertIn("unavailable", (result.reason or "").lower())


class BackendParityTests(unittest.TestCase):
    def test_small_optima_match(self):
        from solver.cpsat_backend import build_cpsat_model_spec, retime_cpsat
        from solver.gurobi_backend import build_gurobi_model_spec, retime_gurobi

        request = RetimeRequest(
            block_ids=(0, 1),
            releases=((0, 0), (1, 0)),
            dues=((0, 2), (1, 3)),
            dwells=((0, 2), (1, 2)),
            current_entries=((0, 0), (1, 2)),
            conflict_pairs=((0, 1),),
            seed=20260710,
            threads=4,
        )
        gurobi_spec = build_gurobi_model_spec(request, timebox=2.0)
        cpsat_spec = build_cpsat_model_spec(request, timebox=2.0)

        def common_spec(spec):
            return (
                spec.horizon,
                tuple(
                    (
                        item.block_id,
                        item.lower_bound,
                        item.upper_bound,
                        item.start,
                        item.variable_type,
                    )
                    for item in spec.entries
                ),
                tuple(
                    (
                        item.block_id,
                        item.lower_bound,
                        item.upper_bound,
                        item.start,
                        item.variable_type,
                    )
                    for item in spec.tardiness
                ),
                tuple(
                    (item.left, item.right, item.start, item.variable_type)
                    for item in spec.disjunctions
                ),
                spec.objective_blocks,
                spec.seed,
                spec.time_limit,
            )

        self.assertEqual(common_spec(gurobi_spec), common_spec(cpsat_spec))
        self.assertEqual(4, cpsat_spec.workers)
        self.assertFalse(cpsat_spec.log_search_progress)

        results = (retime_gurobi(request, 2.0), retime_cpsat(request, 2.0))
        available = [result for result in results if result.status != "unavailable"]
        self.assertTrue(any(result.backend == "cpsat" for result in available))
        self.assertTrue(available)

        prob_info = instance(
            [
                block(due=2, processing=2),
                block(due=3, processing=2),
            ]
        )
        parsed = ProblemInstance.parse(prob_info)
        manual_optimum = 1.0
        for result in available:
            self.assertEqual("optimal", result.status, result.reason)
            self.assertEqual(manual_optimum, result.objective)
            self.assertEqual(manual_optimum, result.bound)
            self.assertEqual(((0, 0, 2), (1, 2, 4)), result.solution)
            self.assertEqual(result, normalize_result(request, result, timebox=2.0))

            state = SolutionState(parsed)
            for block_id, entry, exit_time in result.solution or ():
                state.place(
                    Placement(block_id, 0, 0, 0, 0, entry, exit_time)
                )
            checked = official_check(
                prob_info,
                serialize_non_interlock(state.placements.values()),
            )
            self.assertTrue(checked.feasible, (result.backend, checked.violations))
            self.assertEqual(5, checked.stage)
            self.assertEqual(manual_optimum, checked.obj1)

        self.assertEqual(
            1,
            len({result.objective for result in available}),
            available,
        )


if __name__ == "__main__":
    unittest.main()
