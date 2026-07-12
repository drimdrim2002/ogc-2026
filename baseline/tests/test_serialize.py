"""Canonical non-interlock serialization contracts for S0-04."""

from __future__ import annotations

import unittest

from solver.serialize import serialize_non_interlock
from solver.state import Placement


class SerializeTests(unittest.TestCase):
    def test_canonical_order_and_exactly_one_operation_pair(self):
        placements = [
            Placement(2, bay_id=1, x=3, y=4, orient_idx=2, entry=2, exit=3),
            Placement(0, bay_id=0, x=0, y=1, orient_idx=0, entry=0, exit=2),
            Placement(1, bay_id=1, x=2, y=3, orient_idx=1, entry=2, exit=4),
        ]

        serialized = serialize_non_interlock(placements)

        self.assertEqual(["0", "2", "3", "4"], list(serialized["operations"]))
        self.assertEqual(
            [("ENTRY", 0)],
            [(op["type"], op["block_id"]) for op in serialized["operations"]["0"]],
        )
        self.assertEqual(
            [("EXIT", 0), ("ENTRY", 1), ("ENTRY", 2)],
            [(op["type"], op["block_id"]) for op in serialized["operations"]["2"]],
        )

        all_ops = [
            op
            for operations_at_time in serialized["operations"].values()
            for op in operations_at_time
        ]
        for block_id in range(3):
            self.assertEqual(
                1,
                sum(
                    op["type"] == "ENTRY" and op["block_id"] == block_id
                    for op in all_ops
                ),
            )
            self.assertEqual(
                1,
                sum(
                    op["type"] == "EXIT" and op["block_id"] == block_id
                    for op in all_ops
                ),
            )

        entry = serialized["operations"]["2"][1]
        self.assertEqual(
            {
                "type": "ENTRY",
                "block_id": 1,
                "bay_id": 1,
                "x": 2,
                "y": 3,
                "orient_idx": 1,
            },
            entry,
        )
        self.assertTrue(
            all(
                isinstance(value, int) and not isinstance(value, bool)
                for value in (entry["x"], entry["y"], entry["orient_idx"])
            )
        )


if __name__ == "__main__":
    unittest.main()
