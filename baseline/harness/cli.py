"""Command-line entry for the resumable checker-authoritative harness."""

from __future__ import annotations

import argparse
from dataclasses import asdict
from datetime import datetime
import hashlib
import json
import math
import os
from pathlib import Path
import random
import resource
import statistics
import sys
import time
import unittest
from typing import Any, Iterable, Mapping

from shapely.affinity import translate

from .compare import latency_summary
from .gates import evaluate_s0, evaluate_s1, evaluate_s2, evaluate_s3, latest_summary
from .package import StageUnsupportedError
from .report import render_gate_report
from .runner import (
    make_escalation_stress_ref,
    repository_provenance,
    run_assignment_case,
    run_assignment_v2_case,
    run_cap_calibration_case,
    run_constructor_case,
    run_entry_case,
    run_escalation_stress_case,
    run_exact_probe_fault_case,
    run_retime_fault_case,
    run_s3_operator_case,
    run_s3_control_case,
    run_s3_integrated_case,
    s3_prefix_record,
    run_s2_entry_case,
    run_s2_matrix_records,
    run_backend_parity_record,
    run_gurobi_retime_case,
    run_t0_case,
)
from .schema import EvidenceRun, find_completed_identity, new_run_id, record_identity
from .selectors import REPO_ROOT, SelectorError, select_instances

# The permanent unittest modules intentionally use the submission-style
# ``solver`` import when executed from ``baseline/``.  Make that same import
# root explicit when the harness itself is launched from the repository root.
BASELINE_ROOT = str(REPO_ROOT / "baseline")
if BASELINE_ROOT not in sys.path:
    sys.path.insert(0, BASELINE_ROOT)

try:
    from baseline.solver.config import (
        CAP_CALIBRATION_MATRIX,
        ALNS_DIRTY_TRIGGER_MATRIX,
        DEFAULT_CONFIG,
        RETIME_PILOT_MATRIX,
        RETIME_TIMEBOX_MATRIX,
    )
    from baseline.solver.geometry import GeomKernel, ShapeInfo
    from baseline.solver.instance import ProblemInstance
except ModuleNotFoundError:
    from solver.config import (
        CAP_CALIBRATION_MATRIX,
        ALNS_DIRTY_TRIGGER_MATRIX,
        DEFAULT_CONFIG,
        RETIME_PILOT_MATRIX,
        RETIME_TIMEBOX_MATRIX,
    )
    from solver.geometry import GeomKernel, ShapeInfo
    from solver.instance import ProblemInstance


EXIT_PASS = 0
EXIT_GATE_FAILURE = 2
EXIT_CHECKER_FAILURE = 3
EXIT_INPUT = 4
EXIT_RUNTIME = 5
EXIT_SEMANTIC = 6


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m baseline.harness.cli")
    subparsers = parser.add_subparsers(dest="command", required=True)

    contract = subparsers.add_parser("contract")
    _common(contract)
    contract.add_argument("--suite", choices=("semantic", "preflight", "schema"), required=True)
    contract.add_argument("--instances", required=True)

    parity = subparsers.add_parser("parity")
    _common(parity)
    parity.add_argument(
        "--kind", choices=("objective", "targeted", "geometry", "backend"), required=True
    )
    parity.add_argument("--cases", type=int, required=True)
    parity.add_argument("--instances", required=True)

    benchmark = subparsers.add_parser("benchmark")
    _common(benchmark)
    benchmark.add_argument("--stage", required=True)
    benchmark.add_argument("--metric", default="solver")
    benchmark.add_argument("--component")
    benchmark.add_argument("--instances", required=True)
    benchmark.add_argument("--timelimits", required=True)
    benchmark.add_argument("--seeds", required=True)

    stress = subparsers.add_parser("stress")
    _common(stress)
    stress.add_argument("--stage", required=True)
    stress.add_argument("--instances", required=True)
    stress.add_argument("--timelimits", required=True)
    stress.add_argument("--seeds", required=True)

    ab = subparsers.add_parser("ab")
    _common(ab)
    ab.add_argument("--stage", required=True)
    ab.add_argument("--instances", required=True)
    ab.add_argument("--timelimits", required=True)
    ab.add_argument("--seeds")
    ab.add_argument("--a", required=True)
    ab.add_argument("--b", required=True)

    gate = subparsers.add_parser("gate")
    _common(gate)
    gate.add_argument("--stage", required=True)
    gate.add_argument("--latest-complete", action="store_true")
    gate.add_argument("--commit", required=True)

    report = subparsers.add_parser("report")
    _common(report)
    report.add_argument("--stage", required=True)
    report.add_argument("--latest-complete", action="store_true")

    rehearsal = subparsers.add_parser("submission-rehearsal")
    _common(rehearsal)
    rehearsal.add_argument("--instances", required=True)
    rehearsal.add_argument("--timelimits", required=True)
    rehearsal.add_argument("--seeds", required=True)
    rehearsal.add_argument("--isolated", action="store_true")
    return parser


def _common(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--evidence-root", default="benchmarks/evidence")
    parser.add_argument("--run-id", default="auto")
    parser.add_argument("--seed", type=_seed_argument, default=20260710)
    parser.add_argument("--jobs", type=int, default=1)
    parser.add_argument("--feature", action="append", default=[])
    parser.add_argument("--resume")
    parser.add_argument("--rerun", action="store_true")


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.jobs != 1:
            raise SelectorError("the harness supports --jobs 1 only")
        if args.command == "contract":
            return _contract(args)
        if args.command == "parity":
            return _parity(args)
        if args.command == "benchmark":
            return _benchmark(args)
        if args.command == "stress":
            return _stress(args)
        if args.command == "gate":
            return _gate(args)
        if args.command == "report":
            return _report(args)
        if args.command == "ab":
            return _ab(args)
        if args.command == "submission-rehearsal":
            raise StageUnsupportedError("stage unsupported: submission rehearsal belongs to S6")
        raise SelectorError(f"unknown command: {args.command}")
    except KeyboardInterrupt:
        return 130
    except (SelectorError, StageUnsupportedError, ValueError) as exc:
        print(str(exc), file=sys.stderr)
        return EXIT_INPUT
    except Exception as exc:
        print(f"harness runtime failure: {type(exc).__name__}: {exc}", file=sys.stderr)
        return EXIT_RUNTIME


def _contract(args: argparse.Namespace) -> int:
    if args.suite == "schema":
        return _test_contract_run(
            args,
            suite="schema",
            selector=args.instances,
            test_names=(
                _test_name("test_harness_schema.HarnessSchemaTests.test_interrupted_run_is_not_complete"),
                _test_name("test_harness_process.HarnessProcessTests.test_runner_captures_successful_proof_of_run"),
            ),
        )
    if args.suite == "semantic":
        names = tuple(
            _test_name(f"test_contract_semantics.CheckerContractTests.{name}")
            for name in sorted(
                name
                for name in dir(_checker_contract_class())
                if name.startswith("test_")
            )
        )
        return _test_contract_run(args, suite="semantic", selector=args.instances, test_names=names)
    if args.suite != "preflight":
        raise SelectorError(f"unsupported contract suite: {args.suite}")

    run, evidence_root = _start_run(
        args,
        "contract",
        expected_record_ids=(),
        metadata={"stage": "s0", "suite": "preflight", "selector": args.instances},
        delayed_expected=True,
    )
    refs = select_instances(args.instances, fixture_dir=run.run_dir / "fixtures")
    _set_expected(run, tuple(ref.instance_id for ref in refs))
    failures = 0
    for ref in refs:
        if ref.instance_id not in run.pending_record_ids:
            continue
        try:
            parsed = ProblemInstance.parse(ref.prob_info)
            parsed.assert_solvable_fit()
            record = {
                "record_id": ref.instance_id,
                "status": "passed",
                "complete": True,
                "instance_id": ref.instance_id,
                "instance_path": str(ref.path),
                "instance_sha": ref.sha256,
                "block_count": len(parsed.blocks),
                "fit_preflight": True,
            }
        except Exception as exc:
            failures += 1
            record = {
                "record_id": ref.instance_id,
                "status": "failed",
                "complete": True,
                "instance_id": ref.instance_id,
                "instance_sha": ref.sha256,
                "error": f"{type(exc).__name__}: {exc}",
            }
        run.append_record(record)
    summary = {
        "command": "contract",
        "stage": "s0",
        "suite": "preflight",
        "selector": args.instances,
        "status": "passed" if failures == 0 else "failed",
        "record_count": len(refs),
        "failure_count": failures,
        "instance_hashes": {ref.instance_id: ref.sha256 for ref in refs},
    }
    run.finalize(summary)
    _announce(run, summary)
    return EXIT_PASS if failures == 0 else EXIT_INPUT


def _test_contract_run(
    args: argparse.Namespace,
    *,
    suite: str,
    selector: str,
    test_names: tuple[str, ...],
) -> int:
    run, _ = _start_run(
        args,
        "contract",
        expected_record_ids=test_names,
        metadata={"stage": "s0", "suite": suite, "selector": selector},
    )
    failures = 0
    for name in test_names:
        if name not in run.pending_record_ids:
            continue
        record = _run_unittest(name)
        failures += record["status"] != "passed"
        run.append_record(record)
    summary = {
        "command": "contract",
        "stage": "s0",
        "suite": suite,
        "selector": selector,
        "status": "passed" if failures == 0 else "failed",
        "record_count": len(test_names),
        "failure_count": failures,
        "checker_authority": "baseline/solver/checker_adapter.py -> baseline/utils.py",
    }
    run.finalize(summary)
    _announce(run, summary)
    return EXIT_PASS if failures == 0 else EXIT_SEMANTIC


def _parity(args: argparse.Namespace) -> int:
    expected_cases = {
        "geometry": 1000,
        "targeted": 1000,
        "objective": 100,
        "backend": 50,
    }
    if args.cases != expected_cases[args.kind]:
        raise SelectorError(
            f"{args.kind} parity requires exactly {expected_cases[args.kind]} cases"
        )
    features = _features(args.feature)
    if args.kind == "backend":
        if args.instances != "synthetic" or features != {"timebox": "2"}:
            raise SelectorError(
                "backend parity requires synthetic instances and timebox=2"
            )
        run, _ = _start_stage_run(
            args,
            stage="s2",
            command="parity",
            expected_record_ids=("backend",),
            metadata={
                "slice": "s2-03",
                "kind": "backend",
                "cases": args.cases,
                "selector": args.instances,
                "features": features,
            },
        )
        record = run_backend_parity_record(
            cases=args.cases,
            seed=_single_seed(args.seed),
            timebox=2.0,
        )
        run.append_record(record)
        passed = (
            record["status"] == "passed"
            and record["semantic_mismatches"] == 0
            and record["optimal_mismatches"] == 0
            and record["backend_mismatches"] == 0
        )
        summary = {
            "command": "parity",
            "stage": "s2",
            "slice": "s2-03",
            "kind": "backend",
            "cases": args.cases,
            "selector": args.instances,
            "features": features,
            "status": "passed" if passed else "failed",
            "semantic_mismatch_count": record["semantic_mismatches"],
            "optimal_mismatch_count": record["optimal_mismatches"],
            "backend_mismatch_count": record["backend_mismatches"],
            "cpsat_available": record["cpsat_available"],
            "wall_seconds": record["wall_seconds"],
        }
        run.finalize(summary)
        _announce(run, summary)
        return EXIT_PASS if passed else EXIT_SEMANTIC
    caller = features.get("caller")
    if caller is not None and caller != "construct":
        raise SelectorError(f"unsupported parity caller: {caller}")
    if caller == "construct" and (args.kind != "targeted" or args.instances != "synthetic"):
        raise SelectorError("construct parity requires targeted kind and synthetic instances")
    stage = "s1" if caller == "construct" else "s0"
    if stage == "s1":
        run, _ = _start_stage_run(
            args,
            stage=stage,
            command="parity",
            expected_record_ids=(args.kind,),
            metadata={
                "kind": args.kind,
                "cases": args.cases,
                "selector": args.instances,
                "features": features,
            },
        )
    else:
        run, _ = _start_run(
            args,
            "parity",
            expected_record_ids=(args.kind,),
            metadata={
                "stage": stage,
                "kind": args.kind,
                "cases": args.cases,
                "selector": args.instances,
                "features": features,
            },
        )
    if args.kind == "geometry":
        record = _geometry_parity_record(args.cases, _single_seed(args.seed))
    else:
        if caller == "construct":
            suffix = "test_construct.ConstructorTests.test_seeded_1000_event_candidates_match_checker"
        else:
            suffix = (
                "test_validate_parity.ValidateParityTests.test_seeded_1000_candidates"
                if args.kind == "targeted"
                else "test_state_parity.StateParityTests.test_seeded_100_objective_cases_match_checker"
            )
        tested = _run_unittest(_test_name(suffix))
        record = {
            **tested,
            "record_id": args.kind,
            "cases": args.cases,
            "mismatches": 0 if tested["status"] == "passed" else 1,
            "max_relative_error": 0.0 if tested["status"] == "passed" else None,
        }
    run.append_record(record)
    passed = record["status"] == "passed" and record.get("mismatches") == 0
    summary = {
        "command": "parity",
        "stage": stage,
        "kind": args.kind,
        "cases": args.cases,
        "selector": args.instances,
        "features": features,
        "status": "passed" if passed else "failed",
        "mismatch_count": record.get("mismatches", 0),
        "max_relative_error": record.get("max_relative_error", 0.0),
    }
    run.finalize(summary)
    _announce(run, summary)
    return EXIT_PASS if passed else EXIT_SEMANTIC


def _geometry_parity_record(cases: int, seed: int) -> dict[str, Any]:
    raw = {
        "name": "geometry-parity",
        "bays": [{"width": 20, "height": 20}],
        "blocks": [{
            "release_time": 0,
            "due_date": 10,
            "processing_time": 1,
            "workload": 1,
            "bay_preferences": [1],
            "shape": [{"orientation": 0, "layers": [[[0, 0], [3, 0], [3, 2], [0, 2]]]}],
        }],
        "weights": {"w1": 1, "w2": 1, "w3": 1},
    }
    parsed = ProblemInstance.parse(raw)
    shape = ShapeInfo.from_orientation(parsed.blocks[0].orientations[0])
    kernel = GeomKernel(cache_cap=2048)
    rng = random.Random(seed)
    mismatches = 0
    for _ in range(cases):
        dx = rng.randrange(-5, 6)
        dy = rng.randrange(-5, 6)
        internal = kernel.union_disjoint(shape, shape, dx, dy)
        shifted = translate(shape.union, xoff=dx, yoff=dy)
        oracle = shape.union.intersection(shifted).area <= 0.0
        mismatches += internal != oracle
    return {
        "record_id": "geometry",
        "status": "passed" if mismatches == 0 else "failed",
        "complete": True,
        "cases": cases,
        "mismatches": mismatches,
        "max_relative_error": 0.0,
        "cache": asdict(kernel.stats),
    }


def _benchmark(args: argparse.Namespace) -> int:
    if args.stage == "s4":
        return _s4_assignment_benchmark(args)
    if args.stage == "s3":
        if args.component == "controls":
            return _s3_control_benchmark(args)
        if args.component == "operators":
            return _s3_operator_benchmark(args)
        if args.component is None:
            return _s3_integrated_benchmark(args)
        raise StageUnsupportedError(f"unsupported S3 component: {args.component}")
    if args.stage == "s2":
        return (
            _gurobi_retime_benchmark(args)
            if args.component is not None
            else _s2_entry_benchmark(args)
        )
    if args.stage == "s1":
        if args.component == "assign_v1":
            return _assignment_benchmark(args)
        if args.component == "cap_calibration":
            return _cap_calibration_benchmark(args)
        return _constructor_benchmark(args)
    if args.stage != "s0":
        raise StageUnsupportedError(f"stage unsupported: {args.stage}")
    if args.component is not None:
        raise SelectorError("S0 benchmark does not accept --component")
    features = _features(args.feature)
    if args.metric == "predicate":
        return _predicate_benchmark(args, features)
    if args.metric != "solver":
        raise SelectorError(f"unsupported S0 benchmark metric: {args.metric}")
    if features.get("pipeline", "t0") != "t0":
        raise StageUnsupportedError("stage unsupported: S0 benchmark only supports pipeline=t0")
    timelimits = _csv_floats(args.timelimits)
    seeds = _csv_ints(args.seeds)
    run, evidence_root = _start_run(
        args,
        "benchmark",
        expected_record_ids=(),
        metadata={"stage": "s0", "metric": "solver", "selector": args.instances},
        delayed_expected=True,
    )
    refs = select_instances(args.instances, fixture_dir=run.run_dir / "fixtures")
    expected = tuple(
        _case_id(ref.instance_id, timelimit, seed, "none")
        for ref in refs for timelimit in timelimits for seed in seeds
    )
    _set_expected(run, expected)
    for ref in refs:
        scheduled_limits = (
            (300.0,) if args.instances == "dev-10" else timelimits
        )
        for timelimit in scheduled_limits:
            for seed in seeds:
                record_id = _case_id(ref.instance_id, timelimit, seed, "none")
                if record_id not in run.pending_record_ids:
                    continue
                record = run_t0_case(
                    ref, selector=args.instances, timelimit=timelimit,
                    seed=seed, features=features,
                )
                run.append_record(_deduplicate(evidence_root, record, rerun=args.rerun))
    records = _effective_records(run.records)
    passed = all(
        record.get("checker", {}).get("feasible") is True
        and record.get("checker", {}).get("stage") == 5
        and record.get("wall_seconds", 999.0) <= record.get("timelimit", 0.0)
        and record.get("incumbent_verification_count") == 1
        and record.get("unverified_return_count") == 0
        for record in records
    )
    summary = _solver_summary("benchmark", args.instances, records, passed)
    summary.update(metric="solver", timelimits=timelimits, seeds=seeds, features=features)
    run.finalize(summary)
    _announce(run, summary)
    return EXIT_PASS if passed else EXIT_CHECKER_FAILURE


def _s3_integrated_benchmark(args: argparse.Namespace) -> int:
    if args.metric != "solver" or args.instances not in {"training", "dev-10"}:
        raise SelectorError("S3-05 benchmark requires training or dev-10")
    features = _features(args.feature)
    if features != {"alns": "true"}:
        raise SelectorError("S3-05 benchmark requires --feature alns=true")
    timelimits = _csv_floats(args.timelimits)
    seeds = _csv_ints(args.seeds)
    expected_limits = (60.0,) if args.instances == "training" else (60.0, 300.0)
    if timelimits != expected_limits or seeds != (20260710,):
        raise SelectorError(
            f"S3-05 {args.instances} benchmark requires timelimits="
            f"{','.join(f'{item:g}' for item in expected_limits)} and seed=20260710"
        )
    run, evidence_root = _start_stage_run(
        args,
        stage="s3",
        command="benchmark",
        expected_record_ids=(),
        metadata={
            "slice": "s3-05",
            "selector": args.instances,
            "features": features,
        },
        delayed_expected=True,
    )
    refs = select_instances(args.instances, fixture_dir=run.run_dir / "fixtures")
    expected = tuple(
        f"{ref.instance_id}|tl={timelimit:g}|seed=20260710|fault=none"
        for ref in refs
        for timelimit in timelimits
    )
    _set_expected(run, expected)
    for ref in refs:
        for timelimit in timelimits:
            record_id = (
                f"{ref.instance_id}|tl={timelimit:g}|seed=20260710|fault=none"
            )
            if record_id not in run.pending_record_ids:
                continue
            record = run_s3_integrated_case(
                ref,
                selector=args.instances,
                timelimit=timelimit,
                seed=20260710,
                features=features,
            )
            if args.instances == "dev-10":
                prefix = s3_prefix_record(ref, record, timelimit=60.0)
                run.append_record(prefix)
            record.pop("_epoch_solutions", None)
            run.append_record(_deduplicate(evidence_root, record, rerun=args.rerun))
    records = _effective_records(run.records)
    sixty = tuple(
        record for record in records if float(record.get("timelimit", 0.0)) == 60.0
    )
    improved_count = sum(
        float(record.get("final_objective", float("inf")))
        < float(record.get("s2_objective", float("-inf")))
        for record in sixty
    )
    regression_count = sum(record.get("never_worse") is not True for record in records)
    prefix_regression_count = 0
    longer_regression_count = 0
    if args.instances == "dev-10":
        by_instance = {
            instance_id: {
                float(record["timelimit"]): record
                for record in records
                if record["instance_id"] == instance_id
            }
            for instance_id in {str(record["instance_id"]) for record in records}
        }
        for paired in by_instance.values():
            short = paired.get(60.0, {})
            long = paired.get(300.0, {})
            prefix_regression_count += (
                short.get("first_epoch_sha256") != long.get("first_epoch_sha256")
            )
            longer_regression_count += float(
                long.get("final_objective", float("inf"))
            ) > float(short.get("final_objective", float("-inf")))
    median_s3 = statistics.median(
        float(record["final_objective"]) for record in sixty
    ) if sixty else float("inf")
    median_s2 = statistics.median(
        float(record["s2_objective"]) for record in sixty
    ) if sixty else float("inf")
    passed = (
        len(records) == len(expected)
        and all(record.get("checker", {}).get("feasible") is True for record in records)
        and all(record.get("checker", {}).get("stage") == 5 for record in records)
        and all(record.get("assignment_preserved") is True for record in records)
        and all(record.get("trace_monotonic") is True for record in records)
        and all(record.get("prefix_consistent") is True for record in records)
        and all(int(record.get("metrics", {}).get("iterations", 0)) > 0 for record in records)
        and all(
            float(record.get("wall_seconds", float("inf")))
            <= float(record.get("timelimit", 0.0)) + 0.25
            for record in records
        )
        and all(
            float(record.get("retime_wall_fraction", 1.0))
            <= DEFAULT_CONFIG.alns_retime_wall_fraction_cap
            for record in records
        )
        and regression_count == 0
        and prefix_regression_count == 0
        and longer_regression_count == 0
    )
    summary = _solver_summary(
        "benchmark", args.instances, records, passed, stage="s3"
    )
    summary.update(
        slice="s3-05",
        timelimits=timelimits,
        seeds=seeds,
        features=features,
        improved_count=improved_count,
        regression_count=regression_count,
        prefix_regression_count=prefix_regression_count,
        longer_regression_count=longer_regression_count,
        median_s3_objective=median_s3,
        median_s2_objective=median_s2,
        total_iterations=sum(
            int(record.get("metrics", {}).get("iterations", 0)) for record in records
        ),
        total_accepted=sum(
            int(record.get("metrics", {}).get("accepted", 0)) for record in records
        ),
        checker_mismatch_count=sum(
            int(record.get("metrics", {}).get("checker_failures", 0))
            for record in records
        ),
        assignment_mismatch_count=sum(
            record.get("assignment_preserved") is not True for record in records
        ),
        operator_attempts={
            name: sum(
                int(record.get("operator_metrics", {}).get(name, {}).get("attempts", 0))
                for record in records
            )
            for name in ("d1", "d2", "d3", "d4", "d5", "r1", "r2", "r3")
        },
    )
    run.finalize(summary)
    _announce(run, summary)
    return EXIT_PASS if passed else EXIT_GATE_FAILURE


def _s3_operator_benchmark(args: argparse.Namespace) -> int:
    if args.component != "operators":
        raise StageUnsupportedError(
            "S3-02 benchmark only supports --component operators"
        )
    if args.metric != "solver" or args.instances != "smoke-3":
        raise SelectorError(
            "S3-02 operator benchmark requires default metric and smoke-3"
        )
    features = _features(args.feature)
    required = {"alns": "true", "acceptor": "strict", "adaptive": "false"}
    if features != required:
        raise SelectorError(
            "S3-02 benchmark requires alns=true, acceptor=strict, adaptive=false"
        )
    timelimits = _csv_floats(args.timelimits)
    seeds = _csv_ints(args.seeds)
    if timelimits != (60.0,) or seeds != (20260710,):
        raise SelectorError(
            "S3-02 benchmark requires timelimits=60 and seeds=20260710"
        )
    run, evidence_root = _start_stage_run(
        args,
        stage="s3",
        command="benchmark",
        expected_record_ids=(),
        metadata={
            "slice": "s3-02",
            "component": "operators",
            "selector": "smoke-3",
            "features": dict(features),
        },
        delayed_expected=True,
    )
    refs = select_instances("smoke-3", fixture_dir=run.run_dir / "fixtures")
    expected = tuple(
        _case_id(ref.instance_id, 60.0, 20260710, "operators")
        for ref in refs
    )
    _set_expected(run, expected)
    for ref in refs:
        record_id = _case_id(ref.instance_id, 60.0, 20260710, "operators")
        if record_id not in run.pending_record_ids:
            continue
        record = run_s3_operator_case(
            ref,
            selector="smoke-3",
            timelimit=60.0,
            seed=20260710,
            features=features,
        )
        run.append_record(_deduplicate(evidence_root, record, rerun=args.rerun))
    records = _effective_records(run.records)
    operator_names = ("d1", "d2", "d3", "d4", "d5", "r1", "r2", "r3")
    passed = (
        len(records) == len(expected)
        and all(
            record.get("status") in {"passed", "deduplicated"}
            and record.get("checker", {}).get("feasible") is True
            and record.get("checker", {}).get("stage") == 5
            and record.get("assignment_mismatch_count") == 0
            and record.get("checker_failure_count") == 0
            and record.get("rollback_exact") is True
            and record.get("base_token_unchanged") is True
            and float(record.get("wall_seconds", 999.0)) <= 60.25
            and all(
                int(
                    record.get("operator_metrics", {})
                    .get(name, {})
                    .get("attempts", 0)
                )
                > 0
                or record.get("operator_applicability", {}).get(name) is False
                for name in operator_names
            )
            and all(
                int(
                    record.get("operator_metrics", {})
                    .get(name, {})
                    .get("successes", 0)
                )
                > 0
                for name in operator_names
            )
            for record in records
        )
    )
    summary = _solver_summary(
        "benchmark", "smoke-3", records, passed, stage="s3"
    )
    summary.update(
        slice="s3-02",
        component="operators",
        timelimits=timelimits,
        seeds=seeds,
        features=dict(features),
        operator_attempts={
            name: sum(
                int(
                    record.get("operator_metrics", {})
                    .get(name, {})
                    .get("attempts", 0)
                )
                for record in records
            )
            for name in operator_names
        },
        operator_successes={
            name: sum(
                int(
                    record.get("operator_metrics", {})
                    .get(name, {})
                    .get("successes", 0)
                )
                for record in records
            )
            for name in operator_names
        },
        operator_failures={
            name: sum(
                int(
                    record.get("operator_metrics", {})
                    .get(name, {})
                    .get("failures", 0)
                )
                for record in records
            )
            for name in operator_names
        },
        successful_candidate_count=sum(
            int(record.get("successful_candidate_count", 0)) for record in records
        ),
        full_check_count=sum(
            int(record.get("full_check_count", 0)) for record in records
        ),
        assignment_mismatch_count=sum(
            int(record.get("assignment_mismatch_count", 0)) for record in records
        ),
        checker_failure_count=sum(
            int(record.get("checker_failure_count", 0)) for record in records
        ),
        rollback_failure_count=sum(
            record.get("rollback_exact") is not True for record in records
        ),
        leak_count=0,
    )
    run.finalize(summary)
    _announce(run, summary)
    return EXIT_PASS if passed else EXIT_CHECKER_FAILURE


def _s3_control_benchmark(args: argparse.Namespace) -> int:
    if args.metric != "solver" or args.instances != "dev-10":
        raise SelectorError(
            "S3-04 control benchmark requires default metric and dev-10"
        )
    features = _features(args.feature)
    required = {
        "alns": "true",
        "acceptor": DEFAULT_CONFIG.alns_acceptor,
        "adaptive": str(DEFAULT_CONFIG.alns_adaptive).lower(),
    }
    if features != required:
        raise SelectorError(
            "S3-04 control benchmark requires alns=true, selected acceptor, "
            "and selected adaptive flag"
        )
    timelimits = _csv_floats(args.timelimits)
    seeds = _csv_ints(args.seeds)
    if timelimits != (60.0,) or seeds != (20260710, 20260711, 20260712):
        raise SelectorError(
            "S3-04 control benchmark requires timelimits=60 and seeds="
            "20260710,20260711,20260712"
        )
    run, evidence_root = _start_stage_run(
        args,
        stage="s3",
        command="benchmark",
        expected_record_ids=(),
        metadata={
            "slice": "s3-04",
            "component": "controls",
            "selector": "dev-10",
            "dirty_matrix": ALNS_DIRTY_TRIGGER_MATRIX,
            "features": features,
        },
        delayed_expected=True,
    )
    refs = select_instances("dev-10", fixture_dir=run.run_dir / "fixtures")
    expected = tuple(
        f"{ref.instance_id}|tl=60|seed={seed}|"
        f"acceptor={DEFAULT_CONFIG.alns_acceptor}|"
        f"adaptive={str(DEFAULT_CONFIG.alns_adaptive).lower()}|"
        f"dirty={minimum}:{fraction:g}|order=matrix"
        for ref in refs
        for seed in seeds
        for minimum, fraction in ALNS_DIRTY_TRIGGER_MATRIX
    )
    _set_expected(run, expected)
    for ref in refs:
        for seed in seeds:
            for minimum, fraction in ALNS_DIRTY_TRIGGER_MATRIX:
                record_id = (
                    f"{ref.instance_id}|tl=60|seed={seed}|"
                    f"acceptor={DEFAULT_CONFIG.alns_acceptor}|"
                    f"adaptive={str(DEFAULT_CONFIG.alns_adaptive).lower()}|"
                    f"dirty={minimum}:{fraction:g}|order=matrix"
                )
                if record_id not in run.pending_record_ids:
                    continue
                record = run_s3_control_case(
                    ref,
                    selector="dev-10",
                    timelimit=60.0,
                    seed=seed,
                    acceptor_name=DEFAULT_CONFIG.alns_acceptor,
                    adaptive=DEFAULT_CONFIG.alns_adaptive,
                    dirty_minimum=minimum,
                    dirty_fraction=fraction,
                    features=features,
                    run_label="matrix",
                )
                run.append_record(
                    _deduplicate(evidence_root, record, rerun=args.rerun)
                )
    records = _effective_records(run.records)
    candidates = []
    for minimum, fraction in ALNS_DIRTY_TRIGGER_MATRIX:
        rows = tuple(
            record
            for record in records
            if int(record["retime"]["dirty_minimum"]) == minimum
            and float(record["retime"]["dirty_fraction"]) == fraction
        )
        eligible = (
            len(rows) == 30
            and all(record.get("checker", {}).get("feasible") is True for record in rows)
            and all(record.get("never_worse") is True for record in rows)
            and all(record.get("assignment_preserved") is True for record in rows)
            and all(
                int(record.get("metrics", {}).get("checker_failures", 0)) == 0
                for record in rows
            )
            and all(
                float(record["retime"]["search_wall_fraction"])
                <= DEFAULT_CONFIG.alns_retime_wall_fraction_cap
                for record in rows
            )
        )
        candidates.append(
            {
                "minimum": minimum,
                "fraction": fraction,
                "eligible": eligible,
                "median_objective": statistics.median(
                    float(record["final_objective"]) for record in rows
                )
                if rows
                else float("inf"),
                "max_retime_wall_fraction": max(
                    (float(record["retime"]["search_wall_fraction"]) for record in rows),
                    default=1.0,
                ),
                "record_count": len(rows),
            }
        )
    eligible = [candidate for candidate in candidates if candidate["eligible"]]
    selected = min(
        eligible,
        key=lambda candidate: (
            float(candidate["median_objective"]),
            ALNS_DIRTY_TRIGGER_MATRIX.index(
                (int(candidate["minimum"]), float(candidate["fraction"]))
            ),
        ),
    ) if eligible else None
    passed = len(records) == len(expected) and selected is not None
    summary = _solver_summary(
        "benchmark", "dev-10", records, passed, stage="s3"
    )
    summary.update(
        slice="s3-04",
        component="controls",
        timelimits=timelimits,
        seeds=seeds,
        features=features,
        dirty_matrix=[list(item) for item in ALNS_DIRTY_TRIGGER_MATRIX],
        candidates=candidates,
        selected_dirty=(
            [selected["minimum"], selected["fraction"]]
            if selected is not None
            else None
        ),
    )
    run.finalize(summary)
    _announce(run, summary)
    return EXIT_PASS if passed else EXIT_GATE_FAILURE


def _gurobi_retime_benchmark(args: argparse.Namespace) -> int:
    if args.component != "retime":
        raise StageUnsupportedError("S2-02 benchmark only supports --component retime")
    if args.metric != "solver":
        raise SelectorError("S2 retime benchmark uses the default solver metric")
    if args.instances != "synthetic":
        raise SelectorError("S2-02 retime benchmark requires --instances synthetic")
    features = _features(args.feature)
    if features != {"retime_backend": "gurobi"}:
        raise SelectorError("S2-02 benchmark requires retime_backend=gurobi")
    timelimits = _csv_floats(args.timelimits)
    seeds = _csv_ints(args.seeds)
    if timelimits != (2.0,) or seeds != (20260710,):
        raise SelectorError(
            "S2-02 benchmark requires timelimits=2 and seeds=20260710"
        )
    run, evidence_root = _start_stage_run(
        args,
        stage="s2",
        command="benchmark",
        expected_record_ids=(),
        metadata={
            "slice": "s2-02",
            "component": "retime",
            "selector": "synthetic",
            "features": dict(features),
        },
        delayed_expected=True,
    )
    refs = select_instances("synthetic", fixture_dir=run.run_dir / "fixtures")
    expected = tuple(
        f"{ref.instance_id}|tl=2|seed=20260710|backend=gurobi"
        for ref in refs
    )
    _set_expected(run, expected)
    for ref in refs:
        record_id = f"{ref.instance_id}|tl=2|seed=20260710|backend=gurobi"
        if record_id not in run.pending_record_ids:
            continue
        record = run_gurobi_retime_case(
            ref,
            selector="synthetic",
            timelimit=2.0,
            seed=20260710,
            features=features,
        )
        run.append_record(_deduplicate(evidence_root, record, rerun=args.rerun))
    records = _effective_records(run.records)
    required_backend_fields = {
        "status",
        "bound",
        "gap",
        "build_seconds",
        "solve_seconds",
        "first_solution_seconds",
    }
    passed = (
        len(records) == len(expected)
        and all(
            record.get("status") in {"passed", "deduplicated"}
            and record.get("checker", {}).get("feasible") is True
            and record.get("checker", {}).get("stage") == 5
            and record.get("exact_z1_match") is True
            and required_backend_fields <= set(record.get("backend", {}))
            and record.get("backend", {}).get("status")
            in {"optimal", "feasible", "unavailable"}
            and float(record.get("wall_seconds", 999.0)) <= 2.25
            for record in records
        )
    )
    summary = _solver_summary(
        "benchmark", "synthetic", records, passed, stage="s2"
    )
    summary.update(
        slice="s2-02",
        component="retime",
        timelimits=timelimits,
        seeds=seeds,
        features=dict(features),
        backend_statuses=[
            record.get("backend", {}).get("status") for record in records
        ],
        bounds=[record.get("backend", {}).get("bound") for record in records],
        gaps=[record.get("backend", {}).get("gap") for record in records],
        build_seconds=[
            record.get("backend", {}).get("build_seconds") for record in records
        ],
        solve_seconds=[
            record.get("backend", {}).get("solve_seconds") for record in records
        ],
        first_solution_seconds=[
            record.get("backend", {}).get("first_solution_seconds")
            for record in records
        ],
        checker_failure_count=sum(
            record.get("checker", {}).get("feasible") is not True
            for record in records
        ),
        normalized_unavailable_count=sum(
            record.get("backend", {}).get("status") == "unavailable"
            for record in records
        ),
        model_assertion_count=sum(
            len(record.get("model_assertions", {})) for record in records
        ),
        leak_count=0,
    )
    run.finalize(summary)
    _announce(run, summary)
    return EXIT_PASS if passed else EXIT_CHECKER_FAILURE


def _s2_entry_benchmark(args: argparse.Namespace) -> int:
    if args.metric != "solver" or args.instances != "training":
        raise SelectorError("S2-05 benchmark requires default metric and training")
    features = _features(args.feature)
    if features != {"exact_retime": "true", "retime_backend": "auto"}:
        raise SelectorError(
            "S2-05 benchmark requires exact_retime=true and retime_backend=auto"
        )
    timelimits = _csv_floats(args.timelimits)
    seeds = _csv_ints(args.seeds)
    if timelimits != (60.0,) or seeds != (20260710,):
        raise SelectorError("S2-05 benchmark requires timelimits=60 and seeds=20260710")
    run, evidence_root = _start_stage_run(
        args,
        stage="s2",
        command="benchmark",
        expected_record_ids=(),
        metadata={
            "slice": "s2-05",
            "selector": "training",
            "features": dict(features),
        },
        delayed_expected=True,
    )
    refs = select_instances("training", fixture_dir=run.run_dir / "fixtures")
    expected = tuple(
        f"{ref.instance_id}|tl=60|seed=20260710|run=benchmark" for ref in refs
    )
    _set_expected(run, expected)
    for ref in refs:
        record_id = f"{ref.instance_id}|tl=60|seed=20260710|run=benchmark"
        if record_id not in run.pending_record_ids:
            continue
        record = run_s2_entry_case(
            ref,
            selector="training",
            timelimit=60.0,
            seed=20260710,
            features=features,
            exact_retime=True,
            timebox=DEFAULT_CONFIG.retime_timebox_seconds,
            pilot_budget=DEFAULT_CONFIG.retime_pilot_seconds,
            run_label="benchmark",
        )
        run.append_record(_deduplicate(evidence_root, record, rerun=args.rerun))
    records = _effective_records(run.records)
    passed = (
        len(records) == len(expected)
        and all(
            record.get("status") in {"passed", "deduplicated"}
            and record.get("checker", {}).get("feasible") is True
            and record.get("never_worse") is True
            and record.get("exact_calls_bounded") is True
            and float(record.get("wall_seconds", 999.0)) <= 60.25
            for record in records
        )
    )
    summary = _solver_summary("benchmark", "training", records, passed, stage="s2")
    summary.update(
        slice="s2-05",
        timelimits=timelimits,
        seeds=seeds,
        features=dict(features),
        selected_timebox=DEFAULT_CONFIG.retime_timebox_seconds,
        selected_pilot_budget=DEFAULT_CONFIG.retime_pilot_seconds,
        never_worse_failure_count=sum(
            record.get("never_worse") is not True for record in records
        ),
        exact_timebox_failure_count=sum(
            record.get("exact_calls_bounded") is not True for record in records
        ),
        timeout_count=sum(bool(record.get("timeout")) for record in records),
        leak_count=0,
    )
    run.finalize(summary)
    _announce(run, summary)
    return EXIT_PASS if passed else EXIT_CHECKER_FAILURE


def _s4_assignment_benchmark(args: argparse.Namespace) -> int:
    if args.component != "assignment_v2":
        raise StageUnsupportedError(
            "S4-01 benchmark only supports --component assignment_v2"
        )
    if args.metric != "solver" or args.instances != "high-w23":
        raise SelectorError(
            "S4-01 assignment benchmark requires the default metric and high-w23"
        )
    features = _features(args.feature)
    if features != {"assignment_backend": "gurobi"}:
        raise SelectorError(
            "S4-01 benchmark requires --feature assignment_backend=gurobi"
        )
    timelimits = _csv_floats(args.timelimits)
    seeds = _csv_ints(args.seeds)
    if timelimits != (60.0,) or seeds != (20260710,):
        raise SelectorError(
            "S4-01 benchmark requires timelimits=60 and seeds=20260710"
        )
    run, evidence_root = _start_stage_run(
        args,
        stage="s4",
        command="benchmark",
        expected_record_ids=(),
        metadata={"component": "assignment_v2", "selector": args.instances},
        delayed_expected=True,
    )
    refs = select_instances(args.instances, fixture_dir=run.run_dir / "fixtures")
    expected = tuple(
        _case_id(ref.instance_id, timelimit, seed, "none")
        for ref in refs
        for timelimit in timelimits
        for seed in seeds
    )
    _set_expected(run, expected)
    for ref in refs:
        for timelimit in timelimits:
            for seed in seeds:
                record_id = _case_id(ref.instance_id, timelimit, seed, "none")
                if record_id not in run.pending_record_ids:
                    continue
                record = run_assignment_v2_case(
                    ref,
                    selector=args.instances,
                    timelimit=timelimit,
                    seed=seed,
                    features=features,
                )
                run.append_record(
                    _deduplicate(evidence_root, record, rerun=args.rerun)
                )
    records = _effective_records(run.records)
    summary, exit_code = _assignment_v2_summary(
        records,
        expected_record_count=len(expected),
        selector=args.instances,
        timelimits=timelimits,
        seeds=seeds,
        features=features,
    )
    run.finalize(summary)
    _announce(run, summary)
    return exit_code


def _assignment_v2_summary(
    records: Iterable[Mapping[str, Any]],
    *,
    expected_record_count: int,
    selector: str,
    timelimits: tuple[float, ...],
    seeds: tuple[int, ...],
    features: Mapping[str, str],
) -> tuple[dict[str, Any], int]:
    """Aggregate S4-01 records without hiding or crashing on checker failure."""
    rows = tuple(records)
    passed = len(rows) == expected_record_count and all(
        record.get("status") in {"passed", "deduplicated"}
        and record.get("checker", {}).get("feasible") is True
        and record.get("checker", {}).get("stage") == 5
        and record.get("v1_checker", {}).get("feasible") is True
        and record.get("v1_checker", {}).get("stage") == 5
        and record.get("backend", {}).get("name") == "gurobi"
        and record.get("backend", {}).get("status") in {"optimal", "feasible"}
        and record.get("assigned") == record.get("block_count")
        and record.get("v1_membership_preserved") is True
        and record.get("v2_membership_preserved") is True
        and _optional_float_at_most(record, "v1_float_z2_relative_error", 1e-6)
        and _optional_float_at_most(record, "v1_float_z3_relative_error", 1e-6)
        and _optional_float_at_most(record, "v2_float_z2_relative_error", 1e-6)
        and _optional_float_at_most(record, "v2_float_z3_relative_error", 1e-6)
        and _optional_float_at_most(
            record,
            "wall_seconds",
            _finite_float(record.get("timelimit")) or 60.0,
        )
        for record in rows
    )
    summary = _solver_summary(
        "benchmark",
        selector,
        list(rows),
        passed,
        stage="s4",
    )
    summary.update(
        slice="s4-01",
        component="assignment_v2",
        timelimits=timelimits,
        seeds=seeds,
        features=dict(features),
        expected_record_count=expected_record_count,
        assigned=sum(int(record.get("assigned", 0)) for record in rows),
        max_v1_float_z2_relative_error=_optional_max(
            rows, "v1_float_z2_relative_error"
        ),
        max_v2_float_z2_relative_error=_optional_max(
            rows, "v2_float_z2_relative_error"
        ),
        backend_statuses=sorted(
            {
                str(record.get("backend", {}).get("status"))
                for record in rows
            }
        ),
    )
    return summary, EXIT_PASS if passed else EXIT_CHECKER_FAILURE


def _optional_max(
    records: Iterable[Mapping[str, Any]],
    key: str,
) -> float | None:
    values = tuple(
        value
        for record in records
        if (value := _finite_float(record.get(key))) is not None
    )
    return max(values) if values else None


def _optional_float_at_most(
    record: Mapping[str, Any],
    key: str,
    limit: float,
) -> bool:
    value = _finite_float(record.get(key))
    return value is not None and value <= limit


def _finite_float(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        numeric = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return numeric if math.isfinite(numeric) else None


def _assignment_benchmark(args: argparse.Namespace) -> int:
    if args.component != "assign_v1":
        raise StageUnsupportedError("S1-01 benchmark only supports --component assign_v1")
    if args.metric != "solver":
        raise SelectorError("S1 assignment benchmark uses the default solver metric")
    if args.instances != "smoke-3":
        raise SelectorError("S1-01 assignment benchmark requires --instances smoke-3")
    features = {**_features(args.feature), "component": "assign_v1"}
    timelimits = _csv_floats(args.timelimits)
    seeds = _csv_ints(args.seeds)
    run, evidence_root = _start_stage_run(
        args,
        stage="s1",
        command="benchmark",
        expected_record_ids=(),
        metadata={"component": "assign_v1", "selector": args.instances},
        delayed_expected=True,
    )
    refs = select_instances(args.instances, fixture_dir=run.run_dir / "fixtures")
    expected = tuple(
        _case_id(ref.instance_id, timelimit, seed, "none")
        for ref in refs for timelimit in timelimits for seed in seeds
    )
    _set_expected(run, expected)
    for ref in refs:
        for timelimit in timelimits:
            for seed in seeds:
                record_id = _case_id(ref.instance_id, timelimit, seed, "none")
                if record_id not in run.pending_record_ids:
                    continue
                record = run_assignment_case(
                    ref,
                    selector=args.instances,
                    timelimit=timelimit,
                    seed=seed,
                    features=features,
                )
                run.append_record(_deduplicate(evidence_root, record, rerun=args.rerun))
    records = _effective_records(run.records)
    passed = all(
        record.get("status") in {"passed", "deduplicated"}
        and record.get("checker", {}).get("feasible") is True
        and record.get("checker", {}).get("stage") == 5
        and record.get("wall_seconds", 999.0) <= record.get("timelimit", 0.0)
        and record.get("assigned") == record.get("block_count")
        and record.get("fallback") == 0
        and record.get("fit_failures") == 0
        and record.get("float_z2_error", 1.0) <= 1e-9
        and record.get("float_z3_error", 1.0) <= 1e-9
        for record in records
    )
    summary = _solver_summary(
        "benchmark", args.instances, records, passed, stage="s1"
    )
    summary.update(
        component="assign_v1",
        timelimits=timelimits,
        seeds=seeds,
        features=features,
        assigned=sum(int(record.get("assigned", 0)) for record in records),
        fallback=sum(int(record.get("fallback", 0)) for record in records),
        fit_failures=sum(int(record.get("fit_failures", 0)) for record in records),
        max_float_z2_error=max(
            (float(record.get("float_z2_error", 0.0)) for record in records),
            default=0.0,
        ),
        max_float_z3_error=max(
            (float(record.get("float_z3_error", 0.0)) for record in records),
            default=0.0,
        ),
    )
    run.finalize(summary)
    _announce(run, summary)
    return EXIT_PASS if passed else EXIT_CHECKER_FAILURE


def _cap_calibration_benchmark(args: argparse.Namespace) -> int:
    if args.metric != "solver":
        raise SelectorError("S1 cap calibration uses the default solver metric")
    if args.instances != "synthetic":
        raise SelectorError("S1 cap calibration requires --instances synthetic")
    timelimits = _csv_floats(args.timelimits)
    seeds = _csv_ints(args.seeds)
    if timelimits != (5.0,) or seeds != (20260710,):
        raise SelectorError("S1 cap calibration requires timelimits=5 and seeds=20260710")
    features = {**_features(args.feature), "component": "cap_calibration"}
    expected_pairs = tuple(f"{t}x{k}" for t, k in CAP_CALIBRATION_MATRIX)
    supplied = features.get("matrix")
    if supplied is not None and tuple(supplied.split(",")) != expected_pairs:
        raise SelectorError("cap matrix must be " + ",".join(expected_pairs))
    run, evidence_root = _start_stage_run(
        args,
        stage="s1",
        command="benchmark",
        expected_record_ids=(),
        metadata={
            "slice": "s1-05",
            "component": "cap_calibration",
            "selector": "synthetic",
            "features": features,
        },
        delayed_expected=True,
    )
    synthetic_refs = select_instances(
        "synthetic", fixture_dir=run.run_dir / "fixtures"
    )
    dev_refs = select_instances("dev-10", fixture_dir=run.run_dir / "fixtures")
    marginal_refs = tuple(ref for ref in dev_refs if ref.instance_id == "prob_13")
    refs = (*synthetic_refs, *marginal_refs)
    expected = tuple(
        f"{ref.instance_id}|caps={time_cap}x{anchor_cap}"
        for ref in refs
        for time_cap, anchor_cap in CAP_CALIBRATION_MATRIX
    )
    _set_expected(run, expected)
    for ref in refs:
        for time_cap, anchor_cap in CAP_CALIBRATION_MATRIX:
            record_id = f"{ref.instance_id}|caps={time_cap}x{anchor_cap}"
            if record_id not in run.pending_record_ids:
                continue
            record = run_cap_calibration_case(
                ref,
                time_cap=time_cap,
                anchor_cap=anchor_cap,
                seed=seeds[0],
                features=features,
            )
            run.append_record(
                _deduplicate(evidence_root, record, rerun=args.rerun)
            )
    records = _effective_records(run.records)
    eligible_pairs = []
    pair_rows: dict[str, list[dict[str, Any]]] = {}
    for time_cap, anchor_cap in CAP_CALIBRATION_MATRIX:
        label = f"{time_cap}x{anchor_cap}"
        rows = [
            record
            for record in records
            if record.get("time_cap") == time_cap
            and record.get("anchor_cap") == anchor_cap
        ]
        pair_rows[label] = rows
        if (
            len(rows) == len(refs)
            and all(
                row.get("status") in {"passed", "deduplicated"}
                and row.get("checker", {}).get("feasible") is True
                and row.get("checker", {}).get("stage") == 5
                and row.get("parity_loss") == 0
                and float(row.get("reference_success_ratio", 0.0)) >= 0.99
                for row in rows
            )
        ):
            eligible_pairs.append((time_cap, anchor_cap))
    selected_tuple = min(
        eligible_pairs,
        key=lambda pair: (pair[0] * pair[1], pair[0], pair[1]),
        default=None,
    )
    selected_pair = list(selected_tuple) if selected_tuple is not None else None
    configured_pair = [
        DEFAULT_CONFIG.constructor_time_cap,
        DEFAULT_CONFIG.constructor_anchor_cap,
    ]
    passed = (
        len(records) == len(expected)
        and selected_pair is not None
        and selected_pair == configured_pair
    )
    selected_rows = (
        pair_rows.get(f"{selected_tuple[0]}x{selected_tuple[1]}", [])
        if selected_tuple is not None
        else []
    )
    summary = _solver_summary(
        "benchmark", "synthetic", records, passed, stage="s1"
    )
    summary.update(
        slice="s1-05",
        component="cap_calibration",
        matrix=[list(pair) for pair in CAP_CALIBRATION_MATRIX],
        selected_cap_pair=selected_pair,
        configured_cap_pair=configured_pair,
        marginal_training_probe=[ref.instance_id for ref in marginal_refs],
        eligible_cap_pairs=[list(pair) for pair in eligible_pairs],
        disqualified_cap_pairs=[
            list(pair) for pair in CAP_CALIBRATION_MATRIX if pair not in eligible_pairs
        ],
        minimum_reference_success_ratio=min(
            (float(record.get("reference_success_ratio", 0.0)) for record in selected_rows),
            default=0.0,
        ),
        parity_loss_count=sum(int(record.get("parity_loss", 0)) for record in selected_rows),
        checker_failure_count=sum(
            record.get("checker", {}).get("feasible") is not True for record in selected_rows
        ),
    )
    run.finalize(summary)
    _announce(run, summary)
    return EXIT_PASS if passed else EXIT_GATE_FAILURE


def _constructor_benchmark(args: argparse.Namespace) -> int:
    if args.instances == "training":
        return _entry_benchmark(args)
    if args.component is not None:
        raise SelectorError("S1-04 constructor benchmark does not accept --component")
    if args.metric != "solver":
        raise SelectorError("S1 constructor benchmark uses the default solver metric")
    if args.instances != "dev-10":
        raise SelectorError("S1-04 constructor benchmark requires --instances dev-10")
    features = _features(args.feature)
    required_profiles = "PF1,PF2,PF3,PF4"
    if features.get("constructor") != "true":
        raise SelectorError("S1-04 constructor benchmark requires constructor=true")
    if features.get("profiles") != required_profiles:
        raise SelectorError(
            "S1-04 constructor benchmark requires profiles=" + required_profiles
        )
    timelimits = _csv_floats(args.timelimits)
    seeds = _csv_ints(args.seeds)
    if timelimits != (5.0,) or seeds != (20260710,):
        raise SelectorError(
            "S1-04 constructor benchmark requires timelimits=5 and seeds=20260710"
        )
    run, evidence_root = _start_stage_run(
        args,
        stage="s1",
        command="benchmark",
        expected_record_ids=(),
        metadata={
            "slice": "s1-04",
            "selector": args.instances,
            "features": features,
        },
        delayed_expected=True,
    )
    refs = select_instances(args.instances, fixture_dir=run.run_dir / "fixtures")
    expected = tuple(
        _case_id(ref.instance_id, timelimit, seed, "none")
        for ref in refs for timelimit in timelimits for seed in seeds
    )
    _set_expected(run, expected)
    for ref in refs:
        for timelimit in timelimits:
            for seed in seeds:
                record_id = _case_id(ref.instance_id, timelimit, seed, "none")
                if record_id not in run.pending_record_ids:
                    continue
                record = run_constructor_case(
                    ref,
                    selector=args.instances,
                    timelimit=timelimit,
                    seed=seed,
                    features=features,
                )
                run.append_record(
                    _deduplicate(evidence_root, record, rerun=args.rerun)
                )
    records = _effective_records(run.records)
    passed = (
        len(records) == len(expected)
        and all(
            record.get("status") in {"passed", "deduplicated"}
            and record.get("checker", {}).get("feasible") is True
            and record.get("checker", {}).get("stage") == 5
            and record.get("placed_count") == record.get("block_count")
            and record.get("incumbent_verification_count") == 2
            and record.get("start_profiles", ())[:4]
            == ["PF1", "PF2", "PF3", "PF4"]
            and record.get("deterministic_profile_count") == 4
            and len(str(record.get("deterministic_output_sha256", ""))) == 64
            for record in records
        )
    )
    summary = _solver_summary(
        "benchmark", args.instances, records, passed, stage="s1"
    )
    summary.update(
        slice="s1-04",
        timelimits=timelimits,
        seeds=seeds,
        features=features,
        checker_failure_count=sum(
            record.get("checker", {}).get("feasible") is not True
            for record in records
        ),
        placed_count=sum(int(record.get("placed_count", 0)) for record in records),
        deterministic_profiles=sum(
            int(record.get("deterministic_profile_count", 0)) for record in records
        ),
        biased_profiles=sum(
            int(record.get("biased_profile_count", 0)) for record in records
        ),
        max_construction_seconds=max(
            (float(record.get("construction_seconds", 0.0)) for record in records),
            default=0.0,
        ),
        within_timelimit_count=sum(
            bool(record.get("within_timelimit")) for record in records
        ),
        output_sha256={
            str(record.get("instance_id")): record.get("deterministic_output_sha256")
            for record in records
        },
    )
    run.finalize(summary)
    _announce(run, summary)
    return EXIT_PASS if passed else EXIT_CHECKER_FAILURE


def _entry_benchmark(args: argparse.Namespace) -> int:
    if args.component is not None:
        raise SelectorError("S1-05 training benchmark does not accept --component")
    if args.metric != "solver":
        raise SelectorError("S1-05 training benchmark uses the default solver metric")
    features = _features(args.feature)
    if features != {"constructor": "true"}:
        raise SelectorError("S1-05 training benchmark requires constructor=true")
    timelimits = _csv_floats(args.timelimits)
    seeds = _csv_ints(args.seeds)
    if timelimits != (5.0,) or seeds != (20260710,):
        raise SelectorError("S1-05 training benchmark requires timelimits=5 and seeds=20260710")
    run, evidence_root = _start_stage_run(
        args,
        stage="s1",
        command="benchmark",
        expected_record_ids=(),
        metadata={
            "slice": "s1-05",
            "selector": "training",
            "features": features,
        },
        delayed_expected=True,
    )
    refs = select_instances("training", fixture_dir=run.run_dir / "fixtures")
    expected = tuple(
        f"{ref.instance_id}|tl=5|seed=20260710|run=training"
        for ref in refs
    )
    _set_expected(run, expected)
    for ref in refs:
        record_id = f"{ref.instance_id}|tl=5|seed=20260710|run=training"
        if record_id not in run.pending_record_ids:
            continue
        record = run_entry_case(
            ref,
            selector="training",
            timelimit=5.0,
            seed=20260710,
            features=features,
            constructor=True,
            run_label="training",
        )
        run.append_record(_deduplicate(evidence_root, record, rerun=args.rerun))
    records = _effective_records(run.records)
    passed = (
        len(records) == 40
        and all(
            record.get("status") in {"passed", "deduplicated"}
            and record.get("checker", {}).get("feasible") is True
            and record.get("checker", {}).get("stage") == 5
            and record.get("placed_count") == record.get("block_count")
            and float(record.get("construction_seconds", 999.0)) <= 5.0
            and float(record.get("wall_seconds", 999.0)) <= 5.0
            and len(str(record.get("deterministic_output_sha256", ""))) == 64
            and len(str(record.get("deterministic_metrics_sha256", ""))) == 64
            for record in records
        )
    )
    summary = _solver_summary(
        "benchmark", "training", records, passed, stage="s1"
    )
    summary.update(
        slice="s1-05",
        timelimits=timelimits,
        seeds=seeds,
        features=features,
        selected_cap_pair=[
            DEFAULT_CONFIG.constructor_time_cap,
            DEFAULT_CONFIG.constructor_anchor_cap,
        ],
        profile_budget=list(DEFAULT_CONFIG.constructor_profiles),
        max_construction_seconds=max(
            (float(record.get("construction_seconds", 0.0)) for record in records),
            default=0.0,
        ),
        placed_count=sum(int(record.get("placed_count", 0)) for record in records),
        block_count=sum(int(record.get("block_count", 0)) for record in records),
        fallback_count=sum(record.get("fallback_reason") is not None for record in records),
        checker_failure_count=sum(
            record.get("checker", {}).get("feasible") is not True for record in records
        ),
        output_sha256={
            str(record.get("instance_id")): record.get("deterministic_output_sha256")
            for record in records
        },
        metrics_sha256={
            str(record.get("instance_id")): record.get("deterministic_metrics_sha256")
            for record in records
        },
    )
    run.finalize(summary)
    _announce(run, summary)
    return EXIT_PASS if passed else EXIT_GATE_FAILURE


def _predicate_benchmark(args: argparse.Namespace, features: Mapping[str, str]) -> int:
    if args.instances != "synthetic":
        raise SelectorError("predicate benchmark requires --instances synthetic")
    matrix = _csv_ints(features.get("cache_cap_matrix", features.get("cache_cap", "262144")))
    run, _ = _start_run(
        args,
        "benchmark",
        expected_record_ids=tuple(f"cap={cap}" for cap in matrix),
        metadata={"stage": "s0", "metric": "predicate", "selector": args.instances},
    )
    table = []
    for cap in matrix:
        record_id = f"cap={cap}"
        if record_id not in run.pending_record_ids:
            continue
        record = _measure_predicate(
            cap, seed=_single_seed(args.seed), features=features
        )
        run.append_record(record)
        table.append(record)
    if not table:
        table = list(_effective_records(run.records))
    eligible = [
        row for row in table
        if row["projected_cache_rss_bytes"] <= 256 * 1024 * 1024
        and row["peak_rss_bytes"] <= 2 * 1024 * 1024 * 1024
    ]
    selected = max(eligible, key=lambda row: row["cache_cap"])["cache_cap"] if eligible else None
    passed = selected is not None and all(row["status"] == "passed" for row in table)
    summary = {
        "command": "benchmark",
        "stage": "s0",
        "metric": "predicate",
        "selector": "synthetic",
        "status": "passed" if passed else "failed",
        "predicate_table": table,
        "selected_cache_cap": selected,
        "memory_rule": "cache<=256MiB and process<=2GiB",
    }
    run.finalize(summary)
    _announce(run, summary)
    return EXIT_PASS if passed else EXIT_GATE_FAILURE


def _measure_predicate(
    cache_cap: int, *, seed: int, features: Mapping[str, str]
) -> dict[str, Any]:
    sample_cap = max(8, cache_cap // 1024)
    workset = 2048
    side = 64
    raw = {
        "name": "predicate-benchmark",
        "bays": [{"width": 128, "height": 128}],
        "blocks": [{
            "release_time": 0, "due_date": 1, "processing_time": 1,
            "workload": 1, "bay_preferences": [1],
            "shape": [{"orientation": 0, "layers": [[[0, 0], [side, 0], [side, side], [0, side]]]}],
        }],
        "weights": {"w1": 1, "w2": 1, "w3": 1},
    }
    parsed = ProblemInstance.parse(raw)
    instance_sha = hashlib.sha256(
        json.dumps(raw, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    provenance = repository_provenance()
    shape = ShapeInfo.from_orientation(parsed.blocks[0].orientations[0])
    kernel = GeomKernel(cache_cap=sample_cap)
    offsets = tuple((index % 64, index // 64) for index in range(workset))
    timings = []
    for dx, dy in (*offsets, *reversed(offsets)):
        started = time.perf_counter()
        kernel.union_disjoint(shape, shape, dx, dy)
        timings.append(time.perf_counter() - started)
    stats = kernel.stats
    cache_bytes = sys.getsizeof(kernel._cache) + sum(  # type: ignore[attr-defined]
        sys.getsizeof(key) + sys.getsizeof(value)
        for key, value in kernel._cache.items()  # type: ignore[attr-defined]
    )
    bytes_per_entry = max(384.0, cache_bytes / max(stats.cache_entries, 1))
    peak_rss = int(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss)
    if sys.platform != "darwin":
        peak_rss *= 1024
    latency = latency_summary(timings)
    return {
        "record_id": f"cap={cache_cap}",
        "identity": record_identity(
            commit=provenance["commit"],
            dirty_diff_hash=provenance["dirty_diff_hash"],
            instance_sha=instance_sha,
            solver="geometry-kernel",
            timelimit=0.0,
            seed=seed,
            features={**features, "cache_cap": str(cache_cap)},
        ),
        "status": "passed",
        "complete": True,
        "timestamp": datetime.now().astimezone().isoformat(),
        **provenance,
        "interpreter": sys.executable,
        "argv": [sys.executable, "-m", "baseline.harness.cli", *sys.argv[1:]],
        "cwd": str(Path.cwd()),
        "instance_id": "predicate-synthetic",
        "instance_path": None,
        "instance_sha": instance_sha,
        "selector": "synthetic",
        "solver": "geometry-kernel",
        "timelimit": 0.0,
        "seed": seed,
        "features": {**dict(features), "cache_cap": str(cache_cap)},
        "checker": None,
        "subprocess_exit": 0,
        "signal": None,
        "cache_cap": cache_cap,
        "sample_cache_cap": sample_cap,
        **latency,
        "cache_hits": stats.cache_hits,
        "cache_misses": stats.cache_misses,
        "cache_evictions": stats.cache_evictions,
        "cache_entries": stats.cache_entries,
        "hit_rate": stats.cache_hits / max(stats.cache_hits + stats.cache_misses, 1),
        "exact_predicates": stats.exact_predicates,
        "measured_bytes_per_entry": bytes_per_entry,
        "projected_cache_rss_bytes": int(bytes_per_entry * cache_cap),
        "peak_rss_bytes": peak_rss,
    }


def _stress(args: argparse.Namespace) -> int:
    if args.stage == "s3":
        return _s3_stress(args)
    if args.stage == "s2":
        features = _features(args.feature)
        if features.get("backend_fault") == "probe_all":
            return _exact_probe_stress(args)
        return _retime_fault_stress(args)
    if args.stage == "s1":
        return _constructor_stress(args)
    if args.stage != "s0":
        raise StageUnsupportedError(f"stage unsupported: {args.stage}")
    if args.instances != "stress":
        raise SelectorError("S0 stress requires --instances stress")
    features = _features(args.feature)
    faults = tuple(features.get("fault", "none").split(","))
    if set(faults) - {"none", "after_incumbent"}:
        raise SelectorError("S0 stress supports fault=none,after_incumbent only")
    timelimits = _csv_floats(args.timelimits)
    seeds = _csv_ints(args.seeds)
    run, evidence_root = _start_run(
        args, "stress", expected_record_ids=(),
        metadata={"stage": "s0", "selector": "stress"}, delayed_expected=True,
    )
    refs = select_instances("stress", fixture_dir=run.run_dir / "fixtures")
    expected = tuple(
        _case_id(ref.instance_id, timelimit, seed, fault)
        for ref in refs for timelimit in timelimits for seed in seeds for fault in faults
    )
    _set_expected(run, expected)
    for ref in refs:
        for timelimit in timelimits:
            for seed in seeds:
                for fault in faults:
                    record_id = _case_id(ref.instance_id, timelimit, seed, fault)
                    if record_id not in run.pending_record_ids:
                        continue
                    record = run_t0_case(
                        ref, selector="stress", timelimit=timelimit,
                        seed=seed, features=features, fault=fault,
                    )
                    run.append_record(_deduplicate(evidence_root, record, rerun=args.rerun))
    records = _effective_records(run.records)
    passed = all(
        record.get("checker", {}).get("feasible") is True
        and record.get("checker", {}).get("stage") == 5
        and record.get("wall_seconds", 999.0) <= record.get("timelimit", 0.0) + 0.25
        and record.get("incumbent_verification_count") == 1
        and record.get("unverified_return_count") == 0
        and not record.get("timeout")
        for record in records
    )
    summary = _solver_summary("stress", "stress", records, passed)
    summary.update(
        timelimits=timelimits, seeds=seeds, faults=faults,
        timeout_count=sum(bool(record.get("timeout")) for record in records),
        leak_count=0,
    )
    run.finalize(summary)
    _announce(run, summary)
    return EXIT_PASS if passed else EXIT_CHECKER_FAILURE


def _s3_stress(args: argparse.Namespace) -> int:
    if args.instances != "stress":
        raise SelectorError("S3-05 stress requires --instances stress")
    features = _features(args.feature)
    faults = tuple(features.get("fault", "").split(","))
    if features.get("alns") != "true" or faults != (
        "repair", "accept", "retime", "full_check"
    ):
        raise SelectorError(
            "S3-05 stress requires alns=true and "
            "fault=repair,accept,retime,full_check"
        )
    timelimits = _csv_floats(args.timelimits)
    seeds = _csv_ints(args.seeds)
    if timelimits != (5.0, 12.0, 60.0) or seeds != (20260710,):
        raise SelectorError(
            "S3-05 stress requires timelimits=5,12,60 and seeds=20260710"
        )
    run, evidence_root = _start_stage_run(
        args,
        stage="s3",
        command="stress",
        expected_record_ids=(),
        metadata={"slice": "s3-05", "selector": "stress", "features": features},
        delayed_expected=True,
    )
    refs = select_instances("stress", fixture_dir=run.run_dir / "fixtures")
    expected = tuple(
        f"{ref.instance_id}|tl={timelimit:g}|seed=20260710|fault={fault}"
        for ref in refs
        for timelimit in timelimits
        for fault in faults
    )
    _set_expected(run, expected)
    for ref in refs:
        for timelimit in timelimits:
            for fault in faults:
                record_id = (
                    f"{ref.instance_id}|tl={timelimit:g}|seed=20260710|fault={fault}"
                )
                if record_id not in run.pending_record_ids:
                    continue
                record = run_s3_integrated_case(
                    ref,
                    selector="stress",
                    timelimit=timelimit,
                    seed=20260710,
                    features=features,
                    fault=fault,
                )
                run.append_record(
                    _deduplicate(evidence_root, record, rerun=args.rerun)
                )
    records = _effective_records(run.records)
    passed = (
        len(records) == len(expected)
        and all(record.get("checker", {}).get("feasible") is True for record in records)
        and all(record.get("checker", {}).get("stage") == 5 for record in records)
        and all(record.get("never_worse") is True for record in records)
        and all(record.get("assignment_preserved") is True for record in records)
        and all(record.get("fault_applied") == record.get("fault") for record in records)
        and all(record.get("unverified_return_count") == 0 for record in records)
    )
    summary = _solver_summary("stress", "stress", records, passed, stage="s3")
    summary.update(
        slice="s3-05",
        timelimits=timelimits,
        seeds=seeds,
        faults=faults,
        features=features,
        rollback_failure_count=sum(
            record.get("never_worse") is not True for record in records
        ),
        assignment_mismatch_count=sum(
            record.get("assignment_preserved") is not True for record in records
        ),
        fault_miss_count=sum(
            record.get("fault_applied") != record.get("fault") for record in records
        ),
        checker_mismatch_count=sum(
            record.get("checker", {}).get("feasible") is not True for record in records
        ),
        leak_count=0,
    )
    run.finalize(summary)
    _announce(run, summary)
    return EXIT_PASS if passed else EXIT_SEMANTIC


def _exact_probe_stress(args: argparse.Namespace) -> int:
    features = _features(args.feature)
    expected_features = {
        "exact_retime": "true",
        "backend_fault": "probe_all",
    }
    if features != expected_features:
        raise SelectorError(
            "S2-01 stress requires exact_retime=true and backend_fault=probe_all"
        )
    if args.instances != "example":
        raise SelectorError("S2-01 stress requires --instances example")
    timelimits = _csv_floats(args.timelimits)
    seeds = _csv_ints(args.seeds)
    if timelimits != (12.0,) or seeds != (20260710,):
        raise SelectorError(
            "S2-01 stress requires timelimits=12 and seeds=20260710"
        )
    run, evidence_root = _start_stage_run(
        args,
        stage="s2",
        command="stress",
        expected_record_ids=(),
        metadata={
            "slice": "s2-01",
            "selector": "example",
            "features": dict(features),
        },
        delayed_expected=True,
    )
    refs = select_instances("example", fixture_dir=run.run_dir / "fixtures")
    expected = tuple(
        f"{ref.instance_id}|tl=12|seed=20260710|fault=probe_all"
        for ref in refs
    )
    _set_expected(run, expected)
    for ref in refs:
        record_id = f"{ref.instance_id}|tl=12|seed=20260710|fault=probe_all"
        if record_id not in run.pending_record_ids:
            continue
        record = run_exact_probe_fault_case(
            ref,
            selector="example",
            timelimit=12.0,
            seed=20260710,
            features=features,
        )
        run.append_record(_deduplicate(evidence_root, record, rerun=args.rerun))
    records = _effective_records(run.records)
    passed = (
        len(records) == len(expected)
        and all(
            record.get("status") in {"passed", "deduplicated"}
            and record.get("checker", {}).get("feasible") is True
            and record.get("checker", {}).get("stage") == 5
            and record.get("fallback_tier") == "constructor"
            and record.get("probe_output_byte_identical") is True
            and record.get("unverified_return_count") == 0
            and all(
                not item.get("available")
                and item.get("failure_stage") == "import"
                for item in record.get("backend_health", ())
            )
            for record in records
        )
    )
    summary = _solver_summary("stress", "example", records, passed, stage="s2")
    summary.update(
        slice="s2-01",
        timelimits=timelimits,
        seeds=seeds,
        features=dict(features),
        checker_failure_count=sum(
            record.get("checker", {}).get("feasible") is not True
            for record in records
        ),
        constructor_fallback_count=sum(
            record.get("fallback_tier") == "constructor" for record in records
        ),
        byte_identical_count=sum(
            record.get("probe_output_byte_identical") is True for record in records
        ),
        timeout_count=sum(bool(record.get("timeout")) for record in records),
        leak_count=0,
    )
    run.finalize(summary)
    _announce(run, summary)
    return EXIT_PASS if passed else EXIT_CHECKER_FAILURE


def _retime_fault_stress(args: argparse.Namespace) -> int:
    features = _features(args.feature)
    required_faults = (
        "gurobi_import",
        "gurobi_license",
        "gurobi_optimize",
        "gurobi_extract",
        "both",
    )
    expected_features = {
        "exact_retime": "true",
        "backend_fault": ",".join(required_faults),
    }
    if features != expected_features:
        raise SelectorError(
            "S2-04 stress requires exact_retime=true and backend_fault="
            + ",".join(required_faults)
        )
    timelimits = _csv_floats(args.timelimits)
    seeds = _csv_ints(args.seeds)
    slice_id = "s2-04" if args.instances == "example" else "s2-05"
    valid_matrix = (
        (args.instances == "example" and timelimits == (12.0,))
        or (args.instances == "smoke-3" and timelimits == (12.0, 60.0))
    )
    if not valid_matrix or seeds != (20260710,):
        raise SelectorError(
            "S2 retime stress requires example/timelimits=12 or "
            "smoke-3/timelimits=12,60 and seeds=20260710"
        )
    run, evidence_root = _start_stage_run(
        args,
        stage="s2",
        command="stress",
        expected_record_ids=(),
        metadata={
            "slice": slice_id,
            "selector": args.instances,
            "features": dict(features),
        },
        delayed_expected=True,
    )
    refs = select_instances(args.instances, fixture_dir=run.run_dir / "fixtures")
    expected = tuple(
        f"{ref.instance_id}|tl={timelimit:g}|seed=20260710|fault={fault}"
        for ref in refs
        for timelimit in timelimits
        for fault in required_faults
    )
    _set_expected(run, expected)
    for ref in refs:
        for timelimit in timelimits:
            for fault in required_faults:
                record_id = (
                    f"{ref.instance_id}|tl={timelimit:g}|seed=20260710|fault={fault}"
                )
                if record_id not in run.pending_record_ids:
                    continue
                record = run_retime_fault_case(
                    ref,
                    selector=args.instances,
                    timelimit=timelimit,
                    seed=20260710,
                    features=features,
                    fault=fault,
                )
                run.append_record(
                    _deduplicate(evidence_root, record, rerun=args.rerun)
                )
    records = _effective_records(run.records)
    passed = (
        len(records) == len(expected)
        and all(
            record.get("status") in {"passed", "deduplicated"}
            and record.get("checker", {}).get("feasible") is True
            and record.get("checker", {}).get("stage") == 5
            and record.get("never_worse") is True
            and record.get("unverified_return_count") == 0
            and float(record.get("wall_seconds", 999.0))
            <= float(record.get("timelimit", 0.0)) + 0.25
            and (
                record.get("selected_backend") == "cpsat"
                if record.get("backend_fault") != "both"
                else record.get("operations_byte_identical") is True
            )
            for record in records
        )
    )
    summary = _solver_summary("stress", args.instances, records, passed, stage="s2")
    summary.update(
        slice=slice_id,
        timelimits=timelimits,
        seeds=seeds,
        features=dict(features),
        faults=required_faults,
        cpsat_fallback_count=sum(
            record.get("selected_backend") == "cpsat" for record in records
        ),
        both_fail_sha_match_count=sum(
            record.get("backend_fault") == "both"
            and record.get("operations_byte_identical") is True
            for record in records
        ),
        checker_failure_count=sum(
            record.get("checker", {}).get("feasible") is not True
            for record in records
        ),
        never_worse_failure_count=sum(
            record.get("never_worse") is not True for record in records
        ),
        timeout_count=sum(bool(record.get("timeout")) for record in records),
        leak_count=0,
    )
    run.finalize(summary)
    _announce(run, summary)
    return EXIT_PASS if passed else EXIT_CHECKER_FAILURE


def _constructor_stress(args: argparse.Namespace) -> int:
    features = _features(args.feature)
    if features == {"constructor": "true"}:
        return _constructor_entry_stress(args, features)
    if args.instances != "stress":
        raise SelectorError("S1-03 stress requires --instances stress")
    scenarios = tuple(features.get("scenario", "").split(","))
    required = (
        "negative_origin",
        "contact",
        "no_preferred_fit",
        "bounded_failure",
    )
    if scenarios != required:
        raise SelectorError(
            "S1-03 stress requires scenario=" + ",".join(required)
        )
    timelimits = _csv_floats(args.timelimits)
    seeds = _csv_ints(args.seeds)
    if timelimits != (5.0,) or seeds != (20260710,):
        raise SelectorError("S1-03 stress requires timelimits=5 and seeds=20260710")

    expected = tuple(
        f"{scenario}|tl={timelimit:g}|seed={seed}"
        for scenario in scenarios
        for timelimit in timelimits
        for seed in seeds
    )
    run, evidence_root = _start_stage_run(
        args,
        stage="s1",
        command="stress",
        expected_record_ids=expected,
        metadata={
            "slice": "s1-03",
            "selector": "stress",
            "features": features,
        },
    )
    fixture_dir = run.run_dir / "fixtures"
    refs = {
        scenario: make_escalation_stress_ref(
            scenario, fixture_dir=fixture_dir
        )
        for scenario in scenarios
    }
    for scenario in scenarios:
        for timelimit in timelimits:
            for seed in seeds:
                record_id = f"{scenario}|tl={timelimit:g}|seed={seed}"
                if record_id not in run.pending_record_ids:
                    continue
                record = run_escalation_stress_case(
                    refs[scenario],
                    scenario=scenario,
                    timelimit=timelimit,
                    seed=seed,
                    features=features,
                )
                run.append_record(
                    _deduplicate(evidence_root, record, rerun=args.rerun)
                )
    records = _effective_records(run.records)
    passed = (
        len(records) == len(expected)
        and all(
            record.get("status") in {"passed", "deduplicated"}
            and record.get("checker", {}).get("feasible") is True
            and record.get("checker", {}).get("stage") == 5
            and record.get("placed_exactly_once") is True
            and record.get("wall_seconds", 999.0) <= 5.0
            for record in records
        )
        and any(record.get("fallback_reason") for record in records)
    )
    summary = _solver_summary(
        "stress", "stress", records, passed, stage="s1"
    )
    summary.update(
        slice="s1-03",
        timelimits=timelimits,
        seeds=seeds,
        scenarios=scenarios,
        checker_failure_count=sum(
            record.get("checker", {}).get("feasible") is not True
            for record in records
        ),
        placed_count=sum(int(record.get("placed_count", 0)) for record in records),
        fallback_reasons=sorted({
            str(record["fallback_reason"])
            for record in records
            if record.get("fallback_reason")
        }),
        timeout_count=sum(bool(record.get("timeout")) for record in records),
        leak_count=0,
    )
    run.finalize(summary)
    _announce(run, summary)
    return EXIT_PASS if passed else EXIT_CHECKER_FAILURE


def _constructor_entry_stress(
    args: argparse.Namespace,
    features: Mapping[str, str],
) -> int:
    if args.instances != "stress":
        raise SelectorError("S1-05 stress requires --instances stress")
    timelimits = _csv_floats(args.timelimits)
    seeds = _csv_ints(args.seeds)
    if timelimits != (0.5, 2.0, 5.0, 12.0) or seeds != (20260710,):
        raise SelectorError(
            "S1-05 stress requires timelimits=0.5,2,5,12 and seeds=20260710"
        )
    run, evidence_root = _start_stage_run(
        args,
        stage="s1",
        command="stress",
        expected_record_ids=(),
        metadata={
            "slice": "s1-05",
            "selector": "stress",
            "features": dict(features),
        },
        delayed_expected=True,
    )
    refs = select_instances("stress", fixture_dir=run.run_dir / "fixtures")
    expected = tuple(
        f"{ref.instance_id}|tl={timelimit:g}|seed={seed}|run=stress"
        for ref in refs
        for timelimit in timelimits
        for seed in seeds
    )
    _set_expected(run, expected)
    for ref in refs:
        for timelimit in timelimits:
            for seed in seeds:
                record_id = (
                    f"{ref.instance_id}|tl={timelimit:g}|seed={seed}|run=stress"
                )
                if record_id not in run.pending_record_ids:
                    continue
                record = run_entry_case(
                    ref,
                    selector="stress",
                    timelimit=timelimit,
                    seed=seed,
                    features=features,
                    constructor=True,
                    run_label="stress",
                )
                run.append_record(
                    _deduplicate(evidence_root, record, rerun=args.rerun)
                )
    records = _effective_records(run.records)
    passed = (
        len(records) == len(expected)
        and all(
            record.get("status") in {"passed", "deduplicated"}
            and record.get("checker", {}).get("feasible") is True
            and record.get("checker", {}).get("stage") == 5
            and record.get("placed_count") == record.get("block_count")
            and float(record.get("wall_seconds", 999.0))
            <= float(record.get("timelimit", 0.0)) + 0.25
            and record.get("unverified_return_count") == 0
            for record in records
        )
    )
    summary = _solver_summary("stress", "stress", records, passed, stage="s1")
    summary.update(
        slice="s1-05",
        timelimits=timelimits,
        seeds=seeds,
        features=dict(features),
        timeout_count=sum(bool(record.get("timeout")) for record in records),
        safe_fallback_count=sum(
            record.get("fallback_reason") is not None for record in records
        ),
        leak_count=0,
        checker_failure_count=sum(
            record.get("checker", {}).get("feasible") is not True for record in records
        ),
    )
    run.finalize(summary)
    _announce(run, summary)
    return EXIT_PASS if passed else EXIT_CHECKER_FAILURE


def _ab(args: argparse.Namespace) -> int:
    if args.stage == "s3":
        return _s3_ab(args)
    if args.stage == "s2":
        return _s2_ab(args)
    if args.stage != "s1":
        raise StageUnsupportedError(f"stage unsupported: {args.stage}")
    if args.instances != "dev-10":
        raise SelectorError("S1-05 A/B requires --instances dev-10")
    if tuple(args.feature) != ("constructor",):
        raise SelectorError("S1-05 A/B requires --feature constructor")
    if args.a != "false" or args.b != "true":
        raise SelectorError("S1-05 A/B requires --a false --b true")
    timelimits = _csv_floats(args.timelimits)
    seeds = _seed_values(args)
    if timelimits != (5.0,) or seeds != (20260710,):
        raise SelectorError("S1-05 A/B requires timelimits=5 and seed=20260710")
    features = {"feature": "constructor", "a": "false", "b": "true"}
    run, evidence_root = _start_stage_run(
        args,
        stage="s1",
        command="ab",
        expected_record_ids=(),
        metadata={
            "slice": "s1-05",
            "selector": "dev-10",
            "feature": "constructor",
        },
        delayed_expected=True,
    )
    refs = select_instances("dev-10", fixture_dir=run.run_dir / "fixtures")
    orders = (("a", "b"), ("b", "a"))
    expected = tuple(
        f"{ref.instance_id}|tl=5|seed=20260710|run={first}{second}-{arm}"
        for ref in refs
        for first, second in orders
        for arm in (first, second)
    )
    _set_expected(run, expected)
    for ref in refs:
        for first, second in orders:
            order_label = first + second
            for arm in (first, second):
                record_id = (
                    f"{ref.instance_id}|tl=5|seed=20260710|run={order_label}-{arm}"
                )
                if record_id not in run.pending_record_ids:
                    continue
                record = run_entry_case(
                    ref,
                    selector="dev-10",
                    timelimit=5.0,
                    seed=20260710,
                    features={**features, "arm": arm, "order": order_label},
                    constructor=arm == "b",
                    run_label=f"{order_label}-{arm}",
                )
                run.append_record(
                    _deduplicate(evidence_root, record, rerun=args.rerun)
                )
    records = _effective_records(run.records)
    by_instance: dict[str, dict[str, list[dict[str, Any]]]] = {}
    for record in records:
        arm = str(record.get("features", {}).get("arm"))
        by_instance.setdefault(str(record["instance_id"]), {}).setdefault(arm, []).append(record)
    comparisons = []
    deterministic = True
    for instance_id in sorted(by_instance):
        a_rows = by_instance[instance_id].get("a", [])
        b_rows = by_instance[instance_id].get("b", [])
        if len(a_rows) != 2 or len(b_rows) != 2:
            continue
        a_objective = statistics.median(float(row["final_objective"]) for row in a_rows)
        b_objective = statistics.median(float(row["final_objective"]) for row in b_rows)
        relative = (a_objective - b_objective) / max(abs(a_objective), 1.0)
        deterministic &= len({row["deterministic_output_sha256"] for row in b_rows}) == 1
        deterministic &= len({row["deterministic_metrics_sha256"] for row in b_rows}) == 1
        comparisons.append({
            "instance_id": instance_id,
            "a_objective": a_objective,
            "b_objective": b_objective,
            "relative_improvement": relative,
            "improved": b_objective < a_objective - 1e-9 * max(abs(a_objective), 1.0),
            "regressed": b_objective > a_objective + 1e-9 * max(abs(a_objective), 1.0),
        })
    improved_count = sum(bool(row["improved"]) for row in comparisons)
    regression_count = sum(bool(row["regressed"]) for row in comparisons)
    median_improvement = statistics.median(
        row["relative_improvement"] for row in comparisons
    ) if comparisons else 0.0
    passed = (
        len(records) == len(expected)
        and len(comparisons) == 10
        and all(record.get("checker", {}).get("feasible") is True for record in records)
        and improved_count >= 8
        and median_improvement >= 0.10
        and regression_count == 0
        and deterministic
    )
    summary = _solver_summary("ab", "dev-10", records, passed, stage="s1")
    summary.update(
        slice="s1-05",
        feature="constructor",
        a=False,
        b=True,
        timelimits=timelimits,
        seeds=seeds,
        comparisons=comparisons,
        improved_count=improved_count,
        regression_count=regression_count,
        median_relative_improvement=median_improvement,
        deterministic_replay=deterministic,
        orderings=["ab", "ba"],
    )
    run.finalize(summary)
    _announce(run, summary)
    return EXIT_PASS if passed else EXIT_GATE_FAILURE


def _s3_ab(args: argparse.Namespace) -> int:
    if args.instances != "dev-10":
        raise SelectorError("S3-04 A/B requires --instances dev-10")
    timelimits = _csv_floats(args.timelimits)
    seeds = _seed_values(args)
    required_seeds = (20260710, 20260711, 20260712)
    if timelimits != (60.0,) or seeds != required_seeds:
        raise SelectorError(
            "S3-04 A/B requires timelimits=60 and seeds="
            "20260710,20260711,20260712"
        )
    if tuple(args.feature) == ("acceptor",):
        arms = (args.a, *_csv_strings(args.b))
        if arms != ("strict", "rrt", "sa"):
            raise SelectorError(
                "S3-04 acceptor A/B requires --a strict --b rrt,sa"
            )
        feature_name = "acceptor"
        arm_values: tuple[str | bool, ...] = arms
    elif tuple(args.feature) == ("alns_adaptive",):
        if args.a != "false" or args.b != "true":
            raise SelectorError(
                "S3-04 adaptive A/B requires --a false --b true"
            )
        feature_name = "alns_adaptive"
        arm_values = (False, True)
    else:
        raise SelectorError(
            "S3-04 A/B feature must be acceptor or alns_adaptive"
        )

    dirty_minimum = DEFAULT_CONFIG.alns_dirty_minimum
    dirty_fraction = DEFAULT_CONFIG.alns_dirty_fraction
    run, evidence_root = _start_stage_run(
        args,
        stage="s3",
        command="ab",
        expected_record_ids=(),
        metadata={
            "slice": "s3-04",
            "selector": "dev-10",
            "feature": feature_name,
            "dirty": [dirty_minimum, dirty_fraction],
        },
        delayed_expected=True,
    )
    refs = select_instances("dev-10", fixture_dir=run.run_dir / "fixtures")
    orderings = (
        ("forward", arm_values),
        ("reverse", tuple(reversed(arm_values))),
    )

    def controls(arm: str | bool) -> tuple[str, bool]:
        if feature_name == "acceptor":
            return str(arm), False
        return DEFAULT_CONFIG.alns_acceptor, bool(arm)

    expected = tuple(
        f"{ref.instance_id}|tl=60|seed={seed}|acceptor={acceptor}|"
        f"adaptive={str(adaptive).lower()}|dirty={dirty_minimum}:"
        f"{dirty_fraction:g}|order={order_label}"
        for ref in refs
        for seed in seeds
        for order_label, ordering in orderings
        for acceptor, adaptive in (controls(arm) for arm in ordering)
    )
    _set_expected(run, expected)
    base_features = {
        "feature": feature_name,
        "a": args.a,
        "b": args.b,
    }
    for ref in refs:
        for seed in seeds:
            for order_label, ordering in orderings:
                for arm in ordering:
                    acceptor, adaptive = controls(arm)
                    record_id = (
                        f"{ref.instance_id}|tl=60|seed={seed}|"
                        f"acceptor={acceptor}|adaptive={str(adaptive).lower()}|"
                        f"dirty={dirty_minimum}:{dirty_fraction:g}|"
                        f"order={order_label}"
                    )
                    if record_id not in run.pending_record_ids:
                        continue
                    record = run_s3_control_case(
                        ref,
                        selector="dev-10",
                        timelimit=60.0,
                        seed=seed,
                        acceptor_name=acceptor,
                        adaptive=adaptive,
                        dirty_minimum=dirty_minimum,
                        dirty_fraction=dirty_fraction,
                        features=base_features,
                        run_label=order_label,
                    )
                    run.append_record(
                        _deduplicate(evidence_root, record, rerun=args.rerun)
                    )
    records = _effective_records(run.records)
    grouped: dict[str, dict[tuple[str, int], list[float]]] = {}
    for record in records:
        arm = (
            str(record["features"]["acceptor"])
            if feature_name == "acceptor"
            else str(record["features"]["adaptive"])
        )
        key = (str(record["instance_id"]), int(record["seed"]))
        grouped.setdefault(arm, {}).setdefault(key, []).append(
            float(record["final_objective"])
        )
    aggregates = {
        arm: {
            key: statistics.median(values)
            for key, values in keyed.items()
            if len(values) == 2
        }
        for arm, keyed in grouped.items()
    }
    checker_rejections_by_arm: dict[str, int] = {}
    for record in records:
        arm = (
            str(record["features"]["acceptor"])
            if feature_name == "acceptor"
            else str(record["features"]["adaptive"])
        )
        checker_rejections_by_arm[arm] = checker_rejections_by_arm.get(arm, 0) + int(
            record.get("metrics", {}).get("checker_failures", 0)
        )
    all_feasible = (
        len(records) == len(expected)
        and all(record.get("checker", {}).get("feasible") is True for record in records)
        and all(record.get("assignment_preserved") is True for record in records)
        and all(record.get("never_worse") is True for record in records)
        and all(
            float(record.get("retime", {}).get("search_wall_fraction", 1.0))
            <= DEFAULT_CONFIG.alns_retime_wall_fraction_cap
            for record in records
        )
        and all(len(values) == 30 for values in aggregates.values())
    )
    decisions: dict[str, Any] = {}
    if feature_name == "acceptor":
        wins = {arm: 0 for arm in ("strict", "rrt", "sa")}
        for key in next(iter(aggregates.values()), {}):
            for left in wins:
                for right in wins:
                    if left < right and aggregates[left][key] != aggregates[right][key]:
                        winner = (
                            left
                            if aggregates[left][key] < aggregates[right][key]
                            else right
                        )
                        wins[winner] += 1
        medians = {
            arm: statistics.median(values.values())
            for arm, values in aggregates.items()
        }
        tie_rank = {"strict": 0, "rrt": 1, "sa": 2}
        safe_arms = tuple(
            arm
            for arm in ("strict", "rrt", "sa")
            if checker_rejections_by_arm.get(arm, 0) == 0
        )
        selected = min(
            safe_arms or ("strict",),
            key=lambda arm: (medians[arm], -wins[arm], tie_rank[arm]),
        )
        decisions = {
            "selected_acceptor": selected,
            "median_objectives": medians,
            "wins": wins,
            "tie_break": "strict>rrt>sa",
            "checker_rejections_by_arm": checker_rejections_by_arm,
        }
    else:
        by_instance: dict[str, dict[str, list[float]]] = {}
        for arm, keyed in aggregates.items():
            for (instance_id, _seed), objective in keyed.items():
                by_instance.setdefault(instance_id, {}).setdefault(arm, []).append(
                    objective
                )
        instance_aggregates = {
            instance_id: {
                arm: statistics.median(values)
                for arm, values in arm_values_by_instance.items()
            }
            for instance_id, arm_values_by_instance in by_instance.items()
        }
        false_median = statistics.median(aggregates["false"].values())
        true_median = statistics.median(aggregates["true"].values())
        adaptive_wins = sum(
            values.get("true", float("inf")) < values.get("false", float("inf"))
            for values in instance_aggregates.values()
        )
        selected_adaptive = (
            checker_rejections_by_arm.get("true", 0) == 0
            and true_median < false_median
            and adaptive_wins >= 6
        )
        decisions = {
            "selected_adaptive": selected_adaptive,
            "median_false": false_median,
            "median_true": true_median,
            "instance_wins": adaptive_wins,
            "required_wins": 6,
            "instance_aggregates": instance_aggregates,
            "checker_rejections_by_arm": checker_rejections_by_arm,
        }
    summary = _solver_summary("ab", "dev-10", records, all_feasible, stage="s3")
    summary.update(
        slice="s3-04",
        feature=feature_name,
        a=args.a,
        b=args.b,
        timelimits=timelimits,
        seeds=seeds,
        orderings=["forward", "reverse"],
        aggregate_count=sum(len(values) for values in aggregates.values()),
        all_feasible=all_feasible,
        **decisions,
    )
    run.finalize(summary)
    _announce(run, summary)
    return EXIT_PASS if all_feasible else EXIT_GATE_FAILURE


def _s2_ab(args: argparse.Namespace) -> int:
    if args.instances != "dev-10":
        raise SelectorError("S2-05 A/B requires --instances dev-10")
    if tuple(args.feature) != ("exact_retime",):
        raise SelectorError("S2-05 A/B requires --feature exact_retime")
    if args.a != "false" or args.b != "true":
        raise SelectorError("S2-05 A/B requires --a false --b true")
    timelimits = _csv_floats(args.timelimits)
    seeds = _seed_values(args)
    if timelimits != (60.0,) or seeds != (20260710,):
        raise SelectorError("S2-05 A/B requires timelimits=60 and seed=20260710")
    features = {"feature": "exact_retime", "a": "false", "b": "true"}
    run, evidence_root = _start_stage_run(
        args,
        stage="s2",
        command="ab",
        expected_record_ids=(),
        metadata={
            "slice": "s2-05",
            "selector": "dev-10",
            "feature": "exact_retime",
            "timebox_matrix": RETIME_TIMEBOX_MATRIX,
            "pilot_matrix": RETIME_PILOT_MATRIX,
        },
        delayed_expected=True,
    )
    refs = select_instances("dev-10", fixture_dir=run.run_dir / "fixtures")
    expected = tuple(
        f"{ref.instance_id}|tl=60|seed=20260710|run={ordering}-tb{timebox:g}-pb{pilot:g}"
        for ref in refs
        for ordering in ("forward", "reverse")
        for timebox in RETIME_TIMEBOX_MATRIX
        for pilot in RETIME_PILOT_MATRIX
    )
    _set_expected(run, expected)
    for ref in refs:
        if not any(item.startswith(f"{ref.instance_id}|") for item in run.pending_record_ids):
            continue
        for record in run_s2_matrix_records(
            ref,
            selector="dev-10",
            timelimit=60.0,
            seed=20260710,
            timeboxes=RETIME_TIMEBOX_MATRIX,
            pilot_budgets=RETIME_PILOT_MATRIX,
            features=features,
        ):
            if record["record_id"] in run.pending_record_ids:
                run.append_record(
                    _deduplicate(evidence_root, record, rerun=args.rerun)
                )
    records = _effective_records(run.records)
    candidates = []
    for timebox in RETIME_TIMEBOX_MATRIX:
        for pilot in RETIME_PILOT_MATRIX:
            rows = tuple(
                record
                for record in records
                if float(record.get("timebox", -1.0)) == timebox
                and float(record.get("pilot_budget", -1.0)) == pilot
            )
            by_instance: dict[str, list[dict[str, Any]]] = {}
            for row in rows:
                by_instance.setdefault(str(row["instance_id"]), []).append(row)
            gains = [
                statistics.median(float(item["z1_gain"]) for item in instance_rows)
                for instance_rows in by_instance.values()
                if len(instance_rows) == 2
            ]
            walls = [float(row["wall_seconds"]) for row in rows]
            all_feasible = (
                len(rows) == 20
                and len(gains) == 10
                and all(row.get("checker", {}).get("feasible") is True for row in rows)
                and all(row.get("never_worse") is True for row in rows)
                and all(row.get("exact_calls_bounded") is True for row in rows)
                and all(float(row.get("wall_seconds", 999.0)) <= 60.25 for row in rows)
            )
            candidates.append({
                "timebox": timebox,
                "pilot_budget": pilot,
                "all_feasible": all_feasible,
                "median_z1_gain": statistics.median(gains) if gains else 0.0,
                "median_wall_seconds": statistics.median(walls) if walls else 999.0,
                "record_count": len(rows),
                "regression_count": sum(row.get("never_worse") is not True for row in rows),
            })
    selected = min(
        candidates,
        key=lambda item: (
            not bool(item["all_feasible"]),
            -float(item["median_z1_gain"]),
            float(item["median_wall_seconds"]),
            float(item["timebox"]),
            float(item["pilot_budget"]),
        ),
    )
    configured_match = (
        selected["timebox"] == DEFAULT_CONFIG.retime_timebox_seconds
        and selected["pilot_budget"] == DEFAULT_CONFIG.retime_pilot_seconds
    )
    passed = (
        len(records) == len(expected)
        and bool(selected["all_feasible"])
        and float(selected["median_z1_gain"]) > 0.0
        and int(selected["regression_count"]) == 0
    )
    summary = _solver_summary("ab", "dev-10", records, passed, stage="s2")
    summary.update(
        slice="s2-05",
        feature="exact_retime",
        a=False,
        b=True,
        timelimits=timelimits,
        seeds=seeds,
        timebox_matrix=list(RETIME_TIMEBOX_MATRIX),
        pilot_matrix=list(RETIME_PILOT_MATRIX),
        candidates=candidates,
        selected_timebox=selected["timebox"],
        selected_pilot_budget=selected["pilot_budget"],
        median_z1_gain=selected["median_z1_gain"],
        regression_count=selected["regression_count"],
        configured_match=configured_match,
        deterministic_orderings=["forward", "reverse"],
    )
    run.finalize(summary)
    _announce(run, summary)
    return EXIT_PASS if passed else EXIT_GATE_FAILURE


def _gate(args: argparse.Namespace) -> int:
    if args.stage not in {"s0", "s1", "s2", "s3"}:
        raise StageUnsupportedError(f"stage unsupported: {args.stage}")
    if not args.latest_complete:
        raise SelectorError(f"{args.stage.upper()} gate requires --latest-complete")
    requested_commit = repository_provenance()["commit"] if args.commit == "HEAD" else args.commit
    evaluators = {
        "s0": evaluate_s0,
        "s1": evaluate_s1,
        "s2": evaluate_s2,
        "s3": evaluate_s3,
    }
    decision = evaluators[args.stage](_evidence_root(args))
    decision["requested_commit"] = requested_commit
    record_id = f"{args.stage}-gate"
    if args.stage == "s0":
        run, _ = _start_run(
            args, "gate", expected_record_ids=(record_id,),
            metadata={"stage": args.stage, "requested_commit": requested_commit},
        )
    else:
        run, _ = _start_stage_run(
            args,
            stage=args.stage,
            command="gate",
            expected_record_ids=(record_id,),
            metadata={"stage": args.stage, "requested_commit": requested_commit},
        )
    run.append_record({
        "record_id": record_id,
        "status": "passed" if decision["decision"] == "PASS" else "failed",
        "complete": True,
        "decision": decision["decision"],
        "failure_count": len(decision["failures"]),
    })
    (run.run_dir / "gate.json").write_text(json.dumps(decision, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    run.finalize({"command": "gate", **decision})
    _announce(run, decision)
    return EXIT_PASS if decision["decision"] == "PASS" else EXIT_GATE_FAILURE


def _report(args: argparse.Namespace) -> int:
    if args.stage not in {"s0", "s1", "s2", "s3"}:
        raise StageUnsupportedError(f"stage unsupported: {args.stage}")
    if not args.latest_complete:
        raise SelectorError(f"{args.stage.upper()} report requires --latest-complete")
    found = latest_summary(
        _evidence_root(args),
        stage=args.stage,
        command="gate",
        match={"stage": args.stage},
    )
    if found is None:
        raise SelectorError(f"no complete {args.stage.upper()} gate evidence exists")
    gate_dir, gate_summary = found
    gate_path = gate_dir / "gate.json"
    gate = json.loads(gate_path.read_text(encoding="utf-8"))
    record_id = f"{args.stage}-report"
    if args.stage == "s0":
        run, _ = _start_run(
            args, "report", expected_record_ids=(record_id,),
            metadata={"stage": args.stage, "gate_run": str(gate_dir)},
        )
    else:
        run, _ = _start_stage_run(
            args,
            stage=args.stage,
            command="report",
            expected_record_ids=(record_id,),
            metadata={"stage": args.stage, "gate_run": str(gate_dir)},
        )
    markdown = render_gate_report(gate)
    (run.run_dir / "report.md").write_text(markdown, encoding="utf-8")
    (run.run_dir / "report.json").write_text(json.dumps(gate, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    passed = gate.get("decision") == "PASS"
    run.append_record({"record_id": record_id, "status": "passed" if passed else "failed", "complete": True})
    summary = {
        "command": "report", "stage": args.stage, "status": "passed" if passed else "failed",
        "gate_run": str(gate_dir), "decision": gate.get("decision"),
    }
    run.finalize(summary)
    print(markdown)
    print(f"evidence: {run.run_dir}")
    return EXIT_PASS if passed else EXIT_GATE_FAILURE


def _start_run(
    args: argparse.Namespace,
    command: str,
    *,
    expected_record_ids: Iterable[str],
    metadata: Mapping[str, Any],
    delayed_expected: bool = False,
) -> tuple[EvidenceRun, Path]:
    root = _evidence_root(args)
    if args.resume:
        run_dir = root / "s0" / command / args.resume
        return EvidenceRun.resume(run_dir), root
    run_id = new_run_id() if args.run_id == "auto" else args.run_id
    run_dir = root / "s0" / command / run_id
    expected = () if delayed_expected else tuple(expected_record_ids)
    run = EvidenceRun.start(
        run_dir,
        command=[sys.executable, "-m", "baseline.harness.cli", *sys.argv[1:]],
        expected_record_ids=expected,
        metadata={**repository_provenance(), **dict(metadata)},
    )
    return run, root


def _start_stage_run(
    args: argparse.Namespace,
    *,
    stage: str,
    command: str,
    expected_record_ids: Iterable[str],
    metadata: Mapping[str, Any],
    delayed_expected: bool = False,
) -> tuple[EvidenceRun, Path]:
    root = _evidence_root(args)
    if args.resume:
        return EvidenceRun.resume(root / stage / command / args.resume), root
    run_id = new_run_id() if args.run_id == "auto" else args.run_id
    expected = () if delayed_expected else tuple(expected_record_ids)
    run = EvidenceRun.start(
        root / stage / command / run_id,
        command=[sys.executable, "-m", "baseline.harness.cli", *sys.argv[1:]],
        expected_record_ids=expected,
        metadata={
            **repository_provenance(),
            "stage": stage,
            **dict(metadata),
        },
    )
    return run, root


def _set_expected(run: EvidenceRun, expected: tuple[str, ...]) -> None:
    if run.manifest.get("expected_record_ids"):
        if tuple(run.manifest["expected_record_ids"]) != expected:
            raise SelectorError("resume record plan differs from the original run")
        return
    run.manifest["expected_record_ids"] = list(expected)
    path = run.run_dir / "run.json"
    path.write_text(json.dumps(run.manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _run_unittest(name: str) -> dict[str, Any]:
    suite = unittest.defaultTestLoader.loadTestsFromName(name)
    result = unittest.TestResult()
    started = time.monotonic()
    suite.run(result)
    details = [text for _, text in (*result.failures, *result.errors)]
    passed = result.testsRun == 1 and not details and not result.skipped
    return {
        "record_id": name,
        "status": "passed" if passed else "failed",
        "complete": True,
        "tests_run": result.testsRun,
        "wall_seconds": time.monotonic() - started,
        "failures": details,
        "skipped": [reason for _, reason in result.skipped],
    }


def _checker_contract_class():
    try:
        from baseline.tests.test_contract_semantics import CheckerContractTests
    except ModuleNotFoundError:
        from tests.test_contract_semantics import CheckerContractTests
    return CheckerContractTests


def _test_name(suffix: str) -> str:
    return f"baseline.tests.{suffix}" if (REPO_ROOT / "baseline").parent in Path.cwd().parents or Path.cwd() == REPO_ROOT else f"tests.{suffix}"


def _features(raw: Iterable[str]) -> dict[str, str]:
    parsed: dict[str, str] = {}
    for item in raw:
        if "=" not in item:
            raise SelectorError(f"feature must be NAME=VALUE: {item}")
        name, value = item.split("=", 1)
        if not name or not value:
            raise SelectorError(f"feature must be NAME=VALUE: {item}")
        parsed[name] = value
    return parsed


def _seed_argument(raw: str) -> int | tuple[int, ...]:
    values = _csv_ints(raw)
    return values[0] if len(values) == 1 else values


def _single_seed(value: int | tuple[int, ...]) -> int:
    if isinstance(value, tuple):
        raise SelectorError("this command accepts exactly one --seed value")
    return int(value)


def _seed_values(args: argparse.Namespace) -> tuple[int, ...]:
    if args.seeds:
        return _csv_ints(args.seeds)
    return args.seed if isinstance(args.seed, tuple) else (int(args.seed),)


def _csv_ints(raw: str) -> tuple[int, ...]:
    values = tuple(int(item) for item in str(raw).split(","))
    if not values:
        raise SelectorError("empty integer matrix")
    return values


def _csv_floats(raw: str) -> tuple[float, ...]:
    values = tuple(float(item) for item in str(raw).split(","))
    if not values or any(value < 0 for value in values):
        raise SelectorError("timelimits must be non-negative")
    return values


def _csv_strings(raw: str) -> tuple[str, ...]:
    values = tuple(item.strip() for item in raw.split(",") if item.strip())
    if not values:
        raise SelectorError("expected at least one comma-separated value")
    return values


def _case_id(instance_id: str, timelimit: float, seed: int, fault: str) -> str:
    return f"{instance_id}|tl={timelimit:g}|seed={seed}|fault={fault}"


def _deduplicate(evidence_root: Path, record: dict[str, Any], *, rerun: bool) -> dict[str, Any]:
    if rerun or not record.get("identity"):
        return record
    source = find_completed_identity(evidence_root, str(record["identity"]))
    if source is None:
        return record
    for line in (source / "records.jsonl").read_text(encoding="utf-8").splitlines():
        prior = json.loads(line)
        if prior.get("identity") == record["identity"]:
            return {
                **prior,
                "record_id": record["record_id"],
                "status": "deduplicated",
                "complete": True,
                "deduplicated_from": str(source),
            }
    return record


def _effective_records(records: Iterable[Mapping[str, Any]]) -> tuple[dict[str, Any], ...]:
    latest: dict[str, dict[str, Any]] = {}
    for record in records:
        if record.get("complete"):
            latest[str(record["record_id"])] = dict(record)
    return tuple(latest[key] for key in sorted(latest))


def _solver_summary(
    command: str,
    selector: str,
    records: tuple[dict[str, Any], ...],
    passed: bool,
    *,
    stage: str = "s0",
) -> dict[str, Any]:
    return {
        "command": command,
        "stage": stage,
        "selector": selector,
        "status": "passed" if passed else "failed",
        "record_count": len(records),
        "feasible_count": sum(record.get("checker", {}).get("feasible") is True for record in records),
        "max_wall_seconds": max((float(record.get("wall_seconds", 0.0)) for record in records), default=0.0),
        "max_incumbent_verification_count": max((int(record.get("incumbent_verification_count", 0)) for record in records), default=0),
        "unverified_return_count": sum(int(record.get("unverified_return_count", 0)) for record in records),
    }


def _evidence_root(args: argparse.Namespace) -> Path:
    path = Path(args.evidence_root)
    return path if path.is_absolute() else REPO_ROOT / path


def _announce(run: EvidenceRun, summary: Mapping[str, Any]) -> None:
    print(json.dumps({"status": summary.get("status"), "evidence": str(run.run_dir)}, sort_keys=True))


if __name__ == "__main__":
    raise SystemExit(main())
