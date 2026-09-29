"""Robot telemetry for the kiosk dashboard — RC from the StamPLC SBUS.

RC comes from the StamPLC `[RC]` line (raw SBUS channels + interpreted
mode/deadman/link). Position/heading come from the Thor's fused pose (ROS EKF),
surfaced on NAV, not here. RC values carry their `range` ({min,max,center}) so the
RC widget stays source-agnostic.

SIM mode (OPENDUBS_RC_SIM=1) emits synthetic SBUS RC.
"""
from __future__ import annotations

import math
import os
import time

import stamplc

SBUS_MIN, SBUS_MAX, SBUS_CENTER = 200, 1820, 1000
_RANGE = {"min": SBUS_MIN, "max": SBUS_MAX, "center": SBUS_CENTER}
SIM = os.environ.get("OPENDUBS_RC_SIM", "0") == "1"


def _sim_rc():
    t = time.time()
    amp = (SBUS_MAX - SBUS_CENTER) * 0.85
    ax = lambda p, ph=0: int(SBUS_CENTER + amp * math.sin(t / p + ph))
    channels = [ax(3, 0), ax(4, 1.1), ax(2.5, 2.2), ax(3.5, 3.3), 1000, 1500, 300,
                int(SBUS_MIN + (SBUS_MAX - SBUS_MIN) * (0.5 + 0.45 * math.sin(t / 5)))]
    return {"source": "sim", "connected": True, "link": "ok", "channels": channels,
            "range": dict(_RANGE), "mode": "MANUAL", "deadman": True,
            "failsafe": False, "frame_lost": False, "last_seen": t}


def _rc():
    s = stamplc.reader.snapshot()
    rc = s.get("rc") or {}
    raw = rc.get("raw")
    live = bool(rc.get("live"))
    if not raw:
        return {"source": "stamplc-sbus", "connected": False, "link": None,
                "channels": None, "range": dict(_RANGE), "mode": rc.get("mode"),
                "deadman": rc.get("dead"), "failsafe": rc.get("mode") == "FAILSAFE",
                "frame_lost": True}
    return {"source": "stamplc-sbus", "connected": True,
            "link": "ok" if live else "no_link", "channels": raw, "range": dict(_RANGE),
            "mode": rc.get("mode"), "deadman": bool(rc.get("dead")),
            "failsafe": rc.get("mode") == "FAILSAFE", "frame_lost": not live}


def collect() -> dict:
    rc = _sim_rc() if SIM else _rc()
    return {
        "ts": time.time(),
        "connected": rc["connected"],
        "source": rc["source"],
        "rc": rc,
        # Position/heading come from the ROS fused pose (NAV).
        "gps": {"present": False, "note": "via ROS fused pose (NAV)"},
        "flow": {"present": False},
        "safety": {"mode": rc.get("mode"), "deadman": rc.get("deadman")},
        "subsystems": {
            "base": {"enabled": None, "fault": None},
            "mast": {"enabled": None, "fault": None},
            "left_arm": {"enabled": None, "fault": None},
            "right_arm": {"enabled": None, "fault": None},
        },
    }
