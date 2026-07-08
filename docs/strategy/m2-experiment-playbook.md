# M2 Experiment Playbook

Last updated: 2026-07-08

## Purpose

M2 exists to improve objective quality through low-cost, measured solver changes while preserving the M1 safety rule: never return an unvalidated, infeasible, crashed, or timed-out result for valid official challenge instances.

M2 experiments should stay small enough to implement, benchmark, and decide in one focused session. The goal is not to build the final search stack. The goal is to identify objective improvements that are cheap, explainable, and safe enough to become the next `m2-main` baseline.

## Base Branch and Worktree Policy

Use `m2-main` as the integration branch for M2.

Before starting an experiment:

1. Run `git status --short`.
2. Confirm `git branch --show-current` is `m2-main`.
3. If the current branch is not `m2-main`, check whether it points to the same commit as `m2-main`. If it does, record the alias in `experiment-log.md` before continuing. If it does not, do not discard changes; preserve them by committing, stashing with a descriptive message, or moving the experiment to a separate worktree.

Prefer one worktree per experiment:

```bash
git worktree add ../2026-m2-block-ordering -b codex/m2-block-ordering m2-main
```

If worktrees are awkward in the current environment, use a normal branch from a clean `m2-main` checkout. Do not run two experiment implementations in the same branch.

## Experiment Queue

Run M2 experiments in this default order unless new measurements justify reordering:

| Queue ID | Branch | Intent | Primary Signal |
|---|---|---|---|
| `block_ordering` | `codex/m2-block-ordering` | Try deterministic construction order variants and keep the best validated result. | `dev-10` total objective, especially `obj1` and `obj3`. |
| `time_slot_candidates` | `codex/m2-time-candidates` | Expand bounded time candidates around release, exit, and latest-safe boundaries. | Low-slack `dev-10` `obj1` improvement. |
| `left_shift_polish` | `codex/m2-left-shift` | Pull feasible placements earlier after construction without changing geometry. | `obj1` reduction with stable feasibility/runtime. |
| `placement_candidates` | `codex/m2-placement-candidates` | Add bounded placement anchors from walls, edges, or congestion-sensitive positions. | Dense-instance objective improvement without candidate explosion. |
| `single_block_reinsert` | `codex/m2-single-reinsert` | Remove and reinsert one problematic block at a time as a small polish pass. | Incremental `dev-10` improvement over the best accepted M2 baseline. |

Only one queue item should be active at a time. If an experiment depends on another accepted result, branch it from the updated `m2-main`, not from an older experiment branch.

## Benchmark Result Storage

Store machine-readable benchmark outputs under `experiments/results/`.

Use this naming pattern for flat result files:

```text
experiments/results/YYYY-MM-DD-m2-<queue-id>-<set-name>-<solver>-<timelimit>s.json
```

For M2 bundles with several related result files, prefer a queue-specific
subdirectory:

```text
experiments/results/m2/<queue-id>/YYYY-MM-DD-m2-<queue-id>-<set-name>-<solver>-<timelimit>s[-variant].json
```

Use `<queue-id>=alternative_screening` only for a measured screening bundle that
compares multiple already-available low-cost alternatives before promoting one
small integration change.

Examples:

```text
experiments/results/2026-07-06-m2-block_ordering-smoke-3-myalgorithm-15s.json
experiments/results/2026-07-06-m2-block_ordering-dev-10-myalgorithm-60s.json
experiments/results/m2/block_ordering/2026-07-07-m2-block_ordering-integration-dev-10-myalgorithm-60s.json
experiments/results/m2/alternative_screening/2026-07-08-m2-alt-screening-integration-dev-10-myalgorithm-60s-adaptive.json
```

If a rerun is needed on the same day with the same settings, append `-run2`, `-run3`, and so on before `.json`.

Every accepted or rejected experiment must have enough evidence in `docs/strategy/experiment-log.md` to find the relevant result file, branch, command, and objective/feasibility summary.

## Local Accept Criteria

Use the current `m2-main` result as the comparison baseline. At the start of M2, that baseline is `experiments/results/2026-07-06-dev10-baseline-60s.json` for `baseline_greedy`; when accepted changes land, create a fresh `m2-main` `myalgorithm` baseline before comparing later branches.

An experiment branch is locally acceptable only if all of these are true:

- `smoke-3` returns feasible Stage-5 rows for every instance with no crashes or missing objective fields.
- `dev-10` at 60s per instance returns feasible Stage-5 rows for every instance.
- Total `dev-10` objective is lower than the active `m2-main` comparison baseline, or a predefined subset improves clearly with no feasibility loss and no unexplained broad regression.
- Runtime remains inside the configured per-instance time limit, and any extra search has a bounded fallback to the validated incumbent.
- The changed behavior is isolated to the active queue item and does not mix multiple hypotheses.

For subset-based acceptance, define the subset before reading the final result. Examples include low-slack instances for `time_slot_candidates` or dense/max-layer instances for `placement_candidates`.

## Integration Accept Criteria

Before merging an accepted experiment into `m2-main`:

- Rebase or merge the experiment branch onto the latest `m2-main`.
- Rerun `smoke-3` and `dev-10` from the integration state, not only from the original experiment branch.
- Confirm every result is feasible and Stage 5.
- Compare objective totals and important component deltas (`obj1`, `obj2`, `obj3`) against the active `m2-main` baseline.
- Record the decision, evidence, branch, result file paths, and next baseline action in `experiment-log.md`.

After merge, create or update the active `m2-main` baseline result for future M2 comparisons. Run `daily-40` when an accepted change looks broad, touches shared construction logic, or before treating the result as generally valid.

## Decision States

Use these states consistently in `experiment-log.md` and `idea-bank.md`:

| State | Meaning | Required Evidence |
|---|---|---|
| `accepted` | The experiment met local and integration accept criteria and should become part of `m2-main`. | Result files, objective delta, feasibility summary, branch/commit reference, and any follow-up baseline action. |
| `rejected` | The experiment failed the stated hypothesis or introduced unacceptable feasibility, runtime, complexity, or objective regression. | Result files or command output summary plus the kill condition that fired. |
| `parked` | The idea is not disproven, but the current implementation or evidence is not good enough to merge now. | What blocked acceptance, what evidence is missing, and the condition for reopening. |

Do not leave an experiment in an implicit state. End every experiment session by marking it `accepted`, `rejected`, or `parked`.

## Required End-of-Session Updates

At the end of every M2 experiment session, update:

1. `docs/strategy/experiment-log.md` with the experiment, evidence, decision state, and next step.
2. `docs/strategy/idea-bank.md` if the hypothesis, status, kill condition, or priority changed.
3. `docs/strategy/operating-plan.md` if milestone status, benchmark policy, active baseline, or immediate next actions changed.
4. `docs/strategy/m2-experiment-playbook.md` if the queue, branch rules, accept criteria, or result-path convention changed.

Also keep generated benchmark artifacts in `experiments/results/` and avoid committing local challenge data, solver credentials, or large generated files.
