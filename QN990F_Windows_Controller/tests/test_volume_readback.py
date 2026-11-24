"""Windows wall-clock regression tests for volume readback."""
import importlib.util
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest import mock

spec = importlib.util.spec_from_file_location(
    "windows_volume_readback", Path(__file__).resolve().parents[1] / "volume_readback.py"
)
readback = importlib.util.module_from_spec(spec)
spec.loader.exec_module(readback)


class WindowsReadbackClockTests(unittest.TestCase):
    def test_direct_read_can_share_wall_clock_tick_with_fence(self):
        reader = readback.VolumeReadback(
            lambda: 12, None, None, None, lambda: True,
            SimpleNamespace(is_set=lambda: False), mock.Mock(),
        )
        with mock.patch.object(readback.time, "time", return_value=100):
            self.assertEqual(reader.read(100), (12, 100, "HDMI DDC"))

    def test_menu_request_allows_small_ntfs_clock_skew(self):
        with mock.patch.object(Path, "stat", return_value=SimpleNamespace(
            st_mtime_ns=100_001_000_000, st_mtime=100.001, st_size=7,
        )), mock.patch.object(Path, "read_text", return_value="session"), \
                mock.patch.object(readback.time, "time", return_value=100):
            requested, key = readback.menu_read_requested(Path("."), "session", None)
            self.assertTrue(requested)
            self.assertFalse(readback.menu_read_requested(Path("."), "session", key)[0])
