# OpenDubs — Core Robot Telemetry/Viz + Punch List

_Working document. Core robot first (drive, power, safety, thermal, compute, sensors, FSM); the modular payload (mast/scissor + payload controller + CAN) is deferred — stubbed at the bottom. Status tags: `[DONE]` `[TODO]` `[HW]` (needs hardware/bench) `[DEC]` (decision needed) `[BLOCKED]`._

---

## Where the open work lives

Swept 2026-09-29. Four registers, and nothing else in `notes/` carries open items — the rest are
design records. Check here before starting a new list.

| register | open | what it is |
|---|---|---|
| **this file** | 34 | robot, deployment, security, docs infrastructure |
| [`docs-review-checklist.md`](docs-review-checklist.md) | 41 | the 2026-09-12 documentation review, now tracked so the rulings are durable. **A: 1** left (a content decision), **B: 5** still open, **C: 25** need a photo/measurement/part number, **D: 10** platform projects |
| `docs/build/*.md` | 45 | authoring placeholders — photos, Fusion permalinks, STL/STEP exports, a few TBD values. **This is the same work as bucket C above, seen from the pages instead of the list** |
| [`per-robot-calibration.md`](per-robot-calibration.md) | — | **backup of opendubs2's drive calibration.** Not a source: the values live in `main.cpp` on the robot, and `deploy-all.sh` rsyncs over it with `--delete` |
| `docs/` + code | — | no register; staleness is found by sweeping, not tracked |

Two counting traps, both of which inflated an earlier sweep:

- **`- [ ]` in `docs/build/` is usually a reader-facing verification checkbox**, not unfinished
  authoring — "Every lever-nut conductor survives a firm tug". There are 83 of those against 45
  real authoring TODOs. Do not count them together.
- **Backticked `*.md` names are invisible to `mkdocs build --strict`**, which only checks real
  links. Seven had rotted; that is how `mast-control.md` kept pointing at a document archived
  months earlier. Re-run the check when moving or archiving a doc.

## 0. AT THE ROBOT — next session (agenda captured 2026-09-29)

Everything below needs the robot's network or hands on the machine. Nothing here is desk work.

**1 · Reflash both boards from `main` and confirm.** PR #32 is merged, so `main` carries the pinch
guard *and* Andrew's tracker feedback for the first time, and builds (15.7 s). The guard was
exercised on `e00920a`; **the merged build has never run on hardware.** Confirm two things
separately: the guard still behaves at the bottom of the stroke, and the YOLO tracker still drives
the head. CH5 must be OFF to flash.

**2 · ~~Settle the contactor auto-arm.~~ DONE 2026-10-01** — implemented in the repo, applied to the
robot and verified on hardware: it keeps checking (a wrong CH5 at power-on is not a stuck state),
strobes the PRISM amber while refusing, and arms once authority reaches OFF. It arms at most once
per boot and an e-stop disables it, so nothing re-arms behind the operator.

**2b · Settle the contactor auto-arm.** It exists on the robot and **nowhere in the repo**
([deployment-drift.md](deployment-drift.md)). Decide whether it stays, then either commit it or
remove it — the next flash from the repo overwrites it either way. It blocks step 5 of
[Operating the robot](../docs/operating.md), which currently cannot state what the contactor does
at power-on.

**3 · The PWM mirror**, the other uncommitted on-robot feature. Same disposition question.

**4 · Capture `opendubs-yolo-tracker.service` into the repo** (§0c). The unit exists on the Thor
only; a rebuilt machine loses the tracker silently. The venv already covers its dependencies.

**5 · B9b — rename `rc_serial_probe`.** Ruled. 22 files plus `/opt/opendubs/rc_serial_probe` in the
Thor's systemd units, so repo and robot land together with a re-provision. Group with the deferred
`MAST CMD` rename.

**6 · Tear down the kiosk leftovers.** Removing the files from the repo did not undo what an older
`provision.sh` did to the machine: `opendubs-xorg-fix.service` is still enabled and
`/etc/X11/xorg.conf` is still immutable. Commands are in [Thor setup §4](../docs/thor-setup.md).
Leave the `kiosk` **user** — the dashboard still runs as it.

**7 · Photos for the operating page.** The rear face showing the RUN button and both emergency
stops, and the battery with its latching switch.

**8 · Housekeeping.** Remove `/home/emmar/tab5_build` on opendubs2 — a scratch build directory left
behind when the robot went off-network mid-session.

**Optional, if there is time:** measure the two RPLIDARs' and the ToF's positions in CAD
coordinates. They are in neither `sim/dimensions.yaml` nor `sim/opendubs.xml`, and they are what
stands between the model and a TF tree (§0d).

## 0a. DEPLOYMENT DRIFT — run this before trusting the robot to match the repo

- **[TODO] `tools/deploy-audit.py <robot>`** — the repo is not what is running. PR #9 pinned
  the ESP32 platform on 2026-09-01; opendubs2 never got it, and on 2026-09-08 that broke the
  StamPLC build mid-session for reasons unrelated to what we were changing. See
  [deployment-drift.md](deployment-drift.md). Also: `main.cpp` on the robot carries two
  features that exist nowhere else (PWM mirror, contactor auto-arm) — the next flash from the
  repo overwrites them, and there is no copy anywhere.
- **[DEC] A deploy path that understands per-robot values.** The audit reports drift; it
  does not prevent it. `tools/access.py` already makes the "shared vs per-machine"
  distinction for accounts; deployment needs the same idea.

---

## 0. NEXT RECONNECT — open carryover (captured 2026-08-06, robot disconnected for the day)

- **[DONE] Intermittent RC signal loss — RESOLVED (2026-08-07).** Root cause was NOT wiring: the control loop **blocked on `Serial.printf`**. `kSbusPrintEveryNFrames=1` printed a long `[SBUS] OK` line per frame (~83/s firehose); when the dashboard parser hiccuped, the USB-CDC TX buffer filled and the next printf stalled the loop 0.3–2 s → SBUS unserviced → `publishRc` emitted `[RC] live=0` → dashboard NO-LINK. Fixes: throttled `[SBUS] OK` to ~2/s (kSbusPrintEveryNFrames 1→40); `availableForWrite()` guards on publishRc/Drive/Io (drop, don't block); `Serial.setTxTimeoutMs(0)`; non-blocking BMS state machine `bmsStep()`. Verified 240 s / 521 dashboard polls: rc_link DOWN=0, never not-fresh. Principle enforced: **the control loop never waits on a telemetry consumer.**
- **[TODO][DEC] Graceful bring-down / controlled shutdown sequence.** Architect an orderly power-down: command drive → neutral + disarm, open the R0 contactor (kill motion power), park/settle the mast, then bring down the dashboard/Thor cleanly — vs. today's hard power-cut (which is what has been glitching the StamPLC/RC and hanging firmware). Define the trigger (host cmd / button / dashboard) and the firmware+backend handshake.
- **[TODO][HW] Re-pin-out of the StamPLC connections.** Plan a cleaner, documented pinout/harness for the StamPLC I/O (SBUS G5, RS485 UART1 G0/G39/G46, PaHub Port A G1/G2, DI/relay, CAN, PoE). Goal: robust connectors + strain relief + a labeled map, to kill the marginal-connection class of failures (likely tied to the RC-loss issue above). Produce a pinout doc + re-terminate.

- **[TODO][DEPLOY][SECURITY] Configure the router + on-robot services per the security docs before any untrusted exposure.** Apply `docs/network-architecture.md` → "Secure it before you expose it": add dashboard auth; bind rosbridge (`:9090`) + Foxglove (`:8765`) to the LAN and firewall them off the WAN; default-deny inbound at the router WAN; VPN (WireGuard) for remote/admin; guest SSID/VLAN isolated to the app surface only. (Designing the auth *scheme* is the separate [[opendubs-reconnect-todo]] #9 credentials item; this is the config-execution checklist.)

### Docs pre-share cleanup (audit 2026-08-07)
- **[DONE]** Archived 5 superseded docs → `notes/history/` (control-architecture, power-architecture, data-power-flow, hardening-roadmap, telemetry-spec) with banners + `history/README.md`; repointed all dangling links. Security-redacted the live tree (VNC pw → `OPENDUBS_VNC_PASS`; removed home IPs, SSID "Cat Sounds Central", SSH user/key path, Thor MAC). Rewrote root `README.md` + `docs/index.md` first-impression (Pixhawk removed; StamPLC-SBUS + three-rail + charger baseline stated).
- **[DONE 2026-09-29] Minor content-staleness pass** — Pixhawk/MAVLink is out of `docs/`
  entirely (`docs/history/` was never created; the archive lives in `notes/history/`).
  `pymavlink` is gone from `requirements.txt` and `portability-audit.md`. Firmware README tag
  list now carries `[RC]`/`[DRIVE]`/`[BMS]`/`[BMSC]`. `camera-selection.md` states what is
  actually fitted and both "de-facto"/"the standard" claims are gone. `mast-control.md`'s
  Z-axis framing rewritten, and its dangling reference to the archived control-architecture
  doc removed.
- **[TODO] Vestigial MAVLink in *code*** — out of scope of the docs pass, so never done:
  `status.py:3` still describes itself as gathering Pixhawk/MAVLink health and `:413` returns a
  hard-coded deprecated stub; `opendubs-dash.service:17` still exports
  `OPENDUBS_MAVLINK_DEV=udpin:127.0.0.1:14550`; the frontend references it in `index.html` and
  `app.js`. Also `notes/capability-backlog.md` still says "shore" in three places.
- **[DONE 2026-09-29] Engineering reconciliations.** (1) *Regen clamp* — **one clamp feeding
  both ESCs**, as built. `regen-clamp-sizing.md` §1 rewritten: the 80 A worry came from the
  datasheet stall figure, and measured draw is under 10 A per ESC pair for 20–30 A total, well
  inside the ~80 A rating. Fuse independence *is* given up and is recorded as the reason to
  revisit if the current envelope grows. (2) *Router SKU* — resolved by generalising:
  `network-architecture.md` names no SKU, so the fielded hAP ac3 needs no correction.
  (3) *StamPLC→ROS bridge* — the docs were right and this note was the stale thing:
  `opendubs-stamplc-bridge.service` runs `stamplc-bridge.sh` → `stamplc_bridge.py`, polling
  `/api/stamplc` over HTTP. `stamplc_udp_bridge.py` sits in the tree unused — **[DEC]** keep as
  an alternative or delete.
- **[TODO] Replacement docs for the archived power + control architecture** — the live `docs/` tree now has no standalone power-architecture or control-architecture write-up (current facts live in `system-overview.md` + code). Draft concise current-baseline versions.
- **[TODO] Git hygiene before first commit** — vendored `M5*` repos carry nested `.git` dirs (strip or convert to submodules; root README already flags this); `.gitignore` is thin but covers `.pio/.venv/.env*`.
- **[TODO] Diagrams + CAD: decide embed vs. static, one at a time.** The generic *embed facade* (static poster → click loads a live embed; `docs/javascripts/embed-facade.js` + `.embed-facade` CSS + `data-embed` attr + `--embed-aspect`) now serves BOTH Figma boards and Autodesk Fusion 3D viewers. Live so far: the bus map (`system-overview.md`, Figma) and the Base Frame CAD (`build/base-frame.md`, Fusion `?mode=embed`). Go through the rest and decide per item: interactive embed vs. plain static.
    - **Figma diagrams:** StamPLC buses, StamPLC relays/inputs, power-architecture, e-stop, rc-control-mapping, otos-wiring-harness, rc-rcvr-wiring. Consider: board node ready+public? text-heavy enough to need pan/zoom? does exposing the rest of the board matter? (Bus map deliberately shows both core + payload-connected — wanted there, maybe not elsewhere.)
    - **Fusion CAD:** electronics-bay (`a360.co/4wDfT1I`) and any other module shares — resolve each share's `mode=embed` URL (like the base frame's) and facade it. Tradeoffs: external Autodesk dep, needs public share, per-version snapshot (re-share on change), no offline (poster is the fallback).

---

## 0c-ii. The dashboard's AI TRACKING button — fixed 2026-10-01

Andrew reported it "doesn't work". The endpoint existed; what it did was wrong.

It `subprocess.Popen`'d `yolo_tracker.py` from the backend with the Tab5's address hardcoded as
`10.42.2.14`, ignoring the systemd unit. Three failure modes: a second copy fought the service for
the camera and lost, because a RealSense admits exactly one client; the hardcoded IP broke whenever
DHCP moved the head; and a tracker started by systemd at boot was invisible to the button. A
shutdown hook also killed the tracker whenever the backend restarted — which happened repeatedly
during a day of redeploys.

It drives `systemctl start|stop opendubs-yolo-tracker` now, with a sudoers rule for exactly those
two verbs, and `ai_tracking` is read from `systemctl is-active` rather than remembered — so the
label cannot claim the tracker is running after it has died, nor claim it is stopped while it
tracks. A missing sudoers rule returns 503 with the required rule in the response instead of
failing silently, which is how this got described as "doesn't work" in the first place.

Verified on the robot: `off → inactive`, `on → active`.

## 0c. THE YOLO TRACKER SERVICE IS NOT IN THE REPO — and provisioning used to delete it

Found 2026-09-29 while retiring the kiosk.

- **[DONE 2026-09-29]** `kiosk-dashboard/deploy/provision.sh` carried
  `systemctl disable --now opendubs-yolo-tracker.service` followed by
  `rm -f /etc/systemd/system/opendubs-yolo-tracker.service`. Running `provision-all.sh` therefore
  **destroyed the working face tracker**, while `deploy-all.sh` went on listing
  `opendubs-yolo-tracker` in its service readout — so the report would show it inactive and the
  cause would look like a crash. Those two lines are gone.
- **[DONE 2026-10-01] Captured.** `kiosk-dashboard/deploy/opendubs-yolo-tracker.service` is in the
  repo and `provision.sh` installs and enables it, alongside a persistent `/opt/opendubs/models`
  so the weights survive a reboot (the path was `/tmp`, so every boot re-downloaded them from
  Hugging Face and the tracker could not start offline). Verified on the robot that the only thing
  missing was the unit: the script was deployed, the venv already had pyrealsense2 / ultralytics /
  torch / cv2, and nothing was holding the camera.
- ~~**[TODO][HW] Capture the unit into the repo.**~~ There is no `opendubs-yolo-tracker.service`
  anywhere in the tree, so the service running on the Thor was installed by hand and **cannot be
  reproduced**. A rebuild of that machine loses the tracker. Copy the live unit off the Thor into
  `kiosk-dashboard/deploy/`, and have `provision.sh` install and enable it. The venv already
  covers it — `backend/requirements.txt` carries `ultralytics`, `opencv-python` and
  `pyrealsense2` — so the gap is the unit plus one runtime detail: `yolo_tracker.py` fetches
  `face_yolov8n.pt` from Hugging Face on first run, so a freshly rebuilt robot needs either the
  network up or the model pre-cached. Needs a session on the robot's network.

## 0b. TABLET UI — its own project, not a doc fix

Captured 2026-09-29, when the kiosk panel was confirmed retired. The Android tablet is now the
**only** operator interface, which promotes a set of previously-discussed annoyances from cosmetic
to load-bearing. **This is a project with firmware, backend and UX parts — do not start it as a
side errand inside a documentation pass.**

- **[TODO] The screen sleeps while the robot is in use.** The tablet blanks mid-operation. Needs a
  deliberate keep-awake while a session is live, and a decision about the mechanism: the Wake Lock
  API from the page (simple, needs a user gesture and a secure context — note the dashboard is
  served over plain HTTP today, which the API may refuse), an Android kiosk/launcher setting, or a
  developer-options screen-timeout change. The choice interacts with the authN work in §0, since
  HTTPS may be a prerequisite.
- **[TODO] Use the tablet's camera.** It is a camera the robot already carries to every demo, with
  no USB cost and no bandwidth against the Thor's controllers. Open: what it is *for* — an operator
  view, a second perception source, or documentation capture — and whether frames go anywhere near
  ROS or stay in the browser.
- **[TODO] Use the tablet's audio.** Speaker and microphone. Overlaps the Tab5, which already owns
  sound: decide which device is the robot's voice, and whether the tablet's mic implies
  speech input as a control source — which would have to pass the same failsafe-gated contract as
  every other source.
- **[TODO] Tablet-first styling** (the checklist's **B4b**). `frontend/style.css` is written
  base-first for a 1920x480 strip panel with the tablet on a `@media (max-width: 1600px)`
  fallback, so the tablet runs the secondary branch of the stylesheet. Make the tablet the base
  case and delete the strip-panel path.
- **[DEC] Which tablet, and is it in the BOM?** No display, screen, panel or tablet line exists in
  `parts.csv` or `hardware-bom.md`. If the tablet is required to operate the robot, a replicator
  needs to know what to buy; if any Android tablet does, the docs should say that instead.

Prerequisite for none of the above, but related: **0a/B4a** retires the dead kiosk code that the
tablet replaced.

## 0d. ISAAC ROS — what has to be true before the ROS layer's shape is settled

Raised 2026-09-29 against checklist **B11**. The structural question (one package + launch files
vs. today's 8 systemd units around loose scripts) was deliberately **not** answered, because
committing to Isaac ROS across the robot largely decides it. These are the prerequisites, and
most are not documentation.

**Two hard blockers. Nothing in Isaac ROS is useful until both are cleared.**

- **[TODO] ROS cannot drive the robot.** There is **no `/cmd_vel` subscriber anywhere**. Nav2's
  entire output is `/cmd_vel`, so there is nothing for it to talk to. The path has to be a
  network source claiming a lease through the arbiter — the same treatment as a tablet-triggered
  routine, per [control authority](../docs/control-authority.md) — not a private channel to the
  ESCs. `MAST CMD`'s 250 ms expiring intent is the pattern to copy.
- **[TODO] ROS does not know where the robot is.** There is **no `/odom` publisher**. Pinpoint
  odometry reaches the StamPLC and stops there as an `[ODO]` telemetry line the dashboard renders.
  `software/choreo` already refuses to move the base for exactly this reason. cuVSLAM and nvblox
  both need odometry *and* TF.

**Then the graph problems.**

- **[TODO] TF tree.** Three frames — `laser`, `laser_rear`, `tof_front_link` — are published with
  no transform to `base_link`, so nothing can be fused. The geometry is **not** the hard part:
  `sim/dimensions.yaml` is the datum source and `sim/opendubs.xml` already places the D415 in CAD
  coordinates with a `d415_face` site. What is missing is (a) the two RPLIDARs and the ToF, which
  are in neither file and need CAD positions, and (b) a format — the model is **MuJoCo MJCF**, and
  `robot_state_publisher` wants URDF. Generating both from `dimensions.yaml` keeps one source of
  truth; hand-authoring a URDF creates a second place the geometry lives.
- **[DEC] The D415 is owned by one process.** `yolo_tracker.py` opens it directly through
  `pyrealsense2` inside the dashboard backend. A RealSense admits a single client, so
  `realsense-ros` **cannot** publish that camera while the tracker runs. Either the tracker moves
  to subscribing on ROS image topics, or the camera stays out of the Isaac graph. This is a fork
  in the road, not a detail.
- **[TODO] Containers change the structure question.** `docs/thor-setup.md` already records that
  Isaac ROS runs from NVIDIA's containers rather than host `pip`, because CUDA/cuDNN/TensorRT are
  pinned to the JetPack version. So "one systemd unit running a launch file" becomes "a systemd
  unit running a container that runs a launch file", and our package has to build inside it.
- **[TODO] NITROS is a type contract, not a flag.** GPU-resident image transport only works
  between NITROS-capable endpoints, which in practice means composable C++ nodes in a shared
  component container. Python nodes cannot join the zero-copy path — they belong at the edges of
  the graph, bridging to hardware. That pushes the architecture toward launch files and component
  containers regardless of taste.
- **[TODO] DDS is at defaults** — domain 0, localhost, FastDDS. Fine for one host; needs a
  standardised domain ID and RMW before a second machine or a container joins.

**Already done** (2026-09-29): topics and services are named by function, the duplicate
`stamplc_udp_bridge.py` is gone, and `/zaxis/{up,down,stop}` gives ROS a guarded lift path where
previously the only service on offer was a direct relay write the backend refuses.

## 1. Data-source map (after the architecture changes)

| Source | Provides | Transport | Notes |
|---|---|---|---|
| **StamPLC** (base controller / hub) | RC/SBUS, safety DI/relay, drive cmds+mode+armed, PDH, BMS, 24V-rail sense, thermal (ENV/fan), I²C | USB serial → dashboard; UDP :5005 → ROS | The single hub for almost everything |
| ↳ SBUS RC | channels, authority(CH5), deadman(CH6), speed(CH8), drive fwd/rot/strafe (CH3/CH1/CH4) | on StamPLC | **replaces the Pixhawk RC** |
| ↳ REV PDH | per-channel current, faults, total V/I/P | CAN → StamPLC | keep for now (may → dumb fusebox) |
| ↳ ECO-WORTHY BMS (52V pack) | SoC, pack V/I, cells, temps, protection, charge state | **RS485** → StamPLC | new; the source rail |
| ↳ **Logic 24V bus** (always-on) | rail V/I | sense → StamPLC | own 52/24 step-down; StamPLC/Thor/USB hubs/sensors; **NOT** behind contactor (brain survives e-stop) |
| ↳ **Drive 24V bus** (behind contactor) | rail V, regen-clamp-active | ADS1115/PDH ch + DI | own 52/24 step-down; base drive motors + arm power stubs + lift; **e-stop/contactor kills it**; the ODrive clamp lives here |
| ↳ Thermal | per-zone temp/humidity, fan state | ENV units on PaHub + fan MOSFETs | new |
| **Thor** | CPU/GPU/mem/temp/power, disk | tegrastats/local | existing |
| **RPLIDAR** | /scan | USB → ROS | `/dev/ttyUSB0` now enumerated |
| **Fused pose** (localization) | x/y/heading, velocity, tilt | **Thor/ROS (EKF)** | fuses Pinpoint(ODO+IMU) + GPS+IMU + visual odom + others; replaces Pixhawk GPS/flow/IMU |
| ↳ Pinpoint | odometry + IMU | I²C (PaHub CH0) | a fusion input |
| Network | wired/wifi/ssid | local | existing |
| ~~Pixhawk/MAVLink~~ | ~~RC/GPS/IMU/flow/batt~~ | — | **ELIMINATED** — see §3 IMU gap |

---

## 2. Dashboard target shape (core tabs)

| Tab | Shows | Source | Change from today |
|---|---|---|---|
| **MASTER STATUS** | FSM state, mode inputs, subsystem rollup, safety banner | robot_state | + **charger interlock**, + **drive mode**, + **armed** state |
| **INFO** | detailed status grid, source health, net, compute | status.py | "Pixhawk/MAVLink" row → **"StamPLC RC link"** |
| **POWER** | **three rails**: 52V pack (SoC, cells, I, temps, protection, charge state, **charger interlock**) + **logic 24V** (V/I, always-on) + **drive 24V** (V, **clamp-active**, **contactor state** — e-stop kills it) + PDH channels | BMS + 2× rail sense + PDH | major rework (three-rail + BMS panel) |
| **DRIVE** (was RC TELEM) | RC inputs, mixer output per-wheel, **drive mode** (mecanum/4WD/2WD), armed, decel/ramp | StamPLC SBUS + drive | RC source PWM→SBUS range; add mode/armed/per-wheel |
| **THERMAL** (new) | per-zone temp/humidity, fan on/off, setpoints, overtemp | ENV/fan | new tab |
| **THOR** | Jetson health | tegrastats | unchanged |
| **STAMPLC** | safety DI/relay, I²C, SBUS stats, temp, e-stop/re-arm | StamPLC | unchanged (still core safety I/O) |
| **NAV** | **fused pose** (x/y/heading, velocity, tilt) + per-source health + LIDAR | Thor/ROS EKF (Pinpoint+GPS+visual+…) + RPLIDAR | becomes a localization view: fused estimate + contributing sources |
| **LIDAR** | scan viz | RPLIDAR | unchanged |
| **PAYLOAD** (was MAST) | type-driven lift panel | payload controller | **deferred** (payload buildout) |
| **ARMS / TELEOP** | placeholders | — | unchanged |

---

## 3. Decisions

**Resolved:**
- **RC data path** — RC telemetry is **advisory** (the StamPLC mediates RC in→out and is the authority; the Thor/dashboard/network are listeners only). So it's non-safety-critical: firmware emits an `[RC]` line on **serial** (for the dashboard's `stamplc.py`) and RC is already in the **UDP** broadcast (for the Thor + network listeners). Both consumers covered, one firmware change.
- **Mixer location** — **StamPLC firmware** (RC already there; one place for arming + decel-ramp + drive-mode).
- **IMU / localization** — resolved by adopting **sensor fusion on the Thor/ROS (EKF)**: fuse **Pinpoint (ODO+IMU) + GPS+IMU + visual odometry + others** into one pose estimate. IMU is firmly IN (multiple); tilt/tip can come from the fused state. NAV becomes a localization view; the fused pose is a **Thor/ROS** telemetry source, not the StamPLC.

- **REV PDH** — **keep for now.** Design the POWER tab to degrade gracefully if it's later dropped.
- **Regen clamp** (on the DRIVE 24V bus) — the ODrive board is an **ideal-diode power path + regen clamp** (Vin=drive-24V DC/DC → Vout=motor bus, ~25mV drop, reverse-blocking). **Self-referencing, nothing to set:** brake fires when **Vout − Vin ≥ 2.35–2.5V**, releases at **≤ 0.5–0.75V** (~1.6V differential hysteresis). At 24V supply it holds the motor bus at **~26.35V** during braking — auto-safe under the 2x80A 30V max. The reverse-blocking diode is what protects the unidirectional DC/DC from regen (answers the goBILDA "switching supply needs a suppressor" warning). Resistor = **PATIKIL 100W 1.5Ω** → **~17.6A / ~463W peak** at clamp; within the board's **20A free-air Brake Current** rating (40A w/ heatspreader), fine for intermittent braking (100W continuous, ~22% duty). Onboard 800µF bulk. **Bench-check — does it self-power from Vout? (one property, two faces):** if yes, (a) it dynamically brakes during an e-stop coast-down (contactor open) — a safety plus; but also (b) it adds **speed-proportional drag when manually pushing the unpowered robot** — with the system off, Vin=0 so the clamp trips at ~2.35V (not 26.5V), and the **99.5:1 gearbox multiplies the motor braking up to the wheel**, so it's noticeable. Today's "hard to push" is mostly the gearbox (unpowered ESC = open windings, no electrical brake); the clamp only adds to it if it self-powers. If manual-push drag is a problem: a **manual-move disconnect between the clamp and the ESC bus** (the e-stop contactor won't do it — the clamp sits downstream of the contactor). _Placement (1 clamp at the DC/DC output vs 2 per-ESC) is still open — gated on the §7 scope measurement of combined ESC current vs the clamp's 30A cont/60A peak; doc §1 "80A" is an error, datasheet says 60A peak, and the "28V Thor ceiling" is stale (Thor is on the separate logic rail)._
- **Power rails (clarified 2026-08)** — two INDEPENDENT unidirectional DC/DCs off the 52V pack: (1) a **20A DC/DC → logic-24V** rail (Thor + StamPLC + router + sensors), always-on, upstream of the e-stop contactor; (2) a drive DC/DC → **drive-24V**, behind the GIGAVAC contactor. The Thor is **double-isolated** from drive regen (the drive DC/DC being unidirectional already blocks regen back to the pack, and the Thor sits behind its own separate converter). ⇒ the drive-bus overvoltage ceiling is the **ESC 30V** (+ drive DC/DC tolerance), NOT any "Thor 28V ceiling" (that framing is stale).
- **Logic-leg charger isolation (2026-08)** — problem: with the battery's main switch OFF, charger power sneaks through to the always-on **logic-24V** electronics (via the logic DC/DC). The drive leg is already broken by the GIGAVAC contactor; the logic leg needs its **own hard break, independent of the e-stop loop** (it must still survive e-stop once on). **Placement sets the spec:**
  - **RECOMMENDED — break the 24V output bus:** switch sees only 24V → an easy **30V/15A DC** part (e.g. a DC-rated marine/battery switch, Blue Sea m-Series). Electronics go dead even with the charger in; the logic DC/DC just idles off the charger (a few W while charging — acceptable).
  - **Alternative — break the 58V DC/DC input** (only if zero idle-draw when parked is wanted): needs a **60V+/10A DC** part — harder category: an **e-bike/e-scooter key switch** (48–72V, verify DC rating), a **solar DC isolator** (IMO / Santon / Kraus & Naimer / ABB OTDC, 500–1000V / 16–32A), or a **100V N-FET + high-side driver** (e.g. LTC7000) under MCU control.
  - **DC-rating gotcha:** spec the explicit DC rating. AC-rated rockers/relays (contacts typically DC-rated ~14–48V) **weld** on a 58V bus (DC arcs don't self-quench); they're fine on the 24V bus. This is why marine switches (48V DC max) are marginal at 58V and ordinary automotive relays are unsafe there.
  - Current note: Thor (~8A) + router (~1.6A) + StamPLC/sensors are **24V-side** loads (~10A@24V); reflected to the 58V input that's only ~4–5A.
  - **Future/fleet option — M5 "power guardian":** a low-power ESP32 node that **sips from the pack via a low-Iq buck (NOT a babysat battery)**, drives a **default-off FET** on the logic leg, owns BMS comms (RS485/CAN) so battery telemetry survives the robot being off, and reports SoC/health to an **authenticated (TLS) cloud endpoint** for fleet mgmt (WiFi when the logic bus is up, else UW-MPSK; store-and-forward). **Non-negotiable: a manual override** so a hung guardian can't brick power-on (same watchdog lesson as the StamPLC). Solves the switch parts-hunt (control problem, not exotic part) and enables fleet telemetry; scope it as guardian-as-power-controller first, telemetry/cloud later.
- **Thermal / ENV I²C** — plenty of room: the unique-address devices (Pinpoint 0x31 / 8Servo 0x25 / ExtEncoder 0x59) can sit directly on Port A, freeing PaHub channels for **3+ ENV units** (each on its own channel; they share 0x44). Keep to **5V-tolerant M5 Grove ENV** units on the 5V bus.

**Note (2026-09-29):** items (1) and (2) below are **resolved and built** — they are recorded here
only because the list they sit in is from 2026-08. (1) One regen clamp, shared by both ESCs; see
`docs/regen-clamp-sizing.md` §1, where the 80 A objection proved to be the datasheet stall figure
against a measured 20–30 A. (2) The logic-leg break is **implemented as the RUN latching button**.
Its operating consequence — the BATTERY switch must be on whenever the charger is connected, or the
charger regulates against the logic bus instead of the pack — is documented in
[Operating the robot](../docs/operating.md). Item (3), the M5 power guardian, remains open.

**Open (as written 2026-08):** (1) regen-clamp count/placement — 1 @ DC/DC output vs 2 per-ESC, gated on the §7 scope measurement of combined ESC current vs the 30A cont / 60A peak clamp rating (+ confirm clamp self-powers from Vout, which drives both coast-braking and manual-push drag); (2) logic-leg break placement — 24V-output (recommended) vs 58V-input; (3) whether to build the M5 power-guardian (fleet telemetry) now or later. Plus bench verification (clamp trip range, final zone count).

---

## 4. Punch list

### A. StamPLC firmware
- [DONE] Software drive mixer (`driveTick()`): mecanum + arcade(4WD) + diff(2WD) → 8Servo ch1-4 → two 2x80As. Compiles clean. **Still need to remove the hardware mixer (wiring).**
- [DONE] 2x80A arm sequence (2s neutral-hold once the gate is satisfied) → `[DRIVE] armed=`.
- [DONE] Deceleration slew-rate limiter (25µs/tick) + center deadzone.
- [DONE] Drive-mode from DI7 jumper (mecanum/skid, wheel-swap-interlocked) + `DRIVEMODE` bench override; mode+armed in `[DRIVE]`.
- [DONE] **Drive safety gate** (B): manual|auto-authority ∧ contactor ∧ !estop(local/DI0) ∧ rc_live ∧ !failsafe; deadman gates output; fail-to-neutral; hardware contactor = true kill. **Gated behind `DRIVE_ENABLE` (default 0)** — logic + telemetry run always so it's bench-verifiable with no PWM to the ESCs.
- [DONE] **Autonomous provisioning**: pluggable command source — under RC_AUTO, host `DRIVE <fwd> <rot> <str>` from the Thor replaces the RC sticks; message timestamp = autonomous heartbeat (250ms → neutral).
- [DONE] **Task-watchdog** (`esp_task_wdt`, 8s, fed at loop top) — a firmware hang auto-resets the chip (recovers the silent-hang failure mode). Live-verified.
- [DONE] Emit RC channel values (`[RC] live=/mode=/fs=/rng=/…/raw=<16ch>`) @10Hz for the dashboard RC/DRIVE display — separate from `[DRIVE]`. Live-verified.
- [DONE] **SBUS range-sanitize** (CH1–CH4 must be in [160,1840]) + failsafe threshold consolidated to one `kRcTimeoutMs` — a stick pegged out-of-band (e.g. a receiver failsafe-preset at 0) is treated as FAILSAFE and blocks the arm gate even without the SBUS failsafe flag. _Receiver-side follow-up: set the TX/RX failsafe presets so all sticks park at NEUTRAL, esp. CH3._
- [DONE] RS485 BMS reader → `[BMS]`/`[BMSC]`. **JBD @ 9600** (RS485 on UART1: TX=G0/RX=G39/DIR=G46 half-duplex). `bmsPoll()` auto-polls 0x04 cells + 0x03 basic-info every 1s, decodes, emits the lines the dashboard parses — **verified live end-to-end**: SoC 98%, 53.26V, 16 cells (3.326–3.331V, 5mV Δ), 22.8–24°C, no faults, POWER page pack panel populated. Interactive probe (`BMS`/`RS485TX`/`BMSBAUD`) retained behind `BMSAUTO 0`. _Note: current sign is JBD-native (+chg/−dsg); verify against a real charge/discharge and flip in `bmsPoll()` if inverted._
- [TODO][HW] Rail sensing: **logic-24V** V/I + **drive-24V** V + clamp-active DI + contactor state → telemetry (two rails).
- [TODO][HW] ENV read (per PaHub channel) → `[ENV]` + fan thermostat (2-wire on/off MOSFET, hysteresis) → `[FAN]`.
- [DONE] Charger interlock reworked: "shore" concept dropped everywhere; charger state now = BMS charge-current ("charging"/idle → drive inhibit). **[TODO][HW]** for a true "cable present at 0A" (full-but-plugged): add a charger-present sense line to a DI — no such signal exists today (DI3 reads false plugged in; JBD `chgr` is just `charging`).
- [KEEP] PDH CAN telemetry (working).
- [DEC][HW] IMU add.

### B. Backend (dashboard) — SCAFFOLDED 2026-08 (off-hardware; degrades to "no data" until firmware emits)
- [DONE] RC rewired: authority/deadman/link/channels from StamPLC SBUS in `status.py._robot_state()` + `try_estop_clear()` + `telemetry.py`; `supervisor` readiness now keys on `control_ok` (RC link).
- [DONE] Retired `mavlink_rc.py` (nothing imports it → thread never starts); MAVLink health replaced by `_rc_link()`; `mavlink` key kept as a deprecated stub.
- [DONE] `stamplc.py`: `[BMS]/[BMSC]/[ENV]/[FAN]/[RAIL]/[RC]/[DRIVE]` parsers (token `[TAG] k=v`; contract documented in-file). Verified against sample lines.
- [DONE] `_pack()` (BMS) + **three-rail power model** (`pack` + `logic_rail` + `drive_rail` w/ contactor + clamp) in `collect_fast().power`; `battery` kept as back-compat alias.
- [DONE] Charger interlock (`_charger()`) → `robot_state.derive(charging=…)` → SAFE_IDLE "charging — drive inhibited" (e-stop/faults correctly override). Verified.
- [DONE] `_drive()` (mode/armed/wheels) + `_thermal()` (zones/fans) blocks; OVERTEMP folded into `robot_state` faults.
- [TODO][BLOCKED] **Fused pose** from ROS (subscribe to the EKF pose/tilt on the Thor) → NAV localization block + tip/tilt into `robot_state`. Needs the ROS layer + a backend ROS interface.

### C. Frontend (dashboard) — BUILT + VERIFIED 2026-08 against a local backend (RC sim); no console errors, graceful no-data
- [DONE] POWER: three-card layout — 52V pack (SoC ring, one-line cell strip, signed current, protection/charge badges) + **Usage · Projection** (SoC sparkline on canvas + time-to-full/empty from `pack.usage`) + 24V rails (logic always-on, drive w/ contactor+clamp, charger-interlock, PDH summary). Jetson rails moved to THOR.
- [DONE] DRIVE tab (from RC TELEM): two-variant **mode selector** (mecanum/4WD, jumper-interlocked, change directions + robot-image slots), per-wheel chassis diagram, live SBUS RC bars.
- [DONE] MASTER STATUS: charger / drive-mode / armed chips added to MODE INPUTS.
- [DONE] THERMAL tab: ENV zone cards (temp/humidity/fan) + free component-temp chips (Thor `/sys` + StamPLC board + BMS pack).
- [DONE] INFO: Pixhawk tile → **STAMPLC RC (SBUS)**; battery tile → `power.pack`. Renderers isolated per-frame (one failure can't blank the rest).
- [DONE] Rename MAST→PAYLOAD tab.
- [TODO] Drop `frontend/assets/robot-mecanum.png` + `robot-4wd.png` (the two Fusion renders) into the DRIVE mode-image slots (slots + README in place).
- [TODO][BLOCKED] NAV: localization view — fused pose (x/y/heading), tilt, per-source health, over the LIDAR scan (needs the ROS fused pose).

### D. Hardware / wiring
- [TODO][HW] ODrive regen clamp + brake resistor across the 24V rail (trip ~27–28V; size resistor to worst-stop energy).
- [TODO][HW] 24V-rail voltage sense (ADS1115 / PDH ch).
- [TODO][HW] RS485 wiring StamPLC ↔ BMS.
- [TODO][HW] **Logic-leg power break** (charger-isolation) — maintained disconnect on the logic-24V leg, independent of e-stop. Default = break the **24V output bus** with a DC-rated 30V/15A switch (marine/battery switch); alt = 58V-input break (ebike keyswitch / solar isolator / FET) only if zero idle-draw wanted. **Verify DC (not AC) rating.** See §3 decision. (Possible future: M5 power-guardian drives a default-off FET + fleet telemetry.)
- [TODO][HW] ENV units on PaHub (channel map) + fan MOSFETs (Thor / DC-DC / motor-controller zones).
- [TODO][HW] 3rd 2x80A for the mast lift (payload-side).
- [DONE] Anker hub moved to Type-A host port (touch/LIDAR/StamPLC now enumerate).
- [DEC][HW] IMU.

### E. Deploy / ops
- [DONE] HDMI panel display (forced xorg.conf, immutable, `opendubs-xorg-fix.service`).
- [DONE] Touch (USB-port fix + in-session evdev→XTEST bridge, `TOUCH_ROT=right`).
- [DONE] Kiosk dashboard deployed (kiosk autologin, `opendubs-dash` backend :8080, Chromium kiosk).
- [DONE] Dashboard reload-loop fix (launch-once + `pgrep` guard).
- [DONE] Today's live changes captured to repo (`touch_bridge.py`, `session-panel.sh`, `opendubs-panel.desktop`, `dashboard-autostart.sh`).
- [TODO][BLOCKED] Reboot-test the Thor (panel + kiosk come back unattended).
- [TODO] Reconcile the manual AGX deploy into `provision.sh` (currently hand-done; conflicts with old-Thor xorg/touch parts).
- [TODO][BLOCKED] Deploy `ros/stamplc_udp_bridge.py` (written, not deployed) + systemd unit.
- [TODO][BLOCKED] RPLIDAR ROS node (`/dev/ttyUSB0` now present); rosbridge + foxglove bring-up.
- [TODO][HW] **Localization/fusion (ROS on Thor)**: EKF (e.g. robot_localization) fusing Pinpoint(ODO+IMU) + GPS+IMU + visual odom + others → published pose/tilt the dashboard subscribes to. Sensors + calibration are a bench task.

### F. Payload buildout (DEFERRED)
- [TODO] CAN payload protocol spec (command/telemetry frames + identity/discovery).
- [TODO] M5 payload controller (mast: PWM+encoder+limits; scissor: relay-toggle state machine + feedback).
- [TODO] Backend payload abstraction + type-driven LIFT panel.
- [TODO] Scissor momentary-toggle safety (known-state init, feedback to confirm motion).

---

## 5. Suggested sequence

**Off-hardware now (repo code):** backend two-bus power model + charger-interlock in `robot_state` + drive-mode/armed fields + `stamplc.py` parser stubs (`[BMS]/[ENV]/[FAN]/[RC]`) behind graceful "no data" — so the wiring is ready and the UI renders empties. Frontend POWER two-bus + THERMAL + DRIVE scaffolding.

**On-hardware (next Thor session):** reboot-test; flash firmware (RC emit + mixer + arming + BMS/ENV/rail/interlock); confirm live data flows; then RPLIDAR/rosbridge/foxglove.

**Then:** payload buildout (§F), starting with the CAN protocol.
