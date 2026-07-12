"""Deterministic bay-parallel, bay-serial safe candidate construction."""

from __future__ import annotations

from .budget import Budget
from .instance import Instance
from .state import Placement, SolutionSnapshot, compute_objective


def build_safe_candidate(instance: Instance, budget: Budget) -> SolutionSnapshot:
    del budget  # The mandatory safe construction is never skipped for lack of search time.
    bay_loads = [0.0] * len(instance.bays)
    previous_exit = [0] * len(instance.bays)
    average_area = sum(bay.area for bay in instance.bays) / len(instance.bays)
    placements: list[Placement] = []

    for block in instance.blocks:
        best = None
        best_score = None
        preference_max = max(block.bay_preferences)
        for bay_id, orient_idx, reference_range in block.fitting_options:
            bay = instance.bays[bay_id]
            preference_loss = preference_max - block.bay_preferences[bay_id]
            projected_load = average_area / bay.area * (bay_loads[bay_id] + block.workload)
            score = (preference_loss, projected_load, bay_id, orient_idx)
            if best_score is None or score < best_score:
                best_score = score
                best = (bay_id, orient_idx, reference_range)
        assert best is not None
        bay_id, orient_idx, reference_range = best
        entry = max(block.release_time, previous_exit[bay_id])
        exit_time = entry + block.dwell
        x, y = reference_range.anchor
        placements.append(
            Placement(block.index, bay_id, orient_idx, x, y, entry, exit_time)
        )
        previous_exit[bay_id] = exit_time
        bay_loads[bay_id] += block.workload

    snapshot = SolutionSnapshot(tuple(placements))
    return snapshot.with_objective(compute_objective(instance, snapshot))
