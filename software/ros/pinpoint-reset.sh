#!/bin/bash
# pinpoint-reset.sh: PPCAL RESET with the robot STILL (zeroes the pose, re-measures the IMU zero offset), then
# 30 s of [ODO] to read the heading drift. Run before each floor's mapping (docs/build/pinpoint.md).
HERE=$(dirname "$(readlink -f "$0")")
# PPCAL RESET (robot still), then 30 s of [ODO] to read the drift.
IP=$(getent hosts opendubs-stamplc.local | awk '{print $1}' | head -1); [ -n "$IP" ] || { echo "no stamplc name"; exit 1; }
sudo -n python3 "$HERE/plc_listen.py" 3 "[PPCAL]" "PPCAL RESET" "$IP"
sleep 2
T=$(mktemp); sudo -n python3 "$HERE/plc_listen.py" 30 "[ODO]" > "$T"
python3 - "$T" <<'PY'
import re
import sys
L=[l for l in open(sys.argv[1]) if "[ODO]" in l]
g=lambda l,k: float(re.search(rf" {k}=(-?[0-9.]+)", l).group(1))
a,b=L[0],L[-1]
print(f"{len(L)} lines; first: x={g(a,'x')} y={g(a,'y')} h={g(a,'h')} ex={g(a,'ex'):.0f} ey={g(a,'ey'):.0f}")
print(f"last : x={g(b,'x')} y={g(b,'y')} h={g(b,'h')} ex={g(b,'ex'):.0f} ey={g(b,'ey'):.0f}")
import math
print(f"heading change over the window: {math.degrees(g(b,'h')-g(a,'h')):.3f} deg; rdy/badread last: {re.search(r'rdy=\S+',b).group(0)} {re.search(r'badread=\S+',b).group(0)}")
PY
rm -f "$T"
