"""S3 deadline-fill telemetry schema and deterministic synthetic gates."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import math
from pathlib import Path
import statistics
from types import MappingProxyType
from typing import Any, Iterable, Mapping, Sequence

from .selectors import REPO_ROOT


CANDIDATE_MANIFEST_PATH = Path(
    "benchmarks/manifests/s3-anytime-fill-candidate.json"
)
S3_ANYTIME_TELEMETRY_FIELDS = (
    "run_id",
    "commit",
    "dirty_diff_hash",
    "instance_id",
    "instance_sha",
    "seed",
    "timelimit",
    "wall_seconds",
    "work_deadline_seconds",
    "hard_deadline_seconds",
    "anchor_seconds",
    "anchor_objective",
    "anchor_solution_sha256",
    "s3_fill_seconds",
    "s3_fill_useful_seconds",
    "idle_seconds",
    "finalization_seconds",
    "s3_fill_segments",
    "s3_fill_batches",
    "s3_fill_iterations",
    "s3_fill_accepted",
    "s3_fill_improvements",
    "s3_fill_restarts",
    "s3_fill_deadlines",
    "s3_fill_faults",
    "s3_fill_stopped_reason",
    "time_to_best_seconds",
    "late_improvement_count",
    "checker_stage",
    "feasible",
    "objective",
    "obj1",
    "obj2",
    "obj3",
    "final_solution_sha256",
    "verification_count",
    "termination_reason",
    "fallback_reason",
)
S3_ANYTIME_EVIDENCE_FIELDS = (
    "record_id",
    "complete",
    "status",
    "profile",
    "features",
    "incumbent_objective_trace",
    "checker_solution_sha256",
)
EXPECTED_RECORD_IDS = (
    "prob_21|tl=120",
    "prob_22|tl=120",
    "prob_23|tl=120",
    "prob_24|tl=120",
    "prob_25|tl=120",
    "prob_21|tl=60",
    "prob_21|tl=300",
)
QUALIFICATION_THRESHOLDS = MappingProxyType(
    {
        "minimum_useful_fraction_of_work_deadline": 0.90,
        "minimum_segments_per_record": 1,
        "minimum_batches_per_record": 2,
        "minimum_iterations_per_record": 1,
        "scaling_short_timelimit_seconds": 60.0,
        "scaling_long_timelimit_seconds": 300.0,
        "late_improvements_minimum": 1,
        "objective_relative_tolerance": 1e-9,
        "objective_absolute_tolerance": 1e-6,
    }
)

_USEFUL_CATEGORIES = frozenset({"candidate", "repair", "retime", "checker"})
_IDLE_CATEGORIES = frozenset({"idle", "no_op", "sleep"})
_FINALIZATION_CATEGORIES = frozenset({"finalization", "serialization"})
_ALLOWED_CATEGORIES = (
    frozenset({"anchor"})
    | _USEFUL_CATEGORIES
    | _IDLE_CATEGORIES
    | _FINALIZATION_CATEGORIES
)
_HEX = frozenset("0123456789abcdef")


@dataclass(frozen=True, slots=True)
class HarnessProfile:
    """One explicit harness feature selection."""

    name: str
    s3_anytime_fill: bool

    @property
    def features(self) -> Mapping[str, str]:
        return MappingProxyType(
            {"s3_anytime_fill": str(self.s3_anytime_fill).lower()}
        )

    @property
    def solver_overrides(self) -> Mapping[str, bool]:
        return MappingProxyType({"_s3_anytime_fill": self.s3_anytime_fill})


PUBLIC_PROFILE = HarnessProfile("public", False)
DEFAULT_PROFILE = HarnessProfile("default", False)
CANDIDATE_PROFILE = HarnessProfile("s3-anytime-fill-candidate", True)
PROFILES = MappingProxyType(
    {item.name: item for item in (PUBLIC_PROFILE, DEFAULT_PROFILE, CANDIDATE_PROFILE)}
)


def profile_by_name(name: str) -> HarnessProfile:
    """Resolve an exact named profile; implicit feature overrides are rejected."""
    try:
        return PROFILES[str(name)]
    except KeyError as exc:
        raise ValueError(f"unknown S3 anytime profile: {name}") from exc


def canonical_sha256(payload: Mapping[str, Any]) -> str:
    encoded = json.dumps(
        dict(payload), sort_keys=True, separators=(",", ":")
    ).encode()
    return hashlib.sha256(encoded).hexdigest()


def load_candidate_manifest(path: Path | None = None) -> dict[str, Any]:
    """Load and verify the AF-04 draft without treating it as an AF-05 freeze."""
    relative = CANDIDATE_MANIFEST_PATH if path is None else Path(path)
    absolute = relative if relative.is_absolute() else REPO_ROOT / relative
    payload = json.loads(absolute.read_text(encoding="utf-8"))
    contract = payload.get("qualification_contract")
    if not isinstance(contract, dict):
        raise ValueError("S3 anytime qualification contract is missing")
    if canonical_sha256(contract) != payload.get("qualification_contract_sha256"):
        raise ValueError("S3 anytime qualification contract hash mismatch")
    if payload.get("status") != "UNQUALIFIED_DRAFT":
        raise ValueError("AF-04 manifest must remain UNQUALIFIED_DRAFT")
    if payload.get("source_path") != CANDIDATE_MANIFEST_PATH.as_posix():
        raise ValueError("S3 anytime candidate manifest source path changed")
    candidate = payload.get("candidate", {})
    if candidate.get("profile") != CANDIDATE_PROFILE.name:
        raise ValueError("S3 anytime candidate profile changed")
    if candidate.get("features") != dict(CANDIDATE_PROFILE.features):
        raise ValueError("S3 anytime candidate feature set changed")
    if candidate.get("public_default") is not False:
        raise ValueError("AF-04 public default must remain false")
    if tuple(contract.get("expected_record_ids", ())) != EXPECTED_RECORD_IDS:
        raise ValueError("S3 anytime expected record plan changed")
    if contract.get("thresholds") != dict(QUALIFICATION_THRESHOLDS):
        raise ValueError("S3 anytime frozen thresholds changed")
    if tuple(contract.get("required_telemetry_fields", ())) != S3_ANYTIME_TELEMETRY_FIELDS:
        raise ValueError("S3 anytime telemetry schema changed")
    if tuple(contract.get("required_evidence_fields", ())) != S3_ANYTIME_EVIDENCE_FIELDS:
        raise ValueError("S3 anytime evidence schema changed")
    return payload


def summarize_timing_intervals(
    intervals: Sequence[Mapping[str, Any]],
) -> dict[str, float]:
    """Union actual wall intervals; sleep/no-op/serialization are never useful."""
    by_category: dict[str, list[tuple[float, float]]] = {}
    for index, item in enumerate(intervals):
        category = str(item.get("category", ""))
        if category not in _ALLOWED_CATEGORIES:
            raise ValueError(f"unknown timing category at index {index}: {category}")
        start = _finite_float(item.get("start"))
        end = _finite_float(item.get("end"))
        if start is None or end is None or end < start:
            raise ValueError(f"invalid timing interval at index {index}")
        if end > start:
            by_category.setdefault(category, []).append((start, end))

    anchor = _merge(by_category.get("anchor", ()))
    finalization_raw = _merge(
        interval
        for category in _FINALIZATION_CATEGORIES
        for interval in by_category.get(category, ())
    )
    finalization = _subtract(finalization_raw, anchor)
    useful_raw = _merge(
        interval
        for category in _USEFUL_CATEGORIES
        for interval in by_category.get(category, ())
    )
    idle_raw = _merge(
        interval
        for category in _IDLE_CATEGORIES
        for interval in by_category.get(category, ())
    )
    fill = _subtract(_merge((*useful_raw, *idle_raw)), (*anchor, *finalization))
    idle = _intersection(idle_raw, fill)
    useful = _subtract(_intersection(useful_raw, fill), idle)
    return {
        "anchor_seconds": _duration(anchor),
        "s3_fill_seconds": _duration(fill),
        "s3_fill_useful_seconds": _duration(useful),
        "idle_seconds": _duration(idle),
        "finalization_seconds": _duration(finalization),
    }


def evaluate_s3_anytime_qualification(
    records: Sequence[Mapping[str, Any]],
    manifest: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Evaluate frozen synthetic or captured records without running a solver."""
    frozen = load_candidate_manifest() if manifest is None else dict(manifest)
    contract = frozen.get("qualification_contract", frozen)
    thresholds = dict(contract.get("thresholds", QUALIFICATION_THRESHOLDS))
    expected = tuple(contract.get("expected_record_ids", EXPECTED_RECORD_IDS))
    rows = [dict(record) for record in records]
    completeness_failures: list[str] = []
    actual = tuple(str(row.get("record_id")) for row in rows)
    if actual != expected:
        completeness_failures.append(
            "RECORD_PLAN_MISMATCH:record order or cardinality differs from frozen plan"
        )
    required = (*S3_ANYTIME_EVIDENCE_FIELDS, *S3_ANYTIME_TELEMETRY_FIELDS)
    for index, row in enumerate(rows):
        label = str(row.get("record_id", f"index={index}"))
        for field in required:
            if field not in row:
                completeness_failures.append(f"MISSING_FIELD:{label}:{field}")
    if completeness_failures:
        gates = {
            name: _not_evaluated(completeness_failures)
            for name in ("safety", "time", "scaling", "quality")
        }
        return {
            "status": "incomplete",
            "decision": "INCOMPLETE",
            "decision_reasons": list(completeness_failures),
            "candidate_profile": CANDIDATE_PROFILE.name,
            "records_evaluated": len(rows),
            "expected_record_ids": list(expected),
            "gates": gates,
        }

    safety: list[str] = []
    time_failures: list[str] = []
    scaling: list[str] = []
    quality: list[str] = []
    relative_deltas: list[float] = []
    late_improvements = 0
    run_ids: set[str] = set()
    commits: set[str] = set()

    for row in rows:
        record_id = str(row["record_id"])
        run_ids.add(str(row["run_id"]))
        commits.add(str(row["commit"]))
        if row["complete"] is not True or row["status"] != "passed":
            _failure(safety, "RECORD_NOT_PASSED", record_id)
        if row["profile"] != CANDIDATE_PROFILE.name:
            _failure(safety, "PROFILE_MISMATCH", record_id)
        if row["features"] != dict(CANDIDATE_PROFILE.features):
            _failure(safety, "FEATURE_MISMATCH", record_id)
        if row["dirty_diff_hash"] != "clean":
            _failure(safety, "DIRTY_SOURCE", record_id)
        if not _hex_digest(row["commit"], 40):
            _failure(safety, "INVALID_COMMIT", record_id)
        for field in (
            "instance_sha",
            "anchor_solution_sha256",
            "final_solution_sha256",
            "checker_solution_sha256",
        ):
            if not _hex_digest(row[field], 64):
                _failure(safety, "INVALID_SHA256", record_id, field)
        if row["final_solution_sha256"] != row["checker_solution_sha256"]:
            _failure(safety, "FINAL_CHECKER_SHA_MISMATCH", record_id)
        if int(row["seed"]) != 20260710:
            _failure(safety, "SEED_MISMATCH", record_id)
        if row["feasible"] is not True or int(row["checker_stage"]) != 5:
            _failure(safety, "CHECKER_NOT_STAGE5", record_id)
        if int(row["verification_count"]) <= 0:
            _failure(safety, "NO_VERIFICATION", record_id)
        if int(row["s3_fill_faults"]) != 0:
            _failure(safety, "S3_FILL_FAULT", record_id)
        if row["fallback_reason"] not in {None, ""}:
            _failure(safety, "FALLBACK_USED", record_id)

        timelimit = _number(row["timelimit"])
        wall = _number(row["wall_seconds"])
        work_deadline = _number(row["work_deadline_seconds"])
        hard_deadline = _number(row["hard_deadline_seconds"])
        anchor_seconds = _number(row["anchor_seconds"])
        fill_seconds = _number(row["s3_fill_seconds"])
        useful_seconds = _number(row["s3_fill_useful_seconds"])
        idle_seconds = _number(row["idle_seconds"])
        finalization_seconds = _number(row["finalization_seconds"])
        time_to_best = _number(row["time_to_best_seconds"])
        numeric_times = (
            timelimit,
            wall,
            work_deadline,
            hard_deadline,
            anchor_seconds,
            fill_seconds,
            useful_seconds,
            idle_seconds,
            finalization_seconds,
            time_to_best,
        )
        if any(value is None or value < 0.0 for value in numeric_times):
            _failure(time_failures, "INVALID_TIME_VALUE", record_id)
        else:
            assert timelimit is not None
            assert wall is not None
            assert work_deadline is not None
            assert hard_deadline is not None
            assert anchor_seconds is not None
            assert fill_seconds is not None
            assert useful_seconds is not None
            assert idle_seconds is not None
            assert finalization_seconds is not None
            expected_work = timelimit - _deadline_reserve(timelimit)
            if not math.isclose(work_deadline, expected_work, abs_tol=1e-9):
                _failure(time_failures, "WORK_DEADLINE_MISMATCH", record_id)
            if not math.isclose(hard_deadline, timelimit, abs_tol=1e-9):
                _failure(safety, "HARD_DEADLINE_MISMATCH", record_id)
            if wall > hard_deadline + 1e-9:
                _failure(safety, "HARD_DEADLINE_OVERRUN", record_id)
            if anchor_seconds + fill_seconds + finalization_seconds > wall + 1e-6:
                _failure(time_failures, "PHASE_TIME_DOUBLE_COUNT", record_id)
            if useful_seconds + idle_seconds > fill_seconds + 1e-6:
                _failure(time_failures, "FILL_TIME_DOUBLE_COUNT", record_id)
            minimum_useful = (
                float(thresholds["minimum_useful_fraction_of_work_deadline"])
                * work_deadline
            )
            if useful_seconds + 1e-9 < minimum_useful:
                _failure(time_failures, "USEFUL_TIME_BELOW_MINIMUM", record_id)
            if (
                row["s3_fill_stopped_reason"] == "max_iterations"
                and wall + 1e-9 < work_deadline
            ):
                _failure(time_failures, "EARLY_MAX_ITERATIONS", record_id)
            if row["termination_reason"] != "work_deadline":
                _failure(time_failures, "NOT_WORK_DEADLINE_TERMINATION", record_id)
            if row["s3_fill_stopped_reason"] != "work_deadline":
                _failure(time_failures, "NOT_WORK_DEADLINE_STOP", record_id)

        anchor_objective = _number(row["anchor_objective"])
        objective = _number(row["objective"])
        if anchor_objective is None or objective is None:
            _failure(safety, "INVALID_OBJECTIVE", record_id)
            _failure(quality, "INVALID_OBJECTIVE", record_id)
        else:
            tolerance = max(
                float(thresholds["objective_absolute_tolerance"]),
                float(thresholds["objective_relative_tolerance"])
                * abs(anchor_objective),
            )
            if objective > anchor_objective + tolerance:
                _failure(safety, "ANCHOR_REGRESSION", record_id)
                _failure(quality, "ANCHOR_REGRESSION", record_id)
            relative_deltas.append(
                (objective - anchor_objective) / max(abs(anchor_objective), 1.0)
            )
            if not _trace_is_nonincreasing(row["incumbent_objective_trace"]):
                _failure(safety, "TRACE_INCREASED", record_id)

        segments = _nonnegative_int(row["s3_fill_segments"])
        batches = _nonnegative_int(row["s3_fill_batches"])
        iterations = _nonnegative_int(row["s3_fill_iterations"])
        if segments is None or segments < int(thresholds["minimum_segments_per_record"]):
            _failure(scaling, "NO_SEGMENTS", record_id)
        if batches is None or batches < int(thresholds["minimum_batches_per_record"]):
            _failure(scaling, "INSUFFICIENT_BATCHES", record_id)
        if iterations is None or iterations < int(thresholds["minimum_iterations_per_record"]):
            _failure(scaling, "NO_ITERATIONS", record_id)
        if float(row["timelimit"]) == 120.0:
            late_improvements += int(row["late_improvement_count"])

    if len(run_ids) != 1 or "" in run_ids:
        safety.append("RUN_ID_MISMATCH:records do not share one nonempty run_id")
    if len(commits) != 1:
        safety.append("COMMIT_MISMATCH:records do not share one commit")

    short_tl = float(thresholds["scaling_short_timelimit_seconds"])
    long_tl = float(thresholds["scaling_long_timelimit_seconds"])
    short = next(
        row for row in rows
        if row["instance_id"] == "prob_21" and float(row["timelimit"]) == short_tl
    )
    long = next(
        row for row in rows
        if row["instance_id"] == "prob_21" and float(row["timelimit"]) == long_tl
    )
    for field in ("s3_fill_segments", "s3_fill_batches", "s3_fill_iterations"):
        if int(long[field]) <= int(short[field]):
            _failure(scaling, "LONG_RUN_DID_NOT_SCALE", str(long["record_id"]), field)
    short_objective = _number(short["objective"])
    long_objective = _number(long["objective"])
    if (
        short_objective is None
        or long_objective is None
        or long_objective > short_objective + 1e-6
    ):
        _failure(quality, "LONG_RUN_OBJECTIVE_REGRESSION", str(long["record_id"]))
    median_delta = statistics.median(relative_deltas) if relative_deltas else None
    if median_delta is None or median_delta > 0.0:
        quality.append("POSITIVE_MEDIAN_DELTA:paired median relative delta exceeds zero")
    if late_improvements < int(thresholds["late_improvements_minimum"]):
        quality.append("NO_LATE_IMPROVEMENT:hard-5 late improvement count is below one")

    gates = {
        "safety": _evaluated(safety),
        "time": _evaluated(time_failures),
        "scaling": _evaluated(scaling),
        "quality": _evaluated(quality),
    }
    precedence = (
        ("safety", "GATE_FAILED_SAFETY"),
        ("time", "GATE_FAILED_TIME"),
        ("scaling", "GATE_FAILED_SCALING"),
        ("quality", "GATE_FAILED_QUALITY"),
    )
    failed_gate = next(
        ((name, decision) for name, decision in precedence if not gates[name]["passed"]),
        None,
    )
    decision = "CANDIDATE_PASS" if failed_gate is None else failed_gate[1]
    reasons = [] if failed_gate is None else list(gates[failed_gate[0]]["failures"])
    return {
        "status": "passed" if failed_gate is None else "failed",
        "decision": decision,
        "decision_reasons": reasons,
        "candidate_profile": CANDIDATE_PROFILE.name,
        "records_evaluated": len(rows),
        "expected_record_ids": list(expected),
        "gates": gates,
        "metrics": {
            "late_improvement_count": late_improvements,
            "paired_median_relative_delta": median_delta,
            "scaling_short": {
                field: short[field]
                for field in ("s3_fill_segments", "s3_fill_batches", "s3_fill_iterations", "objective")
            },
            "scaling_long": {
                field: long[field]
                for field in ("s3_fill_segments", "s3_fill_batches", "s3_fill_iterations", "objective")
            },
        },
    }


def _evaluated(failures: Sequence[str]) -> dict[str, Any]:
    return {
        "status": "passed" if not failures else "failed",
        "passed": not failures,
        "failures": list(failures),
    }


def _not_evaluated(failures: Sequence[str]) -> dict[str, Any]:
    return {
        "status": "not_evaluated",
        "passed": False,
        "failures": list(failures),
    }


def _failure(target: list[str], code: str, record_id: str, detail: str = "") -> None:
    suffix = f":{detail}" if detail else ""
    target.append(f"{code}:{record_id}{suffix}")


def _deadline_reserve(timelimit: float) -> float:
    return max(3.0, min(60.0, 0.05 * timelimit))


def _number(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if math.isfinite(parsed) else None


def _finite_float(value: Any) -> float | None:
    return _number(value)


def _nonnegative_int(value: Any) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        return None
    return value


def _hex_digest(value: Any, length: int) -> bool:
    text = str(value)
    return len(text) == length and all(character in _HEX for character in text)


def _trace_is_nonincreasing(raw: Any) -> bool:
    if not isinstance(raw, (list, tuple)) or not raw:
        return False
    values = [_number(item.get("objective") if isinstance(item, dict) else item) for item in raw]
    if any(value is None for value in values):
        return False
    parsed = [float(value) for value in values if value is not None]
    return all(
        right <= left + max(1e-6, 1e-9 * abs(left))
        for left, right in zip(parsed, parsed[1:])
    )


def _merge(intervals: Iterable[tuple[float, float]]) -> list[tuple[float, float]]:
    merged: list[tuple[float, float]] = []
    for start, end in sorted(intervals):
        if not merged or start > merged[-1][1]:
            merged.append((start, end))
        else:
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))
    return merged


def _subtract(
    intervals: Iterable[tuple[float, float]],
    blockers: Iterable[tuple[float, float]],
) -> list[tuple[float, float]]:
    result: list[tuple[float, float]] = []
    blocked = _merge(blockers)
    for start, end in _merge(intervals):
        cursor = start
        for block_start, block_end in blocked:
            if block_end <= cursor:
                continue
            if block_start >= end:
                break
            if block_start > cursor:
                result.append((cursor, min(block_start, end)))
            cursor = max(cursor, block_end)
            if cursor >= end:
                break
        if cursor < end:
            result.append((cursor, end))
    return result


def _intersection(
    left: Iterable[tuple[float, float]],
    right: Iterable[tuple[float, float]],
) -> list[tuple[float, float]]:
    result: list[tuple[float, float]] = []
    left_rows = _merge(left)
    right_rows = _merge(right)
    i = j = 0
    while i < len(left_rows) and j < len(right_rows):
        start = max(left_rows[i][0], right_rows[j][0])
        end = min(left_rows[i][1], right_rows[j][1])
        if start < end:
            result.append((start, end))
        if left_rows[i][1] <= right_rows[j][1]:
            i += 1
        else:
            j += 1
    return result


def _duration(intervals: Iterable[tuple[float, float]]) -> float:
    return sum(end - start for start, end in _merge(intervals))
