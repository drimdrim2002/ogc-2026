# 타이핑용 변환 노트 (원본: fable-plan-2026-07-04.md)

이 파일은 손으로 타이핑하며 원문을 읽기 위해, 키보드로 치기 어려운 유니코드 기호를
쉬운 ASCII 표기로 바꾼 사본입니다. 내용은 원본과 100% 동일하며, 기호만 아래 표대로
치환했습니다. 원문과 대조하며 보고 싶으면 `docs/fable-plan-2026-07-04.md`를 같이 여세요.

| 원본 기호 | 의미 | 변환 표기 |
|---|---|---|
| `Σ` | 합(sum) | `sum` |
| `ᵢ` (아래첨자 i) | 인덱스 i | `_i` (예: `Σᵢ` → `sum_i`, `Pᵢ` → `P_i`) |
| `×` | 곱셈/차원 표시 | `x` |
| `·` | 곱셈(가운뎃점) | `*` |
| `−` `–` `—` | 마이너스/하이픈/줄표 | `-` (단, em dash `—`는 `--`) |
| `≤` `≥` | 이하/이상 | `<=` `>=` |
| `⊆` | 부분집합 | `<=` (문맥상 "포함된다"는 뜻) |
| `≈` | 근사 | `~` |
| `≠` | 같지 않음 | `!=` |
| `∩` | 교집합 | `&` |
| `∅` | 공집합 | `{}` |
| `→` | 화살표(귀결) | `->` |
| `⇒` | 함의(그러면) | `=>` |
| `§` | 절(section) 번호 | `sec ` (예: `§3.1` → `sec 3.1`) |
| `¹` | 각주 표시 | `[1]` |
| `⁴` `⁵` | 위첨자 지수 | `^4` `^5` |
| `Δ` (Δx, Δy) | 변화량 | `dx`, `dy` |
| `α` `β` `γ` | 그리스 문자 | `alpha` `beta` `gamma` |
| `…` | 줄임표 | `...` |
| `─ │ ┌ ┐ └ ┘ ├ ┤ ┬ ▼ ◄ ►` | 아키텍처 다이어그램 상자 선/화살표 | `- \| + + + + + + + v < >` (문자 수 1:1 유지, 정렬 그대로 보존) |

---

# OGC 2026 -- Algorithm Development Plan

**Author:** Claude (Fable 5), written from first principles on 2026-07-04.
**Inputs:** problem statement analysis (`OGC2026_Problem_Analysis.md`), `baseline/` code, all 40 training instances in `data/`.
**Goal:** maximize total leaderboard score = per-instance rank score, where infeasible/TLE/crash = **-1**. Corollary: *a feasible mediocre solution on every instance beats a brilliant solution that fails once.* Feasibility and anytime behavior are the spine of this plan; objective quality is built on top of that spine.

---

## 1. What the data actually says (measured, not assumed)

All 40 training instances parse cleanly. Two clearly distinct regimes:

| | Set 1 (prob 1-20) | Set 2 (prob 21-40) |
|---|---|---|
| Blocks n | 100-300 | 100-250 |
| Bays m | 2-5 | 2-4 |
| Space-time congestion[1] | **0.14-0.29** (loose) | **0.29-0.80** (very tight) |
| Due-date slack (D-R-P), avg | **~1.2-1.6 days** (near zero) | 2.2-5.4 days |
| Processing times | 3-13 | 5-31 |
| Layers K | mostly 1-2, some up to 4 | same |
| Orientations O | 2-8 | 2-8 |
| Polygon vertices | 3-12 (non-convex allowed) | same |
| Horizon | <= ~150 days | <= ~130 days |

[1] congestion = sum_i (max-layer area x P_i) / (total bay area x horizon). At 0.8 (prob_38), with irregular shapes, near-perfect packing density is required just to avoid massive tardiness.

**Regime implications**
- **Set 1 is a scheduling problem.** Slack ~ 1 day means nearly every block must enter at (or within a day of) its release to be on time. Space is not scarce; the solver must simply never delay a block for a bad spatial reason. Tardiness is the whole game (w1 huge).
- **Set 2 is a packing problem.** Space is the binding constraint; some tardiness is unavoidable at congestion >= 0.5. The game is (a) packing density, and (b) *choosing which blocks to be late* -- i.e., weighted-tardiness triage, not "everyone on time".
- **Weights vary an order of magnitude across instances** (w1 from 667 to 29,630; w3 from 13 to 600). The exchange rate "1 day of tardiness ~ (w1/w3) preference points" ranges from ~5 (prob_32: preference really matters) to ~150 (prob_1: preference is a tiebreak). The solver must read weights per instance, never hardcode priorities.

**Baseline reference points (measured on this machine, ogc2026 env)**
- prob_1 (easy): feasible, obj = 795,089 (obj1 = 26 days tardiness -> 95% of objective), 37 s.
- prob_27 (congestion 0.77): **infeasible** (stage-2 sweep collisions survive repair) and **247 s spent on a 60 s budget** -- the baseline both ignores the time budget and cannot handle dense instances.

So the two first-order wins are: a geometry engine ~100x faster than naive shapely loops, and a constructor that is *crane-aware from the start* instead of repair-based.

---

## 2. Structural insights that shape the algorithm

These five observations, all verifiable against `utils.py` semantics, compress the problem substantially:

**(a) EXIT = ENTRY + P is weakly dominant.** Delaying an exit only occupies space longer and can only increase tardiness. The only reason to delay an exit is a crane obstruction (another block overhangs it), which is itself a consequence of placement. Therefore the real decision vector per block is **(bay, x, y, orient, ENTRY)** -- exit is derived, except where interlocking (below) imposes ordering. Tardiness becomes `max(0, ENTRY + P - D)`, and each block has a hard "latest safe entry" `LSE = D - P`.

**(b) "Independence packing" makes crane constraints vanish.** The j >= k sweep rule (both directions) means: if two co-present blocks' *footprint unions* (union over all their layers) have disjoint interiors, then entry and exit are always unobstructed, in any order. A solver that enforces footprint-union disjointness needs **zero** crane checks -- the problem reduces to dynamic 2D irregular packing with integer offsets. This is the safe, fast core mode.

**(c) Interlocking = density bonus + LIFO DAG.** Overlap is allowed when block A's layer k overlaps block B's layer j only for j < k (A overhangs B). Statics and A's entry are fine, but **B cannot exit while A is present** (check_exit hits A's layer j >= B's layer k). So every overhang A->B creates a precedence "A exits before B" (same-day is OK if A's EXIT is listed first). Interlocking is therefore an *optional* density lever that adds a DAG of exit orderings -- valuable exactly in set-2 instances, dangerous if applied blindly. With K <= 2 for most blocks, the practical pattern is "cantilever a 2-layer block's top layer over a 1-layer neighbor whose exit is later or tied".

**(d) Same-day batching is a free sequencing lever.** Stages 2/3 of the checker use strict inequalities (same-time events don't see each other); stage 5 replays each day as *exits in listed order, then entries in listed order*, checking each op against the current bay state. So within one day we can: exit A then exit B (where A blocked B), and enter C into the space A vacated. The solution writer must topologically order same-day exits (overhanging blocks first) and same-day entries (overhung/lower blocks first).

**(e) Everything is small in the time dimension.** Integer days, horizon <= ~150, n <= 300. Time-indexed structures (per-bay list of present blocks per day) are trivially affordable. The spatial dimension is also small: bays <= ~180 x 30 integer cells, placements at integer (x, y) -- a full grid scan per placement is ~5,000 candidate anchors x cheap tests, if the per-candidate test is O(1)-ish.

---

## 3. Architecture

```
                    +--------------------------------------------+
                    |  instance profiler (regime, weights, sizes)|
                    +---------------+----------------------------+
                                    v
   +-------------+   +--------------------------+   +-----------------------+
   | geometry     |<--+ constructor (crane-aware |-->| best-feasible archive |
   | engine       |   | event-driven greedy)     |   | (anytime, always      |
   | (2-tier:     |   +--------------------------+   |  serializable)        |
   | raster +     |<--+ ALNS improvement loop    |-->|                       |
   | exact,       |   | (destroy/repair/SA)      |   +----------+------------+
   | C++ core w/  |   +--------------------------+              v
   | Py fallback) |<--+ interlock densifier      |   +-----------------------+
   +-------------+   | (set-2 only, optional)   |   | final utils.check_    |
                      +--------------------------+   | feasibility gate +    |
                                                     | guaranteed fallback   |
                                                     +-----------------------+
```

Four processes (4-core budget): one orchestrator + 3 worker seeds running the constructor+ALNS portfolio with different randomization/configs; orchestrator merges best solutions and reallocates budget. Portfolio parallelism is chosen over intra-search parallelism because it is simpler, restart-diverse, and immune to GIL issues.

### 3.1 Geometry engine (the force multiplier)

Everything downstream is bounded by "how many placement candidates can I test per second". Design:

- **Tier 1 -- occupancy raster.** Per (block, orientation, layer), pre-rasterize the polygon onto the unit grid as two bitmasks: *full cells* (cell <= polygon) and *touched cells* (cell & polygon != {}). Per (bay, day, layer) maintain the same two masks aggregated over present blocks. Then for a candidate anchor:
  - touched(new) & full(existing) = {} **and** full(new) & touched(existing) = {} and touched&touched = {} -> cases: touched-only overlap => *uncertain*; any overlap involving a full cell => *definitely colliding*; no touched overlap => *definitely free*.
  - Bitwise AND over 64-bit words makes one candidate test ~microseconds; a whole-bay scan for one orientation is a 2D sliding-window convolution, vectorizable.
- **Tier 2 -- exact check.** Only *uncertain* candidates (near-contact placements) go to exact polygon intersection with `area > 0` semantics identical to `utils.py`. Exactness matters: contact is legal, 4-decimal vertices mean touched-cell overlaps are common at tight packings, and tight packings are precisely what set 2 needs.
- **Implementation:** C++ (pybind11) with integer-scaled (x10^4) polygon clipping (Clipper2 or hand-rolled convex-decomposition intersection) for tier 2, and raw bitset ops for tier 1. **Mandatory pure-Python/numpy fallback** with the same API (numpy bool arrays + prepared shapely for tier 2), selected by try/except import at startup -- a `.so` load failure on the server must degrade performance, never correctness.
- **Caching:** memoize pairwise offset-feasibility per (shapeA, orientA, shapeB, orientB, dx, dy) for pairs revisited by local search; memoize per-shape rasters and shapely prepared geometries (utils already caches polygons; reuse it in the exact tier for parity).
- **Parity harness:** a test that samples thousands of random placements and asserts engine verdict == `utils.check_entry/check_exit/check_collisions` verdict. Run in CI on every engine change. Any disagreement is a release blocker -- the evaluation server *is* utils.py.

### 3.2 Constructor: crane-aware, event-driven greedy

Simulate days t = 0...T. At each day:
1. **Exits:** emit EXIT for every block with scheduled exit = t, in DAG-topological order (overhanging first). Default schedule exit = entry + P.
2. **Entries:** consider released, unplaced blocks ordered by priority = `w1-weighted urgency (LSE - t), then workload/size` (big blocks are harder to place late -- place early). For each block:
   - **Bay choice:** score = alpha*(space availability at t..t+P, from raster free-area) + beta*w3*preference loss + gamma*w2*load-imbalance delta. alpha/beta/gamma set from instance weights (the exchange-rate logic of sec 1).
   - **Position/orientation:** scan candidate anchors from the raster engine over a *time-aggregated* occupancy mask (union of occupancy over [t, t+P) -- a position must be free for the whole stay; with independence packing this is exactly one 2D test against the union mask). Score positions by a packing heuristic: minimize wasted enclosure (bottom-left-ish with skyline/contact bonus), and **exit-time zoning** -- long-staying blocks toward bay interior/corners, short-staying blocks near open space, so future entries don't need corridors (with independence packing exits never block; zoning instead protects *future placeability*).
   - If no position exists at t: defer (try next day). If `t > LSE`, the block is going tardy -- switch its priority to weighted-tardiness triage (cheap-to-delay blocks wait; expensive ones get first claim on space; consider evicting choice of *bay* rather than time).
3. Track per-bay load for obj2 incrementally; obj3 from bay choice.

This constructor alone, with the fast engine, should produce in a few seconds what the baseline can't do in minutes, and it is feasible *by construction* (independence packing + ordered same-day ops), needing no repair phase. Target: 40/40 feasible in < 10 s each.

### 3.3 Improvement: ALNS over (sequence, assignment) with the constructor as decoder

Encoding: a priority sequence + per-block (bay, orientation) hints. The decoder is the event-driven constructor honoring hints when feasible. Score = exact objective (cheap to compute incrementally: obj1 from entries, obj2/obj3 closed-form).

- **Destroy operators:** (i) worst-tardiness blocks + their spatial neighbors at the congestion window; (ii) random time-window slice of one bay; (iii) bay-pair exchange candidates (targets obj2/obj3); (iv) Shaw-style relatedness (similar size + overlapping time).
- **Repair operators:** greedy reinsert with regret-2/3 over (bay, orient, entry-day, position); "squeeze" reinsert that allows one-day entry shifts of neighbors.
- **Acceptance:** simulated annealing on total objective, temperature scaled to w1 (one day of tardiness must be acceptable early, not late); adaptive operator weights (classic ALNS bookkeeping).
- **Anytime:** every improved feasible solution is serialized to the archive immediately (in-memory + the orchestrator keeps the global best).

Local moves worth having besides ALNS: single-block re-opt (best position/orient/day for one block, exact), bay-swap of block pairs (obj3/obj2), and entry-day left-shift sweeps (pull every block as early as space allows -- pure obj1 gain).

### 3.4 Interlock densifier (set-2 lever, gated)

Post-pass (and later, an ALNS repair variant) that proposes overhang placements: when a block fails to fit at day t under independence packing, retry allowing its upper layers (k >= 1) to overlap layer-0 of neighbors whose exit <= its exit (DAG edge points the safe way; verify with the exact engine's full j >= k check both directions). Maintain the exit-precedence DAG; the solution writer already knows how to order same-day ops. Cycle = reject. This is strictly additive: if it never fires, we still have the independence solution. Gate: enable only when instance congestion > ~0.35 and only after M2 metrics prove it pays (it's the most bug-prone component; its failure mode must be "not applied", never "infeasible").

### 3.5 Matheuristic (optional, evidence-gated)

Two bounded uses of CP-SAT (ortools is available; Gurobi license on server is sponsored but CP-SAT is safer/portable):
1. **Entry-day re-timing:** fix (bay, x, y, orient) for all blocks; optimize integer entry days under pairwise no-overlap-in-time constraints for spatially-conflicting pairs (a disjunctive scheduling core). Catches coordinated multi-block shifts ALNS finds slowly.
2. **Bay reassignment:** fix relative packings as "patterns"; MIP over which pattern/bay pairs to select for obj2/obj3 with obj1 estimates.

Only pursue after M2 if profiling shows ALNS plateaus with budget left. Not on the critical path.

### 3.6 Robustness & anytime harness (non-negotiable)

- **Budget manager:** reserve ~8% of `timelimit` (min 20 s at large limits, min 5 s at 60 s) for: final `utils.check_feasibility` on the incumbent, serialization, and fallback. Constructor gets a hard slice; ALNS consumes the middle; hard wall-clock aborts via cooperative checks (no signals -- must be firejail/cpulimit-safe).
- **Guaranteed-feasible fallback:** a trivial serializer that schedules blocks one-at-a-time (each block alone in its best bay, sequentially in time). Always feasible (every block fits alone in some bay), computed in milliseconds at startup, so the answer is never -1 even if everything else dies. Order of preference at deadline: best ALNS incumbent -> constructor solution -> trivial fallback.
- **Final gate:** never return a solution that hasn't passed local `utils.check_feasibility`. If the incumbent unexpectedly fails (engine parity bug), fall back down the chain.
- **Exception hygiene:** top-level try/except in `algorithm()` returning the last validated solution; no prints in submission mode; relative paths only; imports restricted to the provided package list; memory kept well under 16 GB (rasters are megabytes, not gigabytes).

---

## 4. Instance-adaptive policy

At startup, the profiler computes: congestion ratio, slack distribution, weight exchange rates, bay count/geometry, layer-count histogram. It selects:
- **Set-1 profile** (congestion < ~0.3, slack <= ~2): constructor prioritizes strict release-time entry; ALNS spends most budget on obj1 left-shifts and obj3/obj2 polishing (since obj1 may reach near-zero); interlocking off.
- **Set-2 profile:** weighted-tardiness triage on; interlock densifier on (post-M3); ALNS destroy ops biased to congestion windows; obj3 given weight per exchange rate.
- Unknown/hidden instances may sit between profiles -- all thresholds continuous, not branchy, so behavior degrades gracefully. Hidden instances may also be *larger* (n > 300 or > 5 bays): all data structures sized dynamically; complexity kept near-linear in n x horizon; a "panic profile" (constructor-only + left-shift polish) triggers if construction hasn't finished by 40% of budget.

---

## 5. Milestones, exit criteria, and evaluation protocol

**Evaluation harness first (M0, ~2 days).** A runner that executes `algorithm()` on all 40 instances at configurable timelimits (60/300/900/1800 s), in a firejail/cpulimit-emulating wrapper (4 cores, 16 GB via `taskset`/`ulimit`), records per-instance (feasible, obj, obj1/2/3, wall time, peak RSS) into a JSONL scoreboard with git-SHA tags, and prints regressions vs. the previous run. Every subsequent claim of progress must cite this scoreboard. Include the parity harness (sec 3.1) and 3-seed variance reporting.

| Milestone | Content | Exit criteria (measured on the harness) |
|---|---|---|
| **M0** (days 1-2) | Harness, parity tests, trivial fallback, budget manager skeleton | 40/40 runs complete inside budget; fallback feasible 40/40 |
| **M1** (week 1) | Geometry engine (numpy tier-1 + exact tier-2 first; C++ port after), crane-aware constructor | 40/40 feasible **including prob_27/38**; each construction < 10 s; obj <= baseline obj on all instances where baseline is feasible; engine parity 0 disagreements on 10^5 samples |
| **M2** (weeks 2-3) | ALNS + local moves + portfolio parallelism + anytime archive | >= 30% mean obj improvement over M1 at 300 s; monotone anytime curve (obj at 60 s <= obj at 300 s <= ...); 0 infeasible over 3 seeds x 40 instances |
| **M3** (week 4) | Interlock densifier; C++ core if not done; optional CP-SAT re-timing; per-regime tuning | measurable set-2 gains (target >= 10% on congestion > 0.5 instances) with 0 feasibility regressions; kill any component that doesn't pay |
| **M4** (final week) | Hardening: server-parity dry runs (Ubuntu 24.04, conda env, zip layout, .so built on target), submission checklist, seed freeze | zip validated locally end-to-end from a clean directory; 3-seed x 4-timelimit full sweep green; submission dry-run doc |

**Submission cadence:** the 12 h cooldown starts at *approval*, so every submission must be locally validated to be un-rejectable (zip structure, root-level `myalgorithm.py`, unmodified `utils.py`, <= 15 MB, no suspicious extensions). Submit early with M1 (feasibility everywhere already beats baseline-class competitors on set 2), then on each milestone. Keep a frozen "last known good" zip at all times.

**Report/presentation (final stage counts!):** log every design decision + ablation in `docs/experiments.md` as we go (what was tried, scoreboard delta, kept/killed). The structural analysis of sec 2 (independence packing, LIFO DAG, same-day batching semantics) is original-methodology material -- write it up properly.

---

## 6. Risks and mitigations

| Risk | Mitigation |
|---|---|
| `.so` incompatible with server (glibc/py-ABI) | build on Ubuntu 24.04 + exact conda env; pure-Python fallback path always shipped and tested to be feasible (slower, never wrong) |
| Engine/utils semantic drift (the -1 killer) | parity harness in CI; final gate always runs real `utils.check_feasibility`; fallback chain |
| Unknown time limit (minutes -> 30 min) | strictly budget-driven control flow off the `timelimit` arg; anytime monotonicity tested at 4 budgets |
| Hidden instances harder/larger than training | continuous adaptive profile, near-linear scaling, panic profile, trivial fallback |
| Interlocking DAG bugs -> stage-3/5 infeasibility | gated feature; exact both-direction verification per proposal; property tests replaying stage-5 semantics; ship-disabled switch |
| cpulimit throttling skews internal timing | measure wall clock, not CPU time; cooperative deadline checks in inner loops |
| Over-investment in matheuristics | evidence gates: components must beat ALNS-only scoreboard at equal budget or be deleted |

---

## 7. Immediate next actions (M0 kickoff)

1. Build the 40-instance harness + scoreboard (`bench/run_bench.py`), wire baseline as the first tracked entrant.
2. Implement the trivial guaranteed-feasible fallback and the budget manager skeleton inside a new `solver/` package with the `algorithm()` entrypoint.
3. Implement tier-1 numpy raster engine + tier-2 shapely-parity exact check + the parity test.
4. Start the crane-aware constructor on independence packing; first target: prob_27 and prob_38 feasible in < 10 s.
