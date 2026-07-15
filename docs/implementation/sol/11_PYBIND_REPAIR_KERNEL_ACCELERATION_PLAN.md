# OGC-SAGE pybind11 repair kernel 가속 계획

작성일: 2026-07-16

상태: 구현 전 계획

기준 source: `8e8acb166843ba156a65e29ff4976f4aba4e52eb`

상위 문서: `docs/implementation/sol/10_PERFORMANCE_ACCELERATION_PLAN.md`

성능 근거: `docs/implementation/sol/performance/04_HEURISTIC_REPAIR_ACCELERATION.md`

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

최종 목표는 다음 세 조건을 동시에 만족하는 것이다.

1. 같은 고정 작업량에서 Python reference와 후보·순서·목적함수·직렬화가 동일하다.
2. 공식 유사 cold process에서 end-to-end 처리량이 실제로 개선된다.
3. 같은 instance/seed에서 원본 baseline 해보다 나쁜 결과를 반환하지 않는다.

속도가 빨라졌다는 사실만으로 승격하지 않는다. 추가 처리량이 checker-valid objective 개선으로 이어지고, baseline-dominance와 패키징/ABI gate를 모두 통과해야 한다.

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
- 원본 해를 같은 process 안에서 보존한 뒤 native 결과가 더 좋을 때만 설치한다.

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

native 가속은 원본 baseline 경로를 대체해서는 안 된다. production 승격 전에 별도의 Stage 1 prerequisite로 다음 계약을 먼저 고정한다.

1. 동일 instance/seed에서 frozen original baseline 경로를 먼저 완료한다.
2. 그 operations/objective/digest를 checker-valid anchor로 저장한다.
3. native repair는 남은 시간의 extension에서만 실행한다.
4. native 결과는 official total objective가 anchor보다 strict하게 낮을 때만 설치한다.
5. timeout, 예외, native 부재, 무개선이면 anchor를 반환한다.

native patch와 이 prerequisite 알고리즘 변경은 같은 patch에 섞지 않는다. prerequisite가 통과하지 않으면 native fixed-time 승격 실험을 시작하지 않는다.

## 5. 목표 아키텍처

### 5.1 Python/native 책임 분리

```text
Python
  - Instance/Shapely preprocessing
  - frozen baseline anchor 및 IncumbentStore
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

각 작업 묶음은 하나의 독립 patch와 판정 기록을 갖는다. 앞 gate가 실패하면 다음 묶음을 시작하지 않는다.

### P0 — prerequisite와 기준선 동결

- frozen original anchor가 candidate process 안에서 동일한 objective/operations digest를 만드는지 검증한다.
- current HEAD, config, hard-10 manifest, dataset, Python/Shapely/Gurobi 환경을 기록한다.
- 외부 CPU 부하가 없는 측정 window와 process audit 절차를 고정한다.
- native 제출 허용, Ubuntu 24.04, CPython 3.12, x86_64, 4 cores/16GB 계약을 재확인한다.

종료 조건:

- anchor parity 100%
- original 대비 fallback result loss 0
- source/config/environment fingerprint 고정

### P1 — native 전용 fixed-work fixture

- hard-10의 실제 repair 입력에서 destroyed ids, retained placements, caps, current placement를 fixture로 저장한다.
- easy/medium/hard, cold/warm cache, boundary coordinate를 포함한다.
- quota 기반 Python reference 결과를 golden artifact로 만든다.
- candidate enumeration 전/후, relation query, score, top-3, commit digest를 기록한다.

종료 조건:

- 기존 프로파일의 repair wall 90% 이상 설명
- C++ 이관 대상의 residual Python-exclusive wall 비중과 Amdahl 예상치 기록
- 실제 입력 fixture hash 고정

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
- debug ASan/UBSan build와 stripped release build 분리
- import 시 stdout/stderr 0
- module import만으로 worker/thread를 시작하지 않음

종료 조건:

- Ubuntu 24.04/CPython 3.12에서 deterministic build
- empty kernel normal/import-absent/wrong-ABI fallback smoke PASS
- native 없이도 기존 Python tests와 Stage 5 smoke PASS

### P3 — candidate enumeration/score kernel

- Python에서 pre-extracted canonical vertices와 fit bounds를 native static object에 전달한다.
- time candidates, wall/contact/lattice anchors, first-occurrence dedup을 동일 순서로 이관한다.
- packed retained state에서 co-present와 fragmentation을 계산한다.
- exact relation을 아직 건너뛰지 않고 기존 순서의 pair query를 생성한다.
- Python exact verdict를 받은 후 canonical top-3를 계산한다.

종료 조건:

- 모든 golden fixture에서 candidate rows/order/top-3/score/state version 동일
- float는 `float.hex()` 또는 bit pattern까지 비교하고 checker tolerance도 병행 확인
- pybind marshal/boundary 시간이 native 대상 구간의 `<=10%`
- native batch kernel warm median 권장 `>=2.5x`

미달 시 C++ 범위를 넓히지 않고 API/packing을 재설계하거나 native 계획을 종료한다.

### P4 — conservative relation prefilter

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
- original anchor 대비 loss 0

### P6 — packaging/ABI

현재 builder는 `.py`만 허용하므로 native 승격 시 의도적으로 allowlist를 확장한다.

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

프로파일 결과는 품질 표본에 포함하지 않는다. 모든 paired run은 exclusive-load window에서 교차 실행한다.

1. dev gate: hard-10, 60초, seed `20260710`, Python/native 각 10회, 총 20회
2. seed robustness가 필요하면 `20260711`, `20260712`를 별도 승인 후 추가
3. final gate: hard-10, 180초, seeds `20260710/11/12`, original/native 각 30회, 총 60회
4. daily-40 전체 matrix는 별도 승인 없이는 실행하지 않음

final gate 전에 source/config/dataset/native binary SHA-256을 고정한다. instance/seed마다 variant 순서를 교차한다.

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

- fixed-work semantic parity 100%
- native target kernel warm median speedup 권장 `>=2.5x`
- hard-10 repair seconds/iteration median `>=25%` 감소 권장
- official-like cold process end-to-end median speedup `>=15%`
- pybind marshal/boundary time `<=10%` of accelerated region
- cold import/initialization `<=100ms` 또는 60초 budget의 `<=1%` 중 더 엄격한 값
- peak RSS 증가 `<=256MiB`, 전체 peak `<=2GiB` 권장

### 8.2 correctness hard blocker

- Stage 5 전 실행 100%
- objective parity `<=1e-6`
- public exception/outer timeout/checker failure 0
- validated-best trace regression 0
- native crash 0
- anchor/fallback digest mismatch 0

### 8.3 품질 gate

- original 대비 `Loss = 0`, worst relative delta `<=0`
- 개선 instance가 하나 이상 존재
- W/T/L, Borda, median/p90/worst, Z1/Z2/Z3, anytime integral 보고
- 추가 iterations/candidates가 new-best로 전환됐는지 보고
- 특정 instance/seed의 중대 회귀를 평균으로 상쇄하지 않음

baseline-dominance가 구현되어 있다면 loss가 발생하는 것 자체가 결함이다. loss를 통계적 변동으로 승인하지 않는다.

### 8.4 승격 결정

다음을 모두 통과할 때만 production default를 native-preferred로 변경할 수 있다.

1. P0~P6 완료
2. native-on/off fixed-work parity
3. end-to-end speed gate
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
| pybind 변환비가 이득 상쇄 | pack/bind/kernel phase time | boundary >10%이면 batch 재설계 |
| native 가속 후 탐색 궤적 악화 | embedded anchor와 W/T/L | anchor strict fallback, Loss 0 요구 |
| deadline 초과 | C++ steady-clock telemetry, outer timeout | bounded polling, current repair identity rollback |
| segfault/UB | ASan/UBSan/fuzz/repeat | 하나라도 재현되면 native release 차단 |
| Ubuntu ABI 불일치 | clean container import, ldd/readelf | 같은 target에서 rebuild; fallback만으로 승인하지 않음 |
| archive 초과/금지 파일 | exact allowlist/size/hash | strip 또는 native default off |
| 메모리 증가 | RSS/cache telemetry | cache cap 축소 또는 patch 중단 |
| 속도만 있고 품질 개선 없음 | fixed-time final gate | benchmark-only 유지 |

## 11. 실행 및 승인 경계

이 문서는 구현 순서와 gate만 정의한다. 작성 시점에는 다음을 수행하지 않는다.

- solver/C++ 구현 변경
- pybind11 설치
- native build
- benchmark 실행
- 60-run final matrix
- daily-40 추가 실행
- submission archive 교체
- commit/push

각 P 단계는 직전 결과를 보고 별도 승인 후 진행한다. 특히 전체 60회 final과 daily-40 matrix는 명시적 승인 없이는 실행하지 않는다.

## 12. 완료 정의

다음 증거가 모두 존재할 때 이 계획을 완료로 판정한다.

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
