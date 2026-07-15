# OGC 2026 Documentation Index

This index lists the active documentation set. Superseded duplicates and study copies are kept under `docs/archive/`.

## Start Here

- [ONBOARDING.md](ONBOARDING.md): project overview, architecture map, and first local checks.
- [2026-06-30-ogc2026-chat-context-summary-ko.md](2026-06-30-ogc2026-chat-context-summary-ko.md): Korean context summary from the earlier planning discussion.
- [2026-07-05-ogc2026-baseline-partial-fallback-summary-ko.md](2026-07-05-ogc2026-baseline-partial-fallback-summary-ko.md): Korean summary of the baseline partial fallback work.

## Problem Analysis

- [fable/OGC2026_Problem_Analysis.md](fable/OGC2026_Problem_Analysis.md): concise English problem analysis.
- [fable/OGC2026_문제분석.md](fable/OGC2026_문제분석.md): concise Korean problem analysis.
- [ogc2026_problem_statement_analysis_en.md](ogc2026_problem_statement_analysis_en.md): extended English analysis and algorithmic implications.
- [ogc2026_problem_statement_analysis_ko.md](ogc2026_problem_statement_analysis_ko.md): extended Korean analysis and ALNS/SA design guide.

## Active Solver Strategy

- [strategy/solver-design.md](strategy/solver-design.md): clean-slate winning solver design — the design source of truth (2026-07-09).
- [strategy/solver-implementation-plan.md](strategy/solver-implementation-plan.md): implementation spec — modules, ALNS operators, SA acceptance, CP-SAT models, parameters, tests.
- [strategy/operating-plan.md](strategy/operating-plan.md): compact operating plan, benchmark policy, milestones, and context discipline.
- [strategy/idea-bank.md](strategy/idea-bank.md): evidence-gated solver idea bank, including Fable-derived candidates.
- [strategy/experiment-log.md](strategy/experiment-log.md): decision log and benchmark experiment outcomes.
- [strategy/m2-experiment-playbook.md](strategy/m2-experiment-playbook.md): experiment process conventions (branches, result paths, decision states); also governs the S-track.
- [strategy/m1-submission-safety-plan.md](strategy/m1-submission-safety-plan.md): M1 safety contract and implementation checklist.
- [strategy/m1-submission-safety-plan-kr.md](strategy/m1-submission-safety-plan-kr.md): Korean version of the M1 safety contract and implementation checklist.
- [strategy/m2-m4-execution-plan.md](strategy/m2-m4-execution-plan.md): superseded 2026-07-09 by the solver design; retained for its measured diagnosis (§1) and evaluator-semantics contract (§2).

## Active Solver Performance Work

- [implementation/sol/10_PERFORMANCE_ACCELERATION_PLAN.md](implementation/sol/10_PERFORMANCE_ACCELERATION_PLAN.md): long-form Python/Numba/native performance strategy and profiling policy.
- [implementation/sol/performance/README.md](implementation/sol/performance/README.md): prioritized hard-10 execution playbook, shared promotion gates, and links to the stage-specific prompts.

## Fable Strategy And Plans

- [fable/2026-07-02-ogc2026-strategy-design.md](fable/2026-07-02-ogc2026-strategy-design.md): strategy design document.
- [fable/fable-plan-2026-07-04-ko.md](fable/fable-plan-2026-07-04-ko.md): Korean algorithm development plan.
- [fable/plan/2026-07-02-ogc2026-week1-core-decoder-hw.md](fable/plan/2026-07-02-ogc2026-week1-core-decoder-hw.md): week 1 implementation contract.
- [fable/plan/2026-07-02-ogc2026-week2-alns.md](fable/plan/2026-07-02-ogc2026-week2-alns.md): week 2 ALNS implementation contract.
- [fable/plan/2026-07-02-ogc2026-week3-matheuristics.md](fable/plan/2026-07-02-ogc2026-week3-matheuristics.md): week 3 matheuristics implementation contract.
- [fable/plan/2026-07-02-ogc2026-week4-hardening-submission.md](fable/plan/2026-07-02-ogc2026-week4-hardening-submission.md): week 4 hardening and submission contract.

## Older Superpowers Planning

- [superpowers/specs/2026-06-28-greedy-lns-design.md](superpowers/specs/2026-06-28-greedy-lns-design.md): earlier Greedy + LNS design spec.
- [superpowers/plans/2026-06-28-greedy-lns.md](superpowers/plans/2026-06-28-greedy-lns.md): earlier Greedy + LNS implementation plan.

## Archive Policy

- Exact duplicates should be deleted.
- Study copies, annotated documents, and alternate language/typing variants should move to `docs/archive/YYYY-MM-DD-pruned/`.
- Active plan documents should remain contract-oriented and avoid parallel annotated variants in the main tree.
