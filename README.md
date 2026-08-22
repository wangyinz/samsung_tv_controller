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
- Raw-motion anti-jitter filtering on Windows; macOS ignores pointer movement by
  default to prevent unattended software-generated wake events.
- Optional automatic `Picture Off` after a configurable period of input inactivity.
- Protection against idle blanking while media or presentation software requests
  that the display remain awake.
- Per-user background startup with no separate server or always-on device.
- Bounded diagnostic logs: one active 1 MB log and three 1 MB backups.
- Isolated Python environment installed without modifying system Python packages.

The hotkey is intentionally one-way: it always sends `Picture Off`; it is not a
power toggle. Automatic input wake is active only after this controller believes
that it successfully blanked the picture.

## Compatibility

This tool depends on TV capabilities rather than a model-name allowlist:

- **Direct LAN control** requires Samsung's encrypted Tizen WebSocket remote
  service on TCP port `8002`, plus firmware support for `KEY_PICTURE_OFF` and a
  wake key such as `KEY_RETURN`.
- **SmartThings cloud control** requires a Samsung OCF Television device exposing
  the `execute` and `samsungvd.remoteControl` capabilities. Discovery confirms
  the API shape, not that the firmware will execute `Picture Off`, so visual
  confirmation remains mandatory.

| Scope | Status |
| --- | --- |
| Samsung QN990F | Primary development and validation model |
| Other Samsung Tizen TVs with the required LAN keys | Potentially compatible; not yet verified |
| Other Samsung OCF TVs with the required SmartThings capabilities | Potentially compatible on macOS; not yet verified |
| TVs without Samsung `Picture Off` support | Not compatible |

Compatibility can vary by model, region, firmware version, and enabled TV
settings. Reports for additional models are welcome.

## Platform comparison

| Capability | macOS | Windows |
| --- | --- | --- |
| Direct LAN WebSocket | Yes | Yes |
| SmartThings cloud | Yes | No |
| Default hotkey | `Control+Command+P` | `Ctrl+Alt+P` |
| Intentional input wake | Keyboard, mouse button, or wheel | Keyboard, button, wheel, or pointer movement |
| Pointer anti-jitter | Pointer movement disabled by default | 24 Raw Input counts per device within 500 ms |
| Software-generated pointer filtering | Prevented by disabling movement-only wake | Raw Input excludes ordinary `SendInput`-style activity |
| Per-device input exclusion | No | Yes, by Raw Input device-path substring |
| Media/presentation protection | macOS display power assertions | Windows `ES_DISPLAY_REQUIRED` |
| Background startup | Per-user LaunchAgent | Per-user Startup shortcut |
| Elevated privileges | Not required | Not required for the controller; the installer may use `winget` to install a missing user-scoped Python runtime |

The macOS controller does not require Accessibility or Input Monitoring
permission. It observes system event counters and pointer position rather than
installing an event tap or reading typed text. The Windows controller uses a
hidden Raw Input window; it classifies key-down events to exclude modifiers but
does not capture text or continuous key sequences. Its log may contain the
single virtual-key code that triggered a wake.

## Connection modes

### Direct LAN WebSocket

Direct LAN is available on both platforms and is the default. The computer
connects to the configured TV address on port `8002`; the TV normally displays a
remote-control pairing prompt on first use.

The computer and TV generally need to be reachable on the same local network.
A full-tunnel or centrally managed VPN may intentionally block local-network
traffic even when both devices use addresses on the same subnet. When that
happens, use an administrator-approved local-network access policy, disconnect
the VPN when permitted, or use SmartThings cloud mode on macOS. This project
does not modify routes or bypass VPN security policy.

### SmartThings cloud

SmartThings cloud mode is available on macOS 13.5 or later. It uses the public
SmartThings HTTPS service through a pinned version of the official SmartThings
CLI, so it can work when a VPN or network policy blocks direct access to the TV's
private LAN address.

Requirements:

- The TV appears in the SmartThings mobile app under the same Samsung account.
- The TV and Mac both have internet access.
- The network permits access to Samsung's sign-in and SmartThings services.
- The discovered TV exposes the required OCF remote-control capabilities.

Cloud mode does not silently fall back to LAN mode. It depends on an internet
service, is usually slower than local control, and may be affected by API or
firmware changes.

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
`sudo`, Accessibility permission, and Input Monitoring permission are not
required.

See the [macOS guide](QN990F_macOS_Controller/README.txt) for detailed setup,
configuration, file locations, and manual commands.

### Windows

1. Open [`QN990F_Windows_Controller`](QN990F_Windows_Controller/).
2. Double-click [`INSTALL-ME.cmd`](QN990F_Windows_Controller/INSTALL-ME.cmd).
3. Enter the TV's LAN address and choose **Allow** on the TV's pairing prompt.
4. Approve the two-second `Picture Off` and restore test, then confirm what you
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
restore the picture. On Windows, deliberate pointer movement also wakes after
crossing the anti-jitter threshold. Modifier keys alone do not wake it.

Pointer movement alone is disabled as a wake source on macOS by default. The
zero-permission macOS event counters include cursor movement posted by software,
and the operating system does not provide reliable source attribution without
Input Monitoring access. Advanced users can set `enable_mouse_move_wake` to
`true` in `config.json` and restart the controller, but software-generated
movement may then wake the TV.

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
- Background cloud commands are non-interactive. If token refresh fails, the
  command stops instead of opening a sign-in page; reauthenticate through the
  macOS configuration tool.
- The project adds no application telemetry. It writes local status and
  diagnostic files only. Windows logs may include Raw Input device paths; macOS
  logs input categories but not typed text. Windows wake records may also
  include the single virtual-key code that caused a wake.
- The active log and three rotating backups retain at most approximately 4 MB
  per installation.

Installers download runtime components and dependencies from their documented
upstream sources. The macOS installer verifies the pinned SmartThings CLI
archive checksum.

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
- If credentials have expired, run the macOS configuration tool and sign in
  again during its interactive verification step.

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
- On macOS, pointer movement alone is ignored by default. Check whether
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
change the TV or revoke its Device Connection Manager entry. On macOS, it also
leaves the SmartThings CLI's shared OAuth profile unchanged.

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

The Windows installer runs the controller's structure self-test before replacing
an existing installation. On a Windows development system with `samsungtvws`
installed, it can also be invoked directly:

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
