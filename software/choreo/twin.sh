#!/bin/sh
# The body twin: a native MuJoCo window that follows the composer's playhead.
# Start the composer first (composer/server.py), open a show, then run this.
#
# mjpython, not python: on macOS a script-driven (passive) MuJoCo viewer must run under it.
exec ~/.local/bin/uv run --python 3.11 --with mujoco --with glfw --with numpy --with pyyaml \
     mjpython "$(dirname "$0")/twin_view.py" "$@"
