#!/bin/bash
set -euo pipefail

APP_DIR="$HOME/Library/Application Support/QN990FController"
PYTHON="$APP_DIR/venv/bin/python"
CONTROLLER="$APP_DIR/QN990FController.py"
CONFIG="$APP_DIR/config.json"
LABEL="local.qn990f.picture-controller"

echo "Samsung TV Picture Controller - Bind TV audio output"
echo
echo "First select the TV controlled by this app as the current macOS sound output."
read -r -p "Is that exact TV selected now? [y/N]: " READY
if ! [[ "$READY" =~ ^[Yy]$ ]]; then exit 1; fi

OUTPUT_JSON="$("$PYTHON" "$CONTROLLER" --audio-output)"
NAME="$("$PYTHON" -c 'import json,sys; print(json.loads(sys.argv[1])["name"])' "$OUTPUT_JSON")"
MAKER="$("$PYTHON" -c 'import json,sys; print(json.loads(sys.argv[1])["manufacturer"])' "$OUTPUT_JSON")"
ADJUSTABLE="$("$PYTHON" -c 'import json,sys; print(str(json.loads(sys.argv[1])["adjustable"]).lower())' "$OUTPUT_JSON")"
PHYSICAL_UID="$("$PYTHON" -c 'import json,sys; print(json.loads(sys.argv[1])["physical_uid"])' "$OUTPUT_JSON")"

echo "Current output: $NAME ($MAKER)"
if [[ "$ADJUSTABLE" == "true" || -z "$PHYSICAL_UID" ]]; then
  echo "This is not a fixed-volume TV audio endpoint. Nothing was changed."
  read -r -p "Press Return to close."
  exit 2
fi
read -r -p "Bind TV volume control to this physical output? [y/N]: " CONFIRM
if ! [[ "$CONFIRM" =~ ^[Yy]$ ]]; then exit 1; fi

if /usr/bin/plutil -extract tv_audio_output_uid raw -o - "$CONFIG" >/dev/null 2>&1; then
  /usr/bin/plutil -replace tv_audio_output_uid -string "$PHYSICAL_UID" "$CONFIG"
else
  /usr/bin/plutil -insert tv_audio_output_uid -string "$PHYSICAL_UID" "$CONFIG"
fi
chmod 600 "$CONFIG"
launchctl kickstart -k "gui/$(id -u)/$LABEL"
echo "Saved the physical TV identity and restarted the controller."
sleep 3
