"""Checker-equivalent geometry preprocessing and four-state relations."""

from __future__ import annotations

from collections import OrderedDict, namedtuple
from dataclasses import dataclass
from enum import Enum
from itertools import combinations
from typing import Iterable

from shapely.affinity import translate
from shapely.geometry import GeometryCollection, Polygon
from shapely.geometry.base import BaseGeometry
from shapely.ops import unary_union

from .instance import Instance, OrientationInfo
from .state import Placement, SolutionSnapshot


AABB = tuple[float, float, float, float]
CacheInfo = namedtuple("CacheInfo", "hits misses maxsize currsize")


class GeometryError(RuntimeError):
    """Raised when an exact geometry operation cannot produce a verdict."""


class PairState(Enum):
    FREE = "FREE"
    I_OUTER = "I_OUTER"
    K_OUTER = "K_OUTER"
    SEPARATE = "SEPARATE"


class TemporalMode(Enum):
    FREE = "FREE"
    I_BEFORE = "I_BEFORE"
    K_BEFORE = "K_BEFORE"
    I_NESTED = "I_NESTED"
    K_NESTED = "K_NESTED"
    INVALID = "INVALID"


@dataclass(frozen=True, slots=True)
class ShapeInfo:
    layers: tuple[BaseGeometry | None, ...]
    layer_aabbs: tuple[AABB | None, ...]
    full_aabb: AABB
    union: BaseGeometry
    union_aabb: AABB | None
    suffix_unions: tuple[BaseGeometry, ...]
    suffix_aabbs: tuple[AABB | None, ...]


@dataclass(frozen=True, slots=True)
class PairRelation:
    state: PairState
    g_i_k: bool
    g_k_i: bool

    @classmethod
    def from_bits(cls, g_i_k: bool, g_k_i: bool) -> "PairRelation":
        if g_i_k and g_k_i:
            state = PairState.SEPARATE
        elif g_i_k:
            state = PairState.I_OUTER
        elif g_k_i:
            state = PairState.K_OUTER
        else:
            state = PairState.FREE
        return cls(state=state, g_i_k=g_i_k, g_k_i=g_k_i)

    def swapped(self) -> "PairRelation":
        return PairRelation.from_bits(self.g_k_i, self.g_i_k)

    @staticmethod
    def _interval(value: tuple[int, int] | Placement) -> tuple[int, int]:
        if isinstance(value, Placement):
            interval = (value.entry, value.exit)
        else:
            try:
                interval = tuple(value)
            except TypeError as exc:
                raise TypeError("interval must be a Placement or (entry, exit) pair") from exc
        if len(interval) != 2:
            raise ValueError("interval must contain entry and exit")
        entry, exit_time = interval
        if exit_time <= entry:
            raise ValueError("interval must have positive duration")
        return entry, exit_time

    def mode(
        self,
        interval_i: tuple[int, int] | Placement,
        interval_k: tuple[int, int] | Placement,
    ) -> TemporalMode:
        ai, ei = self._interval(interval_i)
        ak, ek = self._interval(interval_k)
        if self.state is PairState.FREE:
            return TemporalMode.FREE
        if ei <= ak:
            return TemporalMode.I_BEFORE
        if ek <= ai:
            return TemporalMode.K_BEFORE
        if self.state is PairState.I_OUTER and ai + 1 <= ak and ek <= ei:
            return TemporalMode.K_NESTED
        if self.state is PairState.K_OUTER and ak + 1 <= ai and ei <= ek:
            return TemporalMode.I_NESTED
        return TemporalMode.INVALID

    def allows(
        self,
        interval_i: tuple[int, int] | Placement,
        interval_k: tuple[int, int] | Placement,
    ) -> bool:
        return self.mode(interval_i, interval_k) is not TemporalMode.INVALID


def _aabb(geometry: BaseGeometry | None) -> AABB | None:
    if geometry is None or geometry.is_empty:
        return None
    xmin, ymin, xmax, ymax = geometry.bounds
    return float(xmin), float(ymin), float(xmax), float(ymax)


def _aabb_overlap(left: AABB | None, right: AABB | None, dx: float, dy: float) -> bool:
    if left is None or right is None:
        return False
    return (
        left[0] < right[2] + dx
        and right[0] + dx < left[2]
        and left[1] < right[3] + dy
        and right[1] + dy < left[3]
    )


def _polygon(vertices: tuple[tuple[float, float], ...]) -> BaseGeometry | None:
    if len(vertices) < 3:
        return None
    try:
        polygon: BaseGeometry = Polygon(vertices)
        if not polygon.is_valid:
            polygon = polygon.buffer(0)
        return polygon if not polygon.is_empty else None
    except Exception:
        # This matches the checker helper: an unconstructable layer is skipped.
        return None


def _shape_info(orientation: OrientationInfo) -> ShapeInfo:
    layers = tuple(_polygon(layer) for layer in orientation.raw_layers)
    layer_aabbs = tuple(_aabb(layer) for layer in layers)
    valid_layers = [layer for layer in layers if layer is not None]
    try:
        footprint: BaseGeometry = unary_union(valid_layers) if valid_layers else GeometryCollection()
        suffix_reversed: list[BaseGeometry] = []
        suffix: BaseGeometry = GeometryCollection()
        for layer in reversed(layers):
            if layer is not None:
                suffix = unary_union((layer, suffix)) if not suffix.is_empty else layer
            suffix_reversed.append(suffix)
    except Exception as exc:
        raise GeometryError("failed to construct repaired layer unions") from exc
    suffix_unions = tuple(reversed(suffix_reversed))
    return ShapeInfo(
        layers=layers,
        layer_aabbs=layer_aabbs,
        full_aabb=(orientation.xmin, orientation.ymin, orientation.xmax, orientation.ymax),
        union=footprint,
        union_aabb=_aabb(footprint),
        suffix_unions=suffix_unions,
        suffix_aabbs=tuple(_aabb(item) for item in suffix_unions),
    )


class GeometryKernel:
    """Immutable shapes plus a bounded exact relation-verdict cache."""

    def __init__(self, instance: Instance, cache_size: int = 2**18) -> None:
        if isinstance(cache_size, bool) or not isinstance(cache_size, int) or cache_size < 0:
            raise ValueError("cache_size must be a non-negative integer")
        self.instance = instance
        self._shapes = tuple(
            tuple(_shape_info(orientation) for orientation in block.orientations)
            for block in instance.blocks
        )
        self._cache_size = cache_size
        self._cache: OrderedDict[tuple[int, int, int, int, int, int], PairRelation] = OrderedDict()
        self._hits = 0
        self._misses = 0

    @classmethod
    def from_instance(cls, instance: Instance, cache_size: int = 2**18) -> "GeometryKernel":
        return cls(instance, cache_size=cache_size)

    def shape(self, block_id: int, orient_idx: int) -> ShapeInfo:
        return self._shapes[block_id][orient_idx]

    def fits(self, placement: Placement) -> bool:
        if not 0 <= placement.block_id < len(self.instance.blocks):
            return False
        if not 0 <= placement.bay_id < len(self.instance.bays):
            return False
        block = self.instance.block(placement.block_id)
        if not 0 <= placement.orient_idx < len(block.orientations):
            return False
        reference_range = block.orientations[placement.orient_idx].integer_range(
            self.instance.bay(placement.bay_id)
        )
        return bool(
            reference_range is not None
            and reference_range.x.lower <= placement.x <= reference_range.x.upper
            and reference_range.y.lower <= placement.y <= reference_range.y.upper
        )

    def _obstructs_relative(
        self,
        mover: Placement,
        stationary: Placement,
    ) -> bool:
        mover_shape = self.shape(mover.block_id, mover.orient_idx)
        stationary_shape = self.shape(stationary.block_id, stationary.orient_idx)
        dx = stationary.x - mover.x
        dy = stationary.y - mover.y
        limit = min(len(mover_shape.layers), len(stationary_shape.suffix_unions))
        for layer_index in range(limit):
            mover_layer = mover_shape.layers[layer_index]
            if mover_layer is None:
                continue
            if not _aabb_overlap(
                mover_shape.layer_aabbs[layer_index],
                stationary_shape.suffix_aabbs[layer_index],
                dx,
                dy,
            ):
                continue
            try:
                stationary_suffix = translate(
                    stationary_shape.suffix_unions[layer_index], xoff=dx, yoff=dy
                )
                intersection = mover_layer.intersection(stationary_suffix)
            except Exception as exc:
                raise GeometryError("exact obstruction intersection failed") from exc
            if not intersection.is_empty and intersection.area > 0:
                return True
        return False

    def obstructs(self, mover: Placement, stationary: Placement) -> bool:
        if mover.block_id == stationary.block_id:
            raise ValueError("a block cannot obstruct itself")
        if mover.bay_id != stationary.bay_id:
            return False
        return self._obstructs_relative(mover, stationary)

    @staticmethod
    def _canonical(
        first: Placement, second: Placement
    ) -> tuple[Placement, Placement, bool]:
        if first.block_id == second.block_id:
            raise ValueError("relation requires two distinct blocks")
        if first.block_id < second.block_id:
            return first, second, False
        return second, first, True

    def relation(self, i: Placement, k: Placement) -> PairRelation:
        if i.bay_id != k.bay_id:
            return PairRelation.from_bits(False, False)
        first, second, swapped = self._canonical(i, k)
        key = (
            first.block_id,
            first.orient_idx,
            second.block_id,
            second.orient_idx,
            second.x - first.x,
            second.y - first.y,
        )
        try:
            relation = self._cache.pop(key)
        except KeyError:
            self._misses += 1
            relation = PairRelation.from_bits(
                self._obstructs_relative(first, second),
                self._obstructs_relative(second, first),
            )
            if self._cache_size:
                self._cache[key] = relation
                if len(self._cache) > self._cache_size:
                    self._cache.popitem(last=False)
        else:
            self._hits += 1
            self._cache[key] = relation
        return relation.swapped() if swapped else relation

    def entry_pair_is_free(self, i: Placement, k: Placement) -> bool:
        return self.relation(i, k).state is PairState.FREE

    def exit_edges(
        self,
        exiting_ids: Iterable[int],
        snapshot: SolutionSnapshot,
        bay_id: int,
    ) -> tuple[tuple[int, int], ...]:
        ids = sorted(set(exiting_ids))
        by_id = {placement.block_id: placement for placement in snapshot.placements}
        if any(block_id not in by_id for block_id in ids):
            raise GeometryError("exit set references a missing placement")
        if any(by_id[block_id].bay_id != bay_id for block_id in ids):
            raise GeometryError("exit set contains a placement from another bay")
        edges: list[tuple[int, int]] = []
        for i_id, k_id in combinations(ids, 2):
            relation = self.relation(by_id[i_id], by_id[k_id])
            if relation.g_i_k:
                edges.append((k_id, i_id))
            if relation.g_k_i:
                edges.append((i_id, k_id))
        return tuple(edges)

    def edges(
        self,
        exiting_ids: Iterable[int],
        snapshot: SolutionSnapshot,
        bay_id: int,
    ) -> tuple[tuple[int, int], ...]:
        return self.exit_edges(exiting_ids, snapshot, bay_id)

    def entries_are_free(
        self,
        entering_ids: Iterable[int],
        snapshot: SolutionSnapshot,
        bay_id: int,
    ) -> bool:
        ids = sorted(set(entering_ids))
        by_id = {placement.block_id: placement for placement in snapshot.placements}
        if any(block_id not in by_id or by_id[block_id].bay_id != bay_id for block_id in ids):
            return False
        return all(
            self.entry_pair_is_free(by_id[i_id], by_id[k_id])
            for i_id, k_id in combinations(ids, 2)
        )

    def cache_info(self) -> CacheInfo:
        return CacheInfo(self._hits, self._misses, self._cache_size, len(self._cache))
