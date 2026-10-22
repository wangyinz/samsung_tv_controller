"""Windows native hook tests, using mocked input and TV (no key injection)."""
import ctypes
import queue
import threading
import time
import unittest
from types import SimpleNamespace
from unittest import mock

from test_hdmi_guard import controller as c


class VolumeHookTests(unittest.TestCase):
    def key(self, hook, vkey, message):
        data = c.KBDLLHOOKSTRUCT()
        data.vkCode = vkey
        return hook._hook_proc(0, message, ctypes.addressof(data))

    def test_callback_uses_only_fresh_memory_snapshot(self):
        handler = mock.Mock(return_value=True)
        hook = c.LowLevelVolumeHook(handler, eligible=mock.Mock(side_effect=AssertionError))
        hook._sample = (time.monotonic(), True, True)
        with mock.patch.object(c.user32, "CallNextHookEx", return_value=42), \
             mock.patch.object(c.WindowsEndpointVolume, "is_max", side_effect=AssertionError), \
             mock.patch.object(c, "hdmi_target_connected", side_effect=AssertionError):
            self.assertEqual(self.key(hook, c.VK_VOLUME_UP, c.WM_KEYDOWN), 1)
            self.assertEqual(self.key(hook, c.VK_VOLUME_UP, c.WM_KEYUP), 1)
            handler.assert_called_once_with("up", True)
            hook._sample = (time.monotonic() - 1, True, True)
            self.assertEqual(self.key(hook, c.VK_VOLUME_DOWN, c.WM_KEYDOWN), 42)
            self.assertEqual(handler.call_count, 1)
            hook._sample = (time.monotonic(), False, True)
            self.assertEqual(self.key(hook, c.VK_VOLUME_UP, c.WM_KEYDOWN), 42)

    def test_native_key_invalidates_pre_key_maximum_snapshot(self):
        handler = mock.Mock(return_value=False)
        hook = c.LowLevelVolumeHook(handler)
        hook._sample = (time.monotonic(), True, True)
        with mock.patch.object(c.user32, "CallNextHookEx", return_value=42):
            self.assertEqual(self.key(hook, c.VK_VOLUME_DOWN, c.WM_KEYDOWN), 42)
            self.assertEqual(self.key(hook, c.VK_VOLUME_UP, c.WM_KEYDOWN), 42)
        handler.assert_called_once_with("down", True)

    def test_repeats_and_opposite_steps_buffer_once_without_raw_duplicates(self):
        config = c.DEFAULT_CONFIG.copy()
        tv = mock.Mock()
        tv.authorization_required.return_value = False
        inert_thread = SimpleNamespace(start=lambda: None)
        with mock.patch.object(c.threading, "Thread", return_value=inert_thread):
            volume = c.VolumeCoordinator(config, tv)
        volume._tv_volume = 20
        hook = c.LowLevelVolumeHook(
            lambda direction, at_max: volume.handle_key(direction, at_max, check_hdmi=False))
        hook._sample = (time.monotonic(), True, True)
        with mock.patch.object(c, "hdmi_target_connected", side_effect=AssertionError), \
             mock.patch.object(c.user32, "CallNextHookEx", return_value=42):
            for key in [c.VK_VOLUME_UP] * 5 + [c.VK_VOLUME_DOWN] * 3:
                self.assertEqual(self.key(hook, key, c.WM_KEYDOWN), 1)
                self.assertEqual(self.key(hook, key, c.WM_KEYUP), 1)
        self.assertEqual(volume._tv_volume, 22)
        self.assertEqual(volume._commands.get_nowait(), ("flush_volume", 0))
        with self.assertRaises(queue.Empty):
            volume._commands.get_nowait()
        tv.send.assert_not_called()
        tv.set_volume.assert_not_called()
        with mock.patch.object(c, "hdmi_target_connected", return_value=False):
            volume._process_command("flush_volume", 0)
        tv.set_volume.assert_not_called()
        self.assertIsNone(volume._tv_volume)

    def test_floor_and_system_maximum_keep_native_behavior(self):
        config = c.DEFAULT_CONFIG.copy()
        tv = SimpleNamespace(authorization_required=lambda: False)
        with mock.patch.object(c.threading, "Thread", return_value=SimpleNamespace(start=lambda: None)):
            volume = c.VolumeCoordinator(config, tv)
        volume._tv_volume = 11
        self.assertTrue(volume.handle_key("down", check_hdmi=False))
        self.assertEqual(volume._tv_volume, 10)
        self.assertFalse(volume.handle_key("down", check_hdmi=False))
        self.assertFalse(volume.handle_key("up", False, check_hdmi=False))
        self.assertTrue(volume.handle_key("up", True, check_hdmi=False))

    def test_dedicated_hook_queue_and_com_worker_have_owned_lifetimes(self):
        hook = c.LowLevelVolumeHook(mock.Mock(), eligible=lambda: True)
        release_messages = threading.Event()
        sampled = threading.Event()
        thread_ids = {}

        class Endpoint:
            def open(self):
                thread_ids["open"] = threading.get_ident()

            def is_max(self):
                thread_ids["sample"] = threading.get_ident()
                sampled.set()
                return True

            def close(self):
                thread_ids["close"] = threading.get_ident()

        def install(*args):
            thread_ids["hook"] = threading.get_ident()
            return 123

        def get_message(*args):
            release_messages.wait(2)
            return 0

        with mock.patch.object(c, "WindowsEndpointVolume", Endpoint), \
             mock.patch.object(c.user32, "SetWindowsHookExW", side_effect=install), \
             mock.patch.object(c.user32, "GetMessageW", side_effect=get_message), \
             mock.patch.object(c.user32, "PostThreadMessageW", side_effect=lambda *args: release_messages.set()), \
             mock.patch.object(c.user32, "UnhookWindowsHookEx") as unhook:
            try:
                hook.install()
                self.assertTrue(sampled.wait(2))
            finally:
                hook.close()
            self.assertFalse(hook._thread.is_alive())
            self.assertFalse(hook._sampler.is_alive())
            unhook.assert_called_once_with(123)
        self.assertEqual(thread_ids["open"], thread_ids["sample"])
        self.assertEqual(thread_ids["open"], thread_ids["close"])
        self.assertNotEqual(thread_ids["open"], thread_ids["hook"])
        self.assertNotEqual(thread_ids["hook"], threading.get_ident())


class GlobalHotkeyTests(unittest.TestCase):
    def setUp(self):
        # Never deliver real shell volume messages from this isolated suite.
        for name in ("FindWindowW", "GetShellWindow"):
            patcher = mock.patch.object(c.user32, name, return_value=0)
            patcher.start()
            self.addCleanup(patcher.stop)

    def test_shell_command_handles_volume_and_osd_once_without_endpoint_step(self):
        endpoint = mock.Mock()
        target = 0x123456789

        def send(hwnd, message, source, command, flags, timeout, result):
            self.assertEqual((hwnd, message, source), (target, 0x0319, target))
            self.assertEqual(flags, 0x03)
            self.assertEqual(timeout, 250)
            ctypes.cast(result, ctypes.POINTER(ctypes.c_size_t)).contents.value = 1
            return 1

        with mock.patch.object(c.user32, "FindWindowW", return_value=target), \
             mock.patch.object(c.user32, "SendMessageTimeoutW", side_effect=send) as native:
            c.step_windows_volume_with_osd("up", endpoint)
            c.step_windows_volume_with_osd("down", endpoint)
        self.assertEqual([call.args[3] for call in native.call_args_list],
                         [10 << 16, 9 << 16])
        endpoint.step.assert_not_called()

    def test_shell_timeout_never_double_applies_volume(self):
        endpoint = mock.Mock()
        for error in (0, 1460, 1400):
            with mock.patch.object(c.user32, "FindWindowW", return_value=123), \
                 mock.patch.object(c.user32, "SendMessageTimeoutW", return_value=0), \
                 mock.patch.object(c.ctypes, "get_last_error", return_value=error):
                c.step_windows_volume_with_osd("down", endpoint)
        endpoint.step.assert_not_called()

    def test_delivered_shell_command_returning_zero_is_not_repeated(self):
        endpoint = mock.Mock()
        with mock.patch.object(c.user32, "FindWindowW", return_value=123), \
             mock.patch.object(c.user32, "SendMessageTimeoutW", return_value=1):
            c.step_windows_volume_with_osd("up", endpoint)
        endpoint.step.assert_not_called()

    def test_shell_unavailable_or_definitely_rejected_uses_endpoint_once(self):
        endpoint = mock.Mock()
        c.step_windows_volume_with_osd("up", endpoint)
        endpoint.step.assert_called_once_with("up")
        for error in (5,):
            endpoint.reset_mock()
            with mock.patch.object(c.user32, "GetShellWindow", return_value=123), \
                 mock.patch.object(c.user32, "SendMessageTimeoutW", return_value=0), \
                 mock.patch.object(c.ctypes, "get_last_error", return_value=error):
                c.step_windows_volume_with_osd("down", endpoint)
            endpoint.step.assert_called_once_with("down")

    def test_shell_api_signatures_preserve_64bit_handles_and_result(self):
        self.assertEqual(c.user32.SendMessageTimeoutW.argtypes,
                         [c.wintypes.HWND, c.wintypes.UINT, c.wintypes.WPARAM,
                          c.wintypes.LPARAM, c.wintypes.UINT, c.wintypes.UINT,
                          ctypes.POINTER(ctypes.c_size_t)])
        self.assertEqual(c.user32.SendMessageTimeoutW.restype, ctypes.c_ssize_t)

    def test_tv_route_never_sends_shell_volume(self):
        hotkeys = c.GlobalVolumeHotkeys(mock.Mock(return_value=True))
        with mock.patch.object(c, "step_windows_volume_with_osd") as native:
            hotkeys._route("down", mock.Mock())
        native.assert_not_called()

    def test_audio_startup_failure_never_grabs_global_keys(self):
        hotkeys = c.GlobalVolumeHotkeys(mock.Mock())
        endpoint = mock.Mock()
        endpoint.open.side_effect = OSError("no audio endpoint")
        with mock.patch.object(c, "WindowsEndpointVolume", return_value=endpoint), \
             mock.patch.object(c.user32, "RegisterHotKey") as register:
            with self.assertRaises(RuntimeError):
                hotkeys.install()
        register.assert_not_called()
        endpoint.close.assert_called_once()
        self.assertFalse(hotkeys._worker.is_alive())
        self.assertIsNone(hotkeys._thread)

    def test_close_stops_hotkey_queue_and_audio_worker(self):
        hotkeys = c.GlobalVolumeHotkeys(mock.Mock())
        release = threading.Event()
        with mock.patch.object(c, "WindowsEndpointVolume", return_value=mock.Mock()), \
             mock.patch.object(c.user32, "RegisterHotKey", return_value=True), \
             mock.patch.object(c.user32, "GetMessageW", side_effect=lambda *args: release.wait(2) and 0), \
             mock.patch.object(c.user32, "PostThreadMessageW", side_effect=lambda *args: release.set()), \
             mock.patch.object(c.user32, "UnregisterHotKey") as unregister:
            try:
                hotkeys.install()
            finally:
                hotkeys.close()
        self.assertFalse(hotkeys._thread.is_alive())
        self.assertFalse(hotkeys._worker.is_alive())
        self.assertEqual(unregister.call_count, 2)

    def test_native_steps_unmute_and_use_endpoint_step_not_input_injection(self):
        endpoint = c.WindowsEndpointVolume()
        methods = {14: mock.Mock(return_value=0), 17: mock.Mock(return_value=0),
                   18: mock.Mock(return_value=0)}
        with mock.patch.object(endpoint, "_with_endpoint", side_effect=lambda operation: operation(123)), \
             mock.patch.object(c, "_com_method", side_effect=lambda pointer, index, *args: methods[index]):
            endpoint.step("up")
            endpoint.step("down")
        self.assertEqual(methods[14].call_args_list,
                         [mock.call(123, False, None), mock.call(123, False, None)])
        methods[17].assert_called_once_with(123, None)
        methods[18].assert_called_once_with(123, None)

    def test_route_logging_is_rate_limited(self):
        hotkeys = c.GlobalVolumeHotkeys(mock.Mock(return_value=True))
        with mock.patch.object(c.time, "monotonic", side_effect=[100.0, 100.1, 101.1]), \
             mock.patch.object(c.logger, "info") as log:
            for direction in ("down", "down", "up"):
                hotkeys._route(direction, mock.Mock())
        self.assertEqual(log.call_count, 2)
        self.assertEqual(log.call_args_list[0].args[1:], ("down", "TV"))

    def test_each_hotkey_routes_once_and_native_steps_only_when_not_consumed(self):
        handler = mock.Mock(side_effect=[False, True, True, False])
        endpoint = mock.Mock()
        endpoint.is_max.side_effect = [False, True]
        hotkeys = c.GlobalVolumeHotkeys(handler)
        for direction in ("up", "up", "down", "down"):
            hotkeys._route(direction, endpoint)
        self.assertEqual(handler.call_args_list,
                         [mock.call("up", False), mock.call("up", True),
                          mock.call("down", False), mock.call("down", False)])
        self.assertEqual(endpoint.step.call_args_list, [mock.call("up"), mock.call("down")])

    def test_partial_registration_is_cleaned_before_fallback(self):
        hotkeys = c.GlobalVolumeHotkeys(mock.Mock())
        with mock.patch.object(c.user32, "RegisterHotKey", side_effect=[True, False]), \
             mock.patch.object(c.user32, "UnregisterHotKey") as unregister:
            hotkeys._run_hotkeys()
        self.assertIsNotNone(hotkeys._error)
        self.assertTrue(hotkeys._ready.is_set())
        unregister.assert_called_once_with(None, 0x51A1)

    def test_hotkey_repeats_queue_once_without_keyboard_hook(self):
        hotkeys = c.GlobalVolumeHotkeys(mock.Mock())
        messages = [0x51A1, 0x51A1, 0x51A2]

        def get_message(pointer, *args):
            if not messages:
                return 0
            msg = ctypes.cast(pointer, ctypes.POINTER(c.MSG)).contents
            msg.message = c.WM_HOTKEY
            msg.wParam = messages.pop(0)
            return 1

        with mock.patch.object(c.user32, "RegisterHotKey", return_value=True) as register, \
             mock.patch.object(c.user32, "UnregisterHotKey") as unregister, \
             mock.patch.object(c.user32, "GetMessageW", side_effect=get_message), \
             mock.patch.object(c.user32, "SetWindowsHookExW", side_effect=AssertionError):
            hotkeys._run_hotkeys()
        self.assertIsNone(hotkeys._error)
        self.assertEqual(register.call_args_list,
                         [mock.call(None, 0x51A1, 0, c.VK_VOLUME_UP),
                          mock.call(None, 0x51A2, 0, c.VK_VOLUME_DOWN)])
        self.assertEqual(unregister.call_count, 2)
        self.assertEqual([hotkeys._commands.get_nowait() for _ in range(3)],
                         ["up", "up", "down"])
        with self.assertRaises(queue.Empty):
            hotkeys._commands.get_nowait()

    def test_audio_worker_owns_com_and_releases_on_stop(self):
        hotkeys = c.GlobalVolumeHotkeys(mock.Mock(return_value=False))
        thread_ids = []

        class Endpoint:
            def open(self):
                thread_ids.append(threading.get_ident())

            def is_max(self):
                return False

            def step(self, direction):
                thread_ids.append(threading.get_ident())
                hotkeys._stop.set()

            def close(self):
                thread_ids.append(threading.get_ident())

        hotkeys._commands.put("up")
        with mock.patch.object(c, "WindowsEndpointVolume", Endpoint):
            thread = threading.Thread(target=hotkeys._run_audio)
            thread.start()
            thread.join(2)
        self.assertFalse(thread.is_alive())
        self.assertEqual(len(thread_ids), 3)
        self.assertEqual(len(set(thread_ids)), 1)
        self.assertNotEqual(thread_ids[0], threading.get_ident())


if __name__ == "__main__":
    unittest.main()
