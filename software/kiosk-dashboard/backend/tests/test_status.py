import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import status
import supervisor as supervisor_mod


def _benign_snapshot():
    """A StamPLC snapshot that's fresh and reports no faults from any OTHER
    subsystem (safety/thermal/PDH/charger) and a benign RC state (authority OFF,
    not e-stopped, not booting) — isolates `_robot_state()`'s lift-wiring effect
    from everything else it also folds in."""
    return {
        "fresh": True,
        "rc": {"live": True, "mode": "OFF", "dead": False},
        "di": [], "relay": [], "bms": {}, "env": {}, "fan": {}, "pdh": {},
    }


def test_lift_fresh_no_fault(monkeypatch):
    monkeypatch.setattr(status.stamplc.reader, "snapshot", lambda: {
        "fresh": True,
        "lift": {"live": True, "height_mm": 420.5, "inhibit": False,
                  "motion": "UP", "fault": "NONE"},
    })
    out = status._lift()
    assert out == {
        "fresh": True, "height_mm": 420.5, "inhibit": False,
        "motion": "UP", "fault": "NONE", "fault_active": False,
    }


def test_lift_stale_when_stamplc_link_stale(monkeypatch):
    # StamPLC link itself is stale — doesn't matter what the module's own `live`
    # flag says, `_lift()["fresh"]` must be False (same combination `_rc_link()` uses).
    monkeypatch.setattr(status.stamplc.reader, "snapshot", lambda: {
        "fresh": False,
        "lift": {"live": True, "height_mm": 100.0, "fault": None},
    })
    out = status._lift()
    assert out["fresh"] is False


def test_lift_stale_when_module_not_live(monkeypatch):
    # StamPLC link is fresh but the lift module itself hasn't reported live=1.
    monkeypatch.setattr(status.stamplc.reader, "snapshot", lambda: {
        "fresh": True,
        "lift": {"live": False, "height_mm": None, "fault": None},
    })
    out = status._lift()
    assert out["fresh"] is False


def test_lift_no_lift_data_at_all(monkeypatch):
    # No [LIFT] line ever received — snapshot has no "lift" key at all.
    monkeypatch.setattr(status.stamplc.reader, "snapshot", lambda: {"fresh": True})
    out = status._lift()
    assert out["fresh"] is False
    assert out["height_mm"] is None
    assert out["fault_active"] is False


def test_lift_fault_active(monkeypatch):
    monkeypatch.setattr(status.stamplc.reader, "snapshot", lambda: {
        "fresh": True,
        "lift": {"live": True, "fault": "STUCK"},
    })
    out = status._lift()
    assert out["fault_active"] is True
    assert out["fault"] == "STUCK"


def test_lift_stale_with_fault_code_present_is_not_active(monkeypatch):
    # I3 (dashboard-side belt-and-suspenders): a stale module reporting a cached fault code
    # must NOT surface as fault_active — the StamPLC side (I3's other half) already stops
    # reporting a real fault while stale, but this must hold even if that path is ever
    # bypassed (e.g. a stale StamPLC link itself, independent of the module's own liveness).
    monkeypatch.setattr(status.stamplc.reader, "snapshot", lambda: {
        "fresh": False,
        "lift": {"live": True, "fault": "STUCK"},
    })
    out = status._lift()
    assert out["fresh"] is False
    assert out["fault_active"] is False


def test_lift_fault_none_string_is_not_active(monkeypatch):
    # Firmware reports the literal string "NONE" for no-fault, not an absent key.
    monkeypatch.setattr(status.stamplc.reader, "snapshot", lambda: {
        "fresh": True,
        "lift": {"live": True, "fault": "NONE"},
    })
    out = status._lift()
    assert out["fault_active"] is False


# ---- _robot_state()'s lift wiring --------------------------------------------
# The tests above prove _lift()'s own output is correct in isolation; these prove
# _robot_state() actually WIRES that output into raw_faults/mast_fault correctly
# (the `lift = _lift()` / `REQUIRED_MODULES.get("lift") and not lift["fresh"]` /
# `raw_faults.append(...)` / `mast_fault = lift["fault_active"]` lines themselves).
# Each test gets its own fresh Supervisor() so a fault raised in one test can't
# latch and leak into the next (supervisor.fault_latched persists across calls
# by design — see supervisor.py).

def test_robot_state_wires_required_not_fresh_lift_to_degraded_fault(monkeypatch):
    sup = supervisor_mod.Supervisor()
    sup.ready = True   # boot grace window already passed — I1's gate lets this fault through
    monkeypatch.setattr(status, "supervisor", sup)
    monkeypatch.setattr(status.stamplc.reader, "snapshot", lambda: _benign_snapshot())
    # Silent for longer than LIFT_SILENT_S, with the link fresh all that time.
    t = status.time.monotonic() - 2 * status.LIFT_SILENT_S
    monkeypatch.setattr(status, "_lift_watch", {"link_since": t, "silent_since": t})
    monkeypatch.setattr(status, "_lift", lambda s=None: {
        "fresh": False, "height_mm": None, "inhibit": None,
        "motion": None, "fault": None, "fault_active": False,
    })
    out = status._robot_state()
    assert out["state"] == "DEGRADED"
    assert "lift module not responding" in out["reason"]


def test_robot_state_boot_grace_suppresses_not_fresh_lift_fault(monkeypatch):
    # I1: a fresh Supervisor() (self.ready == False, matching real boot) must NOT latch the
    # required-module fault yet, even though the lift is genuinely not-fresh — every boot
    # starts with the Tab5 silent for the first several seconds while WiFi associates, and
    # this must not require a manual DEGRADED clear on every single boot.
    sup = supervisor_mod.Supervisor()
    assert sup.ready is False
    monkeypatch.setattr(status, "supervisor", sup)
    monkeypatch.setattr(status.stamplc.reader, "snapshot", lambda: _benign_snapshot())
    monkeypatch.setattr(status, "_lift", lambda s=None: {
        "fresh": False, "height_mm": None, "inhibit": None,
        "motion": None, "fault": None, "fault_active": False,
    })
    out = status._robot_state()
    assert "lift module not responding" not in out["reason"]
    assert out["state"] != "DEGRADED"


def test_robot_state_wires_lift_fault_active_to_mast_without_degrading(monkeypatch):
    monkeypatch.setattr(status, "_lift_watch", {"link_since": None, "silent_since": None})
    monkeypatch.setattr(status, "supervisor", supervisor_mod.Supervisor())
    monkeypatch.setattr(status.stamplc.reader, "snapshot", lambda: _benign_snapshot())
    monkeypatch.setattr(status, "_lift", lambda s=None: {
        "fresh": True, "height_mm": 100.0, "inhibit": False,
        "motion": "STOP", "fault": "STUCK", "fault_active": True,
    })
    out = status._robot_state()
    assert out["state"] != "DEGRADED"
    assert out["subsystems"]["mast"]["state"] == "FAULT"


def test_stale_link_does_not_raise_a_lift_fault(monkeypatch):
    """A StamPLC reboot — i.e. every OTA flash — must not latch DEGRADED. With the link stale the
    module cannot report by definition, so that is a link problem, not a lift problem. Observed
    on the robot: an OTA left it DEGRADED with 'lift module not responding'."""
    sup = supervisor_mod.Supervisor()
    sup.ready = True
    monkeypatch.setattr(status, "supervisor", sup)
    snap = _benign_snapshot()
    snap["fresh"] = False                      # the StamPLC link itself is stale
    monkeypatch.setattr(status.stamplc.reader, "snapshot", lambda: snap)
    monkeypatch.setattr(status, "_lift", lambda s=None: {
        "fresh": False, "height_mm": None, "inhibit": None,
        "motion": None, "fault": None, "fault_active": False,
    })
    out = status._robot_state()
    assert "lift module not responding" not in out["reason"]


def test_a_boot_link_blip_does_not_latch_lift_not_responding():
    """opendubs2, 2026-10-19: STALE TELEMETRY for 7 s at boot; the link came back before the first
    [DRIVE] line, the lift read "not fresh" for that moment, and DEGRADED latched with a healthy lift
    until a hand clear. A blip must not latch; a lift really silent 2 s raises it and clears itself."""
    import status
    from supervisor import LIFT_SILENT, Supervisor
    sup, w = Supervisor(), {"link_since": None, "silent_since": None}
    sup.ready = True

    def tick(t, link, lift):
        raw = [LIFT_SILENT] if status.lift_not_responding(link, lift, t, w) else []
        return sup.step(raw_estop_sources=[], raw_faults=raw, fresh=link, control_ok=True, uptime=100 + t)["faults"]

    for i in range(70):                                   # the link stale for 7 s
        assert tick(i * 0.1, False, False) == []
    assert tick(7.0, True, False) == []                   # fresh again, no [DRIVE] yet
    assert tick(7.15, True, True) == []                   # the first [DRIVE]: the lift is there
    for i in range(30):
        assert tick(7.2 + i * 0.1, True, True) == []      # nothing latched

    for i in range(25):                                   # now really silent, the link fresh
        faults = tick(20 + i * 0.1, True, False)
    assert faults == [LIFT_SILENT]
    assert tick(23.0, True, True) == []                   # it answers: cleared by itself


def test_router_report_is_unknown_when_missing_or_stale(tmp_path):
    """The contract (network-router-api.md §4): a missing or >15 s old network.json is UNKNOWN —
    a poller that died must not leave its last state on the operator's screen."""
    import json
    sample = os.path.join(os.path.dirname(__file__), "..", "..", "..", "..",
                          "tools", "network", "sample-network.json")
    p = tmp_path / "network.json"
    assert status._router_report(str(p))["uplink"]["state"] == "UNKNOWN"
    data = {"ts": 1000.0, "uplink": {"state": "WEAK", "say": "Uplink weak", "signal_dbm": -83},
            "robot_wifi": [{"name": "tab", "ip": "10.42.2.13", "signal_dbm": -48, "ccq_pct": 83}]}
    if os.path.exists(sample):                 # the Network lane's sample, when it is on main
        data = json.load(open(sample))
    p.write_text(json.dumps(data))
    fresh = status._router_report(str(p), now=data["ts"] + 2)
    assert fresh["uplink"]["state"] == data["uplink"]["state"] and fresh["uplink"]["age_s"] == 2
    assert fresh["robot_wifi"] == data["robot_wifi"]
    stale = status._router_report(str(p), now=data["ts"] + 16)
    assert stale == {"uplink": {"state": "UNKNOWN", "say": "No network data"}, "robot_wifi": []}
