#!/usr/bin/env python3
"""openarm-motors reads a motor as missing only when it answered no round, not when one reply was
lost. Regression for 2026-10-21: left J1 and J2 lost 20% and 9% of their replies on a bus with
errors, and a single lost reply read as "no reply". Off the robot, no CAN needed.
Run: python3 software/openarm/tests/test_motors_rounds.py
"""
import importlib.machinery
import importlib.util
import os
import socket
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
PATH = os.environ.get("MOTORS_OVERRIDE", os.path.join(HERE, "..", "openarm-motors"))
loader = importlib.machinery.SourceFileLoader("openarm_motors", PATH)
spec = importlib.util.spec_from_loader("openarm_motors", loader)
m = importlib.util.module_from_spec(spec)
loader.exec_module(m)


class FakeBus:
    """Answers each refresh with an ENABLED status frame, except the replies `lose` names
    (motor -> which of its replies, counting from 1, are lost) and motors in `silent`."""

    def __init__(self, lose=None, silent=()):
        self.lose, self.silent, self.asked, self.queue = lose or {}, set(silent), {}, []

    def send(self, b):
        can_id, _n, _f, _a, _b, data = m.FRAME.unpack(b)
        if can_id == 0x7FF and data[2] == 0xCC:
            mid = data[0]
            self.asked[mid] = self.asked.get(mid, 0) + 1
            if mid not in self.silent and self.asked[mid] not in self.lose.get(mid, ()):
                d = bytes([0x10 | mid, 0x80, 0x00, 0x7F, 0xF8, 0x00, 0x1A, 0x18])
                self.queue.append(m.FRAME.pack(0x10 + mid, 8, 0, 0, 0, d.ljust(64, b"\0")))

    def recv(self, _n):
        if not self.queue:
            raise socket.timeout
        return self.queue.pop(0)


fails = 0


def check(name, ok):
    global fails
    fails += not ok
    print(("ok   " if ok else "FAIL ") + name)


ids = list(range(1, 9))
latest, heard = m.poll(FakeBus(lose={1: {1}, 2: {2}}), ids, window=0.01)
check("a motor whose one reply was lost is not missing", latest[1] is not None and latest[2] is not None)
check("and it is reported as intermittent (answered 2 of 3)", heard[1] == 2 and heard[2] == 2 and heard[3] == 3)
latest, heard = m.poll(FakeBus(silent={8}), ids, window=0.01)
check("a motor that answers no round is missing", latest[8] is None and heard[8] == 0 and latest[7] is not None)

sys.exit(1 if fails else 0)
