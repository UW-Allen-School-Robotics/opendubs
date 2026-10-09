"""Which UDP sender is the StamPLC: the check drive_bridge.py makes before it parses [ODO] or sends
DRIVE.

A packet arriving on the StamPLC's port is not proof it came from the StamPLC. The dashboard
learned this on 2026-10-02, when it adopted 192.168.55.1 (the Thor's own, down, l4tbr0 bridge)
as the board because the Thor itself sends to this port. Its guard, in
software/kiosk-dashboard/backend/stamplc.py, has two parts, and this module applies the same two:

  1. never one of this host's own addresses (drive_bridge and stamplc_hb both send to this port,
     and SO_REUSEPORT means we hear them);
  2. the packet must look like StamPLC telemetry, a `[TAG]` line.

For a node that SENDS MOTION COMMANDS there is a third option the dashboard does not need: pin the
board's address with OPENDUBS_STAMP_IP (a per-robot systemd drop-in, never committed), and then
nothing else is ever accepted. Without a pin the peer is learned, but only from packets that pass
both checks, and a change of peer is reported rather than silent.

No ROS imports, so the tests run anywhere (software/ros/tests/test_plc_peer.py).
"""
from __future__ import annotations

import re
import socket
from typing import Callable

TELEM_LINE = re.compile(rb"\[[A-Z][A-Z0-9]{1,9}\]")

_own_cache: dict[str, bool] = {}


def is_own_address(ip: str) -> bool:
    """True if `ip` is assigned to this host. Tested by binding it, as the dashboard does: no
    interface enumeration, correct as interfaces come and go. Cached; checked per packet."""
    hit = _own_cache.get(ip)
    if hit is None:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            s.bind((ip, 0))
            hit = True
        except OSError:
            hit = False
        finally:
            s.close()
        _own_cache[ip] = hit
    return hit


# What offer() reports. The first three mean "use this packet".
ACCEPTED = "accepted"      # from the current peer
ADOPTED = "adopted"        # first valid packet: this sender is now the peer
MOVED = "moved"            # a valid packet from a NEW address: the peer changed (DHCP, a swap)
REFUSED_PIN = "refused-pin"
REFUSED_OWN = "refused-own"
REFUSED_SHAPE = "refused-shape"


class PeerGuard:
    def __init__(self, pinned_ip: str | None = None,
                 own: Callable[[str], bool] = is_own_address) -> None:
        self.pinned = pinned_ip or None
        self.peer: str | None = None
        self._own = own

    def offer(self, data: bytes, ip: str) -> str:
        if self.pinned is not None and ip != self.pinned:
            return REFUSED_PIN
        if self._own(ip):
            return REFUSED_OWN
        if not TELEM_LINE.search(data):
            return REFUSED_SHAPE
        if self.peer == ip:
            return ACCEPTED
        verdict = ADOPTED if self.peer is None else MOVED
        self.peer = ip
        return verdict

    @staticmethod
    def usable(verdict: str) -> bool:
        return verdict in (ACCEPTED, ADOPTED, MOVED)
