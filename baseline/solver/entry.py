"""Verified-incumbent entry shell with calibrated S1 constructor armor."""

from __future__ import annotations

from collections.abc import Callable
from contextlib import contextmanager
from dataclasses import asdict, dataclass, replace
import hashlib
import json
import math
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import threading
import time
from typing import Any, Iterator

from numpy.random import Generator, PCG64

from .alns import (
    CrossBayRegistry,
    RetimeTrigger,
    RetimeWallPolicy,
    StagnationController,
    rank_cross_bay_candidates,
    run_anytime_epochs,
    run_cross_bay_candidate,
)
from .assign import (
    AssignmentVerification,
    assignment_from_state,
    choose_assignment_candidate,
)
from .budget import Budget, BudgetExpired, deadline_reserve
from .checker_adapter import CheckerResult, official_check
from .config import DEFAULT_CONFIG
from .construct import construct_multistart, insert_block
from .cpsat_backend import assign_cpsat, retime_cpsat
from .exact import AssignmentResult as ExactAssignmentResult
from .gurobi_backend import assign_gurobi, retime_gurobi
from .incumbent import VerifiedCheckpoint, VerifiedIncumbent
from .instance import ProblemInstance
from .retime import retime_bay, retime_sweep
from .serialize import serialize_non_interlock
from .state import Placement, SolutionState
from .trivial import build_t0


class InjectedEntryFault(RuntimeError):
    """Test-only fault raised after a verified incumbent exists."""


FaultHook = str | Callable[[str], None] | None


_S4_WORKER_SCHEMA_VERSION = 2
_S4_WORKER_RETURN_MARGIN_SECONDS = 2.5
_S4_PARENT_FINALIZATION_TAIL_SECONDS = 0.5
_S4_MINIMUM_USEFUL_WORK_SECONDS = (
    2.0 * DEFAULT_CONFIG.assignment_timebox_seconds + 0.25
)
_S4_WORKER_AUTHORITATIVE_TELEMETRY = frozenset(
    {
        "s4_worker_status",
        "s4_worker_wall_seconds",
        "s4_worker_exit_code",
        "s4_worker_signal",
        "s4_worker_term_sent",
        "s4_worker_kill_sent",
        "s4_worker_group_clean",
        "s4_hard_timeout_fallback",
        "s4_worker_fallback_reason",
        "s4_worker_launched",
        "s4_skip_reason",
        "s4_verified_checker",
        "s4_verified_solution_sha256",
    }
)


@dataclass(frozen=True, slots=True)
class _S4WorkerProcessResult:
    pid: int
    exit_code: int | None
    signal: int | None
    wall_seconds: float
    timed_out: bool
    term_sent: bool
    kill_sent: bool
    group_clean: bool
    error: str | None = None


class _S4ParentDeadlineExpired(TimeoutError):
    """Raised when parent-side publication work reaches its hard deadline."""


def solve(
    prob_info: dict[str, Any],
    timelimit: float = 60,
    *,
    _fault: FaultHook = None,
    _constructor: bool | None = None,
    _retime: bool | None = None,
    _seed: int | None = None,
    _retime_timebox: float | None = None,
    _retime_pilot: float | None = None,
    _alns: bool | None = None,
    _alns_fault: str | None = None,
    _assignment_refinement: bool | None = None,
    _assignment_v2: bool | None = None,
    _cross_bay: bool | None = None,
    _assignment_backend_fault: str | None = None,
    _telemetry: dict[str, Any] | None = None,
    _s3_checkpoint_out: dict[str, VerifiedCheckpoint] | None = None,
    _stop_after_s3: bool = False,
) -> dict[str, Any]:
    """Build T0, optionally improve it, and return only a verified incumbent.

    Failures before initial checker verification still propagate because no
    safe return object exists.  ``KeyboardInterrupt`` and ``SystemExit`` are
    intentionally outside the ordinary ``Exception`` armor.
    """
    incumbent: VerifiedIncumbent | None = None
    try:
        budget = Budget(timelimit)
        instance = ProblemInstance.parse(prob_info)
        incumbent = VerifiedIncumbent(instance)
        t0_started = time.monotonic()
        current_state = build_t0(instance)
        incumbent.register_initial(current_state)
        if _telemetry is not None:
            _telemetry["t0_and_verify_seconds"] = time.monotonic() - t0_started

        _inject_fault(_fault, "after_incumbent")
        constructor_enabled = (
            DEFAULT_CONFIG.constructor if _constructor is None else _constructor
        )
        if constructor_enabled:
            _inject_fault(_fault, "during_constructor")
            constructor_started = time.monotonic()
            remaining = budget.hard_remaining
            reserve = min(
                DEFAULT_CONFIG.constructor_return_reserve_seconds,
                max(remaining - 0.05, 0.0),
            )
            constructor_budget = Budget(remaining, reserve=reserve)
            constructed = construct_multistart(
                instance,
                incumbent,
                constructor_budget,
                seed=(DEFAULT_CONFIG.constructor_seed if _seed is None else _seed),
                profiles=DEFAULT_CONFIG.constructor_profiles,
                biased_variants=False,
                calibrated_entry=True,
                time_cap=DEFAULT_CONFIG.constructor_time_cap,
                anchor_cap=DEFAULT_CONFIG.constructor_anchor_cap,
            )
            if constructed.metrics.incumbent_updated:
                current_state = constructed.state
            if _telemetry is not None:
                _telemetry["constructor_seconds"] = (
                    time.monotonic() - constructor_started
                )
                _telemetry["constructor_updated"] = (
                    constructed.metrics.incumbent_updated
                )
        budget.checkpoint("post-constructor pipeline")

        alns_enabled = DEFAULT_CONFIG.alns if _alns is None else _alns
        refinement_enabled = (
            DEFAULT_CONFIG.assignment_refinement
            if _assignment_refinement is None
            else _assignment_refinement
        )
        assignment_v2_enabled = refinement_enabled and (
            DEFAULT_CONFIG.assignment_v2
            if _assignment_v2 is None
            else _assignment_v2
        )
        cross_bay_enabled = refinement_enabled and (
            DEFAULT_CONFIG.cross_bay if _cross_bay is None else _cross_bay
        )
        backend_calls = {
            "gurobi": retime_gurobi,
            "cpsat": retime_cpsat,
        }
        available = (
            ("gurobi", "cpsat")
            if DEFAULT_CONFIG.retime_backend == "auto"
            else (DEFAULT_CONFIG.retime_backend,)
        )
        alns_retime_backend = available[0]
        retime_enabled = DEFAULT_CONFIG.exact_retime if _retime is None else _retime
        if retime_enabled and budget.remaining > 0.0:
            _inject_fault(_fault, "during_retime")
            retime_started = time.monotonic()
            before_retime_z1 = incumbent.checker_result.obj1
            timebox = (
                DEFAULT_CONFIG.retime_timebox_seconds
                if _retime_timebox is None
                else float(_retime_timebox)
            )
            pilot_budget = (
                DEFAULT_CONFIG.retime_pilot_seconds
                if _retime_pilot is None
                else float(_retime_pilot)
            )
            exact_allowance = (
                min(budget.remaining, 5.0) if alns_enabled else budget.remaining
            )
            exact_budget = Budget(exact_allowance, reserve=0.0)
            initial = retime_sweep(
                current_state,
                incumbent,
                backend_calls,
                available_backends=available,
                budget=exact_budget,
                first_sweep_budget=exact_allowance,
                seed=(DEFAULT_CONFIG.constructor_seed if _seed is None else _seed),
                threads=DEFAULT_CONFIG.retime_threads,
                call_timebox_cap=timebox,
                pilot_budget_cap=pilot_budget,
                require_certified_optimal=alns_enabled,
                fixed_call_timebox=(0.10 * exact_allowance if alns_enabled else None),
            )
            current_state = initial.state
            if initial.pilot.backend is not None:
                alns_retime_backend = initial.pilot.backend
            final = None
            if (
                DEFAULT_CONFIG.retime_final_sweep
                and initial.accepted_bays > 0
                and exact_budget.remaining >= timebox
            ):
                final = retime_sweep(
                    current_state,
                    incumbent,
                    backend_calls,
                    available_backends=available,
                    budget=exact_budget,
                    first_sweep_budget=exact_budget.remaining,
                    seed=(DEFAULT_CONFIG.constructor_seed if _seed is None else _seed),
                    threads=DEFAULT_CONFIG.retime_threads,
                    call_timebox_cap=timebox,
                    pilot_budget_cap=0.0,
                    require_certified_optimal=alns_enabled,
                    fixed_call_timebox=(
                        0.10 * exact_allowance if alns_enabled else None
                    ),
                )
                current_state = final.state
            if _telemetry is not None:
                _telemetry.update(
                    retime_seconds=time.monotonic() - retime_started,
                    retime_backend=initial.pilot.backend,
                    retime_pilot=asdict(initial.pilot),
                    retime_attempts=[
                        asdict(item)
                        for item in (
                            initial.attempts
                            + (() if final is None else final.attempts)
                        )
                    ],
                    retime_accepted_bays=initial.accepted_bays,
                    retime_final_accepted_bays=(
                        0 if final is None else final.accepted_bays
                    ),
                    retime_fallback=(initial.accepted_bays == 0),
                    retime_before_z1=before_retime_z1,
                    retime_after_z1=incumbent.checker_result.obj1,
                )

        if alns_enabled and budget.remaining > 0.0:
            _inject_fault(_fault, "during_alns")
            alns_started = time.monotonic()
            if _telemetry is not None:
                _telemetry["alns_input_solution"] = incumbent.solution
            seed = DEFAULT_CONFIG.constructor_seed if _seed is None else _seed
            rng = Generator(PCG64(seed))
            trigger = RetimeTrigger(
                min_dirty=DEFAULT_CONFIG.alns_dirty_minimum,
                dirty_fraction=DEFAULT_CONFIG.alns_dirty_fraction,
                min_interval_fraction=DEFAULT_CONFIG.alns_retime_interval_fraction,
            )
            wall_policy = RetimeWallPolicy(
                started_at=alns_started,
                wall_fraction_cap=DEFAULT_CONFIG.alns_retime_wall_fraction_cap,
                solve_timebox_seconds=min(
                    DEFAULT_CONFIG.retime_timebox_seconds,
                    1.0,
                ),
            )

            def guarded_retime(state, bay_id, active_budget, call_timebox):
                if active_budget is None:
                    return None
                outcome = retime_bay(
                    state,
                    bay_id,
                    alns_retime_backend,
                    backend_calls[alns_retime_backend],
                    budget=active_budget,
                    seed=seed,
                    threads=DEFAULT_CONFIG.retime_threads,
                    call_timebox_cap=call_timebox,
                    require_certified_optimal=True,
                )
                return outcome.candidate

            def alns_fault(point):
                if _alns_fault is None:
                    return
                if _alns_fault not in {"repair", "accept", "retime", "full_check"}:
                    raise ValueError(f"unknown S3 fault point: {_alns_fault}")
                if point == _alns_fault:
                    if _telemetry is not None:
                        _telemetry["alns_fault_applied"] = point
                    raise InjectedEntryFault(f"injected S3 fault at {point}")

            alns_result = run_anytime_epochs(
                current_state,
                incumbent,
                rng,
                timelimit_seconds=max(
                    0.0,
                    budget.remaining
                    - (
                        _S4_MINIMUM_USEFUL_WORK_SECONDS
                        if refinement_enabled
                        else 0.0
                    ),
                ),
                epoch_seconds=60.0,
                iterations_per_epoch=300,
                continuation_iterations_per_epoch=1,
                acceptor_name=DEFAULT_CONFIG.alns_acceptor,
                adaptive=DEFAULT_CONFIG.alns_adaptive,
                safety_sample_interval=8,
                retime_trigger=trigger,
                retime_callback=guarded_retime,
                retime_wall_policy=wall_policy,
                stagnation=StagnationController(
                    reheat_after=6,
                    expand_after=12,
                    restart_after=18,
                ),
                fault_hook=alns_fault,
            )
            if _telemetry is not None:
                _telemetry.update(
                    alns_seconds=time.monotonic() - alns_started,
                    alns_metrics=asdict(alns_result.metrics),
                    alns_incumbent_trace=[
                        asdict(item) for item in alns_result.incumbent_trace
                    ],
                    alns_first_epoch_trace=[
                        list(item) for item in alns_result.first_epoch_trace
                    ],
                    alns_prefix_consistent=alns_result.prefix_consistent,
                    alns_epoch_count=alns_result.epoch_count,
                    alns_stopped_reason=alns_result.stopped_reason,
                    alns_operator_metrics={
                        name: {
                            "attempts": attempts,
                            "successes": successes,
                            "failures": failures,
                        }
                        for name, attempts, successes, failures
                        in alns_result.operator_metrics
                    },
                    alns_retime_wall_seconds=trigger.retime_wall_seconds,
                    alns_non_retime_wall_seconds=max(
                        0.0,
                        time.monotonic() - alns_started - trigger.retime_wall_seconds,
                    ),
                    alns_acceptor=DEFAULT_CONFIG.alns_acceptor,
                    alns_adaptive=DEFAULT_CONFIG.alns_adaptive,
                    alns_epoch_solutions=list(alns_result.epoch_solutions),
                )

        s3_checkpoint = incumbent.export_checkpoint()
        if _s3_checkpoint_out is not None:
            _s3_checkpoint_out["checkpoint"] = s3_checkpoint
        if _stop_after_s3:
            if _telemetry is not None:
                _telemetry["incumbent_verification_count"] = (
                    incumbent.verification_count
                )
            return s3_checkpoint.solution_copy()

        if refinement_enabled and alns_enabled:
            return refine_s4_checkpoint(
                prob_info,
                s3_checkpoint,
                remaining_timelimit=budget.hard_remaining,
                original_timelimit=timelimit,
                seed=(
                    DEFAULT_CONFIG.constructor_seed if _seed is None else _seed
                ),
                backend_fault=_assignment_backend_fault,
                telemetry=_telemetry,
                assignment_v2_enabled=assignment_v2_enabled,
                cross_bay_enabled=cross_bay_enabled,
                fault=_fault,
            )
        if _telemetry is not None:
            _telemetry["incumbent_verification_count"] = incumbent.verification_count
        return incumbent.solution
    except Exception as exc:
        if incumbent is None or not incumbent.has_incumbent:
            raise
        if (
            _s3_checkpoint_out is not None
            and "checkpoint" not in _s3_checkpoint_out
        ):
            _s3_checkpoint_out["checkpoint"] = incumbent.export_checkpoint()
        if _telemetry is not None:
            _telemetry["fallback_reason"] = f"{type(exc).__name__}: {exc}"
            _telemetry["incumbent_verification_count"] = incumbent.verification_count
        return incumbent.solution


def solve_to_s3_checkpoint(
    prob_info: dict[str, Any],
    timelimit: float,
    *,
    seed: int,
    telemetry: dict[str, Any] | None = None,
) -> VerifiedCheckpoint:
    """Run the unchanged entry pipeline through S3 and export its verified floor."""
    checkpoint_out: dict[str, VerifiedCheckpoint] = {}
    solve(
        prob_info,
        timelimit,
        _seed=seed,
        _alns=True,
        _assignment_refinement=False,
        _assignment_v2=False,
        _cross_bay=False,
        _telemetry=telemetry,
        _s3_checkpoint_out=checkpoint_out,
        _stop_after_s3=True,
    )
    checkpoint = checkpoint_out.get("checkpoint")
    if checkpoint is None:
        raise RuntimeError("S3 solve returned without a verified checkpoint")
    return checkpoint


def refine_s4_checkpoint(
    prob_info: dict[str, Any],
    checkpoint: VerifiedCheckpoint,
    *,
    remaining_timelimit: float,
    original_timelimit: float,
    seed: int,
    backend_fault: str | None,
    telemetry: dict[str, Any] | None,
    assignment_v2_enabled: bool = True,
    cross_bay_enabled: bool = True,
    fault: FaultHook = None,
) -> dict[str, Any]:
    """Run S4 in one hard-bounded worker and retain the parent-owned S3 floor."""
    parent_started = time.monotonic()
    hard_remaining = _finite_nonnegative(
        remaining_timelimit,
        label="remaining_timelimit",
    )
    original = _finite_nonnegative(original_timelimit, label="original_timelimit")
    reserve = min(deadline_reserve(original), hard_remaining)
    work_allowance = max(0.0, hard_remaining - reserve)
    worker_return_margin = _s4_worker_return_margin(work_allowance, reserve)
    skip_reason = _s4_worker_skip_reason(work_allowance, reserve)
    cooperative_return_deadline = parent_started + work_allowance
    process_kill_deadline = cooperative_return_deadline + worker_return_margin
    parent_hard_deadline = parent_started + hard_remaining
    instance = ProblemInstance.parse(prob_info)
    fallback_solution = checkpoint.solution_copy()
    if _instance_sha256(instance) != checkpoint.instance_sha256:
        raise ValueError("verified checkpoint belongs to a different instance")
    base_telemetry = {
        "s4_shared_checkpoint_sha256": checkpoint.solution_sha256,
        "s4_remaining_timelimit": hard_remaining,
        "s4_resume_reserve": reserve,
        "s4_work_allowance": work_allowance,
        "s4_worker_return_margin": worker_return_margin,
        "s4_minimum_useful_work_seconds": _S4_MINIMUM_USEFUL_WORK_SECONDS,
        "s4_parent_return_tail_seconds": _S4_PARENT_FINALIZATION_TAIL_SECONDS,
        "s4_worker_launched": False,
        "s4_skip_reason": skip_reason,
        "s4_cooperative_return_deadline": cooperative_return_deadline,
        "s4_process_kill_deadline": process_kill_deadline,
        "s4_parent_hard_deadline": parent_hard_deadline,
        "s4_branch_seed": seed,
        "s4_input_solution": fallback_solution,
        "s4_input_objective": checkpoint.checker_result.objective,
        "s4_input_z2": checkpoint.checker_result.obj2,
    }
    if telemetry is not None:
        telemetry.update(base_telemetry)

    def fallback(
        status: str,
        reason: str | None,
        process_result: _S4WorkerProcessResult | None = None,
    ) -> dict[str, Any]:
        verification_count = checkpoint.verification_count
        try:
            if process_result is None:
                verified_floor = VerifiedIncumbent.from_checkpoint(
                    instance,
                    checkpoint,
                )
            else:
                with _s4_parent_deadline_guard(parent_hard_deadline):
                    verified_floor = VerifiedIncumbent.from_checkpoint(
                        instance,
                        checkpoint,
                    )
            checked_floor = verified_floor.checker_result
            verified_solution = verified_floor.solution
            verification_count = verified_floor.verification_count
        except _S4ParentDeadlineExpired:
            checked_floor = checkpoint.checker_result
            verified_solution = fallback_solution
            deadline_reason = "parent hard deadline expired before S3 revalidation"
            reason = deadline_reason if reason is None else f"{reason}; {deadline_reason}"
        authoritative = _s4_authoritative_telemetry(
            status=status,
            reason=reason,
            hard_timeout=(status == "hard_timed_out"),
            process_result=process_result,
            parent_started=parent_started,
            checkpoint=checkpoint,
            checker_result=checked_floor,
        )
        if telemetry is not None:
            telemetry.update(authoritative)
            telemetry.update(
                s4_final_objective=checked_floor.objective,
                s4_final_z2=checked_floor.obj2,
                incumbent_verification_count=verification_count,
            )
            if reason is not None:
                telemetry["assignment_refinement_fallback_reason"] = reason
        return verified_solution

    if skip_reason is not None:
        return fallback(
            "skipped_insufficient_work",
            skip_reason,
        )
    try:
        _inject_fault(fault, "during_assignment_refinement")
    except Exception as exc:
        return fallback("failed", f"{type(exc).__name__}: {exc}")

    request = _s4_worker_request(
        prob_info,
        checkpoint,
        cooperative_return_deadline=cooperative_return_deadline,
        process_kill_deadline=process_kill_deadline,
        parent_hard_deadline=parent_hard_deadline,
        worker_return_margin=worker_return_margin,
        remaining_timelimit=hard_remaining,
        original_timelimit=original,
        work_allowance=work_allowance,
        seed=seed,
        assignment_v2_enabled=assignment_v2_enabled,
        cross_bay_enabled=cross_bay_enabled,
        backend_fault=backend_fault,
    )
    if telemetry is not None:
        telemetry.update(s4_worker_launched=True, s4_skip_reason=None)
    process_result: _S4WorkerProcessResult | None = None
    try:
        with tempfile.TemporaryDirectory(prefix="fable-s4-worker-") as tmpdir:
            input_path = Path(tmpdir) / "input.json"
            output_path = Path(tmpdir) / "output.json"
            input_path.write_text(
                json.dumps(request, sort_keys=True, separators=(",", ":")),
                encoding="utf-8",
            )
            if time.monotonic() >= cooperative_return_deadline:
                return fallback(
                    "skipped_no_work_budget",
                    "S4 payload preparation exhausted work allowance",
                )
            process_result = _run_s4_worker_process(
                _s4_worker_argv(input_path, output_path),
                process_kill_deadline=process_kill_deadline,
                parent_hard_deadline=parent_hard_deadline,
                terminate_grace=min(
                    0.05,
                    max(0.0, parent_hard_deadline - process_kill_deadline) / 4.0,
                ),
            )
            if process_result.timed_out:
                return fallback(
                    "hard_timed_out",
                    "S4 worker failed to return by the process-kill deadline",
                    process_result,
                )
            if process_result.error is not None:
                return fallback(
                    "failed",
                    f"S4 worker process control failed: {process_result.error}",
                    process_result,
                )
            if process_result.exit_code != 0 or process_result.signal is not None:
                detail = (
                    f"signal {process_result.signal}"
                    if process_result.signal is not None
                    else f"exit {process_result.exit_code}"
                )
                return fallback("failed", f"S4 worker {detail}", process_result)
            if not process_result.group_clean:
                return fallback(
                    "failed",
                    "S4 worker process group was not fully cleaned",
                    process_result,
                )
            if not output_path.is_file():
                return fallback(
                    "invalid_output",
                    "invalid S4 worker output: final file is missing",
                    process_result,
                )
            try:
                with _s4_parent_deadline_guard(parent_hard_deadline):
                    output = json.loads(output_path.read_text(encoding="utf-8"))
                    result_checkpoint, worker_telemetry = _validate_s4_worker_output(
                        output,
                        request=request,
                        input_checkpoint=checkpoint,
                    )
                    verified = VerifiedIncumbent.from_checkpoint(
                        instance,
                        result_checkpoint,
                    )
                    checked = verified.checker_result
                    _require_s4_nonregression(checkpoint.checker_result, checked)
                    solution = result_checkpoint.solution_copy()
            except Exception as exc:
                return fallback(
                    "invalid_output",
                    f"invalid S4 worker output: {type(exc).__name__}: {exc}",
                    process_result,
                )
    except Exception as exc:
        return fallback(
            "failed",
            f"S4 worker launch failed: {type(exc).__name__}: {exc}",
            process_result,
        )

    if telemetry is not None:
        telemetry.update(
            {
                key: value
                for key, value in worker_telemetry.items()
                if key not in _S4_WORKER_AUTHORITATIVE_TELEMETRY
            }
        )
        completion_status = worker_telemetry["s4_worker_completion_status"]
        telemetry.update(
            _s4_authoritative_telemetry(
                status=completion_status,
                reason=None,
                hard_timeout=False,
                process_result=process_result,
                parent_started=parent_started,
                checkpoint=result_checkpoint,
                checker_result=checked,
            )
        )
        telemetry.update(
            s4_final_objective=checked.objective,
            s4_final_z2=checked.obj2,
            s4_worker_verification_count=result_checkpoint.verification_count,
            incumbent_verification_count=verified.verification_count,
        )
    return solution


def _refine_s4_checkpoint_core(
    prob_info: dict[str, Any],
    checkpoint: VerifiedCheckpoint,
    *,
    cooperative_return_deadline: float,
    seed: int,
    assignment_v2_enabled: bool,
    cross_bay_enabled: bool,
    backend_fault: str | None,
    telemetry: dict[str, Any] | None,
) -> VerifiedCheckpoint:
    """Worker-only in-process S4 core under an already reserved deadline."""
    instance = ProblemInstance.parse(prob_info)
    if time.monotonic() >= cooperative_return_deadline:
        if telemetry is not None:
            telemetry.update(
                s4_worker_completion_status="completed_after_budget",
                incumbent_verification_count=checkpoint.verification_count,
            )
        return checkpoint
    incumbent = VerifiedIncumbent.from_checkpoint(instance, checkpoint)
    current_state = incumbent.snapshot_state()
    budget = Budget(
        max(0.0, cooperative_return_deadline - time.monotonic()),
        reserve=0.0,
    )
    backend_calls = {
        "gurobi": retime_gurobi,
        "cpsat": retime_cpsat,
    }
    available = (
        ("gurobi", "cpsat")
        if DEFAULT_CONFIG.retime_backend == "auto"
        else (DEFAULT_CONFIG.retime_backend,)
    )
    completion_status = "completed"
    try:
        if budget.remaining > 0.0:
            current_state = _refine_verified_s3(
                instance,
                current_state,
                incumbent,
                budget,
                seed=seed,
                assignment_v2_enabled=assignment_v2_enabled,
                cross_bay_enabled=cross_bay_enabled,
                backend_fault=backend_fault,
                backend_calls=backend_calls,
                available_backends=available,
                fault=None,
                telemetry=telemetry,
            )
    except BudgetExpired:
        completion_status = "completed_after_budget"
    if budget.expired:
        completion_status = "completed_after_budget"
    result = incumbent.export_checkpoint()
    if telemetry is not None:
        telemetry.update(
            s4_worker_completion_status=completion_status,
            incumbent_verification_count=result.verification_count,
        )
    return result


def _s4_resume_budget(
    remaining_timelimit: float,
    *,
    original_timelimit: float,
    clock: Callable[[], float] = time.monotonic,
) -> Budget:
    """Preserve the original pair reserve inside B's remaining hard allowance."""
    remaining = max(0.0, float(remaining_timelimit))
    reserve = min(deadline_reserve(original_timelimit), remaining)
    return Budget(remaining, reserve=reserve, clock=clock)


def _s4_worker_return_margin(
    work_allowance: float,
    resume_reserve: float,
) -> float:
    """Return the full measured publish margin or decline to launch a worker."""
    allowance = _finite_nonnegative(work_allowance, label="work_allowance")
    reserve = _finite_nonnegative(resume_reserve, label="resume_reserve")
    if (
        allowance + 1e-12 < _S4_MINIMUM_USEFUL_WORK_SECONDS
        or reserve + 1e-12
        < _S4_WORKER_RETURN_MARGIN_SECONDS
        + _S4_PARENT_FINALIZATION_TAIL_SECONDS
    ):
        return 0.0
    return _S4_WORKER_RETURN_MARGIN_SECONDS


def _s4_worker_skip_reason(
    work_allowance: float,
    resume_reserve: float,
) -> str | None:
    """Name the independently measured launch resource that is unavailable."""
    allowance = _finite_nonnegative(work_allowance, label="work_allowance")
    reserve = _finite_nonnegative(resume_reserve, label="resume_reserve")
    if allowance + 1e-12 < _S4_MINIMUM_USEFUL_WORK_SECONDS:
        return "minimum_useful_work_not_available"
    required_reserve = (
        _S4_WORKER_RETURN_MARGIN_SECONDS
        + _S4_PARENT_FINALIZATION_TAIL_SECONDS
    )
    if reserve + 1e-12 < required_reserve:
        return "publish_kill_or_parent_return_reserve_not_available"
    return None


@contextmanager
def _s4_parent_deadline_guard(deadline: float) -> Iterator[None]:
    """Interrupt parent-side parsing/checking at the declared hard deadline."""
    remaining = deadline - time.monotonic()
    if remaining <= 0.0:
        raise _S4ParentDeadlineExpired("S4 parent hard deadline expired")

    can_arm = (
        hasattr(signal, "SIGALRM")
        and hasattr(signal, "setitimer")
        and threading.current_thread() is threading.main_thread()
    )
    if not can_arm:
        yield
        if time.monotonic() > deadline:
            raise _S4ParentDeadlineExpired("S4 parent hard deadline expired")
        return

    previous_handler = signal.getsignal(signal.SIGALRM)
    previous_delay, previous_interval = signal.getitimer(signal.ITIMER_REAL)
    armed_at = time.monotonic()

    def expire(_signum: int, _frame: Any) -> None:
        raise _S4ParentDeadlineExpired("S4 parent hard deadline expired")

    signal.signal(signal.SIGALRM, expire)
    signal.setitimer(signal.ITIMER_REAL, max(1e-6, remaining))
    try:
        yield
        if time.monotonic() > deadline:
            raise _S4ParentDeadlineExpired("S4 parent hard deadline expired")
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0.0)
        signal.signal(signal.SIGALRM, previous_handler)
        if previous_delay > 0.0:
            elapsed = max(0.0, time.monotonic() - armed_at)
            signal.setitimer(
                signal.ITIMER_REAL,
                max(1e-6, previous_delay - elapsed),
                previous_interval,
            )


def _finite_nonnegative(value: float, *, label: str) -> float:
    if isinstance(value, bool):
        raise ValueError(f"{label} must be a finite non-negative number")
    try:
        result = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{label} must be a finite non-negative number") from exc
    if not math.isfinite(result) or result < 0.0:
        raise ValueError(f"{label} must be a finite non-negative number")
    return result


def _instance_sha256(instance: ProblemInstance) -> str:
    return hashlib.sha256(
        json.dumps(
            instance.raw,
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
    ).hexdigest()


def _solution_sha256(solution: dict[str, Any]) -> str:
    return hashlib.sha256(
        json.dumps(solution, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def _checkpoint_to_payload(checkpoint: VerifiedCheckpoint) -> dict[str, Any]:
    checkpoint.solution_copy()
    return {
        "instance_sha256": checkpoint.instance_sha256,
        "solution_json": checkpoint.solution_json.decode("utf-8"),
        "solution_sha256": checkpoint.solution_sha256,
        "checker_result": asdict(checkpoint.checker_result),
        "placements": [asdict(item) for item in checkpoint.placements],
        "verification_count": checkpoint.verification_count,
    }


def _checkpoint_from_payload(payload: Any) -> VerifiedCheckpoint:
    required = {
        "instance_sha256",
        "solution_json",
        "solution_sha256",
        "checker_result",
        "placements",
        "verification_count",
    }
    if not isinstance(payload, dict) or set(payload) != required:
        raise ValueError("checkpoint payload keys do not match the worker schema")
    instance_sha = _sha256_text(payload["instance_sha256"], "instance_sha256")
    solution_sha = _sha256_text(payload["solution_sha256"], "solution_sha256")
    solution_json = payload["solution_json"]
    if not isinstance(solution_json, str):
        raise ValueError("checkpoint solution_json must be UTF-8 text")
    count = payload["verification_count"]
    if type(count) is not int or count < 0:
        raise ValueError("checkpoint verification_count must be a non-negative int")
    placement_payload = payload["placements"]
    if not isinstance(placement_payload, list):
        raise ValueError("checkpoint placements must be a list")
    placements = tuple(_placement_from_payload(item) for item in placement_payload)
    checkpoint = VerifiedCheckpoint(
        instance_sha256=instance_sha,
        solution_json=solution_json.encode("utf-8"),
        solution_sha256=solution_sha,
        checker_result=_checker_result_from_payload(payload["checker_result"]),
        placements=placements,
        verification_count=count,
    )
    checkpoint.solution_copy()
    return checkpoint


def _sha256_text(value: Any, label: str) -> str:
    if (
        not isinstance(value, str)
        or len(value) != 64
        or any(character not in "0123456789abcdef" for character in value)
    ):
        raise ValueError(f"{label} must be a lowercase SHA-256 hex digest")
    return value


def _placement_from_payload(payload: Any) -> Placement:
    keys = {"block_id", "bay_id", "x", "y", "orient_idx", "entry", "exit"}
    if not isinstance(payload, dict) or set(payload) != keys:
        raise ValueError("placement payload keys do not match the worker schema")
    if any(type(payload[key]) is not int for key in keys):
        raise ValueError("placement fields must be exact integers")
    return Placement(**payload)


def _checker_result_from_payload(payload: Any) -> CheckerResult:
    keys = {
        "feasible",
        "stage",
        "violations",
        "objective",
        "obj1",
        "obj2",
        "obj3",
    }
    if not isinstance(payload, dict) or set(payload) != keys:
        raise ValueError("checker payload keys do not match the worker schema")
    if type(payload["feasible"]) is not bool:
        raise ValueError("checker feasible must be bool")
    if type(payload["stage"]) is not int:
        raise ValueError("checker stage must be int")
    violations = payload["violations"]
    if not isinstance(violations, list) or any(
        not isinstance(item, str) for item in violations
    ):
        raise ValueError("checker violations must be a list of strings")

    def optional_number(key: str) -> float | None:
        value = payload[key]
        if value is None:
            return None
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ValueError(f"checker {key} must be a finite number or null")
        result = float(value)
        if not math.isfinite(result):
            raise ValueError(f"checker {key} must be a finite number or null")
        return result

    return CheckerResult(
        feasible=payload["feasible"],
        stage=payload["stage"],
        violations=tuple(violations),
        objective=optional_number("objective"),
        obj1=optional_number("obj1"),
        obj2=optional_number("obj2"),
        obj3=optional_number("obj3"),
    )


def _s4_request_identity(request: dict[str, Any]) -> dict[str, Any]:
    checkpoint = request["checkpoint"]
    return {
        "schema_version": request["schema_version"],
        "instance_sha256": checkpoint["instance_sha256"],
        "checkpoint_solution_sha256": checkpoint["solution_sha256"],
        "checkpoint_verification_count": checkpoint["verification_count"],
        "seed": request["seed"],
        "assignment_v2_enabled": request["assignment_v2_enabled"],
        "cross_bay_enabled": request["cross_bay_enabled"],
        "backend_fault": request["backend_fault"],
        "remaining_timelimit": request["remaining_timelimit"],
        "original_timelimit": request["original_timelimit"],
        "work_allowance": request["work_allowance"],
        "worker_return_margin": request["worker_return_margin"],
        "cooperative_return_deadline": request["cooperative_return_deadline"],
        "process_kill_deadline": request["process_kill_deadline"],
        "parent_hard_deadline": request["parent_hard_deadline"],
    }


def _s4_request_id(request: dict[str, Any]) -> str:
    return hashlib.sha256(
        json.dumps(
            _s4_request_identity(request),
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
    ).hexdigest()


def _s4_worker_request(
    prob_info: dict[str, Any],
    checkpoint: VerifiedCheckpoint,
    *,
    cooperative_return_deadline: float,
    process_kill_deadline: float,
    parent_hard_deadline: float,
    worker_return_margin: float,
    remaining_timelimit: float,
    original_timelimit: float,
    work_allowance: float,
    seed: int,
    assignment_v2_enabled: bool,
    cross_bay_enabled: bool,
    backend_fault: str | None,
) -> dict[str, Any]:
    if type(seed) is not int:
        raise ValueError("S4 seed must be an exact int")
    if type(assignment_v2_enabled) is not bool or type(cross_bay_enabled) is not bool:
        raise ValueError("S4 feature flags must be bool")
    if backend_fault not in {None, "gurobi", "cp_sat", "both"}:
        raise ValueError(f"unknown assignment backend fault: {backend_fault}")
    request = {
        "schema_version": _S4_WORKER_SCHEMA_VERSION,
        "request_id": "",
        "prob_info": prob_info,
        "checkpoint": _checkpoint_to_payload(checkpoint),
        "cooperative_return_deadline": cooperative_return_deadline,
        "process_kill_deadline": process_kill_deadline,
        "parent_hard_deadline": parent_hard_deadline,
        "worker_return_margin": worker_return_margin,
        "remaining_timelimit": remaining_timelimit,
        "original_timelimit": original_timelimit,
        "work_allowance": work_allowance,
        "seed": seed,
        "assignment_v2_enabled": assignment_v2_enabled,
        "cross_bay_enabled": cross_bay_enabled,
        "backend_fault": backend_fault,
    }
    request["request_id"] = _s4_request_id(request)
    return request


def _validate_s4_worker_request(payload: Any) -> tuple[dict[str, Any], VerifiedCheckpoint]:
    keys = {
        "schema_version",
        "request_id",
        "prob_info",
        "checkpoint",
        "cooperative_return_deadline",
        "process_kill_deadline",
        "parent_hard_deadline",
        "worker_return_margin",
        "remaining_timelimit",
        "original_timelimit",
        "work_allowance",
        "seed",
        "assignment_v2_enabled",
        "cross_bay_enabled",
        "backend_fault",
    }
    if not isinstance(payload, dict) or set(payload) != keys:
        raise ValueError("S4 worker request keys do not match the schema")
    if payload["schema_version"] != _S4_WORKER_SCHEMA_VERSION:
        raise ValueError("unsupported S4 worker schema version")
    _sha256_text(payload["request_id"], "request_id")
    if payload["request_id"] != _s4_request_id(payload):
        raise ValueError("S4 worker request identity mismatch")
    if not isinstance(payload["prob_info"], dict):
        raise ValueError("S4 worker prob_info must be an object")
    cooperative = _finite_nonnegative(
        payload["cooperative_return_deadline"],
        label="cooperative_return_deadline",
    )
    process_kill = _finite_nonnegative(
        payload["process_kill_deadline"],
        label="process_kill_deadline",
    )
    parent_hard = _finite_nonnegative(
        payload["parent_hard_deadline"],
        label="parent_hard_deadline",
    )
    worker_return_margin = _finite_nonnegative(
        payload["worker_return_margin"],
        label="worker_return_margin",
    )
    if not cooperative < process_kill < parent_hard:
        raise ValueError("S4 worker deadlines must be strictly ordered")
    if not math.isclose(
        process_kill - cooperative,
        worker_return_margin,
        rel_tol=0.0,
        abs_tol=1e-9,
    ):
        raise ValueError("S4 worker return margin does not match its deadlines")
    _finite_nonnegative(payload["remaining_timelimit"], label="remaining_timelimit")
    _finite_nonnegative(payload["original_timelimit"], label="original_timelimit")
    _finite_nonnegative(payload["work_allowance"], label="work_allowance")
    if type(payload["seed"]) is not int:
        raise ValueError("S4 worker seed must be an exact int")
    if type(payload["assignment_v2_enabled"]) is not bool:
        raise ValueError("assignment_v2_enabled must be bool")
    if type(payload["cross_bay_enabled"]) is not bool:
        raise ValueError("cross_bay_enabled must be bool")
    if payload["backend_fault"] not in {None, "gurobi", "cp_sat", "both"}:
        raise ValueError("unknown S4 worker backend fault")
    checkpoint = _checkpoint_from_payload(payload["checkpoint"])
    instance = ProblemInstance.parse(payload["prob_info"])
    if _instance_sha256(instance) != checkpoint.instance_sha256:
        raise ValueError("S4 worker checkpoint instance mismatch")
    return payload, checkpoint


def _validate_s4_worker_output(
    payload: Any,
    *,
    request: dict[str, Any],
    input_checkpoint: VerifiedCheckpoint,
) -> tuple[VerifiedCheckpoint, dict[str, Any]]:
    keys = {
        "schema_version",
        "request_id",
        "input_checkpoint_sha256",
        "checkpoint",
        "telemetry",
    }
    if not isinstance(payload, dict) or set(payload) != keys:
        raise ValueError("S4 worker output keys do not match the schema")
    if payload["schema_version"] != _S4_WORKER_SCHEMA_VERSION:
        raise ValueError("unsupported S4 worker output schema version")
    if payload["request_id"] != request["request_id"]:
        raise ValueError("S4 worker output request identity mismatch")
    if payload["input_checkpoint_sha256"] != input_checkpoint.solution_sha256:
        raise ValueError("S4 worker output input-checkpoint mismatch")
    checkpoint = _checkpoint_from_payload(payload["checkpoint"])
    if checkpoint.instance_sha256 != input_checkpoint.instance_sha256:
        raise ValueError("S4 worker output instance mismatch")
    worker_telemetry = payload["telemetry"]
    if not isinstance(worker_telemetry, dict):
        raise ValueError("S4 worker telemetry must be an object")
    completion_status = worker_telemetry.get("s4_worker_completion_status")
    if completion_status not in {"completed", "completed_after_budget"}:
        raise ValueError("S4 worker completion status is invalid")
    worker_count = worker_telemetry.get("incumbent_verification_count")
    if type(worker_count) is not int or worker_count != checkpoint.verification_count:
        raise ValueError("S4 worker telemetry verification count mismatch")
    minimum_count = input_checkpoint.verification_count + 1
    if (
        completion_status == "completed_after_budget"
        and checkpoint.solution_sha256 == input_checkpoint.solution_sha256
    ):
        minimum_count = input_checkpoint.verification_count
    if checkpoint.verification_count < minimum_count:
        raise ValueError("S4 worker verification count is not monotonic")
    return checkpoint, worker_telemetry


def _s4_worker_argv(input_path: Path, output_path: Path) -> tuple[str, ...]:
    return (
        sys.executable,
        "-m",
        __name__,
        "--s4-worker",
        str(input_path),
        str(output_path),
    )


def _run_s4_worker_process(
    argv: tuple[str, ...],
    *,
    process_kill_deadline: float,
    parent_hard_deadline: float,
    terminate_grace: float,
) -> _S4WorkerProcessResult:
    """Run one worker session and reap its process group on every exit path."""
    started = time.monotonic()
    process = subprocess.Popen(
        list(argv),
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        close_fds=True,
        start_new_session=True,
    )
    timed_out = False
    term_sent = False
    kill_sent = False
    error: str | None = None
    try:
        try:
            process.wait(
                timeout=max(0.0, process_kill_deadline - time.monotonic())
            )
        except subprocess.TimeoutExpired:
            timed_out = True
            term_sent = _signal_s4_worker_group(process, signal.SIGTERM)
            grace_deadline = min(
                parent_hard_deadline,
                time.monotonic() + max(0.0, terminate_grace),
            )
            while time.monotonic() < grace_deadline:
                if process.poll() is not None and not _s4_process_group_exists(
                    process.pid
                ):
                    break
                time.sleep(min(0.005, max(0.0, grace_deadline - time.monotonic())))
            if process.poll() is None or _s4_process_group_exists(process.pid):
                kill_sent = _signal_s4_worker_group(process, signal.SIGKILL)
            _wait_s4_worker_until(process, parent_hard_deadline)
        if _s4_process_group_exists(process.pid):
            term_sent = _signal_s4_worker_group(process, signal.SIGTERM) or term_sent
            cleanup_deadline = min(
                parent_hard_deadline,
                time.monotonic() + max(0.0, terminate_grace),
            )
            while (
                time.monotonic() < cleanup_deadline
                and _s4_process_group_exists(process.pid)
            ):
                time.sleep(min(0.005, cleanup_deadline - time.monotonic()))
            if _s4_process_group_exists(process.pid):
                kill_sent = _signal_s4_worker_group(process, signal.SIGKILL) or kill_sent
                _wait_s4_worker_until(process, parent_hard_deadline)
    except Exception as exc:
        error = f"{type(exc).__name__}: {exc}"
        kill_sent = _signal_s4_worker_group(process, signal.SIGKILL) or kill_sent
        _wait_s4_worker_until(process, parent_hard_deadline)
    except BaseException:
        _signal_s4_worker_group(process, signal.SIGKILL)
        _wait_s4_worker_until(process, parent_hard_deadline)
        cleanup_deadline = min(
            parent_hard_deadline,
            time.monotonic() + max(0.01, terminate_grace),
        )
        while (
            time.monotonic() < cleanup_deadline
            and _s4_process_group_exists(process.pid)
        ):
            time.sleep(min(0.005, cleanup_deadline - time.monotonic()))
        raise
    cleanup_deadline = min(
        parent_hard_deadline,
        time.monotonic() + max(0.01, terminate_grace),
    )
    while (
        time.monotonic() < cleanup_deadline
        and _s4_process_group_exists(process.pid)
    ):
        time.sleep(min(0.005, cleanup_deadline - time.monotonic()))
    return_code = process.poll()
    return _S4WorkerProcessResult(
        pid=process.pid,
        exit_code=(
            return_code if return_code is not None and return_code >= 0 else None
        ),
        signal=(
            -return_code if return_code is not None and return_code < 0 else None
        ),
        wall_seconds=time.monotonic() - started,
        timed_out=timed_out,
        term_sent=term_sent,
        kill_sent=kill_sent,
        group_clean=not _s4_process_group_exists(process.pid),
        error=error,
    )


def _wait_s4_worker_until(
    process: subprocess.Popen[Any],
    deadline: float,
) -> bool:
    """Reap a worker if it exits before deadline, without an unbounded wait."""
    if process.poll() is not None:
        return True
    remaining = max(0.0, deadline - time.monotonic())
    if remaining <= 0.0:
        return False
    try:
        process.wait(timeout=remaining)
    except subprocess.TimeoutExpired:
        return False
    return True


def _signal_s4_worker_group(
    process: subprocess.Popen[Any],
    signum: signal.Signals,
) -> bool:
    try:
        os.killpg(process.pid, signum)
        return True
    except ProcessLookupError:
        if process.poll() is None:
            try:
                os.kill(process.pid, signum)
                return True
            except ProcessLookupError:
                pass
    return False


def _s4_process_group_exists(pgid: int) -> bool:
    try:
        os.killpg(pgid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _s4_authoritative_telemetry(
    *,
    status: str,
    reason: str | None,
    hard_timeout: bool,
    process_result: _S4WorkerProcessResult | None,
    parent_started: float,
    checkpoint: VerifiedCheckpoint,
    checker_result: CheckerResult | None = None,
) -> dict[str, Any]:
    checked = checkpoint.checker_result if checker_result is None else checker_result
    return {
        "s4_worker_status": status,
        "s4_worker_wall_seconds": max(0.0, time.monotonic() - parent_started),
        "s4_worker_exit_code": (
            None if process_result is None else process_result.exit_code
        ),
        "s4_worker_signal": None if process_result is None else process_result.signal,
        "s4_worker_term_sent": (
            False if process_result is None else process_result.term_sent
        ),
        "s4_worker_kill_sent": (
            False if process_result is None else process_result.kill_sent
        ),
        "s4_worker_group_clean": (
            True if process_result is None else process_result.group_clean
        ),
        "s4_hard_timeout_fallback": hard_timeout,
        "s4_worker_fallback_reason": reason,
        "s4_verified_checker": asdict(checked),
        "s4_verified_solution_sha256": checkpoint.solution_sha256,
    }


def _require_s4_nonregression(
    baseline: CheckerResult,
    candidate: CheckerResult,
) -> None:
    if not candidate.feasible or candidate.stage != 5 or candidate.objective is None:
        raise ValueError("S4 worker result is not a feasible Stage-5 incumbent")
    if baseline.objective is None or baseline.obj2 is None or candidate.obj2 is None:
        raise ValueError("S4 worker result is missing objective components")
    objective_tolerance = 1e-9 * max(1.0, abs(baseline.objective))
    z2_tolerance = 1e-9 * max(1.0, abs(baseline.obj2))
    if candidate.objective > baseline.objective + objective_tolerance:
        raise ValueError("S4 worker objective regressed from verified S3")
    if candidate.obj2 > baseline.obj2 + z2_tolerance:
        raise ValueError("S4 worker Z2 regressed from verified S3")


def _s4_worker_main(input_path: Path, output_path: Path) -> int:
    try:
        request, checkpoint = _validate_s4_worker_request(
            json.loads(input_path.read_text(encoding="utf-8"))
        )
        telemetry: dict[str, Any] = {}
        result = _refine_s4_checkpoint_core(
            request["prob_info"],
            checkpoint,
            cooperative_return_deadline=float(
                request["cooperative_return_deadline"]
            ),
            seed=request["seed"],
            assignment_v2_enabled=request["assignment_v2_enabled"],
            cross_bay_enabled=request["cross_bay_enabled"],
            backend_fault=request["backend_fault"],
            telemetry=telemetry,
        )
        envelope = {
            "schema_version": _S4_WORKER_SCHEMA_VERSION,
            "request_id": request["request_id"],
            "input_checkpoint_sha256": checkpoint.solution_sha256,
            "checkpoint": _checkpoint_to_payload(result),
            "telemetry": telemetry,
        }
        temporary = output_path.with_name(
            f"{output_path.name}.tmp-{os.getpid()}"
        )
        with temporary.open("w", encoding="utf-8") as handle:
            json.dump(envelope, handle, sort_keys=True, separators=(",", ":"))
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, output_path)
        return 0
    except Exception:
        return 2


def _refine_verified_s3(
    instance: ProblemInstance,
    current_state: SolutionState,
    incumbent: VerifiedIncumbent,
    budget: Budget,
    *,
    seed: int,
    assignment_v2_enabled: bool,
    cross_bay_enabled: bool,
    backend_fault: str | None,
    backend_calls: dict[str, Callable[..., Any]],
    available_backends: tuple[str, ...],
    fault: FaultHook,
    telemetry: dict[str, Any] | None,
) -> SolutionState:
    """Apply S4 only after materializing a branch-owned verified S3 state."""
    current_state = incumbent.snapshot_state(current_state)
    s3_checkpoint = incumbent.export_checkpoint()
    if telemetry is not None:
        telemetry["s4_input_solution"] = s3_checkpoint.solution_copy()
        telemetry["s4_input_objective"] = s3_checkpoint.checker_result.objective
        telemetry["s4_input_z2"] = s3_checkpoint.checker_result.obj2
    try:
        _inject_fault(fault, "during_assignment_refinement")
    except Exception as exc:
        if telemetry is not None:
            telemetry["assignment_refinement_fallback_reason"] = (
                f"{type(exc).__name__}: {exc}"
            )
    else:
        if assignment_v2_enabled:
            try:
                current_state = _run_assignment_seed(
                    instance,
                    current_state,
                    incumbent,
                    budget,
                    seed=seed,
                    backend_fault=backend_fault,
                    backend_calls=backend_calls,
                    available_backends=available_backends,
                    telemetry=telemetry,
                )
            except Exception as exc:
                if telemetry is not None:
                    telemetry["assignment_seed_fallback_reason"] = (
                        f"{type(exc).__name__}: {exc}"
                    )
        if cross_bay_enabled and budget.remaining > 0.0:
            try:
                current_state = _run_cross_bay_refinement(
                    current_state,
                    incumbent,
                    budget,
                    seed=seed,
                    backend_calls=backend_calls,
                    available_backends=available_backends,
                    max_z2=incumbent.checker_result.obj2,
                    telemetry=telemetry,
                )
            except Exception as exc:
                if telemetry is not None:
                    telemetry["cross_bay_fallback_reason"] = (
                        f"{type(exc).__name__}: {exc}"
                    )
    if telemetry is not None:
        telemetry["s4_final_objective"] = incumbent.checker_result.objective
        telemetry["s4_final_z2"] = incumbent.checker_result.obj2
    return current_state


def _inject_fault(fault: FaultHook, point: str) -> None:
    if fault is None:
        return
    if callable(fault):
        fault(point)
        return
    known_points = {
        "after_incumbent",
        "during_constructor",
        "during_retime",
        "during_alns",
        "during_assignment_refinement",
    }
    if fault not in known_points:
        raise ValueError(f"unknown entry fault point: {fault}")
    if fault == point:
        raise InjectedEntryFault(f"injected entry fault at {point}")


def _run_assignment_seed(
    instance: ProblemInstance,
    current_state: SolutionState,
    incumbent: VerifiedIncumbent,
    budget: Budget,
    *,
    seed: int,
    backend_fault: str | None,
    backend_calls: dict[str, Callable[..., Any]] | None = None,
    available_backends: tuple[str, ...] | None = None,
    telemetry: dict[str, Any] | None,
) -> SolutionState:
    if backend_fault not in {None, "gurobi", "cp_sat", "both"}:
        raise ValueError(f"unknown assignment backend fault: {backend_fault}")
    budget.checkpoint("S4 assignment seed start")
    if telemetry is not None:
        telemetry.update(
            assignment_seed_attempts=[
                {
                    "backend": DEFAULT_CONFIG.assignment_backend,
                    "status": "started",
                    "accepted": False,
                    "reason": None,
                    "exact_z2": None,
                    "exact_z3": None,
                }
            ],
            assignment_seed_updated=False,
        )

    verified_state = incumbent.snapshot_state(current_state)
    starting = assignment_from_state(verified_state)
    constructed: dict[tuple[tuple[int, int, int], ...], SolutionState] = {}

    def key(candidate) -> tuple[tuple[int, int, int], ...]:
        return tuple(
            sorted(
                (block_id, item.bay_id, item.orient_idx)
                for block_id, item in candidate.assignments.items()
            )
        )

    def verify(candidate) -> AssignmentVerification:
        try:
            candidate_key = key(candidate)
            if candidate_key == key(starting):
                constructed[candidate_key] = verified_state
                return AssignmentVerification(
                    feasible=True,
                    objective=incumbent.checker_result.objective,
                    reason=None,
                )
            budget.checkpoint("S4 assignment construction")
            candidate_state = _apply_assignment_delta(
                verified_state,
                candidate,
                budget,
            )
            retime_calls = backend_calls or {
                "gurobi": retime_gurobi,
                "cpsat": retime_cpsat,
            }
            retime_order = available_backends or ("gurobi", "cpsat")
            affected_bays = tuple(
                sorted(
                    {
                        bay_id
                        for block_id, item in candidate.assignments.items()
                        if item.bay_id != starting.assignments[block_id].bay_id
                        for bay_id in (
                            item.bay_id,
                            starting.assignments[block_id].bay_id,
                        )
                    }
                )
            )
            for bay_id in affected_bays:
                for backend in retime_order:
                    budget.checkpoint("S4 assignment affected-bay retime")
                    outcome = retime_bay(
                        candidate_state,
                        bay_id,
                        backend,
                        retime_calls[backend],
                        budget=budget,
                        seed=seed,
                        threads=DEFAULT_CONFIG.retime_threads,
                        call_timebox_cap=min(
                            DEFAULT_CONFIG.retime_timebox_seconds,
                            max(budget.remaining, 0.0),
                        ),
                        require_certified_optimal=True,
                    )
                    if outcome.candidate is not None:
                        candidate_state = outcome.candidate
                        break
            budget.checkpoint("S4 before assignment candidate check")
            checked = official_check(
                instance.raw,
                serialize_non_interlock(candidate_state.placements.values()),
            )
            budget.checkpoint("S4 assignment candidate checked")
            if checked.feasible and checked.stage == 5:
                constructed[candidate_key] = candidate_state
            return AssignmentVerification(
                feasible=checked.feasible and checked.stage == 5,
                objective=checked.objective,
                reason=None if checked.feasible else "; ".join(checked.violations),
            )
        except BudgetExpired:
            raise
        except Exception as exc:
            return AssignmentVerification(
                feasible=False,
                objective=None,
                reason=f"{type(exc).__name__}: {exc}",
            )

    def injected(backend: str):
        def call(_request, _timebox: float) -> ExactAssignmentResult:
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
                reason=f"injected {backend} assignment fault",
            )

        return call

    calls = {
        "gurobi": (
            injected("gurobi")
            if backend_fault in {"gurobi", "both"}
            else assign_gurobi
        ),
        "cpsat": (
            injected("cpsat")
            if backend_fault in {"cp_sat", "both"}
            else assign_cpsat
        ),
    }
    selection = choose_assignment_candidate(
        instance,
        starting,
        config=replace(DEFAULT_CONFIG, constructor_seed=seed),
        backend_calls=calls,
        verify=verify,
        budget=budget,
    )
    selected_state = constructed.get(key(selection.assignment))
    updated = False
    if selected_state is not None:
        budget.checkpoint("S4 before assignment incumbent update")
        updated = incumbent.try_update(selected_state)
        budget.checkpoint("S4 after assignment incumbent update")
    if telemetry is not None:
        telemetry.update(
            assignment_seed_backend=selection.backend,
            assignment_seed_attempts=[asdict(item) for item in selection.attempts],
            assignment_seed_fallback_reason=selection.fallback_reason,
            assignment_seed_updated=updated,
            assignment_seed_z2=selection.evaluation.z2,
            assignment_seed_z3=selection.evaluation.z3,
        )
    return selected_state if updated and selected_state is not None else verified_state


def _apply_assignment_delta(
    verified_state: SolutionState,
    candidate,
    budget: Budget,
) -> SolutionState:
    """Clone S3 and repair only blocks whose assignment proposal changed."""
    state = _copy_solution_state(verified_state)
    changed = tuple(
        block_id
        for block_id, item in candidate.assignments.items()
        if (placement := verified_state.get(block_id)) is None
        or placement.bay_id != item.bay_id
        or placement.orient_idx != item.orient_idx
    )
    if not changed:
        return state
    for block_id in changed:
        budget.checkpoint("S4 assignment delta removal")
        if state.remove(block_id) is None:
            raise AssertionError(f"assignment block {block_id} disappeared")
    changed_set = set(changed)
    for block_id in candidate.order:
        if block_id not in changed_set:
            continue
        budget.checkpoint("S4 assignment delta repair")
        selected = candidate.assignments[block_id]
        inserted = insert_block(
            state,
            block_id,
            bays_try=(selected.bay_id,),
            preferred_orient_idx=selected.orient_idx,
            time_cap=DEFAULT_CONFIG.constructor_time_cap,
            anchor_cap=DEFAULT_CONFIG.constructor_anchor_cap,
        )
        if inserted is None:
            budget.checkpoint("S4 assignment bounded escalated repair")
            inserted = insert_block(
                state,
                block_id,
                bays_try=(selected.bay_id,),
                preferred_orient_idx=selected.orient_idx,
                time_cap=4 * DEFAULT_CONFIG.constructor_time_cap,
                anchor_cap=2 * DEFAULT_CONFIG.constructor_anchor_cap,
            )
        if inserted is None or inserted.placement.bay_id != selected.bay_id:
            raise ValueError(f"assignment delta could not repair block {block_id}")
        state.place(inserted.placement)
    state.assert_invariants()
    return state


def _run_cross_bay_refinement(
    current_state: SolutionState,
    incumbent: VerifiedIncumbent,
    budget: Budget,
    *,
    seed: int,
    backend_calls: dict[str, Callable[..., Any]],
    available_backends: tuple[str, ...],
    max_z2: float | None,
    telemetry: dict[str, Any] | None,
) -> SolutionState:
    rng = Generator(PCG64(seed))
    registry = CrossBayRegistry()
    attempts: list[dict[str, Any]] = []
    retime_attempts = 0

    def retime_affected(
        state: SolutionState,
        bay_id: int,
        active_budget: Budget | None,
    ) -> SolutionState | None:
        nonlocal retime_attempts
        if active_budget is None:
            return None
        for backend in available_backends:
            active_budget.checkpoint("S4 cross-bay retime backend")
            retime_attempts += 1
            outcome = retime_bay(
                state,
                bay_id,
                backend,
                backend_calls[backend],
                budget=active_budget,
                seed=seed,
                threads=DEFAULT_CONFIG.retime_threads,
                call_timebox_cap=min(
                    DEFAULT_CONFIG.retime_timebox_seconds,
                    max(active_budget.remaining, 0.0),
                ),
                require_certified_optimal=True,
            )
            if outcome.candidate is not None:
                return outcome.candidate
        return _copy_solution_state(state)

    budget.checkpoint("S4 before cross-bay ranking")
    ranked_candidates = rank_cross_bay_candidates(
        current_state,
        registry=registry,
        budget=budget,
    )
    baseline_z2 = None if max_z2 is None else float(max_z2)
    for kind in ("move", "swap"):
        candidates = tuple(
            candidate
            for candidate in ranked_candidates
            if candidate.kind == kind
            and (
                baseline_z2 is None
                or candidate.after_z2
                <= baseline_z2 + 1e-9 * max(1.0, abs(baseline_z2))
            )
        )
        if not candidates:
            attempts.append({"kind": kind, "attempted": False, "accepted": False})
            continue
        for selected in candidates:
            budget.checkpoint(f"S4 {kind} candidate")
            result = run_cross_bay_candidate(
                current_state,
                incumbent,
                selected,
                rng,
                retime=retime_affected,
                budget=budget,
            )
            attempts.append(
                {
                    "kind": kind,
                    "attempted": True,
                    "accepted": result.committed,
                    "reason": result.reason,
                    "block_ids": list(selected.block_ids),
                    "delta_z2": selected.delta_z2,
                    "delta_z3": selected.delta_z3,
                    "checker_objective": result.checker_objective,
                }
            )
            if result.committed:
                baseline_z2 = incumbent.checker_result.obj2
                break
    if telemetry is not None:
        telemetry.update(
            cross_bay_attempts=attempts,
            cross_bay_move_attempts=sum(
                item["kind"] == "move" and item["attempted"] for item in attempts
            ),
            cross_bay_move_accepted=sum(
                item["kind"] == "move" and item["accepted"] for item in attempts
            ),
            cross_bay_swap_attempts=sum(
                item["kind"] == "swap" and item["attempted"] for item in attempts
            ),
            cross_bay_swap_accepted=sum(
                item["kind"] == "swap" and item["accepted"] for item in attempts
            ),
            cross_bay_retime_attempts=retime_attempts,
        )
    return current_state


def _copy_solution_state(state: SolutionState) -> SolutionState:
    copied = SolutionState(
        state.instance,
        shape_catalog=state.shape_catalog,
        geom=state.geom,
    )
    for placement in state.placements.values():
        copied.place(placement)
    copied.assert_invariants()
    return copied


def _entry_main(argv: list[str]) -> int:
    if len(argv) == 3 and argv[0] == "--s4-worker":
        return _s4_worker_main(Path(argv[1]), Path(argv[2]))
    return 4


if __name__ == "__main__":
    raise SystemExit(_entry_main(sys.argv[1:]))
