#!/usr/bin/env python3
"""What the head's Femto Bolt can see as the neck looks around, where the arms can reach, and where
the two overlap: a desk tool for choosing where to put a target (the candy bucket for ARCHITECT's
pick, #576).

    uv run --python 3.12 --with mujoco --with pyyaml --with numpy --with matplotlib \\
        python3 description/coverage.py --out /tmp/coverage

Writes, per Femto mount (dimensions.yaml femto_mount.variants), a top-down plan (plan-<mount>.png)
and a side view (side-<mount>.png), in metres from base_link, plus bucket-zone.md: the best
positions and heights. Nothing here talks to the robot.

THE CAMERA. Orbbec's own figures, from the Femto Bolt Hardware Specifications, sections 4 and 5
(doc.orbbec.com, "Orbbec Femto Bolt Documentation / Femto Bolt Hardware Specifications", updated
2025-09-15):
  depth NFOV  75 x 65 deg, 0.5-3.86 m unbinned (0.5-5.46 m 2x2 binned)
  depth WFOV  120 x 120 deg, 0.25-2.88 m 2x2 binned (0.25-2.21 m unbinned)
  colour      80 x 51 deg at 16:9 (65 x 51 at 4:3); no range is given
Orbbec calls the depth figure the "field of interest". NFOV is modelled as a rectangular pyramid, and
WFOV as a cone of half-angle 60 deg, because its field is circular. The colour camera's range is cut
at COLOUR_FAR_M for drawing. Range is the forward (depth) distance, which is what a ToF camera
measures. The frames are the description's: camera_link is the depth camera, pitched 6 deg down from
the colour camera (Orbbec's coordinate-system documentation), and the 30-degree mount adds 30 to
both. Occlusion by the robot's own face, arms or body is NOT modelled.

THE NECK. Two envelopes, both from the neck firmware (firmware/neck_dial/neck/pantilt_core.h), in
real degrees at the servo's 300 deg over 500-2500 us (300/180 = 1.67 real degrees per commanded
degree; the servo's specification, as software/kiosk-dashboard/backend/neck_state.py uses it):
  gaze  what software (a show, a tracker, ARCHITECT) can command: kPanRangeDeg 30 and
        kTiltRangeDeg 18 either side of centre, so pan +/-50 and tilt +/-30, inside the robot's band
  rc    what the transmitter can reach: pan 0-180 commanded (+/-150 real), tilt inside the robot's
        commissioned band (per robot, in the neck's NVS: pass it as --tilt-band, as NECK reports it)
The description's tilt joint stops at 15.75 deg down (an older servo characterisation); the neck
controller's limits are the robot's operating ones (dimensions.yaml head), so they are used here.

THE ARMS. Exactly the checks ARCHITECT's live arm service makes before it moves a gripper to a point
(the Arms lane's answer, 2026-10-27): the arm's workspace box, ik.py's reach within 1 cm, and 30 mm
clearance on the move from stow, with the lift down and the head centred (see Reach). Any hand
orientation: the service places the tool point and holds no orientation.

THE BUCKET ZONE is where a bucket can stand (not over the robot) that an arm reaches and the depth
camera sees WELL: inside the middle WELL of its field, so the bucket and the incoming hand are both in
view, somewhere in the gaze envelope. --sweep-tilt also poses the camera at other down-tilts (see
TILT_PIVOT_EXPORT) and scores each for the zone and for the room.
"""
from __future__ import annotations

import argparse
import math
import os
import sys
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
OURS = HERE / "opendubs"
sys.path.insert(0, str(OURS))

BASE_LINK_CAD = np.array([0.324, 0.0, -0.024])   # dimensions.yaml base_link.xyz_mm
FLOOR_Z = -0.094 - BASE_LINK_CAD[2]              # the floor in base_link: datum.floor_z_mm
LIFT_STROKE = 0.2751                             # dimensions.yaml lift.stroke_mm
COLOUR_FAR_M = 4.0
DOC = ("doc.orbbec.com: Orbbec Femto Bolt Documentation / Femto Bolt Hardware Specifications, "
       "sections 4-5 (2025-09-15)")
FOVS = {
    "depth_nfov": {"site": "camera_link", "kind": "rect", "h": 75.0, "v": 65.0, "near": 0.5, "far": 3.86},
    "depth_wfov": {"site": "camera_link", "kind": "cone", "half": 60.0, "near": 0.25, "far": 2.88},
    "colour": {"site": "camera_color", "kind": "rect", "h": 80.0, "v": 51.0, "near": 0.0, "far": COLOUR_FAR_M},
}
SPAN = 300.0 / 180.0                             # real degrees per commanded degree
GAZE_PAN, GAZE_TILT = 30.0 * SPAN, 18.0 * SPAN   # pantilt_core.h kPanRangeDeg, kTiltRangeDeg
RC_PAN = 90.0 * SPAN                             # kRcPanMin..kRcPanMax = 0..180 commanded


def envelopes(tilt_band):
    """{name: (pan list, tilt list)} in the description's radians: + pan left, + tilt DOWN. The neck's
    commanded tilt RISES as the face tips up, so the band's low end is the furthest down."""
    lo, hi = tilt_band
    down, up = (90.0 - lo) * SPAN, (hi - 90.0) * SPAN
    g_down, g_up = min(GAZE_TILT, down), min(GAZE_TILT, up)
    def grid(a, b, step):
        n = max(2, int(round((b - a) / step)) + 1)
        return [math.radians(x) for x in np.linspace(a, b, n)]
    return {"gaze": (grid(-GAZE_PAN, GAZE_PAN, 2.5), grid(-g_up, g_down, 2.5)),
            "rc": (grid(-RC_PAN, RC_PAN, 5.0), grid(-up, down, 5.0))}


# A mount at any down-tilt, for choosing one to print. The two real mounts fix it: the 30-degree
# mesh's camera is the default's turned 30 deg about the y-parallel line through this point (head
# export frame, mm), which solves (I - R) c = t exactly for its measured transform. Turning the default
# camera about the same line by any angle reproduces both real mounts and models a reprint between or
# beyond them. A reprint drawn differently would hinge elsewhere, by millimetres.
TILT_PIVOT_EXPORT = (88.179, 957.693)                 # x, z
DEPTH_PITCH_DEG = 6.0                                 # Orbbec: depth 6 deg below colour


class Head:
    """The description's head, posed kinematically: camera sites in base_link for a lift and pan/tilt.
    `mount` is a real mount (variants.FEMTO_MOUNTS); `tilt` instead poses the default camera turned
    `tilt` degrees down about TILT_PIVOT_EXPORT."""

    def __init__(self, mount: str = "straight", tilt: float | None = None):
        import mujoco
        import variants
        import yaml
        self.mj = mujoco
        spec = mujoco.MjSpec.from_file(str(OURS / "opendubs.xml"))
        variants.femto_mount(spec, mount if tilt is None else "straight")
        if tilt is not None:
            d = yaml.safe_load((OURS / "dimensions.yaml").read_text())
            marks = d["head"]["export"]["landmarks_export_mm"]
            iface = marks["femto_interface"]
            a = math.radians(tilt)
            cx, cz = TILT_PIVOT_EXPORT
            for site, mark, pitch in (("camera_link", "femto_depth_mark", DEPTH_PITCH_DEG + tilt),
                                      ("camera_color", "femto_color_mark", tilt)):
                x, y, z = marks[mark]
                xr = math.cos(a) * (x - cx) + math.sin(a) * (z - cz) + cx
                zr = -math.sin(a) * (x - cx) + math.cos(a) * (z - cz) + cz
                st = spec.site(site)
                st.pos = [(xr - iface[0]) / 1000, (y - iface[1]) / 1000, (zr - iface[2]) / 1000]
                h = math.radians(pitch) / 2
                st.quat = [math.cos(h), 0.0, math.sin(h), 0.0]
        self.m = spec.compile()
        self.d = mujoco.MjData(self.m)
        self.q = {j: self.m.jnt_qposadr[mujoco.mj_name2id(self.m, mujoco.mjtObj.mjOBJ_JOINT, j)]
                  for j in ("zaxis", "head_pan", "head_tilt")}

    def site(self, name, lift, pan, tilt):
        mj = self.mj
        self.d.qpos[self.q["zaxis"]], self.d.qpos[self.q["head_pan"]], self.d.qpos[self.q["head_tilt"]] = lift, pan, tilt
        mj.mj_kinematics(self.m, self.d)
        i = mj.mj_name2id(self.m, mj.mjtObj.mjOBJ_SITE, name)
        return self.d.site_xpos[i] - BASE_LINK_CAD, self.d.site_xmat[i].reshape(3, 3).copy()


def visible(points, pos, rot, fov, frac=1.0):
    """Boolean mask: which points (N x 3, base_link) the camera at pos/rot sees, x forward. `frac` < 1
    keeps only the middle of the field (that fraction of each half-angle)."""
    p = (points - pos) @ rot                     # into the camera's own axes
    x = p[:, 0]
    ok = (x > max(fov["near"], 1e-3)) & (x < fov["far"])
    if fov["kind"] == "rect":
        ok &= np.abs(np.arctan2(p[:, 1], x)) <= math.radians(frac * fov["h"] / 2)
        ok &= np.abs(np.arctan2(p[:, 2], x)) <= math.radians(frac * fov["v"] / 2)
    else:
        ok &= np.arctan2(np.hypot(p[:, 1], p[:, 2]), x) <= math.radians(frac * fov["half"])
    return ok


def coverage(head, fov, lift, pans, tilts, points, frac=1.0):
    """Union over the envelope, and how far the head must turn to see each point (degrees of pan
    plus tilt; inf where it cannot)."""
    seen = np.zeros(len(points), bool)
    effort = np.full(len(points), np.inf)
    for pan in pans:
        for tilt in tilts:
            pos, rot = head.site(fov["site"], lift, pan, tilt)
            v = visible(points, pos, rot, fov, frac)
            seen |= v
            effort[v] = np.minimum(effort[v], math.degrees(abs(pan)) + math.degrees(abs(tilt)))
    return seen, effort


# ---- the arms ------------------------------------------------------------------------------------
# ARCHITECT's live arm service (software/architect live_arms, #576) accepts a point for a gripper only
# if (1) it is inside that arm's workspace box, (2) ik.Kin solves a reach to it within ik.REACH_TOL_M,
# and (3) the straight move from stow keeps ik.CLEAR_MIN_MM from the robot's own body. reach() makes
# exactly those calls, as the Arms lane specified. The box is PER ROBOT, in
# /etc/opendubs/architect-arms.yaml (not in the repo): pass the robot's with --workspace, or the
# template below (live_arms.CONFIG_TEMPLATE, proposed from the twin on 2026-10-23) is used.
# Chassis frame = the CAD datum: x forward, y left, z up, metres.
WORKSPACE_TEMPLATE = {"left": {"x": [0.60, 0.80], "y": [0.15, 0.45], "z": [0.75, 1.10]},
                      "right": {"x": [0.60, 0.80], "y": [-0.45, -0.15], "z": [0.75, 1.10]}}
REACH_CELL = 0.05
HAND_SAMPLES = 20000


def load_workspace(path):
    if not path:
        return WORKSPACE_TEMPLATE, "the template (live_arms.CONFIG_TEMPLATE, 2026-10-23)"
    import yaml
    return yaml.safe_load(Path(path).read_text())["workspace"], path


class Reach:
    """ARCHITECT's reach gate (above), less the box, on the twin with the lift at `lift_mm` and the
    head centred: the box is applied separately so a plot can show what lies outside it."""

    def __init__(self, lift_mm: float):
        choreo = HERE.parent / "software" / "choreo"
        sys.path[:0] = [str(choreo), str(HERE.parent / "sim")]
        import clearance
        import cue
        import ik
        self.clearance, self.ik = clearance, ik
        self.kin = ik.Kin(cue.load_limits(str(choreo / "tests" / "joint_limits_fixture.yaml")))
        self.lift_mm = lift_mm
        m = self.kin.m
        self.qadr = {s: [m.jnt_qposadr[self.kin._jid(s, j)] for j in range(1, 8)] for s in ("left", "right")}

    def hand_cloud(self, side: str, n: int, seed: int = 0):
        """n hand points (chassis frame) from joint angles drawn inside ik.py's own bounds and shoulder
        rules, kinematics only. A PRE-SCREEN: a grid point no sample lands near is not offered to
        ok(), whose failures cost a full IK search each (about 0.8 s against 0.03 s for a success)."""
        kin, mj = self.kin, self.kin.mj
        rng = np.random.default_rng(seed)
        envs = kin._envelopes(kin._bounds(side))
        site = mj.mj_name2id(kin.m, mj.mjtObj.mjOBJ_SITE, self.ik.TOOL_SITE.format(side=side))
        self.clearance.set_pose(kin.m, kin.d, "", self.lift_mm, 0.0, 0.0)
        rest, pts = kin.d.qpos.copy(), np.zeros((n, 3))
        for k in range(n):
            kin.d.qpos[:] = rest
            for a, (lo, hi) in zip(self.qadr[side], envs[k % len(envs)]):
                kin.d.qpos[a] = rng.uniform(lo, hi)
            mj.mj_kinematics(kin.m, kin.d)
            pts[k] = kin.d.site_xpos[site]                     # the chassis sits at the origin, unrotated
        return pts

    def ok(self, side: str, point) -> bool:
        """point: chassis frame, metres. True if the arm reaches it from stow, clear of the body."""
        kin, np = self.kin, self.kin.np
        self.clearance.set_pose(kin.m, kin.d, "", self.lift_mm, 0.0, 0.0)      # stow: every joint 0
        sol, err, _ = kin.solve(side, "reach", kin.point([float(v) for v in point], side))
        if err > self.ik.REACH_TOL_M:
            return False
        joints = ",".join(f"{side}:{j}={sol[j]}" for j in range(1, 8))
        self.clearance.set_pose(kin.m, kin.d, joints, self.lift_mm, 0.0, 0.0)
        goal = np.array([kin.d.qpos[a] for a in self.qadr[side]])
        self.clearance.set_pose(kin.m, kin.d, "", self.lift_mm, 0.0, 0.0)
        return kin.clearance_along(side, np.zeros(7), goal)[0] >= self.ik.CLEAR_MIN_MM


def reach_grid(lift_mm: float, heights, cache: Path):
    """{(side, height above floor): set of (i, j)} on the REACH_CELL grid in base_link (xs, ys below),
    every point the gate passes (box not applied). Cached in `cache`, keyed by the inputs."""
    import hashlib
    import json
    xs = np.round(np.arange(0.0, 0.80 + 1e-9, REACH_CELL), 3)          # base_link x
    ys = np.round(np.arange(-0.75, 0.75 + 1e-9, REACH_CELL), 3)
    key = hashlib.sha256(json.dumps([lift_mm, list(heights), xs.tolist(), ys.tolist(),
                                     (OURS / "opendubs.xml").read_text()]).encode()).hexdigest()[:12]
    f = cache / f"reach-{key}.json"
    if f.exists():
        raw = json.loads(f.read_text())
        return xs, ys, {(s, float(h)): {tuple(c) for c in v} for s, h, v in raw}
    r = Reach(lift_mm)
    cloud = {s: r.hand_cloud(s, HAND_SAMPLES) for s in ("left", "right")}
    out = {}
    for h in heights:
        z = h - (-0.094)                                           # chassis frame: the floor is at -0.094
        for side in ("left", "right"):
            near = cloud[side][np.abs(cloud[side][:, 2] - z) <= REACH_CELL]
            cand = set()
            for x, y in zip(near[:, 0] - BASE_LINK_CAD[0], near[:, 1]):
                j0, i0 = int(round((x - xs[0]) / REACH_CELL)), int(round((y - ys[0]) / REACH_CELL))
                for di in (-1, 0, 1):
                    for dj in (-1, 0, 1):
                        if 0 <= i0 + di < len(ys) and 0 <= j0 + dj < len(xs):
                            cand.add((i0 + di, j0 + dj))
            cells = {(i, j) for i, j in sorted(cand) if r.ok(side, (xs[j] + BASE_LINK_CAD[0], ys[i], z))}
            out[(side, h)] = cells
            print(f"  reach: lift {lift_mm:.0f} mm, {h:.2f} m, {side}: {len(cells)} of {len(cand)} candidates pass", flush=True)
    f.write_text(json.dumps([[s, h, sorted(v)] for (s, h), v in out.items()]))
    return xs, ys, out


# ---- the planes, the zone and the pictures -------------------------------------------------------
CELL = 0.025                                    # the wide coverage plans
WELL = 0.6                                      # "seen well": the middle 60% of each half-angle
ROOM_AZ = np.radians(np.arange(-90, 91, 5))     # the room: around the front half, as the head looks around
ROOM_R = np.arange(1.0, 3.51, 0.25)             # m from base_link
ROOM_PEOPLE_H = (1.0, 1.4, 1.8)                 # m above the floor: a child's face to an adult's


def plane(xs, ys, h):
    X, Y = np.meshgrid(xs, ys)
    return X, Y, np.column_stack([X.ravel(), Y.ravel(), np.full(X.size, FLOOR_Z + h)])


def placeable(X, Y, gap=0.05):
    """Not over the robot: outside the chassis footprint (dimensions.yaml meshes.chassis) by `gap`."""
    c = BASE_LINK_CAD
    return ~((X > -0.032 - c[0] - gap) & (X < 0.612 - c[0] + gap) & (np.abs(Y) < 0.3245 + gap))


def in_box(X, Y, h, box):
    """The arm's workspace box (chassis frame) on the base_link grid at height h above the floor."""
    xc, z = X + BASE_LINK_CAD[0], h - 0.094
    return ((xc >= box["x"][0] - 1e-6) & (xc <= box["x"][1] + 1e-6) & (Y >= box["y"][0] - 1e-6)
            & (Y <= box["y"][1] + 1e-6) & (box["z"][0] - 1e-6 <= z <= box["z"][1] + 1e-6))


def depth_in(mask):
    """How many cells in from the mask's edge each cell lies (0 outside): a margin to rank by."""
    out, cur, k = np.zeros(mask.shape, int), mask.copy(), 0
    while cur.any():
        k += 1
        out[cur] = k
        nb = cur.copy()
        nb[1:, :] &= cur[:-1, :]; nb[:-1, :] &= cur[1:, :]; nb[:, 1:] &= cur[:, :-1]; nb[:, :-1] &= cur[:, 1:]
        cur = nb
    return out


def outline(ax):
    """The robot from above, in base_link: the chassis box and the lift plate."""
    from matplotlib.patches import Rectangle
    c = BASE_LINK_CAD
    ax.add_patch(Rectangle((-0.032 - c[0], -0.3245), 0.644, 0.649, fill=False, lw=1.2, ec="k"))
    ax.add_patch(Rectangle((0.148 - c[0], -0.16), 0.4, 0.32, fill=False, lw=0.8, ec="0.4"))
    ax.plot([0], [0], "k+", ms=8)


def zone_masks(head, reach, ws, h, env):
    """At height h (lift down): seen well, seen-well effort, seen at rest, each arm's reach, its box,
    placeable; all on the reach grid."""
    xs, ys, cells = reach
    X, Y, P = plane(xs, ys, h)
    seen, effort = (a.reshape(X.shape) for a in coverage(head, FOVS["depth_nfov"], 0.0, *env["gaze"], P, WELL))
    rest = coverage(head, FOVS["depth_nfov"], 0.0, [0.0], [0.0], P, WELL)[0].reshape(X.shape)
    arms = {}
    for side in ("left", "right"):
        m = np.zeros(X.shape, bool)
        for i, j in cells.get((side, h), ()):
            m[i, j] = True
        arms[side] = m
    boxes = {s: in_box(X, Y, h, ws[s]) for s in ("left", "right")}
    return X, Y, seen, effort, rest, arms, boxes, placeable(X, Y)


def best_spots(mount, head, reach, ws, heights, env):
    """Per height and arm, the deepest point of today's zone (inside the box), the head turning least."""
    xs, ys, _ = reach
    rows = []
    for h in heights:
        X, Y, seen, effort, rest, arms, boxes, ok = zone_masks(head, reach, ws, h, env)
        for side in ("left", "right"):
            zone = seen & arms[side] & ok & boxes[side]
            dep = depth_in(zone)
            if dep.max():
                best = np.argwhere(dep == dep.max())
                i, j = min(best, key=lambda ij: (effort[ij[0], ij[1]], abs(ys[ij[0]])))
                rows.append((mount, side, xs[j], ys[i], h, dep.max() * REACH_CELL, effort[i, j], bool(rest[i, j]),
                             int(zone.sum())))
    return rows


def zone_view(mount, head, reach, ws, heights, env, out, plt):
    """Zoomed in front of the robot, one panel per height, lift down."""
    hs = [h for h in heights if any(reach[2].get((s, h)) for s in ("left", "right"))]
    if not hs:
        return
    fig, axes = plt.subplots(1, len(hs), figsize=(3.3 * len(hs), 4.3), squeeze=False)
    for c, h in enumerate(hs):
        ax = axes[0][c]
        X, Y, seen, effort, rest, arms, boxes, ok = zone_masks(head, reach, ws, h, env)
        ax.contourf(X, Y, seen, levels=[0.5, 1.5], colors=["#cfe3f7"])
        if rest.any() and not rest.all():
            ax.contour(X, Y, rest, levels=[0.5], colors=["#1f5fa8"], linewidths=1.3)
        for side, col in (("left", "#2e8b57"), ("right", "#8a6d1f")):
            r = arms[side]
            ax.scatter(X[r], Y[r], s=9, marker="s", c=col, alpha=0.35, lw=0)
            z = seen & r & ok
            ax.scatter(X[z], Y[z], s=9, marker="s", c=col, lw=0)
            b = ws[side]
            ax.add_patch(__import__("matplotlib").patches.Rectangle(
                (b["x"][0] - BASE_LINK_CAD[0], b["y"][0]), b["x"][1] - b["x"][0], b["y"][1] - b["y"][0],
                fill=False, ec=col, lw=1.4, ls="--" if not (b["z"][0] <= h - 0.094 <= b["z"][1]) else "-"))
        for row in best_spots(mount, head, reach, ws, [h], env):
            ax.plot(row[2], row[3], marker="*", ms=12, mec="k", mfc="gold")
        outline(ax)
        ax.set_aspect("equal"); ax.set_xlim(-0.1, 0.85); ax.set_ylim(-0.8, 0.8)
        ax.set_title(f"{h:.2f} m above the floor", fontsize=9)
        ax.tick_params(labelsize=7); ax.grid(alpha=0.25)
    fig.suptitle(f"Bucket zone, {mount}, lift down; x forward / y left (m, base_link). Light blue: the depth camera "
                 f"sees it well somewhere in the gaze envelope; blue line: seen well with the head at rest. Squares: "
                 f"left (green) / right (brown) arm reaches (ARCHITECT's gate), solid where also seen and not over "
                 f"the robot. Rectangles: each arm's workspace box (dashed: this height is outside it). Star: best "
                 f"spot inside the box", fontsize=8, wrap=True)
    fig.tight_layout(rect=(0, 0, 1, 0.86))
    fig.savefig(out / f"zone-{mount}.png", dpi=110)
    plt.close(fig)


def plan_view(mount, head, reach, ws, env, lifts, table_h, out, plt):
    """The wide view: what the camera covers on the floor and at table height, at each lift."""
    from matplotlib.lines import Line2D
    from matplotlib.patches import Patch
    xs, ys = np.arange(-0.6, 3.6 + 1e-9, CELL), np.arange(-2.4, 2.4 + 1e-9, CELL)
    planes = [("floor", 0.0), ("table", table_h)]
    fig, axes = plt.subplots(len(planes), len(lifts), figsize=(6.0 * len(lifts), 6.0 * len(planes)), squeeze=False)
    for r, (pname, h) in enumerate(planes):
        X, Y, P = plane(xs, ys, h)
        for c, lift in enumerate(lifts):
            ax = axes[r][c]
            nfov = coverage(head, FOVS["depth_nfov"], lift, *env["gaze"], P)[0].reshape(X.shape)
            ax.contourf(X, Y, nfov, levels=[0.5, 1.5], colors=["#cfe3f7"])
            for key, envn, col, ls in (("depth_wfov", "gaze", "#1f5fa8", "--"), ("colour", "gaze", "#d9822b", "-"),
                                       ("depth_nfov", "rc", "0.35", ":")):
                msk = coverage(head, FOVS[key], lift, *env[envn], P)[0].reshape(X.shape)
                if msk.any() and not msk.all():
                    ax.contour(X, Y, msk, levels=[0.5], colors=[col], linestyles=[ls], linewidths=1.1)
            if lift == 0.0 and (("left", h) in reach[2] or ("right", h) in reach[2]):
                rx, ry, _ = reach
                RX, RY = np.meshgrid(rx, ry)
                for side in ("left", "right"):
                    for i, j in reach[2].get((side, h), ()):
                        ax.plot(RX[i, j], RY[i, j], "s", ms=2.5, color="#2e8b57" if side == "left" else "#8a6d1f")
            outline(ax)
            ax.set_aspect("equal"); ax.set_xlim(xs[0], xs[-1]); ax.set_ylim(ys[0], ys[-1])
            ax.set_xlabel("x forward (m, base_link)"); ax.set_ylabel("y left (m)")
            ax.set_title(f"{pname} ({h:.2f} m above the floor), lift {lift*1000:.0f} mm", fontsize=10)
            ax.grid(alpha=0.25)
    handles = [Patch(color="#cfe3f7", label="depth NFOV (0.5-3.86 m), gaze envelope: pan ±50°, tilt ±30°"),
               Line2D([], [], color="#1f5fa8", ls="--", label="depth WFOV (0.25-2.88 m), gaze"),
               Line2D([], [], color="#d9822b", label=f"colour, gaze (drawn to {COLOUR_FAR_M:.0f} m)"),
               Line2D([], [], color="0.35", ls=":", label="depth NFOV, RC envelope"),
               Line2D([], [], marker="s", ls="", color="#2e8b57", label="left arm reaches (lift down)"),
               Line2D([], [], marker="s", ls="", color="#8a6d1f", label="right arm reaches (lift down)")]
    fig.legend(handles=handles, loc="lower center", ncol=3, fontsize=8, frameon=False)
    fig.suptitle(f"What the head's Femto Bolt covers looking around: {mount}", fontsize=12)
    fig.tight_layout(rect=(0, 0.06, 1, 0.96))
    fig.savefig(out / f"plan-{mount}.png", dpi=100)
    plt.close(fig)


def side_view(mount, head, reach, env, lifts, out, plt):
    """From the robot's left, cut at y = 0: depth NFOV over the gaze tilt, the colour edge, the frustum
    at both tilt limits, and the heights the arms reach (lift down, any y), as squares."""
    from matplotlib.patches import Rectangle
    xs, zs = np.arange(-0.6, 3.6 + 1e-9, CELL), np.arange(FLOOR_Z, 2.4, CELL)
    X, Z = np.meshgrid(xs, zs)
    P = np.column_stack([X.ravel(), np.zeros(X.size), Z.ravel()])
    c = BASE_LINK_CAD
    fig, axes = plt.subplots(1, len(lifts), figsize=(6.4 * len(lifts), 4.8), squeeze=False)
    tilts = env["gaze"][1]
    for k, lift in enumerate(lifts):
        ax = axes[0][k]
        seen = coverage(head, FOVS["depth_nfov"], lift, [0.0], tilts, P)[0].reshape(X.shape)
        col = coverage(head, FOVS["colour"], lift, [0.0], tilts, P)[0].reshape(X.shape)
        ax.contourf(X, Z, seen, levels=[0.5, 1.5], colors=["#cfe3f7"])
        if col.any():
            ax.contour(X, Z, col, levels=[0.5], colors=["#d9822b"], linewidths=1.1)
        if lift == 0.0:
            rx = reach[0]
            for (side, h), cells in reach[2].items():
                for j in sorted({jj for _, jj in cells}):
                    ax.plot(rx[j], FLOOR_Z + h, "s", ms=3, color="#2e8b57" if side == "left" else "#8a6d1f", alpha=0.6)
        ax.add_patch(Rectangle((-0.032 - c[0], -0.048 - c[2]), 0.644, 0.404, fill=False, ec="k"))
        ax.add_patch(Rectangle((0.393 - c[0], 0.3575 + lift - c[2]), 0.06, 0.773, fill=False, ec="0.4"))
        for tilt, ls in ((tilts[0], "--"), (tilts[-1], "-")):
            p0, rot = head.site("camera_link", lift, 0.0, tilt)
            for a in (+1, -1):
                v = rot @ np.array([1.0, 0.0, a * math.tan(math.radians(FOVS["depth_nfov"]["v"] / 2))])
                v = v / np.linalg.norm(v)
                ax.plot([p0[0], p0[0] + 3.5 * v[0]], [p0[2], p0[2] + 3.5 * v[2]], color="#1f5fa8", lw=0.7, ls=ls)
        pos, _ = head.site("camera_link", lift, 0.0, 0.0)
        ax.plot(pos[0], pos[2], "o", color="#1f5fa8", ms=5)
        ax.axhline(FLOOR_Z, color="k", lw=1.5)
        ax.set_aspect("equal"); ax.set_xlim(xs[0], xs[-1]); ax.set_ylim(FLOOR_Z - 0.05, 2.4)
        ax.set_xlabel("x forward (m, base_link)"); ax.set_ylabel(f"z up (m, base_link; floor at {FLOOR_Z:.2f})")
        ax.set_title(f"lift {lift*1000:.0f} mm: depth NFOV over tilt {math.degrees(tilts[0]):+.0f}..{math.degrees(tilts[-1]):+.0f}° "
                     f"(+ down) at pan 0; dashed/solid: the frustum at the tilt limits", fontsize=8.5)
        ax.grid(alpha=0.25)
    fig.suptitle(f"Side view, {mount}: blue, depth NFOV seen at y = 0; orange, the colour camera's edge; squares, "
                 f"heights and reaches the arms pass ARCHITECT's gate at (lift down, any y)", fontsize=9)
    fig.tight_layout(rect=(0, 0, 1, 0.93))
    fig.savefig(out / f"side-{mount}.png", dpi=100)
    plt.close(fig)


# ---- the tilt sweep -------------------------------------------------------------------------------
def tilt_scores(head, reach, ws, heights, env):
    """(zone seen well, zone seen well with the head level, room people seen, room floor seen), each a
    fraction. The zone is every reachable spot not over the robot, at every height, box or not: the box
    is a per-robot software choice and the mount should not be chosen around it."""
    tot = well = level = 0
    level_env = (env["gaze"][0], [0.0])
    for h in heights:
        X, Y, seen, _, _, arms, _, ok = zone_masks(head, reach, ws, h, env)
        cand = (arms["left"] | arms["right"]) & ok
        _, _, P = plane(reach[0], reach[1], h)
        lv = coverage(head, FOVS["depth_nfov"], 0.0, *level_env, P, WELL)[0].reshape(X.shape)
        tot += int(cand.sum()); well += int((cand & seen).sum()); level += int((cand & lv).sum())
    A, R = np.meshgrid(ROOM_AZ, ROOM_R)
    xy = np.column_stack([(R * np.cos(A)).ravel(), (R * np.sin(A)).ravel()])
    people = np.vstack([np.column_stack([xy, np.full(len(xy), FLOOR_Z + h)]) for h in ROOM_PEOPLE_H])
    floor = np.column_stack([xy, np.full(len(xy), FLOOR_Z)])
    pe = coverage(head, FOVS["colour"], 0.0, *env["gaze"], people)[0].mean()
    fl = coverage(head, FOVS["depth_nfov"], 0.0, *env["gaze"], floor)[0].mean()
    return (well / tot if tot else 0.0, level / tot if tot else 0.0, float(pe), float(fl))


def sweep(reach, ws, heights, env, tilts, out, plt):
    rows = [(t, *tilt_scores(Head(tilt=t), reach, ws, heights, env)) for t in tilts]
    T = np.array(rows)
    best_room = T[:, 3:5].max(axis=0)
    ok = (T[:, 3] >= 0.9 * best_room[0]) & (T[:, 4] >= 0.9 * best_room[1])
    cand = T[ok] if ok.any() else T
    # the most of the zone seen well, then the most seen with the head level, then the most room
    rec = max(cand, key=lambda r: (round(r[1], 3), round(r[2], 3), r[3] + r[4]))
    fig, ax = plt.subplots(figsize=(7.5, 4.4))
    for k, (lab, col, ls) in enumerate((("bucket zone seen well (head may turn)", "#1b7f3b", "-"),
                                        ("bucket zone seen well with the head level", "#1b7f3b", "--"),
                                        ("people (1.0-1.8 m, 1-3.5 m away) seen, colour", "#d9822b", "-"),
                                        ("floor (1-3.5 m away) seen, depth", "#1f5fa8", "-")), start=1):
        ax.plot(T[:, 0], T[:, k] * 100, ls, color=col, marker="o", ms=3, label=lab)
    ax.axvline(rec[0], color="k", lw=0.8, ls=":")
    ax.text(rec[0], 2, f" recommended {rec[0]:.0f}°", fontsize=8)
    ax.axvline(30, color="0.6", lw=0.8)
    ax.text(30, 8, " the printed 30° mount", fontsize=7, color="0.4")
    ax.set_xlabel("Femto mount down-tilt (deg; 0 = the default mount)"); ax.set_ylabel("% covered")
    ax.set_ylim(0, 102); ax.grid(alpha=0.3); ax.legend(fontsize=7.5, loc="lower left")
    ax.set_title("Choosing the mount's tilt: the bucket zone against the room (lift down, gaze envelope)", fontsize=9.5)
    fig.tight_layout()
    fig.savefig(out / "tilt-sweep.png", dpi=110)
    plt.close(fig)
    return rows, rec


def write_table(spots, sweep_rows, rec, out, band, ws_src, heights):
    L = ["# Where to put the candy bucket, and what tilt to print the Femto mount at", "",
         f"Generated by `description/coverage.py`, lift down, head centred for the arms. Reach is ARCHITECT's "
         f"live gate (workspace box, ik.py reach within 1 cm, 30 mm clear from stow); the box is {ws_src}. "
         f"\"Seen well\" is the depth camera's middle {WELL:.0%} of field, somewhere in the gaze envelope "
         f"(pan ±50°, tilt ±30°; tilt band {band[0]:g},{band[1]:g} for RC). Positions in metres from base_link "
         "(x forward, y left), height above the floor. Margin: how far inside today's zone (box included) the "
         "spot is. Head turn: the least |pan| + |tilt| (deg) that sees it well.", ""]
    if sweep_rows:
        r = rec
        L += ["## The mount's tilt", "",
              f"**Recommended: {r[0]:.0f}° down.** It sees {r[1]:.0%} of the bucket zone well ({r[2]:.0%} with the "
              f"head level), and keeps {r[3]:.0%} of the people band and {r[4]:.0%} of the floor in view while "
              "looking around.", "",
              "| tilt (deg) | zone seen well | zone, head level | people seen | floor seen |", "|---|---|---|---|---|"]
        L += [f"| {t:.0f} | {a:.0%} | {b:.0%} | {c:.0%} | {d:.0%} |" for t, a, b, c, d in sweep_rows]
        L += ["", f"The zone: every spot an arm reaches at {', '.join(f'{h:.2f}' for h in heights)} m, not over the "
              "robot, box or not. People: colour camera; floor: depth NFOV. The tilts other than 0 and 30 are modelled "
              "by turning the default camera about the 30° mount's hinge line (TILT_PIVOT_EXPORT).", ""]
    L += ["## The best bucket spots (inside today's workspace box)", "",
          "| mount | arm | x (m) | y (m) | height (m) | margin (m) | head turn (deg) | seen at rest | zone cells |",
          "|---|---|---|---|---|---|---|---|---|"]
    for mount, side, x, y, h, margin, eff, rest, n in spots:
        L.append(f"| {mount} | {side} | {x:.2f} | {y:+.2f} | {h:.2f} | {margin:.2f} | {eff:.0f} | "
                 f"{'yes' if rest else 'no'} | {n} |")
    if not spots:
        L.append("| (none: nothing is both seen well and inside an arm's box at these heights) |||||||||")
    L += ["", "Camera: " + DOC + ". Occlusion by the robot itself (its face, its arms) is not modelled."]
    (out / "bucket-zone.md").write_text("\n".join(L) + "\n")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--out", required=True, help="a directory for the PNGs and the table (outside the repo)")
    ap.add_argument("--tilt-band", default="71,150",
                    help="the robot's commissioned tilt band in commanded degrees, as NECK reports it "
                         "(tilt_band=); 71,150 is opendubs2's (2026-10-15). Only the RC envelope uses it")
    ap.add_argument("--workspace", help="the robot's /etc/opendubs/architect-arms.yaml (copied off it); "
                                        "default: the template")
    ap.add_argument("--table-height", type=float, default=0.75, help="the table plane, m above the floor")
    ap.add_argument("--heights", default="0.7,0.75,0.8,0.9,1.0,1.1",
                    help="bucket heights above the floor (m) for the reach and the zone")
    ap.add_argument("--lifts", default="0,275", help="lift heights in mm for the coverage views (reach: lift down)")
    ap.add_argument("--mounts", default="straight,tilt30", help="the real mounts to draw")
    ap.add_argument("--sweep-tilt", default="0:45:5", help="down-tilts to score, start:stop:step degrees; '' for none")
    args = ap.parse_args()

    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import variants

    out = Path(args.out).expanduser()
    out.mkdir(parents=True, exist_ok=True)
    band = tuple(float(v) for v in args.tilt_band.split(","))
    env = envelopes(band)
    ws, ws_src = load_workspace(args.workspace)
    heights = sorted({float(v) for v in args.heights.split(",")} | {args.table_height})
    lifts = [float(v) / 1000.0 for v in args.lifts.split(",")]
    reach = reach_grid(0.0, heights, out)

    spots = []
    for mount in args.mounts.split(","):
        if mount not in variants.FEMTO_MOUNTS:
            sys.exit(f"no mount {mount!r}: one of {', '.join(variants.FEMTO_MOUNTS)}")
        head = Head(mount)
        plan_view(mount, head, reach, ws, env, lifts, args.table_height, out, plt)
        side_view(mount, head, reach, env, lifts, out, plt)
        zone_view(mount, head, reach, ws, heights, env, out, plt)
        spots += best_spots(mount, head, reach, ws, heights, env)
    sweep_rows, rec = [], None
    if args.sweep_tilt:
        a, b, st = (float(v) for v in args.sweep_tilt.split(":"))
        sweep_rows, rec = sweep(reach, ws, heights, env, list(np.arange(a, b + 1e-9, st)), out, plt)
        head = Head(tilt=rec[0])
        name = f"tilt{rec[0]:.0f}-modelled"
        zone_view(name, head, reach, ws, heights, env, out, plt)
        spots += best_spots(name, head, reach, ws, heights, env)
    write_table(spots, sweep_rows, rec, out, band, ws_src, heights)
    print(f"wrote {out}: plan-*, side-*, zone-*, tilt-sweep.png, bucket-zone.md")
    return 0


if __name__ == "__main__":
    sys.exit(main())
