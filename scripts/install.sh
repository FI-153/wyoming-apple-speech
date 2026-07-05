#!/usr/bin/env bash
set -euo pipefail

# Wyoming Apple STT — Install Script
# Builds Swift binary, sets up Python venv, installs launchd service.

INSTALL_DIR="${HOME}/.local/share/wyoming-apple-stt"
LOG_DIR="${HOME}/Library/Logs/wyoming-apple-stt"
PLIST_NAME="com.wyoming-apple-stt.plist"
PLIST_DIR="${HOME}/Library/LaunchAgents"

PORT="${1:-10300}"
LANGUAGE="${2:-en}"

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"

echo "=== Wyoming Apple STT Installer ==="
echo ""
echo "Install dir:  ${INSTALL_DIR}"
echo "Port:         ${PORT}"
echo "Language:     ${LANGUAGE}"
echo ""

# Require Python 3.11+ (matches pyproject.toml requires-python).
if ! command -v python3 >/dev/null 2>&1; then
    echo "ERROR: python3 not found on PATH. Python >=3.11 is required." >&2
    exit 1
fi
if ! python3 -c 'import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)'; then
    FOUND_PY="$(python3 -c 'import sys; print(".".join(map(str, sys.version_info[:3])))')"
    echo "ERROR: Python ${FOUND_PY} found, but Python >=3.11 is required." >&2
    exit 1
fi

# 1. Build Swift CLI
echo "Building Swift CLI..."
cd "${PROJECT_DIR}/swift"
swift build -c release
SWIFT_BIN="${PROJECT_DIR}/swift/.build/release/apple-stt"
echo "Built: ${SWIFT_BIN}"

# 2. Stop any running service before overwriting its files. Copying over the
#    live binary truncates its inode, breaking the running process's code
#    signature so macOS SIGKILLs it mid-install (and KeepAlive may respawn it
#    against half-installed files). Unload first to avoid that.
echo "Stopping existing service..."
launchctl unload "${PLIST_DIR}/${PLIST_NAME}" 2>/dev/null || true

# 3. Create install directory
echo "Setting up install directory..."
mkdir -p "${INSTALL_DIR}"
cp "${SWIFT_BIN}" "${INSTALL_DIR}/apple-stt"

# 4. Create Python venv
echo "Creating Python virtual environment..."
python3 -m venv "${INSTALL_DIR}/venv"
"${INSTALL_DIR}/venv/bin/pip" install --quiet "${PROJECT_DIR}"

# 5. Create log directory
mkdir -p "${LOG_DIR}"

# 6. Generate and install launchd plist
echo "Installing launchd service..."
mkdir -p "${PLIST_DIR}"

sed \
    -e "s|__VENV_PYTHON__|${INSTALL_DIR}/venv/bin/python|g" \
    -e "s|__PORT__|${PORT}|g" \
    -e "s|__APPLE_STT_BIN__|${INSTALL_DIR}/apple-stt|g" \
    -e "s|__LANGUAGE__|${LANGUAGE}|g" \
    -e "s|__LOG_DIR__|${LOG_DIR}|g" \
    -e "s|__INSTALL_DIR__|${INSTALL_DIR}|g" \
    "${PROJECT_DIR}/com.wyoming-apple-stt.plist.template" \
    > "${PLIST_DIR}/${PLIST_NAME}"

# 7. Load and start the service
launchctl load "${PLIST_DIR}/${PLIST_NAME}"

echo ""
echo "=== Installation complete ==="
echo ""
echo "Service is running on port ${PORT}."
echo "Logs:     ${LOG_DIR}/"
echo "Plist:    ${PLIST_DIR}/${PLIST_NAME}"
echo "Install:  ${INSTALL_DIR}/"
echo ""
echo "In Home Assistant:"
echo "  Settings → Integrations → Add → Wyoming → tcp://<this-mac-ip>:${PORT}"
echo ""
echo "NOTE: If this Mac requires login after reboot, enable auto-login in"
echo "      System Settings → Users & Groups → Automatic login so the"
echo "      service starts without manual intervention."
echo ""
echo "NOTE: On first transcription, macOS will prompt for Speech Recognition"
echo "      permission. You must approve this once."
