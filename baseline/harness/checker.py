"""Harness-side normalization of production checker-adapter results."""

from __future__ import annotations

from typing import Any

try:
    from baseline.solver.checker_adapter import CheckerResult
except ModuleNotFoundError:
    from solver.checker_adapter import CheckerResult


def checker_payload(result: CheckerResult) -> dict[str, Any]:
    return {
        "feasible": result.feasible,
        "stage": result.stage,
        "violations": list(result.violations),
        "objective": result.objective,
        "z1": result.obj1,
        "z2": result.obj2,
        "z3": result.obj3,
    }
