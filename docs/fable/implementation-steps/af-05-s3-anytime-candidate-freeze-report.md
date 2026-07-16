# AF-05 S3 Anytime Candidate Freeze Report — Identity C

Date: 2026-07-16 (Asia/Seoul)

Status: `FROZEN_UNQUALIFIED`

AF-05 recovery passed the new repeatable Tier-S integration, package,
public-loader, deadline, checker, timing-accounting, full-logical-trace,
deterministic-schedule, cadence-prefix, and cleanup validation for recovered
source identity C. This report freezes identity C for a later AF-06 session.
It does not claim that Tier-Q ran or that identity C passed qualification.
Public/default `s3_anytime_fill` remains `false`; only the named
`s3-anytime-fill-candidate` profile selects `true`.

## Immutable identity C

- Candidate label: `C`
- Candidate identity:
  `25a66ff8dcc0827183e01066f83df881a3010e9359c2c9fa987835f6591d6143`
- Clean freeze parent and recovered source commit:
  `b3007dec946dc66cc63bfac4cede8034b7b212d9`
- Algorithm recovery commit:
  `224b3606fb6a2f9d0fd30ffa8ccb723692605032`
- AF-03 validation checkpoint:
  `115f2fc8c6c7fd5e2e77ccafc30dda3903070295`
- AF-04 contract/draft commit:
  `b3007dec946dc66cc63bfac4cede8034b7b212d9`
- Source state: clean (`dirty_diff_hash=clean`)
- `baseline/solver` tree:
  `d0cf28cb5c487815898bef2b24d6bed2f986790b`
- `baseline/myalgorithm.py` blob:
  `d296fc29c8824b090716cca30be64c360e286497`
- `baseline/harness/s3_anytime_worker.py` blob:
  `cb5e984ecca97b5b5c2c9f4c4a04eeaf833a5bb4`
- Required freeze commit message:
  `chore(anytime): freeze s3 deadline-fill candidate`
- Profile: `s3-anytime-fill-candidate`
- Seed/jobs: `20260710` / `1`
- Fixed policy: 8-second segment, 24 iterations per logical batch, `sa`,
  adaptive weights false, safety sample interval 8

The identity hash canonically binds label C, recovered source commit, clean
source state, passing Tier-S identity, qualification contract hash, package
hashes, seed/jobs, new Q run-id, and expected evidence directory. It differs
from consumed identity B. AF-06 must verify that its clean HEAD has this exact
freeze parent and commit message and that the frozen algorithm scope is
unchanged from the recovered source commit.

## Consumed identities A and B

Identity A and identity B are both immutable, consumed, ineligible for
selection, and prohibited from rerun or resume.

Identity A:

- Freeze commit: `5742bb108a67b98a0651344694a1aa24db736750`
- Q run-id: `20260716T033756Z-af05-01-q1`
- Q execution count: `1`
- Outcome: `GATE_FAILED_TIME`
- Failure class: time utilization; it is not a candidate C result
- Immutable Q evidence:
  `benchmarks/evidence/s3-anytime-fill/qualification/20260716T033756Z-af05-01-q1/`

Identity B:

- Candidate identity:
  `85da9be5aba2529986d6f8cb6ea76e211d5005398df8b888013febfee1260c3a`
- Freeze commit: `151d0e715ad3f4c965c7f71645b593e5739da6cb`
- Q run-id: `20260716T062856Z-af05-recovery-01-q1`
- Q execution count: `1`
- Safety/time/quality sub-gates: PASS
- Raw evaluator decision: `GATE_FAILED_SCALING`
- Authority disposition: `GATE_FAILED_TIME`
- Failure reason: `FULL_LOGICAL_TRACE_PREFIX_MISMATCH:prob_21|tl=300`
- Immutable Q evidence:
  `benchmarks/evidence/s3-anytime-fill/qualification/20260716T062856Z-af05-recovery-01-q1/`

Identity B artifact SHA-256 values remain:

- `records.jsonl`:
  `c89bc1ea5da5534a53d580d57c283dd87c7ef8d0d5704cc8bd3148fe7bc75f86`
- `qualification.json`:
  `453c443d0c5d3f797dc368d6f8566e413c9cb0c5de377b327ffe9ab312f753e7`
- `summary.json`:
  `0da64466cbcb1c2b8772b2cb861fbb22a3c01b757a990564eb0d4d4c6aab2cc0`
- `run.json`:
  `304abb286baf8780ba1c7bbf23103c6efc60c04460c2af727291b0636a9b3f5f`
- `command.txt`:
  `dbe46c986df0da190d8f8a85b7091057d0e4a4ee2848336a3b4ca6c2e738ddb0`
- `versions.json`:
  `9055d045fd1461eb54d43fc5b703861ddad901ac61d4ecca26f162bc411b472c`

Identity A and B evidence was read only. Identity C is a new source/package/Q
contract and does not reuse either consumed Q run-id.

## New Tier-S identity C result

The passing identity is
`20260716T083046Z-af05-recovery-identity-c-01-s1`, based on clean source
commit `b3007dec946dc66cc63bfac4cede8034b7b212d9`. No tracked source changed
during Tier-S.

| Step | Result | Evidence |
|---|---:|---|
| affected S3/checkpoint/entry/harness/package tests | 70/70 PASS | `tier-s-s1-01-affected.txt` |
| full unittest discovery | 125/125 PASS | `tier-s-s1-02-full-discovery.txt` |
| Algorithm Tester-style public loader parity | PASS, Stage 5 | `tier-s-s1-03-public-loader.txt` |
| package allowlist and isolated extraction | PASS, Stage 5 | `tier-s-s1-04-package.txt` |
| feature-off tracked example parity | PASS, Stage 5 | `tier-s-s1-05-feature-off.txt` |
| feature-on 5-second smoke | PASS, Stage 5 | `tier-s-s1-06-smoke-5s.txt` |
| feature-on 12-second smoke | PASS, Stage 5 | `tier-s-s1-07-smoke-12s.txt` |
| checker, hard deadline, timing union, full trace, cadence prefix | PASS | `tier-s-s1-08-checker-deadline-contract.txt` |
| remaining child/worker/Q processes | 0 | `tier-s-s1-09-processes.txt` |

Evidence root:
`benchmarks/evidence/s3-anytime-fill/af-05/20260716T083046Z-af05-recovery-identity-c-01/`

The 5-second smoke returned in `2.0016627080040053` seconds against the
2-second work deadline and 5-second hard deadline. It executed 1 operational
segment, 94 logical batches, and 2,252 extension iterations. Its complete
2,552-event anchor-plus-tail logical trace has SHA-256
`cd9afa1f6f471fa300080cc2bfbfbac2cf24d80d6a2c95503b603e449d18b011`.

The 12-second smoke returned in `9.00216245802585` seconds against the
9-second work deadline and 12-second hard deadline. It executed 2 operational
segments, 509 logical batches, and 12,214 extension iterations. Its complete
12,513-event trace has SHA-256
`e2b0271c77307817661cbff1e2991acd72d9bf662195aa0a41548c717e045033`.
The 12-second run has more operational segments, batches, and iterations than
the 5-second run.

Both records were independently re-summarized from contiguous,
non-overlapping `timing_intervals`. Useful time is only the union of actual
anchor/tail `candidate`, `repair`, `retime`, and `checker` work. `idle`,
`no_op`, `sleep`, `serialization`, and `finalization` are excluded. Reported
idle time was zero. Final solution SHA equals checker solution SHA and the
official checker stage is 5.

Every tail logical event obeys the deterministic schedule introduced by the
AF-02 recovery: seed `20260710 + 104729 * logical_segment_index` and profile
rotation `(1.0, continue)`, `(1.5, verified_restart)`,
`(2.0, same_bay_restart)`. Logical segment indices are contiguous and each
completed logical segment contains exactly 24 events. No wall operational
segment selects seed, restart policy, RNG, or logical profile.

The additional different-cadence fake-clock regression used 60 seconds at
cadence 0.125 and 300 seconds at cadence 0.0625. The short complete logical
trace is an exact prefix of the long trace. Operational counts scaled from
7/19/453 to 36/190/4569 (segments/batches/iterations), with the same parent
absolute deadline, anchor policy, public return schema, and Stage 5 result.

Feature-off public/default output matched explicit false byte-for-byte with
solution SHA-256
`50a8d7e6731fcbd7fbe9945e263e9a7c427eec64eb6803b0f093cf5fbf4f7cdc`.

## Package and runtime identity

- Archive SHA-256:
  `a67462c4763523dfb5b2b970a6d5244404e33594a23622d9f0497af5628e5633`
- Archive bytes / entries: `62558` / `20`
- Package manifest SHA-256:
  `0535870796d2dbbb4acb88c5e1d77dcf1a1872f7bb50323ecb236c7178678f04`
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

The hard-5 hashes/order, qualification profile/configuration, seed, jobs,
timelimits, and every frozen threshold remain unchanged.

## Frozen AF-06 identity C workload

Run strictly sequentially with seed `20260710` and jobs `1`:

1. `prob_21` through `prob_25`, each 120 seconds
2. `prob_21` at 60 seconds
3. `prob_21` at 300 seconds

The frozen qualification contract SHA-256 is
`8247ee30f665aa41e399d11d80456bdb6382493bf9e046695f44af4f1a7916de`.
All thresholds, data, order, configuration, required telemetry and evidence
fields, gate precedence, and decision codes are unchanged.

Exact command for the later AF-06 identity C session:

```text
/opt/homebrew/Caskroom/miniforge/base/envs/ogc-2026/bin/python -m baseline.harness.cli qualify-s3-anytime --profile s3-anytime-fill-candidate --manifest benchmarks/manifests/s3-anytime-fill-candidate.json --evidence-root benchmarks/evidence/s3-anytime-fill --run-id 20260716T083046Z-af05-recovery-identity-c-01-q1 --seed 20260710 --jobs 1
```

Expected evidence directory, confirmed absent at freeze time:

```text
benchmarks/evidence/s3-anytime-fill/qualification/20260716T083046Z-af05-recovery-identity-c-01-q1
```

At freeze time, `q_execution_count=0`,
`qualification.real_wall_clock_executed=false`, `result=null`, and
`decision=null`. Only a later AF-06 identity C session owns the one real
Tier-Q execution and decision.
