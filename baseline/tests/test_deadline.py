from __future__ import annotations

import math
import unittest

from solver.budget import Budget
from solver.construct import _Counters, _stop_for_safe_tail
from solver.runtime import constructor_profile_limit, phase_name


class FakeClock:
    def __init__(self, value: float = 0.0):
        self.value = value

    def __call__(self) -> float:
        return self.value


class DeadlineContractTests(unittest.TestCase):
    def test_phase_ladder_boundaries(self):
        expected = {
            1.9: ("safe", 0),
            2.0: ("short", 1),
            11.999: ("short", 1),
            12.0: ("medium", 4),
            59.999: ("medium", 4),
            60.0: ("long", 6),
        }
        for limit, result in expected.items():
            with self.subTest(limit=limit):
                self.assertEqual(result, (phase_name(limit), constructor_profile_limit(limit)))

    def test_checker_p95_and_reserve_formula(self):
        clock = FakeClock()
        budget = Budget.start(60.0, clock=clock)
        for duration in (0.1, 0.2, 0.3, 0.4, 1.0):
            budget.record_checker_duration(duration)
        self.assertEqual(1.0, budget.checker_p95)
        self.assertEqual(min(60.0, 15.0, max(3.0, 2.1)), budget.reserve)

    def test_predicted_checker_and_margin_start_guard(self):
        clock = FakeClock()
        budget = Budget.start(10.0, clock=clock)
        budget.record_checker_duration(0.5)
        clock.value = budget.limit - budget.reserve - 0.61
        self.assertTrue(budget.can_start(0.5, margin=0.1))
        clock.value += 0.02
        self.assertFalse(budget.can_start(0.5, margin=0.1))

    def test_planned_constructor_timebox_is_distinct_from_actual_deadline(self):
        clock = FakeClock()
        budget = Budget.start(1.0, clock=clock)
        counters = _Counters()
        clock.value = 0.95
        self.assertTrue(_stop_for_safe_tail(budget, counters, 0.1))
        self.assertTrue(counters.timebox_exhausted)
        self.assertFalse(counters.deadline_hit)
        clock.value = 1.0
        expired = _Counters()
        self.assertTrue(_stop_for_safe_tail(budget, expired, 0.1))
        self.assertTrue(expired.deadline_hit)

    def test_negative_tiny_large_and_nonfinite_limits(self):
        for limit in (-1.0, 1e-12, 1e9):
            budget = Budget.start(limit, clock=FakeClock())
            self.assertEqual(max(0.0, limit), budget.limit)
        for limit in (math.inf, math.nan, True):
            with self.assertRaises(ValueError):
                Budget.start(limit, clock=FakeClock())


if __name__ == "__main__":
    unittest.main()
