# 01. 기반 계약과 안전 incumbent

## 1. 단계 목표

어떤 optional optimizer도 import하기 전에 공식 checker가 Stage 5로 승인한 bay별 직렬 incumbent를 만들고, 이를 immutable snapshot으로 보존한다. 동시에 이후 모든 단계가 재사용할 instance/state/budget/serializer 계약을 고정한다.

## 2. 설계 근거

- 설계 §1 안전 anytime 원칙, §3 checker objective, §5 P1, §11 canonical serialization, §12 wall-clock, §13 구현 순서 1, §14.1 correctness.
- 이 단계는 설계의 P0 최소 기반과 P1 전체에 대응한다.
- `d_i=max(P_i,1)`을 사용한다. checker Stage 1만 보면 `P=0`의 zero dwell이 허용되지만 Stage 5 replay에서 EXIT-before-ENTRY가 되어 실패하기 때문이다.

## 3. P0~P6 대응

- P0: immutable parsing, orientation local AABB, integer reference range, exact objective, monotonic budget, serializer/checker adapter.
- P1: deterministic parallel-serial fallback와 validated incumbent.
- P2~P6: 비범위. 단, 이후 모듈이 의존할 protocol만 정의한다.

## 4. 선행 조건

- `start-point` 기준 worktree와 깨끗한 단계 시작 상태.
- `baseline/utils.py`를 checker oracle로 읽을 수 있어야 한다.
- Gurobi 설치·license는 선행 조건이 아니다.

## 5. 현재 저장소 상태

- `baseline/myalgorithm.py::algorithm(prob_info,timelimit)`은 함수 내부에서 `baseline_greedy`를 import하고 결과를 그대로 반환한다.
- `baseline/utils.py::check_feasibility`는 operations dictionary를 **삽입 순서대로** 먼저 훑어 assignment를 만들고, Stage 5에서만 날짜를 정렬한다. EXIT가 ENTRY보다 dictionary에 먼저 등장하면 동일 block의 exit가 assignment에 반영되지 않을 수 있다.
- checker는 ENTRY 좌표를 `int(round(...))`으로 geometry에 적용하고, operation date key는 integer 변환 가능 문자열만 허용한다.
- 별도 tests와 solver package가 없으므로 `baseline/tests/`를 신설하고 `unittest`를 사용한다.

## 6. 구현 범위

1. raw dict를 deep-copy/freeze한 immutable domain model로 파싱.
2. 각 orientation의 layer 좌표와 local AABB, bay별 유효 integer reference range 계산.
3. `time.monotonic()` 기반 global deadline/reserve/sub-budget.
4. immutable placement/schedule/snapshot, exact objective components, checker 결과를 포함한 incumbent store.
5. 단 하나의 serializer와 exit precedence provider 계약.
6. bay별 deterministic 직렬 fallback 생성, serialize, full checker 검증.
7. public entry의 exception armor. fallback 검증 후에만 optional phase loader를 호출할 수 있는 orchestration hook 제공.

## 7. 명시적 비범위

- suffix union, exact obstruction/four-state 실제 계산(2단계).
- Gurobi import 또는 model(3·5·7단계).
- overlapping constructor, LNS, interlock.
- `baseline/utils.py`, `baseline/baseline_greedy.py`, 환경 의존성 변경.

## 8. 변경 또는 추가할 파일

| 파일 | 작업 |
|---|---|
| `baseline/myalgorithm.py` | guarded public entry로 교체 |
| `baseline/solver/__init__.py` | public 최소 export |
| `baseline/solver/entry.py` | parse→fallback→validate→optional orchestration→return |
| `baseline/solver/budget.py` | monotonic budget/reserve |
| `baseline/solver/instance.py` | immutable instance와 integer fit |
| `baseline/solver/state.py` | snapshot/draft/objective/incumbent |
| `baseline/solver/serialize.py` | canonical serializer |
| `baseline/solver/fallback.py` | parallel-serial safe candidate |
| `baseline/tests/__init__.py` | unittest discovery |
| `baseline/tests/helpers.py` | synthetic instance/loader/checker import helper |
| `baseline/tests/test_checker_contract.py` | 필수 checker 계약 8종 |
| `baseline/tests/test_foundation.py` | parsing/budget/state/fallback/entry |

## 9. 파일별 책임

- `entry.py`만 checker를 호출하고 validated best를 교체한다. broad exception은 여기서 흡수하고 이미 검증된 incumbent를 반환한다.
- `instance.py`는 raw input mutation을 차단하고 index 기반 lookup만 제공한다. geometry exact 연산은 포함하지 않는다.
- `state.py`는 `SolutionSnapshot`을 frozen tuple로 유지하고 `CandidateDraft`가 commit 전 변경을 격리한다.
- `serialize.py`는 operations 생성의 유일한 위치다. geometry를 import하지 않는다.
- `fallback.py`는 후보를 만들 뿐 checker나 incumbent store를 직접 조작하지 않는다.

## 10. 확정할 인터페이스

```text
parse_instance(raw: Mapping) -> Instance
OrientationInfo(index, raw_layers, xmin, ymin, xmax, ymax)
OrientationInfo.integer_range(bay) -> ReferenceRange | None
Budget.start(limit, clock=time.monotonic) -> Budget
Budget.remaining(), can_start(predicted, margin), child(cap)
Placement(block_id,bay_id,orient_idx,x,y,entry,exit)
SolutionSnapshot(placements: tuple[Placement,...], objective: ObjectiveParts|None)
CandidateDraft.from_snapshot(snapshot); freeze() -> SolutionSnapshot
compute_objective(instance,snapshot) -> ObjectiveParts(z1,z2,z3,total)
ExitPrecedenceProvider.edges(exiting_ids, snapshot, bay_id) -> Iterable[(before,after)]
serialize(snapshot, precedence_provider) -> {"operations": dict[str,list[dict]]}
build_safe_candidate(instance,budget) -> SolutionSnapshot
IncumbentStore.install_if_valid(candidate, operations, checker_result) -> bool
solve(prob_info,timelimit,checker=check_feasibility) -> dict
```

`IncumbentStore`는 checker `feasible=True`, `stage=5`, objective parity, strict improvement를 모두 확인하고 snapshot/serialized operations/checker result를 한 번에 교체한다. 최초 설치에는 strict comparison을 적용하지 않는다. 외부에는 defensive copy 또는 immutable view만 반환한다.

## 11. checker 불변 조건

- block마다 ENTRY/EXIT 정확히 1회, 동일 bay, valid `orient_idx`와 integer id/date/x/y.
- `entry>=R`, `exit-entry>=max(P,1)`.
- local AABB가 음수여도 `ceil(-xmin)..floor(W-xmax)`, `ceil(-ymin)..floor(H-ymax)`가 비어 있지 않아야 fit이다. width/height만 비교하지 않는다.
- 날짜 key는 숫자 오름차순으로 삽입하고 모든 EXIT를 모든 ENTRY보다 먼저 둔다.
- same-bay same-date exit는 provider edge `blocker -> mover`의 위상순서. cycle은 candidate 거부.
- same-bay simultaneous entry는 provider가 모든 pair FREE라고 승인한 경우만 허용. stage 1 safe fallback은 동일 bay entry를 동시화하지 않는다.
- boundary 접촉은 area 0이므로 허용된다.
- objective total과 delta는 checker와 상대 오차 `<=1e-6`.

## 12. 예외·timeout·Gurobi fallback

- parsing 또는 safe candidate 자체가 실패하면 official-valid-instance 전제가 깨진 것이므로 `entry.py`가 명확한 diagnostic을 남기고, 검증되지 않은 객체를 반환하지 않는다. public API에서 사용할 최종 정책(raise 대 emergency behavior)은 O-002 제출 규칙 확인 전 미해결이다.
- safe incumbent가 설치된 뒤 optional loader/import/model/checker 개선 호출에서 발생하는 `ImportError`, license/model/size error, timeout, 일반 예외는 catch하고 incumbent operations를 반환한다.
- `timelimit<2s` 또는 reserve 부족이면 optional hook을 호출하지 않는다.
- 최초 full check는 반드시 수행한다. 검증 완료 전 optimizer import 금지는 import spy 테스트로 고정한다.

## 13. 세부 구현 순서

### 1A. 계약과 parser

1. synthetic fixtures와 실패하는 contract tests를 먼저 추가한다.
2. frozen dataclass/tuple/proxy로 raw input의 aliasing을 제거한다.
3. orientation reference point를 첫 layer 첫 vertex로 두고 local AABB를 계산한다.
4. 모든 block에 최소 하나의 valid integer placement가 있는지 기록한다. 없으면 `NoValidPlacement`.

### 1B. state, objective, budget

1. `ObjectiveParts`, `Placement`, `SolutionSnapshot`, `CandidateDraft` 구현.
2. checker와 같은 `u_j=avg_area/(W_jH_j)`, load range, preference loss 계산.
3. fake clock으로 monotonic budget/reserve 공식을 검증.

### 1C. serializer와 fallback

1. precedence protocol/fake provider로 date/EXIT/ENTRY/topology contract 구현.
2. deterministic bay 선택: valid fit 중 preference loss, normalized projected load, bay id 순으로 최소화.
3. bay별 `previous_exit`, `entry=max(R,previous_exit)`, `exit=entry+d`와 minimum integer anchor 사용.
4. empty precedence provider로 serialize하고 full checker 실행.

### 1D. entry와 immutable incumbent

1. fallback checker PASS를 확인하여 최초 store install.
2. 그 다음에만 lazy optional-phase loader 호출.
3. candidate마다 fresh serialization→full check→strict improvement install.
4. 모든 종료 경로에서 store의 이미 serialized된 operations 반환.

## 14. 하위 단계별 산출물

- 1A: `Instance`와 integer fit 계약, checker edge fixture.
- 1B: exact objective/state/budget.
- 1C: canonical operations와 Stage-5 safe candidate.
- 1D: optional exception에도 변하지 않는 public return path.

각 하위 단계는 해당 test file green 전 다음 하위 단계로 넘어가지 않는다. 하나의 최상위 커밋을 유지하되 필요하면 `1A~1D`를 별도 승인 커밋으로 분리한다.

## 15. 단위 테스트 계획

| 파일/케이스 | 입력 | assertion/예상 결과 |
|---|---|---|
| `test_foundation.py::test_negative_aabb_integer_range` | xmin=-3.49,xmax=7.62, W=12 | x range `4..4`; anchor 0 사용 금지 |
| `...::test_fractional_only_fit_rejected` | 정수 range lower>upper | orientation/bay fit=False |
| `...::test_input_is_immutable` | parse 후 raw nested list 수정 | parsed layers/state 불변 |
| `...::test_zero_processing_uses_one_day` | R=D=4,P=0 | `[4,5)`, checker Stage 5 PASS |
| `...::test_budget_fake_clock` | TL=10, p95 samples, clock advance | remaining 비증가, reserve formula와 child cap 준수 |
| `...::test_objective_float_z2` | 면적 다른 2 bays와 float workloads | checker `obj2/total`과 `isclose` |
| `...::test_draft_rollback` | snapshot에서 placement 수정 후 discard | original/best byte-equivalent |
| `...::test_strict_install` | equal/worse/better checker objective | better만 store 교체 |
| `test_checker_contract.py::test_half_open_handover` | A exit=t, B entry=t, 같은 위치 | EXIT-first이면 Stage 5 PASS |
| `...::test_boundary_contact` | polygon edge/point 접촉 | collision area 0, PASS |
| `...::test_simultaneous_entries` | FREE pair와 obstructed pair | FREE PASS, obstructed expected Stage 2 또는 5 |
| `...::test_simultaneous_exit_topology` | fake edge `k->i`, 역순/정순 | serializer 정순 PASS; 역순 fixture Stage 5 실패 |
| `...::test_chronological_dictionary_reconstruction` | exit date key를 entry보다 먼저 insert한 수동 dict | expected Stage 1 no EXIT; serializer 출력은 PASS |

## 16. 단계 통합 테스트

- `test_public_entry_safe_before_optional_import`: 추적 예제, timelimit 1.0, optional loader spy. 결과 `feasible=True,stage=5`; spy 미호출.
- `test_optional_import_failure_returns_incumbent`: fallback check 시점과 import spy 순서를 기록하고 loader가 `ImportError` 발생. 반환 operations는 저장된 fallback과 동일하고 checker PASS.
- `test_optional_mutation_cannot_corrupt_best`: optional hook이 draft를 변형 후 예외. returned best와 최초 serialized bytes 동일.
- `test_all_blocks_parallel_serial`: 예제의 모든 block이 정확히 한 번 배치되고 bay별 interval은 disjoint, 다른 bay concurrency 허용, internal/checker objective parity.

## 17. 테스트 실행 명령

```bash
cd baseline
/opt/homebrew/Caskroom/miniforge/base/envs/ogc-2026/bin/python -m unittest tests.test_checker_contract tests.test_foundation -v
/opt/homebrew/Caskroom/miniforge/base/envs/ogc-2026/bin/python -m unittest discover -s tests -p 'test_*.py' -v
```

이번 계획 작성 중에는 실행하지 않는다. 구현 단계에서 첫 명령의 실패→구현→green 후 전체 discovery를 실행한다.

## 18. 테스트별 판정 기록

각 테스트 기록에는 fixture 이름/seed, expected checker stage, actual stage, expected/actual `(obj1,obj2,obj3,total)`, 상대 오차를 남긴다. infeasible fixture는 가장 이른 checker stage만 확정값으로 사용한다. Stage 2와 Stage 5가 모두 가능한 simultaneous-entry 위반은 fixture를 고정한 뒤 실제 earliest stage를 테스트 이름/문서에 확정한다.

## 19. 완료 조건

- 1A~1D 단위와 통합 테스트 전부 PASS.
- 예제에서 public entry가 Stage 5 PASS 및 objective parity.
- optional import가 최초 incumbent checker 성공 뒤 발생한다는 event log assertion PASS.
- wall-clock에 `time.time()` 사용 0건(`rg 'time\.time' baseline/solver baseline/myalgorithm.py`).
- `baseline/utils.py`, `baseline/baseline_greedy.py` diff 없음.
- 진행 문서에 증거/변경 파일/커밋 기록 완료.

## 20. 다음 단계 제공 인터페이스

2단계는 `Instance`, `OrientationInfo.raw_layers/AABB`, `Placement`, `SolutionSnapshot`, `CandidateDraft`, `ExitPrecedenceProvider`를 그대로 사용한다. 2단계 구현이나 이름을 1단계가 import하지 않는다.

## 21. 진행 문서 업데이트

1단계 행을 완료로 변경하고 checker contract별 결과, safe check 이전/이후 import event, objective parity, 명령·건수·커밋을 기록한다. 2단계 진입을 `가능`으로 바꾸되 O-003 import 계약은 계속 추적한다.

## 22. 위험과 대응

- checker dictionary reconstruction 함정: chronological insertion을 serializer 단일 경로로 강제.
- negative AABB: reference range 공식을 domain type에 캡슐화.
- mutable raw dict: parse 즉시 deep immutable 변환.
- checker 시간이 TL을 소진: 최초 check는 필수, 이후 p95와 reserve로 새 check 제한.
- fake provider가 실제 geometry를 가릴 위험: 2단계에 real provider parity 통합 gate를 둔다.

## 23. 미해결 사항

- official invalid instance에서 fallback 불가능할 때 public API가 raise 가능한지는 제출 규칙 확인 필요(O-002).
- package import 방식은 tester 실행 위치에 따라 달라질 수 있어 9단계 양쪽 smoke 전 확정하지 않는다(O-003).
- checker copy 두 개 중 제출 시 어느 파일이 실제 load되는지 9단계 tester smoke로 확인한다. 구현 oracle은 `baseline/utils.py`다.

## 24. 커밋 경계

- 권장 메시지: `feat(solver): establish safe validated incumbent`
- 포함: 위 1단계 파일과 테스트만.
- 제외: geometry.py, gurobipy import, constructor/LNS 코드.
- 완료 커밋 조건: 전체 1단계 suite green 후. 해시는 진행 문서에 기록한다.
