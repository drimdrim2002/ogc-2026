"""Repository provenance and checker-authoritative T0 execution records."""

from __future__ import annotations

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
    from baseline.solver.checker_adapter import official_check
    from baseline.solver.incumbent import VerifiedIncumbent
    from baseline.solver.instance import ProblemInstance
    from baseline.solver.serialize import serialize_non_interlock
    from baseline.solver.state import Placement, SolutionState
    from baseline.solver.trivial import build_t0
except ModuleNotFoundError:
    from solver.assign import AssignmentV1
    from solver.checker_adapter import official_check
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
