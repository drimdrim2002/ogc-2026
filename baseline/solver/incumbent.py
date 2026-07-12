"""Checker-verified incumbent registration and immutable return storage."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .checker_adapter import CheckerResult, official_check
from .instance import ProblemInstance
from .serialize import serialize_non_interlock
from .state import SolutionState


class IncumbentVerificationError(ValueError):
    """Raised when an initial incumbent fails the full official checker."""


class NoVerifiedIncumbentError(RuntimeError):
    """Raised when the caller requests a solution before verification."""


@dataclass(frozen=True, slots=True)
class _VerifiedRecord:
    solution: dict[str, Any]
    checker_result: CheckerResult


class VerifiedIncumbent:
    """Own only pre-serialized solutions accepted by the official checker."""

    def __init__(self, prob_info: dict[str, Any] | ProblemInstance) -> None:
        self._prob_info = (
            prob_info.raw if isinstance(prob_info, ProblemInstance) else prob_info
        )
        self._record: _VerifiedRecord | None = None
        self._last_verification: _VerifiedRecord | None = None
        self._verification_count = 0

    @property
    def has_incumbent(self) -> bool:
        return self._record is not None

    @property
    def verification_count(self) -> int:
        return self._verification_count

    @property
    def solution(self) -> dict[str, Any]:
        """Return the already serialized verified object in O(1)."""
        if self._record is None:
            raise NoVerifiedIncumbentError("no checker-verified incumbent is registered")
        return self._record.solution

    @property
    def checker_result(self) -> CheckerResult:
        if self._record is None:
            raise NoVerifiedIncumbentError("no checker-verified incumbent is registered")
        return self._record.checker_result

    @property
    def last_checker_result(self) -> CheckerResult:
        """Return telemetry for the most recent full-check attempt."""
        if self._last_verification is None:
            raise NoVerifiedIncumbentError("no checker verification has run")
        return self._last_verification.checker_result

    def register_initial(self, state: SolutionState) -> CheckerResult:
        """Full-check and atomically store the required first incumbent."""
        if self.has_incumbent:
            raise RuntimeError("initial incumbent is already registered")
        serialized, checked = self._verify(state)
        if not checked.feasible:
            details = "; ".join(checked.violations) or "official checker rejected T0"
            raise IncumbentVerificationError(details)
        self._record = _VerifiedRecord(serialized, checked)
        return checked

    def try_update(self, state: SolutionState) -> bool:
        """Store a feasible candidate only when checker objective strictly improves."""
        if not self.has_incumbent:
            self.register_initial(state)
            return True
        serialized, checked = self._verify(state)
        if not checked.feasible or checked.objective is None:
            return False
        current_objective = self.checker_result.objective
        if current_objective is None:
            raise AssertionError("verified incumbent is missing checker objective")
        tolerance = 1e-9 * max(1.0, abs(current_objective))
        if checked.objective >= current_objective - tolerance:
            return False
        self._record = _VerifiedRecord(serialized, checked)
        return True

    def _verify(self, state: SolutionState) -> tuple[dict[str, Any], CheckerResult]:
        serialized = serialize_non_interlock(state.placements.values())
        checked = official_check(self._prob_info, serialized)
        self._verification_count += 1
        self._last_verification = _VerifiedRecord(serialized, checked)
        return serialized, checked
