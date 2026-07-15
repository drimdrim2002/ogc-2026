"""Reproducible Step 9 benchmark and submission-archive harness."""

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
import shutil
import statistics
import subprocess
import sys
import tempfile
import time
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping


REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from baseline.solver.entry import solve  # noqa: E402
from baseline.solver.instance import parse_instance  # noqa: E402
from baseline.solver.runtime import RunTrace, SubmissionConfig, VARIANTS  # noqa: E402
from baseline.solver.state import Placement, SolutionSnapshot, compute_objective  # noqa: E402
from baseline.utils import check_feasibility  # noqa: E402


SCHEMA_VERSION = 2
ARTIFACT_ROOT = REPO_ROOT / "artifacts/ogc_sage/step9"
PERFORMANCE_ARTIFACT_ROOT = REPO_ROOT / "artifacts/ogc_sage/performance/phase0"
RAW_NAME = "raw.jsonl"
SUMMARY_NAME = "summary.json"
EVIDENCE_MANIFEST = REPO_ROOT / "docs/implementation/sol/evidence/step9-evidence.json"
HARD10_MANIFEST = REPO_ROOT / "docs/implementation/sol/performance/HARD10_MANIFEST.json"
DEFAULT_DATA_DIRS = (REPO_ROOT / "data/train 2", REPO_ROOT / "data/train")
FINAL_VARIANT = "heuristic_lns"
FINAL_BUDGETS = (60.0, 180.0)
FINAL_SEEDS = (20260710, 20260711, 20260712)
FINAL_INSTANCE_COUNT = 40
FINAL_EXPECTED_RUN_COUNT = FINAL_INSTANCE_COUNT * len(FINAL_BUDGETS) * len(FINAL_SEEDS)
TERMINAL_STATUSES = {"completed", "exception", "outer_timeout", "checker_failure"}
RAW_REQUIRED_FIELDS = (
    "schema_version",
    "run_key",
    "status",
    "instance",
    "instance_hash",
    "dataset_hash",
    "variant",
    "config_hash",
    "source_commit",
    "budget_seconds",
    "seed",
    "start_utc",
    "end_utc",
    "elapsed_seconds",
    "feasible",
    "stage",
    "violations",
    "obj1",
    "obj2",
    "obj3",
    "objective",
    "internal_checker_objective_error",
    "phase_timings",
    "checker_timings",
    "model_stats",
    "constructor_deadline_hit",
    "validated_best_trace",
    "lns_invocations",
    "operator_stats",
    "exception",
    "outer_timeout",
    "environment",
)
SUMMARY_REQUIRED_FIELDS = (
    "schema_version",
    "run_id",
    "dataset_sha256",
    "config_sha256",
    "commit",
    "run_count",
    "unique_key_count",
    "expected_run_count",
    "missing_count",
    "duplicate_count",
    "stage5_count",
    "exception_count",
    "outer_timeout_count",
    "objective_parity_max_relative_error",
    "trace_regression_count",
    "retime_z1_worsen_count",
    "longer_budget_regression_count",
    "gate_pass",
    "raw_sha256",
)
FORBIDDEN_ARCHIVE_NAMES = {
    "baseline_greedy.py",
    "run_myalgorithm.py",
    "run_baseline_greedy.py",
}


class ContractError(RuntimeError):
    pass


def validate_final_matrix_contract(
    *, variant: str, budgets: Iterable[float], seeds: Iterable[int],
    requested_instances: Iterable[str] | None,
) -> None:
    if variant != FINAL_VARIANT:
        raise ContractError(f"final gate requires variant {FINAL_VARIANT!r}")
    if tuple(float(value) for value in budgets) != FINAL_BUDGETS:
        raise ContractError(f"final gate requires exact budgets {FINAL_BUDGETS!r}")
    if tuple(int(value) for value in seeds) != FINAL_SEEDS:
        raise ContractError(f"final gate requires exact seeds {FINAL_SEEDS!r}")
    if requested_instances is not None:
        raise ContractError("final gate requires the full official daily-40 dataset")


def canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def percentile(values: Iterable[float], fraction: float) -> float:
    ordered = sorted(float(value) for value in values)
    if not ordered:
        return 0.0
    rank = max(1, math.ceil(fraction * len(ordered)))
    return ordered[rank - 1]


def relative_error(left: float | None, right: float | None) -> float | None:
    if left is None or right is None:
        return None
    return abs(float(left) - float(right)) / max(1.0, abs(float(left)), abs(float(right)))


def _expected_instances(manifest_path: Path = EVIDENCE_MANIFEST) -> dict[str, str]:
    try:
        document = json.loads(manifest_path.read_text(encoding="utf-8"))
        groups = document["dataset"]
    except (OSError, KeyError, TypeError, json.JSONDecodeError) as exc:
        raise ContractError(f"invalid evidence dataset manifest: {exc}") from exc
    expected: dict[str, str] = {}
    for group in groups.values():
        for name, digest in group["instances"].items():
            if name in expected:
                raise ContractError(f"duplicate manifest instance {name}")
            expected[name] = digest
    if len(expected) != 40:
        raise ContractError(f"dataset manifest must contain 40 instances, got {len(expected)}")
    return expected


def validate_dataset(
    data_dirs: Iterable[Path] = DEFAULT_DATA_DIRS,
    manifest_path: Path = EVIDENCE_MANIFEST,
) -> tuple[dict[str, Path], str]:
    """Return the exact daily-40 only after missing/duplicate/hash checks pass."""
    expected = _expected_instances(manifest_path)
    found: dict[str, Path] = {}
    for directory in data_dirs:
        for path in sorted(directory.glob("prob_*.json")):
            if path.name in found:
                raise ContractError(
                    f"duplicate dataset instance {path.name}: {found[path.name]} and {path}"
                )
            found[path.name] = path
    missing = sorted(set(expected) - set(found))
    unexpected = sorted(set(found) - set(expected))
    if missing or unexpected:
        raise ContractError(f"dataset membership mismatch missing={missing} unexpected={unexpected}")
    wrong = {
        name: sha256_file(path)
        for name, path in found.items()
        if sha256_file(path) != expected[name]
    }
    if wrong:
        raise ContractError(f"dataset hash mismatch: {wrong}")
    ordered = dict(sorted(found.items(), key=lambda item: int(item[0].split("_")[1].split(".")[0])))
    dataset_hash = sha256_bytes(canonical_json(expected).encode("utf-8"))
    return ordered, dataset_hash


def variant_config_payload(variant: str) -> dict[str, Any]:
    payload = SubmissionConfig.for_benchmark(variant, seed=0).as_dict()
    payload.pop("seed")
    return {
        "schema_version": SCHEMA_VERSION,
        "variant": variant,
        "submission_config": payload,
        "phase_ladder": {"safe": "<2", "short": "[2,12)", "medium": "[12,60)", "long": ">=60"},
    }


def config_hash(variant: str) -> str:
    return sha256_bytes(canonical_json(variant_config_payload(variant)).encode("utf-8"))


def _historical_raw_records(path: Path) -> list[dict[str, Any]]:
    """Read the immutable v1 baseline without presenting it as v2 telemetry."""
    try:
        records = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]
    except (OSError, json.JSONDecodeError) as exc:
        raise ContractError(f"invalid historical raw artifact: {exc}") from exc
    if not records or any(not isinstance(record, dict) for record in records):
        raise ContractError("historical raw artifact has no object records")
    return records


def _historical_lns_iterations(record: Mapping[str, Any]) -> int:
    try:
        value = record["model_stats"]["lns"]["iterations"]
    except (KeyError, TypeError) as exc:
        raise ContractError("historical raw is missing legacy LNS iteration telemetry") from exc
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ContractError("historical raw has invalid legacy LNS iterations")
    return value


def _invocation_measurement(
    record: Mapping[str, Any], expected_kinds: tuple[str, ...],
) -> tuple[float, float, int, list[dict[str, Any]]]:
    """Validate and aggregate direct v2 invocation telemetry."""
    invocations = record.get("lns_invocations")
    if not isinstance(invocations, list) or len(invocations) != len(expected_kinds):
        raise ContractError("direct telemetry has an unexpected invocation count")
    kinds = tuple(item.get("kind") for item in invocations if isinstance(item, Mapping))
    if kinds != expected_kinds:
        raise ContractError(
            f"direct telemetry has invocation kinds {kinds!r}, expected {expected_kinds!r}"
        )
    repair_seconds = retime_seconds = 0.0
    iterations = 0
    normalized: list[dict[str, Any]] = []
    for item in invocations:
        if not isinstance(item, Mapping):
            raise ContractError("direct telemetry invocation is not an object")
        try:
            repair = float(item["repair_seconds"])
            retime = float(item["retime_seconds"])
            count = int(item["iterations"])
        except (KeyError, TypeError, ValueError) as exc:
            raise ContractError(f"direct telemetry is incomplete: {exc}") from exc
        if (
            not math.isfinite(repair)
            or not math.isfinite(retime)
            or repair < 0.0
            or retime < 0.0
            or count < 0
        ):
            raise ContractError("direct telemetry has invalid phase times or iterations")
        repair_seconds += repair
        retime_seconds += retime
        iterations += count
        normalized.append(dict(item))
    return repair_seconds, retime_seconds, iterations, normalized


def freeze_hard10_manifest(input_raw: Path, output: Path) -> dict[str, Any]:
    """Freeze hard-10 from an exact 40×2×3 original-baseline artifact.

    V2 records use direct invocation telemetry. V1 records retain the
    explicitly labelled historical reconstruction only so that the old
    baseline remains auditable.
    """
    records = _historical_raw_records(input_raw)
    instances, dataset_hash = validate_dataset()
    expected_keys = {
        (name, budget, seed)
        for name in instances
        for budget in FINAL_BUDGETS
        for seed in FINAL_SEEDS
    }
    by_key: dict[tuple[str, float, int], Mapping[str, Any]] = {}
    for record in records:
        try:
            key = (str(record["instance"]), float(record["budget_seconds"]), int(record["seed"]))
            valid = (
                record["variant"] == FINAL_VARIANT
                and record["status"] == "completed"
                and record["feasible"] is True
                and int(record["stage"]) == 5
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise ContractError(f"malformed historical raw record: {exc}") from exc
        if key in by_key:
            raise ContractError(f"duplicate historical run key {key}")
        if key in expected_keys:
            if not valid:
                raise ContractError(f"historical baseline did not complete cleanly: {key}")
            by_key[key] = record
    if set(by_key) != expected_keys:
        missing = sorted(expected_keys - set(by_key))
        raise ContractError(f"historical baseline is not the exact 40×2×3 matrix; missing={missing}")

    direct_flags = ["lns_invocations" in record for record in by_key.values()]
    if any(direct_flags) and not all(direct_flags):
        raise ContractError("baseline artifact mixes legacy and direct telemetry schemas")
    direct_telemetry = all(direct_flags)
    if direct_telemetry:
        for name in instances:
            for seed in FINAL_SEEDS:
                _invocation_measurement(by_key[(name, 60.0, seed)], ("anchor",))
                _invocation_measurement(
                    by_key[(name, 180.0, seed)], ("anchor", "extension")
                )

    ranked: list[dict[str, Any]] = []
    for name, path in instances.items():
        seed_rows: list[dict[str, Any]] = []
        for seed in FINAL_SEEDS:
            anchor = by_key[(name, 60.0, seed)]
            extension = by_key[(name, 180.0, seed)]
            if direct_telemetry:
                repair_seconds, retime_seconds, iterations, invocations = (
                    _invocation_measurement(extension, ("anchor", "extension"))
                )
                seed_rows.append(
                    {
                        "seed": seed,
                        "invocation_measurements": invocations,
                        "repair_seconds": repair_seconds,
                        "retime_seconds": retime_seconds,
                        "iterations_total": iterations,
                        "runtime_hardness": (repair_seconds + retime_seconds)
                        / max(1, iterations),
                    }
                )
            else:
                phase_times = extension.get("phase_timings", {})
                repair_seconds = float(phase_times.get("lns_repair", 0.0))
                retime_seconds = float(phase_times.get("lns_retime", 0.0))
                anchor_iterations = _historical_lns_iterations(anchor)
                extension_iterations = _historical_lns_iterations(extension)
                iterations = anchor_iterations + extension_iterations
                seed_rows.append(
                    {
                        "seed": seed,
                        "repair_seconds_aggregate": repair_seconds,
                        "retime_seconds_aggregate": retime_seconds,
                        "anchor_iterations_reconstructed": anchor_iterations,
                        "extension_iterations_legacy_last_call": extension_iterations,
                        "iterations_total_reconstructed": iterations,
                        "runtime_hardness": (repair_seconds + retime_seconds)
                        / max(1, iterations),
                    }
                )
        raw = json.loads(path.read_text(encoding="utf-8"))
        ranked.append(
            {
                "instance": name,
                "instance_sha256": sha256_file(path),
                "block_count": len(raw["blocks"]),
                "median_runtime_hardness": statistics.median(
                    item["runtime_hardness"] for item in seed_rows
                ),
                (
                    "median_repair_seconds"
                    if direct_telemetry
                    else "median_repair_seconds_aggregate"
                ): statistics.median(
                    item["repair_seconds"]
                    if direct_telemetry
                    else item["repair_seconds_aggregate"]
                    for item in seed_rows
                ),
                "seed_measurements": seed_rows,
            }
        )
    ranked.sort(
        key=lambda item: (
            -float(item["median_runtime_hardness"]),
            -float(
                item[
                    "median_repair_seconds"
                    if direct_telemetry
                    else "median_repair_seconds_aggregate"
                ]
            ),
            int(item["block_count"]),
            int(str(item["instance"]).split("_")[1].split(".")[0]),
        )
    )
    selected = [dict(item, rank=index) for index, item in enumerate(ranked[:10], 1)]
    source_commits = sorted({str(record["source_commit"]) for record in by_key.values()})
    config_hashes = sorted({str(record["config_hash"]) for record in by_key.values()})
    telemetry_source = (
        {
            "status": "measured_from_direct_invocations",
            "explanation": "Every 180-second record contains separately measured anchor and extension invocations. Runtime hardness sums their repair/retime seconds and iterations within the same run.",
        }
        if direct_telemetry
        else {
            "status": "reconstructed_from_legacy_aggregate_raw",
            "explanation": "The v1 raw artifact stores aggregate 180-second LNS phase time and the extension's last-call iterations. The matching 60-second record supplies the deterministic anchor iteration count; phase times are deliberately labelled aggregate rather than invocation-measured.",
        }
    )
    document = {
        "schema_version": 1,
        "status": "frozen",
        "selection": {
            "runtime_hardness_formula": "median_seed((lns_repair_seconds + lns_retime_seconds) / max(1, lns_iterations))",
            "tie_break": ["median_repair_seconds_desc", "block_count_asc", "instance_number_asc"],
            "seed_set": list(FINAL_SEEDS),
            "telemetry_source": telemetry_source,
        },
        "input_artifact": {
            "raw": str(input_raw.relative_to(REPO_ROOT)),
            "raw_sha256": sha256_file(input_raw),
            "source_commits": source_commits,
            "config_hashes": config_hashes,
            "dataset_sha256": dataset_hash,
            "record_count": len(by_key),
        },
        "instances": selected,
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(document, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return document


def hard10_instance_names(manifest_path: Path = HARD10_MANIFEST) -> tuple[str, ...]:
    try:
        document = json.loads(manifest_path.read_text(encoding="utf-8"))
        rows = document["instances"]
        names = tuple(str(row["instance"]) for row in rows)
    except (OSError, KeyError, TypeError, json.JSONDecodeError) as exc:
        raise ContractError(f"invalid hard-10 manifest: {exc}") from exc
    if len(names) != 10 or len(set(names)) != 10:
        raise ContractError("hard-10 manifest must contain exactly 10 unique instances")
    return names


def validate_baseline_contract(
    *, variant: str, budgets: Iterable[float], seeds: Iterable[int],
    requested_instances: Iterable[str] | None,
) -> None:
    if variant != FINAL_VARIANT:
        raise ContractError(f"baseline gate requires variant {FINAL_VARIANT!r}")
    if tuple(float(value) for value in budgets) != (60.0,):
        raise ContractError("baseline gate requires the exact 60-second anchor budget")
    if tuple(int(value) for value in seeds) != (20260710,):
        raise ContractError("baseline gate requires seed 20260710")
    if requested_instances is None or tuple(requested_instances) != hard10_instance_names():
        raise ContractError("baseline gate requires the frozen hard-10 manifest order")


def make_run_key(
    *, source_commit: str, dataset_hash: str, config_digest: str, variant: str,
    instance_hash: str, budget: float, seed: int,
) -> str:
    payload = {
        "schema_version": SCHEMA_VERSION,
        "source_commit": source_commit,
        "dataset_hash": dataset_hash,
        "config_hash": config_digest,
        "variant": variant,
        "instance_hash": instance_hash,
        "budget_seconds": float(budget),
        "seed": int(seed),
    }
    return sha256_bytes(canonical_json(payload).encode("utf-8"))


def validate_raw_record(record: Mapping[str, Any]) -> None:
    missing = [field for field in RAW_REQUIRED_FIELDS if field not in record]
    if missing:
        raise ContractError(f"raw record missing fields {missing}")
    if record["schema_version"] != SCHEMA_VERSION:
        raise ContractError("unsupported raw schema version")
    if record["status"] not in TERMINAL_STATUSES:
        raise ContractError(f"record is not terminal: {record['status']!r}")
    if not isinstance(record["run_key"], str) or len(record["run_key"]) != 64:
        raise ContractError("invalid run key")


def load_raw(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    records: list[dict[str, Any]] = []
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            raise ContractError(f"blank/partial raw line {line_number}")
        try:
            record = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ContractError(f"invalid raw JSON line {line_number}: {exc}") from exc
        if not isinstance(record, dict):
            raise ContractError(f"raw line {line_number} is not an object")
        validate_raw_record(record)
        records.append(record)
    keys = [record["run_key"] for record in records]
    if len(keys) != len(set(keys)):
        raise ContractError("duplicate run key in raw artifact")
    return records


def append_raw(path: Path, record: Mapping[str, Any]) -> None:
    validate_raw_record(record)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(canonical_json(record) + "\n")
        handle.flush()
        os.fsync(handle.fileno())


def reconstruct_snapshot(raw: Mapping[str, Any], operations: Mapping[str, Any]) -> SolutionSnapshot:
    entries: dict[int, tuple[int, int, int, int, int]] = {}
    exits: dict[int, int] = {}
    for date_text, events in operations.get("operations", {}).items():
        date = int(date_text)
        for event in events:
            block_id = int(event["block_id"])
            if event["type"] == "ENTRY":
                entries[block_id] = (
                    int(event["bay_id"]), int(event["orient_idx"]),
                    int(event["x"]), int(event["y"]), date,
                )
            elif event["type"] == "EXIT":
                exits[block_id] = date
    if set(entries) != set(exits) or len(entries) != len(raw["blocks"]):
        raise ContractError("cannot reconstruct complete snapshot from operations")
    placements = tuple(
        Placement(block_id, *entries[block_id][:4], entries[block_id][4], exits[block_id])
        for block_id in sorted(entries)
    )
    parsed = parse_instance(raw)
    return SolutionSnapshot(placements).with_objective(
        compute_objective(parsed, SolutionSnapshot(placements))
    )


def environment_versions() -> dict[str, Any]:
    result = {"python": platform.python_version(), "platform": platform.platform()}
    for distribution in ("shapely", "gurobipy"):
        try:
            result[distribution] = importlib.metadata.version(distribution)
        except importlib.metadata.PackageNotFoundError:
            result[distribution] = None
    return result


def _trace_non_increasing(values: Iterable[float]) -> bool:
    items = tuple(float(value) for value in values)
    return all(right <= left + 1e-9 for left, right in zip(items, items[1:]))


def _retime_z1_worsen(trace: Mapping[str, Any]) -> int:
    count = 0
    for item in trace.get("retiming", []):
        before = after = None
        for detail in item.get("diagnostics", []):
            if isinstance(detail, str) and detail.startswith("z1_before="):
                before = float(detail.split("=", 1)[1])
            if isinstance(detail, str) and detail.startswith("z1_after="):
                after = float(detail.split("=", 1)[1])
        if before is not None and after is not None and after > before + 1e-9:
            count += 1
    return count


def worker_record(request: Mapping[str, Any]) -> dict[str, Any]:
    started_wall = time.monotonic()
    started_utc = datetime.now(timezone.utc).isoformat()
    base = {
        "schema_version": SCHEMA_VERSION,
        "run_key": request["run_key"],
        "status": "exception",
        "instance": request["instance"],
        "instance_hash": request["instance_hash"],
        "dataset_hash": request["dataset_hash"],
        "variant": request["variant"],
        "config_hash": request["config_hash"],
        "source_commit": request["source_commit"],
        "budget_seconds": float(request["budget_seconds"]),
        "seed": int(request["seed"]),
        "start_utc": started_utc,
        "end_utc": started_utc,
        "elapsed_seconds": 0.0,
        "feasible": False,
        "stage": 0,
        "violations": [],
        "obj1": None,
        "obj2": None,
        "obj3": None,
        "objective": None,
        "internal_objective": None,
        "internal_checker_objective_error": None,
        "component_relative_errors": {},
        "phase_timings": {},
        "checker_timings": [],
        "model_stats": {},
        "construction_stats": [],
        "constructor_deadline_hit": False,
        "validated_best_trace": [],
        "validated_best_events": [],
        "trace_non_increasing": True,
        "lns_invocations": [],
        "operator_stats": {},
        "retime_z1_worsen_count": 0,
        "exception": None,
        "outer_timeout": False,
        "captured_stdout": "",
        "captured_stderr": "",
        "environment": environment_versions(),
    }
    trace = RunTrace()
    stdout = io.StringIO()
    stderr = io.StringIO()
    try:
        instance_path = Path(request["instance_path"])
        if sha256_file(instance_path) != request["instance_hash"]:
            raise ContractError("worker instance hash changed")
        raw = json.loads(instance_path.read_text(encoding="utf-8"))
        chosen = SubmissionConfig.for_benchmark(
            request["variant"], seed=int(request["seed"])
        )
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            operations = solve(
                raw,
                float(request["budget_seconds"]),
                config=chosen,
                trace=trace,
            )
        checked = check_feasibility(copy.deepcopy(raw), copy.deepcopy(operations))
        snapshot = reconstruct_snapshot(raw, operations)
        objective = snapshot.objective
        internal = (objective.z1, objective.z2, objective.z3, objective.total)
        external = (
            checked.get("obj1"), checked.get("obj2"),
            checked.get("obj3"), checked.get("objective"),
        )
        errors = [relative_error(left, right) for left, right in zip(internal, external)]
        trace_dict = trace.as_dict()
        base.update(
            status=("completed" if checked.get("feasible") is True and checked.get("stage") == 5 else "checker_failure"),
            feasible=checked.get("feasible") is True,
            stage=int(checked.get("stage", 0)),
            violations=list(checked.get("violations", [])),
            obj1=checked.get("obj1"), obj2=checked.get("obj2"),
            obj3=checked.get("obj3"), objective=checked.get("objective"),
            internal_objective=objective.total,
            internal_checker_objective_error=errors[-1],
            component_relative_errors=dict(zip(("obj1", "obj2", "obj3", "objective"), errors)),
            phase_timings=trace_dict["phase_times"],
            checker_timings=trace_dict["checker_durations"],
            model_stats={"assignment": trace_dict["assignment"], **trace_dict["model_stats"]},
            construction_stats=trace_dict["construction"],
            constructor_deadline_hit=any(
                bool(item.get("metrics", {}).get("deadline_hit"))
                for item in trace_dict["construction"]
            ),
            validated_best_trace=trace_dict["validated_best"],
            validated_best_events=trace_dict["validated_best_events"],
            lns_invocations=trace_dict["lns_invocations"],
            trace_non_increasing=_trace_non_increasing(trace_dict["validated_best"]),
            operator_stats=trace_dict["operator_stats"],
            retime_z1_worsen_count=_retime_z1_worsen(trace_dict),
            exception=(trace_dict["exceptions"] or None),
        )
    except Exception as exc:
        base["exception"] = f"{type(exc).__name__}: {exc}"
    finally:
        base["captured_stdout"] = stdout.getvalue()[:4000]
        base["captured_stderr"] = stderr.getvalue()[:4000]
        base["end_utc"] = datetime.now(timezone.utc).isoformat()
        base["elapsed_seconds"] = time.monotonic() - started_wall
    validate_raw_record(base)
    return base


def _empty_timeout_record(request: Mapping[str, Any], elapsed: float) -> dict[str, Any]:
    now = datetime.now(timezone.utc).isoformat()
    result = {
        field: None for field in RAW_REQUIRED_FIELDS
    }
    result.update(
        schema_version=SCHEMA_VERSION, run_key=request["run_key"], status="outer_timeout",
        instance=request["instance"], instance_hash=request["instance_hash"],
        dataset_hash=request["dataset_hash"], variant=request["variant"],
        config_hash=request["config_hash"], source_commit=request["source_commit"],
        budget_seconds=float(request["budget_seconds"]), seed=int(request["seed"]),
        start_utc=now, end_utc=now, elapsed_seconds=elapsed, feasible=False,
        stage=0, violations=["outer timeout"], obj1=None, obj2=None, obj3=None,
        objective=None, internal_objective=None,
        internal_checker_objective_error=None, component_relative_errors={},
        phase_timings={}, checker_timings=[], model_stats={},
        construction_stats=[],
        constructor_deadline_hit=False, validated_best_trace=[],
        validated_best_events=[],
        trace_non_increasing=True, lns_invocations=[], operator_stats={}, retime_z1_worsen_count=0,
        exception="outer timeout", outer_timeout=True, captured_stdout="",
        captured_stderr="", environment=environment_versions(),
    )
    validate_raw_record(result)
    return result


def run_worker_subprocess(request: Mapping[str, Any], timeout: float) -> dict[str, Any]:
    with tempfile.TemporaryDirectory(prefix="ogc-step9-worker-") as directory:
        root = Path(directory)
        request_path = root / "request.json"
        result_path = root / "result.json"
        request_path.write_text(canonical_json(request), encoding="utf-8")
        command = [
            sys.executable, str(Path(__file__).resolve()), "worker",
            "--request", str(request_path), "--result", str(result_path),
        ]
        started = time.monotonic()
        try:
            completed = subprocess.run(
                command,
                cwd=REPO_ROOT,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                timeout=timeout,
                check=False,
            )
        except subprocess.TimeoutExpired:
            return _empty_timeout_record(request, time.monotonic() - started)
        if not result_path.exists():
            record = _empty_timeout_record(request, time.monotonic() - started)
            record["status"] = "exception"
            record["outer_timeout"] = False
            record["exception"] = f"worker exit={completed.returncode} missing result"
            record["captured_stdout"] = completed.stdout[:4000]
            record["captured_stderr"] = completed.stderr[:4000]
            return record
        record = json.loads(result_path.read_text(encoding="utf-8"))
        validate_raw_record(record)
        if completed.stdout or completed.stderr:
            record["captured_stdout"] = (record.get("captured_stdout", "") + completed.stdout)[:4000]
            record["captured_stderr"] = (record.get("captured_stderr", "") + completed.stderr)[:4000]
        return record


def calibrate(instance_path: Path) -> dict[str, float]:
    launcher = []
    for _ in range(5):
        started = time.monotonic()
        subprocess.run([sys.executable, "-c", "pass"], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        launcher.append(time.monotonic() - started)
    raw = json.loads(instance_path.read_text(encoding="utf-8"))
    trace = RunTrace()
    operations = solve(
        raw, 0.0,
        config=SubmissionConfig.for_benchmark("safe_fallback", seed=20260710),
        trace=trace,
    )
    checker = []
    for _ in range(5):
        started = time.monotonic()
        result = check_feasibility(copy.deepcopy(raw), copy.deepcopy(operations))
        checker.append(time.monotonic() - started)
        if result.get("feasible") is not True or result.get("stage") != 5:
            raise ContractError("calibration safe output failed official checker")
    return {"launcher_p95": percentile(launcher, 0.95), "checker_p95": percentile(checker, 0.95)}


def outer_timeout(budget: float, calibration: Mapping[str, float]) -> float:
    margin = max(
        5.0,
        2.0 * float(calibration["checker_p95"])
        + float(calibration["launcher_p95"])
        + 1.0,
    )
    return max(0.0, float(budget)) + margin


def git_value(*args: str) -> str:
    completed = subprocess.run(
        ["git", *args], cwd=REPO_ROOT, text=True,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False,
    )
    if completed.returncode:
        raise ContractError(completed.stderr.strip() or "git command failed")
    return completed.stdout.strip()


def summarize(
    *, run_id: str, records: list[dict[str, Any]], planned_keys: set[str],
    dataset_hash: str, config_digest: str, source_commit: str,
    raw_hash: str, calibration_values: Mapping[str, float], command: list[str], gate: str,
) -> dict[str, Any]:
    validate_keys = [record["run_key"] for record in records]
    duplicate_count = len(validate_keys) - len(set(validate_keys))
    missing = planned_keys - set(validate_keys)
    unexpected = set(validate_keys) - planned_keys
    if unexpected:
        raise ContractError(f"raw contains unexpected run keys: {sorted(unexpected)}")
    errors = [
        float(record["internal_checker_objective_error"])
        for record in records
        if record["internal_checker_objective_error"] is not None
    ]
    regressions = 0
    groups: dict[tuple[str, int], list[dict[str, Any]]] = {}
    for record in records:
        groups.setdefault((record["instance"], int(record["seed"])), []).append(record)
    for items in groups.values():
        ordered = sorted(items, key=lambda item: float(item["budget_seconds"]))
        for shorter, longer in zip(ordered, ordered[1:]):
            if (
                shorter["objective"] is not None and longer["objective"] is not None
                and float(longer["objective"]) > float(shorter["objective"]) + 1e-6
            ):
                regressions += 1
    constructor_times = [
        float(record.get("phase_timings", {}).get("constructor", 0.0))
        for record in records
    ]
    stage5_count = sum(record["feasible"] is True and record["stage"] == 5 for record in records)
    exception_count = sum(record["exception"] is not None for record in records)
    timeout_count = sum(record["outer_timeout"] is True for record in records)
    trace_regressions = sum(not record.get("trace_non_increasing", False) for record in records)
    retime_worsen = sum(int(record.get("retime_z1_worsen_count", 0)) for record in records)
    parity = max(errors, default=0.0)
    base_pass = (
        len(records) == len(planned_keys)
        and not missing and duplicate_count == 0
        and stage5_count == len(planned_keys)
        and exception_count == 0 and timeout_count == 0
        and parity <= 1e-6 and trace_regressions == 0 and retime_worsen == 0
    )
    gate_pass = base_pass
    if gate == "constructor":
        gate_pass = gate_pass and percentile(constructor_times, 0.90) <= 8.0 and max(constructor_times, default=0.0) <= 12.0 and not any(record["constructor_deadline_hit"] for record in records)
    if gate in {"final", "phase0"}:
        gate_pass = gate_pass and regressions == 0 and len(planned_keys) == FINAL_EXPECTED_RUN_COUNT
    return {
        "schema_version": SCHEMA_VERSION,
        "run_id": run_id,
        "gate": gate,
        "dataset_sha256": dataset_hash,
        "config_sha256": config_digest,
        "commit": source_commit,
        "run_count": len(records),
        "unique_key_count": len(set(validate_keys)),
        "expected_run_count": len(planned_keys),
        "missing_count": len(missing),
        "duplicate_count": duplicate_count,
        "stage5_count": stage5_count,
        "exception_count": exception_count,
        "outer_timeout_count": timeout_count,
        "checker_failure_count": sum(record["status"] == "checker_failure" for record in records),
        "objective_parity_max_relative_error": parity,
        "trace_regression_count": trace_regressions,
        "retime_z1_worsen_count": retime_worsen,
        "longer_budget_regression_count": regressions,
        "constructor_p90_seconds": percentile(constructor_times, 0.90),
        "constructor_max_seconds": max(constructor_times, default=0.0),
        "constructor_deadline_hit_count": sum(record["constructor_deadline_hit"] for record in records),
        "constructor_timebox_exhausted_count": sum(
            any(
                bool(item.get("metrics", {}).get("timebox_exhausted"))
                for item in record.get("construction_stats", [])
            )
            for record in records
        ),
        "gate_pass": gate_pass,
        "raw_sha256": raw_hash,
        "calibration": dict(calibration_values),
        "command": command,
        "environment": environment_versions(),
    }


def validate_summary(summary: Mapping[str, Any]) -> None:
    missing = [field for field in SUMMARY_REQUIRED_FIELDS if field not in summary]
    if missing:
        raise ContractError(f"summary missing fields {missing}")
    if summary["run_count"] != summary["unique_key_count"] + summary["duplicate_count"]:
        raise ContractError("summary raw/unique/duplicate counts disagree")


def _anytime_integral(
    record: Mapping[str, Any], best_objective: float
) -> tuple[float, float | None]:
    elapsed = max(0.0, float(record["elapsed_seconds"]))
    events = [
        (max(0.0, float(item["elapsed_seconds"])), float(item["objective"]))
        for item in record.get("validated_best_events", [])
    ]
    if not events or elapsed <= 0.0:
        return 0.0, None
    events.sort()
    first_improvement = events[1][0] if len(events) > 1 else None
    scale = max(1.0, abs(best_objective))
    current = events[0][1]
    cursor = 0.0
    area = 0.0
    for event_time, objective in events[1:]:
        event_time = min(elapsed, event_time)
        area += max(0.0, (current - best_objective) / scale) * max(0.0, event_time - cursor)
        cursor = event_time
        current = min(current, objective)
    area += max(0.0, (current - best_objective) / scale) * max(0.0, elapsed - cursor)
    return area / elapsed, first_improvement


def build_comparison(records: list[dict[str, Any]], reference: str) -> dict[str, Any]:
    variants = sorted({record["variant"] for record in records})
    if reference not in variants:
        raise ContractError(f"comparison reference {reference!r} was not executed")
    scenarios: dict[tuple[str, float, int], dict[str, dict[str, Any]]] = {}
    for record in records:
        key = (record["instance"], float(record["budget_seconds"]), int(record["seed"]))
        by_variant = scenarios.setdefault(key, {})
        if record["variant"] in by_variant:
            raise ContractError(f"duplicate comparison scenario {key} variant={record['variant']}")
        by_variant[record["variant"]] = record
    instances, _ = validate_dataset()
    weights = {
        name: json.loads(path.read_text(encoding="utf-8"))["weights"]
        for name, path in instances.items()
    }
    per_instance: dict[str, dict[str, dict[str, int]]] = {}
    variant_metrics: dict[str, dict[str, Any]] = {
        variant: {
            "borda": 0.0,
            "relative_gaps": [],
            "weighted_component_improvements": {"w1_delta_z1": [], "w2_delta_z2": [], "w3_delta_z3": []},
            "first_improvement_seconds": [],
            "anytime_primal_integral": [],
            "operator_activity": {},
            "runs": 0,
            "feasible": 0,
            "timeouts": 0,
            "exceptions": 0,
        }
        for variant in variants
    }
    for key, by_variant in scenarios.items():
        successful = {
            variant: record for variant, record in by_variant.items()
            if record["feasible"] is True and record["stage"] == 5 and record["objective"] is not None
        }
        best = min((float(record["objective"]) for record in successful.values()), default=None)
        ordered_values = sorted({float(record["objective"]) for record in successful.values()})
        for variant, record in by_variant.items():
            metric = variant_metrics[variant]
            metric["runs"] += 1
            metric["feasible"] += int(record["feasible"] is True and record["stage"] == 5)
            metric["timeouts"] += int(record["outer_timeout"] is True)
            metric["exceptions"] += int(record["exception"] is not None)
            if variant in successful and best is not None:
                objective = float(record["objective"])
                rank = ordered_values.index(objective) + 1
                metric["borda"] += len(variants) - rank
                metric["relative_gaps"].append((objective - best) / max(1.0, abs(best)))
                integral, first = _anytime_integral(record, best)
                metric["anytime_primal_integral"].append(integral)
                if first is not None:
                    metric["first_improvement_seconds"].append(first)
            for operator, values in record.get("operator_stats", {}).items():
                aggregate = metric["operator_activity"].setdefault(
                    operator,
                    {"attempts": 0, "feasible": 0, "accepted": 0, "new_best": 0},
                )
                for field in aggregate:
                    aggregate[field] += int(values.get(field, 0))
        reference_record = successful.get(reference)
        if reference_record is None:
            continue
        instance_name = key[0]
        instance_table = per_instance.setdefault(instance_name, {})
        for variant, record in successful.items():
            result = instance_table.setdefault(variant, {"wins": 0, "ties": 0, "losses": 0})
            delta = float(reference_record["objective"]) - float(record["objective"])
            if delta > 1e-6:
                result["wins"] += 1
            elif delta < -1e-6:
                result["losses"] += 1
            else:
                result["ties"] += 1
            component = variant_metrics[variant]["weighted_component_improvements"]
            w = weights[instance_name]
            component["w1_delta_z1"].append(float(w["w1"]) * (float(reference_record["obj1"]) - float(record["obj1"])))
            component["w2_delta_z2"].append(float(w["w2"]) * (float(reference_record["obj2"]) - float(record["obj2"])))
            component["w3_delta_z3"].append(float(w["w3"]) * (float(reference_record["obj3"]) - float(record["obj3"])))
    summaries: dict[str, Any] = {}
    for variant, metric in variant_metrics.items():
        gaps = metric.pop("relative_gaps")
        first = metric.pop("first_improvement_seconds")
        integrals = metric.pop("anytime_primal_integral")
        components = metric.pop("weighted_component_improvements")
        summaries[variant] = {
            **metric,
            "best_observed_relative_gap": min(gaps, default=None),
            "relative_gap_median": statistics.median(gaps) if gaps else None,
            "relative_gap_p90": percentile(gaps, 0.90) if gaps else None,
            "relative_gap_worst": max(gaps, default=None),
            "time_to_first_improvement_median": statistics.median(first) if first else None,
            "anytime_primal_integral_mean": statistics.fmean(integrals) if integrals else None,
            "weighted_component_improvement_mean": {
                name: statistics.fmean(values) if values else None
                for name, values in components.items()
            },
        }
    return {
        "schema_version": SCHEMA_VERSION,
        "reference_variant": reference,
        "executed_variants": variants,
        "scenario_count": len(scenarios),
        "per_instance_wtl": per_instance,
        "variant_summary": summaries,
        "environment": environment_versions(),
    }


def command_compare(args: argparse.Namespace) -> int:
    records: list[dict[str, Any]] = []
    raw_hashes: dict[str, str] = {}
    for value in args.runs:
        path = Path(value).resolve()
        raw = path / RAW_NAME if path.is_dir() else path
        loaded = load_raw(raw)
        records.extend(loaded)
        raw_hashes[str(raw)] = sha256_file(raw)
    comparison = build_comparison(records, args.reference)
    comparison["input_raw_sha256"] = raw_hashes
    output = Path(args.output).resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(comparison, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(canonical_json({"comparison": str(output), "sha256": sha256_file(output)}))
    return 0


def command_run(args: argparse.Namespace) -> int:
    instances, dataset_hash = validate_dataset()
    if args.gate == "baseline":
        validate_baseline_contract(
            variant=args.variant, budgets=args.budgets, seeds=args.seeds,
            requested_instances=args.instances,
        )
    if args.gate in {"final", "phase0"}:
        validate_final_matrix_contract(
            variant=args.variant, budgets=args.budgets, seeds=args.seeds,
            requested_instances=args.instances,
        )
        if len(instances) != FINAL_INSTANCE_COUNT:
            raise ContractError(
                f"{args.gate} gate requires {FINAL_INSTANCE_COUNT} official instances, got {len(instances)}"
            )
    if args.instances:
        requested = args.instances
        unknown = sorted(set(requested) - set(instances))
        if unknown:
            raise ContractError(f"unknown subset instances {unknown}")
        instances = {name: instances[name] for name in requested}
    source_commit = args.source_commit or git_value("rev-parse", "HEAD")
    if not args.allow_dirty and git_value("status", "--porcelain"):
        raise ContractError("benchmark source tree is dirty; commit or pass --allow-dirty for non-final probes")
    digest = config_hash(args.variant)
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    run_id = args.run_id or f"step9-{args.gate}-{timestamp}-{source_commit[:12]}-{digest[:12]}"
    artifact_root = (
        PERFORMANCE_ARTIFACT_ROOT
        if args.gate in {"baseline", "phase0"}
        else ARTIFACT_ROOT
    )
    run_dir = artifact_root / run_id
    raw_path = run_dir / RAW_NAME
    summary_path = run_dir / SUMMARY_NAME
    run_dir.mkdir(parents=True, exist_ok=True)
    calibration_values = calibrate(next(iter(instances.values())))
    requests: list[dict[str, Any]] = []
    for name, path in instances.items():
        instance_digest = sha256_file(path)
        for budget in args.budgets:
            for seed in args.seeds:
                key = make_run_key(
                    source_commit=source_commit, dataset_hash=dataset_hash,
                    config_digest=digest, variant=args.variant,
                    instance_hash=instance_digest, budget=budget, seed=seed,
                )
                requests.append({
                    "run_key": key, "instance": name, "instance_path": str(path),
                    "instance_hash": instance_digest, "dataset_hash": dataset_hash,
                    "variant": args.variant, "config_hash": digest,
                    "source_commit": source_commit, "budget_seconds": float(budget),
                    "seed": int(seed),
                })
    records = load_raw(raw_path)
    completed = {record["run_key"] for record in records}
    planned_keys = {request["run_key"] for request in requests}
    if completed - planned_keys:
        raise ContractError("resume artifact contains keys outside the requested matrix")
    for index, request in enumerate(requests, 1):
        if request["run_key"] in completed:
            continue
        record = run_worker_subprocess(
            request, outer_timeout(request["budget_seconds"], calibration_values)
        )
        append_raw(raw_path, record)
        records.append(record)
        print(
            f"[{index}/{len(requests)}] {request['instance']} TL={request['budget_seconds']:g} "
            f"seed={request['seed']} status={record['status']} elapsed={record['elapsed_seconds']:.3f}",
            flush=True,
        )
    records = load_raw(raw_path)
    raw_hash = sha256_file(raw_path)
    summary = summarize(
        run_id=run_id, records=records, planned_keys=planned_keys,
        dataset_hash=dataset_hash, config_digest=digest, source_commit=source_commit,
        raw_hash=raw_hash, calibration_values=calibration_values,
        command=[sys.executable, str(Path(__file__).resolve()), *sys.argv[1:]], gate=args.gate,
    )
    validate_summary(summary)
    summary_path.write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    summary_hash = sha256_file(summary_path)
    (run_dir / "SHA256SUMS").write_text(
        f"{raw_hash}  {RAW_NAME}\n{summary_hash}  {SUMMARY_NAME}\n", encoding="utf-8"
    )
    print(canonical_json({"summary": str(summary_path), "summary_sha256": summary_hash, "gate_pass": summary["gate_pass"]}))
    return 0 if summary["gate_pass"] else 2


def submission_members() -> dict[str, Path]:
    members = {
        "myalgorithm.py": REPO_ROOT / "baseline/myalgorithm.py",
        "utils.py": REPO_ROOT / "baseline/utils.py",
    }
    for path in sorted((REPO_ROOT / "baseline/solver").glob("*.py")):
        members[f"solver/{path.name}"] = path
    return members


def validate_archive_members(names: Iterable[str]) -> None:
    names = tuple(names)
    if "myalgorithm.py" not in names:
        raise ContractError("archive root myalgorithm.py is missing")
    if "utils.py" not in names:
        raise ContractError("archive root utils.py is missing")
    if len(names) != len(set(names)):
        raise ContractError("archive contains duplicate paths")
    for name in names:
        path = Path(name)
        if path.is_absolute() or ".." in path.parts:
            raise ContractError(f"unsafe archive path {name}")
        if path.name in FORBIDDEN_ARCHIVE_NAMES:
            raise ContractError(f"forbidden archive member {name}")
        if path.suffix != ".py" or (path.parent != Path(".") and path.parent != Path("solver")):
            raise ContractError(f"member outside submission allowlist {name}")
        lowered = name.lower()
        if any(token in lowered for token in ("test", "data", "artifact", "cache", "license")):
            raise ContractError(f"forbidden archive content {name}")


def build_archive(output: Path) -> dict[str, Any]:
    members = submission_members()
    validate_archive_members(members)
    output.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        for name, source in members.items():
            archive.write(source, name)
    with zipfile.ZipFile(output) as archive:
        validate_archive_members(archive.namelist())
    size = output.stat().st_size
    if size > 15 * 1024 * 1024:
        raise ContractError(f"archive exceeds 15MB: {size}")
    return {
        "path": str(output), "sha256": sha256_file(output), "size_bytes": size,
        "members": list(members),
    }


def command_archive(args: argparse.Namespace) -> int:
    output = Path(args.output).resolve()
    evidence = build_archive(output)
    print(json.dumps(evidence, indent=2, sort_keys=True))
    return 0


def command_worker(args: argparse.Namespace) -> int:
    request = json.loads(Path(args.request).read_text(encoding="utf-8"))
    record = worker_record(request)
    Path(args.result).write_text(canonical_json(record), encoding="utf-8")
    return 0


def command_freeze_hard10(args: argparse.Namespace) -> int:
    input_raw = Path(args.input_raw).resolve()
    output = Path(args.output).resolve()
    document = freeze_hard10_manifest(input_raw, output)
    print(canonical_json({"manifest": str(output), "sha256": sha256_file(output), "instances": [row["instance"] for row in document["instances"]]}))
    return 0


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(description=__doc__)
    commands = root.add_subparsers(dest="command", required=True)
    run = commands.add_parser("run")
    run.add_argument("--gate", choices=("baseline", "constructor", "feature", "final", "phase0", "exploitation"), required=True)
    run.add_argument("--variant", choices=VARIANTS, default="constructor_retime")
    run.add_argument("--budgets", nargs="+", type=float, required=True)
    run.add_argument("--seeds", nargs="+", type=int, required=True)
    run.add_argument("--instances", nargs="+")
    run.add_argument("--run-id")
    run.add_argument("--source-commit")
    run.add_argument("--allow-dirty", action="store_true")
    run.set_defaults(function=command_run)
    worker = commands.add_parser("worker")
    worker.add_argument("--request", required=True)
    worker.add_argument("--result", required=True)
    worker.set_defaults(function=command_worker)
    freeze = commands.add_parser("freeze-hard10")
    freeze.add_argument("--input-raw", required=True)
    freeze.add_argument("--output", default=str(HARD10_MANIFEST))
    freeze.set_defaults(function=command_freeze_hard10)
    archive = commands.add_parser("archive")
    archive.add_argument("--output", required=True)
    archive.set_defaults(function=command_archive)
    compare = commands.add_parser("compare")
    compare.add_argument("--runs", nargs="+", required=True)
    compare.add_argument("--reference", choices=VARIANTS, default="safe_fallback")
    compare.add_argument("--output", required=True)
    compare.set_defaults(function=command_compare)
    return root


def main() -> int:
    args = parser().parse_args()
    try:
        return int(args.function(args))
    except ContractError as exc:
        print(f"contract error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
