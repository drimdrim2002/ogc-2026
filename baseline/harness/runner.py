"""Repository provenance and checker-authoritative T0 execution records."""

from __future__ import annotations

from dataclasses import asdict
from datetime import datetime
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import time
from typing import Any, Mapping

from .checker import checker_payload
from .schema import record_identity
from .selectors import InstanceRef, REPO_ROOT

try:
    from baseline.solver.assign import AssignmentV1
    from baseline.solver.budget import Budget, BudgetExpired, deadline_reserve
    from baseline.solver.checker_adapter import official_check
    from baseline.solver.config import DEFAULT_CONFIG
    from baseline.solver.construct import (
        construct_multistart,
        construct_profile,
        escalate_insert,
    )
    from baseline.solver.exact import (
        BackendProbe,
        RetimeRequest,
        normalize_result,
        probe_backends,
    )
    from baseline.solver.gurobi_backend import (
        build_gurobi_model_spec,
        retime_gurobi,
    )
    from baseline.solver.incumbent import VerifiedIncumbent
    from baseline.solver.instance import ProblemInstance
    from baseline.solver.serialize import serialize_non_interlock
    from baseline.solver.state import Placement, SolutionState
    from baseline.solver.trivial import build_t0
except ModuleNotFoundError:
    from solver.assign import AssignmentV1
    from solver.budget import Budget, BudgetExpired, deadline_reserve
    from solver.checker_adapter import official_check
    from solver.config import DEFAULT_CONFIG
    from solver.construct import construct_multistart, construct_profile, escalate_insert
    from solver.exact import BackendProbe, RetimeRequest, normalize_result, probe_backends
    from solver.gurobi_backend import build_gurobi_model_spec, retime_gurobi
    from solver.incumbent import VerifiedIncumbent
    from solver.instance import ProblemInstance
    from solver.serialize import serialize_non_interlock
    from solver.state import Placement, SolutionState
    from solver.trivial import build_t0


def repository_provenance() -> dict[str, Any]:
    status = _git("status", "--porcelain=v1", "-z", binary=True)
    commit = _git("rev-parse", "HEAD").strip()
    branch = _git("branch", "--show-current").strip()
    diff = _git("diff", "--binary", "HEAD", binary=True)
    untracked_payload = bytearray()
    entries = status.split(b"\0")
    for entry in entries:
        if entry.startswith(b"?? "):
            path = REPO_ROOT / entry[3:].decode()
            if path.is_file():
                untracked_payload.extend(entry[3:])
                untracked_payload.extend(path.read_bytes())
    dirty_payload = diff + bytes(untracked_payload)
    return {
        "branch": branch,
        "commit": commit,
        "dirty": bool(status),
        "dirty_diff_hash": hashlib.sha256(dirty_payload).hexdigest() if status else "clean",
    }


def run_t0_case(
    ref: InstanceRef,
    *,
    selector: str,
    timelimit: float,
    seed: int,
    features: Mapping[str, str],
    fault: str = "none",
) -> dict[str, Any]:
    provenance = repository_provenance()
    identity = record_identity(
        commit=provenance["commit"],
        dirty_diff_hash=provenance["dirty_diff_hash"],
        instance_sha=ref.sha256,
        solver="native-t0",
        timelimit=timelimit,
        seed=seed,
        features={**features, "fault": fault},
    )
    started = time.monotonic()
    incumbent: VerifiedIncumbent | None = None
    fallback_reason = None
    try:
        parsed = ProblemInstance.parse(ref.prob_info)
        incumbent = VerifiedIncumbent(parsed)
        incumbent.register_initial(build_t0(parsed))
        if fault == "after_incumbent":
            raise RuntimeError("injected harness fault after incumbent")
        if fault != "none":
            raise ValueError(f"unsupported fault: {fault}")
    except Exception as exc:
        if incumbent is None or not incumbent.has_incumbent:
            raise
        fallback_reason = f"{type(exc).__name__}: {exc}"

    assert incumbent is not None and incumbent.has_incumbent
    checked = incumbent.checker_result
    wall_seconds = time.monotonic() - started
    status = "passed" if checked.feasible and checked.stage == 5 else "checker_failed"
    return {
        "record_id": _case_record_id(ref.instance_id, timelimit, seed, fault),
        "identity": identity,
        "complete": True,
        "status": status,
        "timestamp": datetime.now().astimezone().isoformat(),
        **provenance,
        "interpreter": sys.executable,
        "argv": [sys.executable, "-m", "baseline.harness.cli", *sys.argv[1:]],
        "cwd": str(Path.cwd()),
        "instance_id": ref.instance_id,
        "instance_path": str(ref.path),
        "instance_sha": ref.sha256,
        "selector": selector,
        "solver": "native-t0",
        "timelimit": timelimit,
        "seed": seed,
        "features": dict(sorted(features.items())),
        "wall_seconds": wall_seconds,
        "subprocess_exit": 0,
        "signal": None,
        "checker": checker_payload(checked),
        "stage_timing": {"t0_and_verify_seconds": wall_seconds},
        "backend": {
            "name": "t0",
            "status": "verified",
            "bound": None,
            "gap": None,
            "build_seconds": wall_seconds,
            "solve_seconds": 0.0,
            "first_solution_seconds": wall_seconds,
            "fallback": fallback_reason is not None,
        },
        "iterations": 0,
        "proposals": 0,
        "accepted_improving": 0,
        "accepted_worsening": 0,
        "rejected": 0,
        "cache": {"hits": 0, "misses": 0, "evictions": 0, "exact_predicates": 0},
        "timeout": False,
        "crash": False,
        "exception": fallback_reason,
        "incumbent_verification_count": incumbent.verification_count,
        "unverified_return_count": 0,
        "fallback_tier": "t0",
        "fallback_reason": fallback_reason,
    }


def run_assignment_case(
    ref: InstanceRef,
    *,
    selector: str,
    timelimit: float,
    seed: int,
    features: Mapping[str, str],
) -> dict[str, Any]:
    """Assign, place sequentially, and full-check one S1-01 evidence case."""
    provenance = repository_provenance()
    identity = record_identity(
        commit=provenance["commit"],
        dirty_diff_hash=provenance["dirty_diff_hash"],
        instance_sha=ref.sha256,
        solver="assign-v1-sequential-proof",
        timelimit=timelimit,
        seed=seed,
        features=features,
    )
    started = time.monotonic()
    parsed = ProblemInstance.parse(ref.prob_info)
    assignment_started = time.monotonic()
    assignment = AssignmentV1(parsed).assign()
    assignment_seconds = time.monotonic() - assignment_started

    state = SolutionState(parsed)
    available: list[int | None] = [None for _ in parsed.bays]
    for block_id in assignment.order:
        chosen = assignment.assignments[block_id]
        block = parsed.blocks[block_id]
        orientation = block.orientations[chosen.orient_idx]
        ranges = orientation.integer_position_ranges(parsed.bays[chosen.bay_id])
        if ranges is None:
            raise AssertionError("assignment selected a non-fitting orientation")
        prior = available[chosen.bay_id]
        entry = block.release_time if prior is None else max(block.release_time, prior)
        exit_time = entry + block.dwell
        state.place(
            Placement(
                block_id=block_id,
                bay_id=chosen.bay_id,
                x=ranges[0].start,
                y=ranges[1].start,
                orient_idx=chosen.orient_idx,
                entry=entry,
                exit=exit_time,
            )
        )
        available[chosen.bay_id] = exit_time
    state.assert_invariants()
    checked = official_check(
        ref.prob_info,
        serialize_non_interlock(state.placements.values()),
    )
    wall_seconds = time.monotonic() - started
    z2_error = abs(float(checked.obj2) - assignment.z2) if checked.obj2 is not None else None
    z3_error = abs(float(checked.obj3) - assignment.z3) if checked.obj3 is not None else None
    passed = (
        checked.feasible
        and checked.stage == 5
        and z2_error is not None
        and z2_error <= 1e-9
        and z3_error is not None
        and z3_error <= 1e-9
        and assignment.metrics.assigned == len(parsed.blocks)
        and assignment.metrics.fallback == 0
        and assignment.metrics.fit_failures == 0
    )
    return {
        "record_id": _case_record_id(ref.instance_id, timelimit, seed, "none"),
        "identity": identity,
        "complete": True,
        "status": "passed" if passed else "checker_failed",
        "timestamp": datetime.now().astimezone().isoformat(),
        **provenance,
        "interpreter": sys.executable,
        "argv": [sys.executable, "-m", "baseline.harness.cli", *sys.argv[1:]],
        "cwd": str(Path.cwd()),
        "instance_id": ref.instance_id,
        "instance_path": str(ref.path),
        "instance_sha": ref.sha256,
        "selector": selector,
        "solver": "assign-v1-sequential-proof",
        "timelimit": timelimit,
        "seed": seed,
        "features": dict(sorted(features.items())),
        "wall_seconds": wall_seconds,
        "subprocess_exit": 0,
        "signal": None,
        "checker": checker_payload(checked),
        "stage_timing": {
            "assignment_seconds": assignment_seconds,
            "sequential_place_and_full_check_seconds": wall_seconds - assignment_seconds,
        },
        "block_count": len(parsed.blocks),
        "assigned": assignment.metrics.assigned,
        "fallback": assignment.metrics.fallback,
        "fit_failures": assignment.metrics.fit_failures,
        "candidate_evaluations": assignment.metrics.candidate_evaluations,
        "assignment_horizon": assignment.metrics.horizon,
        "assignment_z2": assignment.z2,
        "assignment_z3": assignment.z3,
        "float_z2_error": z2_error,
        "float_z3_error": z3_error,
        "timeout": False,
        "crash": False,
        "exception": None,
        "fallback_tier": None,
        "fallback_reason": None,
    }


def run_constructor_case(
    ref: InstanceRef,
    *,
    selector: str,
    timelimit: float,
    seed: int,
    features: Mapping[str, str],
) -> dict[str, Any]:
    """Run S1-04 multi-start and record the single best full-check."""
    provenance = repository_provenance()
    identity = record_identity(
        commit=provenance["commit"],
        dirty_diff_hash=provenance["dirty_diff_hash"],
        instance_sha=ref.sha256,
        solver="constructor-multistart",
        timelimit=timelimit,
        seed=seed,
        features=features,
    )
    started = time.monotonic()
    parsed = ProblemInstance.parse(ref.prob_info)
    incumbent = VerifiedIncumbent(parsed)
    incumbent.register_initial(build_t0(parsed))

    # S1-04 always measures all four deterministic profiles.  The budget gates
    # only optional biased variants; S1-05 consumes the recorded timing to
    # calibrate caps and enforce the final five-second constructor gate.
    budget = Budget(
        max(0.0, timelimit - 0.25) + deadline_reserve(timelimit)
    )
    result = construct_multistart(parsed, incumbent, budget, seed=seed)
    construction_seconds = result.construction_seconds
    wall_seconds = time.monotonic() - started
    checked = result.checker_result
    metrics = result.metrics
    placed_count = len(result.state.placements)
    profile_metrics = [asdict(item) for item in metrics.profile_metrics]
    passed = (
        checked.feasible
        and checked.stage == 5
        and placed_count == len(parsed.blocks)
        and incumbent.verification_count == 2
    )
    return {
        "record_id": _case_record_id(ref.instance_id, timelimit, seed, "none"),
        "identity": identity,
        "complete": True,
        "status": "passed" if passed else "checker_failed",
        "timestamp": datetime.now().astimezone().isoformat(),
        **provenance,
        "interpreter": sys.executable,
        "argv": [sys.executable, "-m", "baseline.harness.cli", *sys.argv[1:]],
        "cwd": str(Path.cwd()),
        "instance_id": ref.instance_id,
        "instance_path": str(ref.path),
        "instance_sha": ref.sha256,
        "selector": selector,
        "solver": "constructor-multistart",
        "timelimit": timelimit,
        "seed": seed,
        "features": dict(sorted(features.items())),
        "wall_seconds": wall_seconds,
        "construction_seconds": construction_seconds,
        "within_timelimit": construction_seconds <= timelimit,
        "subprocess_exit": 0,
        "signal": None,
        "checker": checker_payload(checked),
        "block_count": len(parsed.blocks),
        "placed_count": placed_count,
        "profile_count": len(profile_metrics),
        "deterministic_profile_count": sum(
            not bool(item["biased"]) for item in profile_metrics
        ),
        "biased_profile_count": sum(
            bool(item["biased"]) for item in profile_metrics
        ),
        "start_profiles": list(metrics.start_profiles),
        "selected_profile": metrics.selected_profile,
        "selected_order": list(metrics.selected_order),
        "profile_metrics": profile_metrics,
        "deterministic_output_sha256": metrics.output_sha256,
        "incumbent_updated": metrics.incumbent_updated,
        "incumbent_verification_count": incumbent.verification_count,
        "cache": {
            "hits": result.state.geom.stats.cache_hits,
            "misses": result.state.geom.stats.cache_misses,
            "evictions": result.state.geom.stats.cache_evictions,
            "exact_predicates": result.state.geom.stats.exact_predicates,
        },
        "timeout": False,
        "crash": False,
        "exception": None,
        "fallback_tier": None,
        "fallback_reason": None,
    }


def run_entry_case(
    ref: InstanceRef,
    *,
    selector: str,
    timelimit: float,
    seed: int,
    features: Mapping[str, str],
    constructor: bool,
    run_label: str = "none",
) -> dict[str, Any]:
    """Run the integrated entry policy and retain T0 on constructor failure."""
    provenance = repository_provenance()
    identity = record_identity(
        commit=provenance["commit"],
        dirty_diff_hash=provenance["dirty_diff_hash"],
        instance_sha=ref.sha256,
        solver="native-constructor-entry" if constructor else "native-t0",
        timelimit=timelimit,
        seed=seed,
        features={**features, "constructor": str(constructor).lower(), "run": run_label},
    )
    started = time.monotonic()
    parsed = ProblemInstance.parse(ref.prob_info)
    incumbent = VerifiedIncumbent(parsed)
    incumbent.register_initial(build_t0(parsed))
    t0_objective = incumbent.checker_result.objective
    result = None
    fallback_reason = None
    construction_seconds = 0.0
    if constructor:
        construction_started = time.monotonic()
        try:
            hard_remaining = max(0.0, timelimit - (construction_started - started))
            reserve = min(
                DEFAULT_CONFIG.constructor_return_reserve_seconds,
                max(hard_remaining - 0.05, 0.0),
            )
            result = construct_multistart(
                parsed,
                incumbent,
                Budget(hard_remaining, reserve=reserve),
                seed=seed,
                profiles=DEFAULT_CONFIG.constructor_profiles,
                biased_variants=False,
                calibrated_entry=True,
                time_cap=DEFAULT_CONFIG.constructor_time_cap,
                anchor_cap=DEFAULT_CONFIG.constructor_anchor_cap,
            )
            construction_seconds = result.construction_seconds
            if not result.metrics.incumbent_updated:
                fallback_reason = "candidate_rejected"
        except BudgetExpired:
            construction_seconds = time.monotonic() - construction_started
            fallback_reason = "budget_expired"
        except Exception as exc:
            construction_seconds = time.monotonic() - construction_started
            fallback_reason = f"{type(exc).__name__}: {exc}"

    checked = incumbent.checker_result
    wall_seconds = time.monotonic() - started
    final_objective = checked.objective
    relative_improvement = 0.0
    if t0_objective is not None and final_objective is not None and t0_objective != 0.0:
        relative_improvement = (t0_objective - final_objective) / abs(t0_objective)
    solution_sha = hashlib.sha256(
        json.dumps(
            incumbent.solution,
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
    ).hexdigest()
    metrics_payload = (
        asdict(result.metrics)
        if result is not None
        else {
            "seed": seed,
            "time_cap": DEFAULT_CONFIG.constructor_time_cap,
            "anchor_cap": DEFAULT_CONFIG.constructor_anchor_cap,
            "profile_budget": DEFAULT_CONFIG.constructor_profiles,
            "fallback_reason": fallback_reason,
        }
    )
    metrics_sha = hashlib.sha256(
        json.dumps(metrics_payload, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    profile_metrics = (
        [asdict(item) for item in result.metrics.profile_metrics]
        if result is not None
        else []
    )
    passed = (
        checked.feasible
        and checked.stage == 5
        and wall_seconds <= timelimit + 0.05
        and incumbent.verification_count in {1, 2}
    )
    return {
        "record_id": f"{ref.instance_id}|tl={timelimit:g}|seed={seed}|run={run_label}",
        "identity": identity,
        "complete": True,
        "status": "passed" if passed else "checker_failed",
        "timestamp": datetime.now().astimezone().isoformat(),
        **provenance,
        "interpreter": sys.executable,
        "argv": [sys.executable, "-m", "baseline.harness.cli", *sys.argv[1:]],
        "cwd": str(Path.cwd()),
        "instance_id": ref.instance_id,
        "instance_path": str(ref.path),
        "instance_sha": ref.sha256,
        "selector": selector,
        "solver": "native-constructor-entry" if constructor else "native-t0",
        "timelimit": timelimit,
        "seed": seed,
        "features": dict(sorted(features.items())),
        "wall_seconds": wall_seconds,
        "construction_seconds": construction_seconds,
        "full_check_seconds": result.full_check_seconds if result is not None else 0.0,
        "within_timelimit": wall_seconds <= timelimit + 0.05,
        "checker": checker_payload(checked),
        "block_count": len(parsed.blocks),
        "placed_count": len(parsed.blocks),
        "constructed_count": len(result.state.placements) if result is not None else 0,
        "time_cap": DEFAULT_CONFIG.constructor_time_cap,
        "anchor_cap": DEFAULT_CONFIG.constructor_anchor_cap,
        "profile_budget": list(DEFAULT_CONFIG.constructor_profiles),
        "profile_metrics": profile_metrics,
        "exact_predicates": sum(
            int(item.get("exact_predicates", 0)) for item in profile_metrics
        ),
        "anchor_attempts": sum(
            sum(int(count) for _, count in item.get("attempt_counts", ()))
            for item in profile_metrics
        ),
        "candidate_times": sum(len(item.get("order", ())) for item in profile_metrics),
        "predicate_s": construction_seconds,
        "t0_objective": t0_objective,
        "final_objective": final_objective,
        "relative_improvement": relative_improvement,
        "incumbent_updated": bool(result and result.metrics.incumbent_updated),
        "incumbent_verification_count": incumbent.verification_count,
        "unverified_return_count": 0,
        "deterministic_output_sha256": solution_sha,
        "deterministic_metrics_sha256": metrics_sha,
        "timeout": fallback_reason == "budget_expired",
        "crash": False,
        "exception": fallback_reason,
        "fallback_tier": "t0" if fallback_reason or not constructor else "constructor",
        "fallback_reason": fallback_reason,
    }


def run_gurobi_retime_case(
    ref: InstanceRef,
    *,
    selector: str,
    timelimit: float,
    seed: int,
    features: Mapping[str, str],
) -> dict[str, Any]:
    """Solve and full-check one synthetic fixed-layout retiming model."""

    provenance = repository_provenance()
    identity = record_identity(
        commit=provenance["commit"],
        dirty_diff_hash=provenance["dirty_diff_hash"],
        instance_sha=ref.sha256,
        solver="gurobi-indicator-retime",
        timelimit=timelimit,
        seed=seed,
        features=features,
    )
    started = time.monotonic()
    parsed = ProblemInstance.parse(ref.prob_info)
    block_ids = tuple(block.block_id for block in parsed.blocks)
    releases = tuple((block.block_id, block.release_time) for block in parsed.blocks)
    dues = tuple((block.block_id, block.due_date) for block in parsed.blocks)
    dwells = tuple((block.block_id, block.dwell) for block in parsed.blocks)
    current_rows = []
    cursor = 0
    for block in parsed.blocks:
        entry = max(cursor, block.release_time)
        current_rows.append((block.block_id, entry))
        cursor = entry + block.dwell
    current_entries = tuple(current_rows)
    conflict_pairs = tuple(
        (left, right)
        for left in block_ids
        for right in block_ids
        if left < right
    )
    request = RetimeRequest(
        block_ids=block_ids,
        releases=releases,
        dues=dues,
        dwells=dwells,
        current_entries=current_entries,
        conflict_pairs=conflict_pairs,
        seed=seed,
        threads=4,
    )
    spec = build_gurobi_model_spec(request, timebox=timelimit)
    raw = retime_gurobi(request, timelimit)
    result = normalize_result(request, raw, timebox=timelimit)
    schedule = (
        result.solution
        if result.solution is not None
        else tuple(
            (block_id, entry, entry + dict(dwells)[block_id])
            for block_id, entry in current_entries
        )
    )
    state = SolutionState(parsed)
    for block_id, entry, exit_time in schedule:
        state.place(
            Placement(
                block_id=block_id,
                bay_id=0,
                x=0,
                y=0,
                orient_idx=0,
                entry=entry,
                exit=exit_time,
            )
        )
    checked = official_check(
        ref.prob_info,
        serialize_non_interlock(state.placements.values()),
    )
    objective_matches = (
        result.objective is None
        or checked.obj1 is not None
        and abs(float(result.objective) - float(checked.obj1)) <= 1e-9
    )
    model_assertions = {
        "bounded_horizon": spec.horizon
        == max(dict(releases).values(), default=0) + sum(dict(dwells).values()),
        "integer_entries": all(item.variable_type == "integer" for item in spec.entries),
        "integer_tardiness": all(
            item.variable_type == "integer" for item in spec.tardiness
        ),
        "indicator_count": 2 * len(spec.disjunctions),
        "mip_start_count": len(spec.entries) + len(spec.disjunctions),
        "threads_at_most_four": spec.threads <= 4,
        "logs_disabled": spec.output_flag == 0,
    }
    normalized_unavailable = (
        result.status == "unavailable"
        and result.solution is None
        and result.objective is None
        and "unavailable" in (result.reason or "").lower()
    )
    solved = result.status in {"optimal", "feasible"}
    passed = (
        checked.feasible
        and checked.stage == 5
        and objective_matches
        and all(
            value is True or key in {"indicator_count", "mip_start_count"}
            for key, value in model_assertions.items()
        )
        and model_assertions["indicator_count"] == 2 * len(conflict_pairs)
        and model_assertions["mip_start_count"]
        == len(block_ids) + len(conflict_pairs)
        and (solved or normalized_unavailable)
    )
    gap = None
    if result.objective is not None and result.bound is not None:
        gap = abs(result.objective - result.bound) / max(1.0, abs(result.objective))
    wall_seconds = time.monotonic() - started
    return {
        "record_id": f"{ref.instance_id}|tl={timelimit:g}|seed={seed}|backend=gurobi",
        "identity": identity,
        "complete": True,
        "status": "passed" if passed else "checker_failed",
        "timestamp": datetime.now().astimezone().isoformat(),
        **provenance,
        "interpreter": sys.executable,
        "argv": [sys.executable, "-m", "baseline.harness.cli", *sys.argv[1:]],
        "cwd": str(Path.cwd()),
        "instance_id": ref.instance_id,
        "instance_path": str(ref.path),
        "instance_sha": ref.sha256,
        "selector": selector,
        "solver": "gurobi-indicator-retime",
        "timelimit": timelimit,
        "seed": seed,
        "features": dict(sorted(features.items())),
        "wall_seconds": wall_seconds,
        "checker": checker_payload(checked),
        "exact_z1_match": objective_matches,
        "model_assertions": model_assertions,
        "backend": {
            "name": "gurobi",
            "status": result.status,
            "bound": result.bound,
            "gap": gap,
            "build_seconds": result.build_s,
            "solve_seconds": result.solve_s,
            "first_solution_seconds": result.first_solution_s,
            "fallback": result.status == "unavailable",
            "reason": result.reason,
        },
        "timeout": result.status == "time_limit",
        "crash": result.status == "error",
        "exception": result.reason if result.status in {"error", "invalid"} else None,
        "incumbent_verification_count": 1,
        "unverified_return_count": 0,
        "fallback_tier": "synthetic-current" if result.status == "unavailable" else None,
        "fallback_reason": result.reason if result.status == "unavailable" else None,
    }


def run_exact_probe_fault_case(
    ref: InstanceRef,
    *,
    selector: str,
    timelimit: float,
    seed: int,
    features: Mapping[str, str],
) -> dict[str, Any]:
    """Fault both lazy probes after construction without touching its incumbent."""

    provenance = repository_provenance()
    identity = record_identity(
        commit=provenance["commit"],
        dirty_diff_hash=provenance["dirty_diff_hash"],
        instance_sha=ref.sha256,
        solver="native-constructor-probe-fallback",
        timelimit=timelimit,
        seed=seed,
        features=features,
    )
    started = time.monotonic()
    parsed = ProblemInstance.parse(ref.prob_info)
    incumbent = VerifiedIncumbent(parsed)
    incumbent.register_initial(build_t0(parsed))
    constructor_started = time.monotonic()
    remaining = max(0.0, timelimit - (constructor_started - started))
    reserve = min(
        DEFAULT_CONFIG.constructor_return_reserve_seconds,
        max(remaining - 0.05, 0.0),
    )
    constructor_result = None
    constructor_error = None
    try:
        constructor_result = construct_multistart(
            parsed,
            incumbent,
            Budget(remaining, reserve=reserve),
            seed=seed,
            profiles=DEFAULT_CONFIG.constructor_profiles,
            biased_variants=False,
            calibrated_entry=True,
            time_cap=DEFAULT_CONFIG.constructor_time_cap,
            anchor_cap=DEFAULT_CONFIG.constructor_anchor_cap,
        )
    except Exception as exc:
        constructor_error = f"{type(exc).__name__}: {exc}"
    construction_seconds = time.monotonic() - constructor_started

    before = json.dumps(
        incumbent.solution,
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
    before_sha = hashlib.sha256(before).hexdigest()

    def fault_probe(backend: str):
        def fail():
            raise RuntimeError(f"injected {backend} probe fault")

        def untouched():
            raise AssertionError("probe continued after its injected import fault")

        return BackendProbe(
            backend=backend,
            import_module=fail,
            create_environment=untouched,
            check_license=untouched,
            build_model=untouched,
            optimize=untouched,
            extract=untouched,
        )

    probe_remaining = max(0.0, timelimit - (time.monotonic() - started))
    health = probe_backends(
        incumbent,
        (fault_probe("gurobi"), fault_probe("cpsat")),
        budget=Budget(probe_remaining, reserve=0.0),
    )
    after = json.dumps(
        incumbent.solution,
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
    after_sha = hashlib.sha256(after).hexdigest()
    checked = incumbent.checker_result
    wall_seconds = time.monotonic() - started
    unchanged = before_sha == after_sha
    constructor_tier = bool(
        constructor_result is not None
        and constructor_result.metrics.incumbent_updated
    )
    passed = (
        checked.feasible
        and checked.stage == 5
        and unchanged
        and constructor_error is None
        and constructor_tier
        and all(
            not item.available and item.failure_stage == "import"
            for item in health
        )
        and wall_seconds <= timelimit + 0.25
    )
    return {
        "record_id": f"{ref.instance_id}|tl={timelimit:g}|seed={seed}|fault=probe_all",
        "identity": identity,
        "complete": True,
        "status": "passed" if passed else "checker_failed",
        "timestamp": datetime.now().astimezone().isoformat(),
        **provenance,
        "interpreter": sys.executable,
        "argv": [sys.executable, "-m", "baseline.harness.cli", *sys.argv[1:]],
        "cwd": str(Path.cwd()),
        "instance_id": ref.instance_id,
        "instance_path": str(ref.path),
        "instance_sha": ref.sha256,
        "selector": selector,
        "solver": "native-constructor-probe-fallback",
        "timelimit": timelimit,
        "seed": seed,
        "features": dict(sorted(features.items())),
        "wall_seconds": wall_seconds,
        "construction_seconds": construction_seconds,
        "checker": checker_payload(checked),
        "block_count": len(parsed.blocks),
        "placed_count": len(parsed.blocks),
        "incumbent_verification_count": incumbent.verification_count,
        "unverified_return_count": 0,
        "constructor_incumbent": constructor_tier,
        "constructor_error": constructor_error,
        "output_sha256_before_probe": before_sha,
        "output_sha256_after_probe": after_sha,
        "probe_output_byte_identical": unchanged,
        "backend_health": [asdict(item) for item in health],
        "timeout": False,
        "crash": False,
        "exception": None,
        "fallback_tier": "constructor" if constructor_tier else "t0",
        "fallback_reason": "probe_all",
    }


def run_cap_calibration_case(
    ref: InstanceRef,
    *,
    time_cap: int,
    anchor_cap: int,
    seed: int,
    features: Mapping[str, str],
) -> dict[str, Any]:
    """Compare one preregistered cap pair with an uncapped synthetic reference."""
    provenance = repository_provenance()
    parsed = ProblemInstance.parse(ref.prob_info)
    assignment = AssignmentV1(parsed).assign()
    started = time.monotonic()
    reference = construct_profile(
        parsed,
        assignment,
        "PF3",
        time_cap=None,
        anchor_cap=None,
    )
    candidate = construct_profile(
        parsed,
        assignment,
        "PF3",
        time_cap=time_cap,
        anchor_cap=anchor_cap,
    )
    reference_checked = official_check(
        ref.prob_info,
        serialize_non_interlock(reference.state.placements.values()),
    )
    candidate_checked = official_check(
        ref.prob_info,
        serialize_non_interlock(candidate.state.placements.values()),
    )
    wall_seconds = time.monotonic() - started
    synthetic_reference = ref.instance_id.startswith("synthetic-")
    reference_success = (
        len(reference.state.placements)
        if synthetic_reference
        else len(parsed.blocks)
    )
    candidate_success = len(candidate.state.placements)
    success_ratio = candidate_success / max(reference_success, 1)
    parity_loss = int(
        candidate_checked.feasible is not True
        or candidate_checked.stage != 5
        or (
            synthetic_reference
            and reference_checked.feasible != candidate_checked.feasible
        )
    )
    objective_loss = (
        max(
            0.0,
            float(candidate_checked.objective or 0.0)
            - float(reference_checked.objective or 0.0),
        )
        if synthetic_reference
        else 0.0
    )
    passed = (
        candidate_checked.feasible
        and candidate_checked.stage == 5
        and parity_loss == 0
        and success_ratio >= 0.99
    )
    pair = f"{time_cap}x{anchor_cap}"
    return {
        "record_id": f"{ref.instance_id}|caps={pair}",
        "identity": record_identity(
            commit=provenance["commit"],
            dirty_diff_hash=provenance["dirty_diff_hash"],
            instance_sha=ref.sha256,
            solver="constructor-cap-calibration",
            timelimit=5.0,
            seed=seed,
            features={**features, "caps": pair},
        ),
        "complete": True,
        "status": "passed" if passed else "failed",
        "timestamp": datetime.now().astimezone().isoformat(),
        **provenance,
        "instance_id": ref.instance_id,
        "instance_sha": ref.sha256,
        "selector": "synthetic" if synthetic_reference else "dev-10",
        "solver": "constructor-cap-calibration",
        "seed": seed,
        "time_cap": time_cap,
        "anchor_cap": anchor_cap,
        "wall_seconds": wall_seconds,
        "reference_insertion_success": reference_success,
        "reference_kind": "uncapped_synthetic" if synthetic_reference else "training_checker_probe",
        "candidate_insertion_success": candidate_success,
        "reference_success_ratio": success_ratio,
        "parity_loss": parity_loss,
        "objective_loss": objective_loss,
        "checker": checker_payload(candidate_checked),
        "exact_predicates": candidate.metrics.exact_predicates,
        "fallback_count": candidate.metrics.fallback_count,
    }


def make_escalation_stress_ref(
    scenario: str,
    *,
    fixture_dir: Path,
) -> InstanceRef:
    """Materialize one preregistered S1-03 escalation fixture."""
    raw = _escalation_fixture(scenario)
    fixture_dir.mkdir(parents=True, exist_ok=True)
    encoded = (json.dumps(raw, indent=2, sort_keys=True) + "\n").encode()
    path = fixture_dir / f"s1-03-{scenario}.json"
    path.write_bytes(encoded)
    return InstanceRef(
        instance_id=f"s1-03-{scenario}",
        path=path,
        sha256=hashlib.sha256(encoded).hexdigest(),
        prob_info=raw,
    )


def run_escalation_stress_case(
    ref: InstanceRef,
    *,
    scenario: str,
    timelimit: float,
    seed: int,
    features: Mapping[str, str],
) -> dict[str, Any]:
    """Exercise one S1-03 escalation path and full-check the final state."""
    provenance = repository_provenance()
    identity = record_identity(
        commit=provenance["commit"],
        dirty_diff_hash=provenance["dirty_diff_hash"],
        instance_sha=ref.sha256,
        solver="anchor-escalation-proof",
        timelimit=timelimit,
        seed=seed,
        features={**features, "scenario_case": scenario},
    )
    started = time.monotonic()
    parsed = ProblemInstance.parse(ref.prob_info)
    state = SolutionState(parsed)
    target_id, preferred_bay, preferred_orient = _seed_escalation_state(
        state, scenario
    )
    result = escalate_insert(
        state,
        target_id,
        preferred_bay_id=preferred_bay,
        preferred_orient_idx=preferred_orient,
    )
    previous = state.place(result.candidate.placement)
    checked = official_check(
        ref.prob_info,
        serialize_non_interlock(state.placements.values()),
    )
    wall_seconds = time.monotonic() - started
    placed_once = previous is None and len(state.placements) == len(parsed.blocks)
    passed = (
        checked.feasible
        and checked.stage == 5
        and placed_once
        and wall_seconds <= timelimit
    )
    return {
        "record_id": f"{scenario}|tl={timelimit:g}|seed={seed}",
        "identity": identity,
        "complete": True,
        "status": "passed" if passed else "checker_failed",
        "timestamp": datetime.now().astimezone().isoformat(),
        **provenance,
        "interpreter": sys.executable,
        "argv": [sys.executable, "-m", "baseline.harness.cli", *sys.argv[1:]],
        "cwd": str(Path.cwd()),
        "instance_id": ref.instance_id,
        "instance_path": str(ref.path),
        "instance_sha": ref.sha256,
        "selector": "stress",
        "solver": "anchor-escalation-proof",
        "timelimit": timelimit,
        "seed": seed,
        "features": {**dict(sorted(features.items())), "scenario_case": scenario},
        "wall_seconds": wall_seconds,
        "checker": checker_payload(checked),
        "block_count": len(parsed.blocks),
        "placed_count": len(state.placements),
        "placed_exactly_once": placed_once,
        "escalation_attempt": result.attempt,
        "attempted_bays": list(result.attempted_bays),
        "time_cap": result.time_cap,
        "anchor_cap": result.anchor_cap,
        "fallback_reason": result.fallback_reason,
        "cache": {
            "hits": state.geom.stats.cache_hits,
            "misses": state.geom.stats.cache_misses,
            "evictions": state.geom.stats.cache_evictions,
            "exact_predicates": state.geom.stats.exact_predicates,
        },
        "timeout": False,
        "crash": False,
        "exception": None,
    }


def _escalation_fixture(scenario: str) -> dict[str, Any]:
    square = ((0, 0), (2, 0), (2, 2), (0, 2))
    if scenario == "negative_origin":
        negative = ((1, 1), (-1, 1), (-1, -1), (1, -1))
        return _raw_fixture(
            "s1-03-negative-origin",
            ((6, 6),),
            (
                _raw_block(square, release=0, due=20, processing=10, preferences=(1,)),
                _raw_block(negative, release=0, due=20, processing=10, preferences=(1,)),
            ),
        )
    if scenario == "contact":
        return _raw_fixture(
            "s1-03-contact",
            ((4, 2),),
            tuple(
                _raw_block(square, release=0, due=20, processing=10, preferences=(1,))
                for _ in range(2)
            ),
        )
    if scenario == "no_preferred_fit":
        return _raw_fixture(
            "s1-03-no-preferred-fit",
            ((1, 1), (3, 3)),
            (_raw_block(square, release=0, due=5, processing=1, preferences=(2, 1)),),
        )
    if scenario == "bounded_failure":
        blocks = tuple(
            _raw_block(
                square,
                release=index,
                due=200,
                processing=1,
                preferences=(1,),
            )
            for index in range(17)
        ) + (
            _raw_block(square, release=0, due=1, processing=1, preferences=(1,)),
        )
        return _raw_fixture("s1-03-bounded-failure", ((2, 2),), blocks)
    raise ValueError(f"unsupported S1-03 stress scenario: {scenario}")


def _seed_escalation_state(
    state: SolutionState,
    scenario: str,
) -> tuple[int, int, int]:
    if scenario in {"negative_origin", "contact"}:
        state.place(Placement(0, 0, 0, 0, 0, 0, 10))
        return 1, 0, 0
    if scenario == "no_preferred_fit":
        return 0, 0, 0
    if scenario == "bounded_failure":
        for block_id in range(16):
            state.place(Placement(block_id, 0, 0, 0, 0, block_id, block_id + 1))
        state.place(Placement(16, 0, 0, 0, 0, 16, 100))
        return 17, 0, 0
    raise ValueError(f"unsupported S1-03 stress scenario: {scenario}")


def _raw_fixture(
    name: str,
    bays: tuple[tuple[int, int], ...],
    blocks: tuple[dict[str, Any], ...],
) -> dict[str, Any]:
    return {
        "name": name,
        "bays": [{"width": width, "height": height} for width, height in bays],
        "blocks": list(blocks),
        "weights": {"w1": 1.0, "w2": 1.0, "w3": 1.0},
    }


def _raw_block(
    layer: tuple[tuple[int, int], ...],
    *,
    release: int,
    due: int,
    processing: int,
    preferences: tuple[int, ...],
) -> dict[str, Any]:
    return {
        "release_time": release,
        "due_date": due,
        "processing_time": processing,
        "workload": 1,
        "bay_preferences": list(preferences),
        "shape": [{
            "orientation": 0,
            "layers": [[list(point) for point in layer]],
        }],
    }


def _case_record_id(instance_id: str, timelimit: float, seed: int, fault: str) -> str:
    return f"{instance_id}|tl={timelimit:g}|seed={seed}|fault={fault}"


def _git(*args: str, binary: bool = False) -> str | bytes:
    completed = subprocess.run(
        ["git", *args],
        cwd=REPO_ROOT,
        check=True,
        capture_output=True,
        text=not binary,
    )
    return completed.stdout
