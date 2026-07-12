"""S3 transactional intra-bay destroy/repair contracts."""

from __future__ import annotations

from contextlib import nullcontext
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import unittest
from unittest.mock import patch

from numpy.random import Generator, PCG64

from solver.alns import run_transactional_iteration
from solver import alns
from solver.assign import AssignmentV1
from solver.budget import Budget, BudgetExpired
from solver.checker_adapter import official_check
from solver.construct import construct_profile
from solver.incumbent import VerifiedIncumbent
from solver.instance import ProblemInstance
from solver.serialize import serialize_non_interlock
from solver.state import Placement, SolutionState
from tests.fixtures import TWO_SQUARE, block, instance


class TransactionTests(unittest.TestCase):
    def _fixture(self):
        prob_info = instance(
            [
                block(layers=(TWO_SQUARE,), due=1, processing=1, preferences=(100, 0)),
                block(layers=(TWO_SQUARE,), due=2, processing=1, preferences=(100, 0)),
                block(layers=(TWO_SQUARE,), due=20, processing=1, preferences=(0, 100)),
                block(layers=(TWO_SQUARE,), due=20, processing=1, preferences=(0, 100)),
            ],
            bays=((2, 2), (2, 2)),
            weights={"w1": 1.0, "w2": 1.0, "w3": 1.0},
        )
        parsed = ProblemInstance.parse(prob_info)
        state = SolutionState(parsed)
        for item in (
            Placement(0, 0, 0, 0, 0, 10, 11),
            Placement(1, 0, 0, 0, 0, 11, 12),
            Placement(2, 1, 0, 0, 0, 0, 1),
            Placement(3, 1, 0, 0, 0, 1, 2),
        ):
            state.place(item)
        incumbent = VerifiedIncumbent(parsed)
        incumbent.register_initial(state)
        return prob_info, state, incumbent

    @staticmethod
    def _solution_sha(solution):
        payload = json.dumps(solution, sort_keys=True, separators=(",", ":")).encode()
        return hashlib.sha256(payload).hexdigest()

    def _assert_restored(self, prob_info, state, before):
        self.assertEqual(before, state.capture_undo_token())
        checked = official_check(
            prob_info, serialize_non_interlock(state.placements.values())
        )
        self.assertTrue(checked.feasible, checked.violations)
        self.assertEqual(5, checked.stage)

    def test_undo_all_outcomes(self):
        # An accepted D1/R3 candidate is complete, checker-feasible, and cannot
        # alter assignment-derived bay membership, loads, Z2, or Z3.
        prob_info, state, incumbent = self._fixture()
        before = state.capture_undo_token()
        incumbent_sha = self._solution_sha(incumbent.solution)
        accepted = run_transactional_iteration(
            state,
            Generator(PCG64(20260710)),
            remove_count=2,
            accept=lambda _before, _after: True,
        )
        self.assertTrue(accepted.committed)
        self.assertEqual(before.bay_members, state.bay_members)
        self.assertEqual(before.bay_loads, state.objective_diagnostics.bay_loads)
        self.assertEqual(before.z3, state.z3)
        self.assertEqual(before.z2, state.z2)
        checked = official_check(
            prob_info, serialize_non_interlock(state.placements.values())
        )
        self.assertTrue(checked.feasible, checked.violations)
        self.assertEqual(5, checked.stage)
        self.assertEqual(incumbent_sha, self._solution_sha(incumbent.solution))

        # Rejection restores both state and the exact PCG64 checkpoint.
        prob_info, state, incumbent = self._fixture()
        before = state.capture_undo_token()
        rng = Generator(PCG64(20260710))
        rng_before = deepcopy(rng.bit_generator.state)
        incumbent_sha = self._solution_sha(incumbent.solution)
        rejected = run_transactional_iteration(
            state,
            rng,
            remove_count=2,
            accept=lambda _before, _after: False,
        )
        self.assertFalse(rejected.committed)
        self.assertEqual("rejected", rejected.reason)
        self._assert_restored(prob_info, state, before)
        self.assertEqual(rng_before, rng.bit_generator.state)
        self.assertEqual(incumbent_sha, self._solution_sha(incumbent.solution))

        # A repair failure is all-or-nothing.
        prob_info, state, incumbent = self._fixture()
        before = state.capture_undo_token()
        rng = Generator(PCG64(20260710))
        rng_before = deepcopy(rng.bit_generator.state)
        incumbent_sha = self._solution_sha(incumbent.solution)
        with patch("solver.alns._repair_r3", return_value=False):
            failed = run_transactional_iteration(
                state,
                rng,
                remove_count=2,
                accept=lambda _before, _after: True,
            )
        self.assertFalse(failed.committed)
        self.assertEqual("repair_failed", failed.reason)
        self._assert_restored(prob_info, state, before)
        self.assertEqual(rng_before, rng.bit_generator.state)
        self.assertEqual(incumbent_sha, self._solution_sha(incumbent.solution))

        # Every removal/insertion mutation boundary rolls back on exception.
        for fault_after in range(4):
            prob_info, state, incumbent = self._fixture()
            before = state.capture_undo_token()
            rng = Generator(PCG64(20260710))
            rng_before = deepcopy(rng.bit_generator.state)
            incumbent_sha = self._solution_sha(incumbent.solution)

            def inject(_event, mutation_index):
                if mutation_index == fault_after:
                    raise RuntimeError(f"injected mutation {fault_after}")

            with self.assertRaisesRegex(RuntimeError, f"injected mutation {fault_after}"):
                run_transactional_iteration(
                    state,
                    rng,
                    remove_count=2,
                    accept=lambda _before, _after: True,
                    on_mutation=inject,
                )
            self._assert_restored(prob_info, state, before)
            self.assertEqual(rng_before, rng.bit_generator.state)
            self.assertEqual(incumbent_sha, self._solution_sha(incumbent.solution))

        # An exception at the decision boundary cannot escape a candidate.
        prob_info, state, incumbent = self._fixture()
        before = state.capture_undo_token()
        rng = Generator(PCG64(20260710))
        rng_before = deepcopy(rng.bit_generator.state)
        incumbent_sha = self._solution_sha(incumbent.solution)

        def fail_accept(_before, _after):
            raise RuntimeError("injected acceptance failure")

        with self.assertRaisesRegex(RuntimeError, "injected acceptance failure"):
            run_transactional_iteration(
                state,
                rng,
                remove_count=2,
                accept=fail_accept,
            )
        self._assert_restored(prob_info, state, before)
        self.assertEqual(rng_before, rng.bit_generator.state)
        self.assertEqual(incumbent_sha, self._solution_sha(incumbent.solution))

        # A deadline observed after the first mutation also restores exactly.
        prob_info, state, incumbent = self._fixture()
        before = state.capture_undo_token()
        rng = Generator(PCG64(20260710))
        rng_before = deepcopy(rng.bit_generator.state)
        incumbent_sha = self._solution_sha(incumbent.solution)
        now = [0.0]
        budget = Budget(1.0, reserve=0.0, clock=lambda: now[0])

        def expire_after_first(_event, mutation_index):
            if mutation_index == 0:
                now[0] = 2.0

        with self.assertRaises(BudgetExpired):
            run_transactional_iteration(
                state,
                rng,
                remove_count=2,
                accept=lambda _before, _after: True,
                budget=budget,
                on_mutation=expire_after_first,
            )
        self._assert_restored(prob_info, state, before)
        self.assertEqual(rng_before, rng.bit_generator.state)
        self.assertTrue(official_check(prob_info, incumbent.solution).feasible)
        self.assertEqual(incumbent_sha, self._solution_sha(incumbent.solution))


class OperatorTests(unittest.TestCase):
    def _fixture(self):
        prob_info = instance(
            [
                block(
                    layers=(TWO_SQUARE,),
                    due=0,
                    processing=1,
                    preferences=(100, 0) if block_id < 6 else (0, 100),
                )
                for block_id in range(12)
            ],
            bays=((2, 2), (2, 2)),
            weights={"w1": 1.0, "w2": 1.0, "w3": 1.0},
        )
        state = SolutionState(ProblemInstance.parse(prob_info))
        for block_id in range(12):
            bay_id = 0 if block_id < 6 else 1
            entry = block_id if bay_id == 0 else block_id - 6
            state.place(Placement(block_id, bay_id, 0, 0, 0, entry, entry + 1))
        state.assert_invariants()
        return prob_info, state

    def _assert_assignment_preserved(self, prob_info, state, before):
        self.assertEqual(before.bay_members, state.bay_members)
        self.assertEqual(before.bay_loads, state.objective_diagnostics.bay_loads)
        self.assertEqual(before.z2, state.z2)
        self.assertEqual(before.z3, state.z3)
        checked = official_check(
            prob_info, serialize_non_interlock(state.placements.values())
        )
        self.assertTrue(checked.feasible, checked.violations)
        self.assertEqual(5, checked.stage)

    def test_all_operators_preserve_assignment(self):
        registry = alns.OperatorRegistry()
        self.assertEqual(("d1", "d2", "d3", "d4", "d5"), registry.destroy_names)
        self.assertEqual(("r1", "r2", "r3"), registry.repair_names)

        # Every destroy operator succeeds with the safe EDD repair, removes
        # from exactly one bay, and returns every block to its original bay.
        for destroy_name in registry.destroy_names:
            prob_info, state = self._fixture()
            before = state.capture_undo_token()
            result = registry.attempt(
                state,
                Generator(PCG64(20260710)),
                destroy_name=destroy_name,
                repair_name="r3",
                remove_count=2,
                commit=True,
            )
            self.assertTrue(result.committed, (destroy_name, result.reason))
            self.assertEqual(2, len(result.removed_block_ids))
            original = {item.block_id: item for item in before.placements}
            self.assertEqual(
                1,
                len({original[block_id].bay_id for block_id in result.removed_block_ids}),
            )
            if destroy_name == "d3":
                self.assertEqual((5, 4), result.removed_block_ids)
            self._assert_assignment_preserved(prob_info, state, before)

        # Each repair uses the same transaction/original-bay contract.
        for repair_name in registry.repair_names:
            prob_info, state = self._fixture()
            before = state.capture_undo_token()
            result = registry.attempt(
                state,
                Generator(PCG64(20260710)),
                destroy_name="d1",
                repair_name=repair_name,
                remove_count=2,
                commit=True,
            )
            self.assertTrue(result.committed, (repair_name, result.reason))
            self._assert_assignment_preserved(prob_info, state, before)

        # Rejection is rollback-neutral and is reported as a failed attempt.
        prob_info, state = self._fixture()
        before = state.capture_undo_token()
        rng = Generator(PCG64(20260710))
        rng_before = deepcopy(rng.bit_generator.state)
        rejected = registry.attempt(
            state,
            rng,
            destroy_name="d2",
            repair_name="r1",
            remove_count=2,
            commit=False,
        )
        self.assertFalse(rejected.committed)
        self.assertEqual("rejected", rejected.reason)
        self.assertEqual(before, state.capture_undo_token())
        self.assertEqual(rng_before, rng.bit_generator.state)
        self._assert_assignment_preserved(prob_info, state, before)
        self.assertGreater(registry.metrics["d2"].failures, 0)
        self.assertGreater(registry.metrics["r1"].failures, 0)

        # A fault after the first partial reinsertion of every repair restores
        # the exact state and RNG checkpoint before escaping.
        for repair_name in registry.repair_names:
            prob_info, state = self._fixture()
            before = state.capture_undo_token()
            rng = Generator(PCG64(20260710))
            rng_before = deepcopy(rng.bit_generator.state)

            def fail_after_first_insert(_event, mutation_index):
                if mutation_index == 2:
                    raise RuntimeError(f"injected {repair_name} failure")

            with self.assertRaisesRegex(
                RuntimeError, f"injected {repair_name} failure"
            ):
                registry.attempt(
                    state,
                    rng,
                    destroy_name="d1",
                    repair_name=repair_name,
                    remove_count=2,
                    commit=True,
                    on_mutation=fail_after_first_insert,
                )
            self.assertEqual(before, state.capture_undo_token())
            self.assertEqual(rng_before, rng.bit_generator.state)
            self._assert_assignment_preserved(prob_info, state, before)

        sampled = alns.sample_destroy_count(
            300, Generator(PCG64(20260710))
        )
        self.assertGreaterEqual(sampled, 6)
        self.assertLessEqual(sampled, 18)
        self.assertLessEqual(sampled, 45)

        for name in registry.destroy_names + registry.repair_names:
            metrics = registry.metrics[name]
            self.assertGreater(metrics.attempts, 0, name)
            self.assertGreater(metrics.successes, 0, name)

        # Seeded selection is replay-stable.
        first_prob, first_state = self._fixture()
        second_prob, second_state = self._fixture()
        first_before = first_state.capture_undo_token()
        second_before = second_state.capture_undo_token()
        first = alns.OperatorRegistry().attempt(
            first_state,
            Generator(PCG64(20260710)),
            destroy_name="d5",
            repair_name="r2",
            remove_count=2,
            commit=True,
        )
        second = alns.OperatorRegistry().attempt(
            second_state,
            Generator(PCG64(20260710)),
            destroy_name="d5",
            repair_name="r2",
            remove_count=2,
            commit=True,
        )
        self.assertEqual(first.removed_block_ids, second.removed_block_ids)
        self.assertEqual(
            serialize_non_interlock(first_state.placements.values()),
            serialize_non_interlock(second_state.placements.values()),
        )
        self._assert_assignment_preserved(first_prob, first_state, first_before)
        self._assert_assignment_preserved(second_prob, second_state, second_before)

        class AssignmentChangingDestroy(alns.DestroyOperator):
            name = "d6"
            changes_assignment = True

            def select(self, state, rng, count):  # pragma: no cover - rejected
                return ()

        with self.assertRaisesRegex(ValueError, "changes_assignment"):
            alns.OperatorRegistry(
                destroys=(AssignmentChangingDestroy(),),
                repairs=tuple(registry.repairs.values()),
            )


class AcceptanceTests(unittest.TestCase):
    @staticmethod
    def _single_block_fixture(*, current_exit=10, incumbent_exit=None):
        prob_info = instance(
            [block(layers=(TWO_SQUARE,), due=0, processing=1, preferences=(0,))],
            bays=((2, 2),),
            weights={"w1": 1.0, "w2": 0.0, "w3": 0.0},
        )
        parsed = ProblemInstance.parse(prob_info)
        state = SolutionState(parsed)
        state.place(
            Placement(0, 0, 0, 0, 0, current_exit - 1, current_exit)
        )
        incumbent = VerifiedIncumbent(parsed)
        incumbent_state = state
        if incumbent_exit is not None:
            incumbent_state = SolutionState(parsed)
            incumbent_state.place(
                Placement(0, 0, 0, 0, 0, incumbent_exit - 1, incumbent_exit)
            )
        incumbent.register_initial(incumbent_state)
        return prob_info, state, incumbent

    @staticmethod
    def _scripted_registry(exits):
        class SingleBlockDestroy(alns.DestroyOperator):
            name = "single"

            def select(self, state, rng, count):
                return (0,) if count == 1 else ()

        class ScriptedRepair(alns.RepairOperator):
            name = "scripted"

            def __init__(self):
                self.exits = iter(exits)

            def repair(self, transaction, removed, *, budget=None):
                if budget is not None:
                    budget.checkpoint("S3 scripted repair")
                original = removed[0]
                new_exit = next(self.exits)
                transaction.state.place(
                    Placement(
                        original.block_id,
                        original.bay_id,
                        original.x,
                        original.y,
                        original.orient_idx,
                        new_exit - 1,
                        new_exit,
                    )
                )
                transaction.record_mutation(f"insert:{original.block_id}")
                return True

        return alns.OperatorRegistry(
            destroys=(SingleBlockDestroy(),),
            repairs=(ScriptedRepair(),),
        )

    def test_improvement_classified_before_cur_obj_update(self):
        prob_info, state, incumbent = self._single_block_fixture()
        events: list[alns.ALNSIterationEvent] = []
        result = alns.run_alns(
            state,
            incumbent,
            Generator(PCG64(20260710)),
            max_iterations=4,
            registry=self._scripted_registry((8, 8, 9, 7)),
            remove_count=1,
            on_event=events.append,
        )

        self.assertEqual(
            ("improving", "current_equal", "worse", "improving"),
            tuple(event.outcome for event in events),
        )
        self.assertEqual(
            (True, False, False, True),
            tuple(event.accepted for event in events),
        )
        self.assertEqual(2, result.metrics.accepted)
        self.assertEqual(2, result.metrics.improving)
        self.assertEqual(1, result.metrics.current_equal)
        self.assertEqual(1, result.metrics.worse)
        self.assertEqual(7.0, state.objective)
        self.assertEqual(
            (10.0, 8.0, 7.0),
            tuple(item.objective for item in result.incumbent_trace),
        )
        self.assertTrue(
            all(
                right.objective < left.objective
                for left, right in zip(
                    result.incumbent_trace,
                    result.incumbent_trace[1:],
                )
            )
        )
        checked = official_check(prob_info, result.solution)
        self.assertTrue(checked.feasible, checked.violations)
        self.assertEqual(7.0, checked.objective)

    def test_seeded_100_iterations_keep_monotonic_verified_trace(self):
        synthetic_prob = instance(
            [
                block(
                    layers=(TWO_SQUARE,),
                    due=0,
                    processing=1,
                    preferences=(0,),
                )
                for _ in range(12)
            ],
            bays=((2, 2),),
            weights={"w1": 1.0, "w2": 0.0, "w3": 0.0},
        )
        synthetic_state = SolutionState(ProblemInstance.parse(synthetic_prob))
        for block_id in range(12):
            entry = 20 + block_id
            synthetic_state.place(
                Placement(block_id, 0, 0, 0, 0, entry, entry + 1)
            )

        example_path = (
            Path(__file__).resolve().parents[2]
            / "alg_tester"
            / "example"
            / "example_B2_b10.json"
        )
        example_prob = json.loads(example_path.read_text(encoding="utf-8"))
        example_instance = ProblemInstance.parse(example_prob)
        example_state = construct_profile(
            example_instance,
            AssignmentV1(example_instance).assign(),
            "PF3",
        ).state

        accepted_over_runs = 0
        for prob_info, state in (
            (synthetic_prob, synthetic_state),
            (example_prob, example_state),
        ):
            before = state.capture_undo_token()
            incumbent = VerifiedIncumbent(state.instance)
            incumbent.register_initial(state)
            result = alns.run_alns(
                state,
                incumbent,
                Generator(PCG64(20260710)),
                max_iterations=100,
                remove_count=2,
                safety_sample_interval=10,
            )
            self.assertEqual("max_iterations", result.stopped_reason)
            self.assertEqual(100, result.metrics.iterations)
            self.assertGreater(result.metrics.proposals, 0)
            accepted_over_runs += result.metrics.accepted
            self.assertTrue(
                all(item.checker_stage == 5 for item in result.incumbent_trace)
            )
            self.assertTrue(
                all(
                    right.objective < left.objective
                    for left, right in zip(
                        result.incumbent_trace,
                        result.incumbent_trace[1:],
                    )
                )
            )
            checked = official_check(prob_info, result.solution)
            self.assertTrue(checked.feasible, checked.violations)
            self.assertEqual(result.incumbent_trace[-1].objective, checked.objective)
            self.assertEqual(before.bay_members, state.bay_members)
            self.assertEqual(before.bay_loads, state.objective_diagnostics.bay_loads)
            self.assertEqual(before.z2, state.z2)
            self.assertEqual(before.z3, state.z3)
        self.assertGreater(accepted_over_runs, 0)

    def test_safety_sample_keeps_better_incumbent_separate(self):
        prob_info, state, incumbent = self._single_block_fixture(
            current_exit=10,
            incumbent_exit=5,
        )
        result = alns.run_alns(
            state,
            incumbent,
            Generator(PCG64(20260710)),
            max_iterations=1,
            registry=self._scripted_registry((8,)),
            remove_count=1,
            safety_sample_interval=1,
        )
        self.assertEqual(8.0, state.objective)
        self.assertEqual(1, result.metrics.accepted)
        self.assertEqual(1, result.metrics.full_checks)
        self.assertEqual(1, result.metrics.safety_samples)
        self.assertEqual(
            (5.0,),
            tuple(item.objective for item in result.incumbent_trace),
        )
        checked = official_check(prob_info, result.solution)
        self.assertTrue(checked.feasible, checked.violations)
        self.assertEqual(5.0, checked.objective)

    def test_faults_and_deadlines_return_stored_incumbent(self):
        def solution_sha(solution):
            payload = json.dumps(
                solution, sort_keys=True, separators=(",", ":")
            ).encode()
            return hashlib.sha256(payload).hexdigest()

        class FaultyStrict(alns.StrictAcceptor):
            def accept(self, previous_cur_obj, new_obj):
                raise RuntimeError("injected accept fault")

        fault_cases = (
            {
                "acceptor": FaultyStrict(),
                "expected": "injected accept fault",
            },
            {
                "patch_target": "solver.incumbent.official_check",
                "expected": "injected full-check fault",
            },
            {
                "on_event": lambda _event: (_ for _ in ()).throw(
                    RuntimeError("injected report fault")
                ),
                "expected": "injected report fault",
            },
        )
        for case in fault_cases:
            prob_info, state, incumbent = self._single_block_fixture()
            before = state.capture_undo_token()
            before_sha = solution_sha(incumbent.solution)
            kwargs = {
                "acceptor": case.get("acceptor"),
                "on_event": case.get("on_event"),
            }
            if case.get("patch_target"):
                context = patch(
                    case["patch_target"],
                    side_effect=RuntimeError("injected full-check fault"),
                )
            else:
                context = nullcontext()
            with context:
                result = alns.run_alns(
                    state,
                    incumbent,
                    Generator(PCG64(20260710)),
                    max_iterations=1,
                    registry=self._scripted_registry((8,)),
                    remove_count=1,
                    **kwargs,
                )
            self.assertIn(case["expected"], result.stopped_reason)
            self.assertEqual(before, state.capture_undo_token())
            self.assertEqual(before_sha, solution_sha(result.solution))
            self.assertTrue(official_check(prob_info, result.solution).feasible)

        boundaries = (
            "S3 iteration start",
            "S3 SINGLE removal",
            "S3 scripted repair",
            "S3 candidate repaired",
            "S3 acceptance reported",
            "S3 before full check",
            "S3 before candidate commit",
            "S3 after candidate commit",
        )

        class BoundaryBudget:
            def __init__(self, target):
                self.target = target

            def checkpoint(self, label):
                if label == self.target:
                    raise BudgetExpired(f"injected deadline at {label}")

        for boundary in boundaries:
            prob_info, state, incumbent = self._single_block_fixture()
            result = alns.run_alns(
                state,
                incumbent,
                Generator(PCG64(20260710)),
                max_iterations=1,
                registry=self._scripted_registry((8,)),
                remove_count=1,
                budget=BoundaryBudget(boundary),
            )
            self.assertEqual("deadline", result.stopped_reason, boundary)
            self.assertEqual(1, result.metrics.deadlines, boundary)
            checked = official_check(prob_info, result.solution)
            self.assertTrue(checked.feasible, (boundary, checked.violations))
            self.assertEqual(result.incumbent_trace[-1].objective, checked.objective)


if __name__ == "__main__":
    unittest.main()
