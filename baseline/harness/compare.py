"""Small deterministic comparison helpers shared by measurements and gates."""

from __future__ import annotations

import math
import statistics
from typing import Iterable


def percentile(values: Iterable[float], quantile: float) -> float:
    ordered = sorted(float(value) for value in values)
    if not ordered:
        return 0.0
    position = (len(ordered) - 1) * quantile
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return ordered[lower]
    weight = position - lower
    return ordered[lower] * (1.0 - weight) + ordered[upper] * weight


def latency_summary(values: Iterable[float]) -> dict[str, float | int]:
    measured = tuple(float(value) for value in values)
    return {
        "count": len(measured),
        "median_seconds": statistics.median(measured) if measured else 0.0,
        "p95_seconds": percentile(measured, 0.95),
    }
