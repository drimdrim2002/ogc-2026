"""Local debug runner for myalgorithm.py.

Edit INSTANCE_PATH and TIMELIMIT below, then press F5 (Debug myalgorithm).
CLI args override these defaults when provided.
"""
from __future__ import annotations

import argparse
import json
import pathlib
import time

from myalgorithm import algorithm
from utils import check_feasibility

_BASELINE_DIR = pathlib.Path(__file__).resolve().parent

# --- Edit these before debugging ---------------------------------------------
# Relative to baseline/, or use an absolute path.
INSTANCE_PATH = "../alg_tester/example/train 2/prob_1.json"
TIMELIMIT = 60.0
# -----------------------------------------------------------------------------


def _resolve_instance(path: str) -> pathlib.Path:
    instance = pathlib.Path(path)
    if not instance.is_absolute():
        instance = (_BASELINE_DIR / instance).resolve()
    return instance


def main() -> None:
    parser = argparse.ArgumentParser(description="Run and check myalgorithm locally")
    parser.add_argument(
        "instance",
        nargs="?",
        default=INSTANCE_PATH,
        help="path to problem JSON (default: INSTANCE_PATH in this file)",
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

    t0 = time.time()
    solution = algorithm(prob_info, args.timelimit)
    elapsed = time.time() - t0

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
