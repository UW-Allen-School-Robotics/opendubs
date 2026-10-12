# Meta Quest Teleop — Proof of Life

**Goal of this beat:** prove the *input pipeline* end to end — put the headset on,
move the controllers, and see the **HMD + both controllers' 6-DoF pose** and the
**buttons/triggers** arrive as live ROS topics on the Thor. **No actuation, no video
to the headset** yet. (Capability backlog #7; the FSM `TELEOP_CONTROL` state is the
next step — see state-machine-review.md R7.)

## Architecture

```
Quest (Unity app: Meta XR SDK + ros-sharp)
   │  controller + HMD pose, buttons  →  ros-sharp RosBridgeClient (WebSocket)
   ▼
ws://<robot>:8080/rosbridge   (the dashboard's route to opendubs-rosbridge — no new service)
   ▼
ROS 2 topics on the Thor  →  ros2 topic echo / Foxglove 3D
```

rosbridge's raw port 9090 listens on the robot's computer only (127.0.0.1); off the robot, every client reaches it through the dashboard's `/rosbridge` route, which relays the same frames unchanged. The route turns away browser pages from other sites (a page always names
itself in an `Origin` header); a native app like this one sends no `Origin`, so it is let through.
Plain `ws://` on 8080, not `wss://` on 8443: the headset would not trust the robot's self-signed
certificate.

Chosen ros-sharp → existing rosbridge over Unity's ROS-TCP-Connector specifically
to add **zero new robot-side services** (ROS-TCP-Endpoint would need a colcon
workspace we otherwise don't use). Same thin-client-over-rosbridge pattern as the
LIDAR tab + StamPLC bridge.

## Topic contract

| Topic | Type | Source |
|---|---|---|
| `/quest/hmd/pose` | `geometry_msgs/PoseStamped` | centerEyeAnchor |
| `/quest/controller/left/pose` | `geometry_msgs/PoseStamped` | LeftHandAnchor |
| `/quest/controller/right/pose` | `geometry_msgs/PoseStamped` | RightHandAnchor |
| `/quest/controller/left/joy` | `sensor_msgs/Joy` | LTouch buttons/axes |
| `/quest/controller/right/joy` | `sensor_msgs/Joy` | RTouch buttons/axes |

`Joy.axes = [index_trigger, hand_trigger, thumbstick_x, thumbstick_y]`;
`Joy.buttons = [A/X, B/Y, thumbstick_click, Start/Menu]`. Poses are in the Quest
tracking-space frame (`frame_id` per publisher), Unity→ROS converted (REP-103).

## Prerequisites (your machine)

- **Unity** 2022.3 LTS (or your Meta-supported LTS). Android Build Support + OpenJDK
  + Android SDK/NDK modules.
- **Meta XR All-in-One SDK** (Unity Asset Store / Package Manager) — provides
  `OVRCameraRig`, `OVRInput`, anchors.
- **ros-sharp (ROS2 variant)** — import the `RosBridgeClient` ROS2 message types
  (the ROS2 build, not ROS1: ROS2 `Header` has no `seq`, stamp is
  `builtin_interfaces/Time`). Repo: <https://github.com/siemens/ros-sharp>.
- **Quest** in Developer Mode, USB to the machine, `adb` working
  (`adb devices` lists it).
- Quest on the **same LAN as the Thor** (the dev LAN).

> **Click-by-click build instructions:** [`UNITY_SETUP.md`](UNITY_SETUP.md) (verified
> versions + exact menu paths). The summary below is the same flow, abbreviated.

## Unity project setup

1. New 3D project → **Build Settings → switch platform to Android**.
2. Import **Meta XR All-in-One SDK**; run **Meta → Tools → Project Setup Tool** and
   apply all Android/XR fixes. Add an **OVRCameraRig** to the scene (or the
   "Camera Rig" Building Block). Confirm it has `centerEyeAnchor`, `LeftHandAnchor`,
   `RightHandAnchor`.
3. Import **ros-sharp (ROS2)**. Create an empty GameObject **`RosConnector`**, add
   the `RosConnector` component:
   - **RosBridgeServerUrl:** `ws://<robot>:8080/rosbridge`, where `<robot>` is the robot computer's address (the Thor's DHCP IP drifts — prefer a router DHCP reservation; Quest mDNS for `.local` is unreliable, may need the reserved IP)
   - **Serializer:** Microsoft (JSON) · **Protocol:** WebSocket .NET
4. Drop `QuestTransformPublisher.cs` and `QuestJoyPublisher.cs` (this folder) into
   `Assets/`.
5. On the `RosConnector` GameObject, add the publisher components:
   | Component | Topic | SourceTransform / Controller | FrameId |
   |---|---|---|---|
   | QuestTransformPublisher | `/quest/hmd/pose` | centerEyeAnchor | `quest_hmd` |
   | QuestTransformPublisher | `/quest/controller/left/pose` | LeftHandAnchor | `quest_left` |
   | QuestTransformPublisher | `/quest/controller/right/pose` | RightHandAnchor | `quest_right` |
   | QuestJoyPublisher | `/quest/controller/left/joy` | LTouch | `quest_left` |
   | QuestJoyPublisher | `/quest/controller/right/joy` | RTouch | `quest_right` |
   (Set `ReferenceFrame` = the OVRCameraRig root on the Transform publishers so poses
   are tracking-space-relative.)
6. **Player Settings:** Scripting backend **IL2CPP**, target **ARM64**; **XR Plug-in
   Management → Oculus** enabled for Android.

## Build, sideload, run

```bash
# With the Quest connected over USB:
adb devices                       # confirm the headset is listed
# In Unity: File → Build Settings → Build (APK), then:
adb install -r path/to/quest_teleop.apk
# (or just use Unity "Build And Run" with the Quest connected)
```
Launch the app from the Quest library (Unknown Sources). On start it connects to
rosbridge; if the `RosConnector` reports connected, poses begin publishing.

## Verify (on the Thor)

```bash
bash /opt/opendubs/teleop/verify-quest.sh     # lists topics + per-topic rate
ros2 topic echo /quest/hmd/pose               # watch live HMD pose
ros2 topic echo /quest/controller/right/joy   # squeeze triggers / press A,B
```
Or open **Foxglove** (`ws://opendubs2.local:8765`), add a **3D** panel, and watch
the three frames move with your head + hands. **That moving-frames view is the
proof of life.**

## Troubleshooting

- **No `/quest/*` topics:** the app isn't connected. Check the Quest is on the
  same LAN as the Thor, the URL/IP is right (`ws://<robot>:8080/rosbridge` — the raw `:9090` is
  not reachable off the robot), and both the dashboard and rosbridge are up
  (`systemctl is-active opendubs-dash opendubs-rosbridge`).
- **Connects but no data / type errors:** you imported the ROS1 ros-sharp message
  types. Use the **ROS2** variant (header/stamp differ).
- **Poses look mirrored / axis-swapped:** expected Unity↔ROS handedness; the
  scripts apply `Unity2Ros()`. If a frame still looks off, it's the anchor choice or
  ReferenceFrame, not the conversion — verify in Foxglove 3D.
- **App won't install:** enable Developer Mode + USB debugging on the Quest; accept
  the USB prompt in-headset; re-run `adb devices`.

## Next steps (after proof of life)

1. **`TELEOP_CONTROL` FSM state** (state-machine-review.md R7): add the state +
   ENABLE row + style; a `teleop` input dict (session/link_fresh/deadman) into
   `derive()`; CH5→RC_MANUAL preempts teleop instantly (physical RC stays the
   override).
2. **Teleop deadman:** require the grip trigger (`Joy.axes[1]`) held + a session
   heartbeat before any command is honored — same gate as the RC deadman.
3. **Map to setpoints:** controller pose/stick → base + (later) arm setpoints,
   injected supervisor-side like autonomy.
4. **Video to headset:** NVENC + WebRTC — a much bigger lift, deferred until a
   camera is wired (camera-selection.md).
