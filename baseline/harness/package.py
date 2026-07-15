"""Development-only audited submission package builder for S6."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import shutil
import subprocess
import sys
import tempfile
from typing import Iterable
import zipfile

from .process import run_process
from .selectors import REPO_ROOT


MAX_ARCHIVE_BYTES = 15 * 1024 * 1024
ZIP_TIMESTAMP = (1980, 1, 1, 0, 0, 0)
ARCHIVE_NAME = "submission.zip"
MANIFEST_NAME = "submission-manifest.json"
PROTECTED_PATHS = (
    "baseline/utils.py",
    "baseline/baseline_greedy.py",
)
PROHIBITED_PARTS = frozenset(
    {
        "__pycache__",
        "benchmarks",
        "data",
        "docs",
        "evidence",
        "harness",
        "tests",
    }
)
PROHIBITED_SUFFIXES = frozenset(
    {
        ".bin",
        ".dll",
        ".dylib",
        ".json",
        ".lic",
        ".license",
        ".pdb",
        ".pdf",
        ".pyc",
        ".pyo",
        ".so",
        ".zip",
    }
)


class StageUnsupportedError(RuntimeError):
    """Raised when the requested harness stage has not been implemented."""


class PackageAuditError(RuntimeError):
    """Raised when a source tree or generated package violates S6 policy."""


@dataclass(frozen=True, slots=True)
class PackageEntry:
    path: str
    sha256: str
    size_bytes: int


@dataclass(frozen=True, slots=True)
class PackageAudit:
    members: tuple[str, ...]
    path_violations: tuple[str, ...]
    prohibited_members: tuple[str, ...]
    prohibited_text_hits: tuple[str, ...]
    import_smoke_passed: bool
    checker_smoke_stage: int
    smoke_stdout: str
    temporary_paths_cleaned: bool


@dataclass(frozen=True, slots=True)
class PackageArtifact:
    archive_path: Path
    manifest_path: Path
    archive_sha256: str
    size_bytes: int
    entries: tuple[PackageEntry, ...]
    audit: PackageAudit


def build_submission_package(
    output_dir: Path | str,
    *,
    source_root: Path | str = REPO_ROOT,
) -> PackageArtifact:
    """Build and audit a deterministic production-only submission archive."""

    root = Path(source_root).resolve()
    destination = Path(output_dir)
    if destination.exists():
        raise PackageAuditError(f"package output already exists: {destination}")
    destination.mkdir(parents=True)

    protected = _protected_objects(root)
    sources = _production_sources(root)
    _assert_clean_tracked_sources(root, sources)
    entries = tuple(
        PackageEntry(
            path=member,
            sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
            size_bytes=path.stat().st_size,
        )
        for member, path in sources
    )
    archive_path = destination / ARCHIVE_NAME
    _write_archive(archive_path, sources)
    size_bytes = archive_path.stat().st_size
    if size_bytes > MAX_ARCHIVE_BYTES:
        raise PackageAuditError(
            f"submission archive is {size_bytes} bytes; limit is {MAX_ARCHIVE_BYTES}"
        )
    archive_sha256 = hashlib.sha256(archive_path.read_bytes()).hexdigest()
    audit = audit_submission_package(
        archive_path,
        source_root=root,
        expected_members=tuple(entry.path for entry in entries),
    )
    manifest_path = destination / MANIFEST_NAME
    manifest = {
        "schema_version": 1,
        "archive": ARCHIVE_NAME,
        "archive_sha256": archive_sha256,
        "archive_size_bytes": size_bytes,
        "archive_limit_bytes": MAX_ARCHIVE_BYTES,
        "zip_timestamp": list(ZIP_TIMESTAMP),
        "entries": [
            {
                "path": entry.path,
                "sha256": entry.sha256,
                "size_bytes": entry.size_bytes,
            }
            for entry in entries
        ],
        "protected_files": protected,
        "audit": {
            "members": list(audit.members),
            "path_violations": list(audit.path_violations),
            "prohibited_members": list(audit.prohibited_members),
            "prohibited_text_hits": list(audit.prohibited_text_hits),
            "import_smoke_passed": audit.import_smoke_passed,
            "checker_smoke_stage": audit.checker_smoke_stage,
            "temporary_paths_cleaned": audit.temporary_paths_cleaned,
        },
    }
    manifest_path.write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return PackageArtifact(
        archive_path=archive_path,
        manifest_path=manifest_path,
        archive_sha256=archive_sha256,
        size_bytes=size_bytes,
        entries=entries,
        audit=audit,
    )


def audit_submission_package(
    archive_path: Path | str,
    *,
    source_root: Path | str = REPO_ROOT,
    expected_members: Iterable[str] | None = None,
) -> PackageAudit:
    """Reject unsafe content and import the extracted package in isolation."""

    archive = Path(archive_path)
    root = Path(source_root).resolve()
    expected = None if expected_members is None else tuple(expected_members)
    with zipfile.ZipFile(archive) as handle:
        infos = handle.infolist()
        members = tuple(info.filename for info in infos)
        if members != tuple(sorted(members)):
            raise PackageAuditError("archive entries are not sorted")
        if len(members) != len(set(members)):
            raise PackageAuditError("archive contains duplicate entries")
        if expected is not None and members != expected:
            raise PackageAuditError(
                f"archive member allowlist mismatch: {members!r} != {expected!r}"
            )
        if not members or members[0] != "myalgorithm.py":
            raise PackageAuditError("archive must contain root myalgorithm.py")
        timestamp_mismatches = tuple(
            info.filename for info in infos if info.date_time != ZIP_TIMESTAMP
        )
        if timestamp_mismatches:
            raise PackageAuditError(
                "archive contains non-deterministic timestamps: "
                + ", ".join(timestamp_mismatches)
            )
        path_violations = tuple(
            name for name in members if not _safe_member_path(name)
        )
        prohibited_members = tuple(
            name for name in members if _prohibited_member(name)
        )
        text_hits: list[str] = []
        source_marker = str(root)
        for info in infos:
            encoded = handle.read(info)
            try:
                text = encoded.decode("utf-8")
            except UnicodeDecodeError:
                prohibited_members += (info.filename,)
                continue
            for marker in ("/Users/", source_marker, "file://", "../", "..\\"):
                if marker and marker in text:
                    text_hits.append(f"{info.filename}:{marker}")
        prohibited_text_hits = tuple(sorted(set(text_hits)))
        if path_violations or prohibited_members or prohibited_text_hits:
            raise PackageAuditError(
                "package audit failed: "
                f"paths={path_violations}, members={prohibited_members}, "
                f"text={prohibited_text_hits}"
            )
        payloads = tuple((info.filename, handle.read(info)) for info in infos)

    smoke_passed, smoke_stage, smoke_stdout, cleaned = _isolated_import_smoke(
        payloads,
        root / "baseline" / "utils.py",
    )
    if not smoke_passed or smoke_stage != 5 or not cleaned:
        raise PackageAuditError(
            "isolated package smoke failed: "
            f"passed={smoke_passed}, checker_stage={smoke_stage}, cleaned={cleaned}, "
            f"stdout={smoke_stdout!r}"
        )
    return PackageAudit(
        members=members,
        path_violations=(),
        prohibited_members=(),
        prohibited_text_hits=(),
        import_smoke_passed=True,
        checker_smoke_stage=smoke_stage,
        smoke_stdout=smoke_stdout,
        temporary_paths_cleaned=True,
    )


def _production_sources(root: Path) -> tuple[tuple[str, Path], ...]:
    baseline = root / "baseline"
    candidates = [("myalgorithm.py", baseline / "myalgorithm.py")]
    candidates.extend(
        (f"solver/{path.name}", path)
        for path in sorted((baseline / "solver").glob("*.py"))
    )
    sources = tuple(sorted(candidates, key=lambda item: item[0]))
    if not sources or any(not path.is_file() or path.is_symlink() for _, path in sources):
        raise PackageAuditError("production allowlist contains a missing file or symlink")
    if any(_prohibited_member(member) for member, _ in sources):
        raise PackageAuditError("production allowlist contains a prohibited path")
    return sources


def _protected_objects(root: Path) -> dict[str, dict[str, str]]:
    protected: dict[str, dict[str, str]] = {}
    for relative in PROTECTED_PATHS:
        worktree_object = _git(root, "hash-object", relative)
        head_object = _git(root, "rev-parse", f"HEAD:{relative}")
        if worktree_object != head_object:
            raise PackageAuditError(
                f"protected file differs from HEAD: {relative} "
                f"({worktree_object} != {head_object})"
            )
        protected[relative] = {
            "worktree_object": worktree_object,
            "head_object": head_object,
        }
    return protected


def _assert_clean_tracked_sources(
    root: Path,
    sources: tuple[tuple[str, Path], ...],
) -> None:
    relative_paths = tuple(
        str(path.relative_to(root)) for _, path in sources
    )
    for relative in relative_paths:
        _git(root, "ls-files", "--error-unmatch", relative)
    result = subprocess.run(
        ["git", "diff", "--quiet", "HEAD", "--", *relative_paths],
        cwd=root,
        check=False,
    )
    if result.returncode != 0:
        raise PackageAuditError("packaged production sources differ from HEAD")


def _write_archive(
    archive_path: Path,
    sources: tuple[tuple[str, Path], ...],
) -> None:
    with zipfile.ZipFile(
        archive_path,
        mode="w",
        compression=zipfile.ZIP_DEFLATED,
        compresslevel=9,
        strict_timestamps=True,
    ) as archive:
        for member, path in sources:
            info = zipfile.ZipInfo(member, date_time=ZIP_TIMESTAMP)
            info.compress_type = zipfile.ZIP_DEFLATED
            info.create_system = 3
            info.external_attr = 0o100644 << 16
            archive.writestr(
                info,
                path.read_bytes(),
                compress_type=zipfile.ZIP_DEFLATED,
                compresslevel=9,
            )


def _safe_member_path(name: str) -> bool:
    path = PurePosixPath(name)
    return (
        bool(name)
        and not name.startswith("/")
        and not name.endswith("/")
        and "\\" not in name
        and path.parts
        and all(part not in {"", ".", ".."} for part in path.parts)
        and str(path) == name
    )


def _prohibited_member(name: str) -> bool:
    path = PurePosixPath(name)
    return (
        not _safe_member_path(name)
        or path.suffix.lower() in PROHIBITED_SUFFIXES
        or path.suffix.lower() != ".py"
        or any(part.lower() in PROHIBITED_PARTS for part in path.parts)
        or (len(path.parts) == 1 and name != "myalgorithm.py")
        or (len(path.parts) > 1 and path.parts[0] != "solver")
    )


def _isolated_import_smoke(
    payloads: tuple[tuple[str, bytes], ...],
    checker_path: Path,
) -> tuple[bool, int, str, bool]:
    temporary = Path(tempfile.mkdtemp(prefix="fable-s6-package-"))
    smoke_stage = 0
    stdout = ""
    passed = False
    try:
        extracted = temporary / "submission"
        runtime = temporary / "official-runtime"
        extracted.mkdir()
        runtime.mkdir()
        for member, payload in payloads:
            target = extracted.joinpath(*PurePosixPath(member).parts)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(payload)
        (runtime / "utils.py").write_bytes(checker_path.read_bytes())
        code = _smoke_program()
        env = {
            "HOME": str(temporary / "home"),
            "LC_ALL": "C",
            "PATH": os.environ.get("PATH", ""),
            "PYTHONDONTWRITEBYTECODE": "1",
            "PYTHONHASHSEED": "0",
            "TMPDIR": str(temporary / "tmp"),
        }
        Path(env["HOME"]).mkdir()
        Path(env["TMPDIR"]).mkdir()
        result = run_process(
            [sys.executable, "-I", "-c", code, str(extracted), str(runtime)],
            timeout=30.0,
            terminate_grace=2.0,
            cwd=str(extracted),
            env=env,
        )
        stdout = result.stdout.strip()
        if stdout:
            try:
                smoke_stage = int(json.loads(stdout.splitlines()[-1])["stage"])
            except (KeyError, TypeError, ValueError, json.JSONDecodeError):
                smoke_stage = 0
        passed = (
            result.exit_code == 0
            and not result.timed_out
            and result.signal is None
            and not result.group_leak_detected
            and not result.group_alive_after_cleanup
            and smoke_stage == 5
        )
        if not passed:
            stdout = f"stdout={result.stdout!r}; stderr={result.stderr!r}"
    finally:
        shutil.rmtree(temporary, ignore_errors=False)
    return passed, smoke_stage, stdout, not temporary.exists()


def _smoke_program() -> str:
    return """
import json
import sys
sys.path[:0] = [sys.argv[1], sys.argv[2]]
from myalgorithm import algorithm
from utils import check_feasibility
problem = {
    "name": "s6-package-smoke",
    "bays": [{"width": 4, "height": 4}],
    "blocks": [{
        "release_time": 0,
        "due_date": 2,
        "processing_time": 0,
        "workload": 1,
        "bay_preferences": [1],
        "shape": [{"orientation": 0, "layers": [[[0, 0], [1, 0], [1, 1], [0, 1]]]}],
    }],
    "weights": {"w1": 1.0, "w2": 1.0, "w3": 1.0},
}
solution = algorithm(problem, 0.5)
checked = check_feasibility(problem, solution)
assert checked.get("feasible") is True
assert checked.get("stage") == 5
print(json.dumps({"stage": checked["stage"], "feasible": checked["feasible"]}, sort_keys=True))
""".strip()


def _git(root: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", *args],
        cwd=root,
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    if result.returncode != 0:
        raise PackageAuditError(
            f"git {' '.join(args)} failed ({result.returncode}): {result.stderr.strip()}"
        )
    return result.stdout.strip()


def submission_rehearsal(*_args, **_kwargs):
    raise StageUnsupportedError("stage unsupported: submission rehearsal belongs to S6-06")
