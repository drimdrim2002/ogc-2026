from __future__ import annotations

from types import SimpleNamespace
import unittest
from unittest.mock import patch

from solver.budget import Budget
from solver.construct import CandidateScore, ConstructorConfig, generate_insertion_candidates
from solver.geometry import GeometryKernel
from solver.instance import parse_instance
from solver.native_repair import (
    NativeCandidateBatch,
    NativePreparedBatch,
    NativeRepairAdapter,
    NativeRepairCursor,
    NativeRepairSession,
    load_native_module,
    snapshot_digest,
)
from solver.neighborhoods import NeighborhoodContext, heuristic_repair
from solver.runtime import SubmissionConfig
from solver.serialize import serialize
from solver.state import Placement, SolutionSnapshot, compute_objective
from tests.helpers import block, checker, instance
from tests.test_neighborhoods import neighborhood_fixture


class _RaisingAdapter:
    def __init__(self, *_args, **_kwargs):
        self.static_packed_bytes_estimate = 0

    def prepare(self, *_args, **_kwargs):
        raise RuntimeError("synthetic C++ exception")

    def create_cursor(self, *args, **kwargs):
        return self.prepare(*args, **kwargs)


class _OSErrorAdapter(_RaisingAdapter):
    def prepare(self, *_args, **_kwargs):
        raise OSError("synthetic native ABI/runtime failure")


class _InvalidAdapter:
    def __init__(self, *_args, **_kwargs):
        self.static_packed_bytes_estimate = 0

    def prepare(self, state, *_args, **_kwargs):
        raw = SimpleNamespace(
            state_version=state.version,
            rows=[("not", "an", "int", "row")],
            pairs=[],
            pair_offsets=[0, 0],
            pair_definitely_free=[],
            total=[0.0],
            tardiness=[0.0],
            assignment=[0.0],
            fragmentation=[0],
        )
        return NativePreparedBatch(raw, state.version, 0, 0, 0, 0)

    prepare_chunk = prepare


class _NativeExactResultAdapter:
    """Return one well-formed native row that Python exact recheck rejects."""

    def __init__(self, *_args, **_kwargs):
        self.static_packed_bytes_estimate = 0
        self._block_id = 0

    def create_cursor(self, state, block_id, *_args, **_kwargs):
        self._block_id = block_id
        raw = SimpleNamespace(
            state_version=state.version,
            prepare_ns=0,
            accepted_total=1,
            deadline_hit=False,
            complete=False,
        )
        return NativeRepairCursor(raw, state.version, 0, 0, 0, 0)

    def run_exact_cursor(self, cursor, _remaining_seconds):
        row = (self._block_id, 0, 0, 1_000_000, 1_000_000, 0, 1)
        raw = SimpleNamespace(
            state_version=cursor.state_version,
            kernel_ns=0,
            generated_candidates=1,
            aabb_skipped_pairs=0,
            geos_exact_calls=0,
            first_conflict_skipped_pairs=0,
            exact_cache_hits=0,
            exact_cache_misses=0,
            geos_error_count=0,
            native_exact_ns=0,
            native_cache_ns=0,
            native_error_ns=0,
            exact_error=False,
            deadline_hit=False,
            complete=True,
        )
        cursor.raw.complete = True
        return NativeCandidateBatch(
            raw,
            cursor.state_version,
            0,
            0,
            (row,),
            (),
            (0, 0),
            (0.0,),
            (0.0,),
            (0.0,),
            (0,),
            (0,),
        )


class _NativeExactGeosErrorAdapter(_NativeExactResultAdapter):
    def create_cursor(self, state, block_id, *_args, **_kwargs):
        cursor = super().create_cursor(state, block_id)
        cursor.raw.accepted_total = 0
        return cursor

    def run_exact_cursor(self, cursor, _remaining_seconds):
        raw = SimpleNamespace(
            state_version=cursor.state_version,
            kernel_ns=1,
            generated_candidates=1,
            aabb_skipped_pairs=0,
            geos_exact_calls=1,
            first_conflict_skipped_pairs=0,
            exact_cache_hits=0,
            exact_cache_misses=1,
            geos_error_count=1,
            native_exact_ns=0,
            native_cache_ns=0,
            native_error_ns=1,
            exact_error=True,
            deadline_hit=False,
            complete=False,
        )
        return NativeCandidateBatch(
            raw,
            cursor.state_version,
            0,
            0,
            (),
            (),
            (0,),
            (),
            (),
            (),
            (),
            (),
        )


class _NativeExactDeadlineAdapter(_NativeExactGeosErrorAdapter):
    def run_exact_cursor(self, cursor, _remaining_seconds):
        raw = SimpleNamespace(
            state_version=cursor.state_version,
            kernel_ns=0,
            generated_candidates=0,
            aabb_skipped_pairs=0,
            geos_exact_calls=0,
            first_conflict_skipped_pairs=0,
            exact_cache_hits=0,
            exact_cache_misses=0,
            geos_error_count=0,
            native_exact_ns=0,
            native_cache_ns=0,
            native_error_ns=0,
            exact_error=False,
            deadline_hit=True,
            complete=False,
        )
        cursor.raw.deadline_hit = True
        return NativeCandidateBatch(
            raw,
            cursor.state_version,
            0,
            0,
            (),
            (),
            (0,),
            (),
            (),
            (),
            (),
            (),
        )


def _native_session_factory(adapter):
    original = NativeRepairSession

    def create(*args, **kwargs):
        return original(*args, **kwargs, adapter_factory=adapter)

    return create


class NativeRepairIntegrationTests(unittest.TestCase):
    def _repair(self, *, backend: str, adapter=None, exact_mode: str = "python"):
        raw, parsed, kernel, snapshot, context = neighborhood_fixture()
        config = ConstructorConfig(
            max_profiles=1,
            repair_backend=backend,
            native_prefilter_enabled=True,
            native_exact_mode=exact_mode,
        )
        context = NeighborhoodContext(parsed, kernel, config)
        if adapter is None:
            return raw, kernel, snapshot, heuristic_repair(
                snapshot, (0, 1), context, Budget.start(5.0)
            )
        with (
            patch("solver.native_repair.native_module_status", return_value=(object(), None)),
            patch("solver.neighborhoods.NativeRepairSession", _native_session_factory(adapter)),
        ):
            result = heuristic_repair(snapshot, (0, 1), context, Budget.start(5.0))
        return raw, kernel, snapshot, result

    def _assert_stage_five_identity_safe(self, raw, kernel, snapshot, result):
        checked = checker(raw, serialize(result.snapshot, kernel))
        self.assertTrue(checked["feasible"], checked)
        self.assertEqual(5, checked["stage"])
        anchor_checked = checker(raw, serialize(snapshot, kernel))
        self.assertTrue(anchor_checked["feasible"], anchor_checked)
        self.assertEqual(5, anchor_checked["stage"])
        self.assertLessEqual(
            result.snapshot.objective.total,
            snapshot.objective.total + 1e-9,
        )
        telemetry = dict(result.telemetry)
        self.assertEqual("native", telemetry["requested_backend"])
        self.assertEqual(snapshot.objective.total, telemetry["original_anchor_objective"])
        self.assertEqual(result.snapshot.objective.total, telemetry["final_objective"])
        self.assertFalse(telemetry["dominance_guard_rejected"])
        self.assertGreaterEqual(telemetry["fallback_count"], 1)

    def test_explicit_python_path_does_not_probe_or_select_native(self):
        raw, parsed, kernel, snapshot, _ = neighborhood_fixture()
        context = NeighborhoodContext(
            parsed,
            kernel,
            ConstructorConfig(
                max_profiles=1,
                repair_backend="python",
                native_exact_mode="python",
            ),
        )
        with patch("solver.native_repair.native_module_status") as availability:
            result = heuristic_repair(snapshot, (0, 1), context, Budget.start(5.0))
        self.assertTrue(result.feasible, result)
        availability.assert_not_called()
        telemetry = dict(result.telemetry)
        self.assertEqual("python", telemetry["requested_backend"])
        self.assertEqual("python", telemetry["actual_backend"])
        self.assertIsNone(telemetry["native_available"])
        checked = checker(raw, serialize(result.snapshot, kernel))
        self.assertEqual(5, checked["stage"])

    def test_native_repair_defaults_are_promoted_and_validate_literals(self):
        constructor = ConstructorConfig()
        submission = SubmissionConfig.from_defaults()
        self.assertEqual("native", constructor.repair_backend)
        self.assertEqual("native", constructor.native_exact_mode)
        self.assertEqual("native", submission.repair_backend)
        self.assertEqual("native", submission.native_exact_mode)
        self.assertFalse(constructor.native_prefilter_enabled)
        self.assertFalse(submission.native_prefilter_enabled)
        self.assertFalse(submission.mip_enabled)
        self.assertFalse(submission.interlock_enabled)
        with self.assertRaises(ValueError):
            ConstructorConfig(native_exact_mode="invalid")
        with self.assertRaises(ValueError):
            SubmissionConfig(native_exact_mode="invalid")

    def test_default_native_import_failure_preserves_stage_five_anchor(self):
        raw, parsed, kernel, snapshot, _ = neighborhood_fixture()
        context = NeighborhoodContext(
            parsed,
            kernel,
            ConstructorConfig(max_profiles=1),
        )
        with patch(
            "solver.native_repair.native_module_status",
            return_value=(None, "native_import_ImportError"),
        ):
            result = heuristic_repair(snapshot, (0, 1), context, Budget.start(5.0))
        self._assert_stage_five_identity_safe(raw, kernel, snapshot, result)
        telemetry = dict(result.telemetry)
        self.assertIn("native_import", telemetry["fallback_reason"])
        self.assertFalse(telemetry["native_available"])

    @unittest.skipUnless(
        load_native_module() is not None,
        "local native extension is not configured",
    )
    def test_default_configuration_selects_native_exact_runtime(self):
        raw, parsed, kernel, snapshot, _ = neighborhood_fixture()
        context = NeighborhoodContext(
            parsed,
            kernel,
            ConstructorConfig(max_profiles=1),
        )
        result = heuristic_repair(
            snapshot, (0, 1), context, Budget.start(5.0)
        )
        checked = checker(raw, serialize(result.snapshot, kernel))
        self.assertEqual(5, checked["stage"])
        telemetry = dict(result.telemetry)
        self.assertEqual("native", telemetry["requested_backend"])
        self.assertEqual("native", telemetry["actual_backend"])
        self.assertEqual("native", telemetry["native_exact_mode"])
        self.assertTrue(telemetry["native_exact_decision_enabled"])
        self.assertGreater(telemetry["native_calls"], 0)
        self.assertEqual(0, telemetry["native_error_count"])

    def test_runtime_oserror_and_cpp_exception_fall_back_to_python_reference(self):
        for adapter in (_OSErrorAdapter, _RaisingAdapter):
            with self.subTest(adapter=adapter.__name__):
                raw, kernel, snapshot, result = self._repair(
                    backend="native", adapter=adapter
                )
                self._assert_stage_five_identity_safe(raw, kernel, snapshot, result)
                telemetry = dict(result.telemetry)
                self.assertGreaterEqual(telemetry["native_exception_count"], 1)
                self.assertGreaterEqual(telemetry["python_reference_calls"], 1)

    def test_invalid_native_output_falls_back_to_python_reference(self):
        raw, kernel, snapshot, result = self._repair(
            backend="native", adapter=_InvalidAdapter
        )
        self._assert_stage_five_identity_safe(raw, kernel, snapshot, result)
        telemetry = dict(result.telemetry)
        self.assertGreaterEqual(telemetry["native_invalid_output_count"], 1)

    def test_deadline_does_not_restart_python_reference_or_mutate_input_state(self):
        raw, parsed, kernel, snapshot, _ = neighborhood_fixture()
        context = NeighborhoodContext(
            parsed,
            kernel,
            ConstructorConfig(max_profiles=1, repair_backend="native"),
        )

        class DeadlineAfterOuterGuard:
            def __init__(self):
                self.calls = 0

            def can_start(self, _predicted, margin=0.0):
                del margin
                self.calls += 1
                return self.calls == 1

            def remaining(self):
                return 0.0

        with (
            patch("solver.native_repair.native_module_status", return_value=(object(), None)),
            patch("solver.neighborhoods.NativeRepairSession", _native_session_factory(_RaisingAdapter)),
        ):
            result = heuristic_repair(
                snapshot, (0, 1), context, DeadlineAfterOuterGuard()
            )
        self.assertIs(snapshot, result.snapshot)
        checked = checker(raw, serialize(result.snapshot, kernel))
        self.assertTrue(checked["feasible"], checked)
        self.assertEqual(5, checked["stage"])
        telemetry = dict(result.telemetry)
        self.assertGreaterEqual(telemetry["native_deadline_count"], 1)
        self.assertEqual("native_deadline_before_prepare", telemetry["fallback_reason"])
        self.assertEqual(0, telemetry["python_reference_calls"])
        self.assertEqual(telemetry["original_anchor_digest"], telemetry["final_digest"])

    @unittest.skipUnless(
        load_native_module() is not None,
        "local native extension is not configured",
    )
    def test_real_cursor_matches_python_and_emits_bounded_batches(self):
        raw, parsed, kernel, snapshot, _ = neighborhood_fixture()
        python_context = NeighborhoodContext(
            parsed,
            kernel,
            ConstructorConfig(
                max_profiles=1,
                repair_backend="python",
                native_exact_mode="python",
            ),
        )
        native_context = NeighborhoodContext(
            parsed,
            kernel,
            ConstructorConfig(
                max_profiles=1,
                repair_backend="native",
                native_prefilter_enabled=True,
                native_exact_mode="python",
            ),
        )
        batch_sizes = []
        original_next_batch = NativeRepairAdapter.next_cursor_batch

        def recorded_next_batch(cursor, remaining_seconds):
            batch = original_next_batch(cursor, remaining_seconds)
            batch_sizes.append(len(batch.rows))
            return batch

        python = heuristic_repair(
            snapshot, (0, 1), python_context, Budget.start(5.0)
        )
        with patch.object(
            NativeRepairAdapter,
            "next_cursor_batch",
            staticmethod(recorded_next_batch),
        ):
            native = heuristic_repair(
                snapshot, (0, 1), native_context, Budget.start(5.0)
            )
        self.assertEqual(snapshot_digest(python.snapshot), snapshot_digest(native.snapshot))
        checked = checker(raw, serialize(native.snapshot, kernel))
        self.assertEqual(5, checked["stage"])
        telemetry = dict(native.telemetry)
        self.assertTrue(batch_sizes)
        self.assertLessEqual(max(batch_sizes), 16)
        self.assertGreater(telemetry["cursor_count"], 0)
        self.assertGreater(telemetry["batches"], 0)
        self.assertEqual(telemetry["candidate_rows"], telemetry["generated_rows"])
        self.assertEqual(telemetry["pair_count"], telemetry["emitted_pairs"])
        self.assertEqual(
            telemetry["exact_relation_calls"], telemetry["python_exact_calls"]
        )

    @unittest.skipUnless(
        load_native_module() is not None,
        "local native extension is not configured",
    )
    def test_phase2_shadow_compares_without_native_decision_authority(self):
        raw, parsed, kernel, snapshot, _ = neighborhood_fixture()
        python_context = NeighborhoodContext(
            parsed,
            kernel,
            ConstructorConfig(max_profiles=1, repair_backend="python"),
        )
        native_context = NeighborhoodContext(
            parsed,
            kernel,
            ConstructorConfig(
                max_profiles=1,
                repair_backend="native",
                native_prefilter_enabled=True,
                native_exact_mode="shadow",
            ),
        )
        python = heuristic_repair(
            snapshot, (0, 1), python_context, Budget.start(5.0)
        )
        native = heuristic_repair(
            snapshot, (0, 1), native_context, Budget.start(5.0)
        )
        self.assertEqual(snapshot_digest(python.snapshot), snapshot_digest(native.snapshot))
        checked = checker(raw, serialize(native.snapshot, kernel))
        self.assertEqual(5, checked["stage"])
        telemetry = dict(native.telemetry)
        self.assertEqual("shadow", telemetry["native_exact_mode"])
        self.assertFalse(telemetry["native_exact_decision_enabled"])
        self.assertGreater(telemetry["shadow_checked_pairs"], 0)
        self.assertEqual(0, telemetry["shadow_mismatch_count"])
        self.assertEqual(0, telemetry["geos_error_count"])
        self.assertEqual(
            telemetry["shadow_checked_pairs"],
            telemetry["shadow_free_free_count"]
            + telemetry["shadow_blocked_blocked_count"],
        )

    @unittest.skipUnless(
        load_native_module() is not None,
        "local native extension is not configured",
    )
    def test_phase3_native_exact_closes_inner_loop_and_rechecks_top3(self):
        raw, parsed, kernel, snapshot, _ = neighborhood_fixture()
        python_context = NeighborhoodContext(
            parsed,
            kernel,
            ConstructorConfig(max_profiles=1, repair_backend="python"),
        )
        native_context = NeighborhoodContext(
            parsed,
            kernel,
            ConstructorConfig(
                max_profiles=1,
                repair_backend="native",
                native_exact_mode="native",
            ),
        )
        python = heuristic_repair(
            snapshot, (0, 1), python_context, Budget.start(5.0)
        )
        native = heuristic_repair(
            snapshot, (0, 1), native_context, Budget.start(5.0)
        )
        self.assertEqual(snapshot_digest(python.snapshot), snapshot_digest(native.snapshot))
        self.assertEqual(5, checker(raw, serialize(native.snapshot, kernel))["stage"])
        telemetry = dict(native.telemetry)
        self.assertTrue(telemetry["native_exact_decision_enabled"])
        self.assertEqual(
            "native_geos_with_python_returned_candidate_recheck",
            telemetry["exact_geometry_authority"],
        )
        self.assertGreater(telemetry["generated_candidates"], 0)
        self.assertGreater(telemetry["exact_relation_calls"], 0)
        self.assertGreater(telemetry["python_returned_candidate_rechecks"], 0)
        self.assertEqual(0, telemetry["returned_candidate_recheck_failures"])
        self.assertEqual(0, telemetry["python_exact_calls"])
        self.assertEqual(0, telemetry["geos_error_count"])
        self.assertEqual(0, telemetry["native_error_count"])
        self.assertEqual(0, telemetry["fallback_count"])

    def test_phase3_recheck_mismatch_discards_repair_without_python_fallback(self):
        raw, kernel, snapshot, result = self._repair(
            backend="native",
            adapter=_NativeExactResultAdapter,
            exact_mode="native",
        )
        self.assertIs(snapshot, result.snapshot)
        self.assertEqual(5, checker(raw, serialize(result.snapshot, kernel))["stage"])
        telemetry = dict(result.telemetry)
        self.assertGreater(telemetry["returned_candidate_recheck_failures"], 0)
        self.assertGreater(telemetry["native_error_count"], 0)
        self.assertEqual(0, telemetry["python_reference_calls"])
        self.assertEqual(0, telemetry["fallback_count"])

    def test_phase3_geos_error_discards_repair_without_python_fallback(self):
        raw, kernel, snapshot, result = self._repair(
            backend="native",
            adapter=_NativeExactGeosErrorAdapter,
            exact_mode="native",
        )
        self.assertIs(snapshot, result.snapshot)
        self.assertEqual(5, checker(raw, serialize(result.snapshot, kernel))["stage"])
        telemetry = dict(result.telemetry)
        self.assertGreater(telemetry["geos_error_count"], 0)
        self.assertGreater(telemetry["native_error_count"], 0)
        self.assertEqual(0, telemetry["python_reference_calls"])
        self.assertEqual(0, telemetry["fallback_count"])

    def test_phase3_deadline_without_partial_top_does_not_start_python_repair(self):
        raw, kernel, snapshot, result = self._repair(
            backend="native",
            adapter=_NativeExactDeadlineAdapter,
            exact_mode="native",
        )
        self.assertIs(snapshot, result.snapshot)
        self.assertEqual(5, checker(raw, serialize(result.snapshot, kernel))["stage"])
        telemetry = dict(result.telemetry)
        self.assertGreater(telemetry["native_deadline_count"], 0)
        self.assertEqual(0, telemetry["python_reference_calls_after_deadline"])
        self.assertEqual(0, telemetry["python_full_repair_started_after_deadline"])
        self.assertEqual(0, telemetry["python_reference_calls"])

    def test_native_dominance_guard_returns_frozen_anchor(self):
        raw = instance([block(due=0, processing=1)], bays=((12, 12),))
        parsed = parse_instance(raw)
        kernel = GeometryKernel.from_instance(parsed)
        anchor = SolutionSnapshot((Placement(0, 0, 0, 0, 0, 0, 1),))
        anchor = anchor.with_objective(compute_objective(parsed, anchor))
        worse = Placement(0, 0, 0, 0, 0, 10, 11)
        score = CandidateScore(
            worse,
            10.0,
            10.0,
            0.0,
            0,
            (10.0, 0, 0, 11, 10, 0, 0, 0, 0, 0),
            0,
        )
        context = NeighborhoodContext(
            parsed, kernel, ConstructorConfig(max_profiles=1, repair_backend="native")
        )
        with patch("solver.neighborhoods.generate_insertion_candidates", return_value=(score,)):
            result = heuristic_repair(anchor, (0,), context, Budget.start(5.0))
        self.assertEqual("DOMINANCE_REJECTED", result.status)
        self.assertIs(anchor, result.snapshot)
        telemetry = dict(result.telemetry)
        self.assertTrue(telemetry["dominance_guard_rejected"])
        self.assertEqual(0.0, result.objective_delta)

    def test_backend_alias_normalizes_to_explicit_python(self):
        config = ConstructorConfig(candidate_backend="python")
        self.assertEqual("python", config.repair_backend)
        self.assertEqual("python", config.candidate_backend)
        with self.assertRaises(ValueError):
            ConstructorConfig(repair_backend="python", candidate_backend="native")


if __name__ == "__main__":
    unittest.main()
