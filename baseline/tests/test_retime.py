from __future__ import annotations

import unittest

from solver.geometry import GeometryKernel, PairRelation, PairState, TemporalMode
from solver.instance import parse_instance
from solver.retime import (
    RetimingConfig,
    bay_horizon,
    build_request,
    mode_allows,
    nonfree_components,
    relation_modes,
)
from solver.state import Placement, SolutionSnapshot
from tests.helpers import block, instance, rectangle


class RetimingPurePythonTests(unittest.TestCase):
    def test_horizon_releases_zero_seven_and_dwell_three_four(self):
        parsed = parse_instance(
            instance(
                [
                    block(release=0, processing=3),
                    block(release=7, processing=4),
                ]
            )
        )
        snapshot = SolutionSnapshot(
            (
                Placement(0, 0, 0, 0, 0, 0, 3),
                Placement(1, 0, 0, 0, 0, 7, 11),
            )
        )

        self.assertEqual(14, bay_horizon(parsed, snapshot, 0))

    def test_empty_bay_has_no_component_and_zero_horizon(self):
        parsed = parse_instance(instance([], bays=((10, 10),)))
        snapshot = SolutionSnapshot(())
        kernel = GeometryKernel.from_instance(parsed)

        self.assertEqual(0, bay_horizon(parsed, snapshot, 0))
        self.assertEqual((), nonfree_components(snapshot, kernel, 0))

    def test_four_state_modes_match_exhaustive_truth_table(self):
        relations = tuple(
            PairRelation.from_bits(g_i_k, g_k_i)
            for g_i_k in (False, True)
            for g_k_i in (False, True)
        )
        cases = 0
        for relation in relations:
            expected_count = {
                PairState.FREE: 1,
                PairState.SEPARATE: 2,
                PairState.I_OUTER: 3,
                PairState.K_OUTER: 3,
            }[relation.state]
            self.assertEqual(expected_count, len(relation_modes(relation)))
            for ai in range(4):
                for ei in range(ai + 1, 6):
                    for ak in range(4):
                        for ek in range(ak + 1, 6):
                            allowed_modes = tuple(
                                mode
                                for mode in relation_modes(relation)
                                if mode_allows(relation, mode, (ai, ei), (ak, ek))
                            )
                            self.assertEqual(relation.allows((ai, ei), (ak, ek)), bool(allowed_modes))
                            self.assertLessEqual(len(allowed_modes), 1)
                            cases += 1
        self.assertEqual(784, cases)

    def test_nonfree_components_include_isolated_free_nodes(self):
        parsed = parse_instance(instance([block(), block(), block()]))
        snapshot = SolutionSnapshot(
            (
                Placement(0, 0, 0, 0, 0, 0, 2),
                Placement(1, 0, 0, 0, 0, 2, 4),
                Placement(2, 0, 0, 5, 0, 0, 2),
            )
        )
        kernel = GeometryKernel.from_instance(parsed)

        self.assertEqual(
            (frozenset((0, 1)), frozenset((2,))),
            nonfree_components(snapshot, kernel, 0),
        )

    def test_request_warm_start_and_affected_component(self):
        parsed = parse_instance(instance([block(), block(), block()]))
        snapshot = SolutionSnapshot(
            (
                Placement(0, 0, 0, 0, 0, 0, 2),
                Placement(1, 0, 0, 0, 0, 2, 4),
                Placement(2, 0, 0, 5, 0, 0, 2),
            )
        )
        kernel = GeometryKernel.from_instance(parsed)

        request = build_request(
            snapshot,
            parsed,
            kernel,
            RetimingConfig(),
            frozenset((0,)),
        )

        self.assertEqual(frozenset((0, 1)), request.modeled_ids)
        self.assertEqual(frozenset((0, 1)), request.free_ids)
        self.assertEqual(1, len(request.pairs))
        self.assertIs(request.pairs[0].warm_mode, TemporalMode.I_BEFORE)

    def test_component_cap_keeps_at_most_eighty_free_and_fixes_boundary(self):
        count = 81
        parsed = parse_instance(instance([block() for _ in range(count)]))
        snapshot = SolutionSnapshot(
            tuple(Placement(index, 0, 0, 0, 0, index * 2, index * 2 + 2) for index in range(count))
        )
        kernel = GeometryKernel.from_instance(parsed)

        request = build_request(snapshot, parsed, kernel, RetimingConfig(max_free=80))

        self.assertEqual(count, len(request.modeled_ids))
        self.assertEqual(80, len(request.free_ids))
        self.assertEqual(80, request.max_component_free)
        self.assertEqual(1, len(request.modeled_ids - request.free_ids))

    def test_one_way_mode_sets_are_symmetric(self):
        i_outer = PairRelation.from_bits(True, False)
        k_outer = i_outer.swapped()

        self.assertEqual(
            (TemporalMode.I_BEFORE, TemporalMode.K_BEFORE, TemporalMode.K_NESTED),
            relation_modes(i_outer),
        )
        self.assertEqual(
            (TemporalMode.I_BEFORE, TemporalMode.K_BEFORE, TemporalMode.I_NESTED),
            relation_modes(k_outer),
        )

    def test_config_rejects_invalid_component_cap(self):
        with self.assertRaises(ValueError):
            RetimingConfig(max_free=0)


if __name__ == "__main__":
    unittest.main()
