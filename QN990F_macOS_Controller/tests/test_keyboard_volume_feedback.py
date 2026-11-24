"""Exercise the production workers and SmartThings command payloads without a TV."""
import contextlib
from datetime import datetime, timezone
import importlib.util
import json
import logging
from pathlib import Path
import sys
import tempfile
import threading
import time
import unittest
from unittest import mock
from urllib import parse

from test_volume_menu import ROOT, load_definition, load_worker


class Transport:
    def __init__(self):
        self.actual = 10
        self.report = {"value": 17, "timestamp": "2020-11-03T23:26:24Z"}
        self.posts = []
        self.refresh_updates = True

    def __call__(self, *, api_request, timeout=None):
        method, path, payload = api_request
        if method == "GET":
            if path.endswith("/status"):
                return json.dumps({"volume": self.report})
            return json.dumps({"components": [{"id": "main", "capabilities": [{"id": "refresh"}]}]})
        commands = [(cmd["command"], cmd["arguments"]) for cmd in payload["commands"]]
        self.posts.append(commands)
        for name, args in commands:
            if name == "refresh" and self.refresh_updates:
                self.report = {"value": self.actual, "timestamp": datetime.now(timezone.utc).isoformat()}
            elif name == "volumeUp":
                self.actual = min(self.actual + 1, 100)
            elif name == "volumeDown":
                self.actual = max(self.actual - 1, 0)
            elif name == "setVolume":
                self.actual = args[0]
        return json.dumps({"results": [{"status": "COMPLETED", "id": str(i)} for i in range(len(commands))]})


def cloud(platform, tx):
    cls = load_definition(platform, "SmartThingsTVClient")
    cls._send_volume_command.__globals__.update(
        urllib_parse=parse, parse_volume_report=load_definition(platform, "parse_volume_report"),
        SmartThingsCommandRejected=RuntimeError,
    )
    cfg = dict(smartthings_device_id="test-tv", tv_volume_floor=10, tv_volume_refresh_seconds=30)
    client = cls(cfg)
    client._run = tx
    return cfg, client


class KeyboardFeedbackTests(unittest.TestCase):
    def wait_for(self, check):
        deadline = time.monotonic() + 4
        while time.monotonic() < deadline:
            if check():
                return
            time.sleep(.01)
        self.fail("Timed out waiting for test volume feedback")

    @contextlib.contextmanager
    def system(self, platform):
        tx = Transport()
        cfg, tv = cloud(platform, tx)
        path = ROOT / f"QN990F_{platform}_Controller" / "volume_readback.py"
        spec = importlib.util.spec_from_file_location("volume_readback", path)
        rb = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(rb)
        stop = mock.Mock()
        stop.is_set.return_value = stop.wait.return_value = False
        reader = rb.VolumeReadback(None, None, tv.read_cloud_volume, tv.request_volume_refresh,
                                   lambda: True, stop, logging.getLogger("feedback-test"))
        reader.refresh_after = time.monotonic() + 300  # A recent menu-open attempt.
        worker = load_worker(platform)
        worker.BUFFER_SECONDS = worker.FEEDBACK_SETTLE = .04
        worker.FEEDBACK_MIN_INTERVAL = .6
        with tempfile.TemporaryDirectory() as tmp, mock.patch.dict(sys.modules, volume_readback=rb):
            directory = Path(tmp)
            vol = worker(cfg, tv, directory, reader=reader)
            status_file = directory / "volume-status.json"
            try:
                self.wait_for(status_file.is_file)
                yield tx, vol, lambda: json.loads(status_file.read_bytes())
            finally:
                vol.stop()

    def test_cold_net_steps_one_post_then_feedback_then_absolute_batch(self):
        for platform in ("macOS", "Windows"):
            with self.subTest(platform=platform), self.system(platform) as (tx, vol, status):
                self.assertIsNone(status()["value"])
                for direction in ("up", "up", "up", "up", "down"):
                    self.assertTrue(vol.handle_key(direction, system_is_max=True))
                self.wait_for(lambda: status()["source"] == "SmartThings refresh")
                self.assertEqual(status()["value"], 13)
                self.assertEqual(tx.posts, [[("volumeUp", [])] * 3, [("refresh", [])]])
                for _ in range(3):
                    self.assertTrue(vol.handle_key("down"))
                self.wait_for(lambda: status()["value"] == 10 and status()["source"] == "SmartThings refresh")
                self.assertEqual(tx.posts[2:], [[("setVolume", [10])], [("refresh", [])]])

    def test_observed_three_tap_cadence_and_continuous_hold(self):
        for platform in ("macOS", "Windows"):
            with self.subTest(platform=platform):
                tx = Transport()
                cfg, tv = cloud(platform, tx)
                cfg["tv_volume_floor"] = 0
                with mock.patch.object(threading.Thread, "start"):
                    vol = load_worker(platform)(cfg, tv)
                # Replay the observed gaps with a virtual monotonic clock; under
                # the old 200 ms window the first flush would fire before tap 2.
                with mock.patch.object(time, "monotonic") as clock:
                    def tap(at):
                        if vol._buffer_deadline and vol._buffer_deadline <= at:
                            clock.return_value = vol._buffer_deadline
                            vol._flush_volume_buffer(0)
                        clock.return_value = at
                        self.assertTrue(vol.handle_key("down", **(
                            {"system_is_adjustable": False} if platform == "macOS" else {})))
                    for at in (1.0, 1.253, 1.470):
                        tap(at)
                    clock.return_value = vol._buffer_deadline
                    vol._flush_volume_buffer(0)
                    self.assertEqual(tx.posts, [[("volumeDown", [])]*3])
                    for at in (4.0, 4.2, 4.4, 4.6, 4.8, 4.99):
                        tap(at)
                    self.assertLessEqual(vol._buffer_deadline, 5.0)
                    clock.return_value = 5.0
                    vol._flush_volume_buffer(0)
                    self.assertEqual(tx.posts[-1], [("volumeDown", [])]*6)

    def test_net_zero_has_no_write_or_refresh_and_slider_supersedes_steps(self):
        for platform in ("macOS", "Windows"):
            with self.subTest(platform=platform), self.system(platform) as (tx, vol, status):
                vol.handle_key("up", system_is_max=True)
                self.assertTrue(vol.handle_key("down"))
                time.sleep(.4)
                self.assertEqual(tx.posts, [])
                vol.BUFFER_SECONDS = .25
                vol.handle_key("up", system_is_max=True)
                target = dict(session=status()["session"], id="slider", value=6, created_at=time.time())
                (vol._menu_dir / "volume-request.json").write_text(json.dumps(target))
                self.wait_for(lambda: status()["value"] == 6 and status()["source"] == "SmartThings refresh")
                self.assertEqual(tx.posts, [[("setVolume", [6])], [("refresh", [])]])

    def test_stale_feedback_is_unknown_not_17_and_new_gestures_share_trailing_read(self):
        for platform in ("macOS", "Windows"):
            with self.subTest(platform=platform), self.system(platform) as (tx, vol, status):
                vol.FEEDBACK_MIN_INTERVAL = 1.0
                tx.refresh_updates = False
                vol.handle_key("up", system_is_max=True)
                self.wait_for(lambda: "not reported" in status()["message"])
                self.assertIsNone(status()["value"])
                for _ in range(2):
                    vol.handle_key("up", system_is_max=True)
                    time.sleep(.16)
                tx.refresh_updates = True
                self.wait_for(lambda: status()["value"] == 13)
                self.assertEqual(tx.posts.count([("refresh", [])]), 2)
                self.assertEqual(tx.posts[-1], [("refresh", [])])

    def test_guardrail_partial_receipt_and_disconnect_never_replay(self):
        for platform in ("macOS", "Windows"):
            with self.subTest(platform=platform):
                tx = Transport()
                _, tv = cloud(platform, tx)
                self.assertTrue(tv.send_volume_steps(-23, lambda: True))
                self.assertEqual(tx.posts, [[("volumeDown", [])]*n for n in (10, 10, 3)])
                tx.posts.clear()
                self.assertFalse(tv.send_volume_steps(23, lambda: not tx.posts))
                self.assertEqual(tx.posts, [[("volumeUp", [])]*10])
                tv._run = mock.Mock(return_value='{"results":[{"id":"one","status":"ACCEPTED"}]}')
                self.assertFalse(tv.send_volume_steps(23, lambda: True))
                self.assertEqual(tv._run.call_count, 1)

    def test_cold_windows_down_keeps_system_floor_and_requests_a_baseline(self):
        with self.system("Windows") as (tx, vol, status):
            tx.actual = 12
            self.assertFalse(vol.handle_key("down"))
            self.wait_for(lambda: status()["value"] == 12)
            self.assertEqual(tx.posts, [[("refresh", [])]])
            self.assertTrue(vol.handle_key("down"))
            self.wait_for(lambda: [("setVolume", [11])] in tx.posts)

    def test_disconnect_discards_pending_feedback(self):
        for platform in ("macOS", "Windows"):
            with self.subTest(platform=platform), self.system(platform) as (tx, vol, status):
                vol.FEEDBACK_SETTLE = 1
                vol.handle_key("up", system_is_max=True)
                self.wait_for(lambda: "checking" in status()["message"])
                disconnect = vol.on_hdmi_disconnected if platform == "macOS" else vol.disconnected
                disconnect()
                vol.on_hdmi_connected()
                time.sleep(1.25)
                self.assertEqual(tx.posts, [[("volumeUp", [])]])
                self.assertIsNone(status()["value"])
