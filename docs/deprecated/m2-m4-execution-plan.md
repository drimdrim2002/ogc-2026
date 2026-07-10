# OGC 2026 M2–M4 실행 계획: Fast Constructor → Budgeted LNS → Profiled Performance

Last updated: 2026-07-09
Status: superseded 2026-07-09 — 알고리즘 설계 기준은 `docs/strategy/solver-design.md`(clean-slate 설계)로 이관되었다. 본 문서의 §1 진단(측정 근거)과 §2 평가기 의미론 계약은 유효한 참고 자료로 남는다.

## 0. 문서의 위상

- 이 문서는 `docs/strategy/operating-plan.md`의 M2/M3/M4를 **실행 수준으로 재정의**한다. 마일스톤 상태·벤치마크 정책의 소유권은 operating-plan에 있고, "무엇을 어떤 순서로 어떻게 구현·측정·수용하는가"는 이 문서가 소유한다.
- `docs/fable/` 문서들은 아이디어 출처다. 이 문서에 명시적으로 채택된 항목만 실행 대상이며, 채택 근거와 수정 사항을 §2, §7에 기록한다.
- 이 문서의 모든 의미론적 주장은 `baseline/utils.py`(공식 체커)와 2026-07-09 진단 아티팩트에서 직접 검증되었다. 주장마다 코드 앵커(`file:line`)를 붙인다. **앵커 없는 의미론 주장은 이 문서에 추가할 수 없다** (§7 재발 방지 프로토콜).

## 1. 확정 진단: 왜 지금까지의 M2/M3가 의미 있는 개선을 내지 못했나

근거 아티팩트: `experiments/results/m3/lns_diagnostic/2026-07-09-m3-lns-diagnostic-dev-10-myalgorithm-300s-small.json` (300s 진단), `experiments/results/m3/small_lns/…` (60s 쌍), 솔버 로그의 `lns_stats` 필드.

### 1.1 구성(Phase 1)이 어떤 예산을 줘도 전부 소모한다

- 60s 실행: **100개 인스턴스-행 전부 elapsed ≥ 56.4s**. daily-40에서 39/40이 "Phase 1 deadline reached"로 종료.
- 300s 진단: 8/10 인스턴스에서 `phase1_elapsed` = 285.0s(= 0.95 × timelimit 데드라인, `baseline_greedy.py:1089`)를 그대로 소진하고도 200~300블록 중 **136~196개만 배치**.
- 블록당 배치 비용이 초선형으로 증가: prob_13 로그 기준 100번째 블록 34s, 175번째 블록 187s.
- 데드라인 이후 `_complete_with_serial_fallback`의 prefix-trim(`baseline_greedy.py:1002-1037`)이 greedy 작업 대부분을 폐기: prob_13은 60s에서 54/119, 300s에서 54/195만 유지 → **두 예산에서 목적함수가 비트 단위로 동일**(618,931,163.99). 대형 인스턴스의 현재 결과는 사실상 "greedy 54개 + serial 나머지"다.

### 1.2 원인은 휴리스틱 품질이 아니라 탐색 구조다

Phase 1이 완주하면 해 품질은 급격히 좋아진다: prob_4는 60s(미완주) obj 172,225,679 → 300s(완주, `phase1_elapsed`=98.6s) obj **248,906 (−99.86%)**. 문제는 오직 속도다.

현재 탐색 구조(코드 앵커):

| 낭비 요인 | 위치 | 내용 |
|---|---|---|
| 후보 위치 데카르트 곱 | `baseline_greedy.py:166-197` | `_candidate_positions`가 x후보 × y후보 전체 곱을 생성. 배치된 블록 m개 기준 O(m²)개 |
| 전-기간 블록 기준 후보 | `baseline_greedy.py:1448-1450` | 후보 생성이 시간 겹침과 무관하게 "그 bay에 배치된 모든 블록"을 사용 — 이미 나간 블록도 후보 격자를 키움 |
| 전수 시간 탐색 | `baseline_greedy.py:311-392` | 후보 위치마다 `_find_earliest_slot`이 {release} ∪ {모든 exit 이벤트}를 순회, 각 시각마다 shapely 기반 entry/exit/충돌 검사 |
| 조기 종료 없음 | `baseline_greedy.py:1432-1476` | 좋은 후보를 찾아도 모든 (bay × orient × 위치) 조합을 끝까지 평가 |

블록당 최악 O(bays · orients · m³) 회의 shapely-경유 검사 → 전체 ~O(n⁴). 관측된 초선형 증가와 일치한다.

### 1.3 M3 LNS는 사실상 사문(死文)이다

- 60s: LNS 진입 **1/40** (prob_21, 잔여 <1s), 수용 0건. daily-40 small-LNS와 adaptive preflight가 obj/obj1/obj2/obj3 **40/40 완전 동일**.
- 300s: 진입 2/10. prob_4 12회 반복·수용 0(timeout), prob_21 25회 반복·**repair 후보 25/25 infeasible**·수용 0.
- 실험 로그의 "dev-10 −7.5% 개선"은 LNS 기여가 아니다(수용 0건). dev-10 small-LNS 행들은 daily-40 preflight adaptive 행들과 동일하고, 비교 대상이었던 adaptive 아티팩트와의 차이는 **baseline 실행 간 편차**다. 데드라인-트림 경로가 벽시계에 민감해서 생기는 잡음이며, §6.3의 측정 무결성 규칙으로 재발을 막는다.

### 1.4 숨은 결함 3종 (이번 조사에서 코드로 확인)

1. **역방향 스윕 미검사**: `_find_earliest_slot`은 새 블록 자신의 entry/exit 경로만 검사한다. 새 블록 N을 놓으면 **N의 체류 구간 안에서 entry/exit하는 기존 블록 C의 크레인 경로**(C의 레이어 k vs N의 레이어 j≥k)가 막힐 수 있는데 이를 검사하지 않는다 (`baseline_greedy.py:355-387`에 해당 검사 부재). Phase-2 repair가 사후에 이를 주워 담느라 시간을 쓰고, LNS repair 후보가 full check에서 무더기로 infeasible 판정(위 prob_21 25/25)되는 직접 원인이다.
2. **직렬화기 동시각 EXIT 순서**: `_build_operations`(`baseline_greedy.py:1746-1783`)는 같은 시각의 EXIT들을 block_id 순으로 나열한다(1773행). 모듈 독스트링(63-65행)은 "각 op가 선행 op 이후 bay 상태에서 feasible하도록 정렬"이라 주장하지만 **구현은 그렇지 않다**. overhang 쌍이 같은 날 exit하면 Stage 5 위반을 만들 수 있는 잠재 결함(§2.4 진리표 참조). 독립-배치 모드(§3)에서는 무해함이 증명되므로 v1에서는 그 모드로 회피하고, stagger 허용 시 §3.4의 canonical serializer로 해결한다.
3. **전-기간 후보 생성**: 1.2 표 참조. 시간을 무시한 후보 격자는 낭비이자 품질 저하 요인.

### 1.5 결론

M2의 목표를 "저비용 목적함수 개선"에서 **"빠르고 정교한 constructive greedy"로 재정의**한다. 구성이 수 초 안에 끝나야 M3(LNS)가 살아나고, M4(성능)는 그 위에서 프로파일링으로 정당화된 것만 한다. 이는 원래 의도("빠른 시간 내 완료되는 정교한 greedy heuristics")로의 복귀다.

## 2. 평가기 의미론 계약 (모든 후속 구현의 단일 진실)

`baseline/utils.py`에서 검증한 사실만 나열한다. 이 절과 모순되는 코드/문서/계획은 그 자체로 결함이다.

### 2.1 시간 모델

- 시각은 정수(operations 키는 `int()` 변환 가능해야 함, `utils.py:1043-1047`). 점유는 반개구간 **[entry, exit)**.
- Stage 2 (entry 시 존재 집합): `entry_k <= t < exit_k` — **하한 포함** (`utils.py:1177`).
- Stage 3 (exit 시 존재 집합): `entry_k < t < exit_k` — **양끝 배제** (`utils.py:1217`).
- Stage 4: 시간 겹침 쌍(`a1 < e2 and a2 < e1`, `utils.py:1156-1158`)에만 같은-레이어 충돌 검사.
- Stage 5: 시각 오름차순으로 재생. 같은 시각 안에서 **EXIT이 ENTRY보다 먼저** 나열되어야 하고(`utils.py:1293-1302`), 같은 시각의 op들은 **나열된 리스트 순서대로** 라이브 present-set에 대해 순차 검사된다(`utils.py:1304-1382`). 즉 같은 타입 간 순서는 "자유"가 아니라 결정 변수다.
- 좌표는 `int(round(x))`로 반올림되어 검사된다(`utils.py:1151-1153`). 솔버는 처음부터 정수 좌표만 출력한다.

### 2.2 타이밍 자유도

- `entry >= release − 1e-6`, `exit − entry >= processing_time − 1e-6` (`utils.py:1123-1131`).
- **exit은 자유 결정 변수다.** `exit = entry + p`는 제약이 아니라 선택이다. 정확한 지배성 범위:
  - 독립-배치 모드(§2.5) **안에서는** exit 최소화가 약지배적이다(체류 연장은 목적함수를 개선하지 못하고 타 블록의 여유만 줄인다).
  - **전역적으로는 지배적이지 않다.** 인터락(§2.4)에서 host는 guest의 exit을 품기 위해 `exit_host ≥ exit_guest`가 필요하므로 exit 연장이 feasibility 레버가 된다. 또한 exit이 막힌 블록은 방해 블록이 떠난 뒤로 exit을 미루는 것이 공간 이동보다 싼 수리가 될 수 있다.
  - 따라서: 구성 v1(Mode A)은 `exit = entry + p`를 쓰되 그 정당성은 "모드-국소 지배성"으로 문서화하고, M3 오퍼레이터와 Mode B는 exit 연장을 1급 레버로 사용한다(§4.3).

### 2.3 목적함수

- `objective = w1·obj1 + w2·obj2 + w3·obj3` (`utils.py:1390-1421`), 전부 **float 그대로, floor/반올림 없음**.
- `obj1 = Σ max(0, exit − due)`: **exit 시각만의 함수**.
- `obj2 = max_{j1≠j2} |u_j1·load_j1 − u_j2·load_j2|`, `u_j = avg_area/(W_j·H_j)` (`utils.py:1409-1419`): **bay 할당만의 함수** (빈 bay도 load 0으로 참여).
- `obj3 = Σ (max_pref − pref_bay)` (`utils.py:1404-1405`): **bay 할당만의 함수**.
- 구조적 귀결: 기하(위치·방향)는 목적함수에 **직접 기여하지 않는다**. 기하는 순수하게 feasibility(→ 더 이른 entry → 더 이른 exit → obj1)를 통해서만 목적에 기여한다. 탐색 설계는 이 분해를 활용한다: bay 이동은 obj2/obj3를 정확히 예측 가능, 시간 이동은 obj1을 정확히 예측 가능.
- 내부 증분 objective는 **탐색 가이드 전용**이다. 수용 판정의 권위는 항상 `check_feasibility` 결과값이다(패리티 테스트 의무, §7).

### 2.4 크레인 스윕과 인터락 진리표

- 스윕 규칙: entry/exit 모두 이동 블록의 레이어 k는 기존 블록의 레이어 j ≥ k와 겹치면 안 된다 (`utils.py:703`, `utils.py:799`). 같은-레이어(k=k) 겹침은 공존 자체가 불가(Stage 4).
- 정의: Stage-4를 통과한 공존 쌍 (A, B)에 대해 **OBS(A←B) := ∃ k < j : area(A_k ∩ B_j) > 0** ("B가 A 위에 걸쳐 있음", B가 A의 아래 레이어 위 상공에 질량을 가짐). A의 entry와 exit은 동일 술어로 막힌다(check_entry/check_exit이 같은 (k, j≥k) 집합을 검사).
- **진리표** (B가 A 위에 걸침, 역방향은 아님; 시간이 겹치는 쌍):

| 사건 | 판정 | 근거 |
|---|---|---|
| A가 B 존재 중 ENTRY | 불가 | A_k vs B_j(j≥k) 겹침 → Stage 2 |
| B가 A 존재 중 ENTRY | 가능 | OBS(B←A)=false |
| ⇒ 진입 순서 | **entry_A < entry_B (strict)** | 같은 날도 불가: Stage 2는 동시 진입자를 존재로 간주(`entry_k <= t`, `utils.py:1177`) — 나열 순서로 구제 불능 |
| A가 B 존재 중 EXIT | 불가 | 동일 술어 → Stage 3 |
| B가 A 존재 중 EXIT | 가능 | OBS(B←A)=false |
| ⇒ 반출 순서 | **exit_B ≤ exit_A** (guest 먼저) | Stage 3는 양끝 배제이므로 동시각 허용 |
| exit_B = exit_A인 경우 | 같은 시각 EXIT 리스트에서 **B(위에 걸친 쪽)를 먼저** 나열 | Stage 5가 리스트 순서로 재생(`utils.py:1364-1382`) — A를 먼저 쓰면 B가 아직 present라 위반 |

- 요약: **guest(위에 걸친 블록)의 체류 구간은 host의 체류 구간에 중첩(nested)되어야 한다**: `entry_host < entry_guest`, `exit_guest ≤ exit_host`. host의 최소 exit(`entry_host + p_host`)이 guest의 exit보다 이르면 host의 exit 연장이 필요하다 — §2.2의 exit 자유도가 필수인 이유.
- 이 표의 각 행은 §3.1의 단위 테스트로 고정한다. 상호 overhang(OBS 양방향)은 공존 불가(양쪽 exit 모두 막힘)이므로 시간 분리만 가능하다.

### 2.5 독립-배치 안전 정리 (Mode A의 기초)

**정리**: 같은 bay에서 시간이 겹치는 모든 블록 쌍이 **footprint 합집합(전 레이어 union) 기준 서로소**(교차 면적 0; 경계 접촉 허용)이고, 각 블록이 bay 경계 안에 있으며 Stage-1 타이밍이 유효하면, 그 해는 feasible하다. 또한 같은 시각 내 op 나열 순서는 (EXIT-before-ENTRY만 지키면) **임의로 해도 안전**하다.

근거: 모든 레이어 쌍 (k, j)에 대해 A_k ∩ B_j ⊆ U_A ∩ U_B = ∅ 이므로 Stage 4(k=k), Stage 2/3(j≥k), Stage 5 재생의 모든 장애물 술어가 공집합. 동시 진입/동시 반출도 상호 장애가 없어 순서 무관. union 교차 면적>0 ⟺ 어떤 레이어 쌍 교차 면적>0 이므로 이 검사는 보수적(모든 staggering 금지)이지만 안전하다.

귀결: Mode A에서는 배치 후보 검사가 **후보 vs 시간-겹침 블록당 union 다각형 교차 1회**(+AABB 프리필터)로 줄고, `check_entry`/`check_exit` 호출이 구성 단계에서 완전히 사라지며, §1.4의 결함 1(역방향 미검사)·2(직렬화 순서)가 원천 차단된다.

### 2.6 기하·수치 사실

- 다각형 꼭짓점은 float(소수 ≤4자리), 위치는 정수. shapely `buffer(0)` 복구(`utils.py:184-193`), 판정은 엄격한 `area > 0`(`utils.py:535,713,809`), AABB 프리필터는 경계 접촉을 겹침으로 치지 않음(`utils.py:458`).
- **"정수 NFP/자체 기하는 정확하다"는 주장은 금지한다.** 허용되는 두 형태만 존재한다: (i) **오라클 답 캐시** — 위치가 정수이므로 (shape, orient, shape, orient, Δx, Δy) 키의 shapely 판정값을 캐시하는 것은 근사가 아니라 저장이다; (ii) **보수적 프리필터** — "확실히 비겹침/확실히 겹침"만 분류하고 불확실 구간은 반드시 shapely로 판정(§5.3).
- 평가 서버는 미공개 timelimit("수 분~30분"), 4코어 제한. **60s는 개발 게이트일 뿐이다.** 모든 단계는 timelimit 파라미터에 비례해 동작해야 하고(anytime), 60s 하드코딩을 금지한다.

### 2.7 과거 오류 레지스터 (재발 금지 목록)

| # | 과거 오류 | 올바른 진술 | 현재 상태 |
|---|---|---|---|
| a | interlock exit 조건 방향 역전 ("neighbor exit ≤ new exit이면 overhang 허용") | §2.4 진리표: guest exit ≤ host exit, entry는 host가 strict하게 먼저 | 분석 문서에서는 제거됨. 진리표+테스트로 고정 |
| b | z2에 floor | obj2는 float 그대로 (`utils.py:1409-1419`) | 2026-07-09 잔존 7곳 수정: 문제분석 2벌 §2.7, week1:405, week2:21·454, week3:788·2121 |
| c | exit = entry + p 고정 | exit은 자유 변수; 지배성은 Mode A 국소(§2.2) | 이 문서로 스코프 명시 |
| d | 동일 시각 내 op 순서 과소평가 | Stage 5 리스트-순서 재생 + Stage 2 동시진입 포함 의미론(§2.1, §2.4) | 문제분석 2벌 §2.3 문구 수정 완료(2026-07-09); serializer 결함은 §3.4에서 해소 |
| e | "정수 NFP는 정확" | §2.6: 캐시=저장(정확), 래스터=보수적 필터, 판정 권위는 shapely 단일 | 이 문서로 정책 고정 |

## 3. M2 재정의: Fast Sharp Constructor

**목표 문장**: 300블록 인스턴스에서 구성+수리가 **5초 이내**에 전 블록을 배치하고, feasible-by-construction으로 Stage 5를 통과하며, 현재 활성 baseline보다 나은 dev-10 총 objective를 낸다.

### 3.1 슬라이스 M2-A: 의미론 테스트 하네스 + 계측 (선행 의무)

구현 전에 §2를 코드로 고정한다. `baseline/tests/test_semantics_contract.py` 신설:

1. 인터락 진리표 4행: 합성 2~3블록 인스턴스(버섯/T자 overhang)로 §2.4 표의 각 판정을 `check_feasibility` 결과(stage, 위반 문구)로 assert. 동시각 EXIT의 리스트 순서 케이스 포함.
2. 독립-배치 정리: union-서로소 배치 + 임의 동시각 op 순서가 Stage 5 통과함을 assert.
3. exit 연장 합법성: `exit > entry + p`가 Stage 1 통과, obj1 반영이 정확함을 assert.
4. obj2 float 패리티: 수작업 계산값과 `check_feasibility` obj2가 floor 없이 일치함을 assert.

계측: `_LAST_LNS_STATS`(이미 존재, `baseline_greedy.py:1097-1125`)에 구성 단계 카운터 추가 — `candidate_positions_tested`, `union_tests`, `aabb_rejects`, `per_block_time_p50/p95`. 벤치마크 행에 이미 `lns_stats`가 실리므로(`benchmark_instances.py:200-211`) 별도 배관 불요.

### 3.2 슬라이스 M2-B: 시간 인지 후보 엔진 v1 (Mode A)

새 함수군으로 구현하고 `greedyalgorithm(…, constructor_mode="fast_v1"|"legacy")`로 전환 가능하게 한다(기본은 legacy 유지, 수용 후 `myalgorithm`에서 fast_v1로 전환). 설계:

- **사전 계산(인스턴스 로드 시 1회)**: (block, orient)별 anchored layers, AABB, **union footprint 다각형**(shapely `unary_union`, 필요시 buffer(0)) 캐시. n_shapes ≈ 수백 개 수준이므로 <1s.
- **bay별 시간 인덱스**: 배치 확정분의 (entry, exit, AABB, union) 배열(numpy). 구간 [t, t+p)와 겹치는 블록 집합 O(t)를 벡터화 조회.
- **시간 후보**: 정렬된 `{release} ∪ {exit 이벤트 e > release} ∪ {entry 이벤트 a − p > release}` 상한 T=8개. (현재 코드는 `{release} ∪ {exits}`만 사용, `baseline_greedy.py:348` — 진입 직전 창을 놓친다.)
- **위치 후보**: 시각 t의 겹침 집합 O(t)에서만 생성. x후보 = {벽} ∪ {C.right − lx0}, y후보 = {벽} ∪ {C.top − ly0}. **(y, x) 사전식 순서로 순회하며 first-fit** — 전수 곱 평가 금지. (bay, orient)당 검사 앵커 상한 K=32.
- **feasibility 검사(Mode A)**: bay 경계(bbox) + O(t)의 각 블록과 AABB 프리필터(numpy 일괄) → 생존자만 union∩union 면적>0 검사. §2.5 정리에 의해 이것으로 Stage 2/3/4/5 전부 충족. **check_entry/check_exit 호출 없음.**
- **선택 규칙**: (bay, orient)마다 "가장 이른 feasible (t, 위치)" 1개를 얻고(내부 first-fit), 그 ≤ bays×orients개 후보를 기존 `_placement_score`(`baseline_greedy.py:204-230`, 의미 유지)로 비교해 최선을 확정. exit = entry + p (Mode A 국소 지배성, §2.2).
- **예산 사다리**: 블록마다 데드라인 검사(기존 `_check_deadline` 재사용). 페이스 미달 시 K→16→8, T→4→2, orient→bbox 최적 1개로 강등, 최후엔 기존 serial completion. 목표는 사다리가 **발동하지 않는 것**이며 발동 여부를 stats로 기록.
- **불변 유지**: `_serial_fallback_solution`, `myalgorithm.py`의 검증-후-반환 계약(M1), `_build_operations`의 EXIT-before-ENTRY는 그대로.

예상 비용: 블록당 (≤5 bay × ~4 orient × ≤8 t × 수 개 앵커) × (numpy AABB 일괄 + 소수 union 교차) → 인스턴스당 shapely 교차 10⁴~10⁵회 수준. 현재 대비 2~3자릿수 감소.

### 3.3 슬라이스 M2-C: left-shift polish (기존 M2 큐 항목 승계)

구성 직후, 지각(tardy) 블록을 우선순위 순으로 순회하며 더 이른 시간 후보로 재배치 시도(위치·방향 유지 → 실패 시 위치 재탐색). 검사는 §4.2의 표적 재검증을 재사용. obj1 직접 공략. 수용 조건: dev-10 obj1 감소, 런타임 예산 내.

### 3.4 슬라이스 M2-D: stagger fallback (Mode B, 게이트드)

Mode A로 tardiness가 남는 블록에 한해 union-겹침 후보를 허용하되:

- 양방향 검사 의무: 새 블록의 entry/exit + **겹침 집합의 각 기존 블록 C의 entry/exit 시각이 새 블록 체류 내에 있으면 C↔N 쌍 검사**(§1.4 결함 1의 해소).
- §2.4 진리표 제약(진입 strict 선행, 반출 nesting, 필요 시 host exit 연장)을 배치 시점에 강제.
- **canonical serializer**: `_build_operations`를 단일 직렬화 모듈로 승격. 같은 시각·같은 bay에 EXIT ≥ 2개면 "남은 present 집합에서 exit 가능한 블록부터"(check_exit fast=True) 위상 정렬. Mode A 산출물엔 오버헤드 0(§2.5로 자명).
- 게이트: max-layer 인스턴스(prob_9/32/40) 부분집합에서 objective 개선이 있을 때만 유지. 없으면 `parked`.

### 3.5 M2 수용 게이트

| 게이트 | 기준 |
|---|---|
| G-M2-1 (smoke-3, 15s) | 3/3 feasible Stage 5, 크래시·필드 누락 없음 |
| G-M2-2 (dev-10, 60s) | 10/10 feasible Stage 5; **전 인스턴스 `phase1_elapsed` ≤ 5s** 및 `greedy_placed_count == n_blocks`(트림·강제배치 0); 총 objective < 활성 baseline 3,620,223,014 |
| G-M2-3 (daily-40, 60s) | 40/40 feasible; 총 objective < 25,376,460,025; 새 baseline 아티팩트 기록 |
| 안전 회귀 | `test_submission_safety` 전체 green; timelimit 0.001s에서 feasible 반환 유지 |

수치는 목표치이며, 조정 시 근거를 experiment-log에 기록한다. 대형 인스턴스가 현재 serial-지배 상태이므로 G-M2-2/3의 objective 기준은 큰 폭으로 달성될 것으로 예상한다(prob_4 완주 증거, §1.2).

## 4. M3 재정의: 예산을 실제로 쓰는 LNS

전제: M2 통과로 60s 중 ≥ 45s가 개선 탐색에 남는다. 현행 small LNS 골격(incumbent 보존, 실패 시 폐기, `baseline_greedy.py:743-868`)은 유지하고 아래를 교체·추가한다.

### 4.1 반복 예산 구조 교체

- `max_iterations = min(25, n/4)`(`baseline_greedy.py:752`) 폐지 → 데드라인 기반 루프.
- 반복당 비용의 지배 요인인 **후보당 full `check_feasibility`**(`baseline_greedy.py:727-735`)를 §4.2로 대체. full check는 **수용 직전 후보에만** 실행(계약 유지: 수용된 incumbent는 항상 공식 체커 통과).

### 4.2 표적 재검증 (targeted revalidation) — 정확하며 근사가 아님

근거: Stage 1은 블록 단위, Stage 2/3/4는 (블록, 블록) 쌍 단위 술어이고(§2.1), Stage 5는 canonical serializer 규약 하에서 쌍 단위 장애물로 환원된다. 따라서 incumbent가 feasible할 때, 변경 집합 M에 대해 다음만 검사하면 후보 feasibility가 **정확히** 결정된다:

1. 각 m ∈ M: Stage-1 타이밍, bay 경계.
2. 각 m ∈ M vs 같은 bay 시간-겹침 블록 전부: Mode A 불변이면 union-서로소 검사만; Mode B 허용 시 k-k 충돌 + OBS 방향 제약 + 진리표 시간 조건.
3. 변경 없는 쌍은 재검사하지 않는다(판정 불변).

이동/재시간(retime)도 같은 기계로 처리된다(겹침 집합이 바뀌므로 2를 새 구간으로 수행). 구현 후 무작위 후보 1,000개에 대해 "표적 판정 == full check 판정" 패리티 테스트를 통과해야 한다.

### 4.3 오퍼레이터 로드맵 (각각 별도 슬라이스, 1세션 1가설)

| 오퍼레이터 | 유형 | 목적 성분 | 비고 |
|---|---|---|---|
| worst_objective / same_bay_time_window | destroy (기존) | obj1 | 유지 |
| random-k | destroy | 탈출 | 소형 추가 |
| left-shift batch | retime 전용 move | obj1 | 공간 불변, 표적 재검증만 |
| **exit-extension unblock** | retime | feasibility→obj1 | 막힌 exit을 방해자 exit 뒤로 지연; due 이내면 비용 0 (§2.2 레버) |
| bay-rebalance | move | obj2/obj3 | obj2/obj3는 할당만의 함수(§2.3) → 이득 정확 예측, 기하 재배치만 검증 |
| 수용 기준 | strict 개선(기존 eps 1e-6) 유지 → 정체가 측정되면 RRT(초기 3%, 선형 감소)를 플래그 뒤에 | | fable week2 채택분 |

증분 objective는 float로 정확 계산(obj1/obj3 델타, obj2는 bay 수 ≤5라 전량 재계산)하되 가이드 전용, 권위는 수용 시 full check(§2.3).

### 4.4 M3 수용 게이트

| 게이트 | 기준 |
|---|---|
| G-M3-1 (dev-10, 60s) | `lns_entered` 10/10; `lns_attempted_iterations` 중앙값 ≥ 100; `accepted_candidate_count ≥ 1`인 인스턴스 ≥ 7/10 |
| G-M3-2 (A/B) | 동일 커밋에서 `--lns-mode off` vs `small` 비교로 **LNS 귀속 개선** ≥ 1% (dev-10 총합) — §1.3의 귀속 오류 재발 방지 |
| G-M3-3 (daily-40, 60s) | 40/40 feasible; M2 baseline 대비 strict 개선 |

### 4.5 명시적 보류 (별도 증거 전까지)

full ALNS 포트폴리오(적응 가중치 포함 — 문헌상 기여 ~0.14%), CP-SAT 재타이밍, 병렬 체인. idea-bank 상태 유지.

## 5. M4: 배치/기하 성능 (프로파일링 게이트)

### 5.1 M4a: 프로파일링 + Python 수준 픽스

- 산출물: dev-10 대표 3개(대형 prob_20, max-layer prob_9, 쉬움 prob_21) cProfile 랭킹 아티팩트(`experiments/results/m4/profiling/…`). M2 이후 기준으로 잰다(M2가 지형을 바꾼다).
- 후보 픽스(랭킹 상위에 한해, 슬라이스당 1개):
  1. **쌍-오프셋 판정 캐시**: 위치가 정수이므로 (shapeA, oA, shapeB, oB, Δx, Δy) → shapely 판정값 dict 캐시. 오라클 답의 저장이므로 정확(§2.6). LNS의 반복 검사에서 적중률 높음. LRU 상한.
  2. present/overlap 집합 인덱스 고도화(정렬 이벤트 + bisect).
  3. `shapely.prepared` / STRtree, `Block` 객체 생성·재변환 오버헤드 제거.
  4. full `check_feasibility`의 수용-시 비용이 지배하면: Stage-4 쌍 열거에 공간 인덱스 적용(체커 자체는 불변 — 별도 사본으로, 판정 권위는 원본 유지).
- 게이트: 픽스당 해당 단계 벽시계 ≥ 20% 감소 **또는** LNS 반복 ≥ 2배, objective/feasibility 비회귀.

### 5.2 M4b: 보수적 래스터 프리필터 (게이트드)

M4a 이후에도 union/충돌 교차가 지배할 때만. (block, orient)당 full-cell(셀⊆다각형)/touched-cell(셀∩다각형≠∅) 비트마스크 2종 → "full 겹침 = 확실 충돌, touched 비겹침 = 확실 자유, 그 외 = 불확실 → shapely". 패리티 하니스(무작위 배치 수천 건, **불일치 1건 = 릴리스 블로커**). 순수 Python/numpy 폴백 필수, import 실패가 정확성에 영향 불가.

### 5.3 금지 사항

C++/컴파일 코어는 M4b로도 부족함이 측정된 뒤에만 별도 정당화. "체커와 동일 의미론 보장" 류의 주장은 패리티 아티팩트 없이 금지. 자체 기하 판정을 feasibility 권위로 삼는 설계 금지.

## 6. 실험 운영 규약

### 6.1 브랜치·아티팩트

`docs/strategy/m2-experiment-playbook.md`의 규칙을 M3/M4로 확장 적용한다:

- 브랜치: `codex/m2-fast-constructor`, `codex/m2-left-shift`, `codex/m2-stagger`, `codex/m3-targeted-reval`, `codex/m3-op-<name>`, `codex/m4a-<fix>` …
- 결과: `experiments/results/m2/fast_constructor/…`, `…/m3/<slice>/…`, `…/m4/<slice>/…` (기존 명명 패턴).
- 세션당 1슬라이스, 상태는 `accepted`/`rejected`/`parked`로 종결, experiment-log에 증거 기록.

### 6.2 측정 명령 (macOS 개발 머신 기준)

```bash
/opt/homebrew/Caskroom/miniforge/base/envs/ogc-2026/bin/python -m unittest discover -s baseline/tests -p 'test_*.py'
/opt/homebrew/Caskroom/miniforge/base/envs/ogc-2026/bin/python baseline/benchmark_instances.py --root . --set-name smoke-3 --solver myalgorithm --timelimit 15 --format json
/opt/homebrew/Caskroom/miniforge/base/envs/ogc-2026/bin/python baseline/benchmark_instances.py --root . --set-name dev-10 --solver myalgorithm --timelimit 60 --format json
```

주의: `data/train`, `data/train 2`는 git-ignore이며 벤치마크 실행 머신에만 존재한다. 문서 작업 환경에서 벤치마크 결론을 만들지 말 것.

### 6.3 측정 무결성 규칙 (신설 — §1.3 재발 방지)

1. 개선 주장에는 **구조적 증거**를 함께 요구한다: LNS 개선이면 `accepted_candidate_count > 0`과 `--lns-mode off` A/B, 구성 개선이면 `phase1_elapsed`/`greedy_placed_count` 변화.
2. 데드라인 근처에서 갈리는 결과(트림 발동 등)는 벽시계 잡음일 수 있다. 잡음 대역은 동일 설정 재실행 1회로 추정하고, 그 이내의 델타로 수용/기각하지 않는다.
3. 서로 다른 커밋의 아티팩트를 비교할 때는 두 커밋 간 관련 코드 diff가 없음을 확인하거나 같은 커밋에서 재측정한다.

## 7. 재발 방지 프로토콜 (계획 문서 작성·구현 공통)

1. **방향성 규칙은 진리표+테스트 선행**: ≤/≥, 먼저/나중, 위/아래가 들어가는 모든 규칙은 (i) utils.py 앵커에서 유도, (ii) §2.4식 진리표 표기, (iii) `check_feasibility`를 오라클로 쓰는 단위 테스트 작성이 끝나기 전에는 휴리스틱/코드에 사용 금지.
2. **목적함수 권위 단일화**: 내부 objective/델타는 반드시 패리티 테스트(±1e-6)를 갖고, 수용 판정은 full check 값으로 확정. floor/int 캐스팅 발견 즉시 결함 처리.
3. **exit 자유도 스코프 명시**: `exit = entry + p`를 쓰는 모든 코드/문서는 "Mode A 국소 지배성" 근거를 주석/본문으로 달아야 하며, 전역 지배성 주장은 금지.
4. **직렬화 단일 경로**: operations dict는 canonical serializer 한 곳에서만 생성. 임의 지점의 수제 조립 금지.
5. **기하 주장 검열**: "정확/동일 의미론" 주장은 (오라클 캐시) 또는 (패리티 아티팩트 링크) 중 하나를 동반해야 함.
6. **문서 앵커 의무**: 의미론 서술에는 `file:line` 앵커. 앵커가 무효화되면(코드 변경) 문서를 같은 PR에서 갱신.
7. **fable 자료 채택 절차**: idea-bank 항목화 → 이 문서의 슬라이스로 편입 → 게이트 통과 후 baseline화. 문서 통째 이식 금지.

## 8. 즉시 다음 행동

1. 다음 세션: `codex/m2-fast-constructor` 브랜치에서 **M2-A(테스트 하네스+계측) → M2-B(fast_v1 엔진)** 구현. `constructor_mode` 플래그로 legacy와 A/B.
2. smoke-3 → dev-10 순서로 측정, G-M2-1/2 판정. 통과 시 daily-40(G-M2-3) 후 `myalgorithm` 전환 및 새 baseline 기록.
3. M2 수용 후: M3-표적재검증 슬라이스(G-M3-1/2), 이후 오퍼레이터 슬라이스 순차.
4. M4a 프로파일링은 M2 수용 직후 1회 실행해 아티팩트만 남기고, 픽스는 M3와 병행하지 않는다(1세션 1슬라이스).
5. 현행 코드의 §1.4 결함 1(역방향 스윕)·2(동시각 EXIT 순서)는 legacy 경로에서는 **수정하지 않는다** — fast_v1(Mode A)이 구조적으로 제거하며, legacy는 비교 기준으로 동결한다.
