# 04. Event-aware union-safe constructor

## 1. 단계 목표

assignment seed를 complete checker-feasible spatial schedule로 변환하는 빠른 exact constructor를 구현한다. 첫 pass는 interval-overlap pair가 모두 `FREE`인 union-safe 해만 허용하고, 실패한 block은 earliest empty-bay window로 반드시 완성한다.

## 2. 설계 근거

설계 §2.1 bottleneck, §4 P3, §7 전체, §12 후보 cap/default, §13 구현 순서 4, §14.2 constructor gate.

## 3. P0~P6 대응

P3 전체. P0 relation/state/serializer, P1 incumbent, 선택적 P2 seeds를 소비한다. P4 retiming이나 P5/P6는 요구하지 않는다.

## 4. 선행 조건

1~3단계 완료. 특히 four-state kernel parity와 canonical serializer가 green이어야 한다. 3단계 portfolio는 empty여도 된다.

## 5. 현재 저장소 상태

기존 `_candidate_positions`는 모든 과거 placement의 right/top anchor를 누적하고 `_find_earliest_slot`은 `{R}∪existing exits`만 본다. insertion이 기존 block의 미래 crane operation을 무효화할 수 있어 post-hoc repair가 필요하다. 새 constructor는 `baseline_greedy.py`를 수정/호출하지 않는다.

## 6. 구현 범위

- deterministic/seeded order profile 6종, time event candidates, co-present-only four-wall/contact/vertex/lattice anchors.
- exact symmetric relation validation과 transactional insert.
- exact weighted insertion delta(`w1*tardiness+w2*ΔZ2+w3*ΔZ3`), fragmentation tie-break only.
- regret-2/3 selection, bounded escalation, earliest empty-bay completion.
- profile별 complete candidate serialize/full check 후 strict incumbent install.
- construction timing/candidate counters.

## 7. 비범위

one-way nesting/interlock 허용(8단계), Gurobi retiming(5단계), adaptive LNS(6단계), daily-40 performance 승인(9단계). constructor는 non-interlocking `FREE` overlap만 사용한다.

## 8. 변경 또는 추가할 파일

- 추가 `baseline/solver/construct.py`.
- 변경 `entry.py`, `state.py`(indexes/delta API가 1단계 계약 내 필요할 때), `geometry.py`(새 알고리즘 없이 batch query만).
- 추가 `baseline/tests/test_construct.py`, `baseline/tests/test_construct_integration.py`.

## 9. 파일별 책임

- `construct.py`: order/time/position 후보, exact validation, regret loop, fallback completion, metrics.
- `state.py`: bay interval/load indexes와 candidate draft commit/rollback; objective authoritative 계산.
- `entry.py`: budget ladder에서 최대 profile 수와 full-check/install 결정.

## 10. 인터페이스

```text
ConstructorConfig(seed=20260710,time_cap=12/32,anchor_cap=48,lattice_cap=512,profiles<=6)
ConstructionSeed(assignment_seed|None,profile,random_seed)
generate_time_candidates(block,state,guide) -> ordered tuple[int,...]
generate_position_candidates(block,bay,orient,interval,state) -> tuple[(x,y),...]
evaluate_insert(draft,placement,kernel) -> CandidateScore|None
construct_complete(instance,kernel,seed,budget,config) -> ConstructionResult
ConstructionResult(snapshot,status,metrics,complete)
```

`CandidateScore`는 total delta, tardiness delta, assignment delta, fragmentation, canonical tie tuple를 가진다. `complete=True`라도 incumbent가 아니며 serialize+full checker 후에만 install한다.

## 11. checker 불변 조건

- time overlap은 half-open. 후보 event set은 `{R_i}∪{e_k}∪{a_k-d_i}`에 guide/near dates를 더한다.
- anchor는 해당 candidate interval에 co-present인 block에서만 생성.
- fit/AABB는 filter, 최종 relation은 exact kernel. overlapping interval의 모든 same-bay pair가 `FREE`.
- 새 block entry/exit뿐 아니라 existing pair와 proposed interval의 exact temporal mode를 검사한다.
- fallback empty window는 bay 전체 interval과 disjoint하며 valid integer anchor/orientation 사용.
- full checker strict improvement만 best install; complete-but-invalid candidate는 discard.

## 12. 예외·timeout·Gurobi fallback

constructor는 pure Python이며 Gurobi seed가 없어도 동작한다. geometry exception/후보 cap/deadline은 해당 후보 포기 후 empty-window fallback으로 이어진다. global search deadline이면 현재 unplaced block을 포함해 남은 모든 block을 deterministic empty-window로 완성하고, reserve가 허용할 때 full check한다. 새 full check를 시작할 시간이 없으면 이미 validated best를 반환한다.

## 13. 세부 구현 순서

1. state bay interval/load index와 exact assignment delta를 테스트로 고정.
2. order profiles: slack/due, release/due, decreasing union-area×dwell, assignment regret, normalized preference pressure, seeded mixture.
3. event time generation/dedup/canonical cost sort, first 12/escalated 32 cap.
4. position generation: four walls, left/right/top/bottom AABB contacts, rounded vertex-edge contacts, repair current position, deterministic low-discrepancy lattice; first 48.
5. AABB/vector filter 후 exact `FREE` validation. 실패 시 최대 512 surviving lattice points.
6. 각 unplaced block best few complete candidates, maximum regret block insert.
7. 후보 없음/deadline 시 earliest empty-bay serial insert.
8. profile complete snapshot을 serialize/check/install. metrics 기록.

## 14. 하위 단계 산출물

- 4A state indexes와 time events.
- 4B anchors/exact insertion.
- 4C regret/fallback complete construction.
- 4D portfolio orchestration/full checker gate.

4A~4D는 순서대로 해당 §15 단위 케이스와 §16의 연관 통합 케이스가 green일 때만 완료한다. 특히 4C는 forced fallback complete/Stage 5, 4D는 profile full-check gate가 독립 완료 조건이다.

## 15. 단위 테스트 계획

| 케이스 | 입력 | 예상 |
|---|---|---|
| event boundaries | R=3,d=2, existing `[1,5)`,`[8,10)` | 3,5,6(`a-d`),10 및 guide; dedup/sort/cap |
| co-present anchors | 과거지만 non-overlap block과 active block | active anchors만; walls 항상 포함 |
| four contacts | rectangle around active AABB | 좌/우/상/하 reference coordinates 모두 생성 |
| negative anchor | negative local bbox | 모든 후보 integer fit range 내부 |
| symmetric validation | insertion은 entry feasible지만 existing future exit 차단 | candidate None |
| exact delta | bay load range가 max bay 변경 | full recompute와 delta `<=1e-6` |
| regret | best/second costs `[1,10]` vs `[2,3]` | 첫 block 선택 |
| empty window | unsorted intervals와 R | earliest non-overlap entry, duration d |
| deterministic mixture | 같은 seed/config | placements/metrics 동일 |
| transaction | exact check 중 exception | state/index/load 전부 원복 |

## 16. 단계 통합 테스트

- `test_construct_example_without_master`: tracked 10-block example, no Gurobi seed. `complete`, serializer/full checker Stage 5, block count 10, objective parity.
- `test_construct_from_each_profile`: 각 deterministic profile complete/Stage 5. randomized는 동일 seed reproducible.
- `test_master_failure_still_constructs`: 3단계 empty/error portfolio로 같은 pure-Python path가 complete.
- `test_forced_escalation_and_fallback`: caps를 0/작게 하여 empty-window path 강제; Stage 5 PASS.
- `test_constructor_does_not_install_invalid`: checker fake가 candidate를 거부하면 initial safe best identity 유지.

## 17. 테스트 실행 명령

```bash
cd baseline
/opt/homebrew/Caskroom/miniforge/base/envs/ogc-2026/bin/python -m unittest tests.test_construct tests.test_construct_integration -v
/opt/homebrew/Caskroom/miniforge/base/envs/ogc-2026/bin/python -m unittest discover -s tests -p 'test_*.py' -v
```

## 18. 테스트별 입력·예상 기록

profile, seed, time/anchor/lattice cap, candidates attempted/rejected by fit/AABB/relation, fallback count, complete flag, checker stage, objective components, construction wall time를 기록한다. 단계 테스트는 40-instance benchmark를 요구하지 않으며 performance 합격을 주장하지 않는다.

## 19. 완료 조건

- tracked example에서 master 유무와 모든 profile이 complete Stage 5.
- symmetric future-operation 회귀 테스트 green.
- 모든 overlapping pair가 kernel FREE임을 integration에서 재검사.
- invalid/exception candidate가 best를 손상하지 않음.
- 전체 1~4단계 suite green. P5/P6 submission flag는 여전히 off.

## 20. 다음 단계 제공 인터페이스

5단계에 checker-feasible fixed `(bay,orient,x,y)` snapshot, relation graph, metrics를 제공한다. retimer가 없어도 constructor는 독립 완료된다.

## 21. 진행 문서 업데이트

profile별 example 결과, fallback 횟수, Stage/objective parity, construction time(참고치로만), full-check install 여부를 기록한다. daily-40 activation gate는 “미실행” 유지한다.

## 22. 위험과 대응

- anchor 폭발: co-present only와 hard cap/escalation.
- filter false negative: low-discrepancy 후 bounded exhaustive row/grid survivor scan; Shapely만 authority.
- regret cost stale: state version을 candidate에 기록하고 commit 직전 재평가.
- deadline 중 incomplete: 남은 block serial fallback; check reserve 부족 시 기존 best 반환.
- seed geometry congestion: seed는 guide일 뿐 bay alternative 허용 여부를 config/profile에 명시.

## 23. 미해결 사항

- 설계의 “rounded polygon vertex-to-edge contacts” 정확한 rounding/tie 규칙은 checker integer placement에 맞춰 floor/ceil 양쪽 생성으로 제안하나 구현 전 결정 기록 필요.
- assignment seed를 hard bay 고정할지 soft guide로 둘지 profile별 정책을 작은 exhaustive fixture 후 확정해야 한다. constructor 완결성 때문에 최소 한 profile은 soft여야 한다.
- daily-40 부재로 p90/max gate는 9단계까지 미해결.

## 24. 커밋 경계

권장 메시지 `feat(solver): add event-aware exact constructor`. pure-Python constructor, 연동, tests만 포함. retimer/LNS를 섞지 않는다.
