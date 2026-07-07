# PROJECT KNOWLEDGE BASE

**Generated:** 2026-07-07 23:27 KST
**Commit:** 028184f
**Branch:** m2-main

## OVERVIEW

OGC 2026 optimization challenge workspace. The core is a Python 3.12 solver
harness under `baseline/`, a PyQt6 visual tester under `alg_tester/`, and a
large planning/results workspace under `docs/` and `experiments/`.

## STRUCTURE

```text
./
|-- baseline/              # submission wrapper, greedy solver, checker, CLIs, tests
|-- alg_tester/            # PyQt6 visual tester and example instances
|   `-- alg_tester_ui/     # custom widgets, painting, subprocess runner UI
|-- docs/                  # active analysis, strategy docs, archived planning notes
|-- experiments/results/   # benchmark JSON outputs and summaries
|-- data/                  # local challenge instances; git-ignored
|-- ogc2026_env.yml        # upstream conda environment definition
`-- README.txt             # challenge quick start
```

## WHERE TO LOOK

| Task | Location | Notes |
| --- | --- | --- |
| Edit submitted solver | `baseline/myalgorithm.py` | Keep `algorithm(prob_info, timelimit)` stable. |
| Change greedy/search logic | `baseline/baseline_greedy.py` | Time guards and serial fallback are test-covered. |
| Change feasibility/scoring | `baseline/utils.py` | Canonical checker contract; widest blast radius. |
| Run one solver locally | `baseline/run_myalgorithm.py` | Prints feasibility and objective details. |
| Benchmark instances | `baseline/benchmark_instances.py` | Named sets: `all`, `daily-40`, `dev-10`, `smoke-3`. |
| Test solver harness | `baseline/tests/` | `unittest`, local JSON fixtures required for full suite. |
| Launch UI | `alg_tester/alg_tester_app.py` | Uses PyQt6 and `alg_tester/settings.json`. |
| UI subprocess flow | `alg_tester/alg_tester_ui/solution_widget.py` | Temp runner script, stdout stream, temp JSON result. |
| UI geometry/painting | `alg_tester/alg_tester_ui/bay_layout_widget.py` | Largest UI hotspot. |
| Navigation docs | `docs/INDEX.md`, `docs/ONBOARDING.md` | Active docs vs archive map. |
| Experiment evidence | `experiments/results/` | Generated JSON outputs; curate intentionally. |
| Local challenge data | `data/train`, `data/train 2` | Ignored, but tests/benchmarks may require them. |

## CODE MAP

Codegraph was available to explorer agents and reported 113 symbols across 14
files. Python LSP was not usable because the language server was not installed;
reference counts below are therefore unmeasured and based on codegraph/AST/text
discovery, not authoritative LSP centrality.

| Symbol | Type | Location | Refs | Role |
| --- | --- | --- | --- | --- |
| `algorithm` | function | `baseline/myalgorithm.py` | unmeasured | Challenge submission API. |
| `greedyalgorithm` | function | `baseline/baseline_greedy.py` | unmeasured | Main construction/repair solver. |
| `_serial_fallback_solution` | function | `baseline/baseline_greedy.py` | unmeasured | Conservative feasible fallback. |
| `check_feasibility` | function | `baseline/utils.py` | unmeasured | Official local oracle for solution validity. |
| `benchmark_instances.main` | function | `baseline/benchmark_instances.py` | unmeasured | Stats/benchmark CLI entry point. |
| `MainWindow` | class | `alg_tester/alg_tester_ui/main_window.py` | unmeasured | Wires control panel, problem tab, solution tab. |
| `SolutionTab` | class | `alg_tester/alg_tester_ui/solution_widget.py` | unmeasured | Runs selected algorithm and renders result. |
| `BayLayoutTab` | class | `alg_tester/alg_tester_ui/bay_layout_widget.py` | unmeasured | Main problem-layout visualization. |
| `BayCanvas` | class | `alg_tester/alg_tester_ui/bay_layout_widget.py` | unmeasured | Custom bay rendering and interactions. |
| `ControlPanel` | class | `alg_tester/alg_tester_ui/control_panel.py` | unmeasured | Instance/algorithm selection and settings. |
| `GanttCanvas` | class | `alg_tester/alg_tester_ui/gantt_widget.py` | unmeasured | Timeline rendering. |

## CONVENTIONS

- Local project rule: do not run bare `python` or `pip` in automation. Use
  `/opt/homebrew/Caskroom/miniforge/base/envs/ogc-2026/bin/python`, or activate
  `ogc-2026` in the same shell command. The upstream env file is named
  `ogc2026_env.yml` and declares `name: ogc2026`; prefer the absolute
  interpreter path when command reproducibility matters.
- Python style is plain 4-space `snake_case`; there is no formatter config.
- `baseline/myalgorithm.py` must return only checker-accepted candidates or the
  verified serial fallback.
- `baseline/utils.py` and `alg_tester/utils.py` contain mirrored geometry and
  checker logic; keep intentional parity when changing shared concepts.
- Tests are `unittest` files under `baseline/tests/`, not pytest.
- Full benchmark/test coverage expects ignored local data under `data/train` and
  `data/train 2`.
- `docs/archive/` is historical context; active navigation starts at
  `docs/INDEX.md`.

## ANTI-PATTERNS (THIS PROJECT)

- Do not commit `gurobi.lic`, solver credentials, `data/`, generated PDFs/zips,
  `alg_tester/settings.json`, `.understand-anything/`, or `__pycache__/`.
- Do not weaken feasibility checks, fallback verification, or tiny-timelimit
  behavior to make a solver change pass.
- Do not change `algorithm()` signature or make the UI require anything beyond a
  folder containing `myalgorithm.py`.
- Do not treat `experiments/results/` as source code; it is evidence/output.
- Do not add child `AGENTS.md` files under generated, ignored, or data-only
  folders unless the user asks for local handling rules there.

## UNIQUE STYLES

- Submission safety is defensive: `myalgorithm.algorithm()` delegates to the
  greedy solver with `_SUBMISSION_BLOCK_ORDER_MODE = "slack"`, validates the
  candidate, and falls back on any exception or invalid output.
- The tester runs algorithms out-of-process through a generated temp runner and
  streams combined stdout/stderr into the UI.
- Benchmark output records solver labels and effective block-order modes; tests
  assert those labels.

## COMMANDS

```bash
conda env create -f ogc2026_env.yml
/opt/homebrew/Caskroom/miniforge/base/envs/ogc-2026/bin/python -m unittest discover -s baseline/tests -p 'test_*.py'
cd baseline && /opt/homebrew/Caskroom/miniforge/base/envs/ogc-2026/bin/python run_myalgorithm.py ../alg_tester/example/example_B2_b10.json --timelimit 10
/opt/homebrew/Caskroom/miniforge/base/envs/ogc-2026/bin/python baseline/benchmark_instances.py --root . --limit 1 --run-baseline --timelimit 0.001
cd alg_tester && /opt/homebrew/Caskroom/miniforge/base/envs/ogc-2026/bin/python alg_tester_app.py
```

## NOTES

- Current source scan: about 10,733 Python lines; largest hotspots are
  `alg_tester/alg_tester_ui/bay_layout_widget.py`, `baseline/utils.py`,
  `alg_tester/utils.py`, `baseline/baseline_greedy.py`,
  `alg_tester/alg_tester_ui/solution_widget.py`, and
  `alg_tester/alg_tester_ui/gantt_widget.py`.
- `baseline/tests/__pycache__` contains a stale compiled test name with no
  matching `.py` source; ignore it.
- No `.github/workflows` or `Makefile` were found.
