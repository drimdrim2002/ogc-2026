# OGC 2026 — 3주차: 매트휴리스틱 마무리 (CP-SAT 재타이밍 + 배치풀 선택 + Z3/Z2 폴리시 + 병렬 체인) 구현 계획

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** ALNS incumbent 위에서 예산 후반 ~20%를 쓰는 매트휴리스틱 파이프라인(CP-SAT 재타이밍 → 배치풀 선택 → Z3/Z2 폴리시)과 병렬 ALNS 체인(2~3 워커)을 완성하고, 스테이지별 기여도를 벤치 하네스로 측정한다.

**Architecture:** 재타이밍은 incumbent의 기하(bay,x,y,orient)를 고정하고 `core.pair_bits_at_current()`의 쌍별 비트(static/entry_ij/entry_ji)를 CP-SAT 시간 제약으로 인코딩해 Z1(tardiness)만 전역 재최적화한다(기하 고정 시 Z2·Z3는 상수). 배치풀은 ALNS 국소최적해에서 블록별 top-K 튜플을 모아 "블록당 정확히 1개 + 충돌쌍 배제" CP-SAT 선택 모델로 조합하며, 충돌은 기존 `place/check_place`의 쌍별 테스트로 사전계산한다. 폴리시는 entry/exit을 고정(ΔZ1≡0)한 채 bay 재배정·스왑으로 w2·Z2+w3·Z3만 내린다. 병렬화는 fork 기반 워커가 각자 Core를 만들고 `shell.improve`를 슬라이스 단위로 돌리며 Queue로 best/pool을 교환한다. 모든 스테이지는 time-box·skippable이고, 결과가 incumbent보다 나쁘면 무조건 incumbent를 유지한다.

**Tech Stack:** Python 3.12, ortools==9.15.6755 (CP-SAT), multiprocessing(fork), 1주차 `ogc_core`(pybind11 .so) + `solver/*` 계약, pytest, `baseline/utils.py`(최종 oracle).

## Global Constraints (설계서 §1·§4·§5에서 복사)

- 서버: Ubuntu 24.04, AMD Threadripper PRO 9955WX, **4코어/16GB/인터넷 없음**, firejail+cpulimit, 시간제한 수 분~30분(비공개).
- Python 3.12 고정 (공식 env). `utils.py` 수정 금지 — 최종 검증 oracle.
- **ortools==9.15.6755 고정.** 미설치 시: `conda run -n ogc2026 pip install ortools==9.15.6755` (2026-07-02 확인: 현재 env에 ortools 없음 — Task 1에서 설치).
- **Gurobi 보류**(라이선스 확인 전) — MIP 계열은 CP-SAT만. 단 `solver/mh/pool_mip.py`는 백엔드 절연 구조로 작성해 Gurobi를 나중에 drop-in 가능하게 한다.
- CP-SAT `num_search_workers=4` (4코어 상한). 병렬 ALNS 체인은 워커 2~3개 + 메인(대기)로 4코어 상한 준수.
- L4 매트휴리스틱은 **예산 후반 ~20%** (spec §4). 각 스테이지는 time-box + Budget으로 스킵 가능.
- 모든 반환/테스트 해는 `utils.check_feasibility` 통과 필수. 매트휴리스틱 결과가 incumbent보다 나쁘면(동률 포함) incumbent 유지.
- 제출 zip: 루트에 `myalgorithm.py`, ≤15MB, 상대경로만. `shell.solve(prob_info, timelimit)` 2-인자 호출 호환 유지(시그니처 확장은 기본값 있는 kwargs만).
- **same-day 규약 (1주차 v0 고정):** 같은 날 EXIT 전량이 ENTRY보다 먼저, EXIT들은 block id 오름차순, ENTRY들도 block id 오름차순.
- 시간 값은 정수 일, 좌표는 정수. EXIT = ENTRY + P 규약(디코더·ALNS·재타이밍 공통).
- 인스턴스의 `weights(w1,w2,w3)`, `release_time/due_date/processing_time/workload`는 전 40개에서 **정수**(2026-07-02 확인: 예 prob_1 w=(29091,7,200)) — CP-SAT 정수 목적함수에 그대로 사용 가능.

## 전제 — 1·2주차 인터페이스 (변경 금지, 이 계획이 소비)

1주차 계약(`docs/superpowers/plans/2026-07-02-ogc2026-week1-core-decoder.md` "인터페이스 계약" 섹션)에서 이번 주가 쓰는 것:

```python
ogc_core.Core / fallback (core_iface.make_core(prob_info)로 획득, API 동일):
.n_blocks / .n_bays / .processing(b) / .release(b) / .due(b) / .num_orients(b)
.place(block,bay,x,y,orient,entry,exit) -> bool   # feasible할 때만 반영
.remove(block) / .clear()
.get_placements() -> list[tuple[block,bay,x,y,orient,entry,exit]]
.load_placements(list) -> int                     # 성공 개수
.check_place(...) -> bool                         # 상태 불변 조회
.free_positions(block,bay,orient,entry,exit) -> list[tuple[x,y]]
.objective() -> tuple[Z1,Z2,Z3]
.pair_bits_at_current() -> list[tuple[i,j,static_hit,entry_ij,entry_ji]]
                                                  # 같은 bay 쌍의 현재 "오프셋" 비트.
                                                  # 오프셋은 기하에만 의존 → 시간을 바꿔도 불변.
                                                  # (all-zero 쌍이 포함되어 와도 무해 — 필터해서 사용)
.verify_full() -> list[str]                       # 빈 리스트 = OK

solver.decoder.construct(core, prob, rng, cfg) -> Placements | None
solver.serialize.to_operations(placements) -> dict
solver.budget.Budget(timelimit, safety=0.08): .remain(), .expired()
solver.shell.solve(prob_info, timelimit) -> dict
```

2주차 가정(내부 구현에 의존 금지, 아래 시그니처만):
- `solver.shell.improve(core, incumbent, budget) -> Placements` — ALNS 개선 루프. budget 준수, incumbent를 warm-start로 받아 같거나 더 좋은 해 반환. 슬라이스 호출(짧은 Budget으로 반복 호출) 가능 — 이것이 "placement-pool-friendly" 사용법이며, 슬라이스마다 반환되는 해를 국소최적해로 간주해 풀에 적립한다.

## 파일 구조 (이번 주 생성/수정)

```
solver/
  mh/__init__.py            # 생성: 패키지 마커 (빈 파일 + docstring)
  mh/retiming.py            # 생성: CP-SAT 재타이밍 (Task 2~3)
  mh/pool.py                # 생성: PlacementPool 수집/병합 (Task 4)
  mh/pool_mip.py            # 생성: 풀 충돌 사전계산 + CP-SAT 선택 모델, 백엔드 절연 (Task 5)
  mh/polish.py              # 생성: Z3/Z2 폴리시 (Task 6)
  parallel.py               # 생성: 병렬 ALNS 체인 + 단일 체인 폴백 (Task 7)
  shell.py                  # 수정: MhCfg + run_matheuristics 오케스트레이션 (Task 8)
  beam.py                   # 생성(OPTIONAL, 여유 시): beam-search 구성기 (Task 10)
experiments/
  run_bench.py              # 수정: 스테이지 on/off + 기여도 리포트 + ablation (Task 9)
tests/
  test_retiming.py          # 생성 (Task 2~3)
  test_pool.py              # 생성 (Task 4)
  test_pool_mip.py          # 생성 (Task 5)
  test_polish.py            # 생성 (Task 6)
  test_parallel.py          # 생성 (Task 7)
  test_shell_phases.py      # 생성 (Task 8)
  test_beam.py              # 생성(OPTIONAL) (Task 10)
```

## 3주차 인터페이스 계약 (4주차가 의존)

```python
# solver/mh/retiming.py
retime(core, prob, placements, budget_s, workers=4) -> tuple[Placements, dict]
    # 항상 (해, stats) 반환. 개선 실패·시간초과·검증실패 시 입력 placements 그대로 반환.
    # stats 키: stage, status, improved, obj1_before, obj1_after, n_pairs, wall_s
add_pair_time_constraints(model, e, p, i, j, static_hit, entry_ij, entry_ji) -> None
    # e: dict[block -> IntVar(entry)], p: dict[block -> int processing]

# solver/mh/pool.py
class PlacementPool:
    def __init__(self, n_blocks: int, k: int = 6)
    def add(self, prob, blk, bay, x, y, orient, entry) -> bool
    def add_solution(self, placements, prob) -> int      # 새로 들어간 튜플 수
    def entries(self, blk) -> list[tuple[bay,x,y,orient,entry]]  # 품질 오름차순(좋은 것부터)
    def dump(self) -> list[tuple[blk,bay,x,y,orient,entry]]      # picklable (mp.Queue용)
    def merge(self, dumped, prob) -> int
    def __len__(self) -> int

# solver/mh/pool_mip.py
select_from_pool(core, prob, pool, incumbent, budget_s, workers=4, backend="cpsat")
    -> tuple[Placements, dict]
    # incumbent 튜플을 풀에 자동 주입해 모델이 항상 feasible. 나쁘면 incumbent 반환.
    # stats 키: stage, status, improved, n_tuples, n_conflicts, obj_before, obj_after, wall_s

# solver/mh/polish.py
polish(core, prob, placements, budget_s) -> tuple[Placements, dict]
    # ΔZ1==0 보장(entry/exit 불변 이동만). stats 키: stage, moves, swaps, improved,
    # obj_sec_before, obj_sec_after, wall_s   (obj_sec = w2*Z2 + w3*Z3)

# solver/parallel.py
run_single_chain(core, prob, incumbent, budget_s, slice_s=6.0, k_pool=6)
    -> tuple[Placements, PlacementPool]        # shell의 기본 ALNS 경로 (풀 수집 겸용)
run_chains(prob_info, incumbent, budget_s, n_workers=2, base_seed=1,
           slice_s=6.0, k_pool=6) -> tuple[Placements, PlacementPool]
    # 예외/이득없음 대비: 호출측(shell)이 실패 시 run_single_chain으로 폴백

# solver/shell.py (확장 — 기존 2-인자 호출 호환)
@dataclass MhCfg: enable_retiming, enable_pool, enable_polish, enable_parallel,
                  n_chains, mh_frac, retiming_frac, pool_frac, polish_frac,
                  slice_s, k_pool, min_stage_s ; MhCfg.from_env()
solve(prob_info, timelimit, cfg=None, stats_out=None) -> dict
run_matheuristics(core, prob, incumbent, mh_budget_s, cfg, stats) -> Placements
# stats_out에 채워지는 키: "alns": {...}, "mh_stages": [stage stats ...]
```

`Placements = list[tuple[block,bay,x,y,orient,entry,exit]]` (1주차 `get_placements()` 형식).

---

### Task 1: ortools 설치 고정 + solver/mh 패키지 스캐폴딩

**Files:**
- Create: `solver/mh/__init__.py`
- Test: `tests/test_retiming.py` (import smoke 부분)

**Interfaces:**
- Consumes: 없음 (환경 준비).
- Produces: `import solver.mh` 가능, `from ortools.sat.python import cp_model` 가능, ortools 버전 == 9.15.6755.

- [ ] **Step 1: ortools 설치 및 버전 고정 확인**

```bash
conda run -n ogc2026 pip install ortools==9.15.6755
conda run -n ogc2026 python -c "import ortools; print(ortools.__version__)"
```

Expected: `9.15.6755`. (프록시/오프라인 환경이면 사전 다운로드한 wheel로 `pip install ortools-9.15.6755-*.whl`.)

- [ ] **Step 2: 실패 테스트 작성** — `tests/test_retiming.py` 신규 생성:

```python
"""CP-SAT 재타이밍 테스트. conftest(1주차)가 solver/·baseline/ 경로를 주입한다."""
import copy
import json
import random

import pytest


def test_ortools_pinned():
    import ortools
    assert ortools.__version__ == "9.15.6755"


def test_mh_package_imports():
    import solver.mh  # noqa: F401
    from ortools.sat.python import cp_model
    m = cp_model.CpModel()
    x = m.NewIntVar(0, 3, "x")
    m.Add(x >= 2)
    s = cp_model.CpSolver()
    assert s.Solve(m) == cp_model.OPTIMAL
```

- [ ] **Step 3: 실패 확인**

Run: `conda run -n ogc2026 python -m pytest tests/test_retiming.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'solver.mh'` (ortools 테스트는 Step 1 후 PASS).

- [ ] **Step 4: 구현** — `solver/mh/__init__.py`:

```python
"""매트휴리스틱 마무리 레이어 (spec §4 L4).

- retiming: incumbent 기하 고정 + CP-SAT 시간 재최적화 (Z1)
- pool / pool_mip: 배치풀 수집 + CP-SAT 조합 선택 (Z1+Z3+Z2 근사)
- polish: ΔZ1==0 조건의 bay 재배정·스왑 (Z2/Z3)
"""
```

- [ ] **Step 5: 통과 확인**

Run: `conda run -n ogc2026 python -m pytest tests/test_retiming.py -v`
Expected: 2 passed.

- [ ] **Step 6: Commit**

```bash
git add solver/mh/__init__.py tests/test_retiming.py
git commit -m "build(mh): ortools 9.15.6755 고정 + solver/mh 패키지 스캐폴딩"
```

### Task 2: 쌍별 시간관계의 CP-SAT 인코딩 (재타이밍의 심장)

**Files:**
- Create: `solver/mh/retiming.py` (인코딩 함수 부분)
- Test: `tests/test_retiming.py` (추가)

**Interfaces:**
- Consumes: 없음 (순수 CP-SAT 모델 조각 — `cp_model.CpModel`, entry IntVar dict, processing dict).
- Produces: `add_pair_time_constraints(model, e, p, i, j, static_hit, entry_ij, entry_ji) -> None`. Task 3의 `retime()`이 유일 소비자.

**인코딩 유도 (1주차 Task-5 판정 표에서 — 이 표가 정답 명세):**

1주차 표: 쌍 (A,B), 구간 `[e, x)` 반개구간, `x = e + P`, same-day 규약(EXIT 전량 먼저, EXIT/ENTRY 각각 id 오름차순).

| 1주차 Task-5 조건 (A 관점) | 필요 비트맵 | CP-SAT 금지 조건 (비트가 1일 때) |
|---|---|---|
| 구간 겹침 (`eA < xB and eB < xA`) | `static(A,B)` | 겹침 자체 금지 → `xA ≤ eB ∨ xB ≤ eA` |
| A ENTRY 시 B 존재: `eB < eA < xB`, 또는 같은 날 진입 `eA == eB`이고 `idA > idB` | `entry(A,B)` | `¬(eB < eA < xB) ∧ ¬(eA == eB ∧ idA > idB)` |
| A EXIT 시 B 존재: `eB < xA < xB`, 또는 같은 날 퇴출 `xA == xB`이고 `idA < idB` | `entry(A,B)` | `¬(eB < xA < xB) ∧ ¬(xA == xB ∧ idA < idB)` |
| 대칭: B의 이벤트가 A 존재 중 | `entry(B,A)` | 위 두 줄에서 A↔B 교환 |

즉 `entry_ij` 비트 히트 = "블록 i는 j의 체류 구간 *strictly inside*에 ENTRY/EXIT 이벤트를 두면 안 된다 — 단, 경계(같은 날)는 same-day 규약이 좌우한다": 진입 동시(`e_i == e_j`)는 `id_i > id_j`일 때만 금지(작은 id가 먼저 들어가 이미 존재), 퇴출 동시(`x_i == x_j`)는 `id_i < id_j`일 때만 금지(작은 id가 먼저 나가는 순간 큰 id가 아직 존재), `x_i == e_j`·`e_i == x_j`는 EXIT-먼저 규칙으로 항상 허용.

각 금지 조건의 여집합을 선형 부등식 2개의 논리합으로 정리(`x = e + P` 대입):

- **ENTRY 측** (i의 ENTRY가 j 체류 밖): `e_i ≤ e_j − δe ∨ e_i ≥ e_j + P_j`, 여기서 `δe = 1 if id_i > id_j else 0`.
- **EXIT 측** (i의 EXIT가 j 체류 밖): `e_i + P_i ≤ e_j ∨ e_i + P_i ≥ e_j + P_j + δx`, 여기서 `δx = 1 if id_i < id_j else 0`.
- **static** (겹침 금지): `e_i + P_i ≤ e_j ∨ e_j + P_j ≤ e_i`.

**흡수(subsumption) 정리:** 위 4개 이벤트-금지 조건은 전부 "구간 strict 겹침"을 함의한다(같은 날 동시 진입/퇴출 포함 — 두 구간이 그 날을 공유). 따라서 `static_hit=1`이면 no-overlap 제약 하나로 이벤트 제약이 전부 자동 충족 → static 쌍에는 disjunction 1개만 추가한다(스펙 §3의 `static ⊆ entry`와 정합). `static_hit=0`인데 entry 비트만 있는 쌍(LIFO 중첩/교차 허용 쌍)은 이벤트 제약만 추가한다.

각 논리합은 Bool 리터럴 1개 + `OnlyEnforceIf` 양방향으로 인코딩한다.

- [ ] **Step 1: 진리표 대조 실패 테스트 작성** — `tests/test_retiming.py`에 추가. 기준 술어(1주차 표를 그대로 옮긴 것)와 CP-SAT 전해 열거를 전수 비교:

```python
from ortools.sat.python import cp_model


def _pair_time_ok(ei, pi, ej, pj, id_i, id_j, static_hit, entry_ij, entry_ji):
    """1주차 Task-5 판정 표의 기준 술어 (테스트 전용 참조 구현)."""
    xi, xj = ei + pi, ej + pj
    if static_hit and (ei < xj and ej < xi):
        return False
    if entry_ij:
        if (ej < ei < xj) or (ei == ej and id_i > id_j):
            return False  # i ENTRY 시 j 존재
        if (ej < xi < xj) or (xi == xj and id_i < id_j):
            return False  # i EXIT 시 j 존재
    if entry_ji:
        if (ei < ej < xi) or (ej == ei and id_j > id_i):
            return False  # j ENTRY 시 i 존재
        if (ei < xj < xi) or (xj == xi and id_j < id_i):
            return False  # j EXIT 시 i 존재
    return True


class _Collect(cp_model.CpModelSolutionCallback):
    def __init__(self, vi, vj):
        super().__init__()
        self.vi, self.vj = vi, vj
        self.sols = set()

    def on_solution_callback(self):
        self.sols.add((self.Value(self.vi), self.Value(self.vj)))


@pytest.mark.parametrize("bits", [
    (1, 1, 1), (1, 0, 0), (0, 1, 0), (0, 0, 1), (0, 1, 1), (0, 0, 0),
])
@pytest.mark.parametrize("ids", [(1, 2), (2, 1)])   # id 순서 양방향 (same-day 규약 방향성)
def test_pair_encoding_matches_task5_table(bits, ids):
    from solver.mh.retiming import add_pair_time_constraints
    static_hit, entry_ij, entry_ji = bits
    id_i, id_j = ids
    pi, pj = 2, 3
    HI = 7
    m = cp_model.CpModel()
    e = {id_i: m.NewIntVar(0, HI, "ei"), id_j: m.NewIntVar(0, HI, "ej")}
    p = {id_i: pi, id_j: pj}
    add_pair_time_constraints(m, e, p, id_i, id_j,
                              bool(static_hit), bool(entry_ij), bool(entry_ji))
    solver = cp_model.CpSolver()
    solver.parameters.enumerate_all_solutions = True
    cb = _Collect(e[id_i], e[id_j])
    solver.SearchForAllSolutions(m, cb)
    expected = {(a, b) for a in range(HI + 1) for b in range(HI + 1)
                if _pair_time_ok(a, pi, b, pj, id_i, id_j,
                                 static_hit, entry_ij, entry_ji)}
    assert cb.sols == expected, (
        f"bits={bits} ids={ids}: model\\expected={sorted(cb.sols - expected)} "
        f"expected\\model={sorted(expected - cb.sols)}")
```

- [ ] **Step 2: 실패 확인**

Run: `conda run -n ogc2026 python -m pytest tests/test_retiming.py -k encoding -v`
Expected: FAIL — `ImportError: cannot import name 'add_pair_time_constraints'`.

- [ ] **Step 3: 구현** — `solver/mh/retiming.py` 신규 생성 (인코딩 부분):

```python
"""CP-SAT 재타이밍: incumbent의 기하(bay,x,y,orient)를 고정하고 entry만 전역 재최적화.

Z2(밸런스)·Z3(선호)는 bay 배정에만 의존하므로 기하 고정 하에서 상수 → Z1(tardiness)
최소화가 전체 목적함수 최소화와 동치. 공간 제약은 core.pair_bits_at_current()의 쌍별
비트로 완전 기술된다: 비트는 두 블록의 "상대 오프셋(기하)"만의 함수라 시간을 바꿔도 불변.

인코딩 원천: 1주차 plan Task-5 판정 표 (same-day id-오름차순 규약 포함).
"""
import time

from ortools.sat.python import cp_model


def _add_event_side(m, e, p, a, b):
    """블록 a의 ENTRY/EXIT 이벤트가 블록 b의 체류 [e_b, e_b+P_b) 중에 일어나지 않게 한다.

    same-day 규약: 같은 날 ENTRY는 id 오름차순(작은 id 먼저 진입), 같은 날 EXIT도
    id 오름차순(작은 id 먼저 퇴출), 같은 날은 EXIT 전량이 ENTRY보다 먼저.
      - a ENTRY 금지역: e_b < e_a < e_b+P_b, 또는 e_a == e_b 이고 id_a > id_b
      - a EXIT  금지역: e_b < e_a+P_a < e_b+P_b, 또는 e_a+P_a == e_b+P_b 이고 id_a < id_b
    """
    de = 1 if a > b else 0
    v1 = m.NewBoolVar(f"aen_{a}_{b}")
    m.Add(e[a] <= e[b] - de).OnlyEnforceIf(v1)            # a가 먼저(또는 같은날 선순위) 진입
    m.Add(e[a] >= e[b] + p[b]).OnlyEnforceIf(v1.Not())    # a 진입이 b 퇴출일 이후(EXIT 먼저 규칙)
    dx = 1 if a < b else 0
    v2 = m.NewBoolVar(f"aex_{a}_{b}")
    m.Add(e[a] + p[a] <= e[b]).OnlyEnforceIf(v2)                     # a 퇴출이 b 진입일 이전(같은날 OK)
    m.Add(e[a] + p[a] >= e[b] + p[b] + dx).OnlyEnforceIf(v2.Not())   # a 퇴출이 b 퇴출 이후(또는 같은날 후순위)


def add_pair_time_constraints(m, e, p, i, j, static_hit, entry_ij, entry_ji):
    """1주차 Task-5 판정 표의 CP-SAT 인코딩.

    static_hit이면 시간 겹침 자체를 금지한다. 이벤트-금지 조건(ENTRY/EXIT가 상대 체류
    strictly inside, 같은 날 규약 포함)은 전부 구간 겹침을 함의하므로 no-overlap이
    이벤트 제약을 흡수한다(스펙 §3 static ⊆ entry와 정합). static이 아니면 방향별
    이벤트 제약만 추가한다(LIFO 중첩·교차 허용).
    """
    if static_hit:
        b = m.NewBoolVar(f"no_{i}_{j}")
        m.Add(e[i] + p[i] <= e[j]).OnlyEnforceIf(b)          # i가 완전히 먼저
        m.Add(e[j] + p[j] <= e[i]).OnlyEnforceIf(b.Not())    # j가 완전히 먼저
        return
    if entry_ij:
        _add_event_side(m, e, p, i, j)
    if entry_ji:
        _add_event_side(m, e, p, j, i)
```

- [ ] **Step 4: 통과 확인**

Run: `conda run -n ogc2026 python -m pytest tests/test_retiming.py -k encoding -v`
Expected: 12 passed (bits 6종 × id 순서 2종). 실패 시 어긋난 (e_i, e_j) 셀이 메시지에 출력됨 — δe/δx 부호부터 재확인.

- [ ] **Step 5: Commit**

```bash
git add solver/mh/retiming.py tests/test_retiming.py
git commit -m "feat(mh): Task-5 시간관계 표의 CP-SAT 인코딩 + 진리표 전수 대조"
```

### Task 3: retime() — 모델 조립·풀이·추출·검증

**Files:**
- Modify: `solver/mh/retiming.py` (Task 2 파일에 `retime()` 추가)
- Test: `tests/test_retiming.py` (추가)

**Interfaces:**
- Consumes: `core.load_placements/clear/objective/pair_bits_at_current/verify_full`, `prob["blocks"][i]["release_time"|"due_date"|"processing_time"]`, Task 2의 `add_pair_time_constraints`.
- Produces: `retime(core, prob, placements, budget_s, workers=4) -> tuple[Placements, dict]` — Task 8 오케스트레이션이 소비. 반환 시 core에는 반환된 해가 로드된 상태.

- [ ] **Step 1: 실패 테스트 작성** — `tests/test_retiming.py`에 추가:

```python
def _sub_instance(prob, idxs, due_override=None, release_override=None):
    """블록 부분집합으로 미니 인스턴스 생성 (id는 0..len-1로 재부여)."""
    sub = {k: v for k, v in prob.items() if k not in ("blocks",)}
    sub["blocks"] = [copy.deepcopy(prob["blocks"][i]) for i in idxs]
    for t, b in enumerate(sub["blocks"]):
        if due_override is not None:
            b["due_date"] = due_override[t]
        if release_override is not None:
            b["release_time"] = release_override[t]
    return sub


def _utils_feasible(prob, placements):
    from solver.serialize import to_operations
    from utils import check_feasibility
    return check_feasibility(prob, to_operations(placements))


def test_retime_pulls_single_block_to_release(prob1):
    """블록 1개, entry를 불필요하게 5일 늦춘 incumbent → 재타이밍이 release로 당긴다."""
    import ogc_core
    from solver.mh.retiming import retime
    p0 = prob1["blocks"][0]["processing_time"]
    r0 = prob1["blocks"][0]["release_time"]
    sub = _sub_instance(prob1, [0], due_override=[r0 + p0])   # due 빠듯 → 당길수록 이득
    core = ogc_core.Core(json.dumps(sub))
    inc = [(0, 1, 5, 1, 0, r0 + 5, r0 + 5 + p0)]              # bay1 (prob_1 블록0 선호 bay)
    assert core.load_placements(inc) == 1
    out, st = retime(core, sub, inc, budget_s=5.0)
    assert st["status"] in ("OPTIMAL", "FEASIBLE")
    assert st["improved"] is True and st["obj1_after"] == 0
    assert out[0][5] == r0 and out[0][6] == r0 + p0           # entry == release
    assert out[0][1:5] == inc[0][1:5]                          # 기하 불변
    res = _utils_feasible(sub, out)
    assert res["feasible"] is True and res["obj1"] == 0


def _find_static_pair(prob):
    """(0,0)에 포개 놓으면 static 충돌이 나는 블록쌍 탐색.

    시간 겹침 배치가 거부되고(공간 충돌) 시간 분리 배치는 허용되는(경계 OK 증명) 쌍을
    앞에서부터 찾는다. prob_1의 블록 bbox 평균 8x7이라 극초반에 발견된다.
    """
    import ogc_core
    for i in range(6):
        for j in range(6):
            if i == j:
                continue
            sub = _sub_instance(prob, [i, j])
            core = ogc_core.Core(json.dumps(sub))
            p0, p1 = core.processing(0), core.processing(1)
            if core.release(0) > 0 or core.release(1) > 0:
                continue
            if not core.place(0, 0, 0, 0, 0, 0, p0):
                continue
            overlap_rejected = not core.check_place(1, 0, 0, 0, 0, 0, p1)
            disjoint_ok = core.check_place(1, 0, 0, 0, 0, p0, p0 + p1)
            if overlap_rejected and disjoint_ok:
                return i, j
    pytest.fail("static 충돌쌍을 찾지 못함 — 코어 판정 회귀 의심")


def test_retime_reorders_static_pair(prob1):
    """같은 자리(static 쌍)를 나쁜 순서로 쓰는 incumbent → 재타이밍이 순서를 뒤집어 Z1=0."""
    import ogc_core
    from solver.mh.retiming import retime
    i, j = _find_static_pair(prob1)
    pi = prob1["blocks"][i]["processing_time"]
    pj = prob1["blocks"][j]["processing_time"]
    # 블록0(=i): due 넉넉(pi+pj), 블록1(=j): due 빠듯(pj — entry 0이어야만 제시간)
    sub = _sub_instance(prob1, [i, j], due_override=[pi + pj, pj],
                        release_override=[0, 0])
    core = ogc_core.Core(json.dumps(sub))
    inc = [(0, 0, 0, 0, 0, 0, pi),               # 느긋한 블록이 자리를 선점
           (1, 0, 0, 0, 0, pi, pi + pj)]         # 급한 블록이 기다림 → 지각 pi
    assert core.load_placements(inc) == 2
    out, st = retime(core, sub, inc, budget_s=5.0)
    assert st["obj1_before"] == pi and st["obj1_after"] == 0 and st["improved"]
    res = _utils_feasible(sub, out)
    assert res["feasible"] is True and res["obj1"] == 0


def test_retime_full_instance_never_worse_and_feasible(prob1):
    """실 인스턴스: construct incumbent → retime → utils feasible + Z1 비악화 + Z2/Z3 불변."""
    import ogc_core
    from solver.decoder import construct
    from solver.mh.retiming import retime
    core = ogc_core.Core(json.dumps(prob1))
    inc = construct(core, prob1, rng=random.Random(0))
    assert inc is not None
    core.clear()
    assert core.load_placements(inc) == len(inc)
    z1b, z2b, z3b = core.objective()
    out, st = retime(core, prob1, inc, budget_s=10.0)
    core.clear()
    assert core.load_placements(out) == len(out)
    z1a, z2a, z3a = core.objective()
    assert z1a <= z1b and (z2a, z3a) == (z2b, z3b)   # 기하 고정 → Z2/Z3 불변
    res = _utils_feasible(prob1, out)
    assert res["feasible"] is True
    assert res["obj1"] == pytest.approx(z1a)


def test_retime_zero_budget_returns_incumbent(prob1):
    import ogc_core
    from solver.decoder import construct
    from solver.mh.retiming import retime
    core = ogc_core.Core(json.dumps(prob1))
    inc = construct(core, prob1, rng=random.Random(1))
    out, st = retime(core, prob1, inc, budget_s=0.05)
    assert out == inc and st["status"] == "SKIPPED"
```

- [ ] **Step 2: 실패 확인**

Run: `conda run -n ogc2026 python -m pytest tests/test_retiming.py -k retime -v`
Expected: FAIL — `ImportError: cannot import name 'retime'`.

- [ ] **Step 3: 구현** — `solver/mh/retiming.py`에 추가:

```python
def retime(core, prob, placements, budget_s, workers=4):
    """incumbent 기하 고정 재타이밍. 항상 (해, stats)를 반환하고 절대 악화시키지 않는다.

    - 블록당 IntVar entry ∈ [release, horizon-P], exit = entry+P (interval 길이 고정).
    - 쌍 제약: core.pair_bits_at_current() (기하 전용이라 시간 변경에 불변).
    - 목적: min Σ tardiness. w1 곱은 상수배라 생략(리포트에서만 곱함).
    - CP-SAT: num_search_workers=workers(기본 4), max_time_in_seconds=budget.
    - 종료 후 코어 재로드 + verify_full()로 자체 검증. 실패·비개선이면 incumbent 원복.
    """
    t0 = time.monotonic()
    stats = {"stage": "retiming", "status": "SKIPPED", "improved": False,
             "obj1_before": None, "obj1_after": None, "n_pairs": 0, "wall_s": 0.0}
    if budget_s < 0.2 or not placements:
        return placements, stats

    core.clear()
    assert core.load_placements(placements) == len(placements), "incumbent 재로드 실패"
    z1_before, _, _ = core.objective()
    stats["obj1_before"] = z1_before

    blocks = prob["blocks"]
    m = cp_model.CpModel()
    max_exit = max(pl[6] for pl in placements)
    max_p = max(int(b["processing_time"]) for b in blocks)
    horizon = int(max_exit) + max_p + 1        # incumbent 힌트가 항상 도메인 안 + 재배열 여유

    e, p = {}, {}
    for (blk, bay, x, y, o, ent, ext) in placements:
        r = int(blocks[blk]["release_time"])
        pi = int(blocks[blk]["processing_time"])
        assert ext == ent + pi, f"EXIT=ENTRY+P 규약 위반 (block {blk})"
        p[blk] = pi
        e[blk] = m.NewIntVar(r, horizon - pi, f"e{blk}")
        m.AddHint(e[blk], int(ent))            # incumbent warm start → 비악화 탐색

    tard_terms = []
    for blk, ev in e.items():
        d = int(blocks[blk]["due_date"])
        t = m.NewIntVar(0, horizon, f"t{blk}")
        m.Add(t >= ev + p[blk] - d)
        tard_terms.append(t)

    for (i, j, static_hit, entry_ij, entry_ji) in core.pair_bits_at_current():
        if static_hit or entry_ij or entry_ji:
            add_pair_time_constraints(m, e, p, i, j,
                                      bool(static_hit), bool(entry_ij), bool(entry_ji))
            stats["n_pairs"] += 1
    m.Minimize(sum(tard_terms))

    solver = cp_model.CpSolver()
    solver.parameters.max_time_in_seconds = max(0.1, budget_s - (time.monotonic() - t0))
    solver.parameters.num_search_workers = workers
    status = solver.Solve(m)
    stats["status"] = solver.StatusName(status)
    if status not in (cp_model.OPTIMAL, cp_model.FEASIBLE):
        stats["wall_s"] = time.monotonic() - t0
        return placements, stats

    new_pl = [(blk, bay, x, y, o,
               int(solver.Value(e[blk])), int(solver.Value(e[blk])) + p[blk])
              for (blk, bay, x, y, o, _e, _x) in placements]

    core.clear()
    ok = core.load_placements(new_pl) == len(new_pl) and not core.verify_full()
    if not ok:                                  # 코어 자체검증 실패 → 원복 (인코딩 회귀 방어선)
        core.clear()
        core.load_placements(placements)
        stats["status"] += "+VERIFY_FAIL"
        stats["wall_s"] = time.monotonic() - t0
        return placements, stats
    z1_after, _, _ = core.objective()
    stats["obj1_after"] = z1_after
    stats["wall_s"] = time.monotonic() - t0
    if z1_after >= z1_before:                   # 동률 포함 비개선 → incumbent 유지
        core.clear()
        core.load_placements(placements)
        return placements, stats
    stats["improved"] = True
    return new_pl, stats
```

- [ ] **Step 4: 통과 확인**

Run: `conda run -n ogc2026 python -m pytest tests/test_retiming.py -v`
Expected: 전부 PASS (인코딩 12 + retime 4). full-instance 테스트가 `VERIFY_FAIL`을 내면 진행 금지 — Task 2 진리표는 통과하는데 실전이 깨지는 경우는 `pair_bits_at_current`의 쌍 방향(i,j) 해석 불일치가 유력하므로 1주차 Task 7 구현과 (i,j, entry_ij) 방향 정의를 대조할 것.

- [ ] **Step 5: 40개 인스턴스 스모크 (수동 1회)**

```bash
conda run -n ogc2026 python - <<'EOF'
import json, random, sys
sys.path[:0] = ["solver", "baseline"]
import ogc_core
from solver.decoder import construct
from solver.mh.retiming import retime
from solver.serialize import to_operations
from utils import check_feasibility
import pathlib
root = pathlib.Path("data")
files = sorted((root / "training_instances/train").glob("prob_*.json")) + \
        sorted((root / "train-set2/train").glob("prob_*.json"))
for f in files:
    prob = json.loads(f.read_text())
    core = ogc_core.Core(json.dumps(prob))
    inc = construct(core, prob, rng=random.Random(0))
    if inc is None:
        print(f.stem, "construct FAIL"); continue
    out, st = retime(core, prob, inc, budget_s=5.0)
    res = check_feasibility(prob, to_operations(out))
    print(f.stem, st["status"], "Z1", st["obj1_before"], "->", st["obj1_after"],
          "feasible", res["feasible"])
    assert res["feasible"]
EOF
```

Expected: 전 인스턴스 `feasible True`, Z1 비악화(다수 인스턴스에서 감소). infeasible 1건이라도 나오면 커밋 금지, 해당 인스턴스를 고정 회귀 테스트로 추가.

- [ ] **Step 6: Commit**

```bash
git add solver/mh/retiming.py tests/test_retiming.py
git commit -m "feat(mh): CP-SAT 재타이밍 retime() — 기하 고정 Z1 전역 재최적화"
```

### Task 4: PlacementPool — 블록별 top-K 튜플 수집

**Files:**
- Create: `solver/mh/pool.py`
- Test: `tests/test_pool.py`

**Interfaces:**
- Consumes: `prob["blocks"][i]["processing_time"|"due_date"|"bay_preferences"]`, `prob["weights"]`, Placements 튜플 형식.
- Produces: `PlacementPool(n_blocks, k=6)` — `.add(prob, blk, bay, x, y, orient, entry) -> bool`, `.add_solution(placements, prob) -> int`, `.entries(blk) -> list[(bay,x,y,orient,entry)]`(품질 좋은 순), `.dump() -> list[(blk,bay,x,y,orient,entry)]`(picklable), `.merge(dumped, prob) -> int`, `len(pool)`. Task 5(선택 모델)·Task 7(체인 교환)·Task 8(셸)이 소비.

- [ ] **Step 1: 실패 테스트 작성** — `tests/test_pool.py` 신규:

```python
"""배치풀 단위 테스트 — 중복 제거, top-K 축출, 품질 순서, dump/merge 왕복."""
import pickle

from solver.mh.pool import PlacementPool


def _mini_prob():
    return {
        "weights": {"w1": 1000, "w2": 7, "w3": 200},
        "blocks": [
            {"processing_time": 5, "due_date": 10, "release_time": 0,
             "workload": 40, "bay_preferences": [0, 100]},
            {"processing_time": 3, "due_date": 4, "release_time": 0,
             "workload": 20, "bay_preferences": [100, 50]},
        ],
    }


def test_add_dedups_and_counts():
    prob = _mini_prob()
    pool = PlacementPool(2, k=3)
    assert pool.add(prob, 0, 1, 5, 5, 0, 0) is True
    assert pool.add(prob, 0, 1, 5, 5, 0, 0) is False          # 완전 중복
    assert len(pool) == 1


def test_topk_evicts_worst_only():
    prob = _mini_prob()
    pool = PlacementPool(2, k=2)
    # 블록0: entry가 늦을수록 tardiness 커져 품질 나쁨 (exit = entry+5, due 10)
    assert pool.add(prob, 0, 1, 0, 0, 0, 9)    # tard 4
    assert pool.add(prob, 0, 1, 1, 0, 0, 7)    # tard 2
    assert pool.add(prob, 0, 1, 2, 0, 0, 0)    # tard 0 → tard4 축출
    assert pool.add(prob, 0, 1, 3, 0, 0, 12) is False   # tard 7 — 기존보다 나빠 미수용
    got = pool.entries(0)
    assert [g[4] for g in got] == [0, 7]        # 품질 좋은 순(entry 0 먼저)
    assert len(pool) == 2


def test_quality_prefers_low_tard_then_pref():
    prob = _mini_prob()
    pool = PlacementPool(2, k=2)
    pool.add(prob, 0, 0, 0, 0, 0, 0)   # tard 0, pref손실 100 → cost w3*100
    pool.add(prob, 0, 1, 0, 0, 0, 0)   # tard 0, pref손실 0   → cost 0 (선호 bay)
    assert pool.entries(0)[0][0] == 1  # 선호 bay 튜플이 1순위


def test_add_solution_and_dump_merge_roundtrip():
    prob = _mini_prob()
    pool = PlacementPool(2, k=3)
    pl = [(0, 1, 5, 5, 0, 0, 5), (1, 0, 2, 2, 1, 0, 3)]
    assert pool.add_solution(pl, prob) == 2
    dumped = pool.dump()
    assert pickle.loads(pickle.dumps(dumped)) == dumped       # mp.Queue 안전
    other = PlacementPool(2, k=3)
    assert other.merge(dumped, prob) == 2
    assert other.entries(0) == pool.entries(0)
    assert other.merge(dumped, prob) == 0                     # 재병합은 전부 중복
```

- [ ] **Step 2: 실패 확인**

Run: `conda run -n ogc2026 python -m pytest tests/test_pool.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'solver.mh.pool'`.

- [ ] **Step 3: 구현** — `solver/mh/pool.py`:

```python
"""배치풀: ALNS 국소최적해들에서 블록별 상위 K개 (bay,x,y,orient,entry) 튜플을 수집.

품질(quality filter) = 튜플 고정 비용 w1*tardiness + w3*pref_loss (둘 다 튜플만으로
계산되는 상수). 완전 일치 튜플은 중복 제거. K개 초과 시 최악 튜플 축출하되 신규가
기존 최악보다 나쁘면 미수용. dump()/merge()는 multiprocessing.Queue 교환용 직렬화.
"""


class PlacementPool:
    def __init__(self, n_blocks, k=6):
        self.n_blocks = n_blocks
        self.k = k
        self._by_block = [dict() for _ in range(n_blocks)]   # key=(bay,x,y,orient,entry) -> cost

    @staticmethod
    def _cost(prob, blk, bay, entry):
        b = prob["blocks"][blk]
        w = prob["weights"]
        tard = max(0, entry + b["processing_time"] - b["due_date"])
        pref = max(b["bay_preferences"]) - b["bay_preferences"][bay]
        return w["w1"] * tard + w["w3"] * pref

    def add(self, prob, blk, bay, x, y, orient, entry):
        key = (bay, x, y, orient, entry)
        d = self._by_block[blk]
        if key in d:
            return False
        c = self._cost(prob, blk, bay, entry)
        if len(d) >= self.k:
            worst = max(d, key=d.get)
            if d[worst] <= c:
                return False
            del d[worst]
        d[key] = c
        return True

    def add_solution(self, placements, prob):
        n = 0
        for (blk, bay, x, y, o, e, _x) in placements:
            n += bool(self.add(prob, blk, bay, x, y, o, e))
        return n

    def entries(self, blk):
        d = self._by_block[blk]
        return sorted(d, key=d.get)

    def dump(self):
        return [(b, *key) for b in range(self.n_blocks) for key in self._by_block[b]]

    def merge(self, dumped, prob):
        n = 0
        for (blk, bay, x, y, o, e) in dumped:
            n += bool(self.add(prob, blk, bay, x, y, o, e))
        return n

    def __len__(self):
        return sum(len(d) for d in self._by_block)
```

- [ ] **Step 4: 통과 확인**

Run: `conda run -n ogc2026 python -m pytest tests/test_pool.py -v`
Expected: 4 passed.

- [ ] **Step 5: Commit**

```bash
git add solver/mh/pool.py tests/test_pool.py
git commit -m "feat(mh): PlacementPool — top-K 수집·중복제거·품질필터·dump/merge"
```

### Task 5: 배치풀 선택 모델 (충돌 사전계산 + CP-SAT, 백엔드 절연)

**Files:**
- Create: `solver/mh/pool_mip.py`
- Test: `tests/test_pool_mip.py`

**Interfaces:**
- Consumes: Task 4 `PlacementPool.entries/add_solution`, `core.clear/place/check_place/load_placements/objective/verify_full`, `prob["weights"|"blocks"|"bays"]`.
- Produces: `select_from_pool(core, prob, pool, incumbent, budget_s, workers=4, backend="cpsat") -> tuple[Placements, dict]` — Task 8이 소비. 내부 `_pairwise_conflicts(core, cand) -> list[(t1,t2)]`, `_solve_cpsat(...)` (Gurobi drop-in 지점).

**모델**: 후보 튜플 t = (blk,bay,x,y,o,entry,exit)에 Bool `s[t]`. 블록당 `ExactlyOne`, 충돌쌍 `¬s[t1] ∨ ¬s[t2]`. 목적(전부 int, SCALE=10^6 배율):
- 튜플 상수비용: `(w1*tard_t + w3*pref_t) * SCALE` — 인스턴스 가중치·시간이 전부 정수라 정확.
- **Z2 선형화(근사, 문서화)**: `L_j = Σ workload*s`, `u_j = avg_area/area_j`를 `U_j = round(u_j*SCALE)` 정수로 스케일하고 `D ≥ ±(U_a*L_a − U_b*L_b)` (bay 쌍 전부) → `w2*D` 가산. 근사는 U 반올림(상대오차 ≤ 1e-6) 하나뿐이다 (utils의 obj2는 floor 없는 float 계산이므로 "floor 생략"이라는 근사 자체가 존재하지 않는다). **최종 채점은 항상 core.objective()/utils로 재계산**하므로 모델 오차는 해 선택 품질에만 영향, 정답성에는 무영향.

충돌 판정은 계약의 기존 API만 사용: 빈 코어에 t1을 `place`(풀 튜플은 feasible 해 출신이라 단독 배치는 항상 성공 — 단항 제약뿐)하고 t2를 `check_place` — 1주차 Task-5의 `required()`가 쌍 양방향(entry_ij·entry_ji)을 모두 검사하므로 한 방향 호출로 쌍 판정이 완결된다. 프루닝: 다른 bay → 충돌 불가, 같은 블록 → ExactlyOne이 배제, 시간 무접촉(`e1 ≥ x2 or e2 ≥ x1`) → 제약 없음(Task-5 표 1행).

- [ ] **Step 1: 실패 테스트 작성** — `tests/test_pool_mip.py` 신규:

```python
"""배치풀 CP-SAT 선택 모델 테스트."""
import json
import random

import pytest

from solver.mh.pool import PlacementPool


def _utils_score(prob, placements):
    from solver.serialize import to_operations
    from utils import check_feasibility
    return check_feasibility(prob, to_operations(placements))


def test_pairwise_conflicts_detects_overlap(prob1):
    """같은 자리+시간겹침 튜플쌍은 충돌 목록에, 시간분리 쌍은 미포함."""
    import ogc_core
    from solver.mh.pool_mip import _pairwise_conflicts
    # test_retiming의 _find_static_pair와 동일 탐색을 로컬로 반복(계획 규칙: 태스크 간 코드 참조 금지)
    import copy
    def sub_two(i, j):
        s = {k: v for k, v in prob1.items() if k != "blocks"}
        s["blocks"] = [copy.deepcopy(prob1["blocks"][i]), copy.deepcopy(prob1["blocks"][j])]
        for b in s["blocks"]:
            b["release_time"] = 0
        return s
    found = None
    for i in range(6):
        for j in range(6):
            if i == j:
                continue
            s = sub_two(i, j)
            core = ogc_core.Core(json.dumps(s))
            p0, p1 = core.processing(0), core.processing(1)
            if core.place(0, 0, 0, 0, 0, 0, p0) and \
               not core.check_place(1, 0, 0, 0, 0, 0, p1) and \
               core.check_place(1, 0, 0, 0, 0, p0, p0 + p1):
                found = (s, p0, p1)
                break
        if found:
            break
    assert found, "static 충돌쌍 미발견 — 코어 회귀 의심"
    s, p0, p1 = found
    core = ogc_core.Core(json.dumps(s))
    cand = [(0, 0, 0, 0, 0, 0, p0),            # t0
            (1, 0, 0, 0, 0, 0, p1),            # t1: t0과 같은 자리+시간겹침 → 충돌
            (1, 0, 0, 0, 0, p0, p0 + p1)]      # t2: 시간분리 → 충돌 아님
    conf = _pairwise_conflicts(core, cand)
    assert (0, 1) in conf and (0, 2) not in conf


def test_select_from_pool_feasible_and_never_worse(prob1):
    """construct 3개(시드 상이)로 풀 구성 → 선택해가 utils feasible + incumbent 비악화."""
    import ogc_core
    from solver.decoder import construct
    from solver.mh.pool_mip import select_from_pool
    core = ogc_core.Core(json.dumps(prob1))
    sols, scores = [], []
    for seed in (0, 1, 2):
        pl = construct(core, prob1, rng=random.Random(seed))
        assert pl is not None
        sols.append(pl)
        scores.append(_utils_score(prob1, pl)["objective"])
    incumbent = sols[scores.index(min(scores))]
    pool = PlacementPool(len(prob1["blocks"]), k=6)
    for pl in sols:
        pool.add_solution(pl, prob1)
    out, st = select_from_pool(core, prob1, pool, incumbent, budget_s=15.0)
    assert st["status"] in ("OPTIMAL", "FEASIBLE", "SKIPPED")
    res = _utils_score(prob1, out)
    assert res["feasible"] is True
    assert res["objective"] <= min(scores) + 1e-6      # 절대 비악화
    assert st["n_tuples"] >= len(prob1["blocks"])      # 블록당 최소 1개(인컴번트 주입)


def test_select_zero_budget_returns_incumbent(prob1):
    import ogc_core
    from solver.decoder import construct
    from solver.mh.pool_mip import select_from_pool
    core = ogc_core.Core(json.dumps(prob1))
    inc = construct(core, prob1, rng=random.Random(0))
    pool = PlacementPool(len(prob1["blocks"]), k=6)
    pool.add_solution(inc, prob1)
    out, st = select_from_pool(core, prob1, pool, inc, budget_s=0.05)
    assert out == inc and st["status"] == "SKIPPED"


def test_unknown_backend_raises(prob1):
    import ogc_core
    from solver.decoder import construct
    from solver.mh.pool_mip import select_from_pool
    core = ogc_core.Core(json.dumps(prob1))
    inc = construct(core, prob1, rng=random.Random(0))
    pool = PlacementPool(len(prob1["blocks"]), k=6)
    pool.add_solution(inc, prob1)
    with pytest.raises(ValueError, match="backend"):
        select_from_pool(core, prob1, pool, inc, budget_s=5.0, backend="gurobi")
```

- [ ] **Step 2: 실패 확인**

Run: `conda run -n ogc2026 python -m pytest tests/test_pool_mip.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'solver.mh.pool_mip'`.

- [ ] **Step 3: 구현** — `solver/mh/pool_mip.py`:

```python
"""배치풀 호환성 선택 모델 (spec §4 L4-2).

블록별 top-K 튜플에서 "블록당 정확히 1개 + 충돌쌍 배제"를 CP-SAT로 풀어
탐색 중 발견한 부분해들의 최적 재조합을 찾는다. 충돌은 기존 Core API의
place/check_place 쌍별 테스트로 사전계산(µs급 x 수만 쌍 = sub-second).

백엔드 절연: _solve_cpsat()만 교체하면 Gurobi drop-in 가능(라이선스 확보 시).
목적함수 정수화: SCALE=10^6 배율. Z2는 스케일 정수 선형화 근사(모듈 docstring이
아니라 Task 5 계획 본문에 근사 오차 문서화). 최종 채점은 core.objective()/utils.
"""
import time
from collections import defaultdict

from ortools.sat.python import cp_model

SCALE = 10 ** 6


def _pairwise_conflicts(core, cand):
    """cand: list[(blk,bay,x,y,o,entry,exit)] → 충돌 튜플 인덱스쌍 (t1<t2) 목록.

    같은 bay + 시간접촉 쌍만 코어로 검사. t1을 빈 코어에 place한 뒤 파트너들을
    check_place — required()가 쌍 양방향을 모두 검사하므로 한 방향이면 충분.
    """
    by_bay = defaultdict(list)
    for t, c in enumerate(cand):
        by_bay[c[1]].append(t)
    conflicts = []
    for bay, ts in sorted(by_bay.items()):
        for a_i, t1 in enumerate(ts):
            b1, bay1, x1, y1, o1, e1, xt1 = cand[t1]
            core.clear()
            ok = core.place(b1, bay1, x1, y1, o1, e1, xt1)
            assert ok, f"풀 튜플이 단독 배치 불가(코어/풀 회귀): {cand[t1]}"
            for t2 in ts[a_i + 1:]:
                b2, bay2, x2, y2, o2, e2, xt2 = cand[t2]
                if b2 == b1:
                    continue                      # ExactlyOne이 배제
                if e1 >= xt2 or e2 >= xt1:
                    continue                      # 시간 무접촉 → 제약 없음(Task-5 표 1행)
                if not core.check_place(b2, bay2, x2, y2, o2, e2, xt2):
                    conflicts.append((t1, t2))
    core.clear()
    return conflicts


def _build_candidates(prob, pool, incumbent):
    """풀 + incumbent 튜플 → 중복 제거된 후보 목록과 incumbent 인덱스."""
    blocks = prob["blocks"]
    seen = set()
    cand, inc_idx = [], []
    for (blk, bay, x, y, o, e, xt) in incumbent:   # incumbent 먼저 → 모델 항상 feasible
        key = (blk, bay, x, y, o, e)
        if key not in seen:
            seen.add(key)
            inc_idx.append(len(cand))
            cand.append((blk, bay, x, y, o, e, xt))
    for blk in range(len(blocks)):
        p = blocks[blk]["processing_time"]
        for (bay, x, y, o, e) in pool.entries(blk):
            key = (blk, bay, x, y, o, e)
            if key not in seen:
                seen.add(key)
                cand.append((blk, bay, x, y, o, e, e + p))
    return cand, inc_idx


def _solve_cpsat(prob, cand, conflicts, inc_idx, budget_s, workers):
    """CP-SAT 백엔드. Gurobi 확보 시 이 함수만 시그니처 동일하게 교체(drop-in)."""
    blocks, bays, w = prob["blocks"], prob["bays"], prob["weights"]
    m = cp_model.CpModel()
    s = [m.NewBoolVar(f"s{t}") for t in range(len(cand))]
    by_block = defaultdict(list)
    for t, c in enumerate(cand):
        by_block[c[0]].append(t)
    assert len(by_block) == len(blocks), "블록당 후보 0개 — incumbent 주입 누락"
    for blk, ts in by_block.items():
        m.AddExactlyOne(s[t] for t in ts)
    for (t1, t2) in conflicts:
        m.AddBoolOr([s[t1].Not(), s[t2].Not()])

    obj_terms = []
    for t, (blk, bay, x, y, o, e, xt) in enumerate(cand):
        b = blocks[blk]
        tard = max(0, xt - b["due_date"])
        pref = max(b["bay_preferences"]) - b["bay_preferences"][bay]
        c = (w["w1"] * tard + w["w3"] * pref) * SCALE
        if c:
            obj_terms.append(c * s[t])

    n_bays = len(bays)
    if n_bays >= 2 and w["w2"] > 0:               # Z2 스케일 정수 선형화 (근사)
        areas = [bb["width"] * bb["height"] for bb in bays]
        avg = sum(areas) / n_bays
        U = [round(avg / a * SCALE) for a in areas]
        max_load = sum(b["workload"] for b in blocks)
        load_expr = {j: [] for j in range(n_bays)}
        for t, (blk, bay, *_rest) in enumerate(cand):
            wl = blocks[blk]["workload"]
            if wl:
                load_expr[bay].append((U[bay] * wl, s[t]))
        D = m.NewIntVar(0, max(U) * max_load, "D")
        for j1 in range(n_bays):
            for j2 in range(j1 + 1, n_bays):
                lhs1 = sum(cf * v for cf, v in load_expr[j1])
                lhs2 = sum(cf * v for cf, v in load_expr[j2])
                m.Add(D >= lhs1 - lhs2)
                m.Add(D >= lhs2 - lhs1)
        obj_terms.append(w["w2"] * D)
    m.Minimize(sum(obj_terms) if obj_terms else 0)

    for t in inc_idx:
        m.AddHint(s[t], 1)                         # incumbent warm start

    solver = cp_model.CpSolver()
    solver.parameters.max_time_in_seconds = max(0.1, budget_s)
    solver.parameters.num_search_workers = workers
    status = solver.Solve(m)
    if status not in (cp_model.OPTIMAL, cp_model.FEASIBLE):
        return None, solver.StatusName(status)
    chosen = [t for t in range(len(cand)) if solver.Value(s[t])]
    return chosen, solver.StatusName(status)


def select_from_pool(core, prob, pool, incumbent, budget_s, workers=4, backend="cpsat"):
    """풀 재조합 선택. 항상 (해, stats) 반환, 절대 악화 없음(나쁘면 incumbent)."""
    t0 = time.monotonic()
    stats = {"stage": "pool", "status": "SKIPPED", "improved": False,
             "n_tuples": 0, "n_conflicts": 0,
             "obj_before": None, "obj_after": None, "wall_s": 0.0}
    if backend != "cpsat":
        raise ValueError(f"unknown backend: {backend!r} (Gurobi는 라이선스 확보 후 drop-in)")
    if budget_s < 0.2 or not incumbent:
        return incumbent, stats

    w = prob["weights"]

    def full_obj(pl):
        core.clear()
        assert core.load_placements(pl) == len(pl)
        z1, z2, z3 = core.objective()
        return w["w1"] * z1 + w["w2"] * z2 + w["w3"] * z3

    obj_inc = full_obj(incumbent)
    stats["obj_before"] = obj_inc

    cand, inc_idx = _build_candidates(prob, pool, incumbent)
    stats["n_tuples"] = len(cand)
    conflicts = _pairwise_conflicts(core, cand)
    stats["n_conflicts"] = len(conflicts)

    solve_s = budget_s - (time.monotonic() - t0)
    if solve_s < 0.2:
        core.clear()
        core.load_placements(incumbent)
        stats["status"] = "SKIPPED"
        stats["wall_s"] = time.monotonic() - t0
        return incumbent, stats
    chosen, status = _solve_cpsat(prob, cand, conflicts, inc_idx, solve_s, workers)
    stats["status"] = status
    if chosen is None:
        core.clear()
        core.load_placements(incumbent)
        stats["wall_s"] = time.monotonic() - t0
        return incumbent, stats

    new_pl = sorted(cand[t] for t in chosen)       # block id 순 정렬(가독·결정성)
    core.clear()
    ok = core.load_placements(new_pl) == len(new_pl) and not core.verify_full()
    if not ok:                                     # 쌍별 사전계산과 코어 전체검증 불일치 방어선
        core.clear()
        core.load_placements(incumbent)
        stats["status"] += "+VERIFY_FAIL"
        stats["wall_s"] = time.monotonic() - t0
        return incumbent, stats
    obj_new = full_obj(new_pl)
    stats["obj_after"] = obj_new
    stats["wall_s"] = time.monotonic() - t0
    if obj_new >= obj_inc:                         # 실측(비근사) 목적으로 최종 판정
        core.clear()
        core.load_placements(incumbent)
        return incumbent, stats
    stats["improved"] = True
    return new_pl, stats
```

- [ ] **Step 4: 통과 확인**

Run: `conda run -n ogc2026 python -m pytest tests/test_pool_mip.py -v`
Expected: 4 passed.

- [ ] **Step 5: 충돌 사전계산 소요 실측 (배치 API 필요성 판단 근거)**

```bash
conda run -n ogc2026 python - <<'EOF'
import json, random, time, sys
sys.path[:0] = ["solver", "baseline"]
import ogc_core
from solver.decoder import construct
from solver.mh.pool import PlacementPool
from solver.mh.pool_mip import _build_candidates, _pairwise_conflicts
prob = json.load(open("data/training_instances/train/prob_1.json"))
core = ogc_core.Core(json.dumps(prob))
pool = PlacementPool(len(prob["blocks"]), k=6)
sols = [construct(core, prob, rng=random.Random(s)) for s in range(6)]
for pl in sols: pool.add_solution(pl, prob)
cand, inc_idx = _build_candidates(prob, pool, sols[0])
t0 = time.monotonic(); conf = _pairwise_conflicts(core, cand); dt = time.monotonic() - t0
print(f"tuples={len(cand)} conflicts={len(conf)} precompute={dt:.2f}s")
EOF
```

Expected: `precompute` **< 2.0s** (튜플 ~600, 같은-bay 쌍 수만 개 × µs급 check_place). 2s 초과 시 "계약 변경 요청"의 배치 API(하단 §계약 변경 요청 1번)를 발동하고, 그 전까지는 k_pool을 6→4로 낮춰 운용.

- [ ] **Step 6: Commit**

```bash
git add solver/mh/pool_mip.py tests/test_pool_mip.py
git commit -m "feat(mh): 배치풀 CP-SAT 선택 모델 — 쌍충돌 사전계산 + Z2 정수 선형화 근사"
```

### Task 6: Z3/Z2 폴리시 — ΔZ1==0 bay 재배정·스왑

**Files:**
- Create: `solver/mh/polish.py`
- Test: `tests/test_polish.py`

**Interfaces:**
- Consumes: `core.remove/place/free_positions/objective/get_placements/load_placements/clear/num_orients/n_bays`, `prob["weights"|"blocks"]`.
- Produces: `polish(core, prob, placements, budget_s) -> tuple[Placements, dict]` — Task 8이 소비. 반환 해는 입력과 entry/exit 완전 동일(ΔZ1≡0 구조 보장), `w2*Z2+w3*Z3` 비악화.

**설계**: 이동은 (entry, exit)을 고정하고 bay/x/y/orient만 바꾼다 → Z1은 exit에만 의존하므로 **구조적으로 ΔZ1==0**. 수용 기준은 `Δ(w2*Z2 + w3*Z3) < 0` (strict). 이동 타깃 위치는 `free_positions`가 주는 feasible 후보 중 원위치와 맨해튼 거리가 가장 가까운 곳(같은/근처 자리 우선). 후보 순서: ① pref_loss 큰 블록부터 선호 bay로 relocate, ② relocate가 안 통하는 블록은 상대 bay 블록과 pairwise swap. first-improvement greedy, 개선 없는 전체 패스에서 종료.

- [ ] **Step 1: 실패 테스트 작성** — `tests/test_polish.py` 신규:

```python
"""Z3/Z2 폴리시 테스트 — ΔZ1==0 보장, 2차 목적 비악화, utils feasible."""
import copy
import json
import random

import pytest


def _utils_score(prob, placements):
    from solver.serialize import to_operations
    from utils import check_feasibility
    return check_feasibility(prob, to_operations(placements))


def test_polish_moves_block_to_preferred_empty_bay(prob1):
    """비선호 bay에 놓인 단일 블록(선호 bay 텅 빔) → 폴리시가 옮겨 Z3=0."""
    import ogc_core
    from solver.mh.polish import polish
    sub = {k: v for k, v in prob1.items() if k != "blocks"}
    sub["blocks"] = [copy.deepcopy(prob1["blocks"][0])]      # prefs [0, 100] → bay1 선호
    core = ogc_core.Core(json.dumps(sub))
    p0, r0 = core.processing(0), core.release(0)
    inc = [(0, 0, 1, 1, 0, r0, r0 + p0)]                      # 비선호 bay0에 배치
    assert core.load_placements(inc) == 1
    out, st = polish(core, sub, inc, budget_s=5.0)
    assert st["improved"] is True and st["moves"] >= 1
    assert out[0][1] == 1                                     # 선호 bay로 이동
    assert out[0][5:7] == inc[0][5:7]                         # entry/exit 불변
    res = _utils_score(sub, out)
    assert res["feasible"] is True and res["obj3"] == 0


def test_polish_never_worse_z1_invariant_on_full_instance(prob1):
    import ogc_core
    from solver.decoder import construct
    from solver.mh.polish import polish
    core = ogc_core.Core(json.dumps(prob1))
    inc = construct(core, prob1, rng=random.Random(0))
    core.clear()
    assert core.load_placements(inc) == len(inc)
    z1b, z2b, z3b = core.objective()
    w = prob1["weights"]
    out, st = polish(core, prob1, inc, budget_s=5.0)
    core.clear()
    assert core.load_placements(out) == len(out)
    z1a, z2a, z3a = core.objective()
    assert z1a == z1b                                          # ΔZ1 == 0
    assert w["w2"] * z2a + w["w3"] * z3a <= w["w2"] * z2b + w["w3"] * z3b
    assert sorted((t[0], t[5], t[6]) for t in out) == \
           sorted((t[0], t[5], t[6]) for t in inc)             # 모든 블록 시간 불변
    assert _utils_score(prob1, out)["feasible"] is True


def test_polish_zero_budget_is_noop(prob1):
    import ogc_core
    from solver.decoder import construct
    from solver.mh.polish import polish
    core = ogc_core.Core(json.dumps(prob1))
    inc = construct(core, prob1, rng=random.Random(1))
    out, st = polish(core, prob1, inc, budget_s=0.0)
    assert out == inc and st["moves"] == 0 and st["swaps"] == 0
```

- [ ] **Step 2: 실패 확인**

Run: `conda run -n ogc2026 python -m pytest tests/test_polish.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'solver.mh.polish'`.

- [ ] **Step 3: 구현** — `solver/mh/polish.py`:

```python
"""Z3/Z2 폴리시 (spec §4 L4-3): Z1 무손실 조건의 bay 재배정·스왑.

이동은 (entry, exit)을 고정한 채 bay/x/y/orient만 변경 → Z1(=f(exit))은 구조적으로
불변. 수용은 Δ(w2*Z2 + w3*Z3) < 0 (strict)일 때만. 위치는 core.free_positions의
feasible 후보 중 원위치 최근접(같은/근처 자리 재배치). first-improvement greedy.
"""
import time


def _secondary(w, z2, z3):
    return w["w2"] * z2 + w["w3"] * z3


def _pref_loss(prob, blk, bay):
    prefs = prob["blocks"][blk]["bay_preferences"]
    return max(prefs) - prefs[bay]


def _relocate(core, cur, bay_to):
    """cur=(blk,bay,x,y,o,e,xt)를 bay_to로 옮겨본다. 성공 시 새 튜플, 실패 시 원복 후 None."""
    blk, bay0, x0, y0, o0, e0, xt0 = cur
    core.remove(blk)
    best = None
    for o in range(core.num_orients(blk)):
        for (x, y) in core.free_positions(blk, bay_to, o, e0, xt0):
            d = abs(x - x0) + abs(y - y0)
            if best is None or d < best[0]:
                best = (d, x, y, o)
    if best is not None:
        _, x, y, o = best
        if core.place(blk, bay_to, x, y, o, e0, xt0):
            return (blk, bay_to, x, y, o, e0, xt0)
    assert core.place(blk, bay0, x0, y0, o0, e0, xt0), "원복 place는 반드시 성공해야 함"
    return None


def _undo(core, placed_tuple, original_tuple):
    core.remove(placed_tuple[0])
    assert core.place(*original_tuple), "원복 place는 반드시 성공해야 함"


def polish(core, prob, placements, budget_s):
    t0 = time.monotonic()
    stats = {"stage": "polish", "moves": 0, "swaps": 0, "improved": False,
             "obj_sec_before": None, "obj_sec_after": None, "wall_s": 0.0}
    core.clear()
    assert core.load_placements(placements) == len(placements)
    if budget_s <= 0.0:
        return placements, stats
    w = prob["weights"]
    z1_ref, z2, z3 = core.objective()
    sec = _secondary(w, z2, z3)
    stats["obj_sec_before"] = sec
    n_bays = core.n_bays

    def out_of_time():
        return time.monotonic() - t0 >= budget_s

    changed = True
    while changed and not out_of_time():
        changed = False
        cur_by_blk = {t[0]: t for t in core.get_placements()}
        # pref_loss 내림차순, 동률이면 workload 내림차순(Z2 지렛대 큰 것 먼저)
        order = sorted(cur_by_blk,
                       key=lambda b: (_pref_loss(prob, b, cur_by_blk[b][1]),
                                      prob["blocks"][b]["workload"]),
                       reverse=True)
        # ① relocate 패스
        for blk in order:
            if out_of_time():
                break
            cur = cur_by_blk[blk]
            targets = sorted((b for b in range(n_bays) if b != cur[1]),
                             key=lambda b: _pref_loss(prob, blk, b))
            for bay_to in targets:
                moved = _relocate(core, cur, bay_to)
                if moved is None:
                    continue
                z1n, z2n, z3n = core.objective()
                assert z1n == z1_ref, "폴리시가 Z1을 건드림 — 이동 로직 버그"
                if _secondary(w, z2n, z3n) < sec:
                    sec = _secondary(w, z2n, z3n)
                    stats["moves"] += 1
                    changed = True
                    cur_by_blk[blk] = moved
                    break
                _undo(core, moved, cur)
        # ② swap 패스: relocate가 못 옮긴 pref_loss>0 블록 × 목표 bay의 블록
        for blk in order:
            if out_of_time():
                break
            cur = cur_by_blk.get(blk)
            if cur is None or _pref_loss(prob, blk, cur[1]) == 0:
                continue
            want = min(range(n_bays), key=lambda b: _pref_loss(prob, blk, b))
            if want == cur[1]:
                continue
            partners = [t for t in core.get_placements() if t[1] == want]
            for pt in partners:
                if out_of_time():
                    break
                moved_a = _relocate(core, cur, want)
                if moved_a is None:
                    continue
                moved_b = _relocate(core, pt, cur[1])
                if moved_b is None:
                    _undo(core, moved_a, cur)
                    continue
                z1n, z2n, z3n = core.objective()
                assert z1n == z1_ref
                if _secondary(w, z2n, z3n) < sec:
                    sec = _secondary(w, z2n, z3n)
                    stats["swaps"] += 1
                    changed = True
                    cur_by_blk[blk] = moved_a
                    cur_by_blk[pt[0]] = moved_b
                    break
                _undo(core, moved_b, pt)
                _undo(core, moved_a, cur)
            if changed:
                break                              # 상태 갱신 후 패스 재시작(스테일 튜플 방지)

    stats["obj_sec_after"] = sec
    stats["improved"] = stats["moves"] + stats["swaps"] > 0
    stats["wall_s"] = time.monotonic() - t0
    return core.get_placements(), stats
```

- [ ] **Step 4: 통과 확인**

Run: `conda run -n ogc2026 python -m pytest tests/test_polish.py -v`
Expected: 3 passed. `assert z1n == z1_ref` 실패가 나오면 `free_positions`/`place`에 잘못된 exit를 넘긴 것 — entry/exit 튜플 자리(인덱스 5,6)를 재확인.

- [ ] **Step 5: Commit**

```bash
git add solver/mh/polish.py tests/test_polish.py
git commit -m "feat(mh): Z3/Z2 폴리시 — ΔZ1==0 bay relocate/swap greedy"
```

### Task 7: 병렬 ALNS 체인 + 단일 체인 폴백

**Files:**
- Create: `solver/parallel.py`
- Test: `tests/test_parallel.py`

**Interfaces:**
- Consumes: `solver.shell.improve(core, incumbent, budget)` (2주차 계약 — 내부 비의존), `solver.core_iface.make_core`, `solver.budget.Budget`, Task 4 `PlacementPool`.
- Produces: `run_single_chain(core, prob, incumbent, budget_s, slice_s=6.0, k_pool=6) -> (Placements, PlacementPool)`, `run_chains(prob_info, incumbent, budget_s, n_workers=2, base_seed=1, slice_s=6.0, k_pool=6) -> (Placements, PlacementPool)` — Task 8이 소비.

**설계**: 워커는 fork로 뜨고(대회 서버 = Linux 고정) 각자 `make_core(prob)`로 **자기 Core**를 만든 뒤 `improve`를 slice_s 단위 Budget으로 반복 호출한다 — 슬라이스마다 나오는 해가 국소최적해이며 풀에 적립("placement-pool-friendly" 사용법). 슬라이스마다 `q_up`으로 (best, pool.dump())를 올리고, 메인이 병합해 더 좋은 best를 `q_down`으로 방송한다. 시드 다양화: 워커에서 `random.seed(seed)` + `improve` 시그니처에 `rng`/`seed` kwarg가 있으면 자동 전달(inspect — 있으면 쓰고 없어도 동작). 4코어 상한: 워커 ≤3 + 메인은 get(timeout) 대기라 코어를 거의 안 씀. **폴백**: 셸이 `run_chains` 예외 시 `run_single_chain`으로 전환하고, 벤치(Task 9)에서 speedup이 안 나오면 `enable_parallel=False`(기본값)를 유지한다.

- [ ] **Step 1: 실패 테스트 작성** — `tests/test_parallel.py` 신규:

```python
"""병렬 체인 배관 테스트. shell.improve를 스텁으로 몽키패치해 결정적으로 검증한다
(fork 시작 방식이라 패치된 모듈 상태가 자식에 복제됨)."""
import json
import random
import time

import pytest


def _stub_improve(core, incumbent, budget):
    """예산만 소모하고 incumbent를 그대로 돌려주는 결정적 스텁."""
    time.sleep(min(0.15, max(0.0, budget.remain())))
    return incumbent


def _utils_feasible(prob, placements):
    from solver.serialize import to_operations
    from utils import check_feasibility
    return check_feasibility(prob, to_operations(placements))


@pytest.fixture()
def incumbent1(prob1):
    import ogc_core
    from solver.decoder import construct
    core = ogc_core.Core(json.dumps(prob1))
    pl = construct(core, prob1, rng=random.Random(0))
    assert pl is not None
    return pl


def test_single_chain_collects_pool_and_never_worse(prob1, incumbent1, monkeypatch):
    import solver.shell as shell
    monkeypatch.setattr(shell, "improve", _stub_improve, raising=False)
    import ogc_core
    from solver.parallel import run_single_chain
    core = ogc_core.Core(json.dumps(prob1))
    best, pool = run_single_chain(core, prob1, incumbent1, budget_s=1.0, slice_s=0.3)
    assert best == incumbent1                       # 스텁이라 개선 없음 = 비악화 확인
    assert len(pool) >= len(prob1["blocks"])        # 블록당 최소 1개 적립
    assert _utils_feasible(prob1, best)["feasible"]


def test_run_chains_returns_feasible_and_joins_workers(prob1, incumbent1, monkeypatch):
    import solver.shell as shell
    monkeypatch.setattr(shell, "improve", _stub_improve, raising=False)
    import multiprocessing as mp
    from solver.parallel import run_chains
    n_alive_before = len(mp.active_children())
    best, pool = run_chains(prob1, incumbent1, budget_s=3.0, n_workers=2, slice_s=0.5)
    assert _utils_feasible(prob1, best)["feasible"]
    assert len(pool) >= len(prob1["blocks"])
    time.sleep(0.5)
    assert len(mp.active_children()) <= n_alive_before   # 좀비 워커 없음


def test_run_chains_wall_clock_bounded(prob1, incumbent1, monkeypatch):
    import solver.shell as shell
    monkeypatch.setattr(shell, "improve", _stub_improve, raising=False)
    from solver.parallel import run_chains
    t0 = time.monotonic()
    run_chains(prob1, incumbent1, budget_s=2.0, n_workers=2, slice_s=0.5)
    assert time.monotonic() - t0 < 2.0 + 6.0        # budget + join 마진 내 종료


def test_real_improve_smoke_if_week2_present(prob1, incumbent1):
    """2주차 improve가 병합돼 있으면 실제 개선 경로 스모크 (없으면 skip)."""
    import solver.shell as shell
    if not hasattr(shell, "improve"):
        pytest.skip("week2 improve 미병합")
    import ogc_core
    from solver.parallel import run_single_chain
    core = ogc_core.Core(json.dumps(prob1))
    best, pool = run_single_chain(core, prob1, incumbent1, budget_s=8.0, slice_s=2.0)
    assert _utils_feasible(prob1, best)["feasible"]
```

- [ ] **Step 2: 실패 확인**

Run: `conda run -n ogc2026 python -m pytest tests/test_parallel.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'solver.parallel'`.

- [ ] **Step 3: 구현** — `solver/parallel.py`:

```python
"""병렬 ALNS 체인 (spec §4 L3 병렬화): fork 워커 2~3개 x (자기 Core + 자기 시드),
슬라이스마다 best/pool을 multiprocessing.Queue로 교환. 4코어 상한 준수.

단일 체인(run_single_chain)이 기본 경로이자 폴백 — 병렬은 벤치로 이득이 검증될 때만
MhCfg.enable_parallel=True로 켠다(오버헤드 지배 시 단일 체인 유지).
"""
import inspect
import json
import multiprocessing as mp
import queue as pyqueue
import random
import time

from solver.budget import Budget
from solver.mh.pool import PlacementPool


def _score(core, prob, placements):
    core.clear()
    if core.load_placements(placements) != len(placements):
        return float("inf")
    z1, z2, z3 = core.objective()
    w = prob["weights"]
    return w["w1"] * z1 + w["w2"] * z2 + w["w3"] * z3


def _improve_kwargs(improve_fn, seed):
    """2주차 improve가 rng/seed kwarg를 지원하면 활용(없어도 동작)."""
    try:
        params = inspect.signature(improve_fn).parameters
    except (TypeError, ValueError):
        return {}
    if "rng" in params:
        return {"rng": random.Random(seed)}
    if "seed" in params:
        return {"seed": seed}
    return {}


def run_single_chain(core, prob, incumbent, budget_s, slice_s=6.0, k_pool=6, seed=0):
    """improve를 슬라이스 단위로 반복하며 국소최적해를 풀에 적립. 반환 시 core=best 상태."""
    import solver.shell as shell                    # 지연 import (순환 참조 회피)
    pool = PlacementPool(len(prob["blocks"]), k=k_pool)
    pool.add_solution(incumbent, prob)
    best = incumbent
    best_obj = _score(core, prob, best)
    kw = _improve_kwargs(shell.improve, seed)
    deadline = time.monotonic() + budget_s
    while deadline - time.monotonic() > 0.5:
        b = Budget(min(slice_s, deadline - time.monotonic()), safety=0.0)
        cand = shell.improve(core, best, b, **kw)
        if cand:
            obj = _score(core, prob, cand)
            pool.add_solution(cand, prob)
            if obj < best_obj:
                best, best_obj = cand, obj
    core.clear()
    core.load_placements(best)
    return best, pool


def _worker(wid, prob_json, incumbent, seed, slice_s, deadline, q_up, q_down, k_pool):
    import solver.shell as shell
    from solver.core_iface import make_core
    random.seed(seed)                               # improve가 전역 rng를 쓰는 경우 대비
    prob = json.loads(prob_json)
    core = make_core(prob)
    pool = PlacementPool(len(prob["blocks"]), k=k_pool)
    pool.add_solution(incumbent, prob)
    best = incumbent
    best_obj = _score(core, prob, best)
    kw = _improve_kwargs(shell.improve, seed)
    try:
        while deadline - time.monotonic() > 0.5:
            b = Budget(min(slice_s, deadline - time.monotonic()), safety=0.0)
            cand = shell.improve(core, best, b, **kw)
            if cand:
                obj = _score(core, prob, cand)
                pool.add_solution(cand, prob)
                if obj < best_obj:
                    best, best_obj = cand, obj
            try:
                q_up.put_nowait(("prog", wid, best_obj, best, pool.dump()))
            except pyqueue.Full:
                pass
            try:                                    # 외부 best 수신 (non-blocking drain)
                while True:
                    _tag, f_obj, f_best = q_down.get_nowait()
                    if f_obj < best_obj:
                        best, best_obj = f_best, f_obj
            except pyqueue.Empty:
                pass
    finally:
        q_up.put(("done", wid, best_obj, best, pool.dump()))


def run_chains(prob_info, incumbent, budget_s, n_workers=2, base_seed=1,
               slice_s=6.0, k_pool=6):
    """병렬 체인. 실패 가능 지점은 호출측(shell)이 try/except로 감싸 단일 체인 폴백."""
    from solver.core_iface import make_core
    assert 1 <= n_workers <= 3, "4코어 상한: 워커는 최대 3"
    prob_json = json.dumps(prob_info)
    merged = PlacementPool(len(prob_info["blocks"]), k=k_pool)
    merged.add_solution(incumbent, prob_info)
    score_core = make_core(prob_info)
    best = incumbent
    best_obj = _score(score_core, prob_info, incumbent)
    deadline = time.monotonic() + budget_s

    ctx = mp.get_context("fork")                    # 대회 서버 = Linux 고정
    q_up = ctx.Queue(maxsize=8 * n_workers)
    q_downs = [ctx.Queue(maxsize=4) for _ in range(n_workers)]
    procs = [ctx.Process(target=_worker,
                         args=(w, prob_json, incumbent, base_seed + 1000 * w,
                               slice_s, deadline, q_up, q_downs[w], k_pool),
                         daemon=True)
             for w in range(n_workers)]
    try:
        for pr in procs:
            pr.start()
        done = 0
        hard_stop = deadline + slice_s + 5.0
        while done < n_workers and time.monotonic() < hard_stop:
            try:
                tag, wid, obj, pl, dump = q_up.get(timeout=1.0)
            except pyqueue.Empty:
                continue
            merged.merge(dump, prob_info)
            if obj < best_obj:
                best, best_obj = pl, obj
                for w2, qd in enumerate(q_downs):
                    if w2 != wid:
                        try:
                            qd.put_nowait(("best", best_obj, best))
                        except pyqueue.Full:
                            pass
            if tag == "done":
                done += 1
    finally:
        for pr in procs:
            pr.join(timeout=3.0)
            if pr.is_alive():
                pr.terminate()
                pr.join(timeout=2.0)
    if _score(score_core, prob_info, best) == float("inf"):   # 방어: 손상 해 수신 시
        best = incumbent
    return best, merged
```

- [ ] **Step 4: 통과 확인**

Run: `conda run -n ogc2026 python -m pytest tests/test_parallel.py -v`
Expected: 4 passed (week2 improve 미병합 상태면 3 passed + 1 skipped).

- [ ] **Step 5: Commit**

```bash
git add solver/parallel.py tests/test_parallel.py
git commit -m "feat(solver): 병렬 ALNS 체인 — fork 워커 + Queue best/pool 교환, 단일 체인 폴백"
```

### Task 8: 셸 오케스트레이션 — construct → ALNS(~80%) → retiming → pool → polish → 최종검증

**Files:**
- Modify: `solver/shell.py` (2주차까지의 solve 골격 유지, ALNS 이후 구간 확장)
- Test: `tests/test_shell_phases.py`

**Interfaces:**
- Consumes: Task 3 `retime`, Task 5 `select_from_pool`, Task 6 `polish`, Task 7 `run_single_chain/run_chains`, 1주차 `Budget/construct/serialize/trivial/utils 최종검증`(기존 solve 골격), 2주차 `improve`.
- Produces: `MhCfg`(+`MhCfg.from_env()`), `solve(prob_info, timelimit, cfg=None, stats_out=None) -> dict`(기존 2-인자 호환 — `submission/myalgorithm.py` 무수정), `run_matheuristics(core, prob, incumbent, mh_budget_s, cfg, stats) -> Placements`. `stats_out`에 `"alns"` dict와 `"mh_stages"` list가 채워짐 — Task 9 벤치가 소비.

**병합 지점 주의**: 2주차가 solve 내부에서 `improve(core, incumbent, budget)`를 호출하는 블록이 있다. 그 블록을 아래 "ALNS + 매트휴리스틱" 블록으로 교체한다. 1주차의 앞부분(Budget 생성 → core 생성 → trivial 안전망 → construct 멀티스타트)과 뒷부분(최종 utils.check_feasibility → 실패 시 하위 폴백 → 최상위 try/except로 trivial 반환)은 그대로 유지한다.

- [ ] **Step 1: 실패 테스트 작성** — `tests/test_shell_phases.py` 신규:

```python
"""셸 오케스트레이션 테스트 — 단계 스케줄, on/off 스위치, 예산 준수, 최종 feasible."""
import time

import pytest

from utils import check_feasibility


def _stub_improve(core, incumbent, budget):
    t = max(0.0, budget.remain())
    time.sleep(min(0.1, t))
    return incumbent


@pytest.fixture()
def fast_improve(monkeypatch):
    import solver.shell as shell
    monkeypatch.setattr(shell, "improve", _stub_improve, raising=False)


def test_solve_two_arg_signature_still_works(prob1, fast_improve):
    from solver.shell import solve
    sol = solve(prob1, 20)                          # myalgorithm 호환 (cfg/stats 없이)
    assert check_feasibility(prob1, sol)["feasible"] is True


def test_solve_runs_all_stages_and_logs(prob1, fast_improve):
    from solver.shell import MhCfg, solve
    st = {}
    cfg = MhCfg(enable_retiming=True, enable_pool=True, enable_polish=True,
                enable_parallel=False)
    t0 = time.monotonic()
    sol = solve(prob1, 30, cfg=cfg, stats_out=st)
    assert time.monotonic() - t0 <= 30.0            # 예산 준수(안전마진 포함)
    assert check_feasibility(prob1, sol)["feasible"] is True
    names = [s["stage"] for s in st["mh_stages"]]
    assert names == ["retiming", "pool", "polish"]
    for s in st["mh_stages"]:                       # 스테이지별 비악화
        assert s["obj_after"] <= s["obj_before"] + 1e-6
    assert st["alns"]["pool_size"] >= len(prob1["blocks"])


def test_solve_respects_disable_flags(prob1, fast_improve):
    from solver.shell import MhCfg, solve
    st = {}
    cfg = MhCfg(enable_retiming=False, enable_pool=False, enable_polish=True)
    sol = solve(prob1, 20, cfg=cfg, stats_out=st)
    assert check_feasibility(prob1, sol)["feasible"] is True
    assert [s["stage"] for s in st["mh_stages"]] == ["polish"]


def test_mhcfg_from_env(monkeypatch):
    from solver.shell import MhCfg
    monkeypatch.setenv("OGC_MH_DISABLE", "pool,polish")
    monkeypatch.setenv("OGC_PARALLEL", "1")
    cfg = MhCfg.from_env()
    assert cfg.enable_retiming and not cfg.enable_pool and not cfg.enable_polish
    assert cfg.enable_parallel


def test_mh_stage_failure_keeps_incumbent(prob1, fast_improve, monkeypatch):
    """스테이지가 예외를 던져도 solve는 feasible 해를 반환한다(견고성 셸)."""
    import solver.shell as shell

    def boom(*a, **k):
        raise RuntimeError("injected")

    monkeypatch.setattr(shell, "retime", boom)
    st = {}
    sol = shell.solve(prob1, 20, cfg=shell.MhCfg(), stats_out=st)
    assert check_feasibility(prob1, sol)["feasible"] is True
```

- [ ] **Step 2: 실패 확인**

Run: `conda run -n ogc2026 python -m pytest tests/test_shell_phases.py -v`
Expected: FAIL — `ImportError: cannot import name 'MhCfg'`.

- [ ] **Step 3: 구현** — `solver/shell.py`에 추가/수정. ① 상단 import에 추가:

```python
import os
import time
from dataclasses import dataclass

from solver.mh.retiming import retime
from solver.mh.pool_mip import select_from_pool
from solver.mh.polish import polish
from solver import parallel
```

② `MhCfg`와 `run_matheuristics` 추가:

```python
@dataclass
class MhCfg:
    """매트휴리스틱 단계 스위치·예산 배분 (spec §4 L4: 예산 후반 ~20%)."""
    enable_retiming: bool = True
    enable_pool: bool = True
    enable_polish: bool = True
    enable_parallel: bool = False      # 벤치로 이득 검증 전 기본 off (Task 9)
    n_chains: int = 2                  # 4코어 상한: 2~3
    mh_frac: float = 0.20              # improve+mh 예산 중 매트휴리스틱 몫
    retiming_frac: float = 0.35        # mh 예산 내부 배분 (합 0.90, 잔여 0.10 = 마진)
    pool_frac: float = 0.40
    polish_frac: float = 0.15
    slice_s: float = 6.0               # ALNS 슬라이스(풀 적립 주기)
    k_pool: int = 6
    min_stage_s: float = 0.5           # 이보다 짧게 남으면 스테이지 스킵

    @classmethod
    def from_env(cls):
        cfg = cls()
        disabled = set(filter(None, os.environ.get("OGC_MH_DISABLE", "").split(",")))
        cfg.enable_retiming = "retiming" not in disabled
        cfg.enable_pool = "pool" not in disabled
        cfg.enable_polish = "polish" not in disabled
        cfg.enable_parallel = os.environ.get("OGC_PARALLEL", "0") == "1"
        return cfg


def _full_obj(core, prob, placements):
    core.clear()
    assert core.load_placements(placements) == len(placements)
    z1, z2, z3 = core.objective()
    w = prob["weights"]
    return w["w1"] * z1 + w["w2"] * z2 + w["w3"] * z3


def run_matheuristics(core, prob, incumbent, mh_budget_s, cfg, stats):
    """retiming → pool-selection → polish. 각 단계 time-box + skippable + 비악화 보장.

    단계 예외는 여기서 포획해 해당 단계만 버린다(incumbent 유지) — 견고성 셸 원칙.
    stats["mh_stages"]에 {stage, obj_before, obj_after, wall_s, ...} 축적.
    """
    stages = stats.setdefault("mh_stages", [])
    best = incumbent
    t_end = time.monotonic() + mh_budget_s
    pool = stats.pop("_pool", None)

    def run_stage(name, enabled, frac, fn):
        nonlocal best
        remain = t_end - time.monotonic()
        tb = min(mh_budget_s * frac, remain)
        if not enabled or tb < cfg.min_stage_s:
            return
        obj0 = _full_obj(core, prob, best)
        try:
            out, st = fn(tb)
        except Exception as ex:                       # 단계 실패 = 그 단계만 포기
            stages.append({"stage": name, "status": f"ERROR:{type(ex).__name__}",
                           "obj_before": obj0, "obj_after": obj0, "wall_s": 0.0})
            core.clear()
            core.load_placements(best)
            return
        st["obj_before"] = obj0
        st["obj_after"] = _full_obj(core, prob, out)
        stages.append(st)
        best = out

    run_stage("retiming", cfg.enable_retiming, cfg.retiming_frac,
              lambda tb: retime(core, prob, best, tb, workers=4))
    run_stage("pool", cfg.enable_pool and pool is not None
              and len(pool) > len(prob["blocks"]), cfg.pool_frac,
              lambda tb: select_from_pool(core, prob, pool, best, tb, workers=4))
    run_stage("polish", cfg.enable_polish, cfg.polish_frac,
              lambda tb: polish(core, prob, best, tb))
    return best
```

③ `solve` 시그니처를 `def solve(prob_info, timelimit, cfg=None, stats_out=None):`로 확장하고, 기존 "improve 호출 블록"을 아래로 교체 (앞: incumbent = 멀티스타트 construct 최선해 확보됨 / 뒤: 기존 최종 utils 검증·폴백·직렬화 그대로):

```python
    # ---- ALNS(~80%) + 매트휴리스틱(~20%) ----------------------------------
    cfg = cfg or MhCfg.from_env()
    stats = stats_out if stats_out is not None else {}
    improve_total = max(0.0, budget.remain())          # 검증·직렬화 마진은 Budget safety가 이미 확보
    alns_s = improve_total * (1.0 - cfg.mh_frac)
    mh_s = improve_total - alns_s
    obj0 = _full_obj(core, prob_info, incumbent)
    t_alns = time.monotonic()
    pool = None
    if alns_s >= cfg.min_stage_s:
        mode = "single"
        if cfg.enable_parallel:
            try:
                incumbent, pool = parallel.run_chains(
                    prob_info, incumbent, alns_s, n_workers=cfg.n_chains,
                    slice_s=cfg.slice_s, k_pool=cfg.k_pool)
                mode = f"parallel{cfg.n_chains}"
                core.clear()
                core.load_placements(incumbent)
            except Exception:
                pool = None                            # 병렬 실패 → 단일 체인 폴백
        if pool is None:
            incumbent, pool = parallel.run_single_chain(
                core, prob_info, incumbent, max(0.0, alns_s - (time.monotonic() - t_alns)),
                slice_s=cfg.slice_s, k_pool=cfg.k_pool)
            mode = mode if mode.startswith("parallel") else "single"
        stats["alns"] = {"mode": mode, "obj_before": obj0,
                         "obj_after": _full_obj(core, prob_info, incumbent),
                         "pool_size": len(pool), "wall_s": time.monotonic() - t_alns}
        stats["_pool"] = pool
    incumbent = run_matheuristics(core, prob_info, incumbent,
                                  min(mh_s, budget.remain()), cfg, stats)
    # ---- (기존) 최종 utils.check_feasibility 검증 → 실패 시 하위 폴백 ----
```

- [ ] **Step 4: 통과 확인**

Run: `conda run -n ogc2026 python -m pytest tests/test_shell_phases.py tests/test_shell.py -v`
Expected: 신규 5개 + 1주차 test_shell 전부 PASS (기존 2-인자 호출·폴백 체인 회귀 없음).

- [ ] **Step 5: Commit**

```bash
git add solver/shell.py tests/test_shell_phases.py
git commit -m "feat(shell): L4 오케스트레이션 — ALNS(80%)→retiming→pool→polish, MhCfg 스위치"
```

### Task 9: 벤치 하네스 확장 — 스테이지 on/off + 기여도 리포트 + ablation

**Files:**
- Modify: `experiments/run_bench.py` (1주차 CLI/스키마 확장 — 기존 필드 유지)
- Test: 수동 실행 (아래 측정 명령), 스키마는 스모크 assert 포함

**Interfaces:**
- Consumes: Task 8 `solve(prob_info, timelimit, cfg, stats_out)`, `MhCfg`.
- Produces: CLI 플래그 `--disable {retiming,pool,polish}`(반복 가능), `--parallel N`(0=off), `--slice S`, `--kpool K`, `--ablation`; 결과 JSON 인스턴스 레코드에 `"stages"`, `"alns"`, `"budget_utilization"` 필드 추가. 4주차 튜닝·보고서가 이 스키마를 소비.

- [ ] **Step 1: run_bench.py 확장 구현** — 기존 argparse에 추가:

```python
    ap.add_argument("--disable", action="append", default=[],
                    choices=["retiming", "pool", "polish"],
                    help="끌 매트휴리스틱 스테이지 (반복 지정 가능)")
    ap.add_argument("--parallel", type=int, default=0,
                    help="병렬 체인 워커 수 (0=단일 체인)")
    ap.add_argument("--slice", type=float, default=6.0, help="ALNS 슬라이스 초")
    ap.add_argument("--kpool", type=int, default=6, help="블록당 풀 튜플 수")
    ap.add_argument("--ablation", action="store_true",
                    help="사전 정의된 스테이지 조합 스윕 실행")
```

cfg 구성과 인스턴스 1개 실행 함수 (기존 `shell.solve(prob, timelimit)` 호출부를 교체):

```python
from solver.shell import MhCfg, solve

ABLATION_PRESETS = [
    ("alns_only",   {"enable_retiming": False, "enable_pool": False, "enable_polish": False}),
    ("retime",      {"enable_pool": False, "enable_polish": False}),
    ("retime_pool", {"enable_polish": False}),
    ("full",        {}),
    ("full_par2",   {"enable_parallel": True, "n_chains": 2}),
    ("full_par3",   {"enable_parallel": True, "n_chains": 3}),
]


def make_cfg(args, overrides=None):
    kw = dict(enable_retiming="retiming" not in args.disable,
              enable_pool="pool" not in args.disable,
              enable_polish="polish" not in args.disable,
              enable_parallel=args.parallel > 0,
              n_chains=max(1, args.parallel) if args.parallel else 2,
              slice_s=args.slice, k_pool=args.kpool)
    kw.update(overrides or {})
    return MhCfg(**kw)


def run_one(name, prob, timelimit, cfg):
    stats = {}
    t0 = time.monotonic()
    sol = solve(prob, timelimit, cfg=cfg, stats_out=stats)
    runtime = time.monotonic() - t0
    res = check_feasibility(prob, sol)
    rec = {"instance": name, "feasible": res["feasible"],
           "objective": res["objective"], "obj1": res["obj1"],
           "obj2": res["obj2"], "obj3": res["obj3"],
           "runtime": runtime, "timelimit": timelimit,
           "budget_utilization": runtime / timelimit,
           "alns": stats.get("alns"),
           "stages": [{k: v for k, v in s.items() if k != "_pool"}
                      for s in stats.get("mh_stages", [])]}
    assert set(rec) >= {"instance", "feasible", "objective", "stages"}   # 스키마 스모크
    return rec
```

`--ablation`이면 인스턴스×프리셋 이중 루프로 `run_one`을 호출하고 레코드에 `"preset": preset_name`을 추가해 같은 JSON에 축적한다. 요약 표 출력에 프리셋별 `Σobjective`와 스테이지별 평균 개선량(`obj_before - obj_after`)을 추가:

```python
def print_contribution(records):
    by = {}
    for r in records:
        for s in r.get("stages", []) or []:
            d = by.setdefault(s["stage"], [0.0, 0])
            d[0] += (s["obj_before"] - s["obj_after"])
            d[1] += 1
    print("stage contribution (mean Δobj):")
    for k, (tot, n) in sorted(by.items()):
        print(f"  {k:9s} n={n:3d}  meanΔ={tot / max(1, n):12.1f}")
```

- [ ] **Step 2: 동작 스모크**

```bash
conda run -n ogc2026 python experiments/run_bench.py \
  --instances prob_1 --timelimit 60 --out experiments/results/week3_smoke.json
conda run -n ogc2026 python experiments/run_bench.py \
  --instances prob_1 --timelimit 60 --disable pool --disable polish \
  --out experiments/results/week3_smoke2.json
```

Expected: 두 실행 모두 feasible, 두 번째 JSON의 stages에 `retiming`만 존재. JSON에 `budget_utilization` 필드 존재.

- [ ] **Step 3: 3주차 완료 기준 측정 ① — 레이어별 기여도 ablation (spec §7)**

```bash
conda run -n ogc2026 python experiments/run_bench.py \
  --timelimit 300 --ablation --out experiments/results/week3_ablation.json
```

Expected: 전 인스턴스 × 6 프리셋 feasible 100%. 기록할 것: 프리셋별 Σobj (alns_only 대비 full의 개선률), 스테이지별 mean Δobj, `full_par2/3` vs `full`의 Δobj(병렬 이득). **병렬 이득이 0 이하이면 `MhCfg.enable_parallel` 기본값을 False로 유지**하고 그 근거 수치를 결과 요약에 남긴다.

- [ ] **Step 4: 3주차 완료 기준 측정 ② — 30분 예산 활용률 (spec §7)**

```bash
conda run -n ogc2026 python experiments/run_bench.py \
  --timelimit 1800 --instances prob_1,prob_23,prob_25 \
  --out experiments/results/week3_1800.json
```

Expected: `budget_utilization ≥ 0.90` (조기 고갈·유휴 없이 마지막 20%가 매트휴리스틱에 쓰임), 인스턴스별 stages wall_s 합이 mh 예산의 ±20% 이내, 전부 feasible. 미달 시 mh_frac/단계 frac 재배분 후 재측정.

- [ ] **Step 5: 결과 요약을 이 계획 파일 하단에 추기하고 Commit**

```bash
git add experiments/run_bench.py experiments/results/week3_ablation.json \
        experiments/results/week3_1800.json docs/superpowers/plans/2026-07-02-ogc2026-week3-matheuristics.md
git commit -m "feat(bench): 스테이지 on/off + 기여도 리포트 + ablation, 3주차 완료 기준 측정"
```

### Task 10 (OPTIONAL — 주차 잔여 시간 있을 때만, spec §7 "여유 시"): beam-search 구성기

**앞의 Task 1~9와 완료 기준 측정이 모두 끝나고 시간이 남을 때만 착수한다. 미착수여도 3주차 완료로 간주.**

**Files:**
- Create: `solver/beam.py`
- Test: `tests/test_beam.py`

**Interfaces:**
- Consumes: `core.clear/load_placements/earliest_feasible/get_placements/processing/release/num_orients/n_bays`, `solver.priority.atc_order(prob)` (1주차, rng=None 결정적).
- Produces: `construct_beam(core, prob, beam_width=8, child_per_node=4, max_delay=3) -> Placements | None` — restart 다양화용 대체 construct. 셸 멀티스타트에서 `construct`와 번갈아 쓸 수 있는 동일 반환 형식.

- [ ] **Step 1: 실패 테스트 작성** — `tests/test_beam.py` 신규:

```python
"""beam-search 구성기 테스트 (OPTIONAL 태스크)."""
import json


def test_beam_constructs_feasible(prob1):
    import ogc_core
    from solver.beam import construct_beam
    from solver.serialize import to_operations
    from utils import check_feasibility
    core = ogc_core.Core(json.dumps(prob1))
    pl = construct_beam(core, prob1, beam_width=6)
    assert pl is not None and len(pl) == len(prob1["blocks"])
    res = check_feasibility(prob1, to_operations(pl))
    assert res["feasible"] is True


def test_beam_differs_from_greedy(prob1):
    """다양화 가치 확인: greedy construct와 다른 해를 낸다(동일하면 restart 가치 없음)."""
    import random
    import ogc_core
    from solver.beam import construct_beam
    from solver.decoder import construct
    core = ogc_core.Core(json.dumps(prob1))
    a = construct_beam(core, prob1, beam_width=6)
    b = construct(core, prob1, rng=random.Random(0))
    assert a is not None and b is not None
    assert sorted(a) != sorted(b)
```

- [ ] **Step 2: 실패 확인**

Run: `conda run -n ogc2026 python -m pytest tests/test_beam.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'solver.beam'`.

- [ ] **Step 3: 구현** — `solver/beam.py`:

```python
"""beam-search 구성기 (restart 다양화용 대체 construct).

노드 = (부분 placements, 누적 tardiness). 코어는 상태 복제가 없으므로 노드 확장 시
prefix를 clear+load_placements로 재생(replay)한다: 레벨당 place 호출 ~ beam*|prefix|,
n=100·beam=8이면 총 ~40k place ≈ 수백 ms (1주차 µs급 place 전제).
"""
from solver.priority import atc_order


def construct_beam(core, prob, beam_width=8, child_per_node=4, max_delay=3):
    blocks = prob["blocks"]
    order = atc_order(prob)                       # 결정적 ATC 순
    horizon = max(b["due_date"] for b in blocks) * 3
    beam = [([], 0)]                              # (placements, tard_sum)
    for blk in order:
        R = core.release(blk)
        P = core.processing(blk)
        due = blocks[blk]["due_date"]
        children = []
        for prefix, tsum in beam:
            core.clear()
            if prefix:
                assert core.load_placements(prefix) == len(prefix), "prefix replay 실패"
            cands = []
            for t in range(R, R + max_delay + 1):     # 소폭 지연 창 우선 탐색
                for bay in range(core.n_bays):
                    for o in range(core.num_orients(blk)):
                        hit = core.earliest_feasible(blk, bay, o, t, t)
                        if hit:
                            e, x, y = hit
                            tard = max(0, e + P - due)
                            cands.append((tard, y, x, bay, o, e))
                if cands and min(c[0] for c in cands) == 0:
                    break                              # 지각 0 발견 → 창 확장 불필요
            if not cands:                              # 창 실패 → bay/orient별 최초 가능 시각
                for bay in range(core.n_bays):
                    for o in range(core.num_orients(blk)):
                        hit = core.earliest_feasible(blk, bay, o, R, horizon)
                        if hit:
                            e, x, y = hit
                            cands.append((max(0, e + P - due), y, x, bay, o, e))
            cands.sort()
            for (tard, y, x, bay, o, e) in cands[:child_per_node]:
                child = prefix + [(blk, bay, x, y, o, e, e + P)]
                children.append((child, tsum + tard))
        if not children:
            return None                            # 어느 노드도 배치 불가
        # 누적 tardiness 오름차순, 동률이면 낮고-왼쪽(y,x 합) 타이브레이크
        children.sort(key=lambda c: (c[1], sum(p[3] + p[2] for p in c[0][-1:])))
        # 중복 prefix 제거(동일 (bay,x,y,e) 자식) 후 beam_width 유지
        seen, beam = set(), []
        for child, tsum in children:
            key = child[-1][1:]
            if key in seen:
                continue
            seen.add(key)
            beam.append((child, tsum))
            if len(beam) >= beam_width:
                break
    best, _ = min(beam, key=lambda c: c[1])
    core.clear()
    assert core.load_placements(best) == len(best)
    return best
```

- [ ] **Step 4: 통과 확인**

Run: `conda run -n ogc2026 python -m pytest tests/test_beam.py -v`
Expected: 2 passed. prob_1에서 wall < 10s가 아니면 `beam_width`·`child_per_node` 축소.

- [ ] **Step 5: Commit**

```bash
git add solver/beam.py tests/test_beam.py
git commit -m "feat(solver): beam-search 대체 구성기 (OPTIONAL, restart 다양화)"
```

---

## 계약 변경 요청

이번 주 구현은 **1주차 Core/solver 계약 변경 없이 전부 가능**하도록 설계했다 (재타이밍 = `pair_bits_at_current`, 풀 충돌 = `place`+`check_place`, 폴리시 = `remove/free_positions/place/objective`). 아래는 조건부·선택 요청이며, 발동 조건을 명시한다.

1. **[조건부 — Core 추가 API] 배치 쌍충돌 일괄 판정** — 발동 조건: Task 5 Step 5 실측에서 `_pairwise_conflicts`가 2.0s 초과(그 전까지는 k_pool 6→4 축소로 대응).
   - 제안 C++ 시그니처 (`core/src/state.hpp`):
     ```cpp
     // tuples: (block, bay, x, y, orient, entry, exit) — get_placements()와 동일 형식.
     // 반환: 페어 제약을 위반하는 튜플 인덱스쌍 (t1 < t2). 상태 불변(조회 전용).
     // 내부 스킵: 다른 bay, 같은 block, 시간 무접촉(e1 >= x2 || e2 >= x1).
     std::vector<std::pair<int, int>> pair_conflicts(
         const std::vector<std::array<int64_t, 7>>& tuples) const;
     ```
   - pybind11 (`core/src/bindings.cpp`): `.def("pair_conflicts", &Core::pair_conflicts, py::arg("tuples"))` → Python `core.pair_conflicts(list[tuple[int,int,int,int,int,int,int]]) -> list[tuple[int,int]]`.
   - 근거: T≈600 튜플이면 같은-bay 쌍 수만 개 × (Python 루프 + 바인딩 왕복 ~2µs)이 병목. C++ 내부 O(T²) 비트맵 테스트면 ~50-100ms. 채택 시 `pool_mip._pairwise_conflicts`는 `hasattr(core, "pair_conflicts")`로 자동 전환하고 기존 Python 경로를 fallback 코어용으로 유지.
2. **[선택 — 2주차 solver 계약 확장] `improve(core, incumbent, budget, rng=None)`** — 병렬 체인의 시드 다양화용 optional kwarg. `solver/parallel.py`는 `inspect.signature`로 자동 감지하므로 **없어도 동작**하지만, 현재는 워커별 `random.seed()`(전역)에 의존해 재현성이 약하다. Week-2 계획 소유자와 동기화 필요 (Core 변경 아님).
3. **[가정 명시 — 변경 아님]** `pair_bits_at_current()`가 all-zero 비트 쌍을 포함해 반환해도 무해하다(재타이밍이 필터). 반대로 nonzero 쌍만 반환해도 무해하다. 단 **같은 bay에서 비트가 하나라도 켜진 쌍은 빠짐없이 포함**되어야 하며, 이는 1주차 계약 문면("같은 bay에 배치된 쌍의 현재 오프셋 비트") 그대로다 — 추가 요구 없음.

## Self-Review 체크 결과

**1. 스펙 커버리지** (설계서 §4 L4·§7 3주차 행 대비):
- §4 L4-1 CP-SAT 재타이밍(기하 고정, interval, pairwise disjunction + 스윕 방향 조건, 수 초 풀이) → Task 2(인코딩+진리표 오라클), Task 3(모델·풀이·검증·비악화 가드). num_search_workers=4, budget time-box 포함.
- §4 L4-2 배치풀 호환성 선택(top-K 풀, 페어와이즈 충돌 사전계산, exactly-one + 충돌 배제, Gurobi 보류) → Task 4(풀), Task 5(충돌+모델+백엔드 절연 `_solve_cpsat`). Z2는 스케일 정수 선형화로 모델에 포함하고 근사 오차(U 반올림 ≤1e-6; utils obj2는 float 그대로라 floor 오차 없음)를 문서화, 최종 판정은 실측 objective — "선형화 문서화" 요구 충족.
- §4 L4-3 Z3/Z2 폴리시(Z1 무손실 bay 재할당·스왑) → Task 6 (entry/exit 고정으로 ΔZ1≡0 구조 보장 + assert).
- §4 L3 병렬화(체인 2~3 + 공유 배치풀·베스트 교환, 4코어 상한) → Task 7 + Task 9 프리셋 `full_par2/3`으로 speedup 실측, 이득 없으면 기본 off(단일 체인 폴백) 명시.
- §4 L4 "예산 후반 ~20%" + 단계 skippable → Task 8 (mh_frac=0.20, min_stage_s 스킵, 단계 예외 격리, 기존 최종 utils 검증·폴백 체인 유지).
- §7 3주차 완료 기준(레이어별 기여도 ablation, 30분 예산 활용률) → Task 9 Step 3·4 (측정 명령·합격 수치 명시).
- "여유 시 beam 구성기" → Task 10 (OPTIONAL 명시, 미착수여도 완료).
- 커버리지 갭: 없음. (Gurobi 이관·firejail 리허설·패키징은 스펙 로드맵상 4주차.)
**2. 플레이스홀더 스캔**: "TBD/TODO/나중에/적절히/Similar to Task N" 없음. 모든 코드 스텝에 실제 코드, 모든 Run 스텝에 명령+기대 출력 존재. 테스트 헬퍼(`_find_static_pair` 등)는 태스크 간 참조 없이 파일마다 반복 정의(규칙 준수). 확인.
**3. 타입/시그니처 일관성**: `retime/select_from_pool/polish`는 모두 `(Placements, dict)` 반환으로 통일, stats에 `stage/obj_before/obj_after/wall_s` 공통 키(Task 8 `run_stage`가 주입·소비). `PlacementPool.add(prob, blk, ...)` 인자 순서는 Task 4 정의 = Task 5/7 사용처와 일치. Placements 튜플 인덱스(5=entry, 6=exit) 사용처(재타이밍 `pl[6]`, 폴리시 `[5:7]`, 풀 `(blk,bay,x,y,o,e,_x)`) 일치. `Budget(timelimit, safety)`·`construct(core, prob, rng)`·`to_operations(placements)`는 1주차 계약 문면과 일치. `solve(prob_info, timelimit, cfg=None, stats_out=None)`는 기존 2-인자 호출(제출 경로) 하위호환 — test_shell_phases가 회귀 가드. 확인.






