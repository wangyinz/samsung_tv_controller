#!/bin/bash
# Optional read-only HDMI volume provider, built for the current user's controller.
set -euo pipefail
app_dir="$HOME/Library/Application Support/QN990FController"
if [[ "$(/usr/bin/uname -m)" != arm64 || ! -f "$app_dir/QN990FController.py" ]]; then
  echo "Install the controller on an Apple Silicon Mac first." >&2
  exit 1
fi
/usr/bin/xcrun --find clang >/dev/null
revision=9d75fb6c407ac60dc77a9e32c43d60fd0e6d1612 # m1ddc v1.3.3
checksum=b2c89e18b01ede37d582d2bb7c838737c97bc076c09753252fd6a7508d8fd02b
stage="$(/usr/bin/mktemp -d "${TMPDIR:-/tmp}/samsung-ddc.XXXXXX")"
trap '/bin/rm -rf -- "$stage"' EXIT
cd "$stage"
/usr/bin/curl --proto '=https' --max-time 60 -fsSL "https://codeload.github.com/waydabber/m1ddc/tar.gz/$revision" -o source.tar.gz
printf '%s  source.tar.gz\n' "$checksum" | /usr/bin/shasum -a 256 -c -
/usr/bin/tar -xzf source.tar.gz
/usr/bin/make -C "m1ddc-$revision" m1ddc
/usr/bin/install -m 644 "m1ddc-$revision/LICENSE" "$app_dir/m1ddc-LICENSE"
/usr/bin/install -m 755 "m1ddc-$revision/m1ddc" "$app_dir/m1ddc"
echo "HDMI volume reader installed. The controller retries unavailable readers within five minutes."
