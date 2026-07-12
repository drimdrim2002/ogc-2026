"""Pure-data exact-retiming contract with isolated, incumbent-gated probes."""

from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
import math
import time
from typing import Protocol

from .budget import Budget


BACKENDS = frozenset({"gurobi", "cpsat"})
RESULT_STATUSES = frozenset(
    {
        "optimal",
        "feasible",
        "no_solution",
        "unavailable",
        "time_limit",
        "error",
        "invalid",
    }
)
SOLUTION_STATUSES = frozenset({"optimal", "feasible"})

IntMap = tuple[tuple[int, int], ...]
RetimeSolution = tuple[tuple[int, int, int], ...]


@dataclass(frozen=True, slots=True)
class RetimeRequest:
    """Immutable fixed-layout scheduling data for one bay.

    Tuple maps keep the backend boundary serializable and prevent solver
    adapters from mutating the live solution state.
    """

    block_ids: tuple[int, ...]
    releases: IntMap
    dues: IntMap
    dwells: IntMap
    current_entries: IntMap
    conflict_pairs: tuple[tuple[int, int], ...]
    seed: int
    threads: int

    def __post_init__(self) -> None:
        if not isinstance(self.block_ids, tuple):
            raise TypeError("block_ids must be a tuple")
        if any(not _plain_int(item) or item < 0 for item in self.block_ids):
            raise ValueError("block_ids must contain non-negative integers")
        if len(set(self.block_ids)) != len(self.block_ids):
            raise ValueError("block_ids must be unique")
        expected = set(self.block_ids)
        for label, values in (
            ("releases", self.releases),
            ("dues", self.dues),
            ("dwells", self.dwells),
            ("current_entries", self.current_entries),
        ):
            _validate_int_map(label, values, expected)
        if any(value <= 0 for _, value in self.dwells):
            raise ValueError("dwells must be positive")
        releases = dict(self.releases)
        if any(entry < releases[block_id] for block_id, entry in self.current_entries):
            raise ValueError("current_entries must respect release times")
        if not isinstance(self.conflict_pairs, tuple):
            raise TypeError("conflict_pairs must be a tuple")
        if len(set(self.conflict_pairs)) != len(self.conflict_pairs):
            raise ValueError("conflict_pairs must be unique")
        for pair in self.conflict_pairs:
            if (
                not isinstance(pair, tuple)
                or len(pair) != 2
                or not all(_plain_int(item) for item in pair)
            ):
                raise ValueError("conflict_pairs must contain integer pairs")
            left, right = pair
            if left not in expected or right not in expected or left >= right:
                raise ValueError("conflict_pairs must be canonical block-id pairs")
        if not _plain_int(self.seed):
            raise ValueError("seed must be an integer")
        if not _plain_int(self.threads) or not 1 <= self.threads <= 4:
            raise ValueError("threads must be an integer from 1 through 4")


@dataclass(frozen=True, slots=True)
class ExactResult:
    """Backend-independent result containing primitives only."""

    backend: str
    status: str
    solution: RetimeSolution | None
    objective: float | None
    bound: float | None
    build_s: float
    solve_s: float
    first_solution_s: float | None
    reason: str | None = None


@dataclass(frozen=True, slots=True)
class BackendHealth:
    """One lazy probe decision with its precise failure boundary."""

    backend: str
    available: bool
    completed_stage: str | None
    failure_stage: str | None
    reason: str | None
    elapsed_s: float


class IncumbentStatus(Protocol):
    @property
    def has_incumbent(self) -> bool: ...


class ExactBackend(Protocol):
    """S2 protocol intentionally contains retiming only."""

    def retime(self, request: RetimeRequest, timebox: float) -> ExactResult: ...


ProbeStep = Callable[[], object]


@dataclass(slots=True)
class BackendProbe:
    """Lazy executable probe plan; cached only after incumbent verification."""

    backend: str
    import_module: ProbeStep
    create_environment: ProbeStep
    check_license: ProbeStep
    build_model: ProbeStep
    optimize: ProbeStep
    extract: ProbeStep
    _health: BackendHealth | None = field(default=None, init=False, repr=False)

    def steps(self) -> tuple[tuple[str, ProbeStep], ...]:
        return (
            ("import", self.import_module),
            ("environment", self.create_environment),
            ("license", self.check_license),
            ("model", self.build_model),
            ("optimize", self.optimize),
            ("extraction", self.extract),
        )


def probe_backends(
    incumbent: IncumbentStatus,
    probes: Iterable[BackendProbe],
    *,
    budget: Budget,
    clock: Callable[[], float] = time.monotonic,
) -> tuple[BackendHealth, ...]:
    """Probe each backend once, only after a verified incumbent exists.

    Ordinary backend exceptions become immutable health records and never
    prevent the remaining backend from being probed.  Interrupting base
    exceptions intentionally retain their normal process semantics.
    """

    plans = tuple(probes)
    _validate_probe_names(plans)
    if not incumbent.has_incumbent:
        return tuple(
            BackendHealth(
                backend=plan.backend,
                available=False,
                completed_stage=None,
                failure_stage="incumbent",
                reason="checker-verified incumbent required before backend probe",
                elapsed_s=0.0,
            )
            for plan in plans
        )

    health: list[BackendHealth] = []
    for plan in plans:
        if plan._health is not None:
            health.append(plan._health)
            continue
        started = float(clock())
        completed_stage: str | None = None
        decision: BackendHealth | None = None
        for stage, action in plan.steps():
            if budget.expired:
                decision = BackendHealth(
                    backend=plan.backend,
                    available=False,
                    completed_stage=completed_stage,
                    failure_stage=stage,
                    reason="probe deadline exhausted",
                    elapsed_s=max(0.0, float(clock()) - started),
                )
                break
            try:
                action()
            except Exception as exc:
                decision = BackendHealth(
                    backend=plan.backend,
                    available=False,
                    completed_stage=completed_stage,
                    failure_stage=stage,
                    reason=f"{type(exc).__name__}: {exc}",
                    elapsed_s=max(0.0, float(clock()) - started),
                )
                break
            completed_stage = stage
        if decision is None:
            decision = BackendHealth(
                backend=plan.backend,
                available=True,
                completed_stage="extraction",
                failure_stage=None,
                reason=None,
                elapsed_s=max(0.0, float(clock()) - started),
            )
        plan._health = decision
        health.append(decision)
    return tuple(health)


def deadline_bounded_call(
    backend: str,
    request: RetimeRequest,
    call: Callable[[RetimeRequest, float], ExactResult],
    *,
    timebox: float,
    budget: Budget,
    clock: Callable[[], float] = time.monotonic,
) -> ExactResult:
    """Call one adapter within the remaining safe budget and isolate faults."""

    if backend not in BACKENDS:
        raise ValueError(f"unsupported backend: {backend}")
    requested = _finite_nonnegative(timebox, "timebox")
    allowance = min(requested, budget.remaining)
    if allowance <= 0.0:
        return _empty_result(backend, "time_limit", "no safe exact budget remains")
    started = float(clock())
    try:
        raw = call(request, allowance)
    except Exception as exc:
        return _empty_result(backend, "error", f"{type(exc).__name__}: {exc}")
    observed = max(0.0, float(clock()) - started)
    if isinstance(raw, ExactResult) and raw.backend != backend:
        return _empty_from(
            raw,
            "invalid",
            f"backend identity mismatch: expected {backend}, got {raw.backend}",
        )
    return normalize_result(request, raw, timebox=allowance, observed_s=observed)


def normalize_result(
    request: RetimeRequest,
    result: object,
    *,
    timebox: float,
    observed_s: float | None = None,
) -> ExactResult:
    """Convert malformed, invalid, or over-budget output to no solution."""

    allowed = _finite_nonnegative(timebox, "timebox")
    if not isinstance(result, ExactResult):
        return _empty_result("unknown", "invalid", "backend returned a non-ExactResult")
    consumed = result.build_s + result.solve_s
    if observed_s is not None:
        consumed = max(consumed, _finite_nonnegative(observed_s, "observed_s"))
    if not math.isfinite(consumed) or consumed > allowed + 1e-9:
        return _empty_from(result, "time_limit", "backend exceeded its exact timebox")
    valid, reason = validate_result(request, result)
    if not valid:
        return _empty_from(result, "invalid", reason or "invalid exact result")
    if result.status not in SOLUTION_STATUSES:
        return _empty_from(result, result.status, result.reason)
    return result


def validate_result(
    request: RetimeRequest,
    result: ExactResult,
) -> tuple[bool, str | None]:
    """Recompute the pure retiming semantics without trusting a backend."""

    if result.backend not in BACKENDS:
        return False, f"unsupported backend: {result.backend}"
    if result.status not in RESULT_STATUSES:
        return False, f"unsupported result status: {result.status}"
    for label, value in (("build_s", result.build_s), ("solve_s", result.solve_s)):
        if not _is_finite_nonnegative(value):
            return False, f"{label} must be finite and non-negative"
    if result.first_solution_s is not None:
        if not _is_finite_nonnegative(result.first_solution_s):
            return False, "first_solution_s must be finite and non-negative"
        if result.first_solution_s > result.solve_s + 1e-9:
            return False, "first_solution_s exceeds solve_s"
    for label, value in (("objective", result.objective), ("bound", result.bound)):
        if value is not None and not _is_finite_number(value):
            return False, f"{label} must be finite when present"
    if result.status not in SOLUTION_STATUSES:
        if result.solution is not None:
            return False, "non-solution status carried a solution"
        return True, None
    if result.solution is None or result.objective is None:
        return False, "solution status requires a schedule and objective"
    if not isinstance(result.solution, tuple):
        return False, "solution must be an immutable tuple"

    releases = dict(request.releases)
    dues = dict(request.dues)
    dwells = dict(request.dwells)
    schedule: dict[int, tuple[int, int]] = {}
    for item in result.solution:
        if (
            not isinstance(item, tuple)
            or len(item) != 3
            or not all(_plain_int(value) for value in item)
        ):
            return False, "solution rows must be integer (block, entry, exit) tuples"
        block_id, entry, exit_time = item
        if block_id in schedule:
            return False, f"duplicate solution block {block_id}"
        if block_id not in releases:
            return False, f"unknown solution block {block_id}"
        if entry < releases[block_id]:
            return False, f"block {block_id} starts before release"
        if exit_time != entry + dwells[block_id]:
            return False, f"block {block_id} violates e=a+dwell"
        schedule[block_id] = (entry, exit_time)
    if set(schedule) != set(request.block_ids):
        return False, "solution block set does not match request"
    for left, right in request.conflict_pairs:
        left_entry, left_exit = schedule[left]
        right_entry, right_exit = schedule[right]
        if not (left_exit <= right_entry or right_exit <= left_entry):
            return False, f"conflict pair {(left, right)} overlaps"
    objective = sum(
        max(0, schedule[block_id][1] - dues[block_id])
        for block_id in request.block_ids
    )
    if not math.isclose(float(result.objective), float(objective), rel_tol=1e-9, abs_tol=1e-9):
        return False, "objective does not match recomputed tardiness"
    return True, None


def _validate_int_map(label: str, values: object, expected: set[int]) -> None:
    if not isinstance(values, tuple):
        raise TypeError(f"{label} must be a tuple")
    if any(
        not isinstance(item, tuple)
        or len(item) != 2
        or not all(_plain_int(value) for value in item)
        for item in values
    ):
        raise ValueError(f"{label} must contain integer pairs")
    keys = [key for key, _ in values]
    if len(keys) != len(set(keys)) or set(keys) != expected:
        raise ValueError(f"{label} keys must exactly match block_ids")


def _validate_probe_names(plans: tuple[BackendProbe, ...]) -> None:
    names = [plan.backend for plan in plans]
    if len(names) != len(set(names)):
        raise ValueError("backend probes must have unique names")
    if any(name not in BACKENDS for name in names):
        raise ValueError("backend probes must be gurobi or cpsat")


def _empty_result(backend: str, status: str, reason: str | None) -> ExactResult:
    return ExactResult(
        backend=backend,
        status=status,
        solution=None,
        objective=None,
        bound=None,
        build_s=0.0,
        solve_s=0.0,
        first_solution_s=None,
        reason=reason,
    )


def _empty_from(result: ExactResult, status: str, reason: str | None) -> ExactResult:
    return ExactResult(
        backend=result.backend,
        status=status,
        solution=None,
        objective=None,
        bound=result.bound if _is_finite_number(result.bound) else None,
        build_s=result.build_s if _is_finite_nonnegative(result.build_s) else 0.0,
        solve_s=result.solve_s if _is_finite_nonnegative(result.solve_s) else 0.0,
        first_solution_s=(
            result.first_solution_s
            if _is_finite_nonnegative(result.first_solution_s)
            else None
        ),
        reason=reason,
    )


def _finite_nonnegative(value: float, label: str) -> float:
    if not _is_finite_nonnegative(value):
        raise ValueError(f"{label} must be finite and non-negative")
    return float(value)


def _is_finite_nonnegative(value: object) -> bool:
    return _is_finite_number(value) and float(value) >= 0.0


def _is_finite_number(value: object) -> bool:
    return (
        not isinstance(value, bool)
        and isinstance(value, (int, float))
        and math.isfinite(float(value))
    )


def _plain_int(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)
