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


def test_drive_line_supplies_lift_when_no_dedicated_line():
    """No [LIFT] line exists today — the StamPLC owns the Z-axis and reports it inside
    [DRIVE]. Note `mast=<type>:<motion>`: the firmware's format is `mast=%s:%d`, so the
    field is "scissor:0", not "0". Taken from a real robot's telemetry."""
    r = _blank_reader()
    r._parse("[DRIVE] mode=MECANUM armed=0 spd=0.00 fwd=0.00 rot=0.00 str=0.00 turn=0 "
             "mast=scissor:2 h=24 pinch=HOLD safe=1")
    lift = r._d["lift"]
    assert lift["motion"] == "DOWN"          # not None — the type prefix must be stripped
    assert lift["height_mm"] == 24.0
    assert lift["inhibit"] is True           # pinch=HOLD
    assert lift["tof_ok"] is True
    assert lift["fault"] == "NONE"


def test_no_tof_is_a_fault_because_the_descent_is_unguarded():
    r = _blank_reader()
    r._parse("[DRIVE] mode=MECANUM armed=0 mast=scissor:0 h=-1 pinch=nosensor safe=1")
    lift = r._d["lift"]
    assert lift["tof_ok"] is False
    assert lift["fault"] == "TOF_FAULT"
    assert lift["height_mm"] is None         # h<0 means unknown, not 'minus one millimetre'
