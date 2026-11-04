# OpenDubs ARCHITECT: the program writer, the demo runner and its listener (software/architect) on
# the Jetson AGX Thor (JetPack 7.1, L4T R38.4). Built and run the way archit-perception is:
# tools/dev.sh, under the archit account, nothing on the host changed.
# Build context is software/architect, plus the voice service's directory as a second context, for
# its speech requirements and its GPL check, and the arm service's interfaces as a third (BuildKit;
# dev.sh build passes them):
#   docker build -f image/Dockerfile -t archit-architect:dev \
#     --build-arg BASE_IMAGE=archit-perception:dev --build-context voice=software/voice \
#     --build-context arms=software/openarm/interfaces software/architect
# dev.sh builds FROM archit-perception:dev (software/perception/image/Dockerfile on
# archit/perception-grasp), so Kokoro uses perception's PyTorch rather than a second copy, and the
# two images share its layers. Its PyTorch stack is kept exactly as it is (perception's code needs
# transformers 4.x). The default below, the plain Isaac ROS base, still builds: PyTorch is then
# installed for Kokoro, on the CPU.
# At a desk (x86): --build-arg BASE_IMAGE=<ROS_BASE_IMAGE in software/stack/versions.env>.
ARG BASE_IMAGE=nvcr.io/nvidia/isaac/ros@sha256:d7bca990d0d29eb38b47bb60ef6155fa361f2be29ad587b5f44ae7df5fee525f
FROM ${BASE_IMAGE}
USER root
ARG DEBIAN_FRONTEND=noninteractive
# libasound2t64: the listener opens the Jabra through it (opendubs_architect/alsa.py; LGPL-2.1).
RUN apt-get update && apt-get install -y --no-install-recommends python3-pip python3-yaml libasound2t64 \
    && rm -rf /var/lib/apt/lists/*
ENV PIP_BREAK_SYSTEM_PACKAGES=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_CONSTRAINT=/etc/pip-constraints.txt
COPY image/constraints.txt /etc/pip-constraints.txt
# The base's PyTorch stack, held where it is for every install below (none, on the plain base).
RUN pip freeze 2>/dev/null | grep -i -E '^(torch|torchvision|transformers|numpy|huggingface[-_]hub|tokenizers|safetensors)==' \
      >> /etc/pip-constraints.txt || true
# mujoco: the twin (software/choreo/ik.py, sim/clearance.py). The rest is what ssa needs.
RUN pip install mujoco "numpy<2" anthropic python-dotenv rich mcap mcap-ros2-support

# Speech for the listener (listener.py, chat.py): sherpa-onnx for the keyword spotter, Silero VAD and
# Parakeet; Kokoro for the voice. Installed as the voice image's GPU variant installs them, from its
# own requirement files: kokoro and misaki with --no-deps, so espeak-ng (GPL) never comes in, and
# software/voice/no_espeak.py (mounted at run time) stands in for it. The check after it refuses a
# GPL package among those this layer added, and espeak-ng anywhere in the image. It does not judge
# the base's own packages: archit-perception:dev carries ultralytics (AGPL-3.0) for its detector.
COPY --from=voice image/requirements-speech-gpu.txt image/requirements-speech-nodeps.txt image/check_no_gpl.py /opt/speech/
COPY image/speech_no_gpl.py /opt/speech/
RUN pip list --format=freeze 2>/dev/null | cut -d= -f1 > /opt/speech/before.txt \
 && (python3 -c "import torch" 2>/dev/null || pip install torch) \
 && pip install -r /opt/speech/requirements-speech-gpu.txt \
 && pip install --no-deps -r /opt/speech/requirements-speech-nodeps.txt \
 && python3 -I /opt/speech/speech_no_gpl.py /opt/speech/before.txt

# CBiRRT for routing a recorded show's moves around the body (opendubs_architect/routing.py; the arm
# motion planner, sim/planner on feat/arm-motion-planner-ui, is mounted at run time): sscbirrt (MIT),
# sstsr (BSD-2-Clause), toppra (MIT). sscbirrt ships no aarch64 wheel, and its source build asks for
# mujoco==3.14.0, so it is BUILT without the image's pins and installed --no-deps: the twin keeps the
# mujoco it was tested with (constraints.txt), and numpy stays below 2.
RUN PIP_CONSTRAINT= pip install --no-deps sscbirrt==3.13.0 && pip install sstsr==3.5.0 toppra==0.6.3

ENV RMW_IMPLEMENTATION=rmw_fastrtps_cpp
# The /robot/ service types, for the live backend's client and the arm node; and for the gafro backend
# (gafro_robot.py), tobi's arm service's ArmCommand action (software/openarm/interfaces, the `arms`
# build context) and control_msgs' GripperCommand.
RUN apt-get update && apt-get install -y --no-install-recommends ros-jazzy-control-msgs \
    && rm -rf /var/lib/apt/lists/*
COPY ros/robot_api_interfaces /opt/architect_ws/src/robot_api_interfaces
COPY --from=arms opendubs_arm_interfaces /opt/architect_ws/src/opendubs_arm_interfaces
RUN bash -c "source /opt/ros/jazzy/setup.bash && cd /opt/architect_ws && colcon build --event-handlers console_direct-" \
 && rm -rf /opt/architect_ws/build /opt/architect_ws/log
# The code is not in the image: dev.sh mounts this repository at /work and ssa at /ssa, read-only.
COPY image/entrypoint.sh /opt/architect/entrypoint.sh

# Show music on the Jabra (opendubs_architect/show_music.py): miniaudio (MIT; its MP3 decoder is dr_mp3,
# public domain / MIT-0). Not ffmpeg, whose distro build carries GPL parts. No aarch64 wheel: built here.
RUN pip install --no-deps miniaudio==1.71
WORKDIR /work
ENTRYPOINT ["/opt/architect/entrypoint.sh"]
CMD ["bash"]
