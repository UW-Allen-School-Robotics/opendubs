#!/usr/bin/env python3
"""How often each OpenArm motor answers on a CAN link, and what its registers say.

  python3 software/openarm/gafro/tools/can_reply_rates.py rates     [--iface arm_left] [--seconds 5]
  python3 software/openarm/gafro/tools/can_reply_rates.py registers [--iface arm_left] [--ids 1 2 3] [--rounds 30]

Read-only: it sends status requests (0x7FF, id, 0xCC) and register reads (0x7FF, id, 0x33, rid), nothing else: no enable, disable, command or register write. Run it with
the arm service for that arm stopped (the replies would also reach a running driver, which is harmless, but a sweep adds load to its bus).

`rates` refreshes all eight ids back to back, as the CAN library's refresh_all does, at several round intervals, and counts the replies that carry a MOS temperature (a
status frame; the acknowledgement of a register write does not). A healthy motor answers about every round. On opendubs2 (2026-10-29) left joint 1 answered 1-3 % of requests
and joint 2 about 17 %, at every rate: the replies per second scale with how often it is asked, the fraction does not. That is why the arm service polls at about 750 Hz while it
confirms the start pose (`start_interval`, `start_attempts` in the arm's hardware config) and why the old stack, which polls at 400 Hz, never noticed.

`registers` reads a few registers of the given motors (control mode, CAN bit rate, ids, timeout) `--rounds` times each and shows what came back. A motor that answers none of them
is not talking at all.
"""
import argparse
import collections
import socket
import struct
import time

BAUD = {0: "125k", 1: "200k", 2: "250k", 3: "500k", 4: "1M", 5: "2M", 6: "2.5M", 7: "3.2M", 8: "4M", 9: "5M"}
REGISTERS = {35: "CAN bit rate (can_br)", 7: "MST_ID", 8: "ESC_ID", 10: "CTRL_MODE", 9: "TIMEOUT"}


def open_socket(iface):
    s = socket.socket(socket.AF_CAN, socket.SOCK_RAW, socket.CAN_RAW)
    s.setsockopt(socket.SOL_CAN_RAW, socket.CAN_RAW_FD_FRAMES, 1)
    s.bind((iface,))
    s.settimeout(0.0005)
    return s


def send(s, data8):
    s.send(struct.pack("=IBBBB", 0x7FF, 8, 1, 0, 0) + (data8 + bytes(56))[:64])   # CAN FD, bit-rate switch, as the library sends


def frames(s, until):
    while time.time() < until:
        try:
            b = s.recv(72)
        except socket.timeout:
            continue
        cid = struct.unpack("=I", b[:4])[0] & 0x1FFFFFFF
        yield cid, b[8:8 + b[4]]


def rates(iface, seconds):
    s = open_socket(iface)
    print(f"{iface}: all 8 ids refreshed back to back each round; {seconds:g} s per setting; replies that carry a temperature")
    print("round interval | rounds/s | J1 replies/s (ratio) | J2 replies/s (ratio) | J3 ratio | J4-J8 ratio")
    for period in (0.020, 0.010, 0.005, 0.0025, 0.001):
        count, rounds, t0 = collections.Counter(), 0, time.time()
        while time.time() - t0 < seconds:
            t = time.time()
            try:
                for i in range(1, 9):
                    send(s, bytes([i, 0, 0xCC, 0, 0, 0, 0, 0]))
            except OSError:                       # the transmit queue is full: the interval is shorter than the link can carry
                time.sleep(0.01)
                continue
            rounds += 1
            for cid, d in frames(s, t + period):
                if 0x11 <= cid <= 0x18 and len(d) == 8 and d[2] not in (0x33, 0x55) and d[6] > 0:
                    count[cid - 0x10] += 1
        for cid, d in frames(s, time.time() + 0.2):
            pass
        dt = time.time() - t0
        rest = sum(count[j] for j in range(4, 9)) / max(rounds * 5, 1)
        print(f"{period * 1000:9.1f} ms   | {rounds / dt:7.0f}  | {count[1] / dt:8.1f} ({100 * count[1] / rounds:5.1f}%)  | {count[2] / dt:8.1f} ({100 * count[2] / rounds:5.1f}%)  | "
              f"{100 * count[3] / rounds:5.1f}%   | {100 * rest:5.1f}%")


def registers(iface, ids, rounds):
    s = open_socket(iface)
    got, asked = collections.defaultdict(collections.Counter), collections.Counter()
    for _ in range(rounds):
        for mid in ids:
            for rid in REGISTERS:
                send(s, bytes([mid & 0xFF, mid >> 8, 0x33, rid, 0, 0, 0, 0]))
                asked[(mid, rid)] += 1
                for cid, d in frames(s, time.time() + 0.012):
                    if len(d) >= 8 and d[2] == 0x33:
                        got[(d[0] | (d[1] << 8), d[3])][struct.unpack("<I", d[4:8])[0]] += 1
    print(f"{iface}: {rounds} read queries per register per motor")
    print("motor  register                replies   values")
    for mid in ids:
        for rid, name in REGISTERS.items():
            c = got.get((mid, rid), {})
            values = ", ".join((f"{v} ({BAUD.get(v, '?')})" if rid == 35 else str(v)) + f" x{k}" for v, k in c.items()) or "-"
            print(f"  J{mid}   {name:22s} {sum(c.values()):3d}/{asked[(mid, rid)]:<3d}   {values}")


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = p.add_subparsers(dest="mode", required=True)
    r = sub.add_parser("rates"); r.add_argument("--iface", default="arm_left"); r.add_argument("--seconds", type=float, default=5.0)
    g = sub.add_parser("registers"); g.add_argument("--iface", default="arm_left"); g.add_argument("--ids", type=int, nargs="+", default=[1, 2, 3]); g.add_argument("--rounds", type=int, default=30)
    a = p.parse_args()
    rates(a.iface, a.seconds) if a.mode == "rates" else registers(a.iface, a.ids, a.rounds)
