#!/usr/bin/env python3
"""How close does the robot come to hitting itself?

    python3 clearance.py --joints "right:2=182,right:4=100"     one pose
    python3 clearance.py --contact right:2 --hold right:4=100   find where it touches
    python3 clearance.py --lift 275 --joints "left:2=170"       ... with the lift raised

`software/choreo/cue.py` refuses a shoulder above 155 degrees: contact with the Galaxy head comes at
about 180 with the elbow folded and the neck centred, SIMULATED, with a hand re-measurement pending
(the old head's 182 was measured BY HAND, ONCE, ON ONE AXIS). This answers the same question from
geometry: the minimum distance between any two parts, for any pose.

A THIRD OPINION, NOT THE GUARD. Two things already protect the robot — the arm model's collision
geometry is a single body_link0.stl that still contains the suppressed chest cover's volume, so
torso clearance is conservative by construction, and the impedance controller constrains the
TCP. The poses that can reach the face are unlikely in normal operation. What this adds is a
number where there was a constant, and a way to check a pose before anyone stands next to it.

NOT A QUESTION HERE: the arms cannot reach the lift. The arm frame rides on the platform, so its
reach envelope moves with it and stays above it. Easy to get wrong by picturing the envelope as
fixed to the chassis, and then hunting a collision geometry rules out.

WHAT IT IS ALLOWED TO CLAIM. Distances, and only distances. Geometry in this model traces to
CAD; its masses and dynamics do not (see the banner in opendubs.xml), so nothing here says
anything about what happens after a collision, only whether one occurs.

AND IT IS NOT THE AUTHORITY. Checked 2026-10-23 against the one pose measured on the robot (the old
Tab5 head): the model puts contact at 181.6 (right) and 182.5 (left), elbow at 100, against 182 felt
by hand, so it agrees within about half a degree. The new head's 180 is simulated, and the hand
re-measurement replaces it. A measured figure outranks the model: where one exists it stays in
charge, and this tool supplies the shape of the curve between the points nobody measured.
"""
from __future__ import annotations

import argparse

import numpy as np

import scene

# Everything that is not a robot part: the ground, and the wheel rollers, which are collision
# geometry for driving and would otherwise dominate every answer with "the floor is 0 mm away".
IGNORE_PREFIX = ("ground",)


def _geom_sets(model):
    """Split the collision geometry into the groups a self-collision question is about."""
    import mujoco

    right, left, body = [], [], []
    for g in range(model.ngeom):
        name = mujoco.mj_id2name(model, mujoco.mjtObj.mjOBJ_GEOM, g) or ""
        if name.startswith(IGNORE_PREFIX) or model.geom_group[g] != 3:
            continue
        if model.geom_contype[g] == 2:          # a wheel roller; not part of this question
            continue
        # BY PREFIX, not by substring. The left arm owns a geom called
        # "oa_openarm_left_right_finger_collision", and a substring test files it under the
        # right arm, which then gets measured against its own hand: every pose reports contact.
        if name.startswith(f"{scene.ARM_PREFIX}openarm_right_"):
            right.append(g)
        elif name.startswith(f"{scene.ARM_PREFIX}openarm_left_"):
            left.append(g)
        else:
            body.append(g)                      # head, chassis, lift, the shared arm frame
    return right, left, body


def pairs_of_interest(model):
    """The pairs worth measuring: each arm against the structure, and the two arms against
    each other. NOT a link against its own neighbour — those touch by construction."""
    right, left, body = _geom_sets(model)
    out = [(a, b) for a in right for b in body]
    out += [(a, b) for a in left for b in body]
    out += [(a, b) for a in right for b in left]
    return [(a, b) for a, b in out if not _adjacent(model, a, b)]


def _adjacent(model, g1, g2) -> bool:
    """Parent and child bodies are bolted together; their geoms touch by construction. MuJoCo
    excludes those from contact for the same reason, and a shoulder mount resting on the frame
    it is bolted to is not a clearance finding."""
    b1, b2 = model.geom_bodyid[g1], model.geom_bodyid[g2]
    return (model.body_parentid[b1] == b2 or model.body_parentid[b2] == b1 or b1 == b2)


def clearances(model, data, limit_mm=1000.0):
    """Every pair's separation in mm, closest first. Negative means interpenetration."""
    import mujoco

    fromto = np.zeros(6)
    out = []
    for a, b in pairs_of_interest(model):
        dist = mujoco.mj_geomDistance(model, data, a, b, limit_mm / 1000.0, fromto)
        out.append((dist * 1000.0,
                    mujoco.mj_id2name(model, mujoco.mjtObj.mjOBJ_GEOM, a),
                    mujoco.mj_id2name(model, mujoco.mjtObj.mjOBJ_GEOM, b)))
    out.sort(key=lambda r: r[0])
    return out


def min_clearance(model, data) -> float:
    """The single number a validator wants: mm to the nearest self-contact."""
    c = clearances(model, data)
    return c[0][0] if c else float("inf")


def set_pose(model, data, joints: str = "", lift_mm: float = 0.0,
             head_pan: float = 0.0, head_tilt: float = 0.0):
    """Place the robot KINEMATICALLY. mj_forward, not mj_step: we are asking where things are,
    which the geometry answers, and not what they weigh, which it does not."""
    import mujoco

    data.qpos[:] = model.qpos0
    for name, val in (("zaxis", lift_mm / 1000.0),
                      ("head_pan", np.radians(head_pan)),
                      ("head_tilt", np.radians(head_tilt))):
        j = mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_JOINT, name)
        data.qpos[model.jnt_qposadr[j]] = val
    if joints:
        scene.set_joints(model, data, joints)
    mujoco.mj_forward(model, data)


def contact_angle(model, data, axis: str, hold: str, lift_mm: float,
                  lo: float = 0.0, hi: float = 220.0) -> float | None:
    """Bisect for the angle on `axis` at which clearance first reaches zero. None if it never
    does within [lo, hi] — which is the answer for most axes, and a useful one."""
    def clear_at(v):
        spec = f"{axis}={v}" + (f",{hold}" if hold else "")
        set_pose(model, data, spec, lift_mm)
        return min_clearance(model, data)

    if clear_at(lo) <= 0:
        return lo
    if clear_at(hi) > 0:
        return None
    for _ in range(50):
        mid = (lo + hi) / 2
        if clear_at(mid) > 0:
            lo = mid
        else:
            hi = mid
    return lo


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--assets")
    ap.add_argument("--joints", default="", help='e.g. "right:2=182,right:4=100"')
    ap.add_argument("--lift", type=float, default=0.0, help="Z-axis height in mm")
    ap.add_argument("--pan", type=float, default=0.0, help="head pan, degrees")
    ap.add_argument("--tilt", type=float, default=0.0, help="head tilt, degrees (+ is down)")
    ap.add_argument("--top", type=int, default=6, help="how many close pairs to list")
    ap.add_argument("--contact", metavar="SIDE:JOINT",
                    help='sweep this joint to find where clearance hits zero, e.g. "right:2"')
    ap.add_argument("--hold", default="", help='joints to hold during --contact')
    a = ap.parse_args()

    import mujoco

    model = scene.build(a.assets)
    data = mujoco.MjData(model)

    if a.contact:
        ang = contact_angle(model, data, a.contact, a.hold, a.lift)
        if ang is None:
            print(f"{a.contact}: no contact anywhere in 0..220 deg"
                  + (f" (holding {a.hold})" if a.hold else ""))
        else:
            print(f"{a.contact}: contact at {ang:.1f} deg"
                  + (f", holding {a.hold}" if a.hold else ""))
            set_pose(model, data, f"{a.contact}={ang:.2f}" + (f",{a.hold}" if a.hold else ""),
                     a.lift)
            _, g1, g2 = clearances(model, data)[0]
            print(f"  between {g1} and {g2}")
        return

    set_pose(model, data, a.joints, a.lift, a.pan, a.tilt)
    rows = clearances(model, data)
    # A pair whose distance does not move with the pose is structure, not choreography: the arm
    # frame sits a fixed distance from the head no matter what the arms do, and that constant
    # floor otherwise hides the number the author actually cares about.
    base = mujoco.MjData(model)
    set_pose(model, base, "", a.lift, a.pan, a.tilt)
    static = {(g1, g2): c for c, g1, g2 in clearances(model, base)}

    print(f"lift {a.lift:.0f} mm, head pan {a.pan:.0f} tilt {a.tilt:.0f}"
          + (f", joints {a.joints}" if a.joints else ", arms at zero"))
    print(f"\n{'mm':>9s}  {'':4s} pair")
    for c, g1, g2 in rows[: a.top]:
        moved = abs(static.get((g1, g2), c) - c) > 0.05
        print(f"{c:9.1f}  {'MOVES' if moved else 'fixed':5s} {g1}  <->  {g2}")
    worst = rows[0][0]
    print(f"\nminimum clearance {worst:.1f} mm"
          + ("   *** CONTACT ***" if worst <= 0 else ""))


if __name__ == "__main__":
    main()
