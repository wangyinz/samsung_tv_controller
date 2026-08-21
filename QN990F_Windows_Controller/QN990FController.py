#!/usr/bin/env python3
"""
QN990F Windows Picture Controller

Features:
- Global hotkey (default Ctrl+Alt+P) -> Samsung KEY_PICTURE_OFF
- When this controller blanked the TV, the next keyboard/mouse input sends a
  wake key (default KEY_RETURN).
- Optional idle blanking after N minutes of Windows keyboard/mouse inactivity.
- Optionally respects Windows ES_DISPLAY_REQUIRED power requests so media apps
  that explicitly request the display to stay awake can suppress idle blanking.
- Uses samsungtvws over Tizen WebSocket TLS port 8002 with a persistent token.

Windows only.
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
import sys
import threading
import time

try:
    from samsungtvws import SamsungTVWS
except ImportError:
    print("samsungtvws is not installed. Run Install-QN990FController.ps1 first.", file=sys.stderr)
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
    "tv_ip": "",
    "port": 8002,
    "idle_minutes": 10.0,
    "enable_idle_off": True,
    "respect_display_required": True,
    "hotkey": "Ctrl+Alt+P",
    "picture_off_key": "KEY_PICTURE_OFF",
    "wake_key": "KEY_RETURN",
    "wake_guard_ms": 700,
    "poll_interval_ms": 100,
    "socket_timeout_seconds": 5.0,
    "key_press_delay_seconds": 0.05,
}

logger = logging.getLogger("QN990FController")
logger.setLevel(logging.INFO)
if not logger.handlers:
    handler = RotatingFileHandler(LOG_FILE, maxBytes=512_000, backupCount=2, encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    logger.addHandler(handler)

# ---------- Win32 API ----------

user32 = ctypes.WinDLL("user32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
powrprof = ctypes.WinDLL("powrprof", use_last_error=True)

WM_HOTKEY = 0x0312
MOD_ALT = 0x0001
MOD_CONTROL = 0x0002
MOD_SHIFT = 0x0004
MOD_WIN = 0x0008
MOD_NOREPEAT = 0x4000
HOTKEY_ID = 0x5199
ERROR_ALREADY_EXISTS = 183
SYSTEM_EXECUTION_STATE = 16
ES_DISPLAY_REQUIRED = 0x00000002


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


user32.GetLastInputInfo.argtypes = [ctypes.POINTER(LASTINPUTINFO)]
user32.GetLastInputInfo.restype = wintypes.BOOL
kernel32.GetTickCount.argtypes = []
kernel32.GetTickCount.restype = wintypes.DWORD

user32.RegisterHotKey.argtypes = [wintypes.HWND, ctypes.c_int, wintypes.UINT, wintypes.UINT]
user32.RegisterHotKey.restype = wintypes.BOOL
user32.UnregisterHotKey.argtypes = [wintypes.HWND, ctypes.c_int]
user32.UnregisterHotKey.restype = wintypes.BOOL
user32.GetMessageW.argtypes = [ctypes.POINTER(MSG), wintypes.HWND, wintypes.UINT, wintypes.UINT]
user32.GetMessageW.restype = ctypes.c_int

kernel32.CreateMutexW.argtypes = [wintypes.LPVOID, wintypes.BOOL, wintypes.LPCWSTR]
kernel32.CreateMutexW.restype = wintypes.HANDLE
kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
kernel32.CloseHandle.restype = wintypes.BOOL

powrprof.CallNtPowerInformation.argtypes = [
    ctypes.c_int,
    wintypes.LPVOID,
    wintypes.ULONG,
    wintypes.LPVOID,
    wintypes.ULONG,
]
powrprof.CallNtPowerInformation.restype = wintypes.ULONG


def get_last_input_tick() -> int:
    info = LASTINPUTINFO()
    info.cbSize = ctypes.sizeof(info)
    if not user32.GetLastInputInfo(ctypes.byref(info)):
        raise ctypes.WinError(ctypes.get_last_error())
    return int(info.dwTime)


def get_idle_ms() -> int:
    # GetLastInputInfo and GetTickCount are DWORD values. Masking handles wraparound.
    now = int(kernel32.GetTickCount())
    return (now - get_last_input_tick()) & 0xFFFFFFFF


def display_is_explicitly_required() -> bool:
    """True when Windows has an active ES_DISPLAY_REQUIRED execution-state request."""
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


def parse_hotkey(spec: str) -> tuple[int, int]:
    """
    Parse strings like Ctrl+Alt+P, Ctrl+Shift+O, Win+Alt+B.
    Final key must be A-Z or 0-9.
    """
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

    config["tv_ip"] = str(config.get("tv_ip", "")).strip()
    if not config["tv_ip"]:
        raise ValueError("config.json has an empty tv_ip.")

    config["port"] = int(config.get("port", 8002))
    config["idle_minutes"] = float(config.get("idle_minutes", 10))
    config["enable_idle_off"] = bool(config.get("enable_idle_off", config["idle_minutes"] > 0))
    config["respect_display_required"] = bool(config.get("respect_display_required", True))
    config["wake_guard_ms"] = max(0, int(config.get("wake_guard_ms", 700)))
    config["poll_interval_ms"] = max(50, int(config.get("poll_interval_ms", 100)))
    config["socket_timeout_seconds"] = max(1.0, float(config.get("socket_timeout_seconds", 5)))
    config["key_press_delay_seconds"] = max(0.0, float(config.get("key_press_delay_seconds", 0.05)))
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


class TVClient:
    def __init__(self, config: dict):
        self.config = config
        self._tv: SamsungTVWS | None = None
        self._lock = threading.Lock()

    def _new_tv(self, pairing: bool = False) -> SamsungTVWS:
        timeout = 45.0 if pairing else float(self.config["socket_timeout_seconds"])
        return SamsungTVWS(
            host=self.config["tv_ip"],
            port=int(self.config["port"]),
            token_file=str(TOKEN_FILE),
            timeout=timeout,
            key_press_delay=float(self.config["key_press_delay_seconds"]),
            name="QN990F-PC-Controller",
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

    def send(self, key: str) -> bool:
        with self._lock:
            for attempt in range(2):
                try:
                    if self._tv is None:
                        self._tv = self._new_tv()
                    self._tv.send_key(
                        key,
                        key_press_delay=float(self.config["key_press_delay_seconds"]),
                    )
                    logger.info("Sent %s", key)
                    return True
                except Exception as exc:
                    logger.warning("Send %s failed (attempt %d): %r", key, attempt + 1, exc)
                    self._reset()
                    if attempt == 0:
                        time.sleep(0.15)
            return False

    def close(self) -> None:
        with self._lock:
            self._reset()


class Controller:
    def __init__(self, config: dict):
        self.config = config
        self.tv = TVClient(config)
        self._operation_lock = threading.Lock()
        self._state_lock = threading.Lock()
        self._stop = threading.Event()

        self.picture_off = False
        self.off_input_tick = get_last_input_tick()
        self.wake_not_before = 0.0
        self.last_wake_time = 0.0
        self.next_off_attempt = 0.0
        self.next_wake_attempt = 0.0

        self._display_required_cache = False
        self._display_required_checked_at = 0.0

    def _set_picture_off_state(self, off: bool) -> None:
        with self._state_lock:
            self.picture_off = off

    def is_picture_off(self) -> bool:
        with self._state_lock:
            return self.picture_off

    def blank(self, reason: str) -> bool:
        with self._operation_lock:
            if self.is_picture_off():
                return True

            ok = self.tv.send(str(self.config["picture_off_key"]))
            if not ok:
                self.next_off_attempt = time.monotonic() + 5.0
                write_status(running=True, state="error", last_action="blank_failed", reason=reason)
                return False

            now = time.monotonic()
            self.off_input_tick = get_last_input_tick()
            self.wake_not_before = now + (int(self.config["wake_guard_ms"]) / 1000.0)
            self._set_picture_off_state(True)
            logger.info("Picture off (%s)", reason)
            write_status(running=True, state="picture_off", last_action="blank", reason=reason)
            return True

    def wake(self, reason: str) -> bool:
        with self._operation_lock:
            if not self.is_picture_off():
                return True

            now = time.monotonic()
            if now < self.next_wake_attempt:
                return False

            ok = self.tv.send(str(self.config["wake_key"]))
            if not ok:
                self.next_wake_attempt = now + 1.0
                write_status(running=True, state="error", last_action="wake_failed", reason=reason)
                return False

            self._set_picture_off_state(False)
            self.last_wake_time = time.monotonic()
            self.next_wake_attempt = 0.0
            logger.info("Picture wake (%s)", reason)
            write_status(running=True, state="awake", last_action="wake", reason=reason)
            return True

    def handle_hotkey(self) -> None:
        # If mouse/keyboard input already made the monitor thread wake the TV just
        # before WM_HOTKEY arrives, do not immediately blank it again.
        if (time.monotonic() - self.last_wake_time) < 1.25:
            logger.info("Ignored hotkey immediately after wake")
            return

        if self.is_picture_off():
            self.wake("hotkey")
        else:
            self.blank("hotkey")

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
        idle_threshold_ms = max(0.0, float(self.config["idle_minutes"])) * 60_000.0

        while not self._stop.is_set():
            try:
                now = time.monotonic()
                current_tick = get_last_input_tick()

                if self.is_picture_off():
                    # Suppress key-up / modifier-release events from the hotkey itself.
                    if now < self.wake_not_before:
                        self.off_input_tick = current_tick
                    elif current_tick != self.off_input_tick:
                        self.off_input_tick = current_tick
                        self.wake("windows_input")
                else:
                    auto_enabled = bool(self.config["enable_idle_off"]) and idle_threshold_ms > 0
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
                                logger.debug("Idle threshold reached but ES_DISPLAY_REQUIRED is active")
            except Exception as exc:
                logger.exception("Monitor loop error: %r", exc)

            self._stop.wait(interval)

    def stop(self) -> None:
        self._stop.set()
        self.tv.close()


def acquire_single_instance() -> wintypes.HANDLE:
    handle = kernel32.CreateMutexW(None, False, "Local\\QN990FPictureController")
    if not handle:
        raise ctypes.WinError(ctypes.get_last_error())

    if ctypes.get_last_error() == ERROR_ALREADY_EXISTS:
        kernel32.CloseHandle(handle)
        raise RuntimeError("QN990F Controller is already running.")
    return handle


def register_configured_hotkey(config: dict) -> tuple[int, int]:
    modifiers, vk = parse_hotkey(str(config["hotkey"]))
    if not user32.RegisterHotKey(None, HOTKEY_ID, modifiers, vk):
        err = ctypes.get_last_error()
        raise RuntimeError(
            f"Could not register global hotkey {config['hotkey']} (Win32 error {err}). "
            "Another program may already use it."
        )
    return modifiers, vk


def check_hotkey(config: dict) -> int:
    try:
        register_configured_hotkey(config)
        print(f"Hotkey {config['hotkey']} is available.")
        return 0
    except Exception as exc:
        print(str(exc), file=sys.stderr)
        return 1
    finally:
        user32.UnregisterHotKey(None, HOTKEY_ID)


def remove_pid_file() -> None:
    try:
        if PID_FILE.exists() and PID_FILE.read_text(encoding="utf-8").strip() == str(os.getpid()):
            PID_FILE.unlink(missing_ok=True)
    except OSError:
        pass


def run_daemon(config: dict) -> int:
    mutex = None
    controller = None
    hotkey_registered = False

    try:
        mutex = acquire_single_instance()
        register_configured_hotkey(config)
        hotkey_registered = True

        PID_FILE.write_text(str(os.getpid()), encoding="utf-8")
        atexit.register(remove_pid_file)

        controller = Controller(config)
        monitor = threading.Thread(
            target=controller.monitor_loop,
            name="QN990F-IdleMonitor",
            daemon=True,
        )
        monitor.start()

        logger.info(
            "Controller started. TV=%s:%s hotkey=%s idle=%s min auto=%s",
            config["tv_ip"],
            config["port"],
            config["hotkey"],
            config["idle_minutes"],
            config["enable_idle_off"],
        )
        write_status(running=True, state="awake", hotkey=config["hotkey"])

        msg = MSG()
        while True:
            result = user32.GetMessageW(ctypes.byref(msg), None, 0, 0)
            if result == 0:
                break
            if result == -1:
                raise ctypes.WinError(ctypes.get_last_error())
            if msg.message == WM_HOTKEY and int(msg.wParam) == HOTKEY_ID:
                controller.handle_hotkey()

        return 0
    except Exception as exc:
        logger.exception("Controller stopped due to error: %r", exc)
        write_status(running=False, state="error", error=str(exc))
        # pythonw has no console, so also leave the error in status.json/log.
        return 1
    finally:
        if controller is not None:
            controller.stop()
        if hotkey_registered:
            user32.UnregisterHotKey(None, HOTKEY_ID)
        if mutex:
            kernel32.CloseHandle(mutex)
        remove_pid_file()
        write_status(running=False, state="stopped")


def main() -> int:
    parser = argparse.ArgumentParser(description="QN990F Windows Picture Controller")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--pair", action="store_true", help="Pair with TV and save token")
    mode.add_argument("--test", action="store_true", help="Blank for two seconds, then wake")
    mode.add_argument("--off", action="store_true", help="Send Picture Off once")
    mode.add_argument("--wake", action="store_true", help="Send wake key once")
    mode.add_argument("--check-hotkey", action="store_true", help="Check whether hotkey can be registered")
    args = parser.parse_args()

    try:
        config = load_config()
    except Exception as exc:
        print(f"Configuration error: {exc}", file=sys.stderr)
        logger.exception("Configuration error")
        return 2

    if args.check_hotkey:
        return check_hotkey(config)

    client = TVClient(config)

    if args.pair:
        print(f"Connecting to Samsung TV at {config['tv_ip']}:{config['port']}...")
        print("If the TV asks whether to allow remote control, choose Allow.")
        try:
            client.pair()
            print(f"Pairing succeeded. Token file: {TOKEN_FILE}")
            return 0
        except Exception as exc:
            print(f"Pairing failed: {exc}", file=sys.stderr)
            logger.exception("Pairing failed")
            return 3
        finally:
            client.close()

    if args.test:
        print("Sending KEY_PICTURE_OFF. The screen should go black for about 2 seconds.")
        if not client.send(str(config["picture_off_key"])):
            print("Failed to send Picture Off. See controller.log.", file=sys.stderr)
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
    return run_daemon(config)


if __name__ == "__main__":
    raise SystemExit(main())
