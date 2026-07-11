# 06. Heuristic LNS

## 1. 단계 목표

heuristic destroy와 regret-2/3 repair만으로 완결되는 deterministic anytime LNS를 구현한다. `current`, immutable validated `best`, transactional `candidate`를 분리하고, search 중 어떤 실패나 deadline에서도 이미 serialized된 best를 O(1)로 반환한다.

## 2. 설계 근거

설계 §4 P5, §9.1~9.2/9.4, §12 defaults/adaptation/stall, §13 구현 순서 6 및 activation gate, §14.

## 3. P0~P6 대응

P5 heuristic core. P0 state/budget/geometry, P1 best, P3 insertion, optional P4 retiming을 소비한다. 7단계 MIP repair를 요구하지 않는다.

## 4. 선행 조건

1~5단계 완료. 4단계 pure-Python insertion API가 destroyed blocks의 재삽입을 지원하고, 5단계 retimer failure가 identity rollback임이 검증되어야 한다.

## 5. 현재 저장소 상태

adaptive search/state/operator 구현이 현재 branch에 없다. 과거 문서는 deprecated이며 단일 설계 기준이 아니다. 설계 기록상 과거 solver는 constructor가 거의 전 시간을 사용해 LNS가 실행되지 않았으므로 이 단계 활성화는 9단계 constructor performance gate 전 금지한다.

## 6. 구현 범위

- 6 destroy operators: tardy chain, congested bay-time, Shaw, Z2 contributor, preference alternative, random-k.
- regret-2/3 heuristic repair와 affected retime hook.
- adaptive weights, SA/RRT worse-current acceptance, stall/reheat.
- exact checker-equivalent objective/delta, strict validated-best install.
- operator attempts/feasible/accepted/new-best/delta/time/cache metrics.

## 7. 비범위

Gurobi repair(7), interlock offsets(8), submission default enable(9). full checker를 모든 current move에 반드시 호출하는 정책도 비범위: local exact predicates로 current feasibility를 보장하고 best install에만 full checker를 강제한다.

## 8. 변경 또는 추가할 파일

- 추가 `baseline/solver/neighborhoods.py`, `baseline/solver/alns.py`.
- 변경 `construct.py`(repair용 public insertion API만), `entry.py`(disabled-by-default hook), `state.py`(indexes/transaction contract 내).
- 추가 `baseline/tests/test_neighborhoods.py`, `baseline/tests/test_alns.py`, `baseline/tests/test_alns_integration.py`.

## 9. 파일별 책임

- `neighborhoods.py`: destroy selection, relatedness, candidate generation/heuristic repair. acceptance/global loop를 모른다.
- `alns.py`: RNG, operator portfolio, current/candidate/best 흐름, retime batching, adaptation/metrics/deadline.
- `entry.py`: feature flag와 gate. 초기값은 off.

## 10. 인터페이스

```text
DestroyOperator.select(current,k,rng,context) -> ordered tuple[block_id,...]
heuristic_repair(current,destroyed,context,budget) -> RepairResult
AlnsConfig(seed=20260710,segment=50,reaction=.2,rewards=(20,5,2),...)
AlnsMetrics(per_operator,iterations,best_trace,cache_stats,time_by_phase)
run_lns(initial,best_store,context,budget,config,retime_hook=None) -> AlnsResult
```

destroy size `max(4,ceil(.02n))`, stall 때 ×1.5, 최대 `ceil(.15n)`. SA warm-up 32 improving-only iterations에서 positive delta median을 수집하고 `T0=median/ln2`; positive sample이 없으면 deterministic non-worse/current policy로 fallback한다.

## 11. checker 불변 조건

- `best`는 full checker strict total improvement만 교체. trace는 non-increasing.
- `current`도 geometry/time/objective exact predicate로 feasible해야 하며 serialize 가능한 상태만 수용.
- candidate는 current copy에서 작업, repair/retime/serialization/check 실패 시 통째 rollback.
- acceptance authoritative value는 scalar weighted total. lexicographic tardiness 사용 금지.
- delta와 full checker objective 상대 오차 `<=1e-6`을 sampled best install마다 검증.
- returned value는 current가 아니라 `best_store.serialized_operations`.

## 12. 예외·timeout·Gurobi fallback

이 단계는 Gurobi 없이 동작한다. optional retime hook의 import/license/timeout/exception은 unre-timed feasible candidate 또는 rollback으로 처리한다. 모든 iteration/operator inner loop에서 deadline을 확인하고, checker predicted duration+margin이 부족하면 best check를 시작하지 않는다. exception counter를 남기고 다음 operator로 진행하되 반복되는 invariant error는 search를 중단하고 best 반환한다.

## 13. 세부 구현 순서

1. relatedness/critical chain/load contribution pure selectors와 deterministic tie.
2. destroy가 state에서 ids를 제거한 draft와 boundary context 생성.
3. 4단계 candidate generator를 current-position 포함 mode로 호출해 regret-2/3 repair.
4. local exact feasibility/objective, transaction freeze.
5. acceptance: improving current, threshold/SA worse current, strict new best 분리.
6. 50-iteration segment reward/weight update, zero-weight floor, stall detection/reheat/k growth.
7. accepted geometry change `max(3,ceil(.03*n_b))` 또는 stall 때 affected retime hook.
8. deadline/metrics/O(1) return와 disabled-by-default entry integration.

## 14. 하위 단계 산출물

- 6A destroy operator unit.
- 6B heuristic transactional repair.
- 6C acceptance/adaptation/stall.
- 6D anytime loop/metrics/entry flag.

6A~6D는 순서대로 operator intent, rollback, deterministic acceptance, end-to-end best-trace 테스트가 green일 때만 완료한다.

## 15. 단위 테스트 계획

| 케이스 | 입력 | 예상 |
|---|---|---|
| tardy chain | directed relation graph와 tardy root | root+active chain, k cap |
| congested window | bay-time union occupancy | 최고 congestion ids 우선 |
| Shaw | space/time/assignment distances | seed와 가장 related한 ids |
| Z2 contributor | normalized loads max/min bays | range 감소 후보 ids 선택 |
| preference | high loss+fit alternative | 해당 block 우선 |
| random-k | fixed RNG | 정확히 k unique, replay 동일 |
| repair rollback | 마지막 block no candidate | input current/index unchanged |
| acceptance | weighted deltas/temperature/fake RNG | improving 항상, worse 확률 경계 정확 |
| weights | attempts/rewards segment | formula `(1-r)w+r(score/use)`와 floor |
| destroy growth | n/stall sequence | initial/×1.5/max cap 정확 |
| best trace | better/equal/worse candidates | strict better만 append/install |
| deadline | fake clock iteration 중 만료 | 추가 operator/check 없음, stored best 반환 |

## 16. 단계 통합 테스트

- `test_seeded_lns_preserves_feasibility`: tracked example constructor snapshot, fixed short iteration budget. 모든 accepted current local feasible, final checker Stage 5.
- `test_best_trace_nonincreasing`: checker result sequence를 instrument하여 strict total 감소만 기록.
- `test_worse_current_never_leaks`: worse feasible current 수용 후 deadline; returned operations는 validated best identity.
- `test_retime_failure_continues`: retime hook raises; heuristic candidate 처리 정책대로 feasible current/best 유지, public exception 없음.
- `test_each_operator_activity_fixture`: operator별 의도된 synthetic family에서 attempts>0, feasible repair>0; accepted는 설계상 가능한 fixture에서 >0.
- `test_disabled_submission_path`: flag off일 때 LNS 함수가 호출되지 않고 5단계 best와 동일.

## 17. 테스트 실행 명령

```bash
cd baseline
/opt/homebrew/Caskroom/miniforge/base/envs/ogc-2026/bin/python -m unittest tests.test_neighborhoods tests.test_alns tests.test_alns_integration -v
/opt/homebrew/Caskroom/miniforge/base/envs/ogc-2026/bin/python -m unittest discover -s tests -p 'test_*.py' -v
```

## 18. 테스트별 입력·예상 기록

seed, start objective, per-operator attempts/feasible/accepted/new-best, delta list, temperature/weights before-after, k, retime triggers, iterations, exit reason, final checker stage/objective를 기록한다. wall-clock 비결정성을 피한 unit test는 fake clock과 iteration cap을 함께 쓴다.

## 19. 완료 조건

- six operators unit/integration intent coverage.
- fixed seed replay에서 selection/metrics/best 동일.
- accepted current feasibility와 best trace non-increasing.
- worse current/exception/deadline이 return best를 바꾸지 않음.
- Gurobi 없이 전체 1~6 suite green.
- submission activation flag는 off로 유지.

## 20. 다음 단계 제공 인터페이스

7단계가 `RepairEngine` 호환 결과를 추가할 수 있도록 heuristic repair와 공통 `RepairResult`를 제공한다. `alns.py`는 configured engine portfolio를 받되 MIP 구체 import를 하지 않는다.

## 21. 진행 문서 업데이트

operator activity, deterministic seed, trace, exception/rollback, disabled flag를 기록한다. operator usefulness 최종 gate는 daily-40에서 9단계가 판단하므로 아직 완료 표시하지 않는다.

## 22. 위험과 대응

- LNS가 시간만 소비: operator metrics와 9단계 removal gate.
- local/current semantic drift: relation predicate와 periodic/sample full checker; best는 항상 full checker.
- SA numerical edge: no-positive-delta fallback, temperature lower bound.
- stale candidate after current change: state version check와 commit 전 재평가.
- best mutation: store가 serialized immutable payload를 독점.

## 23. 미해결 사항

- SA와 RRT 중 기본 acceptance 명칭/공식은 설계가 “SA/RRT”로 열어두었다. 우선 설계 기본값이 명시된 SA를 구현하고 RRT 추가 여부는 ablation 후 결정한다.
- periodic current full-check 빈도는 checker p95와 semantic confidence를 9단계에서 측정해 확정한다.
- operator removal은 daily-40 데이터 없이는 결정하지 않는다.

## 24. 커밋 경계

권장 메시지 `feat(solver): add heuristic anytime lns`. heuristic operators/loop/tests만 포함하며 `repair_mip.py`나 interlock을 포함하지 않는다.
