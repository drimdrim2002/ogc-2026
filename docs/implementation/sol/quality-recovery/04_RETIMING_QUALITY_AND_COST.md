# Phase 4 — Retiming 품질 및 비용 개선

중요도: 중상  
선행 gate: Phase 3 PASS 또는 legacy ALNS 유지 결정  
수정 범위: retime trigger, component selection, model build/solve/reuse, exact skip

## 1. 문제와 목표

Retiming은 공간 배치를 유지한 채 tardiness `Z1`을 낮출 수 있지만, 반복적인 Gurobi model build/solve와 singleton/component 호출이 ALNS iteration을 빼앗을 수 있다. 개선 없는 retime이 자주 호출되면 품질 feature가 오히려 fixed-time 품질을 떨어뜨린다.

목표는 strict `Z1`/total non-worsening contract를 유지하면서 retime wall-clock을 줄이거나 같은 시간에 더 많은 유효 개선을 설치하는 것이다.

## 2. 범위와 비범위

허용:

- affected component batching과 singleton skip
- exact `Z1` lower-bound 기반 skip
- model structure/template 재사용과 warm start
- trigger/cadence/budget guard
- status/SolCount/deadline 처리와 telemetry

금지:

- geometry/assignment 변경
- infeasible 또는 total-worse retime 설치
- Gurobi failure 때 incumbent 제거
- MIP repair/interlock을 동시에 활성화
- license/CPU 오염 결과를 품질 gate에 포함

## 3. 순차 실행 절차

### 4.1 Baseline call graph

1. hard-10 trace에서 retime trigger→component→model build→optimize→serialize/checker→install funnel을 추출한다.
2. call별 affected blocks/components, singleton 여부, predicted lower bound, build/solve 시간, status, SolCount, `Z1/total` delta를 기록한다.
3. 다음 낭비를 분리한다.
   - exact하게 개선 불가능한 component
   - 동일 component의 짧은 간격 재호출
   - build가 solve보다 큰 호출
   - timeout/unknown 후 설치 0
   - strict install이 없는 대형 호출

### 4.2 Fixed-work ablation

기존 `benchmark_retime_fixed_work.py`를 현재 cumulative source에 맞춰 실행한다.

1. legacy와 exact-skip이 동일 입력 snapshot/affected IDs를 받는다.
2. skip 판단은 heuristic이 아니라 증명 가능한 lower bound여야 한다.
3. 실행한 model의 output snapshot, Stage, `Z1/Z2/Z3/total`을 비교한다.
4. batching/reuse variant는 component 순서와 final accepted solution을 검증한다.

### 4.3 단일 수정 우선순위

1. exact no-improvement component skip
2. singleton/duplicate call 제거
3. model build reuse/batching
4. trigger cadence와 minimum remaining budget

fresh fix session은 하나만 선택한다. lower-bound correctness가 입증되지 않은 skip은 적용하지 않는다.

### 4.4 Focused tests

```bash
cd baseline
<python> -m unittest \
  tests.test_retime \
  tests.test_retime_integration \
  tests.test_alns \
  tests.test_exception_matrix \
  tests.test_deadline \
  tests.test_checker_contract
```

추가 gate:

- retime output `Z1 <= input Z1`
- accepted retime total `<=` current total, strict install은 `< best`
- timeout/no solution/import/license/model failure 시 동일 incumbent digest
- pending affected IDs가 성공/실패 후 계약대로 clear/retain

### 4.5 Fixed-time paired benchmark

1. hard-10 60초×3 seeds legacy/candidate alternating run
2. 통과 시 180초×3 seeds
3. `prob_23` 120초×1 seed non-loss anchor
4. retime을 줄여 얻은 ALNS iteration 증가가 실제 objective에 연결되는지 확인
5. solver profiler를 켠 결과는 시간 gate에서 제외

## 4. 필수 지표

- retime triggers/calls/components/singletons/skipped/models
- build/solve/checker/total seconds
- status/SolCount/timeout/failure reason
- attempted/strict installs, `Z1` 및 total improvement per second
- ALNS iterations gained/lost
- objective/component W/T/L, median/p90/worst, Borda, primal integral

## 5. Hard gate

- Stage 5/parity/trace/timeout 공통 gate PASS
- retime이 설치한 모든 snapshot에서 `Z1` non-worse 및 total non-worse
- fixed-work 결과 parity 또는 exact skip 증명 PASS
- retime p50 총 시간이 baseline보다 최소 20% 감소하거나, 동일/작은 시간 증가로 strict objective improvement 수와 final quality가 유의하게 증가
- hard-10 60초 Win > Loss, median < 0, Borda non-worse
- 180초와 `prob_23` non-loss, longer-budget regression 0
- Gurobi absent/import/license/model/timeout fallback Stage 5
- 전체 baseline tests PASS

호출 수만 줄고 final quality가 나빠지면 FAIL이다. `Z1` 개선이 `Z2/Z3` 또는 total 악화로 이어지는 install은 correctness failure다.

## 6. 실패 분기

- exact skip false negative/positive → lower-bound diagnose
- build 지배 → model lifecycle diagnose
- solve 지배 → component size/budget diagnose
- retime 감소 후 quality 악화 → trigger timing/candidate interaction diagnose
- license/environment 문제 → 코드 변경 없이 BLOCKED 또는 fallback gate만 판정
- 모든 후보 NO-GO → legacy retime 유지 후 Phase 5

## 7. 산출물

```text
artifacts/ogc_sage/quality-recovery/p4-<run-id>/
  identity.json
  retime-funnel.json
  fixed-work.json
  hard10-60/
  hard10-180/
  prob23.json
  comparison.json
  gate.json
  SHA256SUMS
```

## 8. Worker 실행 프롬프트

```text
역할: quality recovery Phase 4 retiming worker.

승인된 Phase 3 cumulative candidate를 사용하고,
docs/implementation/sol/quality-recovery/04_RETIMING_QUALITY_AND_COST.md만 실행하라.

retime call funnel을 계측하고 exact skip, duplicate/singleton 제거, model reuse, trigger 중 가장 큰 원인 하나만 수정하라. fixed-work와 failure fallback을 먼저 검증한 뒤 hard-10 60/180초와 prob_23 non-loss를 실행하라. assignment/MIP/interlock/ALNS 정책을 같이 바꾸지 말라.

gate 실패 시 새 diagnose 범위를 남기고 종료하라. artifact와 실행 기록을 남기고 commit/push하지 말라.
```

## 9. 실행 기록

아직 실행되지 않음.
