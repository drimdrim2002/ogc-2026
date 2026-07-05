# M1 제출 안전성 계획

일자: 2026-07-06

## 목표

제출용 `algorithm(prob_info, timelimit)` 진입점을 강화하여, 입력이 유효한
공식 챌린지 인스턴스인 경우 아주 작은 시간 제한, 내부 solver 예외, 부분
할당 후 마감 시점 경로까지 포함해 항상 `utils.check_feasibility`를 통과한
해를 반환하도록 한다.

이 계획은 문서화 전용이다. 이 세션에서는 solver 코드를 변경하지 않는다.

## 범위

- 현재 fallback 경로를 읽고 안전성 공백을 식별한다.
- `algorithm()` 수준 보호에 필요한 최소 구현을 명시한다.
- 아주 작은 시간 제한, 주입된 예외, 잘못된 solver 출력, 부분 fallback
  동작에 대한 unittest 범위를 명시한다.
- 현재 증거와 요구사항 결정을 기록한다.

## 범위 제외

- LNS, ALNS, multi-start, scoring 변경 같은 objective 개선 작업.
- C++ 또는 raster geometry 가속.
- 공개 `algorithm(prob_info, timelimit)` signature 변경.

## 현재 Fallback 맵

- `baseline/myalgorithm.py`는 현재 `algorithm()` 내부에서 `baseline_greedy`를
  import하고 `baseline_greedy.greedyalgorithm(...)` 결과를 그대로 반환한다.
  제출 진입점에는 바깥쪽 `try/except`, 최종 검증, 위임된 solver가 예외를
  발생시켰을 때 사용할 local fallback이 없다.
- `baseline/baseline_greedy.py`에는 내부 deadline guard가 있다.
  `_TimeBudgetExpired`는 부분 할당을 담고, `_place_blocks()`는 비용이 큰
  탐색 loop 전에 deadline을 확인하며, `greedyalgorithm()`은 Phase 1과
  repair에서 deadline 만료를 catch한다.
- `_serial_fallback_solution(prob_info, verify=True)`는 보수적인
  one-block-per-bay schedule을 만들고 `check_feasibility`로 검증한다.
- `_complete_with_serial_fallback(prob_info, assignments, verify=True)`는
  부분 할당을 정규화하고, 누락된 block을 serial 방식으로 채우며, 완성된
  solution을 검증하고, infeasible한 부분 prefix를 trim한 뒤, 부분 완성이
  infeasible하거나 serial fallback보다 나쁘면 검증된 serial solution으로
  fallback한다.
- 기존 테스트는 example instance에서 serial fallback feasibility, partial
  preservation, serial보다 나쁜 partial rejection, `_place_blocks()`에서의
  deadline exception propagation, 그리고 아주 작은 time limit의
  `greedyalgorithm()` 경로 하나를 다룬다.

## 관찰된 증거

2026-07-06에 실행한 명령:

```bash
conda run -n ogc2026 python -m unittest discover -s baseline/tests -p 'test_*.py'
```

결과: 테스트 10개 통과.

```bash
conda run -n ogc2026 python baseline/run_myalgorithm.py ../alg_tester/example/example_B2_b10.json --timelimit 0
conda run -n ogc2026 python baseline/run_myalgorithm.py ../alg_tester/example/example_B2_b10.json --timelimit 0.001
```

결과: 둘 다 `Feasible : True (stage=5)`를 반환했다.

환경 참고: 같은 테스트를 기본 `python`으로 실행하면 solver 실행 전에 실패했다.
그 환경에는 `shapely`가 설치되어 있지 않기 때문이다. repository contract는
`ogc2026` conda 환경을 사용하므로, 이는 solver fallback 결과가 아니라 환경
설정 문제다.

Exception 경로 probe:

```bash
python -c 'import sys, types; sys.path.insert(0, "baseline"); import myalgorithm; fake = types.ModuleType("baseline_greedy"); fake.greedyalgorithm = lambda prob, timelimit: (_ for _ in ()).throw(RuntimeError("boom")); sys.modules["baseline_greedy"] = fake; \
try:
    myalgorithm.algorithm({"bays": [], "blocks": []}, 1)
except Exception as exc:
    print(type(exc).__name__, str(exc))
else:
    print("returned")'
```

결과: `RuntimeError boom`. 이는 현재 `algorithm()` 진입점이 위임된 solver
예외를 catch하지 않는다는 점을 확인한다.

## 안전성 공백

1. 제출 진입점은 `baseline_greedy.greedyalgorithm()`에서 발생한 모든
   예상치 못한 예외를 전파할 수 있다.
2. 제출 진입점은 위임된 solver의 반환값을 반환하기 전에 재검증하지 않는다.
3. 위임된 solver가 예외 없이 malformed 또는 infeasible solution을 반환하면,
   현재 `algorithm()`은 이를 그대로 통과시킨다.
4. `_serial_fallback_solution(..., verify=True)`가 최종 correctness guard지만,
   이것이 예외를 발생시키면 더 높은 수준의 recovery 경로가 없다. M1의 feasible
   return 보장은 valid official instance로 제한되므로, 이 입력 범위에서는 serial
   fallback이 반드시 validate되어야 한다. malformed 또는 impossible input은
   검증되지 않은 output을 만들지 말고 명확히 실패해야 한다.
5. 테스트는 내부 greedy fallback 동작을 다루지만, 주입된 failure 하에서 실제
   `myalgorithm.algorithm()` 안전성 contract는 다루지 않는다.

## 구현 계획

1. `baseline/myalgorithm.py`의 `algorithm()` 근처에 작은 안전성 helper를
   추가한다. 공개 signature는 변경하지 않는다.
2. `utils.check_feasibility`를 호출하고, checker 예외를 validation failure로
   catch하며, `feasible=True`일 때만 `True`를 반환하는
   `_validated(prob_info, solution) -> bool` helper를 추가한다.
3. verification을 켠 serial fallback 경로를 호출하고
   `check_feasibility`를 통과한 solution만 반환하는
   `_verified_serial_fallback(prob_info) -> dict` helper를 추가한다.
4. 위임된 greedy 호출을 `try/except Exception`으로 감싼다.
5. greedy가 validated feasible solution을 반환하면 그대로 반환한다.
6. greedy가 예외를 발생시키거나, malformed output을 반환하거나, infeasible
   output을 반환하면 `_verified_serial_fallback(prob_info)`를 반환한다.
7. 검증되지 않은 candidate는 반환하지 않는다. 어떤 입력에 대해서도 검증된
   fallback을 만들 수 없다면, infeasible solution을 조용히 반환하지 말고
   명확한 예외를 발생시킨다. 이는 공식 instance contract가 feasibility를
   제공하는 동안 "no unvalidated solution" 규칙을 보존한다.
8. objective 개선 코드는 건드리지 않는다.

## 테스트 계획

`baseline/tests/` 아래에 unittest coverage를 추가하거나 확장한다.

- `test_algorithm_zero_timelimit_returns_feasible_fallback`: `example_B2_b10`에
  `timelimit=0`으로 `myalgorithm.algorithm()`을 호출하고,
  `check_feasibility(...)[feasible] is True`를 assert한다.
- `test_algorithm_tiny_timelimit_returns_feasible_fallback`: training instance에
  `timelimit=0.001`로 `myalgorithm.algorithm()`을 호출하고 feasibility를
  assert한다.
- `test_algorithm_catches_greedy_exception`: monkeypatch 또는 fake
  `baseline_greedy.greedyalgorithm`이 `RuntimeError`를 raise하도록 만들고,
  valid instance에서 `algorithm()`이 여전히 feasible fallback을 반환하는지
  assert한다.
- `test_algorithm_replaces_infeasible_greedy_output`: greedy가
  `{"operations": {}}`를 반환하도록 monkeypatch하고, `algorithm()`이 Stage-1
  failure를 그대로 통과시키지 않고 feasible fallback을 반환하는지 assert한다.
- `test_algorithm_replaces_malformed_greedy_output`: greedy가 non-dict 또는
  malformed operations payload를 반환하도록 monkeypatch하고 fallback
  feasibility를 assert한다.
- `test_complete_with_serial_fallback_rejects_malformed_partial`: out-of-range
  `bay_id`, 누락된 field, 또는 mismatched `block_id`가 있는 partial assignment를
  전달하고, verified serial fallback이 반환되는지 assert한다.

권장 검증 명령:

```bash
conda run -n ogc2026 python -m unittest discover -s baseline/tests -p 'test_*.py'
conda run -n ogc2026 python baseline/run_myalgorithm.py ../alg_tester/example/example_B2_b10.json --timelimit 0
conda run -n ogc2026 python baseline/run_myalgorithm.py ../alg_tester/example/example_B2_b10.json --timelimit 0.001
conda run -n ogc2026 python baseline/benchmark_instances.py --root . --limit 1 --run-baseline --timelimit 0.001
```

unit test가 통과한 뒤 선택적으로 더 넓은 safety sweep을 실행한다:

```bash
conda run -n ogc2026 python baseline/benchmark_instances.py --root . --run-baseline --timelimit 0.001 --format json
```

수용 기준:

- `algorithm(prob_info, timelimit)`은 valid official-format input에서
  `timelimit`이 `0`, near-zero, 또는 positive일 때 Stage-5 feasible solution을
  반환한다.
- 주입된 greedy exception은 valid instance에서 `algorithm()` 밖으로 escape하지
  않는다.
- Infeasible 또는 malformed delegated solver output은 반환되지 않는다.
- 기존 baseline test는 계속 green 상태다.
- LNS/ALNS, C++ geometry, objective-scoring 변경은 포함하지 않는다.

## 요구사항 결정

결정: M1의 "always returns feasible" 보장은 valid official challenge instance로
제한한다.

Malformed 또는 impossible `prob_info` 객체에 대해서는 `algorithm()`이 억지로
solution을 만들어내려고 하지 않는다. 명확하게 실패해야 하며, 검증되지 않았거나
infeasible한 solution을 조용히 반환해서는 안 된다.
