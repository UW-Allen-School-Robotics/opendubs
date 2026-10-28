# The hand tracker (hand_tracker/): the perception image plus MediaPipe. A layer of its own, so adding MediaPipe neither
# rebuilds the perception image nor changes the tag its running containers use.
#   docker build -f image/hand.Dockerfile -t archit-perception-hand:dev software/perception    (tools/hand_stack.sh build)
FROM archit-perception:dev

# mediapipe 1.1.1 has an aarch64 wheel and asks only for `numpy` and `opencv-contrib-python`: the image's numpy<2 (ROS
# Jazzy's) holds, and the contrib build is capped like the others (4.12 and later need numpy 2). sounddevice is one of
# its requirements and wants PortAudio at import. The tasks' C library links EGL and GLES even on the CPU, so `import
# mediapipe` succeeds without them and the first landmarker fails (libEGL.so.1): the check loads the library itself.
RUN apt-get update && apt-get install -y --no-install-recommends libportaudio2 libegl1 libgles2 && rm -rf /var/lib/apt/lists/* \
 && pip install mediapipe==1.1.1 "opencv-contrib-python<4.12" \
 && python3 -c "import numpy; assert numpy.__version__ < '2', numpy.__version__; \
from mediapipe.tasks.python.core import mediapipe_c_bindings as b; b.load_raw_library(); import mediapipe; print('mediapipe', mediapipe.__version__)"
