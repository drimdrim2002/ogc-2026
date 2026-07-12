"""Durable evidence schema with safe interruption and resume semantics."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import platform
import secrets
import sys
from typing import Any, Iterable, Mapping


SCHEMA_VERSION = 1
TERMINAL_STATUSES = frozenset(
    {"passed", "failed", "checker_failed", "timeout", "crashed", "deduplicated"}
)


def record_identity(
    *,
    commit: str,
    dirty_diff_hash: str,
    instance_sha: str,
    solver: str,
    timelimit: float,
    seed: int,
    features: Mapping[str, str],
) -> str:
    """Return the canonical content identity used for resume and deduplication."""
    payload = {
        "commit": commit,
        "dirty_diff_hash": dirty_diff_hash,
        "instance_sha": instance_sha,
        "solver": solver,
        "timelimit": timelimit,
        "seed": seed,
        "features": sorted((str(key), str(value)) for key, value in features.items()),
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()


def new_run_id() -> str:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return f"{stamp}-{secrets.token_hex(4)}"


@dataclass(slots=True)
class EvidenceRun:
    """Append-only record writer whose COMPLETE marker is a durable commit point."""

    run_dir: Path
    manifest: dict[str, Any]

    @classmethod
    def start(
        cls,
        run_dir: Path,
        *,
        command: Iterable[str],
        expected_record_ids: Iterable[str],
        metadata: Mapping[str, Any],
    ) -> "EvidenceRun":
        run_dir.mkdir(parents=True, exist_ok=False)
        command_list = [str(item) for item in command]
        manifest = {
            "schema_version": SCHEMA_VERSION,
            "run_id": run_dir.name,
            "started": True,
            "interrupted": False,
            "complete": False,
            "started_at": datetime.now(timezone.utc).isoformat(),
            "expected_record_ids": list(expected_record_ids),
            "command": command_list,
            "cwd": str(Path.cwd()),
            **dict(metadata),
        }
        _atomic_json(run_dir / "run.json", manifest)
        _atomic_text(run_dir / "command.txt", " ".join(command_list) + "\n")
        _atomic_json(
            run_dir / "versions.json",
            _runtime_versions(),
        )
        _atomic_text(run_dir / "records.jsonl", "")
        _atomic_text(run_dir / "failures.jsonl", "")
        return cls(run_dir=run_dir, manifest=manifest)

    @classmethod
    def resume(cls, run_dir: Path) -> "EvidenceRun":
        manifest = json.loads((run_dir / "run.json").read_text(encoding="utf-8"))
        if (run_dir / "COMPLETE").exists():
            raise RuntimeError(f"run {run_dir.name} is already complete")
        manifest["resumed_at"] = datetime.now(timezone.utc).isoformat()
        _atomic_json(run_dir / "run.json", manifest)
        return cls(run_dir=run_dir, manifest=manifest)

    @property
    def records(self) -> tuple[dict[str, Any], ...]:
        path = self.run_dir / "records.jsonl"
        if not path.exists():
            return ()
        return tuple(
            json.loads(line)
            for line in path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        )

    @property
    def terminal_record_ids(self) -> frozenset[str]:
        return frozenset(
            str(record["record_id"])
            for record in self.records
            if record.get("complete") is True
            and record.get("status") in TERMINAL_STATUSES
        )

    @property
    def pending_record_ids(self) -> tuple[str, ...]:
        terminal = self.terminal_record_ids
        return tuple(
            str(record_id)
            for record_id in self.manifest.get("expected_record_ids", ())
            if str(record_id) not in terminal
        )

    def append_record(self, record: Mapping[str, Any]) -> None:
        if (self.run_dir / "COMPLETE").exists():
            raise RuntimeError("cannot append to a complete evidence run")
        payload = {
            "schema_version": SCHEMA_VERSION,
            "run_id": self.manifest["run_id"],
            "timestamp": datetime.now(timezone.utc).isoformat(),
            **dict(record),
        }
        if "record_id" not in payload:
            raise ValueError("record_id is required")
        _append_jsonl(self.run_dir / "records.jsonl", payload)
        if payload.get("status") not in {"passed", "deduplicated"}:
            _append_jsonl(self.run_dir / "failures.jsonl", payload)

    def interrupt(self) -> None:
        self.manifest["interrupted"] = True
        self.manifest["interrupted_at"] = datetime.now(timezone.utc).isoformat()
        _atomic_json(self.run_dir / "run.json", self.manifest)

    def finalize(self, summary: Mapping[str, Any]) -> None:
        missing = self.pending_record_ids
        if missing:
            raise RuntimeError(f"missing terminal records: {', '.join(missing)}")
        _atomic_json(
            self.run_dir / "summary.json",
            {"schema_version": SCHEMA_VERSION, "run_id": self.manifest["run_id"], **dict(summary)},
        )
        self.manifest["complete"] = True
        self.manifest["interrupted"] = False
        self.manifest["completed_at"] = datetime.now(timezone.utc).isoformat()
        _atomic_json(self.run_dir / "run.json", self.manifest)
        _atomic_text(self.run_dir / "COMPLETE", "complete\n")

    @staticmethod
    def completed_identities(run_dir: Path) -> frozenset[str]:
        if not (run_dir / "COMPLETE").exists():
            return frozenset()
        path = run_dir / "records.jsonl"
        if not path.exists():
            return frozenset()
        return frozenset(
            str(record["identity"])
            for line in path.read_text(encoding="utf-8").splitlines()
            if line.strip()
            for record in (json.loads(line),)
            if record.get("complete") is True and record.get("identity")
        )


def find_completed_identity(evidence_root: Path, identity: str) -> Path | None:
    """Find immutable proof for an identity, ignoring partial run directories."""
    if not evidence_root.exists():
        return None
    for marker in sorted(evidence_root.rglob("COMPLETE"), reverse=True):
        run_dir = marker.parent
        if identity in EvidenceRun.completed_identities(run_dir):
            return run_dir
    return None


def _append_jsonl(path: Path, payload: Mapping[str, Any]) -> None:
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(dict(payload), sort_keys=True, separators=(",", ":")) + "\n")
        handle.flush()
        os.fsync(handle.fileno())


def _atomic_json(path: Path, payload: Mapping[str, Any]) -> None:
    _atomic_text(path, json.dumps(dict(payload), indent=2, sort_keys=True) + "\n")


def _atomic_text(path: Path, text: str) -> None:
    temporary = path.with_name(f".{path.name}.{secrets.token_hex(4)}.tmp")
    with temporary.open("w", encoding="utf-8") as handle:
        handle.write(text)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, path)
    directory_fd = os.open(path.parent, os.O_RDONLY)
    try:
        os.fsync(directory_fd)
    finally:
        os.close(directory_fd)


def _runtime_versions() -> dict[str, Any]:
    versions: dict[str, Any] = {
        "python": sys.version,
        "executable": sys.executable,
        "platform": platform.platform(),
    }
    modules = {
        "numpy": "numpy",
        "shapely": "shapely",
        "ortools": "ortools",
        "gurobipy": "gurobipy",
        "psutil": "psutil",
    }
    for label, module_name in modules.items():
        try:
            module = __import__(module_name)
            versions[label] = str(getattr(module, "__version__", "installed"))
        except Exception as exc:
            versions[label] = f"unavailable: {type(exc).__name__}"
    return versions
