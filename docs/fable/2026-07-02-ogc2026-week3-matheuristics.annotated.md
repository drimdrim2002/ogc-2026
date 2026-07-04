# OGC 2026 - 3주차 계획 주석본 (도메인 설명 포함)

> **이 문서에 대하여**
> 이 파일은 `2026-07-02-ogc2026-week3-matheuristics.md` (이하 "원본")를 **그대로 보존**한 채, 필사하며 읽을 때 참고할 수 있도록 도메인 지식·용어 설명을 끼워 넣은 학습용 사본입니다. 원본은 2~4주차 계획이 서로 의존하는 **계약 문서**라서 절대 수정하지 않았습니다. 설명은 `> 💡 설명:` 형태의 인용 블록으로 표시되며, 원문과 섞이지 않도록 구분해두었습니다. 1주차 주석본(`2026-07-02-ogc2026-week1-core-decoder-hw.annotated.md`)에서 이미 설명한 개념(block/bay/ENTRY-EXIT/layer/orientation, Z1/Z2/Z3, Stage1~5, j≥k 크레인 스윕 규칙, SCALE 정수 트릭, NFP 비트맵, ATC, BLF, biased randomization, Core API 등)은 여기서 다시 길게 설명하지 않고 짧게 리마인드만 하며, 자세한 내용은 1주차 주석본을 참고하도록 안내합니다. 3주차에서 **새로 등장하는** 개념(매트휴리스틱, CP-SAT 재타이밍, interval/disjunction, 배치풀, CP-SAT 선택모델, 폴리시, 병렬 ALNS 체인 등)은 이번 문서에서 새로 자세히 설명합니다.

---

## 0. 배경: 3주차는 무엇을 하는 주차인가

### 0.1 매트휴리스틱(matheuristics)이란 무엇인가

3주차 원본 제목부터가 "**매트휴리스틱** 마무리"입니다. 이 단어 자체를 먼저 풀어야 합니다.

> 💡 **설명 — 매트휴리스틱 = 메타휴리스틱 + 수리계획법(exact method)의 결합:** "matheuristics"는 "mathematical programming"과 "heuristics"를 합친 조어입니다. 메타휴리스틱 개발 경험이 있으시니 두 극단을 먼저 대비해보면 이해가 빠릅니다.
>
> - **메타휴리스틱(ALNS, GA, SA, Tabu Search 등)**: 빠르고 유연하지만, "이게 최적이다"라는 보장이 없고 지역 최적해(local optimum)에 갇히기 쉽습니다. 문제가 아무리 커도 시간 예산 안에서 그럭저럭 괜찮은 해를 내는 데 강합니다.
> - **정확한(exact) 수리계획법 솔버(MIP, CP-SAT 등)**: 문제를 수학적으로 정식화(변수·제약·목적함수)해서 풀면, 이론적으로 **전역 최적해를 증명까지 포함해** 찾을 수 있습니다. 하지만 문제가 조금만 커져도(변수·제약이 기하급수적으로 늘어나면) 풀이 시간이 폭발적으로 늘어나 실전에서 못 씁니다.
>
> **매트휴리스틱**은 "문제 전체를 정확한 솔버에 던지면 절대 안 끝나지만, 메타휴리스틱이 이미 상당히 좋은 해(incumbent)를 찾아놓은 상태에서, 그 해의 **일부 구조는 고정하고 나머지 일부 차원만** 정확한 솔버에 맡기면 그 부분은 순식간에, 그리고 증명 가능하게 최적으로 풀린다"는 절충안입니다. 즉 "전역 탐색은 메타휴리스틱이, 국소적으로 정확히 풀 수 있는 하위문제는 exact 솔버가" 분업하는 구조입니다.
>
> 이번 프로젝트에서 정확히 그렇게 씁니다 — ALNS(2주차, 메타휴리스틱)가 예산의 ~80%를 써서 "블록을 어느 bay의 어느 좌표·방향에 놓을지"(기하, geometry)를 정하고 나면, 3주차는 남은 ~20% 예산 동안 **그 기하는 그대로 둔 채** 아래 세 가지 하위 문제만 골라서 CP-SAT(정확한 솔버)에 맡깁니다:
> 1. **재타이밍(retiming)**: 기하 고정, 시간(entry/exit)만 재최적화 → Z1(지연시간) 담당.
> 2. **배치풀 선택(pool selection)**: ALNS가 지나가며 봤던 여러 후보 배치 조각들 중 "블록당 1개씩 골라 조합"만 CP-SAT로 재조합 → Z1+Z2+Z3 근사 재최적화.
> 3. **폴리시(polish)**: 시간 완전 고정, bay 재배정만 → Z2/Z3 담당.
>
> 왜 이 조합이 유용한가: 기하나 시간 중 하나를 완전히 고정하면, 문제가 "많은 변수 중 일부만 남은" 훨씬 작은 문제로 줄어들어 CP-SAT가 수 초~수십 초 안에 그 하위문제의 **진짜 최적해**(혹은 매우 근접한 해)를 찾아줄 수 있습니다. ALNS 혼자였다면 "우연히 더 나은 시간 배정을 찾을 때까지" 무작위 파괴-복구를 반복해야 했을 것을, CP-SAT는 "이 기하 아래에서 가능한 모든 시간 배정 중 최선"을 수학적으로 보장하며 찾아줍니다 — 메타휴리스틱이 지역해에 갇혔을 때 그 지역 안에서나마 확실히 더 잘 짜주는 "마무리 광내기" 역할입니다.

### 0.2 이 프로젝트의 5층 구조에서 3주차의 위치

설계서(`docs/superpowers/specs/2026-07-02-ogc2026-strategy-design.md`) §4는 전체 알고리즘을 5개 층(L1~L5)으로 나눕니다.

```
┌─ L5 견고성 셸 (Python): 시간예산 관리, 항상-실행가능 incumbent,
│    최종 utils.check_feasibility 검증, 3단 폴백
├─ L4 매트휴리스틱 마무리 (예산 후반 ~20%): CP-SAT 재타이밍 / 배치풀 MIP / Z3·Z2 폴리시   ← 3주차
├─ L3 ALNS 코어 (예산 ~80%): 파괴·복구 연산자, RRT/재가열 SA 수용, 경량 적응 가중치       ← 2주차
├─ L2 구성 디코더: ATC+면적 우선순위 biased randomization → time-first BLF              ← 1주차
└─ L1 C++ 기하 코어 (.so): 정수 NFP 비트맵 + (bay,layer,일) 점유 비트보드 + 후보위치 생성  ← 1주차
```

> 💡 **설명:** 1주차가 L1(코어)+L2(디코더)+L5 뼈대, 2주차가 L3(ALNS), 이번 3주차가 **L4**를 채우는 흐름입니다. 즉 3주차 작업물은 "L3(ALNS)가 다 돌고 난 뒤, 최종 제출 직전에 한 번 더 짜내는 마무리 단계"입니다. `shell.solve()`가 실제로 부르는 순서는 `construct(1주차) → improve/ALNS(2주차, 예산 ~80%) → retiming → pool → polish(3주차, 남은 예산 ~20%) → 최종 utils 검증(1주차 셸)`입니다.

### 0.3 1주차에서 이미 설명한 개념 — 짧은 리마인드

아래 개념들은 1주차 주석본 0절·본문에서 이미 자세히 설명했으므로, 여기서는 이번 문서를 읽는 데 필요한 최소한만 되짚습니다. **자세한 내용은 1주차 주석본을 참고하세요.**

- **Block / Bay / ENTRY / EXIT / Layer / Orientation / bay_preferences**: 조선소 블록을 적치장(bay)에 크레인으로 넣고(ENTRY) 빼는(EXIT) 문제. 블록은 여러 층(layer)으로 이루어진 다각형이고, 이산적 방향(orientation) 후보 중 하나로 놓입니다.
- **Z1/Z2/Z3**: Z1=총 지연시간(지배적, ~95%), Z2=bay 부하 불균형, Z3=bay 선호도 손실. 3주차의 세 스테이지가 정확히 이 세 항을 하나씩 겨냥합니다 — retiming→Z1, pool→Z1+Z2+Z3(근사), polish→Z2+Z3.
- **Stage 1~5 / utils.check_feasibility**: 최종 채점 오라클. `entry/exit` 시점 순간 충돌(Stage2/3), 체류 중 정적 충돌(Stage4), 전체 재생(replay) 검증(Stage5).
- **j≥k 크레인 스윕 규칙 / static_bm·entry_bm / OffsetBitmap**: "가만히 있을 때"(j==k, static)와 "크레인 진입·퇴출 스윕 중"(j≥k, entry) 두 종류의 충돌을 구분해 미리 계산해둔 비트맵. 3주차 재타이밍이 바로 이 비트맵의 "현재 배치 상태에서의 값"인 `pair_bits_at_current()`를 CP-SAT 제약의 입력으로 씁니다(아래 0.4절에서 이어서 설명).
- **same-day 규약**: 같은 날 EXIT 전량이 ENTRY보다 먼저, EXIT/ENTRY 각각 block id 오름차순. 3주차 CP-SAT 인코딩이 이 규약을 그대로 부등식으로 옮깁니다.
- **ATC / BLF / biased randomization**: 1주차 디코더의 우선순위·배치 규칙. 2주차 ALNS와 3주차 매트휴리스틱은 이 디코더가 만든 초기해(혹은 ALNS가 개선한 해)를 입력(incumbent)으로 받아서 다듬습니다.
- **Core API (`place/check_place/free_positions/objective/verify_full` 등)**: 3주차 코드 전체가 새 Core API를 거의 추가하지 않고 이 기존 API만으로 구현됩니다(아래 "계약 변경 요청" 절에서 예외적으로 조건부 API 1개만 제안).
- **CP-SAT 예고**: 1주차 0.5절에서 "위치는 고정, 시간만 재조정하는 매트휴리스틱 마무리 단계(3주차)"라고 이미 예고했던 것이 바로 이번 문서의 Task 2~3입니다.

### 0.4 3주차에서 새로 등장하는 핵심 개념 미리보기

아래는 짧은 정의만 먼저 두고, 본문(Task 2, 5, 7, 8)에서 실제 코드와 함께 자세히 설명합니다.

- **CP-SAT / 제약 프로그래밍(Constraint Programming)의 핵심 어휘 — interval, no-overlap, disjunction**: CP-SAT(Google OR-Tools)는 "제약만족문제(CSP) + SAT(불리언 충족가능성) + 정수계획법"을 합친 솔버입니다. 이 프로젝트에서 자주 나오는 세 단어:
  - **interval 변수**: "시작 시각 + 길이 = 끝 시각"인 구간을 하나의 변수처럼 다루는 개념. 이번 프로젝트는 OR-Tools의 전용 `IntervalVar` 객체 대신, entry를 `IntVar`로 두고 `exit = entry + P`(P는 상수)로 직접 계산하는 **수작업(manual) 방식**으로 사실상 같은 효과를 냅니다(왜 전용 NoOverlap 제약 대신 수작업 부등식을 쓰는지는 Task 2에서 설명).
  - **no-overlap(겹침 없음) 제약**: 두 구간이 절대 시간상 겹치면 안 된다는 제약. "A가 완전히 끝난 뒤에 B가 시작하거나, B가 완전히 끝난 뒤에 A가 시작한다" 둘 중 하나(양자택일)로 인코딩합니다.
  - **disjunction(선택적 순서 제약, 논리합)**: "A 아니면 B" 형태의 제약. 이번 프로젝트에서는 "A가 먼저 완전히 끝나고 B가 시작 ∨ B가 먼저 완전히 끝나고 A가 시작"처럼, 정확히 어느 쪽이 먼저인지는 솔버가 자유롭게 고르되 **둘 중 하나는 반드시 성립**하게 강제하는 데 씁니다. CP-SAT에서는 이런 "이거 아니면 저거"를 Bool 변수 하나(`b`)와 `OnlyEnforceIf(b)` / `OnlyEnforceIf(b.Not())`로 표현합니다(Task 2에서 실제 코드로 설명).
- **재타이밍(retiming)**: 배치(bay, x, y, orient)는 완전히 고정한 채, 오직 entry/exit(시간)만 변수로 두고 Z1(지연시간)을 최소화하도록 CP-SAT로 다시 푸는 것.
- **pair_bits_at_current()**: 1주차에서 이미 정의만 해두고 안 쓰던 함수. "현재 배치된, 같은 bay의 블록 쌍마다 static/entry 충돌 비트가 켜져 있는지"를 꺼내주는 함수인데, 이번 주 재타이밍의 CP-SAT 제약을 만드는 **직접적인 입력**으로 처음 실사용됩니다.
- **배치풀(PlacementPool) / pool 스테이지**: ALNS가 예산을 쓰는 동안 지나쳐간 여러 "국소최적해"들에서, 블록별로 "이런 (bay,x,y,orient,entry) 조합도 꽤 괜찮았다"는 후보를 상위 K개씩 모아두는 자료구조와, 그 후보들을 CP-SAT로 "블록당 1개씩 골라 재조합"하는 스테이지.
- **폴리시(polish) 스테이지**: entry/exit(시간)을 완전히 고정한 채 bay만 바꿔치기(재배정·맞교환)해서 Z2/Z3만 다듬는 마지막 스테이지. "폴리시(polish, 광내기)"라는 이름 그대로, 이미 확정된 해의 표면만 다듬는다는 뜻입니다.
- **parallel.py — 병렬 ALNS 체인**: 대회 서버가 4코어라는 제약(Global Constraints) 안에서, 서로 다른 시드로 ALNS를 2~3개 동시에(fork로) 돌려 서로 결과를 교환하는 병렬화.
- **mh_cfg / `--disable {retiming,pool,polish}` / `OGC_MH_DISABLE`**: 이번 3주차 세 스테이지 각각을 켜고 끌 수 있는 설정과 토큰. 목적은 **ablation(제거 실험)** — 특정 스테이지를 꺼봤을 때 목적값이 얼마나 나빠지는지를 재서 "이 스테이지가 실제로 기여하는 정도"를 정량적으로 측정하기 위함입니다.
- **Gurobi vs CP-SAT**: 1주차에서 예고했듯 Gurobi는 라이선스 미확인으로 보류, 대신 무료 오픈소스인 CP-SAT를 씁니다. 3주차는 여기서 한 걸음 더 나아가 "나중에 라이선스가 확보되면 Gurobi로 갈아끼울 수 있도록" `pool_mip.py`를 백엔드 절연(backend-isolated) 구조로 설계합니다(Task 5에서 설명).

---

## 1. 여기부터 원본 문서 + 인라인 설명

> 아래는 원본 파일의 내용을 그대로 옮기고, 어려운 대목 바로 아래 `> 💡 설명:` 블록을 끼워 넣은 것입니다.

# OGC 2026 — 3주차: 매트휴리스틱 마무리 (CP-SAT 재타이밍 + 배치풀 선택 + Z3/Z2 폴리시 + 병렬 체인) 구현 계획

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** ALNS incumbent 위에서 예산 후반 ~20%를 쓰는 매트휴리스틱 파이프라인(CP-SAT 재타이밍 → 배치풀 선택 → Z3/Z2 폴리시)과 병렬 ALNS 체인(2~3 워커)을 완성하고, 스테이지별 기여도를 벤치 하네스로 측정한다.

**Architecture:** 재타이밍은 incumbent의 기하(bay,x,y,orient)를 고정하고 `core.pair_bits_at_current()`의 쌍별 비트(static/entry_ij/entry_ji)를 CP-SAT 시간 제약으로 인코딩해 Z1(tardiness)만 전역 재최적화한다(기하 고정 시 Z2·Z3는 상수). 배치풀은 ALNS 국소최적해에서 블록별 top-K 튜플을 모아 "블록당 정확히 1개 + 충돌쌍 배제" CP-SAT 선택 모델로 조합하며, 충돌은 기존 `place/check_place`의 쌍별 테스트로 사전계산한다. 폴리시는 entry/exit을 고정(ΔZ1≡0)한 채 bay 재배정·스왑으로 w2·Z2+w3·Z3만 내린다. 병렬화는 fork 기반 워커가 각자 Core를 만들고 `shell.improve`를 슬라이스 단위로 돌리며 Queue로 best/pool을 교환한다. 모든 스테이지는 time-box·skippable이고, 결과가 incumbent보다 나쁘면 무조건 incumbent를 유지한다.

> 💡 **설명 — 이 한 문단이 3주차 전체의 요약입니다.** 0.1~0.4절에서 이미 개념은 설명했으니, 여기서는 이 문단의 각 문장이 뒤에 나올 어느 Task와 대응하는지만 짚습니다.
> - "재타이밍은 ... Z1만 전역 재최적화한다" → Task 2(인코딩)·Task 3(`retime()` 조립).
> - "기하 고정 시 Z2·Z3는 상수" → Z2(bay 부하 불균형)·Z3(bay 선호도 손실)는 "블록이 **어느 bay**에 배정되었는가"에만 좌우되는데, 재타이밍은 bay를 안 바꾸므로(시간만 바꿈) 이 두 값은 재타이밍 전후로 절대 변하지 않는다는 뜻입니다 — 그래서 재타이밍의 목적함수는 Z1만 최소화하면 전체 목적함수(w1·Z1+w2·Z2+w3·Z3)를 최소화하는 것과 동치입니다.
> - "배치풀은 ... top-K 튜플을 모아 ... CP-SAT 선택 모델로 조합" → Task 4(풀 자료구조)·Task 5(선택 모델).
> - "폴리시는 ... ΔZ1≡0 ... bay 재배정·스왑" → Task 6.
> - "병렬화는 fork 기반 워커가 ..." → Task 7.
> - "모든 스테이지는 time-box·skippable이고, 결과가 incumbent보다 나쁘면 무조건 incumbent를 유지한다" → 이게 3주차 전체를 관통하는 **안전 원칙**입니다. 매트휴리스틱 스테이지는 "시도해서 더 좋으면 채택, 아니면(시간초과·에러·비개선 포함) 무조건 원래 해를 그대로 반환"하도록 설계되어 있습니다 — 1주차의 "3단 폴백 셸"과 같은 견고성 철학의 연장선입니다.

**Tech Stack:** Python 3.12, ortools==9.15.6755 (CP-SAT), multiprocessing(fork), 1주차 `ogc_core`(pybind11 .so) + `solver/*` 계약, pytest, `baseline/utils.py`(최종 oracle).

> 💡 **설명:**
> - **ortools**: Google이 만든 오픈소스 최적화 라이브러리 모음으로, 그 안에 CP-SAT 솔버가 들어 있습니다. `pip install ortools`로 설치하는 일반 Python 패키지입니다.
> - **multiprocessing(fork)**: 파이썬 표준 라이브러리의 병렬처리 모듈. `fork`는 Linux에서 프로세스를 복제하는 방식 중 하나로, 부모 프로세스의 메모리 상태를 그대로 복사해서 자식 프로세스를 만듭니다(Task 7에서 자세히 설명).

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

> 💡 **설명:**
> - **`num_search_workers=4`**: CP-SAT는 내부적으로 여러 스레드가 서로 다른 탐색 전략(예: 다른 변수 선택 휴리스틱)으로 동시에 같은 문제를 풀고 가장 먼저 끝난 결과를 채택하는 "포트폴리오 병렬 탐색"을 지원합니다. `num_search_workers`는 이때 쓸 스레드 수인데, 서버가 4코어뿐이므로 이 값을 4로 못박아 그 이상 쓰지 않게 합니다.
> - **"CP-SAT 정수 목적함수에 그대로 사용 가능"**: CP-SAT는 (부동소수점이 아니라) 정수 계획법 솔버라서, 목적함수나 제약의 계수가 실수면 스케일링을 거쳐야 하는데, 이 인스턴스들은 가중치·시간 값이 이미 전부 정수라 별도 변환 없이 그대로 CP-SAT 모델에 넣을 수 있습니다 — 1주차 SCALE 트릭과 같은 결의 "정수면 오차 걱정이 없다"는 이점입니다.
> - **"매트휴리스틱 결과가 incumbent보다 나쁘면(동률 포함) incumbent 유지"**: "동률 포함"이 중요합니다 — 같은 값이어도 굳이 바꾸지 않는다는 뜻인데, 이는 불필요한 해 교체로 인한 부작용(예: 부동소수점 오차 누적, 검증 재실행 비용)을 피하기 위한 보수적 규칙입니다.

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

> 💡 **설명 — 이 목록 자체는 1주차 주석본에서 이미 상세히 설명했으니, 3주차 관점에서 특히 중요한 두 줄만 짚습니다.**
> - **`pair_bits_at_current() -> list[tuple[i,j,static_hit,entry_ij,entry_ji]]`**: 1주차에서는 "3주차 CP-SAT 재타이밍 입력으로 미리 준비해둔다"고만 예고됐던 함수인데, 드디어 이번 주 Task 2~3에서 실제로 쓰입니다. 반환값 하나(`i, j, static_hit, entry_ij, entry_ji`)를 풀어보면: `i`, `j`는 같은 bay에 배치된 두 블록의 id, `static_hit`는 "이 둘이 (0.4절 j==k 규칙으로) 같은 층끼리 겹치는 자리에 놓여 있는가"(1이면 시간이 겹치면 절대 안 됨), `entry_ij`/`entry_ji`는 "i(또는 j)가 크레인으로 들어오거나 나갈 때 j(또는 i)가 그 스윕 경로에 걸리는가"를 나타내는 비트입니다. 이 세 비트가 바로 Task 2의 CP-SAT 제약을 만드는 원재료입니다.
> - **"오프셋은 기하에만 의존 → 시간을 바꿔도 불변"**: 이 한 줄이 재타이밍이 성립하는 핵심 전제입니다. `static_hit`·`entry_ij`·`entry_ji`는 "두 블록이 **상대적으로 어디에** 놓여 있는가"(오프셋 dx, dy)만으로 결정되는 값이라서, 두 블록의 (bay, x, y, orient)가 그대로인 한 entry/exit 시각을 아무리 바꿔도 이 비트 값 자체는 절대 바뀌지 않습니다. 그래서 재타이밍 시작 시 딱 한 번만 `pair_bits_at_current()`를 호출해서 "이 인스턴스에서 시간상 조심해야 할 블록 쌍과 그 종류"를 확정해두고, 그 이후로는 시간(entry)만 이리저리 바꿔가며 최적화해도 안전합니다.

2주차 가정(내부 구현에 의존 금지, 아래 시그니처만):
- `solver.shell.improve(core, incumbent, budget) -> Placements` — ALNS 개선 루프. budget 준수, incumbent를 warm-start로 받아 같거나 더 좋은 해 반환. 슬라이스 호출(짧은 Budget으로 반복 호출) 가능 — 이것이 "placement-pool-friendly" 사용법이며, 슬라이스마다 반환되는 해를 국소최적해로 간주해 풀에 적립한다.

> 💡 **설명 — "슬라이스 호출"과 "placement-pool-friendly"란:** `improve`를 한 번에 긴 시간(예: 100초) 통째로 호출하는 대신, 짧은 시간(예: 6초)짜리 `Budget`을 여러 번 반복해서 호출하는 사용 패턴을 말합니다. 매번 짧게 끊어 호출하면, "그 6초 동안 ALNS가 도달한 국소최적해"를 매 슬라이스마다 하나씩 얻을 수 있습니다 — 이렇게 얻은 여러 개의 "그런대로 괜찮은 해"들을 나중에 배치풀(Task 4)에 차곡차곡 적립해서, Task 5에서 "그 후보들 중 최선의 조합"을 다시 골라내는 재료로 씁니다. 한 번에 길게 돌렸다면 이런 중간 스냅샷들을 못 얻고 최종 결과 하나만 남았을 것입니다 — "여러 번 짧게 끊어 호출하는 것이 풀 적립에 유리하다(pool-friendly)"는 뜻입니다.

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

> 💡 **설명 — 큰 그림:** `solver/mh/`가 이번 주 새로 생기는 패키지로, "매트휴리스틱(matheuristics)"의 약자입니다. 그 안에 재타이밍·풀·풀-선택모델·폴리시 네 모듈이 나란히 들어갑니다. `parallel.py`는 `solver/mh/` 밖(즉 `solver/` 바로 아래)에 있는데, 이는 병렬화가 매트휴리스틱 고유 개념이 아니라 ALNS(2주차 L3)를 포함해 셸 전체에 걸친 실행 방식이기 때문입니다. `shell.py`는 1·2주차에 이미 있던 파일을 계속 확장해나가는 것이고, `beam.py`는 "여유 시간이 있으면"이라는 조건부 선택 과제입니다.

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

> 💡 **설명 — 이 계약 전체를 매트휴리스틱 파이프라인 관점에서 한 줄씩 훑으면:**
> - `retime`/`select_from_pool`/`polish` 세 함수가 전부 **같은 모양**(`(core, prob, ..., budget_s, ...) -> tuple[Placements, dict]`)을 하고 있다는 점에 주목하세요. "항상 (해, stats) 반환, 나쁘면 원래 해 반환"이라는 동일한 안전 계약을 공유하기 때문에, Task 8의 `run_matheuristics`가 이 세 함수를 **똑같은 방식으로** 순서대로 호출할 수 있습니다(플러그인처럼 교체 가능한 구조).
> - `PlacementPool`의 `dump()`/`merge()`가 "picklable"이라고 명시된 이유는 Task 7의 멀티프로세싱 때문입니다 — 여러 프로세스(워커) 간에 파이썬 객체를 주고받으려면 `multiprocessing.Queue`를 쓰는데, 이 큐는 내부적으로 `pickle`(파이썬 객체 직렬화 표준 라이브러리)로 객체를 바이트로 변환해 전달합니다. `PlacementPool` 객체 자체(클래스 인스턴스)를 큐에 바로 넣는 대신, `dump()`로 "순수한 튜플 리스트"(항상 pickle 가능)로 바꿔서 보내고 받는 쪽이 `merge()`로 다시 풀에 합칩니다.
> - `MhCfg`의 필드들(`mh_frac, retiming_frac, pool_frac, polish_frac`)은 전부 "예산을 몇 %씩 나눠쓸지"를 나타내는 비율입니다 — Task 8에서 실제 계산식과 함께 설명합니다.
> - `run_matheuristics`가 `stats` 딕셔너리를 인자로 **직접 받아 채워넣는** 방식(반환하지 않고 부작용으로 기록)인 것은, Task 9의 벤치 하네스가 나중에 이 `stats`를 열어서 "어느 스테이지가 얼마나 기여했는지" 리포트를 뽑기 위함입니다.

---

### Task 1: ortools 설치 고정 + solver/mh 패키지 스캐폴딩

**Files:**
- Create: `solver/mh/__init__.py`
- Test: `tests/test_retiming.py` (import smoke 부분)

**Interfaces:**
- Consumes: 없음 (환경 준비).
- Produces: `import solver.mh` 가능, `from ortools.sat.python import cp_model` 가능, ortools 버전 == 9.15.6755.

> 💡 **설명 — 이 Task는 1주차 Task 1(스캐폴딩)과 완전히 같은 패턴입니다.** "아직 실제 기능은 없지만 빌드/import가 되는 뼈대부터 세운다"는 원칙, TDD 순서(① 실패 테스트 → ② 최소 구현 → ③ 커밋)도 동일합니다. 새로 등장하는 것은 `ortools`라는 외부 패키지 하나뿐입니다.

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

> 💡 **설명 — `cp_model.CpModel()`, `NewIntVar`, `CpSolver` — CP-SAT의 가장 기본 어휘:** 이 테스트가 CP-SAT를 처음 만져보는 최소 예제입니다. `CpModel()`은 빈 제약모델을 만드는 객체(1주차의 `ogc_core.Core()`처럼, 상태를 담는 컨테이너), `NewIntVar(0, 3, "x")`는 "0 이상 3 이하의 정수 변수 x를 하나 만들어라"는 뜻(변수 이름 `"x"`는 디버깅용 라벨), `m.Add(x >= 2)`는 제약 하나를 등록, `CpSolver()`는 실제로 풀이를 수행하는 엔진, `.Solve(m)`은 그 모델을 풀어서 상태 코드(`OPTIMAL`/`FEASIBLE`/`INFEASIBLE`/...)를 반환합니다. `OPTIMAL`은 "이게 증명된 최적해다"라는 뜻 — 뒤에 나올 재타이밍·풀선택 모두 이 네 가지 부품(모델, 변수, 제약, 솔버)의 조합일 뿐입니다.

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

> 💡 **설명 — 이 Task의 별명이 "재타이밍의 심장"인 이유:** 1주차 `required()` 함수(Task 5, "utils 의미론의 심장")가 "두 블록의 시간 관계가 주어졌을 때 충돌하는지 판정"하는 함수였다면, 이번 `add_pair_time_constraints`는 그 반대 방향입니다 — "충돌하면 안 된다는 조건(비트)이 주어졌을 때, 그걸 만족하는 시간 배정을 CP-SAT가 찾아내도록 부등식 제약을 만들어 붙이는" 함수입니다. 즉 1주차가 "판정 함수"였다면 3주차는 그 판정 함수를 **거꾸로 뒤집어 제약으로 인코딩**한 것 — 같은 진리표를 공유하지만 방향이 반대라는 점이 핵심입니다.

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

> 💡 **설명 — 이 표와 유도 과정을 처음부터 풀어서 읽기:**
>
> **1) 왜 "여집합"을 인코딩하는가:** 1주차 `required()` 표는 "이 조건을 만족하면 충돌(금지)"라는 형태였습니다. CP-SAT에게는 반대로 "이 부등식을 만족하는 해만 찾아라"고 말해야 하므로, "금지 조건이 성립하지 않는 경우"(여집합, complement)를 제약으로 등록합니다. 예를 들어 "`eB < eA < xB`면 안 된다"의 여집합은 "`eA ≤ eB` 이거나 `eA ≥ xB`"입니다 — 이게 위 "ENTRY 측" 줄의 `e_i ≤ e_j − δe ∨ e_i ≥ e_j + P_j`로 정리된 것입니다(부등호 방향과 `δe` 보정은 same-day 규약의 등호 케이스를 처리하기 위함).
>
> **2) `δe`, `δx`가 하는 일 — 등호(같은 날) 케이스의 방향성:** 순수하게 "구간이 안 겹친다"만 따지면 등호(`e_i == e_j`)는 애매한 경계입니다. same-day 규약("같은 날 ENTRY는 id 오름차순")이 이 애매함을 해소해줍니다 — `id_i > id_j`(i가 나중 순번)면 "i의 ENTRY 시각은 j의 ENTRY 시각보다 **반드시 하루라도 빨라야** 하거나(`δe=1`이므로 `e_i ≤ e_j - 1`, 즉 진짜로 먼저), 아니면 j가 완전히 나간 뒤에 들어와야 한다"가 되고, `id_i < id_j`(i가 먼저 순번)면 `δe=0`이라 `e_i ≤ e_j`(같은 날도 허용, i가 먼저이므로)가 됩니다. 이 하나의 정수 상수(0 또는 1)로 "같은 날이면 누가 먼저인가"라는 규약을 부등식에 자연스럽게 녹여낸 것이 이 인코딩의 핵심 트릭입니다.
>
> **3) "흡수(subsumption)" — 왜 static 쌍은 이벤트 제약을 따로 안 만드는가:** `static_hit=1`인 쌍은 "가만히 있어도 같은 층끼리 겹치는" 관계라서, 애초에 이 둘의 체류 구간이 조금이라도 겹치면 무조건 안 됩니다(entry/exit 이벤트를 따질 필요도 없이). 그래서 "완전히 안 겹치게"(no-overlap, `e_i+P_i ≤ e_j` 이거나 `e_j+P_j ≤ e_i`) 하나만 강제하면, "ENTRY/EXIT 이벤트가 상대 체류 구간 안에 들어가면 안 된다"는 더 약한 조건은 자동으로 만족됩니다(구간이 아예 안 겹치니 이벤트도 당연히 안 겹침) — 그래서 제약을 중복으로 걸 필요가 없습니다. 이게 "no-overlap이 이벤트 제약을 흡수한다"는 말의 의미입니다.
>
> **4) `OnlyEnforceIf` — CP-SAT에서 "이거 아니면 저거"(disjunction)를 표현하는 표준 관용구:** CP-SAT의 제약은 기본적으로 "항상 성립해야 하는" 것들입니다. "A 아니면 B"(둘 중 하나만 성립하면 됨)를 표현하려면, Bool 변수 `b`를 하나 만들고 `model.Add(A조건).OnlyEnforceIf(b)`(b가 참일 때만 A 강제) + `model.Add(B조건).OnlyEnforceIf(b.Not())`(b가 거짓일 때만 B 강제)로 감쌉니다. 솔버는 `b`를 참으로 할지 거짓으로 할지 자유롭게 고를 수 있으므로, 결과적으로 "A 이거나 B 중 최소 하나는 반드시 성립"하는 것과 동일한 효과를 냅니다 — 이게 위에서 예고한 "disjunction(선택적 순서 제약)"의 실제 구현 방법입니다. 아래 코드의 `v1`, `v2`, `b` 변수가 전부 이 역할입니다.

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

> 💡 **설명 — 이 테스트가 검증 전략으로서 특히 훌륭한 이유:** `enumerate_all_solutions=True` + `SearchForAllSolutions`는 CP-SAT에게 "최적해 하나만 말고, 이 제약을 만족하는 **모든** (e_i, e_j) 조합을 다 나열해달라"고 요청하는 기능입니다. `e_i, e_j`의 범위가 `0..7`(`HI=7`)로 작으니 전수 나열이 가능하고, 그 나열된 집합을 "1주차 표를 그대로 옮긴 순수 파이썬 함수"(`_pair_time_ok`)가 계산한 "정답 집합"과 **완전히 일치**하는지(`cb.sols == expected`) 확인합니다. 즉 1주차 Task 3의 fuzz 테스트, Task 4/5의 전수 대조 테스트와 같은 계열의 "전수조사로 오라클과 비교"하는 검증 전략을 CP-SAT 제약에도 그대로 적용한 것입니다. `pytest.mark.parametrize`를 이중으로 걸어(`bits` 6종 × `ids` 2종) 총 12가지 조합을 자동으로 다 돌립니다 — 6가지 비트 조합은 static/entry_ij/entry_ji가 켜지고 꺼지는 모든 의미있는 경우, `ids` 2종은 "i가 더 큰 id일 때"와 "j가 더 큰 id일 때"를 모두 검증해 same-day 규약의 방향성 버그(`δe`/`δx` 부호 실수)를 잡기 위함입니다.

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

> 💡 **설명 — 코드를 표와 대조해 짚기:**
> - `_add_event_side(m, e, p, a, b)`: 표의 "ENTRY 측"·"EXIT 측" 두 줄을 한 함수에 합친 것입니다. `v1`(Bool)로 "a의 ENTRY가 b보다 먼저"인 경우와 "a의 ENTRY가 b의 EXIT 이후"인 경우를 `OnlyEnforceIf`로 양자택일시키고(ENTRY 측), `v2`로 "a의 EXIT가 b의 ENTRY 이전"과 "a의 EXIT가 b의 EXIT 이후"를 양자택일시킵니다(EXIT 측). 함수 이름의 "event side"는 "이벤트(ENTRY 혹은 EXIT)를 기준으로 한쪽 방향"이라는 뜻이고, `add_pair_time_constraints`가 필요에 따라 `_add_event_side(..., i, j)`와 `_add_event_side(..., j, i)`를 **양방향으로** 호출해서 "i가 j에 걸리는 경우"와 "j가 i에 걸리는 경우"를 모두 커버합니다(표의 "대칭: B의 이벤트가 A 존재 중" 행).
> - `static_hit` 분기의 `b = m.NewBoolVar(...)`는 "완전히 순서가 갈리는 두 경우"(i 전체가 먼저 vs j 전체가 먼저) 중 하나를 고르는 가장 단순한 disjunction으로, no-overlap 제약의 표준 형태입니다.
> - 함수에 `return`이 있어 `static_hit`이면 이벤트 제약을 아예 안 만들고 끝냅니다 — 바로 위에서 설명한 "흡수(subsumption)" 최적화가 여기 구현되어 있습니다.

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

> 💡 **설명 — 이 Task가 하는 일을 한 문장으로:** Task 2가 만든 "쌍 하나에 대한 제약 인코딩 부품"을 실제 인스턴스의 **모든** 쌍에 대해 조립해 하나의 완전한 CP-SAT 모델을 만들고, 풀고, 결과를 다시 `Placements` 형식으로 꺼내고, 안전한지(더 나빠지지 않았는지) 검증까지 하는 "조립 라인"입니다.

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

> 💡 **설명 — 각 테스트가 검증하는 시나리오를 메타휴리스틱 검증 방법론 관점에서:**
> - `test_retime_pulls_single_block_to_release`: 가장 단순한 "손으로 반례를 만든" 케이스(unit test) — 블록 1개를 일부러 5일 늦게 넣어놓고, 재타이밍이 "당연히 더 당길 수 있는데 왜 안 당겼지?"를 고쳐서 release(가장 이른 가능 시각)까지 되돌리는지 확인합니다.
> - `_find_static_pair` + `test_retime_reorders_static_pair`: 두 블록이 **같은 자리를 시간을 나눠 써야** 하는(static 충돌) 상황을 억지로 만들고, 순서를 일부러 나쁘게(느긋한 블록이 먼저, 급한 블록이 나중) 배치한 뒤, 재타이밍이 두 블록의 **순서 자체를 뒤집어서** 지각을 0으로 만드는지 확인합니다. 이건 재타이밍의 진짜 능력을 보여주는 테스트입니다 — 단순히 "당길 수 있으면 당긴다"를 넘어서, "누가 먼저 그 자리를 쓸지"라는 **조합적 결정**(disjunction의 `OnlyEnforceIf` 방향)까지 CP-SAT가 알아서 최적으로 골라준다는 것을 보여줍니다.
> - `test_retime_full_instance_never_worse_and_feasible`: 실제 100블록 인스턴스에서 "① Z1은 절대 악화되지 않고, ② Z2/Z3는 (기하를 안 건드렸으니) 정확히 그대로 불변이며, ③ 최종적으로 utils 오라클이 feasible 판정한다"는 세 가지를 한 번에 검증하는 **통합(integration) 테스트**입니다.
> - `test_retime_zero_budget_returns_incumbent`: 예산이 거의 없을 때(`budget_s=0.05`) 억지로 CP-SAT를 돌리려 하지 않고 그냥 원래 해를 그대로 돌려주는지 확인 — Task 8에서 이 스테이지가 "예산이 부족하면 건너뛴다(skippable)"는 전체 설계 원칙의 최소 단위 테스트입니다.

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

> 💡 **설명 — 함수 전체를 위에서 아래로 흐름 따라가기:**
> - **가드(guard) 조건 먼저**: `budget_s < 0.2 or not placements`면 즉시 "SKIPPED" 상태로 원래 해를 그대로 돌려줍니다. 매트휴리스틱 스테이지의 표준 시작 패턴 — 애초에 시도할 가치가 없으면 CP-SAT 모델조차 만들지 않습니다.
> - **변수 생성 구간(`e, p = {}, {}` 블록)**: 블록마다 entry용 `IntVar`를 만드는데, 도메인을 `[release, horizon-P]`로 제한해 "release 이전으로 당길 수 없다"(물리적으로 아직 반입 안 됨)와 "끝까지 밀려도 horizon을 넘지 않는다"를 자동으로 강제합니다. **`m.AddHint(e[blk], int(ent))`**가 핵심 — 이건 "정답을 알려주는" 게 아니라, "여기서부터 탐색을 시작해보라"는 **웜스타트(warm start) 힌트**입니다. CP-SAT는 이 힌트 근방에서 먼저 개선을 시도하는 경향이 있어서, 이미 상당히 괜찮은 incumbent를 갖고 있을 때 처음부터 다시 탐색하는 것보다 훨씬 빨리 "적어도 이만큼은 되는" 해에 도달합니다.
> - **`horizon`**: 시간 변수의 상한선. "incumbent의 가장 늦은 EXIT + 가장 긴 처리시간 + 1"로 넉넉히 잡아서, 재배열 과정에서 살짝 더 늦춰야 하는 경우도 표현 가능한 여유를 둡니다.
> - **tardiness 변수(`tard_terms`)**: 각 블록마다 "지각시간"을 나타내는 변수 `t`를 만들고 `t >= entry + P - due`(즉 `t >= exit - due`)로 제약합니다. 이 부등식만 걸고 `t`의 하한을 명시 안 하는 이유는, `t`의 정의역이 `[0, horizon]`이라 자동으로 "지각이 없으면 0"이 되기 때문입니다(CP-SAT가 목적함수를 최소화하려 하므로 굳이 클 필요가 없는 `t`는 최소 가능한 값 즉 `max(0, exit-due)`로 수렴). 이게 정확히 Z1(`Σ max(0, EXIT-due)`)의 CP-SAT식 표현입니다.
> - **쌍 제약 등록 루프**: `core.pair_bits_at_current()`가 반환한 모든 쌍을 순회하며 Task 2의 `add_pair_time_constraints`를 호출합니다. `if static_hit or entry_ij or entry_ji`로 "셋 다 0인(아예 무관한) 쌍"은 건너뛰어 불필요한 제약 생성을 피합니다(계약에서 "all-zero 쌍이 포함되어 와도 무해"라 했던 부분이 여기서 실제로 필터링됩니다).
> - **`m.Minimize(sum(tard_terms))`**: "w1 곱은 상수배라 생략"이라는 주석의 의미 — 목적함수가 `w1 * Σtard`이든 `Σtard`이든, `w1`이 양수 상수인 이상 "이 값을 최소화하는 해"는 동일합니다(상수를 곱해도 대소관계가 안 바뀌므로). 그래서 모델 자체는 `w1`을 곱하지 않고 단순히 `Σtard`만 최소화하고, 실제 보고용 obj 계산 시에만 나중에 `w1`을 곱합니다 — 계산을 단순화하는 흔한 트릭입니다.
> - **결과 추출과 이중 안전장치**: 풀이 후 `new_pl`을 만들어 코어에 다시 로드하고, `core.verify_full()`(1주차에서 배운 "코어 스스로 Stage1~5 의미론을 재검증"하는 함수)이 빈 리스트가 아니면(즉 뭔가 문제가 있으면) 무조건 원래 `placements`로 원복합니다 — "우리가 짠 CP-SAT 인코딩이 혹시 틀렸더라도, 최종적으로 잘못된 해가 나가는 일은 없다"는 방어선입니다. 그 다음 `z1_after >= z1_before`이면(동률 포함 비개선) 또 원복 — Global Constraints에서 요구한 "동률 포함 비악화 시 incumbent 유지" 규칙이 여기서 구현됩니다.

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

> 💡 **설명 — "배치풀(PlacementPool)"이 왜 필요한가, 큰 그림:** ALNS(2주차)가 예산의 80%를 쓰는 동안, 매 순간 "현재 최선의 해" 하나만 기억하고 그 이전 시도들은 다 버려집니다. 하지만 "지금은 최선이 아니지만, 특정 블록 하나만 놓고 보면 꽤 괜찮았던 배치"들이 탐색 과정에서 많이 스쳐 지나갑니다. `PlacementPool`은 그런 "블록별로 봤을 때 준수했던 후보들"을 계속 모아두는 저장소입니다 — 나중에(Task 5) 이 저장소에서 "블록마다 그 중 하나씩 골라 최선의 조합을 다시 짜맞추면 어떨까?"라는 완전히 다른 종류의 최적화(조합 최적화)를 해볼 수 있게 됩니다. 비유하면, ALNS가 "완성된 요리 여러 개를 만들어봤는데 매번 좋은 재료 조합이 다 달랐다"면, 배치풀은 "그동안 봤던 좋은 재료(블록별 후보)들을 모아뒀다가, 그 중에서 최고의 조합을 다시 골라보는" 단계입니다.

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

> 💡 **설명 — 각 테스트가 확인하는 규칙:**
> - `test_add_dedups_and_counts`: 완전히 똑같은 튜플을 두 번 넣으면 두 번째는 무시됩니다(중복 제거) — 풀에 같은 걸 여러 번 넣어 낭비하지 않기 위함.
> - `test_topk_evicts_worst_only`: `k=2`(블록당 최대 2개만 보관)로 설정했을 때, 더 좋은 후보가 새로 들어오면 **가장 나쁜 기존 후보를 쫓아내고**(evict) 자리를 내주지만, 새 후보가 기존의 "가장 나쁜 것"보다도 못하면 아예 안 받아준다는 것(`add(...) is False`)을 확인합니다. 이건 "우선순위 큐(priority queue)"에서 흔히 쓰는 "고정 크기 상위 K개 유지" 패턴과 동일합니다.
> - `test_quality_prefers_low_tard_then_pref`: "품질(quality)"을 판단하는 기준이 무엇인지 보여줍니다 — 지각이 같다면(둘 다 tard=0), 선호 bay에 놓인 쪽이 더 좋은 후보로 평가됩니다(비용 함수가 아래 구현에서 `w1*tard + w3*pref`이기 때문).
> - `test_add_solution_and_dump_merge_roundtrip`: `add_solution`(완성된 해 하나를 통째로 풀에 넣기), `dump`/`merge`(직렬화 왕복)가 서로 잘 맞물리는지 확인. `pickle.loads(pickle.dumps(dumped)) == dumped`는 "이 데이터가 정말로 순수 파이썬 자료구조(튜플의 리스트)라서 pickle로 문제없이 왕복된다"는 것을 명시적으로 검증하는 것 — Task 7의 `multiprocessing.Queue`가 내부적으로 정확히 이 pickle 왕복을 수행하기 때문에 미리 여기서 보장해두는 것입니다.

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

> 💡 **설명 — 구현 세부사항:**
> - **`_cost`가 `exit`이 아니라 `entry`만 받는 이유**: `exit = entry + processing_time`이고 `processing_time`은 블록마다 고정된 상수이므로, `entry`만 알면 `exit`도 바로 계산할 수 있습니다(`tard = max(0, entry + processing_time - due_date)`). 그래서 튜플의 자유도는 사실상 `(bay, x, y, orient, entry)` 5개뿐이고, 이게 `_by_block`의 딕셔너리 키(`key = (bay, x, y, orient, entry)`)입니다 — Task 4 계약에서 `entries()`가 `exit`을 빼고 5-튜플만 반환하는 이유이기도 합니다.
> - **`max(d, key=d.get)`**: 파이썬 관용구로, 딕셔너리 `d`에서 "값(`d.get`, 즉 비용)이 가장 큰 키"를 찾습니다. 비용이 클수록 나쁜 후보이므로, 이게 "가장 나쁜 기존 후보"를 찾는 코드입니다.
> - **`sorted(d, key=d.get)`**: 반대로 비용이 작은(좋은) 순으로 키를 정렬 — 계약에서 "품질 오름차순(좋은 것부터)"이라 했던 부분이 이 한 줄입니다.
> - **`dump()`의 `(b, *key)`**: 파이썬의 언패킹(unpacking) 문법으로, `key`가 `(bay,x,y,orient,entry)`라면 `(b, *key)`는 `(b, bay, x, y, orient, entry)`라는 평평한(flat) 6-튜플이 됩니다 — 중첩된 구조 없이 순수한 튜플의 리스트라서 pickle이 항상 안전합니다.
> - 이 클래스에 대회 도메인 특유의 복잡한 로직은 없고, "고정 크기 K를 유지하는 최선 후보 저장소"라는 자료구조 패턴 자체는 유전 알고리즘의 "엘리트 보존(elitism)"이나 빔서치의 "상위 B개 유지"와 본질적으로 같은 아이디어입니다.

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
- **Z2 선형화(근사, 문서화)**: `L_j = Σ workload*s`, `u_j = avg_area/area_j`를 `U_j = round(u_j*SCALE)` 정수로 스케일하고 `D ≥ ±(U_a*L_a − U_b*L_b)` (bay 쌍 전부) → `w2*D` 가산. 두 가지 근사를 명시적으로 수용한다: ① utils의 `floor()` 생략(오차 ≤ w2·1), ② U 반올림(상대오차 ≤ 1e-6). **최종 채점은 항상 core.objective()/utils로 재계산**하므로 모델 오차는 해 선택 품질에만 영향, 정답성에는 무영향.

충돌 판정은 계약의 기존 API만 사용: 빈 코어에 t1을 `place`(풀 튜플은 feasible 해 출신이라 단독 배치는 항상 성공 — 단항 제약뿐)하고 t2를 `check_place` — 1주차 Task-5의 `required()`가 쌍 양방향(entry_ij·entry_ji)을 모두 검사하므로 한 방향 호출로 쌍 판정이 완결된다. 프루닝: 다른 bay → 충돌 불가, 같은 블록 → ExactlyOne이 배제, 시간 무접촉(`e1 ≥ x2 or e2 ≥ x1`) → 제약 없음(Task-5 표 1행).

> 💡 **설명 — 이 모델이 무엇을 하는 조합 최적화인지 먼저 그림으로 이해하기:** Task 4의 `PlacementPool`이 "블록마다 최대 K개의 좋은 후보"를 모아뒀다면(예: 100블록 × 6개 = 최대 600개 후보 튜플), 이번 Task는 그 600개 중에서 "**블록당 정확히 1개씩**, 그리고 **서로 시공간이 충돌하지 않게**" 골라내는 조합을 CP-SAT로 찾습니다.
>
> - **`ExactlyOne`**: CP-SAT의 표준 제약 헬퍼로, "주어진 Bool 변수 목록 중 정확히 하나만 참(true)이어야 한다"는 뜻입니다. 여기서는 "블록 하나에 대한 여러 후보 튜플 중 정확히 하나만 최종 해에 채택된다"는 규칙을 나타냅니다 — 당연히 한 블록은 한 곳에만 놓일 수 있으니까요.
> - **충돌쌍 `¬s[t1] ∨ ¬s[t2]`**: "t1과 t2가 서로 충돌한다면, 최소한 하나는 선택되면 안 된다"(둘 다 선택되는 일은 없어야 한다)는 뜻입니다. 이건 그래프 이론의 **독립집합(independent set)** 문제와 정확히 같은 구조입니다 — "충돌 그래프"에서 변이 없는 정점들만 골라야 하는 문제.
> - 이 두 제약을 합치면 결국 "**블록당 정확히 1개** 선택 + **선택된 것들끼리는 서로 충돌 없음**"이라는, 그래프 색칠/집합 커버링과 비슷한 결합 최적화 문제가 됩니다. ALNS 혼자서는 "여러 좋은 부분해 조각들을 통째로 재조합"하는 이런 전역적 조합 탐색을 하기 어렵지만, CP-SAT는 이런 조합적 제약을 정확히 잘 다룹니다 — 이게 매트휴리스틱이 매력적인 두 번째 이유입니다(첫 번째는 재타이밍의 시간 최적화).

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

> 💡 **설명 — `test_pairwise_conflicts_detects_overlap`을 읽는 요령:** Task 3에서 이미 본 `_find_static_pair`와 똑같은 탐색을 이 파일 안에서 다시 반복해 구현하고 있는데, 주석에 "계획 규칙: 태스크 간 코드 참조 금지"라고 적혀 있습니다. 이는 각 Task의 테스트 파일이 서로의 헬퍼 함수를 import해서 재사용하지 않고 **독립적으로 자기 완결적**이도록 하는 이 프로젝트의 관례입니다 — 나중에 Task 3의 헬퍼가 리팩터링되어도 Task 5 테스트가 영향받지 않게 하기 위한 결합도(coupling) 낮추기 전략입니다. `test_unknown_backend_raises`는 "Gurobi 백엔드로 바꾸는 기능이 아직 구현 안 됐지만, 존재하지 않는 백엔드를 요청하면 명확한 에러를 내야 한다"는 것을 미리 계약해두는 테스트 — 나중에 Gurobi가 실제로 drop-in될 때 이 테스트가 "이제 이 백엔드도 지원해야 한다"로 자연스럽게 바뀔 자리입니다.

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

> 💡 **설명 — 함수별로 쪼개서 이해하기:**
>
> **`_pairwise_conflicts`**: 후보 튜플들을 bay별로 묶고(`by_bay`, 다른 bay끼리는 애초에 겹칠 수 없으니), 같은 bay 안에서 두 튜플씩 짝지어 "코어에게 물어봐서" 충돌 여부를 판정합니다. 여기서 재사용하는 트릭이 인상적입니다 — "빈 코어에 t1을 `place`하고, t2를 `check_place`로 물어본다"는 건 **완전히 새로운 충돌판정 코드를 짜지 않고, 1주차에 이미 만들어둔 `place`/`check_place`(내부적으로 `required()` 판정을 씀)를 그대로 재활용**하는 것입니다. `assert ok`(t1의 단독 배치는 항상 성공해야 함)는 "풀에 들어있는 튜플은 애초에 어떤 feasible한 해에서 나온 것이므로, 최소한 혼자 놓았을 때는 반드시 배치 가능해야 한다"는 불변식(invariant)을 검증하는 방어적 assert입니다. 시간 무접촉 쌍은 아예 코어에 물어보지도 않고 건너뛰는(`if e1 >= xt2 or e2 >= xt1: continue`) 최적화도 들어 있습니다.
>
> **`_build_candidates`**: incumbent의 튜플을 **가장 먼저** 후보 목록에 넣는 것이 핵심 설계입니다("incumbent 먼저 → 모델 항상 feasible"). 왜 이게 중요한가 하면, 만약 풀에 있는 후보들만으로 모델을 짰는데 어떤 블록이 풀에 후보가 하나도 없거나(예: k=6인데 그 블록에 대해 딱 6개 미만만 쌓였다면 상관없지만, 혹시 버그로 0개인 경우) ExactlyOne을 만족할 수조차 없다면 모델 자체가 infeasible해집니다. incumbent 자체는 이미 검증된 feasible 해이므로, "최소한 incumbent 그대로 선택하는 조합은 항상 가능하다"는 것을 보장해두면 CP-SAT 모델이 절대 infeasible이 될 수 없습니다 — 이게 계약에서 "incumbent 튜플을 풀에 자동 주입해 모델이 항상 feasible"이라 했던 부분의 구현입니다.
>
> **`_solve_cpsat`의 Z2 선형화 — min-max를 선형 제약으로 바꾸는 표준 트릭:** Z2는 원래 "모든 bay 쌍 중 부하 차이(정규화된)의 **최댓값**"인데, "최댓값을 최소화하라"는 min-max 목적함수는 그 자체로는 선형이 아닙니다(최댓값 연산 자체가 비선형적). 이를 선형 정수계획법(CP-SAT가 다루기 좋은 형태)으로 바꾸는 표준 기법이 "여유 변수(slack variable) `D`를 하나 도입해서, `D`가 **모든** bay 쌍의 차이보다 크거나 같다고 제약한 뒤 `D` 자체를 최소화"하는 것입니다. 코드의 `for j1 ... for j2 ...` 이중루프에서 `m.Add(D >= lhs1 - lhs2)`와 `m.Add(D >= lhs2 - lhs1)`를 **모든 bay 쌍**에 걸어두면, `D`는 필연적으로 "가장 큰 차이"보다 크거나 같아야만 하고, 목적함수가 `D`를 최소화하려 하므로 결국 `D`는 정확히 "가장 큰 차이"값으로 수렴합니다 — `|A - B|`를 선형 부등식 두 개(`D≥A-B`, `D≥B-A`)로 표현하는 것과 완전히 같은 원리를 "여러 쌍 중 최댓값"으로 확장한 것입니다.
> - `U_j = round(avg_area/area_j * SCALE)`: utils의 Z2 공식에서 "면적이 작은 bay일수록 부하에 더 민감하게" 정규화하는 계수 `u`(1주차 0.3절에서 이미 설명)를, 정수 계획법에 넣기 위해 `SCALE=10^6`을 곱해 정수로 반올림한 것입니다. 이 반올림이 "근사"의 원천 중 하나입니다.
> - **명시된 두 근사와 왜 무해한가**: ① utils의 실제 Z2 계산은 최종에 `math.floor()`(소수점 이하 버림)를 한 번 더 씌우는데, 이 모델은 그 floor를 생략합니다(오차가 최대 `w2 * 1`로 미미). ② `U_j`를 정수로 반올림하면서 생기는 상대오차(`≤ 1e-6`)도 무시할 만합니다. 이 두 근사는 "CP-SAT가 어떤 조합을 고를지 판단하는 데만" 살짝 영향을 줄 수 있지만(즉 아주 근소한 차이로 차선책을 고를 가능성), **정답성 자체는 전혀 위협하지 않습니다** — 왜냐하면 `select_from_pool` 마지막에서 실제로 채택할지 말지는 이 근사된 모델 값이 아니라 `core.objective()`로 다시 계산한 **정확한** `full_obj()` 값끼리 비교(`obj_new >= obj_inc`)하기 때문입니다. "모델은 근사해서 빠르게 후보를 찾고, 최종 판정은 항상 정확한 값으로 한다"는 이 원칙이 매트휴리스틱에서 근사를 안전하게 쓰는 표준적인 방법입니다.
> - **`SCALE = 10^6`**: 1주차의 좌표 스케일(`SCALE=10^4`)과는 다른, 이 모델 전용의 별도 정수화 배율입니다. 목적함수 항들(`tard`, `pref`, 정규화계수 `U`)을 정수 계산으로 다루기 위한 배율일 뿐, 좌표 스케일과는 무관합니다.
> - **백엔드 절연(backend isolation)**: `_solve_cpsat(prob, cand, conflicts, inc_idx, budget_s, workers)`라는 함수 하나에 "실제로 풀이 엔진에 넘기는 부분"을 전부 가둬놓았습니다. 나중에 Gurobi 라이선스가 확보되면, 이 함수와 정확히 같은 입력/출력 형태를 갖는 `_solve_gurobi(...)`를 만들어 `select_from_pool`의 분기(`if backend == "cpsat": ... elif backend == "gurobi": ...`)만 추가하면 되고, 모델을 조립하는 `_build_candidates`나 충돌 판정 `_pairwise_conflicts`, 바깥 껍데기 `select_from_pool`은 전혀 손댈 필요가 없습니다 — 이게 "백엔드 절연 구조"이자 "drop-in 가능"의 실제 의미입니다.
>
> **`select_from_pool` 바깥 함수의 흐름**: Task 2~3의 `retime()`과 완전히 같은 안전장치 패턴을 반복합니다 — 예산 부족 시 SKIPPED, 모델이 infeasible/시간초과면 incumbent 원복, `verify_full()`로 자체검증, 마지막으로 **실측(근사 없는) objective 비교**로 최종 채택 여부를 결정. 유일하게 새로 보이는 것은 `new_pl = sorted(cand[t] for t in chosen)`인데, 이는 튜플이 `(blk, bay, x, y, o, e, xt)` 형태라서 정렬하면 자동으로 `blk`(첫 원소) 오름차순이 되어, 뒤에 나올 직렬화·디버깅 시 "블록 id 순으로 가지런한" 결과를 얻기 위한 사소한 가독성 배려입니다.

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

> 💡 **설명 — 이 스텝이 "실측"인 이유:** 이론적으로 몇 초가 걸릴지 미리 계산하는 대신, **실제로 돌려보고** 측정합니다. 1주차의 "us급 배치 검사" 성능이 사실이라면, 이 O(T²)(T=후보 수) 성격의 사전계산도 충분히 빠를 것이라는 **가설**을 세운 것이고, 이 스텝이 그 가설을 검증합니다. 만약 실측 결과가 예상(2초 미만)을 벗어나면, 미리 준비해둔 "비상 계획"(1주차 Core에 새 C++ 함수 `pair_conflicts`를 추가해 이 반복문 전체를 C++로 내려서 훨씬 빠르게 만드는 것 — 아래 "계약 변경 요청" 절 참고)을 발동합니다. 성능이 확실하지 않은 부분에 대해 "일단 실측하고, 기준을 못 넘기면 미리 정해둔 대안으로 전환한다"는 이런 태도는 견고한 엔지니어링에서 매우 중요한 습관입니다 — 근거 없는 가정 위에 다음 단계를 쌓지 않습니다.

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

> 💡 **설명 — "폴리시(polish)"가 왜 마지막 순서인지, 그리고 "광내기"라는 이름의 의미:** 재타이밍(Task 2~3)이 "시간만" 건드리고, 배치풀 선택(Task 4~5)이 "블록별 조합 전체"를 건드렸다면, 폴리시는 가장 국소적이고 안전한 마지막 손질입니다 — **시간은 아예 손대지 않고**(entry/exit 완전 고정) bay 배정만 바꿉니다. 왜 이 순서(재타이밍→풀→폴리시)로 하는지 생각해보면: Z1(지연시간)이 목적값의 95%를 차지하는 지배적 항이므로, 먼저 Z1을 최대한 쥐어짜고(재타이밍, 풀선택도 Z1을 포함해 재최적화), 그 다음에 "이미 Z1은 확정됐으니 이제 Z2/Z3만 다치지 않게 조심스럽게 다듬자"는 게 폴리시의 역할입니다. "폴리시(polish, 광내기)"라는 이름 그대로, 이미 거의 완성된 해의 표면만 매끄럽게 다듬는 마지막 공정이라는 비유입니다.
>
> **왜 이동이 "구조적으로 ΔZ1==0"인가:** Z1(지연시간) 공식은 `Σ max(0, exit - due)`로, 오직 `exit`(그리고 그로부터 파생되는 `entry`, `due`)에만 의존하고 **어느 bay에 있었는지, 어느 좌표였는지는 전혀 상관하지 않습니다**. 그래서 "entry/exit을 그대로 두고 bay/x/y/orient만 바꾼다"는 이동 방식을 지키기만 하면, 코드로 별도 검증할 필요도 없이 수학적으로 Z1이 절대 변하지 않는다는 것이 **보장**됩니다("구조적으로"라는 표현이 그 뜻 — 우연이 아니라 설계 자체가 그렇게 만들어졌다는 것). 그럼에도 아래 구현에는 `assert z1n == z1_ref`라는 방어적 확인이 들어있는데, 이는 "이론적으로 당연한 것도 구현 버그로 깨질 수 있으니 실제로 검증한다"는 견고성 원칙의 반복입니다.
>
> **relocate와 swap의 차이**: "relocate(재배치)"는 블록 하나만 다른 bay의 빈 자리로 옮기는 것이고, "swap(맞교환)"은 두 블록이 서로의 bay를 맞바꾸는 것입니다. 어떤 블록을 선호 bay로 옮기고 싶은데 그 bay에 이미 자리가 꽉 차 있으면 단순 relocate로는 안 되고, 대신 "그 bay에 있는 블록 하나와 자리를 맞바꾸는" swap을 시도합니다 — 이게 relocate 다음에 swap을 시도하는 2단계 구조의 이유입니다.

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

> 💡 **설명:** `test_polish_moves_block_to_preferred_empty_bay`는 "선호 bay가 텅 비어 있는데 일부러 비선호 bay에 넣어둔" 가장 쉬운 케이스에서 폴리시가 확실히 옮겨주는지 확인하는 단위 테스트, `test_polish_never_worse_z1_invariant_on_full_instance`는 실제 100블록 인스턴스에서 "① Z1이 정말 한 치도 안 변하고(`z1a == z1b`, 부등호가 아니라 등호), ② 2차 목적(`w2*Z2+w3*Z3`)은 악화되지 않으며, ③ 모든 블록의 (블록id, entry, exit) 조합이 통째로 그대로"라는 것까지 확인하는 강한 통합 테스트입니다. 특히 세 번째 조건(`sorted((t[0], t[5], t[6]) ...)` 비교)은 "bay/x/y/orient는 바뀌어도 되지만 시간 관련 필드는 단 하루도 안 바뀌어야 한다"는 것을 명시적으로 검증합니다.

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

> 💡 **설명 — 이 알고리즘을 절차대로 따라가기:**
>
> **`_relocate`**: 블록을 일단 코어에서 `remove`(빼내고), 목표 bay의 **모든 방향(orientation)**에 대해 `free_positions`(1주차에서 배운, "지금 이 순간 이 블록이 놓일 수 있는 모든 좌표"를 비트보드로 빠르게 계산해주는 함수)를 물어봐서, 그 중 **원래 위치와 맨해튼 거리(`|Δx|+|Δy|`)가 가장 가까운** 좌표를 고릅니다. "왜 굳이 가장 가까운 곳을 고르는가": 임의로 아무데나 옮기면 다른 블록들과 새로운 충돌 가능성이 커지고 해가 "덜 안정적"으로 흔들리는데, 원래 자리 근처를 우선하면 이미 검증된 배치 구조를 최대한 보존하면서 bay만 바꾸는 보수적인 변경이 됩니다. `free_positions`에서 아무 자리도 못 찾으면(`best is None`) 원래 자리로 되돌리고 `None`을 반환합니다 — "이동 시도가 실패하면 반드시 원상복구한다"는 것이 이 함수의 계약입니다.
>
> **`polish` 메인 루프**: `while changed and not out_of_time()`로 "개선이 더 이상 없거나 시간이 다 될 때까지" 반복하는 전형적인 **first-improvement 지역 탐색(local search)** 구조입니다("first-improvement"란, 여러 개선 후보를 다 검토해서 그 중 최선을 고르는 게 아니라, **처음으로 발견한 개선이면 바로 채택하고 다음으로 넘어간다**는 뜻 — "best-improvement"보다 빠르지만 덜 철저합니다).
> - **순회 순서(`order`)**: `pref_loss`(선호도 손실)가 큰 블록부터, 동률이면 `workload`(작업량)가 큰 블록부터 먼저 시도합니다. 이건 "가장 손해를 많이 보고 있는 블록부터 고쳐준다"는 그리디 원칙이자, `workload`가 큰 블록을 먼저 옮기면 Z2(부하 불균형) 개선 효과도 더 크다는(지렛대 효과, "leverage") 실용적 타이브레이커입니다.
> - **relocate 패스**: 각 블록을 pref_loss가 작아지는(선호도가 더 좋아지는) 순서로 bay들을 시도해보고, 옮긴 뒤 2차 목적(`_secondary`)이 실제로 줄어들면(`< sec`, strict 부등호) 채택, 아니면 즉시 `_undo`로 원상복구합니다. `assert z1n == z1_ref`가 매 이동마다 실행되는 것은, "이 코드가 정말로 entry/exit을 안 건드렸는가"를 매번 실측으로 재확인하는 것 — 이론(설계)과 실제(구현)가 일치하는지 끊임없이 검증하는 방어적 프로그래밍의 예입니다.
> - **swap 패스**: relocate 혼자로는 옮길 자리가 없었던 블록들을 대상으로, "내가 가고 싶은 bay에 있는 블록과 자리를 맞바꿔보면 어떨까"를 시도합니다. `moved_a`(내 블록을 목표 bay로), `moved_b`(상대 블록을 내 원래 bay로)를 순서대로 시도해서 **둘 다** 성공해야 유효한 swap이고, 어느 한쪽이라도 실패하면 지금까지 옮긴 것도 다 되돌립니다(`_undo`) — "부분적으로만 성공한 상태"를 절대 남기지 않는 원자적(atomic) 연산 패턴입니다.
> - **`if changed: break`(swap 패스 안쪽)**: 한 번이라도 swap이 성사되면 그 순간 `cur_by_blk`(현재 배치 스냅샷)이 낡은(stale) 정보가 되므로, 안쪽 루프를 즉시 벗어나 바깥의 `while changed` 루프가 `cur_by_blk`를 다시 최신 상태로 만들고 처음부터 다시 시작하게 합니다 — "상태가 바뀌면 즉시 다시 스캔한다"는 지역 탐색의 안전한 관용구입니다.

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

> 💡 **설명 — 왜 병렬화가 필요하고, 왜 하필 4코어라는 제약이 이렇게까지 자주 언급되는가:** Global Constraints에 못박힌 "4코어/16GB"라는 대회 서버 스펙이 이 Task 전체를 지배합니다. 메타휴리스틱은 원래 "서로 다른 시드로 여러 번 독립적으로 돌려서 그 중 최선을 취하는" 멀티스타트 병렬화가 아주 잘 먹히는 분야입니다(각 실행이 서로 거의 독립적이라 병렬화 효율이 높음). 다만 코어가 4개뿐이라 "무한정 많은 워커"를 띄울 수 없고, CP-SAT 자체도 내부적으로 멀티스레드(`num_search_workers=4`)를 쓰므로, 이 모든 동시성 자원(ALNS 워커 + CP-SAT 스레드)을 합쳐도 4코어를 넘지 않도록 예산을 짜야 합니다. 그래서 이 Task는 "워커는 최대 2~3개"로 제한하고, 메인 프로세스는 계산을 거의 안 하고 그냥 큐에서 기다리기만 하는(`get(timeout=...)`) 구조로 설계되어 있습니다.
>
> **왜 `fork`인가 — 프로세스 생성 방식의 차이:** 파이썬 `multiprocessing`은 새 프로세스를 만드는 방법으로 크게 `fork`, `spawn`, `forkserver` 세 가지를 지원합니다.
> - **`fork`**(Linux/macOS 전용): 부모 프로세스의 메모리 상태를 그대로 복제해서 자식 프로세스를 만듭니다. 부모가 이미 import해둔 모듈, 만들어둔 객체, monkeypatch로 바꿔치기한 함수 등이 **전부 그대로 자식에게 복사**됩니다. 매우 빠르고(메모리 복사가 실제로는 copy-on-write라 거의 즉시), 별도 초기화 코드 없이 그대로 이어서 씁니다.
> - **`spawn`**(모든 OS 지원, Windows 기본): 완전히 새로운 파이썬 인터프리터를 처음부터 띄우고, 필요한 모듈을 처음부터 다시 import합니다. 느리지만 부모-자식 간 상태 오염이 없어 더 "깨끗"합니다.
>
> 이 프로젝트는 "대회 서버 = Linux 고정"이라는 조건이 이미 확정되어 있으므로 굳이 이식성을 걱정할 필요 없이 가장 빠른 `fork`를 씁니다(`ctx = mp.get_context("fork")`). 이 선택이 아래 테스트 설명에서 다시 중요해집니다(monkeypatch가 자식에 그대로 복제되는 이유).

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

> 💡 **설명 — 이 테스트 파일에서 특히 새로 배울 만한 것들:**
> - **`monkeypatch.setattr(shell, "improve", _stub_improve, raising=False)` — "스텁(stub)"으로 결정성 확보:** 2주차 ALNS(`improve`)는 아직 이 계획 시점에는 없을 수도 있고, 있어도 무작위성 때문에 테스트 결과가 매번 달라질 수 있습니다. 그래서 "예산만 그대로 소모하고 입력을 그대로 돌려주는" 아주 단순한 가짜 함수(`_stub_improve`)로 바꿔치기(monkeypatch)해서, "병렬화 배관 자체가 올바르게 동작하는가"(개선 로직의 정확성과는 별개로)만 결정적으로 검증합니다. `raising=False`는 "만약 `shell` 모듈에 `improve`라는 속성이 아직 없어도(2주차 미병합 상태) 에러 없이 새로 만들어 붙여라"는 옵션입니다.
> - **"fork 시작 방식이라 패치된 모듈 상태가 자식에 복제됨" — 왜 이 몽키패치가 워커 프로세스에서도 통하는가:** 위에서 설명했듯 `fork`는 부모의 메모리를 통째로 복사합니다. 테스트가 `monkeypatch.setattr`로 부모 프로세스 안의 `solver.shell.improve`를 이미 바꿔친 **다음에** `run_chains`를 호출하면, `run_chains`가 내부에서 `fork`로 띄우는 자식 워커들은 "이미 바꿔친 상태의 메모리"를 그대로 물려받습니다 — 그래서 워커 안에서도 `shell.improve`가 스텁으로 보입니다. 만약 `spawn` 방식이었다면 자식이 모듈을 처음부터 다시 import하므로 몽키패치가 전달되지 않아 이 테스트 전략 자체가 안 통했을 것입니다 — `fork`를 선택한 것이 병렬 성능뿐 아니라 **테스트 용이성**에도 영향을 준 설계 결정임을 보여주는 대목입니다.
> - **`mp.active_children()`로 "좀비 워커" 검사**: 병렬 프로그래밍에서 흔한 버그가 "부모는 끝났는데 자식 프로세스가 계속 남아있는(좀비/고아 프로세스)" 것입니다. 테스트가 `run_chains` 호출 전후로 살아있는 자식 프로세스 수를 비교해서, 함수가 끝난 뒤 워커들이 확실히 정리(join/terminate)되었는지 검증합니다.
> - **`test_real_improve_smoke_if_week2_present`**: `pytest.skip(...)`으로 "2주차 코드(`shell.improve`)가 아직 실제로 존재하지 않으면 이 테스트는 건너뛴다"는 조건부 테스트입니다. 이건 "3주차가 2주차보다 먼저 개발되고 있을 수도 있다"(주차별로 병렬 개발 가능성)는 현실을 반영한 유연한 테스트 설계입니다 — 있으면 실제 통합 스모크까지 확인하고, 없으면 조용히 넘어갑니다.

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

> 💡 **설명 — 이 파일을 함수별로, 그리고 병렬 프로그래밍 개념과 함께 읽기:**
>
> **`_improve_kwargs` — 인트로스펙션(introspection)으로 선택적 기능 자동 감지:** `inspect.signature(improve_fn).parameters`는 파이썬의 "리플렉션/인트로스펙션" 기능으로, 함수 객체를 실제로 호출하지 않고도 "이 함수가 어떤 매개변수 이름들을 받는가"를 코드로 조회할 수 있게 해줍니다. 여기서는 "2주차가 만든 `improve` 함수가 혹시 `rng`나 `seed`라는 키워드 인자를 지원한다면 그걸 활용해서 워커마다 다른 난수를 쓰게 하고, 지원하지 않으면 그냥 빈 딕셔너리(`{}`)를 반환해 기본 동작 그대로 호출한다"는 뜻입니다. 이건 계약 섹션에서 봤던 "계약 변경 요청 2번: `improve(..., rng=None)` — 없어도 동작"이 실제로 어떻게 구현되는지 보여주는 대목입니다 — **오리(duck) 타이핑**의 파이썬다운 확장판이라 할 수 있습니다("타입을 확인하는 대신, 필요한 기능(매개변수)을 갖고 있는지 직접 조사해서 있으면 쓴다").
>
> **`run_single_chain` — 병렬화 없는 기본 경로이자 최종 폴백:** 이 함수는 여러 프로세스를 쓰지 않고, 지금 프로세스 안에서 `improve`를 `slice_s`(기본 6초)짜리 조각으로 나눠 반복 호출하는 "단일 체인"입니다. 매 슬라이스가 끝날 때마다 결과를 `pool`에 적립하고 `best`를 갱신합니다 — 이게 바로 앞서 "전제" 절에서 설명한 "placement-pool-friendly 사용법"의 실제 구현입니다. `deadline - time.monotonic() > 0.5`라는 조건은 "남은 시간이 반 초보다 적으면 새 슬라이스를 시작하지 않는다"는 것으로, 슬라이스 하나가 어중간하게 시작했다가 시간이 부족해 어정쩡하게 끝나는 것을 막습니다.
>
> **`_worker` — 각 병렬 워커의 몸통:** 이 함수 자체가 새 프로세스에서 실행되는 코드입니다. 눈여겨볼 점들:
> - `prob = json.loads(prob_json)`, `core = make_core(prob)`: 워커가 **자기 자신만의 완전히 독립된 Core 객체**를 새로 만듭니다. 부모의 Core를 공유하지 않는 이유는, `fork`로 메모리는 복제되지만 이후 각 프로세스가 자신의 메모리를 독립적으로 변경하게 되므로(copy-on-write), 애초에 "각자 자기 상태를 갖고 서로 간섭하지 않는" 것이 병렬 탐색의 기본 전제이기 때문입니다.
> - `q_up.put_nowait(...)` / `pyqueue.Full` 예외 처리: `put_nowait`는 큐가 가득 찼으면 즉시 예외를 던지고 실패하는 "논블로킹(non-blocking)" put입니다. 워커가 "큐가 꽉 찼다고 진행을 멈추고 기다리는" 일이 없도록(어차피 진행상황 보고일 뿐이니 한 번 유실돼도 다음 슬라이스에서 다시 보고하면 됨), 실패하면 그냥 조용히 넘어갑니다(`pass`) — "진행 보고는 최선노력(best-effort)이면 충분하다"는 설계 판단입니다.
> - `q_down.get_nowait()`를 `while True`로 감싸 **큐가 빌 때까지 계속 비우는(drain)** 패턴: 다른 워커가 그동안 여러 번 "더 좋은 해를 찾았다"는 메시지를 보냈을 수 있는데, 그 중 가장 최신(혹은 가장 좋은) 것만 반영하면 되므로 큐에 쌓인 메시지를 전부 읽어 치우는 것입니다. `pyqueue.Empty` 예외가 나면(큐가 비었으면) 반복을 멈춥니다.
> - `finally: q_up.put(...)`: 워커가 정상 종료하든 예외로 죽든 **반드시** 마지막 상태를 부모에게 보고하도록 `try/finally`로 감쌌습니다 — 병렬 프로그래밍에서 "자식이 죽었는데 부모가 그걸 모른 채 영원히 기다리는" 교착상태(deadlock)를 막는 표준 패턴입니다.
>
> **`run_chains` — 워커를 띄우고, 메시지를 받아 병합하고, 정리하는 메인 오케스트레이터:**
> - `ctx = mp.get_context("fork")`: 위에서 설명한 fork 방식을 명시적으로 선택.
> - `q_up = ctx.Queue(maxsize=8*n_workers)`: 큐 크기를 유한하게 제한한 것은 "워커가 계속 보고만 하고 메인이 못 따라 읽으면 메모리가 무한정 쌓이는" 것을 막기 위함입니다.
> - `daemon=True`: 이 프로세스를 "데몬 프로세스"로 표시하면, 만약 부모 프로세스가 (버그로) 비정상 종료되더라도 자식이 고아로 계속 남아있지 않고 부모와 함께 강제 종료됩니다 — 좀비 프로세스 방지의 추가 안전장치.
> - 메인 루프(`while done < n_workers and time.monotonic() < hard_stop`): `q_up.get(timeout=1.0)`으로 최대 1초씩 기다리며 워커들의 보고를 받습니다. 더 좋은 해(`obj < best_obj`)가 보고되면, **그 워커를 제외한 나머지 워커들에게**(`if w2 != wid`) 새 best를 `q_down`으로 "방송(broadcast)"합니다 — 이게 "워커끼리 서로 최선의 결과를 공유"하는 협업 메커니즘입니다. `hard_stop = deadline + slice_s + 5.0`은 "예산이 끝나도 워커가 마지막 슬라이스를 마무리하고 보고할 시간(`slice_s`)과 약간의 통신 마진(5초)을 더 기다려준다"는 뜻 — 너무 칼같이 끊으면 워커들의 마지막 보고를 놓칠 수 있기 때문입니다.
> - **`finally` 블록의 이중 정리**: 먼저 `pr.join(timeout=3.0)`(정상 종료를 최대 3초 기다림)를 시도하고, 그래도 살아있으면(`pr.is_alive()`) `pr.terminate()`(강제 종료 신호)를 보낸 뒤 다시 `join`합니다 — "부드럽게 먼저 기다리고, 안 되면 강제로 끝낸다"는 프로세스 정리의 정석 패턴입니다. 이게 위 테스트의 "좀비 워커 없음" 검증이 통과하는 이유입니다.
> - **마지막 방어 코드(`if _score(...) == float("inf")`)**: 혹시 어떤 워커가 손상된(로드 불가능한) 해를 큐로 보냈더라도, 최종적으로 채택하기 전에 한 번 더 점수를 매겨 무한대(`float("inf")`, `_score` 함수에서 로드 실패 시 반환하는 값)라면 원래 `incumbent`로 안전하게 대체합니다 — 지금까지 배운 3주차 전체의 "결과가 의심스러우면 무조건 원래 해로 원복한다"는 견고성 철학이 병렬화 코드 끝자락에도 그대로 적용된 것입니다.

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

> 💡 **설명 — 이 Task가 3주차 전체를 "하나의 파이프라인"으로 꿰매는 자리입니다.** 지금까지 Task 2~7이 각각 독립적으로 잘 작동하는 부품(재타이밍, 풀, 폴리시, 병렬체인)을 만들었다면, Task 8은 그 부품들을 1·2주차가 이미 만들어둔 셸(`shell.solve`)의 정확한 위치에 끼워 넣는 "배선" 작업입니다. "병합 지점 주의" 문단이 강조하는 것은, 이 계획이 완전히 새 파일을 쓰는 게 아니라 **기존 파일의 특정 블록만 교체**하는 수술이라는 점입니다 — 앞부분(초기해 만들기)과 뒷부분(최종 안전 검증)은 절대 건드리지 않고, 중간의 "improve를 부르던 자리"만 "ALNS + 매트휴리스틱 파이프라인 전체"로 확장합니다.

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

> 💡 **설명 — 각 테스트가 지키려는 계약:**
> - `test_solve_two_arg_signature_still_works`: 대회 채점 서버가 실제로 부르는 형태(`solve(prob_info, timelimit)`, 인자 2개)가 3주차 확장 이후에도 여전히 그대로 동작해야 한다는 **하위 호환성(backward compatibility)** 테스트입니다. `submission/myalgorithm.py`는 1주차 이후 단 한 줄도 안 바뀌었는데, 그 코드가 계속 정상 동작하려면 `solve`의 새 매개변수(`cfg`, `stats_out`)가 전부 **기본값이 있는 선택적(optional) 키워드 인자**여야만 합니다.
> - `test_solve_runs_all_stages_and_logs`: 모든 스테이지를 켰을 때, ① 예산을 지키고, ② feasible하고, ③ 실행된 스테이지 순서가 정확히 `["retiming", "pool", "polish"]`이며, ④ 각 스테이지가 기록한 `obj_before`/`obj_after`가 실제로 악화되지 않았는지, ⑤ ALNS 단계가 최소한 블록 수만큼은 풀을 채웠는지를 한 번에 검증하는 종합 테스트입니다.
> - `test_solve_respects_disable_flags`: `enable_retiming=False, enable_pool=False`로 끄면 정말로 `mh_stages` 목록에 `"polish"` 하나만 남는지 — 즉 Config의 on/off 스위치가 실제로 파이프라인의 실행 여부를 좌우하는지 확인합니다. 이게 Task 9의 ablation(제거 실험)이 성립하기 위한 전제 조건입니다.
> - `test_mhcfg_from_env`: 환경변수 `OGC_MH_DISABLE`, `OGC_PARALLEL`을 세팅했을 때 `MhCfg.from_env()`가 올바르게 해석하는지 확인 — 대회 서버에 코드를 다시 빌드/제출하지 않고도 환경변수만으로 특정 스테이지를 끌 수 있게 하는 "운영 스위치"의 정확성 검증입니다.
> - `test_mh_stage_failure_keeps_incumbent`: **가장 중요한 견고성 테스트**입니다 — `shell.retime`을 아예 예외를 던지는 함수로 바꿔치기해도(`boom`), 전체 `solve()`는 여전히 feasible한 해를 반환해야 합니다. 이건 "매트휴리스틱 스테이지 하나가 무슨 이유로든(버그, 예상 못한 입력, CP-SAT 내부 오류 등) 완전히 고장 나더라도, 그 스테이지 하나만 건너뛰고 나머지는 정상 진행되어야 한다"는 원칙을 실제로 예외를 주입해서 검증하는 것 — 이게 "견고성 셸"이라는 이름값을 하는 대목입니다.

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

> 💡 **설명 — `MhCfg`부터 `solve` 확장까지, 예산 배분 계산을 실제 숫자로 따라가 보기:**
>
> **`MhCfg`의 두 층위 예산 비율:**
> 1. 바깥 층위(`mh_frac=0.20`): "improve(ALNS)+매트휴리스틱을 합친 전체 예산" 중 **20%**를 매트휴리스틱(retiming+pool+polish)에, 나머지 80%를 ALNS에 씁니다 — 이게 Global Constraints의 "L4 매트휴리스틱은 예산 후반 ~20%"라는 요구사항의 실제 숫자입니다.
> 2. 안쪽 층위(`retiming_frac=0.35, pool_frac=0.40, polish_frac=0.15`): 그 20%(매트휴리스틱 몫) **안에서** 다시 재타이밍에 35%, 풀선택에 40%, 폴리시에 15%를 배분합니다. 셋을 더하면 0.90인데, 나머지 0.10(10%)은 주석에 "잔여 0.10 = 마진"이라 적힌 것처럼 **의도적으로 남겨둔 안전 여유**입니다 — 각 스테이지가 미세하게 시간을 초과하거나, 스테이지 사이 전환 오버헤드가 있어도 전체 예산을 넘기지 않도록 하는 쿠션입니다.
> - 구체적 예: 전체 timelimit이 100초라 가정하면, `improve_total`(구성 등 앞단계를 뺀 나머지)이 대략 90초라 치면 `alns_s = 90*0.8 = 72초`(ALNS), `mh_s = 90*0.2 = 18초`(매트휴리스틱). 그 18초 안에서 재타이밍 `18*0.35≈6.3초`, 풀선택 `18*0.40≈7.2초`, 폴리시 `18*0.15≈2.7초`, 그리고 `18*0.10≈1.8초`는 안 쓰고 남깁니다.
>
> **`run_matheuristics`의 `run_stage` 클로저 — "스테이지 실행기"를 함수로 추상화:** `run_stage(name, enabled, frac, fn)`이라는 내부 함수 하나가 재타이밍·풀선택·폴리시 세 스테이지를 **똑같은 방식으로** 실행합니다(이게 앞서 "3주차 인터페이스 계약" 절에서 예고했던 "세 함수가 모두 같은 시그니처를 공유하기 때문에 가능한 것"입니다). `run_stage`가 하는 일을 순서대로 보면:
> 1. `enabled`가 꺼져 있거나, 할당된 시간(`tb`)이 `min_stage_s`(0.5초)보다 짧으면 **아예 시도하지 않고 조용히 반환**합니다 — 이것이 "스킵 가능(skippable)"의 구현입니다.
> 2. 시도하기 직전의 목적값(`obj0`)을 기록해두고, `try: out, st = fn(tb)`로 실제 스테이지 함수(`retime`/`select_from_pool`/`polish` 중 하나, `lambda`로 감싸서 전달됨)를 호출합니다.
> 3. **예외가 나면**(`except Exception as ex`): 그 스테이지의 실패를 `"status": f"ERROR:{type(ex).__name__}"`로 기록만 하고, `best`(현재까지의 최선해)는 절대 바꾸지 않은 채(`core`도 `best`로 다시 로드해 원상복구) 조용히 다음 스테이지로 넘어갑니다. 이게 바로 `test_mh_stage_failure_keeps_incumbent` 테스트가 검증하는 동작입니다.
> 4. 성공하면 `obj_before`/`obj_after`를 채워서 `stages` 리스트에 기록하고 `best`를 갱신합니다.
> - `nonlocal best`: 파이썬 클로저(closure) 문법으로, 안쪽 함수(`run_stage`)가 바깥 함수(`run_matheuristics`)의 지역 변수 `best`를 (새로 만드는 게 아니라) **직접 수정**할 수 있게 해주는 키워드입니다. `run_stage`를 세 번 호출하는 동안 `best`가 누적 갱신되어야 하므로 필요한 문법입니다.
> - **`pool`이 있어야만 "pool" 스테이지가 켜지는 조건** (`cfg.enable_pool and pool is not None and len(pool) > len(prob["blocks"])`): 풀에 "블록 수보다 많은" 튜플이 쌓여 있어야만(즉 최소한 어느 블록엔가는 대안 후보가 하나라도 더 있어야만) 풀 선택 모델을 시도할 가치가 있다는 뜻입니다 — 풀 크기가 딱 블록 수만큼(각 블록당 incumbent 튜플 1개씩)이면 대안이 전혀 없어 CP-SAT를 돌려봐야 원래 해와 똑같은 결과만 나올 게 뻔하므로 애초에 건너뜁니다.
>
> **`solve()` 확장부 — ALNS와 매트휴리스틱을 잇는 이음매:**
> - `pool = None` 초기화 후, `cfg.enable_parallel`이면 먼저 `parallel.run_chains`(병렬)를 `try`로 시도하고, **예외가 나면 `pool = None`으로 되돌려** 아래의 `if pool is None:` 분기가 자동으로 `run_single_chain`(단일 체인)을 시도하게 만드는 구조입니다 — "병렬을 우선 시도하되, 뭔가 잘못되면 조용히 단일 체인으로 강등"하는 정확히 계약대로의 폴백입니다.
> - `stats["_pool"] = pool`로 방금 만든 풀 객체를 `stats` 딕셔너리에 임시로 얹어두고, `run_matheuristics` 맨 앞의 `pool = stats.pop("_pool", None)`에서 그걸 다시 꺼내(`pop`이라 꺼내면서 동시에 `stats`에서 지움 — 최종 리포트에 이 무거운 객체가 남지 않도록) 풀 선택 스테이지에 전달합니다. `stats` 딕셔너리를 "임시 배달 상자"처럼 활용하는 살짝 트리키한 방식이지만, `solve`와 `run_matheuristics`라는 두 함수 사이에 새 매개변수를 추가하지 않고도 풀 객체를 넘기는 실용적인 선택입니다.
> - **`obj0`을 미리 재두는 이유**: `stats["alns"]`에 `"obj_before": obj0`(ALNS 시작 전 목적값)를 기록해, 나중에 Task 9의 벤치가 "ALNS 자체가 얼마나 개선했는지"를 계산할 수 있게 합니다.

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

> 💡 **설명 — "ablation(제거 실험)"이란 무엇이고 왜 하는가:** ablation(원래 의학 용어로 "절제, 제거"를 뜻함)은 머신러닝·최적화 연구에서 "시스템의 구성요소 하나씩을 일부러 제거해보면서, 그 요소가 전체 성능에 얼마나 기여했는지"를 측정하는 표준 실험 방법입니다. 이 프로젝트에서는 "재타이밍/풀선택/폴리시/병렬화 각각을 껐다 켰다 하면서 목적값이 얼마나 달라지는지" 재보는 것이 ablation입니다 — 예를 들어 "재타이밍만 켜고 나머지는 끈 경우"와 "다 켠 경우"의 목적값 차이를 보면 "풀선택+폴리시가 추가로 기여하는 몫"을 정량적으로 알 수 있습니다. 이건 단순히 "이 기능을 만들었다"에서 끝나지 않고 "이 기능이 실제로 얼마나 도움이 되는지 숫자로 증명한다"는, 근거 기반 엔지니어링의 핵심 습관입니다. 아래 코드의 `ABLATION_PRESETS`가 이 실험 설계를 그대로 코드화한 것입니다.

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

> 💡 **설명 — `ABLATION_PRESETS`를 하나씩 읽으면 실험 설계가 보입니다:**
> - `"alns_only"`: 3주차 스테이지를 **전부** 끄고 ALNS(2주차)만 돌린 결과 — 이게 "3주차가 없었다면 어땠을까"를 보여주는 **기준선(baseline)**입니다.
> - `"retime"`: 재타이밍만 추가로 켬 — "재타이밍 하나만의 순수 기여도".
> - `"retime_pool"`: 재타이밍+풀선택 — "폴리시를 뺐을 때".
> - `"full"`: 세 스테이지 다 켬(기본 설정) — "3주차 전체 기여도".
> - `"full_par2"`/`"full_par3"`: 다 켠 상태에서 병렬 체인(워커 2개/3개)까지 추가 — "병렬화 자체의 이득"을 `full`과 비교해서 봅니다.
>
> 이 6개를 순서대로 나열하면 "단계를 하나씩 켜나가며 그때마다 목적값이 얼마나 좋아지는지"를 한 눈에 볼 수 있는 **누적 ablation 실험**이 됩니다. `make_cfg`의 `kw.update(overrides or {})`는 "기본 CLI 설정 위에, 프리셋이 지정한 항목만 덮어쓴다"는 뜻으로, 파이썬 딕셔너리의 `update()`가 "있으면 덮어쓰고 없으면 그대로 둔다"는 성질을 활용한 것입니다.
>
> `print_contribution`의 `d[0] += (s["obj_before"] - s["obj_after"])`: 각 스테이지 실행 결과에 기록된 "실행 전/후 목적값" 차이를 계속 누적하고, 마지막에 실행 횟수(`n`)로 나눠 "이 스테이지가 평균적으로 목적값을 얼마나 깎아주는지"(mean Δ)를 출력합니다. `d = by.setdefault(s["stage"], [0.0, 0])`는 "그 스테이지 이름의 항목이 딕셔너리에 아직 없으면 `[0.0, 0]`으로 초기화하고, 있으면 기존 것을 그대로 가져온다"는 파이썬 관용구입니다.

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

> 💡 **설명 — 왜 "예산 활용률"이 별도의 완료 기준인가:** 견고성 셸의 여러 안전장치(각 스테이지 예산 부족 시 스킵, 예외 시 원복 등)가 지나치게 보수적으로 설계되면, "시간이 30분이나 남았는데도 다 못 쓰고 일찍 끝나버리는" 낭비가 생길 수 있습니다. `budget_utilization = runtime / timelimit`이 0.90 이상이어야 한다는 기준은 "주어진 시간을 최대한 알뜰하게 다 쓰고 있는가"를 감시하는 지표입니다 — 대회에서는 시간이 곧 해의 품질과 직결되므로(더 오래 탐색할수록 보통 더 좋은 해), 시간을 남기고 일찍 끝나는 것 자체가 "잠재적으로 더 잘할 수 있었는데 못한" 기회비용입니다.

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

> 💡 **설명 — "빔서치(beam search)"란, 그리고 왜 restart 다양화에 쓰는가:** 1주차의 `construct`(디코더)는 매 단계마다 "지금 당장 가장 좋아 보이는 선택 딱 하나"만 남기고 나머지는 버리는 순수 그리디(greedy) 알고리즘입니다. 빔서치는 이걸 살짝 완화해서, 매 단계마다 "가장 좋아 보이는 후보 상위 **B개**(beam width)"를 동시에 유지하며 진행합니다 — 그리디(B=1)와 완전 탐색(B=무한대, 사실상 불가능)의 중간 지점입니다. B개를 유지하면 "당장은 2등이지만 나중에 보면 1등이었던" 선택지를 그리디보다 더 잘 살릴 수 있어서, 최종적으로 그리디보다 나은 해를 찾을 가능성이 있습니다.
>
> 이 프로젝트에서 굳이 빔서치를 만드는 이유는 성능 개선 자체보다 **"다양화(diversification)"**에 있습니다 — 1주차 `construct`는 `rng`로 무작위성을 주더라도 결국 "같은 계열의 그리디" 안에서만 흔들리는데, 빔서치는 아예 "탐색 구조 자체가 다른" 대안적인 구성 방법이라서, 멀티스타트(여러 초기해를 만들어보는 것) 시 `construct`와 번갈아 쓰면 서로 다른 종류의 초기해를 얻어 ALNS가 더 다양한 출발점에서 탐색을 시작할 수 있게 해줍니다. `test_beam_differs_from_greedy` 테스트가 정확히 "빔서치 결과가 그리디 결과와 달라야 다양화 가치가 있다"는 것을 확인합니다.

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

> 💡 **설명 — 빔서치 알고리즘을 절차대로 이해하기, 그리고 "prefix replay"라는 트릭:**
>
> **핵심 자료구조 `beam`**: `[(placements, tard_sum), ...]` 형태의 리스트로, 지금까지 살아남은 최대 `beam_width`개의 "부분해 후보"를 담습니다. 매 블록(`for blk in order`)마다 이 `beam` 안의 **모든** 부분해를 각각 확장해서(각 부분해에서 다음 블록을 놓을 수 있는 `child_per_node`개의 자식을 만듦) 훨씬 많은 후보(`children`)를 만든 뒤, 그 중 상위 `beam_width`개만 남기고 나머지는 버리는 과정을 블록 수만큼 반복합니다 — 이게 "빔(beam, 서치라이트 빛줄기)"이라는 이름의 유래로, 매 단계마다 "일정 폭(beam_width)의 빛줄기"만 유지하며 앞으로 나아간다는 비유입니다.
>
> **"prefix replay" — 왜 매번 `core.clear()` 후 `load_placements`를 다시 하는가:** docstring에 적혀 있듯 "코어는 상태 복제가 없다"는 것이 핵심 제약입니다. 1주차 `Core` 객체는 "지금 배치된 상태" 단 하나만 들고 있고, 이를 손쉽게 복사(스냅샷)해서 여러 개의 독립된 상태를 동시에 유지하는 기능이 없습니다. 빔서치는 본질적으로 "여러 개의 서로 다른 부분해를 동시에 들고 다녀야" 하는데(beam 안의 각 원소), 코어가 상태 복제를 지원하지 않으므로 **매번 그 부분해를 처음부터 다시 "재생(replay)"** 해서 코어를 그 상태로 되돌립니다 — `core.clear()`(완전히 빈 상태로) → `core.load_placements(prefix)`(그 부분해에 있는 튜플들을 순서대로 다시 `place`) 하는 것이 "재생"의 구현입니다. docstring이 미리 계산해둔 성능 추정(`n=100·beam=8이면 총 ~40k place ≈ 수백 ms`)은, "이 재생 비용이 1주차에서 확보한 마이크로초급 `place` 성능 덕분에 감당할 만한 수준"이라는 것을 미리 검증한 것 — 근거 없이 그냥 짜본 게 아니라, 아키텍처 선택(상태 복제 없음)이 초래하는 비용을 사전에 계산해보고 "괜찮다"고 판단한 후 구현했다는 뜻입니다.
>
> **자식 생성 로직**: 1주차 `construct`(Task 12)의 "time-first" 방식과 매우 비슷하게, 먼저 `R`(release)부터 `max_delay`(기본 3일)까지의 좁은 시간 창에서 지각 0인 배치를 찾아보고, 못 찾으면 창을 벗어나 훨씬 넓은 범위(`R`부터 `horizon`까지)에서 "그나마 가장 이른" 배치를 찾는 2단계 구조입니다. 다만 1주차 `construct`는 매 블록마다 "최선 하나만" 남겼다면, 이 함수는 `cands[:child_per_node]`로 "상위 몇 개"를 자식으로 남겨서 빔의 다양성을 만듭니다.
> - `children.sort(key=lambda c: (c[1], ...))`: 사전식(lexicographic) 정렬로 "누적 지각시간이 적은 것 우선, 동률이면 좌표(y+x)가 작은 것(왼쪽아래 우선, BLF 원칙의 재활용) 우선"으로 자식들을 줄세웁니다.
> - **중복 제거(`seen` 집합)**: 서로 다른 부분해 부모에서 나온 자식이라도 "마지막에 놓인 블록의 (bay,x,y,orient,entry)"가 완전히 같다면 사실상 같은 지역을 탐색하는 셈이라 중복으로 간주해 하나만 남깁니다 — 빔의 `beam_width`개 슬롯을 "서로 다른" 후보로 채워서 탐색 다양성을 낭비하지 않으려는 최적화입니다.
> - 마지막에 `min(beam, key=lambda c: c[1])`로 살아남은 빔 중 누적 지각시간이 가장 낮은 것을 최종 해로 선택하고, 코어를 그 해로 재생시켜서 반환합니다.

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

> 💡 **설명 — 이 절이 하는 역할, 1주차 "계약 부록"과의 관계:** 1주차 주석본에서 본 "계약 부록 - 주차 간 조정 확정"이 **여러 주차의 요청을 사후에 모아 확정**한 결과물이었다면, 이 절은 그 부록에 반영된 3주차 쪽 요청의 **원본**입니다(1주차 부록 5번 항목 "`Core.pair_conflicts` — 3주차 배치풀 충돌 판정이 2.0s 초과 시"가 바로 여기 1번 요청을 가리킵니다). "조건부 사전 승인"이라는 체계가 흥미로운 이유: 이런 성능 최적화용 API는 "일단 필요할 것 같아서 미리 만들어두자"가 아니라, **실제로 측정해보고 기준을 못 넘겼을 때만 만든다**는 원칙을 지킵니다 — 이는 소프트웨어 공학에서 잘 알려진 "섣부른 최적화는 만악의 근원(premature optimization is the root of all evil)"이라는 격언과 같은 태도로, 불필요할 수도 있는 C++ 코드를 미리 짜서 유지보수 부담만 늘리는 것을 피합니다. 2번 항목은 "있으면 좋지만 없어도 지금 당장은 동작하는" 선택적 개선 요청이고, 3번은 새로운 요구가 아니라 "1주차 계약을 우리가 이렇게 해석해서 쓰고 있다"는 것을 명시적으로 확인해두는 문서화입니다.

## Self-Review 체크 결과

**1. 스펙 커버리지** (설계서 §4 L4·§7 3주차 행 대비):
- §4 L4-1 CP-SAT 재타이밍(기하 고정, interval, pairwise disjunction + 스윕 방향 조건, 수 초 풀이) → Task 2(인코딩+진리표 오라클), Task 3(모델·풀이·검증·비악화 가드). num_search_workers=4, budget time-box 포함.
- §4 L4-2 배치풀 호환성 선택(top-K 풀, 페어와이즈 충돌 사전계산, exactly-one + 충돌 배제, Gurobi 보류) → Task 4(풀), Task 5(충돌+모델+백엔드 절연 `_solve_cpsat`). Z2는 스케일 정수 선형화로 모델에 포함하고 근사 오차(floor 생략 ≤ w2·1, U 반올림 ≤1e-6)를 문서화, 최종 판정은 실측 objective — "선형화 문서화" 요구 충족.
- §4 L4-3 Z3/Z2 폴리시(Z1 무손실 bay 재할당·스왑) → Task 6 (entry/exit 고정으로 ΔZ1≡0 구조 보장 + assert).
- §4 L3 병렬화(체인 2~3 + 공유 배치풀·베스트 교환, 4코어 상한) → Task 7 + Task 9 프리셋 `full_par2/3`으로 speedup 실측, 이득 없으면 기본 off(단일 체인 폴백) 명시.
- §4 L4 "예산 후반 ~20%" + 단계 skippable → Task 8 (mh_frac=0.20, min_stage_s 스킵, 단계 예외 격리, 기존 최종 utils 검증·폴백 체인 유지).
- §7 3주차 완료 기준(레이어별 기여도 ablation, 30분 예산 활용률) → Task 9 Step 3·4 (측정 명령·합격 수치 명시).
- "여유 시 beam 구성기" → Task 10 (OPTIONAL 명시, 미착수여도 완료).
- 커버리지 갭: 없음. (Gurobi 이관·firejail 리허설·패키징은 스펙 로드맵상 4주차.)
**2. 플레이스홀더 스캔**: "TBD/TODO/나중에/적절히/Similar to Task N" 없음. 모든 코드 스텝에 실제 코드, 모든 Run 스텝에 명령+기대 출력 존재. 테스트 헬퍼(`_find_static_pair` 등)는 태스크 간 참조 없이 파일마다 반복 정의(규칙 준수). 확인.
**3. 타입/시그니처 일관성**: `retime/select_from_pool/polish`는 모두 `(Placements, dict)` 반환으로 통일, stats에 `stage/obj_before/obj_after/wall_s` 공통 키(Task 8 `run_stage`가 주입·소비). `PlacementPool.add(prob, blk, ...)` 인자 순서는 Task 4 정의 = Task 5/7 사용처와 일치. Placements 튜플 인덱스(5=entry, 6=exit) 사용처(재타이밍 `pl[6]`, 폴리시 `[5:7]`, 풀 `(blk,bay,x,y,o,e,_x)`) 일치. `Budget(timelimit, safety)`·`construct(core, prob, rng)`·`to_operations(placements)`는 1주차 계약 문면과 일치. `solve(prob_info, timelimit, cfg=None, stats_out=None)`는 기존 2-인자 호출(제출 경로) 하위호환 — test_shell_phases가 회귀 가드. 확인.

> 💡 **설명 — 1주차와 같은 형식의 "자체 검토" 절입니다.** 계획 작성자가 스스로 "설계서(스펙) 각 항목이 어느 Task에 대응하는지, 빠뜨린 요구사항은 없는지, 시그니처들이 서로 어긋나지 않는지"를 표로 대조한 마무리 점검입니다. 3번 항목이 특히 실전적인데, "여러 Task가 만든 함수들이 서로 호출·전달할 때 튜플의 몇 번째 자리가 무엇을 뜻하는지(`인덱스 5=entry, 6=exit`)"까지 명시적으로 재확인하고 있습니다 — 이런 "매직 넘버 인덱스"(`pl[6]`, `[5:7]` 등)는 여러 사람(혹은 여러 에이전트)이 나눠서 구현할 때 가장 흔하게 어긋나는 지점이라, 마지막에 한 번 더 전수 대조하는 습관이 버그를 미리 잡아줍니다.

---

## 부록: 이 문서에서 다룬 용어 색인

| 용어 | 처음 나온 곳 |
|---|---|
| 매트휴리스틱(matheuristics) — 메타휴리스틱+exact 솔버 결합 | 0.1 |
| L1~L5 5층 아키텍처에서 3주차(L4)의 위치 | 0.2 |
| interval 변수 / no-overlap 제약 / disjunction(선택적 순서 제약) | 0.4, Task 2 |
| CP-SAT `CpModel`/`NewIntVar`/`CpSolver`/`OnlyEnforceIf` 기초 어휘 | Task 1, Task 2 |
| `pair_bits_at_current()`의 실사용 (static/entry_ij/entry_ji) | 전제, Task 2~3 |
| Task-5 판정 표 → CP-SAT 인코딩 유도, δe/δx 등호 처리, 흡수(subsumption) | Task 2 |
| `AddHint` 웜스타트 / 진리표 전수 대조 테스트(`SearchForAllSolutions`) | Task 2, Task 3 |
| `retime()` — 기하 고정 Z1 전역 재최적화, `verify_full` 이중 안전장치 | Task 3 |
| PlacementPool — top-K 품질필터·중복제거·dump/merge(pickle 안전) | Task 4 |
| 배치풀 선택 모델 — `ExactlyOne`, 충돌쌍 배제, 독립집합 구조 | Task 5 |
| Z2 min-max 선형화(여유변수 D) / SCALE=10^6 / 근사-후-실측 검증 원칙 | Task 5 |
| 백엔드 절연(`_solve_cpsat`) — Gurobi drop-in 대비 | Task 5 |
| 폴리시 — ΔZ1≡0 구조적 보장, relocate/swap, first-improvement 지역탐색 | Task 6 |
| fork vs spawn / copy-on-write / daemon 프로세스 / 좀비 프로세스 방지 | Task 7 |
| `inspect.signature`를 통한 선택적 kwarg 자동 감지 | Task 7 |
| `multiprocessing.Queue` non-blocking put/get, best 방송(broadcast) | Task 7 |
| `MhCfg` 2단계 예산 배분(mh_frac → retiming/pool/polish_frac) | Task 8 |
| `run_stage` 클로저 — 스테이지 실행기 추상화, `nonlocal` | Task 8 |
| `OGC_MH_DISABLE`/`OGC_PARALLEL` 환경변수, `--disable`/`--parallel` CLI | Task 8, Task 9 |
| ablation(제거 실험) / 기여도 리포트 / 예산 활용률(budget_utilization) | Task 9 |
| 빔서치(beam search) / prefix replay(상태 재생) | Task 10 |
| 조건부 사전 승인 API (`pair_conflicts`) — 섣부른 최적화 회피 원칙 | 계약 변경 요청 |

