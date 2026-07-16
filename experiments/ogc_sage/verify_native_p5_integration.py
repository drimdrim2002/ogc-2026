#!/usr/bin/env python3
"""P5 repair-backend integration, fallback, and dominance gate.

This is fixed-work evidence only.  It intentionally does not run P6 packaging
or any fixed-time P7 matrix.  The normal Python production default is checked
alongside explicit native P4-prefilter OFF/ON repair replays.
"""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
BASELINE = ROOT / "baseline"
for path in (ROOT, BASELINE):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from baseline.solver.budget import Budget
from baseline.solver.construct import ConstructorConfig
from baseline.solver.geometry import GeometryKernel
from baseline.solver.instance import parse_instance
from baseline.solver.native_repair import snapshot_digest
from baseline.solver.neighborhoods import NeighborhoodContext, heuristic_repair
from baseline.solver.serialize import serialize
from baseline.solver.state import Placement, SolutionSnapshot, compute_objective
from baseline.utils import check_feasibility
from experiments.ogc_sage.benchmark_ogc_sage import canonical_json


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def value_digest(value: object) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def constructor_config(caps: dict[str, object], *, backend: str, prefilter: bool) -> ConstructorConfig:
    keys = (
        "seed",
        "time_cap",
        "escalated_time_cap",
        "anchor_cap",
        "lattice_cap",
        "max_profiles",
        "max_candidate_attempts",
        "selection_policy",
    )
    return ConstructorConfig(
        **{key: caps[key] for key in keys},
        repair_backend=backend,
        native_prefilter_enabled=prefilter,
    )


def run_repair(
    raw: dict[str, object],
    fixture: dict[str, object],
    *,
    backend: str,
    prefilter: bool,
) -> dict[str, object]:
    instance = parse_instance(raw)
    kernel = GeometryKernel.from_instance(instance)
    anchor = SolutionSnapshot(
        tuple(Placement(*row) for row in fixture["source_snapshot"]["placements"])
    ).with_objective(
        compute_objective(instance, SolutionSnapshot(
            tuple(Placement(*row) for row in fixture["source_snapshot"]["placements"])
        ))
    )
    result = heuristic_repair(
        anchor,
        tuple(fixture["destroy"]["destroyed_ids"]),
        NeighborhoodContext(
            instance,
            kernel,
            constructor_config(
                fixture["fixed_work_caps"], backend=backend, prefilter=prefilter
            ),
        ),
        Budget.start(float(fixture["fixed_work_caps"]["budget_guard_seconds"])),
        regret_depth=int(fixture["fixed_work_caps"]["regret_depth"]),
    )
    operations = serialize(result.snapshot, kernel)
    checked = check_feasibility(raw, operations)
    telemetry = dict(result.telemetry)
    return {
        "backend": backend,
        "prefilter_enabled": prefilter,
        "status": result.status,
        "changed_ids": sorted(result.changed_ids),
        "candidates_generated": result.candidates_generated,
        "objective_delta": result.objective_delta,
        "placement_digest": snapshot_digest(result.snapshot),
        "serialization_digest": value_digest(operations),
        "objective": {
            "z1": result.snapshot.objective.z1,
            "z2": result.snapshot.objective.z2,
            "z3": result.snapshot.objective.z3,
            "total": result.snapshot.objective.total,
        },
        "checker": {
            key: checked.get(key)
            for key in ("feasible", "stage", "violations", "obj1", "obj2", "obj3", "objective")
        },
        "telemetry": telemetry,
    }


def run_fault_suite() -> dict[str, object]:
    """Run focused fault/default/dominance tests in-process for immutable evidence."""
    suite = unittest.defaultTestLoader.loadTestsFromName(
        "tests.test_native_repair_integration"
    )
    stream = io.StringIO()
    result = unittest.TextTestRunner(stream=stream, verbosity=1).run(suite)
    return {
        "tests_run": result.testsRun,
        "failures": len(result.failures),
        "errors": len(result.errors),
        "skipped": len(result.skipped),
        "successful": result.wasSuccessful(),
        "output": stream.getvalue(),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--fixture", type=Path, required=True)
    parser.add_argument("--golden", type=Path, required=True)
    parser.add_argument("--p4-current-summary", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        raise SystemExit(f"refusing to overwrite existing artifact root: {args.output}")

    fixture = json.loads(args.fixture.read_text())
    golden = json.loads(args.golden.read_text())
    p4 = json.loads(args.p4_current_summary.read_text())
    raw = json.loads((ROOT / fixture["instance_path"]).read_text())
    python = run_repair(raw, fixture, backend="python", prefilter=False)
    native_off = run_repair(raw, fixture, backend="native", prefilter=False)
    native_on = run_repair(raw, fixture, backend="native", prefilter=True)
    fault_suite = run_fault_suite()
    repaired = golden["repair_result"]

    common = (python, native_off, native_on)
    stage_five = all(
        item["status"] == "FEASIBLE"
        and item["checker"]["feasible"] is True
        and item["checker"]["stage"] == 5
        and item["checker"]["violations"] == []
        for item in common
    )
    parity = all(
        item["placement_digest"] == repaired["final_placements_sha256"]
        and item["serialization_digest"] == repaired["serialization_sha256"]
        and item["objective"]["total"] == repaired["final_objective"]["total"]["value"]
        for item in common
    )
    p4_full_trace = bool(
        p4["fixed_work"]["off"]["parity"]
        and p4["fixed_work"]["on"]["parity"]
        and p4["fixed_work"]["on"]["false_free"] == 0
        and p4["p1_full_commit_placement_objective_serialization_trace"]["matches_golden"]
        and p4["native_randomized_boundary_differential"]["false_free"] == 0
    )
    native_active = (
        native_off["telemetry"]["actual_backend"] == "native"
        and native_on["telemetry"]["actual_backend"] == "native"
        and native_off["telemetry"]["native_successes"] == 10
        and native_on["telemetry"]["native_successes"] == 10
    )
    default_python = (
        python["telemetry"]["requested_backend"] == "python"
        and python["telemetry"]["actual_backend"] == "python"
        and python["telemetry"]["native_available"] is None
        and python["telemetry"]["native_calls"] == 0
    )
    prefilter_observable = (
        native_off["telemetry"]["prefilter_enabled"] is False
        and native_on["telemetry"]["prefilter_enabled"] is True
        and native_off["telemetry"]["definitely_free_pair_count"] == 0
        and native_on["telemetry"]["definitely_free_pair_count"] > 0
        and native_on["telemetry"]["exact_relation_calls"]
        < native_off["telemetry"]["exact_relation_calls"]
    )
    dominance = all(
        item["telemetry"]["original_anchor_objective"]
        >= item["telemetry"]["final_objective"]
        and item["telemetry"]["dominance_guard_rejected"] is False
        for item in (native_off, native_on)
    )
    checks = {
        "fixed_work_stage5": stage_five,
        "p1_placement_objective_serialization_parity": parity,
        "p4_current_full_trace_and_false_free": p4_full_trace,
        "python_default_selected": default_python,
        "native_opt_in_active": native_active,
        "prefilter_observable_and_conservative": prefilter_observable,
        "fault_injection_and_fallback": fault_suite["successful"],
        "native_anchor_dominance_loss_zero": dominance,
    }
    integration = {
        "schema_version": 1,
        "kind": "pybind_p5_repair_backend_integration_fixed_work",
        "scope": "P5 fixed-work integration only; production default remains Python. No P6 packaging or P7 fixed-time matrix was run.",
        "fixture": {"path": str(args.fixture), "sha256": sha256(args.fixture)},
        "golden": {"path": str(args.golden), "sha256": sha256(args.golden)},
        "p4_current_summary": {
            "path": str(args.p4_current_summary),
            "sha256": sha256(args.p4_current_summary),
        },
        "python_default": python,
        "native_prefilter_off": native_off,
        "native_prefilter_on": native_on,
        "checks": checks,
    }
    gate = {
        "schema_version": 1,
        "phase": "P5",
        "status": "PASS" if all(checks.values()) else "FAIL",
        "checks": checks,
        "contracts": {
            "production_default": "python",
            "native_opt_in": "ConstructorConfig/SubmissionConfig repair_backend='native' only",
            "exact_geometry": "UNKNOWN pairs remain Python GeometryKernel.relation/Shapely; C++ P4 only emits DEFINITELY_FREE.",
            "failure": "native import/OSError/runtime/invalid-output/deadline routes to same-input Python generator; its failure returns no candidates so repair returns the immutable input snapshot.",
            "dominance": "native opt-in rejects a completed repair if its final objective is worse than the frozen repair anchor.",
            "telemetry": "RepairResult telemetry records requested/actual backend, availability/fallback, P5 timing/count/bytes/RSS, anchor/final digests, and P4 prefilter state.",
        },
        "limitations": [
            "macOS arm64 CPython 3.12 local extension evidence is not Ubuntu 24.04 x86_64 CPython 3.12 ABI evidence.",
            "No P6 packaging/ABI or P7 fixed-time quality matrix was run.",
            "The dominance guard is benchmark-opt-in safety behavior; Python-default ALNS acceptance semantics remain unchanged.",
        ],
    }
    args.output.mkdir(parents=True)
    # Preserve the freshly rerun P4 differential summary inside the immutable
    # P5 root instead of relying on a temporary build directory after handoff.
    (args.output / "p4-current-summary.json").write_text(
        json.dumps(p4, indent=2, sort_keys=True) + "\n"
    )
    (args.output / "integration.json").write_text(
        json.dumps(integration, indent=2, sort_keys=True) + "\n"
    )
    (args.output / "fault-injection.json").write_text(
        json.dumps(fault_suite, indent=2, sort_keys=True) + "\n"
    )
    (args.output / "gate.json").write_text(
        json.dumps(gate, indent=2, sort_keys=True) + "\n"
    )
    manifest = "".join(
        f"{sha256(path)}  {path.name}\n"
        for path in sorted(args.output.glob("*.json"))
    )
    (args.output / "SHA256SUMS").write_text(manifest)
    print(json.dumps({"status": gate["status"], "checks": checks}, sort_keys=True))
    return 0 if gate["status"] == "PASS" else 2


if __name__ == "__main__":
    raise SystemExit(main())
