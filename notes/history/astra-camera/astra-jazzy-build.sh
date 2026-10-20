#!/usr/bin/env bash
# Build the ORIGINAL Orbbec Astra (USB 2bc5:0401) ROS 2 driver on Jazzy / aarch64.
# Run ON the Thor.
#
# Why this exists: the modern OrbbecSDK_ROS2 does NOT support the original Astra
# (it reports "0 devices" though the camera is on the bus). The original Astra is
# an OpenNI2-era device; the legacy orbbec/ros2_astra_camera driver supports it but
# was written for Humble/Iron and does NOT compile on Jazzy / GCC-13 unmodified.
# This script clones it and applies the four mechanical port patches, then builds.
#
# (Stopgap camera. The likely FINAL camera is a newer Orbbec that DOES work with
# OrbbecSDK_ROS2 — also cloned into ~/orbbec_ws/src — or a RealSense.)
set -e
WS="${OPENDUBS_CAMERA_WS:-$HOME/orbbec_ws}"
source /opt/ros/jazzy/setup.bash

echo "== deps =="
sudo apt-get update -qq
sudo apt-get install -y -qq python3-colcon-common-extensions python3-rosdep \
  libuvc-dev libuvc0 libgflags-dev nlohmann-json3-dev libgoogle-glog-dev libusb-1.0-0-dev
sudo rosdep init 2>/dev/null || true
rosdep update

mkdir -p "$WS/src" && cd "$WS/src"
[ -d ros2_astra_camera ] || git clone --depth 1 https://github.com/orbbec/ros2_astra_camera.git
D="$WS/src/ros2_astra_camera/astra_camera"

echo "== Jazzy / GCC-13 port patches =="
# 1. rclcpp param-callback type was renamed (OnParametersSet -> OnSetParameters).
sed -i 's/OnParametersSetCallbackType/OnSetParametersCallbackType/g' \
  "$D/include/astra_camera/ros_param_backend.h" "$D/src/ros_param_backend.cpp"
# 2. cv_bridge header .h -> .hpp (Jazzy).
grep -rln '<cv_bridge/cv_bridge.h>' "$D/src" "$D/include" 2>/dev/null \
  | xargs -r sed -i 's#<cv_bridge/cv_bridge.h>#<cv_bridge/cv_bridge.hpp>#g'
# 3. image_geometry header .h -> .hpp (Jazzy).
grep -rln '<image_geometry/pinhole_camera_model.h>' "$D/src" "$D/include" 2>/dev/null \
  | xargs -r sed -i 's#<image_geometry/pinhole_camera_model.h>#<image_geometry/pinhole_camera_model.hpp>#g'
# 4. GCC-13 dropped transitive includes -> constants.h needs explicit <cstdint>.
grep -q '#include <cstdint>' "$D/include/astra_camera/constants.h" \
  || sed -i 's/#pragma once/#pragma once\n#include <cstdint>/' "$D/include/astra_camera/constants.h"

echo "== udev (non-root USB access) =="
RULES=$(find "$WS/src" -name '*obsensor*rules' -o -name '*orbbec*rules' 2>/dev/null | head -1)
[ -n "$RULES" ] && sudo cp "$RULES" /etc/udev/rules.d/99-obsensor-libusb.rules
sudo udevadm control --reload-rules && sudo udevadm trigger

echo "== build =="
cd "$WS"
rosdep install --from-paths src/ros2_astra_camera --ignore-src -r -y
colcon build --symlink-install --packages-up-to astra_camera

echo "== done. Launch: source $WS/install/setup.bash && ros2 launch astra_camera astra.launch.xml =="
