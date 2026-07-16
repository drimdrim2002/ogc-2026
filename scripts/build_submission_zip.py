#!/usr/bin/env python3
"""Build the minimal OGC 2026 algorithm submission archive.

Native packaging is the default and requires the Linux package directory made
by ``scripts/build_native_linux.sh``.  Pure-Python packaging is available only
through the explicit ``--python-only`` option.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import tempfile
import zipfile
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from typing import Iterable
from zoneinfo import ZoneInfo


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
BASELINE_ROOT = REPOSITORY_ROOT / "baseline"
SOLVER_ROOT = BASELINE_ROOT / "solver"
MAX_ARCHIVE_BYTES = 15_000_000
ROOT_ENTRYPOINT = "myalgorithm.py"
UTILS_NAME = "utils.py"
SEOUL_TIMEZONE = ZoneInfo("Asia/Seoul")
DEFAULT_OUTPUT_DIRECTORY = REPOSITORY_ROOT / "dist"
# SHA-256 of the organizer-provided baseline/utils.py in this repository.
OFFICIAL_UTILS_SHA256 = "d45aaeafdce8bf80d59d097f655c43313a4951bed43b6628e3b1cf62d4876a94"

_NATIVE_EXTENSION_NAME = re.compile(
    r"^_ogc_native[^/]*\.cpython-312-[^/]*(?:x86_64|amd64)[^/]*\.so$"
)
_SHARED_LIBRARY_NAME = re.compile(r"^[^/]+\.so(?:\.[A-Za-z0-9_.+-]+)*$")
_READELF_NEEDED = re.compile(r"\(NEEDED\).*Shared library: \[([^]]+)]")
_READELF_RUNTIME_PATH = re.compile(r"\((?:RPATH|RUNPATH)\).*Library r(?:un)?path: \[([^]]*)]")
_OBJDUMP_NEEDED = re.compile(r"^\s*NEEDED\s+(\S+)", re.MULTILINE)
_OBJDUMP_RUNTIME_PATH = re.compile(r"^\s*(?:RPATH|RUNPATH)\s+(\S*)", re.MULTILINE)
_SYSTEM_LIBRARY_NAMES = (
    re.compile(r"^libc\.so(?:\..*)?$"),
    re.compile(r"^libm\.so(?:\..*)?$"),
    re.compile(r"^libdl\.so(?:\..*)?$"),
    re.compile(r"^libpthread\.so(?:\..*)?$"),
    re.compile(r"^librt\.so(?:\..*)?$"),
    re.compile(r"^libutil\.so(?:\..*)?$"),
    re.compile(r"^libresolv\.so(?:\..*)?$"),
    re.compile(r"^libstdc\+\+\.so(?:\..*)?$"),
    re.compile(r"^libgcc_s\.so(?:\..*)?$"),
    re.compile(r"^libpython3\.12\.so(?:\..*)?$"),
    re.compile(r"^ld-linux-x86-64\.so(?:\..*)?$"),
)


class SubmissionArchiveError(RuntimeError):
    """Raised when the archive would not meet the submission contract."""


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def default_output_path(now: datetime | None = None) -> Path:
    """Return the date-stamped default path using the Asia/Seoul build date."""
    instant = now or datetime.now(SEOUL_TIMEZONE)
    if instant.tzinfo is None:
        instant = instant.replace(tzinfo=SEOUL_TIMEZONE)
    build_date = instant.astimezone(SEOUL_TIMEZONE).strftime("%Y%m%d")
    return DEFAULT_OUTPUT_DIRECTORY / f"ogc2026_submission_{build_date}.zip"


def _regular_file(path: Path, description: str) -> None:
    if not path.is_file() or path.is_symlink():
        raise SubmissionArchiveError(f"{description} must be a regular file: {path}")


def _python_submission_members() -> dict[str, Path]:
    entrypoint = BASELINE_ROOT / ROOT_ENTRYPOINT
    _regular_file(entrypoint, "entry point")
    utils = BASELINE_ROOT / UTILS_NAME
    _regular_file(utils, "checker")
    if sha256_file(utils) != OFFICIAL_UTILS_SHA256:
        raise SubmissionArchiveError(
            f"{utils} does not match the organizer-provided unmodified checker"
        )

    members = {ROOT_ENTRYPOINT: entrypoint, UTILS_NAME: utils}
    if not SOLVER_ROOT.is_dir():
        raise SubmissionArchiveError(f"missing solver directory: {SOLVER_ROOT}")

    for source in sorted(SOLVER_ROOT.rglob("*.py")):
        relative_path = source.relative_to(SOLVER_ROOT)
        lowered_parts = tuple(part.lower() for part in relative_path.parts)
        if (
            "__pycache__" in lowered_parts
            or "tests" in lowered_parts
            or relative_path.name.startswith("test_")
        ):
            continue
        _regular_file(source, "solver source")
        members[f"solver/{relative_path.as_posix()}"] = source
    if len(members) == 2:
        raise SubmissionArchiveError("solver package has no Python source files")
    return members


def _validate_elf_amd64(path: Path) -> None:
    """Reject non-ELF and non-amd64 package binaries without executing them."""
    with path.open("rb") as handle:
        header = handle.read(20)
    if len(header) < 20 or header[:4] != b"\x7fELF":
        raise SubmissionArchiveError(f"native binary is not ELF: {path}")
    if header[4] != 2 or header[5] != 1:
        raise SubmissionArchiveError(f"native binary must be 64-bit little-endian ELF: {path}")
    if int.from_bytes(header[18:20], "little") != 62:
        raise SubmissionArchiveError(f"native binary must target amd64/x86_64: {path}")


def _is_system_library(name: str) -> bool:
    return any(pattern.fullmatch(name) for pattern in _SYSTEM_LIBRARY_NAMES)


def _readelf_dynamic(path: Path, readelf: str) -> tuple[set[str], tuple[str, ...]]:
    completed = subprocess.run(
        [readelf, "-d", str(path)],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        check=False,
    )
    if completed.returncode != 0:
        detail = completed.stderr.strip() or completed.stdout.strip()
        raise SubmissionArchiveError(f"readelf failed for {path}: {detail}")
    needed = set(_READELF_NEEDED.findall(completed.stdout))
    runtime_paths = tuple(_READELF_RUNTIME_PATH.findall(completed.stdout))
    return needed, runtime_paths


def _objdump_dynamic(path: Path, objdump: str) -> tuple[set[str], tuple[str, ...]]:
    completed = subprocess.run(
        [objdump, "-p", str(path)],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        check=False,
    )
    if completed.returncode != 0:
        detail = completed.stderr.strip() or completed.stdout.strip()
        raise SubmissionArchiveError(f"objdump failed for {path}: {detail}")
    needed = set(_OBJDUMP_NEEDED.findall(completed.stdout))
    runtime_paths = tuple(_OBJDUMP_RUNTIME_PATH.findall(completed.stdout))
    return needed, runtime_paths


def _validate_runtime_paths(path: Path, runtime_paths: Iterable[str]) -> None:
    for raw in runtime_paths:
        for item in raw.split(":"):
            if item.strip().startswith("/"):
                raise SubmissionArchiveError(
                    f"absolute RPATH/RUNPATH is forbidden in {path}: {item}"
                )


def _validate_ldd(extension: Path, ldd: str, provided: dict[str, Path]) -> None:
    completed = subprocess.run(
        [ldd, str(extension)],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        check=False,
    )
    output = f"{completed.stdout}\n{completed.stderr}"
    if completed.returncode != 0 or "not found" in output:
        raise SubmissionArchiveError(
            f"ldd could not resolve native extension dependencies: {output.strip()}"
        )
    for name, source in provided.items():
        match = re.search(rf"^\s*{re.escape(name)}\s+=>\s+(\S+)", output, re.MULTILINE)
        if match and Path(match.group(1)).resolve() != source.resolve():
            raise SubmissionArchiveError(
                f"native extension resolves {name} outside its package: {match.group(1)}"
            )


def _native_package_members(native_package: Path) -> tuple[dict[str, Path], dict[str, object]]:
    package = native_package.resolve()
    if not package.is_dir():
        raise SubmissionArchiveError(f"native package directory does not exist: {native_package}")

    extensions = sorted(package.glob("_ogc_native*.so"))
    if not extensions:
        raise SubmissionArchiveError(
            f"native package has no root _ogc_native*.so extension: {native_package}"
        )
    if len(extensions) != 1:
        raise SubmissionArchiveError(
            f"native package must contain exactly one extension; found: {[item.name for item in extensions]}"
        )
    extension = extensions[0]
    _regular_file(extension, "native extension")
    if not _NATIVE_EXTENSION_NAME.fullmatch(extension.name):
        raise SubmissionArchiveError(
            "native extension filename must identify CPython 3.12 and amd64: "
            f"{extension.name}"
        )
    _validate_elf_amd64(extension)

    library_dir = package / "lib"
    candidates = sorted(library_dir.glob("*.so*")) if library_dir.is_dir() else []
    if not candidates:
        raise SubmissionArchiveError(
            f"native package has no bundled shared libraries under {library_dir}"
        )
    provided: dict[str, Path] = {}
    for library in candidates:
        _regular_file(library, "bundled shared library")
        if not _SHARED_LIBRARY_NAME.fullmatch(library.name):
            raise SubmissionArchiveError(f"invalid bundled shared library name: {library.name}")
        if library.name in provided:
            raise SubmissionArchiveError(f"duplicate bundled shared library name: {library.name}")
        _validate_elf_amd64(library)
        provided[library.name] = library

    readelf = shutil.which("readelf")
    objdump = shutil.which("objdump")
    ldd = shutil.which("ldd")
    selected: dict[str, Path] = {}
    dynamic_reader: Callable[[Path], tuple[set[str], tuple[str, ...]]] | None = None
    inspection = "ELF-header-only"
    if readelf is not None:
        dynamic_reader = lambda path: _readelf_dynamic(path, readelf)
        inspection = "readelf"
    elif objdump is not None:
        dynamic_reader = lambda path: _objdump_dynamic(path, objdump)
        inspection = "objdump"

    if dynamic_reader is not None:
        pending = [extension]
        inspected: set[Path] = set()
        while pending:
            binary = pending.pop()
            if binary in inspected:
                continue
            inspected.add(binary)
            needed, runtime_paths = dynamic_reader(binary)
            _validate_runtime_paths(binary, runtime_paths)
            for name in sorted(needed):
                bundled = provided.get(name)
                if bundled is not None:
                    if name not in selected:
                        selected[name] = bundled
                        pending.append(bundled)
                elif not _is_system_library(name):
                    raise SubmissionArchiveError(
                        f"required non-system shared library is missing: {name} (needed by {binary.name})"
                    )
    else:
        # The Linux build host has readelf; this fallback still packages only
        # the native package's explicit lib/*.so* candidates on other hosts.
        selected = dict(provided)
        inspection = "ldd" if ldd else inspection

    if not selected:
        raise SubmissionArchiveError(
            "native extension has no required bundled non-system shared libraries"
        )
    if ldd:
        _validate_ldd(extension, ldd, selected)

    members = {f"solver/{extension.name}": extension}
    members.update({f"solver/lib/{name}": path for name, path in sorted(selected.items())})
    evidence: dict[str, object] = {
        "extension": extension.name,
        "dynamic_dependency_inspection": inspection,
        "included_shared_libraries": sorted(selected),
    }
    return members, evidence


def _submission_plan(
    native_package: Path | None = None, *, python_only: bool = False
) -> tuple[dict[str, Path], dict[str, object]]:
    """Return the exact archive allowlist and native validation evidence."""
    if python_only:
        if native_package is not None:
            raise SubmissionArchiveError("--python-only cannot be combined with --native-package")
        members = _python_submission_members()
        return members, {"mode": "python-only"}
    if native_package is None:
        raise SubmissionArchiveError(
            "native submission is the default; provide --native-package <linux-package-directory> "
            "or explicitly request --python-only"
        )
    members = _python_submission_members()
    native_members, evidence = _native_package_members(native_package)
    for name, source in native_members.items():
        if name in members:
            raise SubmissionArchiveError(f"duplicate computed archive path: {name}")
        members[name] = source
    evidence["mode"] = "native"
    return members, evidence


def submission_members(
    native_package: Path | None = None, *, python_only: bool = False
) -> dict[str, Path]:
    """Return the exact archive allowlist for the selected submission mode."""
    return _submission_plan(native_package, python_only=python_only)[0]


def validate_member_names(names: Iterable[str], expected: Iterable[str]) -> None:
    """Reject unsafe paths and any file outside the explicit computed allowlist."""
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
        if path.is_absolute() or ".." in path.parts or "__pycache__" in path.parts:
            raise SubmissionArchiveError(f"unsafe archive member: {name}")
        allowed = (
            name in (ROOT_ENTRYPOINT, UTILS_NAME)
            or (name.startswith("solver/") and path.suffix == ".py")
            or (
                path.parent == Path("solver")
                and _NATIVE_EXTENSION_NAME.fullmatch(path.name) is not None
            )
            or (
                path.parent == Path("solver/lib")
                and _SHARED_LIBRARY_NAME.fullmatch(path.name) is not None
            )
        )
        if not allowed:
            raise SubmissionArchiveError(f"member is outside the submission allowlist: {name}")


def build_archive(
    output: Path, native_package: Path | None = None, *, python_only: bool = False
) -> dict[str, object]:
    """Write and validate a deterministic native or explicit Python-only zip."""
    members, mode_evidence = _submission_plan(native_package, python_only=python_only)
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
        "validation": mode_evidence,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument(
        "--native-package",
        type=Path,
        help="Linux package directory produced by scripts/build_native_linux.sh",
    )
    mode.add_argument(
        "--python-only",
        action="store_true",
        help="explicitly build the legacy Python-only submission",
    )
    parser.add_argument(
        "--output",
        type=Path,
        help="archive path (default: dist/ogc2026_submission_YYYYMMDD.zip in Asia/Seoul)",
    )
    parser.add_argument(
        "--dry-run", action="store_true", help="print the computed allowlist without creating an archive"
    )
    args = parser.parse_args()

    if args.dry_run:
        members, evidence = _submission_plan(
            args.native_package, python_only=args.python_only
        )
        print(json.dumps({"members": list(members), "validation": evidence}, indent=2))
        return 0

    output = args.output if args.output is not None else default_output_path()
    print(
        json.dumps(
            build_archive(output, args.native_package, python_only=args.python_only),
            indent=2,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except SubmissionArchiveError as error:
        raise SystemExit(f"submission archive error: {error}")
