"""Trace the actual Mac and Windows scheduling loops without sleeping or using a TV."""
import importlib.util
import itertools
import logging
import sys
import threading
import time
import unittest
from unittest import mock

from test_volume_menu import ROOT, load_worker


def simulate(platform, ticks, *, attached=True, awake=True, frozen=False):
    path = ROOT / f"QN990F_{platform}_Controller" / "volume_readback.py"
    spec = importlib.util.spec_from_file_location("volume_readback", path)
    rb = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(rb)
    worker = load_worker(platform)
    times = iter(ticks)
    at, controls = next(times)
    actual, report, done = 10, (17, 1), False
    events, samples = [], []
    wall_ticks = itertools.count()
    wall = lambda: 1700000000 + at + next(wall_ticks) * 0.00001
    def refresh(allowed):
        nonlocal report
        assert allowed() and attached and awake
        events.append((at, "refresh"))
        if not frozen:
            report = actual, wall()
        return True
    def read_cloud():
        assert attached and awake
        events.append((at, "get"))
        return report
    stop = mock.Mock()
    stop.is_set.side_effect = lambda: done
    stop.wait.return_value = False  # No real wait for cloud report propagation.
    reader = rb.VolumeReadback(None, None, read_cloud, refresh, lambda: attached and awake,
                               stop, logging.getLogger("sync-test"))
    reader.refresh_after = float("inf")  # The menu cooldown is deliberately active.
    with mock.patch.dict(worker.__init__.__globals__, hdmi_tv_connected=lambda cfg: attached,
                         hdmi_target_connected=lambda cfg: attached), \
            mock.patch.dict(sys.modules, volume_readback=rb), \
            mock.patch.object(time, "monotonic", lambda: at), mock.patch.object(time, "time", wall), \
            mock.patch.object(threading.Thread, "start"), \
            mock.patch.object(rb, "menu_read_requested", side_effect=lambda *a: ("menu" in controls, None)):
        vol = worker({"tv_volume_refresh_seconds": 30}, object(), reader=reader)
        def apply():
            nonlocal attached, awake, actual
            if "unplug" in controls:
                attached = False
                (vol.on_hdmi_disconnected if platform == "macOS" else vol.disconnected)()
            if "plug" in controls:
                attached = True
                vol.on_hdmi_connected()
            if "off" in controls:
                awake = False
            if "awake" in controls:
                awake = True
            if "remote" in controls:
                actual = 13  # Only the TV's remote changed; the cloud cache still says 10.
            if "key write" in controls:
                vol._feedback_due = at
                vol._estimate_valid_until = at + 60
        def advance(_):
            nonlocal at, controls, done
            samples.append((at, vol._tv_volume))
            try:
                at, controls = next(times)
            except StopIteration:
                done = True
                return True
            apply()
            return False
        vol._stop.wait = advance
        vol._stop.is_set = lambda: done
        apply()
        vol._read_loop()
    return events, dict(samples)


class RemoteVolumeSyncTests(unittest.TestCase):
    def test_start_periodic_external_remote_and_reconnect_without_ui(self):
        for platform in ("macOS", "Windows"):
            with self.subTest(platform=platform):
                events, values = simulate(platform, [
                    (100, ()), (101, ("plug",)), (114, ("remote",)), (129, ()), (130, ()),
                    (133, ("unplug",)), (160, ()), (170, ("plug",)), (171, ("plug",)),
                ])
                self.assertEqual(events, [(t, op) for t in (100, 130, 170) for op in ("refresh", "get")])
                self.assertEqual((values[100], values[129], values[130], values[160], values[170]),
                                 (10, 10, 13, None, 13))

    def test_initially_disconnected_or_picture_off_and_collision_with_feedback(self):
        for platform in ("macOS", "Windows"):
            with self.subTest(platform=platform):
                events, _ = simulate(platform, [
                    (100, ()), (120, ("plug", "off")), (124, ()), (140, ("awake",)),
                    (169, ()), (170, ("key write", "menu")), (172, ("off",)), (200, ()),
                    (210, ("awake",)),
                ], attached=False)
                self.assertEqual(events, [(t, op) for t in (140, 170, 210) for op in ("refresh", "get")])

    def test_stale_reports_do_not_poll_in_a_loop_or_get_accepted_as_current(self):
        for platform in ("macOS", "Windows"):
            with self.subTest(platform=platform):
                events, values = simulate(platform, [(t, ()) for t in range(100, 162)], frozen=True)
                self.assertEqual(events, [(t, op) for t in (100, 130, 160) for op in ("refresh", "get", "get")])
                self.assertTrue(all(v is None for v in values.values()))

    def test_passive_menu_reads_cannot_postpone_the_periodic_refresh(self):
        for platform in ("macOS", "Windows"):
            with self.subTest(platform=platform):
                events, values = simulate(platform, [(100, ()), (108, ("menu",)), (119, ("remote", "menu")),
                                                      (129, ("menu",)), (130, ())])
                self.assertEqual([t for t, op in events if op == "refresh"], [100, 130])
                self.assertEqual(values[130], 13)
