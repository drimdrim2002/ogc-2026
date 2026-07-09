import inspect
import json
import pathlib
import sys
import unittest
from contextlib import redirect_stdout
from io import StringIO
from unittest.mock import patch


BASELINE_DIR = pathlib.Path(__file__).resolve().parents[1]
ROOT_DIR = BASELINE_DIR.parent
if str(BASELINE_DIR) not in sys.path:
    sys.path.insert(0, str(BASELINE_DIR))


def _load_example_instance():
    instance_path = ROOT_DIR / "alg_tester/example/example_B2_b10.json"
    return json.loads(instance_path.read_text())


def _assert_stage5_feasible(testcase, prob_info, solution):
    from utils import check_feasibility

    result = check_feasibility(prob_info, solution)
    testcase.assertTrue(result["feasible"], result["violations"][:5])
    testcase.assertEqual(5, result["stage"])


def _assignments_from_operations(operations):
    assignments = {}
    for time_key, ops in operations.items():
        time_int = int(time_key)
        for op in ops:
            block_id = int(op["block_id"])
            assignment = assignments.setdefault(block_id, {"block_id": block_id})
            if op["type"] == "ENTRY":
                assignment.update(
                    {
                        "bay_id": int(op["bay_id"]),
                        "x": int(op["x"]),
                        "y": int(op["y"]),
                        "orient_idx": int(op["orient_idx"]),
                        "entry_time": time_int,
                    }
                )
            elif op["type"] == "EXIT":
                assignment["exit_time"] = time_int
    return assignments


def _selector_prob_info(n_blocks, n_bays, slack, w1=1, w3=1):
    processing_time = 5
    return {
        "bays": [{"width": 10, "height": 10} for _ in range(n_bays)],
        "blocks": [
            {
                "release_time": 0,
                "due_date": processing_time + slack,
                "processing_time": processing_time,
                "bay_preferences": [1 for _ in range(n_bays)],
            }
            for _ in range(n_blocks)
        ],
        "weights": {"w1": w1, "w2": 1, "w3": w3},
    }


def _lns_assignment(
    block_id,
    bay_id=0,
    x=0,
    y=0,
    orient_idx=0,
    entry_time=0,
    exit_time=1,
):
    return {
        "block_id": block_id,
        "bay_id": bay_id,
        "x": x,
        "y": y,
        "orient_idx": orient_idx,
        "entry_time": entry_time,
        "exit_time": exit_time,
    }


def _placement_mode_prob_info(widths):
    blocks = []
    for block_id, width in enumerate(widths):
        blocks.append(
            {
                "release_time": 0,
                "due_date": 10,
                "processing_time": 1,
                "workload": 1,
                "bay_preferences": [1],
                "shape": [
                    {
                        "orientation": 0,
                        "layers": [
                            [
                                [0, 0],
                                [width, 0],
                                [width, 1],
                                [0, 1],
                            ],
                        ],
                    },
                ],
            }
        )
    return {
        "name": "placement_mode_fixture",
        "bays": [{"width": 2, "height": 2}],
        "blocks": blocks,
        "weights": {"w1": 1, "w2": 1, "w3": 1},
    }


def _repair_candidate_prob_info():
    blocks = []
    for block_id, release_time in enumerate((0, 1)):
        blocks.append(
            {
                "release_time": release_time,
                "due_date": 10,
                "processing_time": 1,
                "workload": 1,
                "bay_preferences": [1],
                "shape": [
                    {
                        "orientation": 0,
                        "layers": [
                            [
                                [0, 0],
                                [1, 0],
                                [1, 1],
                                [0, 1],
                            ],
                        ],
                    },
                ],
            }
        )
    return {
        "name": "repair_candidate_fixture",
        "bays": [{"width": 4, "height": 2}],
        "blocks": blocks,
        "weights": {"w1": 1, "w2": 1, "w3": 1},
    }


def _repair_candidate_incumbent():
    return {
        0: _lns_assignment(0, bay_id=0, x=0, y=0, entry_time=0, exit_time=1),
        1: _lns_assignment(1, bay_id=0, x=1, y=0, entry_time=1, exit_time=2),
    }


class SubmissionEntryPointSafetyTests(unittest.TestCase):
    def test_algorithm_public_signature_stays_stable_and_default_lns_small(self):
        import myalgorithm

        signature = inspect.signature(myalgorithm.algorithm)

        self.assertEqual(["prob_info", "timelimit"], list(signature.parameters))
        self.assertEqual(60, signature.parameters["timelimit"].default)
        self.assertEqual("small", myalgorithm._SUBMISSION_LNS_MODE)

    def test_algorithm_zero_timelimit_returns_feasible_fallback(self):
        import myalgorithm

        prob_info = _load_example_instance()

        with redirect_stdout(StringIO()):
            solution = myalgorithm.algorithm(prob_info, timelimit=0)

        _assert_stage5_feasible(self, prob_info, solution)

    def test_algorithm_catches_greedy_exception_returns_feasible_fallback(self):
        import myalgorithm

        prob_info = _load_example_instance()

        with patch("baseline_greedy.greedyalgorithm", side_effect=RuntimeError("boom")):
            with redirect_stdout(StringIO()):
                solution = myalgorithm.algorithm(prob_info, timelimit=1.0)

        _assert_stage5_feasible(self, prob_info, solution)

    def test_algorithm_delegates_with_accepted_block_order_mode(self):
        import myalgorithm

        prob_info = _load_example_instance()
        candidate = {"operations": {}}

        with patch("baseline_greedy.greedyalgorithm", return_value=candidate) as greedy:
            with patch("myalgorithm._is_feasible_solution", return_value=True):
                solution = myalgorithm.algorithm(prob_info, timelimit=1.0)

        self.assertIs(candidate, solution)
        greedy.assert_called_once_with(
            prob_info,
            1.0,
            block_order_mode="slack",
            lns_mode="small",
        )

    def test_algorithm_delegates_with_adaptive_block_order_mode(self):
        import myalgorithm

        prob_info = _selector_prob_info(n_blocks=100, n_bays=2, slack=0)
        candidate = {"operations": {}}

        with patch("baseline_greedy.greedyalgorithm", return_value=candidate) as greedy:
            with patch("myalgorithm._is_feasible_solution", return_value=True):
                solution = myalgorithm.algorithm(prob_info, timelimit=1.0)

        self.assertIs(candidate, solution)
        greedy.assert_called_once_with(
            prob_info,
            1.0,
            block_order_mode="preference_pressure",
            lns_mode="small",
        )

    def test_adaptive_block_order_selector_covers_measured_branches(self):
        import myalgorithm

        cases = [
            (_selector_prob_info(100, 2, 0), "preference_pressure"),
            (_selector_prob_info(150, 2, 0), "latest_safe_entry"),
            (_selector_prob_info(300, 4, 1), "release_edd"),
            (_selector_prob_info(200, 3, 10, w3=600), "release_edd"),
            (_selector_prob_info(250, 3, 1, w1=10001), "release_edd"),
            (_selector_prob_info(250, 3, 2, w1=999), "latest_safe_entry"),
            (_selector_prob_info(50, 3, 10), "slack"),
        ]

        for prob_info, expected_mode in cases:
            with self.subTest(expected_mode=expected_mode, n_blocks=len(prob_info["blocks"])):
                self.assertEqual(
                    expected_mode,
                    myalgorithm._select_submission_block_order_mode(prob_info),
                )

        original_mode = myalgorithm._SUBMISSION_BLOCK_ORDER_MODE
        try:
            myalgorithm._SUBMISSION_BLOCK_ORDER_MODE = "release_edd"
            self.assertEqual(
                "release_edd",
                myalgorithm._select_submission_block_order_mode(cases[0][0]),
            )
        finally:
            myalgorithm._SUBMISSION_BLOCK_ORDER_MODE = original_mode

    def test_benchmark_reports_adaptive_effective_block_order_mode(self):
        import benchmark_instances
        from baseline_greedy import _serial_fallback_solution

        def fake_algorithm(received_prob_info, timelimit):
            return _serial_fallback_solution(received_prob_info)

        out = StringIO()
        with patch("myalgorithm.algorithm", side_effect=fake_algorithm):
            with redirect_stdout(out):
                status = benchmark_instances.main([
                    "--root",
                    str(ROOT_DIR),
                    "--set-name",
                    "dev-10",
                    "--solver",
                    "myalgorithm",
                    "--limit",
                    "1",
                    "--timelimit",
                    "0.001",
                ])

        self.assertEqual(0, status)
        rows = json.loads(out.getvalue())
        self.assertEqual("myalgorithm", rows[0]["solver"])
        self.assertEqual("preference_pressure", rows[0]["block_order_mode"])
        self.assertEqual("small", rows[0]["lns_mode"])

    def test_algorithm_replaces_empty_operations_with_verified_fallback(self):
        import myalgorithm

        prob_info = _load_example_instance()

        with patch("baseline_greedy.greedyalgorithm", return_value={"operations": {}}):
            with redirect_stdout(StringIO()):
                solution = myalgorithm.algorithm(prob_info, timelimit=1.0)

        _assert_stage5_feasible(self, prob_info, solution)

    def test_algorithm_replaces_malformed_output_with_verified_fallback(self):
        import myalgorithm
        from baseline_greedy import _serial_fallback_solution

        prob_info = _load_example_instance()
        expected_fallback = _serial_fallback_solution(prob_info, verify=True)

        with patch("baseline_greedy.greedyalgorithm", return_value="not-a-solution") as greedy:
            with redirect_stdout(StringIO()):
                solution = myalgorithm.algorithm(prob_info, timelimit=1.0)

        greedy.assert_called_once_with(
            prob_info,
            1.0,
            block_order_mode="slack",
            lns_mode="small",
        )
        self.assertEqual(expected_fallback, solution)
        _assert_stage5_feasible(self, prob_info, solution)


class PartialFallbackSafetyTests(unittest.TestCase):
    def test_complete_with_serial_fallback_rejects_malformed_partial(self):
        from baseline_greedy import (
            _complete_with_serial_fallback,
            _serial_fallback_assignments,
        )

        prob_info = _load_example_instance()
        serial_assignments = _serial_fallback_assignments(prob_info)
        malformed_partial = {
            0: dict(serial_assignments[0], bay_id=len(prob_info["bays"]))
        }

        with redirect_stdout(StringIO()):
            solution = _complete_with_serial_fallback(prob_info, malformed_partial)

        recovered = _assignments_from_operations(solution["operations"])
        _assert_stage5_feasible(self, prob_info, solution)
        self.assertEqual(serial_assignments[0], recovered[0])


class LnsStateHelperTests(unittest.TestCase):
    def test_lns_state_copy_assignments_isolates_nested_assignment_dicts(self):
        from baseline_greedy import _copy_assignments

        assignments = {
            1: _lns_assignment(1, x=2, y=3, orient_idx=1, entry_time=5, exit_time=11),
        }
        snapshot = {block_id: dict(assignment) for block_id, assignment in assignments.items()}
        snapshot_json = json.dumps(assignments, sort_keys=True)

        copied = _copy_assignments(assignments)
        copied[1]["x"] = 99
        copied[2] = dict(copied[1], block_id=2)

        self.assertEqual(snapshot, assignments)
        self.assertEqual(snapshot_json, json.dumps(assignments, sort_keys=True))

    def test_lns_state_rebuilds_blocks_schedule_and_loads_from_assignments(self):
        from baseline_greedy import _rebuild_lns_state, _serial_fallback_assignments
        from utils import Bay

        prob_info = _load_example_instance()
        bays = [Bay.from_dict(data, idx) for idx, data in enumerate(prob_info["bays"])]
        serial_assignments = _serial_fallback_assignments(prob_info)
        assignments = {
            block_id: dict(serial_assignments[block_id])
            for block_id in (0, 1, 2)
        }

        bay_placed, bay_schedule, bay_loads = _rebuild_lns_state(
            prob_info["blocks"],
            bays,
            assignments,
        )

        for assignment in assignments.values():
            bay_id = assignment["bay_id"]
            block_id = assignment["block_id"]
            self.assertTrue(
                any(block.block_id == block_id for block in bay_placed[bay_id]),
                f"missing rebuilt block {block_id} in bay {bay_id}",
            )
            self.assertIn(
                (assignment["entry_time"], assignment["exit_time"]),
                bay_schedule[bay_id],
            )

        expected_loads = [0.0] * len(bays)
        for assignment in assignments.values():
            expected_loads[assignment["bay_id"]] += (
                prob_info["blocks"][assignment["block_id"]]["workload"]
            )
        self.assertEqual(expected_loads, bay_loads)

    def test_lns_state_solution_from_assignments_uses_build_operations(self):
        from baseline_greedy import _solution_from_assignments

        assignments = {
            0: _lns_assignment(0, exit_time=5),
            1: _lns_assignment(1, x=1, y=1, entry_time=5, exit_time=7),
        }
        operations = {"5": [{"type": "EXIT", "block_id": 0, "bay_id": 0}]}

        with patch("baseline_greedy._build_operations", return_value=operations) as build:
            solution = _solution_from_assignments(assignments)

        build.assert_called_once_with(list(assignments.values()))
        self.assertEqual({"operations": operations}, solution)


class LnsDestroySelectorTests(unittest.TestCase):
    def test_lns_destroy_size_is_bounded_percentage_with_small_floor(self):
        from baseline_greedy import _lns_destroy_size

        self.assertEqual(1, _lns_destroy_size(0))
        self.assertEqual(1, _lns_destroy_size(1))
        self.assertEqual(2, _lns_destroy_size(2))
        self.assertEqual(2, _lns_destroy_size(66))
        self.assertEqual(3, _lns_destroy_size(67))
        self.assertEqual(8, _lns_destroy_size(1000))

    def test_lns_destroy_worst_objective_blocks_are_bounded_deterministic_and_prioritize_cost(self):
        from baseline_greedy import (
            _lns_destroy_size,
            _lns_select_worst_objective_blocks,
        )

        blocks_data = [
            {
                "due_date": 100,
                "workload": 1,
                "bay_preferences": [10, 10],
            }
            for _ in range(67)
        ]
        blocks_data[2] = {
            "due_date": 100,
            "workload": 5,
            "bay_preferences": [20, 0],
        }
        blocks_data[3] = {
            "due_date": 30,
            "workload": 5,
            "bay_preferences": [10, 10],
        }
        blocks_data[4] = {
            "due_date": 30,
            "workload": 5,
            "bay_preferences": [10, 10],
        }
        blocks_data[5] = {
            "due_date": 30,
            "workload": 2,
            "bay_preferences": [10, 10],
        }
        assignments = {
            block_id: _lns_assignment(block_id, bay_id=0, exit_time=10)
            for block_id in range(len(blocks_data))
        }
        assignments[2] = _lns_assignment(2, bay_id=1, exit_time=40)
        assignments[3] = _lns_assignment(3, bay_id=0, exit_time=50)
        assignments[4] = _lns_assignment(4, bay_id=0, exit_time=50)
        assignments[5] = _lns_assignment(5, bay_id=0, exit_time=50)

        first = _lns_select_worst_objective_blocks(
            assignments,
            blocks_data,
            {"w1": 1, "w3": 1},
        )
        second = _lns_select_worst_objective_blocks(
            assignments,
            blocks_data,
            {"w1": 1, "w3": 1},
        )

        self.assertEqual(first, second)
        self.assertEqual(_lns_destroy_size(len(assignments)), len(first))
        self.assertEqual([3, 4, 2], first)
        self.assertEqual(len(first), len(set(first)))
        self.assertTrue(set(first).issubset(assignments))

    def test_lns_destroy_same_bay_time_window_uses_worst_seed_and_overlap_gap_score_order(self):
        from baseline_greedy import (
            _lns_destroy_size,
            _lns_select_same_bay_time_window,
        )

        blocks_data = [
            {
                "due_date": 1000,
                "workload": 1,
                "bay_preferences": [10, 10],
            }
            for _ in range(200)
        ]
        blocks_data[10] = {
            "due_date": 0,
            "workload": 3,
            "bay_preferences": [10, 10],
        }
        blocks_data[12] = {
            "due_date": 100,
            "workload": 3,
            "bay_preferences": [10, 10],
        }
        blocks_data[13] = {
            "due_date": 122,
            "workload": 3,
            "bay_preferences": [10, 10],
        }
        blocks_data[14] = {
            "due_date": 60,
            "workload": 3,
            "bay_preferences": [10, 10],
        }
        blocks_data[15] = {
            "due_date": 122,
            "workload": 3,
            "bay_preferences": [10, 10],
        }
        blocks_data[16] = {
            "due_date": 20,
            "workload": 3,
            "bay_preferences": [10, 10],
        }
        assignments = {
            block_id: _lns_assignment(block_id, bay_id=0, exit_time=10)
            for block_id in range(len(blocks_data))
        }
        assignments[10] = _lns_assignment(10, bay_id=1, entry_time=100, exit_time=130)
        assignments[11] = _lns_assignment(11, bay_id=1, entry_time=110, exit_time=125)
        assignments[12] = _lns_assignment(12, bay_id=1, entry_time=90, exit_time=105)
        assignments[13] = _lns_assignment(13, bay_id=1, entry_time=130, exit_time=140)
        assignments[14] = _lns_assignment(14, bay_id=1, entry_time=150, exit_time=160)
        assignments[15] = _lns_assignment(15, bay_id=1, entry_time=90, exit_time=100)
        assignments[16] = _lns_assignment(16, bay_id=0, entry_time=100, exit_time=140)

        selected = _lns_select_same_bay_time_window(assignments, blocks_data)

        self.assertEqual(_lns_destroy_size(len(assignments)), len(selected))
        self.assertEqual([10, 11, 12, 13, 15, 14], selected)
        self.assertEqual(len(selected), len(set(selected)))
        self.assertTrue(set(selected).issubset(assignments))

    def test_lns_destroy_selectors_reject_duplicate_or_unknown_assignment_ids(self):
        from baseline_greedy import (
            _lns_select_same_bay_time_window,
            _lns_select_worst_objective_blocks,
        )

        blocks_data = [
            {
                "due_date": 10,
                "workload": 1,
                "bay_preferences": [1],
            }
            for _ in range(3)
        ]
        duplicate_assignments = {
            0: _lns_assignment(0, exit_time=11),
            1: _lns_assignment(0, exit_time=12),
        }
        unknown_assignments = {
            0: _lns_assignment(0, exit_time=11),
            3: _lns_assignment(3, exit_time=12),
        }

        with self.assertRaises(ValueError):
            _lns_select_worst_objective_blocks(
                duplicate_assignments,
                blocks_data,
                {"w1": 1, "w3": 1},
            )
        with self.assertRaises(ValueError):
            _lns_select_worst_objective_blocks(
                unknown_assignments,
                blocks_data,
                {"w1": 1, "w3": 1},
            )
        with self.assertRaises(ValueError):
            _lns_select_same_bay_time_window(duplicate_assignments, blocks_data)
        with self.assertRaises(ValueError):
            _lns_select_same_bay_time_window(unknown_assignments, blocks_data)


class LnsPlacementModeTests(unittest.TestCase):
    def test_lns_repair_place_blocks_allow_force_false_raises_with_partial_assignments(self):
        import baseline_greedy
        from utils import Bay

        prob_info = _placement_mode_prob_info([1, 3])
        bays = [Bay.from_dict(data, idx) for idx, data in enumerate(prob_info["bays"])]
        bay_placed = [[] for _ in bays]
        bay_schedule = [[] for _ in bays]
        bay_loads = [0.0 for _ in bays]

        with self.assertRaises(baseline_greedy._LnsRepairFailed) as ctx:
            baseline_greedy._place_blocks(
                [0, 1],
                prob_info["blocks"],
                bays,
                bay_placed,
                bay_schedule,
                bay_loads,
                1.0,
                1.0,
                1.0,
                forced_ids=set(),
                allow_force=False,
            )

        partial = ctx.exception.assignments
        self.assertEqual({0}, set(partial))
        self.assertEqual(0, partial[0]["block_id"])
        self.assertEqual([0], [block.block_id for block in bay_placed[0]])

    def test_place_blocks_default_keeps_forced_fallback_when_no_candidate_fits(self):
        import baseline_greedy
        from utils import Bay

        prob_info = _placement_mode_prob_info([3])
        bays = [Bay.from_dict(data, idx) for idx, data in enumerate(prob_info["bays"])]
        bay_placed = [[] for _ in bays]
        bay_schedule = [[] for _ in bays]
        bay_loads = [0.0 for _ in bays]

        assignments = baseline_greedy._place_blocks(
            [0],
            prob_info["blocks"],
            bays,
            bay_placed,
            bay_schedule,
            bay_loads,
            1.0,
            1.0,
            1.0,
            forced_ids=set(),
        )

        self.assertEqual({0}, set(assignments))
        self.assertEqual(0, assignments[0]["block_id"])
        self.assertEqual([0], [block.block_id for block in bay_placed[0]])


class LnsLoopTests(unittest.TestCase):
    def _incumbent_result(self, objective=10.0):
        return {
            "feasible": True,
            "stage": 5,
            "violations": [],
            "objective": objective,
            "obj1": objective,
            "obj2": 0.0,
            "obj3": 0.0,
        }

    def test_lns_loop_accepts_only_feasible_objective_improvement(self):
        import baseline_greedy

        prob_info = _repair_candidate_prob_info()
        incumbent = _repair_candidate_incumbent()
        incumbent_snapshot = json.dumps(incumbent, sort_keys=True)
        improved = {
            block_id: dict(assignment)
            for block_id, assignment in incumbent.items()
        }
        improved[1] = dict(improved[1], entry_time=0, exit_time=1)
        better_result = self._incumbent_result(objective=8.0)

        with patch(
            "baseline_greedy._lns_select_worst_objective_blocks",
            return_value=[1],
        ):
            with patch(
                "baseline_greedy._lns_try_repair_candidate",
                return_value=(improved, better_result),
            ) as repair:
                assignments, result = baseline_greedy._lns_improve_assignments(
                    prob_info,
                    incumbent,
                    self._incumbent_result(),
                    "edd",
                    None,
                )

        self.assertEqual(improved, assignments)
        self.assertEqual(better_result, result)
        self.assertEqual(incumbent_snapshot, json.dumps(incumbent, sort_keys=True))
        repair.assert_called_once()

    def test_lns_loop_keeps_incumbent_for_infeasible_worse_or_equal_candidates(self):
        import baseline_greedy

        prob_info = _repair_candidate_prob_info()
        incumbent = _repair_candidate_incumbent()
        incumbent_result = self._incumbent_result()
        rejected = {
            block_id: dict(assignment)
            for block_id, assignment in incumbent.items()
        }
        rejected[1] = dict(rejected[1], entry_time=0, exit_time=1)
        cases = [
            (
                "infeasible",
                {
                    "feasible": False,
                    "stage": 4,
                    "violations": ["forced infeasible"],
                    "objective": 0.0,
                    "obj1": None,
                    "obj2": None,
                    "obj3": None,
                },
            ),
            ("worse", self._incumbent_result(objective=11.0)),
            ("equal", self._incumbent_result(objective=10.0)),
            ("epsilon_tie", self._incumbent_result(objective=9.9999995)),
        ]

        for name, candidate_result in cases:
            with self.subTest(name=name):
                with patch(
                    "baseline_greedy._lns_select_worst_objective_blocks",
                    return_value=[1],
                ):
                    with patch(
                        "baseline_greedy._lns_try_repair_candidate",
                        return_value=(rejected, candidate_result),
                    ):
                        assignments, result = baseline_greedy._lns_improve_assignments(
                            prob_info,
                            incumbent,
                            incumbent_result,
                            "edd",
                            None,
                        )

                self.assertEqual(incumbent, assignments)
                self.assertEqual(incumbent_result, result)

    def test_lns_loop_stops_at_numeric_iteration_cap(self):
        import baseline_greedy

        prob_info = _selector_prob_info(n_blocks=12, n_bays=1, slack=0)
        incumbent = {
            block_id: _lns_assignment(block_id)
            for block_id in range(len(prob_info["blocks"]))
        }

        with patch(
            "baseline_greedy._lns_select_worst_objective_blocks",
            return_value=[0],
        ):
            with patch(
                "baseline_greedy._lns_select_same_bay_time_window",
                return_value=[0],
            ):
                with patch(
                    "baseline_greedy._lns_try_repair_candidate",
                    return_value=None,
                ) as repair:
                    assignments, result = baseline_greedy._lns_improve_assignments(
                        prob_info,
                        incumbent,
                        self._incumbent_result(),
                        "edd",
                        None,
                    )

        self.assertEqual(3, repair.call_count)
        self.assertEqual(incumbent, assignments)
        self.assertEqual(self._incumbent_result(), result)

    def test_lns_loop_stops_when_deadline_reached_or_budget_floor_hit(self):
        import baseline_greedy

        prob_info = _repair_candidate_prob_info()
        incumbent = _repair_candidate_incumbent()

        for now in (100.0, 99.96):
            with self.subTest(now=now):
                with patch("baseline_greedy.time.time", return_value=now):
                    with patch(
                        "baseline_greedy._lns_try_repair_candidate",
                    ) as repair:
                        assignments, result = baseline_greedy._lns_improve_assignments(
                            prob_info,
                            incumbent,
                            self._incumbent_result(),
                            "edd",
                            100.0,
                        )

                repair.assert_not_called()
                self.assertEqual(incumbent, assignments)
                self.assertEqual(self._incumbent_result(), result)

    def test_lns_loop_records_diagnostic_counts(self):
        import baseline_greedy

        prob_info = _selector_prob_info(n_blocks=8, n_bays=1, slack=0)
        incumbent = {
            block_id: _lns_assignment(block_id)
            for block_id in range(len(prob_info["blocks"]))
        }
        improved = {
            block_id: _lns_assignment(block_id)
            for block_id in range(len(prob_info["blocks"]))
        }
        baseline_greedy._LAST_LNS_STATS = {
            "lns_entered": False,
            "lns_available_time_at_entry": None,
            "lns_max_iterations": 0,
            "lns_attempted_iterations": 0,
            "destroy_operator_attempts": {},
            "repair_candidate_attempted_count": 0,
            "repair_candidate_returned_none_count": 0,
            "feasible_candidate_count": 0,
            "accepted_candidate_count": 0,
            "best_objective_before": None,
            "best_objective_after": None,
            "best_objective_delta": None,
            "best_objective_delta_pct": None,
            "no_improvement_reason": None,
        }

        with patch(
            "baseline_greedy._lns_select_worst_objective_blocks",
            return_value=[0, 1],
        ):
            with patch(
                "baseline_greedy._lns_select_same_bay_time_window",
                return_value=[2, 3],
            ):
                with patch(
                    "baseline_greedy._lns_try_repair_candidate",
                    side_effect=[
                        (improved, self._incumbent_result(objective=9.0)),
                        None,
                    ],
                ):
                    assignments, result = baseline_greedy._lns_improve_assignments(
                        prob_info,
                        incumbent,
                        self._incumbent_result(objective=10.0),
                        "edd",
                        None,
                    )

        stats = baseline_greedy.get_last_lns_stats()
        self.assertEqual(improved, assignments)
        self.assertEqual(self._incumbent_result(objective=9.0), result)
        self.assertTrue(stats["lns_entered"])
        self.assertEqual(2, stats["lns_max_iterations"])
        self.assertEqual(2, stats["lns_attempted_iterations"])
        self.assertEqual(
            {"worst_objective": 1, "same_bay_time_window": 1},
            stats["destroy_operator_attempts"],
        )
        self.assertEqual(2, stats["repair_candidate_attempted_count"])
        self.assertEqual(1, stats["repair_candidate_returned_none_count"])
        self.assertEqual(1, stats["feasible_candidate_count"])
        self.assertEqual(1, stats["accepted_candidate_count"])
        self.assertEqual(10.0, stats["best_objective_before"])
        self.assertEqual(9.0, stats["best_objective_after"])
        self.assertEqual(1.0, stats["best_objective_delta"])
        self.assertEqual(10.0, stats["best_objective_delta_pct"])
        self.assertIsNone(stats["no_improvement_reason"])


class GreedyLnsModeTests(unittest.TestCase):
    def test_greedyalgorithm_rejects_unknown_lns_mode(self):
        import baseline_greedy

        with self.assertRaises(ValueError):
            with redirect_stdout(StringIO()):
                baseline_greedy.greedyalgorithm(
                    _repair_candidate_prob_info(),
                    timelimit=1.0,
                    lns_mode="large",
                )

    def test_greedyalgorithm_lns_mode_off_does_not_call_lns_loop(self):
        import baseline_greedy

        prob_info = _repair_candidate_prob_info()

        with patch(
            "baseline_greedy._lns_improve_assignments",
            side_effect=AssertionError("off mode must not call LNS"),
            create=True,
        ):
            with redirect_stdout(StringIO()):
                solution = baseline_greedy.greedyalgorithm(
                    prob_info,
                    timelimit=1.0,
                    lns_mode="off",
                )

        _assert_stage5_feasible(self, prob_info, solution)

    def test_greedyalgorithm_small_lns_runs_after_feasible_incumbent(self):
        import baseline_greedy

        prob_info = _repair_candidate_prob_info()

        def keep_incumbent(
            received_prob_info,
            assignments,
            incumbent_result,
            block_order_mode,
            deadline,
        ):
            self.assertIs(received_prob_info, prob_info)
            self.assertTrue(incumbent_result["feasible"])
            self.assertEqual("edd", block_order_mode)
            self.assertIsNotNone(deadline)
            return assignments, incumbent_result

        with patch(
            "baseline_greedy._lns_improve_assignments",
            side_effect=keep_incumbent,
            create=True,
        ) as improve:
            with redirect_stdout(StringIO()):
                solution = baseline_greedy.greedyalgorithm(
                    prob_info,
                    timelimit=1.0,
                    lns_mode="small",
                )

        _assert_stage5_feasible(self, prob_info, solution)
        improve.assert_called_once()

    def test_greedyalgorithm_small_lns_skips_loop_when_incumbent_infeasible(self):
        import baseline_greedy

        prob_info = _repair_candidate_prob_info()
        incomplete_assignments = {
            0: _lns_assignment(0, entry_time=0, exit_time=1),
        }

        with patch("baseline_greedy._repair", return_value=incomplete_assignments):
            with patch(
                "baseline_greedy._lns_improve_assignments",
                side_effect=AssertionError("infeasible incumbent must skip LNS"),
                create=True,
            ):
                with redirect_stdout(StringIO()):
                    solution = baseline_greedy.greedyalgorithm(
                        prob_info,
                        timelimit=1.0,
                        lns_mode="small",
                    )

        _assert_stage5_feasible(self, prob_info, solution)

    def test_greedyalgorithm_tiny_timelimit_returns_stage5_fallback_with_lns_small(self):
        import baseline_greedy

        prob_info = _load_example_instance()

        with redirect_stdout(StringIO()):
            solution = baseline_greedy.greedyalgorithm(
                prob_info,
                timelimit=0,
                lns_mode="small",
            )

        _assert_stage5_feasible(self, prob_info, solution)


class LnsRepairCandidateTests(unittest.TestCase):
    def test_lns_repair_candidate_returns_feasible_checked_candidate(self):
        import baseline_greedy
        from utils import check_feasibility

        prob_info = _repair_candidate_prob_info()
        incumbent = _repair_candidate_incumbent()
        incumbent_snapshot = json.dumps(incumbent, sort_keys=True)

        with patch(
            "baseline_greedy._build_operations",
            wraps=baseline_greedy._build_operations,
        ) as build_operations:
            with patch("utils.check_feasibility", wraps=check_feasibility) as check:
                candidate = baseline_greedy._lns_try_repair_candidate(
                    prob_info,
                    incumbent,
                    [1],
                    "edd",
                    None,
                )

        self.assertIsNotNone(candidate)
        candidate_assignments, result = candidate
        self.assertTrue(result["feasible"], result["violations"])
        self.assertEqual(5, result["stage"])
        self.assertEqual(set(incumbent), set(candidate_assignments))
        self.assertEqual(incumbent_snapshot, json.dumps(incumbent, sort_keys=True))
        build_operations.assert_called()
        check.assert_called()

    def test_lns_repair_candidate_accepts_duplicate_removed_ids_once(self):
        import baseline_greedy

        prob_info = _repair_candidate_prob_info()
        incumbent = _repair_candidate_incumbent()
        incumbent_snapshot = json.dumps(incumbent, sort_keys=True)

        candidate = baseline_greedy._lns_try_repair_candidate(
            prob_info,
            incumbent,
            [1, 1],
            "edd",
            None,
        )

        self.assertIsNotNone(candidate)
        _, result = candidate
        self.assertTrue(result["feasible"], result["violations"])
        self.assertEqual(incumbent_snapshot, json.dumps(incumbent, sort_keys=True))

    def test_lns_repair_candidate_rejects_unknown_removed_id_without_mutating_incumbent(self):
        import baseline_greedy

        prob_info = _repair_candidate_prob_info()
        incumbent = _repair_candidate_incumbent()
        incumbent_snapshot = json.dumps(incumbent, sort_keys=True)

        candidate = baseline_greedy._lns_try_repair_candidate(
            prob_info,
            incumbent,
            [99],
            "edd",
            None,
        )

        self.assertIsNone(candidate)
        self.assertEqual(incumbent_snapshot, json.dumps(incumbent, sort_keys=True))

    def test_lns_repair_candidate_discards_empty_repair_result_without_mutating_incumbent(self):
        import baseline_greedy

        prob_info = _repair_candidate_prob_info()
        incumbent = _repair_candidate_incumbent()
        incumbent_snapshot = json.dumps(incumbent, sort_keys=True)

        with patch("baseline_greedy._place_blocks", return_value={}):
            candidate = baseline_greedy._lns_try_repair_candidate(
                prob_info,
                incumbent,
                [1],
                "edd",
                None,
            )

        self.assertIsNone(candidate)
        self.assertEqual(incumbent_snapshot, json.dumps(incumbent, sort_keys=True))

    def test_lns_repair_candidate_discards_repair_failure_without_mutating_incumbent(self):
        import baseline_greedy

        prob_info = _repair_candidate_prob_info()
        incumbent = _repair_candidate_incumbent()
        incumbent_snapshot = json.dumps(incumbent, sort_keys=True)

        with patch(
            "baseline_greedy._place_blocks",
            side_effect=baseline_greedy._LnsRepairFailed({}),
        ):
            failed_candidate = baseline_greedy._lns_try_repair_candidate(
                prob_info,
                incumbent,
                [1],
                "edd",
                None,
            )
        with patch("baseline_greedy._place_blocks", side_effect=RuntimeError("boom")):
            exception_candidate = baseline_greedy._lns_try_repair_candidate(
                prob_info,
                incumbent,
                [1],
                "edd",
                None,
            )

        self.assertIsNone(failed_candidate)
        self.assertIsNone(exception_candidate)
        self.assertEqual(incumbent_snapshot, json.dumps(incumbent, sort_keys=True))

    def test_lns_repair_candidate_discards_expired_deadline_without_mutating_incumbent(self):
        import baseline_greedy

        prob_info = _repair_candidate_prob_info()
        incumbent = _repair_candidate_incumbent()
        incumbent_snapshot = json.dumps(incumbent, sort_keys=True)

        candidate = baseline_greedy._lns_try_repair_candidate(
            prob_info,
            incumbent,
            [1],
            "edd",
            0.0,
        )

        self.assertIsNone(candidate)
        self.assertEqual(incumbent_snapshot, json.dumps(incumbent, sort_keys=True))

    def test_lns_repair_candidate_discards_infeasible_checker_result_without_mutating_incumbent(self):
        import baseline_greedy

        prob_info = _repair_candidate_prob_info()
        incumbent = _repair_candidate_incumbent()
        incumbent_snapshot = json.dumps(incumbent, sort_keys=True)
        infeasible = {
            "feasible": False,
            "stage": 1,
            "violations": ["forced test infeasible"],
            "objective": None,
            "obj1": None,
            "obj2": None,
            "obj3": None,
        }

        with patch("utils.check_feasibility", return_value=infeasible):
            candidate = baseline_greedy._lns_try_repair_candidate(
                prob_info,
                incumbent,
                [1],
                "edd",
                None,
            )

        self.assertIsNone(candidate)
        self.assertEqual(incumbent_snapshot, json.dumps(incumbent, sort_keys=True))


if __name__ == "__main__":
    unittest.main()
