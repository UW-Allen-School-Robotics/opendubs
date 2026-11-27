#!/bin/sh
# On the Mac, in the separate Terminal app: log the AprilTags Spot sees, on the Orin, read-only (no
# lease, no E-Stop, no commands). It asks for Spot's login on this terminal. See log_fiducials.py.
#   software/spot/capture/tags-log.sh NAME [SECONDS]      # 60 s by default; Ctrl-C ends it early
set -e
name=${1:?usage: tags-log.sh NAME [SECONDS]}
exec ssh -t "${SPOT_ORIN:-spot-orin-campus}" "cd opendubs-spot && ~/.local/bin/uv run --no-project --python 3.12 \
    --with 'bosdyn-client==5.1.4' python capture/log_fiducials.py '$name' --seconds ${2:-60}"
