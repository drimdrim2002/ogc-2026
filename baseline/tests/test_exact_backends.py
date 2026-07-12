"""Common exact-retiming contract and backend-isolation tests for S2."""

from __future__ import annotations

import unittest

from solver.budget import Budget
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


if __name__ == "__main__":
    unittest.main()
