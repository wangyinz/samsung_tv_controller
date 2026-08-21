QN990F Windows Picture Controller
=================================

Purpose
-------
Makes a Samsung QN990F behave more like a PC monitor on Windows:

1) Global hotkey (default Ctrl+Alt+P)
   -> sends Samsung Tizen KEY_PICTURE_OFF.

2) If this controller blanked the TV, the next Windows keyboard/mouse input
   -> sends KEY_RETURN to wake the picture.

3) Optional automatic blanking after N minutes of keyboard/mouse inactivity.

4) By default, automatic blanking respects Windows ES_DISPLAY_REQUIRED.
   Media/presentation apps that explicitly tell Windows "keep the display on"
   should therefore suppress automatic blanking.


INSTALL
-------
1. Keep all files from this ZIP together.
2. Easiest method: double-click INSTALL-ME.cmd.

   Or open PowerShell in the extracted folder and run:

   powershell -NoProfile -ExecutionPolicy Bypass -File .\Install-QN990FController.ps1

The installer will:
- Ask for the QN990F's LAN IP.
- Ask for the idle timeout (default 10 minutes; 0 disables it).
- Use an existing Python 3.9+ if available.
- If Python is absent, install Python 3.12 with winget.
- Create an isolated venv under:
    %LOCALAPPDATA%\QN990FController
- Install samsungtvws 3.0.5 there. You do NOT need a system "pip" command.
- Pair with the TV over Tizen WebSocket TLS port 8002.
- Run a two-second Picture Off / wake test.
- Start the controller invisibly with pythonw.exe.
- Add it to the current user's Startup folder.


FIRST PAIRING
-------------
Keep the TV on and on the same LAN/subnet as the PC.

When the Samsung TV asks whether to allow "QN990F-PC-Controller",
choose Allow.

If pairing was previously denied, open the TV's Device Connection Manager,
remove/clear the denied device if necessary, and pair again.


NORMAL USE
----------
Ctrl+Alt+P
    Picture Off immediately.

After Picture Off:
    Move the mouse or press a key -> wake picture.

Automatic idle behavior:
    After the configured idle time, KEY_PICTURE_OFF is sent.
    The next real keyboard/mouse input wakes it.

The hotkey is a toggle when the controller still considers the picture off,
but normal mouse/keyboard activity already handles wake automatically.


CONFIGURATION
-------------
Use Start menu:
    QN990F Controller -> Configure QN990F Controller

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


FILES
-----
%LOCALAPPDATA%\QN990FController\config.json
    Main settings.

%LOCALAPPDATA%\QN990FController\samsung-token.txt
    Samsung pairing token. Treat it as a local credential.

%LOCALAPPDATA%\QN990FController\controller.log
    Rotating diagnostic log.

%LOCALAPPDATA%\QN990FController\status.json
    Current/last daemon status.


IMPORTANT BEHAVIOR / LIMITATIONS
--------------------------------
- KEY_PICTURE_OFF is a Samsung remote key, not Windows display sleep.
  The HDMI connection remains logically active; this is why it is useful here.

- Samsung firmware can silently ignore unsupported KEY_* values. The installer
  therefore asks you to visually confirm the Picture Off test.

- Wake uses KEY_RETURN because Samsung documents that a non-Power/non-Volume
  remote key wakes Picture Off. If your firmware does not wake with KEY_RETURN,
  edit config.json and try "KEY_UP", then restart/configure the controller.

- The controller wakes only after Picture Off actions that IT initiated.
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
Open PowerShell and run:

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
Start menu:
    QN990F Controller -> Uninstall QN990F Controller
