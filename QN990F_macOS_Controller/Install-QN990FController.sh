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
SMARTTHINGS="$APP_DIR/smartthings"
SMARTTHINGS_NO_BROWSER_DIR="$APP_DIR/noninteractive-bin"
SMARTTHINGS_VERSION="2.1.2"
SMARTTHINGS_PROFILE="local.qn990f.picture-controller"
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
TEMP_DIR=""

if [[ "$(uname -s)" != "Darwin" ]]; then
  echo "This installer is for macOS only."
  exit 1
fi

step() {
  printf "\n\033[36m==> %s\033[0m\n" "$1"
}

cleanup() {
  if [[ -n "$TEMP_DIR" && -d "$TEMP_DIR" ]]; then
    rm -rf -- "$TEMP_DIR"
  fi
}

trap cleanup EXIT

stop_agent() {
  launchctl bootout "gui/$(id -u)" "$PLIST" >/dev/null 2>&1 || true
  pkill -f "$CONTROLLER" >/dev/null 2>&1 || true
  sleep 0.2
}

run_smartthings() {
  env -u SMARTTHINGS_TOKEN "$SMARTTHINGS" "$@" --token ""
}

install_smartthings_cli() {
  mkdir -p "$SMARTTHINGS_NO_BROWSER_DIR"
  rm -f "$SMARTTHINGS_NO_BROWSER_DIR/open"
  chmod 700 "$SMARTTHINGS_NO_BROWSER_DIR"

  if [[ -x "$SMARTTHINGS" ]] && \
     [[ "$("$SMARTTHINGS" --version 2>/dev/null || true)" == "$SMARTTHINGS_VERSION" ]]; then
    echo "SmartThings CLI $SMARTTHINGS_VERSION is already installed."
    return
  fi

  local asset checksum machine archive actual
  machine="$(uname -m)"
  case "$machine" in
    arm64)
      asset="smartthings-mac-arm64.tgz"
      checksum="0cd323a2261cddfae67769639d84f4505018c04e3786915f1ca7399495b3948a"
      ;;
    x86_64)
      asset="smartthings-mac-x64.tgz"
      checksum="e822ee5f9cd72fd732ecf96dad7d80d2e70cb05c436d1c7fb3b076c934b974bd"
      ;;
    *)
      echo "Unsupported Mac CPU architecture: $machine"
      exit 1
      ;;
  esac

  TEMP_DIR="$(mktemp -d "$APP_DIR/smartthings-install.XXXXXX")"
  archive="$TEMP_DIR/$asset"
  echo "Downloading pinned SmartThings CLI $SMARTTHINGS_VERSION..."
  curl -fL --retry 3 -o "$archive" \
    "https://github.com/SmartThingsCommunity/smartthings-cli/releases/download/%40smartthings/cli%40${SMARTTHINGS_VERSION}/${asset}"
  actual="$(shasum -a 256 "$archive" | awk '{print $1}')"
  if [[ "$actual" != "$checksum" ]]; then
    echo "SmartThings CLI checksum verification failed."
    exit 1
  fi
  tar -xzf "$archive" -C "$TEMP_DIR"
  install -m 755 "$TEMP_DIR/smartthings" "$SMARTTHINGS"
  cleanup
  TEMP_DIR=""
}

echo "QN990F macOS Picture Controller installer"
echo
echo "Default hotkey: Control + Command + P -> Picture Off"
echo "After this controller blanks the TV: next deliberate keyboard/mouse input -> wake"
echo "Tiny pointer movements are ignored until they accumulate past the wake threshold."
echo
echo "No Accessibility or Input Monitoring permission is required."
echo
echo "Control connection:"
echo "  1. Direct LAN WebSocket (original mode)"
echo "  2. SmartThings cloud (works while Cisco VPN blocks the TV's LAN)"

if [[ ! -f "$SCRIPT_DIR/QN990FController.py" ]]; then
  echo "QN990FController.py is missing. Keep all extracted files together."
  exit 1
fi

read -r -p "Choose connection [1]: " CONNECTION_CHOICE
CONNECTION_CHOICE="${CONNECTION_CHOICE:-1}"
case "$CONNECTION_CHOICE" in
  1|lan|LAN)
    CONTROL_METHOD="lan"
    read -r -p "Enter the QN990F LAN IP address (example: 192.168.1.50): " TV_IP
    TV_IP="${TV_IP//[[:space:]]/}"
    if [[ -z "$TV_IP" ]]; then
      echo "TV IP address cannot be empty."
      exit 1
    fi
    ;;
  2|smartthings|SmartThings)
    CONTROL_METHOD="smartthings"
    TV_IP=""
    ;;
  *)
    echo "Choose 1 or 2."
    exit 1
    ;;
esac

if [[ "$CONTROL_METHOD" == "smartthings" ]]; then
  MACOS_VERSION="$(sw_vers -productVersion)"
  MACOS_MAJOR="${MACOS_VERSION%%.*}"
  MACOS_REST="${MACOS_VERSION#*.}"
  MACOS_MINOR="${MACOS_REST%%.*}"
  if (( MACOS_MAJOR < 13 || (MACOS_MAJOR == 13 && MACOS_MINOR < 5) )); then
    echo "SmartThings cloud mode requires macOS 13.5 or later."
    exit 1
  fi
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
rm -f "$PLIST"

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

SMARTTHINGS_DEVICE_ID=""
SMARTTHINGS_DEVICE_LABEL=""

if [[ "$CONTROL_METHOD" == "smartthings" ]]; then
  step "Installing the SmartThings cloud client"
  install_smartthings_cli

  step "Signing in to SmartThings"
  echo "Before continuing, make sure this TV appears in the SmartThings mobile app."
  echo "Your browser may open for Samsung account sign-in and device authorization."
  TEMP_DIR="$(mktemp -d "$APP_DIR/smartthings-setup.XXXXXX")"
  DEVICE_JSON="$TEMP_DIR/devices.json"
  if ! run_smartthings devices \
      --json \
      --output "$DEVICE_JSON" \
      --profile "$SMARTTHINGS_PROFILE"; then
    echo "Could not list SmartThings TVs. Check the browser sign-in and VPN internet access."
    exit 1
  fi

  FILTERED_JSON="$TEMP_DIR/tvs.json"
  DEVICE_COUNT="$("$PYTHON" - "$DEVICE_JSON" "$FILTERED_JSON" <<'PY'
import json, sys
with open(sys.argv[1], encoding="utf-8") as f:
    value = json.load(f)
devices = value if isinstance(value, list) else value.get("items", [])
def is_qn990f(device):
    if device.get("manufacturerName") != "Samsung Electronics":
        return False
    if device.get("type") != "OCF":
        return False
    model = str(device.get("ocf", {}).get("modelNumber", "")).upper()
    if "QN990F" not in model:
        return False
    for component in device.get("components", []):
        if component.get("id") != "main":
            continue
        ids = [c if isinstance(c, str) else c.get("id") for c in component.get("capabilities", [])]
        categories = [
            c if isinstance(c, str) else c.get("name")
            for c in component.get("categories", [])
        ]
        if "execute" in ids and "samsungvd.remoteControl" in ids and "Television" in categories:
            return True
    return False
devices = [device for device in devices if is_qn990f(device)]
with open(sys.argv[2], "w", encoding="utf-8") as f:
    json.dump(devices, f)
print(len(devices))
PY
)"
  if [[ "$DEVICE_COUNT" == "0" ]]; then
    echo "No QN990F with SmartThings OCF cloud control was found."
    echo "Add the QN990F in the SmartThings mobile app, then rerun this installer."
    exit 1
  fi

  echo
  echo "SmartThings TVs:"
  "$PYTHON" - "$FILTERED_JSON" <<'PY'
import json, sys
with open(sys.argv[1], encoding="utf-8") as f:
    devices = json.load(f)
for index, device in enumerate(devices, 1):
    label = device.get("label") or device.get("name") or "Samsung TV"
    model = device.get("ocf", {}).get("modelNumber") or "model unknown"
    print(f"  {index}. {label} ({model}) [{device.get('deviceId', '')}]")
PY

  if [[ "$DEVICE_COUNT" == "1" ]]; then
    read -r -p "Confirm this is the QN990F [1]: " DEVICE_CHOICE
    DEVICE_CHOICE="${DEVICE_CHOICE:-1}"
  else
    read -r -p "Choose the QN990F: " DEVICE_CHOICE
  fi
  if ! [[ "$DEVICE_CHOICE" =~ ^[0-9]+$ ]] || \
     (( DEVICE_CHOICE < 1 || DEVICE_CHOICE > DEVICE_COUNT )); then
    echo "Invalid TV selection."
    exit 1
  fi

  SMARTTHINGS_DEVICE_ID="$("$PYTHON" - "$FILTERED_JSON" "$DEVICE_CHOICE" <<'PY'
import json, sys
with open(sys.argv[1], encoding="utf-8") as f:
    devices = json.load(f)
print(devices[int(sys.argv[2]) - 1].get("deviceId", ""))
PY
)"
  SMARTTHINGS_DEVICE_LABEL="$("$PYTHON" - "$FILTERED_JSON" "$DEVICE_CHOICE" <<'PY'
import json, sys
with open(sys.argv[1], encoding="utf-8") as f:
    devices = json.load(f)
device = devices[int(sys.argv[2]) - 1]
print(device.get("label") or device.get("name") or "Samsung TV")
PY
)"

  cleanup
  TEMP_DIR=""
fi

step "Writing configuration"
ENABLE_IDLE="true"
if [[ "$IDLE" == "0" || "$IDLE" == "0.0" ]]; then
  ENABLE_IDLE="false"
fi

"$PYTHON" - \
  "$CONFIG" "$CONTROL_METHOD" "$TV_IP" "$IDLE" "$HOTKEY" "$ENABLE_IDLE" \
  "$SMARTTHINGS" "$SMARTTHINGS_NO_BROWSER_DIR" \
  "$SMARTTHINGS_PROFILE" "$SMARTTHINGS_DEVICE_ID" <<'PY'
import json, sys
(
    path, method, ip, idle, hotkey, enable_idle, smartthings_cli,
    smartthings_no_browser_dir, smartthings_profile, smartthings_device_id,
) = sys.argv[1:]
config = {
    "control_method": method,
    "tv_ip": ip,
    "port": 8002,
    "idle_minutes": float(idle),
    "enable_idle_off": enable_idle.lower() == "true",
    "respect_display_required": True,
    "hotkey": hotkey,
    "picture_off_key": "KEY_PICTURE_OFF",
    "wake_key": "KEY_RETURN",
    "wake_guard_ms": 800,
    "poll_interval_ms": 50,
    "input_wake_debounce_ms": 180,
    "mouse_wake_threshold_counts": 24,
    "mouse_motion_window_ms": 500,
    "socket_timeout_seconds": 5.0,
    "key_press_delay_seconds": 0.05,
    "smartthings_cli": smartthings_cli,
    "smartthings_no_browser_dir": smartthings_no_browser_dir,
    "smartthings_profile": smartthings_profile,
    "smartthings_device_id": smartthings_device_id,
    "smartthings_command_timeout_seconds": 20.0,
}
with open(path, "w", encoding="utf-8") as f:
    json.dump(config, f, indent=2)
PY

step "Checking the global shortcut"
"$PYTHON" "$CONTROLLER" --check-hotkey

step "Checking macOS idle-input API"
IDLE_NOW="$("$PYTHON" "$CONTROLLER" --idle)"
echo "Current input idle time: ${IDLE_NOW}s"

if [[ "$CONTROL_METHOD" == "smartthings" ]]; then
  step "Validating SmartThings cloud control"
  echo "Keep the QN990F on and connected to the internet."
else
  step "Pairing with the TV"
  echo "Keep the QN990F on and on the same LAN/subnet as this Mac."
  echo "When the TV asks whether to allow QN990F-Mac-Controller, choose Allow."
fi
"$PYTHON" "$CONTROLLER" --pair

step "Testing Picture Off + wake"
echo "The TV should go black for about 2 seconds and then return."
read -r -p "Run this Picture Off test now? [y/N]: " TEST_READY
if ! [[ "$TEST_READY" =~ ^[Yy]$ ]]; then
  echo "No TV command was sent. Automatic startup will NOT be enabled."
  exit 2
fi
if ! "$PYTHON" "$CONTROLLER" --test; then
  echo "The command sequence failed. Automatic startup will NOT be enabled."
  exit 2
fi
read -r -p "Did the TV actually go black and then come back? [y/N]: " ANSWER
if ! [[ "$ANSWER" =~ ^[Yy]$ ]]; then
  echo
  if [[ "$CONTROL_METHOD" == "smartthings" ]]; then
    echo "The SmartThings OCF Picture Off command was not executed by the TV."
    echo "No background startup was enabled. Check the TV's internet connection and rerun INSTALL.command."
  else
    echo "KEY_PICTURE_OFF or KEY_RETURN may be ignored by this firmware."
  fi
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
echo "  Wake:            key/button/wheel, or intentional pointer movement"
if [[ "$CONTROL_METHOD" == "smartthings" ]]; then
  echo "  Connection:      SmartThings cloud ($SMARTTHINGS_DEVICE_LABEL)"
else
  echo "  Connection:      direct LAN ($TV_IP)"
fi
echo "  Files/logs:      $APP_DIR"
echo
echo "Recommended:"
if [[ "$CONTROL_METHOD" == "lan" ]]; then
  echo "  1. Reserve the TV's IP in your router/DHCP settings."
  echo "  2. Set macOS's own display-off timeout longer than this controller's timeout."
else
  echo "  1. Keep the TV signed in and online in SmartThings."
  echo "  2. Use Control + Command + P for the same Picture Off key as the phone remote."
fi
echo
echo "To reconfigure later, double-click:"
echo "  $APP_DIR/Configure.command"
