#!/usr/bin/env python3
"""Rebind only the configured TV's physical HDMI identity on macOS.

The display probe is the controller's read-only --hdmi-displays mode. This
helper deliberately does not import the controller or initialize a TV client.
"""

import fcntl
import json
import os
from pathlib import Path
import re
import signal
import subprocess
import sys
import tempfile
import time


APP_DIR = Path.home() / "Library" / "Application Support" / "QN990FController"
PYTHON = APP_DIR / "venv" / "bin" / "python"
CONTROLLER = APP_DIR / "QN990FController.py"
CONFIG = APP_DIR / "config.json"
PLIST = Path.home() / "Library" / "LaunchAgents" / "local.qn990f.picture-controller.plist"
LABEL = "local.qn990f.picture-controller"
PROBE_TIMEOUT = 20.0
STOP_TIMEOUT = 55.0


def run_bounded(command, timeout=20.0, check=False):
    """Bound and reap a child process and its process group."""
    child = subprocess.Popen(
        [str(part) for part in command],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        start_new_session=True,
    )
    try:
        stdout, stderr = child.communicate(timeout=timeout)
    except BaseException as exc:
        try:
            os.killpg(child.pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
        try:
            child.communicate(timeout=3)
        except subprocess.TimeoutExpired:
            try:
                os.killpg(child.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            child.communicate(timeout=3)
        if isinstance(exc, subprocess.TimeoutExpired):
            raise RuntimeError(f"Timed out: {command[0]}") from None
        raise
    if check and child.returncode:
        raise RuntimeError(stderr.strip() or f"{command[0]} exited {child.returncode}")
    return child.returncode, stdout, stderr


def display_candidates():
    _, output, _ = run_bounded(
        [PYTHON, CONTROLLER, "--hdmi-displays"], timeout=PROBE_TIMEOUT, check=True
    )
    candidates = json.loads(output)
    if not isinstance(candidates, list):
        raise RuntimeError("HDMI display probe returned invalid data")
    hashes = set()
    for item in candidates:
        if not isinstance(item, dict):
            raise RuntimeError("HDMI display probe returned invalid data")
        identity = item.get("edid_sha256")
        if not isinstance(identity, str) or not re.fullmatch(r"[0-9a-f]{64}", identity):
            raise RuntimeError("HDMI display probe returned an invalid identity")
        if identity in hashes:
            raise RuntimeError("HDMI display identity is ambiguous")
        hashes.add(identity)
    return candidates


def running_controller_pid():
    service = f"gui/{os.getuid()}/{LABEL}"
    code, output, _ = run_bounded(["/bin/launchctl", "print", service], timeout=5)
    if code:
        return None
    if not re.search(r"(?m)^\s*state = running\s*$", output):
        return None
    match = re.search(r"(?m)^\s*pid = (\d+)\s*$", output)
    if not match:
        raise RuntimeError("Running controller PID is unavailable; no configuration was changed")
    pid = int(match.group(1))
    code, command, _ = run_bounded(["/bin/ps", "-p", str(pid), "-o", "command="], timeout=5)
    if code or str(CONTROLLER) not in command or str(PYTHON) not in command:
        raise RuntimeError("Controller process identity did not match; no configuration was changed")
    return pid


def running_state():
    return running_controller_pid() is not None


def service_loaded():
    code, _, _ = run_bounded(
        ["/bin/launchctl", "print", f"gui/{os.getuid()}/{LABEL}"], timeout=5
    )
    return code == 0


def stop_loaded_agent():
    """Allow the daemon to release its OAuth and controller locks before bootout."""
    pid = running_controller_pid()
    if pid is not None:
        try:
            os.kill(pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
    wait_for_controller_exit()
    if service_loaded():
        run_bounded(
            ["/bin/launchctl", "bootout", f"gui/{os.getuid()}/{LABEL}"],
            timeout=10, check=True,
        )


def wait_for_controller_exit():
    # The daemon holds this lock for its full lifetime, including cleanup.
    with (APP_DIR / "controller.lock").open("a+") as daemon_lock:
        deadline = time.monotonic() + STOP_TIMEOUT
        while time.monotonic() < deadline:
            try:
                fcntl.flock(daemon_lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                fcntl.flock(daemon_lock, fcntl.LOCK_UN)
                return
            except BlockingIOError:
                time.sleep(0.1)
    raise RuntimeError("Controller did not stop cleanly; configuration was not changed")


def atomic_config(content):
    fd, temporary = tempfile.mkstemp(prefix="config.rebind.", dir=APP_DIR)
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "wb") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, CONFIG)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def bootstrap_running_agent():
    domain = f"gui/{os.getuid()}"
    guard = APP_DIR / "hdmi-rebind-no-idle-off"
    fd = os.open(guard, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    os.close(fd)
    try:
        if not service_loaded():
            run_bounded(["/bin/launchctl", "bootstrap", domain, PLIST], timeout=10, check=True)
        run_bounded(["/bin/launchctl", "kickstart", f"{domain}/{LABEL}"], timeout=10, check=True)
    except Exception:
        # If no daemon started, this marker belongs to this failed attempt.
        # A running daemon consumes it before considering idle Picture Off.
        if not running_state():
            guard.unlink(missing_ok=True)
        raise
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        if running_state():
            return
        time.sleep(0.2)
    raise RuntimeError("Controller did not restart after HDMI rebinding")


def rebind(identity):
    """Commit the selected identity only if a fresh probe still sees it."""
    if not PLIST.is_file():
        raise RuntimeError("The controller LaunchAgent is missing. Rerun INSTALL.command")
    with (APP_DIR / "hdmi-rebind.lock").open("a+") as flow_lock:
        try:
            fcntl.flock(flow_lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise RuntimeError("Another HDMI rebinding operation is already open") from None
        if identity not in {item["edid_sha256"] for item in display_candidates()}:
            raise RuntimeError("The selected HDMI TV is no longer connected")
        previous = CONFIG.read_bytes()
        settings = json.loads(previous.decode("utf-8-sig"))
        if not isinstance(settings, dict):
            raise RuntimeError("Configuration is invalid")
        if settings.get("tv_hdmi_edid_sha256") == identity:
            print("This HDMI TV is already bound. Nothing was changed.")
            return
        settings["tv_hdmi_edid_sha256"] = identity
        updated = (json.dumps(settings, indent=2, ensure_ascii=False) + "\n").encode("utf-8")
        was_running = running_state()
        stopped = False
        committed = False
        try:
            if was_running:
                # A failure after SIGTERM still requires recovery, so record
                # the stopped state as soon as the daemon lock is released.
                pid = running_controller_pid()
                if pid is not None:
                    try:
                        os.kill(pid, signal.SIGTERM)
                    except ProcessLookupError:
                        pass
                wait_for_controller_exit()
                stopped = True
                if service_loaded():
                    run_bounded(
                        ["/bin/launchctl", "bootout", f"gui/{os.getuid()}/{LABEL}"],
                        timeout=10, check=True,
                    )
            else:
                wait_for_controller_exit()
            # The TV may have been unplugged while the daemon was stopping.
            if identity not in {item["edid_sha256"] for item in display_candidates()}:
                raise RuntimeError("The selected HDMI TV disconnected before saving")
            if CONFIG.read_bytes() != previous:
                raise RuntimeError("Configuration changed during HDMI rebinding; nothing was overwritten")
            atomic_config(updated)
            committed = True
            if was_running:
                bootstrap_running_agent()
        except BaseException:
            if stopped and committed:
                # A failed restart may leave the new daemon active. Give it
                # the same graceful shutdown window before restoring config.
                stop_loaded_agent()
            if committed:
                atomic_config(previous)
            if stopped:
                try:
                    bootstrap_running_agent()
                except Exception as exc:
                    print(f"Could not restore the controller startup: {exc}", file=sys.stderr)
            raise
        print("HDMI TV binding updated. All other settings and authorization are preserved.")
        print("Controller restarted." if was_running else "Controller remains stopped.")


def main():
    if not PYTHON.is_file() or not CONTROLLER.is_file() or not CONFIG.is_file():
        print("Controller installation is incomplete. Rerun INSTALL.command.", file=sys.stderr)
        return 1
    try:
        candidates = display_candidates()
        if not candidates:
            raise RuntimeError("No uniquely identifiable Samsung HDMI TV is connected")
        print("Samsung TV Picture Controller - Rebind HDMI TV")
        print("Connect this Mac by HDMI to the exact TV configured for this controller.")
        for index, item in enumerate(candidates, 1):
            print(f"  {index}. Samsung HDMI display product={item['product']} "
                  f"serial={item['serial']} EDID={item['edid_sha256'][:12]}")
        choice = input("Select the HDMI TV to bind (blank cancels): ").strip()
        if not choice:
            print("Canceled; nothing was changed.")
            return 0
        if not choice.isascii() or not choice.isdecimal() or not 1 <= int(choice) <= len(candidates):
            raise RuntimeError("Invalid HDMI TV selection")
        selected = candidates[int(choice) - 1]
        confirmation = input("Confirm this is the exact TV controlled by this app? [y/N]: ").strip()
        if confirmation.lower() != "y":
            print("Canceled; nothing was changed.")
            return 0
        rebind(selected["edid_sha256"])
        return 0
    except (OSError, ValueError, RuntimeError, EOFError) as exc:
        print(f"HDMI rebinding failed: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
