"""Checker-exact geometry and bounded-cache contracts for S0-02."""

from __future__ import annotations

import unittest

from solver.checker_adapter import official_check
from solver.geometry import GeomKernel, ShapeInfo
from solver.instance import ProblemInstance
from tests.fixtures import (
    TWO_SQUARE,
    UNIT_SQUARE,
    block,
    instance,
    interlock_pair_instance,
    placement,
    solution,
)


class GeometryKernelTests(unittest.TestCase):
    @staticmethod
    def shape(layers):
        parsed = ProblemInstance.parse(instance([block(layers=layers)]))
        return ShapeInfo.from_orientation(parsed.blocks[0].orientations[0])

    def test_contact_and_cache_identity(self):
        unit = self.shape((UNIT_SQUARE,))
        subtly_wide = self.shape(
            (((0.0, 0.0), (1.00004, 0.0), (1.00004, 1.0), (0.0, 1.0)),)
        )
        kernel = GeomKernel(cache_cap=8)

        self.assertNotEqual(unit.shape_key, subtly_wide.shape_key)
        self.assertTrue(kernel.union_disjoint(unit, unit, 1, 0))
        self.assertFalse(kernel.union_disjoint(unit, unit, 0, 0))
        self.assertFalse(kernel.union_disjoint(unit, unit, 0, 0))
        self.assertFalse(kernel.union_disjoint(unit, subtly_wide, 0, 0))

        stats = kernel.stats
        self.assertEqual(1, stats.cache_hits)
        self.assertEqual(2, stats.cache_misses)
        self.assertEqual(2, stats.exact_predicates)
        self.assertEqual(2, stats.cache_entries)

    def test_negative_local_minima_and_fit_ranges(self):
        negative = (
            (0.0, 0.0),
            (-2.0, 0.0),
            (-2.0, 1.0),
            (0.0, 1.0),
        )
        parsed = ProblemInstance.parse(
            instance([block(layers=(negative,))], bays=((3, 2),))
        )
        orientation = parsed.blocks[0].orientations[0]
        shape = ShapeInfo.from_orientation(orientation)

        self.assertEqual((-2.0, 0.0, 0.0, 1.0), shape.aabb)
        ranges = orientation.integer_position_ranges(parsed.bays[0])
        self.assertIsNotNone(ranges)
        assert ranges is not None
        self.assertEqual([2, 3], list(ranges[0]))
        self.assertEqual([0, 1], list(ranges[1]))

    def test_union_disjointness_uses_all_layers(self):
        separated_layers = self.shape(
            (
                UNIT_SQUARE,
                ((3.0, 0.0), (4.0, 0.0), (4.0, 1.0), (3.0, 1.0)),
            )
        )
        unit = self.shape((UNIT_SQUARE,))
        kernel = GeomKernel(cache_cap=8)

        self.assertFalse(kernel.union_disjoint(separated_layers, unit, 3, 0))
        self.assertTrue(kernel.union_disjoint(separated_layers, unit, 4, 0))

    def test_directional_obs_is_exact_and_asymmetric(self):
        parsed = ProblemInstance.parse(interlock_pair_instance())
        host = ShapeInfo.from_orientation(parsed.blocks[0].orientations[0])
        guest = ShapeInfo.from_orientation(parsed.blocks[1].orientations[0])
        kernel = GeomKernel(cache_cap=8)

        self.assertEqual((True, False), kernel.obs(host, guest, -2, 0))
        self.assertEqual((True, False), kernel.obs(host, guest, -2, 0))
        self.assertEqual(1, kernel.stats.cache_hits)
        self.assertEqual(1, kernel.stats.obs_predicates)

    def test_lru_eviction_is_bounded_and_deterministic(self):
        square = self.shape((TWO_SQUARE,))
        kernel = GeomKernel(cache_cap=2)

        self.assertFalse(kernel.union_disjoint(square, square, 0, 0))
        self.assertFalse(kernel.union_disjoint(square, square, 0, 0))
        self.assertFalse(kernel.union_disjoint(square, square, 0, 1))
        self.assertFalse(kernel.union_disjoint(square, square, 1, 0))

        stats = kernel.stats
        self.assertEqual(1, stats.cache_hits)
        self.assertEqual(3, stats.cache_misses)
        self.assertEqual(1, stats.cache_evictions)
        self.assertEqual(2, stats.cache_entries)

    def test_aabb_bypass_avoids_cache_and_shapely(self):
        unit = self.shape((UNIT_SQUARE,))
        kernel = GeomKernel(cache_cap=2)

        self.assertTrue(kernel.union_disjoint(unit, unit, 20, 20))
        stats = kernel.stats
        self.assertEqual(1, stats.aabb_bypasses)
        self.assertEqual(0, stats.cache_hits)
        self.assertEqual(0, stats.cache_misses)
        self.assertEqual(0, stats.exact_predicates)
        self.assertEqual(0, stats.cache_entries)

    def test_prepared_bypass_handles_disjoint_polygons_with_overlapping_aabbs(self):
        lower_left = self.shape((((0.0, 0.0), (1.0, 0.0), (0.0, 1.0)),))
        upper_right = self.shape(
            (((1.0, 1.0), (1.0, 0.6), (0.6, 1.0)),)
        )
        kernel = GeomKernel(cache_cap=2)

        self.assertTrue(kernel.union_disjoint(lower_left, upper_right, 1, 1))
        stats = kernel.stats
        self.assertEqual(0, stats.aabb_bypasses)
        self.assertEqual(1, stats.prepared_bypasses)
        self.assertEqual(1, stats.exact_predicates)

    def test_contact_and_overlap_match_official_checker(self):
        prob_info = instance([block(), block()], bays=((2, 1),))
        contact = solution(
            [
                placement(0, entry=0, exit=2),
                placement(1, entry=0, exit=2, x=1),
            ]
        )
        overlap = solution(
            [
                placement(0, entry=0, exit=2),
                placement(1, entry=0, exit=2),
            ]
        )

        contact_result = official_check(prob_info, contact)
        overlap_result = official_check(prob_info, overlap)
        self.assertTrue(contact_result.feasible, contact_result.violations)
        self.assertEqual(5, contact_result.stage)
        self.assertFalse(overlap_result.feasible)
        self.assertEqual(2, overlap_result.stage)


if __name__ == "__main__":
    unittest.main()
