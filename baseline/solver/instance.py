"""Immutable instance parsing and integer placement ranges."""

from __future__ import annotations

import copy
import math
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any, Mapping


class InstanceError(ValueError):
    """Raised when instance data violates a foundation contract."""


class NoValidPlacement(InstanceError):
    """Raised when a block cannot be placed at an integer reference point."""


def _freeze(value: Any) -> Any:
    if isinstance(value, Mapping):
        return MappingProxyType({key: _freeze(item) for key, item in value.items()})
    if isinstance(value, (list, tuple)):
        return tuple(_freeze(item) for item in value)
    return value


@dataclass(frozen=True, slots=True)
class IntegerRange:
    lower: int
    upper: int

    def __post_init__(self) -> None:
        if self.lower > self.upper:
            raise ValueError("empty integer range")


@dataclass(frozen=True, slots=True)
class ReferenceRange:
    x: IntegerRange
    y: IntegerRange

    @property
    def anchor(self) -> tuple[int, int]:
        return self.x.lower, self.y.lower


@dataclass(frozen=True, slots=True)
class BayInfo:
    index: int
    width: int
    height: int

    @property
    def area(self) -> float:
        return float(self.width * self.height)


Coordinate = tuple[float, float]
Layer = tuple[Coordinate, ...]


@dataclass(frozen=True, slots=True)
class OrientationInfo:
    index: int
    raw_layers: tuple[Layer, ...]
    xmin: float
    ymin: float
    xmax: float
    ymax: float

    def integer_range(self, bay: BayInfo) -> ReferenceRange | None:
        x_lower = math.ceil(-self.xmin)
        x_upper = math.floor(bay.width - self.xmax)
        y_lower = math.ceil(-self.ymin)
        y_upper = math.floor(bay.height - self.ymax)
        if x_lower > x_upper or y_lower > y_upper:
            return None
        return ReferenceRange(
            IntegerRange(int(x_lower), int(x_upper)),
            IntegerRange(int(y_lower), int(y_upper)),
        )


@dataclass(frozen=True, slots=True)
class BlockInfo:
    index: int
    release_time: int
    due_date: int
    processing_time: int
    workload: float
    bay_preferences: tuple[float, ...]
    orientations: tuple[OrientationInfo, ...]
    fitting_options: tuple[tuple[int, int, ReferenceRange], ...]

    @property
    def dwell(self) -> int:
        return max(self.processing_time, 1)


@dataclass(frozen=True, slots=True)
class ObjectiveWeights:
    w1: float
    w2: float
    w3: float


@dataclass(frozen=True, slots=True)
class Instance:
    name: str
    bays: tuple[BayInfo, ...]
    blocks: tuple[BlockInfo, ...]
    weights: ObjectiveWeights
    raw: Mapping[str, Any]

    def block(self, block_id: int) -> BlockInfo:
        return self.blocks[block_id]

    def bay(self, bay_id: int) -> BayInfo:
        return self.bays[bay_id]


def _number(raw: Mapping[str, Any], key: str, *, integral: bool = False) -> float | int:
    try:
        value = raw[key]
    except KeyError as exc:
        raise InstanceError(f"missing required key {key!r}") from exc
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(float(value)):
        raise InstanceError(f"{key} must be a finite number")
    if integral:
        if int(value) != value:
            raise InstanceError(f"{key} must be an integer")
        return int(value)
    return float(value)


def _parse_orientation(raw: Mapping[str, Any], index: int) -> OrientationInfo:
    try:
        raw_layers = raw["layers"]
    except KeyError as exc:
        raise InstanceError(f"orientation {index} is missing layers") from exc
    if not isinstance(raw_layers, (list, tuple)) or not raw_layers:
        raise InstanceError(f"orientation {index} must have at least one layer")
    layers: list[Layer] = []
    for layer_index, raw_layer in enumerate(raw_layers):
        if not isinstance(raw_layer, (list, tuple)) or not raw_layer:
            raise InstanceError(f"orientation {index} layer {layer_index} is empty")
        vertices: list[Coordinate] = []
        for raw_vertex in raw_layer:
            if not isinstance(raw_vertex, (list, tuple)) or len(raw_vertex) != 2:
                raise InstanceError("vertices must be coordinate pairs")
            x, y = raw_vertex
            if any(isinstance(item, bool) or not isinstance(item, (int, float)) for item in (x, y)):
                raise InstanceError("vertex coordinates must be numeric")
            if not math.isfinite(float(x)) or not math.isfinite(float(y)):
                raise InstanceError("vertex coordinates must be finite")
            vertices.append((float(x), float(y)))
        layers.append(tuple(vertices))
    ref_x, ref_y = layers[0][0]
    local_layers = tuple(
        tuple((x - ref_x, y - ref_y) for x, y in layer)
        for layer in layers
    )
    vertices = tuple(vertex for layer in local_layers for vertex in layer)
    return OrientationInfo(
        index=index,
        raw_layers=local_layers,
        xmin=min(x for x, _ in vertices),
        ymin=min(y for _, y in vertices),
        xmax=max(x for x, _ in vertices),
        ymax=max(y for _, y in vertices),
    )


def parse_instance(raw: Mapping[str, Any]) -> Instance:
    if not isinstance(raw, Mapping):
        raise InstanceError("prob_info must be a mapping")
    copied = copy.deepcopy(dict(raw))
    raw_bays = copied.get("bays")
    raw_blocks = copied.get("blocks")
    if not isinstance(raw_bays, list) or not raw_bays:
        raise InstanceError("bays must be a non-empty list")
    if not isinstance(raw_blocks, list):
        raise InstanceError("blocks must be a list")

    bays: list[BayInfo] = []
    for index, raw_bay in enumerate(raw_bays):
        if not isinstance(raw_bay, Mapping):
            raise InstanceError(f"bay {index} must be a mapping")
        width = _number(raw_bay, "width", integral=True)
        height = _number(raw_bay, "height", integral=True)
        if width <= 0 or height <= 0:
            raise InstanceError(f"bay {index} dimensions must be positive")
        bays.append(BayInfo(index, width, height))

    blocks: list[BlockInfo] = []
    for index, raw_block in enumerate(raw_blocks):
        if not isinstance(raw_block, Mapping):
            raise InstanceError(f"block {index} must be a mapping")
        raw_shapes = raw_block.get("shape")
        if not isinstance(raw_shapes, list) or not raw_shapes:
            raise InstanceError(f"block {index} must have orientations")
        orientations = tuple(
            _parse_orientation(raw_orientation, orientation_index)
            for orientation_index, raw_orientation in enumerate(raw_shapes)
        )
        raw_preferences = raw_block.get("bay_preferences")
        if not isinstance(raw_preferences, list) or len(raw_preferences) != len(bays):
            raise InstanceError(f"block {index} must have one preference per bay")
        preferences = tuple(float(value) for value in raw_preferences)
        if not all(math.isfinite(value) for value in preferences):
            raise InstanceError(f"block {index} preferences must be finite")
        fitting_options = tuple(
            (bay.index, orientation.index, reference_range)
            for bay in bays
            for orientation in orientations
            if (reference_range := orientation.integer_range(bay)) is not None
        )
        if not fitting_options:
            raise NoValidPlacement(f"block {index} has no valid integer placement")
        release = _number(raw_block, "release_time", integral=True)
        due = _number(raw_block, "due_date", integral=True)
        processing = _number(raw_block, "processing_time", integral=True)
        if processing < 0:
            raise InstanceError(f"block {index} processing_time must be non-negative")
        workload = _number(raw_block, "workload")
        blocks.append(
            BlockInfo(
                index=index,
                release_time=release,
                due_date=due,
                processing_time=processing,
                workload=workload,
                bay_preferences=preferences,
                orientations=orientations,
                fitting_options=fitting_options,
            )
        )

    raw_weights = copied.get("weights", {})
    if not isinstance(raw_weights, Mapping):
        raise InstanceError("weights must be a mapping")
    weights = ObjectiveWeights(
        float(raw_weights.get("w1", 1.0)),
        float(raw_weights.get("w2", 1.0)),
        float(raw_weights.get("w3", 1.0)),
    )
    if not all(math.isfinite(value) for value in (weights.w1, weights.w2, weights.w3)):
        raise InstanceError("weights must be finite")
    return Instance(
        name=str(copied.get("name", "")),
        bays=tuple(bays),
        blocks=tuple(blocks),
        weights=weights,
        raw=_freeze(copied),
    )
