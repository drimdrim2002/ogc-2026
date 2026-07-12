"""Monotonic global and child wall-clock budgets."""

from __future__ import annotations

import math
import time
from collections.abc import Callable


class Budget:
    def __init__(
        self,
        *,
        start_time: float,
        deadline: float,
        clock: Callable[[], float],
        reserve: float = 0.0,
        checker_samples: list[float] | None = None,
        reserve_enabled: bool = True,
    ) -> None:
        self._start_time = start_time
        self._deadline = deadline
        self._clock = clock
        self._reserve = max(0.0, reserve)
        self._checker_samples = list(checker_samples or ())
        self._reserve_enabled = reserve_enabled

    @classmethod
    def start(cls, limit: float, clock: Callable[[], float] = time.monotonic) -> "Budget":
        if isinstance(limit, bool) or not isinstance(limit, (int, float)) or not math.isfinite(float(limit)):
            raise ValueError("time limit must be finite")
        now = clock()
        duration = max(0.0, float(limit))
        budget = cls(start_time=now, deadline=now + duration, clock=clock)
        budget._recompute_reserve()
        return budget

    @property
    def limit(self) -> float:
        return self._deadline - self._start_time

    @property
    def reserve(self) -> float:
        return self._reserve

    @property
    def checker_p95(self) -> float:
        if not self._checker_samples:
            return 0.0
        ordered = sorted(self._checker_samples)
        rank = max(1, math.ceil(0.95 * len(ordered)))
        return ordered[rank - 1]

    def _recompute_reserve(self) -> None:
        if not self._reserve_enabled:
            self._reserve = 0.0
            return
        limit = self.limit
        self._reserve = min(
            60.0,
            0.25 * limit,
            max(0.05 * limit, 2.0 * self.checker_p95 + 0.1),
        )

    def record_checker_duration(self, duration: float) -> None:
        if duration >= 0 and math.isfinite(duration):
            self._checker_samples.append(float(duration))
            self._recompute_reserve()

    def remaining(self) -> float:
        return max(0.0, self._deadline - self._clock())

    def search_remaining(self) -> float:
        return max(0.0, self.remaining() - self._reserve)

    def can_start(self, predicted: float, margin: float = 0.0) -> bool:
        return self.search_remaining() + 1e-12 >= max(0.0, predicted) + max(0.0, margin)

    def child(self, cap: float) -> "Budget":
        now = self._clock()
        duration = min(max(0.0, float(cap)), self.search_remaining())
        return Budget(
            start_time=now,
            deadline=now + duration,
            clock=self._clock,
            checker_samples=self._checker_samples,
            reserve_enabled=False,
        )
