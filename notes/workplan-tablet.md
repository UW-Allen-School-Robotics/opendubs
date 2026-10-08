# Work plan — the tablet: a sensor for the robot, and a head the robot can wear

The plan of record, **agreed by the project team on 2026-10-08**. Work proceeds through these
phases; anything not in a phase, in the team requests, or in the stub register is not being worked
on. Finished items are struck through with a date, as in [`workplan-tab5-idf.md`](workplan-tab5-idf.md)
and [`workplan-choreography.md`](workplan-choreography.md).

Background: the tablet as a managed device and its experiment results are in
[`design/tablet-device.md`](design/tablet-device.md); the kiosk app is
[`software/tablet-kiosk/`](../software/tablet-kiosk/README.md). The Tab5 head is
[`workplan-tab5-idf.md`](workplan-tab5-idf.md) (closed), its platform
[`design/tab5-platform.md`](design/tab5-platform.md), who may move it
[`design/head-ownership.md`](design/head-ownership.md), and the face twin
[`design/face-twin.md`](design/face-twin.md).

**The tablet** is a Samsung Galaxy Tab A11 (SM-X133, Wi-Fi), Android 15 / One UI 7, MediaTek MT6789.
It runs the team's own kiosk app as device owner, in lock task, showing the robot's dashboard over
HTTPS. On robot opendubs2 it rides in a cradle on the robot's back and can be lifted off and held.

**The shape of this plan.** Two parts that share one foundation:

- **Part 1 — the tablet as a sensor.** What the tablet already carries (accelerometer,
  magnetometer, light, a grip sensor, GNSS, cameras), piped to the Thor and ROS — **always labelled
  with whether the tablet is on the robot**, because it can be picked up.
- **Part 2 — the tablet or the Tab5 as the head.** One head interface that both implement, so
  nothing above the head cares which is fitted. With the tablet as the face, it is **the same
  tablet** that is the operator's controller: lift it off the head and it becomes the dashboard.

The foundation both share is the **mount signal**: whether the tablet is in the robot's mount or in
someone's hands.

---

## Standing rules

1. **A held tablet is never the robot.** Everything the tablet contributes carries its mount state.
   **For data, unknown counts as held:** nothing derived from a held or uncertain tablet is
   published as the robot's pose, motion, position or surroundings. **For the screen, unclear
   evidence changes nothing:** the mode (DT-11) changes only on positive evidence of a lift-off or a
   re-mount, so an e-stop cutting the mount's power never hides the face.
2. **The Tab5 is not retired until a replacement fully works** — every item of the retirement
   checklist (7.4) passed on the robot. Until then the system works with **either** head fitted,
   and nothing above the head interface — the dashboard, the show runner, the face bridge, the YOLO
   tracker — cares which.
3. **One device, not two.** If the tablet is the face, it is the same tablet that carries the
   dashboard. There is never a second tablet.
4. **The dashboard stays a web page usable from any browser on the LAN** (choreography standing
   rule 12). The kiosk app's door (DT-6) and the dashboard never depend on each other: each works
   without the other, and kiosk features are detected, never required.
5. **The Thor is for high-level control.** Nothing the Tab5 head does without the Thor today — show
   the robot's state, show NO LINK, offer a soft e-stop and the lift, take RC head aiming — may come
   to need the Thor when the tablet is the head.
6. **Android never closes a motion loop.** Servos run on a real-time controller, under a heartbeat
   or lease, on the e-stop-switched branch. The tablet sends targets and requests.
7. **Honest sensors.** The tablet has **no gyroscope**: its "orientation" is accelerometer plus
   magnetometer, so it gives **tilt, and only while not accelerating** — never a heading, a yaw rate
   or a pose. Its compass is unreliable near the drive motors. GNSS indoors is reported as *no fix*,
   never a stale or guessed position.
8. **Every experiment states its question and its kill criterion before it starts. No robot
   conclusions from the bench** (Tab5 plan, standing rule 1).
9. **Per-robot values stay on the robot** — which head is fitted, its addresses, any mount
   thresholds — in `/etc/opendubs/robot.env`, never in the repo.
10. **Flashing is rote** (`CLAUDE.md`). The neck controller (DT-7) gets its row in `CLAUDE.md`'s
    flash table **before** it is fitted to a robot.
11. **Security is LAN-first and not a blocker** (choreography plan, parked item P1).

---

## Decisions of record — 2026-10-08

Walked through by the team one at a time. Each drives an item below.

### Part 1 — the tablet as a sensor

| # | Decision | Drives |
|---|---|---|
| DT-1 | **The mount signal is a magnet in the mount, confirmed by power and watched by motion.** *Amended 2026-10-08, in review: first agreed as "mount power with grip and motion as vetoes"; changed because the mount's power comes from the e-stop-switched rail, which is off whenever the contactor is open — an e-stop, or CH5 not in OFF at power-on — so power alone cannot say where the tablet is.* **Magnet:** a small magnet in the mount gives the tablet's magnetometer a large, unmistakable field, with the rail on or off and no wiring. **Power:** counts only while the StamPLC's broadcast says the rail should be live; with the contactor open, losing power is not evidence of anything. **Motion** (and grip, if TS-2 shows it readable without mistaking a touch on the screen for a hand) catches a lift-off as it happens. The magnet costs the compass while mounted, which rule 7 already gives up. | 0.3, 1.1, TS-2–TS-4, TS-7 |
| DT-2 | **Tablet data reaches the Thor through the dashboard backend**, as one `tablet` message on the dashboard websocket, accepted only from the designated kiosk device (as show audio designates one device), and published as `/tablet/*` ROS topics with `frame_id: tablet` — **never** `/imu`, never a robot frame, never into TF as part of the robot. **Who decides, confirmed 2026-10-08 in review:** deciding "in the mount or in hand" runs **on the tablet, in the app**, because the face/controller switch must work with the Thor off (DT-6, DT-11). The Thor checks rather than decides: it accepts tablet data only from the designated device, and treats a missing or stale report as held. | 1.2 |
| DT-3 | **Uses in the plan:** the held/docked dashboard switch; a tilt and impact log while docked; ambient light; GNSS (outdoors; indoors it will be absent); the screen-side camera. The rest is parked with its reason. | 1.3–1.7 |
| DT-4 | **Web APIs first, kiosk-bridge methods for the gaps.** DeviceMotion/DeviceOrientation in the page; new bridge methods only for what the WebView does not expose — the grip sensor and ambient light — and the app's permission plumbing for location and camera. In a plain browser the page loses those and keeps working (standing rule 4). | 1.1–1.7 |
| DT-5 | **Cameras are named screen-side and back-side**, never front/rear, which are robot directions. The *use* is named separately: in the rear cradle the screen-side camera faces out of the robot's back (a **robot rear view**) and the back-side camera faces into the cradle; in the face mount the screen-side camera faces forward. | 1.7, Parked |

### Part 2 — the tablet or the Tab5 as the head

| # | Decision | Drives |
|---|---|---|
| DT-6 | **The head interface is the Tab5's own messages on UDP 4242**, written down as a specification with one test suite run against the Tab5, the face twin and the tablet face. **A tablet head answers them itself**: native code in the kiosk app — *the door* — listens on the head's port, sends to the StamPLC, and hears the StamPLC's telemetry broadcast, so the tablet head does not depend on the Thor. No relay on the Thor. The Tab5 and its twin change nothing. | Phase 2, 5.2 |
| DT-7 | **A neck controller** — an M5 Atom/Stamp-class board in the head — drives the pan/tilt servos (8Servo over I2C) and reads the front ToF when the tablet is the face. It runs the Tab5's pan/tilt behaviour (slew limits, bounds, boot centring, lease expiry), is **the authority over head motion** under `head-ownership.md`'s precedence (touch > RC > Thor YOLO > local tracker), takes RC aiming **straight from the StamPLC** (so RC head control survives a Thor outage and the tablet being lifted off), and sits on the e-stop branch. | Phase 6 |
| DT-8 | **On an e-stop the tablet face stays lit and shows E_STOP.** Motion is unpowered (the neck and servos are on the e-stop branch), so the head cannot move; the display carries on and says why the robot stopped. The tablet's own power is not switched. This applies to the tablet head only; the Tab5 head's behaviour is unchanged. | 4.2, 7.4 |
| DT-9 | **The face is a web engine shipped inside the kiosk app, with a library synced from the Thor.** The engine (web code, loaded from the app's own assets) runs with no Thor, and the same files are the face twin in a laptop browser. Emotes, themes and sounds are a **library** the app copies from the Thor into its own storage and chooses from by name; with the Thor down it uses what it has. Engine changes are app updates (rare); library changes are a sync (often). **This amends TD-2's scope rule** ("a shell, never a UI of its own") on purpose: the app becomes a shell, a face engine and a door — still no settings or screens of its own beyond the Thor-down panel (DT-17). | 4.x, 5.1, 5.3 |
| DT-10 | **Head messages are understood by the face's own code; the door only carries them.** One copy of the handling, so the tablet face and its laptop twin cannot drift. The door answers only device facts itself — app and engine version, mount, mode, whether the face is showing — so `PING` always gets an answer. | 4.3, 5.2 |
| DT-11 | **Three modes, following the mount, with one explicit latch.** **FACE** — booted or idle in the face mount: the face, with a strip for critical telemetry only (e-stop, fault, low battery). **ENGAGED** — a touch (or a person at touching distance, DT-12): the head holds still and a touch brings up the dashboard; it returns to FACE when the dashboard goes idle. **CONTROLLER** — out of the face mount, steadily for a debounce: the face is suppressed, the dashboard only, the head parks centred. Back in the mount and idle → FACE. One latch, **"stay a controller"**, on the dashboard for bench work with the tablet mounted — released by a button, **never by a timeout** (`head-ownership.md`'s latch rule). | 5.4, 5.8 |
| DT-12 | **The head holds still for a touch, and for a person at touching distance.** A touch holds the head (on-face touch is already top precedence) and opens the dashboard. A person within reach — the neck's front ToF, or the screen-side camera — makes the head **finish turning to them, then hold**; further away it keeps looking around, so turn-to-look (`head-turn-gaze.md`) survives. | 5.5 |
| DT-13 | **One mount per configuration.** Tablet head: the face mount or in hand — no rear cradle. Tab5 head (today): the rear cradle or in hand. Either way the mount signal answers one question: in the robot's mount, or not. | 1.1, 5.4 |
| DT-14 | **The two faces share meaning; their looks may differ.** Shared: the state names and what each communicates, which states are reserved (E_STOP and DEGRADED colours untouchable by any theme), and the emote, theme and earcon names. How each is drawn may differ, and the tablet face may go further. One theme source (JSON plus source images, per FT-5) with a build step per head. | 4.1, 4.4 |
| DT-15 | **The Galaxy Tab A11 in both roles; the mechanical gate comes first.** One model, spares interchangeable, and the kiosk qualification already done. Before any face software: weigh it, mock up a face mount, and measure pan/tilt torque margin, lift centre of gravity and arm clearance. If it fails, a smaller tablet is reconsidered then, with TB-1–TB-6 re-run. | Phase 3 |
| DT-16 | **The Tab5 keeps every shared message working** and passes the same test suite; fixes continue. New look-and-feel work goes to the tablet face only. A new message enters the shared set only when both heads implement it — otherwise it is marked tablet-only and the Tab5 refuses it cleanly (it already answers `ERR unknown`). | 2.4 |
| DT-17 | **With the Thor down, a touch on the tablet face brings up a minimal built-in panel:** the robot's state from the StamPLC, the soft e-stop and the lift (gated to MANUAL, as on the Tab5), sent through the door straight to the StamPLC, and "dashboard unreachable". With the Thor up, dashboard pages use the Thor's existing `/api/mast/{up\|down\|stop}` and `/api/estop` in every browser — one normal path. | 5.7 |
| DT-18 | **Order: foundations, then gates.** The mount signal and sensor relay; the head's messages written down and tested against the Tab5; the mechanical gate; the face engine on a laptop; the door and modes on the tablet; the neck controller; the robot. The Tab5 stays the fitted head throughout. | Phase order |

---

## The trade, for the record

What the team weighed in choosing to make the tablet a possible head, kept here so the reasons
survive the conversation.

**For the tablet as the face.** Rendering stops being the constraint — a GPU and gigabytes of RAM
against the P4's CPU rasteriser and 32 MB of PSRAM, so no dirty-rect or cost-gate work, and themes
and media become ordinary web assets. One software stack: the face is web code, so its twin is
itself in a browser, and choreography FACE cues, themes and show audio share the dashboard's
JavaScript. A library of emotes, themes and sounds limited by tablet storage, not a flash
partition. Better sensing: the screen-side camera with Android's on-device vision; Android's echo
cancellation, noise suppression, speech recognition and TTS; mixable, stoppable audio. Reliable
touch and Wi-Fi. Spares interchangeable with the operator tablet, and a commodity part an outside
builder can substitute more easily than one specific M5Stack product.

**Against, and how the plan answers it.**

| cost | answered by |
|---|---|
| An 11″ tablet is several times the Tab5's size and mass: pan/tilt torque, centre of gravity on the lift, arm clearance | the mechanical gate, before any software (Phase 3, DT-15) |
| A tablet has its own battery and keeps running through an e-stop | decided: motion unpowered, face lit and showing E_STOP (DT-8) |
| Android boots slowly and is not real-time | the neck controller keeps the servo loop (DT-7, rule 6); boot time measured and reported (7.4) |
| A web page cannot open a network port | the door, in the app (DT-6) |
| The face code lives in an app installed over `adb` | engine in the app, library synced from the Thor (DT-9) |
| Heat and continuous duty in an enclosed head | measured in the mock head (3.2) |
| Lifting the tablet off leaves the robot faceless | by design: CONTROLLER mode, the head parked, shows needing a face refused (DT-11, 5.8) |

---

## What the tablet carries — verified 2026-10-08

Read from the tablet over `adb` (`dumpsys sensorservice`, `pm list features`, `dumpsys location`,
`dumpsys media.camera`).

| sensor | part | reach from the page | good for | the catch |
|---|---|---|---|---|
| accelerometer | Bosch BMA5xy | DeviceMotion events (`accelerationIncludingGravity`) | tilt and impacts while docked; lift-off detection | no gyroscope: `rotationRate` expected null (TS-1) |
| magnetometer | MEMSIC MMC5617 | bridge (raw field, for the mount magnet); DeviceOrientation (fused) | the mount signal: the mount's magnet (DT-1) | a heading near the drive motors is not trustworthy (rule 7); parked |
| fused "orientation", gravity, linear acceleration, rotation vector | accelerometer + magnetometer only | DeviceOrientation | tilt when still | noisy, magnetically disturbed; not an IMU |
| ambient light | Sensortek STK3x1x | not exposed by the WebView → bridge | dashboard and face brightness | — |
| grip | Semtech SX9325 capacitive ("grip sensor for wifi", for antenna power control) | not exposed → bridge, **if an app can read it** (TS-2) | the mount signal's veto | may read a touch on the screen as a hand |
| significant motion, tilt detector, step counter, Samsung motion/Smart Alert | virtual | — | none found | parked |
| GNSS | MediaTek MT6631; `gps` provider enabled, high accuracy | Geolocation, once the app handles the WebView's permission prompt and holds location permission | outdoor demos | indoors poor or absent |
| cameras | one back-side; two screen-side entries (one may be a logical camera) | `getUserMedia` video, once the app grants it to the dashboard's origin (as it grants the mic) | the robot rear view (rear cradle); forward vision (face mount) | the back-side camera faces into the mount |
| microphone | — | already live (TB-1) | choreography plan 4.3 | — |
| Bluetooth/BLE, USB host, Wi-Fi Direct | — | — | none in this plan | parked |

**Page sensor events stop when the page is hidden or the screen is off.** The kiosk keeps the
screen on, and a face is always lit; whether that is enough is TS-6.

---

## Team requests

| # | Request | Status | Item |
|---|---|---|---|
| T1 | **What can the tablet's sensors give the robot**, piped as simply as possible, and what is each good for | planned | Phase 1 |
| T2 | **Every datum says whether the tablet is docked**; a held tablet is never mistaken for the robot | decided (DT-1, DT-2, rule 1) | 1.1, 1.2 |
| T3 | **Which camera is "the rear"?** | answered 2026-10-08: screen-side and back-side (DT-5) | — |
| T4 | **The robot works with the tablet or the Tab5 as its head**; the Tab5 is not retired until a replacement fully works | decided (DT-6, DT-16, rule 2) | Phase 2, 7.4 |
| T5 | **One device**: the dashboard is in the face; lift the tablet off and it is the operator's controller | decided (DT-11, rule 3) | 5.4 |
| T6 | **The kiosk app independent of the Thor**; the Thor concerned with high-level control | decided (DT-6, DT-9, DT-17, rule 5) | 5.2, 5.3, 5.7 |
| T7 | **In the face mount it boots to the face**, with critical telemetry, and shows the face whenever it is not in use as a dashboard | decided (DT-11) | 4.2, 5.4 |
| T8 | **A touch — and a person close by — fixes the head** so the face can be touched; a touch brings up the dashboard | decided (DT-12) | 5.5 |
| T9 | **The Tab5's touch functions become dashboard pages**, placed logically | planned | 5.6 |
| T10 | **A library of emotes, themes and more on the tablet**, chosen situationally | decided (DT-9) | 4.4, 5.3 |
| T11 | **Lifted off and used as a controller, the face is suppressed**; a clear way to latch and unlatch that | decided (DT-11) | 5.4 |

---

## Phase 0 — corrections to what exists

*Cheap groundwork. Every item is "the head", not "the Tab5", with no change in behaviour on a robot
with a Tab5 fitted.*

| # | Item | Kill criterion / done when |
|---|---|---|
| 0.1 | **BUILT, 2026-10-08; not yet deployed.** `backend/head.py` gives the face and neck hosts (`OPENDUBS_HEAD_FACE_HOST`, `OPENDUBS_HEAD_NECK_HOST`; the old `OPENDUBS_TAB5_HOST` still works, with a notice). In `state_out.py` each half is a peer — **one shared peer when the hosts match**, so a Tab5 robot does exactly what it did — with `STATE`/`TURN`/`LIFT`/`PMODE` and `PING` to the face, and the tracking request, `PLCIP` and the StamPLC's `TAB5IP` to the neck (the StamPLC firmware is unchanged: `TAB5IP` stays its word for "where RC pan/tilt goes"). The YOLO tracker defaults to the neck and its service reads `robot.env` instead of naming the Tab5. Verified: 50 backend tests (9 new) and a loopback run with face and neck apart, each message arriving at its half. **Open:** the done-when on opendubs2. *As found:* **Address the head by role, not by device.** Today: `state_out.py` reads `OPENDUBS_TAB5_HOST` (default `opendubs-tab5.local`); `yolo_tracker.py` **hard-codes** `UDP_IP = "opendubs-tab5.local"`, bypassing that setting; the StamPLC learns where to send RC pan/tilt from a `TAB5IP` command. A head has two halves — **face** and **neck** — which are the same box when the Tab5 is fitted. | Face and neck addresses come from `/etc/opendubs/robot.env` (both the Tab5 by default; old names accepted with a notice); `yolo_tracker.py` reads the setting; on opendubs2 the head still answers `PING` and RC still aims it. |
| 0.2 | **HANDED OVER, 2026-10-08** — choreography request E17. **Show requirements name the role.** `cue.py` has `requires: tab5` and an audio output `tab5`. Proposed: `requires: head: face` (and the fitted head's capabilities, from `PING`); `tab5` refused with the new name given, as 0.7 did for CH5 names. **Owned by the choreography plan** — handed over as a request, not edited from here. | The validator and the runner's preflight ask for a face, not a Tab5; a show needing a face is refused while the tablet head is in CONTROLLER. |
| 0.3 | **BUILT, 2026-10-08; not yet on the tablet.** `battery()` adds `"plugged": "ac"\|"usb"\|"wireless"\|"dock"\|null`; `charging` unchanged. The app builds. **The bridge reports *plugged*, not only *charging*.** `battery()` returns `charging`; with a charge limit (0.4) a tablet on mount power is plugged but not charging, so `charging` would read as "lifted off". | `battery()` (or a new method) reports the power source; the dashboard's tablet battery icon unchanged. |
| 0.4 | **Battery protection for continuous mount power.** If One UI offers a charge limit on this model, set it in the setup steps. | In the kiosk README's setup; checked on the tablet with `dumpsys battery`. |

## Phase 1 — the tablet as a sensor (Part 1)

*Experiments first; each answers a question the build items depend on.*

| # | Experiment | Kill criterion / done when |
|---|---|---|
| TS-1 | **What does the kiosk WebView actually expose?** DeviceMotion and DeviceOrientation (rates, which fields are null without a gyroscope), the Generic Sensor classes, `AmbientLightSensor`, Geolocation with the prompt handled. On the tablet, in the app. | A table: each API present or absent, its rate and fields. |
| TS-2 | **Can an app read the SX9325, and what does it say?** Listed as "grip sensor for wifi"; whether a third-party (device-owner) app may register for it is unknown. Readings: in the mount untouched; in the mount with a finger on the screen (a touched face); held in one hand, in two; lying on a table. | **Kill:** an app cannot read it, or a touch on a mounted screen reads as a hand → grip is not used; power and motion decide alone (DT-1). |
| TS-3 | **Mount power, and whether the tablet can tell the rail should be live.** Does the rear cradle supply power today (if not, a USB-C lead from the e-stop-switched rail)? The StamPLC's broadcast carries the e-stop state; does it carry the contactor relay itself, which also stays open when CH5 is not in OFF at power-on? | The tablet knows, from the StamPLC alone, when its mount power should be present; plugged in the mount while it should be. |
| TS-4 | **Lift-off versus the robot moving.** The accelerometer and power-loss signature of a lift-off, against the robot driving over a bump, turning and running the lift. | No robot motion reads as a lift-off; every lift-off is caught within the debounce. |
| TS-5 | **GNSS, honestly.** Fix or not in the lab; time to first fix and accuracy outdoors. | Recorded either way; indoors the outcome may be "never". |
| TS-6 | **Sensors with the screen off or the page hidden.** | Known whether docked telemetry stops; if it does, the docked log runs only while the screen is on, and says so. |
| TS-7 | **The mount magnet.** The field at the magnetometer with a small magnet in a printed mount, against the field near the drive motors and the lift, rail on and off. **And:** does a magnet set off Samsung's cover detection and turn the screen off? (No cover sensor appears in the sensor list, but that is not proof.) | A threshold with a wide gap between "in the mount" and anything the robot itself produces. **Kill:** the screen turns off, or no magnet placement clears the motors' field → power and motion decide alone, and a re-mount during an e-stop waits for the rail. |

| # | Item | Kill criterion / done when |
|---|---|---|
| 1.1 | **The mount state (DT-1, DT-13).** Computed once, in the app, from the magnet, power (only while the StamPLC says the rail should be live), motion and grip (if TS-2 passes); offered to the page as a bridge method and an event; values `in_mount`, `held`, `unknown` and which mount. **Data** treats `unknown` as held; the **screen mode** holds its last state until positive evidence (rule 1). Thresholds and debounce are named placeholders until TS-3, TS-4 and TS-7 measure them. In a plain browser: `unknown`. | Lifting off reads `held` within the debounce; re-mounting reads `in_mount`, with the rail on or off; an e-stop, CH5 left out of OFF at power-on, a drive and a bump change nothing; a touch on the screen changes nothing. |
| 1.2 | **The tablet relay (DT-2).** The page sends one `tablet` message on the dashboard websocket; the backend accepts it only from the designated device, and publishes **raw** `/tablet/imu` (`sensor_msgs/Imu`, angular velocity marked absent by its own convention — element 0 of the covariance set to −1), `/tablet/illuminance`, `/tablet/fix` and `/tablet/mount`, all `frame_id: tablet`. **Robot-facing derived outputs** (1.4) are published only while `in_mount`. A missing or stale report is `held`. | A held tablet produces no derived output; a second browser cannot publish; nothing appears on `/imu` or in the robot's TF tree. |
| 1.3 | **Held/docked dashboard switch** (Tab5-head configuration, tablet in the rear cradle). Lifting off turns the dashboard into a handheld-controller layout; re-docking returns it. Layout agreed with the team when built. | The switch follows 1.1; in a plain browser the dashboard is unchanged. |
| 1.4 | **Tilt and impact log while docked.** Tilt from gravity only while the acceleration magnitude is near 1 g; impacts as peaks above a named threshold; logged against the robot's own clock. No heading. | A ramp and a deliberate bump appear in the log with robot time; a held tablet logs nothing. |
| 1.5 | **Ambient light** (bridge method). Dashboard brightness and dark mode follow the room; later the face's brightness (Phase 4). | Covering the sensor dims the dashboard; a plain browser is unaffected. |
| 1.6 | **GNSS.** The app handles the WebView's geolocation prompt and holds location permission (granted as device owner); `/tablet/fix` carries its fix status. | Outdoors: a fix with its accuracy. Indoors: *no fix*, never a stale position. |
| 1.7 | **The screen-side camera.** The app grants video to the dashboard's origin only, as it does the mic. In the rear cradle: a **robot rear view** on the dashboard. In the face mount: forward vision for the face (Phase 4). | The rear view shows while `in_mount`; when held it is labelled as the tablet's view, not the robot's, or stops. |

## Phase 2 — the head's shared messages (DT-6, DT-16)

*Useful now: it puts the Tab5's existing contract in writing and under test, before any tablet face
exists.*

| # | Item | Kill criterion / done when |
|---|---|---|
| 2.1 | *Found during 0.1, to check here:* the dashboard and the YOLO tracker **both bind UDP 5006** with `SO_REUSEADDR`, though `state_out.py` says it wants a loud failure if anything else takes the port. Which of them receives the head's frames while both run is unverified. **Write the head protocol** as `notes/design/head-protocol.md`: every message the head accepts today (`PING`, `STATE`, `EMOTE`, `THEME`, `CUE`, `CFG`, `SIM`, `TOF`, `I2CSCAN`, `REBOOT`, `OTA` in `firmware/tab5_idf/app_face/net_cmd.cpp`; **also** `TURN`, `LIFT`, `PMODE` and `PLCIP`, which the dashboard sends (`state_out.py`), the pan/tilt frame `0x104` and tracking-mode frame `0x108`), what it sends (the lift request `0x102` and status `0x106`, both to UDP 5006), its replies, the ports, which half owns it (face, neck, device), what `PING` reports for each head kind, and the head's **outbound** report of who owns it (`head-ownership.md` §4 — not built). | The team agrees it, and the Tab5 matches it as read from its source. |
| 2.2 | **A conformance suite**: send each message, check the reply. Runs against the face twin in CI, and on demand against a real head — **read-only messages only on a robot** (never `REBOOT` or `OTA`). | Passes against the face twin and against opendubs2's Tab5. |
| 2.3 | **Every sender names the half it talks to.** The dashboard (`STATE`), the face bridge and runner (face messages), the YOLO tracker (neck), the StamPLC's RC (neck), the OTA scripts (device). | A table in 2.1, and each sender reads its address per 0.1. |
| 2.4 | **Tablet-only messages** (DT-16): marked as such in 2.1; the Tab5 refuses them with `ERR unknown`; the runner checks a show against the fitted head's capabilities. | A show using a tablet-only message is refused, with the reason, when the Tab5 is fitted. |

## Phase 3 — the mechanical gate (DT-15)

*Before any face software. Confirm a person is at the robot before any measurement on it.*

| # | Item | Kill criterion / done when |
|---|---|---|
| 3.1 | **Will an A11 face fit and move?** Weigh it; print a mock face mount; measure the pan/tilt servo torque margin at the worst-case tilt, the centre of gravity with the lift raised, and arm clearance. The MuJoCo twin may screen clearance geometry first — its geometry is real, its dynamics are not (choreography rule 9). | **Kill:** the servos lack margin, or the arms clear the larger head by less than they clear the Tab5 today → stop; bigger servos or a smaller tablet are argued then. |
| 3.2 | **Heat.** The A11 in the mock enclosed head, on mount power, the face at full brightness, for a long run. | **Kill:** a thermal warning, throttling or a battery temperature warning. |
| 3.3 | **The face mount carries the mount magnet and power** (TS-3, TS-7 applied to the face mount). Power from the e-stop-switched rail is fine: the tablet runs on its own battery while the rail is off. | Through an e-stop the tablet stays `in_mount`, the face up and showing E_STOP (DT-8). |

## Phase 4 — the face engine, on a laptop (DT-9, DT-10, DT-14)

*The engine is its own twin: the same files in a desktop browser.*

| # | Item | Kill criterion / done when |
|---|---|---|
| 4.1 | **Write the tablet face's intent**, as `face-twin.md` did for the Tab5: the drawing technology, the shared vocabulary (DT-14), the library format, the critical-telemetry strip, and what the laptop twin cannot tell you (Android's frame timing, the panel, heat). | The team agrees it. Nothing in 4.2–4.4 starts before. |
| 4.2 | **The engine.** States, emotes, themes and earcons by the shared names; reserved states untouchable by any theme; NO LINK; **E_STOP live** (DT-8). | Every state and emote shows on a laptop; a theme cannot recolour E_STOP or DEGRADED. |
| 4.3 | **A desktop stand-in for the door**: a relay on 127.0.0.1:4242 that forwards to the page. The choreography face bridge then drives the web face unchanged. | The conformance suite (2.2) passes against the web face twin; a show plays on it in step with the body twin. |
| 4.4 | **The library.** One theme source (FT-5's folder) with a build step per head; emotes and sounds as data; an index with content hashes for sync. | The same theme means the same thing on both twins. |

## Phase 5 — the door and the modes, in the kiosk app

| # | Item | Kill criterion / done when |
|---|---|---|
| 5.1 | **Amend TD-2** in `tablet-device.md` and the kiosk README: the app is a shell, a face engine and a door — still no settings or screens of its own beyond the Thor-down panel. | Both documents say so. |
| 5.2 | **The door (DT-6, DT-10).** Listens on the head's port and passes lines to the face; answers `PING`'s device facts itself; hears the StamPLC's telemetry broadcast on 5005 (whether Android delivers broadcasts to the app with the screen dimmed is checked here); resolves the StamPLC by name, as the Tab5 does. | The conformance suite passes against the tablet over the robot's Wi-Fi; with `opendubs-dash` stopped, the face still shows the robot's state from the StamPLC. |
| 5.3 | **Library sync** from the Thor into the app's storage. | A theme deployed on the Thor appears on the tablet with no app install; with the Thor off, the face still has it. |
| 5.4 | **The modes (DT-11).** The face (from the app's assets) over the dashboard WebView, which **stays loaded behind it** so show audio and its connections carry on. The "stay a controller" latch on the dashboard. **Experiment TH-1:** does show audio keep playing, in step, with the dashboard WebView behind the face? | Lift off → CONTROLLER after the debounce; re-mount and idle → FACE; the latch holds through a re-mount and releases only by its button. **TH-1 kill:** audio stops behind the face → show audio moves to the face layer, decided then. |
| 5.5 | **Hold still (DT-12).** A touch sends a hold to the neck (on-face touch, top precedence) and opens the dashboard; a person within reach makes the head finish turning, then hold. | The head does not move while touched; released when the face returns. |
| 5.6 | **The Tab5's touch functions as dashboard pages (T9):** the lift with its MANUAL gate and "why it will not move", the soft e-stop, the head-aiming pad, audio settings, telemetry — through the Thor's existing endpoints, so any browser can use them. Placement agreed with the team when built. (Any LAN browser can already move the lift through `/api/mast`; that is true today and recorded here, not changed.) | Each Tab5 touch function has a dashboard home; with the Tab5 fitted, both work. |
| 5.7 | **The Thor-down panel (DT-17):** state from the StamPLC, soft e-stop, the lift gated to MANUAL, "dashboard unreachable" — sent through the door. The page must **renew** a lift command (lease, ≤250 ms, as `MAST CMD` already expects), or the door stops sending. | With `opendubs-dash` stopped: the lift moves in MANUAL and refuses outside it; a frozen page stops the lift; the soft e-stop trips the contactor. |
| 5.8 | **`PING` reports the mode.** In CONTROLLER the face is absent: the runner's preflight refuses shows that need one (0.2), and the neck parks. | A show with FACE cues is refused, with the reason, while the tablet is in hand. |

## Phase 6 — the neck controller (DT-7)

*Bench work follows `CLAUDE.md`'s rote procedure: self-triggering tests that print their own
verdict.*

| # | Item | Kill criterion / done when |
|---|---|---|
| 6.1 | **The board and its firmware.** An Atom/Stamp-class M5 board on the head's I2C (8Servo, front ToF), on the robot's network. Ports the Tab5's `pantilt.cpp` behaviour (slew limits, bounds, boot centring, lease expiry), `head-ownership.md`'s precedence and latch, and the outbound ownership report (2.1). Answers the neck half of the protocol. | The conformance suite's neck messages pass; bench self-test prints PASS for servo range and ToF. |
| 6.2 | **Its flash path in `CLAUDE.md`'s table**, first flash and over-the-air, before it is fitted (rule 10). | The row exists and has been used once. |
| 6.3 | **RC aims the neck.** The StamPLC's RC pan/tilt target (today `TAB5IP`) points at the neck when the tablet head is fitted (0.1). | RC aims the head with the Thor stopped, and with the tablet lifted off. |
| 6.4 | **Parks when the face is absent** (5.8), and **on the e-stop branch**. | Tablet in hand → head centred and still; e-stop → no head motion. |

## Phase 7 — on the robot

*Confirm a person is at the robot before any of these.*

| # | Item | Kill criterion / done when |
|---|---|---|
| 7.1 | **Part 1 on the robot:** TS-3 and TS-4 repeated on a driving robot; 1.2–1.7 against real motion. | The mount state is right through driving, the lift, an e-stop and a lift-off. |
| 7.2 | **TB-6 with the tablet head**: a 10-minute show, start to finish, the tablet as face and audio device. | Not one interruption. |
| 7.3 | **TB-6 with the Tab5 head**, unchanged, after Phases 0 and 2. | The Tab5 configuration still passes. |
| 7.4 | **The Tab5 retirement checklist** (rule 2). On the robot, with the tablet head: the conformance suite passes; RC aims the head with the Thor stopped and with the tablet lifted off; an e-stop leaves the head still and the face showing E_STOP; with the Thor down the face shows the robot's state and the panel works; power-on to face, measured and reported beside the Tab5's; no heat warnings over a long session; a show's FACE cues mean the same on both heads; hold-still, CONTROLLER and the latch behave as DT-11/DT-12; the library syncs. | Every line passed. **Retiring the Tab5 is then a separate team decision**, not automatic. |

---

## Stub register

Everything that pretends, or names a device where it means a role, so nothing is forgotten.

| Stub | Where | Replaced by |
|---|---|---|
| The head's address hard-coded | `software/kiosk-dashboard/backend/yolo_tracker.py` `UDP_IP` | 0.1 |
| The head addressed as "the Tab5" | `state_out.py` `OPENDUBS_TAB5_HOST`; the StamPLC's `TAB5IP` | 0.1, 6.3 |
| Show requirements and audio output name the Tab5 | `software/choreo/cue.py` `REQUIRES_KEYS`, `AUDIO_OUTPUTS` | 0.2 (choreography plan) |
| "Charging" stands in for "on mount power" | kiosk bridge `battery()` | 0.3 |
| The head reports no owner outward | Tab5 firmware (`head-ownership.md` §4) | 2.1, 6.1 |
| Debounce and thresholds for the mount state | 1.1 | TS-3, TS-4, TS-7 |

## Parked

Recorded so they stop recurring as surprises, and not being worked on.

- **The back-side camera** — it faces into the mount in both configurations (DT-5).
- **A compass heading** — the magnetometer near the drive motors, and no gyroscope (rule 7).
- **Step counter, significant motion, tilt detector, Samsung motion and Smart Alert** — no robot use
  found.
- **Bluetooth/BLE, USB host, Wi-Fi Direct** — no use in this plan. The Bluetooth arcade controller
  is choreography request E16.
- **Speech recognition, TTS, echo cancellation and noise suppression on the tablet face** — listed
  among the benefits; they belong to a voice plan, after the tablet head exists.
- **Robot security** — choreography plan P1.
