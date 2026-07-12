from __future__ import annotations

import copy
import unittest

from solver.serialize import EmptyPrecedenceProvider, SerializationError, serialize
from solver.state import Placement, SolutionSnapshot

from tests.helpers import block, checker, instance, placement_ops, rectangle


class FakePrecedenceProvider:
    def __init__(self, edges=(), free_pairs=()):
        self._edges = tuple(edges)
        self._free_pairs = {frozenset(pair) for pair in free_pairs}

    def edges(self, exiting_ids, snapshot, bay_id):
        exiting = set(exiting_ids)
        return [edge for edge in self._edges if edge[0] in exiting and edge[1] in exiting]

    def entries_are_free(self, entering_ids, snapshot, bay_id):
        ids = tuple(entering_ids)
        return all(
            frozenset((ids[left], ids[right])) in self._free_pairs
            for left in range(len(ids))
            for right in range(left + 1, len(ids))
        )


class CheckerContractTests(unittest.TestCase):
    def test_half_open_handover(self):
        prob = instance([block(), block()])
        result = checker(prob, placement_ops([(0, 0, 0, 0, 0, 0, 2), (1, 0, 0, 0, 0, 2, 4)]))
        self.assertTrue(result["feasible"], result)
        self.assertEqual(5, result["stage"])

    def test_boundary_contact_has_zero_collision_area(self):
        prob = instance([block(), block()])
        result = checker(prob, placement_ops([(0, 0, 0, 0, 0, 0, 2), (1, 0, 0, 2, 0, 0, 2)]))
        self.assertTrue(result["feasible"], result)
        self.assertEqual(5, result["stage"])

    def test_simultaneous_entries_free_pass_obstructed_fail(self):
        prob = instance([block(), block()])
        free = checker(prob, placement_ops([(0, 0, 0, 0, 0, 0, 2), (1, 0, 0, 3, 0, 0, 2)]))
        obstructed = checker(prob, placement_ops([(0, 0, 0, 0, 0, 0, 2), (1, 0, 0, 0, 0, 0, 2)]))
        self.assertTrue(free["feasible"], free)
        self.assertFalse(obstructed["feasible"])
        self.assertEqual(2, obstructed["stage"])

    def test_serializer_requires_free_approval_for_same_bay_entries(self):
        snapshot = SolutionSnapshot(
            (
                Placement(0, 0, 0, 0, 0, 0, 2),
                Placement(1, 0, 0, 3, 0, 0, 2),
            )
        )
        with self.assertRaises(SerializationError):
            serialize(snapshot, EmptyPrecedenceProvider())
        output = serialize(snapshot, FakePrecedenceProvider(free_pairs=((0, 1),)))
        self.assertEqual(["ENTRY", "ENTRY"], [op["type"] for op in output["operations"]["0"]])

    def test_simultaneous_exit_topology(self):
        mover = block(orientations=[[[[0, 0], [2, 0], [2, 2], [0, 2]]]])
        blocker = block(
            release=1,
            processing=2,
            orientations=[[
                rectangle(4, 0, 5, 1),
                rectangle(0, 0, 2, 2),
            ]],
        )
        prob = instance([mover, blocker], bays=((8, 4),))
        snapshot = SolutionSnapshot(
            (
                Placement(0, 0, 0, 0, 0, 0, 3),
                Placement(1, 0, 0, 4, 0, 1, 3),
            )
        )
        ordered = serialize(snapshot, FakePrecedenceProvider(edges=((1, 0),)))
        self.assertEqual([1, 0], [op["block_id"] for op in ordered["operations"]["3"]])
        self.assertTrue(checker(prob, ordered)["feasible"], checker(prob, ordered))

        reversed_solution = copy.deepcopy(ordered)
        reversed_solution["operations"]["3"].reverse()
        result = checker(prob, reversed_solution)
        self.assertFalse(result["feasible"])
        self.assertEqual(5, result["stage"])

    def test_exit_topology_cycle_is_rejected(self):
        snapshot = SolutionSnapshot(
            (Placement(0, 0, 0, 0, 0, 0, 2), Placement(1, 0, 0, 3, 0, 0, 2))
        )
        with self.assertRaises(SerializationError):
            serialize(snapshot, FakePrecedenceProvider(edges=((0, 1), (1, 0))))

    def test_chronological_dictionary_reconstruction(self):
        prob = instance([block()])
        manual = {
            "operations": {
                "2": [{"type": "EXIT", "block_id": 0, "bay_id": 0}],
                "0": [{"type": "ENTRY", "block_id": 0, "bay_id": 0, "x": 0, "y": 0, "orient_idx": 0}],
            }
        }
        result = checker(prob, manual)
        self.assertFalse(result["feasible"])
        self.assertEqual(1, result["stage"])
        canonical = serialize(SolutionSnapshot((Placement(0, 0, 0, 0, 0, 0, 2),)))
        self.assertEqual(["0", "2"], list(canonical["operations"]))
        self.assertTrue(checker(prob, canonical)["feasible"])

    def test_serializer_emits_integer_fields_and_dates(self):
        output = serialize(SolutionSnapshot((Placement(0, 0, 0, 4, 5, 6, 8),)))
        self.assertEqual(["6", "8"], list(output["operations"]))
        for date, operations in output["operations"].items():
            self.assertIsInstance(int(date), int)
            for operation in operations:
                for key in ("block_id", "bay_id"):
                    self.assertIsInstance(operation[key], int)
                if operation["type"] == "ENTRY":
                    for key in ("orient_idx", "x", "y"):
                        self.assertIsInstance(operation[key], int)


if __name__ == "__main__":
    unittest.main()
