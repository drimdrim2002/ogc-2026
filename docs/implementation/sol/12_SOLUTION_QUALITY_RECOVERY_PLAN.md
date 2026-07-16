# OGC-SAGE 해 품질 회복 통합 실행 계획

작성일: 2026-07-16  
상태: 실행 준비 완료  
작업공간: `/Users/brown/workspace/ogc/sol-native-implementation`  
기준 설계: `docs/OGC2026_Competition_Algorithm_Design.md`  
실행 문서: `docs/implementation/sol/quality-recovery/`

## 1. 목적

이 계획의 목적은 단순한 처리 속도 향상이 아니라, 동일 wall-clock 예산에서 official checker가 인정하는 최종 objective를 안정적으로 낮추는 것이다. 최근 C++ repair kernel은 fixed-work 후보 생성 속도와 parity를 개선했지만, `prob_23` 120초 관측에서 Python 기본 경로 대비 native 경로가 다음과 같이 크게 회귀했다.

| 항목 | Python | Native | 변화 |
|---|---:|---:|---:|
| final objective | `49,102,302` | `89,478,006` | `+40,375,704`, 약 `+82.23%` 악화 |
| ALNS iterations | `50` | `22` | `-56%` |

Objective는 낮을수록 좋다. 이 결과는 C++ micro-kernel 가속만으로 solver 전체의 fixed-time 품질이 개선되지 않으며, candidate/pair 증폭, Python/Shapely exact resolve, pybind 경계, finalize, deadline fallback, ALNS acceptance 의미론을 함께 다뤄야 함을 보여준다.

동시에 전체 solver의 가장 큰 품질 병목은 여전히 P3 초기 constructor다. 기존 계측에서 constructor profile은 모두 50,000 candidate cap에 도달했고, serial safe-tail fallback 비율 중앙값은 약 96%였다. 따라서 이 계획은 다음 우선순위를 고정한다.

1. 초기해 품질을 지배하는 constructor를 먼저 개선한다.
2. native repair가 fixed-time 탐색량과 품질을 떨어뜨리는 문제를 해결한다.
3. ALNS trajectory, acceptance, stall, neighborhood 의미론을 seed-robust하게 만든다.
4. retiming과 assignment를 그 다음에 최적화한다.
5. MIP/interlock은 잔여 병목 증거가 있을 때만 조건부로 검토한다.
6. 마지막에 독립 부하 환경에서 통합 및 release matrix를 새 source identity로 재실행한다.

## 2. 현재 고정 사실

- official `baseline/utils.py` SHA-256은 `d45aaeafdce8bf80d59d097f655c43313a4951bed43b6628e3b1cf62d4876a94`다.
- `baseline/baseline_greedy.py`는 동결 비교 기준이며 수정하지 않는다.
- production default는 `repair_backend="python"`, native prefilter OFF다.
- candidate MIP과 interlock은 기본 OFF다.
- native fixed-work capped/unbounded parity, conservative prefilter, fallback, Stage 5 회귀 테스트는 통과했다.
- native kernel은 좁은 fixed-work에서 약 18.3배였지만, solver fixed-time 품질 우위는 증명되지 않았다.
- 현재 native는 P5 heuristic repair에만 연결되며 P3 initial constructor에는 연결되지 않는다.
- native는 `UNKNOWN` geometry를 Python `GeometryKernel.relation()`과 Shapely에 위임한다.
- native dominance guard는 Python SA 경로와 다른 trajectory를 만들 수 있다.
- Python default도 repair telemetry session, snapshot digest, RSS 계측 비용을 부담하므로 이전 release artifact와 동일한 실행 비용이라고 가정하지 않는다.
- 기존 60/180초×40×3 seed 결과는 최근 dirty/native source의 release evidence가 아니다.
- 기존 180초 hard-10 final artifact는 외부 benchmark overlap으로 release-ineligible이다.
- macOS local C++ binary 증거는 Ubuntu/ELF/submission archive 증거가 아니다.

## 3. 문서와 phase 우선순위

아래 순서는 해 품질 영향도와 선행 의존성을 반영한 강제 실행 순서다. 이전 phase의 gate와 controller audit가 끝나기 전에 다음 phase를 시작하지 않는다.

| Phase | 중요도 | 문서 | 목표 | 기본 종료 결정 |
|---:|---|---|---|---|
| 0 | 필수 기반 | [00_BASELINE_AND_REGRESSION_CONTRACT.md](quality-recovery/00_BASELINE_AND_REGRESSION_CONTRACT.md) | 현재 source/config/data/환경과 품질 회귀를 재현 가능한 계약으로 동결 | READY/PENDING_ENVIRONMENT/PASS/BLOCKED |
| 1 | 최우선 | [01_CONSTRUCTOR_INITIAL_SOLUTION_QUALITY.md](quality-recovery/01_CONSTRUCTOR_INITIAL_SOLUTION_QUALITY.md) | 96% serial fallback을 줄이고 시간 gate 안에서 강한 초기해 생성 | constructor variant 승격/기각 |
| 2 | 최우선 | [02_NATIVE_REPAIR_FIXED_TIME_RECOVERY.md](quality-recovery/02_NATIVE_REPAIR_FIXED_TIME_RECOVERY.md) | `prob_23` native 82% 회귀와 50→22 iteration 감소 해결 | native benchmark GO/NO-GO |
| 3 | 높음 | [03_ALNS_TRAJECTORY_AND_ACCEPTANCE.md](quality-recovery/03_ALNS_TRAJECTORY_AND_ACCEPTANCE.md) | Python/native acceptance, dominance, stall, cadence를 seed-robust하게 정리 | ALNS 정책 승격/기각 |
| 4 | 중상 | [04_RETIMING_QUALITY_AND_COST.md](quality-recovery/04_RETIMING_QUALITY_AND_COST.md) | 반복 Gurobi 비용을 줄이면서 Z1 개선 유지 | retime 정책 승격/기각 |
| 5 | 중간 | [05_ASSIGNMENT_AND_CONGESTION_QUALITY.md](quality-recovery/05_ASSIGNMENT_AND_CONGESTION_QUALITY.md) | assignment/guide가 constructor 및 Z2/Z3에 주는 효과 개선 | assignment 정책 승격/기각 |
| 6 | 조건부 | [06_MIP_INTERLOCK_CONDITIONAL.md](quality-recovery/06_MIP_INTERLOCK_CONDITIONAL.md) | 잔여 병목이 증명될 때만 MIP/interlock 재검토 | 명시적 ON/OFF 동결 |
| 7 | 통합 gate | [07_INTEGRATED_HARD10_VALIDATION.md](quality-recovery/07_INTEGRATED_HARD10_VALIDATION.md) | 누적 후보를 hard-10 다중 seed에서 검증 | release matrix 진입/복귀 |
| 8 | 최종 gate | [08_FINAL_40_RELEASE_VALIDATION.md](quality-recovery/08_FINAL_40_RELEASE_VALIDATION.md) | 40개 competition-aligned matrix와 archive를 검증 | release 승인/기각 |

Scheduler 운영 계약은 [SCHEDULER_PROTOCOL.md](quality-recovery/SCHEDULER_PROTOCOL.md)를 따른다.

## 4. 절대 불변식

모든 phase와 모든 diagnose/fix/retry session은 다음을 지킨다.

- official checker Stage 5가 최종 권위다.
- internal `Z1`, floored `Z2`, `Z3`, total은 checker와 상대 오차 `<=1e-6`다.
- validated-best trace는 run 내부에서 non-increasing이다.
- candidate 변경은 별도 draft와 transactional commit을 사용한다.
- approximate geometry는 `DEFINITELY_FREE` 또는 reject-only prefilter로만 사용하고 `UNKNOWN`은 exact path로 보낸다.
- Python/native/Gurobi/import/ABI/timeout/invalid-output 실패 시 이미 검증된 incumbent를 보존한다.
- `baseline/utils.py`, `baseline/baseline_greedy.py`, training data는 수정하지 않는다.
- `time.monotonic()` deadline과 checker reserve를 유지한다.
- `-ffast-math`, 비결정적 parallel reduction, native 내부 임의 멀티스레딩을 사용하지 않는다.
- 사용자 변경, 기존 dirty/untracked 파일과 immutable artifact를 삭제·덮어쓰기하지 않는다.
- 한 worker session은 한 phase 또는 한 diagnose/fix/retry 역할만 수행한다.
- 서로 다른 worker가 같은 working tree에 동시에 쓰지 않는다.
- commit/push는 사용자가 별도로 승인하지 않으면 수행하지 않는다.

## 5. 비교 원칙

### 5.1 두 비교 기준

- **frozen Python baseline**: Phase 0에서 현재 source의 Python default를 정확한 hash/config로 동결한다.
- **cumulative candidate**: 직전 phase까지 controller gate를 통과한 구성이다.

각 phase candidate는 과거 artifact가 아니라 같은 source snapshot, dataset, seed, budget, 환경에서 다시 실행한 cumulative baseline과 paired 비교한다.

### 5.2 fixed-work와 fixed-time 분리

- fixed-work는 후보/order/score/RNG/acceptance parity와 순수 처리량을 판정한다.
- fixed-time은 실제 objective, anytime trace, iterations, accepted/new-best를 판정한다.
- fixed-work speedup만으로 품질 승격을 선언하지 않는다.
- profiler 또는 외부 CPU 오염이 있는 fixed-time run은 품질 gate에서 제외한다.

### 5.3 공통 지표

- correctness: Stage, violations, objective/component parity, exception, timeout, trace regression
- quality: W/T/L, Borda, paired relative delta, median/p90/worst, weighted `Z1/Z2/Z3`
- anytime: primal integral, time-to-first-improvement, validated-best event 수
- throughput: ALNS iterations, repair attempts, candidates, pairs, exact calls, accepted/new-best per second
- phase cost: constructor, repair, exact resolve, finalize, retime build/solve, checker
- native: requested/actual backend, calls/successes/fallback/deadline/invalid/exception, boundary and copied bytes

## 6. 공통 품질 gate

각 phase 문서의 추가 gate와 함께 다음을 모두 만족해야 승격할 수 있다.

- 모든 비교 run Stage 5, violations 0
- objective/component parity `<=1e-6`
- exception, outer timeout, checker failure, trace regression 0
- candidate Win > Loss
- median paired relative delta < 0
- Borda가 baseline보다 나빠지지 않음
- p90 또는 worst의 중대한 회귀를 평균 개선으로 덮지 않음
- 같은 seed의 longer-budget regression은 원인 규명 전 release blocker
- 처리량 개선 phase는 fixed-work뿐 아니라 fixed-time 품질 비열화를 입증

단일 regression instance가 명시된 phase에서는 그 instance가 반드시 non-loss여야 한다. Phase 2의 `prob_23`이 이에 해당한다.

## 7. fixed-time CPU measurement 계약

### 7.1 system-wide capacity와 process tree

- per-PID raw `ps %CPU`는 감쇠 평균인 diagnostic top-contributor 정보로만 기록하며 gate 입력으로 사용하지 않는다.
- gate는 1초 cadence의 cumulative system CPU time delta를 사용한다. macOS에서는 Mach host tick 또는 이를 native하게 래핑한 `psutil.cpu_times()` delta를 사용한다.
- 모든 delta는 `logical_cores × wall_seconds` capacity로 정규화한다.
- `(PID, create_time)` identity와 전체 ancestry/descendant를 추적한다. pair runner, solver, monitor subtree만 `measurement`이며 이 subtree CPU seconds만 system busy에서 차감한다.
- 나머지는 `competing_compute`, `os_background`, `controller_ui`, `unknown`으로 진단 분류한다. process 이름은 분류 증거일 뿐 aggregate external busy allowlist가 아니다.
- gating completeness는 서로 독립인 `host_tick_valid`, 1초 host sampling의 `temporal_coverage`, `max_sample_gap_seconds`, `measurement_subtree_identity_complete`, `measurement_subtraction_complete` 필드와 조건으로 기록한다.
- `process_access_coverage`는 접근 가능한 PID 수 / 관측 PID 수인 attribution reachability 진단값일 뿐 `READY/PENDING_ENVIRONMENT/PASS/BLOCKED` 입력이 아니다. `cpu_time_attribution_coverage`는 접근 가능한 non-measurement process에 귀속된 CPU seconds / host-derived external busy seconds를 `0..1`로 clamp한 진단 confidence이며 gate 입력이 아니다.
- `AccessDenied`, `NoSuchProcess`, `ZombieProcess`, `OSError`는 각각 count/evidence로 기록한다. unrelated non-measurement attribution 오류는 diagnostic이고, measurement root/등록 subtree의 접근·identity·CPU delta/subtraction 실패만 structural hard failure다. system tick 오류와 temporal sampling 오류도 별도 축으로 기록한다.
- aggregate external busy는 항상 host cumulative busy에서 measurement subtree CPU만 빼 계산한다. 접근 불가·미귀속 non-measurement workload를 aggregate에서 제외하지 않는다.

### 7.2 pre-run qualification과 liveness

- 최대 300초 동안 10초 qualification window를 탐색한다. sampling cadence는 1초다.
- 10초 평균 available external capacity가 `>=70%`이고 어떤 5초 rolling available capacity도 `<50%`가 아니면 `READY`다.
- temporal coverage `>=95%`와 max sample gap `<=2.5초`도 qualification window의 필수 조건이다. 단일 window의 temporal coverage/gap 실패는 구조적 `BLOCKED`가 아니며 계속 탐색한다.
- 300초 안에 유효 window가 없으면 solver 실패가 아닌 `PENDING_ENVIRONMENT`로 정상 종료한다.
- `BLOCKED`는 host tick 수집/회귀/구조 실패 또는 measurement root/등록 subtree의 접근·identity·CPU subtraction 불가에만 사용한다.
- authoritative contract 밖의 consecutive clean heartbeat 두-window 규칙은 금지한다.

### 7.3 runtime validity

fixed-time run 전체에서 같은 1초 sampling을 지속한다. 다음 중 하나면 material overlap이다.

- run 평균 external busy `>30%`
- 임의 5초 rolling external busy 평균 `>50%`
- 알려진 `competing_compute`가 `>=0.5` core-equivalent로 5초 이상 연속 겹침
- temporal coverage `<95%`
- sample gap `>2.5초`
- measurement subtree CPU 차감 불가

pre-run qualification만으로 runtime validity를 판정하지 않는다. 외부 process를 종료하거나 변경하지 않는다.

### 7.4 atomic alternating pair

- pair 1은 Python→Native, pair 2는 Native→Python 순서다.
- pair 하나가 최소 atomic unit이다. 두 run의 평균 external busy 차이는 `<=5 percentage points`, 1초 sample p95 차이는 `<=10 points`여야 한다.
- 어느 한 run의 overlap/coverage 또는 fairness가 실패하면 양쪽 모두 폐기하고 새 immutable pair ID로 전체 pair를 재실행한다. 좋은 한쪽만 교체하지 않는다.
- source/config/data/native binary/measurement contract가 바뀌면 전체 matrix와 모든 pair에 새 identity를 부여한다.

## 8. Artifact와 기록 규칙

각 phase는 다음 root를 새로 만든다.

```text
artifacts/ogc_sage/quality-recovery/<phase>-<run-id>/
```

필수 항목:

- `identity.json`: branch, HEAD, dirty diff hash, source/config/data/environment
- `commands.json`: 실제 실행 명령과 종료 상태
- `measurement-contract.json`, `cpu-qualification.json`: system-wide capacity 계약과 10초 qualification evidence. schema는 host tick 유효성, temporal coverage, max gap, measurement subtree identity, measurement subtraction 완전성, diagnostic-only process access/CPU-time attribution coverage, categorized process errors를 구분한다.
- 각 fixed-time run의 `runtime-capacity.json` 또는 동등한 embedded record: 1초 interval, temporal coverage, max gap, subtree 차감, diagnostic attribution coverage, overlap 판정
- 각 pair의 immutable ID/order, fairness, 양쪽 disposition
- raw result와 checker result
- `comparison.json`: paired metrics
- `gate.json`: READY/PENDING_ENVIRONMENT/PASS/BLOCKED 또는 phase별 NO-GO와 이유
- recursive `SHA256SUMS`

기존 실패 artifact를 수정하거나 덮어쓰지 않는다. tracked phase 문서에는 artifact 경로, 중요한 수치, gate, SHA-256만 append한다.

## 9. 실패 처리

Phase worker가 실패하거나 결론 불가인 경우 scheduler는 같은 session에서 코드를 고치게 하지 않는다.

```text
phase worker FAIL/NO-GO/INCONCLUSIVE
  -> fresh diagnose session (수정 금지)
  -> controller audit
  -> fresh fix session (진단된 한 원인만 수정)
  -> controller audit
  -> fresh retry session (수정 금지, gate 재검증)
  -> PASS 또는 반복
```

원인이 여러 개면 fix를 한 patch에 섞지 않고 중요도순으로 하나씩 처리한다. 외부 상태만이 blocker라면 코드를 바꾸지 않으며, 동일 blocker를 우회하기 위해 품질 gate를 낮추지 않는다.

## 10. 완료 정의

이 계획은 다음 조건을 모두 만족할 때만 완료된다.

- Phase 0~8의 gate와 controller audit 완료
- `prob_23` native 120초 catastrophic regression 재발 없음
- constructor time/fallback/quality gate 통과
- native를 켤 경우 fixed-time 다중 instance/seed에서 Python 대비 non-loss 및 전체 우세
- native를 끌 경우 그 결정과 Python-only 최종 config를 명시적으로 동결
- hard-10과 all-40 최종 matrix Stage 5 100%
- competition-aligned quality 지표 통과
- current source와 archive allowlist/hash/clean extraction 일치
- 중앙 진행 문서가 실제 source/test/artifact 상태와 일치

## 11. Scheduler 시작점

새 scheduler session은 가장 먼저 다음 문서를 전체로 읽는다.

1. 이 문서
2. `quality-recovery/SCHEDULER_PROTOCOL.md`
3. `quality-recovery/README.md`
4. Phase 0 문서
5. 기존 `performance/README.md`
6. `11_PYBIND_REPAIR_KERNEL_ACCELERATION_PLAN.md`의 최신 실행 기록

그 후 Phase 0 worker session 하나만 생성한다. Phase 0 완료·독립 audit 전에는 Phase 1을 생성하지 않는다.
