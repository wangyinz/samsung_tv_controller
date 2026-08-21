#!/bin/bash
set -euo pipefail

APP_DIR="$HOME/Library/Application Support/QN990FController"
VENV_DIR="$APP_DIR/venv"
PYTHON="$VENV_DIR/bin/python"
CONTROLLER="$APP_DIR/QN990FController.py"
CONFIG="$APP_DIR/config.json"
PLIST="$HOME/Library/LaunchAgents/local.qn990f.picture-controller.plist"
LABEL="local.qn990f.picture-controller"
UV_DIR="$APP_DIR/bootstrap"
UV="$UV_DIR/uv"
UV_VERSION="0.12.5"
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

if [[ "$(uname -s)" != "Darwin" ]]; then
  echo "This installer is for macOS only."
  exit 1
fi

step() {
  printf "\n\033[36m==> %s\033[0m\n" "$1"
}

stop_agent() {
  launchctl bootout "gui/$(id -u)" "$PLIST" >/dev/null 2>&1 || true
  pkill -f "$CONTROLLER" >/dev/null 2>&1 || true
  sleep 0.2
}

echo "QN990F macOS Picture Controller installer"
echo
echo "Default hotkey: Control + Command + P -> Picture Off"
echo "After this controller blanks the TV: next keyboard/mouse input -> wake"
echo
echo "No Accessibility or Input Monitoring permission is required."

if [[ ! -f "$SCRIPT_DIR/QN990FController.py" ]]; then
  echo "QN990FController.py is missing. Keep all extracted files together."
  exit 1
fi

read -r -p "Enter the QN990F LAN IP address (example: 192.168.1.50): " TV_IP
TV_IP="${TV_IP//[[:space:]]/}"
if [[ -z "$TV_IP" ]]; then
  echo "TV IP address cannot be empty."
  exit 1
fi

read -r -p "Idle minutes before automatic Picture Off [10; enter 0 to disable]: " IDLE
IDLE="${IDLE:-10}"
if ! [[ "$IDLE" =~ ^([0-9]+([.][0-9]*)?|[.][0-9]+)$ ]]; then
  echo "Idle minutes must be a number >= 0."
  exit 1
fi

HOTKEY="Ctrl+Cmd+P"

mkdir -p "$APP_DIR" "$HOME/Library/LaunchAgents"
stop_agent

step "Copying controller files"
cp "$SCRIPT_DIR/QN990FController.py" "$CONTROLLER"
cp "$SCRIPT_DIR/Configure.command" "$APP_DIR/Configure.command"
cp "$SCRIPT_DIR/Uninstall.command" "$APP_DIR/Uninstall.command"
cp "$SCRIPT_DIR/README.txt" "$APP_DIR/README.txt"
chmod 755 "$CONTROLLER" "$APP_DIR/Configure.command" "$APP_DIR/Uninstall.command"

step "Installing an isolated Python runtime"
mkdir -p "$UV_DIR"

if [[ ! -x "$UV" ]]; then
  echo "Downloading pinned uv $UV_VERSION from Astral's official installer..."
  curl -LsSf "https://astral.sh/uv/${UV_VERSION}/install.sh" | \
    env UV_UNMANAGED_INSTALL="$UV_DIR" sh
fi

export UV_PYTHON_INSTALL_DIR="$APP_DIR/python"
export UV_CACHE_DIR="$APP_DIR/cache"
export UV_NO_MODIFY_PATH=1
export UV_MANAGED_PYTHON=1

rm -rf "$VENV_DIR"
"$UV" venv --python 3.12 "$VENV_DIR"
"$UV" pip install --python "$PYTHON" "samsungtvws==3.0.5"

step "Writing configuration"
ENABLE_IDLE="true"
if [[ "$IDLE" == "0" || "$IDLE" == "0.0" ]]; then
  ENABLE_IDLE="false"
fi

"$PYTHON" - "$CONFIG" "$TV_IP" "$IDLE" "$HOTKEY" "$ENABLE_IDLE" <<'PY'
import json, sys
path, ip, idle, hotkey, enable_idle = sys.argv[1:]
config = {
    "tv_ip": ip,
    "port": 8002,
    "idle_minutes": float(idle),
    "enable_idle_off": enable_idle.lower() == "true",
    "respect_display_required": True,
    "hotkey": hotkey,
    "picture_off_key": "KEY_PICTURE_OFF",
    "wake_key": "KEY_RETURN",
    "wake_guard_ms": 700,
    "poll_interval_ms": 100,
    "socket_timeout_seconds": 5.0,
    "key_press_delay_seconds": 0.05,
}
with open(path, "w", encoding="utf-8") as f:
    json.dump(config, f, indent=2)
PY

step "Checking the global shortcut"
"$PYTHON" "$CONTROLLER" --check-hotkey

step "Checking macOS idle-input API"
IDLE_NOW="$("$PYTHON" "$CONTROLLER" --idle)"
echo "Current input idle time: ${IDLE_NOW}s"

step "Pairing with the TV"
echo "Keep the QN990F on and on the same LAN/subnet as this Mac."
echo "When the TV asks whether to allow QN990F-Mac-Controller, choose Allow."
"$PYTHON" "$CONTROLLER" --pair

step "Testing Picture Off + wake"
echo "The TV should go black for about 2 seconds and then return."
"$PYTHON" "$CONTROLLER" --test || true
read -r -p "Did the TV actually go black and then come back? [Y/n]: " ANSWER
ANSWER="${ANSWER:-Y}"
if [[ "$ANSWER" =~ ^[Nn] ]]; then
  echo
  echo "KEY_PICTURE_OFF or KEY_RETURN may be ignored by this firmware."
  echo "The files are installed, but automatic startup will NOT be enabled."
  echo "You can retest later with:"
  printf '  "%s" "%s" --test\n' "$PYTHON" "$CONTROLLER"
  exit 2
fi

step "Installing the per-user LaunchAgent"
cat > "$PLIST" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>$LABEL</string>
    <key>ProgramArguments</key>
    <array>
        <string>$PYTHON</string>
        <string>$CONTROLLER</string>
    </array>
    <key>RunAtLoad</key>
    <true/>
    <key>ProcessType</key>
    <string>Interactive</string>
    <key>StandardOutPath</key>
    <string>$APP_DIR/launchd.out.log</string>
    <key>StandardErrorPath</key>
    <string>$APP_DIR/launchd.err.log</string>
</dict>
</plist>
PLIST

plutil -lint "$PLIST" >/dev/null
launchctl bootstrap "gui/$(id -u)" "$PLIST"
launchctl kickstart -k "gui/$(id -u)/$LABEL" >/dev/null 2>&1 || true
sleep 1

step "Verifying background startup"
if launchctl print "gui/$(id -u)/$LABEL" >/dev/null 2>&1; then
  echo "LaunchAgent is loaded."
else
  echo "Warning: LaunchAgent did not appear loaded."
  echo "Check: $APP_DIR/launchd.err.log"
fi

echo
printf "\033[32mInstalled.\033[0m\n"
echo "  Hotkey:          Control + Command + P"
if [[ "$ENABLE_IDLE" == "true" ]]; then
  echo "  Automatic blank: after $IDLE minute(s) of keyboard/mouse inactivity"
else
  echo "  Automatic blank: disabled"
fi
echo "  Wake:            next keyboard/mouse input after this controller blanked the TV"
echo "  Files/logs:      $APP_DIR"
echo
echo "Recommended:"
echo "  1. Reserve the TV's IP in your router/DHCP settings."
echo "  2. Set macOS's own display-off timeout longer than this controller's timeout."
echo
echo "To reconfigure later, double-click:"
echo "  $APP_DIR/Configure.command"
