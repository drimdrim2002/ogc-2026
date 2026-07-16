# 해 품질 회복 Scheduler/Controller 프로토콜

## 1. 역할

Scheduler는 solver를 직접 수정하지 않는다. 사용자에게 보이는 새 Codex session을 한 번에 하나만 생성하고, 각 session이 한 phase 또는 한 diagnose/fix/retry 역할만 수행하도록 관리한다.

Scheduler는 다음 상태 머신을 끝까지 관리한다.

```text
PREFLIGHT
  -> DISPATCH_PHASE
  -> MONITOR
  -> INDEPENDENT_AUDIT
     -> PASS: DISPATCH_NEXT_PHASE
     -> NO-GO/FAIL: DISPATCH_DIAGNOSE
     -> INCONCLUSIVE: 비교 증거 불완전 원인별 DIAGNOSE
     -> PENDING_ENVIRONMENT: 유효 capacity window를 얻지 못했음을 기록하고 fresh retry 대기
     -> BLOCKED: host tick 또는 measurement root/등록 subtree identity·CPU subtraction 구조 복구 대기

DIAGNOSE PASS
  -> DISPATCH_FIX
FIX PASS
  -> DISPATCH_RETRY
RETRY PASS
  -> 원래 phase 완료 처리
RETRY FAIL
  -> 새 DIAGNOSE/FIX/RETRY cycle
```

## 2. 단일 writer 규칙

- 같은 working tree에 쓰는 worker는 항상 하나만 활성 상태여야 한다.
- 기존 `019f66b6-4a1c-7fb1-b365-ce336f118ce9` pybind controller 또는 그 worker가 active이면, 먼저 상태와 마지막 변경을 읽고 write overlap이 없음을 확인한다.
- 기존 controller가 아직 파일을 쓰는 중이면 새 quality phase worker를 시작하지 않는다.
- read-only audit session도 fixed-time CPU 측정과 겹치지 않게 한다.

## 3. 새 session 생성 규칙

모든 worker는 다음 조건으로 생성한다.

- saved project: 현재 `sol-native-implementation`
- environment: local, 현재 working tree
- 새 worktree/새 branch를 만들지 않음
- model/thinking은 사용자가 별도 지정하지 않으면 scheduler 기본값 유지
- phase 이름과 run 역할을 title/prompt 첫 부분에 명시

권장 이름:

```text
quality-p0-baseline
quality-p1-constructor
quality-p2-native-repair
quality-p2-diagnose-1
quality-p2-fix-1
quality-p2-retry-1
...
```

## 4. Worker 공통 prompt

Scheduler는 각 phase 문서의 `Worker 실행 프롬프트`를 그대로 사용하되, 다음 현재 상태를 앞에 추가한다.

- branch/HEAD/dirty state
- 이전 phase 판정과 artifact/SHA
- cumulative candidate config hash
- 사용자 기존 변경과 보존 대상
- active worker가 없다는 확인
- 이번 session의 정확한 역할과 수정 가능 여부

Worker는 계획만 작성하고 끝내지 않는다. phase 문서가 허용한 구현, 테스트, benchmark, artifact, 결과 기록까지 수행한다.

## 5. 독립 audit

Worker가 PASS를 보고해도 scheduler는 그대로 다음 phase로 진행하지 않는다. 최소한 다음을 직접 확인한다.

1. worker session 최종 상태와 보고
2. `git status --short`, branch, HEAD, `git diff --check`
3. 변경 파일이 phase scope 안인지
4. 보호 파일 hash
5. artifact path와 `SHA256SUMS` 전체 검증
6. `gate.json` 수치와 tracked 문서 기록 일치
7. Stage 5/parity/trace/timeout hard blocker
8. pre-run qualification, runtime capacity monitor, atomic-pair fairness 유효성
9. baseline/candidate source/config/data/seed/budget 동등성
10. 다음 phase 선행조건 충족 여부

## 6. 실패 분기

### Diagnose session

- 코드를 수정하지 않는다.
- 실패 최초 지점과 최소 재현을 찾는다.
- 환경 오염, 측정 오류, 알고리즘 의미론, 구현 결함을 분리한다.
- 가능한 fix를 중요도순으로 나누고 한 번에 적용할 최소 fix를 지정한다.

### Fix session

- diagnose가 승인한 원인 하나만 수정한다.
- 다른 phase 개선을 섞지 않는다.
- fixed-work 및 focused regression까지만 수행한다.
- 최종 fixed-time 승격 판정은 retry session에 남긴다.

### Retry session

- solver 코드를 수정하지 않는다.
- 원 phase와 동일한 baseline/candidate 계약으로 gate를 재실행한다.
- 실패 결과를 좋은 표본으로 덮어쓰지 않는다.

## 7. 외부 blocker

- fixed-time CPU 판정은 1초 system cumulative CPU delta와 `(PID, create_time)` 전체 measurement subtree를 사용한다. raw `ps %CPU`와 process 이름은 diagnostic일 뿐 gate 입력이나 allowlist가 아니다. aggregate external busy는 host cumulative busy에서 measurement subtree CPU만 빼며 inaccessible/unattributed non-measurement workload도 그대로 포함한다.
- gating completeness는 `host_tick_valid`, `temporal_coverage`, `max_sample_gap_seconds`, `measurement_subtree_identity_complete`, `measurement_subtraction_complete`를 별도 필드로 판정한다. global PID-count `process_access_coverage`와 CPU-time attribution coverage는 diagnostic-only이며 상태 gate 입력이 아니다.
- pre-run은 최대 300초 동안 10초 window를 찾고 temporal coverage `>=95%`, gap `<=2.5초`, 평균 available `>=70%`, 모든 5초 rolling available `>=50%`일 때만 `READY`다. incomplete temporal/gap window는 계속 탐색하며 timeout 시 `PENDING_ENVIRONMENT`다. consecutive clean heartbeat 두-window 규칙은 금지한다.
- fixed-time 전체를 같은 cadence로 감시하고 평균 external busy `>30%`, 5초 rolling `>50%`, 알려진 competing compute `>=0.5` core-equivalent 5초 연속, temporal coverage `<95%`, gap `>2.5초`, measurement subtree 차감 실패를 material overlap으로 처리한다.
- `AccessDenied`, `NoSuchProcess`, `ZombieProcess`, `OSError`는 별도 count/evidence로 기록한다. unrelated non-measurement attribution 오류는 diagnostic이고 measurement root/등록 subtree 실패만 structural이다. system tick 오류와 temporal sampling 오류도 분리한다.
- CPU capacity window를 300초 안에 얻지 못하면 `PENDING_ENVIRONMENT`이며 solver `FAIL` 또는 `BLOCKED`가 아니다. `BLOCKED`는 host tick 수집/회귀/구조 실패 또는 measurement root/등록 subtree의 접근·identity·CPU subtraction 실패에만 사용한다.
- CPU 부하, 라이선스, target runtime 같은 외부 상태를 코드 변경으로 우회하지 않는다.
- 외부 process를 종료하거나 변경하지 않는다.
- pair 1 Python→Native, pair 2 Native→Python을 atomic하게 실행한다. run 평균 external busy 차이 `<=5pp`, 1초 sample p95 차이 `<=10pp`를 만족하지 못하거나 어느 한 run이 invalid면 양쪽을 폐기하고 새 immutable pair ID를 사용한다.
- source/config/data/native binary/measurement contract 변경은 전체 matrix/pair identity를 무효화한다. 좋은 한쪽만 교체하지 않는다.
- 동일 외부 상태가 반복되면 기존 evidence를 보존하고 새 retry를 무한 생성하지 않는다.
- 상태 변화가 확인되면 fresh retry session을 생성한다.

## 8. Default 및 release 보호

- phase candidate는 benchmark variant로 시작한다.
- phase gate와 integrated gate 전에는 `SubmissionConfig.from_defaults()`를 바꾸지 않는다.
- native fixed-time gate 전에는 native default를 ON으로 바꾸지 않는다.
- MIP/interlock은 Phase 6 결정 전까지 OFF다.
- 최종 release archive는 Phase 8에서만 만든다.

## 9. Scheduler 완료 조건

Phase 8 controller audit가 PASS일 때만 전체 작업 완료를 선언한다. 완료 보고에는 다음을 포함한다.

- phase별 최종 worker/retry session ID
- 최종 source/config/data hash
- phase별 gate와 artifact/SHA
- 최종 W/T/L, Borda, median/p90/worst, weighted components
- native ON/OFF 최종 결정
- submission archive hash/size/smoke
- 남은 known limitation

## 10. Scheduler 최초 실행 프롬프트

```text
당신은 OGC-SAGE 해 품질 회복 계획의 scheduler/controller다.

직접 solver를 구현하지 말고 사용자에게 보이는 새 Codex task를 한 번에 하나만 생성해 전체 Phase 0~8을 순차 관리하라. 하나의 task는 하나의 phase 또는 diagnose/fix/retry 역할만 수행한다.

먼저 다음 파일을 전체로 읽어라.
1. docs/implementation/sol/12_SOLUTION_QUALITY_RECOVERY_PLAN.md
2. docs/implementation/sol/quality-recovery/SCHEDULER_PROTOCOL.md
3. docs/implementation/sol/quality-recovery/README.md
4. docs/implementation/sol/quality-recovery/00_BASELINE_AND_REGRESSION_CONTRACT.md
5. docs/implementation/sol/performance/README.md
6. docs/implementation/sol/11_PYBIND_REPAIR_KERNEL_ACCELERATION_PLAN.md

기존 pybind controller `019f66b6-4a1c-7fb1-b365-ce336f118ce9`와 worker 상태를 먼저 확인한다. 동일 working tree에 active writer가 있으면 새 worker를 시작하지 말고 종료/안정 상태를 기다린다.

그 후 Phase 0 worker task 하나만 현재 saved project의 local working tree에 생성한다. worker가 끝나면 branch/HEAD/diff/protected hash/artifact manifest/gate를 독립 감사한다. PASS일 때만 다음 phase worker를 새 task로 만든다.

phase가 FAIL/NO-GO/INCONCLUSIVE이면 같은 task에서 수정하지 않는다. fresh diagnose task, fresh fix task, fresh retry task를 순서대로 생성하고 완전히 해결될 때까지 반복한다. 외부 blocker는 우회하거나 process를 종료하지 말고 필요한 상태 변화를 보고한다.

commit/push는 사용자 승인 없이 하지 않는다. Phase 8 PASS 전에는 전체 완료를 선언하지 않는다.
```
