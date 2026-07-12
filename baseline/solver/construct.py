"""Shared non-interlock event-time insertion for the S1 constructor."""

from __future__ import annotations

from dataclasses import dataclass

from .state import Placement, SolutionState
from .validate import validate_insertion


DEFAULT_TIME_CAP = 8
ESCALATED_TIME_CAP = 16


@dataclass(frozen=True, slots=True)
class InsertionCandidate:
    """A validated fixed-site insertion and its deterministic ranking values."""

    placement: Placement
    weighted_tardiness: float
    assignment_cost: float
    score: tuple[float, float, int, int, int, int, int, int]


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
    block = state.instance.blocks[block_id]
    for entry in time_candidates(state, block_id, bay_id, cap=time_cap):
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
            continue
        weighted_tardiness = state.instance.weights[0] * max(
            0.0, float(placement.exit - block.due_date)
        )
        score = (
            weighted_tardiness,
            assignment_cost,
            block_id,
            bay_id,
            orient_idx,
            x,
            y,
            entry,
        )
        return InsertionCandidate(
            placement=placement,
            weighted_tardiness=weighted_tardiness,
            assignment_cost=assignment_cost,
            score=score,
        )
    return None


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
