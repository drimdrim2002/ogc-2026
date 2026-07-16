# OGC-SAGE pybind11 repair kernel 가속 계획

작성일: 2026-07-16

상태: 마감 대응 fast-track 구현 전 계획

solver 기준 source: `8e8acb166843ba156a65e29ff4976f4aba4e52eb`

계획 문서 기준 commit: `3e83b52a267e7c74cddcca248e98ff3f8be85cc3`

상위 문서: `docs/implementation/sol/10_PERFORMANCE_ACCELERATION_PLAN.md`

성능 근거: `docs/implementation/sol/performance/04_HEURISTIC_REPAIR_ACCELERATION.md`

## 0. 마감 대응 fast-track 우선 규칙

대회 마감까지 남은 시간을 고려해 이 문서는 전체 P0~P7을 순차 완료한 뒤 구현을 시작하는 방식에서, **P0-lite 직후 좁은 native spike로 GO/NO-GO를 빠르게 판정하는 방식**으로 전환한다. 이 절은 뒤의 장기 release 절차보다 우선한다.

이번 fast-track의 범위는 다음과 같다.

1. 기존 120초 결과를 Python reference 운영 증거로 재사용하고 동일 실행을 반복하지 않는다.
2. P0는 source/environment/native 계약을 확인하는 P0-lite로 축소한다.
3. 같은 세션에서 실제 repair fixture, build scaffold, candidate batch kernel까지 진행할 수 있다.
4. fixed-work parity와 짧은 end-to-end smoke만으로 native spike의 GO/NO-GO를 결정한다.
5. native production default, submission archive 교체, 전체 60회와 daily-40은 별도 release 승인 전까지 수행하지 않는다.

### 0.1 재사용할 120초 Python 결과

다음 결과는 다시 실행하지 않는다.

| 데이터 | solver 시간 | Objective | Z1 | Z2 | Z3 | ALNS 반복 | MIP repair |
|---|---:|---:|---:|---:|---:|---:|---:|
| `prob_21.json` | `114.118s` | `37,637,640` | `2,780` | `895` | `3,753` | `42` | `0` |
| `prob_22.json` | `114.128s` | `2,158,211` | `67` | `3,500` | `3,136` | `68` | `0` |
| `prob_23.json` | `114.117s` | `41,805,318` | `3,060` | `454` | `1,558` | `44` | `0` |
| `prob_24.json` | `114.152s` | `26,885,484` | `1,973` | `215` | `1,928` | `46` | `0` |
| `prob_25.json` | `114.113s` | `5,030,622` | `7,472` | `1,918` | `2,244` | `40` | `0` |

raw artifact가 발견되면 source/config/seed/environment와 SHA-256을 연결한다. 발견되지 않으면 `evidence waiver`로 기록하되 fast-track을 차단하지 않는다. source/seed/hash가 확인되지 않은 표의 objective는 새 native 결과와 직접 비교하지 않고, 120초 예산 사용과 ALNS 활동을 확인하는 운영 신호로만 사용한다.

### 0.2 fast-track에서 유지할 최소 안전선

- Python reference가 production default다.
- native는 명시적 benchmark backend로만 시작한다.
- fixed-work candidate/order/objective/serialization parity는 생략하지 않는다.
- exact Shapely와 official checker 권위는 유지한다.
- native import/runtime 실패 시 Python reference 또는 저장된 checker-valid incumbent를 반환한다.
- native production 승격 전에는 original baseline dominance와 최종 release gate를 별도로 완료한다.

## 1. 결론과 목표

전체 ALNS를 C++로 다시 작성하지 않는다. 실제 측정에서 지배적인 다음 호출 경계만 pybind11 기반 native kernel 후보로 삼는다.

```text
heuristic_repair
  -> generate_insertion_candidates
    -> _candidate_options / search
      -> generate_time_candidates
      -> generate_position_candidates
      -> evaluate_insert
        -> GeometryKernel.fits
        -> IndexedSolutionState.co_present
        -> GeometryKernel.relation
        -> assignment_delta / fragmentation / canonical score
```

1차 목표는 후보 열거·정수 범위 검사·시간/AABB 필터·상태 조회·점수 계산을 큰 batch 단위로 C++에서 수행하는 것이다. Shapely exact polygon 판정은 Python에 남기고, C++은 exact 판정을 대신하지 않는다.

fast-track의 1차 목표는 fixed-work parity와 실측 speedup으로 좁은 native kernel의 투자 가치가 있는지 판단하는 것이다. release의 최종 목표는 다음 세 조건을 동시에 만족하는 것이다.

1. 같은 고정 작업량에서 Python reference와 후보·순서·목적함수·직렬화가 동일하다.
2. 공식 유사 cold process에서 end-to-end 처리량이 실제로 개선된다.
3. 같은 instance/seed에서 원본 baseline 해보다 나쁜 결과를 반환하지 않는다.

속도가 빨라졌다는 사실만으로 production에 승격하지 않는다. fast-track GO는 후속 release 검증을 진행할 자격일 뿐이며, 추가 처리량이 checker-valid objective 개선으로 이어지고 baseline-dominance와 패키징/ABI gate를 모두 통과해야 실제 제출 후보가 된다.

## 2. 실제 병목 근거

### 2.1 phase 비중

기존 180초 계측에서 heuristic repair는 wall time의 약 `74.1%`, LNS retime은 약 `19.5%`였다. retime은 별도 프로파일에서 native Gurobi optimize가 약 `87.1%`를 차지했으므로 Python/C++ wrapper 이관의 우선순위가 낮다.

### 2.2 repair 내부 프로파일

hard-10 repair-only 프로파일의 측정값은 다음과 같다.

| 구간 | 누적 시간 또는 호출 수 | 판단 |
|---|---:|---|
| `generate_insertion_candidates -> _candidate_options/search` | `149.598s`, repair root의 `98.0%` | pybind 1차 경계 |
| `evaluate_insert` | `95.772s` | 후보 batch 평가 대상으로 포함 |
| `GeometryKernel.relation` | `59.370s` | exact와 numeric prefilter를 분리 |
| `GeometryKernel.fits` | `36.326s`, 약 `11.17M` calls | static fit-bounds로 이관 |
| `OrientationInfo.integer_range` | `12.966s` Python self | 반복 계산 제거 대상 |
| `generate_position_candidates` | `11.430s` | canonical anchor 생성 이관 후보 |
| `IndexedSolutionState.co_present` | `11.385s` | per-bay interval index로 이관 |
| `Placement.__post_init__` | `11.460s` | hot loop의 Python 객체 생성 제거 |
| Shapely intersection | 약 `790K` calls, `12.581s` self | 1차 C++ 범위 밖 exact oracle |

이 수치들은 중첩 call tree이므로 합산하지 않는다. repair 후보 경계 전체를 3배 가속할 경우의 이론적 상한은 다음 Amdahl 식으로만 판단한다.

```text
f = 0.741 * 0.98 ~= 0.726
speedup_upper(3x) = 1 / ((1 - f) + f / 3) ~= 1.94x
```

이는 상한일 뿐이다. Python↔C++ 변환, Shapely exact resolve, cold import, deadline polling을 포함한 실측값으로 다시 계산해야 한다.

### 2.3 과거 가속 실험의 교훈

`fits()`의 불변 범위를 Python에서 precompute한 4단계 후보는 fixed-work parity와 warm throughput `+13.92%`, repair seconds/iteration `-17.44%`를 달성했다. 그러나 fixed-time에서 `prob_28 +37.51%`, `prob_24 +26.14%` 회귀가 발생해 승격되지 않았다.

따라서 이번 native 경로는 다음을 전제로 한다.

- precomputed fit 자체를 곧바로 production에 재승격하지 않는다.
- fixed-work 가속과 fixed-time 탐색 궤적을 별도 판정한다.
- fast-track에서는 저장된 checker-valid incumbent를 보존하고, production release에서는 원본 해를 같은 process 안에서 보존한 뒤 native 결과가 더 좋을 때만 설치한다.

## 3. 범위와 비범위

### 3.1 포함 범위

- repair 전용 `generate_insertion_candidates()` 호출 경계
- time/position 후보 생성의 순수 수치 부분
- block/orientation/bay별 integer fit bounds
- retained placement의 bay/time interval 조회
- candidate별 exact relation 필요 pair 산출
- tardiness, assignment delta, fragmentation, canonical tie 계산
- definitely-free만 반환하는 보수적 AABB/layer-AABB prefilter
- pybind adapter, native telemetry, import/runtime fallback
- Ubuntu 24.04 x86_64 CPython 3.12용 build 및 submission archive

### 3.2 제외 범위

- ALNS destroy 선택, acceptance, weight, temperature, cadence
- constructor 정책과 fallback 정책
- neighborhood 크기와 연산자 집합
- retime trigger/model/Gurobi optimize
- MIP repair와 interlock 의미론
- 공식 checker 및 `baseline/utils.py`
- exact polygon intersection의 C++ 재구현
- 멀티스레드 탐색
- `-ffast-math`, 근사 objective, candidate 생략

ALNS 제어부는 repair에 비해 측정 비중이 작고, C++ 이관 시 난수 소비와 tie-break를 흔들 위험이 크므로 Python에 유지한다. Gurobi optimize와 checker는 이미 native 또는 authoritative 경계이므로 이관하지 않는다.

## 4. 필수 불변식

### 4.1 정확성

- 모든 반환 해는 Stage 5여야 한다.
- internal/checker `Z1`, `Z2`, `Z3`, total 상대 오차는 `<=1e-6`이어야 한다.
- native 결과는 canonical serializer와 full checker를 통과한 뒤에만 incumbent에 설치한다.
- exact geometry의 최종 권위는 현재 `GeometryKernel.relation()`과 공식 checker다.
- C++ prefilter는 `DEFINITELY_FREE` 또는 `UNKNOWN`만 반환한다. 불확실한 geometry를 free로 승인하지 않는다.
- native exception/import/ABI 실패는 저장된 checker-valid incumbent를 변경하지 않는다.

### 4.2 결정성

- 입력 순서, first-occurrence dedup, stable ordering, canonical tie를 Python과 동일하게 유지한다.
- hash container는 membership 용도로만 사용하고 iteration order를 결과에 사용하지 않는다.
- `double` 계산 순서와 `floor/ceil` 규칙을 Python reference에 맞춘다.
- NaN/Inf 입력은 binding boundary에서 거부한다.
- `-ffast-math`와 비결정적 parallel reduction을 금지한다.

### 4.3 baseline dominance

마감 대응 fast-track에서는 완전한 original-equivalent anchor 재현을 native spike의 선행 blocker로 두지 않는다. 대신 production default를 Python으로 유지하고, native 결과가 저장된 checker-valid incumbent를 손상시키지 않는 최소 fallback 계약을 먼저 검증한다.

fast-track 최소 계약:

1. native backend는 명시적 benchmark variant에서만 활성화한다.
2. native import/runtime failure는 입력 snapshot 또는 Python reference로 복귀한다.
3. native 결과는 canonical serializer와 full checker를 통과한 뒤에만 설치한다.
4. fixed-work parity가 실패하면 fixed-time smoke를 실행하지 않는다.

production 승격 전에는 별도의 Stage 1 prerequisite로 다음 완전한 dominance 계약을 고정한다.

1. 동일 instance/seed에서 frozen original baseline 경로를 먼저 완료한다.
2. 그 operations/objective/digest를 checker-valid anchor로 저장한다.
3. native repair는 남은 시간의 extension에서만 실행한다.
4. native 결과는 official total objective가 anchor보다 strict하게 낮을 때만 설치한다.
5. timeout, 예외, native 부재, 무개선이면 anchor를 반환한다.

native patch와 이 prerequisite 알고리즘 변경은 같은 patch에 섞지 않는다. prerequisite가 통과하지 않으면 native를 production default로 승격하거나 최종 submission archive에 활성 경로로 포함하지 않는다.

## 5. 목표 아키텍처

### 5.1 Python/native 책임 분리

```text
Python
  - Instance/Shapely preprocessing
  - checker-valid incumbent 및 IncumbentStore
  - release 단계의 frozen baseline anchor
  - ALNS/destroy/acceptance/deadline root
  - UNKNOWN pair의 exact GeometryKernel.relation()
  - transactional commit, serializer, full checker

pybind11 boundary (repair block당 2~3회 이하)
  - immutable static problem 생성
  - candidate batch prepare
  - exact verdict batch finalize

C++
  - compact fit bounds / shape AABB / canonical vertices
  - time/position candidate enumeration
  - per-bay interval scan과 numeric filtering
  - candidate rows 및 UNKNOWN pair index 생성
  - assignment delta / fragmentation / canonical score
  - exact verdict 적용과 stable top-3 선택
```

후보 하나마다 pybind를 호출하지 않는다. 한 destroyed block의 후보군을 한 번에 준비하고, exact verdict를 한 번에 되돌려 최종화한다.

### 5.2 제안 API

Python 공개 adapter는 다음 내부 계약을 갖는다.

```python
class NativeRepairAdapter:
    def prepare(
        self,
        state_rows,
        raw_bay_loads,
        block_id,
        current_placement,
        caps,
        remaining_attempts,
        remaining_seconds,
    ) -> PreparedCandidateBatch: ...

    def finalize(
        self,
        prepared,
        exact_relation_verdicts,
    ) -> tuple[CandidateScore, ...]: ...
```

`PreparedCandidateBatch`는 C++ 소유 메모리를 유지하며 다음 read-only buffer를 노출한다.

- candidate row: `(block, bay, orient, x, y, entry, exit, enumeration_ordinal)`
- candidate별 retained pair offset/index
- candidate별 `fit`, `DEFINITELY_FREE`, `UNKNOWN` 상태
- state version과 원본 attempt ordinal
- C++ 내부 static score/fragmentation 정보

Python exact resolver는 `UNKNOWN` pair만 현재 순서대로 `GeometryKernel.relation()`에 전달하고 byte verdict buffer를 작성한다. `finalize()`는 verdict를 적용한 뒤 Python과 동일한 canonical key로 상위 3개를 반환한다.

### 5.3 static problem representation

native kernel 생성 시 다음 불변 데이터를 한 번만 pack한다.

- block: release, due, dwell, workload, preference
- bay: width, height, area, normalized-load multiplier
- orientation: full AABB, fit bounds
- Shapely가 이미 복구/union한 geometry에서 추출한 canonical vertex sequence
- layer AABB와 suffix AABB
- objective weights

C++에서 polygon을 재생성하거나 union 순서를 다시 결정하지 않는다. Python/Shapely가 만든 vertex/AABB 순서를 그대로 복사해 position anchor와 conservative prefilter에만 사용한다.

### 5.4 exact geometry 전략

1차 native module은 exact intersection을 구현하지 않는다.

- fit bounds, time overlap, same-bay, union/layer AABB disjoint는 수학적으로 확정 가능한 결과만 낸다.
- 두 방향 모두 layer-AABB overlap 가능성이 없을 때만 `DEFINITELY_FREE`로 표시한다.
- 하나라도 애매하면 `UNKNOWN`으로 보내 기존 Shapely exact path를 호출한다.
- native prefilter가 `UNKNOWN`을 많이 반환해 이득이 없으면 범위를 확대하지 않고 종료한다.

GEOS C API나 Clipper 기반 exact 이관은 별도 후속 계획으로만 검토한다. 현재 checker의 polygon repair, boundary contact, `intersection.area > 0` 의미를 byte-for-byte에 가깝게 맞추기 어렵고 ABI/15MB 위험이 크기 때문이다.

### 5.5 deadline과 fallback

- Python은 호출 직전 `Budget.search_remaining()`과 safe-tail reserve를 전달한다.
- C++은 `std::chrono::steady_clock`으로 일정 후보 간격마다 자체 deadline을 확인한다.
- fixed-work parity에서는 wall-clock을 제거하고 exact attempt quota만 사용한다.
- native import가 실패하면 처음부터 Python reference backend를 사용한다.
- prepare/finalize가 Python exception을 던지면 현재 repair attempt를 폐기하고 입력 snapshot을 유지한다.
- 충분한 시간이 남은 경우에만 Python reference repair를 다시 시도한다.
- native segmentation fault는 Python에서 복구할 수 없으므로 sanitizer, fuzz, ABI smoke를 release hard gate로 둔다.

## 6. 구현 작업 묶음

마감 대응 fast-track에서는 P0-lite, P1, P2, P3를 같은 세션에서 진행할 수 있다. P3 fixed-work gate가 실패하면 P4 이후로 진행하지 않는다. P5~P7은 spike GO 후 별도 release 작업으로 취급한다.

### P0-lite — 최소 기준 확인

- current HEAD, dirty state, solver source, config, hard-10 manifest와 dataset hash를 기록한다.
- Python/Shapely/Gurobi/OS/CPU와 외부 CPU-bound process를 확인한다.
- native 제출 허용, Ubuntu 24.04, CPython 3.12, x86_64, 4 cores/16GB 계약을 재확인한다.
- 현재 Python reference의 빠른 Stage 5 smoke만 수행한다.
- §0.1의 120초 결과 raw가 있으면 hash를 연결하고, 없으면 waiver를 기록한다.
- original baseline replay, hard-10 장시간 실행과 동일 120초 재실행은 하지 않는다.

종료 조건:

- source/config/environment fingerprint 기록
- Python reference Stage 5 smoke PASS
- native 제출/runtime 계약 확인
- `P0-lite PASS` 또는 raw evidence가 없는 `PASS-WITH-WAIVER`

### P1 — native 전용 fixed-work fixture

- 기존 Stage 4 artifact 또는 현재 Python reference의 짧은 quota run에서 destroyed ids, retained placements, caps, current placement를 fixture로 저장한다.
- 우선 실제 hard fixture 1개를 만들고, 시간이 허용되면 easy/medium 또는 boundary fixture를 추가한다.
- quota 기반 Python reference 결과를 golden artifact로 만든다.
- candidate enumeration 전/후, relation query, score, top-3, commit digest를 기록한다.

종료 조건:

- 기존 Stage 4 프로파일을 재사용하고 새 장시간 profiler를 실행하지 않음
- 실제 입력 fixture hash 고정
- candidate/order/score/top-3 golden record 생성

### P2 — build/binding scaffold

예상 추가 파일:

```text
native/ogc_native/CMakeLists.txt
native/ogc_native/src/bindings.cpp
native/ogc_native/src/repair_kernel.hpp
native/ogc_native/src/repair_kernel.cpp
native/ogc_native/src/types.hpp
baseline/solver/native_repair.py
scripts/build_native_linux.sh
scripts/verify_native_binary.py
baseline/tests/test_native_import_fallback.py
```

규칙:

- C++20, Release `-O3 -DNDEBUG`, hidden visibility
- `-ffast-math`, `-march=native` 금지
- pybind11 버전과 source hash/license를 pin하고 offline build 가능하게 보존
- pybind11이 없으면 isolated 또는 project-local build dependency로 설치하고 시스템 Python을 광범위하게 변경하지 않음
- debug ASan/UBSan build와 stripped release build 분리
- import 시 stdout/stderr 0
- module import만으로 worker/thread를 시작하지 않음

종료 조건:

- 현재 개발 환경에서 empty kernel build/import와 import-absent fallback smoke PASS
- native 없이도 기존 Python tests와 Stage 5 smoke PASS
- Ubuntu 24.04/CPython 3.12 build는 spike GO 후 release gate로 이관하며, local macOS 결과를 release-ready로 표시하지 않음

### P3 — candidate enumeration/score kernel

- Python에서 pre-extracted canonical vertices와 fit bounds를 native static object에 전달한다.
- time candidates, wall/contact/lattice anchors, first-occurrence dedup을 동일 순서로 이관한다.
- packed retained state에서 co-present와 fragmentation을 계산한다.
- exact relation을 아직 건너뛰지 않고 기존 순서의 pair query를 생성한다.
- Python exact verdict를 받은 후 canonical top-3를 계산한다.

종료 조건:

- 모든 golden fixture에서 candidate rows/order/top-3/score/state version 동일
- float는 `float.hex()` 또는 bit pattern까지 비교하고 checker tolerance도 병행 확인
- pybind marshal/boundary 시간이 native 대상 구간의 `<=15%`
- native batch kernel이 Python 대상 구간보다 `>=1.5x`

`>=2.5x`와 boundary `<=10%`는 release 권장 목표로 유지한다. fast-track 최소 기준에 미달하면 C++ 범위를 넓히지 않고 한 번만 API/packing을 재설계하며, parity 또는 속도가 계속 미달하면 native spike를 NO-GO로 종료한다.

### P4 — conservative relation prefilter

P4는 P3가 fixed-work parity와 최소 speed gate를 통과하고 남은 시간이 있을 때만 수행한다. P3만으로 GO/NO-GO 판단이 가능하면 생략할 수 있다.

- layer/suffix AABB로 두 방향 obstruction 가능성을 batch 판정한다.
- 확정적으로 disjoint인 pair만 Python exact call에서 제외한다.
- 모든 애매한 pair는 기존 Shapely path로 보낸다.
- prefilter ON/OFF를 독립 variant로 유지한다.

종료 조건:

- randomized/boundary differential test에서 false-free 0
- fixed-work placement/objective/serialization parity 100%
- exact relation calls와 Shapely time이 실질적으로 감소
- cache/RSS 증가가 gate 내

### P5 — repair/ALNS 통합과 dominance

- `generate_insertion_candidates()`에 backend seam을 추가한다.
- config는 `python`, `native`를 명시적으로 구분한다.
- production default는 모든 gate 전까지 `python`으로 유지한다.
- native는 import/runtime 실패 시 입력 snapshot 또는 Python reference로 안전하게 복귀한다.
- frozen anchor보다 나쁜 candidate는 설치하지 않는다.

필수 telemetry:

- requested/actual backend
- native availability와 fallback reason
- pack/bind/prepare/exact-resolve/finalize 시간
- candidate/pair/UNKNOWN/definitely-free 수
- copied bytes와 peak native RSS
- native exception, deadline exit, fallback count
- anchor/final objective 및 digest

종료 조건:

- focused/full unittest PASS
- forced import/OSError/C++ exception/invalid output에서 Stage 5 incumbent 보존
- native-on/off fixed-work parity
- fast-track에서는 Python reference/저장된 incumbent fallback 보존
- production 승격 전 별도 dominance gate에서 original anchor 대비 loss 0

### P6 — packaging/ABI

P6는 P3/P5 spike가 GO일 때만 시작한다. 현재 builder는 `.py`만 허용하므로 native 승격 시 의도적으로 allowlist를 확장한다. speed/parity GO 전에는 submission builder와 archive allowlist를 수정하지 않는다.

- 허용 binary는 `solver/_ogc_native*.so` 한 종류와 정확한 SHA-256 manifest로 제한한다.
- `.o`, CMake cache, source, debug symbol, tests, data는 archive에서 제외한다.
- Ubuntu 24.04 x86_64 CPython 3.12에서 release binary를 빌드하고 strip한다.
- `ldd`, `readelf`, GLIBC/GLIBCXX symbols, RPATH/RUNPATH, transitive dependency를 검사한다.
- repository parent, network, compiler, absolute path 없이 clean extraction smoke를 수행한다.

필수 smoke:

1. native present 정상 실행
2. native member 제거 후 Python fallback
3. 잘못된 architecture/손상 binary의 `ImportError`/`OSError` fallback
4. Gurobi absent + native present
5. Gurobi absent + native absent
6. tiny/normal timelimit, public stdout/stderr silence

종료 조건:

- archive `<=15MB`
- duplicate/unsafe/extra member 0
- native present/absent 모두 Stage 5
- binary ABI/dependency 검사 PASS

### P7 — fixed-time 품질 검증

fast-track에서는 장시간 matrix 대신 제한된 end-to-end smoke만 수행한다. fixed-work P3 gate를 통과하지 못하면 이 smoke도 실행하지 않는다.

1. `prob_25.json`, 20~30초, 같은 seed, Python/native 교차 실행
2. 필요하면 `prob_22.json` 한 개만 같은 조건으로 추가
3. Stage 5, objective parity, elapsed, ALNS iterations와 native telemetry 기록
4. §0.1의 120초 objective와 직접 비교하지 않고 이번 Python/native pair끼리만 비교
5. 외부 CPU-bound process가 있으면 fixed-time 수치를 판정에서 제외하고 fixed-work 결과만 사용

hard-10 60초 dev 20회, hard-10 180초 3-seed final 60회, daily-40은 모두 별도 release 승인 대상이며 fast-track 세션에서 자동 실행하지 않는다.

## 7. 테스트 계획

### 7.1 단위 및 differential

- fit bounds: 음수 anchor, 경계 일치, 회전별 range
- time candidates: release, due, same-day handoff, `P=0 -> dwell=1`
- position anchors: wall/contact, fractional floor/ceil, dedup first occurrence
- interval index: half-open overlap와 same entry
- fragmentation: 완전 분리/접촉/중첩 interval
- assignment delta: empty bay 포함, floored Z2, preference Z3
- stable score: 동일 total의 canonical tie 전 필드
- overflow: block/time/coordinate 범위의 `int64` 안전성
- randomized Python/native candidate parity
- four-state FREE/I_OUTER/K_OUTER/SEPARATE boundary corpus

### 7.2 통합

- one-repair fixed quota native-on/off identical snapshot
- multi-block regret repair candidate/order/commit digest parity
- ALNS one-iteration same RNG state와 fixed quota parity
- deadline 중 prepare/exact/finalize 각 지점의 identity rollback
- native exception/invalid buffer/invalid verdict fallback
- native cache cold/warm repeated run 결정성
- official checker Stage 5와 objective parity

### 7.3 native 안정성

- ASan/UBSan fixture suite
- malformed buffer length/dtype/stride fuzz
- repeated module create/destroy/import
- memory leak/RSS plateau
- no Python callback while GIL released
- optimized build deterministic replay

## 8. 측정 및 승격 gate

### 8.1 처리량 gate

fast-track spike GO 최소 조건:

- fixed-work semantic parity 100%
- native target kernel이 Python 대상 구간보다 `>=1.5x`
- pybind marshal/boundary time `<=15%` of accelerated region
- native import/runtime exception과 crash 0
- 짧은 smoke에서 Stage 5와 objective parity 유지

release 권장 조건은 별도로 유지한다.

- native target kernel warm median speedup `>=2.5x`
- hard-10 repair seconds/iteration median `>=25%` 감소
- official-like cold process end-to-end median speedup `>=15%`
- pybind marshal/boundary time `<=10%` of accelerated region
- cold import/initialization `<=100ms` 또는 60초 budget의 `<=1%` 중 더 엄격한 값
- peak RSS 증가 `<=256MiB`, 전체 peak `<=2GiB`

### 8.2 correctness hard blocker

- Stage 5 전 실행 100%
- objective parity `<=1e-6`
- public exception/outer timeout/checker failure 0
- validated-best trace regression 0
- native crash 0
- anchor/fallback digest mismatch 0

### 8.3 품질 gate

fast-track smoke는 GO/NO-GO 진단이며 production 승격 증거가 아니다.

- Python/native pair의 Stage 5, objective parity, elapsed, iterations를 보고
- 처리량 증가가 없거나 명확한 objective 악화가 있으면 benchmark-only 또는 NO-GO
- §0.1의 120초 표와 새 smoke objective를 직접 비교하지 않음

production release에서는 다음 원래 품질 gate를 모두 적용한다.

- original 대비 `Loss = 0`, worst relative delta `<=0`
- 개선 instance가 하나 이상 존재
- W/T/L, Borda, median/p90/worst, Z1/Z2/Z3, anytime integral 보고
- 추가 iterations/candidates가 new-best로 전환됐는지 보고
- 특정 instance/seed의 중대 회귀를 평균으로 상쇄하지 않음

baseline-dominance가 구현되어 있다면 loss가 발생하는 것 자체가 결함이다. loss를 통계적 변동으로 승인하지 않는다.

### 8.4 승격 결정

fast-track spike는 다음을 모두 만족하면 `GO`다.

1. P0-lite PASS 또는 PASS-WITH-WAIVER
2. 실제 fixed-work fixture와 Python/native parity 100%
3. kernel speedup `>=1.5x`, boundary `<=15%`
4. import/runtime fallback과 focused Stage 5 smoke PASS

GO는 P5/P6와 release matrix를 진행할 가치가 있다는 뜻일 뿐이다. 다음을 모두 통과할 때만 production default를 native-preferred로 변경할 수 있다.

1. P0-lite와 P1~P6 완료
2. native-on/off fixed-work parity
3. release end-to-end speed gate
4. final hard-10 quality gate와 loss 0
5. archive/ABI/clean extraction gate
6. 별도 사용자 승인

속도만 개선되고 objective가 개선되지 않으면 native는 benchmark-only로 유지한다. 속도 또는 안정성 gate가 실패하면 Python reference가 production default로 남는다.

## 9. 예상 변경 파일

| 파일 | 책임 |
|---|---|
| `native/ogc_native/*` | C++ kernel, binding, build |
| `baseline/solver/native_repair.py` | lazy import, pack, exact resolve, fallback |
| `baseline/solver/construct.py` | repair candidate backend seam; reference 유지 |
| `baseline/solver/neighborhoods.py` | repair backend 전달과 identity rollback |
| `baseline/solver/runtime.py` | native phase/counter telemetry |
| `baseline/solver/entry.py` 또는 config 위치 | benchmark-only backend 선택 |
| `experiments/ogc_sage/benchmark_ogc_sage.py` | source/binary hash와 native metrics |
| `scripts/build_native_linux.sh` | official-like release build |
| `scripts/verify_native_binary.py` | ELF/ABI/dependency/size 검사 |
| `scripts/build_submission_zip.py` | exact `.so` allowlist와 hash |
| `baseline/tests/test_native_*.py` | parity/fallback/package/native 안정성 |

다음 파일은 수정 금지다.

- `baseline/utils.py`
- `baseline/baseline_greedy.py`
- training instance/data

retime/MIP/interlock/destroy 정책 변경은 별도 알고리즘 단계로 분리한다.

## 10. 위험과 중단 조건

| 위험 | 탐지 | 대응/중단 |
|---|---|---|
| exact geometry 의미 drift | randomized + checker differential | C++ exact 이관 금지, UNKNOWN을 Python으로 반환 |
| tie-break/부동소수점 drift | bit/hex score와 candidate order 비교 | 연산 순서 고정, fast-math 금지 |
| pybind 변환비가 이득 상쇄 | pack/bind/kernel phase time | fast-track boundary >15%이면 한 번만 batch 재설계; release 목표는 10% |
| native 가속 후 탐색 궤적 악화 | embedded anchor와 W/T/L | anchor strict fallback, Loss 0 요구 |
| deadline 초과 | C++ steady-clock telemetry, outer timeout | bounded polling, current repair identity rollback |
| segfault/UB | ASan/UBSan/fuzz/repeat | 하나라도 재현되면 native release 차단 |
| Ubuntu ABI 불일치 | clean container import, ldd/readelf | 같은 target에서 rebuild; fallback만으로 승인하지 않음 |
| archive 초과/금지 파일 | exact allowlist/size/hash | strip 또는 native default off |
| 메모리 증가 | RSS/cache telemetry | cache cap 축소 또는 patch 중단 |
| 속도만 있고 품질 개선 없음 | fixed-time final gate | benchmark-only 유지 |

## 11. 실행 및 승인 경계

마감 대응 fast-track 세션에서는 다음을 한 번에 진행할 수 있다.

- P0-lite source/environment audit
- 기존 artifact 재사용과 fixed-work fixture 1개 생성
- isolated/project-local pybind11 build dependency 설치
- 좁은 C++ batch kernel과 Python adapter 구현
- focused parity/fallback/Stage 5 tests
- `prob_25.json` 20~30초 Python/native smoke
- 필요 시 `prob_22.json` 20~30초 smoke 1회 추가

다음은 별도 승인 없이는 수행하지 않는다.

- §0.1의 120초 5개 재실행
- hard-10 60초 dev 20회
- 60-run final matrix
- daily-40 추가 실행
- production native default ON
- submission archive 교체
- exact geometry C++ 재작성
- commit/push

P3 fixed-work parity 또는 최소 speed gate가 실패하면 P4 이후를 시작하지 않는다. 전체 60회 final과 daily-40 matrix는 명시적 승인 없이는 실행하지 않는다.

## 12. 완료 정의

### 12.1 fast-track spike 완료

다음 증거가 있으면 이번 fast-track을 완료하고 native `GO`, `NO-GO`, `BENCHMARK-ONLY` 중 하나를 판정한다.

- actual HEAD/dirty/environment와 P0-lite 판정
- §0.1 raw artifact 유무와 waiver
- 실제 repair fixed-work fixture/hash
- Python/native candidate/order/score/top-3/serialization parity
- pack/bind/kernel/exact-resolve/finalize timing
- native import/runtime fallback과 focused Stage 5 결과
- 가능한 경우 `prob_25`, 선택적 `prob_22` 짧은 smoke
- 변경 파일, 테스트, 남은 release blocker

### 12.2 production release 완료

다음 증거가 모두 존재할 때만 production release를 완료로 판정한다.

- frozen source/config/dataset/native-binary fingerprint
- 실제 repair fixture와 Python/native fixed-work parity artifact/hash
- target/boundary/cold-start/RSS 프로파일
- sanitizer/fuzz/differential 결과
- native present/absent/wrong-ABI/Gurobi-absent smoke
- exact archive allowlist/size/hash/clean extraction 결과
- hard-10 180초 3-seed 60-run paired summary
- original 대비 Loss 0와 checker correctness hard blocker 전부 PASS
- production default 승인 또는 benchmark-only/rollback 결정

실패 결과도 삭제하거나 덮어쓰지 않고 immutable artifact로 보존하며, 어느 P 단계로 돌아가야 하는지 기록한다.

## 13. Fast-track 실행 기록

실행 세션은 이 절에 다음을 추가한다.

- 실행 일시, branch/HEAD/dirty state
- P0-lite `PASS` 또는 `PASS-WITH-WAIVER`
- 재사용한 120초 결과의 raw artifact 경로/hash 또는 waiver
- 구현한 native 범위와 파일 목록
- Python/native API와 backend default
- fixed-work parity 결과
- pack/bind/kernel/exact-resolve/finalize 시간과 speedup
- import/runtime failure와 Stage 5 fallback 결과
- 제한된 end-to-end smoke 결과
- `GO`, `NO-GO`, `BENCHMARK-ONLY` 판정
- production release 전에 남은 최소 작업

### 2026-07-16 — P0-lite

#### 판정과 범위

**P0-lite PASS**

- 실행 시각: `2026-07-16 02:02:15 KST (+0900)`.
- 이번 실행은 P0-lite source/environment/native 계약 감사, 빠른 Python Stage 5 smoke, 기존 120초 raw 탐색과 checksum 검증만 수행했다.
- 코드, build 설정, training data를 수정하지 않았고 120초 재실행, original baseline replay, 장시간 benchmark를 실행하지 않았다. native 구현 범위도 아직 없다.
- 이번 P0-lite가 변경한 파일은 실행 기록을 추가한 본 계획 문서 `docs/implementation/sol/11_PYBIND_REPAIR_KERNEL_ACCELERATION_PLAN.md` 한 개뿐이다.
- fixed-work parity, native 성능, native fallback은 P0-lite 범위가 아니므로 `NOT RUN / N/A`다. production default는 계속 Python reference다.

#### actual branch, source와 dirty state

- branch/HEAD: `codex/performance-optimization-plan` / `302f9e8f0d3cd5eba8f68f633d1a2ba9013396aa`; `origin/codex/performance-optimization-plan` 대비 ahead `3`, behind `0`.
- P0-lite 시작 전 dirty state는 기존 사용자 변경 `M docs/OGC2026_Problem_Analysis.md`와 untracked `dist/`뿐이었다. 둘 다 reset/restore/checkout/cleanup하거나 수정하지 않았다.
- 현재 solver의 마지막 source 변경 commit은 `c67631128d9715655db1a248530ba1d55b1ffd56` (`fix(solver): preserve ALNS prefix past iteration cap`)이다. `baseline/solver/**/*.py`의 경로순 per-file SHA-256 행을 다시 SHA-256한 aggregate는 `315c7177809acc4c39ff4bbbea219db507ab785c52556ac61ce3db013b09d555`다.
- production benchmark config SHA-256은 `571a5e2fbdc1de294daa76fde7fbbb7fe17e489477bc3c4e33213e506d4d958f`다. 기본 계약은 seed `20260710`, assignment/constructor/retime/LNS ON, candidate MIP/interlock/exact-Z1-skip OFF, constructor `eager_regret`, neighborhood `legacy`다.
- hard-10 manifest `docs/implementation/sol/performance/HARD10_MANIFEST.json` SHA-256은 `5499cf993f5018dceca0464dfcaf0c328e129cb7ed4946c79cc4e1860127d3b7`이고 frozen 순서는 `prob_38, 23, 40, 25, 27, 39, 21, 33, 28, 24`다.
- official daily-40 dataset canonical SHA-256은 `c093fb98b17c714ea5b9c8c829d4bce24dd378f0b558196ce0961feb68b34e1f`다. `baseline/utils.py` / `baseline/baseline_greedy.py` SHA-256은 각각 `d45aaeafdce8bf80d59d097f655c43313a4951bed43b6628e3b1cf62d4876a94` / `8ec2cc816b35b6507a9407bc9f893140a9d1b5e0892af92a2dbac2f91b32103b`로 유지됐다.

#### environment, native 제출과 ABI 계약

- local: macOS `26.5.2` build `25F84`, arm64, Apple M4, physical/logical CPU `10/10`, memory `25,769,803,776` bytes.
- runtime: `/opt/homebrew/Caskroom/miniforge/base/envs/ogc-2026/bin/python`, CPython `3.12.13`, Shapely `2.1.2`, Gurobi `13.0.2`; local SOABI / extension suffix는 `cpython-312-darwin` / `.cpython-312-darwin.so`다.
- native 제출은 허용된다. C/C++ binary는 Ubuntu 24.04에서 사전 컴파일해 zip에 포함해야 하며 server-side compilation은 없다. target은 Ubuntu 24.04 LTS, x86_64 AMD Ryzen Threadripper PRO 9955WX, CPython 3.12, 최대 4 cores/16GB, no network/no parent-directory access다.
- 따라서 local macOS arm64 `.so`는 제출 ABI가 아니다. native release binary는 Ubuntu 24.04 x86_64 CPython 3.12에서 별도로 build하고 ELF/ABI/dependency/clean-extraction gate를 통과해야 한다. 이 target build 검증은 P0-lite가 아니라 spike GO 이후 P6/release blocker로 남는다.

#### 외부 CPU-bound process audit

- `ps aux` snapshot에서 이번 task 밖 Python unittest PID `67431` (`python -m unittest tests.test_alns.S3ExtensionTests tests.test_budget_entry.BudgetTests.test_s3_extension_segment_cannot_extend_parent_absolute_deadline`)가 약 `86.6% CPU`, macOS `StorageManagementService` PID `2362`가 약 `82.8% CPU`, Storage extension PID `2361`이 약 `39.5% CPU`로 관측됐다.
- 어떤 process도 종료하거나 변경하지 않았다. P0-lite에서는 fixed-time/성능 판정을 하지 않았으므로 gate를 차단하지 않는다. 후속 timing gate 전에는 독점 부하 window를 다시 감사해야 한다.

#### 빠른 Python Stage 5 smoke

- 명령 의미: `baseline`에서 public `myalgorithm.algorithm()`을 tracked `example_B2_b10.json`, timelimit `2.0s`로 호출하고 현재 `baseline/utils.py` official checker로 검증했다.
- 결과: `feasible=true`, Stage `5`, violations `[]`, `(Z1,Z2,Z3,total)=(0,14,46,1018)`, captured public stdout/stderr `""/""`; PASS.

#### §0.1 120초 raw artifact

- exact raw를 발견했으므로 evidence waiver를 사용하지 않는다.
- raw: `artifacts/ogc_sage/step9/user-prob21-25-120s-rerun-20260716/raw.jsonl`, SHA-256 `698f68ccad5af46a9e768cd1dd0e8ee6575974fc8876973d393addbef17709fb`.
- summary: `artifacts/ogc_sage/step9/user-prob21-25-120s-rerun-20260716/summary.json`, SHA-256 `b1e22b574c834bd7de758df74e5e3d8da780dcc9024bf1b49256cadb43bf3908`.
- checksum manifest: `artifacts/ogc_sage/step9/user-prob21-25-120s-rerun-20260716/SHA256SUMS`, SHA-256 `cf12aed757f3a5668ff6d05b98ffb36080308bdf306aada7861e47096602dacc`; `shasum -a 256 -c SHA256SUMS`에서 raw/summary 모두 `OK`.
- artifact identity는 source `8e8acb166843ba156a65e29ff4976f4aba4e52eb`, config `571a5e2fbdc1de294daa76fde7fbbb7fe17e489477bc3c4e33213e506d4d958f`, dataset `c093fb98b17c714ea5b9c8c829d4bce24dd378f0b558196ce0961feb68b34e1f`, seed `20260710`, environment macOS 26.5.2 arm64 / Python 3.12.13 / Shapely 2.1.2 / Gurobi 13.0.2다.
- 다섯 record는 모두 Stage 5이며 §0.1의 elapsed/objective/Z1/Z2/Z3/ALNS iterations/MIP repair `0`과 일치한다.
- raw source `8e8acb...`는 현재 solver source `c676311...`보다 이전이다. 따라서 이 artifact는 §0.1이 정한 120초 예산 사용과 ALNS 활동의 운영 증거로만 재사용하며, 현재 source에서 생성할 native 결과와 objective를 직접 비교하지 않는다.

#### blocker와 다음 phase

- P0-lite blocker: 없음.
- 다음 phase 진입: **가능**. 다만 P1 fixture/golden identity는 현재 HEAD와 solver source `c676311...`에서 새로 고정해야 한다. 이번 task에서는 P1을 시작하지 않았다.
- production release 잔여 blocker: target Ubuntu 24.04 x86_64 CPython 3.12 build/ABI, fixed-work parity, speed/boundary, native failure fallback, packaging과 final quality gate 전부 미실행이다.

### 2026-07-16 — P1

#### 판정과 범위

**P1 PASS**

- 기록 시각: `2026-07-16 02:13:03 KST (+0900)`.
- P0-lite PASS와 현재 HEAD/source 일치를 확인한 뒤, frozen hard-10의 `prob_25.json` 한 개에서 current Python reference의 quota-bounded heuristic repair fixture와 golden trace를 새로 만들었다.
- production solver, algorithm/config/default, native build, `baseline/utils.py`, `baseline/baseline_greedy.py`, training data는 변경하지 않았다. 장시간 profiler, fixed-time benchmark, native 구현·성능 판정은 실행하지 않았다.
- 추가한 `experiments/ogc_sage/generate_native_repair_fixture.py`는 production 호출 경로 밖의 evidence harness다. 임시 wrapper로 기존 Python 함수 호출을 관측하며 실행 종료 후 원상 복구한다. harness SHA-256은 `114394ce6022444a490029e3a012258a6d9ea8f64f98c765a2c6b0099573e3cf`다.
- P1 시작 시 기존 dirty state인 사용자 변경 `M docs/OGC2026_Problem_Analysis.md`, untracked `dist/`, P0-lite 기록이 추가된 본 계획 문서를 보존했다. 어떤 기존 파일/process도 reset, restore, checkout, cleanup, 종료하지 않았다.

#### source와 fixture identity

- branch/HEAD: `codex/performance-optimization-plan` / `302f9e8f0d3cd5eba8f68f633d1a2ba9013396aa`; solver source commit / aggregate SHA-256은 `c67631128d9715655db1a248530ba1d55b1ffd56` / `315c7177809acc4c39ff4bbbea219db507ab785c52556ac61ce3db013b09d555`다.
- instance: `data/train/prob_25.json`, SHA-256 `bc05276709763260f8412c37742106d34f44991e0d6ecf163c5e183727057fb5`; dataset SHA-256 `c093fb98b17c714ea5b9c8c829d4bce24dd378f0b558196ce0961feb68b34e1f`다.
- source snapshot은 current Python `build_safe_candidate()` 결과다. official checker Stage `5`, violations `[]`, `(Z1,Z2,Z3,total)=(54706,6615,0,36495517)`, placement/serialization SHA-256은 `3c9d8d0f3b0e3bfc11bdb6bc3df0d2c56c3769f293b1db3b08f7a754c320de46` / `881a6922d2039b71b0a7e5461fd009aefb92abd331fa8af44eb69fc42e1838cf`다.
- destroy는 `TardyChainDestroy`, seed `20260710`, size `4`이며 ids는 `[98,97,99,96]`이다. 같은 순서의 current placements는 `[98,1,0,3,1,1298,1315]`, `[97,1,0,0,0,1273,1298]`, `[99,1,0,11,0,1315,1332]`, `[96,1,0,9,1,1250,1273]`이다. row schema는 `[block,bay,orient,x,y,entry,exit]`다.
- retained placement는 `96`개이고 SHA-256은 `3604f40b414c907b1a5e63b059c08986db394d5dfc0a187fc9450f9182e8b8fd`다. 전체 retained rows, raw bay loads, boundary ids, state version `0`은 fixture에 보존했다.
- fixed-work caps는 seed `20260710`, `time_cap=12`, `escalated_time_cap=32`, `anchor_cap=48`, `lattice_cap=512`, `max_profiles=1`, `max_candidate_attempts=32`, `selection_policy=eager_regret`, `regret_depth=3`이다. `10.0s`는 deadline guard일 뿐 성능 표본이나 work 양 판정에 사용하지 않았다.

#### candidate/order/score/top-3 golden

- repair round별 남은 block의 sorted order를 그대로 호출해 candidate call `10`개를 기록했다. raw enumeration `320`, fit 이후 evaluation `330`(각 call의 current rollback 평가 `10`개 포함), exact 통과 score `102`, 반환 top-3 `30`, production `candidates_generated=30`이다.
- exact relation query는 generation `376` + commit `18` = `394`개다. state는 `SEPARATE 228 / FREE 166`, cache 결과는 `miss 192 / hit 202`이며 모든 query row/order/verdict가 golden에 있다.
- 아래 top-3는 각 call의 canonical order이며 `row@total_delta(float.hex)` 형식이다. full tardiness/assignment/fragmentation/canonical tie, pre/post exact 순서, return order, query 연결과 per-call trace digest는 golden에 보존했다.

| call | round:block | before/eval/accepted/relation | canonical top-3 |
|---:|---:|---:|---|
| 0 | `0:96` | `32/33/11/32` | `[96,1,0,25,1,18,41]@342(0x1.5600000000000p+8)`; `[96,1,0,25,2,18,41]@342(0x1.5600000000000p+8)`; `[96,1,0,9,1,1250,1273]@822086(0x1.9168c00000000p+19)` |
| 1 | `0:97` | `32/33/13/32` | `[97,1,0,0,13,18,43]@111(0x1.bc00000000000p+6)`; `[97,1,0,0,17,18,43]@111(0x1.bc00000000000p+6)`; `[97,1,0,0,0,1273,1298]@835195(0x1.97cf600000000p+19)` |
| 2 | `0:98` | `32/33/15/32` | `[98,1,0,3,13,3,20]@67(0x1.0c00000000000p+6)`; `[98,1,0,3,22,3,20]@67(0x1.0c00000000000p+6)`; `[98,1,0,3,1,1298,1315]@859830(0x1.a3d6c00000000p+19)` |
| 3 | `0:99` | `32/33/16/32` | `[99,1,0,27,0,26,43]@120(0x1.e000000000000p+6)`; `[99,1,0,27,4,26,43]@120(0x1.e000000000000p+6)`; `[99,1,0,11,0,1315,1332]@856548(0x1.a23c800000000p+19)` |
| 4 | `1:96` | `32/33/3/38` | `[96,1,0,63,1,18,41]@341(0x1.5500000000000p+8)`; `[96,1,0,63,12,18,41]@341(0x1.5500000000000p+8)`; `[96,1,0,9,1,1250,1273]@822085(0x1.9168a00000000p+19)` |
| 5 | `1:97` | `32/33/13/44` | `[97,1,0,0,13,20,45]@110(0x1.b800000000000p+6)`; `[97,1,0,0,17,20,45]@110(0x1.b800000000000p+6)`; `[97,1,0,0,0,1273,1298]@835194(0x1.97cf400000000p+19)` |
| 6 | `1:99` | `32/33/16/32` | `[99,1,0,27,0,26,43]@119(0x1.dc00000000000p+6)`; `[99,1,0,27,4,26,43]@119(0x1.dc00000000000p+6)`; `[99,1,0,11,0,1315,1332]@856547(0x1.a23c600000000p+19)` |
| 7 | `2:96` | `32/33/3/40` | `[96,1,0,63,1,18,41]@342(0x1.5600000000000p+8)`; `[96,1,0,63,12,18,41]@342(0x1.5600000000000p+8)`; `[96,1,0,9,1,1250,1273]@822086(0x1.9168c00000000p+19)` |
| 8 | `2:97` | `32/33/9/52` | `[97,1,0,0,13,20,45]@111(0x1.bc00000000000p+6)`; `[97,1,0,0,17,20,45]@111(0x1.bc00000000000p+6)`; `[97,1,0,0,0,1273,1298]@835195(0x1.97cf600000000p+19)` |
| 9 | `3:96` | `32/33/3/42` | `[96,1,0,63,1,18,41]@341(0x1.5500000000000p+8)`; `[96,1,0,63,12,18,41]@341(0x1.5500000000000p+8)`; `[96,1,0,9,1,1250,1273]@822085(0x1.9168a00000000p+19)` |

#### commit과 serialization golden

| commit | selected row / score | state rows SHA-256 after | commit SHA-256 |
|---:|---|---|---|
| 0 | `[98,1,0,3,13,3,20] / 67 (0x1.0c00000000000p+6)` | `2a43603d5e0adcc955257e8376e8e3c1d06fc76645b45de5f05de8f4d6c974ed` | `4721a26404faadcb9f18f685612d48e3b386a11e4316440c9d62008c596f809a` |
| 1 | `[99,1,0,27,0,26,43] / 119 (0x1.dc00000000000p+6)` | `cd2507b7e029142d87ceebf80c2de2ba503460bb3aec3ae1f02bcfc9f7cd4e2f` | `4a62ad999843342ec86eb570afd757ae27fa973633b3d08f50d0c3ee85bc37bd` |
| 2 | `[97,1,0,0,13,20,45] / 111 (0x1.bc00000000000p+6)` | `963ff1cf0a70dbf5eca837b6967571530e64a6d49019e2ee5cf949304664ee88` | `0d44fb107dbef0cbdd20ea46f6db65df6045522701188cc56f98a581f274eca2` |
| 3 | `[96,1,0,63,1,18,41] / 341 (0x1.5500000000000p+8)` | `fad9e1182f7031ba1c0f4ce8a827ad22b3afd260691fc9eb650f97fc82442766` | `e219562194c2502e1d03948af7f43249b131298ff9d073e713a13889335845e7` |

- repair result는 `FEASIBLE`, changed ids `[96,97,98,99]`, objective delta `-3,373,019 (float.hex -0x1.9bbed80000000p+21)`이다.
- final official checker는 Stage `5`, violations `[]`, `(Z1,Z2,Z3,total)=(49649,6615,0,33122498)`이다. final placement SHA-256은 `fad9e1182f7031ba1c0f4ce8a827ad22b3afd260691fc9eb650f97fc82442766`, canonical serialization SHA-256은 `5c17361d4b230d6dd59c9dda3d3dae74378a114f21fa7009ca004733c679c7a7`다.

#### artifact와 검증

- artifact root: `artifacts/ogc_sage/step9/pybind-p1-repair-fixture-20260716`.
- `fixture.json` SHA-256 `7e33b178f9cedc63189eafb41cccf167cdea685005bb550ddef4e34fdf5d4df7`.
- `golden.json` SHA-256 `ebb609131a3fec773ffe302d761cb6860cd8e786d06112c654b7758798db15b1`.
- `summary.json` SHA-256 `8e53264c40b70a4eef5ef712bee8cfea21ce7a88a506fe747ada0158e8055150`.
- `SHA256SUMS` SHA-256 `9f738721aa4d89068b7c37e5fcc25cf3f1091dc439ddb2d49b3bf1273e9e738f`; `shasum -a 256 -c SHA256SUMS`에서 세 JSON 모두 `OK`다.
- 별도 `/tmp` output 두 번과 최종 artifact를 생성해 fixture/golden/summary가 바이트 단위로 동일함을 확인했다. 이 replay는 동일 quota work이며 timing 비교가 아니다.
- focused constructor/geometry/neighborhood/ALNS integration unittest는 `42/42 PASS`; harness `py_compile`과 `git diff --check`도 PASS했다. `baseline/utils.py` / `baseline/baseline_greedy.py` SHA-256은 P0와 같은 `d45aaeafdce8bf80d59d097f655c43313a4951bed43b6628e3b1cf62d4876a94` / `8ec2cc816b35b6507a9407bc9f893140a9d1b5e0892af92a2dbac2f91b32103b`다.

#### blocker와 다음 phase

- P1 blocker: 없음. Python candidate/order/score/top-3/commit/serialization golden이 현재 HEAD/source에 고정됐다.
- Python/native parity와 native 성능은 P1 범위가 아니므로 `NOT RUN / N/A`다. production default는 계속 Python reference다.
- 다음 phase 진입: **가능**. 다만 이번 task에서는 P2 build/binding scaffold나 그 이후 phase를 시작하지 않았다. commit/push도 실행하지 않았다.

### 2026-07-16 — P2

#### 판정과 범위

**P2 PASS**

- P2는 pybind11/C++20의 empty deterministic module과 Python lazy-import/fallback seam만 구현했다. C++에는 `empty_kernel_info()`만 있으며 candidate enumeration, score, relation, exact geometry, ALNS/destroy/acceptance/cadence, retime/MIP/interlock 또는 constructor 정책은 이관하거나 변경하지 않았다.
- 새 source: `native/ogc_native/CMakeLists.txt`, `native/ogc_native/src/{bindings.cpp,repair_kernel.hpp,repair_kernel.cpp,types.hpp}`, `native/ogc_native/third_party/{PYBIND11.lock.json,pybind11-2.13.6.tar.gz}`, `baseline/solver/native_repair.py`, `scripts/build_native_{local.py,linux.sh}`, `baseline/tests/test_native_import_fallback.py`.
- CMake와 local helper 모두 C++20 / `-O3 -DNDEBUG` / hidden visibility를 명시한다. `-ffast-math`와 `-march=native`는 금지하고 CMake는 해당 flag가 전달되면 실패한다. import는 stdout/stderr를 쓰지 않으며 worker/thread를 생성하지 않는다.
- pybind11 `2.13.6` source tarball은 repository에 vendored 되어 offline build가 가능하다. SHA-256은 `ba6af10348c12b24e92fa086b39cfba0eff619b61ac77c406167d813b096d39a`, license는 BSD-3-Clause이며 source tarball의 `LICENSE`와 lock file에 보존했다. Linux helper는 network를 사용하지 않고 hash를 확인한 뒤 caller-selected build directory에서 release binary를 만든다.
- `baseline/solver/native_repair.py`는 production path에 연결하지 않았다. 기본은 계속 Python reference이며 native absent, `ImportError`/`OSError`, native runtime exception이면 조용히 Python reference로 복귀한다. native output은 incumbent를 설치하지 않으므로 current checker-valid incumbent를 바꾸지 않는다.
- 기존 사용자 변경 `docs/OGC2026_Problem_Analysis.md`, `dist/`, P1 harness 및 기존 P0/P1 계획 기록을 보존했다. `baseline/utils.py`, `baseline/baseline_greedy.py`, training data와 solver algorithm files는 수정하지 않았다. build output은 `/tmp/ogc-native-p2-build-cpython312`에만 두어 source/submission 범위에 섞지 않았다.

#### local build/import 및 fallback/Stage 5 검증

- local build: macOS Darwin `25.5.0` arm64, CPython `3.12.13`, Apple clang 21에서 `scripts/build_native_local.py --output-dir /tmp/ogc-native-p2-build-cpython312`을 실행했다. module은 `_ogc_native.cpython-312-darwin.so`, SHA-256 `5d8f7796be0b23d9734ed197e98765bd9470d43e4b86a19f2310a9770c66477a`다.
- present import: explicit `OGC_NATIVE_MODULE_DIR`에서 `empty_kernel_info() == {"name":"ogc_native_empty_kernel","api_version":1}`. captured stdout/stderr는 모두 empty이고 import 전후 active thread 목록은 동일(1 main thread)이다.
- absent/import-failure: native module이 없는 상태와 forced `OSError("bad ABI")`에서 silent Python fallback을 검증했다. fallback snapshot은 official checker `feasible=true`, Stage `5`다.
- focused tests: `tests.test_native_import_fallback`과 public Stage 5/silence smoke 3개를 실행하여 `5/5 PASS`; `py_compile`과 `git diff --check`도 PASS다.
- Python/native fixed-work parity와 speedup은 **N/A**다. P2에는 candidate kernel이 없으며 fixed-work parity 실패 시 fixed-time으로 진행하지 않는 규칙을 유지한다. P7 fixed-time smoke도 실행하지 않았다.

#### artifact, blocker와 다음 phase

- evidence: `artifacts/ogc_sage/step9/pybind-p2-build-scaffold-20260716/summary.json`, SHA-256 `da8ea31af4211328bb41547055a6423625455e2356047db24a8b4ab9d86342b8`. local `.so`와 build cache는 artifact/source tree가 아닌 `/tmp`에만 있다.
- local macOS arm64 result는 P2 scaffold spike 증거일 뿐 Ubuntu 24.04 x86_64 CPython 3.12 release-ready 증거가 아니다. Ubuntu ABI/build, ELF dependency/clean extraction, sanitizer/fuzz, P3 fixed-work parity/speed, P5 dominance/fallback integration, P6 packaging은 계속 release blocker다.
- P2 blocker: 없음. 다음 phase 진입은 **가능**이나, 이 task에서는 P3 또는 candidate algorithm을 시작하지 않았다. commit/push도 실행하지 않았다.

### 2026-07-16 — P3

#### 판정과 범위

**P3 NO-GO 후보 (fixed-work correctness gate FAIL)**

- P3는 benchmark-only pybind batch spike를 추가했다. Python에서 Shapely가 만든 canonical union vertex 순서와 fit bounds를 `StaticProblem`으로 pack하고, C++이 time/wall/contact/lattice anchor, first-occurrence dedup, packed retained-state co-presence, fragmentation 및 assignment/tardiness delta를 계산한다. `GeometryKernel.relation()`의 exact verdict, rollback column, commit/serializer/checker는 Python에 남겼으며 production backend/default에는 연결하지 않았다.
- 구현 파일은 `native/ogc_native/src/{types.hpp,repair_kernel.hpp,repair_kernel.cpp,bindings.cpp}`, `baseline/solver/native_repair.py`, `experiments/ogc_sage/verify_native_p3_fixture.py`다. `-ffast-math`, `-march=native`, thread, exact C++ geometry, P4/P5/P6/P7 및 fixed-time benchmark는 실행하지 않았다.
- P1 fixture의 10 call/state-version을 replay하여 candidate row/order, exact-pair batch, score bit/hex 및 top-3를 Python reference와 비교했다. initial state의 32 row order는 일치했지만 retained pair가 둘 이상인 state에서 Python의 exact-result-dependent candidate collection과 C++ batch finalization의 top-3가 달랐다. 따라서 fixed-work parity는 100%가 아니며 Stage 5 fixed-time smoke는 규칙대로 실행하지 않았다.

#### timing audit와 결과

- timing 직전 `ps` audit에서 외부 Python PID `97701` 약 `98.7% CPU`, `ApplicationsStorageExtension` PID `2377` 약 `43.2% CPU`가 관측됐다. 어떤 process도 종료/변경하지 않았다.
- 동일 부하 아래 15회 paired prepare 표본은 median pack/bind+prepare `46,375ns`, C++ kernel `26,042ns`, boundary `20,375ns` (accelerated prepare region의 약 `43.9%`)였다. call별 exact-resolve/finalize도 artifact에 분리 저장했다. 외부 부하와 parity 실패 때문에 이 수치는 speed gate용 신뢰 표본이 아니며 Python-target speedup은 `NOT VALID / NOT COMPUTED`다. boundary `<=15%`도 충족하지 않는다.
- fixed-work parity FAIL, boundary FAIL, speed gate 판정 불가이므로 GO가 아니다. API/packing 재설계는 **사용하지 않았다**(허용된 1회는 소진하지 않음). 이번 fast-track task에서는 범위를 넓히지 않고 NO-GO 후보로 종료한다.

#### artifact와 최소 검증

- artifact: `artifacts/ogc_sage/step9/pybind-p3-candidate-kernel-20260716/summary.json`, SHA-256 `c9088ecd77d3ef727aabd3f2159d9e1333d29581a4536c349ad569b24ab43d50`; checksum manifest SHA-256 `a5adf3daaba6b0aba8c9eb3c1816092a9c841032d9cd570f2f027419e7b1e25a`, artifact directory에서 `shasum -a 256 -c SHA256SUMS` PASS.
- native local build/import PASS (macOS arm64 CPython 3.12; release evidence 아님). focused import/runtime fallback tests `2/2 PASS`; `py_compile` 및 `git diff --check` PASS. No native exception/crash was observed in the final differential run; checker/Stage 5 integration is not run after fixed-work parity failure.

#### blocker와 다음 phase

- blocker: exact verdict 이후의 batch acceptance/top-3 semantics mismatch, 그리고 external CPU load 때문에 speed gate가 신뢰 불가하다.
- 다음 phase 진입: **불가**. P4 이후, production default 변경, packaging, fixed-time/long matrix는 진행하지 않는다. Python reference remains default.

### 2026-07-16 — P3-fix-1

#### 구현 범위와 redesign 사용

**FIXED (P3-retry-1의 독립 gate 판정 대기)**

- P3에서 허용된 API/packing redesign **1회**를 이 항목에서 사용했고, 추가 redesign은 하지 않는다. `PackedState`는 version별로 C++ 소유 contiguous row/load/per-bay interval 표현을 보유한다. Python의 block-id placement 순서는 time 후보용으로 보존하고, bay interval은 `IndexedSolutionState`와 동일한 `(entry, exit, block_id)` 순서로 한 번만 pack한다.
- `NativeRepairAdapter`는 `(id(state), state.version)` cache를 통해 같은 version의 여러 `prepare`에서 Python row-list 생성과 pybind row cast를 다시 하지 않는다. 이 seam은 benchmark-only이며 production 기본 backend는 계속 Python이다.
- C++ anchor와 retained-pair 순서는 packed bay interval을 사용하며, time overlap 뒤 Python `co_present()`와 동일하다. time/position 후보의 Python first-occurrence/dedup 순서, multi-bay preference/guide tie-break, pair query의 first-failure exact 소비 순서를 맞췄다. Shapely `GeometryKernel.relation()`은 Python에서 계속 순서대로 호출한다.
- finalize는 더 이상 enumeration 중 첫 세 accepted 후보에서 멈추지 않고, 모든 exact-free 후보를 Python canonical key `(total, fragmentation, guide_penalty, exit, entry, bay, orient, x, y, block)`로 stable sort해 top-3를 선택한다. rollback column 및 commit/serializer는 기존 Python 경로에 남겼다.

#### fixed-work replay

- P1 fixture/golden 10 call 모두 row/order, pair offsets/order, 실제 소비 exact query order/count, score float hex, state version 및 canonical top-3가 일치했다. call별 consumed exact query 수는 `32,32,32,32,38,44,32,40,52,42`이며 모두 golden과 동일하다.
- call 0 top-3는 `[96,1,0,25,1,18,41]`, `[96,1,0,25,2,18,41]`, 기존 rollback `[96,1,0,9,1,1250,1273]`로 일치한다.
- call 4의 row 4는 `[96,1,0,20,1,18,41]`이고 retained pair order는 `[98,1,0,3,13,3,20]`, `[0,1,0,17,1,18,39]`로 Python과 일치한다.
- P1 golden의 Python commit/rollback/serialization trace는 유지되며, native top-3가 해당 commit 선택과 같은 후보를 제공한다. 이 task는 production commit 경로를 연결하지 않았다.

#### preliminary timing 및 검증

- macOS arm64 / CPython 3.12 local build의 packed prepare 9회 median은 total `29,667ns`, C++ kernel `27,750ns`, boundary `1,875ns`(약 `6.3%`)였다. 첫 cache-pack sample은 boundary `22,709ns`이며 median에서 제외하지 않았다. `StorageManagementService` 약 `68.3% CPU`, WindowServer 약 `28.5%`, Storage extension 약 `27.6%` 외부 부하가 관측되어 이 수치는 preliminary only이며 speedup gate 판정에는 사용하지 않는다.
- artifact: `artifacts/ogc_sage/step9/pybind-p3-fix1-20260716/summary.json`, SHA-256 `26388c753db2bf6d5d677894d5a912e066639ad065dc26c03507b92039d38381`; `SHA256SUMS` PASS.
- focused native import/fallback `2/2 PASS`, native local build/import PASS, `py_compile`, `git diff --check` PASS. fixed-time/120s/hard/final/daily matrix, P4+, commit/push는 이 task 범위 밖으로 실행하지 않았다.

#### 잔여 blocker

- P3-retry-1은 생성 가능하다. 다만 packed timing의 외부 CPU 부하가 해소된 audit window에서 speedup 및 final P3 gate를 별도로 판정해야 하며, 그 전에는 P3 GO 또는 P4 진입을 선언하지 않는다. Ubuntu 24.04 x86_64 CPython 3.12 ABI/build, production integration, fixed-time matrix와 release gates도 계속 blocker다.

### 2026-07-16 — P3-retry-1

#### 최종 P3 gate 판정

**P3 GO (fixed-work fast-track gate)**

- production default는 계속 Python이며, P4 또는 fixed-time/120초/hard/final/daily matrix는 실행하지 않았다. P3-fix-1에서 소진한 API/packing redesign 외의 algorithm/API/packing/production 변경도 하지 않았다.
- P1 fixture/golden의 10개 call을 현재 HEAD `302f9e8f0d3cd5eba8f68f633d1a2ba9013396aa`에서 재생했다. candidate row/order, full pair offset/order, 실제 소비한 exact relation query order/count (`32,32,32,32,38,44,32,40,52,42`), score float hex/bit, canonical top-3, state version이 모두 `10/10` 일치했다.
- native top-3가 P1의 네 commit 선택 `[98,1,0,3,13,3,20]`, `[99,1,0,27,0,26,43]`, `[97,1,0,0,13,20,45]`, `[96,1,0,63,1,18,41]`을 모두 제공함을 확인했다. 따라서 기존 Python commit 경로의 final placement SHA-256 `fad9e1182f7031ba1c0f4ce8a827ad22b3afd260691fc9eb650f97fc82442766`, canonical serialization SHA-256 `5c17361d4b230d6dd59c9dda3d3dae74378a114f21fa7009ca004733c679c7a7`, official checker Stage `5` / violations `[]` / total `33122498 (0x1.f968c20000000p+24)`도 그대로다.

#### paired fixed-work timing

- work identity는 P1 call 0의 같은 state/version (`0`), block `96`, current row `[96,1,0,9,1,1250,1273]`, attempt cap `32`다. Python/native 각 8회 warmup 뒤 31회 `Python target → native prepare → exact resolve → finalize` 순서로 교차 측정했다.
- Python target은 `generate_insertion_candidates`에서 `GeometryKernel.relation()` 시간만 뺀 값이다. 따라서 Python candidate/time/position enumeration, fit, indexed-state lookup, score/canonical selection과 rollback-column 작업은 분모에 남기고 exact Shapely는 제외했다. native kernel은 C++ `prepare_candidates`만이며 pack/cache lookup, pybind boundary, exact resolve, finalize는 제외했다.
- median: Python target `577,207ns`, native prepare total `35,042ns`, C++ kernel `31,542ns`, bind/boundary `3,083ns`, exact-resolve `90,458ns`, finalize `34,458ns`다. kernel speedup은 `18.299x`; boundary는 `3,083 / 35,042 = 8.798%`로 각각 `>=1.5x`, `<=15%` gate를 통과했다. fresh state/version의 cold-pack은 total/kernel/boundary `94,000/54,500/39,500ns`로 별도 기록했으며 P3 boundary gate에는 warm same-version prepare median만 사용했다.
- timing 전 audit에는 `ApplicationsStorageExtension 91.3%`, `Storage 45.0%`, `WindowServer 27.5%` CPU가, 후 audit에는 `StorageManagementService 63.2%`, `WindowServer 30.9%`가 있었다. 어떤 process도 종료/변경하지 않았다. 부하는 높았으나 paired fixed work와 robust separation으로 판정 가능했다: Python target p10 `546,957ns`가 native kernel p90 `40,334ns`보다 `13.56x` 크고, 전체 최저/최고도 `544,292 / 132,916 = 4.10x`로 gate보다 충분히 크다.

#### 검증과 artifact

- focused fallback/Stage 5/checker suite는 `11/11 PASS` (`test_native_import_fallback`, `test_construct_integration`, `test_four_state_parity`); exception/crash/checker failure는 `0`이다. local macOS arm64 CPython 3.12 build/import 증거이며 Ubuntu release ABI 증거는 아니다.
- artifact root: `artifacts/ogc_sage/step9/pybind-p3-retry1-20260716`. `summary.json` SHA-256 `f5126e5d8c272ca3e1b95a90d77f46331a0c5afc17fb5c22c9fd9a8f9bf3e0c5`; final gate record와 cold-pack replay provenance는 checksum manifest에 고정했다. 31-repeat warm timing summary가 생성된 harness SHA-256은 `c7d36172662a8dd471ec32dbbbd2688ae5954e510a138346d52523a5dd646fe8`이고, cold-pack instrumentation을 포함한 **current** harness SHA-256은 `cf12d75e171e8422e779343191e326b76ef028b4e4c696cd950ede52c11cdfda`다. `cold-pack.json`은 current harness로 실행한 recorded replay summary SHA-256 `b8fbc36155fa54c157bd1edd802088ed5f2e4540f20efb6f16d9f4ee312f4ee3`, 입력 fixture/golden hash와 exact command를 보존한다.

#### blocker와 다음 phase

- P3 fast-track hard gate blocker는 없다. P4 진입은 **가능**하나 이번 task에서는 실행하지 않았다.
- release blocker는 target Ubuntu 24.04 x86_64 CPython 3.12 ABI/package 검증, production integration/dominance, P4 이후 gate 및 final release gate다. commit/push는 실행하지 않았다.

### 2026-07-16 — P4

#### 진입 판정과 보수적 범위

**P4 PASS (benchmark-only conservative relation prefilter)**

- P3-retry-1의 fixed-work GO를 재확인했다. P3 call-0 warm median에서 exact-resolve는 `90,458ns`로 native prepare `35,042ns`와 finalize `34,458ns`보다 큰 remaining path였다.
- 구현 전 P1 실제 generate relation trace `376`개를 layer/suffix AABB shadow predicate로 분석했다. 양 방향 모두 AABB가 확정 disjoint인 `130`개(`34.574468%`)만 DEFINITELY_FREE 후보였고 `246`개는 UNKNOWN이었다. trace false-free는 `0`이었으며, 9 orientation pair의 contact offset + deterministic random translation `713`개에서도 false-free `0`이었다. 따라서 P3만으로 종료하지 않고 P4를 구현할 근거가 충분했다.
- 변경 범위는 `native/ogc_native/src/{types.hpp,repair_kernel.hpp,repair_kernel.cpp,bindings.cpp}`, `baseline/solver/native_repair.py`, benchmark-only `experiments/ogc_sage/verify_native_p4_prefilter.py`다. P5/P6/P7, production backend/default, solver algorithm, commit/rollback/serializer/checker, packaging/allowlist 및 submission builder는 변경하지 않았다.

#### 의미론과 ON/OFF 계약

- Python/Shapely가 만든 `ShapeInfo.layer_aabbs`/`suffix_aabbs`만 `StaticProblem`에 복사한다. C++은 polygon/union을 재생성하지 않고 `prefilter_enabled=False`(P3 호환 기본값)일 때는 모든 pair를 UNKNOWN으로 둔다.
- ON일 때도 C++은 두 방향의 layer-AABB 대 suffix-AABB scan에 **어느 한 방향이라도 overlap 가능성**이 있으면 UNKNOWN을 반환한다. 두 방향 모두 가능한 obstruction이 없을 때만 `pair_definitely_free=true`를 붙인다. strict-open AABB 비교는 기존 `GeometryKernel._obstructs_relative()`의 guard와 동일하다.
- `NativeRepairAdapter(..., prefilter_enabled=False|True)`가 독립 benchmark variant다. adapter는 DEFINITELY_FREE만 verdict `True`로 채우고, 모든 UNKNOWN은 기존 순서대로 Python `GeometryKernel.relation()`/Shapely에 보낸다. candidate/pair order, score/canonical top-3, rollback column, Python transactional commit, serialization과 production default는 그대로다.

#### fixed-work, false-free 및 Stage 5

- ON/OFF 모두 P1 10 call에서 candidate row/order, pair offset/order, float-hex score, canonical top-3와 state version이 `10/10 PASS`다. OFF는 기존 `376` exact calls를 그대로 수행하고 ON은 `246`만 exact oracle로 보냈다. all prepared P4 pair 중 표시된 `210`개와 별도 actual-C++ randomized/boundary `713`개 모두 false-free `0`이다.
- 네 commit 선택이 native top-3에 모두 존재했다. 선택 row를 unchanged Python commit path로 replay한 결과 state digest 4/4, final placement SHA-256 `fad9e1182f7031ba1c0f4ce8a827ad22b3afd260691fc9eb650f97fc82442766`, canonical serialization SHA-256 `5c17361d4b230d6dd59c9dda3d3dae74378a114f21fa7009ca004733c679c7a7`, final objective `33122498`, official checker Stage `5` / violations `[]`가 golden과 일치했다.
- current native source로 prefilter OFF P3 verifier도 P1 10/10 parity PASS했다. focused fallback/Stage 5/checker suite (`test_native_import_fallback`, `test_construct_integration`, `test_four_state_parity`)는 `11/11 PASS`; native exception/crash/checker failure는 `0`이다.

#### ON/OFF timing, cache와 RSS

- work는 P1의 10 candidate call/state 전체다. variant별 8 warmup 뒤 31회 `OFF → ON` paired repeat을 실행했다. prepare는 cache lookup/pybind call, exact-resolve는 adapter의 exact resolver, finalize는 rollback re-evaluation을 제외한 native finalize다.
- median은 OFF→ON으로 prepare `559,793→567,542ns`, C++ kernel `336,124→340,873ns`, exact-resolve `880,043→695,378ns`, finalize `11,250→11,292ns`였다. exact relation call은 `376→246` (`-34.574468%`), exact-resolve는 `-20.983%`, 세 구간 합은 `-12.189%`다.
- warm relation cache currsize는 OFF `192`, ON `123`으로 증가하지 않았다. macOS `ru_maxrss`는 paired timing 중 `58,884,096→58,900,480` bytes (`+16,384`)로 release 증가 한도 `256MiB` 이내다. 이는 local macOS arm64/CPython 3.12 spike 증거이며 Ubuntu release ABI 증거는 아니다.

#### artifact, blocker와 다음 phase

- artifact root: `artifacts/ogc_sage/step9/pybind-p4-prefilter-20260716`. entry shadow `summary.json` SHA-256 `cf1d6493f61ce92dcb5af779ecba762b977b38c5d677c97273dc29701a3219ef`, final actual-C++ differential `p4-native-differential-summary.json` SHA-256 `afb0be0cce40561565a92d51bfef10c4ec28708aa29cfa9313354c6e7fb7f0de`, final gate `gate.json` SHA-256 `9b8839e02fcffe95ce6405805d1b3fac86f48bfaa2e108182c09da45cdfc7380`다. immutable intermediate implementation/full-trace summaries도 같은 root에 보존했다.
- P4 fast-track blocker는 없다. **P5 진입은 가능**하지만 이 task에서는 P5를 시작하지 않았다. target Ubuntu 24.04 x86_64 CPython 3.12 ABI/package, P5 production integration/original-anchor dominance, P6 packaging, P7 fixed-time quality gates는 계속 release blocker다. fixed-time/120s/hard/final/daily matrix, commit/push는 실행하지 않았다.

### 2026-07-16 — P5

#### 최종 판정과 backend 계약

**P5 PASS (explicit native repair integration; production default remains Python)**

- branch/HEAD는 `codex/performance-optimization-plan` / `302f9e8f0d3cd5eba8f68f633d1a2ba9013396aa`이며 commit/push는 하지 않았다. 기존 사용자 변경 `docs/OGC2026_Problem_Analysis.md`, untracked `dist/`, P0~P4 source/artifact는 보존했다. `baseline/utils.py`, `baseline/baseline_greedy.py`, training data, submission builder/allowlist/packaging은 수정하지 않았다.
- `ConstructorConfig.repair_backend`와 `SubmissionConfig.repair_backend`는 정확히 `python|native`만 받는다. alias `candidate_backend`는 fixture/harness 호환용이며 canonical 값은 `repair_backend`다. `from_defaults()`와 production `SubmissionConfig`의 기본값은 **`python`**이고 Python default는 native import/availability probe조차 수행하지 않는다. `native_prefilter_enabled`도 별도 explicit flag이며 P4 ON/OFF 관찰값으로 telemetry에 남는다.
- `generate_insertion_candidates()`는 동일-input Python reference closure와 one-repair `NativeRepairSession` 사이의 seam을 받는다. native opt-in은 `NativeRepairAdapter.prepare → Python GeometryKernel.relation/Shapely UNKNOWN resolve → native finalize`만 수행한다. C++은 P4의 `DEFINITELY_FREE` 외 geometry 판정을 하지 않으며 rollback column, `evaluate_insert` commit recheck, transactional insert, serializer, full checker는 계속 Python 권위다.
- import/build unavailable, `ImportError`/`OSError`, C++/binding exception, malformed row/pair/score/verdict buffer, native deadline check은 모두 같은 input의 Python generator로 재시도한다. Python reference도 실패하면 empty candidates만 반환하여 `heuristic_repair()`가 immutable original snapshot을 반환한다. partial native state는 install 경로에 존재하지 않는다.
- native opt-in repair는 frozen original repair anchor보다 objective가 나빠지면 `DOMINANCE_REJECTED`로 anchor identity를 반환한다. Python-default ALNS의 기존 worse-current acceptance 의미론은 변경하지 않았다. ALNS best install은 기존의 full checker + strict `IncumbentStore` 경로에 남는다.

#### 구현 및 telemetry

- 변경: `baseline/solver/{native_repair.py,construct.py,neighborhoods.py,alns.py,runtime.py,entry.py}`, `baseline/tests/test_native_repair_integration.py`, `experiments/ogc_sage/verify_native_p5_integration.py`.
- `NativeRepairSession` telemetry는 requested/actual backend, native availability, fallback reason/count, native exception/invalid-output/deadline count, pack/bind/prepare/exact-resolve/finalize ns, candidate/pair/UNKNOWN/DEFINITELY_FREE/exact-call count, static+state packed byte estimate, process peak RSS, anchor/final objective/digest, dominance result를 `RepairResult.telemetry`에 기록한다. `AlnsMetrics.repair_events`는 iteration당 bounded copy를 보존한다. solver public output/operations에는 영향을 주지 않는다.
- P1 `prob_25` fixed repair의 final P5 telemetry: native OFF/ON 각각 candidate rows `320`, returned top-3 columns `30`, prepared pairs `608`, successful native calls `10/10`, exception/deadline/fallback `0/0/0`이었다. OFF는 UNKNOWN `608`, exact Python calls `376`, DEFINITELY_FREE `0`; ON은 UNKNOWN `398`, exact Python calls `246`, DEFINITELY_FREE `210`이었다. ON/OFF는 모두 final placement `fad9e118…242766`, canonical serialization `5c17361d…796f7a7`, total `33,122,498`, official checker Stage `5` / violations `[]`다. copied-byte estimate는 `315,839`; local process peak RSS는 OFF/ON `57,360,384 / 58,441,728` bytes였다. 이 timing/RSS는 macOS arm64 spike telemetry이지 release throughput/ABI 판정이 아니다.

#### fixed-work parity, faults, Stage 5 및 tests

- current native build는 `scripts/build_native_local.py --output-dir /tmp/ogc-native-p5-build-cpython312`으로 local macOS arm64 CPython 3.12에서 build했다. Ubuntu release binary가 아니다.
- current P4 actual-C++ differential을 P5 전에 다시 실행했다. P1 10-call OFF/ON row/order/pair/score/top-3 parity, 4 commit/placement/objective/serialization trace가 모두 PASS했고, actual C++ randomized/boundary false-free `0`이다. copied P4 summary SHA-256은 `f3c45c858be0b5eb9a648d23a2225379a3d00db9886a433853acbd84ada5819d`다.
- P5 integration verifier는 Python default와 native OFF/ON의 P1 repair를 actual seam으로 replay했다. 세 variant 모두 placement/objective/serialization parity 및 Stage 5 PASS; default telemetry는 `requested_backend=actual_backend=python`, `native_available=null`, `native_calls=0`; opt-in OFF/ON은 `actual_backend=native`, `native_available=true`, successful calls `10`이다.
- focused fault/default/dominance suite는 `7/7 PASS`: forced import failure, OSError, synthetic C++ exception, invalid native output, deadline fallback, default no-probe, frozen-anchor dominance rejection이다. 각 repair fallback 결과와 original anchor는 Stage 5이며 fallback result가 anchor보다 나빠지는 경우는 `0`이다.
- full regression: `/opt/homebrew/Caskroom/miniforge/base/envs/ogc-2026/bin/python -m unittest discover -s tests -p 'test_*.py' -v` → **204/204 PASS**. focused native suite는 native module present/absent 양쪽에서 PASS했다. `py_compile` 및 `git diff --check`도 PASS다.

#### immutable evidence와 후속 경계

- final artifact root: `artifacts/ogc_sage/step9/pybind-p5-integration-20260716`.
- final `gate.json`: **PASS**, SHA-256 `7b2da1a4cdd17d11a89810698d11c4fe116344c293da79796d5c395578184650`; `SHA256SUMS` SHA-256 `544afc161bf4e2d4514b4e518298e7d5d2f1375d5f4dbbbc0b2ef6af59610f2d`; manifest verification reports all four JSON evidence files `OK`.
- initial P5 harness attempt exposed only a test-isolation issue: an explicitly supplied local module directory prevented the import-failure test from actually failing import. Its immutable FAIL evidence was preserved (not overwritten) at `artifacts/ogc_sage/step9/pybind-p5-integration-20260716-failed-attempt-1`; the test now explicitly mocks import failure and final gate is the root above.
- deadline injection was then strengthened to pass through the actual `heuristic_repair()` native seam and verify its returned anchor at Stage 5. The preceding PASS evidence was preserved (not overwritten) at `artifacts/ogc_sage/step9/pybind-p5-integration-20260716-superseded-predeadline-stage5`; the final gate root above contains the strengthened 7/7 suite.
- **P6 entry is possible**, but P6 itself was not run: Ubuntu 24.04 x86_64 CPython 3.12 build/ELF dependency/clean extraction/allowlist work remains blocked by the P6 scope boundary. P7 fixed-time 20–30s smoke, 120s/hard/final/daily matrices were not run. Native remains benchmark-only and production default remains Python until those independent gates and approval complete.

### 2026-07-16 — P6-local-mac-smoke (user-scoped)

#### 범위 변경과 판정

**LOCAL PASS (macOS C++ 동작 확인; Ubuntu P6 PASS 아님)**

- 사용자가 실행 중 현재 환경이 Mac이므로 Ubuntu target에 맞추지 않고 C++ 모듈이 로컬에서 실제 동작하는 수준으로 범위를 축소했다. 따라서 Ubuntu 24.04 x86_64/ELF/GLIBC/GLIBCXX/archive/clean-extraction gate는 실행하지 않았고 이 기록을 P6 release PASS로 사용하지 않는다.
- branch/HEAD는 `codex/performance-optimization-plan` / `302f9e8f0d3cd5eba8f68f633d1a2ba9013396aa`, 기록 시각은 `2026-07-16 03:31:12 KST (+0900)`다. 기존 dirty state와 사용자 문서 `docs/OGC2026_Problem_Analysis.md`, untracked `dist/`를 보존했고 `baseline/utils.py`, `baseline/baseline_greedy.py`, training data는 수정하지 않았다.
- Ubuntu/package용으로 시작했던 임시 스크립트 변경은 최신 범위에 불필요하여 작업 중 `apply_patch`로 제거했다. 최종 source 변경은 이 실행 기록과 로컬 evidence뿐이며 production builder/default는 바꾸지 않았다.

#### local build/import와 actual native seam

- macOS `26.5.2` arm64, CPython `3.12.13` (`SOABI=cpython-312-darwin`), Apple clang `21.0.0`에서 vendored pybind11 `2.13.6`만 사용해 `-std=c++20 -O3 -DNDEBUG -fvisibility=hidden` module을 새 `/tmp/ogc-native-p6-local-build-20260716`에 build했다.
- `strip -x` 후 `_ogc_native.cpython-312-darwin.so`는 arm64 Mach-O, `282,128` bytes, SHA-256 `95c1026ff3096ec9b5580d8a4564792964367cf4248b963371ba769fdbaa2563`다. direct dependency는 macOS system `libc++.1.dylib`와 `libSystem.B.dylib`뿐이고 RPATH 및 repository 절대경로 문자열은 없었다.
- 실제 `solver.native_repair` lazy import는 `empty_kernel_info() == {"name":"ogc_native_empty_kernel","api_version":1}`을 반환했고 captured stdout/stderr `""/""`, import 전후 thread 목록 동일이었다.
- 새 binary로 P4 actual-C++ verifier를 재실행했다. OFF/ON fixed-work parity 모두 PASS, randomized/boundary `713` pair의 false-free `0`, P1 4-commit placement/objective/serialization trace와 official checker Stage `5`가 일치했다. P4 summary SHA-256은 `6683e0615cabac155e20a3b6f658dd47fca3cc9524aeb7a428305e2f3514a460`다.
- P5 actual seam은 native prefilter OFF/ON 각각 `native_successes=10/10`, exception/invalid/deadline/fallback `0/0/0/0`이었다. 둘 다 final placement `fad9e118…242766`, serialization `5c17361d…796f7a7`, objective `33,122,498`, official checker Stage `5` / violations `[]`다. Python default는 `requested_backend=actual_backend=python`, native calls `0`으로 유지됐다.

#### tests, evidence와 남은 경계

- focused native-present `18/18 PASS`, native-absent/fault/default/dominance `9/9 PASS`, full regression `204/204 PASS` (`3.390s`), `git diff --check` PASS다.
- local artifact root: `artifacts/ogc_sage/step9/pybind-p6-local-macos-20260716`. `summary.json` SHA-256 `3c27d4051407509109f4cb4580b79f691bd9d1b124bff8758603ef544eecbab6`; `gate.json` SHA-256 `d26ec9883d555ccc46643d2491972e404329e0d584983a3ee567d3e3d60f458a`; `SHA256SUMS` SHA-256 `ef0a53b0eedb4096f36aa632fbd9a9f3961948e4a8b42130bb892f4b7dab6a6d`이고 root에서 전체 manifest 검증이 PASS했다. gate status는 의도적으로 `LOCAL_PASS`다.
- 남은 release 경계: Ubuntu 24.04 x86_64 CPython 3.12 target build/import, ELF dependency/version, native archive allowlist/15MB/manifest, clean extraction smoke는 `NOT RUN / USER SCOPED OUT`이다. P7 fixed-time, 120초, hard/final/daily matrix도 시작하지 않았고 commit/push를 실행하지 않았다.

### 2026-07-16 — P7 fixed-time local macOS quality

#### 판정과 실행 계약

**P7 INCONCLUSIVE (BENCHMARK-ONLY; external CPU load invalidated fixed-time interpretation)**

- branch/HEAD는 `codex/performance-optimization-plan` / `302f9e8f0d3cd5eba8f68f633d1a2ba9013396aa`다. P3 fixed-work `GO`, P4/P5 `PASS`, P6 local macOS `LOCAL_PASS`와 P6 manifest 전체 checksum PASS를 진입 조건으로 재확인했다.
- `data/train/prob_25.json`(SHA-256 `bc05276709763260f8412c37742106d34f44991e0d6ecf163c5e183727057fb5`)만 seed `20260710`, fixed time `25s`, 순서 `Python default → explicit native`로 각각 정확히 한 번 실행했다. warmup/retry와 `prob_22`, 120초/hard/final/daily/original-baseline 실행은 없었다.
- Python은 `SubmissionConfig.from_defaults()` 그대로이며 config SHA-256은 `f18a0d3ee7a5f2c76ef59bad6f53c0144288b2134fc3252d59ac9dc477f5924a`다. native는 같은 production config에서 `repair_backend=native`, `native_prefilter_enabled=true`만 명시적으로 opt-in했다. production default는 계속 Python이다.
- native는 P6 binary `artifacts/ogc_sage/step9/pybind-p6-local-macos-20260716/native/_ogc_native.cpython-312-darwin.so`, SHA-256 `95c1026ff3096ec9b5580d8a4564792964367cf4248b963371ba769fdbaa2563`를 checksum 확인 후 직접 load했다. 로드된 module path가 이 exact file과 일치했다.

#### 두 run의 correctness, objective와 telemetry

- Python: official checker Stage `5`, violations `[]`, `(Z1,Z2,Z3,total)=(21,475,357,2,364,14,371,462)`, elapsed `23.783536s`, ALNS iterations `18`, exit `DEADLINE`. `requested/actual=python/python` 18 events, `native_available=null`, native calls/successes/fallback/exception/invalid/deadline `0/0/0/0/0/0`으로 native probe/call 없는 default 경로를 확인했다.
- native: official checker Stage `5`, violations `[]`, `(Z1,Z2,Z3,total)=(30,762,1,323,2,610,20,571,777)`, elapsed `23.942882s`, ALNS iterations `10`, exit `DEADLINE`. `requested_backend=native` / `actual_backend=native` 10 events, availability true 10/10, calls/successes `99/98`, fallback `1`(`native_deadline_after_prepare`), native exception/invalid `0/0`, native deadline `1`이다. prefilter ON에서 candidate rows `780,757`, pairs `5,051,307`, DEFINITELY_FREE `3,377,759`, UNKNOWN `1,673,548`, Python exact relation calls `746,851`을 기록했다. phase 합은 pack/bind/prepare/exact-resolve/finalize `0.001323/0.028971/0.799290/7.864314/0.195993s`다. UNKNOWN exact 권위는 Python `GeometryKernel.relation()`/Shapely로 유지됐다.
- 두 run 모두 public/harness exception `0`, serializable output, internal/checker objective exact match, non-increasing incumbent trace를 만족했다. Python output/placement/serialization SHA-256은 `b8243d48edd47ac525c3a044c54ffcfee627cdd46a6e0d415dbfaa959b2ad634` / `987d620b996ba76b7e7ec6341b7d503dcb4781674fce6bbb37531bc543627bbf` / `89d622c8e3a1428ad78d3b3716330e26efb247824495a04635c8a6416fcbbad2`; native는 `837c1081bf1179de7fe3d4ec5d9e7ee28b1db5ef038089eca02c5630f1393145` / `d62f7b16e60f8c07e9c2f41f4e2c669a4862ae2594f7925a0dba8fbd95d2d902` / `20093add4b41f3fb1cff44d7a600dae7286d7d4f61c4bc30fc910f5e9176f5b4`다.

#### CPU audit, quality delta와 gate

- timing 직전 read-only `ps aux` audit에서 외부 `ApplicationsStorageExtension` PID `2377` `84.2%`, `StorageManagementService` PID `2362` `73.0%` CPU가 관측됐다. 직후에도 PID `2377`이 `63.1%`였다. 어떤 process도 종료하거나 변경하지 않았다.
- 이번 pair의 관측 quality delta는 minimization 기준 `native - Python = +6,200,315`로 native loss다. 그러나 pre/post 모두 material external CPU-bound load가 있어 fixed-time 작업량 자체가 달라졌으므로 elapsed, iterations, speed와 이 objective delta를 품질 gate에 사용하지 않았다. 지시된 부하 정책에 따라 `NO-GO` loss로 확정하지 않고 **P7 INCONCLUSIVE/BENCHMARK-ONLY**로 판정한다. correctness hard gate와 native actual-activation hard gate는 PASS다.
- §0.1 120초 objective나 다른 과거 fixed-time 결과는 비교에 사용하지 않았다. production native default 승격, Ubuntu release-ready 또는 package-ready 주장은 하지 않는다.

#### evidence, 변경과 잔여 경계

- measurement-only harness: `experiments/ogc_sage/verify_native_p7_fixed_time.py`, SHA-256 `48be1e868d1e1aec5083d322a05b7db9b4eb8089d68f7eb7dcbd72025db0625b`. solver algorithm/config/default/API/packing/prefilter/acceptance/serializer/checker는 P7에서 수정하지 않았다.
- artifact root: `artifacts/ogc_sage/step9/pybind-p7-fixed-time-macos-20260716`. `gate.json` SHA-256 `ba263362b4219fed8bd4df60af19fda2785ba0f11c529cae079052f51f524f16`, `comparison.json` SHA-256 `cc2f1919d8f184639f2159e110addbaf0b87923fc0c5ee3655b3f261c954723a`, `SHA256SUMS` SHA-256 `2d30f2b4943670c5c9e594aa7a043e2afd30b11cad1ae13fb86fe260188e59b5`; root에서 전체 manifest verification PASS다.
- harness 내부 `ps` subprocess는 sandbox `PermissionError`로 실패했으며 solver를 실행하지 않은 이 실패 attempt도 `cpu-audit-helper-failed.json`에 보존했다. 즉시 controller의 read-only `ps aux`로 대체했고 두 fixed-time solver run은 각각 한 번뿐이다.
- 기존 사용자 변경 `docs/OGC2026_Problem_Analysis.md`와 untracked `dist/`, P0~P6 변경/artifact를 보존했다. `baseline/utils.py` / `baseline/baseline_greedy.py` / `prob_25.json` SHA-256은 각각 `d45aaeafdce8bf80d59d097f655c43313a4951bed43b6628e3b1cf62d4876a94` / `8ec2cc816b35b6507a9407bc9f893140a9d1b5e0892af92a2dbac2f91b32103b` / `bc05276709763260f8412c37742106d34f44991e0d6ecf163c5e183727057fb5`로 유지됐다. commit/push는 실행하지 않았다.
- 다음 최소 diagnose 입력은 외부 Storage CPU-bound process가 없는 독점 window다. 이 P7 task에서는 후속 run/task를 생성하거나 진행하지 않는다.

### 2026-07-16 — P7 fix1 production-unbounded semantic/cadence

#### 판정과 최소 수정

**P7 FIXED (fixed-work hard gate; fixed-time quality NOT RUN / NOT CLAIMED)**

- P7 진단에서 확인된 `max_candidate_attempts=None` 의미론 차이를 수정했다. capped P3 API는 유지하고, unbounded 경로만 C++ `fitting option × entry` chunk로 준비한 뒤 Python이 UNKNOWN exact verdict를 canonical 순서대로 resolve한다. 한 option에서 exact-free 후보 `3`개를 확보하면 다음 entry를 만들지 않으며, rollback이 없는 construction 호출은 전체 found `3`개에서 Python과 동일하게 fitting search를 종료한다. first pass가 비면 expanded-time row-grid escalation도 같은 chunk seam을 사용한다.
- `guide_penalty`를 C++ prepared schema에 노출하고 native finalize와 Python 재정렬/`CandidateScore.canonical_tie`에 실제 값을 전달했다. native repair API는 scaffold `api_version=1`을 유지하면서 `repair_api_version=2`를 명시 검증하며, 오래된 local binary는 silent Python fallback 대상이다.
- deadline은 Python safe-tail reserve와 같은 margin을 option/entry/16-row exact boundary에서 확인하고, C++ chunk도 16-row마다 `steady_clock` deadline을 확인한다. 이미 exact 승인된 후보는 deadline 뒤에도 보존하고, 최종 rollback column은 Python `evaluate_insert`가 다시 권위 있게 설치한다. import/runtime/invalid output은 same-input Python reference로 fallback하며 exact UNKNOWN 권위는 계속 `GeometryKernel.relation()`/Shapely다.
- production default는 계속 `python`이고 native는 explicit benchmark-only opt-in이다. `baseline/utils.py`, `baseline/baseline_greedy.py`, training data, submission builder/allowlist는 변경하지 않았다.

#### production-unbounded parity, work bound와 fault gate

- P1 `prob_25` source snapshot의 4-block repair를 attempt cap `None`으로 Python, native prefilter OFF/ON 각각 fixed-work replay했다. 세 variant 모두 10 candidate calls의 candidate placement/canonical-tie trace가 완전히 같고, rollback은 `10/10`, final placement `fad9e118…242766`, serialization `5c17361d…c679c7a7`, objective `33,122,498`, official checker Stage `5` / violations `[]`가 일치했다. native OFF/ON은 actual native, successes `10/10`, fallback/exception/invalid/deadline `0/0/0/0`이다.
- 별도 equal-cost synthetic fixture는 두 fitting bays와 두 position을 반환 top-3 안에 강제로 넣었다. Python/native 순서는 guide penalty `0,0,1`로 동일했고 nonzero guide 후보 `1`, rollback 보존 PASS다.
- 진단 fixed-time native 합계 `rows/pairs/Python exact = 780,757 / 5,051,307 / 746,851`에 대해, 새 P1 10-call unbounded fixed-work는 `8,255 / 12,045 / 6,145`, stream chunks `172`다. 서로 다른 workload의 절대 성능 비교가 아니라 production-unbounded inflation 제거용 hard bounds `20,000 / 30,000 / 15,000`과 진단치의 10% 미만을 모두 통과했다.
- deadline fault injection은 before/during/after prepare와 during/after resolve 5개 지점에서 state version 불변, rollback 보존, deadline 관측을 모두 통과했다. 기존 import/OSError/C++ exception/invalid output/default/dominance suite도 `7/7 PASS`다.

#### capped/P4/P5/P6/full 회귀와 evidence

- capped P3 current-source verifier는 candidate/order/score/top-3/state-version parity PASS다. P4 OFF/ON 10-call parity PASS, OFF/ON exact calls `376/246`, randomized/boundary `713` pair false-free `0`다. P5 gate의 Stage 5, P1 digest/objective/serialization, native activation, Python default, conservative prefilter, fallback, dominance 8개 check가 모두 PASS다.
- focused native-present `18/18 PASS`, native-absent `9/9 PASS`, full regression `204/204 PASS` (`3.455s`), `git diff --check` PASS다.
- C++20 `-O3 -DNDEBUG -fvisibility=hidden`으로 CPython 3.12 macOS arm64 binary를 재빌드하고 `strip -x`했다. 새 binary는 `282,160` bytes, SHA-256 `38352e2040e311822ffa591e6d98a0b7754299bc935cd521f9778b508692232d`, direct dependencies는 `/usr/lib/libc++.1.dylib`와 `/usr/lib/libSystem.B.dylib`, import/thread/API smoke PASS다. 이는 local macOS C++ 동작 증거이며 Ubuntu release/ABI/package 증거가 아니다.
- artifact root는 `artifacts/ogc_sage/step9/pybind-p7-fix1-macos-20260716`이다. parity/guide-tie/fault/work-bound, P3/P4/P5 raw summaries, binary/source hashes, gate와 전체 recursive `SHA256SUMS`를 보존한다.
- P7 25초 Python/native pair, `prob_25`/`prob_22` fixed-time solver, 120초/hard/final/daily matrix는 실행하지 않았다. 따라서 이 판정은 fresh P7 fixed-time retry가 가능한 semantic hard gate일 뿐 fixed-time 품질 PASS가 아니다. commit/push도 실행하지 않았다.

### 2026-07-16 — P7 retry 1

#### 판정과 preflight

**P7 RETRY INCONCLUSIVE (BENCHMARK-ONLY; CPU preflight blocked all solver execution)**

- branch/HEAD는 `codex/performance-optimization-plan` / `302f9e8f0d3cd5eba8f68f633d1a2ba9013396aa`로 일치했고 기존 P0-lite~P7 누적 dirty state, 사용자 파일 `docs/OGC2026_Problem_Analysis.md`, `dist/`를 보존했다.
- fix1 `SHA256SUMS` 자체 SHA-256 `706b65146ff5386144ce65e60765f9d39d4a136cc68bcc3dc2e718270b45cbd8`와 manifest 16개 항목 전체를 검증했다. 사용할 macOS CPython 3.12 arm64 binary는 SHA-256 `38352e2040e311822ffa591e6d98a0b7754299bc935cd521f9778b508692232d`, exact module path import, scaffold API `1`, repair API `2`, stdout/stderr silence와 import 전후 MainThread 동일을 확인했다.
- production default는 Python이고 native는 explicit benchmark-only opt-in이라는 계약을 유지했다. `prob_25.json` SHA-256은 `bc05276709763260f8412c37742106d34f44991e0d6ecf163c5e183727057fb5`, seed는 `20260710`, 계획한 budget은 `25s`, 순서는 Python→native였다.

#### CPU blocker와 실행하지 않은 pair

- 고정 25% 규칙의 결정 샘플은 `cpu-preflight-3.json`과 `cpu-preflight-4.json`이다. 명령 시작 간격은 3초였고 `ps` 수집 완료 기준 timestamp 간격은 약 `5.287876s`였다.
- 동일 외부 PID `2377` `ApplicationsStorageExtension`이 `62.4%→89.9%`, PID `166` `WindowServer`가 `33.3%→27.8%`로 두 샘플 모두 지속 CPU-bound였다. 자기 감사 명령은 제외했고 어떤 process도 종료하거나 변경하지 않았다.
- 따라서 규칙대로 fixed-time Python/native solver를 하나도 실행하지 않았다. 두 variant의 Stage 5, objective, elapsed, iterations, placement/serialization digest와 native telemetry는 모두 `NOT RUN / null`, quality delta도 `null`이다. `prob_22`, 120초, hard-10, final/daily, Ubuntu/release/archive도 실행하지 않았다.

#### gate와 evidence

- 품질 열화나 semantic drift를 관측한 실행이 없으므로 NO-GO/FAIL로 확대하지 않고 **P7 RETRY INCONCLUSIVE**다. fixed-time GO는 선언할 수 없고 native는 계속 **BENCHMARK-ONLY**, production default는 Python이다.
- immutable artifact root: `artifacts/ogc_sage/step9/pybind-p7-retry1-macos-20260716`. preflight, raw `ps` stdout/stderr가 포함된 CPU audit, identity/config/input/binary/source hash, NOT-RUN Python/native result, comparison, gate, commands와 recursive `SHA256SUMS`를 보존한다.
- `baseline/utils.py`, `baseline/baseline_greedy.py`, training data, solver/native source를 수정하지 않았고 reset/restore/checkout/clean/stash/commit/push도 실행하지 않았다.

### 2026-07-16 — P7 retry 2

#### 판정과 preflight

**P7 RETRY BLOCKED (terminal repeated external CPU blocker; solver pair NOT RUN)**

- branch/HEAD는 `codex/performance-optimization-plan` / `302f9e8f0d3cd5eba8f68f633d1a2ba9013396aa`로 일치했고 기존 P0-lite~P7 누적 dirty state, 사용자 파일 `docs/OGC2026_Problem_Analysis.md`, `dist/`를 보존했다.
- fix1 `SHA256SUMS` 자체 SHA-256 `706b65146ff5386144ce65e60765f9d39d4a136cc68bcc3dc2e718270b45cbd8`와 16개 항목 전체, retry1 `SHA256SUMS` 자체 SHA-256 `7fb84ae80811a31397d035fcc0d4aa4507a8a3d26fc21e4547d96d6adbb2146f`와 22개 항목 전체를 검증했다.
- 사용할 macOS CPython 3.12 arm64 binary는 SHA-256 `38352e2040e311822ffa591e6d98a0b7754299bc935cd521f9778b508692232d`, exact module path direct import, scaffold API `1`, repair API `2`, stdout/stderr silence와 import 전후 `MainThread` 동일을 확인했다.
- production default는 Python이고 native는 explicit benchmark-only opt-in이다. `prob_25.json` SHA-256 `bc05276709763260f8412c37742106d34f44991e0d6ecf163c5e183727057fb5`, seed `20260710`, 계획 budget `25s`, 순서 Python→native를 고정했으나 CPU gate가 차단했다.

#### CPU audit와 terminal blocker

- threshold `25%`의 성공한 결정 샘플을 정확히 두 번 채취했고 명령 시작 간격은 `3.001851375s`다. sample timestamp는 `04:11:23.905459 KST` / `04:11:26.894623 KST`다.
- 동일 외부 PID `2362` `StorageManagementService`가 `66.7%→63.1%`로 두 샘플 모두 threshold 이상이었다. audit driver/self PID `50713`은 판정에서 제외했고 어떤 process도 종료하거나 변경하지 않았다.
- PID `2362`는 최초 P7 및 retry1의 PID `2377` `ApplicationsStorageExtension`과 동일한 macOS StorageManagement subsystem blocker다. 최초 P7은 PID `2377` `84.2%` pre / `63.1%` post와 PID `2362` `73.0%` pre, retry1은 PID `2377` `62.4%→89.9%`, retry2는 PID `2362` `66.7%→63.1%`로 세 fresh session에서 같은 외부 CPU-bound subsystem이 반복됐다.
- 최초 sandboxed helper pair는 `ps` 실행 전 `PermissionError`로 실패해 CPU sample을 만들지 못했고 raw stderr를 `cpu-audit-driver.json`에 보존했다. 이후 승인된 성공 pair만 결정 샘플이며 추가 성공 sample은 없다.
- 반복-blocker terminal 규칙에 따라 fixed-time Python/native solver는 모두 실행하지 않았다. solver run count `0`; Stage 5, official checker/parity, elapsed, iterations, objective, placement/serialization digest, native actual telemetry/no fallback은 전부 `NOT RUN`이다.

#### gate와 evidence

- 최종 판정은 **P7 RETRY BLOCKED**이며 외부 StorageManagement CPU 상태 변경 없이는 진행할 수 없다. GO/NO-GO 또는 fixed-time 품질 주장을 하지 않고 production default는 Python, native는 benchmark-only로 유지한다.
- immutable artifact root: `artifacts/ogc_sage/step9/pybind-p7-retry2-macos-20260716`. 두 raw CPU audit, 성공/실패 driver streams, preflight/identity/config/input/source/binary hash, NOT-RUN Python/native result, comparison, gate, commands와 recursive `SHA256SUMS`를 보존한다.
- `prob_22`, 120초, hard-10, final/daily, Ubuntu/release/archive는 실행하지 않았다. `baseline/utils.py`, `baseline/baseline_greedy.py`, training data, solver/native source를 수정하지 않았고 reset/restore/checkout/clean/stash/commit/push도 실행하지 않았다.
