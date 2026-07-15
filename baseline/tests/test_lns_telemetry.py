from __future__ import annotations

import copy
from dataclasses import dataclass
import unittest

from solver.alns import AlnsConfig, AlnsContext, run_lns
from solver.entry import OptionalPhaseResult, solve
from solver.geometry import GeometryKernel
from solver.runtime import RunTrace
from tests.helpers import block, checker, instance


@dataclass(frozen=True)
class _OperatorMetrics:
    attempts: int
    feasible: int
    accepted: int
    new_best: int
    delta_sum: float = 0.0
    exceptions: int = 0
    time_s: float = 0.0


@dataclass(frozen=True)
class _Metrics:
    iterations: int
    time_by_phase: tuple[tuple[str, float], ...]
    exit_reason: str
    per_operator: tuple[tuple[str, _OperatorMetrics], ...]
    best_trace: tuple[float, ...]
    mip_events: tuple[tuple[tuple[str, object], ...], ...] = ()


@dataclass(frozen=True)
class _Result:
    metrics: _Metrics


class LnsTelemetryTests(unittest.TestCase):
    def test_telemetry_on_off_preserves_actual_seeded_lns_operations_and_objective(self):
        raw = instance([block(), block()])

        def loader():
            def phase(instance_value, snapshot, phase_budget):
                del snapshot
                kernel = GeometryKernel.from_instance(instance_value)

                def lns_runner(initial, store, raw_value, checker_value, lns_budget):
                    return run_lns(
                        initial,
                        store,
                        AlnsContext(instance_value, kernel, raw_value, checker_value),
                        lns_budget,
                        AlnsConfig(seed=20260710, max_iterations=1),
                    )

                return OptionalPhaseResult(lns_runner=lns_runner)

            return phase

        without_telemetry = solve(raw, 60.0, checker, optional_phase_loader=loader)
        trace = RunTrace()
        with_telemetry = solve(
            raw,
            60.0,
            checker,
            optional_phase_loader=loader,
            trace=trace,
        )

        self.assertEqual(without_telemetry, with_telemetry)
        self.assertEqual(5, checker(copy.deepcopy(raw), copy.deepcopy(with_telemetry))["stage"])
        self.assertEqual(["anchor"], [item["kind"] for item in trace.lns_invocations])

    def test_anchor_and_extension_invocations_are_separate_and_top_level_is_aggregate(self):
        raw = instance([block(), block()])

        def loader():
            def phase(instance_value, snapshot, phase_budget):
                del instance_value, snapshot, phase_budget

                def lns_runner(initial, store, raw_value, checker_value, lns_budget):
                    del initial, store, raw_value, checker_value
                    if lns_budget.limit == 60.0:
                        return _Result(
                            _Metrics(
                                iterations=3,
                                time_by_phase=(("repair", 2.0), ("retime", 0.5), ("checker", 0.3)),
                                exit_reason="ITERATION_LIMIT",
                                per_operator=(("random", _OperatorMetrics(3, 2, 1, 1)),),
                                best_trace=(100.0, 90.0),
                            )
                        )
                    return _Result(
                        _Metrics(
                            iterations=5,
                            time_by_phase=(("repair", 4.0), ("retime", 1.0), ("checker", 0.2)),
                            exit_reason="DEADLINE",
                            per_operator=(("random", _OperatorMetrics(5, 4, 3, 2)),),
                            best_trace=(90.0, 80.0),
                            mip_events=(
                                (
                                    ("dispatch", 1),
                                    ("last_status", "OPTIMAL"),
                                    ("checker_pass", True),
                                    ("strict_install", True),
                                ),
                            ),
                        )
                    )

                return OptionalPhaseResult(lns_runner=lns_runner)

            return phase

        without_telemetry = solve(raw, 180.0, checker, optional_phase_loader=loader)
        trace = RunTrace()
        with_telemetry = solve(
            raw,
            180.0,
            checker,
            optional_phase_loader=loader,
            trace=trace,
        )

        self.assertEqual(without_telemetry, with_telemetry)
        checked = checker(copy.deepcopy(raw), copy.deepcopy(with_telemetry))
        self.assertTrue(checked["feasible"])
        self.assertEqual(5, checked["stage"])

        self.assertEqual(["anchor", "extension"], [item["kind"] for item in trace.lns_invocations])
        self.assertEqual([60.0, 180.0], [item["budget_seconds"] for item in trace.lns_invocations])
        self.assertEqual([3, 5], [item["iterations"] for item in trace.lns_invocations])
        self.assertEqual(["ITERATION_LIMIT", "DEADLINE"], [item["exit_reason"] for item in trace.lns_invocations])
        self.assertEqual([2.0, 4.0], [item["repair_seconds"] for item in trace.lns_invocations])
        self.assertEqual([0.5, 1.0], [item["retime_seconds"] for item in trace.lns_invocations])
        self.assertEqual([0.3, 0.2], [item["checker_seconds"] for item in trace.lns_invocations])
        self.assertEqual(6.0, trace.phase_times["lns_repair"])
        self.assertEqual(1.5, trace.phase_times["lns_retime"])
        self.assertEqual(0.5, trace.phase_times["lns_checker"])
        self.assertEqual(8, trace.model_stats["lns_aggregate"]["iterations"])
        self.assertEqual(
            "OPTIMAL",
            trace.model_stats["lns_aggregate"]["mip_events"][0]["last_status"],
        )
        self.assertTrue(trace.lns_invocations[1]["mip_events"][0]["strict_install"])
        self.assertEqual(5, trace.model_stats["lns_last"]["iterations"])
        self.assertEqual(8, trace.operator_stats["random"]["attempts"])
        self.assertEqual(5, trace.operator_stats_last["random"]["attempts"])


if __name__ == "__main__":
    unittest.main()
