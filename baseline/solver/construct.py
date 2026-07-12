"""Shared non-interlock event-time insertion for the S1 constructor."""

from __future__ import annotations

from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass
import hashlib
import json
import math
import time

from numpy.random import Generator, PCG64

from .assign import AssignmentResult, AssignmentV1
from .budget import Budget, BudgetExpired
from .checker_adapter import CheckerResult
from .geometry import GeomKernel, ShapeInfo
from .incumbent import VerifiedIncumbent
from .instance import ProblemInstance
from .serialize import serialize_non_interlock
from .state import Placement, SolutionState
from .validate import validate_pair, validate_unary


DEFAULT_TIME_CAP = 8
ESCALATED_TIME_CAP = 16
DEFAULT_ANCHOR_CAP = 32
ESCALATED_ANCHOR_CAP = 64
DETERMINISTIC_PROFILES = ("PF1", "PF2", "PF3", "PF4")
BIASED_SELECTION_PROBABILITY = 0.25


@dataclass(frozen=True, slots=True)
class ProfileMetrics:
    """Deterministic construction facts for one isolated profile start."""

    profile: str
    base_profile: str
    biased: bool
    order: tuple[int, ...]
    placed: int
    fallback_count: int
    fallback_reasons: tuple[tuple[str, int], ...]
    attempt_counts: tuple[tuple[str, int], ...]
    z1: float
    z2: float
    z3: float
    objective: float
    exact_predicates: int


@dataclass(frozen=True, slots=True)
class ConstructionMetrics:
    """Replay-stable multi-start metrics excluding clocks and timestamps."""

    seed: int
    time_cap: int | None
    anchor_cap: int | None
    profile_budget: tuple[str, ...]
    start_profiles: tuple[str, ...]
    selected_profile: str
    selected_order: tuple[int, ...]
    profile_metrics: tuple[ProfileMetrics, ...]
    output_sha256: str
    incumbent_updated: bool


@dataclass(frozen=True, slots=True)
class ProfileConstruction:
    """One complete independently allocated constructor state."""

    state: SolutionState
    metrics: ProfileMetrics


@dataclass(frozen=True, slots=True)
class ConstructionResult:
    """The internally best start and its single official-check result."""

    state: SolutionState
    metrics: ConstructionMetrics
    checker_result: CheckerResult
    construction_seconds: float
    full_check_seconds: float


@dataclass(frozen=True, slots=True)
class InsertionCandidate:
    """A validated fixed-site insertion and its deterministic ranking values."""

    placement: Placement
    weighted_tardiness: float
    assignment_cost: float
    score: tuple[float, float, int, int, int, int, int, int]


@dataclass(frozen=True, slots=True)
class EscalationResult:
    """A guaranteed fit-qualified insertion plus its escalation provenance."""

    candidate: InsertionCandidate
    attempt: str
    fallback_reason: str | None
    attempted_bays: tuple[int, ...]
    time_cap: int | None
    anchor_cap: int | None


def construct_profile(
    instance: ProblemInstance,
    assignment: AssignmentResult,
    profile: str,
    *,
    order: Sequence[int] | None = None,
    base_profile: str | None = None,
    biased: bool = False,
    shape_catalog: tuple[tuple[ShapeInfo, ...], ...] | None = None,
    geom: GeomKernel | None = None,
    budget: Budget | None = None,
    time_cap: int | None = ESCALATED_TIME_CAP,
    anchor_cap: int | None = ESCALATED_ANCHOR_CAP,
) -> ProfileConstruction:
    """Construct one full state through the shared escalation insertion path."""
    canonical_profile = profile.upper()
    base = (base_profile or canonical_profile).upper()
    if base not in DETERMINISTIC_PROFILES:
        raise ValueError(f"unsupported construction profile: {base_profile or profile}")
    state = SolutionState(instance, shape_catalog=shape_catalog, geom=geom)
    exact_predicates_before = state.geom.stats.exact_predicates
    selected_order = (
        _profile_order(instance, assignment, base, state.shape_catalog)
        if order is None
        else tuple(order)
    )
    expected = set(range(len(instance.blocks)))
    if len(selected_order) != len(expected) or set(selected_order) != expected:
        raise ValueError("construction order must contain every block exactly once")

    attempts: Counter[str] = Counter()
    fallback_reasons: Counter[str] = Counter()
    for block_id in selected_order:
        if budget is not None:
            budget.checkpoint(f"constructor {canonical_profile} block {block_id}")
        preferred = assignment.assignments[block_id]
        inserted = escalate_insert(
            state,
            block_id,
            preferred_bay_id=preferred.bay_id,
            preferred_orient_idx=preferred.orient_idx,
            time_cap=time_cap,
            anchor_cap=anchor_cap,
        )
        state.place(inserted.candidate.placement)
        attempts[inserted.attempt] += 1
        if inserted.fallback_reason is not None:
            fallback_reasons[inserted.fallback_reason] += 1
    state.assert_invariants()
    diagnostics = state.objective_diagnostics
    return ProfileConstruction(
        state=state,
        metrics=ProfileMetrics(
            profile=canonical_profile,
            base_profile=base,
            biased=biased,
            order=selected_order,
            placed=len(state.placements),
            fallback_count=sum(fallback_reasons.values()),
            fallback_reasons=tuple(sorted(fallback_reasons.items())),
            attempt_counts=tuple(sorted(attempts.items())),
            z1=diagnostics.z1,
            z2=diagnostics.z2,
            z3=diagnostics.z3,
            objective=diagnostics.objective,
            exact_predicates=(
                state.geom.stats.exact_predicates - exact_predicates_before
            ),
        ),
    )


def construct_multistart(
    instance: ProblemInstance,
    incumbent: VerifiedIncumbent,
    budget: Budget,
    *,
    seed: int,
    profiles: Sequence[str] = DETERMINISTIC_PROFILES,
    biased_variants: bool = True,
    calibrated_entry: bool = False,
    time_cap: int | None = ESCALATED_TIME_CAP,
    anchor_cap: int | None = ESCALATED_ANCHOR_CAP,
) -> ConstructionResult:
    """Run deterministic starts, then budgeted PCG64-biased variants.

    Each start owns a fresh ``SolutionState``.  No start is full-checked while
    ranking; after every planned start succeeds, only the internal best is
    passed to ``VerifiedIncumbent.try_update``.  Thus an exception in any
    profile cannot alter the prior verified incumbent.
    """
    normalized = tuple(str(profile).upper() for profile in profiles)
    if not calibrated_entry and normalized != DETERMINISTIC_PROFILES:
        raise ValueError("S1-04 requires deterministic profiles PF1,PF2,PF3,PF4")
    if calibrated_entry and (
        not normalized
        or any(profile not in DETERMINISTIC_PROFILES for profile in normalized)
        or biased_variants
    ):
        raise ValueError(
            "calibrated entry requires a non-empty deterministic profile subset "
            "and no biased variants"
        )
    if isinstance(seed, bool) or not isinstance(seed, int):
        raise ValueError("seed must be an integer")
    if not incumbent.has_incumbent:
        raise ValueError("multi-start requires a prior checker-verified incumbent")

    construction_started = time.monotonic()
    assignment = AssignmentV1(instance).assign()
    shape_catalog = SolutionState(instance).shape_catalog
    geom = GeomKernel()
    starts: list[ProfileConstruction] = []
    for profile in normalized:
        starts.append(
            construct_profile(
                instance,
                assignment,
                profile,
                shape_catalog=shape_catalog,
                geom=geom,
                budget=budget if calibrated_entry else None,
                time_cap=time_cap,
                anchor_cap=anchor_cap,
            )
        )

    if biased_variants:
        rng = Generator(PCG64(seed))
        for profile in normalized:
            try:
                budget.checkpoint(f"constructor {profile} biased")
            except BudgetExpired:
                break
            base_order = _profile_order(
                instance, assignment, profile, shape_catalog
            )
            biased_order = _biased_order(base_order, rng)
            try:
                starts.append(
                    construct_profile(
                        instance,
                        assignment,
                        f"{profile}-BIASED",
                        order=biased_order,
                        base_profile=profile,
                        biased=True,
                        shape_catalog=shape_catalog,
                        geom=geom,
                        budget=budget,
                        time_cap=time_cap,
                        anchor_cap=anchor_cap,
                    )
                )
            except BudgetExpired:
                break

    _, best = min(
        enumerate(starts),
        key=lambda item: (item[1].state.objective, item[0]),
    )
    output = serialize_non_interlock(best.state.placements.values())
    output_sha256 = hashlib.sha256(
        json.dumps(output, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    construction_seconds = time.monotonic() - construction_started
    full_check_started = time.monotonic()
    updated = incumbent.try_update(best.state)
    full_check_seconds = time.monotonic() - full_check_started
    checked = incumbent.last_checker_result
    metrics = ConstructionMetrics(
        seed=seed,
        time_cap=time_cap,
        anchor_cap=anchor_cap,
        profile_budget=normalized,
        start_profiles=tuple(start.metrics.profile for start in starts),
        selected_profile=best.metrics.profile,
        selected_order=best.metrics.order,
        profile_metrics=tuple(start.metrics for start in starts),
        output_sha256=output_sha256,
        incumbent_updated=updated,
    )
    return ConstructionResult(
        best.state,
        metrics,
        checked,
        construction_seconds,
        full_check_seconds,
    )


def _profile_order(
    instance: ProblemInstance,
    assignment: AssignmentResult,
    profile: str,
    shape_catalog: tuple[tuple[ShapeInfo, ...], ...],
) -> tuple[int, ...]:
    def values(block_id: int) -> tuple[float | int, ...]:
        block = instance.blocks[block_id]
        chosen = assignment.assignments[block_id]
        area_time = (
            shape_catalog[block_id][chosen.orient_idx].area
            * block.dwell
        )
        slack = block.due_date - block.release_time - block.dwell
        if profile == "PF1":
            return slack, block.due_date, -area_time, block_id
        if profile == "PF2":
            return block.due_date, -block.dwell, block_id
        if profile == "PF3":
            return -area_time, slack, block_id
        if profile == "PF4":
            return block.release_time, block.due_date, block_id
        raise ValueError(f"unsupported construction profile: {profile}")

    return tuple(sorted(range(len(instance.blocks)), key=values))


def _biased_order(order: Sequence[int], rng: Generator) -> tuple[int, ...]:
    remaining = list(order)
    selected: list[int] = []
    denominator = math.log1p(-BIASED_SELECTION_PROBABILITY)
    while remaining:
        draw = max(float(rng.random()), float.fromhex("0x1.0p-1022"))
        index = min(int(math.floor(math.log(draw) / denominator)), len(remaining) - 1)
        selected.append(remaining.pop(index))
    return tuple(selected)


def anchor_candidates(
    state: SolutionState,
    block_id: int,
    bay_id: int,
    orient_idx: int,
    *,
    cap: int | None = DEFAULT_ANCHOR_CAP,
) -> tuple[tuple[int, int], ...]:
    """Return clipped integer wall/right/top AABB anchors in y-major order."""
    _validate_site_ids(state, block_id, bay_id, orient_idx)
    if cap is not None and (not _plain_int(cap) or cap <= 0):
        raise ValueError("cap must be a positive integer or None")

    instance = state.instance
    orientation = instance.blocks[block_id].orientations[orient_idx]
    ranges = orientation.integer_position_ranges(instance.bays[bay_id])
    if ranges is None:
        return ()
    x_range, y_range = ranges
    shape = state.orientation_shape(block_id, orient_idx)

    x_values = {x_range.start, x_range.stop - 1}
    y_values = {y_range.start, y_range.stop - 1}
    for placed in state.placements.values():
        if placed.block_id == block_id or placed.bay_id != bay_id:
            continue
        existing = state.shape_info(placed)
        right_contact = _integer_coordinate(
            placed.x + existing.aabb[2] - shape.aabb[0]
        )
        top_contact = _integer_coordinate(
            placed.y + existing.aabb[3] - shape.aabb[1]
        )
        if right_contact is not None and right_contact in x_range:
            x_values.add(right_contact)
        if top_contact is not None and top_contact in y_range:
            y_values.add(top_contact)

    ordered = tuple(
        (x, y)
        for y in sorted(y_values)
        for x in sorted(x_values)
        if x in x_range and y in y_range
    )
    return ordered if cap is None else ordered[:cap]


def first_fit(
    state: SolutionState,
    block_id: int,
    bay_id: int,
    orient_idx: int,
    *,
    time_cap: int | None = DEFAULT_TIME_CAP,
    anchor_cap: int | None = DEFAULT_ANCHOR_CAP,
) -> InsertionCandidate | None:
    """Return the first y-major anchor with a checker-parity event insertion."""
    times = time_candidates(state, block_id, bay_id, cap=time_cap)
    assignment_cost = _assignment_cost(state, block_id, bay_id)
    for x, y in anchor_candidates(
        state,
        block_id,
        bay_id,
        orient_idx,
        cap=anchor_cap,
    ):
        for entry in times:
            candidate = _candidate_at(
                state,
                block_id,
                bay_id,
                orient_idx,
                x=x,
                y=y,
                entry=entry,
                assignment_cost=assignment_cost,
            )
            if candidate is not None:
                return candidate
    return None


def escalate_insert(
    state: SolutionState,
    block_id: int,
    *,
    preferred_bay_id: int,
    preferred_orient_idx: int,
    time_cap: int | None = ESCALATED_TIME_CAP,
    anchor_cap: int | None = ESCALATED_ANCHOR_CAP,
) -> EscalationResult:
    """Run the bounded S1 insertion ladder and end in a valid solo window.

    The ladder is constructor-local: preferred 8/32, preferred calibrated
    caps, every fitting bay/orientation at calibrated caps, two explicit
    tardy probes, and an empty-bay time window after all residents have
    exited.  No interlock API is consulted.
    """
    if not _plain_int(block_id) or not 0 <= block_id < len(state.instance.blocks):
        raise IndexError(f"block_id {block_id!r} is out of range")

    attempted_bays: list[int] = []
    preferred_valid = _site_ids_valid(
        state,
        block_id,
        preferred_bay_id,
        preferred_orient_idx,
    )
    if preferred_valid:
        attempted_bays.append(preferred_bay_id)
        candidate = first_fit(
            state,
            block_id,
            preferred_bay_id,
            preferred_orient_idx,
        )
        if candidate is not None:
            return EscalationResult(
                candidate, "preferred_bounded", None,
                tuple(attempted_bays), DEFAULT_TIME_CAP, DEFAULT_ANCHOR_CAP,
            )
        if (time_cap, anchor_cap) != (DEFAULT_TIME_CAP, DEFAULT_ANCHOR_CAP):
            candidate = first_fit(
                state,
                block_id,
                preferred_bay_id,
                preferred_orient_idx,
                time_cap=time_cap,
                anchor_cap=anchor_cap,
            )
            if candidate is not None:
                return EscalationResult(
                    candidate, "escalated_caps", "bounded_caps_exhausted",
                    tuple(attempted_bays), time_cap, anchor_cap,
                )

    fitting_sites = _fitting_sites(state, block_id)
    for bay_id, orient_idx in fitting_sites:
        if bay_id not in attempted_bays:
            attempted_bays.append(bay_id)
        if preferred_valid and (bay_id, orient_idx) == (
            preferred_bay_id,
            preferred_orient_idx,
        ):
            continue
        candidate = first_fit(
            state,
            block_id,
            bay_id,
            orient_idx,
            time_cap=time_cap,
            anchor_cap=anchor_cap,
        )
        if candidate is not None:
            return EscalationResult(
                candidate, "all_fitting_bays", "preferred_site_failed",
                tuple(attempted_bays), time_cap, anchor_cap,
            )

    block = state.instance.blocks[block_id]
    tardy_entries = tuple(dict.fromkeys((
        max(block.release_time, block.due_date),
        max(block.release_time, block.due_date + block.dwell),
    )))
    for bay_id, orient_idx in fitting_sites:
        for x, y in anchor_candidates(
            state,
            block_id,
            bay_id,
            orient_idx,
            cap=anchor_cap,
        ):
            for entry in tardy_entries:
                candidate = _candidate_at(
                    state, block_id, bay_id, orient_idx, x=x, y=y, entry=entry
                )
                if candidate is not None:
                    return EscalationResult(
                        candidate, "tardy_expansion", "bounded_search_failed",
                        tuple(attempted_bays), None, anchor_cap,
                    )

    solo_candidates: list[InsertionCandidate] = []
    for bay_id, orient_idx in fitting_sites:
        anchors = anchor_candidates(
            state, block_id, bay_id, orient_idx, cap=1
        )
        if not anchors:
            continue
        entry = max(
            block.release_time,
            max(
                (
                    placed.exit
                    for placed in state.placements.values()
                    if placed.block_id != block_id and placed.bay_id == bay_id
                ),
                default=block.release_time,
            ),
        )
        candidate = _candidate_at(
            state,
            block_id,
            bay_id,
            orient_idx,
            x=anchors[0][0],
            y=anchors[0][1],
            entry=entry,
        )
        if candidate is not None:
            solo_candidates.append(candidate)
    if solo_candidates:
        selected = min(solo_candidates, key=lambda item: item.score)
        return EscalationResult(
            selected, "solo_window", "solo_window", tuple(attempted_bays), None, 1
        )
    raise AssertionError(
        f"fit-qualified block {block_id} had no validated empty-bay solo placement"
    )


def time_candidates(
    state: SolutionState,
    block_id: int,
    bay_id: int,
    *,
    cap: int | None = DEFAULT_TIME_CAP,
) -> tuple[int, ...]:
    """Return sorted event-boundary entry times for one block and bay.

    The optional cap is a heuristic work limit, not a completeness claim.  A
    later escalation may call this function with a larger cap or ``None``.
    """
    if not _plain_int(block_id) or not 0 <= block_id < len(state.instance.blocks):
        raise IndexError(f"block_id {block_id!r} is out of range")
    if not _plain_int(bay_id) or not 0 <= bay_id < len(state.instance.bays):
        raise IndexError(f"bay_id {bay_id!r} is out of range")
    if cap is not None and (not _plain_int(cap) or cap <= 0):
        raise ValueError("cap must be a positive integer or None")

    block = state.instance.blocks[block_id]
    events = {block.release_time}
    for placed in state.placements.values():
        if placed.block_id == block_id or placed.bay_id != bay_id:
            continue
        if _plain_int(placed.exit):
            events.add(placed.exit)
        if _plain_int(placed.entry):
            events.add(placed.entry - block.dwell)

    ordered = tuple(sorted(time for time in events if time >= block.release_time))
    return ordered if cap is None else ordered[:cap]


def insert_block(
    state: SolutionState,
    block_id: int,
    bay_id: int | None = None,
    orient_idx: int | None = None,
    *,
    x: int | None = None,
    y: int | None = None,
    time_cap: int | None = DEFAULT_TIME_CAP,
    bays_try: Sequence[int] | None = None,
    preferred_orient_idx: int | None = None,
    anchor_cap: int | None = DEFAULT_ANCHOR_CAP,
) -> InsertionCandidate | None:
    """Propose a validated fixed-site or restricted-bay insertion.

    Existing constructor callers provide a fixed bay/orientation/site.  S3
    repair callers instead provide ``bays_try``; every anchor and orientation
    considered then belongs to exactly those bays.  The function never mutates
    state and returns only a candidate accepted by the S0 exact validators.
    """
    if bays_try is not None:
        if any(value is not None for value in (bay_id, orient_idx, x, y)):
            raise ValueError(
                "restricted-bay insertion cannot also specify a fixed site"
            )
        bays = tuple(bays_try)
        if not bays or len(set(bays)) != len(bays):
            raise ValueError("bays_try must contain unique bay IDs")
        candidates: list[InsertionCandidate] = []
        for candidate_bay in bays:
            if not _plain_int(candidate_bay) or not 0 <= candidate_bay < len(
                state.instance.bays
            ):
                raise IndexError(f"bay_id {candidate_bay!r} is out of range")
            orientations = list(
                state.instance.fitting_orientations(block_id, candidate_bay)
            )
            if preferred_orient_idx in orientations:
                orientations.remove(preferred_orient_idx)
                orientations.insert(0, preferred_orient_idx)
            for candidate_orient in orientations:
                proposed = first_fit(
                    state,
                    block_id,
                    candidate_bay,
                    candidate_orient,
                    time_cap=time_cap,
                    anchor_cap=anchor_cap,
                )
                if proposed is not None:
                    candidates.append(proposed)
        return min(candidates, key=lambda candidate: candidate.score, default=None)

    if None in (bay_id, orient_idx, x, y):
        raise ValueError(
            "fixed-site insertion requires bay_id, orient_idx, x, and y"
        )
    assignment_cost = _assignment_cost(state, block_id, bay_id)
    for entry in time_candidates(state, block_id, bay_id, cap=time_cap):
        candidate = _candidate_at(
            state,
            block_id,
            bay_id,
            orient_idx,
            x=x,
            y=y,
            entry=entry,
            assignment_cost=assignment_cost,
        )
        if candidate is not None:
            return candidate
    return None


def _candidate_at(
    state: SolutionState,
    block_id: int,
    bay_id: int,
    orient_idx: int,
    *,
    x: int,
    y: int,
    entry: int,
    assignment_cost: float | None = None,
) -> InsertionCandidate | None:
    block = state.instance.blocks[block_id]
    placement = Placement(
        block_id=block_id,
        bay_id=bay_id,
        x=x,
        y=y,
        orient_idx=orient_idx,
        entry=entry,
        exit=entry + block.dwell,
    )
    if not _validate_constructor_insertion(state, placement):
        return None
    cost = (
        _assignment_cost(state, block_id, bay_id)
        if assignment_cost is None
        else assignment_cost
    )
    weighted_tardiness = state.instance.weights[0] * max(
        0.0, float(placement.exit - block.due_date)
    )
    return InsertionCandidate(
        placement=placement,
        weighted_tardiness=weighted_tardiness,
        assignment_cost=cost,
        score=(
            weighted_tardiness,
            cost,
            block_id,
            bay_id,
            orient_idx,
            x,
            y,
            entry,
        ),
    )


def _validate_constructor_insertion(
    state: SolutionState,
    candidate: Placement,
) -> bool:
    """Use S0 exact validators only for pairs that can share bay and time."""
    if not validate_unary(state, candidate):
        return False
    for other in state.placements.values():
        if other.block_id == candidate.block_id or other.bay_id != candidate.bay_id:
            continue
        if other.exit <= candidate.entry or candidate.exit <= other.entry:
            continue
        if not validate_pair(state, candidate, other):
            return False
    return True


def _assignment_cost(state: SolutionState, block_id: int, bay_id: int) -> float:
    if not _plain_int(block_id) or not 0 <= block_id < len(state.instance.blocks):
        raise IndexError(f"block_id {block_id!r} is out of range")
    if not _plain_int(bay_id) or not 0 <= bay_id < len(state.instance.bays):
        raise IndexError(f"bay_id {bay_id!r} is out of range")

    instance = state.instance
    block = instance.blocks[block_id]
    loads = list(state.objective_diagnostics.bay_loads)
    existing = state.get(block_id)
    if existing is not None and 0 <= existing.bay_id < len(loads):
        loads[existing.bay_id] -= block.workload
    before = _load_range(state, loads)
    loads[bay_id] += block.workload
    after = _load_range(state, loads)
    preference_loss = max(block.bay_preferences) - block.bay_preferences[bay_id]
    _, w2, w3 = instance.weights
    return w2 * (after - before) + w3 * preference_loss


def _load_range(state: SolutionState, loads: list[float]) -> float:
    bays = state.instance.bays
    if len(bays) < 2:
        return 0.0
    average_area = sum(bay.area for bay in bays) / len(bays)
    normalized = tuple(
        average_area / bay.area * load
        for bay, load in zip(bays, loads, strict=True)
    )
    return max(normalized) - min(normalized)


def _plain_int(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _integer_coordinate(value: float) -> int | None:
    numeric = float(value)
    return int(numeric) if numeric.is_integer() else None


def _validate_site_ids(
    state: SolutionState,
    block_id: int,
    bay_id: int,
    orient_idx: int,
) -> None:
    if not _site_ids_valid(state, block_id, bay_id, orient_idx):
        raise IndexError(
            f"invalid constructor site block={block_id!r} bay={bay_id!r} "
            f"orientation={orient_idx!r}"
        )


def _site_ids_valid(
    state: SolutionState,
    block_id: int,
    bay_id: int,
    orient_idx: int,
) -> bool:
    return (
        _plain_int(block_id)
        and 0 <= block_id < len(state.instance.blocks)
        and _plain_int(bay_id)
        and 0 <= bay_id < len(state.instance.bays)
        and _plain_int(orient_idx)
        and 0 <= orient_idx < len(state.instance.blocks[block_id].orientations)
    )


def _fitting_sites(
    state: SolutionState,
    block_id: int,
) -> tuple[tuple[int, int], ...]:
    sites = tuple(
        (bay.bay_id, orient_idx)
        for bay in state.instance.bays
        for orient_idx in state.instance.fitting_orientations(block_id, bay.bay_id)
    )
    if not sites:
        raise AssertionError(f"fit-qualified block {block_id} has no fitting site")
    return sites
