#!/bin/sh
# Interactive MuJoCo viewer:  ./view.sh
#
# Controls: drag to orbit, right-drag to pan, scroll to zoom, double-click a body to select it,
# ctrl+drag a selected body to push it, space to pause. The left panel has joint sliders --
# zaxis, head_pan, head_tilt and all 18 arm joints.
#
# NOT mjpython, despite what MuJoCo's own docs say for macOS. mjpython is broken on this
# machine: mujoco.viewer._MJPYTHON is set, so the trampoline works, but constructing the
# viewer then dies with "RuntimeError: Caught an unknown exception!" -- on 3.14.0 and on
# 3.3.5, for any model including a single sphere, from a sandboxed shell, the app's terminal
# and Terminal.app alike. Under plain python the same construction succeeds.
#
# It works because this is the BLOCKING viewer, which wants the main thread and, run this way,
# has it. mjpython exists for launch_passive, where your script keeps running alongside the
# viewer -- so if we ever want a script to drive the viewer live, that problem comes back.
#
# glfw is explicit: MuJoCo's simulate dlopens it at runtime and fails with "cannot obtain a
# shared object handle" if the package is absent.
exec ~/.local/bin/uv run --python 3.11 --with mujoco --with glfw --with numpy \
     python "$(dirname "$0")/scene.py" --view "$@"
