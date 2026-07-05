# OGC 2026 Optimization Challenge Onboarding

## Project Overview

This repository provides the baseline algorithm template and PyQt6-based Algorithm Tester for the OGC 2026 Optimization Challenge.

- Project: OGC 2026 Optimization Challenge
- Primary language: Python
- Supporting formats: YAML, JSON, Markdown, config files
- Key frameworks and libraries: PyQt6, Shapely, Conda, Gurobi, OR-Tools
- Current graph commit: `52556c188f671ef248c9fd8af867c7072b1a6f85`

The project is organized around one core contract: a participant supplies `algorithm(prob_info, timelimit)` and returns a solution dictionary in the required operations format. The baseline solver, command-line runners, feasibility checker, and GUI tester all orbit that same interface.

## Architecture Layers

### Documentation And Planning

Project usage notes, solver design notes, and future improvement plans live here. Start with these files when you need intent rather than implementation detail.

Key files:

- `README.txt` explains setup, baseline editing, and tester launch flow.
- `baseline/README.txt` documents the algorithm interface and feasibility checker usage.
- `alg_tester/README.txt` documents the GUI tester workflow.
- `docs/superpowers/specs/2026-06-28-greedy-lns-design.md` captures greedy/LNS improvement design notes.
- `docs/superpowers/plans/2026-06-28-greedy-lns.md` captures the related implementation plan.

### Environment And Config

This layer defines how the project should be run locally and which generated or private files are intentionally excluded.

Key files:

- `ogc2026_env.yml` defines the Conda environment, including optimization, scientific computing, GUI, and geometry dependencies.
- `.gitignore` excludes local settings, bytecode, large datasets, PDFs, zip files, and license files.

### Baseline Solver

This is the main algorithm development area. It contains the participant-facing template, the reference greedy solver, CLI runners, and the official feasibility/scoring utilities.

Key files:

- `baseline/myalgorithm.py` is the submission entry point. Keep the `algorithm(prob_info, timelimit)` signature stable.
- `baseline/baseline_greedy.py` implements the EDD-based greedy solver with crane-path checks and iterative repair.
- `baseline/utils.py` defines `Bay`, `Block`, collision checks, entry/exit feasibility, full solution validation, and objective computation.
- `baseline/run_myalgorithm.py` runs the user algorithm from the CLI and validates the returned solution.
- `baseline/run_baseline_greedy.py` runs the reference greedy implementation directly.

### Algorithm Tester UI

This layer is the PyQt6 desktop app for running algorithms, validating outputs, and visualizing schedules and bay layouts.

Key files:

- `alg_tester/alg_tester_app.py` boots the PyQt6 application.
- `alg_tester/alg_tester_ui/main_window.py` wires together the control, solution, and layout tabs.
- `alg_tester/alg_tester_ui/control_panel.py` captures the instance file, algorithm folder, and time limit.
- `alg_tester/alg_tester_ui/solution_widget.py` runs the algorithm subprocess, collects logs, validates the solution, and updates result views.
- `alg_tester/alg_tester_ui/gantt_widget.py` renders bay schedules over time.
- `alg_tester/alg_tester_ui/bay_layout_widget.py` renders bay geometry, block positions, cross-sections, and collision overlays.
- `alg_tester/utils.py` is the GUI-side copy of the feasibility and geometry utilities.

### Sample Inputs

This layer contains small inputs used for manual GUI and algorithm smoke tests.

Key file:

- `alg_tester/example/example_B2_b10.json` is a small example problem instance.

## Key Concepts

### Algorithm Contract

The main integration point is:

```python
def algorithm(prob_info: dict, timelimit: float) -> dict:
    ...
    return solution
```

`prob_info` is loaded from a problem instance JSON file. `solution` must match the expected operations format, with time-keyed `ENTRY` and `EXIT` operations.

### Operations Format

The solver ultimately returns:

```python
{
    "operations": {
        "<time_int>": [
            {"type": "EXIT", "block_id": ..., "bay_id": ...},
            {
                "type": "ENTRY",
                "block_id": ...,
                "bay_id": ...,
                "x": ...,
                "y": ...,
                "orient_idx": ...
            }
        ]
    }
}
```

At each time point, exits should be processed before entries. The feasibility checker reconstructs bay state from these operations.

### Geometry And Crane-Path Feasibility

`baseline/utils.py` is the source of truth for validation. It models rectangular bays, placed blocks, polygon layers, same-layer collisions, and crane entry/exit obstruction.

The important rule is the layer sweep rule: during crane entry or exit, a block layer can collide with existing layers at the same or higher effective height. The code models this through the `j >= k` relationship described in the utility module docstring.

### Greedy Baseline Strategy

`baseline/baseline_greedy.py` places blocks in an EDD-style order, evaluates candidate bay/orientation/position/time-slot combinations, scores placements, then repairs feasibility violations.

The high-level stages are:

1. Generate candidate positions from bay bounds and already placed block edges.
2. Find an entry/exit time slot that passes crane-path and collision checks.
3. Score placements using tardiness, load balance, preference penalty, and packing tie-breaks.
4. Run repair passes for blocks that fail feasibility.
5. Convert assignments to time-keyed operations.

### GUI Execution Model

The GUI does not run participant code directly in the main UI thread. `AlgorithmWorker` in `solution_widget.py` runs the selected algorithm path through a subprocess-style workflow, captures output, and then validates the resulting solution.

This separation is important: UI responsiveness, log collection, and solution validation all depend on keeping algorithm execution isolated from the Qt event loop.

## Guided Tour

Follow this order when onboarding:

1. Project purpose
   Read `README.txt`, `baseline/README.txt`, and `alg_tester/README.txt` to understand the challenge structure, setup flow, and how baseline and tester relate.

2. Submission entry point
   Read `baseline/myalgorithm.py`, then `baseline/baseline_greedy.py`. The first file is the stable interface; the second file is the reference implementation.

3. Feasibility and objective
   Read `baseline/utils.py` before changing solver logic. It defines what makes a solution valid and how objective values are computed.

4. GUI execution flow
   Read `alg_tester/alg_tester_app.py`, `main_window.py`, `control_panel.py`, and `solution_widget.py` to understand how user inputs trigger algorithm execution and validation.

5. Visualization
   Read `gantt_widget.py` and `bay_layout_widget.py` to understand how schedule and spatial placement are displayed.

## File Map

### Root

- `README.txt`: top-level quick start and directory guide.
- `ogc2026_env.yml`: Conda environment for development and execution.

### Baseline Solver

- `baseline/README.txt`: baseline package guide and algorithm contract.
- `baseline/myalgorithm.py`: participant-editable algorithm entry point.
- `baseline/baseline_greedy.py`: reference greedy algorithm with repair.
- `baseline/utils.py`: geometry, feasibility, and objective implementation.
- `baseline/run_myalgorithm.py`: CLI runner for participant algorithm.
- `baseline/run_baseline_greedy.py`: CLI runner for reference greedy algorithm.

### Algorithm Tester

- `alg_tester/README.txt`: GUI tester instructions.
- `alg_tester/alg_tester_app.py`: Qt app startup.
- `alg_tester/alg_tester_ui/main_window.py`: top-level GUI composition.
- `alg_tester/alg_tester_ui/control_panel.py`: input controls.
- `alg_tester/alg_tester_ui/solution_widget.py`: algorithm execution, logging, validation, and result display.
- `alg_tester/alg_tester_ui/gantt_widget.py`: time-based schedule visualization.
- `alg_tester/alg_tester_ui/bay_layout_widget.py`: spatial bay/block visualization.
- `alg_tester/utils.py`: GUI-side feasibility utilities.
- `alg_tester/example/example_B2_b10.json`: small example instance.

### Planning Documents

- `docs/superpowers/specs/2026-06-28-greedy-lns-design.md`: solver improvement design notes.
- `docs/superpowers/plans/2026-06-28-greedy-lns.md`: solver improvement implementation plan.

## Complexity Hotspots

Approach these files carefully:

- `baseline/utils.py`: central validation logic. Changes here affect CLI runs, GUI validation, objective reporting, and solver repair behavior.
- `baseline/baseline_greedy.py`: dense solver logic. Placement search, scoring, repair, timeout behavior, and operation construction are coupled.
- `alg_tester/utils.py`: duplicate feasibility utility used by the GUI. Keep behavior aligned with `baseline/utils.py`.
- `alg_tester/alg_tester_ui/solution_widget.py`: bridges GUI state, subprocess execution, log capture, validation, and visualization updates.
- `alg_tester/alg_tester_ui/bay_layout_widget.py`: large rendering module with geometry, interaction, cross-section, collision, and Gantt-related behavior.
- `alg_tester/alg_tester_ui/gantt_widget.py`: schedule rendering and interaction logic.
- `alg_tester/alg_tester_ui/control_panel.py`: user input state that drives downstream execution.
- `alg_tester/example/example_B2_b10.json`: useful for smoke testing, but do not generalize algorithm behavior from only this small case.

## First Local Checks

Create the Conda environment once:

```bash
conda env create -f ogc2026_env.yml
```

Run the GUI tester:

```bash
conda activate ogc2026
cd alg_tester
python alg_tester_app.py
```

Run the participant algorithm from the CLI:

```bash
conda activate ogc2026
cd baseline
python run_myalgorithm.py ../alg_tester/example/example_B2_b10.json --timelimit 60
```

## Contribution Notes

- Keep `algorithm(prob_info, timelimit)` stable unless the challenge contract changes.
- Treat `check_feasibility` as the authoritative validator.
- When changing solver logic, test through both the CLI and Algorithm Tester.
- If you modify feasibility utilities, keep `baseline/utils.py` and `alg_tester/utils.py` behavior aligned.
- Avoid committing local datasets, PDFs, zip files, bytecode, settings, or license files.
