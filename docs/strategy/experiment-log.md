# OGC 2026 Solver Experiment Log

Last updated: 2026-07-07

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

## Experiment Entries

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
