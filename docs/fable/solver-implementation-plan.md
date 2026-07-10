# OGC 2026 솔버 구현 명세 (Implementation Plan)

Last updated: 2026-07-09
Status: active — `docs/strategy/solver-design.md`(이하 "설계서")의 구현 명세. 설계서가 "무엇을·왜"를 소유하고, 이 문서가 "정확히 어떻게"를 소유한다. 두 문서가 충돌하면 설계서 §2(체커 의미론)가 최우선이고, 그다음 이 문서가 우선한다.

표기: `dwell_i = max(P_i, 1)` (설계서 §2.6, P=0 체류≥1). `slack_i = D_i − R_i − P_i`. 모든 의사코드는 Python 3.12 기준.

---

## 1. 모듈 구조와 핵심 데이터 구조

### 1.1 파일 레이아웃

```text
baseline/
  myalgorithm.py          # 유지: 안전 셸(검증-후-반환). solver.entry.solve()에 위임
  solver/
    __init__.py
    entry.py              # solve(prob_info, timelimit) — 파이프라인 오케스트레이션
    budget.py             # Budget: deadline, 단계 예산, 소예산 사다리
    instance.py           # 파싱, 파생값(slack, dwell, fit 행렬, 환율표)
    geometry.py           # GeomKernel: 사전계산, 판정, 캐시, 앵커 생성
    state.py              # Placement, SolutionState, undo-log, 증분 objective
    validate.py           # 표적 재검증(정확) + full check 래퍼
    serialize.py          # canonical serializer (유일한 operations 생성 경로)
    trivial.py            # T0 순차 단독 배치
    assign.py             # 초기 bay 할당 v1(greedy) / v2(CP-SAT)
    construct.py          # 삽입식 constructor + 우선순위 프로파일 + multi-start
    retime.py             # bay별 CP-SAT retiming
    alns.py               # ALNS 루프: destroy/repair 풀, 적응 가중치, SA 수용
    interlock.py          # (S6, 기본 off) OBS 판정·stagger 삽입·직렬화 확장
  tests/
    test_contract_semantics.py   # 설계서 §2 정리·진리표·엣지 (구현 전 작성)
    test_geometry_kernel.py
    test_state_parity.py         # 증분 objective ↔ 체커 패리티
    test_validate_parity.py      # 표적 재검증 ↔ full check 일치
    test_construct.py
    test_retime.py
    test_alns.py
    test_serialize.py
    test_entry_armor.py          # 예산/예외/소예산 사다리
```

### 1.2 Placement / SolutionState

```python
@dataclass(slots=True)
class Placement:
    bay: int; x: int; y: int; o: int; a: int; e: int   # 전부 int

class SolutionState:
    """단일 진실: 블록별 Placement. 나머지는 파생·증분 유지."""
    plc: list[Placement]                  # index = block_id, 전 블록 배치 후 None 없음
    bay_members: list[set[int]]           # bay -> block ids
    # bay별 numpy 미러(병렬 배열; 멤버 변경 시 재구축, n<=수백이라 O(n) 재구축 허용):
    #   arr_id[b], arr_a[b], arr_e[b], arr_bb[b] (n_b x 4: 월드 AABB)
    z1: float                             # Σ max(0, e−D)  (가중 전)
    z3: float                             # Σ (Smax − S[bay])
    loads: list[float]                    # bay별 Σ workload
    def z2(self) -> float:                # 가중 부하 range (설계서 §2.2)
        w = [u[j] * self.loads[j] for j in range(m)]
        return max(w) - min(w) if m >= 2 else 0.0
    def objective(self) -> float:
        return w1 * self.z1 + w2 * self.z2() + w3 * self.z3
```

- **undo-log 방식**: `apply(move) -> UndoToken`, `undo(token)`. deep copy 금지(파괴 크기 q ≤ 수십이므로 O(q) undo가 압도적으로 싸다). ALNS의 "후보 폐기"는 undo로 구현한다.
- z1/z3/loads는 place/remove 시 O(1) 증분, z2는 호출 시 O(m) 재계산(m ≤ 수 개). 전부 float 그대로(내림 없음) — 체커와 패리티 테스트 의무(§8).
- 증분값은 **탐색 가이드**다. incumbent 교체 시에는 full check의 objective를 저장·보고한다(설계서 §1.3).

### 1.3 incumbent 프로토콜 (state와 분리)

```python
class Incumbent:
    ops: dict          # 이미 직렬화된 operations (반환 즉시 가능)
    objective: float   # check_feasibility가 보고한 값 (권위)
    def try_update(self, state) -> bool:
        sol = serialize(state)                      # §6.1
        res = check_feasibility(prob_info, sol)     # full check (권위)
        if res["feasible"] and res["objective"] < self.objective - EPS_ABS:
            self.ops, self.objective = sol, res["objective"]; return True
        return False   # 불일치·악화 시 무시 (탐색은 계속)
```

`EPS_ABS = 1e-9 · max(1, |objective|)`. full check가 infeasible을 보고하면(표적 재검증과 불일치 — 버그 신호) 카운터 증가 + 해당 후보 폐기, 절대 예외 전파 금지.

---

## 2. 기하 커널 (`geometry.py`)

### 2.1 사전계산 (인스턴스당 1회)

```python
class ShapeInfo(slots):
    layers: list[np.ndarray]     # 로컬 좌표 (reference = layer0 첫 꼭짓점 = (0,0))
    aabb: tuple                  # 전 레이어 로컬 AABB (lx0, ly0, lx1, ly1)
    layer_aabbs: list[tuple]
    union: ShapelyPolygon        # unary_union(layers), invalid면 buffer(0)
    union_prepared: PreparedGeometry
    area: float; n_layers: int
    shape_key: int               # 반올림(1e-4) 꼭짓점 튜플 해시 — 동일 형상 캐시 공유
```

- `fit[i][j] = [o for o in range(O_i) if lx1−lx0 ≤ W_j and ly1−ly0 ≤ H_j]` — 할당 하드 제약.
- 유효 x 범위(정수): `x ∈ [ceil(−lx0), floor(W_j − lx1)]`, y 동일. (경계 접촉 합법 — 설계서 §2.6.)

### 2.2 쌍 판정 (체커 의미론 그대로)

```python
def union_disjoint(si: ShapeInfo, sj: ShapeInfo, dx: int, dy: int) -> bool:
    """area(U_i ∩ U_j) == 0 인가. 체커와 동일한 area>0 기준 (설계서 §4.2)."""
    key = (si.shape_key, sj.shape_key, dx, dy)
    if key in cache: return cache[key]
    uj = translate(sj.union, dx, dy)              # shapely.affinity.translate
    if not si.union_prepared.intersects(uj): v = True          # 서로소 확정
    else: v = si.union.intersection(uj).area <= 0.0            # 접촉(면적 0)은 합법
    cache[key] = v; return v
```

- 캐시 키가 (shape_key, shape_key, Δx, Δy)뿐인 이유: 판정은 평행이동 불변이고 위치가 정수라서다(설계서 §4.2 — 오라클 답 저장). LRU 상한 2^20 엔트리(≈150–200MB 상한), 적중률 계측.
- `obs(si, sj, dx, dy) -> (obs_ij, obs_ji)`: 인터락용 방향 술어 `OBS(X;Y) = ∃k≤j: area(X_k ∩ Y_j) > 0`. 레이어별 AABB 프리필터 후 필요한 (k, j) 쌍만 정확 판정. **interlock 모듈에서만 호출** (기본 off).
- AABB 일괄 필터: 후보 (x, y)에 대해 `arr_bb[b]`와 numpy 브로드캐스트 비교로 생존 이웃만 shapely 경로에 보낸다. AABB 서로소면 union 서로소이므로 캐시 조회조차 생략.

### 2.3 앵커 생성 (정수 좌표 공식)

후보 시각 t에서 공존 집합 `O(t) = {j: a_j < t + dwell_i and e_j > t}` (numpy 마스크). 이웃 j의 월드 AABB (cx0, cy0, cx1, cy1)에 대해:

```text
x 후보:  x_min = ceil(−lx0)  ∪  { ceil(cx1 − lx0) : j ∈ O(t) }      # 우측 접촉
y 후보:  y_min = ceil(−ly0)  ∪  { ceil(cy1 − ly0) : j ∈ O(t) }      # 상단 접촉
앵커    = (정렬·중복제거된 y 후보) × (정렬·중복제거된 x 후보), y-major 순회
검사    = 유효범위 클립 → AABB 일괄 → union_disjoint (survivors만)
중단    = first-fit (해당 (bay, o, t)에서 첫 통과 앵커), 앵커 시도 상한 K
```

ceil로 인해 실수 경계에 최대 1 미만의 틈이 생기는 것은 정수 좌표 제약의 필연이다(정의서 §8). K 기본 32, 에스컬레이션 시 64.

---

## 3. Constructor (`construct.py`)

### 3.1 우선순위 프로파일 (multi-start의 축)

| ID | 정렬 키 (오름차순) | 의도 |
|---|---|---|
| PF1 | `(slack, D, −area·P)` | 빠듯한 블록 먼저 (기본) |
| PF2 | `(D, −P)` | EDD |
| PF3 | `(−area·dwell, slack)` | 큰 블록 먼저 (패킹 난이도 우선) |
| PF4 | `(R, D)` | 도착순 |

편향 무작위 복제: 프로파일 순열에서 `k = min(⌊ln(U)/ln(1−p)⌋, 남은 수−1)`, `p = 0.25` 기하분포로 다음 블록을 뽑는 변형을 시드만 바꿔 생성. 결정적 4종 → 그 뒤 무작위 변형 (구성 예산이 남는 한).

### 3.2 블록 1개 삽입 (핵심 커널 — ALNS repair도 동일 경로)

```python
def insert_block(state, i, bays_try, *, allow_tardy=True, noise=None) -> Candidate | None:
    best = None
    for b in bays_try:                                  # §3.4의 bay 순서
        for o in fit[i][b]:
            # 시간 후보 (설계서 §4.3): 완전성은 이벤트 경계 논거
            times = sorted({R_i} | {e_j for j in bay} | {a_j - dwell_i for j in bay})
            times = [t for t in times if t >= R_i][:T_CAP]          # T_CAP=8, 에스컬레이션 16
            for t in times:
                pos = first_fit_anchor(state, i, b, o, t)           # §2.3
                if pos is None: continue
                tard = max(0, t + dwell_i - D_i)
                if tard > 0 and not allow_tardy: break
                c = cost(i, b, t, tard, pos)                        # §3.3
                if noise: c *= rng.uniform(1 - noise, 1 + noise)
                if best is None or c < best.cost: best = Candidate(b, o, pos, t, c)
                break            # 이 (b,o)의 최조기 feasible만 취함 — §2.5 근거
    return best                  # None이면 호출자가 에스컬레이션 (§3.5)
```

- `(b, o)`당 최조기 t 하나만 취해도 되는 근거: 비-인터락에서 exit = a + dwell이고 tardiness는 t에 단조라, 같은 (b, o)에서 더 늦은 t는 지배당한다(공간 점수 차이는 tie-break 수준으로 격하 — 설계서 §2.5).
- exit는 항상 `a + dwell` (비-인터락 규약; 인터락은 `interlock.py`가 소유).

### 3.3 삽입 비용 함수 (repair 공용)

```text
cost(i,b,t,tard,pos) = w1·tard
                     + w3·(S_i^max − S_ib)
                     + w2·Δz2(load_b += L_i)          # 닫힌 식 (range 재계산)
                     + η·space(pos)                    # η = 0.01·max(w1, 1) — 동률에서만 작동
space(pos) = 0.5·top_norm + 0.3·zoning + 0.2·x_norm
  top_norm = (y + ly1)/H_b,  x_norm = (x + lx1)/W_b   # bottom-left 유도
  zoning   = (dwell_i/med_dwell − 1)_+ · corner_dist(pos)/diag_b   # 장기 체류는 구석으로
```

### 3.4 bay 순회 순서와 bay-bump

- 구성 단계: 할당 결과 `b_i`만 시도(`bays_try = [b_i]`). tardiness가 발생하면 **bay-bump**: `bays_try = fit 가능한 전 bay`로 재시도하고, 총비용(cost 함수가 w2/w3 반영)이 엄격히 낮을 때만 이주 — 상위 레벨 결정을 하위가 국소 수정하는 유일한 지점.
- ALNS repair 단계: destroy가 D6(bay 재배분)이면 전 bay, 아니면 `[원래 bay] + fit bay` 순.

### 3.5 에스컬레이션 사다리 (블록 단위)

1. `T_CAP 8→16`, `K 32→64`로 재시도.
2. bay-bump (§3.4).
3. (S6 활성 시) 인터락 stagger 시도 — `interlock.py`.
4. 지연 수용: 시간 후보를 tardy 영역까지 확장해 최소 tardiness 삽입.
5. (이론상 도달 불가, 방어) 빈-bay 단독 창 배치: `t = max(R_i, 그 bay 전 구간이 비는 최초 시점)` — T0와 같은 논거로 항상 성공.

### 3.6 multi-start 드라이버

구성 예산(§6.3) 안에서 프로파일들을 순차 실행, 각 결과를 full check 없이 증분 objective로 비교(전부 같은 코드 경로라 비교는 공정), 최선 하나만 full check → incumbent 등록 → ALNS로 전달. 각 시작은 독립 `SolutionState`.

---

## 4. ALNS 상세 (`alns.py`)

### 4.1 전체 루프 (current/best 분리 — SA와 armor의 공존)

```python
def alns(state, incumbent, budget):
    current = state                          # feasible 보장 (구성 산출물)
    cur_obj = current.objective()
    ops = OperatorPools(destroys=[D1..D6], repairs=[R1..R3])
    sa = SAAcceptor(warmup=40)               # §4.5
    while budget.remaining_lns() > 0:
        d = ops.pick_destroy(); r = ops.pick_repair()
        removed, undo_d = d.apply(current)                    # 제거 (undo-log)
        ok, undo_r = r.apply(current, removed)                # 재삽입 시도
        if not ok:                                            # repair 실패 → 원상복구
            current.undo(undo_r); current.undo(undo_d)
            ops.report(d, r, OUTCOME_FAIL); continue
        # 이 시점 current는 표적 재검증을 통과한 feasible 후보 (§4.6이 repair 내부에서 보장)
        new_obj = current.objective()
        if sa.accept(new_obj - cur_obj):
            cur_obj = new_obj
            outcome = OUTCOME_ACCEPT_WORSE if new_obj >= cur_obj else OUTCOME_BETTER
            if new_obj < incumbent.objective - EPS:
                if incumbent.try_update(current):             # full check는 여기서만
                    outcome = OUTCOME_NEW_BEST
        else:
            current.undo(undo_r); current.undo(undo_d)        # 거부 → 원상복구
            outcome = OUTCOME_REJECT
        ops.report(d, r, outcome)
        retime_trigger.maybe_run(current, incumbent, budget)  # §4.7
        stall_ctl.step(incumbent)                             # §4.5 리히트/재출발
```

핵심 규약: **current도 항상 feasible**이다(repair가 표적 재검증을 통과한 삽입만 커밋). full check는 incumbent 경계에서만 발생하므로 SA가 아무리 많은 후보를 수용/거부해도 안전성과 비용이 통제된다.

### 4.2 Destroy 연산자 6종

공통: 파괴 크기 `q ~ U{q_min, q_max}`, `q_min = max(2, ⌈0.02n⌉)`, `q_max = max(4, ⌈0.06n⌉)`; 정체 시 `q_max ← min(2q_max, ⌈0.15n⌉)`. 편향 선택은 `idx = ⌊N_cand · U^ρ⌋` (U~U(0,1); ρ 클수록 상위 집중).

| ID | 이름 | 선택 규칙 (정확 명세) | 파라미터 |
|---|---|---|---|
| D1 | random | 균등 무작위 q개 | — |
| D2 | worst_tardiness | 블록을 `w1·max(0, e_i−D_i)` 내림차순 정렬, 편향 ρ=3으로 q개. tardiness 0인 인스턴스면 `e_i−R_i−dwell_i`(대기시간) 내림차순으로 대체 | ρ=3 |
| D3 | critical_chain | 지각 블록 i에서 시작해 **결박 선행자 사슬**을 제거: 같은 bay에서 union-겹침이고 `e_j == a_i`(빡빡하게 막는 쌍)인 j를 따라 역방향으로. 사슬 < q면 D5 관련도로 채움 | 사슬 상한 q |
| D4 | time_window | bay를 `Σ tardiness` 비례 룰렛으로 선택, 그 bay의 지각 블록 하나의 `[a, e)`를 중심으로 폭 `W = 2·median(dwell)` 창과 교차하는 블록 전부(상한 q, 초과 시 교차길이 상위 q) | W 배수 1.5씩 확장 |
| D5 | shaw_related | seed를 D2 규칙으로 1개 → `rel(i,j) = 0.5·dist(AABB중심)/diag_b + 0.25·|a_i−a_j|/H + 0.25·|e_i−e_j|/H + 1.0·[b_i≠b_j]` 오름차순, 편향 ρ=6으로 q−1개 | φ=(0.5,0.25,0.25,1.0), ρ=6 |
| D6 | bay_rebalance | `argmax_j u_j·load_j`(z2 지배 bay)에서 `w3·(S_ib − S_ib') 손실이 작은` 블록을 손실 오름차순 편향 ρ=4로 q개 (b' = 두 번째 선호 bay). **w2·(예상 Δrange) + w3·손실 < 0 후보가 없으면 이 연산자는 스킵을 보고** | ρ=4 |

D3의 "결박 선행자"가 정확히 정의되는 근거: 비-인터락 해에서 i의 진입을 막는 것은 union-겹침 쌍의 시간 분리 제약뿐이므로(설계서 §2.4), `e_j == a_i`인 겹침 쌍이 곧 활성 제약이다.

### 4.3 Repair 연산자 3종

공통 계약: (i) 모든 삽입은 `insert_block`(§3.2) 경로 — 표적 재검증 포함. (ii) **하나라도 못 넣으면 실패를 반환**하고 호출자가 undo (부분 커밋 금지). (iii) 실패 직전 최후 수단으로 "남은 블록들을 원래 Placement 그대로 복원 시도"(자리가 비어 있으면 성공) 후에도 안 되면 실패.

| ID | 이름 | 삽입 순서와 규칙 | 파라미터 |
|---|---|---|---|
| R1 | greedy_noise | 제거 집합을 PF1(slack) 순으로, `cost`에 곱셈 노이즈 U[1−δ, 1+δ] | δ=0.10 |
| R2 | regret2 | 각 미삽입 블록의 최선 `c1`·차선 `c2` 삽입 비용(서로 다른 (bay,o) 조합 기준)을 계산, `c2−c1` 최대인 블록부터 확정. 확정 후에는 **그 블록과 같은 bay를 최선/차선으로 갖던 블록만** 재평가(lazy) | 차선 없으면 c2 = c1 + w1·H (강제 우선) |
| R3 | earliest_due | EDD 순, 노이즈 없음, allow_tardy=True — 안전한 기준 repair | — |

D6와 R1/R2 조합 시 `bays_try = fit 전 bay`(§3.4)로 자동 확장되어 bay 간 재배분이 실제로 일어난다.

### 4.4 적응 가중치 (Ropke–Pisinger 방식, 그대로 명시)

- destroy 풀과 repair 풀을 **독립적으로** 룰렛 선택: `Pr(op) ∝ w_op`.
- 세그먼트 = 100 iteration. 세그먼트 동안 점수 적립: 새 incumbent = **33**, current 개선 수용 = **9**, 악화 수용 = **13**, 거부/실패 = 0.
- 세그먼트 종료 시: `w_op ← (1−r)·w_op + r·(π_op / max(1, θ_op))`, 반응계수 `r = 0.1`, θ = 사용 횟수. 초기 w = 1.
- `alns_adaptive=False` 플래그로 정적 균등과 A/B 가능해야 한다(S3 게이트에서 ablation — 문헌상 기여가 작을 수 있으므로 측정으로 결정).

### 4.5 SA 수용 기준 (요청 사항 — 정확 명세)

**왜 SA인가**: Δ의 절대 스케일이 인스턴스마다 수십 배 다르므로(w1이 4자릿수 등) 고정 임계 방식은 이식성이 없다. SA는 T0를 관측 Δ로 보정하면 스케일-불변이 된다. incumbent가 분리되어 있으므로(§4.1) SA의 악화 수용은 −1 위험과 무관하다.

```python
class SAAcceptor:
    # [1] 워밍업: 첫 40 iteration은 개선만 수용하며 양의 Δ 표본 수집
    # [2] 보정:  T0 = median(positive Δ) / ln(2)    # "중앙값 악화를 확률 0.5로 수용"
    #            T_end = T0 / 1000
    # [3] 스케줄: T(τ) = T0 · (T_end/T0)^τ,  τ = LNS 경과시간/LNS 총예산  (시간 기반 —
    #            timelimit 미공개에 대한 P2 원칙: iteration 수가 아니라 벽시계로 냉각)
    # [4] 수용:  Δ ≤ 0 → 항상.  Δ > 0 → 확률 exp(−Δ / T)
    # [5] 리히트: stall(§아래) 1회당 T ← max(T, 0.3·T0), 최대 2회 연속
```

- 워밍업 중 양의 Δ 표본이 5개 미만이면(개선이 계속 나오는 좋은 상황) T0 = 0.005·current_obj로 폴백.
- **stall 정의**: incumbent 갱신이 `max(30 iter, 0.10·LNS예산 초)` 동안 없음. stall 1·2회차 = 리히트 + `q_max` 확대(§4.2). 3회차 = **perturb 재출발**: D5를 q = ⌈0.20n⌉로 1회 무조건 적용·재삽입(수용 검사 없이 current 교체, 단 feasible 조건은 유지), τ는 그대로 진행.
- 대안 수용(RRT: `accept iff new < best·(1+θ(τ))`, θ 0.03→0 선형)은 `acceptor="rrt"` 플래그로 구현해 두고 S3 게이트에서 SA와 A/B.

### 4.6 표적 재검증 (repair 내부의 정확 feasibility)

`insert_block`이 후보 (b, o, x, y, t)를 수락하기 전에 확인하는 것(설계서 §2.3·§2.4의 구현):

```python
def placement_ok(state, i, b, o, x, y, t):
    if o not in fit[i][b]: return False
    if not in_range(x, y): return False                     # §2.1 유효범위 (경계 접촉 허용)
    e = t + dwell_i
    for j in overlap_set(b, t, e):                           # numpy 마스크
        dx, dy = x - x_j, y - y_j
        if union_disjoint(shape_i_o, shape_j_oj, dx, dy):    # 갈래 2
            continue
        return False                                         # 비-인터락 모드: 갈래 3 미사용
    return True
```

- 시간 분리(갈래 1)는 `overlap_set`이 이미 배제한다(`a_j < e and e_j > t`, 등호 처리로 같은 날 손바뀜 허용).
- 정확성: 바뀐 블록이 관여하는 쌍만이 새 위반의 후보라는 쌍-분해 정리(설계서 §2.3). 무작위 후보 1,000건에 대해 full check와 판정 일치하는 패리티 테스트가 S0 게이트(§8).
- destroy는 제거만 하므로 재검증이 불필요하다(쌍이 줄기만 함).

### 4.7 retime 트리거 정책

- bay별 dirty 카운터: 그 bay에서 **수용된** 공간 변경(삽입 위치/bay 변경) 횟수.
- 실행 조건: `dirty[b] ≥ max(5, 0.05·n_b)` **이고** 그 bay의 직전 retime 후 `0.03·TL` 이상 경과.
- 실행 방법: §5.1 모델을 현 스케줄 hint로 풀고, never-worse 가드로 채택. 채택되면 incumbent.try_update까지 시도.
- 마감 전 최종 스윕: `deadline − reserve − clamp(0.05·TL, 2s, 30s)` 시점에 전 bay 1회씩(예산 내에서 tardiness 큰 bay부터).

---

## 5. CP-SAT 적용 상세 (`retime.py`, `assign.py`)

### 5.1 bay retiming 모델 (요청 사항 — 코드 수준)

```python
def retime_bay(blocks, cur, conflict_pairs, timebox) -> dict | None:
    m = cp_model.CpModel()
    Hb = max(R_i for i in blocks) + sum(dwell_i for i in blocks)   # 안전 상계 (직렬 완료)
    a, e, T = {}, {}, {}
    for i in blocks:
        a[i] = m.NewIntVar(R[i], Hb, f"a{i}")
        e[i] = m.NewIntVar(R[i] + dwell[i], Hb + dwell[i], f"e{i}")
        m.Add(e[i] == a[i] + dwell[i])                 # 비-인터락 규약 (설계서 §2.5)
        T[i] = m.NewIntVar(0, Hb + dwell[i], f"T{i}")
        m.Add(T[i] >= e[i] - D[i])                     # T = max(0, e−D)는 최소화가 보장
    for (i, j) in conflict_pairs:                      # union-겹침 쌍만 (캐시 판정)
        b = m.NewBoolVar(f"b{i}_{j}")
        m.Add(e[i] <= a[j]).OnlyEnforceIf(b)           # ≤ : 같은 날 손바뀜 허용 (§2.4 갈래 1)
        m.Add(e[j] <= a[i]).OnlyEnforceIf(b.Not())
        m.AddHint(b, 1 if cur.e[i] <= cur.a[j] else 0)
    for i in blocks: m.AddHint(a[i], cur.a[i])
    m.Minimize(sum(T.values()))                        # w1은 bay 내 상수 — 생략 가능
    sv = cp_model.CpSolver()
    sv.parameters.max_time_in_seconds = timebox
    sv.parameters.num_search_workers = 4
    st = sv.Solve(m)
    if st not in (cp_model.OPTIMAL, cp_model.FEASIBLE): return None
    if sv.ObjectiveValue() >= cur.z1_bay - 1e-9:       return None   # never-worse 가드
    return {i: (sv.Value(a[i]), sv.Value(e[i])) for i in blocks}
```

- **정확성 근거**: 위치 고정 + 비-인터락에서 쌍 제약은 정확히 "union-겹침이면 시간 분리(≤)"뿐이다(설계서 §2.4). 따라서 이 모델의 최적해는 그 layout이 허용하는 최소 Σ tardiness다. `conflict_pairs`는 AABB 프리필터 → 판정 캐시로 O(n_b²) 조회.
- 채택 절차: 반환 스케줄을 state에 적용 → (시간만 변했으므로) 표적 재검증은 conflict_pairs의 분리 방향 확인으로 대체 가능하나, **규약대로 full check를 거쳐 incumbent 갱신 시도**. state의 current에는 무조건 반영(개선 확정이므로).
- 인터락 변형(S6): 인터락 쌍 (h, g)는 disjunction 대신 `a[h] + 1 <= a[g]`, `e[g] <= e[h]`를 추가하고 h의 `e[h] == a[h]+dwell` 등식을 `>=`로 완화. 그 외 동일.
- 타임박스: `min(5s, 0.10·잔여예산)`. 상태·시간 계측을 벤치 행에 기록.

### 5.2 초기 할당 모델 v2 (CP-SAT; v1 greedy와 포트폴리오)

```python
# 변수: x[i,j] ∈ {0,1}  (fit[i][j] 비어 있으면 변수 생성 안 함)
# Σ_j x[i,j] == 1
# 부하(정수 스케일): S = 10^6, U_j = round(u_j·S), L_i = round(workload_i·SL)
#   Lw[j] = Σ_i U_j·L_i·x[i,j]
# range 선형화: Wmax ≥ Lw[j] ∀j,  Wmin ≤ Lw[j] ∀j,  Dv = Wmax − Wmin
# 혼잡 소프트캡: Σ_i round(area_i·dwell_i)·x[i,j] ≤ cap_j + ov[j]
#   cap_j = ⌊κ·W_j·H_j·T_span⌋, κ=0.85, T_span = max(D)−min(R)
# 목적(정수):  min  w3·SL·S·Σ pen_ij·x[i,j]  +  w2·SL·Dv  +  λ·Σ ov[j]
#   λ = w1·SL·S / max(1, mean(area·dwell))   # 과부하 1단위 ≈ 지각 위험의 보수적 환산
```

- 채택 규칙: **v1 greedy와 v2를 모두 구성기에 통과시켜**(구성 예산이 2·5s 이상일 때) 증분 objective가 낮은 쪽을 채택. 예산이 빠듯하면 v1만. Δ가 미미하면 v1(단순한 쪽).
- 타임박스 1s, workers 4, hint = v1 결과. Z1 정밀 트레이드는 ALNS의 D6/bay-bump가 담당(설계서 §4.6).

### 5.3 MIP 대안 정식화 (참고)와 CP-SAT 선택 이유

retiming을 MIP로 쓰면: 연속/정수 변수 a_i, T_i ≥ e_i − D_i, T_i ≥ 0, 분리 쌍마다 이진 y_ij와 big-M 2개(`a_j ≥ e_i − M(1−y_ij)`, `a_i ≥ e_j − M·y_ij`, M = Hb). 동일 모델이 gurobipy/xpress로도 가능하지만 채택하지 않는 근거:

1. big-M LP 완화가 약해 동급 시간 내 증명 품질이 CP-SAT hint+CP 전파보다 보통 못하다 (순수 disjunctive + 정수 시간 구조는 CP-SAT의 홈그라운드).
2. 서버의 gurobipy/xpress는 **라이선스 상태를 검증할 수 없고**, 최종 코드 공개 요건(정의서 §11)과 상충 위험. 실패 시 런타임 예외 = −1 위험.
3. 결정: OR-Tools CP-SAT 단일. gurobi 경로는 만들지 않는다(코드 경로 수 = 버그 표면).

### 5.4 CP-SAT 공통 규약

worker 4(정의서 §10의 4코어 상한), 항상 hint 제공, 항상 타임박스, 상태가 OPTIMAL/FEASIBLE가 아니면 조용히 무시(카운터만), 모델 빌드도 deadline 검사 하에. `random_seed` 고정.

---

## 6. 직렬화기 · 예산 관리자 · 진입점

### 6.1 canonical serializer (`serialize.py`)

```python
def serialize(state) -> dict:
    buckets: dict[int, list] = {}
    for i, p in enumerate(state.plc):
        buckets.setdefault(p.e, []).append(("EXIT", i, p))
        buckets.setdefault(p.a, []).append(("ENTRY", i, p))
    ops = {}
    for t in sorted(buckets):
        exits  = order_exits(buckets[t])    # 비-인터락: block_id 순 (임의 순서 안전 — 설계서 §2.4 갈래 2)
                                            # 인터락 활성: 같은 bay 2개 이상이면 guest-first 위상 정렬,
                                            # 실패(사이클) 시 SerializeError → 후보 폐기
        entries = sorted_entries(buckets[t])  # block_id 순 (동시 진입 합법 조합은 순서 무관)
        ops[str(t)] = exits + entries       # EXIT 전부 → ENTRY 전부 (utils.py:1293-1302)
    return {"operations": ops}
```

x, y, orient_idx, bay_id, block_id는 전부 `int()` 강제. **이 함수 밖에서 operations dict를 만드는 코드는 금지**(리뷰 체크리스트 항목).

### 6.2 T0 (`trivial.py`)

블록을 (R, D, P) 순으로 정렬해 각 bay의 "완전히 빈 시간 창"에 순차 배치(선호 bay 우선, fit 검사). 진입·반출 시 bay가 비어 있으므로 스윕이 자명히 통과(설계서 §4.1). n번의 창 계산 = O(n·log) — 1초 미만. full check 1회 후 incumbent 등록.

### 6.3 예산 관리자 (`budget.py`) — 소예산 사다리 포함

```text
reserve   = clamp(0.05·TL, 3s, 60s)          # 마감 방어 (반환은 O(1)이므로 여유분)
deadline  = t_start + TL − reserve
TL < 3s   : T0만 만들고 반환
3–12s     : T0 + PF1 구성 1회 (+ 남으면 retime 스윕 1회)
12–60s    : 전 파이프라인, 구성 예산 = 0.15·TL, retime 첫 스윕 = 0.10·TL, 나머지 ALNS
≥ 60s     : 구성 = clamp(0.10·TL, 6s, 30s), retime 첫 스윕 = clamp(0.08·TL, 4s, 40s),
            최종 스윕 = clamp(0.05·TL, 2s, 30s), 나머지 전부 ALNS
```

모든 루프는 iteration 경계에서 `budget.expired()` 검사(협조적). CP-SAT 타임박스는 잔여시간과 min. 시간 측정은 `time.monotonic()`.

### 6.4 진입점 (`entry.py` + `myalgorithm.py`)

```python
def algorithm(prob_info, timelimit=60):          # myalgorithm.py — 시그니처 불변
    try:
        return solver.entry.solve(prob_info, timelimit)   # 내부에서 armor 완결
    except BaseException:
        return _verified_serial_fallback(prob_info)       # 기존 M1 셸 유지 (이중 방어)
```

`solve()` 내부: 최상위 try/except → incumbent.ops 반환. incumbent가 아직 없으면(T0 검증 전 크래시) 예외를 올려 바깥 셸이 처리. **어느 경로로도 미검증 해가 반환되지 않는다.**

---

## 7. 파라미터 총괄표 (기본값 / 범위 / 보정 근거)

| 파라미터 | 기본 | 범위 | 보정 |
|---|---|---|---|
| T_CAP (시간 후보) | 8 | 4–16 | 에스컬레이션 §3.5 |
| K (앵커 상한) | 32 | 16–64 | 〃 |
| p (편향 무작위) | 0.25 | 0.1–0.4 | multi-start 다양성 |
| q_min, q_max | 2%·n, 6%·n | – | stall 시 q_max ×2 (≤15%·n) |
| ρ (D2/D5/D6 편향) | 3 / 6 / 4 | – | 문헌 기본, S3 ablation |
| δ (R1 노이즈) | 0.10 | 0.05–0.2 | 〃 |
| 세그먼트 / 점수 / r | 100 / (33,9,13) / 0.1 | – | Ropke–Pisinger 기본 |
| SA T0 | median(Δ+)/ln2 | – | 워밍업 40 iter 관측 |
| SA T_end / 리히트 | T0/1000 / 0.3·T0 | – | 시간 기반 지수 냉각 |
| stall | max(30 iter, 10%·LNS예산) | – | 3회차 perturb 재출발 |
| retime 타임박스 | min(5s, 10%·잔여) | – | bay당 |
| dirty 임계 | max(5, 5%·n_b) | – | §4.7 |
| κ (할당 소프트캡) | 0.85 | 0.7–0.95 | 부하율 통계로 재유도 |
| 판정 캐시 | 2^20 LRU | – | 적중률·메모리 계측 후 조정 |
| EPS (수용/갱신) | 1e-9 상대 | – | float 동률 방지 |
| seed | 20260709 | – | 단일 RNG(PCG64), 전 확률 경로 공유 |

전 파라미터는 `SolverConfig` dataclass 한 곳에 모으고, 벤치 러너에서 오버라이드 가능해야 한다(ablation 요건).

---

## 8. 테스트 명세 (구현 순서상 코드보다 먼저)

| 파일 | 케이스 (전부 `check_feasibility` 오라클) |
|---|---|
| test_contract_semantics | 진리표 5행(버섯형 2블록), union-서로소 임의 순서 안전, 같은 날 손바뀜 `e_i=a_j`, union-겹침 쌍 같은 날 동시 진입 불가, P=0 체류≥1, exit 연장 합법+Z1 반영, obj2 float 패리티 |
| test_geometry_kernel | union_disjoint == (모든 레이어쌍 area 0), 접촉(공유 변) 통과, 캐시 적중 동일성, 앵커 유효범위(음수 lx0 형상 포함), shape_key 중복 형상 공유 |
| test_state_parity | 무작위 해 100개: state.objective() ↔ 체커 objective (±1e-6 상대), place/remove 증분 == 재계산 |
| test_validate_parity | 무작위 삽입 후보 1,000건: placement_ok ↔ full check 판정 일치 (불일치 0 의무) |
| test_construct | 전 블록 배치 보장, 결정성(동일 시드 동일 해), 에스컬레이션 5단계 도달 케이스, bay-bump 비용 비교 방향 |
| test_retime | 소형 합성 bay에서 CP-SAT 결과 == 수작업 최적, never-worse 가드, hint 재현성, 타임박스 준수 |
| test_alns | undo 완전성(수용/거부/실패 후 state 불변식), SA 보정 경로, stall→리히트→perturb 전이, 적응 가중치 갱신 수식 |
| test_serialize | EXIT-before-ENTRY, 정수 강제, Stage 5 재생 통과(오라클), (인터락) guest-first 위상 정렬 |
| test_entry_armor | timelimit {0.5, 2, 5, 12}s 사다리, 인위적 예외 주입 시 incumbent 반환, 미검증 해 반환 경로 부재(정적 grep + 테스트) |

---

## 9. 작업 분해 (설계서 로드맵 S0–S3의 구현 태스크)

| 태스크 | 내용 | 파일 | 완료 기준 |
|---|---|---|---|
| S0-1 | 계약 테스트 작성 (전부 RED 상태로 시작) | test_contract_semantics | 오라클 기대값 확정 |
| S0-2 | instance/geometry/state/serialize/trivial/budget/entry 골격 | 해당 모듈 | test_geometry, test_state_parity, test_serialize, test_entry_armor green; T0로 전 훈련 인스턴스 5s 100% feasible |
| S0-3 | 마이크로벤치 (판정·삽입 비용 실측) | scripts 또는 벤치 러너 확장 | 설계서 §9.2 표를 실측으로 대체 |
| S1-1 | construct + assign v1 + validate | construct/assign/validate | test_construct, test_validate_parity green; n=300 구성 ≤5s; T0 대비 대폭 개선 |
| S1-2 | multi-start 드라이버 + 계측 필드 | construct | 프로파일별 결과 벤치 행 기록 |
| S2-1 | retime + 트리거 + 최종 스윕 | retime | test_retime green; 전 인스턴스 Z1 비악화, 중앙값 개선 > 0 |
| S3-1 | ALNS 루프 + D1/D2/D5 + R1/R3 + SA | alns | test_alns green; 60s에서 수용 카운트 > 0, anytime 단조 |
| S3-2 | D3/D4/D6 + R2 + 적응 가중치 + ablation | alns | 연산자별 기여 리포트; SA vs RRT vs strict A/B |
| S4+ | assign v2, 병렬 포트폴리오, interlock | assign/interlock | 설계서 게이트 그대로 |

세션 규율: 태스크 1개 = 실험 1개 = experiment-log 1엔트리(기존 플레이북 형식). 벤치는 `benchmark_instances.py`에 `--solver myalgorithm` 경로로 연결(구현 초기엔 별도 `--solver solver_dev` 라벨로 병행 가능).

---

## 10. 부록: 복잡도·메모리 추산 (S0-3에서 실측 대체)

- 삽입 1회: bay ≤ 5 × orient ≤ 4 × T 8 × 앵커 시도 평균 ≤ 5 × (numpy AABB 1회 + union 판정 0–3회) ≈ 정확 판정 수십 회 = 수 ms.
- ALNS 1 iteration: destroy O(q) + repair q·삽입 + 증분 objective O(q + m) ≈ 20–150ms (n=300, q≈10) → 60s 예산에서 수백 iteration, 30분 예산에서 수만 iteration.
- retime: 쌍 후보 O(n_b²) 캐시 조회(수 ms) + CP-SAT 1–5s.
- 메모리: shapes ≤ ~1,500 × (다각형 + prepared) ≪ 100MB; 판정 캐시 2^20 상한 ≈ 150–200MB; state·undo ≈ 수 MB. 16GB 대비 여유.
- full check: 수용 시에만, n=300에서 ~1s — incumbent 갱신 빈도(계측)가 예산의 15%를 넘으면 갱신 임계 δ_min 도입(§1.3 EPS 상향, 플래그).
