# S3 Anytime Candidate History and Identity D Draft

Date: 2026-07-16 (Asia/Seoul)

Status: `UNQUALIFIED_DRAFT`

AF-04 recovery-03 reset the candidate manifest after the algorithm recovery at
commit `127075d0400d3529fb20af7fd9fca0e31b23662c`. Identity D is only the next
label; no D identity, source identity, Tier-S result, package, Q command, Q
run-id, or expected Q evidence directory is frozen here. Public/default
`s3_anytime_fill` remains `false`; only the named
`s3-anytime-fill-candidate` profile selects `true`.

AF-05 recovery-identity-D must run a new Tier-S and package preflight from the
new algorithm HEAD, then freeze a new candidate identity D. It must not reuse
any A, B, or C source/package/Q identity.

## Consumed immutable identities

Identities A, B, and C are consumed, immutable, ineligible for selection, and
prohibited from rerun, resume, or repair.

| Identity | Candidate identity | Frozen Q run | Raw evaluator | Authority | First trace mismatch |
|---|---|---|---|---|---:|
| A | not assigned in schema v1 | `20260716T033756Z-af05-01-q1` | `GATE_FAILED_TIME` | `GATE_FAILED_TIME` | n/a |
| B | `85da9be5aba2529986d6f8cb6ea76e211d5005398df8b888013febfee1260c3a` | `20260716T062856Z-af05-recovery-01-q1` | `GATE_FAILED_SCALING` | `GATE_FAILED_TIME` | 710 |
| C | `25a66ff8dcc0827183e01066f83df881a3010e9359c2c9fa987835f6591d6143` | `20260716T083046Z-af05-recovery-identity-c-01-q1` | `GATE_FAILED_SCALING` | `GATE_FAILED_TIME` | 478 |

The B and C raw failures are
`FULL_LOGICAL_TRACE_PREFIX_MISMATCH:prob_21|tl=300`. The execution plan maps
the raw scaling taxonomy to the authority disposition `GATE_FAILED_TIME`.
Safety, useful-time/deadline, Stage 5, and quality sub-gates passed for both B
and C; exact full logical-prefix did not.

Immutable identity C Q evidence SHA-256 values are:

- `records.jsonl`: `98f147f1df39529fb5d72100c408f3cc36e04c29dbfb5f24fc2acd40cd7812af`
- `qualification.json`: `69718e3acf394087c36e0928bbc4244a4ed2f9929b292d16f4bc68723669e293`
- `summary.json`: `da9855a82541399cea087f4f6305ba1f1628a88aece51eaf31efd24267779378`
- `run.json`: `cfab831b68925553968cb7296a0697ae70a4264465dc353637bd3a15eb335ac2`
- `command.txt`: `3790e34bfd17373ce08cc4cd092a6e19ded6249fcb2ab7cdd6e4bcff58c69d54`
- `versions.json`: `9055d045fd1461eb54d43fc5b703861ddad901ac61d4ecca26f162bc411b472c`

The manifest retains the complete A/B/C candidate contracts, source identity
history, Tier-S history, package history, Q outcomes, and immutable Q hashes
under `superseded_identities`.

## AF-04 recovered-contract validation

Synthetic validation exercised the public `myalgorithm.algorithm` path through
the actual guarded-retime flow. The recovered 60/300 schedule was accepted by
the unchanged evaluator with an exact full logical prefix, Stage 5 at both
limits, strictly greater long work units, a non-worse long objective, the same
parent deadline, timing-interval union accounting, and publication isolated
from logical state.

The immutable B and C records were read only and independently rejected at
their exact first mismatches, indices 710 and 478. The regression matrix also
retained telemetry completeness, candidate-only profile selection, useful-time
exclusions, work-deadline semantics, scaling work units, long objective,
Stage 5, anchor/trace quality, and raw-to-authority taxonomy mapping.

No qualification contract, evaluator, source test, workload, threshold,
hard-5 input, protected source, or frozen Q evidence was changed. No discovery,
hard-5, real wall-clock Q, full qualification, AF-05, or later phase ran.

## Identity D draft contract

The current manifest is AF-04-owned `UNQUALIFIED_DRAFT`:

- candidate identity label and hash: `null`
- source identity: `null`
- Tier-S status: `NOT_RUN`
- package preflight status: `NOT_RUN`
- Q command/run-id/evidence directory/result/decision: `null`
- Q execution count: `0`
- real Q executed: `false`
- next candidate label: `D`
- prohibited labels: `A`, `B`, `C`
- next owner: `AF-05-recovery-identity-D`

The qualification contract SHA-256 remains
`8247ee30f665aa41e399d11d80456bdb6382493bf9e046695f44af4f1a7916de`.
Seed `20260710`, jobs `1`, hard-5 order/hashes, timelimits, profile,
configuration, telemetry fields, thresholds, workload, and gate precedence are
unchanged.

Protected SHA-256 values remain:

- `baseline/utils.py`: `d0347a3eafa14be68393638e9c35aab8d0618092bdc4f6d042a6d11bc6d06e75`
- `baseline/baseline_greedy.py`: `8ec2cc816b35b6507a9407bc9f893140a9d1b5e0892af92a2dbac2f91b32103b`
- `baseline/harness/s3_anytime_worker.py`: `a135e5e8c28f0a6fc18034df06269cee26bf0ca1c337c04c8136f49c20a7a7f2`
