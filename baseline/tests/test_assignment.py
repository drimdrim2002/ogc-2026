from __future__ import annotations

import math
import unittest

from solver.assignment import (
    AssignmentConfig,
    BackendResult,
    BackendSolution,
    build_assignment_data,
    build_congestion_guides,
    build_exact_portfolio,
    candidate_times,
    evaluate_assignment,
    hamming_distance,
    map_backend_status,
    required_hamming,
)
from solver.budget import Budget
from solver.geometry import GeometryKernel
from solver.instance import parse_instance
from tests.helpers import block, instance, rectangle


class FakeBackend:
    def __init__(self, exact: BackendResult | None = None, guide: BackendResult | None = None):
        self.exact_result = exact or BackendResult("INFEASIBLE")
        self.guide_result = guide or BackendResult("INFEASIBLE")
        self.exact_requests = []
        self.guide_requests = []

    def solve_exact(self, request):
        self.exact_requests.append(request)
        return self.exact_result

    def solve_guide(self, request):
        self.guide_requests.append(request)
        return self.guide_result


def _factory(backend):
    return lambda: backend


class AssignmentDataTests(unittest.TestCase):
    def test_fit_mask_excludes_fractional_only_bay(self):
        fractional = [[[0, 0], [-3.01, 1], [8.99, 1], [8.99, 0]]]
        raw = instance(
            [block(preferences=(5, 5), orientations=[fractional])],
            bays=((12, 5), (13, 5)),
        )
        parsed = parse_instance(raw)
        geometry = GeometryKernel.from_instance(parsed)
        data = build_assignment_data(parsed, geometry)

        self.assertEqual((1,), data.feasible_bays[0])
        self.assertIsNone(data.union_area[0][0])
        self.assertGreater(data.union_area[0][1], 0.0)

    def test_exact_request_and_hand_objective(self):
        raw = instance(
            [
                block(workload=1, preferences=(10, 0)),
                block(workload=3, preferences=(0, 10)),
            ],
            bays=((10, 10), (10, 10)),
            weights=(1, 2, 3),
        )
        parsed = parse_instance(raw)
        geometry = GeometryKernel.from_instance(parsed)
        backend = FakeBackend(
            exact=BackendResult(
                "OPTIMAL",
                (BackendSolution((0, 1), objective=4.0),),
                lower_bound=4.0,
                gap=0.0,
                diagnostics=("variables=6", "constraints=6", "SolCount=1"),
            )
        )
        portfolio = build_exact_portfolio(
            parsed, geometry, Budget.start(10), backend_factory=_factory(backend)
        )

        self.assertEqual("OPTIMAL", portfolio.status)
        self.assertEqual((0, 1), portfolio.seeds[0].bay_by_block)
        self.assertEqual(4.0, portfolio.lower_bound)
        request = backend.exact_requests[0]
        self.assertEqual(4, sum(len(row) for row in request.data.feasible_bays))
        self.assertEqual(2, request.w2)
        self.assertEqual(3, request.w3)
        objective = evaluate_assignment(parsed, request.data, (0, 1))
        self.assertEqual((2.0, 0.0, 4.0), (objective.z2, objective.z3, objective.weighted))

    def test_zero_objective_uses_distinct_hamming_without_division(self):
        raw = instance(
            [block(workload=0, preferences=(1, 1)) for _ in range(4)],
            bays=((10, 10), (10, 10)),
            weights=(1, 1, 1),
        )
        parsed = parse_instance(raw)
        geometry = GeometryKernel.from_instance(parsed)
        candidates = ((0, 0, 0, 0), (1, 1, 0, 0), (0, 0, 1, 1), (1, 1, 1, 1))
        backend = FakeBackend(
            exact=BackendResult(
                "OPTIMAL", tuple(BackendSolution(item, objective=0.0) for item in candidates), 0.0, 0.0
            )
        )
        config = AssignmentConfig(portfolio_size=4)
        portfolio = build_exact_portfolio(
            parsed, geometry, Budget.start(10), config, _factory(backend)
        )

        self.assertEqual(4, len(portfolio.seeds))
        self.assertTrue(all(seed.surrogate_cost == 0.0 for seed in portfolio.seeds))
        for index, left in enumerate(portfolio.seeds):
            for right in portfolio.seeds[index + 1 :]:
                self.assertGreaterEqual(hamming_distance(left.bay_by_block, right.bay_by_block), 2)

    def test_n100_diversity_gate_is_three(self):
        raw = instance(
            [block(workload=0, preferences=(1, 1)) for _ in range(100)],
            bays=((10, 10), (10, 10)),
        )
        parsed = parse_instance(raw)
        geometry = GeometryKernel.from_instance(parsed)
        assignments = [tuple(0 for _ in range(100))]
        for offset in (0, 3, 6):
            assignments.append(tuple(1 if offset <= i < offset + 3 else 0 for i in range(100)))
        backend = FakeBackend(
            exact=BackendResult("OPTIMAL", tuple(BackendSolution(item) for item in assignments), 0.0, 0.0)
        )
        config = AssignmentConfig(portfolio_size=4)
        portfolio = build_exact_portfolio(
            parsed, geometry, Budget.start(10), config, _factory(backend)
        )

        self.assertEqual(3, required_hamming(100, config))
        self.assertEqual(4, len(portfolio.seeds))
        for index, left in enumerate(portfolio.seeds):
            for right in portfolio.seeds[index + 1 :]:
                self.assertGreaterEqual(hamming_distance(left.bay_by_block, right.bay_by_block), 3)


class AssignmentContractTests(unittest.TestCase):
    def test_candidate_times_include_on_time_events_and_integer_tardy_cap(self):
        raw = instance(
            [
                block(release=2, due=6, processing=2),
                block(release=3, due=8, processing=1),
            ]
        )
        parsed = parse_instance(raw)
        times = candidate_times(parsed, 0, AssignmentConfig(tardy_time_cap=2))

        self.assertTrue({2, 3, 4}.issubset(times))
        self.assertTrue(all(isinstance(value, int) for value in times))
        self.assertTrue(all(value >= 2 for value in times))
        self.assertLessEqual(len([value for value in times if value > 4]), 4)

    def test_candidate_time_cap_never_drops_mandatory_on_time_range(self):
        raw = instance([block(release=2, due=12, processing=1)])
        parsed = parse_instance(raw)
        times = candidate_times(
            parsed, 0, AssignmentConfig(candidate_time_cap=3, tardy_time_cap=0)
        )

        self.assertTrue(set(range(2, 12)).issubset(times))

    def test_guide_request_has_soft_capacity_profile_and_positive_slack(self):
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
        backend = FakeBackend(
            guide=BackendResult(
                "OPTIMAL",
                (BackendSolution((0, 0), (0, 0), objective=4.2, slack=4.2),),
                lower_bound=4.2,
                gap=0.0,
                diagnostics=("positive_slack=140.0",),
            )
        )
        config = AssignmentConfig(
            portfolio_size=1,
            candidate_time_cap=1,
            tardy_time_cap=0,
            rho_profiles=(0.60,),
            slack_multipliers=(1.0,),
        )
        portfolio = build_congestion_guides(
            parsed, geometry, Budget.start(60), config, _factory(backend)
        )

        self.assertEqual("OPTIMAL", portfolio.status)
        self.assertEqual(1, len(portfolio.seeds))
        request = backend.guide_requests[0]
        self.assertEqual(0.60, request.rho)
        self.assertGreater(request.slack_price, 0.0)
        self.assertEqual(((0,), (0,)), request.candidate_times)
        self.assertIn("profile=0.6/1.0 positive_slack=140.0", portfolio.diagnostics)

    def test_status_mapping_matrix(self):
        expected = {
            ("OPTIMAL", True): "OPTIMAL",
            ("TIME_LIMIT", True): "TIME_LIMIT_FEASIBLE",
            ("TIME_LIMIT", False): "TIME_LIMIT_EMPTY",
            ("INFEASIBLE", False): "INFEASIBLE",
            ("ERROR", False): "ERROR",
        }
        for fixture, status in expected.items():
            with self.subTest(fixture=fixture):
                self.assertEqual(status, map_backend_status(*fixture))

    def test_time_limit_and_threads_follow_global_cap(self):
        raw = instance([block(preferences=(1, 1))], bays=((10, 10), (10, 10)))
        parsed = parse_instance(raw)
        geometry = GeometryKernel.from_instance(parsed)
        backend = FakeBackend(exact=BackendResult("TIME_LIMIT"))
        budget = Budget.start(100)
        portfolio = build_exact_portfolio(
            parsed, geometry, budget, backend_factory=_factory(backend)
        )

        self.assertEqual("TIME_LIMIT_EMPTY", portfolio.status)
        request = backend.exact_requests[0]
        self.assertLessEqual(request.time_limit, 1.0)
        self.assertLessEqual(request.time_limit, 0.03 * budget.search_remaining() + 1e-6)
        self.assertEqual(4, request.threads)
        self.assertEqual(20260710, request.seed)

    def test_timeout_seed_has_no_optimal_lower_bound(self):
        raw = instance([block(preferences=(1, 1))], bays=((10, 10), (10, 10)))
        parsed = parse_instance(raw)
        geometry = GeometryKernel.from_instance(parsed)
        backend = FakeBackend(
            exact=BackendResult(
                "TIME_LIMIT", (BackendSolution((1,), objective=0.0),), lower_bound=-99, gap=0.5
            )
        )
        portfolio = build_exact_portfolio(
            parsed, geometry, Budget.start(10), backend_factory=_factory(backend)
        )

        self.assertEqual("TIME_LIMIT_FEASIBLE", portfolio.status)
        self.assertEqual(1, len(portfolio.seeds))
        self.assertIsNone(portfolio.lower_bound)
        self.assertTrue(math.isclose(0.5, portfolio.gap))


if __name__ == "__main__":
    unittest.main()
