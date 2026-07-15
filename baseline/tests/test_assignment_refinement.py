"""Behavioral contracts for S4 checker-float assignment refinement."""

from __future__ import annotations

import math
from dataclasses import asdict, replace
import hashlib
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from numpy.random import Generator, PCG64

from harness import cli as harness_cli
from harness import runner as harness_runner
from harness.selectors import InstanceRef
from solver import entry as solver_entry
from solver.alns import (
    CrossBayRegistry,
    OperatorRegistry,
    rank_cross_bay_candidates,
    run_cross_bay_candidate,
)
from solver.exact import (
    AssignmentRequest,
    AssignmentResult as ExactAssignmentResult,
    SOLUTION_STATUSES,
)
from solver.assign import (
    AssignmentEvaluation,
    AssignmentSelection,
    AssignmentVerification,
    AssignmentV1,
    assignment_from_solution,
    assignment_request,
    assignment_v2,
    choose_assignment_candidate,
)
from solver.budget import Budget, BudgetExpired
from solver.checker_adapter import official_check
from solver.config import SolverConfig
from solver.construct import construct_profile
from solver.cpsat_backend import (
    ASSIGNMENT_SCALE,
    assign_cpsat,
    build_cpsat_assignment_spec,
)
from solver.gurobi_backend import build_gurobi_assignment_spec
from solver.instance import ProblemInstance
from solver.serialize import serialize_non_interlock
from solver.incumbent import VerifiedIncumbent
from solver.entry import _run_assignment_seed, _run_cross_bay_refinement
from solver.state import Placement, SolutionState
from solver.validate import validate_pair
from tests.fixtures import block, instance


class CrossBayTests(unittest.TestCase):
    def _fixture(self):
        prob_info = instance(
            [
                block(workload=0.10, preferences=(0, 10)),
                block(workload=0.20, preferences=(10, 0)),
                block(workload=0.30, preferences=(10, 0)),
                block(workload=0.40, preferences=(0, 10)),
            ],
            bays=((4, 4), (4, 4)),
            weights={"w1": 1.0, "w2": 2.5, "w3": 7.25},
        )
        parsed = ProblemInstance.parse(prob_info)
        state = SolutionState(parsed)
        for placement in (
            Placement(0, 0, 0, 0, 0, 0, 1),
            Placement(1, 0, 0, 0, 0, 1, 2),
            Placement(2, 1, 0, 0, 0, 0, 1),
            Placement(3, 1, 0, 0, 0, 1, 2),
        ):
            state.place(placement)
        state.assert_invariants()
        incumbent = VerifiedIncumbent(parsed)
        incumbent.register_initial(state)
        return prob_info, state, incumbent

    def _multidigit_checkpoint_fixture(self):
        prob_info = instance(
            [
                block(
                    release=96,
                    due=100,
                    processing=9,
                    workload=3,
                    preferences=(0, 10),
                )
            ],
            bays=((4, 4), (4, 4)),
            weights={"w1": 2.0, "w2": 3.0, "w3": 4.0},
        )
        parsed = ProblemInstance.parse(prob_info)
        state = SolutionState(parsed)
        state.place(Placement(0, 0, 0, 0, 0, 96, 105))
        state.assert_invariants()
        incumbent = VerifiedIncumbent(parsed)
        incumbent.register_initial(state)
        return prob_info, parsed, state, incumbent, incumbent.export_checkpoint()

    @staticmethod
    def _solution_sha(solution):
        payload = json.dumps(solution, sort_keys=True, separators=(",", ":")).encode()
        return hashlib.sha256(payload).hexdigest()

    @staticmethod
    def _copy_state(state):
        copied = SolutionState(
            state.instance,
            geom=state.geom,
            shape_catalog=state.shape_catalog,
        )
        for placement in state.placements.values():
            copied.place(placement)
        return copied

    @staticmethod
    def _wait_for_pid_exit(pid, timeout=1.0):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            try:
                os.kill(pid, 0)
            except ProcessLookupError:
                return True
            time.sleep(0.01)
        return False

    @unittest.skipUnless(hasattr(os, "killpg"), "requires POSIX process groups")
    def test_s4_worker_hard_timeout_reaps_process_group(self):
        run_worker = getattr(solver_entry, "_run_s4_worker_process", None)
        self.assertIsNotNone(
            run_worker,
            "S4 hard isolation process helper is required",
        )
        worker_pid = None
        child_pid = None
        try:
            with tempfile.TemporaryDirectory() as tmpdir:
                pid_path = Path(tmpdir) / "pids.txt"
                child_code = (
                    "import signal,time; "
                    "signal.signal(signal.SIGTERM, signal.SIG_IGN); "
                    "time.sleep(60)"
                )
                worker_code = (
                    "import os,signal,subprocess,sys,time; "
                    "signal.signal(signal.SIGTERM, signal.SIG_IGN); "
                    f"child=subprocess.Popen([sys.executable,'-c',{child_code!r}]); "
                    "open(sys.argv[1],'w').write(f'{os.getpid()} {child.pid}'); "
                    "time.sleep(60)"
                )
                started = time.monotonic()
                result = run_worker(
                    (sys.executable, "-c", worker_code, str(pid_path)),
                    process_kill_deadline=started + 0.2,
                    parent_hard_deadline=started + 0.45,
                    terminate_grace=0.0125,
                )
                worker_pid, child_pid = map(int, pid_path.read_text().split())
                worker_exited = self._wait_for_pid_exit(worker_pid)
                child_exited = self._wait_for_pid_exit(child_pid)
        finally:
            for pid in (worker_pid, child_pid):
                if pid is None:
                    continue
                try:
                    os.kill(pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass

        self.assertTrue(result.timed_out)
        self.assertTrue(result.term_sent)
        self.assertTrue(result.kill_sent)
        self.assertIsNone(result.exit_code)
        self.assertEqual(signal.SIGKILL, result.signal)
        self.assertTrue(worker_exited)
        self.assertTrue(child_exited)
        self.assertLessEqual(result.wall_seconds, 0.45)

    def test_s4_hard_timeout_returns_verified_s3(self):
        resume = getattr(solver_entry, "refine_s4_checkpoint", None)
        worker_argv = getattr(solver_entry, "_s4_worker_argv", None)
        self.assertIsNotNone(worker_argv, "private S4 worker command is required")
        prob_info, _state, incumbent = self._fixture()
        checkpoint = incumbent.export_checkpoint()
        telemetry = {}
        slow_argv = (
            sys.executable,
            "-c",
            "import signal,time; signal.signal(signal.SIGTERM, signal.SIG_IGN); time.sleep(60)",
        )
        with (
            patch("solver.entry._s4_worker_argv", return_value=slow_argv),
            patch("solver.entry._s4_worker_return_margin", return_value=0.05),
            patch("solver.entry._s4_worker_skip_reason", return_value=None),
        ):
            started = time.monotonic()
            solution = resume(
                prob_info,
                checkpoint,
                remaining_timelimit=0.3,
                original_timelimit=2.0,
                seed=20260710,
                backend_fault=None,
                telemetry=telemetry,
            )

        self.assertLessEqual(time.monotonic() - started, 0.45)
        self.assertEqual(checkpoint.solution_copy(), solution)
        self.assertEqual("hard_timed_out", telemetry["s4_worker_status"])
        self.assertTrue(telemetry["s4_hard_timeout_fallback"])
        self.assertTrue(telemetry["s4_worker_term_sent"])
        self.assertTrue(telemetry["s4_worker_kill_sent"])
        self.assertTrue(telemetry["s4_worker_group_clean"])
        self.assertEqual(checkpoint.solution_sha256, telemetry["s4_verified_solution_sha256"])
        self.assertEqual(
            checkpoint.checker_result.objective,
            telemetry["s4_verified_checker"]["objective"],
        )
        self.assertEqual(
            checkpoint.checker_result.obj2,
            telemetry["s4_verified_checker"]["obj2"],
        )
        corrupt = replace(
            checkpoint,
            checker_result=replace(
                checkpoint.checker_result,
                objective=checkpoint.checker_result.objective + 1.0,
            ),
        )
        with self.assertRaisesRegex(ValueError, "checker result mismatch"):
            resume(
                prob_info,
                corrupt,
                remaining_timelimit=0.0,
                original_timelimit=0.5,
                seed=20260710,
                backend_fault=None,
                telemetry={},
            )

    def test_s4_worker_success_roundtrips_verified_checkpoint(self):
        worker_argv = getattr(solver_entry, "_s4_worker_argv", None)
        self.assertIsNotNone(worker_argv, "private S4 worker command is required")
        prob_info, _parsed, _state, _incumbent, checkpoint = (
            self._multidigit_checkpoint_fixture()
        )
        telemetry = {}
        with patch(
            "solver.incumbent.official_check",
            wraps=official_check,
        ) as parent_check:
            solution = solver_entry.refine_s4_checkpoint(
                prob_info,
                checkpoint,
                remaining_timelimit=15.0,
                original_timelimit=15.0,
                seed=20260710,
                backend_fault=None,
                telemetry=telemetry,
            )

        checked = official_check(prob_info, solution)
        self.assertEqual(1, parent_check.call_count)
        self.assertEqual("completed", telemetry["s4_worker_status"])
        self.assertEqual(0, telemetry["s4_worker_exit_code"])
        self.assertIsNone(telemetry["s4_worker_signal"])
        self.assertFalse(telemetry["s4_worker_term_sent"])
        self.assertFalse(telemetry["s4_worker_kill_sent"])
        self.assertFalse(telemetry["s4_hard_timeout_fallback"])
        self.assertEqual(["96", "105"], list(solution["operations"]))
        self.assertEqual(
            self._solution_sha(solution),
            telemetry["s4_verified_solution_sha256"],
        )
        self.assertEqual(checked.objective, telemetry["s4_verified_checker"]["objective"])
        self.assertEqual(checked.obj2, telemetry["s4_verified_checker"]["obj2"])
        self.assertLessEqual(checked.objective, checkpoint.checker_result.objective)
        self.assertLessEqual(checked.obj2, checkpoint.checker_result.obj2)

    def test_s4_cooperative_expiry_publishes_verified_incumbent(self):
        prob_info, state, source_incumbent = self._fixture()
        input_checkpoint = source_incumbent.export_checkpoint()
        candidate = next(
            item
            for item in rank_cross_bay_candidates(state)
            if item.kind == "move" and item.exact_weighted_delta < 0.0
        )
        accepted = run_cross_bay_candidate(
            state,
            source_incumbent,
            candidate,
            Generator(PCG64(20260710)),
            retime=lambda current, _bay_id, _budget: self._copy_state(current),
        )
        self.assertTrue(accepted.committed, accepted.reason)
        expected_checkpoint = source_incumbent.export_checkpoint()
        improved_state = self._copy_state(state)
        telemetry = {}

        def improve_then_expire(
            _instance,
            _current_state,
            worker_incumbent,
            _budget,
            **_kwargs,
        ):
            self.assertTrue(worker_incumbent.try_update(self._copy_state(improved_state)))
            raise BudgetExpired("deterministic cooperative expiry after improvement")

        with (
            patch(
                "solver.incumbent.official_check",
                wraps=official_check,
            ) as parent_check,
            patch("solver.entry.os.replace", wraps=os.replace) as atomic_replace,
            patch(
                "solver.entry._refine_verified_s3",
                side_effect=improve_then_expire,
            ),
        ):
            def run_inline(argv, **_kwargs):
                started = time.monotonic()
                exit_code = solver_entry._s4_worker_main(
                    Path(argv[-2]),
                    Path(argv[-1]),
                )
                parent_check.reset_mock()
                return solver_entry._S4WorkerProcessResult(
                    pid=os.getpid(),
                    exit_code=exit_code,
                    signal=None,
                    wall_seconds=time.monotonic() - started,
                    timed_out=False,
                    term_sent=False,
                    kill_sent=False,
                    group_clean=True,
                )

            with patch(
                "solver.entry._run_s4_worker_process",
                side_effect=run_inline,
            ):
                solution = solver_entry.refine_s4_checkpoint(
                    prob_info,
                    input_checkpoint,
                    remaining_timelimit=15.0,
                    original_timelimit=15.0,
                    seed=20260710,
                    backend_fault=None,
                    telemetry=telemetry,
                )

        self.assertEqual(expected_checkpoint.solution_copy(), solution)
        self.assertLess(
            telemetry["s4_final_objective"],
            input_checkpoint.checker_result.objective,
        )
        self.assertEqual("completed_after_budget", telemetry["s4_worker_status"])
        self.assertEqual(0, telemetry["s4_worker_exit_code"])
        self.assertIsNone(telemetry["s4_worker_signal"])
        self.assertFalse(telemetry["s4_worker_term_sent"])
        self.assertFalse(telemetry["s4_worker_kill_sent"])
        self.assertTrue(telemetry["s4_worker_group_clean"])
        self.assertFalse(telemetry["s4_hard_timeout_fallback"])
        self.assertEqual(1, atomic_replace.call_count)
        self.assertEqual(1, parent_check.call_count)

    def test_s4_deadlines_are_ordered_publishable_and_identity_bound(self):
        prob_info, _state, incumbent = self._fixture()
        checkpoint = incumbent.export_checkpoint()
        captured = {}

        def capture_runner(argv, **kwargs):
            captured["request"] = json.loads(Path(argv[-2]).read_text())
            captured["kwargs"] = kwargs
            return solver_entry._S4WorkerProcessResult(
                pid=12345,
                exit_code=None,
                signal=signal.SIGTERM,
                wall_seconds=0.0,
                timed_out=True,
                term_sent=True,
                kill_sent=False,
                group_clean=True,
            )

        with patch(
            "solver.entry._run_s4_worker_process",
            side_effect=capture_runner,
        ):
            solver_entry.refine_s4_checkpoint(
                prob_info,
                checkpoint,
                remaining_timelimit=15.0,
                original_timelimit=15.0,
                seed=20260710,
                backend_fault=None,
                telemetry={},
            )

        request = captured["request"]
        required = {
            "cooperative_return_deadline",
            "process_kill_deadline",
            "parent_hard_deadline",
            "worker_return_margin",
        }
        self.assertTrue(required <= set(request), sorted(set(request)))
        cooperative = request["cooperative_return_deadline"]
        kill = request["process_kill_deadline"]
        parent_hard = request["parent_hard_deadline"]
        self.assertLess(cooperative, kill)
        self.assertLess(kill, parent_hard)
        self.assertAlmostEqual(2.5, kill - cooperative, places=6)
        self.assertAlmostEqual(request["worker_return_margin"], kill - cooperative)
        self.assertGreaterEqual(parent_hard - kill, 0.5)
        self.assertGreaterEqual(
            request["work_allowance"],
            solver_entry._S4_MINIMUM_USEFUL_WORK_SECONDS,
        )
        self.assertIsNone(
            solver_entry._s4_worker_skip_reason(
                request["work_allowance"],
                parent_hard - cooperative,
            )
        )
        self.assertEqual(
            kill,
            captured["kwargs"]["process_kill_deadline"],
        )
        self.assertEqual(
            parent_hard,
            captured["kwargs"]["parent_hard_deadline"],
        )

        mutations = {
            "cooperative_return_deadline": cooperative + 1.0,
            "process_kill_deadline": kill + 1.0,
            "parent_hard_deadline": parent_hard + 1.0,
            "worker_return_margin": request["worker_return_margin"] + 0.25,
            "remaining_timelimit": request["remaining_timelimit"] + 1.0,
            "original_timelimit": request["original_timelimit"] + 1.0,
            "work_allowance": request["work_allowance"] + 1.0,
            "seed": request["seed"] + 1,
            "assignment_v2_enabled": not request["assignment_v2_enabled"],
            "cross_bay_enabled": not request["cross_bay_enabled"],
            "backend_fault": "gurobi",
        }
        for field, replacement in mutations.items():
            mutated = json.loads(json.dumps(request))
            mutated[field] = replacement
            with self.subTest(field=field):
                self.assertNotEqual(
                    request["request_id"],
                    solver_entry._s4_request_id(mutated),
                )
        for field in ("solution_sha256", "instance_sha256"):
            mutated = json.loads(json.dumps(request))
            mutated["checkpoint"][field] = "0" * 64
            with self.subTest(checkpoint_field=field):
                self.assertNotEqual(
                    request["request_id"],
                    solver_entry._s4_request_id(mutated),
                )

    def test_s4_no_work_returns_exact_verified_s3_telemetry(self):
        prob_info, _state, incumbent = self._fixture()
        checkpoint = incumbent.export_checkpoint()
        telemetry = {}

        with patch(
            "solver.incumbent.official_check",
            wraps=official_check,
        ) as parent_check:
            solution = solver_entry.refine_s4_checkpoint(
                prob_info,
                checkpoint,
                remaining_timelimit=0.0,
                original_timelimit=60.0,
                seed=20260710,
                backend_fault=None,
                telemetry=telemetry,
            )

        self.assertEqual(checkpoint.solution_copy(), solution)
        self.assertEqual(1, parent_check.call_count)
        self.assertEqual("skipped_insufficient_work", telemetry["s4_worker_status"])
        self.assertEqual("minimum_useful_work_not_available", telemetry["s4_skip_reason"])
        self.assertFalse(telemetry["s4_worker_launched"])
        self.assertNotIn("assignment_seed_attempts", telemetry)
        self.assertFalse(telemetry["s4_hard_timeout_fallback"])
        self.assertTrue(telemetry["s4_worker_group_clean"])
        self.assertEqual(
            checkpoint.solution_sha256,
            telemetry["s4_verified_solution_sha256"],
        )

    def test_s4_worker_invalid_output_falls_back_without_regression(self):
        worker_argv = getattr(solver_entry, "_s4_worker_argv", None)
        self.assertIsNotNone(worker_argv, "private S4 worker command is required")
        prob_info, _state, incumbent = self._fixture()
        checkpoint = incumbent.export_checkpoint()
        started = time.monotonic()
        request = solver_entry._s4_worker_request(
            prob_info,
            checkpoint,
            cooperative_return_deadline=started + 12.0,
            process_kill_deadline=started + 14.5,
            parent_hard_deadline=started + 15.0,
            worker_return_margin=2.5,
            remaining_timelimit=15.0,
            original_timelimit=15.0,
            work_allowance=12.0,
            seed=20260710,
            assignment_v2_enabled=True,
            cross_bay_enabled=True,
            backend_fault=None,
        )
        mutated_request = dict(request)
        mutated_request["cooperative_return_deadline"] += 1.0
        self.assertNotEqual(
            request["request_id"],
            solver_entry._s4_request_id(mutated_request),
        )

        def corrupt_argv(input_path, output_path):
            script = (
                "import json,sys; from pathlib import Path; "
                "request=json.loads(Path(sys.argv[1]).read_text()); "
                "checkpoint=request['checkpoint']; "
                "input_sha=checkpoint['solution_sha256']; "
                "mode=sys.argv[3]; "
                "checkpoint['verification_count'] += "
                "(0 if mode=='count' else 1); "
                "checkpoint['solution_sha256']="
                "('0'*64 if mode=='solution_sha' else checkpoint['solution_sha256']); "
                "checkpoint['checker_result']['objective'] += "
                "(1.0 if mode=='checker' else 0.0); "
                "envelope={'schema_version':request['schema_version'],"
                "'request_id':request['request_id'],"
                "'input_checkpoint_sha256':input_sha,'checkpoint':checkpoint,"
                "'telemetry':{'s4_worker_completion_status':'completed',"
                "'incumbent_verification_count':"
                "checkpoint['verification_count']}}; "
                "Path(sys.argv[2]).write_text(json.dumps(envelope))"
            )
            return (
                sys.executable,
                "-c",
                script,
                str(input_path),
                str(output_path),
                corruption,
            )

        for corruption in ("solution_sha", "count", "checker"):
            telemetry = {}
            with (
                self.subTest(corruption=corruption),
                patch("solver.entry._s4_worker_argv", side_effect=corrupt_argv),
            ):
                solution = solver_entry.refine_s4_checkpoint(
                    prob_info,
                    checkpoint,
                    remaining_timelimit=15.0,
                    original_timelimit=15.0,
                    seed=20260710,
                    backend_fault=None,
                    telemetry=telemetry,
                )

                checked = official_check(prob_info, solution)
                self.assertEqual(checkpoint.solution_copy(), solution)
                self.assertEqual("invalid_output", telemetry["s4_worker_status"])
                self.assertEqual(0, telemetry["s4_worker_exit_code"])
                self.assertIsNone(telemetry["s4_worker_signal"])
                self.assertFalse(telemetry["s4_hard_timeout_fallback"])
                self.assertTrue(telemetry["s4_worker_group_clean"])
                self.assertIn(
                    "invalid",
                    telemetry["s4_worker_fallback_reason"].lower(),
                )
                self.assertEqual(
                    checkpoint.solution_sha256,
                    telemetry["s4_verified_solution_sha256"],
                )
                self.assertEqual(5, checked.stage)
                self.assertEqual(checkpoint.checker_result.objective, checked.objective)
                self.assertEqual(checkpoint.checker_result.obj2, checked.obj2)

    @staticmethod
    def _expected_assignment_delta(state, candidate):
        loads = list(state.objective_diagnostics.bay_loads)
        z3 = state.z3
        for block_id, source_bay, target_bay in zip(
            candidate.block_ids,
            candidate.source_bays,
            candidate.target_bays,
            strict=True,
        ):
            block_info = state.instance.blocks[block_id]
            loads[source_bay] -= block_info.workload
            loads[target_bay] += block_info.workload
            preference_best = max(block_info.bay_preferences)
            z3 += (
                preference_best
                - block_info.bay_preferences[target_bay]
                - (preference_best - block_info.bay_preferences[source_bay])
            )
        average_area = sum(bay.area for bay in state.instance.bays) / len(
            state.instance.bays
        )
        normalized = tuple(
            average_area / bay.area * load
            for bay, load in zip(state.instance.bays, loads, strict=True)
        )
        z2 = 0.0 if len(normalized) < 2 else max(normalized) - min(normalized)
        return z2 - state.z2, z3 - state.z3

    def test_move_swap_undo_and_float_delta(self):
        _prob_info, state, _incumbent = self._fixture()
        s3_registry = OperatorRegistry()
        registry = CrossBayRegistry()
        self.assertEqual(("d1", "d2", "d3", "d4", "d5"), s3_registry.destroy_names)
        self.assertEqual(("move", "swap", "d6"), registry.operator_names)
        self.assertTrue(
            all(operator.changes_assignment for operator in registry.operators.values())
        )

        candidates = rank_cross_bay_candidates(state, registry=registry)
        self.assertTrue(candidates)
        by_kind = {
            kind: next(candidate for candidate in candidates if candidate.kind == kind)
            for kind in ("move", "swap")
        }
        self.assertEqual(
            tuple(candidate.priority for candidate in candidates),
            tuple(sorted(candidate.priority for candidate in candidates)),
        )
        for candidate in by_kind.values():
            expected_z2, expected_z3 = self._expected_assignment_delta(
                state, candidate
            )
            self.assertTrue(
                math.isclose(candidate.delta_z2, expected_z2, rel_tol=1e-12, abs_tol=1e-12)
            )
            self.assertTrue(
                math.isclose(candidate.delta_z3, expected_z3, rel_tol=1e-12, abs_tol=1e-12)
            )
            self.assertTrue(
                math.isclose(
                    candidate.exact_weighted_delta,
                    state.instance.weights[1] * expected_z2
                    + state.instance.weights[2] * expected_z3,
                    rel_tol=1e-12,
                    abs_tol=1e-12,
                )
            )

        for fault in ("repair", "retime", "full_check"):
            prob_info, state, incumbent = self._fixture()
            before = state.capture_undo_token()
            before_sha = self._solution_sha(incumbent.solution)
            candidate = next(
                item
                for item in rank_cross_bay_candidates(state, registry=registry)
                if item.kind == "swap"
            )

            def inject(point, *, selected=fault):
                if point == selected:
                    raise RuntimeError(f"injected {selected} fault")

            result = run_cross_bay_candidate(
                state,
                incumbent,
                candidate,
                Generator(PCG64(20260710)),
                retime=lambda current, _bay_id, _budget: self._copy_state(current),
                fault_hook=inject,
            )

            self.assertFalse(result.committed)
            self.assertEqual(f"fault:{fault}", result.reason)
            self.assertEqual(before, state.capture_undo_token())
            self.assertEqual(before_sha, result.incumbent_sha256)
            self.assertEqual(before_sha, self._solution_sha(incumbent.solution))
            checked = official_check(
                prob_info, serialize_non_interlock(state.placements.values())
            )
            self.assertTrue(checked.feasible, checked.violations)
            self.assertEqual(5, checked.stage)

    def test_successful_move_and_swap_are_fully_checked(self):
        for kind in ("move", "swap"):
            prob_info, state, incumbent = self._fixture()
            before = state.capture_undo_token()
            before_sha = self._solution_sha(incumbent.solution)
            candidate = next(
                item
                for item in rank_cross_bay_candidates(state)
                if item.kind == kind and item.exact_weighted_delta < 0.0
            )
            retimed_bays = []

            def retime(current, bay_id, _budget):
                retimed_bays.append(bay_id)
                return self._copy_state(current)

            result = run_cross_bay_candidate(
                state,
                incumbent,
                candidate,
                Generator(PCG64(20260710)),
                retime=retime,
            )

            self.assertTrue(result.committed, (kind, result.reason))
            self.assertEqual((0, 1), result.affected_bays)
            self.assertEqual([0, 1], retimed_bays)
            self.assertTrue(result.checker_feasible)
            self.assertEqual(5, result.checker_stage)
            self.assertNotEqual(before.bay_members, state.bay_members)
            self.assertTrue(
                math.isclose(
                    state.z2 - before.z2,
                    candidate.delta_z2,
                    rel_tol=1e-12,
                    abs_tol=1e-12,
                )
            )
            self.assertTrue(
                math.isclose(
                    state.z3 - before.z3,
                    candidate.delta_z3,
                    rel_tol=1e-12,
                    abs_tol=1e-12,
                )
            )
            self.assertNotEqual(before_sha, result.incumbent_sha256)
            self.assertEqual(
                result.incumbent_sha256, self._solution_sha(incumbent.solution)
            )
            checked = official_check(
                prob_info, serialize_non_interlock(state.placements.values())
            )
            self.assertTrue(checked.feasible, checked.violations)
            self.assertEqual(5, checked.stage)

    def test_cross_bay_summary_requires_move_swap_counters(self):
        record = {
            "status": "passed",
            "checker": {"feasible": True, "stage": 5},
            "prior_checker": {"feasible": True, "stage": 5},
            "move_attempts": 1,
            "move_accepted": 0,
            "swap_attempts": 1,
            "swap_accepted": 1,
            "cross_bay_attempts": 2,
            "d6_attempts": 2,
            "retime_backend_attempts": 4,
            "unverified_return_count": 0,
            "wall_seconds": 1.0,
            "timelimit": 60.0,
        }
        summary, exit_code = harness_cli._cross_bay_summary(
            [record],
            expected_record_count=1,
            selector="high-w23",
            timelimits=(60.0,),
            seeds=(20260710,),
            features={"cross_bay": "true"},
        )
        self.assertEqual(harness_cli.EXIT_PASS, exit_code)
        self.assertEqual(1, summary["move_attempts"])
        self.assertEqual(1, summary["swap_attempts"])

        missing = dict(record)
        del missing["swap_attempts"]
        _summary, failed_exit = harness_cli._cross_bay_summary(
            [missing],
            expected_record_count=1,
            selector="high-w23",
            timelimits=(60.0,),
            seeds=(20260710,),
            features={"cross_bay": "true"},
        )
        self.assertEqual(harness_cli.EXIT_CHECKER_FAILURE, failed_exit)

    def test_assignment_seed_starts_from_verified_incumbent_not_mutable_current(self):
        _prob_info, verified_state, incumbent = self._fixture()
        mutable_current = self._copy_state(verified_state)
        original = mutable_current.get(0)
        self.assertIsNotNone(original)
        mutable_current.place(replace(original, bay_id=1))
        expected_membership = tuple(
            sorted(
                (placement.block_id, placement.bay_id)
                for placement in verified_state.placements.values()
            )
        )
        observed_membership = []

        def choose(_instance, starting, **_kwargs):
            observed_membership.append(
                tuple(
                    sorted(
                        (block_id, item.bay_id)
                        for block_id, item in starting.assignments.items()
                    )
                )
            )
            return AssignmentSelection(
                assignment=starting,
                backend="greedy",
                evaluation=AssignmentEvaluation(
                    bay_workloads=starting.bay_workloads,
                    z2=starting.z2,
                    z3=starting.z3,
                    overload=0.0,
                    objective=0.0,
                ),
                checker_objective=incumbent.checker_result.objective,
                verified=True,
                fallback_reason="no_improvement",
                attempts=(),
            )

        with patch("solver.entry.choose_assignment_candidate", side_effect=choose):
            returned = _run_assignment_seed(
                verified_state.instance,
                mutable_current,
                incumbent,
                Budget(60.0, reserve=0.0),
                seed=20260711,
                backend_fault=None,
                telemetry={},
            )

        self.assertEqual([expected_membership], observed_membership)
        self.assertEqual(
            expected_membership,
            tuple(
                sorted(
                    (placement.block_id, placement.bay_id)
                    for placement in returned.placements.values()
                )
            ),
        )

    def test_cross_bay_tries_next_ranked_candidate_and_preserves_rejected_state(self):
        _prob_info, state, incumbent = self._fixture()
        candidates = tuple(
            candidate
            for candidate in rank_cross_bay_candidates(state)
            if candidate.kind == "move"
            and candidate.after_z2
            <= incumbent.checker_result.obj2
            + 1e-9 * max(1.0, abs(incumbent.checker_result.obj2))
        )[:2]
        self.assertEqual(2, len(candidates))
        state_before = state.capture_undo_token()
        incumbent_before = self._solution_sha(incumbent.solution)
        rejected = SimpleNamespace(
            committed=False,
            reason="checker_not_improving",
            checker_objective=incumbent.checker_result.objective,
        )
        telemetry = {}

        with (
            patch(
                "solver.entry.rank_cross_bay_candidates",
                return_value=candidates,
            ),
            patch(
                "solver.entry.run_cross_bay_candidate",
                side_effect=(rejected, rejected),
            ) as attempted,
        ):
            returned = _run_cross_bay_refinement(
                state,
                incumbent,
                Budget(60.0, reserve=0.0),
                seed=20260710,
                backend_calls={"gurobi": lambda *_args: None},
                available_backends=("gurobi",),
                max_z2=incumbent.checker_result.obj2,
                telemetry=telemetry,
            )

        self.assertIs(state, returned)
        self.assertEqual(2, attempted.call_count)
        self.assertEqual(2, telemetry["cross_bay_move_attempts"])
        self.assertEqual(0, telemetry["cross_bay_move_accepted"])
        self.assertEqual(state_before, state.capture_undo_token())
        self.assertEqual(incumbent_before, self._solution_sha(incumbent.solution))

    def test_s4_ab_summary_enforces_paired_gain_and_z2_safety(self):
        records = []
        for order in ("forward", "reverse"):
            for enabled, objective, z2 in (
                (False, 100.0, 10.0),
                (True, 90.0, 9.0),
            ):
                records.append(
                    {
                        "instance_id": "prob_x",
                        "timelimit": 60.0,
                        "seed": 20260710,
                        "run_label": order,
                        "arm_enabled": enabled,
                        "checker": {
                            "feasible": True,
                            "stage": 5,
                            "objective": objective,
                            "z2": z2,
                        },
                        "status": "passed",
                        "s3_solution_sha256": f"s3-{order}",
                        "s3_checker": {
                            "feasible": True,
                            "stage": 5,
                            "objective": 100.0,
                            "z2": 10.0,
                        },
                        "pair_execution_id": f"pair-{order}",
                        "s3_shared_across_arms": True,
                        "shared_s3_elapsed": 40.0,
                        "s4_remaining_timelimit": 20.0,
                        "s4_allocated_timelimit": 20.0 if enabled else 0.0,
                        "s4_resume_reserve": 3.0 if enabled else 0.0,
                        "s4_branch_elapsed": 10.0 if enabled else 0.0,
                        "wall_seconds": 50.0 if enabled else 40.0,
                        "never_worse": True,
                        "z2_nonregression": True,
                        "cross_bay_move_accepted": int(enabled),
                        "cross_bay_swap_accepted": 0,
                        "unverified_return_count": 0,
                        "timeout": False,
                        "crash": False,
                    }
                )
        summary, exit_code = harness_cli._s4_ab_summary(
            records,
            selector="high-w23",
            expected_record_count=4,
            timelimits=(60.0,),
            seeds=(20260710,),
        )
        self.assertEqual(harness_cli.EXIT_PASS, exit_code)
        self.assertEqual(1, summary["improved_count"])
        self.assertEqual(0, summary["z2_regression_count"])
        self.assertEqual(0, summary["shared_budget_violation_count"])

        records[-1]["s4_allocated_timelimit"] = 60.0
        budget_summary, budget_exit = harness_cli._s4_ab_summary(
            records,
            selector="high-w23",
            expected_record_count=4,
            timelimits=(60.0,),
            seeds=(20260710,),
        )
        self.assertEqual(harness_cli.EXIT_GATE_FAILURE, budget_exit)
        self.assertEqual(1, budget_summary["shared_budget_violation_count"])
        records[-1]["s4_allocated_timelimit"] = 20.0

        records[-1]["checker"]["objective"] = 110.0
        regression_summary, regression_exit = harness_cli._s4_ab_summary(
            records,
            selector="high-w23",
            expected_record_count=4,
            timelimits=(60.0,),
            seeds=(20260710,),
        )
        self.assertEqual(harness_cli.EXIT_GATE_FAILURE, regression_exit)
        self.assertEqual(1, regression_summary["regression_count"])
        records[-1]["checker"]["objective"] = 90.0

        records[-1]["checker"]["z2"] = 11.0
        _summary, failed_exit = harness_cli._s4_ab_summary(
            records,
            selector="high-w23",
            expected_record_count=4,
            timelimits=(60.0,),
            seeds=(20260710,),
        )
        self.assertEqual(harness_cli.EXIT_GATE_FAILURE, failed_exit)

        records[-1]["checker"]["z2"] = 9.0
        records[-1]["s3_solution_sha256"] = "different-floor"
        mismatch_summary, mismatch_exit = harness_cli._s4_ab_summary(
            records,
            selector="high-w23",
            expected_record_count=4,
            timelimits=(60.0,),
            seeds=(20260710,),
        )
        self.assertEqual(harness_cli.EXIT_GATE_FAILURE, mismatch_exit)
        self.assertEqual(1, mismatch_summary["s3_floor_mismatch_count"])

        records[-1]["s3_solution_sha256"] = "s3-reverse"
        records[-1]["s3_checker"]["objective"] = 101.0
        objective_mismatch, objective_mismatch_exit = harness_cli._s4_ab_summary(
            records,
            selector="high-w23",
            expected_record_count=4,
            timelimits=(60.0,),
            seeds=(20260710,),
        )
        self.assertEqual(harness_cli.EXIT_GATE_FAILURE, objective_mismatch_exit)
        self.assertEqual(1, objective_mismatch["s3_floor_mismatch_count"])
        records[-1]["s3_checker"]["objective"] = 100.0

        records[-1]["s3_checker"]["z2"] = 11.0
        z2_mismatch, z2_mismatch_exit = harness_cli._s4_ab_summary(
            records,
            selector="high-w23",
            expected_record_count=4,
            timelimits=(60.0,),
            seeds=(20260710,),
        )
        self.assertEqual(harness_cli.EXIT_GATE_FAILURE, z2_mismatch_exit)
        self.assertEqual(1, z2_mismatch["s3_floor_mismatch_count"])
        records[-1]["s3_checker"]["z2"] = 10.0

        records[-1]["status"] = "checker_failed"
        _failed_summary, row_failure_exit = harness_cli._s4_ab_summary(
            records,
            selector="high-w23",
            expected_record_count=4,
            timelimits=(60.0,),
            seeds=(20260710,),
        )
        self.assertEqual(harness_cli.EXIT_GATE_FAILURE, row_failure_exit)

    def test_s4_ab_summary_uses_each_mixed_timelimit_pair_key(self):
        records = []
        for timelimit, shared_elapsed, remaining, reserve in (
            (60.0, 40.0, 20.0, 3.0),
            (300.0, 60.0, 240.0, 15.0),
        ):
            for order in ("forward", "reverse"):
                for enabled, objective, z2 in (
                    (False, 100.0, 10.0),
                    (True, 90.0, 9.0),
                ):
                    records.append(
                        {
                            "instance_id": "prob_mixed",
                            "timelimit": timelimit,
                            "seed": 20260710,
                            "run_label": order,
                            "arm_enabled": enabled,
                            "checker": {
                                "feasible": True,
                                "stage": 5,
                                "objective": objective,
                                "z2": z2,
                            },
                            "status": "passed",
                            "s3_solution_sha256": f"s3-{timelimit}-{order}",
                            "s3_checker": {
                                "feasible": True,
                                "stage": 5,
                                "objective": 100.0,
                                "z2": 10.0,
                            },
                            "pair_execution_id": f"pair-{timelimit}-{order}",
                            "s3_shared_across_arms": True,
                            "shared_s3_elapsed": shared_elapsed,
                            "s4_remaining_timelimit": remaining,
                            "s4_allocated_timelimit": remaining if enabled else 0.0,
                            "s4_resume_reserve": reserve if enabled else 0.0,
                            "s4_branch_elapsed": 10.0 if enabled else 0.0,
                            "wall_seconds": (
                                shared_elapsed + 10.0 if enabled else shared_elapsed
                            ),
                            "never_worse": True,
                            "z2_nonregression": True,
                            "cross_bay_move_accepted": int(enabled),
                            "cross_bay_swap_accepted": 0,
                            "unverified_return_count": 0,
                            "timeout": False,
                            "crash": False,
                        }
                    )

        summary, exit_code = harness_cli._s4_ab_summary(
            records,
            selector="high-w23",
            expected_record_count=8,
            timelimits=(60.0, 300.0),
            seeds=(20260710,),
        )

        self.assertEqual(harness_cli.EXIT_PASS, exit_code)
        self.assertEqual(0, summary["shared_budget_violation_count"])
        self.assertTrue(summary["all_feasible"])

    def test_paired_s4_ab_runs_s3_once_and_shares_verified_floor(self):
        prob_info, _state, incumbent = self._fixture()
        export = getattr(incumbent, "export_checkpoint", None)
        self.assertIsNotNone(export, "verified S3 checkpoint API is required")
        checkpoint = export()
        paired = getattr(harness_runner, "run_s4_integrated_pair", None)
        self.assertIsNotNone(paired, "paired S4 runner API is required")
        encoded = json.dumps(prob_info, sort_keys=True).encode()
        ref = InstanceRef(
            "paired_fixture",
            Path("/tmp/paired_fixture.json"),
            hashlib.sha256(encoded).hexdigest(),
            prob_info,
        )
        times = iter((100.0, 140.0, 150.0))
        observed_remaining = []

        def refine(
            _prob_info,
            shared_checkpoint,
            *,
            remaining_timelimit,
            original_timelimit,
            seed,
            backend_fault,
            telemetry,
        ):
            self.assertIs(checkpoint, shared_checkpoint)
            self.assertEqual(100.0, original_timelimit)
            self.assertEqual(20260710, seed)
            self.assertIsNone(backend_fault)
            observed_remaining.append(remaining_timelimit)
            telemetry["assignment_seed_attempts"] = []
            telemetry["s4_verified_solution_sha256"] = (
                shared_checkpoint.solution_sha256
            )
            telemetry["s4_verified_checker"] = asdict(
                shared_checkpoint.checker_result
            )
            telemetry["s4_worker_group_clean"] = False
            return shared_checkpoint.solution_copy()

        provenance = {
            "branch": "test",
            "commit": "0" * 40,
            "dirty": True,
            "dirty_diff_hash": "1" * 64,
        }
        with (
            patch(
                "harness.runner.solve_to_s3_checkpoint",
                return_value=checkpoint,
            ) as run_s3,
            patch("harness.runner.refine_s4_checkpoint", side_effect=refine),
            patch("harness.runner.repository_provenance", return_value=provenance),
        ):
            records = paired(
                ref,
                selector="synthetic",
                timelimit=100.0,
                seed=20260710,
                features={"feature": "assignment_refinement"},
                run_label="forward",
                arm_order=(False, True),
                _clock=lambda: next(times),
            )

        self.assertEqual(1, run_s3.call_count)
        self.assertEqual([60.0], observed_remaining)
        self.assertEqual([False, True], [row["arm_enabled"] for row in records])
        self.assertEqual(
            records[0]["s3_solution_sha256"],
            records[1]["s3_solution_sha256"],
        )
        self.assertEqual(
            records[0]["s3_checker"]["objective"],
            records[1]["s3_checker"]["objective"],
        )
        self.assertEqual(
            records[0]["s3_checker"]["z2"],
            records[1]["s3_checker"]["z2"],
        )
        self.assertEqual(40.0, records[0]["wall_seconds"])
        self.assertEqual(50.0, records[1]["wall_seconds"])
        self.assertEqual("checker_failed", records[1]["status"])

    def test_forward_reverse_reconstruct_isolated_branches_and_rng(self):
        prob_info, _state, incumbent = self._fixture()
        export = getattr(incumbent, "export_checkpoint", None)
        resume = getattr(solver_entry, "_refine_s4_checkpoint_core", None)
        self.assertIsNotNone(export, "verified S3 checkpoint API is required")
        self.assertIsNotNone(resume, "S4 checkpoint resume API is required")
        checkpoint = export()
        seen = []

        def assignment_seed(
            _instance,
            state,
            branch_incumbent,
            _budget,
            **_kwargs,
        ):
            seen.append((id(state), id(branch_incumbent), id(state.geom)))
            return state

        forward_telemetry = {}
        reverse_telemetry = {}
        with (
            patch("solver.entry._run_assignment_seed", side_effect=assignment_seed),
            patch("solver.entry.rank_cross_bay_candidates", return_value=()),
            patch("solver.entry.Generator", wraps=Generator) as generators,
        ):
            forward_checkpoint = resume(
                prob_info,
                checkpoint,
                cooperative_return_deadline=time.monotonic() + 60.0,
                seed=20260710,
                assignment_v2_enabled=True,
                cross_bay_enabled=True,
                backend_fault=None,
                telemetry=forward_telemetry,
            )
            reverse_checkpoint = resume(
                prob_info,
                checkpoint,
                cooperative_return_deadline=time.monotonic() + 60.0,
                seed=20260710,
                assignment_v2_enabled=True,
                cross_bay_enabled=True,
                backend_fault=None,
                telemetry=reverse_telemetry,
            )
            forward = forward_checkpoint.solution_copy()
            reverse = reverse_checkpoint.solution_copy()

        self.assertEqual(2, len(seen))
        self.assertNotEqual(seen[0][0], seen[1][0])
        self.assertNotEqual(seen[0][1], seen[1][1])
        self.assertNotEqual(seen[0][2], seen[1][2])
        self.assertEqual(2, generators.call_count)
        self.assertEqual(forward, reverse)
        forward_telemetry["branch_mutation"] = True
        self.assertNotIn("branch_mutation", reverse_telemetry)

    def test_checkpoint_and_recorded_s3_floor_survive_branch_mutation(self):
        _prob_info, state, incumbent = self._fixture()
        export = getattr(incumbent, "export_checkpoint", None)
        restore = getattr(VerifiedIncumbent, "from_checkpoint", None)
        self.assertIsNotNone(export, "verified S3 checkpoint API is required")
        self.assertIsNotNone(restore, "verified branch reconstruction is required")
        checkpoint = export()
        expected_sha = checkpoint.solution_sha256
        expected_objective = checkpoint.checker_result.objective

        mutated = checkpoint.solution_copy()
        mutated.clear()
        branch = restore(state.instance, checkpoint)
        branch.solution["branch_only"] = []
        telemetry = {"s4_input_solution": checkpoint.solution_copy()}
        telemetry["s4_input_solution"].clear()

        self.assertEqual(expected_sha, checkpoint.solution_sha256)
        self.assertEqual(expected_objective, checkpoint.checker_result.objective)
        self.assertEqual(expected_sha, self._solution_sha(checkpoint.solution_copy()))
        self.assertNotIn("branch_only", checkpoint.solution_copy())

    def test_checkpoint_materialization_preserves_numeric_operation_order(self):
        _prob_info, _parsed, state, _incumbent, checkpoint = (
            self._multidigit_checkpoint_fixture()
        )
        original = serialize_non_interlock(state.placements.values())
        identity = json.loads(checkpoint.solution_json)

        self.assertEqual(["96", "105"], list(original["operations"]))
        self.assertEqual(["105", "96"], list(identity["operations"]))
        materialized = checkpoint.solution_copy()
        self.assertEqual(["96", "105"], list(materialized["operations"]))
        self.assertEqual(original, materialized)
        self.assertEqual(
            checkpoint.solution_sha256,
            self._solution_sha(materialized),
        )

    def test_checkpoint_roundtrip_rechecks_exactly_with_multidigit_dates(self):
        prob_info, parsed, state, incumbent, checkpoint = (
            self._multidigit_checkpoint_fixture()
        )
        original = serialize_non_interlock(state.placements.values())
        original_checked = official_check(prob_info, original)
        materialized_checked = official_check(prob_info, checkpoint.solution_copy())

        self.assertEqual(incumbent.checker_result, original_checked)
        self.assertEqual(original_checked, materialized_checked)
        self.assertTrue(materialized_checked.feasible)
        self.assertEqual(5, materialized_checked.stage)
        self.assertEqual(5.0, materialized_checked.obj1)
        self.assertEqual(3.0, materialized_checked.obj2)
        self.assertEqual(10.0, materialized_checked.obj3)
        self.assertEqual(59.0, materialized_checked.objective)

        restored = VerifiedIncumbent.from_checkpoint(parsed, checkpoint)
        self.assertEqual(original_checked, restored.checker_result)
        self.assertEqual(original, restored.solution)
        self.assertEqual(checkpoint.verification_count + 1, restored.verification_count)

    def test_checkpoint_materializations_are_branch_isolated(self):
        _prob_info, _parsed, state, _incumbent, checkpoint = (
            self._multidigit_checkpoint_fixture()
        )
        expected = serialize_non_interlock(state.placements.values())
        first = checkpoint.solution_copy()
        second = checkpoint.solution_copy()

        self.assertIsNot(first, second)
        self.assertIsNot(first["operations"], second["operations"])
        self.assertIsNot(first["operations"]["96"], second["operations"]["96"])
        self.assertIsNot(
            first["operations"]["96"][0],
            second["operations"]["96"][0],
        )
        first["operations"]["96"][0]["x"] = 99
        first["operations"]["105"].clear()
        first["branch_only"] = True

        self.assertEqual(expected, second)
        self.assertEqual(expected, checkpoint.solution_copy())
        self.assertNotIn("branch_only", checkpoint.solution_copy())

    def test_checkpoint_corruption_guards_survive_materialization_change(self):
        prob_info, _parsed, _state, _incumbent, checkpoint = (
            self._multidigit_checkpoint_fixture()
        )

        corrupt_bytes = replace(
            checkpoint,
            solution_json=checkpoint.solution_json + b" ",
        )
        with self.assertRaisesRegex(ValueError, "solution hash mismatch"):
            corrupt_bytes.solution_copy()

        corrupt_sha = replace(checkpoint, solution_sha256="0" * 64)
        with self.assertRaisesRegex(ValueError, "solution hash mismatch"):
            corrupt_sha.solution_copy()

        corrupt_placement = replace(
            checkpoint,
            placements=(replace(checkpoint.placements[0], entry=95),),
        )
        with self.assertRaisesRegex(ValueError, "placement snapshot mismatch"):
            corrupt_placement.solution_copy()

        wrong_instance = instance(
            [
                block(
                    release=96,
                    due=100,
                    processing=9,
                    workload=4,
                    preferences=(0, 10),
                )
            ],
            bays=((4, 4), (4, 4)),
            weights={"w1": 2.0, "w2": 3.0, "w3": 4.0},
        )
        with self.assertRaisesRegex(ValueError, "different instance"):
            VerifiedIncumbent.from_checkpoint(wrong_instance, checkpoint)

        checker_result = checkpoint.checker_result
        self.assertIsNotNone(checker_result.objective)
        corrupt_checker = replace(
            checkpoint,
            checker_result=replace(
                checker_result,
                objective=checker_result.objective + 1.0,
            ),
        )
        with self.assertRaisesRegex(ValueError, "checker result mismatch"):
            VerifiedIncumbent.from_checkpoint(prob_info, corrupt_checker)


class AssignmentV2Tests(unittest.TestCase):
    def test_cpsat_adaptive_scale_preserves_positive_coefficients(self):
        prob_info = instance(
            [
                block(workload=1.0, preferences=(1, 1)),
                block(workload=1.0, preferences=(1, 1)),
            ],
            bays=((8, 8), (8, 8)),
            weights={"w1": 1.0, "w2": 1e8, "w3": 100.0},
        )
        parsed = ProblemInstance.parse(prob_info)
        starting = assignment_from_solution(
            parsed,
            ((0, 0), (1, 1)),
            order=(0, 1),
        )
        request = assignment_request(
            parsed,
            starting,
            config=SolverConfig(assignment_threads=1),
        )

        try:
            spec = build_cpsat_assignment_spec(request, timebox=2.0)
        except OverflowError as exc:
            self.fail(f"representable adaptive coefficient scale was rejected: {exc}")

        raw_coefficients = (
            *request.weights,
            request.congestion_weight,
        )
        quantized_coefficients = (
            spec.w2,
            spec.w3,
            spec.congestion_weight,
        )
        self.assertEqual(ASSIGNMENT_SCALE, spec.scale)
        self.assertEqual(50_000_001, spec.coefficient_scale)
        self.assertEqual(100_000_000.0, spec.objective_normalizer)
        self.assertEqual((50_000_001, 50, 1), quantized_coefficients)
        self.assertTrue(
            all(
                raw <= 0.0 or quantized > 0
                for raw, quantized in zip(
                    raw_coefficients,
                    quantized_coefficients,
                    strict=True,
                )
            )
        )
        self.assertEqual(2_000_000, max(dict(spec.bay_load_upper_bounds).values()))
        self.assertEqual(0, spec.z3_upper_bound)
        self.assertEqual(0, spec.overload_upper_bound)
        self.assertEqual(100_000_002_000_000, spec.objective_upper_bound)
        self.assertLess(spec.objective_upper_bound, 1 << 62)
        self.assertAlmostEqual(
            200_000_000.0,
            spec.objective_upper_bound * spec.objective_unscale_factor,
        )
        self.assertEqual(
            spec.objective_normalizer
            / (spec.scale * spec.coefficient_scale),
            spec.objective_unscale_factor,
        )
        self.assertNotEqual(
            spec.objective_normalizer / (spec.scale * spec.scale),
            spec.objective_unscale_factor,
        )

    def test_gurobi_float_z2_matches_checker(self):
        prob_info = instance(
            [
                block(workload=0.10, preferences=(9, 2, 1)),
                block(workload=0.20, preferences=(8, 3, 1)),
                block(workload=0.30, preferences=(2, 9, 1)),
                block(workload=0.40, preferences=(1, 8, 3)),
                block(workload=0.55, preferences=(1, 2, 9)),
                block(workload=0.65, preferences=(3, 1, 8)),
            ],
            bays=((8, 8), (10, 8), (12, 8)),
            weights={"w1": 5.0, "w2": 17.25, "w3": 0.75},
        )
        parsed = ProblemInstance.parse(prob_info)
        v1 = AssignmentV1(parsed).assign()
        config = SolverConfig(
            assignment_timebox_seconds=2.0,
            assignment_threads=1,
        )

        request = assignment_request(parsed, v1, config=config)
        self.assertIsInstance(request, AssignmentRequest)
        spec = build_gurobi_assignment_spec(
            request,
            timebox=config.assignment_timebox_seconds,
        )

        average_area = sum(bay.area for bay in parsed.bays) / len(parsed.bays)
        expected_factors = tuple(average_area / bay.area for bay in parsed.bays)
        self.assertEqual(
            tuple(item.load_factor for item in spec.bays),
            expected_factors,
        )
        self.assertEqual(spec.range_variable.lower_bound, 0.0)
        self.assertGreater(spec.range_variable.upper_bound, 0.0)
        self.assertTrue(
            all(
                item.start
                == int(v1.assignments[item.block_id].bay_id == item.bay_id)
                for item in spec.binary_variables
            )
        )
        self.assertTrue(
            all(
                isinstance(value, float)
                for value in (
                    spec.w2,
                    spec.w3,
                    spec.congestion_weight,
                    *(item.load_factor for item in spec.bays),
                    *(item.workload for item in spec.binary_variables),
                )
            )
        )

        v1_constructed = harness_runner.construct_fixed_assignment(parsed, v1)
        v1_checked = official_check(
            prob_info,
            serialize_non_interlock(v1_constructed.placements.values()),
        )
        self.assertTrue(v1_checked.feasible, v1_checked.violations)
        self.assertEqual(v1_checked.stage, 5)
        self.assertTrue(
            math.isclose(v1_checked.obj2, v1.z2, rel_tol=1e-6, abs_tol=1e-9)
        )
        self.assertTrue(
            math.isclose(v1_checked.obj3, v1.z3, rel_tol=1e-6, abs_tol=1e-9)
        )

        solved = assignment_v2(parsed, v1, config=config)
        if solved.status not in SOLUTION_STATUSES:
            self.assertIsNone(solved.solution)
            self.assertIn(solved.status, {"unavailable", "time_limit", "no_solution"})
            return

        self.assertIsInstance(solved.solution, tuple)
        proposed = assignment_from_solution(
            parsed,
            solved.solution,
            order=v1.order,
        )
        constructed = harness_runner.construct_fixed_assignment(parsed, proposed)
        checked = official_check(
            prob_info,
            serialize_non_interlock(constructed.placements.values()),
        )

        self.assertTrue(checked.feasible, checked.violations)
        self.assertEqual(checked.stage, 5)
        self.assertIsNotNone(solved.z2)
        self.assertIsNotNone(solved.z3)
        self.assertTrue(
            math.isclose(checked.obj2, solved.z2, rel_tol=1e-6, abs_tol=1e-9),
            (checked.obj2, solved.z2),
        )
        self.assertTrue(
            math.isclose(checked.obj3, solved.z3, rel_tol=1e-6, abs_tol=1e-9),
            (checked.obj3, solved.z3),
        )

    def test_fixed_assignment_construction_never_falls_back_to_another_bay(self):
        prob_info = instance(
            [
                block(
                    processing=1,
                    workload=1,
                    preferences=(100, 0),
                )
                for _ in range(18)
            ],
            bays=((1, 1), (1, 1)),
        )
        parsed = ProblemInstance.parse(prob_info)
        proposed = assignment_from_solution(
            parsed,
            tuple((block_id, 0) for block_id in range(18)),
            order=tuple(range(18)),
        )

        fallback_constructed = construct_profile(parsed, proposed, "PF3")
        self.assertTrue(
            any(
                placement.bay_id != proposed.assignments[placement.block_id].bay_id
                for placement in fallback_constructed.state.placements.values()
            ),
            "fixture must exercise the recorded constructor fallback",
        )

        constructed = harness_runner.construct_fixed_assignment(parsed, proposed)
        self.assertEqual(
            tuple((block_id, 0) for block_id in range(18)),
            tuple(
                sorted(
                    (placement.block_id, placement.bay_id)
                    for placement in constructed.placements.values()
                )
            ),
        )
        checked = official_check(
            prob_info,
            serialize_non_interlock(constructed.placements.values()),
        )
        self.assertTrue(checked.feasible, checked.violations)
        self.assertEqual(5, checked.stage)
        self.assertTrue(
            math.isclose(checked.obj2, proposed.z2, rel_tol=1e-6, abs_tol=1e-9)
        )
        self.assertTrue(
            math.isclose(checked.obj3, proposed.z3, rel_tol=1e-6, abs_tol=1e-9)
        )

    def test_checker_roundoff_contact_is_avoided_by_fixed_construction(self):
        first = (
            (0.0, 0.0),
            (-3.0889, -3.0889),
            (-6.1777, -6.1777),
            (-7.7222, -7.7222),
            (-3.9991, -11.4452),
            (-2.4547, -9.9008),
            (0.6342, -6.8119),
            (3.0421, -7.0361),
            (3.1135, -3.1135),
        )
        second_lower = (
            (0.0, 0.0),
            (-3.9335, -3.9335),
            (-7.8669, -7.8669),
            (-7.084, -0.783),
            (-3.1505, 3.1505),
        )
        second_upper = (
            (0.0, 0.0),
            (-3.9335, -3.9335),
            (-7.8669, -7.8669),
            (-7.084, -0.783),
            (-10.2345, 2.3675),
            (-6.301, 6.301),
            (-3.1505, 3.1505),
        )
        prob_info = instance(
            [
                block(
                    layers=(first,),
                    release=52,
                    due=100,
                    processing=15,
                ),
                block(
                    layers=(second_lower, second_upper),
                    release=52,
                    due=100,
                    processing=12,
                ),
            ],
            bays=((80, 80),),
        )
        parsed = ProblemInstance.parse(prob_info)
        unsafe = (
            Placement(0, 0, 46, 12, 0, 52, 67),
            Placement(1, 0, 50, 16, 0, 52, 64),
        )
        unsafe_state = SolutionState(parsed)
        self.assertTrue(validate_pair(unsafe_state, unsafe[0], unsafe[1]))
        rejected = official_check(prob_info, serialize_non_interlock(unsafe))
        self.assertFalse(rejected.feasible)
        self.assertEqual(2, rejected.stage)

        proposed = assignment_from_solution(parsed, ((0, 0), (1, 0)), order=(0, 1))
        constructed = harness_runner.construct_fixed_assignment(parsed, proposed)
        checked = official_check(
            prob_info,
            serialize_non_interlock(constructed.placements.values()),
        )
        self.assertTrue(checked.feasible, checked.violations)
        self.assertEqual(5, checked.stage)

    def test_failed_checker_summary_is_null_safe_and_nonzero(self):
        failed = {
            "status": "checker_failed",
            "checker": {"feasible": False, "stage": 2},
            "v1_checker": {"feasible": False, "stage": 2},
            "backend": {"name": "gurobi", "status": "optimal"},
            "assigned": 2,
            "block_count": 2,
            "v1_membership_preserved": True,
            "v2_membership_preserved": True,
            "v1_float_z2_relative_error": None,
            "v1_float_z3_relative_error": None,
            "v2_float_z2_relative_error": None,
            "v2_float_z3_relative_error": None,
            "wall_seconds": 0.1,
            "timelimit": 60.0,
        }
        summary, exit_code = harness_cli._assignment_v2_summary(
            [failed],
            expected_record_count=1,
            selector="high-w23",
            timelimits=(60.0,),
            seeds=(20260710,),
            features={"assignment_backend": "gurobi"},
        )

        self.assertEqual("failed", summary["status"])
        self.assertIsNone(summary["max_v1_float_z2_relative_error"])
        self.assertIsNone(summary["max_v2_float_z2_relative_error"])
        self.assertEqual(harness_cli.EXIT_CHECKER_FAILURE, exit_code)


class AssignmentFallbackTests(unittest.TestCase):
    def test_scaled_candidate_rechecked_as_float(self):
        prob_info = instance(
            [
                block(workload=1.0000004, preferences=(1, 1)),
                block(workload=1.0000004, preferences=(1, 1)),
            ],
            bays=((8, 8), (8, 8)),
            weights={"w1": 1.0, "w2": 100.0, "w3": 1.0},
        )
        parsed = ProblemInstance.parse(prob_info)
        incumbent = assignment_from_solution(
            parsed,
            ((0, 0), (1, 1)),
            order=(0, 1),
        )

        def failed_gurobi(request, timebox):
            del request, timebox
            return ExactAssignmentResult(
                backend="gurobi",
                status="error",
                solution=None,
                objective=None,
                bound=None,
                z2=None,
                z3=None,
                overload=None,
                build_s=0.0,
                solve_s=0.0,
                first_solution_s=None,
                reason="injected Gurobi fault",
            )

        def scaled_cpsat(request, timebox):
            del request, timebox
            return ExactAssignmentResult(
                backend="cpsat",
                status="optimal",
                solution=((0, 0), (1, 0)),
                objective=-1.0,
                bound=-1.0,
                z2=0.0,
                z3=0.0,
                overload=0.0,
                build_s=0.0,
                solve_s=0.0,
                first_solution_s=0.0,
                reason=None,
            )

        selected = choose_assignment_candidate(
            parsed,
            incumbent,
            config=SolverConfig(
                assignment_timebox_seconds=2.0,
                assignment_threads=1,
            ),
            backend_calls={
                "gurobi": failed_gurobi,
                "cpsat": scaled_cpsat,
            },
        )

        self.assertEqual("greedy", selected.backend)
        self.assertEqual(incumbent, selected.assignment)
        self.assertEqual(0.0, selected.evaluation.z2)
        self.assertIn("gurobi:error", selected.fallback_reason)
        self.assertIn("cpsat:float_z2_regression", selected.fallback_reason)

    def test_cpsat_unrepresentable_objective_scale_falls_back(self):
        prob_info = instance(
            [block(workload=1.0, preferences=(1, 1))],
            bays=((8, 8), (8, 8)),
            weights={"w1": 1.0, "w2": 1e20, "w3": 1.0},
        )
        parsed = ProblemInstance.parse(prob_info)
        incumbent = assignment_from_solution(
            parsed,
            ((0, 0),),
            order=(0,),
        )
        request = assignment_request(
            parsed,
            incumbent,
            config=SolverConfig(assignment_threads=1),
        )
        unavailable = assign_cpsat(request, 2.0)
        self.assertEqual("unavailable", unavailable.status)
        self.assertIsNone(unavailable.solution)
        self.assertIsNone(unavailable.objective)
        self.assertIsNone(unavailable.bound)
        self.assertIn("unsafe CP-SAT assignment", unavailable.reason)
        self.assertIn("objective", unavailable.reason)

        constructed = harness_runner.construct_fixed_assignment(parsed, incumbent)
        checked = official_check(
            prob_info,
            serialize_non_interlock(constructed.placements.values()),
        )
        self.assertTrue(checked.feasible, checked.violations)
        self.assertEqual(5, checked.stage)

        def failed_gurobi(_request, _timebox):
            return ExactAssignmentResult(
                backend="gurobi",
                status="error",
                solution=None,
                objective=None,
                bound=None,
                z2=None,
                z3=None,
                overload=None,
                build_s=0.0,
                solve_s=0.0,
                first_solution_s=None,
                reason="injected Gurobi fault",
            )

        def verify(candidate):
            candidate_state = harness_runner.construct_fixed_assignment(
                parsed,
                candidate,
            )
            candidate_check = official_check(
                prob_info,
                serialize_non_interlock(candidate_state.placements.values()),
            )
            return AssignmentVerification(
                feasible=candidate_check.feasible and candidate_check.stage == 5,
                objective=candidate_check.objective,
                reason=(
                    None
                    if candidate_check.feasible
                    else "; ".join(candidate_check.violations)
                ),
            )

        selected = choose_assignment_candidate(
            parsed,
            incumbent,
            config=SolverConfig(assignment_threads=1),
            backend_calls={
                "gurobi": failed_gurobi,
                "cpsat": assign_cpsat,
            },
            verify=verify,
        )
        self.assertEqual("greedy", selected.backend)
        self.assertEqual(incumbent, selected.assignment)
        self.assertTrue(selected.verified)
        self.assertEqual(checked.objective, selected.checker_objective)
        self.assertIn("gurobi:error", selected.fallback_reason)
        self.assertIn("cpsat:unavailable", selected.fallback_reason)

    def test_gurobi_fault_uses_cpsat_and_both_faults_keep_greedy(self):
        prob_info = instance(
            [
                block(workload=1.0, preferences=(1, 1)),
                block(workload=1.0, preferences=(1, 1)),
            ],
            bays=((8, 8), (8, 8)),
            weights={"w1": 1.0, "w2": 100.0, "w3": 1.0},
        )
        parsed = ProblemInstance.parse(prob_info)
        incumbent = assignment_from_solution(
            parsed,
            ((0, 0), (1, 0)),
            order=(0, 1),
        )

        def verify(candidate):
            constructed = harness_runner.construct_fixed_assignment(parsed, candidate)
            checked = official_check(
                prob_info,
                serialize_non_interlock(constructed.placements.values()),
            )
            return AssignmentVerification(
                feasible=checked.feasible and checked.stage == 5,
                objective=checked.objective,
                reason=None if checked.feasible else "; ".join(checked.violations),
            )

        def fault(backend, reason):
            def call(request, timebox):
                del request, timebox
                return ExactAssignmentResult(
                    backend=backend,
                    status="error",
                    solution=None,
                    objective=None,
                    bound=None,
                    z2=None,
                    z3=None,
                    overload=None,
                    build_s=0.0,
                    solve_s=0.0,
                    first_solution_s=None,
                    reason=reason,
                )

            return call

        def improving_cpsat(request, timebox):
            del request, timebox
            return ExactAssignmentResult(
                backend="cpsat",
                status="optimal",
                solution=((0, 0), (1, 1)),
                objective=0.0,
                bound=0.0,
                z2=0.0,
                z3=0.0,
                overload=0.0,
                build_s=0.0,
                solve_s=0.0,
                first_solution_s=0.0,
                reason=None,
            )

        cpsat_selected = choose_assignment_candidate(
            parsed,
            incumbent,
            config=SolverConfig(assignment_threads=1),
            backend_calls={
                "gurobi": fault("gurobi", "injected Gurobi fault"),
                "cpsat": improving_cpsat,
            },
            verify=verify,
        )
        self.assertEqual("cpsat", cpsat_selected.backend)
        self.assertTrue(cpsat_selected.verified)
        self.assertEqual(0.0, cpsat_selected.evaluation.z2)
        self.assertIn("gurobi:error", cpsat_selected.fallback_reason)

        greedy_selected = choose_assignment_candidate(
            parsed,
            incumbent,
            config=SolverConfig(assignment_threads=1),
            backend_calls={
                "gurobi": fault("gurobi", "injected Gurobi fault"),
                "cpsat": fault("cpsat", "injected CP-SAT fault"),
            },
            verify=verify,
        )
        self.assertEqual("greedy", greedy_selected.backend)
        self.assertEqual(incumbent, greedy_selected.assignment)
        self.assertTrue(greedy_selected.verified)
        self.assertIn("gurobi:error", greedy_selected.fallback_reason)
        self.assertIn("cpsat:error", greedy_selected.fallback_reason)

    def test_cpsat_assignment_model_and_unsafe_scaling_fallback(self):
        prob_info = instance(
            [
                block(workload=1.0, preferences=(1, 1)),
                block(workload=1.0, preferences=(1, 1)),
            ],
            bays=((8, 8), (8, 8)),
            weights={"w1": 1.0, "w2": 100.0, "w3": 1.0},
        )
        parsed = ProblemInstance.parse(prob_info)
        incumbent = assignment_from_solution(
            parsed,
            ((0, 0), (1, 0)),
            order=(0, 1),
        )
        request = assignment_request(
            parsed,
            incumbent,
            config=SolverConfig(assignment_threads=1),
        )
        spec = build_cpsat_assignment_spec(request, timebox=2.0)
        self.assertEqual(ASSIGNMENT_SCALE, spec.scale)
        self.assertLess(spec.objective_upper_bound, 1 << 62)
        self.assertTrue(
            all(
                pair.start == int(dict(request.current_assignment)[pair.block_id] == pair.bay_id)
                for pair in spec.pairs
            )
        )

        solved = assign_cpsat(request, 2.0)
        self.assertIn(solved.status, SOLUTION_STATUSES)
        self.assertIsNotNone(solved.solution)
        self.assertEqual({0, 1}, {bay_id for _block_id, bay_id in solved.solution})

        unsafe_prob = instance(
            [block(workload=1.0, preferences=(1, 1))],
            bays=((8, 8), (8, 8)),
            weights={"w1": 1.0, "w2": 1e20, "w3": 1.0},
        )
        unsafe_parsed = ProblemInstance.parse(unsafe_prob)
        unsafe_incumbent = assignment_from_solution(
            unsafe_parsed,
            ((0, 0),),
            order=(0,),
        )

        def failed_gurobi(request, timebox):
            del request, timebox
            return ExactAssignmentResult(
                backend="gurobi",
                status="error",
                solution=None,
                objective=None,
                bound=None,
                z2=None,
                z3=None,
                overload=None,
                build_s=0.0,
                solve_s=0.0,
                first_solution_s=None,
                reason="injected Gurobi fault",
            )

        unsafe_selected = choose_assignment_candidate(
            unsafe_parsed,
            unsafe_incumbent,
            config=SolverConfig(assignment_threads=1),
            backend_calls={"gurobi": failed_gurobi, "cpsat": assign_cpsat},
        )
        self.assertEqual("greedy", unsafe_selected.backend)
        self.assertEqual(unsafe_incumbent, unsafe_selected.assignment)
        self.assertIn("cpsat:unavailable", unsafe_selected.fallback_reason)

        unsafe_demand_request = replace(
            request,
            congestion_demands=tuple(
                (block_id, bay_id, 3e12)
                for block_id, bay_id in request.fit_pairs
            ),
            congestion_capacities=tuple(
                (bay_id, 4e12) for bay_id in request.bay_ids
            ),
            congestion_weight=0.0,
        )
        unsafe_demand = assign_cpsat(unsafe_demand_request, 2.0)
        self.assertEqual("unavailable", unsafe_demand.status)
        self.assertIn("unsafe CP-SAT assignment scaling", unsafe_demand.reason)


if __name__ == "__main__":
    unittest.main()
