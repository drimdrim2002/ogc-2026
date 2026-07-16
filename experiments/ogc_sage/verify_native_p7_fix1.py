#!/usr/bin/env python3
"""P7 fix1 fixed-work semantic gate; never runs a fixed-time solver."""

from __future__ import annotations

import argparse
import copy
import hashlib
import io
import json
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
BASELINE = ROOT / "baseline"
for path in (ROOT, BASELINE):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from baseline.solver.budget import Budget
from baseline.solver.construct import ConstructorConfig, evaluate_insert, generate_insertion_candidates
from baseline.solver.geometry import GeometryKernel
from baseline.solver.instance import parse_instance
from baseline.solver.native_repair import NativeRepairAdapter, NativeRepairSession, snapshot_digest
from baseline.solver import neighborhoods as neighborhoods_module
from baseline.solver.neighborhoods import NeighborhoodContext
from baseline.solver.serialize import serialize
from baseline.solver.state import IndexedSolutionState, Placement, SolutionSnapshot, compute_objective
from baseline.utils import check_feasibility
from experiments.ogc_sage.benchmark_ogc_sage import canonical_json


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def digest(value: object) -> str:
    return hashlib.sha256(canonical_json(value).encode()).hexdigest()


def row(item: Placement) -> list[int]:
    return [item.block_id, item.bay_id, item.orient_idx, item.x, item.y, item.entry, item.exit]


def config(caps: dict[str, object], backend: str, prefilter: bool) -> ConstructorConfig:
    return ConstructorConfig(
        seed=int(caps["seed"]),
        time_cap=int(caps["time_cap"]),
        escalated_time_cap=int(caps["escalated_time_cap"]),
        anchor_cap=int(caps["anchor_cap"]),
        lattice_cap=int(caps["lattice_cap"]),
        max_profiles=int(caps["max_profiles"]),
        max_candidate_attempts=None,
        selection_policy=str(caps["selection_policy"]),
        repair_backend=backend,
        native_prefilter_enabled=prefilter,
    )


def run_variant(raw: dict[str, object], fixture: dict[str, object], backend: str, prefilter: bool) -> dict[str, object]:
    instance = parse_instance(raw)
    kernel = GeometryKernel.from_instance(instance)
    source = SolutionSnapshot(tuple(Placement(*item) for item in fixture["source_snapshot"]["placements"]))
    source = source.with_objective(compute_objective(instance, source))
    context = NeighborhoodContext(instance, kernel, config(fixture["fixed_work_caps"], backend, prefilter))
    trace: list[dict[str, object]] = []
    original_generate = neighborhoods_module.generate_insertion_candidates

    def recorded_generate(*args, **kwargs):
        values = tuple(original_generate(*args, **kwargs))
        current = kwargs.get("current_placement")
        trace.append({
            "block_id": int(args[1]),
            "state_version": int(args[0].version),
            "current": None if current is None else row(current),
            "candidates": [
                {"placement": row(item.placement), "canonical_tie": list(item.canonical_tie)}
                for item in values
            ],
        })
        return values

    with patch.object(neighborhoods_module, "generate_insertion_candidates", recorded_generate):
        result = neighborhoods_module.heuristic_repair(
            source,
            tuple(fixture["destroy"]["destroyed_ids"]),
            context,
            Budget.start(float(fixture["fixed_work_caps"]["budget_guard_seconds"])),
            regret_depth=int(fixture["fixed_work_caps"]["regret_depth"]),
        )
    operations = serialize(result.snapshot, kernel)
    checked = check_feasibility(copy.deepcopy(raw), copy.deepcopy(operations))
    nonzero_guides = sum(
        1
        for call in trace
        for candidate in call["candidates"]
        if len(candidate["canonical_tie"]) == 10 and candidate["canonical_tie"][2] == 1
    )
    rollback_calls = sum(
        call["current"] is not None
        and any(item["placement"] == call["current"] for item in call["candidates"])
        for call in trace
    )
    return {
        "backend": backend,
        "prefilter": prefilter,
        "status": result.status,
        "candidate_trace": trace,
        "candidate_trace_sha256": digest(trace),
        "candidate_calls": len(trace),
        "nonzero_guide_candidates": nonzero_guides,
        "rollback_calls": rollback_calls,
        "placement_digest": snapshot_digest(result.snapshot),
        "serialization_digest": digest(operations),
        "objective": result.snapshot.objective.total,
        "checker": {key: checked.get(key) for key in ("feasible", "stage", "violations", "objective")},
        "telemetry": dict(result.telemetry),
    }


class FaultBudget:
    def __init__(self, fail_on: int | None = None, search_remaining: float = 10.0):
        self.fail_on = fail_on
        self.calls = 0
        self.stopped = False
        self._search_remaining = search_remaining

    def can_start(self, _predicted: float, margin: float = 0.0) -> bool:
        del margin
        self.calls += 1
        if self.fail_on is not None and self.calls >= self.fail_on:
            self.stopped = True
        return not self.stopped

    def search_remaining(self) -> float:
        return self._search_remaining

    def remaining(self) -> float:
        return self._search_remaining


def run_faults(raw: dict[str, object], fixture: dict[str, object]) -> dict[str, object]:
    instance = parse_instance(raw)
    kernel = GeometryKernel.from_instance(instance)
    source = SolutionSnapshot(tuple(Placement(*item) for item in fixture["source_snapshot"]["placements"]))
    destroyed = set(fixture["destroy"]["destroyed_ids"])
    state = IndexedSolutionState(
        instance,
        tuple(item for item in source.placements if item.block_id not in destroyed),
        time_cap=int(fixture["fixed_work_caps"]["time_cap"]),
        anchor_cap=int(fixture["fixed_work_caps"]["anchor_cap"]),
        lattice_cap=int(fixture["fixed_work_caps"]["lattice_cap"]),
    )
    state.kernel = kernel
    block_id = min(destroyed)
    current = next(item for item in source.placements if item.block_id == block_id)
    rollback = evaluate_insert(state, current, kernel)
    if rollback is None:
        raise RuntimeError("fault fixture rollback is not exact-safe")

    def execute(name: str, budget: FaultBudget, adapter_factory=NativeRepairAdapter) -> dict[str, object]:
        version = state.version
        session = NativeRepairSession(
            instance,
            kernel,
            requested_backend="native",
            prefilter_enabled=True,
            adapter_factory=adapter_factory,
        )
        values = session.generate(
            state,
            block_id,
            kernel,
            budget,
            current,
            attempt_cap=None,
            escalated_time_cap=int(fixture["fixed_work_caps"]["escalated_time_cap"]),
            python_reference=lambda: (rollback,),
        )
        telemetry = dict(session.telemetry())
        return {
            "name": name,
            "budget_calls": budget.calls,
            "state_version_unchanged": state.version == version,
            "rollback_preserved": any(item.placement == current for item in values),
            "candidate_count": len(values),
            "fallback_reason": telemetry["fallback_reason"],
            "native_deadline_count": telemetry["native_deadline_count"],
            "native_exception_count": telemetry["native_exception_count"],
            "native_invalid_output_count": telemetry["native_invalid_output_count"],
        }

    during_prepare = FaultBudget(search_remaining=min(0.25, max(0.02, 0.0005 * len(instance.blocks))))

    after_prepare_budget = FaultBudget()
    class AfterPrepareAdapter(NativeRepairAdapter):
        def prepare_chunk(self, *args, **kwargs):
            prepared = super().prepare_chunk(*args, **kwargs)
            after_prepare_budget.stopped = True
            return prepared

    after_resolve_budget = FaultBudget()
    class AfterResolveAdapter(NativeRepairAdapter):
        def exact_verdicts_until_deadline(self, *args, **kwargs):
            result = super().exact_verdicts_until_deadline(*args, **kwargs)
            after_resolve_budget.stopped = True
            return result

    cases = [
        execute("before_prepare", FaultBudget(fail_on=1)),
        execute("during_prepare", during_prepare),
        execute("after_prepare", after_prepare_budget, AfterPrepareAdapter),
        execute("during_resolve", FaultBudget(fail_on=5)),
        execute("after_resolve", after_resolve_budget, AfterResolveAdapter),
    ]
    return {
        "cases": cases,
        "all_state_versions_unchanged": all(item["state_version_unchanged"] for item in cases),
        "all_rollback_preserved": all(item["rollback_preserved"] for item in cases),
        "all_deadlines_observed": all(item["native_deadline_count"] >= 1 for item in cases),
    }


def run_fault_unittests() -> dict[str, object]:
    suite = unittest.defaultTestLoader.loadTestsFromName("tests.test_native_repair_integration")
    stream = io.StringIO()
    result = unittest.TextTestRunner(stream=stream, verbosity=1).run(suite)
    return {"tests_run": result.testsRun, "failures": len(result.failures), "errors": len(result.errors), "successful": result.wasSuccessful(), "output": stream.getvalue()}


def run_guide_tie_fixture() -> dict[str, object]:
    """Force equal-cost guide/non-guide options into the returned top three."""
    raw = {
        "name": "p7-fix1-guide-tie",
        "bays": [{"width": 3, "height": 2}, {"width": 3, "height": 2}],
        "blocks": [{
            "release_time": 0,
            "due_date": 10,
            "processing_time": 1,
            "workload": 1.0,
            "bay_preferences": [10.0, 10.0],
            "shape": [{"orientation": 0, "layers": [[[0, 0], [2, 0], [2, 2], [0, 2]]]}],
        }],
        "weights": {"w1": 3.0, "w2": 5.0, "w3": 7.0},
    }
    instance = parse_instance(raw)
    kernel = GeometryKernel.from_instance(instance)
    state = IndexedSolutionState(instance, (), time_cap=1, anchor_cap=8, lattice_cap=16)
    state.kernel = kernel
    current = Placement(0, 0, 0, 0, 0, 0, 1)
    common = dict(
        seed=20260710,
        time_cap=1,
        escalated_time_cap=2,
        anchor_cap=8,
        lattice_cap=16,
        max_profiles=1,
        max_candidate_attempts=None,
    )
    python = generate_insertion_candidates(
        state,
        0,
        kernel,
        Budget.start(2.0),
        current_placement=current,
        config=ConstructorConfig(**common, repair_backend="python"),
    )
    native = generate_insertion_candidates(
        state,
        0,
        kernel,
        Budget.start(2.0),
        current_placement=current,
        config=ConstructorConfig(**common, repair_backend="native", native_prefilter_enabled=True),
    )
    def record(values):
        return [{"placement": row(item.placement), "canonical_tie": list(item.canonical_tie)} for item in values]
    python_record, native_record = record(python), record(native)
    return {
        "python": python_record,
        "native": native_record,
        "parity": python_record == native_record,
        "nonzero_guide_count": sum(len(item["canonical_tie"]) == 10 and item["canonical_tie"][2] == 1 for item in native_record),
        "multiple_fitting_options": len({item["placement"][1] for item in native_record}) >= 2,
        "multiple_positions": len({(item["placement"][3], item["placement"][4]) for item in native_record}) >= 2,
        "rollback_preserved": any(item.placement == current for item in native),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--fixture", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        raise SystemExit(f"refusing to overwrite existing artifact root: {args.output}")
    fixture = json.loads(args.fixture.read_text())
    raw = json.loads((ROOT / fixture["instance_path"]).read_text())
    python = run_variant(raw, fixture, "python", False)
    native_off = run_variant(raw, fixture, "native", False)
    native_on = run_variant(raw, fixture, "native", True)
    faults = run_faults(raw, fixture)
    fault_tests = run_fault_unittests()
    guide_tie = run_guide_tie_fixture()
    variants = (python, native_off, native_on)
    diagnosed = {"candidate_rows": 780757, "pair_count": 5051307, "exact_relation_calls": 746851}
    work = native_on["telemetry"]
    checks = {
        "stage5_all": all(item["checker"]["stage"] == 5 and item["checker"]["violations"] == [] for item in variants),
        "multi_iteration_candidate_trace_parity": native_off["candidate_trace"] == python["candidate_trace"] == native_on["candidate_trace"],
        "placement_serialization_objective_parity": all(item["placement_digest"] == python["placement_digest"] and item["serialization_digest"] == python["serialization_digest"] and item["objective"] == python["objective"] for item in variants),
        "nonzero_guide_tie_exercised": guide_tie["parity"] and guide_tie["nonzero_guide_count"] > 0 and guide_tie["multiple_fitting_options"] and guide_tie["multiple_positions"] and guide_tie["rollback_preserved"],
        "rollback_every_call": all(item["rollback_calls"] == item["candidate_calls"] for item in variants),
        "native_actual_no_fallback": all(item["telemetry"]["actual_backend"] == "native" and item["telemetry"]["fallback_count"] == 0 for item in (native_off, native_on)),
        "bounded_rows": work["candidate_rows"] <= 20000 and work["candidate_rows"] < diagnosed["candidate_rows"] // 10,
        "bounded_pairs": work["pair_count"] <= 30000 and work["pair_count"] < diagnosed["pair_count"] // 10,
        "bounded_exact_calls": work["exact_relation_calls"] <= 15000 and work["exact_relation_calls"] < diagnosed["exact_relation_calls"] // 10,
        "deadline_rollback_faults": faults["all_state_versions_unchanged"] and faults["all_rollback_preserved"] and faults["all_deadlines_observed"],
        "invalid_exception_fallback_tests": fault_tests["successful"],
    }
    args.output.mkdir(parents=True)
    parity = {"schema_version": 1, "kind": "p7_fix1_production_unbounded_parity", "scope": "fixed-work only; no P7 25-second or fixed-time solver run", "fixture": {"path": str(args.fixture), "sha256": sha256(args.fixture)}, "python": python, "native_prefilter_off": native_off, "native_prefilter_on": native_on}
    work_summary = {"schema_version": 1, "diagnosed_unbounded_native": diagnosed, "fixed_native": {key: work[key] for key in ("candidate_rows", "pair_count", "exact_relation_calls", "stream_chunks")}, "bounds": {"candidate_rows": 20000, "pair_count": 30000, "exact_relation_calls": 15000}}
    gate = {"schema_version": 1, "phase": "P7-fix1", "status": "PASS" if all(checks.values()) else "FAIL", "checks": checks, "production_default": "python", "fixed_time_quality": "NOT RUN / NOT CLAIMED"}
    for name, value in (("parity.json", parity), ("guide-tie.json", guide_tie), ("fault-injection.json", faults), ("fault-unittests.json", fault_tests), ("work-bound.json", work_summary), ("gate.json", gate)):
        (args.output / name).write_text(json.dumps(value, indent=2, sort_keys=True) + "\n")
    (args.output / "SHA256SUMS").write_text("".join(f"{sha256(path)}  {path.name}\n" for path in sorted(args.output.glob("*.json"))))
    print(json.dumps({"status": gate["status"], "checks": checks}, sort_keys=True))
    return 0 if gate["status"] == "PASS" else 2


if __name__ == "__main__":
    raise SystemExit(main())
