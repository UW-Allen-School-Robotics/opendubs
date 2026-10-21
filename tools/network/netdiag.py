#!/usr/bin/env python3
"""netdiag: when the robot's network acts up, find the first layer that fails.

    sudo tools/network/netdiag.py          # on the Thor (sudo: it reads /etc/opendubs/network.env)
    sudo tools/network/netdiag.py --json   # the same, for a ticket or a chat

It walks the layers bottom-up and puts a one-line verdict first:
  1. Thor to router: ping, then the REST login (and the certificate pin, over HTTPS)
  2. Router to site: associated or not, which network and access point, signal, SNR, CCQ, rate,
     time since this association, and recent uplink drops (the poller's history and the router's log)
  3. Uplink address: DHCP lease, gateway, DNS, and an uplink subnet that collides with the robot LAN
  4. Internet: gateway ping, name resolution, HTTPS, and a captive-portal check
  5. Load: traffic now against the radio's rate (two reads 2 s apart; it never sends a speed test)
  6. The robot's own Wi-Fi: each client's signal, with the weak ones flagged

Read-only: it never writes to the router. Design: notes/design/network-router-api.md §5.
"""
from __future__ import annotations

import argparse
import ipaddress
import json
import os
import sys
import time
import urllib.parse
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import netmon  # noqa: E402
import routeros as ro  # noqa: E402

ROBOT_LAN = ipaddress.ip_network(os.environ.get("NETMON_ROBOT_LAN", "10.42.2.0/24"))
WEAK_CLIENT_DBM = -70


def _fmt_s(s):
    if s is None:
        return "?"
    d, s = divmod(int(s), 86400)
    h, s = divmod(s, 3600)
    m, s = divmod(s, 60)
    return (f"{d}d" if d else "") + (f"{h}h" if h or d else "") + f"{m}m{s:02d}s"


def diagnose(ros: ro.RouterOS, router_host: str) -> dict:
    """Every layer's findings, and the first failure. Never raises for a network fault."""
    rep = {"layers": [], "verdict": None}

    def layer(name, ok, lines, fail=None):
        rep["layers"].append({"layer": name, "ok": ok, "lines": lines})
        if not ok and rep["verdict"] is None:
            rep["verdict"] = fail or f"{name}: failed"

    # 1. Thor -> router
    ping = netmon._ping_ms(router_host)
    try:
        raw = netmon.read_router(ros)
        err = None
    except ro.RouterError as e:
        raw, err = None, str(e)
    lines = [f"ping {router_host}: " + (f"{ping:.1f} ms" if ping is not None else "no reply")]
    lines.append("REST: " + ("ok" if raw else err))
    if raw is None:
        why = ("the router does not answer at all: check its power and the Thor's cable"
               if ping is None else f"the router answers ping but REST failed: {err}")
        layer("Thor to router", False, lines, f"Can't read the router: {why}")
        return rep
    layer("Thor to router", True, lines)

    # 2. router -> site
    s1 = netmon.summarize(raw, {}, None, time.time())
    u = s1["uplink"]
    lines = []
    if u["via"] == "wired":
        lines.append(f"wired uplink on {netmon.WIRED_IF}")
    elif u["associated"]:
        lines.append(f"joined {u['network'] or '?'} via access point {u['ap']}")
        q = lambda v, unit: "?" if v is None else f"{v} {unit}"
        lines.append(f"signal {q(u['signal_dbm'], 'dBm')}, SNR {q(u['snr_db'], 'dB')}, "
                     f"CCQ {q(u['ccq_pct'], '%')}, receive rate {q(u['rate_mbps'], 'Mbit/s')}, "
                     f"for {_fmt_s(u['since_s'])}")
    else:
        lines.append(f"{netmon.UPLINK_IF} is not associated with any network")
    day = netmon.read_events(since=time.time() - 86400)
    lines.append(f"uplink drops in the last 24 h (the poller's history): {netmon.drops_since(day, 0)}"
                 + ("" if day or os.path.exists(netmon.EVENTS) else "  (the poller isn't running)"))
    try:
        log = ros.get("log", proplist="time,topics,message")
        ups = [l for l in log or [] if "wireless" in (l.get("topics") or "")
               and netmon.UPLINK_IF in (l.get("message") or "")][-6:]
        if ups:
            lines.append("router log (its clock may be wrong until time sync is on):")
            lines += [f"  {l.get('time')}  {l.get('message')}" for l in ups]
    except ro.RouterError:
        pass
    sig, snr = u.get("signal_dbm"), u.get("snr_db")
    if u["via"] == "none" and not u["associated"]:
        layer("Router to site", False, lines,
              "The router is not connected to any uplink network: move the robot, or plug in a cable")
        return rep
    weak = u["via"] == "wifi" and ((sig is not None and sig < netmon.WEAK_DBM)
                                   or (snr is not None and snr < netmon.WEAK_SNR_DB))
    layer("Router to site", True, lines + (["WEAK: expect drops and slow transfers"] if weak else []))

    # 3. uplink address
    lease = (s1["uplink"].get("address") or "")
    lines = [f"address {lease or 'none'}, gateway {u.get('gateway') or 'none'}"]
    overlap = False
    if lease:
        try:
            overlap = ipaddress.ip_interface(lease).network.overlaps(ROBOT_LAN)
        except ValueError:
            pass
    if overlap:
        lines.append(f"the uplink's subnet overlaps the robot LAN {ROBOT_LAN}")
    if u["via"] == "none" or overlap:
        layer("Uplink address", False, lines,
              "The uplink gave no address" if not overlap else
              f"This network uses the robot's own subnet {ROBOT_LAN}: routing can't work here")
        return rep
    layer("Uplink address", True, lines)

    # 4. internet
    p = netmon.probe(u.get("gateway"))
    lines = [f"gateway ping: {p['latency_ms']} ms" if p["latency_ms"] is not None else "gateway ping: no reply (some networks drop it)",
             f"name resolution: {'ok' if p['dns'] else 'FAILED'}",
             f"HTTPS: {'ok' if p['internet'] else 'FAILED'}",
             f"captive portal: {'YES' if p['captive'] else 'no'}"]
    if p["captive"]:
        layer("Internet", False, lines,
              f"{u['network']} has a sign-in page: open any web page from a device on the robot Wi-Fi and accept it")
        return rep
    if not p["internet"]:
        layer("Internet", False, lines, "Connected, but no internet: "
              + ("names don't resolve" if not p["dns"] else "HTTPS fails (the network may block it)"))
        return rep
    layer("Internet", True, lines)

    # 5. load
    time.sleep(2)
    try:
        s2 = netmon.summarize(netmon.read_router(ros), p, s1, time.time())
        u2 = s2["uplink"]
        lines = [f"now: {u2['down_mbps']} Mbit/s down, {u2['up_mbps']} Mbit/s up"
                 + (f" (radio receive rate {u2['rate_mbps']} Mbit/s)" if u2.get("rate_mbps") else "")]
        busy = (u2.get("down_mbps") is not None and u2.get("rate_mbps")
                and u2["down_mbps"] > netmon.BUSY_FRACTION * u2["rate_mbps"])
        layer("Load", True, lines + (["BUSY: something is downloading; interactive traffic will lag"] if busy else []))
    except ro.RouterError as e:
        s2, busy = s1, False
        layer("Load", True, [f"not measured: {e}"])

    # 6. robot Wi-Fi
    cl = s2["robot_wifi"]
    lines = [f"{c['name']:<20} {c['ip'] or '?':<15} {c['signal_dbm']} dBm  CCQ {c['ccq_pct']} %"
             + ("  WEAK" if c["signal_dbm"] is not None and c["signal_dbm"] < WEAK_CLIENT_DBM else "")
             for c in cl] or ["no clients"]
    layer("Robot Wi-Fi", True, lines)

    if rep["verdict"] is None:
        n = netmon.drops_since(day, 0)
        tail = f", {n} drop{'s' if n != 1 else ''} in 24 h" if n else ""
        if busy:
            rep["verdict"] = f"Working, but the uplink is busy{tail}"
        elif weak:
            rep["verdict"] = f"Working, but the uplink is weak: {sig} dBm{tail}"
        else:
            rep["verdict"] = "The network is working" + (f": {sig} dBm on {u['network']}" if sig else "") + tail
    return rep


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--env", default=ro.ENV_FILE)
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args()
    try:
        env = ro.read_env(a.env)
    except PermissionError:
        print(f"netdiag: can't read {a.env}: run it with sudo", file=sys.stderr)
        return 2
    except FileNotFoundError:
        print(f"netdiag: {a.env} is missing: the read-only router account isn't set up "
              "(tools/network/README.md)", file=sys.stderr)
        return 2
    ros = ro.RouterOS.from_env(env)
    rep = diagnose(ros, urllib.parse.urlsplit(env["ROUTER_URL"]).hostname)
    if a.json:
        print(json.dumps(rep, indent=1))
        return 0
    print(rep["verdict"])
    print()
    for l in rep["layers"]:
        print(f"[{'ok' if l['ok'] else 'FAIL'}] {l['layer']}")
        for line in l["lines"]:
            print(f"      {line}")
    return 0 if all(l["ok"] for l in rep["layers"]) else 1


if __name__ == "__main__":
    sys.exit(main())
