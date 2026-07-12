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

    def __post_init__(self) -> None:
        if isinstance(self.max_free, bool) or not isinstance(self.max_free, int):
            raise TypeError("max_free must be an integer")
        if isinstance(self.threads, bool) or not isinstance(self.threads, int):
            raise TypeError("threads must be an integer")
        if isinstance(self.seed, bool) or not isinstance(self.seed, int):
            raise TypeError("seed must be an integer")
        if self.max_free <= 0 or self.threads <= 0:
            raise ValueError("max_free and threads must be positive")
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
) -> tuple[frozenset[int], ...]:
    placements = sorted(
        (item for item in snapshot.placements if item.bay_id == bay_id),
        key=lambda item: item.block_id,
    )
    adjacency = {item.block_id: set() for item in placements}
    for left, right in combinations(placements, 2):
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


def build_request(
    snapshot: SolutionSnapshot,
    instance: Instance,
    kernel: GeometryKernel,
    config: RetimingConfig,
    affected_ids: frozenset[int] | None = None,
) -> RetimingRequest:
    by_id = {item.block_id: item for item in snapshot.placements}
    if affected_ids is not None and not affected_ids <= set(by_id):
        raise ValueError("affected_ids contains an unknown block")
    chosen: list[tuple[frozenset[int], frozenset[int]]] = []
    for bay in instance.bays:
        for component in nonfree_components(snapshot, kernel, bay.index):
            if affected_ids is not None and component.isdisjoint(affected_ids):
                continue
            chosen.append(
                (
                    component,
                    _critical_ids(component, snapshot, instance, config.max_free, affected_ids),
                )
            )
    modeled_ids = frozenset().union(*(item[0] for item in chosen)) if chosen else frozenset()
    free_ids = frozenset().union(*(item[1] for item in chosen)) if chosen else frozenset()
    horizons = tuple(
        (bay.index, bay_horizon(instance, snapshot, bay.index))
        for bay in instance.bays
        if any(by_id[block_id].bay_id == bay.index for block_id in modeled_ids)
    )
    pairs: list[RetimingPair] = []
    for component, selected in chosen:
        for i, k in combinations(sorted(component), 2):
            if i not in selected and k not in selected:
                continue
            relation = kernel.relation(by_id[i], by_id[k])
            if relation.state is PairState.FREE:
                continue
            warm_mode = relation.mode(by_id[i], by_id[k])
            if warm_mode is TemporalMode.INVALID:
                raise ValueError("input snapshot violates a fixed-layout relation")
            pairs.append(RetimingPair(i, k, relation, warm_mode))
    return RetimingRequest(
        snapshot=snapshot,
        free_ids=free_ids,
        modeled_ids=modeled_ids,
        horizons=horizons,
        pairs=tuple(pairs),
        max_component_free=max((len(item[1]) for item in chosen), default=0),
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
        try:
            import gurobipy as gp

            by_id = {item.block_id: item for item in request.snapshot.placements}
            horizons = dict(request.horizons)
            model = gp.Model("exact_four_state_retime")
            model.Params.OutputFlag = 0
            model.Params.Threads = config.threads
            model.Params.Seed = config.seed
            model.Params.MIPFocus = 1
            model.Params.SoftMemLimit = 12
            model.Params.TimeLimit = max(0.001, float(time_limit))
            a: dict[int, Any] = {}
            e: dict[int, Any] = {}
            tardy: dict[int, Any] = {}
            for block_id in sorted(request.modeled_ids):
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
                tardy[block_id] = model.addVar(
                    vtype=gp.GRB.INTEGER, lb=0, ub=horizon, name=f"T_{block_id}"
                )
                model.addConstr(a[block_id] >= block.release_time)
                model.addConstr(e[block_id] >= a[block_id] + block.dwell)
                model.addConstr(tardy[block_id] >= e[block_id] - block.due_date)
                a[block_id].Start = placement.entry
                e[block_id].Start = placement.exit
                tardy[block_id].Start = max(0, placement.exit - block.due_date)

            mode_counts: dict[str, int] = defaultdict(int)
            for pair in request.pairs:
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
            model.optimize()
            first_status = _status_name(gp, model.Status)
            if model.SolCount <= 0:
                return BackendResult(
                    status=first_status,
                    runtime=time.monotonic() - started,
                    variables=model.NumVars,
                    constraints=model.NumConstrs + model.NumGenConstrs,
                    diagnostics=("solution_count=0",),
                )
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

            elapsed = time.monotonic() - started
            remaining = max(0.0, float(time_limit) - elapsed)
            if remaining > 0.001:
                best_primary = int(round(primary))
                model.addConstr(primary_expr == best_primary, name="fix_primary")
                secondary_expr = gp.quicksum(
                    e[block_id] - a[block_id] - instance.block(block_id).dwell
                    for block_id in request.free_ids
                )
                model.setObjective(secondary_expr, gp.GRB.MINIMIZE)
                model.Params.TimeLimit = max(0.001, remaining)
                model.optimize()
                if model.SolCount > 0:
                    best_dates = tuple(
                        (block_id, int(round(a[block_id].X)), int(round(e[block_id].X)))
                        for block_id in sorted(request.modeled_ids)
                    )
                    secondary = float(model.ObjVal)

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
            )
        except Exception as exc:
            return BackendResult(
                status="ERROR",
                runtime=time.monotonic() - started,
                diagnostics=(f"{type(exc).__name__}: {exc}",),
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
    )


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
        request = build_request(snapshot, instance, kernel, config, affected)
        if not request.free_ids:
            return _rollback(snapshot, "EMPTY", started, ("no affected blocks",))
        remaining = budget.search_remaining()
        cap = min(float(config.time_cap_s), 0.05 * remaining)
        if cap <= 0.001:
            return _rollback(snapshot, "BUDGET", started, (f"remaining={remaining}",))
        backend = backend_factory()
        solved = backend.solve(request, instance, cap, config)
        base_diag = (
            f"components_max_free={request.max_component_free}",
            f"horizons={dict(request.horizons)}",
            f"variables={solved.variables}",
            f"constraints={solved.constraints}",
        )
        if not solved.dates or solved.status in {
            "ERROR",
            "INFEASIBLE",
            "INF_OR_UNBD",
            "UNBOUNDED",
            "NUMERIC",
            "INTERRUPTED",
        }:
            return _rollback(snapshot, solved.status, started, base_diag, solved)
        dates = {block_id: (entry, exit_time) for block_id, entry, exit_time in solved.dates}
        if not request.modeled_ids <= set(dates):
            return _rollback(snapshot, "INVALID_SOLUTION", started, base_diag + ("missing dates",), solved)
        horizon_by_bay = dict(request.horizons)
        replacements: dict[int, Placement] = {}
        by_id = {item.block_id: item for item in snapshot.placements}
        for block_id in request.free_ids:
            old = by_id[block_id]
            entry, exit_time = dates[block_id]
            if any(isinstance(value, bool) or not isinstance(value, int) for value in (entry, exit_time)):
                return _rollback(snapshot, "INVALID_SOLUTION", started, base_diag + ("non-integer date",), solved)
            block = instance.block(block_id)
            if (
                entry < 0
                or entry < block.release_time
                or exit_time < entry + block.dwell
                or exit_time > horizon_by_bay[old.bay_id]
            ):
                return _rollback(snapshot, "INVALID_SOLUTION", started, base_diag + ("date bound",), solved)
            replacements[block_id] = replace(old, entry=entry, exit=exit_time)
        candidate = SolutionSnapshot(
            tuple(replacements.get(item.block_id, item) for item in snapshot.placements)
        )
        candidate_by_id = {item.block_id: item for item in candidate.placements}
        for pair in request.pairs:
            if not pair.relation.allows(candidate_by_id[pair.i], candidate_by_id[pair.k]):
                return _rollback(snapshot, "INVALID_RELATION", started, base_diag, solved)
        operations = serialize(candidate, kernel)
        candidate_objective = compute_objective(instance, candidate)
        input_objective = snapshot.objective or compute_objective(instance, snapshot)
        if candidate_objective.z1 > input_objective.z1 + 1e-9:
            return _rollback(snapshot, "WORSE_Z1", started, base_diag, solved)
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
        if (
            math.isclose(candidate_objective.z1, input_objective.z1, rel_tol=0.0, abs_tol=1e-9)
            and secondary_after >= secondary_before
        ):
            return _rollback(
                snapshot,
                "NO_IMPROVEMENT",
                started,
                base_diag
                + (
                    f"secondary_before={secondary_before}",
                    f"secondary_after={secondary_after}",
                ),
                solved,
            )

        try:
            from utils import check_feasibility
        except ImportError:
            from baseline.utils import check_feasibility
        check_started = time.monotonic()
        checked = check_feasibility(_thaw(instance.raw), copy.deepcopy(operations))
        budget.record_checker_duration(time.monotonic() - check_started)
        if checked.get("feasible") is not True or checked.get("stage") != 5:
            return _rollback(
                snapshot,
                "CHECKER_REJECTED",
                started,
                base_diag + (f"stage={checked.get('stage')}",),
                solved,
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
            return _rollback(snapshot, "OBJECTIVE_MISMATCH", started, base_diag, solved)
        changed = frozenset(
            block_id
            for block_id in request.free_ids
            if replacements[block_id] != by_id[block_id]
        )
        if not changed:
            return _rollback(snapshot, "NO_CHANGE", started, base_diag, solved)
        candidate = candidate.with_objective(candidate_objective)
        return RetimingResult(
            snapshot=candidate,
            status=solved.status,
            primary=solved.primary,
            bound=solved.bound,
            gap=solved.gap,
            runtime=time.monotonic() - started,
            changed_ids=changed,
            diagnostics=base_diag
            + (
                f"z1_before={input_objective.z1}",
                f"z1_after={candidate_objective.z1}",
                f"total_before={input_objective.total}",
                f"total_after={candidate_objective.total}",
                "checker_stage=5",
            )
            + solved.diagnostics,
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
    "mode_allows",
    "nonfree_components",
    "relation_modes",
    "retime",
]
