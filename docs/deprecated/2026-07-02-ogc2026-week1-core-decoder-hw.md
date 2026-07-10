# OGC 2026 - 1주차: C++ 기하 코어 v0 + 구성 디코더 + 견고성 뼈대 구현 계획

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 전 40개 인스턴스에서 baseline보다 좋은 feasible 해를 내는 "C++ 기하 코어(ogc_core) + ATC/BLF 구성 디코더 + 3단 폴백 셸"을 완성한다.

**Architecture:** 좌표를 x10^4 스케일 정수로 변환해 Clipper2 정수 클리핑으로 정확한 충돌 판정을 하고, shape쌍별 "충돌 오프셋 비트맵"(static/entry)을 lazy 캐시. 배치 상태에서 페어와이즈 비트맵을 OR 해 free-position 마스크를 만들어 us급 배치 검사를 달성한다. Python 층은 pybind11로 이 코어를 호출하는 디코더(ATC 우선순위 + time-first BLF)와 견고성 셸(시간예산, 폴백, utils 최종검증)을 얹는다.

**Tech Stack:** C++20, CMake, pybind11, Clipper2(vendored, BSL-1.0), nlohmann/json(vendored, MIT), Python 3.12, shapely 2.1.2 (oracle/폴백 전용), pytest.

## Global Constraints (설계서 1절, 5절에서 복사)

- 서버: Ubuntu 24.04, AMD Threadripper PRO 9955WX, **4코어/16GB/인터넷 없음**, firejail+cpulimit, 시간제한 수 분~30분(비공개).
- Python 3.12 고정 (공식 env). `utils.py` 수정 금지 - 최종 검증 oracle.
- 제출 zip: 루트에 `myalgorithm.py`, <=15MB, 상대경로만, `.dll/.exe/.vb` 류 금지.
- 해 형식: x, y는 정수, 시간은 정수 일, 같은 날 EXIT 전량이 ENTRY보다 먼저.
- 빌드: `-O3 -march=x86-64-v3 -static-libstdc++ -static-libgcc`, CPython 3.12 대상.
- 코어 판정은 utils(shapely, `area>0`)와 갈릴 수 있는 경계에서 **미세하게 보수적**이어야 함.
- Gurobi 사용 보류(라이선스 확인 전). MIP 계열은 CP-SAT 사용.

## 파일 구조 (이번 주 생성)

```
core/
  CMakeLists.txt
  third_party/clipper2/...        # vendored
  third_party/nlohmann/json.hpp # vendored
  src/geom.hpp,geom.cpp         # 스케일 정수 폴리곤, overlap predicate
  src/instance.hpp,instance.cpp # 인스턴스 파싱, ShapeTable
  src/nfp.hpp,nfp.cpp           # 충돌 오프셋 비트맵 캐시
  src/state.hpp,state.cpp       # 배치 상태, 시간 관계, free-mask, objective
  src/bindings.cpp              # pybind11 모듈 ogc_core
solver/
  __init__.py
  core_iface.py                 # ogc_core 로드 + 순수 Python 폴백 선택
  fallback_core.py              # shapely 기반 동일-API 폴백 (완전 기능, 저속)
  priority.py                   # ATC + biased randomization
  decoder.py                    # time-first BLF 구성
  serialize.py                  # placements -> operations dict
  budget.py                     # 시간예산 관리자
  shell.py                      # solve(prob_info, timelimit) - 멀티스타트+폴백+최종검증
submission/
  myalgorithm.py                # solver.shell.solve 위임 (제출 스테이징)
experiments/
  run_bench.py                  # 40 인스턴스 벤치, 결과 JSON
tests/
  conftest.py                   # 인스턴스 로더 fixture, baseline utils import 경로
  test_geom_fuzz.py             # C++ overlap vs shapely oracle
  test_nfp.py                   # 비트맵 vs 전수 shapely 대조
  test_state_oracle.py          # required-mask 의미론 vs utils 5단계 oracle
  test_api.py                   # place/check/earliest/free_positions
  test_objective.py             # objective vs utils
  test_serialize.py             # Stage5 통과 직렬화
  test_decoder.py, test_shell.py, test_fallback_parity.py
```

## 인터페이스 계약 (2~4주차 계획이 의존 - 변경 시 전 주차 계획 동기화 필수)

`ogc_core.Core` (pybind11, 모든 좌표는 **비스케일 정수**(원래 단위), 시간은 정수 일):

```python
Core(instance_json: str)                       # json.dumps(prob_info)로 생성
.n_blocks -> int ; .n_bays -> int
.processing(block) -> int ; .release(block) -> int ; .due(block) -> int
.num_orients(block) -> int

# 상태 조작 (feasible할 때만 True 반환하며 반영)
.place(block, bay, x, y, orient, entry, exit) -> bool
.remove(block) -> None
.clear() -> None
.get_placements() -> list[tuple[block,bay,x,y,orient,entry,exit]]
.load_placements(list[tuple]) -> int           # 순서대로 place, 성공 개수 반환

# 조회 (상태 불변)
.check_place(block, bay, x, y, orient, entry, exit) -> bool
.free_positions(block, bay, orient, entry, exit) -> list[tuple[x,y]]
.earliest_feasible(block, bay, orient, t_min, t_max) -> tuple[entry,x,y] | None
.objective() -> tuple[Z1, Z2, Z3]              # 현재 배치된 블록 기준
.pair_bits_at_current() -> list[tuple[i,j,static_hit,entry_ij,entry_ji]]
                                               # 같은 bay에 배치된 쌍의 현재 오프셋 비트
                                               # (3주차 CP-SAT 재타이밍 입력)
.verify_full() -> list[str]                    # utils 1~5단계 의미론 자체 검증, 빈 리스트=OK
```

`solver` 계약:
- `core_iface.make_core(prob_info) -> CoreLike` - ogc_core 실패 시 fallback_core 반환. 두 구현은 위 API 완전 동일.
- `priority.atc_order(prob, t0, kappa, rng, bias_p) -> list[block]`
- `decoder.construct(core, prob, rng, cfg) -> Placements | None` (`Placements = list[tuple]`, get_placements 형식)
- `serialize.to_operations(placements) -> dict` - 제출 형식
- `budget.Budget(timelimit, safety=0.08)`: `.remain()`, `.phase(name, frac)`, `.expired()`
- `shell.solve(prob_info, timelimit) -> dict` - myalgorithm의 유일한 진입점

**같은 날(same-day) 순서 규약 (v0 고정)**: 같은 날 같은 bay의 EXIT들은 block id 오름차순, ENTRY들도 block id 오름차순으로 수행, 직렬화한다. 코어의 시간관계 판정도 동일 규약을 가정한다 (아래 Task 5 표).

### 계약 부록 - 주차 간 조정 확정 (2026-07-03, 2~4주차 계획 문면보다 우선)

2~4주차 계획의 "계약 변경 요청"을 상호 대조해 아래와 같이 확정한다. **각 주차 계획 본문과 이 부록이 다르면 부록을 따른다.**

1. **`shell.improve` 최종 시그니처** (2주차 확정): `improve(core, incumbent, budget, *, prob, rng, cfg=None, trace=None, stats=None)`. 1주차 Task 13은 pass-through 자리만 만든다. 3주차 parallel.py의 "rng kwarg 요청"은 이 시그니처로 이미 충족.
2. **`shell.solve` 시그니처 진화**: 제출 경로는 항상 `solve(prob_info, timelimit)` 위치 인자 2개(불변). 2주차가 `*, alns_cfg=None, report=None` 추가 -> 3주차는 자체 명명(`cfg`/`stats_out`) 대신 **`mh_cfg=None`을 추가하고 `report`를 재사용**한다(치환 지시) -> 4주차 T1이 `solver/config.py::SolverConfig`로 통합(`solve(prob_info, timelimit, *, config=None, report=None)`), alns_cfg/mh_cfg는 SolverConfig의 하위 필드로 흡수.
3. **ALNS 설정 클래스 명칭**: 2주차 정의 `AlnsConfig`(필드: acceptance, rrt_start_pct, sa_start_pct, disable_ops, max_iters)가 정본. 4주차의 `AlnsCfg.max_iters` 추가 요청은 **이미 존재하므로 소멸** - 4주차 문면의 `AlnsCfg`는 `AlnsConfig`로 읽는다.
4. **벤치 토큰 표준**: ALNS 연산자 ablation은 `--disable-op {random,worst,shaw,time_slice,spatial_column,blocking_set,greedy_noise,regret2,regret3,most_constrained}` (2주차). 매트휴리스틱 스테이지는 `--disable {retiming,pool,polish}` + env `OGC_MH_DISABLE`(3주차). 4주차 문면의 `mh_retime/mh_pool/z2z3` 토큰은 `retiming/pool/polish`로 치환.
5. **조건부 Core API 사전 승인** (발동 조건 충족 시에만 구현, 미충족 시 구현 금지):
   - `Core.pair_conflicts(tuples: list[7-tuple]) -> list[(i,j)]` - 3주차 배치풀 충돌 판정이 2.0s 초과 시 (시그니처는 3주차 계획 계약 변경 요청 1 그대로).
   - `Core.set_nfp_cache_limit(max_pairs: int)` + `Core.nfp_cache_info() -> dict` - 4주차 메모리 가드에서 peak RSS 14GB 초과 시.
6. **테스트 전용 Core 멤버 목록** (공개 계약 아님, 이름 고정): `_overlap`, `_static_bit`, `_entry_bit` (1주차), `_nfp_bitmap(bi,oi,bj,oj,kind,method)` (2주차).
7. **환경변수 레지스트리** (전부 opt-in, 미설정 시 서버 기본 거동 불변): `OGC_FORCE_FALLBACK`(1주차), `OGC_MH_DISABLE`(3주차), `OGC_CONFIG`/`OGC_SEED`/`OGC_DET`/`OGC_TRACE_FILE`(4주차).

---

### Task 1: 스캐폴딩 + pybind11 빌드 파이프라인

**Files:** Create: `core/CMakeLists.txt`, `core/src/bindings.cpp`, `solver/__init__.py`, `tests/conftest.py`, `pyproject.toml`(pytest 설정), `Makefile`
**Interfaces:** Produces: `import ogc_core; ogc_core.__version__ == "0.1.0"`, `make core` 빌드 명령.

- [ ] **Step 1: 의존성 설치, 확인**

```bash
conda run -n ogc2026 pip install pybind11 ortools==9.15.6755 pytest
conda run -n ogc2026 python -c "import pybind11, shapely; print('ok')"
cmake --version && g++ --version   # gcc 13+, cmake 3.22+ 확인. 없으면: sudo apt install g++ cmake
```

- [ ] **Step 2: 빈 모듈의 실패 테스트 작성** - `tests/test_api.py`

```python
def test_module_imports():
    import ogc_core
    assert ogc_core.__version__ == "0.1.0"
```

`conda run -n ogc2026 python -m pytest tests/test_api.py -v` -> FAIL (`ModuleNotFoundError`)

- [ ] **Step 3: CMake + 최소 바인딩 구현**

`core/src/bindings.cpp`:
```cpp
#include <pybind11/pybind11.h>
#include <pybind11/stl.h>
namespace py = pybind11;
PYBIND11_MODULE(ogc_core, m) { m.attr("__version__") = "0.1.0"; }
```

`core/CMakeLists.txt`:
```cmake
cmake_minimum_required(VERSION 3.22)
project(ogc_core CXX)
set(CMAKE_CXX_STANDARD 20)
set(CMAKE_CXX_FLAGS_RELEASE "-O3 -march=x86-64-v3")
set(CMAKE_SHARED_LINKER_FLAGS "-static-libstdc++ -static-libgcc")
find_package(pybind11 REQUIRED)   # cmake -Dpybind11_DIR=$(python -m pybind11 --cmakedir)
file(GLOB SRC src/*.cpp)
pybind11_add_module(ogc_core ${SRC})
install(TARGETS ogc_core DESTINATION ${CMAKE_SOURCE_DIR}/../solver)
```

`Makefile` (repo의 ogc2026 루트):
```make
PY := conda run -n ogc2026 python
core:
	cmake -S core -B core/build -DCMAKE_BUILD_TYPE=Release \
	  -Dpybind11_DIR=$$($(PY) -m pybind11 --cmakedir)
	cmake --build core/build -j && cmake --install core/build
test: core
	$(PY) -m pytest tests -x -q
```

- [ ] **Step 4: 빌드, 테스트 통과 확인** - `make core && make test` -> PASS. `.so`는 `solver/`에 설치되어 `sys.path` 조작 없이 import.
- [ ] **Step 5: Commit** - `git add ... && git commit -m "build(ogc2026): pybind11 코어 빌드 파이프라인"`

### Task 2: 인스턴스 파싱 + ShapeTable (스케일 정수 변환)

**Files:** Create: `core/src/instance.hpp`, `core/src/instance.cpp`; vendor `core/third_party/nlohmann/json.hpp`. Modify: `core/src/bindings.cpp`. Test: `tests/test_api.py`
**Interfaces:** Produces: `Core(json_str)`, `.n_blocks/.n_bays/.processing/.release/.due/.num_orients`; C++ 내부 `struct ShapeTable { LayerPoly layers[shape_id][k] }`, `shape_id = shape_index(block, orient)`. **SCALE = 10000** (전 코어 공통 상수).

- [ ] **Step 1: 실패 테스트** - `tests/test_api.py`에 추가:

```python
import json, ogc_core
def test_core_loads_instance(prob1):   # prob1 = conftest fixture (prob_1.json dict)
    core = ogc_core.Core(json.dumps(prob1))
    assert core.n_blocks == 100 and core.n_bays == 2
    assert core.processing(0) == prob1["blocks"][0]["processing_time"]
    assert core.num_orients(0) == len(prob1["blocks"][0]["shape"])
```

`tests/conftest.py`:
```python
import json, sys, pathlib, pytest
ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "solver")); sys.path.insert(0, str(ROOT / "baseline"))
def _load(name):
    for sub in ("training_instances", "train-set2"):
        p = ROOT / "data" / sub / "train" / f"{name}.json"
        if p.exists(): return json.loads(p.read_text())
    raise FileNotFoundError(name)
@pytest.fixture(scope="session")
def prob1(): return _load("prob_1")
@pytest.fixture(scope="session")
def all_probs(): return {f"prob_{i}": _load(f"prob_{i}") for i in range(1, 41)}
```

- [ ] **Step 2: 파서 구현.** 좌표 float -> `llround(v * SCALE)` (int64). 문제 좌표는 <=4자리 소수라 이 변환은 **정확**. 각 (block, orient)를 `shape_id`로 부여, 레이어별 정점 배열과 통합 bbox, 기준점(레이어0 첫 정점)이 (0,0)이 되도록 이미 JSON이 보장 - 검증 assert 포함. release/due/processing/workload/bay_preferences/bay W, H (SCALE 곱한 값)도 보관.
- [ ] **Step 3: 테스트 통과 확인** - `make test` -> PASS.
- [ ] **Step 4: Commit.**

### Task 3: 정확 overlap predicate (Clipper2) + shapely fuzz 대조

**Files:** vendor `core/third_party/clipper2/` (github.com/AngusJohnson/Clipper2 `CPP/Clipper2Lib`의 core/engine/minkowski 소스만, BSL-1.0 라이선스 파일 포함). Create: `core/src/geom.hpp`, `core/src/geom.cpp`. Test: `tests/test_geom_fuzz.py`
**Interfaces:** Produces (C++ 내부): `bool overlap_pos_area(const Path64& a, const Path64& b)` - 교차 면적>0이면 true, 경계 접촉만이면 false. 바인딩(테스트 전용): `ogc_core._overlap(list[[x,y]], list[[x,y]], scale_already=False) -> bool`.

- [ ] **Step 1: 실패 테스트** - `tests/test_geom_fuzz.py`:

```python
import random, json, ogc_core
from shapely.geometry import Polygon

def _shapely_overlap(a, b):
    pa, pb = Polygon(a), Polygon(b)
    if not pa.is_valid: pa = pa.buffer(0)
    if not pb.is_valid: pb = pb.buffer(0)
    inter = pa.intersection(pb)
    return (not inter.is_empty) and inter.area > 0

def test_fuzz_overlap_vs_shapely(all_probs):
    rng = random.Random(42)
    shapes = [l for p in all_probs.values() for b in p["blocks"]
              for s in b["shape"] for l in s["layers"]]
    disagreements = 0
    for _ in range(20000):
        a = rng.choice(shapes); b = rng.choice(shapes)
        dx, dy = rng.randint(-25, 25), rng.randint(-25, 25)
        bt = [[x + dx, y + dy] for x, y in b]
        if ogc_core._overlap(a, bt) != _shapely_overlap(a, bt):
            disagreements += 1
    assert disagreements == 0

def test_touching_edge_is_not_overlap():
    sq = [[0,0],[10,0],[10,10],[0,10]]
    sq_right = [[10,0],[20,0],[20,10],[10,10]]     # 변 공유 = 접촉 허용
    assert ogc_core._overlap(sq, sq_right) is False
    assert ogc_core._overlap(sq, [[9,0],[19,0],[19,10],[9,10]]) is True
```

- [ ] **Step 2: 구현** - `geom.cpp`:

```cpp
#include "clipper2/clipper.h"
using namespace Clipper2Lib;
bool overlap_pos_area(const Path64& a, const Path64& b) {
    Paths64 sol = Intersect(Paths64{a}, Paths64{b}, FillRule::NonZero);
    for (const auto& p : sol) if (std::llabs(Area(p)) > 0) return true;
    return false;
}
```
정수 좌표라 `Area`가 정확(2*면적이 정수). 접촉-only면 교집합이 퇴화(빈/선분)라 면적 0. 자기교차 입력은 NonZero 규칙으로 shapely `buffer(0)`과 동일 의미.

- [ ] **Step 3: fuzz 실행** - 20,000케이스 disagreement 0 확인. 불일치 발생 시 해당 케이스를 고정 회귀 테스트로 추가하고 원인(부동소수점 vs 정수 스케일 순서)을 해소할 때까지 진행 금지.
- [ ] **Step 4: Commit.**

### Task 4: 충돌 오프셋 비트맵(NFP) 캐시

**Files:** Create: `core/src/nfp.hpp`, `core/src/nfp.cpp`. Test: `tests/test_nfp.py`
**Interfaces:** Produces (C++ 내부):

```cpp
struct OffsetBitmap { int dx_lo, dy_lo, w, h; std::vector<uint64_t> bits;
                      bool test(int dx, int dy) const; };
// key = shape_id_a * n_shapes + shape_id_b (ordered)
const OffsetBitmap& static_bm(int sa, int sb);   // union_k  layer_k(a) x layer_k(b)
const OffsetBitmap& entry_bm (int sa, int sb);   // union_{j>=k} layer_k(a) x layer_j(b)
```
의미: `test(dx,dy)==true` <=> shape b를 shape a 기준 상대 오프셋 (dx,dy)(정수, 비스케일)에 두면 해당 제약 위반. 윈도 밖 오프셋은 false. lazy 계산 + 해시맵 캐시(스레드 안전은 이번 주 불필요 - 단일 스레드).
테스트 전용 바인딩: `Core._static_bit(bi, oi, bj, oj, dx, dy) -> bool`, `Core._entry_bit(...) -> bool`.

- [ ] **Step 1: 실패 테스트** - `tests/test_nfp.py`:

```python
import json, random, ogc_core
from shapely.geometry import Polygon

def _lay(block, o):   # 기준점(레이어0 첫 정점) 원점 정렬된 레이어 목록
    ref = block["shape"][o]["layers"][0][0]
    return [[[x - ref[0], y - ref[1]] for x, y in l] for l in block["shape"][o]["layers"]]

def _sh_overlap(la, lb, dx, dy):
    pa, pb = Polygon(la), Polygon([[x + dx, y + dy] for x, y in lb])
    if not pa.is_valid: pa = pa.buffer(0)
    if not pb.is_valid: pb = pb.buffer(0)
    i = pa.intersection(pb)
    return (not i.is_empty) and i.area > 0

def test_bitmaps_match_shapely_exhaustive(prob1):
    rng = random.Random(3); core = ogc_core.Core(json.dumps(prob1))
    for _ in range(30):
        bi, bj = rng.randrange(100), rng.randrange(100)
        oi = rng.randrange(core.num_orients(bi)); oj = rng.randrange(core.num_orients(bj))
        A, B = _lay(prob1["blocks"][bi], oi), _lay(prob1["blocks"][bj], oj)
        for dx in range(-25, 26):
            for dy in range(-25, 26):
                st = any(_sh_overlap(A[k], B[k], dx, dy) for k in range(min(len(A), len(B))))
                en = any(_sh_overlap(A[k], B[j], dx, dy)
                         for k in range(len(A)) for j in range(k, len(B)))
                assert core._static_bit(bi, oi, bj, oj, dx, dy) == st
                assert core._entry_bit(bi, oi, bj, oj, dx, dy) == en
```
(느리면 쌍 수를 10으로 줄이되 dx, dy 전수는 유지 - 윈도 경계 오차를 잡는 것이 목적.)
- [ ] **Step 2: 구현** - 윈도: `dx in [bbox_a.min_x - bbox_b.max_x - 1, bbox_a.max_x - bbox_b.min_x + 1]`(스케일에서 계산 후 `floor/ceil / SCALE`, +/-1 여유), dy 동일. 각 정수 오프셋에서 `overlap_pos_area` per 레이어 조합, OR 집계. v0는 오프셋별 정확 테스트(쌍당 ~0.3-2ms) - **Minkowski 가속은 2주차 과제로 명시적 이연**.
- [ ] **Step 3: 테스트 통과 + 캐시 적중 시 재계산 없음 확인**(호출 카운터 assert).
- [ ] **Step 4: Commit.**

### Task 5: 시간 관계 -> 필요 비트맵 판정 (utils 의미론의 심장)

**Files:** Create: `core/src/state.hpp`, `core/src/state.cpp` (관계 판정 부분). Test: `tests/test_state_oracle.py`
**Interfaces:** Produces (C++ 내부): `RequiredBits required(const Placed& A, const Placed& B)` - 아래 표 구현. 이후 모든 feasibility는 이 함수를 유일 경로로 사용.

**판정 표** (utils Stage2/3/5 + 같은 날 id-오름차순 규약에서 유도; `[e,x)` 반개구간, 모두 정수 일):

| 조건 (A 관점) | 필요 비트맵 |
|---|---|
| 구간 겹침 없음 (`eA >= xB or eB >= xA`) | 없음 |
| 겹침 | `static(A,B)` 항상 |
| A의 ENTRY 시점에 B 존재: `eB < eA < xB`, 또는 같은 날 진입 `eA == eB`이고 `idA > idB` | `entry(A,B)` |
| A의 EXIT 시점에 B 존재: `eB < xA < xB`, 또는 같은 날 퇴출 `xA == xB`이고 `idA < idB` | `entry(A,B)` (exit는 동일 수식) |
| 대칭으로 B의 이벤트가 A 존재 중이면 | `entry(B,A)` |

주의 케이스: A EXIT일 == B ENTRY일 (`xA == eB`) -> EXIT 먼저 규칙으로 **제약 없음**(구간도 안 겹침). `eA == xB`도 동일.

- [ ] **Step 1: oracle 실패 테스트** - `tests/test_state_oracle.py`:

```python
import json, random, ogc_core
from utils import check_feasibility   # baseline/utils.py (conftest가 경로 주입)

def _mk_solution(placements):
    ops = {}
    for (blk, bay, x, y, o, e, xt) in placements:
        ops.setdefault(str(e), []).append(
            {"type":"ENTRY","block_id":blk,"bay_id":bay,"x":x,"y":y,"orient_idx":o})
        ops.setdefault(str(xt), []).append({"type":"EXIT","block_id":blk,"bay_id":bay})
    for t in ops:  # EXIT 먼저 + block id 오름차순 (same-day 규약)
        ops[t].sort(key=lambda op: (op["type"] != "EXIT", op["block_id"]))
    return {"operations": ops}

def test_pairwise_semantics_vs_utils(prob1):
    """무작위 2-블록 배치 2000개: 코어 check_place 판정 == utils 판정(해당 블록쌍만 담은 부분 문제)"""
    rng = random.Random(7)
    sub = {**prob1, "blocks": prob1["blocks"][:2]}   # 블록 0,1만 남긴 부분 인스턴스
    core = ogc_core.Core(json.dumps(sub))
    mism = 0
    for _ in range(2000):
        core.clear()
        pl = []
        for blk in (0, 1):
            o = rng.randrange(core.num_orients(blk))
            e = rng.randint(0, 20); xt = e + max(1, core.processing(blk))
            pl.append((blk, 0, rng.randint(0, 40), rng.randint(0, 15), o, e, xt))
        ours = core.load_placements(pl) == 2
        res = check_feasibility(sub, _mk_solution(pl))
        theirs = res["feasible"]
        if ours != theirs:
            if ours and not theirs: mism += 1          # 우리가 허용한 걸 utils가 거부 = 치명
            # utils 허용 / 우리 거부 = 보수적 -> 카운트만
    assert mism == 0
```

- [ ] **Step 2: 구현** - `Placed{block, shape_id, bay, x, y, entry, exit}`; `required()`는 표 그대로; `check_pair(A,B)` = 필요한 비트맵들에 대해 `test(xB-xA, yB-yA)` 전부 false여야 통과. bay 경계 검사: 스케일 bbox가 `[0, W*SCALE] x [0, H*SCALE]` 내부(<=). release/processing 검사 포함(`entry >= R`, `exit-entry >= P`).
- [ ] **Step 3: oracle 테스트 통과** - "우리 허용 and utils 거부" 0건 필수. "utils 허용 and 우리 거부"(보수)는 비율 출력해 1% 미만인지 확인 - 초과하면 SCALE 경계 처리 버그 탐색.
- [ ] **Step 4: 3-블록 same-day 시나리오 500개 추가** (ENTRY 2개 같은 날 + EXIT 교차) 동일 oracle 비교. utils Stage5가 최종 심판.
- [ ] **Step 5: Commit.**

### Task 6: 배치 상태 + free-position 마스크 + 공개 API

**Files:** Modify: `core/src/state.cpp`, `core/src/bindings.cpp`. Test: `tests/test_api.py`
**Interfaces:** Produces: 계약의 `.place/.remove/.clear/.check_place/.free_positions/.earliest_feasible/.get_placements/.load_placements` 전부.

- [ ] **Step 1: 실패 테스트** - 대표 시나리오 assert:

```python
def test_place_and_free_positions(prob1):
    core = ogc_core.Core(json.dumps(prob1))
    assert core.place(0, 1, 5, 1, 0, 0, 10)        # block0: bay1 선호(pref 100)
    assert not core.check_place(1, 1, 5, 1, 0, 0, 9) is None  # bool 반환 확인
    free = core.free_positions(1, 1, 0, 0, 9)
    for (x, y) in free[:50]:
        assert core.check_place(1, 1, x, y, 0, 0, 9)
    e = core.earliest_feasible(1, 1, 0, 0, 60)
    assert e is None or len(e) == 3

def test_free_positions_matches_bruteforce(prob1):
    """free_positions == 전 좌표 check_place 전수 스캔 (블록 3개 배치 상태에서)"""
```

- [ ] **Step 2: 구현.** bay별 `vector<Placed>` + 구간 겹침 후보 필터(정렬된 entry 리스트, n<=100이라 선형도 충분). `free_positions`: 후보 x-range * y-range 비트보드(행당 uint64 배열)에서 시작해, 관련 블록마다 필요한 OffsetBitmap을 상대 위치로 시프트-OR -> 남은 0비트가 free. `earliest_feasible`: t = max(t_min, R)부터 t_max까지 free_positions 첫 비트 검색, 채택 위치는 (y, x) 최소(bottom-left).
- [ ] **Step 3: 브루트포스 대조 + 통과.**
- [ ] **Step 4: 마이크로벤치** - `tests/test_api.py::test_perf_smoke`: 50블록 배치 상태에서 `check_place` 100k회 wall time < 1.0s (개당 <10us), `free_positions` 1k회 < 1.0s. 미달 시 프로파일 후 비트보드 경로 점검 (목표 미달 상태로 커밋 금지).
- [ ] **Step 5: Commit.**

### Task 7: objective + verify_full + pair_bits_at_current

**Files:** Modify: `core/src/state.cpp`, `core/src/bindings.cpp`. Test: `tests/test_objective.py`
**Interfaces:** Produces: `.objective()`, `.verify_full()`, `.pair_bits_at_current()` (계약 참조).

- [ ] **Step 1: 실패 테스트** - baseline greedy로 prob_1 해를 만들어 `utils.check_feasibility`의 (obj1,obj2,obj3)와 코어 `.objective()` 일치(+/-1e-6) assert. `verify_full()`은 feasible 해에서 `[]`, 고의로 겹친 배치에서 비어있지 않음 assert.
- [ ] **Step 2: 구현** - utils 수식 그대로: Z1=sum(max(0, exit-due)), Z2=max pair |u*load - u*load| (u=평균면적/면적; **floor 없음 — utils는 float 그대로 계산**), Z3=sum(Smax-S). `verify_full`은 배치 전체를 required() 경로로 재검 + Stage1 항목(전 블록 배치 여부는 호출자 책임이므로 배치된 것만).
- [ ] **Step 3: 통과 확인 -> Commit.**

### Task 8: 순수 Python 폴백 코어 + 동등성 테스트

**Files:** Create: `solver/core_iface.py`, `solver/fallback_core.py`. Test: `tests/test_fallback_parity.py`
**Interfaces:** Produces: `core_iface.make_core(prob_info) -> CoreLike` (환경변수 `OGC_FORCE_FALLBACK=1`로 강제 폴백).

- [ ] **Step 1: 실패 테스트** - 동일 무작위 배치 시퀀스 300개에 대해 ogc_core와 fallback의 `place` 성공/실패열과 `objective()` 일치 assert.
- [ ] **Step 2: 구현** - fallback은 shapely로 Task 5의 표를 그대로 구현(utils의 check_entry/check_exit/check_collisions 재사용 가능 - baseline/utils.py를 import해서 호출). 속도 무관, 의미 동일이 목표.
- [ ] **Step 3: 통과 -> Commit.**

### Task 9: 직렬화 (operations dict)

**Files:** Create: `solver/serialize.py`. Test: `tests/test_serialize.py`
**Interfaces:** Produces: `to_operations(placements) -> dict` - same-day 규약(EXIT 전량 먼저, 각각 id 오름차순) 준수.

- [ ] **Step 1: 실패 테스트** - 임의 feasible placements(코어로 생성)를 직렬화해 `utils.check_feasibility(prob, sol)["feasible"] is True` assert; 같은 날 EXIT/ENTRY 혼합 케이스 포함.
- [ ] **Step 2: 구현** (Task 5 테스트의 `_mk_solution`을 정식 모듈로 이동, x, y `int()` 강제).
- [ ] **Step 3: 통과 -> Commit.**

### Task 10: 최후 폴백 - 순차 단독 배치 솔버

**Files:** Create: `solver/trivial.py` (`solve_trivial(core, prob) -> Placements`). Test: `tests/test_shell.py`
**Interfaces:** Produces: 항상 feasible한 placements (품질 무관, 최후 보루).

- [ ] **Step 1: 실패 테스트** - 40개 인스턴스 전부: `solve_trivial` 결과 -> serialize -> utils feasible assert (전 인스턴스 합계 수 분 내).
- [ ] **Step 2: 구현** - bay마다 시간 직렬화: 블록을 release 오름차순으로 선호 bay에 배정, `entry = max(R, 그 bay 직전 블록 exit)`, 위치는 빈 bay 기준 `free_positions` 첫 후보(경계만 검사되는 상태), `exit = entry + P`. 같은 bay에서 시간이 절대 겹치지 않으므로 페어 제약 자동 충족. 어떤 bay에도 못 들어가는 블록 발견 시 예외(로드 시 사전 검증).
- [ ] **Step 3: 통과 -> Commit.**

### Task 11: ATC 우선순위 + biased randomization

**Files:** Create: `solver/priority.py`. Test: `tests/test_decoder.py`
**Interfaces:** Produces: `atc_order(prob, t0=0.0, kappa=3.0, rng=None, bias_p=0.25) -> list[int]` - rng=None이면 결정적 ATC 순, rng 지정 시 기하분포 편향 샘플링.

- [ ] **Step 1: 실패 테스트**

```python
from solver.priority import atc_order
def test_atc_deterministic_prefers_urgent(prob1):
    order = atc_order(prob1)
    zero_slack = [i for i, b in enumerate(prob1["blocks"])
                  if b["due_date"] - b["release_time"] - b["processing_time"] == 0]
    assert set(order[:len(zero_slack)//2]) & set(zero_slack)  # 급한 블록이 앞쪽에 몰림
    assert sorted(order) == list(range(100))
def test_biased_randomization_varies():
    import random
    o1 = atc_order(prob1, rng=random.Random(1)); o2 = atc_order(prob1, rng=random.Random(2))
    assert o1 != o2 and sorted(o1) == sorted(o2)
```

- [ ] **Step 2: 구현**

```python
import math
def atc_order(prob, t0=0.0, kappa=3.0, rng=None, bias_p=0.25):
    blocks = prob["blocks"]
    pbar = sum(b["processing_time"] for b in blocks) / len(blocks)
    def prio(i):
        b = blocks[i]
        slack = max(b["due_date"] - b["processing_time"] - max(t0, b["release_time"]), 0.0)
        urg = (1.0 / max(b["processing_time"], 1)) * math.exp(-slack / (kappa * pbar))
        area = _bbox_area(b)          # orient0 레이어 통합 bbox 면적 (모듈 내 헬퍼, 코드 포함)
        return urg * (1.0 + 0.001 * area)   # 동급이면 큰 블록 먼저
    ranked = sorted(range(len(blocks)), key=prio, reverse=True)
    if rng is None: return ranked
    out = []
    while ranked:                      # 기하분포 편향: 앞쪽일수록 뽑힐 확률 높음
        k = min(int(math.log(rng.random()) / math.log(1 - bias_p)), len(ranked) - 1)
        out.append(ranked.pop(k))
    return out

def _bbox_area(b):
    verts = [v for l in b["shape"][0]["layers"] for v in l]
    xs, ys = [v[0] for v in verts], [v[1] for v in verts]
    return (max(xs) - min(xs)) * (max(ys) - min(ys))
```

- [ ] **Step 3: 통과 -> Commit.**

### Task 12: time-first BLF 구성 디코더

**Files:** Create: `solver/decoder.py`. Test: `tests/test_decoder.py`
**Interfaces:** Produces: `construct(core, prob, rng, cfg=DecoderCfg()) -> Placements | None`. `DecoderCfg(kappa=3.0, bias_p=0.25, max_delay=None, bay_order="pref")`.

- [ ] **Step 1: 실패 테스트** - prob_1에서 `construct` 결과가 utils feasible이고 obj < 795089(baseline). 실패(None) 아님.
- [ ] **Step 2: 구현** - 골자:

```python
def construct(core, prob, rng, cfg=DecoderCfg()):
    core.clear()
    order = atc_order(prob, kappa=cfg.kappa, rng=rng, bias_p=cfg.bias_p)
    horizon = max(b["due_date"] for b in prob["blocks"]) * 3
    for blk in order:
        best = None   # (tardiness, y, x, bay_pref_loss) 사전식 최소
        R, P = core.release(blk), core.processing(blk)
        for t in range(R, horizon):
            for bay in _bays_by_pref(prob, blk, cfg.bay_order):
                for o in range(core.num_orients(blk)):
                    hit = core.earliest_feasible(blk, bay, o, t, t)  # 정확히 t만 시도
                    if hit:
                        e, x, y = hit
                        tard = max(0, e + P - prob["blocks"][blk]["due_date"])
                        cand = (tard, y, x, _pref_loss(prob, blk, bay))
                        if best is None or cand < best[0]: best = (cand, (blk, bay, x, y, o, e, e + P))
            if best is not None and best[0][0] == 0: break   # 지각 0이면 즉시 확정
            if best is not None and t > R + 3: break          # 소폭 지연 탐색 후 확정
        if best is None: return None
        assert core.place(*best[1])
    return core.get_placements()

def _bays_by_pref(prob, blk, mode):
    prefs = prob["blocks"][blk]["bay_preferences"]
    order = sorted(range(len(prefs)), key=lambda j: -prefs[j])
    return order if mode == "pref" else list(range(len(prefs)))

def _pref_loss(prob, blk, bay):
    prefs = prob["blocks"][blk]["bay_preferences"]
    return max(prefs) - prefs[bay]
```
(원칙 고정: "지각 0 발견 즉시 확정, 아니면 t를 R+3까지 늘려 최소 지각 후보 채택". 상수 3은 `DecoderCfg.max_delay`로 노출해 벤치에서 조정.)

- [ ] **Step 3: 40개 인스턴스 스모크** - `construct` 성공률 및 utils feasible 100%, prob_1/23 obj 기록. 실패 인스턴스는 원인(밀도/크레인) 로그 남기고 max_delay 확대로 재시도.
- [ ] **Step 4: Commit.**

### Task 13: 시간예산 + 멀티스타트 셸 + myalgorithm

**Files:** Create: `solver/budget.py`, `solver/shell.py`, `submission/myalgorithm.py`. Test: `tests/test_shell.py`
**Interfaces:** Produces: `shell.solve(prob_info, timelimit) -> dict`; `Budget` (계약 참조). **2주차 ALNS는 shell의 `improve(core, incumbent, budget)` 훅 자리에 끼워진다** - 이번 주는 pass-through.

- [ ] **Step 1: 실패 테스트** - (1) `solve(prob1, 30)`이 25초+/-5 내 반환, utils feasible, obj <= 단일 construct. (2) `OGC_FORCE_FALLBACK=1`에서도 feasible 반환(느려도 폴백 체인 작동). (3) decoder가 예외를 던지도록 몽키패치해도 trivial 폴백으로 feasible 반환.
- [ ] **Step 2: 구현** - `solve`: Budget 생성(safety 8%) -> core 생성(실패 시 fallback) -> trivial 해 확보(=안전망) -> 남은 예산 동안 멀티스타트 `construct`(시드 순회, 최선 갱신) -> 최종 utils.check_feasibility 통과 확인 후 반환(실패 시 하위 해로 강등) -> 전체 try/except로 감싸 어떤 예외도 trivial 해 반환. `submission/myalgorithm.py`:

```python
def algorithm(prob_info, timelimit=60):
    from solver.shell import solve
    return solve(prob_info, timelimit)
```
(제출 zip 스테이징 시 solver/, ogc_core.so를 루트 상대 경로로 복사 - 4주차 패키징 과제에서 자동화.)

- [ ] **Step 3: 통과 -> Commit.**

### Task 14: 벤치 하네스 + 1주차 완료 기준 측정

**Files:** Create: `experiments/run_bench.py`, `experiments/results/` (gitignore에 raw 추가). Test: 수동 실행.
**Interfaces:** Produces: `python experiments/run_bench.py --timelimit 60 --out experiments/results/week1.json` - 인스턴스별 {obj, obj1/2/3, runtime, feasible, seed} JSON. **2~4주차 하네스가 이 CLI/스키마를 확장한다.**

- [ ] **Step 1: 하네스 작성** - `experiments/run_bench.py`:

```python
import argparse, json, time, pathlib, sys
ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "solver")); sys.path.insert(0, str(ROOT / "baseline"))
from utils import check_feasibility

def run_one(prob, algo, timelimit, seed):
    if algo == "baseline":
        from baseline_greedy import greedyalgorithm as fn
        t0 = time.time(); sol = fn(prob, timelimit)
    else:
        from solver.shell import solve
        t0 = time.time(); sol = solve(prob, timelimit)
    rt = time.time() - t0
    r = check_feasibility(prob, sol)
    return {"feasible": r["feasible"], "objective": r["objective"],
            "obj1": r["obj1"], "obj2": r["obj2"], "obj3": r["obj3"],
            "runtime": round(rt, 1), "seed": seed}

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--timelimit", type=int, default=60)
    ap.add_argument("--algo", choices=["ours", "baseline"], default="ours")
    ap.add_argument("--instances", default="all")   # "prob_1,prob_23" 또는 all
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--out", default="experiments/results/latest.json")
    a = ap.parse_args()
    names = ([f"prob_{i}" for i in range(1, 41)] if a.instances == "all"
             else a.instances.split(","))
    results = {}
    for n in names:
        prob = _load(n)                       # tests/conftest.py의 _load와 동일 로직 복사
        results[n] = run_one(prob, a.algo, a.timelimit, a.seed)
        print(f"{n:10s} feas={results[n]['feasible']} obj={results[n]['objective']} "
              f"rt={results[n]['runtime']}s")
    pathlib.Path(a.out).parent.mkdir(parents=True, exist_ok=True)
    json.dump({"algo": a.algo, "timelimit": a.timelimit, "results": results},
              open(a.out, "w"), indent=1)

if __name__ == "__main__":
    main()
```
(결과 JSON 스키마 `{algo, timelimit, results: {name: {feasible, objective, obj1..3, runtime, seed}}}`는 2~4주차 하네스 확장의 계약.)
- [ ] **Step 2: 측정 실행** - `--timelimit 60`으로 ours vs baseline 전 인스턴스 비교. **완료 기준: feasible 40/40, obj 합계 baseline 미만, check_place <10us(Task 6 벤치), fuzz/oracle 테스트 전부 green.**
- [ ] **Step 3: 결과를 `experiments/results/week1.json` + 요약을 plan 파일 하단에 추기. Commit.**

## Self-Review 체크 결과

- 스펙 4절 L1(NFP 비트맵, 점유, API) -> Task 2~7. 4절 L2(ATC, BLF) -> Task 11~12. 4절 L5(예산, 폴백, utils 검증) -> Task 10, 13. 6절(fuzz, oracle, 벤치) -> Task 3, 5, 14. 7절 1주차 완료 기준 -> Task 14. 커버리지 갭 없음.
- Minkowski 가속, 병렬화, ALNS는 의도적으로 2주차 이연 (7절 로드맵과 일치).
- 타입/시그니처: 계약 섹션과 각 Task Interfaces 일치 확인.
