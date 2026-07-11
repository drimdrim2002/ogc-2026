# S1 Execution Plan: Constructor

Parent: [`../fable-native-implementation-progress.md`](../fable-native-implementation-progress.md)

Status: `NOT_STARTED`; gate: `NOT_RUN`; planned slices: 5

## Goal, scope, and evidence

S1 turns the verified T0 foundation into a deterministic non-interlock assignment-v1 and insertion constructor that places every block, improves T0 materially, and constructs a 300-block case in at most five seconds. It consumes S0 fit/geometry/state/validation/serialization/incumbent/budget/harness APIs.

Included: greedy regret assignment v1, event-boundary time candidates, integer contact anchors, bounded exact validation, escalation/empty-bay fallback, profiles, multi-start, instrumentation, and S1 gate calibration. Excluded: exact retiming, ALNS, cross-bay search after construction, assignment-v2, portfolio, and interlock. A constructor-local bay bump may compare all fitting bays for the block during initial construction; iterative cross-bay moves/swaps remain S4.

Design anchors: `solver-design-en.md` §2.2-2.6, §4.3, §4.6, §5 S1, §9.1-9.2. Checker anchors are S0’s plus float assignment objective at `1398-1421`. `slack=D-R-dwell`, not `D-R-P`; capped candidates are heuristic and the empty-bay fallback guarantees placement after fit preflight.

Prerequisite: S0 `COMPLETE` and clean, with selected cache cap and complete gate evidence. Produces `assign_v1`, shared `insert_block`, candidate/escalation interfaces, profile runner, and constructor metrics used by S2 repair warm starts and S3 repair. Explicit non-dependencies: no S2 backend or retime, no S3 acceptance/destroy, no S4 assignment-v2/cross-bay neighborhood, no S6 OBS behavior. S2 entry requires the S1 gate exit 0 and a checker-verified constructor incumbent.

## Files and responsibilities

- Create `baseline/solver/assign.py`: `AssignmentV1`, `assignment_cost`, regret ordering, exact float diagnostic delta for Z2/Z3, fit hard constraints.
- Create `baseline/solver/construct.py`: `time_candidates`, `anchor_candidates`, `first_fit`, `insert_block`, `escalate_insert`, `construct_profile`, `construct_multistart`, `ConstructionMetrics`.
- Change `config.py` for S1 flags/matrices; `entry.py` to call constructor only behind `constructor`; `state.py` for constructor indexes; `incumbent.py` unchanged except telemetry; harness benchmark/report for construction fields.
- Create `tests/test_assign_v1.py`, `tests/test_construct.py`; extend parity and entry tests only where interfaces are consumed.

## Slice dependency contracts

| Slice | Prerequisite / consumes | Produces | Explicit non-dependency | Next entry condition |
|---|---|---|---|---|
| S1-01 | S0 fit/state/objective | Deterministic assignment-v1 | S2 exact/S4 assignment-v2 | Fit/parity/checker proof GREEN |
| S1-02 | S1-01 assignment, S0 validation | Event-time insertion API | Anchors from S1-03, retime | Timing parity zero mismatch |
| S1-03 | S1-02 insertion, S0 geometry | Anchors/escalation/solo fallback | Interlock | Forced escalation checker-feasible |
| S1-04 | S1-01…03 constructor path | Profile/multi-start driver | Exact/LNS | Replay deterministic and feasible |
| S1-05 | All S1 slices and S0 harness | Integrated calibrated constructor/gate | S2 implementation | Full S1 gate exits 0 |

## Atomic slices

Use `PY=/opt/homebrew/Caskroom/miniforge/base/envs/ogc-2026/bin/python` from repository root.

### S1-01 — Assignment v1

Observable: every block receives a fitting bay deterministically; regret ordering and cost use checker-float assignment terms plus a congestion diagnostic.

- RED: `cd baseline && $PY -m unittest tests.test_assign_v1.AssignmentV1Tests.test_all_blocks_fit_and_replay -v`; exit 1 because `AssignmentV1` is missing.
- Minimum: compute per-bay workload, exact prospective Z2 range, preference loss, and instance-derived congestion ratio `sum(area*dwell)/(bay_area*horizon)`; assign highest best-vs-second-best regret first. If only one bay fits, regret is infinity. No exact backend.
- GREEN: targeted test 0; regress all S0 plus `test_assign_v1`.
- Checker: construct a sequential empty-window solution using the assignment and S0 T0 placement, serialize/full-check the tracked example and synthetic one-/multi-bay fixtures; all Stage 5 feasible.
- Evidence: `benchmark --stage s1 --component assign_v1 --instances smoke-3 --timelimits 5 --seeds 20260710`; required counters assigned/fallback/fit failures and float Z2/Z3 parity.
- Commit: `feat(s1): add deterministic assignment v1`.

### S1-02 — Shared event-time insertion

Observable: `insert_block` selects the earliest feasible non-interlock event time for a fixed bay/orientation and uses S0 `validate_insertion`.

- RED: `cd baseline && $PY -m unittest tests.test_construct.ConstructorTests.test_same_day_handoff_candidate -v`; failure is missing `insert_block`, not a semantic fixture error.
- Minimum: candidates `{R_i} ∪ exits ∪ {a_j-dwell_i}`, integer/filter/sort/deduplicate; working T cap 8 then 16; overlap uses half-open equality; score weighted tardiness then assignment cost then stable IDs. Never claim capped completeness.
- GREEN targeted; regress S0, assignment, construct tests.
- Checker: two-block same-day handoff output is Stage 5 feasible; a union-overlap simultaneous-entry candidate is rejected and the accepted alternative full-checks.
- Evidence: `parity --kind targeted --cases 1000 --instances synthetic --seed 20260710 --feature caller=construct` exits 0.
- Commit: `feat(s1): add checker-parity insertion timing`.

### S1-03 — Anchors and escalation

Observable: integer wall/right/top anchors find contact placements, negative local bounds are respected, and every fit-qualified block ultimately uses a valid empty-bay solo window if bounded packing fails.

- RED: `cd baseline && $PY -m unittest tests.test_construct.ConstructorTests.test_anchor_escalation_and_solo_fallback -v`; exit 1 for missing anchor/escalation.
- Minimum: y-major wall plus right/top AABB contact anchors, valid-range clipping, S0 AABB/cache/validation path; working K 32 then 64; escalation T/K, constructor-local all-fitting-bay retry, tardy expansion, then solo window. Interlock step is skipped because flag/API does not exist.
- GREEN targeted; regression all current tests.
- Checker: forced escalation synthetic fixture and tracked example full-check; every block placed exactly once.
- Stress: `stress --stage s1 --instances stress --timelimits 5 --seeds 20260710 --feature scenario=negative_origin,contact,no_preferred_fit,bounded_failure`; zero checker failures, fallback reason present.
- Commit: `feat(s1): add anchor search and guaranteed escalation`.

### S1-04 — Profiles and deterministic multi-start

Observable: PF1 `(slack,D,-area*dwell)`, PF2 `(D,-dwell)`, PF3 `(-area*dwell,slack)`, and PF4 `(R,D)` plus recorded biased variants share the insertion path, respect budget, and replay identically for the same seed.

- RED: `cd baseline && $PY -m unittest tests.test_construct.ConstructorTests.test_multistart_seed_replay -v`; exit 1 because `construct_multistart` is absent.
- Minimum: deterministic profiles first, one PCG64 seed stream, full state per start, internal comparison, then full-check only the best before incumbent update. Store profile/order/metrics. Random variants run only after four deterministic profiles and before budget checkpoint.
- GREEN targeted; full current regression.
- Checker: example and smoke-3 (when data present) best state is serialized and feasible; injected profile exception preserves prior verified incumbent.
- Benchmark: `benchmark --stage s1 --instances dev-10 --timelimits 5 --seeds 20260710 --feature constructor=true --feature profiles=PF1,PF2,PF3,PF4`; profile metrics and deterministic output SHA required.
- Commit: `feat(s1): add deterministic multi-profile construction`.

### S1-05 — Cap calibration, entry integration, and gate

Observable: entry enables the best verified constructor result within budget; candidate caps are selected from the preregistered matrix and all S1 gate evidence is complete.

- RED: `cd baseline && $PY -m unittest tests.test_budget_entry.EntryArmorTests.test_constructor_failure_keeps_t0 -v`; exit 1 because entry has no constructor branch.
- Minimum: integrate feature flag; calibrate `(T,K)` over `(8,32),(8,48),(16,48),(16,64)` against uncapped synthetic reference. Choose the cheapest pair with zero checker/parity loss, ≥99% reference insertion success, and training gate; if none meets five seconds, retain safest pair, mark S1 `BLOCKED`, profile exact predicates/anchor counts, and do not weaken the target.
- GREEN targeted plus `cd baseline && $PY -m unittest discover -s tests -p 'test_*.py' -v`.
- Checker/benchmark/gate: exact sequence below.
- Commit: `feat(s1): integrate calibrated constructor`.

For every slice, update progress before start, after intended RED, after GREEN, after checker, after benchmark/stress, and after gate. Evidence paths are `benchmarks/evidence/s1/<slice>/<run_id>/`. Rollback means disable `constructor`, return verified T0, and revert only the active slice in a new corrective commit; do not alter earlier tests. Clean process groups, incomplete run markers, temporary generated fixtures, and solver objects.

## Full S1 gate

```bash
PY=/opt/homebrew/Caskroom/miniforge/base/envs/ogc-2026/bin/python
cd baseline && $PY -m unittest discover -s tests -p 'test_*.py' -v
cd ..
$PY -m baseline.harness.cli parity --kind targeted --cases 1000 --instances synthetic --seed 20260710 --feature caller=construct
$PY -m baseline.harness.cli benchmark --stage s1 --instances training --timelimits 5 --seeds 20260710 --feature constructor=true
$PY -m baseline.harness.cli ab --stage s1 --instances dev-10 --timelimits 5 --seed 20260710 --feature constructor --a false --b true
$PY -m baseline.harness.cli stress --stage s1 --instances stress --timelimits 0.5,2,5,12 --seeds 20260710 --feature constructor=true
$PY -m baseline.harness.cli gate --stage s1 --latest-complete --commit HEAD
$PY -m baseline.harness.cli report --stage s1 --latest-complete
```

PASS: every training block placed; every returned solution checker-feasible Stage 5; construction-stage wall time (excluding harness startup/checker) ≤5.000s for every 300-block training case and overall solver respects its five-second limit; same seed yields byte-identical operations and metrics excluding timestamps; dev-10 improves T0 on at least 8/10 and has median relative checker-objective improvement ≥10%; no training instance worsens because entry retains the better checker-verified T0/constructor incumbent; parity mismatch zero; stress safe. Exit codes all 0.

FAIL: any fit-qualified unplaced block, infeasibility, nondeterminism, missing metrics, >5-second construction, weaker median/coverage gain, or incumbent loss. If the 5-second hypothesis fails, run `report --stage s1 --latest-complete`, rank `predicate_s`, `anchor_attempts`, `candidate_times`, and `full_check_s`, repeat only the predefined cap matrix, and remain `BLOCKED`; changing the target requires a later design-plan revision, not an ad hoc gate waiver.

Feature/default after PASS: `constructor=true`; selected T/K/profile budget recorded in config and gate evidence. S2 consumes only the checker-verified constructor state and fixed layout APIs. Known risks: training set missing locally, geometric candidate explosion, and apparent improvement dominated by T0 weakness; the explicit cap and paired gate address these.

Next-stage entry: S2 may start only with S1 status `COMPLETE`, gate exit 0, a clean worktree, and the selected cap/profile evidence path recorded in the master progress table.
