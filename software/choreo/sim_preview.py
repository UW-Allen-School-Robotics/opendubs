#!/usr/bin/env python3
"""Step the MuJoCo model through a show's resolved timeline: the simulator as the output sink.

    python3 sim_preview.py shows/beat-demo.yaml --limits <...> --dry-run          # sampled poses
    python3 sim_preview.py shows/beat-demo.yaml --limits <...> --clearance        # + closest approach
    python3 sim_preview.py shows/beat-demo.yaml --limits <...> --frames /tmp/bd   # PNG per frame

Workplan proposal 4 / 5.4: the editor's DEFAULT output is the simulator, and live output is a
separately armed binding that does not exist yet. This is the first half. It consumes exactly what
the runner will consume — `cue.py`'s resolved timeline — so a show previewed here and a show run
later cannot mean different things.

WHAT THIS CAN CLAIM. Geometry only, inherited from sim/: whether a pose collides and how close it
comes. NOT timing, sag, force or tipping — the sim's dynamics are placeholders, and this script
does not use them at all: every frame is placed kinematically (mj_forward), from the timeline.
The clearance it reports is the sim's, which is SIX DEGREES OPTIMISTIC near the head (sim/README);
the validator's measured constant stays in charge.

THREE ASSUMPTIONS, stated because the timeline does not settle them, and one fact:
  * arms follow motion.py's smoothed path between keyframes — the one the robot is sent (since
    2026-10-08); how closely the real controller tracks it was measured by speed_test.py (plan 6.1);
  * the lift's height is net up-time x LIFT_MM_PER_S, MEASURED on opendubs2 (2026-10-13): a show's
    4 s `up` took the platform from ~0 to ~45 mm (the dashboard's mast_h_mm, ToF, +/-10 mm). That is
    an average over the move; the sensor showed almost nothing for the first ~2 s and the rest
    after, so a long climb may run faster than this. One measurement, near the bottom of the
    stroke: re-measure over a long climb. (It replaces a 56 mm/s placeholder from the sim's own
    damping, five times too fast: the twin showed 224 mm where the robot rose 45.)
  * head degrees map as 90 = the sim's zero for both axes, and the SIGN of tilt is unverified —
    the show's 10..170 convention and the sim's asymmetric tilt range have never been reconciled;
  * FACT, not assumption: the head turns toward each target at 25 deg/s per axis, the Tab5
    firmware's own slew limit (HEAD_SLEW_DPS), so the preview shows where it IS, not its target.

`--dry-run` needs only PyYAML. Rendering and clearance need mujoco; the robot model, arms included,
is in description/ (sim/scene.py loads it). Both paths were first run on 2026-10-07 (workplan 2.3).

`--clearance` reports the closest approach the SHOW CREATES — pairs it brings closer than they are
at rest, named — plus any contact at all, and the rest-pose floor separately: at rest the hands
hang ~39 mm from the arm frame, which would otherwise be every show's "closest approach".
"""
from __future__ import annotations

import argparse
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, HERE)

import cue  # noqa: E402
import motion  # noqa: E402

LIFT_MM_PER_S = 11.25     # MEASURED on opendubs2 (2026-10-13): a 4 s up rose ~45 mm (see above)
LIFT_MAX_MM = 275.1       # measured stroke (sim/README)
HEAD_CENTRE_DEG = 90.0    # ASSUMED: the show's 90 is the sim's 0
# The head turns toward each target at this rate, each axis independently: the Tab5 firmware's
# own slew limit (kSlewDps in firmware/tab5_idf/app_face/pantilt.cpp), which no caller can
# override. The servos themselves are faster, so the firmware is what governs.
HEAD_SLEW_DPS = cue.HEAD_SLEW_DPS


def arm_pose(keyframes: list, t: float) -> dict:
    """Outward-sense pose at time t, along the robot's own smoothed path (motion.py): the twin shows
    exactly what the arm is sent."""
    if not keyframes:
        return {str(j): 0.0 for j in range(1, 8)}
    p = motion.Path(motion.knots(keyframes, "pose")).at(t)
    return {str(j): p[j - 1] for j in range(1, 8)}


# The grippers (plan item 3, 2026-10-10). A cue sets a TARGET — `do: open` (1), `do: close` (0) or
# `open: 0..1` — and the fingers travel there at a finite speed. GRIP_STROKE_S is ASSUMED, not
# measured: half a second for a full stroke, until the Arms lane measures the real controller. The
# fingers start CLOSED (0) — the twin's rest — since nothing yet says where the real ones begin.
GRIP_STROKE_S = 0.5


def grip_open(events: list, t: float) -> float:
    """How open (0 closed .. 1 open) a gripper is at t: moving toward each cue's target from where
    it was, at 1 / GRIP_STROKE_S per second."""
    pos, t_prev, target = 0.0, 0.0, 0.0
    for e in sorted(events, key=lambda x: x["t"]):
        if e["t"] > t:
            break
        step = (e["t"] - t_prev) / GRIP_STROKE_S
        pos = target if abs(target - pos) <= step else pos + step * (1 if target > pos else -1)
        c = e["cue"]
        target = 1.0 if c.get("do") == "open" else 0.0 if c.get("do") == "close" else float(c.get("open", target))
        t_prev = e["t"]
    step = (t - t_prev) / GRIP_STROKE_S
    return round(target if abs(target - pos) <= step else pos + step * (1 if target > pos else -1), 4)


def lift_mm(events: list, t: float) -> float:
    h = 0.0
    for e in events:
        if e["start"] > t:
            break
        secs = float(e["cue"].get("secs") or 0.0)
        run = max(0.0, min(t, e["start"] + secs) - e["start"])
        if e["cue"].get("do") == "up":
            h = min(LIFT_MAX_MM, h + run * LIFT_MM_PER_S)
        elif e["cue"].get("do") == "down":
            h = max(0.0, h - run * LIFT_MM_PER_S)
    return h


def _slew(cur: float, tgt: float, dt: float) -> float:
    step = HEAD_SLEW_DPS * max(0.0, dt)
    return tgt if abs(tgt - cur) <= step else cur + (step if tgt > cur else -step)


def head(events: list, t: float) -> tuple:
    """Where the head IS at t, not where it was told to go: from centre, each axis moves toward
    its latest target at HEAD_SLEW_DPS, exactly as the firmware's slew limit does."""
    pan = tilt = tgt_pan = tgt_tilt = HEAD_CENTRE_DEG
    last = 0.0
    for e in events:
        if e["t"] > t + 1e-9:
            break
        pan, tilt = _slew(pan, tgt_pan, e["t"] - last), _slew(tilt, tgt_tilt, e["t"] - last)
        last = e["t"]
        tgt_pan = e["cue"].get("pan", tgt_pan)
        tgt_tilt = e["cue"].get("tilt", tgt_tilt)
    return _slew(pan, tgt_pan, t - last), _slew(tilt, tgt_tilt, t - last)


def base_pose(events: list, t: float, drive: list | None = None) -> tuple:
    """(x m, y m, yaw deg) of the base at t, in the robot's starting frame: the DRIVE moves made by t
    (twin-only; cue.drive_path), then a DANCE move in progress on top, from wherever the base is.

    Every library dance returns the base to where it started, so only the move in progress
    displaces it. Open loop and at the PLACEHOLDER speeds in cue.py (+x forward, +y to the robot's
    left, assumed); this is how a show reads, not where the real robot would end up."""
    import math
    bx = by = byaw = 0.0
    if drive:
        pts = cue.drive_path([(e["start"], e["cue"]) for e in drive])
        for pt in pts:
            if pt[0] > t + 1e-9:
                break
            _, bx, by, byaw = pt
    dx, dy, dyaw = _dance_pose(events, t)
    c, s_ = math.cos(byaw), math.sin(byaw)
    return bx + dx * c - dy * s_, by + dx * s_ + dy * c, math.degrees(byaw) + dyaw


def _dance_pose(events: list, t: float) -> tuple:
    """(x, y, yaw deg) a DANCE move in progress adds, in the base's frame at the move's start."""
    import math
    for e in events:
        c = e["cue"]
        if c.get("move") not in cue.DANCE_MOVES:
            continue
        secs = float(c.get("secs", cue.DANCE_DEFAULT_SECS))
        if not e["start"] <= t < e["start"] + secs:
            continue
        amp = float(c.get("amp", 1.0))
        v = cue.AUTO_MAX_UNIT * amp * cue.BASE_M_PER_S_FULL
        w = cue.AUTO_MAX_UNIT * amp * cue.BASE_RAD_PER_S_FULL
        x = y = yaw = 0.0
        left = t - e["start"]
        for frac, fwd, strafe, rot in cue.DANCE_MOVES[c["move"]]:
            dt = min(frac * secs, left)
            if dt <= 0:
                break
            x += (fwd * math.cos(yaw) - strafe * math.sin(yaw)) * v * dt
            y += (fwd * math.sin(yaw) + strafe * math.cos(yaw)) * v * dt
            yaw += rot * w * dt
            left -= dt
        return x, y, math.degrees(yaw)
    return 0.0, 0.0, 0.0


FINGER_JOINT = "oa_openarm_{side}_finger_joint{k}"     # the vendored arm model's two slides per hand


def apply_grips(model, data, smp: dict) -> None:
    """Open the twin's fingers to each side's `grip_*` fraction (after clearance.set_pose): both
    slides of a hand at fraction x their travel (0..44 mm in the vendored model)."""
    import mujoco
    for side in ("left", "right"):
        frac = float(smp.get(f"grip_{side}", 0.0))
        for k in (1, 2):
            j = mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_JOINT, FINGER_JOINT.format(side=side, k=k))
            if j >= 0:
                data.qpos[model.jnt_qposadr[j]] = frac * model.jnt_range[j][1]
    mujoco.mj_forward(model, data)


def apply_base(model, data, base: tuple) -> None:
    """Place the chassis freejoint at a base pose (after clearance.set_pose, before mj_forward)."""
    import math
    import mujoco
    j = mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_JOINT, "base")
    if j < 0:
        return
    a = model.jnt_qposadr[j]
    x, y, yaw = base
    # A mecanum base turns about the centre of its wheels; the chassis body's origin is at its rear
    # edge, so rotate about the wheel centroid (wheel centres x 0.12 and 0.528 m, opendubs.xml).
    cx, th = (0.12 + 0.528) / 2.0, math.radians(yaw)
    data.qpos[a] += x + cx - cx * math.cos(th)
    data.qpos[a + 1] += y - cx * math.sin(th)
    h = th / 2.0
    data.qpos[a + 3:a + 7] = [math.cos(h), 0.0, 0.0, math.sin(h)]
    mujoco.mj_forward(model, data)


def sample(tl: dict, t: float) -> dict:
    tr = tl["tracks"]
    pan, tilt = head(tr.get("HEAD", []), t)
    return {
        "t": round(t, 4),
        "left": {j: round(v, 2) for j, v in arm_pose(tr.get("ARM_L", []), t).items()},
        "right": {j: round(v, 2) for j, v in arm_pose(tr.get("ARM_R", []), t).items()},
        "lift_mm": round(lift_mm(tr.get("LIFT", []), t), 1),
        "grip_left": grip_open(tr.get("GRIP_L", []), t),
        "grip_right": grip_open(tr.get("GRIP_R", []), t),
        "head_pan": pan, "head_tilt": tilt,
        "base": tuple(round(v, 4) for v in base_pose(tr.get("DANCE", []), t, tr.get("DRIVE", []))),
        # a crawling hand, and tools other than today's gripper, only in a show that has them (effectors.py)
        **({"crawler": crawler_state(tl, t)} if tl.get("crawler") else {}),
        **({"tools": tool_channels(tl, t)} if tl.get("tools") else {}),
    }


# ---- the crawling hand (twin only) ---------------------------------------------------------------------
FLOOR_Z = -0.094               # description/opendubs/dimensions.yaml: datum.floor_z_mm
HAND_CENTRE_M = 0.09           # wrist (the palm's origin) to the hand's centre, along its heading
_CYCLES: dict = {}


def _cycles(tool) -> dict:
    """The tool's recorded gait cycles (effectors/<type>.gaits.json, from the gait model), or {}."""
    import effectors
    key = tool.doc.get("_mirror_of") or tool.id
    if key not in _CYCLES:
        p = os.path.join(effectors.REG_DIR, f"{key}.gaits.json")
        _CYCLES[key] = json.load(open(p)).get("cycles", {}) if os.path.isfile(p) else {}
    return _CYCLES[key]


def crawler_state(tl: dict, t: float) -> dict:
    """The hand at show time t: on the validated path (nominal sim speeds), its body moving as the recorded
    gait cycle moves it, its fingers from the cycle, or a finger dance; standing with nothing to do, its
    idle pattern on the beat (it never freezes). Not attached to the base: a DANCE moves the leash's
    anchor, never the hand. Also where that anchor is. Twin only."""
    import math
    import effectors
    cr = tl["crawler"]
    tool = effectors.registry()[cr["tool"]]
    beat_s = (tl.get("music") or {}).get("beat_s") or 0.5
    legs = [dict(leg, **{"from": tuple(leg["from"]), "to": tuple(leg["to"])}) for leg in cr["legs"]]
    st = effectors.state_at(legs, tuple(cr["start"]), t)
    tr = tl["tracks"]
    base = base_pose(tr.get("DANCE", []), t, tr.get("DRIVE", []))
    x, y, h = st["x"], st["y"], st["heading"]
    chans = tool.rest()
    z, roll, pitch = 0.05, 0.0, 0.0
    cyc = None
    if st["moving"]:
        name = ("gait:" + st["moving"]) if not st["moving"].startswith("pivot_") else "pivot:" + st["moving"][6:]
        if tool.mirrored and name.startswith("pivot:"):           # the mirror's left pivot is the right hand's right
            name = "pivot:" + ("right" if name.endswith("left") else "left")
        cyc = _cycles(tool).get(name)
    if not cyc and st["moving"] and not st["moving"].startswith("pivot_"):
        g = (tool.crawl.get("gaits") or {}).get(st["moving"]) or {}
        if g.get("sim_params"):                       # no recorded cycle: the gait's own formula
            chans.update(effectors.gait_channels(tool, g["sim_params"], st["phase_s"]))
            z = 0.055
    if cyc:
        rows = cyc["rows"]
        row = rows[int((st["phase_s"] % cyc["period_s"]) / cyc["period_s"] * len(rows)) % len(rows)]
        z, roll, pitch = row[0], row[1], row[2]
        chans.update(dict(zip(cyc["fields"][3:], row[3:])))
        if tool.mirrored:
            roll = -roll
    elif st["pattern"] and st["pattern"] in tool.patterns:
        chans.update(effectors.pattern_channels(tool, st["pattern"], st["pattern_t"], beat_s))
    elif not st["moving"] and (tool.crawl or {}).get("idle") in tool.patterns:   # standing: the idle pattern, on the beat
        off = (tl.get("music") or {}).get("offset_s") or 0.0
        chans.update(effectors.pattern_channels(tool, tool.crawl["idle"], max(0.0, t - off), beat_s))
    ax, ay = effectors.base_to_world(base, cr["anchor"][0], cr["anchor"][1])
    hr = math.radians(h)
    return {"tool": tool.id, "x": round(x, 4), "y": round(y, 4), "heading": round(h, 2), "z": z, "roll": roll, "pitch": pitch,
            "wrist": (round(x - HAND_CENTRE_M * math.cos(hr), 4), round(y - HAND_CENTRE_M * math.sin(hr), 4)),
            "channels": {k: (round(v, 3) if isinstance(v, float) else v) for k, v in chans.items()},
            "anchor": (round(ax, 4), round(ay, 4), round(FLOOR_Z + cr["anchor"][2], 4)),
            "length_m": cr["length_m"], "moving": st["moving"], "pattern": st["pattern"]}


def tool_channels(tl: dict, t: float) -> dict:
    """{side: {"tool", "channels"}} for each flange tool other than today's gripper, at show time t: each
    GRIP cue's channel targets, approached at the channels' own speed from where they were."""
    import effectors
    reg, out = effectors.registry(), {}
    for slot, side in (("GRIP_L", "left"), ("GRIP_R", "right")):
        fit = (tl.get("tools") or {}).get(side)
        if not fit or fit.get("type") == "openarm_gripper" or fit.get("mode") != "equipped":
            continue
        tool = reg[fit["type"]]
        cur = tool.rest()
        evs = [e for e in tl["tracks"].get(slot, []) if "channels" in e and e["t"] <= t]
        for i, e in enumerate(evs):                    # toward each target until the next cue (or now)
            cur = _slew_channels(tool, cur, e["channels"], (evs[i + 1]["t"] if i + 1 < len(evs) else t) - e["t"])
        out[side] = {"tool": tool.id, "channels": cur}
    return out


def _slew_channels(tool, cur: dict, target: dict, dt: float) -> dict:
    out = dict(cur)
    for ch, v in target.items():
        a = out.get(ch, v)
        if isinstance(v, str) or isinstance(a, str):
            out[ch] = v if dt >= tool.stroke_s(ch) else a
            continue
        step = max(0.0, dt) / tool.stroke_s(ch)
        out[ch] = v if abs(v - a) <= step else a + (step if v > a else -step)
    return out


def apply_tools(model, data, smp: dict) -> None:
    """Pose the flange tools' channels (after the robot's pose)."""
    import mujoco
    import effectors
    import hand
    for side, st in (smp.get("tools") or {}).items():
        hand.channel_qpos(model, effectors.registry()[st["tool"]], st["channels"], data, f"oa_{hand.PREFIX}{side}_")
    mujoco.mj_forward(model, data)


def apply_crawler(model, data, smp: dict) -> None:
    """Pose the crawling hand and draw its leash (after the robot's pose, before rendering)."""
    import math
    import mujoco
    import effectors
    import hand
    c = smp.get("crawler")
    if not c:
        return
    wx, wy = c["wrist"]
    hand.place_crawler(model, data, wx, wy, c["heading"], FLOOR_Z + c["z"], c["roll"], c["pitch"])
    hand.channel_qpos(model, effectors.registry()[c["tool"]], c["channels"], data)
    mujoco.mj_forward(model, data)
    hr = math.radians(c["heading"])
    back = (wx - 0.075 * math.cos(hr), wy - 0.075 * math.sin(hr), FLOOR_Z + c["z"] - 0.01)   # the puck's leash ring
    hand.leash_update(model, data, c["anchor"], back, c["length_m"], FLOOR_Z)
    mujoco.mj_forward(model, data)


def frames(tl: dict, fps: float):
    n = int(tl["duration_s"] * fps) + 1
    for i in range(n):
        yield sample(tl, i / fps)


def joints_arg(s: dict) -> str:
    """The `--joints` string sim/scene.py understands: degrees, each arm's own sense."""
    return ",".join(f"{side}:{j}={v}" for side in ("left", "right") for j, v in s[side].items())


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("show")
    ap.add_argument("--limits", default=cue.DEFAULT_LIMITS)
    ap.add_argument("--fps", type=float, default=10.0)
    ap.add_argument("--dry-run", action="store_true", help="print sampled states, touch no sim")
    ap.add_argument("--clearance", action="store_true", help="report the closest approach per frame")
    ap.add_argument("--frames", metavar="DIR", help="render a PNG per frame into DIR")
    ap.add_argument("--assets", help="OpenArm assets (see sim/scene.py)")
    ap.add_argument("--allow-invalid", action="store_true")
    a = ap.parse_args()

    tl = cue.load_show(a.show).export(cue.load_limits(a.limits), a.limits)
    if not tl["valid"] and not a.allow_invalid:
        for p in tl["problems"]:
            print("FAIL:", p["msg"], file=sys.stderr)
        sys.exit("refusing to preview an invalid show (pass --allow-invalid to look at it anyway)")

    if a.dry_run:
        for s in frames(tl, a.fps):
            print(json.dumps(s, sort_keys=True))
        return

    sys.path.insert(0, os.path.join(REPO, "sim"))
    import mujoco  # noqa: F401  (fails here, clearly, if the sim stack is not installed)
    import clearance
    import scene

    model = scene.build(a.assets)
    data = mujoco.MjData(model)
    renderer = None
    if a.frames:
        os.makedirs(a.frames, exist_ok=True)
        from PIL import Image
        model.vis.headlight.ambient[:] = [0.55] * 3     # the same lighting as sim/scene.py --render
        model.vis.headlight.diffuse[:] = [0.8] * 3
        renderer = mujoco.Renderer(model, 760, 900)
        cam = mujoco.MjvCamera()
        mujoco.mjv_defaultCamera(cam)
        cam.azimuth, cam.elevation, cam.distance = 128.0, -10.0, 3.0
        cam.lookat[:] = [0.35, 0.0, 0.85]
    # A pair whose distance the show never changes is structure, not choreography: at rest the hands
    # hang ~39 mm from the arm frame, and that floor would otherwise be "the closest approach" of
    # every show and hide the number an author wants. So: the closest pair the show brings CLOSER
    # than it is at rest, named; and any contact at all, whatever caused it.
    base = {}
    if a.clearance:
        bd = mujoco.MjData(model)
        clearance.set_pose(model, bd)
        base = {(g1, g2): c for c, g1, g2 in clearance.clearances(model, bd)}
    worst = (float("inf"), None, None)
    for i, s in enumerate(frames(tl, a.fps)):
        clearance.set_pose(model, data, joints_arg(s), s["lift_mm"],
                           s["head_pan"] - HEAD_CENTRE_DEG, s["head_tilt"] - HEAD_CENTRE_DEG)
        apply_base(model, data, s["base"])
        apply_grips(model, data, s)
        if a.clearance:
            rows = clearance.clearances(model, data)
            moving = [(c, g1, g2) for c, g1, g2 in rows if c < base.get((g1, g2), c) - 0.05]
            touching = [(c, g1, g2) for c, g1, g2 in rows if c <= 0.0]
            c, g1, g2 = (moving or [(float("inf"), "-", "-")])[0]
            if c < worst[0]:
                worst = (c, s["t"], f"{g1} <-> {g2}")
            print(f"t={s['t']:7.2f}  closest approach {c:7.1f} mm  {g1} <-> {g2}"
                  + (f"   *** CONTACT: {touching[0][1]} <-> {touching[0][2]} ***" if touching else ""))
        if renderer is not None:
            renderer.update_scene(data, camera=cam)
            Image.fromarray(renderer.render()).save(os.path.join(a.frames, f"f{i:05d}.png"))
    if a.clearance:
        floor = min(base.items(), key=lambda kv: kv[1]) if base else None
        print(f"closest approach the show creates: {worst[0]:.1f} mm at t={worst[1]} s, {worst[2]} "
              "(the sim is ~6 deg optimistic near the head; the validator's constant governs)")
        if floor:
            print(f"rest-pose floor, unchanged by any show: {floor[1]:.1f} mm, "
                  f"{floor[0][0]} <-> {floor[0][1]}")
    if a.frames:
        print(f"wrote frames to {a.frames}; e.g. ffmpeg -r {a.fps:g} -i {a.frames}/f%05d.png out.mp4")


if __name__ == "__main__":
    main()
