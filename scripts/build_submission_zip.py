#!/usr/bin/env python3
"""Build the minimal OGC 2026 algorithm submission archive.

The resulting archive deliberately contains only the executable submission
entry point, the official unmodified ``utils.py`` checker, and the solver
package it imports.  It excludes development files, test data, and benchmark
artifacts.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import tempfile
import zipfile
from pathlib import Path
from typing import Iterable


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
BASELINE_ROOT = REPOSITORY_ROOT / "baseline"
SOLVER_ROOT = BASELINE_ROOT / "solver"
MAX_ARCHIVE_BYTES = 15 * 1024 * 1024
ROOT_ENTRYPOINT = "myalgorithm.py"
UTILS_NAME = "utils.py"
# SHA-256 of the organizer-provided baseline/utils.py in this repository.
OFFICIAL_UTILS_SHA256 = "d45aaeafdce8bf80d59d097f655c43313a4951bed43b6628e3b1cf62d4876a94"


class SubmissionArchiveError(RuntimeError):
    """Raised when the archive would not meet the submission contract."""


def submission_members() -> dict[str, Path]:
    """Return the exact, minimal allowlist for this project's submission."""
    entrypoint = BASELINE_ROOT / ROOT_ENTRYPOINT
    if not entrypoint.is_file() or entrypoint.is_symlink():
        raise SubmissionArchiveError(f"missing regular entry point: {entrypoint}")
    utils = BASELINE_ROOT / UTILS_NAME
    if not utils.is_file() or utils.is_symlink():
        raise SubmissionArchiveError(f"missing regular checker: {utils}")
    if sha256_file(utils) != OFFICIAL_UTILS_SHA256:
        raise SubmissionArchiveError(
            f"{utils} does not match the organizer-provided unmodified checker"
        )

    members = {ROOT_ENTRYPOINT: entrypoint, UTILS_NAME: utils}
    if not SOLVER_ROOT.is_dir():
        raise SubmissionArchiveError(f"missing solver directory: {SOLVER_ROOT}")

    for source in sorted(SOLVER_ROOT.rglob("*.py")):
        if not source.is_file() or source.is_symlink():
            raise SubmissionArchiveError(f"solver source must be a regular file: {source}")
        relative = source.relative_to(SOLVER_ROOT).as_posix()
        members[f"solver/{relative}"] = source
    if len(members) == 2:
        raise SubmissionArchiveError("solver package has no Python source files")
    return members


def validate_member_names(names: Iterable[str], expected: Iterable[str]) -> None:
    """Reject unsafe paths and any file outside the explicit source allowlist."""
    actual = tuple(names)
    wanted = tuple(expected)
    if len(actual) != len(set(actual)):
        raise SubmissionArchiveError("archive contains duplicate member paths")
    if set(actual) != set(wanted):
        missing = sorted(set(wanted) - set(actual))
        extra = sorted(set(actual) - set(wanted))
        raise SubmissionArchiveError(f"archive allowlist mismatch; missing={missing}, extra={extra}")
    for required in (ROOT_ENTRYPOINT, UTILS_NAME):
        if required not in actual:
            raise SubmissionArchiveError(f"archive root {required} is missing")

    for name in actual:
        path = Path(name)
        if path.is_absolute() or ".." in path.parts or path.suffix != ".py":
            raise SubmissionArchiveError(f"unsafe archive member: {name}")
        if name not in (ROOT_ENTRYPOINT, UTILS_NAME) and not name.startswith("solver/"):
            raise SubmissionArchiveError(f"member is outside the solver package: {name}")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def build_archive(output: Path) -> dict[str, object]:
    """Write and validate a deterministic submission zip with root entry files."""
    members = submission_members()
    validate_member_names(members, members)
    output.parent.mkdir(parents=True, exist_ok=True)

    temporary_name: str | None = None
    try:
        with tempfile.NamedTemporaryFile(
            prefix=f".{output.stem}.", suffix=".tmp", dir=output.parent, delete=False
        ) as temporary:
            temporary_name = temporary.name
        temporary_path = Path(temporary_name)
        with zipfile.ZipFile(
            temporary_path, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9
        ) as archive:
            for name, source in members.items():
                # Fixed metadata makes repeat builds byte-for-byte reproducible.
                info = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
                info.compress_type = zipfile.ZIP_DEFLATED
                info.external_attr = 0o100644 << 16
                archive.writestr(info, source.read_bytes(), compress_type=zipfile.ZIP_DEFLATED)

        with zipfile.ZipFile(temporary_path) as archive:
            validate_member_names(archive.namelist(), members)
            invalid = archive.testzip()
            if invalid is not None:
                raise SubmissionArchiveError(f"corrupt archive member: {invalid}")
        size = temporary_path.stat().st_size
        if size > MAX_ARCHIVE_BYTES:
            raise SubmissionArchiveError(
                f"archive is {size:,} bytes; maximum is {MAX_ARCHIVE_BYTES:,} bytes"
            )
        os.replace(temporary_path, output)
        temporary_name = None
    finally:
        if temporary_name is not None:
            Path(temporary_name).unlink(missing_ok=True)

    return {
        "path": str(output),
        "sha256": sha256_file(output),
        "size_bytes": output.stat().st_size,
        "members": list(members),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output",
        type=Path,
        default=REPOSITORY_ROOT / "dist" / "ogc2026_submission.zip",
        help="archive path (default: %(default)s)",
    )
    parser.add_argument(
        "--dry-run", action="store_true", help="print the allowlist without creating an archive"
    )
    args = parser.parse_args()

    if args.dry_run:
        print(json.dumps({"members": list(submission_members())}, indent=2))
        return 0

    output = args.output.resolve()
    print(json.dumps(build_archive(output), indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except SubmissionArchiveError as error:
        raise SystemExit(f"submission archive error: {error}")
