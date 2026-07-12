"""Mutable placement state with checker-parity objective diagnostics."""

from __future__ import annotations

import math
from dataclasses import dataclass
from types import MappingProxyType
from typing import Mapping

from .geometry import GeomKernel, ShapeInfo
from .instance import ProblemInstance


@dataclass(frozen=True, slots=True)
class Placement:
    """One block's complete non-interlock placement and residence interval."""

    block_id: int
    bay_id: int
    x: int
    y: int
    orient_idx: int
    entry: int
    exit: int


@dataclass(frozen=True, slots=True)
class ObjectiveDiagnostics:
    """Internal objective components in the official checker's float domain."""

    z1: float
    z2: float
    z3: float
    objective: float
    bay_loads: tuple[float, ...]


class SolutionState:
    """Own placements and maintain reversible incremental objective accounting."""

    def __init__(
        self,
        instance: ProblemInstance,
        *,
        geom: GeomKernel | None = None,
    ) -> None:
        self.instance = instance
        self.geom = geom if geom is not None else GeomKernel()
        self._placements: dict[int, Placement] = {}
        self._bay_loads = [0.0 for _ in instance.bays]
        self._z1 = 0.0
        self._z3 = 0.0
        self._shapes = tuple(
            tuple(ShapeInfo.from_orientation(orientation) for orientation in block.orientations)
            for block in instance.blocks
        )

    @property
    def placements(self) -> Mapping[int, Placement]:
        return MappingProxyType(self._placements)

    def get(self, block_id: int) -> Placement | None:
        return self._placements.get(block_id)

    def shape_info(self, placement: Placement) -> ShapeInfo:
        return self.orientation_shape(placement.block_id, placement.orient_idx)

    def orientation_shape(self, block_id: int, orient_idx: int) -> ShapeInfo:
        """Return the cached exact geometry for one constructor orientation."""
        return self._shapes[block_id][orient_idx]

    def place(self, placement: Placement) -> Placement | None:
        """Install or replace a placement and return the prior value for undo."""
        if not 0 <= placement.block_id < len(self.instance.blocks):
            raise IndexError(f"block_id {placement.block_id} is out of range")
        previous = self._placements.get(placement.block_id)
        if previous is not None:
            self._subtract(previous)
        self._placements[placement.block_id] = placement
        self._add(placement)
        return previous

    def remove(self, block_id: int) -> Placement | None:
        """Remove a placement and return it so callers can restore it exactly."""
        previous = self._placements.pop(block_id, None)
        if previous is not None:
            self._subtract(previous)
        return previous

    @property
    def z1(self) -> float:
        return self._z1

    @property
    def z2(self) -> float:
        return _load_range(self.instance, self._bay_loads)

    @property
    def z3(self) -> float:
        return self._z3

    @property
    def objective(self) -> float:
        w1, w2, w3 = self.instance.weights
        return w1 * self.z1 + w2 * self.z2 + w3 * self.z3

    @property
    def objective_diagnostics(self) -> ObjectiveDiagnostics:
        return ObjectiveDiagnostics(
            z1=self.z1,
            z2=self.z2,
            z3=self.z3,
            objective=self.objective,
            bay_loads=tuple(self._bay_loads),
        )

    def recompute_objective(self) -> ObjectiveDiagnostics:
        """Recompute all diagnostics from placements without using incremental state."""
        bay_loads = [0.0 for _ in self.instance.bays]
        z1 = 0.0
        z3 = 0.0
        for placement in self._placements.values():
            block = self.instance.blocks[placement.block_id]
            z1 += max(0.0, float(placement.exit - block.due_date))
            if 0 <= placement.bay_id < len(bay_loads):
                bay_loads[placement.bay_id] += block.workload
                z3 += max(block.bay_preferences) - block.bay_preferences[placement.bay_id]
        z2 = _load_range(self.instance, bay_loads)
        w1, w2, w3 = self.instance.weights
        return ObjectiveDiagnostics(
            z1=z1,
            z2=z2,
            z3=z3,
            objective=w1 * z1 + w2 * z2 + w3 * z3,
            bay_loads=tuple(bay_loads),
        )

    def assert_invariants(self, *, tolerance: float = 1e-9) -> None:
        """Raise when incremental accounting differs from a full recomputation."""
        incremental = self.objective_diagnostics
        recomputed = self.recompute_objective()
        for label in ("z1", "z2", "z3", "objective"):
            if not math.isclose(
                getattr(incremental, label),
                getattr(recomputed, label),
                rel_tol=tolerance,
                abs_tol=tolerance,
            ):
                raise AssertionError(
                    f"incremental {label}={getattr(incremental, label)!r} "
                    f"!= recomputed {getattr(recomputed, label)!r}"
                )
        if len(incremental.bay_loads) != len(recomputed.bay_loads) or any(
            not math.isclose(left, right, rel_tol=tolerance, abs_tol=tolerance)
            for left, right in zip(
                incremental.bay_loads, recomputed.bay_loads, strict=True
            )
        ):
            raise AssertionError(
                f"incremental bay_loads={incremental.bay_loads!r} "
                f"!= recomputed {recomputed.bay_loads!r}"
            )

    def _add(self, placement: Placement) -> None:
        block = self.instance.blocks[placement.block_id]
        self._z1 += max(0.0, float(placement.exit - block.due_date))
        if not 0 <= placement.bay_id < len(self.instance.bays):
            return
        self._bay_loads[placement.bay_id] += block.workload
        self._z3 += max(block.bay_preferences) - block.bay_preferences[placement.bay_id]

    def _subtract(self, placement: Placement) -> None:
        block = self.instance.blocks[placement.block_id]
        self._z1 -= max(0.0, float(placement.exit - block.due_date))
        if not 0 <= placement.bay_id < len(self.instance.bays):
            return
        self._bay_loads[placement.bay_id] -= block.workload
        self._z3 -= max(block.bay_preferences) - block.bay_preferences[placement.bay_id]


def _load_range(instance: ProblemInstance, bay_loads: list[float]) -> float:
    if len(instance.bays) < 2:
        return 0.0
    average_area = sum(bay.area for bay in instance.bays) / len(instance.bays)
    normalized = tuple(
        average_area / bay.area * load
        for bay, load in zip(instance.bays, bay_loads, strict=True)
    )
    return max(normalized) - min(normalized)
