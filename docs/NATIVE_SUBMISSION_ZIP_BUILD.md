# OGC 2026 Native Submission ZIP 빌드 가이드

이 문서는 WSL2의 Ubuntu 24.04 amd64 환경에서 이 저장소의 C++/pybind11
extension을 빌드하고, 최종 OGC 2026 제출 ZIP을 생성·검증하는 절차를 설명한다.

전제:

- WSL2 배포판은 Ubuntu 24.04이다.
- CPU 아키텍처는 amd64/x86_64이다.
- `ogc2026_env.yml`로 만든 Conda 환경 `ogc2026`이 이미 존재한다.
- 저장소 브랜치는 `codex/performance-optimization-plan`을 사용한다.
- 작업은 저장소 루트에서 진행한다.

최종 제출물은 `scripts/build_native_linux.sh`가 만드는 중간 package ZIP이
아니라, `scripts/build_submission_zip.py`가 만드는
`dist/ogc2026_submission_YYYYMMDD.zip`이다.

## 1. WSL2 환경 확인

가능하면 저장소를 `/mnt/c`가 아닌 WSL Linux 파일시스템(예:
`~/workspace`)에 둔다. CMake 빌드 시 Windows 파일시스템의 성능, 권한,
symlink 차이를 피할 수 있다.

```bash
grep -E '^(NAME|VERSION_ID)=' /etc/os-release
uname -r
uname -m
```

확인 조건:

- Ubuntu 버전: `24.04`
- `uname -r`: WSL2 커널을 나타내는 문자열 포함
- `uname -m`: `x86_64`

ARM64 WSL 환경에서 만든 바이너리는 평가 서버용 amd64 바이너리가 아니므로
사용하면 안 된다.

## 2. 브랜치 pull

```bash
cd ~/path/to/ogc-2026
git switch codex/performance-optimization-plan
git pull --ff-only
```

`~/path/to/ogc-2026`은 실제 WSL 저장소 경로로 바꾼다.

## 3. 기존 Conda 환경 활성화 및 확인

```bash
conda activate ogc2026

which python
python --version
python -c 'import platform, shapely; print(platform.machine(), shapely.__version__)'
```

확인 조건:

- `python --version`: `Python 3.12.x`
- `platform.machine()`: `x86_64`
- Shapely: `2.1` 이상

이 절차에서는 별도 venv를 만들거나 Shapely를 다시 설치하지 않는다.
`conda` 명령이 WSL 셸에 초기화되지 않았다면 기존 Miniforge/Conda 설치의
`conda.sh`를 먼저 source한 후 `conda activate ogc2026`을 실행한다.

## 4. Ubuntu native 빌드 도구 설치

다음 패키지는 WSL 배포판에 최초 한 번만 설치하면 된다.

```bash
sudo apt-get update
sudo apt-get install -y \
  build-essential cmake \
  libgeos-dev patchelf binutils pkg-config \
  zip unzip

cmake --version
geos-config --version
patchelf --version
```

`ogc2026_env.yml`은 Python과 Shapely를 제공하지만 C++ extension 빌드에는
Ubuntu의 GEOS 개발 파일이 필요하다. `libgeos-dev`가 제공하는 GEOS에
링크하며, Shapely wheel 내부의 private GEOS 라이브러리에 링크하지 않는다.

pybind11 source는 저장소에 checksum과 함께 포함되어 있으므로 build script가
추가로 다운로드하지 않는다.

## 5. Linux native package 빌드

`ogc2026` 환경이 활성화된 상태에서 실행한다. build script는 기존 추출
디렉터리나 package 디렉터리를 재사용하지 않으므로 매번 새로운 임시 경로를
사용한다.

```bash
BUILD_DIR="$(mktemp -d /tmp/ogc-native-linux-release.XXXXXX)"

./scripts/build_native_linux.sh \
  "$BUILD_DIR" \
  "$BUILD_DIR/package"
```

성공하면 다음 정보가 출력된다.

```text
module=/tmp/.../package/_ogc_native.cpython-312-x86_64-linux-gnu.so
package=/tmp/.../package
archive=/tmp/.../ogc_native_ubuntu24_amd64.zip
archive_bytes=...
```

중간 package 디렉터리는 대략 다음 구조다.

```text
package/
├── _ogc_native.cpython-312-x86_64-linux-gnu.so
├── lib/
│   ├── libgeos_c.so.1
│   └── libgeos.so.*
├── SHA256SUMS
└── THIRD_PARTY_LICENSES/
```

`ogc_native_ubuntu24_amd64.zip`은 native package 전달용 중간 산출물이며 최종
OGC 제출 ZIP이 아니다.

## 6. 최종 native 제출 ZIP 생성

```bash
python scripts/build_submission_zip.py \
  --native-package "$BUILD_DIR/package"

ZIP="$(realpath "dist/ogc2026_submission_$(TZ=Asia/Seoul date +%Y%m%d).zip")"

ls -lh "$ZIP"
sha256sum "$ZIP"
unzip -l "$ZIP"
```

최종 ZIP은 다음 runtime 파일만 포함해야 한다.

```text
myalgorithm.py
utils.py
solver/*.py
solver/_ogc_native.cpython-312-<x86_64-linux-tag>.so
solver/lib/libgeos_c.so.1
solver/lib/libgeos.so.*
```

다음 항목은 최종 ZIP에 없어야 한다.

- `native/` source 및 C/C++ header/source
- build 디렉터리와 CMake 파일
- test, data, experiments, artifacts
- README, license, `SHA256SUMS`, `THIRD_PARTY_LICENSES`
- `__pycache__`, `.pyc`, `.git` 관련 파일

## 7. Clean extraction 및 native import 검증

저장소 source 경로 대신 실제 제출 ZIP만 새 임시 폴더에 푼다.

```bash
REPO_ROOT="$PWD"
SMOKE_DIR="$(mktemp -d /tmp/ogc-native-smoke.XXXXXX)"

unzip -q "$ZIP" -d "$SMOKE_DIR"
cd "$SMOKE_DIR"

python -I -c '
import sys
sys.path.insert(0, ".")
import solver._ogc_native as native
print(native.empty_kernel_info())
'
```

위 명령이 예외 없이 native 정보 dictionary를 출력해야 한다.

공유 라이브러리와 RPATH/RUNPATH도 확인한다.

```bash
ldd solver/_ogc_native*.so
readelf -d solver/_ogc_native*.so | grep -E 'NEEDED|RPATH|RUNPATH'
```

확인 조건:

- `ldd` 결과에 `not found`가 없다.
- `libgeos_c.so.1`과 `libgeos.so.*`가 압축 해제 폴더의 `solver/lib`에서
  로드된다.
- RPATH/RUNPATH에 `/home`, `/usr`, `/opt` 등 개발 머신의 절대 경로가 없다.

## 8. 공개 예제 실행 및 feasibility 검증

계속 clean extraction 폴더에서 실행한다. 문제 파일만 저장소의 공개 예제
절대 경로로 전달하며, Python module은 압축 해제 폴더에서만 import한다.

```bash
python -I -c '
import json
import pathlib
import sys

sys.path.insert(0, ".")

from myalgorithm import algorithm
from solver.native_repair import native_module_status
from solver.runtime import SubmissionConfig
from utils import check_feasibility

problem = json.loads(pathlib.Path(sys.argv[1]).read_text(encoding="utf-8"))

module, reason = native_module_status()
assert module is not None, reason

config = SubmissionConfig.from_defaults()
assert config.repair_backend == "native"
assert config.native_exact_mode == "native"

solution = algorithm(problem, 2.0)
checked = check_feasibility(problem, solution)

print(checked)
assert checked["feasible"]
assert checked["stage"] == 5
' "$REPO_ROOT/alg_tester/example/example_B2_b10.json"
```

## 9. 제출 전 최종 확인

```bash
stat -c '%n %s bytes' "$ZIP"
sha256sum "$ZIP"
unzip -t "$ZIP"
```

제출 조건:

- 파일 크기 `15,000,000 bytes` 이하
- ZIP integrity PASS
- 최상위에 `myalgorithm.py`, `utils.py` 존재
- `solver._ogc_native` import PASS
- 공유 라이브러리 `not found` 없음
- absolute RPATH/RUNPATH 없음
- 공개 예제 `feasible=true`, Stage 5

하나라도 실패하면 해당 ZIP을 제출하지 않는다.

## 빠른 실행 순서

WSL 초기 설정과 Conda 환경 준비가 이미 끝난 이후에는 다음 순서만 반복한다.

```bash
git switch codex/performance-optimization-plan
git pull --ff-only
conda activate ogc2026

BUILD_DIR="$(mktemp -d /tmp/ogc-native-linux-release.XXXXXX)"
./scripts/build_native_linux.sh "$BUILD_DIR" "$BUILD_DIR/package"

python scripts/build_submission_zip.py \
  --native-package "$BUILD_DIR/package"

ZIP="$(realpath "dist/ogc2026_submission_$(TZ=Asia/Seoul date +%Y%m%d).zip")"
ls -lh "$ZIP"
sha256sum "$ZIP"
unzip -l "$ZIP"
```
