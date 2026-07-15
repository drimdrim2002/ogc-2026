"""Compare paired legacy/precomputed GeometryKernel.fits hard-10 runs."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import statistics
import sys
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from experiments.ogc_sage.benchmark_ogc_sage import (  # noqa: E402
    build_comparison,
    canonical_json,
    hard10_instance_names,
    load_raw,
    percentile,
    sha256_file,
)


def _one_record(path: Path) -> dict[str, Any]:
    path = path.resolve()
    raw = path / "raw.jsonl" if path.is_dir() else path
    records = load_raw(raw)
    if len(records) != 1:
        raise ValueError(f"expected exactly one record in {raw}, got {len(records)}")
    record = records[0]
    record["_raw_path"] = str(raw.relative_to(REPO_ROOT))
    record["_raw_sha256"] = sha256_file(raw)
    return record


def _run_metrics(record: dict[str, Any]) -> dict[str, Any]:
    invocations = record.get("lns_invocations", [])
    iterations = sum(int(item.get("iterations", 0)) for item in invocations)
    operator = record.get("operator_stats", {})
    return {
        "elapsed_seconds": float(record["elapsed_seconds"]),
        "phase_timings": {
            name: float(value) for name, value in record.get("phase_timings", {}).items()
        },
        "iterations": iterations,
        "repair_seconds_per_iteration": (
            float(record.get("phase_timings", {}).get("lns_repair", 0.0))
            / max(1, iterations)
        ),
        "attempts": sum(int(item.get("attempts", 0)) for item in operator.values()),
        "feasible": sum(int(item.get("feasible", 0)) for item in operator.values()),
        "accepted": sum(int(item.get("accepted", 0)) for item in operator.values()),
        "new_best": sum(int(item.get("new_best", 0)) for item in operator.values()),
        "validated_best_events": len(record.get("validated_best_trace", [])),
    }


def _aggregate(records: list[dict[str, Any]]) -> dict[str, Any]:
    metrics = [_run_metrics(record) for record in records]
    phase_names = sorted(
        {name for metric in metrics for name in metric["phase_timings"]}
    )
    return {
        "elapsed_seconds": {
            "sum": sum(item["elapsed_seconds"] for item in metrics),
            "median": statistics.median(item["elapsed_seconds"] for item in metrics),
        },
        "phase_seconds_sum": {
            name: sum(item["phase_timings"].get(name, 0.0) for item in metrics)
            for name in phase_names
        },
        "phase_seconds_median": {
            name: statistics.median(
                item["phase_timings"].get(name, 0.0) for item in metrics
            )
            for name in phase_names
        },
        "repair_seconds_per_iteration_median": statistics.median(
            item["repair_seconds_per_iteration"] for item in metrics
        ),
        "iterations": sum(item["iterations"] for item in metrics),
        "attempts": sum(item["attempts"] for item in metrics),
        "feasible": sum(item["feasible"] for item in metrics),
        "accepted": sum(item["accepted"] for item in metrics),
        "new_best": sum(item["new_best"] for item in metrics),
        "validated_best_events": sum(item["validated_best_events"] for item in metrics),
    }


def compare(
    *, baseline_paths: list[Path], candidate_paths: list[Path], output: Path
) -> dict[str, Any]:
    baseline = [_one_record(path) for path in baseline_paths]
    candidate = [_one_record(path) for path in candidate_paths]
    by_baseline = {record["instance"]: record for record in baseline}
    by_candidate = {record["instance"]: record for record in candidate}
    names = hard10_instance_names()
    if tuple(by_baseline) != names or tuple(by_candidate) != names:
        raise ValueError("input paths must follow the frozen hard-10 order")

    labelled: list[dict[str, Any]] = []
    rows: list[dict[str, Any]] = []
    wins = ties = losses = 0
    deltas: list[float] = []
    for name in names:
        before = by_baseline[name]
        after = by_candidate[name]
        scenario_before = (
            before["instance_hash"],
            before["dataset_hash"],
            before["budget_seconds"],
            before["seed"],
            before["source_commit"],
        )
        scenario_after = (
            after["instance_hash"],
            after["dataset_hash"],
            after["budget_seconds"],
            after["seed"],
            after["source_commit"],
        )
        if scenario_before != scenario_after:
            raise ValueError(f"paired scenario mismatch for {name}")
        baseline_objective = float(before["objective"])
        candidate_objective = float(after["objective"])
        delta = (candidate_objective - baseline_objective) / max(
            1.0, abs(baseline_objective)
        )
        deltas.append(delta)
        if candidate_objective < baseline_objective - 1e-6:
            outcome = "W"
            wins += 1
        elif candidate_objective > baseline_objective + 1e-6:
            outcome = "L"
            losses += 1
        else:
            outcome = "T"
            ties += 1
        before_metrics = _run_metrics(before)
        after_metrics = _run_metrics(after)
        rows.append(
            {
                "instance": name,
                "baseline_objective": baseline_objective,
                "candidate_objective": candidate_objective,
                "relative_delta": delta,
                "outcome": outcome,
                "baseline_iterations": before_metrics["iterations"],
                "candidate_iterations": after_metrics["iterations"],
                "baseline_repair_seconds": before_metrics["phase_timings"].get(
                    "lns_repair", 0.0
                ),
                "candidate_repair_seconds": after_metrics["phase_timings"].get(
                    "lns_repair", 0.0
                ),
                "baseline_accepted": before_metrics["accepted"],
                "candidate_accepted": after_metrics["accepted"],
                "baseline_new_best": before_metrics["new_best"],
                "candidate_new_best": after_metrics["new_best"],
            }
        )
        before_labelled = dict(before)
        before_labelled.pop("_raw_path")
        before_labelled.pop("_raw_sha256")
        before_labelled["variant"] = "baseline_legacy_fit"
        after_labelled = dict(after)
        after_labelled.pop("_raw_path")
        after_labelled.pop("_raw_sha256")
        after_labelled["variant"] = "candidate_precomputed_fit"
        labelled.extend((before_labelled, after_labelled))

    comparison = build_comparison(labelled, "baseline_legacy_fit")
    parity_errors = [
        float(record.get("internal_checker_objective_error") or 0.0)
        for record in baseline + candidate
    ]
    input_hashes = {
        record["_raw_path"]: record["_raw_sha256"]
        for record in baseline + candidate
    }
    path_digest = hashlib.sha256(
        "".join(
            f"{digest}  {path}\n" for path, digest in sorted(input_hashes.items())
        ).encode("utf-8")
    ).hexdigest()
    document = {
        "schema_version": 1,
        "kind": "phase4_paired_geometry_fit_ablation",
        "hard10_order": list(names),
        "paired_rows": rows,
        "paired_summary": {
            "wins": wins,
            "ties": ties,
            "losses": losses,
            "relative_delta_median": statistics.median(deltas),
            "relative_delta_mean": statistics.fmean(deltas),
            "relative_delta_p90": percentile(deltas, 0.90),
            "relative_delta_worst": max(deltas),
            "additional_seed_required": abs(wins - losses) <= 2
            or math.isclose(statistics.median(deltas), 0.0, abs_tol=1e-12),
        },
        "baseline_aggregate": _aggregate(baseline),
        "candidate_aggregate": _aggregate(candidate),
        "quality_comparison": comparison,
        "hard_blockers": {
            "run_count": len(baseline) + len(candidate),
            "stage5_count": sum(
                record.get("feasible") is True and record.get("stage") == 5
                for record in baseline + candidate
            ),
            "exception_count": sum(
                record.get("exception") is not None for record in baseline + candidate
            ),
            "outer_timeout_count": sum(
                record.get("outer_timeout") is True for record in baseline + candidate
            ),
            "checker_failure_count": sum(
                record.get("status") == "checker_failure"
                for record in baseline + candidate
            ),
            "trace_regression_count": sum(
                record.get("trace_non_increasing") is not True
                for record in baseline + candidate
            ),
            "retime_z1_worsen_count": sum(
                int(record.get("retime_z1_worsen_count", 0))
                for record in baseline + candidate
            ),
            "objective_parity_max_relative_error": max(parity_errors, default=0.0),
        },
        "config_hashes": {
            "baseline": sorted({record["config_hash"] for record in baseline}),
            "candidate": sorted({record["config_hash"] for record in candidate}),
        },
        "source_commits": sorted(
            {record["source_commit"] for record in baseline + candidate}
        ),
        "dataset_hashes": sorted(
            {record["dataset_hash"] for record in baseline + candidate}
        ),
        "input_raw_sha256": input_hashes,
        "input_raw_path_list_sha256": path_digest,
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(document, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return document


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser()
    result.add_argument("--baseline", nargs=10, required=True, type=Path)
    result.add_argument("--candidate", nargs=10, required=True, type=Path)
    result.add_argument("--output", required=True, type=Path)
    return result


def main() -> int:
    args = parser().parse_args()
    compare(
        baseline_paths=args.baseline,
        candidate_paths=args.candidate,
        output=args.output,
    )
    print(canonical_json({"output": str(args.output), "sha256": sha256_file(args.output)}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
