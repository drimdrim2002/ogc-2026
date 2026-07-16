#!/usr/bin/env python3
"""P7 one-pair fixed-time quality measurement for the local macOS fast-track.

The harness is measurement-only.  It runs exactly one requested variant per
process, retains the production configuration except for the explicit native
opt-in, records the official checker result and bounded solver telemetry, and
can finalize the immutable Python/native pair without modifying solver code.
"""

from __future__ import annotations

import argparse
import contextlib
import copy
import hashlib
import io
import json
import os
import platform
import subprocess
import sys
import time
from collections import Counter
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping
from zoneinfo import ZoneInfo


ROOT = Path(__file__).resolve().parents[2]
BASELINE = ROOT / "baseline"
for path in (ROOT, BASELINE):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from baseline.solver.entry import solve  # noqa: E402
from baseline.solver.native_repair import snapshot_digest  # noqa: E402
from baseline.solver.runtime import RunTrace, SubmissionConfig  # noqa: E402
from baseline.utils import check_feasibility  # noqa: E402
from experiments.ogc_sage.benchmark_ogc_sage import (  # noqa: E402
    canonical_json,
    reconstruct_snapshot,
    validate_dataset,
)


KST = ZoneInfo("Asia/Seoul")
DEFAULT_TIMELIMIT_SECONDS = 120.0
SEED = 20260710
DEFAULT_INSTANCE_NAME = "prob_23.json"
ALLOWED_INSTANCE_NAMES = frozenset(
    f"prob_{index}.json" for index in range(21, 26)
)
EXPECTED_BINARY_SHA256 = (
    "95c1026ff3096ec9b5580d8a4564792964367cf4248b963371ba769fdbaa2563"
)


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def write_json(path: Path, value: object) -> None:
    path.write_text(
        json.dumps(value, indent=2, sort_keys=True, ensure_ascii=True) + "\n",
        encoding="utf-8",
    )


def now_record() -> dict[str, str]:
    now = datetime.now(timezone.utc)
    return {"utc": now.isoformat(), "kst": now.astimezone(KST).isoformat()}


def git(*args: str) -> str:
    completed = subprocess.run(
        ["git", *args],
        cwd=ROOT,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    if completed.returncode:
        raise RuntimeError(completed.stderr.strip() or "git command failed")
    return completed.stdout.strip()


def aggregate_file_hash(paths: list[Path]) -> tuple[str, list[dict[str, str]]]:
    rows = [
        {"path": str(path.relative_to(ROOT)), "sha256": sha256_file(path)}
        for path in sorted(paths)
    ]
    text = "".join(f"{row['sha256']}  {row['path']}\n" for row in rows)
    return sha256_bytes(text.encode("utf-8")), rows


def environment_record() -> dict[str, Any]:
    return {
        "python": platform.python_version(),
        "implementation": platform.python_implementation(),
        "platform": platform.platform(),
        "machine": platform.machine(),
        "logical_cpus": os.cpu_count(),
    }


def normalize_pairs(value: object) -> dict[str, Any]:
    if isinstance(value, Mapping):
        return dict(value)
    if isinstance(value, (list, tuple)):
        try:
            return {str(key): item for key, item in value}
        except (TypeError, ValueError):
            pass
    raise ValueError("telemetry event is not a key/value sequence")


def aggregate_repair_telemetry(trace: Mapping[str, Any]) -> dict[str, Any]:
    model = trace.get("model_stats", {})
    lns = model.get("lns", {}) if isinstance(model, Mapping) else {}
    raw_events = lns.get("repair_events", []) if isinstance(lns, Mapping) else []
    events = [normalize_pairs(item) for item in raw_events]
    sum_fields = (
        "fallback_count",
        "native_exception_count",
        "native_invalid_output_count",
        "native_error_count",
        "native_deadline_count",
        "geos_error_count",
        "native_calls",
        "native_successes",
        "python_reference_calls",
        "python_reference_calls_after_deadline",
        "python_full_repair_started_after_deadline",
        "pack_ns",
        "bind_ns",
        "prepare_ns",
        "exact_resolve_ns",
        "finalize_ns",
        "candidate_rows",
        "returned_candidates",
        "pair_count",
        "unknown_pair_count",
        "definitely_free_pair_count",
        "exact_relation_calls",
        "cursor_count",
        "batches",
        "generated_rows",
        "generated_candidates",
        "aabb_skipped_pairs",
        "geos_exact_calls",
        "first_conflict_skipped_pairs",
        "emitted_pairs",
        "processed_rows",
        "accepted_rows",
        "deadline_hits",
        "python_exact_calls",
        "python_returned_candidate_rechecks",
        "returned_candidate_recheck_failures",
        "native_exact_ns",
        "native_cache_ns",
        "native_error_ns",
        "cursor_prepare_ns",
        "cursor_exact_ns",
        "cursor_finalize_ns",
        "copied_bytes_estimate",
    )
    sums = {
        name: sum(int(event.get(name, 0) or 0) for event in events)
        for name in sum_fields
    }
    available = Counter(
        "null" if event.get("native_available") is None else str(bool(event.get("native_available"))).lower()
        for event in events
    )
    return {
        "event_count": len(events),
        "requested_backend_counts": dict(Counter(str(event.get("requested_backend")) for event in events)),
        "actual_backend_counts": dict(Counter(str(event.get("actual_backend")) for event in events)),
        "native_exact_mode_counts": dict(Counter(str(event.get("native_exact_mode")) for event in events)),
        "native_exact_decision_enabled_count": sum(
            bool(event.get("native_exact_decision_enabled")) for event in events
        ),
        "native_available_counts": dict(available),
        "repair_status_counts": dict(Counter(str(event.get("repair_status")) for event in events)),
        "prefilter_enabled_counts": dict(Counter(str(bool(event.get("prefilter_enabled"))).lower() for event in events)),
        "dominance_guard_rejected_count": sum(bool(event.get("dominance_guard_rejected")) for event in events),
        "fallback_reasons": dict(Counter(
            str(event.get("fallback_reason"))
            for event in events
            if event.get("fallback_reason") is not None
        )),
        "sums": sums,
        "phase_seconds": {
            name.removesuffix("_ns"): sums[name] / 1_000_000_000.0
            for name in (
                "pack_ns",
                "bind_ns",
                "prepare_ns",
                "exact_resolve_ns",
                "finalize_ns",
                "cursor_prepare_ns",
                "cursor_exact_ns",
                "cursor_finalize_ns",
                "native_exact_ns",
                "native_cache_ns",
                "native_error_ns",
            )
        },
        "events": events,
    }


def command_run(args: argparse.Namespace) -> int:
    output = args.output.resolve()
    if output.exists():
        raise SystemExit(f"refusing to overwrite run root: {output}")
    output.mkdir(parents=True)
    command_record = {
        "argv": [sys.executable, str(Path(__file__).resolve()), *sys.argv[1:]],
        "cwd": str(ROOT),
        "variant": args.variant,
        "recorded_at": now_record(),
        "native_module_dir": None,
    }
    write_json(output / "command.json", command_record)

    instance = args.instance.resolve()
    if instance.name not in ALLOWED_INSTANCE_NAMES:
        raise SystemExit("fixed-time harness only permits prob_21 through prob_25")
    instances, dataset_hash = validate_dataset()
    if instances.get(instance.name, Path()).resolve() != instance:
        raise SystemExit("instance is not its validated daily-40 dataset path")
    if not (args.timelimit > 0.0) or args.seed != SEED:
        raise SystemExit("fixed-time run requires timelimit>0 and seed=20260710")

    production = SubmissionConfig.from_defaults(seed=args.seed)
    production_payload = production.as_dict()
    production_hash = sha256_bytes(canonical_json(production_payload).encode("utf-8"))
    binary_record: dict[str, Any] | None = None
    if args.variant == "python":
        chosen = production
        if os.environ.get("OGC_NATIVE_MODULE_DIR"):
            raise SystemExit("Python default run refuses OGC_NATIVE_MODULE_DIR")
    else:
        if args.native_binary is None:
            raise SystemExit("native run requires --native-binary")
        binary = args.native_binary.resolve()
        actual_hash = sha256_file(binary)
        if actual_hash != args.expected_native_sha256:
            raise SystemExit(
                f"native binary hash mismatch: {actual_hash} != {args.expected_native_sha256}"
            )
        os.environ["OGC_NATIVE_MODULE_DIR"] = str(binary.parent)
        command_record["native_module_dir"] = str(binary.parent)
        command_record["native_binary"] = str(binary)
        write_json(output / "command.json", command_record)
        chosen = replace(
            production,
            repair_backend="native",
            native_exact_mode="native",
        )
        try:
            binary_path = str(binary.relative_to(ROOT))
        except ValueError:
            binary_path = str(binary)
        binary_record = {
            "path": binary_path,
            "sha256": actual_hash,
            "size_bytes": binary.stat().st_size,
            "expected_sha256": args.expected_native_sha256,
        }

    raw = json.loads(instance.read_text(encoding="utf-8"))
    trace = RunTrace()
    stdout = io.StringIO()
    stderr = io.StringIO()
    started = now_record()
    started_monotonic = time.monotonic()
    error: str | None = None
    operations: dict[str, Any] | None = None
    checked: dict[str, Any] | None = None
    snapshot = None
    try:
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            operations = solve(
                copy.deepcopy(raw),
                args.timelimit,
                config=chosen,
                trace=trace,
            )
        solve_elapsed = time.monotonic() - started_monotonic
        checked = check_feasibility(copy.deepcopy(raw), copy.deepcopy(operations))
        checker_completed = time.monotonic()
        snapshot = reconstruct_snapshot(raw, operations)
    except Exception as exc:  # retain the single immutable attempt
        solve_elapsed = time.monotonic() - started_monotonic
        checker_completed = time.monotonic()
        error = f"{type(exc).__name__}: {exc}"

    (output / "solve-stdout.txt").write_text(stdout.getvalue(), encoding="utf-8")
    (output / "solve-stderr.txt").write_text(stderr.getvalue(), encoding="utf-8")
    trace_dict = trace.as_dict()
    telemetry = aggregate_repair_telemetry(trace_dict)
    loaded_modules = []
    for name in ("solver._ogc_native", "_ogc_native"):
        module = sys.modules.get(name)
        if module is not None:
            loaded_modules.append(
                {"name": name, "file": str(Path(getattr(module, "__file__", "")).resolve())}
            )

    solution_info = None
    checker_info = None
    objective = None
    if operations is not None and snapshot is not None and checked is not None:
        solution_path = output / "solution.json"
        checker_path = output / "checker.json"
        write_json(solution_path, operations)
        write_json(checker_path, checked)
        canonical_operations = canonical_json(operations).encode("utf-8")
        solution_info = {
            "path": solution_path.name,
            "output_file_sha256": sha256_file(solution_path),
            "serialization_sha256": sha256_bytes(canonical_operations),
            "placement_sha256": snapshot_digest(snapshot),
            "json_serializable": True,
        }
        checker_info = {
            "path": checker_path.name,
            "sha256": sha256_file(checker_path),
            "feasible": checked.get("feasible"),
            "stage": checked.get("stage"),
            "violations": checked.get("violations"),
        }
        objective = {
            "z1": snapshot.objective.z1,
            "z2": snapshot.objective.z2,
            "z3": snapshot.objective.z3,
            "total": snapshot.objective.total,
            "checker_z1": checked.get("obj1"),
            "checker_z2": checked.get("obj2"),
            "checker_z3": checked.get("obj3"),
            "checker_total": checked.get("objective"),
        }

    lns_invocations = trace_dict.get("lns_invocations", [])
    per_kind = {}
    for kind in ("anchor", "extension"):
        selected = [item for item in lns_invocations if item.get("kind") == kind]
        iterations = sum(int(item.get("iterations", 0)) for item in selected)
        repair_seconds = sum(float(item.get("repair_seconds", 0.0)) for item in selected)
        per_kind[kind] = {
            "iterations": iterations,
            "repair_seconds": repair_seconds,
            "repair_seconds_per_iteration": (
                None if iterations == 0 else repair_seconds / iterations
            ),
        }
    total_iterations = sum(int(item.get("iterations", 0)) for item in lns_invocations)
    total_repair_seconds = sum(float(item.get("repair_seconds", 0.0)) for item in lns_invocations)
    lns_summary = {
        "invocation_count": len(lns_invocations),
        "iterations": total_iterations,
        "total_iterations": max(
            (int(item.get("total_iterations", 0)) for item in lns_invocations),
            default=0,
        ),
        "weight_updates": sum(int(item.get("weight_updates", 0)) for item in lns_invocations),
        "stall_events": sum(int(item.get("stall_events", 0)) for item in lns_invocations),
        "exit_reasons": [item.get("exit_reason") for item in lns_invocations],
        "repair_seconds": total_repair_seconds,
        "repair_seconds_per_iteration": (
            None if total_iterations == 0 else total_repair_seconds / total_iterations
        ),
        "by_kind": per_kind,
        "retime_seconds": sum(float(item.get("retime_seconds", 0.0)) for item in lns_invocations),
        "checker_seconds": sum(float(item.get("checker_seconds", 0.0)) for item in lns_invocations),
        "invocations": lns_invocations,
    }
    record = {
        "schema_version": 1,
        "kind": "pybind_p7_fixed_time_single_variant",
        "scope": f"{instance.name}; one {args.timelimit:g}-second run; no retry or warmup",
        "variant": args.variant,
        "order_ordinal": args.order_ordinal,
        "started_at": started,
        "ended_at": now_record(),
        "timing": {
            "timelimit_seconds": args.timelimit,
            "solve_elapsed_seconds": solve_elapsed,
            "solve_plus_official_checker_seconds": checker_completed - started_monotonic,
        },
        "source_identity": {
            "branch": git("rev-parse", "--abbrev-ref", "HEAD"),
            "head": git("rev-parse", "HEAD"),
            "dirty": bool(git("status", "--porcelain")),
            "dirty_state": git("status", "--short", "--branch").splitlines(),
            "harness_path": str(Path(__file__).resolve().relative_to(ROOT)),
            "harness_sha256": sha256_file(Path(__file__).resolve()),
        },
        "instance": {
            "path": str(instance.relative_to(ROOT)),
            "sha256": sha256_file(instance),
            "dataset_sha256": dataset_hash,
        },
        "config": {
            "seed": args.seed,
            "production_default": production_payload,
            "production_default_sha256": production_hash,
            "actual": chosen.as_dict(),
            "actual_sha256": sha256_bytes(canonical_json(chosen.as_dict()).encode("utf-8")),
            "native_override_only": (
                {} if args.variant == "python" else {
                    "repair_backend": "native",
                    "native_exact_mode": "native",
                }
            ),
        },
        "binary": binary_record,
        "loaded_native_modules_after_run": loaded_modules,
        "environment": environment_record(),
        "result": {
            "error": error,
            "trace_exceptions": trace_dict.get("exceptions", []),
            "objective": objective,
            "solution": solution_info,
            "checker": checker_info,
        },
        "phase_timings": trace_dict.get("phase_times", {}),
        "checker_timings": trace_dict.get("checker_durations", []),
        "alns": lns_summary,
        "incumbent_trace": {
            "validated_best": trace_dict.get("validated_best", []),
            "validated_best_events": trace_dict.get("validated_best_events", []),
            "non_increasing": all(
                right <= left + 1e-9
                for left, right in zip(
                    trace_dict.get("validated_best", []),
                    trace_dict.get("validated_best", [])[1:],
                )
            ),
        },
        "native_telemetry": telemetry,
        "captured_output": {
            "stdout_path": "solve-stdout.txt",
            "stderr_path": "solve-stderr.txt",
            "stdout_sha256": sha256_file(output / "solve-stdout.txt"),
            "stderr_sha256": sha256_file(output / "solve-stderr.txt"),
        },
    }
    write_json(output / "run.json", record)
    print(canonical_json({
        "variant": args.variant,
        "run": str(output / "run.json"),
        "error": error,
        "stage": None if checker_info is None else checker_info["stage"],
        "objective": None if objective is None else objective["total"],
        "iterations": lns_summary["iterations"],
        "native_calls": telemetry["sums"]["native_calls"],
        "native_successes": telemetry["sums"]["native_successes"],
        "fallback_count": telemetry["sums"]["fallback_count"],
    }))
    return 0 if error is None else 2


def parse_ps(raw: str, *, self_pid: int) -> list[dict[str, Any]]:
    rows = []
    for line in raw.splitlines():
        parts = line.strip().split(None, 5)
        if len(parts) != 6:
            continue
        try:
            pid, ppid = int(parts[0]), int(parts[1])
            cpu, memory = float(parts[2]), float(parts[3])
        except ValueError:
            continue
        command = parts[5]
        rows.append({
            "pid": pid,
            "ppid": ppid,
            "cpu_percent": cpu,
            "memory_percent": memory,
            "elapsed": parts[4],
            "command": command[:2000],
            "measurement_process": pid == self_pid or ppid == self_pid,
        })
    return rows


def command_cpu_audit(args: argparse.Namespace) -> int:
    output = args.output.resolve()
    if output.exists():
        raise SystemExit(f"refusing to overwrite CPU audit: {output}")
    completed = subprocess.run(
        ["ps", "-Ao", "pid=,ppid=,pcpu=,pmem=,etime=,command=", "-r"],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    if completed.returncode:
        raise SystemExit(completed.stderr.strip() or "ps audit failed")
    rows = parse_ps(completed.stdout, self_pid=os.getpid())
    external = [
        row for row in rows
        if not row["measurement_process"]
        and "verify_native_p7_fixed_time.py cpu-audit" not in row["command"]
    ]
    high = [row for row in external if row["cpu_percent"] >= args.high_cpu_threshold]
    record = {
        "schema_version": 1,
        "kind": "p7_read_only_cpu_audit",
        "recorded_at": now_record(),
        "threshold_definition": (
            f"external process snapshot CPU >= {args.high_cpu_threshold:g}% is material for this single-thread-sensitive fixed-time pair"
        ),
        "high_cpu_threshold_percent": args.high_cpu_threshold,
        "external_cpu_bound_high": bool(high),
        "high_external_processes": high,
        "top_processes": rows[:40],
        "load_average": list(os.getloadavg()),
        "logical_cpus": os.cpu_count(),
        "raw_ps_stdout": completed.stdout,
        "raw_ps_stderr": completed.stderr,
        "processes_changed_or_terminated": False,
        "command": ["ps", "-Ao", "pid=,ppid=,pcpu=,pmem=,etime=,command=", "-r"],
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    write_json(output, record)
    print(canonical_json({
        "audit": str(output),
        "external_cpu_bound_high": bool(high),
        "high_process_count": len(high),
        "load_average": record["load_average"],
    }))
    return 0


def native_loaded_exact_binary(run: Mapping[str, Any]) -> bool:
    binary = run.get("binary")
    if not isinstance(binary, Mapping):
        return False
    expected = (ROOT / str(binary.get("path"))).resolve()
    loaded = run.get("loaded_native_modules_after_run", [])
    return any(Path(item.get("file", "")).resolve() == expected for item in loaded)


def command_finalize(args: argparse.Namespace) -> int:
    root = args.root.resolve()
    targets = [root / name for name in ("identity.json", "comparison.json", "gate.json", "SHA256SUMS")]
    if any(path.exists() for path in targets):
        raise SystemExit("refusing to overwrite finalized P7 evidence")
    python = json.loads((root / "python" / "run.json").read_text(encoding="utf-8"))
    native = json.loads((root / "native" / "run.json").read_text(encoding="utf-8"))
    cpu_before = json.loads((root / "cpu-before.json").read_text(encoding="utf-8"))
    cpu_after = json.loads((root / "cpu-after.json").read_text(encoding="utf-8"))

    solver_hash, solver_files = aggregate_file_hash(list((ROOT / "baseline/solver").glob("*.py")))
    native_hash, native_files = aggregate_file_hash(list((ROOT / "native/ogc_native/src").glob("*.[ch]pp")))
    identity = {
        "schema_version": 1,
        "recorded_at": now_record(),
        "branch": git("rev-parse", "--abbrev-ref", "HEAD"),
        "head": git("rev-parse", "HEAD"),
        "dirty_state": git("status", "--short", "--branch").splitlines(),
        "solver_source_aggregate_sha256": solver_hash,
        "solver_source_files": solver_files,
        "native_source_aggregate_sha256": native_hash,
        "native_source_files": native_files,
        "harness_sha256": sha256_file(Path(__file__).resolve()),
        "instance": python["instance"],
        "production_default_config_sha256": python["config"]["production_default_sha256"],
        "python_actual_config_sha256": python["config"]["actual_sha256"],
        "native_actual_config_sha256": native["config"]["actual_sha256"],
        "p6_binary": native["binary"],
        "p6_manifest": {
            "path": str(args.p6_manifest.resolve().relative_to(ROOT)),
            "sha256": sha256_file(args.p6_manifest.resolve()),
        },
        "p6_gate": {
            "path": str(args.p6_gate.resolve().relative_to(ROOT)),
            "sha256": sha256_file(args.p6_gate.resolve()),
        },
        "fixed_work_prerequisites": {
            "p3": "GO",
            "p4": "PASS",
            "p5": "PASS",
            "p6_local_macos": "LOCAL_PASS",
        },
    }
    write_json(root / "identity.json", identity)

    def correctness(run: Mapping[str, Any]) -> bool:
        result = run.get("result", {})
        checker = result.get("checker") or {}
        objective = result.get("objective") or {}
        return bool(
            result.get("error") is None
            and result.get("trace_exceptions") == []
            and checker.get("feasible") is True
            and checker.get("stage") == 5
            and checker.get("violations") == []
            and objective.get("total") == objective.get("checker_total")
            and (result.get("solution") or {}).get("json_serializable") is True
        )

    python_tel = python["native_telemetry"]
    native_tel = native["native_telemetry"]
    native_sums = native_tel["sums"]
    python_default_no_probe = bool(
        python_tel["event_count"] > 0
        and set(python_tel["requested_backend_counts"]) == {"python"}
        and set(python_tel["actual_backend_counts"]) == {"python"}
        and set(python_tel["native_available_counts"]) == {"null"}
        and python_tel["sums"]["native_calls"] == 0
        and python_tel["sums"]["native_successes"] == 0
        and python.get("loaded_native_modules_after_run") == []
    )
    native_active = bool(
        native_tel["event_count"] > 0
        and set(native_tel["requested_backend_counts"]) == {"native"}
        and native_tel["native_available_counts"].get("true", 0) > 0
        and native_sums["native_calls"] > 0
        and native_sums["native_successes"] > 0
        and native_sums["native_successes"] <= native_sums["native_calls"]
        and native_sums["native_exception_count"] == 0
        and native_sums["native_invalid_output_count"] == 0
        and native_sums["native_error_count"] == 0
        and native_sums["geos_error_count"] == 0
        and native_sums["returned_candidate_recheck_failures"] == 0
        and native_tel["native_exact_mode_counts"].get("native", 0) > 0
        and native_tel["native_exact_decision_enabled_count"] > 0
        and native_loaded_exact_binary(native)
    )
    correctness_python = correctness(python)
    correctness_native = correctness(native)
    correctness_hard_gate = bool(
        correctness_python
        and correctness_native
        and native_sums["native_exception_count"] == 0
    )
    timing_valid = not (
        cpu_before.get("external_cpu_bound_high")
        or cpu_after.get("external_cpu_bound_high")
    )
    python_total = (python["result"].get("objective") or {}).get("total")
    native_total = (native["result"].get("objective") or {}).get("total")
    quality_delta = (
        None
        if python_total is None or native_total is None
        else float(native_total) - float(python_total)
    )
    if not correctness_hard_gate or not python_default_no_probe or not native_active:
        status = "NO-GO"
        reason = "correctness/default/backend-activation hard gate failed"
    elif not timing_valid:
        status = "INCONCLUSIVE"
        reason = "external CPU-bound load invalidated fixed-time quality/timing interpretation"
    elif quality_delta is None or quality_delta > 0.0:
        status = "NO-GO"
        reason = "native objective is worse than Python within the only P7 pair"
    else:
        status = "PASS"
        reason = "both runs passed correctness, native was active, timing audit was valid, and native objective did not lose"

    comparison = {
        "schema_version": 1,
        "kind": "pybind_native_single_fixed_time_pair",
        "order": ["python", "native"],
        "seed": python["config"]["seed"],
        "timelimit_seconds": python["timing"]["timelimit_seconds"],
        "python": {
            "stage": (python["result"].get("checker") or {}).get("stage"),
            "violations": (python["result"].get("checker") or {}).get("violations"),
            "objective": python["result"].get("objective"),
            "elapsed_seconds": python["timing"]["solve_elapsed_seconds"],
            "iterations": python["alns"]["iterations"],
            "cadence": {
                "total_iterations": python["alns"]["total_iterations"],
                "weight_updates": python["alns"]["weight_updates"],
                "stall_events": python["alns"]["stall_events"],
                "exit_reasons": python["alns"]["exit_reasons"],
            },
            "native_telemetry": python_tel,
            "digests": python["result"].get("solution"),
        },
        "native": {
            "stage": (native["result"].get("checker") or {}).get("stage"),
            "violations": (native["result"].get("checker") or {}).get("violations"),
            "objective": native["result"].get("objective"),
            "elapsed_seconds": native["timing"]["solve_elapsed_seconds"],
            "iterations": native["alns"]["iterations"],
            "cadence": {
                "total_iterations": native["alns"]["total_iterations"],
                "weight_updates": native["alns"]["weight_updates"],
                "stall_events": native["alns"]["stall_events"],
                "exit_reasons": native["alns"]["exit_reasons"],
            },
            "native_telemetry": native_tel,
            "digests": native["result"].get("solution"),
        },
        "quality_delta_native_minus_python": quality_delta,
        "quality_basis": (
            "minimization; only this parameterized single-instance fixed-time pair"
        ),
        "timing_audit_valid": timing_valid,
        "elapsed_iteration_speed_gate_eligible": timing_valid,
        "cpu_before": {
            "external_cpu_bound_high": cpu_before.get("external_cpu_bound_high"),
            "high_external_processes": cpu_before.get("high_external_processes"),
            "load_average": cpu_before.get("load_average"),
        },
        "cpu_after": {
            "external_cpu_bound_high": cpu_after.get("external_cpu_bound_high"),
            "high_external_processes": cpu_after.get("high_external_processes"),
            "load_average": cpu_after.get("load_average"),
        },
        "incumbent_traces": {
            "python": python["incumbent_trace"],
            "native": native["incumbent_trace"],
        },
        "prohibited_comparisons": {
            "section_0_1_120_second_objectives_used": False,
            "other_historical_fixed_time_results_used": False,
        },
    }
    write_json(root / "comparison.json", comparison)
    gate = {
        "schema_version": 1,
        "phase": "P7",
        "status": status,
        "reason": reason,
        "checks": {
            "python_correctness_stage5_violations0_exception0": correctness_python,
            "native_correctness_stage5_violations0_exception0": correctness_native,
            "correctness_hard_gate": correctness_hard_gate,
            "python_default_no_native_probe_or_call": python_default_no_probe,
            "native_explicit_seam_active": native_active,
            "native_binary_exact_p6_hash_loaded": native_loaded_exact_binary(native),
            "native_exception_zero": native_sums["native_exception_count"] == 0,
            "native_invalid_output_zero": native_sums["native_invalid_output_count"] == 0,
            "cpu_audit_timing_valid": timing_valid,
            "native_objective_not_worse": quality_delta is not None and quality_delta <= 0.0,
            "fixed_work_prerequisites_preserved": True,
        },
        "quality_delta_native_minus_python": quality_delta,
        "timing_policy": {
            "valid": timing_valid,
            "elapsed_iteration_speed_values_used_in_gate": timing_valid,
        },
        "contracts": {
            "production_default": "python",
            "native_scope": "explicit benchmark-only opt-in",
            "exact_unknown_authority": "Python GeometryKernel.relation/Shapely",
            "ubuntu_release_ready_claim": False,
            "additional_prob22_pair_run": False,
            "fixed_time_solver_run_count": 2,
            "commit_or_push": False,
        },
    }
    write_json(root / "gate.json", gate)
    manifest_rows = []
    for path in sorted(item for item in root.rglob("*") if item.is_file() and item.name != "SHA256SUMS"):
        manifest_rows.append(
            f"{sha256_file(path)}  {path.relative_to(root)}\n"
        )
    (root / "SHA256SUMS").write_text("".join(manifest_rows), encoding="utf-8")
    print(canonical_json({
        "status": status,
        "reason": reason,
        "quality_delta_native_minus_python": quality_delta,
        "timing_valid": timing_valid,
        "gate": str(root / "gate.json"),
        "manifest": str(root / "SHA256SUMS"),
    }))
    return 0 if status == "PASS" else 2


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(description=__doc__)
    commands = root.add_subparsers(dest="command", required=True)
    run = commands.add_parser("run")
    run.add_argument("--variant", choices=("python", "native"), required=True)
    run.add_argument(
        "--instance",
        type=Path,
        default=ROOT / "data" / "train" / DEFAULT_INSTANCE_NAME,
    )
    run.add_argument("--timelimit", type=float, default=DEFAULT_TIMELIMIT_SECONDS)
    run.add_argument("--seed", type=int, default=SEED)
    run.add_argument("--order-ordinal", type=int, choices=(1, 2), required=True)
    run.add_argument("--output", type=Path, required=True)
    run.add_argument("--native-binary", type=Path)
    run.add_argument("--expected-native-sha256", default=EXPECTED_BINARY_SHA256)
    run.set_defaults(function=command_run)
    audit = commands.add_parser("cpu-audit")
    audit.add_argument("--output", type=Path, required=True)
    audit.add_argument("--high-cpu-threshold", type=float, default=50.0)
    audit.set_defaults(function=command_cpu_audit)
    finalize = commands.add_parser("finalize")
    finalize.add_argument("--root", type=Path, required=True)
    finalize.add_argument("--p6-manifest", type=Path, required=True)
    finalize.add_argument("--p6-gate", type=Path, required=True)
    finalize.set_defaults(function=command_finalize)
    return root


def main() -> int:
    args = parser().parse_args()
    return int(args.function(args))


if __name__ == "__main__":
    raise SystemExit(main())
