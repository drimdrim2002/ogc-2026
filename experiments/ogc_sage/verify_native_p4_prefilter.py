#!/usr/bin/env python3
"""P4 ON/OFF fixed-work verifier; production repair remains Python-only."""

from __future__ import annotations

import argparse
import hashlib
import json
import random
import resource
import statistics
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from baseline.solver.budget import Budget
from baseline.solver.construct import ConstructorConfig, generate_insertion_candidates
from baseline.solver.geometry import GeometryKernel, PairState
from baseline.solver.instance import parse_instance
from baseline.solver.native_repair import NativeRepairAdapter
from baseline.solver.serialize import serialize
from baseline.solver.state import IndexedSolutionState, Placement, compute_objective
from baseline.utils import check_feasibility
from experiments.ogc_sage.benchmark_ogc_sage import canonical_json


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def digest_value(value: object) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def percentile(values: list[int], fraction: float) -> float:
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


def score_record(item: object) -> dict[str, object]:
    return {
        "row": [item.placement.block_id, item.placement.bay_id, item.placement.orient_idx,
                item.placement.x, item.placement.y, item.placement.entry, item.placement.exit],
        "total_hex": item.total_delta.hex(), "tardiness_hex": item.tardiness_delta.hex(),
        "assignment_hex": item.assignment_delta.hex(), "fragmentation": item.fragmentation,
        "tie": [value.hex() if isinstance(value, float) else value for value in item.canonical_tie],
        "state_version": item.state_version,
    }


def expected_pairs(state: IndexedSolutionState, rows: list[list[int]]) -> tuple[list[int], list[list[int]]]:
    offsets = [0]
    pairs: list[list[int]] = []
    for row in rows:
        pairs.extend(
            [[item.block_id, item.bay_id, item.orient_idx, item.x, item.y, item.entry, item.exit]
             for item in state.co_present(row[1], (row[5], row[6]))]
        )
        offsets.append(len(pairs))
    return offsets, pairs


def unknown_queries(prepared: object, verdicts: list[bool]) -> list[dict[str, list[int]]]:
    """Return only Python relation calls that reached the exact oracle."""
    flags = prepared.pair_definitely_free
    queries: list[dict[str, list[int]]] = []
    for candidate_index, row in enumerate(prepared.rows):
        rejected = False
        for pair_index in range(prepared.pair_offsets[candidate_index],
                                prepared.pair_offsets[candidate_index + 1]):
            if rejected:
                continue
            if not flags[pair_index]:
                queries.append({"left": list(row), "right": list(prepared.pairs[pair_index])})
            if not verdicts[pair_index]:
                rejected = True
    return queries


def work_items(instance: object, fixture: dict[str, object], golden: dict[str, object]):
    caps = fixture["fixed_work_caps"]
    states: dict[int, list[list[int]]] = {0: fixture["destroy"]["retained_placements"]}
    for commit in golden["commits"]:
        states[commit["state_version_after"]] = commit["state_rows_after"]
    items = []
    for expected in golden["candidate_calls"]:
        state = IndexedSolutionState(
            instance, [Placement(*row) for row in states[expected["state_version"]]],
            time_cap=caps["time_cap"], anchor_cap=caps["anchor_cap"], lattice_cap=caps["lattice_cap"],
        )
        state.version = expected["state_version"]
        current = expected["current_placement"]
        items.append((expected, state, None if current is None else Placement(*current)))
    return items


def config(caps: dict[str, object]) -> ConstructorConfig:
    return ConstructorConfig(**{key: caps[key] for key in (
        "seed", "time_cap", "escalated_time_cap", "anchor_cap", "lattice_cap",
        "max_profiles", "max_candidate_attempts", "selection_policy",
    )})


def verify_p1_commit_and_serialization_trace(
    raw: dict[str, object], instance: object, fixture: dict[str, object], golden: dict[str, object]
) -> dict[str, object]:
    """Replay native-provided selections through unchanged Python commit/serializer."""
    caps = fixture["fixed_work_caps"]
    kernel = GeometryKernel.from_instance(instance)
    state = IndexedSolutionState(
        instance, [Placement(*row) for row in fixture["destroy"]["retained_placements"]],
        time_cap=caps["time_cap"], anchor_cap=caps["anchor_cap"], lattice_cap=caps["lattice_cap"],
    )
    commits = []
    for expected in golden["commits"]:
        placement = Placement(*expected["candidate"]["placement"])
        free_before_commit = all(
            kernel.relation(placement, retained).state is PairState.FREE
            for retained in state.co_present(placement.bay_id, (placement.entry, placement.exit))
        )
        committed = state.transactional_insert(placement)
        rows = [[item.block_id, item.bay_id, item.orient_idx, item.x, item.y, item.entry, item.exit]
                for item in state.placements]
        commits.append({
            "ordinal": expected["commit_ordinal"], "free_before_commit": free_before_commit,
            "committed": committed, "state_version": state.version,
            "state_rows_sha256": digest_value(rows),
            "matches_golden": (
                free_before_commit and committed
                and state.version == expected["state_version_after"]
                and rows == expected["state_rows_after"]
                and digest_value(rows) == expected["state_rows_sha256_after"]
            ),
        })
    snapshot = state.freeze().with_objective(compute_objective(instance, state.freeze()))
    operations = serialize(snapshot, kernel)
    checked = check_feasibility(raw, operations)
    repaired = golden["repair_result"]
    snapshot_rows = [[item.block_id, item.bay_id, item.orient_idx, item.x, item.y, item.entry, item.exit]
                     for item in snapshot.placements]
    return {
        "commits": commits,
        "all_commit_rows_and_versions_match": all(item["matches_golden"] for item in commits),
        "final_placements_sha256": digest_value(snapshot_rows),
        "serialization_sha256": digest_value(operations),
        "final_objective": snapshot.objective.total,
        "official_checker": {key: checked.get(key) for key in ("stage", "objective", "obj1", "obj2", "obj3", "violations")},
        "matches_golden": (
            digest_value(snapshot_rows) == repaired["final_placements_sha256"]
            and digest_value(operations) == repaired["serialization_sha256"]
            and snapshot.objective.total == repaired["final_objective"]["total"]["value"]
            and checked.get("stage") == repaired["official_checker"]["stage"]
            and checked.get("objective") == repaired["official_checker"]["objective"]
            and checked.get("violations") == repaired["official_checker"]["violations"]
        ),
    }


def native_prefilter_flag(
    adapter: NativeRepairAdapter, instance: object, left: Placement, right: Placement
) -> bool:
    """Exercise the C++ P4 flag for one controlled candidate/retained pair."""
    packed = adapter._module.PackedState(  # type: ignore[attr-defined]
        [adapter._row(right)], [0.0 for _ in instance.bays], 0, len(instance.bays)  # type: ignore[attr-defined]
    )
    prepared = adapter._module.prepare_candidates(  # type: ignore[attr-defined]
        adapter._problem, packed, left.block_id, adapter._row(left), 48, 48, 512, True  # type: ignore[attr-defined]
    )
    wanted_left = adapter._row(left)  # type: ignore[attr-defined]
    wanted_right = adapter._row(right)  # type: ignore[attr-defined]
    for candidate_index, row in enumerate(prepared.rows):
        if tuple(row) != wanted_left:
            continue
        for pair_index in range(prepared.pair_offsets[candidate_index], prepared.pair_offsets[candidate_index + 1]):
            if tuple(prepared.pairs[pair_index]) == wanted_right:
                return bool(prepared.pair_definitely_free[pair_index])
    raise AssertionError("controlled P4 pair was not enumerated")


def native_randomized_boundary_differential(
    instance: object, golden: dict[str, object]
) -> dict[str, object]:
    """Compare actual C++ flags with Shapely at randomized/contact offsets."""
    kernel = GeometryKernel.from_instance(instance)
    adapter = NativeRepairAdapter(instance, kernel, prefilter_enabled=True)
    bases: dict[tuple[int, int, int, int], tuple[Placement, Placement]] = {}
    for call in golden["candidate_calls"]:
        for query in call["relation_queries"]:
            if query["phase"] == "generate":
                left, right = Placement(*query["left"]), Placement(*query["right"])
                bases.setdefault((left.block_id, left.orient_idx, right.block_id, right.orient_idx), (left, right))
    rng = random.Random(20260716)
    false_free = covered = 0
    for (_, _, right_id, right_orient), (left, right) in sorted(bases.items()):
        left_shape = kernel.shape(left.block_id, left.orient_idx).full_aabb
        right_shape = kernel.shape(right_id, right_orient).full_aabb
        boundary = {
            int(left_shape[0] - right_shape[2]), int(left_shape[2] - right_shape[0]),
            int(left_shape[1] - right_shape[3]), int(left_shape[3] - right_shape[1]),
        }
        translations = [(x, y) for x in boundary for y in boundary]
        translations.extend((rng.randint(-96, 128), rng.randint(-96, 128)) for _ in range(64))
        for dx, dy in translations:
            shifted = Placement(
                right.block_id, left.bay_id, right.orient_idx, left.x + dx, left.y + dy,
                left.entry, left.exit,
            )
            marked = native_prefilter_flag(adapter, instance, left, shifted)
            covered += 1
            if marked and kernel.relation(left, shifted).state is not PairState.FREE:
                false_free += 1
    return {
        "seed": 20260716, "orientation_pairs": len(bases), "pairs": covered,
        "false_free": false_free,
        "method": "Actual C++ prepare_candidates pair_definitely_free flag versus GeometryKernel.relation/Shapely; contact offsets plus 64 deterministic random translations per orientation pair.",
    }


def replay_variant(
    instance: object, fixture: dict[str, object], golden: dict[str, object], *, enabled: bool
) -> dict[str, object]:
    kernel = GeometryKernel.from_instance(instance)
    adapter = NativeRepairAdapter(instance, kernel, prefilter_enabled=enabled)
    caps = fixture["fixed_work_caps"]
    all_parity = True
    false_free = 0
    pair_flags = 0
    unknown_count = 0
    per_call = []
    offered: dict[int, set[tuple[int, ...]]] = {}
    for expected, state, current in work_items(instance, fixture, golden):
        prepared = adapter.prepare(state, expected["block_id"], current,
                                   attempt_cap=caps["max_candidate_attempts"])
        verdicts = adapter.exact_verdicts(prepared, kernel)
        native = adapter.candidate_scores(prepared, verdicts, current_placement=current,
                                          state=state, kernel=kernel)
        reference = generate_insertion_candidates(
            state, expected["block_id"], kernel, Budget.start(10.0),
            current_placement=current, config=config(caps),
        )
        actual_rows = [list(row) for row in prepared.raw.rows]
        actual_offsets = list(prepared.raw.pair_offsets)
        actual_pairs = [list(pair) for pair in prepared.raw.pairs]
        wanted_offsets, wanted_pairs = expected_pairs(state, actual_rows)
        wanted_queries = [
            {"left": item["left"], "right": item["right"]}
            for item in expected["relation_queries"] if item["phase"] == "generate"
        ]
        actual_unknown = unknown_queries(prepared.raw, verdicts)
        # ON is an ordered subsequence of the original exact trace; OFF must
        # be byte-for-byte the P3 exact query sequence.
        iterator = iter(wanted_queries)
        subsequence = all(any(query == candidate for candidate in iterator) for query in actual_unknown)
        flags = [bool(value) for value in prepared.raw.pair_definitely_free]
        if len(flags) != len(actual_pairs):
            raise RuntimeError("prefilter flag cardinality mismatch")
        # The pair above cannot compare a candidate. Check every marked pair
        # with its candidate row while preserving the original pair offsets.
        for row_index, row in enumerate(actual_rows):
            for pair_index in range(actual_offsets[row_index], actual_offsets[row_index + 1]):
                if flags[pair_index]:
                    pair_flags += 1
                    if kernel.relation(Placement(*row), Placement(*actual_pairs[pair_index])).state is not PairState.FREE:
                        false_free += 1
        parity = (
            actual_rows == [item["row"] for item in expected["enumeration_before_exact"]]
            and wanted_offsets == actual_offsets and wanted_pairs == actual_pairs
            and [score_record(item) for item in reference] == [score_record(item) for item in native]
            and subsequence and (enabled or actual_unknown == wanted_queries)
        )
        all_parity = all_parity and parity
        unknown_count += len(actual_unknown)
        offered.setdefault(state.version, set()).update(tuple(record["row"]) for record in (score_record(item) for item in native))
        per_call.append({
            "ordinal": expected["call_ordinal"], "parity": parity,
            "rows": len(actual_rows), "pairs": len(actual_pairs),
            "unknown_exact_calls": len(actual_unknown),
            "definitely_free_pairs": sum(flags),
        })
    commit_selection = all(
        tuple(commit["candidate"]["placement"]) in offered[commit["state_version_before"]]
        for commit in golden["commits"]
    )
    return {
        "enabled": enabled, "parity": all_parity, "false_free": false_free,
        "pair_definitely_free": pair_flags, "unknown_exact_calls": unknown_count,
        "per_call": per_call, "commit_selection_provided": commit_selection,
        "cache": kernel.cache_info()._asdict(),
    }


def timing_variant(adapter: NativeRepairAdapter, kernel: GeometryKernel, items: list[tuple[dict[str, object], IndexedSolutionState, Placement | None]], caps: dict[str, object]) -> dict[str, int]:
    prepare_total = kernel_ns = exact = finalize = exact_calls = definitely_free = 0
    for expected, state, current in items:
        started = time.perf_counter_ns()
        prepared = adapter.prepare(state, expected["block_id"], current,
                                   attempt_cap=caps["max_candidate_attempts"])
        prepare_total += time.perf_counter_ns() - started
        kernel_ns += prepared.raw.kernel_ns
        started = time.perf_counter_ns()
        verdicts = adapter.exact_verdicts(prepared, kernel)
        exact += time.perf_counter_ns() - started
        started = time.perf_counter_ns()
        prepared.raw.finalize(verdicts)
        finalize += time.perf_counter_ns() - started
        flags = prepared.raw.pair_definitely_free
        for candidate_index in range(len(prepared.raw.rows)):
            rejected = False
            for pair_index in range(prepared.raw.pair_offsets[candidate_index], prepared.raw.pair_offsets[candidate_index + 1]):
                if rejected:
                    continue
                if flags[pair_index]:
                    definitely_free += 1
                else:
                    exact_calls += 1
                if not verdicts[pair_index]:
                    rejected = True
    return {
        "prepare_total_ns": prepare_total, "kernel_ns": kernel_ns,
        "boundary_ns": prepare_total - kernel_ns, "exact_resolve_ns": exact,
        "finalize_ns": finalize, "exact_relation_calls": exact_calls,
        "definitely_free_consumed": definitely_free,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--fixture", type=Path, required=True)
    parser.add_argument("--golden", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--warmups", type=int, default=8)
    parser.add_argument("--repeats", type=int, default=31)
    parser.add_argument("--summary-name", default="p4-implementation-summary.json")
    args = parser.parse_args()
    fixture = json.loads(args.fixture.read_text())
    golden = json.loads(args.golden.read_text())
    raw = json.loads((ROOT / fixture["instance_path"]).read_text())
    instance = parse_instance(raw)
    off = replay_variant(instance, fixture, golden, enabled=False)
    on = replay_variant(instance, fixture, golden, enabled=True)
    commit_serialization = verify_p1_commit_and_serialization_trace(raw, instance, fixture, golden)
    native_differential = native_randomized_boundary_differential(instance, golden)
    caps = fixture["fixed_work_caps"]
    off_kernel, on_kernel = GeometryKernel.from_instance(instance), GeometryKernel.from_instance(instance)
    off_adapter = NativeRepairAdapter(instance, off_kernel, prefilter_enabled=False)
    on_adapter = NativeRepairAdapter(instance, on_kernel, prefilter_enabled=True)
    timing_items = work_items(instance, fixture, golden)
    rss_before = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    for _ in range(args.warmups):
        timing_variant(off_adapter, off_kernel, timing_items, caps)
        timing_variant(on_adapter, on_kernel, timing_items, caps)
    paired = []
    for ordinal in range(args.repeats):
        paired.append({
            "ordinal": ordinal,
            "off": timing_variant(off_adapter, off_kernel, timing_items, caps),
            "on": timing_variant(on_adapter, on_kernel, timing_items, caps),
        })
    rss_after = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    fields = ("prepare_total_ns", "kernel_ns", "boundary_ns", "exact_resolve_ns", "finalize_ns")
    timing = {
        variant: {field: stats([item[variant][field] for item in paired]) for field in fields}
        for variant in ("off", "on")
    }
    exact_calls = {variant: sorted(set(item[variant]["exact_relation_calls"] for item in paired)) for variant in ("off", "on")}
    free_pairs = {variant: sorted(set(item[variant]["definitely_free_consumed"] for item in paired)) for variant in ("off", "on")}
    result = {
        "schema_version": 1,
        "kind": "pybind_p4_conservative_prefilter_fixed_work",
        "fixture_sha256": digest(args.fixture), "golden_sha256": digest(args.golden),
        "scope": "P4 benchmark-only ON/OFF variant; Python remains production default and exact GeometryKernel.relation/Shapely resolves every UNKNOWN.",
        "fixed_work": {"off": off, "on": on},
        "p1_full_commit_placement_objective_serialization_trace": commit_serialization,
        "native_randomized_boundary_differential": native_differential,
        "timing_method": {
            "work": "All ten P1 candidate calls/states, fixed attempt caps; ON/OFF use separate warm adapters and GeometryKernel caches.",
            "order": "OFF then ON per paired repeat after equal warmups.",
            "repeats": args.repeats, "warmups_per_variant": args.warmups,
            "definitions": "prepare includes cache lookup/pybind call; kernel is C++ prepare_candidates; exact-resolve is NativeRepairAdapter.exact_verdicts; finalize is PreparedCandidateBatch.finalize without Python rollback-column re-evaluation.",
        },
        "timing_ns": timing,
        "exact_relation_calls": exact_calls,
        "definitely_free_consumed": free_pairs,
        "cache": {"off": off_kernel.cache_info()._asdict(), "on": on_kernel.cache_info()._asdict()},
        "rss_max": {"unit": "bytes on macOS ru_maxrss", "before": rss_before, "after": rss_after, "delta": rss_after - rss_before},
        "paired_samples": paired,
    }
    args.output.mkdir(parents=True, exist_ok=True)
    summary = args.output / args.summary_name
    if summary.exists():
        raise FileExistsError(summary)
    summary.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")
    checksum = digest(summary)
    print(json.dumps({"off_parity": off["parity"], "on_parity": on["parity"], "false_free": on["false_free"], "summary_sha256": checksum}, sort_keys=True))
    return 0 if off["parity"] and on["parity"] and on["false_free"] == 0 and commit_serialization["matches_golden"] and native_differential["false_free"] == 0 else 2


if __name__ == "__main__":
    raise SystemExit(main())
