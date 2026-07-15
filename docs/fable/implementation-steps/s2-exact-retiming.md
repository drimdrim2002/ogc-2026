# S2 Execution Plan: Exact Retiming

Parent: [`../fable-native-implementation-progress.md`](../fable-native-implementation-progress.md)

Status: `COMPLETE`; gate: `PASS`; planned slices: 5

## Goal and boundary

S2 optimizes entry/exit dates for a fixed non-interlock layout through one immutable retiming contract, isomorphic Gurobi indicator-MIP and CP-SAT models, a bounded pilot selector, warm starts/hints, exception isolation, and a never-worse checker guard. It must improve median Z1, never worsen any tested Z1, respect timeboxes, and preserve the S1 incumbent if one or both backends fail.

Included: backend probe/results, per-bay conflict graph from S0 geometry, retime request/result validation, Gurobi and CP-SAT retime, pilot and one bounded initial/final sweep. Excluded: assignment-v2 (`assign()` is not part of the S2 protocol), LNS triggers/dirty counters, cross-bay changes, process portfolio, and interlock variable exits.

Evidence: design §2.4-2.5, §4.4, §5 S2, §6; checker half-open separation `1156-1158`, collision filtering `1260-1267`, Z1 `1398-1402`. Non-interlock uses `e=a+dwell`; conflict pairs are exactly union-overlap pairs. Equality `e_i<=a_j` is legal.

Prerequisite: S1 `COMPLETE`, constructor incumbent/layout and S0 checker APIs. Produces `RetimeRequest/ExactResult`, backend health/telemetry, `retime_layout`, and selected-backend evidence for S3. Explicit non-dependencies: S3 acceptance/dirty triggers, S4 assignment requests, S5 multiprocessing, S6 nesting. S3 enters only after the S2 gate exits 0.

## Files and symbols

- Create `solver/exact.py`: `RetimeRequest`, `ExactResult`, `BackendHealth`, `probe_backends`, `normalize_result`, `validate_result`, `select_pilot_backend`; protocol has only `retime` in S2.
- Create `solver/gurobi_backend.py::retime_gurobi`; lazy import/env/model, indicators, seed/threads/timebox, MIP start, pure result extraction.
- Create `solver/cpsat_backend.py::retime_cpsat`; same domains/pairs/objective, enforced disjunction, hint, timebox/workers.
- Create `solver/retime.py`: `build_conflict_pairs`, `apply_retime_copy`, `retime_bay`, `retime_sweep`; strict Z1 and official-check guard.
- Change config/budget/entry/harness telemetry. Create `tests/test_exact_backends.py`, `tests/test_retime.py`.

## Slice dependency contracts

| Slice | Prerequisite / consumes | Produces | Explicit non-dependency | Next entry condition |
|---|---|---|---|---|
| S2-01 | Verified S1 incumbent/budget | Immutable retime contract/probes | Backend models, assignment | Fault isolation GREEN |
| S2-02 | S2-01 contract, S0 geometry | Gurobi retime adapter | CP-SAT/selector | Model/checker proof GREEN |
| S2-03 | S2-01/02 domains | CP-SAT adapter/backend parity | S3 trigger, S4 assignment | Synthetic optimal parity GREEN |
| S2-04 | Both adapters, S1 state | Pilot/sweep/never-worse orchestration | LNS dirty counters | Fallback/checker stress GREEN |
| S2-05 | All prior S2 artifacts | Integrated retiming/default evidence | S3 implementation | Full S2 gate exits 0 |

## Atomic slices

### S2-01 — Common contract, request validation, and lazy probe

Observable: malformed/out-of-budget results normalize to no solution, backend exceptions become health records, and no backend initializes before a verified incumbent exists.

- RED: `cd baseline && $PY -m unittest tests.test_exact_backends.ExactContractTests.test_probe_after_incumbent_and_exception_isolation -v`; exit 1 for missing exact module.
- Implement immutable request primitives, result status vocabulary, deadline-bounded call wrapper, lazy probes, pure-data boundary. Probe import/env/license/model/optimize/extraction separately. Protocol excludes assignment.
- GREEN targeted; regression S0-S1 plus exact contract.
- Checker proof: constructor output remains byte-identical/checker-feasible when both probe calls are faulted.
- Stress: `stress --stage s2 --instances example --timelimits 12 --seeds 20260710 --feature exact_retime=true --feature backend_fault=probe_all`; fallback tier `constructor`, feasible.
- Commit: `feat(s2): add isolated exact retiming contract`.

### S2-02 — Gurobi indicator retiming

Observable: fixed-layout conflict pairs use indicator separation, integer starts, `e=a+dwell`, tardiness objective, MIP start, and timebox.

- RED: `cd baseline && $PY -m unittest tests.test_exact_backends.GurobiRetimeTests.test_equality_handoff_optimum -v`; missing function, not license failure. The test is never skipped: with an available license it solves the fixture; without one it asserts the exact normalized unavailability record while mandatory model-builder/indicator assertions still execute.
- Implement bounded horizon `max(R)+sum(dwell)`, integer `a/T`, indicator pair, SolCount-aware extraction, `Threads<=4`, deterministic seed, no logs.
- GREEN targeted exits 0 in both availability branches, with model-builder assertions always executed; regression current suite.
- Checker: apply small synthetic optimum to a state copy, serialize/full-check Stage 5 and exact Z1.
- Benchmark: `benchmark --stage s2 --component retime --instances synthetic --timelimits 2 --seeds 20260710 --feature retime_backend=gurobi`; status/bound/gap/build/solve/first-solution required.
- Commit: `feat(s2): add Gurobi indicator retiming`.

### S2-03 — Isomorphic CP-SAT retiming and optimal parity

Observable: CP-SAT has identical variables/domains/conflict pairs/objective and agrees with Gurobi/manual optima on small fixtures.

- RED: `cd baseline && $PY -m unittest tests.test_exact_backends.BackendParityTests.test_small_optima_match -v`; missing CP-SAT implementation.
- Implement integer vars, Bool disjunction with `OnlyEnforceIf`, current hint, worker/seed/time limit. Compare normalized objective and extracted dates.
- GREEN targeted; regress all exact and prior tests.
- Checker: each available backend’s extracted solution full-checks; optimal Z1 equals manual optimum and available-backend objectives are identical.
- Parity: `parity --kind backend --cases 50 --instances synthetic --seed 20260710 --feature timebox=2`; zero semantic/optimal mismatch.
- Commit: `feat(s2): add isomorphic CP-SAT retiming`.

### S2-04 — Pilot, sweep, and never-worse application

Observable: bounded pilot uses at most two tardiest bays, fixes a winner, applies results to copies, rejects non-improvement/invalid output, and full-checks before incumbent replacement.

- RED: `cd baseline && $PY -m unittest tests.test_retime.RetimeTests.test_never_worse_and_fixed_pilot -v`; missing orchestrator.
- Implement working pilot `min(4s, .08*safe_remaining, .5*first_sweep_budget)` split by available backend/bay; selection is improvement descending, first-solution ascending, solve ascending, backend name tie-break. If no pilot budget, available Gurobi then CP-SAT. Timebox per call `min(5s,.10*safe_remaining)`. These remain flags until S2 gate records measured choice.
- GREEN targeted; regression all.
- Checker: deliberately corrupt backend dates are rejected and S1 incumbent returned; valid strict improvement is full-checked.
- Stress: forced Gurobi import/license/optimize/extraction faults each fall to CP-SAT; forced both fail preserves exact operations SHA of S1 incumbent.
- Commit: `feat(s2): guard and select exact retiming`.

### S2-05 — Entry integration and retiming gate

Observable: after constructor, a bounded initial sweep and optional budgeted final sweep run; no S3 trigger is required; evidence chooses/configures pilot/timebox without weakening safety.

- RED: `cd baseline && $PY -m unittest tests.test_budget_entry.EntryArmorTests.test_retime_failure_keeps_constructor -v`; entry branch absent.
- Implement feature integration, per-stage timing, backend/fallback telemetry. Evaluate timebox matrix `{1,2,5}` seconds and pilot totals `{0,2,4}` on dev-10; select lexicographically by all-feasible, median gain, then least wall time. If no candidate gives median positive Z1, S2 remains blocked.
- GREEN targeted and full discovery.
- Checker/gate: exact sequence below.
- Commit: `feat(s2): integrate measured exact retiming`.

Every slice records before/RED/GREEN/checker/benchmark/gate progress entries. Evidence lives under `benchmarks/evidence/s2/...`. Cleanup requirements: close/dispose every Gurobi Model/Env and CP-SAT object, never persist license data, stop subprocess groups, and remove partial temp packages. Rollback disables `exact_retime` and returns the S1 verified incumbent.

## Full gate

```bash
PY=/opt/homebrew/Caskroom/miniforge/base/envs/ogc-2026/bin/python
cd baseline && $PY -m unittest discover -s tests -p 'test_*.py' -v
cd ..
$PY -m baseline.harness.cli parity --kind backend --cases 50 --instances synthetic --seed 20260710 --feature timebox=2
$PY -m baseline.harness.cli benchmark --stage s2 --instances training --timelimits 60 --seeds 20260710 --feature exact_retime=true --feature retime_backend=auto
$PY -m baseline.harness.cli ab --stage s2 --instances dev-10 --timelimits 60 --seed 20260710 --feature exact_retime --a false --b true
$PY -m baseline.harness.cli stress --stage s2 --instances smoke-3 --timelimits 12,60 --seeds 20260710 --feature exact_retime=true --feature backend_fault=gurobi_import,gurobi_license,gurobi_optimize,gurobi_extract,both
$PY -m baseline.harness.cli gate --stage s2 --latest-complete --commit HEAD
$PY -m baseline.harness.cli report --stage s2 --latest-complete
```

PASS: every case checker-feasible; per-instance Z1 with S2 ≤ paired S1 Z1; dev-10 median `(Z1_S1-Z1_S2)>0`; every exact call wall time ≤requested timebox+0.25s and pipeline within solver timelimit; forced Gurobi failure selects CP-SAT and produces a feasible result or preserves incumbent; both-fail output SHA equals prior verified incumbent; all available backends agree with manual/synthetic optimal objectives; no backend exception escapes. Gate exits 0.

Any objective worsening, checker/parity mismatch, timebox violation, corrupted incumbent, or missing fallback blocks S3. Feature/default after PASS: `exact_retime=true`, `retime_backend=auto` with measured pilot/timebox stored; unavailable backend remains disabled per instance. Known risks are license variance, model construction consuming timebox, and no improvable Z1; the last is a legitimate blocking gate, not a reason to claim success.
