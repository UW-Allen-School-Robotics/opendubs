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
| 1.3b | ~~Beep enable and volume~~ **MOSTLY DONE, 2026-10-05.** Audio enable, volume and on-screen telemetry are resident on the Tab5 in `app_face/ui_settings.cpp`, reachable from the HMI's SETTINGS button, and persisted in NVS. **THE RESERVATION QUESTION IS CLOSED, and the answer is that there is nothing to reserve:** Emma's call, 2026-10-05 — "we work in an academic lab and there's not much that this robot can legitimately know and communicate is wrong - I see beeps as part of demos and 'fun' uses of the robot - not routine." So a global mute MAY silence `Fault` as well; no exemption, no volume floor, no visibly-muted annunciation. The `face-config.md` reservation rule still stands for things that *are* safety communication — it simply does not apply to the earcons. **The interesting direction is voice, not beeps** (1.5), which is not implemented. Remaining: the dashboard as the editor rather than the owner. | Routine cues can be turned down to nothing; the fault cue is explicitly not special. |
| 1.4 | ~~Face detection — the **flash cost** half~~ **MEASURED, 2026-10-05, see below.** `esp-dl` resolves, compiles and links; the cost is **+0.75 MB of app image and a 187 KB model partition**, leaving ~30% free in the 5 MB OTA slot. Detections/second and quality still need the camera and a face. | **Upside, not a gate.** It should work and we want it; if it runs at all we have dramatically exceeded the minimum, and if it does not the IDF switch still stands on 1.1. |
| **1.6** | **Turning to look — head motion as the gaze output.** See [`design/head-turn-gaze.md`](design/head-turn-gaze.md). Face tracking currently terminates at the EYES: the attention target moves the pupils and the chain ends. The robot has a pan/tilt head and **nothing closes the loop from "I see a person" to "turn toward them"** — in the Arduino firmware `TURN L/R` comes from outside and `PANTILT` is an operator D-pad mode, so the head has never moved because the robot noticed someone. At room distance an eye shifting a few dozen pixels is nearly invisible; **a head that turns is the signal.** Eyes lead and the head follows, the re-centring IS the vestibulo-ocular reflex, and the existing confidence grading decides commitment: **a glance never moves the head.** Also reframes the detector's 1.1 m limit — range and legibility are separate problems and we had been treating only one. | **Only verifiable on the robot** (the bench Tab5 has no pan/tilt). The control law, slew limiting, bounds and the eyes-only fallback are host-testable; the *feel* is not. Every threshold ships as a named placeholder until someone watches it turn. |
| 1.5 | ~~**Voice, as a probe.**~~ **MOVED to [`workplan-tab5-audio.md`](workplan-tab5-audio.md), 2026-10-07** — it is that plan's first measurement rather than a loose end of this one. Capture → encode → play locally, to find what the Tab5 end of a conversation costs in CPU. | A local round trip that cannot hold under ~150 ms means the architecture needs revisiting. |

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

**OWNED ELSEWHERE, 2026-10-07: 1.3a has its own branch and its own workplan and is being worked
in a separate session. Do not plan, edit or implement it from here** -- treat this section as
background only. Flagged because two sessions editing one workplan file is how merge conflicts
and duplicated decisions happen.

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

### 1A — The Tab5 as a dashboard · **RETIRED, 2026-10-07**


**Retired at Emma's direction, 2026-10-07: dashboard features get added SELECTIVELY, when something
actually needs them, rather than porting a working HTML dashboard onto the panel wholesale.** The
operator's tablet already serves that audience well. Nothing here is lost -- the panel is still a
1280x720 touchscreen and the HMI already carries the controls that belong in front of the robot --
but "put the dashboard on the face" is no longer a goal, so it stops being an open item. The
JPEG-streaming route in [`design/tab5-platform.md`](design/tab5-platform.md) remains the cheap way
in IF a specific page is ever wanted.

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

### 1B — Communication architecture, Tab5 <-> Thor · **MOVED to [`workplan-tab5-audio.md`](workplan-tab5-audio.md), 2026-10-07**

*Kept here for context only. It is the transport layer of the audio pipeline, so it is owned
by that plan now and must not be tracked in two places.*

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

### WORKING AND VERIFIED OVER THE AIR, 2026-10-05

The whole chain runs on the bench Tab5. Evidence, from the laptop with nobody watching the panel:

```
before: PONG m5stack_tab5 b4e356b-dirty Oct  5 2026 slot=app0
ack:    OK ota
        192.168.68.119 - - "GET /m5stack_tab5.bin HTTP/1.1" 200     <- the DEVICE fetched it
after:  PONG m5stack_tab5 b4e356b-dirty Oct  5 2026 slot=app1
```

**The slot flipping app0 -> app1 is the proof**: the image was pulled over the network, written to
the inactive slot, and booted from. Three stages, each self-verifying without a human at the panel:

1. **STA association** — the status line carries the IP. Stored Arduino-era credentials survived
   the partition change (the board reported reason 201 NO_AP_FOUND, not "none stored"), confirming
   the nvs-at-0x9000 preservation was non-destructive.
2. **UDP :4242 listener** — `PING` replies with project, version, build date and running slot. One
   packet proves association, bind and round trip, and the version string makes a successful OTA
   confirmable by what changed rather than by memory.
3. **`OTA <url>`** — `esp_https_ota_begin` / `_perform` / `_finish`, the NON-BLOCKING form, so the
   10 s stall deadline stays ours. The one-shot convenience call is deliberately avoided: it hides
   the loop, and hiding the loop is how the Arduino head once wedged on "OTA: writing",
   recoverable only by pulling power.

**One configuration decision, made deliberately: `CONFIG_ESP_HTTPS_OTA_ALLOW_HTTP=y`.** IDF labels
it "ONLY FOR TESTING PURPOSE". It is parity rather than a new exposure — the Arduino firmware
already pulls over plain HTTP from the Thor — but it does mean anyone who can reach the head can
tell it to pull firmware from any URL, on a network that will have students and whose dashboard API
has no authentication either. **The fix is signing, not TLS**: `SECURE_SIGNED_APPS_NO_SECURE_BOOT`
makes the bootloader verify the image itself, which defends against a hostile image however it
arrived, including over TLS from a compromised server. That is a real item for before student
exposure and is NOT done.

### THE CANONICAL FLASH PATH DOES NOT CHANGE YET — GATED, 2026-10-05

Emma's call, and it is the right one: *"we need to get the whole stack up and running and have a
viable ready-to-ship product before we change the canonical flash method."* A documented procedure's
entire value is that every row is known to work; swapping one for a path whose firmware is still
~40% of a head controller would spend that credibility to buy nothing.

**So, deliberately NOT done, and not to be done opportunistically:**
- `firmware/tab5_face/ota-from-thor.sh` is **unmodified** and keeps flashing the Arduino firmware.
- `CLAUDE.md`'s flash table gets **no IDF row**.
- The IDF OTA is a bench capability. It works; it is not the procedure.

**Blast-radius check, verified rather than assumed (2026-10-05):**

| | |
|---|---|
| `firmware/rc_serial_probe/**` | **entirely untouched** on this branch |
| `rc_serial_probe/ota-from-thor.sh`, `flash-from-thor.sh`, `tab5_face/ota-from-thor.sh` | **byte-identical to `main`** |
| `tab5_face/platformio.ini` | additive only — new bench envs; `[env:tab5]` extended from, not modified |
| UDP ports | StamPLC **5005**, Tab5 **4242** — already separate, and the IDF listener reuses the Tab5's existing port |
| USB flashing | `idf.py` passes `--chip esp32p4`; esptool **refuses** a mismatched chip, so pointing at the StamPLC's S3 aborts instead of writing face firmware to the safety controller |

**The gate — what "appropriately confident" has to mean, so it is checkable rather than a feeling:**

1. **Phase 2 complete.** Pan/tilt, the front ToF, the lift pages and the StamPLC `STATE` link all
   ported and working. Until then the IDF image is not a replacement for anything.
2. **Rollback paired and proven.** `CONFIG_BOOTLOADER_APP_ROLLBACK_ENABLE` together with
   `esp_ota_mark_app_valid_cancel_rollback()`, demonstrated by deliberately OTA-ing a broken image
   and watching it revert — **on the bench, where USB recovery is free.** Without this, one bad
   OTA means closing the contactor and reaching into the head.
3. **The signing decision made.** Plain-HTTP OTA is parity with today, but it lets anyone who can
   reach the head tell it to pull firmware from any URL, on a network that will have students.
   `SECURE_SIGNED_APPS_NO_SECURE_BOOT` is the fix and it is independent of transport.
4. **A full robot session without a USB recovery.** The procedure earns the table by surviving
   real use, not by passing a bench test.

Only then: update `ota-from-thor.sh`, add the `CLAUDE.md` row, and retire the Arduino path.

**Still to do here:** rollback (`CONFIG_BOOTLOADER_APP_ROLLBACK_ENABLE` paired with
`esp_ota_mark_app_valid_cancel_rollback()` — they must land together or every image reverts after
one boot), and updating `firmware/tab5_face/ota-from-thor.sh` for the IDF build command and binary
path. `CLAUDE.md`'s flash table can now gain an IDF row, because the procedure has actually worked
end to end.

---

**Superseded design note below:** `notes/design/tab5-idf-ota.md`. **Nothing tested over the air**: the bench
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

Filled 2026-10-07, at the close of the port. Everything that was nerfed is now resolved, so this
is a record of what was stubbed and how it ended rather than a list of outstanding work.

| Stubbed | Why | Outcome |
|---|---|---|
| **Touch (GT911)** | The IDF bring-up left the controller ACKing I2C with its scan engine never started | **FIXED** — M5GFX INT-pin sequence. Root cause and the five things it was NOT: [`history/tab5-gt911-touch-root-cause.md`](history/tab5-gt911-touch-root-cause.md) |
| **Pan/tilt (8Servo 0x25)** | No I2C driver in the IDF port | **DONE** — `app_face/pantilt.cpp`, per-axis sign, tilt limits read from on-robot NVS, driven by gaze, RC and the d-pad |
| **Front ToF (0x29)** | No driver; which part was fitted was unknown | **DONE** — identified as a VL53L0X on the robot, `app_face/tof.cpp`, verified by hand. See F5 |
| **Lift pages** | Drawn but inert; `commandBox()` never set `LV_OBJ_FLAG_CLICKABLE` | **DONE** — `MAST CMD` to the StamPLC, hold-to-move, gate annunciated when the robot will refuse |
| **The StamPLC UDP link** | One direction only, and the state chip had no source at all | **DONE** — `ESTOP`/`MAST CMD` out, state derived from the StamPLC's own telemetry broadcast, address resolved by mDNS |
| **OTA** | USB-only | **DONE** — `OTA <url>` over UDP, dual-slot with rollback, and rollback has fired for real |
| **Emote channel** | Shapes ported; nothing selected them outside a bench cycler | **DONE (interim)** — random on unpinned states + a perception reaction. What an emote should MEAN is F1, still open |
| **Perimeter band** | Driven by a bench cycler, not by anything real | **SUPPRESSED** behind `OPENDUBS_FACE_BAND` — Emma's call. F2 holds the question of whether motion can earn its place |
| **Audio cues `Heard` / `Attend`** | Written, nothing triggered them | **`Attend` now fires on a person arriving.** `Heard` still awaits the mic — it belongs to [`workplan-tab5-audio.md`](workplan-tab5-audio.md) |
| **Voice (1.5), Tab5<->Thor comms (1B)** | Never started | **MOVED** to [`workplan-tab5-audio.md`](workplan-tab5-audio.md) |
| **Tab5 as a dashboard (1A)** | Never started | **RETIRED** — features get added selectively, not ported wholesale |

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

---

## Polish backlog — deliberately NOT critical path

Added 2026-10-06 at Emma's direction, after the startup-geometry fix. All four are cosmetic or
hygiene; none blocks a robot flash. Parked here so they are not mistaken for gaps.

| # | item | note |
|---|---|---|
| P1 | **Fade the splash wordmark out.** `app_startup_anim` fades the OpenDubs mark IN (`_opa` 0 -> 255) and then `onClose()` simply `lv_obj_del`s it, so it vanishes abruptly. Fading out would make splash -> face continuous. Cheap and safe: animate the LABEL's own `text_opa`, never a parent's opacity — a parent composites through a layer buffer, which is what produced `lv_draw_buf_create_ex: No memory` until the watchdog fired and why the brow is an `lv_line`. |
| P2 | **Gate the tracking reticle behind a flag.** `g_reticle` is created hidden but unhidden whenever perception tracks, and nothing gates it. On a robot it is a yellow/cyan box tracking people across the face. Same family as the state cycling and the theme toggle: an instrument that would ship. |
| P3 | **Gate the remaining on-screen diagnostic text.** The yellow perception/camera status line is already behind the settings telemetry switch, but the main status label (`g_label`) still carries state/emote/band/theme plus internal heap and the IP address unconditionally. Useful at a bench, noise on a machine. |
| P4 | **Start on the INIT state colour.** `buildScene()` builds each eye teal (`0x22e0d2`) as a construction placeholder and the state colour is applied by the first `renderFace()` pass. Now that that pass runs inside `onOpen`'s lock the placeholder is never visible — so this is tidiness, not a defect. Building in `kStates[0]`'s colour would remove the dead value. |

**Related, and a judgement call rather than polish:** the 18 s plain/Halloween theme toggle at
`app_face.cpp` is still ungated and is NOT behind `OPENDUBS_DBG_UI`, so a release build ships a face
that changes its decorative theme every 18 seconds. Cosmetic, not safety — but unlike P1-P4 it is
active misbehaviour on a robot rather than a missing nicety.

---

## On-robot findings, 2026-10-06 — raised by Emma at the robot

Four items from the first real session in front of the panel. **1 and 2 are design questions and
need a decision, not a patch.** 3 and 4 were one-liners and are already done.

### F1 — The face only ever shows the big neutral eyes. Where are the other emotes? · **OPEN, needs a decision**

All seven states are ported and so are the emote shapes, but **nothing drives the emote channel on
a robot.** `applyEmote()` is reached from the bench's own state cycling and from the per-state
`locked` emote (only `INIT`, `DEGRADED` and `E_STOP` pin one) — so in normal operation the face
sits on `EM_NEUTRAL` indefinitely and every other emote is dead code wearing a test harness.

This is the same shape of gap as the state chip having no source: the capability exists and nothing
is connected to it. The decision is **what an emote should MEAN**, and it is a product question:

- an emote per *activity* (listening / thinking / speaking) — but the perimeter band already carries
  activity, and duplicating it in two channels invites them to disagree;
- an emote per *event* (heard you, lost you, cannot comply) — momentary, returning to neutral, which
  is what the cue sounds already do, so the two could share one trigger;
- an emote per *perception* (a person close by, a person leaving) — the only one the robot can
  currently source by itself, since face detection works.

Until this is decided the honest position is that the emote library is **unexercised**, and a demo
that cycles it is a demo, not a behaviour.

### F2 — The "Siri-like" status spins continuously and means nothing · **OPEN, needs a decision**

Same defect as F1 and worse, because it is actively misleading: a perpetual motion signal reads as
"the robot is doing something" at all times, so it carries no information and trains people to
ignore the one channel that should carry urgency. **Either give it utility or stop it.**

If it earns its place, the band's four behaviours must map to things the robot actually knows:
`idle` breathes, `listening` sweeps slowly, `thinking` sweeps fast, `speaking` pulses — but
`band::g_activity` is set by the bench cycler today, exactly as the emotes are. Options are to
drive it from real activity (mic gate, inference running, audio playing), or to reduce it to
**state colour only, motionless**, and let motion mean something later.

Recommendation: motionless until there is a real source. A still band that occasionally moves is
legible; a band that always moves is decoration.

### F3 — Missing-glyph box in the top bar · **DONE, 2026-10-06**

"OpenDubs ▯ Lift Module" was U+00B7 MIDDLE DOT. The Montserrat subset this build ships has no glyph
for it, so LVGL drew the missing-glyph box. Replaced with an ASCII hyphen. **Any non-ASCII character
in this UI has the same problem** — adding one means extending the font range in Kconfig, not just
typing it.

### F4 — FPS/CPU readout and the eyebrow telemetry text are off · **DONE, 2026-10-06**

`g_label` carried state/emote/band/theme plus internal-DRAM heap plus the IP address
unconditionally, directly under the brows. It now follows the **SETTINGS → On-screen telemetry**
switch that already gated the perception line: same control, persisted in NVS, so it comes back
without a reflash. This closes **P3** in the polish backlog above.

### Also fixed in this session, for the record

- **The lift buttons were not clickable.** `commandBox()` never set `LV_OBJ_FLAG_CLICKABLE`, so
  RAISE and LOWER received no press events at all — nothing was ever sent and the robot's arm gate
  was never reached. The e-stop worked only because it set the flag explicitly.
- **The head fought the d-pad.** Manual aiming was a 600 ms LEASE, so the head re-centred the
  instant it lapsed and a detected face pulled it away as well. The Arduino had no such fight
  because tracking was a PERSISTENT MODE. Aiming by hand now takes ownership and HOLDS, and MODE
  hands it back; the narrow autonomous envelope no longer clamps a hand-aimed heading.
- **The lift gate is annunciated.** Gating the lift behind MANUAL is the design; leaving it silent
  was the defect. RAISE/LOWER grey out and the panel says what to do — "set CH5 to MANUAL", "close
  the contactor", "clear the e-stop at the dashboard".
- **OTA rollback demonstrated in anger**, unplanned: a bad image booted, failed to mark itself
  healthy inside 12 s, and the bootloader reverted to the previous slot. That is Phase 5 item 3's
  deliberate-failure test, arrived at accidentally — a scripted version is still worth doing.

---

## Second round of on-robot findings, 2026-10-06

### F5 — Front ToF now has a driver · **DONE, verified on the robot 2026-10-06**

It was never "unwired" — the panel said so because no driver existed. Identified on the robot from
its model ID: **VL53L0X** (0xEE at register 0xC0), not the VL53L1X. That distinction matters and is
why it was measured rather than assumed — the L1X has sixteen-bit registers and a driver for one
silently fails against the other.

`app_face/tof.cpp` ports ST's bring-up sequence (the one Pololu's Arduino library implements, which
does not exist here): 2V8 mode, stop variable, SPAD reference map, the default tuning table, VHV
and phase reference calibration, then continuous back-to-back ranging polled non-blocking.

**It is ranging.** `start=0x02`, interrupt status shows new-sample-ready, 17 reference SPADs
(aperture type). Every sample comes back `raw=8190 rs=6` — the out-of-range sentinel with ST range
status 6, SIGMA THRESHOLD CHECK failed, i.e. the sensor does not trust the return.

**TESTED: a hand in front reads correctly.** So the `raw=8190 rs=6` stream was a real
out-of-range — there was simply no target within the sensor's reach — and the driver is complete.

**THE TIMING BUDGET DOES NOT NEED PORTING.** `tof.cpp` deliberately omits
`setMeasurementTimingBudget()`, and sigma-threshold failure is exactly what too-short an
integration time also produces, so that was the other candidate. It is now ruled out by
measurement rather than by argument: a strong return at ~20 cm ranges fine on the default budget.
**Do not write the ~150 lines of macro-period / timeout encoding math** — it would be effort spent
to reproduce behaviour that already works. If long-range accuracy ever matters, that is when to
revisit it, and `tof.cpp` says so at the omission.

Deciding this by one physical test rather than by changing the budget and re-testing together is
the repo's own rule — change one variable at a time — and it saved the whole port.

**Open nit, not a defect:** Emma's note that "out of range is a misreport". The sensor genuinely is
out of range when nothing is in front of it, but the phrase reads as a fault rather than as a
normal condition. Wording only — something like "nothing in range" would carry the same fact
without implying something is broken.

HEIGHT still reads `---` and should: that sensor is on the StamPLC and arrives over UDP, which is
not wired. It must not borrow the ToF's number — they measure different things.

### F6 — LVGL's FPS/CPU overlay is off · **DONE**

The "FPS telemetry" was not ours: `CONFIG_LV_USE_PERF_MONITOR`, LVGL's own sysmon overlay, bottom
right. Our OWN `sdkconfig.defaults` was turning it on — twice. It had been set in a session when
sdkconfig.defaults was inert (IDF only consults it when `sdkconfig` is absent), so it appeared
harmless; the later fix that made our deltas actually apply turned the overlay on for real. Now
unset. It is a Kconfig-time feature, so unlike the brow-level HUD it cannot follow the SETTINGS
switch.

### F7 — Emotes cycle randomly on unpinned states · **DONE, interim**

The cycler existed but was behind `OPENDUBS_DBG_UI`, compiled out of the robot build — which is
why the face only ever showed neutral eyes. Now ungated, random rather than sequential (never
repeating the current one), with a 4-9 s jittered dwell so it does not settle into a rhythm.
Pinned states are untouched: DEGRADED and E_STOP are communicating something and must not drift
off it. **This answers F1 provisionally and does not close it** — what an emote should MEAN is
still open.

### F8 — The perimeter band is suppressed · **DONE**

Not built and not updated, behind `OPENDUBS_FACE_BAND` (default 0). Emma: it spun continuously and
meant nothing, so it carried no information and trained people to ignore the one channel that
should carry urgency. Also removes 48 animated segments from every frame. The one real loss is the
hard FAULT flash on DEGRADED/E_STOP; state colour and the fault cue still carry that. F2 stays open
for whether motion can earn its place.

### F9 — Tracking reticle behind a settings switch · **DONE**

**SETTINGS -> Tracking reticle**, default OFF, persisted in NVS. It was created hidden and then
unhidden whenever perception tracked, with nothing gating it — a box chasing people across the
face. Closes **P2** in the polish backlog.

---

## What closes this plan — 2026-10-07

The port is done. After retiring 1A and moving 1.5/1B to
[`workplan-tab5-audio.md`](workplan-tab5-audio.md), these are the only items left, and none is
large:

| # | item | state |
|---|---|---|
| 1 | **The 18 s theme toggle** — a release build changed decorative theme every 18 s | **DONE 2026-10-07**, now behind `OPENDUBS_DBG_UI`. This was the last instrument still shipping |
| 2 | **Stub register** | **DONE 2026-10-07** — filled above |
| 3 | **F1 — perception-driven emote** | **DONE 2026-10-07 (the easy win)**: a person arriving fires `EM_SOFT` + the `Attend` cue, held 6 s against the random cycler; departure returns to idle with no cue. The DECISION — what an emote should mean in general — stays open and is deliberately not being answered by this increment |
| 4 | **F2 — the band** | **DECIDED**: stays suppressed. Revisit only with a real motion source, which the audio plan may supply |
| 5 | **Scripted OTA-rollback test** | **OPEN** — Phase 5 item 3. Rollback has fired twice by accident; a deliberate, repeatable version is still worth having |
| 6 | **1.3b — the dashboard as the settings editor rather than the owner** | **OPEN**, one line, and arguably belongs with 1.3a's configuration work |

So: **one real open item (5) and one that may move (6).** Everything else is closed, decided, moved
or retired.

### Deliberately NOT in this plan any more

1A (retired), 1.5 and 1B (audio plan), 1.3a (its own branch and session — do not plan it from here),
HEIGHT over UDP (the StamPLC owns that sensor; the panel must not borrow the ToF's number).
