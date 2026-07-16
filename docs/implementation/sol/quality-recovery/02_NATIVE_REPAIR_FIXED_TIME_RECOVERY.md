# Phase 2 — Native repair fixed-time 품질 회복

중요도: 최우선  
선행 gate: Phase 1 PASS  
회귀 anchor: `prob_23`, 120초, Python `49,102,302` 대 native `89,478,006`  
production default: gate 완료 전 Python

## 1. 문제와 목표

현재 C++ 경로는 후보 생성·prefilter를 가속하지만 `UNKNOWN` geometry의 정확 판정, transactional commit, finalization은 Python/Shapely에 남아 있다. `prob_23` 관측에서는 native 후보/pair가 커져 exact resolve와 Python finalize가 증가했고 ALNS가 50회에서 22회로 줄어 objective가 약 82% 악화됐다.

목표는 C++ kernel의 micro-benchmark 속도가 아니라 동일 wall-clock에서 다음을 만족하는 것이다.

- native 요청이 Python 대비 최종 objective를 악화시키지 않음
- boundary와 exact resolve가 탐색량을 잠식하지 않음
- candidate/order/score/acceptance 의미론이 명시적임
- deadline 시 중복 Python full repair를 실행하지 않고 validated incumbent를 보존함
- native가 전체 hard-set에서 이길 때만 사용 가능 상태가 됨

## 2. 범위와 비범위

허용:

- C++ candidate generation/prefilter, pybind packing/return 구조
- bounded/streaming top-k, per-call/per-iteration budget
- conservative pair classification과 UNKNOWN batching
- native session/state reuse, copied bytes 감소
- fallback/deadline 정책과 telemetry
- benchmark-only parity/dominance variant

금지:

- Shapely와 다른 approximate 판정을 exact feasible로 승격
- Python/native 서로 다른 candidate cap/order/RNG로 속도 비교
- deadline 뒤 Python full repair를 다시 실행해 outer timeout 유발
- Phase 3 ALNS 정책을 이 phase 수정에 섞음
- fixed-work speedup만으로 default ON

## 3. 순차 실행 절차

### 2.1 회귀 pair 재현

1. Phase 0의 유효한 `prob_23` pair와 identity를 읽는다.
2. native extension을 현재 source로 rebuild하고 source/binary hash를 맞춘다.
3. CPU gate가 깨끗한지 확인한다.
4. Python/native alternating pair를 재실행한다.
5. objective/iterations뿐 아니라 각 repair event의 rows, returned candidates, pairs, UNKNOWN, exact calls, pack/bind/prepare/exact/finalize/fallback을 저장한다.

재현되지 않으면 즉시 성공으로 보지 않는다. source 차이, binary 차이, CPU, random trajectory 중 무엇이 결과를 바꿨는지 diagnose한다.

### 2.2 비용 증폭 분해

각 native repair call에 대해 다음 비율을 계산한다.

- returned/generated candidate ratio
- pair/candidate 및 UNKNOWN/pair
- exact calls/UNKNOWN
- copied bytes/candidate
- `(bind + exact + finalize) / total repair`
- fallback 후 중복 Python work
- repair 1회가 소비한 remaining search budget

상위 10개 비용 call과 quality divergence가 처음 생긴 iteration을 연결한다. 총합만으로 원인을 선택하지 않는다.

### 2.3 수정 우선순위

fresh fix session 하나는 아래 한 항목만 처리한다.

1. unbounded candidate/pair materialization 제거: deterministic streaming top-k와 hard cap
2. C++에서 reject/free를 더 일찍 확정하되 UNKNOWN exact contract 유지
3. Python boundary batching 및 compact POD 반환, state packing reuse
4. exact resolve 중 조기 pruning과 budget guard
5. finalize/commit 중복 재계산 제거
6. deadline/invalid/exception에서 full Python duplicate fallback 방지

cap은 환경에 따라 결과가 달라지는 wall-clock cut이 아니라 deterministic work unit으로 정의한다. canonical tie-break를 보존한다.

### 2.4 Fixed-work parity 및 performance

다음 순서로 검증한다.

1. native fixture 생성 및 source snapshot Stage 5 확인
2. capped 및 unbounded parity
3. conservative prefilter false-free 0
4. integration/fallback/deadline/invalid/exception
5. P7 fix regression
6. 현재 전체 baseline tests

기존 검증 script를 재사용하되 새 artifact와 현재 binary hash를 사용한다.

```bash
<python> experiments/ogc_sage/verify_native_p3_fixture.py ...
<python> experiments/ogc_sage/verify_native_p4_prefilter.py ...
<python> experiments/ogc_sage/verify_native_p5_integration.py ...
<python> experiments/ogc_sage/verify_native_p7_fix1.py ...
cd baseline && <python> -m unittest discover -s tests -p 'test_*.py'
```

fixed-work는 후보 digest, 순서, score, placement/serialization/objective digest와 Stage를 비교한다. 의도적 pruning variant는 Python에도 같은 pruning contract를 적용해 비교한다.

### 2.5 Fixed-time screening

1. `prob_23` 120초×seed `20260710`을 Python/native alternating order로 실행한다.
2. 다음으로 hard-6 또는 비용 증폭 상위 instance 6개를 60초×3 seeds로 실행한다.
3. screening 통과 후 frozen hard-10 60초×3 seeds를 실행한다.
4. profiler를 켠 run은 fixed-time gate에서 제외한다.
5. native-only telemetry 수집이 Python baseline에도 동일 overhead를 주는지 확인한다.

### 2.6 최종 native 결정

- 모든 gate 통과: native를 cumulative benchmark candidate로만 승격한다.
- 품질은 같지만 환경/packaging gate 미완료: production Python 유지, `LOCAL_GO`로만 기록한다.
- 하나라도 핵심 gate 실패: native default OFF를 명시적 `NO-GO`로 동결한다. C++ 코드는 유지할 수 있으나 release path가 자동 선택하지 않는다.

## 4. 필수 지표

- `prob_23`: objective/components, iterations, accepted/new-best, primal integral
- native: requested/actual backend, calls/success/fallback reason/deadline/invalid/exception
- work: candidate rows/returned, pair/UNKNOWN/free/exact counts
- time: pack/bind/kernel/exact/finalize/fallback, total repair, boundary fraction
- parity: candidate/placement/serialization/objective digest
- matrix: W/T/L, median/p90/worst, Borda, weighted components

## 5. Hard gate

다음을 모두 만족해야 native `GO`다.

- Stage 5/parity/trace/timeout 공통 gate 전부 PASS
- fixed-work parity 또는 명시적 동일 pruning contract PASS
- conservative prefilter false-free 0
- 정상 native path의 Python full fallback 0; exception path는 incumbent 보존
- `prob_23` 120초 native objective `<=` paired Python objective
- `prob_23` ALNS iterations가 Python의 90% 이상이거나, 미달 시 primal integral과 final objective가 모두 Python보다 나음
- hard-10 Win > Loss, median delta < 0, Borda non-worse
- hard-10에서 설명되지 않은 objective `>10%` 악화 0
- boundary+exact+finalize가 repair wall-clock을 지배하는 회귀가 제거되고, Python 대비 fixed-time repair throughput이 개선
- 전체 baseline tests PASS

kernel 18.3배 또는 exact call 34.57% 감소 같은 과거 fixed-work 수치는 이 gate를 대체하지 않는다.

## 6. 실패 분기

- candidate/pair 폭증 → streaming/cap diagnose
- UNKNOWN/exact 지배 → classifier/batching/pruning diagnose
- finalize 지배 → Python object materialization diagnose
- iteration은 회복, 품질 악화 → Phase 3 전에 candidate/order/score divergence diagnose
- dominance guard가 Python SA와 달라짐 → Phase 3으로 명시적 이관, native default OFF 유지
- 외부 CPU overlap → 결과 폐기, 코드 수정 없이 fresh retry
- Ubuntu/ABI/package 실패 → 품질 GO와 release GO를 분리하고 production OFF

## 7. 산출물

```text
artifacts/ogc_sage/quality-recovery/p2-<run-id>/
  identity.json
  binary-manifest.json
  fixed-work.json
  call-amplification.json
  prob23/{python.json,native.json,comparison.json}
  hard10/{raw.jsonl,summary.json,comparison.json}
  gate.json
  SHA256SUMS
```

## 8. Worker 실행 프롬프트

```text
역할: quality recovery Phase 2 native repair worker.

Phase 0/1 PASS artifact와 cumulative config를 사용하고,
docs/implementation/sol/quality-recovery/02_NATIVE_REPAIR_FIXED_TIME_RECOVERY.md를 순서대로 실행하라.

prob_23 120초 회귀를 같은 source/config/data/binary/CPU 계약에서 먼저 재현하고, rows/pairs/UNKNOWN/exact/boundary/finalize/fallback 중 가장 큰 원인 하나만 수정하라. fixed-work parity와 전체 tests 후 prob_23, hard subset, hard-10 fixed-time 순으로 검증하라. Phase 3 ALNS 정책을 섞지 말고 production Python default를 유지하라.

gate 미달이면 native NO-GO/OFF를 명시하고 같은 session에서 다른 대규모 fix를 시작하지 말라. 새 artifact와 실행 기록을 남기고 commit/push하지 말라.
```

## 9. 실행 기록

아직 실행되지 않음.
