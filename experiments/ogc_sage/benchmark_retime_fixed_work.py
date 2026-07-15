"""Hard-10 fixed-work parity for the exact Z1 lower-bound retime skip."""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import math
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from experiments.ogc_sage.benchmark_ogc_sage import (
    HARD10_MANIFEST,
    canonical_json,
    config_hash,
    hard10_instance_names,
    sha256_file,
    validate_dataset,
)
from baseline.solver.budget import Budget
from baseline.solver.fallback import build_safe_candidate
from baseline.solver.geometry import GeometryKernel
from baseline.solver.instance import parse_instance
from baseline.solver.retime import BackendResult, RetimingConfig, retime
from baseline.solver.serialize import serialize
from baseline.utils import check_feasibility


class IdentityBackend:
    def __init__(self) -> None:
        self.calls = 0

    def solve(self, request, instance, time_limit, config):
        del time_limit, config
        self.calls += 1
        by_id = {item.block_id: item for item in request.snapshot.placements}
        return BackendResult(
            status="OPTIMAL",
            dates=tuple(
                (block_id, by_id[block_id].entry, by_id[block_id].exit)
                for block_id in sorted(request.modeled_ids)
            ),
            primary=float(
                sum(
                    max(0, by_id[block_id].exit - instance.block(block_id).due_date)
                    for block_id in request.modeled_ids
                )
            ),
            bound=0.0,
            gap=0.0,
        )


def objective_tuple(result):
    objective = result.snapshot.objective
    return (objective.z1, objective.z2, objective.z3, objective.total)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    instances, dataset_hash = validate_dataset()
    rows = []
    for name in hard10_instance_names():
        raw = json.loads(instances[name].read_text(encoding="utf-8"))
        parsed = parse_instance(raw)
        snapshot = build_safe_candidate(parsed, Budget.start(10.0))
        affected = frozenset(item.block_id for item in snapshot.placements)
        legacy_backend = IdentityBackend()
        exact_backend = IdentityBackend()
        legacy = retime(
            snapshot,
            parsed,
            GeometryKernel.from_instance(parsed),
            Budget.start(120.0),
            affected_ids=affected,
            backend_factory=lambda: legacy_backend,
            config=RetimingConfig(exact_z1_skip=False),
        )
        exact = retime(
            snapshot,
            parsed,
            GeometryKernel.from_instance(parsed),
            Budget.start(120.0),
            affected_ids=affected,
            backend_factory=lambda: exact_backend,
            config=RetimingConfig(exact_z1_skip=True),
        )
        legacy_ops = serialize(legacy.snapshot, GeometryKernel.from_instance(parsed))
        exact_ops = serialize(exact.snapshot, GeometryKernel.from_instance(parsed))
        legacy_check = check_feasibility(copy.deepcopy(raw), copy.deepcopy(legacy_ops))
        exact_check = check_feasibility(copy.deepcopy(raw), copy.deepcopy(exact_ops))
        parity = (
            legacy.snapshot.placements == exact.snapshot.placements
            and objective_tuple(legacy) == objective_tuple(exact)
            and legacy_ops == exact_ops
            and legacy_check.get("feasible") is True
            and exact_check.get("feasible") is True
            and legacy_check.get("stage") == exact_check.get("stage") == 5
            and all(
                math.isclose(
                    float(legacy_check[key]),
                    float(exact_check[key]),
                    rel_tol=1e-6,
                    abs_tol=1e-9,
                )
                for key in ("obj1", "obj2", "obj3", "objective")
            )
        )
        rows.append(
            {
                "instance": name,
                "parity": parity,
                "legacy_status": legacy.status,
                "exact_status": exact.status,
                "legacy_backend_calls": legacy_backend.calls,
                "exact_backend_calls": exact_backend.calls,
                "exact_skipped_components": int(
                    exact.telemetry.get("exact_skipped_components", 0)
                ),
                "objective": exact.snapshot.objective.total,
                "operations_sha256": hashlib.sha256(
                    canonical_json(exact_ops).encode("utf-8")
                ).hexdigest(),
            }
        )
    document = {
        "schema_version": 1,
        "dataset_sha256": dataset_hash,
        "hard10_manifest_sha256": sha256_file(HARD10_MANIFEST),
        "legacy_config_sha256": config_hash(
            "heuristic_lns", retime_exact_skip_mode="legacy"
        ),
        "exact_config_sha256": config_hash(
            "heuristic_lns", retime_exact_skip_mode="exact"
        ),
        "all_parity": all(row["parity"] for row in rows),
        "legacy_backend_calls": sum(row["legacy_backend_calls"] for row in rows),
        "exact_backend_calls": sum(row["exact_backend_calls"] for row in rows),
        "exact_skipped_components": sum(
            row["exact_skipped_components"] for row in rows
        ),
        "rows": rows,
    }
    output = Path(args.output).resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(document, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(canonical_json({"output": str(output), "sha256": sha256_file(output), **{key: document[key] for key in ("all_parity", "legacy_backend_calls", "exact_backend_calls", "exact_skipped_components")}}))
    return 0 if document["all_parity"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
