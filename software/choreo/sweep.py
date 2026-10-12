#!/usr/bin/env python3
"""Self-collision along a whole show, on the path the robot actually plays — and the stamp that
carries the result to the robot.

    python3 sweep.py check SHOW     # sweep it, print the closest approach (writes nothing)
    python3 sweep.py stamp SHOW     # sweep it and write `clearance:` into the show (the composer does
                                    # this on every save)

WHY. Arm targets (reach/point_at) are collision-checked when ik.py solves them; arm JOINT keyframes
were not, and neither was the motion BETWEEN keyframes. A show written by hand (or by an agent)
moved both arms into each other (-48 mm), and only a sweep on the twin caught it. So every show
that moves an arm is now swept, end to end, on motion.py's smoothed path (the one arm_output sends
the robot and the twins play), against the twin's collision model (sim/clearance.py: arm-arm,
arm-head, arm-body — the same pairs ik.py checks), with the head, lift and grippers where the show
has them. Closer than THRESHOLD_MM anywhere is a VALIDATOR ERROR.

HOW IT REACHES THE ROBOT. The robot validates without MuJoCo, so the sweep runs where MuJoCo is
(the composer on save, or this tool) and writes its result into the show:

    clearance: { min_mm: 47.2, at_s: 3.8, checked_for: "<motion hash>@<model sig>" }

The validator (cue.py) recomputes the MOTION HASH from the show itself — every arm, gripper, head,
lift and base event, with its time and pose — and refuses a show that moves an arm unless the stamp
is present, matches its motion as it is now, and is at least THRESHOLD_MM. Edit any motion and the
stamp is stale until the show is swept again; edit a face or a light and it stays valid.

HOW FINE. Samples every COARSE_S and at every keyframe time (so no joint reverses between two
samples). Between two samples no distance can change by more than the motion that happened: each
joint's change times how far beyond it the arm reaches (JOINT_LEVER_M), plus the head's, the lift's
and the fingers' movement.
Wherever that bound cannot rule out dipping under the threshold, the interval is halved and
sampled again, down to MIN_STEP_S. So the minimum is not a sampling accident: it is bounded.
"""
from __future__ import annotations

import argparse
import math
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(HERE))
sys.path[:0] = [HERE, os.path.join(REPO, "sim"), os.path.join(HERE, "composer")]

import cue  # noqa: E402

THRESHOLD_MM = 30.0          # the same margin ik.py keeps for a solved target (CLEAR_MIN_MM)
COARSE_S = 0.08            # the bound refines wherever this could miss a dip
MIN_STEP_S = 0.002
# How far any point beyond each joint can be from that joint's axis (the arm's segments, measured in
# the twin: shoulder to elbow 0.222 m, elbow to tool 0.398 m, the fingers past it): a joint moving by
# dq moves nothing by more than lever x dq. J1-J3 at the shoulder, J4-J5 at the elbow, J6-J7 the wrist.
JOINT_LEVER_M = (0.66, 0.66, 0.66, 0.44, 0.44, 0.22, 0.22)
HEAD_REACH_M = 0.30          # any point of the head from its pan/tilt axes
GRIP_TRAVEL_M = 0.044       # a finger's full travel (the vendored model's slide)


def _state(sp, smp):
    """The parts of a sample that change self-distances: both arms (radians), head, lift, fingers."""
    q = []
    for side in ("left", "right"):
        q.append([math.radians(float(smp[side].get(str(j), smp[side].get(j, 0.0)))) for j in range(1, 8)])
    grips = (float(smp.get("grip_left", 0.0)) + float(smp.get("grip_right", 0.0))) * GRIP_TRAVEL_M
    return q, math.radians(smp["head_pan"]), math.radians(smp["head_tilt"]), smp["lift_mm"] / 1000.0, grips


def _bound_mm(s0, s1) -> float:
    """How much any self-distance can change between two samples, at most (mm)."""
    (q0, p0, t0, l0, g0), (q1, p1, t1, l1, g1) = s0, s1
    arms = sum(abs(b - a) * lever for qa, qb in zip(q0, q1) for a, b, lever in zip(qa, qb, JOINT_LEVER_M))
    head = (abs(p1 - p0) + abs(t1 - t0)) * HEAD_REACH_M
    return (arms + head + abs(l1 - l0) + abs(g1 - g0)) * 1000.0


class Sweeper:
    def __init__(self, head: str | None = None):
        import mujoco
        import scene
        self.mj, self.scene = mujoco, scene
        self.m = scene.build(None, head=head or scene.fitted_head())
        self.d = mujoco.MjData(self.m)

    def at(self, tl, t):
        """(closest mm, geom a, geom b, state) for the show at time t."""
        import clearance
        import sim_preview as P
        smp = P.sample(tl, t)
        clearance.set_pose(self.m, self.d, P.joints_arg(smp), smp["lift_mm"], smp["head_pan"] - 90,
                           smp["head_tilt"] - 90)
        P.apply_grips(self.m, self.d, smp)
        c = clearance.clearances(self.m, self.d, limit_mm=500.0)
        mm, a, b = c[0] if c else (float("inf"), None, None)
        return mm, a, b, _state(None, smp)

    def sweep(self, tl) -> dict:
        dur = float(tl.get("duration_s") or 0.0)
        times = {round(k * COARSE_S, 6) for k in range(int(dur / COARSE_S) + 2)}
        for slot in ("ARM_L", "ARM_R", "HEAD", "LIFT"):
            for e in (tl.get("tracks") or {}).get(slot, []):
                for k in ("t", "start", "end"):
                    if e.get(k) is not None:
                        times.add(round(float(e[k]), 6))
        times = sorted(t for t in times if 0.0 <= t <= dur + COARSE_S)
        samples = {t: self.at(tl, t) for t in times}
        best = min(((v[0], t, v[1], v[2]) for t, v in samples.items()), key=lambda x: x[0])
        n = len(samples)
        stack = list(zip(times, times[1:]))
        while stack:
            a, b = stack.pop()
            ca, cb = samples[a], samples[b]
            if max(ca[0], cb[0]) - _bound_mm(ca[3], cb[3]) >= THRESHOLD_MM or b - a <= MIN_STEP_S:
                continue
            mid = round((a + b) / 2, 6)
            samples[mid] = v = self.at(tl, mid)
            n += 1
            if v[0] < best[0]:
                best = (v[0], mid, v[1], v[2])
            stack += [(a, mid), (mid, b)]
        return {"min_mm": round(best[0], 1), "at_s": round(best[1], 3), "pair": [best[2], best[3]],
                "samples": n}


def stamp_text(text: str, result: dict, mhash: str, sig: str) -> str:
    import yamledit
    value = {"min_mm": result["min_mm"], "at_s": result["at_s"], "between": " / ".join(map(str, result["pair"])),
             "checked_for": f"{mhash}@{sig}"}
    return yamledit.apply(text, [{"op": "top", "key": "clearance", "value": value}])


def run(show_path: str, limits_path: str, write: bool, force: bool = False) -> dict:
    """Sweep a show (and stamp it). A show whose stamp already matches its motion is not swept
    again unless `force`: saving a face, a light or a note costs nothing."""
    import ik
    show = cue.load_show(show_path)
    limits = cue.load_limits(limits_path)
    mhash = cue.motion_hash(show.analyse(limits)["events"])
    st = show.doc.get("clearance")
    if not force and isinstance(st, dict) and str(st.get("checked_for", "")).split("@")[0] == mhash:
        return {"min_mm": float(st.get("min_mm", 0.0)), "at_s": st.get("at_s"),
                "pair": str(st.get("between", "? / ?")).split(" / ") + ["?"], "samples": 0,
                "motion": mhash, "current": True}
    tl = show.export(limits, limits_path)
    res = Sweeper().sweep(tl)
    res["motion"] = mhash
    if write:
        with open(show_path, encoding="utf-8") as fh:
            text = fh.read()
        text = stamp_text(text, res, res["motion"], ik.model_sig())
        tmp = show_path + ".sweep-tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            fh.write(text)
        os.replace(tmp, show_path)
    return res


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("cmd", choices=("check", "stamp"))
    ap.add_argument("show")
    ap.add_argument("--limits", default=cue.DEFAULT_LIMITS if os.path.isfile(cue.DEFAULT_LIMITS)
                    else os.path.join(HERE, "tests", "joint_limits_fixture.yaml"))
    a = ap.parse_args()
    import time
    t0 = time.time()
    r = run(a.show, a.limits, a.cmd == "stamp", force=a.cmd == "check")
    verdict = "OK" if r["min_mm"] >= THRESHOLD_MM else f"TOO CLOSE (under {THRESHOLD_MM:g} mm)"
    print(f"{os.path.basename(a.show)}: closest {r['min_mm']} mm at {r['at_s']} s ({r['pair'][0]} / {r['pair'][1]}) "
          f"— {verdict}; {r['samples']} samples in {time.time() - t0:.1f} s"
          + ("; stamped" if a.cmd == "stamp" else ""))
    return 0 if r["min_mm"] >= THRESHOLD_MM else 1


if __name__ == "__main__":
    sys.exit(main())
