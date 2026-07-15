# 0단계 — 외부 240-run invocation telemetry 실행

이 문서는 Codex 세션 밖의 일반 터미널에서, 새 invocation telemetry를 포함한 original baseline matrix를 실행·동결하는 절차다. 실행 중 source를 수정하거나 Phase 1 구현을 시작하지 않는다.

## 실행 대상

- variant: `heuristic_lns`
- instances: official daily-40
- budgets: 정확히 `60`, `180` seconds
- seeds: 정확히 `20260710`, `20260711`, `20260712`
- 총 실행 수: `40 × 2 × 3 = 240`

180초 record에는 `anchor`, `extension` 두 invocation이 각각 남고, 60초 record에는 `anchor` 하나만 남아야 한다. 이 240-run이 완료되면 hard-10의 60초 결과도 이미 포함되어 있으므로 지금 별도 hard-10 run을 추가하지 않는다.

## 실행 전 확인

1. 다른 benchmark/solver process를 실행하지 않는다. 동시 부하는 phase time을 오염시킨다.
2. 현재 worktree의 solver source를 실행 중 수정하지 않는다.
3. Python 환경을 확인한다. 기본값은 `ogc-2026`이지만 다른 환경이면 `PYTHON_BIN`을 명시한다.

## 일반 터미널 명령

```bash
cd /Users/brown/workspace/ogc/sol-native-implementation

RUN_ID=phase0-telemetry-$(date -u +%Y%m%dT%H%M%SZ)-$(git rev-parse --short=12 HEAD) \
bash experiments/ogc_sage/run_phase0_telemetry_matrix.sh
```

예상 경로는 다음과 같다.

`artifacts/ogc_sage/performance/phase0/$RUN_ID/`

완료 시 그 디렉터리에 `raw.jsonl`, `summary.json`, `SHA256SUMS`, source pre/post fingerprint, 후보 manifest, `PHASE0_SHA256SUMS`가 남는다. 스크립트는 source fingerprint가 동일할 때만 후보 manifest를 `HARD10_MANIFEST.json`으로 복사한다.

## 중단 후 재개

같은 `RUN_ID`로 다시 실행한다. benchmark는 이미 끝난 유효 terminal run key를 건너뛴다.

```bash
cd /Users/brown/workspace/ogc/sol-native-implementation

RUN_ID=<이전_RUN_ID> \
bash experiments/ogc_sage/run_phase0_telemetry_matrix.sh
```

`.run-lock`이 남아 있으면 먼저 실제 benchmark process가 없는지 확인한다. process가 없고 비정상 종료가 확인된 경우에만 lock을 제거한다. source/environment fingerprint가 다르면 재개하지 않고 새 `RUN_ID`로 처음부터 실행한다.

## 완료 판정

다음 모두 충족되어야 Phase 0의 새 기준선으로 인정한다.

- `summary.json`: 240 unique terminal records, 240/240 Stage 5, parity/exception/outer-timeout/longer-budget regression 0, `gate_pass=true`
- 모든 60초 record: `lns_invocations == [anchor]`
- 모든 180초 record: `lns_invocations == [anchor, extension]`
- source pre/post fingerprint 동일
- `PHASE0_SHA256SUMS` 검증 성공
- 새 manifest가 `measured_from_direct_invocations`를 telemetry source로 기록

완료 전에는 Phase 1을 시작하지 않는다. 완료 artifact와 manifest hash를 새 세션에 전달한 뒤, 그 세션에서 Phase 1 baseline/candidate를 동일 세션 paired run으로 비교한다.
