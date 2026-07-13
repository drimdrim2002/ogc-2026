"""Event-aware, exact union-safe constructor.

The fast path only overlaps pairs whose exact four-state relation is FREE.
Every failure path finishes the draft with deterministic bay-serial placements,
so the module does not depend on Gurobi or a non-empty assignment portfolio.
"""

from __future__ import annotations

import math
import random
import time
from dataclasses import dataclass, field
from itertools import combinations
from typing import Any, Iterable

from .budget import Budget
from .geometry import GeometryError, GeometryKernel, PairState
from .instance import BayInfo, BlockInfo, Instance, OrientationInfo
from .state import IndexedSolutionState, Placement, SolutionSnapshot, compute_objective


PROFILES = (
    "slack_due",
    "release_due",
    "congestion",
    "assignment_regret",
    "preference_pressure",
    "seeded_mixture",
)


@dataclass(frozen=True, slots=True)
class ConstructorConfig:
    seed: int = 20260710
    time_cap: int = 12
    escalated_time_cap: int = 32
    anchor_cap: int = 48
    lattice_cap: int = 512
    max_profiles: int = 6
    max_candidate_attempts: int | None = None

    def __post_init__(self) -> None:
        values = (
            self.time_cap,
            self.escalated_time_cap,
            self.anchor_cap,
            self.lattice_cap,
            self.max_profiles,
        )
        if any(isinstance(value, bool) or not isinstance(value, int) for value in values):
            raise TypeError("constructor caps must be integers")
        if min(values) < 0 or self.max_profiles > len(PROFILES):
            raise ValueError("constructor caps are outside their supported range")
        if self.escalated_time_cap < self.time_cap:
            raise ValueError("escalated time cap must cover the first-pass cap")
        if self.max_candidate_attempts is not None and (
            isinstance(self.max_candidate_attempts, bool)
            or not isinstance(self.max_candidate_attempts, int)
            or self.max_candidate_attempts <= 0
        ):
            raise ValueError("candidate attempt cap must be a positive integer or None")


@dataclass(frozen=True, slots=True)
class ConstructionSeed:
    assignment_seed: Any | None
    profile: str
    random_seed: int

    def __post_init__(self) -> None:
        if self.profile not in PROFILES:
            raise ValueError(f"unknown constructor profile {self.profile!r}")


@dataclass(frozen=True, slots=True)
class CandidateScore:
    placement: Placement
    total_delta: float
    tardiness_delta: float
    assignment_delta: float
    fragmentation: int
    canonical_tie: tuple[Any, ...]
    state_version: int


@dataclass(frozen=True, slots=True)
class ConstructionMetrics:
    profile: str
    random_seed: int
    time_cap: int
    anchor_cap: int
    lattice_cap: int
    candidates_attempted: int
    rejected_fit: int
    rejected_aabb: int
    rejected_relation: int
    escalations: int
    fallback_count: int
    placements_committed: int
    candidate_cap: int | None
    candidate_cap_exhausted: bool
    timebox_exhausted: bool
    deadline_hit: bool
    construction_time: float


@dataclass(frozen=True, slots=True)
class ConstructionResult:
    snapshot: SolutionSnapshot | None
    status: str
    metrics: ConstructionMetrics
    complete: bool


@dataclass(slots=True)
class _Counters:
    attempted: int = 0
    rejected_fit: int = 0
    rejected_aabb: int = 0
    rejected_relation: int = 0
    escalations: int = 0
    fallback_count: int = 0
    committed: int = 0
    candidate_cap_exhausted: bool = False
    timebox_exhausted: bool = False
    deadline_hit: bool = False

    @property
    def stop_requested(self) -> bool:
        return self.candidate_cap_exhausted or self.timebox_exhausted or self.deadline_hit


def _stop_for_candidate_cap(config: ConstructorConfig, counters: _Counters) -> bool:
    cap = config.max_candidate_attempts
    if cap is None or counters.attempted < cap:
        return False
    counters.candidate_cap_exhausted = True
    return True


def _reserve_safe_tail(instance: Instance) -> float:
    """Reserve a small deterministic serial-tail window before child expiry."""
    return min(0.25, max(0.02, 0.0005 * len(instance.blocks)))


def _stop_for_safe_tail(budget: Budget, counters: _Counters, reserve: float) -> bool:
    if budget.can_start(0.0, margin=reserve):
        return False
    counters.timebox_exhausted = True
    counters.deadline_hit = budget.remaining() <= 0.0
    return True


def _overlaps(left: Placement, right: Placement) -> bool:
    return left.entry < right.exit and right.entry < left.exit


def _time_candidates(
    block: BlockInfo,
    state: IndexedSolutionState,
    guide: int | None,
    cap: int,
) -> tuple[int, ...]:
    values = {block.release_time}
    for placement in state.placements:
        values.add(placement.exit)
        values.add(placement.entry - block.dwell)
    if guide is not None:
        values.update((guide - 1, guide, guide + 1))
    values = {int(value) for value in values if int(value) >= block.release_time}
    ordered = sorted(
        values,
        key=lambda entry: (
            max(0, entry + block.dwell - block.due_date),
            abs(entry - guide) if guide is not None else 0,
            entry,
        ),
    )
    return tuple(ordered[:cap]) if cap > 0 else ()


def generate_time_candidates(
    block: BlockInfo,
    state: IndexedSolutionState,
    guide: int | None,
) -> tuple[int, ...]:
    """Return canonical event dates using the state's first-pass cap."""
    return _time_candidates(block, state, guide, state.time_cap)


def _world_aabb(kernel: GeometryKernel, placement: Placement) -> tuple[float, float, float, float]:
    xmin, ymin, xmax, ymax = kernel.shape(placement.block_id, placement.orient_idx).full_aabb
    return xmin + placement.x, ymin + placement.y, xmax + placement.x, ymax + placement.y


def _integer_pair(x: float, y: float) -> tuple[tuple[int, int], ...]:
    xs = {math.floor(x), math.ceil(x)}
    ys = {math.floor(y), math.ceil(y)}
    return tuple((int(ix), int(iy)) for ix in sorted(xs) for iy in sorted(ys))


def _geometry_vertices(geometry: Any) -> tuple[tuple[float, float], ...]:
    if geometry is None or geometry.is_empty:
        return ()
    if hasattr(geometry, "exterior"):
        return tuple((float(x), float(y)) for x, y in geometry.exterior.coords)
    vertices: list[tuple[float, float]] = []
    for item in getattr(geometry, "geoms", ()):
        vertices.extend(_geometry_vertices(item))
    return tuple(vertices)


def _van_der_corput(index: int, base: int) -> float:
    value = 0.0
    denominator = 1.0
    while index:
        index, remainder = divmod(index, base)
        denominator *= base
        value += remainder / denominator
    return value


def _lattice_points(reference_range: Any, cap: int) -> tuple[tuple[int, int], ...]:
    if cap <= 0:
        return ()
    width = reference_range.x.upper - reference_range.x.lower + 1
    height = reference_range.y.upper - reference_range.y.lower + 1
    total = width * height
    if total <= cap:
        return tuple(
            (x, y)
            for y in range(reference_range.y.lower, reference_range.y.upper + 1)
            for x in range(reference_range.x.lower, reference_range.x.upper + 1)
        )
    points: list[tuple[int, int]] = []
    seen: set[tuple[int, int]] = set()
    index = 1
    while len(points) < min(cap, total):
        x = reference_range.x.lower + min(width - 1, int(_van_der_corput(index, 2) * width))
        y = reference_range.y.lower + min(height - 1, int(_van_der_corput(index, 3) * height))
        point = (x, y)
        if point not in seen:
            seen.add(point)
            points.append(point)
        index += 1
    return tuple(points)


def _row_grid_points(reference_range: Any, cap: int) -> tuple[tuple[int, int], ...]:
    """Deterministic bounded exhaustive scan used only after first-pass failure."""
    if cap <= 0:
        return ()
    points: list[tuple[int, int]] = []
    for y in range(reference_range.y.lower, reference_range.y.upper + 1):
        for x in range(reference_range.x.lower, reference_range.x.upper + 1):
            points.append((x, y))
            if len(points) >= cap:
                return tuple(points)
    return tuple(points)


def generate_position_candidates(
    block: BlockInfo,
    bay: BayInfo,
    orient: OrientationInfo,
    interval: tuple[int, int],
    state: IndexedSolutionState,
    current_position: tuple[int, int] | None = None,
) -> tuple[tuple[int, int], ...]:
    """Generate fit-safe wall/contact/rounded-vertex/lattice anchors.

    Only placements co-present in ``interval`` contribute anchors.  Fractional
    vertex contacts deliberately emit both floor and ceil integer translations.
    """
    reference_range = orient.integer_range(bay)
    if reference_range is None or state.anchor_cap <= 0:
        return ()
    kernel = getattr(state, "kernel", None)
    anchors: list[tuple[int, int]] = []
    if current_position is not None:
        anchors.append((int(current_position[0]), int(current_position[1])))
    anchors.extend([
        (reference_range.x.lower, reference_range.y.lower),
        (reference_range.x.upper, reference_range.y.lower),
        (reference_range.x.lower, reference_range.y.upper),
        (reference_range.x.upper, reference_range.y.upper),
    ])
    raw_cap = max(state.anchor_cap, state.anchor_cap * 8)

    def add_pairs(points: Iterable[tuple[int, int]]) -> None:
        for point in points:
            if len(anchors) >= raw_cap:
                return
            anchors.append(point)

    active = state.co_present(bay.index, interval)
    if kernel is not None:
        candidate_shape = kernel.shape(block.index, orient.index)
        candidate_vertices = _geometry_vertices(candidate_shape.union)
        for placement in active:
            ex0, ey0, ex1, ey1 = _world_aabb(kernel, placement)
            x_contacts = (ex0 - orient.xmax, ex1 - orient.xmin)
            y_contacts = (ey0 - orient.ymax, ey1 - orient.ymin)
            y_bases = (reference_range.y.lower, reference_range.y.upper, math.floor(ey0 - orient.ymin))
            x_bases = (reference_range.x.lower, reference_range.x.upper, math.floor(ex0 - orient.xmin))
            for x in x_contacts:
                for y in y_bases:
                    add_pairs(_integer_pair(x, y))
            for y in y_contacts:
                for x in x_bases:
                    add_pairs(_integer_pair(x, y))

            existing_vertices = _geometry_vertices(
                kernel.shape(placement.block_id, placement.orient_idx).union
            )
            # Align candidate vertices with existing vertical/horizontal edge
            # coordinates.  Floor and ceil are both retained for checker-integer
            # semantics; exact geometry later decides feasibility.
            for cx, cy in candidate_vertices[:32]:
                for ex, ey in existing_vertices[:32]:
                    add_pairs(
                        _integer_pair(ex + placement.x - cx, ey + placement.y - cy)
                    )
                    if len(anchors) >= raw_cap:
                        break
                if len(anchors) >= raw_cap:
                    break
            if len(anchors) >= raw_cap:
                break

    anchors.extend(_lattice_points(reference_range, state.anchor_cap))
    output: list[tuple[int, int]] = []
    seen: set[tuple[int, int]] = set()
    for x, y in anchors:
        point = (int(x), int(y))
        if point in seen:
            continue
        if not (
            reference_range.x.lower <= point[0] <= reference_range.x.upper
            and reference_range.y.lower <= point[1] <= reference_range.y.upper
        ):
            continue
        seen.add(point)
        output.append(point)
        if len(output) >= state.anchor_cap:
            break
    return tuple(output)


def _fragmentation(state: IndexedSolutionState, placement: Placement) -> int:
    intervals = sorted(
        state.bay_intervals(placement.bay_id) + (placement,),
        key=lambda item: (item.entry, item.exit),
    )
    components = 0
    right: int | None = None
    for item in intervals:
        if right is None or item.entry > right:
            components += 1
            right = item.exit
        else:
            right = max(right, item.exit)
    return max(0, components - 1)


def evaluate_insert(
    draft: IndexedSolutionState,
    placement: Placement,
    kernel: GeometryKernel,
) -> CandidateScore | None:
    """Evaluate an insertion without mutating the draft."""
    if placement.block_id in draft.block_ids or not kernel.fits(placement):
        return None
    try:
        for existing in draft.co_present(
            placement.bay_id, (placement.entry, placement.exit)
        ):
            if kernel.relation(placement, existing).state is not PairState.FREE:
                return None
    except GeometryError:
        return None
    block = draft.instance.block(placement.block_id)
    tardiness = float(max(0, placement.exit - block.due_date))
    assignment = draft.assignment_delta(placement.block_id, placement.bay_id)
    total = draft.instance.weights.w1 * tardiness + assignment.weighted
    fragmentation = _fragmentation(draft, placement)
    tie = (
        total,
        fragmentation,
        placement.exit,
        placement.entry,
        placement.bay_id,
        placement.orient_idx,
        placement.x,
        placement.y,
        placement.block_id,
    )
    return CandidateScore(
        placement=placement,
        total_delta=total,
        tardiness_delta=tardiness,
        assignment_delta=assignment.weighted,
        fragmentation=fragmentation,
        canonical_tie=tie,
        state_version=draft.version,
    )


def _profile_order(
    instance: Instance,
    kernel: GeometryKernel,
    seed: ConstructionSeed,
) -> tuple[int, ...]:
    assignment = seed.assignment_seed
    guide_priority = (
        {block_id: rank for rank, block_id in enumerate(assignment.priority)}
        if assignment is not None
        else {}
    )
    rng = random.Random(seed.random_seed)
    random_key = {block.index: rng.random() for block in instance.blocks}

    def union_pressure(block: BlockInfo) -> float:
        areas = [
            kernel.shape(block.index, orient_idx).union.area
            for _, orient_idx, _ in block.fitting_options
        ]
        return min(areas) * block.dwell

    def assignment_regret(block: BlockInfo) -> float:
        best_by_bay: dict[int, float] = {}
        preference_max = max(block.bay_preferences)
        for bay_id, _, _ in block.fitting_options:
            best_by_bay[bay_id] = preference_max - block.bay_preferences[bay_id]
        costs = sorted(best_by_bay.values())
        return (costs[1] - costs[0]) if len(costs) > 1 else math.inf

    def key(block: BlockInfo) -> tuple[Any, ...]:
        slack = block.due_date - block.release_time - block.dwell
        pressure = union_pressure(block)
        regret = assignment_regret(block)
        preference_pressure = (
            max(block.bay_preferences) - min(block.bay_preferences)
        ) / max(abs(instance.weights.w1), 1e-12)
        if seed.profile == "slack_due":
            return (slack, block.due_date, block.index)
        if seed.profile == "release_due":
            return (block.release_time, block.due_date, block.index)
        if seed.profile == "congestion":
            return (-pressure, slack, block.index)
        if seed.profile == "assignment_regret":
            return (-regret, slack, block.index)
        if seed.profile == "preference_pressure":
            return (-preference_pressure, slack, block.index)
        return (
            guide_priority.get(block.index, len(instance.blocks)),
            0.45 * random_key[block.index] + 0.35 * (slack / max(1, abs(block.due_date) + 1))
            - 0.20 * (pressure / max(1.0, instance.bays[0].area)),
            block.index,
        )

    return tuple(block.index for block in sorted(instance.blocks, key=key))


def _guides(seed: ConstructionSeed, block_id: int) -> tuple[int | None, int | None]:
    assignment = seed.assignment_seed
    if assignment is None:
        return None, None
    bay = assignment.bay_by_block[block_id]
    start = assignment.suggested_start[block_id]
    return int(bay), start


def _candidate_options(
    instance: Instance,
    kernel: GeometryKernel,
    state: IndexedSolutionState,
    block_id: int,
    seed: ConstructionSeed,
    config: ConstructorConfig,
    budget: Budget,
    counters: _Counters,
    current_placement: Placement | None = None,
) -> tuple[CandidateScore, ...]:
    block = instance.block(block_id)
    tail_reserve = _reserve_safe_tail(instance)
    guide_bay, guide_start = _guides(seed, block_id)
    if current_placement is not None:
        guide_bay = current_placement.bay_id
        guide_start = current_placement.entry
    fitting = sorted(
        block.fitting_options,
        key=lambda item: (
            item[0] != guide_bay if guide_bay is not None else False,
            max(block.bay_preferences) - block.bay_preferences[item[0]],
            item[0],
            item[1],
        ),
    )

    def search(times: tuple[int, ...], *, lattice_only: bool = False) -> list[CandidateScore]:
        found: list[CandidateScore] = []
        for bay_id, orient_idx, reference_range in fitting:
            found_before_option = len(found)
            if _stop_for_candidate_cap(config, counters) or _stop_for_safe_tail(
                budget, counters, tail_reserve
            ):
                break
            bay = instance.bay(bay_id)
            orient = block.orientations[orient_idx]
            for entry in times:
                if _stop_for_safe_tail(budget, counters, tail_reserve):
                    break
                interval = (entry, entry + block.dwell)
                if lattice_only:
                    positions = _row_grid_points(reference_range, config.lattice_cap)
                else:
                    current_position = None
                    if (
                        current_placement is not None
                        and current_placement.bay_id == bay_id
                        and current_placement.orient_idx == orient_idx
                    ):
                        current_position = (current_placement.x, current_placement.y)
                    positions = generate_position_candidates(
                        block,
                        bay,
                        orient,
                        interval,
                        state,
                        current_position=current_position,
                    )
                for position_index, (x, y) in enumerate(positions):
                    if _stop_for_candidate_cap(config, counters) or (
                        position_index % 16 == 0
                        and _stop_for_safe_tail(budget, counters, tail_reserve)
                    ):
                        break
                    counters.attempted += 1
                    placement = Placement(
                        block_id, bay_id, orient_idx, x, y, entry, entry + block.dwell
                    )
                    if not kernel.fits(placement):
                        counters.rejected_fit += 1
                        continue
                    score = evaluate_insert(state, placement, kernel)
                    if score is None:
                        counters.rejected_relation += 1
                        continue
                    guide_penalty = 0 if guide_bay is None or bay_id == guide_bay else 1
                    found.append(
                        CandidateScore(
                            placement=score.placement,
                            total_delta=score.total_delta,
                            tardiness_delta=score.tardiness_delta,
                            assignment_delta=score.assignment_delta,
                            fragmentation=score.fragmentation,
                            canonical_tie=(
                                score.total_delta,
                                score.fragmentation,
                                guide_penalty,
                            )
                            + score.canonical_tie[2:],
                            state_version=score.state_version,
                        )
                    )
                if len(found) - found_before_option >= 3:
                    break
                if counters.stop_requested:
                    break
            if current_placement is None and len(found) >= 3:
                break
            if counters.stop_requested:
                break
        return found

    first_times = generate_time_candidates(block, state, guide_start)
    found = search(first_times)
    if not found and not counters.stop_requested:
        counters.escalations += 1
        expanded = _time_candidates(block, state, guide_start, config.escalated_time_cap)
        found = search(expanded, lattice_only=True)
    return tuple(sorted(found, key=lambda item: item.canonical_tie)[:3])


def _exact_union_safe(
    state: IndexedSolutionState,
    placement: Placement,
    kernel: GeometryKernel,
) -> bool:
    if not kernel.fits(placement):
        return False
    for existing in state.co_present(placement.bay_id, (placement.entry, placement.exit)):
        if existing.block_id == placement.block_id:
            continue
        if kernel.relation(placement, existing).state is not PairState.FREE:
            return False
    return True


def _commit_score(
    state: IndexedSolutionState,
    score: CandidateScore,
    kernel: GeometryKernel,
) -> bool:
    refreshed = evaluate_insert(state, score.placement, kernel)
    if refreshed is None:
        return False
    return state.transactional_insert(
        refreshed.placement,
        lambda current, placement: _exact_union_safe(current, placement, kernel),
    )


def generate_insertion_candidates(
    state: IndexedSolutionState,
    block_id: int,
    kernel: GeometryKernel,
    budget: Budget,
    *,
    current_placement: Placement | None = None,
    config: ConstructorConfig | None = None,
) -> tuple[CandidateScore, ...]:
    """Public bounded insertion API used by transactional repair engines.

    The candidate's current bay/time/position is included as a deterministic
    anchor when supplied.  The input state is never mutated.
    """
    if block_id in state.block_ids:
        raise ValueError(f"block {block_id} is already present in the repair state")
    if current_placement is not None and current_placement.block_id != block_id:
        raise ValueError("current_placement belongs to a different block")
    config = config or ConstructorConfig(max_profiles=1)
    previous_kernel = state.kernel
    state.kernel = kernel
    try:
        counters = _Counters()
        seed = ConstructionSeed(None, "slack_due", config.seed)
        options = _candidate_options(
            state.instance,
            kernel,
            state,
            block_id,
            seed,
            config,
            budget,
            counters,
            current_placement=current_placement,
        )
        if current_placement is None:
            return options
        current_score = evaluate_insert(state, current_placement, kernel)
        if current_score is None or any(
            item.placement == current_placement for item in options
        ):
            return options
        # Preserve an exact rollback column even when cheaper alternatives fill
        # the bounded top-three list.
        return tuple(options[:2]) + (current_score,)
    finally:
        state.kernel = previous_kernel


def commit_insertion_candidate(
    state: IndexedSolutionState,
    candidate: CandidateScore,
    kernel: GeometryKernel,
) -> bool:
    """Re-evaluate and atomically commit one generated insertion candidate."""
    return _commit_score(state, candidate, kernel)


def select_regret(
    options_by_block: dict[int, Iterable[CandidateScore]],
) -> int | None:
    """Select maximum regret-2, using the best canonical candidate as tie-break."""
    choices: list[tuple[Any, int]] = []
    for block_id, raw_options in options_by_block.items():
        options = sorted(tuple(raw_options), key=lambda item: item.canonical_tie)
        if not options:
            continue
        regret = (
            options[1].total_delta - options[0].total_delta
            if len(options) > 1
            else math.inf
        )
        choices.append(((-regret, options[0].canonical_tie, block_id), block_id))
    return min(choices, key=lambda item: item[0])[1] if choices else None


def _fallback_insert(
    instance: Instance,
    kernel: GeometryKernel,
    state: IndexedSolutionState,
    block_id: int,
    seed: ConstructionSeed,
) -> None:
    block = instance.block(block_id)
    guide_bay, _ = _guides(seed, block_id)
    candidates: list[tuple[Any, Placement]] = []
    for bay_id, orient_idx, reference_range in block.fitting_options:
        entry = state.earliest_empty_window(bay_id, block.release_time, block.dwell)
        x, y = reference_range.anchor
        placement = Placement(
            block_id, bay_id, orient_idx, x, y, entry, entry + block.dwell
        )
        delta = state.assignment_delta(block_id, bay_id)
        tardiness = max(0, placement.exit - block.due_date)
        candidates.append(
            (
                (
                    entry,
                    instance.weights.w1 * tardiness + delta.weighted,
                    bay_id != guide_bay if guide_bay is not None else False,
                    bay_id,
                    orient_idx,
                ),
                placement,
            )
        )
    _, placement = min(candidates, key=lambda item: item[0])
    if not state.transactional_insert(
        placement, lambda current, item: _exact_union_safe(current, item, kernel)
    ):
        raise RuntimeError(f"serial fallback rejected block {block_id}")


def _all_pairs_free(snapshot: SolutionSnapshot, kernel: GeometryKernel) -> bool:
    for left, right in combinations(snapshot.placements, 2):
        if left.bay_id == right.bay_id and _overlaps(left, right):
            try:
                if kernel.relation(left, right).state is not PairState.FREE:
                    return False
            except GeometryError:
                return False
    return True


def construct_complete(
    instance: Instance,
    kernel: GeometryKernel,
    seed: ConstructionSeed,
    budget: Budget,
    config: ConstructorConfig | None = None,
) -> ConstructionResult:
    """Build one complete candidate; no checker-validity claim is made here."""
    config = config or ConstructorConfig()
    started = time.monotonic()
    counters = _Counters()
    state = IndexedSolutionState(
        instance,
        time_cap=config.time_cap,
        anchor_cap=config.anchor_cap,
        lattice_cap=config.lattice_cap,
    )
    state.kernel = kernel
    order = _profile_order(instance, kernel, seed)
    rank = {block_id: index for index, block_id in enumerate(order)}
    unplaced = set(order)
    try:
        while unplaced:
            if _stop_for_candidate_cap(config, counters) or _stop_for_safe_tail(
                budget, counters, _reserve_safe_tail(instance)
            ):
                break
            choices: list[tuple[Any, int, CandidateScore]] = []
            for block_id in sorted(unplaced, key=lambda item: rank[item]):
                options = _candidate_options(
                    instance, kernel, state, block_id, seed, config, budget, counters
                )
                if not options:
                    continue
                regret = (
                    options[1].total_delta - options[0].total_delta
                    if len(options) > 1
                    else math.inf
                )
                choices.append(((-regret, rank[block_id], options[0].canonical_tie), block_id, options[0]))
                if counters.stop_requested:
                    break
            if counters.stop_requested:
                break
            if not choices:
                block_id = min(unplaced, key=lambda item: rank[item])
                _fallback_insert(instance, kernel, state, block_id, seed)
                counters.fallback_count += 1
                counters.committed += 1
                unplaced.remove(block_id)
                continue
            _, block_id, score = min(choices, key=lambda item: item[0])
            if not _commit_score(state, score, kernel):
                _fallback_insert(instance, kernel, state, block_id, seed)
                counters.fallback_count += 1
            counters.committed += 1
            unplaced.remove(block_id)
    except Exception:
        # Geometry failures are candidate-local.  A deterministic serial tail
        # is still safe because every new interval is empty in its bay.
        counters.deadline_hit = counters.deadline_hit or budget.remaining() <= 0.0

    for block_id in sorted(unplaced, key=lambda item: rank[item]):
        _fallback_insert(instance, kernel, state, block_id, seed)
        counters.fallback_count += 1
        counters.committed += 1

    snapshot = state.freeze()
    complete = (
        len(snapshot.placements) == len(instance.blocks)
        and {item.block_id for item in snapshot.placements} == set(range(len(instance.blocks)))
        and _all_pairs_free(snapshot, kernel)
    )
    if complete:
        snapshot = snapshot.with_objective(compute_objective(instance, snapshot))
    metrics = ConstructionMetrics(
        profile=seed.profile,
        random_seed=seed.random_seed,
        time_cap=config.time_cap,
        anchor_cap=config.anchor_cap,
        lattice_cap=config.lattice_cap,
        candidates_attempted=counters.attempted,
        rejected_fit=counters.rejected_fit,
        rejected_aabb=counters.rejected_aabb,
        rejected_relation=counters.rejected_relation,
        escalations=counters.escalations,
        fallback_count=counters.fallback_count,
        placements_committed=counters.committed,
        candidate_cap=config.max_candidate_attempts,
        candidate_cap_exhausted=counters.candidate_cap_exhausted,
        timebox_exhausted=counters.timebox_exhausted,
        deadline_hit=counters.deadline_hit,
        construction_time=time.monotonic() - started,
    )
    status = "COMPLETE" if complete and not counters.fallback_count else "FALLBACK_COMPLETE"
    if not complete:
        status = "INCOMPLETE"
    return ConstructionResult(snapshot=snapshot if complete else None, status=status, metrics=metrics, complete=complete)


def construction_seeds(portfolio: Any | None, config: ConstructorConfig) -> tuple[ConstructionSeed, ...]:
    assignment_seeds = tuple(getattr(portfolio, "seeds", ()) or ())
    seeds: list[ConstructionSeed] = []
    for index, profile in enumerate(PROFILES[: config.max_profiles]):
        assignment = assignment_seeds[index % len(assignment_seeds)] if assignment_seeds else None
        seeds.append(ConstructionSeed(assignment, profile, config.seed + index))
    return tuple(seeds)


def construct_portfolio(
    instance: Instance,
    kernel: GeometryKernel,
    portfolio: Any | None,
    budget: Budget,
    config: ConstructorConfig | None = None,
) -> tuple[ConstructionResult, ...]:
    config = config or ConstructorConfig()
    if config.max_profiles == 0:
        return ()
    profile_limit = 1 if budget.limit < 12 else 4 if budget.limit < 60 else config.max_profiles
    seeds = construction_seeds(portfolio, config)[:profile_limit]
    cap_fraction = 0.4 if config.max_candidate_attempts is not None else 0.12
    total_cap = min(24.0, cap_fraction * budget.limit, budget.search_remaining())
    results: list[ConstructionResult] = []
    for index, seed in enumerate(seeds):
        remaining_profiles = len(seeds) - index
        child = budget.child(max(0.0, total_cap / max(1, remaining_profiles)))
        result = construct_complete(instance, kernel, seed, child, config)
        results.append(result)
        total_cap = max(0.0, total_cap - result.metrics.construction_time)
    return tuple(results)
