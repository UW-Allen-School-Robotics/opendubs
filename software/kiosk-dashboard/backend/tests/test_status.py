import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import status
import supervisor as supervisor_mod


def _benign_snapshot():
    """A StampPLC snapshot that's fresh and reports no faults from any OTHER
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
    # StampPLC link itself is stale — doesn't matter what the module's own `live`
    # flag says, `_lift()["fresh"]` must be False (same combination `_rc_link()` uses).
    monkeypatch.setattr(status.stamplc.reader, "snapshot", lambda: {
        "fresh": False,
        "lift": {"live": True, "height_mm": 100.0, "fault": None},
    })
    out = status._lift()
    assert out["fresh"] is False


def test_lift_stale_when_module_not_live(monkeypatch):
    # StampPLC link is fresh but the lift module itself hasn't reported live=1.
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
    # must NOT surface as fault_active — the StampPLC side (I3's other half) already stops
    # reporting a real fault while stale, but this must hold even if that path is ever
    # bypassed (e.g. a stale StampPLC link itself, independent of the module's own liveness).
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
    monkeypatch.setattr(status, "_lift", lambda: {
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
    monkeypatch.setattr(status, "_lift", lambda: {
        "fresh": False, "height_mm": None, "inhibit": None,
        "motion": None, "fault": None, "fault_active": False,
    })
    out = status._robot_state()
    assert "lift module not responding" not in out["reason"]
    assert out["state"] != "DEGRADED"


def test_robot_state_wires_lift_fault_active_to_mast_without_degrading(monkeypatch):
    monkeypatch.setattr(status, "supervisor", supervisor_mod.Supervisor())
    monkeypatch.setattr(status.stamplc.reader, "snapshot", lambda: _benign_snapshot())
    monkeypatch.setattr(status, "_lift", lambda: {
        "fresh": True, "height_mm": 100.0, "inhibit": False,
        "motion": "STOP", "fault": "STUCK", "fault_active": True,
    })
    out = status._robot_state()
    assert out["state"] != "DEGRADED"
    assert out["subsystems"]["mast"]["state"] == "FAULT"
