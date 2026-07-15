# S3 Execution Plan: Intra-Bay LNS

Parent: [`../fable-native-implementation-progress.md`](../fable-native-implementation-progress.md)

Status: `COMPLETE`; gate: `PASS (clean selected-default requalification)` under [`plan-reset-01.md`](plan-reset-01.md); planned slices: 5

## Goal, ownership, and dependencies

S3 adds an anytime intra-bay destroy/repair search around the S2 solver. It owns state transactions/undo, intra-bay operators, current-versus-incumbent separation, strict/RRT/SA evaluation, retiming triggers, adaptive weighting, stagnation/restart, structural counters, ablation, and prefix-consistent anytime behavior.

S3 explicitly excludes cross-bay moves, swaps, D6 bay rebalance, assignment-v2, portfolio, and interlock. Repair candidates may accept a `bays_try` parameter for S4 compatibility, but S3 always passes exactly the original bay and tests that membership/load/Z2/Z3 do not change. This is the binding S3/S4 boundary.

Included scope is only same-bay removal, reinsertion, retiming, acceptance, and control behavior. Excluded scope is every assignment-changing or parallel/interlock behavior listed above.

Evidence: design §4.5, §5 S3, §6 and verified incumbent §4.1. Draft ALNS mechanics are hypotheses, not defaults. S3 fixes the draft ordering bug by storing `previous_cur_obj`, classifying against it, then updating `cur_obj` only after acceptance. The official checker remains final authority at incumbent boundaries.

Prerequisite: S2 `COMPLETE`, exact retime contract, constructor repair primitive, and complete harness. Produces `alns.py`, intra-bay operator/acceptor interfaces and evidence consumed by S4. Explicit non-dependencies: no `AssignmentRequest`, cross-bay membership mutation, `portfolio.py`, or `interlock.py`. S4 enters only after S3 gate exit 0.

## Files and symbols

- Create `solver/alns.py`: `MoveTransaction`, `DestroyOperator`, `RepairOperator`, D1-D5, R1-R3, `StrictAcceptor`, `RRT_Acceptor`, `SAAcceptor`, `OperatorWeights`, `StagnationController`, `RetimeTrigger`, `run_alns`, metrics.
- Extend `state.py` with exact undo token/invariant snapshot; `construct.insert_block` with S3 repair call mode restricted to original bay; `retime.py` for dirty-bay calls; config/entry/harness flags/reports.
- Create `tests/test_alns.py`; extend state, retime, entry tests.

## Slice dependency contracts

| Slice | Prerequisite / consumes | Produces | Explicit non-dependency | Next entry condition |
|---|---|---|---|---|
| S3-01 | S1 insertion, S2 incumbent | Transaction/D1/R3 core | Cross-bay mutation | Undo/fault proof GREEN |
| S3-02 | S3-01 transaction | Intra-bay D1-D5/R1-R3 pool | D6/S4 assignment | Operator checker proof GREEN |
| S3-03 | S3-01/02 candidates | Correct current/incumbent strict loop | Alternative acceptors | Monotonic trace GREEN |
| S3-04 | S3-03 loop, S2 retime | Triggers/acceptors/weights/stagnation | S4 neighborhoods | Preregistered control A/B complete |
| S3-05 | All prior S3 artifacts | Prefix-consistent integrated LNS | Cross-bay implementation | Full S3 gate exits 0 |

## Atomic slices

### S3-01 — Transactional minimal LNS

Observable: D1 random removal plus R3 EDD reinsertion in the same bay either commits a wholly feasible candidate or restores byte-equivalent state on repair failure/rejection/exception/deadline.

- RED: `cd baseline && $PY -m unittest tests.test_alns.TransactionTests.test_undo_all_outcomes -v`; exit 1 for missing transaction.
- Implement undo tokens for placements, bay arrays, Z1/Z3/loads/RNG checkpoint; D1 and R3 use S1 insertion with `[original_bay]`; validate every insertion. One iteration API only.
- GREEN targeted; regress S0-S2 and state invariants.
- Checker: for accepted and restored synthetic iterations, serialize/full-check; membership, loads, Z2 and Z3 unchanged from pre-iteration.
- Stress: injected failure after each mutation point; output incumbent SHA unchanged, no partial state.
- Commit: `feat(s3): add transactional intra-bay LNS core`.

### S3-02 — Intra-bay destroy/repair pool

Observable: D1 random, D2 worst tardiness, D3 critical chain, D4 time window, D5 Shaw-related and R1 noisy greedy, R2 regret-2, R3 EDD all execute only within one bay and report attempts/success/failure.

- RED: `cd baseline && $PY -m unittest tests.test_alns.OperatorTests.test_all_operators_preserve_assignment -v`; named operators absent.
- Implement q from config (working 2%-6%, cap 15%), exact active-conflict chain, deterministic RNG, all-or-nothing repair. Do not implement D6. Operator registry rejects any S3 operator declaring `changes_assignment=True`.
- GREEN targeted and full current regression.
- Checker: one successful candidate per operator on deterministic synthetic fixtures full-checks; failure cases restore checker-feasible input.
- Benchmark: `benchmark --stage s3 --component operators --instances smoke-3 --timelimits 60 --seeds 20260710 --feature alns=true --feature acceptor=strict --feature adaptive=false`; every enabled operator has attempt count >0 unless its preregistered applicability predicate is false and recorded.
- Commit: `feat(s3): add intra-bay LNS operators`.

### S3-03 — Current/incumbent separation and strict acceptance

Observable: current may change only on accepted feasible candidates; incumbent objective is monotonically decreasing checker objective; outcome scoring correctly distinguishes improving/current-equal/worse using the saved previous value.

- RED: `cd baseline && $PY -m unittest tests.test_alns.AcceptanceTests.test_improvement_classified_before_cur_obj_update -v`; fails for missing loop and directly guards the draft bug.
- Implement `StrictAcceptor` as initial/default, complete loop checkpoints, full check only for potential incumbent plus configurable safety sample, counters and monotonic trace. Strict current acceptance requires `new < previous-EPS`; equal/worse rejects.
- GREEN targeted; full regression.
- Checker: seeded 100 iterations on synthetic and example, every incumbent trace entry full-check feasible, objective strictly decreases; final serialized incumbent full-checks.
- Stress: faults at accept, full-check, and report retain prior incumbent; deadline at each boundary returns stored operations.
- Commit: `feat(s3): separate current and verified incumbent`.

### S3-04 — Retiming, alternative acceptance, weights, and stagnation

Observable: accepted spatial edits increment same-bay dirty counters; bounded S2 retime can improve but never worsen; strict, RRT, and SA plus static/adaptive weights are feature-selectable; stagnation reheats/expands/restarts without assignment change.

- RED: `cd baseline && $PY -m unittest tests.test_alns.ControlTests.test_retime_stall_and_acceptor_transitions -v`; missing controllers.
- Implement dirty threshold working `max(5,ceil(.05*n_b))`, minimum interval `.03*TL` only as matrix candidates; RRT 3%→0 and SA warmup/calibration as flags. Save previous objective before any assignment. Restart perturbs within bays and remains feasible. Static uniform remains default pending A/B.
- GREEN targeted; regression all.
- Checker: controller transition fixture and forced backend failure both preserve feasible incumbent.
- A/B preregistration: compare acceptors `{strict,rrt,sa}` and adaptive `{false,true}` on dev-10 seeds `{20260710,20260711,20260712}` at 60 seconds. Choose acceptor by all-feasible, lowest median paired objective, then most wins, then `strict>rrt>sa` tie-break. Enable adaptive only if its median is lower and at least 6/10 instance-seed aggregates win; otherwise false. Dirty trigger selected from `{max(3,.03n_b),max(5,.05n_b),max(8,.08n_b)}` by lowest objective subject to retime ≤20% search wall time.
- Commit: `feat(s3): add retimed adaptive acceptance controls`.

### S3-05 — Prefix-consistent anytime integration and gate

Observable: same-seed 300-second execution includes an identical first-60-second decision prefix (under fake-clock test) and therefore retains an incumbent no worse than the 60-second run; real runs show iterations and accepted candidates.

- RED: `cd baseline && $PY -m unittest tests.test_alns.AnytimeTests.test_300_budget_contains_60_prefix -v`; entry/search schedule currently absent/non-prefix.
- Implement absolute elapsed-time epochs and fixed per-call timeboxes so decisions before 60 seconds do not depend on declared total TL; only hard-stop availability differs. RNG draw order and profile selection are prefix-stable. A fake monotonic clock asserts identical first-epoch event trace. Integrate selected S3 defaults behind flag.
- GREEN targeted plus full discovery.
- Checker/gate exact sequence below.
- Commit: `feat(s3): integrate prefix-consistent anytime LNS`.

Progress is updated before, after RED/GREEN/checker/benchmark, and after gate. Evidence path `benchmarks/evidence/s3/<slice>/<run_id>/`. Clean backend objects and child groups; remove temporary candidate files; incomplete evidence has no `COMPLETE`. Rollback disables `alns` and returns the S2 verified incumbent. A failed experimental acceptor/adaptation merely leaves that subfeature off; safety failure blocks S4.

## Full gate

```bash
PY=/opt/homebrew/Caskroom/miniforge/base/envs/ogc-2026/bin/python
cd baseline && $PY -m unittest discover -s tests -p 'test_*.py' -v
cd ..
$PY -m baseline.harness.cli benchmark --stage s3 --instances training --timelimits 60 --seeds 20260710 --feature alns=true
$PY -m baseline.harness.cli benchmark --stage s3 --instances dev-10 --timelimits 60,300 --seeds 20260710 --feature alns=true
$PY -m baseline.harness.cli ab --stage s3 --instances dev-10 --timelimits 60 --seed 20260710,20260711,20260712 --feature acceptor --a strict --b rrt,sa
$PY -m baseline.harness.cli ab --stage s3 --instances dev-10 --timelimits 60 --seed 20260710,20260711,20260712 --feature alns_adaptive --a false --b true
$PY -m baseline.harness.cli stress --stage s3 --instances stress --timelimits 5,12,60 --seeds 20260710 --feature alns=true --feature fault=repair,accept,retime,full_check
$PY -m baseline.harness.cli gate --stage s3 --latest-complete --commit HEAD
$PY -m baseline.harness.cli report --stage s3 --latest-complete
```

PASS: all outputs Stage 5 feasible; incumbent trace strictly monotonic/nonincreasing with no unverified entry; for every dev-10 instance, checker objective at 300 seconds ≤ its paired 60-second objective; aggregate 60-second S3 median objective is strictly lower than paired S2 and at least 5/10 improve, with none worse than S2; iterations >0 on all cases with ≥1 second of LNS allocation, total accepted >0 over the set, each applicable operator attempted, and full-check safety samples mismatch zero; selected acceptor/adaptive defaults follow the preregistered rule; assignment/Z2/Z3 stay unchanged within S3; gate exits 0.

Any infeasibility, undo mismatch, assignment change, 300>60 regression, no actual search, or checker mismatch blocks S4. Feature/default after PASS: `alns=true`; acceptor/adaptive/dirty settings exactly as evidence selects. S4 consumes the transaction/operator interfaces but is solely responsible for permitting assignment changes.

## Clean selected-default stabilization qualification

`S3-STABILIZATION-01-RECOVERY-01` established the selected default at candidate commit `2a9da5757b451b2a0c2ed4a884145fdcb255d111` without changing the tracked harness. Its ignored one-shot public-entry smoke evidence is under `benchmarks/evidence/s3-stabilization-01/development/20260715T032800Z-s3-stabilization01-recovery01/`: `smoke-3` was 3/3 Stage 5 feasible with positive ALNS iterations, prefix consistency, acceptor `sa`, adaptive false, accepted candidates, and zero checker, assignment, Z2, Z3, unverified-return, timeout, or leak failures.

Frozen qualification `20260715T033128Z-s3-stabilization01-qualification01` then passed Q1-Q9 from that clean candidate: full unittest 80/80; training 40/40; dev-10 20/20; acceptor A/B 180/180; adaptive A/B 120/120; selected controls 90/90; stress 36/36; gate `PASS`; and report exit 0. Q3 improved 7/10 60-second cases, retained a lower median than S2, and had zero prefix, longer-run, checker, assignment, or incumbent regression. The gate retained `alns=true`, acceptor `sa`, adaptive false, and dirty trigger `max(3,.03*n_b)`. The post-qualification diff from the candidate across `baseline/` is empty; S4, S5, and interlock remain disabled and independently gated.

Known risks and deferred decisions: time-based search can lose prefix consistency, repair may be too slow to produce accepted candidates, and SA/adaptation may add no value. Fake-clock prefix tests, structural counters, and the preregistered A/B rules decide these without relaxing safety. S4 entry requires S3 `COMPLETE`, clean status, gate exit 0, and selected control flags recorded.
