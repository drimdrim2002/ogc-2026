"""Monotonic deadline accounting with protected return-time reserve."""

from __future__ import annotations

from collections.abc import Callable
import math
import time


class BudgetExpired(TimeoutError):
    """Raised by a cooperative checkpoint after the work deadline."""


def deadline_reserve(timelimit: float) -> float:
    """Return the S0 reserve without consuming tiny budgets completely."""
    limit = _valid_timelimit(timelimit)
    if limit < 3.0:
        return min(max(0.05 * limit, 0.05), max(limit - 0.05, 0.05))
    return min(max(0.05 * limit, 3.0), 60.0)


class Budget:
    """Track a soft work deadline and the caller-provided hard deadline."""

    __slots__ = (
        "timelimit",
        "reserve",
        "started_at",
        "deadline",
        "hard_deadline",
        "_clock",
    )

    def __init__(
        self,
        timelimit: float,
        *,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.timelimit = _valid_timelimit(timelimit)
        self.reserve = deadline_reserve(self.timelimit)
        self._clock = clock
        self.started_at = float(clock())
        self.deadline = self.started_at + self.timelimit - self.reserve
        self.hard_deadline = self.started_at + self.timelimit

    @property
    def remaining(self) -> float:
        """Return seconds available for optional work before the reserve."""
        return max(0.0, self.deadline - float(self._clock()))

    @property
    def hard_remaining(self) -> float:
        """Return seconds remaining until the caller's nominal limit."""
        return max(0.0, self.hard_deadline - float(self._clock()))

    @property
    def expired(self) -> bool:
        return float(self._clock()) >= self.deadline

    def checkpoint(self, label: str = "solver") -> None:
        """Cooperatively stop optional work once the protected reserve begins."""
        if self.expired:
            raise BudgetExpired(f"{label} reached the protected work deadline")

    def stage_allowance(self, fraction: float = 1.0, *, cap: float | None = None) -> float:
        """Allocate a bounded fraction of currently available work time."""
        if isinstance(fraction, bool) or not math.isfinite(fraction):
            raise ValueError("fraction must be a finite number")
        if fraction < 0.0 or fraction > 1.0:
            raise ValueError("fraction must be between 0 and 1")
        allowance = self.remaining * fraction
        if cap is not None:
            if isinstance(cap, bool) or not math.isfinite(cap) or cap < 0.0:
                raise ValueError("cap must be a finite non-negative number")
            allowance = min(allowance, cap)
        return allowance


def _valid_timelimit(timelimit: float) -> float:
    if isinstance(timelimit, bool):
        raise ValueError("timelimit must be a finite non-negative number")
    try:
        limit = float(timelimit)
    except (TypeError, ValueError) as exc:
        raise ValueError("timelimit must be a finite non-negative number") from exc
    if not math.isfinite(limit) or limit < 0.0:
        raise ValueError("timelimit must be a finite non-negative number")
    return limit
