#!/bin/bash
HERE=$(dirname "$(readlink -f "$0")")
# exterior-start.sh NAME: bag-only exterior / pass-through segment. SLAM is left as it is (not restarted, not saved).
set -e
N=${1:-gates_ext}-$(date +%Y%m%d); B=/var/lib/opendubs/bags
[ -e $B/$N ] && { echo "$B/$N exists"; exit 1; }
for u in opendubs-floor-map opendubs-ext-bag opendubs-odo-log; do systemctl is-active -q $u && { echo "$u still running"; exit 1; }; sudo -n systemctl reset-failed $u 2>/dev/null || true; done
sudo -n systemd-run --unit=opendubs-odo-log --property=StandardOutput=file:$B/$N-odo.txt --property=StandardError=journal \
  python3 "$HERE/plc_listen.py" 14400 "[ODO],[PPCAL]"
sudo -n systemd-run --unit=opendubs-ext-bag --uid=emmar --gid=emmar --property=KillSignal=SIGINT --property=TimeoutStopSec=120 \
  --setenv=HOME=/home/emmar --property=EnvironmentFile=/etc/opendubs/ros-dds.env \
  bash -c "source /opt/ros/jazzy/setup.bash; exec ros2 bag record -s mcap --storage-preset-profile zstd_fast --max-bag-size 2147483648 -o $B/$N \
    /scan /scan_rear /scan_filtered /odom /tf /tf_static /head/joint_states /head/joint_targets /head/joint_limits \
    /camera/color/image_raw /camera/color/camera_info /camera/depth/image_raw/compressedDepth /camera/depth/camera_info /camera/gyro_accel/sample \
    /tablet/fix /tablet/imu /power/bus_voltage"
sleep 10; journalctl -u opendubs-ext-bag --no-pager -o cat | grep -cE "Subscribed to topic" | sed 's/^/topics subscribed: /'
