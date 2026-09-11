"""Exercise both platform volume workers without OS hooks or a real TV."""
import ast
import json
import logging
import os
from pathlib import Path
import queue
import tempfile
import threading
import time
import unittest
import uuid


ROOT = Path(__file__).parents[2]


def load_worker(platform):
    # Windows imports Win32 at module scope. Compile its actual platform-neutral
    # worker so the same behavioral cases can run on either development OS.
    path = ROOT / f"QN990F_{platform}_Controller" / "QN990FController.py"
    tree = ast.parse(path.read_text(encoding="utf-8"))
    node = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == "VolumeCoordinator")
    namespace = dict(globals(), logger=logging.getLogger("volume-menu-test"))
    exec(compile(ast.Module(body=[node], type_ignores=[]), str(path), "exec"), namespace)
    return namespace["VolumeCoordinator"]


class FakeTV:
    def __init__(self):
        self.value = 14
        self.targets = []
        self.keys = []
        self.fail = False
        self.auth_required = False

    def authorization_required(self):
        return self.auth_required

    def get_volume(self):
        return self.value

    def set_volume(self, target):
        self.targets.append(target)
        if self.fail:
            return False
        self.value = target
        return True

    def send(self, key):
        self.keys.append(key)
        return True


class VolumeMenuCases:
    platform = ""

    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.directory = Path(temporary.name)
        self.tv = FakeTV()
        self.config = {
            "tv_volume_floor": 10,
            "tv_volume_refresh_seconds": 30,
            "enable_volume_control": False,
        }
        self.worker_type = load_worker(self.platform)
        self.volume = self.worker_type(self.config, self.tv, self.directory)
        self.addCleanup(self.volume.stop)
        self.wait_for(lambda: self.status().get("value") == 14)
        self.session = self.status()["session"]

    def status(self):
        try:
            return json.loads((self.directory / "volume-status.json").read_text())
        except FileNotFoundError:
            return {}

    def wait_for(self, predicate):
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            if predicate():
                return
            time.sleep(0.01)
        self.fail("Timed out waiting for the volume worker")

    def request(self, value, **overrides):
        request = dict(session=self.session, id=uuid.uuid4().hex,
                       value=value, created_at=time.time())
        request.update(overrides)
        temporary = self.directory / "test-request.tmp"
        temporary.write_text(json.dumps(request), encoding="utf-8")
        os.replace(temporary, self.directory / "volume-request.json")
        return request["id"]

    def test_slider_sends_exact_targets_even_below_the_media_key_floor(self):
        for target in (7, 0, 100):
            request_id = self.request(target)
            self.wait_for(lambda: self.status().get("request_id") == request_id)
            self.assertEqual(self.status()["value"], target)
            self.assertEqual(self.status()["state"], "ready")
        self.assertEqual(self.tv.targets, [7, 0, 100])
        self.assertEqual(self.tv.keys, [])

    def test_latest_unsent_slider_target_replaces_buffered_keys(self):
        self.volume.BUFFER_SECONDS = 0.4
        self.assertTrue(self.volume.handle_key("up", system_is_max=True))
        self.request(40)
        self.wait_for(lambda: self.status().get("value") == 40)
        request_id = self.request(23)
        self.wait_for(lambda: self.status().get("request_id") == request_id)
        self.assertEqual(self.tv.targets, [23])
        time.sleep(0.2)
        self.assertEqual(self.tv.targets, [23], "A consumed request must not be replayed")

    def test_previous_daemon_session_is_ignored(self):
        self.request(100, session="previous-session")
        time.sleep(0.3)
        self.assertEqual(self.tv.targets, [])
        self.assertEqual(self.status()["value"], 14)

    def test_invalid_and_expired_requests_do_not_change_the_tv(self):
        cases = [
            {"value": -1}, {"value": 101}, {"value": True},
            {"value": "40"}, {"value": 1.5},
            {"value": 40, "created_at": time.time() - 61},
            {"value": 40, "created_at": time.time() + 10},
        ]
        for case in cases:
            request_id = self.request(**case)
            self.wait_for(lambda: self.status().get("request_id") == request_id)
            self.assertEqual(self.status()["state"], "error")
        self.assertEqual(self.tv.targets, [])

    def test_failed_set_is_visible_and_a_new_request_can_retry(self):
        self.tv.fail = True
        request_id = self.request(30)
        self.wait_for(lambda: self.status().get("request_id") == request_id)
        self.assertEqual(self.status()["state"], "error")
        self.assertIn("failed", self.status()["message"])
        self.tv.fail = False
        request_id = self.request(25)
        self.wait_for(lambda: self.status().get("request_id") == request_id)
        self.assertEqual(self.status()["value"], 25)
        self.assertEqual(self.status()["state"], "ready")
        self.assertEqual(self.tv.targets, [30, 25])

    def test_authorization_failure_disables_slider_without_sending(self):
        self.tv.auth_required = True
        self.request(30)
        self.wait_for(lambda: not self.status().get("available", True))
        self.assertEqual(self.status()["state"], "authorization_required")
        self.assertEqual(self.tv.targets, [])

    def test_stop_disables_slider_and_restart_rejects_existing_request(self):
        request_id = self.request(21)
        self.wait_for(lambda: self.status().get("request_id") == request_id)
        self.volume.stop()
        self.assertFalse(self.status()["available"])
        self.assertEqual(self.status()["state"], "stopped")
        restarted = self.worker_type(self.config, self.tv, self.directory)
        self.addCleanup(restarted.stop)
        self.wait_for(lambda: self.status().get("session") != self.session)
        time.sleep(0.3)
        self.assertEqual(self.tv.targets, [21])

    def test_lan_volume_is_explicitly_unavailable(self):
        self.volume.stop()

        class LAN:
            def get_volume(self):
                return None

        lan = self.worker_type(self.config, LAN(), self.directory)
        self.addCleanup(lan.stop)
        self.wait_for(lambda: self.status().get("state") == "unavailable")
        self.assertFalse(self.status()["available"])
        self.assertIn("SmartThings", self.status()["message"])


class MacVolumeMenuTests(VolumeMenuCases, unittest.TestCase):
    platform = "macOS"


class WindowsVolumeMenuTests(VolumeMenuCases, unittest.TestCase):
    platform = "Windows"
