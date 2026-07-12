"""T0-only production entry with verified-incumbent exception armor."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from .budget import Budget
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
) -> dict[str, Any]:
    """Build, verify, and return T0; preserve it after ordinary exceptions.

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
        budget.checkpoint("post-incumbent pipeline")
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
    if fault == point:
        raise InjectedEntryFault(f"injected entry fault at {point}")
    raise ValueError(f"unknown entry fault point: {fault}")
