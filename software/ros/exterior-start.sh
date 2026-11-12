#!/bin/bash
HERE=$(dirname "$(readlink -f "$0")")
# exterior-start.sh NAME: a bag-only segment (outdoors, ramps, a pass-through) through `floor-mapping.sh segment`:
# the same streams as a floor (colour as JPEG, not raw: raw ran at ~49 MB/s on 2026-11-11), plus the tablet's
# GPS and IMU. SLAM is left as it is (not restarted, not saved). The [ODO] text log runs beside it.
set -e
N=${1:-gates_ext}; B=/var/lib/opendubs/bags; D=$(date +%Y%m%d)
for u in opendubs-floor-map opendubs-ext-bag opendubs-odo-log; do systemctl is-active -q $u && { echo "$u still running"; exit 1; }; sudo -n systemctl reset-failed $u 2>/dev/null || true; done
sudo -n systemd-run --unit=opendubs-odo-log --property=StandardOutput=file:$B/$N-$D-odo.txt --property=StandardError=journal \
  python3 "$HERE/plc_listen.py" 14400 "[ODO],[PPCAL],[DRIVE],[SAFE],[8SERVO],[BMS],[PDH],[WDT],[SYS]"
sudo -n systemd-run --unit=opendubs-ext-bag --uid=emmar --gid=emmar \
  --property=KillSignal=SIGINT --property=TimeoutStopSec=120 --property=SendSIGKILL=no \
  --setenv=HOME=/home/emmar --setenv=OPENDUBS_COLOR_FROM_RAW=1 --setenv=OPENDUBS_BAG_DIR=$B \
  "$HERE/floor-mapping.sh" segment "$N"
sleep 15; journalctl -u opendubs-ext-bag --no-pager -n 6 -o cat | grep -v pam_unix
