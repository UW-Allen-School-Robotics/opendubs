#!/usr/bin/env bash
# Record one attended Spot walk on the Orin into a HallPass capture folder. It only records: it sends
# Spot no commands and asks for no password. spot_ros2 and the Ouster driver must already be running,
# and the Ouster driver must stay up until this has stopped (its /ouster/metadata is latched and is
# needed to replay the packets).
#
#   bash record-walk.sh DATA_ROOT CAPTURE_ID     # e.g. bash record-walk.sh /data/captures 2026-11-20_gates_roof
#   Ctrl-C ends it; it then writes SHA256SUMS.
#
# Records everything at full rate into one MCAP (or the default storage if MCAP isn't installed):
# odometry, TF, joint states, power and state, the 5 body cameras (grey and depth) with camera_info,
# and the Ouster's raw packets, IMU and metadata. Splitting it into light/, lidar/ and heavy/ happens
# off the robot, afterwards. Topic names allow one leading namespace (e.g. /spot/odometry).
set -u
ROOT=${1:?usage: record-walk.sh DATA_ROOT CAPTURE_ID}
ID=${2:?usage: record-walk.sh DATA_ROOT CAPTURE_ID}
DIR="$ROOT/$ID"
[ -e "$DIR/bag" ] && { echo "$DIR/bag already exists: choose another CAPTURE_ID"; exit 1; }
mkdir -p "$DIR"

NS='(/[A-Za-z0-9_]+)?'
CAMS='(frontleft|frontright|left|right|back)'
REGEX="^${NS}/(odometry|joint_states|status/.*|(camera|depth)/${CAMS}/(image|camera_info))\$|^/tf(_static)?\$|^${NS}/ouster/(lidar_packets|imu_packets|metadata|imu)\$"

STORAGE=()
ros2 pkg prefix rosbag2_storage_mcap >/dev/null 2>&1 && STORAGE=(-s mcap)

echo "Free space under $ROOT:"; df -h "$ROOT" | tail -1
echo "Orin clock (UTC): $(date -u +%FT%T.%NZ)"
ros2 topic list | grep -qE '/ouster/metadata$' || echo "WARNING: no /ouster/metadata topic: is the Ouster driver up?"
echo "Recording into $DIR/bag. Ctrl-C to stop."

trap 'true' INT        # let ros2 bag finish its file on Ctrl-C, then carry on to the checksums
ros2 bag record "${STORAGE[@]}" -o "$DIR/bag" -e "$REGEX"
trap - INT

touch "$DIR/marks.txt"
( cd "$DIR" && find . -type f ! -name SHA256SUMS -print0 | sort -z | xargs -0 sha256sum > SHA256SUMS )
echo "Done: $DIR"; du -sh "$DIR"
ros2 bag info "$DIR/bag" 2>/dev/null | grep -E 'Duration|Messages|Topic:' | head -40
