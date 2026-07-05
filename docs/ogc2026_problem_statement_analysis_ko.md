# OGC 2026 문제 분석 및 ALNS+SA 설계 가이드

원문 문서: `docs/problem-statement-latest-DBm0ZUoF.pdf`  
문서 제목: Optimization Grand Challenge 2026 (OGC 2026), "The Grand Shipyard Puzzle: Pack the Block, Beat the Clock"  
문서 버전: 1.2, 2026년 6월 15일  
이 문서의 목적: 문제 설명을 단순 요약하는 데서 그치지 않고, 실제 제출 알고리즘을 설계할 때 필요한 해석, 결정 기준, 구현 전략을 정리한다.

---

## 1. 핵심 결론

OGC 2026은 조선소 블록을 여러 베이에 배치하고 처리 일정을 정하는 문제다. 각 블록에 대해 다음 다섯 가지를 모두 결정해야 한다.

- 어느 베이에 배정할지
- 베이 안의 어느 정수 좌표에 둘지
- 어떤 방향을 선택할지
- 언제 베이에 넣을지
- 언제 베이에서 뺄지

이 다섯 결정은 독립적이지 않다. 같은 블록이라도 베이가 바뀌면 가능한 좌표가 바뀌고, 좌표가 바뀌면 충돌 여부와 크레인 입출고 가능성이 바뀐다. 입고/출고 시점이 바뀌면 동시에 존재하는 블록 집합이 바뀌고, 그 결과 같은 배치가 가능해지거나 불가능해진다.

따라서 이 문제를 풀 때 가장 중요한 원칙은 다음과 같다.

1. **가능 해 확보가 최우선이다.**  
   불가능 해, 타임아웃, 크래시는 리더보드에서 모두 실패로 처리된다. 목적함수가 다소 나쁘더라도 제한 시간 안에 가능한 해를 안정적으로 반환해야 한다.

2. **좌표를 전역 정수 격자로 탐색하면 안 된다.**  
   베이 크기와 블록 수를 고려하면 모든 `(x, y)`를 직접 탐색하는 방식은 계산량이 과도하다. 좌표는 현재 베이 상태에서 생성한 제한된 후보 집합 중에서 선택해야 한다.

3. **ALNS는 모든 변수를 직접 흔들지 않는다.**  
   ALNS는 일부 블록을 제거하고 다시 넣는 큰 이웃을 만들고, 실제 `bay / orientation / coordinate / entry / exit` 결정은 repair 삽입기가 한 번에 처리하는 구조가 적합하다.

4. **크레인 제약은 사후 검사가 아니라 삽입 중 검사해야 한다.**  
   정적인 레이어 충돌이 없어도 입고 또는 출고 순간의 수직 이동 경로가 막히면 불가능하다. placement candidate를 평가할 때 entry crane, exit crane, static collision을 함께 확인해야 한다.

5. **train/train2 데이터는 휴리스틱 튜닝 신호다.**  
   제공된 학습 데이터는 하드코딩 대상이 아니라 인스턴스 분포를 읽고 후보 수, destroy 크기, SA 온도, 연산자 가중치를 튜닝하기 위한 자료로 봐야 한다.

---

## 2. 문제의 본질

이 문제는 다음 세 종류의 문제가 결합된 형태다.

- **스케줄링:** 릴리즈 시점, 처리 시간, 납기, 입고/출고 시점 결정
- **불규칙 다각형 패킹:** 여러 레이어를 가진 비볼록 다각형 블록의 2D 배치
- **동적 접근 가능성:** 입고/출고 순간에 크레인 이동 경로가 막히지 않아야 하는 제약

순수 스케줄링으로 보면 공간 배치를 놓치고, 순수 패킹으로 보면 시간과 크레인 제약을 놓친다. 효과적인 알고리즘은 시간과 공간을 분리해서 다루되, 최종 삽입 단계에서는 둘을 동시에 검사해야 한다.

실용적으로는 다음 계층으로 나누는 것이 좋다.

```text
문제 인스턴스
  -> 전처리: 블록 형상, bbox, 면적, 선호 베이, 시간 slack
  -> feasible seed 생성
  -> ALNS destroy: 일부 블록 제거
  -> repair insertion: 제거 블록을 bay/orient/(x,y)/entry/exit까지 재결정
  -> SA acceptance: feasible candidate를 현재 해로 받아들일지 결정
  -> best feasible solution 반환
```

---

## 3. 핵심 개체와 데이터 모델

### 3.1 베이

베이는 고정된 너비와 높이를 가진 작업 공간이다. JSON 입력과 출력에서는 베이 인덱스가 0부터 시작한다.

베이 `j`의 주요 값은 다음과 같다.

- `W_j`: 너비
- `H_j`: 높이
- `A_j = W_j * H_j`: 면적

각 베이는 독립적인 크레인을 가진다고 가정한다. 따라서 서로 다른 베이의 입출고 작업은 공간적으로 독립이다. 그러나 출력 형식에서는 같은 날짜의 작업들이 하나의 리스트에 섞일 수 있으므로, 안전하게 모든 `EXIT`을 먼저 쓰고 그 뒤에 `ENTRY`를 쓰는 방식이 좋다.

알고리즘에서 베이는 다음 상태를 가져야 한다.

```python
BayState:
    bay_id: int
    placed_blocks: list[PlacedBlock]
    intervals: dict[block_id, (entry, exit)]
    load: float
    event_times: sorted set[int]
```

이 상태는 repair 삽입 시 candidate 평가의 기준이 된다.

### 3.2 블록

블록은 여러 다각형 레이어로 표현되는 3차원 물체다. 각 블록은 여러 방향을 가질 수 있고, 각 방향마다 레이어 다각형 목록이 주어진다.

블록 `i`의 입력 정보:

- `release_time`: 가장 이른 입고 가능 시점 `R_i`
- `due_date`: 납기 `D_i`
- `processing_time`: 최소 점유 기간 `P_i`
- `workload`: 작업량 `L_i`
- `bay_preferences`: 베이별 선호 점수 `S_ij`
- `shape`: 방향별 레이어 다각형

중요한 점은 블록의 기준점이 항상 좌하단이 아니라는 것이다. 각 방향에서 첫 번째 레이어의 첫 번째 꼭짓점이 기준점이고, 이 점이 `(x, y)`로 평행이동된다. 따라서 boundary check는 단순히 `0 <= x <= W`, `0 <= y <= H`가 아니라 모든 레이어의 실제 다각형이 베이 내부에 있는지로 판단해야 한다.

알고리즘 전처리에서는 각 `(block, orientation)`에 대해 최소한 다음을 계산해 두는 것이 좋다.

```python
ShapeInfo:
    local_bbox: (min_x, min_y, max_x, max_y)
    width: float
    height: float
    layer_count: int
    layer_polygons: list[Polygon]
    approx_area_by_layer: list[float]
```

### 3.3 배치된 블록

해 내부 표현은 제출 형식보다 assignment 중심이 다루기 쉽다.

```python
PlacedBlock:
    block_id: int
    bay_id: int
    orient_idx: int
    x: int
    y: int
    entry_time: int
    exit_time: int
```

`operations` 형식은 마지막에 이 assignment에서 생성한다. ALNS/LNS 내부 루프에서 매번 operations dict를 직접 다루면 수정과 검사가 불편해진다.

---

## 4. 시간 모델

블록은 다음 반개구간 동안 베이를 점유한다.

```text
[ENTRY_i, EXIT_i)
```

날짜 `t`에 블록이 베이에 존재하는 조건은 다음과 같다.

```text
ENTRY_i <= t < EXIT_i
```

기본 시간 제약:

```text
ENTRY_i >= R_i
EXIT_i - ENTRY_i >= P_i
```

예를 들어 처리 시간이 3인 블록이 5일에 입고되면 5, 6, 7일 동안 처리되고 가장 빠른 출고일은 8일이다.

알고리즘 관점에서 `EXIT_i = ENTRY_i + P_i`가 항상 최선은 아니다. 처리 완료 후에도 블록을 남겨두는 것이 다음 블록의 입고/출고 가능성에 영향을 줄 수 있다. 그러나 지연 목적함수가 있으므로, 출고를 늦추는 결정은 명확한 공간/크레인 이득이 있을 때만 허용해야 한다.

### 4.1 후보 시간 생성

모든 날짜를 검사하는 대신 candidate event time을 제한해야 한다.

입고 후보:

- `R_i`
- `D_i - P_i`가 `R_i` 이상이면 그 값
- 같은 베이에 이미 있는 블록들의 `exit_time`
- 같은 베이에 이미 있는 블록들의 `entry_time`
- 위 후보들의 `±1` 보정값

출고 후보:

- `entry + P_i`
- `D_i`
- 같은 베이에 이미 있는 블록들의 `entry_time`
- 같은 베이에 이미 있는 블록들의 `exit_time`
- entry 이후 후보 중 `entry + P_i` 이상인 값

초기 구현에서는 `exit = entry + P_i`를 우선하고, 크레인 출고가 막힐 때만 후속 event time으로 밀어보는 방식이 안전하다.

---

## 5. 작업 모델과 replay 의미

작업은 두 종류다.

- `ENTRY`: 블록을 베이에 넣는다.
- `EXIT`: 블록을 베이에서 뺀다.

같은 날짜에는 모든 `EXIT` 작업이 모든 `ENTRY` 작업보다 먼저 실행되어야 한다. 이 규칙은 단순한 출력 형식 문제가 아니라 feasibility에 직접 영향을 준다. 예를 들어 같은 날짜에 어떤 블록이 나가고 다른 블록이 들어오는 경우, `EXIT`이 먼저 처리되면 가능하지만 `ENTRY`가 먼저 처리되면 공간이나 크레인 경로가 막힐 수 있다.

권장 출력 생성 규칙:

```text
for each time t:
    append all EXIT operations first
    append all ENTRY operations second
```

같은 유형 내부 순서는 가능하면 다음 기준으로 정렬한다.

- `EXIT`: 출고 대상 주변에 더 많은 블록이 있는 것 먼저, 또는 block_id 순
- `ENTRY`: 더 낮은 y, 더 낮은 x, 더 작은 block_id 순

최종적으로는 `utils.check_feasibility(prob_info, solution)`를 통해 replay 가능성을 확인해야 한다.

---

## 6. 공간 가능성

공간 가능성은 세 단계로 나누어 생각하는 것이 좋다.

1. **베이 내부 포함**
2. **같은 높이 레이어 간 정적 충돌 방지**
3. **입고/출고 순간의 크레인 경로 충돌 방지**

### 6.1 베이 내부 포함

각 블록의 모든 레이어 다각형은 배정된 베이 내부에 있어야 한다. 빠른 후보 필터링에는 bbox를 사용할 수 있다.

방향 `o`의 local bbox가 다음과 같다고 하자.

```text
(lx0, ly0, lx1, ly1)
```

베이 크기가 `(W, H)`일 때 정수 기준점 `(x, y)`가 bbox 기준으로 가능한 범위는 다음과 같다.

```text
ceil(-lx0) <= x <= floor(W - lx1)
ceil(-ly0) <= y <= floor(H - ly1)
```

이 조건은 필요조건에 가깝다. 실제 다각형이 비볼록일 수 있으므로 최종 포함 여부는 polygon containment 또는 공식 utility 검사로 확인해야 한다.

### 6.2 같은 레이어 충돌

같은 날짜, 같은 베이에 존재하는 두 블록은 같은 높이의 레이어끼리 내부가 겹치면 안 된다.

블록 `a`, `b`가 동시에 존재하고 각각 `K_a`, `K_b`개 레이어를 가진다면 검사 대상은 다음이다.

```text
l = 1, ..., min(K_a, K_b)
```

같은 레이어끼리만 검사한다는 점이 중요하다. 위에서 내려다봤을 때 겹쳐 보여도 서로 다른 높이 레이어라면 정적 충돌은 아닐 수 있다.

구현에서는 다음 순서가 효율적이다.

```text
AABB overlap 없음 -> 충돌 없음
AABB overlap 있음 -> 레이어별 polygon intersection 검사
polygon 내부 면적 overlap 있음 -> 충돌
경계만 접촉 -> 허용
```

수치 오차가 있으므로 자체 Shapely 검사와 공식 `utils.py` 결과가 다를 수 있다. 따라서 자체 검사는 빠른 필터와 후보 평가용으로 쓰고, 최종 해는 반드시 공식 checker로 검증해야 한다.

### 6.3 크레인 작업 제약

입고 또는 출고 시점에는 대상 블록을 수직으로 이동시킬 수 있어야 한다. 대상 블록의 레이어 `l1`은 다른 블록의 같은 높이 또는 더 높은 레이어 `l2`와 충돌하면 안 된다.

검사 조건:

```text
l1 <= l2
```

이 제약은 정적 충돌보다 강하다. 어떤 배치가 점유 기간 동안 같은 레이어 충돌을 일으키지 않더라도, 입고/출고 순간에는 높은 레이어가 수직 이동 경로를 막을 수 있다.

알고리즘에서 candidate placement를 평가할 때는 다음을 모두 검사해야 한다.

```text
1. boundary feasible
2. [entry, exit) 동안 겹치는 블록들과 static collision 없음
3. entry 시점에 존재하는 블록들과 entry crane feasible
4. exit 시점에 존재하는 블록들과 exit crane feasible
5. 같은 날짜 operation replay feasible
```

이 중 하나라도 실패하면 해당 candidate는 폐기한다.

---

## 7. 목적 함수와 가중치 해석

전체 목적 함수는 다음과 같다.

```text
w1 * Z1 + w2 * Z2 + w3 * Z3
```

목적 함수는 최소화 대상이다.

### 7.1 총 지연 `Z1`

각 블록의 지연:

```text
T_i = max(0, EXIT_i - D_i)
```

총 지연:

```text
Z1 = sum_i T_i
```

`w1`이 큰 인스턴스에서는 하루 지연 하나가 선호도 위반 수십 개보다 더 큰 손실일 수 있다. 따라서 SA에서 나쁜 해를 수용할 때도 지연 증가를 무작정 받아들이면 안 된다.

### 7.2 작업량 불균형 `Z2`

베이 가중치:

```text
u_j = (전체 베이 평균 면적) / (베이 j의 면적)
```

베이 `j`의 정규화 작업량:

```text
u_j * sum_{i assigned to j} L_i
```

`Z2`는 서로 다른 베이 사이의 정규화 작업량 차이 중 최댓값이다.

이 항은 블록 단위로 국소 개선하기 어렵다. 한 블록 이동이 최댓값을 만드는 베이 쌍을 바꾸기 때문이다. ALNS에서는 `overloaded_bay` destroy 연산자를 두고, 정규화 부하가 큰 베이에서 일부 블록을 제거해 다시 삽입하는 방식이 효과적이다.

### 7.3 선호도 패널티 `Z3`

블록 `i`의 최선호 점수:

```text
S_i^max = max_j S_ij
```

베이 `j`에 배정했을 때의 선호도 패널티:

```text
S_i^max - S_ij
```

`Z3`는 모든 블록의 선호도 패널티 합이다.

이 항은 블록별 기여도가 분명하므로 destroy 대상 선정에 쓰기 좋다. 예를 들어 현재 최선호 베이가 아닌 블록, 또는 선호도 손실이 큰 블록을 제거 대상으로 삼을 수 있다.

### 7.4 가중치 기반 전략 전환

인스턴스마다 `w1`, `w2`, `w3`가 다르므로 하나의 고정 우선순위는 위험하다.

권장 해석:

- `w1`이 지배적이면 지연 없는 feasible schedule을 우선한다.
- `w3`가 상대적으로 크면 선호 베이 위반 블록을 적극적으로 재삽입한다.
- `w2`가 상대적으로 크면 normalized load balance를 맞추는 bay reassignment를 더 자주 시도한다.

SA의 `delta`는 반드시 가중합 objective 기준으로 계산해야 한다. 단, tie-breaker로는 `Z1`, `Z3`, `Z2` 순으로 비교하는 것이 실용적이다.

---

## 8. 입력과 출력 계약

### 8.1 입력 JSON

최상위 구조:

- `name`
- `bays`
- `blocks`
- `weights`

각 베이:

- `width`
- `height`

각 블록:

- `release_time`
- `due_date`
- `processing_time`
- `workload`
- `bay_preferences`
- `shape`

각 방향:

- `orientation`
- `layers`

입력과 출력의 블록/베이 인덱스는 0 기반이다.

### 8.2 출력 형식

제출 함수:

```python
def algorithm(prob_info, timelimit=60):
    return {
        "operations": {
            "0": [
                {
                    "type": "ENTRY",
                    "block_id": 48,
                    "bay_id": 1,
                    "x": 0,
                    "y": 0,
                    "orient_idx": 0
                }
            ],
            "28": [
                {
                    "type": "EXIT",
                    "block_id": 25,
                    "bay_id": 2
                },
                {
                    "type": "ENTRY",
                    "block_id": 15,
                    "bay_id": 0,
                    "x": 106,
                    "y": 0,
                    "orient_idx": 0
                }
            ]
        }
    }
```

출력 요구사항:

- 날짜 키는 문자열이어야 한다.
- 작업이 있는 날짜만 포함하면 된다.
- 같은 날짜에는 `EXIT`이 `ENTRY`보다 앞에 있어야 한다.
- `block_id`, `bay_id`, `orient_idx`, `x`, `y`는 정수여야 한다.
- 좌표는 정수여야 하며, 평가 시스템은 가능성 검사 전에 위치 값을 반올림한다.
- 모든 블록은 정확히 한 번 `ENTRY`되고 정확히 한 번 `EXIT`되어야 한다.

### 8.3 내부 표현과 변환

내부에서는 assignment dict를 쓰는 것이 좋다.

```python
assignments = {
    block_id: {
        "block_id": block_id,
        "bay_id": bay_id,
        "orient_idx": orient_idx,
        "x": x,
        "y": y,
        "entry_time": entry,
        "exit_time": exit,
    }
}
```

마지막에 다음 절차로 operations를 만든다.

```text
for each assignment:
    add ENTRY at entry_time
    add EXIT at exit_time
for each time:
    sort EXIT before ENTRY
convert time keys to str
```

---

## 9. 평가 서버와 제출 제약

제출 요구사항:

- zip 루트에 `myalgorithm.py`가 있어야 한다.
- 함수 시그니처는 `algorithm(prob_info, timelimit=60)`이어야 한다.
- `utils.py`를 포함하더라도 서버에서 덮어쓸 수 있으므로 수정에 의존하면 안 된다.
- zip 파일 크기는 15 MB 이하여야 한다.
- 외부 인터넷 접근은 없다.
- 실행 폴더의 상위 디렉터리 접근은 허용되지 않는다.
- 최대 4 CPU 코어, 16 GB 메모리를 가정해야 한다.

제공 환경에는 Shapely, OR-Tools, Gurobi, Xpress, scikit-learn, numba, torch 등이 포함되어 있다. 그러나 이 문제의 병목은 대부분 동적 기하 검사와 candidate 탐색이므로, 큰 딥러닝 모델이나 거대한 MIP에 의존하는 접근은 제출 안정성 측면에서 위험하다.

---

## 10. 제공 학습 데이터 관찰

현재 저장소 기준으로 학습 인스턴스는 다음 위치에 있다.

- `data/train`
- `data/train 2`

대회 설명이나 대화에서는 두 번째 묶음을 편의상 `train2`라고 부를 수 있지만, 이 저장소의 실제 디렉터리명은 공백이 있는 `data/train 2`다.

로컬 파일 40개를 기준으로 보면 대략 다음 분포를 가진다.

| 항목 | 관찰값 |
|------|--------|
| 인스턴스 수 | 40 |
| 블록 수 | 100-300 |
| 베이 수 | 2-5 |
| 방향 수 | 대부분 8 |
| 평균 레이어 수 | 대략 1.8-3.1 |
| 베이 너비 | 32-179 |
| 베이 높이 | 15-29 |
| slack 중앙값 | 대략 1-5.5일 |
| `train 2` 특성 | slack 중앙값이 대부분 1-2일로 더 타이트함 |

가중치도 인스턴스마다 크게 다르다.

| 가중치 | 관찰 범위 |
|--------|-----------|
| `w1` | 667-29630 |
| `w2` | 1-10 |
| `w3` | 13-600 |

특히 `w1 / w3` 비율이 큰 경우가 많다. 이런 인스턴스에서는 선호도 개선을 위해 하루 지연을 늘리는 move가 거의 항상 손해다.

### 10.1 학습 데이터의 의도

제공 데이터는 다음 용도로 쓰는 것이 적절하다.

- 후보 좌표 개수 cap 결정
- destroy size `k` 범위 결정
- ALNS operator weight 초기값 결정
- SA 초기 온도와 cooling rate 결정
- 인스턴스 profile별 전략 전환 규칙 학습
- bay/orientation 후보 ranking을 위한 작은 모델 또는 규칙 튜닝

반대로 다음 용도로 쓰면 위험하다.

- 특정 파일 번호에 대한 하드코딩
- 특정 베이 크기/블록 수에만 맞춘 예외 처리
- train objective에만 맞춘 과도한 파라미터 튜닝
- 제출 zip 크기를 크게 만드는 대형 모델 포함

### 10.2 추천 인스턴스 profile

알고리즘 시작 시 다음 feature를 계산해 profile을 만들 수 있다.

```text
n_blocks
n_bays
median_slack = median(due - release - processing)
w1, w2, w3
w1 / max(w3, 1)
bay_area_cv
mean_orientation_count
mean_layer_count
total_workload_per_bay_area
```

profile별 전략 예:

- `median_slack <= 1`: 지연 최소화 우선, tardy destroy 가중치 증가
- `w3` 상대 비중 높음: preference violator destroy 증가
- `w2` 상대 비중 높음: overloaded bay destroy 증가
- layer 수 높음: crane-safe 후보를 더 보수적으로 선택

---

## 11. 베이스라인 알고리즘의 의미

문서의 베이스라인은 EDD 기반 greedy다.

대략적인 동작:

- 납기가 빠른 블록부터 배치한다.
- AABB 기반 후보 좌표를 생성한다.
- 가능한 베이, 방향, 위치, 시간 후보를 평가한다.
- 불가능한 블록은 나중에 repair하거나 빈 베이 구간으로 밀어낸다.

베이스라인에서 얻어야 할 교훈은 두 가지다.

1. **정적 패킹만으로는 부족하다.**  
   크레인 입출고 제약 때문에 나중에 불가능해질 수 있다.

2. **한 번 배치한 블록을 되돌리지 않으면 objective 개선이 제한된다.**  
   선호 베이와 부하 균형은 전역적인 성격이 있어서 greedy만으로는 놓치기 쉽다.

따라서 베이스라인은 최종 전략이라기보다 feasible seed와 repair 삽입기의 출발점으로 보는 것이 좋다.

---

## 12. ALNS+SA 전체 아키텍처

권장 구조:

```text
algorithm(prob_info, timelimit)
  -> preprocess(prob_info)
  -> seed = build_feasible_seed()
  -> current = seed
  -> best = seed
  -> initialize operator weights
  -> initialize SA temperature
  -> while time remains:
         operator = select_destroy_operator()
         removed = destroy(current, operator)
         candidate = repair(current - removed, removed)
         if candidate feasible:
             if accept_by_sa(current, candidate, T):
                 current = candidate
                 update best if improved
         update operator weights
         cool down T
  -> return best as operations
```

핵심은 `repair`다. ALNS와 SA는 repair가 만들어낸 feasible candidate를 비교하고 선택할 뿐이다. repair가 약하면 전체 탐색이 작동하지 않는다.

### 12.1 상태 구분

세 종류의 해를 명확히 구분해야 한다.

- `best`: 지금까지 찾은 가장 좋은 feasible solution
- `current`: SA가 현재 탐색 중인 feasible solution
- `candidate`: destroy/repair로 만든 새 feasible solution

Hill climbing은 `best`와 `candidate`만 비교하지만, SA는 `current`보다 나쁜 candidate도 확률적으로 받아들인다. 그래도 최종 반환은 항상 `best`여야 한다.

### 12.2 불가능 해 정책

초기 구현에서는 infeasible candidate를 SA로 받아들이지 않는 것이 안전하다.

```text
repair 실패 -> candidate 폐기
check_feasibility 실패 -> candidate 폐기
feasible candidate만 SA acceptance 대상
```

나중에 penalty 기반 infeasible search를 추가할 수는 있지만, 공식 checker와 불일치하는 위험이 크므로 1차 전략에서는 제외하는 편이 낫다.

---

## 13. 의사결정 변수 분해

문제의 모든 변수를 같은 레벨에서 탐색하면 경우의 수가 폭발한다. 다음처럼 역할을 나누는 것이 좋다.

| 결정 | 담당 계층 | 이유 |
|------|-----------|------|
| 어떤 블록을 다시 배치할지 | ALNS destroy | 큰 이웃 탐색 |
| 제거 블록 삽입 순서 | repair | feasibility와 objective에 직접 영향 |
| 베이 후보 | repair | 선호도, 부하, 공간 가능성 동시 고려 |
| 방향 후보 | repair | bbox, 레이어, 크레인 가능성 영향 |
| 좌표 후보 | placement generator | 전역 격자 탐색 방지 |
| entry/exit 후보 | time-slot generator | 시간 후보 제한 |
| 나쁜 해 수용 여부 | SA | 지역 최적 탈출 |

중요한 원칙:

```text
ALNS는 block subset을 바꾼다.
repair는 block placement를 완성한다.
SA는 feasible solution 사이의 이동을 제어한다.
```

좌표와 방향을 SA move로 직접 조금씩 흔드는 방식은 비효율적이다. 한 칸 이동만으로도 많은 레이어 충돌과 크레인 제약을 다시 검사해야 하며, 성공 확률도 낮다.

---

## 14. 후보 좌표 생성 전략

정수 좌표 후보를 줄이는 것이 성능의 핵심이다.

### 14.1 기본 후보

각 `(block, orientation, bay)`에 대해 local bbox 범위를 계산한다.

```text
x_min = ceil(-local_min_x)
x_max = floor(W - local_max_x)
y_min = ceil(-local_min_y)
y_max = floor(H - local_max_y)
```

기본 후보:

- `(x_min, y_min)`
- 베이 왼쪽/아래 벽에 붙는 좌표
- 기존 블록의 오른쪽 edge에 새 블록 왼쪽 edge가 닿는 좌표
- 기존 블록의 위쪽 edge에 새 블록 아래 edge가 닿는 좌표
- 기존 블록의 왼쪽 edge에 새 블록 오른쪽 edge가 닿는 좌표
- 기존 블록의 아래 edge에 새 블록 위쪽 edge가 닿는 좌표

기존 배치 블록의 world bbox가 `(ox0, oy0, ox1, oy1)`이고 새 블록 local bbox가 `(lx0, ly0, lx1, ly1)`이면 후보는 다음처럼 만들 수 있다.

```text
x candidates:
    ceil(-lx0)
    floor(W - lx1)
    ceil(ox1 - lx0)
    floor(ox0 - lx1)

y candidates:
    ceil(-ly0)
    floor(H - ly1)
    ceil(oy1 - ly0)
    floor(oy0 - ly1)
```

이 후보들의 cross product를 만들되, 너무 많으면 정렬 후 cap을 둔다.

### 14.2 보정 후보

다각형 꼭짓점이 소수이므로 bbox edge 접촉점만으로는 좋은 배치가 빠질 수 있다. 각 후보 주변에 작은 보정을 넣는다.

```text
(x + dx, y + dy), dx, dy in {-1, 0, 1}
```

단, 후보 수가 급증하므로 다음 순서로 필터링한다.

```text
bounds check
duplicate 제거
AABB collision prefilter
candidate score로 상위 N개만 유지
정밀 geometry 검사
```

### 14.3 후보 정렬 기준

좌표 후보는 다음 기준으로 정렬한다.

1. 낮은 `y`
2. 낮은 `x`
3. 낮은 top edge
4. 기존 블록과의 접촉 가능성
5. bay 중심에서 너무 멀지 않은 위치

목표는 단순히 아래쪽에 넣는 것이 아니라, 다음 블록이 들어올 공간을 덜 파편화하는 것이다. 그래도 1차 구현에서는 bottom-left 성향이 가장 안정적이다.

### 14.4 후보 수 cap

인스턴스 크기에 따라 후보 수를 제한해야 한다.

권장 초기값:

```text
small instance: orientation당 좌표 후보 100-200개
large instance: orientation당 좌표 후보 30-80개
repair iteration 내부: 블록당 최종 placement 후보 20-50개
```

이 값은 train/train2 benchmark로 조정해야 한다.

---

## 15. 후보 시간 생성과 삽입 평가

좌표 후보가 정해져도 entry/exit 시간이 정해지지 않으면 placement가 완성되지 않는다.

### 15.1 기본 삽입 절차

```text
for bay in bay_candidates:
  for orientation in orientation_candidates:
    for coordinate in coordinate_candidates:
      if boundary/static geometry impossible by coarse filter:
          continue
      for entry in entry_candidates:
          exit_candidates = generate_exit_candidates(entry)
          for exit in exit_candidates:
              if exit - entry < processing_time:
                  continue
              if static collision over [entry, exit):
                  continue
              if entry crane infeasible:
                  continue
              if exit crane infeasible:
                  continue
              score candidate
```

실제로는 loop 순서를 더 최적화할 수 있다. 예를 들어 베이와 방향을 먼저 줄이고, bbox 기반 좌표 후보를 만든 뒤, 시간 후보를 평가한다.

### 15.2 베이 후보 축소

모든 베이를 매번 보되, 정렬은 해야 한다.

베이 후보 점수:

```text
preference_penalty
+ estimated_load_balance_delta
+ estimated_tardiness_risk
+ geometry_fit_penalty
```

최선호 베이가 공간적으로 불가능하거나 지연을 크게 만들 수 있으므로, 선호도만으로 베이를 고정하면 안 된다.

### 15.3 방향 후보 축소

방향은 대부분 8개지만 모든 방향의 모든 좌표를 매번 정밀 검사하면 비싸다.

방향 후보 정렬 기준:

- bbox가 베이에 들어가는가
- bbox area가 작은가
- 높이 또는 폭이 현재 빈 공간에 맞는가
- 레이어 수가 많은 경우 crane obstruction 가능성이 낮은가
- 과거 repair에서 성공률이 높았는가

초기에는 모든 방향을 보되, 큰 인스턴스에서는 상위 4-6개 방향만 정밀 평가하는 방식을 고려할 수 있다.

---

## 16. 기하 검사와 캐시

기하 검사는 가장 큰 병목 중 하나다. 매번 Shapely 정밀 검사를 호출하면 ALNS iteration 수가 크게 줄어든다.

### 16.1 검사 계층

권장 계층:

```text
1. bbox bounds check
2. pairwise AABB overlap check
3. cached pairwise polygon check
4. official utils 기반 최종 feasibility check
```

`utils.check_feasibility`는 iteration마다 너무 자주 호출하면 비싸다. repair 내부에서는 자체 빠른 검사와 캐시를 쓰고, candidate solution이 완성된 뒤 공식 checker를 호출하는 방식이 좋다.

### 16.2 Pairwise cache key

두 블록의 상대 위치가 같으면 충돌 결과는 재사용할 수 있다.

권장 cache key:

```python
(
    min(block_a, block_b),
    orient_a_or_b_for_min,
    max(block_a, block_b),
    orient_a_or_b_for_max,
    dx,
    dy,
    mode,
)
```

여기서:

- `dx = x_b - x_a`
- `dy = y_b - y_a`
- `mode = "static"` 또는 `"crane_entry_exit"`

주의할 점은 crane 검사는 대상 블록과 상대 블록의 역할이 비대칭일 수 있다는 것이다. 정적 충돌은 대체로 대칭이지만, 크레인 제약은 "작업 대상"이 누구인지에 따라 검사 레이어 조건이 달라진다. 따라서 crane cache는 다음처럼 방향성을 포함하는 편이 안전하다.

```python
(
    target_block,
    target_orient,
    other_block,
    other_orient,
    dx_other_minus_target,
    dy_other_minus_target,
    "crane",
)
```

### 16.3 캐시 크기 관리

블록 수가 300이고 방향이 8개면 가능한 pair가 많다. 무제한 캐시는 메모리를 낭비할 수 있다.

권장 정책:

- Python dict 기반 LRU 또는 단순 size cap
- mode별 cache 분리
- AABB에서 걸러지는 pair는 캐시에 넣지 않음
- 정밀 검사 결과만 캐시

초기 구현에서는 size cap 없이 시작해도 되지만, train2 대형 인스턴스에서 메모리 사용량을 확인해야 한다.

---

## 17. Feasible seed 생성

ALNS+SA는 좋은 seed가 있어야 작동한다. seed가 infeasible이면 이후 탐색이 시작되지 않는다.

### 17.1 seed 우선순위

초기 seed의 우선순위:

1. 모든 블록이 배치된다.
2. 공식 checker를 통과한다.
3. 지연이 과도하지 않다.
4. 선호도와 부하 균형이 너무 나쁘지 않다.

처음부터 최적 objective를 노리기보다 안정적으로 feasible seed를 만드는 것이 중요하다.

### 17.2 정렬 기준

블록 삽입 순서 후보:

- EDD: `(due_date, processing_time)`
- release-aware EDD: `(release_time, due_date)`
- criticality: `(due_date - release_time - processing_time, due_date)`
- geometry-hard-first: 큰 bbox, 많은 레이어, 적은 feasible bay 우선
- preference-aware: 선호 베이 선택지가 좁은 블록 우선

초기 seed에서는 `criticality + geometry-hard-first`를 섞는 것이 좋다. 어려운 블록을 나중에 넣으면 남은 공간이 파편화되어 강제 배치가 늘어날 수 있다.

### 17.3 seed fallback

정상 greedy 삽입이 실패하면 fallback이 필요하다.

권장 fallback 순서:

```text
1. 후보 좌표 cap 증가
2. 방향 후보 전체로 확대
3. entry 후보를 더 뒤까지 확대
4. 덜 선호하는 베이 허용
5. 빈 베이 window에 단독 배치
6. 그래도 실패하면 가장 안전한 강제 배치 전략 사용
```

강제 배치는 objective를 크게 악화시킬 수 있지만, infeasible 반환보다 낫다.

---

## 18. ALNS destroy 연산자

Destroy 연산자는 현재 해에서 일부 블록을 제거한다. 제거 대상이 좋아야 repair가 의미 있는 개선을 만들 수 있다.

### 18.1 기본 연산자

| 연산자 | 제거 대상 | 목적 |
|--------|-----------|------|
| `random_k` | 무작위 k개 블록 | 다양성 |
| `tardy_blocks` | 지연이 있는 블록 | `Z1` 개선 |
| `preference_violators` | 최선호 베이가 아닌 블록 | `Z3` 개선 |
| `overloaded_bay` | 정규화 부하가 큰 베이의 블록 | `Z2` 개선 |
| `spatial_cluster` | 같은 베이, 가까운 좌표의 블록 묶음 | 공간 재배치 |
| `time_cluster` | 같은 시간대에 겹치는 블록 묶음 | 스케줄 재배치 |
| `crane_blockers` | 입출고를 자주 막는 블록 | 크레인 가능성 개선 |

초기 구현에서는 `random_k`, `tardy_blocks`, `preference_violators`, `overloaded_bay` 네 개로 시작하고, 로그를 보고 cluster 계열을 추가하는 것이 좋다.

### 18.2 destroy size

`k`가 너무 작으면 개선 폭이 작고, 너무 크면 repair 실패율이 높다.

권장 초기값:

```text
k_min = 1
k_max = min(20, max(5, n_blocks // 10))
```

운영 방식:

- 초반 온도가 높을 때는 큰 `k`도 허용
- 후반에는 작은 `k` 위주로 미세 개선
- repair 실패율이 높으면 해당 operator의 `k`를 줄임

### 18.3 Adaptive operator weight

연산자 선택은 고정 roulette로 시작하되, 일정 segment마다 가중치를 업데이트한다.

예시 보상:

```text
new global best: +5
improved current: +3
accepted non-improving: +1
repair failed: 0
infeasible candidate: 0
```

segment가 끝나면:

```text
weight_op = (1 - rho) * weight_op + rho * average_score_op
```

초기에는 `rho = 0.1-0.3` 범위가 무난하다.

---

## 19. Repair 삽입 전략

Repair는 제거된 블록을 다시 삽입한다. 이 단계가 실제로 `bay / orientation / coordinate / entry / exit`를 결정한다.

### 19.1 삽입 순서

제거 블록은 단순 EDD보다 regret 기반 순서가 더 좋을 수 있다.

각 블록에 대해 상위 candidate placement 몇 개를 구하고, 다음 값을 계산한다.

```text
regret = score_2nd_best - score_best
```

regret이 큰 블록은 좋은 선택지가 하나뿐이라는 뜻이므로 먼저 삽입한다.

권장 순서:

```text
1. candidate가 적은 블록
2. regret이 큰 블록
3. due_date가 빠른 블록
4. bbox가 큰 블록
```

### 19.2 candidate score

Repair 내부 candidate score는 공식 objective delta를 근사하되, 공간 tie-breaker를 포함한다.

예시:

```text
score =
    w1 * estimated_tardiness_delta
  + w2 * estimated_load_balance_delta
  + w3 * preference_penalty
  + small_weight * top_y
  + small_weight * fragmentation_penalty
  + small_weight * crane_risk
```

공식 objective에 없는 항은 작게 둬야 한다. 이 항들은 후보 정렬용이지 최종 목표가 아니다.

### 19.3 randomized repair

항상 best candidate만 고르면 탐색 다양성이 줄어든다. SA와 함께 쓸 때는 repair에서도 약간의 randomness가 도움이 된다.

방법:

- 상위 `q`개 candidate 중 softmax 확률로 선택
- 또는 확률 `p`로 best가 아닌 상위 후보 선택
- 온도가 높을 때 randomness 증가, 낮을 때 greedy화

단, feasible seed 생성 단계에서는 randomness를 줄이고 안정성을 우선한다.

### 19.4 repair 실패 처리

제거된 블록 중 하나라도 재삽입하지 못하면 해당 ALNS iteration은 실패로 처리한다.

```text
partial repair result 폐기
current solution 유지
operator 실패 통계 기록
필요하면 다음 segment에서 operator weight 감소
```

부분 삽입 상태를 억지로 유지하면 assignment consistency가 깨지기 쉽다.

---

## 20. Simulated Annealing 수용 기준

SA는 local optimum을 벗어나기 위한 장치다. 단, 이 문제에서는 infeasible 해를 받아들이는 SA보다 feasible solution 사이에서만 움직이는 SA가 안정적이다.

### 20.1 수용 공식

현재 해 `current`, 후보 해 `candidate`의 objective를 비교한다.

```text
delta = objective(candidate) - objective(current)
```

수용 기준:

```text
if delta <= 0:
    accept
else:
    accept with probability exp(-delta / T)
```

수용되면 `current = candidate`가 된다. `candidate`가 `best`보다 좋으면 `best`도 갱신한다.

최종 반환은 항상 `best`다.

### 20.2 초기 온도

objective scale이 인스턴스마다 다르므로 고정 온도는 위험하다.

권장 방식:

1. 초반에 몇 개의 trial move를 만들고 positive delta를 수집한다.
2. `median_positive_delta`를 계산한다.
3. 초기 수용확률 목표 `p0`를 정한다. 예: `0.5` 또는 `0.6`
4. 다음 식으로 `T0`를 정한다.

```text
T0 = -median_positive_delta / ln(p0)
```

trial move가 부족하면 다음 fallback을 쓴다.

```text
T0 = 0.01 * current_objective
```

단, objective가 0에 가까운 경우에는 최소 온도를 둔다.

### 20.3 Cooling schedule

간단한 geometric cooling:

```text
T = alpha * T
```

권장 초기 범위:

```text
alpha = 0.995-0.999
```

iteration 수가 적은 인스턴스에서는 너무 빨리 식으면 hill climbing과 다르지 않다. 시간 기반 cooling도 가능하다.

```text
progress = elapsed / time_budget
T = T0 * (T_min / T0) ** progress
```

이 방식은 timelimit가 달라도 비슷한 탐색 온도를 유지하기 쉽다.

### 20.4 지연 증가에 대한 보수성

`w1`이 큰 인스턴스에서는 지연 1일 증가의 delta가 매우 크다. SA가 이를 계속 받아들이면 best는 유지되더라도 current가 나빠져 repair가 불안정해질 수 있다.

권장 정책:

- `Z1`이 증가하는 move는 SA 수용확률을 그대로 쓰되, 너무 큰 증가에는 hard cap을 둔다.
- 예: `candidate.obj1 > best.obj1 + tardiness_margin`이면 reject
- `tardiness_margin`은 0-2일 정도에서 train 데이터로 조정한다.

---

## 21. Objective 평가와 빠른 delta 계산

매 candidate마다 전체 objective를 공식 checker로 계산하면 느리다. repair 내부에서는 빠른 근사 delta를 쓰고, 완성된 candidate에 대해서만 공식 checker를 호출한다.

### 21.1 빠른 objective 구성

Assignment에서 바로 계산 가능한 항:

- `Z1`: 각 블록의 `max(0, exit - due)`
- `Z3`: 각 블록의 `max_pref - assigned_pref`
- `Z2`: 베이별 workload 합으로 계산

이 세 항은 geometry와 무관하므로 빠르게 갱신할 수 있다.

### 21.2 local delta

블록 집합을 제거/삽입할 때는 다음 상태를 유지한다.

```python
ObjectiveState:
    tardiness_by_block
    pref_penalty_by_block
    workload_by_bay
    normalized_load_by_bay
    obj1
    obj2
    obj3
    objective
```

`Z2`는 최댓값 기반이라 완전한 O(1) 업데이트가 복잡하지만, 베이 수가 2-5개 수준이면 매번 전체 베이 배열을 다시 계산해도 충분히 싸다.

---

## 22. 검증 전략

검증은 세 층으로 나누는 것이 좋다.

### 22.1 Unit-level 검사

- bbox bounds 계산이 맞는지
- assignment에서 operations 변환이 맞는지
- `EXIT`이 `ENTRY`보다 먼저 정렬되는지
- objective 빠른 계산이 `utils.check_feasibility` 결과와 일치하는지

### 22.2 Instance-level 검사

각 train 인스턴스에 대해:

```text
run algorithm(prob_info, timelimit)
check_feasibility == feasible
elapsed <= timelimit
objective 기록
obj1/obj2/obj3 기록
repair failure rate 기록
operator success rate 기록
```

### 22.3 Regression benchmark

비교 대상:

- baseline greedy
- greedy seed only
- ALNS without SA
- ALNS + SA
- ALNS + SA + adaptive operator weights

각 단계가 실제로 개선을 만드는지 분리해서 봐야 한다. 전체 알고리즘만 비교하면 어떤 구성요소가 효과적인지 알기 어렵다.

---

## 23. 구현 우선순위

권장 개발 순서:

1. **공식 checker를 통과하는 seed 생성**
   - objective보다 feasibility 우선
   - 모든 train 인스턴스에서 타임아웃 없이 반환

2. **assignment/operations 변환 안정화**
   - 내부 표현과 출력 표현 분리
   - objective 빠른 계산 구현

3. **candidate placement generator 개선**
   - bbox 기반 좌표 후보
   - ±1 보정
   - 후보 수 cap

4. **기하 검사 캐시**
   - static collision cache
   - crane obstruction cache
   - AABB prefilter

5. **기본 LNS**
   - random/preference/tardy/load destroy
   - greedy repair
   - 개선 해만 수용

6. **SA acceptance**
   - feasible candidate만 수용
   - instance scale 기반 T0
   - time-based cooling

7. **Adaptive ALNS**
   - operator score
   - destroy size 조정
   - profile별 초기 가중치

8. **train/train2 튜닝**
   - 후보 수
   - k 범위
   - 온도
   - 시간 배분

---

## 24. 실패 모드와 대응책

| 실패 모드 | 원인 | 대응 |
|-----------|------|------|
| infeasible solution | 크레인 또는 replay 제약 누락 | repair candidate 단계에서 entry/exit/replay 검사 강화 |
| timeout | 후보 좌표/시간 과다 | 후보 cap, AABB prefilter, cache 도입 |
| objective 악화 | SA가 너무 뜨거움 | T0 낮추기, tardiness hard cap |
| repair 실패율 높음 | destroy size 과다 | k 감소, 후보 확대, fallback repair |
| 선호도 개선 없음 | destroy가 random 위주 | preference violator 가중치 증가 |
| 부하 균형 개선 없음 | overloaded bay move 부족 | load imbalance destroy 추가 |
| 지연이 계속 증가 | due-date pressure 과소평가 | tardy destroy, criticality 삽입 순서 강화 |
| 메모리 증가 | collision cache 무제한 | LRU/size cap 적용 |
| local optimum 고착 | hill climbing만 수행 | SA 수용, randomized repair, cluster destroy |

---

## 25. 피해야 할 접근

다음 접근은 이 문제 구조상 위험하다.

1. **모든 정수 좌표 전수 탐색**  
   베이 크기와 블록 수 때문에 계산량이 너무 크다.

2. **좌표를 SA move로 직접 한 칸씩 이동**  
   성공 확률이 낮고 충돌 재검사가 비싸다.

3. **크레인 제약을 마지막에만 검사**  
   repair가 만든 해 대부분이 사후에 무효화될 수 있다.

4. **선호 베이 고정**  
   선호도가 높아도 공간/시간상 불가능하거나 지연을 크게 만들 수 있다.

5. **거대한 MIP/CP 단일 모델**  
   불규칙 다각형, 레이어, 동적 크레인 제약을 모두 정확히 넣으면 모델이 지나치게 커진다.

6. **train 파일 번호 기반 하드코딩**  
   숨겨진 인스턴스 일반화에 실패할 가능성이 높다.

---

## 26. 최종 제출 전 체크리스트

필수 체크:

- `myalgorithm.py`가 zip 루트에 있는가
- `algorithm(prob_info, timelimit=60)` 시그니처를 유지하는가
- 모든 블록이 정확히 한 번 `ENTRY`, 한 번 `EXIT`되는가
- 날짜 키가 문자열인가
- 같은 날짜에 `EXIT`이 `ENTRY`보다 앞서는가
- 모든 좌표와 ID가 정수인가
- 모든 좌표가 공식 checker에서 boundary feasible인가
- 모든 entry/exit가 release/processing 제약을 만족하는가
- 최종 해가 `utils.check_feasibility(prob_info, solution)`를 통과하는가
- timelimit 전에 best feasible solution을 반환하는가
- 외부 인터넷이나 상위 디렉터리 접근에 의존하지 않는가
- 4 CPU, 16 GB 메모리 안에서 동작하는가

성능 체크:

- seed만 실행했을 때 objective
- ALNS without SA objective
- ALNS+SA objective
- 각 operator 호출 수와 성공률
- repair 실패율
- 평균 iteration 시간
- `obj1`, `obj2`, `obj3` 각각의 변화

제출 안정성 체크:

- 예외 발생 시에도 현재 best를 반환하는가
- 시간이 부족할 때 repair 중간 상태를 반환하지 않는가
- candidate가 infeasible이면 best/current를 손상하지 않는가
- random seed가 없어도 실행이 재현 가능한가, 또는 seed를 내부적으로 관리하는가

---

## 27. 추천 MVP 정의

최소 완성본은 다음 수준이면 충분하다.

```text
1. greedy feasible seed 생성
2. assignment 기반 상태 관리
3. random/preference/tardy/load destroy
4. greedy 또는 regret repair
5. feasible candidate만 hill climbing 또는 SA로 수용
6. best feasible solution anytime 반환
7. train/train2 전체에서 타임아웃/크래시 없음
```

그 다음 개선 순서는 다음과 같다.

```text
MVP
  -> collision cache
  -> 좌표 후보 개선
  -> regret repair
  -> SA 온도 튜닝
  -> adaptive operator weights
  -> profile별 파라미터
```

이 문제에서 강한 알고리즘은 한 번에 거대한 최적화 모델을 푸는 방식보다, 빠르고 안정적인 feasible builder와 반복 개선 메타휴리스틱을 결합하는 방식일 가능성이 높다. 특히 ALNS+SA를 적용할 때는 "무엇을 파괴할지"보다 "어떻게 다시 넣을지"가 성능을 결정한다.
