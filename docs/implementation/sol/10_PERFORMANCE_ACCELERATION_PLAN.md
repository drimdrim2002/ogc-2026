# OGC-SAGE Python/Native 성능 개선 계획

작성일: 2026-07-14
계획 브랜치: `codex/performance-optimization-plan`
기준 브랜치/커밋: `sol-native-implementation` / `5f932a611e8076d01a3f59c1e4e44da350d522e6`

## 1. 목표

현재 solver의 정확성, 제출 안전성, 결정성 계약을 유지하면서 동일 wall-clock 예산 안에 더 많은 유효 탐색을 수행하도록 만든다. 성능 개선 자체가 목적이 아니라, 고정 시간에서 checker가 인정하는 objective를 더 낮추는 것이 최종 목적이다.

진행 순서는 다음과 같이 고정한다.

1. 현재 Python 구현의 재현 가능한 기준선을 고정한다.
2. phase, 함수, 라인, native-call 경계까지 병목을 측정한다.
3. 알고리즘 의미를 바꾸지 않는 Python 구조 개선과 Numba를 작은 단위로 적용한다.
4. 고정 작업량에서 속도 향상과 동등성을, 고정 시간에서 objective 감소 효과를 각각 검증한다.
5. 잔여 병목과 Amdahl 상한이 C++ 이관을 정당화할 때만 pybind11 모듈을 만든다.
6. Python fallback, 패키징, 공식 유사 환경 검증을 통과한 경우에만 native 경로를 제출 후보로 승격한다.

중요한 전제는 “Python 파일이 사전 컴파일되지 않았다”는 사실만으로 병목을 단정하지 않는 것이다. 현재 경로에는 Shapely와 Gurobi처럼 이미 native code에서 실행되는 작업도 있다. Numba와 C++은 Python에서 실제로 시간을 소비하는 수치 커널에만 적용한다.

## 2. 범위와 불변식

### 포함 범위

- `baseline/solver/construct.py`: 후보 시간/위치 생성, 삽입 평가, portfolio 구성
- `baseline/solver/neighborhoods.py`: destroy/repair, 후보 열거, local feasibility
- `baseline/solver/geometry.py`: AABB prefilter, relation cache, Shapely 호출 경계
- `baseline/solver/alns.py`: iteration 처리량, repair/retime 호출 정책과 계측
- `baseline/solver/retime.py`: 모델 조립과 native solver 시간 분리
- `baseline/solver/state.py`: objective/delta 및 상태 조회 비용
- `experiments/ogc_sage/benchmark_ogc_sage.py`: 성능·objective 비교 계측
- Numba 가속 경로와, 조건부 pybind11 native 모듈/패키징

### 변경할 수 없는 계약

- 40/40 인스턴스, 모든 시험 seed/budget에서 Stage 5 feasibility 100%
- internal objective와 checker의 `obj1`, `obj2`, `obj3`, total 상대 오차 `<= 1e-6`
- validated-best trace는 항상 non-increasing
- 같은 seed의 longer-budget regression은 원인 규명 전까지 release blocker
- 예외, native import 실패, optimizer 실패에도 pure-Python safe incumbent 반환
- 공식 `baseline/utils.py`는 수정하지 않음
- wall-clock deadline과 checker reserve를 침범하지 않음
- 성능 측정을 위해 후보를 생략하거나 정답 의미론을 근사한 경우에는 별도 variant로 취급하고, 동등 가속으로 보고하지 않음

## 3. 현재 기준선과 1차 병목 가설

현재 가장 신뢰할 수 있는 기준선은 다음 artifact다.

- source commit: `22b08264977ad83980027adfb92843ece8b8f7ff`
- artifact: `artifacts/ogc_sage/step9/step9-final-20260713T235202Z-22b08264977a-6657431f9374`
- 구성: 40 instances × 2 budgets(60/180초) × 3 seeds = 240 runs
- 결과: Stage 5 240/240, objective parity error 0, exception/timeout/regression 0
- 환경: macOS arm64, Python 3.12.13, Shapely 2.1.2, Gurobi 13.0.2

기준 artifact 이후 현재 HEAD까지 solver package는 바뀌지 않았지만 benchmark/packaging 파일은 바뀌었다. 따라서 구현 착수 시 현재 HEAD에서 기준선을 한 번 재생성하고 config/source hash를 새로 고정한다.

기존 240-run raw timing을 합산한 1차 결과는 다음과 같다.

| phase | 60초 run 합산 wall 비중 | 60초 median | 180초 run 합산 wall 비중 | 180초 median | 판단 |
|---|---:|---:|---:|---:|---|
| LNS repair | 52.4% | 12.034s | 74.1% | 127.819s | 최우선 프로파일/가속 대상 |
| LNS retime | 14.2% | 1.129s | 19.5% | 34.027s | 모델 조립과 Gurobi solve를 분리 측정 |
| constructor | 18.0% | 4.384s | 2.7% | 4.391s | short/medium budget에서 중요 |
| assignment | 7.7% | 1.818s | 1.2% | 1.839s | native solver 비중 확인 후 판단 |
| initial retime | 3.1% | 0.635s | 0.5% | 0.635s | 우선순위 낮음 |
| checker + LNS checker | 2.6% | 0.680s | 1.4% | 2.110s | authoritative oracle이므로 유지 |
| LNS destroy | <0.1% | 0.007s | <0.1% | 0.065s | 가속 대상 아님 |

추가 관찰:

- 60초와 180초 matrix의 constructor profile 1,440개 모두 50,000 candidate cap에 도달했다. 총 후보 시도는 budget별 36,000,000회다.
- 60초 constructor p90/max는 각각 5.609/6.973초이고 deadline/timebox hit는 0이다.
- 60초 run 120개 모두 첫 incumbent 이후 개선했고 median validated-best event 수는 17개다. 180초에서는 55.5개다. 처리량 개선이 objective 개선으로 연결될 여지가 있다.
- `geometry.relation()` cache miss는 Shapely translate/intersection을 호출한다. Shapely 연산 자체는 native지만, 후보별 Python 객체 생성과 호출 경계 비용은 별도 측정이 필요하다.
- 현재 project environment에는 Numba 0.61.0이 있으나 pybind11은 설치되어 있지 않다. C++ 단계에 진입할 때 빌드 의존성과 offline 재현 방법을 명시해야 한다.

위 수치는 phase 수준 증거다. 다음 함수는 코드 구조상 유력 후보일 뿐, 프로파일 전에는 확정 병목으로 부르지 않는다.

1. `neighborhoods.heuristic_repair()` 및 내부의 block × bay/orientation × time × position 열거
2. `construct._candidate_options()`와 `evaluate_insertion()`
3. `generate_position_candidates()`의 anchor/contact/lattice 생성과 중복 제거
4. `neighborhoods._candidate_allows_unchanged()`의 retained placement scan
5. `geometry.GeometryKernel.relation()` cache miss와 `_obstructs_relative()`
6. retime request/model 조립과 지나치게 잦은 retime trigger
7. 반복적인 dataclass/tuple/dict 생성, snapshot 재구성, full objective 재계산

## 4. 실험 설계

### 4.1 두 종류의 실험을 분리한다

**고정 작업량 실험**은 semantic parity와 순수 처리량을 검증한다.

- 동일 instance/seed, 동일 constructor candidate quota, 동일 ALNS iteration quota
- baseline과 가속 variant가 같은 후보·수락 순서를 만드는지 확인
- objective parts, placement, serialized operation을 비교
- cold start, warm run, peak RSS, candidates/s, iterations/s를 기록

**고정 시간 실험**은 실제 경쟁 효과를 검증한다.

- 동일 instance/seed/timelimit에서 baseline과 variant를 paired comparison
- 빠른 코드가 추가로 수행한 iteration을 허용
- 최종 objective뿐 아니라 anytime trace 전체를 비교
- profiler가 켜진 결과는 objective 판단 표본에 포함하지 않음

속도 개선과 objective 개선을 한 숫자로 섞지 않는다. 먼저 동등 작업의 runtime이 줄었는지 확인하고, 그다음 같은 시간에서 objective가 낮아졌는지 확인한다.

### 4.2 실험 단계

| 단계 | 데이터 | budgets | seeds | 목적 |
|---|---|---|---|---|
| micro | 합성 입력 + 실제 hotspot fixture | quota 기반 | 고정 1개 | 커널 단위 정확성/처리량/JIT 비용 |
| dev | 크기·혼잡도별 대표 6개 | 10/60초 | 3개 | 빠른 탈락과 프로파일 |
| candidate | 40개 전체 | 60초 | 1개(`20260710`) | 전체 인스턴스 feasibility/회귀 screening |
| release | 계산비용 상위 10개 | 300초 | 1개(`20260710`) | 최종 paired objective/성능 판단 |
| exploitation | 가장 큰/느린 1~3개 | 1,800초 | 1~3개 | 장시간 탐색 활용성 |

대표 6개는 block 수, 공간 혼잡도, 기존 repair p90, retime 비중을 기준으로 baseline artifact에서 기계적으로 선택하고 manifest에 고정한다. 유리한 인스턴스를 수동 선별하지 않는다.

최종 release 10개는 fixed-budget elapsed로 고르지 않는다. 180초 run은 대부분 deadline까지 실행되어 elapsed가 약 171초로 수렴하므로 난이도 구분력이 없기 때문이다. 대신 기존 180초 × 3 seed raw artifact에서 다음 값을 계산해 내림차순으로 고정한다.

`hardness = median_seed((lns_repair_seconds + lns_retime_seconds) / lns_iterations)`

동률은 median repair time, instance 번호 순으로 결정한다. 이 기준으로 선정된 최종 검증 집합은 다음과 같다.

| 순위 | instance | blocks | bays | median sec/iteration | median iterations/180s |
|---:|---|---:|---:|---:|---:|
| 1 | `prob_38.json` | 250 | 3 | 11.3451 | 14 |
| 2 | `prob_23.json` | 100 | 2 | 8.1902 | 20 |
| 3 | `prob_25.json` | 100 | 2 | 6.8374 | 24 |
| 4 | `prob_40.json` | 250 | 4 | 6.7075 | 23 |
| 5 | `prob_27.json` | 150 | 2 | 5.5532 | 29 |
| 6 | `prob_21.json` | 100 | 3 | 5.1895 | 31 |
| 7 | `prob_39.json` | 250 | 3 | 4.8400 | 33 |
| 8 | `prob_33.json` | 200 | 3 | 4.7154 | 34 |
| 9 | `prob_20.json` | 300 | 5 | 4.0592 | 39 |
| 10 | `prob_35.json` | 200 | 3 | 4.0355 | 40 |

최종 검증은 위 10개에 대해 baseline과 승격 후보를 각각 300초, seed `20260710`으로 실행하는 paired comparison이다. 따라서 solver 실행은 variant당 10회, 총 20회이며 명목 timelimit 합계는 6,000초다. 한쪽 결과만 실행하거나 기존 180초 결과와 새 300초 결과를 직접 비교하지 않는다.

### 4.3 보고 지표

- 성능: elapsed, Python CPU time, native time, candidates/s, ALNS iterations/s, geometry cache hit/miss, Shapely call 수/시간, retime model-build/solve time, checker time, cold JIT compile time, peak RSS
- 정확성: Stage 5, violations, objective parity, placement/operation parity, exception, outer timeout
- 품질: win/tie/loss, per-instance relative gap, Borda/rank, `w1*DeltaZ1`, `w2*DeltaZ2`, `w3*DeltaZ3`, anytime primal integral, time-to-first-improvement, validated-best event 수
- 통계: instance/seed paired delta, median/p90/worst, bootstrap 95% CI. raw objective 합계만으로 결론 내리지 않음

## 5. Phase 0 — 기준선 동결

### 작업

1. 새 branch에서 시작 commit, dirty-state, Python/Numba/Shapely/Gurobi 버전, CPU/OS, dataset/config hash를 manifest에 기록한다.
2. 기존 240-run artifact의 checksum과 solver source 동일성을 검증해 병목 선정 기준선으로 재사용한다. 현재 HEAD에서 solver source가 달라졌다면 slow-10을 고르기 전에 전체 기준선을 다시 만든다.
3. 전체 40개 60초 screening과 계산비용 상위 10개 300초 paired release contract를 고정한다.
4. 반복 측정은 micro/dev fixture에서 수행해 결정성 및 측정 변동을 확인한다. 240-run matrix를 관성적으로 재실행하지 않는다.
5. 모든 결과는 gitignored raw artifact에 두고 tracked evidence에는 경로/hash/요약만 기록한다.

### 완료 조건

- 기존 240/240 Stage 5, parity `<=1e-6`, exception/timeout/regression 0 artifact의 checksum/source 계약 확인
- 두 반복의 phase median 변동이 권장 `<=5%`; 넘으면 CPU governor, 병렬 프로세스, background load부터 통제
- baseline commit/config/dataset/environment를 하나의 비교 ID로 고정

## 6. Phase 1 — 병목 확정

### 측정 방법

1. `cProfile`/`pstats`로 cumulative/self time 상위 함수를 수집한다.
2. Python sampling profiler로 GIL 안의 Python 시간과 Shapely/Gurobi native 시간을 분리한다.
3. line-level profiler는 상위 3개 Python 함수에만 적용한다.
4. 다음 계측을 phase trace에 추가한다.
   - repair별 후보 생성 수, 검사 수, 성공 수, 소요 시간
   - constructor/repair의 bay/orientation/time/position loop count
   - relation cache hit/miss/eviction, exact geometry 호출 수/시간
   - objective full recompute와 incremental delta 호출 수/시간
   - retime request build, model build, optimize, result validation 시간
   - ALNS iteration count, retime trigger count, accepted/new-best per second
5. 대표 6개를 cold process에서 profile하고 flame graph와 표를 함께 보존한다.

### 산출물

- 함수/라인별 self time과 호출 수
- Python-only, Shapely/native geometry, Gurobi solve, checker 비중
- 각 hotspot의 입력 크기 분포와 예상 Amdahl 상한
- `Numba`, `Python 구조 개선`, `pybind11`, `건드리지 않음` 분류표

### 완료 조건

- 전체 wall time의 최소 90%가 설명됨
- 상위 hotspot마다 재현 가능한 microbenchmark fixture가 있음
- “느릴 것 같다”가 아니라 호출 수, self time, cache miss 수로 우선순위가 정해짐

## 7. Phase 2 — Python 구조 개선 및 Numba 적용

### 7.1 먼저 수행할 Python 구조 개선

알고리즘 선택을 바꾸지 않는 범위에서 다음을 작은 patch로 각각 실험한다.

- block/bay/orientation의 불변 정보를 compact array로 한 번만 materialize
- 후보 루프 안의 dataclass, tuple, set, dict, Shapely translate 객체 생성을 감소
- retained placement의 bay/time index를 이용해 전수 scan 제거
- full objective 재계산을 검증용으로 남기고 hot path는 증분 delta 사용
- position/time 후보의 중복 제거와 정렬을 loop 바깥 또는 cache로 이동
- relation key/cache locality 개선; miss 원인과 eviction을 계측한 뒤 cache 크기 조정
- retime model-build와 solve를 분리하여, 동일 효과를 내는 trigger batching 가능성 평가

각 patch는 단독 ablation으로 측정한다. 여러 최적화를 한 번에 묶어 원인을 잃지 않는다.

### 7.2 Numba 적합성

Numba는 `@njit(nopython)`으로 컴파일 가능한, 배열 중심의 순수 수치 커널에만 적용한다.

| 후보 | Numba 적합도 | 조건 |
|---|---|---|
| batched AABB/시간 overlap filtering | 높음 | `int32/int64` contiguous array, Python 객체 없음 |
| objective/delta 계산 | 높음 | placement와 block 속성을 SoA array로 변환 |
| candidate score/rank | 높음 | 정렬 key를 수치 배열로 표현하고 tie-break 보존 |
| lattice/anchor 수치 생성·filter | 중간~높음 | Shapely 객체가 아닌 좌표/범위 단계만 분리 |
| retained candidate feasibility prefilter | 중간 | exact relation 전의 conservative reject만 담당 |
| Shapely intersection/translate | 낮음 | Numba가 Shapely 객체를 nopython으로 처리하지 못함 |
| Gurobi model/optimize | 낮음 | 이미 native solver; Python model-build만 별도 최적화 |
| checker, dict/dataclass orchestration | 낮음 | correctness authority로 그대로 유지 |

### 7.3 Numba 구현 규칙

- object mode fallback 금지; compile signature와 nopython 여부를 테스트
- Python reference kernel을 항상 유지하고 randomized/property parity를 수행
- tie-break, stable ordering, integer/float width, half-open interval 의미론을 명시
- `fastmath=True`는 objective/geometry 의미론에 사용하지 않음
- `parallel=True`는 Gurobi threads와 4-core oversubscription을 측정하기 전에는 사용하지 않음
- JIT compile 시간은 algorithm timelimit에 포함된 cold-process 기준으로 보고
- cache 사용 시 fresh sandbox에서 cache miss/hit 양쪽을 검증하고, cache file에 의존해 제출 성공을 가정하지 않음
- Python↔Numba 호출은 후보 하나마다 하지 않고 batch 단위 API로 설계

### patch 승격 조건

- reference 대비 random/property parity 100%, checker parity `<=1e-6`
- microbenchmark warm speedup 권장 `>=1.5x`
- cold JIT 비용을 포함해 target budget에서 end-to-end 손익이 양수
- dev-6에서 candidates/s 또는 iterations/s median 권장 `>=10%` 증가
- 10초 budget의 time-to-safe-incumbent와 objective가 악화되지 않음

기준을 못 넘긴 patch는 default off 또는 제거한다. “Numba를 사용했다” 자체는 완료 조건이 아니다.

## 8. Phase 3 — objective 감소 효과 확인

### 비교 variant

- `baseline`: Phase 0에서 고정한 현재 구현
- `python_structural`: 검증된 Python 구조 개선만 적용
- `numba_off`: 같은 코드 경로에서 reference kernel 사용
- `numba_on_cold`: 새 process/JIT cache 없는 실제 제출 유사 조건
- `numba_on_warm`: 커널 처리량의 상한 파악용; release 판단의 주 근거로 사용하지 않음

### 분석 순서

1. quota run에서 동일 후보/iteration에 대한 결과 parity와 runtime 감소를 확인한다.
2. dev/candidate gate를 통과한 뒤, 확정된 10개에 대한 300초 paired run에서 final objective와 anytime trace를 비교한다.
3. 속도 향상으로 실제 추가 iteration/candidate가 생겼는지 확인한다.
4. 추가 작업이 new-best event, time-to-first-improvement, primal integral 개선으로 이어졌는지 확인한다.
5. total뿐 아니라 `Z1/Z2/Z3` 가중 성분을 분리하여 특정 성분의 회귀를 숨기지 않는다.

### Numba/Python 결과 승격 게이트

- 모든 correctness 불변식 통과
- candidate-40에서 wall/throughput median 개선 권장 `>=10%`
- fixed-time paired W/L이 양수이고 bootstrap 95% CI가 중대한 악화를 배제
- 40개 60초 screening에서 systematic objective/feasibility regression 없음
- slow-10 300초 release matrix에서 Borda, median relative gap, anytime primal integral 중 최소 2개 개선
- peak RSS와 archive 크기가 제출 제약 내에 있음

objective가 줄지 않으면 다음 순서로 원인을 분류한다.

1. 가속된 단계가 더 일찍 종료되어 추가 탐색이 실제로 실행되지 않음
2. iteration은 늘었지만 동일/무효 후보를 반복함
3. repair는 빨라졌지만 retime/Gurobi가 새 병목이 됨
4. JIT cold-start가 short budget 이득을 상쇄함
5. deadline 기반 경로가 바뀌어 stochastic trajectory가 악화됨

원인에 따라 budget allocation 또는 탐색 정책 변경이 필요할 수 있으나, 이는 “동등 성능 최적화”와 분리된 알고리즘 실험으로 취급한다.

## 9. Phase 4 — pybind11/C++ 진입 결정

C++ 이관은 다음 조건을 모두 만족할 때만 시작한다.

1. Python 구조 개선과 Numba 후보를 모두 평가했다.
2. 잔여 Python-exclusive self time이 end-to-end wall의 `>=30%`다.
3. 하나의 좁은 커널이 wall의 `>=20%` 또는 profile p90의 지배 병목이다.
4. 경계 비용을 포함한 Amdahl 예상 end-to-end speedup이 권장 `>=15%`다.
5. 같은 커널의 Numba 구현이 불가능하거나 cold-start/처리량 기준을 못 넘긴 이유가 기록돼 있다.
6. 공식 환경에서 Linux CPython extension `.so` 제출이 허용되고 ABI/CPU/크기 조건이 확인됐다.

### 이관 우선순위

프로파일 결과에 따라 하나만 선택한다.

1. **batched repair candidate scan/evaluation**: 현재 가장 유력하다. Python 호출을 후보별로 왕복하지 않고 한 neighborhood를 한 번에 전달한다.
2. **geometry prefilter/relation kernel**: 정확한 parity 전략이 있을 때만 선택한다. 근사 결과는 reject-only prefilter로 제한하고 최종 권위는 기존 exact path/checker에 둔다.
3. **objective/delta/state index**: Python/Numba로 충분하지 않고 호출량이 큰 경우에만 선택한다.
4. Gurobi optimize와 full checker는 이관 대상이 아니다.

### native module 설계 원칙

- pybind11 API는 POD/contiguous array 또는 buffer protocol 기반의 coarse-grained batch API
- hot loop 동안 GIL release, Python callback 금지
- deterministic ordering/tie-break를 Python reference와 동일하게 명시
- integer scale, overflow 범위, float tolerance를 타입 계약으로 고정
- C++ 결과는 Python exact validation과 final checker를 통과한 뒤에만 incumbent 설치
- import/ABI/runtime 실패 시 자동으로 pure-Python reference path 사용
- native module이 없어도 40/40 feasible한 제출을 보장
- sanitizer/debug build와 optimized release build를 분리

### 검증 및 배포

- Python reference와 수천~수만 randomized differential tests
- boundary contact, negative anchor, simultaneous entry/exit, four-state relation 회귀
- ASan/UBSan 테스트, deterministic replay, repeated import/fallback 테스트
- target: Ubuntu 24.04 x86_64, CPython 3.12, 4 cores/16 GB 기준의 official-like container
- `-O3`를 기본으로 하되 CPU flag는 공식 CPU 하한을 확인한 뒤 결정
- `ldd`, GLIBC/GLIBCXX symbol, transitive shared library, file mode를 검사
- native binary를 포함하도록 archive allowlist를 의도적으로 확장하고 15 MB 제한 검증
- 깨끗한 extraction, network 없는 환경에서 import/smoke/full gate 수행

### C++ 승격 게이트

- Python/Numba 승격 게이트를 그대로 통과
- official-like cold process에서 end-to-end median speedup 권장 `>=15%`
- pybind 경계 시간이 가속 커널 시간의 10%를 넘으면 batch API 재설계
- native-on/off fixed-work 결과 동등
- native import 실패 matrix에서 safe fallback 100%
- archive/ABI/size/smoke gate 통과
- objective 지표가 Numba/Python best variant보다 실제로 개선되지 않으면 default off

## 10. 단계별 산출물과 중단 기준

| 단계 | 필수 산출물 | 다음 단계 진입 조건 | 중단/rollback |
|---|---|---|---|
| 0 기준선 | manifest, raw/summary/hash | 재현·정확성 gate 통과 | 환경 변동부터 해결 |
| 1 profile | flame graph, line table, call counters, Amdahl 표 | wall 90% 설명 | 계측 부족 시 구현 금지 |
| 2 Python/Numba | reference/JIT kernel, parity tests, ablation | cold end-to-end 이득 | default off/patch 제거 |
| 3 objective | paired matrix, W/T/L, gap, primal integral | 품질 gate 통과 | 속도만 있고 품질 없으면 탐색정책 별도 실험 |
| 4 pybind11 | narrow extension, fallback, fuzz/ABI/package tests | native 승격 gate 통과 | Python/Numba best 유지 |
| 5 release | slow-10×300초×1 seed paired evidence, clean archive smoke | 전 gate green | 마지막 green variant로 rollback |

모든 단계는 한 번에 하나의 가설만 변경한다. raw artifact, source/config hash, 명령, 환경을 남기지 않은 성능 수치는 의사결정에 사용하지 않는다.

## 11. 예상 실행 순서

1. 기준선 재생성 및 profile 전용 telemetry 설계
2. dev-6 함수/라인/native-boundary profile
3. 가장 큰 repair hotspot의 Python 구조 개선 1건
4. batched AABB/temporal/score kernel Numba prototype
5. fixed-work parity와 cold/warm microbenchmark
6. dev-6 fixed-time objective 비교
7. 통과한 patch만 40개×60초×1 seed candidate screening으로 승격
8. Python/Numba best variant의 residual profile
9. pybind11 진입 게이트 판정서 작성
10. 게이트 통과 시 narrow C++ spike 1건, 아니면 Python/Numba에서 종료
11. slow-10×300초×1 seed native-on/off paired release와 submission archive 검증

## 12. 확정된 실행 결정

2026-07-14 사용자 답변으로 다음을 확정한다.

1. **native 제출 허용**: Linux CPython 3.12용 `.so`를 submission ZIP에 포함할 수 있다. 현재 `.py` 전용 packager allowlist는 native 단계에서 명시적으로 확장하고 테스트한다.
2. **최종 기준 환경**: Ubuntu 24.04, x86_64, 4 cores/16 GB를 최종 판단 환경으로 사용한다. local macOS arm64 결과는 개발 신호로만 사용한다.
3. **최종 release matrix**: 위에서 고정한 계산비용 상위 10개를 baseline/후보 각각 300초, seed `20260710`으로 실행한다. 후보 승격 권장 기준인 Python/Numba 10%, native 15% end-to-end 개선과 correctness gate는 유지한다.

선정 기준이나 기존 artifact가 바뀌지 않는 한 slow-10 목록을 결과를 본 뒤 교체하지 않는다. 목록을 바꾸려면 새 baseline 전체 40개의 동일 hardness 지표로 먼저 재선정하고 manifest/hash를 갱신한다.
