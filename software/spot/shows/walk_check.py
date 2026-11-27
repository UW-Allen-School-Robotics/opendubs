"""The walking check for the first walking session at Spot: a 60 s plan in 5 s windows that walks a
little forward and back in each step style, so a person can watch two things.
- Do the windows chain without a stutter? Each walk crosses a window boundary mid-stride (at 10, 20,
  30 and 40 s), so a pause there is a chaining fault.
- Which step style looks like tip-toeing for Sugar Plum: CRAWL or a slow TROT, both with swing HIGH?

    uv run --no-project --python 3.12 --with bosdyn-client==5.1.4 python software/spot/shows/walk_check.py [--plans DIR]

It writes walk-check.spot-plan.json to ~/.opendubs-spot/plans (copy it to the Orin's). The player plays
it only when started with --walking. Spot stays within 0.4 m of where it starts, at no more than about
0.06 m/s and 14 deg/s, under a 0.15 m/s speed cap:

   0- 5 s  stand (TROT, MEDIUM)
   5-15 s  CRAWL, swing HIGH: forward 0.4 m      15-25 s  CRAWL, swing HIGH: back to the start
  25-35 s  TROT, swing HIGH: forward 0.4 m       35-45 s  TROT, swing HIGH: back to the start
  45-55 s  TROT, MEDIUM: turn 45 deg left and back in place
  55-60 s  stand
"""
import argparse
import json
import math
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))   # software/spot

from spot_playback import _monotone

WINDOW_S, LEAD_S, END_S = 5.0, 1.0, 60.0
KEYS = [  # (t, x m, angle deg): the base's keys, in Spot's starting frame
    (0, 0.0, 0), (5, 0.0, 0), (15, 0.4, 0), (25, 0.0, 0), (35, 0.4, 0), (45, 0.0, 0), (50, 0.0, 45),
    (55, 0.0, 0), (60, 0.0, 0)]
STYLE = [  # (from s, gait, swing): changes only on window boundaries
    (0, "TROT", "MEDIUM"), (5, "CRAWL", "HIGH"), (25, "TROT", "HIGH"), (45, "TROT", "MEDIUM")]
SPEED_CAP = 0.15


def plan():
    ts = [k[0] for k in KEYS]
    fx = _monotone(ts, [k[1] for k in KEYS])
    fa = _monotone(ts, [k[2] for k in KEYS])
    commands = []
    for w in range(int(END_S / WINDOW_S)):
        t0, t1 = w * WINDOW_S, (w + 1) * WINDOW_S
        times = sorted({t0, t1} | {t for t in ts if t0 < t < t1})
        gait, swing = [(g, s) for start, g, s in STYLE if start <= t0][-1]
        commands.append({
            "window": w, "part": "BASE", "show_start_s": t0,
            "sdk": "synchro_se2_trajectory_command (SE2Trajectory in vision: Spot steps there itself)",
            "mobility": {"gait": gait, "swing": swing, "speed_cap": SPEED_CAP},
            "points": [{"time_since_reference": round(t - t0, 4),
                        "pose": {"x": round(fx(t), 4), "y": 0.0, "angle": round(math.radians(fa(t)), 5)},
                        "march": False} for t in times]})
    return {"robot": "spot", "driver": "spot_sdk", "built": True, "windows": len(commands), "window_s": WINDOW_S,
            "lead_s": LEAD_S, "commands": commands, "problems": [],
            "made_by": "opendubs software/spot/shows/walk_check.py"}


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--plans", default="~/.opendubs-spot/plans")
    a = ap.parse_args()
    out = os.path.join(os.path.expanduser(a.plans), "walk-check.spot-plan.json")
    os.makedirs(os.path.dirname(out), exist_ok=True)
    with open(out, "w") as f:
        json.dump(plan(), f, indent=1)
    print(f"wrote {out}")


if __name__ == "__main__":
    main()
