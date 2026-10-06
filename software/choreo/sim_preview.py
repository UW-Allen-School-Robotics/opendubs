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

THREE ASSUMPTIONS, stated because the timeline does not settle them:
  * arms interpolate LINEARLY between keyframes (the format's definition; the real controller's
    profile is unverified — choreo-format-v1.md 5);
  * the lift's height is net up-time x LIFT_MM_PER_S, a PLACEHOLDER copied from the sim's own
    damping (sim/opendubs.xml), not a measurement of the real lift;
  * head degrees map as 90 = the sim's zero for both axes, and the SIGN of tilt is unverified —
    the show's 10..170 convention and the sim's asymmetric tilt range have never been reconciled.

`--dry-run` and `--clearance`'s sampling need only PyYAML. Rendering and clearance need mujoco and
the OpenArm assets, exactly as sim/scene.py does; this script has NOT been run against them in the
environment it was written in, so treat the render path as untested.
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

LIFT_MM_PER_S = 56.0      # PLACEHOLDER: the sim's damping-limited speed, not the robot's
LIFT_MAX_MM = 275.1       # measured stroke (sim/README)
HEAD_CENTRE_DEG = 90.0    # ASSUMED: the show's 90 is the sim's 0


def arm_pose(keyframes: list, t: float) -> dict:
    """Outward-sense pose at time t: linear from each keyframe's start to its t, then held."""
    prev = {str(j): 0.0 for j in range(1, 8)}
    for kf in keyframes:
        if t < kf["start"]:
            return prev
        if t <= kf["t"]:
            span = kf["t"] - kf["start"]
            f = (t - kf["start"]) / span if span > 0 else 1.0
            return {j: prev[j] + (kf["pose"][j] - prev[j]) * f for j in prev}
        prev = dict(kf["pose"])
    return prev


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


def head(events: list, t: float) -> tuple:
    pan = tilt = HEAD_CENTRE_DEG
    for e in events:
        if e["t"] > t + 1e-9:
            break
        pan = e["cue"].get("pan", pan)
        tilt = e["cue"].get("tilt", tilt)
    return pan, tilt


def sample(tl: dict, t: float) -> dict:
    tr = tl["tracks"]
    pan, tilt = head(tr.get("HEAD", []), t)
    return {
        "t": round(t, 4),
        "left": {j: round(v, 2) for j, v in arm_pose(tr.get("ARM_L", []), t).items()},
        "right": {j: round(v, 2) for j, v in arm_pose(tr.get("ARM_R", []), t).items()},
        "lift_mm": round(lift_mm(tr.get("LIFT", []), t), 1),
        "head_pan": pan, "head_tilt": tilt,
    }


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
        renderer = mujoco.Renderer(model, 760, 900)
        cam = mujoco.MjvCamera()
        mujoco.mjv_defaultCamera(cam)
        cam.azimuth, cam.elevation, cam.distance = 128.0, -10.0, 3.0
        cam.lookat[:] = [0.35, 0.0, 0.85]
    worst = (float("inf"), None)
    for i, s in enumerate(frames(tl, a.fps)):
        clearance.set_pose(model, data, joints_arg(s), s["lift_mm"],
                           s["head_pan"] - HEAD_CENTRE_DEG, s["head_tilt"] - HEAD_CENTRE_DEG)
        if a.clearance:
            c = clearance.min_clearance(model, data)
            if c < worst[0]:
                worst = (c, s["t"])
            print(f"t={s['t']:7.2f}  closest {c:7.1f} mm")
        if renderer is not None:
            renderer.update_scene(data, camera=cam)
            Image.fromarray(renderer.render()).save(os.path.join(a.frames, f"f{i:05d}.png"))
    if a.clearance:
        print(f"closest approach {worst[0]:.1f} mm at t={worst[1]} s "
              "(the sim is ~6 deg optimistic near the head; the validator's constant governs)")
    if a.frames:
        print(f"wrote frames to {a.frames}; e.g. ffmpeg -r {a.fps:g} -i {a.frames}/f%05d.png out.mp4")


if __name__ == "__main__":
    main()
