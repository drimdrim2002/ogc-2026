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
    from baseline.solver.construct import escalate_insert
    from baseline.solver.incumbent import VerifiedIncumbent
    from baseline.solver.instance import ProblemInstance
    from baseline.solver.serialize import serialize_non_interlock
    from baseline.solver.state import Placement, SolutionState
    from baseline.solver.trivial import build_t0
except ModuleNotFoundError:
    from solver.assign import AssignmentV1
    from solver.checker_adapter import official_check
    from solver.construct import escalate_insert
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
