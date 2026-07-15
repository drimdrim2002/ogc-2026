"""Lazy, bounded Gurobi indicator-MIP adapter for fixed-layout retiming."""

from __future__ import annotations

from dataclasses import dataclass
import importlib
import math
import time
from typing import Any

from .exact import AssignmentRequest, AssignmentResult, ExactResult, RetimeRequest


@dataclass(frozen=True, slots=True)
class GurobiVariableSpec:
    block_id: int
    lower_bound: int
    upper_bound: int
    start: int
    variable_type: str


@dataclass(frozen=True, slots=True)
class GurobiDisjunctionSpec:
    left: int
    right: int
    start: int
    variable_type: str = "binary"
    left_value: int = 1
    right_value: int = 0

    @property
    def first_before(self) -> int:
        return self.left

    @property
    def second_after(self) -> int:
        return self.right

    @property
    def second_before(self) -> int:
        return self.right

    @property
    def first_after(self) -> int:
        return self.left


@dataclass(frozen=True, slots=True)
class GurobiModelSpec:
    horizon: int
    entries: tuple[GurobiVariableSpec, ...]
    tardiness: tuple[GurobiVariableSpec, ...]
    disjunctions: tuple[GurobiDisjunctionSpec, ...]
    objective_blocks: tuple[int, ...]
    output_flag: int
    threads: int
    seed: int
    time_limit: float
    mip_focus: int
    mip_gap: float


@dataclass(frozen=True, slots=True)
class GurobiAssignmentVariableSpec:
    block_id: int
    bay_id: int
    workload: float
    preference_penalty: float
    congestion_demand: float
    start: int
    variable_type: str = "binary"


@dataclass(frozen=True, slots=True)
class GurobiAssignmentBaySpec:
    bay_id: int
    load_factor: float
    congestion_capacity: float
    workload_upper_bound: float
    weighted_load_upper_bound: float
    congestion_demand_upper_bound: float
    overload_upper_bound: float


@dataclass(frozen=True, slots=True)
class GurobiContinuousVariableSpec:
    name: str
    lower_bound: float
    upper_bound: float
    start: float
    variable_type: str = "continuous"


@dataclass(frozen=True, slots=True)
class GurobiAssignmentModelSpec:
    binary_variables: tuple[GurobiAssignmentVariableSpec, ...]
    bays: tuple[GurobiAssignmentBaySpec, ...]
    wmax_variable: GurobiContinuousVariableSpec
    wmin_variable: GurobiContinuousVariableSpec
    range_variable: GurobiContinuousVariableSpec
    w2: float
    w3: float
    congestion_weight: float
    output_flag: int
    threads: int
    seed: int
    time_limit: float
    mip_focus: int
    mip_gap: float


def build_gurobi_assignment_spec(
    request: AssignmentRequest,
    *,
    timebox: float,
) -> GurobiAssignmentModelSpec:
    """Build the pure exact-float assignment model description."""

    limit = _timebox(timebox)
    workloads = dict(request.workloads)
    penalties = {
        (block_id, bay_id): value
        for block_id, bay_id, value in request.preference_penalties
    }
    demands = {
        (block_id, bay_id): value
        for block_id, bay_id, value in request.congestion_demands
    }
    factors = dict(request.load_factors)
    capacities = dict(request.congestion_capacities)
    current = dict(request.current_assignment)
    binary_variables = tuple(
        GurobiAssignmentVariableSpec(
            block_id=block_id,
            bay_id=bay_id,
            workload=float(workloads[block_id]),
            preference_penalty=float(penalties[(block_id, bay_id)]),
            congestion_demand=float(demands[(block_id, bay_id)]),
            start=int(current[block_id] == bay_id),
        )
        for block_id, bay_id in request.fit_pairs
    )
    current_workloads = {bay_id: 0.0 for bay_id in request.bay_ids}
    for block_id, bay_id in request.current_assignment:
        current_workloads[bay_id] += workloads[block_id]
    current_weighted = tuple(
        factors[bay_id] * current_workloads[bay_id]
        for bay_id in request.bay_ids
    )
    bay_specs: list[GurobiAssignmentBaySpec] = []
    for bay_id in request.bay_ids:
        eligible = tuple(
            item for item in binary_variables if item.bay_id == bay_id
        )
        workload_upper = sum(item.workload for item in eligible)
        demand_upper = sum(item.congestion_demand for item in eligible)
        bay_specs.append(
            GurobiAssignmentBaySpec(
                bay_id=bay_id,
                load_factor=float(factors[bay_id]),
                congestion_capacity=float(capacities[bay_id]),
                workload_upper_bound=float(workload_upper),
                weighted_load_upper_bound=float(
                    factors[bay_id] * workload_upper
                ),
                congestion_demand_upper_bound=float(demand_upper),
                overload_upper_bound=float(
                    max(0.0, demand_upper - capacities[bay_id])
                ),
            )
        )
    max_weighted = max(
        (item.weighted_load_upper_bound for item in bay_specs),
        default=0.0,
    )
    min_weighted_upper = min(
        (item.weighted_load_upper_bound for item in bay_specs),
        default=0.0,
    )
    current_max = max(current_weighted, default=0.0)
    current_min = min(current_weighted, default=0.0)
    w2, w3 = request.weights
    return GurobiAssignmentModelSpec(
        binary_variables=binary_variables,
        bays=tuple(bay_specs),
        wmax_variable=GurobiContinuousVariableSpec(
            "Wmax",
            0.0,
            float(max_weighted),
            float(current_max),
        ),
        wmin_variable=GurobiContinuousVariableSpec(
            "Wmin",
            0.0,
            float(min_weighted_upper),
            float(current_min),
        ),
        range_variable=GurobiContinuousVariableSpec(
            "load_range",
            0.0,
            float(max_weighted),
            float(current_max - current_min),
        ),
        w2=float(w2),
        w3=float(w3),
        congestion_weight=float(request.congestion_weight),
        output_flag=0,
        threads=min(request.threads, 4),
        seed=request.seed,
        time_limit=limit,
        mip_focus=1,
        mip_gap=0.0,
    )


def build_gurobi_model_spec(
    request: RetimeRequest,
    *,
    timebox: float,
) -> GurobiModelSpec:
    """Build the complete solver-independent indicator model description.

    This pure step is intentionally executable when Gurobi or its license is
    unavailable, keeping the semantic model assertions mandatory.
    """

    limit = _timebox(timebox)
    releases = dict(request.releases)
    dues = dict(request.dues)
    dwells = dict(request.dwells)
    current = dict(request.current_entries)
    horizon = max(releases.values(), default=0) + sum(dwells.values())
    entries = tuple(
        GurobiVariableSpec(
            block_id=block_id,
            lower_bound=releases[block_id],
            upper_bound=horizon,
            start=current[block_id],
            variable_type="integer",
        )
        for block_id in request.block_ids
    )
    tardiness = tuple(
        GurobiVariableSpec(
            block_id=block_id,
            lower_bound=0,
            upper_bound=horizon + dwells[block_id],
            start=max(
                0,
                current[block_id] + dwells[block_id] - dues[block_id],
            ),
            variable_type="integer",
        )
        for block_id in request.block_ids
    )
    disjunctions = tuple(
        GurobiDisjunctionSpec(
            left=left,
            right=right,
            start=(
                1
                if current[left] + dwells[left] <= current[right]
                else 0
            ),
        )
        for left, right in request.conflict_pairs
    )
    return GurobiModelSpec(
        horizon=horizon,
        entries=entries,
        tardiness=tardiness,
        disjunctions=disjunctions,
        objective_blocks=request.block_ids,
        output_flag=0,
        threads=min(request.threads, 4),
        seed=request.seed,
        time_limit=limit,
        mip_focus=1,
        mip_gap=0.0,
    )


def assign_gurobi(
    request: AssignmentRequest,
    timebox: float,
) -> AssignmentResult:
    """Solve one immutable exact-float assignment request."""

    limit = _timebox(timebox)
    total_started = time.monotonic()
    spec = build_gurobi_assignment_spec(request, timebox=limit)
    if limit <= 0.0:
        return _empty_assignment("time_limit", "no Gurobi assignment time remains")

    try:
        gp = importlib.import_module("gurobipy")
    except Exception as exc:
        return _empty_assignment(
            "unavailable",
            f"Gurobi unavailable at import: {type(exc).__name__}: {exc}",
            build_s=time.monotonic() - total_started,
        )

    env: Any | None = None
    model: Any | None = None
    try:
        try:
            env = gp.Env(empty=True)
            env.setParam("OutputFlag", spec.output_flag)
            env.start()
            model = gp.Model("assignment_v2", env=env)
            variables = _populate_assignment_model(gp, model, request, spec)
        except Exception as exc:
            return _empty_assignment(
                "unavailable",
                f"Gurobi unavailable at environment/license/model: "
                f"{type(exc).__name__}: {exc}",
                build_s=time.monotonic() - total_started,
            )

        build_s = time.monotonic() - total_started
        remaining = max(0.0, limit - build_s)
        if remaining <= 0.0:
            return _empty_assignment(
                "time_limit",
                "Gurobi model construction consumed the assignment timebox",
                build_s=build_s,
            )
        model.Params.TimeLimit = max(0.001, remaining - min(0.25, 0.25 * remaining))

        first_solution: list[float | None] = [None]

        def on_progress(active_model: Any, where: int) -> None:
            if first_solution[0] is not None or where != gp.GRB.Callback.MIPSOL:
                return
            try:
                first_solution[0] = float(
                    active_model.cbGet(gp.GRB.Callback.RUNTIME)
                )
            except Exception:
                first_solution[0] = 0.0

        solve_started = time.monotonic()
        try:
            model.optimize(on_progress)
        except Exception as exc:
            return _empty_assignment(
                "error",
                f"Gurobi optimize error: {type(exc).__name__}: {exc}",
                build_s=build_s,
                solve_s=time.monotonic() - solve_started,
            )
        solve_s = time.monotonic() - solve_started
        if int(model.SolCount) <= 0:
            status = (
                "time_limit"
                if int(model.Status)
                in {int(gp.GRB.TIME_LIMIT), int(gp.GRB.INTERRUPTED)}
                else "no_solution"
            )
            return _empty_assignment(
                status,
                f"Gurobi finished without an assignment (status={int(model.Status)})",
                bound=_finite_attr(model, "ObjBound"),
                build_s=build_s,
                solve_s=solve_s,
            )

        try:
            solution = tuple(
                (
                    block_id,
                    max(
                        (
                            bay_id
                            for candidate_block, bay_id in request.fit_pairs
                            if candidate_block == block_id
                        ),
                        key=lambda bay_id: float(variables[(block_id, bay_id)].X),
                    ),
                )
                for block_id in request.block_ids
            )
            objective = float(model.ObjVal)
            bound = _finite_attr(model, "ObjBound")
        except Exception as exc:
            return _empty_assignment(
                "error",
                f"Gurobi extraction error: {type(exc).__name__}: {exc}",
                build_s=build_s,
                solve_s=solve_s,
            )
        status = "optimal" if int(model.Status) == int(gp.GRB.OPTIMAL) else "feasible"
        first = first_solution[0]
        if first is None:
            first = solve_s
        first = min(max(0.0, float(first)), solve_s)
        return AssignmentResult(
            backend="gurobi",
            status=status,
            solution=solution,
            objective=objective,
            bound=bound,
            z2=None,
            z3=None,
            overload=None,
            build_s=build_s,
            solve_s=solve_s,
            first_solution_s=first,
            reason=None,
        )
    finally:
        if model is not None:
            try:
                model.dispose()
            except Exception:
                pass
        if env is not None:
            try:
                env.dispose()
            except Exception:
                pass


def retime_gurobi(request: RetimeRequest, timebox: float) -> ExactResult:
    """Solve one immutable retiming request without exposing solver objects."""

    limit = _timebox(timebox)
    total_started = time.monotonic()
    spec = build_gurobi_model_spec(request, timebox=limit)
    if limit <= 0.0:
        return _empty("time_limit", "no Gurobi retiming time remains")

    try:
        gp = importlib.import_module("gurobipy")
    except Exception as exc:
        return _empty(
            "unavailable",
            f"Gurobi unavailable at import: {type(exc).__name__}: {exc}",
            build_s=time.monotonic() - total_started,
        )

    env: Any | None = None
    model: Any | None = None
    try:
        try:
            env = gp.Env(empty=True)
            env.setParam("OutputFlag", spec.output_flag)
            env.start()
            model = gp.Model("retime", env=env)
            entries, tardiness = _populate_model(gp, model, request, spec)
        except Exception as exc:
            return _empty(
                "unavailable",
                f"Gurobi unavailable at environment/license/model: "
                f"{type(exc).__name__}: {exc}",
                build_s=time.monotonic() - total_started,
            )

        build_s = time.monotonic() - total_started
        remaining = max(0.0, limit - build_s)
        if remaining <= 0.0:
            return _empty(
                "time_limit",
                "Gurobi model construction consumed the exact timebox",
                build_s=build_s,
            )
        # Reserve adapter/extraction overhead inside the immutable call
        # allowance so the common strict timebox normalizer can retain a
        # time-limited incumbent instead of discarding it as overtime.
        model.Params.TimeLimit = max(0.001, remaining - min(0.25, 0.25 * remaining))

        first_solution: list[float | None] = [None]

        def on_progress(active_model: Any, where: int) -> None:
            if first_solution[0] is not None or where != gp.GRB.Callback.MIPSOL:
                return
            try:
                first_solution[0] = float(
                    active_model.cbGet(gp.GRB.Callback.RUNTIME)
                )
            except Exception:
                first_solution[0] = 0.0

        solve_started = time.monotonic()
        try:
            model.optimize(on_progress)
        except Exception as exc:
            return _empty(
                "error",
                f"Gurobi optimize error: {type(exc).__name__}: {exc}",
                build_s=build_s,
                solve_s=time.monotonic() - solve_started,
            )
        solve_s = time.monotonic() - solve_started
        if int(model.SolCount) <= 0:
            status = (
                "time_limit"
                if int(model.Status) in {
                    int(gp.GRB.TIME_LIMIT),
                    int(gp.GRB.INTERRUPTED),
                }
                else "no_solution"
            )
            return _empty(
                status,
                f"Gurobi finished without a solution (status={int(model.Status)})",
                bound=_finite_attr(model, "ObjBound"),
                build_s=build_s,
                solve_s=solve_s,
            )

        try:
            solution = tuple(
                (
                    block_id,
                    int(round(float(entries[block_id].X))),
                    int(round(float(entries[block_id].X)))
                    + dict(request.dwells)[block_id],
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
            bound = _finite_attr(model, "ObjBound")
        except Exception as exc:
            return _empty(
                "error",
                f"Gurobi extraction error: {type(exc).__name__}: {exc}",
                build_s=build_s,
                solve_s=solve_s,
            )
        status = "optimal" if int(model.Status) == int(gp.GRB.OPTIMAL) else "feasible"
        first = first_solution[0]
        if first is None:
            first = solve_s
        first = min(max(0.0, float(first)), solve_s)
        return ExactResult(
            backend="gurobi",
            status=status,
            solution=solution,
            objective=objective,
            bound=bound,
            build_s=build_s,
            solve_s=solve_s,
            first_solution_s=first,
            reason=None,
        )
    finally:
        if model is not None:
            try:
                model.dispose()
            except Exception:
                pass
        if env is not None:
            try:
                env.dispose()
            except Exception:
                pass


def _populate_assignment_model(
    gp: Any,
    model: Any,
    request: AssignmentRequest,
    spec: GurobiAssignmentModelSpec,
) -> dict[tuple[int, int], Any]:
    variable_specs = {
        (item.block_id, item.bay_id): item for item in spec.binary_variables
    }
    variables = {
        pair: model.addVar(vtype=gp.GRB.BINARY, name=f"x_{pair[0]}_{pair[1]}")
        for pair in request.fit_pairs
    }
    for block_id in request.block_ids:
        model.addConstr(
            gp.quicksum(
                variables[(candidate_block, bay_id)]
                for candidate_block, bay_id in request.fit_pairs
                if candidate_block == block_id
            )
            == 1,
            name=f"assign_{block_id}",
        )
    for pair, variable in variables.items():
        variable.Start = variable_specs[pair].start

    wmax = model.addVar(
        vtype=gp.GRB.CONTINUOUS,
        lb=spec.wmax_variable.lower_bound,
        ub=spec.wmax_variable.upper_bound,
        name=spec.wmax_variable.name,
    )
    wmin = model.addVar(
        vtype=gp.GRB.CONTINUOUS,
        lb=spec.wmin_variable.lower_bound,
        ub=spec.wmin_variable.upper_bound,
        name=spec.wmin_variable.name,
    )
    load_range = model.addVar(
        vtype=gp.GRB.CONTINUOUS,
        lb=spec.range_variable.lower_bound,
        ub=spec.range_variable.upper_bound,
        name=spec.range_variable.name,
    )
    wmax.Start = spec.wmax_variable.start
    wmin.Start = spec.wmin_variable.start
    load_range.Start = spec.range_variable.start

    overload: dict[int, Any] = {}
    for bay in spec.bays:
        eligible = tuple(
            item for item in spec.binary_variables if item.bay_id == bay.bay_id
        )
        weighted_load = bay.load_factor * gp.quicksum(
            item.workload * variables[(item.block_id, item.bay_id)]
            for item in eligible
        )
        congestion = gp.quicksum(
            item.congestion_demand * variables[(item.block_id, item.bay_id)]
            for item in eligible
        )
        wmax_constr = model.addConstr(
            wmax >= weighted_load,
            name=f"wmax_{bay.bay_id}",
        )
        wmin_constr = model.addConstr(
            wmin <= weighted_load,
            name=f"wmin_{bay.bay_id}",
        )
        del wmax_constr, wmin_constr
        overload[bay.bay_id] = model.addVar(
            vtype=gp.GRB.CONTINUOUS,
            lb=0.0,
            ub=bay.overload_upper_bound,
            name=f"overload_{bay.bay_id}",
        )
        current_demand = sum(
            item.congestion_demand * item.start for item in eligible
        )
        overload[bay.bay_id].Start = max(
            0.0,
            current_demand - bay.congestion_capacity,
        )
        model.addConstr(
            congestion <= bay.congestion_capacity + overload[bay.bay_id],
            name=f"congestion_{bay.bay_id}",
        )
    model.addConstr(load_range == wmax - wmin, name="load_range_definition")
    preference = gp.quicksum(
        item.preference_penalty * variables[(item.block_id, item.bay_id)]
        for item in spec.binary_variables
    )
    model.setObjective(
        spec.w2 * load_range
        + spec.w3 * preference
        + spec.congestion_weight * gp.quicksum(overload.values()),
        gp.GRB.MINIMIZE,
    )
    model.Params.OutputFlag = spec.output_flag
    model.Params.Threads = spec.threads
    model.Params.Seed = spec.seed
    model.Params.MIPFocus = spec.mip_focus
    model.Params.MIPGap = spec.mip_gap
    return variables


def _populate_model(
    gp: Any,
    model: Any,
    request: RetimeRequest,
    spec: GurobiModelSpec,
) -> tuple[dict[int, Any], dict[int, Any]]:
    releases = dict(request.releases)
    dues = dict(request.dues)
    dwells = dict(request.dwells)
    entry_spec = {item.block_id: item for item in spec.entries}
    tardiness_spec = {item.block_id: item for item in spec.tardiness}
    entries = {
        block_id: model.addVar(
            vtype=gp.GRB.INTEGER,
            lb=releases[block_id],
            ub=spec.horizon,
            name=f"a_{block_id}",
        )
        for block_id in request.block_ids
    }
    tardiness = {
        block_id: model.addVar(
            vtype=gp.GRB.INTEGER,
            lb=0,
            ub=tardiness_spec[block_id].upper_bound,
            name=f"T_{block_id}",
        )
        for block_id in request.block_ids
    }
    for block_id in request.block_ids:
        model.addConstr(
            tardiness[block_id]
            >= entries[block_id] + dwells[block_id] - dues[block_id],
            name=f"tardiness_{block_id}",
        )
        entries[block_id].Start = entry_spec[block_id].start
        tardiness[block_id].Start = tardiness_spec[block_id].start
    for item in spec.disjunctions:
        order = model.addVar(
            vtype=gp.GRB.BINARY,
            name=f"before_{item.left}_{item.right}",
        )
        model.addGenConstrIndicator(
            order,
            item.left_value,
            entries[item.left] + dwells[item.left] <= entries[item.right],
            name=f"indicator_{item.left}_before_{item.right}",
        )
        model.addGenConstrIndicator(
            order,
            item.right_value,
            entries[item.right] + dwells[item.right] <= entries[item.left],
            name=f"indicator_{item.right}_before_{item.left}",
        )
        order.Start = item.start
    model.setObjective(
        gp.quicksum(tardiness[block_id] for block_id in spec.objective_blocks),
        gp.GRB.MINIMIZE,
    )
    model.Params.OutputFlag = spec.output_flag
    model.Params.Threads = spec.threads
    model.Params.Seed = spec.seed
    model.Params.MIPFocus = spec.mip_focus
    model.Params.MIPGap = spec.mip_gap
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
        backend="gurobi",
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
        backend="gurobi",
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


def _finite_attr(value: Any, name: str) -> float | None:
    try:
        result = float(getattr(value, name))
    except Exception:
        return None
    return result if math.isfinite(result) else None


def _timebox(value: float) -> float:
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not math.isfinite(float(value))
        or float(value) < 0.0
    ):
        raise ValueError("timebox must be finite and non-negative")
    return float(value)
