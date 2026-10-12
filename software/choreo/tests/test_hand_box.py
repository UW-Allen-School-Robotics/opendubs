#!/usr/bin/env python3
"""The hands at the arm controllers' own point (hand_tcp), against their workspace box (tcp.py) —
pinned to what the controller itself computed when it rejected a show at the robot (2026-10-12)."""
from __future__ import annotations

import os
import sys

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, ".."))

import cue  # noqa: E402
import tcp  # noqa: E402

LIMITS_PATH = os.path.join(HERE, "joint_limits_fixture.yaml")
LIMITS = cue.load_limits(LIMITS_PATH)
FAILS = []


def check(what, ok, detail=""):
    print(("  ok    " if ok else "  FAIL  ") + what)
    if not ok:
        FAILS.append(f"{what}: {detail}")


chain = tcp.load_chain()
print("hand_tcp: the forward kinematics")
saved = tcp.TCP_EXT_M
tcp.TCP_EXT_M = 0.0                      # the chain's own checks are at the URDF's hand_tcp (link7)
for c in chain["_fk_checks"]:
    q = np.zeros((1, 7))
    for k, v in c["angles"].items():
        q[0, int(k[-1]) - 1] = v
    got = tcp.tcp_points(chain, "left", q)[0]
    check(f"chain.json's own check '{c['label']}'", np.allclose(got, c["expect"], atol=2e-4), (got, c["expect"]))
tcp.TCP_EXT_M = saved
# The two waypoints the controllers REJECTED, with the z they logged (signed degrees as sent)
for side, deg, logged in (("left", [-133.6472, -25.7393, 0.0, 32.7666, 0.0, 0.0, 0.0], 1.2024),
                          ("right", [147.8343, 15.2991, -63.8847, 23.7825, 14.5333, -7.9591, -36.146], 1.2037)):
    z = tcp.tcp_points(chain, side, np.radians(np.array([deg])))[0][2]
    check(f"the {side} waypoint the controller rejected: z {z:.4f} m, as it logged ({logged})", abs(z - logged) < 2e-4, z)

print("\nhand_tcp: the validator")
for f in sorted(os.listdir(os.path.join(HERE, "..", "shows"))):
    if f.endswith(".yaml"):
        a = cue.load_show(os.path.join(HERE, "..", "shows", f)).analyse(LIMITS)
        check(f"{f}: inside the box", not any("workspace box" in q.msg for q in a["problems"]),
              [q.msg for q in a["problems"] if "workspace box" in q.msg])
UP = ("id: up\nslots: [ARM_L, ARM_R]\nrequires: { arms: active, rc_authority: AUTO }\ncues:\n"
      "  - { t: 3.0, slot: ARM_L, joints: { 1: 178, 2: 5, 4: 9 } }\n"
      "  - { t: 3.0, slot: ARM_R, joints: { 1: 178, 2: 5, 4: 9 } }\n"
      "  - { t: 6.0, slot: ARM_L, pose: rest }\n  - { t: 6.0, slot: ARM_R, pose: rest }\n")
a = cue.show_from_text(UP, "up").analyse(LIMITS)
msgs = [q.msg for q in a["problems"] if "workspace box" in q.msg]
check("arms straight up (hand_tcp 1.31 m, over the 1.30 m box) is refused, each arm, with the height and when",
      len(msgs) == 2 and all("1.31" in m for m in msgs) and "REJECT" in msgs[0], msgs)
paths = tcp.CHAIN_PATHS
tcp.CHAIN_PATHS = ("/nonexistent/chain.json",)
a = cue.show_from_text(UP, "up").analyse(LIMITS)
tcp.CHAIN_PATHS = paths
check("no arm chain on this machine: a notice, not a pass and not a refusal",
      not any("workspace box" in q.msg for q in a["problems"]) and any("not checked here" in n for n in a["notices"]),
      a["notices"])

print()
if FAILS:
    for x in FAILS:
        print("FAILED:", x)
    sys.exit(1)
print("all good")
