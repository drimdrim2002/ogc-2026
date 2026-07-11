# S0 Execution Plan: Foundation

Authoritative parent: [`../fable-native-implementation-progress.md`](../fable-native-implementation-progress.md)

Status: `NOT_STARTED`; gate: `NOT_RUN`; planning complete, implementation absent

Planned slices: 6

## Goal and boundaries

S0 creates the semantic/safety foundation, exact geometry oracle cache, state and validation parity, canonical serialization, fit-qualified T0, verified incumbent, deadline/exception armor, and the minimum shared harness. Its observable outcome is a checker-feasible verified T0 on all 40 training instances within a five-second solver timelimit plus measured predicate evidence.

Included: `baseline/solver/{config,instance,geometry,state,checker_adapter,validate,serialize,trivial,budget,incumbent,entry}.py`, the thin entry change in `baseline/myalgorithm.py`, S0 tests/fixtures, the harness core, manifests, and evidence conventions. Excluded: assignment v1, insertion policies/candidate search, multi-start, exact solvers, LNS, cross-bay refinement, portfolio, and interlock. `baseline/utils.py` and `baseline/baseline_greedy.py` are read-only.

Relevant design: `solver-design-en.md` §1, §2, §4.1-4.2, §4.8, §4.10, §5 S0, §6, §8. Primary checker evidence: timing/assignment `1028-1136`, rounding/overlap `1144-1158`, entry `1160-1198`, exit `1200-1232`, collision `1234-1277`, replay `1279-1386`, objective `1388-1421`, plus geometry primitives `252-266`, `461-543`, `603-819`.

## Dependency contract

Prerequisites: planning commit; explicit interpreter; official training data resolvable as `prob_1`…`prob_40` for the full gate. Consumes only the frozen checker, problem analysis, tracked example, and manifests. Produces the APIs and evidence schema consumed by every later stage. Explicit non-dependencies: no constructor-generated candidate, S1 `assign.py`/`construct.py`, exact backend, ALNS, cross-bay move, portfolio, or interlock is used in an S0 test/gate.

The dependency inversion is resolved here: `validate.py` owns minimal `validate_unary`, `validate_pair`, `validate_changed`, and `validate_insertion` plus parity. S1 calls these functions but does not define their correctness. Random parity candidates are produced by S0 fixture mutation, not by the S1 constructor.

S1 may enter only when the S0 gate exits 0, the progress row is `COMPLETE`, the target is clean, and the training manifest hashes are recorded.

## Files and symbol responsibilities

- `config.py`: immutable `SolverConfig`, seed, safe feature defaults, cache cap, comparison tolerance.
- `instance.py`: `ProblemInstance.parse`, `BlockSpec`, `BaySpec`, exact vertex tuple key, `dwell`, corrected `slack=D-R-dwell`, fit matrix and `assert_solvable_fit`.
- `geometry.py`: `ShapeInfo`, `GeomKernel.union_disjoint`, exact cached `obs` storage (tested but behavior used only S6), AABB prefilter, cache counters. No rounding-based shape identity.
- `state.py`: `Placement`, `SolutionState`, full/incremental objective diagnostics, place/remove, invariants.
- `checker_adapter.py`: sole runtime `utils` import; `official_check` and normalized checker result.
- `validate.py`: unary/pair/changed/insertion targeted validation and mismatch telemetry; calls the adapter only for parity/full-check boundaries.
- `serialize.py`: sole `serialize_non_interlock`; sorted integer dates, all EXIT then ENTRY, block-ID within type.
- `trivial.py`: `build_t0` using a fitting orientation and sequential empty-bay windows; raises `UnsolvableInstanceError` after failed fit preflight.
- `incumbent.py`: `VerifiedIncumbent.register_initial/try_update`, pre-serialized O(1) return object.
- `budget.py`: monotonic `Budget`, reserve, cooperative checkpoints, stage allowance.
- `entry.py`: T0-only `solve`, top-level armor, no unverified return path.
- `baseline/myalgorithm.py`: unchanged signature, delegate to `solver.entry.solve`; no greedy mutation.
- `tests/fixtures.py`: deterministic synthetic contracts and mutations.
- `harness/*.py`: CLI, selectors, schema, subprocess/process-group runner, checker/result normalization, comparisons, gates/report.
- `.gitignore`: ignore `benchmarks/evidence/**` except its README, `submission-dist/`, generated fixtures, and temporary run directories; preserve existing ignore rules.

## Slice dependency contracts

| Slice | Prerequisite / consumed artifacts | Produced for the next slice/stages | Explicit non-dependency | Next entry condition |
|---|---|---|---|---|
| S0-01 | Frozen checker, problem analysis | Parsed instance, fit preflight, checker adapter, semantic fixtures | Geometry/state/constructor | Contract suite GREEN and committed |
| S0-02 | S0-01 parsed shapes/fixtures | Exact geometry/cache/counters | State/constructor/interlock behavior | Geometry regression/checker proof GREEN |
| S0-03 | S0-01/02 instance and geometry | State/objective/targeted-validation parity APIs | S1 candidate generator | Zero parity mismatch and commit |
| S0-04 | S0-01…03 fit/state/validation | Serializer, T0, verified incumbent | S1 assignment/constructor | Example incumbent checker-verified |
| S0-05 | S0-04 stored incumbent | Budget and armored T0 entry | Later pipeline branches | Tiny-budget/fault stress GREEN |
| S0-06 | All prior S0 APIs | Shared harness, manifests, S0 gate evidence | Any S1-S6 behavior | Full S0 gate exits 0 |

## Ordered atomic slices

All commands below run from repository root with `PY=/opt/homebrew/Caskroom/miniforge/base/envs/ogc-2026/bin/python`.

### S0-01 — Checker contract lock and parsing

Observable: semantic fixtures call the official checker through one adapter and pin half-open timing, same-day handoff, same-day union-overlap rejection, all five OBS truth-table outcomes, legal contact, `P=0` residence, exit extension/Z1, and float Z2.

- RED: add `test_contract_semantics.py` importing missing `solver.checker_adapter.official_check`; run `cd baseline && $PY -m unittest tests.test_contract_semantics -v`. Expected exit 1 with only `ModuleNotFoundError: solver.checker_adapter`, not fixture/schema errors. Save console to `benchmarks/evidence/s0/s0-01/<run_id>/red.txt`.
- Minimum implementation: package skeleton, `instance.py` parsing/fit/dwell/slack, `checker_adapter.official_check`, and fixtures. Do not implement internal pair validation yet.
- GREEN: same command exits 0 with all named cases. Regression: `cd baseline && $PY -m unittest tests.test_contract_semantics -v` (the same suite is the entire available S0 regression).
- Checker proof: `$PY -m baseline.harness.cli contract --suite semantic --instances synthetic --seed 20260710` exits 0 and writes checker decisions. Until S0-06 supplies the CLI, run its planned equivalent through the test and record it; S0-06 must replay it through the CLI before gate.
- Commit: `test(s0): lock checker semantics and instance parsing`.

### S0-02 — Exact geometry and bounded cache

Observable: fit ranges, contact, negative local minima, union disjointness, directional OBS, exact tuple identity, cache hit/eviction, and AABB bypass are deterministic and match Shapely/checker fixtures.

- RED: `cd baseline && $PY -m unittest tests.test_geometry_kernel.GeometryKernelTests.test_contact_and_cache_identity -v`; exit 1 because `GeomKernel` is absent.
- Minimum implementation: `ShapeInfo`, exact tuple key, translated union, prepared/AABB fast paths, bounded LRU at initial cap 2^18, counters; OBS is an exact stored answer but not connected to solver behavior.
- GREEN: the targeted command exits 0. Regression: `cd baseline && $PY -m unittest tests.test_contract_semantics tests.test_geometry_kernel -v`.
- Checker proof: serialize the two-block contact and overlap fixtures and call `official_check`; contact feasible Stage 5, positive-area overlap infeasible at expected stage.
- Stress/microbenchmark: `$PY -m baseline.harness.cli benchmark --stage s0 --metric predicate --instances synthetic --timelimits 0 --seeds 20260710 --feature cache_cap=65536 --feature cache_cap_matrix=65536,262144,1048576,2097152`. S0-06 replays it. Record per predicate median/p95, hit rate, entries, RSS/entry, peak RSS, and chosen cap under the master rule.
- Commit: `feat(s0): add checker-exact geometry kernel and cache`.

### S0-03 — State, objective parity, and targeted revalidation

Observable: state incremental values equal recomputation; internal Z1/Z2/Z3/objective match checker; changed-block and insertion decisions equal a full check for 1,000 seeded candidates.

- RED: `cd baseline && $PY -m unittest tests.test_validate_parity.ValidateParityTests.test_seeded_1000_candidates -v`; exit 1 due missing `validate_insertion`/`SolutionState`.
- Minimum implementation: state and undo-neutral place/remove primitives; unary/pair branch-1/2 validators; `validate_changed` checking changed unary facts and pairs involving changed blocks; fixture mutation sampler. No candidate-ranking logic.
- GREEN: targeted command exits 0 with mismatch count 0. Regression: `cd baseline && $PY -m unittest tests.test_contract_semantics tests.test_geometry_kernel tests.test_state_parity tests.test_validate_parity -v`.
- Checker proof: `$PY -m baseline.harness.cli parity --kind targeted --cases 1000 --instances synthetic --seed 20260710` and `... parity --kind objective --cases 100 --instances synthetic --seed 20260710`; both exit 0, objective relative error ≤1e-6.
- Commit: `feat(s0): add state and targeted checker parity`.

### S0-04 — Canonical serializer, T0, and verified incumbent

Observable: every valid fixture and tracked example gets exactly one integer ENTRY/EXIT per block, empty-bay sequential placement, canonical order, and initial incumbent only after full check.

- RED: `cd baseline && $PY -m unittest tests.test_trivial.TrivialTests.test_example_registers_verified_incumbent -v`; exit 1 because `build_t0` is absent.
- Minimum implementation: serializer, fit-preflight T0, and incumbent. Empty-bay windows are per bay; choose preferred fitting bay/orientation deterministically, place at integer lower bound, schedule at `max(R_i,bay_available)` with `e=a+dwell`, update availability to e.
- GREEN: targeted test exits 0. Regression: all S0 tests through `test_trivial`.
- Checker proof: `$PY -m baseline.harness.cli benchmark --stage s0 --instances example --timelimits 5 --seeds 20260710 --feature pipeline=t0`; exit 0, Stage 5 feasible, `incumbent_verification_count>=1`.
- Failure proof: a synthetic no-fit instance causes `contract --suite preflight` exit 4 naming the block; it is not labeled solver failure or feasible fallback.
- Commit: `feat(s0): add canonical T0 and verified incumbent`.

### S0-05 — Budget/deadline and exception armor

Observable: timelimits 0.5, 2, 5, and 12 seconds and injected post-incumbent exceptions return the stored verified T0; no return path references an unverified state.

- RED: `cd baseline && $PY -m unittest tests.test_budget_entry.EntryArmorTests.test_exception_returns_verified_serial -v`; exit 1 because `entry.solve`/fault injection is absent.
- Minimum implementation: monotonic budget, conservative reserve (working formula `min(max(.05*TL,0.05), max(TL-0.05,0.05))` for TL<3 and design clamp for longer budgets), checkpointing, fault-injection test seam, entry delegation. Never catch `KeyboardInterrupt/SystemExit` inside production candidate code; the outer entry catches ordinary `Exception`, while harness handles process termination. The stored T0 is returned after post-registration failure.
- GREEN: targeted command exits 0. Regression: `cd baseline && $PY -m unittest discover -s tests -p 'test_*.py' -v` for existing S0 tests.
- Checker/stress: `$PY -m baseline.harness.cli stress --stage s0 --instances stress --timelimits 0.5,2,5,12 --seeds 20260710 --feature fault=none,after_incumbent`; every valid row Stage 5 feasible, wall time ≤TL+0.25s locally, no missing verified incumbent.
- Static safety check: `rg -n 'return .*state|return serialize\(' baseline/solver baseline/myalgorithm.py` is reviewed; only incumbent-return and initial registration sites are allowed and recorded.
- Commit: `feat(s0): protect deadlines and verified fallback`.

### S0-06 — Minimum viable shared harness and full foundation gate

Observable: all eight CLI commands parse, shared runner records proof-of-run, resumes interruption, deduplicates identity, kills process groups, invokes official checker, and evaluates S0 without one-off scripts.

- RED: `cd baseline && $PY -m unittest tests.test_harness_schema.HarnessSchemaTests.test_interrupted_run_is_not_complete -v`; exit 1 because harness modules are absent.
- Minimum implementation: the master harness contract, manifests, ignored evidence/dist directories, contract/parity/benchmark/stress/ab/gate/report/submission-rehearsal command shells. Later-stage command bodies may reject with exit 4 “stage unsupported”; S0 bodies must work. Add `tests/test_harness_schema.py` and `test_harness_process.py`.
- GREEN: targeted harness tests and `cd baseline && $PY -m unittest discover -s tests -p 'test_*.py' -v` exit 0.
- Checker proof: run the exact full gate sequence below. No harness implementation outside `baseline/harness`.
- Commit: `feat(s0): add resumable checker-authoritative harness`.

Every slice changes status/history before start, after RED, after GREEN, after checker proof, after benchmark/stress, and after commit/gate. At each point record exact command and evidence path. Temporary fixture JSON lives inside its run directory; close Shapely/solver objects, terminate process groups, delete partial `submission-dist`, and leave incomplete evidence without `COMPLETE` for safe resume.

## S0 gate

Exact sequence:

```bash
PY=/opt/homebrew/Caskroom/miniforge/base/envs/ogc-2026/bin/python
$PY -m baseline.harness.cli contract --suite preflight --instances training --seed 20260710
$PY -m baseline.harness.cli contract --suite semantic --instances synthetic --seed 20260710
$PY -m baseline.harness.cli parity --kind geometry --cases 1000 --instances synthetic --seed 20260710
$PY -m baseline.harness.cli parity --kind targeted --cases 1000 --instances synthetic --seed 20260710
$PY -m baseline.harness.cli parity --kind objective --cases 100 --instances synthetic --seed 20260710
$PY -m baseline.harness.cli benchmark --stage s0 --metric predicate --instances synthetic --timelimits 0 --seeds 20260710 --feature cache_cap_matrix=65536,262144,1048576,2097152
$PY -m baseline.harness.cli benchmark --stage s0 --instances training --timelimits 5 --seeds 20260710 --feature pipeline=t0
$PY -m baseline.harness.cli stress --stage s0 --instances stress --timelimits 0.5,2,5,12 --seeds 20260710 --feature fault=none,after_incumbent
$PY -m baseline.harness.cli gate --stage s0 --latest-complete --commit HEAD
$PY -m baseline.harness.cli report --stage s0 --latest-complete
```

PASS requires all unittest/contract/parity commands exit 0; all 40 training records present, checker-feasible at Stage 5 within five seconds, exactly one verified initial incumbent, and no unverified-return counter; predicate table with count/median/p95/hit/memory produced; selected cache cap meets the master memory rule; all valid stress rows feasible with no timeout/leak; gate exit 0. Any semantic mismatch, infeasible official case, missing run, or incumbent-safety failure is blocking. Performance failure may recalibrate cache/reserve only within the predefined matrix; the 5-second feasibility requirement is not weakened.

Feature/default after PASS: `native_solver=true` and `pipeline=t0` default; all S1-S6 features false. Rollback on failure is to the last slice commit whose regression and checker proof pass; never roll back by editing the checker or deleting a test. S0 atomic commits are the six messages above.

Known risks/deferred decisions: official training data is not tracked; cache cap is selected by S0 evidence; local 0.5-second scheduling may be noisy, but checker feasibility and no-overrun remain mandatory. S1 receives selected cache cap, verified APIs, manifest hashes, and the S0 `gate.json`; it does not receive any constructor behavior.
