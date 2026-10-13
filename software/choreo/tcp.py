"""Where each hand goes, at the point the ARM CONTROLLERS judge — and whether it stays in their box.

The impedance controllers (Arms lane) refuse a whole FollowJointTrajectory goal if ANY waypoint puts
the hand outside a workspace box (found at the robot, 2026-10-12: both arms' goals rejected, the live
show running on with still arms). The point is the controller's own TCP, `openarm_<side>_hand_tcp`:
link7 + 186 mm along link7's z (hand 102.5 + hand_tcp 83.5), in the frame of the robot description's
root. This is NOT ik.py's `ee_site` (180 mm, MuJoCo) — the controller's point is the one that counts.

Forward kinematics over description/openarm/chain.json (tools/arm_chain.py, from the URDF the stack
launches), vectorised over every waypoint the runner will send: arm_output.track_points at its own
spacing, so the check sees exactly what the controller will see. It reproduced the controller's
logged z for the two waypoints it rejected to 0.1 mm.

HAND_BOX is the controllers' box (Arms lane: per arm, the same for both). The validator keeps every
waypoint MARGIN_M inside it. Change the numbers here when Arms changes the controllers' parameters.
"""
from __future__ import annotations

import json
import math
import os

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
HAND_BOX = {"min": (-0.8, -0.8, 0.0), "max": (0.8, 0.8, 1.3)}   # metres, the controllers' (Arms #214, live 2026-10-12; was z 1.2)
MARGIN_M = 0.02
TCP_EXT_M = 0.186                 # hand_tcp: link7 + 186 mm along link7's z
CHAIN_PATHS = (os.environ.get("OPENDUBS_ARM_CHAIN") or "",
               os.path.join(HERE, "..", "..", "description", "openarm", "chain.json"),     # the repository
               "/opt/opendubs/kiosk-dashboard/frontend/assets/arm/chain.json")            # the robot (deploy.sh)


def load_chain():
    for p in CHAIN_PATHS:
        if p and os.path.isfile(p):
            with open(p, encoding="utf-8") as fh:
                return json.load(fh)
    return None


def _rpy(r, p, y):
    cr, sr, cp, sp, cy, sy = math.cos(r), math.sin(r), math.cos(p), math.sin(p), math.cos(y), math.sin(y)
    return np.array([[cy * cp, cy * sp * sr - sy * cr, cy * sp * cr + sy * sr],
                     [sy * cp, sy * sp * sr + cy * cr, sy * sp * cr - cy * sr],
                     [-sp, cp * sr, cp * cr]])


def _axis_rot(axis, q):
    """Rotations about one fixed axis by each angle in q: (N, 3, 3)."""
    a = np.asarray(axis, float)
    a = a / np.linalg.norm(a)
    x, y, z = a
    c, s = np.cos(q), np.sin(q)
    C = 1 - c
    return np.stack([np.stack([c + x * x * C, x * y * C - z * s, x * z * C + y * s], -1),
                     np.stack([y * x * C + z * s, c + y * y * C, y * z * C - x * s], -1),
                     np.stack([z * x * C - y * s, z * y * C + x * s, c + z * z * C], -1)], -2)


def tcp_points(chain: dict, side: str, q_rad: np.ndarray) -> np.ndarray:
    """hand_tcp positions (N, 3) for joint angles q_rad (N, 7), signed radians as sent to the controller."""
    by_child = {j["child"]: j for j in chain["joints"]}
    joints, link = [], f"openarm_{side}_link7"
    while link in by_child:
        joints.append(by_child[link])
        link = by_child[link]["parent"]
    n = len(q_rad)
    R = np.broadcast_to(np.eye(3), (n, 3, 3)).copy()
    t = np.zeros((n, 3))
    for j in reversed(joints):
        t = t + R @ np.asarray(j["xyz"], float)
        R = R @ _rpy(*j["rpy"])
        if j["type"] == "revolute":
            R = R @ _axis_rot(j["axis"], q_rad[:, int(j["name"][-1]) - 1])
    return t + R @ np.array([0.0, 0.0, TCP_EXT_M])


def link_origin(chain: dict, link: str) -> np.ndarray:
    """A link's origin with every joint at zero, in the chain's root frame (the box's frame)."""
    by_child = {j["child"]: j for j in chain["joints"]}
    joints = []
    while link in by_child:
        joints.append(by_child[link])
        link = by_child[link]["parent"]
    R, t = np.eye(3), np.zeros(3)
    for j in reversed(joints):
        t = t + R @ np.asarray(j["xyz"], float)
        R = R @ _rpy(*j["rpy"])
    return t


def hand_z_ok(chain: dict, side: str, deg_signed) -> tuple:
    """(ok, z): whether one pose's hand_tcp is inside the box's height, less the margin."""
    z = float(tcp_points(chain, side, np.radians(np.array([deg_signed], float)))[0][2])
    return HAND_BOX["min"][2] + MARGIN_M <= z <= HAND_BOX["max"][2] - MARGIN_M, z


def box_problems(chain: dict, tracks: dict, at_label=lambda t: f"t={t:.2f}s") -> list:
    """One sentence per arm whose hand leaves HAND_BOX (less MARGIN_M) anywhere along what the
    runner will send. `tracks`: {slot: [(t, [7 signed degrees])]} (arm_output.track_points)."""
    out = []
    lo = np.array(HAND_BOX["min"]) + MARGIN_M
    hi = np.array(HAND_BOX["max"]) - MARGIN_M
    for slot, side in (("ARM_L", "left"), ("ARM_R", "right")):
        pts = tracks.get(slot) or []
        if not pts:
            continue
        ts = np.array([p[0] for p in pts])
        q = np.radians(np.array([p[1] for p in pts], float))
        xyz = tcp_points(chain, side, q)
        over = np.maximum(xyz - hi, 0.0) + np.maximum(lo - xyz, 0.0)        # metres outside, per axis
        worst = over.max(axis=1)
        bad = worst > 1e-9
        if not bad.any():
            continue
        spans, i = [], 0                       # each stretch outside, with its own worst point
        while i < len(bad):
            if bad[i]:
                j = i
                while j + 1 < len(bad) and bad[j + 1]:
                    j += 1
                k = i + int(np.argmax(worst[i:j + 1]))
                ax = int(np.argmax(over[k]))
                spans.append(f"{'xyz'[ax]} {xyz[k][ax]:.3f} m at {at_label(ts[k])} "
                             f"({at_label(ts[i])}–{at_label(ts[j])})")
                i = j + 1
            else:
                i += 1
        out.append(f"the {side} hand leaves the arm controllers' workspace box (x/y "
                   f"{HAND_BOX['min'][0]:g}..{HAND_BOX['max'][0]:g}, z {HAND_BOX['min'][2]:g}..{HAND_BOX['max'][2]:g} m at "
                   f"hand_tcp, kept {MARGIN_M * 1000:.0f} mm inside): " + "; ".join(spans[:4])
                   + (f"; and {len(spans) - 4} more" if len(spans) > 4 else "")
                   + " — the controller would REJECT the whole routine; lower or narrow the move there")
    return out
