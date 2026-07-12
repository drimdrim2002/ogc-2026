"""Checker-exact geometry predicates with conservative fast paths and bounded caching."""

from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass, field
from typing import TypeAlias

from shapely.affinity import translate
from shapely.geometry import GeometryCollection, Polygon
from shapely.geometry.base import BaseGeometry
from shapely.ops import unary_union
from shapely.prepared import PreparedGeometry, prep

from .instance import OrientationKey, OrientationSpec


AABB: TypeAlias = tuple[float, float, float, float]
ShapeKey: TypeAlias = tuple[int, OrientationKey]
CacheKey: TypeAlias = tuple[object, ...]
CacheValue: TypeAlias = bool | tuple[bool, bool]


@dataclass(frozen=True, slots=True)
class ShapeInfo:
    """Precomputed, reference-aligned geometry for one exact orientation tuple."""

    exact_layers: OrientationKey
    shape_key: ShapeKey
    aabb: AABB
    layer_aabbs: tuple[AABB, ...]
    layers: tuple[BaseGeometry, ...] = field(repr=False, compare=False)
    layer_prepared: tuple[PreparedGeometry, ...] = field(repr=False, compare=False)
    union: BaseGeometry = field(repr=False, compare=False)
    union_prepared: PreparedGeometry = field(repr=False, compare=False)
    area: float
    n_layers: int

    @classmethod
    def from_orientation(cls, orientation: OrientationSpec) -> "ShapeInfo":
        """Build Shapely oracles without rounding or otherwise normalizing the key."""
        ref_x = float(orientation.ref_x)
        ref_y = float(orientation.ref_y)
        polygons: list[BaseGeometry] = []
        layer_aabbs: list[AABB] = []

        for exact_layer in orientation.layers:
            aligned = tuple(
                (float(x) - ref_x, float(y) - ref_y) for x, y in exact_layer
            )
            xs = tuple(point[0] for point in aligned)
            ys = tuple(point[1] for point in aligned)
            layer_aabbs.append((min(xs), min(ys), max(xs), max(ys)))
            polygons.append(_polygon_or_empty(aligned))

        union = unary_union(polygons) if polygons else GeometryCollection()
        if not union.is_valid:
            union = union.buffer(0)
        aabb = (
            float(orientation.min_x) - ref_x,
            float(orientation.min_y) - ref_y,
            float(orientation.max_x) - ref_x,
            float(orientation.max_y) - ref_y,
        )
        return cls(
            exact_layers=orientation.layers,
            shape_key=(orientation.orientation, orientation.key),
            aabb=aabb,
            layer_aabbs=tuple(layer_aabbs),
            layers=tuple(polygons),
            layer_prepared=tuple(prep(polygon) for polygon in polygons),
            union=union,
            union_prepared=prep(union),
            area=float(union.area),
            n_layers=len(polygons),
        )


@dataclass(frozen=True, slots=True)
class GeometryStats:
    """Immutable snapshot of geometry/cache instrumentation."""

    cache_hits: int
    cache_misses: int
    cache_evictions: int
    cache_entries: int
    aabb_bypasses: int
    prepared_bypasses: int
    exact_predicates: int
    union_predicates: int
    obs_predicates: int


class GeomKernel:
    """Store exact Shapely predicate answers in a deterministic bounded LRU."""

    DEFAULT_CACHE_CAP = 1 << 18

    def __init__(self, *, cache_cap: int = DEFAULT_CACHE_CAP) -> None:
        if (
            isinstance(cache_cap, bool)
            or not isinstance(cache_cap, int)
            or cache_cap <= 0
        ):
            raise ValueError("cache_cap must be a positive integer")
        self.cache_cap = cache_cap
        self._cache: OrderedDict[CacheKey, CacheValue] = OrderedDict()
        self._cache_hits = 0
        self._cache_misses = 0
        self._cache_evictions = 0
        self._aabb_bypasses = 0
        self._prepared_bypasses = 0
        self._exact_predicates = 0
        self._union_predicates = 0
        self._obs_predicates = 0

    @property
    def stats(self) -> GeometryStats:
        return GeometryStats(
            cache_hits=self._cache_hits,
            cache_misses=self._cache_misses,
            cache_evictions=self._cache_evictions,
            cache_entries=len(self._cache),
            aabb_bypasses=self._aabb_bypasses,
            prepared_bypasses=self._prepared_bypasses,
            exact_predicates=self._exact_predicates,
            union_predicates=self._union_predicates,
            obs_predicates=self._obs_predicates,
        )

    def union_disjoint(
        self,
        shape_i: ShapeInfo,
        shape_j: ShapeInfo,
        dx: int,
        dy: int,
    ) -> bool:
        """Return whether translated union footprints intersect with zero area."""
        _require_integer_offset(dx, dy)
        translated_j_aabb = _translate_aabb(shape_j.aabb, dx, dy)
        if _aabb_disjoint(shape_i.aabb, translated_j_aabb):
            self._aabb_bypasses += 1
            return True

        key: CacheKey = ("union", shape_i.shape_key, shape_j.shape_key, dx, dy)
        cached = self._cache_lookup(key)
        if cached is not None:
            assert isinstance(cached, bool)
            return cached

        self._cache_misses += 1
        self._exact_predicates += 1
        self._union_predicates += 1
        translated_j = translate(shape_j.union, xoff=dx, yoff=dy)
        if not shape_i.union_prepared.intersects(translated_j):
            result = True
            self._prepared_bypasses += 1
        else:
            result = shape_i.union.intersection(translated_j).area <= 0.0
        self._cache_store(key, result)
        return result

    def obs(
        self,
        shape_i: ShapeInfo,
        shape_j: ShapeInfo,
        dx: int,
        dy: int,
    ) -> tuple[bool, bool]:
        """Return ``(OBS(i;j), OBS(j;i))`` with j translated relative to i."""
        _require_integer_offset(dx, dy)
        translated_j_aabb = _translate_aabb(shape_j.aabb, dx, dy)
        if _aabb_disjoint(shape_i.aabb, translated_j_aabb):
            self._aabb_bypasses += 1
            return False, False

        key: CacheKey = ("obs", shape_i.shape_key, shape_j.shape_key, dx, dy)
        cached = self._cache_lookup(key)
        if cached is not None:
            assert isinstance(cached, tuple)
            return cached

        self._cache_misses += 1
        self._exact_predicates += 1
        self._obs_predicates += 1
        translated_layers_j = tuple(
            translate(layer, xoff=dx, yoff=dy) for layer in shape_j.layers
        )
        translated_aabbs_j = tuple(
            _translate_aabb(aabb, dx, dy) for aabb in shape_j.layer_aabbs
        )
        translated_prepared_j = tuple(prep(layer) for layer in translated_layers_j)

        obs_ij = _directional_obs(
            moving_layers=shape_i.layers,
            moving_prepared=shape_i.layer_prepared,
            moving_aabbs=shape_i.layer_aabbs,
            existing_layers=translated_layers_j,
            existing_aabbs=translated_aabbs_j,
        )
        obs_ji = _directional_obs(
            moving_layers=translated_layers_j,
            moving_prepared=translated_prepared_j,
            moving_aabbs=translated_aabbs_j,
            existing_layers=shape_i.layers,
            existing_aabbs=shape_i.layer_aabbs,
        )
        result = (obs_ij, obs_ji)
        self._cache_store(key, result)
        return result

    def _cache_lookup(self, key: CacheKey) -> CacheValue | None:
        try:
            value = self._cache.pop(key)
        except KeyError:
            return None
        self._cache[key] = value
        self._cache_hits += 1
        return value

    def _cache_store(self, key: CacheKey, value: CacheValue) -> None:
        self._cache[key] = value
        if len(self._cache) > self.cache_cap:
            self._cache.popitem(last=False)
            self._cache_evictions += 1


def _polygon_or_empty(vertices: tuple[tuple[float, float], ...]) -> BaseGeometry:
    try:
        polygon: BaseGeometry = Polygon(vertices)
        if not polygon.is_valid:
            polygon = polygon.buffer(0)
        return polygon if not polygon.is_empty else GeometryCollection()
    except Exception:
        return GeometryCollection()


def _directional_obs(
    *,
    moving_layers: tuple[BaseGeometry, ...],
    moving_prepared: tuple[PreparedGeometry, ...],
    moving_aabbs: tuple[AABB, ...],
    existing_layers: tuple[BaseGeometry, ...],
    existing_aabbs: tuple[AABB, ...],
) -> bool:
    for moving_idx, (moving, prepared, moving_aabb) in enumerate(
        zip(moving_layers, moving_prepared, moving_aabbs, strict=True)
    ):
        if moving.is_empty:
            continue
        for existing_idx in range(moving_idx, len(existing_layers)):
            existing = existing_layers[existing_idx]
            if existing.is_empty or _aabb_disjoint(
                moving_aabb, existing_aabbs[existing_idx]
            ):
                continue
            if (
                prepared.intersects(existing)
                and moving.intersection(existing).area > 0.0
            ):
                return True
    return False


def _translate_aabb(aabb: AABB, dx: int, dy: int) -> AABB:
    return aabb[0] + dx, aabb[1] + dy, aabb[2] + dx, aabb[3] + dy


def _aabb_disjoint(left: AABB, right: AABB) -> bool:
    """Conservatively bypass exact work when interiors cannot overlap."""
    return (
        left[2] <= right[0]
        or right[2] <= left[0]
        or left[3] <= right[1]
        or right[3] <= left[1]
    )


def _require_integer_offset(dx: int, dy: int) -> None:
    if (
        isinstance(dx, bool)
        or isinstance(dy, bool)
        or not isinstance(dx, int)
        or not isinstance(dy, int)
    ):
        raise TypeError("geometry cache offsets must be integers")
