# BASELINE KNOWLEDGE BASE

## OVERVIEW

Core submission harness: solver entry point, reference greedy solver, checker,
benchmark CLI, runners, and `unittest` regression suite.

## WHERE TO LOOK

| Task | Location | Notes |
| --- | --- | --- |
| Submitted algorithm | `myalgorithm.py` | Stable public `algorithm(prob_info, timelimit)` API. |
| Greedy construction/repair | `baseline_greedy.py` | Placement, ordering, deadline, fallback logic. |
| Feasibility/scoring | `utils.py` | Canonical checker and geometry model. |
| Run custom solution | `run_myalgorithm.py` | Debug defaults plus CLI override. |
| Run reference solver | `run_baseline_greedy.py` | Baseline-only local runner. |
| Benchmark/statistics | `benchmark_instances.py` | Instance discovery, named sets, JSON/CSV output. |
| Regression tests | `tests/` | Harness and submission-safety contracts. |

## CODE MAP

| Symbol | Location | Contract |
| --- | --- | --- |
| `algorithm` | `myalgorithm.py` | Return a feasible solution or verified fallback. |
| `_SUBMISSION_BLOCK_ORDER_MODE` | `myalgorithm.py` | Currently `slack`; benchmark tests report this. |
| `greedyalgorithm` | `baseline_greedy.py` | Main solver; accepts `block_order_mode`. |
| `_complete_with_serial_fallback` | `baseline_greedy.py` | Repairs or replaces partial assignments. |
| `_serial_fallback_solution` | `baseline_greedy.py` | Must remain checker-feasible. |
| `_TimeBudgetExpired` | `baseline_greedy.py` | Carries partial assignments on deadline. |
| `check_feasibility` | `utils.py` | Test oracle; stage 5 means valid solution. |
| `main` | `benchmark_instances.py` | CLI path tested through direct `argv`. |

## CONVENTIONS

- Keep `algorithm(prob_info, timelimit=60)` importable from this directory.
- `myalgorithm.py` catches greedy exceptions and invalid candidates; do not
  bypass `_is_feasible_solution` before returning a solver candidate.
- `baseline_greedy.py` has multiple block-order modes: `edd`, `release_edd`,
  `slack`, `latest_safe_entry`, and `preference_pressure`.
- Tiny or zero timelimits must still produce a feasible serial fallback.
- `benchmark_instances.py` discovers `data/train` and `data/train 2`; named
  sets are part of the tested interface.
- Tests patch imports inside test methods and suppress solver stdout with
  `redirect_stdout`.

## ANTI-PATTERNS

- Do not change `utils.py` casually; it is the local scoring contract.
- Do not make `--solver myalgorithm` call `baseline_greedy` directly.
- Do not hide invalid greedy output by returning unchecked candidates.
- Do not delete or weaken tests for fallback, solver dispatch, or benchmark
  output labels.
- Do not commit local training data or generated benchmark artifacts from here.

## COMMANDS

```bash
/opt/homebrew/Caskroom/miniforge/base/envs/ogc-2026/bin/python -m unittest discover -s baseline/tests -p 'test_*.py'
cd baseline && /opt/homebrew/Caskroom/miniforge/base/envs/ogc-2026/bin/python run_myalgorithm.py ../alg_tester/example/example_B2_b10.json --timelimit 10
/opt/homebrew/Caskroom/miniforge/base/envs/ogc-2026/bin/python baseline/benchmark_instances.py --root . --set-name smoke-3 --solver myalgorithm --timelimit 0.001
```

## NOTES

- Full tests require ignored local data under `data/train` and `data/train 2`.
- `run_myalgorithm.py` default points at `../alg_tester/example/train 2/prob_1.json`; tests assert it exists.
