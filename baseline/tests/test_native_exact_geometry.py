from __future__ import annotations

import random
import unittest

from solver.geometry import GeometryKernel, PairState
from solver.instance import parse_instance
from solver.native_repair import (
    NativeRepairAdapter,
    load_native_module,
    pack_static_problem,
)
from solver.state import Placement
from tests.helpers import block, instance, load_example, rectangle


SEED = 20260710
SAMPLES = 2_000
EXACT_CACHE_CAP = 2**18
NATIVE = load_native_module()


def _row(placement: Placement) -> tuple[int, ...]:
    return (
        placement.block_id,
        placement.bay_id,
        placement.orient_idx,
        placement.x,
        placement.y,
        placement.entry,
        placement.exit,
    )


def _fixture() -> dict:
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
        ],
        bays=((12, 12),),
    )


@unittest.skipUnless(NATIVE is not None, "local native extension is not configured")
class NativeExactGeometryTests(unittest.TestCase):
    def setUp(self):
        self.raw = _fixture()
        self.parsed = parse_instance(self.raw)
        self.kernel = GeometryKernel.from_instance(self.parsed)
        self.adapter = NativeRepairAdapter(self.parsed, self.kernel)

    @staticmethod
    def placement(block_id: int, *, x: int = 0, y: int = 0) -> Placement:
        return Placement(block_id, 0, 0, x, y, 0, 2)

    def test_boundary_contact_same_layer_overlap_and_outer_directions(self):
        boundary = self.adapter.exact_verdict(
            self.placement(0), self.placement(2, x=2)
        )
        overlap = self.adapter.exact_verdict(
            self.placement(0), self.placement(2)
        )
        i_outer = self.adapter.exact_verdict(
            self.placement(0, x=4), self.placement(1)
        )
        k_outer = self.adapter.exact_verdict(
            self.placement(1), self.placement(0, x=4)
        )

        self.assertEqual("FREE", boundary[0])
        self.assertEqual("BLOCKED", overlap[0])
        self.assertEqual(PairState.I_OUTER, self.kernel.relation(
            self.placement(0, x=4), self.placement(1)
        ).state)
        self.assertEqual("BLOCKED", i_outer[0])
        self.assertEqual(PairState.K_OUTER, self.kernel.relation(
            self.placement(1), self.placement(0, x=4)
        ).state)
        self.assertEqual("BLOCKED", k_outer[0])

    def test_exact_cache_uses_python_canonical_relative_key(self):
        first = self.adapter.exact_verdict(
            self.placement(0, x=4), self.placement(1)
        )
        translated = self.adapter.exact_verdict(
            self.placement(0, x=9, y=4), self.placement(1, x=5, y=4)
        )
        reversed_pair = self.adapter.exact_verdict(
            self.placement(1), self.placement(0, x=4)
        )
        self.assertFalse(first[1])
        self.assertTrue(translated[1])
        self.assertTrue(reversed_pair[1])
        self.assertEqual(first[0], translated[0])
        self.assertEqual(first[0], reversed_pair[0])

    def test_recent_exact_cache_hit_survives_single_overflow(self):
        sentinel = (self.placement(0), self.placement(2))
        first = self.adapter.exact_verdict(*sentinel)
        self.assertFalse(first[1])

        for dx in range(1, EXACT_CACHE_CAP):
            self.adapter.exact_verdict(
                self.placement(0), self.placement(2, x=dx)
            )

        recent = self.adapter.exact_verdict(*sentinel)
        self.assertTrue(recent[1])
        overflow = self.adapter.exact_verdict(
            self.placement(0), self.placement(2, x=EXACT_CACHE_CAP)
        )
        self.assertFalse(overflow[1])
        after_overflow = self.adapter.exact_verdict(*sentinel)
        self.assertTrue(after_overflow[1])
        self.assertEqual(first[0], after_overflow[0])

    def test_wkb_parse_failure_returns_error_not_free(self):
        payload = pack_static_problem(self.parsed, self.kernel)
        payload["blocks"][0]["orientations"][0]["layers_wkb"][0] = b"not-wkb"
        problem = NATIVE.StaticProblem(payload)
        verdict, cache_hit, _ = problem.exact_verdict(
            _row(self.placement(0)), _row(self.placement(2))
        )
        self.assertEqual("ERROR", verdict)
        self.assertFalse(cache_hit)

    def test_native_free_matches_python_on_2000_deterministic_pairs(self):
        raw = load_example()
        parsed = parse_instance(raw)
        kernel = GeometryKernel.from_instance(parsed)
        adapter = NativeRepairAdapter(parsed, kernel)
        rng = random.Random(SEED)
        fitting_by_bay: dict[int, list[tuple[int, int, object]]] = {}
        for parsed_block in parsed.blocks:
            for bay_id, orient_idx, reference_range in parsed_block.fitting_options:
                fitting_by_bay.setdefault(bay_id, []).append(
                    (parsed_block.index, orient_idx, reference_range)
                )
        eligible_bays = [
            bay_id
            for bay_id, options in fitting_by_bay.items()
            if len({item[0] for item in options}) >= 2
        ]
        checked = 0
        while checked < SAMPLES:
            bay_id = rng.choice(eligible_bays)
            left = rng.choice(fitting_by_bay[bay_id])
            right = rng.choice(fitting_by_bay[bay_id])
            if left[0] == right[0]:
                continue
            mover = Placement(
                left[0], bay_id, left[1],
                rng.randint(left[2].x.lower, left[2].x.upper),
                rng.randint(left[2].y.lower, left[2].y.upper), 0, 2,
            )
            stationary = Placement(
                right[0], bay_id, right[1],
                rng.randint(right[2].x.lower, right[2].x.upper),
                rng.randint(right[2].y.lower, right[2].y.upper), 0, 2,
            )
            expected = (
                kernel.relation(mover, stationary).state is PairState.FREE
            )
            verdict, _, _ = adapter.exact_verdict(mover, stationary)
            fixture = {
                "sample": checked,
                "mover": _row(mover),
                "stationary": _row(stationary),
                "expected": expected,
                "native": verdict,
            }
            self.assertNotEqual("ERROR", verdict, fixture)
            self.assertEqual(expected, verdict == "FREE", fixture)
            checked += 1
        self.assertEqual(SAMPLES, checked)


if __name__ == "__main__":
    unittest.main()
