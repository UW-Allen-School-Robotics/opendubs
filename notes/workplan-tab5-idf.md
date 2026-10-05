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
| 1.2 | **Eyes and emotes.** `firmware/tab5_idf/app_face/` exists and runs; now iterate the aesthetics on the desktop build: layered, PPA-blitted, shape-based gaze, saccadic motion already specified. | Judged by eye, by Emma. Nothing else can judge it. |
| 1.2b | **Replace the M5Stack splash with an OpenDubs splash.** The boot currently shows the Tab5 vendor splash. `app_startup_anim` is the demo's own startup animation app and is the natural place; the robot should not boot into someone else's branding. | Looks like our robot, not a dev board. |
| 1.3 | **Audio cues.** The five rhythm-distinct earcons, on separate virtual channels, envelope-shaped via `playRaw`. | Audible and distinguishable across a room. |
| 1.4 | **Face detection.** Add `esp-dl`, uncomment `HumanFaceDetect`, measure image size, PSRAM and detections/second. | **Upside, not a gate.** It should work and we want it; if it runs at all we have dramatically exceeded the minimum, and if it does not the IDF switch still stands on 1.1. |
| 1.5 | **Voice, as a probe.** Capture → encode → play locally, to find what the Tab5 end of a conversation costs in CPU. | A local round trip that cannot hold under ~150 ms means the architecture needs revisiting. |

**Keep hold of why we are doing this.** The reason to move to ESP-IDF is to inherit established
best-practice graphics so that **we have options for eye animation** — that is the whole thesis, and
1.1 tests it directly. Face detection is a bonus on top. An earlier draft of this plan put face
detection first and treated it as the gate, which lost the point: we would have been deciding the
graphics question on a computer-vision result.

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

## Phase 4 — Stack, flashing, OTA and memory

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
