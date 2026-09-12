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
- Optional keyboard volume integration. Windows can extend its system-volume range
  with TV volume. On macOS, TV volume routing is bound during installation to one
  fixed-volume physical Core Audio device. The controller normalizes the endpoint
  suffix that macOS may add or remove during an OS update; different physical HDMI
  devices, Mac speakers, headphones, and adjustable outputs remain under macOS control.
- A macOS menu-bar item and Windows notification-area icon with live status and
  platform-appropriate repair, reauthorization, restart, and log actions.
- A TV-volume slider in both status menus. In SmartThings mode, choose an exact
  value from 0 to 100 and release the slider to apply it. On Windows, left-click
  the tray icon for a volume-only flyout; right-click for the full control panel.

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
| Status UI | Menu-bar item (`TV`, `TV!`, `TV×`) | TV icon with `!` attention / `×` stopped badges |
| Exact TV-volume slider | SmartThings | SmartThings |
| Elevated privileges | Not required | Not required for the controller; the installer may use `winget` to install a missing user-scoped Python runtime |

The macOS controller uses IOHID matching only for the standard scroll-wheel,
Volume Up, and Volume Down usages; macOS may require Input Monitoring
permission for wheel wake and integrated volume routing. Keyboard and mouse-
button wake continue to use anonymous system event counters, and the controller
does not read typed text.
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
receive the pinned official npm CLI and a private signed Node.js runtime
automatically. The Windows installer verifies the Node.js archive checksum and
OpenJS Foundation signature; no separate Windows MSI or system Node.js is
required.

Requirements:

- The TV appears in the SmartThings mobile app under the same Samsung account.
- The TV and computer both have internet access.
- The network permits access to Samsung's sign-in and SmartThings services.
- The discovered TV exposes the required OCF remote-control capabilities.

Cloud mode does not silently fall back to LAN mode. It depends on an internet
service, is usually slower than local control, and may be affected by API or
firmware changes. The controller makes a read-only SmartThings device request
at startup and every 30 minutes. This gives the controller an opportunity to
refresh credentials before a hotkey is needed and detects failed refreshes
early.

SmartThings sets the access-token lifetime (normally about 24 hours); this
project cannot extend it to a week or month. On both platforms the controller
rotates the access token and single-use refresh token when six hours remain. If
the API rejects an access token before its saved expiry time, the controller
forces one refresh under the same lock and retries the failed request once.
Token writes are atomic and every CLI operation is serialized across processes
so that two refreshes cannot consume the same refresh token or overwrite the
newly rotated token. In normal operation, one browser authorization is
therefore sufficient. Another authorization is needed only if access is
revoked, the saved CLI credentials are removed or damaged, or SmartThings
rejects the current refresh token. See SmartThings'
[token-management documentation](https://developer.smartthings.com/docs/service-integrations/token-management#token-expiry).

When cloud mode runs on more than one computer, authorize each computer with a
different Samsung account and share the same SmartThings Location with those
accounts. In testing, token rotation under the same Samsung account invalidated
the other computer's otherwise separate CLI authorization.

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
and `sudo` are not required. Add the installed controller Python runtime to
**System Settings → Privacy & Security → Input Monitoring** if macOS denies
direct HID access. Without that access, Picture Off and keyboard/button wake
continue, but wheel wake and volume-key routing are disabled for that run.
The menu-bar item shows `TV!` and offers **Repair Input Monitoring** when this
happens. The installation is under the current user's
`~/Library/Application Support`, not the system-wide `/Library/Application Support`.

See the [macOS guide](QN990F_macOS_Controller/README.txt) for detailed setup,
configuration, file locations, and manual commands.

### Windows

1. Open [`QN990F_Windows_Controller`](QN990F_Windows_Controller/).
2. Double-click [`INSTALL-ME.cmd`](QN990F_Windows_Controller/INSTALL-ME.cmd).
3. Choose Direct LAN or SmartThings cloud. In cloud mode, the installer
   installs the pinned official npm CLI with a verified signed Node.js runtime.
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

Integrated volume control is optional during installation. Rerun the installer
to enable or disable it; an upgrade keeps the current choice when the prompt is
left blank.

- On Windows, `Volume Up` changes system volume until it is full, then sends TV
  volume-up. In SmartThings mode, `Volume Down` lowers TV volume to the configured
  floor before resuming normal system-volume reduction.
- On macOS, the TV receives volume keys only when the current output has no
  adjustable system volume and its normalized physical Core Audio identity matches
  the TV output confirmed during installation. Known per-endpoint UID suffix changes
  are tolerated across macOS updates, while another physical HDMI sink is rejected.
  Volume keys for other HDMI devices, Mac speakers, and headphones never alter TV volume. The bound
  TV output uses its full range from 0 to 100.
- SmartThings TV-volume steps received within 200 ms are combined locally. Up
  and down steps cancel each other, and one final target volume is sent.
- Cloud volume state is refreshed no more than once every 30 seconds.
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

On macOS, use the `TV` menu-bar item for current controller/volume status and
the common recovery actions. `TV!` means volume integration needs attention;
`TV×` means the controller is stopped, in error, or requires SmartThings sign-in.
On Windows, left-click the notification-area icon for a compact volume-only
flyout. Right-click for the full panel with controller and keyboard-volume status,
configuration, SmartThings reauthorization, restart, and log actions. Both views
use a Windows 11-inspired design with light/dark and high-contrast themes, a thin
volume track, and rounded corners on Windows 11. They use Windows' built-in WPF
libraries, with no extra UI runtime to install. This controls the TV directly;
it is not the Windows system-volume mixer. Press Esc or click outside to dismiss.
The flyout has a short fade-in/out, respecting Windows' client-area animation and
high-contrast settings. Reopening cancels an in-progress close; repair and log
actions close immediately. On small or highly scaled displays, the panel fits
the monitor work area and scrolls to keep its actions reachable. Volume track
drags retain mouse capture so releasing outside still applies the final target.
The status process is separate, so it remains available when the controller stops.
On either platform, **Quit Controller** stops both the background controller and
its status UI while leaving login startup installed for the next sign-in.

The **TV volume** slider controls the configured TV directly, including values
below the keyboard-volume floor. It works even when keyboard volume integration
is disabled or the computer is using another audio output; on macOS it does not
need Input Monitoring. Drag to choose a value, then release to apply it. The menu
shows the pending change or an error, and periodically updates the displayed TV
volume. Exact values require SmartThings and the TV's `audioVolume` capability;
LAN mode displays an explanation in place of an active slider.

## Security and privacy

- Installation and background startup are per-user; the controller does not
  require root or administrator access.
- Direct LAN mode stores the Samsung pairing token locally in the controller's
  application-data directory. Treat this file as a credential.
- In cloud mode, the official SmartThings CLI handles interactive OAuth sign-in
  and stores its tokens in its own user-level data directory with restrictive
  permissions. The controller's `config.json` does not contain a SmartThings
  access token.
- Background cloud commands are non-interactive and cannot create browser child
  processes. All CLI calls from the controller, installer, and reauthorization
  helper use the same cross-process lock. A network timeout is treated as
  transient and retried later; only an actual OAuth/refresh failure stops cloud
  activity and shows one authorization alert. Choosing **Reauthorize** opens a dedicated helper
  that stops the controller, completes browser sign-in, and restarts it without
  sending a TV command.
- macOS writes a small local `health.json` file for the menu-bar status item. It
  contains state labels and the current audio-output name/normalized identity when
  rebinding is required; it contains no typed keys or SmartThings token.
- The status interfaces read local state and submit slider targets through
  `volume-request.json`. The controller processes them using its existing
  serialized SmartThings client; the interfaces do not handle OAuth credentials.
  `volume-status.json` reports the value and result. These two small files are
  overwritten in place, and requests from a previous controller session are ignored.
- The project adds no application telemetry. It writes local status and
  diagnostic files only. Windows logs may include Raw Input device paths; macOS
  logs input categories but not typed text. Windows wake records may also
  include the single virtual-key code that caused a wake.
- The active log and three rotating backups retain at most approximately 4 MB
  per installation.

Installers download runtime components and dependencies from their documented
upstream sources. macOS verifies the pinned SmartThings CLI archive. Windows
verifies the pinned Node.js archive and Authenticode signature, then installs
the pinned official SmartThings CLI npm package with lifecycle scripts disabled.

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
  and the official CLI. The periodic check keeps the CLI's automatic token
  rotation active and detects failures earlier, but it cannot override
  server-side revocation.
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

### Windows Application Control blocks SmartThings

- Rerun `INSTALL-ME.cmd`. Current Windows installations use the official npm
  CLI through a signed Node.js runtime instead of the unsigned standalone
  `smartthings.exe` found in the SmartThings release ZIP.
- Do not disable Windows Defender, Smart App Control, or enterprise Application
  Control policy. The installer removes the obsolete standalone executable only
  after the replacement runtime has passed validation and the controller starts.

### The hotkey does nothing

- Make sure another application has not registered the same global shortcut.
- Confirm the background controller is running.
- Review `controller.log` and `status.json` in the platform application-data
  directory shown in its guide.
- Run the configuration tool to validate a replacement hotkey.

### Picture Off works but macOS volume keys do not

- Open the `TV` menu-bar item and read the **Volume** status.
- If it shows `TV!`, choose **Repair Input Monitoring** or
  **Bind Current TV Audio Output** as directed.
- Input Monitoring must contain the exact installed runtime at
  `~/Library/Application Support/QN990FController/venv/bin/python`.
- Privacy authorization can be removed during a macOS update. The controller
  reports this explicitly and continues Picture Off without volume interception.
- A changed endpoint suffix alone does not require rebinding. Select and confirm
  the output again only when macOS reports a different physical device identity.

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

Test the Windows flyout's XAML, slider state, request protocol, and monitor-bound
placement without contacting a TV or modifying the installed controller:

```powershell
powershell.exe -NoProfile -Sta -ExecutionPolicy Bypass -File .\QN990F_Windows_Controller\tests\Test-StatusTray.ps1 -RequireWpf
```

PowerShell 7 on other platforms can run the same script without `-RequireWpf`
for protocol and placement checks; WPF checks are explicitly skipped. On Windows,
also check left/right tray clicks, drag-release and keyboard adjustment, outside
click/Esc dismissal, closing/reopening during a fade, and light/dark/high-contrast
appearance at the display scales you use. These interactive checks require a
Windows desktop.

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
