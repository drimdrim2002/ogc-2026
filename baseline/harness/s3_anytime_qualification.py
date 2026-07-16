"""S3 deadline-fill telemetry schema and deterministic synthetic gates."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import math
from pathlib import Path
import statistics
import subprocess
from types import MappingProxyType
from typing import Any, Mapping, Sequence

from .selectors import InstanceRef, REPO_ROOT


CANDIDATE_MANIFEST_PATH = Path(
    "benchmarks/manifests/s3-anytime-fill-candidate.json"
)
FROZEN_STATUS = "FROZEN_UNQUALIFIED"
QUALIFIED_STATUS = "QUALIFIED_CANDIDATE_PASS"
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
    "total_useful_seconds",
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
    "timing_intervals",
    "logical_event_trace",
    "logical_event_trace_sha256",
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
_ALLOWED_CATEGORIES = _USEFUL_CATEGORIES | _IDLE_CATEGORIES | _FINALIZATION_CATEGORIES
_PHASE_ORDER = {"anchor": 0, "tail": 1, "finalization": 2}
_LOGICAL_EVENT_FIELDS = (
    "sequence_index",
    "phase",
    "segment_index",
    "batch_index",
    "iteration",
    "seed",
    "destroy_scale",
    "restart_policy",
    "destroy_name",
    "repair_name",
    "previous_cur_obj",
    "new_obj",
    "outcome",
    "accepted",
    "potential_incumbent",
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


def canonical_sha256(payload: Any) -> str:
    encoded = json.dumps(
        payload, sort_keys=True, separators=(",", ":")
    ).encode()
    return hashlib.sha256(encoded).hexdigest()


def load_candidate_manifest(
    path: Path | None = None,
    *,
    require_frozen: bool = False,
) -> dict[str, Any]:
    """Load a draft, frozen pre-Q, or read-only qualified candidate manifest."""
    relative = CANDIDATE_MANIFEST_PATH if path is None else Path(path)
    absolute = relative if relative.is_absolute() else REPO_ROOT / relative
    payload = json.loads(absolute.read_text(encoding="utf-8"))
    contract = payload.get("qualification_contract")
    if not isinstance(contract, dict):
        raise ValueError("S3 anytime qualification contract is missing")
    if canonical_sha256(contract) != payload.get("qualification_contract_sha256"):
        raise ValueError("S3 anytime qualification contract hash mismatch")
    status = payload.get("status")
    if status not in {"UNQUALIFIED_DRAFT", FROZEN_STATUS, QUALIFIED_STATUS}:
        raise ValueError("unknown S3 anytime candidate manifest status")
    if require_frozen and status != FROZEN_STATUS:
        raise ValueError("AF-06 requires the AF-05 frozen candidate manifest")
    if payload.get("source_path") != CANDIDATE_MANIFEST_PATH.as_posix():
        raise ValueError("S3 anytime candidate manifest source path changed")
    candidate = payload.get("candidate", {})
    if candidate.get("profile") != CANDIDATE_PROFILE.name:
        raise ValueError("S3 anytime candidate profile changed")
    if candidate.get("features") != dict(CANDIDATE_PROFILE.features):
        raise ValueError("S3 anytime candidate feature set changed")
    if candidate.get("public_default") is not False:
        raise ValueError("S3 anytime public default must remain false")
    if tuple(contract.get("expected_record_ids", ())) != EXPECTED_RECORD_IDS:
        raise ValueError("S3 anytime expected record plan changed")
    if contract.get("thresholds") != dict(QUALIFICATION_THRESHOLDS):
        raise ValueError("S3 anytime frozen thresholds changed")
    if tuple(contract.get("required_telemetry_fields", ())) != S3_ANYTIME_TELEMETRY_FIELDS:
        raise ValueError("S3 anytime telemetry schema changed")
    if tuple(contract.get("required_evidence_fields", ())) != S3_ANYTIME_EVIDENCE_FIELDS:
        raise ValueError("S3 anytime evidence schema changed")
    qualification = payload.get("qualification", {})
    if status == "UNQUALIFIED_DRAFT":
        if payload.get("phase_id") != "AF-04":
            raise ValueError("draft candidate manifest must remain owned by AF-04")
        if candidate.get("source_identity") is not None:
            raise ValueError("AF-04 draft cannot claim a source identity")
        if qualification.get("command") is not None:
            raise ValueError("AF-04 draft cannot claim a qualification command")
    elif status == FROZEN_STATUS:
        if payload.get("phase_id") != "AF-05":
            raise ValueError("frozen candidate manifest must be owned by AF-05")
        if not isinstance(candidate.get("source_identity"), dict):
            raise ValueError("AF-05 source identity is missing")
        if not isinstance(qualification.get("command"), list):
            raise ValueError("AF-05 qualification command is missing")
        if qualification.get("real_wall_clock_executed") is not False:
            raise ValueError("AF-05 must not claim real qualification execution")
    else:
        _validate_qualified_candidate(payload, candidate, qualification, contract)
    return payload


def _validate_qualified_candidate(
    payload: Mapping[str, Any],
    candidate: Mapping[str, Any],
    qualification: Mapping[str, Any],
    contract: Mapping[str, Any],
) -> None:
    """Validate the immutable post-Q terminal state without making it runnable."""

    def require(condition: bool, detail: str) -> None:
        if not condition:
            raise ValueError(f"qualified candidate {detail}")

    require(
        payload.get("phase_id") == "AF-05",
        "must retain its AF-05 frozen-manifest ownership",
    )
    require(
        isinstance(candidate.get("source_identity"), dict),
        "source identity is missing",
    )
    require(
        isinstance(qualification.get("command"), list)
        and bool(qualification.get("command")),
        "qualification command is missing",
    )

    identity_contract = candidate.get("candidate_identity_contract")
    require(isinstance(identity_contract, dict), "candidate identity contract is missing")
    identity_digest = canonical_sha256(identity_contract)
    require(
        candidate.get("candidate_identity_contract_sha256") == identity_digest
        and candidate.get("candidate_identity") == identity_digest
        and qualification.get("candidate_identity") == identity_digest,
        "candidate identity self-check failed",
    )
    identity_label = candidate.get("identity_label")
    require(
        isinstance(identity_label, str)
        and identity_label
        and identity_contract.get("candidate_label") == identity_label
        and qualification.get("candidate_identity_label") == identity_label,
        "identity label metadata is inconsistent",
    )
    require(
        identity_contract.get("profile") == candidate.get("profile")
        and identity_contract.get("qualification_contract_sha256")
        == payload.get("qualification_contract_sha256")
        and candidate.get("source_identity", {}).get("qualification_contract_sha256")
        == payload.get("qualification_contract_sha256"),
        "identity contract metadata is inconsistent",
    )
    require(
        qualification.get("freeze_owner") == candidate.get("candidate_identity_owner")
        == candidate.get("source_identity_owner"),
        "freeze ownership metadata is inconsistent",
    )
    require(
        qualification.get("execution_owner") == f"AF-06-identity-{identity_label}",
        "AF-06 execution ownership metadata is inconsistent",
    )
    require(
        qualification.get("q_run_id") == identity_contract.get("q_run_id")
        and qualification.get("expected_evidence_directory")
        == identity_contract.get("expected_evidence_directory"),
        "Q run metadata is inconsistent",
    )

    terminal_values = {
        "command_status": "EXECUTED_ONCE_COMPLETE",
        "real_q_executed": True,
        "real_wall_clock_executed": True,
        "actual_evidence_directory_present": True,
        "expected_evidence_directory_confirmed_absent": False,
        "automatic_resume": False,
        "automatic_rerun": False,
        "candidate_consumed": True,
        "same_identity_rerun_allowed": False,
        "q_evidence_immutable": True,
        "decision": "CANDIDATE_PASS",
    }
    require(
        all(qualification.get(key) == value for key, value in terminal_values.items()),
        "terminal Q metadata is inconsistent",
    )
    require(
        type(qualification.get("q_execution_count")) is int
        and qualification.get("q_execution_count") == 1,
        "Q execution count must be exactly one",
    )

    result = qualification.get("result")
    require(isinstance(result, dict), "result metadata is missing")
    expected_gates = {
        "quality": "PASS",
        "safety": "PASS",
        "scaling": "PASS",
        "time": "PASS",
    }
    expected_count = len(tuple(contract.get("expected_record_ids", ())))
    require(
        type(result.get("command_exit_code")) is int
        and result.get("command_exit_code") == 0
        and result.get("gates") == expected_gates
        and result.get("records_evaluated") == expected_count
        and result.get("records_passed") == expected_count
        and result.get("official_checker_stage_5") == f"{expected_count}/{expected_count}"
        and result.get("run_complete") is True
        and result.get("run_interrupted") is False,
        "CANDIDATE_PASS result metadata is inconsistent",
    )
    q_hashes = result.get("q_evidence_sha256")
    required_q_files = {
        "command.txt",
        "qualification.json",
        "records.jsonl",
        "run.json",
        "summary.json",
        "versions.json",
    }
    require(
        isinstance(q_hashes, dict)
        and set(q_hashes) == required_q_files
        and all(_hex_digest(value, 64) for value in q_hashes.values()),
        "immutable Q evidence hashes are incomplete",
    )

    workload = qualification.get("workload")
    require(isinstance(workload, list), "workload metadata is missing")
    require(
        tuple(str(row.get("record_id")) for row in workload)
        == tuple(contract.get("expected_record_ids", ()))
        and all(row.get("seed") == identity_contract.get("seed") for row in workload)
        and all(row.get("jobs") == identity_contract.get("jobs") for row in workload),
        "workload metadata differs from the qualified identity",
    )


def load_frozen_workload(
    manifest: Mapping[str, Any] | None = None,
) -> tuple[tuple[InstanceRef, float, int], ...]:
    """Load the ordered AF-06 workload and verify every input byte identity."""
    frozen = (
        load_candidate_manifest(require_frozen=True)
        if manifest is None
        else dict(manifest)
    )
    if frozen.get("status") != FROZEN_STATUS:
        raise ValueError("AF-06 workload requires a frozen manifest")
    rows = frozen.get("qualification", {}).get("workload", ())
    expected = tuple(frozen["qualification_contract"]["expected_record_ids"])
    if tuple(str(row.get("record_id")) for row in rows) != expected:
        raise ValueError("AF-06 workload order differs from the frozen contract")
    loaded: list[tuple[InstanceRef, float, int]] = []
    for row in rows:
        path = Path(str(row.get("input_path", "")))
        if not path.is_absolute() or not path.is_file():
            raise ValueError(f"missing frozen qualification input: {path}")
        encoded = path.read_bytes()
        digest = hashlib.sha256(encoded).hexdigest()
        if digest != row.get("instance_sha"):
            raise ValueError(f"frozen qualification input hash mismatch: {path}")
        instance_id = str(row["instance_id"])
        timelimit = float(row["timelimit"])
        seed = int(row["seed"])
        if str(row["record_id"]) != f"{instance_id}|tl={timelimit:g}":
            raise ValueError("AF-06 record id does not match its workload row")
        loaded.append(
            (
                InstanceRef(
                    instance_id=instance_id,
                    path=path,
                    sha256=digest,
                    prob_info=json.loads(encoded),
                ),
                timelimit,
                seed,
            )
        )
    return tuple(loaded)


def verify_frozen_candidate_source(
    manifest: Mapping[str, Any] | None = None,
) -> dict[str, str]:
    """Require clean AF-05 HEAD and unchanged AF-04 algorithm bytes."""
    frozen = (
        load_candidate_manifest(require_frozen=True)
        if manifest is None
        else dict(manifest)
    )
    identity = frozen.get("candidate", {}).get("source_identity", {})
    expected_parent = str(identity.get("freeze_parent_commit", ""))
    expected_message = str(identity.get("freeze_commit_message", ""))
    algorithm_commit = str(identity.get("algorithm_commit", ""))
    algorithm_scope = tuple(str(item) for item in identity.get("algorithm_scope", ()))
    status = _git("status", "--porcelain=v1")
    head = _git("rev-parse", "HEAD")
    parent = _git("rev-parse", "HEAD^")
    message = _git("log", "-1", "--format=%s")
    if status:
        raise ValueError("AF-06 requires a clean frozen worktree")
    if parent != expected_parent or message != expected_message:
        raise ValueError("AF-05 freeze commit identity mismatch")
    if not algorithm_scope:
        raise ValueError("frozen algorithm scope is empty")
    unchanged = subprocess.run(
        ["git", "diff", "--quiet", algorithm_commit, "HEAD", "--", *algorithm_scope],
        cwd=REPO_ROOT,
        check=False,
    )
    if unchanged.returncode != 0:
        raise ValueError("frozen algorithm scope differs from AF-04")
    return {"head": head, "parent": parent, "message": message}


def _git(*args: str) -> str:
    result = subprocess.run(
        ["git", *args],
        cwd=REPO_ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip()


def summarize_timing_intervals(
    intervals: Sequence[Mapping[str, Any]],
) -> dict[str, float]:
    """Validate sequential wall phases and derive useful time from their union."""
    rows: list[tuple[float, float, str, str]] = []
    for index, item in enumerate(intervals):
        if not isinstance(item, Mapping):
            raise ValueError(f"timing interval at index {index} is not an object")
        phase = str(item.get("phase", ""))
        category = str(item.get("category", ""))
        if phase not in _PHASE_ORDER:
            raise ValueError(f"unknown timing phase at index {index}: {phase}")
        if category not in _ALLOWED_CATEGORIES:
            raise ValueError(f"unknown timing category at index {index}: {category}")
        if phase == "finalization" and category not in _FINALIZATION_CATEGORIES:
            raise ValueError("finalization phase contains useful or idle work")
        if phase != "finalization" and category in _FINALIZATION_CATEGORIES:
            raise ValueError("serialization/finalization is outside finalization phase")
        start = _finite_float(item.get("start"))
        end = _finite_float(item.get("end"))
        if start is None or end is None or start < 0.0 or end <= start:
            raise ValueError(f"invalid timing interval at index {index}")
        rows.append((start, end, phase, category))
    if not rows:
        raise ValueError("timing intervals are empty")
    rows.sort(key=lambda item: (item[0], item[1]))
    if not math.isclose(rows[0][0], 0.0, abs_tol=1e-9):
        raise ValueError("timing intervals must start at wall zero")
    prior_end = 0.0
    prior_phase = 0
    for start, end, phase, _category in rows:
        if start < prior_end - 1e-9:
            raise ValueError("timing intervals overlap")
        if not math.isclose(start, prior_end, abs_tol=1e-9):
            raise ValueError("timing intervals have an unclassified gap")
        phase_order = _PHASE_ORDER[phase]
        if phase_order < prior_phase:
            raise ValueError("timing phases are not sequential")
        prior_phase = phase_order
        prior_end = end

    def duration(*, phases: frozenset[str], categories: frozenset[str]) -> float:
        return sum(
            end - start
            for start, end, phase, category in rows
            if phase in phases and category in categories
        )

    work_phases = frozenset({"anchor", "tail"})
    work_categories = _USEFUL_CATEGORIES | _IDLE_CATEGORIES
    return {
        "anchor_seconds": duration(
            phases=frozenset({"anchor"}), categories=work_categories
        ),
        "s3_fill_seconds": duration(
            phases=frozenset({"tail"}), categories=work_categories
        ),
        "s3_fill_useful_seconds": duration(
            phases=frozenset({"tail"}), categories=_USEFUL_CATEGORIES
        ),
        "total_useful_seconds": duration(
            phases=work_phases, categories=_USEFUL_CATEGORIES
        ),
        "idle_seconds": duration(phases=work_phases, categories=_IDLE_CATEGORIES),
        "finalization_seconds": duration(
            phases=frozenset({"finalization"}),
            categories=_FINALIZATION_CATEGORIES,
        ),
        "wall_seconds": prior_end,
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
    validated_traces: dict[str, tuple[dict[str, Any], ...]] = {}

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

        try:
            validated_traces[record_id] = _validated_logical_trace(row)
        except ValueError as exc:
            _failure(scaling, "INVALID_LOGICAL_EVENT_TRACE", record_id, str(exc))

        timelimit = _number(row["timelimit"])
        wall = _number(row["wall_seconds"])
        work_deadline = _number(row["work_deadline_seconds"])
        hard_deadline = _number(row["hard_deadline_seconds"])
        anchor_seconds = _number(row["anchor_seconds"])
        fill_seconds = _number(row["s3_fill_seconds"])
        useful_seconds = _number(row["s3_fill_useful_seconds"])
        total_useful_seconds = _number(row["total_useful_seconds"])
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
            total_useful_seconds,
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
            assert total_useful_seconds is not None
            assert idle_seconds is not None
            assert finalization_seconds is not None
            expected_work = timelimit - _deadline_reserve(timelimit)
            if not math.isclose(work_deadline, expected_work, abs_tol=1e-9):
                _failure(time_failures, "WORK_DEADLINE_MISMATCH", record_id)
            if not math.isclose(hard_deadline, timelimit, abs_tol=1e-9):
                _failure(safety, "HARD_DEADLINE_MISMATCH", record_id)
            if wall > hard_deadline + 1e-9:
                _failure(safety, "HARD_DEADLINE_OVERRUN", record_id)
            try:
                timing = summarize_timing_intervals(row["timing_intervals"])
            except (TypeError, ValueError) as exc:
                _failure(time_failures, "INVALID_TIMING_INTERVALS", record_id, str(exc))
            else:
                reported = {
                    "wall_seconds": wall,
                    "anchor_seconds": anchor_seconds,
                    "s3_fill_seconds": fill_seconds,
                    "s3_fill_useful_seconds": useful_seconds,
                    "total_useful_seconds": total_useful_seconds,
                    "idle_seconds": idle_seconds,
                    "finalization_seconds": finalization_seconds,
                }
                for field, value in reported.items():
                    if not math.isclose(
                        timing[field], value, rel_tol=0.0, abs_tol=1e-6
                    ):
                        _failure(
                            time_failures,
                            "TIME_ACCOUNTING_MISMATCH",
                            record_id,
                            field,
                        )
            minimum_useful = (
                float(thresholds["minimum_useful_fraction_of_work_deadline"])
                * work_deadline
            )
            if total_useful_seconds + 1e-9 < minimum_useful:
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
    short_trace = validated_traces.get(str(short["record_id"]))
    long_trace = validated_traces.get(str(long["record_id"]))
    if short_trace is not None and long_trace is not None and (
        len(short_trace) > len(long_trace)
        or short_trace != long_trace[: len(short_trace)]
    ):
        _failure(
            scaling,
            "FULL_LOGICAL_TRACE_PREFIX_MISMATCH",
            str(long["record_id"]),
        )
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
            "scaling_trace": {
                "short_event_count": 0 if short_trace is None else len(short_trace),
                "long_event_count": 0 if long_trace is None else len(long_trace),
                "exact_prefix": (
                    short_trace is not None
                    and long_trace is not None
                    and len(short_trace) <= len(long_trace)
                    and short_trace == long_trace[: len(short_trace)]
                ),
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


def _validated_logical_trace(
    row: Mapping[str, Any],
) -> tuple[dict[str, Any], ...]:
    raw = row.get("logical_event_trace")
    if not isinstance(raw, (list, tuple)) or not raw:
        raise ValueError("trace is missing or empty")
    if canonical_sha256(raw) != row.get("logical_event_trace_sha256"):
        raise ValueError("trace SHA-256 mismatch")
    parsed: list[dict[str, Any]] = []
    prior_phase = 0
    phases: set[str] = set()
    for index, item in enumerate(raw):
        if not isinstance(item, Mapping):
            raise ValueError(f"event {index} is not an object")
        if len(item) != len(_LOGICAL_EVENT_FIELDS) or set(item) != set(
            _LOGICAL_EVENT_FIELDS
        ):
            raise ValueError(f"event {index} schema mismatch")
        if _nonnegative_int(item["sequence_index"]) != index:
            raise ValueError(f"event {index} sequence is not contiguous")
        phase = str(item["phase"])
        if phase not in {"anchor", "tail"}:
            raise ValueError(f"event {index} phase is invalid")
        phase_order = 0 if phase == "anchor" else 1
        if phase_order < prior_phase:
            raise ValueError(f"event {index} phase order regressed")
        prior_phase = phase_order
        for field in ("batch_index", "iteration"):
            if _nonnegative_int(item[field]) is None:
                raise ValueError(f"event {index} {field} is invalid")
        segment_index = item["segment_index"]
        if (
            isinstance(segment_index, bool)
            or not isinstance(segment_index, int)
            or segment_index < -1
        ):
            raise ValueError(f"event {index} segment_index is invalid")
        seed = item["seed"]
        if isinstance(seed, bool) or not isinstance(seed, int):
            raise ValueError(f"event {index} seed is invalid")
        for field in ("destroy_scale", "previous_cur_obj", "new_obj"):
            if _number(item[field]) is None:
                raise ValueError(f"event {index} {field} is invalid")
        for field in (
            "restart_policy",
            "destroy_name",
            "repair_name",
            "outcome",
        ):
            if not isinstance(item[field], str) or not item[field]:
                raise ValueError(f"event {index} {field} is invalid")
        for field in ("accepted", "potential_incumbent"):
            if not isinstance(item[field], bool):
                raise ValueError(f"event {index} {field} is invalid")
        phases.add(phase)
        parsed.append(dict(item))
    if phases != {"anchor", "tail"}:
        raise ValueError("trace must contain anchor and tail logical events")
    return tuple(parsed)
