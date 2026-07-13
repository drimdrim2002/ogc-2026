"""Bounded, feature-gated one-way interlock densification.

Approximate geometry is used only to rank and discard impossible offsets.  A
candidate is exposed only after the checker-parity geometry kernel, the exact
retimer, canonical serialization, and the full checker all agree.
"""

from __future__ import annotations

import copy
import math
import time
from collections import Counter
from collections.abc import Callable, Iterator, Mapping
from dataclasses import dataclass, replace
from itertools import product
from typing import Any

from .budget import Budget
from .geometry import GeometryKernel, PairRelation, PairState, TemporalMode
from .instance import Instance
from .serialize import serialize
from .state import IncumbentStore, Placement, SolutionSnapshot, compute_objective


@dataclass(frozen=True, slots=True)
class InterlockGate:
    enabled: bool
    tardiness: float
    pressure: float
    stalled: bool
    remaining: float
    reserve: float


@dataclass(frozen=True, slots=True)
class GateDecision:
    run: bool
    reason: str


@dataclass(frozen=True, slots=True)
class SaturatedWindow:
    bay_id: int
    start: int
    end: int
    pressure: float
    active_ids: tuple[int, ...]


@dataclass(frozen=True, slots=True)
class InterlockCandidate:
    mover: int
    host: int
    placement: Placement
    relation: PairRelation
    window: SaturatedWindow
    score_hint: float


@dataclass(frozen=True, slots=True)
class InterlockConfig:
    enabled: bool = False
    pressure_threshold: float = 0.45
    max_budget_fraction: float = 0.08
    max_windows: int = 4
    max_movers: int = 8
    max_hosts_per_mover: int = 8
    max_offsets: int = 64
    checker_margin: float = 0.01

    def __post_init__(self) -> None:
        integer_values = (
            self.max_windows,
            self.max_movers,
            self.max_hosts_per_mover,
            self.max_offsets,
        )
        if any(isinstance(value, bool) or not isinstance(value, int) for value in integer_values):
            raise TypeError("interlock caps must be integers")
        if any(value <= 0 for value in integer_values):
            raise ValueError("interlock caps must be positive")
        if not 0.0 <= float(self.pressure_threshold):
            raise ValueError("pressure threshold must be nonnegative")
        if not 0.0 < float(self.max_budget_fraction) <= 0.08:
            raise ValueError("interlock budget fraction must be in (0, 0.08]")
        if self.checker_margin < 0.0:
            raise ValueError("checker margin must be nonnegative")


@dataclass(frozen=True, slots=True)
class InterlockContext:
    instance: Instance
    kernel: GeometryKernel
    raw: Mapping[str, Any]
    checker: Callable[[dict, dict], dict]
    stalled: bool
    config: InterlockConfig = InterlockConfig()
    clock: Callable[[], float] = time.monotonic


@dataclass(frozen=True, slots=True)
class DensifyMetrics:
    gate_reason: str
    pressure: float
    budget_cap_s: float
    candidates: int
    retime_attempts: int
    checker_attempts: int
    feasible_improvements: int
    layout_improvements: int
    interlock_improvements: int
    installed: int
    geometry_time_s: float
    retime_time_s: float
    checker_time_s: float
    selected_modes: tuple[str, ...] = ()
    rollback_reasons: tuple[tuple[str, int], ...] = ()


@dataclass(frozen=True, slots=True)
class DensifyResult:
    snapshot: SolutionSnapshot
    serialized_operations: Mapping[str, Any]
    metrics: DensifyMetrics


def decide_interlock_gate(
    gate: InterlockGate,
    *,
    pressure_threshold: float = 0.45,
) -> GateDecision:
    """Pure activation predicate with stable first-failure reasons."""
    if not gate.enabled:
        return GateDecision(False, "FEATURE_DISABLED")
    if gate.tardiness <= 0.0:
        return GateDecision(False, "NO_TARDINESS")
    if gate.pressure < pressure_threshold:
        return GateDecision(False, "LOW_PRESSURE")
    if not gate.stalled:
        return GateDecision(False, "NOT_STALLED")
    if gate.remaining <= gate.reserve:
        return GateDecision(False, "DEADLINE_RESERVE")
    return GateDecision(True, "RUN")


def densifier_budget_cap(
    total_limit: float,
    remaining: float,
    reserve: float,
    *,
    fraction: float = 0.08,
) -> float:
    """Return the hard P6 cap without creating or advancing a clock."""
    return max(0.0, min(float(fraction) * max(0.0, total_limit), remaining - reserve))


def saturated_windows(
    snapshot: SolutionSnapshot,
    instance: Instance,
    kernel: GeometryKernel,
) -> tuple[SaturatedWindow, ...]:
    """Compute exact union-area energy pressure for each bay/event window."""
    windows: list[SaturatedWindow] = []
    for bay in instance.bays:
        placements = tuple(item for item in snapshot.placements if item.bay_id == bay.index)
        events = sorted({date for item in placements for date in (item.entry, item.exit)})
        for start, end in zip(events, events[1:]):
            if end <= start:
                continue
            active = tuple(
                sorted(
                    item.block_id
                    for item in placements
                    if item.entry < end and start < item.exit
                )
            )
            if not active:
                continue
            energy = sum(
                float(kernel.shape(block_id, next(
                    item.orient_idx for item in placements if item.block_id == block_id
                )).union.area)
                * (end - start)
                for block_id in active
            )
            capacity = bay.area * (end - start)
            windows.append(
                SaturatedWindow(
                    bay.index,
                    start,
                    end,
                    energy / max(1.0, capacity),
                    active,
                )
            )
    return tuple(
        sorted(
            windows,
            key=lambda item: (-item.pressure, item.start, item.bay_id, item.active_ids),
        )
    )


def union_energy_pressure(
    snapshot: SolutionSnapshot,
    instance: Instance,
    kernel: GeometryKernel,
) -> float:
    windows = saturated_windows(snapshot, instance, kernel)
    return max((item.pressure for item in windows), default=0.0)


def rank_hosts(
    snapshot: SolutionSnapshot,
    mover_id: int,
    host_ids: tuple[int, ...],
    context: InterlockContext,
) -> tuple[Placement, ...]:
    """Prefer hosts with nonnegative dwell-extension slack, then more slack."""
    by_id = {item.block_id: item for item in snapshot.placements}
    unique = [by_id[item] for item in sorted(set(host_ids)) if item in by_id and item != mover_id]
    return tuple(
        sorted(
            unique,
            key=lambda item: (
                context.instance.block(item.block_id).due_date - item.exit < 0,
                -(context.instance.block(item.block_id).due_date - item.exit),
                item.block_id,
            ),
        )
    )


def _world_aabb(placement: Placement, kernel: GeometryKernel) -> tuple[float, float, float, float]:
    shape = kernel.shape(placement.block_id, placement.orient_idx)
    xmin, ymin, xmax, ymax = shape.union_aabb or shape.full_aabb
    return xmin + placement.x, ymin + placement.y, xmax + placement.x, ymax + placement.y


def _aabb_overlaps(left: Placement, right: Placement, kernel: GeometryKernel) -> bool:
    lx0, ly0, lx1, ly1 = _world_aabb(left, kernel)
    rx0, ry0, rx1, ry1 = _world_aabb(right, kernel)
    return lx0 < rx1 and rx0 < lx1 and ly0 < ry1 and ry0 < ly1


def _rounded(values: tuple[float, ...]) -> tuple[int, ...]:
    output: set[int] = set()
    for value in values:
        output.add(math.floor(value))
        output.add(math.ceil(value))
    return tuple(sorted(output))


def _offsets(
    mover: Placement,
    host: Placement,
    context: InterlockContext,
    config: InterlockConfig,
) -> tuple[tuple[int, int], ...]:
    mover_shape = context.kernel.shape(mover.block_id, mover.orient_idx)
    host_shape = context.kernel.shape(host.block_id, host.orient_idx)
    fit = context.instance.block(mover.block_id).orientations[mover.orient_idx].integer_range(
        context.instance.bay(host.bay_id)
    )
    if fit is None:
        return ()
    mx0, my0, mx1, my1 = mover_shape.union_aabb or mover_shape.full_aabb
    hx0, hy0, hx1, hy1 = host_shape.union_aabb or host_shape.full_aabb
    hx0 += host.x
    hx1 += host.x
    hy0 += host.y
    hy1 += host.y
    x_values = _rounded(
        (
            mover.x,
            mover.x - 1,
            mover.x + 1,
            fit.x.lower,
            fit.x.upper,
            hx0 - mx0,
            hx0 - mx1,
            hx1 - mx0,
            hx1 - mx1,
            (hx0 + hx1 - mx0 - mx1) / 2.0,
        )
    )
    y_values = _rounded(
        (
            mover.y,
            mover.y - 1,
            mover.y + 1,
            fit.y.lower,
            fit.y.upper,
            hy0 - my0,
            hy0 - my1,
            hy1 - my0,
            hy1 - my1,
            (hy0 + hy1 - my0 - my1) / 2.0,
        )
    )
    valid = (
        (x, y)
        for x, y in product(x_values, y_values)
        if fit.x.lower <= x <= fit.x.upper and fit.y.lower <= y <= fit.y.upper
    )
    return tuple(
        sorted(
            set(valid),
            key=lambda item: (
                abs(item[0] - mover.x) + abs(item[1] - mover.y),
                item[0],
                item[1],
            ),
        )[: config.max_offsets]
    )


def generate_interlock_candidates(
    snapshot: SolutionSnapshot,
    context: InterlockContext,
    budget: Budget,
    config: InterlockConfig | None = None,
) -> Iterator[InterlockCandidate]:
    """Yield bounded offsets whose exact relation is one-way only."""
    config = config or context.config
    by_id = {item.block_id: item for item in snapshot.placements}
    movers = tuple(
        sorted(
            (
                item
                for item in snapshot.placements
                if item.exit > context.instance.block(item.block_id).due_date
            ),
            key=lambda item: (
                -(item.exit - context.instance.block(item.block_id).due_date),
                context.instance.block(item.block_id).due_date,
                item.block_id,
            ),
        )[: config.max_movers]
    )
    seen: set[tuple[int, ...]] = set()
    for window in saturated_windows(snapshot, context.instance, context.kernel)[: config.max_windows]:
        if not budget.can_start(0.0, margin=0.0001):
            return
        for mover in movers:
            if mover.bay_id != window.bay_id:
                continue
            potential = context.instance.weights.w1 * max(
                0, mover.exit - context.instance.block(mover.block_id).due_date
            )
            if potential <= 0.0:
                continue
            hosts = rank_hosts(snapshot, mover.block_id, window.active_ids, context)
            for host in hosts[: config.max_hosts_per_mover]:
                for x, y in _offsets(mover, host, context, config):
                    if not budget.can_start(0.0, margin=0.0001):
                        return
                    placement = replace(mover, x=x, y=y)
                    key = (
                        mover.block_id,
                        host.block_id,
                        placement.bay_id,
                        placement.orient_idx,
                        x,
                        y,
                    )
                    if key in seen or not context.kernel.fits(placement):
                        continue
                    seen.add(key)
                    # AABB is a rejection-only filter.  Exact Shapely geometry
                    # below remains the relation authority.
                    if not _aabb_overlaps(placement, host, context.kernel):
                        continue
                    relation = context.kernel.relation(placement, host)
                    if relation.state not in {PairState.I_OUTER, PairState.K_OUTER}:
                        continue
                    slack = context.instance.block(host.block_id).due_date - host.exit
                    yield InterlockCandidate(
                        mover.block_id,
                        host.block_id,
                        placement,
                        relation,
                        window,
                        float(potential + 0.001 * max(0, slack)),
                    )


def _apply_transaction(
    snapshot: SolutionSnapshot,
    candidate: InterlockCandidate,
    context: InterlockContext,
    budget: Budget,
) -> tuple[SolutionSnapshot | None, frozenset[int], str]:
    by_id = {item.block_id: item for item in snapshot.placements}
    if candidate.mover not in by_id or candidate.host not in by_id:
        return None, frozenset(), "UNKNOWN_BLOCK"
    if not context.kernel.fits(candidate.placement):
        return None, frozenset(), "OUT_OF_BOUNDS"
    replacements = dict(by_id)
    replacements[candidate.mover] = candidate.placement
    affected = {candidate.mover, candidate.host}
    for other in snapshot.placements:
        if other.block_id == candidate.mover or other.bay_id != candidate.placement.bay_id:
            continue
        if not budget.can_start(0.0, margin=0.0001):
            return None, frozenset(), "BUDGET"
        relation = context.kernel.relation(candidate.placement, other)
        if candidate.placement.entry == other.entry and relation.state is not PairState.FREE:
            return None, frozenset(), "SIMULTANEOUS_ONE_WAY_ENTRY"
        if not relation.allows(candidate.placement, other):
            return None, frozenset(), "AFFECTED_RELATION"
        if relation.state is not PairState.FREE:
            affected.add(other.block_id)
    draft = SolutionSnapshot(tuple(replacements[key] for key in sorted(replacements)))
    try:
        # Defensive same-exit DAG precheck; the post-retime snapshot is checked
        # again before the full checker.
        serialize(draft, context.kernel)
    except Exception:
        return None, frozenset(), "SERIALIZATION_PRECHECK"
    return draft.with_objective(compute_objective(context.instance, draft)), frozenset(affected), "OK"


def _empty_metrics(reason: str, pressure: float, cap: float) -> DensifyMetrics:
    return DensifyMetrics(reason, pressure, cap, 0, 0, 0, 0, 0, 0, 0, 0.0, 0.0, 0.0)


def densify(
    snapshot: SolutionSnapshot,
    best_store: IncumbentStore,
    context: InterlockContext,
    budget: Budget,
    retime_hook: Callable[..., Any] | None,
) -> DensifyResult:
    """Try bounded layout drafts and atomically install strict improvements."""
    config = context.config
    # Feature-off is intentionally checked before any geometry or other gate
    # input so the disabled submission path is bit-identical and O(1).
    if not config.enabled:
        return DensifyResult(
            snapshot,
            best_store.operations,
            _empty_metrics("FEATURE_DISABLED", 0.0, 0.0),
        )

    objective = snapshot.objective or compute_objective(context.instance, snapshot)
    geometry_started = context.clock()
    pressure = union_energy_pressure(snapshot, context.instance, context.kernel)
    geometry_time = max(0.0, context.clock() - geometry_started)
    decision = decide_interlock_gate(
        InterlockGate(
            True,
            objective.z1,
            pressure,
            context.stalled,
            budget.remaining(),
            budget.reserve,
        ),
        pressure_threshold=config.pressure_threshold,
    )
    cap = densifier_budget_cap(
        budget.limit,
        budget.remaining(),
        budget.reserve,
        fraction=config.max_budget_fraction,
    )
    if not decision.run or retime_hook is None or cap <= 0.001:
        reason = decision.reason if not decision.run else (
            "NO_RETIME_HOOK" if retime_hook is None else "BUDGET"
        )
        metrics = _empty_metrics(reason, pressure, cap)
        return DensifyResult(
            snapshot,
            best_store.operations,
            replace(metrics, geometry_time_s=geometry_time),
        )

    child = budget.child(cap)
    attempts = 0
    retime_attempts = 0
    checker_attempts = 0
    feasible_improvements = 0
    layout_improvements = 0
    interlock_improvements = 0
    installed = 0
    retime_time = 0.0
    checker_time = 0.0
    selected_modes: list[str] = []
    rollbacks: Counter[str] = Counter()
    installed_snapshot: SolutionSnapshot | None = None

    try:
        candidates = generate_interlock_candidates(snapshot, context, child, config)
        for candidate in candidates:
            if not child.can_start(0.0, margin=0.0001):
                rollbacks["BUDGET"] += 1
                break
            attempts += 1
            started = context.clock()
            draft, affected, draft_status = _apply_transaction(
                snapshot, candidate, context, child
            )
            geometry_time += max(0.0, context.clock() - started)
            if draft is None:
                rollbacks[draft_status] += 1
                continue
            if not child.can_start(0.0, margin=0.0001):
                rollbacks["BUDGET_BEFORE_RETIME"] += 1
                break
            retime_attempts += 1
            started = context.clock()
            try:
                retimed = retime_hook(
                    draft,
                    context.instance,
                    context.kernel,
                    child,
                    affected_ids=affected,
                )
            except Exception as exc:
                retime_time += max(0.0, context.clock() - started)
                rollbacks[f"RETIME_{type(exc).__name__}"] += 1
                continue
            retime_time += max(0.0, context.clock() - started)
            status = str(getattr(retimed, "status", "")).upper()
            if status in {
                "ERROR",
                "INVALID_INPUT",
                "INVALID_REQUEST",
                "INVALID_SOLUTION",
                "INVALID_RELATION",
                "CHECKER_REJECTED",
                "OBJECTIVE_MISMATCH",
                "WORSE_Z1",
                "INFEASIBLE",
                "NUMERIC",
            }:
                rollbacks[f"RETIME_{status}"] += 1
                continue
            retimed_snapshot = getattr(retimed, "snapshot", None)
            if not isinstance(retimed_snapshot, SolutionSnapshot):
                rollbacks["RETIME_NO_SNAPSHOT"] += 1
                continue
            after_by_id = {item.block_id: item for item in retimed_snapshot.placements}
            if candidate.mover not in after_by_id or candidate.host not in after_by_id:
                rollbacks["RETIME_MISSING_BLOCK"] += 1
                continue
            relation = context.kernel.relation(
                after_by_id[candidate.mover], after_by_id[candidate.host]
            )
            if relation.state not in {PairState.I_OUTER, PairState.K_OUTER}:
                rollbacks["RETIME_LOST_ONE_WAY"] += 1
                continue
            mode = relation.mode(
                after_by_id[candidate.mover], after_by_id[candidate.host]
            )
            selected_modes.append(mode.value)
            nested = mode in {TemporalMode.I_NESTED, TemporalMode.K_NESTED}
            if not child.can_start(budget.checker_p95, margin=config.checker_margin):
                rollbacks["BUDGET_BEFORE_CHECKER"] += 1
                break
            try:
                operations = serialize(retimed_snapshot, context.kernel)
            except Exception:
                rollbacks["SERIALIZATION_CYCLE"] += 1
                continue
            candidate_objective = compute_objective(context.instance, retimed_snapshot)
            retimed_snapshot = retimed_snapshot.with_objective(candidate_objective)
            checker_attempts += 1
            started = context.clock()
            try:
                checked = context.checker(
                    copy.deepcopy(dict(context.raw)), copy.deepcopy(operations)
                )
            except Exception as exc:
                checker_time += max(0.0, context.clock() - started)
                rollbacks[f"CHECKER_{type(exc).__name__}"] += 1
                continue
            duration = max(0.0, context.clock() - started)
            checker_time += duration
            budget.record_checker_duration(duration)
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
            if (
                checked.get("feasible") is not True
                or checked.get("stage") != 5
                or any(
                    isinstance(value, bool) or not isinstance(value, (int, float))
                    for value in external
                )
                or not all(
                    math.isclose(float(left), float(right), rel_tol=1e-6, abs_tol=1e-9)
                    for left, right in zip(internal, external)
                )
            ):
                rollbacks["CHECKER_REJECTED"] += 1
                continue
            current_best = best_store.snapshot.objective
            assert current_best is not None
            if candidate_objective.total >= current_best.total:
                rollbacks["NOT_STRICT_IMPROVEMENT"] += 1
                continue
            feasible_improvements += 1
            if nested:
                interlock_improvements += 1
            else:
                layout_improvements += 1
            if best_store.install_if_valid(retimed_snapshot, operations, checked):
                installed += 1
                installed_snapshot = best_store.snapshot
                break
            rollbacks["STORE_REJECTED"] += 1
    except Exception as exc:
        rollbacks[f"GENERATOR_{type(exc).__name__}"] += 1

    metrics = DensifyMetrics(
        "RUN",
        pressure,
        cap,
        attempts,
        retime_attempts,
        checker_attempts,
        feasible_improvements,
        layout_improvements,
        interlock_improvements,
        installed,
        geometry_time,
        retime_time,
        checker_time,
        tuple(selected_modes),
        tuple(sorted(rollbacks.items())),
    )
    return DensifyResult(
        installed_snapshot or snapshot,
        best_store.operations,
        metrics,
    )


__all__ = [
    "DensifyMetrics",
    "DensifyResult",
    "GateDecision",
    "InterlockCandidate",
    "InterlockConfig",
    "InterlockContext",
    "InterlockGate",
    "SaturatedWindow",
    "decide_interlock_gate",
    "densifier_budget_cap",
    "densify",
    "generate_interlock_candidates",
    "rank_hosts",
    "saturated_windows",
    "union_energy_pressure",
]
