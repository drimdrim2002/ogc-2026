# 07. Gurobi candidate-selection repair

## 1. 단계 목표

LNS가 만든 최대 16개 destroyed block의 bounded complete candidates 중 하나씩 선택하는 multiple-choice set-packing MIP을 optional repair engine으로 추가한다. deterministic solve-inspect-add-cut loop로 exact incompatibility를 분리하고, 실패 시 6단계 heuristic repair로 즉시 되돌아간다.

## 2. 설계 근거

설계 §9.3, §12 MIP cap/default, §13 구현 순서 7, §14 optional exception/operator gate.

## 3. P0~P6 대응

P5 Gurobi repair 완성. P0 geometry/budget/state, P1 best, P4 retime, P5 heuristic shell을 소비한다. P6는 요구하지 않는다.

## 4. 선행 조건

1~6단계 완료. 특히 heuristic-only LNS가 독립 완결되고 `RepairEngine` 계약 및 rollback이 green이어야 한다.

## 5. 현재 저장소 상태

candidate-selection model/cut index가 없다. Gurobi는 optional/restricted entitlement 상태다. checker는 same-exit operation order에 민감하므로 candidate pair의 geometry/time 가능성뿐 아니라 directed exit precedence cycle도 검증해야 한다.

## 6. 구현 범위

- destroyed block당 최대 32 complete `(bay,orient,x,y,entry,exit)` candidates, product `<=512`.
- `z_ic` one-of constraints, known tardiness/preference costs, exact bay load/Qmax/Qmin.
- unchanged block incompatibility 제거/고정 cut.
- selected pair를 bay/time/AABB index→exact geometry로 inspect하고 `z_ic+z_kd<=1` cut 반복.
- same-exit directed crane arc rank constraints 또는 extraction-time cycle cut.
- incumbent candidates를 guaranteed feasible MIP start로 포함.
- 0.2~3s timebox, fallback/metrics/affected retime/full checker.

## 7. 비범위

continuous positions, candidate generation cap 초과, Shapely Gurobi callback, parallel solves, interlock-specific offset generation. model output 직접 best install 금지.

## 8. 변경 또는 추가할 파일

- 추가 `baseline/solver/repair_mip.py`.
- 변경 `baseline/solver/alns.py`(engine portfolio), `neighborhoods.py`(complete candidate export), `entry.py`(flag; submission off).
- 추가 `baseline/tests/test_repair_mip.py`, `baseline/tests/test_repair_mip_integration.py`.

## 9. 파일별 책임

- `repair_mip.py`: cap, candidate canonicalization, model, conflict loop, extraction/status.
- `neighborhoods.py`: geometry-independent bounded candidates와 incumbent candidate 보장.
- `alns.py`: periodic/stall selection, heuristic fallback, retime/check/install transaction.

## 10. 인터페이스

```text
CompleteCandidate(block_id,bay_id,orient_idx,x,y,entry,exit,cost_parts,is_incumbent)
MipRepairConfig(max_blocks=16,max_per_block=32,max_product=512,timebox=(.2,3),...)
repair_with_mip(current,destroyed,candidates,context,budget,backend_factory) -> RepairResult
ConflictIndex.build(candidates,unchanged_state)
inspect_selection(selection,kernel) -> conflicts,exit_cycles
```

한 block당 exactly one `z_ic`; candidates의 assignment로 `Q_j=u_j*(unchanged_load_j+sum L_i z_ic)`; objective는 exact candidate tardiness/preference + `w2(Qmax-Qmin)`. incumbent candidate set은 model-feasible해야 하며 Start=1이다.

## 11. checker 불변 조건

- candidate 자체가 valid integer fit/release/dwell.
- unchanged block 및 selected candidate pair의 exact temporal relation allowed.
- same-bay same-date exit graph acyclic, simultaneous entries pairwise FREE.
- extracted snapshot은 local validation→affected retime→canonical serialization→full checker 순서.
- exact weighted objective/checker parity `<=1e-6`; strict better만 best install.

## 12. 예외·timeout·Gurobi fallback

Gurobi import/license/size/model/numeric/timeout/no feasible extraction/cut iteration cap은 동일 destroyed set의 heuristic repair를 호출한다. feasible TIME_LIMIT selection도 exact inspect/full check 전 신뢰하지 않는다. model timebox는 remaining과 checker reserve 안에서 0.2~3s; product/cap 위반은 model을 만들지 않는다. public exception 0.

## 13. 세부 구현 순서

1. candidate canonical tuple/dedup/cost parity와 incumbent inclusion.
2. hard cap enforcement: destroyed>16 또는 product>512이면 heuristic dispatch.
3. unchanged conflicts를 prefilter/exact check하여 candidate 제거 또는 `z=0`.
4. base multiple-choice/load objective와 MIP start.
5. solve→selected pairs inspect→conflict cuts 추가→remaining time solve 반복; callback 사용 안 함.
6. exit DAG cycle이면 선택된 조합을 배제하는 no-good/cycle cut.
7. exact feasible selection extraction, transaction apply, affected retime/full check.
8. ALNS periodic/stall engine selection과 metrics.

## 14. 하위 단계 산출물

- 7A bounded candidate/model objective.
- 7B deterministic conflict/cycle separation.
- 7C heuristic fallback/ALNS/retime/check integration.

7A는 cap/objective, 7B는 enumeration/cut convergence, 7C는 failure matrix와 transaction integration이 각각 green일 때만 완료한다.

## 15. 단위 테스트 계획

| 케이스 | 입력 | 예상 |
|---|---|---|
| cap blocks | 17 destroyed | backend 미호출, heuristic dispatch |
| cap product | 16×33 | trim 또는 dispatch 후 product<=512; policy 결정된 결과 |
| incumbent inclusion | generated candidates에 current 누락 | current candidate 자동 추가, Start feasible |
| duplicate | 같은 6-tuple 여러 개 | canonical 1개, incumbent flag 보존 |
| unchanged cut | candidate와 fixed block SEPARATE overlap | candidate z=0 |
| pair cut | selected pair exact incompatible | `z_ic+z_kd<=1`, 재solve |
| AABB skip | 다른 bay/time/box | Shapely exact 호출 없음, cut 없음 |
| exact Z2 | selection이 max/min bay 교체 | checker-equivalent objective |
| exit cycle | selected same-exit arcs cycle | selection reject/no-good, serializer 미호출 |
| MIP start | incumbent selection | 모든 one-of/loads/cuts 만족 |

## 16. 단계 통합 테스트

- `test_mip_matches_candidate_enumeration`: 3 blocks×3 candidates tiny set을 전수열거한 exact best와 solve-inspect-cut 결과 일치.
- `test_late_conflict_cut_converges`: cheap incompatible pair가 최초 선택되고 cut 후 feasible next best 선택; cut count=1+, checker Stage 5.
- `test_timeout_uses_feasible_or_heuristic`: incumbent 있는/없는 TIME_LIMIT 두 경우 모두 final Stage 5, best 보존.
- `test_import_license_model_failure`: 세 fake failures마다 heuristic engine 호출 1회, exception 없음.
- `test_mip_result_retime_check_transaction`: selected layout 적용 후 retime exception 또는 checker reject 시 original current/best 불변.

## 17. 테스트 실행 명령

```bash
cd baseline
/opt/homebrew/Caskroom/miniforge/base/envs/ogc-2026/bin/python -m unittest tests.test_repair_mip tests.test_repair_mip_integration -v
/opt/homebrew/Caskroom/miniforge/base/envs/ogc-2026/bin/python -m unittest discover -s tests -p 'test_*.py' -v
```

## 18. 테스트별 입력·예상 기록

destroyed/candidate counts before-after trim, product, vars/constraints, solve/cut iteration, prefilter/exact checks, conflict/cycle cuts, status/SolCount/gap/time, selected ids, heuristic fallback reason, retime/check result를 기록한다.

## 19. 완료 조건

- cap이 모든 path에서 hard enforcement.
- tiny enumeration optimum과 일치하고 conflicts/cycles 0인 extraction만 반환.
- failure matrix가 heuristic fallback Stage 5.
- transaction reject가 current/best를 변경하지 않음.
- 전체 1~7 suite green, submission feature flag off.

## 20. 다음 단계 제공 인터페이스

8단계는 일반 `CompleteCandidate`/repair engine을 재사용할 수 있으나 interlock offset 탐색을 MIP module에 넣지 않는다. P6가 없어도 7단계 완료 가능하다.

## 21. 진행 문서 업데이트

cap/failure/enumeration/cut 결과, actual backend status, operator metrics를 기록한다. daily-40 usefulness와 activation은 미실행 유지한다.

## 22. 위험과 대응

- Python conflict 폭발: 512 product, indexes, iterative selected-only exact checks.
- callback thread/Shapely 안전성: callback 금지.
- incumbent start infeasible: 생성 즉시 local exact assertion, 아니면 model skip.
- load objective 누락: unchanged load 포함 exact formula parity.
- optimizer result semantic drift: extraction 후 exact relation/DAG/full checker.

## 23. 미해결 사항

- product 초과 시 per-block proportional trim 대 전체 heuristic dispatch 중 정책은 incumbent+상위 cost 후보 보존을 전제로 tiny/full data ablation 후 결정한다.
- same-exit rank constraints를 base model에 전부 넣을지 cycle cut만 쓸지는 model size 측정 후 결정; correctness gate는 동일하다.
- restricted license에서 512 candidates model 허용 여부는 9단계 entitlement gate.

## 24. 커밋 경계

권장 메시지 `feat(solver): add bounded candidate repair mip`. MIP repair/engine integration/tests만 포함하며 interlock/packaging을 섞지 않는다.
