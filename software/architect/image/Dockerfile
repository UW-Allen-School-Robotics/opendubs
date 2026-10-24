# OpenDubs ARCHITECT: the program writer and the demo runner (software/architect) on the Jetson
# AGX Thor (JetPack 7.1, L4T R38.4). Built and run the way archit-perception is: tools/dev.sh, under
# the archit account, nothing on the host changed.
# Build context is software/architect:
#   docker build -f image/Dockerfile -t archit-architect:dev software/architect
# The same base as archit-perception, so the two share its layers: NVIDIA Isaac ROS 4.4 for r38.4,
# Ubuntu 24.04 + ROS 2 Jazzy. Nothing here uses the GPU: the twin's IK and clearance sweep run on
# the CPU, and the model is an API call.
# At a desk (x86): --build-arg BASE_IMAGE=<ROS_BASE_IMAGE in software/stack/versions.env>.
ARG BASE_IMAGE=nvcr.io/nvidia/isaac/ros@sha256:d7bca990d0d29eb38b47bb60ef6155fa361f2be29ad587b5f44ae7df5fee525f
FROM ${BASE_IMAGE}
USER root
ARG DEBIAN_FRONTEND=noninteractive
RUN apt-get update && apt-get install -y --no-install-recommends python3-pip python3-yaml \
    && rm -rf /var/lib/apt/lists/*
ENV PIP_BREAK_SYSTEM_PACKAGES=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_CONSTRAINT=/etc/pip-constraints.txt
COPY image/constraints.txt /etc/pip-constraints.txt
# mujoco: the twin (software/choreo/ik.py, sim/clearance.py). The rest is what ssa needs.
RUN pip install mujoco "numpy<2" anthropic python-dotenv rich mcap mcap-ros2-support
ENV RMW_IMPLEMENTATION=rmw_fastrtps_cpp
# The /robot/ service types, for the live backend's client and the arm node.
COPY ros/robot_api_interfaces /opt/architect_ws/src/robot_api_interfaces
RUN bash -c "source /opt/ros/jazzy/setup.bash && cd /opt/architect_ws && colcon build --event-handlers console_direct-" \
 && rm -rf /opt/architect_ws/build /opt/architect_ws/log
# The code is not in the image: dev.sh mounts this repository at /work and ssa at /ssa, read-only.
COPY image/entrypoint.sh /opt/architect/entrypoint.sh
WORKDIR /work
ENTRYPOINT ["/opt/architect/entrypoint.sh"]
CMD ["bash"]
