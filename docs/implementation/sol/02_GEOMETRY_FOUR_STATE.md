# 02. Geometry preprocessing과 four-state parity

## 1. 단계 목표

1단계 orientation raw geometry를 checker와 동일한 Shapely 의미로 전처리하고, fixed placement pair를 `FREE`, `I_OUTER`, `K_OUTER`, `SEPARATE` 중 정확히 하나로 판정한다. serializer와 이후 constructor/retimer가 공유할 유일한 relation kernel을 제공한다.

## 2. 설계 근거

설계 §3 four-state theorem/obstruction, §4 P0, §7.4 symmetric validation, §11 serialization, §12 cache default, §13 구현 순서 2, §14.1 parity.

## 3. P0~P6 대응

- P0 geometry kernel 완성.
- P1 serializer의 real exit precedence 및 simultaneous-entry validation 연결.
- P3~P6가 relation API를 소비하지만 이 단계는 그 알고리즘을 구현하지 않는다.

## 4. 선행 조건

1단계 완료: immutable `Instance`, placement/state, serializer provider protocol, checker fixtures가 green이어야 한다.

## 5. 현재 저장소 상태

현재 `baseline/utils.py`는 polygon 생성 시 invalid geometry를 `buffer(0)`로 repair하고, AABB strict overlap 후 `intersection.area>0`만 collision으로 본다. `check_entry/check_exit`는 mover layer `k`와 stationary layer `j>=k`를 비교한다. 기존 `baseline_greedy.py`는 pair relation/suffix union/cache가 없고 삽입된 block의 양방향 미래 연산을 보장하지 않는다.

## 6. 구현 범위

- layer polygon repair, layer/full/per-layer AABB, all-layer union footprint, suffix unions.
- integer translation을 적용한 exact `G(mover|stationary)`.
- symmetric pair state와 exact temporal-mode predicate.
- `(block_i,orient_i,block_k,orient_k,dx,dy)` key의 bounded LRU(`2^18`).
- serializer `ExitPrecedenceProvider` 구현 및 same-bay simultaneous entry FREE 검증.
- checker oracle 기반 deterministic/random parity tests.

## 7. 비범위

anchor/time 후보 생성, Gurobi constraints, retiming, interlock 탐색, approximate geometry authority. cross-block shape deduplication도 설계 관측상 이득이 없어 하지 않는다.

## 8. 변경 또는 추가할 파일

- 추가 `baseline/solver/geometry.py`.
- 필요 최소 변경 `baseline/solver/serialize.py`, `baseline/solver/__init__.py`.
- 추가 `baseline/tests/test_geometry.py`, `baseline/tests/test_four_state_parity.py`.
- fixture helper만 `baseline/tests/helpers.py`에 추가.

## 9. 파일별 책임

- `geometry.py`: immutable `ShapeInfo`, translated view/AABB, obstruction, pair state, temporal validity, cache stats, serializer provider.
- `serialize.py`: provider를 호출하되 geometry 구체 타입을 알지 않는다.
- parity test: checker 입력을 serializer로 만들어 authoritative result와 relation theorem을 비교한다.

## 10. 인터페이스

```text
enum PairState { FREE, I_OUTER, K_OUTER, SEPARATE }
ShapeInfo(layers, layer_aabbs, full_aabb, union, union_aabb, suffix_unions)
GeometryKernel.from_instance(instance, cache_size=2**18)
shape(block_id,orient_idx) -> ShapeInfo
fits(placement) -> bool
obstructs(mover,stationary) -> bool          # G(mover|stationary)
relation(i,k) -> PairRelation(state,g_i_k,g_k_i)
relation.allows(interval_i,interval_k) -> bool
relation.mode(interval_i,interval_k) -> FREE|I_BEFORE|K_BEFORE|I_NESTED|K_NESTED|INVALID
entry_pair_is_free(i,k) -> bool
exit_edges(exiting_ids,snapshot,bay_id) -> tuple[(before,after),...]
cache_info() -> hits,misses,maxsize,currsize
```

`relation(i,k)`는 canonical block order로 cache하되 반환 방향은 호출 order에 맞춰 `I_OUTER/K_OUTER`를 변환한다. dx/dy는 integer placement 차이이며 bay가 다르면 호출자가 즉시 FREE 처리한다.

## 11. checker 불변 조건

- polygon repair와 positive-area 판정은 checker와 동일: boundary/edge/point contact는 obstruction이 아니다.
- `G(i|k)`는 모든 mover layer `l`과 stationary suffix `U_k[l]`의 positive area intersection 중 하나가 있으면 true.
- `I_OUTER`: separation 양방향 또는 `a_i+1<=a_k && e_k<=e_i`; `K_OUTER` symmetric.
- `SEPARATE`: half-open `e_i<=a_k || e_k<=a_i`만 허용.
- `FREE`: pairwise 시간 제한 없음. 단 Stage 4 same-layer collision도 `G`의 `j=l`에 포함되므로 both false일 때만 truly FREE.
- exit edge `k->i` iff `G(i|k)=true`; cycle candidate는 serializer가 거부한다.

## 12. 예외·timeout·Gurobi fallback

이 단계는 Gurobi를 사용하지 않는다. Shapely topology exception은 approximate answer로 바꾸지 않는다. checker와 같은 repair 후에도 exact 연산이 실패하면 해당 placement/candidate를 `GeometryError`로 거부하며 validated incumbent는 유지한다. global deadline 부족 시 cache miss exact 계산을 시작하지 않고 호출 phase가 candidate를 포기한다; 기존 cached verdict는 사용 가능하다.

## 13. 세부 구현 순서

1. `ShapeInfo` 생성과 invalid/degenerate polygon 처리; 원 raw layer는 변경하지 않는다.
2. 뒤에서 앞으로 suffix union `U_l=union(P_l,U_{l+1})` 생성.
3. translated AABB prefilter와 exact suffix intersection으로 directional obstruction 구현.
4. 두 방향 bit를 enum으로 변환하고 temporal predicate 진리표 구현.
5. canonical bounded LRU와 stats 구현. cache key에 bay id는 넣지 않고 relative offset만 사용한다.
6. serializer provider와 simultaneous-entry validation 연결.
7. direct checker function 및 full solution oracle parity test를 확장한다.

## 14. 하위 단계 산출물

- 2A preprocessing: 모든 orientation `ShapeInfo`.
- 2B relation: exact obstruction/four-state/temporal modes.
- 2C integration: cache, serializer, checker parity.

2A는 suffix-naive parity, 2B는 four-state truth table, 2C는 real-provider checker parity가 각각 green일 때만 완료하며 다음 작업 묶음으로 진행한다.

## 15. 단위 테스트 계획

| 케이스 | 입력 | 검증/예상 |
|---|---|---|
| `test_suffix_union_matches_naive` | 1~4 layer synthetic polygons | 각 l에서 suffix union area/symmetric difference tolerance 0 |
| `test_boundary_contact_is_free` | 사각형 edge/corner 접촉 | both G false, `FREE` |
| `test_same_layer_overlap_separate` | identical 1-layer placement | both G true, `SEPARATE` |
| `test_i_outer` | 낮은 mover가 높은 stationary suffix와 한 방향만 overlap | `(true,false) -> I_OUTER` |
| `test_k_outer` | block order swap | `(false,true) -> K_OUTER` |
| `test_all_four_temporal_modes` | 각 state에 before/after/nested/equal-entry intervals | 설계 진리표와 exact boolean 일치; one-way equal entry nesting 거부 |
| `test_cache_translation_symmetry` | 동일 orientation pair/relative offset, absolute 위치 이동 | 두 번째 hit, verdict 동일 |
| `test_cache_order_symmetry` | relation(i,k), relation(k,i) | FREE/SEPARATE 동일, OUTER enum 반전 |
| `test_cache_bound` | maxsize+N unique keys | currsize<=maxsize, old key miss |
| `test_negative_anchor_geometry` | 음수 local AABB orientation at minimum integer anchor | fits true, checker boundary PASS |

## 16. 단계 통합 테스트

- `test_relation_matches_check_entry_exit`: 추적 예제에서 deterministic seed로 fitting block/orientation/offset pair를 생성. `obstructs(i,k)`를 `check_entry(bay,[k],i)` non-empty와 비교하고 역방향도 비교; 불일치 0.
- `test_four_state_schedule_checker_parity`: 4상태마다 valid/invalid interval mode를 enumerate하여 serialize/full checker. relation이 allowed인 non-degenerate fixture는 Stage 5 PASS, invalid는 expected Stage 2/3/4/5 중 fixture에 고정된 earliest stage.
- `test_real_serializer_exit_topology`: nested same-exit fixture에서 geometry provider가 blocker-first를 생성하고 full checker Stage 5 PASS.
- random test는 고정 seed `20260710`, 최소 2,000 fitting pair-placement/time cases. failure에 seed와 full fixture JSON을 출력한다.

## 17. 테스트 실행 명령

```bash
cd baseline
/opt/homebrew/Caskroom/miniforge/base/envs/ogc-2026/bin/python -m unittest tests.test_geometry tests.test_four_state_parity -v
/opt/homebrew/Caskroom/miniforge/base/envs/ogc-2026/bin/python -m unittest discover -s tests -p 'test_*.py' -v
```

## 18. 테스트별 입력·예상 결과 기록

각 parity case에 block/orient/bay/x/y, relative offset, directional bits, enum, intervals, serializer exit order, checker stage를 기록한다. random sample 수는 실제 생성 성공 수이며 discarded non-fit 수와 섞지 않는다. 불일치 1건도 허용하지 않는다.

## 19. 완료 조건

- four-state 네 enum을 각각 direct fixture와 checker oracle fixture가 커버.
- 2,000+ deterministic pair cases에서 directional obstruction 불일치 0.
- actual geometry provider의 simultaneous exit topology가 Stage 5 PASS.
- cache bound/key symmetry tests PASS.
- 전체 1~2단계 suite green, checker/reference 파일 diff 없음.

## 20. 다음 단계 제공 인터페이스

3단계에는 `ShapeInfo.union.area`, `GeometryKernel.fits`, orientation별 bay fit mask를 제공한다. 4~8단계에는 relation과 temporal predicate를 제공한다. 이 API는 Gurobi 타입을 노출하지 않는다.

## 21. 진행 문서 업데이트

P0 geometry gate, four-state parity sample 수/seed/불일치, cache test, serializer real-provider 결과를 기록하고 2단계를 완료 처리한다. 3단계 진입을 허용한다.

## 22. 위험과 대응

- union repair가 checker layer별 의미와 달라질 위험: suffix는 acceleration일 뿐 naive `j>=l` oracle과 직접 대조.
- floating sliver: checker와 똑같이 strict `area>0`; 임의 epsilon을 넣지 않는다.
- cache orientation/order bug: canonicalization round-trip tests.
- memory: default `2^18` hard bound와 stats.
- Shapely exception 은폐: candidate reject와 diagnostic counter, incumbent 불변.

## 23. 미해결 사항

- checker가 `buffer(0)`을 쓰므로 Shapely version별 topology 결과 차이를 9단계 환경 pin/rehearsal에서 확인한다.
- random 2,000은 단계 gate이고 설계의 “thousands of two-/multi-block” 최종 gate 규모는 9단계에서 데이터/시간에 맞게 수치 확정한다.

## 24. 커밋 경계

- 권장 메시지: `feat(solver): add checker-parity four-state geometry`
- geometry와 그 테스트/serializer provider 연결만 포함.
- assignment/constructor/model 코드는 제외.
- 완료 전 전체 suite green과 진행 문서 증거가 필요하다.
