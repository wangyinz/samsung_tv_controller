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

5. Optionally routes the keyboard volume buttons to TV volume while the current macOS
   output is the fixed-volume physical Core Audio device bound during installation.
   A variable endpoint suffix is normalized so an OS update does not break an
   otherwise unchanged physical-device binding. Other HDMI devices, Mac speakers,
   and headphones remain entirely under macOS control.

6. Provides a TV menu-bar status item with repair, rebinding, reauthorization,
   restart, and log actions.


Compatibility
-------------
This controller was developed and validated with a Samsung QN990F. It is not
restricted to that model name.

Direct LAN mode requires the encrypted Samsung Tizen WebSocket remote on TCP
port 8002, firmware support for KEY_PICTURE_OFF, and support for the configured
wake key (KEY_RETURN by default).

SmartThings cloud mode lists Samsung OCF televisions that expose the execute and
samsungvd.remoteControl capabilities. Optional volume control also requires
audioVolume. Those capabilities indicate
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

Both modes ask whether to enable integrated volume control and ask for the
automatic Picture Off idle interval. Enter 0 to disable automatic blanking.
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
- Installs a user-level menu-bar status app and LaunchAgent


Not Required
------------
- A Raspberry Pi or any other additional hardware
- Homebrew
- System pip
- Xcode / Command Line Tools
- sudo / root

macOS may require Input Monitoring permission for scroll-wheel wake and
optional integrated volume control.


macOS Privacy Permission
------------------------
The controller uses IOHIDManager matching limited to the standard scroll-wheel,
Volume Up, and Volume Down usages. If macOS denies access, add the installed
venv Python runtime to System Settings -> Privacy & Security -> Input Monitoring
when macOS prompts. The controller does not subscribe to ordinary typed-key
usages. If registration is unavailable while login is still starting, the controller
retries three times without delaying the global hotkey. If all attempts fail, Picture Off
and keyboard/button wake continue, but wheel wake and volume routing are disabled for
that run. A definite permission denial shows a recovery alert immediately and changes
the menu-bar title to TV!.

The required runtime is installed under the current user's Library:
    ~/Library/Application Support/QN990FController/venv/bin/python
It is not installed under /Library/Application Support. A macOS update may remove a
previous Input Monitoring entry. Use Repair Input Monitoring from the TV menu-bar item;
the helper opens the correct settings pane, displays the exact runtime path, and restarts
the controller after you confirm the change.

Wake Input and Privacy
----------------------
The global hotkey uses macOS Carbon RegisterEventHotKey.

Wake detection after Picture Off uses:
    CGEventSourceCounterForEventType(kCGEventSourceStateHIDSystemState, ...)

The controller compares cumulative macOS event counters for keyboard key-down
and mouse-button down. Scroll-wheel wake uses the limited IOHID matching above
because some pointing devices do not update the Quartz scroll counter. Mouse
movement is observed only when the advanced enable_mouse_move_wake setting is
explicitly enabled. The controller does not read specific keys or typed text.
Because it observes only the key-down counter, pressing Control, Command,
Option, or Shift alone does not wake the picture.

Pointer movement alone is disabled as a wake source by default. macOS includes
software-posted cursor movement in the zero-permission counters, but does not provide
reliable source attribution without Input Monitoring access. If movement wake is manually
enabled, the controller uses CGEventGetLocation to accumulate movement in
screen-coordinate points and applies a best-effort source check. It cannot read per-device
raw dx/dy values or device paths, so the macOS version cannot reliably distinguish every
synthetic movement or ignore a particular device as Windows Raw Input can.

Automatic idle Picture Off uses this API separately:
    CGEventSourceSecondsSinceLastEventType(..., kCGAnyInputEventType)

These system-level counters and the current pointer position do not require
Accessibility or Input Monitoring permission. The limited scroll-wheel HID
listener may require Input Monitoring permission.


Normal Use
----------
Control + Command + P:
    Sends Picture Off immediately. The hotkey is a one-way command: pressing it again
    sends Picture Off again. It does not restore the picture and is not a toggle.

After Picture Off:
    Press a non-modifier key, click a mouse button, or use the scroll wheel
    -> The picture is restored automatically.

Volume keys:
    Integrated volume control is optional during installation. Rerun
    INSTALL.command to enable, disable, or bind it to a different TV output. The
    installer requires the configured TV to be selected as the current macOS sound output
    and stores a normalized physical identity derived from its Core Audio UID. macOS has
    been observed adding or removing an eight-hex-digit endpoint suffix during an OS
    update; that suffix is intentionally ignored. A different physical UID remains a
    mismatch. Only the confirmed fixed-volume output routes Volume Up and Volume Down to
    the TV across its full 0-100 range. Every other output remains under macOS control.
    SmartThings TV-volume steps within 200 ms are combined into one target;
    volume-up and volume-down steps cancel each other within that buffer.
    Cloud volume state is refreshed no more than once every 30 seconds.

    Native audio keys can have only one exclusive owner. If BetterDisplay is
    installed, open BetterDisplay -> Settings -> Keyboard -> Native (Apple)
    Keyboard Control and turn off "Listen to native audio keys". BetterDisplay
    may remain running for brightness and its other display features.

Direct LAN can send TV volume keys but cannot read current TV volume. SmartThings mode
can read and combine the target TV volume before sending an update.

Wake rules:
- Pressing modifier keys such as Control, Command, Option, or Shift alone does not wake.
- Mouse-button and scroll-wheel input wakes the picture.
- Mouse or trackpad movement alone does not wake by default. This prevents keep-awake
  utilities and other software-generated movement from waking the TV unattended.
- Advanced opt-in: set "enable_mouse_move_wake": true in config.json to restore the
  movement threshold, then run Configure.command to restart the controller. Movement then
  accumulates abs(dx) + abs(dy) while successive samples are no more than 500 ms apart, and
  must reach 24 screen-coordinate points. macOS source attribution is best-effort, so
  synthetic movement may still wake the TV in this mode.
- A short arbitration and guard interval follows hotkey activation, so the hotkey's own
  P key-down does not immediately wake the picture that was just turned off.

If automatic idle Picture Off is configured:
    N minutes without input -> Picture Off
    The next input that meets the rules above -> Restore the picture

Each Picture Off or restore action in cloud mode requires one HTTPS command, so it is
usually slower than LAN mode. Avoid triggering it repeatedly in quick succession during
normal use.
Background control never opens a sign-in page by itself. If OAuth refresh fails, commands
stop and one authorization alert is shown. The controller performs a read-only TV lookup
at startup and every 30 minutes so the controller can refresh credentials before a
hotkey is needed. SmartThings sets the access-token lifetime (normally about 24 hours).
The controller proactively replaces both the access token and the single-use refresh
token when six hours remain. If the API rejects an access token before that saved expiry,
the controller forces one refresh under the same process lock and retries the request
once. Controller, installer, and reauthorization operations share that lock so two
refreshes cannot corrupt the rotation. Network timeouts are retried later; only a
rejected refresh token or revoked authorization requires another sign-in. Choose
Reauthorize in the alert, or double-click Reauthorize.command;
the helper stops the controller, completes browser sign-in, and restarts it without
sending a TV command.

If cloud mode also runs on another computer, authorize that computer with a different
Samsung account and share the same SmartThings Location with both accounts. In testing,
token rotation under one Samsung account invalidated the other computer's otherwise
separate CLI authorization.


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
To renew only the SmartThings sign-in, double-click:

~/Library/Application Support/QN990FController/Reauthorize.command

The TV menu-bar item provides the common recovery actions:
- TV: controller is running and no volume repair is required
- TV!: Input Monitoring, output binding, or volume status needs attention
- TV×: controller is stopped, in error, or needs SmartThings authorization
- Quit Controller stops both the controller and menu for the current login session;
  login startup remains installed for the next sign-in

The TV volume slider sets the configured TV to an exact value from 0 to 100.
Drag to choose a value, then release to apply it. Pending changes and failures
are shown below the slider. SmartThings and the TV's audioVolume capability are
required; LAN mode shows the slider as unavailable.
This explicit TV control works even when keyboard volume integration is disabled,
Input Monitoring is unavailable, or the current audio output is not the bound TV.
The keyboard's volume-floor and audio-output binding rules do not limit the slider.

Use Repair Input Monitoring after privacy authorization is lost. Use Bind Current TV
Audio Output only after selecting and confirming the intended TV in macOS Sound settings;
this explicit confirmation is how the controller relates a Core Audio device to the TV.
It does not guess from a display name or a SmartThings label.

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
    Controller configuration, including the normalized physical Core Audio identity
    bound for optional TV-volume routing; does not contain a SmartThings access token.

~/Library/Application Support/QN990FController/samsung-token.txt
    TV pairing token used only in LAN mode.

~/Library/Application Support/QN990FController/smartthings
    Pinned version of the official SmartThings CLI used in cloud mode.

~/Library/Application Support/QN990FController/Reauthorize.command
    Stops the controller, renews SmartThings OAuth interactively, and restarts it.
    It validates access with a read-only request and sends no TV command.

The official CLI stores the SmartThings OAuth access and refresh tokens in its user-level
data directory with 0600 file permissions. The controller uses a separate profile:
local.qn990f.picture-controller. Because the directory is shared SmartThings CLI data, the
uninstaller does not modify or sign out any profile.

~/Library/Application Support/QN990FController/controller.log
    Rotating controller log. It is limited to controller.log plus three backups of at
    most 1 MB each (about 4 MB total). Oldest entries are discarded automatically.

~/Library/Application Support/QN990FController/status.json
    Current or most recent runtime status.

~/Library/Application Support/QN990FController/health.json
    Current optional-volume health shown by the menu-bar status item. It contains no
    SmartThings token or typed input.

~/Library/Application Support/QN990FController/Samsung TV Picture Controller Status.app
    Menu-bar status, recovery controls, and a TV-volume slider. Slider requests are
    processed by the background controller's existing SmartThings client.

~/Library/Application Support/QN990FController/volume-status.json
~/Library/Application Support/QN990FController/volume-request.json
    Current slider state and latest target. These small files are overwritten,
    contain no OAuth credentials, and cannot replay targets after a controller restart.

~/Library/LaunchAgents/local.qn990f.picture-controller.plist
    Login startup item.

~/Library/LaunchAgents/local.samsung-tv.picture-controller.menu.plist
    Login startup item for the menu-bar status app.


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
   or mouse input that meets the wake rules. Pointer movement does not re-enable it unless
   movement wake was explicitly enabled.

5. SmartThings credential checks are read-only, run at startup and every 30 minutes, and
   are serialized with cloud commands and interactive sign-in. When refresh fails, the
   controller shows one alert and stops cloud commands instead of opening a browser by
   itself. Transient network timeouts do not invalidate the saved authorization. The
   check detects failure earlier but cannot prevent server-side revocation.
   Run Reauthorize.command to sign in again without sending a TV command.

6. Samsung firmware may silently ignore some KEY_* commands. If KEY_RETURN cannot restore
   the picture, change wake_key in config.json to KEY_UP, then run Configure.command.

7. Automatic keyboard and mouse wake applies only when this controller believes that it
   turned off the picture. The controller may not know the current state when the picture
   is turned off through the TV menu, Bixby, or another device.

8. If movement wake is explicitly enabled, its zero-permission threshold is measured in
   screen-coordinate points, not Windows per-device raw motion counts. Movement farther
   toward a display edge may not count while the pointer remains pinned to the edge. The
   macOS version also cannot filter input by device or reliably attribute every synthetic
   cursor event.


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
