"""Verified-incumbent entry shell with calibrated S1 constructor armor."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import asdict
import time
from typing import Any

from .budget import Budget
from .config import DEFAULT_CONFIG
from .construct import construct_multistart
from .cpsat_backend import retime_cpsat
from .gurobi_backend import retime_gurobi
from .incumbent import VerifiedIncumbent
from .instance import ProblemInstance
from .retime import retime_sweep
from .trivial import build_t0


class InjectedEntryFault(RuntimeError):
    """Test-only fault raised after a verified incumbent exists."""


FaultHook = str | Callable[[str], None] | None


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
    _telemetry: dict[str, Any] | None = None,
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
            backend_calls = {
                "gurobi": retime_gurobi,
                "cpsat": retime_cpsat,
            }
            available = (
                ("gurobi", "cpsat")
                if DEFAULT_CONFIG.retime_backend == "auto"
                else (DEFAULT_CONFIG.retime_backend,)
            )
            exact_budget = Budget(budget.remaining, reserve=0.0)
            initial = retime_sweep(
                current_state,
                incumbent,
                backend_calls,
                available_backends=available,
                budget=exact_budget,
                first_sweep_budget=exact_budget.remaining,
                seed=(DEFAULT_CONFIG.constructor_seed if _seed is None else _seed),
                threads=DEFAULT_CONFIG.retime_threads,
                call_timebox_cap=timebox,
                pilot_budget_cap=pilot_budget,
            )
            current_state = initial.state
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
        if _telemetry is not None:
            _telemetry["incumbent_verification_count"] = incumbent.verification_count
        return incumbent.solution
    except Exception as exc:
        if incumbent is None or not incumbent.has_incumbent:
            raise
        if _telemetry is not None:
            _telemetry["fallback_reason"] = f"{type(exc).__name__}: {exc}"
            _telemetry["incumbent_verification_count"] = incumbent.verification_count
        return incumbent.solution


def _inject_fault(fault: FaultHook, point: str) -> None:
    if fault is None:
        return
    if callable(fault):
        fault(point)
        return
    known_points = {"after_incumbent", "during_constructor", "during_retime"}
    if fault not in known_points:
        raise ValueError(f"unknown entry fault point: {fault}")
    if fault == point:
        raise InjectedEntryFault(f"injected entry fault at {point}")
