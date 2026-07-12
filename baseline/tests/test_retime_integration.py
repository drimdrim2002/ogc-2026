from __future__ import annotations

import itertools
import math
import unittest
from unittest.mock import patch

from solver.budget import Budget
from solver.construct import ConstructionSeed, construct_complete
from solver.geometry import GeometryKernel, PairState
from solver.instance import parse_instance
from solver.entry import load_optional_phase, solve
from solver.fallback import build_safe_candidate
from solver.retime import BackendResult, RetimingConfig, retime
from solver.serialize import serialize
from solver.state import Placement, SolutionSnapshot, compute_objective
from tests.helpers import block, checker, instance, load_example, rectangle


def _objective_snapshot(parsed, placements):
    snapshot = SolutionSnapshot(tuple(placements))
    return snapshot.with_objective(compute_objective(parsed, snapshot))


def _assert_stage_five(testcase, raw, parsed, kernel, snapshot):
    checked = checker(raw, serialize(snapshot, kernel))
    testcase.assertTrue(checked["feasible"], checked)
    testcase.assertEqual(5, checked["stage"])
    objective = snapshot.objective or compute_objective(parsed, snapshot)
    for internal, external in zip(
        (objective.z1, objective.z2, objective.z3, objective.total),
        (checked["obj1"], checked["obj2"], checked["obj3"], checked["objective"]),
    ):
        testcase.assertTrue(math.isclose(internal, external, rel_tol=1e-6, abs_tol=1e-9))
    return checked


class _NoSolutionBackend:
    def __init__(self, status):
        self.status = status

    def solve(self, request, instance, time_limit, config):
        del request, instance, time_limit, config
        return BackendResult(status=self.status, diagnostics=("solution_count=0",))


class RetimingIntegrationTests(unittest.TestCase):
    def test_enumerated_two_block_optimum_matches_gurobi_primary_secondary(self):
        raw = instance(
            [
                block(release=0, due=2, processing=2),
                block(release=0, due=2, processing=2),
            ],
            weights=(3, 5, 7),
        )
        parsed = parse_instance(raw)
        kernel = GeometryKernel.from_instance(parsed)
        snapshot = _objective_snapshot(
            parsed,
            (
                Placement(0, 0, 0, 0, 0, 0, 2),
                Placement(1, 0, 0, 0, 0, 5, 7),
            ),
        )
        relation = kernel.relation(snapshot.placements[0], snapshot.placements[1])
        self.assertIs(relation.state, PairState.SEPARATE)
        enumerated = []
        for a0, a1 in itertools.product(range(3), repeat=2):
            e0, e1 = a0 + 2, a1 + 2
            if e0 <= 4 and e1 <= 4 and relation.allows((a0, e0), (a1, e1)):
                enumerated.append((max(0, e0 - 2) + max(0, e1 - 2), 0, a0, a1))
        optimum = min(enumerated)[:2]

        result = retime(snapshot, parsed, kernel, Budget.start(20))

        self.assertIn(result.status, {"OPTIMAL", "TIME_LIMIT", "SUBOPTIMAL"}, result)
        self.assertEqual(float(optimum[0]), result.primary)
        self.assertEqual(0.0, result.gap)
        self.assertEqual(optimum[0], result.snapshot.objective.z1)
        _assert_stage_five(self, raw, parsed, kernel, result.snapshot)

    def test_secondary_tightens_dwell_at_fixed_primary(self):
        raw = instance([block(release=0, due=10, processing=2)])
        parsed = parse_instance(raw)
        kernel = GeometryKernel.from_instance(parsed)
        snapshot = _objective_snapshot(parsed, (Placement(0, 0, 0, 0, 0, 0, 5),))

        result = retime(snapshot, parsed, kernel, Budget.start(20))

        self.assertEqual(0.0, result.primary)
        self.assertEqual((0, 2), (result.snapshot.placements[0].entry, result.snapshot.placements[0].exit))
        self.assertTrue(any(item == "secondary=0.0" for item in result.diagnostics), result.diagnostics)
        _assert_stage_five(self, raw, parsed, kernel, result.snapshot)

    def test_interlock_witness_and_equal_exit_topological_serialization(self):
        low = [rectangle(0, 0, 2, 2)]
        low_with_high_cap = [
            rectangle(0, 0, 2, 2),
            rectangle(4, 0, 6, 2),
        ]
        raw = instance(
            [
                block(release=0, due=5, processing=5, orientations=[low]),
                block(release=0, due=5, processing=4, orientations=[low_with_high_cap]),
            ],
            bays=((12, 12),),
            weights=(10, 1, 1),
        )
        parsed = parse_instance(raw)
        kernel = GeometryKernel.from_instance(parsed)
        snapshot = _objective_snapshot(
            parsed,
            (
                Placement(0, 0, 0, 4, 0, 0, 5),
                Placement(1, 0, 0, 0, 0, 5, 9),
            ),
        )
        self.assertIs(kernel.relation(*snapshot.placements).state, PairState.I_OUTER)

        result = retime(snapshot, parsed, kernel, Budget.start(20))

        self.assertEqual(0.0, result.snapshot.objective.z1)
        by_id = {item.block_id: item for item in result.snapshot.placements}
        self.assertEqual(5, by_id[0].exit)
        self.assertEqual(5, by_id[1].exit)
        operations = serialize(result.snapshot, kernel)
        exits = [item for item in operations["operations"]["5"] if item["type"] == "EXIT"]
        self.assertEqual([1, 0], [item["block_id"] for item in exits])
        _assert_stage_five(self, raw, parsed, kernel, result.snapshot)

    def test_retime_constructed_example_keeps_layout_and_never_worsens(self):
        raw = load_example()
        parsed = parse_instance(raw)
        kernel = GeometryKernel.from_instance(parsed)
        constructed = construct_complete(
            parsed,
            kernel,
            ConstructionSeed(None, "slack_due", 20260710),
            Budget.start(10),
        )
        self.assertTrue(constructed.complete)
        before = constructed.snapshot
        before_layout = tuple(
            (item.block_id, item.bay_id, item.orient_idx, item.x, item.y)
            for item in before.placements
        )

        result = retime(before, parsed, kernel, Budget.start(20))

        after_layout = tuple(
            (item.block_id, item.bay_id, item.orient_idx, item.x, item.y)
            for item in result.snapshot.placements
        )
        self.assertIs(before, result.snapshot)
        self.assertEqual("NO_IMPROVEMENT", result.status)
        self.assertEqual(before_layout, after_layout)
        self.assertLessEqual(result.snapshot.objective.z1, before.objective.z1)
        self.assertLessEqual(result.snapshot.objective.total, before.objective.total)
        _assert_stage_five(self, raw, parsed, kernel, result.snapshot)

    def test_timeout_and_license_failure_roll_back_identity(self):
        raw = instance([block()])
        parsed = parse_instance(raw)
        kernel = GeometryKernel.from_instance(parsed)
        snapshot = _objective_snapshot(parsed, (Placement(0, 0, 0, 0, 0, 0, 2),))

        timeout = retime(
            snapshot,
            parsed,
            kernel,
            Budget.start(20),
            backend_factory=lambda: _NoSolutionBackend("TIME_LIMIT"),
        )

        def license_failure():
            raise RuntimeError("synthetic license failure")

        license_result = retime(
            snapshot,
            parsed,
            kernel,
            Budget.start(20),
            backend_factory=license_failure,
        )

        self.assertIs(snapshot, timeout.snapshot)
        self.assertEqual("TIME_LIMIT", timeout.status)
        self.assertIs(snapshot, license_result.snapshot)
        self.assertEqual("ERROR", license_result.status)
        _assert_stage_five(self, raw, parsed, kernel, timeout.snapshot)
        _assert_stage_five(self, raw, parsed, kernel, license_result.snapshot)

    def test_w1_zero_candidate_is_not_a_strict_total_improvement(self):
        raw = instance([block(release=0, due=10, processing=2)], weights=(0, 1, 1))
        parsed = parse_instance(raw)
        kernel = GeometryKernel.from_instance(parsed)
        snapshot = _objective_snapshot(parsed, (Placement(0, 0, 0, 0, 0, 0, 5),))

        result = retime(snapshot, parsed, kernel, Budget.start(20))

        self.assertEqual("W1_ZERO", result.status)
        self.assertIs(snapshot, result.snapshot)
        _assert_stage_five(self, raw, parsed, kernel, result.snapshot)

    def test_constructor_output_flows_through_optional_retiming_hook(self):
        raw = load_example()
        parsed = parse_instance(raw)
        fallback = build_safe_candidate(parsed, Budget.start(1))

        phase_result = load_optional_phase()(parsed, fallback, Budget.start(5))

        self.assertEqual(1, len(phase_result.candidates))
        self.assertEqual(1, len(phase_result.retiming_results))
        self.assertIs(phase_result.candidates[0], phase_result.retiming_results[0].snapshot)
        _assert_stage_five(
            self,
            raw,
            parsed,
            phase_result.precedence_provider,
            phase_result.candidates[0],
        )

    def test_public_entry_contains_unexpected_retimer_exception(self):
        raw = load_example()

        with patch("solver.retime.retime", side_effect=RuntimeError("synthetic retimer crash")):
            output = solve(raw, 5)

        checked = checker(raw, output)
        self.assertTrue(checked["feasible"], checked)
        self.assertEqual(5, checked["stage"])


if __name__ == "__main__":
    unittest.main()
