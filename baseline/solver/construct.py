"""Shared non-interlock event-time insertion for the S1 constructor."""

from __future__ import annotations

from dataclasses import dataclass

from .state import Placement, SolutionState
from .validate import validate_insertion


DEFAULT_TIME_CAP = 8
ESCALATED_TIME_CAP = 16
DEFAULT_ANCHOR_CAP = 32
ESCALATED_ANCHOR_CAP = 64


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
    for x, y in anchor_candidates(
        state,
        block_id,
        bay_id,
        orient_idx,
        cap=anchor_cap,
    ):
        candidate = insert_block(
            state,
            block_id,
            bay_id,
            orient_idx,
            x=x,
            y=y,
            time_cap=time_cap,
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
) -> EscalationResult:
    """Run the bounded S1 insertion ladder and end in a valid solo window.

    The ladder is constructor-local: preferred 8/32, preferred 16/64,
    every fitting bay/orientation at 16/64, two explicit tardy probes, and an
    empty-bay time window after all residents have exited.  No interlock API
    is consulted.
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
        candidate = first_fit(
            state,
            block_id,
            preferred_bay_id,
            preferred_orient_idx,
            time_cap=ESCALATED_TIME_CAP,
            anchor_cap=ESCALATED_ANCHOR_CAP,
        )
        if candidate is not None:
            return EscalationResult(
                candidate, "escalated_caps", "bounded_caps_exhausted",
                tuple(attempted_bays), ESCALATED_TIME_CAP, ESCALATED_ANCHOR_CAP,
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
            time_cap=ESCALATED_TIME_CAP,
            anchor_cap=ESCALATED_ANCHOR_CAP,
        )
        if candidate is not None:
            return EscalationResult(
                candidate, "all_fitting_bays", "preferred_site_failed",
                tuple(attempted_bays), ESCALATED_TIME_CAP, ESCALATED_ANCHOR_CAP,
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
            cap=ESCALATED_ANCHOR_CAP,
        ):
            for entry in tardy_entries:
                candidate = _candidate_at(
                    state, block_id, bay_id, orient_idx, x=x, y=y, entry=entry
                )
                if candidate is not None:
                    return EscalationResult(
                        candidate, "tardy_expansion", "bounded_search_failed",
                        tuple(attempted_bays), None, ESCALATED_ANCHOR_CAP,
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
    bay_id: int,
    orient_idx: int,
    *,
    x: int,
    y: int,
    time_cap: int | None = DEFAULT_TIME_CAP,
) -> InsertionCandidate | None:
    """Select the earliest validated event time for one fixed bay/orientation/site.

    Spatial anchor generation, cap escalation, and state mutation belong to
    later constructor layers.  This shared kernel only proposes a placement
    after the S0 checker-parity insertion validator accepts it.
    """
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
    if not validate_insertion(state, placement):
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
