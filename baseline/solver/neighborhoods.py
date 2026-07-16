"""Deterministic heuristic destroy and transactional regret repair."""

from __future__ import annotations

import math
import random
from dataclasses import dataclass
from itertools import combinations
from typing import Protocol

from .budget import Budget
from .construct import (
    ConstructorConfig,
    commit_insertion_candidate,
    generate_position_candidates,
    generate_insertion_candidates,
    generate_time_candidates,
)
from .geometry import GeometryKernel, PairState
from .instance import Instance
from .native_repair import NativeRepairSession
from .state import IndexedSolutionState, Placement, SolutionSnapshot, compute_objective


@dataclass(frozen=True, slots=True)
class NeighborhoodContext:
    instance: Instance
    kernel: GeometryKernel
    repair_config: ConstructorConfig = ConstructorConfig(max_profiles=1)


@dataclass(frozen=True, slots=True)
class CandidateCostParts:
    tardiness: float
    preference: float


@dataclass(frozen=True, slots=True)
class CompleteCandidate:
    block_id: int
    bay_id: int
    orient_idx: int
    x: int
    y: int
    entry: int
    exit: int
    cost_parts: CandidateCostParts
    is_incumbent: bool = False

    @classmethod
    def from_placement(
        cls,
        placement: Placement,
        context: NeighborhoodContext,
        *,
        is_incumbent: bool = False,
    ) -> "CompleteCandidate":
        block = context.instance.block(placement.block_id)
        return cls(
            block_id=placement.block_id,
            bay_id=placement.bay_id,
            orient_idx=placement.orient_idx,
            x=placement.x,
            y=placement.y,
            entry=placement.entry,
            exit=placement.exit,
            cost_parts=CandidateCostParts(
                tardiness=float(max(0, placement.exit - block.due_date)),
                preference=float(
                    max(block.bay_preferences)
                    - block.bay_preferences[placement.bay_id]
                ),
            ),
            is_incumbent=is_incumbent,
        )

    @property
    def placement(self) -> Placement:
        return Placement(
            self.block_id,
            self.bay_id,
            self.orient_idx,
            self.x,
            self.y,
            self.entry,
            self.exit,
        )

    @property
    def canonical_key(self) -> tuple[int, ...]:
        return (
            self.block_id,
            self.bay_id,
            self.orient_idx,
            self.x,
            self.y,
            self.entry,
            self.exit,
        )

    def weighted_known_cost(self, context: NeighborhoodContext) -> float:
        return (
            context.instance.weights.w1 * self.cost_parts.tardiness
            + context.instance.weights.w3 * self.cost_parts.preference
        )


class DestroyOperator(Protocol):
    name: str

    def select(
        self,
        current: SolutionSnapshot,
        k: int,
        rng: random.Random,
        context: NeighborhoodContext,
    ) -> tuple[int, ...]: ...


def _cap(current: SolutionSnapshot, k: int) -> int:
    return min(len(current.placements), max(0, int(k)))


def _by_id(snapshot: SolutionSnapshot) -> dict[int, Placement]:
    return {item.block_id: item for item in snapshot.placements}


@dataclass(frozen=True, slots=True)
class TardyChainDestroy:
    name: str = "tardy_chain"

    def select(self, current, k, rng, context) -> tuple[int, ...]:
        del rng
        limit = _cap(current, k)
        if not limit:
            return ()
        by_id = _by_id(current)
        root = min(
            by_id,
            key=lambda block_id: (
                -max(
                    0,
                    by_id[block_id].exit
                    - context.instance.block(block_id).due_date,
                ),
                context.instance.block(block_id).due_date,
                block_id,
            ),
        )
        selected: list[int] = []
        queue = [root]
        seen = {root}
        while queue and len(selected) < limit:
            block_id = queue.pop(0)
            selected.append(block_id)
            placement = by_id[block_id]
            related: list[tuple[int, int, int]] = []
            for other_id, other in by_id.items():
                if other_id in seen or other.bay_id != placement.bay_id:
                    continue
                relation = context.kernel.relation(placement, other)
                if relation.state is PairState.FREE:
                    continue
                time_gap = max(
                    0,
                    max(placement.entry, other.entry)
                    - min(placement.exit, other.exit),
                )
                related.append((time_gap, abs(placement.exit - other.exit), other_id))
            for _, _, other_id in sorted(related):
                seen.add(other_id)
                queue.append(other_id)
        if len(selected) < limit:
            remaining = sorted(
                (block_id for block_id in by_id if block_id not in seen),
                key=lambda block_id: (
                    -max(
                        0,
                        by_id[block_id].exit
                        - context.instance.block(block_id).due_date,
                    ),
                    block_id,
                ),
            )
            selected.extend(remaining[: limit - len(selected)])
        return tuple(selected)


@dataclass(frozen=True, slots=True)
class CongestedWindowDestroy:
    name: str = "congested_window"

    def select(self, current, k, rng, context) -> tuple[int, ...]:
        del rng
        limit = _cap(current, k)
        if not limit:
            return ()
        best: tuple[float, int, int, tuple[int, ...]] | None = None
        for bay in context.instance.bays:
            placements = tuple(item for item in current.placements if item.bay_id == bay.index)
            events = sorted({date for item in placements for date in (item.entry, item.exit)})
            for left, right in zip(events, events[1:]):
                if right <= left:
                    continue
                active = tuple(
                    sorted(
                        item.block_id
                        for item in placements
                        if item.entry < right and left < item.exit
                    )
                )
                if not active:
                    continue
                area = sum(
                    context.kernel.shape(block_id, _by_id(current)[block_id].orient_idx).union.area
                    for block_id in active
                )
                pressure = float(area) / max(1.0, bay.area)
                candidate = (-pressure, left, bay.index, active)
                if best is None or candidate < best:
                    best = candidate
        if best is None:
            return tuple(sorted(_by_id(current))[:limit])
        active = list(best[3])
        if len(active) < limit:
            bay_id = best[2]
            center = best[1]
            extra = sorted(
                (
                    item
                    for item in current.placements
                    if item.bay_id == bay_id and item.block_id not in active
                ),
                key=lambda item: (
                    min(abs(item.entry - center), abs(item.exit - center)),
                    item.block_id,
                ),
            )
            active.extend(item.block_id for item in extra[: limit - len(active)])
        return tuple(active[:limit])


@dataclass(frozen=True, slots=True)
class ShawDestroy:
    name: str = "shaw"

    def select(self, current, k, rng, context) -> tuple[int, ...]:
        limit = _cap(current, k)
        if not limit:
            return ()
        ordered = sorted(current.placements, key=lambda item: item.block_id)
        seed = ordered[rng.randrange(len(ordered))]
        sx0, sy0, sx1, sy1 = context.kernel.shape(seed.block_id, seed.orient_idx).full_aabb
        seed_x = seed.x + (sx0 + sx1) / 2.0
        seed_y = seed.y + (sy0 + sy1) / 2.0
        span = max(
            1.0,
            max((bay.width + bay.height) for bay in context.instance.bays),
        )

        def related(item: Placement) -> tuple[float, int]:
            x0, y0, x1, y1 = context.kernel.shape(item.block_id, item.orient_idx).full_aabb
            spatial = (abs(seed_x - item.x - (x0 + x1) / 2.0) + abs(seed_y - item.y - (y0 + y1) / 2.0)) / span
            temporal = (
                abs(seed.entry - item.entry) + abs(seed.exit - item.exit)
            ) / max(1, seed.exit - seed.entry)
            assignment = 0.0 if seed.bay_id == item.bay_id else 1.0
            return (spatial + temporal + assignment, item.block_id)

        return tuple(item.block_id for item in sorted(ordered, key=related)[:limit])


@dataclass(frozen=True, slots=True)
class Z2ContributorDestroy:
    name: str = "z2_contributor"

    def select(self, current, k, rng, context) -> tuple[int, ...]:
        del rng
        limit = _cap(current, k)
        if not limit:
            return ()
        instance = context.instance
        by_id = _by_id(current)
        raw = [0.0 for _ in instance.bays]
        for placement in current.placements:
            raw[placement.bay_id] += instance.block(placement.block_id).workload
        average_area = sum(bay.area for bay in instance.bays) / len(instance.bays)
        normalized = [average_area / bay.area * raw[bay.index] for bay in instance.bays]
        before = max(normalized) - min(normalized) if len(normalized) > 1 else 0.0
        scored: list[tuple[float, float, int]] = []
        for block_id, placement in by_id.items():
            block = instance.block(block_id)
            best_reduction = 0.0
            fitting_bays = {bay_id for bay_id, _, _ in block.fitting_options}
            for bay_id in sorted(fitting_bays):
                if bay_id == placement.bay_id:
                    continue
                trial = list(normalized)
                trial[placement.bay_id] -= average_area / instance.bay(placement.bay_id).area * block.workload
                trial[bay_id] += average_area / instance.bay(bay_id).area * block.workload
                best_reduction = max(best_reduction, before - (max(trial) - min(trial)))
            scored.append((-best_reduction, -block.workload, block_id))
        return tuple(block_id for _, _, block_id in sorted(scored)[:limit])


@dataclass(frozen=True, slots=True)
class PreferenceAlternativeDestroy:
    name: str = "preference_alternative"

    def select(self, current, k, rng, context) -> tuple[int, ...]:
        del rng
        limit = _cap(current, k)
        scored: list[tuple[float, float, int]] = []
        for placement in current.placements:
            block = context.instance.block(placement.block_id)
            best = max(block.bay_preferences)
            loss = best - block.bay_preferences[placement.bay_id]
            alternatives = {
                bay_id for bay_id, _, _ in block.fitting_options if bay_id != placement.bay_id
            }
            alt_loss = min(
                (best - block.bay_preferences[bay_id] for bay_id in alternatives),
                default=math.inf,
            )
            gain = loss - alt_loss if math.isfinite(alt_loss) else -math.inf
            scored.append((-gain, -loss, placement.block_id))
        return tuple(block_id for _, _, block_id in sorted(scored)[:limit])


@dataclass(frozen=True, slots=True)
class RandomKDestroy:
    name: str = "random_k"

    def select(self, current, k, rng, context) -> tuple[int, ...]:
        del context
        limit = _cap(current, k)
        return tuple(rng.sample(sorted(_by_id(current)), limit)) if limit else ()


DEFAULT_DESTROY_OPERATORS: tuple[DestroyOperator, ...] = (
    TardyChainDestroy(),
    CongestedWindowDestroy(),
    ShawDestroy(),
    Z2ContributorDestroy(),
    PreferenceAlternativeDestroy(),
    RandomKDestroy(),
)


@dataclass(frozen=True, slots=True)
class DestroyedDraft:
    original: SolutionSnapshot
    retained: SolutionSnapshot
    destroyed: tuple[Placement, ...]
    boundary_ids: frozenset[int]


def destroy_snapshot(
    current: SolutionSnapshot,
    destroyed_ids: tuple[int, ...],
) -> DestroyedDraft:
    ids = frozenset(destroyed_ids)
    if len(ids) != len(destroyed_ids):
        raise ValueError("destroyed ids must be unique")
    by_id = _by_id(current)
    if not ids <= set(by_id):
        raise ValueError("destroyed ids contain an unknown block")
    destroyed = tuple(by_id[block_id] for block_id in destroyed_ids)
    retained_placements = tuple(item for item in current.placements if item.block_id not in ids)
    affected_bays = {item.bay_id for item in destroyed}
    boundary = frozenset(
        item.block_id for item in retained_placements if item.bay_id in affected_bays
    )
    return DestroyedDraft(
        original=current,
        retained=SolutionSnapshot(retained_placements),
        destroyed=destroyed,
        boundary_ids=boundary,
    )


def _world_aabb(
    candidate: Placement,
    kernel: GeometryKernel,
) -> tuple[float, float, float, float]:
    xmin, ymin, xmax, ymax = kernel.shape(
        candidate.block_id, candidate.orient_idx
    ).full_aabb
    return (
        xmin + candidate.x,
        ymin + candidate.y,
        xmax + candidate.x,
        ymax + candidate.y,
    )


def _aabb_overlaps(left: Placement, right: Placement, kernel: GeometryKernel) -> bool:
    lx0, ly0, lx1, ly1 = _world_aabb(left, kernel)
    rx0, ry0, rx1, ry1 = _world_aabb(right, kernel)
    return lx0 < rx1 and rx0 < lx1 and ly0 < ry1 and ry0 < ly1


def _candidate_allows_unchanged(
    candidate: Placement,
    retained: tuple[Placement, ...],
    kernel: GeometryKernel,
) -> bool:
    if not kernel.fits(candidate):
        return False
    for existing in retained:
        if existing.bay_id != candidate.bay_id:
            continue
        overlaps = candidate.entry < existing.exit and existing.entry < candidate.exit
        same_entry = candidate.entry == existing.entry
        if not overlaps and not same_entry:
            continue
        if not _aabb_overlaps(candidate, existing, kernel):
            continue
        relation = kernel.relation(candidate, existing)
        if not relation.allows(candidate, existing):
            return False
        if same_entry and relation.state is not PairState.FREE:
            return False
    return True


def export_complete_candidates(
    current: SolutionSnapshot,
    destroyed: tuple[int, ...],
    context: NeighborhoodContext,
    budget: Budget,
    *,
    max_per_block: int = 32,
) -> tuple[tuple[int, tuple[CompleteCandidate, ...]], ...]:
    """Export bounded independent columns against the unchanged snapshot.

    The current placement is inserted before any search and cannot be trimmed.
    Alternatives are checked exactly against unchanged blocks but deliberately
    not against other destroyed blocks; those conflicts belong to the MIP
    solve-inspect-add-cut loop.
    """
    if isinstance(max_per_block, bool) or not isinstance(max_per_block, int):
        raise TypeError("max_per_block must be an integer")
    if not 1 <= max_per_block <= 32:
        raise ValueError("max_per_block must be between 1 and 32")
    draft = destroy_snapshot(current, destroyed)
    current_by_id = _by_id(current)
    state = IndexedSolutionState(
        context.instance,
        draft.retained.placements,
        time_cap=context.repair_config.time_cap,
        anchor_cap=context.repair_config.anchor_cap,
        lattice_cap=context.repair_config.lattice_cap,
    )
    state.kernel = context.kernel
    rows: list[tuple[int, tuple[CompleteCandidate, ...]]] = []
    for block_id in destroyed:
        incumbent = CompleteCandidate.from_placement(
            current_by_id[block_id], context, is_incumbent=True
        )
        by_key: dict[tuple[int, ...], CompleteCandidate] = {
            incumbent.canonical_key: incumbent
        }
        if budget.can_start(0.0, margin=0.0001):
            block = context.instance.block(block_id)
            times = generate_time_candidates(block, state, incumbent.entry)
            for bay_id, orient_idx, _ in block.fitting_options:
                if not budget.can_start(0.0, margin=0.0001):
                    break
                bay = context.instance.bay(bay_id)
                orient = block.orientations[orient_idx]
                for entry in times:
                    current_position = None
                    if bay_id == incumbent.bay_id and orient_idx == incumbent.orient_idx:
                        current_position = (incumbent.x, incumbent.y)
                    positions = generate_position_candidates(
                        block,
                        bay,
                        orient,
                        (entry, entry + block.dwell),
                        state,
                        current_position=current_position,
                    )
                    for position_index, (x, y) in enumerate(positions):
                        if position_index % 16 == 0 and not budget.can_start(
                            0.0, margin=0.0001
                        ):
                            break
                        placement = Placement(
                            block_id,
                            bay_id,
                            orient_idx,
                            x,
                            y,
                            entry,
                            entry + block.dwell,
                        )
                        if not _candidate_allows_unchanged(
                            placement, draft.retained.placements, context.kernel
                        ):
                            continue
                        item = CompleteCandidate.from_placement(placement, context)
                        by_key.setdefault(item.canonical_key, item)
                        if len(by_key) >= max_per_block * 8:
                            break
                    if len(by_key) >= max_per_block * 8:
                        break
                if len(by_key) >= max_per_block * 8:
                    break
        ordered = sorted(
            by_key.values(),
            key=lambda item: (
                item.weighted_known_cost(context),
                item.canonical_key,
            ),
        )
        selected = ordered[:max_per_block]
        if incumbent not in selected:
            selected = selected[: max_per_block - 1] + [incumbent]
        selected.sort(
            key=lambda item: (
                item.weighted_known_cost(context),
                item.canonical_key,
            )
        )
        rows.append((block_id, tuple(selected)))
    return tuple(rows)


@dataclass(frozen=True, slots=True)
class RepairResult:
    snapshot: SolutionSnapshot
    status: str
    destroyed_ids: tuple[int, ...]
    changed_ids: frozenset[int]
    candidates_generated: int
    objective_delta: float
    diagnostics: tuple[str, ...] = ()
    engine: str = "heuristic"
    telemetry: tuple[tuple[str, object], ...] = ()

    @property
    def feasible(self) -> bool:
        return self.status == "FEASIBLE"


def locally_feasible(
    snapshot: SolutionSnapshot,
    context: NeighborhoodContext,
) -> bool:
    if len(snapshot.placements) != len(context.instance.blocks):
        return False
    by_id = _by_id(snapshot)
    if set(by_id) != set(range(len(context.instance.blocks))):
        return False
    for block_id, placement in by_id.items():
        block = context.instance.block(block_id)
        if placement.entry < block.release_time or placement.exit - placement.entry < block.dwell:
            return False
        if not context.kernel.fits(placement):
            return False
    for left, right in combinations(snapshot.placements, 2):
        if left.bay_id != right.bay_id:
            continue
        if not context.kernel.relation(left, right).allows(left, right):
            return False
    return True


def heuristic_repair(
    current: SolutionSnapshot,
    destroyed: tuple[int, ...],
    context: NeighborhoodContext,
    budget: Budget,
    *,
    regret_depth: int = 3,
) -> RepairResult:
    """Repair on a fresh indexed state; any failure returns input identity."""
    if regret_depth not in (2, 3):
        raise ValueError("regret_depth must be 2 or 3")
    backend = NativeRepairSession(
        context.instance,
        context.kernel,
        requested_backend=context.repair_config.repair_backend,
        prefilter_enabled=context.repair_config.native_prefilter_enabled,
    )
    backend.record_anchor(current)

    def finish(
        snapshot: SolutionSnapshot,
        status: str,
        destroyed_ids: tuple[int, ...],
        changed_ids: frozenset[int],
        candidates_generated: int,
        objective_delta: float,
        diagnostics: tuple[str, ...] = (),
    ) -> RepairResult:
        backend.record_final(snapshot)
        return RepairResult(
            snapshot,
            status,
            destroyed_ids,
            changed_ids,
            candidates_generated,
            objective_delta,
            diagnostics,
            telemetry=backend.telemetry(),
        )

    draft = destroy_snapshot(current, destroyed)
    if not destroyed:
        return finish(current, "FEASIBLE", (), frozenset(), 0, 0.0)
    original_by_id = _by_id(current)
    state = IndexedSolutionState(
        context.instance,
        draft.retained.placements,
        time_cap=context.repair_config.time_cap,
        anchor_cap=context.repair_config.anchor_cap,
        lattice_cap=context.repair_config.lattice_cap,
    )
    state.kernel = context.kernel
    remaining = set(destroyed)
    generated = 0
    try:
        while remaining:
            if not budget.can_start(0.0, margin=0.0001):
                return finish(
                    current,
                    "BUDGET",
                    destroyed,
                    frozenset(),
                    generated,
                    0.0,
                    ("repair deadline reached",),
                )
            options_by_id = {}
            for block_id in sorted(remaining):
                options = generate_insertion_candidates(
                    state,
                    block_id,
                    context.kernel,
                    budget,
                    current_placement=original_by_id[block_id],
                    config=context.repair_config,
                    backend_session=backend,
                )
                generated += len(options)
                if not options:
                    return finish(
                        current,
                        "NO_CANDIDATE",
                        destroyed,
                        frozenset(),
                        generated,
                        0.0,
                        (f"block={block_id}",),
                    )
                options_by_id[block_id] = options

            choices = []
            for block_id, options in options_by_id.items():
                ranked = sorted(options, key=lambda item: item.canonical_tie)
                regret = (
                    math.inf
                    if len(ranked) < regret_depth
                    else ranked[regret_depth - 1].total_delta
                    - ranked[0].total_delta
                )
                choices.append(
                    ((-regret, ranked[0].canonical_tie, block_id), block_id, ranked[0])
                )
            _, block_id, candidate = min(choices, key=lambda item: item[0])
            if not commit_insertion_candidate(state, candidate, context.kernel):
                return finish(
                    current,
                    "STALE_CANDIDATE",
                    destroyed,
                    frozenset(),
                    generated,
                    0.0,
                    (f"block={block_id}",),
                )
            remaining.remove(block_id)

        candidate = state.freeze()
        candidate = candidate.with_objective(
            compute_objective(context.instance, candidate)
        )
        if not locally_feasible(candidate, context):
            return finish(
                current,
                "LOCAL_REJECTED",
                destroyed,
                frozenset(),
                generated,
                0.0,
            )
        before = current.objective or compute_objective(context.instance, current)
        after = candidate.objective
        assert after is not None
        # The native opt-in is never allowed to replace the frozen repair
        # anchor with a worse snapshot.  Python-default ALNS acceptance keeps
        # its existing behavior; this guard applies only to the new seam.
        if (
            context.repair_config.repair_backend == "native"
            and after.total > before.total + 1e-9
        ):
            backend.reject_dominance_loss()
            return finish(
                current,
                "DOMINANCE_REJECTED",
                destroyed,
                frozenset(),
                generated,
                0.0,
                ("native result was worse than frozen repair anchor",),
            )
        changed = frozenset(
            block_id
            for block_id in destroyed
            if original_by_id[block_id] != _by_id(candidate)[block_id]
        )
        return finish(
            candidate,
            "FEASIBLE",
            destroyed,
            changed,
            generated,
            after.total - before.total,
        )
    except Exception as exc:
        return finish(
            current,
            "ERROR",
            destroyed,
            frozenset(),
            generated,
            0.0,
            (f"{type(exc).__name__}: {exc}",),
        )


__all__ = [
    "CandidateCostParts",
    "CompleteCandidate",
    "CongestedWindowDestroy",
    "DEFAULT_DESTROY_OPERATORS",
    "DestroyedDraft",
    "DestroyOperator",
    "NeighborhoodContext",
    "PreferenceAlternativeDestroy",
    "RandomKDestroy",
    "RepairResult",
    "ShawDestroy",
    "TardyChainDestroy",
    "Z2ContributorDestroy",
    "destroy_snapshot",
    "export_complete_candidates",
    "heuristic_repair",
    "locally_feasible",
]
