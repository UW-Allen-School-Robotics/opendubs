# The Femto Bolt's ROS 2 driver (OrbbecSDK_ROS2), on the same Isaac ROS base as the perception image.
# Run by tools/camera.sh. Build context is software/perception:
#   docker build -f image/camera.Dockerfile -t archit-perception-camera:dev software/perception

# NVIDIA Isaac ROS 4.4 for r38.4: Ubuntu 24.04 + ROS 2 Jazzy (as image/Dockerfile).
FROM nvcr.io/nvidia/isaac/ros@sha256:d7bca990d0d29eb38b47bb60ef6155fa361f2be29ad587b5f44ae7df5fee525f

USER root
ARG DEBIAN_FRONTEND=noninteractive

# The driver's build dependencies, from its README.
RUN apt-get update && apt-get install -y --no-install-recommends \
      git libgflags-dev nlohmann-json3-dev libdw-dev libssl-dev libgoogle-glog-dev libusb-1.0-0-dev \
      ros-jazzy-image-transport ros-jazzy-image-transport-plugins ros-jazzy-compressed-image-transport \
      ros-jazzy-image-publisher ros-jazzy-camera-info-manager ros-jazzy-diagnostic-updater \
      ros-jazzy-diagnostic-msgs ros-jazzy-statistics-msgs ros-jazzy-xacro ros-jazzy-backward-ros \
    && rm -rf /var/lib/apt/lists/*

# v2.10.6 (2026-09-30), the latest release; it lists the Femto Bolt with firmware 1.1.3.
ARG ORBBEC_ROS2_TAG=v2.10.6
RUN git clone --depth 1 -b ${ORBBEC_ROS2_TAG} https://github.com/orbbec/OrbbecSDK_ROS2.git /opt/orbbec_ws/src/OrbbecSDK_ROS2 \
 && bash -c "source /opt/ros/jazzy/setup.bash && cd /opt/orbbec_ws \
      && colcon build --event-handlers console_direct- --cmake-args -DCMAKE_BUILD_TYPE=Release" \
 && rm -rf /opt/orbbec_ws/build /opt/orbbec_ws/log

ENV RMW_IMPLEMENTATION=rmw_fastrtps_cpp
COPY image/camera_entrypoint.sh /opt/camera/entrypoint.sh
ENTRYPOINT ["/opt/camera/entrypoint.sh"]
CMD ["bash"]
