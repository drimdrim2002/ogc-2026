# Reusable Implementation-Slice Session Prompt

<!--
@prompt-name fable-implementation-slice
@purpose Execute exactly one planned implementation slice in a fresh Codex session.

@param slice_id
  required: true
  type: string
  pattern: ^S[0-6]-[0-9]{2}$
  example: S0-01
  meaning: The exact slice heading to execute from the stage document.

@param stage_doc
  required: true
  type: path
  example: docs/fable/implementation-steps/s0-foundation.md
  meaning: Absolute path, or a path relative to the target worktree, for the authoritative stage plan.

@derived commit_message
  source: The `Commit:` item inside the selected slice.
  fallback: If that item is absent, derive a concise conventional commit message from the completed diff.
  user_input_required: false

@fixed-context worktree=/Users/brown/workspace/ogc/fable-native-implementation
@fixed-context branch=fable-native-implementation
@fixed-context interpreter=/opt/homebrew/Caskroom/miniforge/base/envs/ogc-2026/bin/python

@stop-condition Stop after the selected slice is committed and pushed, or after its blocker is recorded.
@scope-rule Never begin another slice or a full-stage gate in the same session.
-->

## Invocation

In a new session, send only this instruction with the two parameters:

```text
Follow docs/fable/implementation-slice-session-prompt.md.

slice_id: S0-01
stage_doc: docs/fable/implementation-steps/s0-foundation.md
```

Replace the two example values. No commit-message parameter or pasted plan text is required.

## Reusable prompt

You are implementing one planned slice in the OGC 2026 repository.

Runtime parameters supplied by the user:

- `slice_id = {{slice_id}}`
- `stage_doc = {{stage_doc}}`

Treat these placeholders as the values supplied in the invocation message. Reject missing values. Normalize a relative `stage_doc` against `/Users/brown/workspace/ogc/fable-native-implementation`. Confirm that the document exists and contains exactly one heading for `slice_id`. Do not ask the user for information already specified in the repository plans.

### Fixed execution context

- Worktree: `/Users/brown/workspace/ogc/fable-native-implementation`
- Branch: `fable-native-implementation`
- Python: `/opt/homebrew/Caskroom/miniforge/base/envs/ogc-2026/bin/python`
- Master progress document: `docs/fable/fable-native-implementation-progress.md`
- Checker authority: `baseline/utils.py`
- Frozen reference: `baseline/baseline_greedy.py`

Use the explicit Python interpreter for every Python command. Never modify the checker or frozen reference.

### 1. Resolve the selected slice

Read completely, in this order:

1. `docs/fable/fable-native-implementation-progress.md`
2. The supplied `stage_doc`
3. The selected `slice_id` section and its stage-level dependency, gate, cleanup, rollback, evidence, and risk rules
4. Only the source files needed to execute that slice

Extract from the selected slice without asking the user:

- prerequisites and consumed artifacts;
- included and excluded scope;
- exact files and symbols;
- intended RED reason;
- minimum implementation behavior;
- targeted GREEN, regression, checker, benchmark, parity, or stress commands;
- evidence path and PASS/FAIL criteria;
- cleanup and rollback behavior;
- proposed commit message.

The stage document is the execution contract. If its slice-specific text conflicts with higher-priority checker behavior, stop and record the discrepancy instead of silently choosing a new design.

### 2. Preflight

Before editing:

1. Verify worktree path, branch, HEAD, upstream, and `git status --short --branch`.
2. Verify the selected slice's preceding slice and stage prerequisites from the master document and Git history.
3. Verify that every required earlier gate is recorded as passed. S6 may accept S5 `GATE_FAILED_DISABLED` exactly as the plans allow.
4. Verify that the project interpreter exists.
5. Inspect existing changes. Preserve unrelated user changes and never stage them. If they overlap the selected slice and cannot be separated safely, stop and report the blocker.
6. Run any read-only discovery required by the slice.
7. Update the master status/history as prescribed before implementation begins.

Do not start if a mandatory prerequisite is missing. Missing optional later-stage functionality is never a reason to add it early.

### 3. Execute failing-first

Perform only the selected slice:

1. Add the specified behavioral test first.
2. Run the exact targeted test and confirm RED for the intended behavioral reason.
3. A syntax error, broken fixture, wrong import path, missing unrelated dependency, or test-discovery failure is not valid RED. Correct the test setup and rerun until RED identifies the missing or incorrect selected behavior.
4. Record the exact RED command, exit code, intended failure, and evidence path in the progress history.
5. Implement the minimum behavior needed for the slice. Do not implement convenience work assigned to another slice.
6. Run the targeted test to GREEN.
7. Run the slice's specified regression tests.
8. Run its official-checker verification on the specified real or synthetic instance.
9. Run its benchmark, parity, A/B, or stress command when required.
10. Store structured evidence at the planned repository-local path.

Never skip, weaken, delete, or mark a test `xfail` merely to proceed. Never replace the official checker with an internal objective or feasibility calculation.

### 4. Decide the slice

Mark the slice complete only when all slice-level criteria pass:

- intended RED was demonstrated;
- targeted GREEN passed;
- required regressions passed;
- official checker verification passed;
- required structured evidence is complete;
- cleanup completed;
- the repository remains submission-ready.

If safety, checker parity, feasibility, incumbent integrity, timeout, process cleanup, or required evidence fails:

1. Do not weaken the criterion.
2. Do not begin the next slice.
3. Apply the planned rollback or preserve the last verified incumbent as specified.
4. Update the master status/history with the exact blocker and evidence.
5. Stop without creating a success commit from partial behavior.

An optional feature may end disabled only where the stage plan explicitly permits `GATE_FAILED_DISABLED`.

### 5. Commit and push autonomously

After a successful slice:

1. Update the master progress document after RED, GREEN, checker verification, measurement, and the final slice decision.
2. Clean child processes, solver environments, temporary files, generated packages, and partial evidence as required.
3. Inspect the complete diff and confirm that every changed file belongs to the selected slice.
4. Derive the commit message automatically:
   - first use the selected slice's `Commit:` value;
   - if missing, create a concise conventional commit message describing only the completed slice.
5. Stage only selected-slice files.
6. Create exactly one atomic implementation commit.
7. Push the current branch to its configured upstream; if none exists, use `git push -u origin fable-native-implementation`.
8. Verify local HEAD equals upstream HEAD and the worktree is clean.

Do not create a pull request unless the user separately requests one.

### 6. Stop boundary

Stop immediately after the selected slice is successfully committed and pushed, or after a blocker is fully recorded. Do not:

- begin the next slice;
- run or claim the full-stage gate unless the selected slice explicitly is that gate;
- opportunistically refactor unrelated code;
- claim implementation-stage completion from planning status.

### 7. Final response

Report concisely:

1. selected slice and outcome (`COMPLETE` or `BLOCKED`);
2. observable behavior implemented;
3. RED, GREEN, regression, checker, and measurement results;
4. structured evidence path;
5. changed-file summary;
6. commit SHA and push result, if successful;
7. exact final `git status --short --branch`;
8. next eligible slice, stated only for orientation and not started.

If blocked, replace commit/push details with the exact blocker, rollback state, and next action required.

## More invocation examples

```text
Follow docs/fable/implementation-slice-session-prompt.md.

slice_id: S1-03
stage_doc: docs/fable/implementation-steps/s1-constructor.md
```

```text
Follow docs/fable/implementation-slice-session-prompt.md.

slice_id: S4-02
stage_doc: /Users/brown/workspace/ogc/fable-native-implementation/docs/fable/implementation-steps/s4-assignment-refinement.md
```
