#!/usr/bin/env python3
"""Build the whole robot: our chassis and Z-axis, with the OpenArm bimanual model on top.

    python3 scene.py --render /tmp/robot.png
    ./view.sh                                     # interactive viewer
    python3 scene.py --render /tmp/up.png --lift 275 --joints "right:2=150,right:4=100"
    python3 scene.py --check                      # load, report, and exit

The two halves stay in their own files, both under description/ at the repo root:
`description/opendubs/opendubs.xml` is ours; the arms are `description/openarm/`, vendored from
openarm_assets by tools/vendor_openarm.py (unchanged MJCF and collision meshes, decimated visual
meshes, provenance recorded). They are attached at runtime with MuJoCo's MjSpec rather than by
editing either file.

WHERE THE ARMS COME FROM, in order: --assets, $OPENDUBS_OPENARM_ASSETS, then the vendored copy in
this repo. Pass an openarm_assets checkout to try a newer upstream model before re-vendoring it.
"""
from __future__ import annotations

import argparse
import hashlib
import math
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
OURS = os.path.join(REPO, "description", "opendubs")
VENDORED_ARMS = os.path.join(REPO, "description", "openarm")
ARM_XML = "openarm_bimanual_decorated.xml"
ARM_MOUNT_XYZ = (0.423, 0.0, 0.3575)   # openarm_body_link0's origin, in CAD coordinates
ARM_PREFIX = "oa_"

# The chest cover. It is removed on our robot — the face mounts at the top of the extrusion it
# used to cover — so the model should not show one. NOTE: the arm model's COLLISION geometry is
# a single body_link0.stl that still includes the cover's volume, so clearance checks are
# conservative there until we supply our own frame collision mesh.
CHEST_COVER_GEOM = "openarm_body_link0_visual_5"

def _arm_xml(root: str) -> str | None:
    """The bimanual MJCF inside a vendored copy (mjcf/) or an openarm_assets checkout."""
    for sub in ("mjcf", "generated", os.path.join("src", "openarm_assets", "models", "openarm", "generated")):
        p = os.path.join(root, sub, ARM_XML)
        if os.path.isfile(p):
            return p
    return None


def find_assets(explicit: str | None) -> str:
    """The directory holding the arm MJCF's folder and its meshes/ (the model root)."""
    for c in ([explicit] if explicit else []) + [os.environ.get("OPENDUBS_OPENARM_ASSETS"), VENDORED_ARMS]:
        if not c:
            continue
        xml = _arm_xml(os.path.expanduser(c))
        if xml:
            return os.path.dirname(os.path.dirname(xml))
    sys.exit("cannot find the OpenArm model: description/openarm/ is missing, and no --assets or "
             "$OPENDUBS_OPENARM_ASSETS was given")


def _mesh_dir(assets: str) -> str:
    """One directory holding both mesh sets, by symlink — MuJoCo resolves meshes against a
    single meshdir, and copying meshes to render a picture would be silly. Keyed by the arm source,
    so pointing --assets at a different checkout never picks up links left by another one."""
    key = hashlib.sha1(os.path.realpath(assets).encode()).hexdigest()[:10]
    d = os.path.join(tempfile.gettempdir(), "opendubs_sim_meshes_" + key)
    os.makedirs(d, exist_ok=True)
    for src_dir in (os.path.join(assets, "meshes"), os.path.join(OURS, "meshes")):
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


# The head meshes are exported in CAD coordinates but posed at pan=180 and 1.85 mm off the
# centreline; opendubs.xml corrects both in the single transform that places them. Confirmed
# against the robot: the face points forward, the way the front wheels do.
HEAD_TILT_AXIS = (0.423, 0.0, 1.245575)

# Drive is commanded the way the RC sticks command it, not a wheel at a time: the model carries
# three tendons whose coefficients are driveMix() from firmware/rc_serial_probe, so -1..1 here
# means what -1..1 means on the robot. fwd + is forward, rot + turns RIGHT, str + strafes RIGHT.
DRIVE_ACTUATORS = ("drive_fwd", "drive_rot", "drive_strafe")
WHEEL_MAX_RADS = 10.0          # 0.70 m/s at the measured 70 mm radius


# THE FACE ON THE HEAD (choreography plan 2.5). A flat surface on the tablet's glass that shows the
# face twin's own frames: the display area (189.0 x 113.6 mm), centred on the robot description's
# head_screen site (the centre of the tablet's front face, in head_tilt's frame) and 0.5 mm proud of
# it. A preview only, never used for clearance (visual group, no contacts, no mass).
FACE_TEXTURE = "face_screen"
FACE_TEX_W, FACE_TEX_H = 320, 180                 # the face twin's shared frame (frame_tap.h)
FACE_W, FACE_H = 0.1890, 0.1136


def _head_screen() -> tuple:
    """The head_screen site's position in head_tilt's frame, from the robot description. Read as text:
    the description's comments hold "--", which MuJoCo accepts and a strict XML parser does not."""
    import re
    with open(os.path.join(OURS, "opendubs.xml"), encoding="utf-8") as fh:
        m = re.search(r'<site\s+name="head_screen"\s+pos="([^"]+)"', fh.read())
    if not m:
        raise RuntimeError("the robot description has no head_screen site (description/opendubs/opendubs.xml)")
    return tuple(float(v) for v in m.group(1).split())


FACE_X, FACE_YC, FACE_ZC = _head_screen()
FACE_X += 0.0005                                  # just proud of the glass


def _add_face_screen(spec, w_m=FACE_W, h_m=FACE_H, x=FACE_X, yc=FACE_YC, zc=FACE_ZC):
    import mujoco
    spec.add_texture(name=FACE_TEXTURE, type=mujoco.mjtTexture.mjTEXTURE_2D,
                     builtin=mujoco.mjtBuiltin.mjBUILTIN_FLAT, width=FACE_TEX_W, height=FACE_TEX_H,
                     rgb1=[0.03, 0.03, 0.04], rgb2=[0.03, 0.03, 0.04], nchannel=3)
    mat = spec.add_material(name=FACE_TEXTURE, emission=1.0, specular=0.0, shininess=0.0,
                            rgba=[1, 1, 1, 1])
    mat.textures[mujoco.mjtTextureRole.mjTEXROLE_RGB] = FACE_TEXTURE
    w, h = w_m / 2, h_m / 2
    # Facing +x, the way the head looks. Seen from in front, +y is to the viewer's right and +z up,
    # so the corners run bottom-left, bottom-right, top-right, top-left (counter-clockwise).
    verts = [x, yc - w, zc - h,  x, yc + w, zc - h,
             x, yc + w, zc + h,  x, yc - w, zc + h]
    spec.add_mesh(name=FACE_TEXTURE, uservert=verts, userface=[0, 1, 2, 0, 2, 3],
                  usertexcoord=[0, 1, 1, 1, 1, 0, 0, 0],
                  inertia=mujoco.mjtMeshInertia.mjMESH_INERTIA_SHELL)
    spec.body("head_tilt").add_geom(type=mujoco.mjtGeom.mjGEOM_MESH, meshname=FACE_TEXTURE,
                                    material=FACE_TEXTURE, contype=0, conaffinity=0, group=2,
                                    density=0)


# ---- the projection screen (notes/design/choreo-video.md, "The twin's projection screen") ----------
# The show's projector video, on a wall BEHIND the robot, so the audience sees the robot in front of the
# image (the user's choice, 2026-10-21). PLACEHOLDERS, all here: put the real throw and image size in
# once the projector (Nebula P1i) is measured. The robot faces +x; the screen faces it from behind,
# fixed to the world (it does not ride the base).
PROJ_TEXTURE = "projection_screen"
PROJ_TEX_W, PROJ_TEX_H = 640, 360                 # the twin's copy of the picture (16:9)
PROJ_FPS = 15                                     # frames decoded per second of video
PROJ_IMAGE_W_M = 2.0                              # the projected image's width (16:9: height follows)
PROJ_BEHIND_M = 1.2                               # how far behind the robot's start the wall is
PROJ_BOTTOM_Z_M = 0.35                            # the image's lower edge above the floor
PROJ_CENTRE_Y_M = 0.0                             # left/right of the robot's centre line


def projection_corners():
    """The screen's four corners (world metres): bottom-left, bottom-right, top-right, top-left as
    the audience in front sees them."""
    w, h = PROJ_IMAGE_W_M, PROJ_IMAGE_W_M * 9 / 16
    x, y0, z0 = -PROJ_BEHIND_M, PROJ_CENTRE_Y_M, PROJ_BOTTOM_Z_M
    return [(x, y0 - w / 2, z0), (x, y0 + w / 2, z0), (x, y0 + w / 2, z0 + h), (x, y0 - w / 2, z0 + h)]


def _add_projection(spec):
    """An emissive textured plane behind the robot, facing +x (toward the robot and the audience), as
    the face screen is built. Hidden until a show with video is shown (the viewers set its alpha)."""
    import mujoco
    spec.add_texture(name=PROJ_TEXTURE, type=mujoco.mjtTexture.mjTEXTURE_2D,
                     builtin=mujoco.mjtBuiltin.mjBUILTIN_FLAT, width=PROJ_TEX_W, height=PROJ_TEX_H,
                     rgb1=[0.0, 0.0, 0.0], rgb2=[0.0, 0.0, 0.0], nchannel=3)
    mat = spec.add_material(name=PROJ_TEXTURE, emission=1.0, specular=0.0, shininess=0.0, rgba=[1, 1, 1, 1])
    mat.textures[mujoco.mjtTextureRole.mjTEXROLE_RGB] = PROJ_TEXTURE
    verts = [v for c in projection_corners() for v in c]
    # seen from in front (+x looking -x): the audience's left is -y, so the texture's u runs with +y
    spec.add_mesh(name=PROJ_TEXTURE, uservert=verts, userface=[0, 1, 2, 0, 2, 3],
                  usertexcoord=[0, 1, 1, 1, 1, 0, 0, 0], inertia=mujoco.mjtMeshInertia.mjMESH_INERTIA_SHELL)
    spec.worldbody.add_geom(name=PROJ_TEXTURE, type=mujoco.mjtGeom.mjGEOM_MESH, meshname=PROJ_TEXTURE,
                            material=PROJ_TEXTURE, contype=0, conaffinity=0, group=2, density=0,
                            rgba=[1, 1, 1, 0])


def set_projection_visible(model, on: bool) -> None:
    """Show or hide the projection screen (both the geom's and its material's alpha). A model built
    without it is left alone."""
    import mujoco
    g = mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_GEOM, PROJ_TEXTURE)
    m = mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_MATERIAL, PROJ_TEXTURE)
    if g >= 0:
        model.geom_rgba[g][3] = 1.0 if on else 0.0
    if m >= 0:
        model.mat_rgba[m][3] = 1.0 if on else 0.0


# ---- the fitted head ------------------------------------------------------------------------
# The head is a swappable sub-assembly, chosen by ONE setting in the robot description (the Thor
# lane's): head_assembly.fitted in description/opendubs/dimensions.yaml, and the description carries
# only the fitted head's meshes and collision boxes. The body twin builds what the description holds,
# so it is never a second model to keep in step. Fitted now: the Galaxy tablet head, from CAD.
TABLET_HEAD = "galaxy_tablet"


def fitted_head() -> str:
    """head_assembly.fitted from the robot description; the tablet head when it cannot be read."""
    try:
        import yaml
        with open(os.path.join(OURS, "dimensions.yaml"), encoding="utf-8") as fh:
            return (yaml.safe_load(fh).get("head_assembly") or {}).get("fitted") or TABLET_HEAD
    except Exception:  # noqa: BLE001
        return TABLET_HEAD


def build(assets: str | None = None, chest_cover: bool = False, face_screen: bool = False,
          head: str | None = None, projection: bool = False, crawler=None, tools: dict | None = None):
    """`head`: galaxy_tablet, the head the robot description carries (the default).
    `projection`: the projection screen behind the robot, built hidden (geom alpha 0). `crawler`: a
    crawling hand's tool type (effectors.Tool) to add, posed by sim_preview.apply_crawler. `tools`:
    {"left"|"right": effectors.Tool} fitted at a flange in place of the OpenArm gripper, posed by
    sim_preview.apply_tools. Both VISUAL ONLY: the clearance sweep still sees the gripper's geometry."""
    import mujoco

    a = find_assets(assets)
    meshes = _mesh_dir(a)

    ours = mujoco.MjSpec.from_file(os.path.join(OURS, "opendubs.xml"))
    arms = mujoco.MjSpec.from_file(_arm_xml(a))
    ours.meshdir = meshes
    arms.meshdir = meshes
    head = head or fitted_head()
    if head != TABLET_HEAD:
        raise ValueError(f"head {head!r} is not in the robot description, which carries the {TABLET_HEAD} "
                         f"head (dimensions.yaml head_assembly.fitted)")
    if face_screen:
        _add_face_screen(ours)

    if projection:
        _add_projection(ours)

    for side, tool in sorted((tools or {}).items()):
        if tool is None or tool.id == "openarm_gripper":
            continue
        import hand
        hand.add_flange_tool(arms, side, tool)
    frame = ours.body("lift_platform").add_frame(pos=list(ARM_MOUNT_XYZ))
    frame.attach_body(arms.body("openarm_body_link0"), ARM_PREFIX, "")
    if crawler is not None:
        import hand
        hand.add_crawler(ours, crawler)

    model = ours.compile()
    for side, tool in sorted((tools or {}).items()):
        if tool is not None and tool.id != "openarm_gripper":
            import hand
            hand.hide_gripper(model, ARM_PREFIX, side)
    if not chest_cover:
        gid = mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_GEOM, ARM_PREFIX + CHEST_COVER_GEOM)
        if gid >= 0:
            model.geom_rgba[gid][3] = 0.0     # hidden, and it was never a collision geom
        else:
            print(f"note: {CHEST_COVER_GEOM} not found — has the arm model changed?", file=sys.stderr)
    return model


# ---- invariants ---------------------------------------------------------------------------
# Meshes arrive from CAD by hand, and a re-export can silently come back mirrored, upside down
# or about a different origin. Every one of those has happened here. These are the cheapest
# possible tripwires: facts about the robot that a human can confirm by looking at it, checked
# on every --check so a bad export fails loudly instead of quietly moving the head 100 mm.
HEAD_MAX_REACH_MM = 200.0     # nothing on the head is further than this from the tilt axis


def check_invariants(model, head: str | None = None) -> list[str]:
    """Return a list of failures; empty means the geometry is placed as the robot is built."""
    import mujoco
    import numpy as np

    data = mujoco.MjData(model)
    mujoco.mj_forward(model, data)
    fails = []

    def extent(mesh_name):
        for g in range(model.ngeom):
            if model.geom_type[g] != mujoco.mjtGeom.mjGEOM_MESH:
                continue
            if mujoco.mj_id2name(model, mujoco.mjtObj.mjOBJ_MESH, model.geom_dataid[g]) != mesh_name:
                continue
            a, n = model.mesh_vertadr[model.geom_dataid[g]], model.mesh_vertnum[model.geom_dataid[g]]
            R = np.zeros(9)
            mujoco.mju_quat2Mat(R, model.geom_quat[g])
            w = (model.mesh_vert[a:a + n] @ R.reshape(3, 3).T
                 + model.geom_pos[g] + data.xpos[model.geom_bodyid[g]]) * 1000.0
            return w.min(axis=0), w.max(axis=0)
        return None

    axis = np.array(HEAD_TILT_AXIS) * 1000.0
    face, neck = extent("head_face_tilt"), extent("head_neck_pan")
    sid = mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_SITE, "head_screen")
    if face is None or neck is None or sid < 0:
        return ["head meshes or the head_screen site missing from the model"]
    screen = data.site_xpos[sid] * 1000.0

    # The face points FORWARD. This is the one that has actually caught a bad export: a head
    # exported at pan=180 drives and renders perfectly and only looks wrong from the side.
    if screen[0] <= axis[0]:
        fails.append(f"screen is BEHIND the tilt axis (x {screen[0]:.0f} <= {axis[0]:.0f}) "
                     f"— the face is pointing backwards")
    # The screen site is ON the face's front, not floating off it: the glass is the face's front plane.
    if abs(face[1][0] - screen[0]) > 3.0:
        fails.append(f"the head_screen site (x {screen[0]:.1f}) is not on the face's front "
                     f"(x {face[1][0]:.1f}) — the site or the mesh has moved")
    # Everything on the head is near the axis it turns about. Catches a mesh exported about a
    # different origin, which a flip check would happily pass.
    for name, ext in (("face", face), ("neck", neck)):
        far = max(np.abs(np.array(ext[0]) - axis).max(), np.abs(np.array(ext[1]) - axis).max())
        if far > HEAD_MAX_REACH_MM:
            fails.append(f"{name} reaches {far:.0f} mm from the tilt axis, over "
                         f"{HEAD_MAX_REACH_MM:.0f} — exported about a different origin?")

    site = mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_SITE, "arm_frame_mount")
    shoulders = (data.site_xpos[site][2] + 0.094 + 0.698) * 1000.0
    if abs(shoulders - 1149.5) > 2.0:
        fails.append(f"shoulders at {shoulders:.1f} mm above the floor, expected 1149.5")
    return fails


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
    ap.add_argument("--head", choices=(TABLET_HEAD,), default=None,
                    help="which head (default: the robot description's head_assembly.fitted)")
    ap.add_argument("--view", action="store_true",
                    help="open the interactive viewer (see view.sh)")
    ap.add_argument("--lift", type=float, default=0.0, help="Z-axis height in mm (0..275.1)")
    ap.add_argument("--joints", default="", help='e.g. "right:2=150,right:4=100"')
    ap.add_argument("--chest-cover", action="store_true", help="draw the cover we removed")
    ap.add_argument("--drive", metavar="FWD,ROT,STR",
                    help="drive command, each -1..1, same meaning as the robot's DRIVE")
    ap.add_argument("--settle", type=float, default=0.0,
                    help="seconds of physics to run before rendering (needed with --drive)")
    ap.add_argument("--az", type=float, default=128.0)
    ap.add_argument("--el", type=float, default=-10.0)
    ap.add_argument("--dist", type=float, default=3.0)
    a = ap.parse_args()

    import mujoco

    model = build(a.assets, a.chest_cover, head=a.head)
    data = mujoco.MjData(model)
    # NOT qpos[0] any more: the chassis freejoint owns the first seven coordinates.
    zaxis = mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_JOINT, "zaxis")
    data.qpos[model.jnt_qposadr[zaxis]] = a.lift / 1000.0
    if a.joints:
        set_joints(model, data, a.joints)
    if a.drive:
        try:
            fwd, rot, str_ = (float(v) for v in a.drive.split(","))
        except ValueError:
            sys.exit('--drive wants three numbers, e.g. --drive "1,0,0"')
        for name, v in zip(DRIVE_ACTUATORS, (fwd, rot, str_)):
            i = mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_ACTUATOR, name)
            data.ctrl[i] = v * WHEEL_MAX_RADS
    mujoco.mj_forward(model, data)
    for _ in range(int(a.settle / model.opt.timestep)):
        mujoco.mj_step(model, data)

    print(f"{model.nbody} bodies, {model.njnt} joints, {model.ngeom} geoms")
    problems = check_invariants(model, a.head)
    for msg in problems:
        print(f"FAIL: {msg}", file=sys.stderr)
    if not problems:
        print("invariants ok — face forward, the screen on its glass, head on its axis, shoulders at height")
    site = mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_SITE, "arm_frame_mount")
    z = data.site_xpos[site][2]
    print(f"lift {a.lift:6.1f} mm -> arm frame base {(z + 0.094) * 1000:7.1f} mm above the floor, "
          f"shoulders {(z + 0.094 + 0.698) * 1000:7.1f} mm")
    if a.view:
        import mujoco.viewer
        # The BLOCKING viewer, which takes over this thread. A script-driven view uses
        # launch_passive under mjpython instead -- see view.sh.
        mujoco.viewer.launch(model, data)
        return
    if a.check or not a.render:
        sys.exit(1 if problems else 0)

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
