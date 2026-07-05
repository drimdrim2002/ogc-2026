# OGC 2026 Problem Statement Analysis

Source document: `docs/problem-statement-latest-DBm0ZUoF.pdf`  
Document title: Optimization Grand Challenge 2026 (OGC 2026), "The Grand Shipyard Puzzle: Pack the Block, Beat the Clock"  
Document version: 1.2, June 15, 2026  
Pages: 15

## 1. Executive Summary

OGC 2026 defines a combined spatial packing and scheduling problem inspired by shipyard block production. Each block is a layered 3D object represented as one or more polygonal layers. Each block must be assigned to exactly one bay, placed at an integer coordinate with one of its allowed orientations, entered into the bay on a chosen date, and removed on a chosen date.

The main difficulty is that the decisions are tightly coupled:

- Temporal feasibility depends on release dates, processing times, due dates, and the chosen entry and exit dates.
- Spatial feasibility depends on bay containment, layer-by-layer non-overlap, and crane-access constraints at entry and exit times.
- Objective quality depends on three criteria: total tardiness, normalized workload imbalance across bays, and deviation from preferred bay assignments.

This is not a pure scheduling problem and not a pure 2D packing problem. A competitive solution must handle both simultaneously, while also respecting the exact output format and server execution restrictions.

## 2. Core Entities and Decision Variables

### Bays

A bay is a physically separated workspace with fixed width and height. The set of bays is denoted by `M`. In the formal definition the bays are indexed from `1` to `m`, while JSON instances use Python-style 0-based indexing from `0` to `m - 1`.

For bay `j`:

- Width: `W_j`
- Height: `H_j`
- Area: `W_j * H_j`

Each bay has an independent crane. Operations in different bays are therefore independent, but the ordering of operations inside the same bay matters because every entry or exit changes the spatial state of that bay.

### Blocks

A block is a production unit representing a large structural component of a ship. Each block consists of `K_i` polygonal layers. All layers of a block have the same height, layers are ordered from lowest to highest, and adjacent layers are guaranteed to be physically connected.

For block `i`:

- Release date: `R_i`
- Due date: `D_i`
- Processing time: `P_i`
- Workload: `L_i`
- Bay preference score: `S_ij` for each bay `j`
- Number of possible orientations: `O_i`
- Shape: polygon layers for each orientation

The layer polygons may be non-convex. The first vertex of the first layer in each orientation is the reference point, always `[0.0, 0.0]`. If the algorithm places the block at `(x, y)`, all vertices in that orientation are translated by `(x, y)`.

### Required Decisions

For every block, the algorithm must decide:

- Assigned bay
- Placement coordinate `(x, y)`
- Orientation index
- Entry date `ENTRY_i`
- Exit date `EXIT_i`

Once placed, a block cannot be moved or rotated. Each block must have exactly one `ENTRY` operation and exactly one `EXIT` operation.

## 3. Time Model

A block occupies its assigned bay during the half-open interval:

```text
[ENTRY_i, EXIT_i)
```

This means the block is present on date `t` if:

```text
ENTRY_i <= t < EXIT_i
```

The temporal constraints are:

```text
ENTRY_i >= R_i
EXIT_i - ENTRY_i >= P_i
```

If a block enters on day 5 and has processing time 3, it can be processed on days 5, 6, and 7. The earliest exit date is day 8.

Important implication: a block may stay in the bay after its processing is complete. This can be necessary if other blocks prevent it from exiting due to spatial or crane-access constraints. If `EXIT_i > D_i`, tardiness is incurred.

## 4. Operation Model

There are two operation types:

- `ENTRY`: place a block into a bay.
- `EXIT`: remove a block from a bay.

For a given date and bay, any number of operations may be performed before the day's work begins. However, all `EXIT` operations must be performed before any `ENTRY` operations on that date. Among operations of the same type, the algorithm controls the ordering.

This ordering rule is operationally important. Two solutions with the same entry and exit dates can differ in feasibility if the same-day operation order changes the bay state in a different sequence.

## 5. Spatial Feasibility

Spatial feasibility has three parts: bay containment, layer collision freedom, and crane operation feasibility.

### 5.1 Bay Containment

Every placed block must be fully contained within its assigned bay. Because some orientations can place the reference point away from the bottom-left corner of the block's bounding geometry, checking only `0 <= x <= W_j` and `0 <= y <= H_j` is not enough. The translated polygon vertices and polygon interiors must lie inside the bay.

### 5.2 Layer Collision-Free Constraint

For any date `t` and bay `j`, define:

```text
N(t, j) = { i in N : ENTRY_i <= t < EXIT_i and i is assigned to j }
```

For any two distinct blocks in `N(t, j)`, the same-level layers must not overlap in their interiors. Edge sharing is allowed.

If block `i1` and block `i2` are present in the same bay on the same date, their layers must be collision-free for:

```text
l = 1, 2, ..., min(K_i1, K_i2)
```

The key detail is that collision is checked between layers at the same level. Two blocks can appear to overlap from the top view and still be feasible if their occupied layers do not collide at the same vertical levels.

### 5.3 Crane Operation Constraint

For `ENTRY` or `EXIT`, the crane must be able to move the block vertically without interference. For an operation on block `i1`, every layer of `i1` must be collision-free with every same-or-higher layer of every other block currently in the bay.

Formally, for another block `i2` in the bay, the check applies for layer pairs where:

```text
l1 <= l2
```

where `l1` is a layer of the operated block and `l2` is a layer of the other block.

Practical implication: a final static arrangement may be layer-feasible, but a specific `ENTRY` or `EXIT` operation can still be infeasible because a higher layer of another block blocks the crane path.

## 6. Objective Function

The total objective is:

```text
w1 * Z1 + w2 * Z2 + w3 * Z3
```

where `w1`, `w2`, and `w3` are nonnegative instance-provided weights.

### 6.1 Total Tardiness `Z1`

For each block:

```text
T_i = max(0, EXIT_i - D_i)
```

Then:

```text
Z1 = sum_i T_i
```

This component rewards early or on-time exits and penalizes late exits.

### 6.2 Workload Imbalance `Z2`

For each bay `j`, a bay weight `u_j` is defined as:

```text
u_j = (average bay area) / (area of bay j)
```

Larger bays have smaller weights. The weighted workload assigned to a bay is:

```text
u_j * sum_{i assigned to j} L_i
```

`Z2` is the maximum absolute difference between weighted workloads across any pair of distinct bays. This encourages a balanced use of bay capacity after normalizing for bay size.

### 6.3 Preference Penalty `Z3`

For each block:

```text
S_i^max = max_j S_ij
```

The preference penalty for assigning block `i` to bay `j` is:

```text
S_i^max - S_ij
```

Then:

```text
Z3 = sum over assigned blocks of (S_i^max - S_ij)
```

If every block is assigned to its most preferred bay, `Z3 = 0`.

## 7. Problem Instance Format

Each problem instance is a JSON file with four top-level parts:

- `name`: problem name.
- `bays`: list of bay objects, each with `width` and `height`.
- `blocks`: list of block objects.
- `weights`: objective weights `w1`, `w2`, and `w3`.

Each block contains:

- `release_time`
- `due_date`
- `processing_time`
- `workload`
- `bay_preferences`
- `shape`

Each orientation inside `shape` contains:

- `orientation`: integer orientation id.
- `layers`: list of layer polygons from bottom to top.

All block and bay indices in the input and output are 0-based. All time-related values and preference scores are integers. Preference scores for a single block sum to 100 across all bays.

Layer vertices other than the reference point may be fractional with up to four significant digits. Because numerical instability can occur for irregular polygons, the document strongly recommends using the provided `utils.py` functions, especially at the final feasibility-checking stage.

## 8. Required Solution Format

The submitted `algorithm(prob_info, timelimit=60)` function must return a Python dictionary with a single top-level key:

```python
{
    "operations": {
        "0": [
            {
                "type": "ENTRY",
                "block_id": 48,
                "bay_id": 1,
                "x": 0,
                "y": 0,
                "orient_idx": 0
            }
        ],
        "28": [
            {
                "type": "EXIT",
                "block_id": 25,
                "bay_id": 2
            },
            {
                "type": "ENTRY",
                "block_id": 15,
                "bay_id": 0,
                "x": 106,
                "y": 0,
                "orient_idx": 0
            }
        ]
    }
}
```

Important output requirements:

- Date keys are strings.
- Only dates with operations need to be included.
- `EXIT` operations must appear before `ENTRY` operations on the same date.
- All numeric output values should be integers.
- Block coordinates `x` and `y` cannot be fractional.
- The evaluation system rounds location values before feasibility checking.

## 9. Submission and Evaluation Rules

Algorithms are submitted by email to `submission@optichallenge.com`.

Submission requirements:

- Send from the email address used for registration.
- Attach exactly one zip file.
- The zip file must include `myalgorithm.py`.
- `myalgorithm.py` must be at the root of the decompressed archive.
- If `utils.py` is included, it must not be modified and will be overwritten before execution.
- Zip file size must not exceed 15 MB.
- A new submission is not accepted if the previous accepted submission is still within the 12-hour cooldown period.

An accepted submission is not guaranteed to run correctly. The latest accepted submission is used for leaderboard evaluation, even if it later crashes, times out, or returns infeasible solutions.

Possible evaluation statuses include:

- Feasible solution found.
- The solution returned is infeasible.
- Time limit exceeded.
- Algorithm raised an exception.
- Algorithm process terminated unexpectedly.
- Tried to use unavailable Python package or function.

Objective values are reported only for feasible solutions.

## 10. Evaluation Server Environment

The evaluation server is specified as:

- AMD Ryzen Threadripper PRO 9955WX
- Ubuntu 24.04 LTS

Execution restrictions:

- No external internet access.
- No access to parent directories above the execution folder.
- At most 4 CPU cores, expressed as 400 percent CPU usage.
- At most 16 GB memory.

Time limits vary by hidden problem. The document states that typical time limits are expected to range from a few minutes to half an hour in wall-clock time. Algorithms should monitor elapsed time internally and return a valid solution before the limit.

Available notable packages in the provided conda environment include:

- Python 3.12
- pandas 2.2.3
- networkx 3.4.2
- scipy 1.15.2
- scikit-learn 1.6.1
- numba 0.61.0
- cython 3.0.10
- OR-Tools 9.15.6755
- gurobipy 13.0.2
- xpress 9.8.1
- shapely 2.1.2
- torch 2.11.0
- tensorflow 2.21.0
- openjdk 17
- dotnet runtime 8

There is no dedicated GPU. Machine-learning approaches are allowed in principle, but must run on CPU.

## 11. Leaderboard and Final Judging

For each hidden instance:

- Score is `-1` if the solution is infeasible, the algorithm times out, or the algorithm crashes.
- If feasible, score is `R - nb`, where `R` is the number of evaluated teams and `nb` is the number of teams that found a strictly better objective value on that instance.

Leaderboard ranking is based on total points across all problems. During each competition stage, the leaderboard shows tiers such as top 10, top 20, and top 30 rather than exact ranks.

Final winners are not determined by the leaderboard alone. The final criteria include:

- Leaderboard ranking.
- Technical report.
- Presentation.
- Code evaluation and integrity review.

Finalist code and technical reports will be publicly disclosed, so submitted code must be eligible for open-source release.

## 12. Baseline Algorithm

The baseline algorithm is a simple greedy approach based on EDD, the earliest due date rule.

Baseline behavior:

- Selects the next block by earliest due date.
- Tries candidate points based on bottom-right vertices of axis-aligned bounding boxes.
- Initially ignores the crane constraint.
- Later moves infeasible blocks to a later date when bays are cleared.

The baseline is primarily a development reference. Its important lesson is that satisfying static packing constraints is not enough; the crane operation constraint can invalidate a schedule after placement.

## 13. Algorithmic Implications

### 13.1 The Problem Has Multiple Coupled Hard Subproblems

A solution must solve:

- Assignment of blocks to bays.
- Orientation selection.
- Irregular polygon packing.
- Multi-day interval scheduling.
- Entry and exit sequencing.
- Crane-access feasibility.
- Multi-objective tradeoff optimization.

Optimizing one part greedily can easily damage another part. For example, assigning blocks to their preferred bays may increase congestion and tardiness. Exiting blocks as early as possible may improve tardiness but cause unnecessary operation conflicts or prevent better packing later.

### 13.2 Crane Feasibility Is a Major Hidden Trap

The crane rule is stricter than same-level collision freedom. During entry or exit, the operated block must be vertically movable without intersecting layers of other blocks at the same or higher levels. This means that a packing that looks valid over time may fail at the exact operation moment.

A practical algorithm should check operation feasibility at the time of insertion and removal, not only after constructing a static bay layout.

### 13.3 Integer Coordinates Matter

The solution requires integer coordinates, while input polygon vertices can be fractional. Candidate placement generation should account for this. Algorithms using continuous optimization or floating geometry should convert to integer placements carefully and re-check feasibility with the official utilities.

### 13.4 Objective Scaling Matters

The objective weights are instance-provided. A robust algorithm should not assume that tardiness, workload balance, or preference is always dominant. It should read weights and adapt decision priorities accordingly.

For example:

- Large `w1` emphasizes schedule quality and due-date pressure.
- Large `w2` emphasizes balanced bay workloads.
- Large `w3` emphasizes bay preference matching.

### 13.5 Hidden Instances Reward Generalization

Training instances may vary in number of blocks, bay sizes, schedule tightness, and geometry difficulty. Hardcoding behavior to visible instances is risky. Good algorithms should include fallback behavior for dense packing, tight due dates, and cases where preferred bays are infeasible or congested.

## 14. Practical Implementation Checklist

Before submission, verify:

- `myalgorithm.py` exists at the zip root.
- The function signature is exactly `algorithm(prob_info, timelimit=60)`.
- The returned object has exactly the required `operations` structure.
- Every block has exactly one `ENTRY` and one `EXIT`.
- Every block is assigned to exactly one bay.
- All date keys are strings.
- All coordinates and ids are integers.
- Same-day operations list `EXIT` before `ENTRY`.
- No absolute paths are required at runtime.
- No internet access is required.
- Runtime stays below the provided `timelimit`.
- Memory usage stays below 16 GB.
- CPU usage does not assume more than 4 cores.
- Final solution passes `utils.check_feasibility(prob_info, solution)`.

## 15. Recommended Development Direction

The document does not prescribe a method. It explicitly allows mathematical optimization, machine learning, heuristics, and hybrid approaches. Based on the structure of the problem, a strong approach will likely need:

- Fast geometric collision and containment checks, preferably using the provided utilities or consistent Shapely-based logic.
- A schedule builder that can maintain bay state over time.
- Candidate placement generation that is richer than simple grid search but still computationally bounded.
- Repair mechanisms for infeasible crane operations and late exits.
- Local search or metaheuristics for improving assignment, orientation, placement, and timing after an initial feasible solution.
- Time-aware anytime behavior, so the algorithm can return the best feasible solution found before the hidden time limit expires.

The most important engineering priority is feasibility first. The leaderboard gives `-1` for infeasible, timed-out, or crashed submissions, so a worse feasible objective is far better than a sophisticated algorithm that fails to return a valid solution.
