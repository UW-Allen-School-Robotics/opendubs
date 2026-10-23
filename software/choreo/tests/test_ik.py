#!/usr/bin/env python3
"""IK targets in shows (ik.py, plan item 1). The validator's rules run anywhere; the solver's checks
need MuJoCo and skip, saying so, without it."""
from __future__ import annotations

import math
import os
import shutil
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, ".."))

import cue  # noqa: E402

LIMITS_PATH = os.path.join(HERE, "joint_limits_fixture.yaml")
LIMITS = cue.load_limits(LIMITS_PATH)
FAILS = []


def check(what, ok, detail=""):
    print(("  ok    " if ok else "  FAIL  ") + what)
    if not ok:
        FAILS.append(f"{what}: {detail}")


def show(arm_cue):
    return cue.Show({"id": "t", "slots": ["ARM_R"], "requires": {"arms": "active", "rc_authority": "AUTO"},
                     "cues": [arm_cue]}, "test")


print("ik: the validator's rules")
msgs = [q.msg for q in show({"t": 2.0, "slot": "ARM_R", "reach": "head_screen"}).analyse(LIMITS)["problems"]]
check("a target without compiled angles is refused, saying how to compile it", any("ik.py compile" in m for m in msgs), msgs)
solved = {1: 10.0, 2: 10.0, 3: 0.0, 4: 40.0, 5: 0.0, 6: 0.0, 7: 0.0}
msgs = [q.msg for q in show({"t": 2.0, "slot": "ARM_R", "reach": "head", "solved": solved,
                             "solved_for": "reach:head_screen@abcd1234"}).analyse(LIMITS)["problems"]]
check("angles solved for a DIFFERENT target are stale: refused until compiled again", any("edited" in m for m in msgs), msgs)
a = show({"t": 2.0, "slot": "ARM_R", "reach": "head_screen", "solved": solved,
          "solved_for": "reach:head_screen@abcd1234"}).analyse(LIMITS)
kf = [e for e in a["events"] if e.get("kind") == "keyframe"]
check("compiled angles are checked and played like hand-written joints",
      not a["problems"] and kf and all(abs(kf[0]["pose"][j] - solved[j]) < 1e-9 for j in range(1, 8)), (a["problems"], kf[:1]))
msgs = [q.msg for q in show({"t": 2.0, "slot": "ARM_R", "reach": "head", "joints": {4: 30}}).analyse(LIMITS)["problems"]]
check("a cue may carry only one of joints, pose, reach or point_at", any("exactly one" in m for m in msgs), msgs)
bad = {**solved, 4: 400.0}
msgs = [q.msg for q in show({"t": 2.0, "slot": "ARM_R", "point_at": "head", "solved": bad,
                             "solved_for": "point_at:head@x"}).analyse(LIMITS)["problems"]]
check("compiled angles get every usual check — outside the limits is still refused", any("J4" in m for m in msgs), msgs)

print("\nik: the solver (MuJoCo)")
try:
    import mujoco  # noqa: F401
    import numpy as np
except ImportError:
    print("  skip  MuJoCo is not installed here")
else:
    import ik
    import sim_preview
    tmp = tempfile.mkdtemp(prefix="opendubs-ik-")
    demo = os.path.join(tmp, "ik_demo.yaml")
    shutil.copy(os.path.join(HERE, "ik_demo.yaml"), demo)
    problems = ik.compile_show(demo, LIMITS_PATH, write=True, verbose=False)
    tl = cue.load_show(demo).export(LIMITS, LIMITS_PATH)
    check("the demo's four targets compile and the show validates", not problems and tl["valid"], (problems, tl["problems"]))
    kin = ik.Kin(LIMITS)

    def at(t):
        kin.pose(sim_preview.sample(tl, t))
        return kin

    site = lambda side: kin.d.site_xpos[mujoco.mj_name2id(kin.m, mujoco.mjtObj.mjOBJ_SITE, ik.TOOL_SITE.format(side=side))]
    zaxis = lambda side: kin.d.site_xmat[mujoco.mj_name2id(kin.m, mujoco.mjtObj.mjOBJ_SITE, ik.TOOL_SITE.format(side=side))].reshape(3, 3)[:, 2]
    at(2.0)
    goal = kin.point([0.55, -0.25, 0.75], "right")
    check("a reach puts the tool site (ee_site) on its point, within 1 cm",
          np.linalg.norm(site("right") - goal) < ik.REACH_TOL_M, np.linalg.norm(site("right") - goal))
    at(6.0)
    v = kin.point([2.0, 0.6, 1.4], "left") - site("left")
    ang = math.degrees(math.acos(float(zaxis("left") @ (v / np.linalg.norm(v)))))
    check("a point_at aims the tool's +z axis at its target, within 2 deg", ang < ik.POINT_TOL_DEG, ang)
    at(8.0)
    screen = kin.point("head_screen", "right", standoff=0.0)
    d = np.linalg.norm(site("right") - screen)
    check("a reach to a named part of the robot stops at its standoff, not on it",
          abs(d - ik.STANDOFF_M) < 0.02, d)
    lo4 = {sd: math.degrees(LIMITS[f"openarm_{sd}_joint4"][0]) for sd in ("left", "right")}
    b4 = {sd: math.degrees(kin._bounds(sd)[3][0]) for sd in ("left", "right")}
    import yaml as _yaml
    j4 = [c["solved"][4] for c in _yaml.safe_load(open(demo))["cues"] if isinstance(c, dict) and "solved" in c]
    check("the elbow's bound is the ROBOT's limit (joint_limits.yaml) plus the margin, not the MJCF's wider range",
          all(abs(b4[sd] - (lo4[sd] + cue.MARGIN_DEG)) < 1e-6 for sd in b4)
          and all(v >= min(lo4.values()) + cue.MARGIN_DEG for v in j4), (b4, j4))
    up = os.path.join(tmp, "up.yaml")
    with open(up, "w") as fh:
        fh.write('id: up\nslots: [ARM_L, ARM_R]\nrequires: {arms: active, rc_authority: AUTO}\ncues:\n'
                 # out to the sides (y +-0.30): the Galaxy head (2026-10-23) is wide, and nearer the
                 # middle an arm raised overhead meets its clearance before the box; this check is
                 # about the box
                 '  - { t: 4.0, slot: ARM_L, reach: [0.45, 0.30, 1.65] }\n'
                 '  - { t: 4.0, slot: ARM_R, reach: [0.45, -0.30, 1.65] }\n')
    p = ik.compile_show(up, LIMITS_PATH, write=True, verbose=False)
    tu = cue.load_show(up).export(LIMITS, LIMITS_PATH)
    sol = [c["solved"] for c in _yaml.safe_load(open(up))["cues"] if "solved" in c]
    # The arm CONTROLLERS refuse a hand above their box (hand_tcp z <= 1.2 m, tcp.py; found at the robot
    # 2026-10-12), so the solver refuses such a target itself — no angles written, and how much lower
    check("a hand raised overhead past the arm controllers' box is REFUSED by the solver, saying how much lower",
          len(p) == 2 and all("outside their box" in x and "lower this target" in x for x in p) and not sol
          and not tu["valid"], (p, sol))
    with open(up, "w") as fh:
        fh.write('id: up\nslots: [ARM_L, ARM_R]\nrequires: {arms: active, rc_authority: AUTO}\ncues:\n'
                 '  - { t: 4.0, slot: ARM_L, reach: [0.42, 0.25, 1.66] }\n'
                 '  - { t: 4.0, slot: ARM_R, reach: [0.45, -0.25, 1.65] }\n')
    p = ik.compile_show(up, LIMITS_PATH, write=True, verbose=False)
    sol = [c["solved"] for c in _yaml.safe_load(open(up))["cues"] if "solved" in c]
    check("... nearer the middle, a hand raised overhead comes too close to the head and is refused, naming it",
          len(p) == 2 and all("head_collision" in x for x in p) and not sol, (p, sol))
    with open(up, "w") as fh:
        fh.write('id: up\nslots: [ARM_L, ARM_R]\nrequires: {arms: active, rc_authority: AUTO}\ncues:\n'
                 '  - { t: 4.0, slot: ARM_L, reach: [0.42, 0.25, 1.45] }\n'
                 '  - { t: 4.0, slot: ARM_R, reach: [0.5, -0.28, 1.45] }\n')
    p = ik.compile_show(up, LIMITS_PATH, write=True, verbose=False)
    tu = cue.load_show(up).export(LIMITS, LIMITS_PATH)
    sol = [c["solved"] for c in _yaml.safe_load(open(up))["cues"] if "solved" in c]
    check("... a hand raised overhead inside the box is solved, within the shoulder rules, and validates",
          not p and tu["valid"] and len(sol) == 2
          and all(abs(q[2]) <= cue.SHOULDER_EXTENDED_MAX_DEG or q[4] >= cue.ELBOW_FOLD_MIN_DEG for q in sol),
          (p, tu["problems"], sol))
    far = os.path.join(tmp, "far.yaml")
    with open(far, "w") as fh:
        fh.write('id: far\nslots: [ARM_R]\nrequires: {arms: active, rc_authority: AUTO}\n'
                 'cues:\n  - { t: 3.0, slot: ARM_R, reach: [3.0, 0.0, 1.0] }\n')
    p = ik.compile_show(far, LIMITS_PATH, write=True, verbose=False)
    check("an out-of-reach target is refused and gets NO angles (the validator then refuses it too)",
          p and "out of reach" in p[0] and "solved" not in open(far).read(), p)
    saved = ik.CLEAR_MIN_MM
    ik.CLEAR_MIN_MM = 10000.0
    p = ik.compile_show(demo, LIMITS_PATH, write=False, verbose=False)
    ik.CLEAR_MIN_MM = saved
    check("a move that comes too close to the robot's own body is refused, naming the parts",
          p and all("within" in x for x in p), p[:1])

print()
if FAILS:
    for f in FAILS:
        print("FAILED:", f)
    sys.exit(1)
print("all good")
