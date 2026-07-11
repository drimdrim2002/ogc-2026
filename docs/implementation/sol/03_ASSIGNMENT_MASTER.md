# 03. Exact Gurobi assignment portfolio

## 1. 단계 목표

validated safe incumbent를 먼저 확보한 상태에서만 Gurobi를 lazy import하여 exact assignment lower bound와 다양성 있는 assignment/start-time guide를 생성한다. 어떤 optimizer 실패도 pure-Python incumbent 또는 다음 단계의 optimizer-free constructor 진입을 막지 않는다.

## 2. 설계 근거

설계 §1, §4 P2, §6.1~6.2, §12 default/timebox/thread 정책, §13 구현 순서 3, §14.

## 3. P0~P6 대응

P2 전체. P0 budget/state와 P1 incumbent, P0 geometry fit/union area를 소비한다. P3 구현은 하지 않는다.

## 4. 선행 조건

1단계 safe incumbent와 2단계 exact fit/union metadata 완료. Gurobi는 optional이므로 availability는 선행 조건이 아니다.

## 5. 현재 저장소 상태

`ogc2026_env.yml`은 `gurobipy==13.0.2`를 선언하지만 production license/model size는 미확인이다. 현재 solver에는 optimizer adapter가 없고 기존 greedy는 globally coupled `Z2`를 incremental approximation으로 평가한다. 추적 training daily-40은 checkout에 없다.

## 6. 구현 범위

- orientation이 하나라도 exact integer fit인 `(i,j)`만 assignment variable로 허용.
- exact `Z2+Z3` lower-bound model.
- solution pool 또는 deterministic no-good loop로 8개 기본 portfolio, 10% gap, Hamming distance gate.
- best objective가 0일 때 percentage gap 대신 distinct/Hamming만 사용.
- `TL>~30s`에서 capped candidate-time congestion guide와 `rho={.60,.75,.90}`, slack penalty multiplier `{.5,1,2}`.
- Gurobi status/exception을 backend-neutral `AssignmentResult`로 변환.

## 7. 비범위

spatial feasibility 보장, complete placement, retiming, callbacks에서 Shapely, multiprocessing. master guide는 surrogate이며 incumbent를 직접 교체하지 않는다.

## 8. 변경 또는 추가할 파일

- 추가 `baseline/solver/assignment.py`.
- 변경 `baseline/solver/entry.py`(safe install 뒤 lazy phase 호출), `state.py`(seed value object가 필요할 때만).
- 추가 `baseline/tests/test_assignment.py`, `baseline/tests/test_assignment_integration.py`.

## 9. 파일별 책임

- `assignment.py`만 gurobipy를 import하며 import는 함수 내부 adapter factory에서 수행한다.
- `entry.py`는 result status를 기록하고 seed를 다음 phase에 넘기되, 직접 model 상태를 보유하지 않는다.
- tests는 fake backend로 model-independent 계약을 항상 검증하고, Gurobi integration은 availability/license별 expected fallback을 명시한다.

## 10. 인터페이스

```text
AssignmentSeed(bay_by_block, suggested_start, priority, source, surrogate_cost)
AssignmentPortfolio(seeds, lower_bound, status, runtime, gap, diagnostics)
build_exact_portfolio(instance,geometry,budget,config,backend_factory) -> AssignmentPortfolio
build_congestion_guides(...) -> AssignmentPortfolio
try_assignment_portfolio(...) -> AssignmentPortfolio.empty(status=...)
```

Model variables: feasible `x_ij`; `sum_j x_ij=1`; `Q_j=u_j*sum_i L_i x_ij`; `Qmax>=Q_j`; `Qmin<=Q_j`. objective는 `w2*(Qmax-Qmin)+w3*sum loss_ij*x_ij`. Guide `y_ijt`는 block별 정확히 1개, assignment/load와 link, soft area capacity와 nonnegative slack을 가진다.

## 11. checker 불변 조건

- seed의 bay는 exact integer fit이 존재해야 한다.
- internal `Z2/Z3` 계산은 checker와 `<=1e-6` relative error.
- guide start는 integer이고 `>=R`; dwell은 `max(P,1)`. 후보 time에는 on-time range와 선택 event/tardy cap을 포함한다.
- spatial feasibility는 주장하지 않는다. full checker 전 incumbent install 금지.

## 12. 예외·timeout·Gurobi fallback

- safe incumbent 설치 전 `assignment.py` 호출/import 금지.
- import/license/size/model/numeric/timeout에는 empty portfolio와 diagnostic을 반환한다. 예외가 `algorithm()` 밖으로 나가지 않는다.
- feasible incumbent solution이 있는 TIME_LIMIT은 seed로 사용할 수 있으나 optimal lower bound로 표기하지 않는다. incumbent가 없으면 empty.
- `Threads=4,OutputFlag=0,MIPFocus=1,SoftMemLimit=12,Seed=20260710`; 한 번에 model 하나. global budget에서 `min(1s,.03*remaining)`을 cap하고 시작 전 `can_start` 확인.

## 13. 세부 구현 순서

1. pure data로 feasible bay mask, preference loss, normalized load coefficient와 exact seed objective evaluator 작성.
2. fake backend용 model request schema/status mapping 테스트.
3. exact model 구축과 단일 solution extraction.
4. pool/no-good diversity: base seed 포함, Hamming `>=max(2,ceil(.03n))`, 최대 8(확장 config 32).
5. zero objective 처리와 deterministic sort `(assignment_cost,bay_tuple)`.
6. guide candidate times 생성/상한, soft capacity/slack penalty 구현.
7. entry integration: remaining budget ladder에 따라 호출하고 실패 시 empty seed로 constructor가 계속 가능하게 함.

## 14. 하위 단계 산출물

- 3A exact assignment 및 objective parity.
- 3B diverse portfolio.
- 3C congestion guide와 guarded orchestration.

3A는 enumeration, 3B는 Hamming/zero-objective, 3C는 guide/failure integration test가 각각 green일 때만 완료한다.

## 15. 단위 테스트 계획

| 케이스 | 입력 | 예상 |
|---|---|---|
| fit mask | 한 bay는 fractional-only, 다른 bay integer fit | variable은 후자만 |
| one assignment | 2 blocks/2 bays 손계산 | `sum x=1`, extracted bay tuple, exact Z2/Z3 일치 |
| zero objective | 모든 bay 동등 | gap division 없음, no-good로 distinct |
| diversity | n=100 후보 tuples | 모든 pair Hamming>=3, duplicate 0 |
| guide times | R=2,D=6,d=2 + events | 2..4 포함, event/tardy cap, 모두 integer |
| capacity slack | area overload synthetic | model infeasible 아님, positive slack/penalty |
| status mapping | OPTIMAL/TIME_LIMIT/INFEASIBLE/ERROR | incumbent 유무별 정확한 neutral result |
| config | remaining과 global cap | TimeLimit이 child budget 초과 안 함, Threads=4 |

## 16. 단계 통합 테스트

- `test_exact_master_matches_enumeration`: 4 blocks/2 bays synthetic의 모든 feasible assignment 전수열거와 model optimum/lower bound 일치.
- `test_seed_objective_matches_checker`: seed를 1단계 serial layout로 구체화해 full checker PASS, internal Z2/Z3/weighted assignment part parity.
- `test_import_and_license_failure_keep_safe_solution`: 각각 fake `ImportError`/license error. public entry 반환은 최초 fallback과 동일하며 Stage 5 PASS.
- `test_timeout_seed_is_only_guide`: TIME_LIMIT feasible seed가 incumbent store를 직접 바꾸지 않고 다음 phase input에만 나타남.
- Gurobi 실제 smoke는 tiny enumeration fixture만 사용하고 unavailable이면 fallback behavior를 PASS 기준으로 삼는다; 단 9단계 entitlement gate는 skip할 수 없다.

## 17. 테스트 실행 명령

```bash
cd baseline
/opt/homebrew/Caskroom/miniforge/base/envs/ogc-2026/bin/python -m unittest tests.test_assignment tests.test_assignment_integration -v
/opt/homebrew/Caskroom/miniforge/base/envs/ogc-2026/bin/python -m unittest discover -s tests -p 'test_*.py' -v
```

## 18. 테스트 입력·검증·예상 기록

model test에는 variable/constraint 수, status, SolCount, ObjVal/ObjBound/MIPGap, extracted tuple, Hamming 거리를 기록한다. timeout/license test에는 exact exception class와 fallback checker stage를 기록한다. guide는 spatial checker PASS를 기대하지 않고 serial realization 후 assignment component만 대조한다.

## 19. 완료 조건

- tiny exact model이 enumeration optimum과 일치.
- 모든 portfolio seed exact fit 및 pairwise diversity 충족.
- internal assignment objective checker parity.
- import/license/timeout/model error가 safe Stage-5 result를 손상하지 않음.
- constructor가 아직 없어도 3단계 public path가 fallback을 반환해 독립 완료 가능.

## 20. 다음 단계 제공 인터페이스

4단계는 0개 이상의 immutable `AssignmentSeed`를 받는다. empty portfolio면 자체 deterministic bay preference profile을 사용한다. Gurobi model/object를 노출하지 않는다.

## 21. 진행 문서 업데이트

Gurobi version/license 상태, exact enumeration 결과, seed 수/Hamming, failure matrix, objective parity를 기록한다. entitlement O-005는 tiny smoke로 닫지 않는다.

## 22. 위험과 대응

- assignment optimum의 geometry congestion: diversity와 guide, constructor가 권위.
- pool nondeterminism: seed 고정, canonical sort, 기본은 deterministic solve-inspect-add-cut loop 허용.
- surrogate infeasibility: mandatory slack.
- oversubscription: model sequential, Threads 4, multiprocessing 없음.
- lower bound 오표기: TIME_LIMIT bound/status를 명시하고 solution objective와 분리.

## 23. 미해결 사항

- restricted entitlement 최대 model 크기는 9단계 rehearsal 필요.
- solution pool과 repeated no-good 중 실제 13.0.2 환경에서 더 deterministic한 기본을 tiny/full data 측정 후 결정 기록.
- daily-40 부재로 8~32 portfolio의 full-scale cost는 아직 검증 불가.

## 24. 커밋 경계

권장 메시지 `feat(solver): add optional assignment master portfolio`. assignment 모듈, guarded entry 연결, 해당 tests만 포함한다. constructor 코드를 섞지 않는다.
