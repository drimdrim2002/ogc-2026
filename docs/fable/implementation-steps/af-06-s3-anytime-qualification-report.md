# AF-06 S3 Anytime Deadline-Fill Qualification Report

Date: 2026-07-16 (Asia/Seoul)
Decision: `CANDIDATE_PASS`

Identity D completed the frozen AF-06 real wall-clock qualification exactly
once. The command exited `0`, the run completed without interruption, all seven
records passed the official checker at Stage 5, and the safety, time, scaling,
and quality gates all passed. No AF-07 action was executed.

## Frozen identity

- Candidate identity label: `D`.
- Candidate identity: `bb6fa80d93c50770906555b14abb8208060b5c9bac3252d34ccba0116054589c`.
- Frozen HEAD: `ce550f2a2612299e1197e4253e7d6650198fe77c`.
- Frozen parent/source commit: `4f7dbf5cf7871ab40b9924ce4364e6c563425346`.
- Frozen HEAD subject: `chore(anytime): freeze s3 deadline-fill candidate`.
- Manifest SHA-256 at Q start: `364b6c2f33eb155d9db9a4a48a327797bb294c87f8cfa6579af3be2a4455b79f`.
- Qualification contract SHA-256: `8247ee30f665aa41e399d11d80456bdb6382493bf9e046695f44af4f1a7916de`.
- Package archive SHA-256: `4ff92990fb68c744426dc2f334a26e2e060884ce3ba1b0919e38e0696f6e9c2e`.
- Package manifest SHA-256: `67a561879785753bd5810aaf5828833b17ccf10f847239204edfe0b23514d907`.
- Seed: `20260710`; jobs: `1`; profile: `s3-anytime-fill-candidate`.
- Q run ID: `20260716T100018Z-af05-recovery-identity-d-01-q1`.
- Q execution count: `1`; identity D is consumed and cannot be rerun, resumed,
  repaired, or reused.

The exact command was:

```text
/opt/homebrew/Caskroom/miniforge/base/envs/ogc-2026/bin/python -m baseline.harness.cli qualify-s3-anytime --profile s3-anytime-fill-candidate --manifest benchmarks/manifests/s3-anytime-fill-candidate.json --evidence-root benchmarks/evidence/s3-anytime-fill --run-id 20260716T100018Z-af05-recovery-identity-d-01-q1 --seed 20260710 --jobs 1
```

## Hard-5 results

| Record | Anchor objective | Final objective | Relative delta | Useful seconds | Segments / batches / iterations | Checker |
|---|---:|---:|---:|---:|---:|---:|
| `prob_21|tl=120` | 62,068,931.013805 | 59,735,656.013805 | -3.7592% | 114.032057 | 13 / 90 / 2169 | Stage 5 |
| `prob_22|tl=120` | 30,525,847.690805 | 29,432,541.690805 | -3.5816% | 114.024728 | 13 / 107 / 2578 | Stage 5 |
| `prob_23|tl=120` | 54,366,957.679121 | 51,153,474.679121 | -5.9107% | 114.034975 | 12 / 79 / 1895 | Stage 5 |
| `prob_24|tl=120` | 36,834,864.889391 | 35,861,555.889391 | -2.6424% | 114.035168 | 13 / 93 / 2217 | Stage 5 |
| `prob_25|tl=120` | 3,235,896.978273 | 2,855,039.978273 | -11.7698% | 114.023539 | 13 / 78 / 1874 | Stage 5 |

All five records exceeded the 102.6-second useful-work minimum, terminated for
`work_deadline`, returned within the hard-deadline contract, had zero idle
seconds and zero S3 fill faults, preserved nonincreasing incumbent traces, and
matched final solution SHA-256 with the checker. All five strictly improved
over their same-run anchors. The paired median relative delta was
`-0.03759167367456439`, and the strict late-improvement count was `5`.

## Scaling pair

| Record | Objective | Segments | Batches | Iterations | Logical events |
|---|---:|---:|---:|---:|---:|
| `prob_21|tl=60` | 61,402,281.013805 | 6 | 39 | 937 | 1231 |
| `prob_21|tl=300` | 59,735,656.013805 | 34 | 244 | 5876 | 6146 |

The complete 60-second logical trace is an exact prefix of the 300-second
trace. The 300-second run performed strictly more segments, batches, and
iterations, and its objective was not worse.

## Gate decision

- Safety: PASS — 7/7 Stage 5, 7/7 anchor nonregression, 7/7 nonincreasing
  traces, 7/7 final/checker SHA match, and zero crash, timeout, signal, worker
  exception, unverified checkpoint, or process leak.
- Time: PASS — all records terminated at `work_deadline`; timing intervals were
  contiguous non-overlapping useful unions; all process wall times remained
  within hard deadlines.
- Scaling: PASS — exact logical prefix, greater long-run work units, and
  non-worse long-run objective.
- Quality: PASS — hard-5 anchor nonregression and strict improvement 5/5,
  five late improvements, paired median relative delta below zero, and no loss.
- Raw evaluator decision: `CANDIDATE_PASS`.
- Authority decision: `CANDIDATE_PASS`.

## Immutable evidence

Evidence directory:
`benchmarks/evidence/s3-anytime-fill/qualification/20260716T100018Z-af05-recovery-identity-d-01-q1`

- `records.jsonl`: `4c394b163ca8620cdd914ef7680e2b70a1df1da22cbd1ad72fc508c3c978d464`
- `qualification.json`: `df329522ecd8dc4d61f3b4cd0d7074c5b7005b4c776258c0d2af170a9faa160c`
- `summary.json`: `483b88c89aae4f0262cb2335dbaabd73b88eafd1519080582a246906779d020b`
- `run.json`: `e5977541a078fb195725b8c25d3a237f158295d52941a85bfd179c869d52b672`
- `command.txt`: `1df8f10e8d9a48ddb072efe98d4ae063d458b84864f5ecf6cfd76818d16fa077`
- `versions.json`: `9055d045fd1461eb54d43fc5b703861ddad901ac61d4ecca26f162bc411b472c`

Identity A, B, and C remain consumed and immutable; their recorded Q hashes
were rechecked unchanged before and after identity D. Protected files, source
OIDs, hard-5 inputs, package hashes, profile, seed, workload, thresholds, and
gate contract also remained unchanged. The Q evidence directory was not
repaired or modified after completion.
