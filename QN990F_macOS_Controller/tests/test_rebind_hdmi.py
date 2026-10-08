"""Safe rebinding flow; never runs the native probe or a TV command."""

import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock


PATH = Path(__file__).parents[1] / "Rebind-HDMI.py"
SPEC = importlib.util.spec_from_file_location("mac_hdmi_rebind", PATH)
rebind = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(rebind)
ORIGINAL_RUN_BOUNDED = rebind.run_bounded
IDENTITY = "a" * 64
OLD_IDENTITY = "b" * 64


class RebindTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.config = self.root / "config.json"
        self.plist = self.root / "agent.plist"
        self.plist.write_text("agent")
        self.original = {
            "tv_hdmi_edid_sha256": OLD_IDENTITY,
            "enable_idle_off": False,
            "idle_minutes": 10,
            "control_method": "smartthings",
            "smartthings_device_id": "preserve",
            "enable_volume_control": True,
        }
        self.config.write_text(json.dumps(self.original))
        patches = [
            mock.patch.object(rebind, "APP_DIR", self.root),
            mock.patch.object(rebind, "CONFIG", self.config),
            mock.patch.object(rebind, "PLIST", self.plist),
            mock.patch.object(rebind, "display_candidates", return_value=[{
                "edid_sha256": IDENTITY, "product": 1, "serial": 2,
            }]),
            mock.patch.object(rebind, "wait_for_controller_exit"),
            mock.patch.object(rebind, "run_bounded", return_value=(0, "", "")),
            mock.patch.object(rebind.os, "kill"),
        ]
        self.mocks = [self.enterContext(patch) for patch in patches]

    def test_running_controller_is_stopped_and_restarted_with_only_binding_changed(self):
        with mock.patch.object(rebind, "running_controller_pid", return_value=123), \
             mock.patch.object(rebind, "running_state", return_value=True), \
             mock.patch.object(rebind, "service_loaded", return_value=True), \
             mock.patch.object(rebind, "bootstrap_running_agent") as start:
            rebind.rebind(IDENTITY)
        updated = json.loads(self.config.read_text())
        self.assertEqual(updated, {**self.original, "tv_hdmi_edid_sha256": IDENTITY})
        self.assertEqual(self.config.stat().st_mode & 0o777, 0o600)
        commands = [call.args[0] for call in self.mocks[5].call_args_list]
        self.assertEqual([command[1] for command in commands], ["bootout"])
        start.assert_called_once()

    def test_stopped_controller_stays_stopped(self):
        with mock.patch.object(rebind, "running_controller_pid", return_value=None), \
             mock.patch.object(rebind, "service_loaded", return_value=False), \
             mock.patch.object(rebind, "bootstrap_running_agent") as start:
            rebind.rebind(IDENTITY)
        start.assert_not_called()
        self.mocks[5].assert_not_called()
        self.assertEqual(json.loads(self.config.read_text())["tv_hdmi_edid_sha256"], IDENTITY)

    def test_final_probe_failure_does_not_stop_or_modify_controller(self):
        with mock.patch.object(rebind, "display_candidates", side_effect=[
            [{"edid_sha256": IDENTITY}], []
        ]), mock.patch.object(rebind, "running_controller_pid", return_value=123), \
             mock.patch.object(rebind, "service_loaded", return_value=True), \
             mock.patch.object(rebind, "running_state", return_value=True):
            with self.assertRaisesRegex(RuntimeError, "disconnected"):
                rebind.rebind(IDENTITY)
        self.assertEqual(json.loads(self.config.read_text()), self.original)
        commands = [call.args[0][1] for call in self.mocks[5].call_args_list]
        self.assertEqual(commands, ["bootout", "kickstart"])

    def test_failed_restart_restores_previous_config(self):
        with mock.patch.object(rebind, "running_controller_pid", return_value=123), \
             mock.patch.object(rebind, "service_loaded", return_value=True), \
             mock.patch.object(rebind, "running_state", return_value=True), \
             mock.patch.object(rebind, "bootstrap_running_agent", side_effect=[
                 RuntimeError("failed"), None
             ]) as start:
            with self.assertRaisesRegex(RuntimeError, "failed"):
                rebind.rebind(IDENTITY)
        self.assertEqual(json.loads(self.config.read_text()), self.original)
        self.assertEqual(start.call_count, 2)

    def test_concurrent_config_edit_is_not_overwritten(self):
        changed = {**self.original, "enable_idle_off": True}
        calls = 0

        def probe():
            nonlocal calls
            calls += 1
            if calls == 2:
                self.config.write_text(json.dumps(changed))
            return [{"edid_sha256": IDENTITY}]

        with mock.patch.object(rebind, "display_candidates", side_effect=probe), \
             mock.patch.object(rebind, "running_controller_pid", return_value=None):
            with self.assertRaisesRegex(RuntimeError, "Configuration changed"):
                rebind.rebind(IDENTITY)
        self.assertEqual(json.loads(self.config.read_text()), changed)

    def test_cancelled_child_is_terminated_and_reaped(self):
        child = mock.Mock()
        child.pid = 12345
        child.poll.return_value = None
        child.communicate.side_effect = [KeyboardInterrupt(), ("", "")]
        with mock.patch.object(rebind.subprocess, "Popen", return_value=child), \
             mock.patch.object(rebind.os, "killpg") as kill_group:
            with self.assertRaises(KeyboardInterrupt):
                ORIGINAL_RUN_BOUNDED(["/bin/false"])
        kill_group.assert_called_once_with(12345, rebind.signal.SIGTERM)
        self.assertEqual(child.communicate.call_count, 2)

    def test_stop_signals_and_waits_before_bootout(self):
        order = []
        with mock.patch.object(rebind, "running_controller_pid", return_value=123), \
             mock.patch.object(rebind, "service_loaded", return_value=True), \
             mock.patch.object(rebind.os, "kill", side_effect=lambda *_: order.append("signal")), \
             mock.patch.object(rebind, "wait_for_controller_exit", side_effect=lambda: order.append("wait")), \
             mock.patch.object(rebind, "run_bounded", side_effect=lambda *_args, **_kwargs: order.append("bootout")):
            rebind.stop_loaded_agent()
        self.assertEqual(order, ["signal", "wait", "bootout"])

    def test_timeout_kills_group_even_if_parent_exited_but_child_holds_pipe(self):
        child = mock.Mock()
        child.pid = 54321
        child.poll.return_value = 0
        child.communicate.side_effect = [
            rebind.subprocess.TimeoutExpired("probe", 1),
            rebind.subprocess.TimeoutExpired("probe", 3),
            ("", ""),
        ]
        with mock.patch.object(rebind.subprocess, "Popen", return_value=child), \
             mock.patch.object(rebind.os, "killpg") as kill_group:
            with self.assertRaisesRegex(RuntimeError, "Timed out"):
                ORIGINAL_RUN_BOUNDED(["/bin/false"], timeout=1)
        self.assertEqual(
            [call.args for call in kill_group.call_args_list],
            [(54321, rebind.signal.SIGTERM), (54321, rebind.signal.SIGKILL)],
        )
        self.assertEqual(child.communicate.call_count, 3)


if __name__ == "__main__":
    unittest.main()
