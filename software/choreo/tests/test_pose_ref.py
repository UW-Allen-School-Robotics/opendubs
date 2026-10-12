#!/usr/bin/env python3
"""Reference images (pose_ref.py, plan item 1): a person's arm pose becomes a robot arm target. The
mapping runs on synthetic landmarks, so it needs no pose model; the last checks solve the proposed
cues on the twin's model and skip, saying so, without MuJoCo."""
from __future__ import annotations

import math
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, ".."))

import numpy as np  # noqa: E402

import pose_ref as P  # noqa: E402

LIMITS_PATH = os.path.join(HERE, "joint_limits_fixture.yaml")
FAILS = []


def check(what, ok, detail=""):
    print(("  ok    " if ok else "  FAIL  ") + what)
    if not ok:
        FAILS.append(f"{what}: {detail}")


UPPER, FORE, HAND = 0.28, 0.26, 0.08


def person(left_dir, right_dir, left_bend=0.0, right_bend=0.0, yaw_deg=0.0, lean_deg=0.0):
    """MediaPipe-style world landmarks (metres; x right in the image, y DOWN, z AWAY from the camera)
    for a person facing the camera, turned `yaw_deg` about the vertical. Each arm points along a
    direction given in the PERSON's frame (forward, left, up); `bend` folds the elbow (0 = straight)."""
    fwd, left, up = np.array([0, 0, -1.0]), np.array([1.0, 0, 0]), np.array([0, -1.0, 0])
    a = math.radians(yaw_deg)
    turn = lambda v: np.array([v[0] * math.cos(a) + v[2] * math.sin(a), v[1], -v[0] * math.sin(a) + v[2] * math.cos(a)])
    fwd, left, up = turn(fwd), turn(left), turn(up)
    B = np.vstack([fwd, left, up]).T            # person (f, l, u) -> camera; the arms are given in this LEVEL frame
    # the torso may lean sideways (about forward) by lean_deg: its own left/up tilt, the arms do not
    b = math.radians(lean_deg)
    t_left, t_up = left * math.cos(b) + up * math.sin(b), up * math.cos(b) - left * math.sin(b)
    lm = {"left_hip": 0.1 * t_left, "right_hip": -0.1 * t_left,
          "left_shoulder": 0.18 * t_left + 0.5 * t_up, "right_shoulder": -0.18 * t_left + 0.5 * t_up}
    for side, d, bend in (("left", left_dir, left_bend), ("right", right_dir, right_bend)):
        d = B @ (np.asarray(d, float) / np.linalg.norm(d))
        s = lm[f"{side}_shoulder"]
        e = s + UPPER * d
        # fold the forearm toward `up` by `bend` radians
        f = d * math.cos(bend) + up * math.sin(bend)
        w = e + FORE * f
        h = w + HAND * f
        lm.update({f"{side}_elbow": e, f"{side}_wrist": w, f"{side}_index": h + 0.02 * fwd,
                   f"{side}_pinky": h - 0.02 * fwd})
    return {k: list(v) for k, v in lm.items()}


def target(cues, slot):
    c = next(c for c in cues if c["slot"] == slot)
    return np.array(c.get("reach") or c.get("point_at"))


SL, SR = np.array(P.ROBOT_SHOULDER["left"]), np.array(P.ROBOT_SHOULDER["right"])
FULL = P.ROBOT_REACH_M * P.MAX_EXTENSION

print("pose_ref: the person's arm, in their own frame")
lm = person(left_dir=(0, 1, 0), right_dir=(1, 0, 0))     # left arm out to the side, right arm forward
cues, notes = P.propose_cues(lm, "t:2.0", elbows=False)
check("an arm straight out to the person's left: ARM_L's target is straight out to the robot's left, at full reach",
      np.allclose(target(cues, "ARM_L"), SL + [0, FULL, 0], atol=0.005), target(cues, "ARM_L"))
check("an arm straight forward: ARM_R's target is straight in front of its shoulder",
      np.allclose(target(cues, "ARM_R"), SR + [FULL, 0, 0], atol=0.005), target(cues, "ARM_R"))
check("--no-elbows: the cue lands at the asked time, as an ordinary reach cue (hand only)",
      all(c["t"] == 2.0 and set(c) == {"t", "slot", "reach"} for c in cues), cues)
turned = P.propose_cues(person((0, 1, 0), (1, 0, 0), yaw_deg=45), "t:2.0", elbows=False)[0]
check("the camera angle does not matter: the same pose photographed at 45° gives the same targets",
      all(np.allclose(target(turned, s), target(cues, s), atol=0.002) for s in ("ARM_L", "ARM_R")),
      [target(turned, s) for s in ("ARM_L", "ARM_R")])
down = P.propose_cues(person((0, 0, -1), (0, 0, -1)), "bar:3.1", elbows=False)[0]
check("arms hanging: the targets hang below the shoulders, and a bar time is kept as a bar",
      np.allclose(target(down, "ARM_L"), SL - [0, 0, FULL], atol=0.005) and all(c["bar"] == "3.1" for c in down), down)
bent = P.propose_cues(person((1, 0, 0), (1, 0, 0), left_bend=math.radians(90)), "t:1", elbows=False)[0]
ext = np.linalg.norm(target(bent, "ARM_L") - SL) / P.ROBOT_REACH_M
check("a bent elbow brings the hand in: the same fraction of the robot's reach as of the person's",
      0.6 < ext < 0.85, ext)
mir = P.propose_cues(lm, "t:2.0", mirror=True, elbows=False)[0]
check("--mirror: the person's left arm (out to their left) drives ARM_R, out to the robot's RIGHT",
      np.allclose(target(mir, "ARM_R"), SR + [0, -FULL, 0], atol=0.005), target(mir, "ARM_R"))
one = P.propose_cues(lm, "t:2.0", arms=("right",))[0]
check("--arms right: only ARM_R is proposed", [c["slot"] for c in one] == ["ARM_R"], one)
vis = {k: 1.0 for k in P.LM}
vis["left_wrist"] = 0.1
hid, notes = P.propose_cues(lm, "t:2.0", vis=vis)
check("a hand out of view is skipped and said, never guessed",
      [c["slot"] for c in hid] == ["ARM_R"] and any("not clearly in view" in n for n in notes), (hid, notes))
pt = P.propose_cues(lm, "t:2.0", point=True, elbows=False)[0]
check("--point proposes point_at, far out along the person's arm",
      all("point_at" in c for c in pt) and target(pt, "ARM_R")[0] > 2.0, pt)

print("\npose_ref: up is gravity's, the facing is the body's (--frame)")
lean = person((0, 1, 0), (0, 0, -1), lean_deg=-30)         # a level left arm on a body leaning 30° (as photo 1)
g = target(P.propose_cues(lean, "t:1", elbows=False)[0], "ARM_L")
tq = target(P.propose_cues(lean, "t:1", elbows=False, frame="torso")[0], "ARM_L")
check("a LEVEL arm on a leaning body stays level on the robot (gravity frame, the default)",
      abs(g[2] - SL[2]) < 0.005, g)
check("... and --frame torso copies it relative to the leaning torso: raised (the old behaviour, by choice)",
      tq[2] - SL[2] > 0.2, tq)

print("\npose_ref: the arm's SHAPE — elbow: beside reach: (the default)")
sh = P.propose_cues(person((0, 1, 0), (1, 0, 0), left_bend=math.radians(90)), "t:2.0")[0]
c_l = next(c for c in sh if c["slot"] == "ARM_L")
check("each cue carries elbow: beside reach:", set(c_l) == {"t", "slot", "reach", "elbow"}, c_l)
check("the upper arm is laid out at the robot's upper-arm length: out to the side, 0.222 m",
      np.allclose(c_l["elbow"], SL + [0, P.UPPER_ARM_M, 0], atol=0.002), c_l["elbow"])
check("... and the forearm from that elbow at the robot's forearm length: straight up, bent 90°",
      np.allclose(c_l["reach"], SL + [0, P.UPPER_ARM_M, P.FOREARM_M], atol=0.002), c_l["reach"])
st = target(P.propose_cues(person((0, 1, 0), (1, 0, 0)), "t:2.0")[0], "ARM_L")
check("a straight arm still stops short of fully straight (a singular pose)",
      abs(np.linalg.norm(st - SL) - FULL) < 0.003, np.linalg.norm(st - SL))

print("\npose_ref: the proposals, solved on the twin (MuJoCo)")
try:
    import mujoco  # noqa: F401
except ImportError:
    print("  skip  MuJoCo is not installed here")
else:
    import cue
    import ik
    import yaml
    sys.path.insert(0, os.path.join(HERE, "..", "composer"))
    import yamledit

    def solve(cues):
        path = os.path.join(tempfile.mkdtemp(), "ref.yaml")
        text = "id: ref\nslots: [ARM_L, ARM_R]\nrequires: {arms: active, rc_authority: AUTO}\ncues:\n" \
               "  - { t: 0.5, slot: ARM_L, pose: rest }\n"
        text = yamledit.apply(text, [{"op": "add", "cue": c} for c in cues])
        open(path, "w").write(text)
        problems = ik.compile_show(path, LIMITS_PATH, write=True, verbose=False)
        valid = cue.load_show(path).export(cue.load_limits(LIMITS_PATH), LIMITS_PATH)["valid"]
        return problems, yaml.safe_load(open(path))["cues"], valid

    reach = P.propose_cues(person((1, 0, 0.3), (0.5, -0.3, 0.6), right_bend=math.radians(40)), "t:3.0")[0]
    problems, out, valid = solve(reach)
    check("a forward reach and a raised, bent arm: both solved, and the show validates",
          not problems and valid and all("solved" in c for c in out if "reach" in c), problems)
    y = P.propose_cues(person((0, 0.5, 1), (0, -0.5, 1)), "t:3.0")[0]
    problems, out, valid = solve(y)
    check("arms up in a Y: solved by folding the elbows (the shoulder rules), and the show validates",
          not problems and valid, problems)
    problems, out, valid = solve(P.propose_cues(person((0.2, -1, 0), (1, 0, 0)), "t:3.0", arms=("left",))[0])
    check("a pose this arm cannot copy (the left hand reaching across to the right) is REFUSED with the reason",
          problems and "out of reach" in problems[0] and not any("solved" in c for c in out if "reach" in c), problems)
    behind = person((-1, 0, -0.2), (1, 0, 0))
    problems, out, valid = solve(P.propose_cues(behind, "t:3.0", arms=("left",))[0])
    check("a hand behind the back, WITH the arm's shape, is found inside the shoulder rules — and validates",
          not problems and valid and all("solved" in c for c in out if "reach" in c), problems)
    bent_up = P.propose_cues(person((0, 1, 0), (0.3, -1, 0.2), left_bend=math.radians(90)), "t:3.0")[0]
    problems, out, valid = solve(bent_up)
    arm = next(c for c in out if c.get("slot") == "ARM_L" and "reach" in c)
    check("a bent arm's shape is matched: the hand on target, the elbow within 1 cm, written as elbow_off",
          not problems and valid and arm.get("elbow_off") is not None and arm["elbow_off"] <= 0.01, (problems, arm.get("elbow_off")))
    show = cue.Show({"id": "x", "slots": ["ARM_L"], "requires": {"arms": "active", "rc_authority": "AUTO"},
                     "cues": [{"t": 0.5, "slot": "ARM_L", "pose": "rest"}, dict(arm, elbow=[0.4, 0.5, 1.4])]}, "x")
    check("editing the elbow makes the solved angles stale (it is part of the target)",
          any("edited after" in q.msg for q in show.analyse(cue.load_limits(LIMITS_PATH))["problems"]))
    far = cue.Show({"id": "x", "slots": ["ARM_L"], "requires": {"arms": "active", "rc_authority": "AUTO"},
                    "cues": [{"t": 0.5, "slot": "ARM_L", "pose": "rest"}, dict(arm, elbow_off=0.09)]}, "x").analyse(cue.load_limits(LIMITS_PATH))
    check("an elbow 9 cm from its target is a WARNING that says so — the show stays valid, the hand is on target",
          not far["problems"] and any("9 cm" in w.msg for w in far["warnings"]), [w.msg for w in far["warnings"]])

print()
if FAILS:
    for f in FAILS:
        print("FAILED:", f)
    sys.exit(1)
print("all good")
