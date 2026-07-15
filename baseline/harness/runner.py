"""Repository provenance and checker-authoritative T0 execution records."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import asdict, replace
from datetime import datetime
import hashlib
import itertools
import json
import math
from pathlib import Path
import random
import resource
import subprocess
import sys
import time
from typing import Any, Mapping

from numpy.random import Generator, PCG64

from .checker import checker_payload
from .process import run_process
from .schema import record_identity
from .selectors import InstanceRef, REPO_ROOT

try:
    from baseline.solver.alns import (
        OperatorRegistry,
        OperatorWeights,
        RRT_Acceptor,
        RetimeTrigger,
        RetimeWallPolicy,
        SAAcceptor,
        StagnationController,
        StrictAcceptor,
        run_alns,
        sample_destroy_count,
    )
    from baseline.solver.assign import (
        AssignmentVerification,
        AssignmentV1,
        assignment_from_solution,
        assignment_v2,
        choose_assignment_candidate,
    )
    from baseline.solver.budget import Budget, BudgetExpired, deadline_reserve
    from baseline.solver.checker_adapter import official_check
    from baseline.solver.config import DEFAULT_CONFIG
    from baseline.solver.construct import (
        construct_multistart,
        construct_profile,
        escalate_insert,
    )
    from baseline.solver.exact import (
        AssignmentResult as ExactAssignmentResult,
        BackendProbe,
        ExactResult,
        RetimeRequest,
        SOLUTION_STATUSES,
        normalize_result,
        probe_backends,
    )
    from baseline.solver.cpsat_backend import assign_cpsat, retime_cpsat
    from baseline.solver.gurobi_backend import (
        assign_gurobi,
        build_gurobi_model_spec,
        retime_gurobi,
    )
    from baseline.solver.incumbent import VerifiedIncumbent
    from baseline.solver.geometry import GeomKernel, ShapeInfo
    from baseline.solver.entry import solve
    from baseline.solver.instance import ProblemInstance
    from baseline.solver.retime import retime_bay, retime_sweep
    from baseline.solver.serialize import serialize_non_interlock
    from baseline.solver.state import Placement, SolutionState
    from baseline.solver.trivial import build_t0
except ModuleNotFoundError:
    from solver.alns import (
        OperatorRegistry,
        OperatorWeights,
        RRT_Acceptor,
        RetimeTrigger,
        RetimeWallPolicy,
        SAAcceptor,
        StagnationController,
        StrictAcceptor,
        run_alns,
        sample_destroy_count,
    )
    from solver.assign import (
        AssignmentVerification,
        AssignmentV1,
        assignment_from_solution,
        assignment_v2,
        choose_assignment_candidate,
    )
    from solver.budget import Budget, BudgetExpired, deadline_reserve
    from solver.checker_adapter import official_check
    from solver.config import DEFAULT_CONFIG
    from solver.construct import construct_multistart, construct_profile, escalate_insert
    from solver.exact import (
        AssignmentResult as ExactAssignmentResult,
        BackendProbe,
        ExactResult,
        RetimeRequest,
        SOLUTION_STATUSES,
        normalize_result,
        probe_backends,
    )
    from solver.cpsat_backend import assign_cpsat, retime_cpsat
    from solver.gurobi_backend import assign_gurobi, build_gurobi_model_spec, retime_gurobi
    from solver.geometry import GeomKernel, ShapeInfo
    from solver.incumbent import VerifiedIncumbent
    from solver.entry import solve
    from solver.instance import ProblemInstance
    from solver.retime import retime_bay, retime_sweep
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


def run_backend_parity_record(
    *,
    cases: int,
    seed: int,
    timebox: float,
) -> dict[str, Any]:
    """Compare both exact adapters with manual optima and the official checker."""

    rng = random.Random(seed)
    case_records: list[dict[str, Any]] = []
    semantic_mismatches = 0
    optimal_mismatches = 0
    backend_mismatches = 0
    cpsat_available = True
    started = time.monotonic()
    for case_id in range(cases):
        block_count = 2 + rng.randrange(3)
        block_ids = tuple(range(block_count))
        releases = tuple((item, rng.randrange(4)) for item in block_ids)
        dwells = tuple((item, 1 + rng.randrange(3)) for item in block_ids)
        release_map = dict(releases)
        dwell_map = dict(dwells)
        dues = tuple(
            (
                item,
                release_map[item] + dwell_map[item] + rng.randrange(4),
            )
            for item in block_ids
        )
        conflicts = tuple(itertools.combinations(block_ids, 2))
        current = _serial_schedule(block_ids, release_map, dwell_map)
        request = RetimeRequest(
            block_ids=block_ids,
            releases=releases,
            dues=dues,
            dwells=dwells,
            current_entries=tuple((item, current[item][0]) for item in block_ids),
            conflict_pairs=conflicts,
            seed=seed + case_id,
            threads=4,
        )
        manual_objective, manual_schedule = _manual_single_bay_optimum(request)
        prob_info = _retime_prob_info(request, case_id)
        parsed = ProblemInstance.parse(prob_info)
        results = (
            normalize_result(
                request,
                retime_gurobi(request, timebox),
                timebox=timebox,
            ),
            normalize_result(
                request,
                retime_cpsat(request, timebox),
                timebox=timebox,
            ),
        )
        objectives: list[float] = []
        backend_rows: list[dict[str, Any]] = []
        for result in results:
            available = result.status != "unavailable"
            if result.backend == "cpsat" and not available:
                cpsat_available = False
            semantic_ok = True
            checker_z1 = None
            if available:
                semantic_ok = result.status == "optimal" and result.solution is not None
                if semantic_ok:
                    state = SolutionState(parsed)
                    for block_id, entry, exit_time in result.solution or ():
                        state.place(
                            Placement(block_id, 0, 0, 0, 0, entry, exit_time)
                        )
                    checked = official_check(
                        prob_info,
                        serialize_non_interlock(state.placements.values()),
                    )
                    checker_z1 = checked.obj1
                    semantic_ok = checked.feasible and checked.stage == 5
                    semantic_ok = semantic_ok and checker_z1 is not None and math.isclose(
                        float(checker_z1),
                        float(result.objective),
                        rel_tol=1e-9,
                        abs_tol=1e-9,
                    )
                if result.objective is not None:
                    objectives.append(float(result.objective))
                if not semantic_ok:
                    semantic_mismatches += 1
                if result.objective is None or not math.isclose(
                    float(result.objective or 0.0),
                    float(manual_objective),
                    rel_tol=1e-9,
                    abs_tol=1e-9,
                ):
                    optimal_mismatches += 1
            backend_rows.append(
                {
                    "backend": result.backend,
                    "status": result.status,
                    "objective": result.objective,
                    "bound": result.bound,
                    "solution": result.solution,
                    "checker_z1": checker_z1,
                    "build_seconds": result.build_s,
                    "solve_seconds": result.solve_s,
                    "first_solution_seconds": result.first_solution_s,
                    "reason": result.reason,
                }
            )
        if len(objectives) > 1 and any(
            not math.isclose(value, objectives[0], rel_tol=1e-9, abs_tol=1e-9)
            for value in objectives[1:]
        ):
            backend_mismatches += 1
        case_records.append(
            {
                "case_id": case_id,
                "request": {
                    "releases": releases,
                    "dues": dues,
                    "dwells": dwells,
                    "current_entries": request.current_entries,
                    "conflict_pairs": conflicts,
                },
                "manual_objective": manual_objective,
                "manual_solution": tuple(
                    (item, *manual_schedule[item]) for item in block_ids
                ),
                "backends": backend_rows,
            }
        )
    passed = (
        cpsat_available
        and semantic_mismatches == 0
        and optimal_mismatches == 0
        and backend_mismatches == 0
    )
    return {
        "record_id": "backend",
        "status": "passed" if passed else "failed",
        "complete": True,
        "timestamp": datetime.now().astimezone().isoformat(),
        **repository_provenance(),
        "interpreter": sys.executable,
        "cases": cases,
        "seed": seed,
        "timebox": timebox,
        "semantic_mismatches": semantic_mismatches,
        "optimal_mismatches": optimal_mismatches,
        "backend_mismatches": backend_mismatches,
        "cpsat_available": cpsat_available,
        "wall_seconds": time.monotonic() - started,
        "case_records": case_records,
    }


def _serial_schedule(
    order: tuple[int, ...],
    releases: Mapping[int, int],
    dwells: Mapping[int, int],
) -> dict[int, tuple[int, int]]:
    schedule: dict[int, tuple[int, int]] = {}
    cursor = 0
    for block_id in order:
        entry = max(cursor, releases[block_id])
        cursor = entry + dwells[block_id]
        schedule[block_id] = (entry, cursor)
    return schedule


def _manual_single_bay_optimum(
    request: RetimeRequest,
) -> tuple[int, dict[int, tuple[int, int]]]:
    releases = dict(request.releases)
    dues = dict(request.dues)
    dwells = dict(request.dwells)
    best: tuple[int, tuple[tuple[int, int, int], ...]] | None = None
    for order in itertools.permutations(request.block_ids):
        schedule = _serial_schedule(tuple(order), releases, dwells)
        objective = sum(
            max(0, schedule[block_id][1] - dues[block_id])
            for block_id in request.block_ids
        )
        rows = tuple(
            (block_id, schedule[block_id][0], schedule[block_id][1])
            for block_id in request.block_ids
        )
        candidate = (objective, rows)
        if best is None or candidate < best:
            best = candidate
    assert best is not None
    return best[0], {block_id: (entry, exit_time) for block_id, entry, exit_time in best[1]}


def _retime_prob_info(request: RetimeRequest, case_id: int) -> dict[str, Any]:
    releases = dict(request.releases)
    dues = dict(request.dues)
    dwells = dict(request.dwells)
    square = [[[0, 0], [2, 0], [2, 2], [0, 2]]]
    return {
        "name": f"synthetic-backend-parity-{case_id}",
        "bays": [{"width": 10, "height": 10}],
        "blocks": [
            {
                "release_time": releases[block_id],
                "due_date": dues[block_id],
                "processing_time": dwells[block_id],
                "workload": 1,
                "bay_preferences": [1],
                "shape": [{"orientation": 0, "layers": square}],
            }
            for block_id in request.block_ids
        ],
        "weights": {"w1": 1.0, "w2": 1.0, "w3": 1.0},
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


def run_s6_subprocess_case(
    ref: InstanceRef,
    *,
    timelimit: float,
    seed: int,
    features: Mapping[str, str],
    fault: str = "none",
) -> dict[str, Any]:
    """Run one selected-default solver case in a bounded process group."""

    provenance = repository_provenance()
    run_features = {**features, "expanded_fault": fault}
    identity = record_identity(
        commit=provenance["commit"],
        dirty_diff_hash=provenance["dirty_diff_hash"],
        instance_sha=ref.sha256,
        solver="native-s6-stress",
        timelimit=timelimit,
        seed=seed,
        features=run_features,
    )
    result = run_process(
        [
            sys.executable,
            "-m",
            "baseline.harness.s6_worker",
            "--instance",
            str(ref.path),
            "--timelimit",
            f"{timelimit:g}",
            "--seed",
            str(seed),
            "--fault",
            fault,
        ],
        timeout=timelimit + 2.0,
        terminate_grace=2.0,
        cwd=str(REPO_ROOT),
    )
    payload = _last_json_object(result.stdout)
    solution = payload.get("solution")
    checked = (
        official_check(ref.prob_info, deepcopy(solution))
        if isinstance(solution, dict)
        else None
    )
    telemetry = payload.get("telemetry", {})
    if not isinstance(telemetry, dict):
        telemetry = {}
    selected_config = payload.get("selected_config", {})
    if not isinstance(selected_config, dict):
        selected_config = {}
    config_matches = (
        selected_config.get("alns") is True
        and selected_config.get("alns_acceptor") == "sa"
        and selected_config.get("alns_adaptive") is False
        and selected_config.get("alns_dirty_minimum") == 3
        and math.isclose(
            float(selected_config.get("alns_dirty_fraction", -1.0)),
            0.03,
            rel_tol=0.0,
            abs_tol=1e-12,
        )
    )
    if fault == "none":
        fault_applied = True
    elif fault == "alns_full_check":
        fault_applied = telemetry.get("alns_fault_applied") == "full_check"
    else:
        fault_applied = (
            f"injected entry fault at {fault}"
            in str(telemetry.get("fallback_reason", ""))
        )
    case_assertions = _s6_case_assertions(ref, solution)
    cache = _s6_cache_pressure(ref) if ref.instance_id == "s6-cache-pressure" else {
        "hits": 0,
        "misses": 0,
        "evictions": 0,
        "exact_predicates": 0,
    }
    tolerance_seconds = 1.5
    within_timelimit = result.wall_seconds <= timelimit + tolerance_seconds
    passed = (
        result.exit_code == 0
        and result.signal is None
        and not result.timed_out
        and not result.group_leak_detected
        and not result.group_alive_after_cleanup
        and checked is not None
        and checked.feasible
        and checked.stage == 5
        and config_matches
        and fault_applied
        and all(case_assertions.values())
        and within_timelimit
        and (
            ref.instance_id != "s6-cache-pressure"
            or int(cache["evictions"]) > 0
        )
    )
    peak_rss = int(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss)
    if sys.platform != "darwin":
        peak_rss *= 1024
    fallback_reason = telemetry.get("fallback_reason")
    return {
        "record_id": _case_record_id(ref.instance_id, timelimit, seed, fault),
        "identity": identity,
        "complete": True,
        "status": "passed" if passed else "checker_failed",
        "timestamp": datetime.now().astimezone().isoformat(),
        **provenance,
        "interpreter": sys.executable,
        "argv": [sys.executable, "-m", "baseline.harness.cli", *sys.argv[1:]],
        "cwd": str(REPO_ROOT),
        "instance_id": ref.instance_id,
        "instance_path": str(ref.path),
        "instance_sha": ref.sha256,
        "selector": "stress",
        "solver": "native-s6-stress",
        "timelimit": timelimit,
        "budget_tolerance_seconds": tolerance_seconds,
        "within_timelimit": within_timelimit,
        "seed": seed,
        "features": dict(sorted(run_features.items())),
        "expanded_fault": fault,
        "fault_applied": fault_applied,
        "wall_seconds": result.wall_seconds,
        "subprocess_pid": result.pid,
        "subprocess_exit": result.exit_code,
        "signal": result.signal,
        "term_sent": result.term_sent,
        "kill_sent": result.kill_sent,
        "timeout": result.timed_out,
        "group_leak_detected": result.group_leak_detected,
        "group_alive_after_cleanup": result.group_alive_after_cleanup,
        "checker": None if checked is None else checker_payload(checked),
        "worker_checker": payload.get("checker"),
        "solution_sha256": payload.get("solution_sha256"),
        "selected_config": selected_config,
        "selected_config_matches": config_matches,
        "case_assertions": case_assertions,
        "block_count": len(ref.prob_info.get("blocks", ())),
        "bay_count": len(ref.prob_info.get("bays", ())),
        "stage_timing": {
            "t0_and_verify_seconds": telemetry.get("t0_and_verify_seconds"),
            "constructor_seconds": telemetry.get("constructor_seconds"),
            "retime_seconds": telemetry.get("retime_seconds"),
            "alns_seconds": telemetry.get("alns_seconds"),
        },
        "backend": {
            "name": telemetry.get("retime_backend"),
            "status": "fallback" if telemetry.get("retime_fallback") else "completed",
            "attempts": telemetry.get("retime_attempts", ()),
        },
        "iterations": telemetry.get("alns_metrics", {}).get("iterations", 0),
        "proposals": telemetry.get("alns_metrics", {}).get("proposals", 0),
        "accepted_improving": telemetry.get("alns_metrics", {}).get("improving", 0),
        "accepted_worsening": telemetry.get("alns_metrics", {}).get("accepted_worsening", 0),
        "rejected": telemetry.get("alns_metrics", {}).get("rejected", 0),
        "cache": cache,
        "peak_rss_bytes": peak_rss,
        "crash": result.exit_code not in {0, None},
        "exception": payload.get("exception") or fallback_reason,
        "incumbent_verification_count": telemetry.get("incumbent_verification_count", 0),
        "unverified_return_count": 0,
        "fallback_tier": "verified_incumbent" if fallback_reason else None,
        "fallback_reason": fallback_reason,
    }


def _last_json_object(stdout: str) -> dict[str, Any]:
    for line in reversed(stdout.splitlines()):
        if not line.lstrip().startswith("{"):
            continue
        try:
            value = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            return value
    return {}


def _s6_case_assertions(
    ref: InstanceRef,
    solution: Any,
) -> dict[str, bool]:
    if not isinstance(solution, dict):
        return {"solution_mapping": False}
    operations = solution.get("operations", {})
    entries = [
        operation
        for daily in operations.values()
        for operation in daily
        if operation.get("type") == "ENTRY"
    ]
    assertions = {
        "solution_mapping": True,
        "one_entry_per_block": len(entries) == len(ref.prob_info["blocks"]),
    }
    if ref.instance_id == "s6-one-bay":
        assertions["only_bay_zero"] = all(item.get("bay_id") == 0 for item in entries)
    elif ref.instance_id == "s6-one-layer":
        assertions["only_one_layer"] = all(
            len(orientation["layers"]) == 1
            for block in ref.prob_info["blocks"]
            for orientation in block["shape"]
        )
    elif ref.instance_id == "s6-p-zero":
        assertions["all_processing_zero"] = all(
            block["processing_time"] == 0 for block in ref.prob_info["blocks"]
        )
    elif ref.instance_id == "s6-contact":
        manual = {
            "operations": {
                "0": [
                    {"type": "ENTRY", "block_id": 0, "bay_id": 0, "x": 0, "y": 0, "orient_idx": 0},
                    {"type": "ENTRY", "block_id": 1, "bay_id": 0, "x": 2, "y": 0, "orient_idx": 0},
                ],
                "2": [
                    {"type": "EXIT", "block_id": 0, "bay_id": 0},
                    {"type": "EXIT", "block_id": 1, "bay_id": 0},
                ],
            }
        }
        contact_checked = official_check(ref.prob_info, manual)
        assertions["boundary_contact_stage5"] = (
            contact_checked.feasible and contact_checked.stage == 5
        )
    elif ref.instance_id == "s6-preference-fallback":
        assertions["nonpreferred_fit_selected"] = (
            len(entries) == 1 and entries[0].get("bay_id") == 1
        )
    elif ref.instance_id == "s6-dense":
        assertions["dense_block_count"] = len(ref.prob_info["blocks"]) == 24
    elif ref.instance_id == "s6-max-training-n":
        assertions["max_training_block_count"] = len(ref.prob_info["blocks"]) == 300
    return assertions


def _s6_cache_pressure(ref: InstanceRef) -> dict[str, int]:
    parsed = ProblemInstance.parse(ref.prob_info)
    shapes_by_key = {
        shape.shape_key: shape
        for block in parsed.blocks
        for orientation in block.orientations
        for shape in (ShapeInfo.from_orientation(orientation),)
    }
    shapes = tuple(shapes_by_key.values())
    kernel = GeomKernel(cache_cap=32)
    for left, right in itertools.product(shapes, repeat=2):
        kernel.union_disjoint(left, right, 0, 0)
    return {
        "hits": kernel.stats.cache_hits,
        "misses": kernel.stats.cache_misses,
        "evictions": kernel.stats.cache_evictions,
        "exact_predicates": kernel.stats.exact_predicates,
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


def run_assignment_v2_case(
    ref: InstanceRef,
    *,
    selector: str,
    timelimit: float,
    seed: int,
    features: Mapping[str, str],
) -> dict[str, Any]:
    """Build v1/v2 assignments, construct both, and prove checker-float parity."""

    provenance = repository_provenance()
    identity = record_identity(
        commit=provenance["commit"],
        dirty_diff_hash=provenance["dirty_diff_hash"],
        instance_sha=ref.sha256,
        solver="assignment-v2-gurobi-proof",
        timelimit=timelimit,
        seed=seed,
        features=features,
    )
    started = time.monotonic()
    parsed = ProblemInstance.parse(ref.prob_info)
    config = replace(
        DEFAULT_CONFIG,
        assignment_backend="gurobi",
        constructor_seed=seed,
    )

    v1_started = time.monotonic()
    v1 = AssignmentV1(parsed).assign()
    v1_constructed = construct_fixed_assignment(parsed, v1)
    v1_checked = official_check(
        ref.prob_info,
        serialize_non_interlock(v1_constructed.placements.values()),
    )
    v1_seconds = time.monotonic() - v1_started

    v2_started = time.monotonic()
    backend_result = assignment_v2(parsed, v1, config=config)
    v2_assignment = (
        assignment_from_solution(parsed, backend_result.solution, order=v1.order)
        if backend_result.status in SOLUTION_STATUSES
        and backend_result.solution is not None
        else None
    )
    v2_constructed = (
        construct_fixed_assignment(parsed, v2_assignment)
        if v2_assignment is not None
        else None
    )
    v2_checked = (
        official_check(
            ref.prob_info,
            serialize_non_interlock(v2_constructed.placements.values()),
        )
        if v2_constructed is not None
        else None
    )
    v2_seconds = time.monotonic() - v2_started
    wall_seconds = time.monotonic() - started

    v1_z2_error = _relative_error(v1_checked.obj2, v1.z2)
    v1_z3_error = _relative_error(v1_checked.obj3, v1.z3)
    v2_z2_error = _relative_error(
        v2_checked.obj2 if v2_checked is not None else None,
        backend_result.z2,
    )
    v2_z3_error = _relative_error(
        v2_checked.obj3 if v2_checked is not None else None,
        backend_result.z3,
    )
    v1_membership = tuple(
        sorted(
            (placement.block_id, placement.bay_id)
            for placement in v1_constructed.placements.values()
        )
    )
    v2_membership = (
        tuple(
            sorted(
                (placement.block_id, placement.bay_id)
                for placement in v2_constructed.placements.values()
            )
        )
        if v2_constructed is not None
        else ()
    )
    v1_expected = tuple(
        sorted((block_id, item.bay_id) for block_id, item in v1.assignments.items())
    )
    passed = (
        v1_checked.feasible
        and v1_checked.stage == 5
        and v2_checked is not None
        and v2_checked.feasible
        and v2_checked.stage == 5
        and backend_result.status in SOLUTION_STATUSES
        and backend_result.solution is not None
        and v1_membership == v1_expected
        and v2_membership == tuple(sorted(backend_result.solution))
        and v1_z2_error is not None
        and v1_z2_error <= 1e-6
        and v1_z3_error is not None
        and v1_z3_error <= 1e-6
        and v2_z2_error is not None
        and v2_z2_error <= 1e-6
        and v2_z3_error is not None
        and v2_z3_error <= 1e-6
        and wall_seconds <= timelimit
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
        "solver": "assignment-v2-gurobi-proof",
        "timelimit": timelimit,
        "seed": seed,
        "features": dict(sorted(features.items())),
        "wall_seconds": wall_seconds,
        "subprocess_exit": 0,
        "signal": None,
        "checker": checker_payload(v2_checked) if v2_checked is not None else {},
        "v1_checker": checker_payload(v1_checked),
        "stage_timing": {
            "v1_construct_and_check_seconds": v1_seconds,
            "v2_assignment_construct_and_check_seconds": v2_seconds,
            "assignment_build_seconds": backend_result.build_s,
            "assignment_solve_seconds": backend_result.solve_s,
            "assignment_first_solution_seconds": backend_result.first_solution_s,
        },
        "backend": {
            "name": backend_result.backend,
            "status": backend_result.status,
            "bound": backend_result.bound,
            "build_seconds": backend_result.build_s,
            "solve_seconds": backend_result.solve_s,
            "first_solution_seconds": backend_result.first_solution_s,
            "fallback": False,
        },
        "block_count": len(parsed.blocks),
        "assigned": len(backend_result.solution or ()),
        "v1_z2": v1.z2,
        "v1_z3": v1.z3,
        "v2_z2": backend_result.z2,
        "v2_z3": backend_result.z3,
        "v2_overload": backend_result.overload,
        "v2_assignment_objective": backend_result.objective,
        "v1_float_z2_relative_error": v1_z2_error,
        "v1_float_z3_relative_error": v1_z3_error,
        "v2_float_z2_relative_error": v2_z2_error,
        "v2_float_z3_relative_error": v2_z3_error,
        "v1_membership_preserved": v1_membership == v1_expected,
        "v2_membership_preserved": v2_membership
        == tuple(sorted(backend_result.solution or ())),
        "timeout": backend_result.status == "time_limit",
        "crash": False,
        "exception": backend_result.reason,
        "incumbent_verification_count": 0,
        "unverified_return_count": 0,
        "fallback_tier": None,
        "fallback_reason": None,
    }


def run_assignment_fallback_case(
    ref: InstanceRef,
    *,
    selector: str,
    timelimit: float,
    seed: int,
    features: Mapping[str, str],
    fault: str,
) -> dict[str, Any]:
    """Force S4 assignment backend faults and retain a checked incumbent."""

    provenance = repository_provenance()
    identity = record_identity(
        commit=provenance["commit"],
        dirty_diff_hash=provenance["dirty_diff_hash"],
        instance_sha=ref.sha256,
        solver="assignment-fallback-stress",
        timelimit=timelimit,
        seed=seed,
        features={**features, "fault": fault},
    )
    started = time.monotonic()
    parsed = ProblemInstance.parse(ref.prob_info)
    config = replace(
        DEFAULT_CONFIG,
        assignment_backend="gurobi",
        assignment_threads=1,
        constructor_seed=seed,
    )
    v1 = AssignmentV1(parsed).assign()

    def verify(candidate: Any) -> AssignmentVerification:
        try:
            constructed = construct_fixed_assignment(parsed, candidate)
            checked = official_check(
                ref.prob_info,
                serialize_non_interlock(constructed.placements.values()),
            )
        except Exception as exc:
            return AssignmentVerification(
                feasible=False,
                objective=None,
                reason=f"{type(exc).__name__}: {exc}",
            )
        return AssignmentVerification(
            feasible=checked.feasible and checked.stage == 5,
            objective=checked.objective,
            reason=None if checked.feasible else "; ".join(checked.violations),
        )

    def injected(backend: str) -> Any:
        def call(request: Any, timebox: float) -> ExactAssignmentResult:
            del request, timebox
            return ExactAssignmentResult(
                backend=backend,
                status="error",
                solution=None,
                objective=None,
                bound=None,
                z2=None,
                z3=None,
                overload=None,
                build_s=0.0,
                solve_s=0.0,
                first_solution_s=None,
                reason=f"injected {backend} assignment fault",
            )

        return call

    calls = {
        "gurobi": injected("gurobi") if fault in {"gurobi", "both"} else assign_gurobi,
        "cpsat": injected("cpsat") if fault in {"cp_sat", "both"} else assign_cpsat,
    }
    selection = choose_assignment_candidate(
        parsed,
        v1,
        config=config,
        backend_calls=calls,
        verify=verify,
    )
    v1_state = construct_fixed_assignment(parsed, v1)
    v1_checked = official_check(
        ref.prob_info,
        serialize_non_interlock(v1_state.placements.values()),
    )
    selected_state = construct_fixed_assignment(parsed, selection.assignment)
    selected_checked = official_check(
        ref.prob_info,
        serialize_non_interlock(selected_state.placements.values()),
    )
    wall_seconds = time.monotonic() - started
    selected_membership = tuple(
        sorted(
            (placement.block_id, placement.bay_id)
            for placement in selected_state.placements.values()
        )
    )
    planned_membership = tuple(
        sorted(
            (block_id, item.bay_id)
            for block_id, item in selection.assignment.assignments.items()
        )
    )
    tolerance = 1e-9 * max(1.0, abs(float(v1_checked.objective or 0.0)))
    never_worse = (
        v1_checked.objective is not None
        and selected_checked.objective is not None
        and selected_checked.objective <= v1_checked.objective + tolerance
    )
    z2_nonregression = (
        v1_checked.obj2 is not None
        and selected_checked.obj2 is not None
        and selected_checked.obj2
        <= v1_checked.obj2 + 1e-9 * max(1.0, abs(v1_checked.obj2))
    )
    passed = (
        v1_checked.feasible
        and v1_checked.stage == 5
        and selected_checked.feasible
        and selected_checked.stage == 5
        and selected_membership == planned_membership
        and never_worse
        and z2_nonregression
        and selection.fallback_reason is not None
        and len(selection.attempts) == 2
        and (fault != "both" or selection.backend == "greedy")
        and wall_seconds <= timelimit + 0.25
    )
    return {
        "record_id": _case_record_id(ref.instance_id, timelimit, seed, fault),
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
        "solver": "assignment-fallback-stress",
        "timelimit": timelimit,
        "seed": seed,
        "features": dict(sorted(features.items())),
        "fault": fault,
        "fault_applied": fault,
        "wall_seconds": wall_seconds,
        "subprocess_exit": 0,
        "signal": None,
        "checker": checker_payload(selected_checked),
        "prior_checker": checker_payload(v1_checked),
        "selected_backend": selection.backend,
        "backend_attempts": [asdict(attempt) for attempt in selection.attempts],
        "fallback_tier": selection.backend,
        "fallback_reason": selection.fallback_reason,
        "assignment_preserved": selected_membership == planned_membership,
        "never_worse": never_worse,
        "z2_nonregression": z2_nonregression,
        "incumbent_verification_count": 1 + sum(
            attempt.accepted for attempt in selection.attempts
        ),
        "unverified_return_count": 0,
        "timeout": False,
        "crash": False,
        "exception": None,
    }


def construct_fixed_assignment(
    instance: ProblemInstance,
    assignment: Any,
) -> SolutionState:
    """Construct a checker-safe proof state without changing proposed bays."""

    expected = set(range(len(instance.blocks)))
    order = tuple(assignment.order)
    assignments = assignment.assignments
    if len(order) != len(expected) or set(order) != expected:
        raise ValueError("fixed-assignment order must contain every block exactly once")
    if set(assignments) != expected:
        raise ValueError("fixed assignment must contain every block exactly once")

    state = SolutionState(instance)
    bay_available = [0 for _ in instance.bays]
    for block_id in order:
        proposed = assignments[block_id]
        bay_id = proposed.bay_id
        orient_idx = proposed.orient_idx
        if (
            isinstance(bay_id, bool)
            or not isinstance(bay_id, int)
            or not 0 <= bay_id < len(instance.bays)
        ):
            raise ValueError(f"invalid proposed bay for block {block_id}: {bay_id!r}")
        if (
            isinstance(orient_idx, bool)
            or not isinstance(orient_idx, int)
            or not 0 <= orient_idx < len(instance.blocks[block_id].orientations)
        ):
            raise ValueError(
                f"invalid proposed orientation for block {block_id}: {orient_idx!r}"
            )
        ranges = instance.blocks[block_id].orientations[
            orient_idx
        ].integer_position_ranges(instance.bays[bay_id])
        if ranges is None:
            raise ValueError(
                f"proposed assignment does not fit block {block_id} in bay {bay_id}"
            )
        block = instance.blocks[block_id]
        entry = max(block.release_time, bay_available[bay_id])
        placement = Placement(
            block_id=block_id,
            bay_id=bay_id,
            x=ranges[0].start,
            y=ranges[1].start,
            orient_idx=orient_idx,
            entry=entry,
            exit=entry + block.dwell,
        )
        state.place(placement)
        bay_available[bay_id] = placement.exit
    state.assert_invariants()
    return state


def _relative_error(left: float | None, right: float | None) -> float | None:
    if left is None or right is None:
        return None
    left_value = float(left)
    right_value = float(right)
    return abs(left_value - right_value) / max(
        1.0,
        abs(left_value),
        abs(right_value),
    )


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


def run_s3_operator_case(
    ref: InstanceRef,
    *,
    selector: str,
    timelimit: float,
    seed: int,
    features: Mapping[str, str],
) -> dict[str, Any]:
    """Exercise every S3-02 operator on checker-feasible T0 copies."""

    provenance = repository_provenance()
    identity = record_identity(
        commit=provenance["commit"],
        dirty_diff_hash=provenance["dirty_diff_hash"],
        instance_sha=ref.sha256,
        solver="s3-intra-bay-operators",
        timelimit=timelimit,
        seed=seed,
        features=features,
    )
    started = time.monotonic()
    parsed = ProblemInstance.parse(ref.prob_info)
    base = build_t0(parsed)
    base_token = base.capture_undo_token()
    base_check = official_check(
        ref.prob_info, serialize_non_interlock(base.placements.values())
    )
    registry = OperatorRegistry()
    q_rng = Generator(PCG64(seed))
    remove_count = sample_destroy_count(
        len(base.placements),
        q_rng,
        min_fraction=DEFAULT_CONFIG.alns_destroy_min_fraction,
        max_fraction=DEFAULT_CONFIG.alns_destroy_max_fraction,
        cap_fraction=DEFAULT_CONFIG.alns_destroy_cap_fraction,
    )
    pairs = [
        (destroy_name, "r3") for destroy_name in registry.destroy_names
    ] + [("d1", repair_name) for repair_name in registry.repair_names]
    candidate_rows: list[dict[str, Any]] = []
    for attempt_index, (destroy_name, repair_name) in enumerate(pairs):
        state = _copy_solution_state(base)
        before = state.capture_undo_token()
        rng = Generator(PCG64(seed + attempt_index))
        result = registry.attempt(
            state,
            rng,
            destroy_name=destroy_name,
            repair_name=repair_name,
            remove_count=remove_count,
            commit=True,
        )
        checked = official_check(
            ref.prob_info, serialize_non_interlock(state.placements.values())
        )
        assignment_preserved = (
            before.bay_members == state.bay_members
            and before.bay_loads == state.objective_diagnostics.bay_loads
            and before.z2 == state.z2
            and before.z3 == state.z3
        )
        candidate_rows.append(
            {
                "destroy": destroy_name,
                "repair": repair_name,
                "committed": result.committed,
                "reason": result.reason,
                "source_bay": result.source_bay,
                "removed_block_ids": list(result.removed_block_ids),
                "assignment_preserved": assignment_preserved,
                "checker_feasible": checked.feasible,
                "checker_stage": checked.stage,
                "objective": checked.objective,
            }
        )

    # A rejected candidate must restore both state and PCG64 exactly.
    rejected_state = _copy_solution_state(base)
    rejected_before = rejected_state.capture_undo_token()
    rejected_rng = Generator(PCG64(seed + len(pairs)))
    rejected_rng_before = deepcopy(rejected_rng.bit_generator.state)
    rejected = registry.attempt(
        rejected_state,
        rejected_rng,
        destroy_name="d1",
        repair_name="r3",
        remove_count=remove_count,
        commit=False,
    )
    restored_check = official_check(
        ref.prob_info,
        serialize_non_interlock(rejected_state.placements.values()),
    )
    rollback_exact = (
        not rejected.committed
        and rejected.reason == "rejected"
        and rejected_before == rejected_state.capture_undo_token()
        and rejected_rng_before == rejected_rng.bit_generator.state
        and restored_check.feasible
        and restored_check.stage == 5
    )
    metrics = {name: asdict(registry.metrics[name]) for name in registry.metrics}
    applicable = {
        name: metrics[name]["attempts"] > 0 for name in registry.metrics
    }
    candidates_passed = all(
        row["committed"]
        and row["assignment_preserved"]
        and row["checker_feasible"]
        and row["checker_stage"] == 5
        for row in candidate_rows
    )
    operators_counted = all(
        metrics[name]["attempts"] > 0 and metrics[name]["successes"] > 0
        for name in metrics
    )
    wall_seconds = time.monotonic() - started
    passed = (
        base_check.feasible
        and base_check.stage == 5
        and candidates_passed
        and operators_counted
        and rollback_exact
        and wall_seconds <= timelimit + 0.25
    )
    return {
        "record_id": _case_record_id(ref.instance_id, timelimit, seed, "operators"),
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
        "solver": "s3-intra-bay-operators",
        "timelimit": timelimit,
        "seed": seed,
        "features": dict(sorted(features.items())),
        "wall_seconds": wall_seconds,
        "subprocess_exit": 0,
        "signal": None,
        "checker": checker_payload(base_check),
        "stage_timing": {"operators_and_full_checks_seconds": wall_seconds},
        "remove_count": remove_count,
        "destroy_fraction": remove_count / max(1, len(parsed.blocks)),
        "operator_metrics": metrics,
        "operator_applicability": applicable,
        "candidate_rows": candidate_rows,
        "successful_candidate_count": sum(row["committed"] for row in candidate_rows),
        "full_check_count": len(candidate_rows) + 2,
        "assignment_mismatch_count": sum(
            not row["assignment_preserved"] for row in candidate_rows
        ),
        "checker_failure_count": sum(
            not row["checker_feasible"] or row["checker_stage"] != 5
            for row in candidate_rows
        ),
        "rollback_exact": rollback_exact,
        "base_token_unchanged": base_token == base.capture_undo_token(),
        "timeout": wall_seconds > timelimit + 0.25,
        "crash": False,
        "exception": None,
        "incumbent_verification_count": len(candidate_rows) + 2,
        "unverified_return_count": 0,
        "fallback_tier": None,
        "fallback_reason": None,
    }


def _copy_solution_state(state: SolutionState) -> SolutionState:
    copied = SolutionState(
        state.instance,
        geom=state.geom,
        shape_catalog=state.shape_catalog,
    )
    for placement in state.placements.values():
        copied.place(placement)
    copied.assert_invariants()
    return copied


def run_s3_control_case(
    ref: InstanceRef,
    *,
    selector: str,
    timelimit: float,
    seed: int,
    acceptor_name: str,
    adaptive: bool,
    dirty_minimum: int,
    dirty_fraction: float,
    features: Mapping[str, str],
    run_label: str = "forward",
) -> dict[str, Any]:
    """Run a bounded S3-04 component search with guarded S2 retiming."""
    provenance = repository_provenance()
    control_features = {
        **features,
        "acceptor": acceptor_name,
        "adaptive": str(adaptive).lower(),
        "dirty_minimum": str(dirty_minimum),
        "dirty_fraction": str(dirty_fraction),
        "order": run_label,
    }
    identity = record_identity(
        commit=provenance["commit"],
        dirty_diff_hash=provenance["dirty_diff_hash"],
        instance_sha=ref.sha256,
        solver="s3-retimed-controls",
        timelimit=timelimit,
        seed=seed,
        features=control_features,
    )
    started = time.monotonic()
    parsed = ProblemInstance.parse(ref.prob_info)
    state = build_t0(parsed)
    before = state.capture_undo_token()
    incumbent = VerifiedIncumbent(parsed)
    incumbent.register_initial(state)
    initial_objective = incumbent.checker_result.objective
    rng = Generator(PCG64(seed))
    if acceptor_name == "strict":
        acceptor = StrictAcceptor()
    elif acceptor_name == "rrt":
        acceptor = RRT_Acceptor(initial_deviation=0.03)
    elif acceptor_name == "sa":
        acceptor = SAAcceptor(rng)
        scale = max(1.0, abs(float(initial_objective or 1.0)))
        acceptor.calibrate((0.0025 * scale, 0.005 * scale, 0.01 * scale))
    else:
        raise ValueError(f"unsupported S3 acceptor {acceptor_name!r}")
    registry = OperatorRegistry()
    destroy_weights = OperatorWeights(
        registry.destroy_names,
        adaptive=adaptive,
        segment_length=8,
    )
    repair_weights = OperatorWeights(
        registry.repair_names,
        adaptive=adaptive,
        segment_length=8,
    )
    trigger = RetimeTrigger(
        min_dirty=dirty_minimum,
        dirty_fraction=dirty_fraction,
        min_interval_fraction=DEFAULT_CONFIG.alns_retime_interval_fraction,
    )
    budget = Budget(timelimit, reserve=0.0)
    backend_retime_calls = 0

    def guarded_retime(
        current: SolutionState,
        bay_id: int,
        active_budget: Budget | None,
        call_timebox: float,
    ) -> SolutionState | None:
        nonlocal backend_retime_calls
        if active_budget is None:
            raise ValueError("S3 control retime requires a budget")
        if backend_retime_calls >= 1:
            return None
        backend_retime_calls += 1
        outcome = retime_bay(
            current,
            bay_id,
            "gurobi",
            retime_gurobi,
            budget=active_budget,
            seed=seed,
            threads=1,
            call_timebox_cap=call_timebox,
        )
        return outcome.candidate

    search_started = time.monotonic()
    wall_policy = RetimeWallPolicy(
        started_at=search_started,
        wall_fraction_cap=DEFAULT_CONFIG.alns_retime_wall_fraction_cap,
        solve_timebox_seconds=0.02,
    )
    result = run_alns(
        state,
        incumbent,
        rng,
        max_iterations=20,
        registry=registry,
        acceptor=acceptor,
        safety_sample_interval=8,
        budget=budget,
        destroy_weights=destroy_weights,
        repair_weights=repair_weights,
        stagnation=StagnationController(
            reheat_after=6,
            expand_after=12,
            restart_after=18,
        ),
        retime_trigger=trigger,
        retime_callback=guarded_retime,
        retime_wall_policy=wall_policy,
        timelimit_seconds=timelimit,
    )
    search_wall_seconds = time.monotonic() - search_started
    wall_seconds = time.monotonic() - started
    checked = official_check(ref.prob_info, result.solution)
    assignment_preserved = (
        before.bay_members == state.bay_members
        and before.bay_loads == state.objective_diagnostics.bay_loads
        and before.z2 == state.z2
        and before.z3 == state.z3
    )
    retime_fraction = trigger.retime_wall_seconds / max(
        search_wall_seconds,
        1e-12,
    )
    final_objective = checked.objective
    passed = (
        checked.feasible
        and checked.stage == 5
        and final_objective is not None
        and initial_objective is not None
        and final_objective <= initial_objective
        and assignment_preserved
        and retime_fraction <= DEFAULT_CONFIG.alns_retime_wall_fraction_cap
        and result.metrics.checker_failures == 0
    )
    return {
        "record_id": (
            f"{ref.instance_id}|tl={timelimit:g}|seed={seed}|"
            f"acceptor={acceptor_name}|adaptive={str(adaptive).lower()}|"
            f"dirty={dirty_minimum}:{dirty_fraction:g}|order={run_label}"
        ),
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
        "solver": "s3-retimed-controls",
        "timelimit": timelimit,
        "seed": seed,
        "features": dict(sorted(control_features.items())),
        "wall_seconds": wall_seconds,
        "checker": checker_payload(checked),
        "initial_objective": initial_objective,
        "final_objective": final_objective,
        "never_worse": final_objective is not None and initial_objective is not None
        and final_objective <= initial_objective,
        "assignment_preserved": assignment_preserved,
        "metrics": asdict(result.metrics),
        "operator_weights": {
            "destroy": list(destroy_weights.weights),
            "repair": list(repair_weights.weights),
        },
        "retime": {
            "dirty_minimum": dirty_minimum,
            "dirty_fraction": dirty_fraction,
            "attempts": trigger.attempts,
            "backend_calls": backend_retime_calls,
            "improvements": trigger.improvements,
            "failures": trigger.failures,
            "wall_seconds": trigger.retime_wall_seconds,
            "non_retime_wall_seconds": max(
                0.0,
                search_wall_seconds - trigger.retime_wall_seconds,
            ),
            "search_wall_seconds": search_wall_seconds,
            "search_wall_fraction": retime_fraction,
        },
        "incumbent_trace": [asdict(item) for item in result.incumbent_trace],
        "stopped_reason": result.stopped_reason,
        "incumbent_verification_count": incumbent.verification_count,
        "unverified_return_count": 0,
        "timeout": False,
        "crash": False,
        "exception": None,
    }


def run_s3_integrated_case(
    ref: InstanceRef,
    *,
    selector: str,
    timelimit: float,
    seed: int,
    features: Mapping[str, str],
    fault: str | None = None,
) -> dict[str, Any]:
    """Run the checker-verified S2 baseline and integrated S3 entry policy."""
    provenance = repository_provenance()
    run_features = {
        **features,
        "fault": "none" if fault is None else fault,
    }
    identity = record_identity(
        commit=provenance["commit"],
        dirty_diff_hash=provenance["dirty_diff_hash"],
        instance_sha=ref.sha256,
        solver="native-s3-entry",
        timelimit=timelimit,
        seed=seed,
        features=run_features,
    )
    started = time.monotonic()
    telemetry: dict[str, Any] = {}
    solution = solve(
        ref.prob_info,
        timelimit,
        _seed=seed,
        _alns=True,
        _alns_fault=fault,
        _telemetry=telemetry,
    )
    s2_solution = telemetry.get("alns_input_solution", solution)
    s2_checked = official_check(ref.prob_info, s2_solution)
    checked = official_check(ref.prob_info, solution)
    wall_seconds = time.monotonic() - started
    metrics = dict(telemetry.get("alns_metrics", {}))
    trace = list(telemetry.get("alns_incumbent_trace", ()))
    first_epoch = list(telemetry.get("alns_first_epoch_trace", ()))
    trace_monotonic = all(
        float(right["objective"]) < float(left["objective"])
        for left, right in zip(trace, trace[1:])
    )
    assignment_preserved = (
        _solution_membership(solution) == _solution_membership(s2_solution)
        and checked.obj2 == s2_checked.obj2
        and checked.obj3 == s2_checked.obj3
    )
    never_worse = (
        checked.objective is not None
        and s2_checked.objective is not None
        and checked.objective <= s2_checked.objective
    )
    fault_applied = telemetry.get("alns_fault_applied")
    alns_seconds = float(telemetry.get("alns_seconds", 0.0))
    alns_retime_wall_seconds = float(
        telemetry.get("alns_retime_wall_seconds", 0.0)
    )
    alns_non_retime_wall_seconds = float(
        telemetry.get(
            "alns_non_retime_wall_seconds",
            max(0.0, alns_seconds - alns_retime_wall_seconds),
        )
    )
    retime_wall_fraction = alns_retime_wall_seconds / max(alns_seconds, 1e-12)
    passed = (
        checked.feasible
        and checked.stage == 5
        and s2_checked.feasible
        and never_worse
        and assignment_preserved
        and trace_monotonic
        and bool(telemetry.get("alns_prefix_consistent"))
        and int(metrics.get("checker_failures", 0)) == 0
        and wall_seconds <= timelimit + 0.25
        and retime_wall_fraction <= DEFAULT_CONFIG.alns_retime_wall_fraction_cap
        and (fault is None or fault_applied == fault)
    )
    return {
        "record_id": (
            f"{ref.instance_id}|tl={timelimit:g}|seed={seed}|"
            f"fault={'none' if fault is None else fault}"
        ),
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
        "solver": "native-s3-entry",
        "timelimit": timelimit,
        "seed": seed,
        "features": dict(sorted(run_features.items())),
        "wall_seconds": wall_seconds,
        "alns_seconds": alns_seconds,
        "alns_retime_wall_seconds": alns_retime_wall_seconds,
        "alns_non_retime_wall_seconds": alns_non_retime_wall_seconds,
        "retime_wall_fraction": retime_wall_fraction,
        "checker": checker_payload(checked),
        "s2_checker": checker_payload(s2_checked),
        "s2_objective": s2_checked.objective,
        "final_objective": checked.objective,
        "never_worse": never_worse,
        "assignment_preserved": assignment_preserved,
        "metrics": metrics,
        "operator_metrics": telemetry.get("alns_operator_metrics", {}),
        "incumbent_trace": trace,
        "trace_monotonic": trace_monotonic,
        "first_epoch_trace": first_epoch,
        "first_epoch_sha256": hashlib.sha256(
            json.dumps(first_epoch, separators=(",", ":")).encode()
        ).hexdigest(),
        "prefix_consistent": telemetry.get("alns_prefix_consistent", False),
        "epoch_count": telemetry.get("alns_epoch_count", 0),
        "fault": fault,
        "fault_applied": fault_applied,
        "fallback_reason": telemetry.get("fallback_reason"),
        "incumbent_verification_count": telemetry.get(
            "incumbent_verification_count", 0
        ),
        "unverified_return_count": 0,
        "timeout": False,
        "crash": False,
        "exception": None,
        "_epoch_solutions": telemetry.get("alns_epoch_solutions", []),
    }


def s3_prefix_record(
    ref: InstanceRef,
    long_record: Mapping[str, Any],
    *,
    timelimit: float = 60.0,
) -> dict[str, Any]:
    """Materialize the verified first epoch from one longer S3 execution."""
    snapshots = tuple(long_record.get("_epoch_solutions", ()))
    if not snapshots:
        raise ValueError("long S3 record has no epoch incumbent snapshot")
    solution = snapshots[0]
    checked = official_check(ref.prob_info, solution)
    s2_objective = long_record.get("s2_objective")
    never_worse = (
        checked.objective is not None
        and s2_objective is not None
        and checked.objective <= float(s2_objective)
    )
    trace = tuple(
        item
        for item in long_record.get("incumbent_trace", ())
        if int(item.get("iteration", 0)) <= 300
    )
    prefix = {
        key: deepcopy(value)
        for key, value in long_record.items()
        if key != "_epoch_solutions"
    }
    prefix.update(
        record_id=f"{ref.instance_id}|tl={timelimit:g}|seed={long_record['seed']}|fault=none",
        identity=None,
        timelimit=timelimit,
        checker=checker_payload(checked),
        final_objective=checked.objective,
        never_worse=never_worse,
        epoch_count=1,
        incumbent_trace=list(trace),
        wall_seconds=min(float(long_record.get("wall_seconds", 0.0)), timelimit),
    )
    prefix_metrics = dict(prefix.get("metrics", {}))
    prefix_metrics["iterations"] = min(300, int(prefix_metrics.get("iterations", 0)))
    prefix["metrics"] = prefix_metrics
    prefix["status"] = (
        "passed"
        if checked.feasible
        and checked.stage == 5
        and never_worse
        and prefix.get("assignment_preserved") is True
        and prefix.get("trace_monotonic") is True
        else "checker_failed"
    )
    return prefix


def _solution_membership(solution: Mapping[str, Any]) -> tuple[tuple[int, int], ...]:
    return tuple(
        sorted(
            (int(operation["block_id"]), int(operation["bay_id"]))
            for operations in solution.get("operations", {}).values()
            for operation in operations
            if operation.get("type") == "ENTRY"
        )
    )


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


def run_retime_fault_case(
    ref: InstanceRef,
    *,
    selector: str,
    timelimit: float,
    seed: int,
    features: Mapping[str, str],
    fault: str,
) -> dict[str, Any]:
    """Exercise S2-04 backend fallback without risking the S1 incumbent."""

    provenance = repository_provenance()
    identity = record_identity(
        commit=provenance["commit"],
        dirty_diff_hash=provenance["dirty_diff_hash"],
        instance_sha=ref.sha256,
        solver="guarded-retime-fault-stress",
        timelimit=timelimit,
        seed=seed,
        features={**features, "fault_case": fault},
    )
    started = time.monotonic()
    parsed = ProblemInstance.parse(ref.prob_info)
    t0_state = build_t0(parsed)
    incumbent = VerifiedIncumbent(parsed)
    incumbent.register_initial(t0_state)
    current_state = t0_state
    constructor_error = None
    try:
        remaining = max(0.0, timelimit - (time.monotonic() - started))
        reserve = min(
            DEFAULT_CONFIG.constructor_return_reserve_seconds,
            max(remaining - 0.05, 0.0),
        )
        constructed = construct_multistart(
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
        if constructed.metrics.incumbent_updated:
            current_state = constructed.state
    except Exception as exc:
        constructor_error = f"{type(exc).__name__}: {exc}"

    before_bytes = json.dumps(
        incumbent.solution,
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
    before_sha = hashlib.sha256(before_bytes).hexdigest()
    before_z1 = incumbent.checker_result.obj1

    def empty_gurobi(status: str, reason: str) -> ExactResult:
        return ExactResult(
            backend="gurobi",
            status=status,
            solution=None,
            objective=None,
            bound=None,
            build_s=0.0,
            solve_s=0.0,
            first_solution_s=None,
            reason=reason,
        )

    def faulted_gurobi(request: RetimeRequest, _timebox: float) -> ExactResult:
        if fault == "gurobi_license":
            return empty_gurobi("unavailable", "injected gurobi license fault")
        if fault == "gurobi_extract":
            return ExactResult(
                backend="gurobi",
                status="feasible",
                solution=(),
                objective=0.0,
                bound=None,
                build_s=0.0,
                solve_s=0.0,
                first_solution_s=0.0,
                reason="injected gurobi extraction fault",
            )
        stage = "import" if fault == "gurobi_import" else "optimize"
        raise RuntimeError(f"injected gurobi {stage} fault")

    def maybe_faulted_cpsat(request: RetimeRequest, timebox: float) -> ExactResult:
        if fault == "both":
            raise RuntimeError("injected cpsat fault in both-backend case")
        return retime_cpsat(request, timebox)

    if fault == "both":
        def both_faulted_gurobi(
            _request: RetimeRequest, _timebox: float
        ) -> ExactResult:
            raise RuntimeError("injected gurobi fault in both-backend case")

        gurobi_call = both_faulted_gurobi
    else:
        gurobi_call = faulted_gurobi

    remaining = max(0.0, timelimit - (time.monotonic() - started))
    exact_budget = Budget(remaining, reserve=0.0)
    outcome = retime_sweep(
        current_state,
        incumbent,
        {"gurobi": gurobi_call, "cpsat": maybe_faulted_cpsat},
        available_backends=("gurobi", "cpsat"),
        budget=exact_budget,
        first_sweep_budget=exact_budget.remaining,
        seed=seed,
    )
    after_bytes = json.dumps(
        incumbent.solution,
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
    after_sha = hashlib.sha256(after_bytes).hexdigest()
    after_z1 = incumbent.checker_result.obj1
    checked = official_check(ref.prob_info, incumbent.solution)
    wall_seconds = time.monotonic() - started
    unchanged = before_sha == after_sha
    never_worse = (
        before_z1 is not None
        and after_z1 is not None
        and float(after_z1) <= float(before_z1) + 1e-9
    )
    expected_selection = (
        outcome.pilot.backend == "gurobi"
        if fault == "both"
        else outcome.pilot.backend == "cpsat"
    )
    passed = (
        constructor_error is None
        and checked.feasible
        and checked.stage == 5
        and never_worse
        and expected_selection
        and (fault != "both" or unchanged)
        and wall_seconds <= timelimit + 0.25
    )
    return {
        "record_id": f"{ref.instance_id}|tl={timelimit:g}|seed={seed}|fault={fault}",
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
        "solver": "guarded-retime-fault-stress",
        "timelimit": timelimit,
        "seed": seed,
        "features": dict(sorted(features.items())),
        "backend_fault": fault,
        "selected_backend": outcome.pilot.backend,
        "pilot": asdict(outcome.pilot),
        "attempts": [asdict(item) for item in outcome.attempts],
        "accepted_bays": outcome.accepted_bays,
        "wall_seconds": wall_seconds,
        "checker": checker_payload(checked),
        "before_z1": before_z1,
        "after_z1": after_z1,
        "never_worse": never_worse,
        "constructor_error": constructor_error,
        "incumbent_verification_count": incumbent.verification_count,
        "unverified_return_count": 0,
        "output_sha256_before_retime": before_sha,
        "output_sha256_after_retime": after_sha,
        "operations_byte_identical": unchanged,
        "timeout": False,
        "crash": False,
        "exception": None,
        "fallback_tier": "constructor" if unchanged else "retime",
        "fallback_reason": fault,
    }


def run_s2_entry_case(
    ref: InstanceRef,
    *,
    selector: str,
    timelimit: float,
    seed: int,
    features: Mapping[str, str],
    exact_retime: bool,
    timebox: float,
    pilot_budget: float,
    run_label: str,
) -> dict[str, Any]:
    """Run the S2 entry pipeline and expose checker/backend timing evidence."""

    provenance = repository_provenance()
    effective_features = {
        **features,
        "exact_retime": str(exact_retime).lower(),
        "timebox": f"{timebox:g}",
        "pilot_budget": f"{pilot_budget:g}",
        "run": run_label,
    }
    identity = record_identity(
        commit=provenance["commit"],
        dirty_diff_hash=provenance["dirty_diff_hash"],
        instance_sha=ref.sha256,
        solver="native-exact-retime-entry" if exact_retime else "native-constructor-entry",
        timelimit=timelimit,
        seed=seed,
        features=effective_features,
    )
    telemetry: dict[str, Any] = {}
    started = time.monotonic()
    solution = solve(
        ref.prob_info,
        timelimit,
        _constructor=True,
        _retime=exact_retime,
        _seed=seed,
        _retime_timebox=timebox,
        _retime_pilot=pilot_budget,
        _telemetry=telemetry,
    )
    wall_seconds = time.monotonic() - started
    checked = official_check(ref.prob_info, solution)
    output_sha = hashlib.sha256(
        json.dumps(solution, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    attempts = tuple(telemetry.get("retime_attempts", ()))
    pilot_payload = telemetry.get("retime_pilot") or {}
    pilot_trials = tuple(pilot_payload.get("trials", ()))
    exact_calls = attempts + pilot_trials
    max_exact_call_seconds = max(
        (
            float(item.get("build_s", 0.0)) + float(item.get("solve_s", 0.0))
            for item in exact_calls
        ),
        default=0.0,
    )
    max_requested_timebox = max(
        (float(item.get("timebox", 0.0)) for item in exact_calls),
        default=0.0,
    )
    before_z1 = telemetry.get("retime_before_z1", checked.obj1)
    after_z1 = telemetry.get("retime_after_z1", checked.obj1)
    never_worse = (
        before_z1 is not None
        and after_z1 is not None
        and float(after_z1) <= float(before_z1) + 1e-9
    )
    bounded = (
        max_exact_call_seconds <= max_requested_timebox + 0.25
        if exact_calls
        else True
    )
    passed = (
        checked.feasible
        and checked.stage == 5
        and never_worse
        and bounded
        and wall_seconds <= timelimit + 0.25
    )
    accepted = int(telemetry.get("retime_accepted_bays", 0)) + int(
        telemetry.get("retime_final_accepted_bays", 0)
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
        "solver": "native-exact-retime-entry" if exact_retime else "native-constructor-entry",
        "timelimit": timelimit,
        "seed": seed,
        "features": dict(sorted(effective_features.items())),
        "wall_seconds": wall_seconds,
        "checker": checker_payload(checked),
        "stage_timing": {
            "t0_and_verify_seconds": telemetry.get("t0_and_verify_seconds", 0.0),
            "constructor_seconds": telemetry.get("constructor_seconds", 0.0),
            "retime_seconds": telemetry.get("retime_seconds", 0.0),
        },
        "constructor_updated": telemetry.get("constructor_updated", False),
        "selected_backend": telemetry.get("retime_backend"),
        "pilot": telemetry.get("retime_pilot"),
        "attempts": list(attempts),
        "accepted_bays": accepted,
        "before_z1": before_z1,
        "after_z1": after_z1,
        "z1_gain": (
            float(before_z1) - float(after_z1)
            if before_z1 is not None and after_z1 is not None
            else 0.0
        ),
        "final_objective": checked.objective,
        "never_worse": never_worse,
        "max_exact_call_seconds": max_exact_call_seconds,
        "max_requested_timebox": max_requested_timebox,
        "exact_calls_bounded": bounded,
        "output_sha256": output_sha,
        "timeout": wall_seconds > timelimit + 0.25,
        "crash": False,
        "exception": telemetry.get("fallback_reason"),
        "incumbent_verification_count": int(
            telemetry.get("incumbent_verification_count", 0)
        ),
        "unverified_return_count": 0,
        "fallback_tier": "retime" if accepted else "constructor",
        "fallback_reason": telemetry.get("fallback_reason") or (
            "no_strict_retime_improvement" if exact_retime and accepted == 0 else None
        ),
    }


def run_s2_matrix_records(
    ref: InstanceRef,
    *,
    selector: str,
    timelimit: float,
    seed: int,
    timeboxes: tuple[float, ...],
    pilot_budgets: tuple[float, ...],
    features: Mapping[str, str],
) -> tuple[dict[str, Any], ...]:
    """Evaluate the preregistered S2 matrix from one verified S1 state."""

    provenance = repository_provenance()
    construction_started = time.monotonic()
    parsed = ProblemInstance.parse(ref.prob_info)
    base_incumbent = VerifiedIncumbent(parsed)
    t0_state = build_t0(parsed)
    base_incumbent.register_initial(t0_state)
    remaining = max(0.0, timelimit - (time.monotonic() - construction_started))
    reserve = min(
        DEFAULT_CONFIG.constructor_return_reserve_seconds,
        max(remaining - 0.05, 0.0),
    )
    constructed = construct_multistart(
        parsed,
        base_incumbent,
        Budget(remaining, reserve=reserve),
        seed=seed,
        profiles=DEFAULT_CONFIG.constructor_profiles,
        biased_variants=False,
        calibrated_entry=True,
        time_cap=DEFAULT_CONFIG.constructor_time_cap,
        anchor_cap=DEFAULT_CONFIG.constructor_anchor_cap,
    )
    state = constructed.state if constructed.metrics.incumbent_updated else t0_state
    construction_seconds = time.monotonic() - construction_started
    matrix = tuple(itertools.product(timeboxes, pilot_budgets))
    records: list[dict[str, Any]] = []
    for ordering, candidates in (("forward", matrix), ("reverse", tuple(reversed(matrix)))):
        for timebox, pilot_budget in candidates:
            candidate_started = time.monotonic()
            incumbent = VerifiedIncumbent(parsed)
            incumbent.register_initial(state)
            before_z1 = incumbent.checker_result.obj1
            exact_budget = Budget(
                max(0.0, timelimit - construction_seconds), reserve=0.0
            )
            outcome = retime_sweep(
                state,
                incumbent,
                {"gurobi": retime_gurobi, "cpsat": retime_cpsat},
                available_backends=("gurobi", "cpsat"),
                budget=exact_budget,
                first_sweep_budget=exact_budget.remaining,
                seed=seed,
                threads=DEFAULT_CONFIG.retime_threads,
                call_timebox_cap=timebox,
                pilot_budget_cap=pilot_budget,
            )
            checked = official_check(ref.prob_info, incumbent.solution)
            after_z1 = checked.obj1
            attempts = tuple(
                asdict(item)
                for item in outcome.attempts
            )
            pilot = asdict(outcome.pilot)
            exact_calls = attempts + tuple(pilot.get("trials", ()))
            max_call = max(
                (
                    float(item.get("build_s", 0.0))
                    + float(item.get("solve_s", 0.0))
                    for item in exact_calls
                ),
                default=0.0,
            )
            max_requested = max(
                (float(item.get("timebox", 0.0)) for item in exact_calls),
                default=0.0,
            )
            wall_seconds = construction_seconds + time.monotonic() - candidate_started
            never_worse = (
                before_z1 is not None
                and after_z1 is not None
                and float(after_z1) <= float(before_z1) + 1e-9
            )
            bounded = not exact_calls or max_call <= max_requested + 0.25
            passed = (
                checked.feasible
                and checked.stage == 5
                and never_worse
                and bounded
                and wall_seconds <= timelimit + 0.25
            )
            run_label = f"{ordering}-tb{timebox:g}-pb{pilot_budget:g}"
            effective_features = {
                **features,
                "timebox": f"{timebox:g}",
                "pilot_budget": f"{pilot_budget:g}",
                "ordering": ordering,
            }
            records.append({
                "record_id": (
                    f"{ref.instance_id}|tl={timelimit:g}|seed={seed}|run={run_label}"
                ),
                "identity": record_identity(
                    commit=provenance["commit"],
                    dirty_diff_hash=provenance["dirty_diff_hash"],
                    instance_sha=ref.sha256,
                    solver="native-exact-retime-matrix",
                    timelimit=timelimit,
                    seed=seed,
                    features=effective_features,
                ),
                "complete": True,
                "status": "passed" if passed else "checker_failed",
                "timestamp": datetime.now().astimezone().isoformat(),
                **provenance,
                "instance_id": ref.instance_id,
                "instance_path": str(ref.path),
                "instance_sha": ref.sha256,
                "selector": selector,
                "solver": "native-exact-retime-matrix",
                "timelimit": timelimit,
                "seed": seed,
                "features": dict(sorted(effective_features.items())),
                "ordering": ordering,
                "timebox": timebox,
                "pilot_budget": pilot_budget,
                "construction_seconds": construction_seconds,
                "wall_seconds": wall_seconds,
                "checker": checker_payload(checked),
                "selected_backend": outcome.pilot.backend,
                "pilot": pilot,
                "attempts": attempts,
                "accepted_bays": outcome.accepted_bays,
                "before_z1": before_z1,
                "after_z1": after_z1,
                "z1_gain": (
                    float(before_z1) - float(after_z1)
                    if before_z1 is not None and after_z1 is not None
                    else 0.0
                ),
                "never_worse": never_worse,
                "max_exact_call_seconds": max_call,
                "max_requested_timebox": max_requested,
                "exact_calls_bounded": bounded,
                "incumbent_verification_count": incumbent.verification_count,
                "unverified_return_count": 0,
                "timeout": wall_seconds > timelimit + 0.25,
                "crash": False,
                "exception": None,
                "fallback_tier": (
                    "retime" if outcome.accepted_bays > 0 else "constructor"
                ),
                "fallback_reason": (
                    None if outcome.accepted_bays > 0 else "no_strict_retime_improvement"
                ),
            })
    return tuple(records)


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
