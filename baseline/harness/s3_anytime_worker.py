"""Algorithm Tester-style public-entry worker for S3 deadline-fill evidence."""

from __future__ import annotations

import argparse
from dataclasses import asdict
import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile
import time
from typing import Any


PROFILE = "s3-anytime-fill-candidate"


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--timelimit", type=float, required=True)
    parser.add_argument("--seed", type=int, required=True)
    parser.add_argument("--algorithm-root", type=Path, required=True)
    parser.add_argument("--profile", choices=(PROFILE,), required=True)
    return parser


def _solution_sha256(solution: dict[str, Any]) -> str:
    encoded = json.dumps(
        solution, sort_keys=True, separators=(",", ":")
    ).encode()
    return hashlib.sha256(encoded).hexdigest()


def _compact_telemetry(telemetry: dict[str, Any]) -> dict[str, Any]:
    omitted = {"alns_epoch_solutions", "alns_input_solution"}
    return {key: value for key, value in telemetry.items() if key not in omitted}


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        prob_info = json.loads(args.input.read_text(encoding="utf-8"))
        algorithm_root = args.algorithm_root.resolve()
        with tempfile.TemporaryDirectory(prefix="fable-s3-anytime-worker-") as directory:
            os.chdir(directory)
            sys.path.insert(0, str(algorithm_root))
            import myalgorithm
            from solver import entry
            from solver.checker_adapter import official_check

            telemetry: dict[str, Any] = {}
            native_solve = entry.solve

            def configured_solve(problem: dict[str, Any], timelimit: float = 60):
                return native_solve(
                    problem,
                    timelimit,
                    _seed=args.seed,
                    _alns=True,
                    _s3_anytime_fill=True,
                    _telemetry=telemetry,
                )

            entry.solve = configured_solve
            started = time.monotonic()
            solution = myalgorithm.algorithm(prob_info, args.timelimit)
            algorithm_wall_seconds = time.monotonic() - started
            checker_started = time.monotonic()
            checked = official_check(prob_info, solution)
            checker_wall_seconds = time.monotonic() - checker_started
            solution_sha256 = _solution_sha256(solution)
            algorithm_module = str(
                Path(sys.modules[myalgorithm.__name__].__file__).resolve()
            )

        print(
            json.dumps(
                {
                    "worker_pid": os.getpid(),
                    "checker": asdict(checked),
                    "solution_sha256": solution_sha256,
                    "checker_solution_sha256": solution_sha256,
                    "algorithm_wall_seconds": algorithm_wall_seconds,
                    "checker_wall_seconds": checker_wall_seconds,
                    "telemetry": _compact_telemetry(telemetry),
                    "selected_config": {
                        "s3_anytime_fill": True,
                        "entry_mode": "public",
                    },
                    "profile": args.profile,
                    "algorithm_module": algorithm_module,
                    "algorithm_root": str(algorithm_root),
                    "temporary_cwd_cleaned": not Path(directory).exists(),
                },
                sort_keys=True,
                separators=(",", ":"),
            )
        )
        return 0
    except Exception as exc:
        print(
            json.dumps(
                {
                    "exception": f"{type(exc).__name__}: {exc}",
                    "worker_pid": os.getpid(),
                },
                sort_keys=True,
            )
        )
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
