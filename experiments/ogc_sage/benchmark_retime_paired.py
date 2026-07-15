"""Interleaved hard-10 60-second legacy/exact-retime paired benchmark."""

from __future__ import annotations

import argparse
import json
import math
import sys
from collections import Counter
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from experiments.ogc_sage.benchmark_ogc_sage import (  # noqa: E402
    RAW_NAME,
    SUMMARY_NAME,
    append_raw,
    calibrate,
    canonical_json,
    config_hash,
    environment_versions,
    hard10_instance_names,
    load_raw,
    make_run_key,
    outer_timeout,
    percentile,
    run_worker_subprocess,
    sha256_file,
    summarize,
    validate_dataset,
)


def invocation_sum(record, key):
    return sum(float(item.get(key, 0.0)) for item in record.get("lns_invocations", []))


def operator_sum(record, key):
    return sum(int(item.get(key, 0)) for item in record.get("operator_stats", {}).values())


def retime_events(record):
    return [
        event
        for invocation in record.get("lns_invocations", [])
        for event in invocation.get("retime_events", [])
    ]


def phase_totals(records):
    result = Counter()
    for record in records:
        for event in retime_events(record):
            for name, duration in event.get("telemetry", {}).get("phase_times", {}).items():
                result[name] += float(duration)
    return dict(sorted(result.items()))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-root", required=True)
    parser.add_argument("--source-commit", required=True)
    parser.add_argument("--seed", type=int, default=20260710)
    parser.add_argument("--budget", type=float, default=60.0)
    args = parser.parse_args()
    output_root = Path(args.output_root).resolve()
    instances, dataset_hash = validate_dataset()
    names = hard10_instance_names()
    calibration = calibrate(instances[names[0]])
    configs = {
        mode: config_hash("heuristic_lns", retime_exact_skip_mode=mode)
        for mode in ("legacy", "exact")
    }
    planned = {mode: set() for mode in configs}
    requests = []
    for name in names:
        path = instances[name]
        instance_hash = sha256_file(path)
        for mode in ("legacy", "exact"):
            digest = configs[mode]
            key = make_run_key(
                source_commit=args.source_commit,
                dataset_hash=dataset_hash,
                config_digest=digest,
                variant="heuristic_lns",
                instance_hash=instance_hash,
                budget=args.budget,
                seed=args.seed,
            )
            planned[mode].add(key)
            requests.append(
                {
                    "mode": mode,
                    "run_key": key,
                    "instance": name,
                    "instance_path": str(path),
                    "instance_hash": instance_hash,
                    "dataset_hash": dataset_hash,
                    "variant": "heuristic_lns",
                    "config_hash": digest,
                    "geometry_fit_mode": "legacy",
                    "retime_exact_skip_mode": mode,
                    "source_commit": args.source_commit,
                    "budget_seconds": args.budget,
                    "seed": args.seed,
                }
            )
    by_mode = {mode: load_raw(output_root / mode / RAW_NAME) for mode in configs}
    completed = {
        mode: {record["run_key"] for record in records}
        for mode, records in by_mode.items()
    }
    for index, request in enumerate(requests, 1):
        mode = request.pop("mode")
        if request["run_key"] in completed[mode]:
            continue
        record = run_worker_subprocess(
            request, outer_timeout(args.budget, calibration)
        )
        append_raw(output_root / mode / RAW_NAME, record)
        print(
            f"[{index}/{len(requests)}] {request['instance']} {mode} "
            f"status={record['status']} elapsed={record['elapsed_seconds']:.3f}",
            flush=True,
        )
    summaries = {}
    for mode, digest in configs.items():
        directory = output_root / mode
        records = load_raw(directory / RAW_NAME)
        raw_hash = sha256_file(directory / RAW_NAME)
        summary = summarize(
            run_id=f"phase5-paired-{mode}",
            records=records,
            planned_keys=planned[mode],
            dataset_hash=dataset_hash,
            config_digest=digest,
            source_commit=args.source_commit,
            raw_hash=raw_hash,
            calibration_values=calibration,
            command=sys.argv,
            gate="feature",
        )
        directory.mkdir(parents=True, exist_ok=True)
        (directory / SUMMARY_NAME).write_text(
            json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
        summaries[mode] = summary
    legacy = {record["instance"]: record for record in load_raw(output_root / "legacy" / RAW_NAME)}
    exact = {record["instance"]: record for record in load_raw(output_root / "exact" / RAW_NAME)}
    rows = []
    wins = ties = losses = 0
    borda = {"legacy": 0, "exact": 0}
    weighted = Counter()
    for name in names:
        left, right = legacy[name], exact[name]
        baseline_objective = float(left["objective"])
        candidate_objective = float(right["objective"])
        relative_delta = (candidate_objective - baseline_objective) / max(
            1.0, abs(baseline_objective)
        )
        if candidate_objective < baseline_objective - 1e-6:
            wins += 1
            borda["exact"] += 1
        elif candidate_objective > baseline_objective + 1e-6:
            losses += 1
            borda["legacy"] += 1
        else:
            ties += 1
            borda["legacy"] += 1
            borda["exact"] += 1
        weights = json.loads(instances[name].read_text(encoding="utf-8"))["weights"]
        for index, key in enumerate(("w1", "w2", "w3"), 1):
            weighted[key] += float(weights[key]) * (
                float(left[f"obj{index}"]) - float(right[f"obj{index}"])
            )
        exact_events = retime_events(right)
        rows.append(
            {
                "instance": name,
                "legacy_objective": baseline_objective,
                "exact_objective": candidate_objective,
                "relative_delta": relative_delta,
                "legacy_iterations": int(invocation_sum(left, "iterations")),
                "exact_iterations": int(invocation_sum(right, "iterations")),
                "legacy_accepted": operator_sum(left, "accepted"),
                "exact_accepted": operator_sum(right, "accepted"),
                "legacy_new_best": operator_sum(left, "new_best"),
                "exact_new_best": operator_sum(right, "new_best"),
                "legacy_retime_seconds": invocation_sum(left, "retime_seconds"),
                "exact_retime_seconds": invocation_sum(right, "retime_seconds"),
                "legacy_retime_triggers": int(invocation_sum(left, "retime_triggers")),
                "exact_retime_triggers": int(invocation_sum(right, "retime_triggers")),
                "exact_skipped_components": sum(
                    int(event.get("telemetry", {}).get("exact_skipped_components", 0))
                    for event in exact_events
                ),
            }
        )
    deltas = sorted(row["relative_delta"] for row in rows)
    legacy_records = list(legacy.values())
    exact_records = list(exact.values())
    comparison = {
        "schema_version": 1,
        "dataset_sha256": dataset_hash,
        "configs": configs,
        "source_commit": args.source_commit,
        "budget_seconds": args.budget,
        "seed": args.seed,
        "wtl": {"wins": wins, "ties": ties, "losses": losses},
        "relative_delta": {
            "median": (deltas[4] + deltas[5]) / 2,
            "mean": sum(deltas) / len(deltas),
            "p90": percentile(deltas, 0.90),
            "worst": max(deltas),
        },
        "borda": borda,
        "weighted_component_improvement_mean": {
            key: value / len(rows) for key, value in weighted.items()
        },
        "aggregate": {
            "legacy_iterations": sum(row["legacy_iterations"] for row in rows),
            "exact_iterations": sum(row["exact_iterations"] for row in rows),
            "legacy_accepted": sum(row["legacy_accepted"] for row in rows),
            "exact_accepted": sum(row["exact_accepted"] for row in rows),
            "legacy_new_best": sum(row["legacy_new_best"] for row in rows),
            "exact_new_best": sum(row["exact_new_best"] for row in rows),
            "legacy_retime_seconds": sum(row["legacy_retime_seconds"] for row in rows),
            "exact_retime_seconds": sum(row["exact_retime_seconds"] for row in rows),
            "legacy_retime_triggers": sum(row["legacy_retime_triggers"] for row in rows),
            "exact_retime_triggers": sum(row["exact_retime_triggers"] for row in rows),
            "exact_skipped_components": sum(row["exact_skipped_components"] for row in rows),
            "legacy_retime_phase_times": phase_totals(legacy_records),
            "exact_retime_phase_times": phase_totals(exact_records),
        },
        "hard_blockers": {
            "legacy_gate_pass": summaries["legacy"]["gate_pass"],
            "exact_gate_pass": summaries["exact"]["gate_pass"],
            "legacy_retime_z1_worsen_count": summaries["legacy"]["retime_z1_worsen_count"],
            "exact_retime_z1_worsen_count": summaries["exact"]["retime_z1_worsen_count"],
        },
        "rows": rows,
        "environment": environment_versions(),
    }
    comparison_path = output_root / "comparison.json"
    comparison_path.write_text(
        json.dumps(comparison, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    print(
        canonical_json(
            {
                "comparison": str(comparison_path),
                "sha256": sha256_file(comparison_path),
                "wtl": comparison["wtl"],
                "median_relative_delta": comparison["relative_delta"]["median"],
            }
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
