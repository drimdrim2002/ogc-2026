"""Fixed-work semantic and throughput benchmark for hard-10 heuristic repair."""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import resource
import sys
import time
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from baseline.solver.budget import Budget  # noqa: E402
from baseline.solver.construct import ConstructorConfig  # noqa: E402
from baseline.solver.fallback import build_safe_candidate  # noqa: E402
from baseline.solver.geometry import GeometryKernel  # noqa: E402
from baseline.solver.instance import parse_instance  # noqa: E402
from baseline.solver.neighborhoods import (  # noqa: E402
    NeighborhoodContext,
    TardyChainDestroy,
    heuristic_repair,
)
from baseline.solver.serialize import serialize  # noqa: E402
from baseline.utils import check_feasibility  # noqa: E402
from experiments.ogc_sage.benchmark_ogc_sage import (  # noqa: E402
    HARD10_MANIFEST,
    canonical_json,
    environment_versions,
    hard10_instance_names,
    sha256_file,
    validate_dataset,
)


def _peak_rss_bytes() -> int:
    value = int(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss)
    return value if sys.platform == "darwin" else value * 1024


def _digest(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def _snapshot_payload(snapshot) -> list[list[int]]:
    return [
        [
            item.block_id,
            item.bay_id,
            item.orient_idx,
            item.x,
            item.y,
            item.entry,
            item.exit,
        ]
        for item in snapshot.placements
    ]


def _measure(
    *,
    raw: dict[str, Any],
    snapshot,
    destroyed: tuple[int, ...],
    context: NeighborhoodContext,
    repair_budget: float,
    temperature: str,
) -> dict[str, Any]:
    cache_before = context.kernel.cache_info()
    started = time.monotonic()
    result = heuristic_repair(
        snapshot,
        destroyed,
        context,
        Budget.start(repair_budget),
    )
    elapsed = time.monotonic() - started
    cache_after = context.kernel.cache_info()
    operations = serialize(result.snapshot, context.kernel)
    checked = check_feasibility(copy.deepcopy(raw), copy.deepcopy(operations))
    relation_hits = cache_after.hits - cache_before.hits
    relation_misses = cache_after.misses - cache_before.misses
    relation_calls = relation_hits + relation_misses
    return {
        "temperature": temperature,
        "elapsed_seconds": elapsed,
        "status": result.status,
        "stage": checked.get("stage"),
        "objective": checked.get("objective"),
        "obj1": checked.get("obj1"),
        "obj2": checked.get("obj2"),
        "obj3": checked.get("obj3"),
        "objective_delta": result.objective_delta,
        "destroyed_ids": list(result.destroyed_ids),
        "changed_ids": sorted(result.changed_ids),
        "candidates_generated": result.candidates_generated,
        "candidates_per_second": result.candidates_generated / max(elapsed, 1e-12),
        "relation_calls": relation_calls,
        "relation_hits": relation_hits,
        "relation_misses": relation_misses,
        "exact_checks_per_second": relation_misses / max(elapsed, 1e-12),
        "snapshot_sha256": _digest(_snapshot_payload(result.snapshot)),
        "serialization_sha256": _digest(operations),
        "peak_rss_bytes": _peak_rss_bytes(),
        "diagnostics": list(result.diagnostics),
    }


def benchmark(
    *,
    output: Path,
    label: str,
    seed: int,
    destroy_size: int,
    repair_budget: float,
) -> dict[str, Any]:
    instances, dataset_hash = validate_dataset()
    output.mkdir(parents=True, exist_ok=False)
    rows: list[dict[str, Any]] = []

    for index, name in enumerate(hard10_instance_names(), 1):
        raw = json.loads(instances[name].read_text(encoding="utf-8"))
        instance = parse_instance(raw)
        snapshot = build_safe_candidate(instance, Budget.start(10.0))
        selector_kernel = GeometryKernel.from_instance(instance)
        selector_context = NeighborhoodContext(instance, selector_kernel)
        destroyed = TardyChainDestroy().select(
            snapshot,
            destroy_size,
            __import__("random").Random(seed),
            selector_context,
        )

        kernel = GeometryKernel.from_instance(instance)
        context = NeighborhoodContext(
            instance,
            kernel,
            ConstructorConfig(max_profiles=1),
        )
        cold = _measure(
            raw=raw,
            snapshot=snapshot,
            destroyed=destroyed,
            context=context,
            repair_budget=repair_budget,
            temperature="cold",
        )
        warm = _measure(
            raw=raw,
            snapshot=snapshot,
            destroyed=destroyed,
            context=context,
            repair_budget=repair_budget,
            temperature="warm",
        )
        rows.append(
            {
                "instance": name,
                "instance_sha256": sha256_file(instances[name]),
                "fixture_snapshot_sha256": _digest(_snapshot_payload(snapshot)),
                "destroyed_ids": list(destroyed),
                "cold": cold,
                "warm": warm,
            }
        )
        print(
            canonical_json(
                {
                    "progress": f"{index}/10",
                    "instance": name,
                    "cold_seconds": cold["elapsed_seconds"],
                    "warm_seconds": warm["elapsed_seconds"],
                }
            ),
            flush=True,
        )

    document = {
        "schema_version": 1,
        "kind": "phase4_heuristic_repair_fixed_work",
        "label": label,
        "seed": seed,
        "destroy_size": destroy_size,
        "repair_budget_seconds": repair_budget,
        "dataset_sha256": dataset_hash,
        "hard10_manifest": str(HARD10_MANIFEST.relative_to(REPO_ROOT)),
        "hard10_manifest_sha256": sha256_file(HARD10_MANIFEST),
        "environment": environment_versions(),
        "instances": rows,
    }
    summary = output / "summary.json"
    summary.write_text(json.dumps(document, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    checksums = output / "SHA256SUMS"
    checksums.write_text(
        f"{sha256_file(summary)}  {summary.name}\n", encoding="utf-8"
    )
    return document


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser()
    result.add_argument("--output", required=True, type=Path)
    result.add_argument("--label", required=True)
    result.add_argument("--seed", type=int, default=20260710)
    result.add_argument("--destroy-size", type=int, default=4)
    result.add_argument("--repair-budget", type=float, default=120.0)
    return result


def main() -> int:
    args = parser().parse_args()
    benchmark(
        output=args.output,
        label=args.label,
        seed=args.seed,
        destroy_size=args.destroy_size,
        repair_budget=args.repair_budget,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
