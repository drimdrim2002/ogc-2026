# 2단계 — MIP 작동성 복구와 조기 판정

## 목표

MIP을 단순히 flag로 켜는 것이 아니라, hard-10에서 실제 호출·모델 해결·candidate 추출·retime·checker install까지 이어지는지 계측하고 기본 활성화 후보로 계속 가져갈지 조기에 결정한다.

## 근거

- submission default는 `mip_enabled=False`다.
- 기존 dense 비교에서 candidate MIP은 heuristic 대비 9W/1T/8L로 혼합 결과였다.
- Gurobi import/license/size/timeout 실패가 fallback으로 흡수되므로 “예외가 없다”는 사실만으로 MIP이 작동했다고 볼 수 없다.

## 단계의 두 결과

이 단계는 반드시 MIP ON으로 끝나지 않는다.

- **KEEP FOR ACTIVATION**: 작동성과 60초 경쟁 효과가 확인됨. 7단계 최종 활성화 후보로 유지한다.
- **DEFAULT OFF**: 호출은 복구했지만 비용 대비 개선이 없거나 회귀한다. 원인과 재현 증거를 남기고 이후 기본 경로에서는 끈다.

## 작업 범위

- `baseline/solver/repair_mip.py`
- `baseline/solver/alns.py`의 repair engine 선택/계측
- `baseline/solver/entry.py`의 benchmark/production flag 경계
- MIP transaction 및 fallback tests

constructor, heuristic repair 의미론, interlock은 변경하지 않는다.

## 필수 telemetry

- MIP dispatch count
- backend import/license/model-size/timeout/failure count와 원인
- candidate rows/product, variables, constraints, cuts
- model build/solve/extract time
- SolCount/status/gap
- feasible extraction, retime success, checker pass, strict install 수
- MIP당 objective delta와 new-best
- heuristic fallback count와 시간

## 진행 절차

1. 1단계 cumulative baseline과 hard-10 manifest를 확인한다.
2. MIP engine이 periodic/stall 조건에서 실제 선택되는 unit/integration witness를 만든다.
3. 실패 원인을 하나의 `mip_fallback`으로 뭉개지 않고 분류해 trace에 기록한다.
4. restricted/no-license/model-too-large/time-limit/feasible-solution 경로를 모두 테스트한다.
5. MIP candidate가 transactional retime → canonical serialization → full checker → strict install 순서를 지키는지 재검증한다.
6. hard-10 60초 paired benchmark에서는 heuristic-only와 MIP candidate를 같은 seed로 비교한다.
7. MIP OFF pure-Python 경로가 이전 cumulative baseline과 동일한지 확인한다.

## 조기 유지 조건

- 공통 hard blocker 전부 통과
- hard-10에서 MIP dispatch가 실제 발생
- 적어도 일부 instance에서 checker-valid strict improvement 발생
- Win > Loss이고 median paired relative delta < 0, 또는 명확한 후속 최적화 근거가 있는 near-neutral 결과
- MIP 사용 때문에 iteration 처리량이 붕괴하거나 timeout이 발생하지 않음

기준을 만족하지 못하면 단계 자체는 실패가 아니라 `DEFAULT OFF` 결정으로 완료할 수 있다.

## 결과 기록

MIP 상태별 횟수, 시간, 개선 수, hard-10 60초 결과, KEEP/OFF 결정과 7단계 재검토 조건을 추가한다.

### 2026-07-15 — 작동성 복구 및 hard-10 조기 판정

#### 기준선, 범위, 환경

- 직전 승인 cumulative baseline은 1단계 commit `cf74817d2d4d0c9ce90e4d655277097e1ce87737`이다. baseline은 `heuristic_lns`, candidate는 benchmark 전용 `candidate_mip`이며 submission `SubmissionConfig.from_defaults().mip_enabled`는 계속 `False`다.
- baseline/candidate config SHA-256은 각각 `b02608da6524b8651a3ffccdb78e7c6a985404b7740e7207c5337f3caa1a27d3` / `9d465a20bb46ca4c80184b43e3fadee3b87fceb9a8933d1fbb3cd5718cbdb3c8`이다. 계측 변경 solver/tests 결합 식별자는 `55ca846eb26d8132d12831b1fb72faf0c7f5a9029132b4b1dd267a171bec73bf`이다.
- hard-10 manifest / dataset SHA-256은 `5499cf993f5018dceca0464dfcaf0c328e129cb7ed4946c79cc4e1860127d3b7` / `c093fb98b17c714ea5b9c8c829d4bce24dd378f0b558196ce0961feb68b34e1f`이다. membership과 순서는 `prob_38, 23, 40, 25, 27, 39, 21, 33, 28, 24`를 유지했다.
- 환경은 macOS 26.5.2 arm64, Python 3.12.13, Shapely 2.1.2, Gurobi 13.0.2 restricted non-production license(2027-11-29 만료), CPU 10 logical cores다.
- `baseline/utils.py` / `baseline/baseline_greedy.py` SHA-256은 `d45aaeafdce8bf80d59d097f655c43313a4951bed43b6628e3b1cf62d4876a94` / `8ec2cc816b35b6507a9407bc9f893140a9d1b5e0892af92a2dbac2f91b32103b`로 유지했다. constructor와 interlock 파일은 변경하지 않았다.

#### 구현 및 MIP OFF parity

- `RepairResult`에 optional structured telemetry를 추가하고 `repair_mip.py`가 candidate rows/count/product, variables/constraints/cuts, build/solve/extract 시간, status/SolCount/gap, extraction과 fallback 원인을 보존하게 했다.
- import, license, size-limited model, memory, backend timeout/model failure를 분류하고, no-solution `TIME_LIMIT`, infeasible/unbounded, cut stall/cap, local reject, timebox를 별도 fallback reason으로 기록한다. 모든 실패는 동일 destroyed set의 heuristic repair로 한 번만 fallback한다.
- ALNS invocation은 MIP dispatch부터 transactional retime, canonical full checker, acceptance, strict install까지 bounded event로 남긴다. telemetry는 candidate 선택/acceptance/objective/placement를 변경하지 않는다.
- MIP OFF 경로는 1단계와 같은 config hash를 사용하며 repair engine tuple이 pure-Python heuristic 하나뿐인 계약을 유지했다. seeded actual LNS telemetry ON/OFF byte-identity 테스트, default flag/optional import packaging 테스트가 통과했다.

#### 테스트

- 전용 MIP unit/integration: `20/20 PASS`. periodic/stall dispatch witness, tiny enumeration optimum, late conflict cut, incumbent start/cap, feasible/no-solution TIME_LIMIT, import/no-license/size-limit/model failure, retime error, checker reject, strict install을 포함한다.
- telemetry/default/packaging 포함 focused suite: `28/28 PASS`.
- 전체 `baseline/tests`: `187/187 PASS` (`python -m unittest discover -s tests -p 'test_*.py' -v`, exit 0).
- 실제 backend smoke는 restricted license에서 model status `OPTIMAL`, `SolCount=1`을 확인했다.

#### hard-10 60초 paired 실행

기본 seed `20260710`에서 W-L 차이가 1이고 median이 0이어서 플레이북에 따라 `20260711`, `20260712`를 추가했다. 각 seed와 instance에서 baseline 다음 candidate를 연속 실행했다. 총 30 pairs, 60 runs다.

```bash
for seed in 20260710 20260711 20260712; do
  for instance in prob_38.json prob_23.json prob_40.json prob_25.json prob_27.json prob_39.json prob_21.json prob_33.json prob_28.json prob_24.json; do
    python experiments/ogc_sage/benchmark_ogc_sage.py run --gate feature --variant heuristic_lns --budgets 60 --seeds "$seed" --instances "$instance" --source-commit cf74817d2d4d0c9ce90e4d655277097e1ce87737 --allow-dirty
    python experiments/ogc_sage/benchmark_ogc_sage.py run --gate feature --variant candidate_mip --budgets 60 --seeds "$seed" --instances "$instance" --source-commit cf74817d2d4d0c9ce90e4d655277097e1ce87737 --allow-dirty
  done
done
```

| Seed | W/T/L | Paired median | Mean | p90 | Worst |
|---:|---:|---:|---:|---:|---:|
| 20260710 | 4/3/3 | 0.0000% | +7.9992% | +35.5208% | +56.0966% |
| 20260711 | 3/0/7 | +2.2707% | +8.2327% | +32.7603% | +41.2170% |
| 20260712 | 4/0/6 | +0.7003% | +4.2271% | +16.3039% | +37.4216% |
| **전체** | **11/3/16** | **+0.2655%** | **+6.8197%** | **+33.6533%** | **+56.0966%** |

양수 relative delta는 MIP candidate 회귀다.

| Instance | 3-seed W/T/L | Median delta | Worst delta |
|---|---:|---:|---:|
| `prob_38.json` | 1/1/1 | 0.0000% | +0.5257% |
| `prob_23.json` | 1/0/2 | +13.9575% | +41.2170% |
| `prob_40.json` | 1/1/1 | 0.0000% | +1.4536% |
| `prob_25.json` | 2/0/1 | -10.5730% | +37.4216% |
| `prob_27.json` | 0/1/2 | +0.0053% | +5.9803% |
| `prob_39.json` | 2/0/1 | -3.4202% | +4.0157% |
| `prob_21.json` | 1/0/2 | +14.8291% | +33.2346% |
| `prob_33.json` | 2/0/1 | -5.8560% | +0.6988% |
| `prob_28.json` | 0/0/3 | +23.0060% | +56.0966% |
| `prob_24.json` | 1/0/2 | +17.1527% | +31.8207% |

- Borda baseline/candidate는 `19/14`다.
- weighted component improvement(candidate가 좋을 때 양수) 평균은 `w1*ΔZ1 -5,930,568.43`, `w2*ΔZ2 -199.13`, `w3*ΔZ3 -22,277.13`으로 세 component 모두 회귀했다.
- hard blocker는 모두 통과했다. 60/60 Stage 5, exception/outer timeout/checker failure/trace regression/retime Z1 regression `0`, objective parity maximum `0.0`이다.
- elapsed median은 baseline/candidate `57.141s / 57.139s`다. LNS iterations는 `844 → 775`(-8.18%)였고 repair phase 합계는 `1225.031s → 1223.650s`로 거의 같았다. 따라서 repair seconds/iteration은 약 `1.452s → 1.579s`(+8.8%)로 악화됐다.

#### 실제 MIP 작동성 및 비용

- dispatch `75`; 실제 backend solve가 시작된 dispatch `52`; backend `optimize()` `961`회; 모든 반환 status는 `OPTIMAL`이었다. `SolCount` 최대는 6이었다.
- feasible extraction `43`; transactional retime 성공 `38`, retime status 실패 `5`; canonical full checker pass `38/38`; accepted `38`; checker-valid strict install `30`이다. checker reject는 없었다.
- fallback은 `32/75`: late-budget `TIMEBOX` 23, 64회 solve-inspect-cut 뒤 `CUT_ITERATION_CAP` 9다. 실제 run에서는 import/license/model-size failure가 없었고 전용 fake failure matrix로 각각 검증했다.
- MIP event 전체 repair wall time은 `64.584s`(run당 평균 `2.153s`)다. 그 안에서 candidate generation `32.678s`, unchanged prefilter `8.860s`, model build/solve/extract `0.593/0.598/0.075s`, heuristic fallback `19.896s`였다. 즉 Gurobi 자체보다 candidate 생성과 cut-cap 뒤 fallback이 주 비용이다.
- median candidate rows/count/product는 `4/128/128`, 최대 variables/constraints는 `163/376`이다. 9개 dispatch가 `OPTIMAL` selection을 반복하고도 cut iteration cap에 도달했다.
- checker를 통과한 MIP candidate의 post-retime current-relative objective delta 합계는 `-499,494,894`이고 strict install도 30회 있었지만, 전체 탐색 trajectory와 처리량 손실 때문에 최종 paired 결과는 11W/16L 및 평균 +6.82% 회귀였다. local improvement가 fixed-time final improvement로 이어지지 않았다.

#### artifact

- root: `artifacts/ogc_sage/step9/performance/phase2/phase2-paired-hard10-20260715`
- 60개 `raw.jsonl`의 경로순 `(SHA-256 + path)` 목록을 다시 SHA-256한 raw-set digest: `38b58da32c4861825757731e6be2e177ba26897cf272847d93b306f1f5377a4f`
- 30-scenario comparison: `paired-summary-3seeds.json`, SHA-256 `6fd23472e8bdaefb16635ca1608a20b9f8195b03edc65b85edc56c1ae007691b`

#### 판정

**DEFAULT OFF**

MIP은 end-to-end로 실제 작동하고 strict new-best도 만든다. 그러나 유지 조건인 Win > Loss와 음의 median delta를 모두 실패했고, Borda와 세 weighted component가 회귀했으며 `prob_28` 등 중대한 반복 회귀가 있다. submission/production 기본값은 변경하지 않는다.

7단계에서 재검토하려면 별도 후보가 다음을 먼저 입증해야 한다.

1. selected-only conflict separation의 중복 solve를 줄여 `CUT_ITERATION_CAP` 0에 가깝게 만들 것.
2. candidate generation/prefilter와 fallback 중복 비용을 줄여 heuristic-only iteration 처리량 parity를 회복할 것.
3. MIP dispatch를 남은 budget과 expected gain으로 제한하고, 새 hard-10 3-seed paired에서 Win > Loss, median < 0, Borda 비회귀, 중대 instance 회귀 해소를 모두 통과할 것.

이번 단계에서는 constructor/interlock/default를 변경하지 않았고 3단계를 시작하지 않았다.

## 실행 프롬프트

```text
OGC-SAGE 성능 개선 2단계 “MIP 작동성 복구와 조기 판정”만 수행한다.

작업 전에 다음 문서를 읽는다.
1. docs/implementation/sol/performance/README.md
2. docs/implementation/sol/performance/02_MIP_OPERABILITY_PROBE.md
3. docs/implementation/sol/07_CANDIDATE_SELECTION_MIP.md
4. 직전 승인 단계의 결과 기록과 HARD10_MANIFEST.json

이번 목표는 MIP을 무조건 기본 ON으로 만드는 것이 아니라 실제 end-to-end 작동성과 경쟁 효과를 판정하는 것이다.

- periodic/stall dispatch부터 strict checker install까지 계측한다.
- import/license/size/timeout/model/extraction/retime/checker 실패를 구분한다.
- MIP OFF 경로의 parity를 보존한다.
- constructor와 interlock은 변경하지 않는다.
- 전용 테스트와 전체 unittest를 실행한다.
- hard-10에서 heuristic-only baseline과 MIP candidate를 60초 paired 비교한다.
- MIP dispatch, solve, fallback, checker-pass, new-best, 소비 시간과 W/T/L을 보고한다.
- 결과에 따라 KEEP FOR ACTIVATION 또는 DEFAULT OFF를 명시한다.
- 이 단계 문서에 증거를 기록하고 3단계는 시작하지 않는다.
- 커밋/push는 별도 승인 시에만 수행한다.
```
