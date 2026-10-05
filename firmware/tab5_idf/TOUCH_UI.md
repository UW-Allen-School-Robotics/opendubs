# Tab5 touch UI — built, verified, and not

**Status: the device image has NOT been built.** No ESP-IDF and no `M5Tab5-UserDemo` clone
existed on the machine this was written on; the demo tree was cloned to compile against, but
installing the IDF 5.5.x toolchain was out of scope for the sitting. **The desktop target builds
and links clean** — which is a real result for the LVGL 9.2.2 API surface and for memory safety,
and tells you nothing about the panel. Nothing here has been flashed, and nobody has touched the
glass.

Design of record: [`../../notes/design/tab5-dashboard.md`](../../notes/design/tab5-dashboard.md)
(the grades and what goes on the panel) and
[`../../notes/design/tab5-face-architecture.md`](../../notes/design/tab5-face-architecture.md)
(§1.7 touch, §3 module boundaries, §7 the debug layer). Invariants:
[`AUDIT.md`](AUDIT.md), which gained invariant 1b and a precise layer-trigger table for this
work.

---

## What is implemented

Six new files in `app_face/`, flat rather than in the `ui/` and `shared/` subdirectories the
architecture note §3 prescribes. **That is deliberate and it is a deviation.** The subdirectory
split belongs to the note's Stage 1 (moving the animation into the LVGL task), which has not
happened; and until it does, flat files are what `AUDIT.md`'s greps actually cover — every one of
them globs `app_face/*.cpp`. The checklist's own recorded failure is that `cues.cpp` was never
searched because the greps named one file, so adding code where the greps cannot see it is the
exact mistake to avoid. `build.sh`'s directory sync and its drift check are flat for the same
reason. When Stage 1 creates `ui/`, these move with it.

| file | includes `lvgl.h` | what it is |
|---|---|---|
| `face_mailbox.h/.cpp` | **no** | The two POD mailboxes and the seqlocks. `RobotSnapshot` (robot → panel, every value carrying its group's arrival stamp) and `UiIntent` (panel → robot). The `shared/` tier: the UI depends on it, a future `net` depends on it, it depends on neither. |
| `ui_grade.h/.cpp` | yes | The four confirmation grades as machinery. Registration, press tracking, the hold-to-confirm progress, the grade-2 heartbeat, and the independent per-tick re-check that stops a jog whose release event never arrived. |
| `ui_controls.h/.cpp` | yes | The technician screen (8 readout rows, each with an age), the fault screen (what failed / what clears it), and all nine controls. |
| `ui_touch.h/.cpp` | yes | Input-device discovery, the entry gesture, screen arbitration and the two idle reverts. |
| `dbg_stimulus.h/.cpp` | **no** | A synthetic `RobotSnapshot` publisher standing in for `net`, so the screen is testable with no robot and no network. `OPENDUBS_TOUCH_DEBUG=1` only. |

Plus: `app_face.cpp` gains four small edits (an include, a `touchUiBuild` call inside the
existing `onOpen` lock, a locked `touchUiTick` at the top of `onRunning`, and an early return
that yields the glass); `build.sh` generates `touch_ui_config.h`; `AUDIT.md` and `README.md` are
updated.

### The input device

**It already exists, and the code that looks like it should create it is commented out.**
`hal_esp32.cpp` calls `bsp_display_start_with_config()`, which detects the panel and registers
one of two pointer indevs — `bsp_display_indev_init()` for GT911 (a raw `lv_indev_create` plus a
read callback) or `bsp_display_indev_init_to_st7123()` for ST712x (via `lvgl_port_add_touch`).
The HAL's own `lvTouchpad` block, three lines below, is commented out, which is why the firmware
reads as having no touch at all.

So `ui_touch.cpp` **discovers** the indev and never creates one. Creating a second would
double-read the I2C controller and split the press/release edges between two devices — a bug
that presents as "every other tap is ignored", which is precisely the kind of symptom this
project's history gets attributed to hardware. If no pointer indev is found, the panel says so
on the face in words; the fix then belongs in `hal_esp32.cpp` or the BSP's panel detection, both
vendor files already on the README's modification list, and not in an app.

### The grade assignments

| control | grade | gesture | why that grade |
|---|---|---|---|
| screen change, MUTE / UNMUTE, FAULT CLEAR, ACKNOWLEDGE, TECHNICIAN | **1** | single tap | nothing moves, nothing is energised, a mis-tap is undone by tapping again |
| MAST UP, MAST DOWN | **2** | press and hold; re-asserts every 50 ms; release, slide-off, screen change or a lost event all stop it | a live intent with a 250 ms expiry — the value `MAST CMD` and `/api/mast/{cmd}` already enforce |
| SOFTWARE STOP, AUX RELAY 4, ADVANCED | **3** | named action + stated precondition + 1 s hold with a progress bar | discrete and consequential, and the panel can state what it will do before doing it |
| REQUEST E-STOP RE-ARM | **4** | behind ADVANCED, a visible banner, 20 s idle timeout, then a 1.5 s hold | grants energy; it is a *request* and the panel renders blockers but never holds the decision |

**The software stop is grade 3, not grade 1, and the two design notes disagree about this.**
`tab5-dashboard.md`'s grade table puts de-energize at grade 3 and argues it at length — "The
*action* is safe; the **affordance** is the hazard" — while `tab5-face-architecture.md` §1.7 says
"removing energy: one tap, no confirmation". The dashboard note is the later rewrite and the one
with the argument attached, so this follows it. **§1.7's sentence should be corrected or marked
superseded**; right now the two notes contradict each other and the next person to implement a
control will pick whichever they read first.

It is also labelled `SOFTWARE STOP`, is the same size as every other grade-3 button, is amber,
and has the mushroom-head pointer beside it. The only large red on either screen is the fault
screen's annunciation band, which is a statement and not a target.

### Three refusals, recorded in `face_mailbox.h` where a sender will read them

1. **Never `RELAY 1 x` / `RELAY 2 x`** — the lift's DOWN and UP legs. Writing them walks past the
   pinch guard. The mast moves by `MAST CMD` or not at all.
2. **Never `RELAY 0 1` to re-arm.** It works — the StamPLC's parser clears `local_estop_active`
   on it — and that is the hazard: it is the re-energize path with none of the supervisor's
   guard.
3. **Never style a soft stop as the e-stop.**

Which produces a finding for 1B, and it answers architecture-note open question 12 in the
negative. **The StamPLC has no guarded re-arm command at all.** Reading
`firmware/rc_serial_probe/src/main.cpp`'s parser: there is `ESTOP` to drop the contactor and
`RELAY 0 1` to raise it, and nothing in between. The real guard (button released, telemetry
fresh, contactor not welded, CH5 OFF) lives only in the dashboard's
`status.py::try_estop_clear`. So `MB_REQ_ESTOP_REARM` has deliberately **no command string**:
either it goes to the dashboard's guarded endpoint — whose missing authentication has to be
fixed first — or the StamPLC gains a guarded `ESTOP CLEAR`. That is 1B's decision, not this
layer's, and the intent stops at a request so that it cannot be wired to the wrong one by
accident.

### The entry gesture, which is a proposal

A **1.5 s hold on the gap between the eyes** (x 570–710, y 270–450; the eyes occupy 78–558 and
722–1202, so this is inside the bridge). No visible affordance until 400 ms in, at which point a
cyan bar fills along the bottom of the invisible region.

The dashboard note leaves this open and says what would settle it: "whether it is discoverable
enough for a student who was shown it once is a question for the first person who has to use it
in the field, and the evidence that would settle it is watching someone try." The delayed reveal
is an attempt at that — holding the wrong place teaches nothing, holding the right place shows
you both that it is working and how long is left. **It is still a proposal. Watching someone try
is still the evidence.**

---

## What is verified, and by what

| claim | how | result |
|---|---|---|
| Compiles and links against LVGL 9.2.2 and the real demo tree | `cmake --build build-desktop` on a fresh `M5Tab5-UserDemo` clone with the README's deltas applied | **pass**, zero warnings from the new files |
| The compiled-out path is real | same, with `OPENDUBS_TOUCH_UI=0` | **pass** — all four units compile to no-ops and link |
| The instrumented path is real | same, with `OPENDUBS_TOUCH_DEBUG=1` | **pass** |
| No memory-safety or UB defects on the paths a run reaches | `-fsanitize=address,undefined`, ~110 s headless (`SDL_VIDEODRIVER=dummy`), through several full state/emote/theme cycles with both screens built and `touchUiTick` running every frame | **zero AddressSanitizer and zero UndefinedBehavior findings** |
| `build.sh` syncs the new files and proves it | `build.sh`'s own `cmp` drift check | **pass**, 20 files |
| LVGL rotates touch points for a 90-degree display | read `lv_indev.c::indev_pointer_proc` in the fetched LVGL 9.2.2: `x' = ver_res - y - 1; y' = x` | **it does**, and with `x_max=720 / y_max=1280` and no swap/mirror flags in either BSP touch config the result is dimensionally consistent |
| An LVGL event callback already holds the lock | read `esp_lvgl_port.c`: `lvgl_port_lock(0) → read indevs → lv_timer_handler() → unlock` | **it does**, and the mutex is recursive (`xSemaphoreTakeRecursive`), so a nested lock would not deadlock — it would just be a lie |
| Which style properties induce a layer | read `lv_obj_style.c::calculate_layer_type` | six properties, listed in AUDIT.md §2. `opa_layered` is the opacity one; plain `opa` and every per-part `*_opa` are free |

**What the desktop verification cannot do, per AUDIT.md §10, and it matters here more than
usual:** on the desktop, LVGL is single-threaded, so **the simulator can never find a locking
bug**. Every lock claim above is from reading the call graph, not from a run.

---

## What is NOT verified

- **Nothing has been flashed.** No device build, no device image size, no frame-rate measurement.
- **The touch coordinate mapping's handedness.** The transform exists and the dimensions work
  out; whether a touch lands on the thing the operator is looking at depends on the digitiser's
  physical orientation relative to the panel, and nothing in the source settles it. **This is the
  first bench observation and nothing else should be trusted before it passes.**
- **Which indev path is live.** `bsp_detect_display_type()` picks between a GT911 and an ST712x
  panel at runtime; the two register through different code paths. The status line says an indev
  was found, not which.
- **Whether `touchUiTick`'s own cost is visible.** The grade tick walks at most 24 controls and
  polls the indev list; the readouts refresh at 4 Hz with a change-guard; a screen change is one
  full-frame redraw. All of that is *argued* to be cheap and none of it is measured. The cost
  gate's figures are for the face, which was already over budget before this work.
- **That the hidden screens cost nothing per frame.** A hidden LVGL object is not invalidated, so
  by construction it should not — but the measurement (cost gate with and without
  `OPENDUBS_TOUCH_UI`, one variable) has not been taken.
- **Every control's effect.** There is no sender. The intents are published to a mailbox nobody
  reads yet, so no control has ever moved anything.
- **The e-stop-branch power behaviour.** The Tab5 is on the e-stop-protected drive branch, so the
  panel is *off* whenever the contactor is open. Everything about how the screens come back after
  a stop is untested, and the fault screen's "E-STOP LATCHED" row may be a case the panel is
  simply never awake to show.

---

## Bench observations, in order

Flash `OPENDUBS_TOUCH_DEBUG=1` for 1–5. Each has a stated failure signature, because "it didn't
work" has cost this project whole sittings.

**1. Is there an input device?** Look at the face, third status line from the top (cyan).
- pass: `indev ok · touch: face · touch: 9/24 ctl`
- `NO INDEV AT ALL — check bsp_display_start` → the BSP's panel detection failed or
  `bsp_display_start_with_config` errored. Read the boot log for the BSP's own
  `ST712x touch panel initialized successfully` / GT911 messages. Fix belongs in the HAL or BSP.
- `NO POINTER INDEV` → something registered, but not as `LV_INDEV_TYPE_POINTER`.

**2. Does a touch land where the finger is?** A magenta crosshair follows the live point. Touch
each corner and the centre.
- pass: the crosshair sits under the finger everywhere.
- mirrored left-right → set `mirror_x` in the BSP's `esp_lcd_touch_config_t`.
- mirrored top-bottom → `mirror_y`.
- axes swapped (moving the finger right moves the cross down) → `swap_xy`.
- rotated 180° → both mirrors.
- **There are TWO copies of that config in `m5stack_tab5.c`** — `bsp_touch_new` (GT911) and the
  ST7123 block — and both need the same change, or the fix works on one panel revision only.
  Note this in the README's vendor-modification table if it is needed.

**3. The entry gesture.** Press and hold the middle of the panel, between the eyes.
- pass: nothing for ~0.4 s, then a cyan bar fills left to right near the bottom of an invisible
  region, then the technician screen at 1.5 s.
- release at ~1 s → the bar vanishes, the face stays. Tap the same spot → nothing.
- hold a corner for 3 s → nothing (if a corner *does* open it, the region constants are being
  hit-tested in the wrong coordinate space, which means observation 2 has not really passed).

**4. The readouts, their ages, and the grade gates.** `dbg_stimulus.cpp` walks eight six-second
phases and names the current one at the bottom of the screen. Each phase states its own expected
outcome; read the phase line, then check the screen agrees.
- Phase 1/8 `CH5 OFF`: MAST UP/DOWN dim, sub-label `CH5 is OFF`.
- Phase 2/8 `CH5 MANUAL`: both jogs bright, sub-label `hold to raise · release stops`.
- Phase 3/8: the BATTERY and THOR rows go dull, and an age under them counts up —
  `1.2s old`, `2.4s old`, … **The values stay on screen. If they stay bright, the whole
  argument for building native rather than streaming a JPEG is not implemented.**
- Phase 4/8: MAST DOWN dims with `at the bottom limit`; UP stays live.
- Phase 5/8: the fault screen takes the panel inside 250 ms, headline
  `POWER DISTRIBUTION HUB BROWN-OUT`, and the clearing text names the rail and FAULT CLEAR.
- Phase 6/8: headline `E-STOP LATCHED`. Press ACKNOWLEDGE → back to the face; it must **return**
  after 30 s if the phase is still active.
- Phase 7/8: headline `DRIVE CONTACTOR WELDED`, text says service. Go to TECHNICIAN:
  **FAULT CLEAR must be dim** (fatal faults are not clearable).
- Phase 8/8: contactor open → MAST jogs dim with `contactor open`, SOFTWARE STOP dims with
  `already de-energized`.
- In the first 12 s after boot the IP row must read `-- / no data`, **not** an empty value. That
  case only happens once per boot; catch it or power-cycle.

**5. The grade-2 heartbeat, which is the one that must be exactly right.** On the technician
screen in phase 2/8, read the magenta `dbg intent:` line and hold MAST UP for about two seconds.
- pass: `mast +1`, `LIVE`, age under 50 ms, and the republish count rises by roughly 40 in two
  seconds (50 ms republish).
- release: the count stops within one tick; the last publish is `mast 0`; the age counts up and
  the line flips to `EXPIRED = STOP` past 250 ms.
- **slide the finger off the button without lifting it** — the count must stop. This is the path
  the Arduino firmware needed an explicit guard for, and the one a release-event-only
  implementation gets wrong.
- **hold it and let the technician screen time out**, or trigger a fault phase while holding —
  the count must stop and the last publish must be zero. "The screen went away" has to mean "the
  lift stopped".

**6. Grade 3 and grade 4.** HOLD `SOFTWARE STOP` — the bar fills over 1 s and `req` becomes 1 with
`seq` incrementing once. Release at 0.5 s — the bar resets and `seq` does not change. Then HOLD
`ADVANCED`: the amber banner appears, MUTE/FAULT CLEAR/ADVANCED are replaced by
`REQUEST E-STOP RE-ARM` and `LEAVE ADVANCED`, and the banner leaves by itself after 20 s. The
re-arm button's sub-label must state a locally-visible blocker or
`hold 1.5s to REQUEST · the robot decides`.

**7. Soak, release build.** Flash with the flags at their defaults (`OPENDUBS_TOUCH_UI=1`,
`OPENDUBS_TOUCH_DEBUG=0`), leave it 30 minutes, entering and leaving the technician screen
occasionally.
- the face is still animating at the end;
- the `HEAD` row's `heap … free / … largest` is flat — steady-state allocation is zero
  (AUDIT.md invariant 6, and the figures that matter are internal DRAM's);
- no `dbg` text anywhere on either screen (the `OPENDUBS_TOUCH_DEBUG=0` check).

**8. The measurement that was not taken.** `build.sh cost` with `OPENDUBS_TOUCH_UI=0` and then
`=1`, nothing else changed, and compare the mean dirty area per frame. The expectation is *no
difference* — hidden objects are not invalidated — and if there is one, something is being
written to a hidden screen every frame.

---

## Things to be unsure about

| | what would settle it |
|---|---|
| **The entry gesture's discoverability.** The region and the delayed reveal are a guess at an open question. | Watch someone who was shown it once, a week later. The note already says this is the only evidence that counts. |
| **Whether the fault screen should pre-empt a technician screen someone is working on.** It does, on the grounds that a fault is new information and the readouts are not. The opposite case — someone mid-diagnosis losing their screen — is also arguable. | Whether it actually annoys anyone, once. Cheap to reverse: one branch in `touchUiTick`. |
| **The 60 s / 20 s idle timeouts.** Both invented here. The Arduino firmware has an `IDLE_MS` that could be matched instead. | The first person who gets reverted mid-task. |
| **Whether the technician screen should need the Thor at all.** `tab5-dashboard.md` leaves this open for 1B. The mailbox is deliberately source-agnostic — eight groups with independent stamps — so either answer wires in without touching the UI. | 1B. |
| **The grade-2 jog and the pinch-guard prompt are not connected.** `prompt.cpp` in the Arduino firmware renders the pinch-guard banner and the dashboard note calls its consent affordance "the sharpest case" for a local control. `RobotSnapshot.liftGuardHold` is carried and shown in the Z-AXIS row, but there is no consent affordance and no `MAST ACK` intent. | A deliberate decision about `MAST ACK`, which `pinch-guard-consent.md` leaves open and the dashboard note argues belongs here. It is the obvious next control and it was left out rather than guessed at. |
| **A USB HID mouse is also a pointer indev.** The HAL starts USB host and HID, so a mouse plugged into a bench Tab5 drives every control. Harmless and arguably useful; worth knowing before someone reports phantom touches. | Plug one in. |
| **`README.md` says clone ESP-IDF `v5.4.2`; `sdkconfig.defaults` is written for 5.5** (its comments discuss the 5.5 bootloader growth and the 5.5 P4 revision default). One of the two is stale. | Whoever next builds the device image. Not changed here, because which one is right is their call. |
