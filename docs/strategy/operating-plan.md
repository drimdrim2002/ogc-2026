# OGC 2026 Solver Operating Plan

Last updated: 2026-07-08

## Purpose

This document is the compact source of truth for day-to-day solver work. It keeps the project aligned without reloading the large Fable plans every session.

The goal is to build a submission solver that never returns infeasible, timed-out, or crashed results, then improves objective quality through measured experiments.

## Operating Model

- One session should own one experiment bundle: implement, measure, decide keep or revert.
- The repository documents own the global plan. Chat context is temporary working memory.
- `utils.check_feasibility` is the final oracle for every solver output.
- Any algorithmic component must preserve a validated incumbent or fall back to a validated lower tier.
- Fable documents are idea sources, not execution contracts.

## Benchmark Policy

Use three benchmark tiers:

| Tier | Purpose | Instance Set | Time Limit |
|---|---|---:|---:|
| `smoke` | Catch obvious breakage quickly | 2-3 instances | 5-15s each |
| `dev-10` | Main improvement loop | fixed 8 + rotating 2 | 60s each |
| `daily-40` | Regression and generalization check | all 40 training instances via `--set-name daily-40` | 60s each |

`dev-10` is the normal comparison gate for algorithm changes. `daily-40` should run about once per day or before treating a result as broadly valid.

Representative set policy:

- Fixed 8 instances should cover easy scheduling-heavy cases, medium cases, dense/high-risk cases, max-size cases, and known baseline-fragile cases.
- Rotating 2 instances should change periodically to reduce overfitting to the fixed set.
- The actual instance IDs must be selected from measured instance statistics, not guessed from plan documents.

Current `dev-10` representative set:

| Slot | Instance | Path | Blocks | Bays | Max layers | Slack avg | Zero slack | Weights `(w1,w2,w3)` | Selection role |
|---|---|---|---:|---:|---:|---:|---:|---|---|
| Fixed | `prob_4` | `data/train 2/prob_4.json` | 100 | 2 | 2 | 1.20 | 0.350 | `(21918,7,200)` | Lowest average slack; 2-bay dense early-failure proxy. |
| Fixed | `prob_8` | `data/train 2/prob_8.json` | 150 | 2 | 2 | 1.35 | 0.273 | `(10000,4,200)` | 150-block low-slack representative with 2 bays. |
| Fixed | `prob_9` | `data/train 2/prob_9.json` | 200 | 3 | 4 | 1.25 | 0.325 | `(13333,5,150)` | Max-layer, low-slack dense/high-risk case. |
| Fixed | `prob_13` | `data/train 2/prob_13.json` | 250 | 4 | 2 | 1.28 | 0.324 | `(18605,5,133)` | Large 4-bay low-slack/high-zero-slack case. |
| Fixed | `prob_20` | `data/train 2/prob_20.json` | 300 | 5 | 2 | 1.57 | 0.227 | `(26667,6,125)` | Max-size case and only 5-bay representative. |
| Fixed | `prob_21` | `data/train/prob_21.json` | 100 | 3 | 2 | 5.22 | 0.070 | `(13333,10,150)` | Higher-slack/easier contrast with high load-balance weight. |
| Fixed | `prob_32` | `data/train/prob_32.json` | 200 | 3 | 4 | 2.41 | 0.220 | `(3333,5,600)` | Max-layer case with high preference-penalty weight. |
| Fixed | `prob_36` | `data/train/prob_36.json` | 250 | 4 | 2 | 2.30 | 0.200 | `(667,1,13)` | Large low-weight-profile contrast. |
| Rotating | `prob_18` | `data/train 2/prob_18.json` | 300 | 4 | 2 | 1.37 | 0.283 | `(13333,4,133)` | Initial rotation: max-size 4-bay low-slack pressure case. |
| Rotating | `prob_40` | `data/train/prob_40.json` | 250 | 4 | 4 | 4.93 | 0.108 | `(667,1,13)` | Initial rotation: max-layer low-weight-profile case. |

Selection notes:

- The fixed set covers all observed `n_blocks` levels in the 40 training instances: 100, 150, 200, 250, and 300.
- The fixed set covers all observed `n_bays` counts: 2, 3, 4, and 5.
- `prob_9` and `prob_32` keep max-layer geometry in the always-run set; `prob_40` adds another max-layer case through rotation.
- Until measured baseline-fragility data exists, low average slack plus high zero-slack ratio is the failure-early-detection proxy.
- Rotation should prioritize cases that are under-covered by recent experiments, especially other 300-block cases, high `w3` profiles, and max-layer instances.

Current `smoke-3` set:

| Slot | Instance | Path | Blocks | Bays | Max layers | Slack avg | Zero slack | Selection role |
|---|---|---|---:|---:|---:|---:|---:|---|
| Easy contrast | `prob_21` | `data/train/prob_21.json` | 100 | 3 | 2 | 5.22 | 0.070 | Higher-slack/easier case from `dev-10`; catches gross runtime and metadata regressions without stressing dense packing first. |
| Medium | `prob_32` | `data/train/prob_32.json` | 200 | 3 | 4 | 2.41 | 0.220 | Mid-size max-layer case with high preference-penalty weight; exercises geometry and objective reporting beyond the easy case. |
| Dense/high-risk | `prob_9` | `data/train 2/prob_9.json` | 200 | 3 | 4 | 1.25 | 0.325 | Low-slack, high-zero-slack max-layer case; fast proxy for dense/high-risk failures. |

`smoke-3` is a breakage check, not an improvement gate. Use it after larger code changes to confirm feasibility, runtime, and result metadata still work before spending time on `dev-10`.

## Current Milestones

### M0: Measurement Backbone

Status: M2-ready measurement backbone; `daily-40` full baseline artifact remains the next regression baseline action.

Exit criteria:

- `dev-10` fixed/rotating policy documented with concrete instance IDs.
- Benchmark runner records objective, `obj1`, `obj2`, `obj3`, feasibility, stage, runtime, seed, and commit hash.
- Current baseline behavior is measured on `dev-10` in `experiments/results/2026-07-06-dev10-baseline-60s.json`.
- `daily-40` can run from one command and emit machine-readable results with `baseline/benchmark_instances.py --set-name daily-40`.
- Solver selection is executable: `--solver baseline_greedy` runs `baseline_greedy.greedyalgorithm()`, `--solver myalgorithm` runs `myalgorithm.algorithm()`, and `--solver stats_only` runs no solver.

### M1: Submission Safety

Status: complete for valid official challenge instances.

Exit criteria:

- `algorithm(prob_info, timelimit)` always returns a validated fallback on exception or budget exhaustion.
- No unvalidated solution is returned.
- Tiny time limits still return feasible output.
- Existing baseline tests remain green.

### M2: Low-Cost Objective Improvements

Status: active with adaptive block-order selection accepted as the current `m2-main` comparison baseline.

Candidate experiments:

- `block_ordering` on branch `codex/m2-block-ordering` -- accepted and integrated with `slack`.
- `adaptive_block_order_selection` -- accepted; active `m2-main` `myalgorithm` baseline is `experiments/results/m2/alternative_screening/2026-07-08-m2-alt-screening-integration-dev-10-myalgorithm-60s-adaptive.json`; bounded daily-40 holdout is `experiments/results/m2/alternative_screening/2026-07-08-m2-alt-screening-daily-40-myalgorithm-15s-adaptive.json`.
- `time_slot_candidates` on branch `codex/m2-time-candidates`.
- `left_shift_polish` on branch `codex/m2-left-shift`.
- `placement_candidates` on branch `codex/m2-placement-candidates`.
- `single_block_reinsert` on branch `codex/m2-single-reinsert`.

Exit criteria:

- Each kept experiment improves `dev-10` or a clearly defined subset without hurting feasibility.
- Rejected experiments have a recorded reason in `experiment-log.md`.
- Every experiment ends as `accepted`, `rejected`, or `parked` with result artifacts under `experiments/results/`.

### M3: LNS / ALNS Layer

Status: idea-bank gated.

Use Fable LNS/ALNS material only after M2 establishes a stable incumbent and benchmark harness. Start with small destroy-repair LNS, not the full Fable ALNS plan.

Exit criteria:

- Every iteration operates on a copy or snapshot.
- Failed repairs discard the candidate and keep the incumbent.
- Accepted candidates are feasible and no worse according to the configured acceptance rule.

### M4: Geometry Acceleration

Status: profiling gated.

Use Fable raster/C++ ideas only after profiling proves geometry checks dominate runtime and cheaper Python-level fixes are insufficient.

Exit criteria:

- A parity harness compares any accelerated geometry verdict against `utils.py`.
- Any accelerated path has a pure Python fallback.
- A failed acceleration import cannot affect correctness.

### M5: Submission Hardening

Status: later.

Exit criteria:

- Clean zip layout with root-level `myalgorithm.py`.
- No credentials, generated large artifacts, or local data in submission.
- Deterministic seed policy documented.
- Last known good package preserved.

## Context Discipline

At the start of a session, read:

1. This file.
2. `docs/strategy/experiment-log.md`.
3. The latest relevant benchmark result.
4. Only the code files needed for the active experiment.

At the end of a session, update:

1. The experiment log with decision and evidence.
2. The idea bank if a hypothesis is added, promoted, or rejected.
3. This file only if milestone state or benchmark policy changed.

## Immediate Next Actions

1. Use the accepted adaptive block-order selection result as the active `m2-main` `myalgorithm` `dev-10` comparison baseline for later M2 experiments.
2. Run full 60s `daily-40` before making broad claims beyond the bounded 15s holdout.
3. Continue M2 one experiment branch at a time against the adaptive baseline; do not mix multiple hypotheses in one merge.
4. Follow `docs/strategy/m2-experiment-playbook.md` for branch/worktree, benchmark result, and accept/reject rules.
5. Use `--solver myalgorithm` when measuring the submission entry point, and
   `--solver baseline_greedy` when measuring the reference baseline.
