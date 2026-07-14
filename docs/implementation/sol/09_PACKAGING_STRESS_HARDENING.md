# 09. Packaging 및 stress hardening

## 1. 단계 목표

1~8단계를 제출 형태로 묶고, exception/deadline/serialization/environment를 harden한 뒤 daily-40×budget×seed correctness/performance gate를 실행한다. 측정 증거로 P5/P6 default enable 여부를 결정하며, 한 건의 feasibility regression도 허용하지 않는다.

## 2. 설계 근거

설계 §1 exception armor, §12 budget/defaults, §13 packaging/activation gate, §14 validation/competition comparison, §16 advantage, §17 reproduction environment.

## 3. P0~P6 대응

P0~P6 통합 운영 단계. 새 optimization algorithm 범위를 추가하지 않고 packaging, configuration, validation, stress, release decision만 수행한다.

## 4. 선행 조건

1~8단계 각각의 단위·통합 suite와 진행 문서가 완료. 미해결 official data/submission/license/import 계약을 해결할 권위 있는 정보가 있어야 final release gate를 닫을 수 있다.

## 5. 현재 저장소 상태

- D-013 prerequisite remediation에서 official baseline v1.3 `utils.py` 두 copy를 SHA-256 `d45aaeafdce8bf80d59d097f655c43313a4951bed43b6628e3b1cf62d4876a94`로 고정했다.
- local daily-40은 공식 Training Set 1/2와 40/40 byte 일치하며 `.gitignore`의 `data/` 아래 benchmark 입력으로만 존재한다. source/submission/release archive에는 포함하지 않는다.
- `baseline/run_myalgorithm.py`는 positional instance가 필수인 local-only debug tool이고 submission에서 제외한다.
- file-location load, `cd baseline; import myalgorithm`, repository-root `import baseline.myalgorithm` 세 import 경로를 지원한다.
- 제출은 root `myalgorithm.py`와 sibling `solver/`만 허용하며 15MB, no-network/no-parent-access, public-silence, no-default-multiprocessing, Gurobi Threads<=4 계약을 적용한다.
- D-015 feature gate 결과 submission default는 heuristic LNS ON, candidate MIP OFF, interlock OFF다. final 240에서 이 설정을 변경하지 않았다.

## 6. 구현 범위

- public import/CLI/tester/package smoke, archive content manifest, forbidden dependency/import scan.
- monotonic deadline/checker p95 reserve, exception matrix, tiny budget behavior.
- final correctness: exact `heuristic_lns`, official daily-40, budgets `{60,180}`, seeds `{20260710,20260711,20260712}`, expected 240. subset/`--instances`는 final에서 금지한다.
- constructor activation gate와 P5/P6 gate.
- W/T/L, Borda/rank, relative gap, weighted components, median/p90/worst, anytime primal integral/time-to-first-improvement.
- reproducible results artifact/manifest(저장 위치는 구현 시작 전 승인; 이 단계 구현 산출물이며 이번 계획 작업에는 생성하지 않음).
- docs/README update는 실제 제출 사용법에 필요한 경우에만 이 단계 커밋에 포함.

## 7. 비범위

gate를 통과시키기 위한 알고리즘 재설계, checker 수정, benchmark 결과 조작, training-specific hardcoding, default multiprocessing. gate 실패는 해당 feature off 또는 앞 단계로 명시적 반송한다.

## 8. 변경 또는 추가할 파일

예상:

- 변경 `baseline/myalgorithm.py`, `baseline/solver/entry.py`, `budget.py`, 각 config flag, `baseline/run_myalgorithm.py`, 필요 시 `baseline/README.txt`.
- 추가 `baseline/tests/test_packaging.py`, `test_deadline.py`, `test_exception_matrix.py`, `test_stress_contract.py`.
- benchmark harness는 tracked `experiments/ogc_sage/benchmark_ogc_sage.py`로 고정한다.
- benchmark raw/summary는 gitignored `artifacts/ogc_sage/step9/<run-id>/{raw.jsonl,summary.json}`에 두고, tracked evidence manifest에는 경로/hash/요약만 기록한다.

## 9. 파일별 책임

- `myalgorithm.py`: 함수 signature와 guarded import/return만.
- `entry.py`: phase ladder, flags, catch-all boundary, always-return validated best.
- `budget.py`: checker p95/reserve/prediction, global/submodel deadline.
- harness: dataset discovery/hash, variant/budget/seed matrix, subprocess timeout, result schema/aggregation.
- packaging tests: clean copy/archive에서 imports, checker, forbidden files.

## 10. 확정할 인터페이스

```text
algorithm(prob_info,timelimit=60) -> {"operations": ...}
SubmissionConfig.from_defaults() -> immutable config
RunTrace(instance,budget,seed,phase_times,best_events,checker_events,operator/model metrics)
BenchmarkRecord(instance_hash,variant,budget,seed,elapsed,feasible,stage,obj1,obj2,obj3,total,...)
validate_dataset(root,expected_manifest) -> 40 unique instances or hard failure
```

default config는 설계 §12 수치를 그대로 사용하고 ablation 승인 없이는 바꾸지 않는다. environment variable/CLI가 competition `algorithm` behavior를 우발적으로 바꾸지 않도록 benchmark config injection은 test-only 명시 경로로 둔다.

## 11. checker 불변 조건

- 모든 run의 최종 output을 제출 시 실제 사용될 checker import 경로로 full check.
- all budgets/seeds/instances Stage 5 100%; any fail이면 variant gate fail.
- validated-best trace non-increasing; 같은 seed longer-budget regression은 자동 failure/investigation.
- objective/delta relative error `<=1e-6`.
- contract 8종(half-open, P=0, boundary, negative AABB, simultaneous entry/exit, float Z2, chronological dict)과 four-state random parity를 full suite에 유지.
- public exception 0, operations integer/canonical, return O(1) from stored best.

## 12. 예외·timeout·Gurobi fallback

matrix에 최소 `gurobipy absent`, import error, license error, size limit, model error, optimize timeout with/without SolCount, Shapely candidate error, checker rejection, negative/tiny/large timelimit을 포함한다. 모든 official-valid instance path는 pure-Python Stage 5 incumbent를 반환해야 한다. 외부 subprocess hard timeout은 algorithm TL+checker/launcher margin으로 두고 초과 시 gate fail이며 결과를 fabricated fallback으로 대체하지 않는다.

## 13. 세부 구현 순서

1. official submission/archive/tester 규칙과 data manifest/license를 확보해 O-001~O-005 결정.
2. clean environment에서 import paths 두 방식(`cd baseline; import myalgorithm`, repo root package-style 가능 시)과 GUI tester subprocess smoke.
3. static scans: frozen reference files diff, optional imports order, `time.time`/multiprocessing/forbidden files.
4. checker p95 측정 후 reserve 공식 검증; `{<2,2~12,12~60,>=60}` phase ladder branch tests.
5. exception matrix와 deterministic same-seed runs.
6. constructor-only daily-40 60s gate: 40/40, p90<=8s, max<=12s, construction deadline hit 0.
7. feature gate 결정은 heuristic LNS ON, candidate MIP OFF, interlock OFF로 고정한다. final은 40×2 budgets×3 seeds=240 exact matrix만 허용한다.
8. P5 operator usefulness와 P6 dense subset regression 평가. 실패 feature/operator는 off/remove 결정 기록.
9. final PASS 뒤에만 `prob_39.json`, 1800초, seed `20260710` exploitation과 anytime trace를 실행한다.
10. final PASS 뒤에만 archive build/list/hash와 clean extraction smoke를 실행한다.

## 14. 하위 단계 산출물

- 9A rules/data/environment/packaging contract.
- 9B deadline/exception/config hardening.
- 9C constructor and correctness gates.
- 9D performance/feature activation decision.
- 9E final archive rehearsal/evidence.

9A~9E는 순서대로 O-001~O-005 결정, exception suite, constructor/correctness matrix, feature gate, clean-archive smoke가 통과할 때만 완료한다. 앞 작업 묶음이 실패하면 뒤 gate를 실행하지 않는다.

## 15. 단위 테스트 계획

| 케이스 | 입력 | 예상 |
|---|---|---|
| import order | module spy/event log | fallback checker PASS event before gurobi import |
| phase ladder | TL 1.9/2/12/60 + fake clock | 설계 table의 허용 phase만 시작 |
| reserve formula | checker samples/limits | `min(60,.25TL,max(.05TL,2p95+.1))` 정확 |
| checker start guard | predicted+margin vs remaining | 부족 시 호출 0 |
| exception matrix | 각 injected failure | Stage 5 stored best, public exception 0 |
| canonical archive | manifest | reference checker/greedy unchanged, 필요한 solver 포함, data/results/cache/license 제외 |
| reproducibility | same fixture/seed/fake clock | operations/trace 결정적 |
| benchmark schema | partial/failed record | missing field hard error, fail 숨김 없음 |
| dataset manifest | missing/duplicate/wrong hash | benchmark 시작 전 hard failure |

## 16. 단계 통합/stress 테스트

### 필수 correctness matrix

- variant: exact `heuristic_lns`.
- instances: 공식 daily-40 manifest 전체; subset 금지.
- budgets: exact 60, 180 seconds.
- seeds: exact `20260710`, `20260711`, `20260712`.
- expected: 모든 240 runs `feasible=True,stage=5`; objective parity; public exception/outer timeout/checker failure 0; 같은 instance/seed의 60→180 objective regression 0.

### constructor gate

- variant: P0~P4 중 constructor 측정은 retimer 시간을 분리.
- daily-40, 60s, 지정 seed set에서 constructor 40/40 feasible, p90<=8s, max<=12s, its deadline reached 0.
- 하나라도 실패하면 P5/P6 default off 유지하고 6~8단계 성능 활성화로 진행하지 않는다.

### feature gates

- P5: every retained operator가 정당한 instance family에서 feasible/accepted activity>0; overall feasibility regression 0. 시간 대비 improvement가 없으면 off/remove.
- P6: 사전 정의 dense subset에서 union-safe variant 대비 strict improvement instance 존재 및 전체 subset feasibility regression 0. 아니면 off.
- retime: Z1 worsen 0, bound/gap/time/delta 기록.

### competition comparison

- safe fallback, constructor-only, +retime, +heuristic LNS, +MIP repair, +interlock variants.
- per-instance W/T/L, Borda, best-known relative gap, weighted component deltas, seed median/p90/worst, primal integral, first-improvement time.
- 1800s `prob_39.json` run은 final PASS 뒤 장시간 exploitation만 확인하며 240-run final gate를 대체하지 않는다.

## 17. 테스트 실행 명령

tracked harness와 artifact 경로는 D-013으로 고정했다. 정확한 matrix/subset/config 명령은 D-014를 따른다.

```bash
cd baseline
/opt/homebrew/Caskroom/miniforge/base/envs/ogc-2026/bin/python -m unittest discover -s tests -p 'test_*.py' -v
cd ..
/opt/homebrew/Caskroom/miniforge/base/envs/ogc-2026/bin/python experiments/ogc_sage/benchmark_ogc_sage.py run --gate final --variant heuristic_lns --budgets 60 180 --seeds 20260710 20260711 20260712 --run-id <new-run-id>
/opt/homebrew/Caskroom/miniforge/base/envs/ogc-2026/bin/python experiments/ogc_sage/benchmark_ogc_sage.py run --gate exploitation --variant heuristic_lns --budgets 1800 --seeds 20260710 --instances prob_39.json
```

final에서는 `--instances`, `--allow-dirty`, `--source-commit`을 사용하지 않는다. actual 명령/manifest/hash와 실패 결과는 §25 및 evidence manifest에 기록한다.

## 18. 테스트별 입력·검증·예상 기록

raw record는 instance name/hash, variant/config hash, budget/seed, start/end/elapsed, feasible/stage/violations, objective parts, phase/checker/model times, constructor deadline hit, trace, operator stats, Gurobi status/bound/gap, environment versions를 포함한다. summary만 남기지 말고 raw artifact 경로/hash를 진행 문서에 기록한다.

## 19. 완료 조건

- O-001~O-005가 권위 있는 증거/결정으로 닫힘.
- full unit/integration suite green.
- exact 240-run correctness matrix 100% Stage 5, outer timeout/exception/checker failure 0.
- constructor gate 충족.
- P5/P6 각각 증거에 따른 default on/off 결정과 config/문서 일치.
- objective/delta parity, non-increasing best trace, no longer-budget regression(또는 원인 수정/재검증) 모두 green.
- clean archive extraction에서 algorithm/checker smoke PASS, archive manifest 승인.
- 진행 문서의 evidence, commit, changed files, release decision 완료.

gate를 충족하지 못하면 9단계는 완료가 아니며 “차단” 또는 해당 feature off 후 재검증으로 기록한다.

## 20. 최종 제공 인터페이스

외부 계약은 오직 `baseline/myalgorithm.py::algorithm(prob_info,timelimit)->solution`. 내부 config/trace는 제출 output을 오염시키지 않는다. release artifact는 checker/reference를 수정하지 않은 solver package와 필요한 dependency 선언만 포함한다.

## 21. 진행 문서 업데이트

각 matrix 실행 후 raw evidence/hash, environment, summary, failures/retests를 추가한다. validation gate 표 전 항목에 PASS/FAIL/OFF와 근거를 채운다. final commit/archive hash, changed file manifest, release 가능 여부를 기록한다.

## 22. 위험과 대응

- data leakage/missing: manifest/hash hard gate.
- benchmark가 TL을 숨김: subprocess elapsed/outer timeout과 internal trace 모두 기록.
- license rehearsal 불일치: production-like clean environment; pure-Python fallback을 항상 별도 검증.
- feature 평균 개선이 worst feasibility를 가림: correctness가 우선 hard gate, per-instance/seed worst 보고.
- archive import path: clean extraction과 actual GUI tester smoke.
- benchmark 결과를 코드와 같은 commit에 섞음: artifact policy 결정, config hash로 재현.

## 23. 미해결 사항

- O-001~O-005/O-007/O-008의 정책·checker·경로 계약은 D-013에서 해결됐다.
- source `ab63dad...`의 종전 actual final 240에서 발생한 `60→180` longer-budget regression 3건은 source `22b0826...`의 absolute 60초 anchor와 validated incumbent 보존으로 수정됐다. 종전 실패 artifact는 삭제·수정하지 않고 immutable historical evidence로 보존한다.
- 문제 세 pair를 대상으로 한 완전한 6-run probe를 새 run-id로 두 번 실행한 뒤 exact final 240을 실행했고, 두 probe와 final 모두 longer-budget regression 0 및 `gate_pass=true`다.
- fixed final PASS 뒤 1800초 exploitation과 actual archive rehearsal을 순서대로 실행했고 둘 다 완전 PASS했다.
- actual archive clean extraction은 isolated import, official checker, Gurobi-absent fallback과 public stdout/stderr silence를 모두 통과했다. Ubuntu 환경 검증은 이번 완료 조건에서 제외하며 별도 waiver나 잔여 gate로 추가하지 않는다.
- production Gurobi의 명시적 model-size 보장은 없으므로 기존 hard cap과 pure-Python fallback을 유지하며 local restricted-license 성공을 entitlement 증거로 사용하지 않는다.

## 24. 커밋 경계

- release evidence 권장 메시지: `docs(sol): complete step 9 release evidence`.
- 이번 release evidence commit은 진행 문서와 JSON evidence 세 파일만 포함한다.
- gate 실패를 고치기 위한 substantive algorithm 변경은 해당 1~8단계로 별도 되돌려 커밋하고 그 단계 suite부터 재검증한다.
- 최종 commit hash와 archive hash를 진행 문서에 기록한다.

## 25. Actual final 240 evidence

### Active artifact

- run-id: `step9-final-20260713T235202Z-22b08264977a-6657431f9374`
- source: `22b08264977ad83980027adfb92843ece8b8f7ff`
- config SHA-256: `6657431f9374f3ced2ee90176b73ec36ac994f9a7bed0857cbea5561a87cab57`
- dataset SHA-256: `c093fb98b17c714ea5b9c8c829d4bce24dd378f0b558196ce0961feb68b34e1f`
- matrix: `heuristic_lns`, official 40, budgets 60/180, seeds `20260710..20260712`, expected 240.
- raw SHA-256: `72d62d3843533bc1fbbdbb01ec664a85bdb7c53f2a287301b9c6571c917b821e`
- summary SHA-256: `3e0197b88a5f2f0dcb5e4d1bc0135aa3581fb20d45b6726b270f0600a5cf00c7`
- `SHA256SUMS` SHA-256: `5f207686beb65fedaa0ea937ceffc38ef672c2e535c1347eb45c699a52e639df`
- `SHA256SUMS` 검증: PASS. 외부 fable/별도 OGC benchmark wall-clock overlap: 0.

결과는 run/unique/expected `240/240/240`, missing/duplicate 0, Stage 5 240, exception/outer timeout/checker failure 0, objective parity max 0, trace regression 0, retime Z1 worsen 0, longer-budget regression 0이며 `gate_pass=true`다. 종전 문제 세 pair도 모두 180초 objective가 60초 이하이고 각 180초 validated-best trace가 대응 60초 objective를 포함한다.

| instance | seed | objective 60 | objective 180 | delta |
|---|---:|---:|---:|---:|
| `prob_25.json` | 20260712 | 12,172,763 | 5,973,838 | -6,198,925 |
| `prob_35.json` | 20260712 | 596,961,781 | 167,146,795 | -429,814,986 |
| `prob_36.json` | 20260710 | 36,699,285 | 17,047,318 | -19,651,967 |

### Two complete target-probe passes

final 전 동일 source/config/dataset에서 문제 세 pair를 A(`prob_25`,`prob_35`/seed `20260712`)와 B(`prob_36`/seed `20260710`)로 나눈 완전한 6-run probe를 두 번 실행했다. 각 pass의 combined run/unique/expected는 `6/6/6`, Stage 5 6, missing/duplicate/exception/outer-timeout/checker failure/parity/trace/retime/longer-budget regression은 모두 0이며 각 `SHA256SUMS` 검증이 PASS다.

| pass/run-id | objective 60→180 | raw / summary / `SHA256SUMS` SHA-256 |
|---|---|---|
| A1 `step9-anchor-probe-a1-20260713T232942Z-22b08264977a` | prob25 `12,453,685→3,544,344`; prob35 `596,961,781→118,322,127` | `2154d5b4...` / `4b063a79...` / `e43cbd6d...` |
| B1 `step9-anchor-probe-b1-20260713T233650Z-22b08264977a` | prob36 `36,699,285→16,964,610` | `07aafc2b...` / `50671c2a...` / `ad150b4d...` |
| A2 `step9-anchor-probe-a2-20260713T234049Z-22b08264977a` | prob25 `12,469,446→3,869,894`; prob35 `596,961,781→164,658,647` | `825b0f43...` / `7f34e659...` / `349b1cde...` |
| B2 `step9-anchor-probe-b2-20260713T234753Z-22b08264977a` | prob36 `36,699,285→17,015,302` | `58a6a396...` / `db13cb6e...` / `ff61ee77...` |

### Immutable historical artifacts

source `ab63dad...`의 종전 240-run `step9-final-20260713T131023Z-ab63dad35805-6657431f9374`는 240/240 Stage 5였지만 longer-budget regression 3건으로 실패했다. raw/summary/`SHA256SUMS` SHA-256은 각각 `fbf498dd...`/`08b69907...`/`246565be...`이며 immutable/release-ineligible로 보존한다.

그보다 앞선 `step9-final-20260713T033946Z-e49d8d52c369-6657431f9374`는 600-run 계약에서 162 terminal/unique records까지 실행된 historical artifact다. raw SHA-256은 `42f92cafad25d166f737a2aaeb57c806cdfc739dff063a35ef5028f9ddb03bcc`, summary/SHA256SUMS는 없고 외부 benchmark overlap record는 41개(`#9-41,#56-59,#97,#146,#149,#161`)다. active 240 artifact에 복사·병합·resume하지 않으며 release-ineligible로 보존한다.

### Release decision

long-budget anchor 전용 5/5와 전체 `baseline/tests` 180/180이 PASS했고 final correctness matrix도 PASS했다. 이어 §26의 exploitation과 §27의 actual archive rehearsal까지 모두 PASS했으므로 최종 판정은 §28에서 닫는다.

## 26. Actual 1800-second exploitation evidence

- run-id: `step9-exploitation-20260714T080026Z-22b08264977a-6657431f9374`
- artifact: `artifacts/ogc_sage/step9/step9-exploitation-20260714T080026Z-22b08264977a-6657431f9374`
- source/config/dataset: `22b08264977ad83980027adfb92843ece8b8f7ff` / `6657431f9374f3ced2ee90176b73ec36ac994f9a7bed0857cbea5561a87cab57` / `c093fb98b17c714ea5b9c8c829d4bce24dd378f0b558196ce0961feb68b34e1f`
- command: `/opt/homebrew/Caskroom/miniforge/base/envs/ogc-2026/bin/python experiments/ogc_sage/benchmark_ogc_sage.py run --gate exploitation --variant heuristic_lns --budgets 1800 --seeds 20260710 --instances prob_39.json --run-id step9-exploitation-20260714T080026Z-22b08264977a-6657431f9374`
- result: run/unique/expected `1/1/1`, missing/duplicate 0, Stage 5 `1`, exception/outer-timeout/checker failure 0, objective parity max 0, trace regression 0, retime Z1 worsen 0, `gate_pass=true`.
- elapsed: `1740.3399755420105s`; final `(obj1,obj2,obj3,total)=(27313,1494,5652,365018005)`.
- validated-best: 65 events. initial event `(0.146074749995023s, 1962525591)`, first strict improvement `(7.0065483750076964s, 1356306027)`, final event `(1740.1213379999972s, 365018005)`.
- first strict improvement 계산식: ordered events `e[0..n-1]`에서 `min{i | i >= 1 and e[i].objective < e[0].objective}`의 event를 선택했다. 추정값을 사용하지 않았으며 full trace/events는 `step9-evidence.json`과 raw record에 보존한다.
- raw/summary/`SHA256SUMS` SHA-256: `24dfbf2774bd2d7cdda05a85fccd9bf72acc3011b60f1ebea5cd6f25ddf1fd06` / `cebbcdc5f385c2a25e50f037895aa9a4fd2180fe6aaaae3ee05d93baa31921eb` / `98b36f6f3a56b4f57f671caf60743ae0079254ad9049f0a22d5e5502184eb4c0`.
- `shasum -a 256 -c SHA256SUMS`: raw/summary 모두 OK.

## 27. Actual submission archive rehearsal

### Archive identity and members

- archive: `artifacts/ogc_sage/step9/step9-archive-20260714T083017Z-22b08264977a/submission.zip`
- command: `/opt/homebrew/Caskroom/miniforge/base/envs/ogc-2026/bin/python experiments/ogc_sage/benchmark_ogc_sage.py archive --output artifacts/ogc_sage/step9/step9-archive-20260714T083017Z-22b08264977a/submission.zip`
- SHA-256: `dd7893a4e69fb8e30d8fabd99151bccf2a08b9fd81677e3fc0e0cb1175b2275d`
- size: `65746` bytes (`<=15 MiB`). Independent `shasum -a 256`, `unzip -l`, `zipinfo -1`과 duplicate/absolute/`..`/forbidden-path 검사가 PASS했다.
- exact members:

  - `myalgorithm.py`
  - `solver/__init__.py`
  - `solver/alns.py`
  - `solver/assignment.py`
  - `solver/budget.py`
  - `solver/construct.py`
  - `solver/entry.py`
  - `solver/fallback.py`
  - `solver/geometry.py`
  - `solver/instance.py`
  - `solver/interlock.py`
  - `solver/neighborhoods.py`
  - `solver/repair_mip.py`
  - `solver/retime.py`
  - `solver/runtime.py`
  - `solver/serialize.py`
  - `solver/state.py`

`utils.py`, tests, data, benchmark artifacts/results, cache, license, debug runner와 `__pycache__`는 zip member가 아니다. official checker와 training data도 재배포하지 않는다.

### Actual clean extraction smoke

- clean root: `/tmp/ogc-sage-step9-archive-smoke.Zk4n1z/clean` (새 `mktemp -d /tmp/ogc-sage-step9-archive-smoke.XXXXXX`).
- actual zip을 위 root에 추출한 뒤 archive 밖의 `baseline/utils.py`를 server checker `utils.py`로, `alg_tester/example/example_B2_b10.json`을 `instance.json`으로 주입했다.
- normal isolated invocation은 `/opt/homebrew/Caskroom/miniforge/base/envs/ogc-2026/bin/python`, `-I`, `-c`, file-location import/`algorithm(raw,2.0)`/official-check script의 네 argv 항목이다. 실행 script 전문을 포함한 exact argv는 `step9-evidence.json`의 `artifacts.submission_archive.clean_extraction_smoke.normal.argv`에 보존한다.
- Gurobi-absent invocation은 같은 네 argv 구조에서 import hook으로 `name == 'gurobipy'`에 `ImportError('absent')`를 주입했다. 실행 script 전문을 포함한 exact argv는 `step9-evidence.json`의 `artifacts.submission_archive.clean_extraction_smoke.gurobipy_absent_fallback.argv`에 보존한다.
- 두 run 모두 repository parent/개발자 절대경로를 `sys.path`에 추가하지 않았고 extraction root와 interpreter standard/site-package path만 사용했다.
- normal: file-location import 성공, exit 0, public stdout/stderr `""`/`""`, official checker feasible/Stage `true/5`, `(obj1,obj2,obj3,total)=(0,14,46,1018)`.
- Gurobi-absent: import 성공, exit 0, public stdout/stderr `""`/`""`, official checker feasible/Stage `true/5`, `(obj1,obj2,obj3,total)=(0,183,0,1281)`.
- packaging tests: `/opt/homebrew/Caskroom/miniforge/base/envs/ogc-2026/bin/python -m unittest tests.test_packaging -v` → 5/5 PASS.
- full tests: `/opt/homebrew/Caskroom/miniforge/base/envs/ogc-2026/bin/python -m unittest discover -s tests -p 'test_*.py'` → 180/180 PASS.

## 28. Final Step 9/release decision

Fixed final 240, exploitation, archive allowlist/size/hash, actual archive clean extraction, isolated import, official checker Stage 5, Gurobi-absent fallback Stage 5, public stdout/stderr silence와 전체 baseline tests가 모두 PASS했다. Ubuntu 환경 검증은 이번 작업 범위와 완료 조건에서 제외했고 별도 waiver나 잔여 gate로 추가하지 않는다.

- `step9_complete=true`
- `release_ready=true`
- evidence commit 대상은 본 문서, `OGC-SAGE_PROGRESS.md`, `evidence/step9-evidence.json` 세 파일뿐이다.
- push는 실행하지 않는다.
