"""Lazy, bounded Gurobi indicator-MIP adapter for fixed-layout retiming."""

from __future__ import annotations

from dataclasses import dataclass
import importlib
import math
import time
from typing import Any

from .exact import ExactResult, RetimeRequest


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
        model.Params.TimeLimit = remaining

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
            objective = float(model.ObjVal)
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
