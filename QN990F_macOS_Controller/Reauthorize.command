#!/bin/bash
set -euo pipefail

APP_DIR="$HOME/Library/Application Support/QN990FController"
PYTHON="$APP_DIR/venv/bin/python"
CONTROLLER="$APP_DIR/QN990FController.py"
CONFIG="$APP_DIR/config.json"
PLIST="$HOME/Library/LaunchAgents/local.qn990f.picture-controller.plist"
LABEL="local.qn990f.picture-controller"

if [[ ! -x "$PYTHON" || ! -f "$CONTROLLER" || ! -f "$CONFIG" ]]; then
  echo "Samsung TV Picture Controller is not installed correctly."
  read -r -p "Press Return to close."
  exit 1
fi

CONTROL_METHOD="$("$PYTHON" - "$CONFIG" <<'PY'
import json, sys
with open(sys.argv[1], encoding="utf-8") as f:
    print(json.load(f).get("control_method", "lan"))
PY
)"
if [[ "$CONTROL_METHOD" != "smartthings" ]]; then
  echo "This installation is not using SmartThings cloud control."
  read -r -p "Press Return to close."
  exit 1
fi
if [[ ! -f "$PLIST" ]]; then
  echo "The LaunchAgent is missing. Rerun INSTALL.command to repair the installation."
  read -r -p "Press Return to close."
  exit 1
fi

echo "Samsung TV Picture Controller - SmartThings reauthorization"
echo "The background controller will stop while the browser sign-in completes."
echo "No Picture Off or wake command will be sent."
echo

launchctl bootout "gui/$(id -u)" "$PLIST" >/dev/null 2>&1 || true
pkill -f "$CONTROLLER" >/dev/null 2>&1 || true
sleep 0.3

if ! "$PYTHON" "$CONTROLLER" --pair; then
  echo
  echo "Reauthorization failed. The controller remains stopped to avoid a token race."
  echo "Run Reauthorize.command again when sign-in and network access are available."
  read -r -p "Press Return to close."
  exit 2
fi

launchctl bootstrap "gui/$(id -u)" "$PLIST"
launchctl kickstart -k "gui/$(id -u)/$LABEL" >/dev/null 2>&1 || true

echo
echo "SmartThings authorization was renewed and the controller restarted."
read -r -p "Press Return to close."
