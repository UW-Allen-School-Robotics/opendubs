"""Things in the room that are not the robot, for the twin, the clearance sweep and pick planning.

THE CANDY BUCKET (ARCHITECT's trick-or-treat pick, #576): props/candy_bucket.stl, from the user's Fusion
model. The export came out upside down; the file here is flipped about x, in metres, with its origin at
the bottom centre and its handle arching along its own x. MuJoCo collides a mesh as its convex hull, which
would fill the opening, so the mesh is drawn only and the collision is built from primitives that leave
the inside free: the base, sixteen wall panels, and the handle as capsules along its arc.

WHERE IT STANDS. The best seen-and-reachable spot from the Thor lane's coverage study (#589,
description/coverage.py): 0.40 m forward of base_link and 0.20 m to the side, 0.90 m above the floor,
on a stand, because no arm reaches the floor. ARCHITECT's candy show picks with the RIGHT gripper, so
the default is the right side. That height is the CANDY LEVEL, where the gripper's tool point goes, not
the bucket's base: the bucket is taken as half full (CANDY_LEVEL_M above its base). The handle is turned
across the robot's line of approach and the bucket set back so the target is in the half nearer the
robot, clear of the handle (TARGET_OFFSET_M from the centre). The stand is a stand-in, a column of the
bucket's base diameter, until the real one is measured.

IT MOVES. The bucket is a free body (a freejoint, added after the robot's, so the chassis keeps qpos 0-6
and every arm joint keeps its address) resting on the stand, a separate fixed body, by contact. Its pose
is in the model (qpos0), and the target site is its child, so anything that reads the model sees the
bucket where it is. settle_check() drops it a few mm onto the stand and checks it comes to rest seated
and upright: a bad contact or mass would send it flying in every render.

Geometry: base r 92.5 mm, rim r 126 mm at 205 mm, a 5 mm floor, the handle 15 mm wide and up to 285 mm,
all read off the mesh.

TWO VARIANTS, the same bucket: `handle_up` (the default, props/candy_bucket.stl) and `handle_down`
(props/candy_bucket_handle_down.stl, the handle folded down onto the rim, 205 mm tall overall). The
second came from the same Fusion export frame and is flipped the same way. Its folded handle lies
along the rim, within the wall's own radius (r 120-126 mm, z 185-205 mm), so it collides as the base
and wall panels alone: no handle capsules. Mass, centre of mass, pose and target are the same.
"""
from __future__ import annotations

import math
import os

HERE = os.path.dirname(os.path.abspath(__file__))
CANDY_BUCKET_MESH = os.path.join(HERE, "props", "candy_bucket.stl")
# variant: (its mesh, whether its handle stands up and collides)
BUCKET_VARIANTS = {"handle_up": (CANDY_BUCKET_MESH, True),
                   "handle_down": (os.path.join(HERE, "props", "candy_bucket_handle_down.stl"), False)}
BUCKET_VARIANT = "handle_up"
BASE_LINK = (0.324, 0.0, -0.024)       # the model's world is the CAD datum; base_link sits here in it
FLOOR_Z = -0.094                       # the floor in the CAD datum (opendubs.xml)

CANDY_BUCKET_AT = (0.40, -0.20, 0.90)  # base_link x, y and the candy level above the floor (m)
CANDY_LEVEL_M = 0.10                   # half full: the inside runs from 5 mm to the rim at 205 mm
TARGET_OFFSET_M = 0.06                 # the target, this far from the centre toward the robot

BASE_R, RIM_R, RIM_Z, FLOOR_T, WALL_T = 0.0925, 0.1264, 0.205, 0.005, 0.004
HANDLE = [(-0.1165, 0.199), (-0.09, 0.249), (-0.06, 0.2686), (-0.03, 0.2806), (0.0, 0.284),
          (0.03, 0.2806), (0.06, 0.2686), (0.09, 0.249), (0.1165, 0.199)]   # its centreline (x, z)
HANDLE_R = 0.0075
# Its mass: about 0.4 kg of plastic, and half a load of candy. Half full by height is ~3 L of the ~7 L
# inside (it flares), at ~0.6 kg/L for wrapped candy: ~1.8 kg. So 2.2 kg, the centre of mass low.
BUCKET_MASS_KG = 2.2
BUCKET_COM_Z = 0.07


def candy_bucket_pose(at=None) -> tuple:
    """(base position in the model's world, yaw in degrees, the candy target in the world) for a
    bucket whose candy is at `at`: base_link x, y and height above the floor, in metres."""
    x, y, h = at or CANDY_BUCKET_AT
    target = (BASE_LINK[0] + x, BASE_LINK[1] + y, FLOOR_Z + h)
    away = math.atan2(y, x)                 # from base_link toward the bucket, on the floor plane
    base = (target[0] + TARGET_OFFSET_M * math.cos(away), target[1] + TARGET_OFFSET_M * math.sin(away),
            target[2] - CANDY_LEVEL_M)
    return base, math.degrees(away) + 90.0, target   # the handle across the line of approach


def add_candy_bucket(spec, at=None, stand=True, variant=None) -> None:
    """The bucket (and its stand) in the world of an MjSpec: drawn from the mesh, colliding as
    primitives (group 3, names prefixed candy_bucket_), with a site candy_bucket_target at the candy.
    `variant`: a BUCKET_VARIANTS key, handle_up by default."""
    import mujoco
    variant = variant or BUCKET_VARIANT
    if variant not in BUCKET_VARIANTS:
        raise ValueError(f"bucket variant {variant!r}: one of {', '.join(BUCKET_VARIANTS)}")
    mesh, handle = BUCKET_VARIANTS[variant]
    base, yaw, target = candy_bucket_pose(at)
    vis = dict(group=2, contype=0, conaffinity=0)
    spec.add_mesh(name="candy_bucket", file=mesh)
    half = math.radians(yaw) / 2
    b = spec.worldbody.add_body(name="candy_bucket", pos=list(base), quat=[math.cos(half), 0, 0, math.sin(half)])
    b.add_freejoint(name="candy_bucket_free")
    b.explicitinertial = True
    b.mass, b.ipos = BUCKET_MASS_KG, [0, 0, BUCKET_COM_Z]
    r, h = RIM_R * 0.8, RIM_Z                          # a solid cylinder of about its size
    b.inertia = [BUCKET_MASS_KG * (3 * r * r + h * h) / 12] * 2 + [BUCKET_MASS_KG * r * r / 2]
    b.add_geom(name="candy_bucket_visual", type=mujoco.mjtGeom.mjGEOM_MESH, meshname="candy_bucket",
               rgba=[0.95, 0.45, 0.05, 1.0], **vis)
    col = dict(group=3, rgba=[0.9, 0.5, 0.1, 0.4])
    b.add_geom(name="candy_bucket_base", type=mujoco.mjtGeom.mjGEOM_CYLINDER, size=[BASE_R, FLOOR_T / 2, 0],
               pos=[0, 0, FLOOR_T / 2], **col)
    n = 16
    r0, r1 = BASE_R - WALL_T / 2, RIM_R - WALL_T / 2
    slant, tilt = math.hypot(r1 - r0, RIM_Z), math.atan2(r1 - r0, RIM_Z)
    half_w = RIM_R * math.tan(math.pi / n) * 1.05      # wide enough to close the gaps at the rim
    for i in range(n):
        a = 2 * math.pi * i / n
        rm = (r0 + r1) / 2
        # turn about z to the panel's bearing, then tip its top outward about the panel's own y
        qz = (math.cos(a / 2), 0, 0, math.sin(a / 2))
        qy = (math.cos(tilt / 2), 0, math.sin(tilt / 2), 0)
        q = _qmul(qz, qy)
        b.add_geom(name=f"candy_bucket_wall{i:02d}", type=mujoco.mjtGeom.mjGEOM_BOX,
                   size=[WALL_T / 2, half_w, slant / 2], pos=[rm * math.cos(a), rm * math.sin(a), RIM_Z / 2],
                   quat=list(q), **col)
    for i, ((x0, z0), (x1, z1)) in enumerate(zip(HANDLE, HANDLE[1:]) if handle else ()):
        b.add_geom(name=f"candy_bucket_handle{i}", type=mujoco.mjtGeom.mjGEOM_CAPSULE, size=[HANDLE_R, 0, 0],
                   fromto=[x0, 0, z0, x1, 0, z1], **col)
    c, sn = math.cos(math.radians(yaw)), math.sin(math.radians(yaw))
    dx, dy, dz = (t - o for t, o in zip(target, base))
    b.add_site(name="candy_bucket_target", pos=[c * dx + sn * dy, -sn * dx + c * dy, dz], size=[0.01, 0, 0],
               rgba=[1, 0.9, 0.1, 1])                 # in the bucket's frame: it goes where the bucket goes
    if stand:
        h = base[2] - FLOOR_Z
        s = spec.worldbody.add_body(name="candy_bucket_stand", pos=[base[0], base[1], FLOOR_Z + h / 2])
        s.add_geom(name="candy_bucket_stand_visual", type=mujoco.mjtGeom.mjGEOM_CYLINDER, size=[BASE_R, h / 2, 0],
                   rgba=[0.35, 0.35, 0.38, 1.0], **vis)
        s.add_geom(name="candy_bucket_stand", type=mujoco.mjtGeom.mjGEOM_CYLINDER, size=[BASE_R, h / 2, 0],
                   group=3, rgba=[0.35, 0.35, 0.38, 0.4])


def _qmul(p, q):
    a1, b1, c1, d1 = p
    a2, b2, c2, d2 = q
    return (a1 * a2 - b1 * b2 - c1 * c2 - d1 * d2, a1 * b2 + b1 * a2 + c1 * d2 - d1 * c2,
            a1 * c2 - b1 * d2 + c1 * a2 + d1 * b2, a1 * d2 + b1 * c2 - c1 * b2 + d1 * a2)


def settle_check(model, drop_m=0.005, secs=1.0) -> list[str]:
    """Drop the bucket `drop_m` onto its stand and step `secs`: it must end seated (within 2 mm of where
    the model puts it) and upright (within 2 degrees), and at rest. [] when it does; else what went wrong."""
    import mujoco
    import numpy as np
    j = mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_JOINT, "candy_bucket_free")
    if j < 0:
        return []
    a, v = model.jnt_qposadr[j], model.jnt_dofadr[j]
    data = mujoco.MjData(model)
    seat = model.qpos0[a:a + 3].copy()
    data.qpos[a + 2] += drop_m
    for _ in range(int(secs / model.opt.timestep)):
        mujoco.mj_step(model, data)
    pos, quat = data.qpos[a:a + 3], data.qpos[a + 3:a + 7]
    up = 1 - 2 * (quat[1] ** 2 + quat[2] ** 2)          # the bucket's z axis . world z
    fails = []
    if np.linalg.norm(pos - seat) > 0.002:
        fails.append(f"candy bucket did not settle on its stand: {np.round((pos - seat) * 1000, 1)} mm from its seat")
    if up < math.cos(math.radians(2)):
        fails.append(f"candy bucket tipped {math.degrees(math.acos(max(-1.0, min(1.0, up)))):.1f} deg")
    if np.linalg.norm(data.qvel[v:v + 6]) > 0.05:
        fails.append(f"candy bucket still moving after {secs:g} s ({np.linalg.norm(data.qvel[v:v + 6]):.2f})")
    return fails
