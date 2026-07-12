from __future__ import annotations

import itertools
import json
import math
import unittest
from unittest.mock import patch

from solver.assignment import (
    AssignmentConfig,
    BackendResult,
    BackendSolution,
    build_assignment_data,
    build_congestion_guides,
    build_exact_portfolio,
    evaluate_assignment,
)
from solver.budget import Budget
from solver.entry import OptionalPhaseResult, solve
from solver.fallback import build_safe_candidate
from solver.geometry import GeometryKernel
from solver.instance import parse_instance
from solver.serialize import serialize
from solver.state import Placement, SolutionSnapshot, compute_objective
from tests.helpers import block, checker, instance, load_example, rectangle


class FakeBackend:
    def __init__(self, result):
        self.result = result

    def solve_exact(self, request):
        return self.result

    def solve_guide(self, request):
        return self.result


def _serial_realization(parsed, seed) -> SolutionSnapshot:
    previous_exit = [0] * len(parsed.bays)
    placements = []
    for block_info in parsed.blocks:
        bay_id = seed.bay_by_block[block_info.index]
        option = next(item for item in block_info.fitting_options if item[0] == bay_id)
        _, orient_idx, reference_range = option
        entry = max(block_info.release_time, previous_exit[bay_id])
        exit_time = entry + block_info.dwell
        x, y = reference_range.anchor
        placements.append(
            Placement(block_info.index, bay_id, orient_idx, x, y, entry, exit_time)
        )
        previous_exit[bay_id] = exit_time
    snapshot = SolutionSnapshot(tuple(placements))
    return snapshot.with_objective(compute_objective(parsed, snapshot))


def _tiny_exact_instance():
    return instance(
        [
            block(workload=1, preferences=(9, 3)),
            block(workload=2, preferences=(4, 8)),
            block(workload=3, preferences=(7, 7)),
            block(workload=1.5, preferences=(2, 9)),
        ],
        bays=((10, 10), (20, 10)),
        weights=(5, 3, 7),
    )


class AssignmentIntegrationTests(unittest.TestCase):
    def test_exact_master_matches_enumeration(self):
        raw = _tiny_exact_instance()
        parsed = parse_instance(raw)
        geometry = GeometryKernel.from_instance(parsed)
        data = build_assignment_data(parsed, geometry)
        enumerated = [
            (evaluate_assignment(parsed, data, tuple(assignment)).weighted, tuple(assignment))
            for assignment in itertools.product(*data.feasible_bays)
        ]
        expected_cost, _ = min(enumerated)
        portfolio = build_exact_portfolio(parsed, geometry, Budget.start(10))

        if portfolio.status in {"UNAVAILABLE", "ERROR"}:
            self.assertFalse(portfolio.seeds)
            self.assertTrue(any("exception=" in item for item in portfolio.diagnostics))
            return
        self.assertEqual("OPTIMAL", portfolio.status, portfolio.diagnostics)
        self.assertTrue(portfolio.seeds)
        self.assertTrue(math.isclose(expected_cost, portfolio.lower_bound, rel_tol=1e-6, abs_tol=1e-9))
        self.assertTrue(
            math.isclose(expected_cost, portfolio.seeds[0].surrogate_cost, rel_tol=1e-6, abs_tol=1e-9)
        )
        self.assertTrue(any(item.startswith("variables=") for item in portfolio.diagnostics))
        self.assertTrue(any(item.startswith("SolCount=") for item in portfolio.diagnostics))

    def test_seed_objective_matches_checker(self):
        raw = _tiny_exact_instance()
        parsed = parse_instance(raw)
        geometry = GeometryKernel.from_instance(parsed)
        portfolio = build_exact_portfolio(parsed, geometry, Budget.start(10))
        if not portfolio.seeds:
            self.assertIn(portfolio.status, {"UNAVAILABLE", "ERROR", "TIME_LIMIT_EMPTY"})
            return
        seed = portfolio.seeds[0]
        snapshot = _serial_realization(parsed, seed)
        result = checker(raw, serialize(snapshot))
        data = build_assignment_data(parsed, geometry)
        assignment = evaluate_assignment(parsed, data, seed.bay_by_block)

        self.assertTrue(result["feasible"], result)
        self.assertEqual(5, result["stage"])
        self.assertTrue(math.isclose(assignment.z2, result["obj2"], rel_tol=1e-6, abs_tol=1e-9))
        self.assertTrue(math.isclose(assignment.z3, result["obj3"], rel_tol=1e-6, abs_tol=1e-9))
        weighted = parsed.weights.w2 * result["obj2"] + parsed.weights.w3 * result["obj3"]
        self.assertTrue(math.isclose(assignment.weighted, weighted, rel_tol=1e-6, abs_tol=1e-9))

    def test_import_and_license_failure_keep_safe_solution(self):
        raw = load_example()
        parsed = parse_instance(raw)
        fallback = build_safe_candidate(parsed, Budget.start(5))
        fallback_result = checker(raw, serialize(fallback))

        class LicenseError(RuntimeError):
            pass

        failures = (ImportError("missing gurobipy"), LicenseError("license unavailable"))
        for failure in failures:
            with self.subTest(failure=type(failure).__name__):
                with patch("solver.assignment._gurobi_backend_factory", side_effect=failure):
                    result = solve(raw, 5, checker=checker)
                checked = checker(raw, result)
                self.assertTrue(checked["feasible"], checked)
                self.assertEqual(5, checked["stage"])
                self.assertLess(checked["objective"], fallback_result["objective"])

    def test_timeout_seed_is_only_guide(self):
        raw = instance([block(due=0, preferences=(1, 1))], bays=((10, 10), (10, 10)))
        parsed = parse_instance(raw)
        fallback = build_safe_candidate(parsed, Budget.start(5))
        fallback_bytes = json.dumps(serialize(fallback), separators=(",", ":"))
        backend = FakeBackend(
            BackendResult("TIME_LIMIT", (BackendSolution((1,), objective=0.0),), gap=0.5)
        )
        captured = []

        def loader():
            def phase(instance_value, snapshot, budget):
                geometry = GeometryKernel.from_instance(instance_value)
                portfolio = build_exact_portfolio(
                    instance_value,
                    geometry,
                    budget,
                    backend_factory=lambda: backend,
                )
                captured.append((portfolio, snapshot))
                return OptionalPhaseResult(assignment_portfolio=portfolio)

            return phase

        result = solve(raw, 5, checker=checker, optional_phase_loader=loader)

        self.assertEqual(1, len(captured))
        self.assertEqual("TIME_LIMIT_FEASIBLE", captured[0][0].status)
        self.assertEqual((1,), captured[0][0].seeds[0].bay_by_block)
        self.assertEqual(fallback_bytes, json.dumps(result, separators=(",", ":")))
        self.assertEqual(5, checker(raw, result)["stage"])

    def test_actual_capacity_slack_model_is_feasible_when_available(self):
        full_bay = [rectangle(0, 0, 10, 10)]
        raw = instance(
            [
                block(release=0, due=1, processing=1, orientations=[full_bay]),
                block(release=0, due=1, processing=1, orientations=[full_bay]),
            ],
            bays=((10, 10),),
            weights=(3, 1, 1),
        )
        parsed = parse_instance(raw)
        geometry = GeometryKernel.from_instance(parsed)
        config = AssignmentConfig(
            portfolio_size=1,
            candidate_time_cap=1,
            tardy_time_cap=0,
            rho_profiles=(0.60,),
            slack_multipliers=(1.0,),
        )
        portfolio = build_congestion_guides(parsed, geometry, Budget.start(60), config)

        if portfolio.status in {"UNAVAILABLE", "ERROR"}:
            self.assertFalse(portfolio.seeds)
            return
        self.assertEqual("OPTIMAL", portfolio.status, portfolio.diagnostics)
        self.assertEqual(1, len(portfolio.seeds))
        slack_line = next(item for item in portfolio.diagnostics if "positive_slack=" in item)
        self.assertGreater(float(slack_line.rsplit("=", 1)[1]), 0.0)


if __name__ == "__main__":
    unittest.main()
