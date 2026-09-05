#!/bin/bash
set -euo pipefail

APP_DIR="$HOME/Library/Application Support/QN990FController"
CONTROLLER="$APP_DIR/QN990FController.py"
PLIST="$HOME/Library/LaunchAgents/local.qn990f.picture-controller.plist"
MENU_PLIST="$HOME/Library/LaunchAgents/local.samsung-tv.picture-controller.menu.plist"

echo "Uninstalling Samsung TV Picture Controller..."
launchctl bootout "gui/$(id -u)" "$PLIST" >/dev/null 2>&1 || true
launchctl bootout "gui/$(id -u)" "$MENU_PLIST" >/dev/null 2>&1 || true
pkill -f "$CONTROLLER" >/dev/null 2>&1 || true
rm -f "$PLIST" "$MENU_PLIST"
rm -rf "$APP_DIR"
echo "Uninstalled."
echo "The shared SmartThings CLI sign-in was left unchanged."
sleep 1
