# 0단계 — 기준선, hard-10, telemetry 동결

## 목표

알고리즘 품질을 바꾸지 않고 benchmark 관측 오류를 먼저 제거한 뒤, 이후 모든 팀이 사용할 original baseline과 hard-10을 한 번만 확정한다.

## 해결할 문제

현재 180초 실행은 60초 anchor LNS와 extension LNS가 별도 호출되지만 일부 `RunTrace.model_stats`와 operator 통계가 덮어써질 수 있다. 합산 phase time을 마지막 LNS iteration 수로 나누면 instance hardness가 왜곡될 수 있다. 이 상태에서 hard-10을 확정하면 잘못된 대상을 최적화할 수 있다.

## 작업 범위

- `baseline/solver/runtime.py`
- `baseline/solver/entry.py`
- 필요한 경우 `baseline/solver/alns.py`의 telemetry 반환 경계
- `experiments/ogc_sage/benchmark_ogc_sage.py`
- 관련 benchmark/contract tests
- `HARD10_MANIFEST.json` 생성

solver의 후보 선택, acceptance, objective, 배치 결과는 바꾸지 않는다.

## 진행 절차

1. 현재 branch, HEAD, dirty files, Python/Shapely/Gurobi/OS/CPU를 기록한다.
2. 기존 240-run artifact와 source/config/dataset hash를 검증한다.
3. `RunTrace`에 LNS invocation별 레코드를 추가한다. 최소 필드는 `kind=anchor|extension`, start/end, budget, iterations, repair/retime/checker 시간, exit reason, operator stats, best trace다.
4. 기존 top-level 합산 필드는 호환성을 유지하되 합산과 마지막 호출 값을 혼동하지 않게 schema를 명시한다.
5. telemetry ON/OFF가 동일 seed에서 operations와 objective를 바꾸지 않는 parity test를 작성한다.
6. 기존 임시 hard-10을 새 계측으로 재실행하거나, 비용을 감당할 수 없다면 기존 raw에서 정확히 분리 가능한 필드만 사용한다. 추정치를 실제 측정으로 표기하지 않는다.
7. `median_seed((repair_seconds + retime_seconds) / max(1, iterations))`를 기본 runtime hardness로 계산한다.
8. 동률은 median repair time, block 수, instance 번호 순으로 정한다.
9. 상위 10개의 instance/hash/metric/rank와 입력 artifact hash를 `HARD10_MANIFEST.json`에 기록하고 동결한다.
10. original baseline identity와 hard-10 60초 결과를 새 baseline artifact로 보존한다.

## 산출물

- invocation별 LNS telemetry
- telemetry parity tests
- `HARD10_MANIFEST.json`
- original baseline raw/summary/checksum
- 이후 단계가 재사용할 paired benchmark 명령

## 완료 조건

- telemetry 추가 전후 operations/objective parity
- anchor와 extension의 iteration 및 phase time이 각각 보존됨
- hard-10 membership이 기계적으로 재현됨
- hard-10 10/10 Stage 5, parity/exception/timeout 문제 0
- original baseline commit/config/dataset/environment가 문서화됨

## 결과 기록

실행 시 baseline identity, manifest hash, 최종 hard-10, 테스트와 benchmark 결과, 남은 계측 한계를 여기에 추가한다.

### 2026-07-14 — 0단계 동결 결과

#### Original baseline identity

- branch / HEAD: `codex/performance-optimization-plan` / `5f932a611e8076d01a3f59c1e4e44da350d522e6`
- solver semantics: HEAD의 `heuristic_lns` 설정을 유지했다. 이번 변경은 invocation telemetry와 benchmark schema뿐이며 후보 선택, acceptance, objective, placement는 변경하지 않았다.
- telemetry source checksums: `runtime.py` `74dff28ba545b6ab9c8607ecd250d842e8a0c55cec5418d26c1c461195675320`, `entry.py` `b2b7da6b72f3931297404134a9c1987c316b0ba58ceb46f8bec4f75bffda45bd`, `alns.py` `4076a1bc08497f51bc28e42e7f1142586121ed1f70be5b2f645c39126cc73f63`, harness `6d2fef89921e1d0a8f83fe3f5206e7be27ffc2f7b9d504f8497f991e8ad219b3`
- current baseline config / dataset hashes: `b02608da6524b8651a3ffccdb78e7c6a985404b7740e7207c5337f3caa1a27d3` / `c093fb98b17c714ea5b9c8c829d4bce24dd378f0b558196ce0961feb68b34e1f`
- environment: macOS 26.5.2 arm64, Python 3.12.13, Shapely 2.1.2, Gurobi 13.0.2, CPU 10 logical cores.

#### Telemetry schema and parity

- `RunTrace.lns_invocations` is the authoritative per-call sequence. Every record has `kind`, relative start/end, budget, iterations, repair/retime/checker seconds, exit reason, per-operator stats, and best trace.
- `phase_times[lns_*]` and `operator_stats` are aggregate values across invocations. `operator_stats_last`, `model_stats.lns_last`, and `model_stats.lns_aggregate` make the old last-call/aggregate ambiguity explicit; legacy `model_stats.lns` remains the last-call payload for compatibility.
- telemetry ON/OFF contract: an actual seeded one-iteration `run_lns` call at 60 seconds returned byte-identical operations and the same Stage 5 checker objective; the separate 180-second boundary test verified independent anchor/extension records and aggregate counters.

#### Frozen hard-10

Manifest: [`HARD10_MANIFEST.json`](HARD10_MANIFEST.json), SHA-256 `f38e0469c8f84ff45ecbecec5a03f6f539245dcd04f7e9ac667285eeb726164b`.

| Rank | Instance | Runtime hardness (median sec/iteration) |
|---:|---|---:|
| 1 | `prob_38.json` | 5.294367 |
| 2 | `prob_23.json` | 4.550093 |
| 3 | `prob_25.json` | 4.102414 |
| 4 | `prob_40.json` | 3.955678 |
| 5 | `prob_27.json` | 3.578723 |
| 6 | `prob_21.json` | 3.422873 |
| 7 | `prob_39.json` | 3.259593 |
| 8 | `prob_33.json` | 3.206457 |
| 9 | `prob_35.json` | 2.882474 |
| 10 | `prob_20.json` | 2.878336 |

Selection input is the immutable historical 240-run raw artifact `artifacts/ogc_sage/step9/step9-final-20260713T235202Z-22b08264977a-6657431f9374/raw.jsonl`, SHA-256 `72d62d3843533bc1fbbdbb01ec664a85bdb7c53f2a287301b9c6571c917b821e`. Its legacy 180-second phase time is aggregate, but its matching 60-second record supplies the deterministic anchor iteration count; the manifest therefore reconstructs `anchor iterations + extension iterations` and explicitly labels timings as aggregate rather than falsely claiming invocation-level measurement. The formula, all seed rows, tie-break and every instance hash are in the manifest.

#### New hard-10 baseline artifact

- command: `/opt/homebrew/Caskroom/miniforge/base/envs/ogc-2026/bin/python experiments/ogc_sage/benchmark_ogc_sage.py run --gate baseline --variant heuristic_lns --budgets 60 --seeds 20260710 --instances prob_38.json prob_23.json prob_25.json prob_40.json prob_27.json prob_21.json prob_39.json prob_33.json prob_35.json prob_20.json --run-id phase0-original-hard10-20260714T101500Z-5f932a611e80 --source-commit 5f932a611e8076d01a3f59c1e4e44da350d522e6 --allow-dirty`
- artifact directory: `artifacts/ogc_sage/performance/phase0/phase0-original-hard10-20260714T101500Z-5f932a611e80`
- raw / summary / checksum-file SHA-256: `70de3198cd9fb87a3ceba6115488bce36bec0d20839b81f604549d5b8482b140` / `222813b7fdb9f6c53d5aedbcfb91a38cbba7c5eb12c1f96568856e24bdfd77f7` / `fcb6bf6513a889a30ed17883e48cb9efcef1c95b436b1c7d72bebdf43994960b`
- result: 10/10 expected/unique/Stage 5, missing/duplicate/checker failure/exception/outer-timeout `0`, checker objective parity maximum `0.0`, trace regression `0`, retime Z1 regression `0`, gate pass `true`. Every record has exactly one `anchor` invocation; no extension is expected at the fixed 60-second baseline budget.
- execution note: an accidental duplicate parent was stopped before completion. Its one duplicated raw key was reduced to the first valid terminal record before the final summary was regenerated; the canonical artifact above has 10 unique run keys and `duplicate_count=0`.

#### Tests and decision

- focused telemetry/anchor/harness contract tests: `13/13 PASS`.
- full `baseline/tests`: `184/184 PASS` (`/opt/homebrew/Caskroom/miniforge/base/envs/ogc-2026/bin/python -m unittest discover -s tests -p 'test_*.py' -v`, exit 0).
- direct invocation telemetry를 갖는 240-run의 실행·판정 절차는 [`00_RUN_240_INVOCATION_TELEMETRY.md`](00_RUN_240_INVOCATION_TELEMETRY.md)에 기록했다. 아래의 2026-07-15 direct 측정 결과가 historical manifest를 대체한다.
- 1단계 구현은 시작하지 않았다. direct-measurement manifest가 동결되었으므로, 다음 세션에서 fresh baseline/candidate paired run을 수행하는 조건으로 1단계 진입이 가능하다.

### 2026-07-15 — direct invocation 240-run 재동결

외부 일반 터미널에서 `phase0` gate로 full matrix를 완료했다. 이 결과는 historical aggregate reconstruction이 아니라 180초 실행 안에서 실제로 기록된 `anchor`와 `extension` invocation을 사용한다. 따라서 아래 manifest가 이후 단계의 유일한 hard-10 기준선이다.

- run ID / commit: `phase0-telemetry-20260714T152646Z-5f932a611e80` / `5f932a611e8076d01a3f59c1e4e44da350d522e6`
- matrix: daily-40 × `60`/`180` seconds × seeds `20260710`, `20260711`, `20260712` = 240 records
- gate: `gate_pass=true`, `run_count=240`, `unique_key_count=240`, `stage5_count=240`, missing/duplicate/checker failure/exception/outer timeout/trace regression/retime Z1 regression/longer-budget regression 모두 `0`, objective parity maximum `0.0`.
- invocation contract: 60초 `anchor` only 120건, 180초 `anchor` + `extension` 120건. 실행 전·후 source/environment fingerprint SHA-256은 모두 `516656186275c4ae53d2b04d7ffe19eb427ca3d1a5de52e1df149930ed7b7a25`로 동일하다.
- artifact directory: `artifacts/ogc_sage/performance/phase0/phase0-telemetry-20260714T152646Z-5f932a611e80`
- raw / summary / artifact manifest / artifact checksum-file SHA-256: `afb2592787f1f157d74a4b8d9054891b5cba2cfca71800b6a18407adc4d5f8dc` / `473eb02fe70b4056df097f866b741fddde972c8a48daa7cef230b953739a37a0` / `5499cf993f5018dceca0464dfcaf0c328e129cb7ed4946c79cc4e1860127d3b7` / `f38014193269251cda0a7707c1d1335afcdc925f04f131f2c415d1cf0c2a3192`.
- `PHASE0_SHA256SUMS`는 artifact 디렉터리에서 `shasum -a 256 -c PHASE0_SHA256SUMS`로 6/6 성공했다. tracked [`HARD10_MANIFEST.json`](HARD10_MANIFEST.json)도 artifact manifest와 byte-identical이며 SHA-256 `5499cf993f5018dceca0464dfcaf0c328e129cb7ed4946c79cc4e1860127d3b7`이다.

| Rank | Instance | Runtime hardness (median sec/iteration) |
|---:|---|---:|
| 1 | `prob_38.json` | 5.924304 |
| 2 | `prob_23.json` | 4.433616 |
| 3 | `prob_40.json` | 4.337077 |
| 4 | `prob_25.json` | 3.497171 |
| 5 | `prob_27.json` | 3.374033 |
| 6 | `prob_39.json` | 3.357080 |
| 7 | `prob_21.json` | 3.098986 |
| 8 | `prob_33.json` | 3.098603 |
| 9 | `prob_28.json` | 3.074735 |
| 10 | `prob_24.json` | 2.976117 |

잔여 제약은 없다. 단, 이 baseline 자체와 후보 구현의 성능을 직접 비교하는 근거로는 사용할 수 없으므로, 1단계에서는 동일 머신·동일 session에서 baseline/candidate를 paired run으로 다시 측정한다.

## 실행 프롬프트

```text
OGC-SAGE 성능 개선 0단계만 수행한다.

작업 전에 다음 문서를 처음부터 끝까지 읽는다.
1. docs/implementation/sol/performance/README.md
2. docs/implementation/sol/performance/00_BASELINE_HARD10_AND_TELEMETRY.md
3. docs/implementation/sol/10_PERFORMANCE_ACCELERATION_PLAN.md
4. docs/implementation/sol/OGC-SAGE_PROGRESS.md

이번 단계의 목표는 알고리즘 변경이 아니라 telemetry 정확성, original baseline, hard-10 동결이다.

- 현재 worktree와 branch를 사용한다. 새 branch/worktree를 만들지 않는다.
- 기존 사용자 변경을 보존한다.
- anchor/extension LNS 통계가 덮어써지지 않도록 invocation별 telemetry를 구현한다.
- telemetry가 solver operations/objective를 바꾸지 않는 테스트를 먼저 만든다.
- 수정된 계측으로 hard-10을 기계적으로 선정하고 HARD10_MANIFEST.json을 생성한다.
- baseline/utils.py와 baseline/baseline_greedy.py는 수정하지 않는다.
- hard-10 외 전체 40개 최적화 실험은 실행하지 않는다.
- 전체 unittest와 hard-10 baseline 계약을 검증한다.
- 결과와 hash를 이 단계 문서의 결과 기록에 남긴다.
- 완료 후 다음 단계는 시작하지 않는다.
- 커밋/push는 이번 요청에 별도 승인이 있을 때만 수행한다.

종료 시 변경 파일, 테스트, manifest, baseline artifact, 차단 사항을 보고한다.
```
