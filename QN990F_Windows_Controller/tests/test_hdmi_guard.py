"""Windows-only local HDMI safety checks; no real TV or cloud requests."""

import atexit
import importlib.util
import os
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest import mock


SOURCE = Path(__file__).resolve().parents[1] / "QN990FController.py"
SPEC = importlib.util.spec_from_file_location("qn990f_windows_controller", SOURCE)
controller = importlib.util.module_from_spec(SPEC)
_TEST_APP_DIR = tempfile.TemporaryDirectory(prefix="qn990f-hdmi-test-")
with mock.patch.dict(os.environ, {"LOCALAPPDATA": _TEST_APP_DIR.name}):
    SPEC.loader.exec_module(controller)


def _cleanup_test_appdir():
    for handler in list(controller.logger.handlers):
        controller.logger.removeHandler(handler)
        handler.close()
    _TEST_APP_DIR.cleanup()


atexit.register(_cleanup_test_appdir)


class HDMICommandGuardTests(unittest.TestCase):
    def setUp(self):
        self.config = controller.DEFAULT_CONFIG.copy()
        self.config.update({"tv_ip": "192.0.2.1", "enable_volume_control": False})

    def test_missing_or_wrong_target_fails_closed(self):
        target = {"path": "monitor-instance#edid-hash", "manufacturer": 11596,
                  "product": 31013, "name": "SAMSUNG"}
        with mock.patch.object(controller, "active_hdmi_targets", return_value=[target]):
            self.assertFalse(controller.hdmi_target_connected(self.config))
            self.config["hdmi_target"] = dict(target, product=42)
            self.assertFalse(controller.hdmi_target_connected(self.config))
            self.config["hdmi_target"] = target
            self.assertTrue(controller.hdmi_target_connected(self.config))
            with mock.patch.object(controller, "active_hdmi_targets",
                                   side_effect=OSError("topology unavailable")):
                self.assertFalse(controller.hdmi_target_connected(self.config))

    def test_native_monitor_path_changes_do_not_change_bound_identity(self):
        name = controller._DISPLAY_NAME()
        name.manufacturer = 11596
        name.product = 31013
        name.friendly = "SAMSUNG"
        identity = "system\\currentcontrolset\\enum\\display\\sam7925\\uid4352#edid"
        with mock.patch.object(controller, "_registry_hdmi_identity",
                               return_value=identity) as registry:
            name.path = ""
            missing_path = controller._canonical_hdmi_target(4352, name)
            name.path = r"\\?\DISPLAY#SAM7925#UID4352"
            reported_path = controller._canonical_hdmi_target(4352, name)
            self.assertEqual(missing_path, reported_path)
            self.assertEqual(reported_path["path"], identity)
            self.assertEqual(registry.call_count, 2)
        with mock.patch.object(controller, "_registry_hdmi_identity", return_value=""):
            self.assertIsNone(controller._canonical_hdmi_target(4352, name))

    def test_topology_warning_is_rate_limited_without_caching_connection(self):
        self.config["hdmi_target"] = {"path": "bound", "manufacturer": 1, "product": 2}
        controller._hdmi_warning_next_at = 0.0
        with mock.patch.object(controller, "active_hdmi_targets",
                               side_effect=OSError("driver failed")) as probe, \
             mock.patch.object(controller.time, "monotonic",
                               side_effect=[100.0, 101.0, 131.0]), \
             mock.patch.object(controller.logger, "warning") as warning:
            self.assertFalse(controller.hdmi_target_connected(self.config))
            self.assertFalse(controller.hdmi_target_connected(self.config))
            self.assertFalse(controller.hdmi_target_connected(self.config))
            self.assertEqual(probe.call_count, 3)
            self.assertEqual(warning.call_count, 2)

    def test_stale_volume_queue_is_dropped_after_reconnect(self):
        sent = []
        fake_tv = SimpleNamespace(send=lambda key: sent.append(("key", key)),
                                  set_volume=lambda value: sent.append(("volume", value)),
                                  get_volume=lambda: 20)
        fake_thread = SimpleNamespace(start=lambda: None, join=lambda timeout: None)
        with mock.patch.object(controller.threading, "Thread", return_value=fake_thread), \
             mock.patch.object(controller, "hdmi_target_connected", return_value=True):
            volume = controller.VolumeCoordinator(self.config, fake_tv)
            volume._tv_volume = None
            self.assertTrue(volume.handle_key("up", system_is_max=True))
            key, epoch = volume._commands.get_nowait()
            self.assertEqual(key, "KEY_VOLUP")
            volume.disconnected()
            volume._process_command(key, epoch)
            volume._process_command("flush_volume", epoch)
            self.assertEqual(sent, [])
            self.assertIsNone(volume._tv_volume)
            volume.stop()

    def test_disconnect_during_lan_open_never_sends_key(self):
        connected = [True]

        class FakeTV:
            def __init__(self):
                self.keys = []

            def open(self):
                connected[0] = False

            def send_key(self, key, **kwargs):
                self.keys.append(key)

            def close(self):
                pass

        tv = FakeTV()
        client = controller.TVClient(self.config)
        with mock.patch.object(controller, "hdmi_target_connected",
                               side_effect=lambda config: connected[0]), \
             mock.patch.object(client, "_new_tv", return_value=tv):
            self.assertFalse(client.send("KEY_PICTURE_OFF"))
        self.assertEqual(tv.keys, [])

    def test_cloud_post_blocked_after_request_preparation(self):
        self.config.update({"control_method": "smartthings",
                            "smartthings_device_id": "test-device"})
        client = controller.SmartThingsTVClient(self.config)
        credentials = SimpleNamespace(read_text=lambda **kwargs:
                                      '{"test":{"accessToken":"dummy"}}')
        with mock.patch.object(controller, "SMARTTHINGS_CREDENTIALS_FILE", credentials), \
             mock.patch.object(client, "_credential_key", return_value="test"), \
             mock.patch.object(controller, "hdmi_target_connected", return_value=False), \
             mock.patch.object(controller.urllib_request, "urlopen") as urlopen:
            with self.assertRaises(RuntimeError):
                client._api_request("POST", "/devices/test/commands", {}, 1)
            urlopen.assert_not_called()

    def test_disconnect_clears_intent_and_requires_fresh_input(self):
        probe = controller.Controller(self.config)
        probe._set_picture_off_state(True)
        probe.pending_input_wake_at = 12.0
        with mock.patch.object(controller, "write_status"), \
             mock.patch.object(controller, "hdmi_target_connected",
                               side_effect=[False, True, True]):
            self.assertFalse(probe._check_hdmi_connection(1.0))
            self.assertFalse(probe.is_picture_off())
            self.assertEqual(probe.pending_input_wake_at, 0.0)
            self.assertEqual(probe.next_off_attempt, float("inf"))
            self.assertTrue(probe._check_hdmi_connection(3.0))
            self.assertEqual(probe.next_off_attempt, float("inf"))
            probe.handle_raw_input({"kind": "keyboard", "keydown": True,
                                    "vkey": ord("A"), "device": "test"})
            self.assertEqual(probe.next_off_attempt, 0.0)
        probe.stop()


if __name__ == "__main__":
    unittest.main()
