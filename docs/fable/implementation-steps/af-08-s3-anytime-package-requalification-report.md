# AF-08 S3 Anytime Selected-Default Package Requalification

Date: 2026-07-16 (Asia/Seoul)

Decision: `COMPLETE`

Attempt `20260716T111823Z-af08-recovery-01` requalified the selected-default
submission package from clean source HEAD
`742d23560d6bac26abd0c28792f6a53549073957` on
`codex/fable-s3-anytime-fill`. Public loading selected
`s3_anytime_fill=true`; all newly executed package, stress, sentinel, and final
rehearsal outputs reached official checker Stage 5 with zero timeout, crash, or
process leak. AF-06 Q and hard-5 were not rerun, resumed, or repaired.

## Ten-step result

| Step | Fresh AF-08 result |
|---|---|
| 1. Full discovery | PASS, `132/132` tests with the verified transient 40-file overlay |
| 2. Syntax/import | PASS, all tracked `baseline/**/*.py` compiled; public module, solver entry, package runner, CLI, and qualification harness imported |
| 3. Immutable audit | PASS, protected objects match HEAD; identity D remains `CANDIDATE_PASS`; Q execution count remains exactly `1`; A/B/C remain consumed; public default is true |
| 4. Package build | PASS, deterministic archive built twice, 20 exact allowlisted members, zero path/content/prohibited-member violations, no overlay member |
| 5. Isolated public loader | PASS, actual packaged `myalgorithm.algorithm` selected `s3_anytime_fill=true`; official checker Stage 5 |
| 6. Example/stress | PASS, tracked example plus seven preregistered structural stress fixtures, `8/8` Stage 5 |
| 7. Max training size | PASS, 300 blocks, Stage 5, 2.167 s process wall, 248,070,144-byte observed peak RSS, no timeout/crash/leak |
| 8. Tight sentinel | PASS, 0.5 s and 2 s package rehearsal sentinels, `2/2` Stage 5 and hard-deadline tolerance met; this was not AF-06 Q |
| 9. Cleanup/reproducibility | PASS, process leaks/timeouts/crashes `0/0/0`; archive and manifest reproduced byte-for-byte; overlay absent |
| 10. Final rehearsal | PASS, isolated public entry selected true, Stage 5, output SHA `ecba5bb356f983ad3aafc402ec627240b0339518b280ef413b916ca2f9686752` |

The explicit isolated/public solver matrix produced `13/13` Stage-5 outputs:
one loader observation, eight example/stress records, one max-size record, two
deadline sentinels, and one final rehearsal. The two package-builder import
smokes also reached Stage 5.

## Package identity

- Archive:
  `benchmarks/evidence/s3-anytime-fill/af-08/20260716T111823Z-af08-recovery-01/package-first/submission.zip`
- Archive SHA-256:
  `eb5c9aada7629f7e2c8abdd4c65a54ec42816ab745da0e344ef252ef12161775`
- Archive size: `63,135` bytes.
- Manifest SHA-256:
  `1983cff4517fc88a8c9bfd282f635da89082294b1227568c259daf28fbc20941`
- Rebuilt archive and manifest hashes: exact match.
- Allowlist violations, prohibited members/text, overlay members: `0/0/0`.

Exact archive content:

```text
myalgorithm.py
solver/__init__.py
solver/alns.py
solver/assign.py
solver/budget.py
solver/checker_adapter.py
solver/config.py
solver/construct.py
solver/cpsat_backend.py
solver/entry.py
solver/exact.py
solver/geometry.py
solver/gurobi_backend.py
solver/incumbent.py
solver/instance.py
solver/retime.py
solver/serialize.py
solver/state.py
solver/trivial.py
solver/validate.py
```

## Immutable and overlay evidence

- Candidate manifest SHA-256:
  `d68588482a14d1187d120206c8d44bb326a19def71dc9af9d8597d3357a04e58`.
- Candidate identity D:
  `bb6fa80d93c50770906555b14abb8208060b5c9bac3252d34ccba0116054589c`.
- Qualification contract SHA-256:
  `8247ee30f665aa41e399d11d80456bdb6382493bf9e046695f44af4f1a7916de`.
- `baseline/utils.py` SHA-256:
  `d0347a3eafa14be68393638e9c35aab8d0618092bdc4f6d042a6d11bc6d06e75`.
- `baseline/baseline_greedy.py` SHA-256:
  `8ec2cc816b35b6507a9407bc9f893140a9d1b5e0892af92a2dbac2f91b32103b`.
- All six identity-D Q evidence hashes matched the immutable manifest.
- Before copy, canonical source files `40/40` matched
  `benchmarks/manifests/training.json`; after copy, target files `40/40`
  matched again and were Git ignored.
- After validation, only the attempt-created `data/train` and `data/train 2`
  paths were removed; both are absent and the canonical legacy source was read
  only.

## Environment and scope

- Python:
  `/opt/homebrew/Caskroom/miniforge/base/envs/ogc-2026/bin/python`, 3.12.13.
- Platform: `macOS-26.5.2-arm64-arm-64bit`.
- Seed: `20260710`; jobs/process execution was sequential.
- Evidence root:
  `benchmarks/evidence/s3-anytime-fill/af-08/20260716T111823Z-af08-recovery-01`.
- Production algorithm/config/default/parameters/thresholds/workload/gates,
  tests, candidate/qualified manifests, and Q evidence changed files: `0`.
- This report is the sole tracked AF-08 artifact. AF-09 was not started; push
  and PR were not performed.

AF-09 may start only in a separate new session after auditing this report
commit, the clean branch, the archive/report/evidence hashes, and the COMPLETE
handoff.
