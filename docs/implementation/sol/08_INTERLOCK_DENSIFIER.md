# 08. Gated interlock densifier

## 1. 단계 목표

union-safe search가 stalled이고 tardiness와 spatial saturation이 남은 경우에만 one-way `I_OUTER/K_OUTER` placement offsets를 찾아 exact retimer가 nesting 또는 separation을 선택하게 한다. 효과와 안전 gate가 증명되기 전 submission 기본은 off다.

## 2. 설계 근거

설계 §3 theorem, §4 P6, §10, §12 interlock activation/default budget, §13 구현 순서 8/enable gate, §14.2.

## 3. P0~P6 대응

P6 전체. P0 four-state, P4 retimer, P5 neighborhood/state를 소비한다. 이후 9단계 hardening 외 기능 선행 의존성은 없다.

## 4. 선행 조건

1~7단계 완료. 2단계 one-way parity, 5단계 nesting retimer, 6단계 stall/metrics가 green이어야 한다. 7단계 MIP engine은 선택적이며 densifier 독립 완료를 위해 필수로 사용하지 않는다.

## 5. 현재 저장소 상태

interlock 탐색은 없다. 설계는 random overlap sample 약 4%와 real witness를 제시하지만 optimized incumbent의 효과를 보장하지 않는다. real witness training file은 현재 checkout에 없다.

## 6. 구현 범위

- activation predicate: tardiness>0, union-energy pressure>=0.45, union-safe stall, deadline reserve, feature gate.
- saturated bay/time window와 tardy movers/extendable hosts 선택.
- bounded integer offsets에서 exact one-way state 후보 생성.
- candidate를 transactional layout draft에 적용하고 exact retimer로 separation/nesting 결정.
- full checker strict improvement install, gate reason/attempt/improvement metrics.
- TL의 최대 8% budget.

## 7. 비범위

initial constructor에서 interlock, two-way SEPARATE를 interlock으로 부르는 것, approximate nesting acceptance, gate 없는 default 실행, full lattice exhaustive global search.

## 8. 변경 또는 추가할 파일

- `baseline/solver/neighborhoods.py`에 명확히 분리된 interlock candidate generator를 추가하거나, 책임이 커지면 `baseline/solver/interlock.py` 신설. 설계 layout에 없는 새 파일이므로 후자를 택할 경우 진행 문서 D-record에 이유를 남긴다.
- 변경 `baseline/solver/alns.py`, `entry.py`(gate/off default).
- 추가 `baseline/tests/test_interlock.py`, `baseline/tests/test_interlock_integration.py`.

## 9. 파일별 책임

- generator: saturated window, mover/host, integer offsets, one-way exact state.
- `alns.py`: stall 시 한정 호출과 transaction.
- `retime.py`: 기존 API 그대로 사용하며 interlock search를 모른다.
- `entry.py`: submission/config global enable gate.

## 10. 인터페이스

```text
InterlockGate(enabled,tardiness,pressure,stalled,remaining,reserve) -> GateDecision(run,reason)
InterlockCandidate(mover,host,placement,relation,window,score_hint)
generate_interlock_candidates(snapshot,context,budget,config) -> iterator
densify(snapshot,best_store,context,budget,retime_hook) -> DensifyResult
```

pressure는 설계와 같은 union-area energy/bay-time capacity 정의를 사용하고 stage 3 congestion guide surrogate와 혼동하지 않는다. host는 dwell extension이 due tardiness를 만들지 않거나 weighted improvement upper bound가 양수인 순으로 우선한다.

## 11. checker 불변 조건

- accepted geometry relation은 exact `I_OUTER/K_OUTER`; FREE는 일반 LNS, SEPARATE는 interlock gain 아님.
- layout 변경 후 모든 affected pair exact relation allowed.
- retimer가 실제 nested mode를 선택하지 않았더라도 feasible improvement면 일반 layout move로 metric을 분리 기록; “interlock improvement”은 one-way+nested selected인 경우만 집계.
- simultaneous one-way entry 금지, same-exit DAG cycle 거부.
- full checker strict total improvement만 best install.

## 12. 예외·timeout·Gurobi fallback

retimer/Gurobi 실패는 candidate rollback 후 다음 bounded 후보 또는 종료. gate false/deadline 부족/feature off는 input identity 반환. densifier budget은 `min(.08*TL, remaining-reserve)`이며 각 exact geometry/retime/check 전에 확인한다. exception은 metrics에 reason을 남기고 validated best 반환.

## 13. 세부 구현 순서

1. pure activation predicate와 union pressure 계산 테스트.
2. stalled window/critical mover/host ranking.
3. wall/contact/current-near offsets를 integer fit/AABB로 cap하고 exact relation one-way만 유지.
4. score upper-bound로 hopeless candidate 제거하되 final acceptance authority로 쓰지 않음.
5. layout draft apply→affected relation validation→retime.
6. retimer mode/result에서 actual nested use 확인.
7. serialize/full checker/objective parity/strict install.
8. gate-off default, metrics, 8% budget.

## 14. 하위 단계 산출물

- 8A activation/pressure/window.
- 8B one-way offset generator.
- 8C retime/check/install/metrics integration.

8A는 모든 gate branch, 8B는 four-state filter, 8C는 witness/rollback/full-check 테스트가 각각 green일 때만 완료한다.

## 15. 단위 테스트 계획

| 케이스 | 입력 | 예상 |
|---|---|---|
| gate off | enabled false | run false, 다른 조건 평가 결과와 무관 |
| no tardiness | Z1=0 | false `NO_TARDINESS` |
| low pressure | .449999 | false; .45 true when others true |
| no stall | recent best | false |
| reserve | remaining<=reserve | false |
| budget | TL=100 | densifier cap<=8s |
| relation filter | FREE/I/K/SEPARATE offsets | I/K만 candidates |
| integer fit | negative AABB/edge anchor | valid range 밖 후보 0 |
| host ranking | extension slack 0/positive | no-new-tardy host 우선 |
| transaction | retime exception | snapshot unchanged |

## 16. 단계 통합 테스트

- `test_gate_off_bit_identity`: enabled false에서 call counter 0, serialized input/output 동일.
- `test_synthetic_one_way_witness`: outer `[0,10)`, inner target `[2,7)` geometry를 synthetic one-way fixture로 구성. conservative separation minimum tardiness 7, nested candidate checker Stage 5 and tardiness 0.
- `test_real_prob4_witness_when_data_available`: 설계 §17 ids/orient/positions와 expected 7→0; data 없으면 9단계 blocked gate로 남긴다.
- `test_nonbeneficial_interlock_not_installed`: one-way 후보이나 weighted total equal/worse; best identity.
- `test_retimer_license_failure`: rollback/Stage 5 incumbent.
- `test_exit_cycle_rejected`: constructed multi-block same-exit cycle candidate는 full check 전에 discard.

## 17. 테스트 실행 명령

```bash
cd baseline
/opt/homebrew/Caskroom/miniforge/base/envs/ogc-2026/bin/python -m unittest tests.test_interlock tests.test_interlock_integration -v
/opt/homebrew/Caskroom/miniforge/base/envs/ogc-2026/bin/python -m unittest discover -s tests -p 'test_*.py' -v
```

## 18. 테스트별 입력·예상 기록

gate inputs/reason, pressure/window, candidates by relation, geometry/retime time, mode selected, Z1/total before-after, checker stage, rollback reason을 기록한다. synthetic와 real witness를 명확히 구분한다.

## 19. 완료 조건

- 모든 activation branch와 budget cap green.
- synthetic witness가 exact one-way+nested+Stage 5와 expected improvement를 보임.
- gate off identity/failure rollback/strict install green.
- 전체 1~8 suite green.
- submission default는 off; daily-40 dense subset gate 전 enable 금지.

## 20. 다음 단계 제공 인터페이스

9단계에 `GateDecision`, `DensifyMetrics`, feature flag와 dense-subset evaluation hook을 제공한다. packaging은 algorithm을 변경하지 않고 gate config만 확정한다.

## 21. 진행 문서 업데이트

synthetic/real witness 구분, gate branch, mode/improvement, failures를 기록한다. real data와 dense subset 검증 전 interlock usefulness/enable gate는 미완료 유지한다.

## 22. 위험과 대응

- rare opportunity로 시간 낭비: strict activation, 8% budget, attempt/yield metrics.
- false interlock claim: actual retimer nested mode 별도 집계.
- exit order cycle: serializer DAG precheck.
- host extension tardiness: exact retime/full weighted objective.
- feature creep: bounded offsets만, initial constructor 변경 없음.

## 23. 미해결 사항

- predefined dense subset 구성은 daily-40 데이터 제공 후 pressure와 설계 family를 기준으로 stage 9에서 확정.
- 별도 `interlock.py` 신설 여부는 `neighborhoods.py` 책임 크기를 구현 시 검토해 D-record 필요.
- real witness 파일 부재로 최종 activation 증거는 현재 차단.

## 24. 커밋 경계

권장 메시지 `feat(solver): add gated interlock densifier`. gate/generator/integration/tests만 포함. packaging/stress 결과나 default enable 변경은 9단계로 둔다.
