from __future__ import annotations

import unittest

from solver.budget import Budget
from solver.geometry import GeometryKernel, PairState
from solver.instance import parse_instance
from solver.interlock import (
    InterlockConfig,
    InterlockContext,
    InterlockGate,
    decide_interlock_gate,
    densifier_budget_cap,
    densify,
    generate_interlock_candidates,
    rank_hosts,
    saturated_windows,
    union_energy_pressure,
)
from solver.serialize import serialize
from solver.state import IncumbentStore, Placement, SolutionSnapshot, compute_objective
from tests.helpers import block, checker, instance, rectangle


def witness(*, negative_aabb: bool = False):
    if negative_aabb:
        low = [[[6, 6], [0, 6], [0, 0], [6, 0]]]
        host = [
            [[6, 6], [0, 6], [0, 0], [6, 0]],
            [[12, 6], [6, 6], [6, 0], [12, 0]],
        ]
        mover_position = (6, 6)
        host_position = (6, 6)
    else:
        low = [rectangle(0, 0, 6, 6)]
        host = [rectangle(0, 0, 6, 6), rectangle(6, 0, 12, 6)]
        mover_position = (0, 0)
        host_position = (0, 0)
    raw = instance(
        [
            block(release=0, due=10, processing=10, orientations=[low]),
            block(release=2, due=7, processing=5, orientations=[host]),
        ],
        bays=((12, 12),),
        weights=(1, 0, 0),
    )
    parsed = parse_instance(raw)
    kernel = GeometryKernel.from_instance(parsed)
    snapshot = SolutionSnapshot(
        (
            Placement(0, 0, 0, *mover_position, 7, 17),
            Placement(1, 0, 0, *host_position, 2, 7),
        )
    )
    snapshot = snapshot.with_objective(compute_objective(parsed, snapshot))
    operations = serialize(snapshot, kernel)
    store = IncumbentStore(parsed)
    assert store.install_if_valid(snapshot, operations, checker(raw, operations))
    context = InterlockContext(
        parsed,
        kernel,
        raw,
        checker,
        True,
        InterlockConfig(enabled=True),
    )
    return raw, parsed, kernel, snapshot, store, context


class InterlockUnitTests(unittest.TestCase):
    def test_activation_gate_all_branches_and_threshold(self):
        base = dict(
            enabled=True,
            tardiness=1.0,
            pressure=0.45,
            stalled=True,
            remaining=2.0,
            reserve=1.0,
        )
        cases = (
            ({"enabled": False}, "FEATURE_DISABLED"),
            ({"tardiness": 0.0}, "NO_TARDINESS"),
            ({"pressure": 0.449999}, "LOW_PRESSURE"),
            ({"stalled": False}, "NOT_STALLED"),
            ({"remaining": 1.0}, "DEADLINE_RESERVE"),
        )
        for changes, reason in cases:
            with self.subTest(reason=reason):
                values = base | changes
                decision = decide_interlock_gate(InterlockGate(**values))
                self.assertFalse(decision.run)
                self.assertEqual(reason, decision.reason)
        self.assertEqual("RUN", decide_interlock_gate(InterlockGate(**base)).reason)

    def test_budget_is_hard_capped_at_eight_percent(self):
        self.assertEqual(8.0, densifier_budget_cap(100.0, 100.0, 0.0))
        self.assertEqual(3.0, densifier_budget_cap(100.0, 5.0, 2.0))
        self.assertEqual(0.0, densifier_budget_cap(100.0, 2.0, 2.0))
        with self.assertRaises(ValueError):
            InterlockConfig(max_budget_fraction=0.080001)

    def test_pressure_and_window_use_union_area_energy_capacity(self):
        _, parsed, kernel, snapshot, _, _ = witness()
        windows = saturated_windows(snapshot, parsed, kernel)
        self.assertEqual((2, 7), (windows[0].start, windows[0].end))
        self.assertEqual((1,), windows[0].active_ids)
        self.assertAlmostEqual(0.5, windows[0].pressure)
        self.assertAlmostEqual(0.5, union_energy_pressure(snapshot, parsed, kernel))

    def test_generator_keeps_only_exact_one_way_relations(self):
        _, _, kernel, snapshot, _, context = witness()
        candidates = tuple(
            generate_interlock_candidates(snapshot, context, Budget.start(5))
        )
        self.assertTrue(candidates)
        self.assertEqual(
            {PairState.I_OUTER}, {item.relation.state for item in candidates}
        )
        self.assertTrue(all(kernel.fits(item.placement) for item in candidates))
        self.assertIn((6, 0), {(item.placement.x, item.placement.y) for item in candidates})

    def test_negative_aabb_edge_anchor_stays_in_integer_fit_range(self):
        _, _, kernel, snapshot, _, context = witness(negative_aabb=True)
        candidates = tuple(
            generate_interlock_candidates(snapshot, context, Budget.start(5))
        )
        self.assertTrue(candidates)
        self.assertTrue(all(kernel.fits(item.placement) for item in candidates))
        self.assertIn((12, 6), {(item.placement.x, item.placement.y) for item in candidates})

    def test_host_ranking_prefers_zero_or_positive_slack(self):
        raw = instance(
            [
                block(due=0),
                block(due=10),
                block(due=5),
                block(due=4),
            ],
            bays=((20, 10),),
        )
        parsed = parse_instance(raw)
        kernel = GeometryKernel.from_instance(parsed)
        snapshot = SolutionSnapshot(
            tuple(Placement(i, 0, 0, i * 3, 0, 3, 5) for i in range(4))
        )
        context = InterlockContext(parsed, kernel, raw, checker, True)
        ranked = rank_hosts(snapshot, 0, (3, 2, 1), context)
        self.assertEqual((1, 2, 3), tuple(item.block_id for item in ranked))

    def test_gate_off_is_serialized_identity_and_never_calls_retimer(self):
        _, parsed, kernel, snapshot, store, context = witness()
        calls = 0

        def retime_hook(*args, **kwargs):
            nonlocal calls
            calls += 1
            raise AssertionError("feature-off densifier called retimer")

        disabled = InterlockContext(
            parsed,
            kernel,
            context.raw,
            checker,
            True,
            InterlockConfig(enabled=False),
        )
        before = store.operations
        result = densify(snapshot, store, disabled, Budget.start(100), retime_hook)
        self.assertIs(snapshot, result.snapshot)
        self.assertEqual(before, result.serialized_operations)
        self.assertEqual(0, calls)
        self.assertEqual("FEATURE_DISABLED", result.metrics.gate_reason)

    def test_retime_exception_rolls_back_input_transaction(self):
        _, _, _, snapshot, store, context = witness()

        def crash(*args, **kwargs):
            raise RuntimeError("synthetic retime failure")

        before = store.operations
        result = densify(snapshot, store, context, Budget.start(100), crash)
        self.assertIs(snapshot, result.snapshot)
        self.assertEqual(before, result.serialized_operations)
        self.assertIn(("RETIME_RuntimeError", 2), result.metrics.rollback_reasons)


if __name__ == "__main__":
    unittest.main()
