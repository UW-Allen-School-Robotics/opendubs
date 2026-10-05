# Work plan — Tab5 on ESP-IDF, and the face that justifies it

The plan of record. Work proceeds through these phases in order; anything not in a phase is not
being worked on. Written because this effort has repeatedly gone sideways into benchmarking and
re-deriving things, and the cost of that was not the time, it was the credibility of every "we
can't do X" that followed.

## Standing rules

1. **No robot conclusions from bench results.** The bench Tab5 has no Thor, no StamPLC, no pan/tilt,
   no front ToF and no e-stop branch. It can answer questions about the Tab5 *as a device*. It
   cannot tell us anything about the robot, and a bench result must never be reported as one.
2. **Every experiment states its question and its kill criterion before it starts.** "Let's try it
   and see" is how the last sitting was lost.
3. **A log line is not a physical event.** `blip()` returning does not mean a sound was heard. If a
   claim is about the physical world it needs a physical observation, and if nobody is at the bench
   there is no observation.
4. **Confirm presence before any check that needs a human.** Do not engineer around the human;
   ask whether they are there.
5. **Flashing and serial telemetry are rote** — `CLAUDE.md`. Not subjects for investigation.
6. **The Arduino firmware stays the shipping image** until Phase 5 signs off. It is not deleted,
   and the robot is not cut over mid-season. **This is unchanged by 1.1 passing**: the IDF route is
   now the target, but "target" and "shipping" are different things, and the robot is wanted
   working.

---

## Phase 1 — Bench feasibility and design study

*No robot, no Thor. This is about what the Tab5 can do in its position on the robot, and about
getting the function and the aesthetics right. Trying things is the point; untracked trying is not.*

| # | Question | Kill criterion |
|---|---|---|
| **1.1** | ~~Does LVGL + PPA give better eye animation than we could hand-build?~~ | **PASSED, 2026-10-05 — "a clear unequivocal go for abandoning the bespoke approach".** Judged materially better on the desktop build, then built at 2.76 MB and **animating on the Tab5**. `smooth_ui_toolkit` is a motion.dev-style system — springs, easings, sequences, delay/repeat, completion callbacks — against one exponential lerp. **The IDF route is the target architecture; the bespoke M5GFX face is superseded.** |
| 1.2 | **Eyes and emotes.** Core geometry and rendering settled in 1.2a; remaining sub-items below: layered, PPA-blitted, shape-based gaze, saccadic motion already specified. | Judged by eye, by Emma. Nothing else can judge it. |
| 1.2b | **Replace the M5Stack splash with an OpenDubs splash.** The boot currently shows the Tab5 vendor splash. `app_startup_anim` is the demo's own startup animation app and is the natural place; the robot should not boot into someone else's branding. | Looks like our robot, not a dev board. |
| 1.2a | ~~Eye geometry~~ | **DONE, 2026-10-05** — "generally this is much better". 480×480 (41.5 mm) at a 0.34 eye-width gap, radial body shading, soft radial catchlight **inside the pupil**, `lv_line` eyebrows with sprung raise and angle. Human-scale IPD was tried and **rejected**: it read human but less friendly, so gap-as-a-fraction-of-eye-width is the dial and 0.34 is the value. Minor tweaks deferred. |
| 1.2c | ~~State machine port~~ | **DONE, 2026-10-05.** All seven states ported verbatim from `ui_shared.cpp`, each of the seven `Expr` parameters on its own `AnimateValue`. `tilt` drives both the brow angle and the upper-corner cut, `bow` carves the lower lid, `squint` eats height and shifts the centre, `lookY` biases vertically, and state colour animates too. Cuts and bows are **hidden** when their parameter is ~0, so unused states cost nothing. State currently cycles on the bench; Phase 2 wires `STATE <NAME>` from the StamPLC. |
| 1.2d | ~~Perimeter band~~ | **DONE, 2026-10-05.** Full perimeter, 16 px, 48 opacity-animated segments — not a gradient ring or `lv_arc`, which render through a layer. **Colour = state, motion = activity**: idle breathes, listening sweeps slowly, thinking sweeps fast and tight, speaking pulses (envelope stubbed), **fault flashes hard and is owned by DEGRADED/E_STOP so no theme can re-time it**. Eyes keep state colour for now, to be judged by eye. |
| 1.2e | **Funny quotes** preserved in their own bottom region; yield immediately to real state or a person. | Still funny, never in the way. |
| 1.2f | ~~Themes~~ | **DONE, 2026-10-05.** Palette + static accessory layer over an unchanged state machine. **Accessories are static** — created once per theme, never animated, so they cost nothing per frame. `DEGRADED` and `E_STOP` colours pass through untouched. Halloween is the first theme; notably it had to **give up orange**, which sits too close to DEGRADED's amber, so it leans on purple and toxic green and lets the cobweb accessories carry the identity. |
| 1.3 | ~~Audio cues~~ | **DONE, 2026-10-05.** Six cues in `app_face/cues.cpp`, rendered once to 48 kHz stereo PCM with an 8/55 ms envelope. **The channel model in the design was wrong** — this HAL has one player that *refuses* while busy, so a silently dropped cue was the real risk and software priority replaces channels. `Fault` repeats, `Mode` fires on every state change, the vendor startup sound is gone, and `Heard`/`Attend` await the mic. |
| 1.3b | **Beep enable and volume, resident on the Tab5 and settable from the dashboard.** The settings live on the head so it behaves correctly with no link, and the dashboard is the editor rather than the owner. **The decision this forces: whether a global mute may silence the `Fault` cue.** A mute that takes the fault cue with it is a safety-communication regression arrived at by a convenience setting — and the reservation rule in `face-config.md` exists for exactly this shape of problem. Options: exempt `Fault` from mute, give it a volume floor, or allow full mute but annunciate it on the panel and the band so a muted robot is visibly muted. | Routine cues can be turned down to nothing without anyone accidentally disabling the one cue that matters. |
| 1.4 | **Face detection.** Add `esp-dl`, uncomment `HumanFaceDetect`, measure image size, PSRAM and detections/second. | **Upside, not a gate.** It should work and we want it; if it runs at all we have dramatically exceeded the minimum, and if it does not the IDF switch still stands on 1.1. |
| 1.5 | **Voice, as a probe.** Capture → encode → play locally, to find what the Tab5 end of a conversation costs in CPU. | A local round trip that cannot hold under ~150 ms means the architecture needs revisiting. |

**Keep hold of why we are doing this.** The reason to move to ESP-IDF is to inherit established
best-practice graphics so that **we have options for eye animation** — that is the whole thesis, and
1.1 tests it directly. Face detection is a bonus on top. An earlier draft of this plan put face
detection first and treated it as the gate, which lost the point: we would have been deciding the
graphics question on a computer-vision result.

### 1.3a — The choreography creator, as the home for configuration  ·  **data model DONE, see [`design/face-config.md`](design/face-config.md)**

**Taken up immediately after the simulator work, and deliberately so**: the face now has several
things that are *configuration* rather than code — themes, the emote vocabulary, the state→colour
table, the band's motion vocabulary, the earcon set — and they are currently all compiled-in C
tables. That is the wrong home for them, and it is the right moment to say so, because every one of
them was just designed and the shape of the data is fresh.

**Why these belong in the choreography system rather than in firmware:**

- They are **authored, not programmed.** A theme is a palette plus an accessory set; an emote is
  seven numbers. Neither needs a compiler, and putting them behind one means every tweak costs a
  flash — which this session has demonstrated at length.
- They are **per-robot and per-occasion.** A Halloween theme, a demo-day palette, a calmer emote set
  for a classroom. Per-robot values must not live in committed source; that rule already exists for
  drive calibration (`per-robot-values-not-in-repo`) and applies here for the same reason.
- They are **the same kind of thing a routine is.** A choreographed routine is a timeline of
  commanded states; a theme is a lookup from state to appearance. Both are declarative descriptions
  of behaviour that the robot interprets, which is exactly what the choreography system is for.
- The face already has the **right internal shape** for it: states carry a colour and a policy,
  emotes carry seven parameters and a valence/arousal point, themes carry a palette and an accessory
  list. These are tables, and tables want to be data.

**What to settle in this step** (design, not implementation):

1. **Where the data lives** — on the robot, in the dashboard, or in the repo — and how it reaches
   the Tab5. The existing StamPLC protocol is line-based UDP and was not built to carry a payload
   like this.
2. **What is authorable and what is not.** The reserved fault colours and the fault band motion must
   **not** be authorable, by the same argument that keeps them theme-proof. This is the first real
   test of that rule against a system designed to let people change things.
3. **Whether the face validates what it is given.** A theme that recolours `E_STOP` must be rejected
   by the firmware, not merely discouraged by the editor — an editor is a convenience, not a
   safety boundary.
4. **How it degrades.** If the configuration is absent or unreadable, the face must fall back to the
   compiled-in defaults rather than to nothing.

**Not** to be built in this step: the editor UI, or the transport. The deliverable is the data model
and the boundary of what may be authored.

### 1A — The Tab5 as a dashboard

Not to be lost: the Tab5 is a 1280×720 touchscreen on the front of the robot, and the dashboard
already exists as HTML on the Thor.

- What dashboard information belongs on the *robot* versus on the operator's tablet? The audience
  split in `hri-interaction-design.md` applies: bystanders need intent, operators need state.
- Three routes, to be chosen deliberately: **native LVGL pages**, **streamed HTML** (headless
  Chromium on the Thor → changed regions as JPEG tiles → the P4's hardware decoder, which does FHD
  at 48 fps), or **both** — a native face with a streamed dashboard page behind a touch.
- The streamed route is attractive because it reuses the real dashboard and needs no duplicate UI.
  It is also the one that dies when the link drops, so it must not carry anything safety-bearing.

### 1B — Communication architecture, Tab5 ↔ Thor

This is about *how* and about efficiency, and it is the piece most likely to be underestimated.

- **Audio up:** 16 kHz mono PCM is 256 kbit/s raw. Opus at 24 kbit/s is ~10× better and the P4 can
  encode it. Decide: raw for simplicity, or compressed for headroom shared with video.
- **Audio down:** TTS audio back to the speaker. Streamed, not file-at-a-time, or the robot pauses
  before speaking.
- **Video up — open question worth answering here:** does the Thor want the camera feed for
  navigation or for vision work? If so, the P4's **hardware JPEG encoder** makes MJPEG cheap, and
  that choice interacts with the audio budget on the same Wi-Fi.
- **Transport:** UDP for audio (loss-tolerant, latency-critical) versus TCP/WebSocket for control.
  The existing StamPLC protocol is already line-based UDP; conversation should not be bolted onto
  it.
- **Get the Tab5 onto Wi-Fi and OTA it.** It is currently on USB only. This is worth doing as a
  functional check regardless of anything else, because it exercises the C6 radio, the network
  stack and the OTA path that Phase 4 depends on.
- **But this bench network is NOT a latency test, and conclusions drawn from it would be close to
  meaningless.** The bench sits on a large, busy home Wi-Fi network. The robot has its own small
  network with a handful of clients and a **hard-wired Thor**. They are not comparable, and a bad
  number here would say nothing about the robot.
- **The inference only runs one way:** if the protocol performs acceptably on this network, it will
  perform at least as well on the robot's. So treat the bench as a **functional proof and a
  worst-case floor** — does the protocol work, does the audio arrive intact, does it survive packet
  loss — and leave the latency budget to Phase 5 on the real network.

---

## Phase 2 — Systematic port of current Tab5 functionality

*Still no robot. This is about preserving what exists, not improving it.*

- Port against the demo's HAL interface, one app replacing `AppTemplate`.
- **Keep a stub register** (below) of everything nerfed, so nothing is silently forgotten at robot
  time. A stub must log loudly, not return a plausible value.
- Nerfed for now: pan/tilt (8Servo `0x25`), front ToF (`0x29`), the lift control pages, the StamPLC
  UDP link, OTA.
- The I2C work is mechanical: `bsp_ext_i2c_get_handle()` is already on GPIO 53/54, and our ToF read
  path is already raw registers.

---

## Phase 3 — StamPLC: what did we leave on the table?

*Independent of everything else, cheap, and read-only. Runs whenever Phase 1 or 2 is blocked.*

The StamPLC has demo firmware exactly as the Tab5 does — **`m5stack/M5StamPLC-UserDemo`** ("user
demo for hardware evaluation"), plus the **`m5stack/M5StamPLC`** library — and we replaced it with
bespoke code (`firmware/rc_serial_probe`). **It works, and the goal is not to replace it.**

The goal is to find out whether we left **better-performing code or unused hardware capability**
behind by building our own. The Tab5 audit is the cautionary precedent: `audioRecord()` exposed a
gain parameter while we were poking ES7210 registers by hand, and a dual-microphone test already
existed while we wrote a bench harness for it.

The buttons are the one known gap — they are stubbed in our firmware and never worked — but the
point is the general audit, not the buttons specifically.

Deliverable: a table of demo/library capability against ours, with a recommendation per row — adopt,
ignore, or worth knowing. **No code changes in this phase.**

**DONE, 2026-10-05 — `notes/design/stamplc-demo-audit.md`.** Three results worth pulling forward:

- **The premise inverted.** The StamPLC demo contains **zero LVGL** — mooncake + smooth_ui_toolkit +
  **M5GFX**. M5Stack chose LVGL for the Tab5 and M5GFX for the StamPLC, so the renderer choice is
  screen-driven rather than a vendor standard, and *our bespoke StamPLC UI is already on the
  renderer the vendor picked for this board.* The Tab5 lesson does not transfer. **Recommendation:
  do not port.**
- **The buttons are root-caused, by reading, with no hardware.** The stub's comment blames the
  double `begin()`; the actual mechanism is that our loop calls `M5.update()` and then
  `M5StamPLC.update()` — which *is* `M5.update()` — so the second call recomputes the button state
  machine and discards the click edge before `uiButtons()` can read it. `wasClicked()` is a
  per-update verdict, not a latch. Likely a one-line fix, **but** `uiButtons()` holds a BtnC
  hold-to-confirm local e-stop, so it needs a decision and a bench test, not a drive-by edit.
- **Two things worth adopting**, both additive and touching no control path: the battery-backed
  **RTC** (absolute fault timestamps — our logs are `millis()`-relative, so a reboot erases *when*)
  and **SD logging** (a black box that does not depend on the Thor, the network, or a host draining
  a serial port). Plus one to read: the demo's `trigger_relay` is a persisted if/then/else rule
  engine with an on-device editor — the shape we specified for choreography configuration, already
  working.

## Phase 4 — Stack, flashing, OTA and memory

**DESIGN DONE, 2026-10-05 — `notes/design/tab5-idf-ota.md`. Nothing tested over the air**: the bench
Tab5 is on USB and not associated with a network, which is the one thing that cannot be checked yet.

- **The architecture does not change.** `ota-from-thor.sh` never used ArduinoOTA — it abandoned it
  because the listener sits behind the ESP-Hosted co-processor and fails silently. It is a *pull*
  driven by a text command on UDP :4242, and that maps to IDF one-for-one. Only the build command
  and the binary path change.
- **Every prerequisite is now satisfied**, including the one that could have sunk the plan: Wi-Fi
  works on the P4 in IDF, proven by the demo's own `hal_wifi.cpp` over `esp_hosted` +
  `esp_wifi_remote`. Plus `partitions.csv` (two 5 MB slots, 44% headroom), `app_update`,
  `esp_http_client`, `esp_https_ota`, and NVS preserved at 0x9000.
- **The stall deadline survives the port.** `esp_https_ota_perform()` is the non-blocking form, so
  the loop and the 10 s deadline stay ours. The one-shot `esp_https_ota()` convenience call must be
  avoided — it hides the loop, and hiding the loop is how the original wedge-on-"OTA: writing" bug
  happened.
- **Only one genuinely new piece of work: station mode.** The demo does AP; OTA needs STA.
  Credentials go in NVS, never committed.
- **New finding: `CONFIG_BOOTLOADER_APP_ROLLBACK_ENABLE` is not set.** Two slots, no automatic way
  back from an image that boots broken — and the head is on the e-stop branch, so recovery means
  closing the contactor and physically reaching it. Must be enabled *together with* a
  `mark_app_valid` call, or every image reverts after one boot; that pairing is a single decision
  and needs a bench test that deliberately OTAs a broken image.
- **`CLAUDE.md`'s flash table is deliberately NOT updated yet.** Its value is that every row is
  known to work; an untested row would damage exactly that. It graduates when an OTA has succeeded
  end to end.


*Prerequisite for Phase 5. If this is not solved, a robot visit is wasted.*

- **Does the IDF route change flashing?** The `CLAUDE.md` rote procedure is PlatformIO. ESP-IDF uses
  `idf.py`, so the bench procedure needs a second documented path, not a replacement.
- **OTA is the real work.** `ArduinoOTA` has no ESP-IDF equivalent; `esp_https_ota` is already
  compiled into the subtractive build. `firmware/tab5_face/ota-from-thor.sh` changes with it.
- **Partitions:** restore two app slots. Measured: the subtracted image is 2.76 MB against a
  6.25 MB slot, so it fits.
- **NVS must survive.** Both tables place `nvs` at `0x9000`; the demo sizes it `0x6000` against our
  `0x5000`. Per-robot tilt limits and IMU offsets live there. **Verify, do not assume.**
- **Does any of this apply to the StamPLC?** No — the StamPLC is an ESP32-S3 on PlatformIO with its
  own OTA over UDP 5005. Its procedure is unchanged, and the two must not be conflated in the docs.
- Update `CLAUDE.md` and memory once settled.

---

## Phase 5 — Requires the robot

In order, and not before Phase 4 is done:

1. Check out the current code and **verify all existing functionality on the existing firmware**
   first — a known-good baseline before anything new is flashed.
2. Un-nerf and validate each item in the stub register: pan/tilt, front ToF (against a tape
   measure), the StamPLC link, the lift pages.
3. OTA, end to end, including a deliberate failed update and recovery.
4. The Tab5 ↔ Thor comms path, then the LLM path on top of it.

---

## Stub register

Filled in during Phase 2. Every entry needs an owner question for Phase 5.

| Stubbed | Why | Phase 5 check |
|---|---|---|
| *(to be filled)* | | |

---

## Proposed changes to this plan

Offered for acceptance or rejection rather than assumed:

1. ~~Gate on face detection.~~ **Rejected, and rightly.** The gate is 1.1 — whether LVGL and PPA
   give materially better eye-animation options than the bespoke renderer — because that is the
   reason for the switch. Face detection is upside. The gate itself stands: branches stay unmerged
   until 1.1 is answered.
2. **Use the demo's desktop SDL build for 1.2 and 1A.** The factory firmware builds for a laptop.
   Eye, emote and UI design can be iterated at full frame rate with no flashing and no hardware —
   which is exactly the constraint Phase 1 is operating under, and it removes the need for someone
   to be standing at the bench to judge every change. **This is the single highest-leverage item in
   the plan.**
3. **Amended:** get the Tab5 on Wi-Fi and OTA it now as a *functional* check, but do not treat this
   network as a latency measurement — it is a sprawling home network against the robot's small one
   with a wired Thor. Functional proof and worst-case floor only.
4. **Move Phase 4's partition and NVS work earlier**, into Phase 2, since the port has to pick a
   partition table anyway and NVS loss is silent and expensive.
5. **Run Phase 3 as filler.** It is read-only and independent, so it fits whenever something else is
   blocked rather than consuming a slot of its own.
6. **Schedule the aesthetic reviews.** 1.2 and 1.3 can only be judged by a person looking at and
   listening to the device. Those are the only steps that need Emma present, so they should be
   batched into deliberate review points instead of interrupting continuously — and everything else
   should be self-verifying.
