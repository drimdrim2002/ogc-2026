"""Lazy, bounded CP-SAT adapter for fixed-layout retiming."""

from __future__ import annotations

from dataclasses import dataclass
from fractions import Fraction
import importlib
import math
import time
from typing import Any

from .exact import AssignmentRequest, AssignmentResult, ExactResult, RetimeRequest


ASSIGNMENT_SCALE = 1_000_000
_CP_SAT_SAFE_INTEGER = (1 << 62) - 1


@dataclass(frozen=True, slots=True)
class CpSatVariableSpec:
    block_id: int
    lower_bound: int
    upper_bound: int
    start: int
    variable_type: str


@dataclass(frozen=True, slots=True)
class CpSatDisjunctionSpec:
    left: int
    right: int
    start: int
    variable_type: str = "binary"


@dataclass(frozen=True, slots=True)
class CpSatModelSpec:
    horizon: int
    entries: tuple[CpSatVariableSpec, ...]
    tardiness: tuple[CpSatVariableSpec, ...]
    disjunctions: tuple[CpSatDisjunctionSpec, ...]
    objective_blocks: tuple[int, ...]
    workers: int
    seed: int
    time_limit: float
    log_search_progress: bool


@dataclass(frozen=True, slots=True)
class CpSatAssignmentPairSpec:
    block_id: int
    bay_id: int
    weighted_load: int
    preference_penalty: int
    congestion_demand: int
    start: int


@dataclass(frozen=True, slots=True)
class CpSatAssignmentModelSpec:
    scale: int
    coefficient_scale: int
    objective_normalizer: float
    pairs: tuple[CpSatAssignmentPairSpec, ...]
    congestion_capacities: tuple[tuple[int, int], ...]
    bay_load_upper_bounds: tuple[tuple[int, int], ...]
    bay_demand_upper_bounds: tuple[tuple[int, int], ...]
    z3_upper_bound: int
    overload_upper_bound: int
    objective_upper_bound: int
    w2: int
    w3: int
    congestion_weight: int
    workers: int
    seed: int
    time_limit: float
    log_search_progress: bool

    @property
    def objective_unscale_factor(self) -> float:
        """Convert the integer backend objective into checker-float units."""

        return self.objective_normalizer / float(
            self.scale * self.coefficient_scale
        )


def build_cpsat_model_spec(
    request: RetimeRequest,
    *,
    timebox: float,
) -> CpSatModelSpec:
    """Build the solver-independent CP-SAT model description."""

    limit = _timebox(timebox)
    releases = dict(request.releases)
    dues = dict(request.dues)
    dwells = dict(request.dwells)
    current = dict(request.current_entries)
    horizon = max(releases.values(), default=0) + sum(dwells.values())
    entries = tuple(
        CpSatVariableSpec(
            block_id=block_id,
            lower_bound=releases[block_id],
            upper_bound=horizon,
            start=current[block_id],
            variable_type="integer",
        )
        for block_id in request.block_ids
    )
    tardiness = tuple(
        CpSatVariableSpec(
            block_id=block_id,
            lower_bound=0,
            upper_bound=horizon + dwells[block_id],
            start=max(0, current[block_id] + dwells[block_id] - dues[block_id]),
            variable_type="integer",
        )
        for block_id in request.block_ids
    )
    disjunctions = tuple(
        CpSatDisjunctionSpec(
            left=left,
            right=right,
            start=1 if current[left] + dwells[left] <= current[right] else 0,
        )
        for left, right in request.conflict_pairs
    )
    return CpSatModelSpec(
        horizon=horizon,
        entries=entries,
        tardiness=tardiness,
        disjunctions=disjunctions,
        objective_blocks=request.block_ids,
        workers=min(request.threads, 4),
        seed=request.seed,
        time_limit=limit,
        log_search_progress=False,
    )


def build_cpsat_assignment_spec(
    request: AssignmentRequest,
    *,
    timebox: float,
) -> CpSatAssignmentModelSpec:
    """Scale the S4 assignment proposal and reject unsafe int64 models."""

    limit = _timebox(timebox)
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
    current = dict(request.current_assignment)
    pairs = tuple(
        CpSatAssignmentPairSpec(
            block_id=block_id,
            bay_id=bay_id,
            weighted_load=_scaled(factors[bay_id] * workloads[block_id]),
            preference_penalty=_scaled(penalties[(block_id, bay_id)]),
            congestion_demand=_scaled(demands[(block_id, bay_id)]),
            start=int(current[block_id] == bay_id),
        )
        for block_id, bay_id in request.fit_pairs
    )
    capacities = tuple(
        (bay_id, _scaled(value))
        for bay_id, value in request.congestion_capacities
    )
    load_upper_bounds = tuple(
        (
            bay_id,
            sum(
                pair.weighted_load
                for pair in pairs
                if pair.bay_id == bay_id
            ),
        )
        for bay_id in request.bay_ids
    )
    z3_upper_bound = sum(
        max(
            pair.preference_penalty
            for pair in pairs
            if pair.block_id == block_id
        )
        for block_id in request.block_ids
    )
    capacity_by_bay = dict(capacities)
    demand_upper_bounds = tuple(
        (
            bay_id,
            sum(
                pair.congestion_demand
                for pair in pairs
                if pair.bay_id == bay_id
            ),
        )
        for bay_id in request.bay_ids
    )
    overload_upper_bound = sum(
        max(
            0,
            demand_upper_bound - capacity_by_bay[bay_id],
        )
        for bay_id, demand_upper_bound in demand_upper_bounds
    )
    max_load_upper_bound = max(
        (value for _bay_id, value in load_upper_bounds),
        default=0,
    )
    data_guarded = (
        *(pair.weighted_load for pair in pairs),
        *(pair.preference_penalty for pair in pairs),
        *(pair.congestion_demand for pair in pairs),
        *(value for _bay_id, value in capacities),
        *(value for _bay_id, value in load_upper_bounds),
        *(value for _bay_id, value in demand_upper_bounds),
        z3_upper_bound,
        overload_upper_bound,
    )
    if any(
        value < 0 or value > _CP_SAT_SAFE_INTEGER
        for value in data_guarded
    ):
        raise OverflowError(
            "unsafe CP-SAT assignment scaling exceeds the signed int64 safety bound"
        )

    raw_objective_weights = (
        float(request.weights[0]),
        float(request.weights[1]),
        float(request.congestion_weight),
    )
    (
        objective_normalizer,
        coefficient_scale,
        scaled_weights,
        objective_upper_bound,
    ) = _select_assignment_objective_scale(
        raw_objective_weights,
        (
            max_load_upper_bound,
            z3_upper_bound,
            overload_upper_bound,
        ),
    )
    w2, w3, congestion_weight = scaled_weights
    guarded = (
        w2,
        w3,
        congestion_weight,
        objective_upper_bound,
    )
    if any(value < 0 or value > _CP_SAT_SAFE_INTEGER for value in guarded):
        raise OverflowError(
            "unsafe CP-SAT assignment scaling exceeds the signed int64 safety bound"
        )
    return CpSatAssignmentModelSpec(
        scale=ASSIGNMENT_SCALE,
        coefficient_scale=coefficient_scale,
        objective_normalizer=objective_normalizer,
        pairs=pairs,
        congestion_capacities=capacities,
        bay_load_upper_bounds=load_upper_bounds,
        bay_demand_upper_bounds=demand_upper_bounds,
        z3_upper_bound=z3_upper_bound,
        overload_upper_bound=overload_upper_bound,
        objective_upper_bound=objective_upper_bound,
        w2=w2,
        w3=w3,
        congestion_weight=congestion_weight,
        workers=min(request.threads, 4),
        seed=request.seed,
        time_limit=limit,
        log_search_progress=False,
    )


def assign_cpsat(request: AssignmentRequest, timebox: float) -> AssignmentResult:
    """Solve one scaled assignment proposal and return only pure data."""

    limit = _timebox(timebox)
    total_started = time.monotonic()
    if limit <= 0.0:
        return _empty_assignment("time_limit", "no CP-SAT assignment time remains")
    try:
        spec = build_cpsat_assignment_spec(request, timebox=limit)
    except OverflowError as exc:
        return _empty_assignment("unavailable", str(exc))

    try:
        cp_model = importlib.import_module("ortools.sat.python.cp_model")
    except Exception as exc:
        return _empty_assignment(
            "unavailable",
            f"CP-SAT unavailable at import: {type(exc).__name__}: {exc}",
            build_s=time.monotonic() - total_started,
        )

    model: Any | None = None
    solver: Any | None = None
    try:
        try:
            model = cp_model.CpModel()
            variables = _populate_assignment_model(model, request, spec)
            solver = cp_model.CpSolver()
        except Exception as exc:
            return _empty_assignment(
                "error",
                f"CP-SAT assignment model build error: {type(exc).__name__}: {exc}",
                build_s=time.monotonic() - total_started,
            )

        build_s = time.monotonic() - total_started
        remaining = max(0.0, limit - build_s)
        if remaining <= 0.0:
            return _empty_assignment(
                "time_limit",
                "CP-SAT assignment construction consumed the timebox",
                build_s=build_s,
            )
        solver.parameters.max_time_in_seconds = max(
            0.001, remaining - min(0.25, 0.25 * remaining)
        )
        solver.parameters.num_search_workers = spec.workers
        solver.parameters.random_seed = spec.seed
        solver.parameters.log_search_progress = spec.log_search_progress

        first_solution: list[float | None] = [None]
        solve_started = time.monotonic()

        class FirstSolutionCallback(cp_model.CpSolverSolutionCallback):
            def on_solution_callback(self) -> None:
                if first_solution[0] is None:
                    first_solution[0] = time.monotonic() - solve_started

        try:
            status_code = solver.solve(model, FirstSolutionCallback())
        except Exception as exc:
            return _empty_assignment(
                "error",
                f"CP-SAT assignment optimize error: {type(exc).__name__}: {exc}",
                build_s=build_s,
                solve_s=time.monotonic() - solve_started,
            )
        solve_s = time.monotonic() - solve_started
        if status_code not in {cp_model.OPTIMAL, cp_model.FEASIBLE}:
            status = (
                "time_limit"
                if status_code == cp_model.UNKNOWN
                else "error"
                if status_code == cp_model.MODEL_INVALID
                else "no_solution"
            )
            return _empty_assignment(
                status,
                f"CP-SAT assignment finished without a solution (status={solver.status_name(status_code)})",
                bound=(
                    float(solver.best_objective_bound)
                    * spec.objective_unscale_factor
                ),
                build_s=build_s,
                solve_s=solve_s,
            )

        try:
            assignments = variables["assignments"]
            solution = tuple(
                (
                    block_id,
                    next(
                        bay_id
                        for candidate_block, bay_id in request.fit_pairs
                        if candidate_block == block_id
                        and solver.value(assignments[(block_id, bay_id)])
                    ),
                )
                for block_id in request.block_ids
            )
            objective = (
                float(solver.objective_value)
                * spec.objective_unscale_factor
            )
            bound = (
                float(solver.best_objective_bound)
                * spec.objective_unscale_factor
            )
            z2 = float(
                solver.value(variables["maximum_load"])
                - solver.value(variables["minimum_load"])
            ) / spec.scale
            z3 = float(solver.value(variables["z3"])) / spec.scale
            overload = float(
                sum(solver.value(value) for value in variables["overloads"].values())
            ) / spec.scale
        except Exception as exc:
            return _empty_assignment(
                "error",
                f"CP-SAT assignment extraction error: {type(exc).__name__}: {exc}",
                build_s=build_s,
                solve_s=solve_s,
            )
        first = first_solution[0]
        if first is None:
            first = solve_s
        first = min(max(0.0, float(first)), solve_s)
        return AssignmentResult(
            backend="cpsat",
            status="optimal" if status_code == cp_model.OPTIMAL else "feasible",
            solution=solution,
            objective=objective,
            bound=bound,
            z2=z2,
            z3=z3,
            overload=overload,
            build_s=build_s,
            solve_s=solve_s,
            first_solution_s=first,
            reason=None,
        )
    finally:
        model = None
        solver = None


def retime_cpsat(request: RetimeRequest, timebox: float) -> ExactResult:
    """Solve one immutable retiming request without exposing solver objects."""

    limit = _timebox(timebox)
    total_started = time.monotonic()
    spec = build_cpsat_model_spec(request, timebox=limit)
    if limit <= 0.0:
        return _empty("time_limit", "no CP-SAT retiming time remains")

    try:
        cp_model = importlib.import_module("ortools.sat.python.cp_model")
    except Exception as exc:
        return _empty(
            "unavailable",
            f"CP-SAT unavailable at import: {type(exc).__name__}: {exc}",
            build_s=time.monotonic() - total_started,
        )

    model: Any | None = None
    solver: Any | None = None
    try:
        try:
            model = cp_model.CpModel()
            entries, tardiness = _populate_model(cp_model, model, request, spec)
            solver = cp_model.CpSolver()
        except Exception as exc:
            return _empty(
                "error",
                f"CP-SAT model build error: {type(exc).__name__}: {exc}",
                build_s=time.monotonic() - total_started,
            )

        build_s = time.monotonic() - total_started
        remaining = max(0.0, limit - build_s)
        if remaining <= 0.0:
            return _empty(
                "time_limit",
                "CP-SAT model construction consumed the exact timebox",
                build_s=build_s,
            )
        # Keep model teardown and pure-data extraction inside the caller's
        # strict allowance; the outer gate separately permits 0.25s of
        # pipeline scheduling noise.
        solver.parameters.max_time_in_seconds = max(
            0.001, remaining - min(0.25, 0.25 * remaining)
        )
        solver.parameters.num_search_workers = spec.workers
        solver.parameters.random_seed = spec.seed
        solver.parameters.log_search_progress = spec.log_search_progress

        first_solution: list[float | None] = [None]
        solve_started = time.monotonic()

        class FirstSolutionCallback(cp_model.CpSolverSolutionCallback):
            def on_solution_callback(self) -> None:
                if first_solution[0] is None:
                    first_solution[0] = time.monotonic() - solve_started

        callback = FirstSolutionCallback()
        try:
            status_code = solver.solve(model, callback)
        except Exception as exc:
            return _empty(
                "error",
                f"CP-SAT optimize error: {type(exc).__name__}: {exc}",
                build_s=build_s,
                solve_s=time.monotonic() - solve_started,
            )
        solve_s = time.monotonic() - solve_started
        if status_code not in {cp_model.OPTIMAL, cp_model.FEASIBLE}:
            if status_code == cp_model.UNKNOWN:
                status = "time_limit"
            elif status_code == cp_model.MODEL_INVALID:
                status = "error"
            else:
                status = "no_solution"
            return _empty(
                status,
                f"CP-SAT finished without a solution (status={solver.status_name(status_code)})",
                bound=_finite_value(solver.best_objective_bound),
                build_s=build_s,
                solve_s=solve_s,
            )

        try:
            dwells = dict(request.dwells)
            solution = tuple(
                (
                    block_id,
                    int(solver.value(entries[block_id])),
                    int(solver.value(entries[block_id])) + dwells[block_id],
                )
                for block_id in request.block_ids
            )
            dues = dict(request.dues)
            objective = float(
                sum(
                    max(0, exit_time - dues[block_id])
                    for block_id, _entry, exit_time in solution
                )
            )
            bound = _finite_value(solver.best_objective_bound)
        except Exception as exc:
            return _empty(
                "error",
                f"CP-SAT extraction error: {type(exc).__name__}: {exc}",
                build_s=build_s,
                solve_s=solve_s,
            )
        first = first_solution[0]
        if first is None:
            first = solve_s
        first = min(max(0.0, float(first)), solve_s)
        return ExactResult(
            backend="cpsat",
            status="optimal" if status_code == cp_model.OPTIMAL else "feasible",
            solution=solution,
            objective=objective,
            bound=bound,
            build_s=build_s,
            solve_s=solve_s,
            first_solution_s=first,
            reason=None,
        )
    finally:
        model = None
        solver = None


def _populate_assignment_model(
    model: Any,
    request: AssignmentRequest,
    spec: CpSatAssignmentModelSpec,
) -> dict[str, Any]:
    pair_specs = {
        (item.block_id, item.bay_id): item for item in spec.pairs
    }
    assignments = {
        pair: model.new_bool_var(f"x_{pair[0]}_{pair[1]}")
        for pair in request.fit_pairs
    }
    for block_id in request.block_ids:
        choices = [
            assignments[(candidate_block, bay_id)]
            for candidate_block, bay_id in request.fit_pairs
            if candidate_block == block_id
        ]
        model.add(sum(choices) == 1)
    for pair, variable in assignments.items():
        model.add_hint(variable, pair_specs[pair].start)

    load_upper_bounds = dict(spec.bay_load_upper_bounds)
    loads = {
        bay_id: model.new_int_var(
            0,
            load_upper_bounds[bay_id],
            f"load_{bay_id}",
        )
        for bay_id in request.bay_ids
    }
    for bay_id in request.bay_ids:
        model.add(
            loads[bay_id]
            == sum(
                pair_specs[pair].weighted_load * assignments[pair]
                for pair in request.fit_pairs
                if pair[1] == bay_id
            )
        )
    maximum_load_bound = max(load_upper_bounds.values(), default=0)
    maximum_load = model.new_int_var(
        0, maximum_load_bound, "maximum_weighted_load"
    )
    minimum_load = model.new_int_var(
        0, maximum_load_bound, "minimum_weighted_load"
    )
    model.add_max_equality(maximum_load, list(loads.values()))
    model.add_min_equality(minimum_load, list(loads.values()))

    z3 = model.new_int_var(0, spec.z3_upper_bound, "preference_penalty")
    model.add(
        z3
        == sum(
            pair_specs[pair].preference_penalty * assignments[pair]
            for pair in request.fit_pairs
        )
    )
    capacities = dict(spec.congestion_capacities)
    demand_upper_bounds = dict(spec.bay_demand_upper_bounds)
    demands = {
        bay_id: model.new_int_var(
            0, demand_upper_bounds[bay_id], f"demand_{bay_id}"
        )
        for bay_id in request.bay_ids
    }
    overloads = {
        bay_id: model.new_int_var(
            0,
            max(0, demand_upper_bounds[bay_id] - capacities[bay_id]),
            f"overload_{bay_id}",
        )
        for bay_id in request.bay_ids
    }
    for bay_id in request.bay_ids:
        model.add(
            demands[bay_id]
            == sum(
                pair_specs[pair].congestion_demand * assignments[pair]
                for pair in request.fit_pairs
                if pair[1] == bay_id
            )
        )
        model.add_max_equality(
            overloads[bay_id],
            (demands[bay_id] - capacities[bay_id], 0),
        )
    model.minimize(
        spec.w2 * (maximum_load - minimum_load)
        + spec.w3 * z3
        + spec.congestion_weight * sum(overloads.values())
    )
    return {
        "assignments": assignments,
        "loads": loads,
        "maximum_load": maximum_load,
        "minimum_load": minimum_load,
        "z3": z3,
        "demands": demands,
        "overloads": overloads,
    }


def _populate_model(
    cp_model: Any,
    model: Any,
    request: RetimeRequest,
    spec: CpSatModelSpec,
) -> tuple[dict[int, Any], dict[int, Any]]:
    releases = dict(request.releases)
    dues = dict(request.dues)
    dwells = dict(request.dwells)
    entry_spec = {item.block_id: item for item in spec.entries}
    tardiness_spec = {item.block_id: item for item in spec.tardiness}
    entries = {
        block_id: model.new_int_var(
            releases[block_id], spec.horizon, f"a_{block_id}"
        )
        for block_id in request.block_ids
    }
    tardiness = {
        block_id: model.new_int_var(
            0, tardiness_spec[block_id].upper_bound, f"T_{block_id}"
        )
        for block_id in request.block_ids
    }
    for block_id in request.block_ids:
        model.add(
            tardiness[block_id]
            >= entries[block_id] + dwells[block_id] - dues[block_id]
        )
        model.add_hint(entries[block_id], entry_spec[block_id].start)
        model.add_hint(tardiness[block_id], tardiness_spec[block_id].start)
    for item in spec.disjunctions:
        order = model.new_bool_var(f"before_{item.left}_{item.right}")
        model.add(
            entries[item.left] + dwells[item.left] <= entries[item.right]
        ).only_enforce_if(order)
        model.add(
            entries[item.right] + dwells[item.right] <= entries[item.left]
        ).only_enforce_if(order.negated())
        model.add_hint(order, item.start)
    model.minimize(sum(tardiness[block_id] for block_id in spec.objective_blocks))
    return entries, tardiness


def _empty(
    status: str,
    reason: str,
    *,
    bound: float | None = None,
    build_s: float = 0.0,
    solve_s: float = 0.0,
) -> ExactResult:
    return ExactResult(
        backend="cpsat",
        status=status,
        solution=None,
        objective=None,
        bound=bound,
        build_s=max(0.0, float(build_s)),
        solve_s=max(0.0, float(solve_s)),
        first_solution_s=None,
        reason=reason,
    )


def _empty_assignment(
    status: str,
    reason: str,
    *,
    bound: float | None = None,
    build_s: float = 0.0,
    solve_s: float = 0.0,
) -> AssignmentResult:
    return AssignmentResult(
        backend="cpsat",
        status=status,
        solution=None,
        objective=None,
        bound=bound,
        z2=None,
        z3=None,
        overload=None,
        build_s=max(0.0, float(build_s)),
        solve_s=max(0.0, float(solve_s)),
        first_solution_s=None,
        reason=reason,
    )


def _finite_value(value: object) -> float | None:
    try:
        result = float(value)
    except Exception:
        return None
    return result if math.isfinite(result) else None


def _scaled(value: float) -> int:
    numeric = float(value)
    if not math.isfinite(numeric):
        raise OverflowError(
            "unsafe CP-SAT assignment scaling produced a non-finite data value"
        )
    if numeric < 0.0:
        raise ValueError("scaled CP-SAT assignment values must be finite and non-negative")
    scaled = int(round(numeric * ASSIGNMENT_SCALE))
    if scaled > _CP_SAT_SAFE_INTEGER:
        raise OverflowError(
            "unsafe CP-SAT assignment scaling exceeds the signed int64 safety bound"
        )
    return scaled


def _select_assignment_objective_scale(
    raw_coefficients: tuple[float, float, float],
    term_upper_bounds: tuple[int, int, int],
) -> tuple[float, int, tuple[int, int, int], int]:
    """Select one deterministic common coefficient scale or reject it."""

    positives = tuple(value for value in raw_coefficients if value > 0.0)
    normalizer = max(positives) if positives else 1.0
    normalizer_fraction = Fraction.from_float(normalizer)
    ratios = tuple(
        Fraction(0, 1)
        if value == 0.0
        else Fraction.from_float(value) / normalizer_fraction
        for value in raw_coefficients
    )
    minimum_scale = max(
        (_minimum_positive_coefficient_scale(ratio) for ratio in ratios if ratio > 0),
        default=1,
    )
    candidate_scales: list[int] = []
    if ASSIGNMENT_SCALE >= minimum_scale:
        candidate_scales.append(ASSIGNMENT_SCALE)
    if minimum_scale not in candidate_scales:
        candidate_scales.append(minimum_scale)

    for coefficient_scale in candidate_scales:
        coefficients = tuple(
            int(round(ratio * coefficient_scale)) for ratio in ratios
        )
        if any(
            raw > 0.0 and coefficient <= 0
            for raw, coefficient in zip(
                raw_coefficients,
                coefficients,
                strict=True,
            )
        ):
            continue
        objective_upper_bound = sum(
            coefficient * upper_bound
            for coefficient, upper_bound in zip(
                coefficients,
                term_upper_bounds,
                strict=True,
            )
        )
        guarded = (*coefficients, objective_upper_bound)
        if all(
            0 <= value <= _CP_SAT_SAFE_INTEGER
            for value in guarded
        ):
            return (
                normalizer,
                coefficient_scale,
                coefficients,
                objective_upper_bound,
            )

    raise OverflowError(
        "unsafe CP-SAT assignment objective coefficients are unrepresentable "
        "within the signed int64 safety bound"
    )


def _minimum_positive_coefficient_scale(ratio: Fraction) -> int:
    """Return the smallest K for which Python round(K * ratio) is positive."""

    threshold = Fraction(1, 2) / ratio
    return threshold.numerator // threshold.denominator + 1


def _timebox(value: float) -> float:
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not math.isfinite(float(value))
        or float(value) < 0.0
    ):
        raise ValueError("timebox must be finite and non-negative")
    return float(value)
