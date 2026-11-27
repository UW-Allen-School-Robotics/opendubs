#!/bin/sh
# On the Orin: start the Spot player on the payload network. Asks for Spot's login first (kept in memory).
#   ~/opendubs-spot/orin/run-player.sh [--fake] [--walking] [--live] [more spot_player.py options]
# With --live, /live listens on port 8100 on this Orin only and on the payload network (live.py).
# Normally started from the Mac with orin/start.sh, which also brings the operator page to the Mac.
cd "$(dirname "$0")/.." || exit 1
exec "$HOME/.local/bin/uv" run --no-project --python 3.12 --with 'bosdyn-client==5.1.4' --with fastapi \
    --with 'uvicorn[standard]' python spot_player.py --listen 192.168.50.5 --port 8099 \
    --live-listen 127.0.0.1 --live-listen 192.168.50.5 "$@"
