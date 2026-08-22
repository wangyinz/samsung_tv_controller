#!/usr/bin/env python3
from __future__ import annotations

import argparse
import ctypes
from ctypes import c_double, c_int32, c_uint8, c_uint32, c_ulong, c_void_p, POINTER, Structure
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
LOG_MAX_BYTES = 1_000_000
LOG_BACKUP_COUNT = 3
APP_DIR.mkdir(parents=True, exist_ok=True)

DEFAULT_CONFIG = {
    "control_method": "lan",
    "tv_ip": "",
    "port": 8002,
    "idle_minutes": 10.0,
    "enable_idle_off": True,
    "respect_display_required": True,
    "hotkey": "Ctrl+Cmd+P",
    "picture_off_key": "KEY_PICTURE_OFF",
    "wake_key": "KEY_RETURN",
    "wake_guard_ms": 800,
    "poll_interval_ms": 50,
    "input_wake_debounce_ms": 180,
    "enable_mouse_move_wake": False,
    "mouse_wake_threshold_counts": 24,
    "mouse_motion_window_ms": 500,
    "socket_timeout_seconds": 5.0,
    "key_press_delay_seconds": 0.05,
    "remote_name": "Samsung-TV-Picture-Controller",
    "smartthings_cli": str(APP_DIR / "smartthings"),
    "smartthings_no_browser_dir": str(APP_DIR / "noninteractive-bin"),
    "smartthings_profile": "local.qn990f.picture-controller",
    "smartthings_device_id": "",
    "smartthings_command_timeout_seconds": 20.0,
}

logger = logging.getLogger("QN990FController")
logger.setLevel(logging.INFO)
if not logger.handlers:
    h = RotatingFileHandler(
        LOG_FILE,
        maxBytes=LOG_MAX_BYTES,
        backupCount=LOG_BACKUP_COUNT,
        encoding="utf-8",
    )
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


class CGPoint(Structure):
    _fields_ = [("x", c_double), ("y", c_double)]


class MacOSBackend:
    K_EVENT_CLASS_KEYBOARD = fourcc("keyb")
    K_EVENT_HOTKEY_PRESSED = 5
    CMD_KEY = 0x0100
    SHIFT_KEY = 0x0200
    OPTION_KEY = 0x0800
    CONTROL_KEY = 0x1000
    K_CG_EVENT_SOURCE_STATE_COMBINED_SESSION = 0
    K_CG_EVENT_SOURCE_STATE_HID_SYSTEM = 1
    K_CG_ANY_INPUT_EVENT_TYPE = 0xFFFFFFFF
    K_CG_EVENT_LEFT_MOUSE_DOWN = 1
    K_CG_EVENT_RIGHT_MOUSE_DOWN = 3
    K_CG_EVENT_MOUSE_MOVED = 5
    K_CG_EVENT_KEY_DOWN = 10
    K_CG_EVENT_SCROLL_WHEEL = 22
    K_CG_EVENT_OTHER_MOUSE_DOWN = 25

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
        cf = "/System/Library/Frameworks/CoreFoundation.framework/CoreFoundation"
        self.hitoolbox = ctypes.CDLL(ht)
        self.coregraphics = ctypes.CDLL(cg)
        self.corefoundation = ctypes.CDLL(cf)

        self.hitoolbox.GetApplicationEventTarget.argtypes = []
        self.hitoolbox.GetApplicationEventTarget.restype = c_void_p
        self.hitoolbox.GetEventDispatcherTarget.argtypes = []
        self.hitoolbox.GetEventDispatcherTarget.restype = c_void_p
        self.hitoolbox.InstallEventHandler.argtypes = [
            c_void_p, self.CALLBACK, c_ulong, POINTER(EventTypeSpec),
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
        self.hitoolbox.ReceiveNextEvent.argtypes = [
            c_ulong, POINTER(EventTypeSpec), c_double, c_uint8, POINTER(c_void_p)
        ]
        self.hitoolbox.ReceiveNextEvent.restype = c_int32
        self.hitoolbox.SendEventToEventTarget.argtypes = [c_void_p, c_void_p]
        self.hitoolbox.SendEventToEventTarget.restype = c_int32
        self.hitoolbox.ReleaseEvent.argtypes = [c_void_p]
        self.hitoolbox.ReleaseEvent.restype = None

        self.coregraphics.CGEventSourceSecondsSinceLastEventType.argtypes = [c_int32, c_uint32]
        self.coregraphics.CGEventSourceSecondsSinceLastEventType.restype = c_double
        self.coregraphics.CGEventSourceCounterForEventType.argtypes = [c_int32, c_uint32]
        self.coregraphics.CGEventSourceCounterForEventType.restype = c_uint32
        self.coregraphics.CGEventCreate.argtypes = [c_void_p]
        self.coregraphics.CGEventCreate.restype = c_void_p
        self.coregraphics.CGEventGetLocation.argtypes = [c_void_p]
        self.coregraphics.CGEventGetLocation.restype = CGPoint
        self.corefoundation.CFRelease.argtypes = [c_void_p]
        self.corefoundation.CFRelease.restype = None

        self._handler_ref = c_void_p()
        self._hotkey_ref = c_void_p()
        self._callback = None
        self._hotkey_event = threading.Event()
        self._registered = False
        self._dispatcher_target = c_void_p()
        self._input_poll_lock = threading.Lock()
        self._last_input_snapshot = None

    def idle_seconds(self) -> float:
        return max(0.0, float(self.coregraphics.CGEventSourceSecondsSinceLastEventType(
            self.K_CG_EVENT_SOURCE_STATE_COMBINED_SESSION,
            self.K_CG_ANY_INPUT_EVENT_TYPE
        )))

    def _input_snapshot(self):
        counter = self.coregraphics.CGEventSourceCounterForEventType
        state = self.K_CG_EVENT_SOURCE_STATE_HID_SYSTEM
        event = self.coregraphics.CGEventCreate(None)
        if not event:
            raise RuntimeError("CGEventCreate returned NULL.")
        try:
            point = self.coregraphics.CGEventGetLocation(event)
        finally:
            self.corefoundation.CFRelease(event)
        return {
            "keyboard": int(counter(state, self.K_CG_EVENT_KEY_DOWN)),
            "mouse_button": (
                int(counter(state, self.K_CG_EVENT_LEFT_MOUSE_DOWN)),
                int(counter(state, self.K_CG_EVENT_RIGHT_MOUSE_DOWN)),
                int(counter(state, self.K_CG_EVENT_OTHER_MOUSE_DOWN)),
            ),
            "mouse_wheel": int(counter(state, self.K_CG_EVENT_SCROLL_WHEEL)),
            "mouse_move": int(counter(state, self.K_CG_EVENT_MOUSE_MOVED)),
            "cursor": (float(point.x), float(point.y)),
        }

    def reset_input_baseline(self):
        with self._input_poll_lock:
            self._last_input_snapshot = self._input_snapshot()

    def poll_input_events(self):
        with self._input_poll_lock:
            current = self._input_snapshot()
            previous = self._last_input_snapshot
            self._last_input_snapshot = current

        if previous is None:
            return []

        events = []
        if current["keyboard"] != previous["keyboard"]:
            events.append({"kind": "keyboard"})
        if current["mouse_button"] != previous["mouse_button"]:
            events.append({"kind": "mouse_button"})
        if current["mouse_wheel"] != previous["mouse_wheel"]:
            events.append({"kind": "mouse_wheel"})
        if current["mouse_move"] != previous["mouse_move"]:
            old_x, old_y = previous["cursor"]
            new_x, new_y = current["cursor"]
            dx = new_x - old_x
            dy = new_y - old_y
            if dx != 0.0 or dy != 0.0:
                events.append({"kind": "mouse_move", "dx": dx, "dy": dy})
        return events

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
        self._dispatcher_target = self.hitoolbox.GetEventDispatcherTarget()
        if not self._dispatcher_target:
            raise RuntimeError("GetEventDispatcherTarget returned NULL.")

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
        self._dispatcher_target = c_void_p()

    def pump_events(self, timeout: float) -> bool:
        wait = max(0.0, float(timeout))
        while True:
            event = c_void_p()
            status = self.hitoolbox.ReceiveNextEvent(
                0, None, wait, 1, ctypes.byref(event)
            )
            if status == -9875:
                break
            if status != 0:
                raise RuntimeError(f"ReceiveNextEvent failed: OSStatus {status}")
            try:
                self.hitoolbox.SendEventToEventTarget(event, self._dispatcher_target)
            finally:
                self.hitoolbox.ReleaseEvent(event)
            wait = 0.0
        if self._hotkey_event.is_set():
            self._hotkey_event.clear()
            return True
        return False


def load_config():
    cfg = DEFAULT_CONFIG.copy()
    with CONFIG_FILE.open("r", encoding="utf-8-sig") as f:
        user_cfg = json.load(f)
    cfg.update(user_cfg)
    if "remote_name" not in user_cfg:
        cfg["remote_name"] = "QN990F-Mac-Controller"
    cfg["control_method"] = str(cfg.get("control_method", "lan")).strip().lower()
    if cfg["control_method"] not in {"lan", "smartthings"}:
        raise ValueError("control_method must be 'lan' or 'smartthings'.")
    cfg["tv_ip"] = str(cfg.get("tv_ip","")).strip()
    if cfg["control_method"] == "lan" and not cfg["tv_ip"]:
        raise ValueError("config.json has an empty tv_ip.")
    cfg["port"] = int(cfg.get("port",8002))
    cfg["idle_minutes"] = float(cfg.get("idle_minutes",10))
    cfg["enable_idle_off"] = bool(cfg.get("enable_idle_off", cfg["idle_minutes"] > 0))
    cfg["respect_display_required"] = bool(cfg.get("respect_display_required",True))
    cfg["wake_guard_ms"] = max(0, int(cfg.get("wake_guard_ms",800)))
    cfg["poll_interval_ms"] = max(25, int(cfg.get("poll_interval_ms",50)))
    cfg["input_wake_debounce_ms"] = max(
        50, int(cfg.get("input_wake_debounce_ms",180))
    )
    cfg["enable_mouse_move_wake"] = bool(
        cfg.get("enable_mouse_move_wake", False)
    )
    cfg["mouse_wake_threshold_counts"] = max(
        0, int(cfg.get("mouse_wake_threshold_counts",24))
    )
    cfg["mouse_motion_window_ms"] = max(
        100, int(cfg.get("mouse_motion_window_ms",500))
    )
    cfg["socket_timeout_seconds"] = max(1.0, float(cfg.get("socket_timeout_seconds",5)))
    cfg["key_press_delay_seconds"] = max(0.0, float(cfg.get("key_press_delay_seconds",0.05)))
    cfg["remote_name"] = str(cfg.get("remote_name", "")).strip()
    if not cfg["remote_name"]:
        raise ValueError("remote_name cannot be empty.")
    if cfg["control_method"] == "smartthings":
        cfg["smartthings_cli"] = str(cfg.get("smartthings_cli", "")).strip()
        cfg["smartthings_no_browser_dir"] = str(
            cfg.get("smartthings_no_browser_dir", APP_DIR / "noninteractive-bin")
        ).strip()
        cfg["smartthings_profile"] = str(
            cfg.get("smartthings_profile", "local.qn990f.picture-controller")
        ).strip()
        cfg["smartthings_device_id"] = str(cfg.get("smartthings_device_id", "")).strip()
        if not cfg["smartthings_cli"]:
            raise ValueError("smartthings_cli is empty.")
        if not cfg["smartthings_no_browser_dir"]:
            raise ValueError("smartthings_no_browser_dir is empty.")
        if not re.fullmatch(r"[A-Za-z0-9._-]{1,64}", cfg["smartthings_profile"]):
            raise ValueError("smartthings_profile contains unsupported characters.")
        if not re.fullmatch(
            r"[0-9A-Fa-f]{8}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}-"
            r"[0-9A-Fa-f]{4}-[0-9A-Fa-f]{12}",
            cfg["smartthings_device_id"],
        ):
            raise ValueError("smartthings_device_id is not a UUID.")
        cfg["smartthings_command_timeout_seconds"] = min(
            20.0,
            max(5.0, float(cfg.get("smartthings_command_timeout_seconds", 20.0))),
        )
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


def windowserver_activity_is_hardware(output: str) -> bool:
    pattern = re.compile(
        r'^\s*pid\s+\d+\(WindowServer\):.*?'
        r'(\d+):([0-5]\d):([0-5]\d)\s+UserIsActive named:\s*"([^"]+)"'
    )
    latest_age = None
    latest_details = []
    for line in output.splitlines():
        match = pattern.match(line)
        if match:
            age = int(match.group(1)) * 3600 + int(match.group(2)) * 60 + \
                int(match.group(3))
            if latest_age is None or age < latest_age:
                latest_age = age
                latest_details = [match.group(4)]
            elif age == latest_age:
                latest_details.append(match.group(4))
    if latest_age is None or latest_age > 2:
        return False
    return all(
        "serviceID:" in detail and "eventType:" in detail and
        " process:" not in detail
        for detail in latest_details
    )


def latest_pointer_activity_is_hardware() -> bool:
    try:
        completed = subprocess.run(
            ["/usr/bin/pmset", "-g", "assertions"],
            capture_output=True, text=True, timeout=1, check=False,
        )
    except Exception:
        return False
    return completed.returncode == 0 and \
        windowserver_activity_is_hardware(completed.stdout)


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
            name=str(self.cfg["remote_name"])
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


class SmartThingsTVClient:
    # Samsung's TV web plugin maps the Accessibility Mode button to
    # KEY_PICTURE_OFF/Click, then maps Click to this OCF pressAndRelease write.
    EXECUTE_CAPABILITY = "execute"
    REMOTE_MARKER_CAPABILITY = "samsungvd.remoteControl"
    REMOTE_RESOURCE = "/sec/tv/remotecontrol"

    def __init__(self, cfg):
        self.cfg = cfg
        self._lock = threading.Lock()
        self._command_times = []

    def _run(self, *args, timeout=None, allow_login=False):
        cli = Path(str(self.cfg["smartthings_cli"])).expanduser()
        if not cli.is_file() or not os.access(cli, os.X_OK):
            raise RuntimeError(f"SmartThings CLI is missing or not executable: {cli}")
        command = [
            str(cli), *args,
            "--profile", str(self.cfg["smartthings_profile"]),
            "--token", "",
            "--language", "NONE",
        ]
        environment = os.environ.copy()
        environment.pop("SMARTTHINGS_TOKEN", None)
        if not allow_login:
            no_browser_dir = Path(
                str(self.cfg["smartthings_no_browser_dir"])
            ).expanduser()
            if not no_browser_dir.is_dir():
                raise RuntimeError(
                    f"SmartThings noninteractive directory is missing: {no_browser_dir}"
                )
            # SmartThings CLI falls back to interactive OAuth by spawning the
            # macOS `open` executable. An intentionally empty PATH makes that
            # fallback fail immediately while normal token refresh stays usable.
            environment["PATH"] = str(no_browser_dir)
        command_timeout = (
            float(self.cfg["smartthings_command_timeout_seconds"])
            if timeout is None else float(timeout)
        )
        try:
            result = subprocess.run(
                command,
                capture_output=True,
                text=True,
                env=environment,
                timeout=command_timeout,
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            raise RuntimeError(
                "SmartThings command timed out. Run Configure.command to sign in again."
            ) from exc
        if result.returncode != 0:
            detail = (result.stderr or result.stdout).strip()
            if len(detail) > 1200:
                detail = detail[-1200:]
            if "spawn open ENOENT" in detail:
                raise RuntimeError(
                    "SmartThings authorization requires user interaction; "
                    "run Configure.command."
                )
            raise RuntimeError(detail or f"SmartThings CLI exited with {result.returncode}")
        return result.stdout

    def pair(self):
        output = self._run(
            "devices", str(self.cfg["smartthings_device_id"]), "--json",
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
                if isinstance(capability, str):
                    capabilities.add(capability)
                elif isinstance(capability, dict):
                    capabilities.add(str(capability.get("id", "")))
            for category in component.get("categories", []):
                if isinstance(category, str):
                    categories.add(category)
                elif isinstance(category, dict):
                    categories.add(str(category.get("name", "")))
        required_capabilities = {
            self.EXECUTE_CAPABILITY,
            self.REMOTE_MARKER_CAPABILITY,
        }
        if not required_capabilities.issubset(capabilities):
            raise RuntimeError(
                "Selected device does not expose SmartThings TV remote control."
            )
        if "Television" not in categories:
            raise RuntimeError("Selected SmartThings device is not a television.")
        if device.get("manufacturerName") != "Samsung Electronics" or \
           device.get("type") != "OCF":
            raise RuntimeError("Selected SmartThings device is not a Samsung OCF TV.")
        return str(device.get("label") or device.get("name") or "Samsung TV")

    def _ensure_command_budget(self, needed):
        cutoff = time.monotonic() - 60.0
        self._command_times = [stamp for stamp in self._command_times if stamp > cutoff]
        if len(self._command_times) + needed > 12:
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
            "devices:commands", str(self.cfg["smartthings_device_id"]), argument
        )
        logger.info("SmartThings sent OCF remote key %s", remote_key)

    def send(self, key):
        with self._lock:
            try:
                if key == str(self.cfg["picture_off_key"]):
                    self._ensure_command_budget(1)
                    self._send_ocf_remote(key)
                elif key == str(self.cfg["wake_key"]):
                    self._ensure_command_budget(1)
                    self._send_ocf_remote(key)
                else:
                    raise ValueError(f"Unsupported SmartThings action: {key}")
                return True
            except Exception as exc:
                logger.warning("SmartThings action %s failed: %r", key, exc)
                return False

    def close(self):
        pass


def make_tv_client(cfg):
    if cfg["control_method"] == "smartthings":
        return SmartThingsTVClient(cfg)
    return TVClient(cfg)


class Controller:
    def __init__(self, cfg, backend):
        self.cfg = cfg
        self.backend = backend
        self.tv = make_tv_client(cfg)
        self._op_lock = threading.Lock()
        self._state_lock = threading.Lock()
        self._input_lock = threading.Lock()
        self._stop = threading.Event()
        self.picture_off = False
        self.wake_not_before = 0.0
        self.next_off_attempt = 0.0
        self.next_wake_attempt = 0.0
        self.hotkey_input_suppress_until = 0.0
        self.pending_input_wake_at = 0.0
        self.pending_input_source = ""
        self.mouse_motion_last_time = 0.0
        self.mouse_motion_total = 0.0
        self._assert_cache = False
        self._assert_checked = 0.0
        self.backend.reset_input_baseline()

    def is_off(self):
        with self._state_lock: return self.picture_off

    def set_off(self, value):
        with self._state_lock: self.picture_off = value

    def _clear_pending_wake(self):
        with self._input_lock:
            self.pending_input_wake_at = 0.0
            self.pending_input_source = ""
            self.mouse_motion_last_time = 0.0
            self.mouse_motion_total = 0.0

    def blank(self, reason, force=False):
        with self._op_lock:
            if self.is_off() and not force:
                return True
            if not self.tv.send(str(self.cfg["picture_off_key"])):
                if self.cfg["control_method"] == "lan":
                    self.next_off_attempt = time.monotonic() + 5
                else:
                    self.next_off_attempt = float("inf")
                write_status(running=True,state="error",last_action="blank_failed",reason=reason)
                return False

            self.backend.reset_input_baseline()
            self.wake_not_before = (
                time.monotonic() + int(self.cfg["wake_guard_ms"]) / 1000.0
            )
            self._clear_pending_wake()
            self.set_off(True)
            self.next_off_attempt = 0.0
            logger.info("Picture off (%s)", reason)
            write_status(running=True,state="picture_off",last_action="blank",reason=reason)
            return True

    def wake(self, reason, source=""):
        with self._op_lock:
            if not self.is_off(): return True
            now = time.monotonic()
            if reason == "input" and now < self.wake_not_before:
                logger.info("Input wake discarded by Picture Off guard source=%s", source)
                return False
            if now < self.next_wake_attempt: return False
            if not self.tv.send(str(self.cfg["wake_key"])):
                self.next_wake_attempt = now + 1
                write_status(running=True,state="error",last_action="wake_failed",reason=reason)
                return False
            self.set_off(False)
            self._clear_pending_wake()
            self.next_wake_attempt = 0
            if source:
                logger.info("Picture wake (%s) source=%s", reason, source)
            else:
                logger.info("Picture wake (%s)", reason)
            write_status(
                running=True, state="awake", last_action="wake",
                reason=reason, source=source,
            )
            return True

    def handle_hotkey(self):
        now = time.monotonic()
        with self._input_lock:
            self.hotkey_input_suppress_until = now + 0.60
            self.pending_input_wake_at = 0.0
            self.pending_input_source = ""
            self.mouse_motion_last_time = 0.0
            self.mouse_motion_total = 0.0
        logger.info("Global hotkey action; local_picture_off=%s", self.is_off())
        self.blank("hotkey", force=True)

    def _schedule_input_wake(self, source, delay_ms=None):
        now = time.monotonic()
        delay = (
            int(self.cfg["input_wake_debounce_ms"])
            if delay_ms is None else max(0, int(delay_ms))
        ) / 1000.0
        with self._input_lock:
            if now < self.hotkey_input_suppress_until:
                logger.info("Input wake suppressed by hotkey source=%s", source)
                return
            due = now + delay
            pending_is_mouse = self.pending_input_source.startswith("mouse_move:")
            source_is_mouse = source.startswith("mouse_move:")
            replace = self.pending_input_wake_at <= 0.0
            if not replace and pending_is_mouse != source_is_mouse:
                replace = pending_is_mouse and not source_is_mouse
            elif not replace and pending_is_mouse == source_is_mouse:
                replace = due < self.pending_input_wake_at
            if replace:
                self.pending_input_wake_at = due
                self.pending_input_source = source
                logger.info("Input wake scheduled in %d ms source=%s", int(delay * 1000), source)

    def _qualify_input(self, event, now):
        kind = event.get("kind")
        if kind == "keyboard":
            return "keyboard:key_down"
        if kind == "mouse_button":
            return "mouse_button"
        if kind == "mouse_wheel":
            return "mouse_wheel"
        if kind != "mouse_move":
            return ""
        if not self.cfg["enable_mouse_move_wake"]:
            return ""

        dx = float(event.get("dx", 0.0))
        dy = float(event.get("dy", 0.0))
        amount = abs(dx) + abs(dy)
        if amount <= 0.0:
            return ""

        threshold = int(self.cfg["mouse_wake_threshold_counts"])
        if threshold <= 0:
            return f"mouse_move:dx={dx:g},dy={dy:g},sum={amount:g}"

        window = int(self.cfg["mouse_motion_window_ms"]) / 1000.0
        with self._input_lock:
            if (now - self.mouse_motion_last_time) <= window:
                total = self.mouse_motion_total + amount
            else:
                total = amount
            self.mouse_motion_last_time = now
            self.mouse_motion_total = total
            if total < threshold:
                return ""
            self.mouse_motion_last_time = 0.0
            self.mouse_motion_total = 0.0

        return (
            f"mouse_move:threshold={threshold},sum={total:g},"
            f"dx={dx:g},dy={dy:g}"
        )

    def handle_input(self, event):
        now = time.monotonic()
        off = self.is_off()
        if off and now < self.wake_not_before:
            return

        source = self._qualify_input(event, now)
        if not source:
            return

        if not off:
            if self.cfg["control_method"] == "smartthings" and \
               self.next_off_attempt == float("inf"):
                self.next_off_attempt = 0.0
                logger.info("SmartThings Picture Off retry rearmed by input source=%s", source)
            return

        delay_ms = 80 if event.get("kind") in {"mouse_button", "mouse_wheel"} else None
        self._schedule_input_wake(source, delay_ms=delay_ms)

    def _process_pending_wake(self, now):
        due = 0.0
        source = ""
        suppressed = False
        with self._input_lock:
            if self.pending_input_wake_at > 0.0 and now >= self.pending_input_wake_at:
                if now < self.hotkey_input_suppress_until:
                    suppressed = True
                else:
                    due = self.pending_input_wake_at
                    source = self.pending_input_source
                self.pending_input_wake_at = 0.0
                self.pending_input_source = ""
        if suppressed:
            logger.info("Pending input wake cancelled by hotkey")
        elif due > 0.0:
            if source.startswith("mouse_move:") and \
               not latest_pointer_activity_is_hardware():
                logger.info("Ignored unverified/non-hardware pointer wake source=%s", source)
            else:
                self.wake("input", source=source)

    def display_required(self):
        now = time.monotonic()
        if now - self._assert_checked >= 2:
            self._assert_checked = now
            self._assert_cache = display_is_explicitly_required()
        return self._assert_cache

    def monitor(self):
        interval = int(self.cfg["poll_interval_ms"])/1000.0
        threshold = max(0.0,float(self.cfg["idle_minutes"]))*60.0
        while not self._stop.is_set():
            try:
                now = time.monotonic()
                for event in self.backend.poll_input_events():
                    self.handle_input(event)
                if self.is_off():
                    self._process_pending_wake(now)
                else:
                    auto = bool(self.cfg["enable_idle_off"]) and threshold > 0
                    if auto and now >= self.next_off_attempt and \
                       self.backend.idle_seconds() >= threshold:
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
        raise RuntimeError("Samsung TV Picture Controller is already running.")
    return h


def check_hotkey(cfg):
    b = MacOSBackend()
    try:
        b.register_hotkey(str(cfg["hotkey"]))
        b.pump_events(0.05)
        print(f"Hotkey {cfg['hotkey']} registration succeeded.")
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
        write_status(
            running=True, state="awake", hotkey=cfg["hotkey"],
            control_method=cfg["control_method"],
        )
        while not stop.is_set():
            if backend.pump_events(0.10):
                logger.info("Global hotkey received")
                ctrl.handle_hotkey()
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

    tv = make_tv_client(cfg)
    if a.pair:
        if cfg["control_method"] == "smartthings":
            print("Validating SmartThings cloud access...")
            print("A browser will open if SmartThings sign-in is required.")
        else:
            print(f"Connecting to Samsung TV at {cfg['tv_ip']}:{cfg['port']}...")
            print("If the TV asks whether to allow remote control, choose Allow.")
        try:
            label = tv.pair()
            if cfg["control_method"] == "smartthings":
                print(f"SmartThings cloud access succeeded: {label}")
            else:
                print(f"Pairing succeeded. Token file: {TOKEN_FILE}")
            return 0
        except Exception as exc:
            action = "SmartThings validation" if cfg["control_method"] == "smartthings" else "Pairing"
            print(f"{action} failed: {exc}", file=sys.stderr)
            return 3
        finally: tv.close()

    if a.test:
        if cfg["control_method"] == "smartthings":
            print("Sending the SmartThings Accessibility Picture Off key.")
        else:
            print("Sending KEY_PICTURE_OFF.")
        print("The screen should go black for about 2 seconds.")
        if not tv.send(str(cfg["picture_off_key"])):
            tv.close(); return 4
        time.sleep(2)
        if cfg["control_method"] == "smartthings":
            print(f"Sending SmartThings wake key {cfg['wake_key']}...")
        else:
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
