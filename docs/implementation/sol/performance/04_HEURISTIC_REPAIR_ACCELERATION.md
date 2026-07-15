# 4단계 — heuristic repair 가속

## 목표

후보 의미론과 선택 순서를 보존하는 구조 개선을 우선 적용해, 같은 wall-clock 안에서 더 많은 유효 LNS iteration을 수행한다.

## 근거

- 180초 phase 시간의 약 74.1%가 heuristic repair에 사용된다.
- 유력한 비용 구조는 destroyed block × bay/orientation × time × position 열거, retained placement scan, exact geometry cache miss다.
- 이 단계의 목적은 단순 microbenchmark 속도가 아니라 60초 fixed-time objective 개선이다.

## 작업 범위

- `baseline/solver/neighborhoods.py`
- 필요한 최소 범위의 `baseline/solver/state.py`
- 필요한 최소 범위의 `baseline/solver/geometry.py`
- benchmark profiler/telemetry
- repair parity, fixed-work, integration tests

destroy operator 정책, constructor, MIP 활성화, retime trigger 정책은 변경하지 않는다.

## 단계 분리 원칙

다음 후보를 각각 독립 patch와 ablation으로 다룬다.

1. retained placement의 bay/time index로 전수 scan 제거
2. block/bay/orientation 불변 자료의 compact precomputation
3. position/time 후보 생성과 dedup/sort의 loop 밖 이동 또는 cache
4. candidate loop 내부 dataclass/tuple/set/dict 생성 감소
5. conservative batched AABB/time overlap prefilter
6. full objective recompute를 검증용으로 남기고 hot path delta 사용
7. relation cache key/locality 개선

Numba/C++은 프로파일이 좁은 수치 커널을 입증한 뒤에만 별도 variant로 사용한다. Shapely 객체를 억지로 Numba에 넣지 않는다.

## 진행 절차

1. hard-10에서 함수/라인/호출경계 프로파일을 수집한다.
2. repair wall time의 최소 90%를 설명하고 상위 hotspot을 순위화한다.
3. 상위 hotspot의 실제 입력으로 재현 가능한 micro fixture를 만든다.
4. 가장 작은 Python 구조 개선 하나를 구현한다.
5. fixed-work에서 동일 destroyed ids, candidate quota, seed로 candidate/placement/objective/serialization parity를 검증한다.
6. warm/cold 처리량, peak RSS, cache hit/miss를 비교한다.
7. 전용/통합/전체 unittest를 실행한다.
8. hard-10 baseline/candidate 60초 paired benchmark를 실행한다.

## 핵심 지표

- repair seconds/iteration
- candidates/second 및 exact checks/second
- relation cache hit/miss/eviction
- Shapely calls/time
- Python object allocation 또는 주요 호출 수
- LNS total iterations, accepted/new-best
- final objective W/T/L과 anytime integral

## 승격 조건

- fixed-work semantic parity
- hard-10 median repair throughput 권장 10% 이상 개선
- peak RSS와 cold-start가 제출 제약 내
- fixed-time 60초에서 추가 iteration이 실제 발생
- Win > Loss, median paired relative delta < 0

속도는 빨라졌지만 동일/무효 후보만 반복해 objective가 개선되지 않으면 성능 patch와 탐색정책 실험을 분리한다.

## 롤백 조건

- candidate 순서/tie-break가 의도 없이 변함
- exact geometry를 근사 결과로 승인함
- cache 메모리 폭증
- cold-start 또는 package 부담이 이득을 상쇄
- fixed-time objective 체계적 악화

## 결과 기록

프로파일, hotspot, 각 ablation의 fixed-work/fixed-time 결과, 승격 patch와 기각 patch를 추가한다.

### 2026-07-15 — immutable fit-bounds lookup 단일 ablation

#### 기준선, 범위, 환경

- 직전 3단계 `profile_priority` 후보는 constructor time gate 실패로 승격되지 않았다. 따라서 cumulative baseline은 1단계 승인 commit `cf74817d2d4d0c9ce90e4d655277097e1ce87737`과 2단계 계측 commit을 포함한 HEAD `8974e26c50385156387f0fc091131030713c6db2`, production constructor `eager_regret`, MIP `DEFAULT OFF` 상태다.
- baseline/candidate는 같은 source에서 `GeometryKernel.fits()`만 legacy recompute / parsed fit-bounds lookup으로 전환했다. config SHA-256은 baseline `962d4a18cbe4390567361215286bdab70eeb5dee38383973387150c908d7ceec`, candidate `0a79ff52eed2bdc7cbd15a9be29eb13987045473ff008d6104fd5976ac0e6163`다.
- hard-10 manifest / dataset SHA-256은 `5499cf993f5018dceca0464dfcaf0c328e129cb7ed4946c79cc4e1860127d3b7` / `c093fb98b17c714ea5b9c8c829d4bce24dd378f0b558196ce0961feb68b34e1f`다. frozen 순서 `prob_38, 23, 40, 25, 27, 39, 21, 33, 28, 24`를 변경하지 않았다.
- 환경은 macOS 26.5.2 arm64, Python 3.12.13, Shapely 2.1.2, Gurobi 13.0.2 restricted license다. `baseline/utils.py` / `baseline/baseline_greedy.py` SHA-256은 `d45aaeafdce8bf80d59d097f655c43313a4951bed43b6628e3b1cf62d4876a94` / `8ec2cc816b35b6507a9407bc9f893140a9d1b5e0892af92a2dbac2f91b32103b`로 유지했다.
- 수정 범위는 `baseline/solver/geometry.py`, geometry/benchmark contract tests, hard-10 profile/fixed-work/paired comparison harness뿐이다. destroy 정책, candidate 생성·정렬·tie-break, constructor 정책, MIP, interlock, retime trigger는 변경하지 않았다.

#### hard-10 사전 프로파일과 90% 설명

코드 변경 전에 hard-10 각 instance를 60초 `heuristic_lns`, seed `20260710`으로 cProfile하고, 반환된 실제 해에서 `tardy_chain`, destroy size 4의 repair-only fixture를 별도 측정했다.

- profile solve는 10/10 Stage 5, 합계 elapsed `571.061s`, LNS repair/retime `344.157/38.153s`, iterations `206`이었다. profiler 결과는 objective 판정 표본에 포함하지 않았다.
- repair-only 10회는 10/10 `FEASIBLE`/Stage 5, root cumulative wall `152.666s`였다. cProfile header는 `597,007,634` calls/self-time total `137.058s`를 기록했고, `generate_insertion_candidates → _candidate_options/search` call boundary의 cumulative time은 `149.598s`, 즉 repair root cumulative의 `98.0%`였다.
- 그 경계 안에서 `evaluate_insert` cumulative `95.772s`, `GeometryKernel.relation` `59.370s`, `GeometryKernel.fits` `36.326s`, `generate_position_candidates` `11.430s`, `IndexedSolutionState.co_present` `11.385s`, `Placement.__post_init__` `11.460s`였다. 이 값들은 중첩 call tree이므로 합산하지 않는다.
- 순수 Python self-time 1위는 `OrientationInfo.integer_range`의 `12.966s`, `11,184,311` calls였다. `fits`는 `11,166,094` calls였다. exact geometry 쪽은 relation hit/miss `17,672,181/1,183,417`, Shapely intersection `790,125` calls/self `12.581s`였다.
- 따라서 repair의 90% 이상은 후보 search call boundary로 설명되고, 그 안의 가장 큰 Python self hotspot은 이미 `BlockInfo.fitting_options`에 파싱되어 있는 integer fit range를 매 `fits()` 호출마다 다시 구성하는 동작으로 판정했다.

#### 선택한 단일 patch

- candidate는 block/orientation/bay별 `(x_lower, x_upper, y_lower, y_upper)`를 기존 immutable `fitting_options`에서 compact tuple로 한 번 materialize하고 `fits()` hot path에서 조회한다.
- candidate 생성, placement dataclass, time/position loop, relation cache, exact Shapely intersection, checker는 그대로다. 근사 geometry 승인이나 후보 생략은 없다.
- paired 평가 당시 candidate `baseline/solver/geometry.py` SHA-256은 `5439b9cba8830ef2f314815d34376937b3e474084889e7c4a808868f03d32a5d`였다.

#### fixed-work parity와 처리량

hard-10 safe snapshot, seed `20260710`, 동일 `tardy_chain` destroy size 4, 동일 repair budget 120초에서 cold 1회와 같은 kernel의 warm 1회를 비교했다.

| 지표 | Baseline | Candidate |
|---|---:|---:|
| cold/warm Stage 5 | 10/10, 10/10 | 10/10, 10/10 |
| cold/warm generated candidates | 300 / 300 | 300 / 300 |
| cold relation calls (hit/miss) | 191,844 (65,666/126,178) | 191,844 (65,666/126,178) |
| warm relation calls (hit/miss) | 191,844 (191,844/0) | 191,844 (191,844/0) |
| cold runtime median / total delta | — | `-1.7851% / -0.5505%` |
| warm runtime median / total delta | — | `-12.2041% / -9.9765%` |
| warm median throughput delta | — | `+13.9196%` |
| process peak RSS max | 114,802,688 B | 122,814,464 B |

- 20/20 fixture에서 destroyed ids, candidate count, changed ids, placement digest, objective/Z1/Z2/Z3, canonical serialization digest가 모두 일치했다. relation call/hit/miss도 정확히 같았다.
- cold start는 전체적으로 보합이고 warm 처리량은 권장 10%를 넘었다. RSS 증가는 약 8.0MB이며 제출 archive에 새 dependency나 native/JIT payload는 없다.

#### 테스트

- 최종 focused geometry/four-state/repair/ALNS/benchmark contract suite: `47/47 PASS`.
- 최종 전체 `baseline/tests`: `189/189 PASS` (`/opt/homebrew/Caskroom/miniforge/base/envs/ogc-2026/bin/python -m unittest discover -s tests -p 'test_*.py' -v`, exit 0).
- 기각 후 production rollback과 candidate-only 경로를 각각 2초 smoke하여 둘 다 Stage 5였다.

#### hard-10 60초 paired 결과

기본 seed `20260710`에서 각 instance의 baseline 직후 candidate 순으로 실행했다. W-L 차이는 4이고 median은 명확한 음수이므로 플레이북에 따라 seeds `20260711/20260712`는 실행하지 않았다.

| Instance | Baseline | Candidate | Relative delta | Iterations B/C | Accepted B/C | New-best B/C |
|---|---:|---:|---:|---:|---:|---:|
| `prob_38.json` | 2,040,406,878 | 2,040,406,878 | 0.0000% | 18 / 18 | 18 / 18 | 17 / 17 |
| `prob_23.json` | 60,492,321 | 56,377,652 | -6.8020% | 29 / 40 | 24 / 28 | 24 / 28 |
| `prob_40.json` | 75,514,855 | 75,514,855 | 0.0000% | 15 / 15 | 15 / 15 | 13 / 13 |
| `prob_25.json` | 7,131,216 | 6,701,417 | -6.0270% | 25 / 29 | 24 / 27 | 15 / 17 |
| `prob_27.json` | 878,441,661 | 864,745,398 | -1.5592% | 25 / 28 | 24 / 28 | 17 / 18 |
| `prob_39.json` | 807,188,801 | 769,135,783 | -4.7143% | 26 / 33 | 25 / 31 | 22 / 24 |
| `prob_21.json` | 77,686,285 | 69,400,639 | -10.6655% | 29 / 33 | 28 / 30 | 22 / 22 |
| `prob_33.json` | 386,707,235 | 369,285,690 | -4.5051% | 29 / 37 | 28 / 37 | 26 / 28 |
| `prob_28.json` | 150,695,859 | 207,224,475 | +37.5117% | 40 / 38 | 35 / 37 | 29 / 28 |
| `prob_24.json` | 36,595,070 | 46,162,832 | +26.1449% | 34 / 24 | 31 / 24 | 29 / 28 |

- final objective는 **6W/2T/2L**, median/mean/p90/worst relative delta `-3.0321% / +2.9384% / +26.1449% / +37.5117%`, Borda baseline/candidate `4/8`이다.
- candidate weighted component improvement 평균은 `w1*ΔZ1 +1,588,405.2`, `w2*ΔZ2 +411.0`, `w3*ΔZ3 +1,640.0`이다.
- elapsed median은 `57.133/57.138s`다. LNS repair 합계는 `412.373→420.414s`, retime `59.363→58.688s`, LNS checker `6.062→6.538s`다. candidate가 추가 iteration을 사용했기 때문에 repair 합계는 늘었지만 median repair seconds/iteration은 `1.6557→1.3669s`로 17.44% 감소했다.
- iterations `270→295`(+9.26%), attempts/feasible/accepted/new-best `271/252/252/214 → 295/275/275/223`이다. validated-best event는 `240→249`다.
- harness 계산 anytime primal integral mean은 `2.9800→2.9489`, time-to-first-improvement median은 `7.508→6.750s`다.
- 공통 hard blocker는 모두 통과했다. 20/20 Stage 5, exception/outer timeout/checker failure/trace regression/retime Z1 regression `0`, objective parity maximum `0.0`이다.

#### artifact

- profile: `artifacts/ogc_sage/step9/phase4-profile-baseline-20260715`; summary / aggregate repair profile / `SHA256SUMS` SHA-256은 `0d036b060154584d1a0983b6f566c0b672d68dc921062849d0f89fe64fc21a2b` / `5ed68258ae7be4eff8bf7b518bb772dac1d8fbb88f3566b7cdaccc472154603c` / `80854577f1dde0a3d732fe07256e7426fb37fad5b6daeeceb9a9570f4734a76f`다.
- fixed-work baseline / candidate summary SHA-256은 `063b1d924f4ecc1d199bb54152cb8eae85371313efbb4402a0dbaf6cb80247a9` / `a32620fb2aead7a301c16c0dd731780292bec6343004fcc46e0c1e4f9444aea0`이다.
- paired raw는 `artifacts/ogc_sage/step9/phase4-paired-prob_{instance}-{baseline|candidate}-20260715/raw.jsonl` 20개다. 경로순 `(SHA-256 + path)` 목록 digest는 `774ec6ed1edcacad6874411f3d37226b848df9f25a82ad28dd673cf402e0cbf9`다.
- paired comparison: `artifacts/ogc_sage/step9/phase4-evidence-20260715/paired-summary-seed20260710.json`, SHA-256 `7491afa28b7c19cacd09a2106276c1d9819612274942d853c2554e681659de66`이다.

#### 판정과 rollback

**승격 기각 / production `GeometryKernel.fits()` legacy recompute 유지**

fixed-work semantic parity, warm throughput, 추가 iteration, W>L, median relative delta, Borda, weighted Z1/Z2/Z3는 통과했다. 그러나 공통 품질 계약의 “단일 인스턴스 중대 회귀를 원인 없이 평균 개선으로 상쇄하지 않음”을 통과하지 못했다. `prob_28`과 `prob_24`는 각각 `+37.51%`, `+26.14%` 회귀했고, 특히 candidate iteration도 `40→38`, `34→24`로 줄어 warm micro speedup만으로 원인을 설명할 수 없다.

따라서 production 기본은 직전 cumulative baseline의 legacy `fits()`로 롤백했다. precomputed lookup은 `GeometryKernel.fits_precomputed()`와 benchmark-only `--geometry-fit-mode precomputed`에서만 재현 가능하게 남겼다. 재검토하려면 별도 단계에서 deadline-sensitive candidate truncation/trajectory 원인을 먼저 규명하고, 같은 frozen hard-10 전체에 추가 seeds까지 적용해 worst regression이 해소되는지 확인해야 한다. 이번 단계에서는 두 번째 성능 patch와 5단계를 시작하지 않았다.

## 실행 프롬프트

```text
OGC-SAGE 성능 개선 4단계 “heuristic repair 가속”만 수행한다.

먼저 다음 문서를 읽는다.
1. docs/implementation/sol/performance/README.md
2. docs/implementation/sol/performance/04_HEURISTIC_REPAIR_ACCELERATION.md
3. docs/implementation/sol/10_PERFORMANCE_ACCELERATION_PLAN.md의 병목/Phase 1~3
4. HARD10_MANIFEST.json과 직전 cumulative baseline 결과

- 코드 변경 전에 hard-10 프로파일로 repair wall time의 90% 이상을 설명한다.
- 가장 큰 Python hotspot 하나만 선택해 독립 patch로 개선한다.
- fixed-work parity와 fixed-time objective를 분리 검증한다.
- destroy 정책, constructor, MIP, retime trigger는 변경하지 않는다.
- 근사 geometry는 reject-only로 제한하고 exact checker 권위를 유지한다.
- 전용/통합/전체 unittest 후 hard-10 60초 paired benchmark를 실행한다.
- 처리량, 추가 iteration, accepted/new-best, W/T/L을 함께 보고한다.
- objective 개선이 없으면 속도 개선과 탐색정책을 섞지 말고 결과를 기록한다.
- 다음 단계는 시작하지 않으며 커밋/push는 별도 승인 시에만 수행한다.
```
