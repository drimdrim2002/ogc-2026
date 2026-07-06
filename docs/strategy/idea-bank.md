# OGC 2026 Solver Idea Bank

Last updated: 2026-07-07

This file stores solver ideas as hypotheses. It is not an implementation plan. Promote an idea only after a benchmark or profiling result justifies it.

## Entry Template

```text
Idea:
Source:
Hypothesis:
Expected impact:
Risks:
Minimum implementation:
Validation:
Kill condition:
Status:
```

## Active Candidates

### Deterministic Block Ordering

Source: current baseline structure and earlier Greedy + LNS notes.

Hypothesis: The greedy constructor is sensitive to block order. A single stronger deterministic ordering can reduce tardiness and preference penalty while preserving feasibility.

Expected impact: Medium on `obj1`, low to medium on `obj3`.

Risks: A mode that helps `dev-10` may overfit the representative set or regress broader `daily-40` behavior.

Minimum implementation: Add optional block sort keys behind `block_order_mode`, preserve `edd` as the default for existing `baseline_greedy` callers, and select one measured mode in `myalgorithm`.

Validation: Compare each mode against current baseline on `dev-10` at 60s per instance.

Kill condition: No `dev-10` improvement, feasibility loss, or repeated runtime regressions.

Status: accepted and integrated for M2 on `m2-main`; `slack` selected. Local evidence: `experiments/results/m2/block_ordering/2026-07-06-m2-block_ordering-dev-10-summary.json`. Integration evidence: `experiments/results/m2/block_ordering/2026-07-07-m2-block_ordering-integration-summary.json`.

### Expanded Candidate Positions

Source: baseline candidate generator and Fable packing observations.

Hypothesis: More candidate anchors from block edges, bay walls, and possibly due-date congestion windows can improve packing without full geometry acceleration.

Expected impact: Medium on dense instances.

Risks: Candidate explosion and slower greedy placement.

Minimum implementation: Add bounded candidate sources behind a configuration flag.

Validation: Track runtime and objective deltas on dense `dev-10` instances.

Kill condition: Runtime increase without objective gain.

Status: candidate for M2.

### Expanded Time-Slot Candidates

Source: baseline `_find_earliest_slot` behavior.

Hypothesis: Trying release time plus relevant exit boundaries, latest-safe-entry, and nearby shifted days can reduce unnecessary tardiness.

Expected impact: Medium on scheduling-heavy instances.

Risks: Larger search space.

Minimum implementation: Add a bounded time candidate generator and measure per-instance cost.

Validation: Compare `obj1` changes on low-slack `dev-10` instances.

Kill condition: No `obj1` improvement under 60s.

Status: candidate for M2.

### Left-Shift Polish

Source: Fable local move suggestion and scheduling intuition.

Hypothesis: After constructing a feasible solution, pulling entries earlier where feasible can reduce tardiness without changing bay, orientation, or position.

Expected impact: Medium to high on `obj1` if baseline leaves avoidable delays.

Risks: Rechecking feasibility may be expensive; naive ordering can create regressions.

Minimum implementation: Iterate tardy blocks in priority order and accept only validated earlier placements.

Validation: `dev-10` objective and `obj1` delta.

Kill condition: Too slow or no measurable `obj1` reduction.

Status: candidate for M2.

### Small Destroy-Repair LNS

Source: earlier Greedy + LNS spec and Fable ALNS plan.

Hypothesis: Removing a few problematic blocks and reinserting them with greedy repair can improve objective while keeping the incumbent safe.

Expected impact: Medium after M2 constructor improvements.

Risks: Repair failure frequency, slow feasibility checks, accidental incumbent mutation.

Minimum implementation: One or two destroy operators, greedy repair, accept only feasible improvements.

Validation: `dev-10` improvement over the best M2 solver; no feasibility regressions.

Kill condition: No improvement after bounded iterations or unstable runtime.

Status: gated for M3.

### Full ALNS Operator Portfolio

Source: Fable week 2 ALNS plan.

Hypothesis: A larger destroy-repair portfolio with acceptance criteria can outperform small LNS after the constructor is stable.

Expected impact: High if M3 small LNS shows promise.

Risks: Large implementation surface, tuning burden, context overhead.

Minimum implementation: Promote only the destroy/repair operators that small experiments justify.

Validation: Improvement over small LNS on `dev-10` and `daily-40`.

Kill condition: Complexity without measured gains.

Status: idea-bank only.

### CP-SAT Retiming

Source: Fable matheuristics plan.

Hypothesis: Fixing placements and optimizing entry dates can reduce tardiness after spatial decisions are stable.

Expected impact: Unknown; likely useful only after a strong constructor exists.

Risks: Modeling effort, time budget pressure, weak benefit if geometry is the main bottleneck.

Minimum implementation: Prototype on a tiny subset or one bay before solver integration.

Validation: Controlled comparison on instances where `obj1` remains high after M2/M3.

Kill condition: No clear `obj1` gain within budget.

Status: idea-bank only.

### Raster or C++ Geometry Core

Source: Fable geometry acceleration plans.

Hypothesis: Geometry checks dominate runtime enough that a raster or compiled core unlocks broader search.

Expected impact: Potentially high, but only if profiling proves geometry is the bottleneck.

Risks: Semantic drift from `utils.py`, ABI issues, memory growth, large implementation cost.

Minimum implementation: Profiling report first; then a parity-tested Python/numpy prototype before C++.

Validation: Verdict parity against `utils.py` plus end-to-end runtime improvement.

Kill condition: Profiling does not justify it or parity is unreliable.

Status: gated for M4.

### Interlock Densifier

Source: Fable interlocking analysis.

Hypothesis: Controlled layer overhangs can improve dense-instance packing if exit precedence remains acyclic and operation ordering is correct.

Expected impact: Potentially high on dense instances.

Risks: High feasibility risk, difficult operation ordering, hidden checker regressions.

Minimum implementation: Offline proposal pass behind a disabled-by-default flag.

Validation: Exact `utils.check_feasibility` and stage-5 operation replay tests.

Kill condition: Any feasibility instability.

Status: idea-bank only.

## Rejected Ideas

No rejected ideas recorded yet.
