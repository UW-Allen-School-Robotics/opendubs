#!/usr/bin/env python3
"""The smoothed arm path (motion.py): the shape every show's arms follow, on the robot and in the twins."""
from __future__ import annotations

import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, ".."))

import arm_output  # noqa: E402
import cue  # noqa: E402
import motion  # noqa: E402
import sim_preview  # noqa: E402

FAILS = []


def check(what, ok, detail=""):
    print(("  ok    " if ok else "  FAIL  ") + what)
    if not ok:
        FAILS.append(f"{what}: {detail}")


def kf(start, t, **joints):
    pose = {str(j): float(joints.get(f"j{j}", 0.0)) for j in range(1, 8)}
    return {"start": start, "t": t, "pose": pose, "pose_signed": pose}


def sample(path, t0, t1, dt=0.001):
    out, t = [], t0
    while t <= t1 + 1e-12:
        out.append((t, path.at(t), path.speed(t)))
        t += dt
    return out


print("motion: the smoothed path")
# A wrist wave on the beat: 0 -> +15 -> -15 -> +15 -> 0, a keyframe every 0.49 s.
B = 0.4878
wave = [kf(0.0, 1.0, j7=0), kf(1.0, 1.0 + B, j7=15), kf(1.0 + B, 1.0 + 2 * B, j7=-15),
        kf(1.0 + 2 * B, 1.0 + 3 * B, j7=15), kf(1.0 + 3 * B, 1.0 + 4 * B, j7=0)]
P = motion.Path(motion.knots(wave, "pose"))
check("every keyframe is reached exactly at its time",
      all(abs(P.at(k["t"])[6] - k["pose"]["7"]) < 1e-9 for k in wave), [P.at(k["t"])[6] for k in wave])
sp = [abs(P.speed(k["t"])[6]) for k in wave[1:4]]
check("at each reversal the joint slows to ZERO, instead of flipping speed", max(sp) < 1e-9, sp)
s = sample(P, 1.0, 1.0 + 4 * B)
jumps = max(abs(b[2][6] - a[2][6]) for a, b in zip(s, s[1:]))
check("the speed is continuous: no step anywhere (under 1 deg/s between 1 ms samples)", jumps < 1.0, jumps)
avg = 30 / B
peak = max(abs(x[2][6]) for x in s if 1.0 + B <= x[0] <= 1.0 + 2 * B)
check("a reversal-to-reversal swing peaks at 1.5x its average — inside the trip refusal's 1.875x",
      abs(peak / avg - motion.PEAK_TO_AVERAGE) < 0.01 and motion.PEAK_TO_AVERAGE < cue.PEAK_FACTOR, peak / avg)
check("it never overshoots: the wave stays within +-15",
      max(abs(x[1][6]) for x in s) <= 15 + 1e-9, max(abs(x[1][6]) for x in s))

# A joint that keeps going the same way through a keyframe: 0 -> 20 -> 50, no stop at 20.
through = [kf(0.0, 1.0, j4=20), kf(1.0, 2.0, j4=50)]
Q = motion.Path(motion.knots(through, "pose"))
check("passing THROUGH a keyframe the same way, the joint keeps moving (no stop)",
      Q.speed(1.0)[3] > 15, Q.speed(1.0)[3])
s = sample(Q, 0.0, 2.0)
check("... monotone: it never goes backwards or past 50",
      all(b[1][3] >= a[1][3] - 1e-9 for a, b in zip(s, s[1:])) and max(x[1][3] for x in s) <= 50 + 1e-9)
check("... and no segment peaks above 1.5x its own average",
      max(abs(x[2][3]) for x in s if x[0] <= 1.0) <= 1.5 * 20 + 1e-6
      and max(abs(x[2][3]) for x in s if x[0] >= 1.0) <= 1.5 * 30 + 1e-6)

# A hold: arrive at 2 s, hold until 3 s, then move again.
held = [kf(0.0, 2.0, j2=30), kf(3.0, 4.0, j2=0)]
H = motion.Path(motion.knots(held, "pose"))
check("a hold holds: still at 30 between arriving and setting off, at zero speed",
      all(abs(H.at(t)[1] - 30) < 1e-9 and abs(H.speed(t)[1]) < 1e-9 for t in (2.0, 2.5, 3.0)))

print("\nmotion: the robot and the twin play the same path")
lim = cue.load_limits(os.path.join(HERE, "joint_limits_fixture.yaml"))
tl = cue.load_show(os.path.join(HERE, "fixtures", "shows", "robotic-smile.yaml")).export(lim, None)
kfs = tl["tracks"]["ARM_R"]
pts = arm_output.track_points(kfs)
worst = 0.0
for t, sig in pts[::7]:
    twin = sim_preview.arm_pose(kfs, t)
    worst = max(worst, max(abs(sig[j - 1] - twin[str(j)]) for j in range(1, 8)))  # right arm: signed == outward
check("the points sent to the right arm match the twin's pose at the same times (Robotic smile)",
      worst < 1e-3, worst)
spacing = max(b[0] - a[0] for a, b in zip(pts, pts[1:]))
check("they are sent densely enough to follow the curve", spacing <= arm_output.DENSE_S + 1e-6, spacing)

tl2 = cue.load_show(os.path.join(HERE, "fixtures", "shows", "robotic-smile.yaml")).export(lim, None)
for slot in ("ARM_L", "ARM_R"):
    P = motion.Path(motion.knots(tl2["tracks"][slot], "pose_signed"))
    worst = 0.0
    for j in range(1, 8):
        t = 0.0
        while t <= P.end:
            v = abs(P.speed(t)[j - 1])
            worst = max(worst, v / cue.SPEED_CAP_DEG_S[j])
            t += 0.002
    check(f"Robotic smile's {slot}: the smoothed peaks stay under every joint's cap", worst < 1.0, worst)

print()
if FAILS:
    for f in FAILS:
        print("FAILED:", f)
    sys.exit(1)
print("all good")
