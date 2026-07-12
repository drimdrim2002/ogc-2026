"""Canonical serialization for non-interlock solver states."""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from .state import Placement


def serialize_non_interlock(placements: Iterable[Placement]) -> dict[str, Any]:
    """Build the sole checker-facing non-interlock operations representation.

    Dates are emitted as canonical integer strings.  At a shared date every
    EXIT precedes every ENTRY, and operations of the same type are ordered by
    block ID.
    """
    placement_list = list(placements)
    block_ids = [placement.block_id for placement in placement_list]
    if len(set(block_ids)) != len(block_ids):
        raise ValueError("placements must contain each block_id at most once")

    buckets: dict[int, list[dict[str, int | str]]] = {}
    for placement in placement_list:
        _require_integer_placement(placement)
        buckets.setdefault(placement.entry, []).append(
            {
                "type": "ENTRY",
                "block_id": placement.block_id,
                "bay_id": placement.bay_id,
                "x": placement.x,
                "y": placement.y,
                "orient_idx": placement.orient_idx,
            }
        )
        buckets.setdefault(placement.exit, []).append(
            {
                "type": "EXIT",
                "block_id": placement.block_id,
                "bay_id": placement.bay_id,
            }
        )

    operations: dict[str, list[dict[str, int | str]]] = {}
    for date in sorted(buckets):
        operations[str(date)] = sorted(
            buckets[date],
            key=lambda operation: (
                operation["type"] != "EXIT",
                operation["block_id"],
            ),
        )
    return {"operations": operations}


def _require_integer_placement(placement: Placement) -> None:
    for field in (
        "block_id",
        "bay_id",
        "x",
        "y",
        "orient_idx",
        "entry",
        "exit",
    ):
        value = getattr(placement, field)
        if isinstance(value, bool) or not isinstance(value, int):
            raise TypeError(f"placement {field} must be an integer")
