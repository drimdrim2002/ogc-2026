# OGC 2026 Championship-Oriented Solver Design (Clean-Slate Design)

Last updated: 2026-07-10
Status: active — the single source of truth for algorithm design

There are exactly two sources of evidence: `docs/OGC2026_Problem_Analysis.md` (the problem-statement analysis, hereafter the “specification”) and `baseline/utils.py` (the official checker, hereafter the “checker”). Every normative claim in this document is derived directly from one of these two sources, and checker claims carry `utils.py:line` anchors. It assumes no conclusion from existing analysis documents (such as docs/fable). From an execution-roadmap perspective, this document supersedes `docs/deprecated/m2-m4-execution-plan.md`.

---

## 0. Design objectives: principles derived backward from the scoring function

Competition scoring (specification §5.5): an infeasible/timeout/crash result receives **−1 point** per instance; a feasible result receives `R − nb` (`R` = number of evaluated teams; `nb` = number of teams with a **strictly better** objective on that instance). The total score is the sum across all instances. Four design principles follow immediately.

| # | Principle | Basis |
|---|---|---|
| P1 | **Absolute avoidance of −1 is the first priority.** A −1 on one instance forfeits the entire ~R points that could have been obtained there. Any improvement search runs only while a verified feasible solution is already in hand | Specification §5.3, §5.5 |
| P2 | **Operation must scale with the undisclosed timelimit.** The limit is “several minutes to 30 minutes” and arrives as the `algorithm(prob_info, timelimit)` parameter. A fixed pipeline wastes a large budget. An anytime structure (returning the best verified solution whenever interrupted) is mandatory | Specification §5.4, §6.2 |
| P3 | **Strictly better raises the rank.** The objective is a float and ties do not count in nb, so even the final unit of improvement has ranking value. Exact (not approximate) delta evaluation and late-stage fine optimization earn points | Specification §5.5, utils.py:1421 |
| P4 | **Hidden instances reward generalization.** Do not hard-code visible instances. Derive every threshold from instance data (weights, scale, congestion) | Specification §3, §7.3 |

Additional facts: the server provides 4 cores (400%), 16GB, and no internet; it supports shapely 2.1.2, OR-Tools 9.15, the numpy family, and **gurobipy 13.0.2/xpress 9.8.1** (specification §5.4, §6.1). As of 2026-07-10, Gurobi 13.0.2 model construction and optimization also succeeded in this project’s `ogc-2026` environment. Therefore, mathematical optimization is not limited to Google OR-Tools. This design adopts a **hybrid matheuristic that uses Gurobi as a first-class exact backend and retains CP-SAT as complement and fallback**. No external-solver exception may corrupt a verified incumbent.

---

## 1. Verification principles

1. The checker is the sole semantic authority. If the specification and checker admit different readings, the checker takes precedence (the server overwrites and runs `utils.py` — specification §5.1).
2. Derive directional rules (≤/≥, earlier/later, above/below) from the §2 truth table, and lock them before implementation with unit tests that use `check_feasibility` as the oracle.
3. Internal objective/delta calculations require parity tests against the checker, and the authority for solution acceptance is always the `check_feasibility` return value.
4. shapely (the checker path) is the sole authority for geometric predicates. Custom geometry is allowed only as (i) an **exact-decision cache** keyed by integer offsets (storage, not approximation), or (ii) a **conservative prefilter** (uncertainty must fall back to the exact predicate).

---

## 2. Mathematical structure of the problem (theorems derived from the checker)

### 2.1 Decision variables and fundamental constraints

For each block i: bay `b_i`, integer position `(x_i, y_i)`, orientation `o_i`, integer entry day `a_i`, and integer exit day `e_i`. Its occupancy interval is half-open: `[a_i, e_i)`.

- `a_i ≥ R_i`, `e_i − a_i ≥ P_i` (tolerance 1e-6, utils.py:1123-1131). **e is a free variable** — `e = a + P` is a choice, not a constraint; because of checker replay semantics, `P=0` still requires at least `e=a+1` (specification §2.6, §2.3).
- Boundary: the checker’s containment predicate requires the block’s all-layer AABB to lie in `[0,W]×[0,H]` (utils.py:252-266, 1244-1251; boundary contact is allowed). Since a bay is an axis-aligned rectangle, this is equivalent to polygon containment.
- Coordinates are checked after rounding with `int(round())` (utils.py:1151-1153), so the solver outputs integers from the outset.
- Present-set semantics: **Stage 2** (entry) uses `a_k ≤ t < e_k` — lower endpoint included (utils.py:1177). **Stage 3** (exit) uses `a_k < t < e_k` — both endpoints excluded (utils.py:1217). **Stage 4** pairwise temporal overlap is `a1 < e2 and a2 < e1` (utils.py:1156-1158). **Stage 5** replays dates in ascending order, with EXIT before ENTRY on the same date (utils.py:1293-1302), and checks sequentially against the live present-set **in the listed order** within each operation type (utils.py:1304-1382).
- Crane sweep: layer k of a moving block may not overlap internally (area > 0) with layer `j ≥ k` of a present block — identically for entry (utils.py:703) and exit (utils.py:799). Static collisions are only at equal levels, through `min(K1,K2)` (utils.py:521). All predicates repair with shapely `buffer(0)` (utils.py:187-190) and then use strict `area > 0` (utils.py:535, 713, 809); **boundary contact is legal**.

### 2.2 Objective-decomposition theorem

`objective = w1·Z1 + w2·Z2 + w3·Z3`, all float, with no flooring (utils.py:1388-1421).

- `Z1 = Σ max(0, e_i − D_i)` — a **function of exit dates only** (utils.py:1402).
- `Z2 = max_{j≠j'} |u_j·L_j − u_j'·L_j'|` is the **range** (max − min) of weighted loads, where `u_j = average area/area_j`; empty bays participate with L=0 — a **function of bay assignment only** (utils.py:1409-1419). Corollary: with at least two bays, leaving one empty increases this to `Z2 = max_j u_j·L_j`. High-w2 instances require distribution across every bay.
- `Z3 = Σ (S_i^max − S_i,b_i)` — a **function of bay assignment only** (utils.py:1404-1405).

**Consequence (the foundation of the design)**: positions and orientations do **not contribute at all directly** to the objective. Geometry is a pure feasibility resource; it only determines which entry/exit dates can be achieved in which bay. Therefore:

1. **Upper level (assignment)**: selecting `b_i` fixes Z2+Z3 in closed form and fixes each bay’s block set.
2. **Lower level (per-bay scheduling + packing)**: bays have no interaction whatsoever (every checker test is bay-local — utils.py:1168-1273, 1289). Independently minimizing Σ tardiness in each bay minimizes Z1.

This two-level decomposition is **exact**, not heuristic.

### 2.3 Pair-decomposition theorem

Checker feasibility is the conjunction of (i) unary block conditions (Stage 1 timing and boundary) and (ii) **pair** conditions for blocks in the same bay (all Stage 2/3/4/5 obstacle and collision records belong to a particular pair, utils.py:1179-1194, 1219-1228, 1260-1273, 1339-1382). Therefore:

**Intuition and example.** Suppose a candidate changes only block K—its position, orientation, dates, or bay. For two other blocks B and C, both their unary inputs and the inputs to the B–C pair test are bit-for-bit unchanged, so a B–C test that passed before cannot newly fail. The checker has no independent violation that intrinsically requires three or more blocks. Even when Stages 2/3/5 iterate over an entire present-set at an operation time, every reported obstruction is a pair: one moving block and one blocker. It is therefore sufficient to test K's unary conditions and every pair between K and a block in the **same bay in the candidate solution**. If K moves bays, its old-bay pairs are absent from the candidate and only its new-bay pairs need testing. This is an exact reduction from rechecking all pairs to roughly O(k·n) checks for k changed blocks.

- When only part of a solution changes, rechecking **pairs involving changed blocks plus the unary conditions of changed blocks** determines whole-solution feasibility exactly (targeted revalidation — not approximation).
- Non-interlock pair conditions translate exactly to Gurobi indicator constraints or CP-SAT enforced constraints (§4.4): one of the two choices—i vacates first or j vacates first—activates its corresponding date inequality. This is not an approximation of the checker; it is the direct model encoding of the non-interlock trichotomy in §2.4. Interlock pairs require additional nested-time constraints and same-day EXIT ordering, so they are handled only behind a separate gate.

### 2.4 Pair-feasibility trichotomy (the entire feasibility theory)

For a same-bay block pair (i, j), the exact conditions for coexistence/non-coexistence have only the following three branches. `U_i` is the all-layer union (union-footprint) polygon of the placed orientation.

**Branch 1 — temporal separation**: `e_i ≤ a_j` or `e_j ≤ a_i` (equality allowed). Geometry is irrelevant.
Basis: Stage 4 checks only overlapping intervals (utils.py:1263-1265). `e_i = a_j` (a same-day handoff) is also safe: Stage 2 has a strict upper bound (`t < e_k`) and does not count the departing block (utils.py:1177); Stage 3 has a strict lower bound and does not count the entering block (utils.py:1217); and Stage 5 processes EXIT first, removes it from the present-set, then checks ENTRY (utils.py:1357-1382 → 1330-1355). **A same-day handoff is legal and free in occupancy density** — the separation constraint correctly uses `≤`, not `<`.

**Branch 2 — union-disjoint (non-interlock)**: coexistence with `area(U_i ∩ U_j) = 0`.
**Theorem**: in this case every checker predicate for this pair passes at every time and in every same-day listed order.
Proof: every check requires `area(A_k ∩ B_j) > 0` for some layer pair, but `A_k ⊆ U_A` and `B_j ⊆ U_B`, so intersection area is zero. Stage 5 replay obstacle predicates are the same predicate. ∎
The converse is also useful: if `area(U_i ∩ U_j) > 0`, some layer pair (k, j) overlaps, and choosing k ≤ j makes movement in that direction blocked. Corollary: **a union-overlap pair cannot enter concurrently on the same day** (Stage 2 considers concurrent entrants present — `a_k ≤ t`, utils.py:1177 — so list order cannot rescue it).

**Branch 3 — interlock (staggered-layer coexistence)**: coexistence with `area(U_i ∩ U_j) > 0` but disjoint equal-level layers (so Stage 4 passes). Define the directional predicate:
`OBS(X; Y) := ∃ k ≤ j : area(X_k ∩ Y_j) > 0` — “Y blocks X’s vertical movement.”
For a pair to coexist, exactly one direction must be blocked (if both are blocked, neither can leave). Suppose only `OBS(host; guest)` is true (the guest straddles above the host). The **truth table** is:

| Event | Decision | Basis |
|---|---|---|
| host enters/exits while guest exists | Impossible | The same predicate `OBS(host;guest)` → Stage 2/3/5 |
| guest enters/exits while host exists | Possible | `OBS(guest;host)` = false |
| entry order | **`a_host < a_guest` (strict)** | Same day is impossible: Stage 2 includes its lower endpoint (utils.py:1177) |
| exit order | **`e_guest ≤ e_host`** | Stage 3 excludes both endpoints, so equality is allowed (utils.py:1217) |
| day with `e_guest = e_host` | **guest first** in the EXIT list | Stage 5 replays list order (utils.py:1364-1382) |

Thus the guest’s residence is **nested** in the host’s. If the host’s minimum exit day `a_host + P_host` precedes `e_guest`, **extending the host exit is necessary** — this is why e must be free; the extension costs zero while `e_host ≤ D_host`.

This trichotomy is the entire feasibility theory of the problem. Every pair must satisfy one of the three branches, each with the closed-form conditions above.

### 2.5 The exact scope of exit minimality

**Theorem (local dominance in the non-interlock region)**: in a solution where every coexistence pair is only branch 1 or 2, reducing any block’s e to `a + max(P, 1)` preserves feasibility and weakly decreases Z1.
Proof: shrinking an interval preserves branch-1 status (it only strengthens separation), and branch-2 pairs are time-independent. Z1 is monotone in e. ∎
**This is not globally true**: a branch-3 host requires `e_host ≥ e_guest`. Therefore, use “exit = entry + dwell” only as an internal convention in non-interlock mode; for interlock and repair (resolving blocked exits), exit extension is a first-class lever.

### 2.6 Edge-case catalog (derived directly from the checker)

| Case | Rule | Basis |
|---|---|---|
| `P_i = 0` | **Residence ≥ 1 day** is still required. With `e = a`, same-day EXIT is processed before ENTRY and violates “not present” | utils.py:1293-1302, 1357-1363 |
| Same-day handoff `e_i = a_j` | Legal (see the branch-1 derivation) — free in density | §2.4 |
| Concurrent same-day entry of a union-overlap pair | Always impossible | §2.4 branch-2 corollary |
| Boundary/block contact | Legal (only `area > 0` violates) | utils.py:535 etc. |
| Coordinates | Integer output is mandatory (the server rounds before checking) | Specification §4; utils.py:1151 |
| Date keys | Strings; only dates with operations | Specification §4 |
| Missing weights | Checker default is 1.0 | utils.py:1390-1392 |
| One bay | Z2 = 0 (there is no pair) — a purely intra-bay problem | utils.py:1412-1419 |
| A block cannot fit a given bay | Use an orientation-specific AABB-derived `fit(i, j, o)` matrix as a hard assignment constraint | utils.py:252-266 |

---

## 3. Why this design wins (ranking levers)

Each lever is an independent factor that raises rank relative to competitors, and maps one-to-one to a §6 roadmap stage.

1. **Zero −1 (S0)**: the always-valid incumbent protocol (§4.1), a budget manager, and global exception protection deliver a free ranking gain over teams that receive −1 on hidden instances or timeouts. This alone is the cheapest and most reliable source of points.
2. **Search throughput (S1)**: because of the §2.4 trichotomy, placement-candidate checking contracts to “AABB batch → one union intersection” (crane checks disappear entirely for non-interlock candidates), while an integer-offset decision cache reduces repeated work. Validate actual speedup with S0/S1 microbenchmarks.
3. **Per-layout scheduling optimality (S2)**: once non-interlock positions are fixed, the remaining problem is **disjunctive scheduling**: “temporal separation of union-overlap pairs + release + minimum tardiness.” Compare Gurobi indicator-MIP and CP-SAT with the same input and timebox, then keep using the stronger backend for the instance (§4.4). The claim that this recovers Z1 left behind by heuristic-only teams is validated by the S2 gate.
4. **Anytime expansion (S3)**: LNS (destroy-repair) plus periodic retiming converts 5-minute and 30-minute budgets into actual improvement. The gap over teams with a 60-second fixed pipeline widens at large timelimits.
5. **Weight adaptation (S4)**: Z2/Z3 are closed-form, so the gain from bay moves/swaps is calculated exactly. Read the exchange rate `w1 : w3·ΔS : w2·Δrange` and target a different point for each instance. At example weights (26667/10/300), the maximum preference loss (`ΔS=100`) is on the same order as one day of tardiness — the trade differs by instance (specification §2.7).
6. **Interlock bonus (S6, gated)**: few teams use branch 3 on dense instances because it is difficult to implement exactly. Enabling it safely through the truth table and tests earns points others cannot collect. It is default off and is enabled only after passing its gate.
7. **Use of 4 cores**: one exact solve uses Gurobi `Threads=4` or CP-SAT `num_search_workers=4`. When enabling the multi-process portfolio (S5), reduce each exact backend to one thread to prevent nested oversubscription.

---

## 4. Solver architecture

```text
algorithm(prob_info, timelimit)
 ├─ P0 preprocessing   : parsing, geometric precomputation, fit matrix, exchange-rate table, budget plan
 ├─ P0' safety net     : create T0 (sequential solo placement) → check_feasibility → register incumbent
 ├─ P1 assignment      : initial bay assignment reflecting Z2/Z3 + congestion approximation
 ├─ P2 construction    : per-bay insertion constructor (non-interlock, multiple profiles)
 ├─ P2' verify/register: full check → replace incumbent
 ├─ P3 improvement loop: [Gurobi/CP-SAT exact repair ↔ LNS destroy-repair ↔ bay-move]
 │                       only accepted candidates get a full check → replace incumbent (atomically)
 └─ P4 finish          : return incumbent (already verified and serialized)
```

### 4.1 Always-valid incumbent protocol (implementation of P1)

- Immediately after startup (target ~1 second, calibrated empirically in S0), create T0 (place every block sequentially in an “empty-bay solo window” — because the bay is empty on entry and exit, every sweep trivially passes), then register it as the incumbent **after a full check**.
- Every later stage follows “generate candidate → targeted revalidation (§2.3, exact) → if improved, full check → atomically replace incumbent only if it passes.” **There is no path by which an unverified solution becomes incumbent.**
- Wrap the top level in `try/except` and return the current incumbent on any exception. The return object is always a pre-serialized operations dict, so returning immediately before the deadline is O(1).
- Budget: `deadline = start + timelimit − reserve`, `reserve = clamp(0.05·timelimit, 3s, 60s)`. Every loop cooperatively checks its deadline. With an extremely short timelimit (stress: 0.5~5s), skip stages after P2 and return T0 or a partially constructed solution.

### 4.2 Geometry kernel

- **Precomputation (once per instance)**: anchor-aligned layer lists by (block, orient); per-layer/all-layer AABBs; **union-footprint polygons** (`shapely.unary_union` plus `buffer(0)` when needed); `shapely.prepared` objects; area; layer count; and a bay-specific fit(i, j, o) matrix.
- **Predicate semantics**: candidate acceptance uses the same `intersection.area > 0` criterion as the checker. Fast path: if prepared `intersects()` is False, pass immediately (disjointness is certain); if True, calculate intersection area and pass when it is 0 (contact). This preserves legal high-density contact packing.
- **Decision cache**: because positions are integers, a pair predicate is completely determined by `(shape_i, o_i, shape_j, o_j, dx, dy)`. Cache union-disjointness and the OBS direction predicate in a dict keyed this way (bounded LRU). This is **storage of oracle answers**, not a second geometry implementation.
- **AABB batch filter**: maintain AABBs of present blocks per bay in numpy arrays, then send only vector-comparison survivors to shapely.
- (Gate) Conservative raster prefilter: a three-way classification using full-cell/touched-cell dual masks (certain collision/certainly free/uncertain→shapely). Introduce only when profiling justifies it (after S6); one parity-harness mismatch stops introduction.

### 4.3 Constructor (P2): non-interlock insertion construction

Insert blocks into space-time one by one in priority order. For block i:

1. **Time candidates** (ascending, cap T): use `dwell_i = max(P_i, 1)` and take values ≥ `R_i` from `{R_i} ∪ {e_j: placed j} ∪ {a_j − dwell_i: placed j}`. (Interval feasibility changes only at event boundaries, so this set is complete — start immediately after an exit or finish immediately before an entry.)
2. At time t, the **coexistence set** O(t) consists of placed blocks overlapping `[t, t+dwell_i)` (looked up through a sorted event index).
3. **Position candidates**: wall corners plus right/top contact anchors of O(t) blocks, in lexicographic (y, x) order, capped at K. At each anchor: AABB batch → cached union predicate. **First-fit**: stop after finding the earliest (t, position) for each (bay, orient).
4. Select among (bay, orient) candidates lexicographically by (tardiness `max(0, t+dwell_i−D_i)`, spatial score). The spatial score preserves future capacity: maximize contact length (tight fit), apply **residence-time zoning** (long-stay blocks to walls/corners; short-stay blocks to the front), then prefer lower y.
5. Use `a + dwell_i` as exit (§2.5 local dominance plus the §2.6 `P_i=0` exception). When tardiness is unavoidable, escalate: expand anchors → attempt interlock (if S6 is enabled) → propose a bay bump to the upper level → accept delay.

**Multi-profile multi-start**: priority profiles {ascending slack, EDD, descending area×P, preference-pressure} plus weak biased randomization. Run multiple starts within ≤10% of timelimit (cap 30s) and pass the best to P3.

The construction performance target is **≤ 5 seconds** on 300-block instances; treat it as a hypothesis to recalibrate with S0/S1 microbenchmarks. Record the exact-predicate count per candidate as instrumentation.

### 4.4 Exact optimization layer: first-class Gurobi + complementary CP-SAT

**Input**: a fixed (position, orientation) layout in one bay. **Output**: the minimum-Σ-tardiness schedule permitted by that layout.

- Variables: `a_i ∈ [R_i, H]`, `e_i = a_i + max(P_i, 1)` (non-interlock; when interlock pairs exist, extend to variables `e_i ≥ a_i + max(P_i,1)` and add nesting constraints).
- Constraints: for every union-overlap pair, Bool `b_ij`: `b_ij ⇒ e_i ≤ a_j`, `¬b_ij ⇒ e_j ≤ a_i` (equality included — exploit same-day handoffs). Union-disjoint pairs have **no constraint**.
- Objective: `Σ T_i`, with `T_i ≥ e_i − D_i`, `T_i ≥ 0`. w1 is constant within a bay and may be omitted.
- **Gurobi path**: model separation with indicator constraints rather than explicit big-M, and provide current `a/e/b` as a MIP start. Default to `Threads=4`, `TimeLimit=min(5s, budget share)`, `MIPFocus=1`, `MIPGap=0`, and `OutputFlag=0`.
- **CP-SAT path**: express the same integer model with `OnlyEnforceIf` and provide the current solution as a hint. Use `num_search_workers=4` and the same timebox.
- **Never-worse guard**: accept a result only when it is strictly better than the current bay Z1, then full-check it. Ignore failure or timeout.

The correctness basis of both paths is the same §2.4 trichotomy. On the first retime sweep after construction, run a short pilot with the same timebox on at most the two bays with greatest tardiness; cap the total pilot at 50% of the first-sweep budget and split it evenly by backend and bay. Fix the instance’s retime backend by lexicographic `(improvement, time to first improvement, final objective)` comparison. If one backend is unavailable/errors/has no solution, use the other immediately. If both fail, retain the existing feasible schedule.

Initial bay assignment v2 is **Gurobi-first**. Solve one linear MIP with binary `x_ij`, exact-float `u_j·L_j`, `Wmax−Wmin`, preference loss, and a congestion soft cap. CP-SAT needs integer scaling and remains fallback; pass both results through the constructor and checker-measured objective before comparing them. This distinction deliberately uses Gurobi strongly for linear 0-1 assignment while selecting the engine for pure-integer disjunctive retiming from empirical performance.

Initialize Gurobi only once **after** registering T0 as incumbent. Catch import, environment, license, size-limit, and optimize exceptions inside the exact layer; instrument them as `backend_disabled_reason` and fall back to CP-SAT/heuristics. Do not share Gurobi model objects across calls; pass state only after extracting pure integer schedules/assignments.

### 4.5 LNS improvement loop (P3)

Attach move families to one acceptance loop. Every candidate follows targeted revalidation (§2.3, exact) → full check if improved → incumbent replacement.

| Neighborhood | Content | Target component |
|---|---|---|
| worst-tardiness reinsert | Remove the largest tardiness contributors plus k neighboring blocks in the “critical region” causing that tardiness → reinsert with the constructor | Z1 |
| congestion-window reinsert | Remove and reinsert blocks spanning a congested time window in one bay | Z1 |
| random-k | Reinsert a random small subset (escape) | Global |
| **bay-move / swap** | Select candidates using the exchange-rate table (small ΔS and a large block in a congested bay), reinsert into the target bay + retime both bays; compute closed-form Δ(w2·Z2 + w3·Z3) + measure Z1 | Z2, Z3, Z1 |
| exit-extension unblock | (Interlock/repair) delay a blocked exit until after the blocker exits; zero cost when `e ≤ D` | feasibility→Z1 |
| orientation polish | Change a single block orientation to create space | Indirect |
| retime call | Rerun that bay’s exact backend (Gurobi/CP-SAT) after accumulated changes | Z1 |

- Acceptance: strict improvement (float, relative eps 1e-9). If stagnation is observed, enable record-to-record with a linearly decreasing 3%→0 threshold.
- Destruction size k: start at `max(2, 3%·n)` and expand adaptively under stagnation.
- **Restart/diversification**: when a no-improvement interval exceeds ~15% of timelimit, perturb the incumbent and restart. This escapes local optima at large timelimits.
- Incremental objective (guidance): Z1 delta is the sum for changed blocks, Z3 delta is closed-form, and recompute Z2 in full because the number of bays is at most a few — all float-exact. The full-check value remains authoritative.

### 4.6 Assignment initialization (P1)

- v1 (greedy): assign sequentially in regret order to the argmin of a cost combining `w3·(S_i^max − S_ij)`, an approximate `w2`·Δrange, and a congestion penalty (w1-scale penalty when bay load ratio `ρ_j = Σ area_i·P_i / (W_j·H_j·horizon)` exceeds its threshold).
- v2 (gated, **Gurobi preferred**): minimize `w3·Σ pen + w2·(Wmax−Wmin) + λ·Σ overload`, with the fit matrix as a hard constraint and `Wmax/Wmin` linearizing the exact float load range. Retain the integer-scaled CP-SAT model as fallback. The model has thousands of n·m binary variables; determine its timebox empirically at the S4 gate.
- P3 bay moves handle the precise Z1 trade empirically (the initial assignment is only a starting point).

### 4.7 Interlock densifier (S6, gated, default off)

- Activation condition: after non-interlock optimization converges, a tardy block remains and its time window is confirmed to have no space.
- Candidate: stagger a guest (the tardy block) above hosts — use the cached OBS direction predicate and truth-table timing constraints (`a_host < a_guest`, `e_guest ≤ e_host`, preferably extend host exit only to `≤ D_host`); independently check every pair with third parties under the trichotomy.
- Net-gain condition: only when `w1·(T_guest decrease) − w1·(T_host increase) − (estimated subsequent-placement harm) > 0`.
- A serializer upgrade is mandatory (§4.8). Truth-table unit tests must come first.

### 4.8 Canonical serializer (single path)

- Rule: ascending dates; on the same date, all EXIT operations → all ENTRY operations.
- For ≥2 EXITs in the same bay on the same date: arbitrary order is safe in a non-interlock solution (§2.4 branch-2 theorem), so use block_id order. In an interlock solution use OBS topological order (guest first); implement a greedy topological sort that takes a remaining present block passing `check_exit(fast=True)` first, and reject the candidate on failure.
- ENTRY order is arbitrary only because every combination whose concurrent same-day entry is legal is union-disjoint, in which case order is irrelevant (§2.4).
- This module alone creates the operations dict. Do not assemble one manually.

### 4.9 Parallel portfolio (S5, gated, default off)

- Primary parallelism comes from workers inside the selected exact backend (Gurobi `Threads=4` or CP-SAT workers=4). Secondary parallelism uses 3 worker processes (different seeds/profiles) plus an orchestrator with periodic best exchange. Force each process’s Gurobi/CP-SAT thread count to 1. Assume Linux fork, join timeout, and worker death = ignore (the whole system still completes from the single-process result). Enable only when concurrent-license capacity and wall-clock gain are proven in a server-like environment.

### 4.10 Memory and determinism

- Bound the cache LRU (decision cache ~several million entries; polygons once per (block, orient)). Confirm margin within the 16GB limit through S0 memory instrumentation.
- Fix the seed. With parallelism off, tests require complete reproducibility.

---

## 5. Implementation roadmap (remain submission-ready after every stage)

| Stage | Content | Gate (do not proceed if it fails) |
|---|---|---|
| **S0 foundation** | Semantic contract tests (all §2 theorems/truth table/edges), geometry kernel + cache, canonical serializer, T0 + incumbent + budget armor, microbenchmarks (measure predicate cost) | 100% feasible on all training instances at 5s; contract tests green; predicate-cost table produced |
| **S1 constructor** | Insertion construction + multi-profile + P1 v1 assignment | 300-block construction ≤ 5s (empirical recalibration allowed); every block placed; large improvement over T0; 100% feasible |
| **S2 exact retiming** | Gurobi indicator-MIP + isomorphic CP-SAT model + pilot selector + never-worse guard | Z1 non-worsening on 100% of instances; median improvement > 0; backend timeboxes met; forced Gurobi failure successfully falls back to CP-SAT |
| **S3 LNS** | Destroy-repair + retime loop, anytime | Monotonic improvement curve; 300s result ≥ 60s result (zero deterioration); structural evidence of acceptance count > 0 |
| **S4 assignment refinement** | Bay-move/swap + Gurobi v2 initial assignment (CP-SAT fallback) | Improvement on a subset of high-w2/w3-profile instances; non-regression elsewhere; checker-float Z2 non-worsening |
| **S5 parallel portfolio** | Process portfolio (gated) | Enable only after proving wall-clock gain and zero crashes in a server-like environment |
| **S6 interlock + hardening** | Densifier (gated) + submission packaging, stress, report draft | Enable only when dense subset improves; every stress-matrix item feasible |

If time is short, **S0–S3 are the competitive core**. S4 and S6 add profile-dependent points, while S5 is a multiplier.

---

## 6. Verification and measurement protocol

1. **Contract tests** (S0, written before implementation): every §2.4 truth-table row, arbitrary-order safety for union-disjoint pairs, legal same-day handoffs, `P=0` residence≥1, legal exit extension plus Z1 effect, and obj2 float (no flooring) parity — all through synthetic small instances and the `check_feasibility` oracle.
2. **Parity tests**: internal delta-objective vs checker (relative ±1e-6); targeted revalidation vs full check (matching decisions over 1,000 random candidates).
3. **Benchmark matrix**: all training instances × timelimit {10s, 60s, 300s} (periodically 1800s); metrics: feasible rate (=100% mandatory), objective, time by stage, acceptance/iteration counts, retiming gain, cache hit rate, and backend-specific build/solve time, status, gap, and time to first improvement. Build an instrumentation runner starting from the existing baseline scripts (`baseline/run_baseline_greedy.py`, `baseline/run_myalgorithm.py`).
4. **Discipline for improvement claims**: every improvement claim includes structural evidence (acceptance counts, time by stage) and A/B (feature flag on/off, same commit and seed). Do not accept/reject based on deltas within rerun noise bands.
5. **Stress**: timelimit {0.5s, 2s, 5s}; one bay; single-layer instances; blocks physically unable to fit their preferred bay; and large n. Every case must return feasible.
6. **Submission rehearsal** (S6): zip root `myalgorithm.py`, no absolute paths, no modifications to `utils.py`, and isolated execution of all training + stress instances with all-feasible results — every item of the specification §5.1 checklist.

---

## 7. Risk register

| Risk | Mitigation |
|---|---|
| shapely predicates remain the bottleneck | Budget from S0 measurements; prepared+cache+AABB batch; if still insufficient, conservative raster (parity gate) |
| Gurobi license/environment/model-size exceptions | Lazy probe after T0 registration; isolate exceptions in exact layer; automatic CP-SAT/heuristic fallback; submission-environment smoke test |
| One exact backend stagnates on a particular bay | Same-model pilot + per-instance fixed backend; timebox + warm start/hint + never-worse guard |
| Interlock implementation defects | Default off; truth-table tests first; discard candidate if serializer topological ordering fails |
| Multi-process/thread oversubscription | Default off (S5 gate); single-process exact solve uses threads=4, while 3-process mode uses one backend thread per process |
| Extreme timelimits (short/long) | Short: T0+construction path must pass stress; long: diversification through restart consumes budget |
| Hidden-instance distribution shift | No hard-coding (P4); derive every threshold from weights, size, and load ratio; cover edge catalog (§2.6) |
| Memory | Bounded cache LRU; reuse temporary objects during candidate tests |
| Float tie handling | Acceptance is strict `<` (relative eps 1e-9); report the checker value unchanged |

---

## 8. Regression-prevention protocol (permanent sealing of five past errors)

| # | Past error | Permanent safeguard in this document |
|---|---|---|
| a | Reversed interlock exit-condition direction | Derive the §2.4 truth table from utils.py anchors; S0 contract tests pin every row with the oracle |
| b | Inserting floor into Z2 | §2.2 explicitly states float (utils.py:1409-1419); objective-parity test is mandatory |
| c | Globally fixing exit = entry + P | §2.5: dominance is only local to non-interlock; e is a first-class lever in interlock/repair |
| d | Underestimating same-day operation order | §2.1/§2.4 specify Stage-5 list-order replay and Stage-2 inclusion of concurrent entry; one canonical serializer path |
| e | Claiming “integer custom geometry is exact” | §1 principle 4/§4.2 allow only decision caching (storage) and conservative prefiltering; shapely is the sole predicate authority |

General principle: do not put directional rules into code before truth-table + oracle tests. Semantic descriptions carry `utils.py:line` anchors; when an anchor is invalidated, update this document in the same change. Do not create operations dicts outside the canonical serializer.

---

## 9. Appendix

### 9.1 Default parameters (all config; must be re-derivable from instance data)

| Parameter | Default | Note |
|---|---|---|
| Time-candidate cap T | 8 | Expand during escalation |
| Anchor cap K | 32 | Escalate 16→48 |
| Multi-start profiles | 4 types + biased randomization | Construction budget ≤ 10%·TL (cap 30s) |
| LNS destruction size | max(2, 3%·n), ×1.5 under stagnation | |
| Retime backend | `auto` | Fix per instance after first-sweep Gurobi/CP-SAT pilot |
| Retime timebox | min(5s, 10%·remaining) / bay | Gurobi MIP start / CP-SAT hint, threads=4 |
| Assignment backend | `gurobi` | CP-SAT on unavailable/error; greedy v1 if both fail |
| Acceptance | Strict improvement; RRT 3%→0 under stagnation | Flag |
| Restart trigger | No improvement for 15%·TL | Restart after perturbation |
| reserve | clamp(5%·TL, 3s, 60s) | Deadline protection |
| Decision-cache cap | 2^21 LRU entries | Tune after measurement |

### 9.2 Cost estimates (to be replaced by S0 measurements)

| Operation | Estimate | Verification method |
|---|---|---|
| prepared `intersects` | 5–20µs | S0 microbenchmark |
| Intersection-area calculation | 50–500µs | 〃 |
| One insertion (average) | 5–20ms (≤ 50 exact predicates) | Instrumentation field |
| Whole construction (n=300) | ≤ 5s | S1 gate |
| One LNS iteration | 50–200ms | Instrumentation field |
| Bay retime (Gurobi/CP-SAT) | 1–5s per backend | S2 pilot and per-backend instrumentation |
| Full check (n=300) | ~0.5–1.5s | Only when accepting; instrument |

### 9.3 Relationship to other documents

- `docs/fable/solver-implementation-plan.md` owns implementation-level specification (modules, data structures, ALNS destroy/repair, SA acceptance criterion, isomorphic Gurobi/CP-SAT models, backend selection, parameters, and test list). If it conflicts with this document, §2 of this document (checker semantics) takes priority; otherwise, the implementation details take priority in the implementation specification.
- This document supersedes `docs/deprecated/m2-m4-execution-plan.md` (the legacy code-improvement track) as design authority. Its evaluator-semantic contract was confirmed not to conflict with this document’s §2 (both derive from the same source).
- Operational practices such as benchmark-set and experiment-log discipline continue to follow the formats of `docs/deprecated/operating-plan.md` and `docs/deprecated/m2-experiment-playbook.md`.
- Keep the existing `baseline/baseline_greedy.py` frozen as reference implementation and comparison baseline. Implement this design as a new module family (recommended under `solver/`: `geometry.py`, `constructor.py`, `exact.py`, `gurobi_backend.py`, `cpsat_backend.py`, `retime.py`, `lns.py`, `serialize.py`, `budget.py`) to which `myalgorithm.py` delegates. Retain the M1 safety shell (verify-before-return).
