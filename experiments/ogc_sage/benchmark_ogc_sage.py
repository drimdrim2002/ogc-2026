"""Step 9 benchmark contract placeholder.

The prerequisite-remediation session intentionally does not run or implement
the 600-run benchmark.  The future hardening step must write one JSON object per
run to ``raw.jsonl`` and an aggregate object to ``summary.json`` under the
gitignored run directory declared below, then record only hashes and summary
evidence in the tracked manifest.
"""

from __future__ import annotations

from pathlib import Path


ARTIFACT_ROOT = Path("artifacts/ogc_sage/step9")
RAW_NAME = "raw.jsonl"
SUMMARY_NAME = "summary.json"
EVIDENCE_MANIFEST = Path("docs/implementation/sol/evidence/step9-evidence.json")

RAW_REQUIRED_FIELDS = (
    "instance",
    "budget_seconds",
    "seed",
    "stage",
    "objective",
    "elapsed_seconds",
    "exception",
)
SUMMARY_REQUIRED_FIELDS = (
    "run_id",
    "dataset_sha256",
    "config_sha256",
    "commit",
    "run_count",
    "stage5_count",
    "exception_count",
    "objective_parity_max_relative_error",
)


def main() -> None:
    raise SystemExit(
        "Step 9 benchmark is not implemented or run during prerequisite remediation."
    )


if __name__ == "__main__":
    main()
