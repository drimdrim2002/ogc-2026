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
            block_id = op["block_id"]
            item = assignments.setdefault(block_id, {"block_id": block_id})
            if op["type"] == "ENTRY":
                item.update(
                    {
                        "bay_id": op["bay_id"],
                        "x": op["x"],
                        "y": op["y"],
                        "orient_idx": op["orient_idx"],
                        "entry_time": time_int,
                    }
                )
            elif op["type"] == "EXIT":
                item["exit_time"] = time_int
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


if __name__ == "__main__":
    unittest.main()
