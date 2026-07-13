"""Deterministic checker-float bay assignment for the S1 constructor."""

from __future__ import annotations

from dataclasses import dataclass, replace
import math
from collections.abc import Callable, Mapping, Sequence
from statistics import median

from .config import DEFAULT_CONFIG, SolverConfig
from .exact import (
    AssignmentRequest,
    AssignmentCall,
    AssignmentResult as ExactAssignmentResult,
    AssignmentSolution,
    SOLUTION_STATUSES,
    assign as exact_assign,
)
from .geometry import ShapeInfo
from .cpsat_backend import assign_cpsat
from .gurobi_backend import assign_gurobi
from .instance import ProblemInstance


@dataclass(frozen=True, slots=True)
class AssignmentCost:
    """One fitting bay/orientation candidate and its exact diagnostics."""

    bay_id: int
    orient_idx: int
    z2_before: float
    z2_after: float
    z2_delta: float
    preference_loss: float
    weighted_cost: float
    congestion_ratio: float
    area_time: float


@dataclass(frozen=True, slots=True)
class BlockAssignment:
    """The selected candidate plus its regret at commitment time."""

    block_id: int
    bay_id: int
    orient_idx: int
    regret: float
    weighted_cost: float
    z2_after: float
    preference_loss: float
    congestion_ratio: float


@dataclass(frozen=True, slots=True)
class AssignmentMetrics:
    """Structural counters required by the S1 assignment evidence."""

    assigned: int
    fallback: int
    fit_failures: int
    candidate_evaluations: int
    horizon: int


@dataclass(frozen=True, slots=True)
class AssignmentResult:
    """Complete deterministic assignment and checker-float diagnostics."""

    assignments: dict[int, BlockAssignment]
    order: tuple[int, ...]
    bay_workloads: tuple[float, ...]
    bay_area_time: tuple[float, ...]
    z2: float
    z3: float
    metrics: AssignmentMetrics


@dataclass(frozen=True, slots=True)
class AssignmentEvaluation:
    """Exact checker-float assignment terms recomputed outside a backend."""

    bay_workloads: tuple[float, ...]
    z2: float
    z3: float
    overload: float
    objective: float


@dataclass(frozen=True, slots=True)
class AssignmentVerification:
    """Official-check outcome for one constructed assignment proposal."""

    feasible: bool
    objective: float | None
    reason: str | None = None


@dataclass(frozen=True, slots=True)
class AssignmentAttempt:
    """One normalized backend proposal and its exact-float decision."""

    backend: str
    status: str
    accepted: bool
    exact_z2: float | None
    exact_z3: float | None
    reason: str | None


@dataclass(frozen=True, slots=True)
class AssignmentSelection:
    """Best retained assignment with complete fallback provenance."""

    assignment: AssignmentResult
    backend: str
    evaluation: AssignmentEvaluation
    checker_objective: float | None
    verified: bool
    fallback_reason: str | None
    attempts: tuple[AssignmentAttempt, ...]


def assignment_cost(
    instance: ProblemInstance,
    block_id: int,
    bay_id: int,
    orient_idx: int,
    bay_workloads: Sequence[float],
    bay_area_time: Sequence[float],
    horizon: int,
    *,
    shape_area: float | None = None,
) -> AssignmentCost:
    """Return exact Z2/Z3 terms and the instance-scaled congestion diagnostic.

    Congestion is deliberately a deterministic tie-break diagnostic in S1-01.
    Its later coefficient is measurement-owned and must not reverse unequal
    checker-float assignment costs before that calibration occurs.
    """
    if not instance.fit_matrix[block_id][bay_id][orient_idx]:
        raise ValueError("assignment candidate does not fit the requested bay")
    if len(bay_workloads) != len(instance.bays):
        raise ValueError("bay_workloads length does not match the instance")
    if len(bay_area_time) != len(instance.bays):
        raise ValueError("bay_area_time length does not match the instance")
    if horizon <= 0:
        raise ValueError("horizon must be positive")

    block = instance.blocks[block_id]
    before = _load_range(instance, bay_workloads)
    prospective = list(float(value) for value in bay_workloads)
    prospective[bay_id] += block.workload
    after = _load_range(instance, prospective)
    preference_loss = max(block.bay_preferences) - block.bay_preferences[bay_id]
    area = (
        float(shape_area)
        if shape_area is not None
        else ShapeInfo.from_orientation(block.orientations[orient_idx]).area
    )
    area_time = area * block.dwell
    congestion_ratio = (
        float(bay_area_time[bay_id]) + area_time
    ) / (instance.bays[bay_id].area * horizon)
    _, w2, w3 = instance.weights
    z2_delta = after - before
    return AssignmentCost(
        bay_id=bay_id,
        orient_idx=orient_idx,
        z2_before=before,
        z2_after=after,
        z2_delta=z2_delta,
        preference_loss=preference_loss,
        weighted_cost=w2 * z2_delta + w3 * preference_loss,
        congestion_ratio=congestion_ratio,
        area_time=area_time,
    )


class AssignmentV1:
    """Assign blocks by recomputed best-vs-second-best bay regret."""

    def __init__(self, instance: ProblemInstance) -> None:
        instance.assert_solvable_fit()
        self.instance = instance
        self.horizon = _instance_horizon(instance)
        self._shape_areas = tuple(
            tuple(
                ShapeInfo.from_orientation(orientation).area
                for orientation in block.orientations
            )
            for block in instance.blocks
        )
        # Within one bay every orientation has the same checker-float Z2/Z3
        # cost.  Congestion then selects the smallest footprint, followed by
        # orientation ID, so this site can be chosen once without changing
        # AssignmentV1's ordering or result during multi-start construction.
        self._best_fit_by_bay = tuple(
            tuple(
                min(
                    instance.fitting_orientations(block_id, bay.bay_id),
                    key=lambda orient_idx: (
                        self._shape_areas[block_id][orient_idx],
                        orient_idx,
                    ),
                    default=None,
                )
                for bay in instance.bays
            )
            for block_id in range(len(instance.blocks))
        )

    def assign(self) -> AssignmentResult:
        workloads = [0.0 for _ in self.instance.bays]
        area_time = [0.0 for _ in self.instance.bays]
        remaining = set(range(len(self.instance.blocks)))
        selected: dict[int, BlockAssignment] = {}
        order: list[int] = []
        candidate_evaluations = 0

        while remaining:
            choices: list[tuple[int, AssignmentCost, float]] = []
            for block_id in sorted(remaining):
                by_bay: list[AssignmentCost] = []
                for bay in self.instance.bays:
                    orient_idx = self._best_fit_by_bay[block_id][bay.bay_id]
                    if orient_idx is None:
                        continue
                    by_bay.append(
                        assignment_cost(
                            self.instance,
                            block_id,
                            bay.bay_id,
                            orient_idx,
                            workloads,
                            area_time,
                            self.horizon,
                            shape_area=self._shape_areas[block_id][orient_idx],
                        )
                    )
                    candidate_evaluations += 1
                ranked = sorted(by_bay, key=_candidate_key)
                if not ranked:
                    raise AssertionError(
                        f"fit-qualified block {block_id} has no assignment candidate"
                    )
                regret = (
                    math.inf
                    if len(ranked) == 1
                    else max(0.0, ranked[1].weighted_cost - ranked[0].weighted_cost)
                )
                choices.append((block_id, ranked[0], regret))

            block_id, best, regret = min(
                choices,
                key=lambda item: (
                    -item[2],
                    item[1].weighted_cost,
                    item[1].congestion_ratio,
                    item[0],
                ),
            )
            block = self.instance.blocks[block_id]
            workloads[best.bay_id] += block.workload
            area_time[best.bay_id] += best.area_time
            selected[block_id] = BlockAssignment(
                block_id=block_id,
                bay_id=best.bay_id,
                orient_idx=best.orient_idx,
                regret=regret,
                weighted_cost=best.weighted_cost,
                z2_after=best.z2_after,
                preference_loss=best.preference_loss,
                congestion_ratio=best.congestion_ratio,
            )
            order.append(block_id)
            remaining.remove(block_id)

        z3 = sum(item.preference_loss for item in selected.values())
        return AssignmentResult(
            assignments=selected,
            order=tuple(order),
            bay_workloads=tuple(workloads),
            bay_area_time=tuple(area_time),
            z2=_load_range(self.instance, workloads),
            z3=z3,
            metrics=AssignmentMetrics(
                assigned=len(selected),
                fallback=0,
                fit_failures=0,
                candidate_evaluations=candidate_evaluations,
                horizon=self.horizon,
            ),
        )


def assignment_request(
    instance: ProblemInstance,
    starting_assignment: AssignmentResult,
    *,
    config: SolverConfig = DEFAULT_CONFIG,
) -> AssignmentRequest:
    """Build the immutable exact-float S4 request from a v1 MIP start."""

    if config.assignment_backend != "gurobi":
        raise ValueError("S4-01 supports only the Gurobi assignment backend")
    if not 0.0 < float(config.assignment_congestion_cap_fraction) <= 1.0:
        raise ValueError("assignment congestion cap fraction must be in (0, 1]")
    if float(config.assignment_congestion_weight_multiplier) < 0.0:
        raise ValueError("assignment congestion weight multiplier must be non-negative")
    instance.assert_solvable_fit()
    block_ids = tuple(range(len(instance.blocks)))
    bay_ids = tuple(bay.bay_id for bay in instance.bays)
    if set(starting_assignment.assignments) != set(block_ids):
        raise ValueError("starting assignment must contain every block exactly once")

    shape_areas = tuple(
        tuple(
            ShapeInfo.from_orientation(orientation).area
            for orientation in block.orientations
        )
        for block in instance.blocks
    )
    fit_pairs = tuple(
        (block_id, bay_id)
        for block_id in block_ids
        for bay_id in bay_ids
        if instance.fitting_orientations(block_id, bay_id)
    )
    preference_penalties = tuple(
        (
            block_id,
            bay_id,
            float(
                max(instance.blocks[block_id].bay_preferences)
                - instance.blocks[block_id].bay_preferences[bay_id]
            ),
        )
        for block_id, bay_id in fit_pairs
    )
    congestion_demands = tuple(
        (
            block_id,
            bay_id,
            float(
                min(
                    shape_areas[block_id][orient_idx]
                    for orient_idx in instance.fitting_orientations(block_id, bay_id)
                )
                * instance.blocks[block_id].dwell
            ),
        )
        for block_id, bay_id in fit_pairs
    )
    average_area = sum(bay.area for bay in instance.bays) / len(instance.bays)
    horizon = _instance_horizon(instance)
    representative_areas = tuple(
        min(areas) for areas in shape_areas
    )
    w1, w2, w3 = instance.weights
    congestion_weight = (
        max(0.0, float(w1))
        / max(1.0, float(median(representative_areas or (1.0,))))
        * float(config.assignment_congestion_weight_multiplier)
    )
    return AssignmentRequest(
        block_ids=block_ids,
        bay_ids=bay_ids,
        fit_pairs=fit_pairs,
        workloads=tuple(
            (block.block_id, float(block.workload)) for block in instance.blocks
        ),
        preference_penalties=preference_penalties,
        load_factors=tuple(
            (bay.bay_id, float(average_area / bay.area)) for bay in instance.bays
        ),
        congestion_demands=congestion_demands,
        congestion_capacities=tuple(
            (
                bay.bay_id,
                float(
                    config.assignment_congestion_cap_fraction
                    * bay.area
                    * horizon
                ),
            )
            for bay in instance.bays
        ),
        current_assignment=tuple(
            (
                block_id,
                starting_assignment.assignments[block_id].bay_id,
            )
            for block_id in block_ids
        ),
        weights=(float(w2), float(w3)),
        congestion_weight=float(congestion_weight),
        seed=config.constructor_seed,
        threads=config.assignment_threads,
    )


def evaluate_assignment_solution(
    request: AssignmentRequest,
    solution: AssignmentSolution,
) -> AssignmentEvaluation:
    """Recompute exact-float Z2/Z3 and soft overload from pure assignments."""

    if not isinstance(solution, tuple):
        raise TypeError("assignment solution must be a tuple")
    selected = dict(solution)
    if len(selected) != len(solution) or set(selected) != set(request.block_ids):
        raise ValueError("assignment solution must contain every block exactly once")
    feasible = set(request.fit_pairs)
    if any(pair not in feasible for pair in solution):
        raise ValueError("assignment solution contains a non-fitting pair")
    workloads = dict(request.workloads)
    factors = dict(request.load_factors)
    penalties = {
        (block_id, bay_id): value
        for block_id, bay_id, value in request.preference_penalties
    }
    demands = {
        (block_id, bay_id): value
        for block_id, bay_id, value in request.congestion_demands
    }
    capacities = dict(request.congestion_capacities)
    bay_workloads = {bay_id: 0.0 for bay_id in request.bay_ids}
    bay_demands = {bay_id: 0.0 for bay_id in request.bay_ids}
    z3 = 0.0
    for block_id, bay_id in solution:
        bay_workloads[bay_id] += workloads[block_id]
        bay_demands[bay_id] += demands[(block_id, bay_id)]
        z3 += penalties[(block_id, bay_id)]
    weighted_loads = tuple(
        factors[bay_id] * bay_workloads[bay_id] for bay_id in request.bay_ids
    )
    z2 = (
        max(weighted_loads) - min(weighted_loads)
        if len(weighted_loads) >= 2
        else 0.0
    )
    overload = sum(
        max(0.0, bay_demands[bay_id] - capacities[bay_id])
        for bay_id in request.bay_ids
    )
    w2, w3 = request.weights
    return AssignmentEvaluation(
        bay_workloads=tuple(bay_workloads[bay_id] for bay_id in request.bay_ids),
        z2=float(z2),
        z3=float(z3),
        overload=float(overload),
        objective=float(
            w2 * z2 + w3 * z3 + request.congestion_weight * overload
        ),
    )


def assignment_from_solution(
    instance: ProblemInstance,
    solution: AssignmentSolution,
    *,
    order: Sequence[int] | None = None,
) -> AssignmentResult:
    """Convert a pure assignment into the constructor's exact v1-compatible plan."""

    if not isinstance(solution, tuple):
        raise TypeError("assignment solution must be a tuple")
    selected_bays = dict(solution)
    expected = set(range(len(instance.blocks)))
    if len(selected_bays) != len(solution) or set(selected_bays) != expected:
        raise ValueError("assignment solution must contain every block exactly once")
    selected_order = tuple(range(len(instance.blocks))) if order is None else tuple(order)
    if len(selected_order) != len(expected) or set(selected_order) != expected:
        raise ValueError("assignment order must contain every block exactly once")

    workloads = [0.0 for _ in instance.bays]
    area_time = [0.0 for _ in instance.bays]
    assignments: dict[int, BlockAssignment] = {}
    for block_id in selected_order:
        bay_id = selected_bays[block_id]
        fitting = instance.fitting_orientations(block_id, bay_id)
        if not fitting:
            raise ValueError(f"assignment pair {(block_id, bay_id)} does not fit")
        orient_idx = min(
            fitting,
            key=lambda candidate: (
                ShapeInfo.from_orientation(
                    instance.blocks[block_id].orientations[candidate]
                ).area,
                candidate,
            ),
        )
        block_spec = instance.blocks[block_id]
        area = ShapeInfo.from_orientation(
            block_spec.orientations[orient_idx]
        ).area
        workloads[bay_id] += block_spec.workload
        area_time[bay_id] += area * block_spec.dwell
        preference_loss = (
            max(block_spec.bay_preferences) - block_spec.bay_preferences[bay_id]
        )
        assignments[block_id] = BlockAssignment(
            block_id=block_id,
            bay_id=bay_id,
            orient_idx=orient_idx,
            regret=0.0,
            weighted_cost=float(instance.weights[2] * preference_loss),
            z2_after=0.0,
            preference_loss=float(preference_loss),
            congestion_ratio=float(
                area_time[bay_id]
                / (instance.bays[bay_id].area * _instance_horizon(instance))
            ),
        )
    z2 = _load_range(instance, workloads)
    assignments = {
        block_id: replace(item, z2_after=z2)
        for block_id, item in assignments.items()
    }
    return AssignmentResult(
        assignments=assignments,
        order=selected_order,
        bay_workloads=tuple(workloads),
        bay_area_time=tuple(area_time),
        z2=float(z2),
        z3=float(sum(item.preference_loss for item in assignments.values())),
        metrics=AssignmentMetrics(
            assigned=len(assignments),
            fallback=0,
            fit_failures=0,
            candidate_evaluations=sum(
                1
                for block_id in expected
                for bay_id in range(len(instance.bays))
                if instance.fitting_orientations(block_id, bay_id)
            ),
            horizon=_instance_horizon(instance),
        ),
    )


def assignment_v2(
    instance: ProblemInstance,
    starting_assignment: AssignmentResult,
    *,
    config: SolverConfig = DEFAULT_CONFIG,
) -> ExactAssignmentResult:
    """Run only the isolated S4-01 Gurobi assignment proposal."""

    request = assignment_request(instance, starting_assignment, config=config)
    raw = exact_assign(
        "gurobi",
        request,
        assign_gurobi,
        timebox=config.assignment_timebox_seconds,
    )
    if raw.status not in SOLUTION_STATUSES or raw.solution is None:
        return raw
    evaluated = evaluate_assignment_solution(request, raw.solution)
    return replace(
        raw,
        objective=evaluated.objective,
        z2=evaluated.z2,
        z3=evaluated.z3,
        overload=evaluated.overload,
    )


def choose_assignment_candidate(
    instance: ProblemInstance,
    starting_assignment: AssignmentResult,
    *,
    config: SolverConfig = DEFAULT_CONFIG,
    backend_calls: Mapping[str, AssignmentCall] | None = None,
    verify: Callable[[AssignmentResult], AssignmentVerification] | None = None,
) -> AssignmentSelection:
    """Evaluate Gurobi and scaled CP-SAT proposals without risking v1.

    Backend-reported objective terms are never used for acceptance.  Every
    pure assignment is recomputed with checker-float Z2/Z3 first.  A proposal
    that regresses exact Z2 or does not improve the exact assignment objective
    is not viable.  When a construction/check callback is supplied, every
    viable proposal is checked and only the best verified objective replaces
    the already-verified v1 incumbent.
    """

    calls = dict(
        backend_calls
        or {
            "gurobi": assign_gurobi,
            "cpsat": assign_cpsat,
        }
    )
    if set(calls) != {"gurobi", "cpsat"}:
        raise ValueError("backend_calls must provide gurobi and cpsat")

    request = assignment_request(instance, starting_assignment, config=config)
    incumbent_solution = tuple(request.current_assignment)
    incumbent_evaluation = evaluate_assignment_solution(request, incumbent_solution)
    incumbent_verification = (
        verify(starting_assignment) if verify is not None else None
    )
    if incumbent_verification is not None and not incumbent_verification.feasible:
        raise ValueError("starting assignment must be checker-feasible")

    best_assignment = starting_assignment
    best_backend = "greedy"
    best_evaluation = incumbent_evaluation
    best_checker_objective = (
        incumbent_verification.objective
        if incumbent_verification is not None
        else None
    )
    best_verified = incumbent_verification is not None
    attempts: list[AssignmentAttempt] = []
    fallback: list[str] = []

    for backend in ("gurobi", "cpsat"):
        raw = exact_assign(
            backend,
            request,
            calls[backend],
            timebox=config.assignment_timebox_seconds,
        )
        if raw.status not in SOLUTION_STATUSES or raw.solution is None:
            reason = raw.reason or raw.status
            attempts.append(
                AssignmentAttempt(backend, raw.status, False, None, None, reason)
            )
            fallback.append(f"{backend}:{raw.status}")
            continue

        evaluated = evaluate_assignment_solution(request, raw.solution)
        if _rel_greater(evaluated.z2, incumbent_evaluation.z2):
            reason = "float_z2_regression"
            attempts.append(
                AssignmentAttempt(
                    backend,
                    raw.status,
                    False,
                    evaluated.z2,
                    evaluated.z3,
                    reason,
                )
            )
            fallback.append(f"{backend}:{reason}")
            continue
        if not _rel_less(evaluated.objective, incumbent_evaluation.objective):
            reason = "float_objective_non_improvement"
            attempts.append(
                AssignmentAttempt(
                    backend,
                    raw.status,
                    False,
                    evaluated.z2,
                    evaluated.z3,
                    reason,
                )
            )
            fallback.append(f"{backend}:{reason}")
            continue

        proposed = assignment_from_solution(
            instance,
            raw.solution,
            order=starting_assignment.order,
        )
        verification = verify(proposed) if verify is not None else None
        if verification is not None and not verification.feasible:
            reason = verification.reason or "checker_rejected"
            attempts.append(
                AssignmentAttempt(
                    backend,
                    raw.status,
                    False,
                    evaluated.z2,
                    evaluated.z3,
                    reason,
                )
            )
            fallback.append(f"{backend}:checker_rejected")
            continue

        checker_objective = (
            verification.objective if verification is not None else None
        )
        if verification is not None:
            if checker_objective is None or not math.isfinite(checker_objective):
                reason = "checker_objective_missing"
                attempts.append(
                    AssignmentAttempt(
                        backend,
                        raw.status,
                        False,
                        evaluated.z2,
                        evaluated.z3,
                        reason,
                    )
                )
                fallback.append(f"{backend}:{reason}")
                continue
            better = (
                best_checker_objective is None
                or _rel_less(checker_objective, best_checker_objective)
            )
        else:
            better = _rel_less(evaluated.objective, best_evaluation.objective)

        if better:
            best_assignment = proposed
            best_backend = backend
            best_evaluation = evaluated
            best_checker_objective = checker_objective
            best_verified = verification is not None
            accepted = True
            reason = None
        else:
            accepted = False
            reason = "verified_objective_non_improvement"
            fallback.append(f"{backend}:{reason}")
        attempts.append(
            AssignmentAttempt(
                backend,
                raw.status,
                accepted,
                evaluated.z2,
                evaluated.z3,
                reason,
            )
        )

    return AssignmentSelection(
        assignment=best_assignment,
        backend=best_backend,
        evaluation=best_evaluation,
        checker_objective=best_checker_objective,
        verified=best_verified,
        fallback_reason=";".join(fallback) or None,
        attempts=tuple(attempts),
    )


def _candidate_key(candidate: AssignmentCost) -> tuple[float, float, int, int]:
    return (
        candidate.weighted_cost,
        candidate.congestion_ratio,
        candidate.bay_id,
        candidate.orient_idx,
    )


def _rel_less(left: float, right: float) -> bool:
    tolerance = 1e-9 * max(1.0, abs(float(right)))
    return float(left) < float(right) - tolerance


def _rel_greater(left: float, right: float) -> bool:
    tolerance = 1e-9 * max(1.0, abs(float(right)))
    return float(left) > float(right) + tolerance


def _instance_horizon(instance: ProblemInstance) -> int:
    if not instance.blocks:
        return 1
    start = min(block.release_time for block in instance.blocks)
    finish = max(
        max(block.due_date, block.release_time + block.dwell)
        for block in instance.blocks
    )
    return max(1, finish - start)


def _load_range(instance: ProblemInstance, workloads: Sequence[float]) -> float:
    if len(instance.bays) < 2:
        return 0.0
    average_area = sum(bay.area for bay in instance.bays) / len(instance.bays)
    normalized = tuple(
        average_area / bay.area * float(load)
        for bay, load in zip(instance.bays, workloads, strict=True)
    )
    return max(normalized) - min(normalized)
