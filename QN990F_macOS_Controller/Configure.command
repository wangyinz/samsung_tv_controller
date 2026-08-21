#!/bin/bash
set -euo pipefail

APP_DIR="$HOME/Library/Application Support/QN990FController"
PYTHON="$APP_DIR/venv/bin/python"
CONTROLLER="$APP_DIR/QN990FController.py"
CONFIG="$APP_DIR/config.json"
PLIST="$HOME/Library/LaunchAgents/local.qn990f.picture-controller.plist"
LABEL="local.qn990f.picture-controller"

if [[ ! -x "$PYTHON" || ! -f "$CONFIG" ]]; then
  echo "QN990F Controller is not installed."
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
IDLE="$(get_value idle_minutes)"
HOTKEY="$(get_value hotkey)"
RESPECT="$(get_value respect_display_required)"

echo "QN990F macOS Controller configuration"
echo "Press Return to keep the current value."
echo

read -r -p "TV IP [$TV_IP]: " NEW_IP
NEW_IP="${NEW_IP:-$TV_IP}"

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

"$PYTHON" - "$CONFIG" "$NEW_IP" "$NEW_IDLE" "$NEW_HOTKEY" "$NEW_RESPECT" <<'PY'
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
echo "Pairing/validating TV connection..."
if ! "$PYTHON" "$CONTROLLER" --pair; then
  echo "Could not connect/pair. Check TV IP/network."
  read -r -p "Press Return to close."
  exit 3
fi

launchctl bootstrap "gui/$(id -u)" "$PLIST"
launchctl kickstart -k "gui/$(id -u)/$LABEL" >/dev/null 2>&1 || true

echo
echo "Saved and restarted."
echo "Hotkey: $NEW_HOTKEY"
if [[ "$NEW_IDLE" == "0" || "$NEW_IDLE" == "0.0" ]]; then
  echo "Auto Picture Off: disabled"
else
  echo "Auto Picture Off: after $NEW_IDLE minute(s)"
fi
sleep 2
