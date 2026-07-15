"""Profile heuristic repair on the frozen hard-10 without changing solver semantics."""

from __future__ import annotations

import argparse
import copy
import cProfile
import hashlib
import io
import json
import os
import pstats
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
from baseline.solver.entry import solve  # noqa: E402
from baseline.solver.geometry import GeometryKernel  # noqa: E402
from baseline.solver.instance import parse_instance  # noqa: E402
from baseline.solver.neighborhoods import (  # noqa: E402
    NeighborhoodContext,
    TardyChainDestroy,
    heuristic_repair,
)
from baseline.solver.runtime import RunTrace, SubmissionConfig  # noqa: E402
from baseline.solver.serialize import serialize  # noqa: E402
from baseline.utils import check_feasibility  # noqa: E402
from experiments.ogc_sage.benchmark_ogc_sage import (  # noqa: E402
    HARD10_MANIFEST,
    canonical_json,
    hard10_instance_names,
    reconstruct_snapshot,
    sha256_file,
    validate_dataset,
)


def _peak_rss_bytes() -> int:
    value = int(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss)
    # Darwin reports bytes; Linux and most BSDs report KiB.
    return value if sys.platform == "darwin" else value * 1024


def _profile_text(paths: list[Path], sort: str, limit: int = 120) -> str:
    stream = io.StringIO()
    stats = pstats.Stats(*(str(path) for path in paths), stream=stream)
    stats.strip_dirs().sort_stats(sort).print_stats(limit)
    return stream.getvalue()


def _placements_digest(snapshot) -> str:
    payload = [
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
    return hashlib.sha256(canonical_json(payload).encode("utf-8")).hexdigest()


def _trace_totals(trace: RunTrace) -> dict[str, Any]:
    document = trace.as_dict()
    invocations = document["lns_invocations"]
    return {
        "phase_times": document["phase_times"],
        "iterations": sum(int(item["iterations"]) for item in invocations),
        "repair_seconds": sum(float(item["repair_seconds"]) for item in invocations),
        "retime_seconds": sum(float(item["retime_seconds"]) for item in invocations),
        "lns_invocations": invocations,
        "validated_best": document["validated_best"],
    }


def profile_hard10(
    *,
    output: Path,
    solve_budget: float,
    repair_budget: float,
    seed: int,
    destroy_size: int,
) -> dict[str, Any]:
    instances, dataset_hash = validate_dataset()
    names = hard10_instance_names()
    output.mkdir(parents=True, exist_ok=False)
    solve_profile_paths: list[Path] = []
    repair_profile_paths: list[Path] = []
    rows: list[dict[str, Any]] = []

    for index, name in enumerate(names, 1):
        raw = json.loads(instances[name].read_text(encoding="utf-8"))
        trace = RunTrace()
        solve_profiler = cProfile.Profile()
        started = time.monotonic()
        operations = solve_profiler.runcall(
            solve,
            raw,
            solve_budget,
            config=SubmissionConfig.for_benchmark("heuristic_lns", seed=seed),
            trace=trace,
        )
        solve_elapsed = time.monotonic() - started
        solve_profile = output / f"{index:02d}-{name[:-5]}-solve.prof"
        solve_profiler.dump_stats(solve_profile)
        solve_profile_paths.append(solve_profile)

        checked = check_feasibility(copy.deepcopy(raw), copy.deepcopy(operations))
        snapshot = reconstruct_snapshot(raw, operations)
        instance = parse_instance(raw)
        kernel = GeometryKernel.from_instance(instance)
        context = NeighborhoodContext(
            instance,
            kernel,
            ConstructorConfig(max_profiles=1),
        )
        destroyed = TardyChainDestroy().select(
            snapshot,
            destroy_size,
            __import__("random").Random(seed),
            context,
        )
        cache_before = kernel.cache_info()
        repair_profiler = cProfile.Profile()
        repair_started = time.monotonic()
        repaired = repair_profiler.runcall(
            heuristic_repair,
            snapshot,
            destroyed,
            context,
            Budget.start(repair_budget),
        )
        repair_elapsed = time.monotonic() - repair_started
        repair_profile = output / f"{index:02d}-{name[:-5]}-repair.prof"
        repair_profiler.dump_stats(repair_profile)
        repair_profile_paths.append(repair_profile)
        cache_after = kernel.cache_info()

        repaired_stage = None
        repaired_objective = None
        if repaired.feasible:
            repaired_operations = serialize(repaired.snapshot, kernel)
            repaired_checked = check_feasibility(
                copy.deepcopy(raw), copy.deepcopy(repaired_operations)
            )
            repaired_stage = repaired_checked.get("stage")
            repaired_objective = repaired_checked.get("objective")

        trace_totals = _trace_totals(trace)
        rows.append(
            {
                "instance": name,
                "instance_sha256": sha256_file(instances[name]),
                "solve_budget_seconds": solve_budget,
                "solve_elapsed_seconds": solve_elapsed,
                "solve_stage": checked.get("stage"),
                "solve_objective": checked.get("objective"),
                "solve_snapshot_sha256": _placements_digest(snapshot),
                "solve_trace": trace_totals,
                "destroyed_ids": list(destroyed),
                "repair_budget_seconds": repair_budget,
                "repair_elapsed_seconds": repair_elapsed,
                "repair_status": repaired.status,
                "repair_stage": repaired_stage,
                "repair_objective": repaired_objective,
                "repair_snapshot_sha256": _placements_digest(repaired.snapshot),
                "repair_candidates_generated": repaired.candidates_generated,
                "repair_changed_ids": sorted(repaired.changed_ids),
                "repair_objective_delta": repaired.objective_delta,
                "relation_cache_hits": cache_after.hits - cache_before.hits,
                "relation_cache_misses": cache_after.misses - cache_before.misses,
                "peak_rss_bytes": _peak_rss_bytes(),
                "solve_profile": solve_profile.name,
                "repair_profile": repair_profile.name,
            }
        )
        print(
            canonical_json(
                {
                    "progress": f"{index}/{len(names)}",
                    "instance": name,
                    "repair_status": repaired.status,
                    "repair_seconds": repair_elapsed,
                }
            ),
            flush=True,
        )

    solve_cumulative = output / "solve-cumulative.txt"
    solve_self = output / "solve-self.txt"
    repair_cumulative = output / "repair-cumulative.txt"
    repair_self = output / "repair-self.txt"
    solve_cumulative.write_text(
        _profile_text(solve_profile_paths, "cumulative"), encoding="utf-8"
    )
    solve_self.write_text(_profile_text(solve_profile_paths, "tottime"), encoding="utf-8")
    repair_cumulative.write_text(
        _profile_text(repair_profile_paths, "cumulative"), encoding="utf-8"
    )
    repair_self.write_text(
        _profile_text(repair_profile_paths, "tottime"), encoding="utf-8"
    )
    pstats.Stats(*(str(path) for path in solve_profile_paths)).dump_stats(
        output / "solve-hard10.prof"
    )
    pstats.Stats(*(str(path) for path in repair_profile_paths)).dump_stats(
        output / "repair-hard10.prof"
    )

    document = {
        "schema_version": 1,
        "kind": "phase4_hard10_repair_profile",
        "pid": os.getpid(),
        "seed": seed,
        "destroy_size": destroy_size,
        "dataset_sha256": dataset_hash,
        "hard10_manifest": str(HARD10_MANIFEST.relative_to(REPO_ROOT)),
        "hard10_manifest_sha256": sha256_file(HARD10_MANIFEST),
        "instances": rows,
    }
    summary = output / "summary.json"
    summary.write_text(json.dumps(document, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    artifacts = sorted(path for path in output.iterdir() if path.is_file())
    checksums = output / "SHA256SUMS"
    checksums.write_text(
        "".join(f"{sha256_file(path)}  {path.name}\n" for path in artifacts),
        encoding="utf-8",
    )
    return document


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser()
    result.add_argument("--output", required=True, type=Path)
    result.add_argument("--solve-budget", type=float, default=60.0)
    result.add_argument("--repair-budget", type=float, default=120.0)
    result.add_argument("--seed", type=int, default=20260710)
    result.add_argument("--destroy-size", type=int, default=4)
    return result


def main() -> int:
    args = parser().parse_args()
    profile_hard10(
        output=args.output,
        solve_budget=args.solve_budget,
        repair_budget=args.repair_budget,
        seed=args.seed,
        destroy_size=args.destroy_size,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
