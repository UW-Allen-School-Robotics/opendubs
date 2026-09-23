#!/usr/bin/env python3
"""Build the whole robot: our chassis and Z-axis, with the OpenArm bimanual model on top.

    python3 scene.py --render /tmp/robot.png
    python3 scene.py --render /tmp/up.png --lift 275 --joints "right:2=150,right:4=100"
    python3 scene.py --check                      # load, report, and exit

The two halves stay in their own files. `opendubs.xml` is ours; the arms come from
openarm_assets exactly as that project publishes them, and are attached at runtime with
MuJoCo's MjSpec rather than by copying or editing anything. When they update their model we
get the update for free.

WHERE THE ASSETS COME FROM, in order: --assets, $OPENDUBS_OPENARM_ASSETS, then the places the
arm software's own setup.sh puts them. They are not vendored into this repo: they are ~50 MB,
they belong to another project, and a stale copy of someone else's robot is a trap.
"""
from __future__ import annotations

import argparse
import math
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ARM_MOUNT_XYZ = (0.423, 0.0, 0.3575)   # openarm_body_link0's origin, in CAD coordinates
ARM_PREFIX = "oa_"

# The chest cover. It is removed on our robot — the face mounts at the top of the extrusion it
# used to cover — so the model should not show one. NOTE: the arm model's COLLISION geometry is
# a single body_link0.stl that still includes the cover's volume, so clearance checks are
# conservative there until we supply our own frame collision mesh.
CHEST_COVER_GEOM = "openarm_body_link0_visual_5"

ASSET_CANDIDATES = (
    "~/openarm/openarm_robot-code/openarm_assets/src/openarm_assets/models/openarm",
    "/openarm_ws/src/openarm_assets/src/openarm_assets/models/openarm",
    "/tmp/oa_assets/src/openarm_assets/models/openarm",
)


def find_assets(explicit: str | None) -> str:
    for c in ([explicit] if explicit else []) + [os.environ.get("OPENDUBS_OPENARM_ASSETS")] + list(ASSET_CANDIDATES):
        if not c:
            continue
        p = os.path.expanduser(c)
        if os.path.isfile(os.path.join(p, "generated", "openarm_bimanual_decorated.xml")):
            return p
    sys.exit(
        "cannot find the OpenArm assets. Pass --assets <.../models/openarm>, set\n"
        "OPENDUBS_OPENARM_ASSETS, or clone them:\n"
        "  git clone --depth 1 https://github.com/ericjgoossen/openarm_assets /tmp/oa_assets"
    )


def _mesh_dir(assets: str) -> str:
    """One directory holding both mesh sets, by symlink — MuJoCo resolves meshes against a
    single meshdir, and copying 50 MB to render a picture would be silly."""
    d = os.path.join(tempfile.gettempdir(), "opendubs_sim_meshes")
    os.makedirs(d, exist_ok=True)
    for src_dir in (os.path.join(assets, "meshes"), os.path.join(HERE, "meshes")):
        for root, _, files in os.walk(src_dir):
            for f in files:
                if not f.lower().endswith((".stl", ".obj")):
                    continue
                rel = os.path.relpath(os.path.join(root, f), src_dir)
                dst = os.path.join(d, rel)
                os.makedirs(os.path.dirname(dst), exist_ok=True)
                if not os.path.lexists(dst):
                    os.symlink(os.path.join(root, f), dst)
    return d


def build(assets: str | None = None, chest_cover: bool = False):
    import mujoco

    a = find_assets(assets)
    meshes = _mesh_dir(a)

    ours = mujoco.MjSpec.from_file(os.path.join(HERE, "opendubs.xml"))
    arms = mujoco.MjSpec.from_file(os.path.join(a, "generated", "openarm_bimanual_decorated.xml"))
    ours.meshdir = meshes
    arms.meshdir = meshes

    frame = ours.body("lift_platform").add_frame(pos=list(ARM_MOUNT_XYZ))
    frame.attach_body(arms.body("openarm_body_link0"), ARM_PREFIX, "")

    model = ours.compile()
    if not chest_cover:
        gid = mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_GEOM, ARM_PREFIX + CHEST_COVER_GEOM)
        if gid >= 0:
            model.geom_rgba[gid][3] = 0.0     # hidden, and it was never a collision geom
        else:
            print(f"note: {CHEST_COVER_GEOM} not found — has the arm model changed?", file=sys.stderr)
    return model


def set_joints(model, data, spec: str):
    """--joints "right:2=150,right:4=100,left:4=100" — degrees, in each arm's own sense."""
    import mujoco

    for item in filter(None, (s.strip() for s in spec.split(","))):
        side_joint, _, val = item.partition("=")
        side, _, j = side_joint.partition(":")
        name = f"{ARM_PREFIX}openarm_{side}_joint{int(j)}"
        i = mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_JOINT, name)
        if i < 0:
            sys.exit(f"no such joint: {name}")
        sign = -1 if side == "left" and int(j) in (1, 2, 3) else 1
        data.qpos[model.jnt_qposadr[i]] = math.radians(float(val) * sign)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--assets")
    ap.add_argument("--render", metavar="PNG")
    ap.add_argument("--check", action="store_true")
    ap.add_argument("--lift", type=float, default=0.0, help="Z-axis height in mm (0..275.1)")
    ap.add_argument("--joints", default="", help='e.g. "right:2=150,right:4=100"')
    ap.add_argument("--chest-cover", action="store_true", help="draw the cover we removed")
    ap.add_argument("--az", type=float, default=128.0)
    ap.add_argument("--el", type=float, default=-10.0)
    ap.add_argument("--dist", type=float, default=3.0)
    a = ap.parse_args()

    import mujoco

    model = build(a.assets, a.chest_cover)
    data = mujoco.MjData(model)
    data.qpos[0] = a.lift / 1000.0
    if a.joints:
        set_joints(model, data, a.joints)
    mujoco.mj_forward(model, data)

    print(f"{model.nbody} bodies, {model.njnt} joints, {model.ngeom} geoms")
    site = mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_SITE, "arm_frame_mount")
    z = data.site_xpos[site][2]
    print(f"lift {a.lift:6.1f} mm -> arm frame base {(z + 0.094) * 1000:7.1f} mm above the floor, "
          f"shoulders {(z + 0.094 + 0.698) * 1000:7.1f} mm")
    if a.check or not a.render:
        return

    model.vis.headlight.ambient[:] = [0.55] * 3
    model.vis.headlight.diffuse[:] = [0.8] * 3
    cam = mujoco.MjvCamera()
    mujoco.mjv_defaultCamera(cam)
    cam.azimuth, cam.elevation, cam.distance = a.az, a.el, a.dist
    cam.lookat[:] = [0.35, 0.0, 0.85]
    r = mujoco.Renderer(model, 760, 900)
    r.update_scene(data, camera=cam)
    from PIL import Image
    Image.fromarray(r.render()).save(a.render)
    print("wrote", a.render)


if __name__ == "__main__":
    main()
