from __future__ import annotations

import copy
import itertools
import json
import math
import unittest
from pathlib import Path
from unittest.mock import patch

from solver.budget import Budget
from solver.construct import ConstructionSeed, construct_complete
from solver.geometry import GeometryKernel, PairState
from solver.instance import parse_instance
from solver.entry import OptionalPhaseResult, load_optional_phase, solve
from solver.fallback import build_safe_candidate
from solver.retime import BackendResult, RetimingConfig, RetimingResult, retime
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


class _FeasibleTimeLimitBackend:
    def __init__(self, dates, primary):
        self.dates = tuple(dates)
        self.primary = primary

    def solve(self, request, instance, time_limit, config):
        del request, instance, time_limit, config
        return BackendResult(
            status="TIME_LIMIT",
            dates=self.dates,
            primary=float(self.primary),
            bound=0.0,
            gap=None,
            diagnostics=("solution_count=1",),
        )


class _FirstComponentFailsBackend:
    def __init__(self):
        self.calls = 0

    def solve(self, request, instance, time_limit, config):
        del instance, time_limit, config
        self.calls += 1
        if self.calls == 1:
            return BackendResult(status="NUMERIC", diagnostics=("synthetic first failure",))
        block_id = next(iter(request.free_ids))
        return BackendResult(
            status="OPTIMAL",
            dates=((block_id, 0, 2),),
            primary=0.0,
            bound=0.0,
            gap=0.0,
        )


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

    def test_negative_due_date_has_no_false_tardiness_upper_bound(self):
        raw = instance([block(release=0, due=-10, processing=2)])
        parsed = parse_instance(raw)
        kernel = GeometryKernel.from_instance(parsed)
        snapshot = _objective_snapshot(parsed, (Placement(0, 0, 0, 0, 0, 0, 2),))

        result = retime(snapshot, parsed, kernel, Budget.start(20))

        self.assertNotEqual("INFEASIBLE", result.status, result)
        self.assertEqual(12.0, result.primary)
        self.assertIs(snapshot, result.snapshot)
        _assert_stage_five(self, raw, parsed, kernel, result.snapshot)

    def test_free_blocks_overlap_in_actual_models_without_pair_modes(self):
        raw = instance(
            [block(release=0, due=2, processing=2), block(release=0, due=2, processing=2)],
            bays=((20, 12),),
        )
        parsed = parse_instance(raw)
        kernel = GeometryKernel.from_instance(parsed)
        snapshot = _objective_snapshot(
            parsed,
            (
                Placement(0, 0, 0, 0, 0, 0, 2),
                Placement(1, 0, 0, 10, 0, 2, 4),
            ),
        )
        self.assertIs(PairState.FREE, kernel.relation(*snapshot.placements).state)

        result = retime(snapshot, parsed, kernel, Budget.start(20))

        by_id = {item.block_id: item for item in result.snapshot.placements}
        self.assertEqual((0, 2), (by_id[0].entry, by_id[0].exit))
        self.assertEqual((0, 2), (by_id[1].entry, by_id[1].exit))
        self.assertTrue(any(item == "pairs=0" for item in result.diagnostics), result.diagnostics)
        _assert_stage_five(self, raw, parsed, kernel, result.snapshot)

    def test_k_outer_actual_solve_is_symmetric_with_i_outer(self):
        low = [rectangle(0, 0, 2, 2)]
        low_with_high_cap = [rectangle(0, 0, 2, 2), rectangle(4, 0, 6, 2)]
        raw = instance(
            [
                block(release=0, due=5, processing=4, orientations=[low_with_high_cap]),
                block(release=0, due=5, processing=5, orientations=[low]),
            ],
            bays=((12, 12),),
            weights=(10, 1, 1),
        )
        parsed = parse_instance(raw)
        kernel = GeometryKernel.from_instance(parsed)
        snapshot = _objective_snapshot(
            parsed,
            (
                Placement(0, 0, 0, 0, 0, 5, 9),
                Placement(1, 0, 0, 4, 0, 0, 5),
            ),
        )
        self.assertIs(PairState.K_OUTER, kernel.relation(*snapshot.placements).state)

        result = retime(snapshot, parsed, kernel, Budget.start(20))

        self.assertEqual(0.0, result.snapshot.objective.z1)
        by_id = {item.block_id: item for item in result.snapshot.placements}
        self.assertEqual(5, by_id[0].exit)
        self.assertEqual(5, by_id[1].exit)
        _assert_stage_five(self, raw, parsed, kernel, result.snapshot)

    def test_affected_component_keeps_unchanged_boundary_dates_fixed(self):
        raw = instance(
            [
                block(release=0, due=2, processing=2),
                block(release=0, due=2, processing=2),
                block(release=0, due=2, processing=2),
            ]
        )
        parsed = parse_instance(raw)
        kernel = GeometryKernel.from_instance(parsed)
        snapshot = _objective_snapshot(
            parsed,
            tuple(Placement(i, 0, 0, 0, 0, i * 2, i * 2 + 2) for i in range(3)),
        )

        result = retime(
            snapshot,
            parsed,
            kernel,
            Budget.start(20),
            affected_ids={1},
            config=RetimingConfig(max_free=1),
        )

        before = {item.block_id: (item.entry, item.exit) for item in snapshot.placements}
        after = {item.block_id: (item.entry, item.exit) for item in result.snapshot.placements}
        self.assertEqual(before[0], after[0])
        self.assertEqual(before[2], after[2])
        _assert_stage_five(self, raw, parsed, kernel, result.snapshot)

    def test_time_limit_feasible_incumbent_passes_all_candidate_gates(self):
        raw = instance(
            [block(release=0, due=2, processing=2), block(release=0, due=2, processing=2)]
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
        backend = _FeasibleTimeLimitBackend(((0, 0, 2), (1, 2, 4)), primary=2)

        result = retime(
            snapshot,
            parsed,
            kernel,
            Budget.start(20),
            backend_factory=lambda: backend,
        )

        self.assertEqual("TIME_LIMIT", result.status)
        self.assertEqual(2.0, result.snapshot.objective.z1)
        _assert_stage_five(self, raw, parsed, kernel, result.snapshot)

    def test_one_component_failure_does_not_discard_other_component_improvement(self):
        raw = instance(
            [block(release=0, due=2, processing=2), block(release=0, due=2, processing=2)],
            bays=((20, 12),),
        )
        parsed = parse_instance(raw)
        kernel = GeometryKernel.from_instance(parsed)
        snapshot = _objective_snapshot(
            parsed,
            (
                Placement(0, 0, 0, 0, 0, 5, 7),
                Placement(1, 0, 0, 10, 0, 5, 7),
            ),
        )
        backend = _FirstComponentFailsBackend()

        result = retime(
            snapshot,
            parsed,
            kernel,
            Budget.start(20),
            backend_factory=lambda: backend,
        )

        by_id = {item.block_id: item for item in result.snapshot.placements}
        self.assertEqual((5, 7), (by_id[0].entry, by_id[0].exit))
        self.assertEqual((0, 2), (by_id[1].entry, by_id[1].exit))
        self.assertEqual("PARTIAL", result.status)
        _assert_stage_five(self, raw, parsed, kernel, result.snapshot)

    def test_real_prob4_interlock_witness_retimes_seven_to_zero(self):
        source_path = Path(__file__).resolve().parents[2] / "data/train 2/prob_4.json"
        source = json.loads(source_path.read_text())
        outer = copy.deepcopy(source["blocks"][24])
        inner = copy.deepcopy(source["blocks"][46])
        outer.update(release_time=0, due_date=10, processing_time=10, bay_preferences=[0])
        inner.update(release_time=2, due_date=7, processing_time=5, bay_preferences=[0])
        raw = {
            "name": "real-prob4-interlock-witness",
            "bays": [copy.deepcopy(source["bays"][0])],
            "blocks": [outer, inner],
            "weights": {"w1": 1, "w2": 0, "w3": 0},
        }
        parsed = parse_instance(raw)
        kernel = GeometryKernel.from_instance(parsed)
        conservative = _objective_snapshot(
            parsed,
            (
                Placement(0, 0, 5, 102, 18, 7, 17),
                Placement(1, 0, 1, 115, 3, 2, 7),
            ),
        )
        self.assertIs(PairState.I_OUTER, kernel.relation(*conservative.placements).state)
        self.assertEqual(7.0, conservative.objective.z1)

        result = retime(conservative, parsed, kernel, Budget.start(20))

        self.assertEqual(0.0, result.snapshot.objective.z1)
        checked = _assert_stage_five(self, raw, parsed, kernel, result.snapshot)
        self.assertTrue(math.isclose(0.0, checked["objective"], rel_tol=1e-6, abs_tol=1e-9))

    def test_constructor_output_is_exposed_before_optional_retiming_hook(self):
        raw = load_example()
        parsed = parse_instance(raw)
        fallback = build_safe_candidate(parsed, Budget.start(1))

        phase_result = load_optional_phase()(parsed, fallback, Budget.start(5))

        self.assertEqual(1, len(phase_result.candidates))
        self.assertIsNotNone(phase_result.retimer)
        _assert_stage_five(
            self,
            raw,
            parsed,
            phase_result.precedence_provider,
            phase_result.candidates[0],
        )

    def test_constructor_is_checked_and_installed_before_retimer(self):
        raw = load_example()
        parsed = parse_instance(raw)
        kernel = GeometryKernel.from_instance(parsed)
        constructed = construct_complete(
            parsed,
            kernel,
            ConstructionSeed(None, "slack_due", 20260710),
            Budget.start(10),
        ).snapshot
        events = []

        def recording_checker(problem, operations):
            events.append("checker")
            return checker(problem, operations)

        def recording_retimer(snapshot, instance, geometry, budget):
            del instance, geometry, budget
            events.append("retime")
            return RetimingResult(snapshot, "NO_IMPROVEMENT", None, None, None, 0.0, frozenset(), ())

        def loader():
            return lambda *_: OptionalPhaseResult(
                candidates=(constructed,),
                precedence_provider=kernel,
                retimer=recording_retimer,
            )

        output = solve(raw, 5, checker=recording_checker, optional_phase_loader=loader)

        self.assertEqual(["checker", "checker", "retime"], events)
        self.assertEqual(checker(raw, serialize(constructed, kernel))["objective"], checker(raw, output)["objective"])

    def test_entry_strictly_installs_retimed_candidate_from_validated_constructor(self):
        raw = instance(
            [
                block(release=0, due=100, processing=2),
                block(release=0, due=2, processing=2),
                block(release=0, due=4, processing=2),
            ],
            weights=(1, 0, 0),
        )
        parsed = parse_instance(raw)
        kernel = GeometryKernel.from_instance(parsed)
        constructor = _objective_snapshot(
            parsed,
            (
                Placement(0, 0, 0, 0, 0, 2, 4),
                Placement(1, 0, 0, 0, 0, 0, 2),
                Placement(2, 0, 0, 0, 0, 4, 7),
            ),
        )
        self.assertEqual(3.0, constructor.objective.total)
        calls = 0

        def recording_checker(problem, operations):
            nonlocal calls
            calls += 1
            return checker(problem, operations)

        def loader():
            return lambda *_: OptionalPhaseResult(
                candidates=(constructor,),
                precedence_provider=kernel,
                retimer=retime,
            )

        output = solve(raw, 20, checker=recording_checker, optional_phase_loader=loader)
        checked = checker(raw, output)

        self.assertEqual(2, calls)
        self.assertEqual(5, checked["stage"])
        self.assertEqual(0.0, checked["objective"])

    def test_public_entry_preserves_constructor_on_unexpected_retimer_exception(self):
        raw = load_example()
        parsed = parse_instance(raw)
        kernel = GeometryKernel.from_instance(parsed)
        constructed = construct_complete(
            parsed,
            kernel,
            ConstructionSeed(None, "slack_due", 20260710),
            Budget.start(10),
        ).snapshot
        expected_operations = serialize(constructed, kernel)

        def crash(*_):
            raise RuntimeError("synthetic retimer crash")

        def loader():
            return lambda *_: OptionalPhaseResult(
                candidates=(constructed,),
                precedence_provider=kernel,
                retimer=crash,
            )

        output = solve(raw, 5, optional_phase_loader=loader)

        checked = checker(raw, output)
        self.assertTrue(checked["feasible"], checked)
        self.assertEqual(5, checked["stage"])
        self.assertEqual(expected_operations, output)
        self.assertEqual(compute_objective(parsed, constructed).total, checked["objective"])

    def test_actual_optional_phase_retimer_crash_preserves_constructor_result(self):
        raw = load_example()
        expected = solve(raw, 5)

        with patch("solver.retime.retime", side_effect=RuntimeError("synthetic retimer crash")):
            output = solve(raw, 5)

        self.assertEqual(expected, output)
        self.assertEqual(5, checker(raw, output)["stage"])

    def test_public_entry_preserves_constructor_failure_matrix(self):
        raw = load_example()
        parsed = parse_instance(raw)
        kernel = GeometryKernel.from_instance(parsed)
        constructed = construct_complete(
            parsed,
            kernel,
            ConstructionSeed(None, "slack_due", 20260710),
            Budget.start(10),
        ).snapshot
        expected_operations = serialize(constructed, kernel)

        for label in ("import", "license", "model", "numeric"):
            with self.subTest(label=label):
                def crash(*_, failure=label):
                    raise RuntimeError(f"synthetic {failure} failure")

                def loader():
                    return lambda *_: OptionalPhaseResult(
                        candidates=(constructed,),
                        precedence_provider=kernel,
                        retimer=crash,
                    )

                output = solve(raw, 5, optional_phase_loader=loader)
                self.assertEqual(expected_operations, output)
                checked = checker(raw, output)
                self.assertEqual(5, checked["stage"])
                self.assertEqual(constructed.objective.total, checked["objective"])


if __name__ == "__main__":
    unittest.main()
