#!/bin/sh
# Interactive MuJoCo viewer:  ./view.sh
#
# Controls: drag to orbit, right-drag to pan, scroll to zoom, double-click a body to select it,
# ctrl+drag a selected body to push it, space to pause. The left panel has joint sliders --
# zaxis, head_pan, head_tilt and all 18 arm joints.
#
# This is the BLOCKING viewer (sliders, orbit, push), which owns the main thread and so runs under
# plain python. A script that drives the viewer live -- the composer's playhead, for one -- needs
# mujoco.viewer.launch_passive, and on macOS that must run under `mjpython` instead of `python`.
# Both work on the development Macs: verified 2026-10-07 with MuJoCo 3.15.0, the passive viewer
# driving the full robot model (lift and an arm animated from a script) at ~40 fps.
#
# glfw is explicit: MuJoCo's simulate dlopens it at runtime and fails with "cannot obtain a
# shared object handle" if the package is absent.
exec ~/.local/bin/uv run --python 3.11 --with mujoco --with glfw --with numpy \
     python "$(dirname "$0")/scene.py" --view "$@"
