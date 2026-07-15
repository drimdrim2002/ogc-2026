# 5단계 — retime 호출과 모델 비용 감소

## 목표

retime의 correctness와 Z1 비증가 계약을 유지하면서 불필요한 trigger, 반복 모델 조립, 비효율적인 component 요청을 줄인다.

## 근거

- 180초 phase 시간의 약 19.5%, 중앙 약 34초가 LNS retime에 사용된다.
- repair가 빨라지면 retime이 다음 병목이 될 가능성이 높다.
- 현재 retime은 accepted change를 batch하지만 trigger 효과와 모델 build/solve 비율이 충분히 분리 계측되지 않았다.

## 작업 범위

- `baseline/solver/retime.py`
- `baseline/solver/alns.py`의 pending/trigger 정책
- 필요한 최소 범위의 request cache/index
- retime telemetry, parity, failure-isolation tests

constructor, repair candidate 의미론, destroy operator, MIP 활성화는 변경하지 않는다.

## 먼저 분리할 시간

- affected component discovery
- relation/pair preprocessing
- request construction
- Gurobi model construction
- optimize
- extraction
- local feasibility/serialization/full checker

## 검토할 해결 방향

1. 같은 affected component와 boundary를 가진 요청의 안전한 재사용
2. pending changes의 trigger batching 또는 최소 예상이득 gate
3. Z1 개선 가능성이 없는 component의 cheap exact skip
4. model template/constant coefficient 재사용 가능성
5. 작은 component는 더 낮은 cap, 큰 component는 남은 예산 기반 cap
6. accepted candidate마다 retime하지 않고 stall/threshold에서 강제하되 final pending change를 잃지 않음

Gurobi solve 자체를 Python 최적화 대상으로 오인하지 않고 model build와 native optimize를 분리한다.

## 진행 절차

1. 직전 cumulative baseline의 hard-10 retime trigger/result funnel을 계측한다.
2. trigger별 affected ids/component size, input Z1, result Z1, strict install 여부를 기록한다.
3. retime 시간이 큰데 improvement yield가 낮은 원인을 순위화한다.
4. trigger 정책 또는 model-build 개선 중 하나만 선택해 구현한다.
5. FREE/SEPARATE/I_OUTER/K_OUTER, same-exit, negative due, timeout/license/failure rollback tests를 유지한다.
6. fixed-work에서 동일 request의 dates/objective/checker parity를 검증한다.
7. 전용/통합/전체 unittest를 실행한다.
8. hard-10 60초 paired benchmark를 실행한다.

## 핵심 지표

- retime trigger와 solved/requested 비율
- model-build/optimize/checker 시간
- affected component 크기
- trigger당 Z1/total improvement
- retime seconds per strict new-best
- saved time이 repair iterations로 전환됐는지
- final W/T/L과 Z1 weighted delta

## 승격 조건

- 공통 hard blocker 전부 통과
- retime Z1 worsen count 0
- failure/timeout/license rollback 보존
- retime 총시간 또는 improvement당 시간이 실질적으로 감소
- 절약 시간이 추가 탐색 또는 더 나은 objective로 연결
- Win > Loss, median paired relative delta < 0

## 롤백 조건

- 필요한 final retime을 생략해 objective 악화
- affected boundary 또는 four-state 의미론 훼손
- component failure가 다른 component/validated incumbent를 폐기
- model cache가 instance/state를 교차 오염

## 결과 기록

시간 분해, trigger yield, 선택한 개선, fixed-work/fixed-time 결과와 승격 결정을 추가한다.

### 2026-07-15 — exact Z1 lower-bound component skip 단일 ablation

#### 기준선, 범위, 환경

- 4단계 `fits_precomputed` 후보는 hard-10 중대 회귀로 승격되지 않았다. 따라서 cumulative baseline은 production legacy `GeometryKernel.fits()`, constructor `eager_regret`, MIP `DEFAULT OFF`, retime legacy solve 상태의 HEAD `8974e26c50385156387f0fc091131030713c6db2`다.
- 같은 checkout에서 benchmark-only config만 legacy/exact로 분리했다. config SHA-256은 legacy `12efb325e4b61c842c14aa806dcddf64097d3e111aa4e0bdd0d85cdebeebe5cc`, exact `b5d5d079611f45c8a6e309db52befdf0bf9925888ad3630adc666432b188bc9f`다.
- hard-10 manifest / dataset SHA-256은 `5499cf993f5018dceca0464dfcaf0c328e129cb7ed4946c79cc4e1860127d3b7` / `c093fb98b17c714ea5b9c8c829d4bce24dd378f0b558196ce0961feb68b34e1f`이며 frozen 순서 `prob_38, 23, 40, 25, 27, 39, 21, 33, 28, 24`를 변경하지 않았다.
- 환경은 macOS 26.5.2 arm64, Python 3.12.13, Shapely 2.1.2, Gurobi 13.0.2 restricted license다. `baseline/utils.py` / `baseline/baseline_greedy.py` SHA-256은 `d45aaeafdce8bf80d59d097f655c43313a4951bed43b6628e3b1cf62d4876a94` / `8ec2cc816b35b6507a9407bc9f893140a9d1b5e0892af92a2dbac2f91b32103b`로 유지했다.
- 수정 범위는 `retime.py`의 phase/result telemetry와 exact skip, `alns.py`/`runtime.py`의 trigger event telemetry, 최소 config plumbing, 전용 tests/benchmark harness다. repair/constructor/destroy/MIP/interlock 의미론은 변경하지 않았다.

#### baseline trigger/result funnel과 시간 분해

코드 의미론을 바꾸지 않는 telemetry를 먼저 추가하고 seed `20260710` hard-10 60초 baseline 10회를 측정했다.

- 10/10 Stage 5, trigger/event `118`, Z1 개선 및 strict install `40`(33.9%), 무개선 `78`이었다. status는 `PARTIAL 40`, `OPTIMAL 1`, `NO_IMPROVEMENT 59`, `ERROR 14`, `BUDGET 4`였다. 내부 `ERROR/BUDGET`은 component-local rollback으로 격리됐고 run exception/checker failure는 0이었다.
- request component `502`, backend solution 보유 component `275`; component size min/median/p90/max `1/3/69/99`, free size `1/3/69/80`, pair count `0/2/1,980/3,485`였다.
- retime 합계 `54.066s` 중 affected discovery `0.567s`, relation/pair preprocessing `0.436s`, request construction `0.016s`, model construction `4.524s`, native optimize `46.543s`, extraction `0.010s`, local feasibility `0.038s`, serialization `0.035s`, full checker `1.272s`였다. 측정 phase 합계 기준 optimize가 87.1%, model build가 8.5%여서 Python model-build 개선 대신 exact skip 하나만 선택했다.
- baseline profile raw / summary SHA-256은 `7a2834f0dd8aaceacca4f044159001ccd1863e19d21b1d5d1f73b69d859ac42b` / `456d3374cca6fe2e5420e73452b02720dd289f3553a26b87141c55b05eb39f31`이다. 경로는 `artifacts/ogc_sage/performance/phase0/phase5-profile-baseline-20260715`다.

#### 선택한 단일 patch와 fixed-work parity

- free block마다 현재 tardiness가 독립 이론 하한 `max(0, release+dwell-due)`와 같고 `exit-entry==dwell`이면 primary Z1과 secondary dwell extension이 모두 더 좋아질 수 없다. 이 조건을 만족하는 component는 Gurobi model을 만들지 않고 identity-equivalent `EXACT_Z1_SKIP`으로 반환한다.
- 조건에는 negative due를 포함하며, dwell slack이 있으면 skip하지 않는다. four-state relation, same-exit serializer, checker, timeout/license/failure rollback은 그대로다.
- synthetic legacy/exact fixed-work는 동일 dates/objective를 확인했고, hard-10 safe snapshot identity-backend fixture는 10/10 placement/objective/canonical serialization/Stage 5 parity였다. hard-10 fixture의 legacy/exact backend call은 `28/28`이고 이 초기 snapshot에서는 skip 대상이 0이었다. summary SHA-256은 `f2b984e64e8582df80814f995dcb0d4d12e1b2e3305bcc36793ee7a752cfbb29`다.
- candidate `retime.py` SHA-256은 `cc17179e67a758ae4f03f31c5a1c95ffaae1937e5c2107924d75d3feb2654ebb`다. production default는 최종 판정 후 legacy `exact_z1_skip=False`로 롤백했다.

#### 테스트

- focused retime/ALNS/integration/telemetry/contract/packaging suite `63/63 PASS`.
- 전체 `baseline/tests`는 `/opt/homebrew/Caskroom/miniforge/base/envs/ogc-2026/bin/python -m unittest discover -s tests -p 'test_*.py' -v`로 `191/191 PASS`였다.
- FREE/SEPARATE/I_OUTER/K_OUTER, equal-exit topology, negative due, TIME_LIMIT feasible incumbent, import/license/numeric/failure identity rollback을 모두 유지했다.

#### hard-10 60초 paired 결과

기본 seed `20260710`의 median이 정확히 0이라 불명확 판정 규칙에 따라 `20260711`, `20260712`를 추가했다. 각 seed에서 instance별 legacy 직후 exact 순서로 실행했다. 60/60 run 모두 Stage 5이며 exception/outer timeout/checker failure/objective parity/trace regression/retime Z1 worsen count는 0이다.

| Seed | W/T/L | median / mean / p90 / worst relative delta | Borda legacy/exact |
|---:|---:|---:|---:|
| `20260710` | 1/5/4 | `0.0000% / +10.0267% / +44.9156% / +48.0344%` | `9 / 6` |
| `20260711` | 1/8/1 | `0.0000% / +0.9326% / 0.0000% / +19.5522%` | `9 / 9` |
| `20260712` | 2/6/2 | `0.0000% / -3.3512% / +1.6547% / +7.6015%` | `8 / 8` |
| 합계 30 pairs | **4/19/7** | **`0.0000% / +2.5360% / +19.5522% / +48.0344%`** | **`26 / 23`** |

- candidate weighted component improvement 평균은 `w1*ΔZ1 -1,955,845.5`, `w2*ΔZ2 -108.3`, `w3*ΔZ3 -6,469.0`이다.
- median elapsed는 legacy/exact `57.1409/57.1463s`다. iterations `856→880`, accepted `840→864`, new-best `649→660`, retime triggers `402→406`으로 탐색량은 늘었다.
- candidate는 `654` component를 exact skip했지만 retime 총시간은 `195.169→197.664s`로 1.28% 증가했다. phase 합계 legacy/exact는 discovery `1.716/1.707s`, pair preprocessing `1.343/1.449s`, request `0.047/0.047s`, model build `14.023/13.884s`, optimize `171.748/174.389s`, extraction `0.032/0.020s`, local feasibility `0.116/0.047s`, serialization `0.113/0.114s`, checker `4.108/4.120s`다. skip 절약이 추가 trigger/trajectory의 native optimize 증가로 상쇄됐다.
- retime events legacy/exact `402/406`, Z1-improved `139/142`, strict install `135/136`이었다. 추가 iteration/new-best가 최종 objective 개선으로 연결되지 않았다.

#### artifact

- seed `20260710` legacy raw/summary, exact raw/summary, comparison SHA-256: `970ae1b00bdfc76d91683dfd014a478975c5e42c5917e7e146b778186c321821` / `39f4b5773b32924f17c929810508e3374af713012d87216144416bc6ec427cdf` / `0904f43077f3e622e84afe9900fa86f286be813c89857bd8a43078586adcabe0` / `ade0197c617146d74b35c2c60c5938927fc15d9ac5c3b952aa174563cdf5b595` / `09d922e8d3673e657f840072f1d4b546e65930914b8170f8a371fd1ce7d026e8`.
- seed `20260711` legacy raw/summary, exact raw/summary, comparison SHA-256: `6dd5226b7638fa2f927d979f6ebde0672c488330ef41897f708d8202c5144866` / `660722cc3a9c3e65e6098cf765bae09c9ce42adc10fa2bdd0a2098bb69d0f793` / `a9d7704510f5ae46dfc77e5a98fd460ea607cf93fa8ad91b560b2fdda58edf47` / `4be247102ce7a9a4d2cd2bfeabe1fc5bb8c3b078564fbf9f16439c4588246c4e` / `f54e70cc5ae9d90fcd5f4fb1c102b0592de613c2d3113e3bc979044a4cf68508`.
- seed `20260712` legacy raw/summary, exact raw/summary, comparison SHA-256: `f175828a1fe527950abc30f95bccbcff5b242d820064fb773cc2f79d762c0614` / `764caf0dabb2460bb0c6af0e7491d6253e098dc94b50e8cc5dad1cf511a7b3be` / `f79e87a9b0d42a4b4769fd3b0c84ea8a9db1dc903268e26b76b011219e189ba7` / `093a9da9c3ab859728a24991e4637f13396f8d69a278009f9847fbf00b98ce00` / `6b7e9937aa103610f5a3f62df942ee649a9bd99534303c1d70417d12d64934a3`.
- 경로는 각각 `artifacts/ogc_sage/step9/phase5-paired-20260715`, `phase5-paired-seed20260711-20260715`, `phase5-paired-seed20260712-20260715`이다. summary hash는 각 디렉터리의 `legacy/summary.json`, `exact/summary.json`과 `comparison.json`에 대응해 별도 보존했다.

#### 판정과 rollback

**승격 기각 / production legacy retime 유지**

공통 hard blocker, Z1 비증가, failure isolation, fixed-work parity는 통과했다. 그러나 3-seed 합계가 `4W/19T/7L`, median `0`, Borda `26/23`이고 weighted Z1/Z2/Z3도 모두 악화했다. retime 총시간도 감소하지 않아 승격 조건의 “retime 시간 실질 감소”, “Win > Loss”, “median < 0”, “Borda 비열화 금지”를 모두 통과하지 못했다.

따라서 `SubmissionConfig.retime_exact_z1_skip=False`를 production default로 롤백했다. exact skip은 benchmark-only config와 전용 테스트로만 재현 가능하게 남겼다. 다음 재검토는 skip 자체가 아니라 trigger batching과 component cap이 deadline-sensitive trajectory에 미치는 영향을 별도 단계/단일 patch로 검증해야 하며, 이번 단계에서는 두 번째 성능 patch와 6단계를 시작하지 않았다.

## 실행 프롬프트

```text
OGC-SAGE 성능 개선 5단계 “retime 호출과 모델 비용 감소”만 수행한다.

먼저 다음 문서를 읽는다.
1. docs/implementation/sol/performance/README.md
2. docs/implementation/sol/performance/05_RETIME_COST_REDUCTION.md
3. docs/implementation/sol/05_EXACT_RETIMING.md
4. HARD10_MANIFEST.json과 직전 cumulative baseline 결과

- retime 시간을 preprocessing/request/model-build/optimize/extract/checker로 먼저 분리한다.
- trigger yield와 component별 Z1 개선을 계측한다.
- trigger 정책 또는 model-build 개선 중 하나만 수정한다.
- repair/constructor/destroy/MIP 활성화는 변경하지 않는다.
- four-state, same-exit, negative due, timeout/license/failure rollback 테스트를 유지한다.
- fixed-work parity 후 전체 unittest와 hard-10 60초 paired benchmark를 실행한다.
- 절약된 시간이 추가 iteration과 objective 개선으로 연결됐는지 보고한다.
- 결과를 이 문서에 기록하고 6단계는 시작하지 않는다.
- 커밋/push는 별도 승인 시에만 수행한다.
```
