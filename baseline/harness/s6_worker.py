"""Private subprocess worker for bounded S6-05 solver stress cases."""

from __future__ import annotations

import argparse
from copy import deepcopy
from dataclasses import asdict
import hashlib
import json
from pathlib import Path
import sys
from typing import Any

from baseline.solver.checker_adapter import official_check
from baseline.solver.config import DEFAULT_CONFIG
from baseline.solver.entry import solve


ENTRY_FAULTS = frozenset(
    {"after_incumbent", "during_constructor", "during_retime", "during_alns"}
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--instance", required=True)
    parser.add_argument("--timelimit", type=float, required=True)
    parser.add_argument("--seed", type=int, required=True)
    parser.add_argument("--fault", required=True)
    args = parser.parse_args(argv)
    try:
        prob_info = json.loads(Path(args.instance).read_text(encoding="utf-8"))
        telemetry: dict[str, Any] = {}
        entry_fault = args.fault if args.fault in ENTRY_FAULTS else None
        alns_fault = "full_check" if args.fault == "alns_full_check" else None
        if args.fault not in {"none", "alns_full_check", *ENTRY_FAULTS}:
            raise ValueError(f"unsupported S6 worker fault: {args.fault}")
        solution = solve(
            prob_info,
            args.timelimit,
            _seed=args.seed,
            _fault=entry_fault,
            _alns_fault=alns_fault,
            _telemetry=telemetry,
        )
        checked = official_check(prob_info, deepcopy(solution))
        encoded = json.dumps(
            solution,
            sort_keys=False,
            separators=(",", ":"),
        ).encode()
        print(
            json.dumps(
                {
                    "status": "passed" if checked.feasible and checked.stage == 5 else "checker_failed",
                    "solution": solution,
                    "solution_sha256": hashlib.sha256(encoded).hexdigest(),
                    "checker": {
                        "feasible": checked.feasible,
                        "stage": checked.stage,
                        "violations": list(checked.violations),
                        "objective": checked.objective,
                        "obj1": checked.obj1,
                        "obj2": checked.obj2,
                        "obj3": checked.obj3,
                    },
                    "telemetry": telemetry,
                    "selected_config": asdict(DEFAULT_CONFIG),
                },
                sort_keys=False,
                separators=(",", ":"),
            )
        )
        return 0 if checked.feasible and checked.stage == 5 else 3
    except Exception as exc:
        print(
            json.dumps(
                {
                    "status": "crashed",
                    "exception": f"{type(exc).__name__}: {exc}",
                },
                sort_keys=True,
                separators=(",", ":"),
            )
        )
        return 5


if __name__ == "__main__":
    sys.exit(main())
