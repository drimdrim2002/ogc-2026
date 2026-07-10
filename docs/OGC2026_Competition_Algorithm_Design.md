# OGC 2026 Competition Algorithm Design

## OGC-SAGE: a safe anytime Gurobi matheuristic

Date: 2026-07-10  
Status: decision-complete design based on the supplied problem analysis, the checker, the current repository, all 40 local training instances, and executable local probes.

---

## 1. Executive decision

The strongest practical solver for this repository is not a monolithic MIP and not a larger version of the current greedy algorithm. It should be a **safe anytime matheuristic** with three tightly coupled levels:

1. **Gurobi master:** obtain globally strong bay assignments and coarse start-time plans.
2. **Exact geometry constructor:** turn each master plan into a checker-feasible spatial schedule using Shapely as the sole geometry authority.
3. **Gurobi improvement loop:** alternate exact fixed-layout retiming with small candidate-selection neighborhoods and targeted geometric reconstruction.

I call this architecture **OGC-SAGE**: Safe Assignment, Geometry, and Exact-retiming.

The non-negotiable design rule is that a fully serialized, checker-validated incumbent exists before optional search begins. Any exception, model timeout, failed repair, or global deadline returns that immutable incumbent. This follows directly from the competition score: a failure scores `-1`, while every feasible solution receives a positive rank score.

Gurobi should be the primary mathematical optimizer for this design. Version 13.0.2 is present and operational in the project environment, and the user explicitly permits it. The prior draft's two objections do not hold here:

- Gurobi availability is no longer hypothetical.
- The retiming formulation can use **indicator constraints**, so it does not need a weak hand-written big-M relaxation.

The local probe reported a restricted non-production entitlement, so full-size model entitlement must be verified in the submission rehearsal. All Gurobi calls must remain optional and guarded; a size/license/model error returns to the pure-Python incumbent path.

---

## 2. What the repository and training data imply

### 2.1 Current solver state

The checked-out submission entry point, `baseline/myalgorithm.py`, still delegates directly to `baseline_greedy.greedyalgorithm`. The reference greedy has four structural limits:

1. Position candidates are generated from every already placed block, rather than only blocks co-present at the candidate time. The Cartesian product of accumulated x/y anchors grows rapidly.
2. A new insertion checks the moving block's own entry and exit, but an insertion earlier than an existing block can invalidate that existing block's future crane operation. This is why the solver still requires post-hoc repair.
3. The incremental placement score is not the exact final `Z2` range and makes globally coupled assignment decisions greedily.
4. Same-time exits are serialized by block id, which is not safe once layer interlocking is used.

The repository history contains a safer adaptive-greedy/small-LNS branch, but its recorded 300-second diagnostic shows the main bottleneck clearly: on several 150-300 block instances, phase 1 consumed about 285 seconds and LNS never ran. A faster constructor is a prerequisite, not a micro-optimization.

### 2.2 Measured instance structure

The two local training sets contain 40 unique instances.

| Property | Observed range/result |
|---|---:|
| Blocks | 100-300 |
| Bays | 2-5 |
| Bay width | 32-179 |
| Bay height | 15-29 |
| Orientations per block | 2-8 |
| Layers per orientation | 1-4 |
| Median valid integer locations per fitting orientation/bay | 810 |
| 90th percentile valid locations | 2,080 |
| Maximum valid locations | 4,368 |
| Exact orientation geometries inspected | 57,560 |
| Exact duplicate orientation geometries | 0 |

Consequences:

- Exhaustive `(block, bay, orientation, x, y, time)` enumeration is too large.
- A bounded candidate set is necessary, but the small bay height makes selective row/grid scans viable as an escalation path.
- Geometry objects should be cached per orientation, but cross-block shape deduplication will not help on the visible data.
- Pair/offset verdict caching is still valuable because LNS repeatedly revisits the same relative placements.

The two training families differ materially:

| Metric | `prob_1..20` | `prob_21..40` |
|---|---:|---:|
| Mean processing time | 7.52 | 15.98 |
| Mean slack `D-R-P` | 1.43 | 3.53 |
| Mean zero-slack fraction | 28.3% | 13.4% |
| Mean union-area energy / bay-time capacity | 0.212 | 0.501 |
| Maximum union-area energy pressure | 0.301 | 0.816 |

The second family has more due-date slack but is substantially more spatially congested because processing times are longer. Solver profiles must therefore be selected from measured congestion as well as slack.

### 2.3 Executable design evidence

The following probes were run against real repository data and the supplied checker.

| Probe | Result |
|---|---|
| Exact assignment-only Gurobi model on all 40 instances | All 40 solved to proven optimality in 0.114 seconds of optimizer time (model construction excluded); the historical reference assignments were above this lower bound on 37/40 instances, with median weighted `Z2+Z3` headroom of 7,726. This is a lower bound, not a promise that those assignments preserve tardiness. |
| Fixed-layout retiming on 20 real blocks from `prob_4` | Checker-feasible tardiness fell from 702 to 26; Gurobi proved 26 optimal in milliseconds. This is a semantic proof-of-concept, not a full-scale timing result. |
| Exact one-way interlock witness using real `prob_4` shapes | One witness showed conservative time separation with minimum tardiness 7 and exact nesting with tardiness 0; the nested schedule passed all five checker stages. |
| Random pair/placement audit on seven representative instances | Among randomly sampled top-view-overlapping placements, 4.09% were one-way crane relations; per-instance rates ranged from 1.77% to 7.50%. This does not estimate their prevalence in optimized incumbents. |

These probes justify implementing and gating the components. They do not establish end-to-end master construction cost, full-bay retiming scalability, or production interlock gains; those remain explicit benchmark gates below.

---

## 3. Exact checker model

For block `i`, decide:

\[
(b_i,o_i,x_i,y_i,a_i,e_i)
\]

where bay, orientation-list index, position, entry date, and exit date are integral. Let

\[
d_i=\max(P_i,1).
\]

The `max(P_i,1)` is necessary because a zero-duration `[a_i,a_i)` block is replayed as EXIT-before-ENTRY and fails Stage 5.

The checker objective is the unrounded scalar

\[
F=w_1\sum_i\max(0,e_i-D_i)
  +w_2\left(\max_j Q_j-\min_j Q_j\right)
  +w_3\sum_i(S_i^{\max}-S_{i,b_i}),
\]

where

\[
Q_j=u_j\sum_{i:b_i=j}L_i,
\qquad
u_j=\frac{\text{average bay area}}{W_jH_j}.
\]

This is not lexicographic optimization. Every candidate and acceptance decision must use the exact weighted objective.

### 3.1 The four-state pair theorem

For two fixed placements in the same bay, define

\[
G(i\mid k)=1
\iff
\exists\ell, h\ge\ell:
\operatorname{area}(P_{i\ell}\cap P_{kh})>0.
\]

`G(i|k)` means block `i` cannot move vertically while block `k` is stationary. It includes same-layer collision as the `h=ell` case.

Every pair belongs to exactly one of four states:

| State | Geometry | Exact allowed temporal relationships |
|---|---|---|
| `FREE` | `G(i|k)=0`, `G(k|i)=0` | No pairwise temporal restriction. |
| `I_OUTER` | `G(i|k)=1`, `G(k|i)=0` | `i` before `k`, `k` before `i`, or `k` nested inside `i`. |
| `K_OUTER` | `G(i|k)=0`, `G(k|i)=1` | `i` before `k`, `k` before `i`, or `i` nested inside `k`. |
| `SEPARATE` | both directions obstruct | `e_i <= a_k` or `e_k <= a_i`. |

For `I_OUTER`, the nesting alternative is

\[
a_i+1\le a_k,\qquad e_k\le e_i.
\]

The strict one-day entry order is required because Stage 2 treats simultaneous entrants as present. Equal exits are allowed if the inner block is listed first. `K_OUTER` is symmetric.

This four-state form is more useful than the earlier three-way prose description because it is directly encodable in an exact retiming MIP.

### 3.2 Efficient exact obstruction evaluation

For each orientation, precompute:

- repaired Shapely polygon per layer;
- all-layer union footprint;
- complete and per-layer AABBs;
- suffix unions `U_k = union(layer_k, ..., layer_last)`.

Then

\[
G(i\mid k)=1
\iff
\exists\ell:
\operatorname{area}(P_{i\ell}\cap U^k_\ell)>0.
\]

This reduces the exact crane test from all `O(K_i K_k)` layer pairs to at most `O(K_i)` Shapely intersections after AABB filtering, without changing checker semantics.

Cache the final four-state verdict by

```text
(block_i, orient_i, block_k, orient_k, dx, dy)
```

with a bounded LRU. The cache stores checker-equivalent answers; it is not an approximate geometry engine.

---

## 4. OGC-SAGE architecture

```text
algorithm(prob_info, timelimit)
  |
  +-- P0  parse + exact geometry preprocessing + budget setup
  +-- P1  build, serialize, and full-check a parallel-serial safe incumbent
  +-- P2  Gurobi assignment/start-time portfolio
  +-- P3  regret-based exact constructor, several profiles
  +-- P4  Gurobi exact four-state retiming
  +-- P5  anytime improvement loop
  |      +-- destroy critical neighborhood
  |      +-- generate bounded placement/time candidates
  |      +-- heuristic repair or Gurobi candidate-selection repair
  |      +-- retime affected bays
  |      +-- full-check strict improvements
  +-- P6  optional one-way interlock densification and final retiming
  +-- return the already validated incumbent
```

Bay subproblems are independent once assignment is fixed. `Z2` and `Z3` depend only on assignment; `Z1` depends only on exit dates. This permits exact decomposition while still evaluating cross-level moves with the full objective.

---

## 5. Phase P1: guaranteed incumbent

Construct this before any optional optimizer is imported:

1. For every block, find at least one `(bay, orientation)` with a valid **integer** reference placement:

   ```text
   ceil(-xmin) <= floor(W_j-xmax)
   ceil(-ymin) <= floor(H_j-ymax)
   ```

   AABB width/height alone is insufficient when the only fitting translation is fractional.
2. Choose a bay using a deterministic preference/load-balancing greedy rule.
3. Within each bay, schedule its blocks one at a time using `entry=max(R_i, previous_exit_in_bay)` and `exit=entry+d_i`. Different bays may run concurrently.
4. Place the current block at the minimum valid integer reference coordinate.
5. Use dwell `d_i=max(P_i,1)`.
6. Serialize dates in ascending insertion order, all EXITs before all ENTRYs.
7. Run `utils.check_feasibility` and store both the operations object and returned objective.

Because each bay contains at most one block at a time, every crane and collision constraint is trivial. Parallel serial chains are markedly better than one global serial chain and remain just as safe. The guarantee is scoped to valid official instances in which every block has at least one integer placement in some bay; otherwise no valid submission solution exists.

No later phase mutates this object. A new incumbent is installed atomically only after full checker success and strict objective improvement.

---

## 6. Phase P2: Gurobi master portfolio

### 6.1 Exact assignment lower-bound model

Create binary `x_ij` only when block `i` has a fitting orientation in bay `j`.

\[
\sum_jx_{ij}=1.
\]

Define

\[
Q_j=u_j\sum_iL_ix_{ij},
\quad Q^{max}\ge Q_j,
\quad Q^{min}\le Q_j.
\]

Minimize

\[
w_2(Q^{max}-Q^{min})
+w_3\sum_{ij}(S_i^{max}-S_{ij})x_{ij}.
\]

This is an exact lower bound on the assignment portion of the objective. On the visible instances it solves in milliseconds, so it should run whenever Gurobi is available and time remains after registering the safe incumbent.

Do not use only its best solution. Request a small solution pool or add no-good cuts to obtain 8-32 diverse near-optimal assignments. Assignment-optimal can be geometrically congested; diversity is more valuable than another decimal place in the lower bound.

### 6.2 Congestion-aware start-time guide

For budgets above roughly 30 seconds, add a second, still small, master. Let `y_ijt` select bay `j` and a candidate start `t` for block `i`. Candidate times are:

```text
all on-time dates R_i .. D_i-d_i
+ selected release/exit event boundaries
+ a capped set of tardy dates
```

The visible slack makes the on-time portion small. Add soft union-area capacity constraints for each bay/date:

\[
\sum_{i,t:t\le\tau<t+d_i}A^{union}_{ij}y_{ijt}
\le \rho_jW_jH_j+s_{j\tau}.
\]

`A_union` uses the best fitting orientation for the guide only. The slack variable is mandatory because area capacity is a packing surrogate, not a feasibility theorem. Penalize `s` on the `w1` scale and solve several congestion profiles. The constructor, not this master, remains the feasibility authority.

The master objective is the fully weighted expression `w1*candidate_tardiness + w2*Z2 + w3*Z3`, plus the explicitly labeled congestion-surrogate penalty. Its outputs provide assignment, time, and priority seeds for the spatial constructor.

---

## 7. Phase P3: exact fast constructor

### 7.1 Block order profiles

Run several deterministic or seeded profiles within a bounded construction budget:

- increasing slack, then due date;
- release date, then due date;
- decreasing union-area x dwell congestion;
- assignment regret: difference between best and second-best feasible bay cost;
- preference pressure normalized by the price of one tardy day;
- a lightly randomized mixture of the above.

Zero-slack and high-regret blocks should be committed early.

### 7.2 Time candidates

For a non-interlocking insertion with fixed dwell, feasibility changes at event boundaries. Use

\[
\{R_i\}\cup\{e_k\}\cup\{a_k-d_i\}
\]

plus the master start and nearby dates. Sort by exact candidate cost, not date alone, and cap the first pass. Escalate only if no candidate succeeds.

### 7.3 Position candidates

Generate anchors from **only the blocks co-present at the candidate interval**:

- all four bay walls;
- left/right/top/bottom AABB contact with co-present blocks;
- rounded polygon vertex-to-edge contacts;
- current position when repairing;
- a small low-discrepancy sample of valid lattice points.

The current baseline uses only right/top contacts and accumulates anchors from temporally irrelevant blocks. Both choices should be replaced.

The first pass accepts only `FREE` relationships with all overlapping intervals. This is a very fast union-footprint test and gives a robust non-interlocking solution. If no candidate succeeds, use a bounded row/grid scan with vectorized AABB filtering, followed by exact Shapely checks only for survivors.

### 7.4 Symmetric pair validation

An insertion is valid only if it preserves both the new block's operations and every existing block's future operations. For each affected pair, derive the four-state geometry and verify that the proposed intervals satisfy one of that state's exact temporal modes.

This removes the baseline's main source of post-hoc Stage 2/3 repairs.

### 7.5 Regret insertion and fallback

For every unplaced block, retain its best few complete candidates. Insert the block with maximum regret between its best and second-best candidates. Use `w1*candidate_tardiness + w2*DeltaZ2 + w3*DeltaZ3`; fragmentation is only a secondary tie-breaker.

If a block cannot be inserted within its candidate cap, place it in the earliest empty-bay window. Construction therefore always finishes with a complete feasible candidate.

---

## 8. Phase P4: exact four-state Gurobi retiming

For a fixed assignment, orientation, and position, build one model per affected bay or one disconnected all-bay model.

Variables:

\[
a_i,e_i,T_i\in\mathbb Z_{\ge0},
\quad a_i\ge R_i,
\quad e_i\ge a_i+d_i,
\quad T_i\ge e_i-D_i.
\]

A safe horizon for a nonempty bay is

\[
H_b=\max(0,\max_{i\in b}R_i)+\sum_{i\in b}d_i,
\]

because a serial schedule is always feasible within this horizon. Set `0 <= a_i,e_i <= H_b`; skip empty bays.

Pair constraints:

- `FREE`: none.
- `SEPARATE`: one binary selects `e_i <= a_k` or `e_k <= a_i` using Gurobi indicator constraints.
- `I_OUTER`: three binaries sum to one and select:
  1. `e_i <= a_k`,
  2. `e_k <= a_i`, or
  3. `a_i+1 <= a_k` and `e_k <= e_i`.
- `K_OUTER`: symmetric.

Primary objective:

\[
\min\sum_iT_i.
\]

After fixing the best primary value, minimize total dwell extension `sum(e_i-a_i-d_i)` as a secondary objective. This removes gratuitous zero-cost occupancy before due dates and makes later layout search easier.

Warm-start all dates and relation modes from the incumbent. Set `Threads=4`, a hard subproblem `TimeLimit`, deterministic seed, and silent output. If no better feasible schedule is returned, retain the input schedule.

For equal-exit nested blocks, the precedence graph is automatically acyclic: an arc from inner blocker to outer mover strictly decreases entry time, so a directed cycle would imply an impossible strict entry-time cycle. The serializer still verifies this invariant and rejects a cycle defensively.

Run the model once after a complete constructor. During LNS, batch layout changes and retime only the affected non-`FREE` connected component, with unchanged boundary blocks fixed. Trigger after `max(3,ceil(0.03*n_b))` accepted geometry changes or a search stall. Until full-scale timing is measured, cap an exact component at 80 blocks and use a critical subcomponent when larger. The 20-block real-data probe showed a checker-verified reduction from 702 to the proven optimum 26, but does not justify full-bay retiming after every move.

---

## 9. Phase P5: anytime large-neighborhood search

### 9.1 State and acceptance

Maintain separate objects for:

- `current`: may move through worse feasible solutions under SA/RRT;
- `best`: immutable checker-validated incumbent;
- `candidate`: transactional working copy.

The authoritative acceptance value is always the exact checker-equivalent weighted objective. Only strict improvements replace `best`.

### 9.2 Destroy operators

Use an adaptive portfolio:

1. tardy block plus active geometric/precedence chain;
2. congested bay-time window;
3. Shaw-related blocks by space, time, and assignment;
4. blocks contributing most to `Z2` range;
5. high preference loss with a cheap alternative bay;
6. random-k diversification.

Start with `k=max(4,ceil(0.02n))`, grow toward 10-15% of `n` after stalls, and keep neighborhoods smaller when candidate generation is expensive.

### 9.3 Repair methods

Use two repair engines:

- fast regret-2/3 insertion for most iterations;
- Gurobi candidate-selection repair periodically or after stalls.

For Gurobi repair, default to at most 16 destroyed blocks and 32 complete candidates each, with the hard product `destroyed_blocks*candidates_per_block <= 512`. Larger neighborhoods use heuristic repair. Candidate `c` fixes `(bay, orientation, x, y, entry, exit)` and has known tardiness/preference cost. Binary `z_ic` selects one candidate per block.

Add:

- lazily separated incompatibility cuts `z_ic+z_kd<=1` for selected pairwise-infeasible candidates, using bay/time/AABB indexes before exact geometry;
- cuts against unchanged blocks;
- exact bay-load equations and `Qmax-Qmin` variables;
- same-exit precedence ranks for directed crane arcs;
- the incumbent candidates as a guaranteed feasible MIP start.

This is a column-restricted multiple-choice set-packing model. Use a 0.2-3 second timebox, `SoftMemLimit=12`, and the hard candidate cap above; without those bounds, millions of Python-generated conflict cuts are possible. Apply its result transactionally, retime the affected component, serialize, and full-check before accepting.

### 9.4 Adaptive search

Use segment-based destroy/repair weights and a time-based simulated-annealing temperature calibrated from observed positive deltas. Reheat after a stall, but never expose a non-best `current` solution as the return value.

The search must record per-operator attempts, feasible repairs, accepted moves, objective delta, geometry cache hit rate, and time spent. An LNS loop that merely consumes time is not a feature.

---

## 10. Phase P6: interlock densification

Interlock should not burden the first robust constructor, but the exact relation kernel and retimer should support it from the beginning.

Activate a late densification pass only when:

- the union-safe solver has stalled;
- tardiness remains;
- the relevant bay/time window is spatially saturated;
- sufficient deadline reserve remains.

Search offsets that produce `I_OUTER` or `K_OUTER`, prefer hosts that can extend without tardiness, and let the exact retimer choose separation versus nesting. The random audit found one-way relations in about 4% of sampled top-view-overlapping placements; because these were not optimized incumbents, the number is only a search-priority hint.

The real-shape witness demonstrated a strict benefit: separation required 7 tardy days, while nesting achieved zero and passed the checker.

---

## 11. Canonical serialization

There must be one serializer.

1. Insert date keys in ascending numeric order.
2. List every EXIT before every ENTRY on the same date, across all bays.
3. For same-date exits in one bay, construct a graph with arc `k -> i` when `G(i|k)=1`; topologically sort it so the blocker exits before the blocked mover.
4. Reject a candidate if that exit graph is cyclic.
5. Same-date entries are allowed only when every pair **within the same bay** is `FREE`; ordering cannot rescue an obstructed simultaneous entry. Cross-bay entries are independent.
6. Emit integer `x`, `y`, dates, ids, and orientation-list indices.

Chronological dictionary insertion is important because the checker reconstructs assignments while iterating the raw operations dictionary before it later sorts dates.

---

## 12. Wall-clock budget policy

Use `time.monotonic()` exclusively. After the first full check, estimate checker p95 time and set

```text
reserve = min(60s, 0.25*TL,
              max(0.05*TL, 2*checker_p95 + 0.1s))
search_deadline = start + TL - reserve
```

Suggested ladder:

| Supplied limit | Work performed |
|---:|---|
| `< 2s` | Safe parallel-serial incumbent only. |
| `2-12s` | Incumbent, exact assignment master, one fast constructor, optional short retime. |
| `12-60s` | Assignment portfolio, 2-4 constructors, retime, union-safe LNS. |
| `>= 60s` | Full congestion-aware master, constructor portfolio, repeated retime/LNS, candidate-selection MIPs, late interlock pass. |

### 12.1 Deterministic starting configuration

These are implementation defaults, not claims of universal optimality. Change them only through recorded ablations.

| Parameter | Default |
|---|---:|
| Global seed | `20260710` |
| Gurobi | `Threads=4`, `OutputFlag=0`, `MIPFocus=1`, `SoftMemLimit=12` |
| Four-state verdict cache | `2^18` LRU entries |
| Exact assignment master | `min(1s, 0.03*remaining)` |
| Assignment portfolio | 8 solutions, assignment gap <= 10%, pairwise Hamming distance >= `max(2,ceil(0.03*n))` |
| Congestion guide profiles | `rho in {0.60, 0.75, 0.90}` |
| Base overload price | `lambda0 = w1/max(1, median_union_area)`; run multipliers `{0.5,1,2}` |
| Constructor portfolio | at most 6 profiles and `min(20s,0.12*TL)` total |
| Time candidates per insertion | 12 first pass, 32 after escalation |
| Anchor candidates per `(bay,orientation,time)` | 48 first pass |
| Lattice-scan escalation | at most 512 AABB-filtered points per `(bay,orientation,time)` |
| Retiming | `min(3s,0.05*remaining)`, at most 80 free variables |
| Retiming trigger during LNS | `max(3,ceil(0.03*n_b))` accepted geometry changes or a stall |
| Heuristic destroy size | start `max(4,ceil(0.02*n))`; grow by 1.5 up to `ceil(0.15*n)` |
| MIP repair | at most 16 blocks, 32 candidates each, product <= 512, timebox <= 3s |
| ALNS adaptation | 50-iteration segments; rewards `(new_best,current_improve,worse_accept)=(20,5,2)`; reaction 0.2 |
| SA calibration | 32 improving-only warm-up iterations; `T0=median(positive_delta)/ln(2)`; exponential cooling to `T0/1000` |
| Stall | no best update after both 50 iterations and 8% of search-phase time |
| Interlock activation | tardiness > 0, union-energy pressure >= 0.45, union-safe search stalled; budget <= 8% of TL |

If the best assignment objective is zero, the percentage-gap rule is undefined; generate distinct alternatives with the Hamming-distance/no-good cuts only. Implement lazy incompatibility separation as a deterministic solve-inspect-add-cut loop by default, rather than performing Shapely work inside a multithreaded Gurobi callback.

Run only one four-thread Gurobi solve at a time. Do not combine four Gurobi threads with a multiprocessing portfolio under the server's four-core cap. Multiprocessing is a later benchmark-gated option, not a default.

Every loop boundary and every model timebox checks the global deadline. A serialized validated incumbent is always ready, so returning is O(1).

Do not start a new full-check call unless the measured remaining time exceeds its conservative predicted duration plus margin. An in-progress checker call is not externally preemptible.

---

## 13. Implementation layout

Keep `baseline/utils.py` unchanged and freeze `baseline/baseline_greedy.py` as a comparison reference. Implement the new solver separately:

```text
baseline/
  myalgorithm.py           # guarded public entry point only
  solver/
    entry.py               # phase orchestration and exception armor
    budget.py              # monotonic deadline and sub-budgets
    instance.py            # parsed immutable instance data
    geometry.py            # ShapeInfo, suffix unions, four-state cache
    state.py               # transactional solution state and exact objective
    serialize.py           # sole operations serializer
    fallback.py            # parallel-serial verified incumbent
    assignment.py          # Gurobi exact and congestion-aware masters
    construct.py           # event-aware regret constructor
    retime.py              # exact four-state Gurobi model
    neighborhoods.py       # destroy and candidate generation
    repair_mip.py          # candidate-selection Gurobi model
    alns.py                # adaptive anytime loop
```

Implementation order:

1. checker-contract tests, serializer, fallback, budget, immutable incumbent;
2. geometry preprocessing and four-state parity tests;
3. exact Gurobi assignment portfolio;
4. event-aware union-safe constructor;
5. exact retimer;
6. heuristic LNS;
7. Gurobi candidate-selection repair;
8. gated interlock densifier;
9. packaging and stress hardening.

Keep P5 and P6 disabled in the submission configuration until the new constructor is 40/40 feasible at 60 seconds, has p90 construction time at most 8 seconds, maximum construction time at most 12 seconds, and never reaches its construction deadline. This gate is evaluated across the full daily-40 set, not one favorable instance.

---

## 14. Validation gates

### 14.1 Correctness gates

- 100% Stage-5 feasibility on all 40 instances at every tested budget and seed.
- Contract tests for half-open handover, `P=0`, boundary contact, negative local AABB anchors, simultaneous entries, simultaneous exit topology, float `Z2`, and chronological dictionary reconstruction.
- Random four-state property tests: MIP schedules must agree with `check_feasibility` on thousands of two-block and small multi-block cases.
- Internal objective and delta parity with the checker within `1e-6` relative error.
- No optional solver/model exception may escape the public entry point.

### 14.2 Performance gates

- Constructor time reported separately; target less than 5-10 seconds for 300 visible blocks before enabling ALNS.
- Retiming must never worsen `Z1`; record bound, gap, time, and accepted delta.
- Every LNS operator must show nonzero feasible/accepted activity on at least one justified instance family or be removed.
- Interlock remains off unless it improves a predefined dense subset with zero feasibility regressions.
- The validated-best trace within every run must be non-increasing. Treat any longer-budget regression for the same seed as a validation failure to investigate, even though different budget ladders do not make cross-run monotonicity automatic.

### 14.3 Competition-aligned comparison

Do not optimize the sum of raw objectives across heterogeneous instances alone. Report:

- win/tie/loss per instance;
- Borda/rank score among solver variants;
- relative gap to best known per instance;
- `w1*DeltaZ1`, `w2*DeltaZ2`, `w3*DeltaZ3` separately;
- median, p90, and worst result over at least five seeds;
- anytime primal integral and time-to-first-improvement.

Benchmark at `{10, 60, 300}` seconds on all 40 instances, with occasional 1,800-second runs to verify that the search can exploit the largest expected budget.

---

## 15. Design choices explicitly rejected

| Choice | Reason |
|---|---|
| One monolithic continuous-coordinate MIP | Irregular non-convex polygon geometry and crane relations make the formulation far too large and weak. |
| Greedy plus more post-hoc repair | It preserves the dominant candidate explosion and one-sided insertion defect. |
| Lexicographic tardiness | The checker uses a scalar weighted objective; assignment gains can outweigh tardiness on some instances. |
| Full lattice enumeration for every block | Median 810 positions per fitting orientation/bay, up to 4,368, multiplied by blocks, orientations, bays, and dates. |
| Custom approximate geometry as authority | Any semantic drift risks a `-1`; Shapely/checker semantics must remain authoritative. |
| Interlocking in the first constructor | It is valuable but relatively rare and increases ordering risk; use it after a robust union-safe incumbent. |
| Default multiprocessing | Four-core limit and Gurobi threading make oversubscription likely; benchmark before enabling. |
| RL/GNN as the primary optimizer | Forty visible instances are insufficient training evidence, and deterministic matheuristics expose much stronger exact structure. |

---

## 16. Expected competitive advantage

The design has four independent score levers:

1. **Reliability:** a validated solution exists before risky work, protecting every problem's rank score.
2. **Global assignment quality:** exact assignment optimization costs milliseconds on visible instances and exposes large `Z2/Z3` headroom.
3. **Layout-optimal scheduling:** exact four-state retiming recovers tardiness left by constructive heuristics.
4. **Anytime depth:** candidate-selection MIPs and ALNS convert longer limits into real improvements instead of spending the entire budget in phase 1.

No design can guarantee first place on hidden instances. This architecture is the best-supported direction in the current project because every expensive idea is isolated behind a measurable gate, while the checker semantics and small-scale value of its central assignment and retiming components have been demonstrated. Full-scale performance gates remain open by design.

---

## 17. Probe reproduction notes

All probes used `/opt/homebrew/Caskroom/miniforge/base/envs/ogc-2026/bin/python`, Gurobi 13.0.2, Shapely 2.1.2, and the unmodified `baseline/utils.py` checker.

### Assignment probe

- Instances: `data/train 2/prob_1..20.json` and `data/train/prob_21..40.json`.
- Model: Section 6.1, using the current baseline's AABB width/height fit test. Production code must strengthen this to the integer-placement test in Section 5.
- Historical comparison: `origin/main:experiments/results/m3/small_lns/2026-07-08-m3-small-lns-daily-40-myalgorithm-60s-small.json`.
- Reported 0.114 seconds is the sum of `model.optimize()` wall times only; fit checks and model construction were excluded.
- Headroom is reported per instance from `w2*obj2+w3*obj3`; the document uses the median rather than treating the heterogeneous sum as a competition metric.

### Retiming probe

- Shapes/times: first 20 blocks of `data/train 2/prob_4.json`.
- Bay: bay 0.
- Orientations and valid integer positions: deterministic random selection with seed `20260710`.
- Baseline: one EDD serial chain, checker tardiness 702.
- Model: Section 8, once with one-way relations conservatively separated and once with all four states. This sampled layout contained 167 `FREE` and 23 `SEPARATE` pairs, so both models reached tardiness 26 and proved it optimal.
- The serialized result passed `check_feasibility` at Stage 5.

### Interlock witness

- Source: `data/train 2/prob_4.json`, bay 0.
- Outer: source block 24, orientation 5, position `(102,18)`.
- Inner: source block 46, orientation 1, position `(115,3)`.
- Synthetic timing used only to isolate the relation: outer `(R,P,D)=(0,10,10)`, inner `(2,5,7)`.
- The conservative optimum is the serial order inner then outer with tardiness 7. The exact nested schedule is outer `[0,10)`, inner `[2,7)`, with tardiness 0 and a Stage-5 checker pass.

### Random relation audit

- Instances: `prob_4`, `prob_9`, `prob_13`, `prob_20`, `prob_21`, `prob_32`, and `prob_40`.
- Samples: 12,000 random block/orientation/bay/integer-position pairs per instance, seeds `20260710..20260716`.
- Conditioning: both blocks fit the sampled bay; reported percentages condition further on positive top-view overlap.
- Total observed top-view-overlapping samples: 19,447; one-way samples: 795; rate: 4.088%.
- This is a geometry-opportunity audit, not a forecast of optimized-solution usage.
