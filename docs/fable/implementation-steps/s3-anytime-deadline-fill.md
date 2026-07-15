# S3 Wall-Clock Anytime Deadline-Fill Execution Plan

작성일: 2026-07-16 (Asia/Seoul)

상태: `READY — SCHEDULER-MANAGED; IMPLEMENTATION NOT STARTED`

계획 ID: `S3-AF`

실행 모델: **스케줄러 세션 1개 + phase마다 별도의 새 실행 세션 1개**

이 문서는 P0인 S3 ALNS의 조기 `max_iterations` 종료를 제거하고, 기존의
checker-verified S3 결과를 품질 하한으로 보존하면서 남은 monotonic work deadline을
실제 탐색으로 채우는 실행 계약이다. 이 문서는 설계·실행·검증·복구·세션 인계
규칙을 모두 포함한다. 구현 세션은 이 문서에서 지정한 phase 하나만 수행하고
중지한다.

## 0. 고정 컨텍스트

### 0.1 권위와 기준점

- 설계 권위: `docs/fable/solver-design-en.md`, 특히 P1/P2/P3
- checker 권위: `baseline/utils.py`
- 보존/Git 권위: `docs/fable/implementation-steps/plan-reset-01.md`
- 기존 제품 기준 branch: `codex/fable-s6-rehearsal`
- 기존 제품 기준 commit:
  `3046278c337e2b3cfef7f478a0fa420dda22038e`
- 기존 제품 worktree:
  `/Users/brown/workspace/ogc/fable-native-s3-stabilization`
- 새 구현 branch: `codex/fable-s3-anytime-fill`
- 새 구현 worktree:
  `/Users/brown/workspace/ogc/fable-native-s3-anytime`
- 고정 Python:
  `/opt/homebrew/Caskroom/miniforge/base/envs/ogc-2026/bin/python`
- 고정 seed: `20260710`

기준 commit에는 clean S3 selected default, S6 hardening, packaging, isolated rehearsal
지원이 모두 포함되어 있다. 새 branch는 반드시 위 commit에서 시작한다. scheduler나
phase 세션은 자동으로 최신 branch, legacy HEAD 또는 S4 branch를 기준점으로
대체하지 않는다.

### 0.2 읽기 전용 참조 구현

다음 구현은 아이디어와 테스트 계약을 재사용할 수 있는 읽기 전용 참조다.

- resumable S3 참조 commit:
  `fbeb430c7f617c6dcbfa67bd96d17c1d8d6710ff`
- unified S3/S4 참조 commit:
  `4b68031ad219688be18fc05579a21a75ad7fab96`
- AQ-05 최종 기록 commit:
  `bb1ab6bf550aa42c606e5fa2865fcd350a690cff`
- 참조 worktree:
  `/Users/brown/workspace/ogc/fable-native-anytime`
- AQ-05 보고서:
  `docs/fable/implementation-steps/aq-05-anytime-qualification-report.md`
  (참조 worktree 기준)

참조 구현은 120초에서 5/5 Stage 5, 약 102~107초 useful search, 수천 회의 S3
iteration을 입증했지만, B0 대비 1건의 손실과 ALNS balance gate 실패로
`GATE_FAILED_DISABLED`가 되었다. 따라서 전체 commit을 cherry-pick하거나
`anytime_extension=true`로 바꾸지 않는다. 각 phase가 소유하는 최소 hunk만
독립적으로 재구성하고 다시 검증한다.

### 0.3 절대 수정·실행 금지 영역

- legacy 보존 worktree:
  `/Users/brown/workspace/ogc/fable-native-implementation`
- legacy branch: `fable-native-implementation`
- `baseline/utils.py`
- `baseline/baseline_greedy.py`
- 기존 frozen S0~S6 및 AQ evidence
- `data/train/prob_21.json`~`prob_25.json`
- S4 assignment refinement, cross-bay, worker protocol, S5 portfolio, interlock 기능

이 계획 문서가 최초로 작성된 legacy worktree에서는 문서 읽기만 허용한다. 구현,
solver, unittest, harness, benchmark, branch switch, staging, commit, push는 하지 않는다.
AF-00이 별도 worktree를 만든 뒤 모든 실행은 새 worktree에서만 수행한다.

## 1. 문제 정의와 현재 상한

현재 public entry는 S3를 다음과 같이 호출한다.

```text
첫 epoch: 300 iterations
후속 epoch: epoch당 1 iteration
epoch: 60초
```

`run_anytime_epochs()`는 각 epoch에서 `run_alns()`를 한 번만 호출한다. quota가
deadline보다 먼저 끝나도 같은 epoch에서 다음 batch를 시작하지 않는다. 현재 최대
iteration 수는 대략 다음과 같다.

```text
300 + max(0, ceil(ALNS allowance / 60) - 1)
```

앞 단계 시간을 무시한 상한은 다음과 같다.

| timelimit | deadline reserve | optional work | 현재 최대 iteration |
|---:|---:|---:|---:|
| 5초 | 3초 | 2초 | 300 |
| 60초 | 3초 | 57초 | 300 |
| 120초 | 6초 | 114초 | 301 |
| 300초 | 15초 | 285초 | 304 |

tracked example 5초 실행에서는 약 0.383초에 `max_iterations`로 끝났다. 5초의
protected reserve를 고려해도 optional work 약 2초 중 약 81%가 사용되지 않았다.
120초 이상에서는 품질 손실 가능성이 훨씬 크다.

## 2. 결정된 해결 전략

### 2.1 핵심 구조

이 계획은 기존 qualified S3 prefix를 먼저 실행하여 verified anchor를 만든 뒤,
남은 **동일 parent work deadline**에서 짧은 S3 segment와 iteration batch를 반복한다.

```text
T0
  -> constructor
  -> initial exact retime
  -> 기존 qualified S3 anchor batch
  -> immutable verified anchor checkpoint
  -> while parent work deadline remains:
         bounded S3 segment
         while segment deadline remains:
             fixed iteration batch
             latest verified checkpoint 갱신
  -> latest verified checkpoint 반환
```

public 반환 형식은 바꾸지 않는다. feature-off 경로는 기준 commit과 동일해야 한다.

### 2.2 예산 의미론

1. entry에서 만든 원래 `Budget`이 유일한 global deadline 권위다.
2. extension에는 `Budget(budget.remaining)` 같은 새 root budget을 만들지 않는다.
3. segment/batch budget은 parent의 absolute soft deadline보다 늦을 수 없다.
4. normal termination은 parent work deadline이며 hard deadline은 return reserve로
   보호한다.
5. quota 소진은 controller 종료 사유가 아니다. 남은 시간이 있으면 즉시 다음
   batch를 시작한다.
6. fake sleep, idle polling, 빈 loop는 useful search로 인정하지 않는다.

### 2.3 verified anchor와 rollback 의미론

구현은 다음 세 값을 구분한다.

```text
original_anchor_checkpoint
last_verified_checkpoint
current_search_state
```

- `original_anchor_checkpoint`는 기존 qualified S3 prefix가 만든 품질 하한이다.
- `last_verified_checkpoint`는 official checker를 통과한 최신 strict incumbent다.
- `current_search_state`는 SA/RRT가 허용한 incumbent보다 나쁠 수 있는 탐색 상태다.

deadline 또는 fault가 발생하면 현재 `MoveTransaction`만 undo한다. 이전 segment나
batch에서 얻은 `last_verified_checkpoint`를 `original_anchor_checkpoint`까지
되돌리지 않는다. 탐색 상태와 verified incumbent가 달라지면
`snapshot_state()`로 verified state를 재구성한다.

최종 결과는 다음을 항상 만족해야 한다.

```text
official_check(final).stage == 5
objective(final) <= objective(original_anchor)
verified incumbent trace is nonincreasing
```

### 2.4 고정 초기 탐색 정책

처음 구현하는 정책은 하나뿐이다.

- segment: 기본 8초
- batch: 기본 24 iterations
- segment seed: `base_seed + 104729 * segment_index`
- acceptor: 기존 selected default `sa`
- adaptive weights: 기존 selected default `false`
- safety sample interval: 기존 계약 유지
- destroy/restart profile:

| segment index mod 3 | destroy scale | restart policy |
|---:|---:|---|
| 0 | 1.0 | 현재 verified anchor에서 계속 |
| 1 | 1.5 | verified incumbent에서 fresh RNG restart |
| 2 | 2.0, 기존 cap 준수 | same-bay restart 후 계속 |

파라미터 sweep, instance별 분기, wall-clock seed, hard-coded instance ID는 금지한다.

### 2.5 prefix 계약

prefix는 두 수준으로 정의한다.

1. **논리적 prefix**: 같은 seed/profile과 deterministic fake clock에서 짧은 budget의
   완료된 event sequence가 긴 budget event sequence의 prefix다.
2. **실제 실행 monotonicity**: 별도 real run의 backend latency까지 byte-identical일
   필요는 없지만, 긴 budget은 더 많은 segment/batch/iteration을 실행하고 paired
   objective가 짧은 budget보다 나쁘지 않아야 한다.

첫 qualified S3 anchor batch와 extension seed schedule은 total timelimit에 의해
바뀌지 않아야 한다. timelimit은 오직 더 많은 work unit을 허용해야 한다.

### 2.6 feature flag

새 flag 이름은 `s3_anytime_fill`이다.

- AF-03~AF-06 기본값: `false`
- harness candidate profile에서만 명시적으로 `true`
- AF-06 frozen qualification이 통과한 경우에만 AF-07에서 config-only로 `true`
- 실패한 unified 후보의 `anytime_extension` 이름과 의미를 재사용하지 않음

## 3. 범위와 비범위

### 3.1 포함 범위

- immutable, instance-bound verified checkpoint
- verified checkpoint에서 상태 재구성
- repeated S3 segment/batch engine
- public entry의 anchor-plus-tail 연결
- deadline/fault 시 latest verified 반환
- feature flag와 명시적 candidate profile
- useful-search/iteration/anchor telemetry
- fake-clock, fault, checker, prefix, packaging, real wall-clock 검증
- qualification 후 selected default와 새 package/rehearsal

### 3.2 제외 범위

- S4/S5/interlock 구현 또는 enablement
- S3/S4 unified scheduler
- acceptor/operator/retime 파라미터 변경
- full checker 호출을 줄이는 최적화
- `safety_sample_interval` 의미 변경
- 새로운 exact backend
- 전역 최적성 인증
- 다중 process portfolio
- 기존 AQ-05의 balance gate 재조정
- sleep으로 timelimit 채우기

## 4. Git, 증거, 세션 경계

### 4.1 phase commit 규칙

- 한 phase는 정확히 하나의 concern-specific 성공 commit을 만든다.
- 실패한 phase는 성공 commit을 만들지 않는다.
- 각 phase는 시작 시 clean status와 이전 phase commit을 확인한다.
- 각 phase는 exact owned-path allowlist를 만들고 그 경로만 stage한다.
- protected file hash를 phase 전후 비교한다.
- 구현·테스트 변경 후 관련 D/S 검증은 새 attempt ID로 반복할 수 있다.
- frozen Q는 immutable candidate identity당 정확히 한 번이다.
- push는 사용자가 scheduler 세션에 명시적으로 승인한 경우에만 수행한다.
- PR은 이 계획 범위가 아니다.
- 이 문서의 전용 phase/recovery prompt가 일반 구현 슬라이스 prompt보다 우선한다.
  일반 prompt의 자동 push 규칙을 이 계획에 적용하지 않는다.

phase가 실패했을 때 그 세션이 만든 partial diff를 임의로 삭제하지 않는다. 실패
세션은 exact dirty path set, `git diff --binary`, untracked-file hash 목록을 attempt
evidence에 보존하고 worktree를 그대로 인계한다. recovery 세션은 시작 시 이를
감사한다. dirty path가 실패 phase allowlist의 부분집합일 때만 이어서 수정할 수 있다.
관련 없는 path가 하나라도 있으면 자동 restore/reset하지 않고
`BLOCKED(PRECONDITION)`로 scheduler에 반환한다.

### 4.2 evidence tier

| Tier | 목적 | rerun 규칙 | 실패 시 |
|---|---|---|---|
| D | RED/GREEN, synthetic, targeted regression | 관련 변경 뒤 새 attempt로 반복 | 같은 phase에서 수정 가능 |
| S | full regression, public entry, checker, package preflight | 관련 변경 뒤 새 identity로 반복 | candidate freeze 금지 |
| Q | frozen real wall-clock qualification | frozen identity당 정확히 1회 | 같은 identity 재실행 금지 |

증거 root:

```text
benchmarks/evidence/s3-anytime-fill/
  orchestration/
  af-00/
  af-01/
  ...
  af-09/
  qualification/
  package-rehearsal/
```

각 attempt는 `RUN_STARTED`, `commands.txt`, `results.json`, `handoff.json`을 가진다.
성공한 경우에만 `COMPLETE`를 추가한다. 실패 evidence는 삭제하거나 덮어쓰지 않는다.

### 4.3 phase handoff schema

각 phase 세션은 마지막에 다음 정보를 `handoff.json`과 최종 응답에 남긴다.

```json
{
  "plan_id": "S3-AF",
  "phase_id": "AF-XX",
  "attempt_id": "UTC timestamp + nonce",
  "outcome": "COMPLETE|BLOCKED",
  "start_commit": "40-char sha",
  "end_commit": "40-char sha or null",
  "worktree": "/absolute/path",
  "branch": "branch name",
  "red": [{"command": "...", "exit_code": 1, "evidence": "..."}],
  "green": [{"command": "...", "exit_code": 0, "evidence": "..."}],
  "checker": {"stage": 5, "feasible": true},
  "changed_files": ["..."],
  "protected_hashes_match": true,
  "git_status": "...",
  "blocker": null,
  "root_cause": null,
  "recovery_scope": null,
  "next_phase": "AF-YY or null"
}
```

`COMPLETE`는 `end_commit`이 존재하고 phase worktree가 clean일 때만 유효하다.
`BLOCKED` handoff는 `end_commit=null`일 수 있으며, 이때 `git_status`, binary diff
evidence, untracked manifest, 마지막 clean commit을 반드시 기록한다.

## 5. Scheduler 운영 계약

### 5.1 scheduler의 역할

scheduler 세션은 구현하지 않는다. 다음만 수행한다.

1. 이 문서를 처음부터 끝까지 읽는다.
2. legacy worktree가 보존 전용인지 확인하고 실행 대상으로 선택하지 않는다.
3. target branch/worktree, HEAD, clean status, 마지막 `handoff.json`을 감사한다.
4. 정확히 하나의 다음 phase 또는 동일 phase recovery를 선택한다.
5. 새 실행 세션에 전달할 완전한 prompt를 생성하거나 제품이 지원하면 그 prompt로
   새 세션을 시작한다.
6. phase 결과를 감사하기 전에는 다음 phase를 시작하지 않는다.
7. `COMPLETE` handoff와 clean commit이 확인되면 다음 새 세션을 시작한다.
8. `BLOCKED`이면 root cause를 분류하고 동일 phase recovery용 새 세션을 시작한다.
9. Q 실패는 같은 candidate를 재실행하지 않고 D/S로 돌아가 새 source identity를
   만든다.

scheduler가 코드를 고치거나 failing command를 직접 재실행하면 안 된다.

### 5.2 scheduler 상태 머신

```text
READY(AF-00)
  -> RUNNING(AF-00)
  -> COMPLETE(AF-00)
  -> READY(AF-01)
  -> ...

RUNNING(AF-X)
  -> BLOCKED(AF-X, attempt N)
  -> ROOT_CAUSE_REVIEW
  -> RECOVERY_READY(AF-X, attempt N+1)
  -> RUNNING(AF-X)

RUNNING(AF-06 Q)
  -> Q_FAILED(identity A)
  -> RECOVERY_READY(AF-04 or AF-05, identity B)
  -> 새 candidate freeze
  -> 새 Q 1회
```

### 5.3 blocker 분류

| 분류 | 예 | recovery 시작점 |
|---|---|---|
| `PRECONDITION` | base SHA, branch, data hash 불일치 | 같은 phase 사전 점검 |
| `RED_INVALID` | import/fixture 오류로 실패 | 같은 phase test setup |
| `IMPLEMENTATION` | intended test 실패 | 같은 phase production/test |
| `SAFETY` | checker, incumbent, deadline 위반 | 마지막 clean phase, 안전 원인 우선 |
| `REGRESSION` | feature-off 또는 full suite 실패 | 실패를 최초 도입한 phase |
| `TIME_UTILIZATION` | 조기 반환, idle, iteration 미증가 | AF-02/AF-03/AF-04 |
| `QUALITY` | anchor/60초 대비 회귀 | AF-02 또는 AF-03; gate 완화 금지 |
| `PACKAGING` | allowlist, extraction, public loader 실패 | AF-04 또는 AF-08 |
| `ENVIRONMENT` | interpreter/backend/data 부재 | 같은 phase, 환경 증거 후 사용자 보고 |

recovery는 실패 증상을 숨기지 않고 재현 test를 먼저 고정한다. threshold 완화,
test skip, xfail, data 교체, seed 변경은 recovery가 아니다.

### 5.4 성공 phase 감사

scheduler는 다음을 모두 확인해야 다음 phase를 허용한다.

- phase ID가 예상 phase와 일치
- start commit이 이전 완료 commit과 일치
- phase가 허용한 파일만 변경
- 필수 RED/GREEN/S/Q 명령과 evidence 존재
- official checker 요구 충족
- protected hashes 일치
- 성공 commit 존재
- `git status --short --branch` clean
- 다음 phase를 미리 구현하지 않음

## 6. Phase 요약

| Phase | 새 세션의 단일 목적 | 성공 commit |
|---|---|---|
| AF-00 | 전용 worktree/branch와 frozen execution contract 생성 | `docs(anytime): freeze s3 deadline-fill execution contract` |
| AF-01 | immutable verified checkpoint 기반 추가 | `feat(anytime): add immutable verified s3 checkpoints` |
| AF-02 | resumable repeated S3 engine 추가 | `feat(anytime): add verified s3 deadline-fill engine` |
| AF-03 | 기존 S3 anchor 뒤 tail을 feature-off로 연결 | `feat(anytime): fill remaining s3 deadline from verified anchor` |
| AF-04 | telemetry, harness candidate profile, gate evaluator 추가 | `test(anytime): add s3 deadline-fill qualification contract` |
| AF-05 | Tier-S 통합 검증 후 clean candidate 동결 | `chore(anytime): freeze s3 deadline-fill candidate` |
| AF-06 | frozen Tier-Q real qualification 1회 및 판정 | `docs(anytime): record s3 deadline-fill qualification` |
| AF-07 | Q PASS feature set을 config-only default로 연결 | `feat(anytime): enable qualified s3 deadline fill` |
| AF-08 | selected default package/stress/rehearsal 재검증 | `test(anytime): qualify deadline-fill submission package` |
| AF-09 | 최종 progress/evidence/merge handoff closeout | `docs(anytime): close out s3 deadline-fill delivery` |

AF-06이 PASS하지 않으면 AF-07~AF-09는 실행하지 않는다.

## 7. Phase 상세

### AF-00 — Clean bootstrap와 contract freeze

목적: 정확한 제품 기준점에서 전용 branch/worktree를 만들고, 이 문서와 base identity를
새 branch에 동결한다. 구현과 solver 실행은 하지 않는다.

사전 조건:

- 기준 commit `3046278c337e2b3cfef7f478a0fa420dda22038e` 존재
- 기준 worktree clean
- 새 worktree 경로/branch 미사용, 또는 이미 존재하면 정확히 같은 base/branch/clean
- legacy worktree에는 어떤 mutation도 하지 않음

작업:

1. 기준 commit에서 `codex/fable-s3-anytime-fill` branch와
   `/Users/brown/workspace/ogc/fable-native-s3-anytime` worktree를 만든다.
2. 이 문서를 새 worktree의 동일 상대 경로로 가져온다.
3. `benchmarks/manifests/s3-anytime-fill-base.json`을 만들고 다음을 기록한다.
   - base commit/branch/worktree
   - Python 및 package versions
   - `baseline/utils.py`, `baseline/baseline_greedy.py` SHA-256
   - 참조 commit 3개
   - 기존 S3/S6 qualification evidence 경로
   - hard-5 input SHA
4. path allowlist, pre-stage/staged diff hash, final clean status를 기록한다.

허용 파일:

- `docs/fable/implementation-steps/s3-anytime-deadline-fill.md`
- `benchmarks/manifests/s3-anytime-fill-base.json`

금지:

- solver/test/harness 실행
- production/test code 수정
- 참조 commit cherry-pick

완료 조건:

- 새 worktree/branch가 exact base에서 생성됨
- plan/manifest만 commit됨
- protected hashes 기록됨
- clean status

Commit: `docs(anytime): freeze s3 deadline-fill execution contract`

### AF-01 — Immutable verified checkpoint foundation

목적: S3 anchor와 extension 간에 checker-verified solution을 안전하게 전달하고 상태를
재구성하는 최소 immutable checkpoint API를 추가한다.

RED:

- checkpoint가 다른 instance에서 거부됨
- solution JSON 또는 placement snapshot이 변조되면 거부됨
- checkpoint round-trip 뒤 solution SHA, checker result, objective가 동일함
- checkpoint에서 재구성한 `SolutionState`가 invariant와 official checker Stage 5를
  만족함
- caller가 반환 dict/placement를 변경해도 저장된 checkpoint가 변하지 않음
- verification count가 명시된 정책대로 증가함

구현:

- immutable `VerifiedCheckpoint`
- placement snapshot 보관
- `VerifiedIncumbent.export_checkpoint()`
- `VerifiedIncumbent.from_checkpoint()`
- `VerifiedIncumbent.snapshot_state()`
- instance-bound SHA와 canonical solution SHA 검증
- 기존 `solution`, `try_update`, checker authority 의미 보존

허용 파일:

- `baseline/solver/incumbent.py`
- `baseline/tests/test_incumbent.py` 또는 checkpoint 전용 새 test 파일

검증:

- checkpoint RED/GREEN targeted suite
- incumbent 기존 tests
- official checker synthetic round-trip
- feature/public entry 동작 변경 없음

완료 조건:

- 모든 RED가 GREEN
- feature flags와 entry 변경 없음
- protected hashes 일치
- clean atomic commit

Commit: `feat(anytime): add immutable verified s3 checkpoints`

### AF-02 — Resumable repeated S3 engine

목적: verified checkpoint에서 시작하여 parent absolute deadline까지 segment와 batch를
반복하는 내부 S3 API를 추가한다. public entry에는 아직 연결하지 않는다.

내부 API 목표:

```text
run_s3_extension(checkpoint, budget, profile, seed, telemetry, *, prob_info)
    -> S3ExtensionResult(checkpoint, metrics, trace, stopped_reason)
```

RED:

- fake clock 24초, 8초 segment에서 정확히 3개 segment 시작
- 한 batch의 24 iterations가 빨리 끝나면 같은 segment에서 두 번째 batch 시작
- quota 소진이 extension 종료 사유가 아님
- nested segment deadline이 parent deadline을 연장하지 않음
- repair/accept/retime/full-check deadline/fault가 partial state를 노출하지 않음
- 이전 segment에서 개선 후 다음 segment fault가 나도 latest verified improvement 유지
- original anchor보다 나쁜 checkpoint를 반환하지 않음
- 동일 seed/config는 동일 logical event prefix와 final SHA
- assignment, bay membership, Z2는 extension 전후 동일
- 같은 solution fingerprint/RNG/profile 무한 반복 없음

구현:

- `S3ExtensionProfile`, metrics/result/event value objects
- parent-bounded absolute segment budget
- repeated batch loop
- 고정 seed/profile rotation
- `last_verified_checkpoint`를 batch/segment 경계에서 갱신
- fault/deadline 시 latest verified 반환
- 기존 `run_alns()` checker/transaction 의미 재사용
- 필요하면 `run_alns()`에 initial destroy scale 같은 최소 인자만 추가

참조 구현 사용 규칙:

- `fbeb430...`의 소유 hunk를 읽고 재구성 가능
- whole commit cherry-pick 금지
- 참조 구현의 fault 시 original anchor로 되돌아가는 경로는 복사하지 않음

허용 파일:

- `baseline/solver/alns.py`
- `baseline/tests/test_alns.py`
- `baseline/tests/test_budget_entry.py`

검증 제한:

- fake clock/synthetic/targeted tests만
- 실제 시간을 기다리는 smoke 금지
- full discovery 금지
- hard-5 금지

완료 조건:

- RED/GREEN 모두 증거화
- deadline 전 iteration/batch가 계속 증가
- latest verified fault preservation 증명
- entry/config 변경 없음
- clean atomic commit

Commit: `feat(anytime): add verified s3 deadline-fill engine`

### AF-03 — Anchor-plus-tail entry integration

목적: 기존 qualified S3 결과를 anchor로 확정한 뒤 남은 parent work deadline을
AF-02 engine으로 채운다. 새 기능은 명시적 flag로만 선택하고 기본값은 false다.

RED:

- legacy S3가 `max_iterations`로 끝나고 parent budget이 남으면 extension이 호출됨
- extension은 새 root `Budget`이 아니라 같은 parent absolute deadline을 사용함
- extension이 개선하면 final이 개선 checkpoint임
- extension이 no-gain/fault/deadline이면 anchor 이상을 반환함
- feature false에서 기존 event/result/telemetry SHA가 기준과 동일함
- 60/300 fake-clock에서 300이 더 많은 segment/batch/iteration을 실행함
- 짧은 logical event sequence가 긴 sequence의 prefix임
- public return schema가 변하지 않음
- entry fault armor가 verified incumbent를 반환함

구현:

- `SolverConfig.s3_anytime_fill: bool = False`
- test-only `_s3_anytime_fill` override
- existing S3 prefix 직후 `export_checkpoint()`
- 남은 parent budget이 있을 때만 `run_s3_extension()`
- final verified checkpoint 선택과 solution copy 반환
- S4/S5/interlock flag나 코드 추가 없음

feature-on의 initial anchor schedule은 total timelimit에 따라 다른 policy를 선택하지
않는다. 60/300 prefix를 깨는 기존 후속 1-iteration epoch를 anchor로 포함할지 여부는
RED 전에 하나의 명시적 정책으로 고정해야 한다. 기본 결정은 첫 qualified 300회
batch만 anchor prefix로 사용하고 이후 work는 AF-02의 deterministic batch schedule로
처리하는 것이다. feature-off 경로는 기존 호출을 그대로 보존한다.

허용 파일:

- `baseline/solver/config.py`
- `baseline/solver/entry.py`
- `baseline/tests/test_budget_entry.py`
- 필요한 경우 `baseline/tests/test_alns.py`

검증:

- entry integration targeted RED/GREEN
- feature-off parity
- fake-clock 60/300 prefix 및 scaling
- fault injection matrix
- 실제 smoke/full discovery/hard-5 금지

완료 조건:

- feature false default
- anchor non-regression 및 same-parent deadline 증명
- production entry test가 기존 `continuation_iterations_per_epoch=1` 문제를 재현하고
  새 feature-on 경로에서 해결함
- clean atomic commit

Commit: `feat(anytime): fill remaining s3 deadline from verified anchor`

### AF-04 — Telemetry, candidate profile, qualification evaluator

목적: 조기 반환을 숨길 수 없도록 구조화된 계측과 candidate-only 실행 경로, frozen
gate evaluator를 추가한다. 기본값은 false를 유지한다.

필수 telemetry:

```text
run_id, commit, dirty_diff_hash, instance_id, instance_sha, seed, timelimit
wall_seconds, work_deadline_seconds, hard_deadline_seconds
anchor_seconds, anchor_objective, anchor_solution_sha256
s3_fill_seconds, s3_fill_useful_seconds, idle_seconds, finalization_seconds
s3_fill_segments, s3_fill_batches, s3_fill_iterations
s3_fill_accepted, s3_fill_improvements, s3_fill_restarts
s3_fill_deadlines, s3_fill_faults, s3_fill_stopped_reason
time_to_best_seconds, late_improvement_count
checker_stage, feasible, objective, obj1, obj2, obj3
final_solution_sha256, verification_count
termination_reason, fallback_reason
```

RED:

- `max_iterations`인데 work deadline 전 반환하면 time gate 실패
- sleep만 한 record는 useful time gate 실패
- iteration/segment가 0이면 scaling gate 실패
- final objective가 anchor보다 나쁘면 safety/quality gate 실패
- trace가 증가하면 safety gate 실패
- hard deadline overrun 또는 Stage 5 미달이면 safety gate 실패
- 300초 record의 work units가 60초보다 늘지 않으면 scaling gate 실패
- incomplete records/evidence는 PASS로 판정되지 않음

구현:

- named profile `s3-anytime-fill-candidate`
- candidate profile만 `s3_anytime_fill=true`
- public default false
- harness record/schema 확장
- synthetic gate evaluator
- useful time은 candidate/repair/retime/checker 실제 wall만 포함
- no-op/sleep/serialization은 useful time에서 제외

허용 파일:

- `baseline/harness/cli.py`
- `baseline/harness/runner.py`
- `baseline/harness/gates.py`
- 필요하면 새 `baseline/harness/s3_anytime_qualification.py`
- `baseline/tests/test_harness_schema.py`
- `baseline/tests/test_budget_entry.py`
- 새 `baseline/tests/test_s3_anytime_qualification.py`
- candidate manifest 초안

검증 제한:

- synthetic records와 targeted harness tests만
- 실제 wall-clock benchmark 금지
- full discovery 금지

완료 조건:

- PASS/FAIL/incomplete 분기 GREEN
- feature-off/public default false
- telemetry 중복 시간 합산 없음
- clean atomic commit

Commit: `test(anytime): add s3 deadline-fill qualification contract`

### AF-05 — Tier-S integration, package preflight, candidate freeze

목적: 실제 Q 전에 repeatable safety/integration 검증을 통과하고 clean candidate
identity와 qualification manifest를 동결한다.

사전 조건:

- AF-04 clean commit
- feature default false
- candidate profile만 true
- hard-5 input hashes 일치

Tier-S 실행:

1. affected S3/checkpoint/entry/harness tests
2. full unittest discovery
3. Algorithm Tester-style public loader parity
4. package allowlist 및 isolated extraction smoke
5. feature-off tracked example parity
6. feature-on 5초 smoke
7. feature-on 12초 smoke
8. checker Stage 5와 hard deadline 확인
9. 남은 child/process가 0인지 확인

5초 smoke의 optional work deadline은 약 2초라는 reserve 계약을 사용한다. nominal
5초 전체를 탐색으로 채우라는 잘못된 gate를 만들지 않는다. 12초 smoke는 최소 한
extension segment와 여러 batch를 요구한다.

freeze 산출물:

- `benchmarks/manifests/s3-anytime-fill-candidate.json`
- candidate commit/dirty hash
- config/profile/seed/timelimit
- hard-5 hashes와 실행 순서
- exact Q command
- expected evidence directory
- 모든 threshold

AF-05에서는 알고리즘/파라미터를 결과에 맞춰 조정하지 않는다. Tier-S 실패 시 freeze
commit을 만들지 않고 recovery로 보낸다.

허용 파일:

- candidate manifest
- synthetic/packaging test에 필요한 최소 수정
- freeze report 문서

완료 조건:

- Tier-S 전부 PASS
- source clean
- candidate identity immutable
- Q command가 deterministic하게 직렬화됨
- clean atomic commit

Commit: `chore(anytime): freeze s3 deadline-fill candidate`

### AF-06 — Frozen Tier-Q wall-clock qualification

목적: AF-05의 clean immutable candidate를 수정하지 않고 실제 wall-clock anytime,
safety, quality, scaling을 한 번 검증한다.

Q 실행 전:

- commit, status, manifest SHA, input SHA, Python/package versions 확인
- 불일치 시 실행하지 않고 `BLOCKED(PRECONDITION)`
- Q가 시작된 뒤 source/config/test/manifest 변경 금지

Q workload:

1. hard-5 `prob_21 -> prob_25`, 각 120초, seed `20260710`, jobs 1
2. scaling pair 한 개: preregistered `prob_21` 60초 후 300초, seed 동일
3. 모든 run 순차 실행
4. failed command 자동 재실행 금지

안전 gate:

- 모든 run official checker Stage 5
- crash, external timeout, hard-deadline overrun 0
- unverified/forged/stale checkpoint 0
- final objective가 같은 run의 anchor보다 나쁜 사례 0
- incumbent trace nonincreasing
- final SHA와 checker solution SHA 일치

시간 활용 gate:

- 전역 최적성 인증이 없으면 termination reason `work_deadline`
- hard-5 각 run의 useful work가 최소
  `0.90 * (timelimit - deadline_reserve(timelimit))`
- 120초 기준 각 run 최소 102.6초
- fake sleep/idle은 useful에 포함하지 않음
- final return은 hard deadline/reserve 계약 이내

탐색량/scaling gate:

- 각 120초 run에서 extension segment > 0, batch > 1, iteration > 0
- 300초의 segment/batch/iteration이 60초보다 모두 큼
- 60초 logical trace가 300초 trace의 검증 가능한 prefix
- 300초 objective <= 60초 objective

품질 gate:

- final <= same-run anchor: 5/5
- hard-5 전체에서 strict late improvement 최소 1건
- paired median relative delta <= 0
- 어떤 손실도 평균 개선으로 상쇄하지 않음

판정:

- safety 실패: `BLOCKED_SAFETY`
- time/scaling 실패: `GATE_FAILED_TIME`
- quality 실패: `GATE_FAILED_QUALITY`
- 모두 PASS: `CANDIDATE_PASS`

실패하면 같은 frozen identity로 Q를 다시 실행하지 않는다. scheduler는 실패 원인을
AF-02~AF-05의 소유 범위에 매핑하고 새 recovery 세션과 새 candidate identity를
요구한다.

AF-06은 production code를 수정하지 않는다. evidence와 qualification report만
commit한다.

Commit: `docs(anytime): record s3 deadline-fill qualification`

### AF-07 — Config-only selected-default wiring

목적: AF-06 `CANDIDATE_PASS`의 정확한 feature set만 public default로 연결한다.

사전 조건:

- AF-06 decision `CANDIDATE_PASS`
- frozen candidate source/config hash 일치
- branch clean

구현:

- `SolverConfig.s3_anytime_fill = True`
- named candidate와 public default의 flag equivalence test
- public `myalgorithm.algorithm(prob_info, timelimit)` wiring test
- 알고리즘 코드/파라미터 변경 없음

금지:

- AF-06 benchmark 재실행
- parameter 수정
- unrelated config 변경

완료 조건:

- config-only production diff
- targeted wiring/parity GREEN
- candidate profile과 default feature set 동일
- clean atomic commit

Commit: `feat(anytime): enable qualified s3 deadline fill`

### AF-08 — Mandatory package, stress, rehearsal requalification

목적: default가 바뀐 clean commit을 실제 제출 package와 isolated environment에서 다시
검증한다. 이전 S6 evidence는 새로운 default identity를 대신할 수 없다.

사전 조건:

- AF-07 clean commit
- public default true
- candidate/default equivalence GREEN

실행:

1. full unittest discovery
2. syntax/import audit
3. protected-file hashes
4. package allowlist와 archive build
5. isolated extraction에서 public loader 실행
6. tracked example 및 preregistered stress fixtures
7. max-training-size fixture
8. short/tight deadline sentinel
9. process cleanup, archive content/hash audit
10. final submission rehearsal/report

AF-08은 production 알고리즘을 수정하지 않는다. packaging/integration 문제가
발견되면 `BLOCKED`로 끝내고 scheduler가 AF-04 또는 AF-08 recovery를 만든다.
qualification threshold나 default를 조용히 완화하지 않는다.

완료 조건:

- full discovery PASS
- 모든 package/stress output Stage 5
- timeout/crash/leak 0
- public package가 `s3_anytime_fill=true`를 실제 선택
- archive allowlist 위반 0
- clean evidence/report commit

Commit: `test(anytime): qualify deadline-fill submission package`

### AF-09 — Closeout와 delivery handoff

목적: 최종 selected identity, evidence, package, branch 상태를 진행 문서에 반영하고
merge/review 가능한 handoff를 만든다.

작업:

- master progress에 새 S3-AF correction/history append
- P0-1 상태를 `COMPLETE`로 기록
- AF-00~AF-08 commit/evidence index
- 5/120 및 60/300 결과 요약
- final config flags
- package archive SHA 및 rehearsal 결과
- branch/upstream/dirty status
- legacy worktree가 수정되지 않았음을 확인
- AQ-05 unified candidate는 계속 disabled임을 명시

AF-09은 solver/test/harness 코드를 수정하지 않는다.

완료 조건:

- 모든 이전 phase complete
- 최종 branch clean
- closeout 문서와 evidence index 일치
- push가 승인된 경우에만 verified branch push

Commit: `docs(anytime): close out s3 deadline-fill delivery`

## 8. Phase 세션 공통 프롬프트

scheduler는 각 새 실행 세션에 아래 template을 사용한다.

```text
다음 계획 문서를 처음부터 끝까지 읽고 지정된 phase 하나만 실행해주세요.

계획 문서:
/Users/brown/workspace/ogc/fable-native-s3-anytime/docs/fable/implementation-steps/s3-anytime-deadline-fill.md

phase_id: {{AF-XX}}
attempt_id: {{scheduler가 생성한 고유 ID}}
expected_start_commit: {{이전 완료 phase commit}}
target_worktree: /Users/brown/workspace/ogc/fable-native-s3-anytime
target_branch: codex/fable-s3-anytime-fill

이 세션에서는 지정 phase만 수행하고 다음 phase를 시작하지 마세요. 먼저 worktree,
branch, HEAD, clean status와 선행 phase COMPLETE handoff를 검증하세요. RED를 먼저
입증하고 최소 구현으로 GREEN을 만든 뒤 phase에 허용된 검증만 실행하세요.

성공하면 phase 소유 파일만 원자적으로 commit하고 clean status를 확인하세요. push는
하지 마세요. 실패하면 기준을 완화하지 말고 root cause와 recovery scope를 기록하고,
부분 구현을 성공 commit으로 만들지 마세요. handoff.json과 최종 응답에 계획의
필수 schema를 모두 포함하고 중지하세요.
```

AF-00만 plan이 아직 target worktree에 없으므로 다음 source path를 사용한다.

```text
/Users/brown/workspace/ogc/fable-native-implementation/docs/fable/implementation-steps/s3-anytime-deadline-fill.md
```

## 9. Recovery 세션 공통 프롬프트

```text
다음 계획 문서를 처음부터 끝까지 읽고 {{AF-XX}} recovery만 수행해주세요.

계획 문서:
/Users/brown/workspace/ogc/fable-native-s3-anytime/docs/fable/implementation-steps/s3-anytime-deadline-fill.md

failed_phase: {{AF-XX}}
failed_attempt_id: {{attempt id}}
failed_handoff: {{absolute handoff.json path}}
last_clean_commit: {{이전 완료 phase commit}}
recovery_attempt_id: {{새 고유 ID}}
target_worktree: /Users/brown/workspace/ogc/fable-native-s3-anytime
target_branch: codex/fable-s3-anytime-fill

먼저 실패 evidence와 diff를 읽어 root cause를 재현하는 RED를 고정하세요. 동일 phase의
원인만 수정하고 다음 phase 기능을 구현하지 마세요. gate/test/threshold를 완화하지
마세요. Q 실패였다면 같은 frozen identity나 같은 Q command를 재실행하지 말고,
필요한 D/S phase로 돌아가 새 source/candidate identity를 만드세요.

시작 worktree가 dirty이면 failed handoff의 path/hash와 정확히 일치하는지 먼저
감사하세요. 모든 dirty path가 failed phase allowlist 안에 있을 때만 이어서
작업하세요. 불일치하거나 관련 없는 변경이 있으면 reset/restore/stash하지 말고
PRECONDITION blocker로 scheduler에 반환하세요.

성공하면 원래 phase의 모든 완료 조건을 다시 충족하고 새 attempt evidence,
atomic commit, clean handoff를 남기세요. push하지 말고 중지하세요.
```

## 10. Scheduler 세션 시작 프롬프트

사용자가 새 scheduler 세션을 열 때 다음 prompt를 그대로 사용한다.

```text
당신은 S3 wall-clock anytime deadline-fill 계획을 관장하는 scheduler입니다.

권위 문서:
/Users/brown/workspace/ogc/fable-native-implementation/docs/fable/implementation-steps/s3-anytime-deadline-fill.md

문서를 처음부터 끝까지 읽으세요. 직접 production/test 코드를 구현하거나 phase의
검증 명령을 대신 실행하지 마세요. 현재 worktree/branch/commit/phase handoff를
감사하고, 다음으로 실행 가능한 phase 하나를 선택하여 반드시 별도의 새 실행 세션에
전달할 완전한 prompt를 만드세요. 제품이 새 세션 시작 기능을 제공하면 그 prompt로
새 세션을 시작하고, 제공하지 않으면 사용자가 새 세션에 붙여 넣을 prompt를 정확히
출력한 뒤 기다리세요.

phase 결과가 COMPLETE이면 commit, clean status, evidence, protected hashes, scope를
감사한 뒤 다음 phase를 위한 또 다른 새 세션을 준비하세요. BLOCKED이면 다음 phase로
넘어가지 말고 root cause를 분류하여 동일 phase 또는 계획이 지정한 이전 phase의
recovery를 별도의 새 세션으로 시작하세요. Q 실패는 같은 identity로 재실행하지
마세요. AF-09가 완료될 때까지 이 상태 머신을 따르세요.

첫 실행에서는 AF-00만 시작하세요. legacy worktree에서는 어떤 구현/test/Git
mutation도 하지 마세요. push와 PR은 사용자의 별도 승인 전까지 금지합니다.
```

## 11. 최종 성공 기준

계획 전체는 다음을 모두 만족할 때만 `COMPLETE`다.

- public default에서 `s3_anytime_fill=true`
- 기존 qualified S3 anchor보다 나쁜 final 0건
- hard-5 및 package/stress 모든 결과 Stage 5
- hard deadline overrun, crash, leak, unverified return 0
- 120초 hard-5 각각 useful work ≥102.6초
- 300초가 60초보다 segment/batch/iteration 모두 많음
- 300초 objective <= 60초 objective
- quota 소진이 parent work deadline 전 최종 종료 사유가 되는 사례 0
- hard-5 strict late improvement 최소 1건
- feature-off regression과 public return schema 변화 0
- isolated package/rehearsal PASS
- checker/reference/input/protected evidence hash 불변
- legacy worktree mutation 0
- final branch clean, evidence/commit/package identity 일치

하나라도 실패하면 P0-1을 완료로 기록하지 않는다. 마지막 qualified S3/S6 제품은
항상 안전한 fallback으로 유지한다.
