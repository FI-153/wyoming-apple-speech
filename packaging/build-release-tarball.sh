#!/usr/bin/env bash
# Build a release tarball for the Homebrew formula.
#
# Usage: packaging/build-release-tarball.sh <version>
# Example: packaging/build-release-tarball.sh 1.0.0
#
# Produces: dist/wyoming-apple-stt-<version>.tar.gz
set -euo pipefail

if [[ $# -ne 1 ]]; then
    echo "Usage: $0 <version>" >&2
    exit 2
fi

VERSION="$1"
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_DIR="$(dirname "$SCRIPT_DIR")"
DIST_DIR="${REPO_DIR}/dist"
STAGE_DIR="${DIST_DIR}/wyoming-apple-stt-${VERSION}"
TARBALL="${DIST_DIR}/wyoming-apple-stt-${VERSION}.tar.gz"

echo "==> Building universal Swift binaries"
cd "${REPO_DIR}/swift"
swift build -c release --arch arm64 --arch x86_64
BUILD_DIR="$(swift build -c release --arch arm64 --arch x86_64 --show-bin-path)"

echo "==> Verifying binaries are universal"
for binary in apple-stt apple-tts; do
    ARCHS="$(lipo -archs "${BUILD_DIR}/${binary}")"
    if [[ "${ARCHS}" != *"arm64"* ]] || [[ "${ARCHS}" != *"x86_64"* ]]; then
        echo "ERROR: expected universal ${binary}, got: ${ARCHS}" >&2
        exit 1
    fi
    echo "    ${binary} archs: ${ARCHS}"
done

echo "==> Staging tarball contents at ${STAGE_DIR}"
rm -rf "${STAGE_DIR}" "${TARBALL}"
mkdir -p "${STAGE_DIR}"
cp "${BUILD_DIR}/apple-stt" "${STAGE_DIR}/apple-stt"
cp "${BUILD_DIR}/apple-tts" "${STAGE_DIR}/apple-tts"
cp -R "${REPO_DIR}/wyoming_apple_stt" "${STAGE_DIR}/wyoming_apple_stt"
cp "${REPO_DIR}/pyproject.toml" "${STAGE_DIR}/pyproject.toml"
cp "${REPO_DIR}/README.md" "${STAGE_DIR}/README.md"
# Drop any __pycache__ copied in.
find "${STAGE_DIR}/wyoming_apple_stt" -type d -name __pycache__ -exec rm -rf {} +

echo "==> Creating tarball"
tar -czf "${TARBALL}" -C "${DIST_DIR}" "wyoming-apple-stt-${VERSION}"

echo "==> Computing SHA-256"
SHA="$(shasum -a 256 "${TARBALL}" | awk '{print $1}')"

echo ""
echo "Tarball: ${TARBALL}"
echo "SHA256:  ${SHA}"
