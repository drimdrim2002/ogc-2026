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
from typing import Any, Literal, Mapping


VARIANTS = (
    "safe_fallback",
    "constructor_only",
    "candidate_constructor_only",
    "constructor_retime",
    "heuristic_lns",
    "candidate_constructor",
    "candidate_mip",
    "interlock",
)


LONG_BUDGET_ANCHOR_SECONDS = 60.0
LONG_BUDGET_ANCHOR_CONSTRUCTOR_CANDIDATES = 50_000


@dataclass(frozen=True, slots=True)
class SubmissionConfig:
    seed: int = 20260710
    assignment_enabled: bool = True
    constructor_enabled: bool = True
    retime_enabled: bool = True
    lns_enabled: bool = True
    mip_enabled: bool = False
    interlock_enabled: bool = False
    retime_exact_z1_skip: bool = False
    constructor_selection_policy: str = "eager_regret"
    neighborhood_policy: str = "legacy"
    # Phase 4 GO promotes guarded native repair/exact decisions. Optional
    # prefilter, MIP, and interlock features remain independently disabled.
    repair_backend: str = "native"
    native_prefilter_enabled: bool = False
    native_exact_mode: Literal["python", "shadow", "native"] = "native"

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
            self.retime_exact_z1_skip,
            self.native_prefilter_enabled,
        )
        if any(not isinstance(value, bool) for value in flags):
            raise TypeError("submission feature flags must be booleans")
        if self.mip_enabled and not self.lns_enabled:
            raise ValueError("candidate MIP requires LNS")
        if self.interlock_enabled and not self.lns_enabled:
            raise ValueError("interlock requires LNS")
        if self.constructor_selection_policy not in {
            "eager_regret",
            "profile_priority",
        }:
            raise ValueError("unknown constructor selection policy")
        if self.neighborhood_policy not in {"legacy", "portfolio"}:
            raise ValueError("unknown neighborhood policy")
        if not isinstance(self.repair_backend, str):
            raise TypeError("repair backend must be a string")
        if self.repair_backend not in {"python", "native"}:
            raise ValueError("repair backend must be 'python' or 'native'")
        if self.native_exact_mode not in {"python", "shadow", "native"}:
            raise ValueError("unknown native exact mode")

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
            "candidate_constructor_only": (True, True, False, False, False, False),
            "constructor_retime": (True, True, True, False, False, False),
            "heuristic_lns": (True, True, True, True, False, False),
            "candidate_constructor": (True, True, True, True, False, False),
            "candidate_mip": (True, True, True, True, True, False),
            # Stage 7 compares interlock against the approved cumulative
            # heuristic baseline.  MIP remains independently gated because
            # its Stage 2 activation decision was DEFAULT OFF.
            "interlock": (True, True, True, True, False, True),
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
            constructor_selection_policy=(
                "profile_priority"
                if variant in {"candidate_constructor_only", "candidate_constructor"}
                else "eager_regret"
            ),
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
    """Retained compatibility hook; ALNS is always deadline-led."""
    del limit
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

    telemetry_schema: str = "run-trace-lns-invocations-v1"
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
    operator_stats_last: dict[str, Any] = field(default_factory=dict)
    model_stats: dict[str, Any] = field(default_factory=dict)
    lns_invocations: list[dict[str, Any]] = field(default_factory=list)
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

    def add_lns_invocation(
        self,
        *,
        kind: str,
        started: float,
        ended: float,
        budget_seconds: float,
        result: Any | None = None,
        error: BaseException | None = None,
    ) -> None:
        """Record one anchor or extension LNS call without changing its result.

        ``phase_times`` and ``operator_stats`` remain aggregate telemetry for a
        whole solve.  The per-call list is the authoritative source when a
        caller needs to distinguish the 60-second anchor from an extension.
        ``model_stats['lns']`` is retained as the legacy last-call payload;
        the explicit ``lns_last`` and ``lns_aggregate`` keys remove that
        ambiguity for new consumers.
        """
        if kind not in {"anchor", "extension"}:
            raise ValueError(f"unknown LNS invocation kind {kind!r}")
        metrics = getattr(result, "metrics", None)
        phase_times = {
            str(name): float(duration)
            for name, duration in getattr(metrics, "time_by_phase", ())
            if isinstance(duration, (int, float))
            and not isinstance(duration, bool)
            and math.isfinite(float(duration))
            and float(duration) >= 0.0
        }
        operators = {
            str(name): _json_value(values)
            for name, values in getattr(metrics, "per_operator", ())
        }
        best_trace = [
            float(value)
            for value in getattr(metrics, "best_trace", ())
            if isinstance(value, (int, float))
            and not isinstance(value, bool)
            and math.isfinite(float(value))
        ]
        mip_events = [
            _json_value(dict(event))
            for event in getattr(metrics, "mip_events", ())
        ]
        retime_events = [
            _json_value(dict(event))
            for event in getattr(metrics, "retime_events", ())
        ]
        destroy_sizes = [
            _json_value(item)
            for item in getattr(metrics, "per_destroy_size", ())
        ]
        destroy_growth_events = [
            _json_value(dict(event))
            for event in getattr(metrics, "destroy_growth_events", ())
        ]
        record = {
            "kind": kind,
            "start_seconds": max(0.0, float(started) - self._started),
            "end_seconds": max(0.0, float(ended) - self._started),
            "budget_seconds": max(0.0, float(budget_seconds)),
            "elapsed_seconds": max(0.0, float(ended) - float(started)),
            "iterations": int(getattr(metrics, "iterations", 0) or 0),
            "repair_seconds": phase_times.get("repair", 0.0),
            "retime_seconds": phase_times.get("retime", 0.0),
            "checker_seconds": phase_times.get("checker", 0.0),
            "exit_reason": (
                f"ERROR:{type(error).__name__}"
                if error is not None
                else str(getattr(metrics, "exit_reason", "UNKNOWN"))
            ),
            "operator_stats": operators,
            "best_trace": best_trace,
            "mip_events": mip_events,
            "retime_triggers": int(
                getattr(metrics, "retime_triggers", 0) or 0
            ),
            "retime_events": retime_events,
            "destroy_sizes": destroy_sizes,
            "destroy_growth_events": destroy_growth_events,
            "total_iterations": int(
                getattr(metrics, "total_iterations", 0) or 0
            ),
            "warmup_completed": bool(
                getattr(metrics, "warmup_completed", False)
            ),
            "weight_updates": int(
                getattr(metrics, "weight_updates", 0) or 0
            ),
            "stall_events": int(getattr(metrics, "stall_events", 0) or 0),
            "remaining_seconds": float(
                getattr(metrics, "remaining_seconds", 0.0) or 0.0
            ),
            "budget_utilization": float(
                getattr(metrics, "budget_utilization", 0.0) or 0.0
            ),
        }
        self._append(self.lns_invocations, record)
        for name, duration in phase_times.items():
            self.add_phase_time(f"lns_{name}", duration)
        for objective in best_trace:
            self.add_best(objective)

        if metrics is None:
            return
        self.model_stats["lns"] = metrics
        self.model_stats["lns_last"] = _json_value(metrics)
        self.operator_stats_last = operators
        aggregate = self.model_stats.setdefault(
            "lns_aggregate",
            {
                "invocation_count": 0,
                "iterations": 0,
                "repair_seconds": 0.0,
                "retime_seconds": 0.0,
                "checker_seconds": 0.0,
                "exit_reasons": [],
                "mip_events": [],
            },
        )
        aggregate["invocation_count"] += 1
        aggregate["iterations"] += record["iterations"]
        for key in ("repair_seconds", "retime_seconds", "checker_seconds"):
            aggregate[key] += record[key]
        aggregate["exit_reasons"].append(record["exit_reason"])
        aggregate["mip_events"].extend(mip_events)
        for name, values in operators.items():
            totals = self.operator_stats.setdefault(
                name,
                {
                    "attempts": 0,
                    "feasible": 0,
                    "accepted": 0,
                    "new_best": 0,
                    "delta_sum": 0.0,
                    "exceptions": 0,
                    "time_s": 0.0,
                },
            )
            for field in totals:
                value = values.get(field, 0)
                if isinstance(value, (int, float)) and not isinstance(value, bool):
                    totals[field] += value

    def as_dict(self) -> dict[str, Any]:
        return _json_value(self)


__all__ = [
    "LONG_BUDGET_ANCHOR_CONSTRUCTOR_CANDIDATES",
    "LONG_BUDGET_ANCHOR_SECONDS",
    "RunTrace",
    "SubmissionConfig",
    "VARIANTS",
    "constructor_candidate_limit",
    "constructor_profile_limit",
    "lns_iteration_limit",
    "phase_name",
]
