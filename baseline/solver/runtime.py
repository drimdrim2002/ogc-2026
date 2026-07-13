"""Immutable submission settings and bounded, in-memory run evidence.

The public ``algorithm`` entry point always uses ``from_defaults``.  Benchmark
variants are injected explicitly through the internal solver API; environment
variables and command-line arguments are deliberately not consulted here.
"""

from __future__ import annotations

import dataclasses
import math
import time
from dataclasses import dataclass, field
from typing import Any, Mapping


VARIANTS = (
    "safe_fallback",
    "constructor_only",
    "constructor_retime",
    "heuristic_lns",
    "candidate_mip",
    "interlock",
)


LONG_BUDGET_ANCHOR_SECONDS = 60.0
LONG_BUDGET_ANCHOR_CONSTRUCTOR_CANDIDATES = 50_000
LONG_BUDGET_ANCHOR_LNS_ITERATIONS = 16


@dataclass(frozen=True, slots=True)
class SubmissionConfig:
    seed: int = 20260710
    assignment_enabled: bool = True
    constructor_enabled: bool = True
    retime_enabled: bool = True
    lns_enabled: bool = True
    mip_enabled: bool = False
    interlock_enabled: bool = False

    def __post_init__(self) -> None:
        if isinstance(self.seed, bool) or not isinstance(self.seed, int):
            raise TypeError("seed must be an integer")
        flags = (
            self.assignment_enabled,
            self.constructor_enabled,
            self.retime_enabled,
            self.lns_enabled,
            self.mip_enabled,
            self.interlock_enabled,
        )
        if any(not isinstance(value, bool) for value in flags):
            raise TypeError("submission feature flags must be booleans")
        if self.mip_enabled and not self.lns_enabled:
            raise ValueError("candidate MIP requires LNS")
        if self.interlock_enabled and not self.lns_enabled:
            raise ValueError("interlock requires LNS")

    @classmethod
    def from_defaults(cls, *, seed: int = 20260710) -> "SubmissionConfig":
        return cls(seed=seed)

    @classmethod
    def for_benchmark(cls, variant: str, *, seed: int) -> "SubmissionConfig":
        """Create an explicit test/harness-only variant configuration."""
        if variant not in VARIANTS:
            raise ValueError(f"unknown benchmark variant {variant!r}")
        levels = {
            "safe_fallback": (False, False, False, False, False, False),
            "constructor_only": (True, True, False, False, False, False),
            "constructor_retime": (True, True, True, False, False, False),
            "heuristic_lns": (True, True, True, True, False, False),
            "candidate_mip": (True, True, True, True, True, False),
            "interlock": (True, True, True, True, True, True),
        }
        assignment, constructor, retime, lns, mip, interlock = levels[variant]
        return cls(
            seed=seed,
            assignment_enabled=assignment,
            constructor_enabled=constructor,
            retime_enabled=retime,
            lns_enabled=lns,
            mip_enabled=mip,
            interlock_enabled=interlock,
        )

    def as_dict(self) -> dict[str, Any]:
        return dataclasses.asdict(self)


def phase_name(limit: float) -> str:
    if limit < 2.0:
        return "safe"
    if limit < 12.0:
        return "short"
    if limit < 60.0:
        return "medium"
    return "long"


def constructor_profile_limit(limit: float) -> int:
    phase = phase_name(limit)
    return {"safe": 0, "short": 1, "medium": 4, "long": 6}[phase]


def constructor_candidate_limit(limit: float) -> int | None:
    """Return the deterministic constructor quota for the official anchor."""
    if limit >= LONG_BUDGET_ANCHOR_SECONDS:
        return LONG_BUDGET_ANCHOR_CONSTRUCTOR_CANDIDATES
    return None


def lns_iteration_limit(limit: float) -> int | None:
    """Bound the common anchor prefix; extension search remains deadline-led."""
    if math.isclose(
        float(limit),
        LONG_BUDGET_ANCHOR_SECONDS,
        rel_tol=0.0,
        abs_tol=1e-9,
    ):
        return LONG_BUDGET_ANCHOR_LNS_ITERATIONS
    return None


def _json_value(value: Any) -> Any:
    if dataclasses.is_dataclass(value):
        return {
            field.name: _json_value(getattr(value, field.name))
            for field in dataclasses.fields(value)
            if not field.name.startswith("_")
        }
    if isinstance(value, Mapping):
        return {str(key): _json_value(item) for key, item in value.items()}
    if isinstance(value, (tuple, list, set, frozenset)):
        return [_json_value(item) for item in value]
    if isinstance(value, float) and not math.isfinite(value):
        return None
    if isinstance(value, str):
        return value[:1000]
    if value is None or isinstance(value, (int, float, bool)):
        return value
    return repr(value)


@dataclass(slots=True)
class RunTrace:
    """Bounded telemetry owned by one internal solver call."""

    max_events: int = 256
    phase: str = "not_started"
    phase_times: dict[str, float] = field(default_factory=dict)
    checker_durations: list[float] = field(default_factory=list)
    validated_best: list[float] = field(default_factory=list)
    validated_best_events: list[dict[str, float]] = field(default_factory=list)
    construction: list[dict[str, Any]] = field(default_factory=list)
    retiming: list[dict[str, Any]] = field(default_factory=list)
    assignment: dict[str, Any] = field(default_factory=dict)
    operator_stats: dict[str, Any] = field(default_factory=dict)
    model_stats: dict[str, Any] = field(default_factory=dict)
    exceptions: list[dict[str, str]] = field(default_factory=list)
    _started: float = field(default_factory=time.monotonic, repr=False)

    def _append(self, target: list[Any], value: Any) -> None:
        if len(target) < self.max_events:
            target.append(value)

    def add_phase_time(self, name: str, duration: float) -> None:
        if math.isfinite(duration) and duration >= 0.0:
            self.phase_times[name] = self.phase_times.get(name, 0.0) + float(duration)

    def add_checker(self, duration: float) -> None:
        if math.isfinite(duration) and duration >= 0.0:
            self._append(self.checker_durations, float(duration))
            self.add_phase_time("checker", duration)

    def add_best(self, objective: float) -> None:
        value = float(objective)
        if not math.isfinite(value):
            return
        if self.validated_best and value > self.validated_best[-1] + 1e-9:
            self.add_exception("trace", "validated best objective increased")
            return
        if not self.validated_best or value < self.validated_best[-1] - 1e-9:
            self._append(self.validated_best, value)
            self._append(
                self.validated_best_events,
                {"elapsed_seconds": max(0.0, time.monotonic() - self._started), "objective": value},
            )

    def add_exception(self, phase: str, error: BaseException | str) -> None:
        if isinstance(error, BaseException):
            detail = f"{type(error).__name__}: {error}"
        else:
            detail = str(error)
        self._append(self.exceptions, {"phase": phase, "detail": detail[:1000]})

    def as_dict(self) -> dict[str, Any]:
        return _json_value(self)


__all__ = [
    "LONG_BUDGET_ANCHOR_CONSTRUCTOR_CANDIDATES",
    "LONG_BUDGET_ANCHOR_LNS_ITERATIONS",
    "LONG_BUDGET_ANCHOR_SECONDS",
    "RunTrace",
    "SubmissionConfig",
    "VARIANTS",
    "constructor_candidate_limit",
    "constructor_profile_limit",
    "lns_iteration_limit",
    "phase_name",
]
