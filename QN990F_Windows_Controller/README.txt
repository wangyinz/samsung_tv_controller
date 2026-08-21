Samsung TV Picture Controller for Windows
=========================================

PURPOSE
-------
Makes a compatible Samsung TV behave more like a PC monitor on Windows:

1) Global hotkey (default Ctrl+Alt+P)
   -> always sends Samsung Tizen KEY_PICTURE_OFF.

2) If this controller blanked the TV, the next qualifying keyboard or mouse
   input sends KEY_RETURN to wake the picture.

3) Optional automatic blanking after N minutes of keyboard/mouse inactivity.

4) By default, automatic blanking respects Windows ES_DISPLAY_REQUIRED.
   Media/presentation apps that explicitly tell Windows "keep the display on"
   should therefore suppress automatic blanking.

This controller was developed and validated with a Samsung QN990F. Other
Samsung TVs may work when they expose the encrypted Tizen WebSocket remote on
TCP port 8002 and their firmware accepts KEY_PICTURE_OFF plus the configured
wake key. The installer requires a visual Picture Off / wake test before it
enables background startup; that test is the compatibility check for your TV.


INPUT AND WAKE RULES
--------------------
Wake detection uses Windows Raw Input (WM_INPUT):

- Keyboard: a non-modifier key-down qualifies. Pressing only Ctrl, Alt, Shift,
  Win, or a lock key does not wake the TV.
- Mouse: a button press or vertical/horizontal wheel input qualifies.
- Mouse motion: abs(dx) + abs(dy) is accumulated separately for each input
  device while successive events stay no more than 500 ms apart. The total must
  reach 24 raw motion counts. Tiny sensor jitter below that threshold does not
  wake the TV.
- The configured hotkey is arbitrated so its final key-down cannot immediately
  wake the picture it just turned off.

Keyboard/mouse wake is briefly debounced, and begins only after the post-blank
wake guard. GetLastInputInfo is used only for optional idle-auto-OFF timing; it
is not used to decide whether an input should wake the picture.

Every qualifying wake is logged with its Raw Input device path, for example:

    Picture wake (raw_input) source=mouse_move:... device=\\?\HID#VID_...

If the log identifies a noisy or virtual device, config.json supports a
case-insensitive device-path substring filter:

    "ignored_input_device_substrings": ["vid_1234&pid_abcd"]

Use a unique substring copied from the log. Input from matching devices is
ignored. This is a Windows-only, per-device Raw Input feature.


INSTALL
-------
1. Keep all files from this ZIP together.
2. Double-click INSTALL-ME.cmd.

   Or open PowerShell in the extracted folder and run:

   powershell -NoProfile -ExecutionPolicy Bypass -File .\Install-QN990FController.ps1

For a new installation, the installer will:
- Ask for the Samsung TV's LAN IP.
- Ask for the idle timeout (default 10 minutes; 0 disables it).
- Use an existing Python 3.9+ if available.
- If Python is absent, install Python 3.12 with winget.
- Create an isolated venv under:
    %LOCALAPPDATA%\QN990FController
- Install samsungtvws 3.0.5 there. You do NOT need a system "pip" command.
- Pair with the TV over Tizen WebSocket TLS port 8002.
- Ask permission, run a two-second Picture Off / wake visual test, and ask you
  to confirm what you observed.
- Start the controller invisibly with pythonw.exe.
- Add it to the current user's Startup folder.


UPGRADE OR REPAIR
-----------------
Download and extract the latest complete release, then run INSTALL-ME.cmd
again. The same installer handles new installs, upgrades, and repairs.

When an existing installation is detected, the installer preserves config.json
and samsung-token.txt, including the TV IP, hotkey, idle timeout, pairing token,
and advanced input settings. It updates the program/runtime and restarts the
controller without sending the Picture Off / wake visual test. Ordinary upgrades
also skip pairing; repair mode pairs only when the token is missing or you
explicitly supply a different TV IP.


FIRST PAIRING
-------------
Keep the TV on and on the same LAN/subnet as the PC.

On a new installation, when the Samsung TV asks whether to allow
"Samsung-TV-Picture-Controller", choose Allow. An installation upgraded from
an older release may retain the previous remote name so its pairing token stays
valid; allow the displayed request if the installer asks you to pair again.

If pairing was previously denied, open the TV's Device Connection Manager,
remove/clear the denied device if necessary, and pair again.


NORMAL USE
----------
Ctrl+Alt+P
    Picture Off immediately. The hotkey is one-way: pressing it again sends
    Picture Off again; it never acts as a wake/toggle command.

After Picture Off:
    Press a non-modifier key, press a mouse button, use the wheel, or move the
    mouse far enough to cross the anti-jitter threshold -> wake picture.

Automatic idle behavior:
    After the configured idle time, KEY_PICTURE_OFF is sent.
    The next qualifying keyboard/mouse input wakes it.


CONFIGURATION
-------------
Use Start menu:
    Samsung TV Picture Controller -> Configure Samsung TV Picture Controller

You can change:
- TV IP
- Idle timeout (0 disables automatic blanking)
- Global hotkey
- Whether to respect Windows display-required/media power requests

Supported hotkey syntax uses Ctrl/Alt/Shift/Win plus one A-Z or 0-9 key.
Examples:
    Ctrl+Alt+P
    Ctrl+Alt+O
    Ctrl+Shift+9

Advanced input thresholds and ignored device substrings can be edited directly
in config.json. Restart the controller after a manual edit.


FILES
-----
The QN990FController application-data directory is a legacy internal path kept
for in-place upgrades. It does not restrict which compatible TV can be used.

%LOCALAPPDATA%\QN990FController\config.json
    Main settings.

%LOCALAPPDATA%\QN990FController\samsung-token.txt
    Samsung pairing token. Treat it as a local credential.

%LOCALAPPDATA%\QN990FController\controller.log
    Rotating diagnostic log, including qualifying input source/device details.
    The active log and three backups are each limited to 1 MB, so retained logs
    use at most approximately 4 MB in total.

%LOCALAPPDATA%\QN990FController\status.json
    Current/last daemon status.


IMPORTANT BEHAVIOR / LIMITATIONS
--------------------------------
- KEY_PICTURE_OFF is a Samsung remote key, not Windows display sleep.
  The HDMI connection remains logically active; this is why it is useful here.

- Samsung firmware can silently ignore unsupported KEY_* values. A new install
  therefore asks you to visually confirm the Picture Off test.

- Wake uses KEY_RETURN because Samsung documents that a non-Power/non-Volume
  remote key wakes Picture Off. If your firmware does not wake with KEY_RETURN,
  edit config.json and try "KEY_UP", then restart/configure the controller.

- The controller wakes only after Picture Off actions that the controller initiated.
  If you blank the picture by some other method, Windows input is not guaranteed
  to wake it through this controller.

- Reserve the TV's IP address in your router/DHCP settings. If the TV's IP
  changes, run Configure and update it.

- The PC and TV generally need to be on the same subnet for Samsung's local
  WebSocket remote protocol.

- Set Windows' own "turn off my screen" timeout LONGER than this controller's
  timeout, or set it to Never. Otherwise Windows may disable the HDMI/display
  path before the network Picture Off workflow runs.

- Automatic blanking is based on Windows keyboard/mouse inactivity. The
  "respect display-required" option reduces false blanking during media playback
  when the app correctly requests ES_DISPLAY_REQUIRED. An app that does not make
  such a Windows power request can still be blanked after the idle timeout.

- This does not power the TV into standby. It uses Picture Off, so wake is much
  faster and avoids HDMI re-enumeration.


MANUAL TEST COMMANDS
--------------------
These commands send real commands to the TV. Open PowerShell and run:

Picture Off, wait two seconds, then wake:

  & "$env:LOCALAPPDATA\QN990FController\venv\Scripts\python.exe" `
    "$env:LOCALAPPDATA\QN990FController\QN990FController.py" --test

Send Picture Off once:

  & "$env:LOCALAPPDATA\QN990FController\venv\Scripts\python.exe" `
    "$env:LOCALAPPDATA\QN990FController\QN990FController.py" --off

Send wake key once:

  & "$env:LOCALAPPDATA\QN990FController\venv\Scripts\python.exe" `
    "$env:LOCALAPPDATA\QN990FController\QN990FController.py" --wake


UNINSTALL
---------
Use Start menu:
    Samsung TV Picture Controller -> Uninstall Samsung TV Picture Controller

Uninstall stops the controller, removes its Startup and Start menu shortcuts,
and deletes %LOCALAPPDATA%\QN990FController. That deletion includes config.json,
samsung-token.txt, the private Python environment, status, and logs. It does not
change the TV itself or revoke entries in the TV's Device Connection Manager.
