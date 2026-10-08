#!/bin/bash
set -euo pipefail

APP_DIR="$HOME/Library/Application Support/QN990FController"
PYTHON="$APP_DIR/venv/bin/python"
HELPER="$APP_DIR/Rebind-HDMI.py"

if [[ ! -x "$PYTHON" || ! -f "$HELPER" ]]; then
  echo "The installed HDMI rebinding helper is missing. Rerun INSTALL.command."
  read -r -p "Press Return to close."
  exit 1
fi

set +e
"$PYTHON" "$HELPER"
RESULT=$?
set -e
read -r -p "Press Return to close."
exit "$RESULT"
