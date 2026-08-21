#!/bin/bash
set -euo pipefail

APP_DIR="$HOME/Library/Application Support/QN990FController"
PYTHON="$APP_DIR/venv/bin/python"
CONTROLLER="$APP_DIR/QN990FController.py"
CONFIG="$APP_DIR/config.json"
PLIST="$HOME/Library/LaunchAgents/local.qn990f.picture-controller.plist"
STAGED_PLIST="$APP_DIR/local.qn990f.picture-controller.plist.disabled"
LABEL="local.qn990f.picture-controller"

if [[ ! -x "$PYTHON" || ! -f "$CONFIG" ]]; then
  echo "Samsung TV Picture Controller is not installed."
  read -r -p "Press Return to close."
  exit 1
fi

stop_agent() {
  launchctl bootout "gui/$(id -u)" "$PLIST" >/dev/null 2>&1 || true
  pkill -f "$CONTROLLER" >/dev/null 2>&1 || true
  sleep 0.2
}

get_value() {
  "$PYTHON" - "$CONFIG" "$1" <<'PY'
import json, sys
with open(sys.argv[1], encoding="utf-8") as f:
    c = json.load(f)
v = c.get(sys.argv[2], "")
if isinstance(v, bool):
    print("true" if v else "false")
else:
    print(v)
PY
}

TV_IP="$(get_value tv_ip)"
CONTROL_METHOD="$(get_value control_method)"
CONTROL_METHOD="${CONTROL_METHOD:-lan}"
IDLE="$(get_value idle_minutes)"
HOTKEY="$(get_value hotkey)"
RESPECT="$(get_value respect_display_required)"
SMARTTHINGS_DEVICE_ID="$(get_value smartthings_device_id)"

echo "Samsung TV Picture Controller configuration"
echo "Press Return to keep the current value."
echo "Connection: $CONTROL_METHOD"
echo

NEW_IP="$TV_IP"
if [[ "$CONTROL_METHOD" == "smartthings" ]]; then
  echo "SmartThings TV ID: $SMARTTHINGS_DEVICE_ID"
  echo "To select a different TV or switch connection type, rerun INSTALL.command."
else
  read -r -p "TV IP [$TV_IP]: " NEW_IP
  NEW_IP="${NEW_IP:-$TV_IP}"
fi

read -r -p "Idle minutes; 0 disables auto blank [$IDLE]: " NEW_IDLE
NEW_IDLE="${NEW_IDLE:-$IDLE}"
if ! [[ "$NEW_IDLE" =~ ^([0-9]+([.][0-9]*)?|[.][0-9]+)$ ]]; then
  echo "Idle minutes must be a number >= 0."
  read -r -p "Press Return to close."
  exit 1
fi

read -r -p "Global hotkey [$HOTKEY]: " NEW_HOTKEY
NEW_HOTKEY="${NEW_HOTKEY:-$HOTKEY}"

DEFAULT_RESPECT="Y"
if [[ "$RESPECT" == "false" ]]; then DEFAULT_RESPECT="N"; fi
read -r -p "Respect macOS media/display-sleep assertions? [$DEFAULT_RESPECT]: " NEW_RESPECT
NEW_RESPECT="${NEW_RESPECT:-$DEFAULT_RESPECT}"

stop_agent
if [[ -f "$PLIST" ]]; then
  mv -f "$PLIST" "$STAGED_PLIST"
elif [[ ! -f "$STAGED_PLIST" ]]; then
  echo "The LaunchAgent file is missing. Rerun INSTALL.command."
  read -r -p "Press Return to close."
  exit 1
fi

"$PYTHON" - \
  "$CONFIG" "$NEW_IP" "$NEW_IDLE" "$NEW_HOTKEY" "$NEW_RESPECT" <<'PY'
import json, sys
path, ip, idle, hotkey, respect = sys.argv[1:]
with open(path, encoding="utf-8") as f:
    c = json.load(f)
c["tv_ip"] = ip.strip()
c["idle_minutes"] = float(idle)
c["enable_idle_off"] = float(idle) > 0
c["hotkey"] = hotkey.strip()
c["respect_display_required"] = not respect.lower().startswith("n")
with open(path, "w", encoding="utf-8") as f:
    json.dump(c, f, indent=2)
PY

echo
echo "Checking shortcut..."
if ! "$PYTHON" "$CONTROLLER" --check-hotkey; then
  echo "The selected shortcut cannot be registered."
  read -r -p "Press Return to close."
  exit 2
fi

echo
echo "Validating TV connection..."
if ! "$PYTHON" "$CONTROLLER" --pair; then
  if [[ "$CONTROL_METHOD" == "smartthings" ]]; then
    echo "Could not access the TV through SmartThings. Check sign-in and internet access."
  else
    echo "Could not connect/pair. Check TV IP/network."
  fi
  read -r -p "Press Return to close."
  exit 3
fi

if [[ "$CONTROL_METHOD" == "smartthings" ]]; then
  echo
  echo "Testing Picture Off + wake..."
  read -r -p "Run this Picture Off test now? [y/N]: " TEST_READY
  if ! [[ "$TEST_READY" =~ ^[Yy]$ ]]; then
    echo "No TV command was sent. The controller remains stopped."
    read -r -p "Press Return to close."
    exit 4
  fi
  if ! "$PYTHON" "$CONTROLLER" --test; then
    echo "The SmartThings Picture Off command failed. The controller remains stopped."
    read -r -p "Press Return to close."
    exit 4
  fi
  read -r -p "Did the TV actually go black and then come back? [y/N]: " TEST_ANSWER
  if ! [[ "$TEST_ANSWER" =~ ^[Yy]$ ]]; then
    echo "Check the TV's internet connection, then run Configure.command again."
    echo "The controller remains stopped."
    read -r -p "Press Return to close."
    exit 4
  fi
fi

mv -f "$STAGED_PLIST" "$PLIST"
launchctl bootstrap "gui/$(id -u)" "$PLIST"
launchctl kickstart -k "gui/$(id -u)/$LABEL" >/dev/null 2>&1 || true

echo
echo "Saved and restarted."
echo "Hotkey: $NEW_HOTKEY"
echo "Connection: $CONTROL_METHOD"
if [[ "$NEW_IDLE" == "0" || "$NEW_IDLE" == "0.0" ]]; then
  echo "Auto Picture Off: disabled"
else
  echo "Auto Picture Off: after $NEW_IDLE minute(s)"
fi
sleep 2
