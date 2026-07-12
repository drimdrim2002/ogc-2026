"""Transactional intra-bay destroy/repair operators for S3."""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Callable, Iterable, Mapping, Sequence
from copy import deepcopy
from dataclasses import dataclass
import hashlib
import json
import math
from statistics import median
from types import MappingProxyType
from typing import Any

from numpy.random import Generator

from .budget import Budget, BudgetExpired
from .checker_adapter import CheckerResult, official_check
from .config import DEFAULT_CONFIG
from .construct import (
    ESCALATED_ANCHOR_CAP,
    InsertionCandidate,
    first_fit,
    insert_block,
)
from .incumbent import VerifiedIncumbent
from .serialize import serialize_non_interlock
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


@dataclass(frozen=True, slots=True)
class OperatorAttemptResult:
    """One transactional destroy/repair attempt and its structural facts."""

    committed: bool
    reason: str
    destroy_name: str
    repair_name: str
    source_bay: int | None
    removed_block_ids: tuple[int, ...]
    mutation_count: int


@dataclass(slots=True)
class OperatorMetrics:
    """Attempt/success/failure counters for one registered operator."""

    attempts: int = 0
    successes: int = 0
    failures: int = 0


@dataclass(frozen=True, slots=True)
class ALNSIterationEvent:
    """Pre-commit classification emitted before any incumbent replacement."""

    iteration: int
    destroy_name: str
    repair_name: str
    previous_cur_obj: float
    new_obj: float
    outcome: str
    accepted: bool
    potential_incumbent: bool


@dataclass(slots=True)
class ALNSMetrics:
    """Strict-loop structural, safety, and outcome counters."""

    iterations: int = 0
    proposals: int = 0
    accepted: int = 0
    rejected: int = 0
    improving: int = 0
    current_equal: int = 0
    worse: int = 0
    not_applicable: int = 0
    repair_failures: int = 0
    full_checks: int = 0
    safety_samples: int = 0
    checker_failures: int = 0
    faults: int = 0
    deadlines: int = 0


@dataclass(frozen=True, slots=True)
class IncumbentTraceEntry:
    """One checker-verified incumbent boundary in strictly improving order."""

    iteration: int
    objective: float
    obj1: float | None
    obj2: float | None
    obj3: float | None
    checker_stage: int
    solution_sha256: str


@dataclass(frozen=True, slots=True)
class ALNSRunResult:
    """Safe S3-03 return object containing only stored incumbent operations."""

    solution: dict[str, Any]
    metrics: ALNSMetrics
    incumbent_trace: tuple[IncumbentTraceEntry, ...]
    stopped_reason: str


class StrictAcceptor:
    """Accept only a relative-EPS strict improvement over saved current state."""

    __slots__ = ("epsilon",)

    def __init__(self, *, epsilon: float = 1e-9) -> None:
        if (
            isinstance(epsilon, bool)
            or not math.isfinite(epsilon)
            or epsilon < 0.0
        ):
            raise ValueError("epsilon must be a finite non-negative number")
        self.epsilon = float(epsilon)

    def tolerance(self, previous_cur_obj: float) -> float:
        return self.epsilon * max(1.0, abs(previous_cur_obj))

    def classify(self, previous_cur_obj: float, new_obj: float) -> str:
        tolerance = self.tolerance(previous_cur_obj)
        if new_obj < previous_cur_obj - tolerance:
            return "improving"
        if abs(new_obj - previous_cur_obj) <= tolerance:
            return "current_equal"
        return "worse"

    def accept(self, previous_cur_obj: float, new_obj: float) -> bool:
        return self.classify(previous_cur_obj, new_obj) == "improving"


def sample_destroy_count(
    block_count: int,
    rng: Generator,
    *,
    min_fraction: float = 0.02,
    max_fraction: float = 0.06,
    cap_fraction: float = 0.15,
) -> int:
    """Sample q from the configured 2%-6% working range, capped at 15%."""
    if isinstance(block_count, bool) or not isinstance(block_count, int):
        raise TypeError("block_count must be an integer")
    if block_count <= 0:
        raise ValueError("block_count must be positive")
    if not 0 < min_fraction <= max_fraction <= cap_fraction <= 1:
        raise ValueError("destroy fractions must satisfy 0 < min <= max <= cap <= 1")
    lower = min(block_count, max(2, math.ceil(min_fraction * block_count)))
    upper = min(
        block_count,
        max(lower, max(4, math.ceil(max_fraction * block_count))),
        max(lower, math.ceil(cap_fraction * block_count)),
    )
    return int(rng.integers(lower, upper + 1))


class DestroyOperator(ABC):
    """Select and remove a same-bay block set through a MoveTransaction."""

    name = "destroy"
    changes_assignment = False

    @abstractmethod
    def select(
        self, state: SolutionState, rng: Generator, count: int
    ) -> tuple[int, ...]:
        """Return unique placed block IDs from exactly one bay."""

    def destroy(
        self,
        transaction: "MoveTransaction",
        count: int,
        *,
        budget: Budget | None = None,
    ) -> tuple[Placement, ...]:
        if isinstance(count, bool) or not isinstance(count, int) or count <= 0:
            raise ValueError("remove_count must be a positive integer")
        selected = self.select(transaction.state, transaction.rng, count)
        if len(selected) != count or len(set(selected)) != count:
            return ()
        originals = tuple(transaction.state.get(block_id) for block_id in selected)
        if any(item is None for item in originals):
            raise AssertionError(f"{self.name} selected an absent block")
        source_bays = {item.bay_id for item in originals if item is not None}
        if len(source_bays) != 1:
            raise AssertionError(f"{self.name} selected blocks from multiple bays")
        removed: list[Placement] = []
        for block_id in selected:
            if budget is not None:
                budget.checkpoint(f"S3 {self.name.upper()} removal")
            placement = transaction.state.remove(block_id)
            if placement is None:
                raise AssertionError(f"{self.name} selected absent block {block_id}")
            removed.append(placement)
            transaction.record_mutation(f"remove:{self.name}:{block_id}")
        return tuple(removed)


class RepairOperator(ABC):
    """All-or-nothing original-bay repair contract."""

    name = "repair"
    changes_assignment = False

    @abstractmethod
    def repair(
        self,
        transaction: "MoveTransaction",
        removed: tuple[Placement, ...],
        *,
        budget: Budget | None = None,
    ) -> bool:
        """Reinsert every block or return false for transaction rollback."""


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


class D1RandomRemoval(DestroyOperator):
    """Remove a deterministic PCG64 sample from the current placements."""

    name = "d1"
    changes_assignment = False

    def select(
        self, state: SolutionState, rng: Generator, count: int
    ) -> tuple[int, ...]:
        bay_id = _random_eligible_bay(state, rng, count)
        if bay_id is None:
            return ()
        candidates = state.bay_members[bay_id]
        return tuple(
            int(block_id)
            for block_id in rng.choice(
                candidates, size=count, replace=False
            ).tolist()
        )


class R3EDDReinsertion(RepairOperator):
    """Reinsert removed blocks by due date in exactly their original bays."""

    name = "r3"
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
                time_cap=None,
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


class D2WorstTardiness(DestroyOperator):
    """Biased same-bay removal by weighted tardiness, then waiting time."""

    name = "d2"

    def select(
        self, state: SolutionState, rng: Generator, count: int
    ) -> tuple[int, ...]:
        bay_id = _worst_eligible_bay(state, count)
        if bay_id is None:
            return ()
        ranked = _worst_ranked(state, state.bay_members[bay_id])
        return _biased_without_replacement(ranked, rng, count, rho=3.0)


class D3CriticalChain(DestroyOperator):
    """Follow exact active union-conflict predecessors within one bay."""

    name = "d3"

    def select(
        self, state: SolutionState, rng: Generator, count: int
    ) -> tuple[int, ...]:
        bay_id = _worst_eligible_bay(state, count)
        if bay_id is None:
            return ()
        members = state.bay_members[bay_id]
        seed = _worst_ranked(state, members)[0]
        selected = [seed]
        current = state.get(seed)
        while current is not None and len(selected) < count:
            predecessors = []
            for block_id in members:
                if block_id in selected:
                    continue
                candidate = state.get(block_id)
                if candidate is None or candidate.exit != current.entry:
                    continue
                if _placements_conflict(state, candidate, current):
                    predecessors.append(candidate)
            if not predecessors:
                break
            current = min(
                predecessors,
                key=lambda item: (-_tardiness(state, item), item.block_id),
            )
            selected.append(current.block_id)
        if len(selected) < count:
            related = _shaw_ranked(state, seed, members)
            selected.extend(
                block_id
                for block_id in related
                if block_id not in selected
            )
        return tuple(selected[:count])


class D4TimeWindow(DestroyOperator):
    """Remove blocks intersecting a tardy-centered same-bay time window."""

    name = "d4"

    def select(
        self, state: SolutionState, rng: Generator, count: int
    ) -> tuple[int, ...]:
        eligible = _eligible_bays(state, count)
        if not eligible:
            return ()
        weights = [
            sum(
                _tardiness(state, state.get(block_id))
                for block_id in state.bay_members[bay_id]
            )
            for bay_id in eligible
        ]
        bay_id = _weighted_choice(eligible, weights, rng)
        members = state.bay_members[bay_id]
        tardy = [
            block_id
            for block_id in _worst_ranked(state, members)
            if _tardiness(state, state.get(block_id)) > 0
        ]
        center_id = tardy[0] if tardy else _worst_ranked(state, members)[0]
        center = state.get(center_id)
        if center is None:
            return ()
        dwell_median = float(
            median(state.instance.blocks[block_id].dwell for block_id in members)
        )
        width = max(1.0, 2.0 * dwell_median)
        midpoint = (center.entry + center.exit) / 2.0
        placements = tuple(
            placement
            for block_id in members
            if (placement := state.get(block_id)) is not None
        )
        intersecting: list[tuple[float, float, int]] = []
        while True:
            intersecting = []
            for placement in placements:
                overlap = max(
                    0.0,
                    min(float(placement.exit), midpoint + width / 2.0)
                    - max(float(placement.entry), midpoint - width / 2.0),
                )
                if overlap > 0.0:
                    distance = abs(
                        (placement.entry + placement.exit) / 2.0 - midpoint
                    )
                    intersecting.append((-overlap, distance, placement.block_id))
            if len(intersecting) >= count or width >= 2.0 * _instance_horizon(state):
                break
            width *= 1.5
        intersecting.sort()
        selected = [item[2] for item in intersecting[:count]]
        if len(selected) >= count:
            return tuple(selected)
        scored: list[tuple[float, int]] = []
        for block_id in members:
            if block_id in selected:
                continue
            placement = state.get(block_id)
            if placement is None:
                continue
            distance = abs((placement.entry + placement.exit) / 2.0 - midpoint)
            scored.append((distance, block_id))
        scored.sort()
        selected.extend(item[1] for item in scored[: count - len(selected)])
        return tuple(selected)


class D5ShawRelated(DestroyOperator):
    """Biased Shaw-related removal anchored at the worst same-bay block."""

    name = "d5"

    def select(
        self, state: SolutionState, rng: Generator, count: int
    ) -> tuple[int, ...]:
        bay_id = _worst_eligible_bay(state, count)
        if bay_id is None:
            return ()
        members = state.bay_members[bay_id]
        seed = _biased_without_replacement(
            _worst_ranked(state, members), rng, 1, rho=3.0
        )[0]
        related = tuple(
            block_id for block_id in _shaw_ranked(state, seed, members)
            if block_id != seed
        )
        tail = _biased_without_replacement(related, rng, count - 1, rho=6.0)
        return (seed, *tail)


class R1NoisyGreedy(RepairOperator):
    """Slack-ordered greedy repair with deterministic multiplicative noise."""

    name = "r1"

    def repair(
        self,
        transaction: MoveTransaction,
        removed: tuple[Placement, ...],
        *,
        budget: Budget | None = None,
    ) -> bool:
        ordered = sorted(
            removed,
            key=lambda item: (
                transaction.state.instance.blocks[item.block_id].slack,
                item.block_id,
            ),
        )
        for original in ordered:
            if budget is not None:
                budget.checkpoint("S3 R1 reinsertion")
            options = _original_bay_options(transaction.state, original)
            if not options:
                return False
            scored = tuple(
                (
                    _candidate_cost(candidate)
                    * float(transaction.rng.uniform(0.9, 1.1)),
                    candidate.score,
                    candidate,
                )
                for candidate in options
            )
            if not _install_candidate(transaction, min(scored)[2], original):
                return False
        return True


class R2Regret2(RepairOperator):
    """Original-bay regret-2 repair over distinct orientation choices."""

    name = "r2"

    def repair(
        self,
        transaction: MoveTransaction,
        removed: tuple[Placement, ...],
        *,
        budget: Budget | None = None,
    ) -> bool:
        remaining = {item.block_id: item for item in removed}
        horizon = _instance_horizon(transaction.state)
        w1 = transaction.state.instance.weights[0]
        while remaining:
            if budget is not None:
                budget.checkpoint("S3 R2 regret evaluation")
            choices: list[tuple[float, float, int, Placement, InsertionCandidate]] = []
            for block_id, original in remaining.items():
                options = sorted(
                    _original_bay_options(transaction.state, original),
                    key=lambda item: (_candidate_cost(item), item.score),
                )
                if not options:
                    return False
                first_cost = _candidate_cost(options[0])
                second_cost = (
                    _candidate_cost(options[1])
                    if len(options) > 1
                    else first_cost + w1 * horizon
                )
                choices.append(
                    (
                        -(second_cost - first_cost),
                        first_cost,
                        block_id,
                        original,
                        options[0],
                    )
                )
            _neg_regret, _cost, block_id, original, candidate = min(choices)
            if not _install_candidate(transaction, candidate, original):
                return False
            del remaining[block_id]
        return True


class OperatorRegistry:
    """Validated S3 intra-bay operator pool with structural counters."""

    def __init__(
        self,
        *,
        destroys: Sequence[DestroyOperator] | None = None,
        repairs: Sequence[RepairOperator] | None = None,
    ) -> None:
        destroy_items = tuple(destroys) if destroys is not None else (
            D1RandomRemoval(),
            D2WorstTardiness(),
            D3CriticalChain(),
            D4TimeWindow(),
            D5ShawRelated(),
        )
        repair_items = tuple(repairs) if repairs is not None else (
            R1NoisyGreedy(),
            R2Regret2(),
            R3EDDReinsertion(),
        )
        _validate_registry((*destroy_items, *repair_items))
        self.destroys: Mapping[str, DestroyOperator] = MappingProxyType(
            {operator.name: operator for operator in destroy_items}
        )
        self.repairs: Mapping[str, RepairOperator] = MappingProxyType(
            {operator.name: operator for operator in repair_items}
        )
        self._metrics = {
            name: OperatorMetrics()
            for name in (*self.destroys, *self.repairs)
        }

    @property
    def destroy_names(self) -> tuple[str, ...]:
        return tuple(self.destroys)

    @property
    def repair_names(self) -> tuple[str, ...]:
        return tuple(self.repairs)

    @property
    def metrics(self) -> Mapping[str, OperatorMetrics]:
        return MappingProxyType(self._metrics)

    def attempt(
        self,
        state: SolutionState,
        rng: Generator,
        *,
        destroy_name: str,
        repair_name: str,
        remove_count: int | None = None,
        commit: bool = True,
        budget: Budget | None = None,
        on_mutation: MutationHook | None = None,
    ) -> OperatorAttemptResult:
        destroy = self.destroys[destroy_name]
        repair = self.repairs[repair_name]
        count = (
            sample_destroy_count(
                len(state.placements),
                rng,
                min_fraction=DEFAULT_CONFIG.alns_destroy_min_fraction,
                max_fraction=DEFAULT_CONFIG.alns_destroy_max_fraction,
                cap_fraction=DEFAULT_CONFIG.alns_destroy_cap_fraction,
            )
            if remove_count is None
            else remove_count
        )
        for name in (destroy_name, repair_name):
            self._metrics[name].attempts += 1
        try:
            with MoveTransaction(state, rng, on_mutation=on_mutation) as transaction:
                removed = destroy.destroy(transaction, count, budget=budget)
                if len(removed) != count:
                    return self._failure(
                        destroy_name,
                        repair_name,
                        "not_applicable",
                        None,
                        (),
                        transaction.mutation_count,
                    )
                source_bays = {item.bay_id for item in removed}
                if len(source_bays) != 1:
                    raise AssertionError("destroy escaped its single-bay boundary")
                source_bay = next(iter(source_bays))
                if not repair.repair(transaction, removed, budget=budget):
                    return self._failure(
                        destroy_name,
                        repair_name,
                        "repair_failed",
                        source_bay,
                        tuple(item.block_id for item in removed),
                        transaction.mutation_count,
                    )
                for original in removed:
                    replacement = state.get(original.block_id)
                    if replacement is None or replacement.bay_id != original.bay_id:
                        raise AssertionError("repair changed cross-bay membership")
                state.assert_invariants()
                if not commit:
                    return self._failure(
                        destroy_name,
                        repair_name,
                        "rejected",
                        source_bay,
                        tuple(item.block_id for item in removed),
                        transaction.mutation_count,
                    )
                transaction.commit()
                for name in (destroy_name, repair_name):
                    self._metrics[name].successes += 1
                return OperatorAttemptResult(
                    True,
                    "accepted",
                    destroy_name,
                    repair_name,
                    source_bay,
                    tuple(item.block_id for item in removed),
                    transaction.mutation_count,
                )
        except Exception:
            for name in (destroy_name, repair_name):
                self._metrics[name].failures += 1
            raise

    def _failure(
        self,
        destroy_name: str,
        repair_name: str,
        reason: str,
        source_bay: int | None,
        removed_block_ids: tuple[int, ...],
        mutation_count: int,
    ) -> OperatorAttemptResult:
        for name in (destroy_name, repair_name):
            self._metrics[name].failures += 1
        return OperatorAttemptResult(
            False,
            reason,
            destroy_name,
            repair_name,
            source_bay,
            removed_block_ids,
            mutation_count,
        )


class _CheckerSafetyError(RuntimeError):
    """Internal signal that a sampled/accepted candidate failed the checker."""


def run_alns(
    state: SolutionState,
    incumbent: VerifiedIncumbent,
    rng: Generator,
    *,
    max_iterations: int,
    registry: OperatorRegistry | None = None,
    remove_count: int | None = None,
    acceptor: StrictAcceptor | None = None,
    safety_sample_interval: int = 0,
    budget: Budget | None = None,
    on_event: Callable[[ALNSIterationEvent], None] | None = None,
) -> ALNSRunResult:
    """Run the S3-03 strict loop while returning only a verified incumbent.

    Candidate state stays inside a ``MoveTransaction`` until strict acceptance,
    optional safety checking, and any potential incumbent full check complete.
    The current objective is assigned only after transaction commit.
    """
    if not isinstance(state, SolutionState):
        raise TypeError("state must be a SolutionState")
    if not isinstance(incumbent, VerifiedIncumbent) or not incumbent.has_incumbent:
        raise ValueError("run_alns requires a checker-verified incumbent")
    if not isinstance(rng, Generator):
        raise TypeError("rng must be a numpy.random.Generator")
    if (
        isinstance(max_iterations, bool)
        or not isinstance(max_iterations, int)
        or max_iterations < 0
    ):
        raise ValueError("max_iterations must be a non-negative integer")
    if remove_count is not None and (
        isinstance(remove_count, bool)
        or not isinstance(remove_count, int)
        or remove_count <= 0
    ):
        raise ValueError("remove_count must be a positive integer or None")
    if (
        isinstance(safety_sample_interval, bool)
        or not isinstance(safety_sample_interval, int)
        or safety_sample_interval < 0
    ):
        raise ValueError("safety_sample_interval must be a non-negative integer")
    strict = StrictAcceptor() if acceptor is None else acceptor
    if not isinstance(strict, StrictAcceptor):
        raise TypeError("S3-03 supports StrictAcceptor only")
    operators = OperatorRegistry() if registry is None else registry
    if not isinstance(operators, OperatorRegistry):
        raise TypeError("registry must be an OperatorRegistry")

    metrics = ALNSMetrics()
    cur_obj = state.objective
    initial = incumbent.checker_result
    if not initial.feasible or initial.objective is None:
        raise ValueError("incumbent must have a feasible checker objective")
    trace = [_trace_entry(0, incumbent.solution, initial)]
    stopped_reason = "max_iterations"

    for iteration in range(1, max_iterations + 1):
        metrics.iterations += 1
        destroy_name: str | None = None
        repair_name: str | None = None
        operator_outcome_recorded = False
        try:
            _checkpoint(budget, "S3 iteration start")
            destroy_name = _uniform_name(operators.destroy_names, rng)
            repair_name = _uniform_name(operators.repair_names, rng)
            destroy = operators.destroys[destroy_name]
            repair = operators.repairs[repair_name]
            count = (
                sample_destroy_count(
                    len(state.placements),
                    rng,
                    min_fraction=DEFAULT_CONFIG.alns_destroy_min_fraction,
                    max_fraction=DEFAULT_CONFIG.alns_destroy_max_fraction,
                    cap_fraction=DEFAULT_CONFIG.alns_destroy_cap_fraction,
                )
                if remove_count is None
                else remove_count
            )
            for name in (destroy_name, repair_name):
                operators._metrics[name].attempts += 1

            previous_cur_obj = cur_obj
            with MoveTransaction(state, rng) as transaction:
                removed = destroy.destroy(transaction, count, budget=budget)
                if len(removed) != count:
                    metrics.not_applicable += 1
                    metrics.rejected += 1
                    _record_operator_failure(operators, destroy_name, repair_name)
                    operator_outcome_recorded = True
                    continue
                source_bays = {item.bay_id for item in removed}
                if len(source_bays) != 1:
                    raise AssertionError("destroy escaped its single-bay boundary")
                if not repair.repair(transaction, removed, budget=budget):
                    metrics.repair_failures += 1
                    metrics.rejected += 1
                    _record_operator_failure(operators, destroy_name, repair_name)
                    operator_outcome_recorded = True
                    continue
                state.assert_invariants()
                _assert_assignment_unchanged(transaction.undo_token, state)
                _checkpoint(budget, "S3 candidate repaired")

                new_obj = state.objective
                outcome = strict.classify(previous_cur_obj, new_obj)
                metrics.proposals += 1
                setattr(metrics, outcome, getattr(metrics, outcome) + 1)
                accepted = strict.accept(previous_cur_obj, new_obj)
                incumbent_objective = incumbent.checker_result.objective
                if incumbent_objective is None:
                    raise AssertionError("verified incumbent objective disappeared")
                potential_incumbent = (
                    new_obj
                    < incumbent_objective - strict.tolerance(incumbent_objective)
                )
                event = ALNSIterationEvent(
                    iteration=iteration,
                    destroy_name=destroy_name,
                    repair_name=repair_name,
                    previous_cur_obj=previous_cur_obj,
                    new_obj=new_obj,
                    outcome=outcome,
                    accepted=accepted,
                    potential_incumbent=potential_incumbent,
                )
                if on_event is not None:
                    on_event(event)
                _checkpoint(budget, "S3 acceptance reported")
                if not accepted:
                    metrics.rejected += 1
                    _record_operator_failure(operators, destroy_name, repair_name)
                    operator_outcome_recorded = True
                    continue

                sample_due = (
                    safety_sample_interval > 0
                    and metrics.proposals % safety_sample_interval == 0
                )
                if potential_incumbent or sample_due:
                    _checkpoint(budget, "S3 before full check")
                    metrics.full_checks += 1
                    updated = False
                    if potential_incumbent:
                        updated = incumbent.try_update(state)
                        checked = incumbent.last_checker_result
                    else:
                        metrics.safety_samples += 1
                        checked = official_check(
                            state.instance.raw,
                            serialize_non_interlock(state.placements.values()),
                        )
                    if not checked.feasible or checked.objective is None:
                        metrics.checker_failures += 1
                        metrics.rejected += 1
                        _record_operator_failure(
                            operators, destroy_name, repair_name
                        )
                        operator_outcome_recorded = True
                        raise _CheckerSafetyError(
                            "official checker rejected an accepted candidate"
                        )
                    if updated:
                        entry = _trace_entry(
                            iteration,
                            incumbent.solution,
                            incumbent.checker_result,
                        )
                        if entry.objective >= trace[-1].objective - strict.tolerance(
                            trace[-1].objective
                        ):
                            raise AssertionError(
                                "verified incumbent trace is not strictly decreasing"
                            )
                        trace.append(entry)

                _checkpoint(budget, "S3 before candidate commit")
                transaction.commit()
                cur_obj = new_obj
                metrics.accepted += 1
                for name in (destroy_name, repair_name):
                    operators._metrics[name].successes += 1
                operator_outcome_recorded = True
            _checkpoint(budget, "S3 after candidate commit")
        except BudgetExpired:
            metrics.deadlines += 1
            stopped_reason = "deadline"
            break
        except _CheckerSafetyError:
            stopped_reason = "checker_failure"
            break
        except Exception as exc:
            if (
                destroy_name is not None
                and repair_name is not None
                and not operator_outcome_recorded
            ):
                _record_operator_failure(operators, destroy_name, repair_name)
            metrics.faults += 1
            stopped_reason = f"fault:{type(exc).__name__}:{exc}"
            break

    return ALNSRunResult(
        solution=incumbent.solution,
        metrics=metrics,
        incumbent_trace=tuple(trace),
        stopped_reason=stopped_reason,
    )


def _checkpoint(budget: Budget | None, label: str) -> None:
    if budget is not None:
        budget.checkpoint(label)


def _uniform_name(names: tuple[str, ...], rng: Generator) -> str:
    if not names:
        raise ValueError("operator registry contains an empty family")
    if len(names) == 1:
        return names[0]
    return names[int(rng.integers(0, len(names)))]


def _record_operator_failure(
    registry: OperatorRegistry, destroy_name: str, repair_name: str
) -> None:
    for name in (destroy_name, repair_name):
        registry._metrics[name].failures += 1


def _assert_assignment_unchanged(
    before: StateUndoToken, state: SolutionState
) -> None:
    after = state.objective_diagnostics
    if before.bay_members != state.bay_members:
        raise AssertionError("S3 candidate changed bay membership")
    if before.bay_loads != after.bay_loads:
        raise AssertionError("S3 candidate changed bay loads")
    if before.z2 != after.z2 or before.z3 != after.z3:
        raise AssertionError("S3 candidate changed Z2 or Z3")


def _trace_entry(
    iteration: int,
    solution: dict[str, Any],
    checked: CheckerResult,
) -> IncumbentTraceEntry:
    if not checked.feasible or checked.objective is None:
        raise ValueError("incumbent trace entries require a feasible checker objective")
    payload = json.dumps(solution, sort_keys=True, separators=(",", ":")).encode()
    return IncumbentTraceEntry(
        iteration=iteration,
        objective=checked.objective,
        obj1=checked.obj1,
        obj2=checked.obj2,
        obj3=checked.obj3,
        checker_stage=checked.stage,
        solution_sha256=hashlib.sha256(payload).hexdigest(),
    )


def _validate_registry(operators: Iterable[DestroyOperator | RepairOperator]) -> None:
    names: set[str] = set()
    for operator in operators:
        if operator.changes_assignment:
            raise ValueError(
                f"S3 operator {operator.name!r} declares changes_assignment=True"
            )
        if not operator.name or operator.name in names:
            raise ValueError(f"duplicate or empty operator name {operator.name!r}")
        names.add(operator.name)


def _eligible_bays(state: SolutionState, count: int) -> tuple[int, ...]:
    return tuple(
        bay_id
        for bay_id, members in enumerate(state.bay_members)
        if len(members) >= count
    )


def _random_eligible_bay(
    state: SolutionState, rng: Generator, count: int
) -> int | None:
    eligible = _eligible_bays(state, count)
    if not eligible:
        return None
    return eligible[int(rng.integers(0, len(eligible)))]


def _worst_eligible_bay(state: SolutionState, count: int) -> int | None:
    eligible = _eligible_bays(state, count)
    if not eligible:
        return None
    return min(
        eligible,
        key=lambda bay_id: (
            -sum(
                _tardiness(state, state.get(block_id))
                for block_id in state.bay_members[bay_id]
            ),
            bay_id,
        ),
    )


def _tardiness(state: SolutionState, placement: Placement | None) -> float:
    if placement is None:
        return 0.0
    block = state.instance.blocks[placement.block_id]
    return state.instance.weights[0] * max(0.0, placement.exit - block.due_date)


def _worst_ranked(
    state: SolutionState, block_ids: Iterable[int]
) -> tuple[int, ...]:
    placements = tuple(
        placement
        for block_id in block_ids
        if (placement := state.get(block_id)) is not None
    )
    has_tardiness = any(_tardiness(state, item) > 0 for item in placements)

    def score(item: Placement) -> tuple[float, int]:
        block = state.instance.blocks[item.block_id]
        primary = (
            _tardiness(state, item)
            if has_tardiness
            else float(item.exit - block.release_time - block.dwell)
        )
        return (-primary, item.block_id)

    return tuple(item.block_id for item in sorted(placements, key=score))


def _biased_without_replacement(
    ranked: Sequence[int], rng: Generator, count: int, *, rho: float
) -> tuple[int, ...]:
    remaining = list(ranked)
    if count > len(remaining):
        return ()
    selected: list[int] = []
    for _ in range(count):
        index = min(
            int(math.floor(len(remaining) * float(rng.random()) ** rho)),
            len(remaining) - 1,
        )
        selected.append(remaining.pop(index))
    return tuple(selected)


def _placements_conflict(
    state: SolutionState, left: Placement, right: Placement
) -> bool:
    if left.bay_id != right.bay_id:
        return False
    return not state.geom.union_disjoint(
        state.shape_info(left),
        state.shape_info(right),
        right.x - left.x,
        right.y - left.y,
    )


def _shaw_ranked(
    state: SolutionState, seed_id: int, block_ids: Iterable[int]
) -> tuple[int, ...]:
    seed = state.get(seed_id)
    if seed is None:
        return ()
    bay = state.instance.bays[seed.bay_id]
    diagonal = max(math.hypot(bay.width, bay.height), 1.0)
    horizon = _instance_horizon(state)
    seed_shape = state.shape_info(seed)
    seed_center = (
        seed.x + (seed_shape.aabb[0] + seed_shape.aabb[2]) / 2.0,
        seed.y + (seed_shape.aabb[1] + seed_shape.aabb[3]) / 2.0,
    )
    scored = []
    for block_id in block_ids:
        placement = state.get(block_id)
        if placement is None:
            continue
        shape = state.shape_info(placement)
        center = (
            placement.x + (shape.aabb[0] + shape.aabb[2]) / 2.0,
            placement.y + (shape.aabb[1] + shape.aabb[3]) / 2.0,
        )
        relatedness = (
            0.5 * math.dist(seed_center, center) / diagonal
            + 0.25 * abs(seed.entry - placement.entry) / horizon
            + 0.25 * abs(seed.exit - placement.exit) / horizon
            + (1.0 if seed.bay_id != placement.bay_id else 0.0)
        )
        scored.append((relatedness, block_id))
    return tuple(block_id for _score, block_id in sorted(scored))


def _weighted_choice(
    values: Sequence[int], weights: Sequence[float], rng: Generator
) -> int:
    total = sum(max(0.0, float(weight)) for weight in weights)
    if total <= 0:
        return values[int(rng.integers(0, len(values)))]
    threshold = float(rng.random()) * total
    cumulative = 0.0
    for value, weight in zip(values, weights, strict=True):
        cumulative += max(0.0, float(weight))
        if threshold < cumulative:
            return value
    return values[-1]


def _original_bay_options(
    state: SolutionState, original: Placement
) -> tuple[InsertionCandidate, ...]:
    orientations = list(
        state.instance.fitting_orientations(original.block_id, original.bay_id)
    )
    if original.orient_idx in orientations:
        orientations.remove(original.orient_idx)
        orientations.insert(0, original.orient_idx)
    options = []
    for orient_idx in orientations:
        candidate = first_fit(
            state,
            original.block_id,
            original.bay_id,
            orient_idx,
            time_cap=None,
            anchor_cap=ESCALATED_ANCHOR_CAP,
        )
        if candidate is not None:
            options.append(candidate)
    return tuple(options)


def _candidate_cost(candidate: InsertionCandidate) -> float:
    return candidate.weighted_tardiness + candidate.assignment_cost


def _install_candidate(
    transaction: MoveTransaction,
    candidate: InsertionCandidate,
    original: Placement,
) -> bool:
    if candidate.placement.bay_id != original.bay_id:
        raise AssertionError("S3 repair candidate changed assignment")
    if not validate_insertion(transaction.state, candidate.placement):
        return False
    if transaction.state.place(candidate.placement) is not None:
        raise AssertionError("repair replaced a block that was not removed")
    transaction.record_mutation(
        f"insert:{candidate.placement.block_id}"
    )
    return True


def _instance_horizon(state: SolutionState) -> float:
    return max(
        1.0,
        float(
            max(
                (
                    max(block.due_date, block.release_time) + block.dwell
                    for block in state.instance.blocks
                ),
                default=1,
            )
        ),
        float(max((item.exit for item in state.placements.values()), default=1)),
    )


D1 = D1RandomRemoval
D2 = D2WorstTardiness
D3 = D3CriticalChain
D4 = D4TimeWindow
D5 = D5ShawRelated
R1 = R1NoisyGreedy
R2 = R2Regret2
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
