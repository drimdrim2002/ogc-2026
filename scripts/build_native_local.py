#!/usr/bin/env python3
"""Build the P2 macOS/local spike without CMake or network access.

The build reads only the vendored pybind11 source tarball.  Output defaults to
``/tmp`` and is intentionally outside the submission/source tree.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sysconfig
import tarfile
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
NATIVE = ROOT / "native" / "ogc_native"
LOCK = NATIVE / "third_party" / "PYBIND11.lock.json"


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _default_geos_prefix() -> Path | None:
    configured = os.environ.get("GEOS_PREFIX")
    candidates = [Path(configured)] if configured else []
    candidates.extend((Path("/opt/homebrew/opt/geos"), Path(sysconfig.get_config_var("prefix") or "")))
    return next(
        (item for item in candidates if item and (item / "include" / "geos_c.h").is_file()),
        None,
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--cxx", default=shutil.which("c++") or "c++")
    parser.add_argument("--geos-prefix", type=Path, default=_default_geos_prefix())
    parser.add_argument("--geos-lib-dir", type=Path)
    parser.add_argument("--runtime-rpath")
    args = parser.parse_args()

    if args.geos_prefix is None:
        raise SystemExit("public GEOS development prefix is required")
    geos_include = args.geos_prefix / "include"
    geos_lib = args.geos_lib_dir or args.geos_prefix / "lib"
    if not (geos_include / "geos_c.h").is_file():
        raise SystemExit(f"public GEOS header is missing: {geos_include / 'geos_c.h'}")
    if not any(geos_lib.glob("libgeos_c.*")):
        raise SystemExit(f"public GEOS C library is missing: {geos_lib}")

    lock = json.loads(LOCK.read_text(encoding="utf-8"))
    archive = LOCK.parent / lock["source"]
    if _sha256(archive) != lock["sha256"]:
        raise SystemExit("pinned pybind11 source SHA-256 mismatch")

    args.output_dir.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="ogc-native-p2-") as temporary:
        work = Path(temporary)
        with tarfile.open(archive) as source:
            source.extractall(work, filter="data")
        include = work / f"pybind11-{lock['version']}" / "pybind11" / "include"
        suffix = sysconfig.get_config_var("EXT_SUFFIX")
        if not suffix:
            raise SystemExit("Python extension suffix is unavailable")
        output = args.output_dir / f"_ogc_native{suffix}"
        command = [
            args.cxx, "-std=c++20", "-O3", "-DNDEBUG", "-fvisibility=hidden",
            "-I", str(include), "-I", sysconfig.get_paths()["include"],
            "-I", str(NATIVE / "src"), "-I", str(geos_include),
            "-shared", "-fPIC",
            str(NATIVE / "src" / "bindings.cpp"),
            str(NATIVE / "src" / "repair_kernel.cpp"),
            str(NATIVE / "src" / "exact_geometry.cpp"),
            "-L", str(geos_lib), "-lgeos_c", "-o", str(output),
        ]
        if sysconfig.get_platform().startswith("macosx"):
            command.extend(["-undefined", "dynamic_lookup"])
        runtime_rpath = args.runtime_rpath
        if runtime_rpath is None and sysconfig.get_platform().startswith("macosx"):
            runtime_rpath = str(geos_lib)
        if runtime_rpath:
            command.append(f"-Wl,-rpath,{runtime_rpath}")
        subprocess.run(command, check=True)
    print(output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
