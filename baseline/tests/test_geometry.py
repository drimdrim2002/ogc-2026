from __future__ import annotations

import unittest
from unittest.mock import patch

from shapely.ops import unary_union

from solver.geometry import GeometryKernel, PairState, TemporalMode
from solver.instance import parse_instance
from solver.state import Placement
from tests.helpers import block, checker, instance, rectangle


def _placement(
    block_id: int,
    *,
    x: int = 0,
    y: int = 0,
    entry: int = 0,
    exit: int = 2,
    bay_id: int = 0,
    orient_idx: int = 0,
) -> Placement:
    return Placement(block_id, bay_id, orient_idx, x, y, entry, exit)


def _four_state_instance() -> dict:
    low = [rectangle(0, 0, 2, 2)]
    offset_low_with_high_cap = [
        rectangle(0, 0, 2, 2),
        rectangle(4, 0, 6, 2),
    ]
    return instance(
        [
            block(orientations=[low]),
            block(orientations=[offset_low_with_high_cap]),
            block(orientations=[low]),
            block(orientations=[low]),
        ],
        bays=((12, 12),),
    )


class GeometryPreprocessingTests(unittest.TestCase):
    def test_suffix_union_matches_naive(self):
        raw = instance(
            [
                block(
                    orientations=[[
                        rectangle(0, 0, 2, 2),
                        rectangle(1, 0, 4, 1),
                        rectangle(3, 0, 5, 3),
                        rectangle(0, 2, 1, 4),
                    ]]
                )
            ],
            bays=((20, 20),),
        )
        kernel = GeometryKernel.from_instance(parse_instance(raw))
        shape = kernel.shape(0, 0)

        self.assertEqual(len(shape.layers), 4)
        self.assertEqual(len(shape.layer_aabbs), 4)
        self.assertEqual(len(shape.suffix_unions), 4)
        for layer_index in range(4):
            expected = unary_union(
                [geometry for geometry in shape.layers[layer_index:] if geometry is not None]
            )
            actual = shape.suffix_unions[layer_index]
            self.assertEqual(actual.symmetric_difference(expected).area, 0.0)

    def test_invalid_polygon_is_repaired_and_degenerate_is_ignored(self):
        raw = instance(
            [
                block(
                    orientations=[[
                        [[0, 0], [2, 2], [0, 2], [2, 0]],
                        [[0, 0], [1, 0], [2, 0]],
                    ]]
                )
            ],
            bays=((10, 10),),
        )
        shape = GeometryKernel.from_instance(parse_instance(raw)).shape(0, 0)

        self.assertIsNotNone(shape.layers[0])
        self.assertTrue(shape.layers[0].is_valid)
        self.assertIsNone(shape.layers[1])
        self.assertEqual(shape.union.area, shape.layers[0].area)

    def test_negative_anchor_geometry_fits_and_checker_passes(self):
        layers = [[[0, 0], [-3.49, 0], [-3.49, 2], [7.62, 2], [7.62, 0]]]
        raw = instance([block(orientations=[layers])], bays=((12, 4),))
        parsed = parse_instance(raw)
        placement = _placement(0, x=4, y=0)
        kernel = GeometryKernel.from_instance(parsed)

        self.assertTrue(kernel.fits(placement))
        result = checker(
            raw,
            {
                "operations": {
                    "0": [{"type": "ENTRY", "block_id": 0, "bay_id": 0, "x": 4, "y": 0, "orient_idx": 0}],
                    "2": [{"type": "EXIT", "block_id": 0, "bay_id": 0}],
                }
            },
        )
        self.assertTrue(result["feasible"], result)
        self.assertEqual(result["stage"], 5)

    def test_candidate_precomputed_fits_reuses_parsed_integer_bounds(self):
        raw = instance([block()], bays=((12, 12),))
        parsed = parse_instance(raw)
        kernel = GeometryKernel.from_instance(parsed)

        with patch.object(
            type(parsed.block(0).orientations[0]),
            "integer_range",
            side_effect=AssertionError("fits recomputed immutable bounds"),
        ):
            self.assertTrue(kernel.fits_precomputed(_placement(0, x=0, y=0)))
            self.assertFalse(kernel.fits_precomputed(_placement(0, x=100, y=0)))


class FourStateTests(unittest.TestCase):
    def setUp(self):
        self.kernel = GeometryKernel.from_instance(parse_instance(_four_state_instance()), cache_size=4)

    def test_boundary_contact_is_free(self):
        relation = self.kernel.relation(_placement(0), _placement(2, x=2))
        self.assertEqual(relation.state, PairState.FREE)
        self.assertEqual((relation.g_i_k, relation.g_k_i), (False, False))

    def test_same_layer_overlap_is_separate(self):
        relation = self.kernel.relation(_placement(0), _placement(2))
        self.assertEqual(relation.state, PairState.SEPARATE)
        self.assertEqual((relation.g_i_k, relation.g_k_i), (True, True))

    def test_i_outer_and_k_outer(self):
        i_outer = self.kernel.relation(_placement(0, x=4), _placement(1))
        k_outer = self.kernel.relation(_placement(1), _placement(0, x=4))
        self.assertEqual(i_outer.state, PairState.I_OUTER)
        self.assertEqual((i_outer.g_i_k, i_outer.g_k_i), (True, False))
        self.assertEqual(k_outer.state, PairState.K_OUTER)
        self.assertEqual((k_outer.g_i_k, k_outer.g_k_i), (False, True))

    def test_temporal_truth_table(self):
        fixtures = {
            PairState.FREE: self.kernel.relation(_placement(0), _placement(2, x=3)),
            PairState.I_OUTER: self.kernel.relation(_placement(0, x=4), _placement(1)),
            PairState.K_OUTER: self.kernel.relation(_placement(1), _placement(0, x=4)),
            PairState.SEPARATE: self.kernel.relation(_placement(0), _placement(2)),
        }
        before = ((0, 2), (2, 4))
        after = ((2, 4), (0, 2))
        i_contains_k = ((0, 5), (1, 4))
        k_contains_i = ((1, 4), (0, 5))
        equal_entry = ((0, 5), (0, 4))

        for state, relation in fixtures.items():
            with self.subTest(state=state, mode="before"):
                self.assertEqual(relation.mode(*before), TemporalMode.I_BEFORE if state is not PairState.FREE else TemporalMode.FREE)
                self.assertTrue(relation.allows(*before))
            with self.subTest(state=state, mode="after"):
                self.assertEqual(relation.mode(*after), TemporalMode.K_BEFORE if state is not PairState.FREE else TemporalMode.FREE)
                self.assertTrue(relation.allows(*after))

        self.assertEqual(fixtures[PairState.I_OUTER].mode(*i_contains_k), TemporalMode.K_NESTED)
        self.assertEqual(fixtures[PairState.K_OUTER].mode(*k_contains_i), TemporalMode.I_NESTED)
        self.assertFalse(fixtures[PairState.I_OUTER].allows(*k_contains_i))
        self.assertFalse(fixtures[PairState.K_OUTER].allows(*i_contains_k))
        self.assertFalse(fixtures[PairState.SEPARATE].allows(*i_contains_k))
        self.assertFalse(fixtures[PairState.I_OUTER].allows(*equal_entry))

    def test_cache_translation_and_order_symmetry(self):
        first = self.kernel.relation(_placement(0, x=4, y=0), _placement(1, x=0, y=0))
        after_first = self.kernel.cache_info()
        translated = self.kernel.relation(_placement(0, x=9, y=4), _placement(1, x=5, y=4))
        after_translation = self.kernel.cache_info()
        reversed_relation = self.kernel.relation(_placement(1), _placement(0, x=4))
        after_reversed = self.kernel.cache_info()

        self.assertEqual(first, translated)
        self.assertEqual(after_translation.hits, after_first.hits + 1)
        self.assertEqual(reversed_relation.state, PairState.K_OUTER)
        self.assertEqual((reversed_relation.g_i_k, reversed_relation.g_k_i), (False, True))
        self.assertEqual(after_reversed.hits, after_translation.hits + 1)

    def test_cache_bound(self):
        for offset in range(12):
            self.kernel.relation(_placement(0), _placement(1, x=offset))
        info = self.kernel.cache_info()
        self.assertEqual(info.maxsize, 4)
        self.assertLessEqual(info.currsize, info.maxsize)
        misses = info.misses
        self.kernel.relation(_placement(0), _placement(1, x=0))
        self.assertEqual(self.kernel.cache_info().misses, misses + 1)


if __name__ == "__main__":
    unittest.main()
