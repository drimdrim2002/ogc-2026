# S5 Execution Plan: Parallel Portfolio

Parent: [`../fable-native-implementation-progress.md`](../fable-native-implementation-progress.md)

Status: `NOT_STARTED`; gate: `NOT_RUN`; planned slices: 4

## Goal, optional semantics, and dependencies

S5 evaluates a process portfolio of diverse S4 solvers under the four-core limit. It owns worker spawning, seed/profile diversity, verified best exchange, worker/license failure isolation, backend thread limiting, process cleanup, and server-like evidence. It begins and remains disabled until the gain and safety gate passes.

Included: orchestrator/worker protocol, 2/3-worker configurations, periodic exchange of serialized checker-verified incumbents, crash/timeout cleanup, resource telemetry, license behavior, server-like test. Excluded: new search operators, assignment logic, and interlock.

Evidence: design §3 lever 7, §4.9, §5 S5; problem analysis §5.4 (4 cores, 16 GB, Ubuntu 24.04, no internet). The orchestrator never trusts a worker objective: it rechecks each exchanged operations dict with the official checker before replacing its incumbent.

Prerequisite: S4 `COMPLETE`. Consumes the complete single-process entry with config override and harness process utilities. Produces an optional `portfolio.py` path. Explicit non-dependencies: no S6 OBS/interlock/serializer extension. S6 entry accepts S5 `COMPLETE` or `GATE_FAILED_DISABLED`. If gain, license, Linux, cleanup, or resource proof fails, set S5 `GATE_FAILED_DISABLED`, keep `parallel_portfolio=false`, and preserve submission-ready S4.

## Files/symbols

- Create `solver/portfolio.py`: `WorkerProfile`, `ExchangeMessage`, `worker_main`, `PortfolioOrchestrator`, `verify_exchange`, `shutdown_workers`.
- Extend config/entry/exact backends: worker mode forces backend threads=1; single-process remains ≤4.
- Extend harness process/resource telemetry and reports; create `tests/test_portfolio.py`.

## Slice dependency contracts

| Slice | Prerequisite / consumes | Produces | Explicit non-dependency | Next entry condition |
|---|---|---|---|---|
| S5-01 | Complete S4 callable/config | Spawned diverse workers | Exchange/interlock | Worker isolation GREEN |
| S5-02 | S5-01 messages, S0 checker adapter | Verified exchange | Resource gate | Forged/stale rejection GREEN |
| S5-03 | S5-01/02 processes | Bounded cleanup/fault isolation | S6 behavior | Leak/resource stress GREEN |
| S5-04 | All S5 artifacts, Linux host | Optional enabled/disabled decision | Interlock | PASS or `GATE_FAILED_DISABLED` |

## Atomic slices

### S5-01 — Spawned worker protocol and diversity

Observable: two spawned workers receive immutable problem/config, distinct deterministic seed/profile/backend preference, and return structured terminal messages without shared solver objects.

- RED: `cd baseline && $PY -m unittest tests.test_portfolio.PortfolioTests.test_worker_profiles_and_no_solver_object_sharing -v`; missing module.
- Implement `multiprocessing` `spawn` context (portable and avoids inherited Gurobi environments), pure dict messages, seeds `base+104729*worker_id`, profiles fixed before launch, child lazy backend init.
- GREEN targeted and full prior regression.
- Checker: each worker’s final message is independently full-checked on example; invalid message rejected.
- Stress: worker initialization/license exception yields terminal failure record while orchestrator retains S4 incumbent.
- Commit: `feat(s5): add isolated portfolio workers`.

### S5-02 — Verified best exchange

Observable: workers publish only their locally verified serialized incumbent; orchestrator rechecks and broadcasts a strictly better immutable incumbent snapshot at deterministic exchange epochs.

- RED: `cd baseline && $PY -m unittest tests.test_portfolio.PortfolioTests.test_unverified_exchange_is_rejected -v`.
- Implement bounded queues, sequence/run IDs, checker verification, stale/wrong-instance rejection, epoch no faster than five seconds, atomic worker adoption at iteration boundary. Exchange is best-result only, not mutable state/model transfer.
- GREEN targeted and regression.
- Checker: forged objective/infeasible operations rejected; valid improvement accepted with two verification records.
- Benchmark: `benchmark --stage s5 --instances smoke-3 --timelimits 60 --seeds 20260710 --feature parallel_portfolio=true --feature workers=2 --feature exact_threads=1`; exchange sent/verified/adopted counters required.
- Commit: `feat(s5): exchange only verified portfolio incumbents`.

### S5-03 — Resource limits, failure isolation, and cleanup

Observable: max workers 3, every backend thread 1 in worker mode, active solver processes+backend threads stay within four cores, and termination leaves no descendants/queues/models.

- RED: `cd baseline && $PY -m unittest tests.test_portfolio.PortfolioTests.test_crash_timeout_and_cleanup -v`.
- Implement orchestrator-as-idle coordinator, configurations `{2,3}` workers only, runtime assertions, psutil child/RSS/CPU telemetry, TERM/two-second/KILL cleanup, queue close/join, per-child backend disposal. Any worker death is ignored after recording; all-worker failure returns orchestrator’s initial S4 incumbent.
- GREEN targeted/full regression.
- Stress: `stress --stage s5 --instances smoke-3 --timelimits 60 --seeds 20260710 --feature parallel_portfolio=true --feature workers=3 --feature exact_threads=1 --feature fault=worker_crash,worker_hang,license`; zero leaked PID, feasible final result.
- Commit: `feat(s5): bound and clean portfolio resources`.

### S5-04 — Server-like gain gate and optional integration

Observable: entry can invoke portfolio behind a false-by-default flag; a Linux/Ubuntu-like four-core/16-GB check proves or rejects enablement.

- RED: `cd baseline && $PY -m unittest tests.test_budget_entry.EntryArmorTests.test_portfolio_failure_keeps_s4 -v`; no entry branch.
- Integrate only behind flag. Harness `--server-like` requires Linux, visible CPU quota ≤4, memory limit ≤16 GiB, no network dependency, `spawn` success, and records `/proc` quota/memory facts; if environment cannot prove these, gate cannot enable and S5 ends disabled. Evaluate workers `{2,3}` at exact threads 1.
- GREEN targeted/full discovery; run gate below on server-like host.
- Commit: `feat(s5): gate parallel portfolio integration`.

Update progress at required moments. Evidence `benchmarks/evidence/s5/...`. Cleanup is itself a gate: terminate process groups, close queues, dispose solver environments, delete temp sockets/files; never mark `COMPLETE` with a live PID. On any optional failure, record exact reason and S4 fallback evidence rather than blocking S6.

## Optional enablement gate

```bash
PY=/opt/homebrew/Caskroom/miniforge/base/envs/ogc-2026/bin/python
cd baseline && $PY -m unittest discover -s tests -p 'test_*.py' -v
cd ..
$PY -m baseline.harness.cli ab --stage s5 --instances dev-10 --timelimits 60,300 --seed 20260710,20260711 --feature parallel_portfolio --a false --b workers=2,workers=3 --feature exact_threads=1 --server-like
$PY -m baseline.harness.cli stress --stage s5 --instances stress --timelimits 12,60 --seeds 20260710 --feature parallel_portfolio=true --feature workers=3 --feature exact_threads=1 --feature fault=worker_crash,worker_hang,license --server-like
$PY -m baseline.harness.cli gate --stage s5 --latest-complete --commit HEAD
$PY -m baseline.harness.cli report --stage s5 --latest-complete
```

PASS/enable: every final output Stage 5 feasible, zero orchestrator/worker crash in non-fault A/B, zero leaked processes, max workers ≤3, every worker backend threads=1, observed aggregate core use ≤4 and peak RSS <16 GiB; at 300 seconds the selected portfolio has strictly lower median checker objective than S4 and reaches the paired S4 300-second final objective by ≤240 seconds on at least half of cases; results repeat directionally across both seeds; server-like preflight passes. Select fewer workers on ties.

If safety/resource cleanup fails, the feature is disabled and failure is serious but does not corrupt S4. If only measurable gain, license concurrency, or server-like proof fails, set stage `GATE_FAILED_DISABLED`, `parallel_portfolio=false`, record fallback reason and S4 checker evidence, and proceed to S6. Feature default is true only after full PASS; otherwise false. Proposed commits are the four slice messages above.

Known risks and deferred decisions: concurrent Gurobi licensing, spawn overhead, queue backpressure, and OS resource accounting may erase gains. Worker count is selected only from `{2,3}` by the server-like gate. Next-stage entry: S6 starts after S5 `COMPLETE` or `GATE_FAILED_DISABLED`, clean status, and a recorded enabled/disabled reason.
