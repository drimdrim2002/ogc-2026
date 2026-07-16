"""Generate a deterministic Python repair fixture and golden trace for pybind work.

This harness does not change production solver behavior.  It observes one
quota-bounded call to the existing heuristic repair through temporary wrappers
and records the candidate, exact-relation, score, top-three, and commit order.
"""

from __future__ import annotations

import argparse
import copy
import dataclasses
import hashlib
import json
import random
import subprocess
import sys
from collections import defaultdict, deque
from pathlib import Path
from typing import Any
from unittest.mock import patch


REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import baseline.solver.construct as construct_module  # noqa: E402
import baseline.solver.neighborhoods as neighborhoods_module  # noqa: E402
from baseline.solver.budget import Budget  # noqa: E402
from baseline.solver.construct import CandidateScore, ConstructorConfig  # noqa: E402
from baseline.solver.fallback import build_safe_candidate  # noqa: E402
from baseline.solver.geometry import GeometryKernel  # noqa: E402
from baseline.solver.instance import parse_instance  # noqa: E402
from baseline.solver.neighborhoods import (  # noqa: E402
    NeighborhoodContext,
    TardyChainDestroy,
    destroy_snapshot,
)
from baseline.solver.serialize import serialize  # noqa: E402
from baseline.solver.state import (  # noqa: E402
    IndexedSolutionState,
    Placement,
    SolutionSnapshot,
)
from baseline.utils import check_feasibility  # noqa: E402
from experiments.ogc_sage.benchmark_ogc_sage import (  # noqa: E402
    canonical_json,
    environment_versions,
    sha256_file,
    validate_dataset,
)


ROW_SCHEMA = (
    "block_id",
    "bay_id",
    "orient_idx",
    "x",
    "y",
    "entry",
    "exit",
)


def _git(*args: str) -> str:
    completed = subprocess.run(
        ("git", *args),
        cwd=REPO_ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    return completed.stdout.strip()


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _digest(value: Any) -> str:
    return _sha256_bytes(canonical_json(value).encode("utf-8"))


def _solver_source_sha256() -> str:
    rows = "".join(
        f"{sha256_file(path)}  {path.relative_to(REPO_ROOT).as_posix()}\n"
        for path in sorted((REPO_ROOT / "baseline/solver").rglob("*.py"))
    )
    return _sha256_bytes(rows.encode("utf-8"))


def _placement_row(placement: Placement) -> list[int]:
    return [
        placement.block_id,
        placement.bay_id,
        placement.orient_idx,
        placement.x,
        placement.y,
        placement.entry,
        placement.exit,
    ]


def _snapshot_rows(snapshot: SolutionSnapshot) -> list[list[int]]:
    return [_placement_row(item) for item in snapshot.placements]


def _state_rows(state: IndexedSolutionState) -> list[list[int]]:
    return [_placement_row(item) for item in state.placements]


def _float_record(value: float) -> dict[str, Any]:
    number = float(value)
    return {"value": number, "hex": number.hex()}


def _score_record(score: CandidateScore) -> dict[str, Any]:
    return {
        "placement": _placement_row(score.placement),
        "total_delta": _float_record(score.total_delta),
        "tardiness_delta": _float_record(score.tardiness_delta),
        "assignment_delta": _float_record(score.assignment_delta),
        "fragmentation": score.fragmentation,
        "canonical_tie": list(score.canonical_tie),
        "canonical_tie_float_hex": [
            item.hex() if isinstance(item, float) else None
            for item in score.canonical_tie
        ],
        "state_version": score.state_version,
    }


def _objective_record(objective: Any) -> dict[str, Any]:
    return {
        "z1": _float_record(objective.z1),
        "z2": _float_record(objective.z2),
        "z3": _float_record(objective.z3),
        "total": _float_record(objective.total),
    }


class RepairRecorder:
    def __init__(self, kernel: GeometryKernel) -> None:
        self.kernel = kernel
        self.calls: list[dict[str, Any]] = []
        self.commits: list[dict[str, Any]] = []
        self.current_call: dict[str, Any] | None = None
        self.current_commit: dict[str, Any] | None = None
        self.current_phase: str | None = None
        self.active_evaluation_ordinal: int | None = None
        self.pending_ordinals: dict[tuple[int, ...], deque[int]] = defaultdict(deque)

        self.original_generate = neighborhoods_module.generate_insertion_candidates
        self.original_commit = neighborhoods_module.commit_insertion_candidate
        self.original_evaluate = construct_module.evaluate_insert
        self.original_relation = GeometryKernel.relation

    @staticmethod
    def _state_digest(state: IndexedSolutionState) -> str:
        return _digest(_state_rows(state))

    def generate(
        self,
        state: IndexedSolutionState,
        block_id: int,
        kernel: GeometryKernel,
        budget: Budget,
        *,
        current_placement: Placement | None = None,
        config: ConstructorConfig | None = None,
    ) -> tuple[CandidateScore, ...]:
        if self.current_call is not None or self.current_commit is not None:
            raise RuntimeError("nested repair instrumentation is not supported")
        call: dict[str, Any] = {
            "call_ordinal": len(self.calls),
            "repair_round": state.version,
            "block_id": block_id,
            "state_version": state.version,
            "state_rows_sha256": self._state_digest(state),
            "current_placement": (
                None if current_placement is None else _placement_row(current_placement)
            ),
            "enumeration_before_exact": [],
            "evaluation_trace": [],
            "enumeration_after_exact": [],
            "relation_queries": [],
        }
        self.calls.append(call)
        self.current_call = call
        self.current_phase = "generate"
        self.pending_ordinals = defaultdict(deque)

        def placement_factory(*args: Any, **kwargs: Any) -> Placement:
            placement = Placement(*args, **kwargs)
            ordinal = len(call["enumeration_before_exact"])
            row = _placement_row(placement)
            call["enumeration_before_exact"].append(
                {"enumeration_ordinal": ordinal, "row": row}
            )
            self.pending_ordinals[tuple(row)].append(ordinal)
            return placement

        try:
            with patch.object(construct_module, "Placement", placement_factory):
                options = self.original_generate(
                    state,
                    block_id,
                    kernel,
                    budget,
                    current_placement=current_placement,
                    config=config,
                )
            call["top3_return_order"] = [_score_record(item) for item in options]
            call["top3_canonical_order"] = [
                _score_record(item)
                for item in sorted(options, key=lambda item: item.canonical_tie)
            ]
            call["counts"] = {
                "enumerated_before_exact": len(call["enumeration_before_exact"]),
                "evaluated_after_fit": len(call["evaluation_trace"]),
                "accepted_after_exact": len(call["enumeration_after_exact"]),
                "relation_queries": len(call["relation_queries"]),
                "top3": len(options),
            }
            call["trace_sha256"] = _digest(
                {
                    key: call[key]
                    for key in (
                        "enumeration_before_exact",
                        "evaluation_trace",
                        "enumeration_after_exact",
                        "relation_queries",
                        "top3_return_order",
                        "top3_canonical_order",
                    )
                }
            )
            return options
        finally:
            self.pending_ordinals = defaultdict(deque)
            self.current_phase = None
            self.current_call = None

    def evaluate(
        self,
        draft: IndexedSolutionState,
        placement: Placement,
        kernel: GeometryKernel,
        *,
        counters: Any | None = None,
    ) -> CandidateScore | None:
        target = self.current_call if self.current_phase == "generate" else self.current_commit
        if target is None:
            return self.original_evaluate(draft, placement, kernel, counters=counters)

        row = _placement_row(placement)
        pending = self.pending_ordinals.get(tuple(row))
        source_ordinal = pending.popleft() if pending else None
        trace = target.setdefault("evaluation_trace", [])
        event: dict[str, Any] = {
            "evaluation_ordinal": len(trace),
            "enumeration_ordinal": source_ordinal,
            "source": "enumerated" if source_ordinal is not None else "commit_or_rollback",
            "placement": row,
            "relation_query_start": len(target["relation_queries"]),
        }
        trace.append(event)
        self.active_evaluation_ordinal = event["evaluation_ordinal"]
        try:
            score = self.original_evaluate(
                draft, placement, kernel, counters=counters
            )
        finally:
            self.active_evaluation_ordinal = None
        event["relation_query_count"] = (
            len(target["relation_queries"]) - event["relation_query_start"]
        )
        event["accepted"] = score is not None
        event["score"] = None if score is None else _score_record(score)
        if score is not None:
            target.setdefault("enumeration_after_exact", []).append(
                {
                    "accepted_ordinal": len(target["enumeration_after_exact"]),
                    "enumeration_ordinal": source_ordinal,
                    "score": _score_record(score),
                }
            )
        return score

    def relation(
        self,
        kernel: GeometryKernel,
        left: Placement,
        right: Placement,
    ) -> Any:
        cache_before = kernel.cache_info()
        relation = self.original_relation(kernel, left, right)
        cache_after = kernel.cache_info()
        target = self.current_call if self.current_phase == "generate" else self.current_commit
        if kernel is self.kernel and target is not None:
            target["relation_queries"].append(
                {
                    "query_ordinal": len(target["relation_queries"]),
                    "phase": self.current_phase,
                    "evaluation_ordinal": self.active_evaluation_ordinal,
                    "left": _placement_row(left),
                    "right": _placement_row(right),
                    "state": relation.state.value,
                    "g_i_k": relation.g_i_k,
                    "g_k_i": relation.g_k_i,
                    "cache": (
                        "hit"
                        if cache_after.hits > cache_before.hits
                        else "miss"
                        if cache_after.misses > cache_before.misses
                        else "bypass"
                    ),
                }
            )
        return relation

    def commit(
        self,
        state: IndexedSolutionState,
        candidate: CandidateScore,
        kernel: GeometryKernel,
    ) -> bool:
        if self.current_call is not None or self.current_commit is not None:
            raise RuntimeError("nested repair instrumentation is not supported")
        record: dict[str, Any] = {
            "commit_ordinal": len(self.commits),
            "repair_round": state.version,
            "candidate": _score_record(candidate),
            "state_version_before": state.version,
            "state_rows_sha256_before": self._state_digest(state),
            "evaluation_trace": [],
            "enumeration_after_exact": [],
            "relation_queries": [],
        }
        self.commits.append(record)
        self.current_commit = record
        self.current_phase = "commit"
        try:
            committed = self.original_commit(state, candidate, kernel)
            record["committed"] = committed
            record["state_version_after"] = state.version
            record["state_rows_sha256_after"] = self._state_digest(state)
            record["state_rows_after"] = _state_rows(state)
            record["counts"] = {
                "reevaluations": len(record["evaluation_trace"]),
                "relation_queries": len(record["relation_queries"]),
            }
            record["commit_sha256"] = _digest(
                {
                    "candidate": record["candidate"],
                    "committed": committed,
                    "state_version_after": state.version,
                    "state_rows_sha256_after": record["state_rows_sha256_after"],
                }
            )
            return committed
        finally:
            self.current_phase = None
            self.current_commit = None


def generate(
    *,
    output: Path,
    instance_name: str,
    seed: int,
    destroy_size: int,
    candidate_attempt_cap: int,
    budget_guard_seconds: float,
) -> dict[str, Any]:
    instances, dataset_sha256 = validate_dataset()
    if instance_name not in instances:
        raise ValueError(f"unknown official instance {instance_name!r}")
    output.mkdir(parents=True, exist_ok=False)

    instance_path = instances[instance_name]
    raw = json.loads(instance_path.read_text(encoding="utf-8"))
    instance = parse_instance(raw)
    current = build_safe_candidate(instance, Budget.start(1.0))
    current_kernel = GeometryKernel.from_instance(instance)
    current_operations = serialize(current, current_kernel)
    current_checked = check_feasibility(
        copy.deepcopy(raw), copy.deepcopy(current_operations)
    )
    if current_checked.get("stage") != 5:
        raise RuntimeError(f"source snapshot is not Stage 5: {current_checked!r}")

    selector_context = NeighborhoodContext(instance, current_kernel)
    destroyed_ids = TardyChainDestroy().select(
        current,
        destroy_size,
        random.Random(seed),
        selector_context,
    )
    destroyed = destroy_snapshot(current, destroyed_ids)
    config = ConstructorConfig(
        seed=seed,
        max_profiles=1,
        max_candidate_attempts=candidate_attempt_cap,
    )
    retained_state = IndexedSolutionState(
        instance,
        destroyed.retained.placements,
        time_cap=config.time_cap,
        anchor_cap=config.anchor_cap,
        lattice_cap=config.lattice_cap,
    )

    source_identity = {
        "git_branch": _git("branch", "--show-current"),
        "git_head": _git("rev-parse", "HEAD"),
        "solver_source_commit": _git("log", "-1", "--format=%H", "--", "baseline/solver"),
        "solver_source_sha256": _solver_source_sha256(),
    }
    fixture = {
        "schema_version": 1,
        "kind": "pybind_p1_python_repair_fixture",
        "row_schema": list(ROW_SCHEMA),
        "source_identity": source_identity,
        "environment": environment_versions(),
        "dataset_sha256": dataset_sha256,
        "instance": instance_name,
        "instance_path": instance_path.relative_to(REPO_ROOT).as_posix(),
        "instance_sha256": sha256_file(instance_path),
        "source_snapshot": {
            "origin": "current Python build_safe_candidate",
            "placements": _snapshot_rows(current),
            "placements_sha256": _digest(_snapshot_rows(current)),
            "objective": _objective_record(current.objective),
            "serialization": current_operations,
            "serialization_sha256": _digest(current_operations),
            "official_checker": {
                key: current_checked.get(key)
                for key in ("stage", "objective", "obj1", "obj2", "obj3", "violations")
            },
        },
        "destroy": {
            "operator": "TardyChainDestroy",
            "seed": seed,
            "destroy_size": destroy_size,
            "destroyed_ids": list(destroyed_ids),
            "current_placements": [_placement_row(item) for item in destroyed.destroyed],
            "retained_placements": _snapshot_rows(destroyed.retained),
            "retained_placements_sha256": _digest(_snapshot_rows(destroyed.retained)),
            "retained_raw_bay_loads": list(retained_state.raw_bay_loads),
            "retained_state_version": retained_state.version,
            "boundary_ids": sorted(destroyed.boundary_ids),
        },
        "fixed_work_caps": {
            **dataclasses.asdict(config),
            "regret_depth": 3,
            "budget_guard_seconds": budget_guard_seconds,
            "budget_role": "deadline guard only; no timing or throughput judgement",
        },
    }

    repair_kernel = GeometryKernel.from_instance(instance)
    repair_context = NeighborhoodContext(instance, repair_kernel, config)
    recorder = RepairRecorder(repair_kernel)

    def recorded_relation(
        kernel: GeometryKernel,
        left: Placement,
        right: Placement,
    ) -> Any:
        return recorder.relation(kernel, left, right)

    with (
        patch.object(
            neighborhoods_module,
            "generate_insertion_candidates",
            recorder.generate,
        ),
        patch.object(
            neighborhoods_module,
            "commit_insertion_candidate",
            recorder.commit,
        ),
        patch.object(construct_module, "evaluate_insert", recorder.evaluate),
        patch.object(GeometryKernel, "relation", recorded_relation),
    ):
        result = neighborhoods_module.heuristic_repair(
            current,
            destroyed_ids,
            repair_context,
            Budget.start(budget_guard_seconds),
            regret_depth=3,
        )

    if result.status != "FEASIBLE":
        raise RuntimeError(f"quota repair failed: {result.status} {result.diagnostics!r}")
    repaired_operations = serialize(result.snapshot, repair_kernel)
    repaired_checked = check_feasibility(
        copy.deepcopy(raw), copy.deepcopy(repaired_operations)
    )
    if repaired_checked.get("stage") != 5:
        raise RuntimeError(f"repaired snapshot is not Stage 5: {repaired_checked!r}")

    golden = {
        "schema_version": 1,
        "kind": "pybind_p1_python_repair_golden",
        "fixture_sha256": None,
        "row_schema": list(ROW_SCHEMA),
        "candidate_calls": recorder.calls,
        "commits": recorder.commits,
        "repair_result": {
            "status": result.status,
            "destroyed_ids": list(result.destroyed_ids),
            "changed_ids": sorted(result.changed_ids),
            "candidates_generated": result.candidates_generated,
            "objective_delta": _float_record(result.objective_delta),
            "diagnostics": list(result.diagnostics),
            "final_placements": _snapshot_rows(result.snapshot),
            "final_placements_sha256": _digest(_snapshot_rows(result.snapshot)),
            "final_objective": _objective_record(result.snapshot.objective),
            "serialization": repaired_operations,
            "serialization_sha256": _digest(repaired_operations),
            "official_checker": {
                key: repaired_checked.get(key)
                for key in ("stage", "objective", "obj1", "obj2", "obj3", "violations")
            },
        },
    }

    fixture_path = output / "fixture.json"
    golden_path = output / "golden.json"
    summary_path = output / "summary.json"
    fixture_path.write_text(
        json.dumps(fixture, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    fixture_sha256 = sha256_file(fixture_path)
    golden["fixture_sha256"] = fixture_sha256
    golden_path.write_text(
        json.dumps(golden, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    golden_sha256 = sha256_file(golden_path)

    summary = {
        "schema_version": 1,
        "kind": "pybind_p1_python_repair_summary",
        "source_identity": source_identity,
        "instance": instance_name,
        "instance_sha256": sha256_file(instance_path),
        "dataset_sha256": dataset_sha256,
        "destroyed_ids": list(destroyed_ids),
        "candidate_call_count": len(recorder.calls),
        "candidate_counts": {
            "enumerated_before_exact": sum(
                item["counts"]["enumerated_before_exact"] for item in recorder.calls
            ),
            "evaluated_after_fit": sum(
                item["counts"]["evaluated_after_fit"] for item in recorder.calls
            ),
            "accepted_after_exact": sum(
                item["counts"]["accepted_after_exact"] for item in recorder.calls
            ),
            "top3": sum(item["counts"]["top3"] for item in recorder.calls),
        },
        "relation_query_count": sum(
            item["counts"]["relation_queries"] for item in recorder.calls
        )
        + sum(item["counts"]["relation_queries"] for item in recorder.commits),
        "commit_count": len(recorder.commits),
        "commit_sha256_order": [item["commit_sha256"] for item in recorder.commits],
        "repair_status": result.status,
        "changed_ids": sorted(result.changed_ids),
        "candidates_generated": result.candidates_generated,
        "objective_delta": _float_record(result.objective_delta),
        "final_stage": repaired_checked.get("stage"),
        "final_objective": repaired_checked.get("objective"),
        "final_placements_sha256": golden["repair_result"]["final_placements_sha256"],
        "serialization_sha256": golden["repair_result"]["serialization_sha256"],
        "artifacts": {
            "fixture.json": fixture_sha256,
            "golden.json": golden_sha256,
        },
    }
    summary_path.write_text(
        json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    summary_sha256 = sha256_file(summary_path)
    checksums_path = output / "SHA256SUMS"
    checksums_path.write_text(
        "".join(
            (
                f"{fixture_sha256}  {fixture_path.name}\n",
                f"{golden_sha256}  {golden_path.name}\n",
                f"{summary_sha256}  {summary_path.name}\n",
            )
        ),
        encoding="utf-8",
    )
    return summary


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser()
    result.add_argument("--output", required=True, type=Path)
    result.add_argument("--instance", default="prob_25.json")
    result.add_argument("--seed", default=20260710, type=int)
    result.add_argument("--destroy-size", default=4, type=int)
    result.add_argument("--candidate-attempt-cap", default=32, type=int)
    result.add_argument("--budget-guard-seconds", default=10.0, type=float)
    return result


def main() -> int:
    args = parser().parse_args()
    summary = generate(
        output=args.output,
        instance_name=args.instance,
        seed=args.seed,
        destroy_size=args.destroy_size,
        candidate_attempt_cap=args.candidate_attempt_cap,
        budget_guard_seconds=args.budget_guard_seconds,
    )
    print(canonical_json(summary))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
