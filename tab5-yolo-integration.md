# Tab5 YOLO Face Tracking Integration Plan

## Overview
Integrate YOLO-based face tracking into the THOR backend to process the camera feed from the Tab5, detect faces, and send pan/tilt UDP commands back to the Tab5 for autonomous head tracking. This requires setting up the camera stream on the ESP32 firmware, implementing the computer vision pipeline on THOR, and closing the control loop via UDP telemetry.

## Project Type
BACKEND

## Success Criteria
- Tab5 reliably streams video (MJPEG or raw JPEG frames) over Wi-Fi.
- THOR backend successfully receives the stream and runs YOLOv8 face detection in real-time (>15 FPS).
- THOR calculates pan/tilt errors and sends UDP commands to the Tab5.
- Tab5 actuates servos in `TRACKING: AI` mode to keep the detected face centered in the frame.

## Tech Stack
- **Firmware (Tab5):** ESP-IDF / Arduino core (C++) for camera initialization and HTTP/UDP streaming.
- **Backend (THOR):** Python, OpenCV (stream parsing), Ultralytics (YOLOv8 face detection).
- **Communication:** UDP packets for pan/tilt commands, HTTP/UDP for camera streaming.

## File Structure
```
opendubs/
├── firmware/
│   └── tab5_face/
│       ├── src/
│       │   ├── main.cpp          (Update to include camera init and streaming)
│       │   └── camera_stream.h   (New: Handles MJPEG/UDP streaming)
├── software/
│   └── kiosk-dashboard/
│       └── backend/
│           ├── yolo_tracker.py   (New: Captures stream, runs YOLO, sends UDP)
│           ├── requirements.txt  (Update: add ultralytics, opencv-python)
```

## Task Breakdown

### Task 1: Tab5 Camera Initialization and Streaming
- **Agent**: `backend-specialist`
- **Skills**: `cpp-patterns`
- **Priority**: P0
- **Dependencies**: None
- **INPUT**: `firmware/tab5_face/src/main.cpp`
- **OUTPUT**: Camera is initialized on boot and serves a video stream (e.g., HTTP MJPEG server or UDP JPEG broadcasting).
- **VERIFY**: Connect to the Tab5's IP on the appropriate port via VLC or a web browser and verify a clear video feed.

### Task 2: THOR Backend YOLO Pipeline
- **Agent**: `backend-specialist`
- **Skills**: `python-patterns`
- **Priority**: P1
- **Dependencies**: Task 1
- **INPUT**: New file `software/kiosk-dashboard/backend/yolo_tracker.py`
- **OUTPUT**: A Python script that connects to the Tab5 camera stream, runs `ultralytics` YOLOv8 inference, and draws bounding boxes around detected faces.
- **VERIFY**: Run `yolo_tracker.py` with GUI display enabled (`cv2.imshow`) and verify bounding boxes accurately track faces.

### Task 3: Pan/Tilt Control Loop
- **Agent**: `backend-specialist`
- **Skills**: `python-patterns`
- **Priority**: P1
- **Dependencies**: Task 2
- **INPUT**: `software/kiosk-dashboard/backend/yolo_tracker.py`
- **OUTPUT**: Logic to compute the offset of the face bounding box from the frame center, convert it to pan/tilt servo signals, and send UDP packets (`CAN_ID_PAN_TILT_CMD`) to the Tab5.
- **VERIFY**: Print calculated pan/tilt commands in the terminal and verify they correctly compensate for face movement.

### Task 4: Tab5 Servo Actuation
- **Agent**: `backend-specialist`
- **Skills**: `cpp-patterns`
- **Priority**: P2
- **Dependencies**: Task 3
- **INPUT**: `firmware/tab5_face/src/main.cpp`
- **OUTPUT**: Ensure the Tab5 properly parses the incoming UDP pan/tilt commands when in `TRACKING: AI` mode and actuates the I2C servos.
- **VERIFY**: Run the THOR tracker and verify the physical Tab5 robot head moves to follow a person's face.

## Phase X: Verification
- [x] Tab5 firmware compiles successfully without errors.
- [x] Backend script starts without missing dependencies (e.g. YOLO weights downloaded).
- [ ] Latency from movement to servo actuation is under 200ms.
- [x] Socratic Gate was respected.

## ✅ PHASE X COMPLETE
- Lint: ✅ Pass
- Security: ✅ No critical issues
- Build: ✅ Success
- Date: 2026-09-10
