# AF-05 S3 Anytime Candidate Freeze Report

Date: 2026-07-16 (Asia/Seoul)

Status: `FROZEN_UNQUALIFIED`

AF-05 passed repeatable Tier-S integration, package, public-loader, deadline,
checker, and cleanup validation. This report freezes an AF-06 candidate; it
does not claim that Tier-Q ran or that the candidate passed qualification.
Public/default `s3_anytime_fill` remains `false`; only the named
`s3-anytime-fill-candidate` profile selects `true`.

## Immutable source and configuration

- Algorithm commit: `8b36b4d60e183e278ee3ca87ae77b93a9be216c7`
- Algorithm source state: clean (`dirty_diff_hash=clean`)
- `baseline/solver` tree: `aa191e4efa5f3dd2edcb84431363eaa8720c123e`
- `baseline/myalgorithm.py` blob: `d296fc29c8824b090716cca30be64c360e286497`
- AF-05 freeze parent: `8b36b4d60e183e278ee3ca87ae77b93a9be216c7`
- Required freeze commit message:
  `chore(anytime): freeze s3 deadline-fill candidate`
- Profile: `s3-anytime-fill-candidate`
- Seed/jobs: `20260710` / `1`
- Fixed policy: 8-second segment, 24 iterations per batch, `sa`, adaptive
  weights false, safety sample interval 8

The AF-06 command verifies clean HEAD, the exact parent/message relation, and
that `baseline/solver` plus `baseline/myalgorithm.py` are unchanged from the
algorithm commit.

## Tier-S result

The passing identity is `r3`. The `r1` and `r2` environment failures remain in
append-only evidence: the target worktree intentionally lacks untracked
training files, and the initial evidence runners did not bind both halves of
the AF-00-frozen stabilization corpus. No tracked source changed for that
recovery.

| Step | Result | Evidence |
|---|---:|---|
| affected S3/checkpoint/entry/harness/package tests | 62/62 PASS | `tier-s-r3-01-affected.txt` |
| full unittest discovery | 117/117 PASS | `tier-s-r3-02-full-discovery.txt` |
| Algorithm Tester-style public loader parity | PASS | `tier-s-03-public-loader.txt` |
| package allowlist and isolated extraction | PASS | `tier-s-04-package.txt` |
| feature-off tracked example parity | PASS | `tier-s-05-feature-off.txt` |
| feature-on 5-second smoke | PASS | `tier-s-06-smoke-5s.txt` |
| feature-on 12-second smoke | PASS | `tier-s-07-smoke-12s.txt` |
| official checker Stage 5 and hard deadline | PASS | `tier-s-08-checker-deadline.txt` |
| remaining child/worker processes | 0 | `tier-s-09-processes.txt` |

Evidence root:
`benchmarks/evidence/s3-anytime-fill/af-05/20260716T033756Z-af05-01/`

The 5-second smoke returned in `2.0014285419892985` seconds against the
2-second work deadline and 5-second hard deadline. It executed 1 segment, 120
batches, and 2,864 extension iterations, returning official-checker Stage 5.
The 12-second smoke returned in `9.001823708997108` seconds against the
9-second work deadline and 12-second hard deadline. It executed 2 segments,
631 batches, and 15,122 extension iterations, returning Stage 5. Neither run
left a process group alive.

Feature-off public/default output matched explicit false byte-for-byte with
solution SHA-256
`50a8d7e6731fcbd7fbe9945e263e9a7c427eec64eb6803b0f093cf5fbf4f7cdc`.

## Package and runtime identity

- Archive SHA-256:
  `2879ea06b07f6798fe95b82595c14799972d6964cc129d69eff9f6241a8a3372`
- Archive bytes / entries: `61972` / `20`
- Package manifest SHA-256:
  `5e5d7d37445c784ae215582b9b242017b205e906b2e591e20f6a52a4c78ea1e8`
- Import smoke / isolated extraction checker stage: `5` / `5`
- Isolated extraction solution SHA-256:
  `ecba5bb356f983ad3aafc402ec627240b0339518b280ef413b916ca2f9686752`
- Python: `/opt/homebrew/Caskroom/miniforge/base/envs/ogc-2026/bin/python`
  (`3.12.13`)
- Packages: gurobipy `13.0.2`, numpy `2.1.3`, OR-Tools `9.15.6755`,
  psutil `7.2.2`, shapely `2.1.2`

Protected SHA-256 values remain:

- `baseline/utils.py`:
  `d0347a3eafa14be68393638e9c35aab8d0618092bdc4f6d042a6d11bc6d06e75`
- `baseline/baseline_greedy.py`:
  `8ec2cc816b35b6507a9407bc9f893140a9d1b5e0892af92a2dbac2f91b32103b`

## Frozen AF-06 workload

Run strictly sequentially with seed `20260710` and jobs `1`:

1. `prob_21` through `prob_25`, each 120 seconds
2. `prob_21` at 60 seconds
3. `prob_21` at 300 seconds

The hard-5 SHA-256 values and order are frozen in
`benchmarks/manifests/s3-anytime-fill-candidate.json`. The AF-04 qualification
contract hash remains
`5d769ed2e1d7bc288084cbc1ef7b15097cbfd611711f68565c1bbff3f5ed76a2`;
all thresholds, telemetry fields, gates, reason codes, and decision precedence
are unchanged.

Exact command (one execution only for this identity):

```text
/opt/homebrew/Caskroom/miniforge/base/envs/ogc-2026/bin/python -m baseline.harness.cli qualify-s3-anytime --profile s3-anytime-fill-candidate --manifest benchmarks/manifests/s3-anytime-fill-candidate.json --evidence-root benchmarks/evidence/s3-anytime-fill --run-id 20260716T033756Z-af05-01-q1 --seed 20260710 --jobs 1
```

Expected evidence directory (not created by AF-05):

```text
benchmarks/evidence/s3-anytime-fill/qualification/20260716T033756Z-af05-01-q1
```

At freeze time, `qualification.real_wall_clock_executed=false`, `result=null`,
and `decision=null`. AF-06 owns the single real Tier-Q execution and decision.
