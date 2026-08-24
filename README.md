# Samsung TV Picture Controller

Use a compatible Samsung TV as a computer display without putting the TV into
standby. The controller sends Samsung's `Picture Off` command, which blanks the
panel while preserving audio and the logical HDMI connection, then restores the
picture when it detects intentional computer input.

The project provides platform-specific background controllers and installers for macOS and
Windows. It is designed to avoid the display re-enumeration, window movement,
and slower wake cycle that can occur when a TV is powered off like a conventional
monitor.

> [!IMPORTANT]
> The Samsung QN990F is the currently verified model. Other Samsung TVs may work
> if their firmware implements the same remote-control capabilities, but they
> are not yet verified. A new installation enables background startup only
> after a visually confirmed compatibility test.

## Features

- Global, configurable `Picture Off` hotkey.
- Automatic picture restore after qualifying keyboard or mouse input.
- Pointer movement alone is ignored by default on both platforms to prevent
  unattended wake events; an advanced opt-in retains anti-jitter filtering.
- Optional automatic `Picture Off` after a configurable period of input inactivity.
- Protection against idle blanking while media or presentation software requests
  that the display remain awake.
- Per-user background startup with no separate server or always-on device.
- Bounded diagnostic logs: one active 1 MB log and three 1 MB backups.
- Isolated Python environment installed without modifying system Python packages.
- Optional keyboard volume integration: local volume is used first when increasing;
  after it reaches maximum, further presses increase TV volume. In SmartThings
  mode, decreasing lowers TV volume to 10 before lowering system volume. On
  macOS fixed-volume outputs such as HDMI, the TV instead uses the full 0–100
  range because there is no adjustable system-volume layer.

The hotkey is intentionally one-way: it always sends `Picture Off`; it is not a
power toggle. Automatic input wake is active only after this controller believes
that it successfully blanked the picture.

## Compatibility

This tool depends on TV capabilities rather than a model-name allowlist:

- **Direct LAN control** requires Samsung's encrypted Tizen WebSocket remote
  service on TCP port `8002`, plus firmware support for `KEY_PICTURE_OFF` and a
  wake key such as `KEY_RETURN`.
- **SmartThings cloud control** requires a Samsung OCF Television device exposing
  the `execute` and `samsungvd.remoteControl` capabilities. The optional integrated
  volume control additionally requires `audioVolume`. Discovery confirms the API
  shape, not that the firmware will execute `Picture Off`, so visual confirmation
  remains mandatory.

| Scope | Status |
| --- | --- |
| Samsung QN990F | Primary development and validation model |
| Other Samsung Tizen TVs with the required LAN keys | Potentially compatible; not yet verified |
| Other Samsung OCF TVs with the required SmartThings capabilities | Potentially compatible; not yet verified |
| TVs without Samsung `Picture Off` support | Not compatible |

Compatibility can vary by model, region, firmware version, and enabled TV
settings. Reports for additional models are welcome.

## Platform comparison

| Capability | macOS | Windows |
| --- | --- | --- |
| Direct LAN WebSocket | Yes | Yes |
| SmartThings cloud | Yes | Yes |
| Optional integrated system/TV volume keys | Yes | Yes |
| Default hotkey | `Control+Command+P` | `Ctrl+Alt+P` |
| Intentional input wake | Keyboard, mouse button, or wheel | Keyboard, mouse button, or wheel |
| Pointer anti-jitter | Disabled by default; optional 24 screen-coordinate points within 500 ms | Disabled by default; optional 24 Raw Input counts per device within 500 ms |
| Software-generated pointer filtering | Prevented by disabling movement-only wake | Prevented by disabling movement-only wake; optional Raw Input excludes ordinary `SendInput`-style activity |
| Per-device input exclusion | No | Yes, by Raw Input device-path substring |
| Media/presentation protection | macOS display power assertions | Windows `ES_DISPLAY_REQUIRED` |
| Background startup | Per-user LaunchAgent | Per-user Startup shortcut |
| Elevated privileges | Not required | Not required for the controller; the installer may use `winget` to install a missing user-scoped Python runtime |

When volume control is enabled, the macOS controller uses IOHID matching only
for the standard Volume Up and Volume Down usages, so macOS Input Monitoring
permission is required.
Wake detection
continues to use anonymous system event counters and does not read typed text.
The Windows controller uses Raw Input for wake detection and a low-level hook
limited to `Volume Up` and `Volume Down`.

Only one macOS utility can take exclusive ownership of the native audio keys.
If BetterDisplay is installed, open **BetterDisplay → Settings → Keyboard →
Native (Apple) Keyboard Control** and turn off **Listen to native audio keys**
before enabling this controller's volume routing. BetterDisplay can remain
running for its other display features.

## Connection modes

### Direct LAN WebSocket

Direct LAN is available on both platforms and is the default. The computer
connects to the configured TV address on port `8002`; the TV normally displays a
remote-control pairing prompt on first use.

The computer and TV generally need to be reachable on the same local network.
A full-tunnel or centrally managed VPN may intentionally block local-network
traffic even when both devices use addresses on the same subnet. When that
happens, use an administrator-approved local-network access policy, disconnect
the VPN when permitted, or use SmartThings cloud mode. This project
does not modify routes or bypass VPN security policy.

### SmartThings cloud

SmartThings cloud mode is available on macOS 13.5 or later and on Windows. It
uses the public SmartThings HTTPS service through the official SmartThings CLI,
so it can work when a VPN or network policy blocks direct access to the TV's
private LAN address. The macOS installer supplies a pinned CLI; Windows users
also receive a private pinned CLI automatically. Both installers verify the
official release archive before using it; no separate Windows MSI is required.

Requirements:

- The TV appears in the SmartThings mobile app under the same Samsung account.
- The TV and computer both have internet access.
- The network permits access to Samsung's sign-in and SmartThings services.
- The discovered TV exposes the required OCF remote-control capabilities.

Cloud mode does not silently fall back to LAN mode. It depends on an internet
service, is usually slower than local control, and may be affected by API or
firmware changes. The controller makes a read-only SmartThings device request
at startup and every 30 minutes. This lets the official CLI refresh credentials
before a hotkey is needed and detects failed refreshes early; it does not prevent
SmartThings from revoking a refresh token.

## Installation

Download or clone the repository and keep all files in the selected platform
directory together.

### macOS

1. Open [`QN990F_macOS_Controller`](QN990F_macOS_Controller/).
2. Double-click [`INSTALL.command`](QN990F_macOS_Controller/INSTALL.command).
3. Choose Direct LAN or SmartThings cloud and follow the prompts.
4. Approve the two-second `Picture Off` and restore test when you are ready to
   interrupt the TV picture, then confirm what you observed.

If Gatekeeper blocks the downloaded script, Control-click `INSTALL.command`,
choose **Open**, and confirm once more. Homebrew, Xcode Command Line Tools,
and `sudo` are not required. Input Monitoring is needed only for optional
volume-key routing; add the installed controller Python runtime to **System
Settings → Privacy & Security → Input Monitoring** when macOS prompts. If
permission is not granted, Picture Off control continues and only volume-key
routing is disabled for that run.

See the [macOS guide](QN990F_macOS_Controller/README.txt) for detailed setup,
configuration, file locations, and manual commands.

### Windows

1. Open [`QN990F_Windows_Controller`](QN990F_Windows_Controller/).
2. Double-click [`INSTALL-ME.cmd`](QN990F_Windows_Controller/INSTALL-ME.cmd).
3. Choose Direct LAN or SmartThings cloud. In cloud mode, the installer
   downloads and verifies its private copy of the official SmartThings CLI.
4. Choose whether to install integrated volume-key control.
5. Approve the two-second `Picture Off` and restore test, then confirm what you
   observed.

The installer uses an existing Python 3.9 or later when available. Otherwise,
it can install Python 3.12 for the current user through `winget`, then creates an
isolated virtual environment and a per-user Startup shortcut.

See the [Windows guide](QN990F_Windows_Controller/README.txt) for detailed
setup, upgrades, configuration, and manual commands.

Some repository filenames, application-data paths, service labels, and OAuth
profile names retain the original `QN990F` identifier. They are preserved to
avoid needlessly breaking existing installation paths, pairing identity,
startup registration, or SmartThings sign-in. They do not restrict TV model
compatibility.

## Usage

Press the configured hotkey to blank the picture:

- macOS default: `Control+Command+P`
- Windows default: `Ctrl+Alt+P`

Afterward, press a non-modifier key, click a mouse button, or use the wheel to
restore the picture. Modifier keys alone do not wake it.

The hardware volume keys form one control range across the computer and TV:

Integrated volume control is optional during installation. Rerun the installer
to enable or disable it; an upgrade keeps the current choice when the prompt is
left blank.

- `Volume Up` changes system volume until it is full, then sends TV volume-up.
- In SmartThings mode, `Volume Down` lowers TV volume to the configured floor
  (10 by default), then resumes normal system-volume reduction. On macOS, a
  fixed-volume output changes this floor to 0 automatically.
- SmartThings TV-volume steps received within 200 ms are combined locally. Up
  and down steps cancel each other, and one final target volume is sent.
- Direct LAN can send TV volume keys but cannot query the TV's current volume,
  so the safe TV-first decrease rule is available only in SmartThings mode.

Pointer movement alone is disabled as a wake source on both platforms by
default. Advanced users can set `enable_mouse_move_wake` to `true` in
`config.json` and restart the controller. Windows then uses per-device Raw Input
and its anti-jitter threshold. On macOS, the zero-permission event counters
include cursor movement posted by software and cannot reliably attribute its
source without Input Monitoring access, so opt-in synthetic movement may wake
the TV.

The optional idle mode sends `Picture Off` after the configured number of
minutes without keyboard or mouse input. Set the interval to `0` to disable it.
Set the operating system's own display-off timeout longer than the controller's
interval so the HDMI path remains active long enough for this workflow.

Reconfiguration tools are installed with each platform version:

- macOS: `~/Library/Application Support/QN990FController/Configure.command`
- Windows: the controller's **Configure** shortcut in the Start menu

## Security and privacy

- Installation and background startup are per-user; the controller does not
  require root or administrator access.
- Direct LAN mode stores the Samsung pairing token locally in the controller's
  application-data directory. Treat this file as a credential.
- In cloud mode, the official SmartThings CLI handles interactive OAuth sign-in
  and stores its tokens in its own user-level data directory with restrictive
  permissions. The controller's `config.json` does not contain a SmartThings
  access token.
- Background cloud commands are non-interactive and serialized across controller
  processes. If token refresh fails, the command stops and the controller shows
  one authorization alert. Choosing **Reauthorize** opens a dedicated helper
  that stops the controller, completes browser sign-in, and restarts it without
  sending a TV command.
- The project adds no application telemetry. It writes local status and
  diagnostic files only. Windows logs may include Raw Input device paths; macOS
  logs input categories but not typed text. Windows wake records may also
  include the single virtual-key code that caused a wake.
- The active log and three rotating backups retain at most approximately 4 MB
  per installation.

Installers download runtime components and dependencies from their documented
upstream sources. Both installers verify the pinned SmartThings CLI archive
checksum.

## Limitations

- `Picture Off` is not operating-system display sleep and does not put the TV
  into standby. Its purpose is to keep the HDMI session logically connected.
- Samsung firmware may accept a remote command without executing it, which is
  why visual verification is required.
- The default restore key is `KEY_RETURN`. Some firmware may require another
  non-power key such as `KEY_UP`; consult the platform guide before changing
  `wake_key` in `config.json`.
- Input wake is not guaranteed when the picture was turned off from a TV menu,
  voice assistant, mobile app, or another remote because the controller may not
  know the TV's current picture state.
- Automatic idle protection depends on applications correctly requesting that
  the operating system keep the display awake.
- macOS cannot identify or ignore one particular input device. Windows can
  exclude a noisy or virtual device using a case-insensitive Raw Input
  device-path substring in `config.json`.
- SmartThings cloud control uses a private Samsung TV OCF remote-control resource
  through a deprecated `execute` compatibility entry point. Samsung may change
  or remove that behavior.
- SmartThings rate limits apply. Avoid repeatedly sending `Picture Off` and wake
  commands in quick succession.
- SmartThings cloud authentication remains dependent on Samsung's OAuth service
  and the official CLI. The proactive check detects failures earlier but cannot
  guarantee that SmartThings will continue accepting a saved refresh token.
- Volume-key interception depends on the active user's desktop session. If
  macOS denies access, approve Input Monitoring for the installed Python
  runtime.

## Troubleshooting

### Direct LAN pairing times out

- Confirm the TV is on and the configured address is correct.
- Confirm the computer can reach the TV on TCP port `8002`.
- Check whether a VPN, endpoint-security product, guest Wi-Fi, or router setting
  blocks communication between local devices.
- Remove a previously denied remote from the TV's Device Connection Manager,
  then pair again.
- Reserve the TV's address in DHCP so it does not change later.

### SmartThings does not list the TV

- Add the TV to the SmartThings mobile app and confirm it is online.
- Sign in with the same Samsung account used by the mobile app.
- Confirm the network permits Samsung sign-in and SmartThings API access.
- If authorization has expired, choose **Reauthorize** in the alert, run
  `Reauthorize.command` on macOS, or choose **Reauthorize SmartThings** from the
  Windows Start menu. This validation is read-only and sends no TV command.

### The hotkey does nothing

- Make sure another application has not registered the same global shortcut.
- Confirm the background controller is running.
- Review `controller.log` and `status.json` in the platform application-data
  directory shown in its guide.
- Run the configuration tool to validate a replacement hotkey.

### The picture wakes unexpectedly

- Check `controller.log` for the qualifying input category.
- On Windows, the log includes the Raw Input device path. Add a unique substring
  to `ignored_input_device_substrings` only after identifying the noisy device.
- Pointer movement alone is ignored by default on both platforms. Check whether
  `enable_mouse_move_wake` was manually enabled if the log reports a
  `mouse_move` wake.

### Picture Off works but restore does not

Some firmware ignores `KEY_RETURN`. Change `wake_key` to `KEY_UP` in
`config.json`, then restart or reconfigure the controller. Manual test commands
in the platform guides send real TV commands; run them only when interrupting
the picture is acceptable.

## Uninstallation

- macOS: double-click the installed
  `~/Library/Application Support/QN990FController/Uninstall.command`, or use the
  copy in [`QN990F_macOS_Controller`](QN990F_macOS_Controller/Uninstall.command).
- Windows: use the controller's **Uninstall** shortcut in the Start menu, or run
  [`Uninstall-QN990FController.ps1`](QN990F_Windows_Controller/Uninstall-QN990FController.ps1).

Uninstallation stops background startup and removes the controller's local
configuration, LAN pairing token, private runtime, status, and logs. It does not
change the TV or revoke its Device Connection Manager entry. On both platforms,
it also leaves the SmartThings CLI's shared OAuth profile unchanged.

## Development and tests

The controllers intentionally use small, platform-native Python implementations
with installer scripts rather than a shared cross-platform abstraction. Keep
platform behavior aligned where the operating systems expose equivalent input
and power-management features.

Run the macOS unit suite on macOS:

```sh
python3 -B -m unittest discover -s QN990F_macOS_Controller/tests -v
```

Validate the macOS shell entry points without sending TV commands:

```sh
bash -n QN990F_macOS_Controller/INSTALL.command \
  QN990F_macOS_Controller/Install-QN990FController.sh \
  QN990F_macOS_Controller/Configure.command \
  QN990F_macOS_Controller/Uninstall.command
```

The Windows installer runs the controller's ctypes and input-policy self-test
before replacing an existing installation. On a Windows development system
with `samsungtvws` installed, it can also be invoked directly:

```powershell
python .\QN990F_Windows_Controller\QN990FController.py --self-test
```

Do not use `--test`, `--off`, or `--wake` during automated testing: those modes
send real commands to the configured TV.

## Contributing

Issues and pull requests are welcome. For compatibility reports, include the TV
model, region, firmware version, operating system, connection mode, and observed
results. Redact IP addresses, pairing tokens, SmartThings credentials, and Raw
Input device identifiers before sharing logs.

Changes should preserve user consent for visual TV tests, keep macOS and Windows
wake semantics aligned where practical, and include focused regression tests for
behavioral fixes.

## License

This project is available under the [MIT License](LICENSE).
