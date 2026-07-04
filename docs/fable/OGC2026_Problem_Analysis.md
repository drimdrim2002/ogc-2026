# Optimization Grand Challenge 2026 (OGC 2026) Problem Analysis
### "The Grand Shipyard Puzzle: Pack the Block, Beat the Clock" — Detailed Analysis Report

> Source: OGC 2026 Organizing Committee, 2026-06-15 (Document Version 1.2)

---

## 1. Competition Overview

OGC 2026 is an optimization competition dealing with the **block placement and scheduling problem of a shipyard**. The essence of the problem is that three sub-problems are intertwined simultaneously.

| Sub-problem | Description |
|---|---|
| **Assignment** | Which bay each block should be placed into |
| **Packing/Layout** | Where in the bay (position) and in which orientation to place it |
| **Scheduling** | When to bring it in (ENTRY) and when to take it out (EXIT) |

In other words, it can be summarized as a **compound combinatorial optimization problem that simultaneously solves a cutting-stock/packing problem for 2D/3D irregular shapes + a parallel-machine scheduling problem + resource (space) constraints**. Because these three elements are not independent but interact with one another (e.g., if a bay is full, ENTRY is delayed → causing delivery delays), the difficulty is very high.

---

## 2. Problem Definition (Section 1)

### 2.1 Key Terminology

| Symbol | Term | Definition |
|---|---|---|
| $M=\{1,...,m\}$ | Bay | A fixed workspace of size $W_j \times H_j$ |
| $N=\{1,...,n\}$ | Block | A 3D structure consisting of $K_i$ polygon layers, with $O_i$ orientation options |
| $R_i$ | Release time | The earliest time at which block $i$ can start being processed |
| $D_i$ | Due date | The desired completion time for block $i$ |
| $P_i$ | Processing time | The time required to process block $i$ |
| $ENTRY_i$ | Entry time | The time at which the block is placed in the bay |
| $EXIT_i$ | Exit time | The time at which the block leaves the bay |
| $T_i$ | Tardiness | $T_i = \max(0, EXIT_i - D_i)$ |
| $L_i$ | Workload | The total amount of work required to process the block |
| $S_{ij}$ | Preference | The score indicating how much block $i$ prefers being assigned to bay $j$ |

### 2.2 Summary of the Problem Setting

- A single bay can accommodate **multiple blocks simultaneously** — provided they do not spatially overlap.
- Each block has its own unique $R_i, P_i, D_i$, so a feasible solution requires **considering spatial placement and temporal scheduling simultaneously**.

### 2.3 Bay Structure and Crane Operation Rules (Very Important Details)

- Each bay has an **independent crane**, so operations across bays do not interfere with one another.
- A list of crane operations is given on a **daily** basis, and **multiple operations (ENTRY/EXIT) per day** are allowed (no limit on the number).
- **Within the same day, all EXIT operations must be performed before all ENTRY operations.** (The order among operations of the same type can be freely determined by the algorithm.)
- Example: with processing time 3, an ENTRY on day 5 → EXIT can only happen on **day 8** at the earliest (processing occurs on days 5, 6, 7 — i.e., $EXIT_i - ENTRY_i \ge P_i$).
- A block **occupies space during the half-open interval [ENTRY, EXIT) while placed in the bay** (note the half-open interval).

### 2.4 Block Structure

- Each block is a 3D structure composed of $K_i$ **polygon layers** (which may be non-convex), and all layers are assumed to have the same height.
- There are no "empty layers," and adjacent layers must always be physically attached (no "floating" layers allowed).
- Each block is given a **predetermined number of orientation options $O_i$** (not free rotation, but a choice among discrete candidate orientations).
- Each block has an $L_i$ (workload) and $S_{ij}$ (per-bay preference score).
  - $L_i$ is used in the objective function ($Z_2$) that minimizes the workload imbalance across bays.
  - $S_{ij}$ is used in the objective function ($Z_3$) that optimizes preference.

### 2.5 Decision Variables (Section 1.5)

For each block $i$, four things must be decided:
1. The assigned bay $j$
2. The position $(x, y)$ within the bay
3. The chosen orientation
4. $ENTRY_i$, $EXIT_i$

### 2.6 Constraints (Section 1.6)

#### (1) Assignment Constraints
- **Every block must be assigned to exactly one bay** (no unassigned blocks allowed).
- Every block has **exactly one ENTRY and one EXIT** operation. **Once a block is placed, it cannot be moved or rotated** (no relocation).

#### (2) Temporal Constraints
- **Release constraint**: $ENTRY_i \ge R_i$
- **Processing-time constraint**: $EXIT_i - ENTRY_i \ge P_i$
- Note: since $D_i - R_i > P_i$ typically holds, there is "slack" in scheduling → creating room for coordination with other blocks.
- If $EXIT_i > D_i$, tardiness occurs.

#### (3) Spatial Constraints — The Most Complex Part
- **Collision definition**: A collision occurs when the **interiors** of layers at the same level of two blocks overlap. However, **sharing a boundary (edge) is not considered a collision** (contact is allowed).
  $$C(\cdot) = \begin{cases}0 & \text{if } int(\cdot)\cap int(\cdot)=\emptyset\\1 & \text{otherwise}\end{cases}$$
- $N(t,j)$ := the set of blocks actually present in bay $j$ at time $t$ ($ENTRY_i \le t < EXIT_i$).

| Constraint Name | Description |
|---|---|
| **Bay-containment constraint** | The block must fit entirely within the boundary of its assigned bay |
| **Layer-collision-avoidance constraint** | Two blocks in the same bay at the same time must have no collisions **between layers at the same level** (checked up to level $l=1,...,\min(K_{i1},K_{i2})$). Interestingly, **even if the blocks appear to overlap when viewed from above (top view)**, there is no problem as long as there is no actual overlap at the same layer level (see Figure 7(a) — a case where blocks meet only at different, staggered layers) |
| **Crane-operation constraint** | When a block is brought in/out (moved vertically), its layers must not collide with the layers of another block that are at the **same or a higher level**. This reflects the actual physics of a crane — "when lowering or lifting vertically from above, it must not catch on another block" |

> **Key Insight**: The spatial constraint is not a simple 2D bin-packing problem, but a **quasi-3D packing problem that allows overlap by layer (by height)**. Even if two blocks appear to overlap in top view, this is fine statically as long as they only meet at different layers (layer-collision-avoidance); however, when inserting/removing with the crane, the "vertical passage" must not be blocked (crane constraint) — these two conditions must therefore be checked separately.

### 2.7 Objective Function (Section 1.7)

Minimize a weighted sum of three objectives:

$$\min\; w_1 Z_1 + w_2 Z_2 + w_3 Z_3$$

| Term | Name | Formula | Meaning |
|---|---|---|---|
| $Z_1$ | Total tardiness | $\sum_i T_i$ | Due-date compliance performance |
| $Z_2$ | Workload imbalance | $\displaystyle\Big\lfloor \max_{j_1 \neq j_2}\Big\vert u_{j_1}\sum_{i\in N(j_1)}L_i - u_{j_2}\sum_{i\in N(j_2)}L_i\Big\vert \Big\rfloor$ | The maximum deviation of normalized workload between bays (weighted by bay area via $u_j$) |
| $Z_3$ | Preference loss | $\sum_{j}\sum_{i\in N(j)} (S_i^{max} - S_{ij})$ | The total loss relative to each block's most-preferred bay (if every block is assigned to its most-preferred bay, $Z_3=0$) |

- $u_j = \dfrac{\text{average area across all bays}}{W_j \times H_j}$ → **the larger the bay, the smaller the weight** (reflecting the idea that concentrating workload in a large bay results in lower congestion).
- The weights $w_1, w_2, w_3$ are provided within the instance JSON (example: $w_1=26667, w_2=10, w_3=300$ — **note that because the scale differences between the weights are very large, tardiness tends in practice to become the dominant objective**).

---

## 3. Problem Instance Definition (Section 2, JSON Format)

An instance JSON is composed of 4 top-level keys:

```
{
  "name": "...",
  "bays": [ {width, height}, ... ],
  "blocks": [ {release_time, due_date, processing_time, workload,
               bay_preferences, shape}, ... ],
  "weights": {"w1":..., "w2":..., "w3":...}
}
```

### Key Rules and Pitfalls

- **Indexing**: Instance/solution files use **0-based** indexing (whereas Section 1, the formula definitions, uses 1-based indexing) — be careful of confusion when implementing code.
- `bay_preferences`: A list of preference scores for each bay, per block. **The sum of preference scores for a single block is always normalized to 100**.
- All time values and preference values are **integers**.
- `shape`: A list of dictionaries per orientation. Each orientation has an `orientation` (an integer ID) + `layers` (a list of polygon vertices per layer, **ordered from lowest layer to highest layer**).
  - **Reference point**: The **first vertex of layer 0 is always (0.0, 0.0)** — this serves as the reference point, and all remaining vertices are expressed as coordinates relative to this reference point.
  - When specifying the block's position $(x,y)$, the reference point is translated to $(x,y)$ while the remaining vertices retain their relative positions → in other words, **depending on orientation, the reference point does not always end up at the bottom-left** (see Fig. 8).
  - Vertex coordinates may be real numbers with **up to 4 decimal places**, which can lead to numerical instability issues → it is **strongly recommended to use the functions provided in `utils.py` for the final verification step** (the evaluation server uses the same functions).

---

## 4. Solution Submission Format

A solution is a dictionary of the form `{"operations": {date: [list of operations]}}`.

- Date keys (e.g., `"0"`, `"1"`, `"28"`) need only be specified for dates on which an operation occurs (there is no need to enumerate every date).
- Operation types: `"ENTRY"` (requires block_id, bay_id, x, y, orient_idx) / `"EXIT"` (requires only block_id, bay_id).
- **Within the same date, all EXIT operations must be listed before all ENTRY operations** (reiterating the constraint).
- **x and y must be integers** — the evaluation system rounds them and checks feasibility with `utils.check_feasibility()`.

---

## 5. Algorithm Submission and Evaluation (Section 3)

### 5.1 Submission Rules Checklist

- [ ] Send an email to `submission@optichallenge.com`
- [ ] Must be sent from the **email address used at registration**
- [ ] Attach **only one zip file**
- [ ] `myalgorithm.py` must be located at the **root (top level)** of the zip (no subfolders allowed)
- [ ] `utils.py` may be included, but **must not be modified** (the server will overwrite it)
- [ ] zip file size must be **15MB or less**
- [ ] Resubmission is only possible after a **12-hour cooldown** following the **approval** of the previous submission (the cooldown starts at the time of "approval"; if rejected, resubmission is possible immediately)
- [ ] File extensions that could be mistaken for malware (`.dll`, `.vb`, `.exe`, etc.) may be blocked by the email service → teams are responsible for ensuring safe delivery

### 5.2 Types of Evaluation Result Status

| Status | Meaning |
|---|---|
| Feasible solution found | Success, objective value is displayed |
| The solution returned is infeasible | Failed `check_feasibility()` |
| Time limit exceeded | Time limit exceeded (the limit value will be disclosed after the competition ends) |
| Algorithm raised an exception | An exception occurred (details are not disclosed, in order to protect hidden instance information) |
| Algorithm process terminated unexpectedly | Presumed crash/forced termination while running non-Python code |
| Tried to use unavailable Python package or function | Import error |

> The objective value is only displayed when the solution is **feasible**.

### 5.3 Evaluation Server Specifications and Execution Constraints

- Specifications: **AMD Ryzen Threadripper PRO 9955WX**, Ubuntu 24.04 LTS
- Time limit: varies by problem (from a few minutes to 30 minutes; disclosed after the competition ends)
- Resource constraints:
  - **No** internet access
  - **No** access to directories above the execution folder
  - CPU limited to **a maximum of 4 cores (400%)**
  - Memory limited to **a maximum of 16GB**
  - Enforced via `firejail` and `cpulimit` — code that conflicts with these is prohibited
- **Relative paths are recommended instead of absolute paths** (to account for differences between the development environment and the server environment)

### 5.4 Leaderboard Scoring System

Score per problem:

$$\text{score} = \begin{cases} -1 & \text{infeasible / TLE / crash} \\ R - n_b & \text{feasible (}R\text{ = number of teams evaluated, }n_b\text{ = number of teams that found a better objective value than your own)} \end{cases}$$

- The overall ranking is determined by **summing the scores across all problems**.
- The leaderboard displays **only tiers (e.g., top 10/20/30), not exact rankings**, with exact rankings disclosed only after each stage ends.
- **Final award-winning teams** are determined through comprehensive evaluation that includes not only the leaderboard score but also the **technical report + presentation** (weighting is at the organizing committee's discretion).

---

## 6. Development Environment and Baseline (Section 4)

### 6.1 Development Environment

- A `conda` (miniforge) environment based on `python 3.12`. Key packages: the `numpy` family (`scipy`, `pandas`), `shapely 2.1.2` (presumed to be the core library for geometric operations), `networkx`, `ortools 9.15`, support for the **commercial solvers `gurobipy 13.0.2` and `xpress 9.8.1`** (sponsored by Gurobi/FICO), and ML-related packages (`torch`, `tensorflow`, `gymnasium`) — however, **the server has no GPU** (CPU execution only is allowed).
- If additional packages are needed, they can be requested from the organizing committee via Discord.

### 6.2 The `myalgorithm.py` Interface

```python
def algorithm(prob_info, timelimit=60):
    """
    The function signature cannot be changed. All other functions/modules may be freely defined.
    prob_info: a dictionary of problem information
    timelimit: the algorithm's time limit (in seconds)
    Return value: a solution dictionary (containing operations)
    """
```

### 6.3 Baseline Algorithm (Greedy, EDD Rule)

- Based on the **EDD (Earliest Due Date)** rule: blocks with the earliest due date are assigned sequentially first.
- Placement position is chosen from among the **bottom-right corner candidates** of the AABB (axis-aligned bounding box).
- **In stage 1, the crane constraint is ignored** during placement → if infeasibility occurs, stage 2 recovers by **pushing the block back until the bay becomes free**.
- In other words, this baseline represents essentially a lower bound; this suggests that **optimizing the placement order/position while considering the crane constraint** is a substantial point of potential performance improvement.

### 6.4 Use of Non-Python Languages

- C/C++: **compile locally on Ubuntu 24.04** and include the `.so`/executable file in the zip (there is no server-side compilation).
- Java: OpenJDK 17.0.18 is provided. .NET: 8.0.15 is provided.
- For non-Python implementations, compatibility and isolation with the server environment are entirely the team's responsibility.

### 6.5 Algorithm Tester & Visualization Tool

- Download `alg_tester.zip` → run `python alg_tester_app.py` within the `ogc2026` conda environment.
- In the Problem/Solution tabs, block placement can be checked via a cross-sectional view per bay, and the time axis can be checked via a Gantt chart.

---

## 7. Competition Process (Section 5)

```
Preliminary ──▶ Select 30-40 teams to advance to the Final
        │                        │
   Automated evaluation      Automated evaluation + technical
   (leaderboard)              report + presentation
        │                        │
   Displayed by tier         Final award-winning teams
   (exact ranking disclosed  determined by comprehensive review
   only after stage ends)    (automated score + report + code +
                              presentation, combined)
```

### 7.1 Preliminary Stage
1. Automated evaluation → tier-based leaderboard update
2. Concurrent **code verification** by the organizing committee → top 30-40 teams advance to the Final

### 7.2 Final Stage
1. Automated evaluation (same method as the Preliminary stage)
2. Submission of a **technical report** is mandatory (code and report are reviewed separately)
3. A **presentation** is mandatory
4. **Final award-winning teams** are determined by combining the automated score, report, code, and presentation

### 7.3 Evaluation, Disclosure, and Ethics (Section 5.3)

- Individual evaluation results are **not disclosed** (only the rankings of award-winning teams are disclosed).
- Participation requires agreeing that **the code and reports of teams advancing to the Final will be fully open-sourced**.
- Even after the competition ends, awards may be **revoked** if misconduct is discovered, such as plagiarism or unauthorized use of restricted resources.

---

## 8. Organizing Committee (Section 6)

| Name | Affiliation |
|---|---|
| Kyungsik Lee (Chair) | Seoul National University |
| Chungmok Lee | Hankuk University of Foreign Studies |
| Jonghoon Woo | Seoul National University |
| Chankmug Kang | Soongsil University |
| Seulgi Joung | Ajou University |
| Yunwoo Jung | LG CNS |
| Gibeak Ahn | LG CNS |
| Jerimi Lee | LG CNS |

---

## 9. Summary of Key Difficulties and Strategic Implications (Analysis)

1. **A mixed integer nonlinear/combinatorial problem**: Assignment + 2D irregular-shape packing (including orientation selection) + temporal scheduling are combined simultaneously → if fully formulated as a pure MIP, it is highly likely that even Gurobi/Xpress would struggle to find an optimal solution within the time limit as instance size grows. A combination of **metaheuristic/heuristic placement + local search / constraint relaxation followed by repair** appears to be the realistic approach.
2. **The crane constraint is the real trap**: It is not enough to check merely for "no overlap" — it is also necessary to separately verify **whether the vertical passage is blocked at the moment of ENTRY/EXIT**. Even the baseline takes the approach of ignoring this in stage 1 and repairing it afterward — designing a placement order from the start that accounts for this (e.g., not placing blocks that will be removed later on the inside, in a stack-like arrangement) is a key point for improvement.
3. **Scale imbalance in the objective function weights**: Looking at the example weights ($w_1=26667$ vs $w_2=10$, $w_3=300$), even though the $Z_1$ (total tardiness) value itself may be small, the weight is very large, so **due-date compliance is likely to effectively become the dominant objective** — the weights must be checked per instance to adjust priorities accordingly.
4. **Leveraging the advantage of layer-based collision checking**: The rule that "overlapping in top view is fine as long as the actual layer levels differ" (Fig. 7(a)) means there is room to **significantly increase spatial efficiency by arranging blocks in a staggered/interlocking fashion**. This is a point where space utilization can be increased beyond simple rectangular packing.
5. **Numerical stability**: Since vertex coordinates are real numbers with up to 4 decimal places, floating-point error may cause false collisions or false passes → it is essential to reuse the provided `utils.py` (presumed to be based on Shapely) judgment functions as-is, to verify against the same criteria as the evaluation server.
6. **Managing submission-operations risk**: Since the 12-hour cooldown is based on "approved" submissions, rejection causes (zip structure, paths, size, etc.) should be thoroughly verified locally in advance to avoid wasting unnecessary waiting time. Additionally, use of development-environment-only packages or absolute paths can be a major cause of server execution failures (crashes/import errors).
7. **The final ranking is not determined by the leaderboard alone**: While the Preliminary stage centers on automated scoring, the Final stage places **considerable weight on the technical report and presentation**, so it is advantageous to prepare **original methodology (theoretical contribution)** alongside simply maximizing performance.

---

*This document is a summary and analysis based on the uploaded "OGC 2026 Problem Description (Version 1.2)." For actual submission/development, please be sure to consult the latest version of the official competition website's documents, together with `utils.py` and the baseline code.*
