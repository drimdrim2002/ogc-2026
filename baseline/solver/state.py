"""Immutable snapshots, transactional drafts, and validated incumbents."""

from __future__ import annotations

import copy
import math
from dataclasses import dataclass, replace
from collections.abc import Callable, Iterable
from typing import Any, Mapping

from .instance import Instance


@dataclass(frozen=True, slots=True)
class ObjectiveParts:
    z1: float
    z2: float
    z3: float
    total: float


@dataclass(frozen=True, slots=True)
class Placement:
    block_id: int
    bay_id: int
    orient_idx: int
    x: int
    y: int
    entry: int
    exit: int

    def __post_init__(self) -> None:
        for name in ("block_id", "bay_id", "orient_idx", "x", "y", "entry", "exit"):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int):
                raise TypeError(f"{name} must be an integer")
        if self.exit <= self.entry:
            raise ValueError("placement interval must have positive duration")


@dataclass(frozen=True, slots=True)
class SolutionSnapshot:
    placements: tuple[Placement, ...]
    objective: ObjectiveParts | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "placements", tuple(self.placements))
        ids = [placement.block_id for placement in self.placements]
        if len(ids) != len(set(ids)):
            raise ValueError("snapshot contains duplicate block ids")

    def with_objective(self, objective: ObjectiveParts) -> "SolutionSnapshot":
        return replace(self, objective=objective)


@dataclass(slots=True)
class CandidateDraft:
    placements: list[Placement]

    @classmethod
    def from_snapshot(cls, snapshot: SolutionSnapshot) -> "CandidateDraft":
        return cls(list(snapshot.placements))

    def freeze(self) -> SolutionSnapshot:
        return SolutionSnapshot(tuple(self.placements))


@dataclass(frozen=True, slots=True)
class AssignmentDelta:
    """Exact partial-state change in the checker assignment components."""

    z2: float
    z3: float
    weighted: float


def checker_z2(normalized_loads: Iterable[float]) -> float:
    """Return the official checker v1.3 floored normalized-load range."""
    values = tuple(float(value) for value in normalized_loads)
    if len(values) < 2:
        return 0.0
    return float(math.floor(max(values) - min(values)))


class IndexedSolutionState:
    """Transactional constructor draft with bay interval and load indexes.

    The constructor only commits complete placements.  Index mutations are
    rolled back together if validation fails or raises, so a rejected geometry
    operation cannot leave stale interval/load state behind.
    """

    def __init__(
        self,
        instance: Instance,
        placements: Iterable[Placement] = (),
        *,
        time_cap: int = 12,
        anchor_cap: int = 48,
        lattice_cap: int = 512,
    ) -> None:
        self.instance = instance
        self.time_cap = max(0, int(time_cap))
        self.anchor_cap = max(0, int(anchor_cap))
        self.lattice_cap = max(0, int(lattice_cap))
        self.kernel: Any | None = None
        self._by_id: dict[int, Placement] = {}
        self._intervals: list[list[Placement]] = [list() for _ in instance.bays]
        self._loads = [0.0 for _ in instance.bays]
        self.version = 0
        for placement in placements:
            self._apply(placement)
        self.version = 0

    @property
    def placements(self) -> tuple[Placement, ...]:
        return tuple(self._by_id[key] for key in sorted(self._by_id))

    @property
    def block_ids(self) -> frozenset[int]:
        return frozenset(self._by_id)

    @property
    def raw_bay_loads(self) -> tuple[float, ...]:
        return tuple(self._loads)

    def bay_intervals(self, bay_id: int) -> tuple[Placement, ...]:
        return tuple(self._intervals[bay_id])

    def co_present(
        self, bay_id: int, interval: tuple[int, int]
    ) -> tuple[Placement, ...]:
        entry, exit_time = interval
        return tuple(
            placement
            for placement in self._intervals[bay_id]
            if placement.entry < exit_time and entry < placement.exit
        )

    def earliest_empty_window(self, bay_id: int, release: int, dwell: int) -> int:
        start = int(release)
        for placement in self._intervals[bay_id]:
            if placement.exit <= start:
                continue
            if start + dwell <= placement.entry:
                break
            start = placement.exit
        return start

    def normalized_loads(self, *, extra: tuple[int, float] | None = None) -> tuple[float, ...]:
        average_area = sum(bay.area for bay in self.instance.bays) / len(self.instance.bays)
        values = list(self._loads)
        if extra is not None:
            bay_id, workload = extra
            values[bay_id] += workload
        return tuple(
            average_area / bay.area * values[bay.index]
            for bay in self.instance.bays
        )

    @staticmethod
    def _load_range(values: tuple[float, ...]) -> float:
        return checker_z2(values)

    def assignment_delta(self, block_id: int, bay_id: int) -> AssignmentDelta:
        block = self.instance.block(block_id)
        before = self._load_range(self.normalized_loads())
        after = self._load_range(
            self.normalized_loads(extra=(bay_id, block.workload))
        )
        z2 = after - before
        z3 = max(block.bay_preferences) - block.bay_preferences[bay_id]
        weighted = self.instance.weights.w2 * z2 + self.instance.weights.w3 * z3
        return AssignmentDelta(z2=z2, z3=z3, weighted=weighted)

    def _apply(self, placement: Placement) -> None:
        if placement.block_id in self._by_id:
            raise ValueError(f"block {placement.block_id} is already placed")
        if not 0 <= placement.block_id < len(self.instance.blocks):
            raise ValueError("invalid block id")
        if not 0 <= placement.bay_id < len(self.instance.bays):
            raise ValueError("invalid bay id")
        self._by_id[placement.block_id] = placement
        intervals = self._intervals[placement.bay_id]
        intervals.append(placement)
        intervals.sort(key=lambda item: (item.entry, item.exit, item.block_id))
        self._loads[placement.bay_id] += self.instance.block(placement.block_id).workload

    def _undo(self, placement: Placement) -> None:
        del self._by_id[placement.block_id]
        self._intervals[placement.bay_id].remove(placement)
        self._loads[placement.bay_id] -= self.instance.block(placement.block_id).workload

    def transactional_insert(
        self,
        placement: Placement,
        validator: Callable[["IndexedSolutionState", Placement], bool] | None = None,
    ) -> bool:
        self._apply(placement)
        try:
            if validator is not None and validator(self, placement) is not True:
                self._undo(placement)
                return False
        except Exception:
            self._undo(placement)
            raise
        self.version += 1
        return True

    def freeze(self) -> SolutionSnapshot:
        return SolutionSnapshot(self.placements)


def compute_objective(instance: Instance, snapshot: SolutionSnapshot) -> ObjectiveParts:
    if len(snapshot.placements) != len(instance.blocks):
        raise ValueError("objective requires one placement per block")
    placements = {placement.block_id: placement for placement in snapshot.placements}
    if set(placements) != set(range(len(instance.blocks))):
        raise ValueError("objective requires every block id exactly once")

    z1 = 0.0
    z3 = 0.0
    bay_loads = [0.0] * len(instance.bays)
    for block in instance.blocks:
        placement = placements[block.index]
        if not 0 <= placement.bay_id < len(instance.bays):
            raise ValueError(f"invalid bay id for block {block.index}")
        z1 += max(0.0, float(placement.exit - block.due_date))
        bay_loads[placement.bay_id] += block.workload
        z3 += max(block.bay_preferences) - block.bay_preferences[placement.bay_id]

    average_area = sum(bay.area for bay in instance.bays) / len(instance.bays)
    normalized = [
        average_area / bay.area * bay_loads[bay.index]
        for bay in instance.bays
    ]
    z2 = checker_z2(normalized)
    total = instance.weights.w1 * z1 + instance.weights.w2 * z2 + instance.weights.w3 * z3
    return ObjectiveParts(z1=z1, z2=z2, z3=z3, total=total)


class IncumbentStore:
    """Atomically stores only checker-validated strict improvements."""

    def __init__(self, instance: Instance) -> None:
        self._instance = instance
        self._snapshot: SolutionSnapshot | None = None
        self._operations: dict | None = None
        self._checker_result: dict | None = None

    @staticmethod
    def _matches(left: float, right: float) -> bool:
        return math.isclose(left, right, rel_tol=1e-6, abs_tol=1e-9)

    def install_if_valid(
        self,
        candidate: SolutionSnapshot,
        operations: Mapping[str, Any],
        checker_result: Mapping[str, Any],
    ) -> bool:
        if checker_result.get("feasible") is not True or checker_result.get("stage") != 5:
            return False
        objective = candidate.objective or compute_objective(self._instance, candidate)
        checker_values = (
            checker_result.get("obj1"),
            checker_result.get("obj2"),
            checker_result.get("obj3"),
            checker_result.get("objective"),
        )
        if any(isinstance(value, bool) or not isinstance(value, (int, float)) for value in checker_values):
            return False
        internal_values = (objective.z1, objective.z2, objective.z3, objective.total)
        if not all(self._matches(float(left), float(right)) for left, right in zip(internal_values, checker_values)):
            return False
        if self._snapshot is not None:
            assert self._snapshot.objective is not None
            if not objective.total < self._snapshot.objective.total:
                return False
        installed = candidate.with_objective(objective)
        serialized_copy = copy.deepcopy(dict(operations))
        checker_copy = copy.deepcopy(dict(checker_result))
        self._snapshot, self._operations, self._checker_result = installed, serialized_copy, checker_copy
        return True

    @property
    def snapshot(self) -> SolutionSnapshot:
        if self._snapshot is None:
            raise RuntimeError("no validated incumbent installed")
        return self._snapshot

    @property
    def operations(self) -> dict:
        if self._operations is None:
            raise RuntimeError("no validated incumbent installed")
        return copy.deepcopy(self._operations)

    @property
    def checker_result(self) -> dict:
        if self._checker_result is None:
            raise RuntimeError("no validated incumbent installed")
        return copy.deepcopy(self._checker_result)
