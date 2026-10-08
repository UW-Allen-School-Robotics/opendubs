#!/usr/bin/env python3
"""Turn the vendored OpenArm URDF into the compact kinematic tree the dashboard renders.

The dashboard's arm view does its own forward kinematics in the browser and places the vendored
collision meshes. It therefore needs the link tree, joint origins/axes/limits and the per-link
mesh reference -- but NOT the URDF itself, which carries inertials, visual meshes it cannot fetch,
ros2_control blocks and transmission tags that would all be dead weight over the wire.

Why a generator rather than parsing the URDF in JavaScript:

- The URDF is ~39 KB of XML with a lot that the view does not use; this emits a few KB of exactly
  what it needs, so the tablet parses JSON once instead of XML every load.
- `--check` makes drift a CI-able failure. If the vendored URDF is refreshed and the kinematics
  moved, the arm view must not keep rendering the old geometry -- a pose panel that disagrees with
  the robot is worse than no pose panel.

Usage:
  tools/arm_chain.py            regenerate chain.json
  tools/arm_chain.py --check    exit 1 if chain.json does not match the URDF
"""
from __future__ import annotations

import argparse
import json
import pathlib
import sys
import xml.etree.ElementTree as ET

REPO = pathlib.Path(__file__).resolve().parent.parent
# The single robot description (description/README.md). The dashboard serves this folder as
# assets/arm/ -- a symlink in the repo, copied by deploy.sh on the robot.
ASSETS = REPO / "description/openarm"
URDF = ASSETS / "urdf/v1.urdf"
OUT = ASSETS / "chain.json"


def _nums(s, n=3):
    v = [float(x) for x in (s or "").split()]
    return v if len(v) == n else [0.0] * n


def build(urdf_path):
    root = ET.parse(urdf_path).getroot()

    links = {}
    for link in root.findall("link"):
        # The COLLISION geometry deliberately, not visual: the visual meshes are 143 MB and are
        # not vendored. Collision is upstream's decimated set (its body mesh is even named
        # `body_link0_symp.stl`) and is real geometry, not a primitive hull.
        col = link.find("collision")
        entry = {}
        if col is not None:
            mesh = col.find("geometry/mesh")
            if mesh is not None:
                o = col.find("origin")
                entry["mesh"] = mesh.get("filename")
                entry["mesh_xyz"] = _nums(o.get("xyz") if o is not None else None)
                entry["mesh_rpy"] = _nums(o.get("rpy") if o is not None else None)
                sc = mesh.get("scale")
                if sc:
                    entry["mesh_scale"] = _nums(sc)
        links[link.get("name")] = entry

    joints = []
    for j in root.findall("joint"):
        o, a, lim = j.find("origin"), j.find("axis"), j.find("limit")
        rec = {
            "name": j.get("name"),
            "type": j.get("type"),
            "parent": j.find("parent").get("link"),
            "child": j.find("child").get("link"),
            "xyz": _nums(o.get("xyz") if o is not None else None),
            "rpy": _nums(o.get("rpy") if o is not None else None),
        }
        if j.get("type") != "fixed":
            rec["axis"] = _nums(a.get("xyz") if a is not None else None) if a is not None else [0, 0, 1]
            if lim is not None:
                rec["lower"] = float(lim.get("lower"))
                rec["upper"] = float(lim.get("upper"))
        joints.append(rec)

    children = {j["child"] for j in joints}
    roots = [n for n in links if n not in children]
    if len(roots) != 1:
        sys.exit(f"expected exactly one root link, found {roots}")

    out = {
        "_generated_by": "tools/arm_chain.py -- do not edit by hand",
        "_source": URDF.name,
        "root": roots[0],
        "links": links,
        "joints": joints,
    }
    # Ground truth for the browser, generated rather than hand-written. armview-test.html checks
    # its own WebGL forward kinematics against these, so the two implementations are compared
    # against ONE source: if the URDF changes, the expected numbers change with it instead of
    # quietly becoming wrong. The poses are chosen to be diagnostic, not arbitrary -- see the
    # comment on each.
    out["_fk_checks"] = fk_checks(out)
    return out


def _fk(tree, angles, target):
    """Minimal FK over the emitted tree -- deliberately independent of the renderer."""
    def mat_mul(a, b):
        return [[sum(a[i][k] * b[k][j] for k in range(4)) for j in range(4)] for i in range(4)]

    def rpy_mat(r, p, y):
        import math
        cr, sr, cp, sp, cy, sy = (math.cos(r), math.sin(r), math.cos(p),
                                  math.sin(p), math.cos(y), math.sin(y))
        return [[cy * cp, cy * sp * sr - sy * cr, cy * sp * cr + sy * sr, 0],
                [sy * cp, sy * sp * sr + cy * cr, sy * sp * cr - cy * sr, 0],
                [-sp, cp * sr, cp * cr, 0], [0, 0, 0, 1]]

    def axis_mat(ax, q):
        import math
        x, y, z = ax
        n = math.sqrt(x * x + y * y + z * z) or 1.0
        x, y, z = x / n, y / n, z / n
        c, s, tt = math.cos(q), math.sin(q), 1 - math.cos(q)
        return [[tt * x * x + c, tt * x * y - s * z, tt * x * z + s * y, 0],
                [tt * x * y + s * z, tt * y * y + c, tt * y * z - s * x, 0],
                [tt * x * z - s * y, tt * y * z + s * x, tt * z * z + c, 0], [0, 0, 0, 1]]

    by_parent = {}
    for j in tree["joints"]:
        by_parent.setdefault(j["parent"], []).append(j)
    stack = [(tree["root"], [[1, 0, 0, 0], [0, 1, 0, 0], [0, 0, 1, 0], [0, 0, 0, 1]])]
    while stack:
        link, T = stack.pop()
        if link == target:
            return [round(T[0][3], 6), round(T[1][3], 6), round(T[2][3], 6)]
        for j in by_parent.get(link, []):
            tr = [[1, 0, 0, j["xyz"][0]], [0, 1, 0, j["xyz"][1]],
                  [0, 0, 1, j["xyz"][2]], [0, 0, 0, 1]]
            M = mat_mul(mat_mul(T, tr), rpy_mat(*j["rpy"]))
            if j["type"] != "fixed":
                M = mat_mul(M, axis_mat(j["axis"], angles.get(j["name"], 0.0)))
            stack.append((j["child"], M))
    return None


def fk_checks(tree):
    import math
    d = math.radians
    target = "openarm_left_hand_tcp"
    cases = [
        # The arm hangs straight down at zero, so this pins the whole chain plus the fixed mount.
        ("all zero", {}),
        # A gravity-loaded PITCH joint: the end point must actually move.
        ("left elbow j4 = +25deg", {"openarm_left_joint4": d(25)}),
        # A ROLL about the limb axis: the end point must NOT move. This is the case a stick-figure
        # view is blind to, and the one the robot was really sitting in (right arm at j3 -36.6).
        ("left j3 roll = +37deg", {"openarm_left_joint3": d(37)}),
    ]
    return [{"label": lbl, "angles": a, "link": target, "expect": _fk(tree, a, target)}
            for lbl, a in cases]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true", help="verify chain.json matches the URDF")
    args = ap.parse_args()

    if not URDF.exists():
        sys.exit(f"missing {URDF} -- see its directory's README for how it is vendored")

    data = build(URDF)
    text = json.dumps(data, indent=1, sort_keys=True) + "\n"

    movable = [j for j in data["joints"] if j["type"] != "fixed"]
    summary = (f"{len(data['links'])} links, {len(data['joints'])} joints "
               f"({len(movable)} movable), "
               f"{sum(1 for v in data['links'].values() if 'mesh' in v)} with meshes, "
               f"{len(text)} bytes")

    if args.check:
        if not OUT.exists():
            sys.exit(f"{OUT.name} is missing -- run tools/arm_chain.py")
        if OUT.read_text() != text:
            sys.exit(f"{OUT.name} is STALE -- the URDF changed. Re-run tools/arm_chain.py "
                     f"and check the arm view still looks right.")
        print(f"OK -- {OUT.name} matches {URDF.name} ({summary})")
        return

    OUT.write_text(text)
    print(f"wrote {OUT.relative_to(REPO)} -- {summary}")


if __name__ == "__main__":
    main()
