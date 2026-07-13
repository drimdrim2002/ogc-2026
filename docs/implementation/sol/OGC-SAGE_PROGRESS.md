# OGC-SAGE 구현 진행 현황

## 1. 문서 목적과 기준

이 문서는 OGC-SAGE 구현의 유일한 운영 대장이다. 구현자는 각 단계의 시작·검증·완료 때 이 문서를 갱신하고, 상세 절차는 같은 디렉터리의 단계별 실행 문서를 따른다.

- 단일 설계 기준: [`../../OGC2026_Competition_Algorithm_Design.md`](../../OGC2026_Competition_Algorithm_Design.md)
- 계획 작성 기준 커밋: `78ef82ece960ac686a6f5c41497a13c2217ffab5`
- worktree: `../sol-native-implementation`
- 기준 브랜치: `start-point`
- 작업 브랜치: `sol-native-implementation`
- 계획 문서 상태: 작성 완료
- 구현 상태: 9단계 진입 prerequisite remediation 완료. 공식 baseline v1.3 checker를 원본 그대로 설치하고 1~8단계 objective/serializer/model transaction을 재정합화했으며 O-001~O-005/O-007/O-008을 승인 정책으로 닫았다. 9단계 hardening, benchmark, archive rehearsal은 시작하지 않았다.
- 테스트 상태: official-checker 묶음 39/39, 영향 단계 suite 156/156, 전체 `baseline/tests` 156/156 PASS. 세 import 형태와 isolated clean-copy tester smoke도 Stage 5 PASS; daily-40 correctness/performance matrix와 600-run benchmark는 미실행이다.
- 다음 단계 진입: 9단계 hardening 진입 가능. prerequisite remediation 완료만 의미하며 9단계 완료 또는 release 가능을 의미하지 않는다. `LNS_ENABLED=False`, `INTERLOCK_ENABLED=False`를 유지한다.

설계와 저장소가 충돌할 때 구현자가 임의로 해석하지 않는다. 이 문서의 “결정 및 미해결 사항”에 기록하고 결정권자의 승인을 받은 뒤 관련 단계 문서를 함께 갱신한다.

## 2. 현재 저장소 기준선

- 공개 진입점 `baseline/myalgorithm.py`는 guarded import로 `solver.entry.solve`를 호출하고, 먼저 checker-validated safe incumbent를 만든다.
- `baseline/solver/`에 1단계 기반 모듈, 2단계 checker-parity geometry kernel, 3단계 optional assignment master, 4단계 event-aware exact constructor, 5단계 indicator 기반 exact four-state retimer, 6단계 pure-Python heuristic LNS, 7단계 optional bounded candidate-selection MIP repair, 8단계 gated one-way interlock densifier가 있고 `baseline/tests/`에 156개 `unittest` 계약·단위·통합 테스트가 있다. `baseline/` 자체의 `__init__.py`는 없으며 file-location/실행 디렉터리/package import smoke가 통과한 상태다.
- `baseline/utils.py`와 `alg_tester/utils.py`는 공식 baseline v1.3 archive 원본과 각각 byte-for-byte 동일하고 SHA-256 `d45aaeafdce8bf80d59d097f655c43313a4951bed43b6628e3b1cf62d4876a94`로 test-lock됐다. checker는 다시 변경 금지 대상이다.
- `baseline/baseline_greedy.py`는 비교 기준으로 동결되어 1~8단계 동안 변경되지 않았다. 공식 baseline v1.3 copy와도 hash가 다르지만(local `8ec2cc81...`, official `e9cd8e3e...`) 금지 파일이므로 임의로 맞추지 않는다.
- 환경 파일에는 Python 3.12, Shapely 2.1+, Gurobi 13.0.2가 있으나 별도 test dependency는 없다. 따라서 새 테스트는 표준 라이브러리 `unittest`를 사용한다.
- Git 추적 예제는 `alg_tester/example/example_B2_b10.json` 하나다. 로컬 gitignored 검증 데이터 40개는 공식 Training Set 1/2와 40/40 byte 일치한다. 공식 URL/archive hash와 각 instance hash만 tracked evidence manifest에 기록하며 training data는 source/submission/release archive에 재배포하지 않는다.
- `baseline/run_myalgorithm.py`는 submission 밖 local debug tool이며 instance positional argument가 필수이고, tracked example 도움말과 `time.monotonic()`을 사용한다.
- 공식 권위 artifact: [Problem Statement v1.2](https://optichallenge.com/assets/problem-statement-latest-DBm0ZUoF.pdf) SHA-256 `1f7fb17e31fad327259ec967cf143dc5b2baada33b759ab4f926c732659e1e2c`; [baseline v1.3](https://optichallenge.com/assets/baseline-latest-oxXm57Lz.zip) `c37248b9e7f1b8597450d5346a5b25538a7f16f390697b00474cdbd0921bc6c3`; [Training Set 1](https://optichallenge.com/assets/training_instances_20260531-CWCx_z9X.zip) `b9a5790f3ef04edfeec1631531a65f56687e166e11fc10141a74c5f893790aee`; [Training Set 2](https://optichallenge.com/assets/train-set2-UXyrUSG6.zip) `dbe2245bb18b2a5df8bff33d24e65ce137fd21e7cc5579921c11c293d8994bf3`; [FAQ](https://optichallenge.com/faq).

## 3. 구현 순서와 P0~P6 매핑

| 단계 | 상태 | 설계 phase | 구현 범위 요약 | 다음 단계 진입 |
|---|---|---|---|---|
| 1 | 완료 | P0 최소 기반 + P1 | immutable parsing/AABB·integer anchor, checker 계약, serializer, budget, fallback, validated incumbent, exception armor | 23/23 green, 2단계 진입 가능 |
| 2 | 완료 | P0 완성 | Shapely ShapeInfo, repaired layers, suffix union, exact obstruction, four-state, bounded cache | 13/13 전용·36/36 전체 green, 3단계 진입 가능 |
| 3 | 완료 | P2 | optional Gurobi assignment lower bound와 diverse portfolio, congestion guide | 15/15 전용·51/51 전체 green, 4단계 진입 가능 |
| 4 | 완료 | P3 | event-aware union-safe regret constructor와 empty-bay fallback | 15/15 전용·66/66 전체 green, 5단계 진입 가능 |
| 5 | 완료 | P4 | indicator 기반 exact four-state retimer와 affected component | 보완 전용 29/29·전체 95/95, R5-001~R5-005와 real `prob_4` witness green |
| 6 | 완료 | P5 일부 | 6개 heuristic destroy, regret-2/3 repair, current/best/candidate, SA/adaptation/stall/metrics | 전용 23/23·전체 118/118; submission 활성화 gate는 계속 off |
| 7 | 완료 | P5 완성 | bounded candidate-selection Gurobi repair와 cut loop | 전용 20/20·전체 138/138; failure/transaction/Stage 5 green |
| 8 | 완료 | P6 | gate된 one-way interlock densifier와 final retiming | 전용 15/15·전체 153/153; gate/transaction/witness/rollback green |
| 9 | 진입 가능(미완료) | P0~P6 운영 | prerequisite checker/contract remediation 완료; packaging, exception/deadline/stress hardening, daily-40 validation gates는 미실행 | 9단계 hardening 진입 가능; benchmark/archive rehearsal 전이며 릴리스 불가 |

P0은 1~2단계와 9단계, P1은 1·9단계, P2는 3·9단계, P3는 4·9단계, P4는 5·9단계, P5는 6·7·9단계, P6는 8·9단계에서 추적한다. 모든 P0~P6 요소가 최소 한 단계에 배정되어 있다.

## 4. 전체 의존성 순서

`1 foundation → 2 geometry → 3 assignment → 4 constructor → 5 retimer → 6 heuristic LNS → 7 MIP repair → 8 interlock → 9 packaging/stress`

확정된 경계는 다음과 같다.

- 1단계 serializer는 `ExitPrecedenceProvider` 프로토콜만 소비한다. 1단계 fallback은 bay별 직렬이라 빈 선행 그래프를 사용하고, 테스트는 fake provider로 topology를 검증한다. 실제 geometry provider는 2단계가 제공한다.
- 3단계는 1단계 `Instance`, `Budget`, `SolutionSnapshot`과 2단계 fit/area 정보만 사용한다.
- 4단계는 3단계 seed가 없어도 자체 deterministic profile로 실행 가능하다. master 실패가 constructor 완료를 막지 않는다.
- 5단계 입력은 완전한 checker-feasible snapshot이고 retimer 실패는 입력 또는 이미 검증된 constructor incumbent를 보존한다. `entry`는 constructor를 canonical serialize/full-check/strict install한 뒤 설치된 `store.snapshot`만 retime하며, retimer의 canonical full-check 증거를 중복 checker 호출 없이 strict install한다.
- 6단계는 heuristic repair만으로 완결된다. 7단계 MIP repair는 선택적 추가 엔진이다.
- 8단계는 2단계 relation kernel과 5단계 retimer를 재사용하며, 비활성 상태가 기본이다.
- 9단계는 앞 단계 기능을 바꾸지 않고 packaging·validation·gate를 고정한다.

## 5. 단계별 실행 문서

1. [`01_FOUNDATION_SAFE_INCUMBENT.md`](01_FOUNDATION_SAFE_INCUMBENT.md)
2. [`02_GEOMETRY_FOUR_STATE.md`](02_GEOMETRY_FOUR_STATE.md)
3. [`03_ASSIGNMENT_MASTER.md`](03_ASSIGNMENT_MASTER.md)
4. [`04_EXACT_CONSTRUCTOR.md`](04_EXACT_CONSTRUCTOR.md)
5. [`05_EXACT_RETIMING.md`](05_EXACT_RETIMING.md)
6. [`06_HEURISTIC_LNS.md`](06_HEURISTIC_LNS.md)
7. [`07_CANDIDATE_SELECTION_MIP.md`](07_CANDIDATE_SELECTION_MIP.md)
8. [`08_INTERLOCK_DENSIFIER.md`](08_INTERLOCK_DENSIFIER.md)
9. [`09_PACKAGING_STRESS_HARDENING.md`](09_PACKAGING_STRESS_HARDENING.md)

## 6. 단계별 검증 요약

| 단계 | 단위 테스트 초점 | 단계 통합 테스트 초점 | 완료 증거 |
|---|---|---|---|
| 1 | parsing, anchors, budget, state, serializer 8대 계약 | public entry가 optional import 전 Stage 5 incumbent 생성·보존 | 명령, 건수, PASS, checker 결과 |
| 2 | suffix union, FREE/I_OUTER/K_OUTER/SEPARATE, cache | 무작위 2-block relation과 checker parity | seed·sample 수·불일치 0 |
| 3 | fit mask, objective, pool diversity, exception mapping | Gurobi 성공/timeout/license 없음 모두 feasible incumbent | model status·fallback 결과 |
| 4 | event/anchor/regret/delta/empty-window | profile별 complete union-safe candidate가 full checker PASS | profile·시간·stage·objective |
| 5 | horizon, indicators, nesting, same-exit order | retime 결과 checker PASS, Z1 비증가, failure rollback | bound/gap/time/delta |
| 6 | destroy, regret repair, SA/RRT, weights, transaction | seeded loop의 best trace 비증가 및 O(1) return | operator counters·trace |
| 7 | caps, cuts, rank, MIP start, rollback | MIP/timeout/license 없음 모두 heuristic path 보존 | model stats·checker result |
| 8 | activation gate, offset search, one-way candidates | gate-off 동일성, witness 개선, regression 0 | gate reason·dense subset delta |
| 9 | imports, archive content, deadlines, exception matrix | daily-40×budgets×seeds correctness/performance gates | 결과 artifact 경로·요약 |

모든 objective와 internal delta는 checker의 `objective`, `obj1`, `obj2`, `obj3`와 상대 오차 `<=1e-6`이어야 한다. `isclose(a,b,rel_tol=1e-6,abs_tol=1e-9)`을 사용한다.

## 7. 단계 완료 및 갱신 절차

각 단계 종료 때 다음을 한 트랜잭션처럼 갱신한다.

1. 단계 행의 상태를 `진행 중`에서 `완료`로 바꾸되 단위·통합 테스트가 모두 green인 경우에만 수행한다.
2. 아래 테스트 증거에 날짜, 환경, 정확한 명령, 종료 코드, test 수, checker stage/objective, 실패 이력을 기록한다.
3. 결정 사항, 변경 파일, 커밋 해시를 기록한다.
4. validation gate 표에서 새로 충족된 항목만 증거와 함께 갱신한다.
5. 다음 단계 선행 조건을 대조해 `진입 가능` 또는 구체적 차단 사유를 기록한다.
6. 실패 시 완료 처리하지 말고 재현 명령, 실제 결과, 원인, 수정, 재검증 결과를 같은 기록에 남긴다.

## 8. 테스트 증거 기록

| 날짜/단계 | 환경·seed | 실행 명령 | 결과/건수 | checker·objective | 실패→수정→재검증 | 증거 경로 |
|---|---|---|---|---|---|---|
| 2026-07-12 / 1 | Python 3.12.11, Shapely 2.1.2, deterministic fixtures | `cd baseline && /opt/homebrew/Caskroom/miniforge/base/envs/ogc-2026/bin/python -m unittest tests.test_checker_contract tests.test_foundation -v` | 종료 0, 23/23 PASS | 추적 예제 Stage 5; `(121.0, 183.68374331550805, 0.0, 323992.78620320855)`; 내부 대비 상대 오차 전 항목 0 | 최초 red는 solver 미구현으로 2개 import error. 구현 후 2개 fixture 불일치(reference anchor, equal objective)를 계약에 맞게 수정하고 23/23 재검증 | `baseline/tests/test_checker_contract.py`, `baseline/tests/test_foundation.py` |
| 2026-07-12 / 1 | 동일 환경 | `cd baseline && /opt/homebrew/Caskroom/miniforge/base/envs/ogc-2026/bin/python -m unittest discover -s tests -p 'test_*.py' -v` | 종료 0, 전체 23/23 PASS | half-open/boundary/FREE entry/exit DAG/chronological reconstruction/P=0 모두 예상 stage 일치 | 실패 없음 | `baseline/tests/` |
| 2026-07-12 / 1 | 동일 환경, tracked example | `baseline/` 실행 위치 및 저장소 root package import smoke | 양쪽 종료 0 | 양쪽 모두 Stage 5, objective `323992.78620320855` | 실패 없음; O-003 양쪽 import smoke 충족, 최종 tester 계약은 9단계에서 계속 추적 | `alg_tester/example/example_B2_b10.json` |
| 2026-07-12 / 2 | Python 3.12.11, Shapely 2.1.2, seed `20260710`, 실제 fitting pair/time 2,000건 | `cd baseline && /opt/homebrew/Caskroom/miniforge/base/envs/ogc-2026/bin/python -m unittest tests.test_geometry tests.test_four_state_parity -v` | 종료 0, 13/13 PASS; directional·schedule parity 불일치 0 | direct FREE/I_OUTER/K_OUTER/SEPARATE valid fixture 모두 Stage 5; invalid fixture는 serializer 거부 또는 checker Stage 2~5; valid synthetic objective `0.0` | 최초 red는 `solver.geometry` 미구현으로 2개 import error. 구현 후 4개 fixture가 first-layer first-vertex 기준점을 잘못 적용해 실패했고 fixture를 1단계 계약에 맞게 수정한 뒤 13/13 재검증 | `baseline/solver/geometry.py`, `baseline/tests/{test_geometry,test_four_state_parity}.py` |
| 2026-07-12 / 2 | 동일 환경·seed | `cd baseline && /opt/homebrew/Caskroom/miniforge/base/envs/ogc-2026/bin/python -m unittest discover -s tests -p 'test_*.py' -v` | 종료 0, 전체 36/36 PASS | 1단계 objective parity 회귀 포함; actual geometry provider의 same-exit blocker-first 결과 Stage 5 | 실패 없음 | `baseline/tests/` |
| 2026-07-12 / 2 | 동일 환경, tracked example | `cd baseline && /opt/homebrew/Caskroom/miniforge/base/envs/ogc-2026/bin/python -c "import json; from pathlib import Path; from myalgorithm import algorithm; from utils import check_feasibility; raw=json.loads(Path('../alg_tester/example/example_B2_b10.json').read_text()); result=check_feasibility(raw, algorithm(raw, 1.0)); print(result)"` | 종료 0 | Stage 5; `(121.0, 183.68374331550805, 0.0, 323992.78620320855)` | 실패 없음; 1단계 validated incumbent와 objective 불변 | `alg_tester/example/example_B2_b10.json` |
| 2026-07-12 / 3 | Python 3.12.11, Shapely 2.1.2, Gurobi 13.0.2 restricted non-production(2027-11-29 만료), seed `20260710` | `cd baseline && /opt/homebrew/Caskroom/miniforge/base/envs/ogc-2026/bin/python -m unittest tests.test_assignment tests.test_assignment_integration -v` | 종료 0, 15/15 PASS; fit mask, request schema, zero objective, n=100 Hamming 3, status/config/timeout, guide slack 및 failure integration | 4-block/2-bay 전수열거 optimum `10.125` = Gurobi `ObjVal/ObjBound 10.125`, gap 0; serial realization Stage 5, `(Z2,Z3,weighted)=(3.375,0,10.125)` checker 상대 오차 0; synthetic overload positive slack `140.0` | 최초 구현 후 14/14 green; on-time 전체 범위를 cap이 제거하지 않는 계약을 보강해 최종 15/15 재검증 | `baseline/solver/assignment.py`, `baseline/tests/{test_assignment,test_assignment_integration}.py` |
| 2026-07-12 / 3 | 동일 환경 | `cd baseline && /opt/homebrew/Caskroom/miniforge/base/envs/ogc-2026/bin/python -m unittest discover -s tests -p 'test_*.py' -v` | 종료 0, 전체 51/51 PASS | 1~2단계 checker/objective/four-state 회귀 포함; import/license/model status/timeout에서 safe Stage 5 incumbent 불변 | 실패 없음 | `baseline/tests/` |
| 2026-07-12 / 3 | 동일 환경, tracked example | exact portfolio probe와 `algorithm(raw,5.0)` 양쪽 import smoke | exact `OPTIMAL`, variables 22, constraints 18, `ObjVal=1022.6988257889586`, `ObjBound=1022.698825788958`, gap 0, seed 3, 최소 Hamming 2; public entry 종료 0 | public 반환 Stage 5; `(121.0,183.68374331550805,0.0,323992.78620320855)`로 최초 fallback과 동일; safe 이전 `assignment/gurobipy` module load 모두 false | 첫 root smoke에서 top-level `solver.entry`를 잘못 사용해 `ModuleNotFoundError`; O-003 계약에 맞게 root는 `baseline.solver.entry`, `baseline/`은 `solver.entry`로 수정 후 양쪽 PASS | `alg_tester/example/example_B2_b10.json` |
| 2026-07-12 / 4 | Python 3.12.11, Shapely 2.1.2, seed `20260710`; default caps time 12/32, anchor 48, lattice 512 | `cd baseline && /opt/homebrew/Caskroom/miniforge/base/envs/ogc-2026/bin/python -m unittest tests.test_construct tests.test_construct_integration -v` | 종료 0, 15/15 PASS; event/contact/negative anchor/symmetric validation/delta/regret/transaction 및 5개 통합 gate | tracked example에서 master 없음과 AssignmentSeed 제공 6 profile 모두 complete Stage 5; overlapping same-bay pair 전부 exact `FREE`; 강제 cap 0은 fallback 10/10, Stage 5 | 최초 신규 suite는 fixture의 2-bay preference 길이 누락으로 13 PASS/2 ERROR; fixture 수정 후 15/15 재검증 | `baseline/solver/construct.py`, `baseline/tests/{test_construct,test_construct_integration}.py` |
| 2026-07-12 / 4 | 동일 환경·seed | `cd baseline && /opt/homebrew/Caskroom/miniforge/base/envs/ogc-2026/bin/python -m unittest discover -s tests -p 'test_*.py' -v` | 종료 0, 전체 66/66 PASS | 1~3단계 checker/objective/four-state/Gurobi fallback 회귀 포함; invalid constructor candidate full-check 거부 시 initial safe best byte identity 보존 | 최종 재검증 명령을 저장소 root에서 잘못 1회 실행해 `tests` import error 2건; 지정된 `baseline/` cwd에서 재실행해 66/66 PASS | `baseline/tests/` |
| 2026-07-12 / 4 | 동일 환경, tracked example, exact assignment `OPTIMAL` seed 3 | 6 profile constructor probe, public `algorithm(raw,5.0)` baseline/root 양쪽 import smoke, forbidden-file/diff audit | 6/6 complete, profile별 fallback 0, 참고 construction time `0.0304~0.0379s`; 양쪽 public smoke 종료 0; `git diff --check`, `cmp baseline/utils.py alg_tester/utils.py`, 금지 파일 diff 모두 통과 | 각 profile 및 public 반환 Stage 5; `(Z1,Z2,Z3,total)=(0.0,14.671260826994029,46.0,1022.6988257889582)`, 내부 대비 오차 0; 최초 fallback `323992.78620320855`보다 strict improvement로 full-check install | 실패 없음; daily-40 activation benchmark는 9단계 데이터 차단에 따라 미실행 | `alg_tester/example/example_B2_b10.json` |
| 2026-07-12 / 5 | Python 3.12.11, Shapely 2.1.2, Gurobi 13.0.2 restricted non-production(2027-11-29 만료), seed `20260710`; `max_free=80`, `Threads=4` | `cd baseline && /opt/homebrew/Caskroom/miniforge/base/envs/ogc-2026/bin/python -m unittest tests.test_retime tests.test_retime_integration -v` | 종료 0, 전용 16/16 PASS; four-state 784 date/relation truth-table cases, 81-node component free 80/boundary 1, constructor hook·exception armor 포함 | 2-block SEPARATE: `Z1 5→2`, `OPTIMAL`, bound `2`, gap `0`, secondary `0`, 계측 runtime `0.013720s`; synthetic I_OUTER: `Z1 4→0`, equal exit `(0,5)/(1,5)`, blocker-first `[1,0]`, Stage 5, bound/gap `0/0`, runtime `0.001352s`; internal/checker 상대 오차 0 | 최초 전용 실행은 truth-table 예상 case 수를 `900`으로 잘못 기록해 7 PASS/1 FAIL; 실제 `4×14²=784`로 수정. 완료 감사에서 primary/secondary 동률 alternate dates 노출을 발견해 strict lexicographic improvement가 없으면 input identity를 유지하도록 수정한 뒤 16/16 재검증 | `baseline/solver/retime.py`, `baseline/tests/{test_retime,test_retime_integration}.py` |
| 2026-07-12 / 5 | 동일 환경·seed | `cd baseline && /opt/homebrew/Caskroom/miniforge/base/envs/ogc-2026/bin/python -m unittest discover -s tests -p 'test_*.py' -v` | 종료 0, 전체 82/82 PASS | 1~4단계 회귀 포함; tracked constructor `Z1=0`, secondary `0`은 `NO_IMPROVEMENT`로 input identity 보존; timeout/no-solution, license/factory error, unexpected public-hook exception 모두 Stage 5 incumbent 보존 | 수정 후 전용 16/16과 전체 82/82를 연속 재실행해 PASS | `baseline/tests/` |
| 2026-07-12 / 5 | 동일 환경, tracked example | public `algorithm(raw,5.0)` baseline/root 양쪽 import smoke, `compileall`, forbidden-file/time API/diff audit | 양쪽 smoke 종료 0; `git diff --check`, `compileall`, checker copy `cmp`, 금지 파일 diff, `rg 'time\.time\('` 모두 통과 | 양쪽 Stage 5; `(Z1,Z2,Z3,total)=(0.0,14.671260826994029,46.0,1022.6988257889582)`, 내부/checker 오차 0 | 최초 `compileall`을 root에서 `solver tests`로 호출해 경로 경고가 있었고, 지정 `baseline/` cwd에서 재실행해 통과 | `alg_tester/example/example_B2_b10.json` |
| 2026-07-12 / 5 완료 후 감사 | Python 3.12.11, Gurobi 13.0.2, deterministic fake clock/backend | 전용 16/16 및 전체 82/82 재실행 후 orchestration order, forced retimer exception, 0.1s preprocessing budget, 160 singleton request, negative-due Stage 5 진단 | 기존 tests는 모두 PASS했으나 `checker→retime→checker`; retimer crash objective `323992.78620320855` vs 정상 `1022.6988257889582`; 0.1s budget에서 relation 4,950회·fake clock 49.5s; combined request free/modelled 160; negative due 입력 Stage 5이나 retime `INFEASIBLE` | 테스트가 포착하지 못한 R5-001~R5-005를 확인해 5단계를 `보완 필요`로 재개방. 기존 PASS 증거는 삭제하지 않고 보완 regression의 기준선으로 유지 | `baseline/solver/{entry,retime}.py`, `baseline/tests/{test_retime,test_retime_integration}.py` |
| 2026-07-12 / local daily-40 | gitignored local data | `data/**/*.json` 파일 수·문제 번호·JSON/필수 key 검사, `git check-ignore -v` | 40/40 파싱 PASS; `prob_1..40` 정확히 존재; 약 70MB; `.gitignore:8:data/` 적용 | 데이터 부재 차단은 해소됐으나 공식 provenance·기대 hash·redistribution는 미확정. 5단계 보완에서 real `prob_4` witness를 재현하고 전체 benchmark는 9단계 범위 유지 | `data/train 2/prob_1..20.json`, `data/train/prob_21..40.json` |
| 2026-07-12 / 5 보완 | Python 3.12.11, Shapely 2.1.2, Gurobi 13.0.2 restricted non-production, seed `20260710`; fake clock/backend 및 actual Gurobi | `cd baseline && /opt/homebrew/Caskroom/miniforge/base/envs/ogc-2026/bin/python -m unittest tests.test_retime tests.test_retime_integration -v`; 이어서 `-m unittest discover -s tests -p 'test_*.py' -v` | 종료 0, 전용 29/29 PASS, 전체 95/95 PASS. 신규 회귀의 최초 red는 25 tests 중 5 FAIL/1 ERROR로 R5-001~R5-005를 모두 재현 | 호출 순서 `checker→checker→retime`; retimer crash에서도 constructor objective/operations `1022.6988257889582` 보존; retimed strict install `Z1 3→0`; fake 0.1초는 relation 1회·0.01초 후 `BUDGET`, backend 0회; 160 singleton은 실제 backend request 160개, `max free=1<=80`; negative due primary `12`, false infeasible 없음; TIME_LIMIT feasible/no-solution, FREE/SEPARATE/I_OUTER/K_OUTER, fixed boundary, `w1=0`, same-exit blocker-first 모두 green | `baseline/solver/{entry,retime}.py`, `baseline/tests/{test_retime,test_retime_integration}.py` |
| 2026-07-12 / 5 real witness·감사 | local `data/train 2/prob_4.json`, source blocks 24/46 | real witness 전용 test, public baseline/root import smoke, manifest 재검사, `git diff --check`, `compileall`, checker `cmp`, 금지 파일/time API 감사 | 모두 종료 0; 양쪽 public Stage 5 objective `1022.6988257889582`; static audit PASS | bay 0, orientation-list index 5/1, `(102,18)`/`(115,3)`에서 `I_OUTER`; conservative `Z1=7`, nested retime `Z1=0`, Stage 5, internal/checker 최대 상대 오차 0. ordered exit과 synthetic equal-exit blocker-first canonical serialization 모두 PASS | `data/train 2/prob_4.json`(gitignored, 비커밋), `baseline/tests/test_retime_integration.py` |
| 2026-07-13 / 6 | Python 3.12.11, Shapely 2.1.2, seed `20260710`; fake clock/RNG와 synthetic operator families | `cd baseline && /opt/homebrew/Caskroom/miniforge/base/envs/ogc-2026/bin/python -m unittest tests.test_neighborhoods tests.test_alns tests.test_alns_integration -v` | 종료 0, 전용 23/23 PASS; six selector intent, transactional rollback, regret repair, weighted SA boundary, segment formula/floor, destroy growth/cap, strict trace, exact-delta invariant stop, deadline, retime exception/stall, replay, disabled hook | tracked constructor snapshot 6 iterations: start `(Z1,Z2,Z3,total)=(0.0,183.68374331550805,0.0,1285.7862032085563)`, best trace `(1285.7862032085563,1055.7278963621302)`, final Stage 5 `(0.0,19.3896994803043,46.0,1055.7278963621302)`; internal/checker 최대 상대 오차 0 | 초기 21/21 green 후 완료 감사에서 작은 `n`의 destroy max cap과 stall retime 지연을 발견해 보완(22/22). 이어 configured repair engine의 반복 delta 불변식 오류가 즉시 누적·중단되는 regression을 추가해 최종 23/23 재검증 | `baseline/solver/{construct,neighborhoods,alns,entry}.py`, `baseline/tests/{test_neighborhoods,test_alns,test_alns_integration}.py` |
| 2026-07-13 / 6 | 동일 환경·seed | `cd baseline && /opt/homebrew/Caskroom/miniforge/base/envs/ogc-2026/bin/python -m unittest discover -s tests -p 'test_*.py' -v` | 종료 0, 전체 118/118 PASS | 1~5단계 checker/objective/four-state/Gurobi fallback 회귀 포함; fixed-seed metrics/best replay 동일, worse current·repair/retime exception·deadline은 validated best를 변경하지 않음 | 완료 감사 보완 후 전용 23/23과 전체 118/118을 연속 재실행해 PASS | `baseline/tests/` |
| 2026-07-13 / 6 완료 감사 | tracked example, seed `20260710`, `LNS_ENABLED=False` | operator/trace evidence probe, public `algorithm(raw,5.0)` baseline/root 양쪽 import smoke, `compileall`, `git diff --check`, checker `cmp`, 금지 파일/time API 감사 | short run counters `(attempts,feasible,accepted,new-best)`: tardy `(1,1,1,1)`, congested `(1,1,1,0)`, Shaw `(0,0,0,0)`, Z2 `(1,1,1,0)`, preference `(1,1,0,0)`, random `(2,2,2,0)`; 별도 operator activity fixture는 6종 각각 `(1,1,1)`; 양쪽 public Stage 5 objective `1022.6988257889582`; static audit PASS | short run accepted trace `(1285.7862032085563,1285.7862032085563,1285.7862032085563,1055.7278963621302,1055.7278963621302,1055.7278963621302)`, `k=4`, warm-up 미완료로 temperature fallback `None`, weights 각 `1.0`, retime trigger 0, cache hit/miss `16452/6372`; submission hook 호출 없음 | 실패 없음. operator 최종 usefulness/removal 판단은 설계대로 9단계 daily-40 gate에 유지 | `alg_tester/example/example_B2_b10.json`, `baseline/tests/test_alns_integration.py` |
| 2026-07-13 / 7 | Python 3.12.11, Shapely 2.1.2, Gurobi 13.0.2 restricted non-production(2027-11-29 만료), seed `20260710`; actual Gurobi와 deterministic enumeration/failure backends | `cd baseline && /opt/homebrew/Caskroom/miniforge/base/envs/ogc-2026/bin/python -m unittest tests.test_repair_mip tests.test_repair_mip_integration -v` | 종료 0, 전용 20/20 PASS; cap 16/32/512, 16×33 trim, incumbent inclusion/dedup/start, unchanged/pair/AABB/cycle cuts, exact Z2, timeout/failure/transaction/portfolio 포함 | actual 3-block tiny model `OPTIMAL`, variables 10, constraints 7, `ObjVal=ObjBound=15.25`, gap 0; 별도 3×3 candidate solve가 전수열거 exact best와 일치. late conflict와 exit cycle은 각각 pair/no-good cut 후 conflicts/cycles 0 extraction; Stage 5와 internal/checker 상대 오차 0 | 테스트 작성 중 completion audit에서 retimer가 예외를 status `ERROR`로 흡수하는 production 계약을 확인해 MIP transaction이 해당 status도 reject하도록 보강; hard-cap config 상향 금지와 cycle no-good convergence regression 추가 후 20/20 재검증 | `baseline/solver/{repair_mip,neighborhoods,alns,entry}.py`, `baseline/tests/{test_repair_mip,test_repair_mip_integration}.py` |
| 2026-07-13 / 7 | 동일 환경·seed | `cd baseline && /opt/homebrew/Caskroom/miniforge/base/envs/ogc-2026/bin/python -m unittest discover -s tests -p 'test_*.py' -v` | 종료 0, 전체 138/138 PASS | 1~6단계 checker/objective/four-state/Gurobi fallback 회귀 포함; import/license/model/timeout은 heuristic fallback 1회와 Stage 5를 보존하고 retime error/checker reject는 current/best identity를 보존 | 전용 20/20 뒤 전체 138/138을 연속 재실행해 PASS | `baseline/tests/` |
| 2026-07-13 / 7 완료 감사 | tracked example, `LNS_ENABLED=False` | public `algorithm(raw,5.0)` baseline/root 양쪽 import smoke, `compileall`, `git diff --check`, checker `cmp`, 금지 파일/time API 감사 | 모두 종료 0; 양쪽 public Stage 5 `(Z1,Z2,Z3,total)=(0.0,14.671260826994029,46.0,1022.6988257889582)`; `rg 'time\.time\('` 0건; static audit PASS | MIP repair는 safe incumbent와 LNS gate 뒤 lazy 연결, `gurobipy`는 default backend factory 내부에서만 import. submission flag는 계속 off이며 daily-40 usefulness/activation 미실행 유지 | 실패 없음 | `alg_tester/example/example_B2_b10.json`, 변경 파일 diff |
| 2026-07-13 / 8 | Python 3.12.11, Shapely 2.1.2, Gurobi 13.0.2 restricted non-production(2027-11-29 만료); deterministic synthetic/real witness | `cd baseline && /opt/homebrew/Caskroom/miniforge/base/envs/ogc-2026/bin/python -m unittest tests.test_interlock tests.test_interlock_integration -v` | 종료 0, 전용 15/15 PASS; gate 전 분기, `.45` threshold, 8% cap, pressure/window, exact I/K filter, negative-AABB fit, host ranking, transaction, stall hook, strict install, failure/cycle/default-off 포함 | synthetic pressure `0.5`, exact `I_OUTER`, retimer actual `K_NESTED`, `Z1/total 7→0`, Stage 5, parity 0; real `prob_4` source 24/46 witness도 `I_OUTER/K_NESTED`, `7→0`, Stage 5, parity 0. real pressure `0.045942...`이므로 usefulness witness일 뿐 default `.45` activation 증거로 사용하지 않음 | 최초 구현/정식 suite 모두 green; retime exception은 후보별 rollback하고 input serialized identity 보존 | `baseline/solver/interlock.py`, `baseline/tests/{test_interlock,test_interlock_integration}.py` |
| 2026-07-13 / 8 | 동일 환경 | `cd baseline && /opt/homebrew/Caskroom/miniforge/base/envs/ogc-2026/bin/python -m unittest discover -s tests -p 'test_*.py' -v` | 종료 0, 전체 153/153 PASS | 1~7단계 checker/objective/four-state/Gurobi fallback 회귀 포함; gate-off, nonbeneficial, license/error, same-exit cycle은 validated best/Stage 5를 보존 | 전용 15/15 뒤 전체 153/153 연속 PASS | `baseline/tests/` |
| 2026-07-13 / 8 완료 감사 | tracked example, `LNS_ENABLED=False`, `INTERLOCK_ENABLED=False` | public `algorithm(raw,5.0)` baseline/root 양쪽 import smoke, `compileall`, `git diff --check`, checker `cmp`, 금지 파일/time API 감사 | 모두 종료 0; 양쪽 public Stage 5 `(Z1,Z2,Z3,total)=(0.0,14.671260826994029,46.0,1022.6988257889582)`로 7단계 gate-off 결과와 동일; `rg 'time\.time\('` 0건; static audit PASS | densifier는 MIP repair와 분리되고 ALNS stall hook에서 최대 1회 호출. AABB는 reject-only, exact kernel/retime/canonical serializer/full checker/strict store가 acceptance 권위. submission gate 둘 다 계속 off | 실패 없음; 전체 40개 benchmark 미실행 | `alg_tester/example/example_B2_b10.json`, 변경 파일 diff |
| 2026-07-13 / 9A 선행 감사 | 공식 OGC 2026 website, Problem Statement v1.2, FAQ, baseline v1.3, Training Set 1/2; worktree `252f0e4c3e38f8d4e8d898940788fbec6198089a` | `git ls-remote origin refs/heads/sol-native-implementation`; 공식 asset를 `/tmp/ogc-step9-9a-20260713/`에만 `curl -fL -o`로 수집; `shasum -a 256`; `unzip -l`; 40개 `cmp -s`; official/local `utils.py` 및 `baseline_greedy.py` `cmp`/`diff`; 확정 인터페이스·금지 파일 static `rg`/`git diff` | 원격 ref가 지정 8단계 커밋과 일치. 공식 archive hash: Problem Statement `1f7fb17e...`, baseline `c37248b9...`, Set 1 `b9a5790f...`, Set 2 `dbe2245b...`; daily-40 40/40 byte 일치. official/local checker hash `d45aaeaf...`/`d0347a3e...` 불일치, official/local greedy `e9cd8e3e...`/`8ec2cc81...` 불일치 | worktree 내부 checker 두 copy는 동일하고 금지 파일은 1~8단계 동안 diff 0. 하지만 official checker는 simultaneous ENTRY Stage 2와 `Z2` floor semantics가 달라 기존 objective/four-state evidence를 actual submission checker 증거로 사용할 수 없음 | O-001~O-005 전부를 권위 있게 닫지 못했고 O-007 checker 충돌을 발견해 9A에서 중단. 9단계 tests/benchmark/archive/commit/push 미실행 | 임시 공식 asset: `/tmp/ogc-step9-9a-20260713/`(git 비추적); 본 진행 문서만 변경 |
| 2026-07-13 / 9 prerequisite artifact·dataset | Python 3.12.13, macOS 26.5.2 arm64; 공식 asset는 `/tmp/ogc-step9-remediation-20260713/`에만 저장 | 네 URL에 `curl -fL -o`; `shasum -a 256`; Set 1 `prob_1..20`/Set 2 `prob_21..40` 각각 `cmp -s`와 `shasum -a 256` | Problem Statement v1.2 `1f7fb17e31fad327259ec967cf143dc5b2baada33b759ab4f926c732659e1e2c`; baseline v1.3 `c37248b9e7f1b8597450d5346a5b25538a7f16f390697b00474cdbd0921bc6c3`; Set 1 `b9a5790f3ef04edfeec1631531a65f56687e166e11fc10141a74c5f893790aee`; Set 2 `dbe2245bb18b2a5df8bff33d24e65ce137fd21e7cc5579921c11c293d8994bf3`; local 40/40 byte 일치 | hash 불일치 0. instance 40개 full hash는 tracked manifest에 기록하고 데이터는 재배포하지 않음 | 실패 없음 | `docs/implementation/sol/evidence/step9-evidence.json` |
| 2026-07-13 / 9 prerequisite checker remediation | Python 3.12.13, Shapely 2.1.2, Gurobi 13.0.2 restricted local | official archive `utils.py`를 두 위치에 원본 복사; `shasum`/`cmp`; red: `cd baseline && ... -m unittest tests.test_checker_contract tests.test_foundation -v`; 수정 후 official checker 4-suite 39 tests, 지정 영향 18-suite 156 tests, 전체 discovery 156 tests | 두 checker와 official archive 모두 SHA-256 `d45aaeafdce8bf80d59d097f655c43313a4951bed43b6628e3b1cf62d4876a94`. pre-fix red 24 tests 중 failures 3/errors 4: 동시 ENTRY 기대 stage, float `Z2`, safe install parity. post-fix 39/39, 156/156, 156/156 PASS | semantic diff는 Stage 2 co-entry 조건 `a_k <= a_i`→`a_k < a_i`, `Z2=max(range)`→`floor(max(range))`. 내부 objective, constructor delta, assignment exact/guide 및 repair MIP objective를 floor semantics로 정합화; conservative FREE-only simultaneous-entry serializer 유지. 2,000 random four-state 불일치 0, checker rejection rollback green | red를 먼저 재현한 뒤 수정·재검증. checker 원본은 수작업 편집하지 않음 | `baseline/utils.py`, `baseline/solver/`, `baseline/tests/`, `alg_tester/utils.py` |
| 2026-07-13 / 9 prerequisite exception·import | Python 3.12.13; tracked example; isolated `python -I` clean tree | file-location load with algorithm folder `sys.path[0]`; `cd baseline; import myalgorithm`; repo root `import baseline.myalgorithm`; submission tree copy+official server `utils.py` injection; negative/tiny/large timelimit와 Gurobi import/license/model/timeout failure suites | 세 public import와 clean isolated smoke 모두 Stage 5, `(Z1,Z2,Z3,total)=(121.0,183.0,0.0,323988.0)`; public stdout/stderr empty, public exception 0. debug runner positional path/help/monotonic green | import path는 module location 기준이며 repository parent/개발자 절대 경로 의존 0. optional Gurobi failure는 checker-validated Stage 5 incumbent를 보존; local restricted license는 production entitlement 증거로 사용하지 않음 | 실패 없음 | `/tmp/ogc-step9-clean-smoke-20260713/`(비추적), `baseline/tests/` |

### 8.1 prerequisite remediation exact test commands

```bash
cd baseline
/opt/homebrew/Caskroom/miniforge/base/envs/ogc-2026/bin/python -m unittest \
  tests.test_checker_contract tests.test_foundation \
  tests.test_geometry tests.test_four_state_parity -v

/opt/homebrew/Caskroom/miniforge/base/envs/ogc-2026/bin/python -m unittest \
  tests.test_checker_contract tests.test_foundation tests.test_geometry \
  tests.test_four_state_parity tests.test_assignment \
  tests.test_assignment_integration tests.test_construct \
  tests.test_construct_integration tests.test_retime \
  tests.test_retime_integration tests.test_neighborhoods tests.test_alns \
  tests.test_alns_integration tests.test_repair_mip \
  tests.test_repair_mip_integration tests.test_interlock \
  tests.test_interlock_integration -v

/opt/homebrew/Caskroom/miniforge/base/envs/ogc-2026/bin/python \
  -m unittest discover -s tests -p 'test_*.py' -v
```

결과는 각각 `39/39 PASS`, `156/156 PASS`, `156/156 PASS`다. 공식 checker 설치 전 red 재현 명령은 첫 명령에서 `tests.test_geometry tests.test_four_state_parity`를 제외한 형태였고 `24 tests, failures=3, errors=4`였다. import smoke는 file-location, baseline CWD, repository package, isolated clean-copy 네 개를 별도 Python process로 실행했으며 모두 종료 코드 0/Stage 5였다.

## 9. 구현 중 결정 사항

| ID | 날짜 | 단계 | 질문/선택지 | 결정과 근거 | 영향 문서/파일 | 승인자 |
|---|---|---|---|---|---|---|
| D-001 | 계획 시점 | 전체 | 테스트 위치/프레임워크 | 현재 테스트가 없고 pytest 의존성이 없으므로 `baseline/tests/` + `unittest`를 사용 | 전 단계 | 1단계 구현으로 확정 |
| D-002 | 2026-07-12 | 1 | orientation 좌표 보존 방식 | 첫 layer 첫 vertex를 원점으로 모든 layer를 동일 이동한 immutable `raw_layers`와 local AABB를 저장한다. checker의 reference-point 이동과 동치이며 2단계 geometry가 그대로 재사용 가능하다. | `baseline/solver/instance.py` | 구현 증거로 확정 |
| D-003 | 2026-07-12 | 1 | optional phase 연결 방식 | `entry.solve`의 lazy loader가 validated incumbent 설치 후에만 phase callable을 얻고, 반환 candidate stream을 fresh serialize/check/install 한다. 1단계 기본 loader는 no-op이다. | `baseline/solver/entry.py` | event-order 통합 테스트로 확정 |
| D-004 | 2026-07-12 | 2 | geometry/serializer 연결과 cache canonicalization | repaired local layer에서 suffix union을 뒤에서 앞으로 만들고, `(canonical block/orientation pair, relative dx/dy)` bounded LRU에 exact 양방향 bit를 저장한다. `GeometryKernel`이 1단계 `ExitPrecedenceProvider`를 직접 구현하므로 canonical serializer 코드는 변경하지 않는다. | `baseline/solver/geometry.py`, `baseline/solver/__init__.py` | 2,000건 checker parity·cache symmetry·real-provider topology 테스트로 확정 |
| D-005 | 2026-07-12 | 3 | assignment portfolio와 phase 경계 | solution pool 대신 deterministic solve-inspect-add-Hamming-cut loop를 기본으로 하고 canonical `(assignment_cost,bay_tuple)` 정렬을 적용한다. `entry`는 safe install 뒤에만 assignment를 lazy import하며 `OptionalPhaseResult`에 backend-neutral portfolio를 보관하되 seed를 incumbent candidate로 노출하지 않는다. | `baseline/solver/assignment.py`, `baseline/solver/entry.py` | zero-objective/diversity, lazy import, timeout-guide-only, Gurobi enumeration 테스트로 확정 |
| D-006 | 2026-07-12 | 4 | vertex-edge integer rounding과 assignment seed bay 정책 | vertex/edge contact translation은 checker integer 좌표에 맞춰 floor/ceil 양쪽을 생성하고 exact kernel이 최종 판정한다. AssignmentSeed bay/start는 모든 profile에서 canonical tie guide로만 사용해 대체 bay를 허용하며, seed가 없거나 master가 실패해도 같은 pure-Python constructor를 실행한다. | `baseline/solver/{construct,entry}.py` | negative/contact, exact FREE, master-empty, seeded profile 통합 테스트로 확정 |
| D-007 | 2026-07-12 | 5 | component cap, two-pass solve, 동률·`w1=0` 정책 | non-FREE component 전체를 포함하되 component당 tardiness/affected 우선 최대 80개만 free로 두고 나머지는 날짜 고정 boundary로 모델링한다. primary integer optimum을 equality로 고정한 뒤 secondary dwell extension을 최적화한다. primary/secondary strict 개선이 없으면 alternate dates를 노출하지 않고 input identity를 유지하며, `w1=0`은 checker total 개선이 불가능하므로 retiming을 skip한다. | `baseline/solver/{retime,entry}.py` | 81-node cap, exhaustive oracle, secondary tightening, tracked no-improvement identity, `w1=0`, failure rollback 테스트로 확정 |
| D-008 | 2026-07-12 | 5 | 완료 후 감사 결과의 단계 상태 | 초기 82/82 green만으로는 orchestration, 전체 deadline, 실제 backend request cap 계약이 증명되지 않았으므로 5단계를 `보완 필요`로 재개방하고 R5-001~R5-005 및 real-data witness가 green일 때만 다시 완료 처리한다. 기존 커밋 `e5493ef`는 rollback하지 않고 보완 기준선으로 유지한다. | 본 진행 문서, 후속 `entry.py`/`retime.py`/tests | 완료 후 재현 진단과 사용자 결정으로 확정 |
| D-009 | 2026-07-12 | 5 보완 | checker/retimer 책임, component cap과 failure isolation | `entry`가 constructor canonical serialize/full-check/strict install을 소유하고 validated `store.snapshot`만 retimer에 전달한다. retimer는 component별 request(`free_ids<=80`)를 하나의 child deadline 아래 순차 처리하고 relation/serializer/checker/objective gate를 통과한 최종 후보와 checker 증거를 반환한다. `entry`는 이 증거로 strict install해 production 경로의 중복 full-check를 없앤다. 한 component/backend 실패는 다른 component와 이미 설치된 constructor를 폐기하지 않는다. | `baseline/solver/{entry,retime}.py`, 관련 tests | 29/29 전용·95/95 전체와 event/failure matrix로 확정 |
| D-010 | 2026-07-13 | 6 | acceptance 기본, repair 확장 경계, submission hook | 설계의 열린 SA/RRT 선택 중 기본값이 명시된 time-cooled SA를 구현한다. 32회 improving-only warm-up의 improvement magnitude median으로 `T0=median/ln(2)`를 정하고 sample이 없으면 deterministic non-worse fallback을 유지한다. `RepairResult`와 configured repair-engine tuple은 backend-neutral public 경계로 두어 7단계가 MIP engine을 추가하되 `alns.py`가 이를 import하지 않게 한다. `entry.LNS_ENABLED=False`를 submission 기본으로 고정한다. | `baseline/solver/{construct,neighborhoods,alns,entry,__init__}.py`, 관련 tests | 전용 23/23·전체 118/118, replay/acceptance/invariant/deadline/disabled-hook tests로 확정 |
| D-011 | 2026-07-13 | 7 | product 초과 정책, same-exit 제약, engine activation | 후보는 known weighted cost+canonical tuple 순으로 dedup/trim하되 incumbent를 마지막까지 보존하고 16×33 입력을 16×32/product 512로 제한한다. same-exit correctness는 base rank 변수를 늘리는 대신 selected-only exact inspection과 deterministic no-good cycle cut으로 보장한다. 기존 4인자 `RepairEngine`/`RepairResult` 계약은 유지하고 entry의 lazy portfolio `(heuristic,mip)`에서 heuristic을 기본, MIP을 주기적 또는 stall 직후 선택한다. MIP 또는 fallback 결과만 retime→canonical full-check transaction을 강제한다. | `baseline/solver/{repair_mip,neighborhoods,alns,entry}.py`, 관련 tests | 전용 20/20·전체 138/138, actual Gurobi enumeration, pair/cycle cut convergence, failure/transaction matrix로 확정 |
| D-012 | 2026-07-13 | 8 | interlock generator 위치, pressure/gate와 install 경계 | 712줄인 `neighborhoods.py`의 heuristic/MIP column 책임과 one-way offset search를 분리하기 위해 `interlock.py`를 신설한다. pressure는 bay event window별 `sum(union area×duration)/(bay area×duration)`의 최댓값으로 계산하고 `.45` gate에 사용한다. candidate는 AABB reject-only 뒤 exact `I_OUTER/K_OUTER`만 유지하며, affected relation transaction→기존 retimer→actual nested mode 분류→canonical serialization/full checker/objective parity→strict store 순서만 허용한다. ALNS는 stall 후 최대 1회 hook을 호출하고 entry의 `INTERLOCK_ENABLED=False`가 submission 기본이다. | `baseline/solver/{interlock,alns,entry,__init__}.py`, 관련 tests | 전용 15/15·전체 153/153, synthetic/real witness, gate identity, rollback/cycle/default-off 감사로 확정 |
| D-013 | 2026-07-13 | 9 prerequisite | 공식 checker, dataset/package/import/Gurobi/artifact 정책 | Problem Statement v1.2/baseline v1.3/Training Set 1·2/FAQ를 권위로 사용한다. checker 두 copy는 official archive 원본으로 고정하고 모든 내부 `Z2`/delta/model은 floored parity를 사용한다. daily-40은 local benchmark 입력만 허용하고 재배포하지 않는다. submission은 root `myalgorithm.py`+sibling relative helpers만 포함하며 `utils.py`, debug runner, data/result/cache/license를 제외한다. public 출력은 disabled, Gurobi는 license 관리 없이 Threads≤4·동시 model 1개·bounded caps/failure fallback을 유지한다. benchmark raw/summary는 gitignored, tracked manifest에는 hash/command/environment/summary만 둔다. | checker/solver/tests, `.gitignore`, `experiments/ogc_sage/benchmark_ogc_sage.py`, evidence manifest, 본 문서 | 사용자 승인 정책과 39/39·156/156·clean import smoke로 확정 |
| D-014 | 2026-07-13 | 9B-1 | benchmark variant/subset/timeout/run identity 사전 고정 | 결과 열람 전 variant를 `safe_fallback`, `constructor_only`, `constructor_retime`, `heuristic_lns`, `candidate_mip`, `interlock`으로 고정한다. constructor gate는 daily-40, 60초, seed `20260710`, `constructor_retime` 40회다. feature/competition comparison은 아래 dense 9개, 60초, seeds `20260710,20260711`에서 여섯 variant 전부(각 18회, 총 108회) 실행한다. final 600은 선택된 submission default 한 개만 실행한다. dense subset은 input-only score `minimum-orientation bbox-area / total-bay-area * mean(1/fit-bay-count) * sqrt(block-count/100)` 상위 8개 `prob_{39,27,30,37,32,9,8,38}.json`에 기존 문서 witness `prob_4.json`을 합한 9개다. 1800초 subset은 같은 score 1위 `prob_39.json`, seed `20260710`이다. launcher no-op 5회와 safe-output official-checker 5회 p95를 실행 전 측정하고 outer timeout을 `TL + max(5, 2*checker_p95 + launcher_p95 + 1)`로 고정한다. run-id는 `step9-<gate>-<UTC>-<commit12>-<config12>`, config는 canonical JSON SHA-256, run key는 schema/source commit/dataset hash/config hash/variant/instance hash/budget/seed의 canonical JSON SHA-256이다. raw schema v1은 terminal record append+flush, 동일 key의 schema-valid terminal만 resume skip한다. | `09_PACKAGING_STRESS_HARDENING.md`, harness/config/tests/evidence | 결과 기반 subset/threshold 변경 금지; constructor gate 실패 시 feature/full matrix 미실행 |

## 10. 운영 결정과 잔여 미해결 사항

| ID | 단계 | 내용 | 필요한 결정/증거 | 상태 |
|---|---|---|---|---|
| O-001 | 9 | 공식 Training Set 1/2 archive와 local daily-40이 40/40 byte 일치. URL/archive hash/각 instance hash는 evidence manifest에 기록 | training data는 local benchmark 입력으로만 사용하고 source/submission/release archive에서 재배포하지 않는 보수 정책 | 운영상 해결 |
| O-002 | 9 | 단일 zip, root `myalgorithm.py`, 15MB 이하, sibling relative helper, Ubuntu 24.04/4 CPU/16GB, no network/parent access. `utils.py`, data/result/cache/license/debug runner 제외; default multiprocessing 없음, Gurobi Threads≤4·동시 model 1개 | public stdout/stderr disabled, logging 없음. clean simulated server injection smoke PASS; 실제 submission archive 생성/rehearsal은 9단계 범위로 미실행 | 운영상 해결 |
| O-003 | 1/9 | file-location+algorithm folder `sys.path[0]`, `cd baseline; import myalgorithm`, repo-root `import baseline.myalgorithm` 및 isolated clean copy를 모두 지원 | 모든 solver import는 module-relative이며 repository parent/정확한 evaluation CWD/개발자 절대 경로에 의존하지 않음 | 해결 |
| O-004 | 9 | `baseline/run_myalgorithm.py`는 local-only debug tool, instance positional 필수, tracked example 도움말, `time.monotonic()` 사용 | submission tree에서 제외 | 해결 |
| O-005 | 3/9 | 공식 server의 Gurobi 13.0.2 license를 사용하며 algorithm은 license 관리 코드를 포함하지 않음. 명시적 size 보장은 없다고 보고 기존 caps 유지 | import/license/size/model/numeric/timeout/SolCount failure는 optional failure로 Stage 5 incumbent 보존. local restricted license 성공은 production entitlement 증거가 아님 | 운영상 해결 |
| O-007 | 9 | 공식 baseline v1.3 `utils.py`를 두 copy에 byte 설치하고 SHA-256 `d45aaeafdce8bf80d59d097f655c43313a4951bed43b6628e3b1cf62d4876a94` lock. diff는 co-entry Stage 2 strictness와 `Z2` floor | foundation/state, assignment/constructor, retime/LNS/MIP/interlock을 공식 checker로 재감사해 156/156 PASS; checker는 원본 외 수작업 변경 없음 | 해결 |
| O-008 | 9 | tracked harness `experiments/ogc_sage/benchmark_ogc_sage.py`; raw/summary `artifacts/ogc_sage/step9/<run-id>/{raw.jsonl,summary.json}`; tracked `docs/implementation/sol/evidence/step9-evidence.json` | run directory는 gitignored, placeholder만 tracked. manifest schema는 dataset/config/commit/artifact hash·정확한 command·environment·result summary. 600-run artifact는 생성하지 않음 | 정책 해결·benchmark 미실행 |
| O-006 | 5 | 설계 §17 real interlock witness | source block index 24/orientation-list index 5 `(102,18)`와 index 46/index 1 `(115,3)`에서 `I_OUTER`, conservative `Z1=7` vs nested `Z1=0`, Stage 5, parity 0 재현 | 해결 |
| R5-001 | 5 | checker 미검증 constructor snapshot을 retimer가 먼저 소비함 | event order `checker(fallback)→checker(constructor)→retime` 및 validated store snapshot 입력 증명 | 해결 |
| R5-002 | 5 | unexpected retimer exception이 constructor 개선 전체를 폐기함 | constructor 선설치와 후보별 retimer 예외 격리; import/license/model/numeric/unexpected matrix에서 objective/operations 보존 | 해결 |
| R5-003 | 5 | component/pair 전처리가 retiming child/global deadline 확인 전에 O(n²) 실행됨 | 전처리 전 child budget 생성, relation/pair/model-build loop deadline 확인; fake 0.1초에서 relation 1회·backend 0회 | 해결 |
| R5-004 | 5 | component별 80 cap을 합친 단일 backend request가 실제 cap을 초과함 | component별 transactional backend request; 160 singleton에서 request 160개, 각 `free_ids=1`, component 실패 격리 | 해결 |
| R5-005 | 5 | `T_i`의 `ub=horizon` 때문에 negative due가 false infeasible | `T_i>=0`, `T_i>=e_i-D_i`만 유지하도록 잘못된 상한 제거; `(R,D,P)=(0,-10,2)` primary 12 regression green | 해결 |

## 11. 커밋 및 변경 파일 기록

| 단계 | 커밋 해시 | 권장 메시지 | 변경 파일 | 리뷰/비고 |
|---|---|---|---|---|
| 1 | 완료 커밋(본 행을 포함하는 커밋; 종료 보고에 해시 기록) | `step 1: foundation` | `baseline/myalgorithm.py`, `baseline/solver/{__init__,budget,entry,fallback,instance,serialize,state}.py`, `baseline/tests/{__init__,helpers,test_checker_contract,test_foundation}.py`, 본 진행 문서 | 1A~1D 및 23 tests |
| 2 | 완료 커밋(본 행을 포함하는 커밋; 종료 보고에 해시 기록) | `step 2: geometry` | `baseline/solver/{__init__,geometry}.py`, `baseline/tests/{test_geometry,test_four_state_parity}.py`, 본 진행 문서 | 2A~2C, 전용 13 tests, 전체 36 tests, random 2,000 parity |
| 3 | 완료 커밋(본 행을 포함하는 커밋; 종료 보고에 해시 기록) | `step 3: assignment` | `baseline/solver/{assignment,entry}.py`, `baseline/tests/{test_assignment,test_assignment_integration}.py`, 본 진행 문서 | 3A~3C, 전용 15 tests, 전체 51 tests, exact enumeration/objective parity/failure matrix |
| 4 | 완료 커밋(본 행을 포함하는 커밋; 종료 보고에 해시 기록) | `step 4: constructor` | `baseline/solver/{__init__,construct,entry,state}.py`, `baseline/tests/{test_assignment_integration,test_construct,test_construct_integration}.py`, 본 진행 문서 | 4A~4D, 전용 15 tests, 전체 66 tests, 6 profile/fallback/full-check gate |
| 5 | `e5493efd47968cae3be88e5dc953183b79cf2ce8` (초기 구현; 보완 필요) | `step 5: retiming` | `baseline/solver/{__init__,entry,retime}.py`, `baseline/tests/{test_retime,test_retime_integration}.py`, 본 진행 문서 | 초기 5A~5C와 82 tests는 green이나 완료 후 R5-001~R5-005 발견 |
| 5 보완 | 완료 커밋(종료 보고에 해시 기록) | `step 5: harden retiming` | `baseline/solver/{entry,retime}.py`, `baseline/tests/{test_retime,test_retime_integration}.py`, 본 진행 문서 | R5-001~R5-005, failure matrix, component isolation, real `prob_4` witness; 전용 29/29·전체 95/95 |
| 6 | 완료 커밋(본 행을 포함하는 커밋; 종료 보고에 해시 기록) | `step 6: lns` | `baseline/solver/{__init__,construct,entry,neighborhoods,alns}.py`, `baseline/tests/{test_neighborhoods,test_alns,test_alns_integration}.py`, 본 진행 문서 | 6A~6D, 전용 23/23·전체 118/118, tracked Stage 5 trace/replay/rollback/invariant/deadline/flag-off 증거 |
| 7 | 완료 커밋(본 행을 포함하는 커밋; 종료 보고에 해시 기록) | `step 7: mip repair` | `baseline/solver/{repair_mip,neighborhoods,alns,entry}.py`, `baseline/tests/{test_repair_mip,test_repair_mip_integration}.py`, 본 진행 문서 | 7A~7C, 전용 20/20·전체 138/138, actual Gurobi enumeration/cut/fallback/transaction/flag-off 증거 |
| 8 | 완료 커밋(본 행을 포함하는 커밋; 종료 보고에 해시 기록) | `step 8: interlock` | `baseline/solver/{__init__,interlock,alns,entry}.py`, `baseline/tests/{test_interlock,test_interlock_integration}.py`, 본 진행 문서 | 8A~8C, 전용 15/15·전체 153/153, synthetic/real nested witness, rollback/strict install/stall/default-off 증거 |
| 9 prerequisite remediation | 완료 커밋(종료 보고에 해시 기록) | `remediate official checker contract for step 9` | official checker 두 copy, `baseline/{myalgorithm,run_myalgorithm}.py`, `baseline/solver/{state,assignment,repair_mip}.py`, 관련 tests, `.gitignore`, benchmark/evidence placeholder, 본 진행 문서 | official-checker 39/39·영향/전체 156/156·import/clean smoke green; 9단계 hardening/benchmark/archive 미실행 |

한 커밋에 서로 다른 최상위 단계를 섞지 않는다. 단계 구현과 그 테스트는 같은 단계 범위로 관리하되, 테스트가 통과하기 전 커밋을 완료 커밋으로 기록하지 않는다.

## 12. 전체 validation gate 추적

| Gate | 설계 근거 | 적용 단계 | 합격 기준 | 상태/증거 |
|---|---|---|---|---|
| canonical serialization | §5, §11, §14.1 | 1,2,5,9 | 시간 key 오름차순, EXIT-first, exit DAG, simultaneous entry FREE | official checker에서 co-entry는 Stage 2 제외 후 Stage 5 순서 검증됨을 직접 재현. serializer는 보수적 FREE-only co-entry와 exit DAG를 유지하고 checker contract/four-state 23 tests PASS |
| monotonic wall-clock | §12 | 1,3~9 | `time.monotonic()`만 사용, 모든 loop/model 경계 deadline 확인 | 기존 solver 경계 유지. local debug runner도 `time.monotonic()`으로 교체했고 production `time.time()` 0건. negative/tiny/large timelimit Stage 5·public exception 0 |
| immutable validated best | §1, §5, §9 | 1,4~9 | full checker strict improvement만 atomic install | fallback과 constructor를 각각 full checker strict install한 뒤 validated `store.snapshot`만 retime/LNS에 전달한다. 7단계 MIP/fallback transaction 증거를 유지한다. 8단계 offset은 fresh draft에만 적용하고 affected exact relation→retime→canonical full-check/parity를 통과한 strict total improvement만 store에 설치; gate/failure/equal-worse/cycle은 input identity 보존 |
| optional optimizer armor | §1, §5, §12, §14.1 | 1,3,5,7,9 | import/license/model/timeout 예외가 public entry 탈출 안 함 | import/license/size/model/numeric/timeout/SolCount failure를 optional failure로 유지하고 checker-validated incumbent를 보존. public 5초 tracked smoke Stage 5 `(0,14,46,1018)`, public stdout/stderr와 exception 0 |
| Shapely/checker authority | §3, §7, §15 | 2,4,8,9 | approximate filter는 거부 권한 없음; exact survivor check | 2·4단계 증거 유지. 8단계 AABB는 offset reject-only이며 survivor는 checker-parity kernel의 exact `I_OUTER/K_OUTER`, affected-pair exact mode, retimer, canonical serializer와 full checker를 모두 통과해야 함; synthetic/real witness Stage 5, parity 0 |
| contract edge cases | §14.1 | 1,2,9 | 지정 8개 계약 모두 regression green | official hash/copy identity, half-open, P=0, boundary, negative AABB, simultaneous ENTRY/EXIT, chronological dict, floored float-workload Z2, canonical serialization, rejection rollback 전체 PASS |
| four-state parity | §3, §14.1 | 2,5,9 | 4상태와 수천 sample checker 불일치 0 | official checker로 실제 fitting pair-placement/time 2,000건 불일치 0; 784 exhaustive indicator cases와 SEPARATE/I_OUTER/K_OUTER actual solve Stage 5 |
| objective/delta parity | §3, §9, §14.1 | 1,3,4,6,7,9 | 상대 오차 `<=1e-6` | 모든 내부 `Z2`를 official `floor(max(normalized)-min(normalized))`로 통일. constructor delta는 단계별 floored range 차이로 telescope하며 assignment exact/guide와 candidate-selection MIP은 integer floor 변수를 사용한다. 전수열거/model/extraction/checker 최대 상대 오차 0 |
| constructor activation | §13, §14.2 | 4,9 | daily-40 60s: 40/40, p90 `<=8s`, max `<=12s`, deadline hit 0 | local data 40개 제공·파싱 확인; 전체 60s gate는 9단계 범위로 미실행 |
| P5/P6 enable gate | §13 | 6~9 | 위 constructor gate 전 submission default disabled | remediation 후에도 `entry.LNS_ENABLED=False`, `entry.INTERLOCK_ENABLED=False`; daily-40 constructor/dense-subset gate와 실제 enable은 9단계 hardening까지 미실행 |
| retiming safety | §8, §14.2 | 5,9 | Z1 비증가, bound/gap/time 기록, 실패 rollback | R5-001~R5-005 해결. actual Gurobi FREE/SEPARATE/I_OUTER/K_OUTER, fixed boundary, TIME_LIMIT feasible/no-solution, `w1=0`, same-exit canonical, negative due 모두 green. real `prob_4` `Z1 7→0`, Stage 5, parity 0; backend request마다 `free_ids<=80` |
| operator usefulness | §14.2 | 6,7,9 | 각 operator가 정당한 family에서 feasible/accepted >0, 아니면 제거 | 6단계 six-selector 활동 증거 유지. 7단계 repair portfolio가 heuristic 기본, MIP periodic/stall 선택을 재현하고 engine attempts/feasible/fallback/time metrics를 기록함을 통합 테스트로 확인. 최종 usefulness/removal은 daily-40 데이터가 필요한 9단계 gate로 유지 |
| interlock usefulness | §10, §14.2 | 8,9 | predefined dense subset 개선, feasibility regression 0 | 8단계 algorithm correctness 부분 통과: synthetic pressure `.5`와 real `prob_4` shape witness에서 exact `I_OUTER`, actual `K_NESTED`, `Z1/total 7→0`, Stage 5/parity 0; 전체 153/153 regression 0. real witness pressure `.045942...`는 default activation 증거가 아니며 predefined dense subset usefulness/enable 결정은 9단계 미실행 |
| final correctness | §14.1 | 9 | 40 instances×모든 예산×모든 seed Stage 5 100% | local data 40개 제공·파싱 확인; correctness matrix는 9단계 범위로 미실행 |
| competition comparison | §14.3 | 9 | W/T/L, Borda, gap, weighted components, median/p90/worst, primal integral | 미실행 |

## 13. 공통 위험 원칙

- candidate는 항상 별도 draft에서 만들며 current/best를 직접 수정하지 않는다.
- filter/AABB/surrogate는 후보를 줄일 수 있지만, 최종 가능 판정은 Shapely와 full checker만 한다.
- optional Gurobi 코드 import는 safe incumbent가 checker를 통과하고 저장된 뒤에만 수행한다.
- checker 호출 예상 시간과 reserve가 부족하면 새 full check를 시작하지 않는다.
- P5/P6는 gate가 충족되고 그 증거가 이 문서에 기록되기 전 submission 설정에서 켜지 않는다.
- 문서의 기본값 변경은 ablation과 결정 기록 없이 수행하지 않는다.

## 14. 계획 문서 품질 감사

2026-07-12 계획 작성 종료 시 구현·테스트·벤치마크 실행 없이 Markdown 문서만 정적으로 감사했다.

| 감사 항목 | 결과 | 근거 |
|---|---|---|
| 설계 §13의 1~9 구현 순서 전체 포함 | 통과 | 동일 순서의 9개 단계 문서와 링크 존재 |
| P0~P6 누락 없음 | 통과 | §3 매핑표에서 P0~P6 모두 최소 한 단계에 배정 |
| canonical serializer, wall-clock budget, validation gate | 통과 | 1단계, 9단계 및 §12 추적표 |
| forward dependency | 통과 | 1단계 provider protocol→2단계 구현, 4단계 master optional, 6단계 heuristic-only 완결, 8단계 MIP optional; N단계가 N+1 구현을 요구하지 않음 |
| 모든 단계 단위·통합 테스트/명령/expected 결과 | 통과 | 각 단계 문서 §15~18 |
| 완료 및 다음 단계 진입 조건 | 통과 | 각 단계 문서 §19~21 |
| safe incumbent가 optimizer import보다 먼저 | 통과 | 1단계 event-order integration test와 3·5·7단계 lazy import 계약 |
| immutable best/transactional candidate | 통과 | 1단계 state 계약 및 4~8단계 rollback gate |
| Shapely/checker 최종 권위 | 통과 | 2단계 parity와 4·7·8단계 exact survivor check |
| Gurobi failure가 pure-Python path 보존 | 통과 | 3·5·7단계 failure matrix, 4·6단계 독립 완결 |
| P5/P6 activation gate 일치 | 통과 | 4·6~9단계에서 default off와 daily-40 constructor gate 유지 |
| 지정 checker edge cases와 four-state/objective parity | 통과 | 1·2·9단계 테스트 계획과 §12 추적표 |
| 문서 외 파일 변경 없음 | 통과 | 작성 종료 시 worktree status는 `docs/implementation/`만 untracked, 기준 소스 diff 없음 |

정적 감사 결과는 구현 완료나 test PASS를 의미하지 않는다. 각 runtime gate의 상태는 계속 `미실행`이며, 구현 시 실제 증거로 교체해야 한다.
