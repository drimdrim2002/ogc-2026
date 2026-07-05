# OGC2026 Baseline Partial Fallback 작업 요약

작성일: 2026-07-05

## 배경

지난 작업에서 `baseline_greedy.py`는 40개 전체 training instance에 대해 verified serial fallback을 제공하도록 보강되었다. 그 결과 `timelimit=1`과 `timelimit=30` 모두 40/40 feasible은 달성했지만, 30초 동안 Phase 1이 만든 greedy 배치를 deadline 시점에 모두 버리고 전체 serial fallback으로 돌아가면서 objective 개선이 전혀 없었다.

이번 작업의 목표는 30초 benchmark에서 deadline 전에 만들어진 partial greedy assignments를 가능한 범위에서 보존하고, 나머지 block만 serial fallback으로 채워 objective 개선을 만드는 것이었다.

## 핵심 변경

### 1. Deadline 예외에 partial assignments 전달

`baseline/baseline_greedy.py`의 `_TimeBudgetExpired`를 확장해 현재까지 완료된 assignments를 담을 수 있게 했다.

- `_TimeBudgetExpired(assignments=None)` 추가
- `_check_deadline_with_assignments(deadline, assignments)` 추가
- `_place_blocks` 내부 deadline check가 현재까지의 `result`를 예외에 싣도록 변경
- `_find_earliest_slot` 내부에서 deadline이 발생해도 `_place_blocks` 호출 경계에서 partial result를 회수하도록 처리

이제 Phase 1이 deadline에 걸려도 `greedyalgorithm`은 이미 배치한 block 정보를 잃지 않는다.

### 2. Partial serial completion 추가

`_complete_with_serial_fallback(prob_info, assignments, verify=True)`를 추가했다.

동작:

1. 입력 partial assignment를 정규화한다.
2. partial assignment를 fixed bay schedule로 반영한다.
3. 아직 배치되지 않은 block만 release/due/processing 순으로 serial fallback 방식으로 채운다.
4. `utils.check_feasibility`로 최종 solution을 검증한다.
5. 전체 partial이 infeasible이면 feasible prefix만 보존하고 나머지를 serial로 채운다.
6. feasible partial 후보가 serial fallback보다 objective가 나쁘면 serial fallback을 선택한다.

중요한 관찰은 greedy partial 전체가 그대로 feasible하지 않은 경우가 많다는 점이었다. Phase 1은 새 block을 넣을 때 현재 crane entry/exit는 검사하지만, 새 block 삽입이 기존 block의 미래 ENTRY/EXIT 경로를 깨는 경우를 완전히 막지는 못한다. 그래서 partial 전체를 무조건 보존하면 Stage 2/3 위반이 발생한다.

이번 구현은 이 문제를 피하기 위해 partial assignment의 feasible prefix를 찾고, objective guard로 serial fallback보다 나쁜 후보는 버린다.

### 3. Greedy algorithm fallback 경로 변경

`greedyalgorithm`의 deadline 처리 경로를 변경했다.

- Phase 1 deadline:
  - partial assignments가 있으면 `_complete_with_serial_fallback`으로 completion
  - partial이 없으면 기존처럼 verified serial fallback
- Repair deadline:
  - 현재 assignments 또는 예외에 담긴 partial을 `_complete_with_serial_fallback`으로 completion
  - partial이 없으면 verified serial fallback
- 최종 infeasible:
  - 기존처럼 verified serial fallback

### 4. 테스트 추가

`baseline/tests/test_phase0_harness.py`에 다음 테스트를 추가했다.

- `_complete_with_serial_fallback`이 valid partial assignment를 보존하고 나머지를 채우는지 검증
- infeasible suffix가 있는 partial에서 serial fallback보다 나쁜 trimmed prefix를 선택하지 않는지 검증
- `_place_blocks` deadline 예외가 이미 완료한 assignments를 담아 올리는지 검증

기존 테스트와 함께 총 10개 unittest가 통과한다.

## 검증 결과

### Unit / compile 검증

명령:

```bash
conda run -n ogc2026 python -m unittest baseline.tests.test_phase0_harness -v
conda run -n ogc2026 python -m compileall -q baseline
```

결과:

- `10 tests OK`
- `compileall` exit 0

### Serial fallback 안전망 검증

전체 40개 training instance에 대해 `_serial_fallback_solution`을 다시 검증했다.

결과:

- checked 40
- bad 0
- stage `{5: 40}`

즉 fallback 안전망은 계속 유지된다.

### 30초 전체 benchmark

명령:

```bash
conda run -n ogc2026 python baseline/benchmark_instances.py --run-baseline --timelimit 30 --format json > /tmp/ogc2026_tl30_after_objective_guard.json
```

결과:

- rows 40
- feasible 40
- bad 0
- stage `{5: 40}`
- Phase 1 partial completion path: 40/40
- deadline 전 partial assignments: min 53, max 96
- feasibility trim 후 보존 assignments: min 0, max 26, median 10
- elapsed total 1152.496s
- elapsed median 28.722s
- elapsed max 29.726s

Serial fallback 대비 objective 비교:

- improved 39
- equal 1
- worse 0
- total improvement 2,494,568,782.695
- median improvement 41,788,756.0
- max improvement 560,172,722.423

상위 개선 사례:

| Instance | Serial objective | New objective | Improvement |
| --- | ---: | ---: | ---: |
| `data/train/prob_31.json` | 3,673,327,140.236 | 3,113,154,417.813 | 560,172,722.423 |
| `data/train/prob_21.json` | 731,811,175.158 | 481,024,793.971 | 250,786,381.186 |
| `data/train/prob_28.json` | 1,029,458,760.960 | 864,609,548.960 | 164,849,212.000 |
| `data/train/prob_38.json` | 3,755,178,947.542 | 3,629,222,096.542 | 125,956,851.000 |
| `data/train/prob_27.json` | 1,700,548,642.687 | 1,594,324,631.687 | 106,224,011.000 |

## 결과의 의미

이번 변경으로 baseline은 다음 성질을 갖게 되었다.

1. 40개 전체 instance에서 feasible solution을 안정적으로 반환한다.
2. 30초 동안 만든 greedy partial work를 모두 버리지 않는다.
3. Feasible하지 않은 partial은 prefix 단위로 잘라 안전하게 completion한다.
4. Partial completion이 serial fallback보다 나쁘면 serial fallback을 선택하므로 objective 악화가 없다.
5. 이전의 `timelimit=30` 결과가 `timelimit=1` serial fallback과 동일하던 문제는 해결되었다.

다만 이것은 아직 최종 solver 품질이 좋다는 뜻은 아니다. 현재는 "안전한 baseline + partial greedy 효과 확인" 단계다. 보존되는 greedy assignment median이 10개에 불과하므로, Phase 1이 만든 work 중 상당 부분은 아직 feasibility replay 관점에서 버려진다.

## 남은 과제

다음 개선 방향은 partial을 더 많이 살리는 것이다.

우선순위 높은 작업:

1. Phase 1 placement 시 새 block이 기존 block의 미래 ENTRY/EXIT crane path를 깨는지 검사한다.
2. prefix만 보존하는 대신 feasible subset repair 또는 conflict-local trimming을 검토한다.
3. `_complete_with_serial_fallback`의 prefix 후보 평가 비용을 줄인다.
4. 30초 benchmark 외에 짧은 timelimit sweep을 추가해 1s/5s/10s/30s objective curve를 본다.
5. 보존된 partial count와 objective improvement의 상관을 instance별로 기록한다.

## 변경 파일

이번 작업에서 직접 변경한 파일:

- `baseline/baseline_greedy.py`
- `baseline/tests/test_phase0_harness.py`

이번 작업 이전부터 존재하던 변경 또는 사용자 변경으로 보이는 파일은 건드리지 않았다.

- `.gitignore`
- `docs/2026-06-30-ogc2026-chat-context-summary-ko.md`
- `docs/ONBOARDING.md`
- `docs/ogc2026_problem_statement_analysis_en.md`
- `docs/ogc2026_problem_statement_analysis_ko.md`

