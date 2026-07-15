"""Exact fixed-layout four-state retiming with guarded Gurobi use.

The public function is deliberately backend-neutral and transactional.  It
builds checker-equivalent relation requests in pure Python, calls a lazily
loaded optimizer, and exposes a candidate only after relation, canonical
serialization, full-checker, and objective-parity validation.
"""

from __future__ import annotations

import copy
import math
import time
from collections import defaultdict, deque
from collections.abc import Callable, Mapping
from dataclasses import dataclass, replace
from itertools import combinations
from typing import Any

from .budget import Budget
from .geometry import GeometryKernel, PairRelation, PairState, TemporalMode
from .instance import Instance
from .serialize import serialize
from .state import Placement, SolutionSnapshot, compute_objective


@dataclass(frozen=True, slots=True)
class RetimingConfig:
    max_free: int = 80
    time_cap_s: float = 3.0
    threads: int = 4
    seed: int = 20260710
    exact_z1_skip: bool = False

    def __post_init__(self) -> None:
        if isinstance(self.max_free, bool) or not isinstance(self.max_free, int):
            raise TypeError("max_free must be an integer")
        if isinstance(self.threads, bool) or not isinstance(self.threads, int):
            raise TypeError("threads must be an integer")
        if isinstance(self.seed, bool) or not isinstance(self.seed, int):
            raise TypeError("seed must be an integer")
        if not isinstance(self.exact_z1_skip, bool):
            raise TypeError("exact_z1_skip must be a boolean")
        if self.max_free <= 0 or not 1 <= self.threads <= 4:
            raise ValueError("max_free must be positive and Gurobi threads must be 1..4")
        if (
            isinstance(self.time_cap_s, bool)
            or not isinstance(self.time_cap_s, (int, float))
            or not math.isfinite(float(self.time_cap_s))
            or self.time_cap_s <= 0
        ):
            raise ValueError("time_cap_s must be a positive finite number")


@dataclass(frozen=True, slots=True)
class RetimingPair:
    i: int
    k: int
    relation: PairRelation
    warm_mode: TemporalMode


@dataclass(frozen=True, slots=True)
class RetimingRequest:
    snapshot: SolutionSnapshot
    free_ids: frozenset[int]
    modeled_ids: frozenset[int]
    horizons: tuple[tuple[int, int], ...]
    pairs: tuple[RetimingPair, ...]
    max_component_free: int


@dataclass(frozen=True, slots=True)
class BackendResult:
    status: str
    dates: tuple[tuple[int, int, int], ...] = ()
    primary: float | None = None
    bound: float | None = None
    gap: float | None = None
    secondary: float | None = None
    runtime: float = 0.0
    variables: int = 0
    constraints: int = 0
    diagnostics: tuple[str, ...] = ()
    phase_times: tuple[tuple[str, float], ...] = ()


@dataclass(frozen=True, slots=True)
class RetimingResult:
    snapshot: SolutionSnapshot
    status: str
    primary: float | None
    bound: float | None
    gap: float | None
    runtime: float
    changed_ids: frozenset[int]
    diagnostics: tuple[str, ...]
    operations: Mapping[str, Any] | None = None
    checker_result: Mapping[str, Any] | None = None
    telemetry: Mapping[str, Any] | None = None


class _RetimingDeadline(RuntimeError):
    pass


def bay_horizon(instance: Instance, snapshot: SolutionSnapshot, bay_id: int) -> int:
    ids = [item.block_id for item in snapshot.placements if item.bay_id == bay_id]
    if not ids:
        return 0
    return max(0, max(instance.block(block_id).release_time for block_id in ids)) + sum(
        instance.block(block_id).dwell for block_id in ids
    )


def relation_modes(relation: PairRelation) -> tuple[TemporalMode, ...]:
    if relation.state is PairState.FREE:
        return (TemporalMode.FREE,)
    if relation.state is PairState.SEPARATE:
        return (TemporalMode.I_BEFORE, TemporalMode.K_BEFORE)
    if relation.state is PairState.I_OUTER:
        return (
            TemporalMode.I_BEFORE,
            TemporalMode.K_BEFORE,
            TemporalMode.K_NESTED,
        )
    return (
        TemporalMode.I_BEFORE,
        TemporalMode.K_BEFORE,
        TemporalMode.I_NESTED,
    )


def mode_allows(
    relation: PairRelation,
    mode: TemporalMode,
    interval_i: tuple[int, int],
    interval_k: tuple[int, int],
) -> bool:
    return mode in relation_modes(relation) and relation.mode(interval_i, interval_k) is mode


def nonfree_components(
    snapshot: SolutionSnapshot,
    kernel: GeometryKernel,
    bay_id: int,
    budget: Budget | None = None,
) -> tuple[frozenset[int], ...]:
    placements = sorted(
        (item for item in snapshot.placements if item.bay_id == bay_id),
        key=lambda item: item.block_id,
    )
    adjacency = {item.block_id: set() for item in placements}
    for left, right in combinations(placements, 2):
        if budget is not None and budget.search_remaining() <= 0.0:
            raise _RetimingDeadline("component relation scan reached its deadline")
        if kernel.relation(left, right).state is not PairState.FREE:
            adjacency[left.block_id].add(right.block_id)
            adjacency[right.block_id].add(left.block_id)
    components: list[frozenset[int]] = []
    unseen = set(adjacency)
    while unseen:
        root = min(unseen)
        queue = deque((root,))
        current: set[int] = set()
        unseen.remove(root)
        while queue:
            node = queue.popleft()
            current.add(node)
            for neighbor in sorted(adjacency[node]):
                if neighbor in unseen:
                    unseen.remove(neighbor)
                    queue.append(neighbor)
        components.append(frozenset(current))
    return tuple(sorted(components, key=lambda item: (min(item), len(item))))


def _critical_ids(
    component: frozenset[int],
    snapshot: SolutionSnapshot,
    instance: Instance,
    max_free: int,
    affected: frozenset[int] | None,
) -> frozenset[int]:
    by_id = {item.block_id: item for item in snapshot.placements}
    preferred = component & affected if affected is not None else frozenset()
    ordered = sorted(
        component,
        key=lambda block_id: (
            block_id not in preferred,
            -max(0, by_id[block_id].exit - instance.block(block_id).due_date),
            instance.block(block_id).due_date,
            block_id,
        ),
    )
    return frozenset(ordered[:max_free])


def build_requests(
    snapshot: SolutionSnapshot,
    instance: Instance,
    kernel: GeometryKernel,
    config: RetimingConfig,
    affected_ids: frozenset[int] | None = None,
    budget: Budget | None = None,
    *,
    phase_times: dict[str, float] | None = None,
) -> tuple[RetimingRequest, ...]:
    by_id = {item.block_id: item for item in snapshot.placements}
    if affected_ids is not None and not affected_ids <= set(by_id):
        raise ValueError("affected_ids contains an unknown block")
    requests: list[RetimingRequest] = []
    for bay in instance.bays:
        phase_started = time.monotonic()
        components = nonfree_components(snapshot, kernel, bay.index, budget)
        if phase_times is not None:
            phase_times["affected_component_discovery"] += (
                time.monotonic() - phase_started
            )
        for component in components:
            if budget is not None and budget.search_remaining() <= 0.0:
                raise _RetimingDeadline("request construction reached its deadline")
            if affected_ids is not None and component.isdisjoint(affected_ids):
                continue
            phase_started = time.monotonic()
            selected = _critical_ids(
                component, snapshot, instance, config.max_free, affected_ids
            )
            if phase_times is not None:
                phase_times["request_construction"] += time.monotonic() - phase_started
            pairs: list[RetimingPair] = []
            phase_started = time.monotonic()
            for i, k in combinations(sorted(component), 2):
                if i not in selected and k not in selected:
                    continue
                if budget is not None and budget.search_remaining() <= 0.0:
                    raise _RetimingDeadline("request pair scan reached its deadline")
                relation = kernel.relation(by_id[i], by_id[k])
                if relation.state is PairState.FREE:
                    continue
                warm_mode = relation.mode(by_id[i], by_id[k])
                if warm_mode is TemporalMode.INVALID:
                    raise ValueError("input snapshot violates a fixed-layout relation")
                pairs.append(RetimingPair(i, k, relation, warm_mode))
            if phase_times is not None:
                phase_times["relation_pair_preprocessing"] += (
                    time.monotonic() - phase_started
                )
            phase_started = time.monotonic()
            requests.append(
                RetimingRequest(
                    snapshot=snapshot,
                    free_ids=selected,
                    modeled_ids=component,
                    horizons=((bay.index, bay_horizon(instance, snapshot, bay.index)),),
                    pairs=tuple(pairs),
                    max_component_free=len(selected),
                )
            )
            if phase_times is not None:
                phase_times["request_construction"] += time.monotonic() - phase_started
    return tuple(requests)


def build_request(
    snapshot: SolutionSnapshot,
    instance: Instance,
    kernel: GeometryKernel,
    config: RetimingConfig,
    affected_ids: frozenset[int] | None = None,
    budget: Budget | None = None,
) -> RetimingRequest:
    """Build aggregate metadata for compatibility; backend solves use build_requests."""
    requests = build_requests(
        snapshot, instance, kernel, config, affected_ids, budget
    )
    if not requests:
        return RetimingRequest(snapshot, frozenset(), frozenset(), (), (), 0)
    horizons = tuple(sorted({item for request in requests for item in request.horizons}))
    return RetimingRequest(
        snapshot=snapshot,
        free_ids=frozenset().union(*(request.free_ids for request in requests)),
        modeled_ids=frozenset().union(*(request.modeled_ids for request in requests)),
        horizons=horizons,
        pairs=tuple(pair for request in requests for pair in request.pairs),
        max_component_free=max(request.max_component_free for request in requests),
    )


def _status_name(gp: Any, status: int) -> str:
    names = {
        gp.GRB.OPTIMAL: "OPTIMAL",
        gp.GRB.TIME_LIMIT: "TIME_LIMIT",
        gp.GRB.SUBOPTIMAL: "SUBOPTIMAL",
        gp.GRB.INFEASIBLE: "INFEASIBLE",
        gp.GRB.INF_OR_UNBD: "INF_OR_UNBD",
        gp.GRB.UNBOUNDED: "UNBOUNDED",
        gp.GRB.NUMERIC: "NUMERIC",
        gp.GRB.INTERRUPTED: "INTERRUPTED",
    }
    return names.get(status, f"STATUS_{status}")


class _GurobiBackend:
    def solve(
        self,
        request: RetimingRequest,
        instance: Instance,
        time_limit: float,
        config: RetimingConfig,
    ) -> BackendResult:
        started = time.monotonic()
        model_construction = 0.0
        optimize = 0.0
        extraction = 0.0

        def measured_phases() -> tuple[tuple[str, float], ...]:
            return (
                ("model_construction", model_construction),
                ("optimize", optimize),
                ("extraction", extraction),
            )

        try:
            import gurobipy as gp

            deadline = started + max(0.0, float(time_limit))

            def remaining() -> float:
                return max(0.0, deadline - time.monotonic())

            build_started = time.monotonic()
            by_id = {item.block_id: item for item in request.snapshot.placements}
            horizons = dict(request.horizons)
            model = gp.Model("exact_four_state_retime")
            model.Params.OutputFlag = 0
            model.Params.Threads = config.threads
            model.Params.Seed = config.seed
            model.Params.MIPFocus = 1
            model.Params.SoftMemLimit = 12
            a: dict[int, Any] = {}
            e: dict[int, Any] = {}
            tardy: dict[int, Any] = {}
            for block_id in sorted(request.modeled_ids):
                if remaining() <= 0.001:
                    model_construction += time.monotonic() - build_started
                    return BackendResult(
                        status="TIME_LIMIT",
                        runtime=time.monotonic() - started,
                        diagnostics=("deadline during model variables", "solution_count=0"),
                        phase_times=measured_phases(),
                    )
                placement = by_id[block_id]
                block = instance.block(block_id)
                horizon = horizons[placement.bay_id]
                fixed = block_id not in request.free_ids
                a[block_id] = model.addVar(
                    vtype=gp.GRB.INTEGER,
                    lb=placement.entry if fixed else 0,
                    ub=placement.entry if fixed else horizon,
                    name=f"a_{block_id}",
                )
                e[block_id] = model.addVar(
                    vtype=gp.GRB.INTEGER,
                    lb=placement.exit if fixed else 0,
                    ub=placement.exit if fixed else horizon,
                    name=f"e_{block_id}",
                )
                # Due dates may be negative, so tardiness is not bounded by the
                # scheduling horizon.  The defining lower bounds are sufficient.
                tardy[block_id] = model.addVar(
                    vtype=gp.GRB.INTEGER, lb=0, name=f"T_{block_id}"
                )
                model.addConstr(a[block_id] >= block.release_time)
                model.addConstr(e[block_id] >= a[block_id] + block.dwell)
                model.addConstr(tardy[block_id] >= e[block_id] - block.due_date)
                a[block_id].Start = placement.entry
                e[block_id].Start = placement.exit
                tardy[block_id].Start = max(0, placement.exit - block.due_date)

            mode_counts: dict[str, int] = defaultdict(int)
            for pair in request.pairs:
                if remaining() <= 0.001:
                    model_construction += time.monotonic() - build_started
                    return BackendResult(
                        status="TIME_LIMIT",
                        runtime=time.monotonic() - started,
                        variables=model.NumVars,
                        constraints=model.NumConstrs + model.NumGenConstrs,
                        diagnostics=("deadline during model relations", "solution_count=0"),
                        phase_times=measured_phases(),
                    )
                modes = relation_modes(pair.relation)
                selectors = {
                    mode: model.addVar(vtype=gp.GRB.BINARY, name=f"m_{pair.i}_{pair.k}_{mode.value}")
                    for mode in modes
                }
                model.addConstr(gp.quicksum(selectors.values()) == 1)
                for mode, selector in selectors.items():
                    selector.Start = int(mode is pair.warm_mode)
                    mode_counts[mode.value] += 1
                    if mode is TemporalMode.I_BEFORE:
                        model.addGenConstrIndicator(selector, 1, e[pair.i] <= a[pair.k])
                    elif mode is TemporalMode.K_BEFORE:
                        model.addGenConstrIndicator(selector, 1, e[pair.k] <= a[pair.i])
                    elif mode is TemporalMode.K_NESTED:
                        model.addGenConstrIndicator(selector, 1, a[pair.i] + 1 <= a[pair.k])
                        model.addGenConstrIndicator(selector, 1, e[pair.k] <= e[pair.i])
                    elif mode is TemporalMode.I_NESTED:
                        model.addGenConstrIndicator(selector, 1, a[pair.k] + 1 <= a[pair.i])
                        model.addGenConstrIndicator(selector, 1, e[pair.i] <= e[pair.k])

            primary_expr = gp.quicksum(tardy.values())
            model.setObjective(primary_expr, gp.GRB.MINIMIZE)
            model_construction += time.monotonic() - build_started
            first_limit = remaining()
            if first_limit <= 0.001:
                return BackendResult(
                    status="TIME_LIMIT",
                    runtime=time.monotonic() - started,
                    variables=model.NumVars,
                    constraints=model.NumConstrs + model.NumGenConstrs,
                    diagnostics=("deadline before primary solve", "solution_count=0"),
                    phase_times=measured_phases(),
                )
            model.Params.TimeLimit = max(0.001, first_limit)
            optimize_started = time.monotonic()
            model.optimize()
            optimize += time.monotonic() - optimize_started
            first_status = _status_name(gp, model.Status)
            if model.SolCount <= 0:
                return BackendResult(
                    status=first_status,
                    runtime=time.monotonic() - started,
                    variables=model.NumVars,
                    constraints=model.NumConstrs + model.NumGenConstrs,
                    diagnostics=("solution_count=0",),
                    phase_times=measured_phases(),
                )
            extraction_started = time.monotonic()
            primary = float(sum(round(tardy[item].X) for item in tardy))
            bound = float(model.ObjBound)
            gap = float(model.MIPGap) if math.isfinite(float(model.MIPGap)) else None
            best_dates = tuple(
                (block_id, int(round(a[block_id].X)), int(round(e[block_id].X)))
                for block_id in sorted(request.modeled_ids)
            )
            secondary = float(
                sum(
                    round(e[block_id].X)
                    - round(a[block_id].X)
                    - instance.block(block_id).dwell
                    for block_id in request.free_ids
                )
            )
            extraction += time.monotonic() - extraction_started

            second_limit = remaining()
            if second_limit > 0.001:
                build_started = time.monotonic()
                best_primary = int(round(primary))
                model.addConstr(primary_expr == best_primary, name="fix_primary")
                secondary_expr = gp.quicksum(
                    e[block_id] - a[block_id] - instance.block(block_id).dwell
                    for block_id in request.free_ids
                )
                model.setObjective(secondary_expr, gp.GRB.MINIMIZE)
                model.Params.TimeLimit = max(0.001, second_limit)
                model_construction += time.monotonic() - build_started
                optimize_started = time.monotonic()
                model.optimize()
                optimize += time.monotonic() - optimize_started
                if model.SolCount > 0:
                    extraction_started = time.monotonic()
                    best_dates = tuple(
                        (block_id, int(round(a[block_id].X)), int(round(e[block_id].X)))
                        for block_id in sorted(request.modeled_ids)
                    )
                    secondary = float(model.ObjVal)
                    extraction += time.monotonic() - extraction_started

            diagnostics = (
                f"free={len(request.free_ids)}",
                f"modeled={len(request.modeled_ids)}",
                f"pairs={len(request.pairs)}",
                f"modes={dict(sorted(mode_counts.items()))}",
                f"secondary={secondary}",
            )
            return BackendResult(
                status=first_status,
                dates=best_dates,
                primary=primary,
                bound=bound,
                gap=gap,
                secondary=secondary,
                runtime=time.monotonic() - started,
                variables=model.NumVars,
                constraints=model.NumConstrs + model.NumGenConstrs,
                diagnostics=diagnostics,
                phase_times=measured_phases(),
            )
        except Exception as exc:
            return BackendResult(
                status="ERROR",
                runtime=time.monotonic() - started,
                diagnostics=(f"{type(exc).__name__}: {exc}",),
                phase_times=measured_phases(),
            )


def _default_backend_factory() -> Any:
    return _GurobiBackend()


def _thaw(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {key: _thaw(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [_thaw(item) for item in value]
    return copy.deepcopy(value)


def _rollback(
    snapshot: SolutionSnapshot,
    status: str,
    started: float,
    diagnostics: tuple[str, ...],
    backend: BackendResult | None = None,
    telemetry: Mapping[str, Any] | None = None,
) -> RetimingResult:
    return RetimingResult(
        snapshot=snapshot,
        status=status,
        primary=backend.primary if backend else None,
        bound=backend.bound if backend else None,
        gap=backend.gap if backend else None,
        runtime=time.monotonic() - started,
        changed_ids=frozenset(),
        diagnostics=diagnostics + (() if backend is None else backend.diagnostics),
        telemetry=telemetry,
    )


def _candidate_from_backend(
    snapshot: SolutionSnapshot,
    request: RetimingRequest,
    solved: BackendResult,
    instance: Instance,
) -> tuple[SolutionSnapshot | None, str, tuple[str, ...]]:
    dates = {block_id: (entry, exit_time) for block_id, entry, exit_time in solved.dates}
    if not request.modeled_ids <= set(dates):
        return None, "INVALID_SOLUTION", ("missing dates",)
    horizons = dict(request.horizons)
    by_id = {item.block_id: item for item in snapshot.placements}
    replacements: dict[int, Placement] = {}
    for block_id in request.modeled_ids:
        old = by_id[block_id]
        entry, exit_time = dates[block_id]
        if any(isinstance(value, bool) or not isinstance(value, int) for value in (entry, exit_time)):
            return None, "INVALID_SOLUTION", ("non-integer date",)
        if block_id not in request.free_ids:
            if (entry, exit_time) != (old.entry, old.exit):
                return None, "INVALID_SOLUTION", ("boundary date changed",)
            continue
        block = instance.block(block_id)
        if (
            entry < 0
            or entry < block.release_time
            or exit_time < entry + block.dwell
            or exit_time > horizons[old.bay_id]
        ):
            return None, "INVALID_SOLUTION", ("date bound",)
        replacements[block_id] = replace(old, entry=entry, exit=exit_time)

    candidate = SolutionSnapshot(
        tuple(replacements.get(item.block_id, item) for item in snapshot.placements)
    )
    candidate_by_id = {item.block_id: item for item in candidate.placements}
    for pair in request.pairs:
        if not pair.relation.allows(candidate_by_id[pair.i], candidate_by_id[pair.k]):
            return None, "INVALID_RELATION", ()

    before_objective = snapshot.objective or compute_objective(instance, snapshot)
    after_objective = compute_objective(instance, candidate)
    if after_objective.z1 > before_objective.z1 + 1e-9:
        return None, "WORSE_Z1", ()
    secondary_before = sum(
        by_id[block_id].exit
        - by_id[block_id].entry
        - instance.block(block_id).dwell
        for block_id in request.free_ids
    )
    secondary_after = sum(
        candidate_by_id[block_id].exit
        - candidate_by_id[block_id].entry
        - instance.block(block_id).dwell
        for block_id in request.free_ids
    )
    diagnostics = (
        f"secondary_before={secondary_before}",
        f"secondary_after={secondary_after}",
    )
    if (
        math.isclose(after_objective.z1, before_objective.z1, rel_tol=0.0, abs_tol=1e-9)
        and secondary_after >= secondary_before
    ):
        return None, "NO_IMPROVEMENT", diagnostics
    return candidate.with_objective(after_objective), solved.status, diagnostics


def _already_at_exact_lexicographic_lower_bound(
    request: RetimingRequest,
    instance: Instance,
) -> bool:
    """Prove that neither primary tardiness nor secondary dwell can improve."""
    by_id = {item.block_id: item for item in request.snapshot.placements}
    for block_id in request.free_ids:
        placement = by_id[block_id]
        block = instance.block(block_id)
        if placement.exit - placement.entry != block.dwell:
            return False
        current_tardiness = max(0, placement.exit - block.due_date)
        independent_lower_bound = max(
            0, block.release_time + block.dwell - block.due_date
        )
        if current_tardiness != independent_lower_bound:
            return False
    return True


def retime(
    snapshot: SolutionSnapshot,
    instance: Instance,
    kernel: GeometryKernel,
    budget: Budget,
    affected_ids: frozenset[int] | set[int] | None = None,
    backend_factory: Callable[[], Any] = _default_backend_factory,
    config: RetimingConfig | None = None,
) -> RetimingResult:
    """Return a verified fixed-layout candidate or the exact input snapshot."""
    started = time.monotonic()
    phase_times = {
        "affected_component_discovery": 0.0,
        "relation_pair_preprocessing": 0.0,
        "request_construction": 0.0,
        "model_construction": 0.0,
        "optimize": 0.0,
        "extraction": 0.0,
        "local_feasibility": 0.0,
        "serialization": 0.0,
        "checker": 0.0,
    }

    def telemetry(
        *,
        status: str,
        requests: tuple[RetimingRequest, ...] = (),
        solved_results: list[BackendResult] | None = None,
        changed_ids: set[int] | frozenset[int] = frozenset(),
        z1_before: float | None = None,
        z1_after: float | None = None,
    ) -> dict[str, Any]:
        solved = solved_results or []
        return {
            "status": status,
            "phase_times": dict(phase_times),
            "requested_components": len(requests),
            "solved_components": sum(bool(item.dates) for item in solved),
            "exact_skipped_components": sum(
                item.status == "EXACT_Z1_SKIP" for item in solved
            ),
            "improved_components": sum(
                item.status in {"OPTIMAL", "TIME_LIMIT", "SUBOPTIMAL"} and bool(item.dates)
                for item in solved
            ),
            "component_sizes": [len(item.modeled_ids) for item in requests],
            "free_sizes": [len(item.free_ids) for item in requests],
            "pair_counts": [len(item.pairs) for item in requests],
            "changed_ids": len(changed_ids),
            "z1_before": z1_before,
            "z1_after": z1_after,
            "z1_improvement": (
                None
                if z1_before is None or z1_after is None
                else float(z1_before) - float(z1_after)
            ),
        }

    config = config or RetimingConfig()
    try:
        if len(snapshot.placements) != len(instance.blocks):
            return _rollback(snapshot, "INVALID_INPUT", started, ("incomplete snapshot",))
        if math.isclose(instance.weights.w1, 0.0, rel_tol=0.0, abs_tol=0.0):
            return _rollback(
                snapshot,
                "W1_ZERO",
                started,
                ("retiming cannot strictly improve the checker objective",),
            )
        affected = frozenset(affected_ids) if affected_ids is not None else None
        remaining = budget.search_remaining()
        total_cap = min(float(config.time_cap_s), 0.05 * remaining)
        if total_cap <= 0.001:
            return _rollback(snapshot, "BUDGET", started, (f"remaining={remaining}",))
        child = budget.child(total_cap)
        try:
            requests = build_requests(
                snapshot,
                instance,
                kernel,
                config,
                affected,
                child,
                phase_times=phase_times,
            )
        except _RetimingDeadline as exc:
            return _rollback(
                snapshot,
                "BUDGET",
                started,
                (str(exc),),
                telemetry=telemetry(status="BUDGET"),
            )
        if not requests:
            return _rollback(snapshot, "EMPTY", started, ("no affected blocks",))
        if any(len(request.free_ids) > config.max_free for request in requests):
            return _rollback(snapshot, "INVALID_REQUEST", started, ("free cap exceeded",))

        backend = backend_factory()
        working = snapshot
        solved_results: list[BackendResult] = []
        request_diagnostics: list[str] = [
            f"requests={len(requests)}",
            f"components_max_free={max(request.max_component_free for request in requests)}",
        ]
        accepted_statuses: list[str] = []
        changed_ids: set[int] = set()
        last_rejection = "NO_IMPROVEMENT"
        for request_index, request in enumerate(requests):
            solve_cap = child.search_remaining()
            if solve_cap <= 0.001:
                last_rejection = "BUDGET"
                break
            if config.exact_z1_skip and _already_at_exact_lexicographic_lower_bound(
                request, instance
            ):
                solved = BackendResult(
                    status="EXACT_Z1_SKIP",
                    diagnostics=(
                        "primary_at_independent_lower_bound",
                        "secondary_dwell_extension=0",
                    ),
                )
                solved_results.append(solved)
                last_rejection = solved.status
                request_diagnostics.extend(
                    (
                        f"request[{request_index}].free={len(request.free_ids)}",
                        f"request[{request_index}].modeled={len(request.modeled_ids)}",
                        f"request[{request_index}].pairs={len(request.pairs)}",
                        f"request[{request_index}].status={solved.status}",
                    )
                )
                continue
            try:
                solved = backend.solve(request, instance, solve_cap, config)
            except Exception as exc:
                solved = BackendResult(
                    status="ERROR", diagnostics=(f"{type(exc).__name__}: {exc}",)
                )
            solved_results.append(solved)
            for name, duration in solved.phase_times:
                if name in phase_times:
                    phase_times[name] += duration
            request_diagnostics.extend(
                (
                    f"request[{request_index}].free={len(request.free_ids)}",
                    f"request[{request_index}].modeled={len(request.modeled_ids)}",
                    f"request[{request_index}].pairs={len(request.pairs)}",
                    f"request[{request_index}].status={solved.status}",
                    f"pairs={len(request.pairs)}",
                )
            )
            request_diagnostics.extend(
                f"request[{request_index}].{item}" for item in solved.diagnostics
            )
            request_diagnostics.extend(solved.diagnostics)
            if not solved.dates or solved.status in {
                "ERROR",
                "INFEASIBLE",
                "INF_OR_UNBD",
                "UNBOUNDED",
                "NUMERIC",
                "INTERRUPTED",
            }:
                last_rejection = solved.status
                continue
            phase_started = time.monotonic()
            candidate, candidate_status, candidate_diag = _candidate_from_backend(
                working, request, solved, instance
            )
            phase_times["local_feasibility"] += time.monotonic() - phase_started
            request_diagnostics.extend(
                f"request[{request_index}].{item}" for item in candidate_diag
            )
            if candidate is None:
                last_rejection = candidate_status
                continue
            before_by_id = {item.block_id: item for item in working.placements}
            after_by_id = {item.block_id: item for item in candidate.placements}
            changed_ids.update(
                block_id
                for block_id in request.free_ids
                if before_by_id[block_id] != after_by_id[block_id]
            )
            working = candidate
            accepted_statuses.append(candidate_status)

        aggregate = BackendResult(
            status=(accepted_statuses[-1] if accepted_statuses else last_rejection),
            primary=(
                float(sum(item.primary for item in solved_results if item.primary is not None))
                if solved_results and all(item.primary is not None for item in solved_results)
                else None
            ),
            bound=(
                float(sum(item.bound for item in solved_results if item.bound is not None))
                if solved_results and all(item.bound is not None for item in solved_results)
                else None
            ),
            gap=(
                max(item.gap for item in solved_results if item.gap is not None)
                if any(item.gap is not None for item in solved_results)
                else None
            ),
            variables=sum(item.variables for item in solved_results),
            constraints=sum(item.constraints for item in solved_results),
        )
        if working is snapshot or not changed_ids:
            return _rollback(
                snapshot,
                last_rejection,
                started,
                tuple(request_diagnostics),
                aggregate,
                telemetry=telemetry(
                    status=last_rejection,
                    requests=requests,
                    solved_results=solved_results,
                ),
            )

        phase_started = time.monotonic()
        operations = serialize(working, kernel)
        phase_times["serialization"] += time.monotonic() - phase_started
        candidate_objective = working.objective or compute_objective(instance, working)
        input_objective = snapshot.objective or compute_objective(instance, snapshot)

        try:
            from utils import check_feasibility
        except ImportError:
            from baseline.utils import check_feasibility
        check_started = time.monotonic()
        checked = check_feasibility(_thaw(instance.raw), copy.deepcopy(operations))
        checker_duration = time.monotonic() - check_started
        phase_times["checker"] += checker_duration
        budget.record_checker_duration(checker_duration)
        if checked.get("feasible") is not True or checked.get("stage") != 5:
            return _rollback(
                snapshot,
                "CHECKER_REJECTED",
                started,
                tuple(request_diagnostics) + (f"stage={checked.get('stage')}",),
                aggregate,
                telemetry=telemetry(
                    status="CHECKER_REJECTED",
                    requests=requests,
                    solved_results=solved_results,
                    changed_ids=changed_ids,
                    z1_before=input_objective.z1,
                    z1_after=candidate_objective.z1,
                ),
            )
        external = (
            checked.get("obj1"),
            checked.get("obj2"),
            checked.get("obj3"),
            checked.get("objective"),
        )
        internal = (
            candidate_objective.z1,
            candidate_objective.z2,
            candidate_objective.z3,
            candidate_objective.total,
        )
        if not all(
            isinstance(right, (int, float))
            and not isinstance(right, bool)
            and math.isclose(float(left), float(right), rel_tol=1e-6, abs_tol=1e-9)
            for left, right in zip(internal, external)
        ):
            return _rollback(
                snapshot,
                "OBJECTIVE_MISMATCH",
                started,
                tuple(request_diagnostics),
                aggregate,
                telemetry=telemetry(
                    status="OBJECTIVE_MISMATCH",
                    requests=requests,
                    solved_results=solved_results,
                    changed_ids=changed_ids,
                    z1_before=input_objective.z1,
                    z1_after=candidate_objective.z1,
                ),
            )
        candidate = working.with_objective(candidate_objective)
        final_status = accepted_statuses[-1]
        if len(accepted_statuses) < len(requests):
            final_status = "PARTIAL"
        return RetimingResult(
            snapshot=candidate,
            status=final_status,
            primary=aggregate.primary,
            bound=aggregate.bound,
            gap=aggregate.gap,
            runtime=time.monotonic() - started,
            changed_ids=frozenset(changed_ids),
            diagnostics=tuple(request_diagnostics)
            + (
                f"z1_before={input_objective.z1}",
                f"z1_after={candidate_objective.z1}",
                f"total_before={input_objective.total}",
                f"total_after={candidate_objective.total}",
                "checker_stage=5",
            ),
            operations=copy.deepcopy(operations),
            checker_result=copy.deepcopy(checked),
            telemetry=telemetry(
                status=final_status,
                requests=requests,
                solved_results=solved_results,
                changed_ids=changed_ids,
                z1_before=input_objective.z1,
                z1_after=candidate_objective.z1,
            ),
        )
    except Exception as exc:
        return _rollback(snapshot, "ERROR", started, (f"{type(exc).__name__}: {exc}",))


__all__ = [
    "BackendResult",
    "RetimingConfig",
    "RetimingPair",
    "RetimingRequest",
    "RetimingResult",
    "bay_horizon",
    "build_request",
    "build_requests",
    "mode_allows",
    "nonfree_components",
    "relation_modes",
    "retime",
]
