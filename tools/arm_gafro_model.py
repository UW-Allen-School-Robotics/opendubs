#!/usr/bin/env python3
"""Build the arms' gafro model: the OpenArm model of `openarm_assets`, with joint 2 narrowed to what the head allows and its own `ready` pose.

The arm's own limits (joint ranges, rated torque and velocity, gains) are the model's, in `openarm_assets`. What is this robot's is the
head: contact with it is at J2 = 182 deg with the elbow folded, measured, and a show leaves 25 deg (software/choreo/cue.py), so the arm
is not driven past 157 deg. Left joints 1 to 3 are mirrored (sim/scene.py): the outward cap is a lower bound on the left, an upper one on
the right.

It has to be in the model and not only in the driver's config: gafro's joint-limit guard reads each joint's own limits from the model, and
a torque-controlled arm is not clamped by position anywhere else. The driver takes the same limits from the bound model.

The meshes are this repository's (`description/openarm/meshes/`), which `../meshes` reaches from the output folder.

    python3 tools/arm_gafro_model.py            regenerate description/openarm/gafro/openarm_bimanual.xml
    python3 tools/arm_gafro_model.py --check    exit 1 if it does not match what the generator builds now

Needs `openarm_assets` (github.com/personalrobotics/openarm_assets) importable, and mujoco.
"""
from __future__ import annotations

import argparse
import math
import pathlib
import sys

REPO = pathlib.Path(__file__).resolve().parent.parent
OUT = REPO / "description" / "openarm" / "gafro" / "openarm_bimanual.xml"
sys.path.insert(0, str(REPO / "software" / "choreo"))


# The ready pose: where an arm is engaged and homed to, in the model's joint order for the RIGHT arm (rad); the left is the mirror, with
# joints 1 to 3 negated (sim/scene.py). It is not stow: stow is the zero pose, where the elbow is straight and the arm is exactly singular, so
# a controller cannot start there. This one has the elbow bent 103 deg and the hand in front of the chest, the tool pointing forward:
#   smallest Jacobian singular value 0.128 (the controllers' guard floor is 0.06), smallest joint margin 0.57 rad (their guard margin is
#   0.35), 78 mm to the head and 188 mm between the arms at every lift height (tools/arm_ready_pose.py verify).
# Picked by sampling the self-motion manifolds of candidate hand poses with ssik and scoring each posture (tools/arm_ready_pose.py pick).
READY_RIGHT = (0.0, 0.70, -0.50, 1.80, -1.00, -0.10, -0.55)
MIRRORED = (-1.0, -1.0, -1.0, 1.0, 1.0, 1.0, 1.0)


def ready_pose() -> dict[str, float]:
    """The ready keyframe's joint values for both arms, by joint name."""
    pose = {}
    for side, signs in (("right", (1.0,) * 7), ("left", MIRRORED)):
        for index, (value, sign) in enumerate(zip(READY_RIGHT, signs), start=1):
            pose[f"openarm_{side}_joint{index}"] = sign * value
    return pose


def head_cap_rad() -> float:
    """J2's outward cap: the measured contact less the margin a show leaves."""
    import cue

    return math.radians(cue.SHOULDER_HEAD_CONTACT_DEG - cue.SHOULDER_MARGIN_DEG)


def spec_for_robot():
    """The OpenArm spec with joint 2 narrowed on the outward side, to the head cap."""
    from openarm_assets.assembly import build_openarm

    cap = head_cap_rad()
    base = build_openarm()
    right = base.joint("openarm_right_joint2").range
    left = base.joint("openarm_left_joint2").range

    return build_openarm(joint_ranges={
        "openarm_right_joint2": (right[0], min(right[1], cap)),
        "openarm_left_joint2": (max(left[0], -cap), left[1]),
    }, ready=ready_pose())


def compiled_equal(a, b) -> list[str]:
    """What differs between two compiled models, by field: text formatting varies between MuJoCo versions, the model does not."""
    import numpy as np

    differing = []
    for field in dir(a):
        value = getattr(a, field, None)
        if field.startswith("_") or not isinstance(value, np.ndarray) or value.dtype == object:
            continue
        other = getattr(b, field)
        if value.shape != other.shape or not np.allclose(value, other, rtol=1e-5, atol=1e-6, equal_nan=True):
            differing.append(field)
    return differing


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--check", action="store_true", help="exit 1 if the committed model is not what the generator builds now")
    args = parser.parse_args()

    import mujoco
    from openarm_assets.assembly import to_xml

    xml = to_xml(spec_for_robot())

    # Compiled where it will live, so `../meshes` is this repository's meshes and a missing one fails here: the spec in memory would
    # compile against the meshes of the package it came from.
    OUT.parent.mkdir(parents=True, exist_ok=True)
    fresh = OUT.with_name("_generated_check.xml")

    try:
        fresh.write_text(xml)
        built = mujoco.MjModel.from_xml_path(str(fresh))
    finally:
        fresh.unlink(missing_ok=True)

    if args.check:
        if not OUT.exists():
            print(f"{OUT.relative_to(REPO)} is missing; run tools/arm_gafro_model.py")
            return 1
        differing = compiled_equal(mujoco.MjModel.from_xml_path(str(OUT)), built)
        if differing:
            print(f"{OUT.relative_to(REPO)} differs from what the generator builds in {differing}; regenerate it with tools/arm_gafro_model.py")
            return 1
        print(f"{OUT.relative_to(REPO)} is current")
        return 0

    OUT.write_text(xml)
    print(f"wrote {OUT.relative_to(REPO)} (J2 capped at {math.degrees(head_cap_rad()):g} deg)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
