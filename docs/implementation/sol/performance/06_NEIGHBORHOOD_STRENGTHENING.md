# 6단계 — neighborhood 전역 재구성 능력 강화

## 목표

직전 단계까지 처리량을 개선한 cumulative baseline에서 destroy size와 operator 정책을 강화해 assignment·packing·schedule의 큰 구조를 재편할 수 있게 한다.

## 근거

- 기존 180초 run 120개 중 74개가 마지막 기록 기준 destroy size 4였다.
- warm-up/stall 이전에 예산이 끝나는 hard instance가 있어 큰 neighborhood가 거의 실행되지 않았다.
- 작은 이웃은 좋은 초기해의 미세 조정에는 적합하지만, 직렬화되거나 잘못 배정된 여러 block의 상호 의존을 한 번에 풀기 어렵다.

## 작업 범위

- `baseline/solver/neighborhoods.py`
- `baseline/solver/alns.py`의 destroy size/operator scheduling
- 필요 시 기존 MIP repair engine interface 사용
- operator activity, determinism, transaction tests

constructor, retime model, geometry 의미론은 변경하지 않는다.

## 검토할 해결 방향

1. block 수뿐 아니라 남은 시간과 repair throughput을 반영한 destroy size
2. stalled iterations가 아니라 stalled wall time/new-best rate 기반 확대
3. tardy precedence/relation chain을 여러 root에서 선택
4. 고혼잡 bay window와 assignment pressure를 함께 파괴
5. Z1 우선 대형 이웃과 Z2/Z3 보정 이웃을 phase별로 분리
6. small/medium/large neighborhood portfolio와 실제 성과 기반 선택

새 operator를 많이 추가하기 전에 기존 operator가 작은 destroy size 때문에 약한지부터 구분한다.

## 진행 절차

1. hard-10에서 operator별 attempts/feasible/accepted/new-best/time을 측정한다.
2. destroy size별 repair cost와 objective delta 분포를 수집한다.
3. 기존 operator를 그대로 두고 size/stall 정책만 바꾼 variant를 먼저 실험한다.
4. 부족하면 가장 큰 구조적 실패 유형 하나에 대한 새 destroy operator를 추가한다.
5. 같은 seed에서 선택 재현성, unique ids, draft rollback, local/full feasibility를 테스트한다.
6. repair/MIP이 처리할 수 없는 크기를 요청하지 않도록 budget-aware cap을 둔다.
7. 전용/통합/전체 unittest를 실행한다.
8. hard-10 60초 paired benchmark를 실행한다.

## 핵심 지표

- destroy size 분포와 growth trigger
- operator별 feasible/accepted/new-best/time
- repair failure와 iteration time
- local optimum 탈출 후 best improvement
- accepted worse move와 validated-best 분리
- W/T/L, Borda, Z1/Z2/Z3 weighted delta

## 승격 조건

- 공통 hard blocker 전부 통과
- medium/large neighborhood가 hard-10에서 실제 실행됨
- 강화된 경로가 checker-valid strict new-best를 적어도 여러 instance에서 생성
- iteration 비용 증가가 objective 개선으로 정당화됨
- Win > Loss, median paired relative delta < 0

activity만 있고 strict improvement가 없으면 기본 정책으로 승격하지 않는다.

## 롤백 조건

- repair failure 또는 timeout 급증
- current의 worse acceptance가 validated best를 오염
- operator 선택 결정성 상실
- 큰 이웃이 실제로는 같은 후보를 반복
- hard-10 worst regression이 설명 없이 커짐

## 결과 기록

size별 비용/효과, operator ablation, hard-10 60초 결과와 승격 결정을 추가한다.

### 2026-07-15 — budget-aware small/medium/large portfolio 단일 ablation

#### 기준선, 범위, 환경

- 5단계 exact Z1 skip 후보는 승격 기각되어 cumulative baseline은 production legacy retime, constructor `eager_regret`, MIP `DEFAULT OFF`, legacy destroy policy인 HEAD `8974e26c50385156387f0fc091131030713c6db2`다.
- 같은 checkout에서 benchmark-only config만 `legacy`/`portfolio`로 분리했다. config SHA-256은 legacy `571a5e2fbdc1de294daa76fde7fbbb7fe17e489477bc3c4e33213e506d4d958f`, portfolio `5047a9b52ae19080a9a9aefa5827501cd2f11c858ee9eb0473a24417e665391e`다. production default는 계속 `neighborhood_policy="legacy"`다.
- hard-10 manifest / dataset SHA-256은 `5499cf993f5018dceca0464dfcaf0c328e129cb7ed4946c79cc4e1860127d3b7` / `c093fb98b17c714ea5b9c8c829d4bce24dd378f0b558196ce0961feb68b34e1f`이며 frozen 순서 `prob_38, 23, 40, 25, 27, 39, 21, 33, 28, 24`를 변경하지 않았다.
- 환경은 macOS 26.5.2 arm64, Python 3.12.13, Shapely 2.1.2, Gurobi 13.0.2 restricted license다. `baseline/utils.py` / `baseline/baseline_greedy.py` SHA-256은 `d45aaeafdce8bf80d59d097f655c43313a4951bed43b6628e3b1cf62d4876a94` / `8ec2cc816b35b6507a9407bc9f893140a9d1b5e0892af92a2dbac2f91b32103b`로 유지했다.
- constructor, retime model, geometry 의미론과 operator 집합은 변경하지 않았다. 수정은 `alns.py`의 size 계측/정책, 필요한 config/trace plumbing, 전용 tests와 paired harness에 한정했다.

#### baseline 계측과 선택한 단일 가설

- 기존 operator 집합을 그대로 두고 size/stall 정책만 비교했다. legacy는 small `255`회와 stall-grown medium `18`회를 시도했다. small은 feasible/accepted/new-best `246/246/212`, repair `416.239s`; medium은 `12/12/0`, repair `0.049s`로 deadline 인접 fast-failure 성격이었다.
- 가설은 12-iteration deterministic cycle에서 medium을 slot 4, large를 slot 10에 희소 요청하고, 최근 32회의 median repair seconds/block으로 usable remaining time의 35%를 넘지 않게 cap하는 것이다. 목표 크기는 `small=max(4,ceil(.02n))`, `medium=max(small+2,ceil(.05n))`, `large=max(medium+2,ceil(.10n))`, hard cap `ceil(.15n)`이다.
- portfolio growth trigger는 medium `18`회(1회 budget-capped), large `15`회(5회 budget-capped), legacy stall trigger와 동일한 stall `5`회였다. 새 destroy operator는 추가하지 않았다.

#### size별 activity/cost/strict new-best

candidate의 checker-valid strict install은 medium/large 합계로 hard-10 10/10 instance에서 발생했다. 아래 new-best는 해당 destroy iteration 뒤 retime strict install도 포함하므로 attempts보다 클 수 있다.

| Level | Attempts | Feasible | Accepted | Strict new-best | Failures | Repair seconds | Iteration seconds |
|---|---:|---:|---:|---:|---:|---:|---:|
| small | 180 | 177 | 177 | 138 | 3 | 183.098 | 230.357 |
| medium | 34 | 29 | 29 | 19 | 5 | 88.802 | 93.202 |
| large | 10 | 7 | 7 | 12 | 3 | 149.858 | 157.738 |

large는 실제 실행되고 strict improvement도 만들었지만 시도당 평균 repair가 약 `14.986s`여서 small iteration 다수를 대체했다. 특히 size 25는 3회 중 1회만 feasible했고 repair `71.960s`를 소비했다. budget-aware cap은 요청 당시 남은 시간의 35%를 사용했지만 repair cost의 비선형 증가를 충분히 예측하지 못했다.

#### operator activity ablation

동일 여섯 operator를 유지한 size-policy ablation 결과다.

| Operator | Legacy attempts/feasible/accepted/new-best/time | Portfolio attempts/feasible/accepted/new-best/time |
|---|---:|---:|
| congested window | `38/37/37/19/27.411s` | `32/32/32/14/51.523s` |
| preference alternative | `19/19/19/17/32.951s` | `40/39/39/33/60.181s` |
| random-k | `57/53/53/55/96.570s` | `46/44/44/45/106.226s` |
| Shaw | `44/43/43/32/77.544s` | `33/32/32/19/47.715s` |
| tardy chain | `50/48/48/45/138.476s` | `42/41/41/38/88.085s` |
| Z2 contributor | `65/58/58/44/107.290s` | `31/25/25/20/127.567s` |

#### 테스트

- focused neighborhood/ALNS/integration/packaging/stress suite `42/42 PASS`.
- 전체 `baseline/tests`는 `/opt/homebrew/Caskroom/miniforge/base/envs/ogc-2026/bin/python -m unittest discover -s tests -p 'test_*.py' -v`로 `194/194 PASS`였다.
- deterministic medium/large schedule, budget-aware cap, size telemetry, fixed-seed replay, unique ids, repair transaction rollback, worse-current/validated-best 분리, deadline 후 추가 repair/checker 금지를 확인했다.

#### hard-10 60초 paired 결과

seed `20260710`, budget 60초에서 instance별 `legacy` 직후 `portfolio` 순서로 20회를 실행했다. 20/20 Stage 5, exception/outer timeout/checker failure/objective parity error/validated-best trace regression/retime Z1 worsen count는 모두 0이다.

| W/T/L | median / mean / p90 / worst relative delta | Borda legacy/portfolio |
|---:|---:|---:|
| **1/0/9** | **`+12.6896% / +13.5804% / +24.2943% / +33.1516%`** | **`9 / 1`** |

- 유일한 win은 `prob_24`의 `-1.0824%`였고 나머지 9개는 모두 회귀했다.
- candidate weighted component improvement 평균은 `w1*ΔZ1 -52,711,311.9`, `w2*ΔZ2 -3,105.2`, `w3*ΔZ3 -36,808.3`으로 세 component 모두 악화했다.
- median elapsed는 legacy/portfolio `57.1377/57.1856s`다. iterations `272→224`, accepted `258→213`, strict new-best `212→169`, retime triggers `120→101`, weight updates `30→23`이었다.
- LNS phase 합계 legacy/portfolio는 destroy `0.125/0.111s`, repair `416.287/421.759s`, retime `56.033/53.877s`, checker `6.403/4.527s`다. 큰 이웃이 strict improvement를 만들었어도 총 repair 시간은 늘고 전체 iteration/new-best는 감소했다.

#### artifact와 코드 hash

- 경로: `artifacts/ogc_sage/step9/phase6-paired-20260715`.
- legacy raw/summary SHA-256: `fd64f96a3852fbe2e9d540fde970dad0f6116438452390433bc6949c40d8edcc` / `d35e72f74e8c48c431c402d7dfc84c0368695aedf0df0a25577f206952fd103e`.
- portfolio raw/summary SHA-256: `53287ea430aa279794295f4612feddc24d1e3aef125c4ac243e8a973be05e985` / `ee27a1fd6f2df8c77a416378f174d77951d07519a1c943c346ee4353ca4d7d5f`.
- comparison SHA-256: `ab0eb443566955ae49178b0af41bd1ded9992b9c8a931e52b1ff13d866c9813f`.
- candidate code SHA-256: `alns.py 97792d27066d839dd926a37f2f6e5de3fae6d8f9bff793a171ffd5383731c8d4`, `runtime.py 9ea7fba055d4fbde924e7ec2ce5de9f4ddf2291cb890a71b62b6d602180cc617`, `entry.py cfdc6b1b89f4eea571cbc34e90f488797bd78af1c489245546f76f7e823c4e41`, paired harness `0e22a312d0f76fb2a8bb66b9b4b38c5fed8b89302ca7203f89181e6baac9200e`.

#### 판정과 rollback

**승격 기각 / production legacy neighborhood 유지**

공통 hard blocker, determinism/transaction/validated-best 불변식, medium/large 실제 activity, 여러 instance의 checker-valid strict new-best는 통과했다. 그러나 `1W/9L`, median `+12.6896%`, Borda `9/1`, 세 weighted component 악화로 핵심 품질 조건을 모두 실패했다. iteration 비용 증가가 objective 개선으로 정당화되지 않는다.

따라서 `SubmissionConfig.neighborhood_policy="legacy"`를 production default로 유지한다. portfolio는 benchmark-only 재현 경로와 계측으로만 남기며 기본 정책으로 승격하지 않는다. 재검토 시에는 seconds/block 선형 cap이 아니라 size/operator별 비선형 cost model과 large 1회당 hard time cap을 별도 단일 가설로 검증해야 한다. 이번 단계에서는 새 operator 또는 두 번째 정책 patch를 시도하지 않았고 7단계를 시작하지 않는다.

## 실행 프롬프트

```text
OGC-SAGE 성능 개선 6단계 “neighborhood 전역 재구성 능력 강화”만 수행한다.

먼저 다음 문서를 읽는다.
1. docs/implementation/sol/performance/README.md
2. docs/implementation/sol/performance/06_NEIGHBORHOOD_STRENGTHENING.md
3. docs/implementation/sol/06_HEURISTIC_LNS.md
4. HARD10_MANIFEST.json과 직전 cumulative baseline 결과

- operator와 destroy size별 activity/cost/new-best를 먼저 계측한다.
- 먼저 size/stall 정책만 바꾸고, 부족할 때만 새 operator 하나를 추가한다.
- constructor, retime model, geometry 의미론은 변경하지 않는다.
- budget-aware cap, deterministic replay, transaction, validated-best 불변식을 테스트한다.
- 전용/통합/전체 unittest 후 hard-10 60초 paired benchmark를 실행한다.
- 큰 이웃의 activity가 아니라 checker-valid strict improvement로 판정한다.
- 결과를 이 문서에 기록하고 7단계는 시작하지 않는다.
- 커밋/push는 별도 승인 시에만 수행한다.
```
