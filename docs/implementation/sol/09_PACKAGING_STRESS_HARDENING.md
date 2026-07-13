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
- P5/P6 submission default는 constructor gate 전까지 계속 OFF다.

## 6. 구현 범위

- public import/CLI/tester/package smoke, archive content manifest, forbidden dependency/import scan.
- monotonic deadline/checker p95 reserve, exception matrix, tiny budget behavior.
- full correctness: daily-40 at `{10,60,300}` seconds, at least 5 seeds; occasional 1800s.
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
7. gate 통과 전까지 P5/P6 off. 통과 후 variant별 10/60/300×40×5 seeds.
8. P5 operator usefulness와 P6 dense subset regression 평가. 실패 feature/operator는 off/remove 결정 기록.
9. occasional 1800s와 anytime trace; cross-budget regression 조사.
10. archive build/list/hash, clean extraction smoke, final progress/README/commit.

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

- instances: 공식 daily-40 manifest.
- budgets: 10, 60, 300 seconds.
- seeds: 최소 5개(`20260710..20260714`).
- expected: 모든 600 runs `feasible=True,stage=5`; objective parity; public exception/outer timeout 0.

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
- 1800s 일부 run은 장시간 exploitation만 확인하며 10/60/300 gate를 대체하지 않는다.

## 17. 테스트 실행 명령

tracked harness와 artifact 경로는 D-013으로 고정했다. 정확한 matrix/subset/config 명령은 D-014를 따른다.

```bash
cd baseline
/opt/homebrew/Caskroom/miniforge/base/envs/ogc-2026/bin/python -m unittest discover -s tests -p 'test_*.py' -v
cd ..
/opt/homebrew/Caskroom/miniforge/base/envs/ogc-2026/bin/python experiments/ogc_sage/benchmark_ogc_sage.py run --gate final --budgets 10 60 300 --seeds 20260710 20260711 20260712 20260713 20260714
/opt/homebrew/Caskroom/miniforge/base/envs/ogc-2026/bin/python experiments/ogc_sage/benchmark_ogc_sage.py run --gate exploitation --budgets 1800 --seeds 20260710 --instances prob_39.json
```

실제 명령/manifest/hash는 진행 문서에 복사한다. 이번 계획 작업에서는 어느 명령도 실행하지 않는다.

## 18. 테스트별 입력·검증·예상 기록

raw record는 instance name/hash, variant/config hash, budget/seed, start/end/elapsed, feasible/stage/violations, objective parts, phase/checker/model times, constructor deadline hit, trace, operator stats, Gurobi status/bound/gap, environment versions를 포함한다. summary만 남기지 말고 raw artifact 경로/hash를 진행 문서에 기록한다.

## 19. 완료 조건

- O-001~O-005가 권위 있는 증거/결정으로 닫힘.
- full unit/integration suite green.
- 600-run correctness matrix 100% Stage 5, outer timeout/exception 0.
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
- 남은 항목은 실제 constructor/feature/correctness/1800초/archive gate 결과와 Ubuntu 24.04 equivalent smoke 증거다.
- production Gurobi의 명시적 model-size 보장은 없으므로 기존 hard cap과 pure-Python fallback을 유지하며 local restricted-license 성공을 entitlement 증거로 사용하지 않는다.

## 24. 커밋 경계

- 권장 메시지: `chore(solver): harden submission and validation gates`.
- packaging/config/harness/tests/docs와 승인된 default flag 결정만 포함.
- gate 실패를 고치기 위한 substantive algorithm 변경은 해당 1~8단계로 별도 되돌려 커밋하고 그 단계 suite부터 재검증한다.
- 최종 commit hash와 archive hash를 진행 문서에 기록한다.
