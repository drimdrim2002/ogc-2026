"""Transactional intra-bay destroy/repair core for S3-01."""

from __future__ import annotations

from collections.abc import Callable
from copy import deepcopy
from dataclasses import dataclass

from numpy.random import Generator

from .budget import Budget
from .construct import ESCALATED_ANCHOR_CAP, ESCALATED_TIME_CAP, insert_block
from .state import ObjectiveDiagnostics, Placement, SolutionState, StateUndoToken
from .validate import validate_insertion


MutationHook = Callable[[str, int], None]
Acceptance = Callable[[ObjectiveDiagnostics, ObjectiveDiagnostics], bool]


@dataclass(frozen=True, slots=True)
class IterationResult:
    """Outcome of one and only one transactional D1/R3 candidate."""

    committed: bool
    reason: str
    removed_block_ids: tuple[int, ...]
    mutation_count: int
    before_objective: float
    after_objective: float


class MoveTransaction:
    """Own an exact state token and RNG checkpoint until commit or rollback."""

    def __init__(
        self,
        state: SolutionState,
        rng: Generator,
        *,
        on_mutation: MutationHook | None = None,
    ) -> None:
        if not isinstance(rng, Generator):
            raise TypeError("rng must be a numpy.random.Generator")
        self.state = state
        self.rng = rng
        self.undo_token: StateUndoToken = state.capture_undo_token()
        self._rng_state = deepcopy(rng.bit_generator.state)
        self._on_mutation = on_mutation
        self.mutation_count = 0
        self._closed = False
        self._committed = False

    @property
    def committed(self) -> bool:
        return self._committed

    def record_mutation(self, event: str) -> None:
        """Record an already-applied mutation and expose its fault boundary."""
        if self._closed:
            raise RuntimeError("transaction is closed")
        index = self.mutation_count
        self.mutation_count += 1
        if self._on_mutation is not None:
            self._on_mutation(event, index)

    def commit(self) -> None:
        if self._closed:
            raise RuntimeError("transaction is closed")
        self.state.assert_invariants()
        self._committed = True
        self._closed = True

    def rollback(self) -> None:
        if self._closed:
            if self._committed:
                raise RuntimeError("a committed transaction cannot be rolled back")
            return
        self.state.restore_undo_token(self.undo_token)
        self.rng.bit_generator.state = deepcopy(self._rng_state)
        if self.state.capture_undo_token() != self.undo_token:
            raise AssertionError("transaction rollback was not byte-equivalent")
        if self.rng.bit_generator.state != self._rng_state:
            raise AssertionError("transaction RNG rollback was not exact")
        self._closed = True

    def __enter__(self) -> "MoveTransaction":
        return self

    def __exit__(self, exc_type, exc, traceback) -> bool:
        if not self._closed:
            self.rollback()
        return False


class D1RandomRemoval:
    """Remove a deterministic PCG64 sample from the current placements."""

    changes_assignment = False

    def destroy(
        self,
        transaction: MoveTransaction,
        count: int,
        *,
        budget: Budget | None = None,
    ) -> tuple[Placement, ...]:
        if isinstance(count, bool) or not isinstance(count, int) or count <= 0:
            raise ValueError("remove_count must be a positive integer")
        candidates = tuple(transaction.state.placements)
        if count > len(candidates):
            raise ValueError("remove_count exceeds the number of placed blocks")
        selected = tuple(
            int(block_id)
            for block_id in transaction.rng.choice(
                candidates, size=count, replace=False
            ).tolist()
        )
        removed: list[Placement] = []
        for block_id in selected:
            if budget is not None:
                budget.checkpoint("S3 D1 removal")
            placement = transaction.state.remove(block_id)
            if placement is None:
                raise AssertionError(f"D1 selected absent block {block_id}")
            removed.append(placement)
            transaction.record_mutation(f"remove:{block_id}")
        return tuple(removed)


class R3EDDReinsertion:
    """Reinsert removed blocks by due date in exactly their original bays."""

    changes_assignment = False

    def repair(
        self,
        transaction: MoveTransaction,
        removed: tuple[Placement, ...],
        *,
        budget: Budget | None = None,
    ) -> bool:
        state = transaction.state
        ordered = sorted(
            removed,
            key=lambda placement: (
                state.instance.blocks[placement.block_id].due_date,
                placement.block_id,
            ),
        )
        for original in ordered:
            if budget is not None:
                budget.checkpoint("S3 R3 reinsertion")
            candidate = insert_block(
                state,
                original.block_id,
                bays_try=(original.bay_id,),
                preferred_orient_idx=original.orient_idx,
                time_cap=ESCALATED_TIME_CAP,
                anchor_cap=ESCALATED_ANCHOR_CAP,
            )
            if candidate is None or candidate.placement.bay_id != original.bay_id:
                return False
            if not validate_insertion(state, candidate.placement):
                return False
            if state.place(candidate.placement) is not None:
                raise AssertionError("R3 replaced a block that was not removed")
            transaction.record_mutation(f"insert:{original.block_id}")
        return True


D1 = D1RandomRemoval
R3 = R3EDDReinsertion


def _repair_r3(
    transaction: MoveTransaction,
    removed: tuple[Placement, ...],
    *,
    budget: Budget | None,
) -> bool:
    return R3EDDReinsertion().repair(transaction, removed, budget=budget)


def run_transactional_iteration(
    state: SolutionState,
    rng: Generator,
    *,
    remove_count: int = 1,
    accept: Acceptance,
    budget: Budget | None = None,
    on_mutation: MutationHook | None = None,
) -> IterationResult:
    """Build and decide one D1/R3 candidate with rollback on every exit path."""
    before = state.objective_diagnostics
    with MoveTransaction(state, rng, on_mutation=on_mutation) as transaction:
        if budget is not None:
            budget.checkpoint("S3 transaction start")
        removed = D1RandomRemoval().destroy(
            transaction, remove_count, budget=budget
        )
        if not _repair_r3(transaction, removed, budget=budget):
            return IterationResult(
                False,
                "repair_failed",
                tuple(item.block_id for item in removed),
                transaction.mutation_count,
                before.objective,
                before.objective,
            )
        state.assert_invariants()
        if budget is not None:
            budget.checkpoint("S3 transaction decision")
        after = state.objective_diagnostics
        if not accept(before, after):
            return IterationResult(
                False,
                "rejected",
                tuple(item.block_id for item in removed),
                transaction.mutation_count,
                before.objective,
                after.objective,
            )
        transaction.commit()
        return IterationResult(
            True,
            "accepted",
            tuple(item.block_id for item in removed),
            transaction.mutation_count,
            before.objective,
            after.objective,
        )
