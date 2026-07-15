# 3단계 — constructor 직렬 fallback 감소

## 목표

constructor가 대부분의 block을 `earliest_empty_window()`에 직렬 배치하는 현상을 줄이고, LNS가 시작하기 전에 공간·시간을 함께 활용하는 경쟁력 있는 checker-valid 초기해를 만든다.

## 근거

- 기존 240-run에서 constructor fallback 비율 중앙값은 약 97.5%다.
- constructor profile 1,440개가 모두 50,000 candidate cap에 도달했다.
- fallback은 빈 bay window의 anchor 위치를 사용하므로 공간 동시 사용을 포기하고 tardiness를 키운다.
- 높은 `w1` 인스턴스에서는 이 초기 Z1 손실을 작은 LNS 이웃으로 회복하기 어렵다.

## 작업 범위

- `baseline/solver/construct.py`
- `baseline/solver/state.py`의 constructor 조회/index hot path에 필요한 최소 변경
- `baseline/solver/geometry.py`의 기존 exact interface 사용
- constructor tests와 benchmark telemetry

ALNS acceptance, MIP, retime 모델은 변경하지 않는다.

## 먼저 규명할 원인

fallback을 하나의 숫자로 보지 말고 다음 이유로 분리한다.

- candidate cap 도달 후 safe tail
- timebox/deadline reserve
- 모든 후보 fit 실패
- relation/crane feasibility 거절
- transactional commit 실패
- candidate generation 중 예외
- profile/order 때문에 후보가 과도하게 늦게 발견됨

## 검토할 해결 방향

1. 모든 unplaced block을 매 반복마다 전수 평가하는 구조를 priority queue 또는 lazy regret 갱신으로 변경
2. bay/orientation/time/position 불변 후보의 재사용과 중복 제거
3. cheap conservative prefilter 뒤에만 exact relation 호출
4. EDD/slack/congestion에 따른 on-time 후보를 먼저 평가하고 candidate cap을 block/profile별로 배분
5. candidate cap 도달 시 남은 모든 block을 즉시 직렬화하지 않고 제한된 병렬 safe-tail 수행
6. assignment guide를 tie-break가 아니라 후보 우선순위에 더 효과적으로 반영하되 hard constraint로 만들지 않음

여러 방향을 한 patch에 섞지 않고 원인별 ablation을 수행한다.

## 진행 절차

1. 직전 cumulative baseline과 hard-10 manifest를 확인한다.
2. fallback reason telemetry와 후보 funnel(attempted → prefilter pass → exact pass → committed)을 추가한다.
3. hard-10의 profile별 fallback 이유와 Z1 손실을 먼저 측정한다.
4. 가장 큰 원인 하나를 선택해 최소 변경으로 개선한다.
5. fixed-work에서 같은 candidate quota와 seed로 placement/objective parity 또는 의도한 알고리즘 차이를 분리 보고한다.
6. constructor candidate는 canonical serialize/full checker를 통과한 뒤에만 incumbent가 된다.
7. 전용/통합/전체 unittest를 실행한다.
8. hard-10 baseline/candidate 60초 paired benchmark를 실행한다.

## 핵심 지표

- fallback ratio와 reason별 count
- non-fallback committed placements
- candidates attempted/second
- exact relation calls와 cache miss
- constructor elapsed
- constructor 직후 Z1/Z2/Z3/total
- 최종 60초 objective W/T/L과 time-to-first-improvement

## 승격 조건

- 공통 hard blocker 전부 통과
- fallback ratio가 hard-10 중앙값에서 실질적으로 감소하고 감소 원인이 설명됨
- constructor p90/max가 정해진 timebox와 checker reserve를 침범하지 않음
- constructor 직후 objective 또는 최종 60초 objective에서 Win > Loss
- 최종 median paired relative delta < 0

fallback 숫자만 낮아지고 최종 objective가 개선되지 않으면 기본 경로로 승격하지 않는다.

## 롤백 조건

- checker-valid 초기해 생성률 저하
- constructor가 LNS 예산을 과도하게 잠식
- exact relation 또는 serialization 의미론 우회
- 특정 profile만 좋아지고 hard-10 전체가 회귀

## 결과 기록

fallback 원인 분해, 선택한 변경, ablation, hard-10 60초 결과와 승격 결정을 추가한다.

### 2026-07-15 — fallback reason 계측 및 lazy profile-priority ablation

#### 기준선, 범위, 환경

- 직전 승인 cumulative baseline은 1단계 commit `cf74817d2d4d0c9ce90e4d655277097e1ce87737`이다. 2단계는 `DEFAULT OFF`이므로 MIP 기본값은 계속 `False`다. 실행 source metadata는 2단계 계측을 포함한 HEAD `8974e26c50385156387f0fc091131030713c6db2`를 사용했고, baseline/candidate를 같은 source에서 constructor policy만 달리해 paired 실행했다.
- baseline `heuristic_lns/eager_regret` config SHA-256은 `8be8e575990047468d45e3618620fb0a0bbcb49789ae845ca8e019b2f8a73b3c`, candidate `candidate_constructor/profile_priority`는 `582f0f1d8249ca93fa528643fc377e24d8b20f562016e21e5d520dd9d739c79f`다. 변경 solver/tests 결합 식별자는 `a67dce668f37d6d16047d110c8d0a75caca440ef1d92d148afc32194a77f5cf6`이다.
- hard-10 manifest / dataset SHA-256은 `5499cf993f5018dceca0464dfcaf0c328e129cb7ed4946c79cc4e1860127d3b7` / `c093fb98b17c714ea5b9c8c829d4bce24dd378f0b558196ce0961feb68b34e1f`이며 membership과 순서 `prob_38, 23, 40, 25, 27, 39, 21, 33, 28, 24`를 변경하지 않았다.
- 환경은 macOS 26.5.2 arm64, Python 3.12.13, Shapely 2.1.2, Gurobi 13.0.2 restricted license다.
- `baseline/utils.py` / `baseline/baseline_greedy.py` SHA-256은 `d45aaeafdce8bf80d59d097f655c43313a4951bed43b6628e3b1cf62d4876a94` / `8ec2cc816b35b6507a9407bc9f893140a9d1b5e0892af92a2dbac2f91b32103b`로 유지했다. state/geometry, ALNS acceptance, heuristic repair, MIP, interlock, retime 모델은 변경하지 않았다.

#### 원인 계측과 선택한 단일 변경

- `ConstructionMetrics`에 candidate funnel(`attempted`, fit prefilter pass, exact pass, exact relation/cache miss, commit), reason별 fallback, generation exception/commit failure, non-fallback commit, constructor 직후 Z1/Z2/Z3/total을 추가했다. canonical serializer/full checker/strict install 순서는 그대로다.
- 동작 불변 eager probe의 hard-10 60개 profile은 모두 정확히 50,000 attempt cap에 도달했다. 총 9,498 fallback 전부가 `CANDIDATE_CAP_SAFE_TAIL`이었고, fallback ratio median/min/max는 `96.0%/90.0%/98.8%`, profile당 non-fallback commit 중앙값은 6개였다. 3,000,000 attempts 중 fit prefilter pass 3,000,000, exact pass 2,091,387, exact relation call/cache miss 1,765,245/408,264였다. timebox/deadline/no-candidate/commit/exception fallback은 0이었다.
- 따라서 가장 큰 원인은 “한 state version에서 모든 unplaced block을 eager 재평가하고 한 block만 commit하여, 계산한 후보 대부분을 버린 뒤 cap safe-tail로 진입”으로 판정했다.
- 단일 candidate patch는 `profile_priority` policy다. 각 state version에서 profile 우선순위의 다음 block 하나만 lazy 평가하고 즉시 transactional commit한다. candidate 생성, quota 50,000, exact relation, fallback 배치와 checker gate는 바꾸지 않았다. 동일 100-attempt fixture에서 eager non-fallback 0, candidate 1개 이상을 재현 테스트로 고정했다.

#### fixed-work constructor-only ablation

hard-10, budget 60초, seed `20260710`, 같은 50,000 attempt/profile에서 `constructor_only/eager_regret`와 `candidate_constructor_only/profile_priority`를 비교했다.

| 지표 | Baseline | Candidate |
|---|---:|---:|
| profile runs / cap exhausted | 60 / 60 | 60 / 60 |
| fallback ratio median (min/max) | 96.0% (90.0/98.8%) | 66.0% (37.0/85.6%) |
| non-fallback commit median / total | 6 / 402 | 56 / 3,337 |
| attempted / second | 53,687 | 26,521 |
| exact relation calls / cache misses | 1,765,245 / 408,264 | 7,474,104 / 2,181,964 |
| constructor phase p90 / max | 6.410s / 6.891s | 13.184s / 13.920s |

- constructor 직후 checker-valid objective는 **10W/0T/0L**, median/p90/worst relative delta `-67.8282% / -41.7906% / -33.7351%`, Borda baseline/candidate `0/10`이다.
- weighted component improvement(candidate가 좋을 때 양수) 평균은 `w1*ΔZ1 +445,160,618.9`, `w2*ΔZ2 +720.1`, `w3*ΔZ3 +419,361.7`이다.
- fallback은 9,498→6,563으로 30.9% 감소했지만 candidate도 60/60 cap에 도달했다. spatially dense state에서 exact call이 4.23배 증가해 attempt 처리량은 50.6% 감소했다.
- 10/10 Stage 5, exception/outer timeout/checker failure 0, objective parity max `0.0`, deadline/timebox hit 0이다. 그러나 기존 constructor gate `p90 <= 8s`, `max <= 12s`는 baseline PASS, candidate FAIL이다.

#### 테스트

- 최종 focused constructor/config/benchmark contract suite: `34/34 PASS`.
- candidate 상태에서 constructor/retime/ALNS/deadline까지 포함한 focused integration: `66/66 PASS`.
- 최종 전체 `baseline/tests`: `188/188 PASS` (`/opt/homebrew/Caskroom/miniforge/base/envs/ogc-2026/bin/python -m unittest discover -s tests -p 'test_*.py' -v`, exit 0).
- 기본 `python` 3.13에는 Shapely가 없어 최초 import probe가 실패했으며, 문서가 지정한 OGC Python 3.12 환경으로 모든 공식 테스트와 benchmark를 실행했다.

#### hard-10 60초 paired 결과

기본 seed `20260710`에서 각 instance의 baseline 다음 candidate 순으로 교차 실행했다. W-L 차이는 8이고 median이 명확한 음수이므로 플레이북에 따라 추가 seed는 실행하지 않았다.

```bash
for instance in prob_38.json prob_23.json prob_40.json prob_25.json prob_27.json prob_39.json prob_21.json prob_33.json prob_28.json prob_24.json; do
  python experiments/ogc_sage/benchmark_ogc_sage.py run --gate feature --variant heuristic_lns --budgets 60 --seeds 20260710 --instances "$instance" --source-commit 8974e26c50385156387f0fc091131030713c6db2 --allow-dirty
  python experiments/ogc_sage/benchmark_ogc_sage.py run --gate feature --variant candidate_constructor --budgets 60 --seeds 20260710 --instances "$instance" --source-commit 8974e26c50385156387f0fc091131030713c6db2 --allow-dirty
done
```

| Instance | Baseline | Candidate | Relative delta | Iterations B/C |
|---|---:|---:|---:|---:|
| `prob_38.json` | 2,040,406,878 | 1,369,736,210 | -32.8695% | 23 / 11 |
| `prob_23.json` | 61,685,513 | 64,688,086 | +4.8675% | 34 / 8 |
| `prob_40.json` | 75,514,855 | 57,939,998 | -23.2734% | 15 / 15 |
| `prob_25.json` | 7,130,549 | 6,090,625 | -14.5841% | 25 / 4 |
| `prob_27.json` | 878,441,661 | 690,761,571 | -21.3651% | 25 / 12 |
| `prob_39.json` | 807,188,801 | 426,330,869 | -47.1833% | 26 / 13 |
| `prob_21.json` | 105,588,077 | 41,038,531 | -61.1334% | 29 / 25 |
| `prob_33.json` | 399,225,737 | 213,725,379 | -46.4650% | 35 / 14 |
| `prob_28.json` | 230,711,657 | 114,708,391 | -50.2806% | 37 / 16 |
| `prob_24.json` | 38,070,924 | 18,581,144 | -51.1933% | 40 / 29 |

- 최종 objective는 **9W/0T/1L**, median/mean/p90/worst relative delta `-39.6672% / -34.3480% / -14.5841% / +4.8675%`, Borda baseline/candidate `1/9`다. 단일 회귀는 `prob_23`이다.
- weighted component improvement 평균은 `w1*ΔZ1 +163,947,962.0`, `w2*ΔZ2 -4,791.8`, `w3*ΔZ3 +93,214.6`이다. Z1/Z3는 개선됐고 Z2는 회귀했다.
- elapsed median baseline/candidate는 `57.142s / 57.151s`다. constructor phase 합계는 `55.880s → 113.116s`; run별 median/p90/max는 `5.265/6.403/6.938s → 11.268/12.963/13.738s`다.
- LNS repair/retime/checker 합계는 `411.172/58.877/6.385s → 390.034/18.831/3.408s`다. iterations는 합계 `289 → 147`(-49.1%), median `27.5 → 13.5`다. attempts/feasible/accepted/new-best는 `289/267/267/214 → 147/139/137/75`다.
- 공통 hard blocker는 통과했다. 20/20 Stage 5, exception/outer timeout/checker failure/trace regression/retime Z1 regression `0`, objective parity maximum `0.0`이다. candidate constructor의 deadline/timebox hit도 0이다.

#### artifact

- root-cause baseline: `artifacts/ogc_sage/step9/phase3-root-cause-baseline-20260715`; raw / summary SHA-256 `adf2301c0987ca73684facd64396da32e7fab0c4378783c1e7812ebf18479585` / `6efe6a1545dc891619497f0a2e0639026ef9793b71e1d177c9773d274aa12edb`.
- fixed-work candidate: `artifacts/ogc_sage/step9/phase3-constructor-ablation-candidate-20260715`; raw / summary SHA-256 `5e83c03f0268649844e0323083639e2eb2722a5136d5cf74eb7ca060fc6f1fa9` / `eff1e6582c9a449ffa148309554f4d622379d6c8de4817d53143e90999b9ed1f`.
- fixed-work comparison: `artifacts/ogc_sage/step9/phase3-evidence-20260715/constructor-ablation-comparison.json`, SHA-256 `d970517976f5e026488451a77514b9419f268c6a51a6a9aece111d0e651c7775`.
- paired raw: `artifacts/ogc_sage/step9/phase3-paired-prob_{instance}-{baseline|candidate}-20260715/raw.jsonl` 20개. 경로순 `(SHA-256 + path)` 목록 digest는 `26dcd6d0b2a514a349ce8df77de19cd952b9e9163392349b1ccf26252a0447d1`이다.
- paired comparison: `artifacts/ogc_sage/step9/phase3-evidence-20260715/paired-summary-seed20260710.json`, SHA-256 `ecd1cae45a6e033a7e538534e7e996015ccd3b3db52acc82e1447936f68a4c18`.

#### 판정

**승격 기각 / production default `eager_regret` 유지**

fallback 감소와 constructor 직후/최종 objective 조건은 큰 폭으로 통과했고 공통 hard blocker도 모두 통과했다. 그러나 candidate는 고정 50,000 quota에서 exact relation call이 크게 늘어 constructor p90/max `8/12초` gate를 반복 실패하고 LNS iteration을 절반 가까이 줄였다. 최종 품질 개선만으로 명시된 constructor time gate 실패를 상쇄하지 않는다.

따라서 `SubmissionConfig.from_defaults()`와 기존 benchmark/MIP/interlock variant는 `eager_regret`을 유지한다. `profile_priority`는 `candidate_constructor(_only)` benchmark variant에서만 재현 가능하게 남겼다. rollback 의견은 **candidate를 기본 경로에 승격하지 않는 현재 상태 유지**다. 재검토하려면 별도 단일 실험에서 exact call/중복 후보를 줄이거나 profile 조기 종료로 `p90 <= 8s`, `max <= 12s`를 먼저 통과한 뒤 동일 paired matrix를 다시 실행해야 한다. 이번 단계에서는 그 두 번째 patch와 4단계를 시작하지 않았다.

## 실행 프롬프트

```text
OGC-SAGE 성능 개선 3단계 “constructor 직렬 fallback 감소”만 수행한다.

먼저 다음 문서를 읽는다.
1. docs/implementation/sol/performance/README.md
2. docs/implementation/sol/performance/03_CONSTRUCTOR_FALLBACK_REDUCTION.md
3. docs/implementation/sol/04_EXACT_CONSTRUCTOR.md
4. HARD10_MANIFEST.json과 직전 cumulative baseline 결과

- fallback reason과 candidate funnel을 먼저 계측한다.
- hard-10에서 가장 큰 fallback 원인 하나를 선택한다.
- 한 번에 하나의 constructor 개선만 구현하고 ablation한다.
- ALNS acceptance, MIP, interlock, retime 모델은 변경하지 않는다.
- canonical serializer와 공식 checker gate를 유지한다.
- 단위/통합/전체 unittest 후 hard-10 60초 paired benchmark를 실행한다.
- fallback 비율뿐 아니라 constructor 직후와 최종 objective를 모두 비교한다.
- 최종 objective가 개선되지 않으면 fallback 감소만으로 승격하지 않는다.
- 결과를 이 문서에 기록하고 4단계는 시작하지 않는다.
- 커밋/push는 별도 승인 시에만 수행한다.
```
