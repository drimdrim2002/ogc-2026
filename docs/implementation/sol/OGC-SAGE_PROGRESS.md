# OGC-SAGE 구현 진행 현황

## 1. 문서 목적과 기준

이 문서는 OGC-SAGE 구현의 유일한 운영 대장이다. 구현자는 각 단계의 시작·검증·완료 때 이 문서를 갱신하고, 상세 절차는 같은 디렉터리의 단계별 실행 문서를 따른다.

- 단일 설계 기준: [`../../OGC2026_Competition_Algorithm_Design.md`](../../OGC2026_Competition_Algorithm_Design.md)
- 계획 작성 기준 커밋: `78ef82ece960ac686a6f5c41497a13c2217ffab5`
- worktree: `../sol-native-implementation`
- 기준 브랜치: `start-point`
- 작업 브랜치: `sol-native-implementation`
- 계획 문서 상태: 작성 완료
- 구현 상태: 2단계 geometry 완료
- 테스트 상태: 1~2단계 단위·통합 및 `baseline/tests` 전체 36/36 PASS
- 다음 단계 진입: 3단계 assignment 진입 가능

설계와 저장소가 충돌할 때 구현자가 임의로 해석하지 않는다. 이 문서의 “결정 및 미해결 사항”에 기록하고 결정권자의 승인을 받은 뒤 관련 단계 문서를 함께 갱신한다.

## 2. 현재 저장소 기준선

- 공개 진입점 `baseline/myalgorithm.py`는 guarded import로 `solver.entry.solve`를 호출하고, 먼저 checker-validated safe incumbent를 만든다.
- `baseline/solver/`에 1단계 기반 모듈과 2단계 checker-parity geometry kernel, `baseline/tests/`에 36개 `unittest` 계약·단위·통합 테스트가 있다. `baseline/` 자체의 `__init__.py`는 없으며 양쪽 import smoke만 통과한 상태다.
- `baseline/utils.py`와 `alg_tester/utils.py`는 현재 byte-for-byte 동일하다. checker 권위는 변경 금지 대상인 `baseline/utils.py::check_feasibility`로 고정한다.
- `baseline/baseline_greedy.py`는 비교 기준으로 동결한다. 새 구현에서 import하거나 수정하지 않는다.
- 환경 파일에는 Python 3.12, Shapely 2.1+, Gurobi 13.0.2가 있으나 별도 test dependency는 없다. 따라서 새 테스트는 표준 라이브러리 `unittest`를 사용한다.
- 추적된 예제는 `alg_tester/example/example_B2_b10.json` 하나뿐이다. 설계가 참조하는 daily-40 데이터는 현재 checkout에 없다. 9단계 stress gate 전 데이터 위치·무결성을 결정해야 한다.
- `baseline/run_myalgorithm.py`의 기본 instance 경로는 현재 존재하지 않는다. 9단계에서 CLI 기본값 처리 방식을 결정한다.

## 3. 구현 순서와 P0~P6 매핑

| 단계 | 상태 | 설계 phase | 구현 범위 요약 | 다음 단계 진입 |
|---|---|---|---|---|
| 1 | 완료 | P0 최소 기반 + P1 | immutable parsing/AABB·integer anchor, checker 계약, serializer, budget, fallback, validated incumbent, exception armor | 23/23 green, 2단계 진입 가능 |
| 2 | 완료 | P0 완성 | Shapely ShapeInfo, repaired layers, suffix union, exact obstruction, four-state, bounded cache | 13/13 전용·36/36 전체 green, 3단계 진입 가능 |
| 3 | 진입 가능 | P2 | optional Gurobi assignment lower bound와 diverse portfolio, congestion guide | fallback 포함 통합 green 후 4 |
| 4 | 차단(3) | P3 | event-aware union-safe regret constructor와 empty-bay fallback | constructor 후보·checker 통합 green 후 5 |
| 5 | 차단(4) | P4 | indicator 기반 exact four-state retimer와 affected component | objective/checker 통합 green 후 6 |
| 6 | 차단(5) | P5 일부 | heuristic destroy/repair, current/best/candidate, adaptive acceptance | deterministic LNS 통합 green 후 7 |
| 7 | 차단(6) | P5 완성 | bounded candidate-selection Gurobi repair와 cut loop | heuristic fallback 통합 green 후 8 |
| 8 | 차단(7) | P6 | gate된 one-way interlock densifier와 final retiming | gate on/off 통합 green 후 9 |
| 9 | 차단(8) | P0~P6 운영 | packaging, exception/deadline/stress hardening, daily-40 validation gates | 모든 필수 gate 통과 후 릴리스 가능 |

P0은 1~2단계와 9단계, P1은 1·9단계, P2는 3·9단계, P3는 4·9단계, P4는 5·9단계, P5는 6·7·9단계, P6는 8·9단계에서 추적한다. 모든 P0~P6 요소가 최소 한 단계에 배정되어 있다.

## 4. 전체 의존성 순서

`1 foundation → 2 geometry → 3 assignment → 4 constructor → 5 retimer → 6 heuristic LNS → 7 MIP repair → 8 interlock → 9 packaging/stress`

확정된 경계는 다음과 같다.

- 1단계 serializer는 `ExitPrecedenceProvider` 프로토콜만 소비한다. 1단계 fallback은 bay별 직렬이라 빈 선행 그래프를 사용하고, 테스트는 fake provider로 topology를 검증한다. 실제 geometry provider는 2단계가 제공한다.
- 3단계는 1단계 `Instance`, `Budget`, `SolutionSnapshot`과 2단계 fit/area 정보만 사용한다.
- 4단계는 3단계 seed가 없어도 자체 deterministic profile로 실행 가능하다. master 실패가 constructor 완료를 막지 않는다.
- 5단계 입력은 완전한 checker-feasible snapshot이다. retimer 실패는 입력 snapshot을 그대로 반환한다.
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

## 9. 구현 중 결정 사항

| ID | 날짜 | 단계 | 질문/선택지 | 결정과 근거 | 영향 문서/파일 | 승인자 |
|---|---|---|---|---|---|---|
| D-001 | 계획 시점 | 전체 | 테스트 위치/프레임워크 | 현재 테스트가 없고 pytest 의존성이 없으므로 `baseline/tests/` + `unittest`를 사용 | 전 단계 | 1단계 구현으로 확정 |
| D-002 | 2026-07-12 | 1 | orientation 좌표 보존 방식 | 첫 layer 첫 vertex를 원점으로 모든 layer를 동일 이동한 immutable `raw_layers`와 local AABB를 저장한다. checker의 reference-point 이동과 동치이며 2단계 geometry가 그대로 재사용 가능하다. | `baseline/solver/instance.py` | 구현 증거로 확정 |
| D-003 | 2026-07-12 | 1 | optional phase 연결 방식 | `entry.solve`의 lazy loader가 validated incumbent 설치 후에만 phase callable을 얻고, 반환 candidate stream을 fresh serialize/check/install 한다. 1단계 기본 loader는 no-op이다. | `baseline/solver/entry.py` | event-order 통합 테스트로 확정 |
| D-004 | 2026-07-12 | 2 | geometry/serializer 연결과 cache canonicalization | repaired local layer에서 suffix union을 뒤에서 앞으로 만들고, `(canonical block/orientation pair, relative dx/dy)` bounded LRU에 exact 양방향 bit를 저장한다. `GeometryKernel`이 1단계 `ExitPrecedenceProvider`를 직접 구현하므로 canonical serializer 코드는 변경하지 않는다. | `baseline/solver/geometry.py`, `baseline/solver/__init__.py` | 2,000건 checker parity·cache symmetry·real-provider topology 테스트로 확정 |

## 10. 차단 사유와 미해결 사항

| ID | 단계 | 내용 | 필요한 결정/증거 | 상태 |
|---|---|---|---|---|
| O-001 | 9 | daily-40 데이터가 현재 checkout에 없음 | 공식 데이터 위치, 파일명, CI/로컬 제공 방식과 무결성 목록 확정 | 미해결 |
| O-002 | 9 | 제출 패키지 허용 파일·디렉터리와 Gurobi license 제공 조건이 저장소에 없음 | 대회 제출 규칙 및 production entitlement 확인 | 미해결 |
| O-003 | 1/9 | `baseline`은 `__init__.py` 없는 현재 실행 디렉터리 import 관례를 사용 | tester가 `baseline/`을 `sys.path`에 두는 계약 유지 여부; 상대/절대 import 양쪽 smoke 필요 | 미해결 |
| O-004 | 9 | `run_myalgorithm.py` 기본 경로가 checkout에 없음 | 추적 예제로 변경할지, CLI 인자 필수화할지 결정 | 미해결 |
| O-005 | 3 | Gurobi restricted license의 full-size 허용 범위 미확정 | 9단계 rehearsal에서 실제 최대 model 검증 | 미해결 |

## 11. 커밋 및 변경 파일 기록

| 단계 | 커밋 해시 | 권장 메시지 | 변경 파일 | 리뷰/비고 |
|---|---|---|---|---|
| 1 | 완료 커밋(본 행을 포함하는 커밋; 종료 보고에 해시 기록) | `step 1: foundation` | `baseline/myalgorithm.py`, `baseline/solver/{__init__,budget,entry,fallback,instance,serialize,state}.py`, `baseline/tests/{__init__,helpers,test_checker_contract,test_foundation}.py`, 본 진행 문서 | 1A~1D 및 23 tests |
| 2 | 완료 커밋(본 행을 포함하는 커밋; 종료 보고에 해시 기록) | `step 2: geometry` | `baseline/solver/{__init__,geometry}.py`, `baseline/tests/{test_geometry,test_four_state_parity}.py`, 본 진행 문서 | 2A~2C, 전용 13 tests, 전체 36 tests, random 2,000 parity |
| 3 | 미작성 | `feat(solver): add optional assignment master portfolio` | 실행 후 기록 | - |
| 4 | 미작성 | `feat(solver): add event-aware exact constructor` | 실행 후 기록 | - |
| 5 | 미작성 | `feat(solver): add exact four-state retiming` | 실행 후 기록 | - |
| 6 | 미작성 | `feat(solver): add heuristic anytime lns` | 실행 후 기록 | - |
| 7 | 미작성 | `feat(solver): add bounded candidate repair mip` | 실행 후 기록 | - |
| 8 | 미작성 | `feat(solver): add gated interlock densifier` | 실행 후 기록 | - |
| 9 | 미작성 | `chore(solver): harden submission and validation gates` | 실행 후 기록 | - |

한 커밋에 서로 다른 최상위 단계를 섞지 않는다. 단계 구현과 그 테스트는 같은 단계 범위로 관리하되, 테스트가 통과하기 전 커밋을 완료 커밋으로 기록하지 않는다.

## 12. 전체 validation gate 추적

| Gate | 설계 근거 | 적용 단계 | 합격 기준 | 상태/증거 |
|---|---|---|---|---|
| canonical serialization | §5, §11, §14.1 | 1,2,5,9 | 시간 key 오름차순, EXIT-first, exit DAG, simultaneous entry FREE | 1단계 checker contract 8 tests 및 2단계 actual geometry provider same-exit topology/simultaneous-entry gate PASS; 5·9단계 계속 추적 |
| monotonic wall-clock | §12 | 1,3~9 | `time.monotonic()`만 사용, 모든 loop/model 경계 deadline 확인 | 1단계 PASS: fake clock 및 `rg 'time\.time'` 0건; 3~9단계 계속 추적 |
| immutable validated best | §1, §5, §9 | 1,4~9 | full checker strict improvement만 atomic install | 1단계 PASS: equal/worse 거부, strict better atomic install, defensive copy 및 mutation rollback |
| optional optimizer armor | §1, §5, §12, §14.1 | 1,3,5,7,9 | import/license/model/timeout 예외가 public entry 탈출 안 함 | 1단계 PASS: safe check→loader event order, ImportError/일반 예외 시 byte-identical incumbent |
| Shapely/checker authority | §3, §7, §15 | 2,4,8,9 | approximate filter는 거부 권한 없음; exact survivor check | 2단계 PASS: checker와 같은 polygon repair/strict positive-area 판정, suffix-naive 및 direct `check_entry/check_exit` oracle 불일치 0; 4·8·9단계 계속 추적 |
| contract edge cases | §14.1 | 1,2,9 | 지정 8개 계약 모두 regression green | 1단계 지정 계약 및 2단계 negative anchor/boundary/four-state/real exit provider 회귀 전체 PASS |
| four-state parity | §3, §14.1 | 2,5,9 | 4상태와 수천 sample checker 불일치 0 | 2단계 PASS: seed `20260710`, 실제 fitting pair-placement/time 2,000건, directional·schedule checker 불일치 0; 5·9단계 계속 추적 |
| objective/delta parity | §3, §9, §14.1 | 1,3,4,6,7,9 | 상대 오차 `<=1e-6` | 1단계 PASS: tracked example z1/z2/z3/total 상대 오차 모두 0; 후속 delta는 해당 단계 계속 추적 |
| constructor activation | §13, §14.2 | 4,9 | daily-40 60s: 40/40, p90 `<=8s`, max `<=12s`, deadline hit 0 | 미실행/데이터 차단 |
| P5/P6 enable gate | §13 | 6~9 | 위 constructor gate 전 submission default disabled | 미실행 |
| retiming safety | §8, §14.2 | 5,9 | Z1 비증가, bound/gap/time 기록, 실패 rollback | 미실행 |
| operator usefulness | §14.2 | 6,7,9 | 각 operator가 정당한 family에서 feasible/accepted >0, 아니면 제거 | 미실행 |
| interlock usefulness | §10, §14.2 | 8,9 | predefined dense subset 개선, feasibility regression 0 | 미실행 |
| final correctness | §14.1 | 9 | 40 instances×모든 예산×모든 seed Stage 5 100% | 미실행/데이터 차단 |
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
