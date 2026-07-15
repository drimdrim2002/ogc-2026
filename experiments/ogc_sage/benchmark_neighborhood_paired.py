"""Interleaved hard-10 legacy/portfolio neighborhood paired benchmark."""

from __future__ import annotations

import argparse
import json
import statistics
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


def phase_totals(records):
    totals = Counter()
    for record in records:
        for name, duration in record.get("phase_timings", {}).items():
            totals[name] += float(duration)
    return dict(sorted(totals.items()))


def operator_totals(records):
    totals = {}
    for record in records:
        for name, values in record.get("operator_stats", {}).items():
            row = totals.setdefault(
                name,
                {
                    "attempts": 0,
                    "feasible": 0,
                    "accepted": 0,
                    "new_best": 0,
                    "exceptions": 0,
                    "delta_sum": 0.0,
                    "time_s": 0.0,
                },
            )
            for key in row:
                row[key] += values.get(key, 0)
    return dict(sorted(totals.items()))


def size_totals(records):
    totals = {}
    for record in records:
        for invocation in record.get("lns_invocations", []):
            for values in invocation.get("destroy_sizes", []):
                key = f"{values['level']}:{int(values['size'])}"
                row = totals.setdefault(
                    key,
                    {
                        "level": values["level"],
                        "size": int(values["size"]),
                        "attempts": 0,
                        "feasible": 0,
                        "accepted": 0,
                        "new_best": 0,
                        "failures": 0,
                        "repair_seconds": 0.0,
                        "iteration_seconds": 0.0,
                    },
                )
                for field in (
                    "attempts",
                    "feasible",
                    "accepted",
                    "new_best",
                    "failures",
                    "repair_seconds",
                    "iteration_seconds",
                ):
                    row[field] += values.get(field, 0)
    return dict(sorted(totals.items()))


def large_new_best(record):
    return sum(
        int(values.get("new_best", 0))
        for invocation in record.get("lns_invocations", [])
        for values in invocation.get("destroy_sizes", [])
        if values.get("level") in {"medium", "large"}
    )


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
        mode: config_hash("heuristic_lns", neighborhood_mode=mode)
        for mode in ("legacy", "portfolio")
    }
    planned = {mode: set() for mode in configs}
    requests = []
    for name in names:
        path = instances[name]
        instance_hash = sha256_file(path)
        for mode in ("legacy", "portfolio"):
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
                    "retime_exact_skip_mode": "legacy",
                    "neighborhood_mode": mode,
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
            run_id=f"phase6-paired-{mode}",
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
    legacy = {
        record["instance"]: record
        for record in load_raw(output_root / "legacy" / RAW_NAME)
    }
    portfolio = {
        record["instance"]: record
        for record in load_raw(output_root / "portfolio" / RAW_NAME)
    }
    rows = []
    wins = ties = losses = 0
    borda = {"legacy": 0, "portfolio": 0}
    weighted = Counter()
    for name in names:
        left, right = legacy[name], portfolio[name]
        baseline_objective = float(left["objective"])
        candidate_objective = float(right["objective"])
        relative_delta = (candidate_objective - baseline_objective) / max(
            1.0, abs(baseline_objective)
        )
        if candidate_objective < baseline_objective - 1e-6:
            wins += 1
            borda["portfolio"] += 1
        elif candidate_objective > baseline_objective + 1e-6:
            losses += 1
            borda["legacy"] += 1
        else:
            ties += 1
            borda["legacy"] += 1
            borda["portfolio"] += 1
        weights = json.loads(instances[name].read_text(encoding="utf-8"))["weights"]
        for component, weight in enumerate(("w1", "w2", "w3"), 1):
            weighted[weight] += float(weights[weight]) * (
                float(left[f"obj{component}"]) - float(right[f"obj{component}"])
            )
        rows.append(
            {
                "instance": name,
                "legacy_objective": baseline_objective,
                "portfolio_objective": candidate_objective,
                "relative_delta": relative_delta,
                "legacy_iterations": int(invocation_sum(left, "iterations")),
                "portfolio_iterations": int(invocation_sum(right, "iterations")),
                "legacy_accepted": operator_sum(left, "accepted"),
                "portfolio_accepted": operator_sum(right, "accepted"),
                "legacy_new_best": operator_sum(left, "new_best"),
                "portfolio_new_best": operator_sum(right, "new_best"),
                "portfolio_medium_large_new_best": large_new_best(right),
            }
        )
    deltas = sorted(row["relative_delta"] for row in rows)
    legacy_records = list(legacy.values())
    portfolio_records = list(portfolio.values())
    comparison = {
        "schema_version": 1,
        "dataset_sha256": dataset_hash,
        "configs": configs,
        "source_commit": args.source_commit,
        "budget_seconds": args.budget,
        "seed": args.seed,
        "wtl": {"wins": wins, "ties": ties, "losses": losses},
        "relative_delta": {
            "median": statistics.median(deltas),
            "mean": statistics.fmean(deltas),
            "p90": percentile(deltas, 0.90),
            "worst": max(deltas),
        },
        "borda": borda,
        "weighted_component_improvement_mean": {
            key: value / len(rows) for key, value in weighted.items()
        },
        "aggregate": {
            "legacy_iterations": sum(row["legacy_iterations"] for row in rows),
            "portfolio_iterations": sum(row["portfolio_iterations"] for row in rows),
            "legacy_accepted": sum(row["legacy_accepted"] for row in rows),
            "portfolio_accepted": sum(row["portfolio_accepted"] for row in rows),
            "legacy_new_best": sum(row["legacy_new_best"] for row in rows),
            "portfolio_new_best": sum(row["portfolio_new_best"] for row in rows),
            "portfolio_medium_large_new_best_instances": sum(
                row["portfolio_medium_large_new_best"] > 0 for row in rows
            ),
            "legacy_phase_times": phase_totals(legacy_records),
            "portfolio_phase_times": phase_totals(portfolio_records),
            "legacy_operator_activity": operator_totals(legacy_records),
            "portfolio_operator_activity": operator_totals(portfolio_records),
            "legacy_destroy_sizes": size_totals(legacy_records),
            "portfolio_destroy_sizes": size_totals(portfolio_records),
        },
        "hard_blockers": {
            "legacy_gate_pass": summaries["legacy"]["gate_pass"],
            "portfolio_gate_pass": summaries["portfolio"]["gate_pass"],
            "legacy_retime_z1_worsen_count": summaries["legacy"]["retime_z1_worsen_count"],
            "portfolio_retime_z1_worsen_count": summaries["portfolio"]["retime_z1_worsen_count"],
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
                "medium_large_new_best_instances": comparison["aggregate"]["portfolio_medium_large_new_best_instances"],
            }
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
