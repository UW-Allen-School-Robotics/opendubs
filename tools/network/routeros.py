"""A small client for the robot router's REST API (MikroTik RouterOS 7), standard library only.

RouterOS serves REST at <base>/rest/<menu path> through its web service. It authenticates with
HTTP Basic auth, accepts and returns JSON, and encodes every value as a string. Reads are GET;
create is PUT, change is PATCH, delete is DELETE; commands such as `move` or `monitor` are POST.
There are no continuous commands: `monitor` takes `"once": ""`, and every request times out on the
router after 60 s. Reference: https://help.mikrotik.com/docs/spaces/ROS/pages/47579162/REST+API

The router's HTTPS certificate is self-signed, so instead of trusting a CA the caller pins its
SHA-256 fingerprint (ROUTER_CERT_SHA256). Plain HTTP works too: the admin page the team keeps is on
HTTP, so the router serves REST there as well, but then the password crosses the robot Wi-Fi in
clear. Callers say which they accept.

Credentials come from a robot-local file, never the repository (notes/design/network-router-api.md
§2). That file is root-only, so anything reading it runs under sudo or as a systemd unit.
"""
from __future__ import annotations

import base64
import hashlib
import http.client
import json
import re
import ssl
import urllib.parse

ENV_FILE = "/etc/opendubs/network.env"


class RouterError(Exception):
    """The router refused or failed a request. `status` is the HTTP status, 0 when unreachable."""

    def __init__(self, message: str, status: int = 0):
        super().__init__(message)
        self.status = status


def read_env(path: str = ENV_FILE) -> dict:
    """KEY=VALUE lines, as systemd's EnvironmentFile reads them. Quotes around a value are removed."""
    out = {}
    with open(path) as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            v = v.strip()
            if len(v) >= 2 and v[0] == v[-1] and v[0] in "'\"":
                v = v[1:-1]
            out[k.strip()] = v
    return out


class RouterOS:
    def __init__(self, url: str, user: str, password: str, cert_sha256: str | None = None,
                 timeout: float = 5.0):
        u = urllib.parse.urlsplit(url)
        if u.scheme not in ("http", "https") or not u.hostname:
            raise ValueError(f"router URL must be http(s)://host, got {url!r}")
        self.scheme, self.host, self.port = u.scheme, u.hostname, u.port
        self.secure = u.scheme == "https"
        self.pin = (cert_sha256 or "").replace(":", "").lower() or None
        self.timeout = timeout
        self._auth = "Basic " + base64.b64encode(f"{user}:{password}".encode()).decode()

    @classmethod
    def from_env(cls, env: dict, **kw) -> "RouterOS":
        return cls(env["ROUTER_URL"], env["ROUTER_USER"], env["ROUTER_PASS"],
                   env.get("ROUTER_CERT_SHA256"), **kw)

    def _connect(self) -> http.client.HTTPConnection:
        if not self.secure:
            return http.client.HTTPConnection(self.host, self.port or 80, timeout=self.timeout)
        # Self-signed: no CA and no host name to check. The pin below is the check, and without a
        # pin an HTTPS connection is refused rather than silently trusted.
        if not self.pin:
            raise RouterError("HTTPS needs ROUTER_CERT_SHA256 (the router's certificate fingerprint)")
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        conn = http.client.HTTPSConnection(self.host, self.port or 443, timeout=self.timeout,
                                           context=ctx)
        conn.connect()
        der = conn.sock.getpeercert(binary_form=True)
        got = hashlib.sha256(der).hexdigest()
        if got != self.pin:
            conn.close()
            raise RouterError(f"router certificate fingerprint {got} does not match the pin")
        return conn

    def request(self, method: str, path: str, body=None, query: dict | None = None):
        url = "/rest/" + "/".join(urllib.parse.quote(p, safe="") for p in path.strip("/").split("/"))
        if query:
            url += "?" + urllib.parse.urlencode(query)
        headers = {"Authorization": self._auth, "Accept": "application/json"}
        data = None
        if body is not None:
            data = json.dumps(body).encode()
            headers["Content-Type"] = "application/json"
        try:
            conn = self._connect()
            try:
                conn.request(method, url, body=data, headers=headers)
                resp = conn.getresponse()
                raw = resp.read()
            finally:
                conn.close()
        except RouterError:
            raise
        except (OSError, http.client.HTTPException) as e:
            raise RouterError(f"router unreachable at {self.host}: {e}") from e
        if resp.status == 401:
            raise RouterError("router refused the credentials (401)", 401)
        if resp.status >= 400:
            try:
                detail = json.loads(raw).get("detail") or json.loads(raw).get("message")
            except Exception:
                detail = raw[:200].decode(errors="replace")
            raise RouterError(f"{method} {path}: {resp.status} {detail}", resp.status)
        return json.loads(raw) if raw.strip() else None

    def get(self, path: str, proplist: str | None = None, **filters):
        q = dict(filters)
        if proplist:
            q[".proplist"] = proplist
        return self.request("GET", path, query=q or None)

    def put(self, path: str, body: dict):
        return self.request("PUT", path, body)

    def patch(self, path: str, item_id: str, body: dict):
        return self.request("PATCH", f"{path}/{item_id}", body)

    def delete(self, path: str, item_id: str):
        return self.request("DELETE", f"{path}/{item_id}")

    def post(self, path: str, body: dict):
        return self.request("POST", path, body)


# ---- RouterOS value formats. Every REST value is a string; these turn the ones we use into numbers.

def as_int(v, default=None):
    try:
        return int(str(v).strip())
    except (TypeError, ValueError):
        return default


def signal_dbm(v):
    """'-83@12Mbps', '-83@HT20-2' or '-83' -> -83."""
    if v is None:
        return None
    return as_int(str(v).split("@", 1)[0])


_DUR = re.compile(r"(\d+)(w|d|h|ms|m|s)")
_DUR_S = {"w": 604800, "d": 86400, "h": 3600, "m": 60, "s": 1, "ms": 0.001}


def duration_s(v):
    """'1h43m36s', '2d3h', '1w2d03:04:05' or '00:05:00' -> seconds."""
    if v is None:
        return None
    s = str(v).strip()
    total = 0.0
    m = re.search(r"(\d+):(\d\d):(\d\d)$", s)
    if m:
        total += int(m[1]) * 3600 + int(m[2]) * 60 + int(m[3])
        s = s[:m.start()]
    found = False
    for n, unit in _DUR.findall(s):
        total += int(n) * _DUR_S[unit]
        found = True
    if not found and not m:
        return None
    return int(total)


def rate_mbps(v):
    """'52Mbps-20MHz/1S', '144.4Mbps-20MHz/2S/SGI', '5.5Mbps', '1Gbps' -> Mbit/s."""
    if not v:
        return None
    m = re.match(r"([\d.]+)\s*([GMk])bps", str(v))
    if not m:
        return None
    return float(m[1]) * {"G": 1000.0, "M": 1.0, "k": 0.001}[m[2]]


def pair(v):
    """'506076593,23896892180' -> (506076593, 23896892180)."""
    if not v:
        return (None, None)
    a, _, b = str(v).partition(",")
    return (as_int(a), as_int(b))


def truthy(v) -> bool:
    return str(v).lower() in ("true", "yes")
