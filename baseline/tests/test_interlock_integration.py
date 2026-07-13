from __future__ import annotations

import copy
import json
import math
import unittest
from pathlib import Path
from types import SimpleNamespace

from solver import entry
from solver.alns import AlnsConfig, AlnsContext, run_lns
from solver.budget import Budget
from solver.geometry import GeometryKernel, PairState, TemporalMode
from solver.instance import parse_instance
from solver.interlock import InterlockConfig, InterlockContext, densify
from solver.neighborhoods import RepairResult
from solver.retime import retime
from solver.serialize import serialize
from solver.state import IncumbentStore, Placement, SolutionSnapshot, compute_objective
from tests.helpers import block, checker, instance
from tests.test_interlock import witness


def installed(raw, parsed, kernel, snapshot):
    snapshot = snapshot.with_objective(compute_objective(parsed, snapshot))
    operations = serialize(snapshot, kernel)
    store = IncumbentStore(parsed)
    assert store.install_if_valid(snapshot, operations, checker(raw, operations))
    return snapshot, store


class FixedDestroy:
    name = "fixed"

    def select(self, current, k, rng, context):
        del current, k, rng, context
        return (0,)


class InterlockIntegrationTests(unittest.TestCase):
    def test_synthetic_one_way_witness_installs_nested_stage_five_improvement(self):
        raw, _, kernel, snapshot, store, context = witness()

        result = densify(snapshot, store, context, Budget.start(100), retime)

        self.assertEqual(7.0, snapshot.objective.z1)
        self.assertEqual(0.0, result.snapshot.objective.z1)
        checked = checker(copy.deepcopy(raw), copy.deepcopy(result.serialized_operations))
        self.assertTrue(checked["feasible"], checked)
        self.assertEqual(5, checked["stage"])
        self.assertTrue(math.isclose(0.0, checked["objective"], abs_tol=1e-9))
        self.assertEqual(1, result.metrics.installed)
        self.assertEqual(1, result.metrics.interlock_improvements)
        self.assertEqual((TemporalMode.K_NESTED.value,), result.metrics.selected_modes)
        by_id = {item.block_id: item for item in result.snapshot.placements}
        self.assertIs(PairState.I_OUTER, kernel.relation(by_id[0], by_id[1]).state)

    def test_nonbeneficial_one_way_candidate_is_not_installed(self):
        _, _, _, snapshot, store, context = witness()

        def identity_retime(draft, *args, **kwargs):
            del args, kwargs
            return SimpleNamespace(status="NO_IMPROVEMENT", snapshot=draft)

        before = store.operations
        result = densify(snapshot, store, context, Budget.start(100), identity_retime)
        self.assertIs(snapshot, result.snapshot)
        self.assertEqual(before, result.serialized_operations)
        self.assertEqual(0, result.metrics.installed)
        self.assertIn("NOT_STRICT_IMPROVEMENT", dict(result.metrics.rollback_reasons))

    def test_retimer_license_failure_preserves_checker_validated_incumbent(self):
        raw, parsed, kernel, snapshot, store, context = witness()

        def failing_retime(draft, instance, geometry, budget, affected_ids):
            def license_failure():
                raise RuntimeError("synthetic license failure")

            return retime(
                draft,
                instance,
                geometry,
                budget,
                affected_ids=affected_ids,
                backend_factory=license_failure,
            )

        result = densify(snapshot, store, context, Budget.start(100), failing_retime)
        self.assertIs(snapshot, result.snapshot)
        checked = checker(raw, result.serialized_operations)
        self.assertTrue(checked["feasible"], checked)
        self.assertEqual(5, checked["stage"])
        self.assertIn("RETIME_ERROR", dict(result.metrics.rollback_reasons))
        self.assertEqual(snapshot.objective.total, store.snapshot.objective.total)

    def test_same_exit_cycle_is_rejected_before_full_checker(self):
        _, _, _, snapshot, store, context = witness()
        checker_calls = 0

        class CycleKernel:
            def __init__(self, delegate):
                self.delegate = delegate

            def shape(self, *args):
                return self.delegate.shape(*args)

            def fits(self, *args):
                return self.delegate.fits(*args)

            def relation(self, *args):
                return self.delegate.relation(*args)

            def entries_are_free(self, *args):
                return self.delegate.entries_are_free(*args)

            def edges(self, exiting_ids, snapshot, bay_id):
                ids = tuple(sorted(exiting_ids))
                if ids == (0, 1):
                    return ((0, 1), (1, 0))
                return self.delegate.edges(ids, snapshot, bay_id)

        def recording_checker(*args):
            nonlocal checker_calls
            checker_calls += 1
            return checker(*args)

        cycle_context = InterlockContext(
            context.instance,
            CycleKernel(context.kernel),
            context.raw,
            recording_checker,
            True,
            context.config,
        )

        def nested_retime(draft, *args, **kwargs):
            del args, kwargs
            by_id = {item.block_id: item for item in draft.placements}
            nested = SolutionSnapshot(
                (
                    Placement(
                        0,
                        by_id[0].bay_id,
                        by_id[0].orient_idx,
                        by_id[0].x,
                        by_id[0].y,
                        0,
                        10,
                    ),
                    Placement(
                        1,
                        by_id[1].bay_id,
                        by_id[1].orient_idx,
                        by_id[1].x,
                        by_id[1].y,
                        2,
                        10,
                    ),
                )
            )
            return SimpleNamespace(status="OPTIMAL", snapshot=nested)

        result = densify(
            snapshot, store, cycle_context, Budget.start(100), nested_retime
        )
        self.assertIs(snapshot, result.snapshot)
        self.assertEqual(0, checker_calls)
        self.assertIn("SERIALIZATION_CYCLE", dict(result.metrics.rollback_reasons))

    def test_alns_calls_densifier_only_after_stall(self):
        raw = instance([block(due=10)], weights=(1, 0, 0))
        parsed = parse_instance(raw)
        kernel = GeometryKernel.from_instance(parsed)
        initial, store = installed(
            raw,
            parsed,
            kernel,
            SolutionSnapshot((Placement(0, 0, 0, 0, 0, 0, 2),)),
        )
        hook_calls = 0

        def engine(current, destroyed, context, budget):
            del context, budget
            return RepairResult(
                current,
                "FEASIBLE",
                destroyed,
                frozenset(),
                1,
                0.0,
            )

        def densify_hook(snapshot, best_store, context, budget, retime_hook):
            nonlocal hook_calls
            del snapshot, best_store, context, budget, retime_hook
            hook_calls += 1
            return SimpleNamespace(
                metrics=SimpleNamespace(
                    candidates=0,
                    installed=0,
                    interlock_improvements=0,
                    gate_reason="FEATURE_DISABLED",
                )
            )

        result = run_lns(
            initial,
            store,
            AlnsContext(
                parsed,
                kernel,
                raw,
                checker,
                destroy_operators=(FixedDestroy(),),
                repair_engines=(engine,),
            ),
            Budget.start(5),
            AlnsConfig(
                warmup_iterations=0,
                stall_iterations=1,
                stall_time_fraction=0.0,
                max_iterations=2,
            ),
            densify_hook=densify_hook,
        )
        self.assertEqual(1, hook_calls)
        self.assertEqual(1, result.metrics.densify_triggers)
        self.assertEqual("FEATURE_DISABLED", result.metrics.densify_reason)

    def test_submission_activation_defaults_remain_off(self):
        self.assertFalse(entry.LNS_ENABLED)
        self.assertFalse(entry.INTERLOCK_ENABLED)

    def test_real_prob4_witness_when_data_available(self):
        source_path = Path(__file__).resolve().parents[2] / "data/train 2/prob_4.json"
        if not source_path.exists():
            self.skipTest("real prob_4 training file is unavailable")
        source = json.loads(source_path.read_text())
        outer = copy.deepcopy(source["blocks"][24])
        inner = copy.deepcopy(source["blocks"][46])
        outer.update(release_time=0, due_date=10, processing_time=10, bay_preferences=[0])
        inner.update(release_time=2, due_date=7, processing_time=5, bay_preferences=[0])
        raw = {
            "name": "real-prob4-interlock-densifier",
            "bays": [copy.deepcopy(source["bays"][0])],
            "blocks": [outer, inner],
            "weights": {"w1": 1, "w2": 0, "w3": 0},
        }
        parsed = parse_instance(raw)
        kernel = GeometryKernel.from_instance(parsed)
        conservative, store = installed(
            raw,
            parsed,
            kernel,
            SolutionSnapshot(
                (
                    Placement(0, 0, 5, 102, 18, 7, 17),
                    Placement(1, 0, 1, 115, 3, 2, 7),
                )
            ),
        )
        context = InterlockContext(
            parsed,
            kernel,
            raw,
            checker,
            True,
            InterlockConfig(enabled=True, pressure_threshold=0.0),
        )

        result = densify(conservative, store, context, Budget.start(100), retime)

        self.assertEqual(7.0, conservative.objective.z1)
        self.assertEqual(0.0, result.snapshot.objective.z1)
        checked = checker(raw, result.serialized_operations)
        self.assertEqual(5, checked["stage"])
        self.assertEqual(0.0, checked["objective"])
        self.assertEqual(1, result.metrics.interlock_improvements)


if __name__ == "__main__":
    unittest.main()
