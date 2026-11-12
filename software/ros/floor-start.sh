#!/bin/bash
HERE=$(dirname "$(readlink -f "$0")")
# floor-start.sh FRAME "START LANDMARK" [ROOM]: a fresh SLAM map + bag for one floor (the #634 tool), plus the [ODO] log.
set -e
F=$1; START=$2; ROOM=${3:+--room $3}; B=/var/lib/opendubs/bags; D=$(date +%Y%m%d)
[ -n "$F" ] && [ -n "$START" ] || { echo "usage: floor-start.sh FRAME START [ROOM]"; exit 2; }
[ -e /var/lib/opendubs/maps/site.yaml ] && { echo "site.yaml exists: refusing"; exit 1; }
for u in opendubs-floor-map opendubs-odo-log opendubs-ext-bag; do systemctl is-active -q $u && { echo "$u still running"; exit 1; }; sudo -n systemctl reset-failed $u 2>/dev/null || true; done
sudo -n systemd-run --unit=opendubs-odo-log --property=StandardOutput=file:$B/$F-$D-odo.txt --property=StandardError=journal \
  python3 "$HERE/plc_listen.py" 14400 "[ODO],[PPCAL],[DRIVE],[SAFE],[8SERVO],[BMS],[PDH],[WDT],[SYS]"
sudo -n systemd-run --unit=opendubs-floor-map --uid=emmar --gid=emmar \
  --property=KillSignal=SIGINT --property=TimeoutStopSec=600 --property=SendSIGKILL=no \
  --setenv=HOME=/home/emmar --setenv=OPENDUBS_COLOR_FROM_RAW=1 --setenv=OPENDUBS_BAG_DIR=$B \
  "$HERE/floor-mapping.sh" floor $F --operator team --start "$START" $ROOM --notes "drive-to-map, RC in MANUAL"
sleep 25; journalctl -u opendubs-floor-map --no-pager -n 15 -o cat | grep -v pam_unix
