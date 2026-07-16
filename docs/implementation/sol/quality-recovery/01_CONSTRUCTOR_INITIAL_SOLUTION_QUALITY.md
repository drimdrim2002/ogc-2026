# Phase 1 — Constructor 초기해 품질 회복

중요도: 최우선  
선행 gate: Phase 0 PASS  
기본 비교: frozen Python `eager_regret`  
수정 범위: P3 constructor와 직접 필요한 계측/테스트

## 1. 문제와 목표

기존 계측에서 constructor profile은 모두 50,000 candidate cap에 도달했고 serial safe-tail fallback 비율 중앙값은 약 96%였다. 이는 ALNS가 긴 시간 동안 나쁜 초기해를 복구하게 만들며, 같은 120초 안에 탐색할 수 있는 횟수와 최종 objective를 동시에 악화시킨다.

목표는 constructor 시간을 무작정 늘리는 것이 아니라 다음 세 가지를 함께 개선하는 것이다.

1. deadline 안에 더 많은 block을 exact-safe insertion으로 배치한다.
2. serial fallback 의존도를 크게 줄인다.
3. Stage 5와 deterministic contract를 지키면서 initial objective와 최종 fixed-time objective를 낮춘다.

## 2. 범위와 비범위

허용:

- `eager_regret`와 `profile_priority`의 선택/평가 구조 개선
- candidate cache, 중복 제거, incremental feasibility/score 재사용
- profile quota, candidate quota, safe-tail 전환 기준의 benchmark variant
- constructor 전용 telemetry 및 focused tests
- 고정 candidate/order parity가 입증된 경우에 한한 P3 native 실험

금지:

- ALNS acceptance/neighborhood/stall 변경
- retime/assignment/MIP/interlock 의미론 변경
- official checker, baseline greedy, data 변경
- constructor gate 전에 production default 변경
- approximate geometry로 `UNKNOWN`을 feasible 처리

## 3. 순차 실행 절차

### 1.1 Baseline funnel 분석

1. Phase 0 frozen source/config을 로드한다.
2. hard-10 60초×3 seeds에서 profile별로 다음 funnel을 추출한다.
   - blocks total → blocks with candidates → exact committed → escalation → safe-tail
   - candidates attempted/unique/rejected by reason
   - per-block candidate generation, exact relation, commit 시간
   - cap hit, deadline hit, remaining block 수
3. fallback을 `no candidate`, `budget guard`, `candidate cap`, `commit invalidation`, `other`로 분류한다.
4. initial objective와 final objective의 상관, constructor 1초당 objective 개선을 계산한다.

### 1.2 최소 원인 선택

한 session에서 하나의 원인만 선택한다. 우선순위는 다음과 같다.

1. 동일 state/block 후보의 반복 생성과 exact 재판정
2. 모든 unplaced block을 eager 재평가하는 `O(n²)` funnel
3. candidate cap이 낮은 가치 후보로 소진되는 ordering
4. commit 후 과도한 invalidation
5. profile 간 중복 탐색

원인별 예상 절감량을 실제 call/time 비중으로 제시하지 못하면 구현으로 넘어가지 않는다.

### 1.3 Fixed-work 구현 및 검증

1. 선택 원인을 해결하는 최소 variant를 구현한다.
2. 작은 synthetic와 frozen real state에서 baseline/candidate 후보를 canonical tuple로 비교한다.
3. 의도적 ordering 차이가 없으면 candidate/order/score/commit 결과가 동일해야 한다.
4. 의도적 policy 차이가 있으면 첫 divergence와 선택 근거를 trace로 남긴다.
5. cache key에 state revision, block, bay/time/position 및 geometry relevant state가 포함되는지 검증한다.
6. rollback/deadline/exception이 incumbent를 바꾸지 않음을 테스트한다.

최소 focused tests:

```bash
cd baseline
<python> -m unittest \
  tests.test_construct \
  tests.test_construct_integration \
  tests.test_checker_contract \
  tests.test_deadline \
  tests.test_exception_matrix \
  tests.test_four_state_parity
```

### 1.4 Constructor-only ablation

1. `eager_regret` frozen baseline과 candidate를 alternating order로 실행한다.
2. 먼저 hard-10 60초×1 seed에서 gross regression을 screening한다.
3. screening 통과 후 60초×3 seeds 전부 실행한다.
4. candidate count가 아니라 initial checker objective와 fallback funnel을 paired 비교한다.
5. `profile_priority` 기존 evidence인 9W/1L을 현재 source에서 다시 검증한다. 과거 결과를 재사용하지 않는다.

### 1.5 Full daily-40 constructor enablement gate

설계 문서 §13의 P5/P6 활성화 선행조건을 별도로 검증한다.

1. 공식 40개 instance를 60초, default seed `20260710`에서 constructor candidate로 실행한다.
2. 40/40 Stage 5, constructor p90/max, deadline hit, fallback rate를 산출한다.
3. frozen constructor baseline과 initial objective를 paired 비교한다.
4. 이 gate를 통과하지 못하면 retime/LNS 결합 개선을 이유로 Phase 2 이후에 진입하지 않는다.

### 1.6 End-to-end ablation

1. constructor 이후 retime/ALNS는 동일 frozen 설정으로 둔다.
2. hard-10 60초×3 seeds에서 cumulative candidate와 frozen baseline을 paired 실행한다.
3. initial advantage가 final에서 유지되는지, ALNS가 더 많은 iteration을 얻는지 확인한다.
4. 120초 `prob_23` Python default에서도 catastrophic loss가 없는지 확인한다.

### 1.7 Default 결정

모든 gate를 통과한 policy만 cumulative candidate config로 승격한다. 이 단계에서는 benchmark config hash를 먼저 동결하며, production default 반영은 scheduler audit 후 별도 승인된 fix session에서만 한다.

## 4. 필수 지표

- constructor p50/p90/max seconds, deadline hit
- initial `Z1/Z2/Z3/total`, Stage, parity
- fallback blocks/rate 및 reason별 count
- candidate attempts/unique, cap hit, cache hit, exact calls
- profile별 complete/fallback/quality
- end-to-end W/T/L, median/p90/worst relative delta, Borda
- final ALNS iterations, accepted/new-best, primal integral

## 5. Hard gate

다음을 모두 만족해야 PASS다.

- Stage 5/parity/trace/timeout 공통 gate 통과
- constructor p90 `<=8s`, max `<=12s`, constructor deadline hit 0
- daily-40 60초 default seed에서 40/40 Stage 5
- serial fallback 비율 중앙값이 baseline보다 최소 20 percentage point 감소하고 목표 `<=70%`
- hard-10 constructor-only initial objective Win > Loss, median paired delta < 0
- end-to-end hard-10 Win > Loss, median < 0, Borda non-worse
- 설명되지 않은 단일 run objective `>10%` 악화 0
- `prob_23` 120초 Python 경로 non-loss
- 전체 baseline tests PASS

시간 gate를 만족시키기 위해 후보를 조기에 끊어 initial/final 품질을 악화시키면 FAIL이다. fallback 목표를 못 맞춰도 품질과 처리량 개선이 매우 크다는 이유만으로 자동 승격하지 않으며 scheduler가 gate 변경을 승인하지 않는다.

## 6. 실패 분기

- candidate 동일성/geometry 오류 → constructor correctness diagnose
- 시간은 개선, initial 품질 악화 → ordering/selection diagnose
- initial은 개선, final 악화 → Phase 3이 아니라 먼저 constructor-to-ALNS interface diagnose
- profile_priority seed 편향 → RNG/order diagnose
- native P3가 boundary/exact 증폭 → Phase 2로 실험 이관, Python constructor 유지
- gate 미달 → variant NO-GO, eager_regret default 유지

## 7. 산출물

```text
artifacts/ogc_sage/quality-recovery/p1-<run-id>/
  identity.json
  funnel-baseline.json
  fixed-work.json
  constructor-only/{raw.jsonl,summary.json}
  daily40-60/{raw.jsonl,summary.json}
  end-to-end/{raw.jsonl,summary.json}
  prob23.json
  comparison.json
  gate.json
  SHA256SUMS
```

## 8. Worker 실행 프롬프트

```text
역할: quality recovery Phase 1 constructor worker.

Phase 0 PASS artifact와 frozen config hash를 입력으로 받고,
docs/implementation/sol/quality-recovery/01_CONSTRUCTOR_INITIAL_SOLUTION_QUALITY.md를 순서대로 실행하라.

먼저 constructor fallback funnel을 계측해 가장 큰 원인 하나를 증명하라. 해당 원인만 최소 수정하고 fixed-work, focused tests, constructor-only, daily-40 60초 constructor gate, end-to-end hard-10, prob_23 non-loss 순으로 검증하라. ALNS/retime/assignment/MIP/interlock 의미론은 바꾸지 말라. gate 전 production default를 바꾸지 말라.

모든 결과는 새 immutable artifact와 이 문서 실행 기록에 남겨라. FAIL/NO-GO면 같은 session에서 다른 아이디어를 연속 적용하지 말고 원인과 다음 diagnose 범위를 보고하라. commit/push하지 말라.
```

## 9. 실행 기록

아직 실행되지 않음.
