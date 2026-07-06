import json
import pathlib
import sys
import unittest
from contextlib import redirect_stdout
from io import StringIO


BASELINE_DIR = pathlib.Path(__file__).resolve().parents[1]
ROOT_DIR = BASELINE_DIR.parent
if str(BASELINE_DIR) not in sys.path:
    sys.path.insert(0, str(BASELINE_DIR))


class Phase0HarnessTests(unittest.TestCase):
    def test_run_myalgorithm_default_instance_exists(self):
        import run_myalgorithm

        default_path = BASELINE_DIR / run_myalgorithm.INSTANCE_PATH
        self.assertTrue(
            default_path.resolve().is_file(),
            f"default INSTANCE_PATH does not exist: {default_path.resolve()}",
        )

    def test_benchmark_module_finds_all_training_instances(self):
        import benchmark_instances

        paths = benchmark_instances.find_instance_paths(ROOT_DIR)
        self.assertEqual(40, len(paths))
        self.assertTrue(all(path.is_file() for path in paths))

    def test_benchmark_stats_cover_full_scale(self):
        import benchmark_instances

        paths = benchmark_instances.find_instance_paths(ROOT_DIR)
        stats = [benchmark_instances.compute_instance_stats(path) for path in paths]
        self.assertEqual(40, len(stats))
        self.assertEqual(7500, sum(item["n_blocks"] for item in stats))
        self.assertEqual(300, max(item["n_blocks"] for item in stats))
        self.assertEqual(5, max(item["n_bays"] for item in stats))
        self.assertGreaterEqual(max(item["max_layers"] for item in stats), 4)

    def test_benchmark_json_output_is_machine_readable_with_baseline_logs(self):
        import benchmark_instances

        out = StringIO()
        with redirect_stdout(out):
            status = benchmark_instances.main([
                "--root",
                str(ROOT_DIR),
                "--limit",
                "1",
                "--run-baseline",
                "--timelimit",
                "0.001",
            ])

        self.assertEqual(0, status)
        rows = json.loads(out.getvalue())
        self.assertEqual(1, len(rows))
        self.assertIn("git_commit", rows[0])
        self.assertIsNone(rows[0]["seed"])
        self.assertEqual("baseline_greedy", rows[0]["solver"])
        self.assertEqual("all", rows[0]["set_name"])
        self.assertTrue(rows[0]["feasible"])

    def test_benchmark_smoke3_set_uses_documented_instances(self):
        import benchmark_instances

        out = StringIO()
        with redirect_stdout(out):
            status = benchmark_instances.main([
                "--root",
                str(ROOT_DIR),
                "--set-name",
                "smoke-3",
            ])

        self.assertEqual(0, status)
        rows = json.loads(out.getvalue())
        self.assertEqual(
            ["prob_9.json", "prob_21.json", "prob_32.json"],
            [pathlib.Path(row["path"]).name for row in rows],
        )
        self.assertTrue(all(row["set_name"] == "smoke-3" for row in rows))
        self.assertTrue(all(row["solver"] == "stats_only" for row in rows))


class SerialFallbackTests(unittest.TestCase):
    def test_serial_fallback_is_feasible_on_example(self):
        from baseline_greedy import _serial_fallback_solution
        from utils import check_feasibility

        instance_path = ROOT_DIR / "alg_tester/example/example_B2_b10.json"
        prob_info = json.loads(instance_path.read_text())

        solution = _serial_fallback_solution(prob_info)
        result = check_feasibility(prob_info, solution)

        self.assertTrue(result["feasible"], result["violations"][:5])
        self.assertEqual(5, result["stage"])

    def test_complete_with_serial_fallback_preserves_partial_assignments(self):
        from baseline_greedy import (
            _complete_with_serial_fallback,
            _serial_fallback_assignments,
        )
        from utils import check_feasibility

        instance_path = ROOT_DIR / "alg_tester/example/example_B2_b10.json"
        prob_info = json.loads(instance_path.read_text())
        serial_assignments = _serial_fallback_assignments(prob_info)
        partial = {
            0: dict(serial_assignments[0]),
            1: dict(serial_assignments[1]),
        }

        solution = _complete_with_serial_fallback(prob_info, partial)
        result = check_feasibility(prob_info, solution)
        recovered = self._assignments_from_operations(solution["operations"])

        self.assertTrue(result["feasible"], result["violations"][:5])
        self.assertEqual(set(range(len(prob_info["blocks"]))), set(recovered))
        self.assertEqual(partial[0], recovered[0])
        self.assertEqual(partial[1], recovered[1])

    def test_complete_with_serial_fallback_uses_serial_when_trimmed_prefix_is_worse(self):
        from baseline_greedy import (
            _complete_with_serial_fallback,
            _serial_fallback_assignments,
        )
        from utils import check_feasibility

        instance_path = ROOT_DIR / "alg_tester/example/example_B2_b10.json"
        prob_info = json.loads(instance_path.read_text())
        serial_assignments = _serial_fallback_assignments(prob_info)

        preserved = dict(serial_assignments[0])
        preserved["entry_time"] += 1000
        preserved["exit_time"] += 1000

        bad_suffix = dict(serial_assignments[1])
        bad_suffix.update(
            {
                "bay_id": preserved["bay_id"],
                "x": preserved["x"],
                "y": preserved["y"],
                "orient_idx": 0,
                "entry_time": preserved["entry_time"],
                "exit_time": preserved["entry_time"] + prob_info["blocks"][1]["processing_time"],
            }
        )

        with redirect_stdout(StringIO()):
            solution = _complete_with_serial_fallback(
                prob_info,
                {0: preserved, 1: bad_suffix},
            )
        result = check_feasibility(prob_info, solution)
        recovered = self._assignments_from_operations(solution["operations"])

        self.assertTrue(result["feasible"], result["violations"][:5])
        self.assertEqual(serial_assignments[0], recovered[0])
        self.assertNotEqual(preserved, recovered[0])
        self.assertNotEqual(bad_suffix, recovered[1])

    def test_place_blocks_honors_expired_deadline(self):
        from baseline_greedy import _TimeBudgetExpired, _place_blocks
        from utils import Bay

        instance_path = ROOT_DIR / "alg_tester/example/example_B2_b10.json"
        prob_info = json.loads(instance_path.read_text())
        bays = [Bay.from_dict(data, idx) for idx, data in enumerate(prob_info["bays"])]

        with self.assertRaises(_TimeBudgetExpired):
            _place_blocks(
                [0],
                prob_info["blocks"],
                bays,
                [[] for _ in bays],
                [[] for _ in bays],
                [0.0 for _ in bays],
                1.0,
                1.0,
                1.0,
                forced_ids=set(),
                deadline=0.0,
            )

    def test_place_blocks_deadline_exception_carries_completed_assignments(self):
        import baseline_greedy
        from baseline_greedy import _TimeBudgetExpired, _place_blocks
        from utils import Bay

        instance_path = ROOT_DIR / "alg_tester/example/example_B2_b10.json"
        prob_info = json.loads(instance_path.read_text())
        bays = [Bay.from_dict(data, idx) for idx, data in enumerate(prob_info["bays"])]
        calls = []

        def expire_on_second_check(deadline):
            calls.append(deadline)
            if len(calls) >= 2:
                raise _TimeBudgetExpired()

        original_check_deadline = baseline_greedy._check_deadline
        baseline_greedy._check_deadline = expire_on_second_check
        try:
            with self.assertRaises(_TimeBudgetExpired) as ctx:
                _place_blocks(
                    [0, 1],
                    prob_info["blocks"],
                    bays,
                    [[] for _ in bays],
                    [[] for _ in bays],
                    [0.0 for _ in bays],
                    1.0,
                    1.0,
                    1.0,
                    forced_ids={0, 1},
                    deadline=1.0,
                )
        finally:
            baseline_greedy._check_deadline = original_check_deadline

        partial = getattr(ctx.exception, "assignments", None)
        self.assertIsNotNone(partial)
        self.assertEqual({0}, set(partial))
        self.assertEqual(0, partial[0]["block_id"])

    def test_greedy_tiny_timelimit_returns_feasible_fallback(self):
        from baseline_greedy import greedyalgorithm
        from utils import check_feasibility

        instance_path = ROOT_DIR / "data/train 2/prob_1.json"
        prob_info = json.loads(instance_path.read_text())

        with redirect_stdout(StringIO()):
            solution = greedyalgorithm(prob_info, timelimit=0.001)
        result = check_feasibility(prob_info, solution)

        self.assertTrue(result["feasible"], result["violations"][:5])
        self.assertEqual(5, result["stage"])

    def _assignments_from_operations(self, operations):
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


if __name__ == "__main__":
    unittest.main()
