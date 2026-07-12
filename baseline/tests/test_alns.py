"""S3 transactional intra-bay destroy/repair contracts."""

from __future__ import annotations

from copy import deepcopy
import hashlib
import json
import unittest
from unittest.mock import patch

from numpy.random import Generator, PCG64

from solver.alns import run_transactional_iteration
from solver import alns
from solver.budget import Budget, BudgetExpired
from solver.checker_adapter import official_check
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


if __name__ == "__main__":
    unittest.main()
