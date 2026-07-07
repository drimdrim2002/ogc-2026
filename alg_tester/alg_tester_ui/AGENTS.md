# ALG TESTER UI KNOWLEDGE BASE

## OVERVIEW

Widget package for the visual tester. The risky areas are custom painting,
drag/drop geometry, Gantt rendering, and subprocess-driven solution execution.

## WHERE TO LOOK

| Task | Location | Notes |
| --- | --- | --- |
| Top-level wiring | `main_window.py` | Connects `ControlPanel`, `BayLayoutTab`, `SolutionTab`. |
| User selections | `control_panel.py` | File dialogs, algorithm folder, timelimit, settings. |
| Algorithm execution | `solution_widget.py` | `AlgorithmWorker`, `_StdoutReader`, temp runner script. |
| Bay/problem rendering | `bay_layout_widget.py` | Largest file; canvases, drag overlay, cross sections. |
| Timeline rendering | `gantt_widget.py` | Gantt canvas, zoom/sort, bay colors. |

## CONVENTIONS

- Widgets use PyQt6 classes directly; keep signal/slot wiring explicit.
- Long-running solver execution belongs in `QThread` workers. UI updates should
  arrive through Qt signals.
- The runner script in `solution_widget.py` imports `myalgorithm.py` from the
  selected folder with `importlib.util.spec_from_file_location`.
- Geometry drawing uses local helper functions for colors, layers, scaling, and
  block polygons; reuse them instead of adding parallel palettes/math.
- `bay_layout_widget.py` combines multiple visual modes in one file; keep edits
  tightly scoped and verify the relevant tab visually.

## ANTI-PATTERNS

- Do not read or write widgets from worker threads directly.
- Do not silently change the result JSON shape emitted by the temp runner.
- Do not remove `stderr=STDOUT`; the log panel relies on combined output.
- Do not introduce app-wide state outside `ControlPanel` settings persistence
  unless the main window owns the lifecycle.

## MANUAL QA

For UI changes, launch the app, load `example/example_B2_b10.json`, select the
`../baseline` algorithm folder, run with a small timelimit, and inspect both
Problem and Solution tabs.
