from __future__ import annotations

import argparse
import csv
import json
import pathlib
import re
import subprocess
import sys
import time
from contextlib import redirect_stdout
from io import StringIO
from typing import Any


_PROB_RE = re.compile(r"prob_(\d+)\.json$")
_INSTANCE_SETS = {
    "dev-10": [
        "data/train 2/prob_4.json",
        "data/train 2/prob_8.json",
        "data/train 2/prob_9.json",
        "data/train 2/prob_13.json",
        "data/train 2/prob_20.json",
        "data/train/prob_21.json",
        "data/train/prob_32.json",
        "data/train/prob_36.json",
        "data/train 2/prob_18.json",
        "data/train/prob_40.json",
    ],
    "smoke-3": [
        "data/train/prob_21.json",
        "data/train/prob_32.json",
        "data/train 2/prob_9.json",
    ],
}
_EXECUTABLE_SOLVERS = {"baseline_greedy", "myalgorithm"}


def _prob_sort_key(path: pathlib.Path) -> tuple[int, str]:
    match = _PROB_RE.search(path.name)
    return (int(match.group(1)) if match else 10**9, str(path))


def find_instance_paths(root: pathlib.Path | str) -> list[pathlib.Path]:
    root = pathlib.Path(root)
    paths: list[pathlib.Path] = []
    for rel in ("data/train", "data/train 2"):
        data_dir = root / rel
        if data_dir.is_dir():
            paths.extend(data_dir.glob("*.json"))
    return sorted(paths, key=_prob_sort_key)


def select_instance_paths(root: pathlib.Path | str, set_name: str) -> list[pathlib.Path]:
    root = pathlib.Path(root)
    if set_name in {"all", "daily-40"}:
        return find_instance_paths(root)
    return [root / rel_path for rel_path in _INSTANCE_SETS[set_name]]


def _shape_bbox(shape: dict[str, Any]) -> tuple[float, float, float, float]:
    vertices = [vertex for layer in shape["layers"] for vertex in layer]
    xs = [vertex[0] for vertex in vertices]
    ys = [vertex[1] for vertex in vertices]
    return min(xs), min(ys), max(xs), max(ys)


def compute_instance_stats(path: pathlib.Path | str) -> dict[str, Any]:
    path = pathlib.Path(path)
    prob_info = json.loads(path.read_text())
    blocks = prob_info["blocks"]
    bays = prob_info["bays"]

    orientation_counts = [len(block["shape"]) for block in blocks]
    layer_counts: list[int] = []
    vertices_per_layer: list[int] = []
    vertices_per_shape: list[int] = []
    bbox_widths: list[float] = []
    bbox_heights: list[float] = []

    for block in blocks:
        for shape in block["shape"]:
            layers = shape["layers"]
            layer_counts.append(len(layers))
            vertices_per_shape.append(sum(len(layer) for layer in layers))
            vertices_per_layer.extend(len(layer) for layer in layers)
            min_x, min_y, max_x, max_y = _shape_bbox(shape)
            bbox_widths.append(max_x - min_x)
            bbox_heights.append(max_y - min_y)

    slacks = [
        block["due_date"] - block["release_time"] - block["processing_time"]
        for block in blocks
    ]
    weights = prob_info.get("weights", {})
    bay_areas = [bay["width"] * bay["height"] for bay in bays]

    return {
        "path": str(path),
        "name": prob_info.get("name", path.stem),
        "n_blocks": len(blocks),
        "n_bays": len(bays),
        "horizon_due": max(block["due_date"] for block in blocks),
        "horizon_release_processing": max(
            block["release_time"] + block["processing_time"] for block in blocks
        ),
        "bay_width_min": min(bay["width"] for bay in bays),
        "bay_width_max": max(bay["width"] for bay in bays),
        "bay_height_min": min(bay["height"] for bay in bays),
        "bay_height_max": max(bay["height"] for bay in bays),
        "bay_area_min": min(bay_areas),
        "bay_area_max": max(bay_areas),
        "n_shapes": sum(orientation_counts),
        "orientations_min": min(orientation_counts),
        "orientations_max": max(orientation_counts),
        "orientations_avg": sum(orientation_counts) / len(orientation_counts),
        "max_layers": max(layer_counts),
        "layer_count_avg": sum(layer_counts) / len(layer_counts),
        "vertices_per_layer_min": min(vertices_per_layer),
        "vertices_per_layer_max": max(vertices_per_layer),
        "vertices_per_shape_max": max(vertices_per_shape),
        "bbox_width_avg": sum(bbox_widths) / len(bbox_widths),
        "bbox_width_max": max(bbox_widths),
        "bbox_height_avg": sum(bbox_heights) / len(bbox_heights),
        "bbox_height_max": max(bbox_heights),
        "slack_min": min(slacks),
        "slack_avg": sum(slacks) / len(slacks),
        "slack_max": max(slacks),
        "zero_slack_count": sum(1 for slack in slacks if slack == 0),
        "zero_slack_ratio": sum(1 for slack in slacks if slack == 0) / len(slacks),
        "w1": weights.get("w1"),
        "w2": weights.get("w2"),
        "w3": weights.get("w3"),
    }


def run_solver(path: pathlib.Path, solver: str, timelimit: float) -> dict[str, Any]:
    from utils import check_feasibility

    if solver == "baseline_greedy":
        import baseline_greedy

        solver_fn = baseline_greedy.greedyalgorithm
    elif solver == "myalgorithm":
        import myalgorithm

        solver_fn = myalgorithm.algorithm
    else:
        raise ValueError(f"unknown executable solver: {solver}")

    prob_info = json.loads(path.read_text())
    started = time.time()
    solver_log = StringIO()
    with redirect_stdout(solver_log):
        solution = solver_fn(prob_info, timelimit=timelimit)
    elapsed = time.time() - started
    result = check_feasibility(prob_info, solution)
    return {
        "elapsed": elapsed,
        "feasible": result.get("feasible"),
        "stage": result.get("stage"),
        "objective": result.get("objective"),
        "obj1": result.get("obj1"),
        "obj2": result.get("obj2"),
        "obj3": result.get("obj3"),
        "violations": result.get("violations", [])[:5],
        "solver_log": solver_log.getvalue(),
    }


def run_baseline(path: pathlib.Path, timelimit: float) -> dict[str, Any]:
    return run_solver(path, "baseline_greedy", timelimit)


def _git_commit(root: pathlib.Path) -> str | None:
    try:
        completed = subprocess.run(
            ["git", "-C", str(root), "rev-parse", "--short", "HEAD"],
            check=True,
            capture_output=True,
            text=True,
        )
    except (OSError, subprocess.CalledProcessError):
        return None
    return completed.stdout.strip() or None


def _write_csv(rows: list[dict[str, Any]], stream) -> None:
    if not rows:
        return
    fieldnames = sorted({key for row in rows for key in row})
    writer = csv.DictWriter(stream, fieldnames=fieldnames)
    writer.writeheader()
    writer.writerows(rows)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Summarize and benchmark OGC 2026 instances")
    parser.add_argument("--root", type=pathlib.Path, default=pathlib.Path(__file__).resolve().parents[1])
    parser.add_argument("--format", choices=["json", "csv"], default="json")
    parser.add_argument("--run-baseline", action="store_true")
    parser.add_argument("--timelimit", type=float, default=60.0)
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument(
        "--set-name",
        choices=["all", "daily-40", *_INSTANCE_SETS],
        default="all",
        help="Instance set to benchmark; default discovers all training instances.",
    )
    parser.add_argument(
        "--seed",
        type=int,
        default=None,
        help="Metadata-only seed label; current baseline solver behavior is unchanged.",
    )
    parser.add_argument(
        "--solver",
        choices=["stats_only", "baseline_greedy", "myalgorithm"],
        default=None,
        help=(
            "Solver to execute. Use stats_only to print instance statistics without "
            "running a solver. If omitted, --run-baseline selects baseline_greedy; "
            "otherwise stats_only is used."
        ),
    )
    args = parser.parse_args(argv)

    paths = select_instance_paths(args.root, args.set_name)
    if args.limit is not None:
        paths = paths[: args.limit]

    git_commit = _git_commit(args.root)
    solver_name = args.solver or ("baseline_greedy" if args.run_baseline else "stats_only")
    rows: list[dict[str, Any]] = []
    for path in paths:
        row = compute_instance_stats(path)
        row.update(
            {
                "git_commit": git_commit,
                "seed": args.seed,
                "solver": solver_name,
                "set_name": args.set_name,
            }
        )
        if solver_name in _EXECUTABLE_SOLVERS:
            row.update(run_solver(path, solver_name, args.timelimit))
        rows.append(row)

    if args.format == "csv":
        _write_csv(rows, sys.stdout)
    else:
        print(json.dumps(rows, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
