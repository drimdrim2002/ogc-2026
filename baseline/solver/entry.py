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


LNS_ENABLED = False


class SafeIncumbentError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class OptionalPhaseResult:
    """Constructor candidates plus the optional validated-snapshot retimer."""

    candidates: tuple[SolutionSnapshot, ...] = ()
    assignment_portfolio: Any | None = None
    precedence_provider: Any | None = None
    retiming_results: tuple[Any, ...] = ()
    retimer: Callable[..., Any] | None = None
    lns_runner: Callable[..., Any] | None = None


def load_optional_phase():
    """Import optional assignment/constructor code after the safe full check."""
    from .assignment import try_assignment_portfolio
    from .construct import construct_portfolio
    from .geometry import GeometryKernel
    from .retime import retime

    def assignment_phase(instance, snapshot, budget):
        del snapshot  # Seeds guide step 4; they never replace the incumbent directly.
        geometry = GeometryKernel.from_instance(instance)
        portfolio = try_assignment_portfolio(instance, geometry, budget)
        construction = construct_portfolio(instance, geometry, portfolio, budget)
        lns_runner = None
        if LNS_ENABLED:
            from .alns import AlnsConfig, AlnsContext, run_lns
            from .neighborhoods import heuristic_repair
            from .repair_mip import make_mip_repair_engine

            repair_engines = (heuristic_repair, make_mip_repair_engine())

            def lns_runner(initial, store, raw, checker, lns_budget):
                context = AlnsContext(
                    instance=instance,
                    kernel=geometry,
                    raw=raw,
                    checker=checker,
                    repair_engines=repair_engines,
                )
                return run_lns(
                    initial,
                    store,
                    context,
                    lns_budget,
                    AlnsConfig(),
                    retime_hook=retime,
                )

        return OptionalPhaseResult(
            candidates=tuple(
                result.snapshot
                for result in construction
                if result.complete and result.snapshot is not None
            ),
            assignment_portfolio=portfolio,
            precedence_provider=geometry,
            retimer=retime,
            lns_runner=lns_runner,
        )

    return assignment_phase


def _candidate_stream(value: Any) -> Iterable[tuple[SolutionSnapshot, Any | None]]:
    if value is None:
        return ()
    if isinstance(value, OptionalPhaseResult):
        return tuple((candidate, value.precedence_provider) for candidate in value.candidates)
    if isinstance(value, SolutionSnapshot):
        return ((value, None),)
    return tuple((candidate, None) for candidate in value)


def _check_and_install(
    *,
    raw: dict,
    instance: Any,
    budget: Budget,
    checker: Callable[[dict, dict], dict],
    store: IncumbentStore,
    candidate: SolutionSnapshot,
    precedence_provider: Any | None,
) -> bool:
    candidate = candidate.with_objective(compute_objective(instance, candidate))
    candidate_operations = serialize(candidate, precedence_provider)
    check_started = time.monotonic()
    candidate_result = checker(copy.deepcopy(raw), copy.deepcopy(candidate_operations))
    budget.record_checker_duration(time.monotonic() - check_started)
    return store.install_if_valid(candidate, candidate_operations, candidate_result)


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
        phase_result = phase(instance, store.snapshot, budget)
        retimer = phase_result.retimer if isinstance(phase_result, OptionalPhaseResult) else None
        lns_runner = (
            phase_result.lns_runner
            if isinstance(phase_result, OptionalPhaseResult)
            else None
        )
        candidates = _candidate_stream(phase_result)
        for candidate, precedence_provider in candidates:
            if not budget.can_start(budget.checker_p95, margin=0.01):
                break
            if not isinstance(candidate, SolutionSnapshot):
                continue
            try:
                installed = _check_and_install(
                    raw=raw,
                    instance=instance,
                    budget=budget,
                    checker=checker,
                    store=store,
                    candidate=candidate,
                    precedence_provider=precedence_provider,
                )
            except Exception:
                continue
            if not installed or retimer is None:
                continue

            # The retimer is optional and candidate-local.  Its input is always
            # the immutable checker-validated snapshot just installed above.
            try:
                retimed = retimer(store.snapshot, instance, precedence_provider, budget)
            except Exception:
                continue
            retimed_snapshot = getattr(retimed, "snapshot", None)
            if not isinstance(retimed_snapshot, SolutionSnapshot) or retimed_snapshot is store.snapshot:
                continue
            retimed_operations = getattr(retimed, "operations", None)
            retimed_checker = getattr(retimed, "checker_result", None)
            if retimed_operations is not None and retimed_checker is not None:
                store.install_if_valid(retimed_snapshot, retimed_operations, retimed_checker)
            elif budget.can_start(budget.checker_p95, margin=0.01):
                # Backward-compatible hook boundary for custom phases.  The
                # production retimer returns its canonical full-check evidence.
                _check_and_install(
                    raw=raw,
                    instance=instance,
                    budget=budget,
                    checker=checker,
                    store=store,
                    candidate=retimed_snapshot,
                    precedence_provider=precedence_provider,
                )
        if lns_runner is not None and budget.can_start(0.0, margin=0.01):
            try:
                lns_runner(store.snapshot, store, raw, checker, budget)
            except Exception:
                pass
    except Exception:
        return store.operations
    return store.operations
