# M1 Submission Safety Completion Record

Date: 2026-07-06

## Status

M1 entry-point safety is implemented and verified for valid official challenge
instances. This document is now a completion record, not an open execution
plan.

## Requirement Decision

The M1 "always feasible" guarantee is limited to valid official challenge
instances. For malformed or impossible `prob_info` objects, `algorithm()` should
fail clearly and must never silently return unvalidated or infeasible output.

## Implemented Boundary

- `baseline/myalgorithm.py::algorithm(prob_info, timelimit=60)` keeps the public
  challenge signature unchanged.
- The delegated `baseline_greedy.greedyalgorithm()` call is wrapped at the
  submission entry point.
- Delegated candidates are accepted only after `utils.check_feasibility()` says
  they are feasible.
- Delegated exceptions, malformed output, or infeasible output fall back to
  `baseline_greedy._serial_fallback_solution(prob_info, verify=True)`.
- The serial fallback is verified before return.

## Regression Coverage

- `baseline/tests/test_submission_safety.py` covers zero time limit, delegated
  exceptions, empty operations, malformed delegated output, and malformed partial
  fallback recovery.
- `baseline/tests/test_phase0_harness.py` keeps tiny-time-limit fallback and
  benchmark harness behavior covered.

## Verification Evidence

Latest accepted M1 gate evidence:

- `conda run -n ogc2026 python -m unittest discover -s baseline/tests -p 'test_*.py'`
  -> `Ran 16 tests`, `OK`.
- `conda run -n ogc2026 python -m py_compile baseline/myalgorithm.py baseline/baseline_greedy.py baseline/benchmark_instances.py baseline/utils.py`
  -> exit 0.
- `conda run -n ogc2026 python run_myalgorithm.py ../alg_tester/example/example_B2_b10.json --timelimit 0`
  -> `Feasible : True (stage=5)`.
- `conda run -n ogc2026 python run_myalgorithm.py ../alg_tester/example/example_B2_b10.json --timelimit 0.001`
  -> `Feasible : True (stage=5)`.
- `conda run -n ogc2026 python run_myalgorithm.py '../data/train 2/prob_1.json' --timelimit 0.001`
  -> `Feasible : True (stage=5)`.

Latest M0/M1 cleanup verification:

- `conda run -n ogc2026 python -m unittest discover -s baseline/tests -p 'test_*.py'`
  -> `Ran 21 tests`, `OK`.
- `conda run -n ogc2026 python -m py_compile baseline/myalgorithm.py baseline/baseline_greedy.py baseline/benchmark_instances.py baseline/utils.py`
  -> exit 0.
- `conda run -n ogc2026 python baseline/benchmark_instances.py --root . --set-name smoke-3 --solver myalgorithm --timelimit 0.001 --format json`
  -> three feasible Stage-5 rows with `solver: myalgorithm`.

## Remaining Non-M1 Gate

M1 safety itself is complete. M0 measurement cleanup has removed the solver
labeling hazard for M2:

- `--solver baseline_greedy` executes `baseline_greedy.greedyalgorithm()`;
- `--solver myalgorithm` executes `myalgorithm.algorithm()`;
- `--solver stats_only` runs no solver;
- `dev-10` 60s baseline is recorded in `experiments/results/2026-07-06-dev10-baseline-60s.json`.

Remaining next action: record a full 60s `daily-40` baseline artifact.
