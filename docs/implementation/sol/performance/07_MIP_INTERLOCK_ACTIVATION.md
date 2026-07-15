# 7단계 — MIP/interlock 기본 활성화 최종 결정

## 목표

모든 선행 개선이 반영된 cumulative baseline에서 heuristic-only, MIP, interlock, combined variant를 공정하게 비교하고 최종 제출 기본 구성을 동결한다.

## 전제

- 2단계 MIP 결과가 `KEEP FOR ACTIVATION`이면 MIP variant를 포함한다.
- 2단계가 `DEFAULT OFF`라도 이후 repair/neighborhood 개선으로 실패 원인이 제거됐다는 구체적 증거가 있을 때만 한 번 재검토한다.
- interlock은 synthetic witness가 아니라 hard-10 실제 trigger/strict improvement가 있어야 활성화할 수 있다.

## 작업 범위

- `baseline/solver/runtime.py`의 최종 default flags
- `baseline/solver/entry.py`의 variant wiring
- `baseline/solver/repair_mip.py`, `interlock.py`는 활성화 blocker 수정에 필요한 최소 범위
- benchmark variant/config hash와 activation tests

새 constructor/repair/retime/neighborhood 알고리즘을 이 단계에서 추가하지 않는다.

## 비교 variant

- `heuristic_only`: cumulative baseline, MIP OFF, interlock OFF
- `mip`: MIP ON, interlock OFF
- `interlock`: MIP 정책은 baseline과 동일, interlock ON
- `combined`: MIP ON, interlock ON

2단계에서 MIP이 기각됐고 재검토 근거가 없으면 `mip`과 `combined`를 생략하고 그 이유를 기록한다.

## 진행 절차

1. 선행 단계의 승인 commit/config와 MIP KEEP/OFF 결정을 확인한다.
2. variant가 환경변수나 제출 외부 상태가 아니라 명시적 immutable config로만 달라지는지 확인한다.
3. hard-10 60초, seed `20260710`으로 모든 허용 variant를 비교한다.
4. 가장 좋은 두 variant의 W-L 차이가 2 이하이거나 결과가 혼합이면 두 추가 seed를 실행한다.
5. MIP dispatch/strict install과 interlock trigger/candidate/install이 실제로 발생했는지 확인한다.
6. feature가 inactive인 instance에서는 heuristic baseline과 불필요한 overhead가 없는지 확인한다.
7. 모든 failure injection과 packaging/import tests를 실행한다.
8. 최종 default flags를 하나로 동결하고 config hash를 기록한다.

## 판정 지표

- variant별 W/T/L, Borda, median/p90/worst gap
- weighted Z1/Z2/Z3 변화
- anytime integral과 time-to-first-improvement
- MIP dispatch/solve/checker-install/new-best
- interlock trigger/candidates/checker-install/new-best
- feature별 wall time과 fallback

## 활성화 조건

- 공통 hard blocker 전부 통과
- feature가 hard-10에서 실제 activity와 strict improvement를 보임
- heuristic-only 대비 Win > Loss와 median relative delta < 0
- worst regression과 추가 시간 비용이 명시적으로 수용 가능
- import/license/backend 실패 시 heuristic safe path 보존

최적 variant가 heuristic-only면 MIP/interlock을 OFF로 동결하는 것이 정상적인 완료다.

## 결과 기록

실행한 variant, 생략 사유, 모든 비교 지표, 최종 default flags/config hash와 결정 근거를 추가한다.

### 2026-07-15 — heuristic-only 대 interlock 최종 활성화 판정

#### 기준선, 허용 variant, 단일 patch

- 직전 6단계 portfolio 후보는 승격 기각되어 cumulative baseline은 HEAD `8974e26c50385156387f0fc091131030713c6db2`, constructor `eager_regret`, production legacy retime, neighborhood `legacy`, MIP/interlock OFF다.
- 2단계 MIP 결론은 `DEFAULT OFF`다. 재검토 선행 조건인 selected-only conflict 중복 solve 제거, heuristic-only iteration 처리량 parity 회복, budget/expected-gain dispatch의 새 3-seed 증거가 3~6단계에서 만들어지지 않았다. 따라서 `mip`과 `combined`는 문서 계약에 따라 생략했다.
- 실행 variant는 `heuristic_only`에 해당하는 기존 benchmark 이름 `heuristic_lns`와 `interlock` 두 개다. 기존 `interlock` benchmark wiring이 MIP까지 함께 켜던 것을 `mip_enabled=False, interlock_enabled=True`로 분리하고 packaging activation assertion을 추가한 것이 이번 단계의 유일한 patch다. 새 constructor/repair/retime/neighborhood 알고리즘은 추가하지 않았다.
- production `SubmissionConfig.from_defaults()`는 실험 전후 모두 `mip_enabled=False`, `interlock_enabled=False`, `neighborhood_policy="legacy"`다.

#### 환경과 immutable evidence

- 환경은 macOS 26.5.2 arm64, Python 3.12.13, Shapely 2.1.2, Gurobi 13.0.2 restricted license다.
- frozen hard-10 manifest / dataset SHA-256은 `5499cf993f5018dceca0464dfcaf0c328e129cb7ed4946c79cc4e1860127d3b7` / `c093fb98b17c714ea5b9c8c829d4bce24dd378f0b558196ce0961feb68b34e1f`이며 순서 `prob_38, 23, 40, 25, 27, 39, 21, 33, 28, 24`를 변경하지 않았다.
- `baseline/utils.py` / `baseline/baseline_greedy.py` SHA-256은 `d45aaeafdce8bf80d59d097f655c43313a4951bed43b6628e3b1cf62d4876a94` / `8ec2cc816b35b6507a9407bc9f893140a9d1b5e0892af92a2dbac2f91b32103b`로 유지했다.
- benchmark config SHA-256은 heuristic-only `571a5e2fbdc1de294daa76fde7fbbb7fe17e489477bc3c4e33213e506d4d958f`, interlock-only `02186e2896206a3e5cfd164aae856df5f888550d0e588b71bded9f2bb4d33944`다.

#### activation/failure/packaging tests

- interlock, MIP fallback, packaging/import, exception matrix focused suite는 `44/44 PASS`다. gate branch, 8% cap, exact one-way filter, synthetic/real witness, non-beneficial reject, retimer license failure rollback, exit-cycle reject, MIP import/license/size/model failure fallback, clean archive optional-import path를 포함한다.
- 전체 `baseline/tests`는 `/opt/homebrew/Caskroom/miniforge/base/envs/ogc-2026/bin/python -m unittest discover -s tests -p 'test_*.py' -v`로 `194/194 PASS`였다.
- production default의 environment independence/immutability, interlock OFF identity, candidate의 MIP OFF wiring을 별도 assertion으로 확인했다.

#### hard-10 60초 paired 결과

seed `20260710`은 `2W/6T/2L`로 혼합이고 W-L 차이가 0이어서 규약대로 `20260711`, `20260712`를 추가했다. 매 seed와 instance에서 heuristic-only 직후 interlock-only를 실행한 총 30 pairs, 60 runs다. 양수 relative delta는 interlock 회귀다.

| Seed | W/T/L | median / mean / p90 / worst relative delta |
|---:|---:|---:|
| 20260710 | 2/6/2 | `0.0000% / -0.8014% / +1.6089% / +1.7871%` |
| 20260711 | 3/5/2 | `0.0000% / +2.2669% / +10.8025% / +21.8532%` |
| 20260712 | 1/5/4 | `0.0000% / +3.9274% / +11.2059% / +31.5174%` |
| **전체** | **6/16/8** | **`0.0000% / +1.7976% / +10.8025% / +31.5174%`** |

- Borda heuristic-only/interlock은 `24/22`다. best paired delta는 `-7.5439%`였지만 worst는 `+31.5174%`이고 Win > Loss 및 음의 median 조건을 모두 실패했다.
- interlock candidate weighted component improvement 평균(양수가 개선)은 `w1*ΔZ1 -436,681.53`, `w2*ΔZ2 -654.40`, `w3*ΔZ3 -550.23`으로 세 component 모두 악화했다.
- anytime primal integral 평균은 heuristic/interlock `2.472494/2.469394`, time-to-first-improvement 중앙값은 `7.4183/7.4916s`다. anytime integral의 소폭 개선은 최종 품질, activity, strict-install gate 실패를 상쇄하지 않는다.
- 60/60 Stage 5이며 exception, outer timeout, objective parity error, validated-best trace regression은 모두 0이다. median elapsed는 `57.139369/57.139364s`다.

#### activity, overhead, phase와 처리량

- MIP event는 두 variant 모두 `0`; interlock-only가 실제로 MIP을 호출하지 않았음을 확인했다.
- interlock trigger `16`, gate reason은 `RUN 13`, `LOW_PRESSURE 2`, `DEADLINE_RESERVE 1`이다. 실제 interlock candidates `5`, checker-install `0`, strict interlock new-best `0`이다. 즉 hard-10 activity는 있었지만 활성화의 필수 조건인 strict improvement는 없었다.
- LNS iterations는 `862→819`, operator accepted `845→803`, operator strict new-best 집계는 `652→652`다.
- 합산 LNS phase heuristic/interlock은 destroy `0.401/0.418s`, repair `1222.196/1220.365s`, retime `193.195/197.237s`, checker `18.769/18.877s`다. initial retime은 `29.685/29.812s`, constructor는 `163.824/163.477s`다.
- 현재 v1 run trace는 `DensifyMetrics`의 geometry/retime/checker 시간을 별도 직렬화하지 않는다. 따라서 feature wall time의 보수적 대용으로 LNS elapsed에서 계측 phase를 뺀 feature-inclusive residual을 보고한다: heuristic/interlock `4.795/4.904s`, 30 runs 합계 차이 `+0.109s`. trigger가 없던 14 scenarios는 `1W/10T/3L`, median delta `0%`; gate-off identity unit test에서는 densifier call 0이다.
- runtime exception/fallback은 없었다. failure injection에서는 retimer/backend/import/license 실패가 모두 validated heuristic incumbent로 rollback되고 Stage 5를 보존했다.

#### artifact와 hash

- artifact root: `artifacts/ogc_sage/step9/phase7-paired-20260715` 및 sibling per-instance paired run directories.
- 60개 `raw.jsonl`의 경로순 `(SHA-256 + path)` 목록 digest: `5e6ae70a5fff4f4fcfba3768f97a3408f8036247a85a84cce89d947b88c158f8`.
- 3-seed comparison `phase7-paired-20260715-comparison-3seeds.json` SHA-256: `19b77217dbee6a7a64e7cbe12e7721bfa151a04c587906b10d17b2af404b713e`.
- seed별 comparison SHA-256은 `20260710 f1aa7f6ce1f0d60b249e2d450e31afcd5ba244bb7bdd9602cf0884e69dd709b5`, `20260711 c5b7b1c85aca8fb9847d5d4ae10236732cec98c7a77221ce9750892bc8458545`, `20260712 5c15a9d4d1c89197d6ac338798153ccdfea3507aefbe5ba16b6aaf6d57627137`다.
- candidate code SHA-256은 `runtime.py 9fdd8dd891c799cb6e9f2d28e35097c467b408ed76381d8bebae16d2dc70d23e`, `entry.py cfdc6b1b89f4eea571cbc34e90f488797bd78af1c489245546f76f7e823c4e41`, `interlock.py 763cd25bf63510b971f237033cefbdeb7f51cd0cc251f53c875d262f780d187f`, activation test `test_packaging.py c63e1150325658cf84dfe943ab87d7cdd76379cef586fde31567084de280803a`, harness `benchmark_ogc_sage.py a68625bc65fa17f85487d5b99e28c4e083012da462dabdb0322942f3e9e46367`다.

#### 최종 default와 rollback 의견

**MIP OFF / interlock OFF 동결 — activation 기각**

최종 production flags는 `mip_enabled=False`, `interlock_enabled=False`, constructor `eager_regret`, neighborhood `legacy`, exact-Z1-skip OFF다. canonical runtime payload SHA-256은 `0bf8180c517c1cb02c9814dc063dc1456613e8c97a59102cf68bb57d996cfba0`, 제출과 같은 benchmark config SHA-256은 `571a5e2fbdc1de294daa76fde7fbbb7fe17e489477bc3c4e33213e506d4d958f`다.

공통 hard blocker와 fallback safety는 통과했지만 interlock은 strict install 0, `6W/8L`, median `0%`, Borda 하락, 세 weighted component 악화로 활성화 조건을 실패했다. 따라서 production에는 되돌릴 ON 변경이 없고 현재 OFF default를 유지하는 것이 rollback 결정이다. benchmark-only interlock/MIP 분리 wiring은 올바른 재현 경계이며 production 동작에 영향이 없어 유지할 수 있다. 단계 결과를 코드 diff 없이 보존해야 하는 정책이면 `runtime.py`의 benchmark tuple과 해당 packaging assertion 두 줄만 안전하게 되돌릴 수 있다. 8단계는 시작하지 않았다.

## 실행 프롬프트

```text
OGC-SAGE 성능 개선 7단계 “MIP/interlock 기본 활성화 최종 결정”만 수행한다.

먼저 다음 문서를 읽는다.
1. docs/implementation/sol/performance/README.md
2. docs/implementation/sol/performance/02_MIP_OPERABILITY_PROBE.md의 결과 기록
3. docs/implementation/sol/performance/07_MIP_INTERLOCK_ACTIVATION.md
4. docs/implementation/sol/07_CANDIDATE_SELECTION_MIP.md
5. docs/implementation/sol/08_INTERLOCK_DENSIFIER.md
6. HARD10_MANIFEST.json과 직전 cumulative baseline 결과

- 새로운 알고리즘을 추가하지 말고 최종 feature 조합만 비교한다.
- MIP KEEP/OFF 결정과 재검토 조건을 지킨다.
- heuristic_only, 허용된 MIP/interlock/combined variant를 hard-10 60초로 비교한다.
- activity, strict new-best, overhead, failure fallback을 모두 보고한다.
- 결과가 근소하거나 혼합이면 추가 두 seed를 실행한다.
- 승격 기준을 만족하지 않으면 OFF를 최종 결정으로 기록한다.
- 최종 default flags와 config hash를 동결하고 이 문서에 증거를 남긴다.
- 8단계는 시작하지 않으며 커밋/push는 별도 승인 시에만 수행한다.
```
