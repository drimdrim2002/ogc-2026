# S6 Execution Plan: Mandatory Hardening and Optional Interlock

Parent: [`../fable-native-implementation-progress.md`](../fable-native-implementation-progress.md)

Status: `NOT_STARTED`; gate: `NOT_RUN`; planned slices: 6

Plan-reset authority: [`plan-reset-01.md`](plan-reset-01.md). Slice numbers are historical identifiers, not execution order.

## Goal, split gate, and dependencies

S6 first hardens, packages, stress-tests, and rehearses stable S3 or the best already qualified lower tier through S6-05 and S6-06. It may later lock OBS direction semantics and add staggered-layer interlock through S6-01..04 as an independent optional promotion. Interlock starts/defaults off and is enabled only by dense-subset gain with no safety regression. Optional interlock work never delays the first mandatory submission candidate.

Included: truth-table tests before behavior, OBS candidate relation, host/guest timing, extended exits, interlock-aware exact constraints, topological EXIT serializer, safe ordering rejection, dense A/B, complete stress matrix, package audit, isolated rehearsal, report. Excluded: new non-interlock optimization families or geometry acceleration.

Evidence: design §2.4 branch 3, §2.5, §4.7-4.8, §5 S6, §6; checker entry OBS `603-724`, exit OBS `727-819`, entry present `1160-1198`, exit present `1200-1232`, Stage-5 live order `1279-1386`. If only `OBS(host;guest)` is true: `a_host<a_guest`, `e_guest<=e_host`, and equal-day exits list guest first. `e_host` may exceed `a_host+dwell_host`; it may not violate release/minimum dwell and any tardiness is exact Z1.

Mandatory prerequisite: the clean S3 selected-default baseline required by PLAN-RESET-01. S6-05/06 may consume enabled S4/S5 only if those tiers already passed their own frozen promotion gates; otherwise their flags are false. Optional S6-01..04 consume the best qualified lower tier available when that track begins. Produces a hardened package/evidence and, independently, an optional interlock tier. Explicit non-dependencies: mandatory packaging does not depend on S4, S5, OBS candidates, or interlock serialization. Failure of interlock safety or gain never invalidates an earlier qualified package.

## Files and symbols

- Create `solver/interlock.py`: `ObsRelation`, `classify_pair`, `InterlockCandidate`, `find_candidates`, `validate_nesting`, `extend_host_exit`, exact net delta.
- Extend `retime.py`/backends with a separately typed interlock request only after S6 tests; extend state placement exit freedom.
- Extend `serialize.py`: dependency DAG, deterministic `topological_exit_order`, `SerializeError`; incumbent rejects serialization failures.
- Harness `package.py` and submission-rehearsal; manifests dense/stress; create `tests/test_interlock.py`, extend serialize/retime, create `test_packaging.py`.

## Slice dependency contracts

| Slice | Prerequisite / consumes | Produces | Explicit non-dependency | Next entry condition |
|---|---|---|---|---|
| S6-05 | Clean qualified S3 or already promoted lower tier | Audited package and stress proof | S4/S5/interlock implementation | Mandatory hardening GREEN |
| S6-06 | S6-05 package/harness | Isolated rehearsal/report and delivery decision | Optional optimization | Final mandatory gate exits 0 |
| S6-01 | S0 geometry/checker contracts, qualified lower tier | OBS truth classification/tests | Candidate behavior | Truth gate GREEN/committed |
| S6-02 | S6-01 relation, lower-tier state | Guarded nested candidates | Extended-exit backend | Third-party checker proof GREEN |
| S6-03 | S6-02 candidates, S2 backends | Interlock retiming/exit extension | Serializer ordering | Backend/checker parity GREEN |
| S6-04 | S6-01…03 dependencies | Topological serializer/rejection and optional promotion candidate | Mandatory package | Order/cycle safety GREEN; then optional qualification |

## Binding execution order

Execute S6-05 then S6-06 first to produce the mandatory delivery candidate. S6-01..04 may run afterward or on a separate optional branch. If interlock later passes promotion and becomes selected, rerun S6-05 and S6-06 on that new clean selected identity; the prior package remains valid evidence and is never overwritten.

## Atomic slices

### S6-01 — OBS direction truth table before behavior

Observable: checker-oracle mushroom fixtures pin neither/host-only/guest-only/both directions, strict entry nesting, non-strict exit nesting, and equal-exit list order. No candidate generator exists yet.

- RED: `cd baseline && $PY -m unittest tests.test_interlock.ObsTruthTableTests -v`; expected missing `classify_pair`, not incorrect fixture schema.
- Minimum implementation: `ObsRelation` and exact `GeomKernel.obs` adapter/classification only. Do not connect to entry or generate candidates.
- GREEN targeted; regress all contract/geometry tests and full suite.
- Checker: each truth row calls official checker and stores expected feasible/stage/order outcomes.
- Evidence: `contract --suite interlock-truth --instances synthetic --seed 20260710`; zero mismatch.
- Commit: `test(s6): lock checker interlock truth table`.

### S6-02 — Candidate generation and nested timing

Observable: only one-direction OBS/equal-level-disjoint pairs generate host/guest candidates; candidate enforces `a_host<a_guest`, `e_guest<=e_host`, checks every third-party pair, and leaves assignment/layout unchanged unless fully valid.

- RED: `cd baseline && $PY -m unittest tests.test_interlock.InterlockCandidateTests.test_host_guest_and_third_party_validation -v`; generator absent.
- Implement candidate activation only for a tardy/no-space guest after non-interlock convergence; exact pair/third-party checks; strict net-gain proposal using checker-weighted deltas. Both/no-direction pairs rejected.
- GREEN targeted/full regression.
- Checker: accepted synthetic candidate Stage 5 feasible; reversed role/same-day entry/third-party conflict rejected and incumbent unchanged.
- Benchmark: `benchmark --stage s6 --component interlock_candidates --instances dense --timelimits 300 --seeds 20260710 --feature interlock=true`; candidate/rejection reason counters required.
- Commit: `feat(s6): add guarded interlock candidates`.

### S6-03 — Exit extension and interlock retiming

Observable: host exit extends only as needed, exact Z1 includes extension, interlock-aware Gurobi/CP-SAT constraints are isomorphic, never-worse total checker objective guards adoption, and backend failure restores lower tier.

- RED: `cd baseline && $PY -m unittest tests.test_interlock.InterlockTimingTests.test_exit_extension_and_backend_parity -v`.
- Implement `e` integer variables with `e>=a+dwell`, nesting, non-interlock disjunctions, hints/starts/timeboxes; exact total objective comparison, not Z1-only, because extension can cost tardiness.
- GREEN targeted; full backend/retime/interlock regression.
- Checker: manual optimum fixture, available backend equality, forced both-backend failure preserves previous operations SHA.
- Stress: P=0 host/guest, equal exit, host due before guest, and nested third party.
- Commit: `feat(s6): retime interlocks with safe exit extension`.

### S6-04 — Topological serializer and candidate rejection

Observable: same-day interlock exits are deterministically guest-first by a dependency DAG; cycles or inability to find a checker-safe next exit raise `SerializeError`, discard only the candidate, and return prior incumbent.

- RED: `cd baseline && $PY -m unittest tests.test_serialize.SerializeTests.test_interlock_topological_order_and_cycle_rejection -v`.
- Implement edges “blocker/guest exits before blocked/host”, stable block-ID ready queue, optional fast-exit confirmation against current present set, cycle/failure exception. Non-interlock output remains byte-identical. The serializer remains the sole operations builder.
- GREEN targeted/full regression.
- Checker: equal-day nested fixture passes only expected order; deliberately cyclic dependency never reaches incumbent and lower-tier output full-checks.
- Stress: `stress --stage s6 --instances synthetic --timelimits 60 --seeds 20260710 --feature interlock=true --feature fault=serializer_cycle,serializer_order`; final output feasible, rejection counter >0.
- Commit: `feat(s6): serialize interlock exits topologically`.

### S6-05 — Mandatory package and stress hardening (execute first)

Observable: reproducible package contains only audited production files, has root `myalgorithm.py`, no absolute paths/prohibited/local files, imports in isolation, and the solver survives the full matrix.

- RED: `cd baseline && $PY -m unittest tests.test_packaging.PackagingTests.test_audited_isolated_package -v`; package builder absent.
- Implement harness package builder (development-only), sorted timestamps/entries, SHA manifest, ≤15 MB, extract/import smoke. Assert `git hash-object baseline/utils.py` equals `HEAD:baseline/utils.py`; never package changed utils, tests, harness, evidence, data, licenses, caches, binaries, or docs. Search extracted text for `/Users/`, worktree path, `file://`, and parent-directory traversal.
- GREEN targeted/full suite.
- Stress matrix: timelimits `{0.5,2,5,12,60,300}`; one bay; one layer; P=0; contact; no preferred bay fit but another fit; maximal tracked/training n; dense; backend import/license/optimize failures; cache pressure; ordinary exception after every incumbent boundary. Portfolio-worker and interlock-serializer faults are included only when those features are already selected on the frozen identity. Every valid case returns Stage 5 feasible, no leak, within time budget tolerance.
- Commit: `build(s6): harden and audit submission package`.

### S6-06 — Isolated rehearsal and final report (execute second)

Observable: the selected lower-tier package runs all training/stress inputs from a new temporary directory with no parent access; final report is complete. This mandatory slice does not decide interlock enablement.

- RED: `cd baseline && $PY -m unittest tests.test_packaging.PackagingTests.test_rehearsal_rejects_parent_dependency -v`; isolated runner behavior absent.
- Implement isolated extraction/cwd, sanitized environment, no network use, import and per-case subprocess/checker, report-ready Markdown/JSON. Record the selected feature flags from the incoming qualified identity; do not enable an optional feature here.
- GREEN targeted/full discovery and exact gate below.
- Commit: `test(s6): complete isolated rehearsal support`. Raw evidence, generated reports, and packages remain untracked; the progress document records their paths and hashes.

Progress updates occur at all required milestones. Evidence is `benchmarks/evidence/s6/...`; packages are `submission-dist/<run_id>/` and removed after hashes/report are stored unless designated last-known-good outside Git. Cleanup requirements: close models/processes, delete extraction temp dirs, and assert no descendants. Packaging/stress/rehearsal safety failure blocks mandatory delivery. Interlock failure disables or blocks only the optional track and leaves the last mandatory package intact.

## Mandatory hardening qualification

```bash
PY=/opt/homebrew/Caskroom/miniforge/base/envs/ogc-2026/bin/python
cd baseline && $PY -m unittest discover -s tests -p 'test_*.py' -v
cd ..
$PY -m baseline.harness.cli stress --stage s6 --instances stress --timelimits 0.5,2,5,12,60,300 --seeds 20260710 --feature interlock=false --feature parallel_portfolio=false --feature fault=backend,after_incumbent
$PY -m baseline.harness.cli submission-rehearsal --instances training,stress --timelimits 5,60,300 --seed 20260710 --isolated
$PY -m baseline.harness.cli gate --stage s6 --latest-complete --commit HEAD
$PY -m baseline.harness.cli report --stage s6 --latest-complete
```

Mandatory S6 PASS: every stress and isolated training case Stage 5 feasible; package root/layout/size/hash/path/prohibited-file checks pass; unmodified checker/reference verified; all injected failures preserve incumbent; no timeout/crash/leak; `COMPLETE` evidence and final report exist. The S6 stage is `COMPLETE` when mandatory PASS holds, even if interlock is disabled. Final defaults record portfolio/interlock independently. There is no next stage; the artifact is the hardened, checker-verified submission candidate.

## Optional interlock promotion qualification

After S6-01..04 safety work passes on a clean optional branch, freeze a new identity and run:

```bash
$PY -m baseline.harness.cli contract --suite interlock-truth --instances synthetic --seed 20260710
$PY -m baseline.harness.cli ab --stage s6 --instances dense --timelimits 300 --seed 20260710,20260711 --feature interlock --a false --b true
$PY -m baseline.harness.cli ab --stage s6 --instances training --timelimits 60 --seed 20260710 --feature interlock --a false --b true
$PY -m baseline.harness.cli stress --stage s6 --instances stress --timelimits 0.5,2,5,12,60,300 --seeds 20260710 --feature interlock=true --feature fault=backend,serializer,after_incumbent
```

Interlock enablement PASS: truth table zero mismatch; all A/B outputs feasible; dense median checker objective strictly lower with at least 25% of dense cases improving; no dense or remaining training case worse than paired interlock-off incumbent; serializer failures safely reject; structural counters prove candidates were tried/accepted. Otherwise the optional feature is `GATE_FAILED_DISABLED` after safety PASS or `BLOCKED` after safety failure, its default remains false, and the existing mandatory package remains the delivery candidate. An enabled result must repeat S6-05/06 on the newly selected identity before replacing that candidate.

Known risks and deferred decisions: OBS direction reversal, nested third-party cycles, host tardiness erasing guest gain, and environment-dependent packaging are the primary risks. Truth tests, total-objective guards, cycle rejection, and isolated rehearsal are mandatory mitigations; no numeric interlock threshold is enabled without dense A/B evidence.
