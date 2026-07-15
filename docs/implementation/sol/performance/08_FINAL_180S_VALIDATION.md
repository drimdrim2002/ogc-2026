# 8단계 — hard-10 180초 최종 검증

## 목표

모든 승격 단계가 반영된 최종 후보를 original baseline과 동일 조건으로 비교해, 장시간 예산에서 실제 경쟁 품질이 개선됐는지 최종 판정한다.

이 단계에서는 새 최적화를 구현하지 않는다. 발견된 문제는 해당 단계로 되돌려 별도 수정한다.

## 비교 대상

- **original baseline**: 0단계에서 동결한 최초 commit/config
- **final candidate**: 7단계에서 동결한 cumulative commit/default config

중간 artifact나 서로 다른 환경의 과거 결과를 비교에 사용하지 않는다.

## 고정 final matrix

- instances: `HARD10_MANIFEST.json`의 정확한 10개
- budget: 180초
- seeds: `20260710`, `20260711`, `20260712`
- variants: original baseline, final candidate
- 총 실행 수: 10 × 3 × 2 = 60
- 실행 순서: instance/seed별 variant 교차 실행
- profiler: OFF
- outer timeout: benchmark calibration으로 고정

## 사전 조건

- worktree의 source가 비교 commit과 정확히 일치하거나 variant별 깨끗한 실행 경로가 준비됨
- dataset/manifest/config hash 일치
- 전체 unittest 통과
- final candidate의 submission default와 benchmark config 일치
- hard-10 membership 변경 없음

## 진행 절차

1. original/final source, config, dataset, environment identity를 기록한다.
2. benchmark launcher/checker overhead를 calibration한다.
3. 60개 요청 키를 미리 생성하고 expected matrix를 검증한다.
4. baseline/final을 instance/seed별 교차 실행한다.
5. raw record를 append/flush하고 terminal schema-valid record만 resume한다.
6. checker Stage 5, objective parity, trace, timeout, exception을 감사한다.
7. instance/seed paired objective와 weighted components를 계산한다.
8. final candidate archive를 생성해 allowlist, 크기, clean extraction, normal/Gurobi-absent smoke를 검증한다.
9. 승인 또는 기각 결정을 기록한다. 실패 시 이 단계에서 코드를 고치지 않는다.

## 최종 보고 지표

- run/expected/missing/duplicate
- Stage 5, exception, timeout, checker failure
- W/T/L과 Borda
- median/p90/worst paired relative delta
- `w1*DeltaZ1`, `w2*DeltaZ2`, `w3*DeltaZ3`
- anytime primal integral과 time-to-first-improvement
- phase time, iterations, accepted/new-best
- instance별 표와 seed 안정성
- archive hash/size/smoke

## 승인 조건

- 60/60 Stage 5
- missing/duplicate/exception/timeout/checker failure 0
- objective parity `<=1e-6`, trace regression 0
- final candidate Win > Loss
- median paired relative delta < 0
- Borda가 original baseline보다 개선
- 한 seed 또는 한 instance에만 의존하지 않는 개선
- submission archive 계약 통과

승인 조건을 만족하지 않으면 release candidate로 선언하지 않는다. 평균 개선이 있더라도 치명적인 instance 회귀는 원인과 leaderboard 영향을 별도로 평가한다.

## 선택적 release safeguard

사용자가 별도로 승인하면 hard-10 외 전체 40개에 대해 짧은 feasibility/회귀 screening을 추가할 수 있다. 이는 이번 hard-10 180초 필수 matrix에는 포함하지 않으며, hard-10 결과를 대체하지 않는다.

## 결과 기록

최종 60-run identity와 checksum, 모든 지표, archive 결과, 승인/기각 및 후속 단계를 추가한다.

## 실행 프롬프트

```text
OGC-SAGE 성능 개선 8단계 “hard-10 180초 최종 검증”만 수행한다.

먼저 다음 문서를 읽는다.
1. docs/implementation/sol/performance/README.md
2. docs/implementation/sol/performance/08_FINAL_180S_VALIDATION.md
3. 모든 0~7단계 결과 기록
4. HARD10_MANIFEST.json
5. docs/implementation/sol/09_PACKAGING_STRESS_HARDENING.md

이번 단계에서는 알고리즘이나 파라미터를 수정하지 않는다.

- 0단계 original baseline과 7단계 final candidate identity를 확인한다.
- hard-10 × 180초 × 3 seeds × 2 variants의 정확한 60-run matrix를 실행한다.
- instance/seed별로 두 variant를 교차 실행한다.
- profiler를 끄고 raw/summary/checksum과 resume 계약을 지킨다.
- Stage 5, parity, trace, exception, timeout부터 감사한다.
- W/T/L, Borda, relative delta, weighted Z1/Z2/Z3, anytime 지표를 계산한다.
- final archive allowlist/size/clean extraction/normal 및 Gurobi-absent smoke를 검증한다.
- 실패를 발견해도 이 단계에서 수정하지 말고 해당 선행 단계로 되돌릴 근거를 기록한다.
- 승인 또는 기각을 이 문서에 기록하고 종료한다.
- 커밋/push나 전체 40개 추가 screening은 별도 승인 없이는 수행하지 않는다.
```

## 2026-07-15 — 최종 60-run 검증 결과

### 범위와 identity

- run ID prefix: `step8-final-180s-20260715T150000KST`
- matrix: frozen hard-10 × `180` seconds × seeds `20260710`, `20260711`, `20260712` × original/final = 30 pairs, 60 runs.
- 각 pair의 실행 순서는 original→final, final→original을 번갈아 적용했다. profiler와 추가 daily-40 screening은 실행하지 않았다.
- original semantic identity는 0단계 문서의 `5f932a611e8076d01a3f59c1e4e44da350d522e6`이다. 0단계 당시 미커밋이던 telemetry 바이트를 재현 가능하게 보존한 exact execution snapshot `6466ce6c673616a70cb90bf875d8d45c8435d6fc`를 실행했다. 이 snapshot의 solver checksum과 config SHA-256 `b02608da...`는 0단계 동결 기록과 일치한다.
- final은 7단계 HEAD metadata `8974e26c50385156387f0fc091131030713c6db2` 위 현재 동결 payload다. runtime payload / benchmark config / submission source-list SHA-256은 `0bf8180c...` / `571a5e2f...` / `3149ebad...`이며 실행 전후 동일하다.
- hard-10 manifest / dataset SHA-256은 `5499cf99...` / `c093fb98...`다. `baseline/utils.py` / `baseline/baseline_greedy.py`는 `d45aaeaf...` / `8ec2cc81...`로 유지됐다.
- environment: macOS 26.5.2 arm64, Python 3.12.13, Shapely 2.1.2, Gurobi 13.0.2 restricted license.

### 사전 테스트와 hard blocker

- original frozen suite `184/184 PASS`, final suite `194/194 PASS`.
- run/expected/unique `60/60/60`, missing/duplicate/unexpected `0/0/0`.
- completed/Stage 5 `60/60`, exception/outer timeout/checker failure `0/0/0`.
- objective 및 component parity 최대 상대 오차 `0.0`, validated-best trace regression `0`, retime Z1 worsen `0`.
- 모든 record의 LNS invocation shape는 정확히 `anchor`, `extension`이며 partial/non-terminal record는 없다.
- 따라서 품질 계산 전에 확인하는 correctness hard blocker는 모두 PASS다. 단, 아래 실행 환경 overlap 감사가 별도로 FAIL이므로 exact 60-run 품질 matrix는 release evidence로 유효하지 않다.

### 전체 품질, component와 anytime

relative delta는 `(final - original) / original`이며 음수가 final 개선이다. weighted component improvement는 `w*(original - final)`로 양수가 final 개선이다.

아래 60-run 전체 수치는 관측 결과와 재현 진단을 위해 보존하지만, 실행 후 발견된 외부 benchmark overlap 때문에 release 판정용 authoritative metric은 아니다.

- W/T/L: **`19/0/11`**.
- Borda original/final: **`11/19`**.
- relative delta median / mean / p90 / worst: **`-0.8897% / +3.5236% / +37.7982% / +98.5922%`**.
- weighted component improvement 평균: `w1*ΔZ1 +2,758,086.73`, `w2*ΔZ2 -1,697.00`, `w3*ΔZ3 -8,168.33`. Z1은 개선했지만 Z2와 Z3는 악화했다.
- anytime primal integral mean original/final은 `2.746945 / 2.675416`이며 낮을수록 좋다. paired mean delta는 `-0.071529`로 final이 개선했다.
- time-to-first-improvement median original/final은 `7.6618s / 7.5603s`다. 분포 median은 final이 빠르지만 pair별 time delta median은 `+0.0430s`다.

### seed 안정성

| Seed | W/T/L | Borda O/F | Median | p90 | Worst |
|---:|---:|---:|---:|---:|---:|
| `20260710` | `8/0/2` | `2/8` | `-0.6309%` | `+7.1706%` | `+57.9079%` |
| `20260711` | `6/0/4` | `4/6` | `-3.1828%` | `+6.0875%` | `+37.7982%` |
| `20260712` | `5/0/5` | `5/5` | `+1.4417%` | `+90.2965%` | `+98.5922%` |

두 seed에서는 final이 우세하지만 `20260712`는 Win=Loss이고 median도 악화했다. 방향성이 단일 seed 하나에만 의존하지는 않지만 세 seed에서 안정적이지 않다.

### instance별 결과

| Instance | Δ seed10 | Δ seed11 | Δ seed12 | Median | Worst | W/T/L |
|---|---:|---:|---:|---:|---:|---:|
| `prob_38.json` | `-0.4022%` | `-2.7624%` | `+12.7625%` | `-0.4022%` | `+12.7625%` | `2/0/1` |
| `prob_23.json` | `+7.1706%` | `+37.7982%` | `-28.7552%` | `+7.1706%` | `+37.7982%` | `1/0/2` |
| `prob_40.json` | `-3.3688%` | `-3.6033%` | `-0.9200%` | `-3.3688%` | `-0.9200%` | `3/0/0` |
| `prob_25.json` | `-26.8050%` | `-13.4532%` | `+24.9221%` | `-13.4532%` | `+24.9221%` | `2/0/1` |
| `prob_27.json` | `-0.0621%` | `+1.0090%` | `-21.2285%` | `-0.0621%` | `+1.0090%` | `2/0/1` |
| `prob_39.json` | `-15.6844%` | `+4.8649%` | `+3.8033%` | `+3.8033%` | `+4.8649%` | `1/0/2` |
| `prob_21.json` | `-31.0160%` | `-29.4534%` | `+90.2965%` | `-29.4534%` | `+90.2965%` | `2/0/1` |
| `prob_33.json` | `-0.2364%` | `-4.4193%` | `-1.4223%` | `-1.4223%` | `-0.2364%` | `3/0/0` |
| `prob_28.json` | `+57.9079%` | `-31.8260%` | `-23.2282%` | `-23.2282%` | `+57.9079%` | `2/0/1` |
| `prob_24.json` | `-0.8595%` | `+6.0875%` | `+98.5922%` | `+6.0875%` | `+98.5922%` | `1/0/2` |

가장 큰 회귀는 `prob_24/20260712` `21,584,341→42,864,813`(+98.59%), `prob_21/20260712` `30,416,073→57,880,719`(+90.30%), `prob_28/20260710` `82,346,669→130,031,863`(+57.91%), `prob_23/20260711` `35,443,840→48,840,984`(+37.80%)다. 이는 평균이나 median 개선으로 상쇄하기에 너무 크다.

### phase와 activity

| Metric, 30 runs sum unless median | Original | Final |
|---|---:|---:|
| elapsed median | `171.1748s` | `171.1733s` |
| LNS iterations | `1,344` | `1,207` |
| LNS repair | `4,347.906s` | `4,483.594s` |
| LNS retime | `460.493s` | `332.220s` |
| LNS checker | `36.028s` | `30.246s` |
| operator attempts | `1,348` | `1,213` |
| accepted | `1,190` | `1,137` |
| new-best | `963` | `873` |

final은 retime/checker 시간이 줄었지만 repair 시간이 늘고 iteration, accepted, new-best가 감소했다. objective 개선은 처리량의 일관된 증가가 아니라 seed-sensitive trajectory 변화에 의존한다.

### archive 검증

- archive: `artifacts/ogc_sage/step9/step8-final-180s-20260715T150000KST-archive/submission.zip`.
- SHA-256 / size: `46c80f2e26d42e948faf02830e9ffc789a91242411aa68cca6aae381de8f1937` / `90,662 bytes` (`<=15 MiB`).
- current release allowlist 18 members, missing/extra/duplicate/unsafe `0/0/0/0`, ZIP integrity PASS, packaging tests `6/6 PASS`.
- clean normal: public stdout/stderr empty, feasible/Stage `true/5`, objective `1018`.
- clean Gurobi-absent: public stdout/stderr empty, feasible/Stage `true/5`, objective `1281`.
- 09단계 §27의 historical source `22b0826...` archive는 `utils.py`를 제외했지만, 이 성능 계획의 original baseline이 시작되는 후속 commit `5f932a6...`은 root `utils.py`를 organizer hash로 검증해 필수 member로 포함하도록 README/builder/tests를 명시적으로 변경했다. 이번 검증은 서로 일치하는 현재 release contract를 적용했다. 09단계 historical 설명은 packaging 문서 후속 정리 대상이지만 실제 archive gate 실패는 아니다.

### artifact와 checksum

- per-run raw/summary/`SHA256SUMS`: 각 60개, checksum entry 120/120 PASS.
- path-order raw / summary / checksum-file set SHA-256: `5904df4040c2c2e11b734007c40ceb6868d5c6f5bdc983f210ca09c05660c3ed` / `10ff72ef79fbe6913452718e3a4c954bfa512c3aac7cdd5d11a71feb94cb5109` / `993a6c3ca6f65831091a837047598a3a53e7b980fa1a68583e3db6198a90ecaa`.
- combined summary: `artifacts/ogc_sage/step9/step8-final-180s-20260715T150000KST-report/summary.json`, SHA-256 `c07bfa52052e7ca5df26e23296c09f44815971a0592d8de92bdeef6bb70ddc47`.

### 실행 환경 overlap 감사

- matrix UTC window는 `2026-07-15T12:03:02.558112Z`부터 `2026-07-15T14:54:52.613852Z`까지다.
- 최종 process 감사에서 이번 작업과 무관한 CPU-bound `baseline.harness.cli ab --stage s4 ... --feature assignment_refinement` benchmark(PID 관측값 `82113`)가 local `2026-07-15 23:07 KST`에 시작해 계속 실행 중임을 확인했다. 사용자/외부 프로세스이므로 중단하지 않았다.
- raw UTC와 보수적으로 대조하면 pair 22 original 후반이 부분 overlap이고 pair 23~30의 16 runs가 전부 overlap이다. 영향 run은 17개이며 pair 22는 variant 간 비대칭이라 pair 전체가 비교 불가다.
- overlap 전 완전한 clean prefix는 pair 1~21, 42 runs/21 pairs다. 이 prefix의 W/T/L `14/0/7`, median/mean/p90/worst relative delta는 `-0.8595% / -1.7310% / +12.7625% / +57.9079%`다.
- clean prefix에서도 `prob_23`이 두 seed 연속 `+7.17%`, `+37.80%` 회귀했고 `prob_28/20260710`은 `+57.91%` 회귀했다. 따라서 overlap 이전 증거만으로도 1단계 trajectory 안정성 위험은 남지만, 정확한 3-seed final 수치로 확정할 수는 없다.
- 결론: 현재 60-run artifact는 correctness와 재현 진단에는 유효하지만 release 품질 비교에는 **release-ineligible**다. 독점 부하 window에서 전체 60회를 새 run ID로 처음부터 재실행해야 하며 현재 raw에 일부 run을 교체·병합하지 않는다.

### 최종 판정과 복귀 단계

**release candidate 기각**

60/60 correctness와 archive gate는 통과했다. 관측된 전체 matrix도 W>L, 음의 median, Borda 및 anytime 개선을 보였지만 외부 benchmark가 17 runs에 overlap했으므로 이 수치로 승인할 수 없다. 또한 uncontaminated 21-pair prefix 자체가 p90 `+12.76%`, worst `+57.91%`와 `prob_23` 두 seed 연속 회귀를 보여 플레이북의 중대 회귀 위험을 해소하지 못했다.

production 의미론에서 2단계 MIP은 OFF이고 3~7단계 후보도 모두 승격 기각/OFF다. 따라서 original 대비 실제로 승격된 품질 변경은 1단계의 ALNS deadline-led budget, anchor/extension state continuity, `4/8/8` adaptation cadence다. 1단계 결과 기록도 추가 iteration이 acceptance/weight trajectory를 바꿔 일부 회귀를 만든다고 이미 지적했다. **알고리즘 복귀 지점은 1단계**다. seed-robust cadence/trajectory를 별도 patch와 1단계 gate로 다시 검증한 뒤, 외부 benchmark가 없는 독점 부하 window에서 이 60-run 8단계를 새 artifact로 전부 재실행해야 한다.

이번 단계에서는 코드, 알고리즘, 파라미터를 수정하지 않았다. commit/push와 선택적 daily-40 추가 40회 screening도 실행하지 않았다.
