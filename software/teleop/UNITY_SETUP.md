# Quest Teleop — Unity Setup (click-by-click)

Explicit build of the proof-of-life app. Outcome: a Quest APK that publishes HMD +
controller pose (`PoseStamped`) and buttons (`Joy`) to the Thor's rosbridge, through the dashboard's
`/rosbridge` route (rosbridge's own port 9090 listens on the robot only). Scripts
are in [`unity/`](unity/); topic contract + verify steps in [`README.md`](README.md).

**Versions (verified 2026-06):** Unity **6000.0 LTS** (≥ 6000.0.66f2), Meta XR
All-in-One SDK, ros-sharp UPM. **Use the 6000.0 LTS line specifically — NOT the
6000.1 / 6.2 / 6.4 tech-stream releases** (Unity Hub may list one as "latest").
The tech stream ships a stricter Android Gradle Plugin that hits a Meta XR
`com.oculus.Integration` namespace-collision build error. Don't use Unity 2022.3
either unless you pin Meta XR SDK ≤ v73.

> If a build fails with `Namespace 'com.oculus.Integration' is used in multiple
> modules (:SDKTelemetry:, :OVRPlugin:)`, you're on a tech-stream Unity (6000.4.x
> etc.). Fix: install a **6000.0.x LTS** editor and open the project with it (Hub →
> project → editor-version dropdown). Scripts/scene/packages carry over.

---

## A. Install Unity (≈20–40 min, mostly download)

1. Install **Unity Hub** (unity.com/download).
2. Hub → **Installs → Install Editor →** latest **Unity 6 LTS** (6000.0.x).
3. In the module picker, CHECK **Android Build Support** and expand it — also check
   **OpenJDK** and **Android SDK & NDK Tools**. (These give you the toolchain *and*
   a copy of `adb`.) Finish install.

## B. Quest developer mode + adb (do in parallel)

1. Phone **Meta Horizon app → Devices →** your headset **→ Headset Settings →
   Developer Mode → ON**. (Needs a verified developer org at developers.meta.com —
   free; create one if prompted.)
2. Plug the Quest into the Mac by USB-C. Put the headset on → accept **"Allow USB
   debugging"** (and "Always allow from this computer").
3. Confirm the Mac sees it. `adb` is at
   `~/Library/Android/sdk/platform-tools/adb` (from Android Studio or the Unity
   module above):
   ```bash
   ~/Library/Android/sdk/platform-tools/adb devices   # headset should list as "device"
   ```

## C. New project + Android/XR config

1. Hub → **New project → 3D (Built-In Render Pipeline)** is fine → name it
   `quest_teleop` → Create.
2. **File → Build Profiles** (or Build Settings) → select **Android → Switch
   Platform**. (First switch reimports assets — a few minutes.)

## D. Meta XR SDK + camera rig

1. **Window → Package Manager → + → Add package by name** → `com.meta.xr.sdk.all`
   → Add. (Or get "Meta XR All-in-One SDK" free on the Asset Store, then import from
   Package Manager → My Assets.)
2. A **Meta XR Project Setup Tool** window appears (or **Edit → Project Settings →
   Meta XR**). Click **Fix All** then **Apply All** for the Android/XR items.
3. **Edit → Project Settings → XR Plug-in Management** → Android tab → enable
   **OpenXR** (NOT the legacy **Oculus** plugin — Unity deprecated it). Then under
   **OpenXR → Android**: add the **Oculus Touch Controller Profile** interaction
   profile and enable the **Meta Quest Support** feature. (Running the Meta XR
   **Project Setup Tool → Fix All** configures all of this for you — easiest path.)
4. Add the rig: **Meta XR Tools → Building Blocks** → drag the **Camera Rig** block
   into the scene. **Delete the default `Main Camera`** if one remains. Save the
   scene (Ctrl/Cmd-S) as `Assets/Scenes/Main.unity`.
   - In the Hierarchy, expand the rig and confirm it has `CenterEyeAnchor`,
     `LeftHandAnchor`, `RightHandAnchor` (under `TrackingSpace`/`OVRCameraRig`).

## E. ros-sharp

1. **Window → Package Manager → + → Add package from git URL** → paste:
   ```
   https://github.com/siemens/ros-sharp.git?path=/com.siemens.ros-sharp
   ```
   → Add. (Imports RosBridgeClient + Unity components.)
2. Create the connector: in the Hierarchy **right-click → Create Empty**, name it
   **`RosConnector`**. In the Inspector **Add Component → RosConnector**. Set:
   - **Ros Bridge Server Url:** `ws://<robot>:8080/rosbridge` (`<robot>`: the robot computer's
     address. Plain `ws://` on 8080: the headset won't trust the robot's self-signed certificate on
     8443. A native app sends no `Origin` header, so the route lets it through.)
   - **Serializer:** `Microsoft` (JSON) · **Protocol:** `Web Socket NET`
   - (If it won't connect on the headset later, switch Protocol to the other option.)

   > ⚠ **Use a STABLE address, not a raw DHCP IP.** The Thor's IP drifts — it went
   > `.109 → .115` within one session, and another device grabbed `.109`. Prefer a
   > **DHCP reservation** on the router for the Thor's wired MAC (see the router's lease table)
   > and use that fixed IP. `opendubs2.local` works only where mDNS resolves — **Quest/
   > Android mDNS is unreliable**, so on the headset you may need the reserved IP.

## F. Add the publisher scripts + wire them

1. Copy `teleop/unity/QuestTransformPublisher.cs` and
   `teleop/unity/QuestJoyPublisher.cs` into `Assets/Scripts/` (create the folder).
2. Select the **RosConnector** GameObject. **Add Component** the five publishers and
   set their fields (Topic is on the base `UnityPublisher`):

   | Add Component | Topic | SourceTransform | Controller | FrameId |
   |---|---|---|---|---|
   | QuestTransformPublisher | `/quest/hmd/pose` | CenterEyeAnchor | — | `quest_hmd` |
   | QuestTransformPublisher | `/quest/controller/left/pose` | LeftHandAnchor | — | `quest_left` |
   | QuestTransformPublisher | `/quest/controller/right/pose` | RightHandAnchor | — | `quest_right` |
   | QuestJoyPublisher | `/quest/controller/left/joy` | — | `L Touch` | `quest_left` |
   | QuestJoyPublisher | `/quest/controller/right/joy` | — | `R Touch` | `quest_right` |

   **Source Transform** is required (drag the anchor from the Hierarchy into the
   slot). **ReferenceFrame is OPTIONAL — leave it `None` for proof-of-life** (the
   script falls back to world space; the rig is at the origin so it's equivalent).
   If you ever set it, it's the rig root the anchors live under — note that with
   current Building Blocks it may be named `[BB] Camera Rig`, not `OVRCameraRig`.

## G. Build & sideload

1. **File → Build Profiles → Android → Build And Run** (with the Quest connected) —
   Unity builds the APK, installs it, and launches it. (Or **Build** to an `.apk`,
   then `adb install -r quest_teleop.apk`.)
2. Player settings sanity (Edit → Project Settings → Player → Android): **Scripting
   Backend = IL2CPP**, **Target Architectures = ARM64** (Meta's Fix All usually sets
   these).
3. In the headset, the app launches from **Library → Unknown Sources**.

## H. Verify (on the Thor)

```bash
bash /opt/opendubs/teleop/verify-quest.sh        # topics + per-topic rate
ros2 topic echo /quest/hmd/pose                  # live HMD pose
```
Or open Foxglove (`ws://opendubs2.local:8765`) → 3D panel → watch the frames move
with your head/hands. **That's the proof of life.**

## Gotchas

- **No `/quest/*` topics:** RosConnector didn't connect. Verify the Quest is on the
  same Wi-Fi LAN as the Thor, the URL is `ws://<robot>:8080/rosbridge` (not `:9090`), the dashboard
  and rosbridge are up (`systemctl is-active opendubs-dash opendubs-rosbridge`), and try the other RosConnector
  Protocol.
- **Publish/type errors in the log:** that's the ros-sharp **ROS1-vs-ROS2 message
  set**. rosbridge usually tolerates the default types (extra `seq` ignored); if not,
  follow the ros-sharp wiki to enable the ROS2 message types
  (<https://github.com/siemens/ros-sharp/wiki>).
- **Build errors about Android SDK/NDK:** re-open the Unity Hub module installer and
  ensure OpenJDK + Android SDK & NDK Tools are installed for this editor version.
- **Headset not in `adb devices`:** re-toggle Developer Mode, replug USB, accept the
  in-headset debugging prompt.
- **`adb devices` shows "unauthorized":** the "Allow USB debugging?" prompt is waiting
  *inside the headset* — put it on, check "Always allow from this computer", Allow. If
  no prompt: replug while worn, or `adb kill-server && adb start-server`, or revoke USB
  debugging authorizations in headset Developer settings and replug for a fresh prompt.
