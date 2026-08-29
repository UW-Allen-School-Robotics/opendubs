import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import status


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


def test_lift_fault_none_string_is_not_active(monkeypatch):
    # Firmware reports the literal string "NONE" for no-fault, not an absent key.
    monkeypatch.setattr(status.stamplc.reader, "snapshot", lambda: {
        "fresh": True,
        "lift": {"live": True, "fault": "NONE"},
    })
    out = status._lift()
    assert out["fault_active"] is False
