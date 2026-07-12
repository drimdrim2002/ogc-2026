"""Fit-qualified deterministic empty-bay fallback construction."""

from __future__ import annotations

from .instance import ProblemInstance
from .state import Placement, SolutionState


def build_t0(instance: ProblemInstance) -> SolutionState:
    """Construct a feasible-by-separation state after physical-fit preflight."""
    instance.assert_solvable_fit()
    state = SolutionState(instance)
    bay_available: list[int | None] = [None for _ in instance.bays]

    for block in instance.blocks:
        bay_id, orient_idx = _preferred_fitting_assignment(instance, block.block_id)
        orientation = block.orientations[orient_idx]
        ranges = orientation.integer_position_ranges(instance.bays[bay_id])
        if ranges is None:  # Protected by fit preflight and assignment selection.
            raise AssertionError("selected T0 orientation unexpectedly does not fit")
        available = bay_available[bay_id]
        entry = (
            block.release_time
            if available is None
            else max(block.release_time, available)
        )
        exit_time = entry + block.dwell
        state.place(
            Placement(
                block_id=block.block_id,
                bay_id=bay_id,
                x=ranges[0].start,
                y=ranges[1].start,
                orient_idx=orient_idx,
                entry=entry,
                exit=exit_time,
            )
        )
        bay_available[bay_id] = exit_time

    state.assert_invariants()
    return state


def _preferred_fitting_assignment(
    instance: ProblemInstance, block_id: int
) -> tuple[int, int]:
    block = instance.blocks[block_id]
    fitting = [
        (bay.bay_id, orient_idx)
        for bay in instance.bays
        for orient_idx in instance.fitting_orientations(block_id, bay.bay_id)
    ]
    return min(
        fitting,
        key=lambda item: (-block.bay_preferences[item[0]], item[0], item[1]),
    )
