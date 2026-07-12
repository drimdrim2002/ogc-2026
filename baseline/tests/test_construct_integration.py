from __future__ import annotations

import json
import math
import unittest

from solver.assignment import AssignmentPortfolio, AssignmentSeed
from solver.budget import Budget
from solver.construct import (
    PROFILES,
    ConstructionSeed,
    ConstructorConfig,
    construct_complete,
    construct_portfolio,
)
from solver.entry import OptionalPhaseResult, solve
from solver.fallback import build_safe_candidate
from solver.geometry import GeometryKernel, PairState
from solver.instance import parse_instance
from solver.serialize import serialize
from solver.state import Placement, SolutionSnapshot, compute_objective
from tests.helpers import block, checker, instance, load_example


def _assert_complete_stage_five(testcase, raw, result, kernel):
    testcase.assertTrue(result.complete, result)
    testcase.assertIsNotNone(result.snapshot)
    testcase.assertEqual(len(raw["blocks"]), len(result.snapshot.placements))
    operations = serialize(result.snapshot, kernel)
    checked = checker(raw, operations)
    testcase.assertTrue(checked["feasible"], checked)
    testcase.assertEqual(5, checked["stage"])
    objective = compute_objective(parse_instance(raw), result.snapshot)
    for internal, external in zip(
        (objective.z1, objective.z2, objective.z3, objective.total),
        (checked["obj1"], checked["obj2"], checked["obj3"], checked["objective"]),
    ):
        testcase.assertTrue(math.isclose(internal, external, rel_tol=1e-6, abs_tol=1e-9))
    placements = result.snapshot.placements
    for index, left in enumerate(placements):
        for right in placements[index + 1 :]:
            overlaps = left.entry < right.exit and right.entry < left.exit
            if left.bay_id == right.bay_id and overlaps:
                testcase.assertIs(kernel.relation(left, right).state, PairState.FREE)
    return checked


class ConstructorIntegrationTests(unittest.TestCase):
    def test_construct_example_without_master(self):
        raw = load_example()
        parsed = parse_instance(raw)
        kernel = GeometryKernel.from_instance(parsed)
        result = construct_complete(
            parsed,
            kernel,
            ConstructionSeed(None, "slack_due", 20260710),
            Budget.start(10),
        )

        checked = _assert_complete_stage_five(self, raw, result, kernel)
        self.assertEqual(10, result.metrics.placements_committed)
        self.assertGreaterEqual(result.metrics.candidates_attempted, 1)
        self.assertTrue(math.isfinite(checked["objective"]))

    def test_construct_from_each_profile_and_random_profile_reproducible(self):
        raw = load_example()
        parsed = parse_instance(raw)
        kernel = GeometryKernel.from_instance(parsed)
        fallback = build_safe_candidate(parsed, Budget.start(1))
        by_id = {placement.block_id: placement for placement in fallback.placements}
        assignment_seed = AssignmentSeed(
            bay_by_block=tuple(by_id[index].bay_id for index in range(len(parsed.blocks))),
            suggested_start=tuple(by_id[index].entry for index in range(len(parsed.blocks))),
            priority=tuple(range(len(parsed.blocks))),
            source="integration",
            surrogate_cost=0.0,
        )
        results = {}
        for index, profile in enumerate(PROFILES):
            with self.subTest(profile=profile):
                result = construct_complete(
                    parsed,
                    kernel,
                    ConstructionSeed(assignment_seed, profile, 20260710 + index),
                    Budget.start(10),
                )
                _assert_complete_stage_five(self, raw, result, kernel)
                results[profile] = result
        repeated = construct_complete(
            parsed,
            kernel,
            ConstructionSeed(assignment_seed, "seeded_mixture", 20260715),
            Budget.start(10),
        )
        self.assertEqual(
            results["seeded_mixture"].snapshot.placements,
            repeated.snapshot.placements,
        )

    def test_master_failure_empty_portfolio_still_constructs(self):
        raw = load_example()
        parsed = parse_instance(raw)
        kernel = GeometryKernel.from_instance(parsed)
        empty = AssignmentPortfolio.empty(
            status="ERROR", diagnostics=("exception=LicenseError",)
        )

        results = construct_portfolio(parsed, kernel, empty, Budget.start(10))

        self.assertEqual(1, len(results))
        _assert_complete_stage_five(self, raw, results[0], kernel)

    def test_forced_escalation_and_empty_window_fallback(self):
        raw = load_example()
        parsed = parse_instance(raw)
        kernel = GeometryKernel.from_instance(parsed)
        config = ConstructorConfig(
            time_cap=0,
            escalated_time_cap=0,
            anchor_cap=0,
            lattice_cap=0,
        )
        result = construct_complete(
            parsed,
            kernel,
            ConstructionSeed(None, "release_due", 20260710),
            Budget.start(10),
            config,
        )

        _assert_complete_stage_five(self, raw, result, kernel)
        self.assertEqual(len(parsed.blocks), result.metrics.fallback_count)
        self.assertEqual("FALLBACK_COMPLETE", result.status)

    def test_constructor_does_not_install_checker_rejected_candidate(self):
        raw = instance(
            [
                block(release=0, due=2, processing=2),
                block(release=0, due=2, processing=2),
            ],
            bays=((10, 10),),
            weights=(10, 1, 1),
        )
        parsed = parse_instance(raw)
        kernel = GeometryKernel.from_instance(parsed)
        fallback = build_safe_candidate(parsed, Budget.start(5))
        fallback_bytes = json.dumps(serialize(fallback), separators=(",", ":"))
        candidate = SolutionSnapshot(
            (
                Placement(0, 0, 0, 0, 0, 0, 2),
                Placement(1, 0, 0, 3, 0, 0, 2),
            )
        )
        candidate = candidate.with_objective(compute_objective(parsed, candidate))
        calls = 0

        def rejecting_checker(prob_info, operations):
            nonlocal calls
            calls += 1
            if calls == 1:
                return checker(prob_info, operations)
            return {
                "feasible": False,
                "stage": 4,
                "violations": ["synthetic rejection"],
            }

        def loader():
            return lambda *_: OptionalPhaseResult(
                candidates=(candidate,), precedence_provider=kernel
            )

        output = solve(
            raw,
            5,
            checker=rejecting_checker,
            optional_phase_loader=loader,
        )

        self.assertEqual(2, calls)
        self.assertEqual(fallback_bytes, json.dumps(output, separators=(",", ":")))
        self.assertEqual(5, checker(raw, output)["stage"])


if __name__ == "__main__":
    unittest.main()
