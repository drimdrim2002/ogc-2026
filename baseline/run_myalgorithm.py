"""Local-only debug runner for a required problem-instance path.

Example:
    cd baseline
    python run_myalgorithm.py ../alg_tester/example/example_B2_b10.json
"""
from __future__ import annotations

import argparse
import json
import pathlib
import time

from myalgorithm import algorithm
from utils import check_feasibility

_BASELINE_DIR = pathlib.Path(__file__).resolve().parent

TIMELIMIT = 60.0


def _resolve_instance(path: str) -> pathlib.Path:
    instance = pathlib.Path(path)
    if not instance.is_absolute():
        instance = (_BASELINE_DIR / instance).resolve()
    return instance


def main() -> None:
    parser = argparse.ArgumentParser(description="Run and check myalgorithm locally")
    parser.add_argument(
        "instance",
        help=(
            "path to problem JSON; tracked example: "
            "../alg_tester/example/example_B2_b10.json (run from baseline/)"
        ),
    )
    parser.add_argument(
        "--timelimit",
        type=float,
        default=TIMELIMIT,
        help="wall-clock time limit in seconds (default: TIMELIMIT in this file)",
    )
    args = parser.parse_args()

    instance_path = _resolve_instance(args.instance)
    if not instance_path.is_file():
        raise FileNotFoundError(f"Instance file not found: {instance_path.resolve()}")

    with open(instance_path, encoding="utf-8") as f:
        prob_info = json.load(f)

    print(f"Instance : {instance_path.resolve()}")
    print(f"Timelimit: {args.timelimit}s")
    print("Running algorithm...\n")

    t0 = time.monotonic()
    solution = algorithm(prob_info, args.timelimit)
    elapsed = time.monotonic() - t0

    result = check_feasibility(prob_info, solution)

    print(f"Elapsed  : {elapsed:.3f}s")
    print(f"Feasible : {result.get('feasible')}  (stage={result.get('stage')})")
    if result.get("feasible"):
        print(
            f"Objective: {result['objective']:.2f}  "
            f"(obj1={result['obj1']:.1f}, obj2={result['obj2']:.1f}, obj3={result['obj3']:.1f})"
        )
    else:
        for violation in result.get("violations", [])[:10]:
            print(f"  VIOLATION: {violation}")


if __name__ == "__main__":
    main()
