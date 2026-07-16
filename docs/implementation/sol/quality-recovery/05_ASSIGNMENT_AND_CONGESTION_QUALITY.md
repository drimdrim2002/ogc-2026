# Phase 5 — Assignment 및 congestion guide 품질 개선

중요도: 중간  
선행 gate: Phase 4 PASS 또는 legacy retime 유지 결정  
수정 범위: P2 assignment seed/portfolio와 constructor/repair에 전달되는 guide

## 1. 문제와 목표

Assignment는 exact geometry schedule을 직접 결정하지 않지만 constructor가 어떤 bay/time 후보를 먼저 보게 하는지, `Z2` 작업장 분산과 `Z3` 혼잡을 어떻게 trade-off하는지에 큰 영향을 준다. 부정확한 surrogate를 hard constraint로 사용하면 feasible/좋은 공간 배치를 제거할 수 있고, guide 계산 비용이 크면 constructor와 ALNS 시간이 줄어든다.

목표는 assignment를 안전한 soft guide로 유지하면서 초기 `Z2/Z3`와 최종 weighted objective를 개선하고, Phase 1 constructor 시간/fallback gate를 다시 깨뜨리지 않는 것이다.

## 2. 범위와 비범위

허용:

- assignment seed portfolio와 priority/bay/start hint
- exact objective와 일치하는 weighted delta 계산
- congestion proxy의 calibration 및 diversity
- duplicate seed 제거와 budget cap
- assignment failure/fallback telemetry

금지:

- surrogate를 geometry feasibility hard constraint로 사용
- checker의 floored `Z2`와 다른 값을 최종 objective로 사용
- instance 이름별 규칙
- constructor/ALNS/retime 정책을 동시에 변경
- Gurobi assignment 실패 시 safe incumbent 제거

## 3. 순차 실행 절차

### 5.1 Baseline guide audit

1. hard-10에서 assignment runtime/status/gap/lower bound/seed 수를 기록한다.
2. seed별 bay allocation/start hint/priority digest와 중복률을 계산한다.
3. 각 seed를 constructor에 넣었을 때 다음을 연결한다.
   - initial objective와 `Z1/Z2/Z3`
   - fallback/candidate/exact calls
   - constructor time
   - 최종 objective
4. surrogate score와 official weighted delta의 rank correlation을 구한다.
5. checker의 `Z2` floor semantics와 internal objective parity를 다시 확인한다.

### 5.2 원인 선택

우선순위:

1. duplicate/low-diversity seeds
2. official weighted objective와 guide rank 불일치
3. congestion proxy가 dense conflict를 구분하지 못함
4. assignment runtime이 constructor 예산을 잠식
5. failure/status 처리로 빈 portfolio 발생

한 원인의 실제 빈도와 objective 영향이 증명된 뒤에만 수정한다.

### 5.3 Fixed-work 및 synthetic 검증

1. 작은 instance에서 assignment delta와 full recompute가 일치하는지 검증한다.
2. `Z2` floor 경계, 동률, zero-weight, multiple workshops를 포함한다.
3. guide가 없는 경우와 optimizer failure에서 deterministic Python fallback을 확인한다.
4. portfolio ordering/dedup이 seed diversity와 repeatability를 보존하는지 확인한다.
5. guide는 후보 ordering만 바꾸고 exact geometry/commit이 최종 권위임을 테스트한다.

Focused tests:

```bash
cd baseline
<python> -m unittest \
  tests.test_assignment \
  tests.test_assignment_integration \
  tests.test_construct \
  tests.test_construct_integration \
  tests.test_checker_contract \
  tests.test_exception_matrix
```

### 5.4 Constructor interaction gate

1. Phase 1 승인 constructor를 고정한다.
2. hard-10 60초×3 seeds에서 assignment baseline/candidate를 constructor-only로 비교한다.
3. Phase 1의 p90/max time, fallback rate, deadline hit gate를 그대로 재검증한다.
4. initial total뿐 아니라 weighted `w1ΔZ1`, `w2ΔZ2`, `w3ΔZ3`를 보고한다.

### 5.5 End-to-end gate

1. cumulative retime/ALNS/backend를 동일하게 둔다.
2. hard-10 60초×3 seeds paired matrix를 실행한다.
3. 통과 시 180초×3 seeds와 `prob_23` 120초 non-loss를 실행한다.
4. assignment 비용이 늘면 time-to-first-incumbent와 ALNS iteration 감소를 품질과 함께 판정한다.

## 4. 필수 지표

- assignment runtime/status/gap/lower bound, seeds/unique seeds/diversity
- surrogate vs official delta correlation
- constructor initial components/total, fallback/cap/deadline/time
- end-to-end weighted component improvements
- W/T/L, Borda, median/p90/worst, primal integral, iterations
- optimizer absent/license/error fallback

## 5. Hard gate

- Stage 5/parity/trace/timeout 공통 gate PASS
- internal floored `Z2`와 checker parity `<=1e-6`
- guide가 exact feasibility를 제거하지 않음
- Phase 1 constructor p90 `<=8s`, max `<=12s`, deadline hit 0 및 fallback 개선 gate 유지
- constructor-only와 end-to-end hard-10 모두 Win > Loss, median < 0, Borda non-worse
- weighted components 중 하나를 개선하기 위해 total과 다른 주요 component tail을 중대 악화하지 않음
- 180초와 `prob_23` non-loss, longer-budget regression 0
- Gurobi/assignment failure fallback Stage 5
- 전체 baseline tests PASS

## 6. 실패 분기

- objective parity/floor 오류 → assignment correctness diagnose
- initial 개선, final 악화 → guide diversity/ALNS interaction diagnose
- constructor fallback 증가 → Phase 1 interaction diagnose
- runtime 지배 → seed cap/model budget diagnose
- surrogate correlation 낮음 → proxy 재설계 또는 legacy 유지
- 후보 NO-GO → 기존 assignment 유지 후 Phase 6

## 7. 산출물

```text
artifacts/ogc_sage/quality-recovery/p5-<run-id>/
  identity.json
  assignment-audit.json
  fixed-work.json
  constructor-interaction/
  hard10-60/
  hard10-180/
  prob23.json
  comparison.json
  gate.json
  SHA256SUMS
```

## 8. Worker 실행 프롬프트

```text
역할: quality recovery Phase 5 assignment/congestion worker.

Phase 1~4에서 승인된 cumulative config를 고정하고,
docs/implementation/sol/quality-recovery/05_ASSIGNMENT_AND_CONGESTION_QUALITY.md만 실행하라.

assignment guide와 official objective의 관계, seed 중복/다양성, constructor interaction을 먼저 계측하라. 가장 큰 원인 하나만 수정하고 fixed-work/fallback, constructor-only, hard-10 60/180초, prob_23 순으로 검증하라. guide를 hard feasibility constraint로 만들지 말고 다른 phase 정책을 같이 바꾸지 말라.

gate 실패 시 다음 diagnose 범위를 기록하고 종료하라. artifact와 실행 기록을 남기고 commit/push하지 말라.
```

## 9. 실행 기록

아직 실행되지 않음.
