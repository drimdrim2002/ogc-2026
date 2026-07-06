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
            ["prob_21.json", "prob_32.json", "prob_9.json"],
            [pathlib.Path(row["path"]).name for row in rows],
        )
        self.assertTrue(all(row["set_name"] == "smoke-3" for row in rows))
        self.assertTrue(all(row["solver"] == "stats_only" for row in rows))

    def test_benchmark_daily40_set_alias_discovers_all_training_instances(self):
        import benchmark_instances

        paths = benchmark_instances.select_instance_paths(ROOT_DIR, "daily-40")

        self.assertEqual(40, len(paths))
        self.assertEqual(
            [f"prob_{idx}.json" for idx in range(1, 41)],
            [path.name for path in paths],
        )

    def test_benchmark_solver_myalgorithm_calls_submission_entry_point(self):
        import benchmark_instances
        from baseline_greedy import _serial_fallback_solution

        calls = []

        def fake_algorithm(received_prob_info, timelimit):
            calls.append((received_prob_info["name"], timelimit))
            return _serial_fallback_solution(received_prob_info)

        out = StringIO()
        with patch("myalgorithm.algorithm", side_effect=fake_algorithm):
            with redirect_stdout(out):
                status = benchmark_instances.main([
                    "--root",
                    str(ROOT_DIR),
                    "--set-name",
                    "smoke-3",
                    "--solver",
                    "myalgorithm",
                    "--limit",
                    "1",
                    "--timelimit",
                    "0.001",
                ])

        self.assertEqual(0, status)
        rows = json.loads(out.getvalue())
        self.assertEqual([("prob_21", 0.001)], calls)
        self.assertEqual("myalgorithm", rows[0]["solver"])
        self.assertTrue(rows[0]["feasible"])
        self.assertEqual(5, rows[0]["stage"])

    def test_benchmark_solver_myalgorithm_reports_submission_block_order_mode(self):
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
                    "smoke-3",
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
        self.assertEqual("slack", rows[0]["block_order_mode"])

    def test_benchmark_solver_label_cannot_request_unexecuted_solver(self):
        import benchmark_instances
        from baseline_greedy import _serial_fallback_solution

        out = StringIO()
        def fake_algorithm(received_prob_info, timelimit):
            return _serial_fallback_solution(received_prob_info)

        with patch("myalgorithm.algorithm", side_effect=fake_algorithm) as algorithm:
            with patch("baseline_greedy.greedyalgorithm") as greedy:
                with redirect_stdout(out):
                    status = benchmark_instances.main([
                        "--root",
                        str(ROOT_DIR),
                        "--set-name",
                        "smoke-3",
                        "--solver",
                        "myalgorithm",
                        "--limit",
                        "1",
                        "--timelimit",
                        "0.001",
                    ])

        self.assertEqual(0, status)
        algorithm.assert_called_once()
        self.assertFalse(greedy.called)
        rows = json.loads(out.getvalue())
        self.assertEqual("myalgorithm", rows[0]["solver"])
        self.assertIn("feasible", rows[0])

    def test_benchmark_solver_stats_only_does_not_run_any_solver(self):
        import benchmark_instances

        out = StringIO()
        with patch("myalgorithm.algorithm") as algorithm:
            with patch("baseline_greedy.greedyalgorithm") as greedy:
                with redirect_stdout(out):
                    status = benchmark_instances.main([
                        "--root",
                        str(ROOT_DIR),
                        "--set-name",
                        "smoke-3",
                        "--solver",
                        "stats_only",
                        "--limit",
                        "1",
                        "--timelimit",
                        "0.001",
                    ])

        self.assertEqual(0, status)
        self.assertFalse(algorithm.called)
        self.assertFalse(greedy.called)
        rows = json.loads(out.getvalue())
        self.assertEqual("stats_only", rows[0]["solver"])
        self.assertNotIn("feasible", rows[0])

    def test_benchmark_run_baseline_alias_executes_baseline_greedy(self):
        import benchmark_instances
        from baseline_greedy import _serial_fallback_solution

        out = StringIO()

        def fake_greedyalgorithm(received_prob_info, timelimit=60, **kwargs):
            return _serial_fallback_solution(received_prob_info)

        with patch("baseline_greedy.greedyalgorithm", side_effect=fake_greedyalgorithm) as greedy:
            with redirect_stdout(out):
                status = benchmark_instances.main([
                    "--root",
                    str(ROOT_DIR),
                    "--set-name",
                    "smoke-3",
                    "--run-baseline",
                    "--limit",
                    "1",
                    "--timelimit",
                    "0.001",
                ])

        self.assertEqual(0, status)
        greedy.assert_called_once()
        rows = json.loads(out.getvalue())
        self.assertEqual("baseline_greedy", rows[0]["solver"])
        self.assertTrue(rows[0]["feasible"])

    def test_benchmark_passes_block_order_mode_to_baseline_greedy(self):
        import benchmark_instances
        from baseline_greedy import _serial_fallback_solution

        calls = []
        out = StringIO()

        def fake_greedyalgorithm(received_prob_info, timelimit=60, block_order_mode="edd"):
            calls.append((received_prob_info["name"], timelimit, block_order_mode))
            return _serial_fallback_solution(received_prob_info)

        with patch("baseline_greedy.greedyalgorithm", side_effect=fake_greedyalgorithm):
            with redirect_stdout(out):
                status = benchmark_instances.main([
                    "--root",
                    str(ROOT_DIR),
                    "--set-name",
                    "smoke-3",
                    "--solver",
                    "baseline_greedy",
                    "--block-order-mode",
                    "slack",
                    "--limit",
                    "1",
                    "--timelimit",
                    "0.001",
                ])

        self.assertEqual(0, status)
        self.assertEqual([("prob_21", 0.001, "slack")], calls)
        rows = json.loads(out.getvalue())
        self.assertEqual("slack", rows[0]["block_order_mode"])


class SerialFallbackTests(unittest.TestCase):
    def test_default_block_order_matches_existing_edd_sort(self):
        from baseline_greedy import _block_order_indices

        blocks = [
            {"release_time": 3, "due_date": 10, "processing_time": 4, "bay_preferences": [1, 8]},
            {"release_time": 0, "due_date": 7, "processing_time": 5, "bay_preferences": [5, 1]},
            {"release_time": 1, "due_date": 7, "processing_time": 2, "bay_preferences": [2, 4]},
            {"release_time": 2, "due_date": 10, "processing_time": 1, "bay_preferences": [7, 3]},
        ]

        expected = sorted(range(len(blocks)), key=lambda idx: (
            blocks[idx]["due_date"],
            blocks[idx]["processing_time"],
        ))

        self.assertEqual(expected, _block_order_indices(blocks))
        self.assertEqual(expected, _block_order_indices(blocks, "edd"))

    def test_block_order_modes_include_each_block_once(self):
        from baseline_greedy import _block_order_indices

        blocks = [
            {"release_time": 3, "due_date": 10, "processing_time": 4, "bay_preferences": [1, 8]},
            {"release_time": 0, "due_date": 7, "processing_time": 5, "bay_preferences": [5, 1]},
            {"release_time": 1, "due_date": 7, "processing_time": 2, "bay_preferences": [2, 4]},
            {"release_time": 2, "due_date": 10, "processing_time": 1, "bay_preferences": [7, 3]},
        ]

        for mode in (
            "edd",
            "release_edd",
            "slack",
            "latest_safe_entry",
            "preference_pressure",
        ):
            with self.subTest(mode=mode):
                order = _block_order_indices(blocks, mode)
                self.assertEqual(len(blocks), len(order))
                self.assertEqual(set(range(len(blocks))), set(order))

    def test_invalid_block_order_mode_raises(self):
        from baseline_greedy import _block_order_indices

        blocks = [
            {"release_time": 0, "due_date": 1, "processing_time": 1, "bay_preferences": [1]},
        ]

        with self.assertRaisesRegex(ValueError, "block_order_mode"):
            _block_order_indices(blocks, "unknown")

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

    def test_greedy_tiny_timelimit_returns_feasible_fallback_with_non_edd_order(self):
        from baseline_greedy import greedyalgorithm
        from utils import check_feasibility

        instance_path = ROOT_DIR / "data/train 2/prob_1.json"
        prob_info = json.loads(instance_path.read_text())

        with redirect_stdout(StringIO()):
            solution = greedyalgorithm(
                prob_info,
                timelimit=0.001,
                block_order_mode="latest_safe_entry",
            )
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
