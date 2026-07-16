# Phase 6 — Candidate MIP 및 interlock 조건부 검토

중요도: 조건부  
선행 gate: Phase 5 PASS 또는 기존 assignment 유지 결정  
초기 default: MIP OFF, interlock OFF

## 1. 진입 원칙

이 phase는 기능을 켜기 위한 단계가 아니다. 누적 heuristic candidate의 telemetry가 해결되지 않은 잔여 병목을 증명할 때만 candidate-selection MIP 또는 interlock을 각각 독립적으로 검토하고, 증거가 없거나 품질 gate를 못 넘으면 OFF를 최종 결정하는 단계다.

과거 candidate MIP는 30-instance 비교에서 `11W/3T/16L`로 전체 우위가 아니었고, interlock은 strict install 증거가 없었다. 이 과거 결과는 현재 source의 최종 판정은 아니지만, 무조건 활성화하지 않을 강한 prior다.

## 2. 기능별 진입 조건

### Candidate-selection MIP

다음이 모두 관측될 때만 실행한다.

- heuristic repair가 feasible candidates를 충분히 만들지만 조합 선택에서 손실
- target destroy set이 최대 16 blocks, block당 최대 32 candidates, product cap 512 안에 들어옴
- 최소 remaining budget과 3초 timebox를 지킬 수 있음
- candidate selection 후 exact retime/commit으로 strict install 가능성이 있음

### Interlock

다음이 모두 관측될 때만 실행한다.

- tardiness `Z1 > 0`
- union-energy pressure `>=0.45`
- union-safe search가 실제 stall
- one-way/nested relation candidate가 exact geometry에서 존재
- 남은 전체 예산의 최대 8% 안에서 실행 가능

조건이 없으면 기능별로 `NOT_APPLICABLE/OFF`를 기록하며 phase는 정상 완료할 수 있다.

## 3. 범위와 비범위

허용:

- MIP candidate dominance/duplicate pruning, no-good/cut, warm start, timebox
- interlock activation, exact relation candidate, nested schedule retime
- 기능별 telemetry와 failure fallback

금지:

- 두 기능을 한 variant에서 동시에 켜 첫 검증 수행
- approximate geometry를 interlock authority로 사용
- MIP feasible solution을 full local/checker 검증 없이 설치
- 4-thread Gurobi와 multiprocessing portfolio 동시 실행
- sparse/all-zero evidence에서 default ON

## 4. 순차 실행 절차

### 6.1 Residual need audit

1. hard-10 60/180초 cumulative traces를 읽는다.
2. MIP와 interlock 진입 조건의 instance/seed별 빈도를 계산한다.
3. potential objective headroom과 예상 time cost를 산출한다.
4. 기능별로 `PROCEED` 또는 `OFF_NO_EVIDENCE`를 결정한다.

둘 다 OFF_NO_EVIDENCE이면 코드 변경 없이 tests와 default audit 후 PASS한다.

### 6.2 Candidate MIP 독립 ablation

진입한 경우 다음 순서를 따른다.

1. synthetic에서 candidate-selection objective/delta와 exhaustive small oracle 비교
2. infeasible/cut loop, zero solution, timeout with/without SolCount, license/import/model failure
3. MIP result를 transactional exact commit 및 optional retime 후 full validation
4. hard subset 60초×3 seeds screening
5. hard-10 60/180초×3 seeds baseline/candidate paired

MIP variant는 interlock OFF를 유지한다.

### 6.3 Interlock 독립 ablation

진입한 경우 다음 순서를 따른다.

1. synthetic one-way/nested witness에서 exact relation과 expected `Z1` improvement 검증
2. real dense subset을 Phase 0 manifest에서 사전 정의
3. activation false-positive/false-negative와 geometry/retime 시간 측정
4. dense subset 60/180초×3 seeds paired
5. strict installs가 존재할 때만 hard-10 60/180초×3 seeds 확대

Interlock variant는 MIP OFF를 유지한다.

### 6.4 Failure matrix 및 full tests

```bash
cd baseline
<python> -m unittest \
  tests.test_repair_mip \
  tests.test_repair_mip_integration \
  tests.test_interlock \
  tests.test_interlock_integration \
  tests.test_exception_matrix \
  tests.test_deadline \
  tests.test_checker_contract
<python> -m unittest discover -s tests -p 'test_*.py'
```

### 6.5 Default 동결

MIP와 interlock 각각 `ON`, `OFF_NO_EVIDENCE`, `OFF_NO_GO` 중 하나로 기록한다. 하나가 PASS했다고 다른 하나를 함께 켜지 않는다. production default 변경은 integrated gate 전까지 보류할 수 있으며 Phase 7에 들어가는 cumulative config hash에는 실제 상태를 명시한다.

## 5. 필수 지표

- activation eligible/attempted/skipped reason
- candidate blocks/count/product, cuts/iterations/status/SolCount/gap/time
- interlock relation candidates, exact witnesses, retime attempts
- feasible/accepted/strict installs와 objective delta
- failure/fallback 및 incumbent digest
- iterations and phase time displaced
- W/T/L, Borda, median/p90/worst, weighted components

## 6. 기능별 Hard gate

### MIP ON gate

- exhaustive small-oracle/correctness/fallback PASS
- hard-10 60/180초 모두 Win > Loss, median < 0, Borda non-worse
- throughput 감소를 포함한 final/primal-integral 품질 우위
- 설명되지 않은 `>10%` tail regression과 longer-budget regression 0
- 과거 `11W/3T/16L` 패턴이 현재 matrix에서 반복되지 않음

### Interlock ON gate

- synthetic와 real exact witness PASS
- 사전 정의 dense subset에서 strict install `>0`, Win > Loss, feasibility regression 0
- hard-10 확대에서도 median non-worse/Borda non-worse
- activation이 남은 예산 8%를 넘지 않음

공통 gate나 기능별 gate를 못 넘으면 OFF가 최종 결정이다. OFF는 phase 실패가 아니라 안전한 판정이다.

## 7. 실패 분기

- model correctness/commit 실패 → 해당 feature diagnose
- no strict installs → OFF_NO_EVIDENCE, activation threshold를 느슨하게 해 억지 실행하지 않음
- quality tail regression → OFF_NO_GO
- license/CPU blocker → BLOCKED 후 fresh retry, 다른 기능 결과와 섞지 않음
- 두 기능 모두 ON 후보 → Phase 7에서 각각과 결합 variant를 별도 비교한 뒤 최종 config 결정

## 8. 산출물

```text
artifacts/ogc_sage/quality-recovery/p6-<run-id>/
  identity.json
  residual-need.json
  mip/{fixed-work,hard-subset,hard10}/
  interlock/{synthetic,dense-subset,hard10}/
  decisions.json
  gate.json
  SHA256SUMS
```

## 9. Worker 실행 프롬프트

```text
역할: quality recovery Phase 6 conditional MIP/interlock worker.

누적 Phase 5 telemetry와 config를 읽고,
docs/implementation/sol/quality-recovery/06_MIP_INTERLOCK_CONDITIONAL.md만 실행하라.

먼저 residual need audit로 MIP와 interlock 진입 조건을 각각 판정하라. 증거 없는 기능은 수정하지 않고 OFF_NO_EVIDENCE로 동결하라. 진행하는 기능도 서로 독립적으로 fixed-work/failure matrix/fixed-time을 검증하라. gate를 못 넘으면 OFF_NO_GO로 결정하라.

두 기능을 첫 variant에 같이 켜지 말라. artifact와 실행 기록을 남기고 commit/push하지 말라.
```

## 10. 실행 기록

아직 실행되지 않음.
