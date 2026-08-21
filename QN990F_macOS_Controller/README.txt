Samsung TV Picture Controller for macOS
=======================================

Features
--------
Makes a compatible Samsung TV behave more like a conventional computer display on macOS:

1. Default global hotkey:
       Control + Command + P
   -> Turns off the TV picture while preserving audio and the logical HDMI connection.

2. If this controller turned off the picture:
   -> The next keyboard, mouse, or trackpad input that meets the wake rules automatically
      restores the picture.

3. Optional:
   -> Automatically send Picture Off after N minutes of keyboard and mouse inactivity on macOS.

4. Respects macOS display-wake power assertions by default.
   The picture will not be turned off automatically while a video or presentation app
   explicitly requests that the display remain on.


Compatibility
-------------
This controller was developed and validated with a Samsung QN990F. It is not
restricted to that model name.

Direct LAN mode requires the encrypted Samsung Tizen WebSocket remote on TCP
port 8002, firmware support for KEY_PICTURE_OFF, and support for the configured
wake key (KEY_RETURN by default).

SmartThings cloud mode lists Samsung OCF televisions that expose both the
execute and samsungvd.remoteControl capabilities. Those capabilities indicate
the required API shape, but they do not guarantee that a particular firmware
will execute Picture Off. The installer therefore requires a visual Picture
Off / restore test before it enables background startup.


Connection Modes
----------------
The installer asks you to choose one of these modes:

1. Direct LAN WebSocket

   The original mode. The Mac connects directly to TCP port 8002 on the TV and sends
   KEY_PICTURE_OFF / KEY_RETURN. This mode is faster and sends commands more directly
   when the Mac has direct access to the TV's local network. It remains the installer's
   default.

2. SmartThings cloud

   Intended for networks where a VPN, firewall, or managed policy blocks direct access
   to the TV's private LAN address. The Mac accesses the public SmartThings HTTPS service
   instead of connecting directly to the TV. This project does not modify routes or
   bypass network security policy.

   Requirements:
   - macOS 13.5 or later
   - The Samsung TV has been added to your SmartThings mobile app
   - Both the TV and the Mac can access the internet
   - The current network or VPN permits Samsung sign-in and SmartThings API access

   Cloud mode attempts the same KEY_PICTURE_OFF / pressAndRelease operation used by the
   Picture Off button in Accessibility Mode of the SmartThings mobile remote. It writes
   the TV's OCF remote-control resource through the exposed execute capability; it does
   not open the Accessibility menu or depend on menu order. The next Mac input that
   meets the wake rules sends KEY_RETURN through the same path.

The two modes never fall back to each other silently. Rerun INSTALL.command to switch modes.


Installation
------------
1. Extract the ZIP archive.
2. Double-click INSTALL.command.

If macOS blocks the file because it came from the internet:
- Control-click INSTALL.command
- Choose Open
- Choose Open again

SmartThings cloud installation flow:
- Explicitly choose option 2 (SmartThings cloud); option 1 (Direct LAN) remains the default
- The installer downloads and verifies a pinned version of the official SmartThings CLI
- A browser opens for Samsung/SmartThings OAuth sign-in
- Select the compatible Samsung TV to control
- A test command is sent only after you explicitly confirm in Terminal
- Visually verify the Picture Off -> restore after approximately 2 seconds test

Direct LAN installation flow:
- Choose Direct LAN WebSocket
- Enter the Samsung TV's LAN IP address
- Choose Allow when the TV displays the pairing prompt
- A test command is sent only after you explicitly confirm in Terminal
- Visually verify the KEY_PICTURE_OFF -> KEY_RETURN test

Both modes ask for the automatic Picture Off idle interval. Enter 0 to disable it.
The installer enables automatic startup at login only after you confirm that the visual
test actually succeeded.


What the Installer Does Automatically
-------------------------------------
- Installs an isolated runtime in ~/Library/Application Support/QN990FController
- Installs a private Python 3.12 runtime and samsungtvws 3.0.5
- Installs SmartThings CLI 2.1.2 in cloud mode
- Checks the global hotkey and the macOS idle-input API
- Verifies the selected TV connection
- Installs a user-level LaunchAgent


Not Required
------------
- A Raspberry Pi or any other additional hardware
- Homebrew
- System pip
- Xcode / Command Line Tools
- sudo / root
- Accessibility permission
- Input Monitoring permission


Why Accessibility and Input Monitoring Are Not Required
--------------------------------------------------------
The global hotkey uses macOS Carbon RegisterEventHotKey.

Wake detection after Picture Off uses:
    CGEventSourceCounterForEventType(kCGEventSourceStateHIDSystemState, ...)

The controller compares cumulative macOS event counters for keyboard key-down,
mouse-button down, scroll wheel, and mouse movement. It does not install an event tap or
read specific keys, typed text, or event contents. Because it observes only the key-down
counter, pressing Control, Command, Option, or Shift alone does not wake the picture.

When the mouse-movement counter changes, the controller uses CGEventGetLocation to read
the current pointer position. It accumulates movement in screen-coordinate points and
filters small jitter. It cannot read per-device raw dx/dy values or device paths, so the
macOS version cannot log or ignore a particular keyboard or mouse as Windows Raw Input can.

Automatic idle Picture Off uses this API separately:
    CGEventSourceSecondsSinceLastEventType(..., kCGAnyInputEventType)

These system-level counters and the current pointer position do not require Accessibility
or Input Monitoring permission.


Normal Use
----------
Control + Command + P:
    Sends Picture Off immediately. The hotkey is a one-way command: pressing it again
    sends Picture Off again. It does not restore the picture and is not a toggle.

After Picture Off:
    Press a non-modifier key, click a mouse button, use the scroll wheel, or deliberately
    move the mouse or trackpad
    -> The picture is restored automatically.

Wake rules:
- Pressing modifier keys such as Control, Command, Option, or Shift alone does not wake.
- Mouse-button and scroll-wheel input wakes the picture.
- Mouse or trackpad movement accumulates abs(dx) + abs(dy) while successive samples are
  no more than 500 ms apart. The total must reach 24 screen-coordinate points before the
  picture wakes, so small movement is ignored. Before waking, the controller uses pmset
  to verify that
  WindowServer attributes the latest activity to a device rather than a software process;
  generated cursor movement from keep-awake utilities is ignored.
- A short arbitration and guard interval follows hotkey activation, so the hotkey's own
  P key-down does not immediately wake the picture that was just turned off.

If automatic idle Picture Off is configured:
    N minutes without input -> Picture Off
    The next input that meets the rules above -> Restore the picture

Each Picture Off or restore action in cloud mode requires one HTTPS command, so it is
usually slower than LAN mode. Avoid triggering it repeatedly in quick succession during
normal use.
Background control never opens a sign-in page by itself. If OAuth refresh fails, commands
stop. Run Configure.command, sign in again during its interactive verification step, and
then resume using the hotkey.


Video and Presentation Protection
---------------------------------
When the idle interval is reached, the controller checks these macOS display power assertions:
- PreventUserIdleDisplaySleep
- NoDisplaySleepAssertion
- InternalPreventDisplaySleep

Automatic idle Picture Off is deferred when an application correctly creates one of these
assertions. The manual hotkey remains available.


Reconfiguration
---------------
After installation, double-click:

~/Library/Application Support/QN990FController/Configure.command

You can change:
- The automatic idle Picture Off interval
- The global hotkey
- Whether display power assertions are respected
- The TV IP address in LAN mode

Rerun the original INSTALL.command to select another SmartThings TV or switch connection modes.

Hotkey examples:
    Ctrl+Cmd+P
    Ctrl+Cmd+O
    Cmd+Shift+9
    Ctrl+Option+B

The final key supports A-Z / 0-9. Supported modifier names are Ctrl/Control, Cmd/Command,
Option/Opt/Alt, and Shift.


Files and Credentials
---------------------
The QN990FController application-data directory, LaunchAgent label, and
SmartThings profile are legacy internal identifiers retained to avoid breaking
existing installations. They do not restrict TV model compatibility.

~/Library/Application Support/QN990FController/config.json
    Controller configuration; does not contain a SmartThings access token.

~/Library/Application Support/QN990FController/samsung-token.txt
    TV pairing token used only in LAN mode.

~/Library/Application Support/QN990FController/smartthings
    Pinned version of the official SmartThings CLI used in cloud mode.

The official CLI stores the SmartThings OAuth access and refresh tokens in its user-level
data directory with 0600 file permissions. The controller uses a separate profile:
local.qn990f.picture-controller. Because the directory is shared SmartThings CLI data, the
uninstaller does not modify or sign out any profile.

~/Library/Application Support/QN990FController/controller.log
    Rotating controller log. It is limited to controller.log plus three backups of at
    most 1 MB each (about 4 MB total). Oldest entries are discarded automatically.

~/Library/Application Support/QN990FController/status.json
    Current or most recent runtime status.

~/Library/LaunchAgents/local.qn990f.picture-controller.plist
    Login startup item.


Important Limitations
---------------------
1. Picture Off is not macOS display sleep. Its purpose is to preserve the logical HDMI
   connection and prevent the HDMI re-enumeration and window rearrangement caused by
   turning a conventional TV off and on.

2. SmartThings cloud mode depends on a public internet service. A successful API response
   indicates only that the command was accepted, not that the TV executed it. Installation
   and reconfiguration therefore require your visual confirmation.

3. The SmartThings mobile plugin uses the Samsung TV's private OCF remote-control resource.
   The script reproduces the same request through the deprecated execute capability exposed
   by the TV. Cloud mode may require an update if Samsung removes this compatibility entry
   point in the future.

4. SmartThings rate-limits each device; avoid switching the picture repeatedly in quick
   succession during normal use. The controller does not retry an automatic idle Picture
   Off command in a loop after it fails. It is re-enabled only after detecting new keyboard
   or mouse input that meets the wake rules. Small movement below the threshold does not
   re-enable it.

5. When SmartThings credentials expire, background commands fail within a bounded period
   instead of opening a browser. Run Configure.command to sign in interactively again and
   reverify the TV.

6. Samsung firmware may silently ignore some KEY_* commands. If KEY_RETURN cannot restore
   the picture, change wake_key in config.json to KEY_UP, then run Configure.command.

7. Automatic keyboard and mouse wake applies only when this controller believes that it
   turned off the picture. The controller may not know the current state when the picture
   is turned off through the TV menu, Bixby, or another device.

8. The zero-permission movement threshold on macOS is measured in screen-coordinate points,
   not Windows per-device raw motion counts. Movement farther toward a display edge may not
   count while the pointer remains pinned to the edge. The macOS version also cannot filter
   input by device.


Manual Tests
------------
Picture Off, then restore after approximately 2 seconds:

"$HOME/Library/Application Support/QN990FController/venv/bin/python" \
"$HOME/Library/Application Support/QN990FController/QN990FController.py" --test

Picture Off only:

"$HOME/Library/Application Support/QN990FController/venv/bin/python" \
"$HOME/Library/Application Support/QN990FController/QN990FController.py" --off

Send restore only:

"$HOME/Library/Application Support/QN990FController/venv/bin/python" \
"$HOME/Library/Application Support/QN990FController/QN990FController.py" --wake

Show the current keyboard and mouse idle time in seconds:

"$HOME/Library/Application Support/QN990FController/venv/bin/python" \
"$HOME/Library/Application Support/QN990FController/QN990FController.py" --idle


Uninstallation
--------------
Double-click:

~/Library/Application Support/QN990FController/Uninstall.command

This stops the LaunchAgent and deletes this controller's installation directory, including
config.json, the LAN pairing token, private runtime, status, and logs. It does not modify
the TV or alter the SmartThings CLI's shared OAuth profile.
