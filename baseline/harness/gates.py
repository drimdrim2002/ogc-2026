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


def evaluate_s2(evidence_root: Path) -> dict[str, Any]:
    """Evaluate the immutable S2 parity, gain, fallback, and timebox gate."""

    requirements = (
        ("parity", {"stage": "s2", "kind": "backend", "cases": 50}),
        ("benchmark", {"stage": "s2", "slice": "s2-05", "selector": "training"}),
        ("ab", {"stage": "s2", "slice": "s2-05", "selector": "dev-10"}),
        ("stress", {"stage": "s2", "slice": "s2-05", "selector": "smoke-3"}),
    )
    selected: list[dict[str, Any]] = []
    failures: list[str] = []
    for command, match in requirements:
        found = latest_summary(
            evidence_root,
            stage="s2",
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
    if parity is not None and any(
        int(parity.get(key, 0)) != 0
        for key in (
            "semantic_mismatch_count",
            "optimal_mismatch_count",
            "backend_mismatch_count",
        )
    ):
        failures.append("backend parity contains a semantic or optimal mismatch")

    benchmark = chosen("benchmark:")
    if benchmark is not None:
        if benchmark.get("record_count") != 40 or benchmark.get("feasible_count") != 40:
            failures.append("S2 training benchmark does not contain 40 feasible records")
        if benchmark.get("never_worse_failure_count") != 0:
            failures.append("S2 training benchmark contains a Z1 regression")
        if benchmark.get("exact_timebox_failure_count") != 0:
            failures.append("S2 training benchmark contains an exact timebox violation")
        if benchmark.get("timeout_count") != 0 or benchmark.get("leak_count") != 0:
            failures.append("S2 training benchmark contains a timeout or leak")

    ab = chosen("ab:")
    if ab is not None:
        if float(ab.get("median_z1_gain", 0.0)) <= 0.0:
            failures.append("S2 dev-10 median Z1 gain is not positive")
        if ab.get("regression_count") != 0:
            failures.append("S2 dev-10 matrix contains a Z1 regression")
    if benchmark is not None and ab is not None:
        if benchmark.get("selected_timebox") != ab.get("selected_timebox"):
            failures.append("S2 benchmark timebox does not match the measured winner")
        if benchmark.get("selected_pilot_budget") != ab.get("selected_pilot_budget"):
            failures.append("S2 benchmark pilot budget does not match the measured winner")

    stress = chosen("stress:")
    if stress is not None:
        if stress.get("record_count") != 30 or stress.get("feasible_count") != 30:
            failures.append("S2 stress does not contain 30 feasible records")
        if stress.get("cpsat_fallback_count") != 24:
            failures.append("S2 stress did not select CP-SAT for every Gurobi fault")
        if stress.get("both_fail_sha_match_count") != 6:
            failures.append("S2 both-fail stress did not preserve every constructor SHA")
        if stress.get("never_worse_failure_count") != 0:
            failures.append("S2 stress contains a Z1 regression")
        if stress.get("timeout_count") != 0 or stress.get("leak_count") != 0:
            failures.append("S2 stress contains a timeout or leak")

    selected_timebox = ab.get("selected_timebox") if ab is not None else None
    selected_pilot = ab.get("selected_pilot_budget") if ab is not None else None
    return {
        "stage": "s2",
        "status": "passed" if not failures else "failed",
        "decision": "PASS" if not failures else "FAIL",
        "selected_evidence": selected,
        "failures": failures,
        "exact_retime": not failures,
        "retime_backend": "auto" if not failures else None,
        "selected_timebox": selected_timebox if not failures else None,
        "selected_pilot_budget": selected_pilot if not failures else None,
    }


def evaluate_s3(evidence_root: Path) -> dict[str, Any]:
    """Evaluate integrated safety, prefix, improvement, and control evidence."""

    requirements = (
        ("benchmark", {"stage": "s3", "slice": "s3-05", "selector": "training"}),
        ("benchmark", {"stage": "s3", "slice": "s3-05", "selector": "dev-10"}),
        ("ab", {"stage": "s3", "slice": "s3-04", "feature": "acceptor"}),
        ("ab", {"stage": "s3", "slice": "s3-04", "feature": "alns_adaptive"}),
        (
            "benchmark",
            {"stage": "s3", "slice": "s3-04", "component": "controls"},
        ),
        ("stress", {"stage": "s3", "slice": "s3-05", "selector": "stress"}),
    )
    selected: list[dict[str, Any]] = []
    failures: list[str] = []
    for command, match in requirements:
        found = latest_summary(
            evidence_root,
            stage="s3",
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

    def chosen(fragment: str) -> dict[str, Any] | None:
        return next(
            (
                item["summary"]
                for item in selected
                if fragment in item["requirement"]
            ),
            None,
        )

    training = chosen("slice=s3-05,selector=training")
    if training is not None:
        if training.get("record_count") != 40 or training.get("feasible_count") != 40:
            failures.append("S3 training benchmark does not contain 40 feasible records")
        if training.get("regression_count") != 0:
            failures.append("S3 training benchmark contains an S2 regression")
        if training.get("assignment_mismatch_count") != 0:
            failures.append("S3 training benchmark changed assignment/Z2/Z3")
        if int(training.get("total_iterations", 0)) <= 0:
            failures.append("S3 training benchmark performed no search")
        if int(training.get("checker_mismatch_count", 0)) != 0:
            failures.append("S3 training benchmark contains a checker mismatch")

    dev = chosen("slice=s3-05,selector=dev-10")
    if dev is not None:
        if dev.get("record_count") != 20 or dev.get("feasible_count") != 20:
            failures.append("S3 dev benchmark does not contain 20 feasible records")
        if int(dev.get("prefix_regression_count", 0)) != 0:
            failures.append("S3 dev benchmark is not prefix consistent")
        if int(dev.get("longer_regression_count", 0)) != 0:
            failures.append("an S3 300-second result is worse than its 60-second pair")
        if int(dev.get("regression_count", 0)) != 0:
            failures.append("S3 dev benchmark contains an S2 regression")
        if int(dev.get("improved_count", 0)) < 5:
            failures.append("S3 improves fewer than 5 of 10 dev instances")
        if float(dev.get("median_s3_objective", float("inf"))) >= float(
            dev.get("median_s2_objective", float("-inf"))
        ):
            failures.append("S3 median objective is not strictly below paired S2")
        if int(dev.get("total_accepted", 0)) <= 0:
            failures.append("S3 dev benchmark accepted no candidates")
        attempts = dev.get("operator_attempts", {})
        for name in ("d1", "d2", "d3", "d4", "d5", "r1", "r2", "r3"):
            if int(attempts.get(name, 0)) <= 0:
                failures.append(f"S3 operator {name} was never attempted")

    acceptor = chosen("feature=acceptor")
    if acceptor is not None and acceptor.get("selected_acceptor") != "sa":
        failures.append("S3 acceptor evidence did not select sa")
    adaptive = chosen("feature=alns_adaptive")
    if adaptive is not None and adaptive.get("selected_adaptive") is not False:
        failures.append("S3 adaptive evidence did not select false")
    controls = chosen("component=controls")
    if controls is not None and controls.get("selected_dirty") != [3, 0.03]:
        failures.append("S3 dirty-trigger evidence did not select [3, 0.03]")

    stress = chosen("slice=s3-05,selector=stress")
    if stress is not None:
        if stress.get("feasible_count") != stress.get("record_count"):
            failures.append("S3 stress contains checker infeasibility")
        if int(stress.get("rollback_failure_count", 0)) != 0:
            failures.append("S3 stress contains a rollback/incumbent regression")
        if int(stress.get("assignment_mismatch_count", 0)) != 0:
            failures.append("S3 stress changed assignment/Z2/Z3")
        if int(stress.get("fault_miss_count", 0)) != 0:
            failures.append("S3 stress failed to exercise every fault boundary")
        if int(stress.get("leak_count", 0)) != 0:
            failures.append("S3 stress contains a process leak")

    return {
        "stage": "s3",
        "status": "passed" if not failures else "failed",
        "decision": "PASS" if not failures else "FAIL",
        "selected_evidence": selected,
        "failures": failures,
        "alns": not failures,
        "acceptor": "sa" if not failures else None,
        "adaptive": False,
        "dirty_trigger": [3, 0.03] if not failures else None,
    }


def evaluate_s6(
    evidence_root: Path,
    *,
    requested_commit: str,
) -> dict[str, Any]:
    """Evaluate the frozen mandatory package, stress, and rehearsal identity."""

    requirements = (
        ("stress", {"stage": "s6", "selector": "stress"}),
        (
            "submission-rehearsal",
            {"stage": "s6", "selector": "training,stress", "isolated": True},
        ),
    )
    selected: list[dict[str, Any]] = []
    failures: list[str] = []
    for command, match in requirements:
        found = latest_summary(
            evidence_root,
            stage="s6",
            command=command,
            match=match,
        )
        label = f"{command}:{','.join(f'{key}={value}' for key, value in match.items())}"
        if found is None:
            failures.append(f"missing complete evidence for {label}")
            continue
        run_dir, summary = found
        manifest = json.loads((run_dir / "run.json").read_text(encoding="utf-8"))
        selected.append(
            {
                "requirement": label,
                "run_dir": str(run_dir),
                "summary": summary,
                "manifest": manifest,
            }
        )
        if summary.get("status") != "passed":
            failures.append(f"non-passing evidence for {label}")
        if manifest.get("commit") != requested_commit:
            failures.append(
                f"{command} commit {manifest.get('commit')} does not match {requested_commit}"
            )
        if manifest.get("dirty") is not False or manifest.get("dirty_diff_hash") != "clean":
            failures.append(f"{command} did not run from a clean frozen identity")

    def chosen(command: str) -> dict[str, Any] | None:
        return next(
            (
                item["summary"]
                for item in selected
                if item["requirement"].startswith(f"{command}:")
            ),
            None,
        )

    selected_state = {
        "alns": True,
        "acceptor": "sa",
        "adaptive": False,
        "dirty_trigger": "max(3,.03*n_b)",
        "assignment_refinement": False,
        "parallel_portfolio": False,
        "interlock": False,
    }
    stress = chosen("stress")
    if stress is not None:
        if stress.get("selected_product_state") != selected_state:
            failures.append("S6 stress selected product state differs from the qualified S3 identity")
        if stress.get("record_count") != 62 or stress.get("feasible_count") != 62:
            failures.append("S6 stress does not contain 62 Stage-5 feasible solver records")
        if stress.get("structural_case_count") != 48:
            failures.append("S6 stress structural matrix is incomplete")
        if stress.get("boundary_fault_case_count") != 10:
            failures.append("S6 stress incumbent-boundary fault matrix is incomplete")
        if stress.get("backend_fault_case_count") != 4:
            failures.append("S6 stress backend fault matrix is incomplete")
        for key in (
            "checker_failure_count",
            "timeout_count",
            "crash_count",
            "leak_count",
            "unverified_return_count",
        ):
            if int(stress.get(key, -1)) != 0:
                failures.append(f"S6 stress {key} is nonzero")
        if stress.get("package_output_removed") is not True:
            failures.append("S6 stress package output was not removed")

    rehearsal = chosen("submission-rehearsal")
    if rehearsal is not None:
        if rehearsal.get("selected_product_state") != selected_state:
            failures.append("S6 rehearsal selected product state differs from the qualified S3 identity")
        if rehearsal.get("training_instance_count") != 40:
            failures.append("S6 rehearsal does not contain all 40 training inputs")
        if rehearsal.get("stress_instance_count") != 8:
            failures.append("S6 rehearsal does not contain all 8 stress inputs")
        if rehearsal.get("record_count") != 144:
            failures.append("S6 rehearsal does not contain the complete 48x3 case matrix")
        if rehearsal.get("feasible_count") != 144 or rehearsal.get("checker_stage5_count") != 144:
            failures.append("S6 rehearsal contains a non-Stage-5 case")
        for key in (
            "parent_dependency_count",
            "network_guard_failure_count",
            "timeout_count",
            "leak_count",
            "cleanup_failure_count",
        ):
            if int(rehearsal.get(key, -1)) != 0:
                failures.append(f"S6 rehearsal {key} is nonzero")
        if rehearsal.get("package_output_removed") is not True:
            failures.append("S6 rehearsal package output was not removed")

    stress_package = stress.get("package", {}) if stress is not None else {}
    rehearsal_package = rehearsal.get("package", {}) if rehearsal is not None else {}
    for label, package in (
        ("stress", stress_package),
        ("rehearsal", rehearsal_package),
    ):
        if not package:
            failures.append(f"S6 {label} package audit is missing")
            continue
        if int(package.get("archive_size_bytes", 15 * 1024 * 1024 + 1)) > 15 * 1024 * 1024:
            failures.append(f"S6 {label} package exceeds 15 MB")
        if len(str(package.get("archive_sha256", ""))) != 64:
            failures.append(f"S6 {label} package SHA-256 is missing")
        if package.get("import_smoke_passed") is not True or package.get("checker_smoke_stage") != 5:
            failures.append(f"S6 {label} package import/checker smoke failed")
        if package.get("path_violations", []) or package.get("prohibited_members", []) or package.get("prohibited_text_hits", []):
            failures.append(f"S6 {label} package audit contains a path or content violation")
        protected = package.get("protected_files", {})
        for path in ("baseline/utils.py", "baseline/baseline_greedy.py"):
            objects = protected.get(path, {})
            if not objects or objects.get("worktree_object") != objects.get("head_object"):
                failures.append(f"S6 {label} protected hash mismatch for {path}")
    if (
        stress_package
        and rehearsal_package
        and stress_package.get("archive_sha256")
        != rehearsal_package.get("archive_sha256")
    ):
        failures.append("S6 stress and rehearsal package hashes differ")

    return {
        "stage": "s6",
        "status": "passed" if not failures else "failed",
        "decision": "PASS" if not failures else "FAIL",
        "requested_commit": requested_commit,
        "selected_evidence": selected,
        "failures": failures,
        "selected_product_state": selected_state,
        "mandatory_delivery": not failures,
        "optional_interlock_qualified": False,
    }
