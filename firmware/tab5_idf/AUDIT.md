# Invariant checklist for the Tab5 face

**Run this instead of guessing.** Every freeze in this app so far has been one of the classes below,
and each was findable by reading the code — but was instead found by picking a plausible mechanism,
changing it, reflashing and waiting. That costs a flash, a wait and someone's attention per attempt,
and it produced four consecutive "found it" claims that were each only partly right.

**The rule: when a symptom resists two hypotheses, stop generating a third and check this list.**

Run it from the repo root against **every file in `firmware/tab5_idf/app_face/`**, not against
`app_face.cpp` alone. The second run of this checklist missed two bugs because every grep named
that one file, so `cues.cpp` — added after the checklist was written — was never searched. That is
the same failure as `build.sh` syncing a hand-maintained list of three files out of nine: **a list
of files, written once, goes stale silently.** Both now glob the directory.

**Third time: every grep below is now `grep -r`, because `app_face/*.cpp` stopped meaning "every
file" the moment `app_face/` gained subdirectories (`percept/`, `shared/`).** A flat glob against a
tree with subdirectories is the same silent-staleness bug one level down, and it would have skipped
the entire perception layer. `build.sh`'s sync and its drift check went recursive in the same
change — and the sync now *deletes* its destination first, because a copy that never removes
anything left `face_probe.cpp` compiling in the demo tree after it had been replaced.

---

## 1. Every LVGL call from the app task holds the lock

**This caused the longest-running freeze.** `applyEmote()` called `lv_label_set_text()` from an
unlocked block, mutating LVGL's label buffer and invalidation list while the LVGL task could be
mid-render. An unsynchronised LVGL call **corrupts internal state rather than failing cleanly**, so
the symptom was a hang after a *variable* delay with the heap untouched — which reads as
accumulation and is not.

```sh
grep -rc lvglLock   firmware/tab5_idf/app_face/
grep -rc lvglUnlock firmware/tab5_idf/app_face/   # must match
```

**THIS CHECK FAILED IN PRACTICE, 2026-10-05, and the failure was mine.** I ran the balance count,
got 7/7, wrote "invariant 1 clean" — and there were **four unlocked LVGL calls** in `onRunning` at
that moment, which were freezing the face. I then spent an hour on task priorities, core affinity,
mutex identity and scheduler starvation. The document already said balance was insufficient; I
checked it anyway because it is the easy check. So: **run the call-graph check below, not the
count.** The count cannot find the bug this invariant exists to catch.

```sh
# Flags any LVGL mutation reached while the lock is NOT held, per function.
awk '/^(void|bool|int|float|lv_obj_t\*).*\(/ {fn=$0; d=0}
     /lvglLock/   {d=1}
     /lvglUnlock/ {d=0}
     /lv_[a-z_]*_(set|add|remove|create|del)/ && d==0 && !/^[[:space:]]*\/\// {
         print FILENAME":"NR"  UNLOCKED in "fn"\n    "$0 }' \
  $(find firmware/tab5_idf/app_face -name '*.cpp')
```

### The structural check that makes the call-graph check small

The call-graph check above is still the one that finds the bug — but as of the motion-saliency
change there is far less for it to search, because **most of the code that used to be able to make
an LVGL call now cannot name an LVGL symbol at all.** `app_face/percept/` is pure compute: it is
handed a pixel buffer, it writes pixel buffers, it publishes POD structs through a seqlock. It
includes neither `<lvgl.h>` nor `hal/hal.h` — and *both* halves of that matter, because `hal/hal.h`
includes `<lvgl.h>` itself, so a module that reached for `GetHAL()->millis()` would have pulled LVGL
in while passing a grep for `#include <lvgl.h>`. That is why `shared/clock.h` exists instead of one
convenient include.

```sh
# Perception may not reach LVGL, directly or through the HAL. Expect NO output.
# Anchored at the start of the line, so a COMMENT that quotes the include it is forbidding does
# not read as a violation of it. (It did.)
grep -rnE '^ *#include *[<"](lvgl\.h|hal/hal\.h)[>"]' firmware/tab5_idf/app_face/percept/ \
                                                      firmware/tab5_idf/app_face/shared/
# Nor may it name an LVGL symbol or take the lock. Expect NO output.
grep -rnE '\blv_[a-z_]+\(|lvglLock|lvglUnlock' firmware/tab5_idf/app_face/percept/ \
                                               firmware/tab5_idf/app_face/shared/
# Every task is pinned. An UNPINNED priority-2 perception task preempting the priority-1
# animation task on its own core is a failure this project has already had, and pinning -- not
# the priority number -- is what prevents it. Expect NO output.
grep -rn 'xTaskCreate(' firmware/tab5_idf/app_face/
```

Current call graph for the perception side, all covered: **nothing in `percept/` is on it.** The
LVGL work that used to live in the detector task — a hidden camera canvas, a preview canvas, a
reticle, and a lock acquisition per frame — is now `cameraStep()`, `buildPerceptDebug()` and the
gaze block in `app_face.cpp`, all on the main task, each taking the lock around its own calls.

It is crude — it tracks lock depth linearly and does not follow calls into helpers — so it
**over**-reports (comments, and anything after an early `return` inside a locked block) and must be
read rather than trusted. That is still strictly better than a count, which cannot report at all.
Builder helpers called from a locked caller are the known false positives; the table below records
which those are.

Counting lines is necessary but **not sufficient** — it must be checked by call graph, because the
build helpers (`buildScene`, `band::build`, `theme::buildAll`, `cobweb`) touch LVGL while their
caller holds the lock. Current call graph, all covered:

| function | callers | locked |
|---|---|---|
| `buildScene`, `band::build`, `theme::buildAll`, `cobweb`, `plainBox` | `onOpen` | yes |
| `theme::apply`, `theme::hideAll` | theme-cycle block | yes |
| `applyEmote`, `applyState` | `onOpen`, theme block, state block, emote block | yes |
| `band::update`, the eye render loop | `onRunning` | yes |
| `buildPerceptDebug` | `cameraStep` only, one call site | yes |
| `reticleColourFor` | the gaze block only, one call site | yes |
| `costGateBegin` | `onOpen`; desktop-only translation unit | yes |

**Run, 2026-10-05, motion-saliency change: clean.** Lock balance 15/15 in `app_face.cpp` and zero
in every other file; the call-graph pass reports only the ten builder/helper functions in the table
above, each verified at its call sites; the structural greps for `<lvgl.h>`/`hal/hal.h`, for any
`lv_*` symbol and for an unpinned `xTaskCreate(` under `percept/` and `shared/` all return nothing.
The balance count is reported here only because the call-graph pass was also run — on its own it
means nothing, as the note above it says at length.

## 2. No layer-inducing styles

Rotation, `transform_*` and some blend modes make LVGL render the object through a **layer**,
allocating a buffer the size of its bounding box **per draw**. This killed the rotated brow outright
(`lv_draw_buf_create_ex: No memory: 438x42`) and was nearly reintroduced by a `transform_scale_x`
call in the cobweb mirroring.

```sh
grep -rnE "transform_|blend_mode" firmware/tab5_idf/app_face/   # expect none
```

Mirror by negating coordinates; angle with `lv_line`, never with rotation.

## 3. No complex gradients on animated geometry

Radial and conical gradients are cached **per geometry**, so an object that animates its size mints
a new gradient every frame. Disabled globally in `lv_conf.h`; the eye uses linear `VER`.

```sh
grep -rnE "LV_GRAD_DIR_(RADIAL|CONICAL)" firmware/tab5_idf/app_face/   # expect none
# lv_conf.h governs the DESKTOP build only -- the device sets CONFIG_LV_CONF_SKIP=y and is
# configured by Kconfig, so check BOTH or a flag will appear set when it is not compiled in.
grep -n  "LV_USE_DRAW_SW_COMPLEX_GRADIENTS" firmware/tab5_idf/lv_conf.h           # desktop
grep -n  "CONFIG_LV_USE_DRAW_SW_COMPLEX_GRADIENTS" firmware/tab5_idf/sdkconfig.defaults  # device
```

## 4. No unbounded loops in the render path

`while (phase > 1.0f) phase -= 1.0f;` is **O(value)**, so one long frame makes it iterate millions
of times — a CPU hang with the heap untouched. Use `fmodf`, and clamp any `dt` derived from a clock.

```sh
grep -rnE "while *\(" firmware/tab5_idf/app_face/   # expect none outside comments
```

## 5. Deadline tests are wrap-safe

`now > deadline` inverts when the 32-bit millisecond counter wraps at **~49 days** of uptime, after
which every timer either fires continuously or never again. Use the `due()` helper.

```sh
grep -rnE "now[A-Za-z]* *[<>]=? *g_" firmware/tab5_idf/app_face/   # expect none
```

### Second run, 2026-10-05: two findings, both in the file the checklist did not search

`app_face.cpp` passed invariants 1-6 clean (lock 7/7, no layer styles, no complex gradients, no
unbounded loops, no wrap-unsafe deadlines, every allocation inside a startup builder). `cues.cpp`
violated invariant 5 **twice**:

| site | raw test | what the wrap does |
|---|---|---|
| `cuePlay` busy gate | `now < g_busyUntil` | every cue of priority <= the last one is suppressed until a higher-priority cue happens to fire and rewrites the deadline — recoverable |
| `cueTick` fault repeat | `nowMs < g_nextFault` | **permanent.** The early return happens *before* `g_nextFault` is advanced, so the repeating fault announcement never fires again |

The second one is the interesting one: the same raw comparison is a transient bug in one place and
an unrecoverable one in the other, purely because of whether the deadline is updated before or
after the guard. Fixed with the `due()` helper in both.

Worth noting what this says about the checklist itself: it was written from the bugs that had
already bitten, and it worked — it found these two by reading, in the time a single reflash costs.
What it got wrong was its own *scope*.

## 6. Nothing allocates in the steady state

Objects are built once and shown or hidden; the first accessory layer created and deleted 14 objects
per theme change, which is churn on a loop. The only remaining steady-state allocation is
`lv_label_set_text`, once per emote change.

```sh
grep -rnE "lv_obj_create|lv_obj_del|lv_line_create|lv_canvas_create" firmware/tab5_idf/app_face/
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
