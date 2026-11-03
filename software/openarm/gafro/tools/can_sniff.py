#!/usr/bin/env python3
"""Decode what is commanded to an OpenArm's motors, and what they answer, from the CAN link: a sniffer, read-only (it sends nothing).

  python3 software/openarm/gafro/tools/can_sniff.py [arm_left|arm_right] [seconds]        (defaults: arm_left, 90)

Every 2 s it prints, per joint, the commanded angle, kp, kd and feed-forward torque of the newest MIT frame and the angle the motor last reported; ENABLE, DISABLE and set-zero
frames and any unexpected CAN id are printed as they happen; `!!` marks a joint that moved more than 3 degrees in a quarter second or sits more than 5 degrees from its command.
Everything is also written to /tmp/<iface>-sniff.csv. Ranges are the arm service's defaults: J1-2 DM8009, J3-4 DM4340, J5-7 DM4310 (pMax 12.5; vMax 45/10/30; tMax 54/28/10).

Two traps: a motor's reply to a REGISTER WRITE is a frame on the same id that has no temperature and decodes here as a position of about -714 degrees (-12.468 rad) and a
torque of about +-24 or 8.75 N m. It is not a state. The driver's start check refuses it (a status frame carries the temperature); the link recovery used to hold the arm
from it (fixed in openarm-driver c75b615), which is how this tool found that bug. And a motor that answers few requests (left joints 1 and 2 on opendubs2) shows the last frame
it sent, so its angle can be old.
"""
import socket, struct, time, math, sys
IFACE = sys.argv[1] if len(sys.argv) > 1 else "arm_left"; SECS = float(sys.argv[2]) if len(sys.argv) > 2 else 90
LIM = {1: (12.5, 45, 54), 2: (12.5, 45, 54), 3: (12.5, 10, 28), 4: (12.5, 10, 28), 5: (12.5, 30, 10), 6: (12.5, 30, 10), 7: (12.5, 30, 10), 8: (12.5, 30, 10)}
s = socket.socket(socket.AF_CAN, socket.SOCK_RAW, socket.CAN_RAW); s.setsockopt(socket.SOL_CAN_RAW, socket.CAN_RAW_FD_FRAMES, 1); s.bind((IFACE,)); s.settimeout(0.2)
cmd, meas, seen_ids, last_meas = {}, {}, set(), {}
csv = open(f"/tmp/{IFACE}-sniff.csv", "w"); csv.write("t,joint,cmd_deg,kp,kd,tff,meas_deg,meas_tau\n")
t0 = time.time(); last_print = 0; flagged = {}
def up(v, lo, hi, bits): return lo + v * (hi - lo) / ((1 << bits) - 1)
print(f"sniffing {IFACE} for {SECS:.0f} s; MIT commands decoded with J1-2 DM8009, J3-4 DM4340, J5-7 DM4310 ranges")
while time.time() - t0 < SECS:
    now = time.time() - t0
    try: b = s.recv(72)
    except socket.timeout: b = None
    if b:
        cid = struct.unpack("=I", b[:4])[0] & 0x1FFFFFFF; n = b[4]; d = b[8:8 + n]
        if cid not in seen_ids and cid not in range(1, 9) and cid not in range(0x11, 0x19):
            seen_ids.add(cid); print(f"[{now:6.2f}] new id 0x{cid:03X}: {d.hex()}")
        if 1 <= cid <= 8 and len(d) == 8:
            if d[:7] == b"\xff" * 7:
                print(f"[{now:6.2f}] id {cid}: {'ENABLE' if d[7] == 0xFC else 'DISABLE' if d[7] == 0xFD else 'SET-ZERO' if d[7] == 0xFE else 'special 0x%02X' % d[7]}")
            else:
                pm, vm, tm = LIM[cid]
                p = up((d[0] << 8) | d[1], -pm, pm, 16); kp = up(((d[3] & 15) << 8) | d[4], 0, 500, 12); kd = up((d[5] << 4) | (d[6] >> 4), 0, 5, 12); tf = up(((d[6] & 15) << 8) | d[7], -tm, tm, 12)
                cmd[cid] = (math.degrees(p), kp, kd, tf)
        elif 0x11 <= cid <= 0x18 and len(d) == 8:
            j = cid - 0x10; pm, vm, tm = LIM[j]
            p = up((d[1] << 8) | d[2], -pm, pm, 16); tau = up(((d[4] & 15) << 8) | d[5], -tm, tm, 12)
            meas[j] = (math.degrees(p), tau, now)
            if j in cmd: csv.write(f"{now:.3f},{j},{cmd[j][0]:.2f},{cmd[j][1]:.1f},{cmd[j][2]:.2f},{cmd[j][3]:.2f},{meas[j][0]:.2f},{tau:.2f}\n")
            if j in last_meas and now - last_meas[j][1] >= 0.25:
                dq = meas[j][0] - last_meas[j][0]
                if abs(dq) > 3 and now - flagged.get(("v", j), -9) > 1: flagged[("v", j)] = now; print(f"[{now:6.2f}] !! joint {j} moved {dq:+.1f} deg in {now - last_meas[j][1]:.2f} s (meas {meas[j][0]:+.1f}, cmd {cmd.get(j, (float('nan'),))[0]:+.1f}, tff {cmd.get(j, (0, 0, 0, float('nan')))[3]:+.2f} Nm)")
                last_meas[j] = (meas[j][0], now)
            elif j not in last_meas: last_meas[j] = (meas[j][0], now)
            if j in cmd and abs(meas[j][0] - cmd[j][0]) > 5 and now - flagged.get(("e", j), -9) > 2: flagged[("e", j)] = now; print(f"[{now:6.2f}] !! joint {j}: measured {meas[j][0]:+.1f} vs commanded {cmd[j][0]:+.1f} deg (error {meas[j][0] - cmd[j][0]:+.1f}), kp {cmd[j][1]:.1f}, tff {cmd[j][3]:+.2f} Nm, reply torque {tau:+.2f}")
    if now - last_print >= 2:
        last_print = now
        if cmd or meas:
            print(f"[{now:6.2f}] joint: cmd_deg  kp    kd   tff_Nm | meas_deg  tau_Nm")
            for j in range(1, 9):
                c = cmd.get(j); m = meas.get(j)
                print(f"          J{j}:  " + (f"{c[0]:+7.1f} {c[1]:5.1f} {c[2]:4.2f} {c[3]:+6.2f}" if c else "    (no command seen)   ") + " | " + (f"{m[0]:+7.1f} {m[1]:+6.2f}" if m else "  no reply"))
print("done; full log:", f"/tmp/{IFACE}-sniff.csv")
