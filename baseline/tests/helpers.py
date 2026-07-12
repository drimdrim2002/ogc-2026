from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Iterable

from utils import check_feasibility


def rectangle(x0: float, y0: float, x1: float, y1: float) -> list[list[float]]:
    return [[x0, y0], [x1, y0], [x1, y1], [x0, y1]]


def block(
    *,
    release: int = 0,
    due: int = 10,
    processing: int = 2,
    workload: float = 1.0,
    preferences: Iterable[float] = (10.0,),
    orientations: list[list[list[list[float]]]] | None = None,
) -> dict:
    if orientations is None:
        orientations = [[rectangle(0, 0, 2, 2)]]
    return {
        "release_time": release,
        "due_date": due,
        "processing_time": processing,
        "workload": workload,
        "bay_preferences": list(preferences),
        "shape": [
            {"orientation": index, "layers": layers}
            for index, layers in enumerate(orientations)
        ],
    }


def instance(
    blocks: list[dict],
    *,
    bays: Iterable[tuple[int, int]] = ((12, 12),),
    weights: tuple[float, float, float] = (3.0, 5.0, 7.0),
    name: str = "synthetic",
) -> dict:
    bay_list = [{"width": width, "height": height} for width, height in bays]
    for item in blocks:
        if len(item["bay_preferences"]) != len(bay_list):
            raise ValueError("each block needs one preference per bay")
    return {
        "name": name,
        "bays": bay_list,
        "blocks": copy.deepcopy(blocks),
        "weights": {"w1": weights[0], "w2": weights[1], "w3": weights[2]},
    }


def placement_ops(
    placements: Iterable[tuple[int, int, int, int, int, int, int]],
) -> dict:
    """Build chronological EXIT-first operations.

    Tuples are (block_id, bay_id, orient_idx, x, y, entry, exit).
    """
    events: dict[int, list[dict]] = {}
    for block_id, bay_id, orient_idx, x, y, entry, exit_time in placements:
        events.setdefault(entry, []).append(
            {
                "type": "ENTRY",
                "block_id": block_id,
                "bay_id": bay_id,
                "x": x,
                "y": y,
                "orient_idx": orient_idx,
            }
        )
        events.setdefault(exit_time, []).append(
            {"type": "EXIT", "block_id": block_id, "bay_id": bay_id}
        )
    operations: dict[str, list[dict]] = {}
    for date in sorted(events):
        operations[str(date)] = sorted(
            events[date], key=lambda op: (op["type"] != "EXIT", op["block_id"])
        )
    return {"operations": operations}


def load_example() -> dict:
    path = Path(__file__).resolve().parents[2] / "alg_tester/example/example_B2_b10.json"
    return json.loads(path.read_text())


def checker(prob_info: dict, solution: dict) -> dict:
    return check_feasibility(prob_info, solution)
