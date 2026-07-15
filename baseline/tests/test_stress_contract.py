from __future__ import annotations

import copy
import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from experiments.ogc_sage.benchmark_ogc_sage import (  # noqa: E402
    ContractError,
    EVIDENCE_MANIFEST,
    FINAL_BUDGETS,
    FINAL_EXPECTED_RUN_COUNT,
    FINAL_INSTANCE_COUNT,
    FINAL_SEEDS,
    FINAL_VARIANT,
    SCHEMA_VERSION,
    _invocation_measurement,
    config_hash,
    load_raw,
    make_run_key,
    validate_dataset,
    validate_final_matrix_contract,
    validate_raw_record,
    validate_summary,
)


class StressContractTests(unittest.TestCase):
    def test_dataset_manifest_pass_and_missing_duplicate_wrong_hash_fail(self):
        found, digest = validate_dataset()
        self.assertEqual(40, len(found))
        self.assertEqual(64, len(digest))
        with self.assertRaisesRegex(ContractError, "membership mismatch"):
            validate_dataset((ROOT / "data/train 2",))
        with self.assertRaisesRegex(ContractError, "duplicate dataset"):
            validate_dataset((ROOT / "data/train 2", ROOT / "data/train 2", ROOT / "data/train"))
        document = json.loads(EVIDENCE_MANIFEST.read_text(encoding="utf-8"))
        document["dataset"]["training_set_1"]["instances"]["prob_1.json"] = "0" * 64
        with tempfile.TemporaryDirectory() as directory:
            manifest = Path(directory) / "manifest.json"
            manifest.write_text(json.dumps(document), encoding="utf-8")
            with self.assertRaisesRegex(ContractError, "hash mismatch"):
                validate_dataset(manifest_path=manifest)

    def test_partial_invalid_and_duplicate_raw_records_fail_hard(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "raw.jsonl"
            path.write_text('{"schema_version":1', encoding="utf-8")
            with self.assertRaisesRegex(ContractError, "invalid raw JSON"):
                load_raw(path)
            path.write_text("\n", encoding="utf-8")
            with self.assertRaisesRegex(ContractError, "blank/partial"):
                load_raw(path)

    def test_resume_accepts_only_schema_valid_terminal_records(self):
        record = {field: None for field in (
            "schema_version run_key status instance instance_hash dataset_hash variant config_hash source_commit budget_seconds seed start_utc end_utc elapsed_seconds feasible stage violations obj1 obj2 obj3 objective internal_checker_objective_error phase_timings checker_timings model_stats construction_stats constructor_deadline_hit validated_best_trace validated_best_events lns_invocations operator_stats exception outer_timeout environment"
        ).split()}
        record.update(schema_version=SCHEMA_VERSION, run_key="a" * 64, status="completed")
        validate_raw_record(record)
        invalid = copy.deepcopy(record)
        invalid["status"] = "running"
        with self.assertRaisesRegex(ContractError, "not terminal"):
            validate_raw_record(invalid)
        missing = copy.deepcopy(record)
        del missing["stage"]
        with self.assertRaisesRegex(ContractError, "missing fields"):
            validate_raw_record(missing)

    def test_run_key_and_config_hash_are_deterministic(self):
        kwargs = dict(
            source_commit="a" * 40, dataset_hash="b" * 64,
            config_digest=config_hash("constructor_retime"),
            variant="constructor_retime", instance_hash="c" * 64,
            budget=60.0, seed=20260710,
        )
        self.assertEqual(make_run_key(**kwargs), make_run_key(**kwargs))
        changed = dict(kwargs, seed=20260711)
        self.assertNotEqual(make_run_key(**kwargs), make_run_key(**changed))
        self.assertNotEqual(
            config_hash("heuristic_lns", geometry_fit_mode="legacy"),
            config_hash("heuristic_lns", geometry_fit_mode="precomputed"),
        )
        self.assertNotEqual(
            config_hash("heuristic_lns", retime_exact_skip_mode="legacy"),
            config_hash("heuristic_lns", retime_exact_skip_mode="exact"),
        )
        self.assertNotEqual(
            config_hash("heuristic_lns", neighborhood_mode="legacy"),
            config_hash("heuristic_lns", neighborhood_mode="portfolio"),
        )

    def test_final_matrix_contract_is_exact_daily_40_by_2_budgets_by_3_seeds(self):
        self.assertEqual(240, FINAL_EXPECTED_RUN_COUNT)
        self.assertEqual(
            FINAL_EXPECTED_RUN_COUNT,
            FINAL_INSTANCE_COUNT * len(FINAL_BUDGETS) * len(FINAL_SEEDS),
        )
        validate_final_matrix_contract(
            variant=FINAL_VARIANT,
            budgets=FINAL_BUDGETS,
            seeds=FINAL_SEEDS,
            requested_instances=None,
        )
        invalid = (
            {"variant": "constructor_retime"},
            {"budgets": (10.0, 60.0, 300.0)},
            {"budgets": tuple(reversed(FINAL_BUDGETS))},
            {"seeds": (*FINAL_SEEDS, 20260713, 20260714)},
            {"seeds": tuple(reversed(FINAL_SEEDS))},
            {"requested_instances": ("prob_39.json",)},
        )
        defaults = {
            "variant": FINAL_VARIANT,
            "budgets": FINAL_BUDGETS,
            "seeds": FINAL_SEEDS,
            "requested_instances": None,
        }
        for change in invalid:
            with self.subTest(change=change), self.assertRaises(ContractError):
                validate_final_matrix_contract(**(defaults | change))

    def test_summary_missing_field_is_rejected(self):
        with self.assertRaisesRegex(ContractError, "summary missing"):
            validate_summary({"schema_version": SCHEMA_VERSION})

    def test_direct_invocation_measurement_requires_anchor_extension_shape(self):
        record = {
            "lns_invocations": [
                {
                    "kind": "anchor",
                    "repair_seconds": 2.0,
                    "retime_seconds": 0.5,
                    "iterations": 3,
                },
                {
                    "kind": "extension",
                    "repair_seconds": 4.0,
                    "retime_seconds": 1.0,
                    "iterations": 5,
                },
            ]
        }
        repair, retime, iterations, rows = _invocation_measurement(
            record, ("anchor", "extension")
        )
        self.assertEqual((6.0, 1.5, 8), (repair, retime, iterations))
        self.assertEqual(2, len(rows))
        with self.assertRaisesRegex(ContractError, "kinds"):
            _invocation_measurement(record, ("extension", "anchor"))


if __name__ == "__main__":
    unittest.main()
