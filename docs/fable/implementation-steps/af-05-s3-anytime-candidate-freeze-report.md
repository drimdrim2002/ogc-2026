# AF-05 S3 Anytime Candidate Freeze Report — Identity B Recovery

Date: 2026-07-16 (Asia/Seoul)

Status: `FROZEN_UNQUALIFIED`

AF-05 recovery passed all repeatable Tier-S integration, package,
public-loader, deadline, checker, timing-accounting, full-logical-trace, and
cleanup validation for a new recovered-source identity. This report freezes
candidate identity B for a later AF-06 session. It does not claim that Tier-Q
ran or that identity B passed qualification. Public/default
`s3_anytime_fill` remains `false`; only the named
`s3-anytime-fill-candidate` profile selects `true`.

## Immutable identity B

- Candidate label: `B`
- Candidate identity:
  `85da9be5aba2529986d6f8cb6ea76e211d5005398df8b888013febfee1260c3a`
- Recovered source commit:
  `7809cad8df16a3fd0f7d88de7c9bbe0f2e6e2e8c`
- Recovered source state: clean (`dirty_diff_hash=clean`)
- Recovered source parent:
  `5742bb108a67b98a0651344694a1aa24db736750`
- AF-04 recovery commit message:
  `test(anytime): add s3 deadline-fill qualification contract`
- AF-04 recovery binary diff SHA-256:
  `139897ffd4cdc133e59f25c346ed26e77460e2c30970577f4be2532524e9a7f2`
- `baseline/solver` tree:
  `aa191e4efa5f3dd2edcb84431363eaa8720c123e`
- `baseline/myalgorithm.py` blob:
  `d296fc29c8824b090716cca30be64c360e286497`
- AF-05 freeze parent:
  `7809cad8df16a3fd0f7d88de7c9bbe0f2e6e2e8c`
- Required freeze commit message:
  `chore(anytime): freeze s3 deadline-fill candidate`
- Profile: `s3-anytime-fill-candidate`
- Seed/jobs: `20260710` / `1`
- Fixed policy: 8-second segment, 24 iterations per batch, `sa`, adaptive
  weights false, safety sample interval 8

The identity hash canonically binds label B, recovered source commit, clean
source state, passing Tier-S identity, qualification contract hash, package
hashes, seed/jobs, new Q run-id, and expected evidence directory. AF-06 must
also verify that its clean HEAD has the exact freeze parent and commit message
above, and that the frozen source scope is unchanged from the recovered source
commit.

## Identity A is consumed and immutable

The earlier candidate identity A is permanently superseded and is not eligible
for execution:

- AF-05 freeze commit:
  `5742bb108a67b98a0651344694a1aa24db736750`
- AF-05 attempt: `20260716T033756Z-af05-01`
- AF-06 attempt: `20260716T040048Z-af06-01`
- Q run-id: `20260716T033756Z-af05-01-q1`
- Q outcome: `GATE_FAILED_TIME`
- Q execution count: `1`
- Immutable Q evidence:
  `benchmarks/evidence/s3-anytime-fill/qualification/20260716T033756Z-af05-01-q1/`

Identity A must never be rerun, resumed, copied into a new result, modified,
deleted, or overwritten. Its six required artifact hashes match the recorded
preconditions. The immutable AF-06 results file contains a truncated command
SHA transcription; the authoritative actual `command.txt` SHA-256 is
`eaf5cf6802dd0dd504f5d6ba8fb075fb9d6750803208a0751999fc467ec6b256`.

## New Tier-S result

The passing identity is
`20260716T062856Z-af05-recovery-01-s1`, based on the clean recovered source
commit `7809cad8df16a3fd0f7d88de7c9bbe0f2e6e2e8c`. No tracked source changed
during Tier-S.

| Step | Result | Evidence |
|---|---:|---|
| affected S3/checkpoint/entry/harness/package tests | 68/68 PASS | `tier-s-s1-01-affected.txt` |
| full unittest discovery | 123/123 PASS | `tier-s-s1-02-full-discovery.txt` |
| Algorithm Tester-style public loader parity | PASS, Stage 5 | `tier-s-s1-03-public-loader.txt` |
| package allowlist and isolated extraction | PASS, Stage 5 | `tier-s-s1-04-package.txt` |
| feature-off tracked example parity | PASS, Stage 5 | `tier-s-s1-05-feature-off.txt` |
| feature-on 5-second smoke | PASS, Stage 5 | `tier-s-s1-06-smoke-5s.txt` |
| feature-on 12-second smoke | PASS, Stage 5 | `tier-s-s1-07-smoke-12s.txt` |
| official checker, hard deadline, timing union, full trace | PASS | `tier-s-s1-08-checker-deadline-contract.txt` |
| remaining child/worker/Q processes | 0 | `tier-s-s1-09-processes.txt` |

Evidence root:
`benchmarks/evidence/s3-anytime-fill/af-05/20260716T062856Z-af05-recovery-01/`

The 5-second smoke returned in `2.001525708998088` seconds against the
2-second work deadline and 5-second hard deadline. Its non-overlapping actual
anchor-plus-tail useful union was `2.001525708998088` seconds, with zero
idle/non-work time. It executed 1 segment, 110 batches, and 2,617 extension
iterations. Its complete 2,916-event logical trace contains anchor and tail,
has contiguous sequence indices, and matches SHA-256
`10217e88a9af50a3e0268a535bc0206772e053c709f3c74f49c7259b658e1d72`.

The 12-second smoke returned in `9.001590250001755` seconds against the
9-second work deadline and 12-second hard deadline. Its non-overlapping actual
anchor-plus-tail useful union was `9.001590250001755` seconds, with zero
idle/non-work time. It executed 2 segments, 585 batches, and 14,034 extension
iterations. Its complete 14,333-event logical trace contains anchor and tail
and matches SHA-256
`2179e44efa6b4c309983eacf9b63368c6998ac1d627d947063e676b7e59a2586`.

Both emitted records were independently re-summarized from their contiguous,
non-overlapping `timing_intervals`; useful time includes only
candidate/repair/retime/checker work. Idle, no-op, sleep, serialization, and
finalization are excluded. Final solution SHA equals checker solution SHA,
official checker stage is 5, and no child/process leak remains.

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
- `baseline/harness/s3_anytime_worker.py`:
  `a135e5e8c28f0a6fc18034df06269cee26bf0ca1c337c04c8136f49c20a7a7f2`

## Frozen AF-06 identity B workload

Run strictly sequentially with seed `20260710` and jobs `1`:

1. `prob_21` through `prob_25`, each 120 seconds
2. `prob_21` at 60 seconds
3. `prob_21` at 300 seconds

The hard-5 SHA-256 values and order are frozen in
`benchmarks/manifests/s3-anytime-fill-candidate.json`. The recovered AF-04
qualification contract hash is
`8247ee30f665aa41e399d11d80456bdb6382493bf9e046695f44af4f1a7916de`;
all thresholds, seed, jobs, data, order, configuration, required telemetry and
evidence fields, gate precedence, and decision codes are unchanged.

Exact command for the later AF-06 identity B session:

```text
/opt/homebrew/Caskroom/miniforge/base/envs/ogc-2026/bin/python -m baseline.harness.cli qualify-s3-anytime --profile s3-anytime-fill-candidate --manifest benchmarks/manifests/s3-anytime-fill-candidate.json --evidence-root benchmarks/evidence/s3-anytime-fill --run-id 20260716T062856Z-af05-recovery-01-q1 --seed 20260710 --jobs 1
```

Expected evidence directory, confirmed absent at freeze time:

```text
benchmarks/evidence/s3-anytime-fill/qualification/20260716T062856Z-af05-recovery-01-q1
```

At freeze time, `q_execution_count=0`,
`qualification.real_wall_clock_executed=false`, `result=null`, and
`decision=null`. Only a later AF-06 identity B session owns the one real Tier-Q
execution and decision.
