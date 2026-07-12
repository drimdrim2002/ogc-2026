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
