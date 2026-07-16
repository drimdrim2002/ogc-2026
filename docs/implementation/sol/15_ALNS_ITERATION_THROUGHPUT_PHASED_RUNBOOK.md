# ALNS iteration 처리량 개선 phased implementation runbook

> 상태: EXECUTION READY  
> 작업공간: `/Users/brown/workspace/ogc/sol-native-implementation`  
> 설계 근거: `docs/implementation/sol/14_ALNS_ITERATION_THROUGHPUT_REDESIGN.md`  
> 실행 방식: Phase별 새 Codex 세션, 완전 직렬, 이전 Phase PASS 후 다음 Phase 시작

## 1. 목적과 문서 권한

이 문서는 ALNS iteration 처리량 개선을 추가 설계 없이 구현·검증하기 위한 실행 권위 문서다. 14번 문서는 배경과 근거이고, 구현 세부가 충돌하거나 모호하면 이 runbook이 우선한다. 특히 14번 문서 15.3절의 counter 증가 위치 유지 지시는 이 문서 3.1절로 대체한다.

최종 결과는 다음을 동시에 만족해야 한다.

- 실제 destroy → repair → acceptance outer iteration 의미 유지
- repair-local bounded candidate reuse와 lazy refresh 구현
- segment/stall deferred retiming과 best-effort end 처리 구현
- native/Python fallback, exact recheck, transactional safety 유지
- `prob_21.json`~`prob_25.json` 5개에서 binding native baseline strict 개선

이번 runbook은 새 solver, 새 scheduler framework, dependency graph, global cache, native C++ 변경을 만들지 않는다.

## 2. 공통 불변식

모든 Phase와 recovery 세션에 다음 규칙을 적용한다.

- 한 번에 worker 세션 하나만 실행한다. 병렬 worker/subagent/benchmark를 금지한다.
- 각 Phase는 반드시 새로운 Codex 세션에서 실행한다.
- 같은 세션에서 다음 Phase를 시작하지 않는다.
- 이전 Phase가 PASS가 아니면 다음 Phase를 생성하지 않는다.
- 새 branch/worktree를 만들지 않는다.
- 기존 dirty/untracked 파일을 reset, restore, stash, clean, 삭제하지 않는다.
- 사용자 승인 없이 commit/push하지 않는다.
- unrelated refactor, generic abstraction, 새 framework를 만들지 않는다.
- `baseline/solver/native_repair.py`와 `native/ogc_native`는 수정하지 않는다.
- Production defaults `repair_backend="native"`, `native_exact_mode="native"`, prefilter/MIP/interlock OFF를 유지한다.
- 유일한 새 solver setting은 `alns_throughput_redesign_enabled` 하나다.
- 성능 validation data는 `data/train/prob_21.json`~`prob_25.json` 5개만 사용한다.
- 다른 instance, all-40, hard-10, 다중 seed, warmup, 동일-source retry를 금지한다.
- 전체 baseline test suite를 실행하지 않는다. 이 문서에 적힌 focused tests만 실행한다.
- Repository 안에 scheduler state, SHA matrix, 대형 artifact tree를 만들지 않는다.
- Build/benchmark output은 `/tmp`의 지정된 attempt 디렉터리만 사용한다.

## 3. 구현 전 확정 계약

### 3.1 Outer iteration과 operator attempts

완료된 outer iteration은 operator 시도가 repair 또는 exception 처리 경계까지 도달하고 loop 종료부에서 `iterations += 1`이 실행된 경우다. Post-destroy deadline으로 repair를 시작하지 못한 시도는 outer iteration도 operator attempt도 아니다.

`run_lns()`는 다음 local boolean을 사용한다.

```python
attempt_counted = False
```

정상 경로는 다음 순서로 고정한다.

1. Operator와 metric을 선택하고 `iteration_started`를 기록한다.
2. Destroy를 실행한다.
3. Destroy가 비어 있으면 exception 경로로 이동한다.
4. Post-destroy budget gate가 실패하면 `exit_reason="DEADLINE"`로 break한다. 이 경로는 operator/size attempt, operator/size time과 iteration을 증가시키지 않는다. 이미 소비한 destroy 시간은 global `phase_time["destroy"]`에만 남긴다.
5. Gate 통과 직후 `metric.attempts += 1`, `segment_uses[operator_index] += 1`, `attempt_counted=True`를 실행한다.
6. Repair와 acceptance를 실행한다.
7. `finally`의 per-operator/per-size timing 집계는 `attempt_counted`일 때만 실행한다.
8. `try` 안의 destroy 단계에서 exception이 post-destroy gate 이전에 발생하면 exception handler에서 attempt를 정확히 한 번 count하고 `attempt_counted=True`로 만든다.
9. Break가 아니면 loop 종료부에서 `iterations`와 `total_iterations`를 1 증가시킨다.

필수 invariant는 다음이다.

```text
outer_iterations == operator_attempts
```

Counter 값을 맞추기 위해 candidate generation, refresh, retime을 iteration으로 세지 않는다.

### 3.2 Repair-local cache entry

`baseline/solver/neighborhoods.py`에 다음 private type 하나만 추가한다.

```python
@dataclass(frozen=True, slots=True)
class _RepairCandidateCacheEntry:
    source_version: int
    candidates: tuple[CandidateScore, ...]
    rollback_placement: Placement
    rollback_available: bool
```

Cache는 `heuristic_repair()` local dictionary이며 함수 반환과 함께 폐기한다. Cross-repair/global cache는 만들지 않는다.

Entry invariant는 다음이다.

- candidates 길이 1~3
- candidate placement 중복 없음
- candidate block id가 dictionary key와 동일
- candidate `state_version == source_version`
- candidates는 repair canonical tie 순서
- initial generation에서 rollback이 없으면 malformed `ERROR`
- 이후 state에서 rollback이 invalid되면 block-local regeneration을 한 번 수행하고 `rollback_available=False`로 전환 가능
- `rollback_available=False`가 된 block은 같은 repair에서 rollback invalid만을 이유로 다시 regenerate하지 않음
- Placement가 추가되기만 하므로 false에서 true로 되돌리지 않음

### 3.3 Canonical score helper

`baseline/solver/construct.py`에 다음 두 helper만 추가한다.

```python
def normalize_repair_candidate(
    score: CandidateScore,
    rollback_placement: Placement,
) -> CandidateScore:
    ...

def refresh_insertion_candidate(
    state: IndexedSolutionState,
    candidate: CandidateScore,
    kernel: GeometryKernel,
    *,
    rollback_placement: Placement,
) -> CandidateScore | None:
    ...
```

`normalize_repair_candidate()`는 score 값은 바꾸지 않고 canonical tie만 다음 tuple로 재구성한다.

```text
(
  total_delta,
  fragmentation,
  0 if placement.bay_id == rollback_placement.bay_id else 1,
  placement.exit,
  placement.entry,
  placement.bay_id,
  placement.orient_idx,
  placement.x,
  placement.y,
  placement.block_id,
)
```

`refresh_insertion_candidate()`는 `candidate.placement`만 재사용하고 현재 state에서 `evaluate_insert()`를 호출한 뒤 위 canonical tie로 normalize한다. 과거 score의 delta/tie/version은 재사용하지 않는다.

### 3.4 Initial generation, lazy refresh, regeneration

Flag-on `heuristic_repair()`는 다음 순서만 사용한다.

1. Destroy된 모든 block을 sorted order로 한 번씩 full generate한다.
2. Initial result를 검증·normalize하고 cache에 저장한다.
3. Refreshed bounded set의 현행 regret 공식으로 block을 선택한다.
4. 기존 transactional commit을 실행한다.
5. Commit 뒤 remaining block의 cached placement 최대 3개만 refresh한다.
6. 아래 condition이면 해당 block 하나만 full regenerate한다.
7. Regeneration은 block/round당 최대 1회다.
8. Commit 실패 또는 regeneration 실패는 input snapshot identity를 반환한다.

Block-local regeneration condition은 다음 다섯 개뿐이다.

- refreshed candidate가 모두 invalid
- refreshed candidate 수가 `regret_depth` 미만
- `rollback_available=True`인 rollback이 현재 state에서 invalid
- refresh exception/malformed result
- mixed/future/corrupt state version

Regeneration 후 non-empty 후보가 regret depth 미만이면 추가 regeneration 없이 기존 `math.inf` shortage 의미를 사용한다. Regeneration 결과에서 rollback이 빠졌다면 `rollback_available=False`로 저장한다.

### 3.5 Repair failure status

Flag-on 실패 status를 다음으로 고정한다. 모든 실패 snapshot은 `heuristic_repair()` 입력 object identity다.

| Condition | `RepairResult.status` |
|---|---|
| Full generation 시작 전 또는 빈 결과 시 deadline 소진 | `BUDGET` |
| Deadline이 아닌 정상 generation의 빈 결과 | `NO_CANDIDATE` |
| Malformed candidate/version 또는 exception | `ERROR` |
| Transactional commit false | `STALE_CANDIDATE` |
| Final local feasibility 실패 | `LOCAL_REJECTED` |
| Native final candidate가 frozen repair anchor보다 나쁨 | `DOMINANCE_REJECTED` |

Refresh 실패는 즉시 repair 실패가 아니라 block-local regeneration을 먼저 수행한다. Regeneration까지 실패했을 때 위 표를 적용한다.

기존 native dominance guard와 `DOMINANCE_REJECTED`는 그대로 유지한다.

### 3.6 Candidate telemetry counting

다음 counter는 호출 성공 수가 아니라 **시도 수**다. 각 full generation 호출 직전에 증가시킨다.

- `candidate_generation_calls`
- `initial_full_generations`
- `block_local_regenerations`

다음은 placement 단위다.

- `refreshed_candidates`: refresh 호출 수
- `invalidated_candidates`: refresh가 `None`인 수
- `reusable_candidates`: 현재 state score로 성공적으로 재구성된 수
- `rollback_refresh_failures`: block당 최대 1회

Backend telemetry를 dict로 만든 뒤 위 logical fields를 merge하고 key 순서로 정렬해 `RepairResult.telemetry`에 저장한다. 같은 key가 생기면 logical field가 권위다.

### 3.7 Deferred retime state machine

Accepted non-MIP candidate에서 `retime_hook is not None`이고 `changed_ids`가 non-empty일 때만 pending set에 추가한다. `retime_hook is None`이면 pending을 새로 누적하지 않는다.

각 outer iteration 시작 시 다음을 초기화한다.

```python
deferred_retime_boundary_checked = False
```

Boundary 규칙은 다음이다.

- Segment: cooling 뒤, weight update 전
- Stall: 기존 위치인 destroy growth/reset 뒤, densify 전
- End: loop 종료 뒤 best-effort; deadline 종료에서는 skip 예상
- 같은 iteration에서 segment가 평가되면 stall은 재평가하지 않음
- Pending이 있을 때 start 또는 budget skip을 판정하면 `deferred_retime_boundary_checked=True`
- 마지막 iteration에서 이미 boundary를 평가했다면 end에서 즉시 재시도하지 않음
- End retime을 위해 outer loop를 조기 종료하거나 시간을 선예약하지 않음

Start gate는 다음으로 고정한다.

```text
DEFERRED_RETIME_START_MARGIN_SECONDS = 3.0
required_margin = max(3.0, budget.checker_p95 + config.checker_margin)
```

Budget skip은 pending을 유지한다. 실제 retime을 시작한 경우 success/no-improvement/error/worse와 무관하게 captured ids를 pending에서 제거한다.

Deferred retime은 destroy operator의 `accepted`, `new_best`, `exceptions`, `segment_scores`를 변경하지 않는다. Strict best 설치는 `retime_new_best`, exception은 `retime_errors`에만 기록한다. MIP transactional retime은 기존 동작을 유지한다.

### 3.8 단일 feature flag와 benchmark seam

유일한 새 solver setting은 다음이다.

```python
SubmissionConfig.alns_throughput_redesign_enabled: bool = False
```

같은 이름의 internal boolean을 `AlnsContext`와 `NeighborhoodContext`에 default `False`로 두고 `entry.py`가 전달한다. Flag-off는 기존 repair와 immediate retime 경로를 사용한다.

`experiments/ogc_sage/verify_native_p7_fixed_time.py`에는 CLI option 하나만 추가한다.

```text
--alns-throughput-redesign
```

Harness 계약은 다음이다.

- `store_true`, default false
- `--variant native`에서만 허용
- Python variant와 함께 사용하면 실행 전 오류
- Native `replace()`에 `alns_throughput_redesign_enabled=args.alns_throughput_redesign` 전달
- `command.json`, config actual, `native_override_only`에 실제 flag 기록
- 환경변수나 별도 variant를 만들지 않음

### 3.9 Throughput telemetry shape

`AlnsMetrics`에는 다음 field 하나만 추가한다.

```python
throughput_stats: tuple[tuple[str, int | float | None], ...] = ()
```

`AlnsMetrics.throughput_stats`의 raw key는 다음으로 고정하고 key 이름순 tuple로 저장한다.

```text
candidate_generation_calls
initial_full_generations
block_local_regenerations
refreshed_candidates
invalidated_candidates
reusable_candidates
rollback_refresh_failures
retime_pending_ids
retime_new_best
retime_errors
deferred_retime_skips_due_to_budget
```

`RunTrace.add_lns_invocation()`는 raw bundle을 복사한 뒤 다음 derived key를 추가해 `record["throughput"]`에 저장한다.

```text
outer_iterations
operator_attempts
accepted
new_best
retime_triggers
candidate_generation_calls_per_iteration
repair_seconds_per_iteration
retime_seconds_per_iteration
checker_seconds_per_iteration
wall_seconds_per_iteration
```

`operator_attempts`, `accepted`, `new_best`는 invocation의 per-operator 합이다. `wall_seconds_per_iteration`의 분자는 `ended - started`다. Iterations가 0이면 다섯 ratio는 `None`이다. Anchor와 extension record를 각각 유지하고 전체 합은 `lns_invocations`에서 계산한다. Legacy `model_stats["lns"]`는 그대로 둔다.

## 4. 허용 파일 범위

구현 Phase에서 수정 가능한 파일은 다음뿐이다.

- `baseline/solver/construct.py`
- `baseline/solver/neighborhoods.py`
- `baseline/solver/alns.py`
- `baseline/solver/runtime.py`
- `baseline/solver/entry.py`
- `experiments/ogc_sage/verify_native_p7_fixed_time.py`
- 관련 기존 focused test 파일

Focused test 파일은 다음 기존 파일만 사용한다.

- `baseline/tests/test_construct.py`
- `baseline/tests/test_neighborhoods.py`
- `baseline/tests/test_alns.py`
- `baseline/tests/test_alns_integration.py`
- `baseline/tests/test_lns_telemetry.py`
- `baseline/tests/test_packaging.py`
- `baseline/tests/test_native_repair_integration.py`

새 test framework나 새 test directory를 만들지 않는다.

## 5. Phase 개요와 공통 benchmark 계약

| Phase | 새 세션 역할 | 구현 범위 | Phase 종료 benchmark |
|---|---|---|---|
| 1 | IMPLEMENT | Candidate cache, counter invariant, public flag, harness seam | 5개 correctness hard gate + candidate/repair 추세 기록 |
| 2 | IMPLEMENT | Deferred retime state machine | 5개 correctness hard gate + Phase 1 대비 retime 추세 기록 |
| 3 | IMPLEMENT | Invocation throughput telemetry 통합 | 5개 correctness + binding baseline strict AND gate |

Phase 1 PASS 전 Phase 2 금지, Phase 2 PASS 전 Phase 3 금지다. 각 Phase는 구현과 focused tests가 끝난 뒤 같은 Phase 세션에서 benchmark를 실행한다. 세 Phase 모두 구현으로 source가 달라진 뒤 측정하므로 동일-source retry가 아니다.

총 fixed-time 실행은 정상 경로 기준 `3 phases × 5 instances × 120 seconds = 1,800 seconds`이며 모두 직렬이다. 별도 warmup이나 paired Python run은 추가하지 않는다.

### 5.1 고정 binary와 output root

모든 Phase에서 다음 binary만 사용한다.

```text
/tmp/ogc-native-p3/retry-a1/_ogc_native.cpython-312-darwin.so
SHA256 = d41ba45c454ace6750333e6a2c231be5aa6f5d05a1aa1e08aee923d6736355a5
```

Phase별 output root는 다음이다.

```text
/tmp/ogc-alns-throughput/p<PHASE>-benchmark-a<ATTEMPT>
```

`<PHASE>`와 `<ATTEMPT>`를 실행 전에 정수로 치환하고 command에 angle-bracket placeholder가 남아 있지 않음을 확인한다. 기존 root는 삭제하거나 덮어쓰지 않는다. RETRY는 source 수정 후 attempt를 증가시키고 새 root를 사용한다.

### 5.2 공통 fixed-time 명령

아래 5개 명령을 `prob_21`부터 `prob_25` 순서로 하나씩 실행한다. 앞 command가 종료되기 전 다음 command를 시작하지 않는다. 첫 command가 시작된 뒤 한 instance가 nonzero로 끝나도 증거를 보존하고 나머지 instance를 계속 실행한다. 같은 세션에서 재실행하지 않는다.

```bash
env -u OGC_NATIVE_MODULE_DIR PYTHONDONTWRITEBYTECODE=1 \
  /opt/homebrew/Caskroom/miniforge/base/envs/ogc-2026/bin/python \
  experiments/ogc_sage/verify_native_p7_fixed_time.py run \
  --variant native --alns-throughput-redesign \
  --instance data/train/prob_21.json --timelimit 120 --seed 20260710 \
  --order-ordinal 2 \
  --output /tmp/ogc-alns-throughput/p<PHASE>-benchmark-a<ATTEMPT>/prob_21/native \
  --native-binary /tmp/ogc-native-p3/retry-a1/_ogc_native.cpython-312-darwin.so \
  --expected-native-sha256 d41ba45c454ace6750333e6a2c231be5aa6f5d05a1aa1e08aee923d6736355a5

env -u OGC_NATIVE_MODULE_DIR PYTHONDONTWRITEBYTECODE=1 \
  /opt/homebrew/Caskroom/miniforge/base/envs/ogc-2026/bin/python \
  experiments/ogc_sage/verify_native_p7_fixed_time.py run \
  --variant native --alns-throughput-redesign \
  --instance data/train/prob_22.json --timelimit 120 --seed 20260710 \
  --order-ordinal 2 \
  --output /tmp/ogc-alns-throughput/p<PHASE>-benchmark-a<ATTEMPT>/prob_22/native \
  --native-binary /tmp/ogc-native-p3/retry-a1/_ogc_native.cpython-312-darwin.so \
  --expected-native-sha256 d41ba45c454ace6750333e6a2c231be5aa6f5d05a1aa1e08aee923d6736355a5

env -u OGC_NATIVE_MODULE_DIR PYTHONDONTWRITEBYTECODE=1 \
  /opt/homebrew/Caskroom/miniforge/base/envs/ogc-2026/bin/python \
  experiments/ogc_sage/verify_native_p7_fixed_time.py run \
  --variant native --alns-throughput-redesign \
  --instance data/train/prob_23.json --timelimit 120 --seed 20260710 \
  --order-ordinal 2 \
  --output /tmp/ogc-alns-throughput/p<PHASE>-benchmark-a<ATTEMPT>/prob_23/native \
  --native-binary /tmp/ogc-native-p3/retry-a1/_ogc_native.cpython-312-darwin.so \
  --expected-native-sha256 d41ba45c454ace6750333e6a2c231be5aa6f5d05a1aa1e08aee923d6736355a5

env -u OGC_NATIVE_MODULE_DIR PYTHONDONTWRITEBYTECODE=1 \
  /opt/homebrew/Caskroom/miniforge/base/envs/ogc-2026/bin/python \
  experiments/ogc_sage/verify_native_p7_fixed_time.py run \
  --variant native --alns-throughput-redesign \
  --instance data/train/prob_24.json --timelimit 120 --seed 20260710 \
  --order-ordinal 2 \
  --output /tmp/ogc-alns-throughput/p<PHASE>-benchmark-a<ATTEMPT>/prob_24/native \
  --native-binary /tmp/ogc-native-p3/retry-a1/_ogc_native.cpython-312-darwin.so \
  --expected-native-sha256 d41ba45c454ace6750333e6a2c231be5aa6f5d05a1aa1e08aee923d6736355a5

env -u OGC_NATIVE_MODULE_DIR PYTHONDONTWRITEBYTECODE=1 \
  /opt/homebrew/Caskroom/miniforge/base/envs/ogc-2026/bin/python \
  experiments/ogc_sage/verify_native_p7_fixed_time.py run \
  --variant native --alns-throughput-redesign \
  --instance data/train/prob_25.json --timelimit 120 --seed 20260710 \
  --order-ordinal 2 \
  --output /tmp/ogc-alns-throughput/p<PHASE>-benchmark-a<ATTEMPT>/prob_25/native \
  --native-binary /tmp/ogc-native-p3/retry-a1/_ogc_native.cpython-312-darwin.so \
  --expected-native-sha256 d41ba45c454ace6750333e6a2c231be5aa6f5d05a1aa1e08aee923d6736355a5
```

Harness 안에서 실행되는 final official checker가 instance당 정확히 1회다. 별도 checker command를 추가하지 않는다.

### 5.3 모든 Phase의 correctness hard gate

각 Phase의 5개 `run.json` 모두 다음을 만족해야 다음 Phase로 갈 수 있다.

- `.result.error == null`
- `.result.trace_exceptions == []`
- `.result.checker.feasible == true`
- `.result.checker.stage == 5`
- `.result.checker.violations == []`
- Internal/checker `z1`, `z2`, `z3`, `total` parity
- `.alns.iterations > 0`
- Outer iterations == 모든 invocation/operator attempts 합
- `.native_telemetry.sums`의 `native_exception_count`, `native_error_count`, `native_invalid_output_count`, `geos_error_count`, `returned_candidate_recheck_failures`, `python_reference_calls_after_deadline`, `python_full_repair_started_after_deadline`가 모두 0

한 instance라도 correctness FAIL이면 해당 Phase가 FAIL이다.

### 5.4 성능 비교 필드와 gate 수준

Binding baseline 출처는 Codex task `019f69fd-6a79-7a10-ab10-9ba3167d9f59`의 `/tmp/ogc-native-p4/validate-a2/prob_XX/native/run.json`이다.

| Metric | `run.json` 값 |
|---|---|
| Objective | `.result.objective.total` |
| Outer iterations | `.alns.iterations` |
| Total repair s/iter | `.alns.repair_seconds_per_iteration` |
| Extension repair s/iter | `.alns.by_kind.extension.repair_seconds_per_iteration` |
| Retime s/iter | `.alns.retime_seconds / .alns.iterations` |
| Retime triggers/iter | 모든 `.alns.invocations[*].retime_triggers` 합 / `.alns.iterations` |
| Extension candidate generations/iter | `.native_telemetry.sums.candidate_generation_calls / .alns.by_kind.extension.iterations` |

Phase 1은 objective, iterations, repair, retime 지표를 binding baseline과 나란히 기록한다. Binding baseline에는 logical `candidate_generation_calls`가 없으므로 Phase 1 candidate generations/iter는 최초 관측값으로만 기록한다. Phase 2는 Phase 1 PASS artifact 및 binding baseline과 나란히 기록하고 candidate generations/iter는 Phase 1과 비교한다. Phase 1·2에서 correctness는 hard gate지만 성능 수치는 **diagnostic only**다. 중간 Phase의 objective 또는 timing이 strict improvement가 아니어도 그 이유만으로 Phase를 FAIL 처리하지 않는다.

Phase 3만 다음 strict AND gate를 적용한다.

| Instance | Objective | Outer iterations | Total repair s/iter | Extension repair s/iter | Retime s/iter |
|---|---:|---:|---:|---:|---:|
| `prob_21` | `< 17,308,584` | `> 66` | `< 1.1295814367256367` | `< 2.0150268181599675` | `< 0.4291875505445977` |
| `prob_22` | `< 1,594,867` | `> 184` | `< 0.4481704336193734` | `< 0.5321131372973988` | `< 0.13154786231019028` |
| `prob_23` | `< 15,305,668` | `> 54` | `< 1.4491142754591744` | `< 2.4545250666444190` | `< 0.5067918040364964` |
| `prob_24` | `< 3,055,604` | `> 78` | `< 1.0563540379083953` | `< 1.3774584874481661` | `< 0.2567642868462150` |
| `prob_25` | `< 1,851,982` | `> 72` | `< 1.1901187633185246` | `< 1.4031495127532656` | `< 0.2864239149178805` |

각 행의 다섯 조건과 5.3절 correctness를 모두 만족해야 해당 instance가 PASS다. Equality는 FAIL이고 aggregate/평균/median으로 개별 FAIL을 상쇄하지 않는다. 5/5 PASS만 Phase 3 PASS다. Timing noise를 이유로 gate를 완화하거나 동일 source를 재실행하지 않는다.

## 6. Phase 1 — Candidate cache, counter, benchmark seam

### 6.1 수정 범위

- `baseline/solver/construct.py`
- `baseline/solver/neighborhoods.py`
- `baseline/solver/alns.py`: 3.1 counter와 internal context flag
- `baseline/solver/runtime.py`: public flag만
- `baseline/solver/entry.py`: flag 전달만
- `experiments/ogc_sage/verify_native_p7_fixed_time.py`: CLI seam과 candidate logical sum fields만
- `baseline/tests/test_construct.py`
- `baseline/tests/test_neighborhoods.py`
- `baseline/tests/test_alns.py`
- `baseline/tests/test_packaging.py`
- `baseline/tests/test_native_repair_integration.py`

Deferred retime과 3.9 invocation throughput bundle은 이 Phase에서 구현하지 않는다.

### 6.2 필수 구현

- 3.1~3.6 전체
- `AlnsContext`/`NeighborhoodContext` internal flag default false와 전달
- 3.8 public flag, entry propagation, harness CLI seam
- Harness `aggregate_repair_telemetry().sum_fields`에 3.6의 일곱 logical candidate counter 추가
- Flag-off legacy path 유지

### 6.3 필수 tests와 명령

- Current/stale/corrupt version refresh와 canonical tie
- Blocked candidate invalidation과 block-local regeneration 1회 제한
- Rollback invalid 후 반복 regeneration 없음
- Regret depth 미만 `math.inf`
- Failure status와 input identity
- Flag-off legacy full-generation 흐름
- Post-destroy deadline 및 exception의 attempts/iterations 일치
- `SubmissionConfig` flag default false/type validation/as_dict
- Entry propagation
- Harness flag native-only와 candidate logical sum field

```bash
PYTHONPATH=baseline PYTHONDONTWRITEBYTECODE=1 /opt/homebrew/Caskroom/miniforge/base/envs/ogc-2026/bin/python -m unittest \
  baseline.tests.test_construct \
  baseline.tests.test_neighborhoods \
  baseline.tests.test_alns \
  baseline.tests.test_packaging \
  baseline.tests.test_native_repair_integration
```

```bash
/opt/homebrew/Caskroom/miniforge/base/envs/ogc-2026/bin/python \
  experiments/ogc_sage/verify_native_p7_fixed_time.py run --help
```

### 6.4 Benchmark와 PASS 조건

Focused tests와 help 확인이 PASS한 뒤 5.2절 명령을 `PHASE=1`로 실행한다. Objective, iterations, total/extension repair s/iter, retime s/iter는 binding baseline과 비교하고 candidate generations/iter는 최초 관측값으로 instance별 기록한다.

Phase 1 PASS 조건은 다음이다.

- Focused tests 전부 PASS
- Help에 `--alns-throughput-redesign` 존재
- 5개 benchmark artifact 모두 생성
- 5/5 correctness hard gate PASS
- Diagnostic 성능 표 작성
- Flag-off production config 유지
- 허용 파일 밖 변경 없음
- Native C++/native adapter 변경 없음

성능 diagnostic 미달 자체는 Phase 1 FAIL 사유가 아니다. 다른 PASS 조건이 실패하면 같은 세션에서 추가 수정하지 않고 첫 직접 실패 원인과 관련 파일을 보고한 뒤 종료한다.

## 7. Phase 2 — Deferred retime

### 7.1 진입 조건

- Phase 1 PASS 보고와 5개 benchmark artifact 존재
- Phase 1 변경이 current working tree에 유지됨
- Phase 1 PASS attempt 번호와 output root가 보고되어 있음

### 7.2 수정 범위

- `baseline/solver/alns.py`
- `baseline/tests/test_alns.py`
- `baseline/tests/test_alns_integration.py`
- `baseline/tests/test_lns_telemetry.py`

Public flag, harness seam, candidate cache는 이 Phase에서 재설계하지 않는다. 3.9 final throughput bundle은 Phase 3으로 남긴다.

### 7.3 필수 구현

- 3.7 deferred retime state machine
- Segment/stall/end trigger event와 local counters
- Anchor pending state의 extension 전달
- Deferred result의 operator reward/exception 비귀속
- MIP transactional retime 무변경

### 7.4 필수 tests와 명령

- Accepted non-MIP에서 immediate retime 없음
- Segment 위치와 1회 실행
- Stall growth/reset 후, densify 전 실행
- Same-iteration segment/stall 중복 평가 없음
- Deadline/end budget skip과 pending 보존
- Anchor pending의 extension 전달
- Started retime captured ids 소비
- Deferred best/error의 operator 비귀속
- MIP transactional retime 유지

```bash
PYTHONDONTWRITEBYTECODE=1 /opt/homebrew/Caskroom/miniforge/base/envs/ogc-2026/bin/python -m unittest \
  baseline.tests.test_alns \
  baseline.tests.test_alns_integration \
  baseline.tests.test_lns_telemetry
```

### 7.5 Benchmark와 PASS 조건

Focused tests가 PASS한 뒤 5.2절 명령을 `PHASE=2`로 실행한다. Objective, iterations, repair 지표, retime s/iter, retime triggers/iter를 Phase 1 PASS artifact 및 binding baseline과 비교해 instance별로 기록한다.

Phase 2 PASS 조건은 다음이다.

- Focused tests 전부 PASS
- 5개 benchmark artifact 모두 생성
- 5/5 correctness hard gate PASS
- Phase 1/baseline 대비 diagnostic 성능 표 작성
- Flag-off와 MIP transactional behavior 유지
- 허용 파일 밖 변경 없음
- Native C++/native adapter 변경 없음

성능 diagnostic 미달 자체는 Phase 2 FAIL 사유가 아니다. 다른 PASS 조건이 실패하면 같은 세션에서 추가 수정하지 않고 첫 직접 실패 원인과 관련 파일을 보고한 뒤 종료한다.

## 8. Phase 3 — Throughput telemetry와 final strict benchmark

### 8.1 진입 조건

- Phase 2 PASS 보고와 5개 benchmark artifact 존재
- Phase 1/2 변경이 current working tree에 유지됨
- Phase 1/2 PASS attempt 번호와 output root가 보고되어 있음

### 8.2 수정 범위

- `baseline/solver/alns.py`: raw invocation 합계 노출만
- `baseline/solver/runtime.py`: derived throughput record만
- `experiments/ogc_sage/verify_native_p7_fixed_time.py`: 새 throughput readback/record 검증만
- `baseline/tests/test_alns_integration.py`
- `baseline/tests/test_lns_telemetry.py`
- `baseline/tests/test_packaging.py`
- `baseline/tests/test_native_repair_integration.py`

Candidate 선택, acceptance, deferred retime trigger를 이 Phase에서 변경하지 않는다.

### 8.3 필수 구현

- 3.9 invocation throughput telemetry shape 전체
- Repair logical counter의 invocation별 합계
- `retime_new_best`, `retime_errors`, budget skip 합계
- Anchor/extension record 분리와 derived ratios
- Legacy `model_stats["lns"]` 유지

### 8.4 필수 tests와 명령

- `lns_invocations[*].throughput` anchor/extension 분리
- `outer_iterations == operator_attempts`
- Candidate logical counters의 invocation 합계
- Deferred retime counter가 operator counter와 분리됨
- Iterations 0에서 다섯 ratio `None`
- Harness run record에서 throughput readback 가능
- Native production config와 fallback/recheck 계약 유지

```bash
PYTHONDONTWRITEBYTECODE=1 /opt/homebrew/Caskroom/miniforge/base/envs/ogc-2026/bin/python -m unittest \
  baseline.tests.test_alns_integration \
  baseline.tests.test_lns_telemetry \
  baseline.tests.test_packaging \
  baseline.tests.test_native_repair_integration
```

### 8.5 Benchmark와 PASS 조건

Focused tests가 PASS한 뒤 5.2절 명령을 `PHASE=3`으로 실행한다. 5.3절 correctness와 5.4절 binding baseline strict AND gate를 적용한다.

Phase 3 PASS 조건은 다음이다.

- Focused tests 전부 PASS
- 5개 benchmark artifact 모두 생성
- 5/5 correctness hard gate PASS
- 5개 각각 objective strict 감소
- 5개 각각 outer iterations strict 증가
- 5개 각각 total repair, extension repair, retime s/iter strict 감소
- Equality/aggregate 상쇄 없음
- Production native defaults 유지
- 허용 파일 밖 변경 없음
- Native C++/native adapter 변경 없음

하나라도 실패하면 Phase 3 FAIL이며 완료 기준을 낮추지 않는다.

## 9. 실패·진단·수정·재검증 protocol

Phase worker에서 test, runtime, correctness 또는 해당 Phase의 hard gate 문제가 발생하면 worker는 같은 세션에서 반복 수정하지 않는다. 첫 직접 실패 증거를 보고하고 종료한다. Phase 1·2의 diagnostic 성능 미달은 hard gate가 아니므로 recovery를 시작하지 않는다.

Scheduler는 다음 세션을 반드시 순서대로 생성한다.

1. **DIAGNOSE 새 세션**: 코드 수정 없이 첫 직접 원인 하나를 확정한다.
2. **FIX 새 세션**: 확정 원인 하나만 최소 수정하고 해당 focused test를 실행한다.
3. **RETRY 새 세션**: 코드 수정 없이 실패한 Phase의 전체 PASS 조건을 다시 실행한다.

DIAGNOSE가 원인을 확정해야 FIX를 만들고, FIX의 focused test가 PASS해야 RETRY를 만든다. DIAGNOSE 또는 FIX가 FAIL이면 RETRY를 만들지 않고 다음 recovery attempt의 새 DIAGNOSE부터 다시 시작한다. RETRY PASS 전 다음 Phase를 시작하지 않는다. RETRY 실패도 다음 recovery attempt에서 DIAGNOSE → FIX → RETRY를 직렬로 반복한다.

어느 Phase든 benchmark correctness FAIL이면 DIAGNOSE는 기존 5개 `run.json`을 먼저 분석하며 benchmark를 재실행하지 않는다. Phase 3 strict 성능 FAIL도 동일하다. FIX로 source가 변경된 뒤에만 새 RETRY 세션이 해당 Phase의 새 attempt output root에서 5개를 다시 각각 1회 실행할 수 있다. 일부 instance만 선택 재실행하지 않는다.

외부 권한, dependency 설치, destructive operation이 필요할 때만 사용자에게 blocker와 필요한 승인을 보고한다. 성능 미달이나 test 실패만으로 완료 조건을 낮추지 않는다.

## 10. Master scheduler 계약

Scheduler는 구현하지 않는다. 다음 상태만 대화 안에서 관리하며 state file을 만들지 않는다.

```text
current_phase = 1 | 2 | 3 | COMPLETE
role = IMPLEMENT | DIAGNOSE | FIX | RETRY
attempt = positive integer
last_session = Codex task id | NONE
decision = PENDING | PASS | FAIL | BLOCKED
```

각 Phase의 최초 IMPLEMENT는 attempt 1이다. 실패하면 attempt를 1 증가시키고, 같은 recovery cycle의 DIAGNOSE/FIX/RETRY 세션은 같은 attempt 번호를 사용한다. DIAGNOSE, FIX 또는 RETRY가 실패하면 다음 cycle에서 다시 1 증가시킨다. 모든 Phase output root의 `<ATTEMPT>`도 이 번호와 같아야 한다.

Scheduler loop는 다음으로 고정한다.

```text
Phase 1 새 IMPLEMENT 세션 생성 -> 완료 대기 -> 감사
PASS면 Phase 2 새 IMPLEMENT 세션 생성 -> 완료 대기 -> 감사
PASS면 Phase 3 새 IMPLEMENT 세션 생성 -> 완료 대기 -> 감사
PASS면 COMPLETE

어느 Phase든 FAIL:
  attempt 증가
  새 DIAGNOSE 세션 -> 완료 대기 -> 원인 확정 PASS 확인
  새 FIX 세션 -> 완료 대기 -> focused test PASS 확인
  새 RETRY 세션 -> 완료 대기 -> 전체 Phase 감사
  PASS 전까지 같은 Phase 유지
```

한 worker가 끝나기 전에 다른 worker를 만들지 않는다. Scheduler task 자체는 source/test/doc edit, test, benchmark, commit, push를 하지 않는다.

Session title은 다음 형식을 사용한다.

```text
alns-throughput-p<PHASE>-implement-a<ATTEMPT>
alns-throughput-p<PHASE>-diagnose-a<ATTEMPT>
alns-throughput-p<PHASE>-fix-a<ATTEMPT>
alns-throughput-p<PHASE>-retry-a<ATTEMPT>
```

## 11. Worker 공통 prompt

Scheduler는 각 새 worker에 다음 공통 내용을 전달하고 해당 Phase 절을 지정한다.

```text
ALNS iteration 처리량 개선 Phase [PHASE]만 이번 세션에서 수행한다.

작업공간:
- /Users/brown/workspace/ogc/sol-native-implementation

실행 권위 문서:
- docs/implementation/sol/15_ALNS_ITERATION_THROUGHPUT_PHASED_RUNBOOK.md

설계 근거:
- docs/implementation/sol/14_ALNS_ITERATION_THROUGHPUT_REDESIGN.md

역할:
- [IMPLEMENT | DIAGNOSE | FIX | RETRY]
- attempt [ATTEMPT]

시작 전에 branch, HEAD, status, diff와 이전 Phase PASS 또는 실패 증거를 읽기 전용으로 확인한다.
실행 권위 문서의 공통 불변식, 확정 계약, Phase [PHASE] 절만 수행한다.
다음 Phase를 미리 구현하거나 실행하지 않는다.
새 branch/worktree, reset/restore/stash/clean, 승인 없는 commit/push를 금지한다.
기존 dirty/untracked 변경을 보호한다.
한 번에 하나의 command만 실행하고 benchmark를 병렬 실행하지 않는다.
문서에 없는 refactor/framework/추가 validation을 만들지 않는다.

완료 보고:
- 변경 파일
- 실행 명령
- test/benchmark 결과
- Phase PASS 조건별 PASS/FAIL
- 현재 git status/diff
- 첫 직접 실패 원인(FAIL 시)
- 다음 Phase 진입 가능 여부

Phase 결과를 보고한 뒤 중단한다. 같은 세션에서 다음 Phase를 시작하지 않는다.
```

DIAGNOSE는 코드를 수정하지 않는다. FIX는 확정 원인 하나만 수정한다. RETRY는 코드를 수정하지 않고 해당 Phase의 focused tests와 5개 benchmark 전체를 다시 실행한다.

## 12. 최종 완료 조건

전체 작업은 다음을 모두 만족할 때만 COMPLETE다.

- Phase 1 PASS
- Phase 2 PASS
- Phase 3에서 `prob_21`~`prob_25` 5/5 correctness PASS
- 5개 모두 objective, outer iterations, total repair/iter, extension repair/iter, retime/iter strict improvement
- Equality/aggregate 상쇄 없음
- Production native defaults 유지
- Native C++ 변경 없음
- Commit/push 없음

Scheduler는 COMPLETE 보고 후 새 세션을 만들지 않고 종료한다.
