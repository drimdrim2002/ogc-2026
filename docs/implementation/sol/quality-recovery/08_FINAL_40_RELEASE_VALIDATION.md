# Phase 8 — 마감 우선 release 검증

상태: **GO WITH DEADLINE WAIVER**  
적용일: 2026-07-16  
artifact: `artifacts/ogc_sage/quality-recovery/p8-fast-20260716T015038Z-302f9e8f`

## 1. 사용자 deadline override

기존 all-40 × `{10,60,300}`초 × 5 seeds × 2 variants, 1,200-run 계획과 Phase 0~7 순차 재실행은 마감 시간에 맞지 않아 폐기한다. 사용자가 2026-07-16에 명시적으로 제공한 120초 실행 결과 다섯 건을 성능 및 official-data release evidence로 수용하고, 현재 source에는 재실행 시간이 짧은 correctness/package gate만 적용한다.

이 override에 따라 다음은 release 차단 조건이 아니다.

- 1,200-run competition matrix 미실행
- 1,800초 exploitation probe 미실행
- fresh Phase 0 및 Phase 1~7 순차 재실행 미실행
- working tree dirty 상태

dirty working tree 대신 제출 ZIP의 정확한 member 목록, source hash, config hash, ZIP hash를 동결한다. 사용자 제공 결과의 raw log 및 실행 source hash가 없으므로 해당 다섯 행은 `user-supplied / arithmetic-verified`로 기록하며 재현 측정으로 표현하지 않는다.

## 2. 수용한 120초 결과

모든 행은 사용자가 feasible 및 Stage 5 PASS로 확인했다. 현재 local official input의 weights로 Objective를 다시 계산했으며 다섯 행 모두 `Objective = w1·Z1 + w2·Z2 + w3·Z3`가 정확히 일치한다.

| 데이터 | 실제 solver 시간 | Objective | Z1 | Z2 | Z3 | 산술 검증 |
|---|---:|---:|---:|---:|---:|---|
| `prob_21.json` | 114.152초 | 37,563,115 | 2,775 | 829 | 3,705 | PASS |
| `prob_22.json` | 114.167초 | 4,703,663 | 259 | 1,872 | 3,112 | PASS |
| `prob_23.json` | 114.142초 | 59,034,462 | 4,325 | 541 | 1,940 | PASS |
| `prob_24.json` | 114.102초 | 42,703,006 | 3,157 | 1,125 | 2,017 | PASS |
| `prob_25.json` | 114.127초 | 5,892,441 | 8,765 | 226 | 2,298 | PASS |

입력 SHA-256:

- `prob_21.json`: `7026d83d43d479118c6edc918dcddc877c89c3e669163d7228d761912ce8f98b`
- `prob_22.json`: `798f79582b85ca8b312b70b5b6dcbedf4ab1610807c027b9fadf68af6b2d3d12`
- `prob_23.json`: `1b0e48c383157c3140d4197f1bb3326f9e67e0ded0d8345bcc35d57186b94738`
- `prob_24.json`: `b236dc3e0a3326bf25af9762754fdb9ac8cd2277cc5eb030c37162c51d111e60`
- `prob_25.json`: `bc05276709763260f8412c37742106d34f44991e0d6ecf163c5e183727057fb5`

## 3. 마감 우선 hard gate

1. 사용자 제공 5/5가 feasible 및 Stage 5다.
2. 5/5 Objective가 local official weights와 정확히 일치한다.
3. 현재 source의 전체 baseline test suite가 PASS한다.
4. production default config를 hash로 동결한다.
5. canonical ZIP이 allowlist와 15MB 제한을 만족하고 압축 무결성 검사를 통과한다.
6. ZIP을 clean directory에 추출한 뒤 정상 및 Gurobi-absent 환경에서 file-location import, Stage 5, public silence가 모두 PASS한다.
7. solver/config는 이 gate에서 수정하지 않는다.

## 4. 실행 결과

- HEAD: `302f9e8f0d3cd5eba8f68f633d1a2ba9013396aa`
- solver Python aggregate SHA-256: `ec7fad6917ce977d1da598d332f0e9428f42236535753eeede7ad52270553fdc`
- production config SHA-256: `f18a0d3ee7a5f2c76ef59bad6f53c0144288b2134fc3252d59ac9dc477f5924a`
- full baseline tests: `204/204 PASS`
- `git diff --check`: PASS
- archive allowlist/integrity/size: PASS, 19 members, 100,851 bytes
- clean extraction normal smoke: feasible, Stage 5, stdout/stderr empty
- clean extraction Gurobi-absent smoke: feasible, Stage 5, stdout/stderr empty
- archive SHA-256: `bb519ad9c96bf8a4be96b2d5a1c9c4d96f5ea891e3b3ef51ba28fae659e357cb`

## 5. 판정과 한계

최종 판정은 **GO WITH DEADLINE WAIVER**다. 제출물의 correctness, fallback, silence, archive 무결성은 현재 source에서 직접 검증했다. 다섯 개 120초 성능 행의 feasible/Stage 5 판정은 사용자가 제공한 사실을 수용했으며, 이 repository에서 raw output이나 동일 source identity로 재실행하지 않았다. 따라서 이 판정은 기존 1,200-run 통계적 quality gate PASS를 의미하지 않는다.

마감 전 제출 대상으로 사용할 canonical file:

```text
dist/ogc2026_submission.zip
```

위 파일은 evidence copy인 `artifacts/ogc_sage/quality-recovery/p8-fast-20260716T015038Z-302f9e8f/archive/submission.zip`과 byte-for-byte 동일하다.
