"""Deterministic anytime ALNS with immutable validated-best ownership."""

from __future__ import annotations

import copy
import math
import random
import statistics
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

from .budget import Budget
from .construct import ConstructorConfig
from .geometry import GeometryKernel
from .instance import Instance
from .neighborhoods import (
    DEFAULT_DESTROY_OPERATORS,
    DestroyOperator,
    NeighborhoodContext,
    RepairResult,
    heuristic_repair,
    locally_feasible,
)
from .serialize import serialize
from .state import IncumbentStore, SolutionSnapshot, compute_objective


RepairEngine = Callable[
    [SolutionSnapshot, tuple[int, ...], NeighborhoodContext, Budget], RepairResult
]


@dataclass(frozen=True, slots=True)
class AlnsConfig:
    seed: int = 20260710
    segment: int = 50
    reaction: float = 0.2
    rewards: tuple[float, float, float] = (20.0, 5.0, 2.0)
    weight_floor: float = 0.1
    warmup_iterations: int = 32
    cooling_iterations: int = 1000
    stall_iterations: int = 50
    stall_time_fraction: float = 0.08
    max_iterations: int | None = None
    checker_margin: float = 0.01
    max_invariant_errors: int = 3
    mip_period: int = 10

    def __post_init__(self) -> None:
        integer_values = (
            self.seed,
            self.segment,
            self.warmup_iterations,
            self.cooling_iterations,
            self.stall_iterations,
            self.max_invariant_errors,
            self.mip_period,
        )
        if any(isinstance(value, bool) or not isinstance(value, int) for value in integer_values):
            raise TypeError("ALNS iteration and seed settings must be integers")
        if (
            self.segment <= 0
            or self.cooling_iterations <= 0
            or self.stall_iterations <= 0
            or self.mip_period <= 0
        ):
            raise ValueError(
                "segment, cooling_iterations, stall_iterations, and mip_period must be positive"
            )
        if self.warmup_iterations < 0 or self.max_invariant_errors <= 0:
            raise ValueError("warmup must be nonnegative and invariant limit positive")
        if self.max_iterations is not None and (
            isinstance(self.max_iterations, bool)
            or not isinstance(self.max_iterations, int)
            or self.max_iterations < 0
        ):
            raise ValueError("max_iterations must be a nonnegative integer or None")
        if not 0.0 <= self.reaction <= 1.0:
            raise ValueError("reaction must be in [0,1]")
        if self.weight_floor <= 0.0 or self.stall_time_fraction < 0.0:
            raise ValueError("weight floor must be positive and stall fraction nonnegative")
        if len(self.rewards) != 3 or any(value < 0.0 for value in self.rewards):
            raise ValueError("rewards must contain three nonnegative values")


@dataclass(frozen=True, slots=True)
class AlnsContext:
    instance: Instance
    kernel: GeometryKernel
    raw: Mapping[str, Any]
    checker: Callable[[dict, dict], dict]
    repair_config: ConstructorConfig = ConstructorConfig(max_profiles=1)
    destroy_operators: tuple[DestroyOperator, ...] = DEFAULT_DESTROY_OPERATORS
    repair_engines: tuple[RepairEngine, ...] = (heuristic_repair,)
    clock: Callable[[], float] = time.monotonic

    def __post_init__(self) -> None:
        if not self.destroy_operators:
            raise ValueError("at least one destroy operator is required")
        if not self.repair_engines:
            raise ValueError("at least one repair engine is required")

    @property
    def neighborhood(self) -> NeighborhoodContext:
        return NeighborhoodContext(self.instance, self.kernel, self.repair_config)


@dataclass(frozen=True, slots=True)
class OperatorMetrics:
    attempts: int
    feasible: int
    accepted: int
    new_best: int
    delta_sum: float
    exceptions: int
    time_s: float


@dataclass(slots=True)
class _MutableOperatorMetrics:
    attempts: int = 0
    feasible: int = 0
    accepted: int = 0
    new_best: int = 0
    delta_sum: float = 0.0
    exceptions: int = 0
    time_s: float = 0.0

    def freeze(self) -> OperatorMetrics:
        return OperatorMetrics(
            self.attempts,
            self.feasible,
            self.accepted,
            self.new_best,
            self.delta_sum,
            self.exceptions,
            self.time_s,
        )


@dataclass(frozen=True, slots=True)
class AlnsMetrics:
    per_operator: tuple[tuple[str, OperatorMetrics], ...]
    iterations: int
    best_trace: tuple[float, ...]
    accepted_trace: tuple[float, ...]
    cache_stats: tuple[tuple[str, int], ...]
    time_by_phase: tuple[tuple[str, float], ...]
    weights: tuple[tuple[str, float], ...]
    temperature: float | None
    destroy_size: int
    retime_triggers: int
    exit_reason: str
    repair_engines: tuple[tuple[str, int, int, int, float], ...] = ()


@dataclass(frozen=True, slots=True)
class _ValidationEvidence:
    operations: Mapping[str, Any]
    checker_result: Mapping[str, Any]


@dataclass(frozen=True, slots=True)
class AlnsResult:
    snapshot: SolutionSnapshot
    current: SolutionSnapshot
    serialized_operations: Mapping[str, Any]
    metrics: AlnsMetrics


def initial_destroy_size(n: int) -> int:
    return min(max(0, n), max(4, math.ceil(0.02 * n)))


def grow_destroy_size(current: int, n: int) -> int:
    cap = min(
        max(0, n),
        max(initial_destroy_size(n), math.ceil(0.15 * n)),
    )
    if cap == 0:
        return 0
    return min(cap, max(current + 1, math.ceil(current * 1.5)))


def accept_candidate(
    delta: float,
    temperature: float | None,
    rng: random.Random,
    *,
    allow_worse: bool = True,
) -> bool:
    """Accept exact weighted delta; negative/equal is always non-worse."""
    if delta <= 0.0:
        return True
    if not allow_worse or temperature is None or temperature <= 0.0:
        return False
    probability = math.exp(-delta / max(temperature, 1e-300))
    return rng.random() < probability


def update_weights(
    weights: tuple[float, ...],
    uses: tuple[int, ...],
    scores: tuple[float, ...],
    reaction: float,
    floor: float,
) -> tuple[float, ...]:
    if not (len(weights) == len(uses) == len(scores)):
        raise ValueError("weight update vectors must have equal length")
    updated = []
    for weight, use, score in zip(weights, uses, scores):
        target = score / use if use else weight
        updated.append(max(floor, (1.0 - reaction) * weight + reaction * target))
    return tuple(updated)


def _weighted_index(weights: tuple[float, ...], rng: random.Random) -> int:
    total = sum(weights)
    point = rng.random() * total
    cumulative = 0.0
    for index, weight in enumerate(weights):
        cumulative += weight
        if point < cumulative:
            return index
    return len(weights) - 1


def _cache_delta(before: Any, after: Any) -> tuple[tuple[str, int], ...]:
    names = ("hits", "misses", "maxsize", "currsize")
    values = []
    for name in names:
        left = int(getattr(before, name, 0))
        right = int(getattr(after, name, 0))
        values.append((name, right - left if name in {"hits", "misses"} else right))
    return tuple(values)


def _objective(snapshot: SolutionSnapshot, instance: Instance) -> SolutionSnapshot:
    return snapshot.with_objective(compute_objective(instance, snapshot))


def run_lns(
    initial: SolutionSnapshot,
    best_store: IncumbentStore,
    context: AlnsContext,
    budget: Budget,
    config: AlnsConfig | None = None,
    retime_hook: Callable[..., Any] | None = None,
) -> AlnsResult:
    """Run bounded ALNS and always return the already validated best payload."""
    config = config or AlnsConfig()
    rng = random.Random(config.seed)
    current = _objective(initial, context.instance)
    best = _objective(best_store.snapshot, context.instance)
    if not locally_feasible(current, context.neighborhood):
        raise ValueError("initial LNS state is not locally feasible")
    operators = context.destroy_operators
    weights = tuple(1.0 for _ in operators)
    segment_uses = [0 for _ in operators]
    segment_scores = [0.0 for _ in operators]
    counters = [_MutableOperatorMetrics() for _ in operators]
    best_trace = [best.objective.total]
    accepted_trace = [current.objective.total]
    improving_samples: list[float] = []
    temperature: float | None = None
    cooling = math.exp(math.log(0.001) / config.cooling_iterations)
    destroy_size = initial_destroy_size(len(current.placements))
    iterations_since_best = 0
    invariant_errors = 0
    retime_triggers = 0
    pending_retime: set[int] = set()
    stalled_engine_pending = False
    phase_time = {"destroy": 0.0, "repair": 0.0, "retime": 0.0, "checker": 0.0}
    engine_stats: dict[str, list[float]] = {}
    cache_before = context.kernel.cache_info()
    started = context.clock()
    iterations = 0
    exit_reason = "DEADLINE"

    def engine_name(engine: RepairEngine) -> str:
        return str(getattr(engine, "__name__", type(engine).__name__))

    for repair_engine in context.repair_engines:
        engine_stats.setdefault(engine_name(repair_engine), [0.0, 0.0, 0.0, 0.0])

    def validate_candidate(candidate: SolutionSnapshot) -> _ValidationEvidence | None:
        if not budget.can_start(budget.checker_p95, margin=config.checker_margin):
            return None
        try:
            operations = serialize(candidate, context.kernel)
            check_started = context.clock()
            checked = context.checker(
                copy.deepcopy(dict(context.raw)), copy.deepcopy(operations)
            )
            duration = max(0.0, context.clock() - check_started)
            phase_time["checker"] += duration
            budget.record_checker_duration(duration)
            objective = candidate.objective or compute_objective(context.instance, candidate)
            external = (
                checked.get("obj1"),
                checked.get("obj2"),
                checked.get("obj3"),
                checked.get("objective"),
            )
            internal = (objective.z1, objective.z2, objective.z3, objective.total)
            if (
                checked.get("feasible") is not True
                or checked.get("stage") != 5
                or any(
                    isinstance(value, bool) or not isinstance(value, (int, float))
                    for value in external
                )
                or not all(
                    math.isclose(
                        float(left), float(right), rel_tol=1e-6, abs_tol=1e-9
                    )
                    for left, right in zip(internal, external)
                )
            ):
                return None
            return _ValidationEvidence(operations, checked)
        except Exception:
            return None

    def try_install(candidate: SolutionSnapshot, evidence: Any | None = None) -> bool:
        nonlocal best
        candidate = _objective(candidate, context.instance)
        if candidate.objective.total >= best.objective.total:
            return False
        if evidence is not None:
            operations = getattr(evidence, "operations", None)
            checked = getattr(evidence, "checker_result", None)
            if operations is not None and checked is not None:
                installed = best_store.install_if_valid(candidate, operations, checked)
                if installed:
                    best = best_store.snapshot
                return installed
        validated = validate_candidate(candidate)
        if validated is None:
            return False
        installed = best_store.install_if_valid(
            candidate, validated.operations, validated.checker_result
        )
        if installed:
            best = best_store.snapshot
        return installed

    def apply_pending_retime(
        metric: _MutableOperatorMetrics,
        operator_index: int,
        *,
        force: bool = False,
    ) -> None:
        nonlocal current, iterations_since_best, retime_triggers
        if retime_hook is None or not pending_retime:
            return
        if not force:
            affected_bays = {
                item.bay_id
                for item in current.placements
                if item.block_id in pending_retime
            }
            trigger = any(
                sum(
                    item.bay_id == bay_id and item.block_id in pending_retime
                    for item in current.placements
                )
                >= max(
                    3,
                    math.ceil(
                        0.03
                        * sum(
                            item.bay_id == bay_id for item in current.placements
                        )
                    ),
                )
                for bay_id in affected_bays
            )
            if not trigger:
                return
        if not budget.can_start(0.0, margin=0.0001):
            return
        phase_started = context.clock()
        retime_triggers += 1
        try:
            retimed = retime_hook(
                current,
                context.instance,
                context.kernel,
                budget,
                affected_ids=frozenset(pending_retime),
            )
            retimed_snapshot = getattr(retimed, "snapshot", current)
            if (
                not isinstance(retimed_snapshot, SolutionSnapshot)
                or retimed_snapshot is current
                or not locally_feasible(retimed_snapshot, context.neighborhood)
            ):
                return
            retimed_snapshot = _objective(retimed_snapshot, context.instance)
            if retimed_snapshot.objective.total > current.objective.total:
                return
            current = retimed_snapshot
            accepted_trace.append(current.objective.total)
            if try_install(current, retimed):
                metric.new_best += 1
                segment_scores[operator_index] += config.rewards[0]
                best_trace.append(best.objective.total)
                iterations_since_best = 0
        except Exception:
            metric.exceptions += 1
        finally:
            pending_retime.clear()
            phase_time["retime"] += max(0.0, context.clock() - phase_started)

    while budget.can_start(0.0, margin=0.0001):
        if config.max_iterations is not None and iterations >= config.max_iterations:
            exit_reason = "ITERATION_LIMIT"
            break
        operator_index = _weighted_index(weights, rng)
        operator = operators[operator_index]
        metric = counters[operator_index]
        metric.attempts += 1
        segment_uses[operator_index] += 1
        iteration_started = context.clock()
        try:
            phase_started = context.clock()
            destroyed = operator.select(
                current, destroy_size, rng, context.neighborhood
            )
            phase_time["destroy"] += max(0.0, context.clock() - phase_started)
            if not destroyed:
                raise ValueError("destroy operator returned an empty neighborhood")
            if not budget.can_start(0.0, margin=0.0001):
                exit_reason = "DEADLINE"
                break
            if len(context.repair_engines) == 1:
                engine = context.repair_engines[0]
            elif stalled_engine_pending or (
                (iterations + 1) % config.mip_period == 0
            ):
                engine = context.repair_engines[-1]
                stalled_engine_pending = False
            else:
                engine = context.repair_engines[0]
            selected_engine_name = engine_name(engine)
            engine_stats[selected_engine_name][0] += 1
            phase_started = context.clock()
            repaired = engine(
                current, destroyed, context.neighborhood, budget
            )
            repair_duration = max(0.0, context.clock() - phase_started)
            phase_time["repair"] += repair_duration
            engine_stats[selected_engine_name][3] += repair_duration
            if repaired.engine == "mip_fallback":
                engine_stats[selected_engine_name][2] += 1
            if not repaired.feasible:
                iterations_since_best += 1
            else:
                candidate = _objective(repaired.snapshot, context.instance)
                if not locally_feasible(candidate, context.neighborhood):
                    invariant_errors += 1
                    metric.exceptions += 1
                    iterations_since_best += 1
                else:
                    repair_delta = candidate.objective.total - current.objective.total
                    if not math.isclose(
                        repaired.objective_delta,
                        repair_delta,
                        rel_tol=1e-6,
                        abs_tol=1e-9,
                    ):
                        invariant_errors += 1
                        raise ValueError("repair objective delta mismatch")
                    invariant_errors = 0
                    evidence: _ValidationEvidence | None = None
                    mip_transaction = repaired.engine.startswith("mip")
                    if mip_transaction:
                        if retime_hook is not None and repaired.changed_ids:
                            phase_started = context.clock()
                            retime_triggers += 1
                            try:
                                retimed = retime_hook(
                                    candidate,
                                    context.instance,
                                    context.kernel,
                                    budget,
                                    affected_ids=repaired.changed_ids,
                                )
                            finally:
                                phase_time["retime"] += max(
                                    0.0, context.clock() - phase_started
                                )
                            if str(getattr(retimed, "status", "")).upper() in {
                                "ERROR",
                                "INVALID_INPUT",
                                "INVALID_REQUEST",
                                "INVALID_SOLUTION",
                                "INVALID_RELATION",
                                "CHECKER_REJECTED",
                                "OBJECTIVE_MISMATCH",
                                "WORSE_Z1",
                            }:
                                raise ValueError("MIP retime failed transactionally")
                            retimed_snapshot = getattr(retimed, "snapshot", candidate)
                            if (
                                not isinstance(retimed_snapshot, SolutionSnapshot)
                                or not locally_feasible(
                                    retimed_snapshot, context.neighborhood
                                )
                            ):
                                raise ValueError("MIP retime returned an invalid snapshot")
                            retimed_snapshot = _objective(
                                retimed_snapshot, context.instance
                            )
                            if (
                                retimed_snapshot.objective.total
                                > candidate.objective.total + 1e-9
                            ):
                                raise ValueError("MIP retime worsened the candidate")
                            candidate = retimed_snapshot
                        evidence = validate_candidate(candidate)
                        if evidence is None:
                            raise ValueError("MIP candidate failed canonical full check")

                    metric.feasible += 1
                    engine_stats[selected_engine_name][1] += 1
                    delta = candidate.objective.total - current.objective.total
                    metric.delta_sum += delta
                    warmup = iterations < config.warmup_iterations
                    if delta < 0.0 and warmup:
                        improving_samples.append(-delta)
                    if iterations + 1 == config.warmup_iterations:
                        if improving_samples:
                            temperature = statistics.median(improving_samples) / math.log(2.0)
                    accepted = accept_candidate(
                        delta,
                        temperature,
                        rng,
                        allow_worse=not warmup,
                    )
                    if accepted:
                        current = candidate
                        metric.accepted += 1
                        accepted_trace.append(current.objective.total)
                        if not mip_transaction:
                            pending_retime.update(repaired.changed_ids)
                        if try_install(current, evidence):
                            metric.new_best += 1
                            segment_scores[operator_index] += config.rewards[0]
                            best_trace.append(best.objective.total)
                            iterations_since_best = 0
                        else:
                            if delta < 0.0:
                                segment_scores[operator_index] += config.rewards[1]
                            elif delta > 0.0:
                                segment_scores[operator_index] += config.rewards[2]
                            iterations_since_best += 1

                        if not mip_transaction:
                            apply_pending_retime(metric, operator_index)
                    else:
                        iterations_since_best += 1
        except Exception:
            metric.exceptions += 1
            iterations_since_best += 1
        finally:
            metric.time_s += max(0.0, context.clock() - iteration_started)

        iterations += 1
        if temperature is not None and iterations >= config.warmup_iterations:
            temperature = max(temperature * cooling, 1e-300)
        if iterations % config.segment == 0:
            weights = update_weights(
                weights,
                tuple(segment_uses),
                tuple(segment_scores),
                config.reaction,
                config.weight_floor,
            )
            segment_uses = [0 for _ in operators]
            segment_scores = [0.0 for _ in operators]

        stalled = (
            iterations_since_best >= config.stall_iterations
            and context.clock() - started
            >= config.stall_time_fraction * max(0.0, budget.limit)
        )
        if stalled:
            destroy_size = grow_destroy_size(destroy_size, len(current.placements))
            if len(context.repair_engines) > 1:
                stalled_engine_pending = True
            if temperature is not None and improving_samples:
                temperature = max(
                    temperature,
                    statistics.median(improving_samples) / math.log(2.0),
                )
            iterations_since_best = 0
            if retime_hook is not None and pending_retime:
                apply_pending_retime(metric, operator_index, force=True)
        if invariant_errors >= config.max_invariant_errors:
            exit_reason = "INVARIANT_ERROR"
            break
    else:
        exit_reason = "DEADLINE"

    cache_after = context.kernel.cache_info()
    metrics = AlnsMetrics(
        per_operator=tuple(
            (operator.name, metric.freeze())
            for operator, metric in zip(operators, counters)
        ),
        iterations=iterations,
        best_trace=tuple(best_trace),
        accepted_trace=tuple(accepted_trace),
        cache_stats=_cache_delta(cache_before, cache_after),
        time_by_phase=tuple((name, phase_time[name]) for name in sorted(phase_time)),
        weights=tuple((operator.name, weight) for operator, weight in zip(operators, weights)),
        temperature=temperature,
        destroy_size=destroy_size,
        retime_triggers=retime_triggers,
        repair_engines=tuple(
            (
                name,
                int(values[0]),
                int(values[1]),
                int(values[2]),
                values[3],
            )
            for name, values in engine_stats.items()
        ),
        exit_reason=exit_reason,
    )
    return AlnsResult(
        snapshot=best_store.snapshot,
        current=current,
        serialized_operations=best_store.operations,
        metrics=metrics,
    )


__all__ = [
    "AlnsConfig",
    "AlnsContext",
    "AlnsMetrics",
    "AlnsResult",
    "OperatorMetrics",
    "RepairEngine",
    "accept_candidate",
    "grow_destroy_size",
    "initial_destroy_size",
    "run_lns",
    "update_weights",
]
