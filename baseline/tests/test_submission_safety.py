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
