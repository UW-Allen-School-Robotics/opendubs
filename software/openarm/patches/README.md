# Patches to the OpenArm teleop stack

Local changes to `EricJGoossen/openarm`, which lives at `/openarm_ws/src/openarm` **inside the
`openarm` container** and is not ours. They are kept here so a container rebuild does not silently
lose them, the same way the Astra driver's Jazzy ports are carried in `ros/astra-jazzy-build.sh`.

## Why these are scripts, not `.patch` files

**The copy in the container has drifted from the upstream author's published repo**, so a positional patch would
apply to neither reliably. Measured 2026-10-02 against `EricJGoossen/openarm` HEAD:

| | GitHub HEAD | the container |
|---|---|---|
| `target_hz` | **250.0** | **150.0** |
| `ws_port` | 8765 | 8766 |
| `http_port` | 8443 | 8444 |

plus 8 further hunks in `webxr_teleop.py`. The container also has **no `.git`** — the directory was
baked into the image without it — so `git diff` cannot produce a patch there at all.

These scripts therefore use **anchored replacements**: each edit asserts its anchor matches exactly
once, reports edits already applied, and refuses rather than guessing. That survives drift, and it
is idempotent, so re-running after a rebuild is safe.

## `teleop-optional-viewer.py`

Makes the MuJoCo/viser viewer optional, default on. `OPENARM_TELEOP_VIEWER=0` runs headless.

**Why.** The viewer was the dominant cost in the teleop control loop. Profiled with `py-spy` on
opendubs2: **viser in 67% of MainThread samples**, while the loop missed **~23% of its 150 Hz
deadlines** (worst overrun +9 ms against a 6.7 ms budget) and warned that *"tracking is coarser/less
responsive at this rate"*. Headless: **zero overrun warnings in 40 s.**

The cost is **not** `viewer.sync()`, which is already rate-limited to `rendered_fps`. It is the
`TeleopPanel`s — they are registered into `PhysicsEventLoop` and so update **every tick**. Turning
off the viewer therefore has to drop the panels too, which is why this is not a one-line change.

It also needs a new session-lifetime condition: `run_teleop` looped on
`while rig.viewer.is_running()`, so the viewer *was* the session's lifetime. Headless, a
`threading.Event` plus a `SIGTERM` handler takes over, so the `finally:` cleanup still runs.

**Note the inference worth testing:** the container's `target_hz` is 150 against upstream's 250.
Lowering the rate is the symptomatic workaround for this exact problem, and it still did not fit at
150. With the viewer off, 250 may now be sustainable.

```sh
sudo docker cp software/openarm/patches/teleop-optional-viewer.py openarm:/tmp/
sudo docker exec openarm python3 /tmp/teleop-optional-viewer.py
```

**Verify by liveness, not by compiling.** An earlier version of this patch stranded
`self.zero_button = ...` after a `return`, which is legal Python: `py_compile` passed, the process
printed its startup banner, then died on the first tick with `AttributeError`. Check that the
process is still alive and that 8444/8766 are listening a good 20 s after launch, and that 8082 is
*not*.

## Upstreaming

Worth offering upstream, but it must be rebased onto the upstream HEAD — the drift above means these
anchors may not exist in the current upstream tree.
