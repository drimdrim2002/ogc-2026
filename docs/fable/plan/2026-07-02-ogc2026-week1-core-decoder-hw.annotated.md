# OGC 2026 - 1주차 계획 주석본 (도메인 설명 포함)

> **이 문서에 대하여**
> 이 파일은 `2026-07-02-ogc2026-week1-core-decoder-hw.md` (이하 "원본")을 **그대로 보존**한 채, 필사하며 읽을 때 참고할 수 있도록 도메인 지식·용어 설명을 끼워 넣은 학습용 사본입니다. 원본은 2~4주차 계획이 의존하는 **계약 문서**라서 절대 수정하지 않았습니다. 설명은 `> 💡 설명:` 형태의 인용 블록으로 표시되며, 원문과 섞이지 않도록 구분해두었습니다. 메타휴리스틱 개발 경험은 있지만 이 문제(조선소 블록 적치장)의 도메인 지식이 없다는 전제로, 물리적 개념·OR 이론 용어·엔지니어링 트릭을 모두 설명합니다.

---

## 0. 배경: 이 문제는 대체 무엇인가

원본 문서는 이 배경 지식을 전제하고 쓰여 있어서, 먼저 여기서 짚고 갑니다. (근거: `OGC2026_문제분석.md`, `docs/superpowers/specs/2026-07-02-ogc2026-strategy-design.md`, `baseline/utils.py`)

### 0.1 물리적 문제: 조선소 블록 적치장 스케줄링

대회 부제는 "Pack the Block, Beat the Clock" — 이름 그대로 **2D/3D 패킹 문제**와 **스케줄링 문제**가 겹쳐 있습니다.

> 💡 **설명 — 큰 그림:** 조선소는 배를 통째로 만들지 않고, "블록"이라 부르는 대형 강철 구조물 단위로 각각 제작한 뒤 나중에 합칩니다. 이 블록들은 완성되기 전까지 잠시 **적치장(bay, 야적장 구획)**에 보관되는데, 적치장 공간은 한정되어 있고 블록마다 들어와야 하는 시점(release)과 나가야 하는 기한(due date)이 다릅니다. 그래서 "언제, 어느 bay의, 어느 위치에, 어떤 방향으로 블록을 놓을 것인가"를 동시에 정해야 합니다. 메타휴리스틱 관점에서 번역하면: **각 bay는 독립된 '기계'** 역할을 하는 병렬기계 스케줄링 문제이면서, 동시에 각 기계(bay) 내부는 **2D 도형 배치(bin packing) 문제**이기도 한, 두 문제가 결합된 형태입니다.

### 0.2 핵심 개념 사전

| 용어 | 의미 |
|---|---|
| **Block** | 조선소에서 만드는 배의 부품 단위인 대형 강철 구조물. 폴리곤 도형이면서 동시에 처리시간·납기를 가진 "작업(job)"이기도 함. |
| **Bay** | 고정 크기(`width × height`)의 직사각형 적치 공간. 각 bay는 독립된 크레인을 가짐. |
| **ENTRY** | 크레인이 블록을 수직으로 내려서 bay 바닥에 놓는 오퍼레이션. |
| **EXIT** | 크레인이 블록을 수직으로 들어올려 bay 밖으로 빼내는 오퍼레이션. |
| **점유 구간** | 블록은 `[ENTRY, EXIT)` 반개구간(entry 포함, exit 미포함) 동안 그 공간을 차지함. |
| **Orientation(방향)** | 블록은 자유회전이 아니라, 미리 계산된 이산적 방향 후보 중 하나를 선택. `shape` 리스트의 인덱스가 `orient_idx`. |
| **Layer(레이어)** | 블록은 다층 구조물일 수 있음. 레이어 0이 가장 낮은 물리적 높이, 인덱스가 클수록 높은 층(대부분 1~2개 레이어). |
| **레퍼런스 포인트** | 각 방향의 레이어 0 첫 번째 정점은 항상 로컬 좌표 (0,0)으로 고정 — 배치 시 이 점을 실제 좌표 (x,y)로 옮기는 것이 배치 연산. |
| **bay_preferences** | 블록별로 "이 bay에 놓이길 원하는 정도" 점수 리스트, 합계가 항상 100 (예: `[0, 100]`은 두 번째 bay를 100% 선호). |

> 💡 **설명 — 왜 "층(layer)"이 있는가:** 실제 블록은 밑면이 넓고 위로 갈수록 좁아지는 구조가 흔합니다(비행기 동체처럼). 그래서 폴리곤 하나로는 형상을 정확히 표현할 수 없어, 높이별로 서로 다른 폴리곤(레이어)을 겹쳐서 하나의 3D 형상을 근사합니다. 이게 나중에 Task 3~5에서 "레이어 k와 레이어 j가 부딪히는가"를 따지는 이유입니다.

### 0.3 목적함수 Z1 / Z2 / Z3

원본에 자주 등장하는 `objective()`, `obj1/obj2/obj3`가 정확히 무엇을 재는지 정리합니다 (`baseline/utils.py` 구현 기준).

| 항 | 이름 | 계산 | 물리적 의미 |
|---|---|---|---|
| **Z1** | 총 지연시간 | `Σ max(0, EXIT_i − due_i)` | 블록을 납기보다 늦게 빼내면 벌점. 후속 조립공정 지연을 의미. |
| **Z2** | 작업량(적치 부하) 불균형 | bay별 부하를 면적으로 정규화한 값의 최대 편차 | 특정 bay에 일이 몰려 혼잡해지는 것을 방지. |
| **Z3** | bay 선호도 손실 | `Σ (최선호 점수 − 실제 배정 bay 점수)` | 최선호 bay가 아닌 곳에 배정되면 손실. |

> 💡 **설명 — 어느 항이 제일 중요한가:** 실측상 **Z1(지연시간)이 목적값의 약 95%를 차지**하는 "지연시간 지배" 문제입니다. 즉 실질적 우선순위는 **① 실행가능성(feasibility) ≫ ② Z1 ≫ ③ Z3 ≫ ④ Z2** 순서라고 생각하면 됩니다. 뒤에 나올 ATC 우선순위 규칙(Task 11)이 "납기가 급한 블록을 먼저 배치"하는 이유가 바로 이것 — Z1을 직접 겨냥한 설계입니다.

### 0.4 utils.py가 하는 일: 실행가능성 판정 5단계 + 크레인 스윕 규칙

원본에서 "utils(oracle)", "Stage5", "j≥k" 같은 표현이 자주 나옵니다. `baseline/utils.py`의 `check_feasibility` 함수가 최종 심판(oracle)이며, 5단계로 나뉩니다.

| 단계 | 검사 내용 | 물리적 의미 |
|---|---|---|
| **Stage 1** | 모든 블록이 정확히 1개의 ENTRY/EXIT를 가지는지, bay/orient 인덱스가 유효한지, `entry ≥ release`, `exit − entry ≥ processing_time`인지 | 데이터 형식·기초 타이밍 검증 |
| **Stage 2** | ENTRY 시점에, 그 순간 bay에 있던 다른 블록들과 크레인이 부딪히지 않고 내려놓을 수 있는지 | "지금 이 블록을 크레인으로 내려놓을 수 있는가?" |
| **Stage 3** | EXIT 시점에, 크레인이 부딪히지 않고 들어올려 뺄 수 있는지 | "지금 이 블록을 크레인으로 들어올려 뺄 수 있는가?" |
| **Stage 4** | bay 경계 안에 있는지 + 체류기간이 겹치는 모든 블록 쌍이 정적으로 겹치지 않는지 | "놓여있는 동안 내내 서로 겹치지 않는가?" |
| **Stage 5** | 전체 오퍼레이션을 시간순으로 재생(replay)하며 같은 날 EXIT가 ENTRY보다 먼저 실행되는지 등 순서 규약까지 재검증 | "실제로 이 순서대로 실행해도 문제없는가?" 최종 심판 |

> 💡 **설명 — 왜 ENTRY/EXIT "순간"과 "체류기간"을 따로 검사하는가 (j≥k 규칙):** 크레인이 블록을 내릴 때, 새 블록의 레이어 k는 내려가는 도중 자기보다 높은 층들을 스쳐 지나갑니다. 예를 들어 새 블록의 레이어 0(맨 아래층)이 하강하는 순간, 기존에 놓인 블록의 레이어 0뿐 아니라 레이어 1, 2, ...(더 높은 층)와도 같은 높이를 지나칠 수 있습니다. 그래서 **"레이어 k(내 층) vs 레이어 j(상대 층), j ≥ k인 모든 조합"**을 다 검사해야 합니다 — 반대로 `j < k`(상대가 나보다 낮은 층)는 크레인 경로에 걸리지 않으므로 안전합니다. 정리하면:
> - `j == k`: 최종 안착 위치에서의 정적 충돌 검사
> - `j > k`: 크레인이 내려가는/올라가는 **경로 중**의 스윕 충돌 검사
> - `j < k`: 절대 충돌 안 남 (지나칠 일이 없음)
>
> 이게 나중에 Task 4의 `static_bm`(정적 충돌, `j==k`만 봄)과 `entry_bm`(진입/퇴출 충돌, `j≥k` 전부 봄) 두 종류 비트맵으로 나뉘는 이유입니다. "위에서 내려다보면 겹쳐 보여도, 층이 다르면 가만히 있을 때는 괜찮지만 크레인으로 넣고 뺄 때는 막힐 수 있다"는 게 핵심 직관입니다.

### 0.5 이 프로젝트가 쓰는 OR/메타휴리스틱 용어 — 한눈에 미리보기

아래는 짧은 정의만 먼저 두고, 본문(Task 4, 5, 11, 12, 13)에서 실제 코드와 함께 다시 자세히 설명합니다.

- **ATC (Apparent Tardiness Cost)**: 병렬기계 스케줄링의 우선순위 규칙. "짧은 작업 우선(SPT)"과 "납기 임박 우선(EDD)"을 부드럽게 절충한 공식.
- **BLF (Bottom-Left-Fill)**: 2D 패킹에서 도형을 "가능한 한 왼쪽 아래"에 배치하는 휴리스틱.
- **NFP (No-Fit Polygon) / 민코프스키 합**: "도형 A를 어디에 놓으면 도형 B와 겹치는가"를 미리 계산해 캐싱하는 고전적 충돌판정 가속 기법. 이 프로젝트는 좌표가 정수라 근사 없이 정확한 "충돌 오프셋 비트맵"으로 구현합니다.
- **biased randomization**: 순수 랜덤이 아니라, 원래 우선순위를 대체로 존중하되 기하분포로 확률적으로 살짝 흔드는 멀티스타트 다양화 기법.
- **ALNS (Adaptive Large Neighborhood Search)**: 파괴(destroy)-복구(repair)를 반복하며, 최근 성과에 따라 연산자 선택 확률을 적응시키는 메타휴리스틱. (2주차 주제 — 이번 주는 자리만 만듦)
- **CP-SAT**: Google OR-Tools의 제약만족+SAT 기반 솔버. 이 프로젝트는 "위치는 고정하고 시간만 재조정"하는 매트휴리스틱 마무리 단계(3주차)에 씀.

---

## 1. 여기부터 원본 문서 + 인라인 설명

> 아래는 원본 파일의 내용을 그대로 옮기고, 어려운 대목 바로 아래 `> 💡 설명:` 블록을 끼워 넣은 것입니다.

# OGC 2026 - 1주차: C++ 기하 코어 v0 + 구성 디코더 + 견고성 뼈대 구현 계획

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 전 40개 인스턴스에서 baseline보다 좋은 feasible 해를 내는 "C++ 기하 코어(ogc_core) + ATC/BLF 구성 디코더 + 3단 폴백 셸"을 완성한다.

> 💡 **설명:** "인스턴스"는 하나의 문제 케이스(예: `prob_1.json`, 블록 100개 + bay 2개)를 뜻합니다. "feasible 해"는 위 Stage 1~5를 전부 통과하는 배치·스케줄 결과입니다. "baseline"은 `baseline/baseline_greedy.py`에 이미 구현된 EDD(납기순) + Best-Fit Greedy 알고리즘 — 이번 주 목표는 이보다 더 좋은(목적값이 더 낮은) feasible 해를 40개 인스턴스 전부에서 내는 것입니다. "3단 폴백 셸"은 아래 Task 10/13에서 설명하는 안전망 구조(정교한 방법 실패 → 더 단순한 방법 → 최후 보루)입니다.

**Architecture:** 좌표를 x10^4 스케일 정수로 변환해 Clipper2 정수 클리핑으로 정확한 충돌 판정을 하고, shape쌍별 "충돌 오프셋 비트맵"(static/entry)을 lazy 캐시. 배치 상태에서 페어와이즈 비트맵을 OR 해 free-position 마스크를 만들어 us급 배치 검사를 달성한다. Python 층은 pybind11로 이 코어를 호출하는 디코더(ATC 우선순위 + time-first BLF)와 견고성 셸(시간예산, 폴백, utils 최종검증)을 얹는다.

> 💡 **설명 — 이 한 문단이 사실상 전체 설계의 요약입니다.** 하나씩 풀면:
> - **"좌표를 x10^4 스케일 정수로 변환"**: 문제 좌표는 소수점 4자리까지 있는 실수(float)입니다. 부동소수점 비교는 항상 미세한 오차 문제가 있어서(예: `0.1+0.2 != 0.3`), 모든 좌표에 10,000을 곱해 정수로 바꿔버립니다. 그러면 오차 없이 완전히 **정확한** 정수 연산으로 충돌 판정을 할 수 있습니다. (아래 SCALE 상수, Task 2)
> - **"Clipper2 정수 클리핑"**: Clipper2는 다각형의 교집합/합집합 등을 정확히 계산하는 오픈소스 기하 라이브러리입니다. "두 폴리곤이 겹치는가"를 "교집합 면적이 0보다 큰가"로 정확히 판정하는 데 씁니다 (Task 3).
> - **"충돌 오프셋 비트맵(static/entry)"**: 앞서 설명한 NFP(No-Fit Polygon) 개념의 이 프로젝트식 구현입니다. "블록 A 기준 블록 B를 상대적으로 (dx, dy)만큼 떨어뜨려 놓으면 충돌하는가?"를 모든 정수 (dx, dy)에 대해 미리 계산해 비트(0/1) 배열로 저장해둡니다. static은 "가만히 있을 때"(층 같은 것끼리만), entry는 "크레인 진입/퇴출 시"(0.4절의 j≥k 규칙 포함) 충돌을 뜻합니다 (Task 4).
> - **"페어와이즈 비트맵을 OR 해 free-position 마스크"**: 새 블록을 놓을 후보 위치들을 비트보드(1비트=1좌표)로 표현하고, 이미 놓인 블록 하나하나와의 충돌 비트맵을 그 상대 위치로 밀어서(shift) OR 연산으로 합칩니다. 결과적으로 "누구와도 충돌하지 않는 좌표"만 0으로 남습니다 — 이게 `free_positions`의 구현 원리입니다 (Task 6).
> - **"us급 배치 검사"**: 마이크로초(microsecond, 100만분의 1초) 단위로 배치 가능 여부를 판정한다는 뜻. 비트 연산(OR, AND, shift)은 CPU가 한 번에 64비트씩 처리할 수 있어 매우 빠릅니다 — 순수 도형 교차 계산을 매번 새로 하는 것보다 수백~수천 배 빠릅니다.
> - **"디코더(ATC 우선순위 + time-first BLF)"**: 메타휴리스틱에서 흔히 보는 "우선순위 리스트 → 디코더가 실제 해로 변환"하는 구조입니다. ATC로 블록 순서를 정하고(Task 11), BLF 방식으로 순서대로 하나씩 배치해나갑니다(Task 12). 이건 마치 GRASP의 "구성 단계(construction phase)"와 같은 역할이라고 생각하면 됩니다.
> - **"견고성 셸"**: 실제 대회 서버는 인터넷도 없고 시간제한도 있어서, 정교한 방법이 실패하거나 시간이 부족할 때를 대비한 다단계 안전장치가 필요합니다. `shell.solve()`가 이 전체를 감싸는 최상위 진입점입니다 (Task 13).

**Tech Stack:** C++20, CMake, pybind11, Clipper2(vendored, BSL-1.0), nlohmann/json(vendored, MIT), Python 3.12, shapely 2.1.2 (oracle/폴백 전용), pytest.

> 💡 **설명:**
> - **pybind11**: C++로 작성한 코드를 Python에서 마치 일반 Python 모듈처럼(`import ogc_core`) 호출할 수 있게 해주는 바인딩 라이브러리. "빠른 코어는 C++로, 실험/조립은 Python으로"라는 흔한 하이브리드 구조를 가능하게 합니다.
> - **vendored**: 외부 라이브러리 소스코드를 리포지토리 안에 직접 복사해 넣는 것(패키지 매니저 의존 없이). 서버에 인터넷이 없으므로 빌드 시점에 외부 다운로드가 불가능해서 이렇게 합니다.
> - **BSL-1.0 / MIT**: 오픈소스 라이선스 종류. 상업적 이용·재배포가 자유로운 관대한 라이선스들입니다.
> - **shapely**: Python에서 흔히 쓰는 기하 연산 라이브러리(내부적으로 GEOS 사용). 여기서는 "정답 채점기(oracle)" 역할(C++ 코어의 판정이 맞는지 대조하는 기준) 및 C++ 빌드가 안 될 때의 순수 Python 폴백 구현에만 씁니다 — 속도는 느리지만 검증된 정확성을 신뢰할 수 있기 때문입니다.

## Global Constraints (설계서 1절, 5절에서 복사)

- 서버: Ubuntu 24.04, AMD Threadripper PRO 9955WX, **4코어/16GB/인터넷 없음**, firejail+cpulimit, 시간제한 수 분~30분(비공개).
- Python 3.12 고정 (공식 env). `utils.py` 수정 금지 - 최종 검증 oracle.
- 제출 zip: 루트에 `myalgorithm.py`, <=15MB, 상대경로만, `.dll/.exe/.vb` 류 금지.
- 해 형식: x, y는 정수, 시간은 정수 일, 같은 날 EXIT 전량이 ENTRY보다 먼저.
- 빌드: `-O3 -march=x86-64-v3 -static-libstdc++ -static-libgcc`, CPython 3.12 대상.
- 코어 판정은 utils(shapely, `area>0`)와 갈릴 수 있는 경계에서 **미세하게 보수적**이어야 함.
- Gurobi 사용 보류(라이선스 확인 전). MIP 계열은 CP-SAT 사용.

> 💡 **설명:**
> - **firejail+cpulimit**: 대회 채점 서버가 제출된 코드를 격리된 샌드박스(firejail)에서, CPU 사용량을 제한(cpulimit)해 실행한다는 뜻. 임의 코드 실행 대회에서 흔한 보안·자원 제약 장치입니다.
> - **`utils.py` 수정 금지 - 최종 검증 oracle**: 앞서 설명한 Stage 1~5 판정 함수가 담긴 파일이며, 대회 측이 이걸로 제출물을 최종 채점합니다. 우리 C++ 코어가 아무리 빨라도, 최종적으로는 이 utils.py 기준으로 feasible 판정이 나야만 인정됩니다. 그래서 "oracle(신탁, 정답 기준)"이라 부릅니다.
> - **"미세하게 보수적이어야 함"**: 부동소수점(utils/shapely)과 정수 스케일(우리 코어)은 극히 미세한 경계(예: 딱 접촉하는 경우)에서 판정이 갈릴 수 있습니다. 이때 "우리가 안된다고 판단했는데 실제로는 되는 경우"(보수적 오판)는 손해만 보고 끝나지만, "우리가 된다고 판단했는데 실제로 안 되는 경우"(과대평가)는 **최종 제출이 infeasible로 실격**되는 치명적 문제입니다. 그래서 애매하면 항상 "안전한 쪽(거부)"으로 치우치게 설계해야 합니다. (Task 5의 oracle 테스트에서 이 방향의 오차만 0건이어야 한다고 강조하는 이유이기도 합니다.)
> - **CP-SAT**: 위 0.5절 참고. Gurobi 같은 상용 솔버는 라이선스 확인 전이라 보류하고, 무료 오픈소스인 Google OR-Tools의 CP-SAT를 3주차 매트휴리스틱 단계에서 쓸 예정입니다.

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

> 💡 **설명 — 큰 그림에서 이 구조를 보면:** `core/`는 빠른 C++ 엔진(도형·충돌·상태), `solver/`는 그 엔진 위에서 실제로 "어떤 순서로, 어디에 놓을지"를 결정하는 Python 알고리즘 로직, `submission/`은 대회에 제출할 최종 진입점, `experiments/`는 성능 측정, `tests/`는 각 구성요소가 정확한지 검증합니다. `test_*.py` 파일명들이 곧 "이 컴포넌트는 이 기준(대개 shapely나 utils.py)과 대조해서 검증한다"는 원칙을 보여줍니다 — 빠른 C++ 코드를 믿을 수 있는 느린 Python 기준과 계속 대조하는 것이 이 프로젝트의 핵심 신뢰 전략입니다.

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

> 💡 **설명 — 이 API를 메타휴리스틱 프레임워크 관점에서 읽으면:** `Core`는 사실상 "현재 해 상태를 들고 있는 객체"입니다. `place`/`remove`는 ALNS의 destroy/repair 연산이 직접 호출할 저수준 연산이고, `check_place`(부작용 없이 "될까?"만 확인)와 `free_positions`/`earliest_feasible`(어디에·언제 놓을 수 있는지 탐색)은 디코더가 다음 후보를 찾을 때 쓰는 조회 함수입니다. `objective()`가 0.3절의 Z1/Z2/Z3를 반환하고, `verify_full()`은 0.4절 Stage1~5 의미론을 코어 스스로 자체 재검증하는 안전장치입니다. **"상태 불변(read-only)"**이라고 표시된 함수들은 호출해도 현재 배치 상태를 바꾸지 않는다는 뜻 — 그래서 여러 후보를 자유롭게 "찔러볼" 수 있습니다(메타휴리스틱의 이웃해 탐색과 같은 패턴).

`solver` 계약:
- `core_iface.make_core(prob_info) -> CoreLike` - ogc_core 실패 시 fallback_core 반환. 두 구현은 위 API 완전 동일.
- `priority.atc_order(prob, t0, kappa, rng, bias_p) -> list[block]`
- `decoder.construct(core, prob, rng, cfg) -> Placements | None` (`Placements = list[tuple]`, get_placements 형식)
- `serialize.to_operations(placements) -> dict` - 제출 형식
- `budget.Budget(timelimit, safety=0.08)`: `.remain()`, `.phase(name, frac)`, `.expired()`
- `shell.solve(prob_info, timelimit) -> dict` - myalgorithm의 유일한 진입점

**같은 날(same-day) 순서 규약 (v0 고정)**: 같은 날 같은 bay의 EXIT들은 block id 오름차순, ENTRY들도 block id 오름차순으로 수행, 직렬화한다. 코어의 시간관계 판정도 동일 규약을 가정한다 (아래 Task 5 표).

> 💡 **설명:** 같은 날 여러 오퍼레이션이 겹칠 때 "어떤 순서로 실행됐다고 칠 것인가"는 임의로 정해야 하는 규칙입니다(문제 자체는 하루 단위 시간만 주어지고, 하루 안에서의 초 단위 순서는 정의 안 됨). 이 프로젝트는 "EXIT 먼저(같은 bay를 비워야 다음 블록이 들어올 자리가 생기니까), 그 안에서는 block id 순서"로 **일관되게 고정**하기로 정했습니다. 이 규칙이 왜 중요한지는 Task 5의 "주의 케이스"(같은 날 EXIT=ENTRY면 제약 없음)에서 다시 나옵니다.

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

> 💡 **설명 — 이 절은 도메인 지식이라기보다 "프로젝트 관리" 절입니다.** 4주짜리 계획을 미리 다 짜놓다 보니, 나중 주차 계획이 이번 주 계획에 "이런 API/설정을 추가해달라"고 요청한 이력들이 생겼고, 이 부록은 그 요청들을 한곳에 모아 **최종 확정판**으로 정리한 것입니다. 실행 관점에서 기억할 것은 딱 하나: "1주차 코드를 짤 때, 향후 몇 주차에서 이런 이름의 설정 클래스/환경변수/함수가 추가될 예정이니 이름이 겹치거나 어긋나지 않게 하라"는 사전 조율표입니다. 지금 당장 구현할 것은 없고(5번 항목은 "조건 충족 시에만" 구현하라는 뜻 — 지금은 만들지 말라는 것), 참고만 하면 됩니다.

---

### Task 1: 스캐폴딩 + pybind11 빌드 파이프라인

**Files:** Create: `core/CMakeLists.txt`, `core/src/bindings.cpp`, `solver/__init__.py`, `tests/conftest.py`, `pyproject.toml`(pytest 설정), `Makefile`
**Interfaces:** Produces: `import ogc_core; ogc_core.__version__ == "0.1.0"`, `make core` 빌드 명령.

> 💡 **설명 — "스캐폴딩(scaffolding)"**: 건축의 비계처럼, 아직 실제 기능은 없지만 "빌드가 되고 import가 된다"는 뼈대만 먼저 세우는 작업입니다. 이후 모든 Task가 이 뼈대 위에 기능을 하나씩 붙여나갑니다. TDD(테스트 주도 개발) 방식으로 진행되는 것을 볼 수 있는데, 매 Task마다 "① 실패하는 테스트를 먼저 쓴다 → ② 그 테스트를 통과시키는 최소 구현을 한다 → ③ 커밋한다" 순서를 반복합니다.

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

> 💡 **설명 — 빌드 관련 세부사항:**
> - `-O3 -march=x86-64-v3`: `-O3`는 GCC의 최고 수준 자동 최적화, `-march=x86-64-v3`는 AVX2 등 비교적 최신 CPU 명령어셋을 사용하도록 컴파일하라는 뜻(대회 서버 CPU가 이를 지원한다고 확인됨). 비트맵 연산(0.5절 언급) 같은 곳에서 속도 이득이 큽니다.
> - `-static-libstdc++ -static-libgcc`: C++ 표준 라이브러리를 실행파일/모듈 안에 통째로 넣어버리는 정적 링킹. 대회 서버에 어떤 버전의 libstdc++가 깔려있을지 모르니, "내가 컴파일한 그대로 어디서든 동작하게" 만드는 안전장치입니다.
> - `PYBIND11_MODULE(ogc_core, m)`: pybind11이 제공하는 매크로로, 이 C++ 파일이 Python에서 `import ogc_core`로 불러올 수 있는 모듈이 되도록 등록합니다. `m.attr("__version__")`은 Python에서 `ogc_core.__version__`으로 접근 가능한 속성을 만드는 것.
> - `pybind11_add_module`: CMake용 pybind11 헬퍼 함수로, 일반 공유 라이브러리(.so)가 아니라 Python이 바로 import할 수 있는 확장 모듈 형태로 빌드해줍니다.
> - `conda run -n ogc2026 ...`: `ogc2026`이라는 이름의 conda 가상환경 안에서 명령을 실행하라는 뜻. 이 프로젝트는 Python 3.12를 이 conda 환경에 고정해서 씁니다.

- [ ] **Step 4: 빌드, 테스트 통과 확인** - `make core && make test` -> PASS. `.so`는 `solver/`에 설치되어 `sys.path` 조작 없이 import.
- [ ] **Step 5: Commit** - `git add ... && git commit -m "build(ogc2026): pybind11 코어 빌드 파이프라인"`

### Task 2: 인스턴스 파싱 + ShapeTable (스케일 정수 변환)

**Files:** Create: `core/src/instance.hpp`, `core/src/instance.cpp`; vendor `core/third_party/nlohmann/json.hpp`. Modify: `core/src/bindings.cpp`. Test: `tests/test_api.py`
**Interfaces:** Produces: `Core(json_str)`, `.n_blocks/.n_bays/.processing/.release/.due/.num_orients`; C++ 내부 `struct ShapeTable { LayerPoly layers[shape_id][k] }`, `shape_id = shape_index(block, orient)`. **SCALE = 10000** (전 코어 공통 상수).

> 💡 **설명 — `ShapeTable`과 `shape_id`:** 하나의 블록은 여러 방향(orientation) 후보를 가지므로, "블록 몇 번의 몇 번째 방향"이라는 (block, orient) 쌍이 실제로 다루는 최소 단위(도형 하나)입니다. 코드에서 매번 이 쌍을 들고 다니는 대신, `shape_id = shape_index(block, orient)`라는 단일 정수로 압축해서 다룹니다(예: 배열 인덱싱, 캐시 키). `ShapeTable`은 이 `shape_id`별로 레이어별 폴리곤 정점 배열을 저장해두는 테이블입니다. **SCALE = 10000**은 0.앞 문단에서 설명한 "좌표 ×10000 정수화"의 실제 배율 값 — 문제 좌표가 소수 4자리까지이므로 10^4를 곱하면 정확히 정수가 됩니다.

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

> 💡 **설명 — 실제 인스턴스 JSON이 어떻게 생겼는지 감을 잡기 위한 예시** (`data/training_instances/train/prob_1.json`):
> ```python
> {
>   "name": "prob_1",
>   "bays": [{"width": 51, "height": 20}, {"width": 54, "height": 18}],   # bay 2개
>   "weights": {"w1": 29091, "w2": 7, "w3": 200},   # Z1/Z2/Z3 각각의 가중치
>   "blocks": [
>     {
>       "release_time": 0, "due_date": 14, "processing_time": 10, "workload": 238,
>       "bay_preferences": [0, 100],     # 두 번째 bay를 100% 선호
>       "shape": [
>         {"orientation": 0, "layers": [ [[0.0,0.0], [3.9166,-0.3357], ...] ]},  # 방향0: 레이어 1개
>         ... (방향 1~7)
>       ]
>     },
>     ...
>   ]
> }
> ```
> `weights.w1`이 압도적으로 커서(29091 vs 7, 200) 0.3절에서 말한 "Z1(지연시간) 지배" 현상이 여기서 수치로 확인됩니다. 2개 레이어를 가진 블록의 경우 위층(layer 1) 폴리곤이 아래층(layer 0)보다 정점이 적은 더 작은 도형인 경우가 흔합니다(위로 갈수록 좁아지는 형상). 두 레이어 모두 첫 정점이 `(0.0, 0.0)`으로 고정되어 있는 것도 실제로 확인됩니다 — 이게 "레퍼런스 포인트" 규칙입니다.

- [ ] **Step 2: 파서 구현.** 좌표 float -> `llround(v * SCALE)` (int64). 문제 좌표는 <=4자리 소수라 이 변환은 **정확**. 각 (block, orient)를 `shape_id`로 부여, 레이어별 정점 배열과 통합 bbox, 기준점(레이어0 첫 정점)이 (0,0)이 되도록 이미 JSON이 보장 - 검증 assert 포함. release/due/processing/workload/bay_preferences/bay W, H (SCALE 곱한 값)도 보관.
- [ ] **Step 3: 테스트 통과 확인** - `make test` -> PASS.
- [ ] **Step 4: Commit.**

### Task 3: 정확 overlap predicate (Clipper2) + shapely fuzz 대조

**Files:** vendor `core/third_party/clipper2/` (github.com/AngusJohnson/Clipper2 `CPP/Clipper2Lib`의 core/engine/minkowski 소스만, BSL-1.0 라이선스 파일 포함). Create: `core/src/geom.hpp`, `core/src/geom.cpp`. Test: `tests/test_geom_fuzz.py`
**Interfaces:** Produces (C++ 내부): `bool overlap_pos_area(const Path64& a, const Path64& b)` - 교차 면적>0이면 true, 경계 접촉만이면 false. 바인딩(테스트 전용): `ogc_core._overlap(list[[x,y]], list[[x,y]], scale_already=False) -> bool`.

> 💡 **설명 — "overlap predicate"란:** predicate(술어)는 참/거짓만 반환하는 함수를 말합니다. "이 두 폴리곤이 겹치는가?"라는 질문에 true/false로만 답하는 함수가 `overlap_pos_area`입니다. **"경계 접촉만이면 false"**가 중요한 규칙 — 두 블록이 변을 딱 맞대고 붙어있는 것(접촉)은 "겹침"이 아니라 허용되는 상태입니다(실제로 블록들을 빈틈없이 붙여 쌓아야 공간 효율이 좋아지므로). 그래서 "교차 면적이 0보다 크다"(단순히 교집합이 비어있지 않은 게 아니라, **면적**이 있어야 함)로 엄밀하게 정의합니다.
>
> **"fuzz 대조"**란 무작위(랜덤) 입력을 대량으로 생성해서, 우리 C++ 구현과 신뢰할 수 있는 기준(shapely)의 결과가 일치하는지 자동으로 비교하는 테스트 기법입니다(fuzz testing). 아래 코드에서 20,000개의 무작위 폴리곤 쌍·오프셋 조합에 대해 두 구현이 다른 답을 내는 경우("disagreement")가 0건이어야 한다고 요구합니다 — 사람이 일일이 케이스를 상상해서 테스트를 못 짤 정도로 경계 케이스가 많을 수 있는 기하 연산에서 표준적으로 쓰이는 검증 방법입니다.

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

> 💡 **설명 — `pa.buffer(0)`이 뭘 하는 코드인가:** shapely에서 관용적으로 쓰이는 트릭으로, "자기교차(self-intersection)가 있어 유효하지 않은 폴리곤"을 0만큼 팽창(buffer)시키면 부작용으로 자기교차가 없는 유효한 형태로 정리(정규화)됩니다. 아래 C++ 구현 설명에서 이와 동일한 의미를 `FillRule::NonZero`로 구현한다고 나옵니다.

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

> 💡 **설명:**
> - **`FillRule::NonZero`**: 폴리곤 내부/외부를 판정하는 규칙(fill rule)의 한 종류. 자기교차하는 복잡한 폴리곤이 주어져도 "감김 수(winding number)가 0이 아닌 영역"을 내부로 간주해 일관되게 처리합니다 — shapely의 `buffer(0)` 정규화와 실질적으로 같은 효과를 냅니다.
> - **"교집합이 퇴화(빈/선분)라 면적 0"**: 두 폴리곤이 점이나 선(1차원)에서만 만나면(= 변만 맞닿은 접촉), 그 교집합은 넓이가 없는 "퇴화한(degenerate)" 도형이 되어 면적이 정확히 0입니다. 그래서 "면적 > 0" 조건이 접촉과 진짜 겹침을 정확히 구분해줍니다.
> - **정수 좌표라 `Area`가 정확**: 실수 연산은 반올림 오차가 누적되지만, 정수 좌표로 계산된 면적(신발끈 공식 등)은 오차 없이 정확한 정수값이 나옵니다. 이게 "0.앞 문단"에서 설명한 정수 스케일링의 실질적 이득입니다.

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

> 💡 **설명 — 이 Task가 앞서 예고한 "NFP" 개념의 실제 구현체입니다.** `OffsetBitmap`은 "도형 A를 기준으로, 도형 B를 상대 오프셋 (dx, dy)에 놓았을 때 충돌하는가?"를 모든 (dx, dy) 조합에 대해 미리 계산해 비트 배열(1비트=1칸)로 저장한 자료구조입니다. 한 번 계산해두면 이후 `test(dx, dy)`는 배열 조회 한 번으로 끝나 매우 빠릅니다(비트 하나 확인 O(1)).
>
> - `static_bm(sa, sb)`: **정적 충돌** 비트맵 — 0.4절 j≥k 규칙에서 `j == k`(같은 층끼리)만 검사. "가만히 놓여있는 동안" 충돌 여부.
> - `entry_bm(sa, sb)`: **진입/퇴출 충돌** 비트맵 — `j ≥ k`(자기 층 및 그보다 높은 모든 층) 전부를 OR로 합침. "크레인이 내리거나 들어올릴 때 스쳐 지나가는 경로"까지 포함한 더 넓은 의미의 충돌.
> - **"lazy 계산 + 해시맵 캐시"**: 모든 (shape, shape) 조합을 미리 다 계산해두면 시간·메모리 낭비가 크므로, 실제로 필요할 때(처음 조회될 때)만 계산하고 결과를 캐시에 저장해 재사용합니다(lazy evaluation). "스레드 안전은 이번 주 불필요"는 지금은 프로그램이 단일 스레드로만 동작하니 여러 스레드가 동시에 캐시에 접근할 걱정을 안 해도 된다는 뜻(4주차쯤 병렬화가 들어오면 다시 고려해야 할 부분).
> - **"윈도 밖 오프셋은 false"**: 두 도형의 bounding box(둘러싸는 최소 사각형)가 너무 멀리 떨어지면 절대 겹칠 수 없으므로, 그 범위(window) 밖은 계산할 필요도 없이 항상 false로 취급합니다 — 계산량을 줄이는 최적화.

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

> 💡 **설명:** 테스트 코드의 `st = any(... for k in range(min(len(A), len(B))))`가 정확히 "같은 층 k끼리만 비교"(static, j==k)이고, `en = any(... for k in range(len(A)) for j in range(k, len(B)))`가 "k 이상의 모든 j와 비교"(entry, j≥k)입니다. 0.4절에서 설명한 규칙이 테스트 코드로 그대로 옮겨진 형태입니다.

- [ ] **Step 2: 구현** - 윈도: `dx in [bbox_a.min_x - bbox_b.max_x - 1, bbox_a.max_x - bbox_b.min_x + 1]`(스케일에서 계산 후 `floor/ceil / SCALE`, +/-1 여유), dy 동일. 각 정수 오프셋에서 `overlap_pos_area` per 레이어 조합, OR 집계. v0는 오프셋별 정확 테스트(쌍당 ~0.3-2ms) - **Minkowski 가속은 2주차 과제로 명시적 이연**.

> 💡 **설명 — "Minkowski 가속"이 뭔지, 왜 지금은 안 쓰는지:** 전통적인 NFP 계산법은 두 폴리곤의 민코프스키 합(A ⊕ (-B))을 기하학적으로 한 번에 계산해서 그 경계 자체가 곧 "충돌 시작 경계선"이 되는 방식입니다(오프셋 전수조사 없이 한 번의 기하 연산으로 전체 NFP를 구함). 이번 주(v0)는 구현 단순성을 위해 "모든 정수 오프셋 하나하나에 대해 직접 교차 검사"(brute-force, 쌍당 0.3~2ms)로 정확성부터 확보하고, 이 계산을 훨씬 빠르게 하는 민코프스키 합 기반 가속은 의도적으로 2주차로 미룹니다 — "먼저 정확하게, 그다음 빠르게"라는 전형적인 최적화 순서입니다.

- [ ] **Step 3: 테스트 통과 + 캐시 적중 시 재계산 없음 확인**(호출 카운터 assert).
- [ ] **Step 4: Commit.**

### Task 5: 시간 관계 -> 필요 비트맵 판정 (utils 의미론의 심장)

**Files:** Create: `core/src/state.hpp`, `core/src/state.cpp` (관계 판정 부분). Test: `tests/test_state_oracle.py`
**Interfaces:** Produces (C++ 내부): `RequiredBits required(const Placed& A, const Placed& B)` - 아래 표 구현. 이후 모든 feasibility는 이 함수를 유일 경로로 사용.

> 💡 **설명 — 이 Task의 별명이 "utils 의미론의 심장"인 이유:** 0.4절에서 설명한 Stage 2/3/4(진입 가능/퇴출 가능/체류 중 정적 겹침)를 "두 블록이 시간상 어떤 관계에 있는가"에 따라 "static 비트맵을 볼지, entry 비트맵을 볼지"로 통합해서 판정하는 단 하나의 함수(`required`)로 응축한 것이 이 Task입니다. 이 함수가 틀리면 이후 모든 배치 판정이 틀리게 되므로 "심장"이라 부릅니다.

**판정 표** (utils Stage2/3/5 + 같은 날 id-오름차순 규약에서 유도; `[e,x)` 반개구간, 모두 정수 일):

| 조건 (A 관점) | 필요 비트맵 |
|---|---|
| 구간 겹침 없음 (`eA >= xB or eB >= xA`) | 없음 |
| 겹침 | `static(A,B)` 항상 |
| A의 ENTRY 시점에 B 존재: `eB < eA < xB`, 또는 같은 날 진입 `eA == eB`이고 `idA > idB` | `entry(A,B)` |
| A의 EXIT 시점에 B 존재: `eB < xA < xB`, 또는 같은 날 퇴출 `xA == xB`이고 `idA < idB` | `entry(A,B)` (exit는 동일 수식) |
| 대칭으로 B의 이벤트가 A 존재 중이면 | `entry(B,A)` |

주의 케이스: A EXIT일 == B ENTRY일 (`xA == eB`) -> EXIT 먼저 규칙으로 **제약 없음**(구간도 안 겹침). `eA == xB`도 동일.

> 💡 **설명 — 이 표를 말로 풀면:**
> 1. 두 블록의 체류 기간이 아예 안 겹치면 → 서로 신경 쓸 필요 없음(같은 공간을 다른 시간에 쓰는 것뿐).
> 2. 체류 기간이 겹치면 → 최소한 "가만히 있을 때 안 겹치는지"(static)는 항상 확인해야 함.
> 3. 추가로, A가 들어오는(ENTRY) 순간에 B가 이미 그 자리에 있다면 → A 입장에서 크레인 진입 스윕 충돌(entry)까지 확인. 같은 날 동시에 들어오는 경우엔 "id가 큰 쪽(나중 순번)"이 "id 작은 쪽이 이미 있다"고 보고 검사합니다(같은 날 오름차순 규약).
> 4. A가 나가는(EXIT) 순간에 B가 있어도 마찬가지로 entry 비트맵을 봄(진입/퇴출이 대칭적인 크레인 동작이라 같은 비트맵을 재사용).
> 5. 반대 방향(B가 들어오거나 나갈 때 A가 존재)도 대칭적으로 똑같이 검사.
>
> **"주의 케이스"**가 헷갈리기 쉬운데: A가 나가는 날과 B가 들어오는 날이 정확히 같다면(`xA == eB`), 애초에 "구간 겹침"조차 없는 것으로 취급합니다(반개구간 `[e,x)` 정의상 A의 점유는 `xA` 미만까지, B의 점유는 `eB`부터인데 `xA==eB`이므로 겹치는 시각이 없음) — 그리고 "같은 날엔 EXIT을 ENTRY보다 먼저 실행한다"는 규약 덕분에 실제로도 A가 완전히 빠져나간 뒤 B가 들어오는 것이니 물리적으로도 문제 없습니다.

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

> 💡 **설명:** 여기서 "치명적"과 "보수적"의 구분이 Global Constraints 절에서 말한 "미세하게 보수적이어야 함" 원칙이 실제 테스트로 구현된 부분입니다. `ours and not theirs`(우리는 OK라 했는데 실제 심판은 거부) 케이스만 0건이어야 하고, 반대 방향(우리가 더 깐깐하게 거부한 경우)은 허용됩니다 — 손해는 봐도 실격은 안 나는 안전한 방향이기 때문입니다.

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

> 💡 **설명 — `free_positions`의 비트보드 알고리즘, 그림으로 이해하기:** "비트보드(bitboard)"는 체스 엔진 등에서 흔히 쓰이는 기법으로, 2D 격자의 각 칸을 비트 하나로 표현해(64개씩 묶어 `uint64_t` 배열) 여러 칸에 대한 연산(AND/OR/시프트)을 CPU 명령어 한 번에 처리하는 방법입니다.
>
> 절차: (1) 새 블록을 놓을 수 있는 후보 좌표 범위 전체를 "전부 0(=아직 막힌 곳 없음)"인 비트보드로 초기화합니다. (2) 이미 배치되어 있고 시간이 겹치는 블록들 하나하나에 대해, Task 4에서 만든 `OffsetBitmap`(그 블록과 새 블록 사이의 "여기 두면 충돌하는 오프셋들")을 가져와, 그 블록의 실제 위치만큼 이동(shift)시킨 뒤 전체 비트보드에 OR로 합쳐 넣습니다. (3) 모든 기존 블록을 다 처리하고 나면, 비트가 0으로 남은 칸들이 "누구와도 충돌하지 않는 좌표" 즉 `free_positions`입니다.
>
> 이 방식이 빠른 이유는 "블록 하나하나에 대해 매번 새로 기하 계산"을 안 하고, 미리 계산해둔 비트맵을 그냥 밀어서(shift) OR만 하면 되기 때문입니다 — 0.앞 문단에서 말한 "us급 배치 검사"가 여기서 실현됩니다.
>
> **`earliest_feasible`**: "가장 이른 가능 시각"을 찾는 함수로, 후보 시각 `t`를 하나씩 늘려가며(t_min부터) 그 시각의 `free_positions`가 비어있지 않은 첫 시각을 찾습니다. 그 시각에 여러 좌표가 가능하다면 "(y, x) 최소"(즉 아래쪽, 그다음 왼쪽) 좌표를 고릅니다 — 이게 바로 **BLF(Bottom-Left-Fill)** 원칙의 실제 구현입니다(0.5절 예고 참고): 항상 "가능한 한 왼쪽 아래"를 우선하는 타이브레이크 규칙입니다.

- [ ] **Step 3: 브루트포스 대조 + 통과.**
- [ ] **Step 4: 마이크로벤치** - `tests/test_api.py::test_perf_smoke`: 50블록 배치 상태에서 `check_place` 100k회 wall time < 1.0s (개당 <10us), `free_positions` 1k회 < 1.0s. 미달 시 프로파일 후 비트보드 경로 점검 (목표 미달 상태로 커밋 금지).

> 💡 **설명 — "마이크로벤치(micro-benchmark)"**: 함수 하나를 반복 실행해서 걸리는 시간을 재는 소규모 성능 측정. `<10us`(10마이크로초 미만)라는 구체적 목표를 세운 건, 뒤에 나올 디코더(Task 12)가 배치 하나를 결정할 때 이 함수를 수천~수만 번 호출할 것이기 때문에, 개별 호출이 느리면 전체 알고리즘이 시간예산(Task 13) 안에 못 끝날 위험이 있어서입니다.

- [ ] **Step 5: Commit.**

### Task 7: objective + verify_full + pair_bits_at_current

**Files:** Modify: `core/src/state.cpp`, `core/src/bindings.cpp`. Test: `tests/test_objective.py`
**Interfaces:** Produces: `.objective()`, `.verify_full()`, `.pair_bits_at_current()` (계약 참조).

- [ ] **Step 1: 실패 테스트** - baseline greedy로 prob_1 해를 만들어 `utils.check_feasibility`의 (obj1,obj2,obj3)와 코어 `.objective()` 일치(+/-1e-6) assert. `verify_full()`은 feasible 해에서 `[]`, 고의로 겹친 배치에서 비어있지 않음 assert.
- [ ] **Step 2: 구현** - utils 수식 그대로: Z1=sum(max(0, exit-due)), Z2=floor(max pair |u*load - u*load|) (u=평균면적/면적), Z3=sum(Smax-S). `verify_full`은 배치 전체를 required() 경로로 재검 + Stage1 항목(전 블록 배치 여부는 호출자 책임이므로 배치된 것만).

> 💡 **설명:** 0.3절에서 이미 Z1/Z2/Z3의 의미를 설명했으니, 여기서는 수식 표기만 짚습니다. `Z2 = floor(max pair |u*load - u*load|)`에서 `u`(=평균 bay 면적 / 해당 bay 면적)는 "면적이 작은 bay는 가중치를 더 크게 줘서, 작은 공간에 부하가 몰리는 걸 더 민감하게 벌점 매기는" 정규화 계수입니다. `pair_bits_at_current()`는 Task 4의 원시 충돌 비트 정보를 현재 배치 상태 기준으로 꺼내주는 조회 함수인데, 지금 당장 쓰이진 않고 **3주차 CP-SAT 재타이밍**(0.5절에서 예고한 "위치는 고정, 시간만 재조정") 단계의 입력으로 미리 준비해두는 것입니다.

- [ ] **Step 3: 통과 확인 -> Commit.**

### Task 8: 순수 Python 폴백 코어 + 동등성 테스트

**Files:** Create: `solver/core_iface.py`, `solver/fallback_core.py`. Test: `tests/test_fallback_parity.py`
**Interfaces:** Produces: `core_iface.make_core(prob_info) -> CoreLike` (환경변수 `OGC_FORCE_FALLBACK=1`로 강제 폴백).

> 💡 **설명 — 왜 굳이 느린 Python 버전을 또 만드는가:** 대회 서버 환경에서 C++ 코어(`ogc_core.so`)가 어떤 이유로든(호환 안 되는 CPU 명령어, 빌드 실패 등) 로드되지 않을 위험에 대비한 **안전망**입니다. `fallback_core.py`는 shapely로 똑같은 API를 구현하되 느려도 괜찮게 만들어서, C++ 코어가 죽어도 "정답은 낼 수 있는" 상태를 보장합니다. `make_core`가 자동으로 어느 쪽을 쓸지 고르고, `OGC_FORCE_FALLBACK=1`은 개발 중 일부러 폴백 경로를 테스트하기 위한 스위치입니다. "동등성 테스트(parity test)"는 두 구현이 똑같은 입력에 똑같은 결과를 내는지 확인하는 것 — Task 3의 fuzz 테스트와 같은 신뢰 전략의 연장선입니다.

- [ ] **Step 1: 실패 테스트** - 동일 무작위 배치 시퀀스 300개에 대해 ogc_core와 fallback의 `place` 성공/실패열과 `objective()` 일치 assert.
- [ ] **Step 2: 구현** - fallback은 shapely로 Task 5의 표를 그대로 구현(utils의 check_entry/check_exit/check_collisions 재사용 가능 - baseline/utils.py를 import해서 호출). 속도 무관, 의미 동일이 목표.
- [ ] **Step 3: 통과 -> Commit.**

### Task 9: 직렬화 (operations dict)

**Files:** Create: `solver/serialize.py`. Test: `tests/test_serialize.py`
**Interfaces:** Produces: `to_operations(placements) -> dict` - same-day 규약(EXIT 전량 먼저, 각각 id 오름차순) 준수.

> 💡 **설명 — "직렬화(serialize)"**: 코어가 다루는 내부 표현(`(block, bay, x, y, orient, entry, exit)` 튜플 리스트)을, 대회가 요구하는 제출 형식인 `{"operations": {"날짜": [ {type, block_id, ...}, ... ]}}` 형태의 JSON 딕셔너리로 변환하는 작업입니다. Task 5 테스트에서 이미 봤던 `_mk_solution` 헬퍼 함수를 정식 모듈로 승격한 것이 이 Task입니다.

- [ ] **Step 1: 실패 테스트** - 임의 feasible placements(코어로 생성)를 직렬화해 `utils.check_feasibility(prob, sol)["feasible"] is True` assert; 같은 날 EXIT/ENTRY 혼합 케이스 포함.
- [ ] **Step 2: 구현** (Task 5 테스트의 `_mk_solution`을 정식 모듈로 이동, x, y `int()` 강제).
- [ ] **Step 3: 통과 -> Commit.**

### Task 10: 최후 폴백 - 순차 단독 배치 솔버

**Files:** Create: `solver/trivial.py` (`solve_trivial(core, prob) -> Placements`). Test: `tests/test_shell.py`
**Interfaces:** Produces: 항상 feasible한 placements (품질 무관, 최후 보루).

> 💡 **설명 — "3단 폴백 셸" 중 가장 아래 단:** 원본 서두의 "3단 폴백 셸"이 정확히 뭘 가리키는지 이제 감이 오실 텐데, 대략 이런 계층 구조입니다: **① 정교한 방법(디코더+ALNS 등, 품질 최고, 실패 가능성 있음) → ② 그보다 단순한 구성 방법 → ③ `trivial` 최후 보루(품질은 최악이지만 절대 실패하지 않음)**. 이 Task가 만드는 게 그 마지막 ③단계입니다. 목표는 "무조건 실행가능한 해를 낸다"는 것 하나뿐이고, 목적값(Z1/Z2/Z3)이 나쁜 건 감수합니다 — 대회에서 "아무 해도 못 냄(0점/실격)"보다는 "나쁜 해라도 냄"이 항상 낫기 때문입니다.

- [ ] **Step 1: 실패 테스트** - 40개 인스턴스 전부: `solve_trivial` 결과 -> serialize -> utils feasible assert (전 인스턴스 합계 수 분 내).
- [ ] **Step 2: 구현** - bay마다 시간 직렬화: 블록을 release 오름차순으로 선호 bay에 배정, `entry = max(R, 그 bay 직전 블록 exit)`, 위치는 빈 bay 기준 `free_positions` 첫 후보(경계만 검사되는 상태), `exit = entry + P`. 같은 bay에서 시간이 절대 겹치지 않으므로 페어 제약 자동 충족. 어떤 bay에도 못 들어가는 블록 발견 시 예외(로드 시 사전 검증).

> 💡 **설명:** 핵심 아이디어는 "같은 bay 안에서는 블록들이 시간상 절대 겹치지 않게(한 번에 하나씩만) 순차적으로 넣었다 뺐다 한다"는 것입니다. 시간이 아예 안 겹치면 Task 5의 판정 표에서 "구간 겹침 없음 → 제약 없음" 행이 항상 적용되므로, 애초에 충돌 판정 자체가 필요 없어져 반드시 feasible해집니다 — 품질을 포기하는 대신 정확성을 100% 보장하는 전략입니다.

- [ ] **Step 3: 통과 -> Commit.**

### Task 11: ATC 우선순위 + biased randomization

**Files:** Create: `solver/priority.py`. Test: `tests/test_decoder.py`
**Interfaces:** Produces: `atc_order(prob, t0=0.0, kappa=3.0, rng=None, bias_p=0.25) -> list[int]` - rng=None이면 결정적 ATC 순, rng 지정 시 기하분포 편향 샘플링.

> 💡 **설명 — ATC(Apparent Tardiness Cost) 규칙, 제대로 풀어보기:** 병렬기계 스케줄링에서 아주 표준적으로 쓰이는 디스패칭(우선순위) 규칙입니다. 두 극단적 규칙을 절충합니다:
> - **SPT(Shortest Processing Time, 최단작업우선)**: 처리시간이 짧은 작업을 먼저 — 전체 완료시간 합을 줄이는 데 유리.
> - **EDD(Earliest Due Date, 최소납기우선)**: 납기가 급한 작업을 먼저 — 최대지연을 줄이는 데 유리.
>
> ATC는 이 둘을 지수함수로 섞습니다: `urgency = (1/processing_time) * exp(-slack / (kappa * 평균처리시간))`. `slack`(여유시간)이 거의 0에 가까운(납기가 임박한) 작업일수록 `exp(-slack/...)` 항이 1에 가까워져 우선순위가 급격히 올라가고, 여유가 많은 작업은 이 항이 0에 가까워지며 `1/processing_time`(SPT 성향)이 상대적으로 더 지배합니다. `kappa`는 "얼마나 급해야 특별 취급할지"를 조절하는 민감도 파라미터입니다. 0.3절에서 "Z1(지연시간)이 지배적"이라 했던 것과 바로 연결되는 대목 — baseline의 단순 EDD보다, "슬랙 0에 가까운 블록을 더 공격적으로 우선 배치"하는 ATC가 지연시간을 더 잘 줄일 것으로 기대하는 설계입니다.
>
> **biased randomization(편향 랜덤화)**: `rng`가 주어지면 결정적 순서를 그대로 쓰지 않고, 기하분포(geometric distribution)로 "순위 리스트에서 몇 번째를 뽑을지"를 확률적으로 정합니다. 기하분포는 "앞쪽일수록 뽑힐 확률이 기하급수적으로 높고, 뒤쪽은 낮지만 0은 아닌" 분포라서, 결과적으로 "원래 우선순위를 대체로 존중하되, 가끔 순서를 바꿔보는" 효과를 냅니다. `bias_p`가 클수록 원래 순위를 더 강하게 고수합니다(앞쪽만 거의 뽑힘). 이 기법은 GRASP(Greedy Randomized Adaptive Search Procedure)에서 "RCL(Restricted Candidate List)에서 편향된 확률로 뽑는" 것과 정확히 같은 목적 — **멀티스타트(multi-start)**를 위해 매번 살짝 다른 (하지만 여전히 그럴듯한) 순서를 만들어내는 것입니다. Task 13의 멀티스타트 셸이 이 함수를 시드마다 다르게 호출해서 여러 후보 해를 만듭니다.

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

> 💡 **설명 — 왜 "동급이면 큰 블록 먼저"인가:** 2D 패킹의 오래된 경험칙으로, 큰 도형을 먼저 배치해야 작은 도형들이 나중에 남은 자잘한 빈틈을 채우기 쉬워집니다(반대로 작은 것부터 채우면 큰 도형이 들어갈 자리가 안 남을 위험). `urg`(ATC 긴급도)가 비슷한 블록끼리는 이 면적 가중치로 순서를 한 번 더 다듬는 타이브레이커입니다.

- [ ] **Step 3: 통과 -> Commit.**

### Task 12: time-first BLF 구성 디코더

**Files:** Create: `solver/decoder.py`. Test: `tests/test_decoder.py`
**Interfaces:** Produces: `construct(core, prob, rng, cfg=DecoderCfg()) -> Placements | None`. `DecoderCfg(kappa=3.0, bias_p=0.25, max_delay=None, bay_order="pref")`.

> 💡 **설명 — "디코더(decoder)"란 메타휴리스틱 용어로 익숙하실 개념:** 우선순위 순서(Task 11의 `atc_order` 결과)라는 "설계도"를 받아서, 그 순서대로 블록을 하나씩 실제 좌표·시간에 배치해나가며 "구체적인 해"로 변환하는 함수입니다. NEH나 랜덤키(random-key) 기반 유전 알고리즘에서 "순서 리스트 → 디코더 → 실제 스케줄"로 가는 것과 완전히 같은 패턴입니다. "time-first BLF"는 디코더가 각 블록에 대해 "가능한 가장 이른 시각부터" 먼저 훑고, 그 시각에서 BLF(왼쪽아래우선) 방식으로 위치를 고른다는 뜻 — 즉 시간을 바깥쪽 루프, 위치를 안쪽 루프로 탐색합니다.

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

> 💡 **설명 — 이 알고리즘을 한 문장으로 요약하면:** "우선순위 순서대로 블록을 하나씩 꺼내서, 각 블록에 대해 가능한 가장 이른 시각(t)부터 훑어가며, 그 시각에서 (지각시간, y좌표, x좌표, 선호도손실) 기준으로 가장 좋은 (bay, 위치, 방향)을 사전식(lexicographic)으로 골라 즉시 확정하는" 그리디(greedy) 구성 알고리즘입니다. `cand = (tard, y, x, _pref_loss(...))`처럼 튜플로 비교하는 건 파이썬에서 "지각시간을 최우선으로 비교하고, 같으면 y, 그다음 x, 그다음 선호도손실 순으로 비교"하는 사전식 정렬(lexicographic comparison)을 표현하는 관용적 방법입니다 — 튜플의 `<` 연산자가 자동으로 이렇게 동작합니다.
>
> **"지각 0이면 즉시 확정"** 규칙: 완벽한 시각을 더 찾아 헤매지 않고, 지각이 아예 없는 배치를 찾자마자 그리디하게 확정합니다(다른 블록들도 배치해야 하니 무한정 최적을 찾을 순 없음). **"t > R + 3이면 확정"**은 지각 0인 시각을 못 찾아도 "release_time으로부터 딱 3일까지만" 더 미뤄보고 그 안에서 최선을 확정합니다 — 이 3이라는 상수(`max_delay`)는 실험으로 조정할 하이퍼파라미터로 노출해뒀습니다. 이건 "탐욕적이되 무한 탐색은 하지 않는" 전형적인 시간예산 트레이드오프입니다.
>
> `_bays_by_pref`는 0.2절에서 설명한 `bay_preferences`(합계 100인 점수)가 높은 순으로 bay를 먼저 시도하게 정렬합니다. `_pref_loss`는 0.3절 Z3 공식(`최선호 점수 - 실제 배정 점수`) 그대로입니다.
>
> **결측 지식 채우기 — "왜 `earliest_feasible(blk, bay, o, t, t)`처럼 t_min=t_max=t로 호출하는가":** Task 6에서 정의한 `earliest_feasible(t_min, t_max)`는 원래 범위 안에서 가능한 가장 이른 시각을 찾는 함수인데, 여기서는 바깥 for 루프가 이미 `t`를 하나씩 늘려가며 돌고 있으므로, 매번 "정확히 이 t 하루만" 가능한지 검사하도록 범위를 t 하나로 좁혀서 호출합니다. 즉 바깥 루프(디코더)가 시간 탐색을, 안쪽 호출(코어)이 그 시각에서의 공간 탐색만 담당하도록 역할을 나눈 것 — 이게 "time-first"라는 이름의 이유입니다.

- [ ] **Step 3: 40개 인스턴스 스모크** - `construct` 성공률 및 utils feasible 100%, prob_1/23 obj 기록. 실패 인스턴스는 원인(밀도/크레인) 로그 남기고 max_delay 확대로 재시도.
- [ ] **Step 4: Commit.**

### Task 13: 시간예산 + 멀티스타트 셸 + myalgorithm

**Files:** Create: `solver/budget.py`, `solver/shell.py`, `submission/myalgorithm.py`. Test: `tests/test_shell.py`
**Interfaces:** Produces: `shell.solve(prob_info, timelimit) -> dict`; `Budget` (계약 참조). **2주차 ALNS는 shell의 `improve(core, incumbent, budget)` 훅 자리에 끼워진다** - 이번 주는 pass-through.

> 💡 **설명 — 이 Task가 원본 서두에서 말한 "견고성 셸" 전체의 최종 조립입니다.** `Budget`은 "주어진 시간제한(timelimit) 중 몇 %를 어느 단계에 쓸지" 관리하는 시간 예산 관리자입니다(`safety=0.08`은 실제 채점 서버의 오버헤드·오차에 대비해 8%를 안전마진으로 남겨둔다는 뜻 — 시간제한을 딱 맞춰 쓰다가 초과해서 실격되는 것을 방지). `solve()`는 Task 11(우선순위)+Task 12(디코더)로 여러 시드에 대해 반복 시도하는 **멀티스타트(multi-start)** 방식으로 여러 해를 만들고 그중 최선을 고르는 구조입니다 — 메타휴리스틱에서 "여러 번 다르게 시작해서 제일 나은 것을 취한다"는 표준 전략입니다. `improve(core, incumbent, budget)` 훅은 2주차에 ALNS(0.5절 예고)가 끼워질 자리를 미리 마련해두는 것 — "incumbent"는 메타휴리스틱 용어로 "현재까지의 최선해"를 뜻합니다.

- [ ] **Step 1: 실패 테스트** - (1) `solve(prob1, 30)`이 25초+/-5 내 반환, utils feasible, obj <= 단일 construct. (2) `OGC_FORCE_FALLBACK=1`에서도 feasible 반환(느려도 폴백 체인 작동). (3) decoder가 예외를 던지도록 몽키패치해도 trivial 폴백으로 feasible 반환.
- [ ] **Step 2: 구현** - `solve`: Budget 생성(safety 8%) -> core 생성(실패 시 fallback) -> trivial 해 확보(=안전망) -> 남은 예산 동안 멀티스타트 `construct`(시드 순회, 최선 갱신) -> 최종 utils.check_feasibility 통과 확인 후 반환(실패 시 하위 해로 강등) -> 전체 try/except로 감싸 어떤 예외도 trivial 해 반환. `submission/myalgorithm.py`:

```python
def algorithm(prob_info, timelimit=60):
    from solver.shell import solve
    return solve(prob_info, timelimit)
```
(제출 zip 스테이징 시 solver/, ogc_core.so를 루트 상대 경로로 복사 - 4주차 패키징 과제에서 자동화.)

> 💡 **설명 — 지금까지 만든 3단 폴백이 실제로 이렇게 겹칩니다:** 코어 자체가 안 뜨면(C++ 로드 실패) → Task 8의 Python 폴백 코어로 대체. 그다음, 정교한 구성(Task 12 `construct`)이 실패하거나 예외를 던지면 → Task 10의 `trivial` 최후 보루로 대체. `try/except`로 셸 전체를 감싸서 "어떤 예상 못한 에러가 나도 최소한 trivial 해는 반환한다"는 최종 안전장치까지 두었습니다 — 대회에서 "런타임 에러로 0점"이 나오는 최악의 시나리오를 막기 위한 설계입니다. `submission/myalgorithm.py`가 대회 채점 시스템이 실제로 호출하는 단일 진입점(`algorithm(prob_info, timelimit)`)이고, 그 내부에서 지금까지 만든 모든 로직(`shell.solve`)을 그냥 위임 호출합니다.

- [ ] **Step 3: 통과 -> Commit.**

### Task 14: 벤치 하네스 + 1주차 완료 기준 측정

**Files:** Create: `experiments/run_bench.py`, `experiments/results/` (gitignore에 raw 추가). Test: 수동 실행.
**Interfaces:** Produces: `python experiments/run_bench.py --timelimit 60 --out experiments/results/week1.json` - 인스턴스별 {obj, obj1/2/3, runtime, feasible, seed} JSON. **2~4주차 하네스가 이 CLI/스키마를 확장한다.**

> 💡 **설명 — "벤치 하네스(bench harness)"**: 여러 인스턴스에 대해 알고리즘을 자동으로 반복 실행하고 결과(목적값, 실행시간, feasible 여부 등)를 표준화된 형식으로 모아주는 실험 자동화 스크립트입니다. 지금까지 만든 모든 컴포넌트(코어, 디코더, 셸)를 40개 실제 인스턴스에 대해 한 번에 돌려서 "이번 주 목표(Goal)를 달성했는가"를 최종적으로 수치로 확인하는 단계입니다.

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

> 💡 **설명 — "완료 기준"이 이번 주 전체의 채점표입니다:** ① 40개 인스턴스 전부 feasible(하나라도 실패하면 안 됨), ② 목적값 합계가 baseline(EDD+Greedy)보다 낮아야 함(이번 주 Goal 그대로), ③ 배치 판정 속도가 개당 10마이크로초 미만(Task 6 벤치 목표), ④ 지금까지 만든 모든 fuzz/oracle 테스트(Task 3, 5)가 전부 통과. 이 네 가지가 모두 충족되어야 "green"이라 표현하고, 2주차(ALNS)로 넘어갈 준비가 된 것으로 봅니다.

## Self-Review 체크 결과

- 스펙 4절 L1(NFP 비트맵, 점유, API) -> Task 2~7. 4절 L2(ATC, BLF) -> Task 11~12. 4절 L5(예산, 폴백, utils 검증) -> Task 10, 13. 6절(fuzz, oracle, 벤치) -> Task 3, 5, 14. 7절 1주차 완료 기준 -> Task 14. 커버리지 갭 없음.
- Minkowski 가속, 병렬화, ALNS는 의도적으로 2주차 이연 (7절 로드맵과 일치).
- 타입/시그니처: 계약 섹션과 각 Task Interfaces 일치 확인.

> 💡 **설명 — 마지막 절은 계획 작성자 스스로 하는 "자체 검토(self-review)"입니다.** "설계서(스펙) 몇 절이 이 계획의 어느 Task로 구현되는지" 하나하나 대조해서, 빠뜨린 요구사항(커버리지 갭)이 없는지 확인한 기록입니다. "L1, L2, L5" 같은 표기는 설계서(`docs/superpowers/specs/2026-07-02-ogc2026-strategy-design.md`)에서 정의한 아키텍처 레이어 번호를 가리킵니다(L1=기하 코어, L2=구성 디코더, L3=ALNS, L4=매트휴리스틱, L5=견고성 셸 정도로 추정 — 정확한 레이어 정의가 궁금하면 그 설계서 문서를 함께 봐도 좋습니다).

---

## 부록: 이 문서에서 다룬 용어 색인

| 용어 | 처음 나온 곳 |
|---|---|
| Block, Bay, ENTRY/EXIT, Layer, Orientation, bay_preferences | 0.2 |
| Z1/Z2/Z3 목적함수 | 0.3 |
| Stage 1~5, j≥k 크레인 스윕 규칙 | 0.4 |
| SCALE 정수 스케일링 | Architecture, Task 2 |
| Clipper2 / overlap predicate / FillRule::NonZero | Task 3 |
| OffsetBitmap / static_bm / entry_bm (NFP 비트맵) | Task 4 |
| required() 판정 표 (same-day 규약 포함) | Task 5 |
| 비트보드 free-position 마스크 / earliest_feasible / BLF | Task 6 |
| 순수 Python 폴백 코어 | Task 8 |
| trivial 최후 보루 | Task 10 |
| ATC 우선순위 규칙 / biased randomization | Task 11 |
| time-first BLF 디코더 / 사전식(lexicographic) 비교 | Task 12 |
| Budget / 멀티스타트 / incumbent / ALNS 훅 | Task 13 |
| 벤치 하네스 | Task 14 |
