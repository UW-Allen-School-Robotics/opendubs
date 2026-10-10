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


def person(left_dir, right_dir, left_bend=0.0, right_bend=0.0, yaw_deg=0.0):
    """MediaPipe-style world landmarks (metres; x right in the image, y DOWN, z AWAY from the camera)
    for a person facing the camera, turned `yaw_deg` about the vertical. Each arm points along a
    direction given in the PERSON's frame (forward, left, up); `bend` folds the elbow (0 = straight)."""
    fwd, left, up = np.array([0, 0, -1.0]), np.array([1.0, 0, 0]), np.array([0, -1.0, 0])
    a = math.radians(yaw_deg)
    turn = lambda v: np.array([v[0] * math.cos(a) + v[2] * math.sin(a), v[1], -v[0] * math.sin(a) + v[2] * math.cos(a)])
    fwd, left, up = turn(fwd), turn(left), turn(up)
    B = np.vstack([fwd, left, up]).T            # person (f, l, u) -> camera
    lm = {"left_hip": 0.1 * left, "right_hip": -0.1 * left,
          "left_shoulder": 0.18 * left + 0.5 * up, "right_shoulder": -0.18 * left + 0.5 * up}
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
cues, notes = P.propose_cues(lm, "t:2.0")
check("an arm straight out to the person's left: ARM_L's target is straight out to the robot's left, at full reach",
      np.allclose(target(cues, "ARM_L"), SL + [0, FULL, 0], atol=0.005), target(cues, "ARM_L"))
check("an arm straight forward: ARM_R's target is straight in front of its shoulder",
      np.allclose(target(cues, "ARM_R"), SR + [FULL, 0, 0], atol=0.005), target(cues, "ARM_R"))
check("the cue lands at the asked time, as an ordinary reach cue",
      all(c["t"] == 2.0 and set(c) == {"t", "slot", "reach"} for c in cues), cues)
turned = P.propose_cues(person((0, 1, 0), (1, 0, 0), yaw_deg=45), "t:2.0")[0]
check("the camera angle does not matter: the same pose photographed at 45° gives the same targets",
      all(np.allclose(target(turned, s), target(cues, s), atol=0.002) for s in ("ARM_L", "ARM_R")),
      [target(turned, s) for s in ("ARM_L", "ARM_R")])
down = P.propose_cues(person((0, 0, -1), (0, 0, -1)), "bar:3.1")[0]
check("arms hanging: the targets hang below the shoulders, and a bar time is kept as a bar",
      np.allclose(target(down, "ARM_L"), SL - [0, 0, FULL], atol=0.005) and all(c["bar"] == "3.1" for c in down), down)
bent = P.propose_cues(person((1, 0, 0), (1, 0, 0), left_bend=math.radians(90)), "t:1")[0]
ext = np.linalg.norm(target(bent, "ARM_L") - SL) / P.ROBOT_REACH_M
check("a bent elbow brings the hand in: the same fraction of the robot's reach as of the person's",
      0.6 < ext < 0.85, ext)
mir = P.propose_cues(lm, "t:2.0", mirror=True)[0]
check("--mirror: the person's left arm (out to their left) drives ARM_R, out to the robot's RIGHT",
      np.allclose(target(mir, "ARM_R"), SR + [0, -FULL, 0], atol=0.005), target(mir, "ARM_R"))
one = P.propose_cues(lm, "t:2.0", arms=("right",))[0]
check("--arms right: only ARM_R is proposed", [c["slot"] for c in one] == ["ARM_R"], one)
vis = {k: 1.0 for k in P.LM}
vis["left_wrist"] = 0.1
hid, notes = P.propose_cues(lm, "t:2.0", vis=vis)
check("a hand out of view is skipped and said, never guessed",
      [c["slot"] for c in hid] == ["ARM_R"] and any("not clearly in view" in n for n in notes), (hid, notes))
pt = P.propose_cues(lm, "t:2.0", point=True)[0]
check("--point proposes point_at, far out along the person's arm",
      all("point_at" in c for c in pt) and target(pt, "ARM_R")[0] > 2.0, pt)

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
    for what, dirs in (("a hand behind the back", (-1, 0, -0.2)), ("the left hand reaching across to the right", (0.2, -1, 0))):
        problems, out, valid = solve(P.propose_cues(person(dirs, (1, 0, 0)), "t:3.0", arms=("left",))[0])
        check(f"a pose this arm cannot copy ({what}) is REFUSED with the reason, not bent to fit",
              problems and "out of reach" in problems[0] and not any("solved" in c for c in out if "reach" in c), problems)

print()
if FAILS:
    for f in FAILS:
        print("FAILED:", f)
    sys.exit(1)
print("all good")
