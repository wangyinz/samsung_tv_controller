import importlib.util
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time
import types
import unittest
from unittest import mock


CONTROLLER_PATH = Path(__file__).parents[1] / "QN990FController.py"
TEST_HOME = tempfile.TemporaryDirectory()

fake_samsungtvws = types.ModuleType("samsungtvws")
fake_samsungtvws.SamsungTVWS = object
sys.modules["samsungtvws"] = fake_samsungtvws

original_home = os.environ.get("HOME")
original_platform = sys.platform
os.environ["HOME"] = TEST_HOME.name
sys.platform = "darwin"
try:
    spec = importlib.util.spec_from_file_location("qn990f_controller", CONTROLLER_PATH)
    controller = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(controller)
finally:
    sys.platform = original_platform
    if original_home is None:
        os.environ.pop("HOME", None)
    else:
        os.environ["HOME"] = original_home


DEVICE_ID = "12345678-1234-4234-8234-123456789abc"


def cloud_config(temp_dir):
    return {
        "control_method": "smartthings",
        "picture_off_key": "KEY_PICTURE_OFF",
        "wake_key": "KEY_RETURN",
        "smartthings_cli": "/usr/bin/true",
        "smartthings_no_browser_dir": temp_dir,
        "smartthings_profile": "local.qn990f.picture-controller",
        "smartthings_device_id": DEVICE_ID,
        "smartthings_command_timeout_seconds": 20.0,
        "enable_volume_control": True,
    }


def controller_config(control_method="lan"):
    config = {
        **controller.DEFAULT_CONFIG,
        "control_method": control_method,
        "tv_ip": "192.0.2.10",
        "idle_minutes": 10.0,
        "enable_idle_off": True,
        "enable_volume_control": False,
    }
    if control_method == "smartthings":
        config.update(cloud_config(TEST_HOME.name))
    return config


class FakeBackend:
    def __init__(self, idle=0.0):
        self.idle = idle
        self.idle_calls = 0
        self.reset_calls = 0
        self.events = []
        self.on_poll = None

    def idle_seconds(self):
        self.idle_calls += 1
        return self.idle

    def reset_input_baseline(self):
        self.reset_calls += 1

    def poll_input_events(self):
        events, self.events = self.events, []
        if self.on_poll is not None:
            self.on_poll()
        return events


class RecordingTV:
    def __init__(self, results=None):
        self.sent = []
        self.set_volumes = []
        self.volume_set = threading.Event()
        self.results = list(results or [])
        self.auth_required = False

    def authorization_required(self):
        return self.auth_required

    def send(self, key):
        self.sent.append(key)
        return self.results.pop(0) if self.results else True

    def close(self):
        pass

    def get_volume(self):
        return None

    def set_volume(self, value):
        self.set_volumes.append(value)
        self.volume_set.set()
        return self.results.pop(0) if self.results else True


class Clock:
    def __init__(self, value=1.0):
        self.value = value

    def __call__(self):
        return self.value


class LoggingConfigurationTests(unittest.TestCase):
    def test_controller_log_has_bounded_retention(self):
        handlers = [
            handler
            for handler in controller.logger.handlers
            if isinstance(handler, controller.RotatingFileHandler)
        ]

        self.assertEqual(len(handlers), 1)
        self.assertEqual(handlers[0].maxBytes, controller.LOG_MAX_BYTES)
        self.assertEqual(handlers[0].backupCount, controller.LOG_BACKUP_COUNT)
        self.assertEqual(controller.LOG_MAX_BYTES, 1_000_000)
        self.assertEqual(controller.LOG_BACKUP_COUNT, 3)


class AudioIdentityTests(unittest.TestCase):
    def test_endpoint_suffix_is_removed_from_physical_identity(self):
        before_update = "4C2D2579-0000-0000-0123-0103808E5078_00000030"
        after_update = "4C2D2579-0000-0000-0123-0103808E5078"

        self.assertTrue(controller.audio_output_matches(before_update, after_update))

    def test_different_physical_devices_do_not_match(self):
        self.assertFalse(
            controller.audio_output_matches(
                "4C2D2579-0000-0000-0123-0103808E5078_00000030",
                "4C2D2579-0000-0000-0123-0103808E5079_00000030",
            )
        )

    def test_empty_identity_never_matches(self):
        self.assertFalse(controller.audio_output_matches("", ""))
        self.assertFalse(controller.audio_output_matches("bound", ""))

    def test_load_config_migrates_a_legacy_endpoint_uid_in_memory(self):
        config_path = Path(TEST_HOME.name) / "legacy-audio-config.json"
        config_path.write_text(
            json.dumps(
                {
                    "control_method": "lan",
                    "tv_ip": "192.0.2.10",
                    "tv_audio_output_uid": (
                        "4C2D2579-0000-0000-0123-0103808E5078_00000030"
                    ),
                }
            ),
            encoding="utf-8",
        )

        with mock.patch.object(controller, "CONFIG_FILE", config_path):
            loaded = controller.load_config()

        self.assertEqual(
            loaded["tv_audio_output_uid"],
            "4c2d2579-0000-0000-0123-0103808e5078",
        )


class InputSourceTests(unittest.TestCase):
    def test_windowserver_hardware_activity_is_recognized(self):
        output = (
            'pid 396(WindowServer): [0x1] 00:00:00 UserIsActive named: '
            '"com.apple.iohideventsystem.queue.tickle serviceID:100072bfa '
            'service:AppleUserHIDEventService product:Razer Basilisk V3 Pro '
            'eventType:17"'
        )

        self.assertTrue(controller.windowserver_activity_is_hardware(output))

    def test_windowserver_process_activity_is_rejected(self):
        output = (
            'pid 396(WindowServer): [0x1] 00:00:00 UserIsActive named: '
            '"com.apple.iohideventsystem.queue.tickle service:IOHIDSystem '
            'pid:1576 process:Amphetamine"'
        )

        self.assertFalse(controller.windowserver_activity_is_hardware(output))

    def test_stale_windowserver_hardware_activity_is_rejected(self):
        output = (
            'pid 396(WindowServer): [0x1] 00:00:03 UserIsActive named: '
            '"com.apple.iohideventsystem.queue.tickle serviceID:100072bfa '
            'service:AppleUserHIDEventService product:Razer Basilisk V3 Pro '
            'eventType:17"'
        )

        self.assertFalse(controller.windowserver_activity_is_hardware(output))

    def test_same_age_mixed_activity_is_rejected_in_either_order(self):
        hardware = (
            'pid 396(WindowServer): [0x1] 00:00:00 UserIsActive named: '
            '"com.apple.iohideventsystem.queue.tickle serviceID:100072bfa '
            'service:AppleUserHIDEventService product:Razer Basilisk V3 Pro '
            'eventType:17"'
        )
        software = (
            'pid 396(WindowServer): [0x2] 00:00:00 UserIsActive named: '
            '"com.apple.iohideventsystem.queue.tickle service:IOHIDSystem '
            'pid:1576 process:Amphetamine"'
        )

        for output in (f"{hardware}\n{software}", f"{software}\n{hardware}"):
            with self.subTest(output=output):
                self.assertFalse(controller.windowserver_activity_is_hardware(output))

    def test_pointer_source_check_fails_closed(self):
        cases = [
            subprocess.CompletedProcess([], 1, stdout="", stderr="failed"),
            subprocess.CompletedProcess([], 0, stdout="unexpected output", stderr=""),
        ]
        for completed in cases:
            with self.subTest(returncode=completed.returncode, stdout=completed.stdout), \
                 mock.patch.object(controller.subprocess, "run", return_value=completed):
                self.assertFalse(controller.latest_pointer_activity_is_hardware())

        with mock.patch.object(
            controller.subprocess,
            "run",
            side_effect=subprocess.TimeoutExpired(["pmset"], 1),
        ):
            self.assertFalse(controller.latest_pointer_activity_is_hardware())


class MacOSBackendTests(unittest.TestCase):
    def test_volume_hid_matching_accepts_only_volume_usages(self):
        backend = controller.MacOSBackend

        self.assertEqual(
            backend._volume_direction_for_hid_usage(
                backend.K_HID_PAGE_CONSUMER,
                backend.K_HID_USAGE_CONSUMER_VOLUME_INCREMENT,
            ),
            "up",
        )
        self.assertEqual(
            backend._volume_direction_for_hid_usage(
                backend.K_HID_PAGE_KEYBOARD,
                backend.K_HID_USAGE_KEYBOARD_VOLUME_DOWN,
            ),
            "down",
        )
        self.assertIsNone(
            backend._volume_direction_for_hid_usage(
                backend.K_HID_PAGE_KEYBOARD, 0x04
            )
        )

    def test_hid_wheel_accepts_both_directions_but_not_zero(self):
        backend = controller.MacOSBackend
        page = backend.K_HID_PAGE_GENERIC_DESKTOP
        usage = backend.K_HID_USAGE_GENERIC_DESKTOP_WHEEL

        self.assertEqual(
            backend._hid_input_for_usage(page, usage, 1), "mouse_wheel"
        )
        self.assertEqual(
            backend._hid_input_for_usage(page, usage, -1), "mouse_wheel"
        )
        self.assertIsNone(backend._hid_input_for_usage(page, usage, 0))

    def test_hid_volume_classification_ignores_key_release(self):
        backend = controller.MacOSBackend
        page = backend.K_HID_PAGE_CONSUMER
        usage = backend.K_HID_USAGE_CONSUMER_VOLUME_INCREMENT

        self.assertEqual(
            backend._hid_input_for_usage(page, usage, 1), "volume_up"
        )
        self.assertIsNone(backend._hid_input_for_usage(page, usage, 0))

    def test_manual_carbon_pump_dispatches_and_releases_event(self):
        calls = []
        backend = controller.MacOSBackend.__new__(controller.MacOSBackend)
        backend._dispatcher_target = controller.c_void_p(99)
        backend._hotkey_event = controller.threading.Event()

        class FakeHIToolbox:
            statuses = [0, -9875, -9875]

            def ReceiveNextEvent(self, count, event_types, timeout, pull, out_event):
                calls.append(("receive", count, event_types, timeout, pull))
                status = self.statuses.pop(0)
                if status == 0:
                    out_event._obj.value = 123
                return status

            def SendEventToEventTarget(self, event, target):
                calls.append(("send", event.value, target.value))
                backend._hotkey_event.set()
                return 0

            def ReleaseEvent(self, event):
                calls.append(("release", event.value))

        backend.hitoolbox = FakeHIToolbox()

        self.assertTrue(backend.pump_events(0.1))
        self.assertFalse(backend.pump_events(0.1))

        self.assertIn(("send", 123, 99), calls)
        self.assertIn(("release", 123), calls)

    def test_hid_counter_changes_are_normalized_as_input_events(self):
        backend = controller.MacOSBackend.__new__(controller.MacOSBackend)
        backend._input_poll_lock = controller.threading.Lock()
        backend._last_input_snapshot = {
            "keyboard": 7,
            "mouse_button": (2, 3, 4),
            "mouse_wheel": 5,
            "mouse_move": 6,
            "cursor": (10.0, 20.0),
        }
        current = {
            "keyboard": 8,
            "mouse_button": (3, 3, 4),
            "mouse_wheel": 6,
            "mouse_move": 7,
            "cursor": (14.0, 18.0),
        }

        with mock.patch.object(backend, "_input_snapshot", return_value=current):
            events = backend.poll_input_events()

        self.assertEqual(
            events,
            [
                {"kind": "keyboard"},
                {"kind": "mouse_button"},
                {"kind": "mouse_wheel"},
                {"kind": "mouse_move", "dx": 4.0, "dy": -2.0},
            ],
        )

    def test_cursor_change_without_hid_motion_counter_is_ignored(self):
        backend = controller.MacOSBackend.__new__(controller.MacOSBackend)
        backend._input_poll_lock = controller.threading.Lock()
        backend._last_input_snapshot = {
            "keyboard": 1,
            "mouse_button": (1, 1, 1),
            "mouse_wheel": 1,
            "mouse_move": 1,
            "cursor": (10.0, 20.0),
        }
        current = {**backend._last_input_snapshot, "cursor": (30.0, 40.0)}

        with mock.patch.object(backend, "_input_snapshot", return_value=current):
            self.assertEqual(backend.poll_input_events(), [])

    def test_fixed_volume_output_is_treated_as_already_at_maximum(self):
        backend = controller.MacOSBackend.__new__(controller.MacOSBackend)

        def read_property(_object_id, selector, _scope, value, element=0):
            if selector == backend.K_AUDIO_HARDWARE_PROPERTY_DEFAULT_OUTPUT_DEVICE:
                value.value = 42
                return value.value
            raise RuntimeError("no writable volume scalar")

        backend._audio_property = read_property
        self.assertEqual(backend.system_volume_state(), (False, True))
        self.assertTrue(backend.system_volume_is_max())

    def test_default_audio_output_info_includes_stable_device_identity(self):
        backend = controller.MacOSBackend.__new__(controller.MacOSBackend)

        def read_property(_object_id, selector, _scope, value, element=0):
            del element
            if selector == backend.K_AUDIO_HARDWARE_PROPERTY_DEFAULT_OUTPUT_DEVICE:
                value.value = 42
            elif selector == backend.K_AUDIO_DEVICE_PROPERTY_TRANSPORT_TYPE:
                value.value = controller.fourcc("hdmi")
            else:
                self.fail(f"Unexpected selector: {selector}")
            return value.value

        values = {
            backend.K_AUDIO_OBJECT_PROPERTY_NAME: "QN990F",
            backend.K_AUDIO_OBJECT_PROPERTY_MANUFACTURER: "Samsung",
            backend.K_AUDIO_DEVICE_PROPERTY_DEVICE_UID: "coreaudio-tv-uid",
        }
        backend._audio_property = read_property
        backend._audio_string_property = lambda _device, selector: values[selector]

        self.assertEqual(
            backend.default_audio_output_info(),
            {
                "name": "QN990F",
                "manufacturer": "Samsung",
                "uid": "coreaudio-tv-uid",
                "physical_uid": "coreaudio-tv-uid",
                "transport": "hdmi",
            },
        )


class DirectHIDStartupTests(unittest.TestCase):
    class Backend:
        def __init__(self, failures):
            self.failures = failures
            self.register_calls = 0
            self.unregister_calls = 0

        def register_volume_keys(self, _handler, wheel_handler):
            self.register_calls += 1
            self.wheel_handler = wheel_handler
            if self.register_calls <= self.failures:
                raise RuntimeError("not ready")

        def unregister_volume_keys(self):
            self.unregister_calls += 1

    def make_controller(self):
        volume = mock.Mock()
        ctrl = types.SimpleNamespace(
            cfg={"enable_volume_control": True},
            handle_volume_key=mock.Mock(),
            handle_input=mock.Mock(),
            set_hid_input_state=mock.Mock(),
            volume=volume,
        )
        return ctrl, volume

    def test_direct_hid_retries_until_registration_succeeds(self):
        backend = self.Backend(failures=2)
        ctrl, volume = self.make_controller()

        controller.register_direct_hid_input(
            backend, ctrl, threading.Event(), retry_delays=(0.0, 0.0)
        )

        self.assertEqual(backend.register_calls, 3)
        self.assertEqual(backend.unregister_calls, 2)
        ctrl.set_hid_input_state.assert_called_once_with(True)
        self.assertIs(ctrl.volume, volume)
        volume.stop.assert_not_called()

    def test_direct_hid_failure_keeps_the_menu_volume_worker_available(self):
        backend = self.Backend(failures=3)
        ctrl, volume = self.make_controller()

        controller.register_direct_hid_input(
            backend, ctrl, threading.Event(), retry_delays=(0.0, 0.0)
        )

        self.assertEqual(backend.register_calls, 3)
        self.assertEqual(backend.unregister_calls, 3)
        ctrl.set_hid_input_state.assert_called_once_with(
            False, "not ready", notify=True
        )
        volume.stop.assert_not_called()
        self.assertIs(ctrl.volume, volume)

    def test_denied_input_monitoring_prompts_without_waiting_for_retries(self):
        backend = self.Backend(failures=3)
        backend.input_monitoring_access = mock.Mock(return_value="denied")
        ctrl, volume = self.make_controller()

        controller.register_direct_hid_input(
            backend, ctrl, threading.Event(), retry_delays=(5.0, 15.0, 30.0)
        )

        self.assertEqual(backend.register_calls, 1)
        ctrl.set_hid_input_state.assert_called_once_with(
            False, "not ready", notify=True
        )
        volume.stop.assert_not_called()
        self.assertIs(ctrl.volume, volume)


class LANClientTests(unittest.TestCase):
    def test_new_config_uses_generic_remote_name(self):
        config = controller_config("lan")

        with mock.patch.object(controller, "SamsungTVWS") as samsung_tv:
            controller.TVClient(config)._new()

        self.assertEqual(
            samsung_tv.call_args.kwargs["name"],
            "Samsung-TV-Picture-Controller",
        )


class SmartThingsTVClientTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.cfg = cloud_config(self.temp_dir.name)
        self.client = controller.SmartThingsTVClient(self.cfg)
        controller.SMARTTHINGS_CREDENTIALS_FILE.unlink(missing_ok=True)

    def tearDown(self):
        controller.SMARTTHINGS_CREDENTIALS_FILE.unlink(missing_ok=True)
        self.temp_dir.cleanup()

    def write_credentials(self, lifetime=controller.timedelta(days=1)):
        expires = (
            controller.datetime.now(controller.timezone.utc)
            + lifetime
        ).isoformat(timespec="milliseconds").replace("+00:00", "Z")
        credentials = {
            self.client._credential_key(): {
                "accessToken": "old-access",
                "refreshToken": "old-refresh",
                "expires": expires,
                "scope": ["r:devices:*", "x:devices:*"],
                "installedAppId": "installed-app",
                "deviceId": "oauth-device",
            }
        }
        self.client._write_credentials(credentials)

    def test_picture_off_uses_phone_accessibility_ocf_payload(self):
        with mock.patch.object(self.client, "_run") as run:
            self.client._send_ocf_remote("KEY_PICTURE_OFF")

        run.assert_called_once_with(
            "devices:commands",
            DEVICE_ID,
            'main:execute:execute("/sec/tv/remotecontrol",'
            '{"x.com.samsung.tv.keyvalue":"KEY_PICTURE_OFF",'
            '"x.com.samsung.tv.keystatus":"pressAndRelease"})',
        )

    def test_send_uses_same_ocf_transport_for_off_and_wake(self):
        with mock.patch.object(self.client, "_send_ocf_remote") as send:
            self.assertTrue(self.client.send("KEY_PICTURE_OFF"))
            self.assertTrue(self.client.send("KEY_RETURN"))

        self.assertEqual(
            send.call_args_list,
            [mock.call("KEY_PICTURE_OFF"), mock.call("KEY_RETURN")],
        )

    def test_volume_status_is_read_from_audio_volume_capability(self):
        status = {
            "components": {
                "main": {"audioVolume": {"volume": {"value": 17}}}
            }
        }
        with mock.patch.object(
            self.client, "_run", return_value=json.dumps(status)
        ) as run:
            self.assertEqual(self.client.get_volume(), 17)

        run.assert_called_once_with("devices:status", DEVICE_ID, "--json")

    def test_set_volume_uses_explicit_audio_volume_target(self):
        with mock.patch.object(self.client, "_run") as run:
            self.assertTrue(self.client.set_volume(10))

        run.assert_called_once_with(
            "devices:commands",
            DEVICE_ID,
            "main:audioVolume:setVolume(10)",
        )

    def test_volume_keys_use_audio_volume_capability(self):
        with mock.patch.object(self.client, "_send_volume_command") as send:
            self.assertTrue(self.client.send("KEY_VOLUP"))
            self.assertTrue(self.client.send("KEY_VOLDOWN"))

        self.assertEqual(
            send.call_args_list,
            [mock.call("KEY_VOLUP"), mock.call("KEY_VOLDOWN")],
        )

    def test_volume_down_command_uses_standard_audio_volume_capability(self):
        with mock.patch.object(self.client, "_run") as run:
            self.client._send_volume_command("KEY_VOLDOWN")

        run.assert_called_once_with(
            "devices:commands",
            DEVICE_ID,
            "main:audioVolume:volumeDown()",
        )

    def test_run_is_noninteractive_and_does_not_inherit_pat(self):
        completed = subprocess.CompletedProcess([], 0, stdout="ok", stderr="")
        with mock.patch.dict(os.environ, {"SMARTTHINGS_TOKEN": "must-not-leak"}), \
             mock.patch.object(controller.subprocess, "run", return_value=completed) as run:
            output = self.client._run("devices", DEVICE_ID, "--json")

        self.assertEqual(output, "ok")
        args, kwargs = run.call_args
        self.assertEqual(
            args[0],
            [
                "/usr/bin/true",
                "devices",
                DEVICE_ID,
                "--json",
                "--profile",
                "local.qn990f.picture-controller",
                "--token",
                "",
                "--language",
                "NONE",
            ],
        )
        self.assertNotIn("SMARTTHINGS_TOKEN", kwargs["env"])
        self.assertEqual(kwargs["env"]["PATH"], self.temp_dir.name)
        self.assertEqual(kwargs["env"]["BROWSER"], "none")
        self.assertNotIn("shell", kwargs)

    def test_refresh_401_is_reported_as_interactive_authorization(self):
        completed = subprocess.CompletedProcess(
            [], 1, stdout="", stderr="Request failed with status code 401"
        )
        with mock.patch.object(
            controller.subprocess, "run", return_value=completed
        ):
            with self.assertRaises(controller.SmartThingsAuthRequired):
                self.client._run("devices", DEVICE_ID, "--json")

        self.assertTrue(self.client.authorization_required())

    def test_early_api_401_forces_refresh_and_retries_once(self):
        self.write_credentials()
        failed = subprocess.CompletedProcess(
            [], 1, stdout="", stderr="Request failed with status code 401"
        )
        succeeded = subprocess.CompletedProcess(
            [], 0, stdout="device", stderr=""
        )
        token_response = io.BytesIO(json.dumps({
            "access_token": "new-access",
            "refresh_token": "new-refresh",
            "expires_in": 86400,
        }).encode("utf-8"))

        with mock.patch.object(
            controller.subprocess, "run", side_effect=[failed, succeeded]
        ) as run, mock.patch.object(
            controller.urllib_request, "urlopen", return_value=token_response
        ) as urlopen:
            output = self.client._run("devices", DEVICE_ID, "--json")

        self.assertEqual(output, "device")
        self.assertEqual(run.call_count, 2)
        urlopen.assert_called_once()
        refresh_request = urlopen.call_args.args[0]
        self.assertEqual(refresh_request.get_header("User-agent"), "@smartthings/cli")
        saved = json.loads(
            controller.SMARTTHINGS_CREDENTIALS_FILE.read_text(encoding="utf-8")
        )[self.client._credential_key()]
        self.assertEqual(saved["accessToken"], "new-access")
        self.assertEqual(saved["refreshToken"], "new-refresh")
        self.assertFalse(self.client.authorization_required())

    def test_authorization_is_refreshed_proactively_within_six_hours(self):
        self.write_credentials(controller.timedelta(hours=1))
        succeeded = subprocess.CompletedProcess(
            [], 0, stdout="device", stderr=""
        )
        token_response = io.BytesIO(json.dumps({
            "access_token": "new-access",
            "refresh_token": "new-refresh",
            "expires_in": 86400,
        }).encode("utf-8"))

        with mock.patch.object(
            controller.subprocess, "run", return_value=succeeded
        ) as run, mock.patch.object(
            controller.urllib_request, "urlopen", return_value=token_response
        ) as urlopen:
            output = self.client._run("devices", DEVICE_ID, "--json")

        self.assertEqual(output, "device")
        self.assertEqual(run.call_count, 1)
        urlopen.assert_called_once()

    def test_rejected_reactive_refresh_requires_authorization(self):
        self.write_credentials()
        failed = subprocess.CompletedProcess(
            [], 1, stdout="", stderr="Request failed with status code 401"
        )
        rejection = controller.urllib_error.HTTPError(
            controller.SMARTTHINGS_OAUTH_TOKEN_URL,
            401,
            "Unauthorized",
            None,
            io.BytesIO(),
        )
        self.addCleanup(rejection.close)

        with mock.patch.object(
            controller.subprocess, "run", return_value=failed
        ) as run, mock.patch.object(
            controller.urllib_request, "urlopen", side_effect=rejection
        ):
            with self.assertRaises(controller.SmartThingsAuthRequired):
                self.client._run("devices", DEVICE_ID, "--json")

        self.assertEqual(run.call_count, 1)
        self.assertTrue(self.client.authorization_required())

    def test_transient_reactive_refresh_failure_does_not_latch_authorization(self):
        self.write_credentials()
        failed = subprocess.CompletedProcess(
            [], 1, stdout="", stderr="Request failed with status code 401"
        )

        with mock.patch.object(
            controller.subprocess, "run", return_value=failed
        ), mock.patch.object(
            controller.urllib_request,
            "urlopen",
            side_effect=controller.urllib_error.URLError("offline"),
        ):
            with self.assertRaisesRegex(RuntimeError, "authorization service"):
                self.client._run("devices", DEVICE_ID, "--json")

        self.assertFalse(self.client.authorization_required())

    def test_background_timeout_is_transient_and_can_retry(self):
        timeout = subprocess.TimeoutExpired(["smartthings"], 20)
        with mock.patch.object(
            controller.subprocess, "run", side_effect=timeout
        ) as run:
            with self.assertRaisesRegex(RuntimeError, "network connection"):
                self.client._run("devices", DEVICE_ID, "--json")
            with self.assertRaisesRegex(RuntimeError, "network connection"):
                self.client._run("devices", DEVICE_ID, "--json")

        self.assertFalse(self.client.authorization_required())
        smartthings_calls = [
            call for call in run.call_args_list
            if call.args and call.args[0][0] == "/usr/bin/true"
        ]
        self.assertEqual(len(smartthings_calls), 2)

    def test_blocked_background_browser_fallback_latches_authorization(self):
        completed = subprocess.CompletedProcess(
            [], 1, stdout="", stderr="Error: spawn open ENOENT"
        )
        with mock.patch.object(
            controller.subprocess, "run", return_value=completed
        ):
            with self.assertRaises(controller.SmartThingsAuthRequired):
                self.client._run("devices", DEVICE_ID, "--json")

        self.assertTrue(self.client.authorization_required())

    def test_authorization_check_is_read_only_and_serialized_by_client(self):
        with mock.patch.object(self.client, "_run", return_value="{}") as run:
            self.assertTrue(self.client.check_authorization())

        run.assert_called_once_with("devices", DEVICE_ID, "--json")

    def test_authorization_check_stops_after_interactive_login_is_required(self):
        self.client._authorization_required = True
        with mock.patch.object(self.client, "_run") as run:
            self.assertFalse(self.client.check_authorization())

        run.assert_not_called()

    def test_pair_accepts_samsung_ocf_tv_shape_with_null_device_type_name(self):
        device = {
            "label": '65" Neo QLED 8K',
            "manufacturerName": "Samsung Electronics",
            "deviceTypeName": None,
            "type": "OCF",
            "ocf": {"modelNumber": "QN65QN990FFXZA"},
            "components": [
                {
                    "id": "main",
                    "capabilities": [
                        {"id": "execute", "version": 1},
                        {"id": "samsungvd.remoteControl", "version": 1},
                        {"id": "audioVolume", "version": 1},
                    ],
                    "categories": [{"name": "Television"}],
                }
            ],
        }
        with mock.patch.object(self.client, "_run", return_value=json.dumps(device)):
            self.assertEqual(self.client.pair(), '65" Neo QLED 8K')

    def test_pair_accepts_compatible_non_qn990f_model(self):
        device = {
            "label": "Living Room TV",
            "manufacturerName": "Samsung Electronics",
            "deviceTypeName": None,
            "type": "OCF",
            "ocf": {"modelNumber": "QE65S95FAUXZA"},
            "components": [
                {
                    "id": "main",
                    "capabilities": [
                        {"id": "execute", "version": 1},
                        {"id": "samsungvd.remoteControl", "version": 1},
                        {"id": "audioVolume", "version": 1},
                    ],
                    "categories": [{"name": "Television"}],
                }
            ],
        }
        with mock.patch.object(self.client, "_run", return_value=json.dumps(device)):
            self.assertEqual(self.client.pair(), "Living Room TV")

    def test_pair_rejects_device_without_execute_capability(self):
        device = {
            "manufacturerName": "Samsung Electronics",
            "type": "OCF",
            "ocf": {"modelNumber": "QN65QN990FFXZA"},
            "components": [{"id": "main", "capabilities": []}],
        }
        with mock.patch.object(self.client, "_run", return_value=json.dumps(device)):
            with self.assertRaisesRegex(RuntimeError, "required SmartThings TV"):
                self.client.pair()

    def test_pair_rejects_tv_without_audio_volume_capability(self):
        device = {
            "manufacturerName": "Samsung Electronics",
            "type": "OCF",
            "components": [
                {
                    "id": "main",
                    "capabilities": [
                        {"id": "execute", "version": 1},
                        {"id": "samsungvd.remoteControl", "version": 1},
                    ],
                    "categories": [{"name": "Television"}],
                }
            ],
        }
        with mock.patch.object(self.client, "_run", return_value=json.dumps(device)):
            with self.assertRaisesRegex(RuntimeError, "required SmartThings TV"):
                self.client.pair()

    def test_pair_accepts_tv_without_audio_volume_when_volume_control_is_disabled(self):
        device = {
            "manufacturerName": "Samsung Electronics",
            "type": "OCF",
            "components": [
                {
                    "id": "main",
                    "capabilities": [
                        {"id": "execute", "version": 1},
                        {"id": "samsungvd.remoteControl", "version": 1},
                    ],
                    "categories": [{"name": "Television"}],
                }
            ],
        }
        self.client.cfg["enable_volume_control"] = False

        with mock.patch.object(self.client, "_run", return_value=json.dumps(device)):
            self.assertEqual(self.client.pair(), "Samsung TV")

    def test_pair_rejects_light_sensor_child_device(self):
        device = {
            "manufacturerName": "Samsung Electronics",
            "type": "OCF",
            "ocf": {"modelNumber": "QN65QN990FFXZA"},
            "components": [
                {
                    "id": "main",
                    "capabilities": [{"id": "execute", "version": 1}],
                    "categories": [{"name": "LightSensor"}],
                }
            ],
        }
        with mock.patch.object(self.client, "_run", return_value=json.dumps(device)):
            with self.assertRaisesRegex(RuntimeError, "required SmartThings TV"):
                self.client.pair()


class CloudConfigTests(unittest.TestCase):
    def test_cloud_mode_keeps_requested_idle_automation(self):
        config = {
            **controller.DEFAULT_CONFIG,
            **cloud_config(TEST_HOME.name),
            "idle_minutes": 7.5,
            "enable_idle_off": True,
        }
        controller.CONFIG_FILE.write_text(json.dumps(config), encoding="utf-8")

        class BackendWithoutFrameworks:
            def parse_hotkey(self, _spec):
                return 0, 0

        with mock.patch.object(controller, "MacOSBackend", BackendWithoutFrameworks):
            loaded = controller.load_config()

        self.assertEqual(loaded["idle_minutes"], 7.5)
        self.assertTrue(loaded["enable_idle_off"])

    def test_legacy_fast_volume_refresh_is_clamped_for_cloud_use(self):
        config = {
            **controller.DEFAULT_CONFIG,
            **cloud_config(TEST_HOME.name),
            "tv_volume_refresh_seconds": 3.0,
        }
        controller.CONFIG_FILE.write_text(json.dumps(config), encoding="utf-8")

        class BackendWithoutFrameworks:
            def parse_hotkey(self, _spec):
                return 0, 0

        with mock.patch.object(controller, "MacOSBackend", BackendWithoutFrameworks):
            loaded = controller.load_config()

        self.assertEqual(loaded["tv_volume_refresh_seconds"], 30.0)

    def test_legacy_lan_config_keeps_existing_pairing_identity(self):
        config = {
            **controller.DEFAULT_CONFIG,
            "control_method": "lan",
            "tv_ip": "192.0.2.10",
        }
        config.pop("remote_name")

        with tempfile.TemporaryDirectory() as temp_dir:
            config_file = Path(temp_dir) / "config.json"
            config_file.write_text(json.dumps(config), encoding="utf-8")

            class BackendWithoutFrameworks:
                def parse_hotkey(self, _spec):
                    return 0, 0

            with mock.patch.object(controller, "CONFIG_FILE", config_file), \
                 mock.patch.object(controller, "MacOSBackend", BackendWithoutFrameworks):
                loaded = controller.load_config()

        self.assertEqual(loaded["remote_name"], "QN990F-Mac-Controller")

    def test_existing_config_defaults_mouse_movement_wake_to_disabled(self):
        config = {
            **controller.DEFAULT_CONFIG,
            "control_method": "lan",
            "tv_ip": "192.0.2.10",
        }
        config.pop("enable_mouse_move_wake")

        with tempfile.TemporaryDirectory() as temp_dir:
            config_file = Path(temp_dir) / "config.json"
            config_file.write_text(json.dumps(config), encoding="utf-8")

            class BackendWithoutFrameworks:
                def parse_hotkey(self, _spec):
                    return 0, 0

            with mock.patch.object(controller, "CONFIG_FILE", config_file), \
                 mock.patch.object(controller, "MacOSBackend", BackendWithoutFrameworks):
                loaded = controller.load_config()

        self.assertFalse(loaded["enable_mouse_move_wake"])


class VolumeCoordinatorTests(unittest.TestCase):
    def setUp(self):
        self.tv = RecordingTV()
        self.cfg = {
            **controller.DEFAULT_CONFIG,
            "tv_volume_floor": 10,
            "tv_volume_refresh_seconds": 60.0,
        }
        self.volume = controller.VolumeCoordinator(self.cfg, self.tv)
        self.volume.BUFFER_SECONDS = 0.01
        self.addCleanup(self.volume.stop)

    def test_volume_up_routes_to_tv_only_after_system_reaches_maximum(self):
        self.assertFalse(self.volume.handle_key("up", system_is_max=False))
        self.assertTrue(self.volume.handle_key("up", system_is_max=True))

    def test_volume_keys_are_released_after_authorization_failure(self):
        self.tv.auth_required = True

        self.assertFalse(self.volume.handle_key("up", system_is_max=True))
        self.assertFalse(self.volume.handle_key("down"))

    def test_volume_down_stops_tv_at_floor_then_returns_to_system(self):
        with self.volume._lock:
            self.volume._tv_volume = 11

        self.assertTrue(self.volume.handle_key("down"))
        self.assertFalse(self.volume.handle_key("down"))
        self.assertTrue(self.tv.volume_set.wait(1.0))
        self.assertEqual(self.tv.set_volumes, [10])

    def test_fixed_system_output_uses_full_tv_volume_range(self):
        with self.volume._lock:
            self.volume._tv_volume = 1

        self.assertTrue(
            self.volume.handle_key("down", system_is_adjustable=False)
        )
        self.assertFalse(
            self.volume.handle_key("down", system_is_adjustable=False)
        )
        self.assertTrue(self.tv.volume_set.wait(1.0))
        self.assertEqual(self.tv.set_volumes, [0])

    def test_fixed_system_output_routes_down_when_tv_state_is_unknown(self):
        self.assertTrue(
            self.volume.handle_key("down", system_is_adjustable=False)
        )

        deadline = time.monotonic() + 1.0
        while time.monotonic() < deadline and self.tv.sent != ["KEY_VOLDOWN"]:
            time.sleep(0.01)
        self.assertEqual(self.tv.sent, ["KEY_VOLDOWN"])

    def test_consecutive_volume_up_steps_are_sent_as_one_target(self):
        with self.volume._lock:
            self.volume._tv_volume = 20

        for _ in range(5):
            self.assertTrue(self.volume.handle_key("up", system_is_max=True))

        self.assertTrue(self.tv.volume_set.wait(1.0))
        self.assertEqual(self.tv.set_volumes, [25])

    def test_mixed_volume_steps_are_combined_as_net_change(self):
        with self.volume._lock:
            self.volume._tv_volume = 20

        for direction in ("up", "up", "down", "up", "down"):
            self.assertTrue(
                self.volume.handle_key(
                    direction,
                    system_is_max=direction == "up",
                )
            )

        self.assertTrue(self.tv.volume_set.wait(1.0))
        self.assertEqual(self.tv.set_volumes, [21])

    def test_cancelled_volume_steps_do_not_send_a_cloud_command(self):
        with self.volume._lock:
            self.volume._tv_volume = 20

        self.assertTrue(self.volume.handle_key("up", system_is_max=True))
        self.assertTrue(self.volume.handle_key("down"))
        time.sleep(0.1)

        self.assertEqual(self.tv.set_volumes, [])


class ControllerInputTests(unittest.TestCase):
    def setUp(self):
        self.status_patch = mock.patch.object(controller, "write_status")
        self.status_patch.start()
        self.addCleanup(self.status_patch.stop)

    def make_controller(self, control_method="lan", tv=None):
        backend = FakeBackend()
        with mock.patch.object(controller, "make_tv_client", return_value=tv or RecordingTV()):
            instance = controller.Controller(
                controller_config(control_method),
                backend,
            )
        self.addCleanup(instance.stop)
        return instance, backend

    @staticmethod
    def enable_mouse_move_wake(instance):
        instance.cfg["enable_mouse_move_wake"] = True

    def test_authorization_monitor_reports_failed_refresh_once(self):
        instance, _backend = self.make_controller("smartthings")
        instance.tv = mock.Mock()
        instance.tv.authorization_required.return_value = False
        instance.tv.check_authorization.return_value = False

        with mock.patch.object(instance._stop, "wait", return_value=False), \
             mock.patch.object(instance, "_report_authorization_required") as report:
            instance._authorization_monitor()

        report.assert_called_once_with()

    def test_adjustable_system_output_never_routes_volume_to_tv(self):
        instance, backend = self.make_controller()
        instance.cfg["enable_volume_control"] = True
        instance.volume = mock.Mock()
        backend.system_volume_state = mock.Mock(return_value=(True, True))

        self.assertFalse(instance.handle_volume_key("up"))

        instance.volume.handle_key.assert_not_called()

    def test_fixed_system_output_routes_volume_to_tv(self):
        instance, backend = self.make_controller()
        instance.cfg["enable_volume_control"] = True
        instance.volume = mock.Mock()
        instance.volume.handle_key.return_value = True
        instance.cfg["tv_audio_output_uid"] = "tv-output"
        backend.system_volume_state = mock.Mock(return_value=(False, True))
        backend.default_audio_output_info = mock.Mock(
            return_value={"uid": "tv-output"}
        )

        self.assertTrue(instance.handle_volume_key("down"))

        instance.volume.handle_key.assert_called_once_with(
            "down", system_is_max=True, system_is_adjustable=False
        )

    def test_unbound_fixed_output_never_routes_volume_to_tv(self):
        instance, backend = self.make_controller()
        instance.cfg["enable_volume_control"] = True
        instance.volume = mock.Mock()
        instance.cfg["tv_audio_output_uid"] = "tv-output"
        backend.system_volume_state = mock.Mock(return_value=(False, True))
        backend.default_audio_output_info = mock.Mock(
            return_value={
                "name": "Other HDMI",
                "uid": "other-output",
                "physical_uid": "other-output",
            }
        )

        with mock.patch.object(controller, "show_audio_binding_prompt"):
            self.assertFalse(instance.handle_volume_key("down"))

        instance.volume.handle_key.assert_not_called()

    def test_menu_worker_does_not_enable_disabled_media_key_routing(self):
        instance, _backend = self.make_controller()
        instance.volume = mock.Mock()
        self.assertFalse(instance.handle_volume_key("up"))
        instance.volume.handle_key.assert_not_called()

    def test_volume_health_reports_a_bound_physical_output_as_ready(self):
        instance, backend = self.make_controller()
        instance.cfg["enable_volume_control"] = True
        instance.cfg["tv_audio_output_uid"] = "physical-tv"
        backend.system_volume_state = mock.Mock(return_value=(False, True))
        backend.default_audio_output_info = mock.Mock(
            return_value={
                "name": "Samsung TV",
                "uid": "physical-tv_00000030",
                "physical_uid": "physical-tv",
            }
        )

        with mock.patch.object(controller, "write_volume_health") as write_health:
            instance.set_hid_input_state(True)

        self.assertEqual(write_health.call_args.args[:2], (
            "ready", "TV volume is active for Samsung TV."
        ))

    def test_input_permission_failure_is_visible_when_volume_is_disabled(self):
        instance, _backend = self.make_controller()

        with mock.patch.object(controller, "write_volume_health") as write_health:
            instance.set_hid_input_state(False, "permission denied")

        self.assertEqual(
            write_health.call_args.args[:2],
            ("input_permission_required", "permission denied"),
        )

    def test_volume_health_reports_binding_required_for_another_fixed_output(self):
        instance, backend = self.make_controller()
        instance.cfg["enable_volume_control"] = True
        instance.cfg["tv_audio_output_uid"] = "bound-tv"
        backend.system_volume_state = mock.Mock(return_value=(False, True))
        backend.default_audio_output_info = mock.Mock(
            return_value={
                "name": "Other HDMI",
                "uid": "other-output",
                "physical_uid": "other-output",
            }
        )

        with mock.patch.object(controller, "write_volume_health") as write_health:
            instance.set_hid_input_state(True)

        self.assertEqual(write_health.call_args.args[0], "binding_required")
        self.assertEqual(
            write_health.call_args.kwargs["current_audio_physical_uid"],
            "other-output",
        )

    def test_binding_alert_is_shown_once_per_unmatched_output(self):
        instance, _backend = self.make_controller()
        output = {"physical_uid": "other-output"}

        with mock.patch.object(controller.threading, "Thread") as thread:
            instance._report_binding_required(output)
            instance._report_binding_required(output)

        thread.assert_called_once()
        self.assertIs(thread.call_args.kwargs["target"], controller.show_audio_binding_prompt)

    def test_authorization_monitor_reports_a_latched_background_failure(self):
        instance, _backend = self.make_controller("smartthings")
        instance.tv = mock.Mock()
        instance.tv.authorization_required.return_value = True

        with mock.patch.object(instance._stop, "wait", return_value=False), \
             mock.patch.object(instance, "_report_authorization_required") as report:
            instance._authorization_monitor()

        report.assert_called_once_with()
        instance.tv.check_authorization.assert_not_called()

    def test_authorization_prompt_is_only_started_once(self):
        instance, _backend = self.make_controller("smartthings")

        with mock.patch.object(controller.threading, "Thread") as thread:
            instance._report_authorization_required()
            instance._report_authorization_required()

        thread.assert_called_once()
        thread.return_value.start.assert_called_once_with()

    def test_hotkey_is_one_way_picture_off_even_when_already_off(self):
        instance, backend = self.make_controller()
        clock = Clock(100.0)

        with mock.patch.object(controller.time, "monotonic", clock):
            instance.handle_hotkey()
            instance.pending_input_wake_at = 101.0
            instance.pending_input_source = "keyboard:key_down"
            instance.handle_hotkey()

        self.assertTrue(instance.is_off())
        self.assertEqual(
            instance.tv.sent,
            ["KEY_PICTURE_OFF", "KEY_PICTURE_OFF"],
        )
        self.assertEqual(instance.pending_input_wake_at, 0.0)
        self.assertEqual(backend.reset_calls, 3)

    def test_hotkey_suppression_rejects_a_concurrent_key_event(self):
        instance, _backend = self.make_controller()
        instance.set_off(True)
        instance.wake_not_before = 0.0
        instance.hotkey_input_suppress_until = 10.0
        clock = Clock(9.8)

        with mock.patch.object(controller.time, "monotonic", clock):
            instance.handle_input({"kind": "keyboard"})

        self.assertEqual(instance.pending_input_wake_at, 0.0)
        self.assertEqual(instance.tv.sent, [])

    def test_micro_mouse_motion_must_accumulate_to_threshold(self):
        instance, _backend = self.make_controller()
        self.enable_mouse_move_wake(instance)
        instance.set_off(True)
        instance.wake_not_before = 0.0
        clock = Clock(1.0)

        with mock.patch.object(controller.time, "monotonic", clock):
            instance.handle_input({"kind": "mouse_move", "dx": 4, "dy": 4})
            self.assertEqual(instance.pending_input_wake_at, 0.0)

            clock.value = 1.2
            instance.handle_input({"kind": "mouse_move", "dx": 5, "dy": 3})
            self.assertEqual(instance.pending_input_wake_at, 0.0)

            clock.value = 1.4
            instance.handle_input({"kind": "mouse_move", "dx": 4, "dy": 4})
            self.assertAlmostEqual(instance.pending_input_wake_at, 1.58)

            instance._process_pending_wake(1.57)
            self.assertEqual(instance.tv.sent, [])

            clock.value = 1.58
            with mock.patch.object(
                controller, "latest_pointer_activity_is_hardware", return_value=True
            ):
                instance._process_pending_wake(1.58)

        self.assertFalse(instance.is_off())
        self.assertEqual(instance.tv.sent, ["KEY_RETURN"])

    def test_mouse_motion_window_discards_old_accumulation(self):
        instance, _backend = self.make_controller()
        self.enable_mouse_move_wake(instance)
        instance.set_off(True)
        instance.wake_not_before = 0.0
        clock = Clock(1.0)

        with mock.patch.object(controller.time, "monotonic", clock):
            instance.handle_input({"kind": "mouse_move", "dx": 10, "dy": 10})
            clock.value = 1.51
            instance.handle_input({"kind": "mouse_move", "dx": 3, "dy": 2})

        self.assertEqual(instance.mouse_motion_total, 5.0)
        self.assertEqual(instance.pending_input_wake_at, 0.0)
        self.assertEqual(instance.tv.sent, [])

    def test_software_cursor_warp_does_not_wake_picture(self):
        instance, _backend = self.make_controller()
        self.enable_mouse_move_wake(instance)
        instance.set_off(True)
        instance.wake_not_before = 0.0
        clock = Clock(1.0)

        with mock.patch.object(controller.time, "monotonic", clock), \
             mock.patch.object(
                 controller, "latest_pointer_activity_is_hardware", return_value=False
             ):
            instance.handle_input(
                {"kind": "mouse_move", "dx": 720.448, "dy": -1176.762}
            )
            self.assertAlmostEqual(instance.pending_input_wake_at, 1.18)
            clock.value = 1.5
            instance._process_pending_wake(clock.value)

        self.assertEqual(instance.pending_input_wake_at, 0.0)
        self.assertTrue(instance.is_off())
        self.assertEqual(instance.tv.sent, [])

    def test_single_large_mouse_sample_qualifies_motion(self):
        instance, _backend = self.make_controller()
        self.enable_mouse_move_wake(instance)
        instance.set_off(True)
        instance.wake_not_before = 0.0
        clock = Clock(1.0)

        with mock.patch.object(controller.time, "monotonic", clock):
            instance.handle_input({"kind": "mouse_move", "dx": 30, "dy": 0})

        self.assertAlmostEqual(instance.pending_input_wake_at, 1.18)
        self.assertIn("sum=30", instance.pending_input_source)

    def test_periodic_software_cursor_warps_do_not_wake(self):
        instance, _backend = self.make_controller()
        self.enable_mouse_move_wake(instance)
        instance.set_off(True)
        instance.wake_not_before = 0.0
        clock = Clock(1.0)

        with mock.patch.object(controller.time, "monotonic", clock), \
             mock.patch.object(
                 controller, "latest_pointer_activity_is_hardware", return_value=False
             ):
            instance.handle_input({"kind": "mouse_move", "dx": 800, "dy": 500})
            clock.value = 1.18
            instance._process_pending_wake(clock.value)
            clock.value = 61.0
            instance.handle_input({"kind": "mouse_move", "dx": -800, "dy": -500})
            clock.value = 61.18
            instance._process_pending_wake(clock.value)

        self.assertEqual(instance.pending_input_wake_at, 0.0)
        self.assertEqual(instance.tv.sent, [])

    def test_zero_distance_threshold_qualifies_first_nonzero_sample(self):
        instance, _backend = self.make_controller()
        self.enable_mouse_move_wake(instance)
        instance.cfg["mouse_wake_threshold_counts"] = 0
        instance.set_off(True)
        instance.wake_not_before = 0.0
        clock = Clock(1.0)

        with mock.patch.object(controller.time, "monotonic", clock):
            instance.handle_input({"kind": "mouse_move", "dx": 1, "dy": 0})

        self.assertAlmostEqual(instance.pending_input_wake_at, 1.18)

    def test_button_debounce_can_replace_a_later_keyboard_wake(self):
        instance, _backend = self.make_controller()
        instance.set_off(True)
        instance.wake_not_before = 0.0
        clock = Clock(1.0)

        with mock.patch.object(controller.time, "monotonic", clock):
            instance.handle_input({"kind": "keyboard"})
            self.assertAlmostEqual(instance.pending_input_wake_at, 1.18)

            clock.value = 1.05
            instance.handle_input({"kind": "mouse_button"})
            self.assertAlmostEqual(instance.pending_input_wake_at, 1.13)

            clock.value = 1.06
            instance.handle_input({"kind": "mouse_wheel"})

        self.assertAlmostEqual(instance.pending_input_wake_at, 1.13)
        self.assertEqual(instance.pending_input_source, "mouse_button")

    def test_trusted_key_replaces_pending_unverified_mouse_wake(self):
        instance, _backend = self.make_controller()
        self.enable_mouse_move_wake(instance)
        instance.set_off(True)
        instance.wake_not_before = 0.0
        clock = Clock(1.0)

        with mock.patch.object(controller.time, "monotonic", clock), \
             mock.patch.object(
                 controller,
                 "latest_pointer_activity_is_hardware",
                 side_effect=AssertionError("trusted input must bypass pointer verification"),
             ):
            instance.handle_input({"kind": "mouse_move", "dx": 30, "dy": 0})
            self.assertAlmostEqual(instance.pending_input_wake_at, 1.18)

            clock.value = 1.05
            instance.handle_input({"kind": "keyboard"})
            self.assertAlmostEqual(instance.pending_input_wake_at, 1.23)
            self.assertEqual(instance.pending_input_source, "keyboard:key_down")

            clock.value = 1.23
            instance._process_pending_wake(clock.value)

        self.assertFalse(instance.is_off())
        self.assertEqual(instance.tv.sent, ["KEY_RETURN"])

    def test_mouse_movement_wake_is_disabled_by_default(self):
        instance, _backend = self.make_controller()
        instance.set_off(True)
        instance.wake_not_before = 0.0
        clock = Clock(1.0)

        with mock.patch.object(controller.time, "monotonic", clock), \
             mock.patch.object(
                 controller,
                 "latest_pointer_activity_is_hardware",
                 side_effect=AssertionError("disabled movement must not be verified"),
             ):
            instance.handle_input(
                {"kind": "mouse_move", "dx": 1200, "dy": -900}
            )
            instance._process_pending_wake(2.0)

        self.assertTrue(instance.is_off())
        self.assertEqual(instance.pending_input_wake_at, 0.0)
        self.assertEqual(instance.mouse_motion_total, 0.0)
        self.assertEqual(instance.tv.sent, [])

    def test_wake_guard_discards_input_before_qualification(self):
        instance, _backend = self.make_controller()
        instance.set_off(True)
        instance.wake_not_before = 2.0
        clock = Clock(1.9)

        with mock.patch.object(controller.time, "monotonic", clock):
            instance.handle_input({"kind": "mouse_move", "dx": 30, "dy": 0})
            instance.handle_input({"kind": "keyboard"})

            self.assertEqual(instance.mouse_motion_total, 0.0)
            self.assertEqual(instance.pending_input_wake_at, 0.0)

            clock.value = 2.0
            instance.handle_input({"kind": "keyboard"})

        self.assertAlmostEqual(instance.pending_input_wake_at, 2.18)

    def test_wake_rechecks_guard_after_waiting_for_picture_off(self):
        instance, _backend = self.make_controller()
        instance.set_off(True)
        instance.pending_input_wake_at = 2.0
        instance.pending_input_source = "keyboard:key_down"
        instance.wake_not_before = 2.8
        clock = Clock(2.0)

        with mock.patch.object(controller.time, "monotonic", clock):
            instance._process_pending_wake(2.0)

        self.assertTrue(instance.is_off())
        self.assertEqual(instance.pending_input_wake_at, 0.0)
        self.assertEqual(instance.tv.sent, [])

    def test_failed_cloud_blank_rearms_only_after_qualified_input(self):
        instance, _backend = self.make_controller(
            "smartthings",
            tv=RecordingTV(results=[False]),
        )

        self.assertFalse(instance.blank("idle"))
        self.assertEqual(instance.next_off_attempt, float("inf"))

        instance.handle_input({"kind": "mouse_move", "dx": 1, "dy": 1})
        self.assertEqual(instance.next_off_attempt, float("inf"))

        instance.handle_input({"kind": "keyboard"})
        self.assertEqual(instance.next_off_attempt, 0.0)

    def test_idle_api_is_not_used_to_wake_picture(self):
        instance, backend = self.make_controller()
        instance.set_off(True)
        backend.on_poll = instance._stop.set

        instance.monitor()

        self.assertEqual(backend.idle_calls, 0)
        self.assertEqual(instance.tv.sent, [])


if __name__ == "__main__":
    unittest.main()
