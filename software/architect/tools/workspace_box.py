#!/usr/bin/env python3
"""Propose each arm's workspace box from the twin: where its gripper reaches cleanly.

    python3 software/architect/tools/workspace_box.py --limits <joint_limits.yaml>          # ~1 min on 32 cores
    python3 software/architect/tools/workspace_box.py --limits <...> --step 0.1 --jobs 8    # coarser, quicker

The workspace box (live_arms.ArmConfig, the arm config's `workspace:`) is the region, in the chassis
frame (x forward, y left, z up, metres), that a program may send an arm's gripper into. A target
outside it is refused before it is planned. This proposes one by asking the planner the live
service uses (live_arms.Planner): from stow, which grid points does each arm reach within
ik.REACH_TOL_M and at least ik.CLEAR_MIN_MM from the robot's body and the other arm? It then fits the
largest box whose every grid point is clean, inside these bounds:
  * in front of the arm column and the head (x >= --x-min, default 0.50; they end at x 0.45 and 0.51);
  * above the base (z >= --z-min, default 0.50; the base ends at z 0.36);
  * below the head (z <= --z-max, default 1.10; the head starts at z 1.16);
  * on the arm's own side of the centre line (|y| >= --y-min, default 0.05).
It proposes ONE box for both arms, mirrored about the centre line: the overlap of the two arms'
boxes (each arm's own largest box can be clean from stow and still not clean inside it). Last, it
checks every move between the box's corners and centre, from each to every other.

It proposes; a person decides. The twin's geometry is from CAD, about 6 degrees optimistic at the
shoulder (sim/clearance.py), and knows nothing about what stands in front of the robot. Run it with
the robot's own joint limits: the test fixture's are not any robot's.
"""
from __future__ import annotations

import argparse
import itertools
import json
import multiprocessing as mp
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, ".."))

_P = _la = _STOW = None


def _init(limits_path):
    global _P, _la, _STOW
    from opendubs_architect import live_arms
    import cue
    _la = live_arms
    lim = cue.load_limits(limits_path)
    _P = live_arms.Planner(lim, live_arms.ArmConfig())
    _STOW = {live_arms.ARM_SLOT[s]: live_arms.to_signed(s, [cue.rest_pose(lim, s)[j] for j in range(1, 8)])
             for s in live_arms.SIDES}


def _reach(job):
    side, p = job
    try:
        _P.plan(side, _STOW, point=list(p))
        return side, p, True
    except _la.Refused:
        return side, p, False


def _moves(job):
    side, pts, i = job
    first = _P.plan(side, _STOW, point=pts[i])
    pose = dict(_STOW)
    pose[_la.ARM_SLOT[side]] = first.goal
    out = []
    for j, t in enumerate(pts):
        if j != i:
            try:
                m = _P.plan(side, pose, point=t)
                out.append((i, j, None, m.clearance_mm))
            except _la.Refused as e:
                out.append((i, j, f"{e.code}: {e.reason}", None))
    return side, out


def grid(lo, hi, step):
    return [round(lo + k * step, 3) for k in range(int(round((hi - lo) / step)) + 1)]


def best_box(ok: dict, xs, ys, zs):
    """The largest box (by volume, then by its smallest side) of grid points that are all clean."""
    nx, ny, nz = len(xs), len(ys), len(zs)
    P = {}
    for i, j, k in itertools.product(range(nx + 1), range(ny + 1), range(nz + 1)):
        P[i, j, k] = 0 if not (i and j and k) else (
            ok[i - 1, j - 1, k - 1] + P[i - 1, j, k] + P[i, j - 1, k] + P[i, j, k - 1]
            - P[i - 1, j - 1, k] - P[i - 1, j, k - 1] - P[i, j - 1, k - 1] + P[i - 1, j - 1, k - 1])
    best = None
    for a, A in itertools.combinations(range(nx + 1), 2):
        for b, B in itertools.combinations(range(ny + 1), 2):
            for c, C in itertools.combinations(range(nz + 1), 2):
                n = P[A, B, C] - P[a, B, C] - P[A, b, C] - P[A, B, c] + P[a, b, C] + P[a, B, c] + P[A, b, c] - P[a, b, c]
                if n != (A - a) * (B - b) * (C - c):
                    continue
                sides = (xs[A - 1] - xs[a], ys[B - 1] - ys[b], zs[C - 1] - zs[c])
                key = (round(sides[0] * sides[1] * sides[2], 6), min(sides))
                if best is None or key > best[0]:
                    best = (key, {"x": [xs[a], xs[A - 1]], "y": [ys[b], ys[B - 1]], "z": [zs[c], zs[C - 1]]})
    return best[1] if best else None


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--limits", required=True, help="the arm stack's joint_limits.yaml")
    ap.add_argument("--step", type=float, default=0.05, help="grid spacing, metres")
    ap.add_argument("--jobs", type=int, default=max(1, (os.cpu_count() or 2) - 2))
    ap.add_argument("--x-min", type=float, default=0.50)
    ap.add_argument("--x-max", type=float, default=0.80)
    ap.add_argument("--y-min", type=float, default=0.05, help="|y| at least this: the arm's own side")
    ap.add_argument("--y-max", type=float, default=0.60)
    ap.add_argument("--z-min", type=float, default=0.50)
    ap.add_argument("--z-max", type=float, default=1.10)
    a = ap.parse_args()
    os.environ.setdefault("OMP_NUM_THREADS", "1")

    xs, zs = grid(a.x_min, a.x_max, a.step), grid(a.z_min, a.z_max, a.step)
    ys = {"left": grid(a.y_min, a.y_max, a.step)}
    ys["right"] = sorted(-y for y in ys["left"])
    jobs = [(s, (x, y, z)) for s in ("left", "right") for x in xs for y in ys[s] for z in zs]
    print(f"probing {len(jobs)} points with {a.jobs} workers", file=sys.stderr)
    with mp.Pool(a.jobs, initializer=_init, initargs=(a.limits,)) as pool:
        reached = pool.map(_reach, jobs, chunksize=4)
        boxes = {}
        for s in ("left", "right"):
            idx = {p: (xs.index(p[0]), ys[s].index(p[1]), zs.index(p[2])) for side, p, _ in reached if side == s}
            ok = {idx[p]: int(good) for side, p, good in reached if side == s}
            boxes[s] = best_box(ok, xs, ys[s], zs)
            n_ok = sum(ok.values())
            print(f"{s}: {n_ok}/{len(ok)} points clean; box {boxes[s]}", file=sys.stderr)
        if not (boxes["left"] and boxes["right"]):
            print("no clean box for an arm inside these bounds", file=sys.stderr)
            return 1
        L, R = boxes["left"], boxes["right"]
        shared = {"x": [max(L["x"][0], R["x"][0]), min(L["x"][1], R["x"][1])],
                  "y": [max(L["y"][0], -R["y"][1]), min(L["y"][1], -R["y"][0])],
                  "z": [max(L["z"][0], R["z"][0]), min(L["z"][1], R["z"][1])]}
        if any(lo >= hi for lo, hi in shared.values()):
            print(f"the arms' boxes do not overlap when mirrored: {boxes}", file=sys.stderr)
            return 1
        each = boxes
        boxes = {"left": shared, "right": {**shared, "y": [-shared["y"][1], -shared["y"][0]]}}
        print(f"proposed for both arms: {boxes}", file=sys.stderr)
        moves = [(s, [list(p) for p in itertools.product(*(boxes[s][k] for k in "xyz"))]
                  + [[sum(boxes[s][k]) / 2 for k in "xyz"]], i) for s in boxes if boxes[s] for i in range(9)]
        results = pool.map(_moves, moves)
    report = {"limits": a.limits, "step_m": a.step, "workspace": boxes, "each_arm_alone": each, "moves": {}}
    for s in boxes:
        rows = [r for side, out in results if side == s for r in out]
        bad = [r for r in rows if r[2]]
        report["moves"][s] = {"checked": len(rows), "refused": [f"{i}->{j}: {why}" for i, j, why, _ in bad],
                              "closest_mm": min((r[3] for r in rows if not r[2]), default=None)}
    print(json.dumps(report, indent=2))
    return 0 if all(not m["refused"] for m in report["moves"].values()) else 1


if __name__ == "__main__":
    sys.exit(main())
