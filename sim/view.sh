#!/bin/sh
# Interactive MuJoCo viewer.
#
# Two macOS-specific things, both learned the hard way:
#   * the viewer must own the main thread, so it runs under mjpython, not python;
#   * mujoco is PINNED to 3.3.5. On 3.14.0 the viewer dies constructing its window with
#     "RuntimeError: Caught an unknown exception!" — for any model, including a single sphere,
#     so it is the build and not this scene. Offscreen rendering is fine on either.
#
# Controls: drag to orbit, right-drag to pan, scroll to zoom, double-click a body to select,
# Ctrl+drag a selected body to push it. Space pauses. The left panel has the joint sliders.
exec ~/.local/bin/uv run --python 3.11 --with mujoco==3.3.5 --with numpy \
     mjpython "$(dirname "$0")/scene.py" --view "$@"
