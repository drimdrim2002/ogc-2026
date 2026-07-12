"""Canonical and sole serializer for solver snapshots."""

from __future__ import annotations

from collections import defaultdict
from typing import Iterable, Protocol

from .state import Placement, SolutionSnapshot


class SerializationError(ValueError):
    pass


class ExitPrecedenceProvider(Protocol):
    def edges(
        self,
        exiting_ids: Iterable[int],
        snapshot: SolutionSnapshot,
        bay_id: int,
    ) -> Iterable[tuple[int, int]]: ...

    def entries_are_free(
        self,
        entering_ids: Iterable[int],
        snapshot: SolutionSnapshot,
        bay_id: int,
    ) -> bool: ...


class EmptyPrecedenceProvider:
    def edges(self, exiting_ids, snapshot, bay_id):
        return ()

    def entries_are_free(self, entering_ids, snapshot, bay_id):
        return len(tuple(entering_ids)) <= 1


def _topological_order(
    ids: list[int],
    edges: Iterable[tuple[int, int]],
) -> list[int]:
    node_set = set(ids)
    successors = {node: set() for node in ids}
    indegree = {node: 0 for node in ids}
    for before, after in edges:
        if before not in node_set or after not in node_set:
            raise SerializationError("precedence edge references a non-exiting block")
        if before == after:
            raise SerializationError("self precedence is invalid")
        if after not in successors[before]:
            successors[before].add(after)
            indegree[after] += 1
    ready = sorted(node for node in ids if indegree[node] == 0)
    ordered: list[int] = []
    while ready:
        node = ready.pop(0)
        ordered.append(node)
        for successor in sorted(successors[node]):
            indegree[successor] -= 1
            if indegree[successor] == 0:
                ready.append(successor)
                ready.sort()
    if len(ordered) != len(ids):
        raise SerializationError("same-date exit precedence contains a cycle")
    return ordered


def serialize(
    snapshot: SolutionSnapshot,
    precedence_provider: ExitPrecedenceProvider | None = None,
) -> dict:
    provider = precedence_provider or EmptyPrecedenceProvider()
    by_id = {placement.block_id: placement for placement in snapshot.placements}
    events: dict[int, dict[str, list[Placement]]] = defaultdict(lambda: {"EXIT": [], "ENTRY": []})
    for placement in snapshot.placements:
        events[placement.exit]["EXIT"].append(placement)
        events[placement.entry]["ENTRY"].append(placement)

    operations: dict[str, list[dict]] = {}
    for date in sorted(events):
        output_at_date: list[dict] = []
        exits_by_bay: dict[int, list[Placement]] = defaultdict(list)
        for placement in events[date]["EXIT"]:
            exits_by_bay[placement.bay_id].append(placement)
        for bay_id in sorted(exits_by_bay):
            ids = sorted(placement.block_id for placement in exits_by_bay[bay_id])
            ordered_ids = _topological_order(ids, provider.edges(ids, snapshot, bay_id))
            for block_id in ordered_ids:
                placement = by_id[block_id]
                output_at_date.append(
                    {"type": "EXIT", "block_id": int(block_id), "bay_id": int(placement.bay_id)}
                )

        entries_by_bay: dict[int, list[Placement]] = defaultdict(list)
        for placement in events[date]["ENTRY"]:
            entries_by_bay[placement.bay_id].append(placement)
        for bay_id in sorted(entries_by_bay):
            placements = sorted(entries_by_bay[bay_id], key=lambda item: item.block_id)
            ids = [placement.block_id for placement in placements]
            if len(ids) > 1 and not provider.entries_are_free(ids, snapshot, bay_id):
                raise SerializationError("simultaneous same-bay entries are not all FREE")
            for placement in placements:
                output_at_date.append(
                    {
                        "type": "ENTRY",
                        "block_id": int(placement.block_id),
                        "bay_id": int(placement.bay_id),
                        "x": int(placement.x),
                        "y": int(placement.y),
                        "orient_idx": int(placement.orient_idx),
                    }
                )
        operations[str(int(date))] = output_at_date
    return {"operations": operations}
