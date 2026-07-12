"""Optional exact assignment and congestion-guide masters.

This module deliberately keeps every Gurobi import inside the default backend
factory.  The public entry point imports and calls this module only after the
checker-validated incumbent has been installed.
"""

from __future__ import annotations

import math
import statistics
import time
from dataclasses import dataclass, replace
from typing import Any, Protocol

from .budget import Budget
from .geometry import GeometryKernel
from .instance import Instance


@dataclass(frozen=True, slots=True)
class AssignmentSeed:
    bay_by_block: tuple[int, ...]
    suggested_start: tuple[int | None, ...]
    priority: tuple[int, ...]
    source: str
    surrogate_cost: float

    def __post_init__(self) -> None:
        object.__setattr__(self, "bay_by_block", tuple(self.bay_by_block))
        object.__setattr__(self, "suggested_start", tuple(self.suggested_start))
        object.__setattr__(self, "priority", tuple(self.priority))
        if len(self.bay_by_block) != len(self.suggested_start):
            raise ValueError("assignment and start vectors must have equal length")
        if set(self.priority) != set(range(len(self.bay_by_block))):
            raise ValueError("priority must be a permutation of block ids")
        if not math.isfinite(float(self.surrogate_cost)):
            raise ValueError("surrogate cost must be finite")


@dataclass(frozen=True, slots=True)
class AssignmentPortfolio:
    seeds: tuple[AssignmentSeed, ...]
    lower_bound: float | None
    status: str
    runtime: float
    gap: float | None
    diagnostics: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "seeds", tuple(self.seeds))
        object.__setattr__(self, "diagnostics", tuple(self.diagnostics))

    @classmethod
    def empty(
        cls,
        *,
        status: str,
        runtime: float = 0.0,
        diagnostics: tuple[str, ...] = (),
    ) -> "AssignmentPortfolio":
        return cls((), None, status, max(0.0, float(runtime)), None, diagnostics)


@dataclass(frozen=True, slots=True)
class AssignmentConfig:
    portfolio_size: int = 8
    max_portfolio_size: int = 32
    relative_gap: float = 0.10
    hamming_fraction: float = 0.03
    exact_time_cap: float = 1.0
    exact_remaining_fraction: float = 0.03
    guide_min_limit: float = 30.0
    guide_total_cap: float = 3.0
    candidate_time_cap: int = 32
    tardy_time_cap: int = 4
    rho_profiles: tuple[float, ...] = (0.60, 0.75, 0.90)
    slack_multipliers: tuple[float, ...] = (0.5, 1.0, 2.0)
    threads: int = 4
    seed: int = 20260710
    soft_mem_limit: float = 12.0

    def __post_init__(self) -> None:
        if not 1 <= self.portfolio_size <= self.max_portfolio_size <= 32:
            raise ValueError("portfolio size must be between 1 and 32")
        if self.relative_gap < 0 or self.hamming_fraction < 0:
            raise ValueError("gap and Hamming fraction must be non-negative")
        if self.candidate_time_cap < 1 or self.tardy_time_cap < 0:
            raise ValueError("candidate-time caps must be non-negative")


@dataclass(frozen=True, slots=True)
class AssignmentObjective:
    z2: float
    z3: float
    weighted: float


@dataclass(frozen=True, slots=True)
class AssignmentData:
    bay_count: int
    feasible_bays: tuple[tuple[int, ...], ...]
    preference_loss: tuple[tuple[float, ...], ...]
    load_coefficient: tuple[tuple[float, ...], ...]
    union_area: tuple[tuple[float | None, ...], ...]


@dataclass(frozen=True, slots=True)
class ExactAssignmentRequest:
    data: AssignmentData
    w2: float
    w3: float
    time_limit: float
    max_solutions: int
    relative_gap: float
    hamming_distance: int
    threads: int
    seed: int
    soft_mem_limit: float


@dataclass(frozen=True, slots=True)
class CongestionGuideRequest:
    data: AssignmentData
    candidate_times: tuple[tuple[int, ...], ...]
    release_times: tuple[int, ...]
    due_dates: tuple[int, ...]
    dwell: tuple[int, ...]
    bay_areas: tuple[float, ...]
    w1: float
    w2: float
    w3: float
    rho: float
    slack_multiplier: float
    slack_price: float
    time_limit: float
    threads: int
    seed: int
    soft_mem_limit: float


@dataclass(frozen=True, slots=True)
class BackendSolution:
    bay_by_block: tuple[int, ...]
    suggested_start: tuple[int | None, ...] | None = None
    objective: float | None = None
    slack: float = 0.0


@dataclass(frozen=True, slots=True)
class BackendResult:
    status: str
    solutions: tuple[BackendSolution, ...] = ()
    lower_bound: float | None = None
    gap: float | None = None
    runtime: float = 0.0
    diagnostics: tuple[str, ...] = ()


class AssignmentBackend(Protocol):
    def solve_exact(self, request: ExactAssignmentRequest) -> BackendResult: ...

    def solve_guide(self, request: CongestionGuideRequest) -> BackendResult: ...


def build_assignment_data(instance: Instance, geometry: GeometryKernel) -> AssignmentData:
    """Build exact integer-fit, preference, normalized-load, and area data."""
    bay_count = len(instance.bays)
    average_area = sum(bay.area for bay in instance.bays) / bay_count
    feasible_rows: list[tuple[int, ...]] = []
    loss_rows: list[tuple[float, ...]] = []
    load_rows: list[tuple[float, ...]] = []
    area_rows: list[tuple[float | None, ...]] = []
    for block in instance.blocks:
        orientations_by_bay: dict[int, list[int]] = {}
        for bay_id, orient_idx, _ in block.fitting_options:
            orientations_by_bay.setdefault(bay_id, []).append(orient_idx)
        feasible = tuple(sorted(orientations_by_bay))
        preference_max = max(block.bay_preferences)
        losses = tuple(preference_max - value for value in block.bay_preferences)
        loads = tuple(
            average_area / bay.area * block.workload for bay in instance.bays
        )
        areas: list[float | None] = []
        for bay_id in range(bay_count):
            orientations = orientations_by_bay.get(bay_id, ())
            areas.append(
                min(geometry.shape(block.index, orient_idx).union.area for orient_idx in orientations)
                if orientations
                else None
            )
        feasible_rows.append(feasible)
        loss_rows.append(losses)
        load_rows.append(loads)
        area_rows.append(tuple(areas))
    return AssignmentData(
        bay_count=bay_count,
        feasible_bays=tuple(feasible_rows),
        preference_loss=tuple(loss_rows),
        load_coefficient=tuple(load_rows),
        union_area=tuple(area_rows),
    )


def evaluate_assignment(
    instance: Instance,
    data: AssignmentData,
    bay_by_block: tuple[int, ...],
) -> AssignmentObjective:
    if len(bay_by_block) != len(instance.blocks):
        raise ValueError("assignment must contain every block")
    bay_loads = [0.0] * len(instance.bays)
    z3 = 0.0
    for block_id, bay_id in enumerate(bay_by_block):
        if bay_id not in data.feasible_bays[block_id]:
            raise ValueError(f"block {block_id} is assigned to a non-fitting bay")
        bay_loads[bay_id] += data.load_coefficient[block_id][bay_id]
        z3 += data.preference_loss[block_id][bay_id]
    z2 = max(bay_loads) - min(bay_loads) if len(bay_loads) > 1 else 0.0
    weighted = instance.weights.w2 * z2 + instance.weights.w3 * z3
    return AssignmentObjective(z2=z2, z3=z3, weighted=weighted)


def required_hamming(block_count: int, config: AssignmentConfig) -> int:
    if block_count <= 0:
        return 0
    return min(block_count, max(2, math.ceil(config.hamming_fraction * block_count)))


def hamming_distance(left: tuple[int, ...], right: tuple[int, ...]) -> int:
    if len(left) != len(right):
        raise ValueError("Hamming vectors must have equal length")
    return sum(a != b for a, b in zip(left, right))


def _priority(instance: Instance, starts: tuple[int | None, ...]) -> tuple[int, ...]:
    return tuple(
        sorted(
            range(len(instance.blocks)),
            key=lambda block_id: (
                starts[block_id]
                if starts[block_id] is not None
                else instance.blocks[block_id].release_time,
                instance.blocks[block_id].due_date
                - instance.blocks[block_id].release_time
                - instance.blocks[block_id].dwell,
                block_id,
            ),
        )
    )


def map_backend_status(status: str, has_incumbent: bool) -> str:
    normalized = str(status).upper()
    if normalized == "OPTIMAL":
        return "OPTIMAL" if has_incumbent else "EMPTY"
    if normalized in {"TIME_LIMIT", "TIME_LIMIT_FEASIBLE", "TIME_LIMIT_EMPTY"}:
        return "TIME_LIMIT_FEASIBLE" if has_incumbent else "TIME_LIMIT_EMPTY"
    if normalized in {"INFEASIBLE", "INF_OR_UNBD", "UNBOUNDED"}:
        return "INFEASIBLE"
    if normalized in {"IMPORT_ERROR", "UNAVAILABLE"}:
        return "UNAVAILABLE"
    if normalized in {"SKIPPED", "SKIPPED_BUDGET"}:
        return "SKIPPED_BUDGET"
    return "ERROR"


def _filtered_seeds(
    instance: Instance,
    data: AssignmentData,
    solutions: tuple[BackendSolution, ...],
    *,
    source: str,
    config: AssignmentConfig,
    include_start_cost: bool = False,
) -> tuple[AssignmentSeed, ...]:
    evaluated: list[tuple[float, tuple[int, ...], BackendSolution]] = []
    for solution in solutions:
        assignment = tuple(solution.bay_by_block)
        try:
            objective = evaluate_assignment(instance, data, assignment)
        except (TypeError, ValueError, IndexError):
            continue
        starts = solution.suggested_start
        if starts is None:
            starts = (None,) * len(instance.blocks)
        else:
            starts = tuple(starts)
        if len(starts) != len(instance.blocks):
            continue
        if any(
            start is not None
            and (isinstance(start, bool) or not isinstance(start, int) or start < instance.blocks[i].release_time)
            for i, start in enumerate(starts)
        ):
            continue
        cost = objective.weighted
        if include_start_cost:
            cost += instance.weights.w1 * sum(
                max(0, int(starts[i]) + instance.blocks[i].dwell - instance.blocks[i].due_date)
                for i in range(len(starts))
                if starts[i] is not None
            )
            cost += max(0.0, float(solution.slack))
        evaluated.append((cost, assignment, replace(solution, suggested_start=starts)))
    evaluated.sort(key=lambda item: (item[0], item[1], item[2].suggested_start or ()))
    if not evaluated:
        return ()

    best_cost = evaluated[0][0]
    if math.isclose(best_cost, 0.0, rel_tol=0.0, abs_tol=1e-12):
        threshold = 1e-9
    else:
        threshold = best_cost * (1.0 + config.relative_gap) + 1e-9
    minimum_hamming = required_hamming(len(instance.blocks), config)
    selected: list[AssignmentSeed] = []
    for cost, assignment, solution in evaluated:
        if not include_start_cost and cost > threshold:
            continue
        if any(hamming_distance(assignment, seed.bay_by_block) < minimum_hamming for seed in selected):
            continue
        starts = solution.suggested_start or (None,) * len(instance.blocks)
        selected.append(
            AssignmentSeed(
                bay_by_block=assignment,
                suggested_start=starts,
                priority=_priority(instance, starts),
                source=f"{source}:{len(selected)}",
                surrogate_cost=float(cost),
            )
        )
        if len(selected) >= config.portfolio_size:
            break
    return tuple(selected)


def build_exact_portfolio(
    instance: Instance,
    geometry: GeometryKernel,
    budget: Budget,
    config: AssignmentConfig | None = None,
    backend_factory: Any | None = None,
) -> AssignmentPortfolio:
    config = config or AssignmentConfig()
    started = time.monotonic()
    if not budget.can_start(0.0, margin=0.001):
        return AssignmentPortfolio.empty(status="SKIPPED_BUDGET")
    remaining = budget.search_remaining()
    time_limit = min(config.exact_time_cap, config.exact_remaining_fraction * remaining)
    if time_limit <= 0.0:
        return AssignmentPortfolio.empty(status="SKIPPED_BUDGET")
    data = build_assignment_data(instance, geometry)
    request = ExactAssignmentRequest(
        data=data,
        w2=instance.weights.w2,
        w3=instance.weights.w3,
        time_limit=time_limit,
        max_solutions=config.portfolio_size,
        relative_gap=config.relative_gap,
        hamming_distance=required_hamming(len(instance.blocks), config),
        threads=config.threads,
        seed=config.seed,
        soft_mem_limit=config.soft_mem_limit,
    )
    factory = backend_factory or _gurobi_backend_factory
    try:
        result = factory().solve_exact(request)
    except Exception as exc:
        runtime = time.monotonic() - started
        return AssignmentPortfolio.empty(
            status="UNAVAILABLE" if isinstance(exc, ImportError) else "ERROR",
            runtime=runtime,
            diagnostics=(f"exception={type(exc).__name__}: {exc}",),
        )
    seeds = _filtered_seeds(
        instance, data, tuple(result.solutions), source="exact", config=config
    )
    status = map_backend_status(result.status, bool(seeds))
    lower_bound = result.lower_bound if status == "OPTIMAL" else None
    return AssignmentPortfolio(
        seeds=seeds,
        lower_bound=lower_bound,
        status=status,
        runtime=max(result.runtime, time.monotonic() - started),
        gap=result.gap if seeds else None,
        diagnostics=tuple(result.diagnostics)
        + (
            f"feasible_variables={sum(len(row) for row in data.feasible_bays)}",
            f"seed_count={len(seeds)}",
            f"hamming={required_hamming(len(instance.blocks), config)}",
        ),
    )


def candidate_times(
    instance: Instance,
    block_id: int,
    config: AssignmentConfig | None = None,
) -> tuple[int, ...]:
    config = config or AssignmentConfig()
    block = instance.blocks[block_id]
    last_on_time = block.due_date - block.dwell
    on_time = list(range(block.release_time, last_on_time + 1)) if last_on_time >= block.release_time else []
    events = {
        other.release_time
        for other in instance.blocks
        if other.release_time >= block.release_time
    }
    events.update(
        other.due_date - other.dwell
        for other in instance.blocks
        if other.due_date - other.dwell >= block.release_time
    )
    events.add(block.release_time)
    tardy_start = max(block.release_time, last_on_time + 1)
    tardy = list(range(tardy_start, tardy_start + config.tardy_time_cap))
    ordered: list[int] = []
    for value in on_time + sorted(events) + tardy:
        if isinstance(value, int) and value >= block.release_time and value not in ordered:
            ordered.append(value)
    if len(ordered) <= config.candidate_time_cap:
        return tuple(sorted(ordered))
    # Preserve the complete on-time range; the cap limits only selected
    # event/tardy additions when that mandatory range is already large.
    mandatory = set(on_time)
    for value in sorted(events) + tardy:
        if len(mandatory) >= max(len(on_time), config.candidate_time_cap):
            break
        mandatory.add(value)
    return tuple(sorted(mandatory))


def build_congestion_guides(
    instance: Instance,
    geometry: GeometryKernel,
    budget: Budget,
    config: AssignmentConfig | None = None,
    backend_factory: Any | None = None,
) -> AssignmentPortfolio:
    config = config or AssignmentConfig()
    started = time.monotonic()
    if budget.limit <= config.guide_min_limit or not budget.can_start(0.0, margin=0.001):
        return AssignmentPortfolio.empty(status="SKIPPED_BUDGET")
    data = build_assignment_data(instance, geometry)
    all_times = tuple(candidate_times(instance, block.index, config) for block in instance.blocks)
    if any(not times for times in all_times):
        return AssignmentPortfolio.empty(
            status="ERROR", diagnostics=("candidate_time_generation=empty",)
        )
    positive_areas = [
        area
        for row in data.union_area
        for area in row
        if area is not None and area > 0
    ]
    median_area = statistics.median(positive_areas) if positive_areas else 1.0
    base_slack_price = instance.weights.w1 / max(1.0, median_area)
    profiles = tuple(
        (rho, multiplier)
        for rho in config.rho_profiles
        for multiplier in config.slack_multipliers
    )
    total_cap = min(config.guide_total_cap, budget.search_remaining())
    factory = backend_factory or _gurobi_backend_factory
    solutions: list[BackendSolution] = []
    diagnostics: list[str] = []
    statuses: list[str] = []
    runtime = 0.0
    for profile_index, (rho, multiplier) in enumerate(profiles):
        if not budget.can_start(0.0, margin=0.001):
            break
        remaining_profiles = len(profiles) - profile_index
        per_profile = max(0.001, (total_cap - runtime) / remaining_profiles)
        request = CongestionGuideRequest(
            data=data,
            candidate_times=all_times,
            release_times=tuple(block.release_time for block in instance.blocks),
            due_dates=tuple(block.due_date for block in instance.blocks),
            dwell=tuple(block.dwell for block in instance.blocks),
            bay_areas=tuple(bay.area for bay in instance.bays),
            w1=instance.weights.w1,
            w2=instance.weights.w2,
            w3=instance.weights.w3,
            rho=rho,
            slack_multiplier=multiplier,
            slack_price=base_slack_price * multiplier,
            time_limit=per_profile,
            threads=config.threads,
            seed=config.seed,
            soft_mem_limit=config.soft_mem_limit,
        )
        try:
            result = factory().solve_guide(request)
        except Exception as exc:
            statuses.append("UNAVAILABLE" if isinstance(exc, ImportError) else "ERROR")
            diagnostics.append(f"profile={rho}/{multiplier} exception={type(exc).__name__}: {exc}")
            continue
        runtime += max(0.0, result.runtime)
        statuses.append(map_backend_status(result.status, bool(result.solutions)))
        diagnostics.extend(f"profile={rho}/{multiplier} {item}" for item in result.diagnostics)
        solutions.extend(result.solutions)
        if runtime >= total_cap:
            break
    seeds = _filtered_seeds(
        instance,
        data,
        tuple(solutions),
        source="congestion",
        config=config,
        include_start_cost=True,
    )
    if seeds:
        status = "OPTIMAL" if "OPTIMAL" in statuses else "TIME_LIMIT_FEASIBLE"
    elif statuses and all(status == "UNAVAILABLE" for status in statuses):
        status = "UNAVAILABLE"
    elif statuses and all(status == "SKIPPED_BUDGET" for status in statuses):
        status = "SKIPPED_BUDGET"
    else:
        status = "ERROR"
    return AssignmentPortfolio(
        seeds=seeds,
        lower_bound=None,
        status=status,
        runtime=max(runtime, time.monotonic() - started),
        gap=None,
        diagnostics=tuple(diagnostics)
        + (f"profiles={len(statuses)}", f"seed_count={len(seeds)}"),
    )


def _merge_portfolios(
    exact: AssignmentPortfolio,
    guides: AssignmentPortfolio,
    config: AssignmentConfig,
) -> AssignmentPortfolio:
    ordered = sorted(
        exact.seeds + guides.seeds,
        key=lambda seed: (seed.surrogate_cost, seed.bay_by_block, seed.suggested_start),
    )
    selected: list[AssignmentSeed] = []
    minimum = required_hamming(len(ordered[0].bay_by_block), config) if ordered else 0
    for seed in ordered:
        if any(hamming_distance(seed.bay_by_block, other.bay_by_block) < minimum for other in selected):
            continue
        selected.append(seed)
        if len(selected) >= config.portfolio_size:
            break
    status = exact.status if exact.seeds or not guides.seeds else guides.status
    return AssignmentPortfolio(
        seeds=tuple(selected),
        lower_bound=exact.lower_bound,
        status=status,
        runtime=exact.runtime + guides.runtime,
        gap=exact.gap,
        diagnostics=exact.diagnostics + guides.diagnostics,
    )


def try_assignment_portfolio(
    instance: Instance,
    geometry: GeometryKernel,
    budget: Budget,
    config: AssignmentConfig | None = None,
    backend_factory: Any | None = None,
) -> AssignmentPortfolio:
    """Never-raising orchestration for exact assignment and optional guides."""
    config = config or AssignmentConfig()
    started = time.monotonic()
    try:
        exact = build_exact_portfolio(instance, geometry, budget, config, backend_factory)
        if budget.limit <= config.guide_min_limit:
            return exact
        guides = build_congestion_guides(instance, geometry, budget, config, backend_factory)
        return _merge_portfolios(exact, guides, config)
    except Exception as exc:
        return AssignmentPortfolio.empty(
            status="ERROR",
            runtime=time.monotonic() - started,
            diagnostics=(f"exception={type(exc).__name__}: {exc}",),
        )


class _GurobiBackend:
    def __init__(self, gp: Any) -> None:
        self.gp = gp

    @staticmethod
    def _status_name(gp: Any, status: int) -> str:
        names = {
            gp.GRB.OPTIMAL: "OPTIMAL",
            gp.GRB.TIME_LIMIT: "TIME_LIMIT",
            gp.GRB.INFEASIBLE: "INFEASIBLE",
            gp.GRB.INF_OR_UNBD: "INF_OR_UNBD",
            gp.GRB.UNBOUNDED: "UNBOUNDED",
        }
        return names.get(status, "ERROR")

    @staticmethod
    def _configure(model: Any, request: Any) -> None:
        model.Params.OutputFlag = 0
        model.Params.Threads = request.threads
        model.Params.MIPFocus = 1
        model.Params.SoftMemLimit = request.soft_mem_limit
        model.Params.Seed = request.seed
        model.Params.TimeLimit = max(0.001, request.time_limit)

    def solve_exact(self, request: ExactAssignmentRequest) -> BackendResult:
        gp = self.gp
        started = time.monotonic()
        model = gp.Model("assignment_exact")
        self._configure(model, request)
        x = {
            (i, j): model.addVar(vtype=gp.GRB.BINARY, name=f"x_{i}_{j}")
            for i, bays in enumerate(request.data.feasible_bays)
            for j in bays
        }
        qmax = model.addVar(lb=-gp.GRB.INFINITY, name="qmax")
        qmin = model.addVar(lb=-gp.GRB.INFINITY, name="qmin")
        for i, bays in enumerate(request.data.feasible_bays):
            model.addConstr(gp.quicksum(x[i, j] for j in bays) == 1, name=f"assign_{i}")
        bay_count = request.data.bay_count
        for j in range(bay_count):
            load = gp.quicksum(
                request.data.load_coefficient[i][j] * x[i, j]
                for i, bays in enumerate(request.data.feasible_bays)
                if j in bays
            )
            model.addConstr(qmax >= load, name=f"qmax_{j}")
            model.addConstr(qmin <= load, name=f"qmin_{j}")
        preference = gp.quicksum(
            request.data.preference_loss[i][j] * var for (i, j), var in x.items()
        )
        objective = request.w2 * (qmax - qmin) + request.w3 * preference
        model.setObjective(objective, gp.GRB.MINIMIZE)
        model.optimize()
        first_status = self._status_name(gp, model.Status)
        first_sol_count = int(model.SolCount)
        if not first_sol_count:
            return BackendResult(
                status=first_status,
                runtime=time.monotonic() - started,
                diagnostics=(
                    f"variables={model.NumVars}",
                    f"constraints={model.NumConstrs}",
                    f"SolCount={first_sol_count}",
                ),
            )
        best_objective = float(model.ObjVal)
        first_bound = float(model.ObjBound)
        first_gap = float(model.MIPGap)
        solutions: list[BackendSolution] = []
        while len(solutions) < request.max_solutions and model.SolCount:
            assignment = tuple(
                max(bays, key=lambda j: x[i, j].X)
                for i, bays in enumerate(request.data.feasible_bays)
            )
            if assignment not in [solution.bay_by_block for solution in solutions]:
                solutions.append(BackendSolution(assignment, objective=float(model.ObjVal)))
            if len(solutions) >= request.max_solutions:
                break
            if not assignment or request.hamming_distance <= 0:
                break
            chosen = gp.quicksum(x[i, assignment[i]] for i in range(len(assignment)))
            model.addConstr(
                chosen <= len(assignment) - request.hamming_distance,
                name=f"diversity_{len(solutions)}",
            )
            if len(solutions) == 1:
                ceiling = 1e-9 if abs(best_objective) <= 1e-12 else best_objective * (1.0 + request.relative_gap) + 1e-9
                model.addConstr(objective <= ceiling, name="portfolio_gap")
            remaining = request.time_limit - (time.monotonic() - started)
            if remaining <= 0.001:
                break
            model.Params.TimeLimit = remaining
            model.optimize()
            if model.Status not in (gp.GRB.OPTIMAL, gp.GRB.TIME_LIMIT) or not model.SolCount:
                break
        return BackendResult(
            status=first_status,
            solutions=tuple(solutions),
            lower_bound=first_bound if first_status == "OPTIMAL" else None,
            gap=first_gap,
            runtime=time.monotonic() - started,
            diagnostics=(
                f"variables={model.NumVars}",
                f"constraints={model.NumConstrs}",
                f"SolCount={first_sol_count}",
                f"ObjVal={best_objective}",
                f"ObjBound={first_bound}",
                f"MIPGap={first_gap}",
            ),
        )

    def solve_guide(self, request: CongestionGuideRequest) -> BackendResult:
        gp = self.gp
        started = time.monotonic()
        model = gp.Model("assignment_congestion_guide")
        self._configure(model, request)
        y = {
            (i, j, t): model.addVar(vtype=gp.GRB.BINARY, name=f"y_{i}_{j}_{t}")
            for i, bays in enumerate(request.data.feasible_bays)
            for j in bays
            for t in request.candidate_times[i]
        }
        qmax = model.addVar(lb=-gp.GRB.INFINITY, name="qmax")
        qmin = model.addVar(lb=-gp.GRB.INFINITY, name="qmin")
        for i, bays in enumerate(request.data.feasible_bays):
            model.addConstr(
                gp.quicksum(y[i, j, t] for j in bays for t in request.candidate_times[i]) == 1,
                name=f"choose_{i}",
            )
        bay_count = len(request.bay_areas)
        for j in range(bay_count):
            load = gp.quicksum(
                request.data.load_coefficient[i][j] * y[i, j, t]
                for i, bays in enumerate(request.data.feasible_bays)
                if j in bays
                for t in request.candidate_times[i]
            )
            model.addConstr(qmax >= load, name=f"qmax_{j}")
            model.addConstr(qmin <= load, name=f"qmin_{j}")
        slack: dict[tuple[int, int], Any] = {}
        all_dates = sorted(
            {
                tau
                for i, times in enumerate(request.candidate_times)
                for t in times
                for tau in range(t, t + request.dwell[i])
            }
        )
        for j in range(bay_count):
            for tau in all_dates:
                active = [
                    request.data.union_area[i][j] * y[i, j, t]
                    for i, bays in enumerate(request.data.feasible_bays)
                    if j in bays and request.data.union_area[i][j] is not None
                    for t in request.candidate_times[i]
                    if t <= tau < t + request.dwell[i]
                ]
                if not active:
                    continue
                slack[j, tau] = model.addVar(lb=0.0, name=f"slack_{j}_{tau}")
                model.addConstr(
                    gp.quicksum(active) <= request.rho * request.bay_areas[j] + slack[j, tau],
                    name=f"capacity_{j}_{tau}",
                )
        preference = gp.quicksum(
            request.data.preference_loss[i][j] * var for (i, j, _), var in y.items()
        )
        tardiness = gp.quicksum(
            max(0, t + request.dwell[i] - request.due_dates[i]) * var
            for (i, _, t), var in y.items()
        )
        objective = (
            request.w1 * tardiness
            + request.w2 * (qmax - qmin)
            + request.w3 * preference
            + request.slack_price * gp.quicksum(slack.values())
        )
        model.setObjective(objective, gp.GRB.MINIMIZE)
        model.optimize()
        status = self._status_name(gp, model.Status)
        if not model.SolCount:
            return BackendResult(
                status=status,
                runtime=time.monotonic() - started,
                diagnostics=(f"variables={model.NumVars}", f"constraints={model.NumConstrs}", "SolCount=0"),
            )
        assignment: list[int] = []
        starts: list[int] = []
        for i, bays in enumerate(request.data.feasible_bays):
            chosen = max(
                ((j, t) for j in bays for t in request.candidate_times[i]),
                key=lambda item: y[i, item[0], item[1]].X,
            )
            assignment.append(chosen[0])
            starts.append(chosen[1])
        slack_total = sum(variable.X for variable in slack.values()) * request.slack_price
        solution = BackendSolution(
            tuple(assignment), tuple(starts), float(model.ObjVal), float(slack_total)
        )
        return BackendResult(
            status=status,
            solutions=(solution,),
            lower_bound=float(model.ObjBound) if status == "OPTIMAL" else None,
            gap=float(model.MIPGap),
            runtime=time.monotonic() - started,
            diagnostics=(
                f"variables={model.NumVars}",
                f"constraints={model.NumConstrs}",
                f"SolCount={model.SolCount}",
                f"ObjVal={model.ObjVal}",
                f"ObjBound={model.ObjBound}",
                f"MIPGap={model.MIPGap}",
                f"positive_slack={sum(variable.X for variable in slack.values())}",
            ),
        )


def _gurobi_backend_factory() -> AssignmentBackend:
    import gurobipy as gp

    return _GurobiBackend(gp)
