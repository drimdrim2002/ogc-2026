"""Copy-based, checker-guarded orchestration for fixed-layout retiming."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass

from .budget import Budget
from .exact import (
    ExactResult,
    PilotSelection,
    RetimeCall,
    RetimeRequest,
    SOLUTION_STATUSES,
    deadline_bounded_call,
    select_pilot_backend,
    validate_result,
)
from .incumbent import VerifiedIncumbent
from .state import Placement, SolutionState


@dataclass(frozen=True, slots=True)
class RetimeBayOutcome:
    """One fixed-backend call and its copy-only candidate, if improving."""

    bay_id: int
    backend: str
    request: RetimeRequest
    result: ExactResult
    candidate: SolutionState | None
    timebox: float


@dataclass(frozen=True, slots=True)
class RetimeAttempt:
    """Audit record for one bay in the fixed-backend sweep."""

    bay_id: int
    backend: str
    status: str
    accepted: bool
    before_z1: float
    candidate_z1: float | None
    timebox: float
    reason: str | None


@dataclass(frozen=True, slots=True)
class RetimeSweepOutcome:
    """Final checker-accepted state plus pilot and per-bay telemetry."""

    state: SolutionState
    pilot: PilotSelection
    attempts: tuple[RetimeAttempt, ...]
    accepted_bays: int


def build_conflict_pairs(
    state: SolutionState,
    bay_id: int,
) -> tuple[tuple[int, int], ...]:
    """Return exactly the fixed-layout union-overlap pairs in one bay."""

    placements = sorted(
        (item for item in state.placements.values() if item.bay_id == bay_id),
        key=lambda item: item.block_id,
    )
    pairs: list[tuple[int, int]] = []
    for index, left in enumerate(placements):
        left_shape = state.shape_info(left)
        for right in placements[index + 1 :]:
            right_shape = state.shape_info(right)
            if not state.geom.union_disjoint(
                left_shape,
                right_shape,
                right.x - left.x,
                right.y - left.y,
            ):
                pairs.append((left.block_id, right.block_id))
    return tuple(pairs)


def apply_retime_copy(
    state: SolutionState,
    request: RetimeRequest,
    result: ExactResult,
) -> SolutionState | None:
    """Apply a valid strict-Z1 improvement to a fresh state copy only."""

    valid, _reason = validate_result(request, result)
    if not valid or result.status not in SOLUTION_STATUSES or result.solution is None:
        return None
    current_entries = dict(request.current_entries)
    if any(
        (placement := state.get(block_id)) is None
        or placement.entry != current_entries[block_id]
        for block_id in request.block_ids
    ):
        return None

    candidate = _copy_state(state)
    for block_id, entry, exit_time in result.solution:
        placement = candidate.get(block_id)
        if placement is None:
            return None
        candidate.place(
            Placement(
                block_id=placement.block_id,
                bay_id=placement.bay_id,
                x=placement.x,
                y=placement.y,
                orient_idx=placement.orient_idx,
                entry=entry,
                exit=exit_time,
            )
        )
    candidate.assert_invariants()
    tolerance = 1e-9 * max(1.0, abs(state.z1))
    if candidate.z1 >= state.z1 - tolerance:
        return None
    return candidate


def retime_bay(
    state: SolutionState,
    bay_id: int,
    backend: str,
    call: RetimeCall,
    *,
    budget: Budget,
    seed: int = 20260710,
    threads: int = 4,
) -> RetimeBayOutcome:
    """Run one backend within the S2 per-call timebox and return a copy."""

    request = _request_for_bay(state, bay_id, seed=seed, threads=threads)
    timebox = min(5.0, 0.10 * budget.remaining)
    result = deadline_bounded_call(
        backend,
        request,
        call,
        timebox=timebox,
        budget=budget,
    )
    return RetimeBayOutcome(
        bay_id=bay_id,
        backend=backend,
        request=request,
        result=result,
        candidate=apply_retime_copy(state, request, result),
        timebox=timebox,
    )


def retime_sweep(
    state: SolutionState,
    incumbent: VerifiedIncumbent,
    backend_calls: Mapping[str, RetimeCall],
    *,
    available_backends: Iterable[str],
    budget: Budget,
    first_sweep_budget: float,
    seed: int = 20260710,
    threads: int = 4,
) -> RetimeSweepOutcome:
    """Pilot once, fix a winner, and checker-guard each improving bay copy."""

    if not incumbent.has_incumbent:
        raise ValueError("checker-verified incumbent required before retiming")
    ordered_bays = _tardiest_bays(state)
    pilot_requests = tuple(
        _request_for_bay(state, bay_id, seed=seed, threads=threads)
        for bay_id in ordered_bays[:2]
    )
    pilot = select_pilot_backend(
        pilot_requests,
        backend_calls,
        available_backends=available_backends,
        budget=budget,
        first_sweep_budget=first_sweep_budget,
    )
    current = _copy_state(state)
    if pilot.backend is None:
        return RetimeSweepOutcome(current, pilot, (), 0)

    attempts: list[RetimeAttempt] = []
    accepted_bays = 0
    for bay_id in ordered_bays:
        if budget.remaining <= 0.0:
            break
        before_z1 = current.z1
        outcome = retime_bay(
            current,
            bay_id,
            pilot.backend,
            backend_calls[pilot.backend],
            budget=budget,
            seed=seed,
            threads=threads,
        )
        candidate_z1 = (
            outcome.candidate.z1 if outcome.candidate is not None else None
        )
        accepted = False
        reason = outcome.result.reason
        if outcome.candidate is not None:
            accepted = incumbent.try_update(outcome.candidate)
            if accepted:
                current = outcome.candidate
                accepted_bays += 1
                reason = None
            else:
                reason = "official checker rejected incumbent replacement"
        elif reason is None:
            reason = "invalid or non-improving retime result"
        attempts.append(
            RetimeAttempt(
                bay_id=bay_id,
                backend=pilot.backend,
                status=outcome.result.status,
                accepted=accepted,
                before_z1=before_z1,
                candidate_z1=candidate_z1,
                timebox=outcome.timebox,
                reason=reason,
            )
        )
    return RetimeSweepOutcome(current, pilot, tuple(attempts), accepted_bays)


def _request_for_bay(
    state: SolutionState,
    bay_id: int,
    *,
    seed: int,
    threads: int,
) -> RetimeRequest:
    placements = tuple(
        sorted(
            (item for item in state.placements.values() if item.bay_id == bay_id),
            key=lambda item: item.block_id,
        )
    )
    block_ids = tuple(item.block_id for item in placements)
    return RetimeRequest(
        block_ids=block_ids,
        releases=tuple(
            (block_id, state.instance.blocks[block_id].release_time)
            for block_id in block_ids
        ),
        dues=tuple(
            (block_id, state.instance.blocks[block_id].due_date)
            for block_id in block_ids
        ),
        dwells=tuple(
            (block_id, state.instance.blocks[block_id].dwell)
            for block_id in block_ids
        ),
        current_entries=tuple((item.block_id, item.entry) for item in placements),
        conflict_pairs=build_conflict_pairs(state, bay_id),
        seed=seed,
        threads=threads,
    )


def _tardiest_bays(state: SolutionState) -> tuple[int, ...]:
    scores = []
    for bay_id in range(len(state.instance.bays)):
        placements = tuple(
            item for item in state.placements.values() if item.bay_id == bay_id
        )
        if not placements:
            continue
        tardiness = sum(
            max(0, item.exit - state.instance.blocks[item.block_id].due_date)
            for item in placements
        )
        scores.append((-tardiness, bay_id))
    return tuple(bay_id for _negative_tardiness, bay_id in sorted(scores))


def _copy_state(state: SolutionState) -> SolutionState:
    copied = SolutionState(
        state.instance,
        geom=state.geom,
        shape_catalog=state.shape_catalog,
    )
    for placement in state.placements.values():
        copied.place(placement)
    return copied
