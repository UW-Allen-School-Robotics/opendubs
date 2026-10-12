#!/usr/bin/env python3
"""plc_peer.PeerGuard: the StamPLC-sender check drive_bridge.py applies before parsing [ODO] or
sending DRIVE. Pure Python; no ROS needed. Run: python3 software/ros/tests/test_plc_peer.py"""
import pathlib
import sys
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
import plc_peer as pp  # noqa: E402

OWN = {"127.0.0.1", "10.42.2.10", "192.168.55.1"}   # stand-ins for the Thor's own addresses
ODO = b"[ODO] ok=1 x=12.0 y=-3.5 h=0.10\n"


def guard(pinned=None):
    return pp.PeerGuard(pinned, own=lambda ip: ip in OWN)


class PeerGuardTest(unittest.TestCase):
    def test_adopts_first_valid_sender(self):
        g = guard()
        self.assertEqual(g.offer(ODO, "10.42.2.20"), pp.ADOPTED)
        self.assertEqual(g.peer, "10.42.2.20")
        self.assertEqual(g.offer(ODO, "10.42.2.20"), pp.ACCEPTED)

    def test_never_adopts_own_address(self):
        # The 2026-10-02 failure: the Thor's own l4tbr0 address adopted as the board.
        g = guard()
        self.assertEqual(g.offer(ODO, "192.168.55.1"), pp.REFUSED_OWN)
        self.assertIsNone(g.peer)

    def test_own_command_echo_is_refused(self):
        g = guard()
        self.assertEqual(g.offer(b"DRIVE 0.100 0.000 0.000\n", "10.42.2.10"), pp.REFUSED_OWN)

    def test_non_telemetry_is_refused(self):
        g = guard()
        self.assertEqual(g.offer(b"DRIVE 0.1 0 0\n", "10.42.2.99"), pp.REFUSED_SHAPE)
        self.assertEqual(g.offer(b"hello\n", "10.42.2.99"), pp.REFUSED_SHAPE)
        self.assertIsNone(g.peer)

    def test_stray_packet_does_not_hijack_learned_peer(self):
        g = guard()
        g.offer(ODO, "10.42.2.20")
        self.assertEqual(g.offer(b"junk", "10.42.2.66"), pp.REFUSED_SHAPE)
        self.assertEqual(g.peer, "10.42.2.20")

    def test_valid_new_address_is_reported_as_moved(self):
        g = guard()
        g.offer(ODO, "10.42.2.20")
        self.assertEqual(g.offer(ODO, "10.42.2.21"), pp.MOVED)
        self.assertEqual(g.peer, "10.42.2.21")

    def test_pin_refuses_everything_else(self):
        g = guard("10.42.2.20")
        self.assertEqual(g.offer(ODO, "10.42.2.21"), pp.REFUSED_PIN)
        self.assertEqual(g.offer(ODO, "10.42.2.20"), pp.ADOPTED)
        self.assertEqual(g.peer, "10.42.2.20")

    def test_usable(self):
        for v in (pp.ACCEPTED, pp.ADOPTED, pp.MOVED):
            self.assertTrue(pp.PeerGuard.usable(v))
        for v in (pp.REFUSED_PIN, pp.REFUSED_OWN, pp.REFUSED_SHAPE):
            self.assertFalse(pp.PeerGuard.usable(v))

    def test_real_own_address_check(self):
        self.assertTrue(pp.is_own_address("127.0.0.1"))
        self.assertFalse(pp.is_own_address("203.0.113.7"))   # TEST-NET-3, never assigned


if __name__ == "__main__":
    unittest.main(verbosity=2)
