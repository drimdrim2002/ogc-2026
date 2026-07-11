# 05. Exact four-state retimer

## 1. 단계 목표

checker-feasible fixed layout의 bay/component별 entry/exit를 Gurobi indicator constraints로 exact 최적화한다. primary `sum tardiness`, secondary dwell extension을 최소화하고, 결과는 full checker strict improvement일 때만 설치한다.

## 2. 설계 근거

설계 §3 four-state theorem, §4 P4, §8 전체, §11 same-exit topology, §12 timebox/default, §13 구현 순서 5, §14.

## 3. P0~P6 대응

P4 전체. P0 geometry/serializer/budget/state와 P1 best, P3 layout을 소비한다. P5 LNS는 선행 조건이 아니다.

## 4. 선행 조건

1~4단계 완료. 입력 snapshot은 full checker Stage 5이고 fixed placement relation graph가 2단계 parity를 통과해야 한다. Gurobi availability는 선행 조건이 아니다.

## 5. 현재 저장소 상태

retiming module/model은 없다. checker interval semantics는 half-open이며 one-way nesting은 simultaneous entry를 허용하지 않지만 equal exit은 blocker-first serialization이면 허용한다. 설계 probe의 20-block 성능은 증거이지 full-scale 보장은 아니다.

## 6. 구현 범위

- bay 또는 affected non-FREE connected component model.
- integer `a,e,T`, release/dwell/tardiness/horizon.
- FREE/SEPARATE/I_OUTER/K_OUTER indicator encoding.
- warm start, primary tardiness 후 best primary 고정 및 secondary dwell extension.
- unchanged boundary block fixed constraints, component max 80 free variables.
- status/bound/gap/runtime/accepted delta metrics와 rollback.

## 7. 비범위

layout/assignment 변경, candidate generation, ALNS trigger 운영(6단계), interlock offset search(8단계). retimer는 `Z2/Z3`를 바꾸지 않는다.

## 8. 변경 또는 추가할 파일

- 추가 `baseline/solver/retime.py`.
- 최소 변경 `entry.py`(constructor 뒤 optional call), `serialize.py`(기존 provider 사용).
- 추가 `baseline/tests/test_retime.py`, `baseline/tests/test_retime_integration.py`.

## 9. 파일별 책임

- `retime.py`: component 선택, model request/build/solve/extract/verify. gurobipy lazy import는 이 파일 함수 내부.
- `geometry.py`: fixed relation 제공만; model knowledge 없음.
- `entry.py`: budget ladder, result serialize/full-check/install.

## 10. 인터페이스

```text
RetimingConfig(max_free=80,time_cap_s=3,threads=4,seed=20260710)
RetimingResult(snapshot,status,primary,bound,gap,runtime,changed_ids,diagnostics)
nonfree_components(snapshot,kernel,bay_id) -> tuple[frozenset[id],...]
retime(snapshot,instance,kernel,budget,affected_ids=None,backend_factory=...) -> RetimingResult
```

Bay horizon `H_b=max(0,max R_i)+sum d_i`; empty bay skip. `0<=a_i,e_i<=H_b`, `a_i>=R_i`, `e_i>=a_i+d_i`, `T_i>=e_i-D_i`, `T_i>=0`.

Pair encoding:

- SEPARATE: 1 binary, exactly one of `e_i<=a_k`, `e_k<=a_i` by indicators.
- I_OUTER: 3 binary sum=1 for i-before, k-before, `a_i+1<=a_k && e_k<=e_i`.
- K_OUTER: symmetric.
- FREE: no pair constraint.

## 11. checker 불변 조건

- every extracted integer date respects release/dwell and same fixed placement.
- selected relation mode must pass `PairRelation.allows` before serialization.
- equal-exit nested blocks use geometry exit DAG order; cycle rejects result.
- model objective primary는 unweighted `Z1`. fixed assignment이라 full total improvement는 `w1*ΔZ1`; `w1=0`이면 secondary가 total strict improvement를 만들지 못하므로 best install 금지(현재 snapshot 유지 가능).
- retime must never worsen Z1; full objective/checker parity `<=1e-6`.

## 12. 예외·timeout·Gurobi fallback

import/license/size/model/numeric/no solution/timeout은 input snapshot을 identity-equivalent하게 반환한다. TIME_LIMIT with feasible solution은 relation predicate, serializer, full checker를 모두 통과하고 Z1 비증가일 때 candidate로 반환 가능하다. global budget은 `min(3s,.05*remaining)`이며 checker reserve를 제외한다. 예외는 public entry를 탈출하지 않는다.

## 13. 세부 구현 순서

1. pure-Python relation-mode enumeration oracle와 component extraction.
2. horizon/release/dwell/T constraints 및 warm start.
3. four-state indicator builders; pair마다 exactly-one 검사.
4. primary optimize. feasible incumbent 없으면 rollback.
5. best primary 값 고정(수치 tolerance 없이 integer equality), secondary `sum(e-a-d)` optimize with remaining child budget.
6. integer extraction, per-pair predicate, changed component boundary 확인.
7. serialize/full checker/Z1/total parity 후 strict install.
8. metrics(bound,gap,time,delta,mode counts) 기록.

## 14. 하위 단계 산출물

- 5A component/horizon/model-independent truth table.
- 5B Gurobi primary/secondary solve.
- 5C serialization/full-check/fallback integration.

5A는 exhaustive truth table, 5B는 enumeration optimum, 5C는 checker/rollback integration이 각각 green일 때만 완료한다.

## 15. 단위 테스트 계획

| 케이스 | 입력 | 예상 |
|---|---|---|
| horizon | releases `[0,7]`, d `[3,4]` | H=14, serial schedule representable |
| empty bay | ids 없음 | model 없음, unchanged result |
| FREE | 2 blocks overlapping dates | binary/constraint 0 for pair, overlap allowed |
| SEPARATE | both G true | valid solution 중 한 serial order, overlap 거부 |
| I_OUTER | one-way fixture | before/after/nested 세 mode만; equal entry nested 거부 |
| K_OUTER | swapped | symmetric truth table |
| secondary | same optimal tardiness의 extended vs tight dwell | tight dwell 선택 |
| warm start | checker-feasible snapshot | all a/e/mode Starts set, itself feasible |
| component boundary | affected subset와 external non-FREE neighbor | boundary dates fixed 또는 neighbor component 포함 |
| max 80 | 81-node component | documented critical subcomponent/fallback, free vars<=80 |

## 16. 단계 통합 테스트

- `test_enumerated_two_block_optimum`: small integer horizon에서 모든 dates/modes 전수열거 optimum과 Gurobi primary/secondary 일치.
- `test_interlock_witness`: 설계 §17의 real shape data가 현재 checkout에 있을 때 outer/inner fixture; 없으면 동일 relation synthetic fixture를 필수 실행하고 real witness는 9단계 data gate로 기록. nested retime은 checker PASS, conservative 대비 Z1 개선.
- `test_retime_constructed_example`: 4단계 snapshot retime, fixed bay/orient/x/y, checker Stage 5, Z1 비증가, total 비증가.
- `test_timeout_and_license_rollback`: fake timeout/license. serialized result가 input과 동일, checker PASS.
- `test_equal_exit_topological_serialization`: nested equal exit을 만들도록 fixture; blocker-first operations와 Stage 5 PASS.

## 17. 테스트 실행 명령

```bash
cd baseline
/opt/homebrew/Caskroom/miniforge/base/envs/ogc-2026/bin/python -m unittest tests.test_retime tests.test_retime_integration -v
/opt/homebrew/Caskroom/miniforge/base/envs/ogc-2026/bin/python -m unittest discover -s tests -p 'test_*.py' -v
```

## 18. 테스트별 입력·예상 기록

각 solve에 block/component 수, relation state/mode counts, variables/constraints, horizon, status, primary, bound/gap, secondary, runtime, Z1 before/after, checker stage를 기록한다. timeout test는 feasible incumbent 유무를 구분한다.

## 19. 완료 조건

- four-state indicator가 exhaustive tiny oracle와 전부 일치.
- constructed example에서 checker PASS, Z1/total 비증가, fixed layout 불변.
- equal-exit topology Stage 5 PASS.
- 모든 optimizer failure rollback 및 incumbent 보존.
- 전체 1~5단계 suite green.

## 20. 다음 단계 제공 인터페이스

6단계는 `retime(snapshot,affected_ids)`를 optional polishing hook으로 사용한다. hook 실패/없음에도 heuristic LNS iteration은 완결되어야 한다. `RetimingResult` 외 Gurobi object는 노출하지 않는다.

## 21. 진행 문서 업데이트

truth-table case 수, example before/after, bound/gap/time, equal-exit result, failure matrix를 기록한다. real witness data가 없으면 substitute와 차단을 명시한다.

## 22. 위험과 대응

- horizon 오류: serial witness test로 모든 bay feasibility 증명.
- indicator mode 누락: pure enumeration parity.
- secondary가 primary 훼손: integer best primary equality 고정 후 재최적화.
- large component: 80 cap, critical subset, unchanged boundary fixed.
- TIME_LIMIT solution 신뢰: internal predicate+serializer+full checker 세 gate.

## 23. 미해결 사항

- `w1=0`에서 retiming은 checker total을 개선할 수 없으므로 실행을 skip할지 layout ease용 current만 갱신할지 6단계 acceptance 정책과 함께 결정 필요. best는 교체하지 않는다.
- real `prob_4` witness가 checkout에 없어 9단계 데이터 제공 전 재현 불가.
- Gurobi multi-objective API와 two-pass 중 default는 설계대로 two-pass이나 실제 gap/time behavior 측정 후 결정 기록.

## 24. 커밋 경계

권장 메시지 `feat(solver): add exact four-state retiming`. retime과 연동 tests만 포함하고 LNS trigger/offset search는 제외한다.
