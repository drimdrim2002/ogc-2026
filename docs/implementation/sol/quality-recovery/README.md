# OGC-SAGE 해 품질 회복 실행 문서

상위 계획: `../12_SOLUTION_QUALITY_RECOVERY_PLAN.md`

## 고정 실행 순서

1. [Phase 0 — baseline/regression 계약](00_BASELINE_AND_REGRESSION_CONTRACT.md)
2. [Phase 1 — constructor 초기해](01_CONSTRUCTOR_INITIAL_SOLUTION_QUALITY.md)
3. [Phase 2 — native fixed-time 회복](02_NATIVE_REPAIR_FIXED_TIME_RECOVERY.md)
4. [Phase 3 — ALNS trajectory](03_ALNS_TRAJECTORY_AND_ACCEPTANCE.md)
5. [Phase 4 — retiming](04_RETIMING_QUALITY_AND_COST.md)
6. [Phase 5 — assignment/congestion](05_ASSIGNMENT_AND_CONGESTION_QUALITY.md)
7. [Phase 6 — MIP/interlock 조건부](06_MIP_INTERLOCK_CONDITIONAL.md)
8. [Phase 7 — hard-10 통합](07_INTEGRATED_HARD10_VALIDATION.md)
9. [Phase 8 — all-40 release](08_FINAL_40_RELEASE_VALIDATION.md)

이 디렉터리는 phase별 실행 단위다. 한 worker session은 정확히 한 phase만 수행한다. 실패 분석, 수정, 재검증도 각각 별도 session으로 분리한다.

## 역할 구분

- scheduler/controller: worker 생성, 진행 감시, 결과 독립 감사, 다음 phase 진입 결정
- phase worker: 해당 phase 문서 범위만 구현·검증
- diagnose worker: 실패 원인 분석만 수행하고 파일을 수정하지 않음
- fix worker: 승인된 단일 원인만 최소 수정
- retry worker: 수정 없이 동일 gate를 재검증

## 문서 결과 기록

각 phase 문서 끝의 `실행 기록`에 다음 형식으로 append한다.

```text
### YYYY-MM-DD — <run-id>

판정: PASS | NO-GO | FAIL | BLOCKED | INCONCLUSIVE
source/config/data/environment:
변경 범위:
tests:
fixed-work:
fixed-time:
quality:
artifact:
SHA256SUMS:
다음 단계 허용 여부:
```

결과가 없는 계획 섹션을 수정하지 않고 실행 기록만 append한다.
