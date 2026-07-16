#!/usr/bin/env python3
"""P3 fixed-work differential runner; it never selects the production backend."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import statistics
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from baseline.solver.construct import ConstructorConfig, generate_insertion_candidates
from baseline.solver.geometry import GeometryKernel
from baseline.solver.instance import parse_instance
from baseline.solver.native_repair import NativeRepairAdapter
from baseline.solver.state import IndexedSolutionState, Placement
from baseline.solver.budget import Budget


def canonical(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def score_record(item: object) -> dict[str, object]:
    if isinstance(item, tuple):
        row = item
        return {
            "row": list(row[:7]), "total_hex": row[7].hex(),
            "tardiness_hex": row[8].hex(), "assignment_hex": row[9].hex(),
            "fragmentation": row[10],
            "tie": [row[7].hex(), row[10], 0, row[6], row[5], row[1], row[2], row[3], row[4], row[0]],
            "state_version": row[11],
        }
    return {
        "row": [item.placement.block_id, item.placement.bay_id, item.placement.orient_idx,
                item.placement.x, item.placement.y, item.placement.entry, item.placement.exit],
        "total_hex": item.total_delta.hex(), "tardiness_hex": item.tardiness_delta.hex(),
        "assignment_hex": item.assignment_delta.hex(), "fragmentation": item.fragmentation,
        "tie": [x.hex() if isinstance(x, float) else x for x in item.canonical_tie],
        "state_version": item.state_version,
    }


def exact_query_order(prepared: object, verdicts: list[bool]) -> list[dict[str, object]]:
    """Return only relations actually consumed under Python first-failure flow."""
    queries: list[dict[str, object]] = []
    for candidate_index, row in enumerate(prepared.rows):
        for pair_index in range(prepared.pair_offsets[candidate_index],
                                prepared.pair_offsets[candidate_index + 1]):
            queries.append({"left": list(row), "right": list(prepared.pairs[pair_index])})
            if not verdicts[pair_index]:
                break
    return queries


def percentile(values: list[int], fraction: float) -> float:
    """Linear percentile for a small, explicitly recorded timing sample."""
    ordered = sorted(values)
    index = (len(ordered) - 1) * fraction
    lower = int(index)
    upper = min(lower + 1, len(ordered) - 1)
    return ordered[lower] + (ordered[upper] - ordered[lower]) * (index - lower)


def stats(values: list[int]) -> dict[str, float | int]:
    return {
        "count": len(values),
        "min_ns": min(values),
        "median_ns": statistics.median(values),
        "max_ns": max(values),
        "p10_ns": percentile(values, 0.10),
        "p90_ns": percentile(values, 0.90),
    }


def python_target_measurement(
    state: IndexedSolutionState,
    block_id: int,
    current: Placement,
    kernel: GeometryKernel,
    config: ConstructorConfig,
) -> dict[str, int]:
    """Time the Python prepare-equivalent, excluding exact relation time.

    This leaves Python candidate generation, fit bounds, indexed-state lookups,
    score calculation, and canonical selection in the denominator.  The actual
    GeometryKernel.relation/Shapely duration is measured and excluded because
    P3 deliberately retains it in Python.  The rollback-column timing is also
    reported separately so it is not credited to the native kernel.
    """
    relation_ns = 0
    original_relation = kernel.relation

    def measured_relation(*args: object, **kwargs: object) -> object:
        nonlocal relation_ns
        started = time.perf_counter_ns()
        try:
            return original_relation(*args, **kwargs)
        finally:
            relation_ns += time.perf_counter_ns() - started

    kernel.relation = measured_relation  # type: ignore[method-assign]
    try:
        started = time.perf_counter_ns()
        generate_insertion_candidates(
            state, block_id, kernel, Budget.start(10.0),
            current_placement=current, config=config,
        )
        total_ns = time.perf_counter_ns() - started
    finally:
        kernel.relation = original_relation  # type: ignore[method-assign]
    return {
        "python_total_ns": total_ns,
        "python_exact_relation_ns": relation_ns,
        "python_target_ns": total_ns - relation_ns,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--fixture", type=Path, required=True)
    parser.add_argument("--golden", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--repeats", type=int, default=9)
    parser.add_argument("--timing-warmups", type=int, default=8)
    parser.add_argument("--timing-repeats", type=int, default=31)
    args = parser.parse_args()
    fixture = json.loads(args.fixture.read_text())
    golden = json.loads(args.golden.read_text())
    raw = json.loads((ROOT / fixture["instance_path"]).read_text())
    instance = parse_instance(raw)
    kernel = GeometryKernel.from_instance(instance)
    adapter = NativeRepairAdapter(instance, kernel)
    initial = fixture["destroy"]["retained_placements"]
    states: dict[int, list[list[int]]] = {0: initial}
    for commit in golden["commits"]:
        states[commit["state_version_after"]] = commit["state_rows_after"]
    caps = fixture["fixed_work_caps"]
    calls: list[dict[str, object]] = []
    all_parity = True
    for expected in golden["candidate_calls"]:
        version = expected["state_version"]
        state = IndexedSolutionState(instance, [Placement(*row) for row in states[version]],
                                    time_cap=caps["time_cap"], anchor_cap=caps["anchor_cap"],
                                    lattice_cap=caps["lattice_cap"])
        state.version = version
        current = expected["current_placement"]
        current_placement = None if current is None else Placement(*current)
        prepared = adapter.prepare(state, expected["block_id"], current_placement,
                                   attempt_cap=caps["max_candidate_attempts"])
        started = time.perf_counter_ns()
        verdicts = adapter.exact_verdicts(prepared, kernel)
        exact_ns = time.perf_counter_ns() - started
        started = time.perf_counter_ns()
        native = adapter.candidate_scores(prepared, verdicts, current_placement=current_placement,
                                          state=state, kernel=kernel)
        finalize_ns = time.perf_counter_ns() - started
        reference = generate_insertion_candidates(
            state, expected["block_id"], kernel, Budget.start(10.0),
            current_placement=current_placement,
            config=ConstructorConfig(**{key: caps[key] for key in (
                "seed", "time_cap", "escalated_time_cap", "anchor_cap", "lattice_cap",
                "max_profiles", "max_candidate_attempts", "selection_policy")}))
        expected_rows = [item["row"] for item in expected["enumeration_before_exact"]]
        actual_rows = [list(row) for row in prepared.raw.rows]
        expected_scores = [score_record(item) for item in reference]
        actual_scores = [score_record(item) for item in native]
        expected_queries = [
            {"left": item["left"], "right": item["right"]}
            for item in expected["relation_queries"] if item["phase"] == "generate"
        ]
        actual_queries = exact_query_order(prepared.raw, verdicts)
        expected_pair_offsets = [0]
        expected_pairs: list[list[int]] = []
        for row in actual_rows:
            expected_pairs.extend(
                [[item.block_id, item.bay_id, item.orient_idx, item.x, item.y,
                  item.entry, item.exit]
                 for item in state.co_present(row[1], (row[5], row[6]))]
            )
            expected_pair_offsets.append(len(expected_pairs))
        actual_pair_offsets = list(prepared.raw.pair_offsets)
        actual_pairs = [list(item) for item in prepared.raw.pairs]
        parity = (expected_rows == actual_rows and expected_scores == actual_scores
                  and expected_pair_offsets == actual_pair_offsets
                  and expected_pairs == actual_pairs
                  and expected_queries == actual_queries)
        all_parity = all_parity and parity
        calls.append({"ordinal": expected["call_ordinal"], "state_version": version,
                      "rows": len(actual_rows), "pairs": len(prepared.raw.pairs),
                      "exact_query_count": len(actual_queries), "parity": parity,
                      "kernel_ns": prepared.raw.kernel_ns,
                      "exact_resolve_ns": exact_ns, "finalize_ns": finalize_ns,
                      "expected_rows": expected_rows, "actual_rows": actual_rows,
                      "expected_pair_offsets": expected_pair_offsets,
                      "actual_pair_offsets": actual_pair_offsets,
                      "expected_pairs": expected_pairs, "actual_pairs": actual_pairs,
                      "expected_queries": expected_queries, "actual_queries": actual_queries,
                      "expected_scores": expected_scores, "actual_scores": actual_scores})
    # Repeated batch measurements use one fixed call/state (P1 call 0) and
    # alternate Python then native after warmup.  They are evidence only; the
    # controller records the external CPU-load audit before deciding whether
    # they are gate-quality.
    state = IndexedSolutionState(instance, [Placement(*row) for row in initial],
                                time_cap=caps["time_cap"], anchor_cap=caps["anchor_cap"], lattice_cap=caps["lattice_cap"])
    current = Placement(*fixture["destroy"]["current_placements"][3])
    timing_config = ConstructorConfig(**{key: caps[key] for key in (
        "seed", "time_cap", "escalated_time_cap", "anchor_cap", "lattice_cap",
        "max_profiles", "max_candidate_attempts", "selection_policy")})
    cold_started = time.perf_counter_ns()
    cold_prepared = adapter.prepare(state, 96, current, attempt_cap=caps["max_candidate_attempts"])
    cold_prepare_ns = time.perf_counter_ns() - cold_started
    cold_pack = {
        "native_prepare_total_ns": cold_prepare_ns,
        "native_kernel_ns": cold_prepared.raw.kernel_ns,
        "native_bind_boundary_ns": cold_prepare_ns - cold_prepared.raw.kernel_ns,
        "definition": "First prepare for this new Python state object/version; includes Python row/load extraction and pybind PackedState construction. Excluded from warm same-version boundary gate.",
    }
    for _ in range(args.timing_warmups):
        python_target_measurement(state, 96, current, kernel, timing_config)
        adapter.prepare(state, 96, current, attempt_cap=caps["max_candidate_attempts"])
    paired_timing = []
    for ordinal in range(args.timing_repeats):
        python_timing = python_target_measurement(state, 96, current, kernel, timing_config)
        bind_started = time.perf_counter_ns()
        prepared = adapter.prepare(state, 96, current, attempt_cap=caps["max_candidate_attempts"])
        bind_prepare_ns = time.perf_counter_ns() - bind_started
        exact_started = time.perf_counter_ns()
        verdicts = adapter.exact_verdicts(prepared, kernel)
        exact_resolve_ns = time.perf_counter_ns() - exact_started
        finalize_started = time.perf_counter_ns()
        adapter.candidate_scores(prepared, verdicts, current_placement=current, state=state, kernel=kernel)
        finalize_ns = time.perf_counter_ns() - finalize_started
        paired_timing.append({
            "ordinal": ordinal,
            **python_timing,
            "native_prepare_total_ns": bind_prepare_ns,
            "native_kernel_ns": prepared.raw.kernel_ns,
            "native_bind_boundary_ns": bind_prepare_ns - prepared.raw.kernel_ns,
            "exact_resolve_ns": exact_resolve_ns,
            "finalize_ns": finalize_ns,
        })
    repeats = []
    for _ in range(args.repeats):
        started = time.perf_counter_ns()
        prepared = adapter.prepare(state, 96, current, attempt_cap=caps["max_candidate_attempts"])
        bind_prepare_ns = time.perf_counter_ns() - started
        repeats.append({"prepare_total_ns": bind_prepare_ns, "kernel_ns": prepared.raw.kernel_ns,
                        "boundary_ns": bind_prepare_ns - prepared.raw.kernel_ns})
    result = {"schema_version": 1, "kind": "pybind_p3_fixed_work",
              "fixture_sha256": digest(args.fixture), "golden_sha256": digest(args.golden),
              "native_module_dir": os.environ.get("OGC_NATIVE_MODULE_DIR"),
              "candidate_order_score_top3_state_version_parity": all_parity,
              "calls": calls, "repeated_prepare": repeats,
              "timing_method": {
                  "work_identity": "P1 fixture call 0: state_version=0, block_id=96, current placement [96,1,0,9,1,1250,1273], attempt_cap=32",
                  "warmups_per_variant": args.timing_warmups,
                  "paired_alternating_order": "python_target_then_native_prepare_then_exact_resolve_then_finalize",
                  "repeats": args.timing_repeats,
                  "python_target_definition": "generate_insertion_candidates elapsed minus only GeometryKernel.relation elapsed; it retains Python candidate/time/position enumeration, fits, state lookup, scoring, canonical selection, and rollback-column work",
                  "native_kernel_definition": "PreparedCandidateBatch.kernel_ns from prepare_candidates; excludes Python pack/cache lookup, pybind call boundary, exact GeometryKernel.relation, and Python finalize/rollback",
                  "boundary_formula": "median(native_bind_boundary_ns) / median(native_prepare_total_ns) * 100; native_prepare_total_ns = native_bind_boundary_ns + native_kernel_ns",
              },
              "paired_timing": paired_timing,
              "cold_pack": cold_pack,
              "paired_timing_summary": {
                  "python_target": stats([item["python_target_ns"] for item in paired_timing]),
                  "python_exact_relation": stats([item["python_exact_relation_ns"] for item in paired_timing]),
                  "native_prepare_total": stats([item["native_prepare_total_ns"] for item in paired_timing]),
                  "native_kernel": stats([item["native_kernel_ns"] for item in paired_timing]),
                  "native_bind_boundary": stats([item["native_bind_boundary_ns"] for item in paired_timing]),
                  "exact_resolve": stats([item["exact_resolve_ns"] for item in paired_timing]),
                  "finalize": stats([item["finalize_ns"] for item in paired_timing]),
              },
              "note": "No production backend was changed; exact GeometryKernel.relation remains Python."}
    args.output.mkdir(parents=True, exist_ok=False)
    output = args.output / "summary.json"
    output.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")
    checksum = digest(output)
    (args.output / "SHA256SUMS").write_text(f"{checksum}  summary.json\n")
    print(canonical({"parity": all_parity, "summary_sha256": checksum}))
    return 0 if all_parity else 2


if __name__ == "__main__":
    raise SystemExit(main())
