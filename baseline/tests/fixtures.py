"""Deterministic synthetic checker-contract fixtures."""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any


Point = tuple[float, float]
Layer = tuple[Point, ...]


UNIT_SQUARE: Layer = ((0.0, 0.0), (1.0, 0.0), (1.0, 1.0), (0.0, 1.0))
TWO_SQUARE: Layer = ((0.0, 0.0), (2.0, 0.0), (2.0, 2.0), (0.0, 2.0))


def block(
    *,
    layers: Iterable[Iterable[Point]] = (UNIT_SQUARE,),
    release: int = 0,
    due: int = 20,
    processing: int = 1,
    workload: int = 1,
    preferences: Iterable[int] = (100,),
    orientations: Iterable[Iterable[Iterable[Point]]] | None = None,
) -> dict[str, Any]:
    """Build a checker-shaped block without rounding any vertex coordinates."""
    raw_orientations = [layers] if orientations is None else list(orientations)
    return {
        "release_time": release,
        "due_date": due,
        "processing_time": processing,
        "workload": workload,
        "bay_preferences": list(preferences),
        "shape": [
            {
                "orientation": orient_idx,
                "layers": [
                    [[x, y] for x, y in layer]
                    for layer in orientation_layers
                ],
            }
            for orient_idx, orientation_layers in enumerate(raw_orientations)
        ],
    }


def instance(
    blocks: Iterable[dict[str, Any]],
    *,
    bays: Iterable[tuple[int, int]] = ((10, 10),),
    weights: dict[str, float] | None = None,
) -> dict[str, Any]:
    bay_list = list(bays)
    result = {
        "name": "synthetic-contract",
        "bays": [{"width": width, "height": height} for width, height in bay_list],
        "blocks": list(blocks),
        "weights": {"w1": 1.0, "w2": 1.0, "w3": 1.0},
    }
    if weights is not None:
        result["weights"] = dict(weights)
    return result


def placement(
    block_id: int,
    *,
    entry: int,
    exit: int,
    bay: int = 0,
    x: int = 0,
    y: int = 0,
    orient: int = 0,
) -> dict[str, int]:
    return {
        "block_id": block_id,
        "entry": entry,
        "exit": exit,
        "bay": bay,
        "x": x,
        "y": y,
        "orient": orient,
    }


def solution(
    placements: Iterable[dict[str, int]],
    *,
    operation_order: dict[int, list[tuple[str, int]]] | None = None,
) -> dict[str, dict[str, list[dict[str, int | str]]]]:
    """Serialize fixture placements, defaulting to EXIT-before-ENTRY ordering."""
    placement_list = list(placements)
    by_id = {item["block_id"]: item for item in placement_list}
    operations: dict[int, list[dict[str, int | str]]] = {}

    def make_op(kind: str, item: dict[str, int]) -> dict[str, int | str]:
        op: dict[str, int | str] = {
            "type": kind,
            "block_id": item["block_id"],
            "bay_id": item["bay"],
        }
        if kind == "ENTRY":
            op.update(x=item["x"], y=item["y"], orient_idx=item["orient"])
        return op

    for item in placement_list:
        operations.setdefault(item["entry"], []).append(make_op("ENTRY", item))
        operations.setdefault(item["exit"], []).append(make_op("EXIT", item))

    for time, ops in operations.items():
        if operation_order is not None and time in operation_order:
            ops[:] = [make_op(kind, by_id[block_id]) for kind, block_id in operation_order[time]]
        else:
            ops.sort(key=lambda op: (op["type"] != "EXIT", op["block_id"]))

    return {"operations": {str(time): operations[time] for time in sorted(operations)}}


def interlock_pair_instance() -> dict[str, Any]:
    """Return a pair where the guest blocks only the host's vertical path."""
    guest_lower: Layer = ((0.0, 0.0), (1.0, 0.0), (1.0, 1.0), (0.0, 1.0))
    guest_upper: Layer = ((2.0, 0.0), (4.0, 0.0), (4.0, 2.0), (2.0, 2.0))
    return instance(
        [
            block(layers=(TWO_SQUARE,)),
            block(layers=(guest_lower, guest_upper)),
        ]
    )


def nested_interlock_placements(
    *, host_exit: int = 10, guest_exit: int = 5
) -> list[dict[str, int]]:
    return [
        placement(0, entry=0, exit=host_exit, x=2),
        placement(1, entry=1, exit=guest_exit, x=0),
    ]
