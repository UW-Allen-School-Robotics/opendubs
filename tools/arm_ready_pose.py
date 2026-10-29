#!/usr/bin/env python3
"""The arms' ready pose: how it was picked, and a check that it still holds.

Stow (all joints at zero) is exactly singular: the elbow is straight, so a controller cannot start there, and the controllers' guard swings
the arm 0.75 rad off it when engaged. The ready pose is where an arm is engaged and homed to. It is in `tools/arm_gafro_model.py`
(READY_RIGHT) and ends up as the model's `ready` keyframe.

    python3 tools/arm_ready_pose.py verify     the pose's singularity, joint margins, hand position and clearance
    python3 tools/arm_ready_pose.py pick       sample candidates and print the best (needs self-motion-charts and ssik)

`pick` takes candidate hand poses in front of the chest (the tool pointing forward, rolled four ways), asks ssik for each pose's self-motion
manifold (every posture that holds the hand there), and scores each posture: the smallest singular value of the arm's Jacobian and the
smallest joint margin, then prefers those near a neutral posture. The best are checked against the head and each other in this robot's
scene (sim/clearance.py), counting only pairs whose separation depends on the posture (the two fixed shoulder mounts are always 62 mm apart).

Needs gafro (the public one), openarm_assets and mujoco; `pick` also self_motion_charts and ssik.
"""
from __future__ import annotations

import argparse
import itertools
import math
import pathlib
import sys

import numpy as np

REPO = pathlib.Path(__file__).resolve().parent.parent
MODEL = REPO / "description" / "openarm" / "gafro" / "openarm_bimanual.xml"
sys.path[:0] = [str(REPO / "tools"), str(REPO / "sim")]

import arm_gafro_model as generator  # noqa: E402

GUARD_FLOOR = 0.06       # controllers.yaml: the singular_values guard
GUARD_MARGIN = 0.35      # controllers.yaml: the joint_limits guard


def right_arm():
    import gafro as g
    system = g.SystemSerialization.load(str(MODEL))
    return system, system.get_task_space("right")


def metrics(system, task_space, q):
    sv = np.linalg.svd(np.asarray(task_space.compute_ee_frame_jacobian(np.asarray(q, float))), compute_uv=False)
    lo = np.array([system.get_joint(f"openarm_right_joint{i}").get_limits().position_lower for i in range(1, 8)])
    hi = np.array([system.get_joint(f"openarm_right_joint{i}").get_limits().position_upper for i in range(1, 8)])
    return sv, np.minimum(np.asarray(q) - lo, hi - np.asarray(q))


def clearances(q) -> list[tuple[float, str]]:
    """Posture-dependent clearance (mm) at three lift heights, both arms in the same outward pose."""
    import mujoco
    import clearance
    import scene

    model = scene.build(None, head=scene.fitted_head())
    data = mujoco.MjData(model)
    degrees = np.degrees(q)
    pose = ",".join(f"{side}:{j + 1}={degrees[j]:.3f}" for side in ("right", "left") for j in range(7))
    out = []
    for lift in (0.0, 137.0, 275.0):
        clearance.set_pose(model, data, pose, lift, 0.0, 0.0)
        pairs = [p for p in clearance.clearances(model, data) if "link0_collision" not in p[1] and "link0_collision" not in p[2]]
        out.append((pairs[0][0], f"lift {lift:g}: {pairs[0][1]} ~ {pairs[0][2]}".replace(scene.ARM_PREFIX, "")))
    return out


def verify() -> int:
    system, task_space = right_arm()
    q = np.array(generator.READY_RIGHT)
    sv, margin = metrics(system, task_space, q)
    problems = []
    if sv.min() < 2 * GUARD_FLOOR:
        problems.append(f"smallest singular value {sv.min():.3f} is under twice the guard's floor ({GUARD_FLOOR})")
    if margin.min() < GUARD_MARGIN:
        problems.append(f"smallest joint margin {margin.min():.2f} rad is under the guard's {GUARD_MARGIN}")
    print(f"singular values {sv.round(3)} (smallest {sv.min() / GUARD_FLOOR:.1f}x the guard floor)")
    print(f"joint margins {margin.round(2)} rad (smallest {margin.min():.2f}; the guard's is {GUARD_MARGIN})")
    for worst, where in clearances(q):
        print(f"clearance {worst:.0f} mm ({where})")
        if worst < 40.0:
            problems.append(f"{worst:.0f} mm of clearance, {where}")
    for problem in problems:
        print(f"PROBLEM: {problem}")
    print("OK" if not problems else f"{len(problems)} problem(s)")
    return 1 if problems else 0


def pick() -> int:
    from self_motion_charts import bridge

    arm = bridge.Arm("OpenArm right", str(MODEL), "right", "openarm_right_hand_tcp", "openarm_right_joint7", 7)

    class OpenArm(bridge.Model):
        def joint_names(self):  # the model's joint list has fixed joints in it
            return [f"openarm_right_joint{i}" for i in range(1, 8)]

    model = OpenArm(arm)
    system, task_space = right_arm()
    lo, hi = model.limits[:, 0], model.limits[:, 1]
    neutral, scale = np.array([0.0, 0.3, 0.0, 1.4, 0.0, 0.0, 0.0]), np.array([0.5, 0.5, 0.5, 0.5, 0.5, 0.4, 0.5])
    found = []
    for x, y, z, roll in itertools.product((0.30, 0.36, 0.42), (-0.12, -0.18, -0.24), (0.34, 0.42, 0.50), (0.0, math.pi / 4, math.pi / 2, -math.pi / 4)):
        # the tool's z axis points forward, rolled about it
        x0 = np.array([0.0, 0.0, -1.0])
        z_axis = np.array([1.0, 0.0, 0.0])
        rotation = np.stack([x0, np.cross(z_axis, x0), z_axis], axis=1) @ np.array([[math.cos(roll), -math.sin(roll), 0], [math.sin(roll), math.cos(roll), 0], [0, 0, 1]])
        target = np.eye(4)
        target[:3, :3], target[:3, 3] = rotation, [x, y, z]
        try:
            manifold = model.self_motion(target)
        except Exception:  # noqa: BLE001 -- a pose with no solution is skipped
            continue
        for chart in manifold.charts:
            for start, end in chart.domain:
                for q in np.asarray(chart.q(np.linspace(start, end, 120))):
                    if not np.all(np.isfinite(q)) or np.any(q < lo) or np.any(q > hi):
                        continue
                    sv, margin = metrics(system, task_space, q)
                    if sv.min() >= 2 * GUARD_FLOOR - 0.02 and margin.min() >= 0.45:
                        found.append((float(np.linalg.norm((q - neutral) / scale)), sv.min(), margin.min(), (x, y, z), q))
    found.sort(key=lambda f: f[0])
    print(f"{len(found)} postures with a smallest singular value near or above twice the guard floor and a joint margin of 0.45 rad or more")
    # The 40 nearest a neutral posture, best clearance first (then the nearest neutral): clearance is what tells them apart.
    ranked = sorted(((min(c[0] for c in clearances(f[4])), f) for f in found[:40]), key=lambda r: (-round(r[0]), r[1][0]))
    for worst, (distance, sv, margin, hand, q) in ranked[:10]:
        print(f"  clearance {worst:.0f} mm  sv {sv:.3f}  margin {margin:.2f}  from neutral {distance:.2f}  hand {tuple(round(v, 2) for v in hand)}  q {np.round(q, 2).tolist()}")
    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("command", choices=("verify", "pick"))
    sys.exit({"verify": verify, "pick": pick}[parser.parse_args().command]())
