"""Read TV volume only. Kept identical in the two standalone installer folders."""
import http.client
import ipaddress
import json
import os
from pathlib import Path
import re
import socket
import subprocess
import tempfile
import threading
import time
from urllib.parse import urljoin, urlsplit
import xml.etree.ElementTree as ET


class Unavailable(ValueError):
    pass


def lan_host(value):
    ip = ipaddress.IPv4Address(value)
    if not any(ip in net for net in PRIVATE_LANS):
        raise Unavailable("Expected the TV's private LAN IPv4 address")
    return str(ip)


PRIVATE_LANS = tuple(map(ipaddress.ip_network, ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16")))
RC = "urn:schemas-upnp-org:service:RenderingControl:"
SOAP = "http://schemas.xmlsoap.org/soap/envelope/"


def endpoint(url, host):
    p = urlsplit(url)
    if (p.scheme != "http" or p.hostname != host or p.username is not None
            or p.password is not None or p.fragment or not (0 < (p.port or 80) < 65536)):
        raise Unavailable("UPnP URL is not on the configured TV")
    return p


def xml_request(url, host, body=None, action=None):
    p = endpoint(url, host)
    conn = http.client.HTTPConnection(host, p.port or 80, timeout=1.2)
    try:
        headers = {"Content-Type": 'text/xml; charset="utf-8"', "SOAPACTION": '"' + action + '"'} if body else {}
        conn.request("POST" if body else "GET", (p.path or "/") + ("?" + p.query if p.query else ""), body, headers)
        response = conn.getresponse()
        if response.status != 200:
            raise Unavailable(f"UPnP HTTP {response.status}")
        raw = response.read(131073)
        if len(raw) > 131072 or b"\0" in raw or b"<!DOCTYPE" in raw.upper() or b"<!ENTITY" in raw.upper():
            raise Unavailable("Invalid UPnP document")
        return ET.fromstring(raw)
    finally:
        conn.close()


def discover(host):
    # Use normal routing; accept responses/URLs only from the configured TV.
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, 1)
        for destination in (host, "239.255.255.250"):
            request = (f'M-SEARCH * HTTP/1.1\r\nHOST: {destination}:1900\r\n'
                       f'MAN: "ssdp:discover"\r\nMX: 1\r\nST: {RC}1\r\n\r\n').encode("ascii")
            sock.sendto(request, (destination, 1900))
            until = time.monotonic() + 1.25
            for _ in range(32):
                remaining = until - time.monotonic()
                if remaining <= 0:
                    break
                sock.settimeout(remaining)
                try:
                    data, sender = sock.recvfrom(8192)
                except socket.timeout:
                    break
                if sender[0] == host:
                    location = re.search(rb"(?im)^location:[ \t]*([^\r\n]+)", data)
                    if location:
                        url = location[1].decode("ascii").strip()
                        endpoint(url, host)
                        return url
    raise Unavailable("TV did not advertise a RenderingControl description")


def upnp_volume(host, description, udn, *, inspect=False):
    host = lan_host(host)
    if not udn and not inspect:
        raise Unavailable("UPnP TV identity has not been bound")
    url = description or discover(host)
    root = xml_request(url, host)
    base = root.findtext("{*}URLBase") or url
    endpoint(base, host)
    found = []
    for device in root.iterfind(".//{*}device"):
        if not (device.findtext("{*}deviceType") or "").startswith("urn:schemas-upnp-org:device:MediaRenderer:"):
            continue
        if "samsung" not in (device.findtext("{*}manufacturer") or "").lower():
            continue
        identity = (device.findtext("{*}UDN") or "").strip()
        if not identity or (udn and identity.casefold() != udn.casefold()):
            continue
        for svc in device.findall("{*}serviceList/{*}service"):
            kind = svc.findtext("{*}serviceType") or ""
            if re.fullmatch(re.escape(RC) + r"[1-9]\d*", kind):
                found.append((identity, kind, svc))
    if len(found) != 1:
        raise Unavailable("No unique matching Samsung RenderingControl service")
    identity, kind, svc = found[0]
    def service_url(field):
        path = svc.findtext("{*}" + field)
        if not path:
            raise Unavailable("Incomplete UPnP service description")
        return urljoin(base, path)
    scpd = xml_request(service_url("SCPDURL"), host)
    ranges = [v.find("{*}allowedValueRange") for v in scpd.findall("{*}serviceStateTable/{*}stateVariable")
              if v.findtext("{*}name") == "Volume"]
    if len(ranges) != 1 or ranges[0] is None or (
        ranges[0].findtext("{*}minimum"), ranges[0].findtext("{*}maximum"),
        ranges[0].findtext("{*}step", "1")) != ("0", "100", "1"):
        raise Unavailable("UPnP volume is not on a known 0-100 scale")
    envelope = (f'<s:Envelope xmlns:s="{SOAP}"><s:Body><u:GetVolume xmlns:u="{kind}">'
                '<InstanceID>0</InstanceID><Channel>Master</Channel></u:GetVolume></s:Body></s:Envelope>')
    result = xml_request(service_url("controlURL"), host, envelope.encode(), kind + "#GetVolume")
    value = result.findtext(f"{{{SOAP}}}Body/{{{kind}}}GetVolumeResponse/{{*}}CurrentVolume")
    if value is None or not re.fullmatch(r"[0-9]{1,3}", value) or int(value) > 100:
        raise Unavailable("Invalid UPnP volume reply")
    return int(value), identity, url


def helper_number(args, timeout):
    result = subprocess.run(args, stdin=subprocess.DEVNULL, capture_output=True, timeout=timeout,
                            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    number = result.stdout.strip()
    if result.returncode or not re.fullmatch(rb"[0-9]{1,3}", number):
        raise Unavailable("DDC read unsupported or timed out")
    return int(number)


def bind_upnp(path, host, description, allowed, confirm=input):
    """Explicitly pin the inspected renderer without editing any other setting."""
    before = path.read_bytes()
    settings = json.loads(before.decode("utf-8-sig"))
    value, udn, url = upnp_volume(host, description, "", inspect=True)
    print(f"Samsung renderer at {url}\nIdentity: {udn}\nReported volume: {value}")
    if confirm("Bind this exact TV for LAN volume reads? [y/N] ").strip().lower() != "y":
        return False
    if not allowed():
        raise Unavailable("HDMI or picture state changed")
    upnp_volume(host, url, udn)  # Recheck the identity after confirmation.
    settings.update(volume_upnp_ip=host, volume_upnp_udn=udn, volume_upnp_description=url)
    fd, temporary = tempfile.mkstemp(prefix="volume-bind.", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(settings, stream, indent=2, ensure_ascii=False)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        if path.read_bytes() != before or not allowed():
            raise Unavailable("Configuration or connection changed; not saved")
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)
    return True


def mac_ddc(config, bindings, app_dir):
    expected = config.get("tv_hdmi_edid_sha256")
    matches = [d for d in bindings() if expected and d["edid_sha256"] == expected]
    if len(matches) != 1:
        raise Unavailable("No unique bound HDMI TV")
    paths = [Path(app_dir) / "m1ddc", Path("/opt/homebrew/bin/m1ddc"), Path("/usr/local/bin/m1ddc")]
    tool = next((str(p) for p in paths if p.is_file()), None)
    if not tool:
        raise Unavailable("m1ddc is not installed")
    dev = matches[0]
    args = [tool, "display", "basic={vendor}:{product}:{serial}".format(**dev)]
    if helper_number(args + ["max", "volume"], 2) != 100:
        raise Unavailable("Unsupported DDC speaker volume scale")
    return helper_number(args + ["get", "volume"], 2)


class VolumeReadback:
    """One background caller. Lower-priority paths are tried only if needed."""
    def __init__(self, ddc, upnp, cloud, refresh, allowed, stop, logger):
        self.direct = (("HDMI DDC", ddc), ("LAN UPnP", upnp))
        self.cloud, self.refresh, self.allowed = cloud, refresh, allowed
        self.stop, self.logger = stop, logger
        self.retry_at = {}
        self.refresh_after = 0.0
        self.previous_direct = {}
        self.checked_write = {}
        self.disputed = {}

    def read(self, fence, demand=False, *, known=None, last_set=None, valid=lambda: True, after_write=False):
        def allowed():
            return not self.stop.is_set() and valid() and self.allowed()
        def recent(report):
            now = time.time()
            return report and fence < report[1] <= now and now - report[1] <= 120
        for name, getter in self.direct:
            if not allowed():
                return None
            if getter is None or time.monotonic() < self.retry_at.get(name, 0):
                continue
            started = time.time()
            try:
                value = getter()
                if type(value) is not int or not 0 <= value <= 100:
                    raise Unavailable("Invalid volume value")
                if started >= fence and allowed():
                    # A DDC/UPnP implementation can return a fixed number too.
                    # If it did not reflect a subsequent local write, quarantine
                    # that number until this provider demonstrates a change.
                    if last_set and self.checked_write.get(name) != last_set[1]:
                        self.checked_write[name] = last_set[1]
                        self.disputed.pop(name, None)
                        if value != last_set[0] and self.previous_direct.get(name, value) == value:
                            self.disputed[name] = value
                            self.logger.info("%s volume %d disagrees with last set %d; trying other readers",
                                             name, value, last_set[0])
                    self.previous_direct[name] = value
                    if self.disputed.get(name) == value:
                        continue
                    self.disputed.pop(name, None)
                    return value, started, name
            except (OSError, ValueError, RuntimeError, ET.ParseError, http.client.HTTPException, subprocess.SubprocessError) as exc:
                self.retry_at[name] = time.monotonic() + 300
                self.logger.info("%s volume unavailable (%s); retry in 5 min", name, type(exc).__name__)
        if not self.cloud or not allowed():
            return None
        report = self.cloud()
        if recent(report) and allowed():
            return (*report, "SmartThings")
        if report and report == known and 0 <= time.time() - report[1] <= 120:
            return None  # The same still-current report doesn't warrant a refresh.
        # A completed user adjustment has its own coalesced, rate-limited
        # feedback schedule. A recent menu open must not suppress that feedback.
        if not (demand and self.refresh and allowed() and (after_write or time.monotonic() >= self.refresh_after)):
            return None
        self.refresh_after = time.monotonic() + 300
        requested = time.time()
        if not self.refresh(allowed):
            return None
        for delay in (1, 2):
            if self.stop.wait(delay) or not allowed():
                return None
            report = self.cloud()
            if recent(report) and allowed():
                if report[1] >= requested:
                    return (*report, "SmartThings refresh")
                if after_write:
                    # An update from after the write but just before refresh is
                    # also independent evidence. Do not label it refresh-generated.
                    return (*report, "SmartThings")
        return None


def menu_read_requested(directory, session, previous):
    """Only consume recent menu-open signals addressed to this daemon session."""
    if directory is None:
        return False, previous
    path = directory / "volume-read.request"
    try:
        stat = path.stat()
        key = stat.st_mtime_ns
        # NTFS modification timestamps can lead Python's wall clock by a
        # timer tick. Allow a small skew while rejecting stale requests.
        if key == previous or stat.st_size > 128 or not -2 <= time.time() - stat.st_mtime <= 10:
            return False, key
        return path.read_text(encoding="utf-8").strip() == session, key
    except (OSError, UnicodeError):
        return False, previous


def probe_paths(child, client, allowed, logger):
    """Manual independent checks: even a working DDC path doesn't hide other results."""
    results = {}
    for name, flag in (("ddc", "--read-ddc-volume"), ("upnp", "--read-upnp-volume")):
        if not allowed():
            results[name] = {"status": "inactive"}
            continue
        try:
            value = helper_number(child + [flag], 6)
            if not 0 <= value <= 100 or not allowed():
                raise Unavailable("Unverified reading")
            results[name] = {"value": value}
        except (OSError, ValueError, subprocess.SubprocessError):
            results[name] = {"status": "unavailable or not configured"}
    cloud = getattr(client, "read_cloud_volume", None)
    mux = VolumeReadback(None, None, cloud, getattr(client, "request_volume_refresh", None),
                         allowed, threading.Event(), logger)
    result = mux.read(time.time() - 120, demand=True)
    results["cloud"] = ({"value": result[0], "reported_at": result[1], "source": result[2]}
                        if result else {"status": "no fresh report"})
    return results
