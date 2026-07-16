"""Checker-verified incumbent registration and immutable return storage."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from typing import Any

from .checker_adapter import CheckerResult, official_check
from .instance import ProblemInstance
from .serialize import serialize_non_interlock
from .state import Placement, SolutionState


class IncumbentVerificationError(ValueError):
    """Raised when an initial incumbent fails the full official checker."""


class NoVerifiedIncumbentError(RuntimeError):
    """Raised when the caller requests a solution before verification."""


@dataclass(frozen=True, slots=True)
class _VerifiedRecord:
    solution: dict[str, Any]
    checker_result: CheckerResult
    placements: tuple[Placement, ...]


@dataclass(frozen=True, slots=True)
class VerifiedCheckpoint:
    """Immutable, instance-bound representation of a verified incumbent."""

    instance_sha256: str
    solution_json: bytes
    solution_sha256: str
    checker_result: CheckerResult
    placements: tuple[Placement, ...]
    verification_count: int

    def solution_copy(self) -> dict[str, Any]:
        """Materialize a caller-owned solution after snapshot integrity checks."""
        if hashlib.sha256(self.solution_json).hexdigest() != self.solution_sha256:
            raise ValueError("verified checkpoint solution hash mismatch")
        solution = serialize_non_interlock(self.placements)
        if _canonical_json(solution) != self.solution_json:
            raise ValueError("verified checkpoint placement snapshot mismatch")
        return solution


class VerifiedIncumbent:
    """Own only pre-serialized solutions accepted by the official checker."""

    def __init__(self, prob_info: dict[str, Any] | ProblemInstance) -> None:
        self._instance = (
            prob_info
            if isinstance(prob_info, ProblemInstance)
            else ProblemInstance.parse(prob_info)
        )
        self._prob_info = self._instance.raw
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

    def snapshot_state(
        self,
        template: SolutionState | None = None,
    ) -> SolutionState:
        """Reconstruct an independent state from the accepted placements."""
        if self._record is None:
            raise NoVerifiedIncumbentError("no checker-verified incumbent is registered")
        if template is not None and template.instance is not self._instance:
            raise ValueError("snapshot template belongs to a different problem instance")
        state = SolutionState(
            self._instance,
            geom=None if template is None else template.geom,
            shape_catalog=None if template is None else template.shape_catalog,
        )
        for placement in self._record.placements:
            state.place(placement)
        state.assert_invariants()
        return state

    def export_checkpoint(self) -> VerifiedCheckpoint:
        """Export frozen bytes and placement value objects without rechecking."""
        if self._record is None:
            raise NoVerifiedIncumbentError("no checker-verified incumbent is registered")
        solution = serialize_non_interlock(self._record.placements)
        solution_json = _canonical_json(solution)
        return VerifiedCheckpoint(
            instance_sha256=_instance_sha256(self._instance),
            solution_json=solution_json,
            solution_sha256=hashlib.sha256(solution_json).hexdigest(),
            checker_result=self._record.checker_result,
            placements=self._record.placements,
            verification_count=self._verification_count,
        )

    @classmethod
    def from_checkpoint(
        cls,
        prob_info: dict[str, Any] | ProblemInstance,
        checkpoint: VerifiedCheckpoint,
    ) -> VerifiedIncumbent:
        """Recheck a checkpoint and create a fresh incumbent owner."""
        if not isinstance(checkpoint, VerifiedCheckpoint):
            raise TypeError("checkpoint must be an immutable verified checkpoint")
        instance = (
            prob_info
            if isinstance(prob_info, ProblemInstance)
            else ProblemInstance.parse(prob_info)
        )
        if _instance_sha256(instance) != checkpoint.instance_sha256:
            raise ValueError("verified checkpoint belongs to a different instance")
        if hashlib.sha256(checkpoint.solution_json).hexdigest() != (
            checkpoint.solution_sha256
        ):
            raise ValueError("verified checkpoint solution hash mismatch")
        solution = checkpoint.solution_copy()
        state = SolutionState(instance)
        for placement in checkpoint.placements:
            state.place(placement)
        state.assert_invariants()
        if _canonical_json(serialize_non_interlock(state.placements.values())) != (
            checkpoint.solution_json
        ):
            raise ValueError("verified checkpoint placement snapshot mismatch")
        checked = official_check(instance.raw, solution)
        if checked != checkpoint.checker_result:
            raise ValueError("verified checkpoint checker result mismatch")
        if not checked.feasible or checked.stage != 5 or checked.objective is None:
            raise ValueError("verified checkpoint is not a feasible Stage-5 incumbent")
        incumbent = cls(instance)
        incumbent._record = _VerifiedRecord(
            solution,
            checked,
            checkpoint.placements,
        )
        incumbent._last_verification = incumbent._record
        incumbent._verification_count = checkpoint.verification_count + 1
        return incumbent

    def register_initial(self, state: SolutionState) -> CheckerResult:
        """Full-check and atomically store the required first incumbent."""
        if self.has_incumbent:
            raise RuntimeError("initial incumbent is already registered")
        serialized, checked = self._verify(state)
        if not checked.feasible:
            details = "; ".join(checked.violations) or "official checker rejected T0"
            raise IncumbentVerificationError(details)
        self._record = _VerifiedRecord(serialized, checked, _placement_snapshot(state))
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
        self._record = _VerifiedRecord(serialized, checked, _placement_snapshot(state))
        return True

    def _verify(self, state: SolutionState) -> tuple[dict[str, Any], CheckerResult]:
        serialized = serialize_non_interlock(state.placements.values())
        checked = official_check(self._prob_info, serialized)
        self._verification_count += 1
        self._last_verification = _VerifiedRecord(
            serialized,
            checked,
            _placement_snapshot(state),
        )
        return serialized, checked


def _placement_snapshot(state: SolutionState) -> tuple[Placement, ...]:
    return tuple(
        sorted(state.placements.values(), key=lambda placement: placement.block_id)
    )


def _canonical_json(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode()


def _instance_sha256(instance: ProblemInstance) -> str:
    return hashlib.sha256(_canonical_json(instance.raw)).hexdigest()
