# Native repair streaming 및 exact C++ 단계적 이관 설계

작성일: 2026-07-16
상태: 구현 전 상세 설계
작업공간: `/Users/brown/workspace/ogc/sol-native-implementation`
관련 코드: `baseline/solver/native_repair.py`, `baseline/solver/construct.py`, `native/ogc_native/`
실행 prompt: `docs/implementation/sol/13_NATIVE_REPAIR_SCHEDULER_PROMPT.md`

## 1. 결정 요약

최종 방향은 repair의 candidate generation, exact-free 판정, scoring, top-3 선택을 C++ 내부에서 완료하는 것이다. 다만 한 번에 전체를 옮기지 않고 다음 네 phase로 진행한다.

1. stateful streaming cursor를 도입하고 exact 판정은 Python에 유지한다.
2. C++ exact-free 판정을 shadow mode로 추가한다.
3. 검증된 C++ exact-free를 활성화하고 repair inner loop를 C++에서 닫는다.
4. `prob_23` 성공 후에만 `prob_21`부터 `prob_25`까지 확장 검증한다.

각 phase는 하나의 Codex worker 세션에서 구현, focused test, `prob_23` 검증, 결과 보고까지 끝낼 수 있어야 한다. Master scheduler는 세션 생성과 완료 조건 감사만 담당한다. Worker 내부에 별도 scheduler, phase state file, CPU gate, 새 worktree, 거대한 immutable artifact는 만들지 않는다.

production default는 전체 과정에서 다음 상태를 유지한다.

```text
repair_backend = "python"
native exact = OFF
native prefilter = OFF
MIP = OFF
interlock = OFF
```

## 2. 현재 기준과 문제 정의

동일한 `prob_23`, seed `20260710`, 120초 조건의 현재 기준은 다음과 같다.

| 항목 | Python | Native |
|---|---:|---:|
| objective | `52,450,017` | `59,142,141` |
| ALNS iterations | `40` | `32` |
| accepted | `37` | `28` |
| new-best | `32` | `31` |
| repair seconds | `90.21` | `91.57` |

초기 anchor 구간에서는 native가 Python보다 빠르다.

| Anchor 지표 | Python | Native |
|---|---:|---:|
| iterations | `19` | `21` |
| repair/iteration | `1.96s` | `1.84s` |
| 종료 objective | `82,453,165` | `82,115,373` |

회귀는 extension 구간에서 발생한다.

| Extension 지표 | Python | Native |
|---|---:|---:|
| iterations | `21` | `11` |
| accepted | `18` | `8` |
| repair/iteration | `2.53s` | `4.81s` |
| repair seconds | `53.04s` | `52.87s` |

Native extension telemetry는 다음 데이터 증폭을 보여준다.

- candidate rows: `3,174,280`
- Python으로 전달된 pairs: `13,355,586`
- 실제 exact relation 호출: `3,710,154`
- 반환된 top candidate: `228`
- deadline 직전 Python fallback calls: `20`

전달된 pair의 약 72%는 첫 충돌 이후 exact 판정에 사용되지 않지만 이미 생성, 복사, 검증, 순회된다. 또한 현재 `prepare_candidate_chunk()`는 entry chunk마다 fitting options와 time candidates를 다시 계산한다. 따라서 현재 병목은 C++ 산술이 아니라 다음 hybrid 경계다.

```text
C++: 후보와 pair를 eager materialization
  -> pybind Python 객체 변환
  -> Python validation
  -> Python/Shapely exact relation
  -> C++ finalize
```

fixed-work에서 최초 8회 candidate, placement, digest parity가 확인되었으므로 현재 최종 objective 차이의 주원인은 후보 의미가 아니라 extension 처리량 부족이다.

## 3. 목표와 비목표

### 3.1 목표

- Python의 candidate 순서, dedup, tie-break, top-3 결과를 유지한다.
- extension repair/iteration을 Python 수준 이하로 낮춘다.
- `prob_23` 120초에서 native ALNS iterations 감소를 제거한다.
- `prob_23`에서 Python 대비 objective non-loss를 달성한다.
- C++ exact 오류가 있어도 checker-valid incumbent를 보존한다.
- Phase 3까지 통과한 뒤 `prob_21`~`prob_25`로만 검증 범위를 넓힌다.

### 3.2 비목표

- ALNS destroy, acceptance, temperature, weight adaptation을 C++로 옮기지 않는다.
- retiming, MIP, interlock, constructor를 변경하지 않는다.
- official checker를 C++로 재작성하지 않는다.
- repair에 필요하지 않은 완전한 temporal four-state API를 C++ 공개 API로 만들지 않는다.
- custom polygon intersection engine을 작성하지 않는다.
- 멀티스레딩, SIMD 전용 경로, buffer protocol 전용 프레임워크를 만들지 않는다.
- 각 phase마다 별도 benchmark framework나 artifact schema를 만들지 않는다.
- `prob_23` 개선 전 hard-10, all-40, 다중 seed matrix를 실행하지 않는다.

## 4. 유지해야 할 불변식

### 4.1 의미론

- 후보 순서는 Python의 fitting option, entry, position 순서와 동일하다.
- lattice point는 Python처럼 고유 좌표가 cap에 찰 때까지 생성한다.
- exact-free는 `GeometryKernel.relation(candidate, existing).state is PairState.FREE`와 동일해야 한다.
- boundary contact와 intersection area `0`은 free다.
- 어느 방향이든 obstruction intersection area가 `> 0`이면 free가 아니다.
- canonical tie는 total, fragmentation, guide penalty, exit, entry, bay, orientation, x, y, block 순서를 유지한다.
- current placement rollback column을 보존한다.

### 4.2 안전성

- native 결과는 Python에서 반환된 최대 3개 후보만 재검증한 뒤 commit한다.
- state version이 다르면 native 결과를 폐기한다.
- native import, ABI, GEOS, topology, invalid output 오류는 입력 snapshot을 손상시키지 않는다.
- deadline 도달 후 새 Python full repair를 시작하지 않는다.
- 부분 native 결과가 없으면 repair는 `BUDGET` 또는 기존 incumbent identity로 종료한다.
- 최종 operations는 official checker Stage 5를 통과해야 한다.

### 4.3 결정성

- hash container iteration order를 결과 순서로 사용하지 않는다.
- `-ffast-math`와 `-march=native`를 사용하지 않는다.
- exact cache는 결과에 영향을 주지 않고 성능에만 영향을 준다.
- native mode에서도 Python과 동일한 seed를 사용하며 C++에서 별도 RNG를 만들지 않는다.

## 5. 목표 구조

### 5.1 Phase 1 구조

```text
Python heuristic_repair
  -> C++ RepairCursor.next_batch(max_rows=16)
  -> Python exact-free 판정
  -> C++ RepairCursor.consume(verdicts)
  -> 충분한 후보 또는 deadline까지 반복
  -> C++ RepairCursor.finalize_top3()
  -> Python returned-candidate validation/commit/checker
```

### 5.2 Phase 3 최종 구조

```text
Python heuristic_repair
  -> C++ RepairCursor.run_exact(remaining_seconds)
       candidate generation
       -> AABB skip
       -> GEOS exact-free
       -> first-conflict short-circuit
       -> scoring
       -> stable top-3
  -> Python으로 최대 3개 후보만 반환
  -> Python exact recheck of returned candidates
  -> transactional commit
  -> official checker
```

Python은 최종 안전 경계로 남지만, candidate/pair 전체는 Python으로 전달하지 않는다.

## 6. 최소 native API 설계

새 API는 기존 `PreparedCandidateBatch`를 즉시 삭제하지 않고 cursor 경로를 추가한다. Phase 1~2 동안 기존 경로는 fallback과 비교용으로 유지한다.

### 6.1 `RepairCursor`

C++ 내부 상태는 다음 필드만 가진다.

```cpp
struct RepairCursor {
  const StaticProblem* problem;
  const PackedState* state;
  Row current;
  bool has_current;

  std::vector<Fitting> fitting;
  std::vector<std::int64_t> entries;
  std::int64_t option_index;
  std::int64_t entry_index;
  std::int64_t position_index;

  std::int64_t option_accepted;
  std::int64_t accepted_total;
  std::vector<ScoredRow> best;

  bool lattice_only;
  bool deadline_hit;
  bool complete;
};
```

`fitting`과 `entries`는 cursor 생성 시 한 번만 계산한다. `next_batch()`마다 다시 계산하지 않는다.

### 6.2 `CandidateBatch`

Phase 1의 Python exact용 batch는 최대 16개 row만 포함한다.

```cpp
struct CandidateBatch {
  std::vector<Row> rows;                 // <= 16
  std::vector<std::int64_t> pair_offsets;
  std::vector<Row> pairs;                // definitely-free pair 제외
  std::vector<double> total;
  std::vector<double> tardiness;
  std::vector<double> assignment;
  std::vector<std::int64_t> fragmentation;
  std::vector<std::int64_t> guide_penalty;
  bool deadline_hit;
  bool complete;
};
```

Batch 크기 `16`은 기존 deadline polling 단위와 맞추며 설정 옵션으로 노출하지 않는다. 성능이 나쁘다는 증거 없이 batch-size tuning을 추가하지 않는다.

### 6.3 Python-visible methods

```python
cursor = module.RepairCursor(
    problem,
    packed_state,
    block_id,
    current_row,
    time_cap,
    anchor_cap,
    lattice_cap,
)

batch = cursor.next_batch(max_rows=16, remaining_seconds=remaining)
cursor.consume(verdicts, processed_rows)
result = cursor.finalize_top3()
```

Phase 3에서는 같은 cursor에 다음 한 메서드만 추가한다.

```python
result = cursor.run_exact(remaining_seconds=remaining)
```

별도의 iterator framework, callback registry, async API는 만들지 않는다.

## 7. exact-free C++ 계약

Repair candidate validation에는 완전한 `PairRelation` 공개 객체가 필요하지 않다. co-present pair가 `FREE`인지 여부만 필요하다. C++ exact 범위는 다음 함수로 제한한다.

```cpp
ExactVerdict is_free(const Row& candidate, const Row& existing);
```

결과는 `FREE`, `BLOCKED`, `ERROR` 세 값만 사용한다. `ERROR`는 free로 승인하지 않고 Python fallback 또는 incumbent 보존으로 처리한다.

### 7.1 Geometry 입력

Raw polygon을 C++에서 다시 정규화하지 않는다. Python `GeometryKernel`이 이미 다음 작업을 완료한다.

- invalid polygon `buffer(0)` repair
- degenerate/empty layer 제거
- layer geometry 구성
- suffix union 구성

Phase 2에서는 이 정규화된 결과를 WKB로 pack한다.

```python
orientation_payload = {
    "layers_wkb": [None if layer is None else layer.wkb for layer in shape.layers],
    "suffix_unions_wkb": [item.wkb for item in shape.suffix_unions],
    "layer_aabbs": ...,
    "suffix_aabbs": ...,
}
```

C++은 WKB를 GEOS geometry로 한 번만 parse해 `StaticProblem` 수명 동안 보관한다. 현재의 union exterior vertex 목록은 position anchor 생성에만 유지하고 exact 판정에 사용하지 않는다. exterior vertex만으로는 hole과 MultiPolygon 의미를 보존할 수 없기 때문이다.

### 7.2 Exact 판정

Python `_obstructs_relative()`와 동일한 순서를 사용한다.

1. mover layer와 stationary suffix의 공통 layer 범위를 계산한다.
2. strict AABB overlap이 아니면 해당 layer를 건너뛴다.
3. stationary suffix를 상대 좌표 `dx`, `dy`만큼 translate한다.
4. GEOS intersection을 계산한다.
5. intersection이 non-empty이고 area가 `> 0`이면 obstruction이다.
6. 두 방향 모두 obstruction이 없을 때만 `FREE`다.

GEOS exception, non-finite area, WKB parse 실패는 `ERROR`다.

### 7.3 GEOS 의존성 원칙

- custom polygon intersection을 구현하지 않는다.
- Shapely private symbol이나 private capsule을 직접 호출하지 않는다.
- GEOS C API의 공개 header/library만 사용한다.
- local 환경의 Shapely는 `2.1.2`, GEOS는 `3.13.1`이지만 bundled dylib만 있고 development header는 현재 확인되지 않았다.
- Phase 2 세션은 지원되는 GEOS header/library 경로를 먼저 확인한다.
- 지원되는 build dependency가 없으면 hand-written ABI 선언이나 `dlopen` 우회를 만들지 않고 Phase 2를 `BLOCKED_GEOS_BUILD`로 종료한다.
- GEOS linkage가 확정되기 전에는 production archive에 native exact를 포함하지 않는다.

이 제한은 구현을 어렵게 만드는 규칙이 아니라 topology correctness를 보존하기 위한 최소 안전선이다.

### 7.4 Exact cache

Cache key는 Python과 동일하다.

```text
(first.block_id, first.orient_idx,
 second.block_id, second.orient_idx,
 second.x - first.x, second.y - first.y)
```

Phase 2에서는 단순 bounded map 하나만 사용한다. 복잡한 LRU를 구현하지 않는다. 최대 크기는 Python과 같은 `2**18`이며 cap 도달 시 전체 clear를 허용한다. Cache policy는 결과에 영향을 주지 않는다.

## 8. 실행 Phase

### Phase 1. Stateful bounded streaming

#### 목표

Python exact authority를 유지하면서 repeated time/option construction, 대량 batch materialization, deadline fallback 낭비를 제거한다.

#### 한 세션의 변경 범위

- `native/ogc_native/src/types.hpp`
- `native/ogc_native/src/repair_kernel.hpp`
- `native/ogc_native/src/repair_kernel.cpp`
- `native/ogc_native/src/bindings.cpp`
- `baseline/solver/native_repair.py`
- `experiments/ogc_sage/verify_native_p7_fixed_time.py`의 instance/timelimit parameterization
- 관련 focused tests

`alns.py`, acceptance, destroy operator, retiming은 수정하지 않는다.

#### 구현 순서

1. 현재 branch/HEAD/status와 diff를 읽기 전용으로 확인한다.
2. `RepairCursor`와 `CandidateBatch`를 추가한다.
3. fitting options와 entries를 cursor 생성 시 한 번만 계산한다.
4. `next_batch()`가 canonical 순서로 최대 16개 row를 반환하게 한다.
5. Python exact verdict를 `consume()`으로 돌려주고 option당 accepted 3개에서 중단한다.
6. current placement rollback과 lattice escalation을 기존 순서로 유지한다.
7. deadline 도달 후 `python_reference()`를 새로 실행하지 않고 partial top 또는 `BUDGET`을 반환한다.
8. 기존 batch 경로는 fallback으로 유지한다.

#### 최소 telemetry

- cursor count
- batches
- generated rows
- emitted pairs
- processed rows
- accepted rows
- deadline hits
- Python exact calls
- cursor prepare/exact/finalize seconds

Row별 trace나 전체 후보 dump는 만들지 않는다.

#### 검증

1. focused native/neighborhood tests
2. 전체 baseline unit tests
3. `prob_23` 동일 state/destroy에 대한 최초 8회 fixed-work 비교
4. `prob_23`, seed `20260710`, 120초 Python 1회와 native 1회

#### 완료 조건

- candidate mismatch `0`
- placement/digest mismatch `0`
- Stage 5, violations `0`, objective/checker parity
- native extension repair/iteration `< 4.81s`
- native ALNS iterations `>= 32`
- native objective `<= 59,142,141`

Phase 1은 Phase 2의 기반이므로 parity가 통과하고 기존보다 명확히 악화되지 않으면 완료한다. 목표 처리량에 미달해도 같은 세션에서 추가 튜닝 원인을 여러 개 섞지 않는다.

#### 세션 종료 보고

- 변경 파일
- focused/full test 결과
- fixed-work parity
- Python/native objective와 iterations
- extension repair/iteration
- 현재 diff

### Phase 2. GEOS exact-free shadow mode

#### 목표

C++ exact-free 결과를 실제 의사결정에 사용하지 않고 Python 결과와 비교해 의미론을 검증한다.

#### 한 세션의 변경 범위

- `native/ogc_native/src/exact_geometry.hpp` 신규
- `native/ogc_native/src/exact_geometry.cpp` 신규
- `native/ogc_native/src/types.hpp`
- `native/ogc_native/src/bindings.cpp`
- `baseline/solver/native_repair.py`
- `baseline/solver/construct.py`의 mode 필드
- `baseline/solver/runtime.py`의 단일 mode 필드
- `baseline/solver/entry.py`의 mode 전달
- local/CMake build script
- geometry/native focused tests

Mode는 하나의 필드만 추가한다.

```python
native_exact_mode: Literal["python", "shadow", "native"] = "python"
```

Production default는 `python`이다.

#### 구현 순서

1. supported GEOS C header/library와 build linkage를 확인한다.
2. Python-normalized layers/suffix unions를 WKB로 pack한다.
3. C++ `ExactGeometryStore`가 WKB를 한 번 parse하고 소유하게 한다.
4. strict AABB skip과 두 방향 obstruction을 구현한다.
5. cursor가 처리한 pair마다 Python/C++ free verdict를 비교한다.
6. 의사결정과 top-3는 계속 Python verdict를 사용한다.
7. 첫 mismatch의 candidate, existing, relative translation, Python/C++ verdict만 기록한다.

#### 최소 telemetry

- shadow checked pairs
- shadow free/free, blocked/blocked counts
- shadow mismatch count
- GEOS error count
- Python exact seconds
- C++ exact seconds
- exact cache hits/misses

#### 검증

1. boundary contact, same-layer overlap, outer-direction geometry tests
2. 기존 deterministic 2,000 pair/time parity test
3. `prob_23` 최초 8회 fixed-work repair의 실제 pair stream shadow 비교
4. candidate/placement/digest parity

Shadow mode는 같은 exact를 두 번 실행하므로 120초 objective 성능 비교를 하지 않는다. 이 phase의 검증 instance는 `prob_23`으로 제한한다.

#### 완료 조건

- shadow mismatch `0`
- GEOS error `0`
- 기존 2,000-case tests 통과
- `prob_23` candidate/placement/digest mismatch `0`
- production default가 `native_exact_mode="python"`

Mismatch가 있으면 그 세션에서는 첫 mismatch 하나만 진단하고 Phase 3로 진행하지 않는다.

### Phase 3. Native exact 활성화 및 inner loop 완결

#### 목표

Candidate generation부터 exact-free, first-conflict short-circuit, scoring, stable top-3까지 C++ 내부에서 완료하고 Python에는 최대 3개 후보만 반환한다.

#### 한 세션의 변경 범위

- Phase 1 cursor와 Phase 2 exact kernel
- `baseline/solver/native_repair.py`
- focused integration tests
- `prob_23` 비교 harness의 mode 선택

새 solver feature나 별도 알고리즘은 추가하지 않는다.

#### 구현 순서

1. `RepairCursor.run_exact()`가 내부적으로 후보를 순회한다.
2. pair별 첫 `BLOCKED`에서 즉시 다음 후보로 이동한다.
3. option당 accepted 3개와 current rollback 규칙을 적용한다.
4. stable canonical top-3만 Python으로 반환한다.
5. Python은 반환 후보 최대 3개를 `evaluate_insert()`로 재검증한다.
6. 재검증 mismatch 또는 GEOS error가 있으면 해당 repair 결과를 폐기한다.
7. deadline에서는 partial top을 반환하고 새 Python full repair를 시작하지 않는다.
8. 기존 Python-exact cursor 경로는 명시적 fallback으로 유지한다.

#### 최소 telemetry

- generated candidates
- AABB-skipped pairs
- GEOS exact calls
- first-conflict skipped pairs
- returned candidates
- native exact/cache/error seconds
- Python returned-candidate rechecks
- native error/fallback/deadline counts

#### 검증 순서

1. focused native, geometry, neighborhood tests
2. 전체 baseline tests
3. `prob_23` 최초 8회 fixed-work Python/native 비교
4. `prob_23`, seed `20260710`, 120초 Python 1회
5. 같은 조건 native exact 120초 1회

#### 완료 조건

- fixed-work candidate/placement/digest mismatch `0`
- returned-candidate Python recheck failure `0`
- GEOS/native exception `0`
- 두 run 모두 Stage 5, violations `0`, checker parity
- native objective `<=` paired Python objective
- native ALNS iterations `>=` paired Python iterations
- native extension repair/iteration `<=` paired Python extension repair/iteration

어느 하나라도 실패하면 `prob_21`~`prob_25`를 실행하지 않는다. 같은 세션에서 다른 원인까지 수정하지 않고 telemetry와 첫 직접 원인을 보고한다.

### Phase 4. `prob_21`~`prob_25` 확장 검증

#### 진입 조건

Phase 3의 `prob_23` 완료 조건을 모두 만족해야 한다. 이 phase는 validation-only 세션이다. 검증 도중 solver 코드를 수정하지 않는다.

#### 실행 범위

- instances: `prob_21`, `prob_22`, `prob_23`, `prob_24`, `prob_25`
- seed: `20260710`
- budget: `120초`
- variants: Python, native exact
- 각 instance당 Python 1회, native 1회
- 총 10회 실행

한 instance의 Python/native를 연속 실행한다. CPU clean-window나 process gate를 만들지 않는다. 명백한 외부 부하는 결과 메모에만 기록한다.

#### 수집 지표

- objective와 Z1/Z2/Z3
- Stage, violations, checker parity
- ALNS iterations, attempts, accepted, new-best
- repair/iteration, extension repair/iteration
- native generated candidates, exact calls, skipped pairs
- deadline, fallback, exception

#### 판정

`GO`:

- 5/5 Stage 5, violations 0
- 5/5 native objective가 paired Python보다 나쁘지 않음
- native 총 ALNS iterations가 Python 총 iterations보다 낮지 않음
- native exact/recheck mismatch 0

`CONDITIONAL`:

- correctness는 모두 통과
- median objective delta는 non-positive
- 일부 instance가 Python보다 나쁘지만 최악이 `+5%` 이내

이 경우 production default는 Python으로 유지하고 wider validation은 보류한다.

`NO-GO`:

- Stage/checker/parity 실패
- 어느 instance든 objective `+5%` 초과 악화
- native exception 또는 returned-candidate mismatch
- 총 iterations가 Python의 90% 미만

Phase 4 이후에도 all-40은 자동 실행하지 않는다.

## 9. 공통 세션 실행 규칙

각 phase 세션은 다음 순서만 따른다.

1. branch, HEAD, status, 현재 diff를 읽기 전용으로 확인한다.
2. 해당 phase 문서 절과 관련 코드만 읽는다.
3. phase 범위의 코드만 수정한다.
4. focused tests를 실행한다.
5. 전체 baseline tests를 실행한다.
6. 해당 phase의 `prob_23` 검증을 실행한다.
7. 실행 명령, 핵심 결과, 현재 diff를 간단히 보고한다.

금지 사항:

- 새 branch/worktree 생성
- 기존 dirty/untracked 변경 reset, restore, stash, clean
- 사용자 승인 없는 commit/push
- worker 세션 내부의 nested scheduler와 retry automation 생성
- CPU PID threshold와 clean-window 설계
- phase 안에서 unrelated refactor
- 실패 후 여러 원인을 한 patch에 동시 수정
- 각 실행마다 대형 artifact tree 생성

Build와 run output은 기본적으로 `/tmp`의 phase별 단일 디렉터리에 둔다. workspace에는 solver source, tests, 이 설계 문서만 남긴다.

## 10. 검증 명령 기준

정확한 바이너리 경로와 output 경로는 세션마다 `/tmp` 아래 새 경로를 사용한다.

```bash
# Local native build
/opt/homebrew/Caskroom/miniforge/base/envs/ogc-2026/bin/python \
  scripts/build_native_local.py \
  --output-dir /tmp/ogc-native-<phase>

# Focused tests
cd baseline
/opt/homebrew/Caskroom/miniforge/base/envs/ogc-2026/bin/python -m unittest -v \
  tests.test_native_import_fallback \
  tests.test_native_repair_integration \
  tests.test_neighborhoods \
  tests.test_geometry \
  tests.test_four_state_parity

# Full baseline tests
/opt/homebrew/Caskroom/miniforge/base/envs/ogc-2026/bin/python \
  -m unittest discover -s tests -q
```

Fixed-time 비교는 기존 `verify_native_p7_fixed_time.py`의 수집 포맷을 재사용한다. Phase 1에서 instance와 timelimit을 안전하게 parameterize해 `prob_23`, 120초를 직접 받을 수 있게 하되 별도 orchestration framework는 만들지 않는다. Phase 4에서는 같은 command를 `prob_21`~`prob_25`에 반복 사용한다.

## 11. 파일별 책임

| 파일 | 책임 |
|---|---|
| `baseline/solver/construct.py` | Python reference 의미론, 수정 최소화 |
| `baseline/solver/native_repair.py` | cursor adapter, mode 선택, validation, fallback, telemetry |
| `baseline/solver/runtime.py` | `native_exact_mode` 기본값 `python` |
| `baseline/solver/entry.py` | `native_exact_mode`를 repair config로 전달 |
| `native/ogc_native/src/types.hpp` | cursor/batch/result compact types |
| `native/ogc_native/src/repair_kernel.cpp` | candidate cursor, scoring, top-3 |
| `native/ogc_native/src/exact_geometry.cpp` | GEOS WKB parse와 exact-free 판정 |
| `native/ogc_native/src/bindings.cpp` | 최소 pybind API와 오류 변환 |
| `scripts/build_native_local.py` | local GEOS/pybind linkage |
| `native/ogc_native/CMakeLists.txt` | target GEOS/pybind linkage |
| `experiments/ogc_sage/verify_native_p7_fixed_time.py` | 단일 instance Python/native 측정 |

`alns.py`와 `neighborhoods.py`는 수정하지 않는다. `entry.py`는 mode 전달 한 곳만 변경한다.

## 12. 완료 정의

이 설계의 구현은 다음 조건을 모두 만족할 때 완료된다.

1. Stateful cursor가 Python reference와 fixed-work parity를 유지한다.
2. C++ exact shadow mismatch가 `0`이다.
3. Native exact active mode가 반환한 후보는 Python 재검증을 통과한다.
4. `prob_23` 120초에서 native objective와 ALNS iterations가 paired Python보다 나쁘지 않다.
5. `prob_21`~`prob_25` 검증 결과가 명시적인 `GO`, `CONDITIONAL`, `NO-GO` 중 하나로 보고된다.
6. 모든 결과가 Stage 5이고 objective/checker parity를 유지한다.
7. Production default는 별도 승인 전까지 Python으로 유지된다.
8. all-40 validation은 별도 후속 작업으로 남는다.

## 13. Phase 4 이후 release decision

2026-07-16 Phase 4가 `GO`를 충족한 뒤 별도 사용자 승인을 받아 production
기본값을 다음과 같이 승격한다.

```text
repair_backend = "native"
native_exact_mode = "native"
native prefilter = OFF
MIP = OFF
interlock = OFF
```

Native module import 또는 runtime 실행이 실패하면 기존 Python reference로
fallback한다. 반환 후보 Python 재검증, rollback, transactional commit,
serialization, full checker, frozen-anchor dominance guard의 권한은 유지한다.
all-40과 다중 seed 검증은 별도 후속 검증으로 남는다.
