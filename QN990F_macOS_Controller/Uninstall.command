#!/bin/bash
set -euo pipefail

APP_DIR="$HOME/Library/Application Support/QN990FController"
CONTROLLER="$APP_DIR/QN990FController.py"
PLIST="$HOME/Library/LaunchAgents/local.qn990f.picture-controller.plist"

echo "Uninstalling QN990F macOS Controller..."
launchctl bootout "gui/$(id -u)" "$PLIST" >/dev/null 2>&1 || true
pkill -f "$CONTROLLER" >/dev/null 2>&1 || true
rm -f "$PLIST"
rm -rf "$APP_DIR"
echo "Uninstalled."
sleep 1
