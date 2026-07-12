"""Verified-incumbent entry shell with calibrated S1 constructor armor."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from .budget import Budget
from .config import DEFAULT_CONFIG
from .construct import construct_multistart
from .incumbent import VerifiedIncumbent
from .instance import ProblemInstance
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
    _seed: int | None = None,
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
        incumbent.register_initial(build_t0(instance))

        _inject_fault(_fault, "after_incumbent")
        constructor_enabled = (
            DEFAULT_CONFIG.constructor if _constructor is None else _constructor
        )
        if constructor_enabled:
            _inject_fault(_fault, "during_constructor")
            remaining = budget.hard_remaining
            reserve = min(
                DEFAULT_CONFIG.constructor_return_reserve_seconds,
                max(remaining - 0.05, 0.0),
            )
            constructor_budget = Budget(remaining, reserve=reserve)
            construct_multistart(
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
        budget.checkpoint("post-constructor pipeline")
        return incumbent.solution
    except Exception:
        if incumbent is None or not incumbent.has_incumbent:
            raise
        return incumbent.solution


def _inject_fault(fault: FaultHook, point: str) -> None:
    if fault is None:
        return
    if callable(fault):
        fault(point)
        return
    known_points = {"after_incumbent", "during_constructor"}
    if fault not in known_points:
        raise ValueError(f"unknown entry fault point: {fault}")
    if fault == point:
        raise InjectedEntryFault(f"injected entry fault at {point}")
