# ALG TESTER KNOWLEDGE BASE

## OVERVIEW

PyQt6 visual tester for selecting an instance, selecting an algorithm folder,
running `myalgorithm.py` out-of-process, and visualizing feasibility/results.

## STRUCTURE

```text
alg_tester/
|-- alg_tester_app.py      # QApplication entry point
|-- alg_tester_ui/         # widgets, custom painting, subprocess flow
|-- utils.py               # tester-side geometry/checker utilities
|-- example/               # example JSON plus local ignored training files/zips
`-- README.txt             # tester usage
```

## WHERE TO LOOK

| Task | Location | Notes |
| --- | --- | --- |
| Start app | `alg_tester_app.py` | Creates `MainWindow`; uses app-local `settings.json`. |
| UI shell | `alg_tester_ui/main_window.py` | Wires control panel, problem tab, solution tab. |
| Path/time controls | `alg_tester_ui/control_panel.py` | Instance, algorithm folder, timelimit, settings. |
| Run algorithm | `alg_tester_ui/solution_widget.py` | Subprocess, stdout stream, temp JSON IPC. |
| Problem canvas | `alg_tester_ui/bay_layout_widget.py` | Block/bay drawing, layers, drag overlay. |
| Timeline | `alg_tester_ui/gantt_widget.py` | Gantt rendering and color-by-bay convention. |
| Checker utilities | `utils.py` | Mirrors baseline geometry/checker concepts. |

## CONVENTIONS

- Launch from `alg_tester/` or with `alg_tester/` on `sys.path`; modules insert
  the parent path for local imports.
- The selected algorithm path is a folder, not a file; it must contain
  `myalgorithm.py`.
- `settings.json` stores last instance path, algorithm folder, and timelimit and
  is intentionally ignored.
- `solution_widget.py` writes a temporary runner script and result JSON; keep
  stdout streaming responsive when changing execution flow.
- Example `train*` folders and zip files are local data artifacts, not source.

## ANTI-PATTERNS

- Do not block the Qt main thread while waiting for solver work.
- Do not commit `settings.json`, example training zips, or generated caches.
- Do not merge tester `utils.py` with `baseline/utils.py` without checking both
  UI rendering needs and checker parity.
- Do not assume UI tests exist; use manual PyQt smoke checks for UI changes.

## COMMANDS

```bash
cd alg_tester && /opt/homebrew/Caskroom/miniforge/base/envs/ogc-2026/bin/python alg_tester_app.py
```
