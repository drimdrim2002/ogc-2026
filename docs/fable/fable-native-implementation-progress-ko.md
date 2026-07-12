# Fable 네이티브 솔버: S0-S6 진행 및 실행 계약

최종 업데이트 날짜: 2026-07-12(아시아/서울)

계획 기준: `78ef82ece960ac686a6f5c41497a13c2217ffab5`

구현 브랜치/작업 트리: `/Users/brown/workspace/ogc/fable-native-implementation`의 `fable-native-implementation`

계획 상태: 완료; 구현 상태: S0 진행 중

## 1. 목표, 비목표 및 제출 준비 정의

목표는 완전히 검증된 실행 가능 현직해를 항상 보호하고 S0-S6을 통해 배정, 적재 및 타이밍을 개선하는 체커 권위 기반 anytime solver를 만드는 것이다. 이 문서는 변경 가능한 상태 보드, 추가 전용 구현 이력, 아키텍처 계약, 증거 색인 및 재개 지점이다. [`implementation-steps/`](implementation-steps/) 아래의 7개 단계 계획이 실행 명세다.

이 계획은 `baseline/utils.py` 또는 `baseline/baseline_greedy.py` 변경, 체커/안전 게이트 완화, 근사 기하를 오라클로 사용, 숨겨진 인스턴스 동작 하드코딩, A/B 게이트 없이 선택 기능을 활성화하는 일을 허용하지 않는다. 계획 완료는 구현 완료가 아니다.

"제출 준비"는 현재 커밋에서 다음을 모두 의미합니다.

1. `baseline/myalgorithm.py`는 필수 시그니처 `algorithm(prob_info, timelimit)`를 가지며, 수정되지 않은 `baseline/utils.py::check_feasibility`가 수용한 작업 딕셔너리만 반환한다.
2. 모든 유효 인스턴스(아래 정의)에 대해 현재 필수 파이프라인은 마감 시한, 백엔드 예외, 후보 거부 및 선택 기능 실패 상황에서도 검증된 현직해 대체 경로를 제공한다.
3. 가장 최근 완료 단계까지의 필수 게이트는 통과 상태다. 선택인 S5 포트폴리오 또는 S6 인터록은 이득 게이트 실패 뒤에 비활성화할 수 있으며, 이는 하위 계층 solver를 무효화하지 않는다.
4. 정확한 인터프리터, 소스 커밋, dirty 상태, 인스턴스 해시, 시드, 명령, 기능 플래그, 체커 결과 및 소요 시간을 완전한 증거로 기록한다.
5. 패키징 리허설에서는 루트 수준 `myalgorithm.py`, 상대 런타임 경로만 생성되고, 수정된 체커/참조 파일이 없고, 로컬 데이터나 자격 증명이 없고, 금지된 확장자가 없고, 15MB 이하의 zip이 생성됩니다.

**유효 인스턴스**는 체커 필수 스키마를 갖고, 모든 블록이 하나 이상의 bay에서 적어도 하나의 경계 내 방향을 가진다. 체커와 명세는 잘못된 형식 또는 물리적으로 불가능한 입력이 해를 가져야 한다고 말하지 않는다. S0 사전 검사는 모든 공식 학습 입력에 대해 이 조건을 증명해야 한다. 실패하면 하니스는 해당 블록을 명시한 입력 오류로 종료한다. 코드는 T0가 불가능한 인스턴스를 풀 수 있다고 주장해서는 안 된다.

## 2. 권위와 불변성

권위, 높은 것부터:

1. `baseline/utils.py` 실제 동작.
2. [`../OGC2026_Problem_Analysis.md`](../OGC2026_Problem_Analysis.md).
3. [`solver-design-en.md`](solver-design-en.md) §2의 체커 의미 규칙.
4. `solver-design-en.md`의 나머지 디자인 및 로드맵.
5. [`solver-implementation-plan.md`](solver-implementation-plan.md), 초안으로 처리됩니다.
6. 인스턴스 세트 및 실험 규칙에만 사용되는 역사적/더 이상 사용되지 않는 문서.

안전 및 의미론적 불변성:

- 정수 배치 좌표와 날짜를 출력한다. 비인터록 모드에서 점유는 `dwell=max(P,1)`인 반열린 구간 `[a,e)`이다.
- 같은 날의 작업은 모두 ENTRY보다 EXIT가 먼저다. 체커 Stage 5에서는 같은 유형 내 순서도 의미론적으로 유효하다.
- 경계 접촉과 면적 0의 다각형 접촉은 적법하다. 체커를 통한 Shapely 동작이 최종 기하 오라클이다.
- 체커 실행 가능 직렬화 해만 `Incumbent`에 들어갈 수 있으며, 교체는 원자적이고 상대 허용오차 `1e-9` 내에서 체커 목적함수를 엄격히 개선한다.
- 내부 목적함수와 표적 검증은 동등성이 증명될 때까지 진단/필터다. 모든 현직해 교체는 전체 공식 검사를 받는다.
- 안전, 체커 동등성, 실행 불가능성 또는 현직해 안전 실패는 다음 필수 단계를 차단한다. 계속 진행하기 위해 기준을 완화하지 않는다.
- S5 이득 실패는 포트폴리오를 비활성화하고 `GATE_FAILED_DISABLED`를 기록합니다. S4 단일 프로세스 솔버를 무효화하지 않습니다. S6 인터록 이득 실패로 인해 인터록이 비활성화됩니다. 필수 강화 및 포장이 여전히 완료될 수 있습니다.
- Sn을 완성하면 Sn+1에 필요한 모든 아티팩트가 제공됩니다. 어떤 단계 게이트도 이후 단계에서 소유한 코드나 계측을 사용하지 않습니다.

모든 단계 계획에서 사용하는 정확한 체커 기준점은 `baseline/utils.py:252-266`의 `Bay.contains_block`, `461-543`의 동일 레이어 충돌, `603-724`의 입고 OBS, `727-819`의 출고 OBS, `1028-1136`의 ENTRY/EXIT 각 1회 및 타이밍, `1144-1158`의 좌표 반올림과 겹침, `1160-1198`의 입고 present-set, `1200-1232`의 출고 present-set, `1234-1277`의 충돌/경계, `1279-1386`의 순서 있는 재생, `1388-1421`의 float Z1/Z2/Z3이다.

## 3. 저장소 평가

계획 기준에서 저장소에는 체커, 참조 greedy 솔버, 한 줄 `baseline/myalgorithm.py` 위임, 테스터 UI, 하나의 추적된 10블록 예제, 설계 문서 및 환경 정의가 포함되어 있습니다. 솔버 패키지, 자동화된 테스트, 사내 CLI 하니스, 전용 학습 데이터, 증거 저장소, 제출 빌더 또는 S0-S6 구현이 없습니다. 기록 문서에는 40개의 학습 사례와 안정적인 `smoke-3`/`dev-10` ID가 설명되어 있지만 이 깨끗한 대상에는 무시된 로컬 데이터가 없습니다. 필요한 인터프리터는 `/opt/homebrew/Caskroom/miniforge/base/envs/ogc-2026/bin/python`에 있으며 Python 3.12.13, numpy 2.1.3, Shapely 2.1.2, OR-Tools 9.15.6755, gurobipy 13.0.2 및 psutil 7.2.2를 보고합니다. `pytest`는 설치되지 않았으므로 계획에서는 표준 라이브러리 `unittest`를 사용하고 모호한 `python` 실행 파일에 의존하지 않습니다.

재사용 가능한 자산:

- `baseline/utils.py`: oracle 및 사용 가능한 체커 프리미티브.
- `baseline/baseline_greedy.py`: 동결된 비교/참조 및 긴급 행동 연구에만 해당됩니다.
- `baseline/run_myalgorithm.py`: 로컬 수동 진단; 새 하니스는 삭제하지 않고 이를 대체합니다.
- `alg_tester/example/example_B2_b10.json`: 연기/예제 사례를 추적했습니다.
- `docs/deprecated/operating-plan.md:37-66`: `dev-10` 및 `smoke-3` 멤버십을 측정했습니다.
- `ogc2026_env.yml`: 서버 호환 종속성 계약.

S0 구현 전 전제 조건: 공식 40개 학습 JSON 파일을 커밋하지 않고 `data/train*/` 또는 `alg_tester/example/train*/` 아래에 복원한다. 각 `prob_1`…`prob_40`에 정확히 하나의 바이트 동일성을 확인한다. 명시적 프로젝트 인터프리터 실행 파일을 유지한다. 학습 데이터 누락은 S0 학습 게이트를 차단하지만 첫 S0 단위 슬라이스는 차단하지 않는다.

## 4. 최종 파일 맵 및 관심사 분리

```text
baseline/
  myalgorithm.py                 # submission entry, thin safety shell
  solver/                        # production only
    __init__.py config.py instance.py geometry.py state.py checker_adapter.py
    validate.py serialize.py trivial.py budget.py incumbent.py entry.py
    assign.py construct.py exact.py gurobi_backend.py cpsat_backend.py
    retime.py alns.py interlock.py portfolio.py
  tests/                         # unittest; never packaged
    __init__.py fixtures.py test_contract_semantics.py
    test_geometry_kernel.py test_state_parity.py test_validate_parity.py
    test_serialize.py test_trivial.py test_budget_entry.py
    test_harness_schema.py test_harness_process.py test_assign_v1.py test_construct.py
    test_exact_backends.py test_retime.py test_alns.py
    test_assignment_refinement.py test_portfolio.py test_interlock.py
    test_packaging.py
  harness/                       # development-only in-house harness
    __init__.py cli.py schema.py selectors.py runner.py checker.py
    compare.py gates.py report.py process.py package.py
benchmarks/
  manifests/                    # committed selectors and synthetic/stress recipes
    training.json smoke-3.json dev-10.json synthetic.json dense.json stress.json
  evidence/                     # generated, ignored except README; never packaged
    <stage>/<slice>/<run_id>/{run.json,records.jsonl,summary.json,
                              failures.jsonl,command.txt,versions.json,COMPLETE}
  baselines/                    # committed small gate summaries, not raw data
submission-dist/                # generated and ignored
.gitignore                      # ignores raw evidence, local data, temp/package output
docs/fable/fable-native-implementation-progress.md
docs/fable/implementation-steps/s0-foundation.md ... s6-interlock-hardening.md
```

프로덕션 코드는 `tests`, `harness`, `benchmarks`, 절대 경로 또는 로컬 체커 복사본을 import하지 않는다. `solver/checker_adapter.py`만 런타임 `utils` 체커를 import하고, 이를 복제하지 않고 결과를 정규화하는 프로덕션 모듈이다. `solver/validate.py`는 동등성이 증명된 표적 검증을 소유하고 전체 검사에만 어댑터를 호출한다. 테스트는 fixture를 소유한다. 하니스는 서브프로세스 시간 제한, 선택, 결과 스키마, 비교 및 게이트 평가를 소유한다. manifest는 코드 리뷰되는 입력이다. 증거는 추가 전용 생성 출력이며, 간결한 기준선은 의도적으로 커밋할 수 있다. `SolverConfig`는 모든 기능 플래그와 임계값을 소유한다. 명시적으로 감사한 의존성이 필요하지 않은 한 패키징에는 `myalgorithm.py`와 `solver/**.py`만 포함한다.

정식 모듈 이름은 `construct.py` 및 `alns.py`입니다. 이는 보다 구체적인 구현 초안을 선호하는 디자인 부록의 `constructor.py`/`lns.py` 제안을 해결하고 중복된 별칭을 방지합니다.

## 5. 하니스 계약

이후의 모든 명령은 대상 저장소 루트에서 실행되고 다음을 사용합니다.

```bash
PY=/opt/homebrew/Caskroom/miniforge/base/envs/ogc-2026/bin/python
$PY -m baseline.harness.cli <subcommand> ...
```

전역 옵션은 `--evidence-root benchmarks/evidence`(기본값), `--run-id auto`(UTC 타임스탬프 + 8자 nonce), `--seed 20260710`, `--jobs 1`, `--feature NAME=VALUE`(반복 가능) 및 `--resume RUN_ID`입니다. `--resume`는 터미널 레코드 없이 레코드만 실행합니다. 레코드 ID는 커밋, dirty diff 해시, 인스턴스 SHA, 솔버, 시간 제한, 시드 및 정렬된 기능 플래그의 SHA-256입니다. 기본값은 완료된 ID를 중복 제거합니다. `--rerun`는 ​​`supersedes`와 연결된 새로운 시도를 만듭니다.

선택자는 결정론적이다. `example`은 추적되는 예제다. `training`은 `prob_1`…`prob_40`을 요구하며, 각 ID를 순서대로 `data/train/prob_N.json`, `data/train 2/prob_N.json`, `alg_tester/example/train/prob_N.json`, `alg_tester/example/train 2/prob_N.json`에서 해석한다. 한 ID에 서로 다른 해시가 여러 개면 입력 오류다. `smoke-3={prob_21,prob_32,prob_9}`이고, `dev-10={prob_4,prob_8,prob_9,prob_13,prob_20,prob_21,prob_32,prob_36,prob_18,prob_40}`이다. `dense`는 결과를 보기 전에 `area*dwell/(bay-area*horizon)`의 상위 사분위로 선택하며, 동점은 적어도 네 레이어로 해소한다. `high-w23`은 적어도 두 bay에서 `(w2+w3)/max(w1,1)`의 상위 사분위다. 합성 및 스트레스 manifest는 결정론적 fixture를 생성하고 JSON 및 SHA를 증거 디렉터리에 저장한다.

하위 명령:

| 명령 | 필수/선택 입력 | 출력 및 PASS |
|---|---|---|
| `contract` | `--suite semantic|preflight|schema`, `--instances` | 유닛/오라클 행; 예상되는 모든 체커 결정이 일치하고 프리플라이트에서 잘못된 공식 입력이 발견되지 않으면 통과합니다. |
| `parity` | `--kind objective|targeted|geometry`, `--cases`, 선택기 | 내부/체커 쌍으로 결정; 불일치가 0인 경우 통과(목적함수 상대 오차 ≤1e-6). |
| `benchmark` | `--stage`, 선택기, `--timelimits`, `--seeds`, 플래그 | 사례당 하나의 JSONL 레코드입니다. PASS 모든 실행이 종료되면 체커가 가능하며 요청된 구조 카운터가 존재합니다. |
| `stress` | `--stage`, `--instances stress`, 매트릭스 | 장애/미소 예산/자원 사례; 예상된 fallback이 발생하고 모든 유효 사례가 체커 실행 가능일 때 PASS. |
| `ab` | 선택기, 동일한 시드/시간, `--feature`, `--a`, `--b` | 페어링된 레코드와 델타/잡음 대역; PASS 규칙은 단계 게이트에서 제공되며 결과 후에는 절대 추론되지 않습니다. |
| `gate` | `--stage sN --latest-complete --commit HEAD` | 불변의 증거를 재평가하고 `gate.json`를 작성합니다. 모든 필수 기준을 통과한 경우에만 0입니다. |
| `report` | `--stage`, 실행 ID 또는 `--latest-complete` | 실패 및 출처가 포함된 Markdown/JSON 요약 보고는 게이트 상태를 변경하지 않습니다. |
| `submission-rehearsal` | 선택기, 행렬, `--isolated` | 패키지를 빌드, 검사, 추출, 가져오기, 실행, 확인 및 정리합니다. 모든 S6 패키징 규칙과 체커 케이스가 통과되면 통과합니다. |

종료 코드는 고정이다. `0` PASS, `2` 측정 게이트/비회귀 실패, `3` 체커 실행 불가능 또는 현직해 누락, `4` 잘못된 구성/입력/manifest, `5` 시간 초과·크래시·누수·미처리 예외, `6` 의미론/동등성/현직해 안전 위반, `130` 인터럽트다. `run.json`은 실행 전에 `started=true`로 작성한다. `COMPLETE`는 요청된 모든 레코드와 요약이 fsync된 뒤에만 원자적으로 생성된다. 따라서 비어 있거나 부분적인 디렉터리는 명령이 실행되었다는 증거가 될 수 없다.

모든 벤치마크 레코드는 스키마 버전, 실행/레코드 ID, 타임스탬프와 시간대, 브랜치·커밋·dirty 불리언과 diff 해시, 인터프리터 경로, Python/의존성 버전, 정확한 argv와 cwd, 기능 플래그, 시드, 인스턴스 ID/경로/SHA와 선택자, timelimit, wall seconds, 서브프로세스 종료/시그널, 체커 실행 가능/단계/위반/목적함수와 Z1/Z2/Z3, 단계별 시간, backend/name/status/bound/gap/build/solve/first-solution/fallback, 반복/제안/개선 수용/악화 수용/거부 카운트, 캐시 hit/miss/eviction·정확 술어 카운트, timeout/crash/exception 상세, 현직해 검증 횟수, fallback tier/reason을 포함한다. 실패 레코드 예시는 `{"instance_id":"prob_9","status":"checker_failed","checker":{"feasible":false,"stage":5},"failure_stage":"serialize","exit_code":3,"complete":true}`다.

시간 초과 처리에서는 솔버 실행당 새로운 프로세스 그룹인 `SIGTERM`, 2초 유예, `SIGKILL`를 사용합니다. 두 이벤트가 모두 기록됩니다. 정리에서는 아이가 남아 있지 않다고 주장합니다. A/B는 쌍을 이루는 시드에 대해 A, B, B, A를 실행하여 웜 캐시/순서 효과를 노출합니다. `gate`는 현재 단계를 동일한 선택기/시간 제한/시드에서 마지막 완전한 이전 단계 증거와 비교하며, 관련 없는 과거 실행은 절대 사용하지 않습니다.

## 6. 의존성 그래프와 단계 테이블

```text
S0 foundation
  -> S1 constructor
    -> S2 exact retiming
      -> S3 intra-bay LNS
        -> S4 cross-bay assignment refinement
          -> S5 optional process portfolio (may finish disabled)
            -> S6 optional interlock + mandatory hardening/package
```

| 단계 | 상태 | 활성 슬라이스 | 게이트 | 플래그/초기 기본값 | 전제 조건 | 마지막 증거 | 마지막 구현 커밋 | 차단 요인/대체 경로 | 다음 작업 |
|---|---|---|---|---|---|---|---|---|---|
| S0 | `IN_PROGRESS` | — | `NOT_RUN` | `native_solver=false` | 계획 커밋, 데이터 사전 검사 | `benchmarks/evidence/s0/s0-01/20260712T110925Z-s0-01/` | S0-01 원자적 커밋 | 학습 JSON 없음; 초기 단위 슬라이스는 차단하지 않음 | S0-02 실행 |
| S1 | `NOT_STARTED` | — | `NOT_RUN` | `constructor=false` | S0 필수 게이트 | — | — | — | S0을 기다리세요 |
| S2 | `NOT_STARTED` | — | `NOT_RUN` | `exact_retime=false` | S1 필수 게이트 | — | — | — | S1을 기다려 |
| S3 | `NOT_STARTED` | — | `NOT_RUN` | `alns=false`; 수용기 `strict` | S2 필수 게이트 | — | — | — | S2를 기다려 |
| S4 | `NOT_STARTED` | — | `NOT_RUN` | `assignment_refinement=false` | S3 필수 게이트 | — | — | — | S3를 기다려 |
| S5 | `NOT_STARTED` | — | `NOT_RUN` | `parallel_portfolio=false` | S4 필수 게이트 | — | — | 선택적 실패로 인해 S4가 유지됨 | S4를 기다려 |
| S6 | `NOT_STARTED` | — | `NOT_RUN` | `interlock=false` | S5 `COMPLETE` 또는 `GATE_FAILED_DISABLED` | — | — | 인터록 실패로 인해 S5/S4 계층이 강화됨 | S5를 기다려 |

허용되는 구현 상태는 정확히 `NOT_STARTED`, `IN_PROGRESS`, `BLOCKED`, `GATE_FAILED_DISABLED` 및 `COMPLETE`입니다. 전환 규칙은 결정적입니다.

- `NOT_STARTED -> IN_PROGRESS`는 전제 조건이 확인되고 "시작 전" 기록 행이 추가된 후에만 가능합니다.
- 모든 필수 슬라이스가 커밋되고 단계 게이트가 0을 종료한 후에만 `IN_PROGRESS -> COMPLETE`입니다.
- 해결되지 않은 필수 안전성/동등성/타당성 조건에 대해서만 `IN_PROGRESS -> BLOCKED`; 실패한 증거를 정확하게 기록하고 다음 단계를 시작하지 마십시오.
- 안전은 통과했으나 선택적 wall-clock/license 이득 게이트가 실패하면 S5는 `IN_PROGRESS -> GATE_FAILED_DISABLED`가 된다. 그 후 S6를 시작할 수 있다.
- S6는 인터록 기능 상태 `GATE_FAILED_DISABLED`를 독립적으로 기록한다. 인터록을 끈 상태에서 필수 강화/패키지가 통과하면 단계는 `COMPLETE`가 된다.
- `BLOCKED -> IN_PROGRESS`에는 해결된 조건 및 증거를 명명하는 새로운 기록 행이 필요합니다. `COMPLETE`는 다시 열리지 않습니다. 수정은 새 슬라이스를 시작하고 이전 완료가 기록에 유지된 상태로 일시적으로 단계를 `IN_PROGRESS`로 반환합니다.

## 7. 비판적 검토 결정 원장

`ADOPT`는 이월을 의미합니다. `ADOPT_WITH_MODIFICATIONS`는 아래 결의안이 구속력이 있음을 의미합니다. `DEFER_PENDING_MEASUREMENT`는 명명된 이전 게이트가 사용자 입력 없이 선택됨을 의미합니다. `REJECT`는 구현하지 않음을 의미합니다.

| 초안 결정 영역 | 분류 | 구속력 있는 결의 및 상위 권한 |
|---|---|---|
| Checker는 오라클입니다. 현직해 전 전체 확인 | `ADOPT` | 설계 §1/§4.1 및 체커 단계 `917-1421`에 의해 요구됩니다. |
| 증분 Z1/Z3/load 및 O(m) Z2를 가진 배치/상태 | `ADOPT_WITH_MODIFICATIONS` | 내부 값은 S0 목적함수 동등성 전까지 진단용이다. 현직해는 체커 값을 저장한다. |
| 표적 재검증은 생성자/S1에 할당하고 동등성은 S0 게이트로 둠 | `ADOPT_WITH_MODIFICATIONS` | 최소 `validate.py::{validate_unary,validate_pair,validate_changed,validate_insertion}` 및 1,000개 사례 동등성은 S0에 속한다. S1은 후보 생성만 소비/확장한다. 이는 역방향 의존성을 없앤다. |
| 기하학 정확한 답변 캐시 및 AABB 필터 | `ADOPT` | 체커 영역 의미 및 디자인 §1/§4.2와 일치합니다. 사용자 정의 근사 판정은 없습니다. |
| 캐시 캡 2^20 대 디자인 2^21 | `DEFER_PENDING_MEASUREMENT` | S0은 2^18에서 시작하고 벤치마크는 2^16…2^21입니다. 캐시 RSS가 256MiB 이하이고 총 프로세스 피크가 2GiB인 두 가지 중 가장 큰 전력을 선택합니다. 단, 다음으로 작은 용량의 hit률이 99% 이상인 경우는 예외입니다. 레코드 선택. |
| 1e-4로 반올림한 shape key | `REJECT` | 서로 다른 체커 기하가 합쳐질 수 있다. 정확한 입력 정점 튜플과 방향을 정규 키로 사용하며, 동일한 튜플만 답을 공유한다. |
| `slack=D-R-P` | `ADOPT_WITH_MODIFICATIONS` | `P=0`는 체커 재생(`1291-1382`)에서 하루가 필요하므로 `D-R-dwell`를 사용합니다. |
| K=32, 확대 64 대 설계 16→48 | `DEFER_PENDING_MEASUREMENT` | 작동하는 S1 값은 32, 그 다음은 64입니다. {16,32,48,64}를 평가합니다. 타당성 손실이 없고, 철저한 참조 삽입 성공률이 99% 이상이며, 구성 목표가 있는 가장 작은 쌍을 유지합니다. |
| T_CAP=8→16 및 이벤트 경계 시간 | `ADOPT_WITH_MODIFICATIONS` | 작업 값은 유지되지만 한도 설정 후 완전성은 주장되지 않습니다. S1 확대에는 제한이 없는 빈 bay 대체가 포함됩니다. |
| 비용 공식 및 `eta=0.01*max(w1,1)` | `DEFER_PENDING_MEASUREMENT` | S1 A/B는 동점 해소만 보정한다. eta는 동점이 아닌 가중 목적함수 순서를 절대 뒤집어서는 안 된다. |
| T0는 "항상 성공" | `ADOPT_WITH_MODIFICATIONS` | 프리플라이트가 적용된 후에만 참입니다. 피팅 bay가 없는 입력은 이 모델에 대해 명시적으로 만족스럽지 않으며 공식 데이터 계약을 차단합니다. |
| 다중 시작 프로필 및 편향된 무작위화 | `ADOPT_WITH_MODIFICATIONS` | 결정론적 프로필이 먼저입니다. 무작위 프로필은 기록된 RNG 하나를 사용하고 측정된 생성자 예산 내에서만 실행됩니다. |
| Gurobi-첫 번째 정확한 레이어와 CP-SAT fallback | `ADOPT` | 환경/사양은 둘 다 지원합니다. S2는 T0을 확인한 후에만 느리게 프로브합니다. |
| 정확한 백엔드 프로토콜에는 S2 | `ADOPT_WITH_MODIFICATIONS` | S2 계약은 리타임만 소유합니다. S4는 할당 요청으로 이를 확장하여 S2가 S4에 의존하는 것을 방지합니다. |
| Gurobi 표시기 / 동형 CP-SAT 재타이밍 | `ADOPT` | 디자인 §2.4의 직접 비연동 트리코토미 인코딩. |
| 고정된 정확한 시간 상자, pilot 공식, dirty 임계값 | `DEFER_PENDING_MEASUREMENT` | S2는 미리 정의된 매트릭스에서 timebox/pilot을 수정합니다. S3는 dirty 트리거를 교정합니다. 선택될 때까지 초안의 보수적인 값은 증거로 뒷받침되는 기본값이 아니라 플래그입니다. |
| S3의 ALNS D6 크로스 bay 수리 초안 | `REJECT` | S3은 인트라bay입니다. 크로스bay D6, 이동, 교체 및 assignment-v2는 S4에만 속합니다. |
| Undo log 및 current/incumbent 분리 | `ADOPT` | 후보 롤백과 anytime 안전에 필요하다. |
| 초안 의사 코드는 개선을 분류하기 전에 `cur_obj`를 할당합니다 | `ADOPT_WITH_MODIFICATIONS` | `previous_cur_obj`를 저장합니다. `new_obj < previous_cur_obj-EPS`를 분류하고; 분류/승인 후에만 할당합니다. 회귀 테스트를 추가합니다. |
| 엄격한 설계 후 RRT vs 초안 SA 기본값 + RRT | `ADOPT_WITH_MODIFICATIONS` | 먼저 엄격한 기준을 구현한 다음 RRT 및 SA에 플래그를 지정합니다. 사전 등록된 S3 A/B가 하나를 선택할 때까지 기본값은 엄격하게 유지됩니다. 현직해은 여전히 ​​엄격하고/체커가 검증했습니다. |
| Ropke-Pisinger 가중치/숫자 연산자 상수 | `DEFER_PENDING_MEASUREMENT` | 정적 uniform은 안전한 초기 기본값입니다. S3 A/B는 적응을 가능하게 할 수 있습니다. 증거 없이는 문헌 상수가 기본값이 되지 않습니다. |
| `construct.py`/`alns.py` 대 `constructor.py`/`lns.py` | `ADOPT_WITH_MODIFICATIONS` | 정식 이름은 `construct.py` 및 `alns.py`입니다. 별칭이 없습니다. |
| 오래된 `solver-design.md` 권한 참조 | `REJECT` | 모든 새로운 계획은 `solver-design-en.md`를 연결합니다. 비슷한 이름의 한국어 파일은 영어 권한이 아닙니다. |
| 유일한 operations builder인 정식 serializer | `ADOPT` | 체커 재생 `1279-1386`에 필요합니다. S6은 출력 경로가 아닌 순서를 확장합니다. |
| 예산 예비 사다리 | `ADOPT_WITH_MODIFICATIONS` | S0 스트레스는 이를 측정합니다. 예약은 증가할 수 있지만 보장된 T0 경로를 소비하지 않습니다. `time.monotonic()`는 필수입니다. |
| 현직해 후보 개선에만 전수점검 | `ADOPT_WITH_MODIFICATIONS` | 또한 모든 슬라이스는 관찰 가능한 경계에서 전체 검사를 수행합니다. 런타임 검색은 잠재적인 현직해 교체와 단계별로 구성된 주기적인 안전 샘플링만 확인합니다. |
| Assignment-v2 Gurobi 정확한 부동 소수점, CP-SAT 크기 조정 대체 | `ADOPT_WITH_MODIFICATIONS` | S4가 소유하고 있습니다. 반환된 모든 할당은 checker-float Z2로 다시 계산되고 구성/전체 확인 후에만 비교됩니다. |
| S5 세 작업자와 오케스트레이터 | `DEFER_PENDING_MEASUREMENT` | 2개와 3개의 작업자를 평가합니다. 각각의 정확한 백엔드 스레드는 1입니다. 4코어 및 라이선스 제한 내에서만 활성화합니다. |
| 인터록 serializer 위상 순서 | `ADOPT_WITH_MODIFICATIONS` | S6 진리표 테스트가 동작에 앞선다. 순서 실패는 후보를 거부하고 현직해를 유지한다. |
| S6 이후 래스터/컴파일된 형상 | S0-S6용 `REJECT` | 이 로드맵 외부; 무대 게이트에는 필요하지 않습니다. |

체커 의미론에 의해 명시적으로 고정되지 않은 다른 모든 숫자 초안 임계값은 구성 가설이며 사전 등록된 매트릭스에서 가장 먼저 소유하는 측정 게이트에서 선택되어야 합니다. 의미 상수(`dwell=max(P,1)`, 반 개방 부등식, 진입 전 EXIT, 부동 목표)는 조정 가능하지 않고 증거에 의해 고정됩니다.

임계값 출처 레지스트리:

| 임계값 제품군 | 출처 및 결정 포인트 |
|---|---|
| `dwell`, 부등식, 정수 출력, 목적식 | §2에 인용된 체커 라인으로 수정되었습니다. 결코 교정되지 않았습니다. |
| 목적함수 동등성 `1e-6` 상대 | 설계 §6의 진단 허용 오차를 수정했습니다. 기존 결정은 여전히 ​​체커 값을 사용하며 타당성-결정 불일치는 허용되지 않습니다. |
| 합격 비교 `1e-9` 상대 | 채택된 설계 보호 장치; S0 동등성는 체커보다 더 나쁜 교체를 승인하지 않음을 확인합니다. |
| 1,000개의 표적/기하학 및 100개의 목적함수 사례 | 디자인 §6/초안 테스트의 구현 전 적용 범위 수를 수정했습니다. 실패 횟수는 0이어야 합니다. |
| 캐시 한도 및 256MiB 캐시/2GiB 프로세스 측정 예산 | S0 매트릭스로 연기된 한도; 메모리 예산은 사양의 16GiB 서버 한도보다 훨씬 낮은 보수적인 계획 한도입니다. |
| 5초 S0/S1 및 300블록 목표 | 로드맵 게이트로 수정되었습니다. 실패가 차단되면 기준을 다시 보정하지 않습니다. |
| S1 중앙값 10% 및 8/10 이득 | 역사적 대표자 `dev-10`에 대한 "의미 있는"의 정량적 의미로 이전 결과를 수정했습니다. 알고리즘 임계값이 아닙니다. |
| T/K/프로필 예산/타이브레이크 | S1의 사전 등록된 매트릭스 및 인스턴스 측정에 따라 연기됩니다. |
| 백엔드 timebox/pilot | 마감일 안전에 따라 S2 `{1,2,5}`/`{0,2,4}` 매트릭스로 연기됩니다. |
| 크기, dirty 트리거, 수용자, 적응 점수 파괴 | S3 행렬/A-B로 연기됨; static strict/uniform은 안전한 사전 게이트 기본값입니다. |
| 높은 w23/밀도 하위 집합 경계 | 결과 전 인스턴스 통계에서 파생됩니다(§5의 상위 사분위 수식). |
| S3/S4 개선 범위 | 실행 전 게이트 정책을 수정했습니다. 현직해 보존은 체커 목적함수 회귀를 전혀 적용하지 않습니다. |
| S5 240초 매칭 및 S6 25% 밀도 커버리지 | 결과 이전의 선택적 활성화 정책을 수정했습니다. 실패는 그들을 약화시키기보다는 무력화시킵니다. |
| 4코어, 16GiB, 15MB 패키지 | 문제 분석 §5.1/§5.4에 따라 수정되었습니다. |

## 8. 단계별 추적성

| 요구사항 | 계획된 소유자/슬라이스 | 영구 테스트/증거 |
|---|---|---|
| 체커 진리표, P=0, 접촉, 당일 핸드오프/순서 | S0-01 | `test_contract_semantics`; `contract --suite semantic` |
| 정확한 형상/캐시/맞춤 | S0-02 | `test_geometry_kernel`; 조건부 마이크로벤치마크 |
| 목표/상태/표적 동등성 | S0-03 | `test_state_parity`, `test_validate_parity`; 1,000건 |
| serializer/T0/현직해/마감 시한 방어 | S0-04/S0-05 | serializer, trivial 예산 입력 테스트; 5초 학습 실행 |
| 공유 하니스 및 증거 증명 | S0-06 | 스키마/프로세스 테스트 활용 S0 게이트 |
| assignment-v1 + 삽입 + 후보자/확대 | S1-01…04 | `test_construct`; 학습 구성 지표 |
| 다중 시작/결정성/의미 있는 이득 | S1-05 | T0 A/B 쌍; 재생 평등 |
| 정확한 백엔드 격리/동형성/재타이밍 | S2-01…04 | 백엔드/재타이밍 테스트; 합성 최적 평등 |
| 결코 악화되지 않는 대체 조치 | S2-05 | 강제 백엔드 실패; Z1 게이트 |
| bay 내 파괴/수리/수용/anytime | S3-01…05 | `test_alns`; 60/300 A/B 및 카운터 |
| bay 간 이동/스왑 및 assignment-v2 | S4-01…04 | 과제 개선 테스트; 높은 w23 A/B |
| 프로세스 포트폴리오/자원/정리 | S5-01…04 | 포트폴리오/프로세스 테스트; 서버 같은 스트레스 |
| OBS 진리표/연동/순서 | S6-01…04 | 인터록/serializer 테스트; 조밀한 A/B |
| 패키지/스트레스/리허설 | S6-05/S6-06 | 포장 테스트 및 격리 리허설 |

## 9. 증거, 커밋, 정리 및 다시 시작

각 동작 슬라이스는 상태를 `IN_PROGRESS`로 갱신하고 실패 테스트를 추가하며, 문법/설정 실패가 아닌 누락/잘못된 동작을 보이는 정확한 표적 RED 증거를 저장한다. 최소 구현 후 표적 GREEN, 이전 회귀, 실제 또는 합성 공식 체커 사례를 실행하고 구조화된 증거를 생성한다. 자식 프로세스·임시 solver 환경·패키지를 정리하고 이력을 갱신한 다음 단계 계획의 메시지로 원자적 구현 커밋 하나를 만든다. 원시 증거와 로컬 데이터는 패키징하지 않고 자동 stage하지 않는다.

채팅 기록 없이 다시 시작:

1. `git -C /Users/brown/workspace/ogc/fable-native-implementation status --short --branch` 및 브랜치를 확인합니다.
2. 이 문서의 현재 테이블과 마지막 기록 항목을 읽은 다음 활성 단계 문서를 읽습니다.
3. 마지막 구현 커밋이 존재하는지 확인하고 참조 증거 `COMPLETE`, `summary.json` 및 `gate.json`를 검사합니다.
4. `$PY -m baseline.harness.cli report --stage sN --latest-complete`와 활성 슬라이스의 대상 회귀 명령을 실행합니다.
5. 기록된 다음 동작만 재개합니다. 파일이나 채팅만으로 완료를 추론하지 마세요.

## 10. 추가 전용 구현 내역

기존 행/항목을 편집하거나 삭제하지 마십시오. 전환/증거 이벤트당 하나의 분리된 YAML 항목을 추가합니다.

```yaml
- timestamp: 2026-07-12T00:00:00+09:00
  stage: S0
  slice: S0-01
  old_status: NOT_STARTED
  new_status: IN_PROGRESS
  branch: fable-native-implementation
  commit: <HEAD>
  dirty: false
  commands:
    - <exact argv>
  red_evidence: <path or null>
  green_evidence: <path or null>
  checker_result: <feasible/stage/objective or null>
  benchmark_or_stress_evidence: <path or null>
  gate_decision: NOT_RUN|PASS|FAIL|GATE_FAILED_DISABLED
  failure_or_fallback_reason: null
  feature_default_decision: <flag=value and rationale>
  next_action: <single executable action>
```

아직 구현 내역 항목이 없습니다. S0-S6은 `NOT_STARTED`로 유지됩니다. 모든 구현 게이트는 `NOT_RUN`로 유지됩니다.

```yaml
- timestamp: 2026-07-12T20:07:06+09:00
  stage: S0
  slice: S0-01
  old_status: NOT_STARTED
  new_status: IN_PROGRESS
  branch: fable-native-implementation
  commit: e2b0d33f43fe5129459785a52165b8babfe3b95a
  dirty: false
  commands:
    - git status --short --branch
    - git rev-parse HEAD
    - git rev-parse '@{upstream}'
    - /opt/homebrew/Caskroom/miniforge/base/envs/ogc-2026/bin/python --version
    - rg -n '^### S0-01\\b' docs/fable/implementation-steps/s0-foundation.md
  red_evidence: null
  green_evidence: null
  checker_result: null
  benchmark_or_stress_evidence: null
  gate_decision: NOT_RUN
  failure_or_fallback_reason: null
  feature_default_decision: native_solver=false until the full S0 gate passes
  next_action: add the S0-01 semantic contract test and demonstrate the intended adapter-import RED
```

```yaml
- timestamp: 2026-07-12T20:09:25+09:00
  stage: S0
  slice: S0-01
  old_status: IN_PROGRESS
  new_status: IN_PROGRESS
  branch: fable-native-implementation
  commit: e2b0d33f43fe5129459785a52165b8babfe3b95a
  dirty: true
  commands:
    - cd baseline && /opt/homebrew/Caskroom/miniforge/base/envs/ogc-2026/bin/python -m unittest tests.test_contract_semantics -v
  red_evidence: benchmarks/evidence/s0/s0-01/20260712T110925Z-s0-01/red.txt
  green_evidence: null
  checker_result: null
  benchmark_or_stress_evidence: null
  gate_decision: NOT_RUN
  failure_or_fallback_reason: intended RED confirmed with exit 1 solely because solver.checker_adapter is absent
  feature_default_decision: native_solver=false until the full S0 gate passes
  next_action: implement the minimum checker adapter and instance parsing/fit preflight
```

```yaml
- timestamp: 2026-07-12T20:12:03+09:00
  stage: S0
  slice: S0-01
  old_status: IN_PROGRESS
  new_status: IN_PROGRESS
  branch: fable-native-implementation
  commit: e2b0d33f43fe5129459785a52165b8babfe3b95a
  dirty: true
  commands:
    - cd baseline && /opt/homebrew/Caskroom/miniforge/base/envs/ogc-2026/bin/python -m unittest tests.test_contract_semantics -v
    - cd baseline && /opt/homebrew/Caskroom/miniforge/base/envs/ogc-2026/bin/python -m unittest tests.test_contract_semantics -v
    - /opt/homebrew/Caskroom/miniforge/base/envs/ogc-2026/bin/python -m py_compile baseline/solver/__init__.py baseline/solver/checker_adapter.py baseline/solver/instance.py baseline/tests/__init__.py baseline/tests/fixtures.py baseline/tests/test_contract_semantics.py
    - git diff --check
  red_evidence: benchmarks/evidence/s0/s0-01/20260712T110925Z-s0-01/red.txt
  green_evidence: benchmarks/evidence/s0/s0-01/20260712T110925Z-s0-01/green.txt
  checker_result: 16 official checker decisions matched; feasible cases reached Stage 5 and expected rejections occurred at Stages 2, 3, or 5
  benchmark_or_stress_evidence: null
  gate_decision: NOT_RUN
  failure_or_fallback_reason: null
  feature_default_decision: native_solver=false until the full S0 gate passes
  next_action: audit the selected-slice diff and finalize structured checker evidence
```

```yaml
- timestamp: 2026-07-12T20:13:34+09:00
  stage: S0
  slice: S0-01
  old_status: IN_PROGRESS
  new_status: IN_PROGRESS
  branch: fable-native-implementation
  commit: pending atomic commit test(s0): lock checker semantics and instance parsing
  dirty: true
  commands:
    - cd baseline && /opt/homebrew/Caskroom/miniforge/base/envs/ogc-2026/bin/python -m unittest tests.test_contract_semantics -v
    - /opt/homebrew/Caskroom/miniforge/base/envs/ogc-2026/bin/python -m py_compile baseline/solver/__init__.py baseline/solver/checker_adapter.py baseline/solver/instance.py baseline/tests/__init__.py baseline/tests/fixtures.py baseline/tests/test_contract_semantics.py
    - /opt/homebrew/Caskroom/miniforge/base/envs/ogc-2026/bin/python tracked-example fit preflight
    - git diff --check
    - git diff --quiet -- baseline/utils.py baseline/baseline_greedy.py
  red_evidence: benchmarks/evidence/s0/s0-01/20260712T110925Z-s0-01/red.txt
  green_evidence: benchmarks/evidence/s0/s0-01/20260712T110925Z-s0-01/green.txt
  checker_result: PASS; 16 official checker decisions, zero mismatches, feasible cases at Stage 5
  benchmark_or_stress_evidence: not required for S0-01
  gate_decision: NOT_RUN
  failure_or_fallback_reason: null
  feature_default_decision: native_solver=false until the full S0 gate passes
  next_action: commit and push S0-01, then stop; S0-02 is the next eligible slice
```

## 11. 기획 품질 감사

기획심사 완료 2026-07-12 아시아/서울: **PASS**. 이는 구현 게이트가 아닌 문서 품질 결과일 뿐입니다.

- 8개의 기본 문서가 모두 존재합니다. 선언된/실제 슬라이스 수는 S0=6, S1=5, S2=5, S3=5, S4=4, S5=4, S6=6입니다.
- 모든 단계와 슬라이스에는 명시적인 전제 조건/소비된 아티팩트, 출력, 비종속성, 다음 항목 조건, RED/GREEN/체커/증거 작업, 롤백, 정리, 플래그/기본값 및 원자성 커밋 메시지가 있습니다.
- 모든 단계 상태는 `NOT_STARTED`입니다. 모든 게이트는 `NOT_RUN`입니다. 대상 변경 사항에는 솔버, 테스트, 하니스, 벤치마크 또는 패키지 구현이 없습니다.
- S0은 검증 동등성를 소유합니다. S3/S4 소유권은 분리되어 있습니다. S2에는 S4 할당이 필요하지 않습니다. S5/S6 비활성화 기능 결과는 하위 계층을 유지합니다. 이전 게이트에서는 이후 단계 동작을 호출하지 않습니다.
- 하니스 스키마, 선택기, 종료 코드, 실행 증명, 재개/중복 제거, A/B, 이전 단계 비교, 게이트 평가 및 실패 형식이 지정됩니다.
- 모든 로컬 마크다운 링크가 해결됩니다. 신규 기획 텍스트는 모두 한글 부재 확인을 통과하여 영어입니다.
- 두 개의 계획 입력은 소스와 바이트가 동일하게 유지됩니다. 소스 브랜치/HEAD는 `start-point`/`78ef82ece960ac686a6f5c41497a13c2217ffab5`로 유지됩니다. 기존의 추적되지 않은 세 가지 경로는 그대로 유지됩니다.
- 대상 상태에는 이 마스터 문서와 `docs/fable/implementation-steps/`만 포함됩니다. 체커/참조/입력 문서는 수정되지 않습니다.

명시적 프로젝트 인터프리터 아래의 `rg`, `wc`, `cmp`, `git status --short --branch` 및 로컬 링크 확인자를 통해 읽기 전용 검사를 사용하는 감사 명령입니다. 향후 계획을 편집한 후에는 감사를 다시 실행해야 합니다. 실패하면 계획 세션에서 이러한 계획 문서에 대한 편집만 허용됩니다.
