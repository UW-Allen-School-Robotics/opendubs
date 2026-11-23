#!/bin/sh
# On the Mac, in the separate Terminal app: start recording Spot's cameras and odometry on the Orin.
#   software/spot/capture/cams-start.sh CAPTURE_ID      # e.g. 2026-11-23_gates269_to_atrium
# It asks for Spot's login. Once it says "recording", you can close the window: it keeps going on the
# Orin. Stop it with cams-stop.sh. SPOT_ORIN names the ~/.ssh/config host (spot-orin-campus by default).
set -e
id=${1:?usage: cams-start.sh CAPTURE_ID}
host="${SPOT_ORIN:-spot-orin-campus}"
# The Orin's clock minus real time (the Mac's, NTP-synced), from the quickest of 3 round trips.
offset=$(python3 - "$host" <<'PY'
import subprocess, sys, time
best = None
for _ in range(3):
    a = time.time(); r = float(subprocess.check_output(["ssh", sys.argv[1], "date +%s.%N"])); b = time.time()
    if best is None or b - a < best[0]:
        best = (b - a, r - (a + b) / 2)
print(f"{best[1]:.3f}")
PY
)
echo "the Orin's clock is $offset s from the Mac's"
exec ssh -t "$host" "cd opendubs-spot && ~/.local/bin/uv run --no-project --python 3.12 --with 'bosdyn-client==5.1.4' \
    python capture/record_cameras.py '$id' --wall-offset $offset"
