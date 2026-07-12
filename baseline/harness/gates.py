"""Immutable evidence selection and S0 gate evaluation."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping


def latest_summary(
    evidence_root: Path,
    *,
    stage: str,
    command: str,
    match: Mapping[str, Any],
) -> tuple[Path, dict[str, Any]] | None:
    root = evidence_root / stage / command
    if not root.exists():
        return None
    candidates: list[tuple[Path, dict[str, Any]]] = []
    for marker in root.glob("*/COMPLETE"):
        summary_path = marker.parent / "summary.json"
        if not summary_path.is_file():
            continue
        summary = json.loads(summary_path.read_text(encoding="utf-8"))
        if all(summary.get(key) == value for key, value in match.items()):
            candidates.append((marker.parent, summary))
    return max(candidates, key=lambda item: item[0].name) if candidates else None


def evaluate_s0(evidence_root: Path) -> dict[str, Any]:
    requirements = (
        ("contract", {"suite": "preflight", "selector": "training"}),
        ("contract", {"suite": "semantic", "selector": "synthetic"}),
        ("parity", {"kind": "geometry", "cases": 1000}),
        ("parity", {"kind": "targeted", "cases": 1000}),
        ("parity", {"kind": "objective", "cases": 100}),
        ("benchmark", {"metric": "predicate", "stage": "s0"}),
        ("benchmark", {"metric": "solver", "stage": "s0", "selector": "training"}),
        ("stress", {"stage": "s0", "selector": "stress"}),
    )
    selected: list[dict[str, Any]] = []
    failures: list[str] = []
    for command, match in requirements:
        found = latest_summary(
            evidence_root, stage="s0", command=command, match=match
        )
        label = f"{command}:{','.join(f'{key}={value}' for key, value in match.items())}"
        if found is None:
            failures.append(f"missing complete evidence for {label}")
            continue
        run_dir, summary = found
        selected.append({"requirement": label, "run_dir": str(run_dir), "summary": summary})
        if summary.get("status") != "passed":
            failures.append(f"non-passing evidence for {label}")

    training = next(
        (item["summary"] for item in selected if "selector=training" in item["requirement"] and item["requirement"].startswith("benchmark")),
        None,
    )
    if training is not None:
        if training.get("record_count") != 40 or training.get("feasible_count") != 40:
            failures.append("training benchmark does not contain 40 Stage-5 feasible records")
        if training.get("max_incumbent_verification_count") != 1:
            failures.append("training benchmark did not use exactly one initial verification")
        if training.get("unverified_return_count") != 0:
            failures.append("training benchmark contains an unverified return")
        if training.get("max_wall_seconds", 999.0) > 5.0:
            failures.append("training benchmark exceeded five seconds")

    predicate = next(
        (item["summary"] for item in selected if "metric=predicate" in item["requirement"]),
        None,
    )
    if predicate is not None and not predicate.get("predicate_table"):
        failures.append("predicate benchmark table is missing")

    stress = next(
        (item["summary"] for item in selected if item["requirement"].startswith("stress")),
        None,
    )
    if stress is not None:
        if stress.get("record_count") != 24 or stress.get("feasible_count") != 24:
            failures.append("stress evidence does not contain 24 feasible rows")
        if stress.get("timeout_count") != 0 or stress.get("leak_count") != 0:
            failures.append("stress evidence contains a timeout or process leak")

    return {
        "stage": "s0",
        "status": "passed" if not failures else "failed",
        "decision": "PASS" if not failures else "FAIL",
        "selected_evidence": selected,
        "failures": failures,
        "native_solver": not failures,
        "pipeline": "t0",
        "later_stage_features": False,
    }


def evaluate_s1(evidence_root: Path) -> dict[str, Any]:
    requirements = (
        ("parity", {"stage": "s1", "kind": "targeted", "cases": 1000}),
        (
            "benchmark",
            {
                "stage": "s1",
                "component": "cap_calibration",
                "selector": "synthetic",
            },
        ),
        ("benchmark", {"stage": "s1", "slice": "s1-05", "selector": "training"}),
        ("ab", {"stage": "s1", "slice": "s1-05", "selector": "dev-10"}),
        ("stress", {"stage": "s1", "slice": "s1-05", "selector": "stress"}),
    )
    selected: list[dict[str, Any]] = []
    failures: list[str] = []
    for command, match in requirements:
        found = latest_summary(
            evidence_root,
            stage="s1",
            command=command,
            match=match,
        )
        label = f"{command}:{','.join(f'{key}={value}' for key, value in match.items())}"
        if found is None:
            failures.append(f"missing complete evidence for {label}")
            continue
        run_dir, summary = found
        selected.append({"requirement": label, "run_dir": str(run_dir), "summary": summary})
        if summary.get("status") != "passed":
            failures.append(f"non-passing evidence for {label}")

    def chosen(prefix: str) -> dict[str, Any] | None:
        return next(
            (item["summary"] for item in selected if item["requirement"].startswith(prefix)),
            None,
        )

    parity = chosen("parity:")
    if parity is not None and parity.get("mismatch_count") != 0:
        failures.append("constructor targeted parity contains a mismatch")

    calibration = chosen("benchmark:stage=s1,component=cap_calibration")
    if calibration is not None:
        if calibration.get("selected_cap_pair") != [16, 48]:
            failures.append("cap calibration did not select the cheapest eligible 16/48 pair")
        if float(calibration.get("minimum_reference_success_ratio", 0.0)) < 0.99:
            failures.append("cap calibration reference insertion success is below 99%")
        if calibration.get("parity_loss_count") != 0 or calibration.get("checker_failure_count") != 0:
            failures.append("cap calibration contains checker or parity loss")

    training = chosen("benchmark:stage=s1,slice=s1-05,selector=training")
    if training is not None:
        if training.get("record_count") != 40 or training.get("feasible_count") != 40:
            failures.append("training benchmark does not contain 40 Stage-5 feasible records")
        if training.get("placed_count") != training.get("block_count"):
            failures.append("training benchmark did not return every block exactly once")
        if float(training.get("max_construction_seconds", 999.0)) > 5.0:
            failures.append("a training construction attempt exceeded five seconds")
        if float(training.get("max_wall_seconds", 999.0)) > 5.0:
            failures.append("a training solver run exceeded five seconds")
        if training.get("unverified_return_count") != 0:
            failures.append("training benchmark contains an unverified return")
        if not training.get("output_sha256") or not training.get("metrics_sha256"):
            failures.append("training determinism hashes are missing")

    ab = chosen("ab:")
    if ab is not None:
        if int(ab.get("improved_count", 0)) < 8:
            failures.append("constructor improves fewer than 8 of 10 dev instances")
        if float(ab.get("median_relative_improvement", 0.0)) < 0.10:
            failures.append("constructor median relative improvement is below 10%")
        if ab.get("regression_count") != 0:
            failures.append("constructor A/B contains an incumbent regression")
        if ab.get("deterministic_replay") is not True:
            failures.append("constructor A/B replay is nondeterministic")

    stress = chosen("stress:")
    if stress is not None:
        if stress.get("feasible_count") != stress.get("record_count"):
            failures.append("constructor stress contains checker infeasibility")
        if stress.get("leak_count") != 0:
            failures.append("constructor stress contains a process leak")
        if stress.get("unverified_return_count") != 0:
            failures.append("constructor stress contains an unverified return")

    return {
        "stage": "s1",
        "status": "passed" if not failures else "failed",
        "decision": "PASS" if not failures else "FAIL",
        "selected_evidence": selected,
        "failures": failures,
        "constructor": not failures,
        "selected_cap_pair": [16, 48] if not failures else None,
        "profile_budget": ["PF3"] if not failures else None,
    }
