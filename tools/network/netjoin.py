#!/usr/bin/env python3
"""netjoin: add, order and switch the robot router's uplink networks without the admin page.

    sudo tools/network/netjoin.py list                  saved networks, in priority order
    sudo tools/network/netjoin.py status                which network the uplink is on now
    sudo tools/network/netjoin.py init                  ONE TIME: turn the fixed uplink into a saved list
    sudo tools/network/netjoin.py add "Venue WiFi"      asks for its passphrase (--open for none)
    sudo tools/network/netjoin.py prefer "Venue WiFi"   move it to the top (--now to switch at once)
    sudo tools/network/netjoin.py disable|enable|remove "Venue WiFi"
    sudo tools/network/netjoin.py scan                  visible 5 GHz networks (interrupts the uplink)
    sudo tools/network/netjoin.py wired-first           a cable into ether1 wins over Wi-Fi

How it works (notes/design/network-uplinks.md §3):
- The router's station radio keeps a connect list: one entry per saved network, each with its own
  security profile. With the radio's own SSID left empty, the station joins the saved network
  highest in the list that is in range ("only the first matching rule is applied").
- `init` does the one-time switch from today's fixed SSID. It also sets `default-authentication=no`,
  so the robot never joins an unknown open network by itself.

What it may touch, and nothing else:
- Connect-list entries and security profiles that carry the comment `netjoin`.
- The uplink radio's SSID and default-authentication, in `init` only.
- The two DHCP clients' route distances, in `wired-first` only.
It never touches the robot's own Wi-Fi, the bridge, the LAN's DHCP server or the firewall. The Thor
reaches the router over the LAN whatever happens to the uplink, so no change here can lock anyone
out.

Credentials: the router's admin password is typed at the prompt every time, from the password
manager, and never stored. The router's address comes from /etc/opendubs/network.env (hence sudo),
or --url. Every change is printed and confirmed before it is sent.

LANE RULE: a router change needs the user's word and a person at the robot (notes/workplan-network.md).
"""
from __future__ import annotations

import argparse
import getpass
import os
import re
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import routeros as ro  # noqa: E402

TAG = "netjoin"
UPLINK_IF = os.environ.get("NETMON_UPLINK_IF", "wlan2")
WIRED_IF = os.environ.get("NETMON_WIRED_IF", "ether1")
CL = "interface/wireless/connect-list"
SP = "interface/wireless/security-profiles"
WL = "interface/wireless"
DHCPC = "ip/dhcp-client"


class Refused(Exception):
    """netjoin will not do this. The message says why and what to do instead."""


def op(desc, method, path, item_id=None, body=None):
    return {"desc": desc, "method": method, "path": path, "id": item_id, "body": body}


# ---------------------------------------------------------------------------- reading the router

class View:
    """The parts of the router netjoin plans against. Built from the router, or by hand in tests."""

    def __init__(self, radio: dict, entries: list, profiles: list, dhcp: list):
        self.radio, self.entries, self.profiles, self.dhcp = radio, entries, profiles, dhcp

    @classmethod
    def read(cls, ros: ro.RouterOS) -> "View":
        radio = (ros.get(WL, name=UPLINK_IF) or [None])[0]
        if not radio:
            raise Refused(f"the router has no wireless interface named {UPLINK_IF}")
        entries = ros.get(CL, interface=UPLINK_IF) or []
        # Never read the passphrases back: name, mode and comment are all netjoin needs.
        profiles = ros.get(SP, proplist=".id,name,mode,comment") or []
        dhcp = ros.get(DHCPC, proplist=".id,interface,default-route-distance,check-gateway") or []
        return cls(radio, entries, profiles, dhcp)

    @property
    def initialised(self) -> bool:
        return not self.radio.get("ssid")

    def entry(self, ssid: str) -> dict | None:
        return next((e for e in self.entries if e.get("ssid") == ssid), None)

    def tagged_entry(self, ssid: str) -> dict:
        e = self.entry(ssid)
        if e is None:
            raise Refused(f"no saved network called {ssid!r} (see `netjoin list`)")
        if e.get("comment") != TAG:
            raise Refused(f"the entry for {ssid!r} wasn't made by netjoin, so netjoin won't change it")
        return e


def _require_init(v: View):
    if not v.initialised:
        raise Refused(f"the uplink is still fixed to {v.radio.get('ssid')!r}: run `netjoin init` first")


def _slug(ssid: str) -> str:
    return re.sub(r"[^A-Za-z0-9]+", "-", ssid).strip("-").lower()[:20] or "net"


# ---------------------------------------------------------------------------- plans

def plan_init(v: View) -> list:
    if v.initialised:
        raise Refused("already done: the uplink uses the saved list")
    ssid, prof = v.radio["ssid"], v.radio.get("security-profile") or "default"
    plan = []
    if not v.entry(ssid):
        plan.append(op(f"save the current network {ssid!r} (profile {prof!r}) as the first entry",
                       "PUT", CL, body={"interface": UPLINK_IF, "ssid": ssid, "security-profile": prof,
                                        "connect": "yes", "comment": TAG}))
    plan.append(op(f"clear {UPLINK_IF}'s fixed SSID and stop it joining unknown networks",
                   "PATCH", WL, v.radio[".id"], {"ssid": "", "default-authentication": "no"}))
    return plan


def plan_uninit(v: View) -> list:
    if not v.initialised:
        raise Refused("nothing to undo: the uplink is fixed to one network")
    first = next((e for e in v.entries if e.get("disabled") != "true"), None)
    if not first:
        raise Refused("no enabled saved network to fix the uplink to")
    return [op(f"fix {UPLINK_IF} to {first['ssid']!r} again (the saved list stays, unused)",
               "PATCH", WL, v.radio[".id"],
               {"ssid": first["ssid"], "security-profile": first.get("security-profile") or "default",
                "default-authentication": "yes"})]


def plan_add(v: View, ssid: str, psk: str | None, top: bool, min_dbm: int | None) -> list:
    _require_init(v)
    if v.entry(ssid):
        raise Refused(f"{ssid!r} is already saved: use `prefer`, `enable` or `remove`")
    names = {p.get("name") for p in v.profiles}
    name, n = f"nj-{_slug(ssid)}", 2
    while name in names:
        name, n = f"nj-{_slug(ssid)}-{n}", n + 1
    if psk is None:
        prof = {"name": name, "mode": "none", "comment": TAG}
        how = "open (no passphrase)"
    else:
        prof = {"name": name, "mode": "dynamic-keys", "authentication-types": "wpa2-psk",
                "unicast-ciphers": "aes-ccm", "group-ciphers": "aes-ccm,tkip",
                "wpa2-pre-shared-key": psk, "comment": TAG}
        how = "WPA2-PSK"
    entry = {"interface": UPLINK_IF, "ssid": ssid, "security-profile": name, "connect": "yes",
             "comment": TAG}
    if min_dbm is not None:
        entry["signal-range"] = f"{min_dbm}..120"
    plan = [op(f"create security profile {name!r} ({how})", "PUT", SP, body=prof),
            op(f"save {ssid!r} at the {'top' if top else 'bottom'} of the list"
               + (f", only above {min_dbm} dBm" if min_dbm is not None else ""), "PUT", CL, body=entry)]
    if top and v.entries:
        plan.append(op(f"move {ssid!r} above {v.entries[0].get('ssid')!r}", "POST", CL + "/move",
                       body={"numbers": "$new", "destination": v.entries[0][".id"]}))
    return plan


def plan_prefer(v: View, ssid: str) -> list:
    _require_init(v)
    e = v.entry(ssid)
    if e is None:
        raise Refused(f"no saved network called {ssid!r}")
    # Moving changes only the order, so any saved entry may move, netjoin's or not.
    if v.entries and v.entries[0].get(".id") == e[".id"]:
        raise Refused(f"{ssid!r} is already first")
    return [op(f"move {ssid!r} to the top", "POST", CL + "/move",
               body={"numbers": e[".id"], "destination": v.entries[0][".id"]})]


def plan_enable(v: View, ssid: str, on: bool) -> list:
    e = v.tagged_entry(ssid)
    if not on and sum(1 for x in v.entries if x.get("disabled") != "true") <= 1 and e.get("disabled") != "true":
        raise Refused(f"{ssid!r} is the only enabled network: the robot would have no uplink")
    return [op(f"{'enable' if on else 'disable'} {ssid!r}", "PATCH", CL, e[".id"],
               {"disabled": "no" if on else "yes"})]


def plan_remove(v: View, ssid: str, force: bool) -> list:
    e = v.tagged_entry(ssid)
    enabled = [x for x in v.entries if x.get("disabled") != "true"]
    if e in enabled and len(enabled) <= 1 and not force:
        raise Refused(f"{ssid!r} is the only enabled network: the robot would have no uplink (--force)")
    plan = [op(f"forget {ssid!r}", "DELETE", CL, e[".id"])]
    prof = next((p for p in v.profiles if p.get("name") == e.get("security-profile")), None)
    used = sum(1 for x in v.entries if x.get("security-profile") == e.get("security-profile"))
    if prof and prof.get("comment") == TAG and used == 1:
        plan.append(op(f"delete its security profile {prof['name']!r}", "DELETE", SP, prof[".id"]))
    return plan


def plan_wired_first(v: View, undo: bool) -> list:
    d = {x.get("interface"): x for x in v.dhcp}
    if WIRED_IF not in d or UPLINK_IF not in d:
        raise Refused(f"expected DHCP clients on both {WIRED_IF} and {UPLINK_IF}")
    if undo:
        return [op(f"{WIRED_IF} and {UPLINK_IF} back to equal route distance 1", "PATCH", DHCPC,
                   d[WIRED_IF][".id"], {"default-route-distance": "1", "check-gateway": "none"}),
                op("", "PATCH", DHCPC, d[UPLINK_IF][".id"], {"default-route-distance": "1"})]
    return [op(f"{WIRED_IF}: route distance 1, and drop the route if its gateway stops answering",
               "PATCH", DHCPC, d[WIRED_IF][".id"], {"default-route-distance": "1", "check-gateway": "ping"}),
            op(f"{UPLINK_IF}: route distance 2, so a cable wins", "PATCH", DHCPC, d[UPLINK_IF][".id"],
               {"default-route-distance": "2"})]


# ---------------------------------------------------------------------------- doing it

def apply(ros: ro.RouterOS, plan: list) -> None:
    new_id = None
    for o in plan:
        body = dict(o["body"]) if o["body"] else None
        if body and body.get("numbers") == "$new":
            body["numbers"] = new_id
        if o["method"] == "PUT":
            r = ros.put(o["path"], body)
            new_id = (r or {}).get(".id")
        elif o["method"] == "PATCH":
            ros.patch(o["path"], o["id"], body)
        elif o["method"] == "DELETE":
            ros.delete(o["path"], o["id"])
        else:
            ros.post(o["path"], body)


def confirm(plan: list, yes: bool) -> bool:
    print("This will change the robot router:")
    for o in plan:
        if o["desc"]:
            print(f"  - {o['desc']}")
    if yes:
        return True
    return input("Apply? [y/N] ").strip().lower() in ("y", "yes")


def monitor(ros: ro.RouterOS) -> dict:
    r = ros.post(f"{WL}/monitor", {"numbers": UPLINK_IF, "once": ""})
    return (r[0] if isinstance(r, list) and r else r) or {}


def reconnect(ros: ro.RouterOS, v: View) -> None:
    """Drop and rejoin, so the station picks the top saved network now. The uplink is down a few
    seconds; the robot LAN is not affected."""
    ros.patch(WL, v.radio[".id"], {"disabled": "yes"})
    time.sleep(1)
    ros.patch(WL, v.radio[".id"], {"disabled": "no"})


def wait_for(ros: ro.RouterOS, ssid: str | None, timeout: float = 60) -> bool:
    end = time.time() + timeout
    m = {}
    while time.time() < end:
        try:
            m = monitor(ros)
        except ro.RouterError:
            m = {}
        if m.get("status") == "connected-to-ess" and (ssid is None or m.get("ssid") == ssid):
            print(f"Joined {m.get('ssid')}: {ro.signal_dbm(m.get('signal-strength'))} dBm. "
                  "Run netdiag to check the internet through it.")
            return True
        time.sleep(3)
    print(f"Not joined {ssid or 'any network'} after {int(timeout)} s (radio status: {m.get('status')}).")
    print("If the passphrase is wrong, the router keeps retrying that network while it's in range: "
          "`netjoin disable` it to fall back to the next one.")
    return False


def router_from_args(a) -> ro.RouterOS:
    url = a.url
    if not url:
        try:
            url = ro.read_env(a.env)["ROUTER_URL"]
            pin = ro.read_env(a.env).get("ROUTER_CERT_SHA256")
        except PermissionError:
            raise Refused(f"can't read {a.env}: run with sudo, or give --url")
        except (FileNotFoundError, KeyError):
            raise Refused(f"no ROUTER_URL in {a.env}: give --url http(s)://10.42.2.1")
    else:
        pin = a.pin
    if url.startswith("http:") and not a.allow_http:
        raise Refused("the router URL is plain HTTP, so the admin password would cross the robot "
                      "Wi-Fi in clear. Pass --allow-http to accept that (as the admin page does)")
    pw = getpass.getpass(f"Router password for {a.user} (from the password manager): ")
    return ro.RouterOS(url, a.user, pw, pin, timeout=10)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0],
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--env", default=ro.ENV_FILE)
    ap.add_argument("--url", help="router base URL, e.g. https://10.42.2.1 (default: from --env)")
    ap.add_argument("--pin", help="router certificate SHA-256, with --url over HTTPS")
    ap.add_argument("--user", default="admin")
    ap.add_argument("--allow-http", action="store_true")
    ap.add_argument("--yes", action="store_true", help="don't ask before applying")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("list")
    sub.add_parser("status")
    p = sub.add_parser("init")
    p.add_argument("--undo", action="store_true")
    p = sub.add_parser("add")
    p.add_argument("ssid")
    p.add_argument("--open", action="store_true", help="a network with no passphrase")
    p.add_argument("--top", action="store_true", help="put it first instead of last")
    p.add_argument("--min-dbm", type=int, help="ignore it when weaker than this, e.g. -78")
    p.add_argument("--now", action="store_true", help="with --top: switch to it now")
    p = sub.add_parser("prefer")
    p.add_argument("ssid")
    p.add_argument("--now", action="store_true", help="switch now (the uplink drops a few seconds)")
    for name in ("enable", "disable"):
        sub.add_parser(name).add_argument("ssid")
    p = sub.add_parser("remove")
    p.add_argument("ssid")
    p.add_argument("--force", action="store_true")
    sub.add_parser("scan")
    p = sub.add_parser("wired-first")
    p.add_argument("--undo", action="store_true")
    a = ap.parse_args()

    try:
        ros = router_from_args(a)
        v = View.read(ros)
        if a.cmd == "list":
            cur = monitor(ros).get("ssid")
            if not v.initialised:
                print(f"The uplink is fixed to {v.radio.get('ssid')!r} (run `netjoin init` to use a saved list).")
            for i, e in enumerate(v.entries, 1):
                flags = [] if e.get("disabled") != "true" else ["disabled"]
                if e.get("comment") != TAG:
                    flags.append("not netjoin's")
                if e.get("ssid") == cur:
                    flags.append("CONNECTED")
                print(f"{i:>2}. {e.get('ssid')!r:<32} {e.get('security-profile') or '':<20} {' '.join(flags)}")
            if not v.entries:
                print("(no saved networks)")
            return 0
        if a.cmd == "status":
            m = monitor(ros)
            print(f"{UPLINK_IF}: {m.get('status')}  network {m.get('ssid')!r}  "
                  f"signal {ro.signal_dbm(m.get('signal-strength'))} dBm")
            return 0
        if a.cmd == "scan":
            print(f"Scanning takes {UPLINK_IF} off its network for about 5 s: the robot has no internet meanwhile.")
            if not a.yes and input("Scan? [y/N] ").strip().lower() not in ("y", "yes"):
                return 1
            rows = ros.post(f"{WL}/scan", {"number": UPLINK_IF, "duration": "5"}) or []
            for r in sorted(rows, key=lambda r: ro.as_int(r.get("signal-strength") or r.get("signal"), -200), reverse=True):
                print(f"{r.get('ssid')!r:<32} {r.get('signal-strength') or r.get('signal')} dBm  "
                      f"{r.get('channel') or ''}")
            return 0

        if a.cmd == "init":
            plan = plan_uninit(v) if a.undo else plan_init(v)
        elif a.cmd == "add":
            psk = None
            if not a.open:
                psk = getpass.getpass(f"Passphrase for {a.ssid!r}: ")
                if psk != getpass.getpass("Again: "):
                    raise Refused("the passphrases don't match")
                if not 8 <= len(psk) <= 63:
                    raise Refused("a WPA2 passphrase is 8 to 63 characters")
            plan = plan_add(v, a.ssid, psk, a.top, a.min_dbm)
        elif a.cmd == "prefer":
            plan = plan_prefer(v, a.ssid)
        elif a.cmd in ("enable", "disable"):
            plan = plan_enable(v, a.ssid, a.cmd == "enable")
        elif a.cmd == "remove":
            plan = plan_remove(v, a.ssid, a.force)
        elif a.cmd == "wired-first":
            plan = plan_wired_first(v, a.undo)
        else:
            return 2
        if not confirm(plan, a.yes):
            print("Nothing changed.")
            return 1
        apply(ros, plan)
        print("Done.")
        if a.cmd == "init" and not a.undo:
            return 0 if wait_for(ros, v.radio.get("ssid"), 30) else 1
        if getattr(a, "now", False) and (a.cmd == "prefer" or a.top):
            reconnect(ros, View.read(ros))
            return 0 if wait_for(ros, a.ssid) else 1
        return 0
    except Refused as e:
        print(f"netjoin: {e}", file=sys.stderr)
        return 2
    except ro.RouterError as e:
        print(f"netjoin: {e}", file=sys.stderr)
        return 3


if __name__ == "__main__":
    sys.exit(main())
