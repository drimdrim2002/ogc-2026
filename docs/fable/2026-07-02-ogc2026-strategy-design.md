# OGC 2026 알고리즘 전략 설계서

- 작성일: 2026-07-02
- 상태: 승인됨 (접근안 A, 코어 언어 C++, 로드맵 4주+)
- 문제: 조선소 블록 배치·스케줄링 (bay 할당 + 2D 비정형 패킹 + ENTRY/EXIT 스케줄링)
- 참조: `OGC2026_문제분석.md`, `baseline/utils.py`, `baseline/baseline_greedy.py`

---

## 0. 요약

**"스케줄링 주도(scheduling-led) ALNS + C++ 정수격자 기하 코어 + CP-SAT 재타이밍 매트휴리스틱"**을 5개 레이어(L1 기하 코어 ~ L5 견고성 셸, §4)로 구성.

- 목적함수는 실측 결과 tardiness가 ~95%를 차지하는 **tardiness 지배 문제** → 탐색은 스케줄링이 주도하고 패킹은 feasibility oracle 역할.
- 순수 Python+shapely는 greedy 1회 구성에 40~72초 소요(고밀도 인스턴스는 infeasible) → **C++ 기하 코어(.so)가 전제조건**. 목표: 배치 타당성 검사 µs급.
- Infeasible 제출 = 리더보드 -1점 → **항상-실행가능 incumbent + 3단 폴백 + 최종 utils 검증**의 견고성 셸 필수.
- 학계 대응 문제: **shipyard spatial scheduling** (He, Hong & Kim 2024 *J. of Scheduling*이 직계 조상) + **Berth Allocation Problem의 1차원 확장** (BAP ALNS 연산자 이식).

## 1. 문제 요약과 승리 조건

블록 i마다 (bay j, 위치 x,y ∈ 정수, 방향 o ∈ 이산 후보, ENTRY, EXIT ∈ 정수 일)을 결정.
제약: bay 경계, 동일 레이어 간 충돌 금지(정적), 크레인 수직 삽입/제거 경로(스윕, j≥k 규칙), R_i ≤ ENTRY, EXIT−ENTRY ≥ P_i, 같은 날은 EXIT 전량 → ENTRY 순서.
목적: min w1·ΣT_i + w2·(bay간 정규화 워크로드 최대편차) + w3·Σ(S_max−S_ij).

우선순위(실측 가중치 기준): **실행가능성 ≫ Z1(tardiness) ≫ Z3(preference) ≫ Z2(balance)**.
환산: 지각 1일 ≈ w1(13k~29k) ≈ preference 50~100점 ≈ balance 수천 단위.

평가 환경: AMD Threadripper PRO 9955WX, Ubuntu 24.04, **4코어/16GB/인터넷 없음**, 시간제한 수 분~30분(비공개), firejail+cpulimit. 로컬 컴파일한 .so 동봉 가능(서버 컴파일 없음).

## 2. 근거 데이터 (2026-07-02 측정)

유효 인스턴스 8개(prob_1~4, 21~23, 25) 분석:

| 항목 | 값 | 함의 |
|---|---|---|
| 규모 | n=100, m=2~3, 지평 55~86일 | 중규모 — 페어와이즈 사전계산 가능 |
| 블록 | 방향 6~8개, 레이어 1~2 (85%가 2), 정점 4~10 | 레이어 스윕 제약이 실제로 작동 |
| 크기 | bay ~51×20…150×25, 블록 bbox 평균 8.3×7.5 최대 ~20×20 | bay당 동시 체류 대략 8~15블록 |
| slack | 평균 1.2~5.4일, **zero-slack 26%** | 시간 고정 블록 = 패킹 앵커 |
| 밀도 | 공간-시간 밀도 0.24~**0.80** | 고밀도 인스턴스는 패킹 자체가 난관 |
| baseline | prob_1: 39.6s, obj=795,089 (tard 26일→756k=95%) / prob_23: 72s, **INFEASIBLE** | 기하 연산 속도가 병목이자 리스크 |
| NFP 메모리 | shape 796개, 쌍당 평균 341 오프셋(~43B), 전쌍 ~0.1GB | 정수 NFP 비트맵 전략 성립 |

참고: 최초 확보본은 40개 중 32개가 1MiB에서 절단된 손상 상태였으나 **2026-07-02 재다운로드로 복구 완료** (40/40 JSON 파싱 검증됨). 위 표의 통계는 유효했던 8개 기준이므로 전체 40개 기준 재집계가 1주차 과제에 포함됨.

## 3. 구조적 통찰 (설계에 직접 반영)

1. **EXIT = ENTRY + P 고정**: 블록을 오래 두는 것은 공간 낭비이고 tardiness는 EXIT에만 의존. 의사결정변수를 (bay, x, y, o, entry) 5개로 축소. EXIT 시점 크레인 경로가 막히는 예외만 수리 로직으로 처리(EXIT 지연 또는 방해 블록 재배치).
2. **페어와이즈 제약 대수**: utils.py의 entry-check(A|B)와 exit-check(A|B)는 동일 수식(A 레이어 k vs B 레이어 j≥k). 따라서 두 블록의 시간 구간이:
   - 겹침 → static(A,B) 필요 (레이어 k vs k, 대칭)
   - **포개짐(nested, LIFO)** → 안쪽 블록 방향의 sweep 제약 1개만 추가
   - **교차(crossing)** → 양방향 sweep 제약 모두 필요 (가장 엄격)
   → 같은 자리를 시간대별로 공유하는 블록은 LIFO 중첩이 유리. 디코더와 연산자가 temporal affinity(비슷한 EXIT끼리 인접 배치)를 선호하도록 설계.
3. **정수 오프셋 NFP는 근사가 아니라 정확**: 위치가 정수이므로 shape쌍별 "충돌 오프셋 집합" 비트맵이 완전한 표현. Minkowski 합의 robustness 지뢰를 피해 **오프셋별 정확 다각형 교차 테스트(쌍당 ~341회 × ~1µs)를 lazy 계산 + LRU 캐시**로 구축. 레이어 조합을 합집합해 쌍당 2장으로 압축:
   - `static(A,B)` = ∪_k (A층k × B층k 충돌 오프셋) — 대칭
   - `entry(A,B)` = ∪_{j≥k} (A층k × B층j) — 비대칭, static ⊆ entry
4. **zero-slack 앵커**: 시간이 완전히 고정된 26%의 블록을 먼저 배치해 골격을 만들고, slack 있는 블록을 사이에 끼워 넣는 구성 순서.

## 4. 아키텍처 (5 레이어)

```
┌─ L5 견고성 셸 (Python): 시간예산 관리, 항상-실행가능 incumbent,
│    최종 utils.check_feasibility 검증, 3단 폴백
├─ L4 매트휴리스틱 마무리 (예산 후반 ~20%): CP-SAT 재타이밍 / 배치풀 MIP / Z3·Z2 폴리시
├─ L3 ALNS 코어 (예산 ~80%): 파괴·복구 연산자, RRT/재가열 SA 수용, 경량 적응 가중치
├─ L2 구성 디코더: ATC+면적 우선순위 biased randomization → time-first BLF
└─ L1 C++ 기하 코어 (.so): 정수 NFP 비트맵 + (bay,layer,일) 점유 비트보드 + 후보위치 생성
```

### L1. C++ 기하 코어

**자료구조**
- Shape 테이블: 인스턴스 로드 시 (블록×방향)별 레이어 다각형을 정규화(기준점 이동)해 보관.
- **NFP 비트맵 캐시**: key=(shape_a, shape_b) → `static`/`entry` 오프셋 비트맵 (윈도 = 두 bbox 합, 평균 ~341셀). lazy 계산: 오프셋별 정확 polygon-polygon 교차(area>0) 판정. 자체 기하 루틴(클리핑 or 분리축+교차면적)으로 구현하고 shapely 결과와 대조 테스트.
- **점유 인덱스**: bay별 시간 정렬 이벤트 리스트 + (bay, layer, day) 점유 비트보드(bay ≤ 150×25 → 행당 uint64 3개). 후보 위치 스캔용.
- 배치 상태: 블록별 (bay, x, y, o, entry) + bay별 체류 인터벌 트리.

**핵심 API (pybind11 바인딩, 중간 입도)**

바인딩 오버헤드가 무의미해지도록 API는 **파괴/복구 연산 단위의 배치 호출**로 설계한다 (충돌 검사 1회 단위의 잦은 왕복 금지). 프로파일링 결과 경계 비용이 유의미하면 ALNS 내부 루프를 통째로 C++로 이관한다.
- `load_instance(json_bytes)` → 핸들
- `check_place(block, bay, x, y, o, entry)` → feasible? (정적+스윕+경계, 증분)
- `find_positions(block, bay, o, entry, mode)` → corner-point 후보와 점수(스캔라인+비트연산)
- `earliest_entry(block, bay, x, y, o, t_min)` / `best_insertion(block, ...)` → 디코더 내장 루프
- `apply/undo(move)`, `objective_delta(move)` — LNS 증분 평가
- `verify_full()` → utils 5단계와 동일 의미론의 자체 전체 검증

**빌드·배포**
- CMake, `-O3 -march=x86-64-v3`(AVX2, 서버 Zen5 호환), `-static-libstdc++ -static-libgcc`로 심볼 의존 최소화.
- WSL2 Ubuntu 24.04에서 빌드 = 서버 배포판 일치. `firejail --net=none --rlimit-as=16g` + `cpulimit -l 400` 재현 테스트.
- **pybind11 채택**: 대회 공식 env가 python=3.12로 고정이라 CPython ABI 리스크가 낮고, 호출 오버헤드(~0.1-0.3µs)가 ctypes(~1-2µs)보다 훨씬 작다. 로컬 빌드는 서버와 동일한 CPython 3.12 대상. (대안 검토: nanobind는 stable ABI 지원+더 낮은 오버헤드로 기술적 우위이나 성숙도·익숙함에서 pybind11 우선. subprocess+JSON은 ABI 완전 면역이지만 증분 통합 불리 — 비상 대안으로만 유지.)
- **폴백**: .so 로드 실패 시 numpy/shapely 순수 Python 경로로 자동 전환(성능만 하락, 기능 동일).

**정확성 원칙**: utils.py(shapely, `area > 0`)와 판정이 갈릴 수 있는 경계 사례는 **코어가 미세하게 보수적**(충돌로 간주)이도록 epsilon 설정. 최종 해는 반환 직전 반드시 utils로 검증.

### L2. 구성 디코더 (multi-start)

1. 우선순위: **ATC(Apparent Tardiness Cost)** `(1/p_i)·exp(−max(d_i−p_i−t,0)/(k·p̄))` × 면적 가중 혼합. zero-slack 블록 최우선 앵커.
2. **biased randomization**(기하분포 편향 샘플링)으로 순서 다양화 — multi-start·ALNS 복구에 공용.
3. **time-first BLF**: 후보 entry를 R_i부터 오름차순으로, 각 시각에서 (bay × corner-point 후보 × 방향) 중 최선을 선택. 점수 = lexicographic (Δtardiness, 공간 단편화/temporal affinity 타이브레이크).
4. 실패 시 entry 지연(= tardiness 비용 명시적 수용) — baseline의 "밀어내기 복구"를 디코더 안에 내장.

### L3. ALNS 코어

**파괴 연산자** (제거율 20~35%, BAP 문헌 튜닝값 ~0.33):
| 연산자 | 내용 |
|---|---|
| random | 순수 다양화 |
| worst-tardiness | 지각 기여 상위 블록 제거 |
| Shaw(관련도) | 공간·시간 근접도 `A·dist + B·|Δentry| + C·|Δexit|` 기반 군집 제거 |
| time-slice | 특정 bay의 시간창 내 전 블록 제거 (시간 슬라이스 해방) |
| spatial-column | 특정 공간 영역을 시간축 전체로 제거 (공간 기둥 해방) |
| **blocking-set** (문제 특화) | 지각 블록의 조기 진입을 막는 방해 집합(공간 선점자 + 스윕 차단자) 역추적 제거 |

**복구 연산자**: greedy+noise / regret-2,3 / most-constrained-first — 모두 L2 디코더 재사용, 후보는 corner-point로 한정.

**수용·적응**: record-to-record travel(초기 임계 2~5% 선형 감소) 기본, 재가열 SA 대안. 적응 가중치는 경량(세그먼트 100회, σ=33/13/9) — 문헌상 기여 ~0.14%이므로 연산자 품질에 공수 집중.

**병렬화**: 4코어 → 독립 ALNS 체인 2~3개(시드 상이) + 공유 배치풀·베스트 교환, 잔여 코어는 복구 후보 평가 병렬화.

### L4. 매트휴리스틱 마무리 (예산 후반 ~20%)

1. **CP-SAT 재타이밍**: incumbent의 기하(bay, x, y, o) 고정 → 블록 = interval 변수(release/duration), 공간 충돌쌍 = pairwise disjunction(+ 스윕 방향 조건은 순서 제약으로 인코딩) → 전체 좌측이동을 전역 최적화. 100 인터벌 + 희소 충돌 그래프는 수 초면 풀림. 정체 시마다 호출 가능.
2. **배치풀 호환성 선택 모델** (CP-SAT): 탐색 중 발견한 블록별 상위 K개 (bay,x,y,o,entry) 튜플 풀 → 페어와이즈 충돌 사전계산 → "블록당 정확히 1개 선택 + 충돌쌍 배제" 모델을 CP-SAT로 풀이. 풀이 다양할 때만 유효, 종료 직전 1회. (Gurobi 사용은 라이선스 서버 확인 후 결정 — 확보 시 동일 모델을 MIP로 이관해 성능 비교.)
3. **Z3/Z2 폴리시**: Z1 무손실 조건에서 bay 재할당·스왑으로 preference/balance 개선.

### L5. 견고성 셸

- **시간예산 관리자**: timelimit을 [로드·전처리 | 구성 | ALNS | 매트휴리스틱 | 검증·직렬화] 단계로 배분, 인스턴스 규모에 따라 단계 스킵. 안전 마진(예: 5~10%)을 남기고 조기 반환.
- **3단 폴백**: ① ALNS 최선해 → ② multi-start greedy 최선해 → ③ 순차 단독 배치해(bay당 한 블록씩 시간 직렬화 — 항상 feasible, 최후 보루).
- 반환 직전 **utils.check_feasibility 필수 통과** 확인. 실패 시 위반 블록 국소 수리 후 재검증, 그래도 실패면 하위 폴백.
- 모든 난수 시드 고정 옵션(재현성), 예외는 최상위에서 포획해 폴백 반환(서버의 "exception" 판정 방지).

## 5. 저장소 구조와 제출 패키징

```
ogc2026/
  core/            # C++ 소스, CMake, 단위테스트(vs shapely 대조)
  solver/          # Python: 디코더, ALNS, 매트휴리스틱, 견고성 셸
  submission/      # 제출 zip 스테이징: myalgorithm.py(루트), utils.py, *.so, solver/*
  experiments/     # 벤치 하네스, 결과 JSON, ablation 로그
  data/            # 인스턴스 (손상분 재다운로드 필요)
  baseline/        # 제공 원본 (수정 금지)
```

제출 체크: zip 루트에 myalgorithm.py / ≤15MB / 상대경로만 / utils.py 무수정 / .exe·.dll류 확장자 금지 / 12시간 쿨다운은 "승인" 기준이므로 제출 전 로컬 full-simulation 통과를 게이트로.

## 6. 실험·검증 전략

- **정확성**: C++ 코어 vs shapely 무작위 대조 테스트(다각형 쌍 10^5 스케일 fuzz), utils.check_feasibility를 최종 oracle로.
- **성능 벤치**: 인스턴스 8개(복구 후 40개) × 시간제한 {60s, 300s, 1800s} 매트릭스, obj 분해(Z1/Z2/Z3) 추적, 결과 JSON 축적.
- **ablation**: 연산자별 on/off, 매트휴리스틱 기여도, 코어 유무 처리량 — 기술보고서 재료로 처음부터 축적.
- **서버 시뮬레이션**: firejail(네트워크 차단, 16GB) + cpulimit 400% 환경에서 전 인스턴스 무사고 통과를 제출 게이트로.

## 7. 로드맵 (4주+)

| 주차 | 목표 | 완료 기준 (측정 가능) |
|---|---|---|
| 1주 | C++ 코어 v0(NFP 비트맵, 점유 인덱스, check/find API) + L2 디코더 + L5 뼈대 | 전 유효 인스턴스 feasible, baseline obj 개선, check_place ~µs 실측, shapely 대조 통과 |
| 2주 | ALNS 코어(연산자 6+3, RRT, 증분 평가) + 실험 하네스 | 5분 예산에서 1주차 구성해 대비 obj 개선폭 정량화(목표: Z1 20%+ 감소), prob_23급 고밀도 포함 전 인스턴스 feasible 유지, 재현 가능 벤치 리포트 |
| 3주 | CP-SAT 재타이밍 + 배치풀 MIP + Z3/Z2 폴리시 + (여유 시) beam 구성기·병렬 체인 | 레이어별 기여도 ablation, 30분 예산 활용률 점검 |
| 4주 | 견고성 하드닝(폴백·예외·시간예산) + 튜닝 + 제출 패키징·서버 시뮬 | firejail 통과, 제출 리허설(zip 구조 검증), 파라미터 확정 |
| 이후 | 리더보드 피드백 반영, 숨은 인스턴스 분포 대응, 기술보고서 초안 | — |

## 8. 리스크와 대응

| 리스크 | 대응 |
|---|---|
| utils.py와 충돌 판정 불일치 (float, area>0) | 코어를 미세 보수적으로, 최종 utils 검증 + 국소 수리 |
| .so 서버 로드 실패 | static-libstdc++, 서버 동일 CPython 3.12 대상 빌드, x86-64-v3, 순수 Python 폴백 동봉 (비상 시 subprocess+JSON 방식 전환) |
| 숨은 인스턴스 분포 변화 (n·m·레이어·지평 확대) | 코어 파라미터 독립 설계, 예산 관리자 단계 스킵, 폴백 체인 |
| 고밀도 인스턴스 feasible 실패 | 순차 단독 배치 최후 폴백(항상 feasible), blocking-set 수리 |
| Time limit exceeded | 안전 마진 조기 반환, 단계별 하드 데드라인 |
| 12시간 쿨다운 낭비 | 제출 전 로컬 full-simulation 게이트 (§6) |

## 9. 핵심 참고문헌

- He, Hong & Kim (2024) 조선 spatiotemporal scheduling, *J. of Scheduling* — 본 대회의 직계 조상: https://link.springer.com/article/10.1007/s10951-024-00804-1
- Martín-Iradi et al. (2024) 연속 BAP ALNS(연산자·튜닝값 원천), *EJOR*: https://arxiv.org/abs/2302.02356
- Hansen et al. (2020) RoRo 적재 ALNS — blocking 제약의 최근접 유사 사례: https://link.springer.com/article/10.1007/s10732-020-09451-z
- Libralesso & Fontan — ROADEF 2018/2022 우승(anytime tree search, "디코더 속도가 승부처"): https://arxiv.org/pdf/2004.00963
- Santini, Ropke & Hvattum (2018) 수용 기준 비교(RRT 권장 근거): https://santini.in/files/papers/santini-ropke-hvattum-2018.pdf
- Turkeš, Sörensen & Hvattum (2021) ALNS 적응층 메타분석(기여 ~0.14%): https://pmc.ncbi.nlm.nih.gov/articles/PMC7711214/
- Kwon & Lee (2015) 대형 블록 spatial scheduling(diagonal-fill): https://www.sciencedirect.com/science/article/abs/pii/S0360835215002296
- Ge & Wang (2021) 비정형 블록 spatial scheduling(직사각형화 손실 정량화): https://www.sciencedirect.com/science/article/abs/pii/S0360835220306550
- jagua-rs / sparrow — Rust 2D irregular 충돌엔진·네스팅 SOTA(설계 참조): https://github.com/JeroenGar/sparrow
- 반이산(semi-discrete) BLF (EJOR 2022): https://arxiv.org/abs/2103.08739
- CP-SAT 스케줄링: https://github.com/google/or-tools/blob/stable/ortools/sat/docs/scheduling.md

## 10. 선행 과제 (구현 시작 전)

1. ~~손상 인스턴스 재확보~~ — **완료 (2026-07-02)**: 재다운로드 후 40/40 JSON 파싱 검증됨.
2. **로컬 env 복구**: `conda run -n ogc2026 pip install ortools==9.15.6755 pybind11` (shapely 2.1.2는 설치 완료). **gurobipy는 보류** — 라이선스 서버 확인 후 진행 예정. torch/tensorflow는 본 설계에서 불필요 — 실패해도 무방.
3. C++ 툴체인 확인: gcc-13+/CMake on WSL2 Ubuntu 24.04.
