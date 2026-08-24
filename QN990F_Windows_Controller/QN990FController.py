#!/usr/bin/env python3
"""
Samsung TV Picture Controller for Windows

Key changes vs v1/v2:
- Ctrl+Alt+P is always Picture Off (never a state-dependent toggle).
- Picture wake is driven by Win32 Raw Input (WM_INPUT), not by changes in
  GetLastInputInfo(). This lets us identify the input device and avoids
  treating arbitrary timestamp changes / SendInput-style activity as a wake.
- Mouse movement alone is ignored by default; an advanced opt-in applies a
  configurable raw-motion threshold.
- Qualifying wake events are logged with the raw device path.
- GetLastInputInfo remains only for optional idle-auto-off timing.
- A Samsung WebSocket that has sat idle is proactively discarded before a key
  send so we do not first write to a connection the TV already closed.
"""

from __future__ import annotations

import argparse
import atexit
import ctypes
from ctypes import wintypes
import json
import logging
from logging.handlers import RotatingFileHandler
import os
from pathlib import Path
import queue
import re
import subprocess
import sys
import threading
import time

try:
    from samsungtvws import SamsungTVWS
except ImportError:
    print("samsungtvws is not installed. Run INSTALL-ME.cmd first.", file=sys.stderr)
    raise

if os.name != "nt":
    raise SystemExit("This controller is Windows-only.")

APP_DIR = Path(os.environ.get("LOCALAPPDATA", str(Path.home()))) / "QN990FController"
CONFIG_FILE = APP_DIR / "config.json"
TOKEN_FILE = APP_DIR / "samsung-token.txt"
LOG_FILE = APP_DIR / "controller.log"
PID_FILE = APP_DIR / "controller.pid"
STATUS_FILE = APP_DIR / "status.json"

APP_DIR.mkdir(parents=True, exist_ok=True)

DEFAULT_CONFIG = {
    "control_method": "lan",
    "tv_ip": "",
    "port": 8002,
    "idle_minutes": 10.0,
    "enable_idle_off": True,
    "respect_display_required": True,
    "hotkey": "Ctrl+Alt+P",
    "picture_off_key": "KEY_PICTURE_OFF",
    "wake_key": "KEY_RETURN",
    "wake_guard_ms": 800,
    "poll_interval_ms": 50,
    "socket_timeout_seconds": 5.0,
    "key_press_delay_seconds": 0.05,
    "remote_name": "Samsung-TV-Picture-Controller",
    "connection_refresh_seconds": 8.0,
    "input_wake_debounce_ms": 180,
    "enable_mouse_move_wake": False,
    "mouse_wake_threshold_counts": 24,
    "mouse_motion_window_ms": 500,
    "ignored_input_device_substrings": [],
    "smartthings_cli": str(APP_DIR / "smartthings.exe"),
    "smartthings_profile": "local.qn990f.picture-controller",
    "smartthings_device_id": "",
    "smartthings_command_timeout_seconds": 20.0,
    "smartthings_auth_check_interval_seconds": 1800.0,
    "enable_volume_control": True,
    "tv_volume_floor": 10,
    "tv_volume_refresh_seconds": 30.0,
}

logger = logging.getLogger("QN990FController")
logger.setLevel(logging.INFO)
if not logger.handlers:
    handler = RotatingFileHandler(LOG_FILE, maxBytes=1_000_000, backupCount=3, encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    logger.addHandler(handler)

# ---------------- Win32 constants ----------------

user32 = ctypes.WinDLL("user32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
powrprof = ctypes.WinDLL("powrprof", use_last_error=True)
ole32 = ctypes.WinDLL("ole32")

WM_INPUT = 0x00FF
WM_INPUT_DEVICE_CHANGE = 0x00FE
WM_HOTKEY = 0x0312
WM_DESTROY = 0x0002
WM_KEYDOWN = 0x0100
WM_KEYUP = 0x0101
WM_SYSKEYDOWN = 0x0104
WM_SYSKEYUP = 0x0105

MOD_ALT = 0x0001
MOD_CONTROL = 0x0002
MOD_SHIFT = 0x0004
MOD_WIN = 0x0008
MOD_NOREPEAT = 0x4000
HOTKEY_ID = 0x5199
WH_KEYBOARD_LL = 13
VK_VOLUME_DOWN = 0xAE
VK_VOLUME_UP = 0xAF
CLSCTX_ALL = 23
COINIT_APARTMENTTHREADED = 2
E_RENDER = 0
E_CONSOLE = 0
WAIT_OBJECT_0 = 0
WAIT_ABANDONED = 0x00000080
WAIT_TIMEOUT = 0x00000102
JOB_OBJECT_LIMIT_ACTIVE_PROCESS = 0x00000008
JOB_OBJECT_BASIC_LIMIT_INFORMATION_CLASS = 2
CREATE_SUSPENDED = 0x00000004

ERROR_ALREADY_EXISTS = 183
ERROR_CLASS_ALREADY_EXISTS = 1410

SYSTEM_EXECUTION_STATE = 16
ES_DISPLAY_REQUIRED = 0x00000002

# Raw Input
RIDEV_INPUTSINK = 0x00000100
RIDEV_DEVNOTIFY = 0x00002000
RID_INPUT = 0x10000003
RIDI_DEVICENAME = 0x20000007
RIM_TYPEMOUSE = 0
RIM_TYPEKEYBOARD = 1

RI_KEY_BREAK = 0x0001

RI_MOUSE_LEFT_BUTTON_DOWN = 0x0001
RI_MOUSE_RIGHT_BUTTON_DOWN = 0x0004
RI_MOUSE_MIDDLE_BUTTON_DOWN = 0x0010
RI_MOUSE_BUTTON_4_DOWN = 0x0040
RI_MOUSE_BUTTON_5_DOWN = 0x0100
RI_MOUSE_WHEEL = 0x0400
RI_MOUSE_HWHEEL = 0x0800

MOUSE_BUTTON_DOWN_MASK = (
    RI_MOUSE_LEFT_BUTTON_DOWN
    | RI_MOUSE_RIGHT_BUTTON_DOWN
    | RI_MOUSE_MIDDLE_BUTTON_DOWN
    | RI_MOUSE_BUTTON_4_DOWN
    | RI_MOUSE_BUTTON_5_DOWN
)

# HID Usage Page Generic Desktop / Mouse / Keyboard
HID_USAGE_PAGE_GENERIC = 0x01
HID_USAGE_GENERIC_MOUSE = 0x02
HID_USAGE_GENERIC_KEYBOARD = 0x06

# Modifier / lock-ish keys that should not by themselves wake the TV.
# Ignoring modifier key-down is also important when the TV is off and the user
# begins pressing Ctrl+Alt+P: P will be arbitrated with WM_HOTKEY, while Ctrl
# and Alt alone do not prematurely wake.
MODIFIER_VKS = {
    0x10,  # VK_SHIFT
    0x11,  # VK_CONTROL
    0x12,  # VK_MENU / Alt
    0x14,  # VK_CAPITAL
    0x5B,  # VK_LWIN
    0x5C,  # VK_RWIN
    0x90,  # VK_NUMLOCK
    0x91,  # VK_SCROLL
    0xA0, 0xA1,  # L/R SHIFT
    0xA2, 0xA3,  # L/R CONTROL
    0xA4, 0xA5,  # L/R ALT
}


# ---------------- Win32 structures ----------------

class LASTINPUTINFO(ctypes.Structure):
    _fields_ = [
        ("cbSize", wintypes.UINT),
        ("dwTime", wintypes.DWORD),
    ]


class POINT(ctypes.Structure):
    _fields_ = [
        ("x", wintypes.LONG),
        ("y", wintypes.LONG),
    ]


class MSG(ctypes.Structure):
    _fields_ = [
        ("hwnd", wintypes.HWND),
        ("message", wintypes.UINT),
        ("wParam", wintypes.WPARAM),
        ("lParam", wintypes.LPARAM),
        ("time", wintypes.DWORD),
        ("pt", POINT),
        ("lPrivate", wintypes.DWORD),
    ]


class RAWINPUTDEVICE(ctypes.Structure):
    _fields_ = [
        ("usUsagePage", wintypes.USHORT),
        ("usUsage", wintypes.USHORT),
        ("dwFlags", wintypes.DWORD),
        ("hwndTarget", wintypes.HWND),
    ]


class RAWINPUTHEADER(ctypes.Structure):
    _fields_ = [
        ("dwType", wintypes.DWORD),
        ("dwSize", wintypes.DWORD),
        ("hDevice", wintypes.HANDLE),
        ("wParam", wintypes.WPARAM),
    ]


class _RAWMOUSE_BUTTONS(ctypes.Structure):
    _fields_ = [
        ("usButtonFlags", wintypes.USHORT),
        ("usButtonData", wintypes.USHORT),
    ]


class _RAWMOUSE_UNION(ctypes.Union):
    # RAWMOUSE contains an anonymous union whose second member is itself an
    # anonymous struct. Marking "buttons" anonymous is required for ctypes to
    # expose mouse.usButtonFlags / mouse.usButtonData like the Win32 C struct.
    _anonymous_ = ("buttons",)
    _fields_ = [
        ("ulButtons", wintypes.ULONG),
        ("buttons", _RAWMOUSE_BUTTONS),
    ]


class RAWMOUSE(ctypes.Structure):
    _anonymous_ = ("u",)
    _fields_ = [
        ("usFlags", wintypes.USHORT),
        ("u", _RAWMOUSE_UNION),
        ("ulRawButtons", wintypes.ULONG),
        ("lLastX", wintypes.LONG),
        ("lLastY", wintypes.LONG),
        ("ulExtraInformation", wintypes.ULONG),
    ]


class RAWKEYBOARD(ctypes.Structure):
    _fields_ = [
        ("MakeCode", wintypes.USHORT),
        ("Flags", wintypes.USHORT),
        ("Reserved", wintypes.USHORT),
        ("VKey", wintypes.USHORT),
        ("Message", wintypes.UINT),
        ("ExtraInformation", wintypes.ULONG),
    ]


class _RAWINPUT_UNION(ctypes.Union):
    _fields_ = [
        ("mouse", RAWMOUSE),
        ("keyboard", RAWKEYBOARD),
    ]


class RAWINPUT(ctypes.Structure):
    _fields_ = [
        ("header", RAWINPUTHEADER),
        ("data", _RAWINPUT_UNION),
    ]


LRESULT = ctypes.c_ssize_t
WNDPROC = ctypes.WINFUNCTYPE(
    LRESULT,
    wintypes.HWND,
    wintypes.UINT,
    wintypes.WPARAM,
    wintypes.LPARAM,
)


class WNDCLASSW(ctypes.Structure):
    _fields_ = [
        ("style", wintypes.UINT),
        ("lpfnWndProc", WNDPROC),
        ("cbClsExtra", ctypes.c_int),
        ("cbWndExtra", ctypes.c_int),
        ("hInstance", wintypes.HINSTANCE),
        ("hIcon", wintypes.HANDLE),
        ("hCursor", wintypes.HANDLE),
        ("hbrBackground", wintypes.HANDLE),
        ("lpszMenuName", wintypes.LPCWSTR),
        ("lpszClassName", wintypes.LPCWSTR),
    ]


class KBDLLHOOKSTRUCT(ctypes.Structure):
    _fields_ = [
        ("vkCode", wintypes.DWORD),
        ("scanCode", wintypes.DWORD),
        ("flags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ctypes.c_size_t),
    ]


class GUID(ctypes.Structure):
    _fields_ = [
        ("Data1", wintypes.DWORD),
        ("Data2", wintypes.WORD),
        ("Data3", wintypes.WORD),
        ("Data4", ctypes.c_ubyte * 8),
    ]


class JOBOBJECT_BASIC_LIMIT_INFORMATION(ctypes.Structure):
    _fields_ = [
        ("PerProcessUserTimeLimit", ctypes.c_longlong),
        ("PerJobUserTimeLimit", ctypes.c_longlong),
        ("LimitFlags", wintypes.DWORD),
        ("MinimumWorkingSetSize", ctypes.c_size_t),
        ("MaximumWorkingSetSize", ctypes.c_size_t),
        ("ActiveProcessLimit", wintypes.DWORD),
        ("Affinity", ctypes.c_size_t),
        ("PriorityClass", wintypes.DWORD),
        ("SchedulingClass", wintypes.DWORD),
    ]


# ---------------- API prototypes ----------------

user32.GetLastInputInfo.argtypes = [ctypes.POINTER(LASTINPUTINFO)]
user32.GetLastInputInfo.restype = wintypes.BOOL

kernel32.GetTickCount.argtypes = []
kernel32.GetTickCount.restype = wintypes.DWORD

user32.RegisterHotKey.argtypes = [wintypes.HWND, ctypes.c_int, wintypes.UINT, wintypes.UINT]
user32.RegisterHotKey.restype = wintypes.BOOL
user32.UnregisterHotKey.argtypes = [wintypes.HWND, ctypes.c_int]
user32.UnregisterHotKey.restype = wintypes.BOOL
user32.MessageBoxW.argtypes = [
    wintypes.HWND, wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.UINT
]
user32.MessageBoxW.restype = ctypes.c_int

user32.GetMessageW.argtypes = [ctypes.POINTER(MSG), wintypes.HWND, wintypes.UINT, wintypes.UINT]
user32.GetMessageW.restype = ctypes.c_int
user32.TranslateMessage.argtypes = [ctypes.POINTER(MSG)]
user32.TranslateMessage.restype = wintypes.BOOL
user32.DispatchMessageW.argtypes = [ctypes.POINTER(MSG)]
user32.DispatchMessageW.restype = LRESULT
user32.DefWindowProcW.argtypes = [
    wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM
]
user32.DefWindowProcW.restype = LRESULT

kernel32.GetModuleHandleW.argtypes = [wintypes.LPCWSTR]
kernel32.GetModuleHandleW.restype = wintypes.HINSTANCE

user32.RegisterClassW.argtypes = [ctypes.POINTER(WNDCLASSW)]
user32.RegisterClassW.restype = wintypes.ATOM
user32.CreateWindowExW.argtypes = [
    wintypes.DWORD,
    wintypes.LPCWSTR,
    wintypes.LPCWSTR,
    wintypes.DWORD,
    ctypes.c_int,
    ctypes.c_int,
    ctypes.c_int,
    ctypes.c_int,
    wintypes.HWND,
    wintypes.HANDLE,
    wintypes.HINSTANCE,
    wintypes.LPVOID,
]
user32.CreateWindowExW.restype = wintypes.HWND
user32.DestroyWindow.argtypes = [wintypes.HWND]
user32.DestroyWindow.restype = wintypes.BOOL
user32.PostQuitMessage.argtypes = [ctypes.c_int]
user32.PostQuitMessage.restype = None

user32.RegisterRawInputDevices.argtypes = [
    ctypes.POINTER(RAWINPUTDEVICE), wintypes.UINT, wintypes.UINT
]
user32.RegisterRawInputDevices.restype = wintypes.BOOL
user32.GetRawInputData.argtypes = [
    ctypes.c_void_p,
    wintypes.UINT,
    wintypes.LPVOID,
    ctypes.POINTER(wintypes.UINT),
    wintypes.UINT,
]
user32.GetRawInputData.restype = wintypes.UINT
user32.GetRawInputDeviceInfoW.argtypes = [
    wintypes.HANDLE,
    wintypes.UINT,
    wintypes.LPVOID,
    ctypes.POINTER(wintypes.UINT),
]
user32.GetRawInputDeviceInfoW.restype = wintypes.UINT

kernel32.CreateMutexW.argtypes = [wintypes.LPVOID, wintypes.BOOL, wintypes.LPCWSTR]
kernel32.CreateMutexW.restype = wintypes.HANDLE
kernel32.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
kernel32.WaitForSingleObject.restype = wintypes.DWORD
kernel32.ReleaseMutex.argtypes = [wintypes.HANDLE]
kernel32.ReleaseMutex.restype = wintypes.BOOL
kernel32.CreateJobObjectW.argtypes = [wintypes.LPVOID, wintypes.LPCWSTR]
kernel32.CreateJobObjectW.restype = wintypes.HANDLE
kernel32.SetInformationJobObject.argtypes = [
    wintypes.HANDLE, ctypes.c_int, wintypes.LPVOID, wintypes.DWORD
]
kernel32.SetInformationJobObject.restype = wintypes.BOOL
kernel32.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
kernel32.AssignProcessToJobObject.restype = wintypes.BOOL
kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
kernel32.CloseHandle.restype = wintypes.BOOL

ntdll = ctypes.WinDLL("ntdll")
ntdll.NtResumeProcess.argtypes = [wintypes.HANDLE]
ntdll.NtResumeProcess.restype = wintypes.LONG

powrprof.CallNtPowerInformation.argtypes = [
    ctypes.c_int,
    wintypes.LPVOID,
    wintypes.ULONG,
    wintypes.LPVOID,
    wintypes.ULONG,
]
powrprof.CallNtPowerInformation.restype = wintypes.ULONG

HOOKPROC = ctypes.WINFUNCTYPE(
    LRESULT, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM
)
user32.SetWindowsHookExW.argtypes = [ctypes.c_int, HOOKPROC, wintypes.HINSTANCE, wintypes.DWORD]
user32.SetWindowsHookExW.restype = wintypes.HANDLE
user32.CallNextHookEx.argtypes = [
    wintypes.HANDLE, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM
]
user32.CallNextHookEx.restype = LRESULT
user32.UnhookWindowsHookEx.argtypes = [wintypes.HANDLE]
user32.UnhookWindowsHookEx.restype = wintypes.BOOL

ole32.CLSIDFromString.argtypes = [wintypes.LPCWSTR, ctypes.POINTER(GUID)]
ole32.CLSIDFromString.restype = wintypes.LONG
ole32.CoInitializeEx.argtypes = [wintypes.LPVOID, wintypes.DWORD]
ole32.CoInitializeEx.restype = wintypes.LONG
ole32.CoUninitialize.argtypes = []
ole32.CoUninitialize.restype = None
ole32.CoCreateInstance.argtypes = [
    ctypes.POINTER(GUID), wintypes.LPVOID, wintypes.DWORD,
    ctypes.POINTER(GUID), ctypes.POINTER(ctypes.c_void_p),
]
ole32.CoCreateInstance.restype = wintypes.LONG


def self_test_structures() -> tuple[bool, str]:
    """
    Catch ctypes layout and default input-policy regressions before install.
    """
    try:
        mouse = RAWMOUSE()
        mouse.usButtonFlags = RI_MOUSE_LEFT_BUTTON_DOWN
        mouse.usButtonData = 123
        mouse.lLastX = 7
        mouse.lLastY = -3

        if int(mouse.usButtonFlags) != RI_MOUSE_LEFT_BUTTON_DOWN:
            return False, "RAWMOUSE.usButtonFlags alias failed"
        if int(mouse.usButtonData) != 123:
            return False, "RAWMOUSE.usButtonData alias failed"
        if int(mouse.lLastX) != 7 or int(mouse.lLastY) != -3:
            return False, "RAWMOUSE motion fields failed"

        # On 32/64-bit Windows the Win32 RAWMOUSE layout is 24 bytes.
        size = ctypes.sizeof(RAWMOUSE)
        if size != 24:
            return False, f"Unexpected RAWMOUSE size {size}; expected 24"

        probe_config = DEFAULT_CONFIG.copy()
        probe_config["tv_ip"] = "127.0.0.1"
        probe = Controller(probe_config)
        probe._set_picture_off_state(True)
        probe.handle_raw_input(
            {"kind": "mouse_move", "device": "self-test", "dx": 1000, "dy": 1000}
        )
        if probe.pending_input_wake_at != 0.0 or probe.mouse_motion:
            return False, "Default mouse-movement wake suppression failed"

        probe.config["enable_mouse_move_wake"] = True
        probe.handle_raw_input(
            {"kind": "mouse_move", "device": "self-test", "dx": 1000, "dy": 1000}
        )
        if probe.pending_input_wake_at <= 0.0:
            return False, "Opt-in mouse-movement wake self-test failed"

        class ProbeTV:
            def __init__(self):
                self.set_volumes = []
                self.volume_set = threading.Event()

            def get_volume(self):
                return None

            def send(self, _key):
                return True

            def set_volume(self, value):
                self.set_volumes.append(value)
                self.volume_set.set()
                return True

        probe_tv = ProbeTV()
        volume = VolumeCoordinator(probe_config, probe_tv)
        with volume._lock:
            volume._tv_volume = 11
        if not volume.handle_key("down") or volume.handle_key("down"):
            volume.stop()
            return False, "TV volume-floor routing self-test failed"
        if not probe_tv.volume_set.wait(1.0) or probe_tv.set_volumes != [10]:
            volume.stop()
            return False, "Explicit TV volume target self-test failed"
        if volume.handle_key("up", False) or not volume.handle_key("up", True):
            volume.stop()
            return False, "System volume-maximum routing self-test failed"
        volume.stop()
        probe.stop()

        endpoint = WindowsEndpointVolume()
        try:
            endpoint.open()
            endpoint.is_max()
        finally:
            endpoint.close()

        return True, (
            f"Controller self-test passed (RAWMOUSE size={size}; "
            "mouse movement disabled by default; volume routing and endpoint passed)"
        )
    except Exception as exc:
        return False, f"Controller self-test failed: {exc!r}"


# ---------------- General helpers ----------------

def get_last_input_tick() -> int:
    info = LASTINPUTINFO()
    info.cbSize = ctypes.sizeof(info)
    if not user32.GetLastInputInfo(ctypes.byref(info)):
        raise ctypes.WinError(ctypes.get_last_error())
    return int(info.dwTime)


def get_idle_ms() -> int:
    now = int(kernel32.GetTickCount())
    return (now - get_last_input_tick()) & 0xFFFFFFFF


def display_is_explicitly_required() -> bool:
    state = wintypes.ULONG(0)
    status = powrprof.CallNtPowerInformation(
        SYSTEM_EXECUTION_STATE,
        None,
        0,
        ctypes.byref(state),
        ctypes.sizeof(state),
    )
    if status != 0:
        logger.debug("CallNtPowerInformation failed with status %s", status)
        return False
    return bool(int(state.value) & ES_DISPLAY_REQUIRED)


def _guid(text: str) -> GUID:
    value = GUID()
    result = ole32.CLSIDFromString(text, ctypes.byref(value))
    if result < 0:
        raise OSError(f"CLSIDFromString failed: HRESULT 0x{result & 0xFFFFFFFF:08X}")
    return value


def _com_method(pointer, index, restype, *argtypes):
    vtable = ctypes.cast(
        pointer, ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p))
    ).contents
    return ctypes.WINFUNCTYPE(restype, ctypes.c_void_p, *argtypes)(vtable[index])


def _check_hresult(result, operation):
    if result < 0:
        raise OSError(f"{operation} failed: HRESULT 0x{result & 0xFFFFFFFF:08X}")


class WindowsEndpointVolume:
    CLSID_MMDEVICE_ENUMERATOR = "{BCDE0395-E52F-467C-8E3D-C4579291692E}"
    IID_IMMDEVICE_ENUMERATOR = "{A95664D2-9614-4F35-A746-DE8DB63617E6}"
    IID_IAUDIO_ENDPOINT_VOLUME = "{5CDF2C82-841E-4546-9722-0CF74078229A}"

    def __init__(self):
        self._initialized = False
        self._enumerator = ctypes.c_void_p()

    def open(self) -> None:
        result = ole32.CoInitializeEx(None, COINIT_APARTMENTTHREADED)
        # S_OK and S_FALSE both require a matching CoUninitialize.
        if result in (0, 1):
            self._initialized = True
        elif result < 0:
            _check_hresult(result, "CoInitializeEx")

        clsid = _guid(self.CLSID_MMDEVICE_ENUMERATOR)
        iid_enumerator = _guid(self.IID_IMMDEVICE_ENUMERATOR)
        result = ole32.CoCreateInstance(
            ctypes.byref(clsid), None, CLSCTX_ALL,
            ctypes.byref(iid_enumerator), ctypes.byref(self._enumerator),
        )
        _check_hresult(result, "CoCreateInstance(MMDeviceEnumerator)")

    def is_max(self) -> bool:
        device = ctypes.c_void_p()
        endpoint = ctypes.c_void_p()
        get_default = _com_method(
            self._enumerator, 4, wintypes.LONG,
            ctypes.c_int, ctypes.c_int, ctypes.POINTER(ctypes.c_void_p),
        )
        result = get_default(
            self._enumerator, E_RENDER, E_CONSOLE, ctypes.byref(device)
        )
        _check_hresult(result, "IMMDeviceEnumerator.GetDefaultAudioEndpoint")

        try:
            iid_endpoint = _guid(self.IID_IAUDIO_ENDPOINT_VOLUME)
            activate = _com_method(
                device, 3, wintypes.LONG,
                ctypes.POINTER(GUID), wintypes.DWORD, wintypes.LPVOID,
                ctypes.POINTER(ctypes.c_void_p),
            )
            result = activate(
                device, ctypes.byref(iid_endpoint), CLSCTX_ALL, None,
                ctypes.byref(endpoint),
            )
            _check_hresult(result, "IMMDevice.Activate(IAudioEndpointVolume)")

            value = ctypes.c_float()
            get_scalar = _com_method(
                endpoint, 9, wintypes.LONG, ctypes.POINTER(ctypes.c_float)
            )
            result = get_scalar(endpoint, ctypes.byref(value))
            _check_hresult(result, "IAudioEndpointVolume.GetMasterVolumeLevelScalar")
            return float(value.value) >= 0.999
        finally:
            self._release(endpoint)
            self._release(device)

    @staticmethod
    def _release(pointer) -> None:
        if pointer:
            release = _com_method(pointer, 2, wintypes.ULONG)
            release(pointer)

    def close(self) -> None:
        self._release(self._enumerator)
        self._enumerator = ctypes.c_void_p()
        if self._initialized:
            ole32.CoUninitialize()
            self._initialized = False


class LowLevelVolumeHook:
    def __init__(self, handler):
        self.handler = handler
        self.handle = None
        self.endpoint_volume = WindowsEndpointVolume()
        self._consumed = set()
        self._callback = HOOKPROC(self._hook_proc)

    def install(self) -> None:
        self.endpoint_volume.open()
        self.handle = user32.SetWindowsHookExW(
            WH_KEYBOARD_LL, self._callback, kernel32.GetModuleHandleW(None), 0
        )
        if not self.handle:
            raise ctypes.WinError(ctypes.get_last_error())

    def _hook_proc(self, code, wparam, lparam):
        if code >= 0 and int(wparam) in {
            WM_KEYDOWN, WM_KEYUP, WM_SYSKEYDOWN, WM_SYSKEYUP,
        }:
            data = ctypes.cast(
                lparam, ctypes.POINTER(KBDLLHOOKSTRUCT)
            ).contents
            vkey = int(data.vkCode)
            if vkey in {VK_VOLUME_UP, VK_VOLUME_DOWN}:
                if int(wparam) in {WM_KEYDOWN, WM_SYSKEYDOWN}:
                    direction = "up" if vkey == VK_VOLUME_UP else "down"
                    try:
                        at_max = direction == "up" and self.endpoint_volume.is_max()
                        if self.handler(direction, at_max):
                            self._consumed.add(vkey)
                            return 1
                    except Exception as exc:
                        logger.warning("Volume-key routing failed: %r", exc)
                elif vkey in self._consumed:
                    self._consumed.discard(vkey)
                    return 1
        return user32.CallNextHookEx(self.handle, code, wparam, lparam)

    def close(self) -> None:
        if self.handle:
            user32.UnhookWindowsHookEx(self.handle)
        self.handle = None
        self._consumed.clear()
        self.endpoint_volume.close()


def parse_hotkey(spec: str) -> tuple[int, int]:
    parts = [p.strip().upper() for p in spec.split("+") if p.strip()]
    if len(parts) < 2:
        raise ValueError("Hotkey must include at least one modifier and one A-Z/0-9 key.")

    modifier_map = {
        "CTRL": MOD_CONTROL,
        "CONTROL": MOD_CONTROL,
        "ALT": MOD_ALT,
        "SHIFT": MOD_SHIFT,
        "WIN": MOD_WIN,
        "WINDOWS": MOD_WIN,
    }

    key = parts[-1]
    if len(key) != 1 or not key.isalnum() or ord(key) > 0x7F:
        raise ValueError("Hotkey final key must be one ASCII letter A-Z or digit 0-9.")

    modifiers = MOD_NOREPEAT
    for item in parts[:-1]:
        if item not in modifier_map:
            raise ValueError(f"Unsupported hotkey modifier: {item}")
        modifiers |= modifier_map[item]

    return modifiers, ord(key)


def load_config() -> dict:
    config = DEFAULT_CONFIG.copy()
    if not CONFIG_FILE.exists():
        raise FileNotFoundError(f"Missing configuration: {CONFIG_FILE}")

    with CONFIG_FILE.open("r", encoding="utf-8-sig") as f:
        user_config = json.load(f)
    config.update(user_config)
    if "remote_name" not in user_config:
        config["remote_name"] = "QN990F-PC-Controller"

    config["control_method"] = str(config.get("control_method", "lan")).strip().lower()
    if config["control_method"] not in {"lan", "smartthings"}:
        raise ValueError("control_method must be 'lan' or 'smartthings'.")
    config["tv_ip"] = str(config.get("tv_ip", "")).strip()
    if config["control_method"] == "lan" and not config["tv_ip"]:
        raise ValueError("config.json has an empty tv_ip.")

    config["port"] = int(config.get("port", 8002))
    config["idle_minutes"] = float(config.get("idle_minutes", 10))
    config["enable_idle_off"] = bool(config.get("enable_idle_off", config["idle_minutes"] > 0))
    config["respect_display_required"] = bool(config.get("respect_display_required", True))
    config["wake_guard_ms"] = max(0, int(config.get("wake_guard_ms", 800)))
    config["poll_interval_ms"] = max(25, int(config.get("poll_interval_ms", 50)))
    config["socket_timeout_seconds"] = max(1.0, float(config.get("socket_timeout_seconds", 5)))
    config["key_press_delay_seconds"] = max(
        0.0, float(config.get("key_press_delay_seconds", 0.05))
    )
    config["remote_name"] = str(config.get("remote_name", "")).strip()
    if not config["remote_name"]:
        raise ValueError("remote_name cannot be empty.")
    config["connection_refresh_seconds"] = max(
        0.0, float(config.get("connection_refresh_seconds", 8.0))
    )
    config["input_wake_debounce_ms"] = max(
        50, int(config.get("input_wake_debounce_ms", 180))
    )
    config["enable_mouse_move_wake"] = bool(
        config.get("enable_mouse_move_wake", False)
    )
    config["mouse_wake_threshold_counts"] = max(
        0, int(config.get("mouse_wake_threshold_counts", 24))
    )
    config["mouse_motion_window_ms"] = max(
        100, int(config.get("mouse_motion_window_ms", 500))
    )
    config["enable_volume_control"] = bool(config.get("enable_volume_control", True))
    config["tv_volume_floor"] = min(
        100, max(0, int(config.get("tv_volume_floor", 10)))
    )
    config["tv_volume_refresh_seconds"] = max(
        30.0, float(config.get("tv_volume_refresh_seconds", 30.0))
    )

    if config["control_method"] == "smartthings":
        config["smartthings_cli"] = str(config.get("smartthings_cli", "")).strip()
        config["smartthings_profile"] = str(
            config.get("smartthings_profile", "local.qn990f.picture-controller")
        ).strip()
        config["smartthings_device_id"] = str(
            config.get("smartthings_device_id", "")
        ).strip()
        if not config["smartthings_cli"]:
            raise ValueError("smartthings_cli is empty.")
        if not re.fullmatch(r"[A-Za-z0-9._-]{1,64}", config["smartthings_profile"]):
            raise ValueError("smartthings_profile contains unsupported characters.")
        if not re.fullmatch(
            r"[0-9A-Fa-f]{8}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}-"
            r"[0-9A-Fa-f]{4}-[0-9A-Fa-f]{12}",
            config["smartthings_device_id"],
        ):
            raise ValueError("smartthings_device_id is not a UUID.")
        config["smartthings_command_timeout_seconds"] = min(
            20.0,
            max(5.0, float(config.get("smartthings_command_timeout_seconds", 20.0))),
        )
        config["smartthings_auth_check_interval_seconds"] = min(
            3600.0,
            max(
                300.0,
                float(config.get("smartthings_auth_check_interval_seconds", 1800.0)),
            ),
        )

    ignored = config.get("ignored_input_device_substrings", [])
    if not isinstance(ignored, list):
        ignored = []
    config["ignored_input_device_substrings"] = [
        str(x).strip().lower() for x in ignored if str(x).strip()
    ]

    parse_hotkey(str(config.get("hotkey", "Ctrl+Alt+P")))
    return config


def write_status(**kwargs) -> None:
    data = {
        "pid": os.getpid(),
        "timestamp": time.time(),
        **kwargs,
    }
    try:
        STATUS_FILE.write_text(json.dumps(data, indent=2), encoding="utf-8")
    except OSError:
        pass


def show_smartthings_auth_prompt() -> None:
    helper = APP_DIR / "Reauthorize-SmartThings.ps1"
    if not helper.is_file():
        logger.error("SmartThings reauthorization helper is missing: %s", helper)
        return
    message = (
        "SmartThings authorization needs renewal. Picture Off commands cannot run "
        "until you sign in again.\n\nReauthorize now?"
    )
    result = user32.MessageBoxW(
        None,
        message,
        "Samsung TV Picture Controller",
        0x00000004 | 0x00000030 | 0x00010000,
    )
    if result != 6:
        return
    try:
        subprocess.Popen(
            [
                "powershell.exe",
                "-NoProfile",
                "-ExecutionPolicy",
                "Bypass",
                "-File",
                str(helper),
            ],
            cwd=str(APP_DIR),
            creationflags=getattr(subprocess, "CREATE_NEW_CONSOLE", 0),
        )
    except Exception as exc:
        logger.warning("Could not open SmartThings reauthorization: %r", exc)


# ---------------- Raw Input window ----------------

class RawInputWindow:
    """Hidden Win32 window that receives WM_INPUT and WM_HOTKEY."""

    def __init__(self, config: dict):
        self.config = config
        self.hwnd = None
        self.class_name = f"QN990FRawInput_{os.getpid()}"
        self.hinstance = kernel32.GetModuleHandleW(None)
        self.hotkey_event = threading.Event()
        self.raw_events: queue.SimpleQueue[dict] = queue.SimpleQueue()
        self._device_names: dict[int, str] = {}
        self._wndproc = WNDPROC(self._window_proc)

    def create(self) -> None:
        wc = WNDCLASSW()
        wc.style = 0
        wc.lpfnWndProc = self._wndproc
        wc.cbClsExtra = 0
        wc.cbWndExtra = 0
        wc.hInstance = self.hinstance
        wc.hIcon = None
        wc.hCursor = None
        wc.hbrBackground = None
        wc.lpszMenuName = None
        wc.lpszClassName = self.class_name

        atom = user32.RegisterClassW(ctypes.byref(wc))
        if not atom:
            err = ctypes.get_last_error()
            if err != ERROR_CLASS_ALREADY_EXISTS:
                raise ctypes.WinError(err)

        # Style 0 and never ShowWindow -> hidden top-level window.
        self.hwnd = user32.CreateWindowExW(
            0,
            self.class_name,
            "QN990F Raw Input",
            0,
            0, 0, 0, 0,
            None,
            None,
            self.hinstance,
            None,
        )
        if not self.hwnd:
            raise ctypes.WinError(ctypes.get_last_error())

        rid_array = (RAWINPUTDEVICE * 2)(
            RAWINPUTDEVICE(
                HID_USAGE_PAGE_GENERIC,
                HID_USAGE_GENERIC_MOUSE,
                RIDEV_INPUTSINK | RIDEV_DEVNOTIFY,
                self.hwnd,
            ),
            RAWINPUTDEVICE(
                HID_USAGE_PAGE_GENERIC,
                HID_USAGE_GENERIC_KEYBOARD,
                RIDEV_INPUTSINK | RIDEV_DEVNOTIFY,
                self.hwnd,
            ),
        )
        if not user32.RegisterRawInputDevices(
            rid_array, 2, ctypes.sizeof(RAWINPUTDEVICE)
        ):
            raise ctypes.WinError(ctypes.get_last_error())

        modifiers, vk = parse_hotkey(str(self.config["hotkey"]))
        if not user32.RegisterHotKey(self.hwnd, HOTKEY_ID, modifiers, vk):
            err = ctypes.get_last_error()
            raise RuntimeError(
                f"Could not register global hotkey {self.config['hotkey']} "
                f"(Win32 error {err}). Another program may already use it."
            )

        logger.info(
            "Raw Input registered for keyboard + mouse; hotkey=%s",
            self.config["hotkey"],
        )

    def close(self) -> None:
        if self.hwnd:
            try:
                user32.UnregisterHotKey(self.hwnd, HOTKEY_ID)
            except Exception:
                pass
            try:
                user32.DestroyWindow(self.hwnd)
            except Exception:
                pass
            self.hwnd = None

    def _device_name(self, hdevice) -> str:
        if not hdevice:
            return "<unknown-device>"

        key = int(ctypes.cast(hdevice, ctypes.c_void_p).value or 0)
        cached = self._device_names.get(key)
        if cached is not None:
            return cached

        size = wintypes.UINT(0)
        result = user32.GetRawInputDeviceInfoW(
            hdevice, RIDI_DEVICENAME, None, ctypes.byref(size)
        )
        if result == 0xFFFFFFFF or size.value == 0:
            name = f"<device:{key:#x}>"
        else:
            buf = ctypes.create_unicode_buffer(size.value + 1)
            cap = wintypes.UINT(len(buf))
            result = user32.GetRawInputDeviceInfoW(
                hdevice,
                RIDI_DEVICENAME,
                ctypes.cast(buf, wintypes.LPVOID),
                ctypes.byref(cap),
            )
            if result == 0xFFFFFFFF:
                name = f"<device:{key:#x}>"
            else:
                name = buf.value or f"<device:{key:#x}>"

        self._device_names[key] = name
        return name

    def _read_raw_input(self, lparam) -> dict | None:
        size = wintypes.UINT(0)
        res = user32.GetRawInputData(
            ctypes.c_void_p(int(lparam)),
            RID_INPUT,
            None,
            ctypes.byref(size),
            ctypes.sizeof(RAWINPUTHEADER),
        )
        if res == 0xFFFFFFFF or size.value == 0:
            return None

        buf = ctypes.create_string_buffer(size.value)
        res = user32.GetRawInputData(
            ctypes.c_void_p(int(lparam)),
            RID_INPUT,
            ctypes.cast(buf, wintypes.LPVOID),
            ctypes.byref(size),
            ctypes.sizeof(RAWINPUTHEADER),
        )
        if res == 0xFFFFFFFF:
            return None

        raw = ctypes.cast(buf, ctypes.POINTER(RAWINPUT)).contents
        device = self._device_name(raw.header.hDevice)

        if raw.header.dwType == RIM_TYPEKEYBOARD:
            kb = raw.data.keyboard
            vkey = int(kb.VKey)
            is_break = bool(int(kb.Flags) & RI_KEY_BREAK)
            return {
                "kind": "keyboard",
                "device": device,
                "vkey": vkey,
                "keydown": not is_break,
                "make_code": int(kb.MakeCode),
                "flags": int(kb.Flags),
                "message": int(kb.Message),
            }

        if raw.header.dwType == RIM_TYPEMOUSE:
            mouse = raw.data.mouse
            button_flags = int(mouse.usButtonFlags)
            dx = int(mouse.lLastX)
            dy = int(mouse.lLastY)

            if button_flags & (RI_MOUSE_WHEEL | RI_MOUSE_HWHEEL):
                return {
                    "kind": "mouse_wheel",
                    "device": device,
                    "button_flags": button_flags,
                    "button_data": int(mouse.usButtonData),
                    "dx": dx,
                    "dy": dy,
                }

            if button_flags & MOUSE_BUTTON_DOWN_MASK:
                return {
                    "kind": "mouse_button",
                    "device": device,
                    "button_flags": button_flags,
                    "dx": dx,
                    "dy": dy,
                }

            if dx != 0 or dy != 0:
                return {
                    "kind": "mouse_move",
                    "device": device,
                    "dx": dx,
                    "dy": dy,
                    "mouse_flags": int(mouse.usFlags),
                }

        return None

    def _window_proc(self, hwnd, msg, wparam, lparam):
        try:
            if msg == WM_HOTKEY and int(wparam) == HOTKEY_ID:
                self.hotkey_event.set()
                return 0

            if msg == WM_INPUT:
                event = self._read_raw_input(lparam)
                if event is not None:
                    self.raw_events.put(event)
                # This is an INPUTSINK window, but DefWindowProc is safe and
                # ensures any system-side raw-input cleanup is performed.
                return user32.DefWindowProcW(hwnd, msg, wparam, lparam)

            if msg == WM_INPUT_DEVICE_CHANGE:
                # Device handles can be reused. Clear names so future logs show
                # the current device identity.
                self._device_names.clear()
                logger.info(
                    "Raw Input device change wParam=%s device=%s",
                    int(wparam),
                    hex(int(lparam)) if lparam else "0x0",
                )
                return 0

            if msg == WM_DESTROY:
                user32.PostQuitMessage(0)
                return 0
        except Exception as exc:
            logger.exception("WindowProc error: %r", exc)

        return user32.DefWindowProcW(hwnd, msg, wparam, lparam)

    def drain_raw_events(self):
        while True:
            try:
                yield self.raw_events.get_nowait()
            except queue.Empty:
                break


# ---------------- Samsung client ----------------

class TVClient:
    def __init__(self, config: dict):
        self.config = config
        self._tv: SamsungTVWS | None = None
        self._lock = threading.Lock()
        self._last_send_monotonic = 0.0

    def _new_tv(self, pairing: bool = False) -> SamsungTVWS:
        timeout = 45.0 if pairing else float(self.config["socket_timeout_seconds"])
        return SamsungTVWS(
            host=self.config["tv_ip"],
            port=int(self.config["port"]),
            token_file=str(TOKEN_FILE),
            timeout=timeout,
            key_press_delay=float(self.config["key_press_delay_seconds"]),
            name=str(self.config["remote_name"]),
        )

    def pair(self) -> None:
        tv = self._new_tv(pairing=True)
        try:
            tv.open()
        finally:
            try:
                tv.close()
            except Exception:
                pass

    def _reset(self) -> None:
        if self._tv is not None:
            try:
                self._tv.close()
            except Exception:
                pass
        self._tv = None
        self._last_send_monotonic = 0.0

    def send(self, key: str) -> bool:
        with self._lock:
            now = time.monotonic()
            refresh_after = float(self.config.get("connection_refresh_seconds", 8.0))

            if (
                self._tv is not None
                and refresh_after > 0
                and self._last_send_monotonic > 0
                and (now - self._last_send_monotonic) >= refresh_after
            ):
                logger.info(
                    "Discarding idle Samsung WebSocket before %s (idle %.2fs)",
                    key,
                    now - self._last_send_monotonic,
                )
                self._reset()

            for attempt in range(2):
                try:
                    if self._tv is None:
                        logger.info("Opening Samsung WebSocket for %s", key)
                        self._tv = self._new_tv()
                    self._tv.send_key(
                        key,
                        key_press_delay=float(self.config["key_press_delay_seconds"]),
                    )
                    self._last_send_monotonic = time.monotonic()
                    logger.info("Sent %s", key)
                    return True
                except Exception as exc:
                    logger.warning(
                        "Send %s failed (attempt %d): %r",
                        key,
                        attempt + 1,
                        exc,
                    )
                    self._reset()
                    if attempt == 0:
                        time.sleep(0.12)
            return False

    def get_volume(self):
        # The LAN WebSocket accepts volume keys but has no volume-state query.
        return None

    def close(self) -> None:
        with self._lock:
            self._reset()


class SmartThingsAuthRequired(RuntimeError):
    pass


class SmartThingsTVClient:
    EXECUTE_CAPABILITY = "execute"
    REMOTE_MARKER_CAPABILITY = "samsungvd.remoteControl"
    AUDIO_VOLUME_CAPABILITY = "audioVolume"
    REMOTE_RESOURCE = "/sec/tv/remotecontrol"

    def __init__(self, config: dict):
        self.config = config
        self._lock = threading.Lock()
        self._command_times = []
        self._authorization_required = False

    @staticmethod
    def _acquire_process_lock(timeout):
        handle = kernel32.CreateMutexW(
            None, False, "Local\\SamsungTVPictureControllerSmartThings"
        )
        if not handle:
            raise ctypes.WinError(ctypes.get_last_error())
        result = kernel32.WaitForSingleObject(handle, max(1, int(timeout * 1000)))
        if result not in {WAIT_OBJECT_0, WAIT_ABANDONED}:
            error = ctypes.get_last_error()
            kernel32.CloseHandle(handle)
            if result == WAIT_TIMEOUT:
                raise RuntimeError("Timed out waiting for another SmartThings operation.")
            raise ctypes.WinError(error)
        return handle

    @staticmethod
    def _is_auth_error(detail):
        detail = detail.lower()
        return any(marker in detail for marker in (
            "enoent",
            "invalid_grant",
            "refresh token",
            "401",
            "authorization requires user interaction",
        ))

    def authorization_required(self):
        return self._authorization_required

    @staticmethod
    def _run_noninteractive(command, environment, timeout):
        job = kernel32.CreateJobObjectW(None, None)
        if not job:
            raise ctypes.WinError(ctypes.get_last_error())
        limits = JOBOBJECT_BASIC_LIMIT_INFORMATION()
        limits.LimitFlags = JOB_OBJECT_LIMIT_ACTIVE_PROCESS
        limits.ActiveProcessLimit = 1
        process = None
        try:
            if not kernel32.SetInformationJobObject(
                job,
                JOB_OBJECT_BASIC_LIMIT_INFORMATION_CLASS,
                ctypes.byref(limits),
                ctypes.sizeof(limits),
            ):
                raise ctypes.WinError(ctypes.get_last_error())
            process = subprocess.Popen(
                command,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                env=environment,
                creationflags=(
                    getattr(subprocess, "CREATE_NO_WINDOW", 0) | CREATE_SUSPENDED
                ),
            )
            process_handle = wintypes.HANDLE(int(process._handle))
            if not kernel32.AssignProcessToJobObject(job, process_handle):
                raise ctypes.WinError(ctypes.get_last_error())
            resume_status = ntdll.NtResumeProcess(process_handle)
            if resume_status < 0:
                raise OSError(
                    f"NtResumeProcess failed: NTSTATUS "
                    f"0x{resume_status & 0xFFFFFFFF:08X}"
                )
            stdout, stderr = process.communicate(timeout=timeout)
            return subprocess.CompletedProcess(
                command, process.returncode, stdout, stderr
            )
        except Exception:
            if process is not None and process.poll() is None:
                process.kill()
                process.communicate()
            raise
        finally:
            kernel32.CloseHandle(job)

    def _run(self, *args, timeout=None, allow_login=False):
        auth_message = (
            "SmartThings authorization requires user interaction; "
            "run Reauthorize SmartThings from the Start menu."
        )
        if self._authorization_required and not allow_login:
            raise SmartThingsAuthRequired(auth_message)
        cli = Path(str(self.config["smartthings_cli"])).expanduser()
        if not cli.is_file():
            raise RuntimeError(f"SmartThings CLI is missing: {cli}")
        command = [
            str(cli), *args,
            "--profile", str(self.config["smartthings_profile"]),
            "--token", "",
            "--language", "NONE",
        ]
        environment = os.environ.copy()
        environment.pop("SMARTTHINGS_TOKEN", None)
        if not allow_login:
            environment["BROWSER"] = "none"
        command_timeout = (
            float(self.config["smartthings_command_timeout_seconds"])
            if timeout is None else float(timeout)
        )
        process_lock = self._acquire_process_lock(command_timeout)
        try:
            try:
                if allow_login:
                    result = subprocess.run(
                        command,
                        capture_output=True,
                        text=True,
                        env=environment,
                        timeout=command_timeout,
                        check=False,
                        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                    )
                else:
                    result = self._run_noninteractive(
                        command, environment, command_timeout
                    )
            except subprocess.TimeoutExpired as exc:
                raise RuntimeError(
                    "SmartThings command timed out. Check the network connection."
                ) from exc
        finally:
            kernel32.ReleaseMutex(process_lock)
            kernel32.CloseHandle(process_lock)
        if result.returncode != 0:
            detail = "\n".join(
                part for part in (result.stdout, result.stderr) if part
            ).strip()
            if len(detail) > 1200:
                detail = detail[-1200:]
            if self._is_auth_error(detail):
                self._authorization_required = True
                raise SmartThingsAuthRequired(auth_message)
            raise RuntimeError(detail or f"SmartThings CLI exited with {result.returncode}")
        if allow_login:
            self._authorization_required = False
        return result.stdout

    def check_authorization(self):
        with self._lock:
            if self._authorization_required:
                return False
            try:
                self._run(
                    "devices", str(self.config["smartthings_device_id"]), "--json"
                )
                logger.info("SmartThings authorization check succeeded")
                return True
            except SmartThingsAuthRequired as exc:
                logger.error("SmartThings authorization check failed: %s", exc)
                return False
            except Exception as exc:
                logger.warning("SmartThings authorization check unavailable: %r", exc)
                return None

    def pair(self):
        output = self._run(
            "devices", str(self.config["smartthings_device_id"]), "--json",
            timeout=650,
            allow_login=True,
        )
        try:
            device = json.loads(output)
        except json.JSONDecodeError as exc:
            raise RuntimeError("SmartThings CLI returned invalid device data.") from exc
        capabilities = set()
        categories = set()
        for component in device.get("components", []):
            if component.get("id") != "main":
                continue
            for capability in component.get("capabilities", []):
                capabilities.add(
                    capability if isinstance(capability, str)
                    else str(capability.get("id", ""))
                )
            for category in component.get("categories", []):
                categories.add(
                    category if isinstance(category, str)
                    else str(category.get("name", ""))
                )
        required = {self.EXECUTE_CAPABILITY, self.REMOTE_MARKER_CAPABILITY}
        if self.config["enable_volume_control"]:
            required.add(self.AUDIO_VOLUME_CAPABILITY)
        if not required.issubset(capabilities) or "Television" not in categories:
            raise RuntimeError(
                "Selected device does not expose the required SmartThings TV controls."
            )
        if device.get("manufacturerName") != "Samsung Electronics" or \
           device.get("type") != "OCF":
            raise RuntimeError("Selected SmartThings device is not a Samsung OCF TV.")
        return str(device.get("label") or device.get("name") or "Samsung TV")

    def _ensure_command_budget(self, needed):
        cutoff = time.monotonic() - 60.0
        self._command_times = [stamp for stamp in self._command_times if stamp > cutoff]
        if len(self._command_times) + needed > 100:
            raise RuntimeError("SmartThings command limit reached; wait one minute.")

    def _send_ocf_remote(self, remote_key):
        payload = {
            "x.com.samsung.tv.keyvalue": remote_key,
            "x.com.samsung.tv.keystatus": "pressAndRelease",
        }
        argument = "main:{capability}:execute({resource},{payload})".format(
            capability=self.EXECUTE_CAPABILITY,
            resource=json.dumps(self.REMOTE_RESOURCE),
            payload=json.dumps(payload, separators=(",", ":")),
        )
        self._command_times.append(time.monotonic())
        self._run(
            "devices:commands", str(self.config["smartthings_device_id"]), argument
        )
        logger.info("SmartThings sent OCF remote key %s", remote_key)

    def _send_volume_command(self, remote_key):
        command = "volumeUp" if remote_key == "KEY_VOLUP" else "volumeDown"
        self._command_times.append(time.monotonic())
        self._run(
            "devices:commands",
            str(self.config["smartthings_device_id"]),
            f"main:{self.AUDIO_VOLUME_CAPABILITY}:{command}()",
        )
        logger.info("SmartThings sent audioVolume.%s", command)

    def send(self, key: str) -> bool:
        with self._lock:
            try:
                allowed = {
                    str(self.config["picture_off_key"]),
                    str(self.config["wake_key"]),
                    "KEY_VOLUP",
                    "KEY_VOLDOWN",
                }
                if key not in allowed:
                    raise ValueError(f"Unsupported SmartThings action: {key}")
                self._ensure_command_budget(1)
                if key in {"KEY_VOLUP", "KEY_VOLDOWN"}:
                    self._send_volume_command(key)
                else:
                    self._send_ocf_remote(key)
                return True
            except Exception as exc:
                logger.warning("SmartThings action %s failed: %r", key, exc)
                return False

    def get_volume(self):
        with self._lock:
            try:
                output = self._run(
                    "devices:status", str(self.config["smartthings_device_id"]), "--json"
                )
                status = json.loads(output)
                value = status["components"]["main"]["audioVolume"]["volume"]["value"]
                return min(100, max(0, int(value)))
            except Exception as exc:
                logger.warning("SmartThings volume query failed: %r", exc)
                return None

    def set_volume(self, value: int) -> bool:
        with self._lock:
            try:
                target = min(100, max(0, int(value)))
                self._ensure_command_budget(1)
                self._command_times.append(time.monotonic())
                self._run(
                    "devices:commands",
                    str(self.config["smartthings_device_id"]),
                    f"main:{self.AUDIO_VOLUME_CAPABILITY}:setVolume({target})",
                )
                logger.info("SmartThings set audioVolume to %d", target)
                return True
            except Exception as exc:
                logger.warning("SmartThings set volume failed: %r", exc)
                return False

    def close(self) -> None:
        pass


def make_tv_client(config: dict):
    if config["control_method"] == "smartthings":
        return SmartThingsTVClient(config)
    return TVClient(config)


class VolumeCoordinator:
    BUFFER_SECONDS = 0.2

    def __init__(self, config: dict, tv):
        self.config = config
        self.tv = tv
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._commands = queue.SimpleQueue()
        self._tv_volume = None
        self._estimate_valid_until = 0.0
        self._buffer_start_volume = None
        self._buffer_deadline = 0.0
        self._worker = threading.Thread(
            target=self._run, daemon=True, name="QN990F-VolumeControl"
        )
        self._worker.start()

    def handle_key(self, direction: str, system_is_max=False) -> bool:
        if getattr(self.tv, "authorization_required", lambda: False)():
            return False
        if direction == "up":
            if not system_is_max:
                return False
            with self._lock:
                if self._tv_volume is None:
                    self._commands.put("KEY_VOLUP")
                    return True
                if self._tv_volume >= 100:
                    return True
                notify = self._buffer_deadline == 0.0
                if notify:
                    self._buffer_start_volume = self._tv_volume
                self._tv_volume += 1
                now = time.monotonic()
                self._buffer_deadline = now + self.BUFFER_SECONDS
                self._estimate_valid_until = now + max(
                    5.0, float(self.config["tv_volume_refresh_seconds"]) * 2
                )
            if notify:
                self._commands.put("flush_volume")
            return True

        floor = int(self.config["tv_volume_floor"])
        with self._lock:
            if self._tv_volume is None or self._tv_volume <= floor:
                return False
            notify = self._buffer_deadline == 0.0
            if notify:
                self._buffer_start_volume = self._tv_volume
            self._tv_volume -= 1
            now = time.monotonic()
            self._buffer_deadline = now + self.BUFFER_SECONDS
            self._estimate_valid_until = now + max(
                5.0, float(self.config["tv_volume_refresh_seconds"]) * 2
            )
        if notify:
            self._commands.put("flush_volume")
        return True

    def _flush_volume_buffer(self) -> None:
        while not self._stop.is_set():
            with self._lock:
                remaining = self._buffer_deadline - time.monotonic()
            if remaining > 0:
                if self._stop.wait(remaining):
                    return
                continue
            with self._lock:
                target = self._tv_volume
                start = self._buffer_start_volume
                self._buffer_deadline = 0.0
                self._buffer_start_volume = None
            if target is None or target == start:
                return
            if not self.tv.set_volume(target):
                with self._lock:
                    self._tv_volume = None
                    self._estimate_valid_until = 0.0
            return

    def _run(self) -> None:
        refresh_at = 0.0
        while not self._stop.is_set():
            if getattr(self.tv, "authorization_required", lambda: False)():
                return
            now = time.monotonic()
            if now >= refresh_at:
                with self._lock:
                    estimate_is_current = now < self._estimate_valid_until
                if not estimate_is_current:
                    volume = self.tv.get_volume()
                    if volume is not None:
                        with self._lock:
                            self._tv_volume = volume
                    elif getattr(
                        self.tv, "authorization_required", lambda: False
                    )():
                        return
                refresh_at = time.monotonic() + float(
                    self.config["tv_volume_refresh_seconds"]
                )
            if self._stop.is_set():
                break
            try:
                command = self._commands.get(timeout=0.1)
            except queue.Empty:
                continue
            if command == "flush_volume":
                self._flush_volume_buffer()
                with self._lock:
                    if self._tv_volume is None:
                        refresh_at = 0.0
            else:
                self.tv.send(command)

    def stop(self) -> None:
        self._stop.set()
        timeout = float(
            self.config.get("smartthings_command_timeout_seconds", 0.0)
        ) + 1.0
        self._worker.join(timeout=max(1.0, timeout))


# ---------------- Controller state machine ----------------

class Controller:
    def __init__(self, config: dict):
        self.config = config
        self.tv = make_tv_client(config)
        self.volume = (
            VolumeCoordinator(config, self.tv)
            if config["enable_volume_control"] else None
        )
        self._operation_lock = threading.Lock()
        self._state_lock = threading.Lock()
        self._input_lock = threading.Lock()
        self._stop = threading.Event()
        self._auth_notice_sent = False
        self._auth_thread = None

        self.picture_off = False
        self.wake_not_before = 0.0
        self.next_off_attempt = 0.0
        self.next_wake_attempt = 0.0

        self.hotkey_input_suppress_until = 0.0
        self.pending_input_wake_at = 0.0
        self.pending_input_source = ""
        self.pending_input_device = ""

        # device -> (last_motion_time, accumulated |dx|+|dy|)
        self.mouse_motion: dict[str, tuple[float, int]] = {}

        self._display_required_cache = False
        self._display_required_checked_at = 0.0

    def start_authorization_monitor(self) -> None:
        if self.config["control_method"] != "smartthings":
            return
        self._auth_thread = threading.Thread(
            target=self._authorization_monitor,
            daemon=True,
            name="QN990F-SmartThingsAuth",
        )
        self._auth_thread.start()

    def _report_authorization_required(self) -> None:
        with self._state_lock:
            if self._auth_notice_sent:
                return
            self._auth_notice_sent = True
        logger.error("SmartThings authorization requires interactive sign-in")
        write_status(
            running=True,
            state="authorization_required",
            last_action="smartthings_auth_failed",
            reason="reauthorization_required",
        )
        threading.Thread(
            target=show_smartthings_auth_prompt,
            daemon=True,
            name="QN990F-SmartThingsAuthPrompt",
        ).start()

    def _authorization_monitor(self) -> None:
        if self._stop.wait(5.0):
            return
        interval = float(self.config["smartthings_auth_check_interval_seconds"])
        check_at = 0.0
        while not self._stop.is_set():
            if self.tv.authorization_required():
                self._report_authorization_required()
                return
            now = time.monotonic()
            if now >= check_at:
                result = self.tv.check_authorization()
                if result is False:
                    self._report_authorization_required()
                    return
                check_at = now + (
                    interval if result is True else min(interval, 300.0)
                )
            if self._stop.wait(min(1.0, max(0.0, check_at - time.monotonic()))):
                return

    def _report_auth_after_failure(self) -> bool:
        if self.config["control_method"] == "smartthings" and \
           getattr(self.tv, "authorization_required", lambda: False)():
            self._report_authorization_required()
            return True
        return False

    def handle_volume_key(self, direction: str, system_is_max=False) -> bool:
        if self.volume is None:
            return False
        return self.volume.handle_key(direction, system_is_max)

    def _set_picture_off_state(self, off: bool) -> None:
        with self._state_lock:
            self.picture_off = off

    def is_picture_off(self) -> bool:
        with self._state_lock:
            return self.picture_off

    def _clear_pending_wake(self) -> None:
        with self._input_lock:
            self.pending_input_wake_at = 0.0
            self.pending_input_source = ""
            self.pending_input_device = ""
            self.mouse_motion.clear()

    def blank(self, reason: str, force: bool = False) -> bool:
        with self._operation_lock:
            if self.is_picture_off() and not force:
                return True

            ok = self.tv.send(str(self.config["picture_off_key"]))
            if not ok:
                self.next_off_attempt = time.monotonic() + 5.0
                if not self._report_auth_after_failure():
                    write_status(
                        running=True,
                        state="error",
                        last_action="blank_failed",
                        reason=reason,
                    )
                return False

            now = time.monotonic()
            self.wake_not_before = now + (
                int(self.config["wake_guard_ms"]) / 1000.0
            )
            self._clear_pending_wake()
            self._set_picture_off_state(True)

            logger.info("Picture off (%s)", reason)
            write_status(
                running=True,
                state="picture_off",
                last_action="blank",
                reason=reason,
            )
            return True

    def wake(self, reason: str, source: str = "", device: str = "") -> bool:
        with self._operation_lock:
            if not self.is_picture_off():
                return True

            now = time.monotonic()
            # A forced hotkey blank may finish after the monitor dequeues a
            # pending Raw Input wake. Recheck the newly established guard
            # after acquiring the operation lock so that stale input cannot
            # immediately undo the successful Picture Off command.
            if reason == "raw_input" and now < self.wake_not_before:
                return False
            if now < self.next_wake_attempt:
                return False

            ok = self.tv.send(str(self.config["wake_key"]))
            if not ok:
                self.next_wake_attempt = now + 1.0
                if not self._report_auth_after_failure():
                    write_status(
                        running=True,
                        state="error",
                        last_action="wake_failed",
                        reason=reason,
                    )
                return False

            self._set_picture_off_state(False)
            self._clear_pending_wake()
            self.next_wake_attempt = 0.0

            if source or device:
                logger.info(
                    "Picture wake (%s) source=%s device=%s",
                    reason,
                    source or "<unknown>",
                    device or "<unknown>",
                )
            else:
                logger.info("Picture wake (%s)", reason)

            write_status(
                running=True,
                state="awake",
                last_action="wake",
                reason=reason,
                source=source,
                device=device,
            )
            return True

    def handle_hotkey(self) -> None:
        # One-way action: Ctrl+Alt+P ALWAYS means Picture Off.
        #
        # Raw keyboard input for the P key may arrive around the same time.
        # Suppress any candidate wake for 600 ms and force the off command.
        now = time.monotonic()
        with self._input_lock:
            self.hotkey_input_suppress_until = now + 0.60
            self.pending_input_wake_at = 0.0
            self.pending_input_source = ""
            self.pending_input_device = ""
            self.mouse_motion.clear()

        logger.info(
            "WM_HOTKEY action; local_picture_off=%s",
            self.is_picture_off(),
        )
        self.blank("hotkey", force=True)

    def _device_is_ignored(self, device: str) -> bool:
        d = device.lower()
        return any(token in d for token in self.config["ignored_input_device_substrings"])

    def _schedule_raw_wake(
        self,
        source: str,
        device: str,
        delay_ms: int | None = None,
    ) -> None:
        now = time.monotonic()
        delay = (
            int(self.config["input_wake_debounce_ms"])
            if delay_ms is None
            else max(0, int(delay_ms))
        ) / 1000.0

        with self._input_lock:
            if now < self.hotkey_input_suppress_until:
                logger.info(
                    "Raw wake candidate suppressed by hotkey arbitration "
                    "source=%s device=%s",
                    source,
                    device,
                )
                return

            due = now + delay
            if self.pending_input_wake_at <= 0.0 or due < self.pending_input_wake_at:
                self.pending_input_wake_at = due
                self.pending_input_source = source
                self.pending_input_device = device
                logger.info(
                    "Raw wake scheduled in %d ms source=%s device=%s",
                    int(delay * 1000),
                    source,
                    device,
                )

    def handle_raw_input(self, event: dict) -> None:
        if not self.is_picture_off():
            return

        now = time.monotonic()
        if now < self.wake_not_before:
            return

        device = str(event.get("device", "<unknown-device>"))
        if self._device_is_ignored(device):
            logger.info(
                "Ignored raw input from configured ignored device=%s kind=%s",
                device,
                event.get("kind"),
            )
            return

        kind = event.get("kind")

        if kind == "keyboard":
            if not event.get("keydown", False):
                return

            vkey = int(event.get("vkey", 0))
            if vkey in MODIFIER_VKS or vkey in (0, 0xFF):
                return

            source = f"keyboard:vkey=0x{vkey:02X}"
            self._schedule_raw_wake(source, device)
            return

        if kind == "mouse_button":
            flags = int(event.get("button_flags", 0))
            self._schedule_raw_wake(
                f"mouse_button:flags=0x{flags:04X}",
                device,
                delay_ms=80,
            )
            return

        if kind == "mouse_wheel":
            flags = int(event.get("button_flags", 0))
            self._schedule_raw_wake(
                f"mouse_wheel:flags=0x{flags:04X}",
                device,
                delay_ms=80,
            )
            return

        if kind == "mouse_move":
            if not self.config["enable_mouse_move_wake"]:
                return

            dx = int(event.get("dx", 0))
            dy = int(event.get("dy", 0))
            amount = abs(dx) + abs(dy)
            if amount <= 0:
                return

            threshold = int(self.config["mouse_wake_threshold_counts"])
            if threshold <= 0:
                self._schedule_raw_wake(
                    f"mouse_move:dx={dx},dy={dy},sum={amount}",
                    device,
                )
                return

            window = int(self.config["mouse_motion_window_ms"]) / 1000.0
            qualified = False
            total = amount

            with self._input_lock:
                last_time, old_total = self.mouse_motion.get(device, (0.0, 0))
                if (now - last_time) <= window:
                    total = old_total + amount
                self.mouse_motion[device] = (now, total)

                if total >= threshold:
                    self.mouse_motion.pop(device, None)
                    qualified = True

            if qualified:
                self._schedule_raw_wake(
                    f"mouse_move:threshold={threshold},sum={total},dx={dx},dy={dy}",
                    device,
                )

    def _display_required(self) -> bool:
        now = time.monotonic()
        if (now - self._display_required_checked_at) >= 2.0:
            self._display_required_checked_at = now
            try:
                self._display_required_cache = display_is_explicitly_required()
            except Exception as exc:
                logger.debug("Display-required query failed: %r", exc)
                self._display_required_cache = False
        return self._display_required_cache

    def monitor_loop(self) -> None:
        interval = int(self.config["poll_interval_ms"]) / 1000.0
        idle_threshold_ms = (
            max(0.0, float(self.config["idle_minutes"])) * 60_000.0
        )

        while not self._stop.is_set():
            try:
                now = time.monotonic()

                # Raw Input wake candidates are intentionally delayed a little,
                # allowing a Ctrl+Alt+P WM_HOTKEY to cancel its own P key event.
                if self.is_picture_off():
                    due = 0.0
                    source = ""
                    device = ""
                    suppressed = False

                    with self._input_lock:
                        if (
                            self.pending_input_wake_at > 0.0
                            and now >= self.pending_input_wake_at
                        ):
                            if now < self.hotkey_input_suppress_until:
                                self.pending_input_wake_at = 0.0
                                self.pending_input_source = ""
                                self.pending_input_device = ""
                                suppressed = True
                            else:
                                due = self.pending_input_wake_at
                                source = self.pending_input_source
                                device = self.pending_input_device
                                self.pending_input_wake_at = 0.0
                                self.pending_input_source = ""
                                self.pending_input_device = ""

                    if suppressed:
                        logger.info("Pending Raw Input wake cancelled by hotkey")
                    elif due > 0.0:
                        self.wake(
                            "raw_input",
                            source=source,
                            device=device,
                        )

                else:
                    # GetLastInputInfo is retained only for optional auto-OFF.
                    # It is no longer used to decide whether to WAKE.
                    auto_enabled = (
                        bool(self.config["enable_idle_off"])
                        and idle_threshold_ms > 0
                    )
                    if auto_enabled and now >= self.next_off_attempt:
                        idle = get_idle_ms()
                        if idle >= idle_threshold_ms:
                            keep_display = (
                                bool(self.config["respect_display_required"])
                                and self._display_required()
                            )
                            if not keep_display:
                                self.blank("idle")
                            else:
                                logger.debug(
                                    "Idle threshold reached but "
                                    "ES_DISPLAY_REQUIRED is active"
                                )
            except Exception as exc:
                logger.exception("Monitor loop error: %r", exc)

            self._stop.wait(interval)

    def stop(self) -> None:
        self._stop.set()
        if self._auth_thread:
            timeout = float(self.config["smartthings_command_timeout_seconds"]) + 1.0
            self._auth_thread.join(timeout=timeout)
        if self.volume:
            self.volume.stop()
        self.tv.close()


# ---------------- Process / hotkey checks ----------------

def acquire_single_instance() -> wintypes.HANDLE:
    handle = kernel32.CreateMutexW(
        None, False, "Local\\QN990FPictureController"
    )
    if not handle:
        raise ctypes.WinError(ctypes.get_last_error())

    if ctypes.get_last_error() == ERROR_ALREADY_EXISTS:
        kernel32.CloseHandle(handle)
        raise RuntimeError("Samsung TV Picture Controller is already running.")
    return handle


def register_configured_hotkey_for_test(config: dict) -> None:
    modifiers, vk = parse_hotkey(str(config["hotkey"]))
    if not user32.RegisterHotKey(None, HOTKEY_ID, modifiers, vk):
        err = ctypes.get_last_error()
        raise RuntimeError(
            f"Could not register global hotkey {config['hotkey']} "
            f"(Win32 error {err}). Another program may already use it."
        )


def check_hotkey(config: dict) -> int:
    try:
        register_configured_hotkey_for_test(config)
        print(f"Hotkey {config['hotkey']} is available.")
        return 0
    except Exception as exc:
        print(str(exc), file=sys.stderr)
        return 1
    finally:
        user32.UnregisterHotKey(None, HOTKEY_ID)


def remove_pid_file() -> None:
    try:
        if (
            PID_FILE.exists()
            and PID_FILE.read_text(encoding="utf-8").strip() == str(os.getpid())
        ):
            PID_FILE.unlink(missing_ok=True)
    except OSError:
        pass


def run_daemon(config: dict) -> int:
    mutex = None
    controller = None
    input_window = None
    volume_hook = None

    try:
        mutex = acquire_single_instance()

        PID_FILE.write_text(str(os.getpid()), encoding="utf-8")
        atexit.register(remove_pid_file)

        controller = Controller(config)
        controller.start_authorization_monitor()
        input_window = RawInputWindow(config)
        input_window.create()
        if config["enable_volume_control"]:
            volume_hook = LowLevelVolumeHook(controller.handle_volume_key)
            volume_hook.install()

        monitor = threading.Thread(
            target=controller.monitor_loop,
            name="QN990F-Monitor",
            daemon=True,
        )
        monitor.start()

        logger.info(
            "Controller v3.2 started. connection=%s TV=%s:%s hotkey=%s idle=%s min auto=%s "
            "mouse_move_wake=%s mouse_threshold=%s",
            config["control_method"],
            config["tv_ip"],
            config["port"],
            config["hotkey"],
            config["idle_minutes"],
            config["enable_idle_off"],
            config["enable_mouse_move_wake"],
            config["mouse_wake_threshold_counts"],
        )
        write_status(
            running=True,
            state="awake",
            version="3.2",
            hotkey=config["hotkey"],
        )

        msg = MSG()
        while True:
            result = user32.GetMessageW(
                ctypes.byref(msg), None, 0, 0
            )
            if result == 0:
                break
            if result == -1:
                raise ctypes.WinError(ctypes.get_last_error())

            user32.TranslateMessage(ctypes.byref(msg))
            user32.DispatchMessageW(ctypes.byref(msg))

            if input_window.hotkey_event.is_set():
                input_window.hotkey_event.clear()
                logger.info("Windows delivered WM_HOTKEY")
                controller.handle_hotkey()

            for event in input_window.drain_raw_events():
                controller.handle_raw_input(event)

        return 0

    except Exception as exc:
        logger.exception("Controller stopped due to error: %r", exc)
        write_status(
            running=False,
            state="error",
            version="3.2",
            error=str(exc),
        )
        return 1
    finally:
        if volume_hook is not None:
            volume_hook.close()
        if controller is not None:
            controller.stop()
        if input_window is not None:
            input_window.close()
        if mutex:
            kernel32.CloseHandle(mutex)
        remove_pid_file()
        write_status(running=False, state="stopped", version="3.2")


# ---------------- CLI ----------------

def main() -> int:
    parser = argparse.ArgumentParser(
        description="Samsung TV Picture Controller for Windows"
    )
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--pair", action="store_true")
    mode.add_argument("--test", action="store_true")
    mode.add_argument("--off", action="store_true")
    mode.add_argument("--wake", action="store_true")
    mode.add_argument("--check-hotkey", action="store_true")
    mode.add_argument("--self-test", action="store_true")
    args = parser.parse_args()

    if args.self_test:
        ok, message = self_test_structures()
        print(message)
        return 0 if ok else 6

    try:
        config = load_config()
    except Exception as exc:
        print(f"Configuration error: {exc}", file=sys.stderr)
        logger.exception("Configuration error")
        return 2

    if args.check_hotkey:
        return check_hotkey(config)

    client = make_tv_client(config)

    if args.pair:
        if config["control_method"] == "smartthings":
            print("Validating SmartThings cloud access...")
            print("A browser will open if SmartThings sign-in is required.")
        else:
            print(
                f"Connecting to Samsung TV at "
                f"{config['tv_ip']}:{config['port']}..."
            )
            print(
                "If the TV asks whether to allow remote control, choose Allow."
            )
        try:
            label = client.pair()
            if config["control_method"] == "smartthings":
                print(f"SmartThings cloud access succeeded: {label}")
            else:
                print(f"Pairing succeeded. Token file: {TOKEN_FILE}")
            return 0
        except Exception as exc:
            print(f"Pairing failed: {exc}", file=sys.stderr)
            logger.exception("Pairing failed")
            return 3
        finally:
            client.close()

    if args.test:
        print(
            "Sending KEY_PICTURE_OFF. "
            "The screen should go black for about 2 seconds."
        )
        if not client.send(str(config["picture_off_key"])):
            print(
                "Failed to send Picture Off. See controller.log.",
                file=sys.stderr,
            )
            client.close()
            return 4
        time.sleep(2.0)
        print(f"Sending wake key {config['wake_key']}...")
        ok = client.send(str(config["wake_key"]))
        client.close()
        return 0 if ok else 5

    if args.off:
        ok = client.send(str(config["picture_off_key"]))
        client.close()
        return 0 if ok else 4

    if args.wake:
        ok = client.send(str(config["wake_key"]))
        client.close()
        return 0 if ok else 5

    client.close()

    ok, message = self_test_structures()
    if not ok:
        logger.error(message)
        print(message, file=sys.stderr)
        return 6
    logger.info(message)

    return run_daemon(config)


if __name__ == "__main__":
    raise SystemExit(main())
