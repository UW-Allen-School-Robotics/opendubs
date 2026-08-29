import os
import sys
import threading

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import stamplc


def _blank_reader():
    """A StampReader with parser-relevant state initialized but WITHOUT running
    __init__ (which spawns the background I/O thread — module-level `stamplc.reader`
    already runs one; a pure parse-logic test doesn't want a second)."""
    r = stamplc.StampReader.__new__(stamplc.StampReader)
    r._lock = threading.Lock()
    r._d = {}
    r._last = None
    return r


def test_lift_line_parses_all_fields():
    r = _blank_reader()
    r._parse("[LIFT] live=1 height_mm=420.5 inhibit=0 at_top=0 at_bot=1 tof_ok=1 motion=UP fault=NONE")
    assert r._d["lift"] == {
        "live": True,
        "height_mm": 420.5,
        "inhibit": False,
        "at_top": False,
        "at_bot": True,
        "tof_ok": True,
        "motion": "UP",
        "fault": "NONE",
    }
    assert r._last is not None


def test_lift_line_missing_height_is_none_and_fault_carries():
    r = _blank_reader()
    r._parse("[LIFT] live=0 inhibit=1 at_top=0 at_bot=0 tof_ok=0 motion=STOP fault=STUCK")
    lift = r._d["lift"]
    assert lift["live"] is False
    assert lift["height_mm"] is None
    assert lift["inhibit"] is True
    assert lift["fault"] == "STUCK"
