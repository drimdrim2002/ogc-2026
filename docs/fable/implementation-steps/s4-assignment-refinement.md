# S4 Execution Plan: Assignment Refinement

Parent: [`../fable-native-implementation-progress.md`](../fable-native-implementation-progress.md)

Status: `BLOCKED`; gate: `NOT_RUN`; selected defaults: `assignment_refinement=false`, `cross_bay=false`; planned slices: 4

Plan-reset authority: [`plan-reset-01.md`](plan-reset-01.md). S4 is now an optional promotion track. Its current dirty RECOVERY-08 experiment is preserved but is not a selected product baseline and does not block mandatory hardening/package work from stable S3.

## Goal and stage boundary

S4 is the sole owner of assignment-changing search: cross-bay moves, swaps, D6-style rebalance, and assignment-v2. It reuses S3 transactions/repair/acceptance and S2 backend isolation without changing S3’s gate or requiring S4 artifacts backward. Every candidate evaluates checker-float Z2 and Z3 exactly, measures Z1 after repair/retime, and can only replace the incumbent after full check.

Included: `AssignmentRequest/Result` extension, Gurobi preferred float MIP, CP-SAT scaled fallback, greedy fallback, cross-bay move/swap neighborhoods, two-bay retime, high-w2/w3 A/B, and default decision. Excluded: process portfolio and interlock.

Evidence: design §2.2, §4.5-4.6, §5 S4; checker Z2/Z3 `1398-1421`. Empty bays participate in Z2; one bay has Z2=0. CP-SAT’s scaled model is only a proposal generator: returned assignment is recomputed with exact checker floats and compared after construction/checking.

Prerequisite: a clean, qualified S3 selected-default baseline established under PLAN-RESET-01. Consumes fit matrix, constructor, exact layer, S3 transactions/acceptor, and verified incumbent. Produces an optional assignment-changing single-process tier. Explicit non-dependencies: no process exchange, no interlock/OBS, and no mandatory hardening/package behavior. S5 may evaluate the chosen stable lower tier even when S4 finishes `GATE_FAILED_DISABLED` or remains `BLOCKED` with its flags false.

## Files/symbols

- Extend `exact.py` with S4-only `AssignmentRequest`, `AssignmentResult`, `assign` adapter; S2 retime protocol/tests remain unchanged.
- Extend `gurobi_backend.py::assign_gurobi`, `cpsat_backend.py::assign_cpsat`; create exact-float recomputation in `assign.py`.
- Extend `assign.py`: `assignment_v2`, `choose_assignment_candidate`, greedy fallback.
- Extend `alns.py`: S4 registry D6/cross-bay move and swap, `changes_assignment=True`, two-bay transaction/repair/retime.
- Create `tests/test_assignment_refinement.py`; extend backend/entry/harness reports.

## Slice dependency contracts

| Slice | Prerequisite / consumes | Produces | Explicit non-dependency | Next entry condition |
|---|---|---|---|---|
| S4-01 | S2 exact isolation, S1 v1 | Gurobi assignment-v2 proposal | Cross-bay/portfolio | Float parity/model proof GREEN |
| S4-02 | S4-01 request/evaluator | CP-SAT/v1 fallbacks | S5 processes | Fault fallback GREEN |
| S4-03 | S3 transactions/operators, S4-01/02 | Move/swap/D6 refinement | Portfolio/interlock | Undo/checker proof GREEN |
| S4-04 | All prior S4 artifacts | Safety decision plus optional promotion decision | S5 implementation and mandatory hardening | Safety passes; promotion is `COMPLETE` or `GATE_FAILED_DISABLED` |

## Atomic slices

### S4-01 — Assignment-v2 contract and Gurobi model

Observable: fit-constrained binary assignment optimizes exact-float weighted-load range plus preference and congestion, starts from v1, and returns pure assignments without touching state.

- RED: `cd baseline && $PY -m unittest tests.test_assignment_refinement.AssignmentV2Tests.test_gurobi_float_z2_matches_checker -v`; missing request/model.
- Implement `x_ij`, exact `u_j*load_j`, bounded Wmax/Wmin/range, preference, overload; timebox from config, v1 MIP start, lazy isolated backend. Objective terms retain Python/Gurobi floats.
- GREEN targeted (model-builder mandatory; solve when available), regress all.
- Checker: pass v1/v2 proposals through S1 constructor and official checker; internal/checker Z2 difference ≤1e-6 relative.
- Benchmark: `benchmark --stage s4 --component assignment_v2 --instances high-w23 --timelimits 60 --seeds 20260710 --feature assignment_backend=gurobi`.
- Commit: `feat(s4): add exact-float Gurobi assignment v2`.

### S4-02 — CP-SAT and greedy fallbacks

Observable: Gurobi fault selects scaled CP-SAT, then greedy v1; exact-float post-evaluation prevents a scaled-model Z2 regression from being accepted.

- RED: `cd baseline && $PY -m unittest tests.test_assignment_refinement.AssignmentFallbackTests.test_scaled_candidate_rechecked_as_float -v`; fallback missing.
- Implement scale 1e6 proposal with overflow-bound preflight, normalized status, exact float recomputation; if unsafe scaling or backend failure use v1. Construct/check each viable proposal within budget and retain best verified incumbent.
- GREEN targeted and full regression.
- Checker/stress: forced Gurobi, CP-SAT, and both faults; all preserve checker-feasible prior solution, with fallback reason.
- Commit: `feat(s4): add safe assignment fallbacks`.

### S4-03 — Cross-bay moves and swaps

Observable: move/swap candidate updates both bay memberships, loads, exact Z2/Z3, repairs only affected bays, retimes them, and fully undoes on any failure. S3 operators remain intra-bay.

- RED: `cd baseline && $PY -m unittest tests.test_assignment_refinement.CrossBayTests.test_move_swap_undo_and_float_delta -v`; cross-bay registry absent.
- Implement candidates prioritized by exact `(w2*deltaZ2+w3*deltaZ3)` plus measured congestion; use fit constraints, two-bay transaction, S1 insertion in target bay, S2 retime both bays, S3 current/incumbent protocol. No approximate delta can authorize incumbent update.
- GREEN targeted; regress `test_alns` to prove S3 unchanged plus full suite.
- Checker: successful move and swap synthetic fixtures full-check; injected repair/retime/check faults return exact pre-candidate incumbent SHA.
- Benchmark: `benchmark --stage s4 --component cross_bay --instances high-w23 --timelimits 60 --seeds 20260710 --feature cross_bay=true`; move/swap attempts and accepted counts required.
- Commit: `feat(s4): add guarded cross-bay refinement`.

### S4-04 — Entry integration, A/B, and default gate

Observable: assignment-v2 may seed construction and cross-bay neighborhoods may run after S3; each is separately flaggable and default-enabled only by paired frozen qualification evidence.

- RED: `cd baseline && $PY -m unittest tests.test_budget_entry.EntryArmorTests.test_assignment_refinement_failure_keeps_s3 -v`; entry branch absent.
- Integrate with exact timeboxes and feature flags. Predefine `high-w23` from manifest statistics before outcomes; remaining set is `training - high-w23`. Separate implementation safety from promotion gain. A safety failure leaves this optional track `BLOCKED`; safety PASS with gain failure records `GATE_FAILED_DISABLED`. In both cases flags remain false and stable S3 proceeds to mandatory hardening.
- GREEN targeted/full discovery; exact gate below.
- Commit: `feat(s4): integrate measured assignment refinement`.

RECOVERY-08 disposition: focused GREEN passed 8/8 and affected regression passed 66/67. The failure occurred because the 25-percent margin cap skipped the worker at about 8.92 seconds of work allowance, so the existing sufficient-work telemetry test did not observe `assignment_seed_attempts`. The next development identity must split safe-skip telemetry from sufficient-work attempt telemetry. Keep the measured 2.5-second publish margin; replace the percentage cap only after measuring minimum useful worker time and parent/kill tail.

Update progress at all required moments. Evidence path `benchmarks/evidence/s4/...`. Cleanup requirements: dispose exact models, restore transactions, stop processes, and delete partial fixtures/package. Rollback both S4 flags and return the qualified S3 incumbent. Atomic commits are the four messages above, but no current dirty recovery diff may be committed wholesale; follow PLAN-RESET-01 Git boundaries.

## Repeatable safety/prequalification and frozen promotion gate

Targeted RED/GREEN, affected regression, checker sentinels, and deadline/cleanup stress are repeatable development or safety checks after a relevant source change, each with a new attempt identity. The A/B and final `gate` command below are Tier Q frozen qualification: run them exactly once only after a clean source commit and complete qualification manifest are frozen. A failed frozen identity is not rerun unchanged; return to development and create a new identity.

```bash
PY=/opt/homebrew/Caskroom/miniforge/base/envs/ogc-2026/bin/python
cd baseline && $PY -m unittest discover -s tests -p 'test_*.py' -v
cd ..
$PY -m baseline.harness.cli parity --kind objective --cases 100 --instances high-w23 --seed 20260710 --feature component=assignment
$PY -m baseline.harness.cli ab --stage s4 --instances high-w23 --timelimits 60,300 --seed 20260710,20260711 --feature assignment_refinement --a false --b true
$PY -m baseline.harness.cli ab --stage s4 --instances training --timelimits 60 --seed 20260710 --feature assignment_refinement --a false --b true
$PY -m baseline.harness.cli stress --stage s4 --instances smoke-3 --timelimits 60 --seeds 20260710 --feature assignment_refinement=true --feature backend_fault=gurobi,cp_sat,both
$PY -m baseline.harness.cli gate --stage s4 --latest-complete --commit HEAD
$PY -m baseline.harness.cli report --stage s4 --latest-complete
```

Safety PASS: all Stage 5 feasible; exact-float internal/checker Z2 relative difference ≤1e-6; no accepted incumbent is worse than paired S3; every remaining training case is no worse than paired S3; backend faults, deadline expiry, invalid worker output, and cleanup preserve the verified prior solution; no timeout, overrun, leak, or unverified return occurs. Safety failures cannot be averaged away and leave S4 `BLOCKED`, but they do not block mandatory hardening from stable S3.

Promotion PASS: after safety PASS, high-w23 median total checker objective is strictly lower, at least half (5/10) of high-w23 cases improve, at least one move or swap is accepted, and the default-on frozen A/B satisfies every safety rule. If safety passes but this gain rule fails, record `GATE_FAILED_DISABLED`, keep both S4 flags false, and continue with stable S3.

Feature/default after promotion PASS: `assignment_refinement=true`, Gurobi preferred, CP-SAT then v1 fallback; a specific assignment-v2/cross-bay subflag may remain off only if the other alone satisfies the full frozen promotion gate and the disabled decision is recorded. Otherwise defaults remain false. Known risks: scaled CP-SAT ranking discrepancy, construction cost for multiple assignments, deadline-return overhead, and high-w23 sample size; exact post-check, split-deadline safety, and a preregistered selector control them.
