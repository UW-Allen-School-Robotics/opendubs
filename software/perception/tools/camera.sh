#!/usr/bin/env bash
# Run the Femto Bolt's driver on opendubs2 under the archit account (notes/workplan-perception.md).
# One container, archit-perception-camera, publishing /camera/... on the robot's graph (domain 0),
# so any node on the robot can read it. Only one process can open the camera: stop any other
# driver first. The perception server (dev.sh up) reads it over shared memory, without the UDP
# losses of this host (net.core.rmem_max is the 208 KB default).
#
# Reading it from another user's node:  export FASTDDS_BUILTIN_TRANSPORTS=UDPv4  (see below)
#
#   camera.sh up        start the driver
#   camera.sh status    rates of colour and aligned depth, and the colour calibration
#   camera.sh logs      follow the driver log
#   camera.sh down      stop and remove the container
#
# Colour is undistorted and depth aligned to it in software (the Femto Bolt has no hardware
# alignment), so both share one pinhole calibration. With colour left distorted, depth sat 3-4 px
# off the colour edges.
set -euo pipefail

NAME=archit-perception-camera
IMAGE=archit-perception-camera:dev
DOCKER="sudo -n docker"
LAUNCH_ARGS=(depth_registration:=true align_mode:=SW enable_color_undistortion:=true)

case "${1:-}" in
  up)
    # USB devices are character major 189; the container runs as root, so no host udev rules.
    # The Femto Bolt computes depth on the host in an OpenGL depth engine: without the NVIDIA
    # runtime it falls back to Mesa's software renderer and takes ~9 cores (130 % with the GPU).
    # --ipc shareable: the server joins this container's shared memory (dev.sh up) and reads the
    # frames there. Not the host's: Fast DDS shared memory does not work between users anyway
    # (its files are 0644), and a participant that dies holding a lock there leaves every later
    # root participant hanging at creation. Every other node, on the host or in another container,
    # must read over UDP: FASTDDS_BUILTIN_TRANSPORTS=UDPv4 in its environment, or it receives
    # nothing (Fast DDS sees the same host and insists on shared memory it cannot open).
    ${DOCKER} run -d --rm --name "${NAME}" --runtime nvidia \
      -e NVIDIA_VISIBLE_DEVICES=all -e NVIDIA_DRIVER_CAPABILITIES=all --network host --ipc shareable \
      -v /dev/bus/usb:/dev/bus/usb --device-cgroup-rule='c 189:* rmw' \
      -e ROS_DOMAIN_ID=0 -e ROS_AUTOMATIC_DISCOVERY_RANGE=SUBNET \
      "${IMAGE}" ros2 launch orbbec_camera femto_bolt.launch.py "${LAUNCH_ARGS[@]}" ${CAMERA_ARGS:-}
    echo "started ${NAME}; 'camera.sh logs' to follow"
    ;;
  status)
    ${DOCKER} exec "${NAME}" /opt/camera/entrypoint.sh bash -c \
      "timeout 6 ros2 topic hz /camera/color/image_raw & timeout 6 ros2 topic hz /camera/depth/image_raw & wait" \
      | grep -E "average rate" || true
    ${DOCKER} exec "${NAME}" /opt/camera/entrypoint.sh \
      timeout 10 ros2 topic echo --once --field d /camera/color/camera_info || true
    ;;
  logs)
    ${DOCKER} logs -f "${NAME}"
    ;;
  down)
    ${DOCKER} stop -t 5 "${NAME}" >/dev/null && echo "stopped ${NAME}"
    ;;
  *)
    sed -n '2,13p' "$0"; exit 1
    ;;
esac
