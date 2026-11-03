#!/usr/bin/env python3
"""Summarise the move recordings of the arm service (`--record-dir`): which joint deviated from its path, when, and how old its feedback was.

  python3 software/openarm/gafro/tools/move_summary.py [directory]           (default /var/tmp/arm-rec)

One block per CSV (`arm_service-<side>-<move>-<time>.csv`, about 450 samples a second: joint angles q1..q7, velocities, torques, references ref1..ref7 and `error`, the largest
joint deviation the follower checks against its limit). Per joint it prints the travel, the largest |q - ref| and when it happened, the share of samples in which the angle did not change
(a stale reading, or a joint that is not moving) and the longest such run, and the largest torque. The last two columns are for the joints commanded to move: the longest feedback gap
while the reference was moving faster than 0.1 rad/s (over 0.1 s), and the error that gap would produce at the joint's peak speed. A move that stopped on TRACKING shows up as a peak near the limit.
"""
import csv
import glob
import os
import sys


def summarise(path):
    rows = list(csv.DictReader(open(path)))
    n, t = len(rows), [float(r["t"]) for r in rows]
    dur = t[-1] - t[0]
    dt = dur / n
    q = [[float(r[f"q{j}"]) for r in rows] for j in range(1, 8)]
    ref = [[float(r[f"ref{j}"]) for r in rows] for j in range(1, 8)]
    err = [float(r["error"]) for r in rows]
    print(f"\n{os.path.basename(path)}: {n} samples over {dur:.1f} s ({n / dur:.0f}/s); `error` peaks at {max(err):.3f} rad (t={t[err.index(max(err))]:.2f} s)")
    print("  joint | travel | max |q-ref| (t)    | unchanged: share, longest ms | gap while moving ms, peak rad/s, implied rad | tau max")
    for j in range(7):
        e = [abs(a - b) for a, b in zip(q[j], ref[j])]
        k = e.index(max(e))
        same = [q[j][i] == q[j][i - 1] for i in range(1, n)]
        window = max(1, round(0.1 / dt))                  # speed over 0.1 s: consecutive samples are too coarse (the loop and the recorder do not tick together)
        speed = [abs(ref[j][min(i + window, n - 1)] - ref[j][i]) / (window * dt) for i in range(n - 1)]
        run = longest = mrun = mlongest = 0
        for i, s in enumerate(same):
            run = run + 1 if s else 0
            longest = max(longest, run)
            mrun = mrun + 1 if s and speed[i] > 0.1 else 0
            mlongest = max(mlongest, mrun)
        peak = max(speed)
        tau = max(abs(float(r[f"tau{j + 1}"])) for r in rows)
        print(f"   J{j + 1}   | {max(ref[j]) - min(ref[j]):5.2f}  | {max(e):6.3f} ({t[k]:5.2f} s)   | {100 * sum(same) / len(same):5.1f}%  {1000 * longest * dt:6.0f}         | "
              f"{1000 * mlongest * dt:5.0f}  {peak:5.2f}  {mlongest * dt * peak:5.2f}                  | {tau:5.1f}")


if __name__ == "__main__":
    directory = sys.argv[1] if len(sys.argv) > 1 else "/var/tmp/arm-rec"
    files = sorted(glob.glob(os.path.join(directory, "arm_service-*.csv")))
    if not files:
        sys.exit(f"no arm_service-*.csv in {directory} (is the unit running with --record-dir?)")
    for f in files:
        summarise(f)
