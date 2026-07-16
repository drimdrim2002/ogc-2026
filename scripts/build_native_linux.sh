#!/usr/bin/env bash
set -euo pipefail

# Offline Ubuntu build helper.  It never downloads dependencies and writes all
# build artifacts to the caller-selected directory (default: /tmp).
root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
native="$root/native/ogc_native"
lock="$native/third_party/PYBIND11.lock.json"
build_dir="${1:-/tmp/ogc-native-linux-release}"
package_dir="${2:-$build_dir/package}"
source_dir="$build_dir/pybind11-source"
archive="$native/third_party/pybind11-2.13.6.tar.gz"
expected="ba6af10348c12b24e92fa086b39cfba0eff619b61ac77c406167d813b096d39a"

test -f "$lock"
test "$(sha256sum "$archive" | awk '{print $1}')" = "$expected"
if test -e "$source_dir"; then
  echo "refusing to reuse existing extracted source: $source_dir" >&2
  exit 2
fi
if test -e "$package_dir"; then
  echo "refusing to overwrite package directory: $package_dir" >&2
  exit 2
fi
mkdir -p "$source_dir" "$build_dir/build"
tar -xzf "$archive" -C "$source_dir" --strip-components=1

cmake -S "$native" -B "$build_dir/build" \
  -DPYBIND11_SOURCE_DIR="$source_dir" \
  -DCMAKE_BUILD_TYPE=Release
cmake --build "$build_dir/build" --config Release --parallel 4
module="$(find "$build_dir/build" -name '_ogc_native*.so' -type f -print -quit)"
test -n "$module"
strip --strip-unneeded "$module"

# Bundle only the non-system GEOS runtime. The extension resolves libgeos_c
# relative to itself; libgeos_c resolves libgeos from its own directory.
mkdir -p "$package_dir/lib" "$package_dir/THIRD_PARTY_LICENSES"
packaged_module="$package_dir/$(basename "$module")"
cp "$module" "$packaged_module"
geos_c_path="$(ldd "$module" | awk '$1 == "libgeos_c.so.1" {print $3}')"
test -f "$geos_c_path"
geos_c_needed="$(patchelf --print-needed "$module" | awk '/^libgeos_c\.so/ {print; exit}')"
test -n "$geos_c_needed"
cp "$(readlink -f "$geos_c_path")" "$package_dir/lib/$geos_c_needed"
geos_needed="$(patchelf --print-needed "$geos_c_path" | awk '/^libgeos\.so/ {print; exit}')"
test -n "$geos_needed"
geos_path="$(ldd "$geos_c_path" | awk -v needed="$geos_needed" '$1 == needed {print $3}')"
test -f "$geos_path"
cp "$(readlink -f "$geos_path")" "$package_dir/lib/$geos_needed"

patchelf --set-rpath '$ORIGIN/lib' "$packaged_module"
patchelf --set-rpath '$ORIGIN' "$package_dir/lib/$geos_c_needed"
strip --strip-unneeded "$package_dir/lib/$geos_c_needed"
strip --strip-unneeded "$package_dir/lib/$geos_needed"

geos_license="$(find /usr/share/doc -maxdepth 2 -path '*/libgeos*/copyright' -type f -print -quit)"
test -f "$geos_license"
cp "$geos_license" "$package_dir/THIRD_PARTY_LICENSES/GEOS.txt"
cp "$source_dir/LICENSE" "$package_dir/THIRD_PARTY_LICENSES/PYBIND11.txt"

readelf -d "$packaged_module" | grep -F '[$ORIGIN/lib]'
readelf -d "$package_dir/lib/$geos_c_needed" | grep -F '[$ORIGIN]'
if readelf -d "$packaged_module" "$package_dir/lib/$geos_c_needed" | grep -E '(RPATH|RUNPATH).*/(usr|opt|home)/'; then
  echo "absolute runtime path leaked into package" >&2
  exit 2
fi
ldd "$packaged_module" | grep -F "$package_dir/lib/$geos_c_needed"
ldd "$packaged_module" | grep -F "$package_dir/lib/$geos_needed"

(
  cd "$package_dir"
  find . -type f ! -name SHA256SUMS -print0 |
    sort -z |
    xargs -0 sha256sum > SHA256SUMS
)
archive="$build_dir/ogc_native_ubuntu24_amd64.zip"
(
  cd "$package_dir"
  zip -9 -q -r "$archive" .
)
archive_bytes="$(stat -c %s "$archive")"
if test "$archive_bytes" -gt 15000000; then
  echo "package exceeds the 15 MB submission limit: $archive_bytes" >&2
  exit 2
fi

printf 'module=%s\npackage=%s\narchive=%s\narchive_bytes=%s\n' \
  "$packaged_module" "$package_dir" "$archive" "$archive_bytes"
