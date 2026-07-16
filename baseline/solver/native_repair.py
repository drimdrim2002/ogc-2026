"""Native repair adapter with a Python-authoritative safety seam.

After the Phase 4 GO release gate, callers use the ``native`` repair backend
and native exact-free decisions by default. Unsupported imports and runtime
errors still fall back to the Python reference. Python rechecks the returned
top three and owns rollback, transactional commit, serialization, full
checking, and the frozen-anchor dominance guard.
"""

from __future__ import annotations

import hashlib
import importlib
import json
import math
import os
import resource
import sys
import time
from collections.abc import Callable
from contextlib import contextmanager
from dataclasses import dataclass
from types import ModuleType
from typing import TypeVar


T = TypeVar("T")
_NATIVE_MODULE_DIR_ENV = "OGC_NATIVE_MODULE_DIR"
_ROW_BYTES = 7 * 8


@contextmanager
def _configured_module_dir():
    """Temporarily expose an explicit local spike output, if requested."""
    directory = os.environ.get(_NATIVE_MODULE_DIR_ENV)
    if not directory:
        yield
        return
    sys.path.insert(0, directory)
    try:
        yield
    finally:
        try:
            sys.path.remove(directory)
        except ValueError:
            pass


def native_module_status() -> tuple[ModuleType | None, str | None]:
    """Return optional module plus a compact, silent unavailability reason."""
    first_error: BaseException | None = None
    try:
        return importlib.import_module("solver._ogc_native"), None
    except (ImportError, OSError) as exc:
        first_error = exc
    if os.environ.get(_NATIVE_MODULE_DIR_ENV):
        try:
            with _configured_module_dir():
                return importlib.import_module("_ogc_native"), None
        except (ImportError, OSError) as exc:
            first_error = exc
    if first_error is None:
        return None, "native_import_unavailable"
    return None, f"native_import_{type(first_error).__name__}"


def load_native_module() -> ModuleType | None:
    """Return the optional module, treating ABI/import failures as unavailable.

    No diagnostics are printed: public submission calls must remain silent and
    P5 records controlled telemetry at the repair boundary instead.
    """
    return native_module_status()[0]


def empty_kernel_info() -> dict[str, object] | None:
    """Validate the P2 module ABI surface, or return ``None`` when absent."""
    module = load_native_module()
    if module is None:
        return None
    try:
        info = module.empty_kernel_info()
    except Exception:
        return None
    if not isinstance(info, dict) or info.get("api_version") != 1:
        return None
    return info


def with_python_fallback(
    native_call: Callable[[ModuleType], T], python_reference: Callable[[], T]
) -> T:
    """Run optional native work only when available, otherwise use reference."""
    module = load_native_module()
    if module is None:
        return python_reference()
    try:
        return native_call(module)
    except Exception:
        return python_reference()


def _vertices(geometry: object) -> list[tuple[float, float]]:
    if geometry is None or getattr(geometry, "is_empty", True):
        return []
    exterior = getattr(geometry, "exterior", None)
    if exterior is not None:
        return [(float(x), float(y)) for x, y in exterior.coords]
    result: list[tuple[float, float]] = []
    for item in getattr(geometry, "geoms", ()):
        result.extend(_vertices(item))
    return result


def _payload_bytes_estimate(value: object) -> int:
    """Bounded payload estimate that counts binary WKB without hex expansion."""
    if isinstance(value, bytes):
        return len(value)
    if isinstance(value, dict):
        return sum(
            len(str(key).encode("utf-8")) + _payload_bytes_estimate(item)
            for key, item in value.items()
        )
    if isinstance(value, (list, tuple)):
        return sum(_payload_bytes_estimate(item) for item in value)
    return len(str(value).encode("utf-8"))


def pack_static_problem(instance: object, kernel: object) -> dict[str, object]:
    """Copy Python/Shapely-owned geometry for numeric native work only."""
    return {
        "weights": {
            "w1": instance.weights.w1,
            "w2": instance.weights.w2,
            "w3": instance.weights.w3,
        },
        "bays": [{"width": bay.width, "height": bay.height} for bay in instance.bays],
        "blocks": [
            {
                "release": block.release_time,
                "due": block.due_date,
                "dwell": block.dwell,
                "workload": block.workload,
                "preferences": list(block.bay_preferences),
                "orientations": [
                    {
                        "xmin": shape.full_aabb[0],
                        "ymin": shape.full_aabb[1],
                        "xmax": shape.full_aabb[2],
                        "ymax": shape.full_aabb[3],
                        "vertices": _vertices(shape.union),
                        "layer_aabbs": [
                            None if aabb is None else list(aabb)
                            for aabb in shape.layer_aabbs
                        ],
                        "suffix_aabbs": [
                            None if aabb is None else list(aabb)
                            for aabb in shape.suffix_aabbs
                        ],
                        "layers_wkb": [
                            None if layer is None else bytes(layer.wkb)
                            for layer in shape.layers
                        ],
                        "suffix_unions_wkb": [
                            bytes(item.wkb) for item in shape.suffix_unions
                        ],
                    }
                    for shape in (
                        kernel.shape(block.index, orient.index)
                        for orient in block.orientations
                    )
                ],
            }
            for block in instance.blocks
        ],
    }


@dataclass(frozen=True, slots=True)
class NativePreparedBatch:
    raw: object
    state_version: int
    pack_ns: int
    bind_ns: int
    prepare_ns: int
    packed_bytes_estimate: int
    # pybind readonly vector properties materialize a fresh Python list on
    # every access.  Keep one boundary copy per chunk so validation, exact
    # resolution, and finalization do not repeatedly copy the same buffers.
    rows: tuple[object, ...] | None = None
    pairs: tuple[object, ...] | None = None
    pair_offsets: tuple[int, ...] | None = None
    pair_definitely_free: tuple[object, ...] | None = None
    total: tuple[object, ...] | None = None
    tardiness: tuple[object, ...] | None = None
    assignment: tuple[object, ...] | None = None
    fragmentation: tuple[object, ...] | None = None
    guide_penalty: tuple[object, ...] | None = None


@dataclass(frozen=True, slots=True)
class NativeRepairCursor:
    raw: object
    state_version: int
    pack_ns: int
    bind_ns: int
    prepare_ns: int
    packed_bytes_estimate: int


@dataclass(frozen=True, slots=True)
class NativeCandidateBatch:
    raw: object
    state_version: int
    bind_ns: int
    prepare_ns: int
    rows: tuple[object, ...]
    pairs: tuple[object, ...]
    pair_offsets: tuple[int, ...]
    total: tuple[object, ...]
    tardiness: tuple[object, ...]
    assignment: tuple[object, ...]
    fragmentation: tuple[object, ...]
    guide_penalty: tuple[object, ...]


def _materialize_cursor_batch(raw: object) -> tuple[tuple[object, ...], ...]:
    return (
        tuple(raw.rows),
        tuple(raw.pairs),
        tuple(raw.pair_offsets),
        tuple(raw.total),
        tuple(raw.tardiness),
        tuple(raw.assignment),
        tuple(raw.fragmentation),
        tuple(raw.guide_penalty),
    )


def _materialize_prepared(raw: object) -> tuple[tuple[object, ...], ...]:
    return (
        tuple(raw.rows),
        tuple(raw.pairs),
        tuple(raw.pair_offsets),
        tuple(raw.pair_definitely_free),
        tuple(raw.total),
        tuple(raw.tardiness),
        tuple(raw.assignment),
        tuple(raw.fragmentation),
        tuple(raw.guide_penalty),
    )


def _prepared_buffers(
    prepared: NativePreparedBatch,
) -> tuple[tuple[object, ...], ...]:
    cached = (
        prepared.rows,
        prepared.pairs,
        prepared.pair_offsets,
        prepared.pair_definitely_free,
        prepared.total,
        prepared.tardiness,
        prepared.assignment,
        prepared.fragmentation,
        prepared.guide_penalty,
    )
    if all(value is not None for value in cached):
        return tuple(value for value in cached if value is not None)
    return _materialize_prepared(prepared.raw)


class NativeRepairAdapter:
    """Native adapter with explicit Python-exact and native-exact paths."""

    def __init__(
        self,
        instance: object,
        kernel: object,
        *,
        prefilter_enabled: bool = False,
    ) -> None:
        module = load_native_module()
        if module is None:
            raise RuntimeError("native module is unavailable")
        info = module.empty_kernel_info()
        if not isinstance(info, dict) or info.get("repair_api_version") != 5:
            raise RuntimeError("native repair API version mismatch")
        self._module = module
        static_spec = pack_static_problem(instance, kernel)
        # This is an explicit payload-size estimate, not an ABI-size claim.
        self.static_packed_bytes_estimate = _payload_bytes_estimate(static_spec)
        self._problem = module.StaticProblem(static_spec)
        # P4 ON can only mark a pair definitely free.  All UNKNOWN pairs keep
        # their existing Python GeometryKernel.relation/Shapely authority.
        self._prefilter_enabled = bool(prefilter_enabled)
        self._packed_states: dict[tuple[int, int], object] = {}

    def exact_verdict(
        self, candidate: object, existing: object
    ) -> tuple[str, bool, int]:
        verdict, cache_hit, exact_ns = self._problem.exact_verdict(
            self._row(candidate), self._row(existing)
        )
        if verdict not in {"FREE", "BLOCKED", "ERROR"}:
            raise ValueError("native exact verdict is invalid")
        if type(cache_hit) is not bool:
            raise ValueError("native exact cache result is invalid")
        if isinstance(exact_ns, bool) or not isinstance(exact_ns, int) or exact_ns < 0:
            raise ValueError("native exact timing is invalid")
        return verdict, cache_hit, exact_ns

    @staticmethod
    def _row(item: object) -> tuple[int, int, int, int, int, int, int]:
        return (
            item.block_id,
            item.bay_id,
            item.orient_idx,
            item.x,
            item.y,
            item.entry,
            item.exit,
        )

    @staticmethod
    def _packed_state_bytes(state: object) -> int:
        # PackedState stores a contiguous row vector and a by-bay row copy;
        # loads are copied once.  This deliberately reports an estimate.
        return 2 * len(state.placements) * _ROW_BYTES + len(state.raw_bay_loads) * 8

    def prepare(
        self,
        state: object,
        block_id: int,
        current_placement: object | None,
        *,
        attempt_cap: int,
    ) -> NativePreparedBatch:
        total_started = time.perf_counter_ns()
        key = (id(state), state.version)
        packed = self._packed_states.get(key)
        pack_ns = 0
        packed_bytes = 0
        if packed is None:
            pack_started = time.perf_counter_ns()
            packed = self._module.PackedState(
                [self._row(item) for item in state.placements],
                list(state.raw_bay_loads),
                state.version,
                len(state.instance.bays),
            )
            pack_ns = max(0, time.perf_counter_ns() - pack_started)
            packed_bytes = self._packed_state_bytes(state)
            # One state object cannot mutate without a new version.  Keep only
            # the active version cache to bound memory deterministically.
            self._packed_states = {key: packed}
        raw = self._module.prepare_candidates(
            self._problem,
            packed,
            block_id,
            None if current_placement is None else self._row(current_placement),
            state.time_cap,
            state.anchor_cap,
            attempt_cap,
            self._prefilter_enabled,
        )
        buffers = _materialize_prepared(raw)
        total_ns = max(0, time.perf_counter_ns() - total_started)
        prepare_ns = int(raw.kernel_ns)
        bind_ns = max(0, total_ns - pack_ns - prepare_ns)
        return NativePreparedBatch(
            raw=raw,
            state_version=state.version,
            pack_ns=pack_ns,
            bind_ns=bind_ns,
            prepare_ns=prepare_ns,
            packed_bytes_estimate=packed_bytes,
            rows=buffers[0],
            pairs=buffers[1],
            pair_offsets=buffers[2],
            pair_definitely_free=buffers[3],
            total=buffers[4],
            tardiness=buffers[5],
            assignment=buffers[6],
            fragmentation=buffers[7],
            guide_penalty=buffers[8],
        )

    def prepare_chunk(
        self,
        state: object,
        block_id: int,
        current_placement: object | None,
        *,
        time_cap: int,
        position_cap: int,
        fitting_option_index: int,
        entry_index: int,
        lattice_only: bool,
        remaining_seconds: float,
    ) -> NativePreparedBatch:
        """Prepare one canonical option/entry unit for exact-aware streaming."""
        total_started = time.perf_counter_ns()
        key = (id(state), state.version)
        packed = self._packed_states.get(key)
        pack_ns = 0
        packed_bytes = 0
        if packed is None:
            pack_started = time.perf_counter_ns()
            packed = self._module.PackedState(
                [self._row(item) for item in state.placements],
                list(state.raw_bay_loads),
                state.version,
                len(state.instance.bays),
            )
            pack_ns = max(0, time.perf_counter_ns() - pack_started)
            packed_bytes = self._packed_state_bytes(state)
            self._packed_states = {key: packed}
        raw = self._module.prepare_candidate_chunk(
            self._problem,
            packed,
            block_id,
            None if current_placement is None else self._row(current_placement),
            time_cap,
            position_cap,
            self._prefilter_enabled,
            fitting_option_index,
            entry_index,
            lattice_only,
            max(0.0, float(remaining_seconds)),
        )
        buffers = _materialize_prepared(raw)
        total_ns = max(0, time.perf_counter_ns() - total_started)
        prepare_ns = int(raw.kernel_ns)
        return NativePreparedBatch(
            raw=raw,
            state_version=state.version,
            pack_ns=pack_ns,
            bind_ns=max(0, total_ns - pack_ns - prepare_ns),
            prepare_ns=prepare_ns,
            packed_bytes_estimate=packed_bytes,
            rows=buffers[0],
            pairs=buffers[1],
            pair_offsets=buffers[2],
            pair_definitely_free=buffers[3],
            total=buffers[4],
            tardiness=buffers[5],
            assignment=buffers[6],
            fragmentation=buffers[7],
            guide_penalty=buffers[8],
        )

    def create_cursor(
        self,
        state: object,
        block_id: int,
        current_placement: object | None,
        *,
        time_cap: int,
        lattice_only: bool,
    ) -> NativeRepairCursor:
        """Create one stateful cursor; fitting options and entries are built once."""
        total_started = time.perf_counter_ns()
        key = (id(state), state.version)
        packed = self._packed_states.get(key)
        pack_ns = 0
        packed_bytes = 0
        if packed is None:
            pack_started = time.perf_counter_ns()
            packed = self._module.PackedState(
                [self._row(item) for item in state.placements],
                list(state.raw_bay_loads),
                state.version,
                len(state.instance.bays),
            )
            pack_ns = max(0, time.perf_counter_ns() - pack_started)
            packed_bytes = self._packed_state_bytes(state)
            self._packed_states = {key: packed}
        raw = self._module.RepairCursor(
            self._problem,
            packed,
            block_id,
            None if current_placement is None else self._row(current_placement),
            time_cap,
            state.anchor_cap,
            state.lattice_cap,
            self._prefilter_enabled,
            lattice_only,
        )
        total_ns = max(0, time.perf_counter_ns() - total_started)
        prepare_ns = int(raw.prepare_ns)
        return NativeRepairCursor(
            raw=raw,
            state_version=state.version,
            pack_ns=pack_ns,
            bind_ns=max(0, total_ns - pack_ns - prepare_ns),
            prepare_ns=prepare_ns,
            packed_bytes_estimate=packed_bytes,
        )

    @staticmethod
    def next_cursor_batch(
        cursor: NativeRepairCursor, remaining_seconds: float
    ) -> NativeCandidateBatch:
        started = time.perf_counter_ns()
        raw = cursor.raw.next_batch(
            max_rows=16,
            remaining_seconds=max(0.0, float(remaining_seconds)),
        )
        buffers = _materialize_cursor_batch(raw)
        elapsed = max(0, time.perf_counter_ns() - started)
        prepare_ns = int(raw.kernel_ns)
        return NativeCandidateBatch(
            raw=raw,
            state_version=cursor.state_version,
            bind_ns=max(0, elapsed - prepare_ns),
            prepare_ns=prepare_ns,
            rows=buffers[0],
            pairs=buffers[1],
            pair_offsets=buffers[2],
            total=buffers[3],
            tardiness=buffers[4],
            assignment=buffers[5],
            fragmentation=buffers[6],
            guide_penalty=buffers[7],
        )

    @staticmethod
    def run_exact_cursor(
        cursor: NativeRepairCursor, remaining_seconds: float
    ) -> NativeCandidateBatch:
        """Close candidate traversal and exact-free decisions inside C++."""
        started = time.perf_counter_ns()
        raw = cursor.raw.run_exact(
            remaining_seconds=max(0.0, float(remaining_seconds)),
        )
        buffers = _materialize_cursor_batch(raw)
        elapsed = max(0, time.perf_counter_ns() - started)
        kernel_ns = int(raw.kernel_ns)
        return NativeCandidateBatch(
            raw=raw,
            state_version=cursor.state_version,
            bind_ns=max(0, elapsed - kernel_ns),
            prepare_ns=0,
            rows=buffers[0],
            pairs=buffers[1],
            pair_offsets=buffers[2],
            total=buffers[3],
            tardiness=buffers[4],
            assignment=buffers[5],
            fragmentation=buffers[6],
            guide_penalty=buffers[7],
        )

    @staticmethod
    def consume_cursor_batch(
        cursor: NativeRepairCursor,
        verdicts: list[bool],
        processed_rows: int,
    ) -> None:
        cursor.raw.consume(verdicts, processed_rows)

    @staticmethod
    def finalize_cursor(cursor: NativeRepairCursor) -> NativeCandidateBatch:
        started = time.perf_counter_ns()
        raw = cursor.raw.finalize_top3()
        buffers = _materialize_cursor_batch(raw)
        elapsed = max(0, time.perf_counter_ns() - started)
        return NativeCandidateBatch(
            raw=raw,
            state_version=cursor.state_version,
            bind_ns=elapsed,
            prepare_ns=0,
            rows=buffers[0],
            pairs=buffers[1],
            pair_offsets=buffers[2],
            total=buffers[3],
            tardiness=buffers[4],
            assignment=buffers[5],
            fragmentation=buffers[6],
            guide_penalty=buffers[7],
        )

    def exact_cursor_batch_until_deadline(
        self,
        batch: NativeCandidateBatch,
        kernel: object,
        budget: object,
        deadline_margin: float,
        exact_mode: str,
    ) -> tuple[list[bool], int, bool, int, dict[str, object]]:
        from .geometry import PairState
        from .state import Placement

        verdicts = [False] * len(batch.pairs)
        processed_rows = 0
        exact_calls = 0
        shadow: dict[str, object] = {
            "checked_pairs": 0,
            "free_free": 0,
            "blocked_blocked": 0,
            "mismatches": 0,
            "geos_errors": 0,
            "python_exact_ns": 0,
            "cpp_exact_ns": 0,
            "cache_hits": 0,
            "cache_misses": 0,
            "first_mismatch": None,
        }
        for row_index, row in enumerate(batch.rows):
            if not budget.can_start(0.0, margin=deadline_margin):
                return verdicts, processed_rows, True, exact_calls, shadow
            candidate = Placement(*row)
            rejected = False
            for pair_index in range(
                batch.pair_offsets[row_index],
                batch.pair_offsets[row_index + 1],
            ):
                if rejected:
                    continue
                exact_calls += 1
                existing = Placement(*batch.pairs[pair_index])
                python_started = time.perf_counter_ns()
                verdicts[pair_index] = (
                    kernel.relation(candidate, existing).state is PairState.FREE
                )
                shadow["python_exact_ns"] = int(shadow["python_exact_ns"]) + max(
                    0, time.perf_counter_ns() - python_started
                )
                if exact_mode in {"shadow", "native"}:
                    cpp_verdict, cache_hit, cpp_exact_ns = self.exact_verdict(
                        candidate, existing
                    )
                    shadow["checked_pairs"] = int(shadow["checked_pairs"]) + 1
                    shadow["cpp_exact_ns"] = int(shadow["cpp_exact_ns"]) + cpp_exact_ns
                    cache_key = "cache_hits" if cache_hit else "cache_misses"
                    shadow[cache_key] = int(shadow[cache_key]) + 1
                    python_verdict = "FREE" if verdicts[pair_index] else "BLOCKED"
                    if cpp_verdict == "ERROR":
                        shadow["geos_errors"] = int(shadow["geos_errors"]) + 1
                    if cpp_verdict == python_verdict:
                        agreement = (
                            "free_free" if python_verdict == "FREE"
                            else "blocked_blocked"
                        )
                        shadow[agreement] = int(shadow[agreement]) + 1
                    else:
                        shadow["mismatches"] = int(shadow["mismatches"]) + 1
                        if shadow["first_mismatch"] is None:
                            shadow["first_mismatch"] = (
                                ("candidate", self._row(candidate)),
                                ("existing", self._row(existing)),
                                (
                                    "relative_translation",
                                    (existing.x - candidate.x, existing.y - candidate.y),
                                ),
                                ("python_verdict", python_verdict),
                                ("cpp_verdict", cpp_verdict),
                            )
                rejected = not verdicts[pair_index]
            processed_rows += 1
        return verdicts, processed_rows, False, exact_calls, shadow

    def exact_verdicts(self, prepared: NativePreparedBatch, kernel: object) -> list[bool]:
        from .geometry import PairState
        from .state import Placement

        verdicts: list[bool] = []
        rows, pairs, offsets, definitely_free, *_ = _prepared_buffers(prepared)
        if len(definitely_free) != len(pairs):
            raise RuntimeError("native prefilter flag length mismatch")
        for index, row in enumerate(rows):
            candidate = Placement(*row)
            rejected = False
            for pair_index in range(offsets[index], offsets[index + 1]):
                if rejected:
                    verdicts.append(False)
                    continue
                if definitely_free[pair_index]:
                    verdicts.append(True)
                    continue
                verdict = (
                    kernel.relation(candidate, Placement(*pairs[pair_index])).state
                    is PairState.FREE
                )
                verdicts.append(verdict)
                rejected = not verdict
        return verdicts

    def exact_verdicts_until_deadline(
        self,
        prepared: NativePreparedBatch,
        kernel: object,
        budget: object,
        deadline_margin: float,
    ) -> tuple[list[bool], int, bool]:
        """Resolve rows in Python's order, polling before row 0/16/32/... ."""
        from .geometry import PairState
        from .state import Placement

        rows, pairs, offsets, flags, *_ = _prepared_buffers(prepared)
        verdicts = [False] * len(pairs)
        processed_rows = 0
        for index, row in enumerate(rows):
            if index % 16 == 0 and not budget.can_start(
                0.0, margin=deadline_margin
            ):
                return verdicts, processed_rows, True
            candidate = Placement(*row)
            rejected = False
            for pair_index in range(offsets[index], offsets[index + 1]):
                if rejected:
                    continue
                if flags[pair_index]:
                    verdicts[pair_index] = True
                else:
                    verdicts[pair_index] = (
                        kernel.relation(candidate, Placement(*pairs[pair_index])).state
                        is PairState.FREE
                    )
                rejected = not verdicts[pair_index]
            processed_rows += 1
        return verdicts, processed_rows, False

    def finalize(
        self,
        prepared: NativePreparedBatch,
        verdicts: list[bool],
    ) -> tuple[tuple[object, ...], ...]:
        (
            rows,
            _,
            _,
            _,
            total,
            tardiness,
            assignment,
            fragmentation,
            guide_penalty,
        ) = _prepared_buffers(prepared)
        values = [
            (
                *rows[index],
                total[index],
                tardiness[index],
                assignment[index],
                fragmentation[index],
                guide_penalty[index],
                prepared.state_version,
            )
            for index in prepared.raw.finalize(verdicts)
        ]
        values.sort(
            key=lambda row: (
                row[7], row[10], row[11], row[6], row[5], row[1], row[2], row[3], row[4], row[0]
            )
        )
        return tuple(values[:3])

    def candidate_scores(
        self,
        prepared: NativePreparedBatch,
        verdicts: list[bool],
        *,
        current_placement: object | None,
        state: object,
        kernel: object,
    ) -> tuple[object, ...]:
        """Materialize P3 top-three, retaining Python's rollback-column rule."""
        from .construct import CandidateScore, evaluate_insert
        from .state import Placement

        scores = []
        for row in self.finalize(prepared, verdicts):
            placement = Placement(*row[:7])
            total, tardiness, assignment, fragmentation, guide_penalty, version = row[7:]
            scores.append(
                CandidateScore(
                    placement=placement,
                    total_delta=total,
                    tardiness_delta=tardiness,
                    assignment_delta=assignment,
                    fragmentation=fragmentation,
                    canonical_tie=(
                        total,
                        fragmentation,
                        guide_penalty,
                        placement.exit,
                        placement.entry,
                        placement.bay_id,
                        placement.orient_idx,
                        placement.x,
                        placement.y,
                        placement.block_id,
                    ),
                    state_version=version,
                )
            )
        if current_placement is not None:
            current = evaluate_insert(state, current_placement, kernel)
            if current is not None and not any(
                item.placement == current_placement for item in scores
            ):
                scores = scores[:2] + [current]
        return tuple(scores)


def _rss_bytes() -> int:
    value = int(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss)
    # macOS reports bytes, Linux reports KiB.  This remains a process peak,
    # explicitly not a native-exclusive allocation measurement.
    return value if sys.platform == "darwin" else value * 1024


def snapshot_digest(snapshot: object) -> str:
    rows = [
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
    encoded = json.dumps(rows, separators=(",", ":"), ensure_ascii=True).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


class NativeRepairSession:
    """One repair-attempt backend session with recoverable native execution.

    The session has no authority to mutate a state or incumbent.  Its only
    output is a candidate tuple; its caller still performs the existing exact
    transactional commit and final Stage-5 validation.
    """

    def __init__(
        self,
        instance: object,
        kernel: object,
        *,
        requested_backend: str = "python",
        prefilter_enabled: bool = False,
        adapter_factory: Callable[..., NativeRepairAdapter] = NativeRepairAdapter,
    ) -> None:
        self.requested_backend = requested_backend
        self.prefilter_enabled = bool(prefilter_enabled)
        self.native_available: bool | None = None
        self._adapter: NativeRepairAdapter | None = None
        self._timing = {
            "pack_ns": 0,
            "bind_ns": 0,
            "prepare_ns": 0,
            "exact_resolve_ns": 0,
            "finalize_ns": 0,
            "cursor_prepare_ns": 0,
            "cursor_exact_ns": 0,
            "cursor_finalize_ns": 0,
        }
        self._candidate_rows = 0
        self._stream_chunks = 0
        self._cursor_count = 0
        self._processed_rows = 0
        self._accepted_rows = 0
        self._returned_candidates = 0
        self._pairs = 0
        self._unknown_pairs = 0
        self._definitely_free_pairs = 0
        self._exact_relation_calls = 0
        self._python_exact_calls = 0
        self._native_exact_mode = "python"
        self._shadow_checked_pairs = 0
        self._shadow_free_free = 0
        self._shadow_blocked_blocked = 0
        self._shadow_mismatch_count = 0
        self._geos_error_count = 0
        self._python_exact_ns = 0
        self._cpp_exact_ns = 0
        self._exact_cache_hits = 0
        self._exact_cache_misses = 0
        self._aabb_skipped_pairs = 0
        self._geos_exact_calls = 0
        self._first_conflict_skipped_pairs = 0
        self._native_exact_ns = 0
        self._native_cache_ns = 0
        self._native_error_ns = 0
        self._returned_candidate_rechecks = 0
        self._returned_candidate_recheck_failures = 0
        self._native_error_count = 0
        self._first_shadow_mismatch: object | None = None
        self._copied_bytes_estimate = 0
        self._native_exception_count = 0
        self._native_invalid_output_count = 0
        self._native_deadline_count = 0
        self._fallback_count = 0
        self._fallback_reasons: dict[str, int] = {}
        self._fallback_reason: str | None = None
        self._native_calls = 0
        self._native_successes = 0
        self._python_calls = 0
        self._python_calls_after_deadline = 0
        self._python_full_repair_after_deadline = 0
        self._deadline_observed = False
        self._dominance_rejected = False
        self._anchor_objective: float | None = None
        self._anchor_digest: str | None = None
        self._final_objective: float | None = None
        self._final_digest: str | None = None
        self._peak_rss_bytes = _rss_bytes()
        self._deadline_margin = min(
            0.25, max(0.02, 0.0005 * len(instance.blocks))
        )

        if requested_backend == "python":
            return
        if requested_backend != "native":
            self._record_fallback("invalid_requested_backend")
            self.native_available = False
            return
        module, reason = native_module_status()
        self.native_available = module is not None
        if module is None:
            self._record_fallback(reason or "native_import_unavailable")
            return
        try:
            self._adapter = adapter_factory(
                instance, kernel, prefilter_enabled=self.prefilter_enabled
            )
            self._copied_bytes_estimate += int(
                getattr(self._adapter, "static_packed_bytes_estimate", 0)
            )
        except Exception as exc:
            self._native_exception_count += 1
            self._record_fallback(f"native_adapter_{type(exc).__name__}")

    @property
    def actual_backend(self) -> str:
        return "native" if self._native_successes else "python"

    def record_anchor(self, snapshot: object) -> None:
        objective = getattr(snapshot, "objective", None)
        self._anchor_objective = (
            float(objective.total) if objective is not None else None
        )
        self._anchor_digest = snapshot_digest(snapshot)

    def record_final(self, snapshot: object) -> None:
        objective = getattr(snapshot, "objective", None)
        self._final_objective = float(objective.total) if objective is not None else None
        self._final_digest = snapshot_digest(snapshot)
        self._peak_rss_bytes = max(self._peak_rss_bytes, _rss_bytes())

    def reject_dominance_loss(self) -> None:
        self._dominance_rejected = True

    def _record_fallback(self, reason: str) -> None:
        self._fallback_count += 1
        self._fallback_reason = reason
        self._fallback_reasons[reason] = self._fallback_reasons.get(reason, 0) + 1

    def _account_shadow(self, values: dict[str, object]) -> None:
        self._shadow_checked_pairs += int(values["checked_pairs"])
        self._shadow_free_free += int(values["free_free"])
        self._shadow_blocked_blocked += int(values["blocked_blocked"])
        self._shadow_mismatch_count += int(values["mismatches"])
        self._geos_error_count += int(values["geos_errors"])
        self._python_exact_ns += int(values["python_exact_ns"])
        self._cpp_exact_ns += int(values["cpp_exact_ns"])
        self._exact_cache_hits += int(values["cache_hits"])
        self._exact_cache_misses += int(values["cache_misses"])
        if self._first_shadow_mismatch is None:
            self._first_shadow_mismatch = values["first_mismatch"]

    def _deadline_reached(
        self, budget: object, margin: float = 0.0001
    ) -> bool:
        reached = not budget.can_start(0.0, margin=margin)
        if reached:
            self._deadline_observed = True
        return reached

    @staticmethod
    def _row(value: object, *, block_id: int | None = None) -> tuple[int, ...]:
        try:
            result = tuple(value)
        except TypeError as exc:
            raise ValueError("native row is not iterable") from exc
        if len(result) != 7 or any(
            isinstance(item, bool) or not isinstance(item, int) for item in result
        ):
            raise ValueError("native row is not seven int values")
        if block_id is not None and result[0] != block_id:
            raise ValueError("native row has an unexpected block id")
        # Placement validates duration and prevents invalid native rows from
        # reaching any later commit path.
        from .state import Placement

        Placement(*result)
        return result

    def _validate_prepared(self, prepared: object, state: object, block_id: int) -> tuple[list[tuple[int, ...]], list[tuple[int, ...]], list[int], list[bool]]:
        raw = prepared.raw
        if prepared.state_version != state.version or raw.state_version != state.version:
            raise ValueError("native state version mismatch")
        try:
            (
                raw_rows,
                raw_pairs,
                raw_offsets,
                raw_flags,
                total,
                tardiness,
                assignment,
                fragmentation,
                guide_penalty,
            ) = _prepared_buffers(prepared)
        except (AttributeError, TypeError) as exc:
            raise ValueError("native prepared buffer is incomplete") from exc
        rows = [self._row(item, block_id=block_id) for item in raw_rows]
        if len(set(rows)) != len(rows):
            raise ValueError("native rows are not deduplicated")
        pairs = [self._row(item) for item in raw_pairs]
        offsets = list(raw_offsets)
        flags = list(raw_flags)
        if (
            len(offsets) != len(rows) + 1
            or not offsets
            or offsets[0] != 0
            or offsets[-1] != len(pairs)
            or any(
                isinstance(item, bool) or not isinstance(item, int)
                for item in offsets
            )
            or any(left > right for left, right in zip(offsets, offsets[1:]))
            or len(flags) != len(pairs)
            or any(item not in (0, 1, False, True) for item in flags)
        ):
            raise ValueError("native pair buffer is malformed")
        for values in (total, tardiness, assignment, fragmentation, guide_penalty):
            if len(values) != len(rows):
                raise ValueError("native score buffer length mismatch")
        if any(
            not math.isfinite(float(value))
            for values in (total, tardiness, assignment)
            for value in values
        ):
            raise ValueError("native score buffer is not finite")
        if any(
            isinstance(value, bool) or not isinstance(value, int)
            for value in fragmentation
        ):
            raise ValueError("native fragmentation buffer is invalid")
        return rows, pairs, offsets, [bool(item) for item in flags]

    def _validate_cursor_batch(
        self,
        batch: NativeCandidateBatch,
        state: object,
        block_id: int,
        *,
        emitted: bool,
    ) -> tuple[list[tuple[int, ...]], list[tuple[int, ...]], list[int]]:
        raw = batch.raw
        if (
            batch.state_version != state.version
            or raw.state_version != state.version
        ):
            raise ValueError("native cursor state version mismatch")
        rows = [self._row(item, block_id=block_id) for item in batch.rows]
        pairs = [self._row(item) for item in batch.pairs]
        offsets = list(batch.pair_offsets)
        if emitted and len(rows) > 16:
            raise ValueError("native cursor emitted more than 16 rows")
        if len(set(rows)) != len(rows):
            raise ValueError("native cursor rows are not deduplicated")
        if (
            len(offsets) != len(rows) + 1
            or not offsets
            or offsets[0] != 0
            or offsets[-1] != len(pairs)
            or any(
                isinstance(item, bool) or not isinstance(item, int)
                for item in offsets
            )
            or any(left > right for left, right in zip(offsets, offsets[1:]))
        ):
            raise ValueError("native cursor pair buffer is malformed")
        for values in (
            batch.total,
            batch.tardiness,
            batch.assignment,
            batch.fragmentation,
            batch.guide_penalty,
        ):
            if len(values) != len(rows):
                raise ValueError("native cursor score buffer length mismatch")
        if any(
            not math.isfinite(float(value))
            for values in (batch.total, batch.tardiness, batch.assignment)
            for value in values
        ):
            raise ValueError("native cursor score buffer is not finite")
        if any(
            isinstance(value, bool) or not isinstance(value, int)
            for values in (batch.fragmentation, batch.guide_penalty)
            for value in values
        ):
            raise ValueError("native cursor integer score buffer is invalid")
        return rows, pairs, offsets

    @staticmethod
    def _validate_verdicts(verdicts: object, pair_count: int) -> list[bool]:
        try:
            values = list(verdicts)
        except TypeError as exc:
            raise ValueError("native exact verdict buffer is not iterable") from exc
        if len(values) != pair_count or any(type(item) is not bool for item in values):
            raise ValueError("native exact verdict buffer is malformed")
        return values

    @staticmethod
    def _validate_scores(values: object, state: object, block_id: int) -> tuple[object, ...]:
        try:
            scores = tuple(values)
        except TypeError as exc:
            raise ValueError("native candidate scores are not iterable") from exc
        if len(scores) > 3:
            raise ValueError("native returned more than top three candidates")
        seen = set()
        for score in scores:
            placement = getattr(score, "placement", None)
            if placement is None or placement.block_id != block_id:
                raise ValueError("native candidate has an invalid placement")
            if placement.block_id in state.block_ids or placement in seen:
                raise ValueError("native candidate duplicates state or output")
            seen.add(placement)
            if getattr(score, "state_version", None) != state.version:
                raise ValueError("native candidate state version mismatch")
            for name in ("total_delta", "tardiness_delta", "assignment_delta"):
                value = getattr(score, name, None)
                if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(float(value)):
                    raise ValueError("native candidate score is not finite")
        return scores

    @staticmethod
    def _consumed_pair_counts(
        offsets: list[int],
        flags: list[bool],
        verdicts: list[bool],
        row_count: int | None = None,
    ) -> tuple[int, int]:
        unknown = definitely_free = 0
        limit = len(offsets) - 1 if row_count is None else row_count
        for start, stop in zip(offsets[:limit], offsets[1 : limit + 1]):
            rejected = False
            for index in range(start, stop):
                if rejected:
                    continue
                if flags[index]:
                    definitely_free += 1
                else:
                    unknown += 1
                rejected = not verdicts[index]
        return unknown, definitely_free

    @staticmethod
    def _accepted_count(
        offsets: list[int], verdicts: list[bool], processed_rows: int
    ) -> int:
        return sum(
            all(verdicts[index] for index in range(offsets[row], offsets[row + 1]))
            for row in range(processed_rows)
        )

    @staticmethod
    def _merge_scores(*groups: tuple[object, ...]) -> tuple[object, ...]:
        return tuple(
            sorted(
                (score for group in groups for score in group),
                key=lambda item: item.canonical_tie,
            )[:3]
        )

    def _account_prepared(
        self,
        prepared: NativePreparedBatch,
        state: object,
        block_id: int,
    ) -> tuple[list[tuple[int, ...]], list[tuple[int, ...]], list[int], list[bool]]:
        rows, pairs, offsets, flags = self._validate_prepared(
            prepared, state, block_id
        )
        self._timing["pack_ns"] += int(prepared.pack_ns)
        self._timing["bind_ns"] += int(prepared.bind_ns)
        self._timing["prepare_ns"] += int(prepared.prepare_ns)
        self._copied_bytes_estimate += int(prepared.packed_bytes_estimate)
        self._candidate_rows += len(rows)
        self._pairs += len(pairs)
        self._unknown_pairs += sum(1 for flag in flags if not flag)
        self._definitely_free_pairs += sum(1 for flag in flags if flag)
        return rows, pairs, offsets, flags

    def _generate_unbounded(
        self,
        state: object,
        block_id: int,
        kernel: object,
        budget: object,
        current_placement: object | None,
        *,
        escalated_time_cap: int,
        exact_mode: str,
    ) -> tuple[object, ...]:
        """Stream at most 16 rows per batch while Python owns exact verdicts."""
        assert self._adapter is not None

        # Preserve the rollback column, but validate it before streaming. This
        # avoids starting any new Python exact work after a later deadline.
        rollback = None
        if current_placement is not None:
            from .construct import evaluate_insert

            rollback = evaluate_insert(state, current_placement, kernel)

        def batch_scores(
            batch: NativeCandidateBatch, *, recheck: bool = False
        ) -> tuple[tuple[object, ...], bool]:
            from .construct import CandidateScore, evaluate_insert
            from .state import Placement

            rows, _, _ = self._validate_cursor_batch(
                batch, state, block_id, emitted=False
            )
            values = []
            recheck_failed = False
            for index, row in enumerate(rows):
                placement = Placement(*row)
                total = float(batch.total[index])
                fragmentation = int(batch.fragmentation[index])
                guide_penalty = int(batch.guide_penalty[index])
                score = CandidateScore(
                        placement=placement,
                        total_delta=total,
                        tardiness_delta=float(batch.tardiness[index]),
                        assignment_delta=float(batch.assignment[index]),
                        fragmentation=fragmentation,
                        canonical_tie=(
                            total,
                            fragmentation,
                            guide_penalty,
                            placement.exit,
                            placement.entry,
                            placement.bay_id,
                            placement.orient_idx,
                            placement.x,
                            placement.y,
                            placement.block_id,
                        ),
                        state_version=state.version,
                    )
                if recheck:
                    self._returned_candidate_rechecks += 1
                    refreshed = evaluate_insert(state, placement, kernel)
                    matched = bool(
                        refreshed is not None
                        and refreshed.placement == score.placement
                        and refreshed.state_version == score.state_version
                        and math.isclose(
                            refreshed.total_delta,
                            score.total_delta,
                            rel_tol=0.0,
                            abs_tol=1e-9,
                        )
                        and math.isclose(
                            refreshed.tardiness_delta,
                            score.tardiness_delta,
                            rel_tol=0.0,
                            abs_tol=1e-9,
                        )
                        and math.isclose(
                            refreshed.assignment_delta,
                            score.assignment_delta,
                            rel_tol=0.0,
                            abs_tol=1e-9,
                        )
                        and refreshed.fragmentation == score.fragmentation
                    )
                    if not matched:
                        self._returned_candidate_recheck_failures += 1
                        recheck_failed = True
                values.append(score)
            if recheck_failed:
                return (), True
            return self._validate_scores(values, state, block_id), False

        def run_native_exact_phase(
            *, time_cap: int, lattice_only: bool
        ) -> tuple[tuple[object, ...], int, bool, bool]:
            cursor = self._adapter.create_cursor(
                state,
                block_id,
                current_placement,
                time_cap=time_cap,
                lattice_only=lattice_only,
            )
            if cursor.state_version != state.version or cursor.raw.state_version != state.version:
                raise ValueError("native cursor state version mismatch")
            self._cursor_count += 1
            self._timing["pack_ns"] += int(cursor.pack_ns)
            self._timing["bind_ns"] += int(cursor.bind_ns)
            self._timing["prepare_ns"] += int(cursor.prepare_ns)
            self._timing["cursor_prepare_ns"] += int(cursor.prepare_ns)
            self._copied_bytes_estimate += int(cursor.packed_bytes_estimate)

            remaining_seconds = max(
                0.0, budget.search_remaining() - self._deadline_margin
            )
            batch = self._adapter.run_exact_cursor(cursor, remaining_seconds)
            rows, pairs, _ = self._validate_cursor_batch(
                batch, state, block_id, emitted=False
            )
            if pairs:
                raise ValueError("native exact result exposed pair rows")
            raw = batch.raw
            counters = (
                raw.generated_candidates,
                raw.aabb_skipped_pairs,
                raw.geos_exact_calls,
                raw.first_conflict_skipped_pairs,
                raw.exact_cache_hits,
                raw.exact_cache_misses,
                raw.geos_error_count,
                raw.native_exact_ns,
                raw.native_cache_ns,
                raw.native_error_ns,
            )
            if any(isinstance(value, bool) or not isinstance(value, int) or value < 0 for value in counters):
                raise ValueError("native exact telemetry is malformed")
            if type(raw.exact_error) is not bool:
                raise ValueError("native exact error flag is malformed")

            kernel_ns = int(raw.kernel_ns)
            self._timing["bind_ns"] += int(batch.bind_ns)
            self._timing["exact_resolve_ns"] += kernel_ns
            self._timing["cursor_exact_ns"] += kernel_ns
            self._candidate_rows += int(raw.generated_candidates)
            self._processed_rows += int(raw.generated_candidates)
            self._stream_chunks += int(raw.generated_candidates > 0)
            self._aabb_skipped_pairs += int(raw.aabb_skipped_pairs)
            self._geos_exact_calls += int(raw.geos_exact_calls)
            self._first_conflict_skipped_pairs += int(
                raw.first_conflict_skipped_pairs
            )
            self._exact_cache_hits += int(raw.exact_cache_hits)
            self._exact_cache_misses += int(raw.exact_cache_misses)
            queried_pairs = int(raw.exact_cache_hits) + int(raw.exact_cache_misses)
            self._pairs += queried_pairs
            self._unknown_pairs += queried_pairs
            self._exact_relation_calls += queried_pairs
            self._native_exact_ns += int(raw.native_exact_ns)
            self._native_cache_ns += int(raw.native_cache_ns)
            self._native_error_ns += int(raw.native_error_ns)
            self._cpp_exact_ns += (
                int(raw.native_exact_ns)
                + int(raw.native_cache_ns)
                + int(raw.native_error_ns)
            )
            self._geos_error_count += int(raw.geos_error_count)
            self._accepted_rows += int(cursor.raw.accepted_total)

            deadline_hit = bool(raw.deadline_hit or cursor.raw.deadline_hit)
            if deadline_hit:
                self._native_deadline_count += 1
                self._deadline_observed = True
            if raw.exact_error or raw.geos_error_count:
                self._native_error_count += 1
                return (), int(cursor.raw.accepted_total), deadline_hit, True

            scores, recheck_failed = batch_scores(batch, recheck=True)
            if recheck_failed:
                self._native_error_count += 1
                return (), int(cursor.raw.accepted_total), deadline_hit, True
            return scores, int(cursor.raw.accepted_total), deadline_hit, False

        def run_phase(*, time_cap: int, lattice_only: bool) -> tuple[tuple[object, ...], int, bool]:
            cursor = self._adapter.create_cursor(
                state,
                block_id,
                current_placement,
                time_cap=time_cap,
                lattice_only=lattice_only,
            )
            if cursor.state_version != state.version or cursor.raw.state_version != state.version:
                raise ValueError("native cursor state version mismatch")
            self._cursor_count += 1
            self._timing["pack_ns"] += int(cursor.pack_ns)
            self._timing["bind_ns"] += int(cursor.bind_ns)
            self._timing["prepare_ns"] += int(cursor.prepare_ns)
            self._timing["cursor_prepare_ns"] += int(cursor.prepare_ns)
            self._copied_bytes_estimate += int(cursor.packed_bytes_estimate)
            deadline_hit = False

            while not cursor.raw.complete and not cursor.raw.deadline_hit:
                remaining_seconds = max(
                    0.0, budget.search_remaining() - self._deadline_margin
                )
                batch = self._adapter.next_cursor_batch(
                    cursor, remaining_seconds
                )
                rows, pairs, _ = self._validate_cursor_batch(
                    batch, state, block_id, emitted=True
                )
                self._timing["bind_ns"] += int(batch.bind_ns)
                self._timing["prepare_ns"] += int(batch.prepare_ns)
                self._timing["cursor_prepare_ns"] += int(batch.prepare_ns)
                if rows:
                    self._stream_chunks += 1
                    self._candidate_rows += len(rows)
                    self._pairs += len(pairs)
                    self._unknown_pairs += len(pairs)
                if not rows:
                    if batch.raw.deadline_hit:
                        deadline_hit = True
                    if batch.raw.complete or batch.raw.deadline_hit:
                        break
                    raise ValueError("native cursor made no progress")

                exact_started = time.perf_counter_ns()
                verdicts, processed_rows, exact_deadline, exact_calls, shadow = (
                    self._adapter.exact_cursor_batch_until_deadline(
                        batch,
                        kernel,
                        budget,
                        self._deadline_margin,
                        exact_mode,
                    )
                )
                self._account_shadow(shadow)
                verdicts = self._validate_verdicts(verdicts, len(pairs))
                exact_ns = max(0, time.perf_counter_ns() - exact_started)
                self._timing["exact_resolve_ns"] += exact_ns
                self._timing["cursor_exact_ns"] += exact_ns
                self._exact_relation_calls += exact_calls
                self._python_exact_calls += exact_calls
                self._processed_rows += processed_rows
                accepted_before = int(cursor.raw.accepted_total)
                finalize_started = time.perf_counter_ns()
                self._adapter.consume_cursor_batch(
                    cursor, verdicts, processed_rows
                )
                consume_ns = max(0, time.perf_counter_ns() - finalize_started)
                self._timing["finalize_ns"] += consume_ns
                self._timing["cursor_finalize_ns"] += consume_ns
                self._accepted_rows += (
                    int(cursor.raw.accepted_total) - accepted_before
                )
                if batch.raw.deadline_hit or exact_deadline or cursor.raw.deadline_hit:
                    deadline_hit = True
                    break

            finalize_started = time.perf_counter_ns()
            final_batch = self._adapter.finalize_cursor(cursor)
            finalize_ns = max(0, time.perf_counter_ns() - finalize_started)
            self._timing["finalize_ns"] += finalize_ns
            self._timing["cursor_finalize_ns"] += finalize_ns
            return (
                batch_scores(final_batch)[0],
                int(cursor.raw.accepted_total),
                deadline_hit or bool(cursor.raw.deadline_hit),
            )

        discarded = False
        if exact_mode == "native":
            scores, accepted_total, deadline_hit, discarded = run_native_exact_phase(
                time_cap=state.time_cap,
                lattice_only=False,
            )
            if not deadline_hit and not discarded and accepted_total == 0:
                scores, accepted_total, deadline_hit, discarded = run_native_exact_phase(
                    time_cap=escalated_time_cap,
                    lattice_only=True,
                )
        else:
            scores, accepted_total, deadline_hit = run_phase(
                time_cap=state.time_cap,
                lattice_only=False,
            )
            if not deadline_hit and accepted_total == 0:
                scores, accepted_total, deadline_hit = run_phase(
                    time_cap=escalated_time_cap,
                    lattice_only=True,
                )
            if deadline_hit:
                self._native_deadline_count += 1
                self._deadline_observed = True

        if discarded or (deadline_hit and not scores):
            return ()

        if rollback is not None and not any(
            item.placement == current_placement for item in scores
        ):
            scores = tuple(scores[:2]) + (rollback,)
        return scores

    def _python_fallback(self, reason: str, reference: Callable[[], tuple[object, ...]]) -> tuple[object, ...]:
        self._record_fallback(reason)
        self._python_calls += 1
        if self._deadline_observed:
            self._python_calls_after_deadline += 1
            self._python_full_repair_after_deadline += 1
        try:
            return tuple(reference())
        except Exception as exc:
            # The repair caller returns its immutable input identity for an
            # empty candidate set; no native failure can leak a partial state.
            self._record_fallback(f"python_reference_{type(exc).__name__}")
            return ()

    def generate(
        self,
        state: object,
        block_id: int,
        kernel: object,
        budget: object,
        current_placement: object | None,
        *,
        attempt_cap: int | None,
        escalated_time_cap: int,
        exact_mode: str = "python",
        python_reference: Callable[[], tuple[object, ...]],
    ) -> tuple[object, ...]:
        """Return native candidates or the same-input Python reference result."""
        if exact_mode not in {"python", "shadow", "native"}:
            raise ValueError("native exact mode must be 'python', 'shadow', or 'native'")
        self._native_exact_mode = exact_mode
        if self.requested_backend != "native":
            self._python_calls += 1
            return tuple(python_reference())
        if self._adapter is None:
            if self._deadline_reached(budget, self._deadline_margin):
                self._native_deadline_count += 1
                self._record_fallback("native_deadline_without_adapter")
                return ()
            return self._python_fallback(
                self._fallback_reason or "native_adapter_unavailable",
                python_reference,
            )
        if self._deadline_reached(budget, self._deadline_margin):
            self._native_deadline_count += 1
            self._record_fallback("native_deadline_before_prepare")
            return ()
        try:
            self._native_calls += 1
            if attempt_cap is None:
                native_error_before = self._native_error_count
                scores = self._validate_scores(
                    self._generate_unbounded(
                        state,
                        block_id,
                        kernel,
                        budget,
                        current_placement,
                        escalated_time_cap=escalated_time_cap,
                        exact_mode=exact_mode,
                    ),
                    state,
                    block_id,
                )
                if self._native_error_count > native_error_before:
                    self._peak_rss_bytes = max(self._peak_rss_bytes, _rss_bytes())
                    return ()
                self._native_successes += 1
                self._returned_candidates += len(scores)
                self._peak_rss_bytes = max(self._peak_rss_bytes, _rss_bytes())
                return scores
            prepared = self._adapter.prepare(
                state,
                block_id,
                current_placement,
                attempt_cap=-1 if attempt_cap is None else attempt_cap,
            )
            rows, pairs, offsets, flags = self._account_prepared(
                prepared, state, block_id
            )
            if self._deadline_reached(budget):
                self._native_deadline_count += 1
                self._record_fallback("native_deadline_after_prepare")
                return ()
            exact_started = time.perf_counter_ns()
            verdicts = self._validate_verdicts(
                self._adapter.exact_verdicts(prepared, kernel), len(pairs)
            )
            self._timing["exact_resolve_ns"] += max(0, time.perf_counter_ns() - exact_started)
            exact_unknown, exact_free = self._consumed_pair_counts(offsets, flags, verdicts)
            self._exact_relation_calls += exact_unknown
            # The prepared totals remain useful as an observability count;
            # consumed counts distinguish short-circuit exact resolution.
            del exact_free
            if self._deadline_reached(budget):
                self._native_deadline_count += 1
            finalize_started = time.perf_counter_ns()
            scores = self._validate_scores(
                self._adapter.candidate_scores(
                    prepared,
                    verdicts,
                    current_placement=current_placement,
                    state=state,
                    kernel=kernel,
                ),
                state,
                block_id,
            )
            self._timing["finalize_ns"] += max(0, time.perf_counter_ns() - finalize_started)
            if self._deadline_reached(budget):
                self._native_deadline_count += 1
            self._native_successes += 1
            self._returned_candidates += len(scores)
            self._peak_rss_bytes = max(self._peak_rss_bytes, _rss_bytes())
            return scores
        except (AttributeError, TypeError, ValueError) as exc:
            self._native_invalid_output_count += 1
            if self._deadline_reached(budget):
                self._record_fallback(
                    f"native_deadline_invalid_output_{type(exc).__name__}"
                )
                return ()
            return self._python_fallback(
                f"native_invalid_output_{type(exc).__name__}", python_reference
            )
        except Exception as exc:
            self._native_exception_count += 1
            if self._deadline_reached(budget):
                self._record_fallback(
                    f"native_deadline_exception_{type(exc).__name__}"
                )
                return ()
            return self._python_fallback(
                f"native_exception_{type(exc).__name__}", python_reference
            )

    def telemetry(self) -> tuple[tuple[str, object], ...]:
        """Return bounded scalar telemetry safe to attach to ``RepairResult``."""
        self._peak_rss_bytes = max(self._peak_rss_bytes, _rss_bytes())
        values: dict[str, object] = {
            "requested_backend": self.requested_backend,
            "actual_backend": self.actual_backend,
            "native_available": self.native_available,
            "prefilter_enabled": self.prefilter_enabled,
            "native_exact_mode": self._native_exact_mode,
            "exact_geometry_authority": (
                "native_geos_with_python_returned_candidate_recheck"
                if self._native_exact_mode == "native"
                else "python_geometrykernel_relation_shapely"
            ),
            "native_exact_decision_enabled": self._native_exact_mode == "native",
            "shadow_checked_pairs": self._shadow_checked_pairs,
            "shadow_free_free_count": self._shadow_free_free,
            "shadow_blocked_blocked_count": self._shadow_blocked_blocked,
            "shadow_mismatch_count": self._shadow_mismatch_count,
            "geos_error_count": self._geos_error_count,
            "python_exact_ns": self._python_exact_ns,
            "cpp_exact_ns": self._cpp_exact_ns,
            "python_exact_seconds": self._python_exact_ns / 1_000_000_000.0,
            "cpp_exact_seconds": self._cpp_exact_ns / 1_000_000_000.0,
            "exact_cache_hits": self._exact_cache_hits,
            "exact_cache_misses": self._exact_cache_misses,
            "aabb_skipped_pairs": self._aabb_skipped_pairs,
            "geos_exact_calls": self._geos_exact_calls,
            "first_conflict_skipped_pairs": self._first_conflict_skipped_pairs,
            "native_exact_ns": self._native_exact_ns,
            "native_cache_ns": self._native_cache_ns,
            "native_error_ns": self._native_error_ns,
            "native_exact_seconds": self._native_exact_ns / 1_000_000_000.0,
            "native_cache_seconds": self._native_cache_ns / 1_000_000_000.0,
            "native_error_seconds": self._native_error_ns / 1_000_000_000.0,
            "python_returned_candidate_rechecks": self._returned_candidate_rechecks,
            "returned_candidate_recheck_failures": self._returned_candidate_recheck_failures,
            "first_shadow_mismatch": self._first_shadow_mismatch,
            "fallback_reason": self._fallback_reason,
            "fallback_count": self._fallback_count,
            "fallback_reasons": tuple(sorted(self._fallback_reasons.items())),
            "native_exception_count": self._native_exception_count,
            "native_invalid_output_count": self._native_invalid_output_count,
            "native_error_count": (
                self._native_error_count
                + self._native_exception_count
                + self._native_invalid_output_count
            ),
            "native_deadline_count": self._native_deadline_count,
            "native_calls": self._native_calls,
            "native_successes": self._native_successes,
            "python_reference_calls": self._python_calls,
            "python_reference_calls_after_deadline": self._python_calls_after_deadline,
            "python_full_repair_started_after_deadline": self._python_full_repair_after_deadline,
            "pack_ns": self._timing["pack_ns"],
            "bind_ns": self._timing["bind_ns"],
            "prepare_ns": self._timing["prepare_ns"],
            "exact_resolve_ns": self._timing["exact_resolve_ns"],
            "finalize_ns": self._timing["finalize_ns"],
            "cursor_prepare_ns": self._timing["cursor_prepare_ns"],
            "cursor_exact_ns": self._timing["cursor_exact_ns"],
            "cursor_finalize_ns": self._timing["cursor_finalize_ns"],
            "cursor_count": self._cursor_count,
            "batches": self._stream_chunks,
            "generated_rows": self._candidate_rows,
            "generated_candidates": self._candidate_rows,
            "emitted_pairs": self._pairs,
            "processed_rows": self._processed_rows,
            "accepted_rows": self._accepted_rows,
            "deadline_hits": self._native_deadline_count,
            "python_exact_calls": self._python_exact_calls,
            "candidate_rows": self._candidate_rows,
            "stream_chunks": self._stream_chunks,
            "returned_candidates": self._returned_candidates,
            "pair_count": self._pairs,
            "unknown_pair_count": self._unknown_pairs,
            "definitely_free_pair_count": self._definitely_free_pairs,
            "exact_relation_calls": self._exact_relation_calls,
            "copied_bytes_estimate": self._copied_bytes_estimate,
            "copied_bytes_estimate_definition": "static JSON payload plus 2*row(7*int64)+raw_bay_load double for each newly packed state version",
            "peak_rss_bytes": self._peak_rss_bytes,
            "original_anchor_objective": self._anchor_objective,
            "original_anchor_digest": self._anchor_digest,
            "final_objective": self._final_objective,
            "final_digest": self._final_digest,
            "dominance_guard_rejected": self._dominance_rejected,
        }
        return tuple(sorted(values.items()))


__all__ = [
    "NativeCandidateBatch",
    "NativePreparedBatch",
    "NativeRepairAdapter",
    "NativeRepairCursor",
    "NativeRepairSession",
    "empty_kernel_info",
    "load_native_module",
    "native_module_status",
    "pack_static_problem",
    "snapshot_digest",
    "with_python_fallback",
]
