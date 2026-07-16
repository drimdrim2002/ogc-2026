# Phase 3 — ALNS trajectory 및 acceptance 품질 안정화

중요도: 높음  
선행 gate: Phase 2 PASS 또는 native 명시적 OFF/NO-GO  
수정 범위: ALNS state transition, acceptance, operator adaptation, stall, neighborhood schedule

## 1. 문제와 목표

동일한 후보 집합도 acceptance, RNG 소비, operator weight update, stall trigger, retime cadence가 달라지면 탐색 trajectory가 크게 달라진다. 현재 native dominance guard는 Python simulated annealing 경로보다 강한 reject를 만들 수 있어 fixed-work parity가 있어도 final quality가 달라질 수 있다. 또한 `stall_time_fraction=0.08`, iteration stall, operator segment가 실제 iteration 수가 적은 instance에서 탐색을 조기에 편향시킬 수 있다.

목표는 backend와 무관한 하나의 명시적 trajectory contract를 만들고, seed 하나의 우연이 아닌 다중 seed에서 anytime 품질을 개선하는 것이다.

## 2. 고정할 두 모드

ALNS 실험은 다음을 섞지 않는다.

- **parity mode**: Python/native가 같은 후보, RNG draw, acceptance, state transition을 사용한다. backend 성능 비교용이다.
- **guarded extension mode**: candidate가 current/best를 지배하지 못할 때 추가 reject하는 실험 정책이다. Python/native 양쪽에 동일하게 적용해야 한다.

native에서만 dominance guard를 켠 결과를 backend 우위 증거로 사용하지 않는다. production 후보는 두 모드 중 하나를 명시적으로 선택한다.

## 3. 범위와 비범위

허용:

- SA temperature/cooling과 acceptance event 기록
- RNG stream 분리 또는 draw count 고정
- operator score/weight segment, min floor, selection cadence
- iteration/time stall 조건과 restart/retime/densify trigger
- budget-aware neighborhood portfolio
- validated-best/current/candidate state transition 명확화

금지:

- constructor/native boundary/retime model/assignment objective를 동시에 변경
- checker 전 candidate를 best로 간주
- 실패 candidate 때문에 validated incumbent를 덮어쓰기
- seed별 특수값이나 instance 이름 hardcoding

## 4. 순차 실행 절차

### 3.1 Trajectory baseline 동결

1. Phase 2 최종 결정에 따라 backend를 고정한다. native NO-GO면 Python만 사용한다.
2. `prob_23`과 hard-10에서 iteration별 event trace를 저장한다.
3. 각 event에 다음을 넣는다.
   - iteration/operator/destroyed IDs digest
   - candidate digest와 delta
   - temperature, random draw, acceptance threshold/reason
   - current/best objective before/after
   - operator score/weight before/after
   - stall/restart/retime trigger
   - remaining budget
4. trace 크기는 bounded summary와 optional benchmark artifact로 분리한다. production output은 오염시키지 않는다.

### 3.2 첫 divergence 찾기

1. parity fixture에서 Python/native 또는 legacy/candidate를 같은 seed로 실행한다.
2. 첫 candidate digest, RNG draw, acceptance, current state 중 처음 달라지는 지점을 찾는다.
3. divergence 이전과 이후를 구분해 원인을 다음 중 하나로 분류한다.
   - candidate generation/order
   - RNG consumption
   - acceptance policy
   - operator weight update
   - deadline/cadence
4. Phase 2 소유 원인이면 Phase 2 diagnose로 반송한다.

### 3.3 단일 정책 ablation

아래 순서로 한 번에 하나만 비교한다.

1. backend 공통 parity acceptance 대 backend 공통 guarded acceptance
2. legacy stall 대 time-only/iteration-only/combined stall
3. legacy neighborhood 대 budget-aware portfolio
4. legacy operator segment/cooling 대 후보 값

각 variant는 동일 candidate/backend/config에서 실행한다. 여러 변경을 한 config로 묶어 이긴 이유를 불명확하게 하지 않는다.

### 3.4 Focused correctness

최소 검증:

```bash
cd baseline
<python> -m unittest \
  tests.test_alns \
  tests.test_alns_integration \
  tests.test_neighborhoods \
  tests.test_lns_telemetry \
  tests.test_long_budget_anchor \
  tests.test_deadline \
  tests.test_exception_matrix
```

추가 invariant:

- 같은 accepted transition이면 같은 current digest
- rejected candidate는 current/best를 바꾸지 않음
- validated-best objective trace non-increasing
- state resume 전후 RNG/operator/pending-retime 의미론 동일
- backend failure가 acceptance draw 순서를 바꾸지 않음

### 3.5 Fixed-time screening 및 full matrix

1. `prob_23` 120초×3 seeds로 catastrophic tail을 먼저 검사한다.
2. hard-10 60초×3 seeds를 baseline/candidate alternating order로 실행한다.
3. 통과한 단일 정책만 누적하고, 누적 candidate를 다시 hard-10 180초×3 seeds에서 검증한다.
4. 같은 instance/seed에서 180초 objective가 60초보다 나빠지면 release blocker로 기록한다.
5. mean뿐 아니라 seed별 W/T/L, p90/worst와 primal integral을 판정한다.

## 5. 필수 지표

- iterations/second, repair attempts, accepted, improving, new-best
- operator별 calls/success/score/weight trajectory
- acceptance reason별 count와 worse-move magnitude
- stall/restart/retime trigger 시점과 남은 budget
- first improvement, best-event count, primal integral
- seed별 objective/component, W/T/L, median/p90/worst, Borda
- first divergence event와 원인 class

## 6. Hard gate

- Stage 5/parity/trace/timeout 공통 gate PASS
- `prob_23` 각 seed에서 cumulative baseline 대비 catastrophic loss 없음; median non-worse
- hard-10 60초와 180초 모두 Win > Loss, median delta < 0, Borda non-worse
- worst paired regression `<=10%`; 초과 run은 원인 규명 및 retry 전 승격 금지
- same-seed 60→180 longer-budget regression 0
- primal integral 또는 time-to-first-improvement 중 하나 이상 개선되고 다른 하나가 중대 악화하지 않음
- backend 공통 trajectory contract와 mode가 문서/config에 명시됨
- 전체 baseline tests PASS

단일 seed 평균 개선으로 tail regression을 덮지 않는다. guarded mode가 iteration을 크게 줄이면 objective가 조금 좋아도 180초 안정성 증거가 없이는 승격하지 않는다.

## 7. 실패 분기

- 첫 divergence가 candidate/order → Phase 2 또는 Phase 1로 반송
- RNG draw mismatch → ALNS deterministic diagnose
- short 개선/long 악화 → cooling/stall/cadence diagnose
- accepted candidate 후 checker reject → transactional validation diagnose
- operator 한 개 독점 → adaptation/weight floor diagnose
- 모든 variant NO-GO → legacy ALNS 유지도 유효한 PASS 결정으로 기록하고 Phase 4 진행

## 8. 산출물

```text
artifacts/ogc_sage/quality-recovery/p3-<run-id>/
  identity.json
  trajectory-contract.json
  divergence.json
  ablations/<variant>/
  prob23/
  hard10-60/
  hard10-180/
  comparison.json
  gate.json
  SHA256SUMS
```

## 9. Worker 실행 프롬프트

```text
역할: quality recovery Phase 3 ALNS trajectory worker.

Phase 0~2 audit 결과와 최종 backend 결정을 읽고,
docs/implementation/sol/quality-recovery/03_ALNS_TRAJECTORY_AND_ACCEPTANCE.md만 실행하라.

iteration event trace로 첫 divergence를 찾고 parity mode와 guarded extension을 분리하라. acceptance, stall, neighborhood, adaptation을 한 번에 하나씩 ablation하라. focused tests 후 prob_23, hard-10 60/180초 다중 seed에서 평가하라. constructor/native kernel/retime/assignment를 같이 변경하지 말라.

gate가 실패하면 같은 session에서 다음 정책까지 연쇄 수정하지 말고 diagnose 대상을 보고하라. artifact와 실행 기록을 남기고 commit/push하지 말라.
```

## 10. 실행 기록

아직 실행되지 않음.
