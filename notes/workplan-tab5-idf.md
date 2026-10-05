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
| 1.2b | ~~OpenDubs splash~~ **DONE, 2026-10-05** (commit 7732192).** Replace the M5Stack splash.** The boot currently shows the Tab5 vendor splash. `app_startup_anim` is the demo's own startup animation app and is the natural place; the robot should not boot into someone else's branding. | Looks like our robot, not a dev board. |
| 1.2a | ~~Eye geometry~~ | **DONE, 2026-10-05** — "generally this is much better". 480×480 (41.5 mm) at a 0.34 eye-width gap, radial body shading, soft radial catchlight **inside the pupil**, `lv_line` eyebrows with sprung raise and angle. Human-scale IPD was tried and **rejected**: it read human but less friendly, so gap-as-a-fraction-of-eye-width is the dial and 0.34 is the value. Minor tweaks deferred. |
| 1.2c | ~~State machine port~~ | **DONE, 2026-10-05.** All seven states ported verbatim from `ui_shared.cpp`, each of the seven `Expr` parameters on its own `AnimateValue`. `tilt` drives both the brow angle and the upper-corner cut, `bow` carves the lower lid, `squint` eats height and shifts the centre, `lookY` biases vertically, and state colour animates too. Cuts and bows are **hidden** when their parameter is ~0, so unused states cost nothing. State currently cycles on the bench; Phase 2 wires `STATE <NAME>` from the StamPLC. |
| 1.2d | ~~Perimeter band~~ | **DONE, 2026-10-05.** Full perimeter, 16 px, 48 opacity-animated segments — not a gradient ring or `lv_arc`, which render through a layer. **Colour = state, motion = activity**: idle breathes, listening sweeps slowly, thinking sweeps fast and tight, speaking pulses (envelope stubbed), **fault flashes hard and is owned by DEGRADED/E_STOP so no theme can re-time it**. Eyes keep state colour for now, to be judged by eye. |
| 1.2e | ~~Funny quotes~~ **DONE, 2026-10-05** (commit 7732192). **Funny quotes** preserved in their own bottom region; yield immediately to real state or a person. | Still funny, never in the way. |
| 1.2f | ~~Themes~~ | **DONE, 2026-10-05.** Palette + static accessory layer over an unchanged state machine. **Accessories are static** — created once per theme, never animated, so they cost nothing per frame. `DEGRADED` and `E_STOP` colours pass through untouched. Halloween is the first theme; notably it had to **give up orange**, which sits too close to DEGRADED's amber, so it leans on purple and toxic green and lets the cobweb accessories carry the identity. |
| 1.3 | ~~Audio cues~~ | **DONE, 2026-10-05.** Six cues in `app_face/cues.cpp`, rendered once to 48 kHz stereo PCM with an 8/55 ms envelope. **The channel model in the design was wrong** — this HAL has one player that *refuses* while busy, so a silently dropped cue was the real risk and software priority replaces channels. `Fault` repeats, `Mode` fires on every state change, the vendor startup sound is gone, and `Heard`/`Attend` await the mic. |
| 1.3b | **Beep enable and volume, resident on the Tab5 and settable from the dashboard.** The settings live on the head so it behaves correctly with no link, and the dashboard is the editor rather than the owner. **The decision this forces: whether a global mute may silence the `Fault` cue.** A mute that takes the fault cue with it is a safety-communication regression arrived at by a convenience setting — and the reservation rule in `face-config.md` exists for exactly this shape of problem. Options: exempt `Fault` from mute, give it a volume floor, or allow full mute but annunciate it on the panel and the band so a muted robot is visibly muted. | Routine cues can be turned down to nothing without anyone accidentally disabling the one cue that matters. |
| 1.4 | ~~Face detection — the **flash cost** half~~ **MEASURED, 2026-10-05, see below.** `esp-dl` resolves, compiles and links; the cost is **+0.75 MB of app image and a 187 KB model partition**, leaving ~30% free in the 5 MB OTA slot. Detections/second and quality still need the camera and a face. | **Upside, not a gate.** It should work and we want it; if it runs at all we have dramatically exceeded the minimum, and if it does not the IDF switch still stands on 1.1. |
| 1.5 | **Voice, as a probe.** Capture → encode → play locally, to find what the Tab5 end of a conversation costs in CPU. | A local round trip that cannot hold under ~150 ms means the architecture needs revisiting. |

#### 1.4 measured, 2026-10-05 — flash cost only

Built four times in the ephemeral tree; **nothing was committed and no dependency was added to the
repo.** Each delta below compares two builds that share one `sdkconfig`, because mixing configs is
exactly the mistake that produced a wrong number mid-measurement (see the drift note).

| build | app image | free in the 5 MB slot |
|---|---|---|
| no face detection | 0x2bd9d0 — 2.74 MB | 45% |
| + face detection, model in **flash_rodata** (the component's default) | — | — |
| + face detection, model in **flash_partition** | 0x37e4f0 — 3.49 MB | **30%** |

- **model in its own partition (recommended): +0.75 MB** of app image, plus a **187 KB** packed
  `.espdl` in the `human_face_det` partition — which is *why* the vendor's table reserves 512 KB
  there, a detail I had preserved without knowing its purpose.
- **model in rodata (the default): +1.05 MB** of app image and nothing in the partition.
- So moving the model out of the app saves ~304 KB while the packed model is 187 KB. The residual
  ~117 KB is **not explained** by the file size; the plausible reason is that rodata mode embeds the
  two unpacked models (`msr_s8_v1` + `mnp_s8_v1`) rather than the single packed blob. Stated as a
  hypothesis, not a finding.
- **The OTA trade-off is the real reason to choose.** In rodata the model ships *inside* the app, so
  one OTA updates both. In a partition it does not, so a model change needs a separate flash. For a
  head on the e-stop branch that is a meaningful difference, and it argues for rodata once there is
  room — 23% free is still workable.

**Verdict: affordable, and not a constraint on the graphics decision.** 1.5 MB of headroom remains
with the model in its partition.

**The remaining half is no longer human-gated, and recording it as such was wrong.** It was listed
as "needs the camera and a face", against a rule already in `CLAUDE.md`: *a test that needs a human
at the bench is not a test.* esp-dl's own example embeds a reference JPEG and prints scores and
boxes, so the firmware can supply its own stimulus and state its own verdict.
`app_face/face_probe.{h,cpp}` + `probe/human_face.jpg` (esp-dl's own 320×240 reference image) does
that: PASS / FAIL / INCONCLUSIVE, first-pass and steady-state latency, PSRAM, repeated on a timer,
behind `OPENDUBS_PROBE_FACEDET=1`. A fixed image is **better** than a live face — byte-identical
input, so latency is comparable across builds and a miss is a regression rather than bad lighting.

**It does not run, and the blocker is far more significant than the flash figure.**

```
assert failed: xTaskCreateStaticPinnedToCore (xPortCheckValidTCBMem(pxTaskBuffer))
```

Four experiments, one variable each. The first two killed my own explanations:

| experiment | result |
|---|---|
| probe task buffers via `MALLOC_CAP_DMA` (instead of `MALLOC_CAP_INTERNAL`) | **still asserts — and the probe's own log line never prints.** The failing task is not ours. |
| read the 14 lines before the assert | It fires at **1293 ms, immediately after `H_API: ESP-Hosted starting` and `add_esp_wifi_remote_channels`** — inside **ESP-Hosted's own** task creation, long before `app_face` opens. Our code never runs. |
| compare internal RAM at `heap_init` | **esp-dl has MORE, not less**: 64+18+**230** KiB against 64+18+**167** KiB. So "esp-dl starves internal DRAM" was wrong too; the linker relayouts memory substantially and something in that relayout puts ESP-Hosted's static task buffers where the TCB check rejects them. |
| esp-dl **linked but never referenced** | **Boots fine**, byte-identical image (the linker drops it). So declaring the dependency is free and harmless; only *referencing* the detector breaks the boot. |
| model in its own flash partition instead of rodata | **Still asserts.** So it is not the ~1 MB of model rodata either — it is esp-dl's own code/data. |

### RESOLVED, 2026-10-05 — IDF 5.5.5. Face detection WORKS, and the fix was the newer IDF

Emma said "try a newer IDF version". That was right, and it was right for a reason I had to find in
the source rather than in the issue tracker.

**Verified on device, read back off the chip rather than scraped from a build log:**

```
Chip is ESP32-P4 (revision v1.0)
m5stack_tab5   Oct  5 2026 09:29:21   v5.5.5      <- app descriptor at 0x10000
```

```
[faceprobe] decoded 320x240 rgb888
[faceprobe]   face score=0.835 box=(97,67)-(193,189)
[faceprobe] first=24.6ms steady=24.7ms (40.5 fps) faces=1 expect=1
[faceprobe] psram free=21238K largest=20992K
[faceprobe] VERDICT: PASS -- detector works, 24.7ms per frame steady
```

**CORRECTION, same day: I over-claimed this, and the over-claim caused a long bad detour.** What
is proven below is that **the MODEL runs**. It exercises **no part of the camera** — the live path
had not run once when I wrote "face detection WORKS". Live camera detection is **not working**; see
`notes/design/tab5-face-tracking.md` §4. Emma's words for the error, which are the right ones: *a
test without me in frame won't prove anything.*

**~25 ms per inference, ~38 fps, score 0.835, and the identical bounding box on eight consecutive
runs** — the determinism the fixed reference image was chosen for. No assert. ESP-Hosted starts
normally.

**Why 5.5 and not 5.4.** `heap_idf.c`, which defines the check that was failing, is **byte-identical
across 5.4.2, 5.4.4 and 5.5.5** — so "upgrade" was not automatically the answer, and 5.4.4 would
have been a wasted day. The fix is on the *allocator* side, in `heap/port/esp32p4/memory_layout.c`:

| | 5.4.2 | 5.5.5 |
|---|---|---|
| memory types | L2MEM, SPIRAM, **TCM**, RTCRAM | L2MEM, RETENT_MEM, SPIRAM, SPM, RTCRAM |
| TCM's caps | `MALLOC_CAP_TCM \| COMMON_CAPS \| MALLOC_CAP_INTERNAL` | **TCM is not registered in the heap at all** |

In 5.4.2 TCM was advertised as internal *and* byte-capable, so a generic internal allocation could
land there — and then FreeRTOS's `esp_ptr_byte_accessible()` rejected it. 5.5.5 removes TCM from the
allocator, so the fallback cannot happen.

**Three config changes were needed, and two were traps:**

1. **The bootloader outgrew its slot.** 5.5's is 0x6120 against a 0x6000 ceiling (0x2000 →
   `PARTITION_TABLE_OFFSET` 0x8000); on 5.4.2 it was 0x5a60, already 94% full. The obvious fix —
   move the partition table to 0xA000 — is wrong, because `nvs` sits at 0x9000 *specifically* to
   match the Arduino layout so per-robot values survive. Shrank the bootloader instead:
   `BOOTLOADER_LOG_LEVEL` 3 → 2. It is already compiled for size, so log strings were the slack.
   Cost: we lose the bootloader's partition-table dump; an app panic still prints, so crash-loop
   detection is unaffected.
2. **5.5 changed the P4 minimum-revision default to v3.01**, and this Tab5's P4 is **rev v1.0**, so
   esptool refused the image outright. Support is not dropped.
3. **The pre-v3 revisions are gated.** `REV_MIN_0/1/100` all `depend on
   ESP32P4_SELECTS_REV_LESS_V3`, default n — so setting a revision *alone silently does nothing*
   and the choice stayed at 301. Both are needed.

**A constraint worth carrying:** IDF states the two silicon families are mutually exclusive —
"revisions >=3.0 and <3.0 have huge hardware difference". This build therefore supports 0.x/1.x
and **not** >=3.x. **If a replacement or second Tab5 ships a v3 chip it needs its own build**, and
that is exactly the kind of thing that presents as "the new board is bricked".

**Still unverified: whether the FACE still renders correctly on 5.5.5.** The probe proves the
detector and the radio; it says nothing about the graphics, and that needs eyes on the panel. The
bench device currently holds the 5.5.5 + probe build.

**Incidental corroboration of a Phase 3 recommendation:** every probe log line is stamped
`2007-12-31` — the clock is unset. That is the RTC gap the StamPLC audit called the best-value item
on its list, showing up unprompted.

---

**Superseded below, kept because the reasoning errors are the useful part:**

**"MUTUALLY EXCLUSIVE" IS WITHDRAWN — it is a known upstream ESP-IDF bug on the P4, and the
premise of this whole item was wrong as well. Checked against what M5Stack actually ships:**

1. **M5Stack never shipped face detection on the Tab5.** Their `idf_component.yml` has **zero**
   esp-dl references, `HumanFaceDetect` exists only as three commented-out lines in
   `hal_camera.cpp`, their partition table reserves `human_face_det` at 400K and **nothing ever
   fills it**, and the M5Stack org has no face-detection repository. This item was written as
   "add `esp-dl`, **uncomment** `HumanFaceDetect`" — language that assumed a working vendor feature
   merely switched off. There is no such feature. It is abandoned scaffolding, and the reserved
   partition is what made it look finished.
2. **The crash is a documented ESP-IDF defect on the ESP32-P4**, not a property of the Tab5 and not
   a face-detection-versus-Wi-Fi law:
   [espressif/esp-idf#15996](https://github.com/espressif/esp-idf/issues/15996). When internal DRAM
   gets tight the allocator falls back to **TCM** (0x30100000–0x30102000) or PSRAM for a task's
   control block or stack, and FreeRTOS's own validity check then rejects it. Reported against
   v5.4.1 and v5.5-dev; **we are on v5.4.2**. Marked resolved upstream. The reporter's project also
   used `esp_hosted`, which is exactly where ours fails.

So esp-dl is not incompatible with Wi-Fi. It simply consumes enough internal memory to trip an
allocator fallback that this IDF version mishandles. **Fix path: a newer IDF, or free internal
DRAM** — not a choice between the camera and the radio.

**Also correcting my own reasoning:** experiment 3 below ("esp-dl has MORE internal RAM, so it is
not a shortage") is what steered me wrong. `heap_init` reports the **total pools at startup**, which
is not the same as DRAM still free at the moment ESP-Hosted creates its task. The shortage is real
and local; I measured the wrong quantity and then drew a confident conclusion from it.

**The original, now-superseded framing of the consequence:** The thing that breaks is ESP-Hosted — the SDIO link to the ESP32-C6 that *is* the
Tab5's only radio. Phase 4's OTA depends on it, and so does all of 1B. That makes this a planning
constraint, not a 1.4 detail.

**Scoped carefully, because the version pin matters.** This is esp-dl **3.1.3** with
`human_face_detect` **0.2.3**, and 3.1.3 is forced: 3.3.13 does not compile against that consumer.
A newer *pair* may not have the problem at all, so this is a statement about these versions, not
about the P4. Checking a newer matched pair is the cheapest next move, ahead of any attempt to
fix the memory layout by hand.

Mechanism **not established**, and deliberately left that way rather than guessed at a fifth time.

Also corrected: esp-dl was briefly blamed for the i2c abort above. **It was innocent** — that build
also carried the regenerated `sdkconfig`, which reproduces the abort on its own with no esp-dl
present. Two variables changed at once and the conclusion went to the interesting one instead of
the established one.

**A pin is required, and it is the third instance of the same upstream pattern.** `human_face_detect`
declares `esp-dl: ^3.1.3`; the solver takes that to 3.3.13, which **does not compile against its own
consumer** (`DL_IMAGE_CAP_RGB_SWAP` undeclared, `MSRPostprocessor` ctor mismatch,
`set_resize_scale_x` gone). Pinning `espressif/esp-dl: "3.1.3"` alongside
`espressif/human_face_detect: "0.2.3"` builds clean. That is the same failure as the LVGL 9 pin and
the `esp_codec_dev` 1.3.4 pin: **an M5/Espressif component's own declared range resolves to
something that breaks it.** Three for three — treat a caret range in this ecosystem as a bug until
proven otherwise.

**RETRACTED — I had this backwards, and acting on it broke the boot.** I claimed `sdkconfig` was a
generated, accumulating artifact that had drifted, and made `build.sh` delete it whenever
`sdkconfig.defaults` was newer. That is true of most IDF projects and **false here**: the Tab5 demo
**commits** `platforms/tab5/sdkconfig` — 94 KB, 3245 lines, the vendor's known-good configuration.
Deleting it replaced all of that with whatever our 1.7 KB defaults plus Kconfig defaults produced,
and the result aborted at 1307 ms with `i2c: CONFLICT! driver_ng is not allowed to be used with
this old driver`. Proven by bisection: restoring the vendor's exact `sdkconfig` and changing nothing
else boots clean. The 64 KB size change I reported as "drift closed" was this bug's symptom.

**What the dig did find, and it invalidates a standing assumption: `sdkconfig.defaults` had never
applied to a device build.** IDF consults it only when `sdkconfig` is *absent*, and `sdkconfig` has
existed since the clone — so our settings were inert. Concretely, `CONFIG_LV_USE_PERF_MONITOR`
reads as enabled in our tracked file and was **not set** in the config actually built: the FPS
counter was never on screen, which I had been recording as "never looked at". Two claims do survive,
checked directly against the vendor config: `CONFIG_LV_CONF_SKIP=y` and complex gradients off are
both genuinely true on device.

Fixed properly: `sdkconfig.vendor` (the demo's committed config, vendored into our repo so we notice
if the clone moves) is the base, `sdkconfig.defaults` is applied **on top**, and `build.sh`
regenerates from that ordered list every build. The whole configuration is now reproducible from
tracked files, and our overrides take effect for the first time — so the perf monitor is now
genuinely on.

So the pattern found today is **not** "stale generated files drift" three times over. It is the
narrower and more useful one: **a file nobody owns is dangerous in both directions** — `build.sh`'s
three-of-nine list and `AUDIT.md`'s single-file greps went stale because nothing regenerated them,
and `sdkconfig` broke because I regenerated something that was never mine to regenerate. The test
is not "is it generated?" but **"who owns this file, and does the tool agree?"**

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

**DECIDED, 2026-10-05 — `notes/design/tab5-dashboard.md`.** Desk work; no robot, no network.

- **It is a question about AUTHORITY, not information.** 17 of the dashboard's 24 endpoints POST
  *actuation* — estop/clear, relay, mast, teleop, arms, pdh, system. The Tab5 is a touchscreen on
  the front of the robot at roughly human eye height, reachable by anyone in the room, and the API
  has no authN yet. "Put the dashboard on the Tab5" naively means unauthenticated drive and
  arm controls within reach of a passer-by.
- **The rule: the head displays; it does not command.** Read-only, plus at most a single
  de-energize affordance — the same asymmetry `rc_serial_probe` already settled for the StamPLC's
  buttons ("buttons may only DE-energize; re-arm stays host/dashboard-guarded"). This also kills
  the "both" route: a streamed page behind a gesture hands over the whole control surface at once.
- **Route: native LVGL reading the existing `/api/snapshot` + `/ws`. Do NOT build the Chromium
  pipeline.** The thing worth reusing is the data, not the pixels, and the backend needs no change.
  The deciding argument is link loss and it is a safety one: **a streamed JPEG that stops arriving
  is pixel-identical to a live panel showing steady values**, so nobody at the robot can tell
  "SOC is 71%" from "SOC was 71% when the link died". Native holds the values *and* the age.
- **Being fair to streaming:** the three things genuinely hard to redraw are the lidar scan
  canvases, the arm-pose canvas and a sparkline — and all three are operator-analysis views that
  do not belong on the robot's face. The only content that justified streaming is content that
  should be on the tablet, so the hybrid collapses. The option does not expire if one view ever
  needs it.
- **Content: not 13 views.** One screen the face yields to: the face (default), a read-only
  technician card, and an automatic fault screen saying what failed and what clears it. The fault
  screen is the genuinely novel thing the panel can do, because the person who needs it is the one
  who walked over.

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
