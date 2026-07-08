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
    from baseline_greedy import _assignments_from_operations as parse_operations

    return parse_operations(operations)


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


class SubmissionEntryPointSafetyTests(unittest.TestCase):
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
        greedy.assert_called_once_with(prob_info, 1.0, block_order_mode="slack")

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

    def test_algorithm_replaces_empty_operations_with_verified_fallback(self):
        import myalgorithm

        prob_info = _load_example_instance()

        with patch("baseline_greedy.greedyalgorithm", return_value={"operations": {}}):
            with redirect_stdout(StringIO()):
                solution = myalgorithm.algorithm(prob_info, timelimit=1.0)

        _assert_stage5_feasible(self, prob_info, solution)

    def test_algorithm_replaces_malformed_output_with_verified_fallback(self):
        import myalgorithm

        prob_info = _load_example_instance()

        with patch("baseline_greedy.greedyalgorithm", return_value="not-a-solution"):
            with redirect_stdout(StringIO()):
                solution = myalgorithm.algorithm(prob_info, timelimit=1.0)

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
    def test_lns_state_assignments_from_operations_returns_canonical_shape(self):
        from baseline_greedy import _assignments_from_operations

        entry_op = {
            "type": "ENTRY",
            "block_id": 2,
            "bay_id": 1,
            "x": 4,
            "y": 5,
            "orient_idx": 0,
        }
        exit_op = {"type": "EXIT", "block_id": 2, "bay_id": 1}
        operations = {
            "9": [exit_op],
            "3": [entry_op],
        }

        assignments = _assignments_from_operations(operations)

        self.assertEqual(
            {2: _lns_assignment(2, bay_id=1, x=4, y=5, entry_time=3, exit_time=9)},
            assignments,
        )

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


if __name__ == "__main__":
    unittest.main()
