"""Immutable parsing and physical-fit preflight for problem instances."""

from __future__ import annotations

import math
from dataclasses import dataclass
from numbers import Real
from typing import Any


Coordinate = int | float
Vertex = tuple[Coordinate, Coordinate]
LayerKey = tuple[Vertex, ...]
OrientationKey = tuple[LayerKey, ...]


class InstanceFormatError(ValueError):
    """Raised when an instance does not satisfy the checker-facing schema."""


class UnsolvableInstanceError(ValueError):
    """Raised when a valid block has no fitting bay/orientation combination."""


@dataclass(frozen=True, slots=True)
class BaySpec:
    bay_id: int
    width: int
    height: int

    @property
    def area(self) -> int:
        return self.width * self.height


@dataclass(frozen=True, slots=True)
class OrientationSpec:
    orient_idx: int
    orientation: int
    layers: OrientationKey
    min_x: Coordinate
    min_y: Coordinate
    max_x: Coordinate
    max_y: Coordinate
    ref_x: Coordinate
    ref_y: Coordinate

    @property
    def key(self) -> OrientationKey:
        """Return the exact input vertex tuple without coordinate rounding."""
        return self.layers

    def integer_position_ranges(self, bay: BaySpec) -> tuple[range, range] | None:
        """Return all integer reference-coordinate ranges that fit this bay."""
        x_lower = math.ceil(self.ref_x - self.min_x)
        x_upper = math.floor(bay.width + self.ref_x - self.max_x)
        y_lower = math.ceil(self.ref_y - self.min_y)
        y_upper = math.floor(bay.height + self.ref_y - self.max_y)
        if x_lower > x_upper or y_lower > y_upper:
            return None
        return range(x_lower, x_upper + 1), range(y_lower, y_upper + 1)

    def fits(self, bay: BaySpec) -> bool:
        return self.integer_position_ranges(bay) is not None


@dataclass(frozen=True, slots=True)
class BlockSpec:
    block_id: int
    release_time: int
    due_date: int
    processing_time: int
    workload: float
    bay_preferences: tuple[float, ...]
    orientations: tuple[OrientationSpec, ...]

    @property
    def dwell(self) -> int:
        return max(self.processing_time, 1)

    @property
    def slack(self) -> int:
        return self.due_date - self.release_time - self.dwell

    @property
    def orientation_keys(self) -> tuple[OrientationKey, ...]:
        return tuple(orientation.key for orientation in self.orientations)


FitMatrix = tuple[tuple[tuple[bool, ...], ...], ...]


@dataclass(frozen=True, slots=True)
class ProblemInstance:
    name: str
    bays: tuple[BaySpec, ...]
    blocks: tuple[BlockSpec, ...]
    weights: tuple[float, float, float]
    fit_matrix: FitMatrix
    raw: dict[str, Any]

    @classmethod
    def parse(cls, prob_info: dict[str, Any]) -> "ProblemInstance":
        if not isinstance(prob_info, dict):
            raise InstanceFormatError("prob_info must be a dict")
        raw_bays = _required_list(prob_info, "bays")
        raw_blocks = _required_list(prob_info, "blocks")
        if not raw_bays:
            raise InstanceFormatError("prob_info['bays'] must not be empty")

        bays = tuple(_parse_bay(raw, bay_id) for bay_id, raw in enumerate(raw_bays))
        blocks = tuple(
            _parse_block(raw, block_id, len(bays))
            for block_id, raw in enumerate(raw_blocks)
        )
        fit_matrix: FitMatrix = tuple(
            tuple(
                tuple(orientation.fits(bay) for orientation in block.orientations)
                for bay in bays
            )
            for block in blocks
        )
        raw_weights = prob_info.get("weights", {})
        if not isinstance(raw_weights, dict):
            raise InstanceFormatError("prob_info['weights'] must be a dict when present")
        weights = tuple(
            _finite_number(raw_weights.get(key, 1.0), f"weights.{key}")
            for key in ("w1", "w2", "w3")
        )
        return cls(
            name=str(prob_info.get("name", "")),
            bays=bays,
            blocks=blocks,
            weights=(float(weights[0]), float(weights[1]), float(weights[2])),
            fit_matrix=fit_matrix,
            raw=prob_info,
        )

    def fitting_orientations(self, block_id: int, bay_id: int) -> tuple[int, ...]:
        return tuple(
            orient_idx
            for orient_idx, fits in enumerate(self.fit_matrix[block_id][bay_id])
            if fits
        )

    def assert_solvable_fit(self) -> None:
        unfittable = [
            block.block_id
            for block, block_fit in zip(self.blocks, self.fit_matrix, strict=True)
            if not any(fits for bay_fit in block_fit for fits in bay_fit)
        ]
        if unfittable:
            labels = ", ".join(f"block {block_id}" for block_id in unfittable)
            raise UnsolvableInstanceError(
                f"no in-bound integer placement exists for {labels} in any bay/orientation"
            )


def _parse_bay(raw: Any, bay_id: int) -> BaySpec:
    if not isinstance(raw, dict):
        raise InstanceFormatError(f"bays[{bay_id}] must be a dict")
    width = _positive_int(raw.get("width"), f"bays[{bay_id}].width")
    height = _positive_int(raw.get("height"), f"bays[{bay_id}].height")
    return BaySpec(bay_id=bay_id, width=width, height=height)


def _parse_block(raw: Any, block_id: int, bay_count: int) -> BlockSpec:
    prefix = f"blocks[{block_id}]"
    if not isinstance(raw, dict):
        raise InstanceFormatError(f"{prefix} must be a dict")
    release_time = _int_value(raw.get("release_time"), f"{prefix}.release_time")
    due_date = _int_value(raw.get("due_date"), f"{prefix}.due_date")
    processing_time = _int_value(raw.get("processing_time"), f"{prefix}.processing_time")
    if processing_time < 0:
        raise InstanceFormatError(f"{prefix}.processing_time must be non-negative")
    workload = float(_finite_number(raw.get("workload"), f"{prefix}.workload"))

    raw_preferences = _required_list(raw, "bay_preferences", prefix=prefix)
    if len(raw_preferences) != bay_count:
        raise InstanceFormatError(
            f"{prefix}.bay_preferences has {len(raw_preferences)} values for {bay_count} bays"
        )
    bay_preferences = tuple(
        float(_finite_number(value, f"{prefix}.bay_preferences[{index}]"))
        for index, value in enumerate(raw_preferences)
    )

    raw_shapes = _required_list(raw, "shape", prefix=prefix)
    if not raw_shapes:
        raise InstanceFormatError(f"{prefix}.shape must not be empty")
    orientations = tuple(
        _parse_orientation(shape, block_id, orient_idx)
        for orient_idx, shape in enumerate(raw_shapes)
    )
    return BlockSpec(
        block_id=block_id,
        release_time=release_time,
        due_date=due_date,
        processing_time=processing_time,
        workload=workload,
        bay_preferences=bay_preferences,
        orientations=orientations,
    )


def _parse_orientation(raw: Any, block_id: int, orient_idx: int) -> OrientationSpec:
    prefix = f"blocks[{block_id}].shape[{orient_idx}]"
    if not isinstance(raw, dict):
        raise InstanceFormatError(f"{prefix} must be a dict")
    orientation = _int_value(raw.get("orientation"), f"{prefix}.orientation")
    raw_layers = _required_list(raw, "layers", prefix=prefix)
    if not raw_layers:
        raise InstanceFormatError(f"{prefix}.layers must not be empty")

    layers: list[LayerKey] = []
    for layer_idx, raw_layer in enumerate(raw_layers):
        if not isinstance(raw_layer, list) or len(raw_layer) < 3:
            raise InstanceFormatError(f"{prefix}.layers[{layer_idx}] must contain at least 3 vertices")
        vertices: list[Vertex] = []
        for vertex_idx, raw_vertex in enumerate(raw_layer):
            vertex_path = f"{prefix}.layers[{layer_idx}][{vertex_idx}]"
            if not isinstance(raw_vertex, list) or len(raw_vertex) != 2:
                raise InstanceFormatError(f"{vertex_path} must be a two-coordinate list")
            x = _finite_number(raw_vertex[0], f"{vertex_path}[0]")
            y = _finite_number(raw_vertex[1], f"{vertex_path}[1]")
            vertices.append((x, y))
        layers.append(tuple(vertices))

    exact_layers = tuple(layers)
    all_vertices = tuple(vertex for layer in exact_layers for vertex in layer)
    ref_x, ref_y = exact_layers[0][0]
    return OrientationSpec(
        orient_idx=orient_idx,
        orientation=orientation,
        layers=exact_layers,
        min_x=min(vertex[0] for vertex in all_vertices),
        min_y=min(vertex[1] for vertex in all_vertices),
        max_x=max(vertex[0] for vertex in all_vertices),
        max_y=max(vertex[1] for vertex in all_vertices),
        ref_x=ref_x,
        ref_y=ref_y,
    )


def _required_list(raw: dict[str, Any], key: str, *, prefix: str = "prob_info") -> list[Any]:
    value = raw.get(key)
    if not isinstance(value, list):
        raise InstanceFormatError(f"{prefix}['{key}'] must be a list")
    return value


def _positive_int(value: Any, path: str) -> int:
    result = _int_value(value, path)
    if result <= 0:
        raise InstanceFormatError(f"{path} must be positive")
    return result


def _int_value(value: Any, path: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise InstanceFormatError(f"{path} must be an integer")
    return value


def _finite_number(value: Any, path: str) -> Coordinate:
    if isinstance(value, bool) or not isinstance(value, Real):
        raise InstanceFormatError(f"{path} must be a number")
    if not math.isfinite(float(value)):
        raise InstanceFormatError(f"{path} must be finite")
    return value
