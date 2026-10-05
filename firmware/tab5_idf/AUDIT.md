# Invariant checklist for the Tab5 face

**Run this instead of guessing.** Every freeze in this app so far has been one of the classes below,
and each was findable by reading the code — but was instead found by picking a plausible mechanism,
changing it, reflashing and waiting. That costs a flash, a wait and someone's attention per attempt,
and it produced four consecutive "found it" claims that were each only partly right.

**The rule: when a symptom resists two hypotheses, stop generating a third and check this list.**

Run it from the repo root against `firmware/tab5_idf/app_face/app_face.cpp`.

---

## 1. Every LVGL call from the app task holds the lock

**This caused the longest-running freeze.** `applyEmote()` called `lv_label_set_text()` from an
unlocked block, mutating LVGL's label buffer and invalidation list while the LVGL task could be
mid-render. An unsynchronised LVGL call **corrupts internal state rather than failing cleanly**, so
the symptom was a hang after a *variable* delay with the heap untouched — which reads as
accumulation and is not.

```sh
grep -c lvglLock   firmware/tab5_idf/app_face/app_face.cpp
grep -c lvglUnlock firmware/tab5_idf/app_face/app_face.cpp   # must match
```

Counting lines is necessary but **not sufficient** — it must be checked by call graph, because the
build helpers (`buildScene`, `band::build`, `theme::buildAll`, `cobweb`) touch LVGL while their
caller holds the lock. Current call graph, all covered:

| function | callers | locked |
|---|---|---|
| `buildScene`, `band::build`, `theme::buildAll`, `cobweb` | `onOpen` | yes |
| `theme::apply`, `theme::hideAll` | theme-cycle block | yes |
| `applyEmote`, `applyState` | `onOpen`, theme block, state block, emote block | yes |
| `band::update`, the eye render loop | `onRunning` | yes |

## 2. No layer-inducing styles

Rotation, `transform_*` and some blend modes make LVGL render the object through a **layer**,
allocating a buffer the size of its bounding box **per draw**. This killed the rotated brow outright
(`lv_draw_buf_create_ex: No memory: 438x42`) and was nearly reintroduced by a `transform_scale_x`
call in the cobweb mirroring.

```sh
grep -nE "transform_|blend_mode" firmware/tab5_idf/app_face/app_face.cpp   # expect none
```

Mirror by negating coordinates; angle with `lv_line`, never with rotation.

## 3. No complex gradients on animated geometry

Radial and conical gradients are cached **per geometry**, so an object that animates its size mints
a new gradient every frame. Disabled globally in `lv_conf.h`; the eye uses linear `VER`.

```sh
grep -nE "LV_GRAD_DIR_(RADIAL|CONICAL)" firmware/tab5_idf/app_face/app_face.cpp   # expect none
grep -n  "LV_USE_DRAW_SW_COMPLEX_GRADIENTS" firmware/tab5_idf/lv_conf.h           # expect 0
```

## 4. No unbounded loops in the render path

`while (phase > 1.0f) phase -= 1.0f;` is **O(value)**, so one long frame makes it iterate millions
of times — a CPU hang with the heap untouched. Use `fmodf`, and clamp any `dt` derived from a clock.

```sh
grep -nE "while *\(" firmware/tab5_idf/app_face/app_face.cpp   # expect none outside comments
```

## 5. Deadline tests are wrap-safe

`now > deadline` inverts when the 32-bit millisecond counter wraps at **~49 days** of uptime, after
which every timer either fires continuously or never again. Use the `due()` helper.

```sh
grep -nE "now > (g_next|next)" firmware/tab5_idf/app_face/app_face.cpp   # expect none
```

## 6. Nothing allocates in the steady state

Objects are built once and shown or hidden; the first accessory layer created and deleted 14 objects
per theme change, which is churn on a loop. The only remaining steady-state allocation is
`lv_label_set_text`, once per emote change.

```sh
grep -nE "lv_obj_create|lv_obj_del|lv_line_create" firmware/tab5_idf/app_face/app_face.cpp
```
All hits must be inside `buildScene`/`band::build`/`cobweb`, i.e. startup only.

## 7. Fixed pools have headroom, and overflow is not silent

The cobweb point pools were sized at *exactly* their demand (8 strands, 6 threads for two webs), and
the loop guards simply stop drawing when full — a third web would vanish with no error. Now sized
for four webs.

## 8. Every per-frame write is change-guarded

Each `lv_obj_set_*` invalidates and schedules a redraw, so an unguarded write costs its area every
frame whether or not anything moved. `lv_line_set_points` on the brow was the one that slipped
through, costing ~16 k px per eye per frame while the brow was static.

## 9. Budget check

Dirty area per frame must stay well under one screen: the measured blit rate is ~16 Mpx/s against
921,600 px per screen, so a full redraw at 30 fps is already 1.7× over. See
`notes/design/tab5-platform.md` §6b.

---

## 10. The desktop build must be current, and it has a blind spot

**It went stale and nobody noticed**, which is worse than not having it: the simulator was showing
code 45 minutes and several features old, so it could not have validated anything, and "it looks
fine on the laptop" meant nothing. `build.sh both` builds device and desktop from one command so
they cannot diverge — use it rather than building either alone.

**The blind spot matters and is structural.** On the desktop, `app::Update()` calls
`lv_timer_handler()` on the main thread, so **LVGL is single-threaded there**. The unlocked
`lv_label_set_text()` that caused the longest freeze is therefore *impossible to reproduce on the
desktop* — there is no second thread to race with. The simulator will never find a locking bug.

A **third** use, beyond design iteration and the sanitizers: **evaluating an authored
configuration**. Dirty area per frame is a *geometric* property, not a CPU property — so the
desktop cannot tell you whether something runs on the Tab5, but it *can* count the pixels a
configuration would invalidate per frame, and that number is identical on both. Measured against
the device's ~16 Mpx/s, that catches a theme which would freeze the robot without putting it on the
robot. See `notes/design/face-config.md`.

| the desktop is good for | the desktop is blind to |
|---|---|
| geometry, proportion, colour, motion feel | **anything involving the LVGL lock** |
| iterating at full frame rate, no flashing | the real frame budget (it has far more CPU) |
| **AddressSanitizer / UndefinedBehaviorSanitizer** | layer allocation failures (it has GBs of heap) |
| crashes with a usable stack trace | panel-specific rendering |

**First run, 2026-10-05: clean.** 1,877 sanitizer symbols and the ASan runtime confirmed linked,
run through several full theme cycles — **no AddressSanitizer or UndefinedBehavior findings at
all.** That is a real if negative result: the point-pool sizing, the loop guards and the `expf`
divisor hold up under instrumentation.

One caveat on coverage, worth knowing before trusting a clean run: **`mclog` output does not reach
stdout on the desktop build**, so a clean log cannot by itself prove which code paths were
exercised. Confidence here comes from the run length against the 18 s theme cycle, not from the log.

**The sanitizers are the thing the simulator is uniquely good for**, and were not being used.
`build.sh asan` builds the native binary with `-fsanitize=address,undefined`, which catches exactly
the classes that are invisible on the device: array overruns — the cobweb point pools were sized at
precisely their demand — use-after-free, and the integer and float UB that becomes a NaN in a style
value. Run it after any change to geometry or pool sizing.

## What is NOT on this list, and why

**Memory.** Three rounds went into leaks, fragmentation and cache churn because the symptom took
time to appear. The on-screen heap readout settled it instantly: **23 MB free, 22 MB largest block,
and frozen.** Put a falsifying measurement on the panel *first* — it costs one line and eliminates
a whole category. Console telemetry cannot do this job, because a console write blocks the calling
task when no host is draining the port, making the instrument a suspect for the symptom.
