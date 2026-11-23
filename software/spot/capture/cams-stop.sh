#!/bin/sh
# On the Mac: stop the camera recording on the Orin and show what it recorded.
#   software/spot/capture/cams-stop.sh
host="${SPOT_ORIN:-spot-orin-campus}"
ssh "$host" 'pkill -INT -f "[r]ecord_cameras.py" || { echo "no recording is running"; exit 0; }
  sleep 3; d=$(ls -td ~/captures/*/ | head -1); echo "$d"; cat "$d/summary.json" 2>/dev/null; du -sh "$d"; tail -2 "$d/recorder.log"'
