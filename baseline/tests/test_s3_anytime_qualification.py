"""Synthetic AF-04 deadline-fill profile, telemetry, and gate contracts."""

from __future__ import annotations

from copy import deepcopy
import hashlib
import json
from pathlib import Path
import tempfile
import unittest

from harness.cli import build_parser
from harness.s3_anytime_qualification import (
    CANDIDATE_MANIFEST_PATH,
    CANDIDATE_PROFILE,
    DEFAULT_PROFILE,
    FROZEN_STATUS,
    PUBLIC_PROFILE,
    S3_ANYTIME_TELEMETRY_FIELDS,
    canonical_sha256,
    evaluate_s3_anytime_qualification,
    load_candidate_manifest,
    load_frozen_workload,
    profile_by_name,
    summarize_timing_intervals,
)
from harness.s3_anytime_worker import PROFILE as WORKER_PROFILE, _parser as worker_parser
from harness.runner import _s3_anytime_logical_trace, _s3_anytime_timing_intervals
from solver.config import DEFAULT_CONFIG


EXPECTED_TELEMETRY_FIELDS = (
    "run_id", "commit", "dirty_diff_hash", "instance_id", "instance_sha",
    "seed", "timelimit", "wall_seconds", "work_deadline_seconds",
    "hard_deadline_seconds", "anchor_seconds", "anchor_objective",
    "anchor_solution_sha256", "s3_fill_seconds", "s3_fill_useful_seconds",
    "total_useful_seconds",
    "idle_seconds", "finalization_seconds", "s3_fill_segments",
    "s3_fill_batches", "s3_fill_iterations", "s3_fill_accepted",
    "s3_fill_improvements", "s3_fill_restarts", "s3_fill_deadlines",
    "s3_fill_faults", "s3_fill_stopped_reason", "time_to_best_seconds",
    "late_improvement_count", "checker_stage", "feasible", "objective",
    "obj1", "obj2", "obj3", "final_solution_sha256",
    "verification_count", "termination_reason", "fallback_reason",
)


class S3AnytimeQualificationTests(unittest.TestCase):
    @staticmethod
    def _qualified_manifest_payload() -> dict:
        repo_root = Path(__file__).resolve().parents[2]
        return json.loads(
            (repo_root / CANDIDATE_MANIFEST_PATH).read_text(encoding="utf-8")
        )

    @staticmethod
    def _load_manifest_payload(
        payload: dict,
        *,
        require_frozen: bool = False,
    ) -> dict:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "candidate.json"
            path.write_text(json.dumps(payload), encoding="utf-8")
            return load_candidate_manifest(path, require_frozen=require_frozen)

    def _draft_manifest_payload(self) -> dict:
        payload = self._qualified_manifest_payload()
        payload["phase_id"] = "AF-04"
        payload["status"] = "UNQUALIFIED_DRAFT"
        payload["candidate"]["source_identity"] = None
        payload["qualification"]["command"] = None
        payload["qualification"]["real_wall_clock_executed"] = False
        return payload

    def _frozen_manifest_payload(self) -> dict:
        payload = self._qualified_manifest_payload()
        payload["phase_id"] = "AF-05"
        payload["status"] = FROZEN_STATUS
        payload["qualification"]["real_wall_clock_executed"] = False
        return payload

    @staticmethod
    def _trace_sha256(trace: list[dict]) -> str:
        encoded = json.dumps(trace, sort_keys=True, separators=(",", ":")).encode()
        return hashlib.sha256(encoded).hexdigest()

    @staticmethod
    def _logical_event(index: int) -> dict:
        return {
            "sequence_index": index,
            "phase": "anchor" if index < 2 else "tail",
            "segment_index": -1 if index < 2 else (index - 2) // 2,
            "batch_index": 0 if index < 2 else (index - 2) // 2,
            "iteration": index if index < 2 else index - 2,
            "seed": 20260710 if index < 2 else 20260710 + 104729 * ((index - 2) // 2),
            "destroy_scale": 1.0,
            "restart_policy": "anchor" if index < 2 else "continue",
            "destroy_name": "worst_tardiness",
            "repair_name": "greedy_reinsert",
            "previous_cur_obj": 1000.0 - index,
            "new_obj": 999.0 - index,
            "outcome": "accepted_improvement",
            "accepted": True,
            "potential_incumbent": True,
        }

    def _logical_trace(self, timelimit: float) -> list[dict]:
        tail_events = 3 if timelimit == 60.0 else 6 if timelimit == 300.0 else 4
        return [self._logical_event(index) for index in range(2 + tail_events)]

    def _record(
        self,
        instance_id: str,
        timelimit: float,
        *,
        anchor_objective: float = 1000.0,
        objective: float = 1000.0,
        late_improvement_count: int = 0,
    ) -> dict:
        work_deadline = timelimit - max(3.0, min(60.0, 0.05 * timelimit))
        if timelimit == 60.0:
            segments, batches, iterations = 1, 2, 3
            anchor_seconds, fill_seconds, finalization = 6.0, 53.0, 1.0
        elif timelimit == 300.0:
            segments, batches, iterations = 3, 4, 6
            anchor_seconds, fill_seconds, finalization = 34.0, 265.0, 1.0
        else:
            segments, batches, iterations = 2, 3, 4
            anchor_seconds, fill_seconds, finalization = 11.0, 108.0, 1.0
        record_id = f"{instance_id}|tl={timelimit:g}"
        instance_sha = hashlib.sha256(instance_id.encode()).hexdigest()
        anchor_sha = hashlib.sha256((record_id + "|anchor").encode()).hexdigest()
        final_sha = hashlib.sha256((record_id + "|final").encode()).hexdigest()
        total_useful = 0.91 * work_deadline
        tail_useful = total_useful - anchor_seconds
        trace = self._logical_trace(timelimit)
        timing_intervals = [
            {
                "phase": "anchor",
                "category": "candidate",
                "start": 0.0,
                "end": anchor_seconds,
            },
            {
                "phase": "tail",
                "category": "repair",
                "start": anchor_seconds,
                "end": anchor_seconds + tail_useful,
            },
            {
                "phase": "tail",
                "category": "idle",
                "start": anchor_seconds + tail_useful,
                "end": anchor_seconds + fill_seconds,
            },
            {
                "phase": "finalization",
                "category": "serialization",
                "start": anchor_seconds + fill_seconds,
                "end": timelimit,
            },
        ]
        return {
            "record_id": record_id,
            "complete": True,
            "status": "passed",
            "profile": CANDIDATE_PROFILE.name,
            "features": {"s3_anytime_fill": "true"},
            "incumbent_objective_trace": [anchor_objective, objective],
            "checker_solution_sha256": final_sha,
            "timing_intervals": timing_intervals,
            "logical_event_trace": trace,
            "logical_event_trace_sha256": self._trace_sha256(trace),
            "run_id": "synthetic-af04",
            "commit": "a" * 40,
            "dirty_diff_hash": "clean",
            "instance_id": instance_id,
            "instance_sha": instance_sha,
            "seed": 20260710,
            "timelimit": timelimit,
            "wall_seconds": timelimit,
            "work_deadline_seconds": work_deadline,
            "hard_deadline_seconds": timelimit,
            "anchor_seconds": anchor_seconds,
            "anchor_objective": anchor_objective,
            "anchor_solution_sha256": anchor_sha,
            "s3_fill_seconds": fill_seconds,
            "s3_fill_useful_seconds": tail_useful,
            "total_useful_seconds": total_useful,
            "idle_seconds": fill_seconds - tail_useful,
            "finalization_seconds": finalization,
            "s3_fill_segments": segments,
            "s3_fill_batches": batches,
            "s3_fill_iterations": iterations,
            "s3_fill_accepted": 100,
            "s3_fill_improvements": int(objective < anchor_objective),
            "s3_fill_restarts": max(0, segments - 1),
            "s3_fill_deadlines": 1,
            "s3_fill_faults": 0,
            "s3_fill_stopped_reason": "work_deadline",
            "time_to_best_seconds": work_deadline - 2.0,
            "late_improvement_count": late_improvement_count,
            "checker_stage": 5,
            "feasible": True,
            "objective": objective,
            "obj1": objective,
            "obj2": 0.0,
            "obj3": 0.0,
            "final_solution_sha256": final_sha,
            "verification_count": 3,
            "termination_reason": "work_deadline",
            "fallback_reason": None,
        }

    def _passing_records(self) -> list[dict]:
        records = [
            self._record(
                f"prob_{index}",
                120.0,
                objective=990.0 if index == 21 else 1000.0,
                late_improvement_count=1 if index == 21 else 0,
            )
            for index in range(21, 26)
        ]
        records.extend(
            (
                self._record("prob_21", 60.0),
                self._record("prob_21", 300.0, objective=990.0),
            )
        )
        return records

    def _assert_gate_failed(self, report: dict, gate: str) -> None:
        self.assertEqual("failed", report["status"])
        self.assertFalse(report["gates"][gate]["passed"])
        self.assertTrue(report["gates"][gate]["failures"])

    @staticmethod
    def _make_legacy_tail_metric_pass(records: list[dict]) -> None:
        for row in records:
            useful = 0.91 * row["work_deadline_seconds"]
            row["s3_fill_useful_seconds"] = useful
            row["idle_seconds"] = row["s3_fill_seconds"] - useful

    def test_required_telemetry_schema_is_exact_and_manifest_path_is_fixed(self):
        self.assertEqual(EXPECTED_TELEMETRY_FIELDS, S3_ANYTIME_TELEMETRY_FIELDS)
        self.assertEqual(
            "benchmarks/manifests/s3-anytime-fill-candidate.json",
            CANDIDATE_MANIFEST_PATH.as_posix(),
        )

    def test_draft_frozen_and_qualified_manifest_states_are_distinct(self):
        draft = self._load_manifest_payload(self._draft_manifest_payload())
        self.assertEqual("UNQUALIFIED_DRAFT", draft["status"])
        self.assertEqual("AF-04", draft["phase_id"])
        self.assertIsNone(draft["candidate"]["source_identity"])
        self.assertIsNone(draft["qualification"]["command"])

        frozen = self._load_manifest_payload(
            self._frozen_manifest_payload(), require_frozen=True
        )
        self.assertEqual(FROZEN_STATUS, frozen["status"])
        self.assertEqual("AF-05", frozen["phase_id"])
        self.assertIsInstance(frozen["candidate"]["source_identity"], dict)
        self.assertIsInstance(frozen["qualification"]["command"], list)
        self.assertFalse(frozen["qualification"]["real_wall_clock_executed"])

        qualified = load_candidate_manifest()
        self.assertEqual("QUALIFIED_CANDIDATE_PASS", qualified["status"])
        self.assertEqual("AF-05", qualified["phase_id"])
        self.assertEqual("AF-06-identity-D", qualified["qualification"]["execution_owner"])
        self.assertTrue(qualified["qualification"]["real_wall_clock_executed"])
        self.assertEqual("CANDIDATE_PASS", qualified["qualification"]["decision"])

    def test_qualified_manifest_is_read_only_and_never_satisfies_frozen_pre_q(self):
        with self.assertRaisesRegex(ValueError, "AF-06 requires.*frozen"):
            load_candidate_manifest(require_frozen=True)
        with self.assertRaisesRegex(ValueError, "frozen manifest"):
            load_frozen_workload(load_candidate_manifest())

    def test_draft_and_frozen_strict_invariants_reject_malformed_state(self):
        draft_mutations = {
            "phase": lambda payload: payload.__setitem__("phase_id", "AF-05"),
            "source_identity": lambda payload: payload["candidate"].__setitem__(
                "source_identity", {}
            ),
            "command": lambda payload: payload["qualification"].__setitem__(
                "command", []
            ),
        }
        for label, mutate in draft_mutations.items():
            with self.subTest(state="draft", label=label):
                payload = self._draft_manifest_payload()
                mutate(payload)
                with self.assertRaises(ValueError):
                    self._load_manifest_payload(payload)

        frozen_mutations = {
            "phase": lambda payload: payload.__setitem__("phase_id", "AF-04"),
            "source_identity": lambda payload: payload["candidate"].__setitem__(
                "source_identity", None
            ),
            "command": lambda payload: payload["qualification"].__setitem__(
                "command", None
            ),
            "real_wall_clock": lambda payload: payload["qualification"].__setitem__(
                "real_wall_clock_executed", True
            ),
        }
        for label, mutate in frozen_mutations.items():
            with self.subTest(state="frozen", label=label):
                payload = self._frozen_manifest_payload()
                mutate(payload)
                with self.assertRaises(ValueError):
                    self._load_manifest_payload(payload)

    def test_unknown_status_and_malformed_qualified_state_are_rejected(self):
        unknown = self._qualified_manifest_payload()
        unknown["status"] = "QUALIFIED_MAYBE"
        with self.assertRaisesRegex(ValueError, "unknown.*status"):
            self._load_manifest_payload(unknown)

        mutations = {
            "freeze_phase": lambda payload: payload.__setitem__("phase_id", "AF-06"),
            "execution_owner": lambda payload: payload["qualification"].__setitem__(
                "execution_owner", "AF-05"
            ),
            "decision": lambda payload: payload["qualification"].__setitem__(
                "decision", "CANDIDATE_FAIL"
            ),
            "real_wall_clock": lambda payload: payload["qualification"].__setitem__(
                "real_wall_clock_executed", False
            ),
            "q_count": lambda payload: payload["qualification"].__setitem__(
                "q_execution_count", 2
            ),
            "result": lambda payload: payload["qualification"]["result"].__setitem__(
                "command_exit_code", 1
            ),
            "gate": lambda payload: payload["qualification"]["result"]["gates"].__setitem__(
                "quality", "FAIL"
            ),
            "consumed": lambda payload: payload["qualification"].__setitem__(
                "candidate_consumed", False
            ),
            "rerun": lambda payload: payload["qualification"].__setitem__(
                "same_identity_rerun_allowed", True
            ),
            "q_evidence": lambda payload: payload["qualification"].__setitem__(
                "q_evidence_immutable", False
            ),
        }
        for label, mutate in mutations.items():
            with self.subTest(label=label):
                payload = self._qualified_manifest_payload()
                mutate(payload)
                with self.assertRaisesRegex(ValueError, "qualified candidate"):
                    self._load_manifest_payload(payload)

    def test_manifest_common_contract_invariants_remain_enforced(self):
        mutations = {
            "contract_hash": lambda payload: payload.__setitem__(
                "qualification_contract_sha256", "0" * 64
            ),
            "source_path": lambda payload: payload.__setitem__(
                "source_path", "elsewhere.json"
            ),
            "profile": lambda payload: payload["candidate"].__setitem__(
                "profile", "public"
            ),
            "features": lambda payload: payload["candidate"].__setitem__(
                "features", {"s3_anytime_fill": "false"}
            ),
            "public_default": lambda payload: payload["candidate"].__setitem__(
                "public_default", True
            ),
            "record_ids": lambda payload: payload["qualification_contract"].__setitem__(
                "expected_record_ids", []
            ),
            "thresholds": lambda payload: payload["qualification_contract"].__setitem__(
                "thresholds", {}
            ),
            "telemetry": lambda payload: payload["qualification_contract"].__setitem__(
                "required_telemetry_fields", []
            ),
            "evidence": lambda payload: payload["qualification_contract"].__setitem__(
                "required_evidence_fields", []
            ),
        }
        contract_mutations = {"record_ids", "thresholds", "telemetry", "evidence"}
        for label, mutate in mutations.items():
            with self.subTest(label=label):
                payload = self._qualified_manifest_payload()
                mutate(payload)
                if label in contract_mutations:
                    payload["qualification_contract_sha256"] = canonical_sha256(
                        payload["qualification_contract"]
                    )
                with self.assertRaises(ValueError):
                    self._load_manifest_payload(payload)

    def test_qualified_candidate_identity_self_check_is_enforced(self):
        payload = self._qualified_manifest_payload()
        payload["candidate"]["candidate_identity_contract"]["candidate_label"] = "X"
        with self.assertRaisesRegex(ValueError, "candidate identity"):
            self._load_manifest_payload(payload)

    def test_candidate_cli_and_public_worker_profiles_are_explicit(self):
        parser = build_parser()
        synthetic = parser.parse_args(
            [
                "qualify-s3-anytime",
                "--profile",
                CANDIDATE_PROFILE.name,
                "--records",
                "synthetic.json",
            ]
        )
        frozen = parser.parse_args(
            ["qualify-s3-anytime", "--profile", CANDIDATE_PROFILE.name]
        )
        self.assertEqual("synthetic.json", synthetic.records)
        self.assertIsNone(frozen.records)
        worker = worker_parser().parse_args(
            [
                "--input",
                "fixture.json",
                "--timelimit",
                "12",
                "--seed",
                "20260710",
                "--algorithm-root",
                "baseline",
                "--profile",
                CANDIDATE_PROFILE.name,
            ]
        )
        self.assertEqual(WORKER_PROFILE, worker.profile)

    def test_runner_materializes_sequential_timing_and_full_event_evidence(self):
        intervals = _s3_anytime_timing_intervals(
            {
                "t0_and_verify_seconds": 1.0,
                "constructor_seconds": 2.0,
                "retime_seconds": 1.0,
                "alns_seconds": 2.0,
            },
            10.0,
        )
        self.assertEqual(
            ["anchor", "anchor", "anchor", "anchor", "tail"],
            [item["phase"] for item in intervals],
        )
        summary = summarize_timing_intervals(intervals)
        self.assertEqual(6.0, summary["anchor_seconds"])
        self.assertEqual(4.0, summary["s3_fill_useful_seconds"])
        self.assertEqual(10.0, summary["total_useful_seconds"])

        common = {
            "iteration": 0,
            "destroy_name": "worst_tardiness",
            "repair_name": "greedy_reinsert",
            "previous_cur_obj": 1000.0,
            "new_obj": 999.0,
            "outcome": "accepted_improvement",
            "accepted": True,
            "potential_incumbent": True,
        }
        trace = _s3_anytime_logical_trace(
            {
                "anchor_logical_event_trace": [common],
                "tail_logical_event_trace": [
                    {
                        **common,
                        "segment_index": 0,
                        "batch_index": 0,
                        "seed": 20260710,
                        "destroy_scale": 1.0,
                        "restart_policy": "continue",
                    }
                ],
            },
            seed=20260710,
        )
        self.assertEqual(["anchor", "tail"], [item["phase"] for item in trace])
        self.assertEqual([0, 1], [item["sequence_index"] for item in trace])

    def test_valid_synthetic_records_pass(self):
        report = evaluate_s3_anytime_qualification(self._passing_records())
        self.assertEqual("passed", report["status"])
        self.assertEqual("CANDIDATE_PASS", report["decision"])
        self.assertTrue(all(gate["passed"] for gate in report["gates"].values()))

    def test_anchor_actual_candidate_work_counts_toward_total_useful_gate(self):
        records = self._passing_records()
        for row in records:
            minimum = 0.9 * row["work_deadline_seconds"]
            self.assertLess(row["s3_fill_useful_seconds"], minimum)
            self.assertGreaterEqual(row["total_useful_seconds"], minimum)
        self.assertTrue(
            evaluate_s3_anytime_qualification(records)["gates"]["time"]["passed"]
        )

    def test_max_iterations_before_work_deadline_fails_time_gate(self):
        records = self._passing_records()
        records[0].update(
            wall_seconds=50.0,
            s3_fill_seconds=39.0,
            s3_fill_stopped_reason="max_iterations",
            termination_reason="max_iterations",
        )
        self._assert_gate_failed(
            evaluate_s3_anytime_qualification(records), "time"
        )

    def test_sleep_only_record_fails_useful_time_gate(self):
        records = self._passing_records()
        self._make_legacy_tail_metric_pass(records)
        row = records[0]
        row["timing_intervals"] = [
            {"phase": "anchor", "category": "sleep", "start": 0.0, "end": 40.0},
            {"phase": "tail", "category": "no_op", "start": 40.0, "end": 80.0},
            {"phase": "tail", "category": "idle", "start": 80.0, "end": 119.0},
            {
                "phase": "finalization",
                "category": "serialization",
                "start": 119.0,
                "end": 120.0,
            },
        ]
        self._assert_gate_failed(
            evaluate_s3_anytime_qualification(records), "time"
        )

    def test_overlapping_phase_intervals_are_rejected_not_double_counted(self):
        records = self._passing_records()
        self._make_legacy_tail_metric_pass(records)
        records[0]["timing_intervals"] = [
            {"phase": "anchor", "category": "candidate", "start": 0.0, "end": 70.0},
            {"phase": "tail", "category": "repair", "start": 50.0, "end": 119.0},
            {
                "phase": "finalization",
                "category": "serialization",
                "start": 119.0,
                "end": 120.0,
            },
        ]
        self._assert_gate_failed(
            evaluate_s3_anytime_qualification(records), "time"
        )

    def test_zero_segment_or_iteration_fails_scaling_gate(self):
        for field in ("s3_fill_segments", "s3_fill_iterations"):
            with self.subTest(field=field):
                records = self._passing_records()
                records[0][field] = 0
                self._assert_gate_failed(
                    evaluate_s3_anytime_qualification(records), "scaling"
                )

    def test_final_worse_than_anchor_fails_safety_and_quality(self):
        records = self._passing_records()
        records[0].update(objective=1001.0, obj1=1001.0)
        records[0]["incumbent_objective_trace"] = [1000.0, 1001.0]
        report = evaluate_s3_anytime_qualification(records)
        self._assert_gate_failed(report, "safety")
        self.assertFalse(report["gates"]["quality"]["passed"])

    def test_increasing_trace_fails_safety_gate(self):
        records = self._passing_records()
        records[0]["incumbent_objective_trace"] = [1000.0, 990.0, 995.0]
        self._assert_gate_failed(
            evaluate_s3_anytime_qualification(records), "safety"
        )

    def test_hard_deadline_overrun_or_checker_below_stage5_fails_safety(self):
        for field, value in (("wall_seconds", 120.001), ("checker_stage", 4)):
            with self.subTest(field=field):
                records = self._passing_records()
                records[0][field] = value
                if field == "checker_stage":
                    records[0]["feasible"] = False
                self._assert_gate_failed(
                    evaluate_s3_anytime_qualification(records), "safety"
                )

    def test_300_second_work_units_must_all_exceed_60_second_units(self):
        for field in (
            "s3_fill_segments", "s3_fill_batches", "s3_fill_iterations"
        ):
            with self.subTest(field=field):
                records = self._passing_records()
                short = next(row for row in records if row["timelimit"] == 60.0)
                long = next(row for row in records if row["timelimit"] == 300.0)
                long[field] = short[field]
                self._assert_gate_failed(
                    evaluate_s3_anytime_qualification(records), "scaling"
                )

    def test_missing_or_malformed_full_logical_trace_never_passes_scaling(self):
        for mutation in ("missing", "malformed", "hash_mismatch"):
            with self.subTest(mutation=mutation):
                records = self._passing_records()
                if mutation == "missing":
                    del records[-1]["logical_event_trace"]
                elif mutation == "malformed":
                    records[-1]["logical_event_trace"][0]["sequence_index"] = 9
                    records[-1]["logical_event_trace_sha256"] = self._trace_sha256(
                        records[-1]["logical_event_trace"]
                    )
                else:
                    records[-1]["logical_event_trace_sha256"] = "0" * 64
                report = evaluate_s3_anytime_qualification(records)
                self.assertFalse(report["gates"]["scaling"]["passed"])

    def test_60_second_full_logical_trace_must_match_300_second_prefix(self):
        records = self._passing_records()
        long = next(row for row in records if row["timelimit"] == 300.0)
        long["logical_event_trace"][1]["repair_name"] = "mismatched_repair"
        long["logical_event_trace_sha256"] = self._trace_sha256(
            long["logical_event_trace"]
        )
        self._assert_gate_failed(
            evaluate_s3_anytime_qualification(records), "scaling"
        )

    def test_exact_full_logical_trace_prefix_passes_scaling(self):
        records = self._passing_records()
        report = evaluate_s3_anytime_qualification(records)
        self.assertEqual("CANDIDATE_PASS", report["decision"])
        self.assertTrue(report["gates"]["scaling"]["passed"])

    def test_incomplete_records_or_fields_are_never_pass(self):
        variants = (self._passing_records()[:-1], self._passing_records())
        del variants[1][0]["anchor_solution_sha256"]
        for records in variants:
            with self.subTest(records=len(records)):
                report = evaluate_s3_anytime_qualification(records)
                self.assertEqual("incomplete", report["status"])
                self.assertEqual("INCOMPLETE", report["decision"])
                self.assertFalse(any(gate["passed"] for gate in report["gates"].values()))

    def test_selected_profiles_match_qualified_candidate_feature_set(self):
        qualified = {"s3_anytime_fill": "true"}
        selected = {
            "DEFAULT_CONFIG": {
                "s3_anytime_fill": str(DEFAULT_CONFIG.s3_anytime_fill).lower()
            },
            PUBLIC_PROFILE.name: {
                "s3_anytime_fill": str(DEFAULT_CONFIG.s3_anytime_fill).lower()
            },
            DEFAULT_PROFILE.name: {
                "s3_anytime_fill": str(DEFAULT_CONFIG.s3_anytime_fill).lower()
            },
            CANDIDATE_PROFILE.name: dict(CANDIDATE_PROFILE.features),
        }

        self.assertEqual(
            qualified,
            dict(profile_by_name(CANDIDATE_PROFILE.name).features),
        )
        self.assertEqual(
            {tuple(sorted(qualified.items()))},
            {tuple(sorted(features.items())) for features in selected.values()},
        )

    def test_timing_intervals_are_union_accounted_and_exclusions_not_useful(self):
        summary = summarize_timing_intervals(
            (
                {"phase": "anchor", "category": "candidate", "start": 0.0, "end": 2.0},
                {"phase": "anchor", "category": "checker", "start": 2.0, "end": 3.0},
                {"phase": "tail", "category": "repair", "start": 3.0, "end": 8.0},
                {"phase": "tail", "category": "sleep", "start": 8.0, "end": 10.0},
                {"phase": "tail", "category": "no_op", "start": 10.0, "end": 11.0},
                {"phase": "finalization", "category": "serialization", "start": 11.0, "end": 12.0},
            )
        )
        self.assertEqual(3.0, summary["anchor_seconds"])
        self.assertEqual(8.0, summary["s3_fill_seconds"])
        self.assertEqual(5.0, summary["s3_fill_useful_seconds"])
        self.assertEqual(8.0, summary["total_useful_seconds"])
        self.assertEqual(3.0, summary["idle_seconds"])
        self.assertEqual(1.0, summary["finalization_seconds"])
        self.assertEqual(12.0, summary["wall_seconds"])


if __name__ == "__main__":
    unittest.main()
