#!/usr/bin/env python3
"""opendubs-netmon: reads the robot router every few seconds and writes the network's health for the
dashboard (notes/design/network-router-api.md §3–4).

Read-only. It never writes to the router. Each poll it:
  - reads the router over REST: the uplink's association, signal and rates, the robot Wi-Fi's
    clients, the uplink's DHCP lease, and the interface counters;
  - probes from the Thor itself every PROBE_EVERY seconds: ping the uplink gateway, resolve a
    name, fetch an HTTPS URL, and check for a captive portal;
  - classifies the uplink into one state with one operator sentence;
  - writes it all atomically to OUT (default /run/opendubs-network/network.json);
  - appends uplink events (lost, reassociated, back, internet lost/back) to EVENTS. The router
    keeps its own log only in RAM and loses it at every reboot.

Readers need no credentials: the dashboard reads OUT, and `netdiag` reads both files.

Run as the systemd unit opendubs-netmon.service, which reads /etc/opendubs/network.env (root, 0600)
for ROUTER_URL, ROUTER_USER, ROUTER_PASS and ROUTER_CERT_SHA256. `--once` polls once and prints
the JSON, for a quick look by hand (needs sudo for the env file).
"""
from __future__ import annotations

import argparse
import http.client
import json
import os
import re
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import routeros as ro  # noqa: E402

OUT = os.environ.get("NETMON_OUT", "/run/opendubs-network/network.json")
EVENTS = os.environ.get("NETMON_EVENTS", "/var/lib/opendubs-network/events.jsonl")
POLL_S = float(os.environ.get("NETMON_POLL_S", "5"))
PROBE_EVERY = float(os.environ.get("NETMON_PROBE_S", "15"))
UPLINK_IF = os.environ.get("NETMON_UPLINK_IF", "wlan2")    # the 5 GHz station radio
WIRED_IF = os.environ.get("NETMON_WIRED_IF", "ether1")     # the WAN port, for a dock
AP_IF = os.environ.get("NETMON_AP_IF", "wlan1")            # the robot's own Wi-Fi
CHECK_HOST = os.environ.get("NETMON_CHECK_HOST", "connectivitycheck.gstatic.com")
EVENTS_MAX = 5000

# Thresholds (design note §4). A first guess, tuned on the robot.
WEAK_DBM = -75
WEAK_SNR_DB = 20
BUSY_FRACTION = 0.5     # downstream above half the radio's receive rate: 802.11 delivers ~50–65 %
                        # of its PHY rate, and on 2026-10-19 a full link ran at 31 of 52 Mbit/s
BUSY_POLLS = 2          # ...for two polls in a row (10 s at 5 s polls)
DOWN_PROBE_FAILS = 3    # the HTTPS probe failed this many times in a row
DROP_EVENTS = ("uplink_lost", "uplink_reassociated")


# ---------------------------------------------------------------------------------- the router

def read_router(ros: ro.RouterOS) -> dict:
    """Everything one poll needs, as the router returns it. Raises RouterError if unreachable."""
    raw = {
        "reg": ros.get("interface/wireless/registration-table"),
        "wireless": ros.get("interface/wireless", proplist="name,ssid,mode,disabled,running"),
        "ifaces": ros.get("interface", proplist="name,running,disabled,rx-byte,tx-byte"),
        "dhcpc": ros.get("ip/dhcp-client", proplist="interface,status,address,gateway,disabled"),
        "leases": ros.get("ip/dhcp-server/lease",
                          proplist="address,active-address,mac-address,host-name,comment"),
        "resource": ros.get("system/resource", proplist="uptime,cpu-load,version"),
    }
    # With saved networks (netjoin) the station's own SSID is empty, so ask the radio which network
    # it is on. Optional: an account without the right to run `monitor` still gets everything else.
    try:
        mon = ros.post("interface/wireless/monitor", {"numbers": UPLINK_IF, "once": ""})
        raw["monitor"] = mon[0] if isinstance(mon, list) and mon else mon
    except ro.RouterError:
        raw["monitor"] = None
    return raw


# ---------------------------------------------------------------------------------- probes

def _ping_ms(host: str) -> float | None:
    try:
        r = subprocess.run(["ping", "-c", "1", "-W", "1", "-q", host], capture_output=True,
                           text=True, timeout=3)
    except Exception:
        return None
    m = re.search(r"= [\d.]+/([\d.]+)/", r.stdout)   # rtt min/avg/max/mdev
    return float(m[1]) if r.returncode == 0 and m else None


def _resolves(host: str, timeout: float = 2.0) -> bool:
    ok = []
    t = threading.Thread(target=lambda: ok.append(bool(socket.getaddrinfo(host, 443))), daemon=True)
    t.start()
    t.join(timeout)
    return bool(ok and ok[0])


def _status(host: str, https: bool, timeout: float = 3.0) -> int | None:
    try:
        c = (http.client.HTTPSConnection if https else http.client.HTTPConnection)(host, timeout=timeout)
        c.request("GET", "/generate_204", headers={"User-Agent": "opendubs-netmon"})
        s = c.getresponse().status
        c.close()
        return s
    except Exception:
        return None


def probe(gateway: str | None) -> dict:
    """From the Thor, through the router. `captive` is a portal answering plain HTTP in place of the
    expected empty 204."""
    https = _status(CHECK_HOST, True)
    plain = _status(CHECK_HOST, False)
    return {
        "latency_ms": _ping_ms(gateway) if gateway else None,
        "dns": _resolves(CHECK_HOST),
        "internet": https == 204,
        "captive": plain is not None and plain != 204,
    }


# ---------------------------------------------------------------------------------- summary

def _by(rows, key, val):
    return [r for r in rows or [] if r.get(key) == val]


def _lease_names(leases) -> tuple[dict, dict]:
    by_mac, by_ip = {}, {}
    for l in leases or []:
        name = l.get("comment") or l.get("host-name")
        if not name:
            continue
        if l.get("mac-address"):
            by_mac[l["mac-address"].upper()] = name
        for k in ("active-address", "address"):
            if l.get(k):
                by_ip[l[k]] = name
    return by_mac, by_ip


def summarize(raw: dict | None, probes: dict, prev: dict | None, now: float) -> dict:
    """One poll's facts (no state yet). `prev` is the previous summary, for counter rates."""
    if raw is None:
        return {"router_ok": False, "ts": now, "uplink": {"via": "none"}, "robot_wifi": [],
                "probes": probes}
    up = next((r for r in _by(raw["reg"], "interface", UPLINK_IF)), None)
    dhcp = {d.get("interface"): d for d in raw.get("dhcpc") or []}
    ifaces = {i.get("name"): i for i in raw.get("ifaces") or []}
    wired_lease = dhcp.get(WIRED_IF, {})
    wifi_lease = dhcp.get(UPLINK_IF, {})
    wired = (wired_lease.get("status") == "bound" and ro.truthy(ifaces.get(WIRED_IF, {}).get("running")))
    wifi = up is not None and wifi_lease.get("status") == "bound"
    via = "wired" if wired else "wifi" if wifi else "none"
    iface = WIRED_IF if via == "wired" else UPLINK_IF
    lease = wired_lease if via == "wired" else wifi_lease

    mon = raw.get("monitor") or {}
    wl = next((w for w in _by(raw.get("wireless"), "name", UPLINK_IF)), {})
    network = "wired" if via == "wired" else (mon.get("ssid") or wl.get("ssid") or None)

    counters = {n: (ro.as_int(i.get("rx-byte")), ro.as_int(i.get("tx-byte"))) for n, i in ifaces.items()}
    down = upr = None
    if prev and prev.get("_counters") and prev.get("ts"):
        dt = now - prev["ts"]
        a, b = prev["_counters"].get(iface), counters.get(iface)
        if dt > 0 and a and b and None not in a + b and b[0] >= a[0] and b[1] >= a[1]:
            down = round((b[0] - a[0]) * 8 / dt / 1e6, 2)
            upr = round((b[1] - a[1]) * 8 / dt / 1e6, 2)

    by_mac, by_ip = _lease_names(raw.get("leases"))
    clients = []
    for r in _by(raw["reg"], "interface", AP_IF):
        mac = (r.get("mac-address") or "").upper()
        ip = r.get("last-ip")
        clients.append({
            "name": by_mac.get(mac) or by_ip.get(ip) or "unknown",
            "ip": ip,
            "signal_dbm": ro.signal_dbm(r.get("signal-strength")),
            "ccq_pct": ro.as_int(r.get("tx-ccq")),
            "connected_s": ro.duration_s(r.get("uptime")),
        })
    clients.sort(key=lambda c: (c["signal_dbm"] is None, -(c["signal_dbm"] or 0)))

    if via == "wired":
        since = None
        link = None
    else:
        since = ro.duration_s(up.get("uptime")) if up else None
        link = ro.rate_mbps(up.get("rx-rate")) if up else None
    res = raw.get("resource") or {}
    return {
        "router_ok": True,
        "ts": now,
        "_counters": counters,
        "router": {"ok": True, "uptime_s": ro.duration_s(res.get("uptime")), "cpu_pct": ro.as_int(res.get("cpu-load")),
                   "version": res.get("version")},
        "uplink": {
            "via": via,
            "network": network,
            "ap": up.get("mac-address") if up and via == "wifi" else None,
            "associated": up is not None,
            "signal_dbm": ro.signal_dbm(up.get("signal-strength")) if up and via != "wired" else None,
            "snr_db": ro.as_int(up.get("signal-to-noise")) if up and via != "wired" else None,
            "ccq_pct": ro.as_int(up.get("tx-ccq")) if up and via != "wired" else None,
            "rate_mbps": link,
            "down_mbps": down,
            "up_mbps": upr,
            "since_s": since,
            "address": lease.get("address"),
            "gateway": lease.get("gateway"),
            "clients": len(clients),
        },
        "robot_wifi": clients,
        "probes": probes,
    }


class Streaks:
    """Counters carried between polls, so one bad sample does not flip the state."""

    def __init__(self):
        self.https_fail = 0
        self.busy = 0

    def update(self, s: dict, probed: bool) -> None:
        """`probed`: this poll ran the probes (they run less often than the router is read)."""
        p = s.get("probes") or {}
        if probed and p.get("internet") is True:
            self.https_fail = 0
        elif probed and p.get("internet") is False:
            self.https_fail += 1
        u = s["uplink"]
        if u.get("down_mbps") is not None and u.get("rate_mbps"):
            busy = u["down_mbps"] > BUSY_FRACTION * u["rate_mbps"]
            self.busy = self.busy + 1 if busy else 0


def classify(s: dict, streaks: Streaks) -> tuple[str, str]:
    """The uplink's one state and the sentence the operator reads. First match wins (§4)."""
    if not s.get("router_ok"):
        if s.get("router_refused"):
            # Seen at the first setup (2026-10-20): reachable, but "can't reach" sent people to the
            # cables. The state stays NO_ROUTER (no uplink data either way); the sentence says why.
            return "NO_ROUTER", "The router refused netmon's login: check the netmon account and /etc/opendubs/network.env"
        return "NO_ROUTER", "Can't reach the robot's router"
    u, p = s["uplink"], s.get("probes") or {}
    net = u.get("network") or "the network"
    if u["via"] == "none":
        if u.get("associated"):
            return "DOWN", f"No uplink: joined {net} but got no address"
        return "DOWN", "No uplink: not connected to any network"
    if streaks.https_fail >= DOWN_PROBE_FAILS and not p.get("captive"):
        return "DOWN", f"No internet through {net}"
    if p.get("captive"):
        return "CAPTIVE", f"{net} wants its sign-in page accepted: open any web page on the robot Wi-Fi"
    if streaks.busy >= BUSY_POLLS:
        return "BUSY", f"Uplink busy: {u['down_mbps']:.0f} of ~{u['rate_mbps']:.0f} Mbit/s in use on {net}"
    if u["via"] == "wifi":
        sig, snr = u.get("signal_dbm"), u.get("snr_db")
        if (sig is not None and sig < WEAK_DBM) or (snr is not None and snr < WEAK_SNR_DB):
            return "WEAK", f"Uplink weak: {sig} dBm on {net}"
        return "GOOD", f"Uplink good: {sig} dBm on {net}"
    return "GOOD", "Uplink good: wired"


# ---------------------------------------------------------------------------------- events

def events_between(prev: dict | None, cur: dict) -> list[dict]:
    """Uplink changes worth keeping. A reassociation shows as the association's uptime going back or
    the access point changing, which also catches a drop shorter than one poll."""
    if not prev:
        return []
    out = []
    t = cur["ts"]
    pu, cu = prev.get("uplink") or {}, cur.get("uplink") or {}
    if prev.get("router_ok") and not cur.get("router_ok"):
        out.append({"ts": t, "event": "router_unreachable"})
    elif not prev.get("router_ok") and cur.get("router_ok"):
        out.append({"ts": t, "event": "router_back"})
    if not cur.get("router_ok") or not prev.get("router_ok"):
        return out
    if pu.get("via") != "none" and cu.get("via") == "none":
        out.append({"ts": t, "event": "uplink_lost", "network": pu.get("network")})
    elif pu.get("via") == "none" and cu.get("via") != "none":
        out.append({"ts": t, "event": "uplink_up", "via": cu["via"], "network": cu.get("network")})
    elif (cu.get("via") == "wifi" and pu.get("via") == "wifi"
          and ((cu.get("since_s") is not None and pu.get("since_s") is not None
                and cu["since_s"] < pu["since_s"]) or cu.get("ap") != pu.get("ap"))):
        out.append({"ts": t, "event": "uplink_reassociated", "network": cu.get("network"),
                    "ap_from": pu.get("ap"), "ap_to": cu.get("ap")})
    elif cu.get("via") != pu.get("via"):
        out.append({"ts": t, "event": "uplink_switched", "from": pu.get("via"), "to": cu.get("via"),
                    "network": cu.get("network")})
    pi, ci = (prev.get("probes") or {}).get("internet"), (cur.get("probes") or {}).get("internet")
    if pi is True and ci is False:
        out.append({"ts": t, "event": "internet_lost"})
    elif pi is False and ci is True:
        out.append({"ts": t, "event": "internet_back"})
    return out


def read_events(path: str = EVENTS, since: float = 0.0) -> list[dict]:
    try:
        with open(path) as f:
            rows = [json.loads(l) for l in f if l.strip()]
    except (OSError, ValueError):
        return []
    return [r for r in rows if r.get("ts", 0) >= since]


def append_events(evs: list[dict], path: str = EVENTS) -> None:
    if not evs:
        return
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    with open(p, "a") as f:
        for e in evs:
            f.write(json.dumps(e) + "\n")
    lines = p.read_text().splitlines()
    if len(lines) > EVENTS_MAX:
        p.write_text("\n".join(lines[-EVENTS_MAX:]) + "\n")


def drops_since(evs: list[dict], since: float) -> int:
    return sum(1 for e in evs if e.get("event") in DROP_EVENTS and e.get("ts", 0) >= since)


# ---------------------------------------------------------------------------------- output

def public(s: dict, state: str, say: str, drops_24h: int) -> dict:
    """The file's shape: the contract in notes/design/network-router-api.md §4."""
    u = dict(s.get("uplink") or {})
    p = s.get("probes") or {}
    u.update({"state": state, "say": say, "drops_24h": drops_24h,
              "internet": p.get("internet"), "captive": p.get("captive"),
              "latency_ms": p.get("latency_ms")})
    return {"ts": s["ts"], "router": s.get("router") or {"ok": False},
            "uplink": u, "robot_wifi": s.get("robot_wifi") or []}


def write_atomic(obj: dict, path: str = OUT) -> None:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(obj, indent=1))
    os.chmod(tmp, 0o644)
    os.replace(tmp, p)


def poll_once(ros: ro.RouterOS | None, prev: dict | None, streaks: Streaks, last_probe: dict,
              do_probe: bool) -> dict:
    now = time.time()
    refused = False
    try:
        raw = read_router(ros) if ros else None
    except ro.RouterError as e:
        print(f"netmon: {e}", file=sys.stderr)
        raw = None
        refused = e.status in (401, 403)   # reachable, but the netmon login is wrong or missing
    gw = None
    if raw:
        for d in raw.get("dhcpc") or []:
            if d.get("status") == "bound" and d.get("gateway"):
                gw = d["gateway"]
                break
    probes = probe(gw) if do_probe else last_probe
    s = summarize(raw, probes, prev, now)
    if refused:
        s["router_refused"] = True
    streaks.update(s, do_probe)
    return s


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--env", default=ro.ENV_FILE, help="credentials file (default %(default)s)")
    ap.add_argument("--once", action="store_true", help="poll once, print the JSON, write nothing")
    a = ap.parse_args()

    # systemd hands the file's values over as environment variables; by hand, read the file.
    try:
        env = ro.read_env(a.env)
    except (FileNotFoundError, PermissionError):   # under systemd the file is root-only: fine
        env = {}
    env.update({k: v for k, v in os.environ.items() if k.startswith("ROUTER_")})
    ros = ro.RouterOS.from_env(env)
    streaks, prev, last_probe, next_probe = Streaks(), None, {}, 0.0

    while True:
        do_probe = time.time() >= next_probe
        s = poll_once(ros, prev, streaks, last_probe, do_probe)
        if do_probe:
            last_probe, next_probe = s["probes"], time.time() + PROBE_EVERY
        state, say = classify(s, streaks)
        if a.once:
            print(json.dumps(public(s, state, say, 0), indent=1))
            return 0
        evs = events_between(prev, s)
        append_events(evs)
        day = read_events(since=s["ts"] - 86400)
        write_atomic(public(s, state, say, drops_since(day, s["ts"] - 86400)))
        prev = s
        time.sleep(max(0.5, POLL_S - (time.time() - s["ts"])))


if __name__ == "__main__":
    sys.exit(main())
