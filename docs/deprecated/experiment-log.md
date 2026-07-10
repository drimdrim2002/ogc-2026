# OGC 2026 Solver Experiment Log

Last updated: 2026-07-09

This file records decisions and experiment outcomes. Keep entries short, factual, and tied to measurements where possible.

## Decision Log

### 2026-07-06: M2 Experiment Playbook

Decision:

- Use `docs/strategy/m2-experiment-playbook.md` as the operating guide for M2 objective-improvement experiments.
- Run the default M2 queue as separate measured branches: `block_ordering`, `time_slot_candidates`, `left_shift_polish`, `placement_candidates`, and `single_block_reinsert`.
- Prefer one worktree per experiment; use a normal branch only when worktrees are awkward in the current environment.
- End each experiment as `accepted`, `rejected`, or `parked`, with benchmark result files stored under `experiments/results/`.

Rationale:

- M2 needs consistent branch, benchmark, and decision rules before objective-improvement implementations start.
- The playbook keeps `m2-main` as the integration baseline while preserving small, single-hypothesis experiment branches.

Open follow-up:

- Start the first M2 experiment on `codex/m2-block-ordering`.
- Record a fresh `m2-main` `myalgorithm` comparison baseline after the first accepted M2 merge.

### 2026-07-06: smoke-3 and Benchmark Metadata Baseline

Decision:

- Use `prob_21`, `prob_32`, and `prob_9` as the initial `smoke-3` set.
- Add benchmark result metadata fields: `git_commit`, `seed`, `solver`, and `set_name`.
- Keep `seed` metadata-only for now; current solver behavior is unchanged.
- Add `--set-name` support for `all`, `dev-10`, and `smoke-3`.

Evidence:

| Slot | Instance | Path | Role |
|---|---|---|---|
| Easy contrast | `prob_21` | `data/train/prob_21.json` | Higher-slack/easier `dev-10` case for quick runtime and metadata checks. |
| Medium | `prob_32` | `data/train/prob_32.json` | Mid-size max-layer case with high preference-penalty weight. |
| Dense/high-risk | `prob_9` | `data/train 2/prob_9.json` | Low-slack/high-zero-slack max-layer case for early dense failure detection. |

Validation:

- Added regression coverage for benchmark metadata and `smoke-3` set selection in `baseline/tests/test_phase0_harness.py`.
- Focused RED/GREEN check passed after implementation.

Open follow-up:

- `dev-10` 60s baseline is now recorded in `experiments/results/2026-07-06-dev10-baseline-60s.json`.
- Record current baseline objective and feasibility on `daily-40` at 60s per instance.

### 2026-07-06: dev-10 Representative Set Selected

Decision:

- Use these fixed 8 instances for `dev-10`: `prob_4`, `prob_8`, `prob_9`, `prob_13`, `prob_20`, `prob_21`, `prob_32`, `prob_36`.
- Use these initial rotating 2 instances: `prob_18`, `prob_40`.
- Keep the per-instance `dev-10` solver time limit at 60s.

Evidence:

| Slot | Instance | Blocks | Bays | Max layers | Slack avg | Zero slack | Weights `(w1,w2,w3)` | Role |
|---|---|---:|---:|---:|---:|---:|---|---|
| Fixed | `prob_4` | 100 | 2 | 2 | 1.20 | 0.350 | `(21918,7,200)` | Lowest average slack; 2-bay early-failure proxy. |
| Fixed | `prob_8` | 150 | 2 | 2 | 1.35 | 0.273 | `(10000,4,200)` | 150-block low-slack representative. |
| Fixed | `prob_9` | 200 | 3 | 4 | 1.25 | 0.325 | `(13333,5,150)` | Max-layer low-slack dense/high-risk case. |
| Fixed | `prob_13` | 250 | 4 | 2 | 1.28 | 0.324 | `(18605,5,133)` | Large 4-bay low-slack/high-zero case. |
| Fixed | `prob_20` | 300 | 5 | 2 | 1.57 | 0.227 | `(26667,6,125)` | Max-size and only 5-bay case. |
| Fixed | `prob_21` | 100 | 3 | 2 | 5.22 | 0.070 | `(13333,10,150)` | Higher-slack/easier high-load-balance contrast. |
| Fixed | `prob_32` | 200 | 3 | 4 | 2.41 | 0.220 | `(3333,5,600)` | Max-layer high-preference-weight contrast. |
| Fixed | `prob_36` | 250 | 4 | 2 | 2.30 | 0.200 | `(667,1,13)` | Large low-weight-profile contrast. |
| Rotating | `prob_18` | 300 | 4 | 2 | 1.37 | 0.283 | `(13333,4,133)` | Max-size 4-bay low-slack pressure case. |
| Rotating | `prob_40` | 250 | 4 | 4 | 4.93 | 0.108 | `(667,1,13)` | Max-layer low-weight-profile rotation case. |

Rationale:

- The fixed set covers all observed training `n_blocks` levels: 100, 150, 200, 250, and 300.
- The fixed set covers all observed `n_bays` counts: 2, 3, 4, and 5.
- It mixes low-slack/high-zero-slack pressure cases with higher-slack and unusual objective-weight profiles.
- Baseline-fragility has not been measured yet, so low slack plus high zero-slack ratio is used as the initial failure-early-detection proxy.

Open follow-up:

- Revisit rotating slots after the first few experiment bundles or after a `daily-40` run exposes under-covered failures.

### 2026-07-06: Benchmark Cadence

Decision:

- Use a mixed representative benchmark set.
- `dev-10` consists of fixed 8 instances plus rotating 2 instances.
- Each `dev-10` instance receives a 60s solver time limit.
- `daily-40` runs all 40 training instances about once per day.

Rationale:

- Running all 40 instances after every algorithm change is too slow.
- A fixed subset supports apples-to-apples comparisons.
- Rotating slots reduce overfitting to the fixed subset.

Open follow-up:

- Record baseline objective and feasibility on `daily-40`.

### 2026-07-06: Context Management

Decision:

- One session should own one experiment bundle.
- The global plan lives in `docs/strategy/operating-plan.md`.
- Ideas live in `docs/strategy/idea-bank.md`.
- Outcomes live in this log and machine-readable benchmark results.

Rationale:

- The 258k context window should be used for real implementation and analysis, not reloading large archived plans.
- Short operating documents keep future sessions aligned.

### 2026-07-06: Fable Material Handling

Decision:

- Treat Fable documents as an idea bank, not as execution contracts.
- M3 may borrow LNS/ALNS ideas after M2 evidence exists.
- M4 may borrow raster/C++ geometry ideas only after profiling evidence exists.

Rationale:

- Current repository state is much smaller than the Fable implementation plan assumes.
- Large components should be promoted only when smaller measured improvements are exhausted.

### 2026-07-09: M4/M5 Scope Clarification After M3

Decision:

- Rename the next performance milestone from narrow geometry acceleration to placement/geometry performance.
- Treat M4a as profiling plus cheap Python-level placement/candidate optimization before raster, numpy, or compiled geometry work.
- Keep raster/C++ geometry as M4b and require profiling plus parity evidence before adopting it.
- Keep M5 as submission hardening only; no new solver optimization should start in M5.

Rationale:

- M3 small LNS was safe but produced no strict full `daily-40` objective improvement against its preflight baseline.
- The likely next bottleneck is time spent in greedy placement, candidate-position search, or trusted geometry checks, which leaves little budget for useful search.
- Placing candidate optimization under M4a reduces ambiguity between old M2 constructor ideas and later geometry acceleration, while keeping M5 clean.

Open follow-up:

- Run a focused profiling slice against the accepted M3 baseline before expanding LNS operators or starting compiled/raster geometry work.

### 2026-07-09: M2-M4 Execution Plan Reset (Fast Constructor First)

Decision:

- Created `docs/strategy/m2-m4-execution-plan.md` as the execution contract for M2/M3/M4: M2 is rescoped to a time-aware fast constructor (Mode A union-disjoint placement, feasible-by-construction), M3 to a budget-driven LNS with exact targeted revalidation, M4 stays profiling-gated but is sequenced after M2 because the constructor rewrite changes the hotspot map.
- Recorded a verified evaluator-semantics contract in the plan (interlock truth table, Stage-5 list-order replay and Stage-2 same-time-entry semantics, float `obj2` without floor, exit-time freedom and its mode-local dominance scope, shapely-as-only-geometry-authority) with `file:line` anchors into `baseline/utils.py`.

Evidence:

- 300s diagnostic `experiments/results/m3/lns_diagnostic/2026-07-09-m3-lns-diagnostic-dev-10-myalgorithm-300s-small.json`: `phase1_elapsed` hits the 0.95 deadline on 8/10 instances while placing only 136-196 of 200-300 blocks; prob_4 finishes construction at 98.6s and improves from `172225678.79` (60s, unfinished) to `248906.00` (-99.86%); LNS entered on 2/10 with 0 accepted candidates (prob_21 repair candidates 25/25 infeasible).
- 60s daily-40 small-LNS vs adaptive preflight: `objective`/`obj1`/`obj2`/`obj3` identical on 40/40 rows; LNS entered 1/40 with under 1s remaining. The earlier "-7.5% dev-10" improvement attributed to small LNS is baseline run variance: the dev-10 small-LNS rows equal the daily-40 adaptive preflight rows for the same instances, and `accepted_candidate_count` is 0.
- Latent code defects recorded in plan §1.4: `_find_earliest_slot` checks only the new block's crane paths and never re-checks existing blocks whose entry/exit falls inside the new block's stay; `_build_operations` orders same-time EXITs by `block_id` while its docstring claims dependency order; `_candidate_positions` builds candidate grids from all blocks ever placed in the bay regardless of time overlap.
- Documentation fixes applied on 2026-07-09: removed the incorrect floor from the Z2 formula in `docs/fable/OGC2026_Problem_Analysis.md` and `docs/fable/OGC2026_문제분석.md` (section 2.7) and from `docs/fable/plan/2026-07-02-ogc2026-week1-core-decoder-hw.md:405`, `week2-alns.md:21`, `week2-alns.md:454` (code snippet), `week3-matheuristics.md:788`, `week3-matheuristics.md:2121`; reworded the misleading "same-type operation order is free" parenthetical in both problem-analysis docs (section 2.3).

Decision state: plan `accepted` as the active execution contract.

Next step:

- Implement M2-A (semantics test harness + instrumentation) and M2-B (`fast_v1` constructor behind `constructor_mode`) on `codex/m2-fast-constructor`, then gate on smoke-3 and dev-10 per plan §3.5.

### 2026-07-09: Clean-Slate Winning Solver Design Adopted

Decision:

- Created `docs/strategy/solver-design.md` from first principles using only `docs/ogc2026_problem_statement_analysis_en.md` and `baseline/utils.py`, at the user's request to ignore prior analysis. It supersedes `docs/strategy/m2-m4-execution-plan.md` as the design source of truth; that document's diagnosis (§1) and semantics contract (§2) remain valid references.
- Core design: exact two-level decomposition (bay assignment fully owns Z2/Z3 in closed form; per-bay scheduling owns Z1; geometry is a pure feasibility resource), a pair-feasibility trichotomy (time-separated with same-day handover legal / union-disjoint safe in any op order / interlock with a derived truth table and nested stay intervals), an always-valid incumbent protocol for zero minus-one risk, an insertion constructor, per-bay CP-SAT retiming over the union-overlap conflict graph (exact for fixed layouts), an LNS improvement loop, and a staged roadmap S0-S6 where every stage leaves a submittable solver.
- Scoring analysis drove the priorities: infeasible/timeout/crash scores -1 per instance, feasible scores by strict-better rank, and the hidden timelimit ranges from minutes to 30 minutes, so feasibility armor and anytime scaling outrank raw heuristic strength.

Next step:

- Implement stage S0 (contract tests, geometry kernel with verdict cache, canonical serializer, T0 fallback + incumbent/budget armor) per `solver-design.md` §5, then S1.

### 2026-07-09: Solver Implementation Spec Written

Decision:

- Created `docs/strategy/solver-implementation-plan.md` as the implementation-level companion to `solver-design.md`, covering: module/file layout with an undo-log `SolutionState`; geometry kernel with a translation-invariant verdict cache keyed by `(shape_key, shape_key, dx, dy)`; the insertion kernel shared by the constructor and all ALNS repairs; a full ALNS specification (6 destroy operators with exact selection formulas including a critical-chain destroy derived from binding `e_j == a_i` conflict pairs, 3 repair operators with all-or-nothing undo semantics, Ropke-Pisinger adaptive weights 100/(33,9,13)/0.1); simulated-annealing acceptance with data-calibrated `T0 = median(positive delta)/ln 2`, wall-clock geometric cooling, reheat-then-perturb stall ladder, and a separate always-validated incumbent so SA never touches the -1 armor; CP-SAT models in code form (per-bay retiming over union-overlap conflict pairs with `<=` separations enabling same-day handover, hints, 4 workers, never-worse guard; scaled-integer assignment model with range linearization and congestion soft caps); big-M MIP alternative documented and rejected (license/exception risk, weaker relaxation); canonical serializer, budget ladder for small timelimits, full parameter table, and a test-first task breakdown mapped to roadmap stages S0-S3.

Next step:

- Begin S0-1 (write contract-semantics tests RED) and S0-2 (kernel skeleton) per the implementation plan §9.

## Experiment Entries

### 2026-07-08: M3 Small Destroy-Repair LNS

Experiment:

- Added a bounded small destroy-repair LNS layer behind `lns_mode="small"` and activated it for the submission entry point after measured gates passed.
- Branch context: current branch `main`; activation commit `c40700912aa02d6617ec2f8d08453f159ec02db4` (`feat(submission): enable measured small lns`).
- The active submission baseline now combines adaptive block-order selection with `_SUBMISSION_LNS_MODE = "small"` while preserving the public `algorithm(prob_info, timelimit)` signature and final checker/fallback contract.
- Fable LNS/ALNS docs were used only as idea sources; this accepted slice stayed on the trusted Python geometry/checker path.

Evidence:

- Full 60s adaptive `daily-40` preflight baseline before activation: `experiments/results/m3/small_lns/2026-07-08-m3-small-lns-preflight-daily-40-myalgorithm-60s-adaptive-baseline.json`, 40/40 feasible Stage 5, total objective `25376460025.16775`, total `obj1` `2493592.0`, total `obj2` `344342.0895618068`, total `obj3` `4991.0`, artifact `git_commit` `4d03ba6`.
- Small-LNS smoke gate: `experiments/results/m3/small_lns/2026-07-08-m3-small-lns-smoke-3-myalgorithm-15s-small.json`, 3/3 feasible Stage 5, total objective `931619615.8035469`, total `obj1` `107813.0`, total `obj2` `13942.528504411242`, total `obj3` `780.0`, effective LNS mode `small`, artifact `git_commit` `6e6c454`.
- Small-LNS `dev-10` gate: `experiments/results/m3/small_lns/2026-07-08-m3-small-lns-dev-10-myalgorithm-60s-small.json`, 10/10 feasible Stage 5, total objective `3620223014.45645`, total `obj1` `764243.0`, total `obj2` `86326.84947915215`, total `obj3` `3479.0`, delta vs active adaptive M2 baseline `3914797993.4149823` was `-294574978.95853233` (`-7.5246533653596455%`), effective LNS mode `small`, artifact `git_commit` `6e6c454`.
- Small-LNS full 60s `daily-40` non-regression gate before activation: `experiments/results/m3/small_lns/2026-07-08-m3-small-lns-daily-40-myalgorithm-60s-small.json`, 40/40 feasible Stage 5, total objective `25376460025.16775`, total `obj1` `2493592.0`, total `obj2` `344342.0895618068`, total `obj3` `4991.0`, non-regression delta vs the full adaptive preflight baseline `0.0`, effective LNS mode `small`, artifact `git_commit` `6e6c454`.
- Todo 10 tiny-budget final smoke: `experiments/results/m3/small_lns/2026-07-08-m3-small-lns-smoke-3-myalgorithm-0.001s-final.json`, 3/3 feasible Stage 5, total objective `1503916861.6392648`, total `obj1` `165986.0`, total `obj2` `14886.212076201271`, total `obj3` `628.0`, effective LNS mode `small`, artifact `git_commit` `c407009`.

Decision:

- `accepted`.
- Use M3 small destroy-repair LNS as the active `myalgorithm` comparison baseline.
- Keep the full Fable ALNS portfolio, simulated annealing acceptance, operator weighting, CP-SAT retiming, and geometry acceleration deferred until separately justified by focused evidence.

Next step:

- Compare the next solver experiment against the accepted M3 small-LNS baseline and rerun `daily-40` before making any new broad generalization claim.

### 2026-07-08: M2 Adaptive Block-Order Selection

Experiment:

- Compared the accepted global `slack` submission baseline against a low-cost adaptive selector over already implemented block-order modes.
- Branch context: this run used the current `main` checkout; `main` and `m2-main` both pointed at `70ac6f7` before the local changes, so the branch-name mismatch was an alias of the same M2 base commit.
- Reused prior dev-10 single-mode artifacts for `release_edd`, `latest_safe_entry`, and `preference_pressure`, then screened a feature rule that selects among those modes per instance.
- Screened `repair_mode="simple"` on smoke-3 for both global `slack` and adaptive order selection; it produced no objective change, so it was rejected as a no-op for this bundle.
- Wired `baseline/myalgorithm.py` to select the measured block-order mode per instance while keeping the public `algorithm(prob_info, timelimit)` signature and fallback verification unchanged.
- Updated benchmark metadata so `--solver myalgorithm` records the effective per-instance block-order mode rather than the selector label.

Evidence:

- Active comparison baseline: `experiments/results/m2/block_ordering/2026-07-07-m2-block_ordering-integration-dev-10-myalgorithm-60s.json`, 10/10 feasible Stage 5, total objective `4260238079.234912`, total `obj1` `853345.0`, mode `slack`.
- Existing single-mode dev-10 artifacts showed an oracle best total `3620223014.4564495` across `preference_pressure`, `latest_safe_entry`, `release_edd`, and `slack`, versus global `slack` total `3965663100.276379` from the same mode batch.
- Smoke screen: `experiments/results/m2/alternative_screening/2026-07-08-m2-alt-screening-smoke-3-variants-15s.json`. Adaptive greedy repair returned 3/3 feasible Stage 5 with objective `931619615.8035469`; global `slack` returned `976921751.8035469`; simple repair matched greedy repair exactly for both order policies.
- Dev-10 adaptive screen: `experiments/results/m2/alternative_screening/2026-07-08-m2-alt-screening-dev-10-adaptive-greedy-60s.json`, 10/10 feasible Stage 5, total objective `3914797993.4149823`, total `obj1` `786353.0`, max elapsed `59.38332796096802`, delta vs active slack `-345440085.8199296` (`-8.108469043165929%`).
- Integration smoke through `myalgorithm`: `experiments/results/m2/alternative_screening/2026-07-08-m2-alt-screening-integration-smoke-3-myalgorithm-15s-adaptive.json`, 3/3 feasible Stage 5, objective `931619615.8035469`.
- Integration dev-10 through `myalgorithm`: `experiments/results/m2/alternative_screening/2026-07-08-m2-alt-screening-integration-dev-10-myalgorithm-60s-adaptive.json`, 10/10 feasible Stage 5, total objective `3914797993.4149823`, total `obj1` `786353.0`, total `obj2` `90451.74533243042`, total `obj3` `1753.0`, max elapsed `59.3257110118866`.
- Selected modes in the integration dev-10 run: `prob_4:preference_pressure`, `prob_8:latest_safe_entry`, `prob_9:slack`, `prob_13:release_edd`, `prob_20:slack`, `prob_21:slack`, `prob_32:release_edd`, `prob_36:latest_safe_entry`, `prob_18:release_edd`, `prob_40:slack`.
- Bounded daily-40 holdout through `myalgorithm`: `experiments/results/m2/alternative_screening/2026-07-08-m2-alt-screening-daily-40-myalgorithm-15s-adaptive.json`, 40/40 feasible Stage 5, total objective `26165605047.325424`, total `obj1` `2549737.0`, total `obj2` `350544.64663166535`, total `obj3` `3158.0`, max elapsed `16.576851844787598`. Mode distribution: `preference_pressure=5`, `latest_safe_entry=4`, `release_edd=7`, `slack=24`.
- RED/GREEN: `baseline.tests.test_submission_safety` first failed because the adaptive-case test observed `block_order_mode="slack"`; after implementation it passed. Added direct selector branch coverage for every adaptive rule and the explicit non-adaptive override path. Full suite passed: `/opt/homebrew/Caskroom/miniforge/base/envs/ogc-2026/bin/python -m unittest discover -s baseline/tests -p 'test_*.py'`, `Ran 31 tests`, `OK`.
- Manual submission CLI: `experiments/results/m2/alternative_screening/2026-07-08-m2-alt-screening-manual-run_myalgorithm-example_B2_b10-10s.txt`, reported `Feasible : True (stage=5)` and objective details.

Decision:

- `accepted`.
- Use adaptive block-order selection as the new active M2 `myalgorithm` comparison baseline.
- Keep expanded time-slot candidates, expanded placement candidates, left-shift polish, and single-block reinsert in the M2 queue, but compare them against the adaptive baseline rather than the old global `slack` baseline.

Next step:

- Run full 60s `daily-40` before treating adaptive selection as broadly validated beyond the bounded 15s holdout.

### 2026-07-06: M2 Block Ordering

Experiment:

- Branch: `codex/m2-block-ordering` from clean `m2-main`.
- Integration branch: fast-forwarded into `m2-main` at `ab1087d`.
- Added `baseline_greedy.greedyalgorithm(..., block_order_mode="edd")` with deterministic modes: `edd`, `release_edd`, `slack`, `latest_safe_entry`, and `preference_pressure`.
- Kept `baseline_greedy` default behavior compatible with the original EDD order.
- Added benchmark runner support for `--block-order-mode` and result metadata.
- Wired the submission entry point `myalgorithm.algorithm()` to the locally accepted single mode: `slack`.

Evidence:

- Unit tests: `conda run -n ogc2026 python -m unittest discover -s baseline/tests -p 'test_*.py'` passed: `Ran 28 tests`, `OK`.
- Existing comparison baseline: `experiments/results/2026-07-06-dev10-baseline-60s.json`, 10 feasible Stage-5 rows, total objective `5562716279.375933`.
- Mode result files are under `experiments/results/m2/block_ordering/`.
- Machine-readable summary: `experiments/results/m2/block_ordering/2026-07-06-m2-block_ordering-dev-10-summary.json`.
- Smoke check: `experiments/results/m2/block_ordering/2026-07-06-m2-block_ordering-smoke-3-baseline_greedy-15s-slack.json`, 3 feasible Stage-5 rows, total objective `976921751.8035469`.
- Submission entry check: `experiments/results/m2/block_ordering/2026-07-06-m2-block_ordering-dev-10-myalgorithm-60s-slack.json`, 10 feasible Stage-5 rows, total objective `3965663100.276379`.
- Integration compile check: `conda run -n ogc2026 python -m py_compile baseline/baseline_greedy.py baseline/myalgorithm.py baseline/benchmark_instances.py baseline/tests/test_phase0_harness.py baseline/tests/test_submission_safety.py` exited 0 on `m2-main`.
- Integration unit tests: `conda run -n ogc2026 python -m unittest discover -s baseline/tests -p 'test_*.py'` passed on `m2-main`: `Ran 28 tests`, `OK`.
- Integration smoke check: `experiments/results/m2/block_ordering/2026-07-07-m2-block_ordering-integration-smoke-3-myalgorithm-15s.json`, 3 feasible Stage-5 rows, total objective `976921751.8035469`, mode `slack`.
- Integration dev-10 check: `experiments/results/m2/block_ordering/2026-07-07-m2-block_ordering-integration-dev-10-myalgorithm-60s.json`, 10 feasible Stage-5 rows, total objective `4260238079.234912`, total `obj1` `853345.0`, max elapsed `59.105170011520386`, mode `slack`.
- Integration summary: `experiments/results/m2/block_ordering/2026-07-07-m2-block_ordering-integration-summary.json`.
- Integration delta vs active comparison baseline: objective `-1302478200.1410208` (`-23.41%`), `obj1` `-127345.0`, `obj2` `-3749.880449863369`, `obj3` `+400.0`.

Dev-10 mode results at 60s per instance:

| Mode | Feasible Stage-5 | Objective total | Delta vs `edd` | Delta % |
|---|---:|---:|---:|---:|
| `edd` | 10/10 | `5562716279.375933` | `0.0` | `0.00%` |
| `release_edd` | 10/10 | `4506648648.397401` | `-1056067630.9785318` | `-18.98%` |
| `slack` | 10/10 | `3965663100.276379` | `-1597053179.0995536` | `-28.71%` |
| `latest_safe_entry` | 10/10 | `5124021360.36705` | `-438694919.0088825` | `-7.89%` |
| `preference_pressure` | 10/10 | `4622033473.284263` | `-940682806.09167` | `-16.91%` |

Decision:

- `accepted` locally and integration accepted on `m2-main`.
- Select `slack` as the M2 block-ordering mode because it has the best local dev-10 objective, preserves 10/10 feasibility and Stage 5, passes integration smoke-3 and dev-10, and is verified through the `myalgorithm` submission entry point.
- `release_edd`, `latest_safe_entry`, and `preference_pressure` are measured but not selected for this branch.

Next step:

- Use `experiments/results/m2/block_ordering/2026-07-07-m2-block_ordering-integration-dev-10-myalgorithm-60s.json` as the active `m2-main` `myalgorithm` comparison baseline for the next M2 experiment.
- Run `daily-40` before treating the improvement as broadly valid beyond the `dev-10` gate.

### 2026-07-06: M0/M1 Gate Cleanup Before M2

Experiment:

- Converted `baseline/benchmark_instances.py --solver` from metadata-only labeling to executable solver selection.
- Preserved `--run-baseline` as a compatibility alias for `--solver baseline_greedy`.
- Added `--solver stats_only` behavior for statistics-only output and a `daily-40` set alias for all 40 training instances.
- Aligned `smoke-3` code order with the documented easy -> medium -> dense order: `prob_21`, `prob_32`, `prob_9`.
- Updated M1 status documentation from open implementation plan to completed safety record.

Evidence:

- Added regression tests proving `--solver myalgorithm` calls `myalgorithm.algorithm()` and that solver labels cannot request an unexecuted solver.
- `conda run -n ogc2026 python -m unittest discover -s baseline/tests -p 'test_*.py'` passed: `Ran 21 tests`, `OK`.
- `conda run -n ogc2026 python -m py_compile baseline/myalgorithm.py baseline/baseline_greedy.py baseline/benchmark_instances.py baseline/utils.py` exited 0.
- `conda run -n ogc2026 python baseline/benchmark_instances.py --root . --set-name smoke-3 --solver stats_only --format json` returned `prob_21`, `prob_32`, `prob_9` in that order and no feasibility fields.
- `conda run -n ogc2026 python baseline/benchmark_instances.py --root . --set-name smoke-3 --solver myalgorithm --timelimit 0.001 --format json` returned three feasible Stage-5 rows with `solver: myalgorithm`.
- `conda run -n ogc2026 python baseline/benchmark_instances.py --root . --set-name dev-10 --solver baseline_greedy --limit 1 --timelimit 0.001 --format json` returned one feasible Stage-5 row with `solver: baseline_greedy`.
- `conda run -n ogc2026 python baseline/benchmark_instances.py --root . --set-name dev-10 --solver baseline_greedy --timelimit 60 --format json > experiments/results/2026-07-06-dev10-baseline-60s.json` completed successfully.
- Saved result file validation: 10 rows, all `solver: baseline_greedy`, all feasible, all Stage 5, objective sum `5562716279.375933`, solver elapsed sum `574.5575788021088`.

Decision:

- M0 is now M2-ready for `dev-10` comparisons: the runner executes the selected solver and the 60s baseline artifact is recorded.
- M1 entry-point safety remains accepted; no solver objective-improvement code was changed.
- `daily-40` can now run by set name but the full 60s baseline artifact is still a next action.

### 2026-07-06: M1 Submission Safety Entry Point Guard

Experiment:

- Hardened `baseline/myalgorithm.py::algorithm()` as a safety shell around `baseline_greedy.greedyalgorithm()`.
- Added focused unittest coverage in `baseline/tests/test_submission_safety.py`.

Evidence:

- `conda run -n ogc2026 python -m unittest baseline.tests.test_submission_safety` passed.
- `conda run -n ogc2026 python -m unittest discover -s baseline/tests -p 'test_*.py'` passed.
- `conda run -n ogc2026 python baseline/run_myalgorithm.py ../alg_tester/example/example_B2_b10.json --timelimit 0` returned `Feasible : True (stage=5)`.
- `conda run -n ogc2026 python baseline/run_myalgorithm.py ../alg_tester/example/example_B2_b10.json --timelimit 0.001` returned `Feasible : True (stage=5)`.
- `conda run -n ogc2026 python baseline/benchmark_instances.py --root . --limit 1 --run-baseline --timelimit 0.001` returned feasible `true` and stage `5`.

Decision:

- M1 entry point safety guard is accepted for valid official challenge instances.
- Malformed or impossible `prob_info` objects may fail clearly, but `algorithm()` must not silently return unvalidated output.

### 2026-07-06: M1 Submission Safety Analysis Plan

Experiment:

- Documentation-only safety analysis for M1.
- Created `docs/strategy/m1-submission-safety-plan.md`.
- No solver code changes were made.

Evidence:

- `conda run -n ogc2026 python -m unittest discover -s baseline/tests -p 'test_*.py'` passed: 10 tests OK.
- `conda run -n ogc2026 python baseline/run_myalgorithm.py ../alg_tester/example/example_B2_b10.json --timelimit 0` returned `Feasible : True (stage=5)`.
- `conda run -n ogc2026 python baseline/run_myalgorithm.py ../alg_tester/example/example_B2_b10.json --timelimit 0.001` returned `Feasible : True (stage=5)`.
- A monkeypatch probe confirmed `myalgorithm.algorithm()` currently propagates delegated solver exceptions.

Decision:

- Treat `baseline/myalgorithm.py` as the M1 hardening boundary.
- Next implementation should wrap the delegated greedy call, revalidate returned solutions, and fall back to verified serial output on exception, malformed output, infeasible output, or deadline fallback failure.
- Do not return unvalidated fallback output.
- Scope the "always feasible" guarantee to valid official challenge instances only. Malformed or impossible `prob_info` objects should fail clearly and must not produce unvalidated output.

## Entry Template

```text
Date:
Experiment:
Hypothesis:
Changed files:
Command:
Result summary:
Decision:
Next step:
```
