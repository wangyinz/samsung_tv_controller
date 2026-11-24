"""Exercise actual provider ordering, refresh timing, wire parsing and binding."""
import ast
import ctypes
from ctypes import wintypes
import importlib.util
import logging
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import Mock, patch
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[2]
src = ROOT / "QN990F_macOS_Controller/volume_readback.py"
spec = importlib.util.spec_from_file_location("tested_readback", src)
rb = importlib.util.module_from_spec(spec)
spec.loader.exec_module(rb)


class ReadbackTests(unittest.TestCase):
    def setUp(self):
        self.ddc, self.upnp = Mock(return_value=None), Mock(return_value=None)
        self.cloud = Mock(return_value=(17, time.time() - 20*86400))
        self.refresh = Mock(return_value=True)
        self.stop = Mock()
        self.stop.is_set.return_value = self.stop.wait.return_value = False
        self.gate = Mock(return_value=True)
        self.r = rb.VolumeReadback(self.ddc, self.upnp, self.cloud, self.refresh,
                                   self.gate, self.stop, logging.getLogger("volume-test"))
        self.fence = time.time() - 30

    def test_standalone_windows_installer_ships_identical_policy(self):
        self.assertEqual(src.read_bytes(), (ROOT / "QN990F_Windows_Controller/volume_readback.py").read_bytes())

    def test_direct_zero_is_valid_and_skips_other_providers(self):
        self.ddc.return_value = 0
        v, stamp, source = self.r.read(self.fence, True)
        self.assertEqual((v, source), (0, "HDMI DDC"))
        self.assertGreater(stamp, self.fence)
        self.upnp.assert_not_called()
        self.refresh.assert_not_called()

    def test_ddc_timeout_falls_back_to_lan_and_backs_off_only_failed_path(self):
        self.ddc.side_effect = rb.subprocess.TimeoutExpired("ddc", 2)
        self.upnp.side_effect = [10, 11, 10]
        self.assertEqual([self.r.read(self.fence)[0] for _ in range(3)], [10, 11, 10])
        self.assertEqual(self.ddc.call_count, 1)
        self.cloud.assert_not_called()

    def test_passive_poll_and_old_refresh_reply_never_resurrect_17(self):
        self.assertIsNone(self.r.read(self.fence))
        self.refresh.assert_not_called()
        self.assertIsNone(self.r.read(self.fence, True))
        self.assertIsNone(self.r.read(self.fence, True))
        self.refresh.assert_called_once()
        self.assertEqual(self.stop.wait.call_count, 2)

    def test_only_post_refresh_report_counts_as_refreshed(self):
        self.cloud.side_effect = [None, (17, self.fence - 1), None]
        self.assertIsNone(self.r.read(self.fence, True))
        self.r.refresh_after = 0
        results = iter([lambda: None, lambda: (10, time.time())])
        self.cloud.side_effect = lambda: next(results)()
        self.assertEqual(self.r.read(self.fence, True)[::2], (10, "SmartThings refresh"))

    def test_known_recent_cloud_sample_does_not_trigger_refresh(self):
        stamp = time.time() - 1
        self.cloud.return_value = (10, stamp)
        self.assertIsNone(self.r.read(stamp, True, known=(10, stamp)))
        self.refresh.assert_not_called()

    def test_stuck_direct_reader_cannot_override_a_new_local_setting(self):
        self.ddc.side_effect = [17, 17, 17, 11]
        self.upnp.side_effect = [10, 10]
        self.assertEqual(self.r.read(self.fence)[0], 17)
        write = (10, time.time())
        for _ in range(2):
            result = self.r.read(write[1], last_set=write)
            self.assertEqual(result[::2], (10, "LAN UPnP"))
        self.assertEqual(self.r.read(write[1], last_set=write)[::2], (11, "HDMI DDC"))
        self.cloud.assert_not_called()

    def test_disconnect_or_new_write_discards_in_flight_result(self):
        valid = Mock(side_effect=[True, False, False])
        self.ddc.return_value = 99
        self.assertIsNone(self.r.read(self.fence, True, valid=valid))
        self.cloud.assert_not_called()
        self.refresh.assert_not_called()
        self.gate.return_value = False
        self.assertIsNone(self.r.read(self.fence, True))
        self.assertEqual(self.ddc.call_count, 1)

    def test_menu_open_signal_is_fresh_per_session_and_one_shot(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "volume-read.request"
            path.write_text("old-session")
            self.assertFalse(rb.menu_read_requested(path.parent, "current", None)[0])
            path.write_text("current")
            ok, key = rb.menu_read_requested(path.parent, "current", None)
            self.assertTrue(ok)
            self.assertFalse(rb.menu_read_requested(path.parent, "current", key)[0])
            with patch.object(rb.time, "time", return_value=time.time() + 30):
                self.assertFalse(rb.menu_read_requested(path.parent, "current", None)[0])

    def test_mac_ddc_only_uses_bound_edid_and_checks_volume_scale(self):
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / "m1ddc").touch()
            target = dict(edid_sha256="target", vendor=19501, product=31013, serial=123)
            cfg = {"tv_hdmi_edid_sha256": "target"}
            with patch.object(rb, "helper_number", side_effect=[100,10]) as helper:
                self.assertEqual(rb.mac_ddc(cfg, lambda: [target], tmp), 10)
                self.assertEqual([c.args[0][1:] for c in helper.call_args_list], [
                    ["display", "basic=19501:31013:123", verb, "volume"] for verb in ("max", "get")])
            with patch.object(rb, "helper_number", return_value=254) as helper:
                with self.assertRaises(rb.Unavailable):
                    rb.mac_ddc(cfg, lambda: [target], tmp)
                self.assertEqual(helper.call_count, 1)
            with self.assertRaises(rb.Unavailable):
                rb.mac_ddc(cfg, lambda: [target, target], tmp)


class RenderingControlTests(unittest.TestCase):
    host = "192.168.50.164"
    url = "http://192.168.50.164:9197/dmr"
    kind = rb.RC + "1"
    ident = "uuid:example-tv"

    def documents(self):
        return [ET.fromstring(x) for x in (
            f'<root xmlns="urn:schemas-upnp-org:device-1-0"><device>'
            f'<deviceType>urn:schemas-upnp-org:device:MediaRenderer:1</deviceType>'
            f'<manufacturer>Samsung Electronics</manufacturer><UDN>{self.ident}</UDN>'
            f'<serviceList><service><serviceType>{self.kind}</serviceType>'
            f'<SCPDURL>/rc.xml</SCPDURL><controlURL>/rc</controlURL></service></serviceList></device></root>',
            '<scpd><serviceStateTable><stateVariable><name>Volume</name><allowedValueRange>'
            '<minimum>0</minimum><maximum>100</maximum></allowedValueRange></stateVariable></serviceStateTable></scpd>',
            f'<s:Envelope xmlns:s="{rb.SOAP}"><s:Body><u:GetVolumeResponse xmlns:u="{self.kind}">'
            f'<CurrentVolume>10</CurrentVolume></u:GetVolumeResponse></s:Body></s:Envelope>',
        )]

    def test_only_master_getvolume_sent_after_identity_and_scale_checks(self):
        with patch.object(rb, "xml_request", side_effect=self.documents()) as http:
            self.assertEqual(rb.upnp_volume(self.host, self.url, self.ident), (10, self.ident, self.url))
            url, host, body, action = http.call_args.args
            self.assertEqual((url, host, action), ("http://192.168.50.164:9197/rc", self.host, self.kind+"#GetVolume"))
            self.assertIn(b"<Channel>Master</Channel>", body)
            self.assertNotIn(b"SetVolume", body)
        with patch.object(rb, "xml_request", side_effect=self.documents()) as http:
            with self.assertRaises(rb.Unavailable):
                rb.upnp_volume(self.host, self.url, "uuid:other")
            self.assertEqual(http.call_count, 1)

    def test_unbound_unknown_scales_and_external_urls_fail_closed(self):
        with self.assertRaises(rb.Unavailable):
            rb.upnp_volume(self.host, self.url, "")
        docs = self.documents()
        docs[1].find(".//maximum").text = "255"
        with patch.object(rb, "xml_request", side_effect=docs) as http:
            with self.assertRaises(rb.Unavailable):
                rb.upnp_volume(self.host, self.url, self.ident)
            self.assertEqual(http.call_count, 2)
        for url in ("http://example.org/rc", "http://localhost/rc", "file:///rc", "http://x@192.168.50.164/rc"):
            with self.assertRaises(rb.Unavailable):
                rb.endpoint(url, self.host)

    def test_binding_requires_confirmation_and_rechecks_identity_and_config(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "config.json"
            saved = b'{"keep": "existing setting"}'
            path.write_bytes(saved)
            with patch.object(rb, "upnp_volume", return_value=(10, self.ident, self.url)) as read:
                self.assertFalse(rb.bind_upnp(path, self.host, self.url, lambda: True, lambda _: "n"))
                self.assertEqual(path.read_bytes(), saved)
                read.reset_mock()
                self.assertTrue(rb.bind_upnp(path, self.host, self.url, lambda: True, lambda _: "y"))
                self.assertEqual(read.call_args.args, (self.host, self.url, self.ident))
                self.assertEqual(rb.json.loads(path.read_text()), dict(keep="existing setting",
                    volume_upnp_ip=self.host, volume_upnp_udn=self.ident, volume_upnp_description=self.url))
                def changed(_):
                    path.write_bytes(saved)
                    return "y"
                with self.assertRaises(rb.Unavailable):
                    rb.bind_upnp(path, self.host, self.url, lambda: True, changed)
                self.assertEqual(path.read_bytes(), saved)


class WindowsDDCTests(unittest.TestCase):
    """Run the Windows handle ownership/targeting code with driver calls stubbed."""
    def native_probe(self, *, source_changes=False, count=1, read_ok=True, maximum=100):
        source = ROOT / "QN990F_Windows_Controller/QN990FController.py"
        nodes = ast.parse(source.read_text()).body
        fn = next(n for n in nodes if getattr(n, "name", "") == "native_ddc_volume")
        user, dx = Mock(), Mock()
        pick = Mock(side_effect=["DISPLAY2", "DISPLAY3" if source_changes else "DISPLAY2"])
        namespace = dict(ctypes=ctypes, wintypes=wintypes, bound_ddc_source=pick)
        exec(compile(ast.Module(body=[fn], type_ignores=[]), str(source), "exec"), namespace)
        def enumerate_monitors(dc, area, visit, param):
            return visit(10, None, None, 0) and visit(20, None, None, 0)
        def info(handle, p):
            p._obj.device = "DISPLAY2" if handle == 20 else "DISPLAY1"
            return True
        def quantity(handle, p):
            self.assertEqual(handle, 20)
            p._obj.value = count
            return True
        def physical(handle, n, p):
            self.assertEqual((handle, n), (20, 1))
            p._obj.handle = 123
            return True
        def read(handle, code, kind, value, top):
            self.assertEqual((handle, code), (123, 0x62))
            value._obj.value, top._obj.value = 10, maximum
            return read_ok
        user.EnumDisplayMonitors.side_effect = enumerate_monitors
        user.GetMonitorInfoW.side_effect = info
        dx.GetNumberOfPhysicalMonitorsFromHMONITOR.side_effect = quantity
        dx.GetPhysicalMonitorsFromHMONITOR.side_effect = physical
        dx.GetVCPFeatureAndVCPFeatureReply.side_effect = read
        with patch.object(ctypes, "WinDLL", create=True, side_effect=[user, dx]), \
             patch.object(ctypes, "WINFUNCTYPE", ctypes.CFUNCTYPE, create=True):
            if count != 1 or not read_ok or maximum != 100 or source_changes:
                with self.assertRaises(ValueError):
                    namespace["native_ddc_volume"]({})
            else:
                self.assertEqual(namespace["native_ddc_volume"]({}), 10)
        if count == 1:
            dx.DestroyPhysicalMonitor.assert_called_once_with(123)
        else:
            dx.GetPhysicalMonitorsFromHMONITOR.assert_not_called()
        dx.SetVCPFeature.assert_not_called()

    def test_bound_display_only_and_handle_cleanup(self):
        for args in ({}, {"source_changes": True}, {"count": 2}, {"read_ok": False}, {"maximum": 254}):
            with self.subTest(args=args):
                self.native_probe(**args)
