# DOCS KNOWLEDGE BASE

## OVERVIEW

Planning, analysis, onboarding, strategy, experiment notes, and archived context
for the OGC 2026 workspace. This is not the runnable source surface.

## STRUCTURE

```text
docs/
|-- INDEX.md                         # navigation anchor
|-- ONBOARDING.md                    # architecture/workflow overview
|-- strategy/                        # operating plans and experiment logs
|-- fable/                           # problem analysis and multi-week plans
|-- archive/2026-07-05-pruned/       # historical/pruned planning copies
`-- superpowers/                     # older generated plans/specs
```

## WHERE TO LOOK

| Task | Location | Notes |
| --- | --- | --- |
| Find current docs | `INDEX.md` | Update when adding/removing active docs. |
| Onboard to repo | `ONBOARDING.md` | Human-readable architecture and workflow. |
| Experiment records | `strategy/experiment-log.md` | Commands, results, and interpretation. |
| Solver planning | `strategy/`, `fable/plan/` | Active plans and milestone notes. |
| Problem analysis | `ogc2026_problem_statement_analysis_*.md`, `fable/` | English/Korean analysis files. |
| Historical context | `archive/2026-07-05-pruned/` | Do not treat as active instructions. |

## CONVENTIONS

- Keep active navigation in `INDEX.md` accurate when docs move.
- Preserve Korean and English variants unless the user asks to prune one.
- Put new benchmark evidence near `strategy/experiment-log.md` or a clearly
  named strategy note, not under `archive/`.
- Archive files are read-mostly historical context; edit them only for explicit
  archival cleanup.
- Commands in docs may be historical. Verify current runnable commands against
  root `AGENTS.md`, `README.txt`, and the actual scripts before relying on them.

## ANTI-PATTERNS

- Do not commit generated PDFs/zips derived from docs.
- Do not delete old planning context just because it is superseded; move or
  archive only with a clear reason.
- Do not let docs claim benchmark/objective impact without a matching result
  artifact or command transcript.
