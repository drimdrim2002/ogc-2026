"""Exact branch-1/2 validation for unary, pair, and targeted changes."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

from .state import Placement, SolutionState


@dataclass(slots=True)
class ValidationTelemetry:
    """Counters for checker-parity sampling at validation boundaries."""

    cases: int = 0
    mismatches: int = 0

    def record_parity(self, targeted: bool, full_check: bool) -> None:
        self.cases += 1
        if targeted != full_check:
            self.mismatches += 1


def validate_unary(state: SolutionState, placement: Placement) -> bool:
    """Check block identity, integer timing/position, fit, and residence."""
    instance = state.instance
    if not _plain_int(placement.block_id) or not (
        0 <= placement.block_id < len(instance.blocks)
    ):
        return False
    if not _plain_int(placement.bay_id) or not (
        0 <= placement.bay_id < len(instance.bays)
    ):
        return False
    block = instance.blocks[placement.block_id]
    if not _plain_int(placement.orient_idx) or not (
        0 <= placement.orient_idx < len(block.orientations)
    ):
        return False
    if not all(
        _plain_int(value)
        for value in (placement.x, placement.y, placement.entry, placement.exit)
    ):
        return False
    if placement.entry < block.release_time:
        return False
    if placement.exit - placement.entry < block.dwell:
        return False
    ranges = block.orientations[placement.orient_idx].integer_position_ranges(
        instance.bays[placement.bay_id]
    )
    return (
        ranges is not None
        and placement.x in ranges[0]
        and placement.y in ranges[1]
    )


def validate_pair(
    state: SolutionState,
    left: Placement,
    right: Placement,
) -> bool:
    """Check the exact non-interlock pair trichotomy branches 1 and 2."""
    if left.block_id == right.block_id:
        return False
    if left.bay_id != right.bay_id:
        return True
    if left.exit <= right.entry or right.exit <= left.entry:
        return True
    left_shape = state.shape_info(left)
    right_shape = state.shape_info(right)
    return state.geom.union_disjoint(
        left_shape,
        right_shape,
        right.x - left.x,
        right.y - left.y,
    )


def validate_changed(state: SolutionState, changed_ids: Iterable[int]) -> bool:
    """Recheck changed unary facts and each pair involving a changed block."""
    changed = set(changed_ids)
    if any(not _plain_int(block_id) for block_id in changed):
        return False
    if any(block_id not in state.placements for block_id in changed):
        return False
    if any(not validate_unary(state, state.placements[block_id]) for block_id in changed):
        return False

    placements = tuple(state.placements.values())
    for index, left in enumerate(placements):
        for right in placements[index + 1 :]:
            if (
                left.block_id in changed or right.block_id in changed
            ) and not validate_pair(state, left, right):
                return False
    return True


def validate_insertion(state: SolutionState, candidate: Placement) -> bool:
    """Check a candidate against unary facts and all other same-state blocks."""
    if not validate_unary(state, candidate):
        return False
    return all(
        validate_pair(state, candidate, other)
        for other in state.placements.values()
        if other.block_id != candidate.block_id
    )


def _plain_int(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)
