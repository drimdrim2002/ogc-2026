# M1 Submission Safety Plan

Date: 2026-07-06

## Goal

Harden the submitted `algorithm(prob_info, timelimit)` entry point so it returns
a solution that has passed `utils.check_feasibility` whenever the input is a
valid official challenge instance, including tiny time limits, internal solver
exceptions, and partial-assignment deadline paths.

This plan is documentation-only. No solver code is changed in this session.

## In Scope

- Read the current fallback path and identify safety gaps.
- Specify the minimum implementation needed for `algorithm()` level protection.
- Specify unittest coverage for tiny time limits, injected exceptions, invalid
  solver output, and partial fallback behavior.
- Record the current evidence and requirement decision.

## Out Of Scope

- Objective improvement work such as LNS, ALNS, multi-start, or scoring changes.
- C++ or raster geometry acceleration.
- Changes to the public `algorithm(prob_info, timelimit)` signature.

## Current Fallback Map

- `baseline/myalgorithm.py` currently imports `baseline_greedy` inside
  `algorithm()` and directly returns `baseline_greedy.greedyalgorithm(...)`.
  There is no outer `try/except`, no final validation at the submission entry
  point, and no local fallback if the delegated solver raises.
- `baseline/baseline_greedy.py` has internal deadline guards:
  `_TimeBudgetExpired` carries partial assignments, `_place_blocks()` checks
  the deadline before expensive search loops, and `greedyalgorithm()` catches
  deadline expiration in Phase 1 and repair.
- `_serial_fallback_solution(prob_info, verify=True)` builds a conservative
  one-block-per-bay schedule and validates it with `check_feasibility`.
- `_complete_with_serial_fallback(prob_info, assignments, verify=True)`
  normalizes partial assignments, serially fills missing blocks, validates the
  completed solution, trims infeasible partial prefixes, and falls back to the
  verified serial solution when partial completion is infeasible or worse than
  serial fallback.
- Existing tests cover serial fallback feasibility on the example instance,
  partial preservation, partial rejection when worse than serial, deadline
  exception propagation from `_place_blocks()`, and one tiny-timelimit
  `greedyalgorithm()` path.

## Observed Evidence

Commands run on 2026-07-06:

```bash
conda run -n ogc2026 python -m unittest discover -s baseline/tests -p 'test_*.py'
```

Result: 10 tests passed.

```bash
conda run -n ogc2026 python baseline/run_myalgorithm.py ../alg_tester/example/example_B2_b10.json --timelimit 0
conda run -n ogc2026 python baseline/run_myalgorithm.py ../alg_tester/example/example_B2_b10.json --timelimit 0.001
```

Result: both returned `Feasible : True (stage=5)`.

Environment note: running the same tests with the default `python` failed before
solver execution because `shapely` is not installed there. The repository
contract uses the `ogc2026` conda environment, so this is an environment setup
issue rather than a solver fallback result.

Exception-path probe:

```bash
python -c 'import sys, types; sys.path.insert(0, "baseline"); import myalgorithm; fake = types.ModuleType("baseline_greedy"); fake.greedyalgorithm = lambda prob, timelimit: (_ for _ in ()).throw(RuntimeError("boom")); sys.modules["baseline_greedy"] = fake; \
try:
    myalgorithm.algorithm({"bays": [], "blocks": []}, 1)
except Exception as exc:
    print(type(exc).__name__, str(exc))
else:
    print("returned")'
```

Result: `RuntimeError boom`. This confirms the current `algorithm()` entry point
does not catch delegated solver exceptions.

## Safety Gaps

1. The submission entry point can propagate any unexpected exception from
   `baseline_greedy.greedyalgorithm()`.
2. The submission entry point does not revalidate the delegated solver's return
   value before returning it.
3. If the delegated solver returns a malformed or infeasible solution without
   raising, `algorithm()` will currently pass it through.
4. `_serial_fallback_solution(..., verify=True)` is the final correctness guard,
   but if it raises there is no higher-level recovery path. The M1 feasible
   return guarantee is scoped to valid official instances, so serial fallback
   must validate for that input class. Malformed or impossible inputs should
   fail clearly rather than producing unvalidated output.
5. Tests exercise internal greedy fallback behavior, but not the actual
   `myalgorithm.algorithm()` safety contract under injected failures.

## Implementation Plan

1. Add small safety helpers near `algorithm()` in `baseline/myalgorithm.py`.
   Keep the public signature unchanged.
2. Add a `_validated(prob_info, solution) -> bool` helper that calls
   `utils.check_feasibility` and returns `True` only for `feasible=True`,
   catching checker exceptions as validation failures.
3. Add a `_verified_serial_fallback(prob_info) -> dict` helper that calls the
   serial fallback path with verification enabled and returns only a solution
   that passes `check_feasibility`.
4. Wrap the delegated greedy call in `try/except Exception`.
5. If greedy returns a validated feasible solution, return it.
6. If greedy raises, returns malformed output, or returns infeasible output,
   return `_verified_serial_fallback(prob_info)`.
7. Do not return any unvalidated candidate. If no verified fallback can be built
   for an input, raise a clear exception rather than silently returning an
   infeasible solution. This preserves the "no unvalidated solution" rule while
   the official-instance contract supplies feasibility.
8. Leave objective-improvement code untouched.

## Test Plan

Add or extend unittest coverage under `baseline/tests/`.

- `test_algorithm_zero_timelimit_returns_feasible_fallback`: call
  `myalgorithm.algorithm()` with `timelimit=0` on `example_B2_b10` and assert
  `check_feasibility(...)[feasible] is True`.
- `test_algorithm_tiny_timelimit_returns_feasible_fallback`: call
  `myalgorithm.algorithm()` with `timelimit=0.001` on a training instance and
  assert feasibility.
- `test_algorithm_catches_greedy_exception`: monkeypatch or fake
  `baseline_greedy.greedyalgorithm` to raise `RuntimeError`; assert
  `algorithm()` still returns a feasible fallback on a valid instance.
- `test_algorithm_replaces_infeasible_greedy_output`: monkeypatch greedy to
  return `{"operations": {}}`; assert `algorithm()` returns a feasible fallback
  instead of passing through the Stage-1 failure.
- `test_algorithm_replaces_malformed_greedy_output`: monkeypatch greedy to
  return a non-dict or malformed operations payload; assert fallback feasibility.
- `test_complete_with_serial_fallback_rejects_malformed_partial`: pass a partial
  assignment with an out-of-range `bay_id`, missing field, or mismatched
  `block_id`; assert the verified serial fallback is returned.

Recommended verification commands:

```bash
conda run -n ogc2026 python -m unittest discover -s baseline/tests -p 'test_*.py'
conda run -n ogc2026 python baseline/run_myalgorithm.py ../alg_tester/example/example_B2_b10.json --timelimit 0
conda run -n ogc2026 python baseline/run_myalgorithm.py ../alg_tester/example/example_B2_b10.json --timelimit 0.001
conda run -n ogc2026 python baseline/benchmark_instances.py --root . --limit 1 --run-baseline --timelimit 0.001
```

Optional wider safety sweep after the unit tests pass:

```bash
conda run -n ogc2026 python baseline/benchmark_instances.py --root . --run-baseline --timelimit 0.001 --format json
```

Acceptance criteria:

- `algorithm(prob_info, timelimit)` returns a Stage-5 feasible solution on valid
  official-format inputs when `timelimit` is `0`, near-zero, or positive.
- Injected greedy exceptions do not escape `algorithm()` on valid instances.
- Infeasible or malformed delegated solver output is not returned.
- Existing baseline tests remain green.
- No LNS/ALNS, C++ geometry, or objective-scoring changes are included.

## Requirement Decision

Decision: the M1 "always returns feasible" guarantee is limited to valid
official challenge instances.

For malformed or impossible `prob_info` objects, `algorithm()` should not try to
fabricate a solution. It should fail clearly, and it must never silently return
an unvalidated or infeasible solution.
