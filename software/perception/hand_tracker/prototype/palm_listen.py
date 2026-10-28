#!/usr/bin/env python3
"""Print the palm targets that `hand3d.py --publish` sends, to check them before an arm
process consumes them.

Run:  python palm_listen.py [--port 47800] [--calib cam_to_arm.json]

With --calib the palm is also printed in the arm model's frame (+x forward, +y left, +z up),
to check the camera-to-arm transform without moving anything: hold a palm under the gripper
and compare with where the hand is.
"""
import argparse
import json
import socket
import time


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=47800)
    ap.add_argument("--print-hz", type=float, default=5.0)
    ap.add_argument("--calib", help="cam_to_arm.json: also print palms in the arm model frame")
    args = ap.parse_args()

    R = t = None
    if args.calib:
        import numpy as np
        cal = json.load(open(args.calib))
        R, t = np.asarray(cal["R"]), np.asarray(cal["t"])
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.bind((args.host, args.port))
    except OSError as e:
        raise SystemExit(f"udp {args.host}:{args.port}: {e.strerror}; pick another port")
    sock.settimeout(1.0)
    print(f"Listening on udp://{args.host}:{args.port}. Ctrl+C to quit.")

    last_print, n, last_seq, dropped = 0.0, 0, None, 0
    try:
        while True:
            try:
                data, _ = sock.recvfrom(65536)
            except socket.timeout:
                print("(nothing for 1 s)")
                continue
            msg = json.loads(data)
            n += 1
            if last_seq is not None and msg["seq"] > last_seq + 1:
                dropped += msg["seq"] - last_seq - 1
            last_seq = msg["seq"]
            now = time.time()
            if now - last_print < 1.0 / args.print_hz:
                continue
            last_print = now
            age_ms = (now - msg["stamp"]) * 1000
            head = f"seq {msg['seq']} age {age_ms:4.0f} ms dropped {dropped}"
            if not msg["person"]:
                print(f"{head}  no person")
                continue
            print(f"{head}  person @ {msg['person_z']:.2f} m")
            for side, p in msg["palms"].items():
                if p["xyz"] is None:
                    print(f"  {side:5s} lost")
                    continue
                x, y, z = p["xyz"]
                flags = ("FOLLOW" if p["followable"] else "hold  ") + \
                    (f" still {p['still_s']:.1f}s" if p["still"] else "") + \
                    ("" if p["open"] is None else (" open" if p["open"] else " closed"))
                nrm = "" if p["normal"] is None else " n=({:+.2f},{:+.2f},{:+.2f})".format(*p["normal"])
                arm = ""
                if R is not None:
                    a = R @ np.array([x, y, z]) + t
                    arm = f"  ARM fwd={a[0]:+.3f} left={a[1]:+.3f} up={a[2]:+.3f}"
                print(f"  {side:5s} {flags:24s} x={x:+.3f} y={y:+.3f} z={z:.3f}{nrm}  [{p['source']}]{arm}")
    except KeyboardInterrupt:
        pass
    print(f"{n} messages, {dropped} dropped")


if __name__ == "__main__":
    main()
