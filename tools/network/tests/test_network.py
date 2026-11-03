"""Checks that earn their place (CLAUDE.md): RouterOS's string formats parse, the dashboard's uplink
state is right (a wrong state misleads the operator), and netjoin refuses changes that could strand
the robot or touch config it didn't create. Run: python3 -m unittest discover -s tools/network/tests
"""
import copy
import json
import sys
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
import netjoin  # noqa: E402
import netmon  # noqa: E402
import routeros as ro  # noqa: E402

RAW = json.loads((HERE / "router_2026-10-19.json").read_text())
ONLINE = {"internet": True, "captive": False, "dns": True, "latency_ms": 9.0}


def state(raw, probes=ONLINE, streaks=None, prev=None, now=1000.0):
    s = netmon.summarize(raw, probes, prev, now)
    st = streaks or netmon.Streaks()
    st.update(s, True)
    return s, netmon.classify(s, st)


class Parsing(unittest.TestCase):
    def test_formats(self):
        self.assertEqual(ro.signal_dbm("-83@12Mbps"), -83)
        self.assertEqual(ro.signal_dbm("-45@HT20-2"), -45)
        self.assertEqual(ro.duration_s("1h43m36s"), 6216)
        self.assertEqual(ro.duration_s("1w2d03:04:05"), 9 * 86400 + 11045)
        self.assertEqual(ro.rate_mbps("144.4Mbps-20MHz/2S/SGI"), 144.4)
        self.assertEqual(ro.rate_mbps("5.5Mbps"), 5.5)
        self.assertEqual(ro.pair("506076593,23896892180"), (506076593, 23896892180))


class UplinkState(unittest.TestCase):
    def test_measured_day_is_weak(self):
        s, (st, say) = state(RAW)
        self.assertEqual(st, "WEAK")
        self.assertIn("-83 dBm on UW MPSK", say)
        u = s["uplink"]
        self.assertEqual((u["via"], u["snr_db"], u["rate_mbps"], u["since_s"]), ("wifi", 25, 52.0, 6216))
        self.assertEqual([c["name"] for c in s["robot_wifi"]], ["esp32s3-000016", "Galaxy-Tab-A11"])

    def test_good_signal(self):
        raw = copy.deepcopy(RAW)
        raw["reg"][2]["signal-strength"] = "-60@54Mbps"
        self.assertEqual(state(raw)[1][0], "GOOD")

    def test_no_router(self):
        self.assertEqual(state(None)[1][0], "NO_ROUTER")

    def test_refused_login_says_so(self):
        s = netmon.summarize(None, ONLINE, None, 1000.0)
        s["router_refused"] = True
        st, say = netmon.classify(s, netmon.Streaks())
        self.assertEqual(st, "NO_ROUTER")
        self.assertIn("refused", say)

    def test_not_associated_is_down(self):
        raw = copy.deepcopy(RAW)
        raw["reg"] = raw["reg"][:2]
        raw["dhcpc"][1]["status"] = "searching..."
        self.assertEqual(state(raw)[1][0], "DOWN")

    def test_captive(self):
        self.assertEqual(state(RAW, {**ONLINE, "internet": False, "captive": True})[1][0], "CAPTIVE")

    def test_internet_down_needs_three_failures(self):
        st = netmon.Streaks()
        off = {**ONLINE, "internet": False}
        results = [state(RAW, off, st)[1][0] for _ in range(3)]
        self.assertEqual(results, ["WEAK", "WEAK", "DOWN"])

    def test_busy_needs_two_polls(self):
        st = netmon.Streaks()
        prev = netmon.summarize(RAW, ONLINE, None, 1000.0)
        raw = copy.deepcopy(RAW)
        got = []
        for i in (1, 2):                      # 40 Mbit/s down for 5 s, against a 52 Mbit/s radio
            raw["ifaces"][1]["rx-byte"] = str(int(raw["ifaces"][1]["rx-byte"]) + 25_000_000)
            s, (st_, _) = state(raw, ONLINE, st, prev, 1000.0 + 5 * i)
            prev = s
            got.append(st_)
        self.assertEqual(got, ["WEAK", "BUSY"])

    def test_reassociation_is_a_drop(self):
        prev = netmon.summarize(RAW, ONLINE, None, 1000.0)
        raw = copy.deepcopy(RAW)
        raw["reg"][2].update({"mac-address": "02:00:00:00:00:02", "uptime": "18s"})
        cur = netmon.summarize(raw, ONLINE, prev, 1005.0)
        evs = netmon.events_between(prev, cur)
        self.assertEqual([e["event"] for e in evs], ["uplink_reassociated"])
        self.assertEqual(netmon.drops_since(evs, 0), 1)


def view(initialised=True, extra=()):
    radio = {".id": "*6", "name": "wlan2", "ssid": "" if initialised else "UW MPSK",
             "security-profile": "uwmpsk"}
    entries = [{".id": "*A", "ssid": "UW MPSK", "security-profile": "uwmpsk", "comment": "netjoin",
                "disabled": "false"},
               {".id": "*B", "ssid": "Hand-made", "security-profile": "x", "disabled": "false"}]
    entries += list(extra)
    profiles = [{".id": "*1", "name": "uwmpsk"}, {".id": "*2", "name": "x"}]
    return netjoin.View(radio, entries, profiles, [])


class NetjoinGuards(unittest.TestCase):
    def test_never_touches_entries_it_did_not_make(self):
        for plan in (lambda v: netjoin.plan_enable(v, "Hand-made", False),
                     lambda v: netjoin.plan_remove(v, "Hand-made", True)):
            with self.assertRaises(netjoin.Refused):
                plan(view())

    def test_wont_leave_no_uplink(self):
        v = view()
        v.entries[1]["disabled"] = "true"
        with self.assertRaises(netjoin.Refused):
            netjoin.plan_enable(v, "UW MPSK", False)
        with self.assertRaises(netjoin.Refused):
            netjoin.plan_remove(v, "UW MPSK", False)

    def test_add_needs_init_and_hides_the_passphrase(self):
        with self.assertRaises(netjoin.Refused):
            netjoin.plan_add(view(initialised=False), "Venue", "test-passphrase", False, None)
        plan = netjoin.plan_add(view(), "Venue", "test-passphrase", True, -78)
        self.assertTrue(all("test-passphrase" not in o["desc"] for o in plan))
        self.assertEqual(plan[0]["body"]["comment"], "netjoin")
        self.assertEqual(plan[-1]["body"], {"numbers": "$new", "destination": "*A"})

    def test_init_saves_the_current_network_first(self):
        fixed = netjoin.View({".id": "*6", "ssid": "UW MPSK", "security-profile": "uwmpsk"}, [], [], [])
        plan = netjoin.plan_init(fixed)
        self.assertEqual([o["method"] for o in plan], ["PUT", "PATCH"])
        self.assertEqual(plan[1]["body"], {"ssid": "", "default-authentication": "no"})


if __name__ == "__main__":
    unittest.main()
