#!/usr/bin/env python3
"""openarm-motors accepts every status frame, including one whose position's low byte is 0x33 or
0x55, and still skips register replies. Off the robot, no CAN needed.
Run: python3 software/openarm/tests/test_motors_parse.py
"""
import importlib.machinery
import importlib.util
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
PATH = os.environ.get("MOTORS_OVERRIDE", os.path.join(HERE, "..", "openarm-motors"))
loader = importlib.machinery.SourceFileLoader("openarm_motors", PATH)
spec = importlib.util.spec_from_loader("openarm_motors", loader)
m = importlib.util.module_from_spec(spec)
loader.exec_module(m)

fails = 0


def check(name, got, want):
    global fails
    ok = got == want
    fails += not ok
    print(("ok   " if ok else "FAIL ") + name + ("" if ok else f": got {got!r}, want {want!r}"))


def state(mid, hexdata, can_id=None):
    r = m.parse(mid, 0x10 + mid if can_id is None else can_id, 8, bytes.fromhex(hexdata))
    return None if r is None else (r[0], round(r[1], 2))


# Captured on opendubs2 2026-10-12: right J1 held at +1.13 deg, position 0x8033.
check("status frame, position low byte 0x33", state(1, "1180337fe7fd1917"), ("ENABLED", 1.13))
check("status frame, position low byte 0x55", state(4, "1480557ff7fc1918"), ("ENABLED", 1.87))
check("status frame, DISABLED, low byte 0x55", state(2, "02 7f 55 7f f7 fe 1a 17".replace(" ", "")),
      ("DISABLED", -3.73))
check("ordinary status frame", state(3, "137ffa7ff8151a18"), ("ENABLED", -0.12))
check("register read reply skipped", state(1, "0100330a00000000"), None)
check("register write reply skipped", state(8, "0800550a01000000"), None)
check("another motor's reply ignored", state(1, "1280337fe7fd1917", can_id=0x12), None)
check("short frame ignored", m.parse(1, 0x11, 4, bytes.fromhex("1180337fe7fd1917")), None)

sys.exit(1 if fails else 0)
