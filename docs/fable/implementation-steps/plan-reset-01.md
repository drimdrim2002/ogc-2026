# PLAN-RESET-01 — Stable Core and Optional Optimization Split

Authoritative parent: [`../fable-native-implementation-progress.md`](../fable-native-implementation-progress.md)

Status: `READY`; implementation execution: `NOT_STARTED`; document decision date: 2026-07-15 (Asia/Seoul)

This reset replaces the linear rule that made S4 completion a prerequisite for all remaining work. It does not declare the current dirty S4 experiment complete, does not relax checker or deadline safety, and does not authorize a commit from the current worktree.

## 1. Why the plan is reset

The current plan cannot converge reliably for four independent reasons:

1. The mandatory path is coupled to optional optimization. S4 gain and worker-protocol recovery have blocked hardening and packaging for three days even though the original design treats S0-S3 as the core solver and S4-S6 optimization features as gated additions.
2. There is no clean, selected-default S3 baseline. S3 gate evidence passed, and commit `c0da4a7971c57b85066f2610ead9b68d6305fe65` contains the S3 implementation, but its committed `SolverConfig` still has `alns=false`. The gate evidence was produced from a dirty identity. A clean, reproducible S3 default must therefore be established before new feature work.
3. The legacy worktree holds a large mixed experiment. At reset audit time it is on branch `fable-native-implementation`, HEAD/upstream `388db7b27eda189e69140520548fc00362fba3f3`, with 15 tracked dirty paths, `+6178/-65`, and binary diff hash `7fc5be2e1bbb5cab5af9c4db1a7d18662cc4a684a9e587dd68a4b2ac154e2a12`. The dirty `config.py` contains unselected S4 flags set true even though the recorded selected defaults are false.
4. Frozen qualification rules were applied to ordinary development feedback. RED, focused GREEN, and affected regression must be repeatable after a code change. Exact-once applies only to an explicitly frozen qualification identity, not to development tests.

The terminal RECOVERY-08 result remains valid evidence: focused GREEN passed 8/8, affected regression passed 66/67, and the failure was caused by a 25-percent cap rejecting the measured 2.5-second return margin at about 8.92 seconds of work allowance. The worker was safely skipped but `assignment_seed_attempts` was absent. That result blocks promotion of the current S4 experiment; it does not block the stable-core path.

## 2. Binding delivery topology

```text
mandatory delivery track
S0 -> S1 -> S2 -> S3-stable -> S6-05 hardening/package -> S6-06 final rehearsal

optional promotion tracks
S3-stable -> S4 assignment refinement
chosen stable lower tier -> S5 process portfolio
S3-stable / enabled S4 / enabled S5 -> S6-01..S6-04 interlock
```

`S6-05` and `S6-06` keep their historical identifiers, but execution order is now governed by the diagram rather than numeric order. Mandatory hardening may run before optional S4, S5, or interlock work. A disabled optional feature is a valid product state.

The tracks use these decisions:

- Mandatory safety, feasibility, checker parity, incumbent integrity, deadline, cleanup, and packaging failures are blocking.
- Optional implementation safety must pass before the feature can be considered for promotion.
- An optional feature that is safe but fails its gain criterion ends `GATE_FAILED_DISABLED`; its flags remain false and mandatory delivery continues.
- An unsafe or incomplete optional experiment is `BLOCKED` within its own track, with the stable lower-tier fallback still eligible for hardening and packaging.
- S4, S5, and interlock enablement are independent product decisions. No optional gate may invalidate the last qualified lower tier.

## 3. Git preservation and execution boundary

The following boundary is mandatory for the next execution session.

### 3.1 Legacy worktree is preservation-only

`/Users/brown/workspace/ogc/fable-native-implementation` on `fable-native-implementation` is the source of the uncommitted recovery experiment. Until preservation is complete, do not switch branches, reset, restore, stash, stage, commit, cherry-pick, merge, rebase, clean, or push from it. Do not run solver, unittest, harness, benchmark, stress, A/B, gate, or rehearsal commands there.

Before any Git mutation, capture an append-only preservation manifest containing:

- local HEAD, upstream and merge base;
- `git status --short`, exact dirty path set, numstat, and binary diff hash;
- a binary patch of tracked changes and a separately hashed list/copy of untracked files, if any;
- hashes of `baseline/utils.py`, `baseline/baseline_greedy.py`, all prior immutable evidence named by the progress history, and this reset document;
- timestamp, command lines, destination paths, and a restore rehearsal result.

The patch and manifest belong under ignored evidence storage with a new run ID. Never overwrite RECOVERY-06, RECOVERY-07, or RECOVERY-08 evidence.

### 3.2 Stabilization uses a separate worktree

After preservation succeeds, create a sibling worktree on new branch `codex/fable-s3-stabilization` from exact commit `c0da4a7971c57b85066f2610ead9b68d6305fe65`. This document specifies the branch; it does not create it.

Only proven S3 integration/default hunks may enter that branch. S4 assignment refinement, cross-bay search, hard-isolated S4 workers, S4 telemetry, S4 harness gates, and RECOVERY-06..08 tests are excluded. The selected baseline must have S4/S5/interlock flags false and must make the intended S3 feature state explicit.

Qualify the stable baseline from a clean commit identity. If it passes, commit it independently as:

```text
fix(s3): establish verified default baseline
```

Mandatory hardening and packaging then use a separate branch or commit series based on that clean baseline. An optional S4 branch may be created later from the same baseline. Safe S4-01..03 work may be cherry-picked only after a hunk-by-hunk ownership and regression audit; never apply the whole legacy dirty diff.

### 3.3 Atomic Git rules

- One concern and one slice per commit; no stage-sized dirty accumulation.
- Before staging, generate an exact path allowlist from the selected slice and reject any path outside it.
- Record pre-stage and staged diff hashes. Review `git diff` and `git diff --cached` separately.
- Never mix plan-status edits, production code, tests, and generated evidence without the slice contract explicitly owning them.
- Optional flags remain false in the stable branch until frozen promotion qualification passes.
- Do not commit or push a blocked experiment merely to obtain a clean worktree. Preserve it, then reconstruct only reviewed pieces on the appropriate branch.

## 4. Repeatable development and frozen qualification

Evidence is divided into three tiers.

| Tier | Purpose | Typical commands | Rerun rule | Failure effect |
|---|---|---|---|---|
| D — development | RED/GREEN and local behavior feedback | targeted tests, affected regression, static checks | Repeat after a relevant code/test change with a new attempt ID | Fix within the same slice; no gate decision |
| S — safety/prequalification | broader safety, checker, sentinel, deadline, and cleanup proof | full regression, parity, stress, sentinels | Repeat after a relevant change with a new identity; retain all failed evidence | Blocks freezing while failing |
| Q — frozen qualification | final promotion or delivery decision for one immutable source/config/data identity | preregistered A/B, final gate, rehearsal | Exactly once per frozen identity | Stops the remaining Q chain for that identity; a source/config/test change creates a new identity |

Exact-once therefore means “once for this frozen qualification identity.” It never means that a failed development regression cannot be fixed and rerun. Every attempt records its source commit, dirty hash, tests/config, instance hashes, seed, command, and superseded attempt where applicable.

Before Tier Q, freeze the source commit, require a clean worktree, record the complete qualification manifest, and prohibit edits until the Q chain ends. A Q failure cannot be rerun unchanged; it may be addressed only by returning to Tier D with a new source identity.

## 5. S4 recovery disposition

The existing S4 gain policy remains a promotion policy: on preregistered `high-w23`, median total checker objective must be strictly lower and at least 5/10 cases must improve, with zero paired regression and all safety criteria green. It is no longer a mandatory-delivery prerequisite.

S4 outcomes are:

- safety failure: `BLOCKED`, S4 flags false, stable S3 continues to mandatory hardening;
- safety pass plus gain failure: `GATE_FAILED_DISABLED`, S4 flags false, stable S3 continues;
- safety and gain pass: `COMPLETE`, selected S4 flags may become true on the optional promotion branch.

The next S4 development attempt must separate two contracts that RECOVERY-08 conflated:

1. A deliberately insufficient work window safely skips the worker and emits explicit skip telemetry. It does not promise assignment attempt counters.
2. A sufficient work window launches the worker and must emit assignment attempt counters even when the prior S3 incumbent is returned.

Keep the measured 2.5-second publish margin until new measurement justifies changing it. The arbitrary all-or-nothing 25-percent cap is not a safety invariant. Worker launch should instead require independently measured minimum useful-work allowance plus publish/kill/parent-tail reserves. Changing this policy begins at Tier D and must pass Tier S before any new frozen gain qualification.

## 6. Reset exit criteria

PLAN-RESET-01 is executed, rather than merely documented, only when all of the following are true:

1. The legacy dirty experiment has a verified preservation manifest and restorable patch.
2. `codex/fable-s3-stabilization` exists in a separate worktree at the recorded base.
3. The stable branch contains no S4/S5/interlock behavior and has explicit selected defaults.
4. S3 regression, checker safety, deadline safety, and the S3 gate pass from one clean immutable identity.
5. The stable baseline is committed and the master history records its evidence.
6. Mandatory S6-05 hardening/package and S6-06 rehearsal can proceed without waiting for optional S4/S5/interlock.
7. Optional track states are recorded independently as `NOT_STARTED`, `BLOCKED`, `GATE_FAILED_DISABLED`, or `COMPLETE`.

The next authorized action is preservation-manifest creation and separate-worktree creation. No solver recovery work should resume in the legacy dirty worktree.
