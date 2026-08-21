#!/usr/bin/env python3
from __future__ import annotations

import argparse
import ctypes
from ctypes import c_double, c_int32, c_uint32, c_void_p, POINTER, Structure
import fcntl
import json
import logging
from logging.handlers import RotatingFileHandler
import os
from pathlib import Path
import re
import signal
import subprocess
import sys
import threading
import time

from samsungtvws import SamsungTVWS

if sys.platform != "darwin":
    raise SystemExit("This controller is for macOS only.")

APP_DIR = Path.home() / "Library" / "Application Support" / "QN990FController"
CONFIG_FILE = APP_DIR / "config.json"
TOKEN_FILE = APP_DIR / "samsung-token.txt"
LOG_FILE = APP_DIR / "controller.log"
STATUS_FILE = APP_DIR / "status.json"
LOCK_FILE = APP_DIR / "controller.lock"
APP_DIR.mkdir(parents=True, exist_ok=True)

DEFAULT_CONFIG = {
    "tv_ip": "",
    "port": 8002,
    "idle_minutes": 10.0,
    "enable_idle_off": True,
    "respect_display_required": True,
    "hotkey": "Ctrl+Cmd+P",
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
    h = RotatingFileHandler(LOG_FILE, maxBytes=512_000, backupCount=2, encoding="utf-8")
    h.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    logger.addHandler(h)


def fourcc(text: str) -> int:
    v = 0
    for ch in text.encode("ascii"):
        v = (v << 8) | ch
    return v


class EventTypeSpec(Structure):
    _fields_ = [("eventClass", c_uint32), ("eventKind", c_uint32)]


class EventHotKeyID(Structure):
    _fields_ = [("signature", c_uint32), ("id", c_uint32)]


class MacOSBackend:
    K_EVENT_CLASS_KEYBOARD = fourcc("keyb")
    K_EVENT_HOTKEY_PRESSED = 5
    CMD_KEY = 0x0100
    SHIFT_KEY = 0x0200
    OPTION_KEY = 0x0800
    CONTROL_KEY = 0x1000
    K_CG_EVENT_SOURCE_STATE_COMBINED_SESSION = 0
    K_CG_ANY_INPUT_EVENT_TYPE = 0xFFFFFFFF

    KEY_CODES = {
        "A":0x00,"S":0x01,"D":0x02,"F":0x03,"H":0x04,"G":0x05,
        "Z":0x06,"X":0x07,"C":0x08,"V":0x09,"B":0x0B,
        "Q":0x0C,"W":0x0D,"E":0x0E,"R":0x0F,"Y":0x10,"T":0x11,
        "1":0x12,"2":0x13,"3":0x14,"4":0x15,"6":0x16,"5":0x17,
        "9":0x19,"7":0x1A,"8":0x1C,"0":0x1D,
        "O":0x1F,"U":0x20,"I":0x22,"P":0x23,
        "L":0x25,"J":0x26,"K":0x28,"N":0x2D,"M":0x2E,
    }

    CALLBACK = ctypes.CFUNCTYPE(c_int32, c_void_p, c_void_p, c_void_p)

    def __init__(self):
        ht = "/System/Library/Frameworks/Carbon.framework/Frameworks/HIToolbox.framework/HIToolbox"
        cg = "/System/Library/Frameworks/CoreGraphics.framework/CoreGraphics"
        self.hitoolbox = ctypes.CDLL(ht)
        self.coregraphics = ctypes.CDLL(cg)

        self.hitoolbox.GetApplicationEventTarget.argtypes = []
        self.hitoolbox.GetApplicationEventTarget.restype = c_void_p
        self.hitoolbox.InstallEventHandler.argtypes = [
            c_void_p, self.CALLBACK, c_uint32, POINTER(EventTypeSpec),
            c_void_p, POINTER(c_void_p)
        ]
        self.hitoolbox.InstallEventHandler.restype = c_int32
        self.hitoolbox.RemoveEventHandler.argtypes = [c_void_p]
        self.hitoolbox.RemoveEventHandler.restype = c_int32
        self.hitoolbox.RegisterEventHotKey.argtypes = [
            c_uint32, c_uint32, EventHotKeyID, c_void_p, c_uint32, POINTER(c_void_p)
        ]
        self.hitoolbox.RegisterEventHotKey.restype = c_int32
        self.hitoolbox.UnregisterEventHotKey.argtypes = [c_void_p]
        self.hitoolbox.UnregisterEventHotKey.restype = c_int32
        self.hitoolbox.RunCurrentEventLoop.argtypes = [c_double]
        self.hitoolbox.RunCurrentEventLoop.restype = c_int32

        self.coregraphics.CGEventSourceSecondsSinceLastEventType.argtypes = [c_int32, c_uint32]
        self.coregraphics.CGEventSourceSecondsSinceLastEventType.restype = c_double

        self._handler_ref = c_void_p()
        self._hotkey_ref = c_void_p()
        self._callback = None
        self._hotkey_event = threading.Event()
        self._registered = False

    def idle_seconds(self) -> float:
        return max(0.0, float(self.coregraphics.CGEventSourceSecondsSinceLastEventType(
            self.K_CG_EVENT_SOURCE_STATE_COMBINED_SESSION,
            self.K_CG_ANY_INPUT_EVENT_TYPE
        )))

    def parse_hotkey(self, spec: str):
        parts = [p.strip().upper() for p in spec.split("+") if p.strip()]
        if len(parts) < 2:
            raise ValueError("Hotkey needs a modifier and an A-Z/0-9 key.")
        mmap = {
            "CTRL": self.CONTROL_KEY, "CONTROL": self.CONTROL_KEY,
            "CMD": self.CMD_KEY, "COMMAND": self.CMD_KEY,
            "SHIFT": self.SHIFT_KEY,
            "ALT": self.OPTION_KEY, "OPTION": self.OPTION_KEY, "OPT": self.OPTION_KEY,
        }
        key = parts[-1]
        if key not in self.KEY_CODES:
            raise ValueError("Hotkey final key must be A-Z or 0-9.")
        mods = 0
        for p in parts[:-1]:
            if p not in mmap:
                raise ValueError(f"Unsupported modifier: {p}")
            mods |= mmap[p]
        return self.KEY_CODES[key], mods

    def register_hotkey(self, spec: str):
        key_code, mods = self.parse_hotkey(spec)
        target = self.hitoolbox.GetApplicationEventTarget()
        if not target:
            raise RuntimeError("GetApplicationEventTarget returned NULL.")

        def cb(_next, _event, _user):
            self._hotkey_event.set()
            return 0

        self._callback = self.CALLBACK(cb)
        event_spec = EventTypeSpec(self.K_EVENT_CLASS_KEYBOARD, self.K_EVENT_HOTKEY_PRESSED)
        status = self.hitoolbox.InstallEventHandler(
            target, self._callback, 1, ctypes.byref(event_spec), None,
            ctypes.byref(self._handler_ref)
        )
        if status != 0:
            raise RuntimeError(f"InstallEventHandler failed: OSStatus {status}")

        hk_id = EventHotKeyID(fourcc("QN9F"), 1)
        status = self.hitoolbox.RegisterEventHotKey(
            key_code, mods, hk_id, target, 0, ctypes.byref(self._hotkey_ref)
        )
        if status != 0:
            self.unregister_hotkey()
            raise RuntimeError(
                f"Could not register global hotkey {spec}: OSStatus {status}. "
                "Another app/macOS may already own it."
            )
        self._registered = True

    def unregister_hotkey(self):
        if self._hotkey_ref:
            try: self.hitoolbox.UnregisterEventHotKey(self._hotkey_ref)
            except Exception: pass
        if self._handler_ref:
            try: self.hitoolbox.RemoveEventHandler(self._handler_ref)
            except Exception: pass
        self._hotkey_ref = c_void_p()
        self._handler_ref = c_void_p()
        self._callback = None
        self._registered = False

    def pump_events(self, timeout: float) -> bool:
        self.hitoolbox.RunCurrentEventLoop(float(timeout))
        if self._hotkey_event.is_set():
            self._hotkey_event.clear()
            return True
        return False


def load_config():
    cfg = DEFAULT_CONFIG.copy()
    with CONFIG_FILE.open("r", encoding="utf-8-sig") as f:
        cfg.update(json.load(f))
    cfg["tv_ip"] = str(cfg.get("tv_ip","")).strip()
    if not cfg["tv_ip"]:
        raise ValueError("config.json has an empty tv_ip.")
    cfg["port"] = int(cfg.get("port",8002))
    cfg["idle_minutes"] = float(cfg.get("idle_minutes",10))
    cfg["enable_idle_off"] = bool(cfg.get("enable_idle_off", cfg["idle_minutes"] > 0))
    cfg["respect_display_required"] = bool(cfg.get("respect_display_required",True))
    cfg["wake_guard_ms"] = max(0, int(cfg.get("wake_guard_ms",700)))
    cfg["poll_interval_ms"] = max(50, int(cfg.get("poll_interval_ms",100)))
    cfg["socket_timeout_seconds"] = max(1.0, float(cfg.get("socket_timeout_seconds",5)))
    cfg["key_press_delay_seconds"] = max(0.0, float(cfg.get("key_press_delay_seconds",0.05)))
    MacOSBackend().parse_hotkey(str(cfg.get("hotkey","Ctrl+Cmd+P")))
    return cfg


def write_status(**kwargs):
    try:
        STATUS_FILE.write_text(json.dumps({
            "pid": os.getpid(), "timestamp": time.time(), **kwargs
        }, indent=2), encoding="utf-8")
    except OSError:
        pass


def display_is_explicitly_required() -> bool:
    try:
        p = subprocess.run(
            ["/usr/bin/pmset","-g","assertions"],
            capture_output=True, text=True, timeout=3, check=False
        )
    except Exception:
        return False
    if p.returncode != 0:
        return False
    wanted = {
        "PreventUserIdleDisplaySleep",
        "NoDisplaySleepAssertion",
        "InternalPreventDisplaySleep",
    }
    in_status = False
    for line in p.stdout.splitlines():
        s = line.strip()
        if s.startswith("Assertion status system-wide:"):
            in_status = True
            continue
        if in_status and s.startswith("Listed by owning process:"):
            break
        if not in_status:
            continue
        m = re.match(r"^([A-Za-z][A-Za-z0-9]+)\s+(\d+)\s*$", s)
        if m and m.group(1) in wanted and int(m.group(2)) > 0:
            return True
    return False


class TVClient:
    def __init__(self, cfg):
        self.cfg = cfg
        self._tv = None
        self._lock = threading.Lock()

    def _new(self, pairing=False):
        return SamsungTVWS(
            host=self.cfg["tv_ip"], port=int(self.cfg["port"]),
            token_file=str(TOKEN_FILE),
            timeout=45.0 if pairing else float(self.cfg["socket_timeout_seconds"]),
            key_press_delay=float(self.cfg["key_press_delay_seconds"]),
            name="QN990F-Mac-Controller"
        )

    def pair(self):
        tv = self._new(pairing=True)
        try: tv.open()
        finally:
            try: tv.close()
            except Exception: pass

    def _reset(self):
        if self._tv:
            try: self._tv.close()
            except Exception: pass
        self._tv = None

    def send(self, key):
        with self._lock:
            for attempt in range(2):
                try:
                    if self._tv is None:
                        self._tv = self._new()
                    self._tv.send_key(
                        key, key_press_delay=float(self.cfg["key_press_delay_seconds"])
                    )
                    logger.info("Sent %s", key)
                    return True
                except Exception as exc:
                    logger.warning("Send %s failed attempt %d: %r", key, attempt+1, exc)
                    self._reset()
                    if attempt == 0: time.sleep(0.15)
            return False

    def close(self):
        with self._lock:
            self._reset()


class Controller:
    def __init__(self, cfg, backend):
        self.cfg = cfg
        self.backend = backend
        self.tv = TVClient(cfg)
        self._op_lock = threading.Lock()
        self._state_lock = threading.Lock()
        self._stop = threading.Event()
        self.picture_off = False
        self.off_last_idle = backend.idle_seconds()
        self.wake_not_before = 0.0
        self.last_wake_time = 0.0
        self.next_off_attempt = 0.0
        self.next_wake_attempt = 0.0
        self._assert_cache = False
        self._assert_checked = 0.0

    def is_off(self):
        with self._state_lock: return self.picture_off

    def set_off(self, value):
        with self._state_lock: self.picture_off = value

    def blank(self, reason):
        with self._op_lock:
            if self.is_off(): return True
            if not self.tv.send(str(self.cfg["picture_off_key"])):
                self.next_off_attempt = time.monotonic() + 5
                write_status(running=True,state="error",last_action="blank_failed",reason=reason)
                return False
            self.off_last_idle = self.backend.idle_seconds()
            self.wake_not_before = time.monotonic() + int(self.cfg["wake_guard_ms"])/1000.0
            self.set_off(True)
            write_status(running=True,state="picture_off",last_action="blank",reason=reason)
            return True

    def wake(self, reason):
        with self._op_lock:
            if not self.is_off(): return True
            now = time.monotonic()
            if now < self.next_wake_attempt: return False
            if not self.tv.send(str(self.cfg["wake_key"])):
                self.next_wake_attempt = now + 1
                return False
            self.set_off(False)
            self.last_wake_time = time.monotonic()
            self.next_wake_attempt = 0
            write_status(running=True,state="awake",last_action="wake",reason=reason)
            return True

    def hotkey(self):
        if time.monotonic() - self.last_wake_time < 1.25:
            return
        if self.is_off(): self.wake("hotkey")
        else: self.blank("hotkey")

    def display_required(self):
        now = time.monotonic()
        if now - self._assert_checked >= 5:
            self._assert_checked = now
            self._assert_cache = display_is_explicitly_required()
        return self._assert_cache

    def monitor(self):
        interval = int(self.cfg["poll_interval_ms"])/1000.0
        threshold = max(0.0,float(self.cfg["idle_minutes"]))*60.0
        while not self._stop.is_set():
            try:
                now = time.monotonic()
                idle = self.backend.idle_seconds()
                if self.is_off():
                    if now < self.wake_not_before:
                        self.off_last_idle = idle
                    elif idle + 0.05 < self.off_last_idle:
                        self.off_last_idle = idle
                        self.wake("mac_input")
                    else:
                        self.off_last_idle = idle
                else:
                    auto = bool(self.cfg["enable_idle_off"]) and threshold > 0
                    if auto and now >= self.next_off_attempt and idle >= threshold:
                        keep = bool(self.cfg["respect_display_required"]) and self.display_required()
                        if not keep:
                            self.blank("idle")
            except Exception as exc:
                logger.exception("Monitor error: %r", exc)
            self._stop.wait(interval)

    def stop(self):
        self._stop.set()
        self.tv.close()


def acquire_lock():
    h = LOCK_FILE.open("a+")
    try:
        fcntl.flock(h.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        h.close()
        raise RuntimeError("QN990F Controller is already running.")
    return h


def check_hotkey(cfg):
    b = MacOSBackend()
    try:
        b.register_hotkey(str(cfg["hotkey"]))
        b.pump_events(0.05)
        print(f"Hotkey {cfg['hotkey']} is available.")
        return 0
    except Exception as exc:
        print(exc, file=sys.stderr)
        return 1
    finally:
        b.unregister_hotkey()


def run_daemon(cfg):
    backend = MacOSBackend()
    ctrl = None
    lock = None
    stop = threading.Event()
    def stop_handler(_sig,_frame): stop.set()
    signal.signal(signal.SIGTERM, stop_handler)
    signal.signal(signal.SIGINT, stop_handler)
    try:
        lock = acquire_lock()
        backend.register_hotkey(str(cfg["hotkey"]))
        ctrl = Controller(cfg, backend)
        threading.Thread(target=ctrl.monitor, daemon=True, name="QN990F-IdleMonitor").start()
        write_status(running=True,state="awake",hotkey=cfg["hotkey"])
        while not stop.is_set():
            if backend.pump_events(0.10):
                ctrl.hotkey()
        return 0
    except Exception as exc:
        logger.exception("Controller stopped: %r", exc)
        write_status(running=False,state="error",error=str(exc))
        return 1
    finally:
        if ctrl: ctrl.stop()
        backend.unregister_hotkey()
        if lock:
            try: fcntl.flock(lock.fileno(), fcntl.LOCK_UN)
            except Exception: pass
            lock.close()
        write_status(running=False,state="stopped")


def main():
    ap = argparse.ArgumentParser()
    g = ap.add_mutually_exclusive_group()
    g.add_argument("--pair",action="store_true")
    g.add_argument("--test",action="store_true")
    g.add_argument("--off",action="store_true")
    g.add_argument("--wake",action="store_true")
    g.add_argument("--check-hotkey",action="store_true")
    g.add_argument("--idle",action="store_true")
    a = ap.parse_args()
    try:
        cfg = load_config()
    except Exception as exc:
        print(f"Configuration error: {exc}", file=sys.stderr)
        return 2

    if a.check_hotkey: return check_hotkey(cfg)
    if a.idle:
        print(f"{MacOSBackend().idle_seconds():.3f}")
        return 0

    tv = TVClient(cfg)
    if a.pair:
        print(f"Connecting to Samsung TV at {cfg['tv_ip']}:{cfg['port']}...")
        print("If the TV asks whether to allow remote control, choose Allow.")
        try:
            tv.pair()
            print(f"Pairing succeeded. Token file: {TOKEN_FILE}")
            return 0
        except Exception as exc:
            print(f"Pairing failed: {exc}", file=sys.stderr)
            return 3
        finally: tv.close()

    if a.test:
        print("Sending KEY_PICTURE_OFF; screen should go black for about 2 seconds.")
        if not tv.send(str(cfg["picture_off_key"])):
            tv.close(); return 4
        time.sleep(2)
        print(f"Sending wake key {cfg['wake_key']}...")
        ok = tv.send(str(cfg["wake_key"]))
        tv.close()
        return 0 if ok else 5

    if a.off:
        ok = tv.send(str(cfg["picture_off_key"])); tv.close()
        return 0 if ok else 4
    if a.wake:
        ok = tv.send(str(cfg["wake_key"])); tv.close()
        return 0 if ok else 5

    tv.close()
    return run_daemon(cfg)

if __name__ == "__main__":
    raise SystemExit(main())
