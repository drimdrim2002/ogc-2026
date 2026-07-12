"""Deterministic checker-float bay assignment for the S1 constructor."""

from __future__ import annotations

from dataclasses import dataclass
import math
from collections.abc import Sequence

from .geometry import ShapeInfo
from .instance import ProblemInstance


@dataclass(frozen=True, slots=True)
class AssignmentCost:
    """One fitting bay/orientation candidate and its exact diagnostics."""

    bay_id: int
    orient_idx: int
    z2_before: float
    z2_after: float
    z2_delta: float
    preference_loss: float
    weighted_cost: float
    congestion_ratio: float
    area_time: float


@dataclass(frozen=True, slots=True)
class BlockAssignment:
    """The selected candidate plus its regret at commitment time."""

    block_id: int
    bay_id: int
    orient_idx: int
    regret: float
    weighted_cost: float
    z2_after: float
    preference_loss: float
    congestion_ratio: float


@dataclass(frozen=True, slots=True)
class AssignmentMetrics:
    """Structural counters required by the S1 assignment evidence."""

    assigned: int
    fallback: int
    fit_failures: int
    candidate_evaluations: int
    horizon: int


@dataclass(frozen=True, slots=True)
class AssignmentResult:
    """Complete deterministic assignment and checker-float diagnostics."""

    assignments: dict[int, BlockAssignment]
    order: tuple[int, ...]
    bay_workloads: tuple[float, ...]
    bay_area_time: tuple[float, ...]
    z2: float
    z3: float
    metrics: AssignmentMetrics


def assignment_cost(
    instance: ProblemInstance,
    block_id: int,
    bay_id: int,
    orient_idx: int,
    bay_workloads: Sequence[float],
    bay_area_time: Sequence[float],
    horizon: int,
    *,
    shape_area: float | None = None,
) -> AssignmentCost:
    """Return exact Z2/Z3 terms and the instance-scaled congestion diagnostic.

    Congestion is deliberately a deterministic tie-break diagnostic in S1-01.
    Its later coefficient is measurement-owned and must not reverse unequal
    checker-float assignment costs before that calibration occurs.
    """
    if not instance.fit_matrix[block_id][bay_id][orient_idx]:
        raise ValueError("assignment candidate does not fit the requested bay")
    if len(bay_workloads) != len(instance.bays):
        raise ValueError("bay_workloads length does not match the instance")
    if len(bay_area_time) != len(instance.bays):
        raise ValueError("bay_area_time length does not match the instance")
    if horizon <= 0:
        raise ValueError("horizon must be positive")

    block = instance.blocks[block_id]
    before = _load_range(instance, bay_workloads)
    prospective = list(float(value) for value in bay_workloads)
    prospective[bay_id] += block.workload
    after = _load_range(instance, prospective)
    preference_loss = max(block.bay_preferences) - block.bay_preferences[bay_id]
    area = (
        float(shape_area)
        if shape_area is not None
        else ShapeInfo.from_orientation(block.orientations[orient_idx]).area
    )
    area_time = area * block.dwell
    congestion_ratio = (
        float(bay_area_time[bay_id]) + area_time
    ) / (instance.bays[bay_id].area * horizon)
    _, w2, w3 = instance.weights
    z2_delta = after - before
    return AssignmentCost(
        bay_id=bay_id,
        orient_idx=orient_idx,
        z2_before=before,
        z2_after=after,
        z2_delta=z2_delta,
        preference_loss=preference_loss,
        weighted_cost=w2 * z2_delta + w3 * preference_loss,
        congestion_ratio=congestion_ratio,
        area_time=area_time,
    )


class AssignmentV1:
    """Assign blocks by recomputed best-vs-second-best bay regret."""

    def __init__(self, instance: ProblemInstance) -> None:
        instance.assert_solvable_fit()
        self.instance = instance
        self.horizon = _instance_horizon(instance)
        self._shape_areas = tuple(
            tuple(
                ShapeInfo.from_orientation(orientation).area
                for orientation in block.orientations
            )
            for block in instance.blocks
        )

    def assign(self) -> AssignmentResult:
        workloads = [0.0 for _ in self.instance.bays]
        area_time = [0.0 for _ in self.instance.bays]
        remaining = set(range(len(self.instance.blocks)))
        selected: dict[int, BlockAssignment] = {}
        order: list[int] = []
        candidate_evaluations = 0

        while remaining:
            choices: list[tuple[int, AssignmentCost, float]] = []
            for block_id in sorted(remaining):
                by_bay: list[AssignmentCost] = []
                for bay in self.instance.bays:
                    candidates = [
                        assignment_cost(
                            self.instance,
                            block_id,
                            bay.bay_id,
                            orient_idx,
                            workloads,
                            area_time,
                            self.horizon,
                            shape_area=self._shape_areas[block_id][orient_idx],
                        )
                        for orient_idx in self.instance.fitting_orientations(
                            block_id, bay.bay_id
                        )
                    ]
                    candidate_evaluations += len(candidates)
                    if candidates:
                        by_bay.append(min(candidates, key=_candidate_key))
                ranked = sorted(by_bay, key=_candidate_key)
                if not ranked:
                    raise AssertionError(
                        f"fit-qualified block {block_id} has no assignment candidate"
                    )
                regret = (
                    math.inf
                    if len(ranked) == 1
                    else max(0.0, ranked[1].weighted_cost - ranked[0].weighted_cost)
                )
                choices.append((block_id, ranked[0], regret))

            block_id, best, regret = min(
                choices,
                key=lambda item: (
                    -item[2],
                    item[1].weighted_cost,
                    item[1].congestion_ratio,
                    item[0],
                ),
            )
            block = self.instance.blocks[block_id]
            workloads[best.bay_id] += block.workload
            area_time[best.bay_id] += best.area_time
            selected[block_id] = BlockAssignment(
                block_id=block_id,
                bay_id=best.bay_id,
                orient_idx=best.orient_idx,
                regret=regret,
                weighted_cost=best.weighted_cost,
                z2_after=best.z2_after,
                preference_loss=best.preference_loss,
                congestion_ratio=best.congestion_ratio,
            )
            order.append(block_id)
            remaining.remove(block_id)

        z3 = sum(item.preference_loss for item in selected.values())
        return AssignmentResult(
            assignments=selected,
            order=tuple(order),
            bay_workloads=tuple(workloads),
            bay_area_time=tuple(area_time),
            z2=_load_range(self.instance, workloads),
            z3=z3,
            metrics=AssignmentMetrics(
                assigned=len(selected),
                fallback=0,
                fit_failures=0,
                candidate_evaluations=candidate_evaluations,
                horizon=self.horizon,
            ),
        )


def _candidate_key(candidate: AssignmentCost) -> tuple[float, float, int, int]:
    return (
        candidate.weighted_cost,
        candidate.congestion_ratio,
        candidate.bay_id,
        candidate.orient_idx,
    )


def _instance_horizon(instance: ProblemInstance) -> int:
    if not instance.blocks:
        return 1
    start = min(block.release_time for block in instance.blocks)
    finish = max(
        max(block.due_date, block.release_time + block.dwell)
        for block in instance.blocks
    )
    return max(1, finish - start)


def _load_range(instance: ProblemInstance, workloads: Sequence[float]) -> float:
    if len(instance.bays) < 2:
        return 0.0
    average_area = sum(bay.area for bay in instance.bays) / len(instance.bays)
    normalized = tuple(
        average_area / bay.area * float(load)
        for bay, load in zip(instance.bays, workloads, strict=True)
    )
    return max(normalized) - min(normalized)
