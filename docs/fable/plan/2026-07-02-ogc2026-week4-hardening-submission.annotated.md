# OGC 2026 - 4주차 계획 주석본 (도메인 설명 포함)

> **이 문서에 대하여**
> 이 파일은 `2026-07-02-ogc2026-week4-hardening-submission.md` (이하 "원본")을 **그대로 보존**한 채, 필사하며 읽을 때 참고할 수 있도록 도메인 지식·용어 설명을 끼워 넣은 학습용 사본입니다. 원본은 2~4주차 계획이 서로 의존하는 **계약 문서**라서 절대 수정하지 않았습니다. 설명은 `> 💡 설명:` 형태의 인용 블록으로 표시되며, 원문과 섞이지 않도록 구분해두었습니다. 1주차 주석본(`2026-07-02-ogc2026-week1-core-decoder-hw.annotated.md`)에서 이미 설명한 개념은 짧게만 리마인드하고, 4주차에서 새로 등장하는 개념(설정 통합, 메모리 가드, 환경변수, 결정론적 실행, 하드닝, 제출 패키징 자동화, 서버 시뮬레이션, 튜닝, ablation 등)을 자세히 설명합니다.

---

## 0. 배경: 4주차는 무엇을 하는 주차인가

### 0.1 "하드닝(hardening)"이란 무슨 뜻인가

원본 제목부터 "견고성 하드닝"이라는 표현이 나옵니다.

> 💡 **설명 — 소프트웨어 엔지니어링 용어로서의 "하드닝":** 하드닝은 특정 기법 하나를 가리키는 말이 아니라, **"예외 상황·경계 조건·자원 제약에서도 시스템이 죽지 않고 견디도록 방어 코드를 보강하는 작업 전반"**을 뜻하는 일반 용어입니다(보안 분야에서 "서버 하드닝"이라 하면 불필요한 포트를 닫고 권한을 최소화하는 작업을 가리키는 것과 같은 어법). 1~3주차는 "더 좋은 해를 어떻게 찾을까"(품질)에 집중했다면, 4주차는 **"어떤 입력이 오든, 메모리가 부족하든, 코드 어딘가가 죽든, 시간이 부족하든 절대 예외를 던지지 않고 뭐라도 feasible한 해를 돌려준다"**(생존)에 집중합니다. 대회 규정상 예외/크래시/타임아웃은 전부 -1점이므로(Global Constraints 참고), "최고의 해"보다 "절대 죽지 않는 해"가 이번 주의 최우선순위입니다.

### 0.2 이전 주차 개념 리마인드 (자세한 설명은 1주차 주석본 참고)

아래 개념들은 1주차 주석본에서 이미 자세히 설명했으므로, 여기서는 이번 주 코드를 읽는 데 필요한 만큼만 짧게 되짚습니다.

- **Block/Bay/ENTRY/EXIT/Layer/Orientation/bay_preferences**: 조선소 블록 적치장의 물리적 개념. (1주차 주석본 0.2절)
- **Z1/Z2/Z3 목적함수**: 지연시간(지배적)/작업량 불균형/bay 선호도 손실. (0.3절)
- **Stage 1~5 판정, j≥k 크레인 스윕 규칙**: `utils.check_feasibility`가 최종 심판(oracle)이며, 정적 충돌(`j==k`)과 진입/퇴출 스윕 충돌(`j≥k`)을 구분해서 검사. (0.4절)
- **SCALE(=10000) 정수 스케일링, Clipper2, NFP 오프셋 비트맵(static_bm/entry_bm)**: 좌표를 정수화해 오차 없는 충돌 판정을 하고, "이 오프셋에 두면 충돌하는가"를 미리 비트로 캐싱. (Architecture, Task 2, Task 4)
- **ATC(Apparent Tardiness Cost), BLF(Bottom-Left-Fill), biased randomization**: 우선순위 규칙(SPT+EDD 절충) + 왼쪽아래 우선 배치 + 기하분포 편향 랜덤화. (Task 11, 12)
- **`ogc_core.Core` API**: `place/remove/check_place/free_positions/earliest_feasible/objective/verify_full` 등. (인터페이스 계약)
- **서버 제약**: 4코어/16GB/인터넷 없음, firejail+cpulimit, `utils.py` 수정 금지·최종 oracle, 제출 zip ≤15MB·상대경로만.
- **3단 폴백 셸**: 정교한 방법 → 더 단순한 구성 방법 → `trivial` 최후 보루.

### 0.3 4주차에 새로 등장하는 개념 — 한눈에 미리보기

아래는 짧은 정의만 먼저 두고, 본문(각 Task)에서 실제 코드와 함께 다시 자세히 설명합니다.

- **`SolverConfig` 통합**: 1~3주차에 걸쳐 여기저기 흩어졌던 설정 클래스(`DecoderCfg`, `AlnsConfig`, MH 설정 등)를 하나의 단일 소스(`solver/config.py`)로 합치는 리팩터링. 왜 필요한지는 0.4절 참고.
- **메모리 가드 / RSS / LRU 캐시 축출**: 최대 규모 인스턴스에서 메모리 사용량(RSS)이 서버 한계(16GB)에 근접하지 않도록 감시하고, 필요시 NFP 캐시 크기를 제한.
- **환경변수 레지스트리 (`OGC_*`)**: `OGC_CONFIG`/`OGC_SEED`/`OGC_DET`/`OGC_TRACE_FILE`/`OGC_DEBUG`/`OGC_LONG`/`OGC_FORCE_FALLBACK` — 전부 opt-in이며 제출 환경(서버)에는 설정되지 않아 기본 거동을 바꾸지 않음.
- **결정론적(deterministic) 실행 모드**: 같은 시드로 실행하면 항상 바이트 단위로 동일한 결과가 나오게 만드는 모드. 디버깅·재현성에 필수.
- **페이즈 격리 + 티어 강등 체인**: `shell.solve`를 "각 단계(construct/ALNS/MH)가 죽어도 이전 단계 결과로 강등해서 계속 진행"하는 구조로 재설계.
- **스트레스 인스턴스 생성기**: 실제 대회에서 마주칠 수 있는 "숨은 분포 변화"(더 큰 n, 더 많은 bay, 더 많은 레이어 등)를 시뮬레션하는 합성 인스턴스를 만들어 선제적으로 검증.
- **firejail + cpulimit 서버 시뮬레이션**: 실제 채점 서버의 샌드박스(인터넷 차단, 메모리 제한, CPU 제한)를 로컬에서 재현해 제출 전에 리허설.
- **제출 패키징 자동화**: zip 빌드 + 규정(15MB, 상대경로, 금지 확장자, utils.py 무수정 등) 자동 검증기.
- **파라미터 튜닝 하네스**: 그리드/랜덤 서치로 여러 설정을 실험하고, 통계적으로 유의미하게(2개 이상 시드에서 전승) 개선되는 설정만 채택.
- **Ablation(제거 실험) / 수렴곡선(convergence curve)**: 특정 연산자·단계를 껐을 때 성능이 얼마나 나빠지는지로 그 구성요소의 기여도를 측정하는 기법, 시간에 따른 목적값 개선 추이 기록.

### 0.4 왜 `SolverConfig`로 "통합"이 필요한가

원본을 이해하기 전에 미리 짚어둘 배경입니다. 1주차는 `decoder.DecoderCfg(kappa, bias_p, ...)`를 만들었고, 2주차는 ALNS용 `AlnsConfig(acceptance, rrt_start_pct, ...)`를 추가했고, 3주차는 매트휴리스틱용 설정을 또 추가했습니다. `shell.solve(prob_info, timelimit)`의 시그니처는 제출 경로이므로 절대 바꿀 수 없는데(대회가 `myalgorithm.algorithm(prob_info, timelimit=60)`을 그 시그니처로 직접 호출), 주차가 늘어날수록 "이 설정은 어디서 오는가"가 `alns_cfg=None, mh_cfg=None, report=None` 식으로 키워드 인자가 계속 늘어나며 산발적으로 관리되는 문제가 생깁니다. 4주차는 이걸 **하나의 불변(frozen) 데이터클래스 `SolverConfig`** 로 합쳐서, "튜닝 파라미터가 어디 있는지"를 한 파일에서 관리하고, `OGC_CONFIG`(JSON 파일)/`OGC_SEED`/`OGC_DET` 같은 환경변수로 그 값을 오버라이드할 수 있게 만듭니다. 이 통합 방식은 1주차 계획의 "계약 부록 - 주차 간 조정 확정" 절(원본 205~219행)에서 이미 예고되어 있던 것으로, 이번 주 Task 1이 그 약속을 실제로 구현합니다.

---

## 1. 여기부터 원본 문서 + 인라인 설명

> 아래는 원본 파일의 내용을 그대로 옮기고, 어려운 대목 바로 아래 `> 💡 설명:` 블록을 끼워 넣은 것입니다.

# OGC 2026 — 4주차: 견고성 하드닝 + 스트레스 검증 + 튜닝 + 제출 패키징 구현 계획

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 어떤 입력·예산·환경에서도 예외 없이 시간 내 feasible 해를 반환하는 상태로 솔버를 하드닝하고, firejail 서버 시뮬레이션과 제출 zip 리허설을 통과시키며, 튜닝된 파라미터를 `solver/config.py`에 확정한다.

**Architecture:** L5 견고성 셸을 "최상위 절대 무예외 + 페이즈별 예외 격리 + 티어(incumbent) 강등 체인"으로 재구조화하고, 설정을 `solver/config.py` 단일 소스(+`OGC_*` 환경변수 오버라이드)로 통합해 튜닝·결정성·저하모드를 같은 축으로 제어한다. 숨은 인스턴스 리스크는 합성 스트레스 인스턴스 생성기로 선제 검증하고, firejail+cpulimit 서버 시뮬레이션 → zip 빌드·검증 → 언팩 리허설의 3단 게이트로 12시간 쿨다운 낭비를 차단한다.

> 💡 **설명 — 이 한 문단의 4개 핵심 아이디어:**
> - **"L5 견고성 셸"**: 설계서(`docs/superpowers/specs/2026-07-02-ogc2026-strategy-design.md`)가 정의한 5개 아키텍처 레이어 중 마지막 층. L1=C++ 기하 코어, L2=구성 디코더, L3=ALNS(파괴-복구 반복, 예산의 약 80%), L4=매트휴리스틱 마무리(CP-SAT 재타이밍 등, 예산 후반 약 20%), **L5=견고성 셸**(시간예산 관리, 항상-실행가능 incumbent 유지, 최종 utils 검증, 3단 폴백)입니다. 4주차는 이 L5를 다시 손보는 주차입니다.
> - **"페이즈별 예외 격리 + 티어(incumbent) 강등 체인"**: construct(L2)/ALNS(L3)/MH(L4) 각 단계를 독립된 "페이즈"로 나누고, 어느 한 페이즈가 죽어도(예외 발생) 그 이전 페이즈까지 확보한 결과("티어")로 조용히 물러나 계속 진행하는 구조입니다. 자세한 메커니즘은 Task 2에서 코드와 함께 설명합니다.
> - **"12시간 쿨다운 낭비를 차단"**: 대회 규정상 제출이 "승인"되면 다음 제출까지 12시간을 기다려야 합니다(거절이면 즉시 재제출 가능). 만약 로컬에서 미리 걸러내지 못한 사소한 버그(예: zip 구조 오류, 상대경로 누락) 때문에 제출이 실패하면 12시간을 허비하게 되므로, 제출 전에 "서버와 최대한 비슷한 환경에서 미리 돌려보는" 3단 게이트(서버 시뮬레이션 → zip 검증 → 언팩 후 재실행)를 만들어 이런 낭비를 막습니다.

**Tech Stack:** Python 3.12 (conda env `ogc2026`), pytest, firejail + cpulimit (WSL2 Ubuntu 24.04), zipfile/hashlib/ast (표준 라이브러리만으로 패키징·검증), 기존 `ogc_core`(.so) + `baseline/utils.py`(최종 oracle).

> 💡 **설명:**
> - **firejail**: 리눅스에서 프로세스를 샌드박스(격리된 실행 환경)에 가둬 실행하는 오픈소스 도구. 네트워크 차단, 파일시스템 접근 제한, 메모리 상한 등을 강제할 수 있습니다. 실제 채점 서버가 제출된 코드를 이 방식으로 격리해서 돌리므로, 로컬에서도 같은 도구로 미리 재현해보는 것입니다.
> - **cpulimit**: 특정 프로세스가 쓸 수 있는 CPU 사용률(%)을 제한하는 도구. 서버가 "4코어(400%)"로 CPU를 제한한다고 했으므로, 로컬 개발 머신이 코어가 더 많아도 `cpulimit -l 400`으로 서버와 비슷한 조건을 만듭니다.
> - **zipfile/hashlib/ast**: 전부 Python 표준 라이브러리. `zipfile`은 zip 압축/해제, `hashlib`은 해시(예: SHA-256) 계산(파일이 변조되지 않았는지 확인하는 데 사용), `ast`(Abstract Syntax Tree, 추상 구문 트리)는 Python 코드를 실행하지 않고 코드의 구조만 파싱해서 분석하는 모듈입니다(예: "이 파일에 정말 `algorithm`이라는 함수가 있고, 인자가 정확히 두 개인가?"를 코드를 실행하지 않고도 확인). "표준 라이브러리만으로"라고 강조하는 이유는 서버에 인터넷이 없어 새 패키지를 설치할 수 없기 때문에, 검증 스크립트 자체도 이미 설치된 것만 써야 한다는 원칙 때문입니다.

## Global Constraints (설계서 §1·§5·§8, 문제분석 §5에서 복사)

- 서버: AMD Threadripper PRO 9955WX, Ubuntu 24.04, **4코어(400%)/16GB/인터넷 없음**, firejail+cpulimit, 실행 폴더 상위 접근 금지, 시간제한 수 분~30분(비공개).
- Python 3.12 고정(공식 env). **`baseline/utils.py` 수정 금지** — 최종 검증 oracle이자 제출 동봉본.
- 제출: `submission@optichallenge.com`, 등록 이메일 발신, **zip 1개만 첨부**, zip 루트에 `myalgorithm.py`(하위 폴더 금지), `utils.py` 동봉 가능하나 무수정(서버가 덮어씀), **≤15MB**, `.dll/.vb/.exe` 등 악성 오인 확장자 금지, **상대경로만**.
- 재제출은 직전 제출 "승인" 후 **12시간 쿨다운**(거절 시 즉시 재제출 가능) → 제출 전 로컬 full-simulation 통과가 게이트.
- 평가 상태 6종: Feasible / Infeasible / Time limit exceeded / **Algorithm raised an exception** / **Algorithm process terminated unexpectedly** / Unavailable package — 뒤 5종은 전부 해당 문제 **-1점**. `shell.solve`는 어떤 경우에도 예외를 전파하면 안 된다.
- `myalgorithm.algorithm(prob_info, timelimit=60)` 시그니처 변경 금지.
- 해 형식: x·y 정수, 시간 정수 일, 같은 날 EXIT 전량이 ENTRY보다 먼저. same-day 규약(v0): 같은 날 EXIT들은 block id 오름차순, ENTRY들도 id 오름차순.
- 안전 마진: `Budget(timelimit, safety=0.08)` — timelimit의 92% 시점까지 반환 경로 진입.
- 코어 판정은 utils(shapely, `area>0`)보다 미세하게 보수적. 최종 해는 반환 직전 `utils.check_feasibility` 통과 필수.
- Gurobi 보류(라이선스 미확인) — MIP 계열은 CP-SAT만.

> 💡 **설명 — 이번 주에 처음 명시되는 "평가 상태 6종"이 왜 이 계획 전체의 축인가:** 1주차 문서에서는 "feasible/infeasible"만 이야기했지만, 실제 대회 채점 시스템은 6가지 상태로 결과를 분류합니다. 그중 **Feasible/Infeasible을 뺀 나머지 4가지**(Time limit exceeded, exception, process terminated, unavailable package)는 전부 "우리 코드가 뭔가 잘못되어 죽거나 시간을 넘긴" 상황이고, 대회 규정상 **점수가 -1점**입니다(단순히 0점이 아니라 마이너스). 즉 "차라리 아무것도 안 내는 것"보다 "이상하게 죽는 것"이 더 나쁩니다. 4주차의 존재 이유가 정확히 이 4가지 나쁜 상태를 절대 발생시키지 않는 것이며, 원본 뒷부분(Task 13의 "평가 상태 6종 대응표")에서 각 상태에 대응하는 로컬 테스트를 표로 정리해둔 것을 볼 수 있습니다.
>
> - **"terminated unexpectedly"**: 예외(Python에서 잡을 수 있는 오류)가 아니라, 세그폴트(segmentation fault, C++ 코어의 메모리 오류)나 OOM-killer(메모리 부족으로 운영체제가 프로세스를 강제 종료하는 것) 같은 **프로세스 자체가 통째로 죽는** 상황을 가리킵니다. 그래서 Task 6(메모리 가드)이 이 항목을 직접 겨냥합니다 — 파이썬 `try/except`로는 절대 못 잡는 종류의 실패라서, 애초에 메모리를 16GB 밑으로 억제하는 예방이 유일한 대책입니다.
> - **"unavailable package"**: 제출한 코드가 import하는 어떤 패키지가 서버에 없는 경우입니다. 그래서 Task 12(제출 패키징)의 "언팩 후 깨끗한 프로세스에서 import 검사"가 이걸 겨냥합니다.

## 전제 A — 1주차 고정 계약 (변경 금지, 이 계획 전체가 의존)

```python
# ogc_core.Core / solver.fallback_core (core_iface.make_core가 선택, API 완전 동일)
Core(instance_json: str)
.n_blocks -> int ; .n_bays -> int
.processing(block) -> int ; .release(block) -> int ; .due(block) -> int
.num_orients(block) -> int
.place(block, bay, x, y, orient, entry, exit) -> bool ; .remove(block) ; .clear()
.get_placements() -> list[tuple[block,bay,x,y,orient,entry,exit]]
.load_placements(list[tuple]) -> int
.check_place(...) -> bool ; .free_positions(...) -> list[tuple[x,y]]
.earliest_feasible(block, bay, orient, t_min, t_max) -> tuple[entry,x,y] | None
.objective() -> tuple[Z1, Z2, Z3] ; .verify_full() -> list[str]
.pair_bits_at_current() -> list[tuple]

# solver 계약
core_iface.make_core(prob_info) -> CoreLike        # OGC_FORCE_FALLBACK=1 → 순수 Python 강제
priority.atc_order(prob, t0=0.0, kappa=3.0, rng=None, bias_p=0.25) -> list[int]
decoder.construct(core, prob, rng, cfg: DecoderCfg) -> Placements | None
decoder.DecoderCfg(kappa=3.0, bias_p=0.25, max_delay=None, bay_order="pref")
serialize.to_operations(placements) -> dict         # 제출 형식 {"operations": {...}}
budget.Budget(timelimit, safety=0.08): .remain() / .phase(name, frac) / .expired()
trivial.solve_trivial(core, prob) -> Placements     # 항상 feasible 최후 보루
shell.solve(prob_info, timelimit) -> dict           # myalgorithm 유일 진입점
utils.check_feasibility(prob_info, solution) -> dict
#   키: feasible(bool), stage(int), violations(list), objective/obj1/obj2/obj3(float|None)
```

> 💡 **설명:** 이 블록은 전부 1주차 주석본에서 이미 자세히 설명한 API들입니다(0.5절, Task 6/7/8/9/10/11/12/13 참고). 4주차 코드는 이 계약을 절대 바꾸지 않고 그 위에 하드닝 레이어만 얹습니다. 하나만 새로 눈에 띄는 표기는 `Placements | None`, `int | None` 같은 **PEP 604 유니온 타입 표기**(`X | None`은 "X 아니면 None"이라는 뜻, Python 3.10+ 문법)인데 이미 1주차부터 쓰이던 표기법입니다.

## 전제 B — 2~3주차 산출물 가정 인터페이스 (이 계획이 사용하는 이름·타입)

실제 2~3주차 구현과 이름이 다르면 **이 계획의 코드 쪽을 실제 이름으로 치환**한다(계약 변경 아님). 아래 목록 외의 내부 구조는 가정하지 않는다.

```python
solver/alns/loop.py:
  AlnsCfg(removal_rate=0.30, rrt_start=0.03, seg_len=100, max_iters=None)
  run_alns(core, prob, incumbent: Placements, budget: Budget,
           cfg: AlnsCfg, rng: random.Random) -> Placements
solver/mh/retime.py:  retime(core, prob, incumbent, budget) -> Placements
solver/mh/policy.py:  improve_z2z3(core, prob, incumbent, budget) -> Placements

experiments/run_bench.py CLI (1주차 정의 + 2주차 확장):
  --timelimit N --out PATH --instances prob_1,prob_23 --algo ours|baseline
  --disable TOKEN[,TOKEN]   # TOKEN ∈ {random, worst, shaw, time_slice,
                            #   spatial_column, blocking_set, mh_retime, mh_pool, z2z3}
  결과 JSON: {"meta": {...}, "runs": [{"instance","obj","obj1","obj2","obj3",
                                       "runtime","feasible","seed"}]}
```

`AlnsCfg.max_iters`가 2주차 구현에 없으면 → 계약 변경 요청 #1 (문서 말미).

> 💡 **설명 — "전제(assumption)"라는 이 절의 성격:** 이 계획은 2~3주차가 끝난 뒤에 실행되지만, 4주차 계획 자체는 2~3주차 계획과 별도 문서로 미리(1주차와 같은 날) 작성되었습니다. 그래서 "2~3주차가 실제로 이런 이름의 함수/클래스를 만들어 놓았을 것"이라는 **가정**을 여기 명시해두고, 실제 구현 때 이름이 다르면 (계약을 어긴 게 아니라) 그냥 이 문서의 코드 쪽 이름을 바꿔치기하면 된다고 안전장치를 걸어둔 것입니다. `retime`(재타이밍)은 "위치는 그대로 두고 시간(entry/exit)만 CP-SAT으로 재조정"하는 L4 매트휴리스틱 단계, `improve_z2z3`는 "Z2(불균형)·Z3(선호도손실)를 낮추는 국소 정책 조정" 단계입니다(1주차 0.5절에서 예고된 CP-SAT 매트휴리스틱의 실체).
>
> **`--disable TOKEN`**: 벤치 하네스가 특정 ALNS 연산자(`random, worst, shaw, ...` — "파괴 연산자"의 이름들, 2주차 산출물)나 매트휴리스틱 단계(`mh_retime, mh_pool, z2z3`)를 강제로 꺼서 실행할 수 있게 하는 옵션입니다. 이건 뒤에 나올 **ablation(제거 실험)**을 위한 준비물입니다 — "이 연산자를 껐더니 결과가 얼마나 나빠지는가"로 그 연산자의 기여도를 측정하는 실험 기법(Task 14 참고).

## 파일 구조 (이번 주 생성/수정)

```
solver/
  config.py                    # [신규] SolverConfig 단일 설정 소스 + OGC_* 오버라이드
  shell.py                     # [수정] 무예외 최상위 + 페이즈 격리 + 티어 체인 + trace 훅
experiments/
  gen_stress.py                # [신규] 적대적/엣지 인스턴스 생성기 → data/stress/*.json
  tune.py                      # [신규] 그리드/랜덤 서치 튜닝 하네스
  fallback_quality.py          # [신규] 저하모드 품질 측정 → docs/report-assets
  report_data.py               # [신규] ablation/수렴곡선/인스턴스 통계 집계
scripts/
  server_sim.sh                # [신규] firejail+cpulimit 서버 재현 러너
  sim_runner.py                # [신규] 샌드박스 내부 단일 인스턴스 실행기
  build_submission.py          # [신규] 제출 zip 조립 + 검증기
  rehearsal.sh                 # [신규] 빌드→검증→언팩→server_sim 제출 게이트
docs/
  server-sim.md                # [신규] firejail/cpulimit 설치·사용 (WSL2 Ubuntu 24.04)
  submission-checklist.md      # [신규] 문제분석 §5.1 제출 규정 체크리스트
  report-assets/               # [신규] 보고서 재료 (md/csv)
data/stress/                   # [신규] 생성된 스트레스 인스턴스 (git 포함, 재현 가능)
tests/
  test_config.py test_shell_hardening.py test_determinism.py
  test_budget_stress.py test_degraded_mode.py test_memory_guard.py
  test_gen_stress.py test_stress_solve.py test_server_sim.py
  test_tune.py test_submission.py test_report_data.py
```

> 💡 **설명 — 이 구조를 4주차의 목표에 대응시켜 보면:** `solver/config.py`(설정 통합) + `solver/shell.py` 수정(견고성) 두 파일이 "제출 코드 자체"를 하드닝하는 핵심이고, 나머지는 전부 **그 하드닝이 실제로 효과가 있는지 검증하는 도구**들입니다 — `experiments/gen_stress.py`(가혹한 입력을 인위적으로 만들기), `scripts/server_sim.sh`(실제 서버와 비슷한 환경 재현), `scripts/build_submission.py`(제출물 자체의 규정 준수 자동 검사), `experiments/tune.py`(파라미터 최적화). "코드를 튼튼하게 만드는 것"과 "튼튼한지 확인하는 도구를 만드는 것"이 거의 1:1 비율로 섞여 있는 게 하드닝 주차의 특징입니다.

---

### Task 1: `solver/config.py` — 단일 설정 소스 + `OGC_*` 환경변수 오버라이드

**Files:**
- Create: `solver/config.py`
- Test: `tests/test_config.py`

**Interfaces:**
- Consumes: 없음 (표준 라이브러리만).
- Produces: `SolverConfig` frozen dataclass, `DEFAULT: SolverConfig`, `load_config() -> SolverConfig`. 오버라이드 우선순위: `DEFAULT` < `OGC_CONFIG`(JSON 파일 경로) < `OGC_SEED`/`OGC_DET`. Task 2의 shell, Task 10의 tune.py, Task 3의 결정성 테스트가 이것을 사용한다. 튜닝 확정값(Task 11)은 이 파일의 `DEFAULT` 필드 기본값을 직접 수정해 반영한다.

> 💡 **설명 — `frozen dataclass`란 무엇이고 왜 여기 쓰는가:** Python의 `@dataclass`는 필드 목록만 선언하면 `__init__`, `__eq__` 등을 자동으로 만들어주는 데코레이터입니다. `frozen=True`를 주면 인스턴스 생성 후 필드를 변경할 수 없는 **불변(immutable) 객체**가 됩니다. 설정값을 불변으로 만드는 이유는, 이 `SolverConfig` 객체가 ALNS나 디코더의 여러 함수에 두루 전달되며 읽히는데, 만약 어딘가에서 실수로 `cfg.kappa = 5.0`처럼 값을 바꿔버리면 "다른 곳에서는 여전히 원래 값을 쓴다"는 미묘한 버그가 생길 수 있기 때문입니다. 불변으로 만들면 그런 실수 자체가 원천 차단됩니다(시도하면 예외 발생). 값을 "바꾸고 싶을 때"는 새 객체를 만드는 `dataclasses.replace(cfg, kappa=4.0)` 함수를 씁니다 — 아래 구현에서 실제로 이렇게 씁니다.
>
> **오버라이드 우선순위**란 "여러 군데서 같은 설정을 정할 수 있을 때 누가 이기는가"의 순서입니다. 여기서는 `DEFAULT`(코드에 박힌 기본값)가 가장 낮고, `OGC_CONFIG` 환경변수가 가리키는 JSON 파일이 그걸 덮어쓰고, `OGC_SEED`/`OGC_DET` 환경변수가 최종적으로 한 번 더 덮어씁니다. **제출 환경(서버)에는 이런 `OGC_*` 환경변수가 전혀 설정되어 있지 않으므로, 서버에서는 항상 `DEFAULT` 그대로 실행됩니다** — 이게 "opt-in"(명시적으로 켜야만 동작)이라는 설계 원칙입니다.

- [ ] **Step 1: 실패 테스트 작성** — `tests/test_config.py`

```python
import json
import pytest


def test_default_config_values():
    from solver.config import DEFAULT, SolverConfig, load_config
    assert isinstance(DEFAULT, SolverConfig)
    # 1~3주차에서 쓰던 기본값과 동일해야 함 (튜닝 전 기준점)
    assert DEFAULT.kappa == 3.0 and DEFAULT.bias_p == 0.25
    assert DEFAULT.removal_rate == 0.30 and DEFAULT.rrt_start == 0.03
    assert abs(DEFAULT.phase_construct + DEFAULT.phase_alns + DEFAULT.phase_mh - 1.0) < 1e-9
    assert DEFAULT.seed is None and DEFAULT.deterministic is False
    assert load_config() == DEFAULT          # env 없으면 DEFAULT 그대로


def test_ogc_config_file_override(tmp_path, monkeypatch):
    from solver.config import load_config, DEFAULT
    p = tmp_path / "cfg.json"
    p.write_text(json.dumps({"kappa": 4.0, "removal_rate": 0.2}))
    monkeypatch.setenv("OGC_CONFIG", str(p))
    c = load_config()
    assert c.kappa == 4.0 and c.removal_rate == 0.2
    assert c.bias_p == DEFAULT.bias_p         # 나머지는 기본값 유지


def test_env_seed_and_det_override(tmp_path, monkeypatch):
    from solver.config import load_config
    monkeypatch.setenv("OGC_SEED", "42")
    monkeypatch.setenv("OGC_DET", "1")
    c = load_config()
    assert c.seed == 42 and c.deterministic is True


def test_unknown_key_fails_fast(tmp_path, monkeypatch):
    from solver.config import load_config
    p = tmp_path / "bad.json"
    p.write_text(json.dumps({"no_such_param": 1}))
    monkeypatch.setenv("OGC_CONFIG", str(p))
    with pytest.raises(ValueError, match="no_such_param"):
        load_config()
```

> 💡 **설명 — `monkeypatch.setenv`란:** pytest가 제공하는 표준 픽스처(fixture)로, 테스트 도중에만 환경변수를 임시로 설정했다가 테스트가 끝나면 자동으로 원래 상태로 되돌려주는 도구입니다(수동으로 `os.environ`을 건드리고 나중에 지우는 걸 깜빡하는 실수를 막아줍니다). 여기서는 "OGC_CONFIG가 설정되어 있을 때"를 재현해 `load_config()`가 그 파일을 실제로 읽는지 검증합니다.
>
> **`test_unknown_key_fails_fast`가 왜 중요한가 — "fail fast(빨리 실패하기)" 원칙:** 만약 오타가 있는 설정 파일(`{"kapa": 4.0}`처럼 `kappa`를 잘못 씀)을 조용히 무시하고 기본값을 쓴다면, 튜닝 실험을 몇 시간 돌리고 나서야 "어? 이 설정이 전혀 반영이 안 됐네"를 깨닫는 최악의 상황이 벌어질 수 있습니다. 그래서 `load_config()`는 알 수 없는 키가 하나라도 있으면 그 즉시(fast) `ValueError`를 던져서, 잘못된 설정으로 오랜 시간을 낭비하는 것을 막습니다.

- [ ] **Step 2: 실패 확인**

Run: `conda run -n ogc2026 python -m pytest tests/test_config.py -v`
Expected: 4건 모두 FAIL (`ModuleNotFoundError: No module named 'solver.config'`)

- [ ] **Step 3: 구현** — `solver/config.py`

```python
"""솔버 전역 튜닝 파라미터의 단일 소스.

우선순위: DEFAULT < OGC_CONFIG(JSON 파일) < OGC_SEED / OGC_DET 환경변수.
제출 환경에서는 어떤 OGC_* 환경변수도 없으므로 DEFAULT가 그대로 쓰인다.
튜닝 확정(4주차 Task 11)은 아래 필드 기본값을 직접 수정해 반영한다.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, fields, replace


@dataclass(frozen=True)
class SolverConfig:
    # L2 디코더
    kappa: float = 3.0            # ATC look-ahead
    bias_p: float = 0.25          # biased randomization 기하분포 p
    # L3 ALNS
    removal_rate: float = 0.30    # 파괴 비율
    rrt_start: float = 0.03       # record-to-record 초기 임계(현 최적 대비 비율)
    # L5 예산 배분 (합 = 1.0; Budget safety 0.08은 별도)
    phase_construct: float = 0.15
    phase_alns: float = 0.70
    phase_mh: float = 0.15
    # 재현성
    seed: int | None = None       # None이면 time 기반 엔트로피
    deterministic: bool = False   # True: wall-clock 대신 고정 반복수, MH 스킵
    det_starts: int = 6           # 결정성 모드 multi-start 횟수
    det_alns_iters: int = 3000    # 결정성 모드 ALNS 반복수


DEFAULT = SolverConfig()
_FIELD_NAMES = {f.name for f in fields(SolverConfig)}


def load_config() -> SolverConfig:
    cfg = DEFAULT
    path = os.environ.get("OGC_CONFIG")
    if path:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        unknown = set(data) - _FIELD_NAMES
        if unknown:
            raise ValueError(f"OGC_CONFIG 미지의 키: {sorted(unknown)}")
        cfg = replace(cfg, **data)
    if os.environ.get("OGC_SEED") is not None:
        cfg = replace(cfg, seed=int(os.environ["OGC_SEED"]))
    if os.environ.get("OGC_DET"):
        cfg = replace(cfg, deterministic=True)
    return cfg
```

> 💡 **설명 — 필드 하나하나가 왜 필요한지:**
> - **`kappa`, `bias_p`**: 1주차에서 이미 설명한 ATC 민감도, biased randomization 확률. 이제 `DecoderCfg`가 아니라 `SolverConfig`에서 나와서 decoder로 전달됩니다.
> - **`removal_rate`, `rrt_start`**: 2주차 ALNS의 "파괴 비율"(한 번의 반복에서 기존 해의 몇 %를 허물고 다시 짓는가)과 "record-to-record travel"(RRT, 현재까지 최고 기록 대비 몇 % 이내로 나빠지는 해까지 수용할지 정하는 임계값 — 담금질기법(simulated annealing)과 비슷하되 더 단순한 수용 규칙)의 초기값입니다.
> - **`phase_construct/phase_alns/phase_mh`**: 전체 시간예산을 구성(L2)/ALNS(L3)/매트휴리스틱(L4) 세 단계에 각각 몇 %씩 배분할지(합이 1.0이어야 함). `Budget(timelimit, safety=0.08)`의 `safety`는 이 배분과 별개로 "전체의 8%는 아예 손대지 않고 안전마진으로 남겨둔다"는 뜻입니다.
> - **`seed`**: `None`이면 `time.time_ns()`같은 시간 기반 값으로 매번 다른 난수열을 쓰고(제출 환경의 기본 동작 — 대회는 어차피 1회성 실행이라 재현성보다 다양성이 유리), 정수를 주면 그 값으로 고정해 항상 같은 난수열을 재현합니다.
> - **`deterministic`**: 활성화하면 "시간이 다 될 때까지 반복"하는 대신 "정해진 횟수만큼만 반복"하도록 바꿉니다(`det_starts`, `det_alns_iters`). 왜 필요한지는 Task 3에서 자세히 설명합니다 — 힌트: wall-clock(실제 경과 시간) 기반 반복은 컴퓨터 성능이나 부하에 따라 매번 다른 횟수만큼 돌게 되어 재현이 안 됩니다.

- [ ] **Step 4: 통과 확인**

Run: `conda run -n ogc2026 python -m pytest tests/test_config.py -v`
Expected: 4 passed

- [ ] **Step 5: Commit**

```bash
git add solver/config.py tests/test_config.py
git commit -m "feat(week4): solver 설정 단일 소스 config.py + OGC_* 오버라이드"
```

### Task 2: `shell.solve` 무예외 재구조화 — 페이즈 격리 + 티어 강등 체인

**Files:**
- Modify: `solver/shell.py` (기존 solve 본문을 아래 골격으로 재배치)
- Test: `tests/test_shell_hardening.py`

**Interfaces:**
- Consumes: Task 1 `config.load_config()`; 전제 A의 `make_core/DecoderCfg/construct/solve_trivial/to_operations/Budget/check_feasibility`; 전제 B의 `run_alns/AlnsCfg/retime/improve_z2z3`.
- Produces: `shell.solve(prob_info, timelimit) -> dict` (시그니처 불변, **어떤 예외도 전파 금지**). 모듈 심볼: `_phase_setup(ctx)`, `_phase_construct(ctx)`, `_phase_alns(ctx)`, `_phase_mh(ctx)`, `_finalize(ctx) -> dict`, `_last_resort(prob_info) -> dict`, `_Ctx` (테스트가 monkeypatch로 의존). 티어 순서 `_Ctx.TIER_ORDER = ("mh", "alns", "construct", "trivial")`. 부수 기능: `OGC_TRACE_FILE` 지정 시 incumbent 갱신마다 `"<경과초>,<가중목적값>"` 한 줄 append (Task 14 수렴곡선이 사용), `OGC_DEBUG=1`이면 페이즈 오류 traceback을 stdout에 출력.

> 💡 **설명 — 이 Task가 4주차의 심장입니다. "왜 이렇게까지 복잡하게 만드는가"부터 짚고 갑니다:** 1~3주차의 `shell.solve`는 대략 "구성 → ALNS → 매트휴리스틱을 순서대로 실행하고, 전체를 하나의 `try/except`로 감싸서 실패하면 trivial을 반환"하는 정도였을 것입니다. 문제는 이 방식은 **"어디서 실패했는지"에 상관없이 최종 결과가 항상 trivial(최악의 품질)로 떨어진다**는 점입니다. 예를 들어 ALNS 단계에서 버그로 예외가 나면, 이미 구성(construct) 단계에서 만들어둔 꽤 괜찮은 해가 있는데도 그걸 버리고 trivial로 강등해버리는 건 낭비입니다. 이번 주의 재구조화는 **"각 단계를 독립적으로 격리하고, 실패한 단계 이전까지 확보한 가장 좋은 결과를 쓴다"**는 개선입니다. 이게 "페이즈 격리 + 티어 강등 체인"의 정확한 의미입니다:
> - **페이즈(phase)**: `_phase_setup`(코어 초기화 + trivial 확보) → `_phase_construct`(멀티스타트 구성) → `_phase_alns`(ALNS 개선) → `_phase_mh`(매트휴리스틱 마무리) 순서로 실행되는 각 단계. 하나씩 독립된 함수라서, 하나가 예외를 던져도 `try/except`로 그 함수 호출만 감싸서 잡고 다음 단계로 넘어갈 수 있습니다(아래 `_solve_guarded`의 for 루프 참고).
> - **티어(tier)**: 각 페이즈가 만들어낸 "결과물의 품질 등급"입니다. `mh > alns > construct > trivial` 순으로 좋다고 가정합니다(매트휴리스틱까지 거친 해가 가장 좋고, trivial이 가장 나쁨). `ctx.best`라는 딕셔너리에 티어별로 "지금까지 그 티어에서 찾은 가장 좋은 해"를 저장해둡니다.
> - **강등(degrade) 체인**: 최종적으로 `_finalize`가 "가장 좋은 티어부터 순서대로" utils 검증을 시도하고, 그 티어가 (오염되었거나 다른 이유로) feasible이 아니면 그다음으로 좋은 티어로 "강등"해서 다시 시도합니다. 모든 티어가 실패하면 빈 결과를 반환합니다(더 아래 `_last_resort`가 또 한 번의 안전망).
>
> 요컨대 "최고 품질부터 순서대로 utils 검증을 시도하다가, 안 되면 한 단계씩 눈을 낮춰서 결국 뭐라도 feasible한 걸 낸다"는 다단계 안전장치입니다. 1주차의 "3단 폴백"(정교한 방법→단순한 방법→trivial)이라는 아이디어를, 이제 **"단계별로 독립 격리되고, 몇 단계든(mh/alns/construct/trivial 4단계로 확장) 유연하게 강등 가능한" 좀 더 정교한 형태**로 발전시킨 것이라고 보면 됩니다.

- [ ] **Step 1: 실패 테스트 작성** — `tests/test_shell_hardening.py`

```python
import json
import pytest
from utils import check_feasibility


def _boom(*a, **k):
    raise RuntimeError("의도된 페이즈 폭발")


@pytest.mark.parametrize("phase", ["_phase_construct", "_phase_alns", "_phase_mh"])
def test_single_phase_exception_still_feasible(prob1, monkeypatch, phase):
    import solver.shell as shell
    monkeypatch.setattr(shell, phase, _boom)
    sol = shell.solve(prob1, 30)
    res = check_feasibility(prob1, sol)
    assert res["feasible"] is True            # 하위 티어로 강등해 feasible 유지


def test_all_phases_exception_returns_trivial(prob1, monkeypatch):
    import solver.shell as shell
    for ph in ("_phase_construct", "_phase_alns", "_phase_mh"):
        monkeypatch.setattr(shell, ph, _boom)
    sol = shell.solve(prob1, 30)
    assert check_feasibility(prob1, sol)["feasible"] is True   # trivial 티어


def test_even_setup_exception_never_raises(prob1, monkeypatch):
    import solver.shell as shell
    monkeypatch.setattr(shell, "_phase_setup", _boom)
    sol = shell.solve(prob1, 30)              # _last_resort 경로
    assert isinstance(sol, dict) and "operations" in sol
    assert check_feasibility(prob1, sol)["feasible"] is True


def test_garbage_input_never_raises():
    import solver.shell as shell
    sol = shell.solve({"bays": [], "blocks": [], "weights": {}}, 5)
    assert isinstance(sol, dict) and "operations" in sol       # 절대 무예외


def test_finalize_rejects_infeasible_tier(prob1, monkeypatch):
    """상위 티어가 오염된 placements를 내놓아도 utils 검증에서 걸러 하위 티어 반환."""
    import solver.shell as shell
    orig = shell._phase_construct

    def poison(ctx):
        orig(ctx)
        if "construct" in ctx.best:
            obj, pl = ctx.best["construct"]
            bad = [(b, bay, x + 10**6, y, o, e, xt) for (b, bay, x, y, o, e, xt) in pl]
            ctx.best["construct"] = (obj - 1.0, bad)   # bay 밖 좌표 = infeasible
    monkeypatch.setattr(shell, "_phase_construct", poison)
    monkeypatch.setattr(shell, "_phase_alns", _boom)
    monkeypatch.setattr(shell, "_phase_mh", _boom)
    sol = shell.solve(prob1, 30)
    assert check_feasibility(prob1, sol)["feasible"] is True   # trivial로 강등됨


def test_trace_file_records_incumbents(prob1, tmp_path, monkeypatch):
    import solver.shell as shell
    tf = tmp_path / "trace.csv"
    monkeypatch.setenv("OGC_TRACE_FILE", str(tf))
    shell.solve(prob1, 20)
    lines = tf.read_text().strip().splitlines()
    assert len(lines) >= 1
    t, obj = lines[0].split(",")
    assert float(t) >= 0.0 and float(obj) > 0.0
```

> 💡 **설명 — `monkeypatch.setattr(shell, phase, _boom)`이 뭘 하는가:** 이 프로젝트에서 처음 등장하는 중요한 테스트 기법입니다. `monkeypatch.setattr(모듈, "함수이름", 대체함수)`는 "테스트가 실행되는 동안만, 그 모듈의 그 함수를 다른 함수(`_boom`, 호출하면 무조건 예외를 던지는 가짜 함수)로 바꿔치기한다"는 뜻입니다(끝나면 자동 원복). 실제로 ALNS나 매트휴리스틱 코드에 진짜 버그를 심어서 테스트할 필요 없이, "이 단계가 실패했다고 치면 어떻게 되는가"를 인위적으로 시뮬레이션하는 것입니다. 이게 가능하려면 `shell.py`가 `_phase_construct` 같은 이름을 **모듈 레벨의 독립된 함수**로 노출해야 하므로(클래스 메서드나 지역 함수로 숨기면 monkeypatch로 갈아끼울 수 없음), Interfaces 절에서 "테스트가 monkeypatch로 의존"한다고 명시한 것입니다.
>
> `test_finalize_rejects_infeasible_tier`는 좀 더 교묘한 시나리오입니다 — 페이즈 자체는 "성공"했다고 보고했지만, 그 결과물이 실제로는 좌표가 bay 밖으로 나가는 등 **잘못된(오염된) 해**인 경우입니다(코드에 버그가 있어서 이런 일이 생길 수 있음을 가정). 이 경우 "실패를 잡는 `try/except`"만으로는 못 걸러내고, **최종적으로 utils.check_feasibility를 통과하는지 실제로 확인**해야만 걸러낼 수 있습니다 — 그래서 `_finalize`가 각 티어를 "정말 utils가 인정하는지" 하나씩 검증하며 내려가는 구조가 필요한 것입니다.
>
> `test_trace_file_records_incumbents`는 `OGC_TRACE_FILE` 환경변수(0.3절에서 예고)의 기능을 검증합니다 — "incumbent"(현재까지의 최선해)가 갱신될 때마다 "몇 초 시점에 목적값이 얼마였는지"를 CSV 한 줄로 파일에 追記(append)합니다. 이 파일은 나중에 Task 14에서 "시간이 지남에 따라 해가 얼마나 개선되는가"(수렴곡선, convergence curve)를 그리는 원재료로 쓰입니다.

- [ ] **Step 2: 실패 확인**

Run: `conda run -n ogc2026 python -m pytest tests/test_shell_hardening.py -v`
Expected: FAIL (AttributeError: `_phase_construct` 등 모듈 심볼 부재 — 3주차 shell은 이 구조가 아님)

- [ ] **Step 3: shell.py 재구조화 구현**

아래 골격으로 재배치한다. **기존 1~3주차 페이즈 로직(멀티스타트 construct 루프, ALNS 호출, CP-SAT 재타이밍·Z3/Z2 폴리시 호출)은 삭제하지 말고 해당 `_phase_*` 함수 본문으로 이동**한다. 이동 시 결과 보고는 전부 `ctx.offer(tier, obj, placements)`로 일원화한다.

```python
"""L5 견고성 셸 — 절대 무예외, 페이즈 격리, 티어 강등 체인."""
from __future__ import annotations

import os
import random
import time
import traceback

from solver import serialize, trivial
from solver import config as cfgmod
from solver.budget import Budget
from solver.core_iface import make_core
from solver.decoder import DecoderCfg, construct

_T0 = 0.0  # solve 진입 시각 (trace용)


def solve(prob_info, timelimit=60):
    """myalgorithm 유일 진입점. 어떤 입력·내부 오류에도 예외를 전파하지 않는다."""
    global _T0
    _T0 = time.monotonic()
    try:
        return _solve_guarded(prob_info, timelimit)
    except BaseException:
        try:
            return _last_resort(prob_info)
        except BaseException:
            return {"operations": {}}          # 최악: infeasible이어도 예외보단 낫다


def _solve_guarded(prob_info, timelimit):
    cfg = cfgmod.load_config()
    budget = Budget(timelimit, safety=0.08)
    ctx = _Ctx(prob_info, budget, cfg)
    for phase in (_phase_setup, _phase_construct, _phase_alns, _phase_mh):
        if budget.expired():
            break
        try:
            phase(ctx)
        except BaseException:
            ctx.log_error(phase.__name__, traceback.format_exc())
            continue                            # 다음 페이즈로 — 확보된 티어는 유지
    return _finalize(ctx)


class _Ctx:
    TIER_ORDER = ("mh", "alns", "construct", "trivial")   # 좋은 순

    def __init__(self, prob, budget, cfg):
        self.prob, self.budget, self.cfg = prob, budget, cfg
        self.core = None
        self.best = {}                          # tier -> (weighted_obj, placements)
        self.errors = []
        self.base_seed = cfg.seed if cfg.seed is not None else time.time_ns() % (2**31)

    def offer(self, tier, obj, placements):
        cur = self.best.get(tier)
        if cur is None or obj < cur[0]:
            self.best[tier] = (obj, list(placements))
            _trace(obj)

    def tiers(self):
        for t in self.TIER_ORDER:
            if t in self.best:
                yield t, self.best[t][1]

    def log_error(self, where, tb):
        self.errors.append((where, tb))
        if os.environ.get("OGC_DEBUG"):
            print(f"[shell] {where} 실패:\n{tb}", flush=True)


def _trace(obj):
    path = os.environ.get("OGC_TRACE_FILE")
    if path:
        with open(path, "a", encoding="utf-8") as f:
            f.write(f"{time.monotonic() - _T0:.2f},{obj:.1f}\n")


def _weighted_obj(ctx, placements):
    ctx.core.clear()
    n = ctx.core.load_placements(placements)
    if n != len(placements):
        raise ValueError("placements 재적용 실패 — 오염된 해")
    z1, z2, z3 = ctx.core.objective()
    w = ctx.prob["weights"]
    return w["w1"] * z1 + w["w2"] * z2 + w["w3"] * z3


def _phase_setup(ctx):
    ctx.core = make_core(ctx.prob)              # .so 실패 시 자체적으로 fallback
    pl = trivial.solve_trivial(ctx.core, ctx.prob)
    ctx.offer("trivial", _weighted_obj(ctx, pl), pl)


def _phase_construct(ctx):
    ctx.budget.phase("construct", ctx.cfg.phase_construct)
    dcfg = DecoderCfg(kappa=ctx.cfg.kappa, bias_p=ctx.cfg.bias_p)
    limit = ctx.cfg.det_starts if ctx.cfg.deterministic else 10**9
    i = 0
    while i < limit and not ctx.budget.expired():
        pl = construct(ctx.core, ctx.prob, random.Random(ctx.base_seed + i), dcfg)
        if pl is not None:
            ctx.offer("construct", _weighted_obj(ctx, pl), pl)
        i += 1
    # (3주차까지의 멀티스타트 고도화 로직이 있으면 이 루프 자리로 이동)


def _phase_alns(ctx):
    from solver.alns.loop import run_alns, AlnsCfg       # 2주차 산출물
    if "construct" not in ctx.best and "trivial" not in ctx.best:
        return
    ctx.budget.phase("alns", ctx.cfg.phase_alns)
    start = ctx.best.get("construct", ctx.best["trivial"])[1]
    acfg = AlnsCfg(removal_rate=ctx.cfg.removal_rate, rrt_start=ctx.cfg.rrt_start,
                   max_iters=ctx.cfg.det_alns_iters if ctx.cfg.deterministic else None)
    pl = run_alns(ctx.core, ctx.prob, start, ctx.budget,
                  acfg, random.Random(ctx.base_seed ^ 0xA15A))
    if pl:
        ctx.offer("alns", _weighted_obj(ctx, pl), pl)


def _phase_mh(ctx):
    if ctx.cfg.deterministic:
        return                                   # CP-SAT wall-time 비결정성 회피
    from solver.mh.retime import retime          # 3주차 산출물
    from solver.mh.policy import improve_z2z3
    best_tier = next(ctx.tiers(), None)
    if best_tier is None:
        return
    ctx.budget.phase("mh", ctx.cfg.phase_mh)
    pl = retime(ctx.core, ctx.prob, best_tier[1], ctx.budget)
    if pl:
        ctx.offer("mh", _weighted_obj(ctx, pl), pl)
        pl2 = improve_z2z3(ctx.core, ctx.prob, pl, ctx.budget)
        if pl2:
            ctx.offer("mh", _weighted_obj(ctx, pl2), pl2)


def _finalize(ctx):
    from utils import check_feasibility          # 제출 zip 루트/테스트 경로의 utils
    for tier, placements in ctx.tiers():
        try:
            sol = serialize.to_operations(placements)
            if check_feasibility(ctx.prob, sol)["feasible"]:
                return sol
        except BaseException:
            continue                             # 다음(하위) 티어로 강등
    return {"operations": {}}


def _last_resort(prob_info):
    """_solve_guarded 자체가 죽었을 때: 순수 Python 경로로 trivial 해."""
    os.environ["OGC_FORCE_FALLBACK"] = "1"
    core = make_core(prob_info)
    return serialize.to_operations(trivial.solve_trivial(core, prob_info))
```

주의: `test_garbage_input_never_raises`는 blocks가 빈 입력 — `solve_trivial`/`serialize`가 빈 리스트에서 예외 없이 `{"operations": {}}`를 내도록 (필요 시 `trivial.solve_trivial`에 `if not prob["blocks"]: return []` 한 줄 추가).

> 💡 **설명 — 구현을 세 겹의 안전망으로 다시 읽어보면:**
> 1. **가장 바깥(`solve` 함수)**: `_solve_guarded` 전체를 감싸는 `try/except BaseException`. `BaseException`은 Python에서 잡을 수 있는 거의 모든 것(일반 `Exception`뿐 아니라 `KeyboardInterrupt`, `SystemExit`까지)을 포괄하는 최상위 예외 클래스입니다 — "정말 무엇이 터지든 잡는다"는 의지의 표현입니다. 여기서 잡히면 `_last_resort`로 넘어갑니다.
> 2. **중간(페이즈 루프)**: `_solve_guarded` 안의 `for phase in (...)` 루프가 각 페이즈를 개별 `try/except`로 감싸서, 한 페이즈가 죽어도 `continue`로 다음 페이즈를 계속 시도합니다. 이게 "페이즈 격리"의 실체입니다.
> 3. **`_last_resort` 자체도 실패할 경우**: 맨 바깥의 `except BaseException: return {"operations": {}}`가 최후의 최후 방어선입니다. 빈 `operations` 딕셔너리는 물론 utils 기준 infeasible이겠지만, **최소한 예외를 던지지 않고 dict를 반환**하므로 "Algorithm raised an exception"(−1점) 대신 "Infeasible"(역시 안 좋지만 규정상 더 나은 신호)로 처리될 여지를 남깁니다.
>
> **`ctx.offer(tier, obj, placements)`**: "이 티어에 대해 이 정도 품질의 해를 제안한다"는 뜻의 메서드로, 기존에 그 티어에 저장된 것보다 목적값이 낮으면(더 좋으면) 교체합니다. 이렇게 하면 `_phase_construct`가 멀티스타트로 여러 번 호출되어도 "그 티어 안에서 지금까지 나온 것 중 최선"만 자동으로 유지됩니다.
>
> **`_weighted_obj`**: Z1·Z2·Z3 세 목적을 각각의 가중치(`w1, w2, w3` — 1주차 0.3절에서 `w1`이 압도적으로 크다고 설명)로 합쳐 **단일 스칼라 값**으로 만드는 함수입니다. 서로 다른 페이즈의 결과를 "숫자 하나"로 비교해야 `ctx.offer`에서 "더 좋다"는 판단(`obj < cur[0]`)을 내릴 수 있기 때문입니다. `core.load_placements`로 그 placements를 코어에 다시 적용해 재검증하는 것도 중요한 방어 코드입니다 — 만약 `placements` 리스트 자체가 어딘가에서 오염되어 있었다면 `n != len(placements)`(전부 적용 성공하지 못함)로 걸러져 예외를 던지고, 그 페이즈는 실패로 처리됩니다.
>
> **`_phase_mh`의 `if ctx.cfg.deterministic: return`**: 결정성 모드에서는 매트휴리스틱(CP-SAT) 단계를 아예 건너뜁니다. 이유는 Task 3에서 다시 설명하지만 미리 짚으면, CP-SAT 같은 외부 솔버는 내부적으로 "시간을 얼마나 썼는가"에 따라 탐색 경로가 달라질 수 있어(wall-time 기반 타임아웃, 멀티스레드 타이밍 등) 완벽한 재현이 어렵기 때문에, "결정론적 실행"을 보장해야 하는 모드에서는 아예 그 변동 요인을 제거하는 것입니다.

- [ ] **Step 4: 전체 테스트 통과 확인 (회귀 포함)**

Run: `conda run -n ogc2026 python -m pytest tests/test_shell_hardening.py tests/test_shell.py -v`
Expected: 신규 7건 + 1주차 test_shell.py 전부 PASS (기존 폴백 동작 회귀 없음)

- [ ] **Step 5: Commit**

```bash
git add solver/shell.py solver/trivial.py tests/test_shell_hardening.py
git commit -m "feat(week4): shell 무예외 재구조화 - 페이즈 격리 + 티어 강등 + trace 훅"
```

### Task 3: 결정성 옵션 — 고정 시드 재현성 테스트

**Files:**
- Modify: `solver/shell.py` (Task 2 골격에 이미 det 분기 포함 — 본 Task는 검증·누수 수정), `solver/alns/loop.py` (rng 누수 발견 시)
- Test: `tests/test_determinism.py`

**Interfaces:**
- Consumes: Task 1 `SolverConfig.seed/deterministic/det_starts/det_alns_iters`, Task 2 `_Ctx.base_seed` 시드 배선.
- Produces: `OGC_SEED=<n> OGC_DET=1` 환경에서 `shell.solve`가 완전 재현(직렬화 결과 바이트 동일). 제출 기본 경로(env 없음)는 영향 없음.

> 💡 **설명 — "결정론적(deterministic) 실행"이 왜 이 대회에서 중요한가:** 결정론적이라는 건 "같은 입력을 주면 항상 정확히 같은 출력이 나온다"는 성질입니다. 메타휴리스틱은 태생적으로 난수를 많이 쓰고(우선순위 편향 랜덤화, ALNS의 파괴 연산자 선택 등), 시간예산 기반으로 "시간이 될 때까지 반복"하는 구조라서, 기본적으로는 실행할 때마다 조금씩 다른 결과가 나오는 게 정상입니다. 그런데 이게 **개발·디버깅 단계에서는 큰 골칫거리**가 됩니다 — "방금 고친 코드가 실제로 나아졌는지"를 확인하려 해도, 원래도 매번 결과가 달랐다면 "우연히 좋아진 건지 진짜 개선인지" 구분할 수 없기 때문입니다. 그래서 `OGC_SEED`+`OGC_DET` 조합으로 **"난수를 고정된 시드로만 쓰고, 반복 횟수도 시간이 아니라 고정된 숫자로 캡"**하면, 완전히 같은 실행을 몇 번이고 재현할 수 있습니다. 이건 대회 채점 자체보다는(서버는 시드를 안 주니 결정론 모드가 아님), **우리 쪽 개발·회귀 테스트·튜닝 실험의 신뢰성**을 위한 장치입니다 — 예를 들어 Task 10의 튜닝 하네스가 "설정 A가 설정 B보다 실제로 낫다"고 결론 내리려면, 그 비교가 우연한 난수빨이 아니라 진짜 설정 차이 때문이어야 하므로 결정성이 뒷받침되어야 신뢰할 수 있습니다.
>
> **"완전 재현(직렬화 결과 바이트 동일)"**이라는 강한 기준: 단순히 "목적값이 비슷하다"가 아니라, `json.dumps(sol, sort_keys=True)`로 직렬화한 **문자열이 완전히 똑같아야** 통과입니다. 이렇게 엄격한 기준을 두는 이유는, 아래 테스트에서 보듯 "겉보기엔 시드를 잘 물려준 것 같은데 사실 코드 어딘가 구석에 시드 없는 전역 난수(`random.random()` 같은 것)가 숨어 있는" 미묘한 버그를 잡아내기 위해서입니다 — 결과가 "거의 같다"면 그 미묘한 누수를 놓치기 쉽지만, "완전히 같아야 한다"는 기준은 아주 작은 차이도 즉시 실패로 드러냅니다.

- [ ] **Step 1: 실패 테스트 작성** — `tests/test_determinism.py`

```python
import json


def test_fixed_seed_reproducible_in_process(prob1, monkeypatch):
    from solver.shell import solve
    monkeypatch.setenv("OGC_SEED", "42")
    monkeypatch.setenv("OGC_DET", "1")
    s1 = solve(prob1, 60)
    s2 = solve(prob1, 60)
    assert json.dumps(s1, sort_keys=True) == json.dumps(s2, sort_keys=True)


def test_different_seed_differs(prob1, monkeypatch):
    from solver.shell import solve
    monkeypatch.setenv("OGC_DET", "1")
    monkeypatch.setenv("OGC_SEED", "1")
    a = solve(prob1, 60)
    monkeypatch.setenv("OGC_SEED", "2")
    b = solve(prob1, 60)
    # 다른 시드는 (거의 확실히) 다른 해 — 같으면 시드가 배선되지 않은 것
    assert json.dumps(a, sort_keys=True) != json.dumps(b, sort_keys=True)


def test_det_mode_subprocess_reproducible(prob1, tmp_path):
    """프로세스 경계를 넘어도 재현 — 캐시/해시 순서 비결정성까지 검출."""
    import subprocess, sys, pathlib
    root = pathlib.Path(__file__).resolve().parents[1]
    code = (
        "import json,sys,os;"
        "sys.path[:0]=[str(r) for r in [%r,%r]];"
        "from solver.shell import solve;"
        "p=json.load(open(%r));"
        "print(json.dumps(solve(p,45),sort_keys=True))"
    ) % (str(root / "solver"), str(root / "baseline"),
         str(root / "data" / "training_instances" / "train" / "prob_1.json"))
    env = {"OGC_SEED": "7", "OGC_DET": "1", "PATH": "/usr/bin:/bin"}
    outs = [subprocess.run([sys.executable, "-c", code], cwd=root, env=env,
                           capture_output=True, text=True, check=True).stdout
            for _ in range(2)]
    assert outs[0] == outs[1]
```

> 💡 **설명 — 세 테스트가 각각 다른 종류의 재현성을 검사합니다:**
> - `test_fixed_seed_reproducible_in_process`: 같은 파이썬 프로세스 안에서 두 번 `solve`를 호출해도 같은 결과가 나오는지. 가장 기본적인 검사입니다.
> - `test_different_seed_differs`: 반대 방향 검사 — 시드를 바꿨는데도 결과가 똑같다면, 그건 재현성이 "너무 잘 되는" 게 아니라 **`base_seed`가 애초에 난수 생성기에 제대로 전달되지 않고 있다는 버그 신호**입니다(즉 rng를 실제로는 안 쓰고 있거나, 하드코딩된 값을 쓰고 있는 것). 재현성 테스트는 "같아야 할 때 같은지"뿐 아니라 "달라야 할 때 정말 다른지"도 함께 확인해야 의미가 있습니다.
> - `test_det_mode_subprocess_reproducible`: **완전히 새로운 별도의 파이썬 프로세스(subprocess)** 두 개를 띄워서 비교합니다. 왜 같은 프로세스 안에서의 재현성만으로는 부족한가? 파이썬의 `dict`나 `set`은 특정 상황에서 프로세스마다 다른 내부 해시 순서를 가질 수 있고(예: `PYTHONHASHSEED`가 랜덤이면 문자열 해시가 매 프로세스 실행마다 달라짐), C++ 코어의 캐시(예: NFP 비트맵 해시맵)가 삽입 순서에 따라 내부적으로 다르게 구성될 수도 있습니다. 이런 "프로세스 경계를 넘어야만 드러나는 비결정성"까지 잡아내려면 실제로 별도 프로세스를 두 번 실행해서 비교해야 합니다. `subprocess.run([sys.executable, "-c", code], ...)`는 "지금 쓰고 있는 것과 같은 파이썬 인터프리터로, 주어진 코드 문자열을 새 프로세스에서 실행하라"는 뜻입니다.

- [ ] **Step 2: 실패 확인**

Run: `conda run -n ogc2026 python -m pytest tests/test_determinism.py -v`
Expected: 최소 `test_fixed_seed_reproducible_in_process` FAIL (2~3주차 코드가 전역 `random`/`time` 기반 난수를 쓰거나 wall-clock 반복이면 결과 상이)

- [ ] **Step 3: 비결정성 누수 제거**

체크리스트 (실패 원인을 하나씩 소거):
1. `solver/` 전체에서 시드 없는 난수 사용 검색: `grep -rn "random\.\(random\|randint\|choice\|shuffle\|sample\)" solver/ | grep -v "Random("` → 발견 시 해당 함수에 `rng` 파라미터를 물려받도록 수정 (shell이 `random.Random(ctx.base_seed …)`를 이미 주입).
2. `numpy.random` 전역 사용 검색: `grep -rn "np\.random\.\|numpy\.random\." solver/` → `numpy.random.Generator(numpy.random.PCG64(seed))` 주입으로 교체.
3. det 모드에서 wall-clock 조건 잔존 검색: `_phase_construct`는 `det_starts`로, ALNS는 `max_iters`로 반복이 캡되는지 확인 (`AlnsCfg.max_iters` 미구현이면 `run_alns` 루프 조건에 `it < (cfg.max_iters or 10**18)` 추가 — 계약 변경 요청 #1).
4. `dict`/`set` 순회 순서 의존 검색: 연산자 선택·후보 정렬에 `set` 순회가 있으면 `sorted()`로 고정.

> 💡 **설명 — 각 항목이 잡아내는 비결정성의 원인:**
> 1. **시드 없는 `random.*` 전역 함수**: Python의 `random.random()`, `random.choice()` 등을 인자 없이 바로 호출하면, 내부적으로 모듈 전역의 숨겨진 난수 생성기 상태를 씁니다. 이 전역 상태는 프로그램 시작 시각 등으로 초기화되어 실행마다 다르므로, `rng.random()`처럼 **명시적으로 전달받은 `random.Random(seed)` 인스턴스**를 통해서만 난수를 뽑아야 재현이 됩니다. `grep -v "Random("`은 "이미 `Random(...)` 객체를 통해 호출하는 정상적인 줄은 제외하고, 전역 함수를 직접 부르는 의심스러운 줄만 걸러내라"는 필터입니다.
> 2. **`numpy.random`도 마찬가지 문제**: numpy를 쓴다면 똑같이 전역 상태 문제가 있습니다. `numpy.random.Generator(numpy.random.PCG64(seed))`는 numpy의 최신 권장 방식으로, PCG64라는 특정 난수 알고리즘을 시드로 초기화한 뒤 그 인스턴스(`Generator`)를 명시적으로 여기저기 전달해서 쓰는 패턴입니다(레거시 `numpy.random.seed(...)` 전역 설정 방식보다 안전).
> 3. **wall-clock(실제 경과 시간) 기반 반복의 문제**: "시간이 될 때까지 계속 반복"하는 루프는 CPU 속도, 시스템 부하, 다른 프로세스와의 경합에 따라 매번 다른 횟수만큼 반복됩니다. 반복 횟수가 다르면 당연히 최종 결과도 달라질 수 있으므로, 결정성 모드에서는 "시간이 아니라 정해진 반복 횟수(`det_starts`, `det_alns_iters`, `max_iters`)로 멈춘다"로 바꿔야 합니다.
> 4. **`dict`/`set`의 순회 순서**: Python 3.7+ 부터 `dict`는 삽입 순서를 보장하지만, `set`은 여전히 순서가 원소들의 해시값에 의해 결정되며 이 해시값은 (문자열의 경우 특히) 프로세스마다 다를 수 있습니다(`PYTHONHASHSEED` 환경변수가 무작위인 게 Python 기본값). 그래서 "여러 후보 중 하나를 고르는" 로직에서 `set`을 순회하면서 순서에 의존하고 있다면, 그 순서 자체가 프로세스마다 바뀌어 다른 결과를 낳을 수 있습니다. `sorted()`로 명시적 정렬을 강제하면 이 문제가 사라집니다.

- [ ] **Step 4: 통과 확인**

Run: `conda run -n ogc2026 python -m pytest tests/test_determinism.py -v`
Expected: 3 passed (in-process 2회, subprocess 2회 모두 동일 출력)

- [ ] **Step 5: Commit**

```bash
git add solver/ tests/test_determinism.py
git commit -m "feat(week4): OGC_SEED/OGC_DET 고정 시드 재현성 보장"
```

### Task 4: 예산 스트레스 테스트 — 30s/60s/300s/1800s wall-clock 보증

**Files:**
- Modify: `pyproject.toml` (pytest 마커 등록 + 기본 제외)
- Test: `tests/test_budget_stress.py`

**Interfaces:**
- Consumes: Task 2의 `shell.solve`, `utils.check_feasibility`, conftest `all_probs`.
- Produces: pytest 마커 `slow`(장시간), `stress`(스트레스 인스턴스), `sim`(firejail 필요) — 기본 실행에서 제외. 이후 Task 6/8/9가 같은 마커를 사용.

> 💡 **설명 — "pytest 마커(marker)"란:** pytest 테스트 함수에 `@pytest.mark.이름`을 붙여 "이 테스트는 이런 성격이다"라고 분류표를 붙이는 기능입니다. 여기서 `slow`/`stress`/`sim` 세 마커를 새로 등록하는 이유는, 이번 주에 추가되는 테스트 중 일부는 **몇 분~수십 분씩 걸리거나(slow), 별도 준비물(스트레스 인스턴스 생성, stress)이 필요하거나, firejail 같은 시스템 도구 설치가 필요(sim)**해서, 평소 개발 중 빠르게 돌리는 기본 `pytest` 실행에서는 자동으로 빼고 싶기 때문입니다. `addopts = "-m 'not slow and not stress and not sim'"`을 `pyproject.toml`에 넣으면 "이 마커가 붙은 테스트는 명시적으로 `-m slow`처럼 지정하지 않는 한 항상 건너뛴다"는 기본 정책이 됩니다. 이렇게 하면 평소엔 빠른 테스트만 돌리다가, 필요할 때(예: 제출 직전)만 `-m "stress or sim"`처럼 명시적으로 무거운 테스트를 돌릴 수 있습니다 — 개발 생산성과 철저한 검증을 양립시키는 표준적인 pytest 관례입니다.

- [ ] **Step 1: pyproject.toml에 마커 등록**

`pyproject.toml`의 `[tool.pytest.ini_options]` 테이블에 다음 두 키를 추가(기존 키는 유지):

```toml
[tool.pytest.ini_options]
addopts = "-m 'not slow and not stress and not sim'"
markers = [
    "slow: 장시간(300s+) 예산/메모리 테스트 - OGC_LONG=1 및 -m slow로 실행",
    "stress: data/stress 스트레스 인스턴스 게이트",
    "sim: firejail/cpulimit 필요한 서버 시뮬레이션",
]
```

확인: `conda run -n ogc2026 python -m pytest tests -q --collect-only | tail -3` → 에러 없이 수집되고, 이후 작성될 마커 테스트는 기본 실행에서 deselect 된다.

- [ ] **Step 2: 실패(신규) 테스트 작성** — `tests/test_budget_stress.py`

```python
import os
import time

import pytest
from utils import check_feasibility

# 반환 마진: Budget(safety=0.08)이 92% 시점 반환을 설계 목표로 하므로
# 측정 상한은 97%로 잡는다 (utils 최종검증 포함 여유 5%p).
MARGIN = 0.97


def _run(prob, tl):
    from solver.shell import solve
    t0 = time.monotonic()
    sol = solve(prob, tl)
    elapsed = time.monotonic() - t0
    return sol, elapsed


@pytest.mark.parametrize("name,tl", [
    ("prob_1", 30), ("prob_23", 30),      # 고밀도 30s가 최난관
    ("prob_1", 60), ("prob_23", 60),
])
def test_budget_short(all_probs, name, tl):
    prob = all_probs[name]
    sol, elapsed = _run(prob, tl)
    assert elapsed <= tl * MARGIN, f"{name}@{tl}s: {elapsed:.1f}s > {tl * MARGIN:.1f}s"
    res = check_feasibility(prob, sol)
    assert res["feasible"] is True, (name, res["stage"], res["violations"][:3])


@pytest.mark.slow
@pytest.mark.parametrize("name,tl", [("prob_23", 300), ("prob_23", 1800)])
def test_budget_long(all_probs, name, tl):
    if tl >= 1800 and not os.environ.get("OGC_LONG"):
        pytest.skip("1800s 케이스는 OGC_LONG=1 로만 실행")
    prob = all_probs[name]
    sol, elapsed = _run(prob, tl)
    assert elapsed <= tl * MARGIN, f"{name}@{tl}s: {elapsed:.1f}s"
    assert check_feasibility(prob, sol)["feasible"] is True


def test_tiny_budget_still_returns(all_probs):
    """비정상적으로 작은 예산(5s)에도 trivial 티어라도 시간 내 반환."""
    prob = all_probs["prob_1"]
    sol, elapsed = _run(prob, 5)
    assert elapsed <= 5.0
    assert check_feasibility(prob, sol)["feasible"] is True
```

> 💡 **설명 — 왜 하필 30/60/300/1800초의 4가지 시간대를 테스트하는가:** Global Constraints에서 "시간제한 수 분~30분(비공개)"이라 했으므로, 실제 채점 서버가 몇 초를 줄지 정확히 알 수 없습니다. 그래서 **짧은 쪽 극단(30초 — 알고리즘이 제대로 몸풀 시간도 없는 경우)부터 긴 쪽 극단(1800초=30분 — 자원을 오래 쓰다 보면 새로운 종류의 버그, 예를 들어 메모리 누적이나 느린 연산의 시간 초과가 나타날 수 있는 경우)까지 골고루** 검증해 어떤 실제 시간제한이 주어져도 안전하다는 확신을 얻으려는 것입니다. `MARGIN = 0.97`이라는 상한은 `Budget(safety=0.08)`(92% 시점에 반환 시작)에 "utils 최종 검증까지 마치고 실제로 함수가 리턴하는 데 걸리는 약간의 시간"을 더한 현실적인 여유(5%p)입니다 — 설계 목표(92%)와 테스트 판정 기준(97%)을 살짝 다르게 두는 것은 "목표는 타이트하게, 테스트 통과 기준은 약간의 여유를 두어 사소한 타이밍 흔들림에 테스트가 깜빡이지(flaky) 않도록" 하는 실용적 선택입니다.
>
> **`test_tiny_budget_still_returns`(5초)**: 극단적으로 짧은 예산에서는 construct 한 번 돌 시간도 없을 수 있는데, 이때도 `_phase_setup`에서 미리 확보해둔 trivial 해가 있으므로 최소한의 feasible한 결과는 반드시 나와야 한다는 걸 검증합니다.

- [ ] **Step 3: 단기 케이스 실행**

Run: `conda run -n ogc2026 python -m pytest tests/test_budget_stress.py -v`
Expected: `test_budget_short` 4건 + `test_tiny_budget_still_returns` PASS (slow는 deselect). 실패 시 흔한 원인: (a) `_finalize`의 utils 검증이 마진 밖으로 밀림 → `Budget` safety 내에서 검증 시간을 선확보하도록 `budget.phase("finalize", ...)` 예약 추가, (b) ALNS가 `budget.expired()`를 반복 내부에서 확인하지 않음 → 루프 선두에 체크 삽입. 원인 수정 전 진행 금지.

- [ ] **Step 4: 장기 케이스 실행 (수동, 1회)**

Run: `OGC_LONG=1 conda run -n ogc2026 python -m pytest tests/test_budget_stress.py -m slow -v`
Expected: 300s·1800s 2건 PASS (총 ~35분 소요). 결과 로그를 확인해 1800s 케이스의 실제 반환 시각(예: ~1650s)을 기록.

> 💡 **설명 — `OGC_LONG=1`은 왜 또 필요한가 (마커 안에 마커):** `@pytest.mark.slow`만으로도 기본 실행에서는 이미 제외됩니다. 그런데 `test_budget_long`은 `-m slow`로 명시적으로 실행해도 **300초짜리는 돌지만 1800초(30분)짜리는 한 번 더 걸러서 스킵**합니다. 이건 "느린 테스트 중에서도 특히 더 느린 것"을 이중으로 방어하는 장치입니다 — 300초 테스트 2개(각 인스턴스)만 해도 이미 부담스러운데, 여기에 1800초짜리까지 매번 같이 돌리면 일상적인 검증 루틴이 너무 무거워지기 때문에, "정말 필요할 때(`OGC_LONG=1`을 명시적으로 켰을 때)만" 도는 한 단계 더 깊은 opt-in을 만든 것입니다.

- [ ] **Step 5: Commit**

```bash
git add pyproject.toml tests/test_budget_stress.py
git commit -m "test(week4): 30/60/300/1800s 예산 스트레스 - 마진 내 반환 보증"
```

### Task 5: 저하 모드(OGC_FORCE_FALLBACK) 검증 + 품질 저하 문서화

**Files:**
- Create: `experiments/fallback_quality.py`, `docs/report-assets/fallback-quality.md` (스크립트가 생성)
- Test: `tests/test_degraded_mode.py`

**Interfaces:**
- Consumes: 전제 A `core_iface.make_core`의 `OGC_FORCE_FALLBACK=1` 규약, Task 2 `shell.solve`.
- Produces: "순수 Python 경로도 소형 인스턴스에서 예산 내 feasible" 보증 + 품질 저하율 문서. Task 14 보고서가 `docs/report-assets/fallback-quality.md`를 인용.

> 💡 **설명 — "저하 모드(degraded mode)"란:** `OGC_FORCE_FALLBACK`은 1주차부터 있던 환경변수로(1주차 주석본 Task 8 참고), C++ 코어(`ogc_core.so`)가 로드되지 않을 때를 대비한 순수 Python 폴백 코어를 강제로 쓰게 만드는 스위치입니다. 지금까지는 "폴백 코어가 C++ 코어와 같은 답을 내는가"(동등성, parity)만 확인했는데, 이번 주는 한 걸음 더 나가 **"폴백 코어를 쓰는 상황에서도 시간예산 안에 feasible한 해를 낼 수 있는가"**(생존성)와 **"그럴 때 품질이 얼마나 나빠지는가"**(정도 측정)까지 확인합니다. "저하 모드"라는 이름 자체가 "정상 모드보다 성능이 떨어지지만 완전히 죽지는 않는 대체 운영 상태"를 가리키는 일반적인 시스템 설계 용어입니다(예: 웹서비스가 캐시 서버가 죽으면 느리지만 DB 직접조회로 "저하된 서비스"를 계속 제공하는 것과 같은 개념).

- [ ] **Step 1: 실패 테스트 작성** — `tests/test_degraded_mode.py`

```python
import time

import pytest
from utils import check_feasibility


@pytest.mark.parametrize("name", ["prob_1", "prob_2"])
def test_force_fallback_feasible_within_budget(all_probs, name, monkeypatch):
    """순수 Python 경로(.so 로드 실패 가정)도 60s 내 feasible을 반환해야 한다."""
    monkeypatch.setenv("OGC_FORCE_FALLBACK", "1")
    from solver.shell import solve
    prob = all_probs[name]
    t0 = time.monotonic()
    sol = solve(prob, 60)
    elapsed = time.monotonic() - t0
    assert elapsed <= 60 * 0.97, f"{name}: {elapsed:.1f}s"
    res = check_feasibility(prob, sol)
    assert res["feasible"] is True, (name, res["stage"])


def test_fallback_env_does_not_leak(all_probs, monkeypatch):
    """저하 모드 후 일반 모드가 다시 C++ 코어를 쓰는지 (env 격리) 확인."""
    from solver.core_iface import make_core
    monkeypatch.setenv("OGC_FORCE_FALLBACK", "1")
    slow = make_core(all_probs["prob_1"])
    monkeypatch.delenv("OGC_FORCE_FALLBACK")
    fast = make_core(all_probs["prob_1"])
    assert type(slow).__module__ != type(fast).__module__
```

> 💡 **설명 — `test_fallback_env_does_not_leak`이 검사하는 "누수(leak)"란:** 여기서 "leak"은 메모리 누수가 아니라 **환경(설정) 상태가 의도한 범위를 벗어나 다음 호출에까지 새어나가는 것**을 가리킵니다. 예를 들어 `make_core`가 내부적으로 "한 번 fallback을 쓰기로 결정하면 그 이후로도 계속 fallback만 쓴다"는 캐시(예: 모듈 전역 변수)를 잘못 두었다면, `OGC_FORCE_FALLBACK`을 지운 뒤에도 여전히 느린 폴백 코어가 나올 수 있습니다. 이 테스트는 `type(slow).__module__ != type(fast).__module__`(반환된 객체가 서로 다른 모듈 — 하나는 `ogc_core`, 하나는 `solver.fallback_core` — 에서 왔는지)로 "환경변수가 지워지면 즉시 정상 동작(C++ 코어)으로 돌아오는지"를 확인합니다.

- [ ] **Step 2: 실행·수정**

Run: `conda run -n ogc2026 python -m pytest tests/test_degraded_mode.py -v`
Expected: PASS. FAIL이면 원인별 처방: (a) fallback 경로에서 construct가 60s를 다 먹고 utils 검증 시간이 없음 → `_phase_construct`에서 `ctx.budget.expired()`를 **블록 단위**(매 배치 시도 후)로 확인하도록 fallback 경로 한정 체크 주기 강화, (b) trivial조차 느림 → `solve_trivial`은 페어 검사 없이 시간 직렬화이므로 O(n)이어야 정상 — shapely 호출이 있으면 제거.

- [ ] **Step 3: 품질 저하 측정 스크립트 작성** — `experiments/fallback_quality.py`

```python
"""저하 모드 품질 측정: 일반 vs OGC_FORCE_FALLBACK=1, 60s, 소형 인스턴스 4개."""
import json
import os
import pathlib
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
INSTANCES = ["prob_1", "prob_2", "prob_3", "prob_4"]


def _find(name):
    for sub in ("training_instances", "train-set2"):
        p = ROOT / "data" / sub / "train" / f"{name}.json"
        if p.exists():
            return p
    raise FileNotFoundError(name)


def run_one(inst_path, fallback):
    code = (
        "import json,sys; sys.path[:0]=['solver','baseline'];"
        "from solver.shell import solve; from utils import check_feasibility;"
        f"p=json.load(open({str(inst_path)!r}));"
        "s=solve(p,60); r=check_feasibility(p,s);"
        "print(json.dumps({'feasible':r['feasible'],'objective':r['objective']}))"
    )
    env = dict(os.environ, OGC_SEED="42")
    if fallback:
        env["OGC_FORCE_FALLBACK"] = "1"
    out = subprocess.run([sys.executable, "-c", code], cwd=ROOT, env=env,
                         capture_output=True, text=True, check=True)
    return json.loads(out.stdout.strip().splitlines()[-1])


def main():
    rows = []
    for name in INSTANCES:
        p = _find(name)
        normal = run_one(p, fallback=False)
        degraded = run_one(p, fallback=True)
        ratio = (degraded["objective"] / normal["objective"]
                 if normal["objective"] else float("nan"))
        rows.append((name, normal, degraded, ratio))
    out = ROOT / "docs" / "report-assets" / "fallback-quality.md"
    out.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        "# 저하 모드(순수 Python 폴백) 품질 — 60s, seed=42",
        "",
        "| instance | 일반 obj | 폴백 obj | 폴백/일반 | 폴백 feasible |",
        "|---|---|---|---|---|",
    ]
    for name, n, d, r in rows:
        lines.append(f"| {name} | {n['objective']:.0f} | {d['objective']:.0f} "
                     f"| {r:.2f}x | {d['feasible']} |")
    lines += ["", "해석: 폴백 경로는 .so 로드 실패 시의 보험이다. 멀티스타트/ALNS 반복량이",
              "크게 줄어 목적값이 악화되지만(위 배율), feasible 반환은 유지된다."]
    out.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(out)


if __name__ == "__main__":
    main()
```

> 💡 **설명 — 이 스크립트가 매번 `subprocess`를 새로 띄워서 측정하는 이유:** `OGC_FORCE_FALLBACK` 같은 환경변수는 프로세스가 시작될 때 `core_iface.make_core`가 한 번 읽고 나면, 같은 프로세스 안에서 이미 import된 모듈(`import ogc_core`)의 상태가 남아있을 수 있어 "정상 → 폴백 → 정상"을 한 프로세스 안에서 오가며 정확히 측정하기 까다롭습니다. 매번 완전히 새로운 파이썬 프로세스를 `subprocess.run`으로 띄우면 환경변수 설정이 그 프로세스에만 적용되고 깨끗하게 격리되므로, "일반 모드"와 "폴백 모드"를 공정하게 비교할 수 있습니다. 결과 배율(예: "폴백이 일반보다 1.5배 나쁜 목적값")을 표로 남겨두는 이유는, 나중에 실제로 서버에서 C++ 코어 로드가 실패하는 최악의 시나리오가 벌어졌을 때 "그래도 이 정도 품질은 나온다"는 근거 자료가 되고, Task 14의 기술보고서에도 인용되기 때문입니다.

- [ ] **Step 4: 측정 실행·문서 확인**

Run: `conda run -n ogc2026 python experiments/fallback_quality.py && head -12 docs/report-assets/fallback-quality.md`
Expected: 4행 표 출력, 전 행 `폴백 feasible = True`, 배율(예: 1.1x~3x)이 기록됨. 배율이 1.0 미만이면 일반 경로 회귀 의심 — 원인 확인.

- [ ] **Step 5: Commit**

```bash
git add tests/test_degraded_mode.py experiments/fallback_quality.py docs/report-assets/fallback-quality.md
git commit -m "test(week4): 저하 모드 예산 내 feasible 보증 + 품질 저하 문서화"
```

### Task 6: 메모리 가드 — 최대 인스턴스 peak RSS < 14GB

**Files:**
- Test: `tests/test_memory_guard.py`

**Interfaces:**
- Consumes: Task 2 `shell.solve`, Task 7의 `data/stress/stress_n400.json`(없으면 40개 중 최대 인스턴스로 대체 — 테스트가 자동 선택).
- Produces: peak RSS 상한 회귀 테스트. 초과 시에만 NFP 캐시 축출 도입(계약 변경 요청 #2, 조건부).

> 💡 **설명 — RSS(Resident Set Size)란 무엇인가:** RSS는 한 프로세스가 실제로 물리 메모리(RAM)에 올려서 쓰고 있는 양을 말합니다(가상 메모리 주소 공간 전체가 아니라, 실제로 RAM을 점유한 부분만). Global Constraints에서 서버가 "16GB" 메모리라고 했으니, 우리 프로세스의 RSS가 이 한계를 넘으면 운영체제나 firejail의 `--rlimit-as`(주소공간 제한) 설정에 의해 **프로세스가 강제 종료**될 수 있습니다 — 이게 앞서 설명한 평가 상태 6종 중 "Algorithm process terminated unexpectedly"(-1점)의 전형적인 원인입니다. 그래서 이 테스트는 실제로 서버 한계(16GB)보다 조금 낮은 **14GB**를 안전 상한으로 잡아(2GB 여유), "가장 큰 인스턴스를 풀어도 여기를 넘지 않는지"를 회귀 테스트로 고정해둡니다.
>
> **왜 NFP 비트맵 캐시가 메모리를 많이 먹을 수 있는가 (1주차 Task 4 캐시와 연결):** 1주차 주석본에서 설명했듯, `OffsetBitmap`은 (shape_a, shape_b) 도형 쌍마다 "모든 정수 오프셋에 대한 충돌 여부"를 비트로 저장한 캐시입니다. 이 캐시는 **lazy(필요할 때만 계산)** 이지만, 한번 계산된 건 계속 메모리에 남아있습니다(1주차 계획엔 "이번 주는 스레드 안전 불필요" 정도만 언급되고 캐시 축출은 명시적으로 다루지 않았음). 문제는 블록 수(n)와 방향(orientation) 수가 늘어나면 **shape 쌍의 개수가 조합적으로(대략 n² 또는 shape² 규모로) 폭증**한다는 점입니다 — 설계서에 따르면 n=100 규모에서는 shape 796개, 쌍당 평균 43바이트, 전체 쌍 약 0.1GB 수준이지만, 스트레스 인스턴스(Task 7)가 만드는 n=400 규모에서는 쌍의 수가 이론상 16배(400²/100² 규모)까지 늘 수 있어 GB 단위로 커질 위험이 있습니다. 이게 "메모리 가드"가 필요한 이유입니다 — 코드 로직 자체는 정확해도, 큰 인스턴스에서 캐시가 무한정 자라나 메모리를 다 써버리는 것도 "죽음"의 한 형태입니다.
>
> **LRU 캐시 제한이란:** LRU는 "Least Recently Used"(가장 최근에 쓰이지 않은 것)의 약자로, 캐시가 가득 찼을 때 "가장 오랫동안 사용되지 않은 항목부터 밀어내는(축출, eviction)" 고전적인 캐시 교체 정책입니다. 아래 (조건부) Step 3에서 말하는 `set_nfp_cache_limit(max_pairs)`가 바로 이 정책을 NFP 캐시에 적용해, "캐시에 쌍이 `max_pairs`개를 넘으면 가장 오래된(안 쓰인) 쌍부터 지워서 메모리 상한을 유지"하는 장치입니다. 다만 캐시를 지우면 그 쌍이 나중에 다시 필요할 때 재계산해야 하므로 약간의 속도 손해를 감수하는 트레이드오프입니다 — "메모리를 지키기 위해 약간의 속도를 희생"하는 전형적인 자원 관리 결정입니다.

- [ ] **Step 1: 실패(신규) 테스트 작성** — `tests/test_memory_guard.py`

```python
import json
import pathlib
import subprocess
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
LIMIT_KB = 14 * 1024 * 1024          # 14GB (서버 16GB에서 2GB 여유)


def _largest_instance():
    """stress_n400가 있으면 그것, 없으면 40개 중 n_blocks*n_bays 최대."""
    stress = ROOT / "data" / "stress" / "stress_n400.json"
    if stress.exists():
        return stress
    best, best_key = None, -1
    for sub in ("training_instances", "train-set2"):
        for p in sorted((ROOT / "data" / sub / "train").glob("*.json")):
            d = json.loads(p.read_text())
            key = len(d["blocks"]) * len(d["bays"])
            if key > best_key:
                best, best_key = p, key
    return best


@pytest.mark.slow
def test_peak_rss_under_14gb():
    inst = _largest_instance()
    code = (
        "import json,sys,resource;"
        "sys.path[:0]=['solver','baseline'];"
        "from solver.shell import solve;"
        f"p=json.load(open({str(inst)!r}));"
        "solve(p,120);"
        "print(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss)"
    )
    out = subprocess.run([sys.executable, "-c", code], cwd=ROOT,
                         capture_output=True, text=True, check=True)
    peak_kb = int(out.stdout.strip().splitlines()[-1])
    print(f"peak RSS = {peak_kb / 1024 / 1024:.2f} GB on {inst.name}")
    assert peak_kb < LIMIT_KB, f"peak {peak_kb} KB >= 14GB 한계"
```

> 💡 **설명 — `resource.getrusage(resource.RUSAGE_SELF).ru_maxrss`란:** `resource`는 Python 표준 라이브러리로, 프로세스의 자원 사용량 통계를 운영체제에서 직접 조회합니다. `RUSAGE_SELF`는 "현재 프로세스 자신"을 뜻하고, `ru_maxrss`는 그 프로세스가 지금까지 실행되는 동안 **최고점(peak)** 으로 찍었던 RSS 값입니다(리눅스에서는 KB 단위). "지금 이 순간"이 아니라 "역대 최고치"를 재는 이유는, 우리가 걱정하는 건 "언젠가 한순간이라도 16GB를 넘어서 프로세스가 죽는 것"이지 "평균적으로 메모리를 얼마나 쓰는가"가 아니기 때문입니다 — 짧은 순간이라도 스파이크가 나면 그 순간에 죽습니다. 여기서도 `subprocess`로 새 프로세스를 띄우는 이유는(Task 5와 같은 이유) 이 측정 자체가 "이 인스턴스를 풀 때만 딱 쓰는 메모리"를 깨끗하게 재기 위해서입니다 — 같은 프로세스에서 이것저것 테스트를 연달아 돌리면 이전 테스트가 남긴 메모리까지 섞여 측정이 왜곡됩니다.

- [ ] **Step 2: 실행**

Run: `conda run -n ogc2026 python -m pytest tests/test_memory_guard.py -m slow -v -s`
Expected: PASS + `peak RSS = x.xx GB` 출력. 설계 근거(설계서 §2): n=100 전쌍 NFP ~0.1GB → n=400은 쌍수 16배 ≈ 1.6GB + 상태/파이썬 오버헤드로 수 GB 수준이 정상.

- [ ] **Step 3: (조건부) 14GB 초과 시에만 — NFP 캐시 축출 도입**

초과 시 절차: ① `ogc_core`에 LRU 상한 추가 — C++ `nfp.cpp`의 해시맵을 "삽입 시 상한 초과면 가장 오래된 키 제거"로 바꾸고 바인딩 `Core.set_nfp_cache_limit(max_pairs: int)`·`Core.nfp_cache_info() -> tuple[pairs, bytes]` 추가(계약 변경 요청 #2 승인 후). ② `_phase_setup`에서 `n_blocks > 200`일 때 `set_nfp_cache_limit(120_000)` 호출. ③ 본 테스트 재실행으로 상한 준수 확인. **14GB 미만이면 이 단계를 건너뛴다(YAGNI).**

> 💡 **설명 — "조건부 구현"과 "YAGNI"라는 개발 원칙:** 이 Step은 "무조건 지금 구현하라"가 아니라 "실제로 문제가 생겼을 때만 구현하라"는 지시입니다. **YAGNI**("You Aren't Gonna Need It", "그거 필요 없을 거야")는 애자일 소프트웨어 개발에서 유명한 원칙으로, "지금 당장 필요하지 않은 기능을 미리 만들어두지 말라"는 뜻입니다. 왜 여기 적용되는가 하면, LRU 캐시 축출 로직은 C++ 코드를 건드리는 데다(`nfp.cpp` 수정) 버그가 생기면 정확성(캐시가 지워진 뒤 재계산이 진짜 원래와 같은 값을 내는지)까지 다시 검증해야 하는 **비용이 꽤 큰 작업**입니다. 만약 실측(Step 2) 결과 어차피 14GB를 안 넘긴다면, 이 복잡한 기능을 미리 만들어두는 건 "혹시 몰라서" 들이는 불필요한 비용과 리스크입니다. 그래서 1주차 계약 부록(원본에서 인용된 "계약 부록 - 주차 간 조정 확정" 5번 항목)에서도 이 API를 "발동 조건 충족 시에만 구현, 미충족 시 구현 금지"라고 **사전에 미리 승인**해둔 것입니다 — "필요해지면 이 이름으로 이렇게 만들어도 된다"고 미리 합의는 해두되, 실제로 필요해지기 전까지는 안 만드는 것이 이 프로젝트의 방침입니다.

- [ ] **Step 4: Commit**

```bash
git add tests/test_memory_guard.py
git commit -m "test(week4): 최대 인스턴스 peak RSS < 14GB 메모리 가드"
```

### Task 7: 적대적/엣지 인스턴스 생성기 `experiments/gen_stress.py`

**Files:**
- Create: `experiments/gen_stress.py`, `data/stress/*.json` (생성물, git 포함)
- Modify: `tests/conftest.py` (`stress_probs` fixture 추가)
- Test: `tests/test_gen_stress.py`

**Interfaces:**
- Consumes: 인스턴스 JSON 스키마 (`name/bays/blocks/weights`; block = `release_time, due_date, processing_time, workload, bay_preferences, shape[{orientation, layers}]`; layer0 첫 정점 (0.0, 0.0), 좌표 소수 ≤4자리, `bay_preferences` 합 100 정수).
- Produces: `python experiments/gen_stress.py --out data/stress` → 결정적(시드 고정) 스트레스 인스턴스 9개. conftest `stress_probs` fixture. Task 6/8/9/13이 이 파일들을 사용. 커버 리스크(설계서 §8 "숨은 인스턴스 분포 변화"): n>100(200/400), bay 4~8개, 레이어 3~5, 장기 지평, 극한 밀도, bay에 딱 맞는 블록, 단일 bay(Z2 엣지), 방향 선택지 1개.

> 💡 **설명 — "숨은 인스턴스 분포 변화"라는 리스크, 그리고 왜 인스턴스를 "생성"까지 해야 하는가:** 지금까지 우리가 가진 건 40개의 학습(training) 인스턴스뿐입니다. 그런데 실제 채점에 쓰이는 인스턴스는 이 40개와 "통계적으로 비슷하지만 다른" 새로운 인스턴스들일 가능성이 높습니다(대회에서 흔한 관례 — 학습셋과 평가셋을 분리). 문제는 우리 알고리즘이 40개에서만 잘 맞춰져 있고, 예를 들어 "블록이 400개나 되는 경우"나 "bay가 8개나 되는 경우"처럼 **40개 학습셋에는 없던 극단적인 형태**가 평가셋에 있다면 거기서 처음 마주치는 버그(인덱스 초과, 시간 초과, 메모리 폭증 등)가 튀어나올 수 있다는 것입니다. 이 리스크를 "숨은 인스턴스 분포 변화"라 부릅니다.
>
> 대응 전략은 "가만히 기다리다가 채점에서 터지는 것"이 아니라, **우리가 먼저 그런 극단적인 상황을 인위적으로(합성, synthetic) 만들어서 지금 이 자리에서 검증**해버리는 것입니다. 이건 Task 3의 "fuzz 테스트"(무작위 입력으로 숨은 버그를 찾기)와 철학이 같지만, 여기서는 완전 무작위가 아니라 **"이런 축(axis)에서 극단적일 것이다"라고 미리 예상한 9가지 시나리오**(n=200/400, bay 8개, 레이어 5개, 긴 지평, 고밀도, 꽉 끼는 블록, bay 1개, 방향 선택지 1개)를 목표로 삼아 **적대적(adversarial)** 으로, 즉 "우리 알고리즘을 일부러 괴롭히려는 의도로" 설계된 인스턴스를 만듭니다. 아래 목록의 각 항목이 왜 알고리즘을 괴롭히는지 짚어보면:
> - **n=200/400 (더 많은 블록)**: Task 6에서 설명한 메모리·시간 폭증 위험.
> - **bay 4~8개**: 1주차 코드가 "bay가 2개인 경우"만 가정하고 뭔가를 하드코딩하지 않았는지 드러냄.
> - **레이어 3~5개**: j≥k 크레인 스윕 규칙(1주차 0.4절)의 조합 수가 레이어 수의 제곱에 비례해 늘어나므로 그 경로의 성능·정확성을 시험.
> - **극한 밀도**: bay 공간 대비 블록 면적 총합이 거의 꽉 차서, "배치할 자리를 못 찾는" 상황을 유도 — feasible 해를 못 낼 위험을 시험.
> - **bay에 딱 맞는 블록**: 블록의 bounding box가 bay 크기와 정확히 일치 — "경계가 같으면 (`<=`) 허용해야 하는데 실수로 `<`로 짜서 거부하는" 흔한 부등호 버그(off-by-one 류)를 잡아냄.
> - **단일 bay(Z2 엣지)**: Z2(작업량 불균형)는 원래 "여러 bay 사이의 편차"를 재는 값인데, bay가 1개면 비교할 상대가 없어 "bay 쌍을 순회하는 코드가 빈 리스트에서 죽지 않는지"(아래 `max(..., default=0)` 처방 참고)를 시험.
> - **방향 선택지 1개**: 디코더가 "여러 방향 중 고른다"는 걸 당연시하고 짠 코드가, 선택지가 1개뿐일 때도 정상 동작하는지 시험.

- [ ] **Step 1: 실패 테스트 작성** — `tests/test_gen_stress.py`

```python
import json
import subprocess
import sys
import pathlib

import pytest
from shapely.geometry import Polygon

ROOT = pathlib.Path(__file__).resolve().parents[1]

EXPECTED = {
    "stress_n200": dict(n=200), "stress_n400": dict(n=400),
    "stress_bays8": dict(m=8), "stress_layers5": dict(max_layers=5),
    "stress_horizon300": dict(horizon=300), "stress_dense": dict(),
    "stress_tight": dict(), "stress_singlebay": dict(m=1),
    "stress_noorient": dict(orients=1),
}


@pytest.fixture(scope="module")
def generated(tmp_path_factory):
    out = tmp_path_factory.mktemp("stress")
    subprocess.run([sys.executable, "experiments/gen_stress.py", "--out", str(out)],
                   cwd=ROOT, check=True)
    return {p.stem: json.loads(p.read_text()) for p in sorted(out.glob("*.json"))}


def test_all_instances_generated(generated):
    assert set(generated) == set(EXPECTED)


def test_schema_valid(generated):
    for name, prob in generated.items():
        assert set(prob) == {"name", "bays", "blocks", "weights"}
        assert set(prob["weights"]) == {"w1", "w2", "w3"}
        for b in prob["blocks"]:
            assert b["release_time"] >= 0 and b["processing_time"] >= 1
            assert b["due_date"] >= b["release_time"] + b["processing_time"]
            assert len(b["bay_preferences"]) == len(prob["bays"])
            assert sum(b["bay_preferences"]) == 100
            assert all(isinstance(v, int) for v in b["bay_preferences"])
            for s in b["shape"]:
                l0 = s["layers"][0]
                assert l0[0] == [0.0, 0.0]              # 기준점 규약
                for layer in s["layers"]:
                    poly = Polygon(layer)
                    assert poly.is_valid and poly.area > 0
                    for x, y in layer:                   # 소수 4자리
                        assert x == round(x, 4) and y == round(y, 4)


def test_axes_of_stress(generated):
    g = generated
    assert len(g["stress_n200"]["blocks"]) == 200
    assert len(g["stress_n400"]["blocks"]) == 400
    assert len(g["stress_bays8"]["bays"]) == 8
    assert max(len(s["layers"]) for b in g["stress_layers5"]["blocks"]
               for s in b["shape"]) == 5
    assert max(b["due_date"] for b in g["stress_horizon300"]["blocks"]) >= 300
    assert len(g["stress_singlebay"]["bays"]) == 1
    assert all(len(b["shape"]) == 1 for b in g["stress_noorient"]["blocks"])
    # tight: bbox가 bay 치수와 정확히 일치하는 블록 존재
    bay = g["stress_tight"]["bays"][0]
    def bbox(layers):
        xs = [x for l in layers for x, _ in l]; ys = [y for l in layers for _, y in l]
        return max(xs) - min(xs), max(ys) - min(ys)
    assert any(bbox(b["shape"][0]["layers"]) == (bay["width"], bay["height"])
               for b in g["stress_tight"]["blocks"])


def test_every_block_fits_some_bay(generated):
    """trivial 폴백 성립 조건: 모든 블록이 orient 0로 어떤 bay엔 들어감."""
    for name, prob in generated.items():
        for i, b in enumerate(prob["blocks"]):
            layers = b["shape"][0]["layers"]
            xs = [x for l in layers for x, _ in l]; ys = [y for l in layers for _, y in l]
            w, h = max(xs) - min(xs), max(ys) - min(ys)
            assert any(w <= bay["width"] and h <= bay["height"]
                       for bay in prob["bays"]), (name, i)


def test_deterministic_regeneration(tmp_path):
    for d in ("a", "b"):
        subprocess.run([sys.executable, "experiments/gen_stress.py",
                        "--out", str(tmp_path / d)], cwd=ROOT, check=True)
    for p in sorted((tmp_path / "a").glob("*.json")):
        assert p.read_bytes() == (tmp_path / "b" / p.name).read_bytes()
```

> 💡 **설명 — `test_every_block_fits_some_bay`가 검증하는 "trivial 폴백 성립 조건"이 왜 중요한가:** 1주차 Task 10에서 만든 `trivial.solve_trivial`(최후 보루)은 "모든 블록을 orient 0(첫 번째 방향)으로, 어딘가 한 bay에는 들어갈 수 있다"는 전제 위에서 동작합니다(그래야 항상 feasible한 배치를 만들 수 있음). 만약 스트레스 생성기가 실수로 "그 어떤 bay에도 안 들어가는 블록"을 만들어버리면, 최후 보루조차 작동하지 않는 인스턴스를 우리 스스로 만든 셈이 되어 테스트의 의미가 없어집니다. 그래서 생성기 자체(`_mk_instance`의 while 루프, 아래 구현 참고)가 "이 조건을 만족할 때까지 블록 크기를 줄인다"는 자기 검증 로직을 갖고 있고, 이 테스트가 그 보장이 실제로 지켜지는지 다시 한번 확인합니다.
>
> **`test_deterministic_regeneration`**: 스트레스 생성기 자체도 "같은 코드 = 항상 같은 바이트의 출력"이어야 한다는 걸 확인합니다(Task 3에서 배운 결정론적 실행과 같은 원칙 — 여기서는 알고리즘이 아니라 "테스트 데이터 생성기"에 적용). 왜 중요한가: 만약 스트레스 인스턴스가 실행할 때마다 바뀐다면, "어제는 통과했는데 오늘은 실패한다"는 현상이 생겨도 그게 "코드가 나빠져서"인지 "우연히 오늘 더 어려운 인스턴스가 생성되어서"인지 구분할 수 없게 됩니다. 결정적 생성을 보장해야 `data/stress/*.json`을 git에 커밋해서 "고정된 회귀 테스트 셋"으로 쓸 수 있습니다.

- [ ] **Step 2: 실패 확인**

Run: `conda run -n ogc2026 python -m pytest tests/test_gen_stress.py -v`
Expected: FAIL (`gen_stress.py` 부재)

- [ ] **Step 3: 생성기 구현** — `experiments/gen_stress.py`

```python
"""숨은 인스턴스 리스크(설계서 §8)를 겨냥한 합성 스트레스 인스턴스 생성기.

결정적(고정 시드): 같은 코드 = 같은 바이트 출력. 모든 블록은 orient 0으로
최소 1개 bay에 들어가도록 보장 → trivial 폴백이 항상 성립.
"""
import argparse
import json
import pathlib
import random


def _round4(v):
    return round(float(v), 4)


def _rect(w, h):
    return [[0.0, 0.0], [_round4(w), 0.0], [_round4(w), _round4(h)], [0.0, _round4(h)]]


def _lshape(w, h, cw, ch):
    # 반시계 L자 (cw<w, ch<h)
    return [[0.0, 0.0], [_round4(w), 0.0], [_round4(w), _round4(ch)],
            [_round4(cw), _round4(ch)], [_round4(cw), _round4(h)], [0.0, _round4(h)]]


def _inset_rect(w, h, m):
    return [[_round4(m), _round4(m)], [_round4(w - m), _round4(m)],
            [_round4(w - m), _round4(h - m)], [_round4(m), _round4(h - m)]]


def _orient(layers, k):
    """k*90도 회전 후 layer0 첫 정점이 (0,0)이 되도록 전 레이어 평행이동."""
    rot = []
    for layer in layers:
        vs = [list(v) for v in layer]
        for _ in range(k % 4):
            vs = [[-y, x] for x, y in vs]
        rot.append(vs)
    ax, ay = rot[0][0]
    return [[[_round4(x - ax), _round4(y - ay)] for x, y in layer] for layer in rot]


def _prefs(n_bays, rng):
    if n_bays == 1:
        return [100]
    raw = [rng.randint(1, 10) for _ in range(n_bays)]
    total = sum(raw)
    prefs = [r * 100 // total for r in raw]
    prefs[raw.index(max(raw))] += 100 - sum(prefs)   # 잔여를 최대 항목에
    return prefs


def _mk_block(rng, bays, horizon, n_layers, n_orients, dims, slack_zero_frac):
    w, h = dims
    frac = rng.choice([0.0, 0.25, 0.4166, 0.8621])   # 소수 좌표 스트레스
    w = _round4(max(2.0, w - frac))
    h = _round4(max(2.0, h))
    base = _lshape(w, h, _round4(w * 0.6), _round4(h * 0.6)) if (
        rng.random() < 0.3 and w >= 4 and h >= 4) else _rect(w, h)
    layers = [base]
    for k in range(1, n_layers):
        m = min(0.5 * k, (min(w, h) - 1.0) / 2)
        if w * 0.6 - 2 * m > 0.5 and rng.random() < 0.5 and len(base) == 6:
            layers.append(_inset_rect(_round4(w * 0.6), h, m))   # L의 왼쪽 다리 안
        else:
            layers.append(_inset_rect(min(w, _round4(w * 0.6)) if len(base) == 6 else w,
                                      h, m))
    shape = [{"orientation": k, "layers": _orient(layers, k)}
             for k in range(n_orients)]
    p = rng.randint(3, 10)
    r = rng.randint(0, max(0, int(horizon * 0.6)))
    slack = 0 if rng.random() < slack_zero_frac else rng.randint(1, 8)
    return {
        "release_time": r, "due_date": r + p + slack, "processing_time": p,
        "workload": rng.randint(10, 100), "bay_preferences": _prefs(len(bays), rng),
        "shape": shape,
    }


def _mk_instance(name, seed, n, bays, horizon, max_layers, n_orients,
                 dim_lo=(4, 4), dim_hi=(14, 12), slack_zero_frac=0.25,
                 tight_frac=0.0):
    rng = random.Random(seed)
    blocks = []
    for i in range(n):
        if rng.random() < tight_frac:
            bay = bays[rng.randrange(len(bays))]
            dims = (float(bay["width"]), float(bay["height"]))   # bay에 정확히 맞음
            b = _mk_block(rng, bays, horizon, 1, 1, dims, slack_zero_frac)
            # 정확 일치 보장: frac 감산 무효화
            b["shape"] = [{"orientation": 0,
                           "layers": [_rect(bay["width"], bay["height"])]}]
        else:
            dims = (rng.uniform(*[dim_lo[0], dim_hi[0]]),
                    rng.uniform(*[dim_lo[1], dim_hi[1]]))
            b = _mk_block(rng, bays, horizon,
                          rng.randint(1, max_layers), rng.randint(1, n_orients),
                          dims, slack_zero_frac)
        # feasibility 보장: orient 0이 어느 bay에도 안 들어가면 축소
        while True:
            ls = b["shape"][0]["layers"]
            xs = [x for l in ls for x, _ in l]; ys = [y for l in ls for _, y in l]
            bw, bh = max(xs) - min(xs), max(ys) - min(ys)
            if any(bw <= bb["width"] and bh <= bb["height"] for bb in bays):
                break
            b["shape"] = [{"orientation": 0,
                           "layers": [_rect(min(bw, bays[0]["width"]) * 0.8,
                                            min(bh, bays[0]["height"]) * 0.8)]}]
        blocks.append(b)
    return {"name": name, "bays": bays, "blocks": blocks,
            "weights": {"w1": 26667, "w2": 10, "w3": 300}}


SPECS = [
    ("stress_n200", dict(seed=101, n=200, horizon=120, max_layers=2, n_orients=4,
                         bays=[{"width": 100, "height": 22}, {"width": 80, "height": 20},
                               {"width": 60, "height": 18}])),
    ("stress_n400", dict(seed=102, n=400, horizon=200, max_layers=2, n_orients=4,
                         bays=[{"width": 120, "height": 25}, {"width": 100, "height": 22},
                               {"width": 80, "height": 20}, {"width": 60, "height": 18}])),
    ("stress_bays8", dict(seed=103, n=160, horizon=100, max_layers=2, n_orients=4,
                          bays=[{"width": 40 + 10 * j, "height": 16 + j}
                                for j in range(8)])),
    ("stress_layers5", dict(seed=104, n=100, horizon=80, max_layers=5, n_orients=4,
                            bays=[{"width": 80, "height": 20}, {"width": 60, "height": 18}])),
    ("stress_horizon300", dict(seed=105, n=120, horizon=300, max_layers=2, n_orients=4,
                               bays=[{"width": 70, "height": 20}, {"width": 60, "height": 18}])),
    ("stress_dense", dict(seed=106, n=120, horizon=60, max_layers=2, n_orients=4,
                          dim_lo=(8, 8), dim_hi=(18, 14), slack_zero_frac=0.4,
                          bays=[{"width": 60, "height": 20}, {"width": 50, "height": 18}])),
    ("stress_tight", dict(seed=107, n=60, horizon=90, max_layers=2, n_orients=4,
                          tight_frac=0.12,
                          bays=[{"width": 30, "height": 15}, {"width": 45, "height": 18}])),
    ("stress_singlebay", dict(seed=108, n=60, horizon=80, max_layers=2, n_orients=4,
                              bays=[{"width": 90, "height": 22}])),
    ("stress_noorient", dict(seed=109, n=100, horizon=80, max_layers=2, n_orients=1,
                             bays=[{"width": 80, "height": 20}, {"width": 60, "height": 18}])),
]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="data/stress")
    args = ap.parse_args()
    out = pathlib.Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    for name, kw in SPECS:
        inst = _mk_instance(name, **kw)
        path = out / f"{name}.json"
        path.write_text(json.dumps(inst, sort_keys=True), encoding="utf-8")
        print(f"{path}  n={len(inst['blocks'])} m={len(inst['bays'])}")


if __name__ == "__main__":
    main()
```

> 💡 **설명 — 구현 세부사항 몇 가지:**
> - **`_orient(layers, k)`의 회전 트릭 `[-y, x]`**: 2D 평면에서 점 `(x, y)`를 원점 기준 반시계 방향으로 90도 회전시키면 정확히 `(-y, x)`가 됩니다(고등학교 좌표기하 공식 그대로). `for _ in range(k % 4)`로 이 90도 회전을 k번 반복하면 결국 `k*90`도 회전을 구현한 것입니다. 회전 후에는 "레이어0의 첫 정점이 (0,0)이 되도록" 다시 평행이동시켜서, 1주차에서 배운 "레퍼런스 포인트 규약"을 지킵니다.
> - **`_prefs`의 나머지 배분(`prefs[raw.index(max(raw))] += 100 - sum(prefs)`)**: `bay_preferences`는 반드시 정수이면서 합이 정확히 100이어야 하는데, 비율을 정수 나눗셈(`r * 100 // total`)으로 계산하면 반올림 오차 때문에 합이 100이 안 될 수 있습니다(예: 33+33+33=99). 그래서 "가장 선호도가 높은 항목에 남는 나머지를 몰아준다"는 간단한 트릭으로 항상 합이 정확히 100이 되도록 보정합니다.
> - **`frac = rng.choice([0.0, 0.25, 0.4166, 0.8621])`**: 일부러 소수점 4자리까지 있는 애매한 숫자들을 섞어서, 정수 스케일링(1주차 SCALE=10000) 경로에서 반올림·경계 오차가 생기지 않는지 압박하는 "소수 좌표 스트레스"입니다.
> - **`_mk_instance`의 `while True` 루프**: 앞서 설명한 "trivial 폴백 성립 조건"(모든 블록이 orient 0으로 어딘가엔 들어가야 함)을 강제로 만족시킬 때까지 블록 크기를 줄여나가는 자기 검증 루프입니다.
> - **`SPECS` 리스트**: 9개 스트레스 인스턴스 각각의 파라미터(시드, 블록 수, bay 구성, 레이어 수 등)를 한곳에 정리한 표입니다. `seed=101~109`로 각기 다른 고정 시드를 줘서, 서로 다른 9개 인스턴스가 서로 다른 내용이면서도 각각은 항상 똑같이 재현되도록 합니다.

- [ ] **Step 4: conftest fixture 추가** — `tests/conftest.py` 말미에 추가:

```python
@pytest.fixture(scope="session")
def stress_probs():
    d = ROOT / "data" / "stress"
    if not d.exists() or not list(d.glob("*.json")):
        pytest.skip("먼저 실행: python experiments/gen_stress.py --out data/stress")
    return {p.stem: json.loads(p.read_text()) for p in sorted(d.glob("*.json"))}
```

- [ ] **Step 5: 통과 확인 + 정식 생성**

Run: `conda run -n ogc2026 python -m pytest tests/test_gen_stress.py -v`
Expected: 6 passed
Run: `conda run -n ogc2026 python experiments/gen_stress.py --out data/stress`
Expected: 9개 파일 경로와 n/m 출력

- [ ] **Step 6: Commit**

```bash
git add experiments/gen_stress.py tests/test_gen_stress.py tests/conftest.py data/stress/
git commit -m "feat(week4): 적대적/엣지 스트레스 인스턴스 생성기 + 9종 생성"
```

### Task 8: 스트레스 셋 solve 게이트 — 전 인스턴스 예산 내 feasible

**Files:**
- Test: `tests/test_stress_solve.py`
- Create(테스트가 생성): `experiments/results/stress_baseline.json`

**Interfaces:**
- Consumes: Task 7 `stress_probs` fixture, Task 2 `shell.solve`, `utils.check_feasibility`.
- Produces: 스트레스 9종 전부 "예산 내 반환 + utils feasible" 게이트(마커 `stress`), 목적값 기준선 JSON(회귀 추적용). Task 15 종료 기준이 이 게이트를 포함.

> 💡 **설명 — Task 7이 "인스턴스를 만드는" 단계였다면, 이 Task는 "그 인스턴스로 실제 우리 솔버를 돌려서 진짜 통과하는지 확인"하는 단계입니다.** 인스턴스를 아무리 정교하게 만들어도, 그걸로 `shell.solve`를 실제로 돌려서 utils가 feasible이라고 인정할 때까지 검증하지 않으면 의미가 없습니다. "기준선(baseline) JSON"을 별도로 저장해두는 이유는(`stress_baseline.json`), 나중에 코드를 변경했을 때 "이 변경이 스트레스 인스턴스에서 이전보다 목적값이 나빠지지 않았는지" 비교할 수 있는 회귀 기준점을 남겨두기 위해서입니다 — 소프트웨어 엔지니어링에서 "베이스라인을 기록해두고 다음 변경과 비교한다"는 흔한 회귀 방지 패턴입니다.

- [ ] **Step 1: 실패 테스트 작성** — `tests/test_stress_solve.py`

```python
import json
import pathlib
import time

import pytest
from utils import check_feasibility

ROOT = pathlib.Path(__file__).resolve().parents[1]
MARGIN = 0.97


def _tl(prob):
    return 120 if len(prob["blocks"]) >= 300 else 60


@pytest.mark.stress
def test_stress_all_feasible_within_budget(stress_probs):
    from solver.shell import solve
    rows = []
    for name, prob in sorted(stress_probs.items()):
        tl = _tl(prob)
        t0 = time.monotonic()
        sol = solve(prob, tl)
        elapsed = time.monotonic() - t0
        assert elapsed <= tl * MARGIN, f"{name}: {elapsed:.1f}s > {tl * MARGIN:.1f}s"
        res = check_feasibility(prob, sol)
        assert res["feasible"] is True, (name, res["stage"], res["violations"][:3])
        rows.append({"instance": name, "objective": res["objective"],
                     "obj1": res["obj1"], "obj2": res["obj2"], "obj3": res["obj3"],
                     "elapsed": round(elapsed, 1), "timelimit": tl})
    out = ROOT / "experiments" / "results" / "stress_baseline.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(rows, indent=1), encoding="utf-8")


@pytest.mark.stress
def test_singlebay_z2_edge(stress_probs):
    """bay 1개면 Z2 쌍이 없어 obj2 == 0 — utils도 우리 objective도 여기서 죽으면 안 됨."""
    from solver.shell import solve
    prob = stress_probs["stress_singlebay"]
    sol = solve(prob, 60)
    res = check_feasibility(prob, sol)
    assert res["feasible"] is True and res["obj2"] == 0


@pytest.mark.stress
def test_noorient_and_tight_do_not_crash(stress_probs):
    """방향 1개 / bay 정확 일치 블록에서 인덱스·경계 예외가 없어야 한다."""
    from solver.shell import solve
    for name in ("stress_noorient", "stress_tight"):
        prob = stress_probs[name]
        sol = solve(prob, 60)
        assert check_feasibility(prob, sol)["feasible"] is True, name
```

> 💡 **설명 — 왜 n=300 이상만 timelimit을 120초로 늘리는가(`_tl` 함수):** 스트레스 인스턴스 중 `stress_n400`(블록 400개)처럼 규모가 훨씬 큰 경우, 같은 60초 안에 feasible한 해를 찾는 것 자체가 알고리즘적으로 더 어려울 수 있습니다(탐색 공간이 커지므로). 이 테스트의 목적은 "60초라는 타이트한 예산에서도 큰 인스턴스를 반드시 풀어라"가 아니라 "적절한 예산이 주어지면 큰 인스턴스에서도 죽지 않고 feasible을 낸다"는 것을 확인하는 것이므로, 실제 대회에서도 큰 인스턴스에는 더 긴 시간제한이 주어질 가능성을 감안해 현실적인 기준(120초)을 적용합니다.

- [ ] **Step 2: 실행**

Run: `conda run -n ogc2026 python -m pytest tests/test_stress_solve.py -m stress -v -s`
Expected: 3 passed (총 ~12분: 9×60~120s). FAIL 유형별 처방: (a) n=400에서 시간 초과 → `_phase_construct` 멀티스타트 횟수가 인스턴스 규모에 비례해 줄어드는지 확인(첫 construct가 예산 절반을 넘기면 이후 스타트 스킵), (b) tight 블록 infeasible → `free_positions`가 bbox==bay일 때 (0,0) 후보를 내는지 코어 경계(≤) 확인, (c) singlebay에서 예외 → Z2 계산의 bay쌍 순회가 빈 시퀀스에서 `max()` 호출하지 않는지 확인(`max(..., default=0)`).

> 💡 **설명 — `max(..., default=0)`이 왜 흔한 방어 코드 패턴인가:** Python의 내장 `max()` 함수는 인자로 빈 시퀀스(빈 리스트 등)를 받으면 `ValueError: max() arg is an empty sequence`를 던집니다. `stress_singlebay`처럼 bay가 1개뿐이면 "bay 쌍(pair)"을 순회하는 코드는 자연스럽게 빈 리스트를 만나게 되는데, 이때 `max()`를 그냥 호출하면 예외가 터집니다. `default=0`을 지정해두면 "빈 시퀀스일 땐 그냥 0을 달라"고 미리 정해둘 수 있어 이런 예외를 방지합니다 — Z2(작업량 불균형)는 애초에 "비교할 다른 bay가 없으면 불균형도 없다(=0)"는 것이 물리적으로도 맞는 값이므로, 이 기본값이 의미상으로도 올바릅니다.

- [ ] **Step 3: Commit**

```bash
git add tests/test_stress_solve.py experiments/results/stress_baseline.json
git commit -m "test(week4): 스트레스 9종 예산 내 feasible 게이트 + 기준선 기록"
```

### Task 9: 서버 시뮬레이션 — `scripts/server_sim.sh` (firejail + cpulimit)

**Files:**
- Create: `scripts/server_sim.sh`, `scripts/sim_runner.py`, `docs/server-sim.md`
- Test: `tests/test_server_sim.py`

**Interfaces:**
- Consumes: 패키지 디렉토리(= zip 언팩 결과와 동일 구조: `myalgorithm.py`, `utils.py`, `solver/` + `.so`).
- Produces: `bash scripts/server_sim.sh <pkg_dir> <timelimit_s> <inst.json>...` — 인스턴스별 `[이름] FEASIBLE obj=... | INFEASIBLE | EXCEPTION | TLE(...) | CRASH(...)` 출력, 전부 FEASIBLE이면 `SERVER-SIM PASS`/exit 0, 아니면 `SERVER-SIM FAIL`/exit 1. Task 13 리허설이 이 스크립트를 호출.

> 💡 **설명 — 지금까지의 테스트와 이 Task가 근본적으로 다른 점:** Task 1~8의 모든 테스트는 **우리 개발 환경(conda env, 일반 파이썬 프로세스) 안에서** `solver.shell.solve`를 직접 파이썬 함수로 호출해서 검증했습니다. 그런데 실제 채점 서버는 우리 코드를 이렇게 "직접 함수 호출"로 실행하지 않습니다 — **zip으로 제출된 코드를 언팩해서, firejail 샌드박스 안에서, cpulimit으로 CPU를 제한한 채, `myalgorithm.py`의 `algorithm()` 함수를 상대경로 import로 호출**합니다. 이 사이에는 여러 가지 "우리 개발 환경에서는 안 보이던" 문제가 숨어있을 수 있습니다: 상대경로 import가 실제로 되는지, 네트워크가 없어도 죽지 않는지, 메모리 제한이 실제로 걸렸을 때 무슨 일이 생기는지 등. 이 Task는 이런 것들을 **최대한 실제 서버와 비슷한 조건에서** 재현해 미리 확인하는 것입니다.

- [ ] **Step 1: 샌드박스 내부 실행기 작성** — `scripts/sim_runner.py`

```python
"""firejail 내부에서 단일 인스턴스 실행. 패키지 디렉토리에 _sim_runner.py로 복사되어
cwd=패키지 루트에서 실행된다 (서버와 동일하게 상대 import 경로만 사용)."""
import json
import sys
import time


def main():
    inst_path, tl = sys.argv[1], int(sys.argv[2])
    with open(inst_path, "r", encoding="utf-8") as f:
        prob = json.load(f)
    status, obj, feas, elapsed = "ok", None, False, 0.0
    t0 = time.monotonic()
    try:
        from myalgorithm import algorithm       # zip 루트 규약
        sol = algorithm(prob, tl)
        elapsed = time.monotonic() - t0
        import utils                            # 동봉 utils (서버가 덮어쓰는 그 파일)
        res = utils.check_feasibility(prob, sol)
        feas, obj = bool(res["feasible"]), res["objective"]
    except BaseException as e:                  # 진단용 — 서버라면 이 시점에 -1점
        status, elapsed = f"exception:{type(e).__name__}", time.monotonic() - t0
    print(json.dumps({"status": status, "feasible": feas, "objective": obj,
                      "elapsed": round(elapsed, 2), "timelimit": tl}))


if __name__ == "__main__":
    main()
```

> 💡 **설명 — 왜 `sim_runner.py`는 "패키지 디렉토리 안으로 복사"되어 실행되는가:** 이 스크립트가 `from myalgorithm import algorithm`을 쓰려면, 파이썬이 `myalgorithm.py`를 찾을 수 있는 위치에서 실행되어야 합니다. 실제 서버는 zip을 언팩한 디렉토리를 현재 작업 디렉토리(cwd)로 삼아 실행하므로, 이 시뮬레이터도 **`sim_runner.py` 자체를 패키지 루트에 복사해 넣고, 그 디렉토리에서 실행**해야 "상대 import가 실제로 되는가"까지 똑같이 재현할 수 있습니다. 만약 그냥 원래 있던 `scripts/` 폴더에서 `sys.path`를 조작해 실행한다면, "우리 개발 환경만의 특별한 편의"가 섞여 들어가 진짜 서버 조건을 재현하지 못하게 됩니다. Task 2의 `_last_resort`에서 본 `try/except BaseException`과 똑같은 이유로 여기서도 `except BaseException as e`로 정말 모든 예외를 잡아 "예외 발생 시 상태를 기록만 하고 계속 진행"합니다 — 단, 이 파일 자체는 **진단(diagnosis) 목적**이라 "만약 이게 서버라면 -1점 처리됐을 순간"이라는 걸 기록해 알려주는 역할이고, 실제 하드닝(예외를 안 던지게 만드는 것)은 이미 `solver/shell.py`(Task 2)에서 끝난 상태여야 합니다.

- [ ] **Step 2: 러너 스크립트 작성** — `scripts/server_sim.sh`

```bash
#!/usr/bin/env bash
# 평가 서버 재현: firejail(네트워크 차단, 주소공간 16GB) + cpulimit 400%.
# 사용법: scripts/server_sim.sh <package_dir> <timelimit_s> <instance.json>...
# 통과 기준: 전 인스턴스 FEASIBLE (크래시 0, TLE 0, 예외 0, infeasible 0).
set -u
PKG=$(readlink -f "$1"); TL="$2"; shift 2
for t in firejail cpulimit; do
  command -v "$t" >/dev/null 2>&1 || { echo "미설치: $t — docs/server-sim.md 참조"; exit 2; }
done
PYBIN=$(conda run -n ogc2026 python -c 'import sys; print(sys.executable)')
SCRIPT_DIR=$(cd "$(dirname "$0")" && pwd)
cp "$SCRIPT_DIR/sim_runner.py" "$PKG/_sim_runner.py"
PASS=1
for inst in "$@"; do
  name=$(basename "$inst" .json)
  cp "$(readlink -f "$inst")" "$PKG/_inst.json"
  out=$(cd "$PKG" && timeout $((TL + 60)) \
        firejail --quiet --noprofile --net=none --rlimit-as=17179869184 -- \
        cpulimit -l 400 -f -- "$PYBIN" _sim_runner.py _inst.json "$TL" 2>/dev/null \
        | tail -n 1)
  rc=$?
  if [ $rc -eq 124 ]; then verdict="TLE(hard-kill)"; PASS=0
  elif [ $rc -ne 0 ] || [ -z "$out" ]; then verdict="CRASH(rc=$rc)"; PASS=0
  else
    verdict=$(echo "$out" | "$PYBIN" -c '
import json, sys
tl = float(sys.argv[1]); r = json.loads(sys.stdin.read())
if r["status"] != "ok":
    print("EXCEPTION " + r["status"])
elif r["elapsed"] > tl:
    print("TLE(%.1fs > %ds)" % (r["elapsed"], int(tl)))
elif not r["feasible"]:
    print("INFEASIBLE")
else:
    print("FEASIBLE obj=%.0f %.1fs" % (r["objective"], r["elapsed"]))
' "$TL")
    case "$verdict" in FEASIBLE*) : ;; *) PASS=0 ;; esac
  fi
  echo "[$name] $verdict"
done
rm -f "$PKG/_inst.json" "$PKG/_sim_runner.py"
if [ $PASS -eq 1 ]; then echo "SERVER-SIM PASS"; exit 0
else echo "SERVER-SIM FAIL"; exit 1; fi
```

`chmod +x scripts/server_sim.sh` 실행.

> 💡 **설명 — 이 bash 스크립트를 한 줄씩 뜯어보면:**
> - **`--net=none`**: firejail에게 "이 프로세스는 네트워크 접근을 아예 못 하게 하라"는 옵션. 실제 서버가 "인터넷 없음"이라는 제약을 로컬에서 재현합니다.
> - **`--rlimit-as=17179869184`**: 17179869184바이트 = 정확히 16GB(2^34). "주소공간(address space) 제한"으로, 프로세스가 이 이상의 가상 메모리를 요구하면 강제로 실패시킵니다. RSS(실제 물리 메모리 사용량)와는 살짝 다른 개념(주소공간은 예약만 해두고 실제로 안 쓸 수도 있음)이지만, 아래 문서에서 설명하듯 "이 기준이 실제 서버보다 더 엄격해서, 우리가 여기서 통과하면 실제 서버에서는 더 여유 있다"는 안전한 방향의 근사입니다.
> - **`cpulimit -l 400 -f`**: CPU 사용률을 400%(= 정확히 4개 코어 풀가동)로 제한. `-f`(forked, 자식 프로세스까지 추적) 옵션으로, 우리 프로그램이 내부적으로 멀티프로세스/멀티스레드를 쓰더라도 합쳐서 400%를 넘지 못하게 합니다.
> - **`timeout $((TL + 60))`**: bash의 `timeout` 명령으로, 시뮬레이션 자체가 "타임리밋 + 60초"를 넘으면 강제 종료합니다(예상보다 훨씬 오래 걸리면 그건 이미 실패로 간주). `rc -eq 124`(124는 `timeout` 명령이 강제종료했을 때의 관례적인 종료 코드)이면 "TLE(hard-kill)"로 표시합니다.
> - **`| tail -n 1`**: firejail이나 cpulimit이 시작할 때 출력하는 로그(배너 등)를 무시하고, 우리 `sim_runner.py`가 마지막에 출력한 JSON 한 줄만 취합니다.
> - **bash에서 `verdict`를 만들 때 다시 파이썬을 파이프로 연결**(`echo "$out" | "$PYBIN" -c '...'`): bash 자체는 JSON을 파싱하거나 숫자를 깔끔하게 포맷하는 데 서투르므로, JSON 해석과 사람이 읽기 좋은 문자열 조립은 다시 짧은 파이썬 한 줄 스크립트에 맡기는 실용적인 패턴입니다.
> - **`case "$verdict" in FEASIBLE*) : ;; *) PASS=0 ;; esac`**: bash의 `case`문으로 "verdict 문자열이 `FEASIBLE`로 시작하면 아무것도 안 하고(`:`는 아무 동작도 안 하는 bash의 no-op 명령), 그 외의 모든 경우는 `PASS`를 0(실패)으로 내린다"는 뜻입니다. 인스턴스 하나라도 FEASIBLE이 아니면 전체가 FAIL이 되는 것입니다.

- [ ] **Step 3: 설치 문서 작성** — `docs/server-sim.md`

```markdown
# 서버 시뮬레이션 (firejail + cpulimit) — WSL2 Ubuntu 24.04

## 설치
    sudo apt-get update
    sudo apt-get install -y firejail cpulimit

## WSL2 특이사항
- firejail은 setuid-root 바이너리라 별도 sysctl 없이 동작하는 것이 기본.
  "cannot create user namespace" 오류가 나면(Ubuntu 24.04의 AppArmor 제한):
      sudo sysctl -w kernel.apparmor_restrict_unprivileged_userns=0
- cpulimit는 SIGSTOP/SIGCONT 방식이라 cgroup/systemd가 필요 없다(WSL2 무관).
- `--rlimit-as=17179869184`(16GB)는 주소공간 기준이라 실제 서버의 RSS 기준
  cgroup보다 보수적이다(먼저 죽으면 우리 쪽이 더 엄격했던 것 — 안전).

## 사용
    bash scripts/server_sim.sh <패키지_디렉토리> <timelimit초> data/*/train/*.json data/stress/*.json
- 패키지 디렉토리 = 제출 zip을 언팩한 형태(myalgorithm.py, utils.py, solver/).
- 통과 기준: 모든 행 FEASIBLE + 마지막 줄 SERVER-SIM PASS (exit 0).
- 재현 항목: 네트워크 차단(--net=none), 메모리 16GB(rlimit-as), CPU 400%(cpulimit),
  cwd=패키지 루트(상대경로만 동작해야 함). 완전 재현이 아닌 항목: 파일시스템
  상위 접근 차단은 근사(--noprofile) — 절대경로 사용은 Task 12 검증기가 별도 차단.
```

> 💡 **설명:**
> - **setuid-root 바이너리**: 실행 파일에 특별한 권한 비트(setuid)가 설정되어 있어서, 어떤 일반 사용자가 실행해도 root(관리자) 권한으로 동작하는 실행파일입니다. firejail이 샌드박스를 만들려면 커널 수준의 격리 기능(네임스페이스 등)에 접근해야 하는데, 일반 사용자 권한으로는 부족하기 때문에 이런 방식으로 설치됩니다.
> - **AppArmor**: 리눅스 커널의 보안 모듈(프로그램별로 접근 가능한 자원을 제한). Ubuntu 24.04부터 "권한 없는 사용자 네임스페이스 생성"을 기본적으로 더 엄격히 제한하기 시작해서, firejail 같은 도구가 이 정책 때문에 막힐 수 있어 `sysctl`로 완화해줘야 하는 경우가 있습니다.
> - **cgroup/systemd가 필요 없다**: cgroup(control group)은 리눅스의 자원 제한 메커니즘이고 systemd는 이를 관리하는 흔한 도구인데, WSL2(Windows Subsystem for Linux 2, 윈도우 안에서 리눅스를 돌리는 가상화 계층) 환경에서는 이런 시스템 서비스들이 완전히 지원되지 않을 수 있습니다. `cpulimit`는 대신 `SIGSTOP`/`SIGCONT`(프로세스를 잠깐 멈췄다 재개했다 반복해서 평균 CPU 사용률을 낮추는 훨씬 단순한 신호 기반 방식)를 쓰기 때문에 이런 제약이 없어 WSL2에서도 잘 동작합니다.

- [ ] **Step 4: 스모크 테스트 작성** — `tests/test_server_sim.py`

```python
import pathlib
import shutil
import subprocess

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]


def _assemble_pkg(dst: pathlib.Path):
    """언팩된 제출물과 동일한 최소 구조를 조립 (Task 12의 빌더와 동일 레이아웃)."""
    dst.mkdir(parents=True, exist_ok=True)
    shutil.copytree(ROOT / "solver", dst / "solver",
                    ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    shutil.copy2(ROOT / "baseline" / "utils.py", dst / "utils.py")
    (dst / "myalgorithm.py").write_text(
        "import os, sys\n"
        "sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))\n"
        "def algorithm(prob_info, timelimit=60):\n"
        "    from solver.shell import solve\n"
        "    return solve(prob_info, timelimit)\n",
        encoding="utf-8")


@pytest.mark.sim
@pytest.mark.skipif(shutil.which("firejail") is None or shutil.which("cpulimit") is None,
                    reason="firejail/cpulimit 미설치 — docs/server-sim.md")
def test_server_sim_smoke(tmp_path):
    pkg = tmp_path / "pkg"
    _assemble_pkg(pkg)
    inst = ROOT / "data" / "training_instances" / "train" / "prob_1.json"
    out = subprocess.run(["bash", str(ROOT / "scripts" / "server_sim.sh"),
                          str(pkg), "30", str(inst)],
                         capture_output=True, text=True)
    assert "SERVER-SIM PASS" in out.stdout, out.stdout + out.stderr
    assert "[prob_1] FEASIBLE" in out.stdout
    assert out.returncode == 0
```

> 💡 **설명 — `@pytest.mark.skipif(...)`**: "이 조건이 참이면 테스트를 건너뛴다(skip)"는 pytest 데코레이터입니다. `shutil.which("firejail")`은 "firejail이라는 실행 파일이 시스템 PATH 어딘가에 설치되어 있는지" 찾아보고, 없으면 `None`을 반환합니다. 즉 이 조건은 "firejail이나 cpulimit이 설치 안 되어 있으면 (에러로 실패시키지 말고) 그냥 건너뛰고 이유를 설명하라"는 뜻입니다 — CI 환경이나 다른 개발자의 컴퓨터에 이 도구들이 없을 수도 있으므로, 없다고 테스트 스위트 전체가 실패로 보이면 안 되기 때문에 이런 우아한 처리를 해두는 것입니다(무설치 = 실패가 아니라 "검증 못 함"이라는 다른 상태로 명확히 구분).

- [ ] **Step 5: 설치·스모크 실행**

Run: `sudo apt-get install -y firejail cpulimit` (최초 1회)
Run: `conda run -n ogc2026 python -m pytest tests/test_server_sim.py -m sim -v`
Expected: 1 passed (`[prob_1] FEASIBLE obj=... SERVER-SIM PASS`). firejail 권한 오류 시 docs/server-sim.md의 sysctl 처방 적용 후 재실행.

- [ ] **Step 6: Commit**

```bash
git add scripts/server_sim.sh scripts/sim_runner.py docs/server-sim.md tests/test_server_sim.py
git commit -m "feat(week4): firejail+cpulimit 서버 시뮬레이션 러너 + WSL2 설치 문서"
```

### Task 10: 파라미터 튜닝 하네스 `experiments/tune.py`

**Files:**
- Create: `experiments/tune.py`
- Test: `tests/test_tune.py`

**Interfaces:**
- Consumes: Task 1 `OGC_CONFIG`/`OGC_SEED` 오버라이드, 전제 B `experiments/run_bench.py` CLI·결과 JSON.
- Produces: CLI `python experiments/tune.py --mode random|grid --trials N --seeds 2 --timelimit 60 --instances all|prob_1,... --out experiments/results/tune/`. 산출물: `trial_<k>_seed<s>.json`(run_bench 원본), `summary.json`, `best_config.json`. 순수 함수 `sample_configs(mode, trials, rng) -> list[dict]`, `score_trial(runs, base_runs) -> float`, `select_winner(summary) -> dict | None` (Task 11과 단위 테스트가 사용). 탐색 공간: `kappa, bias_p, removal_rate, rrt_start, phase_fracs`.

> 💡 **설명 — 이 Task가 하려는 일과, "그냥 아무 설정이나 제일 좋아 보이는 걸 고르면 안 되는 이유":** 지금까지 `SolverConfig`의 기본값(kappa=3.0, bias_p=0.25 등)은 전부 "1~3주차에서 대충 정한 감각적인 값"입니다. 이 Task는 여러 설정 조합을 실제로 실행해보고, 어떤 조합이 40개 인스턴스에서 평균적으로 더 낮은 목적값을 내는지 **체계적으로 탐색**합니다. 두 가지 탐색 전략을 지원합니다:
> - **그리드 서치(grid search)**: 각 파라미터 후보값의 모든 조합을 하나도 빠짐없이 전부 시도(`itertools.product`). 확실하지만 파라미터 수·후보 수가 늘면 조합 수가 기하급수적으로 폭증합니다.
> - **랜덤 서치(random search)**: 정해진 횟수(`trials`)만큼 조합 공간에서 무작위로 몇 개만 뽑아 시도. 전수조사는 아니지만 시간 예산이 한정된 상황에서 훨씬 현실적입니다.
>
> **왜 "시드 2개 이상, 전 시드에서 우세해야 채택"이라는 통계적 검증이 필요한가:** 메타휴리스틱은 난수를 쓰므로, 어떤 설정이 우연히 "운 좋은 시드"를 만나 좋아 보일 수 있습니다. 예를 들어 설정 A가 시드 1번에서는 설정 B(기본값)보다 좋았지만 시드 2번에서는 오히려 나빴다면, 이건 "설정 A가 진짜 더 좋다"는 증거가 아니라 그냥 우연(난수 변동성)일 가능성이 큽니다. 그래서 `select_winner`는 **"여러 시드 전부에서 일관되게 기본값보다 좋아야만"** 승자로 인정합니다(`test_select_winner_requires_both_seeds_better` 테스트가 이 규칙을 검증). 이건 통계학의 A/B 테스트에서 "표본이 하나뿐이면 우연과 진짜 효과를 구분 못 한다"는 것과 같은 원리를 아주 간소화해서(정식 유의성 검정 없이 "전부 우세"라는 보수적 규칙으로) 적용한 것입니다 — 통계 전문 지식 없이도 "우연한 개선"에 속지 않기 위한 실용적인 최소 안전장치입니다.

- [ ] **Step 1: 실패 테스트 작성** — `tests/test_tune.py`

```python
import random


def test_sample_configs_grid_and_random():
    from experiments.tune import sample_configs, SPACE
    grid = sample_configs("grid", trials=0, rng=random.Random(0))
    expect = 1
    for v in SPACE.values():
        expect *= len(v)
    assert len(grid) == expect                       # 전조합
    rnd = sample_configs("random", trials=12, rng=random.Random(0))
    assert len(rnd) == 12
    assert rnd == sample_configs("random", trials=12, rng=random.Random(0))  # 결정적
    for cfg in rnd:
        assert set(cfg) == {"kappa", "bias_p", "removal_rate", "rrt_start",
                            "phase_construct", "phase_alns", "phase_mh"}
        assert abs(cfg["phase_construct"] + cfg["phase_alns"] + cfg["phase_mh"] - 1.0) < 1e-9


def test_score_is_normalized_mean():
    from experiments.tune import score_trial
    runs = [{"instance": "a", "obj": 90.0, "feasible": True},
            {"instance": "b", "obj": 220.0, "feasible": True}]
    base = [{"instance": "a", "obj": 100.0, "feasible": True},
            {"instance": "b", "obj": 200.0, "feasible": True}]
    assert abs(score_trial(runs, base) - (0.9 + 1.1) / 2) < 1e-9


def test_infeasible_trial_disqualified():
    from experiments.tune import score_trial
    runs = [{"instance": "a", "obj": 1.0, "feasible": False}]
    base = [{"instance": "a", "obj": 100.0, "feasible": True}]
    assert score_trial(runs, base) == float("inf")   # infeasible = 실격


def test_select_winner_requires_both_seeds_better():
    from experiments.tune import select_winner
    summary = {
        "default": {"scores": {"1": 1.0, "2": 1.0}},
        "trials": [
            {"config": {"kappa": 4.0}, "scores": {"1": 0.95, "2": 1.02}},  # 한 시드만 우세
            {"config": {"kappa": 2.0}, "scores": {"1": 0.93, "2": 0.97}},  # 둘 다 우세
        ],
    }
    assert select_winner(summary) == {"kappa": 2.0}
    summary["trials"] = summary["trials"][:1]
    assert select_winner(summary) is None            # 전 시드 우세 없으면 기본값 유지
```

> 💡 **설명 — `score_trial`의 "정규화 평균(normalized mean)"이 왜 필요한가:** 40개 인스턴스는 각각 목적값의 스케일이 완전히 다릅니다(어떤 인스턴스는 원래 목적값이 수백, 어떤 인스턴스는 수만일 수 있음). 만약 그냥 "40개 인스턴스의 목적값 합"으로 성능을 비교하면, 스케일이 큰 인스턴스 몇 개가 전체 평가를 지배해버려서 나머지 인스턴스에서의 성능 차이가 묻혀버립니다. 그래서 각 인스턴스에서 **"이 설정의 목적값 / 기본설정의 목적값"이라는 비율(ratio)** 로 정규화한 뒤, 그 비율들의 평균을 점수로 씁니다(`score_trial` 테스트: 90/100=0.9, 220/200=1.1, 평균 1.0). 비율이 1보다 작으면 "기본값보다 좋다", 1보다 크면 "나쁘다"는 뜻이 되어, 인스턴스 스케일에 무관하게 공정한 비교가 가능해집니다. `test_infeasible_trial_disqualified`가 보여주듯, 목적값이 아무리 좋아 보여도(`obj=1.0`) feasible하지 않은 해라면 그 설정은 무조건 `float("inf")`(무한대, 즉 최악)로 실격 처리됩니다 — "품질은 좋은데 실행 불가능한 설정"을 절대 승자로 뽑지 않기 위한 규칙입니다.

- [ ] **Step 2: 실패 확인**

Run: `conda run -n ogc2026 python -m pytest tests/test_tune.py -v`
Expected: FAIL (`experiments.tune` 부재). `experiments/`가 패키지가 아니면 빈 `experiments/__init__.py` 생성.

- [ ] **Step 3: 구현** — `experiments/tune.py`

```python
"""파라미터 튜닝 하네스 — OGC_CONFIG 주입 + run_bench 서브프로세스.

통계적 최소 안전장치: 시드 2개 이상, 정규화 평균 점수(기본설정 대비 obj 비율),
전 시드에서 기본설정보다 좋아야만 승자로 채택.
"""
import argparse
import itertools
import json
import os
import pathlib
import random
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]

SPACE = {
    "kappa": [2.0, 3.0, 4.0],
    "bias_p": [0.15, 0.25, 0.35],
    "removal_rate": [0.20, 0.28, 0.35],
    "rrt_start": [0.02, 0.03, 0.05],
    "phase_fracs": [(0.15, 0.70, 0.15), (0.10, 0.75, 0.15), (0.10, 0.80, 0.10)],
}


def _expand(choice):
    c, a, m = choice.pop("phase_fracs")
    choice.update(phase_construct=c, phase_alns=a, phase_mh=m)
    return choice


def sample_configs(mode, trials, rng):
    keys = list(SPACE)
    if mode == "grid":
        return [_expand(dict(zip(keys, combo)))
                for combo in itertools.product(*(SPACE[k] for k in keys))]
    return [_expand({k: rng.choice(SPACE[k]) for k in keys}) for _ in range(trials)]


def score_trial(runs, base_runs):
    base = {r["instance"]: r["obj"] for r in base_runs}
    ratios = []
    for r in runs:
        if not r["feasible"]:
            return float("inf")                    # infeasible 설정은 실격
        ratios.append(r["obj"] / max(base[r["instance"]], 1e-9))
    return sum(ratios) / len(ratios)


def select_winner(summary):
    default_scores = summary["default"]["scores"]
    best, best_mean = None, sum(default_scores.values()) / len(default_scores)
    for t in summary["trials"]:
        s = t["scores"]
        if set(s) != set(default_scores):
            continue
        if all(s[k] < default_scores[k] for k in s):     # 전 시드 우세 필수
            mean = sum(s.values()) / len(s)
            if mean < best_mean:
                best, best_mean = t["config"], mean
    return best


def run_trial(config, seed, timelimit, instances, out_path, runner=None):
    """config(dict|None=기본값)를 OGC_CONFIG로 주입해 run_bench 실행."""
    env = dict(os.environ, OGC_SEED=str(seed))
    cfg_file = None
    if config is not None:
        cfg_file = out_path.with_suffix(".cfg.json")
        cfg_file.write_text(json.dumps(config), encoding="utf-8")
        env["OGC_CONFIG"] = str(cfg_file)
    cmd = [sys.executable, "experiments/run_bench.py", "--algo", "ours",
           "--timelimit", str(timelimit), "--out", str(out_path)]
    if instances != "all":
        cmd += ["--instances", instances]
    (runner or subprocess.run)(cmd, cwd=ROOT, env=env, check=True)
    return json.loads(out_path.read_text())["runs"]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--mode", choices=["grid", "random"], default="random")
    ap.add_argument("--trials", type=int, default=12)
    ap.add_argument("--seeds", type=int, default=2)
    ap.add_argument("--timelimit", type=int, default=60)
    ap.add_argument("--instances", default="all")
    ap.add_argument("--out", default="experiments/results/tune")
    args = ap.parse_args()
    outdir = pathlib.Path(args.out)
    outdir.mkdir(parents=True, exist_ok=True)
    seeds = [1 + i for i in range(args.seeds)]

    base_runs = {}                                   # 기본설정 기준선 (seed별)
    for s in seeds:
        base_runs[s] = run_trial(None, s, args.timelimit, args.instances,
                                 outdir / f"default_seed{s}.json")
    summary = {"default": {"scores": {str(s): score_trial(base_runs[s], base_runs[s])
                                      for s in seeds}},
               "trials": []}
    for k, cfg in enumerate(sample_configs(args.mode, args.trials, random.Random(2026))):
        scores = {}
        for s in seeds:
            runs = run_trial(cfg, s, args.timelimit, args.instances,
                             outdir / f"trial_{k}_seed{s}.json")
            scores[str(s)] = score_trial(runs, base_runs[s])
        summary["trials"].append({"config": cfg, "scores": scores})
        print(f"trial {k}: {cfg} -> {scores}")
    (outdir / "summary.json").write_text(json.dumps(summary, indent=1), encoding="utf-8")
    winner = select_winner(summary)
    (outdir / "best_config.json").write_text(json.dumps(winner, indent=1), encoding="utf-8")
    print("WINNER:", winner if winner else "기본값 유지 (유의미한 개선 없음)")


if __name__ == "__main__":
    main()
```

> 💡 **설명 — 구현에서 눈여겨볼 부분:**
> - **`run_trial`이 `OGC_CONFIG`로 설정을 "파일을 통해" 주입하는 이유**: `run_trial`은 `experiments/run_bench.py`를 별도 `subprocess`로 실행합니다(Task 5·6에서 본 것과 같은 프로세스 격리 이유 — 매 trial마다 완전히 새 프로세스에서 깨끗하게 시작). 그런데 별도 프로세스에게 "이번엔 kappa=4.0을 써라"라고 직접 함수 인자로 전달할 방법이 없으므로(프로세스 경계를 넘을 수 있는 건 커맨드라인 인자, 환경변수, 파일 등뿐), Task 1에서 만든 `OGC_CONFIG`(JSON 파일 경로) 메커니즘을 그대로 재사용합니다 — 설정을 JSON 파일에 써두고, 그 경로를 환경변수로 알려주는 방식입니다. 이게 "설정 단일 소스"(Task 1)가 튜닝 하네스에서도 재사용되는 실제 사례입니다.
> - **`random.Random(2026)`**: 튜닝 자체가 어떤 설정 조합을 무작위로 뽑을지 정하는 이 난수도, "이번 튜닝 실행을 다시 돌리면 같은 조합을 시도하는지" 재현할 수 있도록 고정 시드(2026, 아마 올해 연도)를 씁니다. `test_sample_configs_grid_and_random`의 "결정적(rnd == sample_configs(...))" 검증과 같은 원칙입니다.
> - **`runner=None` 매개변수와 `(runner or subprocess.run)(...)`**: `run_trial` 함수가 기본적으로는 `subprocess.run`을 쓰지만, 테스트에서는 실제로 몇 시간짜리 벤치마크를 subprocess로 돌릴 수 없으므로 "가짜 실행기(mock runner)"를 주입할 수 있게 만든 것입니다(**의존성 주입, dependency injection** 패턴 — 실제 실행 대신 테스트용 대역을 끼워 넣을 수 있게 설계). 다만 위 `test_tune.py`의 4개 테스트는 이 `run_trial` 자체를 직접 테스트하지 않고 `sample_configs`/`score_trial`/`select_winner`라는 "부수효과 없는 순수 함수"들만 단위 테스트하는데, 이런 함수들을 **순수 함수(pure function)**(입력이 같으면 항상 같은 출력, 외부 상태를 바꾸지 않음)로 분리해둔 덕분에 subprocess 실행 없이도 이 함수들의 핵심 로직(탐색 공간 샘플링, 점수 계산, 승자 선정)을 빠르고 안정적으로 테스트할 수 있는 것입니다.

- [ ] **Step 4: 단위 테스트 통과 확인**

Run: `conda run -n ogc2026 python -m pytest tests/test_tune.py -v`
Expected: 4 passed

- [ ] **Step 5: 3-트라이얼 스모크 (짧은 예산)**

Run: `conda run -n ogc2026 python experiments/tune.py --mode random --trials 2 --seeds 2 --timelimit 20 --instances prob_1,prob_2 --out experiments/results/tune-smoke`
Expected: `default_seed*.json`, `trial_*.json`, `summary.json`, `best_config.json` 생성, `WINNER: ...` 출력, 예외 없음.

- [ ] **Step 6: Commit**

```bash
git add experiments/tune.py experiments/__init__.py tests/test_tune.py
git commit -m "feat(week4): OGC_CONFIG 주입식 튜닝 하네스 (2-시드 통계 검증)"
```

### Task 11: 튜닝 실행 → `solver/config.py` 기본값 확정

**Files:**
- Modify: `solver/config.py` (DEFAULT 필드값), `tests/test_config.py` (확정값 반영)
- Create(실행 산출물): `experiments/results/tune/summary.json`, `best_config.json`

**Interfaces:**
- Consumes: Task 10 `tune.py`, 40개 인스턴스, Task 1 `SolverConfig`.
- Produces: 튜닝 확정 파라미터가 반영된 `DEFAULT` (제출 경로가 사용하는 값 — 4주차 종료 기준 "파라미터 확정").

> 💡 **설명 — Task 10이 "도구를 만드는" 단계였다면, 이 Task는 "그 도구를 실제로 크게 한 번 돌려서 결론을 내는" 단계입니다.** 지금까지는 전부 코드/테스트였지만, 이 Task는 처음으로 "실험을 실제로 수행하고, 그 결과를 보고 제품 코드(`solver/config.py`의 `DEFAULT`)를 사람이 직접 고치는" 실험적·의사결정적 단계입니다.

- [ ] **Step 1: 본 튜닝 실행 (백그라운드, ~16시간)**

Run: `nohup conda run -n ogc2026 python experiments/tune.py --mode random --trials 10 --seeds 2 --timelimit 60 --instances all --out experiments/results/tune > experiments/results/tune.log 2>&1 &`
예상 소요: (10 trials + default) × 2 seeds × 40 인스턴스 × 60s ≈ 15시간. 야간 실행 권장. 진행 확인: `tail -f experiments/results/tune.log` (trial별 점수 라인).

> 💡 **설명 — `nohup ... &`가 뭘 하는 명령인가:** `&`는 리눅스 셸에서 명령을 **백그라운드**로 실행하라는 뜻(터미널을 계속 다른 용도로 쓸 수 있게 즉시 프롬프트를 돌려받음)이고, `nohup`("no hang up")은 "터미널 세션이 끊겨도(SSH 연결이 끊기거나 터미널 창을 닫아도) 이 프로세스를 계속 실행되게 하라"는 뜻입니다. 이 튜닝 실행이 15시간이나 걸리므로, 터미널을 계속 띄워둔 채 기다릴 수 없어 "터미널과 무관하게 백그라운드에서 계속 도는" 이 조합을 씁니다. `> experiments/results/tune.log 2>&1`은 "표준출력(1)과 표준에러(2)를 모두 이 로그 파일로 보내라"는 리다이렉션(2>&1은 "에러 출력을 표준출력이 가는 곳과 같은 곳으로 보내라"는 뜻)이라서, 나중에 `tail -f`(파일에 새로 추가되는 내용을 실시간으로 계속 보여주는 명령)로 진행 상황을 확인할 수 있습니다.

- [ ] **Step 2: 승자 확인·반영**

Run: `cat experiments/results/tune/best_config.json`
- `null`이면: 기본값 유지가 결론 — Step 3으로 건너뛰고 그 사실을 커밋 메시지에 명기.
- 값이 있으면: `solver/config.py`의 `SolverConfig` 필드 기본값을 best_config.json 값으로 **직접 수정**하고, `tests/test_config.py::test_default_config_values`의 기대값도 같은 값으로 갱신. (예: `kappa: float = 2.0` 등 — 실제 값은 튜닝 결과를 따른다.)

> 💡 **설명 — "null이어도 괜찮다"는 것이 왜 중요한 태도인가:** 튜닝 실험을 15시간이나 돌렸는데 결과가 "기본값을 그대로 쓰는 게 낫다"(`best_config.json`이 `null`)로 나올 수도 있습니다. 이건 실패가 아니라 **유효한 실험 결과**입니다 — Task 10에서 설명한 "전 시드에서 우세해야만 채택"이라는 엄격한 기준을 통과하는 설정이 없었다는 뜻이고, 이는 "1~3주차에서 감각적으로 정했던 기본값이 이미 상당히 합리적이었다"는 것을 검증해준 셈입니다. 잘못된 방향으로 "그래도 뭔가 바꿔야 할 것 같다"며 통계적 근거 없이 설정을 바꾸는 것보다, "바꿀 근거가 없으면 바꾸지 않는다"는 원칙을 지키는 것이 더 안전한 태도입니다.

- [ ] **Step 3: 확정값 회귀 확인**

Run: `conda run -n ogc2026 python -m pytest tests/test_config.py tests/test_budget_stress.py -v`
Expected: 전부 PASS (새 기본값으로도 예산 준수).
Run: `conda run -n ogc2026 python experiments/run_bench.py --algo ours --timelimit 60 --instances prob_1,prob_2,prob_21,prob_23 --out experiments/results/post-tune-sanity.json`
Expected: 4개 전부 feasible, obj 합계가 튜닝 전(default_seed1.json의 해당 인스턴스 합계) 이하.

> 💡 **설명 — "확정값 회귀 확인"이 왜 별도 단계로 필요한가:** 튜닝에서 "이 설정이 목적값 기준으로 더 좋다"고 나왔더라도, 그 설정이 **다른 요구사항(시간예산 준수 등)을 깨뜨리지 않는지**는 별개로 확인해야 합니다. 예를 들어 `removal_rate`를 키우면 ALNS 반복 하나하나가 더 무거워져서 전체 반복 횟수가 줄고, 극단적인 경우 시간예산(`test_budget_stress.py`)을 못 지킬 수도 있습니다. "목적값만 보고 좋다고 확정했다가, 알고 보니 시간예산을 어기게 되어 다시 롤백해야 하는" 상황을 막기 위해, 확정 전에 반드시 이미 만들어둔 다른 하드닝 테스트들(Task 4)까지 함께 재확인하는 것입니다 — 소프트웨어에서 "한 지표를 최적화하다가 다른 지표를 깨뜨리지 않았는지 확인한다"는 일반적인 회귀 테스트 원칙입니다.

- [ ] **Step 4: Commit**

```bash
git add solver/config.py tests/test_config.py experiments/results/tune/summary.json experiments/results/tune/best_config.json experiments/results/post-tune-sanity.json
git commit -m "chore(week4): 튜닝 확정 파라미터 반영 (2-시드 전승 기준)"
```

### Task 12: 제출 패키징 자동화 — `scripts/build_submission.py` + 검증기

**Files:**
- Create: `scripts/build_submission.py`
- Test: `tests/test_submission.py`

**Interfaces:**
- Consumes: `submission/myalgorithm.py`(1주차), `solver/`(+`ogc_core*.so`), `baseline/utils.py`, `core/third_party/clipper2/LICENSE*`, `core/third_party/nlohmann/` 라이선스.
- Produces: CLI `python scripts/build_submission.py --out dist/submission.zip [--staging dist/staging]` 및 `--check-only <zip>`. 함수 `build(out_zip: pathlib.Path, staging: pathlib.Path) -> pathlib.Path`, `validate_zip(zip_path: pathlib.Path) -> list[str]` (빈 리스트 = 합격). Task 13 리허설이 이 CLI를 호출. zip 레이아웃: 루트에 `myalgorithm.py`·`utils.py`, `solver/`(패키지+`.so`), `LICENSES/`.

> 💡 **설명 — 1주차에서 예고만 되어 있던 "제출 스테이징"이 이제 실제로 구현되는 곳입니다.** 1주차 `submission/myalgorithm.py`는 "이 파일이 `solver.shell.solve`를 위임 호출하는 진입점"이라고만 만들어두고, "실제로 zip을 어떻게 조립할지"는 "4주차 패키징 과제에서 자동화"라고 미뤄뒀었습니다(1주차 주석본 Task 13 참고). 이 Task가 그 약속을 지키는 곳입니다. 핵심 아이디어는 두 가지 별도 함수로 나뉩니다:
> - **`build()`**: 여기저기 흩어진 소스 파일(`myalgorithm.py`, `baseline/utils.py`, `solver/` 패키지, 라이선스 파일)을 `dist/staging`이라는 임시 디렉토리에 "제출 zip과 똑같은 모양"으로 모아 복사한 뒤, zip으로 압축합니다. **스테이징(staging)**은 "최종 배포 전에 임시로 준비·정렬해두는 중간 단계 디렉토리"를 가리키는 일반적인 배포 용어입니다.
> - **`validate_zip()`**: 만들어진 zip이 정말 대회 규정(15MB, 상대경로, 금지 확장자, utils.py 무수정 등)을 지키는지 자동으로 검사해서, 위반 사항을 문자열 리스트로 반환합니다(빈 리스트 = 문제 없음). 사람이 매번 눈으로 규정을 확인하는 대신, 코드가 기계적으로 확인하게 만든 것입니다 — "체크리스트를 자동화한다"는 하드닝의 전형적인 방식입니다.

- [ ] **Step 1: 실패 테스트 작성** — `tests/test_submission.py`

```python
import hashlib
import pathlib
import shutil
import subprocess
import sys
import zipfile

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module")
def built_zip(tmp_path_factory):
    d = tmp_path_factory.mktemp("dist")
    subprocess.run([sys.executable, "scripts/build_submission.py",
                    "--out", str(d / "submission.zip"), "--staging", str(d / "staging")],
                   cwd=ROOT, check=True)
    return d / "submission.zip"


def _validate(zip_path):
    sys.path.insert(0, str(ROOT / "scripts"))
    from build_submission import validate_zip
    return validate_zip(pathlib.Path(zip_path))


def test_build_passes_validator(built_zip):
    assert _validate(built_zip) == []


def test_zip_layout(built_zip):
    names = zipfile.ZipFile(built_zip).namelist()
    assert "myalgorithm.py" in names               # zip 루트 (하위 폴더 금지)
    assert "utils.py" in names
    assert any(n.startswith("solver/") and n.endswith(".py") for n in names)
    assert any(n.startswith("LICENSES/") for n in names)
    assert built_zip.stat().st_size <= 15 * 1024 * 1024


def test_utils_is_unmodified_copy(built_zip):
    packed = zipfile.ZipFile(built_zip).read("utils.py")
    orig = (ROOT / "baseline" / "utils.py").read_bytes()
    assert hashlib.sha256(packed).hexdigest() == hashlib.sha256(orig).hexdigest()


def _tamper(src_zip, tmp_path, mutate):
    dst = tmp_path / "tampered.zip"
    with zipfile.ZipFile(src_zip) as zin, zipfile.ZipFile(dst, "w") as zout:
        for item in zin.infolist():
            zout.writestr(item, zin.read(item.filename))
        mutate(zout)
    return dst


def test_validator_catches_modified_utils(built_zip, tmp_path):
    dst = tmp_path / "t1.zip"
    with zipfile.ZipFile(built_zip) as zin, zipfile.ZipFile(dst, "w") as zout:
        for item in zin.infolist():
            data = zin.read(item.filename)
            if item.filename == "utils.py":
                data += b"\n# tampered\n"
            zout.writestr(item, data)
    assert any("utils.py" in v for v in _validate(dst))


def test_validator_catches_forbidden_ext(built_zip, tmp_path):
    dst = _tamper(built_zip, tmp_path, lambda z: z.writestr("evil.exe", b"MZ"))
    assert any(".exe" in v for v in _validate(dst))


def test_validator_catches_absolute_path_literal(built_zip, tmp_path):
    dst = _tamper(built_zip, tmp_path,
                  lambda z: z.writestr("solver/bad.py", 'P = "/home/brown/x"\n'))
    assert any("절대경로" in v for v in _validate(dst))


def test_unpacked_zip_solves_prob1(built_zip, tmp_path):
    """언팩 → 깨끗한 서브프로세스에서 import·미니 solve — .so 상대 로드 검증."""
    with zipfile.ZipFile(built_zip) as z:
        z.extractall(tmp_path)
    inst = ROOT / "data" / "training_instances" / "train" / "prob_1.json"
    code = (
        "import json, inspect;"
        "from myalgorithm import algorithm;"
        "assert list(inspect.signature(algorithm).parameters) == ['prob_info', 'timelimit'];"
        f"prob = json.load(open({str(inst)!r}));"
        "sol = algorithm(prob, 20);"
        "import utils;"
        "assert utils.check_feasibility(prob, sol)['feasible'] is True;"
        "print('SUBMISSION-OK')"
    )
    out = subprocess.run([sys.executable, "-c", code], cwd=tmp_path,
                         env={"PATH": "/usr/bin:/bin", "PYTHONNOUSERSITE": "1"},
                         capture_output=True, text=True)
    assert out.returncode == 0 and "SUBMISSION-OK" in out.stdout, out.stderr
```

> 💡 **설명 — "변조(tamper) 테스트"라는 검증 스타일:** `_tamper` 헬퍼와 `test_validator_catches_*` 세 테스트들은 "정상적으로 빌드된 zip을 일부러 망가뜨린 뒤, 검증기가 그 망가진 부분을 제대로 잡아내는지" 확인합니다. 이건 Task 3의 "test_different_seed_differs"와 같은 철학입니다 — **검증기가 진짜로 동작하는지 확인하려면, "정상 케이스가 통과하는지"뿐 아니라 "비정상 케이스가 실제로 걸러지는지"도 확인해야 검증기 자체를 신뢰할 수 있습니다.** 만약 `validate_zip`이 버그로 인해 뭘 검사하든 항상 빈 리스트(`[]`, 합격)를 반환한다면, `test_build_passes_validator`(정상 케이스)만으로는 그 버그를 절대 발견할 수 없습니다. 일부러 `.exe` 파일을 심어보고, `utils.py`를 변조해보고, 코드에 절대경로 문자열을 심어보면서 "검증기가 이걸 실제로 잡아내는가"를 확인해야 비로소 검증기가 제 역할을 한다고 믿을 수 있습니다.
>
> **`test_unpacked_zip_solves_prob1`이 가장 강력한 검증인 이유**: 이 테스트는 "zip을 실제로 풀어서, 그 디렉토리를 작업 디렉토리로 삼아, 완전히 새로운 파이썬 프로세스에서, `myalgorithm.algorithm`을 실제로 호출해 인스턴스 하나를 풀어보는" — 지금까지의 모든 검증 중 **실제 서버 실행 조건에 가장 가까운** 테스트입니다. 특히 `.so` 파일(C++로 컴파일된 `ogc_core`)이 "zip 안에 상대경로로 잘 들어있고, 그 위치에서 정상적으로 import되는지"는 이렇게 실제로 언팩해서 실행해보지 않으면 확인할 방법이 없습니다(우리 개발 환경에서는 `.so`가 이미 `solver/`에 설치되어 있으니 이 문제가 절대 드러나지 않습니다). `inspect.signature(algorithm).parameters`로 함수의 매개변수 이름까지 확인하는 것은, Global Constraints의 "`myalgorithm.algorithm(prob_info, timelimit=60)` 시그니처 변경 금지" 규정을 코드로 강제하는 것입니다.

- [ ] **Step 2: 실패 확인**

Run: `conda run -n ogc2026 python -m pytest tests/test_submission.py -v`
Expected: FAIL (`build_submission.py` 부재)

- [ ] **Step 3: 구현** — `scripts/build_submission.py`

```python
"""제출 zip 조립 + 검증. 규정 출처: 문제분석 §5.1 (zip 루트 myalgorithm.py,
utils.py 무수정, <=15MB, 악성 오인 확장자 금지, 상대경로만)."""
import argparse
import ast
import hashlib
import pathlib
import re
import shutil
import subprocess
import sys
import zipfile

ROOT = pathlib.Path(__file__).resolve().parents[1]
MAX_SIZE = 15 * 1024 * 1024
FORBIDDEN_EXT = {".dll", ".exe", ".vb", ".vbs", ".bat", ".cmd", ".msi",
                 ".scr", ".com", ".pif", ".jar", ".js"}
ABS_PATH_RE = re.compile(r"""["'](/home/|/mnt/|/usr/|/opt/|[A-Za-z]:\\)""")

MYALGO_SRC = '''import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))


def algorithm(prob_info, timelimit=60):
    from solver.shell import solve
    return solve(prob_info, timelimit)
'''


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def build(out_zip: pathlib.Path, staging: pathlib.Path) -> pathlib.Path:
    if staging.exists():
        shutil.rmtree(staging)
    staging.mkdir(parents=True)
    # 1) myalgorithm.py (루트, 자기 위치를 sys.path에 추가 — 절대경로 리터럴 없음)
    (staging / "myalgorithm.py").write_text(MYALGO_SRC, encoding="utf-8")
    # 2) utils.py — baseline 원본 무수정 복사
    shutil.copy2(ROOT / "baseline" / "utils.py", staging / "utils.py")
    assert _sha256((staging / "utils.py").read_bytes()) == \
        _sha256((ROOT / "baseline" / "utils.py").read_bytes())
    # 3) solver 패키지 (+ ogc_core*.so, 캐시 제외)
    shutil.copytree(ROOT / "solver", staging / "solver",
                    ignore=shutil.ignore_patterns("__pycache__", "*.pyc", "tests"))
    # 4) vendored 라이선스
    lic = staging / "LICENSES"
    lic.mkdir()
    clipper = list((ROOT / "core" / "third_party" / "clipper2").glob("LICENSE*"))
    assert clipper, "Clipper2 라이선스 파일 없음 — vendoring 확인"
    shutil.copy2(clipper[0], lic / "Clipper2-BSL-1.0.txt")
    nlohmann = list((ROOT / "core" / "third_party" / "nlohmann").glob("LICENSE*"))
    if nlohmann:
        shutil.copy2(nlohmann[0], lic / "nlohmann-json-MIT.txt")
    else:  # json.hpp 단일 헤더 배포본은 라이선스가 헤더 주석에 포함됨 — 발췌 저장
        (lic / "nlohmann-json-MIT.txt").write_text(
            "nlohmann/json - MIT License. Full text embedded in "
            "solver-vendored json.hpp header (see core/third_party/nlohmann).\n",
            encoding="utf-8")
    # 5) 컴파일 검사 후 zip (루트 = staging 내용물, 최상위 폴더 없음)
    subprocess.run([sys.executable, "-m", "compileall", "-q", str(staging)], check=True)
    for pyc in staging.rglob("__pycache__"):
        shutil.rmtree(pyc)
    out_zip.parent.mkdir(parents=True, exist_ok=True)
    if out_zip.exists():
        out_zip.unlink()
    with zipfile.ZipFile(out_zip, "w", zipfile.ZIP_DEFLATED) as z:
        for p in sorted(staging.rglob("*")):
            if p.is_file():
                z.write(p, p.relative_to(staging))
    return out_zip


def validate_zip(zip_path: pathlib.Path) -> list[str]:
    v = []
    if zip_path.stat().st_size > MAX_SIZE:
        v.append(f"크기 초과: {zip_path.stat().st_size} > {MAX_SIZE}")
    with zipfile.ZipFile(zip_path) as z:
        names = z.namelist()
        if "myalgorithm.py" not in names:
            v.append("zip 루트에 myalgorithm.py 없음")
        if "utils.py" not in names:
            v.append("utils.py 미동봉")
        elif _sha256(z.read("utils.py")) != \
                _sha256((ROOT / "baseline" / "utils.py").read_bytes()):
            v.append("utils.py가 baseline 원본과 다름 (수정 금지 위반)")
        for n in names:
            ext = pathlib.Path(n).suffix.lower()
            if ext in FORBIDDEN_EXT:
                v.append(f"금지 확장자: {n} ({ext})")
            if n.endswith(".py"):
                src = z.read(n).decode("utf-8", errors="replace")
                if ABS_PATH_RE.search(src):
                    v.append(f"절대경로 리터럴 발견: {n}")
        if "myalgorithm.py" in names:
            tree = ast.parse(z.read("myalgorithm.py"))
            fns = {f.name: f for f in ast.walk(tree) if isinstance(f, ast.FunctionDef)}
            if "algorithm" not in fns:
                v.append("myalgorithm.algorithm 함수 없음")
            else:
                args = [a.arg for a in fns["algorithm"].args.args]
                if args != ["prob_info", "timelimit"]:
                    v.append(f"algorithm 시그니처 불일치: {args}")
        if not any(n.startswith("solver/") and n.endswith(".so") for n in names):
            v.append("경고아님-치명: ogc_core .so 미포함 (make core 먼저 실행)")
    return v


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="dist/submission.zip")
    ap.add_argument("--staging", default="dist/staging")
    ap.add_argument("--check-only", default=None, metavar="ZIP")
    args = ap.parse_args()
    if args.check_only:
        problems = validate_zip(pathlib.Path(args.check_only))
    else:
        z = build(pathlib.Path(args.out), pathlib.Path(args.staging))
        problems = validate_zip(z)
        print(f"built: {z} ({z.stat().st_size / 1024 / 1024:.1f} MB)")
    for p in problems:
        print("VIOLATION:", p)
    sys.exit(1 if problems else 0)


if __name__ == "__main__":
    main()
```

`dist/`를 `.gitignore`에 추가.

> 💡 **설명 — 구현의 여러 트릭들:**
> - **`ABS_PATH_RE = re.compile(r"""["'](/home/|/mnt/|/usr/|/opt/|[A-Za-z]:\\)""")`**: 정규표현식(regular expression)으로 "따옴표 뒤에 `/home/`, `/mnt/`, `/usr/`, `/opt/`로 시작하거나(리눅스 절대경로), `C:\`처럼 드라이브 문자로 시작하는(윈도우 절대경로) 문자열"을 찾습니다. 소스코드 안에 이런 패턴이 있으면 "이 개발자의 로컬 컴퓨터 경로가 하드코딩되어 있다"는 강한 신호이고, 서버에서는 당연히 그런 경로가 존재하지 않으므로 실행 시 실패할 것입니다. 완벽한 검사는 아니지만(코드가 아닌 문자열 데이터에 우연히 이런 패턴이 있을 수도 있음), 실수를 조기에 잡아내는 실용적인 필터입니다.
> - **`ast.parse(...)` + `ast.walk(tree)`로 함수 시그니처 확인**: 코드를 **실행하지 않고** `myalgorithm.py`의 소스코드를 구문 트리로만 분석해서, `algorithm`이라는 이름의 함수가 정말 존재하고 매개변수가 정확히 `["prob_info", "timelimit"]`인지 확인합니다. 왜 "실행"이 아니라 "정적 분석"으로 하는가 하면, 이 시점에는 아직 `.so`나 다른 의존성이 온전한 환경인지 모르는 상태에서 함수 시그니처만 빠르고 안전하게 확인하고 싶기 때문입니다(import해서 실행하면 어떤 부작용이 있을지 모름). `ast.FunctionDef`는 "def로 시작하는 함수 정의"를 나타내는 구문 트리 노드 타입입니다.
> - **`compileall` 호출**: 파이썬 표준 라이브러리 `compileall` 모듈로 staging 디렉토리의 모든 `.py` 파일을 바이트코드로 컴파일해봅니다 — 이건 "문법 오류나 명백한 구문 문제가 없는지"를 최종 zip 생성 직전에 한 번 더 확인하는 빠른 스모크 체크입니다(단, 이 컴파일 산출물인 `__pycache__`는 zip에 넣을 필요가 없으므로 바로 뒤에서 지웁니다).
> - **"경고아님-치명" 문구**: `validate_zip`이 반환하는 위반 메시지 중 하나에 일부러 "경고아님-치명"이라는 접두어를 붙인 이유는, 이 검증기를 쓰는 사람이 목록을 훑어볼 때 "이건 그냥 사소한 경고인가, 아니면 반드시 고쳐야 하는 심각한 문제인가"를 즉시 구분할 수 있게 하기 위해서입니다(`.so`가 없으면 코어가 아예 안 돌아가므로, 이건 목록에서 가장 심각한 축에 속합니다).

- [ ] **Step 4: 통과 확인**

Run: `conda run -n ogc2026 python -m pytest tests/test_submission.py -v`
Expected: 8 passed (특히 `test_unpacked_zip_solves_prob1`이 `.so` 상대 로드까지 검증)
Run: `conda run -n ogc2026 python scripts/build_submission.py --out dist/submission.zip`
Expected: `built: dist/submission.zip (x.x MB)` + VIOLATION 없음 + exit 0

- [ ] **Step 5: Commit**

```bash
git add scripts/build_submission.py tests/test_submission.py .gitignore
git commit -m "feat(week4): 제출 zip 빌더 + 규정 검증기 (15MB/확장자/utils해시/절대경로/시그니처)"
```

### Task 13: 제출 리허설 게이트 — `scripts/rehearsal.sh` + 제출 체크리스트

**Files:**
- Create: `scripts/rehearsal.sh`, `docs/submission-checklist.md`
- Test: 수동 실행 (`--quick` 스모크는 Step 3)

**Interfaces:**
- Consumes: Task 12 `build_submission.py`, Task 9 `server_sim.sh`, Task 7 `data/stress/*.json`.
- Produces: `bash scripts/rehearsal.sh [--quick]` — 빌드→검증→언팩→server_sim(40개+스트레스 9개)→`REHEARSAL GO`/`NO-GO`. **GO 없이는 제출 금지** (12시간 쿨다운 보호, 설계서 §8). Task 15 종료 기준이 이 게이트를 포함.

> 💡 **설명 — 이 Task는 지금까지 만든 3개의 큰 도구(Task 9 서버 시뮬레이션, Task 12 제출 빌더, Task 7 스트레스 인스턴스)를 하나의 파이프라인으로 엮는 "최종 게이트"입니다.** "게이트(gate)"라는 표현은 소프트웨어 배포에서 흔히 쓰는 말로, "이 관문을 통과하지 못하면 다음 단계(여기서는 실제 이메일 제출)로 진행할 수 없다"는 강제적인 체크포인트를 뜻합니다. 흐름은 다음과 같습니다: **① `build_submission.py`로 zip 빌드+자동 검증(Task 12) → ② 그 zip을 실제로 압축 해제(unzip) → ③ 언팩된 디렉토리를 `server_sim.sh`(Task 9)에 넘겨 firejail+cpulimit 환경에서 40개 학습 인스턴스 + 9개 스트레스 인스턴스(Task 7) 전부를 실행 → ④ 전부 FEASIBLE이면 `REHEARSAL GO`.** "GO가 아니면 절대 제출하지 않는다"는 규칙은, Global Constraints에서 설명한 "12시간 쿨다운"의 낭비를 막기 위한 최후의 안전장치입니다 — 이 리허설 하나만 통과하면 실제 제출에서 나쁜 상태(exception/TLE/crash/infeasible)가 나올 확률을 최대한 낮췄다고 believe할 수 있는 것입니다.

- [ ] **Step 1: 스크립트 작성** — `scripts/rehearsal.sh`

```bash
#!/usr/bin/env bash
# 제출 전 최종 게이트: zip 빌드 -> 규정 검증 -> 언팩 -> 서버 시뮬레이션.
# GO가 아니면 절대 제출하지 않는다 (승인 후 12시간 쿨다운 보호).
set -euo pipefail
cd "$(dirname "$0")/.."
PY="conda run -n ogc2026 python"

$PY scripts/build_submission.py --out dist/submission.zip --staging dist/staging \
  || { echo "REHEARSAL NO-GO (zip 규정 위반)"; exit 1; }

TMP=$(mktemp -d)
trap 'rm -rf "$TMP"' EXIT
unzip -q dist/submission.zip -d "$TMP"

if [ "${1:-}" = "--quick" ]; then
  TL=30
  INST="data/training_instances/train/prob_1.json data/stress/stress_singlebay.json"
else
  TL=60
  INST=$(ls data/training_instances/train/*.json data/train-set2/train/*.json \
            data/stress/*.json)
fi

if bash scripts/server_sim.sh "$TMP" "$TL" $INST; then
  echo "REHEARSAL GO — dist/submission.zip 제출 가능"
else
  echo "REHEARSAL NO-GO — 위 FAIL 인스턴스 원인 해결 전 제출 금지"
  exit 1
fi
```

`chmod +x scripts/rehearsal.sh` 실행.

> 💡 **설명:**
> - **`set -euo pipefail`**: bash 스크립트 맨 앞에 붙이는 관용적인 "엄격 모드" 선언입니다. `-e`는 "어떤 명령이든 실패(0이 아닌 종료코드)하면 즉시 스크립트를 중단하라", `-u`는 "정의되지 않은 변수를 참조하면 에러로 취급하라"(오타로 빈 변수를 참조하는 실수 방지), `-o pipefail`은 "파이프(`|`)로 연결된 명령들 중 하나라도 실패하면 전체 파이프라인을 실패로 간주하라"(기본값은 마지막 명령의 종료코드만 봄)는 뜻입니다. 이 3가지를 합치면 "뭔가 잘못되면 조용히 넘어가지 말고 즉시 멈춰서 알린다"는 방어적인 스크립트 작성 관례가 됩니다 — 하드닝 정신이 셸 스크립트에도 그대로 적용된 것입니다.
> - **`trap 'rm -rf "$TMP"' EXIT`**: bash의 `trap`은 "특정 신호(signal)나 이벤트가 발생하면 지정한 명령을 실행하라"는 예약 기능입니다. `EXIT`는 "이 스크립트가 어떤 이유로 끝나든(정상 종료든, 에러로 중단되든)" 발생하는 이벤트이므로, 이 줄은 "스크립트가 어떻게 끝나든 마지막에 반드시 임시 디렉토리(`$TMP`)를 정리한다"는 뜻입니다 — 프로그래밍 언어의 `try/finally`(무슨 일이 있어도 뒷정리는 실행)와 정확히 같은 역할을 셸 스크립트에서 하는 것입니다.
> - **`--quick` 모드**: 매번 40+9=49개 인스턴스를 전부 도는 "풀 리허설"은 약 50분이 걸립니다(아래 Step 4). 코드를 조금 고칠 때마다 매번 50분을 기다릴 수는 없으므로, "대표적인 인스턴스 2개(`prob_1`, `stress_singlebay`)만 30초씩 빠르게 확인"하는 `--quick` 모드를 별도로 둡니다 — 개발 중에는 빠른 스모크로 감을 잡고, 실제 제출 직전에만 무거운 풀 리허설을 도는 실용적인 절충입니다.

- [ ] **Step 2: 체크리스트 문서 작성** — `docs/submission-checklist.md` (문제분석 §5.1·§5.2 원문 기준)

```markdown
# 제출 체크리스트 (매 제출마다 위에서 아래로)

## 자동 게이트 (스크립트가 강제)
- [ ] `bash scripts/rehearsal.sh` → **REHEARSAL GO** (풀셋: 40개 + 스트레스 9개)
- [ ] zip 루트에 `myalgorithm.py` (하위 폴더 금지) — validate_zip
- [ ] `utils.py` 무수정 (baseline sha256 일치) — validate_zip
- [ ] zip ≤ 15MB — validate_zip
- [ ] `.dll/.vb/.exe/.bat/...` 금지 확장자 없음 — validate_zip
- [ ] 절대경로 리터럴 없음 (상대경로만) — validate_zip
- [ ] `algorithm(prob_info, timelimit)` 시그니처 원형 — validate_zip
- [ ] 언팩 후 깨끗한 프로세스에서 import + prob_1 미니 solve 성공 — test_submission

## 수동 확인 (사람이 직접)
- [ ] 발신 계정 = 대회 등록 이메일
- [ ] 수신: submission@optichallenge.com, 첨부는 zip **1개만**
- [ ] 직전 제출 "승인" 후 12시간 경과했는가 (거절이었다면 즉시 가능)
- [ ] 튜닝/코드 변경이 리허설 이후 없었는가 (있었으면 리허설 재실행)

## 평가 상태 6종 대응표
| 서버 상태 | 로컬 게이트 |
|---|---|
| Infeasible | rehearsal의 utils.check_feasibility 전건 통과 |
| Time limit exceeded | test_budget_stress (30/60/300/1800s) + sim의 elapsed<=tl |
| raised an exception | test_shell_hardening (무예외) + sim EXCEPTION=0 |
| terminated unexpectedly | firejail rlimit 16GB 하 크래시 0 (server_sim) |
| unavailable package | 언팩 클린 import 검사 (test_submission) |
```

> 💡 **설명 — 이 체크리스트가 이 문서 전체의 "요약본"인 이유:** 잘 보면 "자동 게이트" 절의 각 항목은 지금까지 만든 Task들(Task 2/7/9/12)의 산출물을 한 줄씩 다시 가리키고 있고, "평가 상태 6종 대응표"는 이 계획 맨 앞(Global Constraints)에서 소개했던 6가지 평가 상태 각각에 대해 "그 나쁜 상태를 막기 위해 우리가 만든 로컬 테스트가 무엇인가"를 1:1로 대응시킨 표입니다. 즉 이 문서는 "① 나쁜 결과가 나올 수 있는 6가지 경로를 파악하고 → ② 각 경로를 막는 코드/테스트를 하나씩 만들고 → ③ 마지막에 이 표로 빠짐없이 다 막았는지 스스로 대조한다"는 구조로 짜여 있습니다. 이런 "리스크 목록 → 대응 매핑 표"는 소프트웨어 품질 보증(QA)에서 표준적으로 쓰이는 **트레이서빌리티 매트릭스(traceability matrix)**와 같은 발상입니다(요구사항 하나하나가 어떤 테스트로 커버되는지 빠짐없이 추적하는 표).

- [ ] **Step 3: 스모크 실행**

Run: `bash scripts/rehearsal.sh --quick`
Expected: `[prob_1] FEASIBLE ...`, `[stress_singlebay] FEASIBLE ...`, `SERVER-SIM PASS`, `REHEARSAL GO` (총 ~1분 + 빌드)

- [ ] **Step 4: 풀 리허설 실행 (수동, 1회)**

Run: `bash scripts/rehearsal.sh`
Expected: 49개 전 행 FEASIBLE → `REHEARSAL GO`. 소요 ~50분(49×60s). NO-GO면 해당 인스턴스를 `bash scripts/server_sim.sh dist/staging 60 <그 인스턴스>`로 단독 재현해 원인 수정 후 재실행 — GO 전 진행 금지.

- [ ] **Step 5: Commit**

```bash
git add scripts/rehearsal.sh docs/submission-checklist.md
git commit -m "feat(week4): 제출 리허설 게이트 (빌드-검증-언팩-서버심 일괄) + 체크리스트"
```

### Task 14: 기술보고서 데이터 수집 — `experiments/report_data.py`

**Files:**
- Create: `experiments/report_data.py`, `docs/report-assets/` 산출물(md/csv)
- Test: `tests/test_report_data.py`

**Interfaces:**
- Consumes: 전제 B `run_bench.py --disable` 토큰, Task 2 `OGC_TRACE_FILE` 훅, 40개 인스턴스 JSON.
- Produces: CLI `python experiments/report_data.py {stats|ablation|convergence|all} [--timelimit 60] [--instances ...]`. 산출물: `docs/report-assets/instance-stats.md`, `ablation-operators.md`, `ablation-stages.md`, `convergence-prob1.csv`, `convergence-prob23.csv`, `convergence.md`. 순수 함수 `instance_stats_row(name, prob) -> dict`, `render_ablation_md(base_runs, disabled_map) -> str`, `milestones(trace_lines, marks) -> list[tuple]` (단위 테스트 대상).

> 💡 **설명 — 지금까지의 모든 Task는 "더 견고하게 만들기"였다면, 이 Task는 유일하게 "설명 자료를 모으기" 위한 것입니다.** 대회는 보통 최종 기술보고서(technical report) 제출을 요구하는데, 그 보고서에 "우리 알고리즘이 왜 이렇게 설계됐고, 각 구성요소가 실제로 얼마나 기여하는가"를 뒷받침할 **정량적 근거**가 필요합니다. 이 Task는 그 근거 자료 세 종류를 자동으로 뽑아내는 스크립트를 만듭니다:
> - **인스턴스 통계(instance stats)**: 40개 인스턴스가 각각 얼마나 크고(n, m), 복잡하고(레이어 수), 촉박한지(zero_slack_pct, density) 요약한 표. "우리 문제가 이런 성격이라 이런 설계를 했다"는 근거.
> - **Ablation(제거 실험, 절제 실험)**: 아래에서 자세히 설명.
> - **수렴곡선(convergence curve)**: 아래에서 자세히 설명.

- [ ] **Step 1: 실패 테스트 작성** — `tests/test_report_data.py`

```python
def test_instance_stats_row(prob1):
    from experiments.report_data import instance_stats_row
    row = instance_stats_row("prob_1", prob1)
    assert row["instance"] == "prob_1"
    assert row["n"] == 100 and row["m"] == 2
    assert row["max_layers"] >= 1 and row["horizon"] > 0
    assert 0.0 < row["density"] < 2.0


def test_render_ablation_md():
    from experiments.report_data import render_ablation_md
    base = [{"instance": "a", "obj": 100.0, "feasible": True},
            {"instance": "b", "obj": 200.0, "feasible": True}]
    disabled = {"shaw": [{"instance": "a", "obj": 110.0, "feasible": True},
                         {"instance": "b", "obj": 220.0, "feasible": True}]}
    md = render_ablation_md(base, disabled)
    assert "| shaw |" in md and "+10.0%" in md   # off 시 10% 악화 = 연산자 기여 10%


def test_milestones_from_trace():
    from experiments.report_data import milestones
    lines = ["1.0,1000.0", "5.0,900.0", "20.0,850.0", "100.0,800.0"]
    got = milestones(lines, marks=[10, 60, 120])
    assert got == [(10, 900.0), (60, 850.0), (120, 800.0)]  # 각 시점의 best-so-far
```

> 💡 **설명 — "Ablation(절제/제거 실험)"이란 정확히 무엇인가:** ablation은 원래 의학·생물학에서 "조직을 절제해서 그 기능을 알아본다"는 뜻의 용어인데, 머신러닝/알고리즘 연구에서는 "시스템의 특정 구성요소 하나를 빼고(꺼서) 돌려보고, 전체 성능이 얼마나 나빠지는지로 그 구성요소의 기여도를 측정하는" 실험 기법을 가리킵니다. 예를 들어 ALNS에 6가지 파괴 연산자(`random, worst, shaw, ...`)가 있다면, "shaw 연산자만 꺼서 돌려봤더니 목적값이 10% 나빠졌다"는 결과는 "shaw 연산자가 전체 성능의 약 10%를 기여하고 있다"고 해석할 수 있습니다. `test_render_ablation_md`에서 `"+10.0%"`을 검증하는 게 정확히 이 계산입니다(off 했을 때 obj 합계가 base 대비 몇 % 늘었는지). 이건 전제 B에서 봤던 `run_bench.py --disable TOKEN` 옵션이 정확히 이 목적을 위해 존재했던 것임을 이제 알 수 있습니다 — "이 연산자/단계를 강제로 끄고 돌려볼 수 있게" 미리 만들어둔 스위치였던 것입니다.
>
> **수렴곡선(convergence curve)과 `milestones` 함수**: "시간이 흐름에 따라 목적값(해의 품질)이 어떻게 개선되는가"를 그래프로 나타낸 것이 수렴곡선입니다. Task 2에서 만든 `OGC_TRACE_FILE`(incumbent가 갱신될 때마다 "경과초,목적값" 한 줄을 기록)이 정확히 이 원재료를 만들기 위한 장치였습니다. `milestones(trace_lines, marks)` 함수는 이 원시 기록에서 "10초 시점엔 최선의 값이 얼마였나, 60초 시점엔?, 120초 시점엔?"처럼 **정해진 몇몇 시점의 스냅샷**만 뽑아내는 요약 함수입니다. 테스트를 보면 `marks=[10, 60, 120]`에 대해 각 시점 **이전까지 기록된 값들 중 최솟값**(`min(upto)`, 목적값은 낮을수록 좋으므로)을 그 시점의 "best-so-far"(그 시점까지의 최선)로 삼습니다 — 이게 "지금까지 발견된 것 중 가장 좋은 값"이라는 개념을 코드로 옮긴 것으로, ALNS 논문 등에서 흔히 쓰는 수렴곡선의 표준적인 정의입니다.

- [ ] **Step 2: 실패 확인**

Run: `conda run -n ogc2026 python -m pytest tests/test_report_data.py -v`
Expected: FAIL (`experiments.report_data` 부재)

- [ ] **Step 3: 구현** — `experiments/report_data.py`

```python
"""기술보고서 재료 집계: 인스턴스 통계 / 연산자·스테이지 ablation / 수렴곡선."""
import argparse
import json
import os
import pathlib
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
ASSETS = ROOT / "docs" / "report-assets"
OPERATORS = ["random", "worst", "shaw", "time_slice", "spatial_column", "blocking_set"]
STAGES = ["mh_retime", "mh_pool", "z2z3"]
DEFAULT_INSTANCES = "prob_1,prob_2,prob_3,prob_4,prob_21,prob_22,prob_23,prob_25"


def instance_stats_row(name, prob):
    blocks = prob["blocks"]
    bay_area = sum(b["width"] * b["height"] for b in prob["bays"])
    horizon = max(b["due_date"] for b in blocks)

    def bbox_area(b):
        ls = b["shape"][0]["layers"]
        xs = [x for l in ls for x, _ in l]
        ys = [y for l in ls for _, y in l]
        return (max(xs) - min(xs)) * (max(ys) - min(ys))
    demand = sum(bbox_area(b) * b["processing_time"] for b in blocks)
    return {
        "instance": name, "n": len(blocks), "m": len(prob["bays"]),
        "max_layers": max(len(s["layers"]) for b in blocks for s in b["shape"]),
        "mean_orients": round(sum(len(b["shape"]) for b in blocks) / len(blocks), 1),
        "horizon": horizon,
        "zero_slack_pct": round(100 * sum(
            1 for b in blocks
            if b["due_date"] - b["release_time"] - b["processing_time"] == 0
        ) / len(blocks)),
        "density": round(demand / (bay_area * max(horizon, 1)), 2),
    }


def render_ablation_md(base_runs, disabled_map):
    base = {r["instance"]: r["obj"] for r in base_runs if r["feasible"]}
    base_sum = sum(base.values())
    lines = ["| off 구성요소 | obj 합계 | 전체 대비 | 해석 |", "|---|---|---|---|",
             f"| (none, full) | {base_sum:.0f} | +0.0% | 기준 |"]
    for name, runs in sorted(disabled_map.items()):
        s = sum(r["obj"] for r in runs if r["feasible"])
        infeas = sum(1 for r in runs if not r["feasible"])
        delta = 100 * (s - base_sum) / base_sum
        note = f"infeasible {infeas}건" if infeas else "off 시 악화 = 기여도"
        lines.append(f"| {name} | {s:.0f} | {delta:+.1f}% | {note} |")
    return "\n".join(lines) + "\n"


def milestones(trace_lines, marks):
    pts = [(float(t), float(o)) for t, o in
           (ln.split(",") for ln in trace_lines if ln.strip())]
    out = []
    for m in marks:
        upto = [o for t, o in pts if t <= m]
        out.append((m, min(upto) if upto else float("nan")))
    return out


def _bench(out_path, timelimit, instances, disable=None):
    cmd = [sys.executable, "experiments/run_bench.py", "--algo", "ours",
           "--timelimit", str(timelimit), "--out", str(out_path),
           "--instances", instances]
    if disable:
        cmd += ["--disable", disable]
    subprocess.run(cmd, cwd=ROOT, env=dict(os.environ, OGC_SEED="42"), check=True)
    return json.loads(out_path.read_text())["runs"]


def cmd_stats(_args):
    rows = []
    for sub in ("training_instances", "train-set2"):
        for p in sorted((ROOT / "data" / sub / "train").glob("*.json"),
                        key=lambda p: int(p.stem.split("_")[1])):
            rows.append(instance_stats_row(p.stem, json.loads(p.read_text())))
    hdr = list(rows[0])
    md = ["| " + " | ".join(hdr) + " |", "|" + "---|" * len(hdr)]
    md += ["| " + " | ".join(str(r[k]) for k in hdr) + " |" for r in rows]
    (ASSETS / "instance-stats.md").write_text("\n".join(md) + "\n", encoding="utf-8")


def cmd_ablation(args):
    work = ROOT / "experiments" / "results" / "ablation"
    work.mkdir(parents=True, exist_ok=True)
    base = _bench(work / "full.json", args.timelimit, args.instances)
    for group, out_name in ((OPERATORS, "ablation-operators.md"),
                            (STAGES, "ablation-stages.md")):
        dis = {tok: _bench(work / f"off_{tok}.json", args.timelimit,
                           args.instances, disable=tok) for tok in group}
        (ASSETS / out_name).write_text(render_ablation_md(base, dis), encoding="utf-8")


def cmd_convergence(args):
    md = ["| instance | " + " | ".join(f"t={m}s" for m in (10, 30, 60, 120, 300)) + " |",
          "|---|---|---|---|---|---|"]
    for name in ("prob_1", "prob_23"):
        trace = ROOT / "experiments" / "results" / f"trace_{name}.csv"
        trace.unlink(missing_ok=True)
        code = ("import json,sys; sys.path[:0]=['solver','baseline'];"
                "from solver.shell import solve;"
                f"p=json.load(open('data/training_instances/train/{name}.json'));"
                "solve(p,300)")
        subprocess.run([sys.executable, "-c", code], cwd=ROOT, check=True,
                       env=dict(os.environ, OGC_SEED="42",
                                OGC_TRACE_FILE=str(trace)))
        lines = trace.read_text().strip().splitlines()
        (ASSETS / f"convergence-{name.replace('_', '')}.csv").write_text(
            "\n".join(lines) + "\n", encoding="utf-8")
        ms = milestones(lines, [10, 30, 60, 120, 300])
        md.append(f"| {name} | " + " | ".join(f"{o:.0f}" for _, o in ms) + " |")
    (ASSETS / "convergence.md").write_text("\n".join(md) + "\n", encoding="utf-8")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("what", choices=["stats", "ablation", "convergence", "all"])
    ap.add_argument("--timelimit", type=int, default=60)
    ap.add_argument("--instances", default=DEFAULT_INSTANCES)
    args = ap.parse_args()
    ASSETS.mkdir(parents=True, exist_ok=True)
    if args.what in ("stats", "all"):
        cmd_stats(args)
    if args.what in ("ablation", "all"):
        cmd_ablation(args)
    if args.what in ("convergence", "all"):
        cmd_convergence(args)
    print("written to", ASSETS)


if __name__ == "__main__":
    main()
```

주의: `prob_1.json`이 `train-set2`에 있으면 `cmd_convergence`의 경로를 conftest `_load`처럼 두 디렉토리 탐색으로 바꾼다 (Task 5의 `_find` 함수와 동일 패턴 재사용).

> 💡 **설명 — `cmd_convergence`가 왜 굳이 `subprocess`로 `solve(p, 300)`을 돌리면서 `OGC_TRACE_FILE`을 지정하는가:** 300초짜리 실행 하나를 통해 "그 300초 동안 목적값이 어떻게 개선되어 갔는지"의 전체 기록을 얻으려는 것입니다. `OGC_TRACE_FILE` 환경변수(Task 2에서 배선)를 지정해서 실행하면, `shell.solve` 내부의 `ctx.offer`가 호출될 때마다(더 좋은 해를 찾을 때마다) 자동으로 그 파일에 "경과초,목적값"이 追記됩니다. 이 CSV 파일 자체를 `docs/report-assets/convergence-prob1.csv`처럼 그대로 보관해두는 이유는, 나중에 보고서에서 실제 꺾은선 그래프(예: matplotlib이나 표 계산 프로그램으로)를 그릴 수 있는 원본 데이터로 남겨두기 위해서입니다. `milestones` 함수로 뽑은 요약표(`convergence.md`)는 "본문에 바로 넣을 간단한 표", CSV는 "필요하면 그래프로 그릴 수 있는 원본 데이터"로 역할이 나뉩니다.

- [ ] **Step 4: 단위 테스트 + stats 실행**

Run: `conda run -n ogc2026 python -m pytest tests/test_report_data.py -v`
Expected: 3 passed
Run: `conda run -n ogc2026 python experiments/report_data.py stats && head -5 docs/report-assets/instance-stats.md`
Expected: 40행 표 (n/m/레이어/지평/zero-slack/밀도)

- [ ] **Step 5: ablation + convergence 실행 (수동, ~90분)**

Run: `conda run -n ogc2026 python experiments/report_data.py ablation --timelimit 60`
Expected: `ablation-operators.md`(연산자 6행), `ablation-stages.md`(3행) — off 시 악화율이 양수인 연산자가 기여 연산자. `--disable` 토큰이 run_bench와 다르면 `OPERATORS`/`STAGES` 상수를 실제 토큰으로 수정(전제 B).
Run: `conda run -n ogc2026 python experiments/report_data.py convergence`
Expected: csv 2개 + `convergence.md` 마일스톤 표.

- [ ] **Step 6: Commit**

```bash
git add experiments/report_data.py tests/test_report_data.py docs/report-assets/
git commit -m "feat(week4): 보고서 데이터 집계 (ablation/수렴곡선/인스턴스 통계)"
```

### Task 15: 4주차 종료 기준 총괄 검증·기록

**Files:**
- Modify: `docs/superpowers/plans/2026-07-02-ogc2026-week4-hardening-submission.md` (하단에 결과 추기)

**Interfaces:**
- Consumes: Task 1~14 전부.
- Produces: 설계서 §7 4주차 완료 기준("firejail 통과, 제출 리허설(zip 구조 검증), 파라미터 확정") 충족 증거.

> 💡 **설명 — 1주차의 "완료 기준"(Task 14 말미)과 완전히 같은 성격의 마무리 절입니다.** 1주차 주석본에서 "완료 기준이 이번 주 전체의 채점표"라고 설명했던 것처럼, 이 Task도 4주차 전체가 정말 목표를 달성했는지 최종적으로 세 항목(firejail 통과/리허설 GO/파라미터 확정)으로 자가 채점합니다. 여기서 흥미로운 점은 이 Task의 "Modify" 대상이 **바로 이 계획 문서 자기 자신**이라는 것입니다 — 계획을 실행한 결과(측정값)를 계획 문서 하단에 그대로 기록해 남겨서, 나중에 누구든 "4주차가 실제로 어떻게 끝났는지"를 이 파일 하나만 보고 알 수 있게 하는 것입니다(그래서 원본 문서 맨 끝에 "4주차 종료 기록 (실행 후 채움)"이라는 빈 틀이 있는 것입니다). 참고로 이 학습용 주석본은 원본을 그대로 옮긴 사본이므로, 실제 프로젝트에서 이 기록을 채우는 작업은 (계약대로) 원본 파일에서 이루어져야 하며 이 주석본에는 반영되지 않습니다.

- [ ] **Step 1: 전체 테스트 스위트**

Run: `conda run -n ogc2026 python -m pytest tests -q`
Expected: 전부 PASS (slow/stress/sim 제외 기본셋)
Run: `conda run -n ogc2026 python -m pytest tests -q -m "stress or sim"`
Expected: 스트레스 게이트 + 서버심 스모크 PASS
Run: `OGC_LONG=1 conda run -n ogc2026 python -m pytest tests -q -m slow`
Expected: 300/1800s 예산 + 메모리 가드 PASS

- [ ] **Step 2: 종료 기준 3항목 확인**

1. firejail 통과: `bash scripts/server_sim.sh dist/staging 60 $(ls data/*/train/*.json data/stress/*.json)` → `SERVER-SIM PASS`
2. 제출 리허설: `bash scripts/rehearsal.sh` → `REHEARSAL GO` (zip 구조 검증 포함)
3. 파라미터 확정: `git log --oneline -5`에 Task 11 커밋 존재 + `experiments/results/tune/summary.json` 존재

- [ ] **Step 3: 결과 추기 + 최종 커밋**

이 plan 파일 하단에 아래 형식으로 측정값을 기록:

```markdown
## 4주차 종료 기록 (실행 후 채움)
- pytest 기본/stress/sim/slow: <각 pass 수>
- server_sim 풀셋: PASS (49/49 FEASIBLE, TLE 0, EXCEPTION 0, CRASH 0)
- rehearsal: GO / zip <크기>MB / 검증기 위반 0
- 확정 파라미터: kappa=<v> bias_p=<v> removal_rate=<v> rrt_start=<v> phases=<v,v,v>
- peak RSS(최대 인스턴스): <v> GB / 1800s 반환 시각: <v> s
```

```bash
git add docs/superpowers/plans/2026-07-02-ogc2026-week4-hardening-submission.md
git commit -m "chore(week4): 종료 기준 충족 기록 (firejail/리허설/파라미터 확정)"
```

---

## 계약 변경 요청

1주차 "인터페이스 계약"은 전부 무변경으로 유지한다. 아래는 2~3주차 산출물에 대한 **추가(additive) 요청**뿐이며, 기존 시그니처 변경은 없다.

1. **(필수·추가) `solver/alns/loop.py::AlnsCfg`에 `max_iters: int | None = None` 필드 추가**, `run_alns` 루프 조건을 `while not budget.expired() and it < (cfg.max_iters or 10**18):`로 확장. 용도: 결정성 모드(Task 3)에서 wall-clock 대신 반복수 캡. 2주차 구현에 이미 있으면 요청 소멸.
2. **(조건부·추가) `ogc_core.Core.set_nfp_cache_limit(max_pairs: int) -> None` 및 `Core.nfp_cache_info() -> tuple[int, int]`(쌍 수, 바이트)**. 발동 조건: Task 6 메모리 가드가 14GB를 초과할 때만. 미초과 시 도입하지 않는다.
3. **(정보·비변경) `experiments/run_bench.py --disable` 토큰 표준 목록 확정 요청**: `random, worst, shaw, time_slice, spatial_column, blocking_set, mh_retime, mh_pool, z2z3`. 2주차 실제 토큰이 다르면 본 계획 Task 14의 `OPERATORS`/`STAGES` 상수를 실제 값으로 치환(계약 변경 아님).
4. **(정보·비변경) 신규 환경변수는 전부 opt-in**: `OGC_CONFIG`, `OGC_SEED`, `OGC_DET`, `OGC_TRACE_FILE`, `OGC_DEBUG`, `OGC_LONG` — 미설정 시 기존 동작과 동일하며 제출 환경에는 존재하지 않으므로 서버 거동 불변. (`OGC_FORCE_FALLBACK`은 1주차 기존 계약.)

> 💡 **설명 — "계약 변경 요청"이라는 이 절의 역할, 그리고 1주차 "계약 부록"과의 관계:** 이 절은 이 문서(4주차 계획)가 **2~3주차 계획에게** 보내는 요청 목록입니다 — "너희가 만든 산출물에 이런 필드/함수를 추가로 얹어달라"는 것. 재미있게도 1주차 주석본에서 이미 읽었던 "계약 부록 - 주차 간 조정 확정" 절(1주차 원본 205~219행)이 바로 **이 요청들에 대한 최종 답변**입니다 — 실제로 대조해보면:
> - 요청 1(`AlnsCfg.max_iters`)은 1주차 부록 3번 항목에서 "2주차 정의 `AlnsConfig`가 정본이고, 이 필드 추가 요청은 필드가 이미 있으므로 소멸"이라고 이미 해소되어 있습니다.
> - 요청 2(`set_nfp_cache_limit`/`nfp_cache_info`)는 1주차 부록 5번 항목에서 "조건부 Core API 사전 승인 — 발동 조건 충족 시에만 구현"이라고 이미 승인되어 있습니다(정확히 여기서 말하는 "14GB 초과 시").
> - 요청 3(disable 토큰 표준)은 1주차 부록 4번 항목("벤치 토큰 표준")에서 실제 토큰 이름이 `retiming/pool/polish`로 정리되었다고 확정되어 있습니다(이 4주차 문서의 `mh_retime/mh_pool/z2z3`이라는 표기는 그 이름들의 구버전 초안이었던 셈).
> - 요청 4(환경변수)는 1주차 부록 7번 항목("환경변수 레지스트리")에 이미 등록되어 있습니다.
>
> 즉 이 절을 보면 "4주차 계획을 쓴 시점에는 아직 2~3주차 구현이 어떻게 될지 확실하지 않아서 이렇게 요청해두었지만, 나중에(2026-07-03) 전체 계획을 한 번 더 조율하면서 1주차 문서의 부록에 최종 결론이 이미 정리되었다"는 **문서들 사이의 시간적 관계**를 알 수 있습니다. 실제 구현자는 이 절이 아니라 1주차의 "계약 부록"을 최종 기준으로 삼아야 합니다(1주차 부록 자체가 "본문보다 부록이 우선"이라고 명시하고 있음).

## Self-Review 체크 결과

**1. 스펙 커버리지** (설계서 §5·§6·§7 4주차·§8 ↔ Task 매핑):
- §7 4주차 "견고성 하드닝(폴백·예외·시간예산)" → Task 2(무예외+티어), Task 4(예산 30/60/300/1800s), Task 5(저하 모드), Task 6(메모리), Task 3(재현성).
- §8 "숨은 인스턴스 분포 변화 / 고밀도 feasible 실패" → Task 7(9종 생성기: n200/n400, bay8, layers5, horizon300, dense, tight, singlebay, noorient), Task 8(전건 feasible 게이트).
- §6 "서버 시뮬레이션(firejail 16GB + cpulimit 400%) 무사고 통과 = 제출 게이트" → Task 9 + Task 13.
- §7 "튜닝" / §4 파라미터(kappa, bias_p, removal 0.2~0.35, RRT 2~5%, 예산 배분) → Task 1(설정 소스), Task 10(하네스+2시드 통계), Task 11(확정).
- §5 제출 패키징(루트 myalgorithm/15MB/상대경로/utils 무수정/확장자/12h 쿨다운 게이트) + 문제분석 §5.1·§5.2 → Task 12(빌더+검증기), Task 13(리허설+체크리스트, 평가 상태 6종 대응표).
- §6 "ablation·기술보고서 재료 축적" → Task 14. §7 종료 기준 3항목 → Task 15.
- 갭 없음. 리더보드 피드백 대응·보고서 초안 작성 자체는 §7 "이후" 항목으로 의도적 제외.

**2. 플레이스홀더 스캔**: 전 Task에 실행 가능한 테스트/구현 코드, 실행 명령, 기대 출력 포함. "기존 로직 이동"(Task 2)·"실제 토큰으로 치환"(Task 14)은 2~3주차 기존 코드에 대한 정밀 리팩터 지시로, 신규 코드 생략이 아님. 조건부 단계(Task 6 Step 3)는 발동 조건과 절차를 명시.

**3. 타입/시그니처 일관성**: `SolverConfig` 필드명(kappa/bias_p/removal_rate/rrt_start/phase_construct/phase_alns/phase_mh/seed/deterministic/det_starts/det_alns_iters)이 Task 1 정의 = Task 2 shell 사용 = Task 10 SPACE/`_expand` 출력 = Task 11 반영 대상과 일치. `_Ctx.TIER_ORDER`·`_phase_*` 심볼이 Task 2 정의 = Task 2/3 테스트 monkeypatch 대상과 일치. `validate_zip -> list[str]`이 Task 12 테스트 `_validate` 사용과 일치. `server_sim.sh <pkg> <tl> <inst...>` CLI가 Task 9 테스트 = Task 13 rehearsal 호출과 일치. run_bench 결과 키(`obj`, `feasible`, `instance`)가 전제 B = Task 10 `score_trial` = Task 14 `render_ablation_md`와 일치. `check_feasibility` 반환 키(feasible/stage/violations/objective/obj1/obj2/obj3)는 baseline utils 실물과 대조 확인함.

> 💡 **설명 — 마지막 절도 1주차와 같은 성격의 "자체 검토(self-review)"입니다.** 1주차 주석본에서 설명했듯, "설계서 몇 절이 이 계획의 어느 Task로 구현되는지" 하나하나 대조해 빠뜨린 요구사항이 없는지 스스로 점검한 기록입니다. 4주차 버전에서 새로 눈에 띄는 건 **3번 항목("타입/시그니처 일관성")** 이 유독 꼼꼼하다는 점인데, 이는 이 계획 자체가 `SolverConfig`라는 **여러 Task를 관통하는 공유 설정 객체**를 새로 도입했기 때문입니다 — 설정 클래스의 필드 이름이 Task 1(정의)→Task 2(사용)→Task 10(탐색공간)→Task 11(확정 반영)까지 하나라도 어긋나면 전체가 깨지므로, 이런 "여러 Task에 걸쳐 이름이 반드시 일치해야 하는 것들"을 self-review에서 명시적으로 다시 짚어준 것입니다. 1주차의 self-review는 "스펙 커버리지"만 확인했다면, 4주차는 "스펙 커버리지 + 플레이스홀더(구현 생략) 없음 + 타입/시그니처 일관성"까지 3단계로 늘어난 것도, 이번 주차가 여러 주차의 산출물을 통합하는 성격이라 더 꼼꼼한 자기 검증이 필요했기 때문이라고 볼 수 있습니다.

---

## 부록: 이 문서에서 다룬 용어 색인

| 용어 | 처음 나온 곳 |
|---|---|
| 하드닝(hardening) — 예외/경계/자원 제약 견고성 강화 | 0.1 |
| (리마인드) Block/Bay/ENTRY·EXIT/Layer/Orientation, Z1/Z2/Z3, Stage1~5·j≥k, SCALE/NFP, ATC/BLF/biased randomization, Core API | 0.2 (1주차 주석본 참고) |
| `SolverConfig` 단일 설정 소스 통합 배경 | 0.4 |
| frozen dataclass / `dataclasses.replace` | Task 1 |
| `OGC_CONFIG`/`OGC_SEED`/`OGC_DET` 오버라이드 우선순위, fail-fast | Task 1 |
| 페이즈 격리 + 티어 강등 체인 / `_Ctx.offer`/`tiers` | Task 2 |
| `BaseException` 3중 방어망 / `monkeypatch.setattr` | Task 2 |
| `OGC_TRACE_FILE` incumbent 기록 훅 | Task 2 |
| 결정론적(deterministic) 실행이 재현성에 왜 필요한가 | Task 3 |
| 비결정성 누수 4유형(전역 random/numpy.random/wall-clock/dict·set 순서) | Task 3 |
| subprocess 기반 프로세스 경계 재현성 검증 | Task 3 |
| pytest 마커(slow/stress/sim)와 `addopts` | Task 4 |
| 저하 모드(degraded mode) / 환경변수 격리(leak) 검사 | Task 5 |
| RSS(Resident Set Size) / peak RSS / `resource.getrusage` | Task 6 |
| NFP 캐시 메모리 폭증 원인 / LRU 캐시 축출 | Task 6 |
| YAGNI 원칙 / 조건부 구현(계약 사전 승인) | Task 6 |
| 숨은 인스턴스 분포 변화 리스크 / 적대적(adversarial) 합성 인스턴스 생성 | Task 7 |
| 결정적 데이터 생성기(재현 가능한 테스트 픽스처) | Task 7 |
| `max(..., default=0)` 방어 패턴 | Task 8 |
| firejail(샌드박스)/cpulimit(CPU 제한)/`--rlimit-as`/`--net=none` | Task 9 |
| WSL2·AppArmor·setuid-root·cgroup 관련 특이사항 | Task 9 |
| `@pytest.mark.skipif` | Task 9 |
| 그리드 서치 vs 랜덤 서치 / 정규화 평균 점수 | Task 10 |
| 전 시드 우세 채택 규칙(통계적 최소 안전장치) | Task 10 |
| 의존성 주입(`runner=None`) / 순수 함수 단위테스트 | Task 10 |
| `nohup ... &` 백그라운드 장기 실행 | Task 11 |
| 튜닝 결과 "null(기본값 유지)"의 정당성 | Task 11 |
| 제출 zip 스테이징(staging) / `build()` vs `validate_zip()` 분리 | Task 12 |
| 변조(tamper) 테스트 스타일 | Task 12 |
| `ast.parse`/`ast.walk`로 실행 없이 시그니처 검증 | Task 12 |
| 언팩 후 클린 서브프로세스 검증(`.so` 상대 로드) | Task 12 |
| 제출 리허설 게이트 / `set -euo pipefail` / `trap ... EXIT` | Task 13 |
| 트레이서빌리티 매트릭스(리스크→테스트 매핑 표) | Task 13 |
| Ablation(제거 실험)과 기여도 해석 | Task 14 |
| 수렴곡선(convergence curve) / `milestones` best-so-far | Task 14 |
| 계약 변경 요청과 1주차 "계약 부록"의 최종 확정 관계 | 계약 변경 요청 |

