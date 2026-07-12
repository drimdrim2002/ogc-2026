"""Safe incumbent first orchestration and public exception armor."""

from __future__ import annotations

import copy
import time
from dataclasses import dataclass
from collections.abc import Callable, Iterable, Mapping
from typing import Any

try:
    from utils import check_feasibility
except ImportError:  # package import from the repository root
    from baseline.utils import check_feasibility

from .budget import Budget
from .fallback import build_safe_candidate
from .instance import parse_instance
from .serialize import serialize
from .state import IncumbentStore, SolutionSnapshot, compute_objective


class SafeIncumbentError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class OptionalPhaseResult:
    """Forward-compatible optional-phase output; assignment seeds are guides only."""

    candidates: tuple[SolutionSnapshot, ...] = ()
    assignment_portfolio: Any | None = None


def load_optional_phase():
    """Import assignment only after the safe incumbent has passed the checker."""
    from .assignment import try_assignment_portfolio
    from .geometry import GeometryKernel

    def assignment_phase(instance, snapshot, budget):
        del snapshot  # Seeds guide step 4; they never replace the incumbent directly.
        geometry = GeometryKernel.from_instance(instance)
        portfolio = try_assignment_portfolio(instance, geometry, budget)
        return OptionalPhaseResult(assignment_portfolio=portfolio)

    return assignment_phase


def _candidate_stream(value: Any) -> Iterable[SolutionSnapshot]:
    if value is None:
        return ()
    if isinstance(value, OptionalPhaseResult):
        return value.candidates
    if isinstance(value, SolutionSnapshot):
        return (value,)
    return value


def solve(
    prob_info: Mapping[str, Any],
    timelimit: float,
    checker: Callable[[dict, dict], dict] = check_feasibility,
    optional_phase_loader: Callable[[], Any] | None = None,
) -> dict:
    raw = copy.deepcopy(dict(prob_info))
    budget = Budget.start(timelimit)
    instance = parse_instance(raw)
    fallback = build_safe_candidate(instance, budget)
    fallback_operations = serialize(fallback)

    check_started = time.monotonic()
    checker_result = checker(copy.deepcopy(raw), copy.deepcopy(fallback_operations))
    budget.record_checker_duration(time.monotonic() - check_started)
    store = IncumbentStore(instance)
    if not store.install_if_valid(fallback, fallback_operations, checker_result):
        raise SafeIncumbentError(
            "mandatory safe candidate failed full checker validation: "
            f"stage={checker_result.get('stage')} violations={checker_result.get('violations')}"
        )

    if float(timelimit) < 2.0 or not budget.can_start(0.0, margin=0.01):
        return store.operations

    loader = optional_phase_loader or load_optional_phase
    try:
        phase = loader()
        if phase is None:
            return store.operations
        candidates = _candidate_stream(phase(instance, store.snapshot, budget))
        for candidate in candidates:
            if not budget.can_start(budget.checker_p95, margin=0.01):
                break
            if not isinstance(candidate, SolutionSnapshot):
                continue
            candidate = candidate.with_objective(compute_objective(instance, candidate))
            candidate_operations = serialize(candidate)
            check_started = time.monotonic()
            candidate_result = checker(copy.deepcopy(raw), copy.deepcopy(candidate_operations))
            budget.record_checker_duration(time.monotonic() - check_started)
            store.install_if_valid(candidate, candidate_operations, candidate_result)
    except Exception:
        return store.operations
    return store.operations
