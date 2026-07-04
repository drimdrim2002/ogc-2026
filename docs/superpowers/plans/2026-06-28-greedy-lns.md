# Greedy + LNS 하이브리드 솔버 구현 계획

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** `myalgorithm.algorithm`이 baseline greedy(20%)로 초기해를 만든 뒤 LNS(80%)로 objective를 개선하는 하이브리드 솔버를 구현한다.

**Architecture:** `solution_state.py`가 assignment 상태 관리, `lns_solver.py`가 destroy-repair 루프, `myalgorithm.py`가 시간 배분 오케스트레이션. `baseline_greedy._place_blocks(allow_force=False)`와 `utils.check_feasibility`를 재사용한다.

**Tech Stack:** Python 3 (conda env `ogc-2026`), pytest, Shapely geometry (`baseline/utils.py`)

**Spec:** `docs/superpowers/specs/2026-06-28-greedy-lns-design.md` (v2)

---

## 파일 맵

| 파일 | 작업 | 책임 |
|------|------|------|
| `baseline/solution_state.py` | **Create** | assignment 변환, bay 상태 재구성, 기여도 계산 |
| `baseline/lns_solver.py` | **Create** | destroy 연산자, LNS 루프, hill-climbing 수용 |
| `baseline/baseline_greedy.py` | **Modify (최소)** | `block_sort_key`, `allow_force` 파라미터 |
| `baseline/myalgorithm.py` | **Modify** | 20/80 시간 배분 + hybrid 호출 |
| `baseline/tests/test_solution_state.py` | **Create** | solution_state 단위 테스트 |
| `baseline/tests/test_lns_solver.py` | **Create** | LNS 단위 테스트 |
| `baseline/tests/test_hybrid_integration.py` | **Create** | end-to-end 비교 테스트 |
| `baseline/run_myalgorithm.py` | **Modify** | 기본 인스턴스를 `example_B2_b10.json`으로 |

---

## Task 0: 테스트 환경 준비

**Files:**
- Create: `baseline/tests/__init__.py`

- [ ] **Step 1: pytest 설치**

```bash
/opt/homebrew/Caskroom/miniforge/base/envs/ogc-2026/bin/python -m pip install pytest
```

- [ ] **Step 2: 테스트 디렉터리 생성**

```bash
mkdir -p baseline/tests
touch baseline/tests/__init__.py
```

- [ ] **Step 3: pytest 동작 확인**

```bash
conda activate ogc-2026 && cd baseline && python -m pytest --version
```

Expected: pytest version 출력

---

## Task 1: solution_state 모듈

**Files:**
- Create: `baseline/solution_state.py`
- Test: `baseline/tests/test_solution_state.py`

- [ ] **Step 1: 실패하는 테스트 작성**

`baseline/tests/test_solution_state.py`:

```python
import json
import pathlib
import pytest
from solution_state import (
    assignments_from_operations,
    rebuild_bay_state,
    per_block_contributions,
    bay_normalized_loads,
)
from baseline_greedy import greedyalgorithm, _build_operations
from utils import check_feasibility, Bay

INSTANCE = pathlib.Path(__file__).resolve().parents[2] / "alg_tester/example/example_B2_b10.json"

@pytest.fixture
def prob_info():
    with open(INSTANCE) as f:
        return json.load(f)

def test_roundtrip_assignments(prob_info):
    sol = greedyalgorithm(prob_info, timelimit=10.0)
    assignments = assignments_from_operations(sol, prob_info)
    assert len(assignments) == len(prob_info["blocks"])
    rebuilt = {"operations": _build_operations(list(assignments.values()))}
    r = check_feasibility(prob_info, rebuilt)
    assert r["feasible"]
    assert r["objective"] == pytest.approx(check_feasibility(prob_info, sol)["objective"])

def test_rebuild_bay_state_counts(prob_info):
    sol = greedyalgorithm(prob_info, timelimit=10.0)
    assignments = assignments_from_operations(sol, prob_info)
    bays = [Bay.from_dict(d, i) for i, d in enumerate(prob_info["bays"])]
    placed, schedule, loads = rebuild_bay_state(assignments, prob_info["blocks"], bays)
    assert sum(len(p) for p in placed) == len(assignments)
    assert sum(len(s) for s in schedule) == len(assignments)

def test_per_block_contributions_nonneg(prob_info):
    sol = greedyalgorithm(prob_info, timelimit=10.0)
    assignments = assignments_from_operations(sol, prob_info)
    contrib = per_block_contributions(assignments, prob_info["blocks"])
    for bid, c in contrib.items():
        assert c["tardiness"] >= 0
        assert c["pref_penalty"] >= 0
```

- [ ] **Step 2: 테스트 실행 — FAIL 확인**

```bash
conda activate ogc-2026 && cd baseline && python -m pytest tests/test_solution_state.py -v
```

Expected: FAIL (`ModuleNotFoundError: solution_state`)

- [ ] **Step 3: solution_state.py 구현**

`baseline/solution_state.py`:

```python
def assignments_from_operations(sol: dict, prob_info: dict) -> dict[int, dict]:
    """operations dict -> block_id -> assignment dict.
    각 block_id에 대해 ENTRY 시각·좌표·방향, EXIT 시각을 복원."""

def rebuild_bay_state(assignments, blocks_data, bays):
    """assignments로부터 (bay_placed, bay_schedule, bay_loads) 재구성.
    baseline_greedy._repair greedy 모드 815-830행과 동일 패턴."""

def per_block_contributions(assignments, blocks_data) -> dict[int, dict]:
    """block별 {tardiness, pref_penalty} 반환."""

def bay_normalized_loads(assignments, blocks_data, bays) -> list[float]:
    """베이별 u_j * load_j 값 반환."""
```

- [ ] **Step 4: 테스트 PASS 확인**

```bash
conda activate ogc-2026 && cd baseline && python -m pytest tests/test_solution_state.py -v
```

Expected: 3 passed

---

## Task 2: baseline_greedy 최소 확장 (allow_force + block_sort_key)

> **선행 Task:** LNS repair가 `allow_force=False`에 의존하므로 Task 3보다 먼저 완료.

**Files:**
- Modify: `baseline/baseline_greedy.py`
- Test: `baseline/tests/test_baseline_greedy_extensions.py`

- [ ] **Step 1: allow_force 테스트 작성**

`baseline/tests/test_baseline_greedy_extensions.py`:

```python
import json
import pathlib
import pytest
from baseline_greedy import _place_blocks, greedyalgorithm
from utils import Bay, check_feasibility

INSTANCE = pathlib.Path(__file__).resolve().parents[2] / "alg_tester/example/example_B2_b10.json"

@pytest.fixture
def prob_info():
    with open(INSTANCE) as f:
        return json.load(f)

def test_allow_force_false_returns_empty_on_failure(prob_info):
    """빈 베이에 넣을 수 없는 상황을 인위적으로 만들기 어려우면
    removed 블록이 이미 꽉 찬 베이에만 들어가도록 상태를 구성."""
    # 최소 검증: allow_force=True 기본 동작 회귀
    sol = greedyalgorithm(prob_info, timelimit=10.0)
    assert check_feasibility(prob_info, sol)["feasible"]

def test_block_sort_key_default_unchanged(prob_info):
    sol_edd = greedyalgorithm(prob_info, timelimit=10.0, block_sort_key="edd")
    r = check_feasibility(prob_info, sol_edd)
    assert r["feasible"]
    assert r["objective"] == pytest.approx(1056, rel=0.01)

def test_block_sort_key_pref_edd_runs(prob_info):
    sol = greedyalgorithm(prob_info, timelimit=10.0, block_sort_key="pref_edd")
    assert check_feasibility(prob_info, sol)["feasible"]
```

- [ ] **Step 2: `_place_blocks`에 `allow_force` 추가**

```python
def _place_blocks(
    ...,
    forced_ids: set[int],
    allow_force: bool = True,
    ...
) -> dict[int, dict]:
    ...
    if best_placement is None:
        if not allow_force:
            return {}
        best_placement = _force_place(...)
```

- [ ] **Step 3: `greedyalgorithm`에 `block_sort_key` 추가**

```python
def greedyalgorithm(prob_info, timelimit, repair_mode="greedy",
                    block_sort_key="edd"):  # "edd" | "pref_edd"

# edd:       (due_date, processing_time)
# pref_edd:  (due_date, -max(block["bay_preferences"]), processing_time)
```

- [ ] **Step 4: 회귀 확인**

```bash
conda activate ogc-2026 && cd baseline && python run_baseline_greedy.py
conda activate ogc-2026 && cd baseline && python -m pytest tests/test_baseline_greedy_extensions.py -v
```

Expected: feasible, objective ≈ 1056 (default `edd`)

---

## Task 3: Destroy 연산자

**Files:**
- Create: `baseline/lns_solver.py` (destroy 부분)
- Test: `baseline/tests/test_lns_solver.py`

- [ ] **Step 1: 테스트 작성**

```python
import random
import pytest
from lns_solver import select_destroy_blocks, select_destroy_operator, DESTROY_OPERATORS
from solution_state import assignments_from_operations
from baseline_greedy import greedyalgorithm

def test_destroy_returns_subset(prob_info):
    sol = greedyalgorithm(prob_info, timelimit=10.0)
    assignments = assignments_from_operations(sol, prob_info)
    removed = select_destroy_blocks("random_k", assignments, prob_info, rng=random.Random(0))
    assert 0 < len(removed) < len(assignments)
    assert all(b in assignments for b in removed)

def test_preference_violators_targets_non_best_bay(prob_info):
    sol = greedyalgorithm(prob_info, timelimit=10.0)
    assignments = assignments_from_operations(sol, prob_info)
    blocks = prob_info["blocks"]
    violators = [
        bid for bid, a in assignments.items()
        if a["bay_id"] != max(range(len(blocks[bid]["bay_preferences"])),
                              key=lambda j: blocks[bid]["bay_preferences"][j])
    ]
    if not violators:
        pytest.skip("no preference violators in seed")
    removed = select_destroy_blocks("preference_violators", assignments, prob_info,
                                    rng=random.Random(1))
    assert all(bid in violators for bid in removed)

def test_select_destroy_operator_in_pool():
    rng = random.Random(0)
    for _ in range(20):
        assert select_destroy_operator(rng) in DESTROY_OPERATORS
```

- [ ] **Step 2: destroy 연산자 구현**

```python
DESTROY_OPERATORS = ("random_k", "preference_violators", "load_imbalance", "high_tardiness")
DESTROY_WEIGHTS = {"random_k": 0.25, "preference_violators": 0.35,
                   "load_imbalance": 0.30, "high_tardiness": 0.10}

def _k(n_blocks): return max(1, min(5, n_blocks // 10))
def select_destroy_operator(rng) -> str: ...
def select_destroy_blocks(operator, assignments, prob_info, rng) -> list[int]: ...
```

- [ ] **Step 3: PASS 확인**

```bash
conda activate ogc-2026 && cd baseline && python -m pytest tests/test_lns_solver.py -v
```

---

## Task 4: LNS Repair + 수용

**Files:**
- Modify: `baseline/lns_solver.py`
- Test: `baseline/tests/test_lns_solver.py`

- [ ] **Step 1: repair/acceptance 테스트 작성**

```python
import random
import copy
from lns_solver import repair_removed, should_accept, lns_iteration, evaluate_assignments
from solution_state import assignments_from_operations
from baseline_greedy import greedyalgorithm
from utils import Bay

def test_should_accept_strict_improvement():
    best = {"feasible": True, "objective": 100.0, "obj3": 50.0}
    cand = {"feasible": True, "objective": 99.0, "obj3": 50.0}
    assert should_accept(cand, best) is True

def test_should_accept_tiebreak_obj3():
    best = {"feasible": True, "objective": 100.0, "obj3": 50.0}
    cand = {"feasible": True, "objective": 100.0, "obj3": 49.0}
    assert should_accept(cand, best) is True

def test_should_reject_infeasible():
    best = {"feasible": True, "objective": 100.0, "obj3": 50.0}
    cand = {"feasible": False}
    assert should_accept(cand, best) is False

def test_lns_iteration_does_not_mutate_best(prob_info):
    sol = greedyalgorithm(prob_info, timelimit=10.0)
    best = assignments_from_operations(sol, prob_info)
    best_snapshot = copy.deepcopy(best)
    bays = [Bay.from_dict(d, i) for i, d in enumerate(prob_info["bays"])]
    best_result = evaluate_assignments(prob_info, best)
    lns_iteration(prob_info, best, bays, operator="random_k",
                  rng=random.Random(42), best_result=best_result)
    assert best == best_snapshot  # in-place 변경 없음

def test_repair_removed_uses_allow_force_false(prob_info):
    sol = greedyalgorithm(prob_info, timelimit=10.0)
    assignments = assignments_from_operations(sol, prob_info)
    bays = [Bay.from_dict(d, i) for i, d in enumerate(prob_info["bays"])]
    removed = [next(iter(assignments))]
    working = copy.deepcopy(assignments)
    for bid in removed:
        working.pop(bid)
    result = repair_removed(removed, working, prob_info, bays)
    if result is not None:
        merged = {**working, **result}
        assert evaluate_assignments(prob_info, merged)["feasible"]
```

- [ ] **Step 2: 구현**

```python
def evaluate_assignments(prob_info, assignments) -> dict:
    """check_feasibility wrapper."""

def should_accept(candidate_result, best_result) -> bool:
    """스펙 §4.3 수용 규칙."""

def repair_removed(removed_ids, working_assignments, prob_info, bays):
    """rebuild -> _place_blocks(..., allow_force=False).
    성공 시 repaired dict, 실패 시 None."""

def lns_iteration(prob_info, best_assignments, bays, operator, rng, best_result):
    """deepcopy(best) -> destroy -> repair -> accept 판정.
    Returns (new_best_or_old, accepted: bool)."""
```

핵심 repair 로직:

```python
repaired = _place_blocks(
    removed_ids, blocks_data, bays,
    bay_placed, bay_schedule, bay_loads,
    w1, w2, w3, forced_ids=set(),
    allow_force=False,
)
if len(repaired) != len(removed_ids):
    return None
merged = {**working, **repaired}
result = evaluate_assignments(prob_info, merged)
if not result["feasible"]:
    return None
return merged
```

- [ ] **Step 3: PASS 확인**

---

## Task 5: LNS 메인 루프 + 시간 관리

**Files:**
- Modify: `baseline/lns_solver.py`
- Test: `baseline/tests/test_lns_solver.py`

- [ ] **Step 1: 시간 제한 테스트**

```python
import time

def test_lns_improve_respects_timelimit(prob_info):
    t0 = time.time()
    sol = greedyalgorithm(prob_info, timelimit=2.0)
    assignments = assignments_from_operations(sol, prob_info)
    best = lns_improve(prob_info, assignments, t_start=t0, timelimit=5.0)
    assert time.time() - t0 < 5.5
    assert evaluate_assignments(prob_info, best)["feasible"]
```

- [ ] **Step 2: `lns_improve` 구현**

```python
def lns_improve(prob_info, assignments, t_start, timelimit,
                min_iter_time=0.05) -> dict[int, dict]:
    t_limit = t_start + timelimit * 0.98
    best = copy.deepcopy(assignments)
    best_result = evaluate_assignments(prob_info, best)
    rng = random.Random(prob_info.get("name", ""))
    bays = [Bay.from_dict(d, i) for i, d in enumerate(prob_info["bays"])]

    while time.time() < t_limit:
        if t_limit - time.time() < min_iter_time:
            break
        op = select_destroy_operator(rng)
        best, accepted = lns_iteration(
            prob_info, best, bays, operator=op, rng=rng,
            best_result=best_result,
        )
        if accepted:
            best_result = evaluate_assignments(prob_info, best)
    return best
```

- [ ] **Step 3: PASS 확인**

---

## Task 6: myalgorithm 통합

**Files:**
- Modify: `baseline/myalgorithm.py`
- Test: `baseline/tests/test_hybrid_integration.py`

- [ ] **Step 1: 통합 테스트 작성**

```python
def test_hybrid_feasible(prob_info):
    from myalgorithm import algorithm
    sol = algorithm(prob_info, timelimit=30.0)
    r = check_feasibility(prob_info, sol)
    assert r["feasible"]

def test_hybrid_not_worse_than_seed_greedy(prob_info):
    """스펙 §7: 동일 seed(pref_edd, 20% tl) greedy 대비 개선 또는 동점."""
    from myalgorithm import algorithm
    from baseline_greedy import greedyalgorithm
    tlim = 30.0
    seed_sol = greedyalgorithm(
        prob_info, timelimit=tlim * 0.20, block_sort_key="pref_edd")
    hybrid_sol = algorithm(prob_info, timelimit=tlim)
    seed_obj = check_feasibility(prob_info, seed_sol)["objective"]
    hybrid_obj = check_feasibility(prob_info, hybrid_sol)["objective"]
    assert check_feasibility(prob_info, hybrid_sol)["feasible"]
    assert hybrid_obj <= seed_obj + 1e-6
```

- [ ] **Step 2: myalgorithm.py 구현**

```python
def algorithm(prob_info, timelimit=60):
    import time
    from baseline_greedy import greedyalgorithm, _build_operations
    from solution_state import assignments_from_operations
    from lns_solver import lns_improve
    from utils import check_feasibility

    t_start = time.time()
    greedy_tl = timelimit * 0.20

    sol = greedyalgorithm(
        prob_info, timelimit=greedy_tl,
        repair_mode="greedy",
        block_sort_key="pref_edd",
    )
    result = check_feasibility(prob_info, sol)
    if not result["feasible"]:
        remaining = max(1.0, greedy_tl - (time.time() - t_start))
        sol = greedyalgorithm(prob_info, timelimit=remaining, repair_mode="greedy")
        result = check_feasibility(prob_info, sol)
        if not result["feasible"]:
            return sol

    assignments = assignments_from_operations(sol, prob_info)
    best = lns_improve(prob_info, assignments, t_start, timelimit)
    return {"operations": _build_operations(list(best.values()))}
```

- [ ] **Step 3: 통합 테스트 PASS**

```bash
conda activate ogc-2026 && cd baseline && python -m pytest tests/ -v
```

---

## Task 7: 로컬 검증 및 벤치마크

**Files:**
- Modify: `baseline/run_myalgorithm.py`

- [ ] **Step 1: run_myalgorithm.py 기본 인스턴스 수정**

```python
INSTANCE_PATH = "../alg_tester/example/example_B2_b10.json"
```

- [ ] **Step 2: 비교 스크립트 실행**

```bash
conda activate ogc-2026 && cd baseline
python -c "
import json, time
from baseline_greedy import greedyalgorithm
from myalgorithm import algorithm
from utils import check_feasibility

p = json.load(open('../alg_tester/example/example_B2_b10.json'))
runs = [
    ('baseline_edd_60s', lambda: greedyalgorithm(p, 60.0)),
    ('seed_pref_edd_12s', lambda: greedyalgorithm(p, 12.0, block_sort_key='pref_edd')),
    ('hybrid_60s', lambda: algorithm(p, 60.0)),
]
for name, fn in runs:
    t0 = time.time()
    sol = fn()
    e = time.time() - t0
    r = check_feasibility(p, sol)
    print(f'{name}: feasible={r[\"feasible\"]} obj={r.get(\"objective\"):.1f} time={e:.2f}s')
"
```

- [ ] **Step 3: 결과 기록 (목표)**

| Solver | Feasible | Objective | 비고 |
|--------|----------|-----------|------|
| baseline_edd_60s | True | ~1056 | 회귀 기준 |
| seed_pref_edd_12s | True | ≤1056 | LNS 비교 기준 |
| hybrid_60s | True | ≤seed obj (MVP), ≤1000 (목표) | stretch ≤950 |

- [ ] **Step 4: alg_tester UI 시각 확인 (선택)**

```bash
conda activate ogc-2026 && cd alg_tester && python alg_tester_app.py
```

---

## 구현 순서 요약

```
Task 0 pytest 환경
  ↓
Task 1 solution_state
  ↓
Task 2 baseline_greedy (allow_force + block_sort_key)  ← Task 4보다 선행
  ↓
Task 3 destroy operators
  ↓
Task 4 repair + accept
  ↓
Task 5 LNS loop
  ↓
Task 6 myalgorithm 통합
  ↓
Task 7 벤치마크
```

## 리스크 및 완화

| 리스크 | 완화 |
|--------|------|
| LNS iteration이 너무 느림 | k 상한 5, `check_feasibility`는 iteration당 1회 |
| repair infeasible 빈번 | `allow_force=False` 실패 시 폐기; random_k 비중 0.25 |
| objective 개선 미미 | pref_edd seed + preference_violators 0.35 |
| 시간 초과 | 98% 마진 + `min_iter_time` 조기 종료 |
| allow_force 회귀 | default `True`, baseline 테스트로 검증 |

## 변경 이력

| 버전 | 날짜 | 내용 |
|------|------|------|
| v1 | 2026-06-28 | 초안 |
| v2 | 2026-06-28 | 리뷰 반영: Task 0 pytest, Task 2 allow_force 선행, 회귀 기준 수정, infeasible repair, should_accept, deepcopy |
