# ALNS iteration 처리량 재설계

> 상태: DESIGN ONLY  
> 구현 기준: `codex/performance-optimization-plan`, solver source `49cea953f6040fa213408b79932f0951b3eb4efc`  
> 대상: 실제 destroy → repair → acceptance 외부 ALNS iteration  
> 선택 설계: repair-local bounded candidate reuse + lazy refresh, segment/stall deferred retiming + best-effort end

## 1. 결정 요약

ALNS outer iteration의 의미와 counter는 변경하지 않는다. 한 iteration은 지금과 같이 destroy operator 선택·실행, repair 한 번, feasibility/acceptance 처리 한 번으로 구성된다. `metric.attempts`와 loop 종료부의 `iterations`/`total_iterations` 증가 위치도 유지한다.

다만 flag-on repair의 candidate 선택 영역은 의도적으로 바뀐다. Regret 공식, canonical ordering, exact refresh/commit은 유지하지만, 두 번째 insertion round부터 regret은 현재 전체 생성공간이 아니라 **refresh된 repair-local bounded cache**를 대상으로 계산한다. 따라서 기존 repair trajectory나 full-space top-3 parity는 보장하지 않으며, 이 근사화는 `prob_21`~`prob_25` 각 instance의 Phase 4 native baseline을 모두 strict하게 개선해야 하는 gate로 통제한다.

처리량 개선은 다음 두 변경으로만 달성한다.

1. `heuristic_repair()` 한 번의 수명 안에서 block별 top-3 `CandidateScore`를 보관하고, 첫 insertion 뒤에는 전체 후보 공간을 다시 생성하지 않고 cached placement 최대 3개만 현재 state에서 재평가한다. 재평가 결과가 부족하거나 손상됐을 때만 해당 block 하나를 full regenerate한다.
2. accepted non-MIP repair가 만든 `changed_ids`는 중복 없는 pending set에 누적하고 즉시 retime하지 않는다. retime은 segment와 stall 경계에서 실행하며, search end에서는 non-deadline 종료에 한해 best-effort로 시도한다. MIP transaction의 필수 retime은 현행대로 즉시 수행한다.

이 두 변경은 하나의 explicit benchmark seam인 `alns_throughput_redesign_enabled`로 함께 활성화한다. 최초 구현의 기본값은 `False`다. 현재 production의 native repair 기본값은 철회하지 않는다. benchmark gate를 통과하기 전에는 production default 동작을 바꾸지 않는다.

## 2. 현재 source identity와 production defaults

요청된 설계 기준은 다음과 같다.

```text
branch = codex/performance-optimization-plan
solver baseline HEAD = 49cea953f6040fa213408b79932f0951b3eb4efc
commit = feat(native): promote guarded repair runtime
```

재검토 시 관찰한 worktree HEAD는 `2e5eca33fe59b68e646a62c70f6ab4a06a8e4574`(`move native ZIP guide to standalone doc`)다. 이 커밋은 `49cea95`의 후속이며, `49cea95..2e5eca3`에서 이 문서가 대상으로 삼는 다음 solver 파일에는 차이가 없다.

- `baseline/solver/alns.py`
- `baseline/solver/neighborhoods.py`
- `baseline/solver/construct.py`
- `baseline/solver/native_repair.py`
- `baseline/solver/runtime.py`
- `baseline/solver/entry.py`

따라서 설계의 solver source identity는 요청된 `49cea95`로 고정한다. Branch HEAD는 문서·packaging 작업으로 이동할 수 있으므로 후속 IMPLEMENT 시작 시 같은 solver-file diff 확인을 다시 수행한다. 관찰 HEAD `2e5eca3`을 clean `49cea95` benchmark로 간주하지 않는다.

`49cea95`의 release decision과 현재 source가 선언하는 production defaults는 다음과 같다.

```text
repair_backend = "native"
native_exact_mode = "native"
native prefilter = OFF
MIP = OFF
interlock = OFF
```

Native import/runtime 실패 시 Python reference fallback, native 반환 후보의 Python 재검증, rollback, transactional commit, serialization/full checker, frozen-anchor dominance guard는 유지한다.

## 3. Evidence provenance와 제한

이 문서가 사용하는 120초 수치는 다음 세 파일에서 읽었다.

- `/tmp/ogc-native-p3/implement/fixed-time/python/run.json`
- `/tmp/ogc-native-p3/implement/fixed-time/native/run.json`
- `/tmp/ogc-native-p3/implement/prob23-native-exact.json`

추가로 다음 Phase 4 validation 결과를 **binding improvement baseline**으로 사용한다.

- Codex task: `019f69fd-6a79-7a10-ab10-9ba3167d9f59` (`native-repair-p4-validate-a2`)
- Output root: `/tmp/ogc-native-p4/validate-a2`
- Native records: `/tmp/ogc-native-p4/validate-a2/prob_21/native/run.json`부터 `prob_25/native/run.json`까지 5개
- 실행 순서: `prob_21` → `prob_22` → `prob_23` → `prob_24` → `prob_25`
- 각 instance: seed `20260710`, 120초, native exact 1회, warmup/retry 없음
- Binary SHA-256: `d41ba45c454ace6750333e6a2c231be5aa6f5d05a1aa1e08aee923d6736355a5`

Phase 3와 Phase 4 fixed-time records의 source identity는 HEAD `302f9e8f0d3cd5eba8f68f633d1a2ba9013396aa`, dirty source다. 당시 기록된 production default는 Python이었고, native records는 `repair_backend="native"`, `native_exact_mode="native"`, prefilter/MIP/interlock OFF actual config를 사용했다. 이 native actual config는 현재 release default와 같지만, evidence 자체는 clean `49cea95`에서 생성되지 않았다.

따라서 `prob_23` Phase 3 수치는 병목 설명용 **reference baseline**, `prob_21`~`prob_25` Phase 4 native 수치는 후속 IMPLEMENT 판정용 **binding improvement baseline**으로 구분한다. 어느 쪽도 다음을 주장하지 않는다.

- clean `49cea95`의 재현 성능
- 재검토 시 관찰 HEAD `2e5eca3`의 측정 성능
- 여러 seed 또는 여러 instance에서의 일반화된 성능
- 200/1,000 iterations 달성 사실

이번 DESIGN 세션에서는 benchmark, test, build를 새로 실행하지 않았다.

## 4. 실제 outer iteration 정의

`run_lns()`의 외부 iteration은 다음 순서를 가진다.

1. weighted destroy operator 하나를 고르고 `metric.attempts`와 `segment_uses`를 1 증가시킨다.
2. 현재 snapshot에서 block 집합을 destroy한다.
3. repair engine을 정확히 한 번 호출한다.
4. repair 결과의 feasibility와 objective delta를 확인한다.
5. MIP transaction이면 필수 retime과 full checker를 transaction 안에서 수행한다.
6. acceptance rule을 한 번 적용하고 accepted current/best를 갱신한다.
7. iteration timing을 닫는다.
8. `iterations`와 `total_iterations`를 각각 1 증가시킨다.
9. cooling, segment update, stall 처리를 수행한다.

Anchor와 extension은 `AlnsSearchState`를 공유하므로 `total_iterations`는 연속된다. invocation별 `iterations`의 합이 전체 outer iterations다. Phase 3 reference에서 operator attempts 합과 iterations가 Python `52=52`, native `58=58`로 일치한다. 처리량 부족은 counter 오류가 아니다.

재설계 이후에도 다음 등식이 필수다.

```text
outer_iterations
  = sum(lns_invocations[*].iterations)
  = sum(lns_invocations[*].operator_stats[*].attempts)
  = operator_attempts
```

Candidate generation call, cached refresh, native cursor/batch, retime trigger를 iteration으로 세지 않는다.

## 5. Phase 3 timing evidence와 Phase 4 native binding baseline

조건은 `prob_23.json`, seed `20260710`, timelimit 120초, warmup/retry 없음이다.

| 항목 | Python reference | Native exact |
|---|---:|---:|
| Outer iterations | 52 | 58 |
| Operator attempts | 52 | 58 |
| Repair seconds | 86.9927 | 77.5031 |
| Repair seconds / iteration | 1.6729 | 1.3363 |
| Retime seconds | 19.3583 | 28.2620 |
| Retime seconds / iteration | 0.3723 | 0.4873 |
| Retime triggers | 17 | 24 |
| Retime triggers / iteration | 0.3269 | 0.4138 |
| Checker seconds | 0.7365 | 1.1335 |
| Final objective | 40,773,964 | 12,029,367 |
| Official checker | Stage 5, violations 0, parity | Stage 5, violations 0, parity |

Native exact repair 시간은 invocation별로 크게 다르다.

| Native invocation | Iterations | Repair seconds | Repair seconds / iteration |
|---|---:|---:|---:|
| Anchor | 39 | 28.6989 | 0.735868 |
| Extension | 19 | 48.8042 | 2.568644 |

Native extension의 마지막-invocation repair telemetry에는 163 native candidate-generation calls가 있다. 이는 `163 / 19 = 8.5789`, 약 8.6 calls/iteration이다. 이 수치는 전체 58 iterations의 합이 아니라 extension 19 iterations에 대한 값이다.

Native exact kernel 자체가 빨라졌어도 extension에서는 한 outer iteration이 초 단위다. 원인은 iteration 안에서 큰 candidate/pair 공간을 여러 번 다시 탐색하고, accepted repair 뒤 retime을 빈번하게 실행하는 outer orchestration에 있다.

### 5.1 Phase 4 native binding baseline

다음 값은 task `019f69fd-6a79-7a10-ab10-9ba3167d9f59`의 최종 GO 결과와 각 native `run.json`에서 확인한 exact baseline이다. 모든 record는 Stage 5, violations 0, internal/checker objective parity를 충족했다.

| Instance | Native objective `(Z1/Z2/Z3)` | Outer iterations `(anchor/extension)` | Repair s/iteration | Extension repair s/iteration | Retime s/iteration |
|---|---:|---:|---:|---:|---:|
| `prob_21` | 17,308,584 `(1258/32/3569)` | 66 `(41/25)` | 1.1295814367256367 | 2.0150268181599675 | 0.4291875505445977 |
| `prob_22` | 1,594,867 `(28/2381/3036)` | 184 `(79/105)` | 0.4481704336193734 | 0.5321131372973988 | 0.13154786231019028 |
| `prob_23` | 15,305,668 `(1112/980/1106)` | 54 `(34/20)` | 1.4491142754591744 | 2.4545250666444190 | 0.5067918040364964 |
| `prob_24` | 3,055,604 `(188/1020/1813)` | 78 `(38/40)` | 1.0563540379083953 | 1.3774584874481661 | 0.2567642868462150 |
| `prob_25` | 1,851,982 `(2707/293/2306)` | 72 `(36/36)` | 1.1901187633185246 | 1.4031495127532656 | 0.2864239149178805 |

표의 full-precision 값이 비교 권위다. 표시용 반올림값이나 5개 aggregate/median으로 판정하지 않는다.

### 5.2 Strict per-instance improvement 계약

Flag-on 후속 결과는 위 5개 baseline보다 **각 instance에서 반드시 좋아야 한다**. `prob_21`부터 `prob_25`까지 모든 instance가 다음 다섯 조건을 동시에 충족해야 한다.

1. Final objective가 해당 baseline보다 strict하게 작다.
2. Outer iterations가 해당 baseline보다 strict하게 크다.
3. Total repair seconds/iteration이 해당 baseline보다 strict하게 작다.
4. Extension repair seconds/iteration이 해당 baseline보다 strict하게 작다.
5. Retime seconds/iteration이 해당 baseline보다 strict하게 작다.

동률은 improvement가 아니므로 실패다. 한 instance의 실패를 다른 instance의 큰 개선, 합계, 평균, median으로 상쇄하지 않는다. 5개 중 하나라도 다섯 조건 중 하나를 충족하지 못하면 baseline improvement gate는 `NO-GO`다. Correctness hard gate 실패도 성능과 무관하게 `NO-GO`다.

## 6. 반복 candidate generation 병목

현재 `heuristic_repair()`는 production legacy policy에서 통상 destroy size 4를 repair한다. `remaining`이 빌 때까지 반복하며, 매 insertion round마다 `sorted(remaining)`의 모든 block에 `generate_insertion_candidates()`를 다시 호출한다.

Destroy size가 4이면 full generation 호출 상한은 다음과 같다.

```text
round 1: 4 blocks
round 2: 3 blocks
round 3: 2 blocks
round 4: 1 block
total : 4 + 3 + 2 + 1 = 10 calls/repair
```

각 호출은 Python 또는 native existing generation path를 통해 bounded top-3만 반환하지만, 그 top-3를 찾기 위해 candidate rows와 exact candidate/pair 공간을 다시 탐색한다. Native session은 한 repair 동안 static/packed 자원을 재사용해도, state version이 바뀔 때 새 candidate traversal을 수행한다.

Phase 3 native extension의 약 8.6 calls/iteration은 이 구조와 일치한다. 최대 10보다 낮은 이유는 deadline, empty/failure, 실제 호출 종료 조건이 섞일 수 있기 때문이다. 이는 counter를 줄여 해결할 문제가 아니라 한 repair 내부의 중복 full generation을 줄여야 하는 문제다.

### 6.1 모든 cached `CandidateScore`가 stale할 수 있는 이유

하나의 placement를 transactional commit하면 draft state가 바뀐다. 남은 block의 기존 `CandidateScore`는 다음 모든 면에서 stale할 수 있다.

- 새 placement와의 exact feasibility
- bay assignment delta
- bay interval fragmentation
- total/tardiness/assignment delta 조합
- guide penalty가 포함된 canonical tie
- `state_version`

따라서 과거 score를 그대로 regret comparison에 쓰거나 commit해서는 안 된다. 이 설계에서 재사용하는 것은 `CandidateScore`의 점수가 아니라 **최대 3개의 placement 열**이다. 모든 재사용 placement는 현재 state에서 `evaluate_insert()`와 같은 exact boundary를 통과해 새 `CandidateScore`로 재구성된다.

기존 `commit_insertion_candidate()`의 commit 직전 재평가와 `_exact_union_safe()` transactional guard도 그대로 유지한다. Lazy refresh는 commit guard를 대체하지 않는다.

## 7. Immediate retiming 병목

현재 accepted non-MIP 경로는 다음 동작을 한다.

```text
pending_retime.update(repaired.changed_ids)
apply_pending_retime(metric, operator_index)
```

`apply_pending_retime()`는 bay별 pending block 수 threshold를 확인하지만 accepted iteration 직후마다 호출된다. Phase 3 native reference에서는 58 iterations 중 24회 retime이 시작됐고 28.2620초를 사용했다. `pending`이라는 이름과 달리 pending set은 대부분 즉시 소비된다.

현재 stall 경계의 forced retime은 이미 존재하지만 segment 경계와 search-end flush는 없다. 재설계는 non-MIP retime을 명시적 경계 작업으로 바꾼다. Acceptance/cooling 공식과 destroy operator 구현은 바꾸지 않는다. 다만 bounded-cache candidate와 deferred retime의 operator 비귀속 때문에 flag-on search trajectory와 이후 weight 값은 flag-off와 달라질 수 있다.

## 8. 선택한 최소 설계

선택한 설계는 두 부분을 하나의 feature seam 아래 묶는다.

```text
alns_throughput_redesign_enabled = False  # 최초 구현 기본값
```

- `False`: 현재 repair regeneration과 immediate threshold retime 동작을 그대로 유지한다.
- `True`: repair-local bounded cache/lazy refresh와 deferred retime cadence를 함께 사용한다.

이 flag는 유일한 새 public setting이다. Candidate count 3, segment 50, retime start margin 같은 값은 기존 계약 또는 module-level implementation constant로 고정하며 새 knob로 만들지 않는다. 최초 IMPLEMENT 검증은 internal `solve(..., config=SubmissionConfig(...))` benchmark seam에서만 flag를 `True`로 설정한다.

Production release decision은 이 문서의 범위가 아니다. Flag가 `False`여도 다음 production defaults는 그대로다.

```text
repair_backend = "native"
native_exact_mode = "native"
native_prefilter_enabled = False
mip_enabled = False
interlock_enabled = False
```

## 9. Repair-local candidate cache 계약

### 9.1 Ownership와 수명

Cache owner는 `heuristic_repair()`의 local stack이다. 한 `heuristic_repair()` 반환과 함께 폐기한다.

금지한다.

- ALNS iteration 간 공유
- anchor/extension invocation 간 공유
- repair session 간 공유
- process/global cache
- 전체 candidate row 또는 candidate/pair dump 보관

`NativeRepairSession`의 현행 repair-local 수명은 유지한다. Candidate cache는 session이 반환한 top-3를 소비할 뿐 native API나 C++ ownership을 확장하지 않는다.

### 9.2 최소 entry

`neighborhoods.py`에 private repair-local entry를 둔다.

```python
@dataclass(frozen=True, slots=True)
class _RepairCandidateCacheEntry:
    source_version: int
    candidates: tuple[CandidateScore, ...]  # 0..3, 정상 저장 시 1..3
    rollback_placement: Placement
```

Dictionary key가 block id이므로 entry에 block id를 중복 저장하지 않는다. `candidates`에는 score 기준 최대 3개만 둔다. `rollback_placement`는 destroy 전 current snapshot의 동일 block placement다.

정상 cache invariant는 다음과 같다.

- 모든 candidate placement의 block id가 dictionary key와 같다.
- candidate placement는 중복되지 않는다.
- 모든 candidate `state_version == source_version`이다.
- `source_version <= state.version`이다.
- tuple은 현재 canonical ordering으로 정렬돼 있다.
- initial/full regeneration 직후에는 가능한 경우 rollback placement가 한 열로 포함된다.

### 9.3 최초 round

최초 round에서만 destroy된 모든 block에 기존 `generate_insertion_candidates()`를 한 번씩 호출한다.

1. `sorted(destroyed)` 순서로 existing Python/native path를 호출한다.
2. 반환값이 malformed인지 검증한다.
3. 모든 반환 candidate의 repair canonical tie를 정규화한다.
4. 최대 3개와 현재 `state.version`을 cache에 저장한다.
5. 빈 결과가 있으면 현행처럼 repair를 폐기하고 input snapshot identity를 반환한다.
6. 최초 round의 full-generation 결과를 대상으로 현행 regret 공식으로 첫 block을 선택한다.
7. 기존 `commit_insertion_candidate()`로 transactional commit한다.

Destroy size 4의 정상 fast path는 이 단계의 4 full generations로 시작한다.

## 10. Lazy refresh와 invalidation 규칙

첫 commit 이후 각 round에서 남은 block마다 다음 절차를 수행한다.

1. Cache metadata invariant를 검사한다.
2. Cached placement 최대 3개 각각을 현재 state에서 refresh한다.
3. Refresh된 non-`None` score만 모은다.
4. 중복 placement를 제거하고 canonical ordering으로 정렬한다.
5. Regeneration condition이 없으면 entry의 `source_version`과 모든 score version을 현재 `state.version`으로 교체한다.
6. Refresh된 score만 regret comparison과 commit 후보로 사용한다.

`construct.py`에 다음 최소 helper를 추가한다.

```python
def refresh_insertion_candidate(
    state: IndexedSolutionState,
    candidate: CandidateScore,
    kernel: GeometryKernel,
    *,
    rollback_placement: Placement,
) -> CandidateScore | None:
    ...
```

Helper는 `candidate.placement`만 재사용하고 `evaluate_insert(state, placement, kernel)`을 호출한다. 반환 score의 다음 값을 전부 현재 state에서 다시 계산한다.

- exact feasibility
- `total_delta`
- `tardiness_delta`
- `assignment_delta`
- `fragmentation`
- `canonical_tie`
- `state_version`

`evaluate_insert()`가 `None`이면 placement는 invalidated다. 예외, non-finite score, 잘못된 block id/version/tie는 malformed refresh로 처리한다.

Expected stale와 corrupt mismatch를 구분한다.

- `entry.source_version < state.version`: commit 후 정상적으로 예상되는 stale 상태다. Lazy refresh한다.
- candidate들의 version이 `entry.source_version`과 다름, version이 섞임, 또는 `entry.source_version > state.version`: 명시적 state-version mismatch다. Cached score를 쓰지 않고 block-local full regeneration한다.
- 과거 state version의 score를 직접 비교하거나 commit하는 경로는 허용하지 않는다.

### 10.1 Bounded-cache 선택 영역

Lazy refresh는 cached placement의 현재 feasibility와 score를 정확히 다시 계산하지만, cache 밖 placement를 탐색하지 않는다. State insertion은 exact feasibility를 더 제한할 뿐이지만, `assignment_delta`는 현재 normalized bay loads에 의존하고 fragmentation도 현재 interval 구성에 의존한다. 따라서 최초 round에서 4위 이하였던 미보관 placement가 이후 state에서 더 좋은 candidate가 될 수 있으며, cached top-3가 모두 valid한 경우 이 변화만으로는 block-local regeneration이 발생하지 않는다.

이는 알려진 의도적 근사화다. Flag-on 경로가 보존하는 것은 다음으로 한정한다.

- refresh된 bounded set 안의 regret 공식과 canonical ordering
- 현재 state 기준 exact feasibility와 score
- rollback availability와 transactional commit safety
- 외부 destroy → repair → acceptance iteration 의미

Flag-on 경로는 full-space top-3, flag-off repair trajectory, 동일 seed의 candidate parity를 보장하지 않는다. 이를 보장하려면 remaining block의 전체 후보공간을 매 round 다시 생성해야 하므로 본 처리량 목표와 양립하지 않는다. 허용 여부는 18절의 5-instance correctness 및 strict objective/throughput improvement gate로만 판정한다.

## 11. Block-local full regeneration

Lazy refresh 중 다음 중 하나가 발생하면 **해당 block 하나만** existing full generation path로 다시 계산한다.

- cached candidate가 모두 invalid
- refresh 후 candidate 수가 `regret_depth`보다 적음
- cached rollback placement가 현재 state에서 invalid
- refresh 중 exception 또는 malformed result
- cache metadata의 명시적 state-version mismatch

Regeneration 절차는 다음으로 고정한다.

1. 해당 block에만 `generate_insertion_candidates()`를 호출한다.
2. 동일한 `NativeRepairSession`, current state, original rollback placement를 전달한다.
3. 반환 candidate를 검증하고 repair canonical tie를 정규화한다.
4. 최대 3개와 현재 version으로 해당 entry만 교체한다.
5. 다른 remaining block의 cache는 일괄 regenerate하지 않는다.

Regeneration 결과가 non-empty지만 `regret_depth`보다 적으면 추가 재생성 loop를 만들지 않는다. 아래 12절의 기존 `math.inf` 규칙으로 한 번만 처리한다.

다음은 regeneration 실패다.

- 빈 candidate set
- budget/deadline으로 generation을 안전하게 시작/완료하지 못함
- generation exception
- malformed candidate
- 현재 state와 다른 version 반환
- transactional commit 실패

실패 시 repair 전체를 폐기하고 `heuristic_repair()` 입력 snapshot과 동일한 object identity를 반환한다. Partial draft는 외부로 노출하지 않는다.

정상 destroy size 4 fast path의 full generation calls는 10에서 4로 줄어든다. 추가 호출은 block-local invalidation이 실제 발생한 경우에만 생긴다.

## 12. Bounded-cache regret, rollback, canonical tie 의미론

### 12.1 Canonical tie

Repair에서 initial generation, refresh, regeneration, rollback은 모두 다음 동일한 10-field ordering을 사용한다.

```text
(
  total_delta,
  fragmentation,
  guide_penalty,
  exit,
  entry,
  bay_id,
  orient_idx,
  x,
  y,
  block_id,
)
```

Repair에는 assignment seed guide가 없으므로 guide는 destroy 전 original placement로 고정한다.

```text
guide_penalty = 0 if candidate.bay_id == rollback_placement.bay_id else 1
```

Rollback candidate도 예외 없이 같은 tuple을 사용한다. Rollback placement는 original bay에 있으므로 유효할 때 guide penalty는 0이다. Rollback이라는 이유로 별도 우선순위를 주지는 않는다. 강제 포함은 availability 계약이고, 선택 순서는 동일한 canonical score 계약을 따른다.

Initial Python/native path에서 반환된 rollback score가 guide-penalty field 없는 `evaluate_insert()` tie를 가지고 있더라도 repair cache에 넣기 전에 위 tuple로 정규화한다. Native C++ API는 변경하지 않는다.

### 12.2 Regret depth

각 block의 refresh/regeneration 결과는 canonical tie로 정렬한다. **선택식**은 현재 `heuristic_repair()`와 동일하지만, flag-on 두 번째 round부터 **선택 영역**은 refresh된 bounded cache다.

```text
if len(ranked) < regret_depth:
    regret = math.inf
else:
    regret = ranked[regret_depth - 1].total_delta - ranked[0].total_delta
```

Refresh 결과가 depth보다 작으면 먼저 block-local full regeneration을 정확히 한 번 시도한다. Full regeneration 후에도 non-empty 후보 수가 depth보다 작을 때만 기존 `math.inf` shortage 의미를 적용한다. Cached 후보가 depth를 만족하면 미보관 후보의 잠재적 순위 변화만을 이유로 regenerate하지 않는다.

### 12.3 Commit

Regret winner의 `ranked[0]`만 commit 대상으로 삼는다. Commit 직전에 현행 `commit_insertion_candidate()`가 다시 `evaluate_insert()`하고 `_exact_union_safe()` transactional guard를 통과해야 한다. 실패하면 다른 cached candidate로 즉석 retry하지 않고 repair를 폐기한다. 이는 현행 stale-candidate failure semantics와 input identity 안전성을 유지한다.

## 13. Deferred retiming cadence

### 13.1 Accepted non-MIP path

Flag가 켜진 accepted non-MIP path는 다음만 수행한다.

```python
pending_retime.update(repaired.changed_ids)
```

여기서 `apply_pending_retime()`를 호출하지 않는다. Set이므로 같은 block id는 한 번만 유지된다. Rejected, infeasible repair의 ids는 추가하지 않는다.

### 13.2 실행 경계와 순서

Pending retime은 다음 세 경계에서만 평가한다.

1. **Segment boundary**: iteration 증가와 cooling 뒤, weight update 전에 실행한다. Deferred retime은 operator reward를 만들지 않으므로 weight update는 해당 segment의 repair 결과만 사용한다. Retime이 new best를 설치하면 `iterations_since_best`를 0으로 만든 뒤 stall 여부를 계산한다.
2. **Stall boundary**: 현행 forced-retime 위치를 유지한다. 즉 stall event 기록, destroy-size growth, stalled-engine/temperature/`iterations_since_best` 처리 뒤, densify 전에 실행한다.
3. **Search end**: loop 종료 뒤 metrics와 `AlnsSearchState`를 freeze하기 전에 best-effort로 평가한다. 정상 deadline-led 종료에서는 `search_remaining()`이 이미 소진되므로 skip이 예상되는 정상 동작이다. End retime을 위해 다음 outer iteration 시작을 조기에 중단하거나 시간을 별도 선예약하지 않는다.

Iteration-local `deferred_retime_boundary_checked`를 두어 같은 iteration에 boundary retime을 한 번만 평가한다. Segment가 우선이며, segment에서 pending이 소비됐거나 budget skip된 경우 같은 iteration의 stall에서 즉시 재시도하지 않는다. Segment retime이 new best를 설치하면 stall 판정 자체가 해소될 수 있다. Segment가 skip되고 stall 조건이 유지되면 기존 stall growth/reset/densify 흐름은 그대로 수행하되 retime만 재시도하지 않는다.

Trigger 문자열은 `segment`, `stall`, `end`, 현행 MIP용 `mip_transaction`으로 고정한다. Flag가 꺼진 legacy path의 `threshold`/`forced` telemetry는 유지한다.

### 13.3 Budget gate

새 public 설정을 늘리지 않고 `alns.py` module constant를 사용한다.

```text
DEFERRED_RETIME_START_MARGIN_SECONDS = 3.0
required_margin = max(
    DEFERRED_RETIME_START_MARGIN_SECONDS,
    budget.checker_p95 + config.checker_margin,
)
```

Boundary에서 `budget.can_start(0.0, margin=required_margin)`가 false면 retime을 시작하지 않는다.

- `deferred_retime_skips_due_to_budget`를 1 증가시킨다.
- pending set은 지우지 않는다.
- validated incumbent를 그대로 유지한다.
- Deadline-led search end에서는 skip을 정상으로 기록하고 그대로 metrics/state를 freeze한다.
- Anchor end skip이면 pending set이 continuation state를 통해 extension으로 전달될 수 있다.
- Final extension end skip이면 pending set이 state에 남더라도 이미 validated된 incumbent를 반환한다.

3초는 tuning knob나 runtime 예약량이 아니라 이번 최소 설계의 고정 start guard다. 실제 retimer의 child cap은 현행대로 `min(RetimingConfig.time_cap_s, 0.05 * budget.search_remaining())`이므로, 3초 gate를 막 통과한 호출의 retime cap은 최대 0.15초다. 이 gate는 유용한 solve time을 보장하지 않으며 deadline 근처의 새 작업 시작만 억제한다. Retimer 자체의 budget handling과 checker evidence 계약은 유지한다.

### 13.4 Started retime의 소비 규칙

Budget gate를 통과해 retime을 시작하면 시작 시점의 pending ids를 immutable `frozenset`으로 캡처한다. 실행 중 새 ids가 추가되는 동시 경로는 없지만 입력 안정성을 명시한다.

Started retime은 성공, no-improvement, invalid/worse result, status failure, exception과 무관하게 해당 captured batch를 한 번 소비하고 pending에서 제거한다. 동일한 실패 batch를 다음 경계에서 반복 실행하지 않는다. Budget gate 때문에 시작하지 않은 batch만 pending에 남긴다.

Retime 결과는 다음 조건을 모두 만족할 때만 current/best 후보가 된다.

- `SolutionSnapshot`
- local feasibility
- objective가 pre-retime current보다 악화되지 않음
- best 갱신 시 canonical checker evidence 또는 기존 full validation 통과

실패, exception, worse/invalid result는 current와 incumbent를 바꾸지 않는다. Deferred retime은 여러 accepted repair의 ids를 합칠 수 있으므로 boundary를 닫은 destroy operator에 귀속하지 않는다.

- Deferred retime은 per-operator `accepted`, `new_best`, `exceptions`와 `segment_scores`를 변경하지 않는다.
- Valid retime 결과는 current/accepted trace를 갱신할 수 있다.
- Strict best installation이면 best trace와 `iterations_since_best`를 갱신하고 `retime_new_best`를 1 증가시킨다.
- Exception이면 `retime_errors`를 1 증가시키며 operator exception으로 세지 않는다.
- MIP transactional retime만 해당 MIP operator transaction의 기존 attribution을 유지한다.

### 13.5 MIP transaction

`repaired.engine.startswith("mip")` transaction 안의 retime은 deferred cadence의 적용 대상이 아니다. Changed ids가 있으면 현행처럼 candidate acceptance 전에 즉시 retime하고, invalid/error/worse retime이면 transaction 전체를 실패시킨다. MIP default는 계속 OFF다.

## 14. Deadline, error, incumbent 안전성

다음 안전 계약은 flag 상태와 무관하게 유지한다.

- Solver input과 checker input snapshot은 mutate하지 않는다.
- Repair draft는 local이며 성공 전 외부로 노출하지 않는다.
- Cached score가 아닌 cached placement만 재사용한다.
- 과거 state version score를 regret/commit에 사용하지 않는다.
- Refresh 실패는 block-local regeneration으로 격리한다.
- Regeneration/commit 실패는 repair 폐기와 incumbent identity로 종료한다.
- Deadline/exception은 이미 validated된 incumbent를 손상시키지 않는다.
- Exact feasibility를 prefilter 추정으로 완화하지 않는다.
- Native returned candidate Python recheck를 유지한다.
- Native import/runtime/invalid output은 기존 Python fallback 계약을 유지한다.
- Native fallback도 deadline 이후 새 Python full repair를 시작하지 않는다.
- Official checker Stage 5, violations 0, objective parity가 best installation의 최종 권한이다.
- Retime 실패는 pre-retime current와 incumbent를 유지한다.

## 15. 파일별 구현 계획

### 15.1 `baseline/solver/neighborhoods.py`

- `NeighborhoodContext`에 internal propagated boolean `alns_throughput_redesign_enabled`를 추가한다.
- `_RepairCandidateCacheEntry`를 private type으로 추가한다.
- `heuristic_repair()`의 flag-on 경로에 initial full generation, lazy refresh, block-local regeneration 흐름을 구현한다.
- Cache는 함수 local dictionary로만 소유한다.
- Existing `NativeRepairSession` 하나를 repair 전체에서 계속 재사용한다.
- Logical generation/refresh/regeneration/rollback telemetry를 `RepairResult.telemetry`에 합친다.
- Flag-off 경로는 현행 loop를 보존한다.

### 15.2 `baseline/solver/construct.py`

- `refresh_insertion_candidate()` 최소 helper를 추가한다.
- `evaluate_insert()`를 exact score authority로 재사용한다.
- Repair guide penalty와 10-field canonical tie를 재구성하는 작은 helper를 공유한다.
- Initial/regenerated rollback score도 같은 canonical tie로 정규화할 수 있게 한다.
- `commit_insertion_candidate()`와 `_exact_union_safe()` 경계는 변경하지 않는다.
- Candidate generator의 top-3 bound와 Python/native selection은 변경하지 않는다.

### 15.3 `baseline/solver/alns.py`

- `AlnsContext`에 internal propagated flag를 추가하고 `.neighborhood`로 전달한다.
- Flag-on accepted non-MIP path에서 immediate `apply_pending_retime()`를 제거한다.
- Segment/stall/best-effort-end boundary dispatcher와 iteration-local 중복 평가 guard를 추가한다.
- Stall retime은 현행과 같은 destroy-growth/reset 후, densify 전 위치를 유지한다.
- 3초 고정 start margin과 budget skip count를 추가한다.
- Deferred retime 결과는 destroy operator reward/exception에 귀속하지 않고 `retime_new_best`/`retime_errors`로 분리한다.
- MIP transactional retime은 변경하지 않는다.
- `iterations`, `total_iterations`, operator attempts 증가 위치를 변경하지 않는다.
- Per-invocation throughput counters를 `AlnsMetrics`의 bounded scalar bundle로 노출한다.

### 15.4 `baseline/solver/runtime.py`

- 유일한 새 public setting `SubmissionConfig.alns_throughput_redesign_enabled: bool = False`를 추가하고 type validation/as_dict에 포함한다.
- `RunTrace.add_lns_invocation()` record에 invocation별 throughput scalar bundle, operator attempts/accepted/new-best 합, 별도 `retime_new_best`/`retime_errors`를 추가한다.
- `model_stats["lns"]`의 legacy last-call 동작은 유지한다.
- `lns_invocations`를 전체 합계의 authoritative source로 유지한다.
- Telemetry aggregation framework를 새 schema/framework로 전면 교체하지 않는다.

### 15.5 `baseline/solver/entry.py`

- `SubmissionConfig`의 flag를 생성되는 `AlnsContext`와 `NeighborhoodContext` 경로로 전달한다.
- Anchor/extension이 같은 feature mode와 continuation state를 사용하게 한다.
- Production native default, retime enablement, MIP/interlock defaults는 변경하지 않는다.

### 15.6 `baseline/solver/native_repair.py`

변경하지 않는다. Existing top-3 `CandidateScore`, repair-local `NativeRepairSession`, state-version validation, native returned-candidate Python recheck를 그대로 소비한다. C++ API 확장은 없다.

### 15.7 `native/ogc_native`

변경 범위에서 제외한다.

## 16. 최소 telemetry

Telemetry는 동작을 제어하지 않는다. Repair event, `AlnsMetrics`, `RunTrace.lns_invocations`에 bounded scalar를 추가하는 수준으로 제한한다.

| Field | 정의 |
|---|---|
| `outer_iterations` | invocation의 실제 loop 완료 수. 전체는 `lns_invocations` 합 |
| `operator_attempts` | per-operator attempts 합. `outer_iterations`와 같아야 함 |
| `destroy_size` | iteration의 실제 destroyed block 수 또는 기존 size histogram |
| `candidate_generation_calls` | initial full generation + block-local full regeneration 논리 호출 수. Refresh는 제외 |
| `candidate_generation_calls_per_iteration` | invocation generation calls / invocation outer iterations. Anchor/extension/전체를 별도 보고 |
| `initial_full_generations` | 최초 round에서 실행한 block별 full generation 수 |
| `block_local_regenerations` | Lazy refresh 후 해당 block만 full regenerate한 수 |
| `refreshed_candidates` | 현재 state에서 refresh를 시도한 cached placements 수 |
| `invalidated_candidates` | Refresh가 `None` 또는 exact-invalid로 판정한 수 |
| `reusable_candidates` | Refresh 후 현재 state score로 재구성되어 regret에 사용 가능한 수 |
| `rollback_refresh_failures` | Cached rollback placement refresh가 invalid/exception인 수 |
| `retime_pending_ids` | 각 boundary retime event가 관찰한 unique pending id 수 |
| `retime_triggers` | 실제 retime hook 시작 수. Budget skip은 제외 |
| `deferred_retime_skips_due_to_budget` | Pending이 있지만 boundary budget gate로 시작하지 않은 수 |
| `retime_new_best` | Deferred retime이 strict best를 설치한 수. Operator `new_best`와 분리 |
| `retime_errors` | Deferred retime exception 수. Operator `exceptions`와 분리 |
| `repair_seconds_per_iteration` | invocation repair seconds / iterations |
| `retime_seconds_per_iteration` | invocation retime seconds / iterations |
| `checker_seconds_per_iteration` | invocation checker seconds / iterations |
| `wall_seconds_per_iteration` | invocation elapsed seconds / iterations |
| `accepted` | per-operator accepted 합 |
| `new_best` | per-operator repair/MIP new-best 합. Deferred retime best는 제외 |
| deadline/error/fallback counts | repair events의 native deadline/error/fallback 및 repair/deferred-retime error 합 |

`candidate_generation_calls`는 backend-independent logical call count다. Existing native telemetry의 `native_calls`는 실제 native adapter call 수로 남겨 두고 parity 진단에 사용한다. 두 값이 항상 같다고 가정하지 않는다. Python fallback, deadline 전환, malformed output이 차이를 만들 수 있다.

### 16.1 Aggregation 범위 주의

현재 `RunTrace.model_stats["lns"]`는 `add_lns_invocation()`마다 마지막 `AlnsMetrics` object로 덮어쓴다. `verify_native_p7_fixed_time.py`의 `aggregate_repair_telemetry()`도 이 legacy field의 `repair_events`를 읽으므로 native reference의 163 calls/19 events는 extension invocation만 나타낸다.

전체 timing과 iteration 합은 이미 `lns_invocations`의 anchor와 extension을 합산해야 한다. 새 throughput scalar도 invocation record에 직접 포함하고 다음처럼 합산한다.

```text
total(field) = sum(invocation.throughput[field] for invocation in lns_invocations)
ratio(field) = total(field) / sum(invocation.iterations)
```

단, Phase 3의 `8.6 candidate-generation calls/iteration`은 마지막 extension invocation만의 baseline이다. 해당 성능 gate는 새 run의 **extension invocation ratio와 extension-to-extension으로만 비교**한다. Anchor와 전체 ratio는 함께 보고하지만 8.6 hard comparison에는 사용하지 않는다.

이번 재설계에서 generic telemetry aggregation framework나 legacy `model_stats` 구조를 제거하지 않는다.

## 17. 후속 IMPLEMENT 최소 검증

이번 세션에서는 아래 검증을 실행하지 않는다. 후속 IMPLEMENT 세션은 Phase 4 native baseline과 동일한 데이터·seed·시간 조건에서 flag-on native 결과만 정확히 한 번씩 측정한다.

```text
instances = prob_21.json, prob_22.json, prob_23.json, prob_24.json, prob_25.json
order = prob_21 -> prob_22 -> prob_23 -> prob_24 -> prob_25
seed = 20260710 only
timelimit = 120 seconds per instance
variant = native exact with alns_throughput_redesign_enabled=True
warmup = none
retry = none
official checker = once per instance, five total
```

금지한다.

- `prob_21`~`prob_25` 외 다른 instance
- 다중 seed
- all-40
- hard-10
- 전체 baseline test suite
- benchmark warmup/retry
- Phase 4 baseline 또는 paired Python 재실행
- 실패 instance 선택적 재실행

### 17.1 Affected focused unit tests

필수 focused tests만 실행한다.

1. Cached candidate refresh가 현재 `state_version`과 현재 score/tie를 생성한다.
2. 정상적인 과거 version은 lazy refresh하고 corrupt/mixed version만 block-local regenerate한다.
3. 새 placement와 blocked된 cached candidate가 invalidated된다.
4. Invalid/insufficient cache가 해당 block 하나만 regenerate한다.
5. Initial/full regeneration에서 rollback column이 보존되고 동일 canonical tie를 사용한다.
6. Regeneration 후에도 depth 미만이면 기존 `math.inf` shortage 의미가 적용된다.
7. Cached top-3가 모두 valid이면 cache 밖 candidate의 순위 변화만으로 regenerate하지 않는 bounded-cache 의미가 고정된다.
8. Flag-off 경로는 현행 per-round full generation 동작을 유지한다.
9. Accepted non-MIP repair가 즉시 retime하지 않고 segment/stall/best-effort-end에서만 실행한다.
10. Stall retime은 destroy-size growth/reset 후, densify 전에 실행한다.
11. Segment와 stall이 겹쳐도 boundary retime은 한 번만 평가한다.
12. Deferred retime best/error가 closing operator의 reward/exception을 바꾸지 않는다.
13. Budget margin 부족 및 deadline-led end에서 retime을 시작하지 않고 pending과 incumbent를 보존한다.
14. Anchor end skip의 pending ids가 extension state로 전달된다.
15. Refresh/regeneration/retime exception 시 input 또는 validated incumbent identity가 보존된다.
16. `lns_invocations`가 anchor/extension throughput을 분리하며 extension ratio를 독립 계산할 수 있다.
17. Iterations와 operator attempts가 계속 일치한다.

### 17.2 Native focused integration test

Native production config와 flag-on에서 다음을 확인한다.

- Existing `NativeRepairSession` 재사용
- native returned-candidate Python recheck 유지
- state-version mismatch 폐기
- fallback 동작 유지
- Stage 5, violations 0, objective/checker parity
- deadline 이후 새 Python full repair/reference 0

### 17.3 5개 fixed-time native run

`verify_native_p7_fixed_time.py`의 validation-only config seam에서 다음 actual config로 `prob_21`부터 `prob_25`까지 순서대로 각각 120초를 정확히 한 번 실행한다. Python variant는 다시 실행하지 않고 5.1절의 frozen Phase 4 native records와 비교한다.

```text
repair_backend = "native"
native_exact_mode = "native"
native_prefilter_enabled = False
mip_enabled = False
interlock_enabled = False
alns_throughput_redesign_enabled = True
```

각 run 뒤 해당 solution에 official checker를 정확히 한 번 실행한다. 총 benchmark는 native 5회, official checker는 5회다. 특정 instance가 실패해도 retry하거나 parameter를 바꾸지 않으며, runtime 자체가 불가능한 경우를 제외하고 나머지 instance를 계속 측정해 5개 결과를 모두 보고한다.

## 18. 완료 조건

### 18.1 필수 correctness gate

다음은 `prob_21`~`prob_25` 5개 모두에 적용하는 hard gate다.

- `outer_iterations == operator_attempts`
- Official checker Stage 5
- violations 0
- internal objective와 checker objective parity
- native exception 0
- returned-candidate recheck failure 0
- GEOS error 0
- deadline 이후 새 Python full repair/reference 0
- refresh/regeneration/retime failure에서 incumbent identity 보존

한 instance라도 correctness 항목을 위반하면 strict improvement 수치와 무관하게 `NO-GO`다.

### 18.2 필수 Phase 4 native baseline improvement gate

5.1절의 동일 instance exact baseline과 full-precision으로 비교한다.

| Instance | Required objective | Required outer iterations | Required total repair s/iter | Required extension repair s/iter | Required retime s/iter |
|---|---:|---:|---:|---:|---:|
| `prob_21` | `< 17,308,584` | `> 66` | `< 1.1295814367256367` | `< 2.0150268181599675` | `< 0.4291875505445977` |
| `prob_22` | `< 1,594,867` | `> 184` | `< 0.4481704336193734` | `< 0.5321131372973988` | `< 0.13154786231019028` |
| `prob_23` | `< 15,305,668` | `> 54` | `< 1.4491142754591744` | `< 2.4545250666444190` | `< 0.5067918040364964` |
| `prob_24` | `< 3,055,604` | `> 78` | `< 1.0563540379083953` | `< 1.3774584874481661` | `< 0.2567642868462150` |
| `prob_25` | `< 1,851,982` | `> 72` | `< 1.1901187633185246` | `< 1.4031495127532656` | `< 0.2864239149178805` |

판정 규칙은 strict AND다.

- 각 행의 다섯 조건을 모두 만족해야 해당 instance가 PASS다.
- 5개 instance가 모두 PASS해야 전체 baseline improvement gate가 PASS다.
- Equality는 FAIL이다.
- Aggregate, 평균, median, percent improvement로 개별 FAIL을 상쇄하지 않는다.
- 한 instance라도 FAIL이면 전체 판정은 `NO-GO`이며 production promotion을 금지한다.

### 18.3 추가 성능 목표

다음은 필수 18.2 gate와 별도로 보고할 목표이며 구현 전 달성 사실이 아니다.

- Extension candidate-generation calls/iteration `< 8.6` native extension reference
- Anchor, extension, 전체 candidate-generation calls/iteration을 각각 보고
- Retime triggers/iteration `< 24/58 ≈ 0.4138`
- 200 iterations 및 0.5초/iteration: 목표값
- 1,000 iterations: stretch goal이며 hard gate가 아님

Correctness 또는 18.2 strict improvement gate를 통과하지 못하면 추가 목표 달성과 무관하게 실패다. 200 iterations, 0.5초/iteration, 1,000 iterations는 18.2를 대체하지 않는다.

## 19. 비목표

- ALNS iteration counter 재정의
- Candidate generation call을 iteration으로 간주
- Flag-on repair의 full-space top-3, flag-off trajectory, 동일-seed candidate parity 보장
- Global/cross-iteration/cross-repair candidate cache
- 전체 candidate/pair 공간 저장 또는 dump
- Generic dependency graph
- 새 solver 또는 새 repair 알고리즘
- Native C++ kernel/API 변경
- Production native default를 Python으로 되돌림
- MIP/interlock 활성화
- Acceptance/cooling 공식 또는 destroy operator 구현 변경
- Multi-threading
- 새 scheduler 또는 phase framework
- Telemetry framework 전면 개편
- `prob_21`~`prob_25` 외 instance 또는 다중 seed 검증
- 1,000 iterations를 release hard gate로 사용

이 문서는 후속 IMPLEMENT 세션의 구현 결정을 확정한다. 같은 세션에서 구현을 시작하지 않는다.
