#!/bin/bash
set -euo pipefail

APP_DIR="$HOME/Library/Application Support/QN990FController"
PYTHON="$APP_DIR/venv/bin/python"
CONTROLLER="$APP_DIR/QN990FController.py"
LABEL="local.qn990f.picture-controller"

echo "Samsung TV Picture Controller - Input Monitoring repair"
echo
echo "The controller needs permission for this exact runtime:"
echo "$PYTHON"
echo
"$PYTHON" "$CONTROLLER" --request-input-access >/dev/null 2>&1 || true
/usr/bin/open "x-apple.systempreferences:com.apple.preference.security?Privacy_ListenEvent"
echo "In Input Monitoring, add or enable the runtime shown above."
echo "Use Command+Shift+G in the file chooser to enter its full path."
read -r -p "After enabling it, press Return to restart the controller."

launchctl kickstart -k "gui/$(id -u)/$LABEL"
sleep 8

HEALTH="$APP_DIR/health.json"
STATE="$(/usr/bin/plutil -extract volume_control_state raw -o - "$HEALTH" 2>/dev/null || true)"
MESSAGE="$(/usr/bin/plutil -extract volume_control_message raw -o - "$HEALTH" 2>/dev/null || true)"
echo
echo "Volume status: ${STATE:-unknown}"
if [[ -n "$MESSAGE" ]]; then echo "$MESSAGE"; fi
read -r -p "Press Return to close."
