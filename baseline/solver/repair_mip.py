"""Optional bounded candidate-selection MIP repair.

The module is backend-neutral and imports Gurobi only inside the default
factory.  Every public repair failure dispatches the same destroyed set to the
pure-Python heuristic engine.
"""

from __future__ import annotations

import math
import time
from collections import defaultdict
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, replace
from itertools import combinations
from typing import Any, Protocol

from .budget import Budget
from .geometry import GeometryKernel, PairState
from .neighborhoods import (
    CompleteCandidate,
    NeighborhoodContext,
    RepairResult,
    destroy_snapshot,
    export_complete_candidates,
    heuristic_repair,
    locally_feasible,
)
from .state import Placement, SolutionSnapshot, compute_objective


CandidateRef = tuple[int, int]


class MipCapError(ValueError):
    pass


class MipDeadline(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class MipRepairConfig:
    max_blocks: int = 16
    max_per_block: int = 32
    max_product: int = 512
    min_timebox_s: float = 0.2
    max_timebox_s: float = 3.0
    max_cut_iterations: int = 64
    threads: int = 4
    seed: int = 20260710
    soft_mem_limit: float = 12.0

    def __post_init__(self) -> None:
        integer_values = (
            self.max_blocks,
            self.max_per_block,
            self.max_product,
            self.max_cut_iterations,
            self.threads,
            self.seed,
        )
        if any(isinstance(value, bool) or not isinstance(value, int) for value in integer_values):
            raise TypeError("MIP caps, threads, and seed must be integers")
        if min(integer_values[:5]) <= 0:
            raise ValueError("MIP caps and threads must be positive")
        if self.max_blocks > 16 or self.max_per_block > 32 or self.max_product > 512:
            raise ValueError("MIP hard caps are 16 blocks, 32 candidates, product 512")
        if not 0.0 < self.min_timebox_s <= self.max_timebox_s <= 3.0:
            raise ValueError("MIP timebox must satisfy 0 < min <= max <= 3 seconds")
        if self.max_blocks * self.max_per_block > self.max_product:
            raise ValueError("configured block/candidate product exceeds the hard cap")


@dataclass(frozen=True, slots=True)
class CandidateTable:
    rows: tuple[tuple[int, tuple[CompleteCandidate, ...]], ...]
    before_count: int
    after_count: int
    product: int
    incumbent_choice: tuple[CandidateRef, ...]

    def row_map(self) -> dict[int, tuple[CompleteCandidate, ...]]:
        return dict(self.rows)


@dataclass(frozen=True, slots=True)
class ConflictIndex:
    excluded: frozenset[CandidateRef]
    prefilter_checks: int
    exact_checks: int
    aabb_skips: int

    @classmethod
    def build(
        cls,
        candidates: CandidateTable,
        unchanged_state: SolutionSnapshot,
        kernel: GeometryKernel,
        budget: Budget | None = None,
    ) -> "ConflictIndex":
        excluded: set[CandidateRef] = set()
        prefilter_checks = 0
        exact_before = kernel.cache_info().misses
        aabb_skips = 0
        unchanged = unchanged_state.placements
        for block_id, row in candidates.rows:
            for candidate_index, candidate in enumerate(row):
                if budget is not None and not budget.can_start(0.0, margin=0.0001):
                    raise MipDeadline("deadline during unchanged conflict prefilter")
                prefilter_checks += 1
                conflicts, cycles = inspect_selection(
                    (candidate,), kernel, unchanged=unchanged
                )
                if conflicts or cycles:
                    excluded.add((block_id, candidate_index))
                else:
                    aabb_skips += sum(
                        _obviously_independent(candidate.placement, item, kernel)
                        for item in unchanged
                    )
        return cls(
            excluded=frozenset(excluded),
            prefilter_checks=prefilter_checks,
            exact_checks=max(0, kernel.cache_info().misses - exact_before),
            aabb_skips=aabb_skips,
        )


@dataclass(frozen=True, slots=True)
class MipSelectionRequest:
    rows: tuple[tuple[int, tuple[CompleteCandidate, ...]], ...]
    excluded: frozenset[CandidateRef]
    pair_cuts: frozenset[tuple[CandidateRef, CandidateRef]]
    no_good_cuts: tuple[tuple[CandidateRef, ...], ...]
    incumbent_choice: tuple[CandidateRef, ...]
    unchanged_loads: tuple[float, ...]
    load_factors: tuple[float, ...]
    workloads: tuple[float, ...]
    w1: float
    w2: float
    w3: float
    time_limit: float
    threads: int
    seed: int
    soft_mem_limit: float


@dataclass(frozen=True, slots=True)
class MipBackendResult:
    status: str
    selected: tuple[CandidateRef, ...] = ()
    objective: float | None = None
    bound: float | None = None
    gap: float | None = None
    runtime: float = 0.0
    variables: int = 0
    constraints: int = 0
    diagnostics: tuple[str, ...] = ()


class MipBackend(Protocol):
    def solve(self, request: MipSelectionRequest) -> MipBackendResult: ...


def _known_cost(candidate: CompleteCandidate, context: NeighborhoodContext) -> float:
    return candidate.weighted_known_cost(context)


def canonicalize_candidates(
    current: SolutionSnapshot,
    destroyed: tuple[int, ...],
    candidates: Iterable[tuple[int, Iterable[CompleteCandidate]]],
    context: NeighborhoodContext,
    config: MipRepairConfig | None = None,
) -> CandidateTable:
    config = config or MipRepairConfig()
    if len(destroyed) != len(set(destroyed)):
        raise ValueError("destroyed ids must be unique")
    if len(destroyed) > config.max_blocks:
        raise MipCapError("destroyed block cap exceeded")
    provided = {int(block_id): tuple(row) for block_id, row in candidates}
    current_by_id = {item.block_id: item for item in current.placements}
    if any(block_id not in current_by_id for block_id in destroyed):
        raise ValueError("destroyed ids contain an unknown block")
    before_count = sum(len(provided.get(block_id, ())) for block_id in destroyed)
    rows: list[tuple[int, tuple[CompleteCandidate, ...]]] = []
    incumbent_choice: list[CandidateRef] = []
    per_block_limit = min(
        config.max_per_block,
        config.max_product // max(1, len(destroyed)),
    )
    if destroyed and per_block_limit <= 0:
        raise MipCapError("candidate product leaves no incumbent column")
    for block_id in destroyed:
        incumbent_key = CompleteCandidate.from_placement(
            current_by_id[block_id], context, is_incumbent=True
        ).canonical_key
        deduplicated: dict[tuple[int, ...], CompleteCandidate] = {}
        for supplied in provided.get(block_id, ()):
            if not isinstance(supplied, CompleteCandidate) or supplied.block_id != block_id:
                continue
            try:
                placement = supplied.placement
            except (TypeError, ValueError, IndexError):
                continue
            block = context.instance.block(block_id)
            if (
                placement.entry < block.release_time
                or placement.exit - placement.entry < block.dwell
                or not context.kernel.fits(placement)
            ):
                continue
            normalized = CompleteCandidate.from_placement(
                placement,
                context,
                is_incumbent=supplied.is_incumbent or supplied.canonical_key == incumbent_key,
            )
            previous = deduplicated.get(normalized.canonical_key)
            if previous is None or normalized.is_incumbent:
                deduplicated[normalized.canonical_key] = normalized
        incumbent = CompleteCandidate.from_placement(
            current_by_id[block_id], context, is_incumbent=True
        )
        deduplicated[incumbent.canonical_key] = incumbent
        ordered = sorted(
            deduplicated.values(),
            key=lambda item: (_known_cost(item, context), item.canonical_key),
        )
        selected = ordered[:per_block_limit]
        if incumbent not in selected:
            selected = selected[: per_block_limit - 1] + [incumbent]
            selected.sort(key=lambda item: (_known_cost(item, context), item.canonical_key))
        row = tuple(selected)
        rows.append((block_id, row))
        incumbent_choice.append((block_id, row.index(incumbent)))
    product = len(rows) * max((len(row) for _, row in rows), default=0)
    if product > config.max_product or any(len(row) > config.max_per_block for _, row in rows):
        raise MipCapError("candidate cap enforcement failed")
    return CandidateTable(
        rows=tuple(rows),
        before_count=before_count,
        after_count=sum(len(row) for _, row in rows),
        product=product,
        incumbent_choice=tuple(incumbent_choice),
    )


def _world_aabb(
    placement: Placement, kernel: GeometryKernel
) -> tuple[float, float, float, float]:
    xmin, ymin, xmax, ymax = kernel.shape(
        placement.block_id, placement.orient_idx
    ).full_aabb
    return xmin + placement.x, ymin + placement.y, xmax + placement.x, ymax + placement.y


def _aabb_overlap(left: Placement, right: Placement, kernel: GeometryKernel) -> bool:
    lx0, ly0, lx1, ly1 = _world_aabb(left, kernel)
    rx0, ry0, rx1, ry1 = _world_aabb(right, kernel)
    return lx0 < rx1 and rx0 < lx1 and ly0 < ry1 and ry0 < ly1


def _obviously_independent(
    left: Placement, right: Placement, kernel: GeometryKernel
) -> bool:
    if left.bay_id != right.bay_id:
        return True
    temporal_overlap = left.entry < right.exit and right.entry < left.exit
    same_event = left.entry == right.entry or left.exit == right.exit
    if not temporal_overlap and not same_event:
        return True
    return not _aabb_overlap(left, right, kernel)


def _cyclic_nodes(nodes: tuple[int, ...], edges: tuple[tuple[int, int], ...]) -> tuple[int, ...]:
    incoming = {node: 0 for node in nodes}
    outgoing: dict[int, list[int]] = {node: [] for node in nodes}
    for source, target in edges:
        if source == target or source not in incoming or target not in incoming:
            continue
        outgoing[source].append(target)
        incoming[target] += 1
    ready = sorted(node for node, count in incoming.items() if count == 0)
    visited: list[int] = []
    while ready:
        node = ready.pop(0)
        visited.append(node)
        for target in sorted(outgoing[node]):
            incoming[target] -= 1
            if incoming[target] == 0:
                ready.append(target)
                ready.sort()
    if len(visited) == len(nodes):
        return ()
    return tuple(sorted(node for node in nodes if incoming[node] > 0))


def inspect_selection(
    selection: Iterable[CompleteCandidate],
    kernel: GeometryKernel,
    *,
    unchanged: Iterable[Placement] = (),
) -> tuple[tuple[tuple[int, int], ...], tuple[tuple[int, ...], ...]]:
    selected = tuple(selection)
    selected_ids = {item.block_id for item in selected}
    if len(selected_ids) != len(selected):
        raise ValueError("selection contains duplicate block ids")
    placements = tuple(unchanged) + tuple(item.placement for item in selected)
    conflicts: set[tuple[int, int]] = set()
    relation_cache: dict[tuple[int, int], Any] = {}

    def relation(left: Placement, right: Placement):
        key = (left.block_id, right.block_id)
        if key not in relation_cache:
            relation_cache[key] = kernel.relation(left, right)
        return relation_cache[key]

    for left, right in combinations(placements, 2):
        if not ({left.block_id, right.block_id} & selected_ids):
            continue
        temporal_overlap = left.entry < right.exit and right.entry < left.exit
        same_entry = left.entry == right.entry
        if left.bay_id != right.bay_id or (not temporal_overlap and not same_entry):
            continue
        if not _aabb_overlap(left, right, kernel):
            continue
        pair_relation = relation(left, right)
        if not pair_relation.allows(left, right) or (
            same_entry and pair_relation.state is not PairState.FREE
        ):
            conflicts.add(tuple(sorted((left.block_id, right.block_id))))

    exit_groups: dict[tuple[int, int], list[Placement]] = defaultdict(list)
    for placement in placements:
        exit_groups[(placement.bay_id, placement.exit)].append(placement)
    cycles: set[tuple[int, ...]] = set()
    for group in exit_groups.values():
        if len(group) < 2 or not any(item.block_id in selected_ids for item in group):
            continue
        edges: list[tuple[int, int]] = []
        for left, right in combinations(group, 2):
            if not _aabb_overlap(left, right, kernel):
                continue
            pair_relation = relation(left, right)
            if pair_relation.g_i_k:
                edges.append((right.block_id, left.block_id))
            if pair_relation.g_k_i:
                edges.append((left.block_id, right.block_id))
        cyclic = _cyclic_nodes(
            tuple(sorted(item.block_id for item in group)), tuple(edges)
        )
        if cyclic:
            cycles.add(cyclic)
    return tuple(sorted(conflicts)), tuple(sorted(cycles))


def _selection_from_result(
    table: CandidateTable,
    solved: MipBackendResult,
) -> tuple[tuple[CandidateRef, ...], tuple[CompleteCandidate, ...]] | None:
    row_map = table.row_map()
    refs = tuple(solved.selected)
    if len(refs) != len(table.rows) or {block_id for block_id, _ in refs} != set(row_map):
        return None
    selected: list[CompleteCandidate] = []
    canonical_refs: list[CandidateRef] = []
    for block_id, candidate_index in sorted(refs):
        row = row_map[block_id]
        if isinstance(candidate_index, bool) or not isinstance(candidate_index, int):
            return None
        if not 0 <= candidate_index < len(row):
            return None
        canonical_refs.append((block_id, candidate_index))
        selected.append(row[candidate_index])
    return tuple(canonical_refs), tuple(selected)


def _request(
    table: CandidateTable,
    current: SolutionSnapshot,
    context: NeighborhoodContext,
    excluded: frozenset[CandidateRef],
    pair_cuts: frozenset[tuple[CandidateRef, CandidateRef]],
    no_good_cuts: tuple[tuple[CandidateRef, ...], ...],
    time_limit: float,
    config: MipRepairConfig,
) -> MipSelectionRequest:
    destroyed_ids = {block_id for block_id, _ in table.rows}
    unchanged_loads = [0.0 for _ in context.instance.bays]
    for placement in current.placements:
        if placement.block_id not in destroyed_ids:
            unchanged_loads[placement.bay_id] += context.instance.block(
                placement.block_id
            ).workload
    average_area = sum(bay.area for bay in context.instance.bays) / len(
        context.instance.bays
    )
    return MipSelectionRequest(
        rows=table.rows,
        excluded=excluded,
        pair_cuts=pair_cuts,
        no_good_cuts=no_good_cuts,
        incumbent_choice=table.incumbent_choice,
        unchanged_loads=tuple(unchanged_loads),
        load_factors=tuple(average_area / bay.area for bay in context.instance.bays),
        workloads=tuple(block.workload for block in context.instance.blocks),
        w1=context.instance.weights.w1,
        w2=context.instance.weights.w2,
        w3=context.instance.weights.w3,
        time_limit=time_limit,
        threads=config.threads,
        seed=config.seed,
        soft_mem_limit=config.soft_mem_limit,
    )


def _fallback(
    current: SolutionSnapshot,
    destroyed: tuple[int, ...],
    context: NeighborhoodContext,
    budget: Budget,
    fallback_engine: Callable[..., RepairResult],
    reason: str,
    diagnostics: tuple[str, ...] = (),
) -> RepairResult:
    try:
        result = fallback_engine(current, destroyed, context, budget)
    except Exception as exc:
        return RepairResult(
            current,
            "ERROR",
            destroyed,
            frozenset(),
            0,
            0.0,
            (f"mip_fallback_reason={reason}",)
            + diagnostics
            + (f"fallback_exception={type(exc).__name__}: {exc}",),
            "mip_fallback",
        )
    return replace(
        result,
        diagnostics=(f"mip_fallback_reason={reason}",) + diagnostics + result.diagnostics,
        engine="mip_fallback",
    )


def repair_with_mip(
    current: SolutionSnapshot,
    destroyed: tuple[int, ...],
    candidates: Iterable[tuple[int, Iterable[CompleteCandidate]]] | None,
    context: NeighborhoodContext,
    budget: Budget,
    backend_factory: Callable[[], MipBackend] | None = None,
    *,
    config: MipRepairConfig | None = None,
    fallback_engine: Callable[..., RepairResult] = heuristic_repair,
) -> RepairResult:
    """Run bounded solve-inspect-add-cut repair or one heuristic fallback."""
    config = config or MipRepairConfig()
    if not destroyed:
        return RepairResult(current, "FEASIBLE", (), frozenset(), 0, 0.0, engine="mip")
    if len(destroyed) > config.max_blocks:
        return _fallback(
            current, destroyed, context, budget, fallback_engine, "BLOCK_CAP"
        )
    try:
        if candidates is None:
            candidates = export_complete_candidates(
                current,
                destroyed,
                context,
                budget,
                max_per_block=config.max_per_block,
            )
        table = canonicalize_candidates(current, destroyed, candidates, context, config)
        draft = destroy_snapshot(current, destroyed)
        available = budget.search_remaining()
        if available < config.min_timebox_s:
            return _fallback(
                current,
                destroyed,
                context,
                budget,
                fallback_engine,
                "TIMEBOX",
                (f"remaining={available}",),
            )
        child = budget.child(min(config.max_timebox_s, available))
        index = ConflictIndex.build(table, draft.retained, context.kernel, child)
        if any(ref in index.excluded for ref in table.incumbent_choice):
            return _fallback(
                current,
                destroyed,
                context,
                budget,
                fallback_engine,
                "INCUMBENT_PREFILTER",
            )
        for block_id, row in table.rows:
            if all((block_id, index_) in index.excluded for index_ in range(len(row))):
                return _fallback(
                    current,
                    destroyed,
                    context,
                    budget,
                    fallback_engine,
                    "EMPTY_ROW",
                    (f"block={block_id}",),
                )
        factory = backend_factory or _gurobi_backend_factory
        backend = factory()
        pair_cuts: set[tuple[CandidateRef, CandidateRef]] = set()
        no_good_cuts: list[tuple[CandidateRef, ...]] = []
        diagnostics = [
            f"candidates_before={table.before_count}",
            f"candidates_after={table.after_count}",
            f"product={table.product}",
            f"prefilter_checks={index.prefilter_checks}",
            f"prefilter_exact={index.exact_checks}",
            f"prefilter_excluded={len(index.excluded)}",
        ]
        last_result: MipBackendResult | None = None
        for iteration in range(config.max_cut_iterations):
            remaining = child.search_remaining()
            if remaining <= 0.001:
                return _fallback(
                    current,
                    destroyed,
                    context,
                    budget,
                    fallback_engine,
                    "TIMEOUT",
                    tuple(diagnostics),
                )
            request = _request(
                table,
                current,
                context,
                index.excluded,
                frozenset(pair_cuts),
                tuple(no_good_cuts),
                remaining,
                config,
            )
            last_result = backend.solve(request)
            diagnostics.extend(last_result.diagnostics)
            diagnostics.append(f"iteration={iteration + 1} status={last_result.status}")
            extracted = _selection_from_result(table, last_result)
            if extracted is None:
                return _fallback(
                    current,
                    destroyed,
                    context,
                    budget,
                    fallback_engine,
                    "NO_FEASIBLE_EXTRACTION",
                    tuple(diagnostics),
                )
            refs, selected = extracted
            conflicts, cycles = inspect_selection(
                selected, context.kernel, unchanged=draft.retained.placements
            )
            ref_by_block = dict(refs)
            new_cut = False
            for left_id, right_id in conflicts:
                selected_pair = tuple(
                    sorted(
                        (block_id, ref_by_block[block_id])
                        for block_id in (left_id, right_id)
                        if block_id in ref_by_block
                    )
                )
                if len(selected_pair) == 2:
                    cut = (selected_pair[0], selected_pair[1])
                    if cut not in pair_cuts:
                        pair_cuts.add(cut)
                        new_cut = True
                elif selected_pair:
                    cut = tuple(selected_pair)
                    if cut not in no_good_cuts:
                        no_good_cuts.append(cut)
                        new_cut = True
            for cycle in cycles:
                cut = tuple(
                    sorted(
                        (block_id, ref_by_block[block_id])
                        for block_id in cycle
                        if block_id in ref_by_block
                    )
                )
                if cut and cut not in no_good_cuts:
                    no_good_cuts.append(cut)
                    new_cut = True
            if conflicts or cycles:
                diagnostics.append(
                    f"cuts_pair={len(pair_cuts)} cuts_cycle={len(no_good_cuts)}"
                )
                if not new_cut:
                    return _fallback(
                        current,
                        destroyed,
                        context,
                        budget,
                        fallback_engine,
                        "CUT_STALL",
                        tuple(diagnostics),
                    )
                continue

            selected_by_id = {item.block_id: item.placement for item in selected}
            snapshot = SolutionSnapshot(
                tuple(
                    selected_by_id.get(item.block_id, item)
                    for item in current.placements
                )
            )
            snapshot = snapshot.with_objective(compute_objective(context.instance, snapshot))
            if not locally_feasible(snapshot, context):
                return _fallback(
                    current,
                    destroyed,
                    context,
                    budget,
                    fallback_engine,
                    "LOCAL_REJECTED",
                    tuple(diagnostics),
                )
            before = current.objective or compute_objective(context.instance, current)
            changed = frozenset(
                block_id
                for block_id in destroyed
                if selected_by_id[block_id]
                != next(item for item in current.placements if item.block_id == block_id)
            )
            diagnostics.extend(
                (
                    f"selected={refs}",
                    f"conflict_cuts={len(pair_cuts)}",
                    f"cycle_cuts={len(no_good_cuts)}",
                    f"status={last_result.status}",
                    f"gap={last_result.gap}",
                    f"runtime={last_result.runtime}",
                )
            )
            return RepairResult(
                snapshot,
                "FEASIBLE",
                destroyed,
                changed,
                table.after_count,
                snapshot.objective.total - before.total,
                tuple(diagnostics),
                "mip",
            )
        return _fallback(
            current,
            destroyed,
            context,
            budget,
            fallback_engine,
            "CUT_ITERATION_CAP",
            tuple(diagnostics),
        )
    except Exception as exc:
        return _fallback(
            current,
            destroyed,
            context,
            budget,
            fallback_engine,
            "EXCEPTION",
            (f"{type(exc).__name__}: {exc}",),
        )


def make_mip_repair_engine(
    *,
    config: MipRepairConfig | None = None,
    backend_factory: Callable[[], MipBackend] | None = None,
    fallback_engine: Callable[..., RepairResult] = heuristic_repair,
) -> Callable[[SolutionSnapshot, tuple[int, ...], NeighborhoodContext, Budget], RepairResult]:
    chosen_config = config or MipRepairConfig()

    def engine(
        current: SolutionSnapshot,
        destroyed: tuple[int, ...],
        context: NeighborhoodContext,
        budget: Budget,
    ) -> RepairResult:
        return repair_with_mip(
            current,
            destroyed,
            None,
            context,
            budget,
            backend_factory,
            config=chosen_config,
            fallback_engine=fallback_engine,
        )

    engine.__name__ = "mip_repair"
    return engine


class _GurobiBackend:
    def __init__(self, gp: Any) -> None:
        self.gp = gp

    @staticmethod
    def _status_name(gp: Any, status: int) -> str:
        names = {
            gp.GRB.OPTIMAL: "OPTIMAL",
            gp.GRB.TIME_LIMIT: "TIME_LIMIT",
            gp.GRB.INFEASIBLE: "INFEASIBLE",
            gp.GRB.INF_OR_UNBD: "INF_OR_UNBD",
            gp.GRB.UNBOUNDED: "UNBOUNDED",
        }
        return names.get(status, "ERROR")

    def solve(self, request: MipSelectionRequest) -> MipBackendResult:
        gp = self.gp
        started = time.monotonic()
        model = gp.Model("candidate_selection_repair")
        model.Params.OutputFlag = 0
        model.Params.Threads = request.threads
        model.Params.MIPFocus = 1
        model.Params.SoftMemLimit = request.soft_mem_limit
        model.Params.Seed = request.seed
        model.Params.TimeLimit = max(0.001, request.time_limit)
        z = {
            (block_id, candidate_index): model.addVar(
                vtype=gp.GRB.BINARY,
                name=f"z_{block_id}_{candidate_index}",
            )
            for block_id, row in request.rows
            for candidate_index in range(len(row))
        }
        for block_id, row in request.rows:
            model.addConstr(
                gp.quicksum(z[block_id, index] for index in range(len(row))) == 1,
                name=f"choose_{block_id}",
            )
        for ref in request.excluded:
            z[ref].UB = 0.0
        for left, right in request.pair_cuts:
            model.addConstr(z[left] + z[right] <= 1)
        for cut_index, cut in enumerate(request.no_good_cuts):
            model.addConstr(
                gp.quicksum(z[ref] for ref in cut) <= len(cut) - 1,
                name=f"nogood_{cut_index}",
            )
        for ref in request.incumbent_choice:
            z[ref].Start = 1.0

        qmax = model.addVar(lb=-gp.GRB.INFINITY, name="qmax")
        qmin = model.addVar(lb=-gp.GRB.INFINITY, name="qmin")
        row_map = dict(request.rows)
        for bay_id, factor in enumerate(request.load_factors):
            load = request.unchanged_loads[bay_id] + gp.quicksum(
                request.workloads[block_id] * z[block_id, candidate_index]
                for block_id, row in request.rows
                for candidate_index, candidate in enumerate(row)
                if candidate.bay_id == bay_id
            )
            normalized = factor * load
            model.addConstr(qmax >= normalized, name=f"qmax_{bay_id}")
            model.addConstr(qmin <= normalized, name=f"qmin_{bay_id}")
        known = gp.quicksum(
            (
                request.w1 * candidate.cost_parts.tardiness
                + request.w3 * candidate.cost_parts.preference
            )
            * z[block_id, candidate_index]
            for block_id, row in request.rows
            for candidate_index, candidate in enumerate(row)
        )
        model.setObjective(known + request.w2 * (qmax - qmin), gp.GRB.MINIMIZE)
        model.optimize()
        status = self._status_name(gp, model.Status)
        if model.SolCount <= 0:
            return MipBackendResult(
                status=status,
                runtime=time.monotonic() - started,
                variables=model.NumVars,
                constraints=model.NumConstrs,
                diagnostics=(f"SolCount={model.SolCount}",),
            )
        selected = tuple(
            (
                block_id,
                max(range(len(row)), key=lambda index: z[block_id, index].X),
            )
            for block_id, row in request.rows
        )
        return MipBackendResult(
            status=status,
            selected=selected,
            objective=float(model.ObjVal),
            bound=float(model.ObjBound),
            gap=float(model.MIPGap) if math.isfinite(float(model.MIPGap)) else None,
            runtime=time.monotonic() - started,
            variables=model.NumVars,
            constraints=model.NumConstrs,
            diagnostics=(
                f"variables={model.NumVars}",
                f"constraints={model.NumConstrs}",
                f"SolCount={model.SolCount}",
                f"ObjVal={model.ObjVal}",
                f"ObjBound={model.ObjBound}",
                f"MIPGap={model.MIPGap}",
            ),
        )


def _gurobi_backend_factory() -> MipBackend:
    import gurobipy as gp

    return _GurobiBackend(gp)


__all__ = [
    "CandidateTable",
    "ConflictIndex",
    "MipBackend",
    "MipBackendResult",
    "MipCapError",
    "MipRepairConfig",
    "MipSelectionRequest",
    "canonicalize_candidates",
    "inspect_selection",
    "make_mip_repair_engine",
    "repair_with_mip",
]
