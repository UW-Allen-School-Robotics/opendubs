#!/usr/bin/env python3
"""What the robot says about itself: only what its dashboard shows, only while it is live, and
nothing only an operator should hear. Both would be heard by a visitor if they broke.

    python3 software/voice/tests/test_robot_facts.py
"""
import re
import unittest

import fakes  # noqa: F401  (puts the service on the path)
import robot_facts as rf

NOW = 1_000_000.0


def snap(**kw):
    s = {"ts": NOW - 1, "state": {"name": "SAFE_IDLE", "words": "Ready"},
         "faults": [], "battery": {"percent": 82, "charging": False},
         "arms": {"left": "holding", "right": "off"}, "show": {"running": False, "name": None},
         "network": {"uplink": "good"}, "uptime_s": 3 * 3600 + 5 * 60, "controller": None}
    s.update(kw)
    return s


class LeftOut(unittest.TestCase):
    """IP and MAC addresses, network names, device paths and IDs, serials, emails, links and account
    names never reach the model, whichever field they turn up in."""

    SECRETS = ["10.42.2.10", "192.168.88.1:8080", "fe80::1c2d:3e4f", "F8:3D:C6:91:75:C2", "Opendubs2",
               "UW MPSK", "/dev/ttyACM0", "usb-1a86_7523", "a3f9c2e81b7d", "SN 4471-22", "ops@example.org",
               "https://10.42.2.10/", "emmar"]

    def test_none_of_it_is_said(self):
        s = snap(state={"words": "Ready at 10.42.2.10"},
                 network={"uplink": "weak, SSID: UW MPSK via 192.168.88.1:8080", "ip": "10.42.2.10",
                          "ssid": "Opendubs2", "mac": "F8:3D:C6:91:75:C2"},
                 arms={"left": "holding on /dev/ttyACM0", "right": "off, serial SN 4471-22"},
                 faults=[{"words": "power board fault, id a3f9c2e81b7d", "code": None},
                         {"words": "see https://10.42.2.10/ or mail ops@example.org", "code": None}],
                 show={"running": True, "name": "wave"},
                 controller="user: emmar",
                 net={"wired": {"ip": "10.42.2.10"}, "wifi": {"ssid": "UW MPSK", "mac": "fe80::1c2d:3e4f"}},
                 client_ip="10.42.2.13", device="usb-1a86_7523")
        said = rf.summary(s, now=NOW, help={})
        for secret in self.SECRETS:
            self.assertNotIn(secret.lower(), said.lower(), secret)
        self.assertIsNone(re.search(r"\d+\.\d+\.\d+\.\d+", said))
        self.assertIn("left arm: holding", said)                 # the words around them stay
        self.assertIn("running the show wave", said)

    def test_only_the_agreed_fields_are_read(self):
        said = rf.summary(snap(odo={"x": 1.25}, power={"pdh_ch": [3.3]}, rc_link={"rssi": -61}), now=NOW, help={})
        for k in ("odo", "1.25", "pdh", "rssi", "-61"):
            self.assertNotIn(k, said)


class Honest(unittest.TestCase):
    def test_the_dashboards_own_shape(self):
        """As /api/status/spoken sends it (#433): caption words, a routine id, the arms mostly null."""
        said = rf.summary(snap(state={"name": "CHARGING", "words": "Charging 82%"},
                               show={"running": True, "name": "herb_oreo_v2"}, arms={"left": None, "right": None},
                               faults=[{"words": "lift not responding", "code": "LIFT_NOT_RESPONDING"}],
                               network={"uplink": "no router"}), now=NOW, help={})
        for part in ("state: Charging 82%", "running the show herb oreo v2", "left arm: unknown",
                     "fault: lift not responding", "network uplink: no router"):
            self.assertIn(part, said)

    def test_live_data_is_said(self):
        said = rf.summary(snap(), now=NOW, help={})
        for part in ("state: Ready", "battery: 82 percent", "charging: not charging", "left arm: holding",
                     "no show running", "network uplink: good", "uptime: 3 hours 5 minutes", "faults: none"):
            self.assertIn(part, said)

    def test_a_live_snapshot_just_ahead_of_the_clock_is_live(self):
        """It happened: the dashboard rounds ts to 0.1 s, so a live snapshot was often a few ms in
        the future, was refused as invalid, and the robot said it could not tell its battery."""
        said = rf.summary(snap(ts=NOW + 0.05), now=NOW, help={})
        self.assertIn("battery: 82 percent", said)

    def test_a_stale_or_missing_snapshot_says_it_cannot_tell(self):
        for s in (snap(ts=NOW - 60), snap(ts=NOW + 30), snap(ts=None), None, "error", {}):
            said = rf.summary(s, now=NOW, help={})
            self.assertIn(rf.CANT_TELL, said, s)
            self.assertNotIn("82", said)                          # no old number is passed on
            self.assertNotIn("Ready", said)

    def test_what_the_dashboard_cannot_vouch_for_is_unknown(self):
        said = rf.summary(snap(battery={"percent": None, "charging": None}, network={"uplink": None},
                               arms=None, show={"running": None}, faults=None, uptime_s=None), now=NOW, help={})
        for part in ("battery: unknown", "charging: unknown", "network uplink: unknown", "left arm: unknown",
                     "show: unknown", "faults: unknown", "uptime: unknown"):
            self.assertIn(part, said)
        self.assertIn(rf.CANT_TELL, said)                         # the rule for unknowns travels with them

    def test_nonsense_numbers_are_not_said(self):
        for bad in (140, -3, "82", True):
            said = rf.summary(snap(battery={"percent": bad, "charging": False}), now=NOW, help={})
            self.assertIn("battery: unknown", said, bad)


class FaultHelp(unittest.TestCase):
    def test_an_active_fault_brings_its_registry_words(self):
        help = {"CONTACTOR_OPEN": {"title": "The drive contactor is open",
                                   "means": "The drive [contactor](glossary.md#contactor) is open, so the drive bus is unpowered.",
                                   "do": ["Check that **RUN** is on.", "See https://10.42.2.10/docs."]}}
        said = rf.summary(snap(faults=[{"words": "power cut-off not working", "code": "CONTACTOR_OPEN"}]),
                          now=NOW, help=help)
        self.assertIn("fault: power cut-off not working (What it means: The drive contactor is open", said)
        self.assertIn("What to do: Check that RUN is on.", said)
        for leak in ("glossary.md", "**", "10.42.2.10"):
            self.assertNotIn(leak, said)

    def test_a_fault_without_an_entry_is_still_said(self):
        said = rf.summary(snap(faults=[{"words": "needs a check", "code": "NOT_IN_REGISTRY"}]), now=NOW, help={})
        self.assertIn("fault: needs a check", said)


if __name__ == "__main__":
    unittest.main()
