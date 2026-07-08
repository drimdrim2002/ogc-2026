"""
baseline_greedy.py -- EDD + Best-Fit Greedy Algorithm with Post-Hoc Repair

===============================================================================
ALGORITHM OVERVIEW
===============================================================================

Phase 1 -- Aggressive greedy placement (configurable block order):
  Blocks are sorted by the selected block_order_mode.  The default "edd" mode
  preserves the original Earliest Due Date order, with ties broken by Shortest
  Processing Time.  For each block, every (bay, orientation, position, time-slot)
  combination is scored; the cheapest is committed.  Crane-path feasibility
  (check_entry / check_exit) is verified against the current bay state, so
  most Phase-1 placements are already crane-feasible.

Phase 2 -- Iterative repair:
  check_feasibility is called on the Phase-1 solution.  Violating blocks are
  re-placed in EDD order.  Two modes are supported (repair_mode parameter):

  * "greedy" (default)
      Violating blocks are removed from the current solution and re-placed
      using the same full Phase-1 search (best bay + position + time-slot).
      State (bay_placed / bay_schedule / bay_loads) is reconstructed from
      the non-violating assignments before each pass.
      Cycle detection: if a block reappears in a second repair pass it is
      added to forced_ids, which bypasses search and uses _force_place
      (empty-bay window at (0,0)) to guarantee termination.
      Time guard: blocks whose turn comes after 90% of timelimit are also
      sent to _force_place to ensure all blocks are assigned before timeout.

  * "simple"
      Each violating block keeps its current (bay, x, y, orient) and is only
      pushed to the next empty-bay window (bay completely empty for the full
      processing duration).  Stage-4 (spatial collision) violations are also
      reset to position (0,0).

===============================================================================
SOLUTION DICT FORMAT
===============================================================================

{
    "operations": {
        "<time_int>": [           # integer time-point as string key
            {
                "type":       "EXIT",   # crane removes block from bay
                "block_id":   int,
                "bay_id":     int,
            },
            {
                "type":       "ENTRY",  # crane places block into bay
                "block_id":   int,
                "bay_id":     int,
                "x":          int,      # bottom-left x the reference point within the bay
                "y":          int,      # bottom-left y the reference point within the bay
                "orient_idx": int,      # index into block["shape"] list
            },
            ...
        ],
        ...
    }
}

At each time-point, EXIT operations always precede ENTRY operations.
Within the same type, operations are ordered so that each is feasible given
the bay state after all preceding operations at that time have completed.
entry_time = int(t_str) for ENTRY ops; exit_time = int(t_str) for EXIT ops.

Feasibility checking and objective computation: utils.check_feasibility(prob_info, solution).
"""

import math
import time
from utils import Bay, Block, check_entry, check_exit, check_collisions, _resolve_layers, _bounding_box


class _TimeBudgetExpired(RuntimeError):
    """Raised internally when the greedy search should stop and fall back."""

    def __init__(self, assignments: dict[int, dict] | None = None):
        super().__init__("time budget expired")
        self.assignments = assignments


class _LnsRepairFailed(RuntimeError):
    def __init__(self, assignments: dict[int, dict]):
        super().__init__("lns repair failed")
        self.assignments = assignments


def _check_deadline(deadline: float | None) -> None:
    if deadline is not None and time.time() >= deadline:
        raise _TimeBudgetExpired()


def _check_deadline_with_assignments(
    deadline: float | None,
    assignments: dict[int, dict],
) -> None:
    try:
        _check_deadline(deadline)
    except _TimeBudgetExpired as exc:
        partial = dict(assignments)
        if exc.assignments:
            partial.update(exc.assignments)
        raise _TimeBudgetExpired(partial) from exc


# -----------------------------------------------------------------------------
# Helpers: block bounding box (anchored, per orientation)
# -----------------------------------------------------------------------------

def _block_bbox(block_data: dict, orient_idx: int) -> tuple[float, float, float, float]:
    """Bounding box of a block in local coordinates relative to the reference
    point (first vertex of first layer = (0, 0)).  Returns (min_x, min_y, max_x, max_y)."""
    raw_layers = block_data["shape"][orient_idx]["layers"]
    layers = _resolve_layers(raw_layers)
    if not layers:
        return (0.0, 0.0, 1.0, 1.0)
    all_verts = [v for l in layers for v in l]
    return _bounding_box(all_verts)


def _block_size(block_data: dict, orient_idx: int) -> tuple[float, float]:
    """(width, height) of a block derived from its anchored bounding box."""
    bb = _block_bbox(block_data, orient_idx)
    return (bb[2] - bb[0], bb[3] - bb[1])


# -----------------------------------------------------------------------------
# Helper: time interval overlap check
# -----------------------------------------------------------------------------

def _time_overlaps(a_entry: int, a_exit: int,
                   b_entry: int, b_exit: int) -> bool:
    """True if intervals [a_entry, a_exit) and [b_entry, b_exit) overlap."""
    return a_entry < b_exit and b_entry < a_exit


# -----------------------------------------------------------------------------
# Helper: candidate position generation (bottom-left corner based)
# -----------------------------------------------------------------------------

def _candidate_positions(bay_w: float, bay_h: float,
                         placed_blocks: list[Block],
                         blk_bb: tuple[float, float, float, float]) -> list[tuple[int, int]]:
    """
    Return integer (x, y) reference-point candidate positions for a new block
    using the "bottom-left fill" heuristic.

    blk_bb = (local_min_x, local_min_y, local_max_x, local_max_y) in local
    coordinates (reference point = first vertex of first layer = (0, 0)).
    A placement (x, y) is valid iff the block's world bbox stays within the bay:
      x + blk_bb[0] >= 0,  y + blk_bb[1] >= 0
      x + blk_bb[2] <= bay_w,  y + blk_bb[3] <= bay_h
    Candidates are sorted by (x, y) so the search visits left-most / bottom-most
    positions first.
    """
    lx0, ly0, lx1, ly1 = blk_bb
    # Smallest valid integer reference-point position (block's left/bottom edge at bay wall)
    xs = {max(0, math.ceil(-lx0))}
    ys = {max(0, math.ceil(-ly0))}
    for b in placed_blocks:
        bb = b.bounding_rect()
        # Reference-point x/y such that new block's left/bottom edge touches the
        # right/top edge of this placed block
        xs.add(math.ceil(bb[2] - lx0))
        ys.add(math.ceil(bb[3] - ly0))

    candidates = []
    for x in sorted(xs):
        for y in sorted(ys):
            if x + lx1 <= bay_w + 1e-6 and y + ly1 <= bay_h + 1e-6:
                candidates.append((int(x), int(y)))
    return candidates


# -----------------------------------------------------------------------------
# Placement score (lower is better)
# -----------------------------------------------------------------------------

def _placement_score(tardiness: float, workload: float,
                     bay_loads: list[float], bay_id: int,
                     pref_penalty: float,
                     bay_weights: list[float],
                     w1: float, w2: float, w3: float,
                     top_y: float = 0.0, w4: float = 1e-4) -> float:
    """
    Composite score for placing a block in bay_id (lower is better).

      w1 * tardiness    -- total tardiness: max(0, exit_time - due_date).

      w2 * new_obj2     -- approximation of normalized load-balance penalty.
                          new_obj2 = max_j |u[bay_id]*new_load - u[j]*load_j|
                          where u_j = avg_bay_area / (W_j * H_j).

      w3 * pref_penalty -- preference penalty: S_i_max - S_i_bay_id.
                          0 when placed in most-preferred bay.

      w4 * top_y        -- tie-breaking: lower top edge -> tighter packing.
    """
    new_load = bay_loads[bay_id] + workload
    new_obj2 = max(
        (abs(bay_weights[bay_id] * new_load - bay_weights[j] * bay_loads[j])
         for j in range(len(bay_loads)) if j != bay_id),
        default=0.0
    )
    return w1 * tardiness + w2 * new_obj2 + w3 * pref_penalty + w4 * top_y


# -----------------------------------------------------------------------------
# Block ordering modes
# -----------------------------------------------------------------------------

_VALID_BLOCK_ORDER_MODES = (
    "edd",
    "release_edd",
    "slack",
    "latest_safe_entry",
    "preference_pressure",
)


def _validate_block_order_mode(block_order_mode: str) -> None:
    if block_order_mode not in _VALID_BLOCK_ORDER_MODES:
        choices = ", ".join(_VALID_BLOCK_ORDER_MODES)
        raise ValueError(f"unknown block_order_mode={block_order_mode!r}; choose one of: {choices}")


def _preference_pressure(block_data: dict) -> float:
    prefs = sorted(block_data.get("bay_preferences", []), reverse=True)
    if len(prefs) < 2:
        return 0.0
    return float(prefs[0] - prefs[1])


def _block_order_key(blocks_data: list[dict], idx: int, block_order_mode: str) -> tuple:
    block = blocks_data[idx]
    release = block["release_time"]
    due = block["due_date"]
    proc = block["processing_time"]

    if block_order_mode == "edd":
        return (due, proc, idx)
    if block_order_mode == "release_edd":
        return (release, due, proc, idx)
    if block_order_mode == "slack":
        slack = due - release - proc
        return (slack, due, release, proc, idx)
    if block_order_mode == "latest_safe_entry":
        return (due - proc, release, due, proc, idx)
    if block_order_mode == "preference_pressure":
        return (-_preference_pressure(block), due, proc, idx)

    _validate_block_order_mode(block_order_mode)
    raise AssertionError("unreachable")


def _ordered_block_ids(
    block_ids: list[int],
    blocks_data: list[dict],
    block_order_mode: str = "edd",
) -> list[int]:
    _validate_block_order_mode(block_order_mode)
    return sorted(
        block_ids,
        key=lambda idx: _block_order_key(blocks_data, idx, block_order_mode),
    )


def _block_order_indices(blocks_data: list[dict], block_order_mode: str = "edd") -> list[int]:
    return _ordered_block_ids(list(range(len(blocks_data))), blocks_data, block_order_mode)


# -----------------------------------------------------------------------------
# Earliest feasible entry slot (aggressive -- allows time overlap)
# -----------------------------------------------------------------------------

def _find_earliest_slot(new_blk: Block,
                        bay: Bay,
                        placed_in_bay: list[Block],
                        schedule_in_bay: list[tuple[int, int]],
                        r_time: int,
                        proc: int,
                        deadline: float | None = None) -> tuple[int | None, int | None]:
    """
    Return the earliest (entry, exit_t) time slot >= r_time at which new_blk
    can be crane-placed into bay without violating Stage-2 (entry) or Stage-3
    (exit) feasibility.  Returns (None, None) if no candidate entry passes
    both checks -- this means the (position, bay) combination is infeasible for
    any time and the caller should try a different position.

    -- Candidate enumeration ----------------------------------------------------
    Candidates = {r_time} | {exit_time of every already-placed block in bay}.

    -- Feasibility checks (mirror of check_feasibility Stages 2 & 3) -----------
    Stage-2 (crane entry): the crane path must not be blocked at entry_time.
      present_at_entry = blocks b_k with  a_k <= entry < e_k
      check_entry(bay, present_at_entry, new_blk, fast=True) returns True if
      ANY block in present_at_entry obstructs the crane path; fast=True exits
      on the first obstruction to avoid unnecessary Shapely work.

    Stage-3 (crane exit): the crane path must not be blocked at exit_time.
      present_at_exit  = [new_blk] + blocks b_k with  a_k < exit_t < e_k
      (new_blk itself is included because it will be present during its own exit)
      check_exit(bay, present_at_exit, new_blk, fast=True) returns True if
      ANY block in present_at_exit obstructs the crane exit path.

    Stage-4 (interior-interval): blocks whose interval is strictly inside
      [entry, exit_t) -- i.e. entry < a_k AND e_k < exit_t -- are invisible
      to the Stage-2 and Stage-3 boundary checks above.  They are present
      during new_blk's stay but not at its entry or exit moment.  A per-pair
      spatial collision check is run for these blocks to avoid producing
      Stage-4 violations that the repair loop cannot detect at placement time.
    """
    candidate_entries = sorted({r_time} | {e for _, e in schedule_in_bay})

    for entry_candidate in candidate_entries:
        _check_deadline(deadline)
        entry  = max(r_time, entry_candidate)
        exit_t = entry + proc

        # Stage-2: blocks whose interval [a_k, e_k) contains entry_time
        present_at_entry = [
            b for b, (a, e) in zip(placed_in_bay, schedule_in_bay)
            if a <= entry < e
        ]
        if check_entry(bay, present_at_entry, new_blk, fast=True):
            continue  # crane path blocked at entry -> try next exit boundary

        # Stage-3: blocks whose interval [a_k, e_k) strictly contains exit_t
        # (a_k < exit_t AND e_k > exit_t means the block spans across exit_t)
        present_at_exit = [new_blk] + [
            b for b, (a, e) in zip(placed_in_bay, schedule_in_bay)
            if a < exit_t < e
        ]
        if check_exit(bay, present_at_exit, new_blk, fast=True):
            continue  # crane path blocked at exit -> try next exit boundary

        # Stage-4 pre-check: blocks strictly interior to [entry, exit_t).
        # These blocks are invisible to Stage-2 (not yet present at entry)
        # and Stage-3 (already gone at exit), but are physically co-present
        # during [a_k, e_k) -- a subset of [entry, exit_t).  A spatial
        # collision with any such block would cause a Stage-4 violation after
        # placement; catch it here to avoid re-triggering repair passes.
        s4_blocked = False
        for b_other, (a_other, e_other) in zip(placed_in_bay, schedule_in_bay):
            if a_other <= entry or e_other >= exit_t:
                continue  # handled by Stage-2 or Stage-3 above
            if not _time_overlaps(entry, exit_t, a_other, e_other):
                continue  # disjoint in time
            if check_collisions(bay, [new_blk, b_other]):
                s4_blocked = True
                break
        if s4_blocked:
            continue

        return entry, exit_t

    return None, None  # no valid time slot for this (position, bay) combination


# -----------------------------------------------------------------------------
# Guaranteed-feasible entry: empty-bay window
# -----------------------------------------------------------------------------

def _empty_bay_entry(schedule_in_bay: list[tuple[int, int]],
                     r_time: int, proc: int) -> int:
    """
    Return the earliest entry time >= r_time such that the bay is completely
    empty for the entire window [entry, entry + proc).

    This guarantees crane-path feasibility: when the bay is empty at both
    entry_time and exit_time, check_entry and check_exit trivially pass
    (no blocks present means no polygon obstructions).

    Algorithm -- iterative push:
      Start with entry = r_time.  Scan all existing slots (a_k, e_k).  If
      [entry, entry+proc) overlaps any slot, advance entry to e_k (the end of
      that slot) so the window no longer overlaps it.  Repeat until no
      overlaps remain.

    Convergence guarantee:
      Each iteration advances entry by at least the distance to the next
      slot endpoint.  Because the number of slots is finite, the loop
      terminates after at most len(schedule_in_bay) passes.
    """
    entry = int(r_time)
    changed = True
    while changed:
        changed = False
        exit_t = entry + proc
        for a, e in schedule_in_bay:
            if _time_overlaps(entry, exit_t, a, e):
                entry = max(entry, e)  # push past the overlapping slot
                changed = True
    return entry


def _serial_fallback_assignments(prob_info: dict) -> dict[int, dict]:
    """Build a conservative solution by running at most one block per bay at a time."""
    bays = [Bay.from_dict(data, idx) for idx, data in enumerate(prob_info["bays"])]
    blocks_data = prob_info["blocks"]
    bay_schedule: list[list[tuple[int, int]]] = [[] for _ in bays]
    assignments: dict[int, dict] = {}

    block_order = sorted(
        range(len(blocks_data)),
        key=lambda idx: (
            blocks_data[idx]["release_time"],
            blocks_data[idx]["due_date"],
            blocks_data[idx]["processing_time"],
            idx,
        ),
    )

    for bi in block_order:
        block = blocks_data[bi]
        prefs = block["bay_preferences"]
        placement = None
        for bay_id in sorted(range(len(bays)), key=lambda idx: prefs[idx], reverse=True):
            bay = bays[bay_id]
            for orient_idx in range(len(block["shape"])):
                width, height = _block_size(block, orient_idx)
                if width > bay.width + 1e-6 or height > bay.height + 1e-6:
                    continue
                bbox = _block_bbox(block, orient_idx)
                x = max(0, math.ceil(-bbox[0]))
                y = max(0, math.ceil(-bbox[1]))
                placed_block = Block(block_id=bi, block_data=block, x=x, y=y, orient_idx=orient_idx)
                if not bay.contains_block(placed_block):
                    continue
                entry = _empty_bay_entry(
                    bay_schedule[bay_id],
                    block["release_time"],
                    block["processing_time"],
                )
                placement = (bay_id, x, y, orient_idx, entry, entry + block["processing_time"])
                break
            if placement is not None:
                break

        if placement is None:
            raise ValueError(f"block {bi} does not fit in any bay/orientation")

        bay_id, x, y, orient_idx, entry, exit_t = placement
        bay_schedule[bay_id].append((entry, exit_t))
        assignments[bi] = {
            "block_id": bi,
            "bay_id": bay_id,
            "x": int(x),
            "y": int(y),
            "orient_idx": orient_idx,
            "entry_time": int(entry),
            "exit_time": int(exit_t),
        }

    return assignments


def _assignments_from_operations(operations: dict) -> dict[int, dict]:
    assignments: dict[int, dict] = {}
    for time_key, ops in operations.items():
        time_int = int(time_key)
        for op in ops:
            block_id = int(op["block_id"])
            assignment = assignments.setdefault(block_id, {"block_id": block_id})
            if op["type"] == "ENTRY":
                assignment.update(
                    {
                        "bay_id": int(op["bay_id"]),
                        "x": int(op["x"]),
                        "y": int(op["y"]),
                        "orient_idx": int(op["orient_idx"]),
                        "entry_time": time_int,
                    }
                )
            elif op["type"] == "EXIT":
                assignment["exit_time"] = time_int
    return assignments


def _copy_assignments(assignments: dict[int, dict]) -> dict[int, dict]:
    return {
        int(block_id): dict(assignment)
        for block_id, assignment in assignments.items()
    }


def _rebuild_lns_state(
    blocks_data: list[dict],
    bays: list[Bay],
    assignments: dict[int, dict],
) -> tuple[list[list[Block]], list[list[tuple[int, int]]], list[float]]:
    bay_placed: list[list[Block]] = [[] for _ in bays]
    bay_schedule: list[list[tuple[int, int]]] = [[] for _ in bays]
    bay_loads: list[float] = [0.0] * len(bays)

    for assignment in assignments.values():
        block_id = int(assignment["block_id"])
        bay_id = int(assignment["bay_id"])
        block = Block(
            block_id=block_id,
            block_data=blocks_data[block_id],
            x=int(assignment["x"]),
            y=int(assignment["y"]),
            orient_idx=int(assignment["orient_idx"]),
        )
        bay_placed[bay_id].append(block)
        bay_schedule[bay_id].append((
            int(assignment["entry_time"]),
            int(assignment["exit_time"]),
        ))
        bay_loads[bay_id] += blocks_data[block_id]["workload"]

    return bay_placed, bay_schedule, bay_loads


def _solution_from_assignments(assignments: dict[int, dict]) -> dict:
    return {"operations": _build_operations(list(assignments.values()))}


def _complete_with_serial_fallback(
    prob_info: dict,
    assignments: dict[int, dict],
    verify: bool = True,
) -> dict:
    """
    Preserve already-built assignments and serially fill only missing blocks.

    Existing assignments are treated as fixed bay occupancy.  Missing blocks are
    then placed in empty windows, so the completion path keeps useful greedy
    work while retaining the serial fallback's feasibility guard.
    """
    bays = [Bay.from_dict(data, idx) for idx, data in enumerate(prob_info["bays"])]
    blocks_data = prob_info["blocks"]
    ordered_assignments: list[tuple[int, dict]] = []

    try:
        for raw_block_id, raw_assignment in assignments.items():
            block_id = int(raw_block_id)
            assignment = dict(raw_assignment)
            assignment["block_id"] = int(assignment.get("block_id", block_id))
            if assignment["block_id"] != block_id:
                raise ValueError("assignment key and block_id differ")
            assignment["bay_id"] = int(assignment["bay_id"])
            assignment["x"] = int(assignment["x"])
            assignment["y"] = int(assignment["y"])
            assignment["orient_idx"] = int(assignment["orient_idx"])
            assignment["entry_time"] = int(assignment["entry_time"])
            assignment["exit_time"] = int(assignment["exit_time"])
            if not (0 <= block_id < len(blocks_data)):
                raise ValueError("block_id out of range")
            if not (0 <= assignment["bay_id"] < len(bays)):
                raise ValueError("bay_id out of range")
            ordered_assignments.append((block_id, assignment))
    except (KeyError, TypeError, ValueError, IndexError):
        return _serial_fallback_solution(prob_info, verify=verify)

    def build_solution(prefix_len: int) -> dict | None:
        completed = _copy_assignments(dict(ordered_assignments[:prefix_len]))
        bay_schedule: list[list[tuple[int, int]]] = [[] for _ in bays]

        for assignment in completed.values():
            bay_schedule[assignment["bay_id"]].append((
                assignment["entry_time"],
                assignment["exit_time"],
            ))

        block_order = sorted(
            (idx for idx in range(len(blocks_data)) if idx not in completed),
            key=lambda idx: (
                blocks_data[idx]["release_time"],
                blocks_data[idx]["due_date"],
                blocks_data[idx]["processing_time"],
                idx,
            ),
        )

        for bi in block_order:
            block = blocks_data[bi]
            prefs = block["bay_preferences"]
            placement = None
            for bay_id in sorted(range(len(bays)), key=lambda idx: prefs[idx], reverse=True):
                bay = bays[bay_id]
                for orient_idx in range(len(block["shape"])):
                    width, height = _block_size(block, orient_idx)
                    if width > bay.width + 1e-6 or height > bay.height + 1e-6:
                        continue
                    bbox = _block_bbox(block, orient_idx)
                    x = max(0, math.ceil(-bbox[0]))
                    y = max(0, math.ceil(-bbox[1]))
                    placed_block = Block(
                        block_id=bi,
                        block_data=block,
                        x=x,
                        y=y,
                        orient_idx=orient_idx,
                    )
                    if not bay.contains_block(placed_block):
                        continue
                    entry = _empty_bay_entry(
                        bay_schedule[bay_id],
                        block["release_time"],
                        block["processing_time"],
                    )
                    placement = (
                        bay_id,
                        x,
                        y,
                        orient_idx,
                        entry,
                        entry + block["processing_time"],
                    )
                    break
                if placement is not None:
                    break

            if placement is None:
                return None

            bay_id, x, y, orient_idx, entry, exit_t = placement
            bay_schedule[bay_id].append((entry, exit_t))
            completed[bi] = {
                "block_id": bi,
                "bay_id": bay_id,
                "x": int(x),
                "y": int(y),
                "orient_idx": orient_idx,
                "entry_time": int(entry),
                "exit_time": int(exit_t),
            }

        return _solution_from_assignments(completed)

    full_solution = build_solution(len(ordered_assignments))
    if full_solution is None:
        return _serial_fallback_solution(prob_info, verify=verify)
    if not verify:
        return full_solution

    from utils import check_feasibility

    serial_solution = _serial_fallback_solution(prob_info, verify=True)
    serial_result = check_feasibility(prob_info, serial_solution)
    full_result = check_feasibility(prob_info, full_solution)
    if full_result["feasible"]:
        if full_result["objective"] <= serial_result["objective"]:
            return full_solution
        print("[Greedy] Partial completion feasible but worse than serial fallback; "
              "using serial fallback")
        return serial_solution

    best_solution = serial_solution
    best_objective = serial_result["objective"]
    best_prefix_len = 0
    max_feasible_prefix = 0
    lo = 0
    hi = len(ordered_assignments) - 1
    while lo <= hi:
        mid = (lo + hi) // 2
        candidate = build_solution(mid)
        if candidate is None:
            hi = mid - 1
            continue

        result = check_feasibility(prob_info, candidate)
        if result["feasible"]:
            max_feasible_prefix = mid
            lo = mid + 1
        else:
            hi = mid - 1

    for prefix_len in range(1, max_feasible_prefix + 1):
        candidate = build_solution(prefix_len)
        if candidate is None:
            continue
        result = check_feasibility(prob_info, candidate)
        if result["feasible"] and result["objective"] < best_objective:
            best_solution = candidate
            best_objective = result["objective"]
            best_prefix_len = prefix_len

    if best_prefix_len < len(ordered_assignments):
        print(
            f"[Greedy] Partial completion kept {best_prefix_len}/"
            f"{len(ordered_assignments)} assignments after feasibility trim"
        )
    return best_solution


def _serial_fallback_solution(prob_info: dict, verify: bool = True) -> dict:
    assignments = _serial_fallback_assignments(prob_info)
    solution = _solution_from_assignments(assignments)
    if verify:
        from utils import check_feasibility

        result = check_feasibility(prob_info, solution)
        if not result["feasible"]:
            raise ValueError(f"serial fallback infeasible: {result['violations'][:5]}")
    return solution


# -----------------------------------------------------------------------------
# Main algorithm
# -----------------------------------------------------------------------------

def greedyalgorithm(prob_info: dict, timelimit: float,
                    repair_mode: str = "greedy",
                    block_order_mode: str = "edd") -> dict:
    """
    EDD + Best-Fit Greedy algorithm with post-hoc feasibility repair.

    Parameters
    ----------
    prob_info   : instance JSON dict with keys "name", "bays", "blocks", "weights"
    timelimit   : wall-clock time limit in seconds
    repair_mode : "greedy" (default) or "simple" -- see module docstring for details
    block_order_mode : deterministic construction order; "edd" preserves the
        original behavior.

    Returns
    -------
    solution dict in the format described in the module docstring

    Phase 1 -- greedy placement:
        Blocks sorted by block_order_mode.  For each block, every
        (bay, orientation, candidate position) is tried; _find_earliest_slot
        computes the earliest crane-feasible time slot.  The combination
        minimising _placement_score is committed.  bay_placed, bay_schedule,
        and bay_loads are updated incrementally.

    Phase 2 -- Repair (see _repair and module docstring for details):
        Calls _repair which runs up to max_passes rounds of
        check_feasibility -> re-place violating blocks.
    """
    t_start = time.time()
    deadline = t_start + max(0.0, timelimit) * 0.95
    _validate_block_order_mode(block_order_mode)

    bays_data   = prob_info["bays"]
    blocks_data = prob_info["blocks"]
    n_bays      = len(bays_data)
    n_blocks    = len(blocks_data)

    w1 = prob_info.get("weights", {}).get("w1", 1.0)
    w2 = prob_info.get("weights", {}).get("w2", 1.0)
    w3 = prob_info.get("weights", {}).get("w3", 1.0)

    print(f"[Greedy] Instance : {prob_info.get('name', '?')}")
    print(f"[Greedy] Bays     : {n_bays}  |  Blocks : {n_blocks}  |  Timelimit : {timelimit:.1f}s")
    print(f"[Greedy] Weights  : w1={w1}  w2={w2}  w3={w3}")
    print(f"[Greedy] {'-' * 56}")

    bays = [Bay.from_dict(d, i) for i, d in enumerate(bays_data)]
    for i, b in enumerate(bays):
        print(f"[Greedy]   bay[{i}]  {b.width}x{b.height}")

    # -- Phase 1: aggressive greedy --------------------------------------------
    sorted_indices = _block_order_indices(blocks_data, block_order_mode)
    print(f"[Greedy] {'-' * 56}")
    print(f"[Greedy] Phase 1 : {block_order_mode} greedy placement ...")

    bay_placed:   list[list[Block]]             = [[] for _ in range(n_bays)]
    bay_schedule: list[list[tuple[int, int]]]   = [[] for _ in range(n_bays)]
    bay_loads:    list[float]                   = [0.0] * n_bays

    try:
        assignments = _place_blocks(
            sorted_indices, blocks_data, bays,
            bay_placed, bay_schedule, bay_loads,
            w1, w2, w3, forced_ids=set(),
            t_start=t_start, log_interval=max(1, n_blocks // 10),
            deadline=deadline,
        )
    except _TimeBudgetExpired as exc:
        partial = exc.assignments or {}
        if partial:
            print(f"[Greedy] Phase 1 deadline reached; serially completing "
                  f"{len(partial)}/{n_blocks} greedy assignments")
            return _complete_with_serial_fallback(prob_info, partial)
        print("[Greedy] Phase 1 deadline reached; returning verified serial fallback")
        return _serial_fallback_solution(prob_info)

    elapsed_p1 = time.time() - t_start
    loads_str = "  ".join(f"bay{i}={round(bay_loads[i])}" for i in range(n_bays))
    print(f"[Greedy] Phase 1 done  |  placed={len(assignments)}  {loads_str}  "
          f"elapsed={elapsed_p1:.2f}s")

    # -- Phase 2: repair infeasible assignments --------------------------------
    print(f"[Greedy] {'-' * 56}")
    print(f"[Greedy] Phase 2 : repair  mode={repair_mode}")
    sol = {"operations": _build_operations(list(assignments.values()))}
    try:
        assignments = _repair(prob_info, sol, assignments, bays, blocks_data,
                              w1, w2, w3, t_start, timelimit,
                              repair_mode=repair_mode,
                              block_order_mode=block_order_mode,
                              deadline=deadline)
    except _TimeBudgetExpired as exc:
        partial = exc.assignments or assignments
        if partial:
            print(f"[Greedy] Repair deadline reached; serially completing "
                  f"{len(partial)}/{n_blocks} assignments")
            return _complete_with_serial_fallback(prob_info, partial)
        print("[Greedy] Repair deadline reached; returning verified serial fallback")
        return _serial_fallback_solution(prob_info)

    elapsed_total = time.time() - t_start
    final_sol = {"operations": _build_operations(list(assignments.values()))}

    from utils import check_feasibility
    final_result = check_feasibility(prob_info, final_sol)
    print(f"[Greedy] {'-' * 56}")
    print(f"[Greedy] Done  |  assigned={len(assignments)}/{n_blocks}  "
          f"elapsed={elapsed_total:.2f}s")
    if final_result["feasible"]:
        print(f"[Greedy] Objective : {final_result['objective']:.0f}  "
              f"(obj1={final_result['obj1']:.1f}  "
              f"obj2={final_result['obj2']:.1f}  "
              f"obj3={final_result['obj3']:.1f})")
    else:
        print(f"[Greedy] INFEASIBLE stage={final_result['stage']}")
        for v in final_result["violations"][:5]:
            print(f"[Greedy]   {v}")
        print("[Greedy] Returning verified serial fallback")
        return _serial_fallback_solution(prob_info)

    return final_sol


# -----------------------------------------------------------------------------
# Force-place helper (no feasibility check -- used as phase-1 last resort)
# -----------------------------------------------------------------------------

def _force_place(bi: int,
                 blocks_data: list[dict],
                 bays: list[Bay],
                 bay_schedule: list[list[tuple[int, int]]],
                 prefs: list[float]) -> tuple:
    """
    Fallback placement: place block bi at the minimum valid position in the
    highest-preference bay whose dimensions accommodate the block, using
    an empty-bay entry window.

    When called:
      * _place_blocks found no feasible (position, bay, time-slot) combination
        during Phase-1 search (should be rare for well-formed instances).
      * bi is in forced_ids during repair -- the block has appeared in two or
        more consecutive repair passes, indicating a crane-path cycle.  Forcing
        it to an empty-bay window breaks the cycle by guaranteeing that both
        check_entry and check_exit trivially pass (bay is empty).

    Why minimum-valid position with empty-bay window is always feasible:
      _empty_bay_entry returns a time interval [entry, exit_t) during which no
      other block occupies the bay.  With the bay empty at both entry_time and
      exit_time, check_entry/check_exit have no polygon obstructions to report,
      so Stage-2 and Stage-3 always pass regardless of block shape or position.
      The minimum-valid position (max(0, ceil(-lx0)), max(0, ceil(-ly0)))
      ensures the block's bounding box starts at the bay's lower-left corner,
      so the bay boundary check also passes.

    Orientation selection: the first orientation whose footprint fits within
    the bay is used.  If no orientation fits (degenerate instance), the
    preferred bay with orientation 0 is used as an absolute last resort.

    Position selection: the minimum valid reference-point position is used,
    i.e. (max(0, ceil(-lx0)), max(0, ceil(-ly0))) derived from the block's
    local bounding box.  This ensures the block's world bounding box starts at
    the bay's lower-left corner regardless of which vertex is the reference
    point.  Placing at (0, 0) would be wrong when lx0 < 0 or ly0 < 0.
    """
    blk_data = blocks_data[bi]
    r_time   = blk_data["release_time"]
    proc     = blk_data["processing_time"]
    n_bays   = len(bays)

    for bay_id in sorted(range(n_bays), key=lambda j: prefs[j], reverse=True):
        bay = bays[bay_id]
        for oi in range(len(blk_data["shape"])):
            bw, bh = _block_size(blk_data, oi)
            if bw <= bay.width + 1e-6 and bh <= bay.height + 1e-6:
                bb = _block_bbox(blk_data, oi)
                px = max(0, math.ceil(-bb[0]))
                py = max(0, math.ceil(-bb[1]))
                entry = _empty_bay_entry(bay_schedule[bay_id], r_time, proc)
                return (bay_id, px, py, oi, entry, entry + proc)

    # absolute last resort -- ignore fit
    bay_id = max(range(n_bays), key=lambda j: prefs[j])
    bb     = _block_bbox(blk_data, 0)
    px     = max(0, math.ceil(-bb[0]))
    py     = max(0, math.ceil(-bb[1]))
    entry  = _empty_bay_entry(bay_schedule[bay_id], r_time, proc)
    return (bay_id, px, py, 0, entry, entry + proc)


# -----------------------------------------------------------------------------
# Shared greedy placement kernel (used by Phase 1 and _repair)
# -----------------------------------------------------------------------------

def _place_blocks(
    block_ids: list[int],
    blocks_data: list[dict],
    bays: list[Bay],
    bay_placed: list[list[Block]],
    bay_schedule: list[list[tuple[int, int]]],
    bay_loads: list[float],
    w1: float, w2: float, w3: float,
    forced_ids: set[int],
    allow_force: bool = True,
    prev_assignments: dict[int, dict] | None = None,
    t_start: float | None = None,
    log_interval: int = 0,
    deadline: float | None = None,
) -> dict[int, dict]:
    """
    Shared placement kernel used by both Phase 1 and _repair (greedy mode).

    For each block in block_ids, finds the best (bay, x, y, orient, entry_time)
    by minimising _placement_score, then commits it to bay_placed /
    bay_schedule / bay_loads.  Returns a dict mapping block_id -> assignment.

    Search order:
      1. Repair fast-path (only when prev_assignments is provided):
         Try the block's previous (bay, x, y, orient) with _find_earliest_slot.
         If that position is still crane-feasible, record it as the initial
         best candidate.  This avoids re-solving the position search for blocks
         that only need a time adjustment.
      2. Full search (Phase-1 style):
         Iterate bays in decreasing preference order, then orientations, then
         candidate positions from _candidate_positions.  For each (bay, orient,
         pos), call _find_earliest_slot to get the earliest crane-feasible slot.
         Keep the (bay, orient, pos, slot) with the lowest _placement_score.
      3. Forced path (forced_ids or no feasible combination found):
         Blocks in forced_ids skip steps 1-2 entirely and go straight to
         _force_place.  If the full search in step 2 found nothing, _force_place
         is used as a fallback and n_fallback is incremented.

    Parameters
    ----------
    block_ids        : ordered list of block indices to place (EDD order)
    blocks_data      : raw block data list from prob_info
    bays             : Bay objects (width, height, polygon)
    bay_placed       : mutable per-bay lists of placed Block objects (updated in-place)
    bay_schedule     : mutable per-bay lists of (entry_time, exit_time) (updated in-place)
    bay_loads        : mutable per-bay cumulative workload floats (updated in-place)
    w1, w2, w3       : objective weights
    forced_ids       : block ids to bypass search and use _force_place directly
    allow_force      : when false, raise _LnsRepairFailed instead of force-placing
    prev_assignments : previous assignment dict (repair mode fast-path)
    t_start          : wall-clock start time (for log timestamps)
    log_interval     : print a progress line every N blocks (0 = silent)

    Returns
    -------
    dict[block_id -> assignment dict] for all blocks in block_ids
    """
    n_bays  = len(bays)
    n_total = len(block_ids)
    result: dict[int, dict] = {}
    n_forced = n_fallback = 0

    # Bay weights for normalized obj2: u_j = avg_area / (W_j * H_j)
    _bay_areas   = [bay.width * bay.height for bay in bays]
    _avg_area    = sum(_bay_areas) / n_bays
    bay_weights  = [_avg_area / a for a in _bay_areas]

    for rank, bi in enumerate(block_ids):
        _check_deadline_with_assignments(deadline, result)
        blk_data = blocks_data[bi]
        r_time   = blk_data["release_time"]
        due      = blk_data["due_date"]
        proc     = blk_data["processing_time"]
        workload = blk_data["workload"]
        prefs    = blk_data["bay_preferences"]
        s_max    = max(prefs)
        n_orient = len(blk_data["shape"])

        best_score     = float("inf")
        best_placement = None
        used_forced    = bi in forced_ids

        if not used_forced:
            # -- Repair fast-path: try previous (bay, x, y, orient) first -----
            if prev_assignments and bi in prev_assignments:
                pa = prev_assignments[bi]
                pb_id = pa["bay_id"]
                px, py, poi = int(pa["x"]), int(pa["y"]), pa["orient_idx"]
                prev_blk = Block(block_id=bi, block_data=blk_data,
                                 x=px, y=py, orient_idx=poi)
                if bays[pb_id].contains_block(prev_blk):
                    try:
                        entry, exit_t = _find_earliest_slot(
                            prev_blk, bays[pb_id],
                            bay_placed[pb_id], bay_schedule[pb_id],
                            r_time, proc, deadline=deadline
                        )
                    except _TimeBudgetExpired as exc:
                        raise _TimeBudgetExpired(dict(result)) from exc
                    if entry is not None:
                        tardiness = max(0.0, exit_t - due)
                        p_bb = _block_bbox(blk_data, poi)
                        best_score     = _placement_score(
                            tardiness, workload, bay_loads, pb_id,
                            s_max - prefs[pb_id], bay_weights, w1, w2, w3,
                            top_y=py + p_bb[3]
                        )
                        best_placement = (pb_id, px, py, poi, entry, exit_t)

            # -- Full search (Phase-1 style) -----------------------------------
            bay_order = sorted(range(n_bays), key=lambda j: prefs[j], reverse=True)
            for bay_id in bay_order:
                _check_deadline_with_assignments(deadline, result)
                bay             = bays[bay_id]
                placed_in_bay   = bay_placed[bay_id]
                schedule_in_bay = bay_schedule[bay_id]

                for oi in range(n_orient):
                    _check_deadline_with_assignments(deadline, result)
                    blk_bb = _block_bbox(blk_data, oi)
                    bw = blk_bb[2] - blk_bb[0]
                    bh = blk_bb[3] - blk_bb[1]
                    if bw > bay.width + 1e-6 or bh > bay.height + 1e-6:
                        continue

                    candidates = _candidate_positions(
                        bay.width, bay.height, placed_in_bay, blk_bb
                    )
                    for (cx, cy) in candidates:
                        _check_deadline_with_assignments(deadline, result)
                        new_blk = Block(block_id=bi, block_data=blk_data,
                                        x=cx, y=cy, orient_idx=oi)
                        if not bay.contains_block(new_blk):
                            continue

                        try:
                            entry, exit_t = _find_earliest_slot(
                                new_blk, bay, placed_in_bay, schedule_in_bay,
                                r_time, proc, deadline=deadline
                            )
                        except _TimeBudgetExpired as exc:
                            raise _TimeBudgetExpired(dict(result)) from exc
                        if entry is None:
                            continue

                        tardiness = max(0.0, exit_t - due)
                        score = _placement_score(
                            tardiness, workload, bay_loads, bay_id,
                            s_max - prefs[bay_id], bay_weights, w1, w2, w3,
                            top_y=cy + blk_bb[3]
                        )
                        if score < best_score:
                            best_score     = score
                            best_placement = (bay_id, cx, cy, oi, entry, exit_t)

        if best_placement is None:
            if not allow_force:
                raise _LnsRepairFailed(dict(result))
            best_placement = _force_place(bi, blocks_data, bays, bay_schedule, prefs)
            n_fallback += 1

        if used_forced:
            n_forced += 1

        bay_id, cx, cy, oi, entry, exit_t = best_placement
        final_blk = Block(block_id=bi, block_data=blk_data, x=cx, y=cy, orient_idx=oi)
        bay_placed[bay_id].append(final_blk)
        bay_schedule[bay_id].append((entry, exit_t))
        bay_loads[bay_id] += workload

        result[bi] = {
            "block_id":   bi,
            "bay_id":     bay_id,
            "x":          int(round(cx)),
            "y":          int(round(cy)),
            "orient_idx": oi,
            "entry_time": int(round(entry)),
            "exit_time":  int(round(exit_t)),
        }

        if log_interval > 0 and t_start is not None:
            n_done = rank + 1
            if n_done % log_interval == 0 or n_done == n_total:
                elapsed = time.time() - t_start
                loads_str = " ".join(f"b{i}={round(bay_loads[i])}" for i in range(n_bays))
                flag = " [forced]" if used_forced else (" [fallback]" if best_score == float("inf") else "")
                print(f"[Greedy]   {n_done:4d}/{n_total}"
                      f"  block{bi:<4d} -> bay{bay_id} ({cx},{cy}) oi={oi}"
                      f"  t=[{int(round(entry))},{int(round(exit_t))})"
                      f"  loads=[{loads_str}]"
                      f"  fallback={n_fallback}{flag}"
                      f"  {elapsed:.1f}s")

    return result


# -----------------------------------------------------------------------------
# Phase 2: repair infeasible blocks
# -----------------------------------------------------------------------------

def _repair(prob_info: dict,
            sol: dict,
            assignments: dict[int, dict],
            bays: list[Bay],
            blocks_data: list[dict],
            w1: float, w2: float, w3: float,
            t_start: float,
            timelimit: float,
            max_passes: int = 10,
            repair_mode: str = "greedy",
            block_order_mode: str = "edd",
            deadline: float | None = None) -> dict[int, dict]:
    """
    Iteratively detect infeasible blocks and repair them.

    Runs up to max_passes rounds of: check_feasibility -> collect violating
    block ids -> re-place them.  Stops early if the solution becomes feasible
    or 98% of timelimit is consumed.

    -- repair_mode="greedy" (default) ------------------------------------------
    Violating blocks are removed from assignments and re-placed using the full
    Phase-1 search (all bays, orientations, positions, time-slots).  The state
    arrays (bay_placed, bay_schedule, bay_loads) are reconstructed from the
    remaining non-violating assignments before each block is re-placed, so the
    search sees the current bay state.

    Cycle detection:
      repaired_counts[bid] tracks how many repair passes have touched block bid.
      If bid appears in a second pass (count > 1) it is added to forced_ids.
      Blocks in forced_ids skip search and go straight to _force_place (empty-
      bay window), which is structurally guaranteed to produce a crane-feasible
      placement.  This breaks cycles where two blocks keep displacing each other.

    Time guard (90% threshold):
      For each block in to_repair, if wall-clock time > 90% of timelimit before
      its turn, it is added to forced_ids.  This ensures all blocks are assigned
      before timeout rather than leaving some unassigned (Stage-1 failure).

    -- repair_mode="simple" -----------------------------------------------------
    Each violating block keeps its current (bay, x, y, orient) and is only
    pushed to the next empty-bay time window via _empty_bay_entry.  Stage-4
    violations (spatial collision) are also reset to position (0, 0).
    Faster than greedy mode, but cannot improve spatial placement quality.

    Parameters
    ----------
    prob_info   : instance JSON dict
    sol         : current solution dict (operations format)
    assignments : current assignment dict (block_id -> assignment dict)
    bays        : Bay objects
    blocks_data : raw block data from prob_info
    w1,w2,w3    : objective weights
    t_start     : wall-clock start time
    timelimit   : total wall-clock time limit
    max_passes  : maximum number of repair iterations
    repair_mode : "greedy" or "simple"
    block_order_mode : ordering mode for violating blocks during repair

    Returns
    -------
    Updated assignments dict (all blocks assigned)
    """
    from utils import check_feasibility

    repaired_counts: dict[int, int] = {}
    forced_ids:      set[int]       = set()

    for pass_idx in range(max_passes):
        _check_deadline_with_assignments(deadline, assignments)
        if time.time() - t_start > timelimit * 0.98:
            break

        result = check_feasibility(prob_info, sol)
        if result["feasible"]:
            break

        viols = result["violations"]
        elapsed_r = time.time() - t_start
        print(f"[Greedy] Repair pass {pass_idx+1}: {len(viols)} violation(s)  "
              f"stage={result['stage']}  elapsed={elapsed_r:.1f}s")

        # -- Parse block ids from violation messages ---------------------------
        # Each violation string contains "block <id>" somewhere in the text.
        # Deduplicate while preserving first-occurrence order.
        to_repair: list[int] = []
        seen: set[int] = set()
        for v in viols:
            try:
                bid = int(v.split("block ")[1].split()[0])
                if bid not in seen:
                    seen.add(bid)
                    to_repair.append(bid)
            except (IndexError, ValueError):
                pass

        if not to_repair:
            break

        to_repair = _ordered_block_ids(to_repair, blocks_data, block_order_mode)
        n_repl = len(to_repair)

        if repair_mode == "simple":
            # -- Simple mode: adjust only the time window, keep position/orient -
            # Rebuild the per-bay time schedule from all current assignments so
            # that _empty_bay_entry can find a gap with no other blocks present.
            n_bays = len(bays)
            bay_schedule: list[list[tuple[int, int]]] = [[] for _ in range(n_bays)]
            for a in assignments.values():
                bay_schedule[a["bay_id"]].append((a["entry_time"], a["exit_time"]))

            for ri, bid in enumerate(to_repair):
                _check_deadline_with_assignments(deadline, assignments)
                a      = assignments[bid]
                bay_id = a["bay_id"]
                r_time = blocks_data[bid]["release_time"]
                proc   = blocks_data[bid]["processing_time"]

                # Remove the block's current slot before searching for a new one
                old_slot = (a["entry_time"], a["exit_time"])
                if old_slot in bay_schedule[bay_id]:
                    bay_schedule[bay_id].remove(old_slot)

                entry  = _empty_bay_entry(bay_schedule[bay_id], r_time, proc)
                exit_t = entry + proc

                # Stage-4 (spatial collision): also reset position to (0,0)
                # to eliminate any spatial overlap with other blocks
                x, y, oi = a["x"], a["y"], a["orient_idx"]
                if result["stage"] == 4:
                    x, y = 0, 0

                assignments[bid] = dict(a, x=x, y=y, orient_idx=oi,
                                        entry_time=int(round(entry)),
                                        exit_time=int(round(exit_t)))
                bay_schedule[bay_id].append((entry, exit_t))

                prev_t = f"[{a['entry_time']},{a['exit_time']})"
                new_t  = f"[{entry},{exit_t})"
                tag    = "[s4->(0,0)]" if result["stage"] == 4 else "[time]"
                elapsed_ri = time.time() - t_start
                print(f"[Greedy]   repair {ri+1:3d}/{n_repl}"
                      f"  block{bid:<4d} {tag}"
                      f"  bay{bay_id} ({int(x)},{int(y)})"
                      f"  {prev_t} -> {new_t}"
                      f"  elapsed={elapsed_ri:.1f}s")

        else:
            # -- Greedy mode: full Phase-1 re-search for violating blocks ------
            # Mark repeat offenders as forced before touching assignments, so
            # the flag is active when _place_blocks processes them below.
            for bid in to_repair:
                repaired_counts[bid] = repaired_counts.get(bid, 0) + 1
                if repaired_counts[bid] > 1:
                    forced_ids.add(bid)

            # Remove violating blocks from assignments so the state reconstruction
            # below does not include their (now invalid) positions/slots.
            for bid in to_repair:
                assignments.pop(bid, None)

            bay_placed, bay_schedule2, bay_loads = _rebuild_lns_state(
                blocks_data,
                bays,
                assignments,
            )

            for ri, bi in enumerate(to_repair):
                _check_deadline_with_assignments(deadline, assignments)
                # Time guard: switch to forced path when 90% of timelimit is used.
                # Without this, a slow repair search could exhaust the timelimit
                # before all blocks are placed, causing Stage-1 (assignment) failures.
                if time.time() - t_start > timelimit * 0.90:
                    forced_ids.add(bi)
                prev_a  = assignments.get(bi)
                try:
                    partial = _place_blocks(
                        [bi], blocks_data, bays,
                        bay_placed, bay_schedule2, bay_loads,
                        w1, w2, w3, forced_ids,
                        prev_assignments=assignments,
                        deadline=deadline,
                    )
                except _TimeBudgetExpired as exc:
                    partial_assignments = dict(assignments)
                    if exc.assignments:
                        partial_assignments.update(exc.assignments)
                    raise _TimeBudgetExpired(partial_assignments) from exc
                assignments.update(partial)
                new_a       = partial[bi]
                is_forced   = bi in forced_ids
                changed_bay = prev_a and prev_a["bay_id"] != new_a["bay_id"]
                changed_pos = prev_a and (prev_a["x"] != new_a["x"]
                                          or prev_a["y"] != new_a["y"])
                tag = ("[forced]" if is_forced
                       else "[bay]" if changed_bay
                       else "[pos]" if changed_pos
                       else "[time]")
                prev_t = (f"[{int(prev_a['entry_time'])},{int(prev_a['exit_time'])})"
                          if prev_a else "N/A")
                new_t  = f"[{int(new_a['entry_time'])},{int(new_a['exit_time'])})"
                elapsed_ri = time.time() - t_start
                print(f"[Greedy]   repair {ri+1:3d}/{n_repl}"
                      f"  block{bi:<4d} {tag}"
                      f"  bay{new_a['bay_id']} ({int(new_a['x'])},{int(new_a['y'])})"
                      f"  {prev_t} -> {new_t}"
                      f"  elapsed={elapsed_ri:.1f}s")

        sol = _solution_from_assignments(assignments)

    result = check_feasibility(prob_info, sol)
    status = "feasible" if result["feasible"] else f"INFEASIBLE stage={result['stage']}"
    obj    = f"obj={result['objective']:.0f}" if result["feasible"] else ""
    forced_note = f"  forced={len(forced_ids)}" if forced_ids else ""
    elapsed_done = time.time() - t_start
    print(f"[Greedy] Repair done  |  {status}  {obj}{forced_note}  elapsed={elapsed_done:.1f}s")

    return assignments


# -----------------------------------------------------------------------------
# Build operations dict from assignments
# -----------------------------------------------------------------------------

def _build_operations(assignments: list[dict]) -> dict:
    """
    Build the "operations" dict from a flat list of assignment dicts.

    Groups operations by integer time-point into buckets, sorts each bucket
    so that EXIT operations precede ENTRY operations at the same time, and
    within each type sorts by block_id for deterministic ordering.

    Bucket tuple format: (sort_key, type_str, block_id, bay_id, x, y, orient_idx)
      sort_key = 0 for EXIT  -> sorts before
      sort_key = 1 for ENTRY -> sorts after
    The sort key ensures EXIT-before-ENTRY without explicit type-string comparison.

    The returned dict maps str(time_int) -> list of operation dicts, ordered as
    required by check_feasibility (EXIT ops first within each time-point).
    """
    buckets: dict[int, list[tuple]] = {}
    for a in assignments:
        t_entry = int(a["entry_time"])
        t_exit  = int(a["exit_time"])
        bid     = a["block_id"]
        bay     = a["bay_id"]
        buckets.setdefault(t_exit,  []).append((0, "EXIT",  bid, bay, None,    None,    None))
        buckets.setdefault(t_entry, []).append((1, "ENTRY", bid, bay, a["x"], a["y"], a["orient_idx"]))

    operations: dict[str, list[dict]] = {}
    for t in sorted(buckets):
        ops = sorted(buckets[t], key=lambda x: (x[0], x[2]))
        result = []
        for _, kind, bid, bay, x, y, orient_idx in ops:
            op: dict = {"type": kind, "block_id": bid, "bay_id": bay}
            if kind == "ENTRY":
                op["x"] = x
                op["y"] = y
                op["orient_idx"] = orient_idx
            result.append(op)
        operations[str(t)] = result
    return operations


# -----------------------------------------------------------------------------
# CLI run
# -----------------------------------------------------------------------------

if __name__ == "__main__":
    import argparse
    import json
    import pathlib
    from collections import defaultdict
    from utils import check_feasibility

    parser = argparse.ArgumentParser(description="EDD greedy algorithm smoke test")
    parser.add_argument("instance", help="path to instance JSON file")
    parser.add_argument("--timelimit", type=float, default=60.0,
                        help="wall-clock time limit in seconds (default: %(default)s)")
    parser.add_argument("--repair", choices=["greedy", "simple"], default="greedy",
                        help="repair mode (default: %(default)s)")
    parser.add_argument("--block-order-mode", choices=_VALID_BLOCK_ORDER_MODES, default="edd",
                        help="block ordering mode (default: %(default)s)")
    args = parser.parse_args()

    inst_file = pathlib.Path(args.instance)

    with open(inst_file) as f:
        prob_info = json.load(f)

    t0  = time.time()
    sol = greedyalgorithm(
        prob_info,
        timelimit=args.timelimit,
        repair_mode=args.repair,
        block_order_mode=args.block_order_mode,
    )
    elapsed = time.time() - t0

    result = check_feasibility(prob_info, sol)

    n_assigned = sum(1 for ops in sol["operations"].values()
                     for op in ops if op["type"] == "ENTRY")
    print(f"Instance : {prob_info['name']}")
    print(f"Elapsed  : {elapsed:.3f}s")
    print(f"Assigned : {n_assigned} / {len(prob_info['blocks'])} blocks")
    print(f"Feasible : {result['feasible']}  (stage={result['stage']})")
    if result["feasible"]:
        print(f"Objective: {result['objective']:.2f}  "
              f"(obj1={result['obj1']:.1f}, obj2={result['obj2']:.1f}, obj3={result['obj3']:.1f})")
    else:
        for v in result["violations"][:10]:
            print(f"  VIOLATION: {v}")
