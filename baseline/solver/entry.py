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
from .runtime import (
    LONG_BUDGET_ANCHOR_SECONDS,
    RunTrace,
    SubmissionConfig,
    constructor_candidate_limit,
    constructor_profile_limit,
    lns_iteration_limit,
    phase_name,
)


LNS_ENABLED = True
INTERLOCK_ENABLED = False


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


def load_optional_phase(
    config: SubmissionConfig | None = None,
    trace: RunTrace | None = None,
):
    """Import optional assignment/constructor code after the safe full check."""
    from .assignment import AssignmentConfig, try_assignment_portfolio
    from .construct import ConstructorConfig, construct_portfolio
    from .geometry import GeometryKernel
    from .retime import RetimingConfig, retime

    chosen = config or SubmissionConfig(
        lns_enabled=LNS_ENABLED,
        interlock_enabled=INTERLOCK_ENABLED,
    )

    def assignment_phase(instance, snapshot, budget):
        del snapshot  # Seeds guide step 4; they never replace the incumbent directly.
        phase = phase_name(budget.limit)
        geometry = GeometryKernel.from_instance(instance)
        portfolio = None
        if chosen.assignment_enabled:
            started = time.monotonic()
            portfolio = try_assignment_portfolio(
                instance,
                geometry,
                budget,
                AssignmentConfig(seed=chosen.seed),
            )
            if trace is not None:
                trace.add_phase_time("assignment", time.monotonic() - started)
                trace.assignment = {
                    "status": portfolio.status,
                    "runtime": portfolio.runtime,
                    "gap": portfolio.gap,
                    "lower_bound": portfolio.lower_bound,
                    "seed_count": len(portfolio.seeds),
                    "diagnostics": portfolio.diagnostics[:64],
                }
        construction = ()
        if chosen.constructor_enabled:
            construction = construct_portfolio(
                instance,
                geometry,
                portfolio,
                budget,
                ConstructorConfig(
                    seed=chosen.seed,
                    max_profiles=constructor_profile_limit(budget.limit),
                    max_candidate_attempts=constructor_candidate_limit(budget.limit),
                    selection_policy=chosen.constructor_selection_policy,
                ),
            )
            if trace is not None:
                for result in construction:
                    trace.construction.append(
                        {
                            "status": result.status,
                            "complete": result.complete,
                            "metrics": result.metrics,
                        }
                    )
                    trace.add_phase_time(
                        "constructor", result.metrics.construction_time
                    )
        lns_runner = None
        if chosen.lns_enabled and phase in {"medium", "long"}:
            from .alns import AlnsConfig, AlnsContext, run_lns
            from .neighborhoods import heuristic_repair

            repair_engines = (heuristic_repair,)
            if chosen.mip_enabled:
                from .repair_mip import MipRepairConfig, make_mip_repair_engine

                repair_engines += (
                    make_mip_repair_engine(
                        config=MipRepairConfig(seed=chosen.seed)
                    ),
                )
            densify_hook = None
            if chosen.interlock_enabled and phase == "long":
                from .interlock import InterlockConfig, InterlockContext, densify

                def densify_hook(initial, store, alns_context, densify_budget, retime_hook):
                    interlock_context = InterlockContext(
                        instance=alns_context.instance,
                        kernel=alns_context.kernel,
                        raw=alns_context.raw,
                        checker=alns_context.checker,
                        stalled=True,
                        config=InterlockConfig(enabled=True),
                        clock=alns_context.clock,
                    )
                    return densify(
                        initial,
                        store,
                        interlock_context,
                        densify_budget,
                        retime_hook,
                    )

            search_state = None

            def lns_runner(initial, store, raw, checker, lns_budget):
                nonlocal search_state
                context = AlnsContext(
                    instance=instance,
                    kernel=geometry,
                    raw=raw,
                    checker=checker,
                    repair_engines=repair_engines,
                )
                result = run_lns(
                    initial,
                    store,
                    context,
                    lns_budget,
                    AlnsConfig(
                        seed=chosen.seed,
                        max_iterations=lns_iteration_limit(lns_budget.limit),
                        stall_time_fraction=0.0,
                        neighborhood_policy=chosen.neighborhood_policy,
                    ),
                    retime_hook=(
                        lambda *args, **kwargs: retime(
                            *args,
                            **kwargs,
                            config=RetimingConfig(
                                seed=chosen.seed,
                                exact_z1_skip=chosen.retime_exact_z1_skip,
                            ),
                        )
                        if chosen.retime_enabled
                        else None
                    ),
                    densify_hook=densify_hook,
                    state=search_state,
                )
                search_state = result.state
                return result

        return OptionalPhaseResult(
            candidates=tuple(
                result.snapshot
                for result in construction
                if result.complete and result.snapshot is not None
            ),
            assignment_portfolio=portfolio,
            precedence_provider=geometry,
            retimer=(
                lambda *args, **kwargs: retime(
                    *args,
                    **kwargs,
                    config=RetimingConfig(
                        seed=chosen.seed,
                        exact_z1_skip=chosen.retime_exact_z1_skip,
                    ),
                )
                if chosen.retime_enabled
                else None
            ),
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
    trace: RunTrace | None = None,
) -> bool:
    candidate = candidate.with_objective(compute_objective(instance, candidate))
    candidate_operations = serialize(candidate, precedence_provider)
    check_started = time.monotonic()
    candidate_result = checker(copy.deepcopy(raw), copy.deepcopy(candidate_operations))
    duration = time.monotonic() - check_started
    budget.record_checker_duration(duration)
    if trace is not None:
        trace.add_checker(duration)
    installed = store.install_if_valid(candidate, candidate_operations, candidate_result)
    if installed and trace is not None and candidate.objective is not None:
        trace.add_best(candidate.objective.total)
    return installed


def solve(
    prob_info: Mapping[str, Any],
    timelimit: float,
    checker: Callable[[dict, dict], dict] = check_feasibility,
    optional_phase_loader: Callable[[], Any] | None = None,
    *,
    config: SubmissionConfig | None = None,
    trace: RunTrace | None = None,
) -> dict:
    chosen = config or SubmissionConfig.from_defaults()
    raw = copy.deepcopy(dict(prob_info))
    budget = Budget.start(timelimit)
    if trace is not None:
        trace.phase = phase_name(budget.limit)
    instance = parse_instance(raw)
    fallback_started = time.monotonic()
    fallback = build_safe_candidate(instance, budget)
    if trace is not None:
        trace.add_phase_time("fallback", time.monotonic() - fallback_started)
    fallback_operations = serialize(fallback)

    check_started = time.monotonic()
    checker_result = checker(copy.deepcopy(raw), copy.deepcopy(fallback_operations))
    duration = time.monotonic() - check_started
    budget.record_checker_duration(duration)
    if trace is not None:
        trace.add_checker(duration)
    store = IncumbentStore(instance)
    if not store.install_if_valid(fallback, fallback_operations, checker_result):
        raise SafeIncumbentError(
            "mandatory safe candidate failed full checker validation: "
            f"stage={checker_result.get('stage')} violations={checker_result.get('violations')}"
        )
    if trace is not None and fallback.objective is not None:
        trace.add_best(fallback.objective.total)

    if float(timelimit) < 2.0 or not budget.can_start(0.0, margin=0.01):
        return store.operations
    if not any(
        (
            chosen.assignment_enabled,
            chosen.constructor_enabled,
            chosen.retime_enabled,
            chosen.lns_enabled,
            chosen.mip_enabled,
            chosen.interlock_enabled,
        )
    ):
        return store.operations

    search_budget = (
        budget.anchor(LONG_BUDGET_ANCHOR_SECONDS)
        if budget.limit >= LONG_BUDGET_ANCHOR_SECONDS
        else budget
    )

    loader = optional_phase_loader or (lambda: load_optional_phase(chosen, trace))
    try:
        phase = loader()
        if phase is None:
            return store.operations
        phase_result = phase(instance, store.snapshot, search_budget)
        retimer = phase_result.retimer if isinstance(phase_result, OptionalPhaseResult) else None
        lns_runner = (
            phase_result.lns_runner
            if isinstance(phase_result, OptionalPhaseResult)
            else None
        )
        candidates = _candidate_stream(phase_result)
        for candidate, precedence_provider in candidates:
            if not search_budget.can_start(search_budget.checker_p95, margin=0.01):
                break
            if not isinstance(candidate, SolutionSnapshot):
                continue
            try:
                installed = _check_and_install(
                    raw=raw,
                    instance=instance,
                    budget=search_budget,
                    checker=checker,
                    store=store,
                    candidate=candidate,
                    precedence_provider=precedence_provider,
                    trace=trace,
                )
            except Exception as exc:
                if trace is not None:
                    trace.add_exception("candidate_checker", exc)
                continue
            if not installed or retimer is None:
                continue

            # The retimer is optional and candidate-local.  Its input is always
            # the immutable checker-validated snapshot just installed above.
            try:
                retimed = retimer(
                    store.snapshot, instance, precedence_provider, search_budget
                )
            except Exception as exc:
                if trace is not None:
                    trace.add_exception("retime", exc)
                continue
            if trace is not None:
                trace.retiming.append(
                    {
                        "status": getattr(retimed, "status", None),
                        "runtime": getattr(retimed, "runtime", None),
                        "primary": getattr(retimed, "primary", None),
                        "bound": getattr(retimed, "bound", None),
                        "gap": getattr(retimed, "gap", None),
                        "diagnostics": getattr(retimed, "diagnostics", ())[:64],
                    }
                )
                trace.add_phase_time("retime", float(getattr(retimed, "runtime", 0.0)))
            retimed_snapshot = getattr(retimed, "snapshot", None)
            if not isinstance(retimed_snapshot, SolutionSnapshot) or retimed_snapshot is store.snapshot:
                continue
            retimed_operations = getattr(retimed, "operations", None)
            retimed_checker = getattr(retimed, "checker_result", None)
            if retimed_operations is not None and retimed_checker is not None:
                installed = store.install_if_valid(
                    retimed_snapshot, retimed_operations, retimed_checker
                )
                if installed and trace is not None and retimed_snapshot.objective is not None:
                    trace.add_best(retimed_snapshot.objective.total)
            elif search_budget.can_start(
                search_budget.checker_p95, margin=0.01
            ):
                # Backward-compatible hook boundary for custom phases.  The
                # production retimer returns its canonical full-check evidence.
                _check_and_install(
                    raw=raw,
                    instance=instance,
                    budget=search_budget,
                    checker=checker,
                    store=store,
                    candidate=retimed_snapshot,
                    precedence_provider=precedence_provider,
                    trace=trace,
                )
        if lns_runner is not None and search_budget.can_start(0.0, margin=0.01):
            lns_started = time.monotonic()
            try:
                lns_result = lns_runner(
                    store.snapshot, store, raw, checker, search_budget
                )
            except Exception as exc:
                if trace is not None:
                    trace.add_lns_invocation(
                        kind="anchor",
                        started=lns_started,
                        ended=time.monotonic(),
                        budget_seconds=search_budget.limit,
                        error=exc,
                    )
                    trace.add_exception("lns", exc)
            else:
                if trace is not None:
                    trace.add_lns_invocation(
                        kind="anchor",
                        started=lns_started,
                        ended=time.monotonic(),
                        budget_seconds=search_budget.limit,
                        result=lns_result,
                    )
        if (
            lns_runner is not None
            and budget.limit > search_budget.limit + 1e-9
            and budget.can_start(0.0, margin=0.01)
        ):
            lns_started = time.monotonic()
            try:
                lns_result = lns_runner(store.snapshot, store, raw, checker, budget)
            except Exception as exc:
                if trace is not None:
                    trace.add_lns_invocation(
                        kind="extension",
                        started=lns_started,
                        ended=time.monotonic(),
                        budget_seconds=budget.limit,
                        error=exc,
                    )
                    trace.add_exception("lns_extension", exc)
            else:
                if trace is not None:
                    trace.add_lns_invocation(
                        kind="extension",
                        started=lns_started,
                        ended=time.monotonic(),
                        budget_seconds=budget.limit,
                        result=lns_result,
                    )
    except Exception as exc:
        if trace is not None:
            trace.add_exception("optional_phase", exc)
        return store.operations
    return store.operations
