# 1단계 — ALNS 예산 활용과 상태 연속성

## 목표

60초에서 16회만 실행하고 조기 종료하는 구조를 제거하며, 60초 anchor와 장시간 extension 사이에서 ALNS 적응 상태를 보존한다.

## 근거

- 60초 run 중앙 elapsed는 약 23.46초다.
- 120개 중 118개가 `ITERATION_LIMIT`로 끝났다.
- 16회 제한은 warm-up 32, segment 50, stall 50보다 작다.
- 180초 경로는 anchor 뒤에 새 `run_lns()`를 호출해 RNG, weights, temperature, stall state가 다시 시작된다.

## 작업 범위

- `baseline/solver/runtime.py`
- `baseline/solver/entry.py`
- `baseline/solver/alns.py`
- `baseline/solver/budget.py`는 필요한 최소 범위
- ALNS deadline/state/anchor tests

repair 후보 의미론, retime 모델, constructor 정책은 변경하지 않는다.

## 검토할 해결안

1. 60초 iteration cap 제거 후 deadline-led 단일 LNS 실행
2. anchor에서 만든 `AlnsSearchState`를 extension에 전달
3. anchor/extension을 나누지 않고 root budget 안에서 하나의 연속 LNS로 실행하되 60초 incumbent를 checkpoint로 보존
4. warm-up/segment/stall을 고정 회수가 아니라 예상 iteration 처리량에 맞춰 phase별 조정

우선순위는 상태 연속성과 deadline 사용이다. 파라미터 숫자 튜닝을 여러 개 동시에 섞지 않는다.

## 진행 절차

1. 0단계 original/cumulative baseline과 hard-10 manifest를 확인한다.
2. fake clock 테스트로 60초 경로의 16회 제한, 남은 예산, reserve 동작을 재현한다.
3. ALNS 상태 객체가 필요한 최소 필드(RNG state, weights, temperature, destroy size, stall counters, pending retime)를 정의한다.
4. 60초에서 checker reserve 전까지 탐색하고 안전하게 종료하도록 변경한다.
5. 180초에서 anchor checkpoint를 보존하면서 extension이 같은 적응 상태를 이어받게 한다.
6. 같은 seed의 60초 trace가 180초 trace의 validated prefix 또는 checkpoint로 보존되는지 검증한다.
7. 단위/통합/전체 unittest를 실행한다.
8. hard-10에서 cumulative baseline과 candidate를 60초 paired 실행한다.

## 핵심 지표

- elapsed/budget utilization
- 총 LNS iterations와 invocation별 iterations
- warm-up 완료 비율
- operator weight update 및 stall growth 발생 수
- accepted/new-best와 time-to-first-improvement
- objective W/T/L, median relative delta, Borda

## 승격 조건

- 공통 hard blocker 전부 통과
- 60초 median budget utilization이 의도한 reserve를 제외하고 유의미하게 증가
- hard-10 전부에서 LNS가 조기 고정 cap이 아니라 deadline/reserve로 종료
- Win > Loss이고 median paired relative delta < 0
- longer-budget checkpoint가 60초 incumbent보다 나빠지지 않음

단순히 iteration 수만 늘고 objective가 개선되지 않으면 파라미터 변경을 승격하지 않는다.

## 롤백 조건

- deadline 침범 또는 outer timeout
- 60초 incumbent가 180초 경로에서 보존되지 않음
- 상태 재사용으로 결정성/trace invariant가 깨짐
- hard-10 objective가 체계적으로 악화

## 결과 기록

실행 시 선택한 상태 연속성 방식, 대안별 근거, 테스트, hard-10 60초 결과와 승격 결정을 추가한다.

### 2026-07-15 — 1단계 구현 및 검증 결과

#### 기준선과 범위

- cumulative baseline은 0단계 동결 commit `6466ce6c673616a70cb90bf875d8d45c8435d6fc`이다. original baseline `5f932a611e8076d01a3f59c1e4e44da350d522e6`와 동결 hard-10은 변경하지 않았다.
- hard-10 manifest / dataset SHA-256은 각각 `5499cf993f5018dceca0464dfcaf0c328e129cb7ed4946c79cc4e1860127d3b7` / `c093fb98b17c714ea5b9c8c829d4bce24dd378f0b558196ce0961feb68b34e1f`이다. membership은 `prob_38, 23, 40, 25, 27, 39, 21, 33, 28, 24` 순서를 그대로 사용했다.
- baseline/candidate의 benchmark config SHA-256은 모두 `b02608da6524b8651a3ffccdb78e7c6a985404b7740e7207c5337f3caa1a27d3`이다. benchmark 시 candidate는 commit 전 작업 트리였으며 변경 solver 3개 파일 결합 식별자는 `7a1e52a3ed1d5d176d43af915a6c0b7ea81d9d179e7ce1674cd5eb06534d097e`이다. 아래 승격 결정을 포함하는 이 commit을 이후 단계의 cumulative baseline으로 사용한다.
- 환경은 macOS 26.5.2 arm64, Python 3.12.13, Shapely 2.1.2, Gurobi 13.0.2이다.
- `baseline/utils.py` / `baseline/baseline_greedy.py` SHA-256은 `d45aaeafdce8bf80d59d097f655c43313a4951bed43b6628e3b1cf62d4876a94` / `8ec2cc816b35b6507a9407bc5f893140a9d1b5e0892af92a2dbac2f91b32103b`로 유지했다. constructor, heuristic repair 후보 의미론, MIP, interlock, retime 모델은 변경하지 않았다.

#### 선택한 구현

- 60초 `max_iterations=16` quota를 제거하고 기존 monotonic root/anchor budget의 checker reserve 직전까지 탐색하게 했다. constructor의 50,000 candidate quota는 그대로다.
- `AlnsSearchState`에 current snapshot, RNG state, operator weights, segment uses/scores, improving samples, temperature, destroy size, stall/invariant counters, pending retime, pending engine/densify 상태, 누적 iteration을 보존한다. `load_optional_phase()`가 anchor 결과 state를 extension `run_lns()`에 그대로 전달한다.
- 기존 32/50/50 cadence는 60초 hard instance에서 도달할 수 없으므로 production LNS에 warm-up/segment/stall `4/8/8`의 단일 반응형 cadence를 적용했다. acceptance, reward/weight 공식, destroy 성장, repair와 retime 의미론은 바꾸지 않았다.
- invocation telemetry에 `total_iterations`, `warmup_completed`, `weight_updates`, `stall_events`, `remaining_seconds`, `budget_utilization`을 추가했다.

#### 테스트

- 재현 테스트는 구현 전에 `lns_iteration_limit(60)==16`, state 부재, adaptation/remaining telemetry 부재로 1 failure + 3 errors를 확인했다.
- 전용 테스트: neighborhoods/ALNS/integration/anchor/telemetry `33/33 PASS`.
- 전체 테스트: `python -m unittest discover -s tests -p 'test_*.py' -v`, `187/187 PASS`.
- fake clock 60초 테스트는 iteration 16을 넘어 reserve 3초에서 `DEADLINE`으로 종료했다. split deterministic replay는 연속 6회와 3+3회 state의 current/RNG/weights/temperature/stall/segment 상태가 동일함을 검증했다.
- 180초 실제 probe(`prob_38`, seed `20260710`)는 anchor 18회, extension 10회, 누적 28회였다. `anchor.best_trace[-1] == extension.best_trace[0] == 2,040,406,878`이고 extension 최종은 `1,717,878,626`, Stage 5였다. raw / summary SHA-256은 `1b8e20275e67502df853bfa1552ff678027e2d79d5b53d9cb165b134fa916a9d` / `a052d47f6d75d226ba1a6bde393ca93573ab41c161ca5ccfb9e18577c7f59bbd`이다.

#### hard-10 60초 paired 결과

동일 seed `20260710`에서 인스턴스별 cumulative baseline 다음 candidate 순으로 교차 실행했다. W-L 차이가 6이고 median 판정이 명확하므로 플레이북 조건에 따라 추가 seed는 실행하지 않았다.

| Instance | Baseline | Candidate | Relative delta | Candidate iterations |
|---|---:|---:|---:|---:|
| `prob_38.json` | 1,944,925,896 | 2,040,406,878 | +4.9092% | 18 |
| `prob_23.json` | 86,035,570 | 60,451,644 | -29.7365% | 34 |
| `prob_40.json` | 71,343,866 | 75,514,855 | +5.8463% | 15 |
| `prob_25.json` | 8,498,613 | 7,052,811 | -17.0122% | 27 |
| `prob_27.json` | 906,386,120 | 878,441,661 | -3.0831% | 26 |
| `prob_39.json` | 997,984,831 | 769,215,781 | -22.9231% | 32 |
| `prob_21.json` | 130,219,204 | 80,961,880 | -37.8265% | 22 |
| `prob_33.json` | 466,911,198 | 380,628,537 | -18.4795% | 28 |
| `prob_28.json` | 442,634,312 | 215,235,130 | -51.3741% | 38 |
| `prob_24.json` | 112,545,918 | 34,177,951 | -69.6319% | 35 |

- objective: **8W/0T/2L**, median / p90 / worst relative delta `-20.7013%` / `+4.9092%` / `+5.8463%`; Borda baseline/candidate `2/8`.
- weighted component improvement(candidate가 좋을 때 양수) 평균: `w1*ΔZ1 +62,501,299.3`, `w2*ΔZ2 +106.3`, `w3*ΔZ3 +38,434.4`.
- median elapsed / budget utilization: baseline `35.541s / 59.23%`, candidate `57.140s / 95.23%`.
- iteration은 baseline `16/16/16`(min/median/max, 합 160)에서 candidate `15/27.5/38`(합 275)로 변했다. candidate 10/10은 고정 cap이 아닌 `DEADLINE`으로 종료했다.
- candidate activity: warm-up 완료 `10/10`, weight update `30`, stall event `5`, attempts/feasible/accepted/new-best `277/263/263/216`.
- candidate LNS phase 합계 repair/retime/checker `417.771/57.351/6.443s`; baseline은 `194.580/57.626/4.560s`이다.
- hard blocker: 20/20 Stage 5, exception/outer timeout/checker failure/trace regression `0`, objective parity maximum `0.0`.

Artifact directory는 `artifacts/ogc_sage/performance/phase1/phase1-paired-hard10-20260715`이다. baseline raw / candidate raw / paired summary SHA-256은 `0255944df9c4ca74e691a73a7d8e1b6eea27cf47060d7c9d4976e66997bc0c0b` / `f40eac2fb789151988154482e7e48dcda3482d04ff79ad2959afd08e2f0d454e` / `8772a9510f264436e64b3a5f9687e777a6996bbe78d17d8f60f2e77de980a78f`이다.

#### 승격 결정

**1단계 candidate 승격을 승인한다.** 모든 hard blocker, 예산 활용, deadline 종료, W>L, 음의 median delta, Borda, checkpoint 보존 조건을 통과했다. `prob_38`(+4.91%)와 `prob_40`(+5.85%)는 별도 회귀 위험이다. 원인은 단순 추가 iteration이 아니라 4회 warm-up 뒤 acceptance/weight trajectory가 기존 16회 prefix와 달라지는 데 있으며, 전체 평균으로 숨기지 않고 후속 최종 검증에서 감시해야 한다. 이번 단계에서는 두 회귀를 없애기 위한 operator/repair/retime 튜닝을 섞지 않는다. 2026-07-15 사용자 승인에 따라 본 1단계 구현과 결과 기록을 포함하는 이 commit을 새 cumulative baseline으로 승격한다.

## 실행 프롬프트

```text
OGC-SAGE 성능 개선 1단계 “ALNS 예산 활용과 상태 연속성”만 수행한다.

먼저 다음을 읽는다.
1. docs/implementation/sol/performance/README.md
2. docs/implementation/sol/performance/00_BASELINE_HARD10_AND_TELEMETRY.md
3. docs/implementation/sol/performance/01_ALNS_BUDGET_AND_STATE_CONTINUITY.md
4. docs/implementation/sol/06_HEURISTIC_LNS.md

0단계가 완료되고 HARD10_MANIFEST.json과 cumulative baseline이 존재하는지 확인한다. 없으면 구현하지 말고 차단 사유를 보고한다.

- 16회 제한 제거, deadline 사용, anchor/extension 상태 연속성만 다룬다.
- repair, constructor, retime 알고리즘은 변경하지 않는다.
- fake clock과 deterministic replay 테스트를 먼저 추가한다.
- 60초 reserve와 180초 checkpoint 보존을 검증한다.
- 전체 unittest 후 고정 hard-10에서 baseline/candidate 60초 paired benchmark를 실행한다.
- W/T/L, relative delta, budget utilization, iterations, warm-up/weight/stall activity를 보고한다.
- 승격 조건을 통과하지 못하면 default로 남기지 말고 롤백 가능한 상태와 원인을 기록한다.
- 결과를 이 단계 문서에 기록하고 다음 단계는 시작하지 않는다.
- 커밋/push는 별도 승인 시에만 수행한다.
```
