# OGC 2026 - 1주차: C++ 기하 코어 v0 + 구성 디코더 + 견호성 뼈대 구성
> For Agent workers: Use superpowers:subagent-driven-development to implement this plan task by task. Steps use (` - [ ] `) syntax for tracking

**GOAL**: 전 40개 인스턴스에서 baseline 보다 좋은 feasible해를 내는 "C++ 기하 코어(ogc_core) + ATC/BLF 구성 디코더 + 3단 폴백 셀"을 완성한다.


> **설명** "인스턴스"는 하나의 문제 케이스(예: `prob_1.json`, 블록 100개 + bay 2개)를 뜻합니다. "feasible 해"는 위 Stage1~5 를 전부 통과하는 배치 스케쥴 결과입니다. "baseline"은 `baseline\baseline_greedy.py`에 이미 구현된 EDD(납기순) + Best Fit Greedy 알고리즘 - 이번 주 목표는 이보다 더 좋은 (목적값이 더 낮은) feasible한 해를 40개 인스턴스 전부에서 내는 것입니다. "3단 폴백 셸"은 아래 task 10/13 에서 설명하는 안전망 구조(정교한 방법 실패 -> 더 단순한 방법 -> 최후 보루)입니다.

**Architecture** 좌표를 x10^4 스케일 정수로 변환해 Clipper2 정수 클리핑으로 정확한 충돌 판정을 하고, sahpe쌍별 "충돌 오프셋 비트뱁" (static/entry)을 lazy 캐시, 배치 상태에서 페어와이즈 비트맵을 OR 해 free-position 마스크를 만들어 us급 배치 검사를 달성한다. Python 층은 pybind11 로 이 코어를 호출하는 디코더 (ATC 우선순위 + time-first BLF)와 견고성 셸 (시간예상, 폴백, utils 최종검증)을 얹는다.

> **설명** 
> - **좌표를 x10^4 스케일 정수로 변환**: 문제 좌표는 소수점 4자리까지 있는 실수(float)입니다. 부동소수점 비교는 항상 미세한 오차 문제가 있어서, 모든 좌표에 10,000을 곱해 정수로 바꿔버립니다. 그러면 **정확한** 정수 연산으로 충돌 판정 가능합니다.(아래 SCALE 상수, Task 2)
> - **Clipper2 정수 클리핑**: Clipper2는 다각형의 교집한/합집합 등을 정확히 계산하는 오픈소스 기하 라이브러리입니다. "두 폴리곤이 겹치는가"를 "교집합 면적이 0보다 큰가"로 정확히 판정하는데 씁니다. (Task 3)
> - **충돌 오프셋 비트맵(static/entry)**: 앞서 설명한 NFP(No-Fit Polygon) 개념의 이 프로젝트식 구현입니다. "블록 A 기준 블록 B를 상대적으로 (dx, dy)만큼 떨어트려 놓으면 충돌하는가?"를 모든 정수 (dx, dy)에 대해 미리 계산해 비트(0/1) 배열로 저장해둡니다. static은 "가만히 있을 때"(층 같은 것끼리만), entry는 "크레인 진입/진출 시" 충돌을 뜻합니다. (Task4)
> - **페어와이즈 비트맵을 OR 해 free-position 마스크**: 새 블록을 놓을 후보 위치들을 비트보드(1비트=1좌표)로 표현하고, 이미 놓인 블록 하나하나와의 충돌 비트맵을 그 상태 위치로 밀어서(shift) OR 연산으로 합칩니다. 결과적으로 "누구와도 충돌하지 않는 좌표"만 0으로 남습니다. - 이게 `free_positions`의 구현 원리입니다. (Track 6)
> - **us급 배치 검사**: 마이크로초(microsecond) 단위로 배차 가능 여부를 판정합니다. 비트 연산(OR, AND, shift)은 cpu가 한번에 64비트씩 처리할 수 있어서 매우 빠릅니다. -순수 도형 교차 계산을 매번 새로 하는 것보다 수백~수천배 빠릅니다.
> - **디코더(ATC 우선 순위 + time-first BLF)**: 메타휴리스틱에서 흔히 보는 "우선 순위 리스트->디코더가 실제 해로 반환"하는 구조입니다. ATC로 블록 순서를 정하고(Task11), BLF 방식으로 순서대로 하나씩 배치해나갑니다.(Task12). 이건 마치 GRASP의 "구성 단계(construction phase)"와 같은 역할이라고 생각하면 됩니다.
> - **견고성 셀**: 실제 대회 서버는 인터넷도 없고 시간 제한도 미리 알려지지 않아서, 정교한 방법이 실패하거나 시간이 부족할 때를 대비한 다단계 안정장치가 필요합니다. `shell.solve()`가 이 전체를 감싸는 최상위 진입점입니다. (Task 13)

**Tech Stack**: C++20, CMake, pybind11, Clipper2(vendored, BSL-1.0), nlohmann/json(vendored, MIT), Python3.12, shapely 2.2.2 (oracle/폴백전용), pytest

**이 프로젝즈가 사용하는 OR/메타휴리스틱 용어**
- **ATC(Apprarent Tardiness Cost)**: 병렬기계 스케쥴링의 우선 순위 규칙. "짧은 작업 우선(SPT)"과 "납기 임박 우선(EDD)"을 부드럽게 절충한 공식
- **BLF(Bottom-Left-Full)**: 2D Packing에서 도형을 "가능한 왼쪽 아래"에 배치하는 휴리스틱
- **NFP(No-Fit Polygon)**: "도형 A를 어디에 놓으면 도형 B와 겹치는가"를 미리 계산해 캐싱하는 고전적 충돌판정 가속 기법. 이 프로젝트는 좌표가 정수라 근사 없이 정확한 "충돌 오프셋 비트맵" 으로 구현합니다.
- **biased randomization**: 순수 랜덤이 아니라 원래 우선 순위를 대체로 존중하되, 기하분포로 확률적으로 살짝 흔드는 멀티스타트 다양화 기법
- **ALNS**: 파괴(destroy), 복구(repair)를 반복하여, 최근 성과에 따라 연산자 선택 확률을 적용하는 메타휴리스틱
- **CP-SAT**: Google OR Tools의 제약 만족 + SAT 기반 솔버. 



## 파일 구조 
```
core/
  CMakeList.txt
  third-party/clipper2/... # vendored
  third-party/nlohman/json.hpp # vendored
  src/geom.hpp, geom.cpp # 스케일 정수 폴리곤, overlap predicate
  src/instance.hpp, instance.cpp #인스턴스 파싱, ShapeTable
  src/nfp.hpp, nfp.cpp # 충돌 오프셋 비트맵 캐시
  src/state.hpp, state.cpp # 배치 상태, 시간 관계, free-mask, objective
  src/bindings.cpp #pybind11 모듈 ogc_core
solver/
  __init__.py
  core_iface.py # ogc_core 로드 + 순수 python 
  fallback_core.py # shapely 기반 동일-API 폴백 (완전 기능, 저속)
  priority.py # ATC + biased randomization
  decoder.py # time-first BLF 구성
  serialize.py # placements -> operations dict
  budget.py # 시간 예산 관리자
  shell.py # solve (prob_info, timelimit) - 멀티스타트 + 폴백 + 최종 검증
submission/
  myalgorithm.py # solver.shell.solve 위임 (제출 스테이징)
experiements/
  run_bench.py # 40 인스턴스 벤치, 결과 JSON
tests/
  confest.py # 인스턴스 로더 fixture, baseline utils import 경로
  test_geom_fuzz.py # C++ overlap vs shapely 대조
  test_npf.py # 비트맵 vs 전수 shapely 대조
  test_state_oracle.py #required-mask 의미론 vs utils 5단계 oracle
  test_api.py # place/check/earliest/free_positions
  test_objective.py #objective vs utils
  test_serialize.py # Stage5 통과 직렬과
  test_decoder.py, test_shell.py, test_fallback_parity.py
```

> **설명**  `core/`는 빠른 C++ 엔진 (도형,충돌,상태), `solver/`는 그 엔진 위에서 실제로 "어떤 순서로, 어디에 놓을지"를 결정하는 Python 알고리즘 로직, `submission/`은 대회에 제출할 최종 진입점, `experiments/`는 성능 측정, `tests\`는 각 구성요소가 정확한지 검증합니다. `test_*.py`는 파일명들이 곧 "이 컴포넌트는 이 기준(대개 shapely나 util.py)과 대조해서 검증한다"는 원칙을 보여줍니다. - 빠른 C++ 코드를 믿을 수 있는 느린 Python 기준과 계속 대조하는 것이 이 프로젝트의 핵심 신뢰 전략입니다.

## 인터페이스 계약 (2~4주차 계획이 의존 - 변경 시 주차 계획 동기화 필수)
`ogc_core.Core` (pybind11, 모든 좌표는 비스케일 정수(원래 단위), 시간은 정수 일):

```python
Core(instance_json: str) # json.dumps(prob_info)로 생성
.n_blocks -> int ; .n_bays -> int
.processing(block) -> int; .release(block)-> int ;  .due(block) -> int
.num_orients(block) -> int

# 상태 조작 (feasible할 때만 True 반환하며 반영)
.place(block, bay, x, y, orient, entry, exit) -> bool
.remove(block) -> None
.clear() -> None
.get_placements() -> list[tuple[block,bay,x,y,orient,entry,exit]]
.load_placements(list[tuple]) -> int # 순서대로 place, 성공 개수 반환

# 조회(상태 불변)
.check_place(block, bay, x,y,orient, entry, exit) -> bool
.free_position(block, bay,orient, entry, exit ) -> list[tuple[x,y]]
.earlist_feasible(block, bay, orient, t_min, t_max) -> tuple[entry, x,y] |None
.objective() -> tuple[z1,z2,z3]
.pair_bits_at_current() -> list[tuple[i,j,static_hit, entry_ij, entry_ji]]
  # 같은 bay에 배치된 쌍의 현재 오프셋 비트
  # (3주차 CP-SAT 재타이밍 입력)
.verify_full() -> list[str]   # utils 1~5단계 의미론 자체 검증, 빈 리스트=OK
```

>**설명 - 이 API를 메타휴리스틱 프레임워크 관점에서 읽으면** `Core`는 사실상 "현재 해 상태를 들고 있는 객체"입니다.`place`/`remove`는 ALNS의 destroy/repair연산이 직접 호출할 저수준 연산이고, `check_place`(부작용 없이 "될까?"만 확인)와 `free_positions`/ `earliest_feasible`(어디에 언제 놓을 수 있는지 탐색)은 디코더가 다음 후보를 찾을 때 쓰는 조회함수입니다.  `objective()`는 목적함수를 반환하고, `verify_full()`은 0.4절 Stage1~5 의미론을 코어 스스로 자체 재검증하는 안전장치입니다. **상태 불편(read-only)**이라고 표시된 함수들은 호출해도 현재 배치 상태를 바꾸지 않는다는 뜻 - 그래서 여러 후보들을 자유롭게 "찔러볼" 수 있습니다. (메타휴리스틱의 이웃해 탐색가 같은 패턴)


`solver` 계약:
 - `core_iface.make_core(prob_info) -> CoreLike` -ogc_core 실패시 fallback_core 반환. 두 구현은 위 API 완전 동일
 - `priority.atc_order(prob,t0, paka,rng,bias_p)-> list[block]`
 - `decoder.construct(core, prob, rng, cfg) -> Placements | None` (`Placements = list[tuple]`, get_placements 형식)
 - `serialize.to_operations(placements) -> dict` - 제출 형식
 - `budget.Budget(timelimit,  safety=0.08)` : `.remain()`, `.phase(name, frac)`, `.expired()`
 - `shell.solve(prob_info, timelimit) -> dict` - myalgorithm의 유일한 진입점
 
**같은 날(same-day) 순서 규약(v0 고정)**: 같은 날 같은 bay의 EXIT들은 block id 오름차순, ENTRY들도 block id 오름차순으로 수행, 직렬화한다. 코어의 시간관계 판정도 동일 규약을 가정한다. (아래 Task 5 표)  

> **설명** 같은 날 여러 오퍼레이션이 겹칠 때 "어떤 순서로 실행됐다고 칠 것인가"는 임의로 정해야 하는 규칙입니다.(문제 자체는 하루 단위 시간만 주어지고, 하루 안에서의 초 단위 순서는 정의 안 됨). 이 프로젝트는 "EXIT 먼저(같은 bay를 비워야 다음 블록이 들어올 자리가 생기니까), 그 안에서는 block id 순서"로 **일관되게 고정**하기로 정했습니다. 이 규칙이 왜 중요한지는 Task5의 "주의 케이스(같은 날 EXIT=ENTRY면 제약 없음)에서 다시 나옵니다."

### 계약 부록 - 주차 간 조정 확정 (2026-07-03, 2~4주차 계획보다 우선)
2~3주차 계획의 "계약 변경 요청"을 상호 대조해 아래와 같이 확정한다. **각 주차 계획 본문과 이 부록이 다르면 부록을 따른다**

1. **`shell.improve` 최종 시그니처** (2주차 확정): `improve(core, incumbent, budget, *, prob, rng, cfg=None, trace=None, status=None)`. 1주차 Task 13은 pass-through 자리만 만든다. 3주차 parallel.py의 "rng kwarg 요청"은 이 시그니처로 이미 충족.
2. **`shell.solve` 시그니처 진화**: 제출 경로는 항상 `solve(prob_info, timelimit)` 위치 인자 2개(불변). 2주차가 `*, alns_cfg=None, report=None` 추가 -> 3주차는 자체 명령(`cfg`/`status_out`) 대신 **`mh_cfg=None`을 추가하고 `report`를 재사용**한다. (치환 지시) -> 4주차 T1이 `solver/config.py::SolvingConfig`로 통합 (`solve(prob_info, timelimit, *, config=None, report=None)`) alns_cfg/mh_cfg는 SolverConfig의 하위 필드로 흡수.
3. **ALNS설정클래스 명칭**: 2주차 정의 `AlnsConfig`(필드: acceptance, rrt_start_pct, sa_start_pct, disable_ops, max_iters)가 정봄. 4주차의 `AlnsCfg.max_iters`추가 요청은 **이미 존재하므로 소멸** - 4주차 문면의 `AlnsCfg`는 `AlnsConfig`로 읽는다.
4. **벤치 토근 표준**: ALNS 연산자 ablation은 `--disable-op {random, worst, shaw, time_slice spatial_column blocking_set, greedy_noise, regret2, regret3, most_constrained}` (2주차). 메타휴리스틱 스테이지는 `--disable {retiming,pool,polish}` + env `OGC_MH_DISABLE`(3주차). 4주차 문면의 ₩