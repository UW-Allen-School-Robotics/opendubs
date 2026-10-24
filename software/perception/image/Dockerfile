# OpenDubs perception: CLIPSeg + YOLO-World + NanoSAM on Jetson AGX Thor (JetPack 7.1, L4T R38.4).
# Design: notes/design/perception-shared-models.md. Build and run: notes/workplan-perception.md.
# Build context is software/perception:
#   docker build -f image/Dockerfile -t archit-perception:dev software/perception

# NVIDIA Isaac ROS 4.4 for r38.4: Ubuntu 24.04 + ROS 2 Jazzy, no CUDA.
# Tag isaac_ros_28556f8bc78a98822bd08b2d7c6fcf9b-arm64-jetpack, created 2026-04-30.
FROM nvcr.io/nvidia/isaac/ros@sha256:d7bca990d0d29eb38b47bb60ef6155fa361f2be29ad587b5f44ae7df5fee525f

USER root
ARG DEBIAN_FRONTEND=noninteractive

# On JetPack 7 the NVIDIA runtime mounts only devices and the driver, so TensorRT goes in the
# image, pinned to the host's version (dpkg -l on opendubs2, 2026-10-22).
COPY image/nvidia-r38.4.pref /etc/apt/preferences.d/nvidia-r38.4.pref
RUN apt-get update && apt-get install -y --no-install-recommends \
      cuda-cudart-13-0=13.0.48-1 \
      libcublas-13-0=13.0.0.19-1 \
      tensorrt-libs=10.13.3.9-1+cuda13.0 \
      libnvinfer-bin=10.13.3.9-1+cuda13.0 \
      python3-libnvinfer=10.13.3.9-1+cuda13.0 \
      python3-pip git wget \
    && rm -rf /var/lib/apt/lists/*

ENV PIP_BREAK_SYSTEM_PACKAGES=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_CONSTRAINT=/etc/pip-constraints.txt
COPY image/constraints.txt /etc/pip-constraints.txt

RUN pip install torch torchvision "numpy<2" \
 && pip install transformers ultralytics opencv-python-headless onnx onnxscript gdown pyyaml timm websockets \
 && pip install git+https://github.com/ultralytics/CLIP.git \
 && pip install git+https://github.com/NVIDIA-AI-IOT/torch2trt.git

# NanoSAM (NVIDIA) and HOUND's perception_models, both pinned. Both go on PYTHONPATH: nanosam/
# has no __init__.py, so `pip install -e` finds no packages and installs an empty mapping.
RUN git clone https://github.com/NVIDIA-AI-IOT/nanosam.git /opt/nanosam \
 && git -C /opt/nanosam checkout 6536336 \
 && git clone https://github.com/sidtalia/perception_models.git /opt/perception_models \
 && git -C /opt/perception_models checkout 2cd9a68
ENV PYTHONPATH=/opt/nanosam:/opt/perception_models

# ultralytics pulls in the desktop opencv-python, which needs libGL.
RUN apt-get update && apt-get install -y --no-install-recommends libgl1 libglib2.0-0t64 \
    && rm -rf /var/lib/apt/lists/*

# The NVIDIA runtime injects the driver only when these are set. NVIDIA's CUDA images set them;
# the Isaac ROS base does not.
ENV NVIDIA_VISIBLE_DEVICES=all \
    NVIDIA_DRIVER_CAPABILITIES=all
ENV RMW_IMPLEMENTATION=rmw_fastrtps_cpp

# The Segment action and Instance message.
COPY perception_msgs /opt/perception_ws/src/perception_msgs
RUN bash -c "source /opt/ros/jazzy/setup.bash && cd /opt/perception_ws && colcon build --event-handlers console_direct-" \
 && rm -rf /opt/perception_ws/build /opt/perception_ws/log
COPY image/entrypoint.sh /opt/perception/entrypoint.sh

WORKDIR /work
ENTRYPOINT ["/opt/perception/entrypoint.sh"]
CMD ["bash"]
