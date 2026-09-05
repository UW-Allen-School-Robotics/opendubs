# Tab5 Face & Payload Controller

The `tab5_face` firmware runs on the M5Stack Tab5 (ESP32-P4) and serves as the primary payload controller and dynamic UI for the OpenDubs robot.

## Features
- **Dynamic Face UI:** Displays real-time robot state and smooth animations.
- **Wireless Telemetry (UDP):** Streams Time-of-Flight (ToF) distance and UI events back to the Thor via a CAN-over-UDP protocol.
- **Autonomous Head Tracking:** Receives UDP pan/tilt commands from Thor's YOLOv8 node and drives the I2C servos.
- **Over-The-Air (OTA) Updates:** Allows for wireless flashing of firmware.

## Build and Upload (USB)

By default, the `platformio.ini` is configured to upload over USB. Ensure your Tab5 is connected via the USB-C port.

```bash
# Upload via USB (Default)
pio run -t upload
```

## Over-The-Air (OTA) Upload

Once the Tab5 is connected to the `Opendubs2` network, you can update the firmware wirelessly using ArduinoOTA.

```bash
# Upload via OTA
pio run -e tab5_ota -t upload
```

### ⚠️ OTA Troubleshooting: "No response from device"

If you try to upload via OTA on Windows and the process hangs on `Waiting for device...` before eventually timing out with `No response from device`, **your Windows Firewall is likely blocking the connection.**

The ArduinoOTA process requires the ESP32 to open a reverse TCP connection back to your laptop to download the firmware. If your firewall blocks inbound connections to Python, this will fail.

**To fix this on Windows:**
1. Press the Windows Key and search for **Allow an app through Windows Firewall**.
2. Click **Change settings** (requires administrator privileges).
3. Scroll down the list and locate `Python` (or `python.exe`).
4. Ensure **both** the `Private` and `Public` checkboxes are ticked for Python.
5. Save changes and try the OTA upload command again.

## Current State & TODOs

### Current State
* **Firmware**: The Tab5 firmware successfully supports toggling between `TRACKING: AI` and `TRACKING: OFF` modes via the HMI controller.
* **Control Handoff**: In `TRACKING: AI` mode, the Tab5 accepts UDP `CAN_ID_PAN_TILT_CMD` packets to dynamically control the servos. In `TRACKING: OFF` mode, it relies strictly on the touch screen D-pad.
* **Link State**: The Tab5 correctly maintains the `g_linkOnline` state when receiving valid UDP packets from Thor.
* **Thor Backend**: A prototype Python tracker script (`thor_face_tracker.py`) has been written to pull the Tab5 camera stream, perform OpenCV motion tracking (frame differencing), and send UDP pan/tilt commands back.

### TODOs
* [ ] **Integrate Python Tracker**: The `thor_face_tracker.py` prototype needs to be integrated into Thor's main software stack (e.g. as a systemd service, ROS node, or within the primary python daemon) so that it runs automatically on startup.
* [ ] **Network Stability**: mDNS resolution (`opendubs2.local`, `opendubs-tab5.local`) on the Windows laptop is currently unstable. The backend script currently relies on hardcoded IP addresses. Need to resolve mDNS routing issues on the network to make the IPs dynamic.
* [ ] **Advanced Face Detection**: The prototype tracker currently uses motion tracking (frame differencing) because the local OpenCV build was missing `CascadeClassifier`. Once integrated into Thor, this should be upgraded back to a Haar Cascade or a neural network (like YOLO/MediaPipe) for true face detection.
