"""S2 fixed-layout retiming orchestration and incumbent-safety tests."""

from __future__ import annotations

import unittest

from solver.budget import Budget
from solver.checker_adapter import official_check
from solver.exact import ExactResult, RetimeRequest
from solver.incumbent import VerifiedIncumbent
from solver.instance import ProblemInstance
from solver.retime import build_conflict_pairs, retime_sweep
from solver.state import Placement, SolutionState
from tests.fixtures import block, instance


def _retimed_result(
    backend: str,
    request: RetimeRequest,
    *,
    corrupt: bool = False,
    first_solution_s: float = 0.05,
    solve_s: float = 0.10,
) -> ExactResult:
    cursor = min(dict(request.releases).values(), default=0)
    dwells = dict(request.dwells)
    releases = dict(request.releases)
    dues = dict(request.dues)
    rows = []
    for block_id in request.block_ids:
        entry = max(cursor, releases[block_id])
        exit_time = entry + dwells[block_id]
        rows.append((block_id, entry, exit_time))
        cursor = exit_time
    if corrupt and rows:
        block_id, entry, exit_time = rows[0]
        rows[0] = (block_id, entry, exit_time + 1)
    objective = sum(
        max(0, exit_time - dues[block_id])
        for block_id, _entry, exit_time in rows
    )
    return ExactResult(
        backend=backend,
        status="optimal",
        solution=tuple(rows),
        objective=float(objective),
        bound=float(objective),
        build_s=0.01,
        solve_s=solve_s,
        first_solution_s=first_solution_s,
    )


def _current_result(backend: str, request: RetimeRequest) -> ExactResult:
    entries = dict(request.current_entries)
    dwells = dict(request.dwells)
    dues = dict(request.dues)
    rows = tuple(
        (block_id, entries[block_id], entries[block_id] + dwells[block_id])
        for block_id in request.block_ids
    )
    objective = sum(
        max(0, exit_time - dues[block_id])
        for block_id, _entry, exit_time in rows
    )
    return ExactResult(
        backend=backend,
        status="optimal",
        solution=rows,
        objective=float(objective),
        bound=float(objective),
        build_s=0.0,
        solve_s=0.0,
        first_solution_s=0.0,
    )


class RetimeTests(unittest.TestCase):
    def test_never_worse_and_fixed_pilot(self):
        prob_info = instance(
            [
                block(due=1, processing=1, preferences=(100, 0, 0)),
                block(due=2, processing=1, preferences=(100, 0, 0)),
                block(due=1, processing=1, preferences=(0, 100, 0)),
                block(due=2, processing=1, preferences=(0, 100, 0)),
                block(due=1, processing=1, preferences=(0, 0, 100)),
                block(due=2, processing=1, preferences=(0, 0, 100)),
            ],
            bays=((10, 10), (10, 10), (10, 10)),
            weights={"w1": 1.0, "w2": 0.0, "w3": 0.0},
        )
        parsed = ProblemInstance.parse(prob_info)
        state = SolutionState(parsed)
        for block_id, bay_id, entry in (
            (0, 0, 10),
            (1, 0, 11),
            (2, 1, 6),
            (3, 1, 7),
            (4, 2, 3),
            (5, 2, 4),
        ):
            state.place(
                Placement(block_id, bay_id, 0, 0, 0, entry, entry + 1)
            )
        incumbent = VerifiedIncumbent(parsed)
        incumbent.register_initial(state)
        original_solution = incumbent.solution
        original_placements = tuple(state.placements.values())
        self.assertEqual(((0, 1),), build_conflict_pairs(state, 0))

        pilot_calls: list[tuple[str, tuple[int, ...], float]] = []

        def gurobi(request, timebox):
            pilot_calls.append(("gurobi", request.block_ids, timebox))
            return _retimed_result(
                "gurobi", request, first_solution_s=0.10, solve_s=0.20
            )

        def cpsat(request, timebox):
            pilot_calls.append(("cpsat", request.block_ids, timebox))
            return _retimed_result(
                "cpsat", request, first_solution_s=0.05, solve_s=0.10
            )

        improved = retime_sweep(
            state,
            incumbent,
            {"gurobi": gurobi, "cpsat": cpsat},
            available_backends=("gurobi", "cpsat"),
            budget=Budget(100, reserve=0),
            first_sweep_budget=20,
        )
        self.assertEqual("cpsat", improved.pilot.backend)
        self.assertEqual(4.0, improved.pilot.total_budget)
        self.assertEqual(1.0, improved.pilot.call_timebox)
        self.assertEqual(4, len(improved.pilot.trials))
        self.assertEqual(
            {(0, 1), (2, 3)},
            {trial.block_ids for trial in improved.pilot.trials},
        )
        self.assertNotIn((4, 5), {trial.block_ids for trial in improved.pilot.trials})
        self.assertTrue(all(attempt.backend == "cpsat" for attempt in improved.attempts))
        self.assertEqual(2, sum(name == "gurobi" for name, _blocks, _box in pilot_calls))
        self.assertEqual(5, sum(name == "cpsat" for name, _blocks, _box in pilot_calls))
        self.assertEqual(3, improved.accepted_bays)
        self.assertLess(improved.state.z1, state.z1)
        self.assertEqual(original_placements, tuple(state.placements.values()))
        self.assertIsNot(original_solution, incumbent.solution)
        self.assertGreater(incumbent.verification_count, 1)
        checked = official_check(prob_info, incumbent.solution)
        self.assertTrue(checked.feasible, checked.violations)
        self.assertEqual(improved.state.z1, checked.obj1)

        corrupt_incumbent = VerifiedIncumbent(parsed)
        corrupt_incumbent.register_initial(state)
        preserved = corrupt_incumbent.solution

        def corrupt(request, _timebox):
            return _retimed_result("gurobi", request, corrupt=True)

        rejected = retime_sweep(
            state,
            corrupt_incumbent,
            {"gurobi": corrupt},
            available_backends=("gurobi",),
            budget=Budget(100, reserve=0),
            first_sweep_budget=0,
        )
        self.assertEqual("gurobi", rejected.pilot.backend)
        self.assertEqual((), rejected.pilot.trials)
        self.assertEqual(0, rejected.accepted_bays)
        self.assertIs(preserved, corrupt_incumbent.solution)
        self.assertEqual(original_placements, tuple(rejected.state.placements.values()))
        self.assertTrue(official_check(prob_info, corrupt_incumbent.solution).feasible)

        unchanged_incumbent = VerifiedIncumbent(parsed)
        unchanged_incumbent.register_initial(state)
        unchanged_solution = unchanged_incumbent.solution
        non_improving = retime_sweep(
            state,
            unchanged_incumbent,
            {"cpsat": lambda request, _timebox: _current_result("cpsat", request)},
            available_backends=("cpsat",),
            budget=Budget(100, reserve=0),
            first_sweep_budget=0,
        )
        self.assertEqual("cpsat", non_improving.pilot.backend)
        self.assertEqual(0, non_improving.accepted_bays)
        self.assertIs(unchanged_solution, unchanged_incumbent.solution)


if __name__ == "__main__":
    unittest.main()
