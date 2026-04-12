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

# 1. Build Swift CLI
echo "Building Swift CLI..."
cd "${PROJECT_DIR}/swift"
swift build -c release
SWIFT_BIN="${PROJECT_DIR}/swift/.build/release/apple-stt"
echo "Built: ${SWIFT_BIN}"

# 2. Create install directory
echo "Setting up install directory..."
mkdir -p "${INSTALL_DIR}"
cp -r "${PROJECT_DIR}/wyoming_apple_stt" "${INSTALL_DIR}/"
cp "${PROJECT_DIR}/requirements.txt" "${INSTALL_DIR}/"
cp "${SWIFT_BIN}" "${INSTALL_DIR}/apple-stt"

# 3. Create Python venv
echo "Creating Python virtual environment..."
python3 -m venv "${INSTALL_DIR}/venv"
"${INSTALL_DIR}/venv/bin/pip" install --quiet -r "${INSTALL_DIR}/requirements.txt"

# 4. Create log directory
mkdir -p "${LOG_DIR}"

# 5. Generate and install launchd plist
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

# 6. Load and start the service
launchctl unload "${PLIST_DIR}/${PLIST_NAME}" 2>/dev/null || true
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
