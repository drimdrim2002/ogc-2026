# OGC 2026 — 4주차: 견고성 하드닝 + 스트레스 검증 + 튜닝 + 제출 패키징 구현 계획

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 어떤 입력·예산·환경에서도 예외 없이 시간 내 feasible 해를 반환하는 상태로 솔버를 하드닝하고, firejail 서버 시뮬레이션과 제출 zip 리허설을 통과시키며, 튜닝된 파라미터를 `solver/config.py`에 확정한다.

**Architecture:** L5 견고성 셸을 "최상위 절대 무예외 + 페이즈별 예외 격리 + 티어(incumbent) 강등 체인"으로 재구조화하고, 설정을 `solver/config.py` 단일 소스(+`OGC_*` 환경변수 오버라이드)로 통합해 튜닝·결정성·저하모드를 같은 축으로 제어한다. 숨은 인스턴스 리스크는 합성 스트레스 인스턴스 생성기로 선제 검증하고, firejail+cpulimit 서버 시뮬레이션 → zip 빌드·검증 → 언팩 리허설의 3단 게이트로 12시간 쿨다운 낭비를 차단한다.

**Tech Stack:** Python 3.12 (conda env `ogc2026`), pytest, firejail + cpulimit (WSL2 Ubuntu 24.04), zipfile/hashlib/ast (표준 라이브러리만으로 패키징·검증), 기존 `ogc_core`(.so) + `baseline/utils.py`(최종 oracle).

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

---

### Task 1: `solver/config.py` — 단일 설정 소스 + `OGC_*` 환경변수 오버라이드

**Files:**
- Create: `solver/config.py`
- Test: `tests/test_config.py`

**Interfaces:**
- Consumes: 없음 (표준 라이브러리만).
- Produces: `SolverConfig` frozen dataclass, `DEFAULT: SolverConfig`, `load_config() -> SolverConfig`. 오버라이드 우선순위: `DEFAULT` < `OGC_CONFIG`(JSON 파일 경로) < `OGC_SEED`/`OGC_DET`. Task 2의 shell, Task 10의 tune.py, Task 3의 결정성 테스트가 이것을 사용한다. 튜닝 확정값(Task 11)은 이 파일의 `DEFAULT` 필드 기본값을 직접 수정해 반영한다.

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

- [ ] **Step 2: 실패 확인**

Run: `conda run -n ogc2026 python -m pytest tests/test_determinism.py -v`
Expected: 최소 `test_fixed_seed_reproducible_in_process` FAIL (2~3주차 코드가 전역 `random`/`time` 기반 난수를 쓰거나 wall-clock 반복이면 결과 상이)

- [ ] **Step 3: 비결정성 누수 제거**

체크리스트 (실패 원인을 하나씩 소거):
1. `solver/` 전체에서 시드 없는 난수 사용 검색: `grep -rn "random\.\(random\|randint\|choice\|shuffle\|sample\)" solver/ | grep -v "Random("` → 발견 시 해당 함수에 `rng` 파라미터를 물려받도록 수정 (shell이 `random.Random(ctx.base_seed …)`를 이미 주입).
2. `numpy.random` 전역 사용 검색: `grep -rn "np\.random\.\|numpy\.random\." solver/` → `numpy.random.Generator(numpy.random.PCG64(seed))` 주입으로 교체.
3. det 모드에서 wall-clock 조건 잔존 검색: `_phase_construct`는 `det_starts`로, ALNS는 `max_iters`로 반복이 캡되는지 확인 (`AlnsCfg.max_iters` 미구현이면 `run_alns` 루프 조건에 `it < (cfg.max_iters or 10**18)` 추가 — 계약 변경 요청 #1).
4. `dict`/`set` 순회 순서 의존 검색: 연산자 선택·후보 정렬에 `set` 순회가 있으면 `sorted()`로 고정.

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

- [ ] **Step 3: 단기 케이스 실행**

Run: `conda run -n ogc2026 python -m pytest tests/test_budget_stress.py -v`
Expected: `test_budget_short` 4건 + `test_tiny_budget_still_returns` PASS (slow는 deselect). 실패 시 흔한 원인: (a) `_finalize`의 utils 검증이 마진 밖으로 밀림 → `Budget` safety 내에서 검증 시간을 선확보하도록 `budget.phase("finalize", ...)` 예약 추가, (b) ALNS가 `budget.expired()`를 반복 내부에서 확인하지 않음 → 루프 선두에 체크 삽입. 원인 수정 전 진행 금지.

- [ ] **Step 4: 장기 케이스 실행 (수동, 1회)**

Run: `OGC_LONG=1 conda run -n ogc2026 python -m pytest tests/test_budget_stress.py -m slow -v`
Expected: 300s·1800s 2건 PASS (총 ~35분 소요). 결과 로그를 확인해 1800s 케이스의 실제 반환 시각(예: ~1650s)을 기록.

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
- Consumes: Task 2 `shell.solve`, Task 7의 `data/stress/stress_n400.json` (없으면 40개 중 최대 인스턴스로 대체 — 테스트가 자동 선택).
- Produces: peak RSS 상한 회귀 테스트. 초과 시에만 NFP 캐시 축출 도입(계약 변경 요청 #2, 조건부).

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

- [ ] **Step 2: 실행**

Run: `conda run -n ogc2026 python -m pytest tests/test_memory_guard.py -m slow -v -s`
Expected: PASS + `peak RSS = x.xx GB` 출력. 설계 근거(설계서 §2): n=100 전쌍 NFP ~0.1GB → n=400은 쌍수 16배 ≈ 1.6GB + 상태/파이썬 오버헤드로 수 GB 수준이 정상.

- [ ] **Step 3: (조건부) 14GB 초과 시에만 — NFP 캐시 축출 도입**

초과 시 절차: ① `ogc_core`에 LRU 상한 추가 — C++ `nfp.cpp`의 해시맵을 "삽입 시 상한 초과면 가장 오래된 키 제거"로 바꾸고 바인딩 `Core.set_nfp_cache_limit(max_pairs: int)`·`Core.nfp_cache_info() -> tuple[pairs, bytes]` 추가(계약 변경 요청 #2 승인 후). ② `_phase_setup`에서 `n_blocks > 200`일 때 `set_nfp_cache_limit(120_000)` 호출. ③ 본 테스트 재실행으로 상한 준수 확인. **14GB 미만이면 이 단계를 건너뛴다(YAGNI).**

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

- [ ] **Step 2: 실행**

Run: `conda run -n ogc2026 python -m pytest tests/test_stress_solve.py -m stress -v -s`
Expected: 3 passed (총 ~12분: 9×60~120s). FAIL 유형별 처방: (a) n=400에서 시간 초과 → `_phase_construct` 멀티스타트 횟수가 인스턴스 규모에 비례해 줄어드는지 확인(첫 construct가 예산 절반을 넘기면 이후 스타트 스킵), (b) tight 블록 infeasible → `free_positions`가 bbox==bay일 때 (0,0) 후보를 내는지 코어 경계(≤) 확인, (c) singlebay에서 예외 → Z2 계산의 bay쌍 순회가 빈 시퀀스에서 `max()` 호출하지 않는지 확인(`max(..., default=0)`).

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

- [ ] **Step 1: 본 튜닝 실행 (백그라운드, ~16시간)**

Run: `nohup conda run -n ogc2026 python experiments/tune.py --mode random --trials 10 --seeds 2 --timelimit 60 --instances all --out experiments/results/tune > experiments/results/tune.log 2>&1 &`
예상 소요: (10 trials + default) × 2 seeds × 40 인스턴스 × 60s ≈ 15시간. 야간 실행 권장. 진행 확인: `tail -f experiments/results/tune.log` (trial별 점수 라인).

- [ ] **Step 2: 승자 확인·반영**

Run: `cat experiments/results/tune/best_config.json`
- `null`이면: 기본값 유지가 결론 — Step 3으로 건너뛰고 그 사실을 커밋 메시지에 명기.
- 값이 있으면: `solver/config.py`의 `SolverConfig` 필드 기본값을 best_config.json 값으로 **직접 수정**하고, `tests/test_config.py::test_default_config_values`의 기대값도 같은 값으로 갱신. (예: `kappa: float = 2.0` 등 — 실제 값은 튜닝 결과를 따른다.)

- [ ] **Step 3: 확정값 회귀 확인**

Run: `conda run -n ogc2026 python -m pytest tests/test_config.py tests/test_budget_stress.py -v`
Expected: 전부 PASS (새 기본값으로도 예산 준수).
Run: `conda run -n ogc2026 python experiments/run_bench.py --algo ours --timelimit 60 --instances prob_1,prob_2,prob_21,prob_23 --out experiments/results/post-tune-sanity.json`
Expected: 4개 전부 feasible, obj 합계가 튜닝 전(default_seed1.json의 해당 인스턴스 합계) 이하.

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
