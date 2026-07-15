#!/usr/bin/env bash
# Run from a normal terminal, not from Codex. It resumes with the same RUN_ID
# and freezes HARD10_MANIFEST.json only after the exact 240-run gate and a
# stable solver-source fingerprint both pass.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
PYTHON_BIN="${PYTHON_BIN:-/opt/homebrew/Caskroom/miniforge/base/envs/ogc-2026/bin/python}"
SOURCE_COMMIT="$(git -C "$REPO_ROOT" rev-parse HEAD)"
RUN_ID="${RUN_ID:-phase0-telemetry-$(date -u +%Y%m%dT%H%M%SZ)-${SOURCE_COMMIT:0:12}}"
RUN_DIR="$REPO_ROOT/artifacts/ogc_sage/performance/phase0/$RUN_ID"
LOCK_DIR="$RUN_DIR/.run-lock"
BENCHMARK="$REPO_ROOT/experiments/ogc_sage/benchmark_ogc_sage.py"
MANIFEST="$REPO_ROOT/docs/implementation/sol/performance/HARD10_MANIFEST.json"

if [[ ! -x "$PYTHON_BIN" ]]; then
  echo "PYTHON_BIN is not executable: $PYTHON_BIN" >&2
  exit 2
fi

mkdir -p "$RUN_DIR"
if ! mkdir "$LOCK_DIR" 2>/dev/null; then
  echo "another phase-0 run is active or stale: $LOCK_DIR" >&2
  echo "inspect the process before removing a stale lock" >&2
  exit 2
fi
trap 'rmdir "$LOCK_DIR"' EXIT

fingerprint() {
  local output="$1"
  {
    echo "source_commit=$SOURCE_COMMIT"
    echo "python=$($PYTHON_BIN --version)"
    "$PYTHON_BIN" - <<'PY'
import importlib.metadata
import platform

for package in ("shapely", "gurobipy"):
    try:
        value = importlib.metadata.version(package)
    except importlib.metadata.PackageNotFoundError:
        value = None
    print(f"{package}={value}")
print(f"platform={platform.platform()}")
PY
    git -C "$REPO_ROOT" status --porcelain -- baseline/myalgorithm.py baseline/solver experiments/ogc_sage/benchmark_ogc_sage.py
    find "$REPO_ROOT/baseline" -type f -name '*.py' -print | LC_ALL=C sort | xargs shasum -a 256
    shasum -a 256 "$BENCHMARK"
  } > "$output"
}

PRE_FINGERPRINT="$RUN_DIR/source-pre.sha256"
POST_FINGERPRINT="$RUN_DIR/source-post.sha256"
CURRENT_FINGERPRINT="$RUN_DIR/source-current.sha256"
if [[ -f "$RUN_DIR/raw.jsonl" && ! -f "$PRE_FINGERPRINT" ]]; then
  echo "existing raw artifact has no source-pre.sha256; refusing an unverifiable resume" >&2
  exit 2
fi
fingerprint "$CURRENT_FINGERPRINT"
if [[ -f "$PRE_FINGERPRINT" ]]; then
  if ! cmp -s "$PRE_FINGERPRINT" "$CURRENT_FINGERPRINT"; then
    echo "source/environment differs from the existing artifact; refusing to resume" >&2
    exit 3
  fi
else
  mv "$CURRENT_FINGERPRINT" "$PRE_FINGERPRINT"
fi

echo "run_id=$RUN_ID"
echo "artifact=$RUN_DIR"
"$PYTHON_BIN" "$BENCHMARK" run \
  --gate phase0 \
  --variant heuristic_lns \
  --budgets 60 180 \
  --seeds 20260710 20260711 20260712 \
  --run-id "$RUN_ID" \
  --source-commit "$SOURCE_COMMIT" \
  --allow-dirty

fingerprint "$POST_FINGERPRINT"
if ! cmp -s "$PRE_FINGERPRINT" "$POST_FINGERPRINT"; then
  echo "solver source/environment changed during the matrix; manifest was not updated" >&2
  exit 3
fi

CANDIDATE_MANIFEST="$RUN_DIR/HARD10_MANIFEST.json"
"$PYTHON_BIN" "$BENCHMARK" freeze-hard10 \
  --input-raw "$RUN_DIR/raw.jsonl" \
  --output "$CANDIDATE_MANIFEST"
cp "$CANDIDATE_MANIFEST" "$MANIFEST"

(
  cd "$RUN_DIR"
  shasum -a 256 raw.jsonl summary.json SHA256SUMS source-pre.sha256 source-post.sha256 HARD10_MANIFEST.json > PHASE0_SHA256SUMS
)

echo "completed: $RUN_DIR"
echo "frozen manifest: $MANIFEST"
echo "checksums: $RUN_DIR/PHASE0_SHA256SUMS"
