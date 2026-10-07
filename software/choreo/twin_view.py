#!/usr/bin/env python3
"""The body twin: a native MuJoCo window that follows the composer's playhead (workplan 2.2).

    ./twin.sh                                   # with the composer already running on :8091
    ./twin.sh --composer http://127.0.0.1:8091 --assets /path/to/openarm_assets

Start the composer, open a show, then run this. Play, pause, scrub or edit in the composer and
the window follows. Orbit, pan and zoom in the window as usual.

HOW IT STAYS IN STEP. The composer publishes only the playhead's time and whether it is playing
(`GET /api/twin`), plus the resolved timeline whenever the show changes (`/api/twin/timeline`).
This viewer evaluates that timeline at the playhead itself — with the same sampling as
`sim_preview.py`, so the window and an offline preview cannot disagree — and extrapolates on its
own clock between updates. Seeking is free and network delay cannot accumulate.

WHAT IT CAN CLAIM. Exactly what `sim_preview.py` can: geometry. Arms interpolate linearly between
keyframes; the lift's speed is a placeholder; head tilt's sign is unverified. See that file's
docstring. It never talks to the robot: its only connection is to the composer, on localhost.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(REPO, "sim"))

import sim_preview  # noqa: E402  the sampling the offline preview uses, reused not copied

POLL_S = 0.05        # how often to ask the composer where the playhead is
FPS = 60.0


def get(base: str, path: str) -> dict:
    with urllib.request.urlopen(base.rstrip("/") + path, timeout=0.5) as r:
        return json.loads(r.read())


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--composer", default="http://127.0.0.1:8091")
    ap.add_argument("--assets", help="an openarm_assets checkout instead of description/openarm")
    ap.add_argument("--log", action="store_true", help="print the followed state once a second")
    a = ap.parse_args()

    import clearance
    import mujoco
    import mujoco.viewer
    import scene

    model = scene.build(a.assets)
    data = mujoco.MjData(model)
    clearance.set_pose(model, data)

    rev, tl, path = -1, None, None
    t_base, playing, clock_base = 0.0, False, time.monotonic()
    online, last_poll, last_log = False, 0.0, 0.0

    with mujoco.viewer.launch_passive(model, data) as v:
        v.cam.azimuth, v.cam.elevation, v.cam.distance = 128.0, -10.0, 3.0
        v.cam.lookat[:] = [0.35, 0.0, 0.85]
        while v.is_running():
            now = time.monotonic()
            if now - last_poll >= POLL_S:
                last_poll = now
                try:
                    s = get(a.composer, "/api/twin")
                    if s["rev"] != rev:
                        d = get(a.composer, "/api/twin/timeline")
                        rev, tl, path = d["rev"], d["timeline"], d["path"]
                    playing = bool(s["playing"])
                    t_base = s["t"] + ((s["now"] - s["at"]) if playing else 0.0)
                    clock_base, online = now, True
                except (urllib.error.URLError, OSError, ValueError, KeyError):
                    online, playing = False, False      # hold the last pose; say so

            t = t_base + ((time.monotonic() - clock_base) if playing else 0.0)
            if tl and tl.get("tracks") is not None:
                smp = sim_preview.sample(tl, t)
                clearance.set_pose(model, data, sim_preview.joints_arg(smp), smp["lift_mm"],
                                   smp["head_pan"] - sim_preview.HEAD_CENTRE_DEG,
                                   smp["head_tilt"] - sim_preview.HEAD_CENTRE_DEG)
                sim_preview.apply_base(model, data, smp["base"])

            if a.log and now - last_log >= 1.0 and tl:
                last_log = now
                print(f"t={t:7.2f} {'PLAYING' if playing else 'paused '} online={online} rev={rev} "
                      f"lift={smp['lift_mm']:5.1f}mm R2={smp['right'].get('2', 0):6.1f} "
                      f"head={smp['head_pan']:.0f}/{smp['head_tilt']:.0f} "
                      f"base=({smp['base'][0]:+.2f},{smp['base'][1]:+.2f},{smp['base'][2]:+.0f}deg)", flush=True)
            # The declared dance floor, drawn as a flat disc around the base's start (plan 2.4).
            r = ((tl or {}).get("dance") or {}).get("floor_radius_m")
            try:
                with v.lock():
                    if r:
                        mujoco.mjv_initGeom(v.user_scn.geoms[0], mujoco.mjtGeom.mjGEOM_CYLINDER,
                                            [float(r), 0.0005, 0.0], [0.324, 0.0, 0.001],
                                            [1, 0, 0, 0, 1, 0, 0, 0, 1], [0.1, 0.8, 0.7, 0.18])
                        v.user_scn.ngeom = 1
                    else:
                        v.user_scn.ngeom = 0
            except (AttributeError, TypeError, IndexError):
                pass                                  # an older viewer: no floor drawn
            if hasattr(v, "set_texts"):
                state = ("PLAYING" if playing else "paused") if online else "composer not reachable"
                left = f"{path or 'no show'}   {t:7.2f} s   {state}"
                right = "PREVIEW ONLY — geometry, not timing"
                v.set_texts((mujoco.mjtFontScale.mjFONTSCALE_150, mujoco.mjtGridPos.mjGRID_TOPLEFT,
                             left, right))
            v.sync()
            time.sleep(max(0.0, 1.0 / FPS - (time.monotonic() - now)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
