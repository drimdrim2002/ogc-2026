# S3 Anytime Candidate Freeze — Identity D

Date: 2026-07-16 (Asia/Seoul)

Status: `FROZEN_UNQUALIFIED`

AF-05 recovery identity D froze a new clean candidate from source commit
`4f7dbf5cf7871ab40b9924ce4364e6c563425346` after a fresh Tier-S run. It does not claim that Tier-Q ran:
`q_execution_count=0`, `real_q_executed=false`, and the reserved evidence
directory `benchmarks/evidence/s3-anytime-fill/qualification/20260716T100018Z-af05-recovery-identity-d-01-q1` is absent.

## Identity D

- candidate identity: `bb6fa80d93c50770906555b14abb8208060b5c9bac3252d34ccba0116054589c`
- source identity / clean source commit: `4f7dbf5cf7871ab40b9924ce4364e6c563425346`
- Tier-S identity: `20260716T100018Z-af05-recovery-identity-d-01-s1`
- Tier-S evidence: `benchmarks/evidence/s3-anytime-fill/af-05/20260716T100018Z-af05-recovery-identity-d-01`
- package archive SHA-256: `4ff92990fb68c744426dc2f334a26e2e060884ce3ba1b0919e38e0696f6e9c2e`
- package manifest SHA-256: `67a561879785753bd5810aaf5828833b17ccf10f847239204edfe0b23514d907`
- qualification contract SHA-256: `8247ee30f665aa41e399d11d80456bdb6382493bf9e046695f44af4f1a7916de`
- Q run-id: `20260716T100018Z-af05-recovery-identity-d-01-q1`
- profile / seed / jobs: `s3-anytime-fill-candidate` / `20260710` / `1`

Fresh Tier-S passed affected `71/71`, full discovery `126/126`, public loader,
isolated package build/import/extraction, feature-off exact parity, 5-second and
12-second candidate-only smoke, official Stage 5, hard deadlines, non-overlapping
timing-union/useful accounting, actual guarded-retime execution under different
60/300 cadences with an exact full logical prefix, and zero process leaks.

Short metrics were 5s `1/94/2233`
and 12s `2/507/12153`
(segments/batches/iterations). The actual-retime synthetic ran Stage 5 at both
60s and 300s, with `8/15/350`
versus `36/44/1078`.

## Consumed immutable history

Identities A, B, and C remain consumed, immutable, unselectable, and prohibited
from rerun, resume, repair, or reuse. Their candidate/source/Tier-S/package/Q
history and all immutable Q hashes are preserved byte-for-byte under
`superseded_identities`. Identity C remains
`25a66ff8dcc0827183e01066f83df881a3010e9359c2c9fa987835f6591d6143`
with Q run `20260716T083046Z-af05-recovery-identity-c-01-q1`.

No source/test, qualification contract, workload, thresholds, hard-5 input, or
protected file changed. Public/default `s3_anytime_fill` remains false; only the
candidate profile enables it.

## Frozen AF-06 command — do not execute in AF-05

```text
/opt/homebrew/Caskroom/miniforge/base/envs/ogc-2026/bin/python -m baseline.harness.cli qualify-s3-anytime --profile s3-anytime-fill-candidate --manifest benchmarks/manifests/s3-anytime-fill-candidate.json --evidence-root benchmarks/evidence/s3-anytime-fill --run-id 20260716T100018Z-af05-recovery-identity-d-01-q1 --seed 20260710 --jobs 1
```

AF-06 identity D may execute that command exactly once only after verifying the
clean freeze commit, manifest/candidate identity, package hashes, hard-5 hashes,
protected hashes/OIDs, immutable A/B/C Q hashes, and absence of the expected D
Q directory. Automatic rerun/resume is forbidden.
