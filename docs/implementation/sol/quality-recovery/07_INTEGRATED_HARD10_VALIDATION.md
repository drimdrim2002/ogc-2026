# Phase 7 — 누적 후보 hard-10 통합 검증

중요도: 통합 release gate  
선행 gate: Phase 0~6 controller audit 완료  
solver 코드 수정: 금지

## 1. 목표

각 phase에서 개별 승격된 변경이 결합됐을 때도 성능과 품질이 유지되는지, frozen Python baseline보다 competition-aligned 지표에서 우세한지 검증한다. 이 phase는 알고리즘을 고치는 곳이 아니라 release-scale all-40 실행 전에 결합 회귀를 찾는 곳이다.

## 2. 비교 대상 동결

최소 두 variant를 동결한다.

- `frozen-python`: Phase 0의 현재-source Python default
- `cumulative`: Phase 1~6에서 gate를 통과한 최종 config

native, MIP, interlock이 조건부 ON이면 config와 binary hash를 명시한다. 두 개 이상의 조합 후보가 남아 있으면 본 matrix 전에 60초 hard-10 screening으로 하나를 선택하고 선택 규칙을 사전에 `matrix-contract.json`에 기록한다.

## 3. 순차 실행 절차

### 7.1 Preflight 및 불변성 감사

1. branch/HEAD/dirty source aggregate hash와 각 phase artifact hash를 검증한다.
2. protection hashes, dataset, hard-10 manifest, config hash, native binary hash를 확인한다.
3. 전체 baseline tests를 실행한다.
4. CPU snapshot 두 개에서 외부 지속 `>=25%` process가 없는지 확인한다.
5. benchmark 순서와 randomization seed를 matrix 실행 전에 동결한다.

### 7.2 Matrix 계약

다음 exact matrix를 실행한다.

- instances: frozen hard-10 10개
- budgets: 60, 180초
- seeds: `20260710`, `20260711`, `20260712`
- variants: frozen-python, cumulative
- total: `10 × 2 × 3 × 2 = 120` runs

같은 scenario의 두 variant를 한 pair로 보고, pair 순서를 교대한다. 60/180 결과를 섞어서 유리한 run만 다시 실행하지 않는다.

### 7.3 실행 중 감시

1. 각 run 후 terminal record와 Stage를 확인하되 objective를 보고 조기 중단하지 않는다.
2. 외부 CPU overlap, outer timeout, crash가 발생하면 해당 pair 또는 사전 정의 batch 전체를 invalid로 표시한다.
3. invalid batch는 동일 run ID에 덮어쓰지 않고 새 immutable retry artifact로 전부 재실행한다.
4. worker는 solver source/config를 수정하지 않는다.

### 7.4 Correctness 및 quality 분석

1. 120/120 terminal/unique/expected와 Stage 5를 확인한다.
2. internal/checker component parity, trace monotonicity, longer-budget monotonicity를 검증한다.
3. instance/seed/budget별 paired relative delta를 계산한다.
4. W/T/L, per-instance W/T/L, Borda/rank, median/p90/worst, weighted components를 보고한다.
5. anytime primal integral, time-to-first-improvement, iterations/accepted/new-best를 비교한다.
6. `prob_23`이 hard-10에 없더라도 별도 120초×3 seed anchor를 실행해 Phase 2 회귀가 재발하지 않았는지 확인한다.

### 7.5 독립 audit용 freeze

raw, summary, comparison, gate를 닫은 뒤 recursive SHA-256를 생성한다. scheduler가 hash와 수치를 독립 검증하기 전 Phase 8을 시작하지 않는다.

## 4. Hard gate

- 전체 tests PASS
- expected run 120/120, missing/duplicate 0
- Stage 5 120/120, violations/exception/outer timeout/checker failure 0
- objective/component parity `<=1e-6`, trace regression 0
- 60초와 180초 각각 cumulative Win > Loss, median delta < 0, Borda non-worse
- 전체 p90 paired delta `<=0`; worst regression `<=10%`이며 모든 positive tail에 원인 설명
- same instance/seed 60→180 objective regression 0
- `prob_23` 120초 각 seed catastrophic regression 0, median non-worse
- conditional ON feature가 실제 activation/success를 보이고 throughput을 붕괴시키지 않음
- CPU audit와 artifact SHA256 PASS

## 5. 실패 처리

통합 worker는 코드를 수정하지 않는다.

1. failing scenario와 최초 divergence/owner phase를 식별한다.
2. scheduler가 fresh integrated-diagnose session을 만든다.
3. owner phase의 fresh fix session에서 한 원인만 수정한다.
4. owner focused gate를 fresh retry session에서 재실행한다.
5. Phase 7 전체 120-run matrix를 새 run ID로 다시 실행한다.

일부 성공 row를 새 matrix에 복사하지 않는다. 문제가 feature interaction이면 두 feature 조합을 먼저 OFF/ON ablation한 뒤 최소 승리 조합으로 되돌린다.

## 6. 산출물

```text
artifacts/ogc_sage/quality-recovery/p7-<run-id>/
  identity.json
  matrix-contract.json
  commands.json
  cpu-audit*.json
  raw.jsonl
  summary.json
  comparison.json
  prob23.json
  gate.json
  SHA256SUMS
```

## 7. Worker 실행 프롬프트

```text
역할: quality recovery Phase 7 integrated hard-10 validation worker. 이 session은 solver 코드를 수정하지 않는다.

Phase 0~6의 controller-audited config/artifact를 읽고,
docs/implementation/sol/quality-recovery/07_INTEGRATED_HARD10_VALIDATION.md를 그대로 실행하라.

frozen-python과 cumulative의 hard-10 60/180초×3 seed, 총 120-run paired matrix와 prob_23 anchor를 CPU-clean 환경에서 실행하라. correctness, W/T/L, Borda, median/p90/worst, components, primal integral, longer-budget regression을 판정하라.

실패하면 코드를 고치지 말고 failing scenario, 최초 divergence, owner phase를 보고하라. immutable artifact와 실행 기록을 남기고 commit/push하지 말라.
```

## 8. 실행 기록

아직 실행되지 않음.
