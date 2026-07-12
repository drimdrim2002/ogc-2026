from __future__ import annotations

import copy
import json
import math
import unittest

from myalgorithm import algorithm
from solver.budget import Budget
from solver.entry import solve
from solver.fallback import build_safe_candidate
from solver.instance import NoValidPlacement, parse_instance
from solver.serialize import serialize
from solver.state import (
    CandidateDraft,
    IncumbentStore,
    ObjectiveParts,
    Placement,
    SolutionSnapshot,
    compute_objective,
)

from tests.helpers import block, checker, instance, load_example, rectangle


class FakeClock:
    def __init__(self, now=100.0):
        self.now = now

    def __call__(self):
        return self.now

    def advance(self, seconds):
        self.now += seconds


class FoundationTests(unittest.TestCase):
    def test_negative_aabb_integer_range(self):
        prob = instance([
            block(orientations=[[[[0, 0], [-3.49, 1], [7.62, 1], [7.62, 0]]]])
        ], bays=((12, 5),))
        parsed = parse_instance(prob)
        orientation = parsed.blocks[0].orientations[0]
        x_range = orientation.integer_range(parsed.bays[0]).x
        self.assertEqual((4, 4), (x_range.lower, x_range.upper))
        self.assertEqual((4, 0), orientation.integer_range(parsed.bays[0]).anchor)

    def test_fractional_only_fit_rejected(self):
        prob = instance([
            block(orientations=[[[[0, 0], [-3.01, 1], [8.99, 1], [8.99, 0]]]])
        ], bays=((12, 5),))
        with self.assertRaises(NoValidPlacement):
            parse_instance(prob)

    def test_input_is_immutable(self):
        prob = instance([block()])
        parsed = parse_instance(prob)
        frozen_vertex = parsed.blocks[0].orientations[0].raw_layers[0][0]
        prob["blocks"][0]["shape"][0]["layers"][0][0][0] = 999
        prob["bays"][0]["width"] = 999
        self.assertEqual((0.0, 0.0), frozen_vertex)
        self.assertEqual(12, parsed.bays[0].width)
        with self.assertRaises(TypeError):
            parsed.raw["name"] = "changed"

    def test_zero_processing_uses_one_day(self):
        prob = instance([block(release=4, due=4, processing=0)])
        parsed = parse_instance(prob)
        snapshot = build_safe_candidate(parsed, Budget.start(1.0))
        placement = snapshot.placements[0]
        self.assertEqual((4, 5), (placement.entry, placement.exit))
        result = checker(prob, serialize(snapshot))
        self.assertTrue(result["feasible"], result)
        self.assertEqual(5, result["stage"])

    def test_budget_fake_clock(self):
        clock = FakeClock()
        budget = Budget.start(10.0, clock=clock)
        budget.record_checker_duration(0.5)
        budget.record_checker_duration(1.0)
        budget.record_checker_duration(1.5)
        self.assertAlmostEqual(2.5, budget.reserve)
        self.assertAlmostEqual(10.0, budget.remaining())
        self.assertAlmostEqual(7.5, budget.search_remaining())
        self.assertTrue(budget.can_start(7.0, margin=0.5))
        self.assertFalse(budget.can_start(7.1, margin=0.5))
        clock.advance(2.0)
        self.assertAlmostEqual(8.0, budget.remaining())
        child = budget.child(3.0)
        self.assertAlmostEqual(3.0, child.remaining())
        clock.advance(1.0)
        self.assertAlmostEqual(2.0, child.remaining())

    def test_objective_float_z2(self):
        prob = instance(
            [
                block(workload=1.25, preferences=(10, 2)),
                block(workload=2.75, preferences=(1, 10)),
            ],
            bays=((10, 10), (20, 10)),
            weights=(2.0, 3.0, 5.0),
        )
        parsed = parse_instance(prob)
        snapshot = SolutionSnapshot(
            (
                Placement(0, 0, 0, 0, 0, 0, 2),
                Placement(1, 1, 0, 0, 0, 0, 2),
            )
        )
        objective = compute_objective(parsed, snapshot)
        result = checker(prob, serialize(snapshot))
        self.assertTrue(result["feasible"], result)
        self.assertTrue(math.isclose(objective.z2, result["obj2"], rel_tol=1e-6, abs_tol=1e-9))
        self.assertTrue(math.isclose(objective.total, result["objective"], rel_tol=1e-6, abs_tol=1e-9))

    def test_draft_rollback(self):
        original = SolutionSnapshot((Placement(0, 0, 0, 0, 0, 0, 2),))
        original_bytes = json.dumps(serialize(original), sort_keys=False)
        draft = CandidateDraft.from_snapshot(original)
        draft.placements[0] = Placement(0, 0, 0, 5, 5, 3, 6)
        self.assertEqual(original_bytes, json.dumps(serialize(original), sort_keys=False))
        self.assertNotEqual(original, draft.freeze())

    def test_strict_install_and_defensive_operations(self):
        prob = instance([block(due=0)], weights=(1, 1, 1))
        parsed = parse_instance(prob)
        first = SolutionSnapshot((Placement(0, 0, 0, 0, 0, 0, 4),))
        first = first.with_objective(compute_objective(parsed, first))
        better = SolutionSnapshot((Placement(0, 0, 0, 0, 0, 0, 2),))
        better = better.with_objective(compute_objective(parsed, better))
        store = IncumbentStore(parsed)
        first_ops = serialize(first)
        first_check = checker(prob, first_ops)
        self.assertTrue(store.install_if_valid(first, first_ops, first_check))
        self.assertFalse(store.install_if_valid(first, first_ops, first_check))
        self.assertTrue(store.install_if_valid(better, serialize(better), checker(prob, serialize(better))))
        exposed = store.operations
        exposed["operations"].clear()
        self.assertTrue(store.operations["operations"])

        mismatch = copy.deepcopy(checker(prob, serialize(first)))
        mismatch["objective"] += 1.0
        empty = IncumbentStore(parsed)
        self.assertFalse(empty.install_if_valid(first, first_ops, mismatch))

    def test_fallback_is_parallel_serial_and_objective_matches(self):
        prob = load_example()
        parsed = parse_instance(prob)
        snapshot = build_safe_candidate(parsed, Budget.start(2.0))
        result = checker(prob, serialize(snapshot))
        objective = compute_objective(parsed, snapshot)
        self.assertTrue(result["feasible"], result)
        self.assertEqual(5, result["stage"])
        self.assertEqual(len(prob["blocks"]), len(snapshot.placements))
        for left, placement in enumerate(snapshot.placements):
            for other in snapshot.placements[left + 1:]:
                if placement.bay_id == other.bay_id:
                    self.assertTrue(placement.exit <= other.entry or other.exit <= placement.entry)
        self.assertTrue(math.isclose(objective.total, result["objective"], rel_tol=1e-6, abs_tol=1e-9))

    def test_public_entry_safe_before_optional_import(self):
        prob = load_example()
        events = []

        def recording_checker(raw, operations):
            events.append("check")
            return checker(raw, operations)

        def loader():
            events.append("import")
            return lambda *args: ()

        result = solve(prob, 1.0, checker=recording_checker, optional_phase_loader=loader)
        checked = checker(prob, result)
        self.assertTrue(checked["feasible"], checked)
        self.assertEqual(["check"], events)

    def test_public_algorithm_returns_stage_five_incumbent(self):
        prob = load_example()
        result = algorithm(prob, 1.0)
        checked = checker(prob, result)
        self.assertTrue(checked["feasible"], checked)
        self.assertEqual(5, checked["stage"])

    def test_optional_import_failure_returns_incumbent(self):
        prob = load_example()
        events = []
        fallback_bytes = []

        def recording_checker(raw, operations):
            events.append("check")
            fallback_bytes.append(json.dumps(operations, separators=(",", ":")))
            return checker(raw, operations)

        def loader():
            events.append("import")
            raise ImportError("optional optimizer unavailable")

        result = solve(prob, 5.0, checker=recording_checker, optional_phase_loader=loader)
        self.assertEqual(["check", "import"], events)
        self.assertEqual(fallback_bytes[0], json.dumps(result, separators=(",", ":")))
        self.assertTrue(checker(prob, result)["feasible"])

    def test_optional_mutation_cannot_corrupt_best(self):
        prob = load_example()
        checked_operations = []

        def recording_checker(raw, operations):
            checked_operations.append(json.dumps(operations, separators=(",", ":")))
            return checker(raw, operations)

        def loader():
            def phase(instance, snapshot, budget):
                draft = CandidateDraft.from_snapshot(snapshot)
                draft.placements[0] = Placement(0, 0, 0, 999, 999, 0, 1)
                raise RuntimeError("candidate failed")
            return phase

        result = solve(prob, 5.0, checker=recording_checker, optional_phase_loader=loader)
        self.assertEqual(checked_operations[0], json.dumps(result, separators=(",", ":")))
        self.assertTrue(checker(prob, result)["feasible"])

    def test_optional_candidates_install_only_strict_improvement(self):
        prob = instance([block(due=0)], weights=(1, 1, 1))

        def loader():
            def phase(parsed, snapshot, budget):
                current = snapshot.placements[0]
                yield SolutionSnapshot((Placement(0, 0, 0, current.x, current.y, 0, 2),))
            return phase

        result = solve(prob, 5.0, checker=checker, optional_phase_loader=loader)
        checked = checker(prob, result)
        self.assertTrue(checked["feasible"], checked)
        self.assertEqual(2.0, checked["objective"])

    def test_no_valid_placement_raises_diagnostic(self):
        prob = instance([block(orientations=[[[[0, 0], [20, 0], [20, 20], [0, 20]]]])])
        with self.assertRaisesRegex(NoValidPlacement, "block 0"):
            solve(prob, 1.0, checker=checker)


if __name__ == "__main__":
    unittest.main()
