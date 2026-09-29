# Camera Selection (Head / Wrist / Nav / RGBD)

Research report for capability-backlog.md item 2 (camera
control & viewing) and item 7 (VR teleop), aligned with
hardening-roadmap.md track 5 (Thor utilization: Isaac ROS +
NITROS, TensorRT, NVENC). Compute is a **Jetson AGX Thor dev kit** (JetPack 7 /
Ubuntu 24.04 / ROS 2 Jazzy, Isaac ROS 4.0 available).

**Fitted today: one RealSense D415 on the head** — a stopgap, chosen for
availability rather than from this analysis, and expected to be replaced. Nothing below is
installed yet.

Researched 2026-06-09. **Thor is new — driver maturity for every camera below is
less proven than on Orin; verify-on-Thor items are flagged throughout.**

---

## Executive summary

1. **The single most important platform fact:** the **AGX Thor dev kit carrier
   board has NO MIPI-CSI ports.** NVIDIA is moving Thor cameras to
   **Camera-over-Ethernet (CoE)** via the Holoscan Sensor Bridge (HSB); GMSL2
   and CSI cameras attach only through an HSB bridge board (D3 Embedded, Leopard
   Imaging, e-con/Lattice) or a partner carrier board for the T5000 module.
   This **rules out all Arducam CSI modules and the ZED X GMSL family on the
   stock dev kit today**, and makes **USB3 the pragmatic interface** for the
   first build (consistent with the off-the-shelf / minimal-fabrication ethos).
2. **Recommended 2-SKU camera BOM:**
   - **Stereolabs ZED 2i (×1–2)** — head (and optionally a second for nav).
     Natively supported on Thor over USB3 (ZED SDK 5.1+, JetPack 7 / CUDA 13);
     the ZED ROS 2 wrapper supports Jazzy and builds **NITROS-enabled**
     (GPU-resident image/depth publishing) when `isaac_ros_nitros` is present —
     the best Isaac ROS fit of anything purchasable today. Stereo RGB also
     gives a real left/right pair for VR passthrough.
   - **RealSense D405 (×2)** — wrists. Purpose-built short-range stereo depth
     (7–50 cm ideal, sub-mm at close range), global shutter, 42×42×23 mm /
     60 g, USB3. Short range is the point: it is built for a camera that
     sits on the hand rather than across the room.
3. **Nav/VSLAM:** start with the head ZED 2i feeding **Isaac ROS cuVSLAM +
   nvblox** (plus the existing RPLIDAR A1 for 2D safety scans); add a
   **RealSense D455** (or second ZED 2i) as a dedicated fixed forward nav
   camera when the head is busy doing teleop gaze.
4. **VR teleop video path:** ZED stereo side-by-side → CUDA → **NVENC**
   (H.264/HEVC low-latency preset) → **WebRTC** to the Quest. Encode/decode and
   WebRTC each add only ~10 ms; the camera + USB capture dominates (~100 ms),
   so pick 60 fps global/fast-readout sensors and keep frames GPU-resident.
   Typical achievable glass-to-glass: **~150–180 ms**.
5. **GMSL is the right long-term answer, wrong first move on Thor.** Revisit
   (ZED X family or Leopard/e-con GMSL-over-HSB) once partner carrier boards /
   HSB bundles mature and if USB cable length or EMI actually bites.

---

## Platform constraints (Thor-specific)

- **No CSI on the dev kit.** Jetson Linux on Thor supports cameras via **SIPL /
  Camera-over-Ethernet (HSB → MGBE 10 GbE, IEEE 1722b)**; GMSL2 and MIPI cameras
  are bridged through HSB hardware. ([NVIDIA CoE docs][nv-coe], [RidgeRun
  Thor CoE wiki][ridgerun-coe], [Stereolabs Thor FAQ][zed-thor-faq])
- **Isaac ROS 4.0 is GA for Thor**: JetPack 7.0 + ROS 2 Jazzy + Isaac Sim 5.1.
  Its release notes explicitly mention **RealSense SDK stability issues on
  JetPack 7** with a setup tutorial as the fix. ([Isaac ROS release
  notes][isaac-rel], [discourse announcement][isaac-discourse])
- **Isaac ROS officially supports**: Intel RealSense family, Stereolabs ZED 2 /
  ZED X, Leopard Imaging Hawk. ([Isaac ROS hardware setup][isaac-hw])
  Caveat: the Hawk path historically used `isaac_ros_argus_camera` (Argus/CSI);
  on Thor Argus is superseded by SIPL/CoE — **verify before assuming Hawk
  applies to Thor**.
- **librealsense V4L native backend is verified on AGX Thor JetPack 7.0 at
  "beta level"** per the official Jetson installation doc. ([librealsense
  Jetson doc][rs-jetson])
- **ZED SDK 5.1+ supports Thor** (JetPack 7, CUDA 13) for **USB cameras only**
  (ZED 2i, ZED Mini). ZED X / ZED X One / ZED Link capture cards **do not work
  on the dev kit** (no CSI); partner carrier boards for the T5000 module are
  the stated path. ([Stereolabs blog][zed-thor-blog], [FAQ][zed-thor-faq])

## Comparison table

| Camera | Interface | Depth tech | Res / FPS / FOV | Onboard compute | ROS 2 Jazzy driver | Isaac ROS / NITROS fit | Thor status | ~Price | Role fit |
|---|---|---|---|---|---|---|---|---|---|
| **Stereolabs ZED 2i** | USB 3.1 | Passive stereo + **neural depth** (GPU) | 2×2K; 1080p@30 / 720p@60; up to 120° (2.1 mm lens); 9-DoF IMU, IP66 | None (host GPU does depth) | `zed-ros2-wrapper` (Jazzy ✓) | **Best**: wrapper publishes via Managed NITROS (GPU-resident) when `isaac_ros_nitros` found; ZED listed in Isaac ROS hw support | **Native today** (SDK 5.1, USB) | $549 (+$50 polarizer) | Head ✓, Nav ✓, RGBD ✓ |
| **Stereolabs ZED Mini** | USB 3 (Type-C) | Passive stereo + neural depth | 2×720p@60 (2K@15); 110°; 63 mm human baseline; 6-DoF IMU | None | `zed-ros2-wrapper` (Jazzy ✓) | Same wrapper/NITROS path | **Native today** (SDK 5.1, USB) | $399 | Head (VR passthrough heritage) ✓, wrist-ish (older sensor) |
| **Stereolabs ZED X** | **GMSL2** (needs ZED Link card) | Passive stereo + neural depth | 2×1200p@60 global shutter; 0.3–20 m | None | `zed-ros2-wrapper` | ZED X explicitly in Isaac ROS hw list | **✗ on dev kit** (no CSI → no ZED Link); partner carrier boards "soon" | $599 + capture card | Future nav/head when carrier exists |
| **RealSense D435i** | USB 3.1 | **Active IR stereo** | Depth 1280×720@90; RGB 1080p (rolling shutter); 87° depth FOV; IMU | ASIC (D4) does stereo match | `realsense-ros` (Jazzy ✓) | RealSense is the reference camera in cuVSLAM/nvblox tutorials; standard ROS msgs (no native NITROS pub) | librealsense V4L backend verified on Thor JP7 (**beta**); Isaac ROS notes JP7 stability workaround | $334 | Nav ✓, budget RGBD ✓ |
| **RealSense D455 / D456** | USB 3.1 | Active IR stereo | Depth 1280×720@90; **global-shutter RGB matched to 86°/87° depth FOV**; 95 mm baseline (<2% err @4 m); IMU; D456 = IP65 | D4 ASIC | `realsense-ros` (Jazzy ✓) | Same as D435i — the cuVSLAM tutorial camera | Same Thor caveats | $419 / $469 | **Nav ✓✓**, RGBD ✓, head alt |
| **RealSense D405** | USB 3.1 | Active-less stereo, short range | 1280×720; **7–50 cm ideal, sub-mm @7 cm**; global shutter; 42×42×23 mm, 60 g; no IMU | D4 ASIC | `realsense-ros` (Jazzy ✓) | Standard ROS msgs; fine for MoveIt/servoing | Same Thor caveats | ~$270 | **Wrist ✓✓** |
| **Luxonis OAK-D S2 / Pro** | USB 3 (Type-C) | Passive stereo (S2) / **active IR dot-projector stereo + IR illum (Pro)** | Depth 1280×800; RGB 4K; ~80–90° (W variants ~120°); Pro range 0.7–12 m | **Myriad X VPU (RVC2), 4 TOPS** — depth + NN on-camera | `depthai-ros`: **no Jazzy binary** (Humble/Iron/Kilted released; Jazzy = source build) | Not NITROS-native; on-camera compute *bypasses* the Thor GPU advantage | Host-agnostic UVC-ish protocol → likely fine, unverified on Thor | $199–$249 (S2) / ~$329–$399 (Pro) | Wrist alt ✓ (smart standalone), nav alt |
| **Luxonis OAK-FFC (3P/4P) + Arducam modules** | USB 3 host link; modules on FFC | Build-your-own stereo (e.g. 2× OV9282/OV9782/AR0234 global shutter) | Module-dependent; custom baselines | Myriad X (RVC2) | `depthai-ros` (same caveat) | Same as OAK-D | Same | ~$249 board + ~$50–100/module | Custom wrist/nav experiments — conflicts with minimal-fabrication ethos |
| **Arducam CSI modules (IMX477/IMX219/etc.)** | MIPI-CSI | None (RGB only) | various | None | needs CSI drivers | n/a | **✗ — dev kit has no CSI** | $30–$100 | Not usable on Thor dev kit |
| **Arducam / generic UVC USB cams (incl. global-shutter AR0234 USB)** | USB 3/2 UVC | None | various | None | `usb_cam` / `v4l2_camera` (Jazzy ✓) | Plain `sensor_msgs/Image`; fine as NVENC source | UVC works anywhere | $60–$150 | Cheap wrist RGB / extra teleop views |
| **Leopard Imaging GMSL2-on-Thor (LI-GMSL2-DESER-HOLOSCAN + ISX031 / IMX728 / AR0234 stereo)** | **GMSL2 → HSB → 10 GbE CoE** | None–stereo (AR0234 stereo module) | e.g. ISX031 3MP HDR | FPGA bridge (HSB) | SIPL/Holoscan path; ROS integration young | Designed for the NVIDIA Thor/Holoscan pipeline; Hawk lineage | **The sanctioned GMSL path on Thor**; contact-sales pricing | $$$ (bundle) | Future nav (long cable, multi-cam) |
| **e-con Systems 10G Holoscan cameras (e-CAM25/56/85_CUHSB, DepthVista_HSB ToF)** | MIPI → Lattice **HSB → 10 GbE** | None / **ToF** (DepthVista) | e.g. AR0234 global shutter FHD; IMX568 5MP GS | FPGA + TintE ISP | Holoscan SDK; ROS layer young | Built for AGX Thor/Orin Holoscan | Thor-supported per e-con | contact sales | Future nav / ToF RGBD alt |

## Per-role recommendations

### (a) HEAD — pan/tilt, VR-teleop POV + general perception
- **Primary: ZED 2i** (2.1 mm wide lens, $549). Works on Thor **today** over
  USB3; true stereo RGB pair for Quest passthrough; neural depth + 9-DoF IMU
  make the head double as the first RGBD + VSLAM sensor; NITROS-enabled Jazzy
  wrapper. ~166 g — size the pan/tilt servos for it (backlog item 1).
- **Alternate: ZED Mini** ($399, lighter, 63 mm human-eye baseline, built for
  stereo VR passthrough) — better ergonomic stereo for teleop, but a dated
  sensor (720p@60) for perception. If head perception matters more than stereo
  comfort, the D455 is the other alternate.

### (b) WRIST — one per OpenArm 7-DOF wrist
- **Primary: RealSense D405 ×2** (~$270 ea). Suited to eye-in-hand use:
  7–50 cm ideal range, sub-mm close-range depth, global shutter, 60 g, USB3,
  `realsense-ros` on Jazzy. No IMU (irrelevant on a wrist — the arm provides
  kinematics).
- **Alternate: OAK-D Pro (or OAK-D S2)** — onboard Myriad X runs depth + a
  detection NN on-camera, offloading Thor entirely; active IR projector helps
  on textureless objects. Caveats: ~0.7 m minimum ideal depth range is poor
  for in-hand work, and **no Jazzy binary of `depthai-ros`** (source build).
  A bare global-shutter UVC camera (Arducam AR0234 USB, ~$100) is the budget
  RGB-only servoing fallback.

### (c) NAV — obstacle avoidance + VO/VSLAM (complements RPLIDAR A1)
- **Primary: RealSense D455** ($419). The literal reference camera of the
  Isaac ROS cuVSLAM and nvblox tutorials (stereo IR pair + IMU; run with the
  IR emitter off for VSLAM, on for depth); global-shutter RGB matched to the
  86° depth FOV; 95 mm baseline = usable depth to ~6 m. D456 ($469) if it
  needs IP65. **Verify on Thor:** librealsense V4L backend is "beta" on JP7
  and Isaac ROS 4.0 notes a RealSense stability workaround; also confirm IMU
  topics on JP7 (IMU needed kernel-module care on JP5/6).
- **Alternate: second ZED 2i** — one SDK across head+nav, NITROS publishing,
  neural depth; passive stereo only (no IR assist in the dark) and more Thor
  GPU spent on depth. Long-term alternate: Leopard/e-con **GMSL-over-HSB** or
  **ZED X on a partner carrier** for front+rear nav pairs with long cables.
- Phase 0 (free): the head ZED 2i already feeds cuVSLAM/nvblox; park the head
  forward when navigating. Buy the dedicated nav camera when teleop gaze and
  navigation genuinely conflict.

### (d) RGBD / depth
- **Covered by (a) + (c)** — ZED 2i (neural stereo depth, 0.3–20 m class) and
  D455 (active IR stereo, 0.4–6 m) both produce RGBD; together they hedge the
  passive-vs-active failure modes (texture-poor walls vs IR-washout outdoors).
- If only ONE depth camera is bought first: **ZED 2i** (head) — it is the one
  camera on this list with first-class Thor support *today* and serves
  head + RGBD + VSLAM simultaneously. **Alternate: D455.**

## One model for everything?

Almost. **ZED 2i for head + nav + RGBD, D405 for wrists** is a clean 2-SKU BOM
(one Stereolabs SDK + one RealSense SDK). A true single-vendor option is
all-RealSense (D455 head/nav + D405 wrists) — simpler software stack, but you
lose the native stereo-RGB VR passthrough and the NITROS-enabled wrapper, and
you concentrate all risk on the JP7 librealsense "beta" status. No single SKU
spans 7 cm wrist work and 10 m navigation; the D405/long-range split is
physics (baseline), not vendor churn.

## Low-latency VR teleop path (NVENC + WebRTC)

- Pipeline: **ZED stereo (side-by-side) → CUDA buffer → NVENC H.264/HEVC
  low-latency preset (no B-frames, CBR, short IDR) → WebRTC → Quest browser or
  native client.** Keep frames GPU-resident end-to-end (NITROS/CUDA →
  NVENC) — this *is* hardening-roadmap track 5.
- Latency budget (measured industry numbers): camera + USB capture ~100 ms
  (dominant), HW encode ≤10 ms, WebRTC stack ~10 ms, decode ≤10 ms → **~150–180
  ms glass-to-glass** is realistic on a LAN ([Transitive Robotics
  breakdown][webrtc-lat]). Lowering it further means 60 fps capture, short
  exposure, and tuning the capture path — not the encoder.
- USB3 is fine for this; the camera interface is not the latency bottleneck.
- The dashboard CAMERA tab can take a lower-rate WebRTC/MJPEG tap of the
  same encoded stream; don't build a second capture path.
- Off-the-shelf scaffolding exists (e.g. Transitive Robotics webrtc-video uses
  Jetson HW encoders automatically); a GStreamer `nvv4l2h264enc` → `webrtcbin`
  pipeline is the DIY route.

## GMSL vs USB on a mobile robot

| | USB3 | GMSL2 (via HSB/CoE on Thor) |
|---|---|---|
| Cable length | ~1–2 m practical | up to ~15 m coax, power-over-coax |
| Connector | friction USB-C (vibration risk; add screw-lock cables) | locking FAKRA |
| EMI | USB3 radiates near 2.4 GHz — keep cables shielded/away from the planned onboard WiFi router (backlog item 3) | excellent immunity, deterministic |
| Thor dev kit support | **works today** | only via HSB bridge bundle or partner carrier board; ecosystem mid-transition |
| Cost/complexity | camera only | + bridge/carrier ($$$), SIPL/Holoscan software path |

Verdict: this robot's cable runs are short (base-mounted Thor → head/wrists);
**USB3 now, GMSL when partner Thor carriers mature** and only if EMI or cabling
actually becomes a problem.

## Suggested BOM delta

| Role | Part | Qty | ~Price |
|---|---|---:|---|
| Head (+first RGBD/VSLAM) | ZED 2i (2.1 mm) | 1 | $549 |
| Wrist | RealSense D405 | 2 | ~$540 |
| Nav (phase 2) | RealSense D455 | 1 | $419 |
| | **Total** | | **~$1.5 k** |

## Open questions / verify on Thor

1. **librealsense on JP7**: V4L native backend is "beta level" on Thor; confirm
   stable depth + **IMU** streams (D455) and apply the Isaac ROS 4.0 RealSense
   setup tutorial. This is the main risk on the D405/D455 picks.
2. **ZED SDK 5.1 on Thor**: confirm neural-depth performance and NITROS build of
   `zed-ros2-wrapper` against Isaac ROS 4.0 (Jazzy) on JetPack 7.
3. **NVENC on Thor**: confirm codec matrix (H.264/HEVC/AV1) and the
   GStreamer `nvv4l2*enc` elements under JetPack 7, and whether the chosen
   WebRTC stack picks up the HW encoder.
4. **USB topology**: how many independent USB3 host controllers does the Thor
   dev kit expose? ZED 2i + 2× D405 + D455 + RPLIDAR must not share one
   bandwidth-limited bus. The robot carries **no local display**, so the port the
   retired touch panel used is free — and the USB instability attributed to that
   panel is gone with it.
5. **depthai-ros on Jazzy**: no binary release (Humble/Iron/Kilted only) —
   source-build feasibility on Thor if OAK is pursued.
6. **ZED X partner carrier boards for Thor**: timeline/pricing — the trigger to
   reopen the GMSL decision.
7. **Hawk / `isaac_ros_argus_camera` on Thor**: Argus is superseded by SIPL/CoE
   on Thor — confirm whether the Isaac ROS Argus path exists at all on JP7.
8. **Pan/tilt payload**: ZED 2i (~166 g + mount + cable torque) vs the M5
   8Servo-driven head servos (backlog item 1) — size or upgrade servos.

## Sources

- NVIDIA Camera-over-Ethernet overview (Thor SIPL/CoE): <https://docs.nvidia.com/jetson/archives/r38.2.1/DeveloperGuide/SD/CameraDevelopment/CoECameraDevelopment/SIPL-for-L4T/CoE-Solution-Overview.html>
- RidgeRun — AGX Thor Camera over Ethernet: <https://developer.ridgerun.com/wiki/index.php/NVIDIA_Jetson_AGX_Thor/Video_Capture_and_Display/Cameras/Camera_Over_Ethernet>
- D3 Embedded GMSL2 HSB bundle for AGX Thor: <https://www.d3embedded.com/product/designcore-gmsl2-holoscan-sensor-bridge-bundle-nvidia-jetson-agx-thor/>
- Isaac ROS release notes (4.0: JetPack 7 / Thor / Jazzy; RealSense JP7 stability note): <https://nvidia-isaac-ros.github.io/releases/index.html>
- Isaac ROS 4.0 for Thor announcement: <https://discourse.openrobotics.org/t/nvidia-isaac-ros-4-0-for-thor-has-arrived/51079>
- Isaac ROS hardware setup (supported cameras): <https://nvidia-isaac-ros.github.io/troubleshooting/hardware_setup.html>
- Isaac ROS cuVSLAM RealSense tutorial: <https://nvidia-isaac-ros.github.io/concepts/visual_slam/cuvslam/tutorial_realsense.html>
- Stereolabs — ZED cameras on Jetson Thor (SDK 5.1, USB-only today): <https://www.stereolabs.com/blog/zed-cameras-on-nvidia-jetson-thor-high-performance-perception-at-the-edge>
- Stereolabs FAQ — ZED X GMSL2 on AGX Thor dev kit (not compatible; partner carriers): <https://support.stereolabs.com/hc/en-us/articles/36125536933015>
- Stereolabs Isaac ROS integration (NITROS in zed-ros2-wrapper): <https://www.stereolabs.com/docs/isaac-ros>
- zed-ros2-wrapper (Foxy/Humble/Jazzy): <https://github.com/stereolabs/zed-ros2-wrapper>
- ZED 2i store/specs: <https://www.stereolabs.com/store/products/zed-2i>
- ZED Mini store/specs: <https://www.stereolabs.com/store/products/zed-mini>
- ZED X store/specs: <https://www.stereolabs.com/store/products/zed-x-stereo-camera>
- librealsense Jetson installation (V4L backend verified on AGX Thor JP7 beta): <https://github.com/realsenseai/librealsense/blob/master/doc/installation_jetson.md>
- realsense-ros (ROS 2 Humble/Iron/Jazzy): <https://github.com/realsenseai/realsense-ros>
- RealSense D455: <https://www.intelrealsense.com/depth-camera-d455/> · D456: <https://www.intelrealsense.com/depth-camera-d456/> · D405: <https://www.realsenseai.com/products/stereo-depth-camera-d405/>
- RealSense store pricing (D435i $334 / D455 $419 / D456 $469): <https://store.intelrealsense.com/products.html>
- Luxonis OAK-D S2: <https://shop.luxonis.com/products/oak-d-s2> · OAK-D Pro: <https://shop.luxonis.com/products/oak-d-pro>
- depthai-ros: <https://github.com/luxonis/depthai-ros> · ROS index (no Jazzy release): <https://index.ros.org/p/depthai_ros_driver/>
- OAK-D Series 2 overview (pricing): <https://www.cnx-software.com/2022/09/18/luxonis-oak-d-series-2-usb-and-poe-cameras-integrate-3d-depth-and-ai-for-robotics-applications/>
- Arducam OAK-FFC-4P: <https://www.arducam.com/product/opencv-ai-kit-oak-ffc-4p-oak-usb3-edition-for-depthai/> · OV9782 GS module: <https://www.arducam.com/product/arducam-1mp-ov9782-global-shutter-color-mipi-camera-module-20pin-for-depthai/>
- Leopard Imaging GMSL2 on Thor: <https://leopardimaging.com/gmsl2-cameras-on-thor/>
- e-con Systems 10G Holoscan cameras for Thor/Orin: <https://www.e-consystems.com/nvidia-holoscan/10g-holoscan-cameras-nvidia-holoscan-sdk.asp>
- Transitive Robotics — WebRTC latency breakdown: <https://transitiverobotics.com/blog/webrtc-latency-breakdown/>
- Transitive Robotics — webrtc-video (Jetson HW encoder support): <https://transitiverobotics.com/caps/transitive-robotics/webrtc-video/>

[nv-coe]: https://docs.nvidia.com/jetson/archives/r38.2.1/DeveloperGuide/SD/CameraDevelopment/CoECameraDevelopment/SIPL-for-L4T/CoE-Solution-Overview.html
[ridgerun-coe]: https://developer.ridgerun.com/wiki/index.php/NVIDIA_Jetson_AGX_Thor/Video_Capture_and_Display/Cameras/Camera_Over_Ethernet
[zed-thor-faq]: https://support.stereolabs.com/hc/en-us/articles/36125536933015
[zed-thor-blog]: https://www.stereolabs.com/blog/zed-cameras-on-nvidia-jetson-thor-high-performance-perception-at-the-edge
[isaac-rel]: https://nvidia-isaac-ros.github.io/releases/index.html
[isaac-discourse]: https://discourse.openrobotics.org/t/nvidia-isaac-ros-4-0-for-thor-has-arrived/51079
[isaac-hw]: https://nvidia-isaac-ros.github.io/troubleshooting/hardware_setup.html
[rs-jetson]: https://github.com/realsenseai/librealsense/blob/master/doc/installation_jetson.md
[webrtc-lat]: https://transitiverobotics.com/blog/webrtc-latency-breakdown/
