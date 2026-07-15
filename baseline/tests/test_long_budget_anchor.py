from __future__ import annotations

import copy
import unittest

from solver.budget import Budget
from solver.construct import ConstructionSeed, ConstructorConfig, construct_complete
from solver.entry import OptionalPhaseResult, solve
from solver.geometry import GeometryKernel
from solver.instance import parse_instance
from solver.runtime import (
    LONG_BUDGET_ANCHOR_CONSTRUCTOR_CANDIDATES,
    constructor_candidate_limit,
    lns_iteration_limit,
)
from solver.serialize import serialize
from solver.state import Placement, SolutionSnapshot, compute_objective
from solver.runtime import RunTrace
from tests.helpers import block, checker, instance


class FakeClock:
    def __init__(self) -> None:
        self.value = 0.0

    def __call__(self) -> float:
        return self.value


class LongBudgetAnchorTests(unittest.TestCase):
    def test_anchor_window_is_absolute_and_matches_short_budget(self):
        clock = FakeClock()
        short = Budget.start(60.0, clock=clock)
        long = Budget.start(180.0, clock=clock)
        clock.value = 7.0

        short_anchor = short.anchor(60.0)
        long_anchor = long.anchor(60.0)

        self.assertEqual(60.0, short_anchor.limit)
        self.assertEqual(short_anchor.limit, long_anchor.limit)
        self.assertEqual(short_anchor.remaining(), long_anchor.remaining())
        self.assertEqual(short_anchor.reserve, long_anchor.reserve)

    def test_official_anchor_keeps_constructor_quota_but_lns_is_deadline_led(self):
        self.assertEqual(
            LONG_BUDGET_ANCHOR_CONSTRUCTOR_CANDIDATES,
            constructor_candidate_limit(60.0),
        )
        self.assertIsNone(lns_iteration_limit(60.0))
        self.assertIsNone(lns_iteration_limit(180.0))

    def test_constructor_stops_at_candidate_quota_before_wall_clock_deadline(self):
        raw = instance([block()])
        parsed = parse_instance(raw)
        kernel = GeometryKernel.from_instance(parsed)

        result = construct_complete(
            parsed,
            kernel,
            ConstructionSeed(None, "slack_due", 20260710),
            Budget.start(5.0),
            ConstructorConfig(max_profiles=1, max_candidate_attempts=1),
        )

        self.assertTrue(result.complete)
        self.assertEqual(1, result.metrics.candidates_attempted)
        self.assertTrue(result.metrics.candidate_cap_exhausted)
        self.assertFalse(result.metrics.timebox_exhausted)

    def test_sixty_and_one_eighty_share_initial_anchor_schedule(self):
        raw = instance([block()])

        def run(limit):
            events = []

            def loader():
                def phase(instance_value, snapshot, phase_budget):
                    del instance_value
                    events.append(
                        ("phase", phase_budget.limit, snapshot.placements)
                    )

                    def lns_runner(
                        initial, store, raw_value, checker_value, lns_budget
                    ):
                        del store, raw_value, checker_value
                        events.append(
                            ("lns", lns_budget.limit, initial.placements)
                        )

                    return OptionalPhaseResult(lns_runner=lns_runner)

                return phase

            solve(raw, limit, checker, optional_phase_loader=loader)
            return events

        short_events = run(60.0)
        long_events = run(180.0)

        self.assertEqual(short_events, long_events[: len(short_events)])
        self.assertEqual(["phase", "lns"], [item[0] for item in short_events])
        self.assertEqual(180.0, long_events[-1][1])

    def test_long_extension_preserves_validated_anchor_against_worse_candidate(self):
        raw = instance(
            [block(due=0), block(due=0)],
            bays=((20, 10),),
            weights=(1, 0, 0),
        )
        parsed = parse_instance(raw)
        kernel = GeometryKernel.from_instance(parsed)
        anchor = SolutionSnapshot(
            (
                Placement(0, 0, 0, 0, 0, 0, 2),
                Placement(1, 0, 0, 3, 0, 0, 2),
            )
        )
        worse = SolutionSnapshot(
            (
                Placement(0, 0, 0, 0, 0, 5, 7),
                Placement(1, 0, 0, 3, 0, 5, 7),
            )
        )
        worse = worse.with_objective(compute_objective(parsed, worse))
        calls: list[tuple[float, float]] = []

        def loader():
            def phase(instance_value, snapshot, phase_budget):
                del instance_value, snapshot

                def lns_runner(initial, store, raw_value, checker_value, lns_budget):
                    calls.append((lns_budget.limit, initial.objective.total))
                    if lns_budget.limit > 60.0:
                        operations = serialize(worse, kernel)
                        checked = checker_value(
                            copy.deepcopy(raw_value), copy.deepcopy(operations)
                        )
                        self.assertFalse(
                            store.install_if_valid(worse, operations, checked)
                        )

                self.assertEqual(60.0, phase_budget.limit)
                return OptionalPhaseResult(
                    candidates=(anchor,),
                    precedence_provider=kernel,
                    lns_runner=lns_runner,
                )

            return phase

        trace = RunTrace()
        operations = solve(
            raw,
            180.0,
            checker,
            optional_phase_loader=loader,
            trace=trace,
        )
        checked = checker(copy.deepcopy(raw), copy.deepcopy(operations))

        self.assertEqual([60.0, 180.0], [item[0] for item in calls])
        self.assertEqual(calls[0][1], calls[1][1])
        self.assertEqual(5, checked["stage"])
        self.assertTrue(checked["feasible"])
        self.assertEqual(4.0, checked["objective"])
        self.assertTrue(
            all(
                right < left
                for left, right in zip(
                    trace.validated_best, trace.validated_best[1:]
                )
            )
        )


if __name__ == "__main__":
    unittest.main()
