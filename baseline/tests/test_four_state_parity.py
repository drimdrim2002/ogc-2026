from __future__ import annotations

import random
import unittest

from solver.geometry import GeometryKernel, PairRelation, PairState
from solver.instance import parse_instance
from solver.serialize import SerializationError, serialize
from solver.state import Placement, SolutionSnapshot
from tests.helpers import block, checker, instance, load_example, rectangle
from utils import Bay, Block, check_entry, check_exit


SEED = 20260710
SAMPLES = 2_000


class _RelationProvider:
    def __init__(self, relation: PairRelation) -> None:
        self.relation = relation

    def edges(self, exiting_ids, snapshot, bay_id):
        ids = set(exiting_ids)
        if ids != {0, 1}:
            return ()
        edges = []
        if self.relation.g_i_k:
            edges.append((1, 0))
        if self.relation.g_k_i:
            edges.append((0, 1))
        return tuple(edges)

    def entries_are_free(self, entering_ids, snapshot, bay_id):
        return len(tuple(entering_ids)) <= 1 or self.relation.state is PairState.FREE


def _state_instance(state: PairState) -> tuple[dict, Placement, Placement]:
    low = [rectangle(0, 0, 2, 2)]
    capped = [rectangle(0, 0, 2, 2), rectangle(4, 0, 6, 2)]
    if state is PairState.FREE:
        raw = instance([block(orientations=[low]), block(orientations=[low])], bays=((12, 12),))
        return raw, Placement(0, 0, 0, 0, 0, 0, 4), Placement(1, 0, 0, 3, 0, 0, 4)
    if state is PairState.I_OUTER:
        raw = instance([block(orientations=[low]), block(orientations=[capped])], bays=((12, 12),))
        return raw, Placement(0, 0, 0, 4, 0, 0, 5), Placement(1, 0, 0, 0, 0, 1, 4)
    if state is PairState.K_OUTER:
        raw = instance([block(orientations=[capped]), block(orientations=[low])], bays=((12, 12),))
        return raw, Placement(0, 0, 0, 0, 0, 1, 4), Placement(1, 0, 0, 4, 0, 0, 5)
    raw = instance([block(orientations=[low]), block(orientations=[low])], bays=((12, 12),))
    return raw, Placement(0, 0, 0, 0, 0, 0, 2), Placement(1, 0, 0, 0, 0, 2, 4)


class FourStateParityTests(unittest.TestCase):
    def test_four_state_schedule_checker_parity(self):
        for expected_state in PairState:
            raw, first, second = _state_instance(expected_state)
            kernel = GeometryKernel.from_instance(parse_instance(raw))
            relation = kernel.relation(first, second)
            with self.subTest(state=expected_state, validity="valid"):
                self.assertEqual(relation.state, expected_state)
                self.assertTrue(relation.allows((first.entry, first.exit), (second.entry, second.exit)))
                result = checker(raw, serialize(SolutionSnapshot((first, second)), kernel))
                self.assertTrue(result["feasible"], result)
                self.assertEqual(result["stage"], 5)

            if expected_state is PairState.FREE:
                continue
            if expected_state is PairState.I_OUTER:
                invalid = (Placement(0, 0, 0, 4, 0, 1, 4), Placement(1, 0, 0, 0, 0, 0, 5))
            elif expected_state is PairState.K_OUTER:
                invalid = (Placement(0, 0, 0, 0, 0, 0, 5), Placement(1, 0, 0, 4, 0, 1, 4))
            else:
                invalid = (Placement(0, 0, 0, 0, 0, 0, 3), Placement(1, 0, 0, 0, 0, 1, 4))
            with self.subTest(state=expected_state, validity="invalid"):
                invalid_relation = kernel.relation(*invalid)
                self.assertFalse(invalid_relation.allows((invalid[0].entry, invalid[0].exit), (invalid[1].entry, invalid[1].exit)))
                if invalid[0].entry == invalid[1].entry:
                    with self.assertRaises(SerializationError):
                        serialize(SolutionSnapshot(invalid), kernel)
                else:
                    result = checker(raw, serialize(SolutionSnapshot(invalid), kernel))
                    self.assertFalse(result["feasible"], result)
                    self.assertIn(result["stage"], (2, 3, 4, 5))

    def test_real_serializer_exit_topology(self):
        raw, outer, inner = _state_instance(PairState.I_OUTER)
        outer = Placement(outer.block_id, outer.bay_id, outer.orient_idx, outer.x, outer.y, 0, 5)
        inner = Placement(inner.block_id, inner.bay_id, inner.orient_idx, inner.x, inner.y, 1, 5)
        kernel = GeometryKernel.from_instance(parse_instance(raw))
        solution = serialize(SolutionSnapshot((outer, inner)), kernel)

        exits = [operation["block_id"] for operation in solution["operations"]["5"] if operation["type"] == "EXIT"]
        self.assertEqual(exits, [1, 0])
        result = checker(raw, solution)
        self.assertTrue(result["feasible"], result)
        self.assertEqual(result["stage"], 5)

    def test_same_bay_simultaneous_entries_require_free_relation(self):
        free_raw, free_i, free_k = _state_instance(PairState.FREE)
        free_kernel = GeometryKernel.from_instance(parse_instance(free_raw))
        free_solution = serialize(SolutionSnapshot((free_i, free_k)), free_kernel)
        self.assertEqual(checker(free_raw, free_solution)["stage"], 5)

        outer_raw, outer, inner = _state_instance(PairState.I_OUTER)
        inner = Placement(inner.block_id, inner.bay_id, inner.orient_idx, inner.x, inner.y, 0, 4)
        outer_kernel = GeometryKernel.from_instance(parse_instance(outer_raw))
        with self.assertRaises(SerializationError):
            serialize(SolutionSnapshot((outer, inner)), outer_kernel)

    def test_relation_matches_checker_on_2000_deterministic_pair_time_cases(self):
        raw = load_example()
        parsed = parse_instance(raw)
        kernel = GeometryKernel.from_instance(parsed)
        rng = random.Random(SEED)
        fitting_by_bay: dict[int, list[tuple[int, int, object]]] = {}
        for parsed_block in parsed.blocks:
            for bay_id, orient_idx, reference_range in parsed_block.fitting_options:
                fitting_by_bay.setdefault(bay_id, []).append((parsed_block.index, orient_idx, reference_range))
        eligible_bays = [bay_id for bay_id, options in fitting_by_bay.items() if len({item[0] for item in options}) >= 2]
        self.assertTrue(eligible_bays)

        checked = 0
        discarded = 0
        interval_modes = (
            ((0, 2), (2, 4)),
            ((2, 4), (0, 2)),
            ((0, 5), (1, 4)),
            ((1, 4), (0, 5)),
            ((0, 4), (0, 3)),
            ((0, 3), (2, 5)),
            ((0, 4), (1, 4)),
        )
        while checked < SAMPLES:
            bay_id = rng.choice(eligible_bays)
            options = fitting_by_bay[bay_id]
            left = rng.choice(options)
            right = rng.choice(options)
            if left[0] == right[0]:
                discarded += 1
                continue
            left_x = rng.randint(left[2].x.lower, left[2].x.upper)
            left_y = rng.randint(left[2].y.lower, left[2].y.upper)
            right_x = rng.randint(right[2].x.lower, right[2].x.upper)
            right_y = rng.randint(right[2].y.lower, right[2].y.upper)
            interval_i, interval_k = rng.choice(interval_modes)
            mover = Placement(left[0], bay_id, left[1], left_x, left_y, *interval_i)
            stationary = Placement(right[0], bay_id, right[1], right_x, right_y, *interval_k)
            checker_bay = Bay.from_dict(raw["bays"][bay_id], bay_id)
            checker_mover = Block.from_instance(mover.block_id, raw, mover.x, mover.y, mover.orient_idx)
            checker_stationary = Block.from_instance(stationary.block_id, raw, stationary.x, stationary.y, stationary.orient_idx)
            expected_entry = bool(check_entry(checker_bay, [checker_stationary], checker_mover, fast=True))
            expected_exit = bool(check_exit(checker_bay, [checker_stationary], checker_mover, fast=True))
            actual = kernel.obstructs(mover, stationary)
            relation = kernel.relation(mover, stationary)
            expected_schedule = relation.allows(interval_i, interval_k)
            pair_raw = instance(
                [
                    block(
                        processing=1,
                        preferences=(0.0,),
                        orientations=[raw["blocks"][mover.block_id]["shape"][mover.orient_idx]["layers"]],
                    ),
                    block(
                        processing=1,
                        preferences=(0.0,),
                        orientations=[raw["blocks"][stationary.block_id]["shape"][stationary.orient_idx]["layers"]],
                    ),
                ],
                bays=((raw["bays"][bay_id]["width"], raw["bays"][bay_id]["height"]),),
            )
            mapped = SolutionSnapshot(
                (
                    Placement(0, 0, 0, mover.x, mover.y, mover.entry, mover.exit),
                    Placement(1, 0, 0, stationary.x, stationary.y, stationary.entry, stationary.exit),
                )
            )
            try:
                pair_solution = serialize(mapped, _RelationProvider(relation))
            except SerializationError:
                actual_schedule = False
                schedule_stage = "serializer-reject"
            else:
                pair_result = checker(pair_raw, pair_solution)
                actual_schedule = pair_result["feasible"] is True
                schedule_stage = pair_result["stage"]
            fixture = {
                "seed": SEED,
                "sample": checked,
                "discarded": discarded,
                "bay": bay_id,
                "mover": (mover.block_id, mover.orient_idx, mover.x, mover.y),
                "stationary": (stationary.block_id, stationary.orient_idx, stationary.x, stationary.y),
                "relative": (stationary.x - mover.x, stationary.y - mover.y),
                "actual": actual,
                "entry": expected_entry,
                "exit": expected_exit,
                "bits": (relation.g_i_k, relation.g_k_i),
                "state": relation.state.value,
                "intervals": (interval_i, interval_k),
                "relation_allows": expected_schedule,
                "checker_allows": actual_schedule,
                "checker_stage": schedule_stage,
            }
            self.assertEqual(expected_entry, expected_exit, fixture)
            self.assertEqual(actual, expected_entry, fixture)
            self.assertEqual(actual_schedule, expected_schedule, fixture)
            checked += 1

        self.assertEqual(checked, SAMPLES)


if __name__ == "__main__":
    unittest.main()
