# Phase 0 — Baseline 및 회귀 계약 동결

중요도: 필수 기반  
수정 허용: 계측·benchmark harness·문서만  
알고리즘/default 변경: 금지

## 1. 목표

현재 working tree의 Python default를 이후 모든 phase가 비교할 **frozen baseline**으로 만든다. 최근 `prob_23` 120초 Python/native 결과가 실제 동일 조건의 회귀인지 source, binary, config, dataset, seed, CPU 상태까지 검증하고, 재현 가능한 regression fixture와 immutable artifact 계약을 만든다.

이 phase는 품질을 개선하지 않는다. 잘못된 baseline이나 오염된 benchmark 위에서 이후 변경을 승격하지 못하게 하는 단계다.

## 2. 선행조건과 보호 범위

- `../12_SOLUTION_QUALITY_RECOVERY_PLAN.md`와 `SCHEDULER_PROTOCOL.md`를 읽는다.
- 기존 pybind controller `019f66b6-4a1c-7fb1-b365-ce336f118ce9`가 같은 working tree에 쓰는 중인지 확인한다.
- 현재 dirty/untracked 파일은 사용자 작업으로 간주하며 삭제, stash, reset, restore하지 않는다.
- `baseline/utils.py`, `baseline/baseline_greedy.py`, training data를 수정하지 않는다.
- production `SubmissionConfig.from_defaults()`와 feature default를 바꾸지 않는다.
- 과거 artifact를 수정하거나 현재 source의 증거로 재분류하지 않는다.

## 3. 반드시 동결할 identity

`identity.json`에 다음을 기록한다.

1. UTC/KST, hostname, OS/architecture, logical/physical CPU, RAM
2. Python/compiler/CMake/pybind11/Shapely/GEOS/Gurobi 버전과 Gurobi license 상태
3. branch, `HEAD`, `git status --porcelain=v1`, tracked diff SHA-256, untracked source 목록
4. solver Python 파일별 SHA-256와, native를 비교할 경우 extension binary SHA-256
5. official checker와 greedy SHA-256
6. dataset manifest 및 aggregate SHA-256, hard-10 manifest SHA-256
7. variant config 전체와 canonical config hash
8. budget, seed, instance, 실행 순서, outer timeout, checker reserve
9. 1초 system CPU delta, `(PID, create_time)` measurement tree, pre-run qualification, runtime coverage/overlap와 atomic-pair fairness

`HEAD`만으로 dirty source를 식별하지 않는다. tracked diff와 실행 대상 untracked source를 포함한 `source_snapshot_sha256`를 따로 계산한다.

## 4. 순차 실행 절차

### 0.1 Preflight

1. repo root, branch, HEAD, status를 기록한다.
2. `git diff --check`를 실행한다. 기존 오류와 phase가 만든 오류를 구분한다.
3. 보호 파일 hash가 상위 계획의 checker hash와 일치하는지 확인한다.
4. native extension이 있으면 import 경로와 실제 로드된 파일 hash를 기록한다. source와 binary가 맞는다는 추정은 금지한다.
5. 현재 전체 test 수와 결과를 동결한다.

권장 smoke 명령은 다음과 같다. worker는 실제 interpreter 절대경로를 `commands.json`에 기록한다.

```bash
cd baseline
<python> -m unittest discover -s tests -p 'test_*.py'
```

### 0.2 Dataset 및 scenario manifest

1. `benchmark_ogc_sage.py`의 dataset validation으로 공식 40개 파일과 hash를 검증한다.
2. 기존 hard-10 manifest가 정확히 10개 unique instance를 가리키는지 검증한다.
3. 다음 고정 scenario를 별도 manifest로 만든다.
   - `prob_23` regression anchor: 120초, seed `20260710`, Python/native paired
   - constructor gate: hard-10, 60초, seeds `20260710..20260712`
   - integrated gate: hard-10, 60/180초, 같은 3 seeds
   - final gate: all-40, Phase 8 문서의 budgets/seeds
4. manifest에는 파일명뿐 아니라 instance SHA-256를 포함한다.

### 0.3 CPU measurement 계약 검증

1. raw per-PID `ps %CPU`는 diagnostic top contributor로만 기록하고 gate 입력에서 제외한다.
2. 1초 cadence로 cumulative system CPU time delta를 수집하고 `logical_cores × wall_seconds`로 정규화한다. macOS Mach host tick 또는 `psutil.cpu_times()` native wrapper를 사용한다.
3. `(PID, create_time)`와 전체 ancestry/descendant로 pair runner/solver/monitor measurement subtree를 식별하고 그 CPU seconds만 system busy에서 차감해 `external_busy_fraction`과 `available_external_capacity`를 계산한다.
4. 나머지 process를 `competing_compute`, `os_background`, `controller_ui`, `unknown`으로 진단 분류한다. 이름은 aggregate allowlist가 아니며 measurement 이외의 접근 불가·미귀속 busy까지 host-derived external aggregate에 남는다.
5. gating completeness는 `host_tick_valid`, 1초 host sampling `temporal_coverage`, `max_sample_gap_seconds`, `measurement_subtree_identity_complete`, `measurement_subtraction_complete`로 각각 기록한다. global PID-count `process_access_coverage`는 attribution reachability 진단값이고 gate 입력이 아니다. `cpu_time_attribution_coverage = attributed accessible non-measurement CPU seconds / host-derived external busy seconds`를 `0..1` diagnostic confidence로 기록하며 gate 입력으로 사용하지 않는다.
6. `AccessDenied`, `NoSuchProcess`, `ZombieProcess`, `OSError`는 각각 count/evidence로 기록한다. unrelated non-measurement attribution 오류는 diagnostic이다. measurement root/등록 subtree의 접근·identity·CPU delta/subtraction 실패만 structural hard failure이며, system tick 오류와 temporal sampling 오류도 별도로 기록한다.
7. 최대 300초 동안 10초 qualification window를 탐색한다. temporal coverage `>=95%`, sample gap `<=2.5초`, 평균 available capacity `>=70%`이고 어떤 5초 rolling window도 `<50%`가 아니면 `READY`다. 단일 temporal/gap invalid window는 `BLOCKED`가 아니며 탐색을 계속하고, 유효 window가 없으면 `PENDING_ENVIRONMENT`로 정상 종료한다.
8. host tick 수집/회귀/구조 실패 또는 measurement root/등록 subtree의 접근·identity·CPU subtraction이 구조적으로 불가능할 때만 `BLOCKED`다. authoritative 문서 밖의 consecutive clean heartbeat 두-window 규칙은 금지한다.
9. fixed-time run 전체를 같은 cadence로 감시한다. 평균 external busy `>30%`, 임의 5초 rolling `>50%`, known competing compute `>=0.5` core-equivalent 5초 연속, temporal coverage `<95%`, sample gap `>2.5초`, measurement subtree 차감 실패 중 하나면 해당 run은 invalid이며 atomic pair 양쪽을 폐기한다.
10. process를 종료하거나 변경하지 않는다. host tick과 measurement subtree structural failure는 fail-closed다.

### 0.4 Frozen Python baseline 생성

1. 현재 production Python default를 explicit benchmark variant로 복제하되 solver 의미론은 바꾸지 않는다.
2. hard-10 60초×3 seeds matrix의 각 fixed-time run을 runtime capacity monitor 아래 새 run ID로 실행한다. 시간이 부족하거나 runtime validity가 실패하면 일부 결과를 gate 증거로 승격하지 않는다.
3. 모든 raw record에서 Stage 5, violations 0, component parity, trace monotonicity, exception/outer timeout을 확인한다.
4. constructor와 ALNS telemetry가 이후 phase에 필요한 필드를 실제로 포함하는지 검사한다.
5. 결과를 frozen baseline manifest로 가리키고 raw artifact를 immutable하게 둔다.

### 0.5 `prob_23` 회귀 재검증

1. 기존 `49,102,302` 대 `89,478,006` pair의 source/config/data/binary/CPU identity가 완전한지 먼저 감사한다.
2. 한 항목이라도 없으면 이 수치는 **회귀 신호**로만 유지하고 승격/기각의 유일한 표본으로 사용하지 않는다.
3. `READY` qualification 뒤 pair 1은 Python → native, pair 2는 native → Python 순으로 실행한다. pair 하나는 atomic unit이다.
4. 양쪽 모두 동일 instance, 120초, seed, source snapshot, checker, reserve를 사용한다.
5. objective와 함께 ALNS iterations, primal integral, repair calls, candidate rows, pairs, UNKNOWN, exact calls, boundary/finalize/fallback 시간을 기록한다.
6. 두 run 평균 external busy 차이는 `<=5 percentage points`, 1초 sample p95 차이는 `<=10 points`여야 한다. 어느 한 run의 overlap/coverage/fairness가 실패하면 양쪽 모두 폐기하고 새 immutable pair ID로 전체 pair를 재실행한다.
7. source/config/data/native binary/measurement contract가 바뀌면 전체 matrix와 모든 pair에 새 identity를 부여한다. 좋은 한쪽만 교체하지 않는다.

### 0.6 Artifact 마감

1. `identity.json`, `measurement-contract.json`, `commands.json`, `cpu-qualification.json`, run별 runtime capacity, pair fairness/disposition, raw/checker/comparison, `gate.json`을 생성한다.
2. artifact root 내부 모든 regular file의 recursive `SHA256SUMS`를 생성하고 즉시 재검증한다.
3. 이 문서의 `실행 기록`에 경로와 manifest SHA-256만 append한다.

## 5. 필수 지표

- correctness: completed/Stage/violations/parity/trace/timeout/exception
- baseline quality: objective, `Z1/Z2/Z3`, per-instance/seed distribution
- constructor: time, profiles, candidates, cap hit, fallback count/rate, initial objective
- ALNS: iterations, attempted/accepted/new-best, first improvement, primal integral
- repair: calls, rows, returned candidates, pairs, UNKNOWN, exact calls, phase seconds
- environment: host tick validity, temporal coverage, gap, measurement subtraction, system busy/external busy/available capacity, diagnostic process access/CPU-time attribution coverage, categorized process errors, process classification, runtime material overlap, pair fairness

## 6. 통과 기준

다음을 모두 만족해야 `PASS`다.

- 보호 hash 및 dataset/hard-10 manifest 검증 PASS
- 전체 baseline tests PASS
- frozen Python matrix가 전부 Stage 5, violations/exception/outer timeout/parity/trace regression 0
- baseline source/config/data/environment hash가 완전함
- `prob_23` 두 atomic pair가 runtime/fairness를 포함해 유효하게 재실행됨
- artifact `SHA256SUMS` 검증 PASS

상태는 다음 네 가지를 사용한다.

- `READY`: identity/tests/scenario/measurement contract와 유효 10초 qualification이 완성되어 run 시작 가능
- `PENDING_ENVIRONMENT`: 300초 안에 유효 qualification window가 없어 solver run 없이 정상 종료
- `PASS`: 유효 matrix와 모든 atomic pair가 완성됨
- `BLOCKED`: host tick 수집/회귀/구조 실패 또는 measurement root/등록 subtree 접근·identity·CPU subtraction 불가

`PENDING_ENVIRONMENT`를 solver FAIL/BLOCKED로 위장하지 않고, `BLOCKED`를 단순 외부 busy에 사용하지 않는다. Phase 1은 Phase 0 `PASS` 후에만 허용한다.

## 7. 실패 분기

- identity 누락/비교 불공정 → 새 diagnose session에서 harness 계약 분석
- Stage/parity/trace 실패 → correctness owner phase로 복귀, 품질 개선 금지
- native import/binary mismatch → pybind/native diagnose session
- 300초 capacity window 미획득 → 코드 수정 없이 `PENDING_ENVIRONMENT`
- host tick 수집/회귀/구조 실패 또는 measurement root/등록 subtree 접근·identity·CPU subtraction 실패 → `BLOCKED`
- baseline 비결정성 → RNG/config/global state diagnose 후 fresh fix/retry

## 8. 산출물

```text
artifacts/ogc_sage/quality-recovery/p0-<run-id>/
  identity.json
  scenarios.json
  measurement-contract.json
  commands.json
  cpu-qualification.json
  baseline/raw.jsonl
  baseline/summary.json
  baseline/runtime-capacity/*.json
  prob23/pairs/<immutable-pair-id>/cpu-qualification.json
  prob23/pairs/<immutable-pair-id>/run-*/run.json
  prob23/pairs/<immutable-pair-id>/pair.json
  prob23/python.json
  prob23/native.json
  comparison.json
  gate.json
  SHA256SUMS
```

## 9. Worker 실행 프롬프트

```text
역할: quality recovery Phase 0 baseline/regression worker.

docs/implementation/sol/12_SOLUTION_QUALITY_RECOVERY_PLAN.md,
docs/implementation/sol/quality-recovery/SCHEDULER_PROTOCOL.md,
docs/implementation/sol/quality-recovery/00_BASELINE_AND_REGRESSION_CONTRACT.md를 전체로 읽고 이 문서만 실행하라.

알고리즘과 production default를 변경하지 말라. 현재 dirty/untracked 작업을 보존하라. source/config/data/native binary/CPU measurement identity를 동결하고, 전체 tests와 frozen Python baseline을 검증하라. 최대 300초의 10초 system-capacity qualification이 READY일 때만 prob_23 120초 Python/native atomic alternating pair를 실행하고 fixed-time 전체를 1초 cadence로 감시하라. 모든 raw/checker/runtime capacity/pair fairness/comparison/gate/SHA256SUMS를 새 immutable artifact에 남기고 실행 기록을 append하라.

비교 계약이 불완전하면 결과를 추정하지 말라. qualification timeout은 PENDING_ENVIRONMENT, 구조적 측정 불가는 BLOCKED로 구분하라. commit/push하지 말라.
```

## 10. 실행 기록

아래 기존 기록의 per-PID `ps %CPU >=25%`와 2-snapshot 판정은 당시 artifact의 immutable historical fact일 뿐, §0.3의 현재 gate 입력이나 fresh retry 규칙이 아니다.

### 2026-07-16 — p0-20260715T193411Z-302f9e8f-retry3

판정: **BLOCKED**

source/config/data/environment:

- branch/HEAD: `codex/performance-optimization-plan` / `302f9e8f0d3cd5eba8f68f633d1a2ba9013396aa`; upstream 대비 behind/ahead `0/3`.
- source snapshot SHA-256: `0462447baa88578c214630c9651d8650cb3f3fa6d480e3e2b2b175815a1546da`; 시작 전 tracked diff와 untracked 보호 목록은 `identity.json`에 별도로 구분했다.
- frozen Python config SHA-256: `f18a0d3ee7a5f2c76ef59bad6f53c0144288b2134fc3252d59ac9dc477f5924a`; production default Python, native prefilter/MIP/interlock OFF를 유지했다.
- official daily-40 `40/40`, dataset SHA-256 `c093fb98b17c714ea5b9c8c829d4bce24dd378f0b558196ce0961feb68b34e1f`; hard-10 `10/10 unique`, manifest SHA-256 `5499cf993f5018dceca0464dfcaf0c328e129cb7ed4946c79cc4e1860127d3b7`.
- macOS `26.5.2` arm64, physical/logical CPU `10/10`, RAM `25,769,803,776` bytes, CPython `3.12.13`, Shapely `2.1.2`, GEOS `3.13.1`, Gurobi `13.0.2` license available, vendored pybind11 `2.13.6`; CMake executable은 PATH에 없었다.
- native comparison binary는 exact path에서 load됐고 SHA-256 `38352e2040e311822ffa591e6d98a0b7754299bc935cd521f9778b508692232d`, scaffold API `1`, repair API `2`였다. 현재 gate로 실행하지 않았다.

변경 범위:

- measurement-only `experiments/ogc_sage/verify_quality_p0.py` 추가.
- `experiments/ogc_sage/benchmark_ogc_sage.py`에 benchmark artifact parent override 옵션 추가.
- solver algorithm, production config/default, checker, greedy, training data는 변경하지 않았다.

tests:

- full unittest `204/204 PASS`; `git diff --check` PASS.
- 보호 hash와 dataset/hard-10 validation PASS.

fixed-work: Phase 0 신규 fixed-work 없음. 기존 pybind artifact는 historical evidence로만 분류했다.

fixed-time:

- 결정 CPU snapshot 시작 간격 `2.510077916s`.
- 동일 외부 PID `2362` `StorageManagementService`가 `82.8% -> 54.7%`로 두 snapshot 모두 `>=25%`였다. 어떤 process도 종료하거나 변경하지 않았다.
- hard-10 Python 60초 x 3 seeds: planned `30`, executed `0`.
- `prob_23` 120초 Python/native alternating 2 pairs: planned `4`, executed `0`.
- 부분 matrix를 PASS로 승격하지 않았다. 재현 명령과 frozen scenario는 artifact에 완성했다.

quality: 현재 pair가 없어 `INCONCLUSIVE / NOT RUN`; 과거 `49,102,302 vs 89,478,006`은 current identity 불완전으로 regression signal만 유지했다.

artifact: `artifacts/ogc_sage/quality-recovery/p0-20260715T193411Z-302f9e8f-retry3`

SHA256SUMS: `83ad801d22aa954e217a2dd833cd3fbea3349e1fcbc7bfbed0cf4477b764db88` (17개 항목 전체 재검증 PASS)

다음 단계 허용 여부: **불가**. 지속 외부 PID `>=25%`가 없는 window에서 fresh Phase 0 retry가 필요하다.

준비 중 실패/무효 시도도 덮어쓰지 않고 별도 봉인했다: 최초 prepare FAIL `6f0b8ca404f2b7705c94cc5d7fff38ca4ebf6421efc64102136682fed2574cbf`, CPU 간격 초과 INCONCLUSIVE `821cfcbadca37e47a466ed93d71fffbd5e269b8af80ba520a0e6e9a45673bf7f`, identity 불완전 INCONCLUSIVE `9a05fb8a71113062ef0f17f43aed0066735b4fef12c5363efab370a1e68de259`.

### 2026-07-16 — p0-20260715T195412Z-302f9e8f-retry

판정: **BLOCKED**

source/config/data/environment:

- branch/HEAD는 `codex/performance-optimization-plan` / `302f9e8f0d3cd5eba8f68f633d1a2ba9013396aa`로 고정값과 일치했다.
- source snapshot SHA-256 `0462447baa88578c214630c9651d8650cb3f3fa6d480e3e2b2b175815a1546da`, execution files aggregate `85792d7b66d56fa89942768fa4e7d11ba03037a5257e0bfdfbbe1c9a36d05d2d`, frozen Python config `f18a0d3ee7a5f2c76ef59bad6f53c0144288b2134fc3252d59ac9dc477f5924a`가 exact match였다.
- official daily-40 `40/40`, dataset SHA-256 `c093fb98b17c714ea5b9c8c829d4bce24dd378f0b558196ce0961feb68b34e1f`; hard-10 `10/10 unique`, manifest SHA-256 `5499cf993f5018dceca0464dfcaf0c328e129cb7ed4946c79cc4e1860127d3b7`; `prob_23` SHA-256 `1b0e48c383157c3140d4197f1bb3326f9e67e0ded0d8345bcc35d57186b94738`가 모두 exact match였다.
- native comparison binary SHA-256 `38352e2040e311822ffa591e6d98a0b7754299bc935cd521f9778b508692232d`, scaffold API `1`, repair API `2`; benchmark harness `3f11bffd338491b3df09d6b933c51f24a1900e27e1db05d1b34ab9fad49e4b59`, retry harness `a4b6e6525a4ba971957a01c39243065f6422a33c6fd965a3ad9a0f9afa186599`가 exact match였다.
- 시작 시 Phase 0 문서 SHA-256 `12b7b3f905891da45df36a0c090123eea1001a95cb63db75bb540331282d8680` 및 나머지 보호 hash가 dispatch 계약과 일치했다. retry harness의 `finalize-blocked`가 이전 실행 기록 append 전 문서 hash를 하드코딩해 `gate.json`의 `protected_hashes_pass`를 false로 기록하는 stale 판정은 `retry-contract-audit.json`에 실제 exact match와 함께 명시했다.

변경 범위:

- 새 immutable retry artifact 생성과 이 Phase 0 실행 기록 append만 수행했다.
- solver/source/config/harness/tests/training data를 수정하지 않았고 기존 dirty/untracked 및 이전 artifact를 보존했다.

tests:

- full unittest `204/204 PASS`; `git diff --check` PASS.
- dataset daily-40 및 hard-10 validation PASS.

fixed-work: Phase 0 신규 fixed-work 없음.

fixed-time:

- 결정 CPU snapshot 시작 간격 `2.510076750011649s`.
- 동일 외부 PID `2362` `StorageManagementService`가 `105.4% -> 88.6%`로 두 snapshot 모두 `>=25%`였다. process를 종료하거나 변경하지 않았다.
- hard-10 Python 60초 x 3 seeds: planned `30`, executed `0`.
- `prob_23` 120초 Python/native alternating 2 pairs: planned `4`, executed `0`.
- CPU hard gate에 따라 solver run은 0이며 partial matrix를 PASS로 승격하지 않았다.

quality: 현재 matrix/pair가 없어 `INCONCLUSIVE / NOT RUN`; gate 판정은 외부 CPU에 의한 `BLOCKED`다.

artifact: `artifacts/ogc_sage/quality-recovery/p0-20260715T195412Z-302f9e8f-retry`

SHA256SUMS: `52ac85e2c9b87292154801e8517d394e05241253b1b8b6dd11189b8de6c2640d` (19개 항목 전체 재검증 PASS)

다음 단계 허용 여부: **불가**. 지속 외부 PID `>=25%`가 없는 window에서 fresh Phase 0 retry가 필요하다.

### 2026-07-16 — p0-fix-20260716T010657Z-302f9e8f

판정: **FIXED / PASS**

source/config/data/environment:

- branch/HEAD는 `codex/performance-optimization-plan` / `302f9e8f0d3cd5eba8f68f633d1a2ba9013396aa`로 유지됐다.
- retry harness SHA-256은 `a4b6e6525a4ba971957a01c39243065f6422a33c6fd965a3ad9a0f9afa186599`에서 `8d62b2c9292c499e6b8b9275b5148b0e89529871e745674469256624040549ae`로 갱신됐다.
- execution files aggregate SHA-256은 `9c3f951e0156b410534a3773474e805d5c2a02c49ded1df344dd9a6ceb144144`, source snapshot SHA-256은 `91086b0bade6e1314deeebe49f6aefeff4336cec14b3c55907505c3019ef4fc0`로 재계산했다. 이전 retry의 `85792d7b66d56fa89942768fa4e7d11ba03037a5257e0bfdfbbe1c9a36d05d2d` / `0462447baa88578c214630c9651d8650cb3f3fa6d480e3e2b2b175815a1546da`를 재사용하지 않았다.
- frozen Python config `f18a0d3ee7a5f2c76ef59bad6f53c0144288b2134fc3252d59ac9dc477f5924a`, dataset `c093fb98b17c714ea5b9c8c829d4bce24dd378f0b558196ce0961feb68b34e1f`, hard-10 manifest `5499cf993f5018dceca0464dfcaf0c328e129cb7ed4946c79cc4e1860127d3b7`, `prob_23` `1b0e48c383157c3140d4197f1bb3326f9e67e0ded0d8345bcc35d57186b94738`, native binary `38352e2040e311822ffa591e6d98a0b7754299bc935cd521f9778b508692232d`는 미변경이다.

변경 범위:

- `experiments/ogc_sage/verify_quality_p0.py` 한 줄만 수정했다. `finalize-blocked`의 Phase 0 문서 비교를 최초 pre-append 상수와의 비교에서, retry별 `identity-preflight.json` 동결 hash와 finalize 시점 현재 문서 hash의 비교로 변경했다.
- checker, greedy, pybind 계획 문서의 절대 hash 비교는 유지했다. solver/native/config/default/benchmark/artifact schema/CPU gate는 변경하지 않았다.

tests:

- temporary fixture 6/6 PASS: D0 unchanged `true`, seal 후 append된 D1의 fresh preflight/unchanged finalize `true`, 같은 retry prepare-finalize 사이 Phase 0 문서 변경 `false`, checker/greedy/pybind plan 개별 변경은 각각 `false`.
- canonical interpreter py_compile PASS; `git diff --check` PASS.
- 기존 artifact 두 개의 `shasum -a 256 -c SHA256SUMS` 전체 PASS 및 self hash `83ad801d22aa954e217a2dd833cd3fbea3349e1fcbc7bfbed0cf4477b764db88`, `52ac85e2c9b87292154801e8517d394e05241253b1b8b6dd11189b8de6c2640d` 유지.

fixed-work: focused harness fixture만 실행했다. solver fixed-work는 실행하지 않았다.

fixed-time: CPU audit, fixed-time solver, hard-10, `prob_23`, Phase 0 retry, Phase 1은 실행하지 않았다.

quality: 알고리즘/품질 판정 변경 없음. 이 fix는 dispatch-frozen Phase 0 문서 identity의 false-negative만 제거한다.

artifact: `artifacts/ogc_sage/quality-recovery/p0-fix-20260716T010657Z-302f9e8f`

SHA256SUMS: `cfb7fc57202a16d2ca2b93eeb38ee9ac889cfee5d4c531a584d962b06c05b229` (8개 항목 전체 재검증 PASS)

다음 단계 허용 여부: **fresh Phase 0 retry 허용**. Phase 1은 아직 불가하며, fresh retry는 새 harness/execution/source identity를 사용해야 한다.

### 2026-07-16 — p0-cpu-contract-fix-20260716T014245Z-302f9e8f

판정: **FIXED / PASS**

source/config/data/environment:

- branch/HEAD는 `codex/performance-optimization-plan` / `302f9e8f0d3cd5eba8f68f633d1a2ba9013396aa`로 유지됐다.
- pre-append harness SHA-256은 `26da658cc084b2a17558baf32675910c762c53f57e4f5229f2467da8eb8b4753`, execution files aggregate는 `8097db3470049cf3f4560ffd81b352a0b21300966adf249e83446d6f84372723`, source snapshot은 `18bef6efabf0fcb41740c549ef22f95ca5711a5221077c52d3fa263eb2af926f`, matrix identity는 `8bf75fa9ba1b2b1d94a91f8612fc855f6bc71bfe4145a1028410630bb26564ec`다. 이전 `8d62...` / `9c3f...` / `9108...` identity를 재사용하지 않았다.
- frozen config `f18a0d3ee7a5f2c76ef59bad6f53c0144288b2134fc3252d59ac9dc477f5924a`, dataset `c093fb98b17c714ea5b9c8c829d4bce24dd378f0b558196ce0961feb68b34e1f`, hard-10 `5499cf993f5018dceca0464dfcaf0c328e129cb7ed4946c79cc4e1860127d3b7`, `prob_23` `1b0e48c383157c3140d4197f1bb3326f9e67e0ded0d8345bcc35d57186b94738`, native binary `38352e2040e311822ffa591e6d98a0b7754299bc935cd521f9778b508692232d`, benchmark harness `3f11bffd338491b3df09d6b933c51f24a1900e27e1db05d1b34ab9fad49e4b59`는 미변경이다.

변경 범위:

- 허용된 네 파일만 변경했다: 상위 계획, scheduler protocol, 이 Phase 0 계약, `experiments/ogc_sage/verify_quality_p0.py`.
- raw `ps %CPU` gate를 제거하고 1초 cumulative system CPU delta, logical-core capacity 정규화, `(PID, create_time)` ancestry/descendant measurement subtree 차감, diagnostic classification, 10초/300초 qualification, runtime overlap/coverage/gap, atomic alternating pair fairness, READY/PENDING_ENVIRONMENT/PASS/BLOCKED 상태를 구현했다.
- solver algorithm/default/config/data/native binary/benchmark behavior와 README/pybind 계획은 변경하지 않았고 새 tracked source/test 파일을 만들지 않았다.

tests:

- synthetic focused 10/10 PASS: 4/10/16-core normalization, self/child/grandchild와 Codex ancestor/sibling/PID reuse, WindowServer/Storage/Codex/solver/compiler/profiler/unknown 분류, 세 historical raw-ps fixture, 70/50/30 및 rolling boundary, coverage/gap/permission fail-closed, alternating atomic invalidation, 5pp/10pp fairness, liveness, dispatch-frozen D0/D1/mid-run/checker/greedy/pybind hash contract.
- canonical `py_compile` PASS, prepare-only schema v2 smoke PASS, full unittest `204/204 PASS`, `git diff --check` PASS.
- 실제 CPU qualification, solver fixed-time, hard-10, `prob_23`, CPU stress, Phase 0 retry, Phase 1은 실행하지 않았다.

preservation:

- 기존 두 Phase 0 retry artifact와 이전 hash-fix artifact의 recursive `SHA256SUMS`를 재검증했고 self hash `83ad801d22aa954e217a2dd833cd3fbea3349e1fcbc7bfbed0cf4477b764db88`, `52ac85e2c9b87292154801e8517d394e05241253b1b8b6dd11189b8de6c2640d`, `cfb7fc57202a16d2ca2b93eeb38ee9ac889cfee5d4c531a584d962b06c05b229`를 유지했다.

artifact: `artifacts/ogc_sage/quality-recovery/p0-cpu-contract-fix-20260716T014245Z-302f9e8f`

SHA256SUMS: `2c187bd21306d6bb212de0510facf88e7ecc062547b2005af4ae4fe8cc1f1f2b` (14개 항목 전체 재검증 PASS)

다음 단계 허용 여부: **fresh Phase 0 retry 허용**. 이 append 뒤의 새 Phase 0 문서와 execution/source identity를 fresh preflight에서 동결해야 한다. Phase 1은 Phase 0 `PASS` 전까지 불가하다.

### 2026-07-16 — p0-20260716T015448Z-302f9e8f-retry-after-cpu-contract-fix-1

판정: **BLOCKED**

source/config/data/environment:

- branch/HEAD는 `codex/performance-optimization-plan` / `302f9e8f0d3cd5eba8f68f633d1a2ba9013396aa`로 dispatch 값과 일치했다.
- pre-append top plan `fdecaa193a1771b461ae16cacf2289cee67858bf120b2001895d94ed36fad6d2`, scheduler protocol `647d8e17db5e0e02d32cfa6c6ea82872a448fb492425eb12dbd26baaccbf4325`, Phase 0 contract `d6e81e24b410509f0eecbc3b138aea850fbb9df95c3894c982880fc64c553d58`, Phase 0 harness `26da658cc084b2a17558baf32675910c762c53f57e4f5229f2467da8eb8b4753`가 exact match였다.
- execution aggregate `06712feb1f03cbddfdfae478c35dba72faede7336030c19ddae5aa1944e2fa53`, source snapshot `237017a4b5bc3e7881ac993bb9bb5a82c34751aee9babd2d3786721b6fc11c93`, matrix identity `133bf8b47ab851a4726e6803f6949cc8d2a65fd81c8a5f3c3d6691ae1be310c9`, CPU contract `b102f6838e751849727451a681a5650e0621c07f4a3fc8e93c610445dd468fd8`가 dispatch 값과 일치했다.
- frozen Python config `f18a0d3ee7a5f2c76ef59bad6f53c0144288b2134fc3252d59ac9dc477f5924a`, dataset `c093fb98b17c714ea5b9c8c829d4bce24dd378f0b558196ce0961feb68b34e1f`, hard-10 manifest `5499cf993f5018dceca0464dfcaf0c328e129cb7ed4946c79cc4e1860127d3b7`, `prob_23` `1b0e48c383157c3140d4197f1bb3326f9e67e0ded0d8345bcc35d57186b94738`, native binary `38352e2040e311822ffa591e6d98a0b7754299bc935cd521f9778b508692232d`, benchmark harness `3f11bffd338491b3df09d6b933c51f24a1900e27e1db05d1b34ab9fad49e4b59`가 exact match였다.
- CPU contract fix artifact `p0-cpu-contract-fix-20260716T014245Z-302f9e8f`는 self hash `2c187bd21306d6bb212de0510facf88e7ecc062547b2005af4ae4fe8cc1f1f2b`, recursive `14/14 PASS`로 재검증했다.

변경 범위:

- 새 immutable Phase 0 artifact를 생성·봉인하고 이 실행 기록만 append했다.
- solver algorithm/default/config/data/native binary/benchmark behavior를 수정하지 않았고 production default Python, native/MIP/interlock OFF를 유지했다. 기존 dirty/untracked 변경과 기존 artifact를 삭제·덮어쓰기·변경하지 않았다.

tests:

- full unittest `204/204 PASS`; `git diff --check` PASS.
- 보호 hash, official daily-40 `40/40`, hard-10 `10/10 unique`, native import/API 및 dispatch-frozen identity 검증 PASS.

fixed-work: Phase 0 신규 fixed-work 없음.

fixed-time:

- 새 계약의 1초 cumulative system CPU delta를 10 interval / `10.0s` 수집했다. logical core `10`, temporal coverage `100%`, 최대 sample gap `1.010131375s`, measurement subtree 차감은 complete였다.
- process-tree access coverage가 `73.475035%`로 요구 `95%`에 미달해 구조적 measurement coverage hard gate가 `BLOCKED`를 반환했다.
- 외부 capacity 자체는 평균 available `85.043182%`, 5초 rolling available 최저 `82.352677%`, 평균 external busy `14.956818%`, p95 `23.127737%`였다. raw `ps` 값과 process 이름은 diagnostic only이며 gate에 사용하지 않았다. consecutive clean-heartbeat 규칙도 사용하지 않았다.
- 최초 sandboxed qualification 시도는 diagnostic-only `ps` 실행이 `PermissionError`로 실패해 gate record를 만들지 못했고, 승인된 동일 명령 재실행의 authoritative system-delta evidence만 판정에 사용했다. 어떤 외부 process도 종료하거나 변경하지 않았다.
- hard-10 Python 60초 × 3 seeds: planned `30`, executed `0`; `prob_23` 120초 atomic alternating 2 pairs: planned `4` runs, executed `0`. partial matrix를 PASS로 승격하지 않았다.

quality: current matrix/pair가 없어 `INCONCLUSIVE / NOT RUN`; task gate는 process-tree access coverage 구조 실패에 따른 `BLOCKED`다.

artifact: `artifacts/ogc_sage/quality-recovery/p0-20260716T015448Z-302f9e8f-retry-after-cpu-contract-fix-1`

SHA256SUMS: `8f9f23884dbdb29ebbcec97a332e0e6cfba6cf3be7848215be62d2037332a601` (20개 항목 recursive 재검증 PASS)

다음 단계 허용 여부: **불가**. Phase 1은 생성·실행하지 않았다. fresh Phase 0 retry 전 host tick/process-tree 측정 access coverage를 `>=95%`로 복구해야 한다.

### 2026-07-16 — p0-process-coverage-fix-20260716T022130Z-302f9e8f

판정: **FIXED / PASS**

source/config/data/environment:

- branch/HEAD는 `codex/performance-optimization-plan` / `302f9e8f0d3cd5eba8f68f633d1a2ba9013396aa`로 유지됐다.
- artifact 봉인 전 top plan `3a4f1d9fc043cd496930e3143e2a16357ab91b7293f9e660de0ffb327e31c101`, scheduler protocol `f0d55ce3667171cf535ca26676d74f7f0a791b3fee74a5bacd5dacd95b85a8c4`, 이 Phase 0 contract `c9aeaa547f9ad69ad082aa9a5b6203a693cffd05625f6d26a82b979b69723952`, harness `2862e61c68b5561f8b4f8d2df45f0416468f88870433611dfe1e33b28ee3a32d`를 기록했다.
- pre-append execution aggregate `e03bce22213056ba1e177d4db7f1f93d065c8fe45bcd50e29dcc5c6af79e5697`, source snapshot `236afdc81818d9d79af5da14624e70d826ea3096589b8f527920119c4497da8a`, matrix identity `ed3b3977875721b068c816629a46a93c2fb82050f2a7c90046c9608eedb86d75`, CPU contract `3fa9b6c3b2d27312402afb478f0bb6b233789f160469fb7892a12b926222ffee`다.
- frozen config `f18a0d3ee7a5f2c76ef59bad6f53c0144288b2134fc3252d59ac9dc477f5924a`, dataset `c093fb98b17c714ea5b9c8c829d4bce24dd378f0b558196ce0961feb68b34e1f`, hard-10 `5499cf993f5018dceca0464dfcaf0c328e129cb7ed4946c79cc4e1860127d3b7`, `prob_23` `1b0e48c383157c3140d4197f1bb3326f9e67e0ded0d8345bcc35d57186b94738`, native binary `38352e2040e311822ffa591e6d98a0b7754299bc935cd521f9778b508692232d`, benchmark harness `3f11bffd338491b3df09d6b933c51f24a1900e27e1db05d1b34ab9fad49e4b59`는 미변경이다.

변경 범위:

- 허용된 네 파일만 변경했다: 상위 계획, scheduler protocol, 이 Phase 0 contract, `experiments/ogc_sage/verify_quality_p0.py`.
- schema v3에서 gating completeness를 `host_tick_valid`, `temporal_coverage`, `max_sample_gap_seconds`, `measurement_subtree_identity_complete`, `measurement_subtraction_complete`로 분리했다.
- global PID-count `process_access_coverage`와 CPU-time attribution coverage를 diagnostic-only로 분리했다. host-derived aggregate external busy에는 inaccessible/unattributed non-measurement workload가 계속 남는다.
- `AccessDenied`, `NoSuchProcess`, `ZombieProcess`, `OSError`, system tick 오류, temporal sampling 오류를 별도 count/evidence로 기록한다. unrelated attribution 오류는 gate가 아니며 measurement root/등록 subtree의 접근·identity·CPU subtraction 실패만 structural hard failure다.
- solver algorithm/default/config/data/native binary/benchmark behavior와 README/pybind 계획은 변경하지 않았고 새 tracked source/test 파일을 만들지 않았다.

tests:

- synthetic focused `13/13 PASS`: 최신 `73.475%` PID fixture, 50/75/100% reachability independence, unattributed CPU aggregate 보존, temporal `95%`/`94.99%` 및 gap `2.5s`/초과 경계, qualification/runtime 상태 분기, 네 process error taxonomy, 0.5-core/5s, 70/50/30, atomic pair와 5pp/10pp, raw ps diagnostic-only, D0/D1/mid-run/checker/greedy/pybind hash regression, schema v3.
- canonical `py_compile` PASS, prepare-only schema v3 smoke PASS, full unittest `204/204 PASS`, `git diff --check` PASS.
- 실제 CPU qualification, solver fixed-time, hard-10, `prob_23`, CPU stress, Phase 0 retry, Phase 1은 실행하지 않았다.

preservation:

- 기존 다섯 artifact의 recursive `SHA256SUMS`와 self hash `83ad801d22aa954e217a2dd833cd3fbea3349e1fcbc7bfbed0cf4477b764db88`, `52ac85e2c9b87292154801e8517d394e05241253b1b8b6dd11189b8de6c2640d`, `cfb7fc57202a16d2ca2b93eeb38ee9ac889cfee5d4c531a584d962b06c05b229`, `2c187bd21306d6bb212de0510facf88e7ecc062547b2005af4ae4fe8cc1f1f2b`, `8f9f23884dbdb29ebbcec97a332e0e6cfba6cf3be7848215be62d2037332a601`를 재검증했다.

artifact: `artifacts/ogc_sage/quality-recovery/p0-process-coverage-fix-20260716T022130Z-302f9e8f`

SHA256SUMS: `2fa5dca11e89e59582d87122e317dbcf000e9515568ce083a2aa402a8ac0751c` (10개 항목 recursive 재검증 PASS)

다음 단계 허용 여부: **fresh Phase 0 retry 허용**. 이 append 뒤의 새 top/scheduler/Phase 0/harness/execution/source/matrix/CPU contract identity를 fresh preflight에서 동결해야 한다. Phase 1은 Phase 0 `PASS` 전까지 불가하다.
