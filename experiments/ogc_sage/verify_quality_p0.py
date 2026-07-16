#!/usr/bin/env python3
"""Phase 0 baseline/regression contract harness.

This harness records identity, tests, system-capacity qualification, continuous
runtime evidence, atomic pair fairness, and explicit benchmark variants.  It
never changes solver defaults or terminates processes.
"""

from __future__ import annotations

import argparse
import contextlib
import copy
import hashlib
import importlib.metadata
import io
import json
import math
import os
import platform
import re
import socket
import subprocess
import sys
import threading
import time
from dataclasses import asdict, dataclass, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence
from zoneinfo import ZoneInfo


ROOT = Path(__file__).resolve().parents[2]
BASELINE = ROOT / "baseline"
for candidate in (ROOT, BASELINE):
    if str(candidate) not in sys.path:
        sys.path.insert(0, str(candidate))

from baseline.solver.entry import solve  # noqa: E402
from baseline.solver.native_repair import load_native_module, snapshot_digest  # noqa: E402
from baseline.solver.runtime import RunTrace, SubmissionConfig  # noqa: E402
from baseline.utils import check_feasibility  # noqa: E402
from experiments.ogc_sage.benchmark_ogc_sage import (  # noqa: E402
    HARD10_MANIFEST,
    canonical_json,
    hard10_instance_names,
    reconstruct_snapshot,
    sha256_bytes,
    sha256_file,
    validate_dataset,
)
from experiments.ogc_sage.verify_native_p7_fixed_time import (  # noqa: E402
    aggregate_repair_telemetry,
)


KST = ZoneInfo("Asia/Seoul")
SEEDS = (20260710, 20260711, 20260712)
PROB23 = "prob_23.json"
PROB23_SECONDS = 120.0
CPU_CONTRACT_SCHEMA_VERSION = 3
CPU_SAMPLE_CADENCE_SECONDS = 1.0
CPU_QUALIFICATION_WINDOW_SECONDS = 10.0
CPU_ACQUISITION_LIMIT_SECONDS = 300.0
CPU_ROLLING_WINDOW_SECONDS = 5.0
CPU_PRE_AVERAGE_AVAILABLE_MIN = 0.70
CPU_PRE_ROLLING_AVAILABLE_MIN = 0.50
CPU_RUNTIME_AVERAGE_EXTERNAL_MAX = 0.30
CPU_RUNTIME_ROLLING_EXTERNAL_MAX = 0.50
CPU_COMPETING_CORE_EQUIVALENT_MIN = 0.50
CPU_COVERAGE_MIN = 0.95
CPU_SAMPLE_GAP_MAX_SECONDS = 2.5
PAIR_MEAN_EXTERNAL_BUSY_DELTA_MAX = 0.05
PAIR_P95_EXTERNAL_BUSY_DELTA_MAX = 0.10
NATIVE_BINARY = (
    ROOT
    / "artifacts/ogc_sage/step9/pybind-p7-fix1-macos-20260716/native"
    / "_ogc_native.cpython-312-darwin.so"
)
NATIVE_SHA256 = "38352e2040e311822ffa591e6d98a0b7754299bc935cd521f9778b508692232d"

EXPECTED_CHECKER_SHA256 = "d45aaeafdce8bf80d59d097f655c43313a4951bed43b6628e3b1cf62d4876a94"
EXPECTED_GREEDY_SHA256 = "8ec2cc816b35b6507a9407bc9f893140a9d1b5e0892af92a2dbac2f91b32103b"
EXPECTED_PYBIND_PLAN_SHA256 = "7c083a6238c11ed8c84712c22c879d26205094cd8048ca6ac3c4363bc3c6597d"

PREEXISTING_TRACKED = (
    "baseline/solver/alns.py",
    "baseline/solver/construct.py",
    "baseline/solver/entry.py",
    "baseline/solver/neighborhoods.py",
    "baseline/solver/runtime.py",
    "docs/OGC2026_Problem_Analysis.md",
    "docs/implementation/sol/11_PYBIND_REPAIR_KERNEL_ACCELERATION_PLAN.md",
)
PREEXISTING_UNTRACKED = (
    "baseline/solver/native_repair.py",
    "baseline/tests/test_native_import_fallback.py",
    "baseline/tests/test_native_repair_integration.py",
    "dist/",
    "docs/implementation/sol/12_SOLUTION_QUALITY_RECOVERY_PLAN.md",
    "docs/implementation/sol/quality-recovery/",
    "experiments/ogc_sage/generate_native_repair_fixture.py",
    "experiments/ogc_sage/verify_native_p3_fixture.py",
    "experiments/ogc_sage/verify_native_p4_prefilter.py",
    "experiments/ogc_sage/verify_native_p5_integration.py",
    "experiments/ogc_sage/verify_native_p7_fix1.py",
    "experiments/ogc_sage/verify_native_p7_fixed_time.py",
    "native/",
    "scripts/build_native_linux.sh",
    "scripts/build_native_local.py",
)


@dataclass(frozen=True, order=True)
class ProcessIdentity:
    pid: int
    create_time: float

    @property
    def key(self) -> str:
        return f"{self.pid}@{self.create_time:.9f}"

    @classmethod
    def parse(cls, value: str) -> "ProcessIdentity":
        pid, create_time = value.split("@", 1)
        return cls(int(pid), float(create_time))


@dataclass(frozen=True)
class ProcessRecord:
    identity: ProcessIdentity
    ppid: int
    name: str
    executable: str
    command: str
    cpu_seconds: float
    raw_ps_cpu_percent: float | None = None


@dataclass(frozen=True)
class CapacitySnapshot:
    monotonic_seconds: float
    epoch_seconds: float
    logical_cores: int
    system_cpu_times: Mapping[str, float]
    processes: tuple[ProcessRecord, ...]
    process_count_observed: int
    process_count_accessible: int
    host_tick_errors: tuple[str, ...] = ()
    process_errors: tuple[Mapping[str, Any], ...] = ()

    @property
    def process_access_coverage(self) -> float:
        if self.process_count_observed <= 0:
            return 0.0
        return min(1.0, self.process_count_accessible / self.process_count_observed)


@dataclass(frozen=True)
class CapacityInterval:
    started_monotonic: float
    ended_monotonic: float
    wall_seconds: float
    logical_cores: int
    capacity_seconds: float
    system_busy_seconds: float
    measurement_cpu_seconds: float
    external_busy_seconds: float
    external_busy_fraction: float
    available_external_capacity: float
    competing_compute_core_equivalent: float
    process_access_coverage: float
    attributed_external_cpu_seconds: float
    cpu_time_attribution_coverage: float
    host_tick_valid: bool
    measurement_subtree_identity_complete: bool
    measurement_subtraction_complete: bool
    classification_cpu_seconds: Mapping[str, float]
    top_contributors: tuple[Mapping[str, Any], ...]
    system_tick_errors: tuple[str, ...] = ()
    temporal_sampling_errors: tuple[str, ...] = ()
    measurement_subtree_errors: tuple[str, ...] = ()
    process_attribution_errors: tuple[Mapping[str, Any], ...] = ()


def cpu_measurement_contract() -> dict[str, Any]:
    return {
        "schema_version": CPU_CONTRACT_SCHEMA_VERSION,
        "gate_input": "cumulative_system_cpu_time_delta",
        "raw_ps_cpu_percent_role": "diagnostic_top_contributors_only_never_gate_input",
        "normalization": "logical_cores_x_wall_seconds",
        "measurement_exclusion": "pid_create_time_identity_full_descendant_tree_only",
        "gating_completeness": {
            "host_cumulative_tick_valid": True,
            "temporal_coverage_min_inclusive": CPU_COVERAGE_MIN,
            "sample_gap_max_seconds_inclusive": CPU_SAMPLE_GAP_MAX_SECONDS,
            "measurement_subtree_identity_required": True,
            "measurement_subtraction_required": True,
        },
        "diagnostic_attribution": {
            "process_access_coverage": "accessible_pid_count_divided_by_observed_pid_count_never_gate_input",
            "cpu_time_attribution_coverage": "accessible_non_measurement_cpu_seconds_divided_by_host_derived_external_busy_seconds_clamped_0_1_never_gate_input",
            "categorized_process_errors": [
                "AccessDenied", "NoSuchProcess", "ZombieProcess", "OSError"
            ],
            "unrelated_non_measurement_errors_are_structural": False,
        },
        "classification": [
            "measurement", "competing_compute", "os_background", "controller_ui", "unknown"
        ],
        "classification_is_allowlist": False,
        "pre_run": {
            "cadence_seconds": CPU_SAMPLE_CADENCE_SECONDS,
            "qualification_window_seconds": CPU_QUALIFICATION_WINDOW_SECONDS,
            "acquisition_limit_seconds": CPU_ACQUISITION_LIMIT_SECONDS,
            "average_available_capacity_min_inclusive": CPU_PRE_AVERAGE_AVAILABLE_MIN,
            "rolling_window_seconds": CPU_ROLLING_WINDOW_SECONDS,
            "rolling_available_capacity_min_inclusive": CPU_PRE_ROLLING_AVAILABLE_MIN,
        },
        "runtime_material_overlap": {
            "average_external_busy_max_exclusive": CPU_RUNTIME_AVERAGE_EXTERNAL_MAX,
            "rolling_window_seconds": CPU_ROLLING_WINDOW_SECONDS,
            "rolling_external_busy_max_exclusive": CPU_RUNTIME_ROLLING_EXTERNAL_MAX,
            "competing_compute_core_equivalent_min_inclusive": CPU_COMPETING_CORE_EQUIVALENT_MIN,
            "competing_compute_continuous_seconds": CPU_ROLLING_WINDOW_SECONDS,
            "temporal_coverage_min_inclusive": CPU_COVERAGE_MIN,
            "sample_gap_max_seconds_inclusive": CPU_SAMPLE_GAP_MAX_SECONDS,
            "measurement_subtree_identity_required": True,
            "measurement_subtraction_required": True,
        },
        "pair": {
            "orders": {"1": ["python", "native"], "2": ["native", "python"]},
            "atomic": True,
            "mean_external_busy_delta_max_inclusive": PAIR_MEAN_EXTERNAL_BUSY_DELTA_MAX,
            "p95_external_busy_delta_max_inclusive": PAIR_P95_EXTERNAL_BUSY_DELTA_MAX,
            "invalid_action": "discard_both_and_rerun_with_new_immutable_pair_id",
        },
        "liveness_states": ["READY", "PENDING_ENVIRONMENT", "PASS", "BLOCKED"],
        "blocked_definition": "host_tick_failure_or_measurement_root_subtree_identity_cpu_subtraction_failure",
        "consecutive_clean_heartbeat_windows": "PROHIBITED",
    }


def _system_busy_total(cpu_times: Mapping[str, float]) -> float:
    excluded = {"idle", "iowait", "guest", "guest_nice"}
    return sum(float(value) for key, value in cpu_times.items() if key not in excluded)


def normalize_cpu_delta(
    *, system_busy_seconds: float, measurement_cpu_seconds: float,
    logical_cores: int, wall_seconds: float,
) -> dict[str, float]:
    if logical_cores <= 0 or wall_seconds <= 0.0:
        raise ValueError("logical_cores and wall_seconds must be positive")
    if system_busy_seconds < 0.0 or measurement_cpu_seconds < 0.0:
        raise ValueError("CPU deltas must be non-negative")
    capacity_seconds = logical_cores * wall_seconds
    external_busy_seconds = max(0.0, system_busy_seconds - measurement_cpu_seconds)
    external_busy_fraction = min(1.0, external_busy_seconds / capacity_seconds)
    return {
        "capacity_seconds": capacity_seconds,
        "external_busy_seconds": external_busy_seconds,
        "external_busy_fraction": external_busy_fraction,
        "available_external_capacity": 1.0 - external_busy_fraction,
    }


def _process_map(processes: Iterable[ProcessRecord]) -> dict[ProcessIdentity, ProcessRecord]:
    return {process.identity: process for process in processes}


def _pid_map(processes: Iterable[ProcessRecord]) -> dict[int, ProcessRecord]:
    return {process.identity.pid: process for process in processes}


def measurement_tree_identities(
    processes: Iterable[ProcessRecord], roots: Iterable[ProcessIdentity],
) -> set[ProcessIdentity]:
    records = tuple(processes)
    by_pid = _pid_map(records)
    root_set = set(roots)
    result: set[ProcessIdentity] = set()
    for process in records:
        current = process
        visited: set[ProcessIdentity] = set()
        while current.identity not in visited:
            visited.add(current.identity)
            if current.identity in root_set:
                result.add(process.identity)
                break
            parent = by_pid.get(current.ppid)
            if parent is None or parent.identity.create_time > current.identity.create_time + 1e-6:
                break
            current = parent
    return result


_OS_BACKGROUND_PATTERNS = re.compile(
    r"windowserver|storagemanagement|applicationsstorageextension|kernel_task|"
    r"\bmds\b|mdworker|corespotlightd|backupd|cloudd|bird|fseventsd",
    re.IGNORECASE,
)
_CONTROLLER_UI_PATTERNS = re.compile(r"codex|chatgpt|electron", re.IGNORECASE)
_COMPETING_COMPUTE_PATTERNS = re.compile(
    r"solver|benchmark|verify_native|verify_quality|pytest|unittest|py-spy|profiler|"
    r"\bperf\b|dtrace|instruments|\bclang\b|clang\+\+|\bgcc\b|\bg\+\+\b|cc1|"
    r"\bcmake\b|\bninja\b|\bmake\b|xcodebuild|\bcargo\b|\brustc\b|\bgo build\b",
    re.IGNORECASE,
)


def classify_process(
    process: ProcessRecord, measurement_identities: set[ProcessIdentity],
) -> str:
    if process.identity in measurement_identities:
        return "measurement"
    diagnostic_text = " ".join((process.name, process.executable, process.command))
    if _OS_BACKGROUND_PATTERNS.search(diagnostic_text):
        return "os_background"
    if _CONTROLLER_UI_PATTERNS.search(diagnostic_text):
        return "controller_ui"
    if _COMPETING_COMPUTE_PATTERNS.search(diagnostic_text):
        return "competing_compute"
    return "unknown"


def derive_capacity_interval(
    previous: CapacitySnapshot,
    current: CapacitySnapshot,
    measurement_roots: Iterable[ProcessIdentity],
) -> CapacityInterval:
    wall_seconds = current.monotonic_seconds - previous.monotonic_seconds
    system_tick_errors = list(previous.host_tick_errors) + list(current.host_tick_errors)
    temporal_sampling_errors: list[str] = []
    measurement_subtree_errors: list[str] = []
    current_process_errors = [dict(error) for error in current.process_errors]
    if wall_seconds <= 0.0:
        raise ValueError("temporal_sampling_error:capacity_snapshots_must_be_monotonic")
    if current.logical_cores != previous.logical_cores or current.logical_cores <= 0:
        system_tick_errors.append("logical_core_identity_changed_or_unavailable")
    system_busy_seconds = _system_busy_total(current.system_cpu_times) - _system_busy_total(previous.system_cpu_times)
    if system_busy_seconds < -1e-6:
        system_tick_errors.append("system_cpu_time_regressed")
    system_busy_seconds = max(0.0, system_busy_seconds)

    previous_by_identity = _process_map(previous.processes)
    current_by_identity = _process_map(current.processes)
    previous_measurement = measurement_tree_identities(previous.processes, measurement_roots)
    current_measurement = measurement_tree_identities(current.processes, measurement_roots)
    root_set = set(measurement_roots)
    identity_complete = root_set.issubset(current_measurement)
    if not identity_complete:
        measurement_subtree_errors.append("measurement_root_missing_or_pid_reused")
    vanished_measurement = previous_measurement - current_measurement
    if vanished_measurement:
        identity_complete = False
        measurement_subtree_errors.append("measurement_descendant_disappeared_before_cpu_delta")
    subtraction_complete = identity_complete

    class_cpu: dict[str, float] = {
        "measurement": 0.0,
        "competing_compute": 0.0,
        "os_background": 0.0,
        "controller_ui": 0.0,
        "unknown": 0.0,
    }
    contributors: list[dict[str, Any]] = []
    for identity, process in current_by_identity.items():
        prior = previous_by_identity.get(identity)
        if prior is not None:
            cpu_delta = process.cpu_seconds - prior.cpu_seconds
        elif identity.create_time >= previous.epoch_seconds - 0.050:
            cpu_delta = process.cpu_seconds
        else:
            continue
        if cpu_delta < -1e-6:
            error = {
                "error_type": "ProcessCpuTimeRegression",
                "operation": "cpu_delta",
                "pid": identity.pid,
                "identity": identity.key,
            }
            if identity in current_measurement or identity in previous_measurement:
                subtraction_complete = False
                measurement_subtree_errors.append(
                    f"measurement_process_cpu_time_regressed:{identity.key}"
                )
            else:
                current_process_errors.append(error)
            continue
        cpu_delta = max(0.0, cpu_delta)
        category = classify_process(process, current_measurement)
        class_cpu[category] += cpu_delta
        if cpu_delta > 0.0:
            contributors.append({
                "identity": identity.key,
                "name": process.name,
                "category": category,
                "cpu_seconds": cpu_delta,
                "core_equivalent": cpu_delta / wall_seconds,
                "raw_ps_cpu_percent": process.raw_ps_cpu_percent,
            })

    measurement_cpu_seconds = class_cpu["measurement"]
    if measurement_cpu_seconds > system_busy_seconds + max(0.05, 0.02 * current.logical_cores * wall_seconds):
        subtraction_complete = False
        measurement_subtree_errors.append("measurement_cpu_exceeds_system_busy_delta")
    normalized = normalize_cpu_delta(
        system_busy_seconds=system_busy_seconds,
        measurement_cpu_seconds=measurement_cpu_seconds if subtraction_complete else 0.0,
        logical_cores=max(1, current.logical_cores),
        wall_seconds=wall_seconds,
    )
    access_coverage = min(previous.process_access_coverage, current.process_access_coverage)
    attributed_external_cpu_seconds = sum(
        value for category, value in class_cpu.items() if category != "measurement"
    )
    external_busy_seconds = normalized["external_busy_seconds"]
    attribution_coverage = (
        1.0 if external_busy_seconds <= 1e-12
        else min(1.0, max(0.0, attributed_external_cpu_seconds / external_busy_seconds))
    )
    return CapacityInterval(
        started_monotonic=previous.monotonic_seconds,
        ended_monotonic=current.monotonic_seconds,
        wall_seconds=wall_seconds,
        logical_cores=current.logical_cores,
        capacity_seconds=normalized["capacity_seconds"],
        system_busy_seconds=system_busy_seconds,
        measurement_cpu_seconds=measurement_cpu_seconds,
        external_busy_seconds=normalized["external_busy_seconds"],
        external_busy_fraction=normalized["external_busy_fraction"],
        available_external_capacity=normalized["available_external_capacity"],
        competing_compute_core_equivalent=class_cpu["competing_compute"] / wall_seconds,
        process_access_coverage=access_coverage,
        attributed_external_cpu_seconds=attributed_external_cpu_seconds,
        cpu_time_attribution_coverage=attribution_coverage,
        host_tick_valid=not system_tick_errors,
        measurement_subtree_identity_complete=identity_complete,
        measurement_subtraction_complete=subtraction_complete,
        classification_cpu_seconds=class_cpu,
        top_contributors=tuple(sorted(contributors, key=lambda row: -float(row["cpu_seconds"]))[:20]),
        system_tick_errors=tuple(sorted(set(system_tick_errors))),
        temporal_sampling_errors=tuple(sorted(set(temporal_sampling_errors))),
        measurement_subtree_errors=tuple(sorted(set(measurement_subtree_errors))),
        process_attribution_errors=tuple(current_process_errors),
    )


def _tail_pieces(intervals: Sequence[CapacityInterval], seconds: float) -> list[tuple[CapacityInterval, float]]:
    remaining = seconds
    pieces: list[tuple[CapacityInterval, float]] = []
    for interval in reversed(intervals):
        if remaining <= 1e-9:
            break
        used = min(interval.wall_seconds, remaining)
        pieces.append((interval, used))
        remaining -= used
    if remaining > 1e-6:
        return []
    pieces.reverse()
    return pieces


def rolling_weighted_values(
    intervals: Sequence[CapacityInterval], field: str, window_seconds: float,
) -> list[float]:
    values: list[float] = []
    for end in range(1, len(intervals) + 1):
        pieces = _tail_pieces(intervals[:end], window_seconds)
        if not pieces:
            continue
        values.append(sum(float(getattr(item, field)) * used for item, used in pieces) / window_seconds)
    return values


def rolling_min_values(
    intervals: Sequence[CapacityInterval], field: str, window_seconds: float,
) -> list[float]:
    values: list[float] = []
    for end in range(1, len(intervals) + 1):
        pieces = _tail_pieces(intervals[:end], window_seconds)
        if pieces:
            values.append(min(float(getattr(item, field)) for item, _used in pieces))
    return values


def nearest_rank_percentile(values: Sequence[float], percentile: float) -> float:
    if not values:
        raise ValueError("percentile requires at least one value")
    ordered = sorted(float(value) for value in values)
    rank = max(1, math.ceil(percentile * len(ordered)))
    return ordered[rank - 1]


def summarize_capacity(intervals: Sequence[CapacityInterval]) -> dict[str, Any]:
    if not intervals:
        return {
            "duration_seconds": 0.0,
            "interval_count": 0,
            "mean_external_busy_fraction": None,
            "mean_available_external_capacity": None,
            "p95_external_busy_fraction": None,
            "host_tick_valid": False,
            "temporal_coverage": 0.0,
            "process_access_coverage": 0.0,
            "cpu_time_attribution_coverage": 0.0,
            "max_sample_gap_seconds": None,
            "measurement_subtree_identity_complete": False,
            "measurement_subtraction_complete": False,
            "system_tick_errors": [],
            "temporal_sampling_errors": [],
            "measurement_subtree_errors": [],
            "process_error_counts": {
                name: 0 for name in ("AccessDenied", "NoSuchProcess", "ZombieProcess", "OSError")
            },
            "process_error_evidence": [],
            "rolling_5s_external_busy": [],
            "rolling_5s_available_capacity": [],
            "rolling_5s_competing_min_core_equivalent": [],
        }
    duration = sum(item.wall_seconds for item in intervals)
    mean_external = sum(item.external_busy_fraction * item.wall_seconds for item in intervals) / duration
    access_coverage = sum(item.process_access_coverage * item.wall_seconds for item in intervals) / duration
    temporal_span = sum(
        item.ended_monotonic - item.started_monotonic for item in intervals
    )
    temporal_coverage = min(
        1.0,
        len(intervals) * CPU_SAMPLE_CADENCE_SECONDS / max(temporal_span, 1e-12),
    )
    attributed_external = sum(item.attributed_external_cpu_seconds for item in intervals)
    host_external = sum(item.external_busy_seconds for item in intervals)
    attribution_coverage = (
        1.0 if host_external <= 1e-12
        else min(1.0, max(0.0, attributed_external / host_external))
    )
    process_errors = [
        dict(error) for item in intervals for error in item.process_attribution_errors
    ]
    process_error_counts: dict[str, int] = {
        name: 0 for name in ("AccessDenied", "NoSuchProcess", "ZombieProcess", "OSError")
    }
    for error in process_errors:
        error_type = str(error.get("error_type", "Unknown"))
        process_error_counts[error_type] = process_error_counts.get(error_type, 0) + 1
    system_tick_errors = sorted({
        error for item in intervals for error in item.system_tick_errors
    })
    temporal_sampling_errors = sorted({
        error for item in intervals for error in item.temporal_sampling_errors
    })
    measurement_subtree_errors = sorted({
        error for item in intervals for error in item.measurement_subtree_errors
    })
    return {
        "duration_seconds": duration,
        "interval_count": len(intervals),
        "mean_external_busy_fraction": mean_external,
        "mean_available_external_capacity": 1.0 - mean_external,
        "p95_external_busy_fraction": nearest_rank_percentile(
            [item.external_busy_fraction for item in intervals], 0.95
        ),
        "host_tick_valid": all(item.host_tick_valid for item in intervals),
        "process_access_coverage": access_coverage,
        "process_access_coverage_role": "diagnostic_only_never_gate_input",
        "cpu_time_attribution_coverage": attribution_coverage,
        "cpu_time_attribution_coverage_role": "diagnostic_only_never_gate_input",
        "attributed_external_cpu_seconds": attributed_external,
        "host_derived_external_busy_seconds": host_external,
        "temporal_coverage": temporal_coverage,
        "max_sample_gap_seconds": max(
            item.ended_monotonic - item.started_monotonic for item in intervals
        ),
        "measurement_subtree_identity_complete": all(
            item.measurement_subtree_identity_complete for item in intervals
        ),
        "measurement_subtraction_complete": all(item.measurement_subtraction_complete for item in intervals),
        "system_tick_errors": system_tick_errors,
        "temporal_sampling_errors": temporal_sampling_errors,
        "measurement_subtree_errors": measurement_subtree_errors,
        "process_error_counts": process_error_counts,
        "process_error_evidence": process_errors,
        "rolling_5s_external_busy": rolling_weighted_values(
            intervals, "external_busy_fraction", CPU_ROLLING_WINDOW_SECONDS
        ),
        "rolling_5s_available_capacity": rolling_weighted_values(
            intervals, "available_external_capacity", CPU_ROLLING_WINDOW_SECONDS
        ),
        "rolling_5s_competing_min_core_equivalent": rolling_min_values(
            intervals, "competing_compute_core_equivalent", CPU_ROLLING_WINDOW_SECONDS
        ),
    }


def evaluate_prequalification(intervals: Sequence[CapacityInterval]) -> dict[str, Any]:
    pieces = _tail_pieces(intervals, CPU_QUALIFICATION_WINDOW_SECONDS)
    if not pieces:
        return {"state": "PENDING_ENVIRONMENT", "ready": False, "reason": "qualification_window_incomplete"}
    window = [replace(item, wall_seconds=used) if used != item.wall_seconds else item for item, used in pieces]
    summary = summarize_capacity(window)
    structural = (
        not summary["host_tick_valid"]
        or not summary["measurement_subtree_identity_complete"]
        or not summary["measurement_subtraction_complete"]
        or bool(summary["system_tick_errors"])
        or bool(summary["measurement_subtree_errors"])
    )
    if structural:
        return {
            "state": "BLOCKED",
            "ready": False,
            "reason": "host_tick_or_measurement_subtree_structurally_impossible",
            "summary": summary,
        }
    rolling = summary["rolling_5s_available_capacity"]
    ready = (
        summary["temporal_coverage"] >= CPU_COVERAGE_MIN
        and summary["mean_available_external_capacity"] >= CPU_PRE_AVERAGE_AVAILABLE_MIN
        and bool(rolling)
        and min(rolling) >= CPU_PRE_ROLLING_AVAILABLE_MIN
        and summary["max_sample_gap_seconds"] <= CPU_SAMPLE_GAP_MAX_SECONDS
    )
    reason = "qualified_capacity_window"
    if summary["temporal_coverage"] < CPU_COVERAGE_MIN:
        reason = "qualification_temporal_coverage_below_95_percent"
    elif summary["max_sample_gap_seconds"] > CPU_SAMPLE_GAP_MAX_SECONDS:
        reason = "qualification_sample_gap_above_2_5_seconds"
    elif not ready:
        reason = "capacity_window_not_qualified"
    return {
        "state": "READY" if ready else "PENDING_ENVIRONMENT",
        "ready": ready,
        "reason": reason,
        "summary": summary,
    }


def evaluate_runtime(intervals: Sequence[CapacityInterval]) -> dict[str, Any]:
    summary = summarize_capacity(intervals)
    reasons: list[str] = []
    if not summary["measurement_subtraction_complete"]:
        reasons.append("measurement_subtree_cpu_subtraction_unavailable")
    if not summary["measurement_subtree_identity_complete"]:
        reasons.append("measurement_subtree_identity_unavailable")
    if summary["temporal_coverage"] < CPU_COVERAGE_MIN:
        reasons.append("temporal_coverage_below_95_percent")
    if summary["max_sample_gap_seconds"] is None or summary["max_sample_gap_seconds"] > CPU_SAMPLE_GAP_MAX_SECONDS:
        reasons.append("sample_gap_above_2_5_seconds")
    if summary["mean_external_busy_fraction"] is None or summary["mean_external_busy_fraction"] > CPU_RUNTIME_AVERAGE_EXTERNAL_MAX:
        reasons.append("run_average_external_busy_above_30_percent")
    if any(value > CPU_RUNTIME_ROLLING_EXTERNAL_MAX for value in summary["rolling_5s_external_busy"]):
        reasons.append("rolling_5s_external_busy_above_50_percent")
    if any(
        value >= CPU_COMPETING_CORE_EQUIVALENT_MIN
        for value in summary["rolling_5s_competing_min_core_equivalent"]
    ):
        reasons.append("known_competing_compute_at_least_half_core_for_5_seconds")
    if not summary["host_tick_valid"] or summary["system_tick_errors"]:
        reasons.append("host_tick_measurement_failure")
    if summary["measurement_subtree_errors"]:
        reasons.append("measurement_subtree_failure")
    if summary["temporal_sampling_errors"]:
        reasons.append("temporal_sampling_failure")
    return {
        "valid": not reasons,
        "material_overlap": bool(reasons),
        "reasons": sorted(set(reasons)),
        "summary": summary,
    }


def evaluate_pair_fairness(left: Mapping[str, Any], right: Mapping[str, Any]) -> dict[str, Any]:
    left_summary = left["summary"]
    right_summary = right["summary"]
    mean_delta = abs(
        float(left_summary["mean_external_busy_fraction"])
        - float(right_summary["mean_external_busy_fraction"])
    )
    p95_delta = abs(
        float(left_summary["p95_external_busy_fraction"])
        - float(right_summary["p95_external_busy_fraction"])
    )
    passed = (
        mean_delta <= PAIR_MEAN_EXTERNAL_BUSY_DELTA_MAX
        and p95_delta <= PAIR_P95_EXTERNAL_BUSY_DELTA_MAX
    )
    return {
        "passed": passed,
        "mean_external_busy_delta": mean_delta,
        "p95_external_busy_delta": p95_delta,
        "mean_boundary_max": PAIR_MEAN_EXTERNAL_BUSY_DELTA_MAX,
        "p95_boundary_max": PAIR_P95_EXTERNAL_BUSY_DELTA_MAX,
    }


def evaluate_atomic_pair(
    *, pair_id: str, pair_ordinal: int, matrix_identity: str,
    runs: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    expected_order = ["python", "native"] if pair_ordinal == 1 else ["native", "python"]
    identity_ok = all(
        run.get("pair_id") == pair_id and run.get("matrix_identity") == matrix_identity
        for run in runs
    )
    order_ok = [run.get("variant") for run in runs] == expected_order
    runtime_ok = len(runs) == 2 and all(bool(run.get("runtime_capacity", {}).get("valid")) for run in runs)
    fairness = (
        evaluate_pair_fairness(runs[0]["runtime_capacity"], runs[1]["runtime_capacity"])
        if len(runs) == 2 and runtime_ok else {"passed": False, "reason": "runtime_invalid"}
    )
    valid = len(runs) == 2 and identity_ok and order_ok and runtime_ok and fairness["passed"]
    return {
        "pair_id": pair_id,
        "pair_ordinal": pair_ordinal,
        "matrix_identity": matrix_identity,
        "expected_order": expected_order,
        "identity_unchanged": identity_ok,
        "order_valid": order_ok,
        "both_runtime_valid": runtime_ok,
        "fairness": fairness,
        "valid": valid,
        "status": "VALID" if valid else "INVALID_DISCARD_BOTH",
        "run_disposition": ["KEEP", "KEEP"] if valid else ["DISCARD", "DISCARD"],
        "required_action": None if valid else "rerun_entire_pair_with_new_immutable_pair_id",
    }


def liveness_state(
    *, identity_complete: bool, tests_complete: bool, scenario_complete: bool,
    contract_complete: bool, qualification_state: str, valid_matrix_complete: bool,
) -> str:
    if qualification_state == "BLOCKED":
        return "BLOCKED"
    if qualification_state == "PENDING_ENVIRONMENT":
        return "PENDING_ENVIRONMENT"
    ready = all((identity_complete, tests_complete, scenario_complete, contract_complete))
    if not ready:
        raise ValueError("READY requires complete identity/tests/scenario/contract")
    return "PASS" if valid_matrix_complete else "READY"


def now() -> dict[str, str]:
    value = datetime.now(timezone.utc)
    return {"utc": value.isoformat(), "kst": value.astimezone(KST).isoformat()}


def write_json_new(path: Path, value: object) -> None:
    if path.exists():
        raise SystemExit(f"refusing to overwrite {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, indent=2, sort_keys=True, ensure_ascii=True) + "\n",
        encoding="utf-8",
    )


def run(command: list[str], *, cwd: Path = ROOT, timeout: float | None = None) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(
            command,
            cwd=cwd,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=timeout,
            check=False,
        )
    except FileNotFoundError as exc:
        return subprocess.CompletedProcess(command, 127, "", f"{type(exc).__name__}: {exc}\n")


def git(*args: str) -> str:
    completed = run(["git", *args])
    if completed.returncode:
        raise RuntimeError(completed.stderr.strip() or "git command failed")
    return completed.stdout.strip()


def command_record(command: list[str], completed: subprocess.CompletedProcess[str]) -> dict[str, Any]:
    return {
        "argv": command,
        "cwd": str(ROOT),
        "returncode": completed.returncode,
        "stdout": completed.stdout,
        "stderr": completed.stderr,
    }


def sysctl(name: str) -> str | None:
    completed = run(["sysctl", "-n", name])
    return completed.stdout.strip() if completed.returncode == 0 else None


def version_record() -> dict[str, Any]:
    import shapely

    try:
        pybind_version = importlib.metadata.version("pybind11")
    except importlib.metadata.PackageNotFoundError:
        pybind_version = "vendored-2.13.6"

    try:
        import gurobipy as gp

        gurobi_version = ".".join(str(item) for item in gp.gurobi.version())
        env = gp.Env(empty=True)
        env.setParam("OutputFlag", 0)
        env.start()
        env.dispose()
        license_state = {"available": True, "error": None}
    except Exception as exc:  # environment identity, not a solver fallback
        gurobi_version = None
        license_state = {"available": False, "error": f"{type(exc).__name__}: {exc}"}
    cmake = run(["cmake", "--version"])
    return {
        "python_executable": sys.executable,
        "python": platform.python_version(),
        "python_compiler": platform.python_compiler(),
        "cmake": cmake.stdout.splitlines()[0] if cmake.returncode == 0 else None,
        "pybind11": pybind_version,
        "shapely": shapely.__version__,
        "geos": shapely.geos_version_string,
        "gurobi": gurobi_version,
        "gurobi_license": license_state,
    }


def aggregate_paths(paths: list[Path]) -> tuple[str, list[dict[str, str]]]:
    rows = [
        {"path": str(path.relative_to(ROOT)), "sha256": sha256_file(path)}
        for path in sorted(set(paths))
        if path.is_file()
    ]
    digest = sha256_bytes(
        "".join(f"{row['sha256']}  {row['path']}\n" for row in rows).encode("utf-8")
    )
    return digest, rows


def source_identity() -> dict[str, Any]:
    preexisting_diff = run(["git", "diff", "--binary", "--", *PREEXISTING_TRACKED]).stdout
    current_diff = run(["git", "diff", "--binary"]).stdout
    solver_paths = list((ROOT / "baseline/solver").glob("*.py"))
    native_paths = [path for path in (ROOT / "native").rglob("*") if path.is_file()]
    harness_paths = [
        ROOT / "experiments/ogc_sage/benchmark_ogc_sage.py",
        Path(__file__).resolve(),
        ROOT / "docs/implementation/sol/12_SOLUTION_QUALITY_RECOVERY_PLAN.md",
        ROOT / "docs/implementation/sol/quality-recovery/SCHEDULER_PROTOCOL.md",
        ROOT / "docs/implementation/sol/quality-recovery/00_BASELINE_AND_REGRESSION_CONTRACT.md",
    ]
    aggregate, rows = aggregate_paths(solver_paths + native_paths + harness_paths)
    snapshot_payload = {
        "tracked_diff_sha256": sha256_bytes(current_diff.encode("utf-8")),
        "execution_files_sha256": aggregate,
        "head": git("rev-parse", "HEAD"),
    }
    return {
        "branch": git("branch", "--show-current"),
        "head": git("rev-parse", "HEAD"),
        "upstream_left_right": git(
            "rev-list", "--left-right", "--count",
            "origin/codex/performance-optimization-plan...HEAD",
        ),
        "git_status_porcelain_v1": git("status", "--porcelain=v1").splitlines(),
        "preexisting_tracked": list(PREEXISTING_TRACKED),
        "preexisting_untracked": list(PREEXISTING_UNTRACKED),
        "phase0_changes_at_prepare": [
            "docs/implementation/sol/12_SOLUTION_QUALITY_RECOVERY_PLAN.md",
            "docs/implementation/sol/quality-recovery/SCHEDULER_PROTOCOL.md",
            "docs/implementation/sol/quality-recovery/00_BASELINE_AND_REGRESSION_CONTRACT.md",
            "experiments/ogc_sage/verify_quality_p0.py",
        ],
        "preexisting_tracked_diff_sha256": sha256_bytes(preexisting_diff.encode("utf-8")),
        "current_tracked_diff_sha256": snapshot_payload["tracked_diff_sha256"],
        "execution_files_sha256": aggregate,
        "execution_files": rows,
        "source_snapshot_sha256": sha256_bytes(canonical_json(snapshot_payload).encode("utf-8")),
    }


def native_import_record() -> dict[str, Any]:
    if sha256_file(NATIVE_BINARY) != NATIVE_SHA256:
        raise RuntimeError("native binary hash mismatch")
    old = os.environ.get("OGC_NATIVE_MODULE_DIR")
    os.environ["OGC_NATIVE_MODULE_DIR"] = str(NATIVE_BINARY.parent)
    try:
        module = load_native_module()
        info = None if module is None else module.empty_kernel_info()
        return {
            "available": module is not None,
            "requested_path": str(NATIVE_BINARY.resolve()),
            "loaded_path": None if module is None else str(Path(module.__file__).resolve()),
            "sha256": sha256_file(NATIVE_BINARY),
            "size_bytes": NATIVE_BINARY.stat().st_size,
            "api": info,
            "repair_api_version": None if info is None else info.get("repair_api_version"),
        }
    finally:
        if old is None:
            os.environ.pop("OGC_NATIVE_MODULE_DIR", None)
        else:
            os.environ["OGC_NATIVE_MODULE_DIR"] = old


def scenario_record() -> dict[str, Any]:
    instances, dataset_sha = validate_dataset()
    names = hard10_instance_names()
    hard10 = [{"instance": name, "sha256": sha256_file(instances[name])} for name in names]
    production = SubmissionConfig.from_defaults(seed=SEEDS[0]).as_dict()
    native = replace(
        SubmissionConfig.from_defaults(seed=SEEDS[0]),
        repair_backend="native",
        native_prefilter_enabled=True,
    ).as_dict()
    return {
        "schema_version": CPU_CONTRACT_SCHEMA_VERSION,
        "dataset": {
            "count": len(instances),
            "sha256": dataset_sha,
            "instances": [
                {"instance": name, "path": str(path.relative_to(ROOT)), "sha256": sha256_file(path)}
                for name, path in instances.items()
            ],
        },
        "hard10": {
            "count": len(names),
            "unique_count": len(set(names)),
            "manifest": str(HARD10_MANIFEST.relative_to(ROOT)),
            "manifest_sha256": sha256_file(HARD10_MANIFEST),
            "instances": hard10,
        },
        "frozen_python": {
            "instances": list(names),
            "budget_seconds": 60.0,
            "seeds": list(SEEDS),
            "planned_run_count": len(names) * len(SEEDS),
            "config": production,
            "config_sha256": sha256_bytes(canonical_json(production).encode("utf-8")),
            "outer_timeout_contract": "budget + max(5s, 2*checker_p95 + launcher_p95 + 1s)",
        },
        "prob23": {
            "instance": PROB23,
            "instance_sha256": sha256_file(instances[PROB23]),
            "budget_seconds": PROB23_SECONDS,
            "seed": SEEDS[0],
            "orders": [["python", "native"], ["native", "python"]],
            "planned_run_count": 4,
            "python_config": production,
            "python_config_sha256": sha256_bytes(canonical_json(production).encode("utf-8")),
            "native_config": native,
            "native_config_sha256": sha256_bytes(canonical_json(native).encode("utf-8")),
            "checker_reserve": "SubmissionConfig/RunTrace production budget reserve; unchanged",
            "atomic_pair_contract": cpu_measurement_contract()["pair"],
        },
        "integrated": {"instances": list(names), "budgets_seconds": [60.0, 180.0], "seeds": list(SEEDS)},
        "final": {"instances": "official daily-40", "source": "Phase 8 contract"},
        "cpu_measurement_contract": cpu_measurement_contract(),
    }


def matrix_identity(source: Mapping[str, Any], scenarios: Mapping[str, Any]) -> str:
    payload = {
        "source_snapshot_sha256": source["source_snapshot_sha256"],
        "execution_files_sha256": source["execution_files_sha256"],
        "config_sha256": scenarios["frozen_python"]["config_sha256"],
        "dataset_sha256": scenarios["dataset"]["sha256"],
        "hard10_manifest_sha256": scenarios["hard10"]["manifest_sha256"],
        "prob23_sha256": scenarios["prob23"]["instance_sha256"],
        "native_binary_sha256": NATIVE_SHA256,
        "cpu_contract_sha256": sha256_bytes(
            canonical_json(cpu_measurement_contract()).encode("utf-8")
        ),
    }
    return sha256_bytes(canonical_json(payload).encode("utf-8"))


def current_matrix_identity() -> str:
    return matrix_identity(source_identity(), scenario_record())


def command_prepare(args: argparse.Namespace) -> int:
    import psutil

    root = args.artifact_root.resolve()
    if root.exists():
        raise SystemExit(f"refusing to reuse artifact root {root}")
    (root / "baseline").mkdir(parents=True)
    (root / "prob23").mkdir()
    commands: list[dict[str, Any]] = []
    diff_command = ["git", "diff", "--check"]
    diff = run(diff_command)
    commands.append(command_record(diff_command, diff))
    test_command = [sys.executable, "-m", "unittest", "discover", "-s", "tests", "-p", "test_*.py", "-v"]
    tests = run(test_command, cwd=BASELINE, timeout=300.0)
    (root / "tests.stdout.txt").write_text(tests.stdout, encoding="utf-8")
    (root / "tests.stderr.txt").write_text(tests.stderr, encoding="utf-8")
    combined = tests.stdout + tests.stderr
    match = re.search(r"Ran (\d+) tests", combined)
    test_record = {
        "argv": test_command,
        "cwd": str(BASELINE),
        "returncode": tests.returncode,
        "count": None if match is None else int(match.group(1)),
        "passed": tests.returncode == 0 and "\nOK\n" in combined,
        "stdout_sha256": sha256_file(root / "tests.stdout.txt"),
        "stderr_sha256": sha256_file(root / "tests.stderr.txt"),
    }
    commands.append(test_record)
    scenarios = scenario_record()
    source = source_identity()
    frozen_matrix_identity = matrix_identity(source, scenarios)
    identity = {
        "schema_version": CPU_CONTRACT_SCHEMA_VERSION,
        "recorded_at": now(),
        "hostname": socket.gethostname(),
        "os": platform.platform(),
        "architecture": platform.machine(),
        "cpu": {
            "brand": sysctl("machdep.cpu.brand_string"),
            "physical": psutil.cpu_count(logical=False),
            "logical": psutil.cpu_count(logical=True),
        },
        "ram_bytes": psutil.virtual_memory().total,
        "versions": version_record(),
        "source": source,
        "protected": {
            "baseline/utils.py": sha256_file(ROOT / "baseline/utils.py"),
            "baseline/baseline_greedy.py": sha256_file(ROOT / "baseline/baseline_greedy.py"),
            "docs/implementation/sol/11_PYBIND_REPAIR_KERNEL_ACCELERATION_PLAN.md": sha256_file(ROOT / "docs/implementation/sol/11_PYBIND_REPAIR_KERNEL_ACCELERATION_PLAN.md"),
            "docs/implementation/sol/12_SOLUTION_QUALITY_RECOVERY_PLAN.md": sha256_file(ROOT / "docs/implementation/sol/12_SOLUTION_QUALITY_RECOVERY_PLAN.md"),
            "docs/implementation/sol/quality-recovery/SCHEDULER_PROTOCOL.md": sha256_file(ROOT / "docs/implementation/sol/quality-recovery/SCHEDULER_PROTOCOL.md"),
            "docs/implementation/sol/quality-recovery/00_BASELINE_AND_REGRESSION_CONTRACT.md": sha256_file(ROOT / "docs/implementation/sol/quality-recovery/00_BASELINE_AND_REGRESSION_CONTRACT.md"),
        },
        "native_binary": native_import_record(),
        "dataset_sha256": scenarios["dataset"]["sha256"],
        "hard10_manifest_sha256": scenarios["hard10"]["manifest_sha256"],
        "config_sha256": scenarios["frozen_python"]["config_sha256"],
        "config": scenarios["frozen_python"]["config"],
        "matrix_identity_sha256": frozen_matrix_identity,
        "cpu_contract_sha256": sha256_bytes(canonical_json(cpu_measurement_contract()).encode("utf-8")),
        "fixed_time_cpu_measurement": "not_started",
    }
    write_json_new(root / "identity-preflight.json", identity)
    write_json_new(root / "scenarios.json", scenarios)
    write_json_new(root / "tests.json", test_record)
    write_json_new(root / "native-import.json", identity["native_binary"])
    write_json_new(root / "measurement-contract.json", cpu_measurement_contract())
    write_json_new(root / "commands-preflight.json", commands)
    print(canonical_json({"artifact_root": str(root), "tests": test_record, "source_snapshot_sha256": identity["source"]["source_snapshot_sha256"]}))
    return 0 if diff.returncode == 0 and test_record["passed"] else 2


def parse_ps_diagnostic(raw: str) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for line in raw.splitlines():
        parts = line.strip().split(None, 5)
        if len(parts) != 6:
            continue
        try:
            pid, ppid = int(parts[0]), int(parts[1])
            cpu, memory = float(parts[2]), float(parts[3])
        except ValueError:
            continue
        rows.append({
            "pid": pid,
            "ppid": ppid,
            "cpu_percent": cpu,
            "memory_percent": memory,
            "elapsed": parts[4],
            "command": parts[5][:2000],
        })
    return rows


def diagnostic_ps_snapshot() -> dict[str, Any]:
    started = time.monotonic()
    completed = run(["ps", "-Ao", "pid=,ppid=,pcpu=,pmem=,etime=,command=", "-r"])
    rows = parse_ps_diagnostic(completed.stdout) if completed.returncode == 0 else []
    return {
        "recorded_at": now(),
        "monotonic_started": started,
        "load_average": list(os.getloadavg()),
        "role": "diagnostic_only_never_gate_input",
        "top_processes": rows[:100],
        "raw_ps_sha256": sha256_bytes(completed.stdout.encode("utf-8")),
        "raw_ps_stdout": completed.stdout,
        "raw_ps_stderr": completed.stderr,
        "returncode": completed.returncode,
    }


def process_error_type(exc: BaseException) -> str:
    import psutil

    if isinstance(exc, psutil.AccessDenied):
        return "AccessDenied"
    if isinstance(exc, psutil.ZombieProcess):
        return "ZombieProcess"
    if isinstance(exc, psutil.NoSuchProcess):
        return "NoSuchProcess"
    return "OSError"


def capture_capacity_snapshot() -> CapacitySnapshot:
    import psutil

    monotonic_seconds = time.monotonic()
    epoch_seconds = time.time()
    host_tick_errors: list[str] = []
    try:
        logical_cores = int(psutil.cpu_count(logical=True) or 0)
        cpu_times = {
            key: float(value) for key, value in psutil.cpu_times()._asdict().items()
        }
        if logical_cores <= 0 or not cpu_times:
            host_tick_errors.append("host_cpu_identity_or_ticks_unavailable")
    except (psutil.Error, OSError) as exc:
        logical_cores = 0
        cpu_times = {}
        host_tick_errors.append(f"{type(exc).__name__}:{exc}")

    def process_error(exc: BaseException, pid: int | None, operation: str) -> dict[str, Any]:
        return {
            "error_type": process_error_type(exc),
            "operation": operation,
            "pid": pid,
            "evidence": f"{type(exc).__name__}:{exc}"[:1000],
        }

    process_errors: list[dict[str, Any]] = []
    try:
        pids = psutil.pids()
    except (psutil.AccessDenied, psutil.NoSuchProcess, psutil.ZombieProcess, OSError) as exc:
        process_errors.append(process_error(exc, None, "enumerate_pids"))
        return CapacitySnapshot(
            monotonic_seconds, epoch_seconds, logical_cores, cpu_times, (), 0, 0,
            tuple(host_tick_errors), tuple(process_errors),
        )
    records: list[ProcessRecord] = []
    for pid in pids:
        try:
            process = psutil.Process(pid)
            with process.oneshot():
                create_time = float(process.create_time())
                ppid = int(process.ppid())
                cpu = process.cpu_times()
                name = process.name() or ""
            try:
                executable = process.exe() or ""
            except (psutil.AccessDenied, psutil.NoSuchProcess, psutil.ZombieProcess, OSError) as exc:
                executable = ""
                process_errors.append(process_error(exc, pid, "exe"))
            try:
                command = " ".join(process.cmdline())
            except (psutil.AccessDenied, psutil.NoSuchProcess, psutil.ZombieProcess, OSError) as exc:
                command = ""
                process_errors.append(process_error(exc, pid, "cmdline"))
            records.append(ProcessRecord(
                identity=ProcessIdentity(pid, create_time),
                ppid=ppid,
                name=name,
                executable=executable,
                command=command[:2000],
                cpu_seconds=float(cpu.user) + float(cpu.system),
            ))
        except (psutil.AccessDenied, psutil.NoSuchProcess, psutil.ZombieProcess, OSError) as exc:
            process_errors.append(process_error(exc, pid, "identity_ppid_name_cpu_times"))
            continue
    return CapacitySnapshot(
        monotonic_seconds=monotonic_seconds,
        epoch_seconds=epoch_seconds,
        logical_cores=logical_cores,
        system_cpu_times=cpu_times,
        processes=tuple(records),
        process_count_observed=len(pids),
        process_count_accessible=len(records),
        host_tick_errors=tuple(host_tick_errors),
        process_errors=tuple(process_errors),
    )


def current_process_identity() -> ProcessIdentity:
    import psutil

    process = psutil.Process(os.getpid())
    return ProcessIdentity(process.pid, float(process.create_time()))


def acquire_capacity_window(
    *, acquisition_limit_seconds: float = CPU_ACQUISITION_LIMIT_SECONDS,
) -> dict[str, Any]:
    if acquisition_limit_seconds != CPU_ACQUISITION_LIMIT_SECONDS:
        raise ValueError("production acquisition limit is fixed at 300 seconds")
    root = current_process_identity()
    intervals: list[CapacityInterval] = []
    started = time.monotonic()
    previous = capture_capacity_snapshot()
    attempts = 0
    temporal_sampling_errors: list[str] = []
    last_evaluation: dict[str, Any] = {
        "state": "PENDING_ENVIRONMENT", "ready": False, "reason": "qualification_window_incomplete"
    }
    while time.monotonic() - started < acquisition_limit_seconds:
        target = previous.monotonic_seconds + CPU_SAMPLE_CADENCE_SECONDS
        time.sleep(max(0.0, target - time.monotonic()))
        current = capture_capacity_snapshot()
        attempts += 1
        try:
            interval = derive_capacity_interval(previous, current, (root,))
        except (ValueError, RuntimeError) as exc:
            evidence = f"{type(exc).__name__}:{exc}"
            if "temporal_sampling_error" in str(exc):
                temporal_sampling_errors.append(evidence)
                previous = current
                continue
            return {
                "schema_version": CPU_CONTRACT_SCHEMA_VERSION,
                "state": "BLOCKED",
                "reason": f"host_tick_or_process_tree_measurement_impossible:{type(exc).__name__}:{exc}",
                "attempts": attempts,
                "elapsed_seconds": time.monotonic() - started,
                "diagnostic_ps": diagnostic_ps_snapshot(),
                "system_tick_errors": [evidence],
                "temporal_sampling_errors": temporal_sampling_errors,
                "intervals": [asdict(item) for item in intervals],
            }
        intervals.append(interval)
        last_evaluation = evaluate_prequalification(intervals)
        if last_evaluation["state"] == "BLOCKED" or last_evaluation["ready"]:
            break
        previous = current
    state = last_evaluation["state"]
    if state != "READY" and state != "BLOCKED":
        state = "PENDING_ENVIRONMENT"
    return {
        "schema_version": CPU_CONTRACT_SCHEMA_VERSION,
        "state": state,
        "reason": last_evaluation.get("reason"),
        "attempts": attempts,
        "elapsed_seconds": time.monotonic() - started,
        "measurement_root": root.key,
        "contract": cpu_measurement_contract(),
        "evaluation": last_evaluation,
        "diagnostic_ps": diagnostic_ps_snapshot(),
        "temporal_sampling_errors": temporal_sampling_errors,
        "intervals": [asdict(item) for item in intervals],
        "processes_changed_or_terminated": False,
    }


class RuntimeCapacityMonitor:
    def __init__(self, measurement_roots: Iterable[ProcessIdentity] | None = None) -> None:
        self.roots = tuple(measurement_roots or (current_process_identity(),))
        self.snapshots: list[CapacitySnapshot] = []
        self.intervals: list[CapacityInterval] = []
        self.errors: list[str] = []
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if self._thread is not None:
            raise RuntimeError("runtime capacity monitor already started")
        self.snapshots.append(capture_capacity_snapshot())
        self._thread = threading.Thread(target=self._sample_loop, name="p0-capacity-monitor", daemon=True)
        self._thread.start()

    def _sample_loop(self) -> None:
        while not self._stop.wait(CPU_SAMPLE_CADENCE_SECONDS):
            try:
                self._append_snapshot(capture_capacity_snapshot())
            except Exception as exc:  # fail-closed measurement evidence
                self.errors.append(f"{type(exc).__name__}:{exc}")
                return

    def _append_snapshot(self, snapshot: CapacitySnapshot) -> None:
        if self.snapshots:
            self.intervals.append(derive_capacity_interval(self.snapshots[-1], snapshot, self.roots))
        self.snapshots.append(snapshot)

    def stop(self) -> dict[str, Any]:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=CPU_SAMPLE_GAP_MAX_SECONDS)
        if not self.snapshots or time.monotonic() - self.snapshots[-1].monotonic_seconds >= 0.100:
            try:
                self._append_snapshot(capture_capacity_snapshot())
            except Exception as exc:
                self.errors.append(f"{type(exc).__name__}:{exc}")
        evaluation = evaluate_runtime(self.intervals)
        if self.errors:
            summary = dict(evaluation["summary"])
            summary["temporal_sampling_errors"] = sorted(set(
                list(summary.get("temporal_sampling_errors", [])) + self.errors
            ))
            evaluation = {
                **evaluation,
                "valid": False,
                "material_overlap": True,
                "reasons": sorted(set(evaluation["reasons"] + ["monitor_measurement_failure"])),
                "monitor_errors": list(self.errors),
                "summary": summary,
            }
        return {
            **evaluation,
            "schema_version": CPU_CONTRACT_SCHEMA_VERSION,
            "measurement_roots": [root.key for root in self.roots],
            "cadence_seconds": CPU_SAMPLE_CADENCE_SECONDS,
            "intervals": [asdict(item) for item in self.intervals],
            "diagnostic_ps": diagnostic_ps_snapshot(),
        }


def command_cpu_qualification(args: argparse.Namespace) -> int:
    root = args.artifact_root.resolve()
    output = root / "cpu-qualification.json"
    if output.exists():
        raise SystemExit(f"refusing to overwrite {output}")
    record = acquire_capacity_window()
    write_json_new(output, record)
    print(canonical_json({
        "state": record["state"],
        "reason": record["reason"],
        "elapsed_seconds": record["elapsed_seconds"],
        "qualification": str(output),
    }))
    return 2 if record["state"] == "BLOCKED" else 0


def objective_parity(objective: Mapping[str, Any] | None) -> float | None:
    if objective is None:
        return None
    pairs = (("z1", "checker_z1"), ("z2", "checker_z2"), ("z3", "checker_z3"), ("total", "checker_total"))
    return max(
        abs(float(objective[left]) - float(objective[right]))
        / max(1.0, abs(float(objective[left])), abs(float(objective[right])))
        for left, right in pairs
    )


def command_prob23_run(args: argparse.Namespace) -> int:
    output = args.output.resolve()
    if output.exists():
        raise SystemExit(f"refusing to overwrite {output}")
    output.mkdir(parents=True)
    qualification = json.loads(args.qualification.read_text(encoding="utf-8"))
    if qualification.get("state") != "READY":
        raise SystemExit("fixed-time run requires READY 10-second capacity qualification")
    if qualification.get("measurement_root") != args.measurement_root:
        raise SystemExit("qualification and runtime measurement tree roots differ")
    preflight_identity = json.loads((args.artifact_root / "identity-preflight.json").read_text(encoding="utf-8"))
    if args.matrix_identity != preflight_identity.get("matrix_identity_sha256"):
        raise SystemExit("matrix identity changed after dispatch")
    if current_matrix_identity() != args.matrix_identity:
        raise SystemExit("source/config/data/binary/contract mutation invalidates the entire matrix")
    expected_order = ("python", "native") if args.pair_ordinal == 1 else ("native", "python")
    if args.variant != expected_order[args.order_ordinal - 1]:
        raise SystemExit("variant does not match required alternating pair order")
    instances, dataset_sha = validate_dataset()
    instance = instances[PROB23]
    production = SubmissionConfig.from_defaults(seed=SEEDS[0])
    chosen = production
    binary: dict[str, Any] | None = None
    if args.variant == "native":
        if sha256_file(NATIVE_BINARY) != NATIVE_SHA256:
            raise SystemExit("native binary identity mismatch")
        os.environ["OGC_NATIVE_MODULE_DIR"] = str(NATIVE_BINARY.parent)
        chosen = replace(production, repair_backend="native", native_prefilter_enabled=True)
        binary = {"path": str(NATIVE_BINARY.relative_to(ROOT)), "sha256": NATIVE_SHA256}
    elif os.environ.get("OGC_NATIVE_MODULE_DIR"):
        raise SystemExit("Python run refuses OGC_NATIVE_MODULE_DIR")
    raw = json.loads(instance.read_text(encoding="utf-8"))
    trace = RunTrace()
    stdout, stderr = io.StringIO(), io.StringIO()
    started = time.monotonic()
    error = None
    operations = checked = snapshot = None
    pair_runner_root = ProcessIdentity.parse(args.measurement_root)
    monitor = RuntimeCapacityMonitor((pair_runner_root,))
    monitor.start()
    try:
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            operations = solve(copy.deepcopy(raw), PROB23_SECONDS, config=chosen, trace=trace)
        solve_elapsed = time.monotonic() - started
        checked = check_feasibility(copy.deepcopy(raw), copy.deepcopy(operations))
        snapshot = reconstruct_snapshot(raw, operations)
    except Exception as exc:
        solve_elapsed = time.monotonic() - started
        error = f"{type(exc).__name__}: {exc}"
    runtime_capacity = monitor.stop()
    (output / "solve-stdout.txt").write_text(stdout.getvalue(), encoding="utf-8")
    (output / "solve-stderr.txt").write_text(stderr.getvalue(), encoding="utf-8")
    trace_dict = trace.as_dict()
    invocations = trace_dict.get("lns_invocations", [])
    objective = None
    checker = None
    solution = None
    if operations is not None and checked is not None and snapshot is not None:
        write_json_new(output / "solution.json", operations)
        write_json_new(output / "checker.json", checked)
        objective = {
            "z1": snapshot.objective.z1, "z2": snapshot.objective.z2,
            "z3": snapshot.objective.z3, "total": snapshot.objective.total,
            "checker_z1": checked.get("obj1"), "checker_z2": checked.get("obj2"),
            "checker_z3": checked.get("obj3"), "checker_total": checked.get("objective"),
        }
        checker = {"feasible": checked.get("feasible"), "stage": checked.get("stage"), "violations": checked.get("violations"), "sha256": sha256_file(output / "checker.json")}
        solution = {"placement_sha256": snapshot_digest(snapshot), "serialization_sha256": sha256_bytes(canonical_json(operations).encode("utf-8")), "file_sha256": sha256_file(output / "solution.json")}
    telemetry = aggregate_repair_telemetry(trace_dict)
    record = {
        "schema_version": CPU_CONTRACT_SCHEMA_VERSION,
        "kind": "quality_recovery_p0_prob23_single_variant",
        "variant": args.variant,
        "pair_id": args.pair_id,
        "pair_ordinal": args.pair_ordinal,
        "matrix_identity": args.matrix_identity,
        "order_ordinal": args.order_ordinal,
        "source_snapshot_sha256": json.loads((args.artifact_root / "identity-preflight.json").read_text())["source"]["source_snapshot_sha256"],
        "dataset_sha256": dataset_sha,
        "instance": {"name": PROB23, "sha256": sha256_file(instance)},
        "config": chosen.as_dict(),
        "config_sha256": sha256_bytes(canonical_json(chosen.as_dict()).encode("utf-8")),
        "binary": binary,
        "budget_seconds": PROB23_SECONDS,
        "seed": SEEDS[0],
        "solve_elapsed_seconds": solve_elapsed,
        "error": error,
        "checker": checker,
        "objective": objective,
        "objective_parity_max_relative_error": objective_parity(objective),
        "solution": solution,
        "phase_timings": trace_dict.get("phase_times", {}),
        "checker_timings": trace_dict.get("checker_durations", []),
        "alns": {
            "iterations": sum(int(item.get("iterations", 0)) for item in invocations),
            "invocations": invocations,
            "validated_best": trace_dict.get("validated_best", []),
            "validated_best_events": trace_dict.get("validated_best_events", []),
            "trace_non_increasing": all(right <= left + 1e-9 for left, right in zip(trace_dict.get("validated_best", []), trace_dict.get("validated_best", [])[1:])),
        },
        "native_telemetry": telemetry,
        "pre_run_qualification": {
            "path": str(args.qualification),
            "sha256": sha256_file(args.qualification),
            "state": qualification["state"],
        },
        "runtime_capacity": runtime_capacity,
        "captured_stdout_sha256": sha256_file(output / "solve-stdout.txt"),
        "captured_stderr_sha256": sha256_file(output / "solve-stderr.txt"),
    }
    write_json_new(output / "run.json", record)
    print(canonical_json({"run": str(output / "run.json"), "variant": args.variant, "stage": None if checker is None else checker["stage"], "objective": None if objective is None else objective["total"], "error": error, "runtime_capacity_valid": runtime_capacity["valid"]}))
    return 0 if error is None else 2


def command_run_pair(args: argparse.Namespace) -> int:
    artifact_root = args.artifact_root.resolve()
    if not re.fullmatch(r"[A-Za-z0-9._-]+", args.pair_id):
        raise SystemExit("pair id must be an immutable path-safe identifier")
    pair_root = artifact_root / "prob23" / "pairs" / args.pair_id
    if pair_root.exists():
        raise SystemExit(f"refusing to reuse immutable pair id {args.pair_id}")
    pair_root.mkdir(parents=True)
    preflight = json.loads((artifact_root / "identity-preflight.json").read_text(encoding="utf-8"))
    matrix_id = preflight["matrix_identity_sha256"]
    if current_matrix_identity() != matrix_id:
        write_json_new(pair_root / "pair.json", {
            "schema_version": CPU_CONTRACT_SCHEMA_VERSION,
            "pair_id": args.pair_id,
            "pair_ordinal": args.pair_ordinal,
            "matrix_identity": matrix_id,
            "status": "INVALID_DISCARD_BOTH",
            "reason": "matrix_identity_changed_before_pair",
            "run_disposition": ["DISCARD", "DISCARD"],
            "required_action": "new_matrix_identity_and_new_immutable_pair_ids",
        })
        return 3

    qualification_path = pair_root / "cpu-qualification.json"
    qualification = acquire_capacity_window()
    write_json_new(qualification_path, qualification)
    if qualification["state"] != "READY":
        status = qualification["state"]
        write_json_new(pair_root / "pair.json", {
            "schema_version": CPU_CONTRACT_SCHEMA_VERSION,
            "pair_id": args.pair_id,
            "pair_ordinal": args.pair_ordinal,
            "matrix_identity": matrix_id,
            "status": status,
            "solver_runs_started": 0,
            "reason": qualification["reason"],
            "required_action": (
                "retry_later_with_new_immutable_pair_id"
                if status == "PENDING_ENVIRONMENT"
                else "repair_measurement_permission_or_structure_before_retry"
            ),
        })
        return 2 if status == "BLOCKED" else 0

    order = ("python", "native") if args.pair_ordinal == 1 else ("native", "python")
    pair_runner_root = current_process_identity()
    command_records: list[dict[str, Any]] = []
    runs: list[dict[str, Any]] = []
    for order_ordinal, variant in enumerate(order, 1):
        output = pair_root / f"run-{order_ordinal}-{variant}"
        command = [
            sys.executable, str(Path(__file__).resolve()), "prob23-run",
            "--artifact-root", str(artifact_root),
            "--variant", variant,
            "--pair-id", args.pair_id,
            "--pair-ordinal", str(args.pair_ordinal),
            "--order-ordinal", str(order_ordinal),
            "--matrix-identity", matrix_id,
            "--qualification", str(qualification_path),
            "--measurement-root", pair_runner_root.key,
            "--output", str(output),
        ]
        completed = run(command, timeout=PROB23_SECONDS + 60.0)
        command_records.append(command_record(command, completed))
        run_path = output / "run.json"
        if run_path.exists():
            runs.append(json.loads(run_path.read_text(encoding="utf-8")))
        if completed.returncode != 0:
            break
    pair = evaluate_atomic_pair(
        pair_id=args.pair_id,
        pair_ordinal=args.pair_ordinal,
        matrix_identity=matrix_id,
        runs=runs,
    )
    pair.update({
        "schema_version": CPU_CONTRACT_SCHEMA_VERSION,
        "qualification_sha256": sha256_file(qualification_path),
        "commands": command_records,
        "run_files": [
            {
                "variant": run["variant"],
                "path": str(pair_root / f"run-{index}-{run['variant']}" / "run.json"),
                "sha256": sha256_file(pair_root / f"run-{index}-{run['variant']}" / "run.json"),
            }
            for index, run in enumerate(runs, 1)
        ],
    })
    write_json_new(pair_root / "pair.json", pair)
    print(canonical_json({
        "pair": str(pair_root / "pair.json"),
        "status": pair["status"],
        "required_action": pair["required_action"],
    }))
    return 0 if pair["valid"] else 3


def seal_manifest(root: Path) -> tuple[int, str]:
    manifest = root / "SHA256SUMS"
    if manifest.exists():
        raise SystemExit("artifact is already sealed")
    files = sorted(path for path in root.rglob("*") if path.is_file())
    manifest.write_text(
        "".join(f"{sha256_file(path)}  {path.relative_to(root)}\n" for path in files),
        encoding="utf-8",
    )
    return len(files), sha256_file(manifest)


def command_seal_failed_prepare(args: argparse.Namespace) -> int:
    root = args.artifact_root.resolve()
    write_json_new(root / "prepare-failure.json", {
        "status": "FAILED_PREPARE_ATTEMPT",
        "recorded_at": now(),
        "error": args.error,
        "fixed_time_started": False,
        "files_overwritten_or_deleted": False,
    })
    write_json_new(root / "gate.json", {
        "phase": 0,
        "status": "FAIL",
        "reason": "preflight harness environment probe failed before identity/CPU/fixed-time",
        "next_phase_allowed": False,
    })
    count, digest = seal_manifest(root)
    print(canonical_json({"sealed_failed_prepare": str(root), "manifest_files": count, "SHA256SUMS_sha256": digest}))
    return 0


def protected_hashes_pass(
    preflight: Mapping[str, Any], current_hashes: Mapping[str, str] | None = None,
) -> bool:
    protected = preflight["protected"]
    if current_hashes is None:
        current_hashes = {
            "docs/implementation/sol/12_SOLUTION_QUALITY_RECOVERY_PLAN.md": sha256_file(
                ROOT / "docs/implementation/sol/12_SOLUTION_QUALITY_RECOVERY_PLAN.md"
            ),
            "docs/implementation/sol/quality-recovery/SCHEDULER_PROTOCOL.md": sha256_file(
                ROOT / "docs/implementation/sol/quality-recovery/SCHEDULER_PROTOCOL.md"
            ),
            "docs/implementation/sol/quality-recovery/00_BASELINE_AND_REGRESSION_CONTRACT.md": sha256_file(
                ROOT / "docs/implementation/sol/quality-recovery/00_BASELINE_AND_REGRESSION_CONTRACT.md"
            ),
        }
    checks = [
        protected["baseline/utils.py"] == EXPECTED_CHECKER_SHA256,
        protected["baseline/baseline_greedy.py"] == EXPECTED_GREEDY_SHA256,
        protected["docs/implementation/sol/11_PYBIND_REPAIR_KERNEL_ACCELERATION_PLAN.md"] == EXPECTED_PYBIND_PLAN_SHA256,
        protected["docs/implementation/sol/quality-recovery/00_BASELINE_AND_REGRESSION_CONTRACT.md"]
        == current_hashes["docs/implementation/sol/quality-recovery/00_BASELINE_AND_REGRESSION_CONTRACT.md"],
    ]
    for optional in (
        "docs/implementation/sol/12_SOLUTION_QUALITY_RECOVERY_PLAN.md",
        "docs/implementation/sol/quality-recovery/SCHEDULER_PROTOCOL.md",
    ):
        if optional in protected:
            checks.append(protected[optional] == current_hashes[optional])
    return all(checks)


def _finalize_without_solver(args: argparse.Namespace, expected_state: str) -> int:
    root = args.artifact_root.resolve()
    qualification = json.loads((root / "cpu-qualification.json").read_text(encoding="utf-8"))
    if qualification.get("state") != expected_state:
        raise SystemExit(f"{expected_state} finalization requires matching qualification state")
    scenarios = json.loads((root / "scenarios.json").read_text(encoding="utf-8"))
    tests = json.loads((root / "tests.json").read_text(encoding="utf-8"))
    preflight = json.loads((root / "identity-preflight.json").read_text(encoding="utf-8"))
    pending = expected_state == "PENDING_ENVIRONMENT"
    not_run_status = "NOT_RUN_PENDING_ENVIRONMENT" if pending else "NOT_RUN_MEASUREMENT_BLOCKED"
    baseline_summary = {
        "status": not_run_status,
        "planned_run_count": scenarios["frozen_python"]["planned_run_count"],
        "executed_run_count": 0,
        "stage5_count": 0,
        "tests_passed": tests["passed"],
        "tests_count": tests["count"],
        "partial_matrix_promoted": False,
        "reason": qualification["reason"],
    }
    (root / "baseline/raw.jsonl").write_text("", encoding="utf-8")
    write_json_new(root / "baseline/summary.json", baseline_summary)
    not_run = {
        "status": not_run_status,
        "planned_pairs": 2,
        "executed_run_count": 0,
        "budget_seconds": PROB23_SECONDS,
        "seed": SEEDS[0],
        "reason": baseline_summary["reason"],
        "atomic_pair_disposition": "NO_RUNS_CREATED",
    }
    write_json_new(root / "prob23/python.json", {**not_run, "variant": "python"})
    write_json_new(root / "prob23/native.json", {**not_run, "variant": "native", "binary_sha256": NATIVE_SHA256})
    comparison = {
        "status": "INCONCLUSIVE_NOT_RUN",
        "current_pair_count": 0,
        "objective_delta": None,
        "iteration_delta": None,
        "historical_signal": {
            "python_objective": 49102302,
            "python_iterations": 50,
            "native_objective": 89478006,
            "native_iterations": 22,
            "classification": "signal_only_identity_incomplete_for_current_contract",
        },
    }
    write_json_new(root / "comparison.json", comparison)
    write_json_new(root / "cpu-runtime-summary.json", {
        "status": "NOT_APPLICABLE_NO_FIXED_TIME_STARTED",
        "qualification_sha256": sha256_file(root / "cpu-qualification.json"),
        "processes_changed_or_terminated": False,
    })
    preflight["fixed_time_cpu_measurement"] = {
        "path": "cpu-qualification.json",
        "sha256": sha256_file(root / "cpu-qualification.json"),
        "state": expected_state,
        "solver_runs_started": 0,
    }
    preflight["recorded_final_at"] = now()
    write_json_new(root / "identity.json", preflight)
    hard10_command = [
        sys.executable, str(ROOT / "experiments/ogc_sage/benchmark_ogc_sage.py"), "run",
        "--gate", "constructor", "--variant", "heuristic_lns", "--budgets", "60",
        "--seeds", *[str(seed) for seed in SEEDS], "--instances", *hard10_instance_names(),
        "--allow-dirty", "--artifact-root", str(root), "--run-id", "baseline",
    ]
    prob23_commands = [
        [
            sys.executable, str(Path(__file__).resolve()), "run-pair",
            "--artifact-root", str(root), "--pair-id", f"<new-immutable-pair-{ordinal}-id>",
            "--pair-ordinal", str(ordinal),
        ]
        for ordinal in (1, 2)
    ]
    commands = {
        "preflight": json.loads((root / "commands-preflight.json").read_text(encoding="utf-8")),
        "cpu_qualification": {
            "argv": [sys.executable, str(Path(__file__).resolve()), "cpu-qualification", "--artifact-root", str(root)],
            "returncode": 0,
        },
        "fixed_time": {
            "status": not_run_status,
            "frozen_python_matrix": hard10_command,
            "frozen_python_runtime_requirement": "each fixed-time run must use the same 1-second runtime capacity monitor",
            "prob23_atomic_alternating_pairs": prob23_commands,
        },
        "pre_harness_observation": {
            "native_import_wrong_cwd": "ModuleNotFoundError: baseline (non-gating command setup error)",
            "full_tests_then_passed": 204,
        },
    }
    write_json_new(root / "commands.json", commands)
    gate = {
        "phase": 0,
        "status": expected_state,
        "reason": baseline_summary["reason"],
        "protected_hashes_pass": protected_hashes_pass(preflight),
        "dataset_manifest_pass": scenarios["dataset"]["count"] == 40,
        "hard10_manifest_pass": scenarios["hard10"]["count"] == scenarios["hard10"]["unique_count"] == 10,
        "full_tests_pass": tests["passed"],
        "full_tests_count": tests["count"],
        "frozen_python_matrix": "NOT_RUN",
        "frozen_python_planned_runs": scenarios["frozen_python"]["planned_run_count"],
        "frozen_python_completed_runs": 0,
        "prob23_pair": "NOT_RUN",
        "prob23_planned_runs": 4,
        "prob23_completed_runs": 0,
        "partial_matrix_promoted": False,
        "cpu_contract_schema_version": CPU_CONTRACT_SCHEMA_VERSION,
        "raw_ps_used_as_gate": False,
        "consecutive_clean_heartbeat_windows_used": False,
        "solver_algorithm_or_default_changed_by_phase0": False,
        "production_default": "python",
        "native_mip_interlock": "OFF",
        "next_phase_allowed": False,
        "fresh_phase0_retry_allowed": pending,
        "required_state_change": (
            "fresh Phase 0 retry may acquire a new 10-second qualified capacity window"
            if pending
            else "restore host tick or measurement root/subtree identity and CPU subtraction"
        ),
    }
    write_json_new(root / "gate.json", gate)
    count, manifest_sha = seal_manifest(root)
    print(canonical_json({"gate": expected_state, "artifact_root": str(root), "manifest_files": count, "SHA256SUMS_sha256": manifest_sha}))
    return 0


def command_finalize_pending_environment(args: argparse.Namespace) -> int:
    return _finalize_without_solver(args, "PENDING_ENVIRONMENT")


def command_finalize_blocked(args: argparse.Namespace) -> int:
    return _finalize_without_solver(args, "BLOCKED")


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description=__doc__)
    commands = result.add_subparsers(dest="command", required=True)
    prepare = commands.add_parser("prepare")
    prepare.add_argument("--artifact-root", type=Path, required=True)
    prepare.set_defaults(function=command_prepare)
    qualification = commands.add_parser("cpu-qualification")
    qualification.add_argument("--artifact-root", type=Path, required=True)
    qualification.set_defaults(function=command_cpu_qualification)
    prob23 = commands.add_parser("prob23-run")
    prob23.add_argument("--artifact-root", type=Path, required=True)
    prob23.add_argument("--variant", choices=("python", "native"), required=True)
    prob23.add_argument("--pair-id", required=True)
    prob23.add_argument("--pair-ordinal", type=int, choices=(1, 2), required=True)
    prob23.add_argument("--order-ordinal", type=int, choices=(1, 2), required=True)
    prob23.add_argument("--matrix-identity", required=True)
    prob23.add_argument("--qualification", type=Path, required=True)
    prob23.add_argument("--measurement-root", required=True)
    prob23.add_argument("--output", type=Path, required=True)
    prob23.set_defaults(function=command_prob23_run)
    pair = commands.add_parser("run-pair")
    pair.add_argument("--artifact-root", type=Path, required=True)
    pair.add_argument("--pair-id", required=True)
    pair.add_argument("--pair-ordinal", type=int, choices=(1, 2), required=True)
    pair.set_defaults(function=command_run_pair)
    finalize = commands.add_parser("finalize-blocked")
    finalize.add_argument("--artifact-root", type=Path, required=True)
    finalize.set_defaults(function=command_finalize_blocked)
    pending = commands.add_parser("finalize-pending-environment")
    pending.add_argument("--artifact-root", type=Path, required=True)
    pending.set_defaults(function=command_finalize_pending_environment)
    failed = commands.add_parser("seal-failed-prepare")
    failed.add_argument("--artifact-root", type=Path, required=True)
    failed.add_argument("--error", required=True)
    failed.set_defaults(function=command_seal_failed_prepare)
    return result


def main() -> int:
    args = parser().parse_args()
    return int(args.function(args))


if __name__ == "__main__":
    raise SystemExit(main())
