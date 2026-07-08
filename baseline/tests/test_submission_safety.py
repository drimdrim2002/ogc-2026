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


if __name__ == "__main__":
    unittest.main()
