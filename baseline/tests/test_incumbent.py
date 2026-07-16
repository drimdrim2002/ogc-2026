"""Immutable verified-checkpoint contracts for AF-01."""

from __future__ import annotations

from dataclasses import FrozenInstanceError, replace
import hashlib
import json
import unittest

from solver.checker_adapter import official_check
import solver.incumbent as incumbent_module
from solver.instance import ProblemInstance
from solver.serialize import serialize_non_interlock
from solver.state import Placement, SolutionState
from tests.fixtures import block, instance


class VerifiedCheckpointTests(unittest.TestCase):
    def setUp(self) -> None:
        self.prob_info = instance(
            [block(preferences=(10, 0))],
            bays=((3, 3), (3, 3)),
            weights={"w1": 1.0, "w2": 0.0, "w3": 1.0},
        )
        self.parsed = ProblemInstance.parse(self.prob_info)
        self.state = SolutionState(self.parsed)
        self.state.place(
            Placement(0, bay_id=0, x=0, y=0, orient_idx=0, entry=0, exit=1)
        )
        self.incumbent = incumbent_module.VerifiedIncumbent(self.parsed)
        checked = self.incumbent.register_initial(self.state)
        self.assertTrue(checked.feasible, checked.violations)
        self.assertEqual(5, checked.stage)

    def _export(self, contract: str):
        export = getattr(self.incumbent, "export_checkpoint", None)
        self.assertIsNotNone(export, f"AF-01 missing checkpoint API for {contract}")
        assert export is not None
        return export()

    def test_checkpoint_is_frozen_and_instance_bound(self) -> None:
        checkpoint = self._export("immutable instance binding")
        checkpoint_type = getattr(incumbent_module, "VerifiedCheckpoint", None)
        self.assertIsNotNone(checkpoint_type)
        self.assertIsInstance(checkpoint, checkpoint_type)
        with self.assertRaises(FrozenInstanceError):
            checkpoint.solution_sha256 = "0" * 64

        other_prob_info = json.loads(json.dumps(self.prob_info))
        other_prob_info["name"] = "different-instance"
        with self.assertRaisesRegex(ValueError, "different instance"):
            incumbent_module.VerifiedIncumbent.from_checkpoint(
                other_prob_info, checkpoint
            )

    def test_rejects_solution_json_and_placement_tampering(self) -> None:
        checkpoint = self._export("solution and placement tamper rejection")
        forged_solution = {"operations": {}}
        forged_json = json.dumps(
            forged_solution, sort_keys=True, separators=(",", ":")
        ).encode()
        forged_solution_checkpoint = replace(
            checkpoint,
            solution_json=forged_json,
            solution_sha256=hashlib.sha256(forged_json).hexdigest(),
        )
        with self.assertRaisesRegex(ValueError, "placement snapshot mismatch"):
            incumbent_module.VerifiedIncumbent.from_checkpoint(
                self.parsed, forged_solution_checkpoint
            )

        forged_placement = replace(checkpoint.placements[0], x=1)
        forged_placement_checkpoint = replace(
            checkpoint, placements=(forged_placement,)
        )
        with self.assertRaisesRegex(ValueError, "placement snapshot mismatch"):
            incumbent_module.VerifiedIncumbent.from_checkpoint(
                self.parsed, forged_placement_checkpoint
            )

    def test_round_trip_preserves_sha_checker_result_and_objective(self) -> None:
        checkpoint = self._export("round-trip identity")
        restored = incumbent_module.VerifiedIncumbent.from_checkpoint(
            self.parsed, checkpoint
        )
        round_trip = restored.export_checkpoint()

        self.assertEqual(checkpoint.solution_sha256, round_trip.solution_sha256)
        self.assertEqual(checkpoint.checker_result, round_trip.checker_result)
        self.assertEqual(
            checkpoint.checker_result.objective,
            restored.checker_result.objective,
        )

    def test_snapshot_state_satisfies_invariants_and_official_stage_5(self) -> None:
        checkpoint = self._export("SolutionState reconstruction")
        restored = incumbent_module.VerifiedIncumbent.from_checkpoint(
            self.parsed, checkpoint
        )

        snapshot = restored.snapshot_state()
        snapshot.assert_invariants()
        checked = official_check(
            self.prob_info,
            serialize_non_interlock(snapshot.placements.values()),
        )
        self.assertTrue(checked.feasible, checked.violations)
        self.assertEqual(5, checked.stage)
        self.assertEqual(checkpoint.checker_result.objective, snapshot.objective)

    def test_caller_mutation_cannot_change_stored_checkpoint(self) -> None:
        checkpoint = self._export("caller-owned copies")
        original_json = checkpoint.solution_json
        original_placements = checkpoint.placements

        returned_solution = checkpoint.solution_copy()
        returned_solution["operations"].clear()
        restored = incumbent_module.VerifiedIncumbent.from_checkpoint(
            self.parsed, checkpoint
        )
        restored.solution["operations"].clear()
        snapshot = restored.snapshot_state()
        snapshot.place(replace(snapshot.placements[0], x=1))

        self.assertEqual(original_json, checkpoint.solution_json)
        self.assertEqual(original_placements, checkpoint.placements)
        self.assertNotEqual(returned_solution, checkpoint.solution_copy())

    def test_verification_count_increments_only_on_official_checks(self) -> None:
        self.assertEqual(1, self.incumbent.verification_count)
        checkpoint = self._export("verification-count policy")
        self.assertEqual(1, checkpoint.verification_count)
        self.incumbent.snapshot_state()
        self.assertEqual(1, self.incumbent.verification_count)

        restored = incumbent_module.VerifiedIncumbent.from_checkpoint(
            self.parsed, checkpoint
        )
        self.assertEqual(2, restored.verification_count)
        restored.export_checkpoint()
        restored.snapshot_state()
        self.assertEqual(2, restored.verification_count)
        self.assertFalse(restored.try_update(self.state))
        self.assertEqual(3, restored.verification_count)


if __name__ == "__main__":
    unittest.main()
