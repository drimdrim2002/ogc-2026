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


@dataclass(frozen=True, slots=True)
class StateUndoToken:
    """Exact mutable-state checkpoint used by transactional local search."""

    placements: tuple[Placement, ...]
    bay_members: tuple[tuple[int, ...], ...]
    bay_loads: tuple[float, ...]
    z1: float
    z2: float
    z3: float
    objective: float
    _owner_id: int


class SolutionState:
    """Own placements and maintain reversible incremental objective accounting."""

    def __init__(
        self,
        instance: ProblemInstance,
        *,
        geom: GeomKernel | None = None,
        shape_catalog: tuple[tuple[ShapeInfo, ...], ...] | None = None,
    ) -> None:
        self.instance = instance
        self.geom = geom if geom is not None else GeomKernel()
        self._placements: dict[int, Placement] = {}
        self._bay_members = [[] for _ in instance.bays]
        self._bay_loads = [0.0 for _ in instance.bays]
        self._z1 = 0.0
        self._z3 = 0.0
        self._shapes = (
            shape_catalog
            if shape_catalog is not None
            else tuple(
                tuple(
                    ShapeInfo.from_orientation(orientation)
                    for orientation in block.orientations
                )
                for block in instance.blocks
            )
        )
        if len(self._shapes) != len(instance.blocks) or any(
            len(shapes) != len(block.orientations)
            for shapes, block in zip(self._shapes, instance.blocks, strict=True)
        ):
            raise ValueError("shape_catalog does not match the problem instance")

    @property
    def placements(self) -> Mapping[int, Placement]:
        return MappingProxyType(self._placements)

    @property
    def bay_members(self) -> tuple[tuple[int, ...], ...]:
        """Return the exact ordered block membership array for every bay."""
        return tuple(tuple(members) for members in self._bay_members)

    @property
    def shape_catalog(self) -> tuple[tuple[ShapeInfo, ...], ...]:
        """Return the immutable per-instance geometry catalog for fresh states."""
        return self._shapes

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
            if previous.bay_id != placement.bay_id:
                self._remove_bay_member(previous)
        self._placements[placement.block_id] = placement
        if previous is None or previous.bay_id != placement.bay_id:
            self._add_bay_member(placement)
        self._add(placement)
        return previous

    def remove(self, block_id: int) -> Placement | None:
        """Remove a placement and return it so callers can restore it exactly."""
        previous = self._placements.pop(block_id, None)
        if previous is not None:
            self._subtract(previous)
            self._remove_bay_member(previous)
        return previous

    def capture_undo_token(self) -> StateUndoToken:
        """Capture every mutable semantic field without recomputing any value."""
        return StateUndoToken(
            placements=tuple(self._placements.values()),
            bay_members=self.bay_members,
            bay_loads=tuple(self._bay_loads),
            z1=self._z1,
            z2=self.z2,
            z3=self._z3,
            objective=self.objective,
            _owner_id=id(self),
        )

    def restore_undo_token(self, token: StateUndoToken) -> None:
        """Restore an exact checkpoint and reject tokens from another state."""
        if not isinstance(token, StateUndoToken) or token._owner_id != id(self):
            raise ValueError("undo token does not belong to this SolutionState")
        if len(token.bay_members) != len(self.instance.bays) or len(
            token.bay_loads
        ) != len(self.instance.bays):
            raise ValueError("undo token bay arrays do not match this instance")
        self._placements = {
            placement.block_id: placement for placement in token.placements
        }
        self._bay_members = [list(members) for members in token.bay_members]
        self._bay_loads = list(token.bay_loads)
        self._z1 = token.z1
        self._z3 = token.z3
        self.assert_invariants()
        restored = self.capture_undo_token()
        if restored != token:
            raise AssertionError("restored state does not exactly match undo token")

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
        expected_members = tuple(
            tuple(sorted(
                placement.block_id
                for placement in self._placements.values()
                if placement.bay_id == bay_id
            ))
            for bay_id in range(len(self.instance.bays))
        )
        if self.bay_members != expected_members:
            raise AssertionError(
                f"bay_members={self.bay_members!r} != expected={expected_members!r}"
            )
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

    def _add_bay_member(self, placement: Placement) -> None:
        if 0 <= placement.bay_id < len(self._bay_members):
            self._bay_members[placement.bay_id].append(placement.block_id)
            self._bay_members[placement.bay_id].sort()

    def _remove_bay_member(self, placement: Placement) -> None:
        if 0 <= placement.bay_id < len(self._bay_members):
            self._bay_members[placement.bay_id].remove(placement.block_id)


def _load_range(instance: ProblemInstance, bay_loads: list[float]) -> float:
    if len(instance.bays) < 2:
        return 0.0
    average_area = sum(bay.area for bay in instance.bays) / len(instance.bays)
    normalized = tuple(
        average_area / bay.area * load
        for bay, load in zip(instance.bays, bay_loads, strict=True)
    )
    return max(normalized) - min(normalized)
