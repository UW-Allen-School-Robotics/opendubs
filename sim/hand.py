#!/usr/bin/env python3
"""A tool from the registry (software/choreo/effectors/) as a twin model: the Surge hand from its vendor
URDF, mirrored for the left hand, or a PRIMITIVE stand-in built from the same channels.

    python3 hand.py --check                         # load each hand type, report, prove the mirror
    python3 hand.py --render /tmp/hand.png --tool surge_left --pose fist

WHERE THE SURGE MODEL COMES FROM: the folder named by $OPENDUBS_SURGE_DIR, holding the vendor's
`surge_hands/` (its URDF, meshes/ and LICENSE: MIT, (c) 2025 Alt-Bionics Inc). It is not in this
repository (its visual meshes are 18 MB). Without it, every Surge type is drawn as the primitive hand,
which moves through the same channels, and the tests that need the real model skip.

THE LOADER is ported from the project's Surge Hand investigation (its sim/surge_model.py), with its two
fixes to the vendor URDF: the Middle MCP joint's upper limit `1774` is `1.774` (a typo), and the masses
are scaled to the datasheet's 540 g (the links sum to ~1.68 kg). Here the hand is only ever POSED, never
simulated, so its geoms are visual only: no contacts, no mass in the robot's model.

THE MIRROR (a left hand from the right URDF) reflects the model through the hand's own y = 0 plane:
  * each mesh is scaled by -1 in y (MuJoCo flips its faces' winding to keep them outward);
  * each body's, joint's and geom's position has y negated; each orientation R becomes M R M with
    M = diag(1, -1, 1), i.e. a quaternion (w, x, y, z) becomes (w, -x, y, -z);
  * each hinge axis a becomes -M a: a reflected rotation turns the other way about the reflected axis,
    so the extra sign keeps "positive = flex" for every channel;
  * each inertia has its xy and yz products negated.
`mirror_check()` proves it: for random channel values, every body of the left hand sits exactly at the
right hand's position reflected (tests/test_effectors.py runs it when the model is on disk).
"""
from __future__ import annotations

import math
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(REPO, "software", "choreo"))

PREFIX = "hand_"              # every name the hand adds to a scene
CRAWLER_BODY = "crawler"      # the mocap body a crawling hand hangs from
LEASH_SEGMENTS = 28


def surge_dir() -> str | None:
    d = os.environ.get("OPENDUBS_SURGE_DIR")
    if not d:
        return None
    d = os.path.expanduser(d)
    for c in (d, os.path.join(d, "surge_hands")):
        if os.path.isdir(os.path.join(c, "meshes")):
            return c
    return None


def _mq(q):
    """A quaternion's mirror image through the y = 0 plane (M R M, M = diag(1, -1, 1))."""
    w, x, y, z = q
    return [w, -x, y, -z]


def mirror_spec(spec) -> None:
    """Reflect a whole MjSpec through y = 0, in place, keeping every joint's positive sense (see above)."""
    for b in spec.bodies:
        if b.name == "world":
            continue
        b.pos = [b.pos[0], -b.pos[1], b.pos[2]]
        b.quat = _mq(b.quat)
        b.ipos = [b.ipos[0], -b.ipos[1], b.ipos[2]]
        b.iquat = _mq(b.iquat)
        fi = list(b.fullinertia)
        if any(not math.isnan(v) for v in fi):
            b.fullinertia = [fi[0], fi[1], fi[2], -fi[3], fi[4], -fi[5]]
    for j in spec.joints:
        j.pos = [j.pos[0], -j.pos[1], j.pos[2]]
        a = j.axis
        j.axis = [-a[0], a[1], -a[2]]
    for g in spec.geoms:
        g.pos = [g.pos[0], -g.pos[1], g.pos[2]]
        g.quat = _mq(g.quat)
        ft = list(g.fromto)
        if not any(math.isnan(v) for v in ft):
            g.fromto = [ft[0], -ft[1], ft[2], ft[3], -ft[4], ft[5]]
    for s in spec.sites:
        s.pos = [s.pos[0], -s.pos[1], s.pos[2]]
        s.quat = _mq(s.quat)
    for m in spec.meshes:
        sc = list(m.scale)
        m.scale = [sc[0], -sc[1], sc[2]]


def _surge_spec(tool, hand_dir: str):
    import mujoco
    urdf_name = tool.sim.get("urdf")
    with open(os.path.join(hand_dir, urdf_name), encoding="latin-1") as f:
        urdf = f.read()
    urdf = re.sub(r'upper="1774"', 'upper="1.774"', urdf)            # the vendor's typo (Middle MCP)
    inject = (f'<mujoco><compiler meshdir="{hand_dir}" strippath="false" balanceinertia="true" '
              f'discardvisual="false" fusestatic="false"/></mujoco>')
    urdf = re.sub(r"(<robot[^>]*>)", r"\1" + inject, urdf, count=1)
    spec = mujoco.MjSpec.from_string(urdf)
    for g in list(spec.geoms):                 # the posed twin needs only what is seen
        if g.group == 0 and g.meshname and "collide" in str(g.meshname):
            spec.delete(g)
    for m in spec.meshes:              # in full: a scene that attaches the hand has its own meshdir
        if not os.path.isabs(m.file):
            m.file = os.path.join(hand_dir, m.file)
    total = sum(b.mass for b in spec.bodies if b.name != "world")
    k = float(tool.sim.get("mass_kg", 0.54)) / total if total > 0 else 1.0
    for b in spec.bodies:
        if b.name != "world":
            b.mass *= k
    return spec


def _primitive_spec(tool):
    """A hand of boxes and capsules with the Surge's joint names and the same kinematic sense (palm frame:
    +z along the fingers, +x palmar, +y toward the thumb on a right hand) — or a flange tool's parts."""
    import mujoco
    spec = mujoco.MjSpec()
    palm = spec.worldbody.add_body(name="Palm")
    grey, dark = [0.72, 0.72, 0.76, 1], [0.25, 0.25, 0.28, 1]
    if tool.kind != "hand":
        for i, part in enumerate(tool.sim.get("parts") or []):
            shape = {"box": mujoco.mjtGeom.mjGEOM_BOX, "cylinder": mujoco.mjtGeom.mjGEOM_CYLINDER}[part["shape"]]
            b = palm.add_body(name=f"part{i}", pos=part["pos"])
            if part.get("moves"):
                ax = {"x": [1, 0, 0], "y": [0, 1, 0], "z": [0, 0, 1]}[part["moves"]["axis"]]
                b.add_joint(name=f"part{i}_slide", type=mujoco.mjtJoint.mjJNT_SLIDE, axis=ax, range=[-0.1, 0.1])
            b.add_geom(type=shape, size=list(part["size"]) + [0] * (3 - len(part["size"])), rgba=part.get("rgba", grey))
        return spec
    pr = tool.sim.get("primitive") or {}
    hx, hy, hz = pr.get("palm", [0.026, 0.048, 0.062])
    palm.add_geom(type=mujoco.mjtGeom.mjGEOM_BOX, size=[hx * 0.5, hy, hz], pos=[0, 0, hz * 0.8], rgba=grey)
    l1, l2 = pr.get("finger_len", [0.05, 0.045])
    r = pr.get("finger_r", 0.009)
    for name, y in (("Index", 0.032), ("Middle", 0.0094), ("Ring", -0.0134), ("Pinky", -0.0359)):
        p = palm.add_body(name=f"{name}_Proximal", pos=[-0.01, y, 0.104])
        p.add_joint(name=f"{name}_MCP_Joint", axis=[0, 1, 0], range=[0, 1.774])
        p.add_geom(type=mujoco.mjtGeom.mjGEOM_CAPSULE, fromto=[0, 0, 0, 0, 0, l1], size=[r, 0, 0], rgba=grey)
        d = p.add_body(name=f"{name}_Distal", pos=[0, 0, l1])
        d.add_joint(name=f"{name}_PIP_Joint", axis=[0, 1, 0], range=[0, 1.571])
        d.add_geom(type=mujoco.mjtGeom.mjGEOM_CAPSULE, fromto=[0, 0, 0, 0, 0, l2], size=[r * 0.9, 0, 0], rgba=dark)
    mc = palm.add_body(name="Metacarpal", pos=[0.008, 0.0254, 0.0324])
    mc.add_joint(name="Metacarpal_Joint", axis=[0, 0, -1], range=[0, 1.774])
    mc.add_geom(type=mujoco.mjtGeom.mjGEOM_CAPSULE, fromto=[0, 0, 0, 0, 0.03, 0], size=[r, 0, 0], rgba=grey)
    th = mc.add_body(name="Thumb", pos=[0, 0.03, 0.002])
    th.add_joint(name="Thumb_Joint", axis=[0, -1, 0], range=[0, 1.774])
    th.add_geom(type=mujoco.mjtGeom.mjGEOM_CAPSULE, fromto=[0, 0, 0, 0, 0, 0.045], size=[r, 0, 0], rgba=dark)
    return spec


def tool_spec(tool, hand_dir: str | None = None):
    """(MjSpec of the tool, alone, rooted at a body named Palm; 'surge' or 'primitive')."""
    src = None
    if tool.sim.get("model") == "surge":
        src = hand_dir or surge_dir()
    if src:
        spec, kind = _surge_spec(tool, src), "surge"
    else:
        spec, kind = _primitive_spec(tool), "primitive"
    if tool.mirrored and not tool.doc.get("_own_urdf"):
        mirror_spec(spec)
    return spec, kind


# ---- in the robot's scene ---------------------------------------------------------------------------------
def add_crawler(scene_spec, tool, hand_dir: str | None = None) -> str:
    """Put a crawling hand in the scene: a mocap body (posed each frame from the crawl's predicted path)
    carrying the hand palm down, fingers forward, and a leash drawn as segments. Visual only: no contact,
    no mass. Returns 'surge' or 'primitive'."""
    import mujoco
    spec, kind = tool_spec(tool, hand_dir)
    for g in spec.geoms:
        g.contype = g.conaffinity = 0
        g.group = 2
    _add_puck(spec.body("Palm"))
    body = scene_spec.worldbody.add_body(name=PREFIX + CRAWLER_BODY, mocap=True)
    # palm down, fingers forward: the hand's +x (palmar) to world -z, its +z (fingers) to world +x
    frame = body.add_frame(quat=[math.cos(math.pi / 4), 0, math.sin(math.pi / 4), 0])
    frame.attach_body(spec.body("Palm"), PREFIX, "")
    # the leash: one mocap body per segment, placed each frame (leash_update). Not geoms on the world body:
    # MuJoCo treats those as static and never recomputes their orientation.
    for i in range(LEASH_SEGMENTS):
        seg = scene_spec.worldbody.add_body(name=f"{PREFIX}leash{i}", mocap=True, pos=[0, 0, -5])
        seg.add_geom(name=f"{PREFIX}leash{i}", type=mujoco.mjtGeom.mjGEOM_CAPSULE, size=[0.004, 0.01, 0],
                     rgba=[0.95, 0.55, 0.1, 1], contype=0, conaffinity=0, group=2)
    return kind


def add_flange_tool(arm_spec, side: str, tool) -> str:
    """Fit a tool at an arm's flange (the OpenArm hand body's origin), its +z along the tool axis, in the
    ARM model's spec before it is attached to the robot. Visual only. Returns 'surge' or 'primitive'."""
    import mujoco
    import numpy as np
    spec, kind = tool_spec(tool)
    for g in spec.geoms:
        g.contype = g.conaffinity = 0
        g.group = 2
    # seat the tool's base on the flange: lift it by however far its palm reaches below its own origin
    m = spec.compile()
    d = mujoco.MjData(m)
    mujoco.mj_forward(m, d)
    palm, low = m.body("Palm").id, 0.0
    for g in range(m.ngeom):
        if m.geom_bodyid[g] != palm:
            continue
        if m.geom_type[g] == mujoco.mjtGeom.mjGEOM_MESH:
            a, n = m.mesh_vertadr[m.geom_dataid[g]], m.mesh_vertnum[m.geom_dataid[g]]
            z = (d.geom_xpos[g] + m.mesh_vert[a:a + n] @ d.geom_xmat[g].reshape(3, 3).T)[:, 2]
            low = min(low, float(z.min()))
        else:
            low = min(low, float(d.geom_xpos[g][2] - m.geom_rbound[g]))
    arm_spec.body(f"openarm_{side}_hand").add_frame(pos=[0, 0, -low]).attach_body(spec.body("Palm"), f"{PREFIX}{side}_", "")
    return kind


def hide_gripper(model, arm_prefix: str, side: str) -> None:
    """Hide the OpenArm gripper a fitted tool replaces (its hand and finger visuals). Its collision geometry
    stays: the clearance sweep still checks the gripper, not the tool."""
    import mujoco
    for g in range(model.ngeom):
        b = mujoco.mj_id2name(model, mujoco.mjtObj.mjOBJ_BODY, model.geom_bodyid[g]) or ""
        if b.startswith(f"{arm_prefix}openarm_{side}_") and b.endswith(("_hand", "_finger")) and model.geom_group[g] != 3:
            model.geom_rgba[g][3] = 0.0


def _add_puck(palm) -> None:
    """The wrist puck a crawling hand wears (the Surge Hand investigation's design: a flange on the wrist,
    a body behind it, two free wheels and a leash ring), drawn in the palm's frame (+x palmar, z = 0 the
    wrist face). Its dimensions follow that design's defaults; drawn only."""
    import mujoco
    q90x = [math.cos(math.pi / 4), math.sin(math.pi / 4), 0, 0]
    teal, dark = [0.25, 0.55, 0.5, 1], [0.15, 0.15, 0.17, 1]
    palm.add_geom(type=mujoco.mjtGeom.mjGEOM_CYLINDER, size=[0.0315, 0.005, 0], pos=[0, 0, -0.005], rgba=teal)
    palm.add_geom(type=mujoco.mjtGeom.mjGEOM_BOX, size=[0.025, 0.0315, 0.031], pos=[0, 0, -0.041], rgba=teal)
    for side in (1, -1):
        palm.add_geom(type=mujoco.mjtGeom.mjGEOM_CYLINDER, size=[0.02, 0.006, 0], pos=[0.025, side * 0.04, -0.014],
                      quat=q90x, rgba=dark)


def channel_qpos(model, tool, values: dict, data, prefix: str = PREFIX) -> None:
    """Set the hand's joints from channel values (tool.sim.joints: radians at channel 1)."""
    import mujoco
    for ch, joints in (tool.sim.get("joints") or {}).items():
        v = values.get(ch)
        if v is None or isinstance(v, str):
            continue
        for jname, rad in joints.items():
            j = mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_JOINT, prefix + jname)
            if j >= 0:
                data.qpos[model.jnt_qposadr[j]] = float(v) * float(rad)


def place_crawler(model, data, x: float, y: float, heading_deg: float, z: float, roll_deg: float = 0.0,
                  pitch_deg: float = 0.0) -> None:
    """The crawler's mocap body: (x, y) on the floor, heading about z, then the gait's roll and pitch."""
    import mujoco
    b = mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_BODY, PREFIX + CRAWLER_BODY)
    mid = model.body_mocapid[b]
    if mid < 0:
        return
    data.mocap_pos[mid] = [x, y, z]
    h, r, p = (math.radians(v) / 2 for v in (heading_deg, roll_deg, pitch_deg))
    qz = [math.cos(h), 0, 0, math.sin(h)]
    qx = [math.cos(r), math.sin(r), 0, 0]
    qy = [math.cos(p), 0, math.sin(p), 0]
    import numpy as np
    tmp, q = np.zeros(4), np.zeros(4)
    mujoco.mju_mulQuat(tmp, np.array(qz), np.array(qy))
    mujoco.mju_mulQuat(q, tmp, np.array(qx))
    data.mocap_quat[mid] = q


def leash_update(model, data, a, b, length: float, floor_z: float) -> None:
    """Draw the leash from anchor `a` to the hand's wrist `b` (world xyz): a slack cable that drops from the
    anchor to the floor and lies along it to the hand, its spare length laid as a gentle bow."""
    import mujoco
    import numpy as np
    a, b = np.asarray(a, float), np.asarray(b, float)
    n = LEASH_SEGMENTS
    flat = np.linalg.norm((b - a)[:2])
    spare = max(0.0, length - flat - (a[2] - floor_z))
    side = np.array([-(b - a)[1], (b - a)[0], 0.0])
    side = side / (np.linalg.norm(side) or 1.0)
    bow = min(0.35, 0.5 * math.sqrt(max(0.0, spare) * max(flat, 0.05)))
    pts = []
    for i in range(n + 1):
        s = i / n
        p = a + (b - a) * s
        drop = max(0.0, 1.0 - s / 0.18)                     # down off the anchor within the first stretch
        p[2] = floor_z + 0.005 + (a[2] - floor_z - 0.005) * drop ** 2 + (b[2] - floor_z) * s ** 4
        p += side * bow * math.sin(math.pi * s)
        pts.append(p)
    for i in range(n):
        g = mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_GEOM, f"{PREFIX}leash{i}")
        mid_ = model.body_mocapid[model.geom_bodyid[g]]
        p0, p1 = pts[i], pts[i + 1]
        mid, d = (p0 + p1) / 2, p1 - p0
        L = float(np.linalg.norm(d)) or 1e-6
        model.geom_size[g][:2] = [0.004, L / 2 + 0.002]
        q = np.zeros(4)
        mujoco.mju_quatZ2Vec(q, d / L)
        data.mocap_pos[mid_] = mid
        data.mocap_quat[mid_] = q


def mirror_check(hand_dir: str | None = None, trials: int = 20, seed: int = 0) -> float:
    """The worst distance (m) between a left-hand body and the right hand's matching body reflected, over
    random channel values — and the same for every geom's centre, which a joint turning the wrong way would
move. 0 (to rounding) is a correct mirror."""
    import mujoco
    import numpy as np
    import effectors
    reg = effectors.registry()
    models = {}
    for tid in ("surge_right", "surge_left"):
        spec, _ = tool_spec(reg[tid], hand_dir)
        spec.body("Palm").pos = [0, 0, 0]
        models[tid] = spec.compile()
    rng = np.random.default_rng(seed)
    worst = 0.0
    for _ in range(trials):
        vals = {ch: float(rng.uniform(0, 1)) for ch in reg["surge_right"].channels}
        pos = {}
        for tid, m in models.items():
            d = mujoco.MjData(m)
            for ch, joints in reg[tid].sim["joints"].items():
                for jn, rad in joints.items():
                    j = mujoco.mj_name2id(m, mujoco.mjtObj.mjOBJ_JOINT, jn)
                    d.qpos[m.jnt_qposadr[j]] = vals[ch] * rad
            mujoco.mj_forward(m, d)
            pos[tid] = {m.body(i).name: d.xpos[i].copy() for i in range(1, m.nbody)}
            # every geom's centre too (a mesh's centroid): this is what catches a joint turning the wrong way
            pos[tid].update({f"geom{i}": d.geom_xpos[i].copy() for i in range(m.ngeom)})
        for name, p in pos["surge_right"].items():
            q = pos["surge_left"][name]
            worst = max(worst, float(np.linalg.norm(q - p * np.array([1, -1, 1]))))
    return worst


def main():
    import argparse
    import effectors
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true")
    ap.add_argument("--render", metavar="PNG")
    ap.add_argument("--tool", default="surge_right")
    ap.add_argument("--pose", default="open")
    a = ap.parse_args()
    reg = effectors.registry()
    print("surge model:", surge_dir() or "NOT FOUND ($OPENDUBS_SURGE_DIR) — the primitive stands in")
    if a.check:
        for tid, t in reg.items():
            spec, kind = tool_spec(t)
            m = spec.compile()
            print(f"  {tid}: {kind}, {m.nbody - 1} bodies, {m.njnt} joints, {m.ngeom} geoms")
        print(f"  mirror: worst body error {mirror_check() * 1000:.4f} mm")
    if a.render:
        import mujoco
        from PIL import Image
        t = reg[a.tool]
        spec, kind = tool_spec(t)
        spec.worldbody.add_light(pos=[0.3, 0.3, 0.6], dir=[-0.5, -0.5, -1])
        m = spec.compile()
        d = mujoco.MjData(m)
        for ch, joints in (t.sim.get("joints") or {}).items():
            for jn, rad in joints.items():
                j = mujoco.mj_name2id(m, mujoco.mjtObj.mjOBJ_JOINT, jn)
                if j >= 0:
                    d.qpos[m.jnt_qposadr[j]] = t.poses.get(a.pose, {}).get(ch, 0) * rad
        mujoco.mj_forward(m, d)
        cam = mujoco.MjvCamera()
        cam.lookat[:] = [0, 0, 0.09]
        cam.distance, cam.azimuth, cam.elevation = 0.45, 200, -20
        r = mujoco.Renderer(m, 480, 480)
        r.update_scene(d, cam)
        Image.fromarray(r.render()).save(a.render)
        print("wrote", a.render)


if __name__ == "__main__":
    main()
