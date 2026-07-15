# OGC-SAGE 성능 개선 실행 플레이북

작성일: 2026-07-14
상위 계획: `docs/implementation/sol/10_PERFORMANCE_ACCELERATION_PLAN.md`

## 1. 목적

이 디렉터리는 현재 solver의 경쟁 성능 문제를 **한 단계씩 분리해 수정하고, 고정된 hard-10에서 동일 조건으로 검증**하기 위한 실행 문서 모음이다. 조사와 대안 설계는 팀별로 병렬 수행할 수 있지만, solver 변경의 통합과 기준선 승격은 반드시 아래 순서대로 한 단계씩 진행한다.

## 2. 고정 실행 순서

| 단계 | 문서 | 목표 | 종료 시 가능한 결정 |
|---:|---|---|---|
| 0 | [00_BASELINE_HARD10_AND_TELEMETRY.md](00_BASELINE_HARD10_AND_TELEMETRY.md) | 계측 수정, hard-10 및 비교 계약 동결 | 이후 실험 시작 허용/차단 |
| 1 | [01_ALNS_BUDGET_AND_STATE_CONTINUITY.md](01_ALNS_BUDGET_AND_STATE_CONTINUITY.md) | 16회 제한, 예산 낭비, anchor/extension 상태 단절 해결 | 새 누적 기준선 승격/롤백 |
| 2 | [02_MIP_OPERABILITY_PROBE.md](02_MIP_OPERABILITY_PROBE.md) | MIP이 실제로 호출·해결·검증되는지 복구하고 조기 판정 | 계속 사용/기본 OFF 유지 |
| 3 | [03_CONSTRUCTOR_FALLBACK_REDUCTION.md](03_CONSTRUCTOR_FALLBACK_REDUCTION.md) | 직렬 fallback 97.5%를 낮추고 병렬 초기해 강화 | 새 누적 기준선 승격/롤백 |
| 4 | [04_HEURISTIC_REPAIR_ACCELERATION.md](04_HEURISTIC_REPAIR_ACCELERATION.md) | 가장 큰 Python 병목인 repair 처리량 개선 | 새 누적 기준선 승격/롤백 |
| 5 | [05_RETIME_COST_REDUCTION.md](05_RETIME_COST_REDUCTION.md) | retime trigger, 모델 조립, solve 비용 개선 | 새 누적 기준선 승격/롤백 |
| 6 | [06_NEIGHBORHOOD_STRENGTHENING.md](06_NEIGHBORHOOD_STRENGTHENING.md) | 작은 destroy 이웃을 보완해 전역 재구성 능력 확보 | 새 누적 기준선 승격/롤백 |
| 7 | [07_MIP_INTERLOCK_ACTIVATION.md](07_MIP_INTERLOCK_ACTIVATION.md) | 누적 기준선에서 MIP/interlock 기본 활성화 최종 결정 | 최종 제출 구성 동결 |
| 8 | [08_FINAL_180S_VALIDATION.md](08_FINAL_180S_VALIDATION.md) | 최초 기준선과 최종 후보를 hard-10 180초로 비교 | release candidate 승인/기각 |

다음 단계는 이전 단계 문서의 종료 조건과 증거 기록이 끝나기 전에는 시작하지 않는다.

## 3. 현재 확인된 기준 사실

- 공식 checker 기반 40 instances × 60/180초 × 3 seeds = 240회는 Stage 5 240/240이다.
- constructor profile의 block별 fallback 비율 중앙값은 약 97.5%이며, 모든 profile이 50,000 candidate cap에 도달했다.
- 60초 run은 중앙 23.46초만 사용하고, 120개 중 118개가 16회 iteration limit로 종료했다.
- ALNS 기본값은 warm-up 32회, weight segment 50회, stall 50회이므로 60초 anchor에서는 적응이 시작되지 않는다.
- 180초 phase 시간의 약 74.1%가 heuristic repair, 약 19.5%가 LNS retime이다.
- candidate MIP의 기존 dense 비교는 heuristic LNS 대비 9W/1T/8L이므로, 작동성 복구와 기본 활성화는 별도 결정이어야 한다.

## 4. hard-10 계약

모든 단계의 고정시간 품질 비교는 0단계에서 생성하는 다음 manifest를 단일 기준으로 사용한다.

`docs/implementation/sol/performance/HARD10_MANIFEST.json`

기존 artifact로 계산한 임시 후보는 다음과 같다.

1. `prob_38.json`
2. `prob_23.json`
3. `prob_25.json`
4. `prob_40.json`
5. `prob_27.json`
6. `prob_21.json`
7. `prob_39.json`
8. `prob_33.json`
9. `prob_20.json`
10. `prob_35.json`

이 목록은 0단계에서 anchor와 extension의 계측이 분리된 뒤 한 번만 재계산한다. 이후 결과를 보고 인스턴스를 교체하지 않는다. manifest에는 instance hash, 선정 공식, 입력 artifact hash, 순위와 tie-break를 기록한다.

## 5. 팀 운영 계약

### 병렬로 가능한 작업

- 해당 단계의 root-cause 조사
- 대안 설계와 위험 분석
- 읽기 전용 프로파일 분석
- 테스트 및 benchmark 설계
- 단계 문서의 제안 섹션 작성

### 반드시 직렬로 수행할 작업

- 공용 solver 코드 변경
- benchmark schema 변경
- hard-10 manifest 변경
- cumulative baseline 승격
- submission default 변경

각 팀은 자기 단계 문서와 명시된 코드 범위만 수정한다. 다른 단계의 구현을 미리 포함하지 않는다. 여러 최적화를 한 patch에 섞지 않는다.

## 6. 기준선과 비교 방법

두 기준선을 구분한다.

- **original baseline**: 0단계에서 동결한 개선 전 commit/config/dataset/environment
- **cumulative baseline**: 직전 단계까지 승격된 최신 commit

각 단계는 candidate를 cumulative baseline과 비교한다. 8단계에서는 최종 후보를 cumulative baseline뿐 아니라 original baseline과도 비교한다.

### 단계별 60초 paired matrix

- 데이터: 고정 hard-10
- budget: 60초
- 기본 seed: `20260710`
- 실행: baseline 10회 + candidate 10회
- 순서: instance별 baseline/candidate를 교차 실행해 시스템 부하 편향을 줄임
- 추가 seed: W-L 차이가 2 이하이거나 median 판정이 불명확할 때 `20260711`, `20260712` 추가

기존 artifact와 새 candidate를 직접 비교하지 않는다. source/config가 다른 두 variant는 같은 실행 세션과 환경에서 다시 paired 실행한다.

### 공통 hard blocker

- 모든 실행 Stage 5
- exception, outer timeout, checker failure 0
- objective parity 상대 오차 `<=1e-6`
- validated-best non-increasing
- wall-clock deadline과 checker reserve 준수
- `baseline/utils.py`와 `baseline/baseline_greedy.py` 변경 없음

### 공통 품질 판정

- lower objective 기준 Win > Loss
- median paired relative delta < 0
- Borda가 baseline보다 낮아지지 않음
- 단일 인스턴스의 중대한 회귀는 별도 원인 없이 평균 개선으로 상쇄하지 않음
- `w1*Z1`, `w2*Z2`, `w3*Z3`를 분리 보고

성능 단계는 추가로 fixed-work 처리량 개선을 입증해야 한다. 알고리즘 단계는 처리량이 아니라 fixed-time objective 개선으로 승격한다.

## 7. 단계별 증거 기록

각 단계 문서의 `결과 기록` 섹션에 다음을 남긴다.

- baseline/candidate commit과 config hash
- dataset/hard-10 manifest hash
- 실행 환경과 명령
- raw/summary artifact 경로와 SHA-256
- W/T/L, median/p90/worst relative delta, Borda
- Z1/Z2/Z3 가중 변화
- phase time, iterations, candidates, accepted/new-best
- 테스트 결과
- 승격/기각/차단 결정과 이유

raw benchmark 결과는 기존 정책대로 gitignored artifact에 두고, tracked 문서에는 요약과 hash만 기록한다.

## 8. 공통 구현 불변식

- validated incumbent는 immutable하게 유지한다.
- candidate 변경은 transactional하게 처리한다.
- canonical serializer와 공식 checker를 최종 권위로 사용한다.
- geometry 근사는 reject-only prefilter로만 사용한다.
- Gurobi import/license/model/timeout 실패 시 pure-Python 경로와 Stage 5 incumbent를 보존한다.
- 모든 deadline은 `time.monotonic()`을 사용한다.
- 사용자 변경과 unrelated dirty files를 삭제하거나 덮어쓰지 않는다.
- 커밋과 push는 해당 실행 요청에서 명시적으로 승인된 경우에만 수행한다.

## 9. 단계 종료 원칙

한 단계가 끝나면 다음 단계로 자동 진행하지 않는다. 결과를 보고한 뒤 중단한다. candidate가 실패하면 코드를 억지로 유지하지 말고 rollback 가능 상태로 두며, 실패 원인과 재현 명령을 단계 문서에 기록한다.
