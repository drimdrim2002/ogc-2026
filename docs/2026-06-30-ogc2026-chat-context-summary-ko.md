# 2026-06-30 OGC 2026 대화 요약

이 문서는 다른 Codex 채팅에서 현재 대화 맥락을 이어받기 위한 참고 요약입니다.

## 배경

사용자는 OGC 2026 문제를 분석하고 있으며, CVRPTW 문제를 메타휴리스틱 방식으로 푸는 경험이 있다. 현재 문제도 같은 관점, 즉 feasible insertion, repair, LNS/ALNS 방식으로 접근하는 방향에 동의했다.

참고한 주요 로컬 문서와 파일은 다음과 같다.

- `docs/ogc2026_problem_statement_analysis_ko.md`
- `baseline/myalgorithm.py`
- `baseline/baseline_greedy.py`
- `baseline/utils.py`
- `data/train`
- `data/train 2`

## 문제 이해

OGC 2026은 조선소 block을 여러 bay에 배치하고 스케줄링하는 문제다. 각 block에 대해 다음 결정을 동시에 해야 한다.

- 어느 bay에 배정할지
- bay 안의 어느 정수 좌표 `(x, y)`에 둘지
- 어떤 방향 `orient_idx`를 선택할지
- 언제 bay에 넣을지 `ENTRY`
- 언제 bay에서 뺄지 `EXIT`

어려운 점은 공간과 시간이 강하게 결합되어 있다는 것이다. 단순히 같은 시간에 polygon이 겹치지 않는지만 보면 부족하고, `ENTRY`와 `EXIT` 순간에 crane이 block을 수직으로 넣고 뺄 수 있는지도 검사해야 한다.

## 현재 데이터에서 확인한 특징

`data/train`과 `data/train 2`를 훑어본 결과:

- 총 40개 인스턴스
- 총 7,500개 block
- block 수는 인스턴스당 100~300개
- bay 수는 2~5개
- 방향 수는 대부분 8개
- layer 수는 1~4개
- `due_date - release_time - processing_time` slack 중앙값은 2
- slack 3 이하 block은 5,748 / 7,500개

따라서 train 데이터는 일정이 매우 빡빡하다. 좋은 해를 만들려면 예쁜 packing보다 먼저 `entry ~= release_time`, `exit ~= entry + processing_time`에 가까운 feasible schedule을 만드는 것이 중요하다.

## 연구 방향 요약

관련 연구는 크게 세 축으로 정리했다.

1. 불규칙 다각형 packing / nesting
   - No-Fit Polygon, raster/scanline, bottom-left fill, local search 등이 관련된다.
   - OGC에서는 전체 문제를 exact MIP로 풀기보다, collision 후보를 줄이고 위치 후보를 만드는 데 이 아이디어를 쓰는 것이 현실적이다.

2. time-space allocation
   - berth allocation처럼 시간과 위치를 함께 정하는 문제와 닮아 있다.
   - 단, OGC는 berth의 1D 위치가 아니라 bay 내부 2D irregular polygon placement라 더 어렵다.

3. LNS/ALNS 기반 메타휴리스틱
   - 초기 feasible solution을 만든 뒤 일부 block을 제거하고 다시 삽입한다.
   - CVRPTW의 destroy-repair 구조와 가장 유사하다.

## 현재 프로젝트 코드 구조

### `baseline/myalgorithm.py`

현재 제출 진입점이다.

현재는 매우 얇은 wrapper로, 실제로는 `baseline_greedy.greedyalgorithm(prob_info, timelimit)`를 호출하고 끝난다.

향후 LNS/ALNS를 붙인다면 이 파일이 orchestrator 역할을 하게 된다.

예상 흐름:

```text
1. greedyalgorithm으로 초기 feasible solution 생성
2. solution을 assignment 형태로 복원
3. 일부 block destroy
4. 제거한 block을 다시 insertion
5. check_feasibility로 feasible/objective 확인
6. objective가 좋아지면 accept
7. timelimit까지 반복
```

### `baseline/baseline_greedy.py`

현재 solver의 핵심이다. CVRPTW 관점으로 보면 insertion heuristic에 해당한다.

중요 함수:

- `greedyalgorithm(...)`
  - 전체 greedy solver
  - block을 정렬한 뒤 순서대로 배치한다.
- `_place_blocks(...)`
  - 각 block에 대해 `(bay, x, y, orient, entry, exit)` 후보를 평가하고 가장 좋은 후보를 commit한다.
- `_candidate_positions(...)`
  - bay 안의 후보 정수 좌표를 만든다.
  - 현재는 bottom-left / bbox contact 기반으로 후보를 만든다.
- `_find_earliest_slot(...)`
  - 특정 위치와 방향이 주어졌을 때 가능한 earliest `entry, exit`를 찾는다.
- `_placement_score(...)`
  - tardiness, workload balance, bay preference penalty를 합쳐 후보 점수를 계산한다.
- `_repair(...)`
  - infeasible solution이 나오면 문제 block을 다시 배치한다.
- `_force_place(...)`
  - 끝까지 안 되는 block을 empty-bay window에 강제로 넣어 feasibility를 확보한다.

### `baseline/utils.py`

이 파일은 feasibility oracle이다. CVRPTW의 시간창/용량 feasibility checker와 같은 위치에 있다.

중요 클래스와 함수:

- `Bay`
  - bay 크기와 block 포함 여부 검사
- `Block`
  - 특정 `(x, y, orient_idx)`에 놓인 block
  - layer polygon을 world coordinate로 변환해 캐시한다.
- `check_collisions(...)`
  - 같은 bay, 같은 시간에 존재하는 block끼리 같은 layer polygon이 겹치는지 검사한다.
- `check_entry(...)`
  - crane이 block을 bay에 넣을 수 있는지 검사한다.
  - `j >= k` layer rule을 사용한다.
- `check_exit(...)`
  - crane이 block을 bay에서 뺄 수 있는지 검사한다.
  - entry와 동일하게 `j >= k` layer rule을 사용한다.
- `check_feasibility(...)`
  - 완성된 solution을 5단계로 검증하고 objective를 계산한다.

`check_feasibility`의 단계:

```text
Stage 1: 모든 block이 정확히 한 번 ENTRY/EXIT 되었는가
Stage 2: 각 ENTRY 시점에 crane entry가 가능한가
Stage 3: 각 EXIT 시점에 crane exit가 가능한가
Stage 4: 시간 겹치는 block 간 layer collision이 없는가
Stage 5: operation을 시간순으로 replay해도 가능한가
```

## 추천 접근 5개를 현재 프로젝트 기준으로 해석

### 1. Feasibility-first greedy builder

현재 `_place_blocks`가 이 역할을 한다.

각 block을 하나씩 보면서 가능한 후보만 남긴다.

```text
block 선택
-> bay 후보
-> orientation 후보
-> x,y 후보
-> entry/exit 후보
-> check_entry/check_exit/check_collisions 통과
-> 점수 계산
-> 가장 좋은 후보 commit
```

개선 후보:

- 단순 EDD 대신 slack 작은 block 우선
- 큰 block 우선
- layer 수 많은 block 우선
- orientation 선택지가 적은 block 우선
- preference regret 큰 block 우선

### 2. Geometry engine 강화

새로운 polygon checker를 직접 만들기보다 `utils.py`를 oracle로 유지하는 것이 좋다.

개선의 핵심은 Shapely 호출을 줄이는 것이다.

가능한 개선:

- block-orientation별 bbox 사전 계산
- layer별 bbox 사전 계산
- 후보 `(block_i, orient_i, block_j, orient_j, dx, dy)` 충돌 결과 캐시
- same-layer collision과 crane-path collision을 별도 캐시
- 모든 정수 좌표 탐색 대신 contact/corner 후보만 생성

### 3. Time slot은 sparse하게 탐색

현재 `_find_earliest_slot`은 이미 모든 날짜를 보지 않고 sparse 후보만 본다.

현재 후보:

```text
release_time
기존 block들의 exit_time
```

개선 후보:

```text
release_time
기존 block exit_time
due_date - processing_time
due_date 주변
bay가 잠깐 비는 시간
```

모든 날짜를 다 훑는 방식은 피해야 한다.

### 4. Repair와 fallback은 필수

현재 `_repair`와 `_force_place`가 이 역할을 한다.

OGC는 infeasible이면 leaderboard 점수가 사실상 망가지므로, objective가 조금 나빠져도 feasible solution을 반환하는 것이 우선이다.

CVRPTW로 비유하면:

- infeasible route에서 고객 일부를 제거
- 다시 삽입
- 그래도 안 되면 아주 늦은 시간이나 별도 route에 넣어 feasibility 확보

OGC에서는 이 fallback이 empty-bay window에 해당한다.

### 5. LNS/ALNS로 개선

현재 본격 LNS는 아직 없다. `myalgorithm.py`가 greedy 한 번 호출하고 끝난다.

LNS를 넣는다면 destroy 대상은 다음처럼 고를 수 있다.

- tardiness가 큰 block
- 선호 bay를 못 받은 block
- 혼잡한 bay의 block
- crane obstruction을 자주 일으키는 block
- top_y가 높은 block
- 시간/공간상 가까운 block 묶음

accept 기준은 단순하게 시작하면 된다.

```text
check_feasibility 결과 feasible이고 objective가 낮아지면 accept
```

## 다음 채팅에서 이어갈 때의 권장 시작점

다른 채팅에서 이어간다면 다음 요청으로 시작하면 좋다.

```text
docs/2026-06-30-ogc2026-chat-context-summary-ko.md를 읽고,
현재 baseline/myalgorithm.py, baseline/baseline_greedy.py, baseline/utils.py 구조를 기준으로
CVRPTW식 LNS/ALNS 개선 계획을 세워주세요.
우선 구현하지 말고, 어떤 destroy/repair operator부터 넣을지 설계해주세요.
```

