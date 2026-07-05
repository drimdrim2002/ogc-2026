# Repository Guidelines

## Project Structure & Module Organization

This repository contains an OGC 2026 optimization challenge workspace. `baseline/` holds the submitted algorithm interface, reference greedy solver, local runner, benchmark helpers, and `baseline/tests/` unit tests. Edit `baseline/myalgorithm.py` for custom solutions and keep the required `algorithm(prob_info: dict, timelimit: float) -> dict` entry point unchanged. `alg_tester/` contains the PyQt-based tester UI and a small example instance under `alg_tester/example/`. `docs/` stores problem analysis, plans, and archived notes. `data/` is local challenge data and is intentionally git-ignored.

## Build, Test, and Development Commands

- `conda env create -f ogc2026_env.yml`: create the Python 3.12 `ogc2026` environment.
- `conda activate ogc2026`: activate dependencies, including PyQt6, OR-Tools, Gurobi, Xpress, and scientific Python packages.
- `cd alg_tester && python alg_tester_app.py`: launch the visual algorithm tester.
- `cd baseline && python run_myalgorithm.py ../alg_tester/example/example_B2_b10.json --timelimit 10`: run `myalgorithm.py` and print feasibility/objective details.
- `python -m unittest discover -s baseline/tests -p 'test_*.py'`: run the baseline harness tests.
- `python baseline/benchmark_instances.py --root . --limit 1 --run-baseline --timelimit 0.001`: smoke-test instance discovery and baseline feasibility.

## Coding Style & Naming Conventions

Use standard Python with 4-space indentation and descriptive snake_case names for functions, variables, and modules. Keep public entry points stable, especially `algorithm()` in `baseline/myalgorithm.py` and checker utilities in `baseline/utils.py`. Prefer small helper functions with type hints for new benchmark or solver code. There is no repository formatter configuration; keep imports grouped as standard library, third-party, then local modules.

## Testing Guidelines

Tests use Python `unittest` and live in `baseline/tests/` with `test_*.py` filenames. Add regression tests for feasibility, runner behavior, benchmark discovery, and time-limit fallback logic when changing solver code. Some tests expect local ignored training data under `data/train` and `data/train 2`; restore those challenge files before running the full suite.

## Commit & Pull Request Guidelines

Recent history uses short imperative or scoped messages such as `docs: prune duplicate planning files` and `Ignore gurobi.lic to keep WLS license credentials out of the repo.` Keep commits focused and mention the affected area when useful. Pull requests should include a concise description, the commands run, any benchmark or objective impact, and screenshots only for `alg_tester` UI changes.

## Security & Configuration Tips

Do not commit `gurobi.lic`, solver credentials, generated PDFs/zips, or local training data. `.gitignore` already excludes these paths; keep new generated artifacts out of version control unless they are small, reproducible fixtures.
