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


class ControlTests(unittest.TestCase):
    def test_retime_stall_and_acceptor_transitions(self):
        trigger = alns.RetimeTrigger(
            min_dirty=5,
            dirty_fraction=0.05,
            min_interval_fraction=0.03,
        )
        self.assertEqual(5, trigger.threshold(40))
        for _ in range(5):
            trigger.record_spatial_accept(0)
        self.assertFalse(
            trigger.should_retime(0, bay_size=40, elapsed=2.99, timelimit=100.0)
        )
        self.assertTrue(
            trigger.should_retime(0, bay_size=40, elapsed=3.0, timelimit=100.0)
        )
        trigger.record_retime(0, elapsed=3.0, wall_seconds=0.25, improved=False)
        self.assertEqual(0, trigger.dirty_count(0))
        self.assertEqual(0.25, trigger.retime_wall_seconds)

        rrt = alns.RRT_Acceptor(initial_deviation=0.03)
        rrt.begin_iteration(progress=0.0, incumbent_obj=100.0)
        self.assertTrue(rrt.accept(100.0, 102.0))
        rrt.begin_iteration(progress=1.0, incumbent_obj=100.0)
        self.assertFalse(rrt.accept(100.0, 100.01))

        sa = alns.SAAcceptor(
            Generator(PCG64(20260710)),
            target_worse_acceptance=0.5,
        )
        sa.calibrate((1.0, 2.0, 4.0))
        sa.begin_iteration(progress=0.5, incumbent_obj=100.0)
        self.assertGreater(sa.temperature, 0.0)
        cooled_temperature = sa.temperature
        sa.reheat(2.0)
        self.assertGreater(sa.temperature, cooled_temperature)
        reheated_temperature = sa.temperature
        sa.begin_iteration(progress=0.5, incumbent_obj=100.0)
        self.assertEqual(reheated_temperature, sa.temperature)

        static = alns.OperatorWeights(("d1", "d2"), adaptive=False)
        adaptive = alns.OperatorWeights(
            ("d1", "d2"), adaptive=True, segment_length=2, reaction=0.5
        )
        for weights in (static, adaptive):
            weights.record("d1", "improving")
            weights.record("d1", "improving")
        self.assertEqual((1.0, 1.0), static.weights)
        self.assertGreater(adaptive.weight("d1"), adaptive.weight("d2"))

        stall = alns.StagnationController(
            reheat_after=2,
            expand_after=4,
            restart_after=6,
        )
        self.assertEqual("none", stall.record(improved=False))
        self.assertEqual("reheat", stall.record(improved=False))
        self.assertEqual("none", stall.record(improved=False))
        self.assertEqual("expand", stall.record(improved=False))
        self.assertEqual("none", stall.record(improved=False))
        self.assertEqual("restart", stall.record(improved=False))
        self.assertEqual("none", stall.record(improved=True))

    @staticmethod
    def _copy_with_exit(state, exit_time):
        candidate = SolutionState(
            state.instance,
            geom=state.geom,
            shape_catalog=state.shape_catalog,
        )
        for placement in state.placements.values():
            candidate.place(
                Placement(
                    placement.block_id,
                    placement.bay_id,
                    placement.x,
                    placement.y,
                    placement.orient_idx,
                    exit_time - 1,
                    exit_time,
                )
            )
        return candidate

    def test_guarded_retime_and_backend_failure_keep_incumbent(self):
        for mode in ("improve", "worsen", "fail"):
            prob_info, state, incumbent = AcceptanceTests._single_block_fixture()
            before = state.capture_undo_token()
            trigger = alns.RetimeTrigger(
                min_dirty=1,
                dirty_fraction=0.01,
                min_interval_fraction=0.0,
            )

            def retime(current, bay_id, budget, _call_timebox):
                self.assertEqual(0, bay_id)
                if mode == "fail":
                    raise RuntimeError("forced backend failure")
                return self._copy_with_exit(
                    current,
                    7 if mode == "improve" else 9,
                )

            result = alns.run_alns(
                state,
                incumbent,
                Generator(PCG64(20260710)),
                max_iterations=1,
                registry=AcceptanceTests._scripted_registry((8,)),
                remove_count=1,
                retime_trigger=trigger,
                retime_callback=retime,
                timelimit_seconds=60.0,
            )
            expected = 7.0 if mode == "improve" else 8.0
            self.assertEqual(expected, state.objective, mode)
            checked = official_check(prob_info, result.solution)
            self.assertTrue(checked.feasible, (mode, checked.violations))
            self.assertEqual(expected, checked.objective, mode)
            self.assertEqual(before.bay_members, state.bay_members, mode)
            self.assertEqual(before.bay_loads, state.objective_diagnostics.bay_loads)
            self.assertEqual(before.z2, state.z2, mode)
            self.assertEqual(before.z3, state.z3, mode)
            self.assertEqual(1, result.metrics.retime_attempts, mode)
            self.assertEqual(int(mode == "improve"), result.metrics.retime_improvements)
            self.assertEqual(int(mode == "fail"), result.metrics.retime_failures)

    def test_rrt_worse_current_never_replaces_incumbent(self):
        prob_info, state, incumbent = AcceptanceTests._single_block_fixture(
            current_exit=100
        )
        result = alns.run_alns(
            state,
            incumbent,
            Generator(PCG64(20260710)),
            max_iterations=1,
            registry=AcceptanceTests._scripted_registry((102,)),
            remove_count=1,
            acceptor=alns.RRT_Acceptor(initial_deviation=0.03),
            safety_sample_interval=1,
        )
        self.assertEqual(102.0, state.objective)
        self.assertEqual(1, result.metrics.accepted_worsening)
        checked = official_check(prob_info, result.solution)
        self.assertTrue(checked.feasible, checked.violations)
        self.assertEqual(100.0, checked.objective)

    def test_same_bay_restart_is_checker_feasible(self):
        prob_info, state, _incumbent = AcceptanceTests._single_block_fixture()
        before = state.capture_undo_token()
        restarted = alns.same_bay_restart(
            state,
            Generator(PCG64(20260710)),
            AcceptanceTests._scripted_registry((9,)),
        )
        self.assertTrue(restarted)
        checked = official_check(
            prob_info,
            serialize_non_interlock(state.placements.values()),
        )
        self.assertTrue(checked.feasible, checked.violations)
        self.assertEqual(before.bay_members, state.bay_members)
        self.assertEqual(before.bay_loads, state.objective_diagnostics.bay_loads)
        self.assertEqual(before.z2, state.z2)
        self.assertEqual(before.z3, state.z3)


class AnytimeTests(unittest.TestCase):
    class FakeClock:
        def __init__(self):
            self.now = 0.0

        def __call__(self):
            return self.now

        def advance(self, seconds):
            self.now += float(seconds)

    def test_300_budget_contains_60_prefix(self):
        def run(timelimit):
            prob_info, state, incumbent = AcceptanceTests._single_block_fixture(
                current_exit=100
            )
            clock = self.FakeClock()
            events = []

            def record(event):
                events.append(
                    (
                        event.iteration,
                        event.destroy_name,
                        event.repair_name,
                        event.previous_cur_obj,
                        event.new_obj,
                        event.outcome,
                        event.accepted,
                    )
                )
                clock.advance(10.0)

            result = alns.run_anytime_epochs(
                state,
                incumbent,
                Generator(PCG64(20260710)),
                timelimit_seconds=timelimit,
                epoch_seconds=60.0,
                iterations_per_epoch=6,
                registry=AcceptanceTests._scripted_registry(range(99, 69, -1)),
                remove_count=1,
                acceptor_name="strict",
                adaptive=False,
                clock=clock,
                on_event=record,
            )
            checked = official_check(prob_info, result.solution)
            self.assertTrue(checked.feasible, checked.violations)
            return events, result

        short_events, short = run(60.0)
        long_events, long = run(300.0)

        self.assertEqual(short_events, long_events[: len(short_events)])
        self.assertEqual(6, len(short_events))
        self.assertEqual(30, len(long_events))
        self.assertLessEqual(
            long.incumbent_trace[-1].objective,
            short.incumbent_trace[-1].objective,
        )
        self.assertTrue(short.prefix_consistent)
        self.assertTrue(long.prefix_consistent)
        self.assertEqual(short_events, list(long.first_epoch_trace))

    def test_retime_wall_policy_skips_unsafe_short_call(self):
        nominal_solve_timebox = 0.01
        actual_backend_wall = 0.25

        def run_case(*, timelimit, search_wall):
            prob_info, state, incumbent = AcceptanceTests._single_block_fixture(
                current_exit=100
            )
            before = state.capture_undo_token()
            clock = self.FakeClock()
            budget = Budget(timelimit, clock=clock, reserve=0.0)
            trigger = alns.RetimeTrigger(
                min_dirty=1,
                dirty_fraction=0.01,
                min_interval_fraction=0.0,
            )
            policy = alns.RetimeWallPolicy(
                started_at=clock(),
                wall_fraction_cap=0.20,
                solve_timebox_seconds=nominal_solve_timebox,
                clock=clock,
            )
            backend_calls = 0

            def record(_event):
                clock.advance(search_wall)

            def retime(_current, bay_id, _active_budget, call_timebox):
                nonlocal backend_calls
                self.assertEqual(0, bay_id)
                self.assertLessEqual(call_timebox, nominal_solve_timebox)
                backend_calls += 1
                clock.advance(actual_backend_wall)
                return None

            result = alns.run_alns(
                state,
                incumbent,
                Generator(PCG64(20260710)),
                max_iterations=1,
                registry=AcceptanceTests._scripted_registry((99,)),
                remove_count=1,
                budget=budget,
                on_event=record,
                retime_trigger=trigger,
                retime_callback=retime,
                retime_wall_policy=policy,
                timelimit_seconds=timelimit,
            )
            checked = official_check(prob_info, result.solution)
            self.assertTrue(checked.feasible, checked.violations)
            self.assertEqual(5, checked.stage)
            self.assertEqual(before.bay_members, state.bay_members)
            self.assertEqual(before.bay_loads, state.objective_diagnostics.bay_loads)
            self.assertEqual(before.z2, state.z2)
            self.assertEqual(before.z3, state.z3)
            return clock(), backend_calls, trigger, result

        long_wall, long_calls, long_trigger, long_result = run_case(
            timelimit=5.0,
            search_wall=4.0,
        )
        self.assertEqual(1, long_calls)
        self.assertGreater(actual_backend_wall, nominal_solve_timebox)
        self.assertEqual(actual_backend_wall, long_trigger.retime_wall_seconds)
        self.assertLessEqual(
            long_trigger.retime_wall_seconds / long_wall,
            0.20,
        )
        self.assertEqual(99.0, long_result.incumbent_trace[-1].objective)

        short_wall, short_calls, short_trigger, short_result = run_case(
            timelimit=1.0,
            search_wall=0.1,
        )
        self.assertEqual(0, short_calls)
        self.assertLessEqual(short_trigger.retime_wall_seconds, 0.20 * short_wall)
        self.assertEqual(99.0, short_result.incumbent_trace[-1].objective)


class S3ExtensionTests(unittest.TestCase):
    class FakeClock:
        def __init__(self):
            self.now = 0.0

        def __call__(self):
            return self.now

        def advance(self, seconds):
            self.now += float(seconds)

    @staticmethod
    def _profile(**overrides):
        values = {
            "segment_seconds": 1.0,
            "iterations_per_batch": 1,
            "remove_count": 1,
            "acceptor_name": "strict",
            "adaptive": False,
            "safety_sample_interval": 0,
        }
        values.update(overrides)
        return alns.S3ExtensionProfile(**values)

    def _run_scripted(
        self,
        *,
        limit=1.0,
        exits=(99,),
        profile=None,
        seed=23,
        on_event=None,
        fault_hook=None,
        retime_trigger=None,
        retime_callback=None,
        budget=None,
    ):
        prob_info, _state, incumbent = AcceptanceTests._single_block_fixture(
            current_exit=100
        )
        checkpoint = incumbent.export_checkpoint()
        clock = self.FakeClock()
        active_budget = (
            Budget(limit, clock=clock, reserve=0.0) if budget is None else budget
        )
        result = alns.run_s3_extension(
            checkpoint,
            active_budget,
            self._profile() if profile is None else profile,
            seed,
            {},
            prob_info=prob_info,
            registry=AcceptanceTests._scripted_registry(exits),
            on_event=on_event,
            fault_hook=fault_hook,
            retime_trigger=retime_trigger,
            retime_callback=retime_callback,
        )
        return prob_info, checkpoint, clock, result

    def test_fault_and_deadline_boundaries_return_only_latest_verified_state(self):
        for point in ("repair", "accept", "full_check"):
            def fail(active_point, *, expected=point):
                if active_point == expected:
                    raise RuntimeError(f"injected {expected} fault")

            prob_info, anchor, _clock, result = self._run_scripted(
                fault_hook=fail
            )
            self.assertTrue(result.stopped_reason.startswith("fault:"), point)
            self.assertEqual(anchor.solution_sha256, result.checkpoint.solution_sha256)
            checked = official_check(prob_info, result.checkpoint.solution_copy())
            self.assertTrue(checked.feasible, (point, checked.violations))
            self.assertEqual(5, checked.stage)

        class BoundaryBudget:
            deadline = 1.0
            remaining = 1.0

            def __init__(self, target):
                self.target = target

            def checkpoint(self, label="S3 extension"):
                if label == self.target:
                    raise BudgetExpired(f"injected deadline at {label}")

        for label, expected_objective in (
            ("S3 scripted repair", 100.0),
            ("S3 acceptance reported", 100.0),
            ("S3 before full check", 100.0),
            ("S3 before candidate commit", 99.0),
        ):
            prob_info, _anchor, _clock, result = self._run_scripted(
                budget=BoundaryBudget(label)
            )
            self.assertEqual("deadline_fault", result.stopped_reason, label)
            checked = official_check(prob_info, result.checkpoint.solution_copy())
            self.assertTrue(checked.feasible, (label, checked.violations))
            self.assertEqual(expected_objective, checked.objective, label)

        trigger = alns.RetimeTrigger(
            min_dirty=1,
            dirty_fraction=0.01,
            min_interval_fraction=0.0,
        )
        retime_clock = self.FakeClock()

        def corrupt_then_fail(state, _bay_id, _budget, _timebox):
            placement = state.placements[0]
            state.remove(0)
            state.place(
                Placement(
                    0,
                    placement.bay_id,
                    placement.x,
                    placement.y,
                    placement.orient_idx,
                    49,
                    50,
                )
            )
            retime_clock.advance(1.0)
            raise RuntimeError("injected retime mutation fault")

        prob_info, _state, incumbent = AcceptanceTests._single_block_fixture(
            current_exit=100
        )
        anchor = incumbent.export_checkpoint()
        retimed = alns.run_s3_extension(
            anchor,
            Budget(1.0, clock=retime_clock, reserve=0.0),
            self._profile(),
            23,
            {},
            prob_info=prob_info,
            registry=AcceptanceTests._scripted_registry((99,)),
            retime_trigger=trigger,
            retime_callback=corrupt_then_fail,
        )
        checked = official_check(prob_info, retimed.checkpoint.solution_copy())
        self.assertTrue(checked.feasible, checked.violations)
        self.assertEqual(99.0, checked.objective)

        retime_deadline = self._run_scripted(
            budget=BoundaryBudget("S3 before retime"),
            retime_trigger=alns.RetimeTrigger(
                min_dirty=1,
                dirty_fraction=0.01,
                min_interval_fraction=0.0,
            ),
            retime_callback=lambda *_args: None,
        )[3]
        self.assertEqual("deadline_fault", retime_deadline.stopped_reason)
        self.assertEqual(99.0, retime_deadline.checkpoint.checker_result.objective)

    def test_later_segment_fault_preserves_latest_verified_improvement(self):
        clock = self.FakeClock()
        repair_count = 0

        def fail_second_repair(point):
            nonlocal repair_count
            if point == "repair":
                repair_count += 1
                if repair_count == 2:
                    raise RuntimeError("fault after verified improvement")

        prob_info, _state, incumbent = AcceptanceTests._single_block_fixture(
            current_exit=100
        )
        anchor = incumbent.export_checkpoint()
        real_run_alns = alns.run_alns
        batch_count = 0

        def end_first_segment(*args, **kwargs):
            nonlocal batch_count
            batch_count += 1
            batch_result = real_run_alns(*args, **kwargs)
            if batch_count == 1:
                clock.advance(1.0)
            return batch_result

        with patch("solver.alns.run_alns", side_effect=end_first_segment):
            result = alns.run_s3_extension(
                anchor,
                Budget(2.0, clock=clock, reserve=0.0),
                self._profile(remove_count=None),
                29,
                {},
                prob_info=prob_info,
                registry=AcceptanceTests._scripted_registry((99, 98)),
                fault_hook=fail_second_repair,
            )
        self.assertEqual(2, result.metrics.segments_started)
        self.assertTrue(result.stopped_reason.startswith("fault:"))
        self.assertNotEqual(anchor.solution_sha256, result.checkpoint.solution_sha256)
        checked = official_check(prob_info, result.checkpoint.solution_copy())
        self.assertTrue(checked.feasible, checked.violations)
        self.assertEqual(99.0, checked.objective)

    def test_throughput_jitter_cannot_change_full_logical_prefix(self):
        prob_info, _state, incumbent = AcceptanceTests._single_block_fixture(
            current_exit=100
        )
        checkpoint = incumbent.export_checkpoint()
        profile = self._profile(segment_seconds=1.0, iterations_per_batch=4)

        def run(limit, cadence):
            clock = self.FakeClock()
            parent = Budget(limit, clock=clock, reserve=0.0)

            def fake_batch(_state, active_incumbent, rng, **kwargs):
                for iteration in range(1, kwargs["max_iterations"] + 1):
                    draw = int(rng.integers(0, 2**31, dtype="int64"))
                    kwargs["on_event"](
                        alns.ALNSIterationEvent(
                            iteration=iteration,
                            destroy_name=f"d{draw % 5}",
                            repair_name=f"r{draw % 3}",
                            previous_cur_obj=float(draw),
                            new_obj=float(draw),
                            outcome="current_equal",
                            accepted=False,
                            potential_incumbent=False,
                        )
                    )
                return alns.ALNSRunResult(
                    solution=active_incumbent.solution,
                    metrics=alns.ALNSMetrics(
                        iterations=kwargs["max_iterations"]
                    ),
                    incumbent_trace=(),
                    stopped_reason="max_iterations",
                )

            with (
                patch("solver.alns.run_alns", side_effect=fake_batch),
                patch("solver.alns.same_bay_restart", return_value=False),
            ):
                result = alns.run_s3_extension(
                    checkpoint,
                    parent,
                    profile,
                    20260710,
                    {},
                    prob_info=prob_info,
                    on_event=lambda _event: clock.advance(cadence),
                )
            return parent, clock, result

        short_parent, short_clock, short = run(2.0, 0.125)
        long_parent, long_clock, long = run(4.0, 0.0625)

        self.assertEqual("work_deadline", short.stopped_reason)
        self.assertEqual("work_deadline", long.stopped_reason)
        self.assertEqual(2, short.metrics.segments_started)
        self.assertEqual(4, long.metrics.segments_started)
        self.assertEqual(short_parent.deadline, short_clock.now)
        self.assertEqual(long_parent.deadline, long_clock.now)
        self.assertGreater(len(long.trace), len(short.trace))

        mismatch = next(
            (
                index
                for index, (short_event, long_event) in enumerate(
                    zip(short.trace, long.trace)
                )
                if short_event != long_event
            ),
            None,
        )
        detail = None
        if mismatch is not None:
            detail = {
                "index": mismatch,
                "short": short.trace[mismatch],
                "long": long.trace[mismatch],
            }
        self.assertIsNone(
            mismatch,
            "wall-clock throughput changed the logical schedule: "
            f"{detail}",
        )
        self.assertEqual(short.trace, long.trace[: len(short.trace)])

    def test_real_retime_path_jitter_cannot_contaminate_logical_state(self):
        prob_info, _state, incumbent = AcceptanceTests._single_block_fixture(
            current_exit=100
        )
        checkpoint = incumbent.export_checkpoint()
        profile = self._profile(segment_seconds=1.0, iterations_per_batch=4)

        def run(cadence):
            clock = self.FakeClock()
            trigger = alns.RetimeTrigger(
                min_dirty=1,
                dirty_fraction=0.01,
                min_interval_fraction=0.0,
            )
            policy = alns.RetimeWallPolicy(
                started_at=clock(),
                wall_fraction_cap=0.5,
                solve_timebox_seconds=1.0,
                clock=clock,
            )
            backend_calls = 0
            event_count = 0
            backend_call_events = []

            def retime(current, bay_id, active_budget, call_timebox):
                nonlocal backend_calls
                self.assertEqual(0, bay_id)
                self.assertIsNotNone(active_budget)
                self.assertGreater(call_timebox, 0.0)
                backend_calls += 1
                backend_call_events.append(event_count)
                retime_exit = 50 if cadence < 0.2 else 60
                candidate = SolutionState(
                    current.instance,
                    geom=current.geom,
                    shape_catalog=current.shape_catalog,
                )
                for placement in current.placements.values():
                    candidate.place(
                        Placement(
                            placement.block_id,
                            placement.bay_id,
                            placement.x,
                            placement.y,
                            placement.orient_idx,
                            retime_exit - 1,
                            retime_exit,
                        )
                    )
                return candidate

            def advance(_event):
                nonlocal event_count
                event_count += 1
                clock.advance(cadence)

            result = alns.run_s3_extension(
                checkpoint,
                Budget(3.0, clock=clock, reserve=0.0),
                profile,
                20260710,
                {},
                prob_info=prob_info,
                registry=AcceptanceTests._scripted_registry(range(99, 70, -1)),
                retime_trigger=trigger,
                retime_callback=retime,
                retime_wall_policy=policy,
                on_event=advance,
            )
            return result, trigger, backend_calls, backend_call_events

        fast, fast_trigger, fast_backend_calls, fast_call_events = run(0.15)
        slow, slow_trigger, slow_backend_calls, slow_call_events = run(0.24)

        self.assertGreater(fast_trigger.attempts, 0)
        self.assertGreater(slow_trigger.attempts, 0)
        self.assertGreater(fast_backend_calls + slow_backend_calls, 0)
        common = min(len(fast.trace), len(slow.trace))
        self.assertGreaterEqual(common, 2)
        self.assertEqual(fast.trace[:common], slow.trace[:common])
        self.assertEqual(fast_call_events, slow_call_events)
        self.assertEqual(50.0, fast.checkpoint.checker_result.objective)
        self.assertEqual(60.0, slow.checkpoint.checker_result.objective)

    def test_operational_boundary_resumes_partial_logical_batch(self):
        prob_info, _state, incumbent = AcceptanceTests._single_block_fixture(
            current_exit=100
        )
        checkpoint = incumbent.export_checkpoint()
        profile = self._profile(segment_seconds=1.0, iterations_per_batch=6)

        def run(limit, cadence):
            clock = self.FakeClock()
            parent = Budget(limit, clock=clock, reserve=0.0)

            def sliced_batch(_state, active_incumbent, rng, **kwargs):
                metrics = alns.ALNSMetrics()
                stopped_reason = "max_iterations"
                for iteration in range(1, kwargs["max_iterations"] + 1):
                    metrics.iterations += 1
                    try:
                        kwargs["budget"].checkpoint("S3 iteration start")
                    except BudgetExpired:
                        metrics.deadlines += 1
                        stopped_reason = "deadline"
                        break
                    draw = int(rng.integers(0, 2**31, dtype="int64"))
                    kwargs["on_event"](
                        alns.ALNSIterationEvent(
                            iteration=iteration,
                            destroy_name=f"d{draw % 5}",
                            repair_name=f"r{draw % 3}",
                            previous_cur_obj=float(draw),
                            new_obj=float(draw),
                            outcome="current_equal",
                            accepted=False,
                            potential_incumbent=False,
                        )
                    )
                return alns.ALNSRunResult(
                    solution=active_incumbent.solution,
                    metrics=metrics,
                    incumbent_trace=(),
                    stopped_reason=stopped_reason,
                )

            with (
                patch("solver.alns.run_alns", side_effect=sliced_batch),
                patch("solver.alns.same_bay_restart", return_value=False),
            ):
                result = alns.run_s3_extension(
                    checkpoint,
                    parent,
                    profile,
                    20260710,
                    {},
                    prob_info=prob_info,
                    on_event=lambda _event: clock.advance(cadence),
                )
            return parent, clock, result

        short_parent, short_clock, short = run(2.0, 0.125)
        long_parent, long_clock, long = run(4.0, 0.0625)

        self.assertEqual(2, short.metrics.segments_started)
        self.assertEqual(4, long.metrics.segments_started)
        self.assertEqual(short_parent.deadline, short_clock.now)
        self.assertEqual(long_parent.deadline, long_clock.now)
        self.assertEqual(short.trace, long.trace[: len(short.trace)])

    def test_seeded_prefix_final_sha_and_assignment_are_deterministic(self):
        def run(limit):
            prob_info, _state, incumbent = AcceptanceTests._single_block_fixture(
                current_exit=100
            )
            anchor = incumbent.export_checkpoint()
            clock = self.FakeClock()
            result = alns.run_s3_extension(
                anchor,
                Budget(limit, clock=clock, reserve=0.0),
                self._profile(iterations_per_batch=2),
                31,
                {},
                prob_info=prob_info,
                registry=AcceptanceTests._scripted_registry(range(99, 70, -1)),
                on_event=lambda _event: clock.advance(0.25),
            )
            return prob_info, anchor, result

        short_prob, short_anchor, short = run(1.0)
        long_prob, long_anchor, long = run(2.0)
        _replay_prob, _replay_anchor, replay = run(2.0)

        self.assertEqual(short.trace, long.trace[: len(short.trace)])
        self.assertEqual(long.trace, replay.trace)
        self.assertEqual(
            long.checkpoint.solution_sha256,
            replay.checkpoint.solution_sha256,
        )
        self.assertLessEqual(
            long.checkpoint.checker_result.objective,
            long_anchor.checker_result.objective,
        )
        self.assertEqual(
            tuple((item.block_id, item.bay_id) for item in long_anchor.placements),
            tuple((item.block_id, item.bay_id) for item in long.checkpoint.placements),
        )
        self.assertEqual(
            long_anchor.checker_result.obj2,
            long.checkpoint.checker_result.obj2,
        )
        self.assertTrue(
            official_check(long_prob, long.checkpoint.solution_copy()).feasible
        )
        self.assertTrue(
            official_check(short_prob, short.checkpoint.solution_copy()).feasible
        )

    def test_worse_search_state_never_replaces_original_anchor(self):
        clock = self.FakeClock()
        prob_info, _state, incumbent = AcceptanceTests._single_block_fixture(
            current_exit=100
        )
        anchor = incumbent.export_checkpoint()
        real_run_alns = alns.run_alns

        def finish_batch(*args, **kwargs):
            batch_result = real_run_alns(*args, **kwargs)
            clock.advance(1.0)
            return batch_result

        with patch("solver.alns.run_alns", side_effect=finish_batch):
            result = alns.run_s3_extension(
                anchor,
                Budget(1.0, clock=clock, reserve=0.0),
                self._profile(acceptor_name="rrt"),
                37,
                {},
                prob_info=prob_info,
                registry=AcceptanceTests._scripted_registry((102,)),
            )
        self.assertEqual(1, result.metrics.accepted)
        self.assertEqual(anchor.solution_sha256, result.checkpoint.solution_sha256)
        self.assertEqual(100.0, result.checkpoint.checker_result.objective)

    def test_identical_state_rng_profile_cannot_spin_forever(self):
        prob_info, _state, incumbent = AcceptanceTests._single_block_fixture(
            current_exit=100
        )
        anchor = incumbent.export_checkpoint()
        clock = self.FakeClock()
        calls = 0

        def no_progress(_state, active_incumbent, _rng, **_kwargs):
            nonlocal calls
            calls += 1
            return alns.ALNSRunResult(
                solution=active_incumbent.solution,
                metrics=alns.ALNSMetrics(),
                incumbent_trace=(),
                stopped_reason="max_iterations",
            )

        with (
            patch("solver.alns.run_alns", side_effect=no_progress),
            patch("solver.alns.same_bay_restart", return_value=False),
        ):
            result = alns.run_s3_extension(
                anchor,
                Budget(24.0, clock=clock, reserve=0.0),
                self._profile(segment_seconds=8.0, iterations_per_batch=24),
                41,
                {},
                prob_info=prob_info,
            )
        self.assertEqual(1, calls)
        self.assertEqual(1, result.metrics.batches_started)
        self.assertEqual(1, result.metrics.duplicate_skips)
        self.assertEqual("stalled", result.stopped_reason)


if __name__ == "__main__":
    unittest.main()
