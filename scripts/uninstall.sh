#!/usr/bin/env bash
set -euo pipefail

# Wyoming Apple STT — Uninstall Script

INSTALL_DIR="${HOME}/.local/share/wyoming-apple-stt"
LOG_DIR="${HOME}/Library/Logs/wyoming-apple-stt"
PLIST_NAME="com.wyoming-apple-stt.plist"
PLIST_PATH="${HOME}/Library/LaunchAgents/${PLIST_NAME}"

echo "=== Wyoming Apple STT Uninstaller ==="
echo ""

# 1. Stop and unload the service
if [ -f "${PLIST_PATH}" ]; then
    echo "Stopping service..."
    launchctl unload "${PLIST_PATH}" 2>/dev/null || true
    rm "${PLIST_PATH}"
    echo "Removed: ${PLIST_PATH}"
else
    echo "No launchd plist found (already removed?)."
fi

# 2. Remove install directory
if [ -d "${INSTALL_DIR}" ]; then
    echo "Removing install directory..."
    rm -rf "${INSTALL_DIR}"
    echo "Removed: ${INSTALL_DIR}"
else
    echo "No install directory found."
fi

# 3. Remove logs
if [ -d "${LOG_DIR}" ]; then
    echo "Removing logs..."
    rm -rf "${LOG_DIR}"
    echo "Removed: ${LOG_DIR}"
else
    echo "No log directory found."
fi

echo ""
echo "=== Uninstall complete ==="
echo ""
echo "Remember to remove the Wyoming integration from Home Assistant if no longer needed."
