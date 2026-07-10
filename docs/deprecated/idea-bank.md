# OGC 2026 Solver Idea Bank

Last updated: 2026-07-09

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

### Time-Aware Union-Disjoint Fast Constructor

Source: 2026-07-09 diagnostic and `docs/strategy/m2-m4-execution-plan.md` §2.5/§3.

Hypothesis: Generating position candidates only from the time-overlap set, testing one cached union-footprint polygon intersection per co-present block (with AABB prefilter), and first-fit bottom-left scanning makes construction feasible-by-construction in seconds, eliminating the crane-path checks and the one-sided-sweep defect class entirely.

Expected impact: Very high on `obj1` for 150+ block instances (currently serial-fallback dominated) and unlocks the entire LNS budget.

Risks: Union-disjointness forbids layer staggering, which may cost density on max-layer instances; mitigated by the gated Mode B stagger fallback.

Minimum implementation: `constructor_mode="fast_v1"` behind a flag with legacy A/B, per plan §3.2.

Validation: plan gates G-M2-1/2/3 (`phase1_elapsed <= 5s`, `greedy_placed_count == n_blocks`, objective vs active baseline).

Kill condition: Cannot meet the runtime gate without objective regression on dev-10.

Status: promoted to core — clean-slate design (`solver-design.md` §2.4-2.5, §4.3) and `solver-implementation-plan.md` §3 (stage S1).

### Pair-Offset Verdict Cache

Source: Fable NFP bitmap idea, restated policy-compliant in plan §5.1.

Hypothesis: Because positions are integers and shapes are fixed per (block, orientation), caching the shapely verdict keyed by (shapeA, orientA, shapeB, orientB, dx, dy) is exact storage of the oracle's answer, not a second geometry implementation, and hit rates in LNS are high.

Expected impact: Medium to high on LNS iteration throughput.

Risks: Memory growth (needs LRU cap); low hit rate during pure construction.

Minimum implementation: Lazy dict cache around the union-disjointness test, hit/miss counters in stats.

Validation: M4a profiling shows the cache removes a ranked hotspot; objective unchanged.

Kill condition: Hit rate too low to change the profile.

Status: promoted to core geometry kernel — `solver-design.md` §4.2, `solver-implementation-plan.md` §2.2 (stage S0).

### Exit-Extension Unblock Move

Source: Evaluator timing semantics (`exit - entry >= processing_time` only) and plan §2.2/§4.3.

Hypothesis: Delaying a blocked block's exit until after its blocker leaves is a cheap feasibility lever, free in objective terms whenever the new exit stays at or before the due date, and cheaper than spatial relocation.

Expected impact: Medium on repair success rate and `obj1` in congested bays; prerequisite for hosting interlock guests in Mode B.

Risks: Extending stays increases co-presence and can crowd later placements.

Minimum implementation: An M3 retime operator using targeted revalidation only.

Validation: A/B on dev-10 low-slack instances.

Kill condition: No repair-success or objective gain.

Status: S-track lever — repair/unblock use from stage S3 (`solver-design.md` §4.5); interlock host extension in `solver-implementation-plan.md` (interlock.py, stage S6).

### Deterministic Block Ordering

Source: current baseline structure and earlier Greedy + LNS notes.

Hypothesis: The greedy constructor is sensitive to block order. A single stronger deterministic ordering can reduce tardiness and preference penalty while preserving feasibility.

Expected impact: Medium on `obj1`, low to medium on `obj3`.

Risks: A mode that helps `dev-10` may overfit the representative set or regress broader `daily-40` behavior.

Minimum implementation: Add optional block sort keys behind `block_order_mode`, preserve `edd` as the default for existing `baseline_greedy` callers, and select one measured mode in `myalgorithm`.

Validation: Compare each mode against current baseline on `dev-10` at 60s per instance.

Kill condition: No `dev-10` improvement, feasibility loss, or repeated runtime regressions.

Status: accepted and integrated for M2 on `m2-main`; `slack` was the first selected global mode, then adaptive per-instance selection among existing modes was accepted. Local evidence: `experiments/results/m2/block_ordering/2026-07-06-m2-block_ordering-dev-10-summary.json`. Slack integration evidence: `experiments/results/m2/block_ordering/2026-07-07-m2-block_ordering-integration-summary.json`. Adaptive integration evidence: `experiments/results/m2/alternative_screening/2026-07-08-m2-alt-screening-integration-dev-10-myalgorithm-60s-adaptive.json`; bounded daily-40 holdout evidence: `experiments/results/m2/alternative_screening/2026-07-08-m2-alt-screening-daily-40-myalgorithm-15s-adaptive.json`.

### Expanded Candidate Positions

Source: baseline candidate generator and Fable packing observations.

Hypothesis: Better bounded candidate anchors, ordering, pruning, or caching can improve packing or reduce wasted placement checks without full geometry acceleration.

Expected impact: Medium on dense instances.

Risks: Candidate explosion and slower greedy placement.

Minimum implementation: Profile `_candidate_positions` and `_place_blocks` first; then add only bounded candidate sources, ordering rules, early cutoffs, or cache layers behind a configuration flag.

Validation: Track runtime, candidate count, LNS time/iteration budget, and objective deltas on dense `dev-10` instances.

Kill condition: Runtime increase without objective gain.

Status: subsumed by the clean-slate insertion kernel — anchor generation and AABB batch filtering in `solver-implementation-plan.md` §2.3, §3.2 (stage S1).

### Expanded Time-Slot Candidates

Source: baseline `_find_earliest_slot` behavior.

Hypothesis: Trying release time plus relevant exit boundaries, latest-safe-entry, and nearby shifted days can reduce unnecessary tardiness.

Expected impact: Medium on scheduling-heavy instances.

Risks: Larger search space.

Minimum implementation: Add a bounded time candidate generator and measure per-instance cost.

Validation: Compare `obj1` changes on low-slack `dev-10` instances.

Kill condition: No `obj1` improvement under 60s.

Status: subsumed by the insertion kernel's complete event-boundary time-candidate set, including the `a_j − dwell` window class (`solver-implementation-plan.md` §3.2; stage S1).

### Left-Shift Polish

Source: Fable local move suggestion and scheduling intuition.

Hypothesis: After constructing a feasible solution, pulling entries earlier where feasible can reduce tardiness without changing bay, orientation, or position.

Expected impact: Medium to high on `obj1` if baseline leaves avoidable delays.

Risks: Rechecking feasibility may be expensive; naive ordering can create regressions.

Minimum implementation: Iterate tardy blocks in priority order and accept only validated earlier placements.

Validation: `dev-10` objective and `obj1` delta.

Kill condition: Too slow or no measurable `obj1` reduction.

Status: subsumed and dominated by per-bay CP-SAT retiming (`solver-design.md` §4.4, stage S2), which computes the layout-optimal schedule directly; no standalone left-shift slice.

### Small Destroy-Repair LNS

Source: earlier Greedy + LNS spec and Fable ALNS plan.

Hypothesis: Removing a few problematic blocks and reinserting them with greedy repair can improve objective while keeping the incumbent safe.

Expected impact: Medium after M2 constructor improvements.

Risks: Repair failure frequency, slow feasibility checks, accidental incumbent mutation.

Minimum implementation: One or two destroy operators, greedy repair, accept only feasible improvements.

Validation: `dev-10` improvement over the best M2 solver; no feasibility regressions.

Kill condition: No improvement after bounded iterations or unstable runtime.

Status: accepted and integrated for M3. Evidence: `experiments/results/m3/small_lns/2026-07-08-m3-small-lns-dev-10-myalgorithm-60s-small.json` improved the active adaptive M2 `dev-10` baseline from `3914797993.4149823` to `3620223014.45645` with 10/10 feasible Stage 5 rows; `experiments/results/m3/small_lns/2026-07-08-m3-small-lns-daily-40-myalgorithm-60s-small.json` matched the full 60s adaptive `daily-40` preflight baseline at `25376460025.16775` with 40/40 feasible Stage 5 rows. Activation commit: `c40700912aa02d6617ec2f8d08453f159ec02db4`.

2026-07-09 reinterpretation: the 300s diagnostic showed the dev-10 delta above was baseline run variance, not an LNS effect — LNS entered on 1/40 daily-40 instances at 60s with 0 accepted candidates, and the dev-10 small-LNS rows equal the adaptive preflight rows. The mechanism stays integrated in the legacy solver; effectiveness work moves to the ALNS specification in `docs/strategy/solver-implementation-plan.md` §4 (stage S3).

### Full ALNS Operator Portfolio

Source: Fable week 2 ALNS plan.

Hypothesis: A larger destroy-repair portfolio with acceptance criteria can outperform small LNS after the constructor is stable.

Expected impact: High if M3 small LNS shows promise.

Risks: Large implementation surface, tuning burden, context overhead.

Minimum implementation: Promote only the destroy/repair operators that small experiments justify.

Validation: Improvement over small LNS on `dev-10` and `daily-40`.

Kill condition: Complexity without measured gains.

Status: specified for the S-track — full destroy/repair pools, adaptive weights, and SA acceptance in `solver-implementation-plan.md` §4 (stage S3, with static-uniform and RRT ablation flags).

### CP-SAT Retiming

Source: Fable matheuristics plan.

Hypothesis: Fixing placements and optimizing entry dates can reduce tardiness after spatial decisions are stable.

Expected impact: Unknown; likely useful only after a strong constructor exists.

Risks: Modeling effort, time budget pressure, weak benefit if geometry is the main bottleneck.

Minimum implementation: Prototype on a tiny subset or one bay before solver integration.

Validation: Controlled comparison on instances where `obj1` remains high after M2/M3.

Kill condition: No clear `obj1` gain within budget.

Status: promoted to core — exact per-bay retiming over the union-overlap conflict graph in `solver-design.md` §4.4 and `solver-implementation-plan.md` §5.1 (stage S2).

### Placement / Geometry Runtime Profiling

Source: M3 small-LNS outcome and current greedy placement logs.

Hypothesis: Greedy placement, candidate-position generation, and trusted geometry checks consume enough of the 60s budget that search layers cannot run often enough to improve most instances.

Expected impact: High if profiling confirms a concentrated hotspot.

Risks: Profiling noise, overfitting to one instance, and optimizing a path that is not actually dominant across `dev-10` or dense holdouts.

Minimum implementation: Produce a profiling report for `_candidate_positions`, `_place_blocks`, overlap/containment checks, repeated shape/orientation computations, and final `check_feasibility`; then select one cheap Python-level fix.

Validation: The report identifies ranked hotspots, and the selected fix improves runtime, LNS iteration count, or objective on representative cases without feasibility regressions.

Kill condition: Profiling does not show a placement or geometry bottleneck, or the cheapest fix fails to improve runtime/search budget.

Status: folded into stage S0-3 microbenchmarks and per-stage instrumentation (`solver-implementation-plan.md` §9, appendix §10).

### Raster or C++ Geometry Core

Source: Fable geometry acceleration plans.

Hypothesis: Geometry checks dominate runtime enough that a raster or compiled core unlocks broader search.

Expected impact: Potentially high, but only if profiling proves geometry is the bottleneck.

Risks: Semantic drift from `utils.py`, ABI issues, memory growth, large implementation cost.

Minimum implementation: Only after M4a profiling and cheap Python-level fixes are insufficient; then build a parity-tested Python/numpy prototype before C++.

Validation: Verdict parity against `utils.py` plus end-to-end runtime improvement.

Kill condition: Profiling does not justify it or parity is unreliable.

Status: gated behind S-track profiling — conservative raster prefilter only, parity-mismatch = release blocker (`solver-design.md` §4.2); no compiled geometry authority.

### Interlock Densifier

Source: Fable interlocking analysis.

Hypothesis: Controlled layer overhangs can improve dense-instance packing if exit precedence remains acyclic and operation ordering is correct.

Expected impact: Potentially high on dense instances.

Risks: High feasibility risk, difficult operation ordering, hidden checker regressions.

Minimum implementation: Offline proposal pass behind a disabled-by-default flag.

Validation: Exact `utils.check_feasibility` and stage-5 operation replay tests.

Kill condition: Any feasibility instability.

Status: scheduled as gated stage S6 (`solver-design.md` §2.4 truth table, §4.7; `solver-implementation-plan.md` interlock.py) with mandatory truth-table tests; default off.

## Rejected Ideas

No rejected ideas recorded yet.
