#!/usr/bin/env python3
"""prob_23 fixed-work GEOS shadow/native-exact gate (exactly eight repairs)."""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import random
import sys
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[2]
BASELINE = ROOT / "baseline"
for path in (ROOT, BASELINE):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from baseline.solver.budget import Budget  # noqa: E402
from baseline.solver.construct import ConstructorConfig  # noqa: E402
from baseline.solver.fallback import build_safe_candidate  # noqa: E402
from baseline.solver.geometry import GeometryKernel  # noqa: E402
from baseline.solver.instance import parse_instance  # noqa: E402
from baseline.solver.native_repair import snapshot_digest  # noqa: E402
from baseline.solver import neighborhoods as neighborhoods_module  # noqa: E402
from baseline.solver.neighborhoods import NeighborhoodContext  # noqa: E402
from baseline.solver.serialize import serialize  # noqa: E402
from baseline.solver.state import Placement  # noqa: E402
from baseline.utils import check_feasibility  # noqa: E402
from experiments.ogc_sage.benchmark_ogc_sage import (  # noqa: E402
    canonical_json,
    validate_dataset,
)
from baseline.solver.neighborhoods import TardyChainDestroy  # noqa: E402


SEED = 20260710
INSTANCE = "prob_23.json"
REPAIRS = 8


def digest(value: object) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def placement_row(item: Placement) -> list[int]:
    return [
        item.block_id,
        item.bay_id,
        item.orient_idx,
        item.x,
        item.y,
        item.entry,
        item.exit,
    ]


def config(*, native_exact_mode: str | None) -> ConstructorConfig:
    native = native_exact_mode is not None
    return ConstructorConfig(
        seed=SEED,
        max_profiles=1,
        max_candidate_attempts=None,
        repair_backend="native" if native else "python",
        native_prefilter_enabled=native_exact_mode == "shadow",
        native_exact_mode=native_exact_mode or "python",
    )


def run_repair(
    raw: dict[str, object],
    instance: object,
    source: object,
    destroyed_ids: tuple[int, ...],
    *,
    native_exact_mode: str | None,
    budget_guard_seconds: float,
) -> dict[str, object]:
    kernel = GeometryKernel.from_instance(instance)
    context = NeighborhoodContext(
        instance, kernel, config(native_exact_mode=native_exact_mode)
    )
    candidate_trace: list[dict[str, object]] = []
    original_generate = neighborhoods_module.generate_insertion_candidates

    def recorded_generate(*args, **kwargs):
        values = tuple(original_generate(*args, **kwargs))
        current = kwargs.get("current_placement")
        candidate_trace.append(
            {
                "block_id": int(args[1]),
                "state_version": int(args[0].version),
                "current": None if current is None else placement_row(current),
                "candidates": [
                    {
                        "placement": placement_row(item.placement),
                        "canonical_tie": list(item.canonical_tie),
                    }
                    for item in values
                ],
            }
        )
        return values

    with patch.object(
        neighborhoods_module, "generate_insertion_candidates", recorded_generate
    ):
        result = neighborhoods_module.heuristic_repair(
            source,
            destroyed_ids,
            context,
            Budget.start(budget_guard_seconds),
            regret_depth=3,
        )
    operations = serialize(result.snapshot, kernel)
    checked = check_feasibility(copy.deepcopy(raw), copy.deepcopy(operations))
    return {
        "result": result,
        "candidate_trace": candidate_trace,
        "candidate_digest": digest(candidate_trace),
        "placement_digest": snapshot_digest(result.snapshot),
        "serialization_digest": digest(operations),
        "objective": result.snapshot.objective.total,
        "checker": {
            key: checked.get(key)
            for key in ("feasible", "stage", "violations", "objective")
        },
        "telemetry": dict(result.telemetry),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--budget-guard-seconds", type=float, default=60.0)
    parser.add_argument("--destroy-size", type=int, default=4)
    parser.add_argument(
        "--native-exact-mode", choices=("shadow", "native"), default="shadow"
    )
    args = parser.parse_args()
    if args.output.exists():
        raise SystemExit(f"refusing to overwrite output: {args.output}")
    if args.budget_guard_seconds <= 0 or args.destroy_size <= 0:
        raise SystemExit("fixed-work guard and destroy size must be positive")

    instances, dataset_sha256 = validate_dataset()
    instance_path = instances[INSTANCE]
    raw = json.loads(instance_path.read_text(encoding="utf-8"))
    instance = parse_instance(raw)
    source = build_safe_candidate(instance, Budget.start(2.0))
    selector_kernel = GeometryKernel.from_instance(instance)
    selector_context = NeighborhoodContext(instance, selector_kernel)
    selector = TardyChainDestroy()
    rng = random.Random(SEED)

    repairs: list[dict[str, object]] = []
    aggregate: dict[str, int | float] = {
        "shadow_checked_pairs": 0,
        "shadow_free_free_count": 0,
        "shadow_blocked_blocked_count": 0,
        "shadow_mismatch_count": 0,
        "geos_error_count": 0,
        "python_exact_seconds": 0.0,
        "cpp_exact_seconds": 0.0,
        "exact_cache_hits": 0,
        "exact_cache_misses": 0,
        "generated_candidates": 0,
        "aabb_skipped_pairs": 0,
        "geos_exact_calls": 0,
        "first_conflict_skipped_pairs": 0,
        "returned_candidates": 0,
        "native_exact_seconds": 0.0,
        "native_cache_seconds": 0.0,
        "native_error_seconds": 0.0,
        "python_returned_candidate_rechecks": 0,
        "returned_candidate_recheck_failures": 0,
        "native_error_count": 0,
        "fallback_count": 0,
        "native_deadline_count": 0,
        "python_reference_calls_after_deadline": 0,
        "python_full_repair_started_after_deadline": 0,
    }
    candidate_mismatches = placement_mismatches = serialization_mismatches = 0
    first_failure: object | None = None

    for ordinal in range(REPAIRS):
        destroyed_ids = selector.select(
            source, args.destroy_size, rng, selector_context
        )
        python = run_repair(
            raw,
            instance,
            source,
            destroyed_ids,
            native_exact_mode=None,
            budget_guard_seconds=args.budget_guard_seconds,
        )
        native = run_repair(
            raw,
            instance,
            source,
            destroyed_ids,
            native_exact_mode=args.native_exact_mode,
            budget_guard_seconds=args.budget_guard_seconds,
        )
        telemetry = native["telemetry"]
        for key in aggregate:
            aggregate[key] += telemetry.get(key, 0)
        candidate_match = python["candidate_digest"] == native["candidate_digest"]
        placement_match = python["placement_digest"] == native["placement_digest"]
        serialization_match = (
            python["serialization_digest"] == native["serialization_digest"]
        )
        candidate_mismatches += not candidate_match
        placement_mismatches += not placement_match
        serialization_mismatches += not serialization_match
        stage_five = all(
            item["checker"]["stage"] == 5
            and item["checker"]["violations"] == []
            for item in (python, native)
        )
        repairs.append(
            {
                "ordinal": ordinal,
                "destroyed_ids": list(destroyed_ids),
                "candidate_match": candidate_match,
                "placement_match": placement_match,
                "serialization_match": serialization_match,
                "objective_match": python["objective"] == native["objective"],
                "stage_five": stage_five,
                "python": {
                    key: python[key]
                    for key in (
                        "candidate_digest",
                        "placement_digest",
                        "serialization_digest",
                        "objective",
                        "checker",
                    )
                },
                args.native_exact_mode: {
                    key: native[key]
                    for key in (
                        "candidate_digest",
                        "placement_digest",
                        "serialization_digest",
                        "objective",
                        "checker",
                    )
                },
                "native_telemetry": telemetry,
            }
        )
        if (
            telemetry["native_exact_decision_enabled"]
            != (args.native_exact_mode == "native")
            or telemetry["native_exact_mode"] != args.native_exact_mode
            or telemetry["shadow_mismatch_count"]
            or telemetry["geos_error_count"]
            or telemetry["returned_candidate_recheck_failures"]
            or telemetry["native_error_count"]
            or telemetry["fallback_count"]
            or telemetry["python_reference_calls_after_deadline"]
            or telemetry["python_full_repair_started_after_deadline"]
            or not candidate_match
            or not placement_match
            or not serialization_match
            or not stage_five
        ):
            first_failure = {
                "repair_ordinal": ordinal,
                "first_shadow_mismatch": telemetry["first_shadow_mismatch"],
                "returned_candidate_recheck_failures": telemetry[
                    "returned_candidate_recheck_failures"
                ],
                "native_error_count": telemetry["native_error_count"],
                "candidate_match": candidate_match,
                "placement_match": placement_match,
                "serialization_match": serialization_match,
                "stage_five": stage_five,
            }
            break
        source = python["result"].snapshot

    completed = len(repairs) == REPAIRS
    passed = bool(
        completed
        and aggregate["shadow_mismatch_count"] == 0
        and aggregate["geos_error_count"] == 0
        and aggregate["returned_candidate_recheck_failures"] == 0
        and aggregate["native_error_count"] == 0
        and aggregate["fallback_count"] == 0
        and aggregate["python_reference_calls_after_deadline"] == 0
        and aggregate["python_full_repair_started_after_deadline"] == 0
        and candidate_mismatches == 0
        and placement_mismatches == 0
        and serialization_mismatches == 0
        and all(item["stage_five"] for item in repairs)
    )
    document = {
        "schema_version": 1,
        "kind": f"native_{args.native_exact_mode}_fixed_work",
        "native_exact_mode": args.native_exact_mode,
        "instance": INSTANCE,
        "instance_sha256": hashlib.sha256(instance_path.read_bytes()).hexdigest(),
        "dataset_sha256": dataset_sha256,
        "seed": SEED,
        "requested_repairs": REPAIRS,
        "completed_repairs": len(repairs),
        "destroy_size": args.destroy_size,
        "budget_guard_seconds": args.budget_guard_seconds,
        "aggregate": aggregate,
        "parity": {
            "candidate_mismatches": candidate_mismatches,
            "placement_mismatches": placement_mismatches,
            "serialization_mismatches": serialization_mismatches,
        },
        "first_failure": first_failure,
        "repairs": repairs,
        "production_default": "python",
        "status": "PASS" if passed else "FAIL",
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(document, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    print(
        json.dumps(
            {
                "status": document["status"],
                "completed_repairs": len(repairs),
                "aggregate": aggregate,
                "parity": document["parity"],
                "first_failure": first_failure,
                "output": str(args.output),
            },
            sort_keys=True,
        )
    )
    return 0 if passed else 2


if __name__ == "__main__":
    raise SystemExit(main())
