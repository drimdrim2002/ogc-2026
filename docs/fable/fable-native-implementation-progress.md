# Fable Native Solver: S0-S6 Progress and Execution Contract

Last updated: 2026-07-12 (Asia/Seoul)

Planning baseline: `78ef82ece960ac686a6f5c41497a13c2217ffab5`

Implementation branch/worktree: `fable-native-implementation` at `/Users/brown/workspace/ogc/fable-native-implementation`

Planning status: complete; implementation status: S0 in progress

## 1. Objective, non-objectives, and submission-ready definition

The objective is a checker-authoritative, anytime solver that always protects a fully verified feasible incumbent and then improves assignment, packing, and timing through S0-S6. This document is the mutable status board, append-only implementation history, architecture contract, evidence index, and restart point. The seven stage plans under [`implementation-steps/`](implementation-steps/) are the execution specifications.

This plan does not authorize changing `baseline/utils.py` or `baseline/baseline_greedy.py`, weakening a checker/safety gate, using approximate geometry as an oracle, hard-coding hidden-instance behavior, or enabling an optional feature without its A/B gate. Planning completion is not implementation completion.

“Submission-ready” means all of the following at the current commit:

1. `baseline/myalgorithm.py` has the required `algorithm(prob_info, timelimit)` signature and returns only an operations dictionary previously accepted by the unmodified `baseline/utils.py::check_feasibility`.
2. For every valid instance (defined below), the current mandatory pipeline has a verified-incumbent fallback under deadline, backend exception, candidate rejection, and optional-feature failure.
3. The mandatory gate through the latest completed stage is green; optional S5 portfolio or S6 interlock may be disabled after a failed gain gate without invalidating the lower-tier solver.
4. The exact interpreter, source commit, dirty state, instance hashes, seed, command, feature flags, checker result, and timings are recorded in complete evidence.
5. The packaging rehearsal produces a root-level `myalgorithm.py`, only relative runtime paths, no modified checker/reference file, no local data or credentials, no prohibited extension, and a zip no larger than 15 MB.

A **valid instance** has the checker-required schema and at least one in-bound orientation in at least one bay for every block. The checker and specification do not state that arbitrary malformed or physically impossible inputs must admit a solution. S0 preflight must prove this condition for every official training input. If it fails, the harness exits with input error and names the block; code must not claim that T0 can solve an impossible instance.

## 2. Authority and immutable invariants

Authority, highest first:

1. `baseline/utils.py` actual behavior.
2. [`../OGC2026_Problem_Analysis.md`](../OGC2026_Problem_Analysis.md).
3. Checker-semantic rules in [`solver-design-en.md`](solver-design-en.md) §2.
4. The remaining design and roadmap in `solver-design-en.md`.
5. [`solver-implementation-plan.md`](solver-implementation-plan.md), treated as a draft.
6. Historical/deprecated documents, used only for instance-set and experiment conventions.

Safety and semantic invariants:

- Integer placement coordinates and dates are emitted; occupancy is half-open `[a,e)`, with `dwell=max(P,1)` in non-interlock mode.
- Same-day operations are all EXIT before all ENTRY. Within-type order is semantically live in checker Stage 5.
- Boundary contact and polygon contact of zero area are legal. Shapely behavior through the checker is the final geometric oracle.
- Only a checker-feasible serialized solution can enter `Incumbent`; replacement is atomic and strictly improves checker objective within relative tolerance `1e-9`.
- Internal objective and targeted validation are diagnostics/filters until parity-proved. Every incumbent replacement still receives a full official check.
- A safety, checker-parity, infeasibility, or incumbent-safety failure blocks the next mandatory stage. Criteria are never relaxed to continue.
- S5 gain failure disables portfolio and records `GATE_FAILED_DISABLED`; it does not invalidate the S4 single-process solver. S6 interlock gain failure disables interlock; mandatory hardening and packaging may still complete.
- Completion of Sn provides every artifact needed by Sn+1. No stage gate consumes code or instrumentation owned by a later stage.

Precise checker anchors used by all stage plans include `Bay.contains_block` at `baseline/utils.py:252-266`; equal-layer collision at `461-543`; entry OBS `603-724`; exit OBS `727-819`; one ENTRY/EXIT and timing at `1028-1136`; coordinate rounding and overlap at `1144-1158`; entry present-set at `1160-1198`; exit present-set at `1200-1232`; collision/boundary at `1234-1277`; ordered replay at `1279-1386`; and float Z1/Z2/Z3 at `1388-1421`.

## 3. Repository assessment

At planning baseline the repository contains the checker, reference greedy solver, a one-line `baseline/myalgorithm.py` delegation, tester UI, one tracked 10-block example, design documents, and environment definition. It has no solver package, automated tests, in-house CLI harness, committed training data, evidence store, submission builder, or S0-S6 implementation. Historical documents describe 40 training cases and stable `smoke-3`/`dev-10` IDs, but the ignored local data is absent in this clean target. The required interpreter exists at `/opt/homebrew/Caskroom/miniforge/base/envs/ogc-2026/bin/python` and reports Python 3.12.13, numpy 2.1.3, shapely 2.1.2, OR-Tools 9.15.6755, gurobipy 13.0.2, and psutil 7.2.2. `pytest` is not installed, so the plan uses standard-library `unittest` and never relies on an ambiguous `python` executable.

Reusable assets:

- `baseline/utils.py`: oracle and usable checker primitives.
- `baseline/baseline_greedy.py`: frozen comparison/reference and emergency behavioral study only.
- `baseline/run_myalgorithm.py`: local manual diagnostic; the new harness supersedes it without deleting it.
- `alg_tester/example/example_B2_b10.json`: tracked smoke/example case.
- `docs/deprecated/operating-plan.md:37-66`: measured `dev-10` and `smoke-3` membership.
- `ogc2026_env.yml`: server-compatible dependency contract.

Prerequisites before S0 implementation: restore the official 40 training JSON files under either `data/train*/` or `alg_tester/example/train*/` without committing them; verify exactly one byte identity per `prob_1`…`prob_40`; and keep the explicit project interpreter executable. Missing training data blocks the S0 training gate, but not the first S0 unit slices.

## 4. Final file map and separation of concerns

```text
baseline/
  myalgorithm.py                 # submission entry, thin safety shell
  solver/                        # production only
    __init__.py config.py instance.py geometry.py state.py checker_adapter.py
    validate.py serialize.py trivial.py budget.py incumbent.py entry.py
    assign.py construct.py exact.py gurobi_backend.py cpsat_backend.py
    retime.py alns.py interlock.py portfolio.py
  tests/                         # unittest; never packaged
    __init__.py fixtures.py test_contract_semantics.py
    test_geometry_kernel.py test_state_parity.py test_validate_parity.py
    test_serialize.py test_trivial.py test_budget_entry.py
    test_harness_schema.py test_harness_process.py test_assign_v1.py test_construct.py
    test_exact_backends.py test_retime.py test_alns.py
    test_assignment_refinement.py test_portfolio.py test_interlock.py
    test_packaging.py
  harness/                       # development-only in-house harness
    __init__.py cli.py schema.py selectors.py runner.py checker.py
    compare.py gates.py report.py process.py package.py
benchmarks/
  manifests/                    # committed selectors and synthetic/stress recipes
    training.json smoke-3.json dev-10.json synthetic.json dense.json stress.json
  evidence/                     # generated, ignored except README; never packaged
    <stage>/<slice>/<run_id>/{run.json,records.jsonl,summary.json,
                              failures.jsonl,command.txt,versions.json,COMPLETE}
  baselines/                    # committed small gate summaries, not raw data
submission-dist/                # generated and ignored
.gitignore                      # ignores raw evidence, local data, temp/package output
docs/fable/fable-native-implementation-progress.md
docs/fable/implementation-steps/s0-foundation.md ... s6-interlock-hardening.md
```

Production code never imports `tests`, `harness`, `benchmarks`, absolute paths, or a local checker copy. `solver/checker_adapter.py` is the only production module that imports the runtime `utils` checker and normalizes its result without duplicating it; `solver/validate.py` owns parity-proved targeted validation and calls the adapter only for full checks. Tests own fixtures. Harness owns subprocess timeouts, selection, result schemas, comparison, and gate evaluation. Manifests are code-reviewed inputs. Evidence is append-only generated output; compact baselines may be committed deliberately. `SolverConfig` owns all feature flags and thresholds. Packaging includes only `myalgorithm.py` and `solver/**.py` unless an explicitly audited dependency is required.

Canonical module names are `construct.py` and `alns.py`. This resolves the design appendix’s `constructor.py`/`lns.py` suggestions in favor of the more specific implementation draft and avoids duplicate aliases.

## 5. Harness contract

All future commands run from the target repository root and use:

```bash
PY=/opt/homebrew/Caskroom/miniforge/base/envs/ogc-2026/bin/python
$PY -m baseline.harness.cli <subcommand> ...
```

Global options are `--evidence-root benchmarks/evidence` (default), `--run-id auto` (UTC timestamp plus eight-character nonce), `--seed 20260710`, `--jobs 1`, `--feature NAME=VALUE` (repeatable), and `--resume RUN_ID`. `--resume` runs only records without a terminal record. A record identity is SHA-256 of commit, dirty diff hash, instance SHA, solver, timelimit, seed, and sorted feature flags. The default deduplicates completed identities; `--rerun` creates a new attempt linked by `supersedes`.

Selectors are deterministic. `example` is the tracked example. `training` requires `prob_1`…`prob_40`, resolving each ID in order from `data/train/prob_N.json`, `data/train 2/prob_N.json`, `alg_tester/example/train/prob_N.json`, or `alg_tester/example/train 2/prob_N.json`; multiple different hashes for an ID are an input error. `smoke-3={prob_21,prob_32,prob_9}`. `dev-10={prob_4,prob_8,prob_9,prob_13,prob_20,prob_21,prob_32,prob_36,prob_18,prob_40}`. `dense` is selected before looking at outcomes as the top quartile of `area*dwell/(bay-area*horizon)` with at least four layers breaking ties; `high-w23` is the top quartile of `(w2+w3)/max(w1,1)` with at least two bays. Synthetic and stress manifests generate deterministic fixtures and store their JSON plus SHA in the evidence directory.

Subcommands:

| Command | Required/optional inputs | Output and PASS |
|---|---|---|
| `contract` | `--suite semantic|preflight|schema`, `--instances` | Unit/oracle rows; PASS iff all expected checker decisions match and preflight finds no invalid official input. |
| `parity` | `--kind objective|targeted|geometry`, `--cases`, selector | Paired internal/checker decisions; PASS iff zero mismatch (objective relative error ≤1e-6). |
| `benchmark` | `--stage`, selector, `--timelimits`, `--seeds`, flags | One JSONL record per case; PASS iff every run terminates, checker is feasible, and requested structural counters exist. |
| `stress` | `--stage`, `--instances stress`, matrix | Fault/tiny-budget/resource cases; PASS iff expected fallback occurs and every valid case is checker-feasible. |
| `ab` | selector, same seed/time, `--feature`, `--a`, `--b` | Paired records plus deltas/noise band; PASS rules are supplied by the stage gate, never inferred after results. |
| `gate` | `--stage sN --latest-complete --commit HEAD` | Re-evaluates immutable evidence and writes `gate.json`; 0 only when all mandatory criteria pass. |
| `report` | `--stage`, run IDs or `--latest-complete` | Markdown/JSON summary with failures and provenance; reporting does not change gate status. |
| `submission-rehearsal` | selector, matrix, `--isolated` | Builds, inspects, extracts, imports, runs, checks, and cleans a package; PASS iff all S6 packaging rules and checker cases pass. |

Exit codes are fixed: `0` PASS; `2` measured gate/non-regression failure; `3` checker infeasibility or missing incumbent; `4` invalid config/input/manifest; `5` timeout, crash, leak, or unhandled exception; `6` semantic/parity/incumbent-safety violation; `130` interrupted. `run.json` is written before execution with `started=true`; `COMPLETE` is atomically created only after all requested records and summaries are fsynced. Thus an empty or partial directory cannot prove a command ran.

Every benchmark record contains: schema version; run/record IDs; timestamp and timezone; branch, commit, dirty boolean and diff hash; interpreter path; Python/dependency versions; exact argv and cwd; feature flags; seed; instance ID/path/SHA and selector; timelimit; wall seconds; subprocess exit/signal; checker feasible/stage/violations/objective and Z1/Z2/Z3; per-stage timing; backend/name/status/bound/gap/build/solve/first-solution/fallback; iteration/proposal/accepted-improving/accepted-worsening/rejected counts; cache hits/misses/evictions and exact-predicate counts; timeout/crash/exception details; incumbent verification count; and fallback tier/reason. An example failure record is `{"instance_id":"prob_9","status":"checker_failed","checker":{"feasible":false,"stage":5},"failure_stage":"serialize","exit_code":3,"complete":true}`.

Timeout handling uses a fresh process group per solver run, `SIGTERM`, a two-second grace, then `SIGKILL`; both events are recorded. Cleanup asserts no child remains. A/B executes A then B and B then A over paired seeds to expose warm-cache/order effects. `gate` compares the current stage with the last complete preceding-stage evidence at the same selector/timelimit/seed, never with an unrelated historical run.

## 6. Dependency graph and stage table

```text
S0 foundation
  -> S1 constructor
    -> S2 exact retiming
      -> S3 intra-bay LNS
        -> S4 cross-bay assignment refinement
          -> S5 optional process portfolio (may finish disabled)
            -> S6 optional interlock + mandatory hardening/package
```

| Stage | Status | Active slice | Gate | Flag / initial default | Prerequisite | Last evidence | Last implementation commit | Blocker/fallback | Next action |
|---|---|---|---|---|---|---|---|---|---|
| S0 | `IN_PROGRESS` | `S0-05` | `NOT_RUN` | `native_solver=false` | planning commit, data preflight | `benchmarks/evidence/s0/s0-05/20260712T123100Z-s0-05/` | `3b18b8ac58aabdc17f330a1c09eac2db3b65ab4f` | training JSON absent; does not block early unit slices | commit and push S0-05; S0-06 is next after the atomic commit |
| S1 | `NOT_STARTED` | — | `NOT_RUN` | `constructor=false` | S0 mandatory gate | — | — | — | wait for S0 |
| S2 | `NOT_STARTED` | — | `NOT_RUN` | `exact_retime=false` | S1 mandatory gate | — | — | — | wait for S1 |
| S3 | `NOT_STARTED` | — | `NOT_RUN` | `alns=false`; acceptor `strict` | S2 mandatory gate | — | — | — | wait for S2 |
| S4 | `NOT_STARTED` | — | `NOT_RUN` | `assignment_refinement=false` | S3 mandatory gate | — | — | — | wait for S3 |
| S5 | `NOT_STARTED` | — | `NOT_RUN` | `parallel_portfolio=false` | S4 mandatory gate | — | — | optional failure keeps S4 | wait for S4 |
| S6 | `NOT_STARTED` | — | `NOT_RUN` | `interlock=false` | S5 `COMPLETE` or `GATE_FAILED_DISABLED` | — | — | interlock failure keeps hardened S5/S4 tier | wait for S5 |

Allowed implementation statuses are exactly `NOT_STARTED`, `IN_PROGRESS`, `BLOCKED`, `GATE_FAILED_DISABLED`, and `COMPLETE`. Transition rules are deterministic:

- `NOT_STARTED -> IN_PROGRESS` only after prerequisites are verified and the “before starting” history row is appended.
- `IN_PROGRESS -> COMPLETE` only after all mandatory slices are committed and the stage gate exits 0.
- `IN_PROGRESS -> BLOCKED` only for an unresolved mandatory safety/parity/feasibility condition; record exact failing evidence and do not start the next stage.
- S5 `IN_PROGRESS -> GATE_FAILED_DISABLED` when safety passes but the optional wall-clock/license gain gate fails; S6 then may start.
- S6 records interlock feature state `GATE_FAILED_DISABLED` independently; the stage becomes `COMPLETE` if mandatory hardening/package passes with interlock off.
- `BLOCKED -> IN_PROGRESS` requires a new history row naming the resolved condition and evidence. `COMPLETE` is not reopened; corrections start a new slice and temporarily return the stage to `IN_PROGRESS` with the prior completion retained in history.

## 7. Critical-review decision ledger

`ADOPT` means carry forward; `ADOPT_WITH_MODIFICATIONS` means the resolution below is binding; `DEFER_PENDING_MEASUREMENT` means the named earlier gate chooses without user input; `REJECT` means do not implement.

| Draft decision area | Class | Binding resolution and higher authority |
|---|---|---|
| Checker is oracle; full check before incumbent | `ADOPT` | Required by design §1/§4.1 and checker stages `917-1421`. |
| Placement/state with incremental Z1/Z3/load and O(m) Z2 | `ADOPT_WITH_MODIFICATIONS` | Internal values are diagnostics until S0 objective parity; incumbent stores checker values. |
| Targeted revalidation assigned to constructor/S1 while parity is an S0 gate | `ADOPT_WITH_MODIFICATIONS` | Minimal `validate.py::{validate_unary,validate_pair,validate_changed,validate_insertion}` and its 1,000-case parity belong to S0. S1 only consumes/extends candidate generation. This removes the backward dependency. |
| Geometry exact-answer cache and AABB filter | `ADOPT` | Consistent with checker area semantics and design §1/§4.2; no custom approximate verdict. |
| Cache cap 2^20 vs design 2^21 | `DEFER_PENDING_MEASUREMENT` | S0 starts at 2^18 and benchmarks 2^16…2^21. Select the largest power of two with cache RSS ≤256 MiB and total process peak ≤2 GiB, unless the next smaller cap has ≥99% of its hit rate; record selection. |
| Shape key rounded to 1e-4 | `REJECT` | Distinct checker geometries may collapse. Key canonical exact input vertex tuples plus orientation; only identical tuples share answers. |
| `slack=D-R-P` | `ADOPT_WITH_MODIFICATIONS` | Use `D-R-dwell` because `P=0` needs one day under checker replay (`1291-1382`). |
| K=32, escalation 64 vs design 16→48 | `DEFER_PENDING_MEASUREMENT` | Working S1 values are 32 then 64; evaluate {16,32,48,64}. Keep the smallest pair with zero feasibility loss, ≥99% of exhaustive-reference insertion success, and the construction target. |
| T_CAP=8→16 and event-boundary times | `ADOPT_WITH_MODIFICATIONS` | Working values retained, but completeness is not claimed after capping. S1 escalation includes an uncapped empty-bay fallback. |
| Cost formula and `eta=0.01*max(w1,1)` | `DEFER_PENDING_MEASUREMENT` | S1 A/B calibrates tie-break only; eta must never reverse a non-tied weighted objective ordering. |
| T0 is “always successful” | `ADOPT_WITH_MODIFICATIONS` | True only after fit preflight. No-fitting-bay input is explicitly unsatisfiable for this model and blocks official-data contract. |
| Multi-start profiles and biased randomization | `ADOPT_WITH_MODIFICATIONS` | Deterministic profiles first; randomized profiles use one recorded RNG and run only within measured constructor budget. |
| Gurobi-first exact layer plus CP-SAT fallback | `ADOPT` | Environment/spec supports both; S2 probes lazily only after verified T0. |
| Exact backend protocol includes assignment in S2 | `ADOPT_WITH_MODIFICATIONS` | S2 contract owns retiming only. S4 extends it with assignment requests, preventing S2 from depending on S4. |
| Gurobi indicator / isomorphic CP-SAT retime | `ADOPT` | Direct non-interlock trichotomy encoding from design §2.4. |
| Fixed exact timeboxes, pilot formula, dirty thresholds | `DEFER_PENDING_MEASUREMENT` | S2 fixes timebox/pilot from predefined matrix; S3 calibrates dirty trigger. Until chosen, conservative values in the draft are flags, not evidence-backed defaults. |
| Draft ALNS D6 cross-bay repair in S3 | `REJECT` | S3 is intra-bay. Cross-bay D6, moves, swaps, and assignment-v2 belong solely to S4. |
| Undo log and current/incumbent separation | `ADOPT` | Required for candidate rollback and anytime safety. |
| Draft pseudocode assigns `cur_obj` before classifying improvement | `ADOPT_WITH_MODIFICATIONS` | Save `previous_cur_obj`; classify `new_obj < previous_cur_obj-EPS`; assign only after classification/acceptance. Add a regression test. |
| Design strict then RRT vs draft SA default plus RRT | `ADOPT_WITH_MODIFICATIONS` | Implement strict baseline first, then flagged RRT and SA. Default stays strict until preregistered S3 A/B selects one; incumbent remains strict/checker verified. |
| Ropke-Pisinger weights/numeric operator constants | `DEFER_PENDING_MEASUREMENT` | Static uniform is the safe initial default. S3 A/B may enable adaptation; no literature constant becomes default without evidence. |
| `construct.py`/`alns.py` vs `constructor.py`/`lns.py` | `ADOPT_WITH_MODIFICATIONS` | Canonical names are `construct.py` and `alns.py`; no aliases. |
| Stale `solver-design.md` authority reference | `REJECT` | All new plans link `solver-design-en.md`; the similarly named Korean file is not the English authority. |
| Canonical serializer as only operations builder | `ADOPT` | Required by checker replay `1279-1386`; S6 extends ordering, not the output path. |
| Budget reserve ladder | `ADOPT_WITH_MODIFICATIONS` | S0 stress measures it; reserve can increase but never consume the guaranteed T0 path. `time.monotonic()` is mandatory. |
| Full check only on improving incumbent candidate | `ADOPT_WITH_MODIFICATIONS` | Every slice also full-checks at its observable boundary; runtime search only checks potential incumbent replacements, plus periodic safety sampling configured by stage. |
| Assignment-v2 Gurobi exact float, CP-SAT scaled fallback | `ADOPT_WITH_MODIFICATIONS` | Owned by S4. All returned assignments are recomputed with checker-float Z2 and compared only after construction/full check. |
| S5 three workers plus orchestrator | `DEFER_PENDING_MEASUREMENT` | Evaluate 2 and 3 workers, each exact backend thread=1; enable only within four-core and license limits. |
| Interlock serializer topological order | `ADOPT_WITH_MODIFICATIONS` | S6 truth-table tests precede behavior; ordering failure rejects candidate and keeps incumbent. |
| Raster/compiled geometry after S6 | `REJECT` for S0-S6 | Outside this roadmap; no stage gate needs it. |

All other numeric draft thresholds not explicitly fixed by checker semantics are configuration hypotheses and must be selected at the earliest owning measurement gate from a preregistered matrix. Semantic constants (`dwell=max(P,1)`, half-open inequalities, EXIT-before-ENTRY, float objective) are fixed by evidence, not tunable.

Threshold provenance registry:

| Threshold family | Provenance and decision point |
|---|---|
| `dwell`, inequalities, integer output, objective formulas | Fixed by checker lines cited in §2; never calibrated. |
| Objective parity `1e-6` relative | Fixed diagnostic tolerance from design §6; the incumbent decision still uses the checker value, and any feasibility-decision mismatch has zero tolerance. |
| Acceptance comparison `1e-9` relative | Adopted design safeguard; S0 parity confirms it never authorizes a checker-worse replacement. |
| 1,000 targeted/geometry and 100 objective cases | Fixed preimplementation coverage counts from design §6/draft tests; failure count must be zero. |
| Cache cap and 256 MiB cache / 2 GiB process measurement budgets | Cap deferred to S0 matrix; memory budgets are conservative plan limits well below the specification’s 16 GiB server ceiling. |
| Five-second S0/S1 and 300-block target | Fixed by the roadmap gate; failure blocks, never recalibrates the criterion. |
| S1 10% median and 8/10 gain | Fixed before results as the quantitative meaning of “meaningful” on the historical representative `dev-10`; not an algorithm threshold. |
| T/K/profile budget/tie-break | Deferred to S1’s preregistered matrix and instance measurements. |
| Backend timebox/pilot | Deferred to S2 `{1,2,5}`/`{0,2,4}` matrix subject to deadline safety. |
| Destroy size, dirty trigger, acceptor, adaptive scores | Deferred to S3 matrices/A-B; static strict/uniform is the safe pre-gate default. |
| High-w23/dense subset boundaries | Derived from instance statistics before outcomes (top quartile formulas in §5). |
| S3/S4 improvement coverage | Fixed gate policies before execution; incumbent preservation imposes zero checker-objective regression. |
| S5 240-second match and S6 25% dense coverage | Fixed optional enablement policies before results; failure disables rather than weakens them. |
| 4 cores, 16 GiB, 15 MB package | Fixed by problem analysis §5.1/§5.4. |

## 8. Cross-stage traceability

| Requirement | Planned owner/slice | Permanent tests/evidence |
|---|---|---|
| Checker truth table, P=0, contact, same-day handoff/order | S0-01 | `test_contract_semantics`; `contract --suite semantic` |
| Exact geometry/cache/fit | S0-02 | `test_geometry_kernel`; predicate microbenchmark |
| Objective/state/targeted parity | S0-03 | `test_state_parity`, `test_validate_parity`; 1,000 cases |
| Serializer/T0/incumbent/deadline armor | S0-04/S0-05 | serializer, trivial, budget-entry tests; 5-second training run |
| Shared harness and evidence proof | S0-06 | harness schema/process tests; S0 gate |
| Assignment-v1 + insertion + candidates/escalation | S1-01…04 | `test_construct`; training construction metrics |
| Multi-start/determinism/meaningful gain | S1-05 | paired T0 A/B; replay equality |
| Exact backend isolation/isomorphism/retime | S2-01…04 | backend/retime tests; synthetic optimal equality |
| Never-worse and fallback | S2-05 | forced backend failures; Z1 gate |
| Intra-bay destroy/repair/acceptance/anytime | S3-01…05 | `test_alns`; 60/300 A/B and counters |
| Cross-bay move/swap and assignment-v2 | S4-01…04 | assignment refinement tests; high-w23 A/B |
| Process portfolio/resources/cleanup | S5-01…04 | portfolio/process tests; server-like stress |
| OBS truth table/interlock/order | S6-01…04 | interlock/serializer tests; dense A/B |
| Package/stress/rehearsal | S6-05/S6-06 | packaging test and isolated rehearsal |

## 9. Evidence, commits, cleanup, and restart

Each behavioral slice follows: update this status to `IN_PROGRESS`; add a failing test; run the exact targeted test and save RED evidence showing missing/wrong behavior rather than syntax/setup failure; implement the minimum; run targeted GREEN; run prior regressions; run a real or synthetic official-checker case; generate structured evidence; clean child processes/temp solver environments/packages; update history; make one atomic implementation commit with the stage plan’s message. Generated raw evidence and local data are never packaged and are not automatically staged.

Restart without chat history:

1. `git -C /Users/brown/workspace/ogc/fable-native-implementation status --short --branch` and verify branch.
2. Read this document’s current table and last history entry, then the active stage document.
3. Verify the last implementation commit exists and inspect the referenced evidence `COMPLETE`, `summary.json`, and `gate.json`.
4. Run `$PY -m baseline.harness.cli report --stage sN --latest-complete` and the active slice’s targeted regression command.
5. Resume only the recorded next action. Never infer completion from files alone or from chat.

## 10. Append-only implementation history

Never edit or delete existing rows/entries. Append one fenced YAML entry per transition/evidence event:

```yaml
- timestamp: 2026-07-12T00:00:00+09:00
  stage: S0
  slice: S0-01
  old_status: NOT_STARTED
  new_status: IN_PROGRESS
  branch: fable-native-implementation
  commit: <HEAD>
  dirty: false
  commands:
    - <exact argv>
  red_evidence: <path or null>
  green_evidence: <path or null>
  checker_result: <feasible/stage/objective or null>
  benchmark_or_stress_evidence: <path or null>
  gate_decision: NOT_RUN|PASS|FAIL|GATE_FAILED_DISABLED
  failure_or_fallback_reason: null
  feature_default_decision: <flag=value and rationale>
  next_action: <single executable action>
```

No implementation history entries exist yet. S0-S6 remain `NOT_STARTED`; every implementation gate remains `NOT_RUN`.

```yaml
- timestamp: 2026-07-12T20:07:06+09:00
  stage: S0
  slice: S0-01
  old_status: NOT_STARTED
  new_status: IN_PROGRESS
  branch: fable-native-implementation
  commit: e2b0d33f43fe5129459785a52165b8babfe3b95a
  dirty: false
  commands:
    - git status --short --branch
    - git rev-parse HEAD
    - git rev-parse '@{upstream}'
    - /opt/homebrew/Caskroom/miniforge/base/envs/ogc-2026/bin/python --version
    - rg -n '^### S0-01\\b' docs/fable/implementation-steps/s0-foundation.md
  red_evidence: null
  green_evidence: null
  checker_result: null
  benchmark_or_stress_evidence: null
  gate_decision: NOT_RUN
  failure_or_fallback_reason: null
  feature_default_decision: native_solver=false until the full S0 gate passes
  next_action: add the S0-01 semantic contract test and demonstrate the intended adapter-import RED
```

```yaml
- timestamp: 2026-07-12T20:09:25+09:00
  stage: S0
  slice: S0-01
  old_status: IN_PROGRESS
  new_status: IN_PROGRESS
  branch: fable-native-implementation
  commit: e2b0d33f43fe5129459785a52165b8babfe3b95a
  dirty: true
  commands:
    - cd baseline && /opt/homebrew/Caskroom/miniforge/base/envs/ogc-2026/bin/python -m unittest tests.test_contract_semantics -v
  red_evidence: benchmarks/evidence/s0/s0-01/20260712T110925Z-s0-01/red.txt
  green_evidence: null
  checker_result: null
  benchmark_or_stress_evidence: null
  gate_decision: NOT_RUN
  failure_or_fallback_reason: intended RED confirmed with exit 1 solely because solver.checker_adapter is absent
  feature_default_decision: native_solver=false until the full S0 gate passes
  next_action: implement the minimum checker adapter and instance parsing/fit preflight
```

```yaml
- timestamp: 2026-07-12T20:12:03+09:00
  stage: S0
  slice: S0-01
  old_status: IN_PROGRESS
  new_status: IN_PROGRESS
  branch: fable-native-implementation
  commit: e2b0d33f43fe5129459785a52165b8babfe3b95a
  dirty: true
  commands:
    - cd baseline && /opt/homebrew/Caskroom/miniforge/base/envs/ogc-2026/bin/python -m unittest tests.test_contract_semantics -v
    - cd baseline && /opt/homebrew/Caskroom/miniforge/base/envs/ogc-2026/bin/python -m unittest tests.test_contract_semantics -v
    - /opt/homebrew/Caskroom/miniforge/base/envs/ogc-2026/bin/python -m py_compile baseline/solver/__init__.py baseline/solver/checker_adapter.py baseline/solver/instance.py baseline/tests/__init__.py baseline/tests/fixtures.py baseline/tests/test_contract_semantics.py
    - git diff --check
  red_evidence: benchmarks/evidence/s0/s0-01/20260712T110925Z-s0-01/red.txt
  green_evidence: benchmarks/evidence/s0/s0-01/20260712T110925Z-s0-01/green.txt
  checker_result: 16 official checker decisions matched; feasible cases reached Stage 5 and expected rejections occurred at Stages 2, 3, or 5
  benchmark_or_stress_evidence: null
  gate_decision: NOT_RUN
  failure_or_fallback_reason: null
  feature_default_decision: native_solver=false until the full S0 gate passes
  next_action: audit the selected-slice diff and finalize structured checker evidence
```

```yaml
- timestamp: 2026-07-12T20:13:34+09:00
  stage: S0
  slice: S0-01
  old_status: IN_PROGRESS
  new_status: IN_PROGRESS
  branch: fable-native-implementation
  commit: pending atomic commit test(s0): lock checker semantics and instance parsing
  dirty: true
  commands:
    - cd baseline && /opt/homebrew/Caskroom/miniforge/base/envs/ogc-2026/bin/python -m unittest tests.test_contract_semantics -v
    - /opt/homebrew/Caskroom/miniforge/base/envs/ogc-2026/bin/python -m py_compile baseline/solver/__init__.py baseline/solver/checker_adapter.py baseline/solver/instance.py baseline/tests/__init__.py baseline/tests/fixtures.py baseline/tests/test_contract_semantics.py
    - /opt/homebrew/Caskroom/miniforge/base/envs/ogc-2026/bin/python tracked-example fit preflight
    - git diff --check
    - git diff --quiet -- baseline/utils.py baseline/baseline_greedy.py
  red_evidence: benchmarks/evidence/s0/s0-01/20260712T110925Z-s0-01/red.txt
  green_evidence: benchmarks/evidence/s0/s0-01/20260712T110925Z-s0-01/green.txt
  checker_result: PASS; 16 official checker decisions, zero mismatches, feasible cases at Stage 5
  benchmark_or_stress_evidence: not required for S0-01
  gate_decision: NOT_RUN
  failure_or_fallback_reason: null
  feature_default_decision: native_solver=false until the full S0 gate passes
  next_action: commit and push S0-01, then stop; S0-02 is the next eligible slice
```

```yaml
- timestamp: 2026-07-12T20:46:59+09:00
  stage: S0
  slice: S0-02
  old_status: IN_PROGRESS
  new_status: IN_PROGRESS
  branch: fable-native-implementation
  commit: 6d2f08acbbee4e3a308111a562d67bfc6ba4e916
  dirty: false
  commands:
    - git status --short --branch
    - git rev-parse HEAD
    - git rev-parse '@{upstream}'
    - /opt/homebrew/Caskroom/miniforge/base/envs/ogc-2026/bin/python --version
    - rg -n '^### S0-02\\b' docs/fable/implementation-steps/s0-foundation.md
    - test -f benchmarks/evidence/s0/s0-01/20260712T110925Z-s0-01/COMPLETE
    - git merge-base --is-ancestor 814a716 HEAD
  red_evidence: null
  green_evidence: null
  checker_result: null
  benchmark_or_stress_evidence: null
  gate_decision: NOT_RUN
  failure_or_fallback_reason: null
  feature_default_decision: native_solver=false until the full S0 gate passes; initial geometry cache cap is 262144 entries
  next_action: add the S0-02 geometry test and demonstrate the intended GeomKernel-import RED
```

```yaml
- timestamp: 2026-07-12T20:47:56+09:00
  stage: S0
  slice: S0-02
  old_status: IN_PROGRESS
  new_status: IN_PROGRESS
  branch: fable-native-implementation
  commit: 6d2f08acbbee4e3a308111a562d67bfc6ba4e916
  dirty: true
  commands:
    - cd baseline && /opt/homebrew/Caskroom/miniforge/base/envs/ogc-2026/bin/python -m unittest tests.test_geometry_kernel.GeometryKernelTests.test_contact_and_cache_identity -v
  red_evidence: benchmarks/evidence/s0/s0-02/20260712T114659Z-s0-02/red.txt
  green_evidence: null
  checker_result: null
  benchmark_or_stress_evidence: null
  gate_decision: NOT_RUN
  failure_or_fallback_reason: intended RED confirmed with exit 1 solely because solver.geometry is absent
  feature_default_decision: native_solver=false until the full S0 gate passes; initial geometry cache cap is 262144 entries
  next_action: implement ShapeInfo and GeomKernel with exact tuple identity, translated union and OBS predicates, AABB fast paths, bounded LRU, and counters
```

```yaml
- timestamp: 2026-07-12T20:49:30+09:00
  stage: S0
  slice: S0-02
  old_status: IN_PROGRESS
  new_status: IN_PROGRESS
  branch: fable-native-implementation
  commit: 6d2f08acbbee4e3a308111a562d67bfc6ba4e916
  dirty: true
  commands:
    - cd baseline && /opt/homebrew/Caskroom/miniforge/base/envs/ogc-2026/bin/python -m unittest tests.test_geometry_kernel.GeometryKernelTests.test_contact_and_cache_identity -v
    - cd baseline && /opt/homebrew/Caskroom/miniforge/base/envs/ogc-2026/bin/python -m unittest tests.test_geometry_kernel -v
    - cd baseline && /opt/homebrew/Caskroom/miniforge/base/envs/ogc-2026/bin/python -m unittest tests.test_contract_semantics tests.test_geometry_kernel -v
  red_evidence: benchmarks/evidence/s0/s0-02/20260712T114659Z-s0-02/red.txt
  green_evidence: benchmarks/evidence/s0/s0-02/20260712T114659Z-s0-02/green-targeted.txt
  checker_result: null
  benchmark_or_stress_evidence: null
  gate_decision: NOT_RUN
  failure_or_fallback_reason: null
  feature_default_decision: native_solver=false until the full S0 gate passes; exact geometry behavior is implemented but not connected to solver behavior
  next_action: serialize contact and positive-area overlap fixtures and verify them with official_check
```

```yaml
- timestamp: 2026-07-12T20:49:50+09:00
  stage: S0
  slice: S0-02
  old_status: IN_PROGRESS
  new_status: IN_PROGRESS
  branch: fable-native-implementation
  commit: 6d2f08acbbee4e3a308111a562d67bfc6ba4e916
  dirty: true
  commands:
    - /opt/homebrew/Caskroom/miniforge/base/envs/ogc-2026/bin/python -c official-check-contact-and-overlap-fixtures
  red_evidence: benchmarks/evidence/s0/s0-02/20260712T114659Z-s0-02/red.txt
  green_evidence: benchmarks/evidence/s0/s0-02/20260712T114659Z-s0-02/green-targeted.txt
  checker_result: PASS; contact fixture feasible at Stage 5 and positive-area overlap rejected at Stage 2
  benchmark_or_stress_evidence: null
  gate_decision: NOT_RUN
  failure_or_fallback_reason: null
  feature_default_decision: native_solver=false until the full S0 gate passes; OBS remains stored-only and disconnected
  next_action: run the planned S0 predicate cache-cap measurement equivalent pending the S0-06 harness replay
```

```yaml
- timestamp: 2026-07-12T20:52:24+09:00
  stage: S0
  slice: S0-02
  old_status: IN_PROGRESS
  new_status: IN_PROGRESS
  branch: fable-native-implementation
  commit: 6d2f08acbbee4e3a308111a562d67bfc6ba4e916
  dirty: true
  commands:
    - for cap in 65536 262144 1048576 2097152; do /opt/homebrew/Caskroom/miniforge/base/envs/ogc-2026/bin/python benchmarks/evidence/s0/s0-02/20260712T114659Z-s0-02/predicate_benchmark.py --cache-cap "$cap"; done
  red_evidence: benchmarks/evidence/s0/s0-02/20260712T114659Z-s0-02/red.txt
  green_evidence: benchmarks/evidence/s0/s0-02/20260712T114659Z-s0-02/green-targeted.txt
  checker_result: PASS; contact fixture feasible at Stage 5 and positive-area overlap rejected at Stage 2
  benchmark_or_stress_evidence: benchmarks/evidence/s0/s0-02/20260712T114659Z-s0-02/summary.json
  gate_decision: NOT_RUN
  failure_or_fallback_reason: null
  feature_default_decision: select cache_cap=262144; projected cache RSS 88801280 bytes and measured peak process RSS 87408640 bytes are within limits, 65536 had 0% versus 50% hit rate, and larger caps projected above 256 MiB
  next_action: run final regression, syntax, frozen-file, evidence, and selected-slice diff checks
```

```yaml
- timestamp: 2026-07-12T20:53:27+09:00
  stage: S0
  slice: S0-02
  old_status: IN_PROGRESS
  new_status: IN_PROGRESS
  branch: fable-native-implementation
  commit: pending atomic commit feat(s0): add checker-exact geometry kernel and cache
  dirty: true
  commands:
    - cd baseline && /opt/homebrew/Caskroom/miniforge/base/envs/ogc-2026/bin/python -m unittest tests.test_geometry_kernel.GeometryKernelTests.test_contact_and_cache_identity -v
    - cd baseline && /opt/homebrew/Caskroom/miniforge/base/envs/ogc-2026/bin/python -m unittest tests.test_contract_semantics tests.test_geometry_kernel -v
    - /opt/homebrew/Caskroom/miniforge/base/envs/ogc-2026/bin/python -m py_compile baseline/solver/geometry.py baseline/tests/test_geometry_kernel.py
    - /opt/homebrew/Caskroom/miniforge/base/envs/ogc-2026/bin/python -c official-check-contact-and-overlap-fixtures
    - for cap in 65536 262144 1048576 2097152; do /opt/homebrew/Caskroom/miniforge/base/envs/ogc-2026/bin/python benchmarks/evidence/s0/s0-02/20260712T114659Z-s0-02/predicate_benchmark.py --cache-cap "$cap"; done
    - git diff --check
    - git diff --quiet -- baseline/utils.py baseline/baseline_greedy.py
  red_evidence: benchmarks/evidence/s0/s0-02/20260712T114659Z-s0-02/red.txt
  green_evidence: benchmarks/evidence/s0/s0-02/20260712T114659Z-s0-02/green-targeted.txt
  checker_result: PASS; contact fixture feasible at Stage 5 and positive-area overlap rejected at Stage 2
  benchmark_or_stress_evidence: benchmarks/evidence/s0/s0-02/20260712T114659Z-s0-02/summary.json
  gate_decision: NOT_RUN
  failure_or_fallback_reason: null
  feature_default_decision: cache_cap=262144 selected under the preregistered memory/hit-rate rule; native_solver=false until the full S0 gate passes; OBS remains stored-only and disconnected
  next_action: commit and push S0-02, then stop; S0-03 is the next eligible slice
```

```yaml
- timestamp: 2026-07-12T21:02:00+09:00
  stage: S0
  slice: S0-03
  old_status: IN_PROGRESS
  new_status: IN_PROGRESS
  branch: fable-native-implementation
  commit: d3677fa590450178eafb7a82d8cf2f2efaec11c3
  dirty: false
  commands:
    - git status --short --branch
    - git rev-parse HEAD
    - git rev-parse '@{upstream}'
    - /opt/homebrew/Caskroom/miniforge/base/envs/ogc-2026/bin/python --version
    - rg -n '^### S0-03\\b' docs/fable/implementation-steps/s0-foundation.md
    - test -f benchmarks/evidence/s0/s0-01/20260712T110925Z-s0-01/COMPLETE
    - test -f benchmarks/evidence/s0/s0-02/20260712T114659Z-s0-02/COMPLETE
    - git merge-base --is-ancestor 814a716 HEAD
    - git merge-base --is-ancestor d3677fa HEAD
  red_evidence: null
  green_evidence: null
  checker_result: null
  benchmark_or_stress_evidence: null
  gate_decision: NOT_RUN
  failure_or_fallback_reason: null
  feature_default_decision: native_solver=false until the full S0 gate passes; targeted validation is diagnostic and branch-1/2 only
  next_action: add the S0-03 state and validation parity tests and demonstrate the intended missing API RED
```

```yaml
- timestamp: 2026-07-12T21:02:07+09:00
  stage: S0
  slice: S0-03
  old_status: IN_PROGRESS
  new_status: IN_PROGRESS
  branch: fable-native-implementation
  commit: d3677fa590450178eafb7a82d8cf2f2efaec11c3
  dirty: true
  commands:
    - cd baseline && /opt/homebrew/Caskroom/miniforge/base/envs/ogc-2026/bin/python -m unittest tests.test_validate_parity.ValidateParityTests.test_seeded_1000_candidates -v
  red_evidence: benchmarks/evidence/s0/s0-03/20260712T120202Z-s0-03/red.txt
  green_evidence: null
  checker_result: null
  benchmark_or_stress_evidence: null
  gate_decision: NOT_RUN
  failure_or_fallback_reason: intended RED confirmed with exit 1 solely because solver.state is absent
  feature_default_decision: native_solver=false until the full S0 gate passes; targeted validation is diagnostic and branch-1/2 only
  next_action: implement Placement, SolutionState, objective diagnostics, and unary/pair/changed/insertion validation
```

```yaml
- timestamp: 2026-07-12T21:03:00+09:00
  stage: S0
  slice: S0-03
  old_status: IN_PROGRESS
  new_status: IN_PROGRESS
  branch: fable-native-implementation
  commit: d3677fa590450178eafb7a82d8cf2f2efaec11c3
  dirty: true
  commands:
    - cd baseline && /opt/homebrew/Caskroom/miniforge/base/envs/ogc-2026/bin/python -m unittest tests.test_validate_parity.ValidateParityTests.test_seeded_1000_candidates -v
    - cd baseline && /opt/homebrew/Caskroom/miniforge/base/envs/ogc-2026/bin/python -m unittest tests.test_contract_semantics tests.test_geometry_kernel tests.test_state_parity tests.test_validate_parity -v
  red_evidence: benchmarks/evidence/s0/s0-03/20260712T120202Z-s0-03/red.txt
  green_evidence: benchmarks/evidence/s0/s0-03/20260712T120202Z-s0-03/green-targeted.txt
  checker_result: targeted GREEN; 1000 seeded candidates, zero feasibility mismatches
  benchmark_or_stress_evidence: null
  gate_decision: NOT_RUN
  failure_or_fallback_reason: null
  feature_default_decision: native_solver=false until the full S0 gate passes; validation remains a branch-1/2 diagnostic/filter
  next_action: run the 100-case official-checker objective parity proof
```

```yaml
- timestamp: 2026-07-12T21:04:08+09:00
  stage: S0
  slice: S0-03
  old_status: IN_PROGRESS
  new_status: IN_PROGRESS
  branch: fable-native-implementation
  commit: d3677fa590450178eafb7a82d8cf2f2efaec11c3
  dirty: true
  commands:
    - cd baseline && /opt/homebrew/Caskroom/miniforge/base/envs/ogc-2026/bin/python -m unittest tests.test_state_parity.StateParityTests.test_seeded_100_objective_cases_match_checker -v
  red_evidence: benchmarks/evidence/s0/s0-03/20260712T120202Z-s0-03/red.txt
  green_evidence: benchmarks/evidence/s0/s0-03/20260712T120202Z-s0-03/green-targeted.txt
  checker_result: PASS; 100 targeted objective cases matched official checker Z1, Z2, Z3, and weighted objective within 1e-6 relative tolerance
  benchmark_or_stress_evidence: benchmarks/evidence/s0/s0-03/20260712T120202Z-s0-03/summary.json
  gate_decision: NOT_RUN
  failure_or_fallback_reason: null
  feature_default_decision: native_solver=false until the full S0 gate passes; S0-06 must replay both parity commands through the shared harness
  next_action: run final regression, syntax, frozen-file, evidence, and selected-slice diff checks
```

```yaml
- timestamp: 2026-07-12T21:04:35+09:00
  stage: S0
  slice: S0-03
  old_status: IN_PROGRESS
  new_status: IN_PROGRESS
  branch: fable-native-implementation
  commit: pending atomic commit feat(s0): add state and targeted checker parity
  dirty: true
  commands:
    - cd baseline && /opt/homebrew/Caskroom/miniforge/base/envs/ogc-2026/bin/python -m unittest tests.test_validate_parity.ValidateParityTests.test_seeded_1000_candidates -v
    - cd baseline && /opt/homebrew/Caskroom/miniforge/base/envs/ogc-2026/bin/python -m unittest tests.test_state_parity.StateParityTests.test_seeded_100_objective_cases_match_checker -v
    - cd baseline && /opt/homebrew/Caskroom/miniforge/base/envs/ogc-2026/bin/python -m unittest tests.test_contract_semantics tests.test_geometry_kernel tests.test_state_parity tests.test_validate_parity -v
    - /opt/homebrew/Caskroom/miniforge/base/envs/ogc-2026/bin/python -m py_compile baseline/solver/state.py baseline/solver/validate.py baseline/tests/fixtures.py baseline/tests/test_state_parity.py baseline/tests/test_validate_parity.py
    - git diff --check
    - git diff --quiet -- baseline/utils.py baseline/baseline_greedy.py
  red_evidence: benchmarks/evidence/s0/s0-03/20260712T120202Z-s0-03/red.txt
  green_evidence: benchmarks/evidence/s0/s0-03/20260712T120202Z-s0-03/green-targeted.txt
  checker_result: PASS; 1000 targeted decisions and 100 objective cases matched the official checker with zero mismatches
  benchmark_or_stress_evidence: benchmarks/evidence/s0/s0-03/20260712T120202Z-s0-03/summary.json
  gate_decision: NOT_RUN
  failure_or_fallback_reason: null
  feature_default_decision: native_solver=false until the full S0 gate passes; no candidate ranking or S1 behavior added
  next_action: commit and push S0-03, then stop; S0-04 is the next eligible slice
```

```yaml
- timestamp: 2026-07-12T21:16:16+09:00
  stage: S0
  slice: S0-04
  old_status: IN_PROGRESS
  new_status: IN_PROGRESS
  branch: fable-native-implementation
  commit: 3378bb2b0a790c14be4069a4abedbb200b1c4da1
  dirty: false
  commands:
    - git status --short --branch
    - git rev-parse HEAD
    - git rev-parse '@{upstream}'
    - /opt/homebrew/Caskroom/miniforge/base/envs/ogc-2026/bin/python --version
    - rg -n '^### S0-04\\b' docs/fable/implementation-steps/s0-foundation.md
    - test -f benchmarks/evidence/s0/s0-01/20260712T110925Z-s0-01/COMPLETE
    - test -f benchmarks/evidence/s0/s0-02/20260712T114659Z-s0-02/COMPLETE
    - test -f benchmarks/evidence/s0/s0-03/20260712T120202Z-s0-03/COMPLETE
    - git merge-base --is-ancestor 3378bb2 HEAD
  red_evidence: null
  green_evidence: null
  checker_result: null
  benchmark_or_stress_evidence: null
  gate_decision: NOT_RUN
  failure_or_fallback_reason: null
  feature_default_decision: native_solver=false until the full S0 gate passes; T0 is the only planned pipeline for this slice
  next_action: add the S0-04 serializer/T0/incumbent tests and demonstrate the intended build_t0 RED
```

```yaml
- timestamp: 2026-07-12T21:18:03+09:00
  stage: S0
  slice: S0-04
  old_status: IN_PROGRESS
  new_status: IN_PROGRESS
  branch: fable-native-implementation
  commit: 3378bb2b0a790c14be4069a4abedbb200b1c4da1
  dirty: true
  commands:
    - cd baseline && /opt/homebrew/Caskroom/miniforge/base/envs/ogc-2026/bin/python -m unittest tests.test_trivial.TrivialTests.test_example_registers_verified_incumbent -v
  red_evidence: benchmarks/evidence/s0/s0-04/20260712T121616Z-s0-04/red.txt
  green_evidence: null
  checker_result: null
  benchmark_or_stress_evidence: null
  gate_decision: NOT_RUN
  failure_or_fallback_reason: intended RED confirmed with exit 1 solely because solver.trivial is absent
  feature_default_decision: native_solver=false until the full S0 gate passes; T0 remains the only planned pipeline
  next_action: implement canonical serialization, fit-qualified empty-bay T0, and checker-gated verified incumbent storage
```

```yaml
- timestamp: 2026-07-12T21:18:24+09:00
  stage: S0
  slice: S0-04
  old_status: IN_PROGRESS
  new_status: IN_PROGRESS
  branch: fable-native-implementation
  commit: 3378bb2b0a790c14be4069a4abedbb200b1c4da1
  dirty: true
  commands:
    - cd baseline && /opt/homebrew/Caskroom/miniforge/base/envs/ogc-2026/bin/python -m unittest tests.test_trivial.TrivialTests.test_example_registers_verified_incumbent -v
    - cd baseline && /opt/homebrew/Caskroom/miniforge/base/envs/ogc-2026/bin/python -m unittest tests.test_contract_semantics tests.test_geometry_kernel tests.test_state_parity tests.test_validate_parity tests.test_serialize tests.test_trivial -v
  red_evidence: benchmarks/evidence/s0/s0-04/20260712T121616Z-s0-04/red.txt
  green_evidence: benchmarks/evidence/s0/s0-04/20260712T121616Z-s0-04/green-targeted.txt
  checker_result: targeted GREEN; tracked example reached Stage 5 with exactly one initial incumbent verification
  benchmark_or_stress_evidence: null
  gate_decision: NOT_RUN
  failure_or_fallback_reason: null
  feature_default_decision: native_solver=false until the full S0 gate passes; canonical T0 is implemented without S1 assignment or construction behavior
  next_action: run the direct example benchmark and no-fit preflight equivalents pending S0-06 harness replay
```

```yaml
- timestamp: 2026-07-12T21:20:41+09:00
  stage: S0
  slice: S0-04
  old_status: IN_PROGRESS
  new_status: IN_PROGRESS
  branch: fable-native-implementation
  commit: 3378bb2b0a790c14be4069a4abedbb200b1c4da1
  dirty: true
  commands:
    - cd baseline && /opt/homebrew/Caskroom/miniforge/base/envs/ogc-2026/bin/python -m unittest tests.test_contract_semantics tests.test_geometry_kernel tests.test_state_parity tests.test_validate_parity tests.test_serialize tests.test_trivial -v
    - /opt/homebrew/Caskroom/miniforge/base/envs/ogc-2026/bin/python benchmarks/evidence/s0/s0-04/20260712T121616Z-s0-04/slice_proof.py example
    - /opt/homebrew/Caskroom/miniforge/base/envs/ogc-2026/bin/python benchmarks/evidence/s0/s0-04/20260712T121616Z-s0-04/slice_proof.py preflight
  red_evidence: benchmarks/evidence/s0/s0-04/20260712T121616Z-s0-04/red.txt
  green_evidence: benchmarks/evidence/s0/s0-04/20260712T121616Z-s0-04/final-regression.txt
  checker_result: PASS; tracked example Stage 5 feasible with objective 323992.78620320855 and exactly one incumbent verification
  benchmark_or_stress_evidence: benchmarks/evidence/s0/s0-04/20260712T121616Z-s0-04/summary.json
  gate_decision: NOT_RUN
  failure_or_fallback_reason: synthetic no-fit preflight exited 4 as input_error naming block 0; direct proof runner required one import-path setup retry before solver behavior was exercised
  feature_default_decision: native_solver=false until the full S0 gate passes; S0-06 must replay the benchmark and preflight proofs through the shared harness
  next_action: run final syntax, frozen-file, evidence, cleanup, and selected-slice diff checks
```

```yaml
- timestamp: 2026-07-12T21:21:29+09:00
  stage: S0
  slice: S0-04
  old_status: IN_PROGRESS
  new_status: IN_PROGRESS
  branch: fable-native-implementation
  commit: pending atomic commit feat(s0): add canonical T0 and verified incumbent
  dirty: true
  commands:
    - cd baseline && /opt/homebrew/Caskroom/miniforge/base/envs/ogc-2026/bin/python -m unittest tests.test_trivial.TrivialTests.test_example_registers_verified_incumbent -v
    - cd baseline && /opt/homebrew/Caskroom/miniforge/base/envs/ogc-2026/bin/python -m unittest tests.test_contract_semantics tests.test_geometry_kernel tests.test_state_parity tests.test_validate_parity tests.test_serialize tests.test_trivial -v
    - /opt/homebrew/Caskroom/miniforge/base/envs/ogc-2026/bin/python benchmarks/evidence/s0/s0-04/20260712T121616Z-s0-04/slice_proof.py example
    - /opt/homebrew/Caskroom/miniforge/base/envs/ogc-2026/bin/python benchmarks/evidence/s0/s0-04/20260712T121616Z-s0-04/slice_proof.py preflight
    - /opt/homebrew/Caskroom/miniforge/base/envs/ogc-2026/bin/python -m py_compile baseline/solver/serialize.py baseline/solver/trivial.py baseline/solver/incumbent.py baseline/tests/test_serialize.py baseline/tests/test_trivial.py
    - git diff --check
    - git diff --quiet -- baseline/utils.py baseline/baseline_greedy.py
    - ps aux process cleanup inspection
  red_evidence: benchmarks/evidence/s0/s0-04/20260712T121616Z-s0-04/red.txt
  green_evidence: benchmarks/evidence/s0/s0-04/20260712T121616Z-s0-04/final-regression.txt
  checker_result: PASS; tracked example Stage 5 feasible in 0.00786445802077651 seconds with exactly one verified initial incumbent
  benchmark_or_stress_evidence: benchmarks/evidence/s0/s0-04/20260712T121616Z-s0-04/summary.json
  gate_decision: NOT_RUN
  failure_or_fallback_reason: null
  feature_default_decision: native_solver=false until the full S0 gate passes; no S1 assignment or constructor behavior added
  next_action: commit and push S0-04, then stop; S0-05 is the next eligible slice
```

```yaml
- timestamp: 2026-07-12T21:30:22+09:00
  stage: S0
  slice: S0-05
  old_status: IN_PROGRESS
  new_status: IN_PROGRESS
  branch: fable-native-implementation
  commit: 3b18b8ac58aabdc17f330a1c09eac2db3b65ab4f
  dirty: false
  commands:
    - git status --short --branch
    - git rev-parse HEAD
    - git rev-parse '@{upstream}'
    - /opt/homebrew/Caskroom/miniforge/base/envs/ogc-2026/bin/python --version
    - rg -n '^### S0-05\\b' docs/fable/implementation-steps/s0-foundation.md
    - test -f benchmarks/evidence/s0/s0-01/20260712T110925Z-s0-01/COMPLETE
    - test -f benchmarks/evidence/s0/s0-02/20260712T114659Z-s0-02/COMPLETE
    - test -f benchmarks/evidence/s0/s0-03/20260712T120202Z-s0-03/COMPLETE
    - test -f benchmarks/evidence/s0/s0-04/20260712T121616Z-s0-04/COMPLETE
    - git merge-base --is-ancestor 3b18b8ac HEAD
  red_evidence: null
  green_evidence: null
  checker_result: null
  benchmark_or_stress_evidence: null
  gate_decision: NOT_RUN
  failure_or_fallback_reason: null
  feature_default_decision: native_solver=false until the full S0 gate passes; T0 is the only returnable pipeline and fault injection is test-only
  next_action: add the S0-05 entry-armor test and demonstrate the intended missing entry API RED
```

```yaml
- timestamp: 2026-07-12T21:31:10+09:00
  stage: S0
  slice: S0-05
  old_status: IN_PROGRESS
  new_status: IN_PROGRESS
  branch: fable-native-implementation
  commit: 3b18b8ac58aabdc17f330a1c09eac2db3b65ab4f
  dirty: true
  commands:
    - cd baseline && /opt/homebrew/Caskroom/miniforge/base/envs/ogc-2026/bin/python -m unittest tests.test_budget_entry.EntryArmorTests.test_exception_returns_verified_serial -v
  red_evidence: benchmarks/evidence/s0/s0-05/20260712T123100Z-s0-05/red.txt
  green_evidence: null
  checker_result: null
  benchmark_or_stress_evidence: null
  gate_decision: NOT_RUN
  failure_or_fallback_reason: intended RED confirmed with exit 1 solely because solver.entry is absent
  feature_default_decision: native_solver=false until the full S0 gate passes; T0 is the only returnable pipeline and fault injection is test-only
  next_action: implement the monotonic budget and verified-incumbent entry shell
```

```yaml
- timestamp: 2026-07-12T21:32:30+09:00
  stage: S0
  slice: S0-05
  old_status: IN_PROGRESS
  new_status: IN_PROGRESS
  branch: fable-native-implementation
  commit: 3b18b8ac58aabdc17f330a1c09eac2db3b65ab4f
  dirty: true
  commands:
    - cd baseline && /opt/homebrew/Caskroom/miniforge/base/envs/ogc-2026/bin/python -m unittest tests.test_budget_entry.EntryArmorTests.test_exception_returns_verified_serial -v
  red_evidence: benchmarks/evidence/s0/s0-05/20260712T123100Z-s0-05/red.txt
  green_evidence: benchmarks/evidence/s0/s0-05/20260712T123100Z-s0-05/green-targeted.txt
  checker_result: targeted GREEN; injected post-incumbent Exception returned the stored T0 and reached official checker Stage 5
  benchmark_or_stress_evidence: null
  gate_decision: NOT_RUN
  failure_or_fallback_reason: null
  feature_default_decision: native_solver=false until the full S0 gate passes; entry delegates only to T0 and catches ordinary Exception only after verified registration
  next_action: run the complete existing S0 unittest regression suite
```

```yaml
- timestamp: 2026-07-12T21:33:00+09:00
  stage: S0
  slice: S0-05
  old_status: IN_PROGRESS
  new_status: IN_PROGRESS
  branch: fable-native-implementation
  commit: 3b18b8ac58aabdc17f330a1c09eac2db3b65ab4f
  dirty: true
  commands:
    - cd baseline && /opt/homebrew/Caskroom/miniforge/base/envs/ogc-2026/bin/python -m unittest discover -s tests -p 'test_*.py' -v
  red_evidence: benchmarks/evidence/s0/s0-05/20260712T123100Z-s0-05/red.txt
  green_evidence: benchmarks/evidence/s0/s0-05/20260712T123100Z-s0-05/green-targeted.txt
  checker_result: PASS; all 37 existing S0 tests passed including deadline reserve, tiny-budget fallback, and BaseException propagation
  benchmark_or_stress_evidence: null
  gate_decision: NOT_RUN
  failure_or_fallback_reason: null
  feature_default_decision: native_solver=false until the full S0 gate passes; S0-06 must replay stress through the shared harness
  next_action: run the direct S0-05 stress/checker equivalent pending S0-06 harness replay
```

```yaml
- timestamp: 2026-07-12T21:34:39+09:00
  stage: S0
  slice: S0-05
  old_status: IN_PROGRESS
  new_status: IN_PROGRESS
  branch: fable-native-implementation
  commit: 3b18b8ac58aabdc17f330a1c09eac2db3b65ab4f
  dirty: true
  commands:
    - /opt/homebrew/Caskroom/miniforge/base/envs/ogc-2026/bin/python benchmarks/evidence/s0/s0-05/20260712T123100Z-s0-05/slice_proof.py
    - rg -n 'return .*state|return serialize\\(' baseline/solver baseline/myalgorithm.py
  red_evidence: benchmarks/evidence/s0/s0-05/20260712T123100Z-s0-05/red.txt
  green_evidence: benchmarks/evidence/s0/s0-05/20260712T123100Z-s0-05/green-targeted.txt
  checker_result: PASS; 24 of 24 stress rows were Stage 5 feasible with one initial verification, stored-object identity, zero unverified returns, and wall time at most TL+0.25s; myalgorithm delegation was Stage 5 feasible
  benchmark_or_stress_evidence: benchmarks/evidence/s0/s0-05/20260712T123100Z-s0-05/summary.json
  gate_decision: NOT_RUN
  failure_or_fallback_reason: direct proof runner required one import-path setup retry before solver behavior was exercised; static matches were validate_pair returning an internal boolean predicate and build_t0 returning state solely into initial incumbent registration
  feature_default_decision: native_solver=false until the full S0 gate passes; S0-06 must replay the stress command through the shared harness
  next_action: run final regression, safety, cleanup, and selected-slice diff checks
```

```yaml
- timestamp: 2026-07-12T21:35:25+09:00
  stage: S0
  slice: S0-05
  old_status: IN_PROGRESS
  new_status: IN_PROGRESS
  branch: fable-native-implementation
  commit: pending atomic commit feat(s0): protect deadlines and verified fallback
  dirty: true
  commands:
    - cd baseline && /opt/homebrew/Caskroom/miniforge/base/envs/ogc-2026/bin/python -m unittest tests.test_budget_entry.EntryArmorTests.test_exception_returns_verified_serial -v
    - cd baseline && /opt/homebrew/Caskroom/miniforge/base/envs/ogc-2026/bin/python -m unittest discover -s tests -p 'test_*.py' -v
    - /opt/homebrew/Caskroom/miniforge/base/envs/ogc-2026/bin/python benchmarks/evidence/s0/s0-05/20260712T123100Z-s0-05/slice_proof.py
    - rg -n 'return .*state|return serialize\\(' baseline/solver baseline/myalgorithm.py
    - /opt/homebrew/Caskroom/miniforge/base/envs/ogc-2026/bin/python -m py_compile baseline/solver/budget.py baseline/solver/entry.py baseline/myalgorithm.py baseline/tests/test_budget_entry.py
    - git diff --check
    - git diff --quiet -- baseline/utils.py baseline/baseline_greedy.py
    - ps aux process cleanup inspection
  red_evidence: benchmarks/evidence/s0/s0-05/20260712T123100Z-s0-05/red.txt
  green_evidence: benchmarks/evidence/s0/s0-05/20260712T123100Z-s0-05/final-regression.txt
  checker_result: PASS; all 38 S0 tests passed and 24 of 24 stress rows plus myalgorithm delegation were Stage 5 feasible with no missing or unverified incumbent
  benchmark_or_stress_evidence: benchmarks/evidence/s0/s0-05/20260712T123100Z-s0-05/summary.json
  gate_decision: NOT_RUN
  failure_or_fallback_reason: null
  feature_default_decision: native_solver=false until the full S0 gate passes; only the T0 pipeline is active and later-stage features remain absent
  next_action: commit and push S0-05, then stop; S0-06 is the next eligible slice
```

## 11. Planning quality audit

Planning audit completed 2026-07-12 Asia/Seoul: **PASS**. This is a document-quality result only, not an implementation gate.

- All eight primary documents exist; declared/actual slice counts are S0=6, S1=5, S2=5, S3=5, S4=4, S5=4, S6=6.
- Every stage and slice has explicit prerequisites/consumed artifacts, outputs, non-dependencies, next-entry conditions, RED/GREEN/checker/evidence work, rollback, cleanup, flags/defaults, and an atomic commit message.
- All stage statuses are `NOT_STARTED`; all gates are `NOT_RUN`; no solver, test, harness, benchmark, or package implementation exists in the target changes.
- S0 owns validation parity; S3/S4 ownership is separated; S2 does not require S4 assignment; S5/S6 disabled-feature outcomes preserve the lower tier; no earlier gate calls later-stage behavior.
- Harness schema, selectors, exit codes, proof-of-run, resume/dedup, A/B, previous-stage comparison, gate evaluation, and failure format are specified.
- All local Markdown links resolve. All new planning text passes the Hangul absence check and is English.
- The two planning inputs remain byte-identical to source. Source branch/HEAD remain `start-point`/`78ef82ece960ac686a6f5c41497a13c2217ffab5`; its three pre-existing untracked paths remain untouched.
- Target status contains only this master document and `docs/fable/implementation-steps/`; checker/reference/input documents are unmodified.

Audit commands used read-only checks with `rg`, `wc`, `cmp`, `git status --short --branch`, and a local-link resolver under the explicit project interpreter. The audit must be rerun after any future plan edit; a failure permits edits only to these planning documents in the planning session.
