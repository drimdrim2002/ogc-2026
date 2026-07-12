"""Single checker boundary used by production solver code."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

try:
    from utils import check_feasibility as _check_feasibility
except ModuleNotFoundError as exc:
    if exc.name != "utils":
        raise
    from baseline.utils import check_feasibility as _check_feasibility


@dataclass(frozen=True, slots=True)
class CheckerResult:
    """Stable, immutable view of an official checker decision."""

    feasible: bool
    stage: int
    violations: tuple[str, ...]
    objective: float | None
    obj1: float | None
    obj2: float | None
    obj3: float | None


def official_check(prob_info: dict[str, Any], solution: dict[str, Any]) -> CheckerResult:
    """Run the unmodified official checker and normalize its result."""
    raw = _check_feasibility(prob_info, solution)
    return CheckerResult(
        feasible=bool(raw.get("feasible", False)),
        stage=int(raw.get("stage", 0)),
        violations=tuple(str(item) for item in raw.get("violations", ())),
        objective=_optional_float(raw.get("objective")),
        obj1=_optional_float(raw.get("obj1")),
        obj2=_optional_float(raw.get("obj2")),
        obj3=_optional_float(raw.get("obj3")),
    )


def _optional_float(value: Any) -> float | None:
    return None if value is None else float(value)
