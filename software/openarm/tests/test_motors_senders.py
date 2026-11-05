#!/usr/bin/env python3
"""openarm-motors' `senders` counts another controller's commands, and never the frames any stack's
status checks put on the bus: the dashboard's 0xCC queries, register reads, the motors' replies.
Counting those would refuse every ACTIVATE while the dashboard is open. Off the robot, no CAN needed.
Run: python3 software/openarm/tests/test_motors_senders.py
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


def kind(can_id, hexdata):
    return m.command_kind(can_id, bytes.fromhex(hexdata))


# Not commands: what openarm-motors and the dashboard send, and what the motors answer.
check("status query (0xCC)", kind(0x7FF, "0100cc0000000000"), None)
check("register read (0x33)", kind(0x7FF, "0200330e00000000"), None)
check("a motor's status reply", kind(0x011, "017fff7ff7ff1716"), None)
check("the gripper's reply", kind(0x018, "087fff7ff7ff1716"), None)
# Commands: another controller at work.
check("MIT command to J1 (gafro, the ROS stack)", kind(0x001, "7fff7ff000000800") is not None, True)
check("enable to the gripper", kind(0x008, "fffffffffffffffc") is not None, True)
check("velocity-mode command", kind(0x203, "0000000000000000") is not None, True)
check("register write", kind(0x7FF, "0100550a01000000") is not None, True)
check("register save", kind(0x7FF, "0100aa0000000000") is not None, True)
sys.exit(1 if fails else 0)
