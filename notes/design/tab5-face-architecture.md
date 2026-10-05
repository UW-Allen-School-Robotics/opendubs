# Tab5 face — software resource architecture

**Status: design, 2026-10-05. Nothing here has been built or flashed.** Written from the sources in
`firmware/tab5_idf/`, the ephemeral `M5Tab5-UserDemo` build tree it is applied to (IDF 5.5.5, the
`m5stack_tab5.map` of the current 3.94 MB probe image), and the design notes it cites. Where a
number is measured it says so; where it is read from configuration it says so; where it is an
estimate it says so and names the measurement that would replace it.

**Why this exists.** Features have been landing one at a time, each destabilising the last: four
unlocked LVGL calls froze the face; a priority-2 unpinned perception task preempted the
priority-1 animation task on its own core; a 20 Hz debug preview hogged the single LVGL lock and
parked `main` in eBlocked. Each was found after the fact. This document plans the resources once
so that a new feature has a slot to go into and cannot take the face down by landing in the wrong
one. Settled and not reopened: LVGL (not a bespoke renderer); the StamPLC firmware (not ported);
the perception design (motion saliency primary, face detection a close-range confirmation).

Companion notes: `tab5-platform.md` (hardware inventory, graphics budget), `face-design.md` (the
behaviours), `face-config.md` (what may be authored), `tab5-idf-ota.md` (OTA), `tab5-dashboard.md`
(the panel as a control surface — its "displays, does not command" rule is **superseded** here, see
§1.7), `tab5-face-tracking.md` (the camera pipeline as read from source), `../workplan-tab5-idf.md`.

---

## 0. Three facts read from the build tree that change the plan

These were not in the brief and each moves a decision below.

1. **A 956 KB MP3 we never play is in the shipping image.** `platforms/tab5/main/CMakeLists.txt`
   embeds `../audio/canon_in_d.mp3` (956,208 bytes) with `EMBED_TXTFILES`, and
   `hal_audio.cpp`'s music-test code references it so the linker keeps it. It is 24% of the
   3.94 MB image and **almost exactly the flash cost of esp-dl** (esp-dl 726 KB + model-in-rodata
   196 KB = 922 KB). Dropping it is a two-line edit to vendor files we already track changes to.
   The flash question in §8 looks different with this known.
2. **The LVGL render task is unpinned.** `ESP_LVGL_PORT_INIT_CONFIG()` is `priority 4, stack 7168,
   affinity -1, 5 ms tick`; the vendor HAL passes it unchanged. So the task that holds the lock and
   draws every frame floats between cores, which means "pin perception away from the UI core" is
   not currently a meaningful instruction — there is no UI core. Pinning it is one field in a file
   we already modify.
3. **Display rotation is already hardware.** The panel is natively 720×1280 portrait; the vendor
   HAL rotates 90° via `sw_rotate`, and in `esp_lvgl_port_disp.c` the 90° branch calls
   `rotate_copy_pixel()` which is a **PPA** `ppa_do_scale_rotate_mirror` per flushed area, then
   `esp_lcd_panel_draw_bitmap` (DMA2D) into the single DSI framebuffer. The CPU cost of a frame is
   therefore LVGL's software rasterisation only; the rotate and the copy are DMA. Do not spend
   effort "fixing" rotation. (The 180°/270° branches are software, but we use 90°.)

Also worth stating because it was wrong in the brief: `hal_camera.cpp` **does** yield — its loop
ends in `vTaskDelay(pdMS_TO_TICKS(10))` and `VIDIOC_DQBUF` blocks until a frame arrives. If the
"never yields" observation came from a different revision of that file, re-read before relying on
either version. It does not change the plan (we stop using that loop, §2.3) but it should not be
repeated as a fact.

---

## 1. Responsibility inventory, with costs

Measured baselines the costs are set against:

| resource | number | source |
|---|---|---|
| internal DRAM free at `heap_init` | **~250–310 KiB** | brief; consistent with the map: static D/IRAM used 209 KB (IRAM .text 165 KB of which LVGL fast-mem 64 KB; .data 24 KB; .bss 20 KB), 236 KB remaining in that region |
| PSRAM | 32 MB; **21.2 MB free after the face-detect model loaded** | probe log |
| flash image | **3,943,308 B** used of a 5 MB slot with esp-dl; 2.92 MB without | map; brief |
| per-frame draw budget | ~16 Mpx/s *software rasterisation*, miscalibrated (measured on ellipses, applied to fills); real figure unknown | `tab5-platform.md` §6b |
| LVGL refresh period | 33 ms (`CONFIG_LV_DEF_REFR_PERIOD=33`); port tick 5 ms | sdkconfig, port |
| camera | 1280×720 RGB565 @ 30 fps; 2 V4L2 buffers + 1 PPA output = 3 × 1.84 MB PSRAM | `hal_camera.cpp` |
| display buffers | 2 full-frame LVGL draw buffers + 1 rotation buffer + 1 DSI framebuffer = 4 × 1.84 MB PSRAM | `hal_esp32.cpp` (`buffer_size = 720*1280, double_buffer = true`), port, BSP `num_fbs = 1` |
| code in PSRAM | .text 2.11 MB + .rodata 1.76 MB copied to PSRAM at boot (`SPIRAM_XIP_FROM_PSRAM`) | sdkconfig, map |
| allocator policy | **every `malloc` under 16 KB lands in internal DRAM** (`SPIRAM_MALLOC_ALWAYSINTERNAL=16384`); 32 KB internal reserved for DMA/internal-only | sdkconfig |
| face inference | 25–35 ms per 320×240 RGB888 frame on one core | probe |

"Lock pressure" below means acquisitions of the one LVGL mutex per second from outside the LVGL
task, and the longest hold. The target in §2 is **zero** from application code.

### 1.1 Eye/emote animation, band, quotes, themes, 7-state machine (exists)

| | |
|---|---|
| CPU | the dominant consumer of **CPU0**: LVGL rasterises the dirty area each refresh. Estimated dirty area after the 2026-10-05 fixes ~131 k px/frame worst case (`tab5-platform.md`), i.e. ~4 Mpx/s at 30 fps. Animation arithmetic itself is <1 ms/frame. **Unmeasured as a CPU%** — `LV_USE_PERF_MONITOR` is now genuinely on and its CPU% + FPS readout is the baseline every other number here waits for. |
| internal DRAM | ~40 LVGL objects × ~150–250 B (`LV_USE_CLIB_MALLOC`, so every object is a `malloc` under 16 KB → internal) ≈ 8–12 KB + styles; label text re-allocated on every `lv_label_set_text` (steady-state churn, see §4.3) |
| PSRAM | none of its own (draw buffers are the port's) |
| flash | app ~3 KB + LVGL 418 KB + referenced fonts (`montserrat_40` alone is 71 KB; unused sizes are already dropped by the linker — fonts are **not** a lever) |
| lock | today: 7 lock sites in `onRunning` plus the per-frame render block, from `main`. After §2: zero — the animation runs inside the LVGL task. |

### 1.2 Audio out (earcons now, speech later) and audio in (mic now, wake/voice later)

| | |
|---|---|
| CPU | earcon playback: ~0 (I2S DMA; the task blocks in `i2s_write`). Speech: Opus decode of a 16 kHz mono stream is a few % of one core; raw PCM is ~0. Mic: `i2s_read` of 4 ch × 48 kHz × 16-bit = **384 KB/s**; downmix to 16 kHz mono + RMS per 20 ms frame ≈ 1–2% of a core; a wake-word model later is a model-sized question (esp-sr WakeNet is on the order of 10–20% of a core continuously — **unmeasured here**). |
| internal DRAM | I2S DMA descriptors/buffers (driver-owned, a few KB each direction). **Today's bug:** `cues.cpp` renders the `Heard` cue to a 13.4 KB `std::vector` — under 16 KB, so it lands in internal DRAM; the HAL's `audioPlay` then **copies** the vector per play (`_audio_task_data.audio_data = data`), another internal allocation per cue. `audioRecord` `resize()`s per call. All fixable by explicit `MALLOC_CAP_SPIRAM` and preallocated rings. |
| PSRAM | earcon PCM ≈ 260 KB total at 48 kHz stereo (Boot 77 KB, Mode 65 KB, Fault 61 KB, Refuse 67 KB, Attend 30 KB, Heard 13 KB); speech ring 1 s @ 16 kHz mono = 32 KB; mic capture ring 100 ms @ 384 KB/s = 38 KB |
| flash | esp_driver_i2s + codec_dev ≈ 40 KB (present). Opus later ≈ 150–250 KB. Wake word later 300–800 KB. **Vendor leftovers: `canon_in_d.mp3` 956 KB, `libhelix` 36 KB, `esp-audio-player` — removable.** |
| lock | zero; audio never touches LVGL. The "speaking" band activity is a published envelope value the UI reads. |

### 1.3 Camera perception (design given; fitted here)

| | |
|---|---|
| CPU | **CPU1 only.** Frame acquisition ≈ 0 CPU (V4L2 + PPA downscale, both DMA). Motion saliency on 160×90 luma: 14,400 abs-diffs + a 16×9 cell accumulate ≈ 0.1 ms. Face detection 25–35 ms per run: at 4 Hz ≈ 12% of CPU1, at 10 Hz ≈ 30%. **Today's software downscale (320×180 strided reads from a 1.84 MB PSRAM buffer, plus RGB888 conversion) is ~57,600 iterations/frame — a few ms — and goes away when the PPA scales.** |
| internal DRAM | task stack 6–8 KB (or in PSRAM via `xTaskCreateWithCaps`, allowed: `CONFIG_SPIRAM_ALLOW_STACK_EXTERNAL_MEMORY=y`); **esp-dl's internal footprint is unmeasured** and is the one number that decides §8 — measure `heap_caps_get_free_size(MALLOC_CAP_INTERNAL)` before `new HumanFaceDetect()`, after it, and after the first `run()`. |
| PSRAM | V4L2 2 × 1.84 MB (required); PPA outputs 320×180×2 = 115 KB and 160×90×2 = 29 KB; previous luma 14 KB; RGB888 detector input 173 KB; model weights + tensors ~10 MB (the 21.2 MB-free figure already includes them). **The vendor's 1.84 MB `img_show` full-res mirrored copy and its 55 MB/s of PSRAM write bandwidth disappear** when we stop using `startCameraCapture`. |
| flash | esp_video + ISP + cam drivers ≈ 80 KB (present); esp-dl 922 KB with model in rodata, 726 KB + a 191 KB `human_face_det` partition image with model in partition |
| lock | **zero.** Perception publishes a `GazeIntent`; it does not include `lvgl.h`. The debug preview is painted by the UI task polling a frame sequence number. The vendor camera task's per-frame `bsp_display_lock(0)` also disappears. |

### 1.4 IMU: self-motion gating and the vestibulo-ocular reflex

| | |
|---|---|
| CPU | BMI270 burst read (12 bytes @ 400 kHz I²C ≈ 0.4 ms bus time) at 100 Hz ≈ 4% of CPU1 wall time, almost all of it blocked on I²C, ~0 compute. VOR integration is a handful of multiplies per frame in the UI tick. |
| internal DRAM | shared `sense` task stack 4 KB; a 32-byte seqlock sample |
| flash | `sensor_bmi270` 22 KB (present) |
| lock | zero |
| constraint | the BMI270 is on the **internal** I²C bus with the touch controller (read by the LVGL task), the codec, the IO expanders, INA226 and RTC. `i2c_master` serialises per bus; a 100 Hz IMU read adds ≤0.5 ms of bus occupancy ahead of a touch read. Acceptable; keep IMU reads to one burst, never a register-by-register loop. |

### 1.5 Pan/tilt servos (8Servo at 0x25, external I²C, Port A GPIO 53/54)

| | |
|---|---|
| CPU | two 4-byte register writes at 50 Hz ≈ 0.2 ms bus time per tick; the integrator is trivial. Stall-current homing at boot is a ~20 s sequence (as in the Arduino firmware) — runs in `sense` before the servo loop starts. |
| internal DRAM | part of `sense`; `HeadState` seqlock 32 B |
| flash | ~2 KB |
| lock | zero. The UI consumes `HeadState` (pan/tilt actual + moving flag) for eyes-lead-head-follows and for gating motion saliency; it never writes I²C. |
| per-robot state | tilt limits in NVS (`tiltMin`/`tiltMax`), re-derivable by homing. Servo direction (`kPanReverse`/`kTiltReverse`) is per-mechanism and belongs in NVS too, not in source (`per-robot-values-not-in-repo`). |

### 1.6 Front ToF (VL53L0X/L1X at 0x29, same external bus)

| | |
|---|---|
| CPU | one 2–4 byte status/range read at 20 Hz ≈ 0.1 ms; L1X init blob once |
| flash | ~3 KB (raw register sequences lifted from `lift.cpp`) |
| lock | zero; publishes distance + freshness in `SenseState` |
| ownership | **one task owns the external bus** (`sense`), so the ToF and 8Servo cannot contend by construction. |

### 1.7 Touch UI, including controls that actuate the robot

The dashboard note's rule — "the head displays; it does not command" — is superseded at the user's
direction. The reasoning: physical reachability is not a new exposure (anyone who can touch the
screen can touch the robot; the e-stop is deliberately reachable); the dashboard API's missing
authentication is a defect to fix on the API, not an argument about where a UI lives; and the
person standing at the robot is the one who most needs local control — jogging the mast while
watching clearance is the canonical case. **Design for confirmation and intent, not prohibition.**

| | |
|---|---|
| CPU | touch is read inside the LVGL task (`LVGL_PORT_EVENT_TOUCH`, I²C to the ST7123 at 100 kHz); handlers run as LVGL events, under the lock, on CPU0. A screen switch is one full-frame redraw (921,600 px, once, ~60 ms) — fine as an event, never as a loop. While the controls screen is shown the face timers are paused, so the two never compete. |
| internal DRAM | a second pre-built LVGL screen of ~30 objects ≈ 6–8 KB, built once at startup, hidden |
| flash | ~20–50 KB of page code and labels |
| lock | zero explicit — handlers already run in the LVGL task |
| intent model | **removing energy: one tap, no confirmation** (`ESTOP`, as today). **Jogging (mast up/down, pan/tilt D-pad): press-and-hold = a request republished at 20 Hz as a heartbeat; release, touch loss, screen change or link loss = STOP** — the StamPLC's pinch guard and deadman still govern, exactly as the Arduino firmware's `Lift.request()` path does. **Granting energy (`estop/clear`, re-arm, mode changes): hold-to-confirm ≥1.5 s with a visible progress ring, then the request is sent; the StamPLC's own re-arm preconditions decide.** The UI never opens a socket: it writes `UiIntent` (seqlock) and `net` sends. |

### 1.8 Wi-Fi station, UDP :4242 text commands, OTA by HTTP pull

| | |
|---|---|
| CPU | idle traffic ≈ 0; the 20 Hz status broadcast ≈ 0; OTA ≈ 10–20% of CPU1 for the duration (HTTP receive + flash write); esp_hosted SDIO at 40 MHz is DMA |
| internal DRAM | **esp_hosted + lwIP is the largest internal consumer we do not control**: `SPIRAM_TRY_ALLOCATE_WIFI_LWIP` is **not** set, so Wi-Fi RX/TX buffers (10 static RX, 32 dynamic RX, 32 dynamic TX) and lwIP pools are internal; esp_hosted's SDIO mempool must be internal DMA memory (the Arduino firmware's `sdio_mempool_create` assert at 134 KB free proves it). **Estimate 60–100 KB; measure** `MALLOC_CAP_INTERNAL` before/after `esp_wifi_start()`. `net` stack 6 KB. |
| PSRAM | OTA receive buffer 4–16 KB (force PSRAM) |
| flash | lwIP 135 KB, esp_hosted 99 KB, esp_netif, http_parser (present); `esp_http_client` + `esp_https_ota` + `app_update` ≈ +60–100 KB (not yet linked — `esp_http_server` is, for the vendor AP demo, and goes) |
| lock | zero. `STATE`, `GAZE`, link-age and OTA progress are published values the UI reads. |
| hard requirement | the stall deadline (`tab5-idf-ota.md`): `esp_https_ota_perform()` in **our** loop with a 10 s no-progress abort; never the one-shot `esp_https_ota()`. With `SPIRAM_XIP_FROM_PSRAM=y` flash writes should not stall code execution (code runs from PSRAM, not flash) — **expected, not measured; measure frame-time jitter during an OTA.** |

### 1.9 State sync from the StamPLC (`STATE <NAME>`)

Negligible in every column. Arrives on :4242, parsed in `net`, written to `RobotState` (seqlock
with a timestamp). Link loss is derived in the UI from the timestamp age (5 s, as today), so the
display cannot show a stale state as live.

### 1.10 Choreography / configuration

| | |
|---|---|
| CPU | parse + validate at boot ≈ 50–200 ms for a few tens of KB of JSON (cJSON); zero steady-state |
| internal DRAM | cJSON tree is transient (parse → copy into fixed POD tables → free). The `FaceConfig` POD ≈ 4–8 KB — put it in PSRAM explicitly (it is read a few times per second, not per pixel). |
| PSRAM | theme bitmaps if themes become assets: a 480×480 RGB565 eye body is 460 KB (691 KB with A8); budget ~4 MB for an active theme's layers — comfortably inside 21 MB |
| flash | littlefs ≈ 30 KB + cJSON ≈ 20 KB; partitions per §5 |
| lock | zero. Config is applied by the UI task at its own tick; the loader is pure data. |

### 1.11 Summary: where the budget is tight

| resource | tight? | the one measurement to take first |
|---|---|---|
| CPU0 | **yes, structurally** — LVGL rasterisation is single-core and uncounted | `LV_USE_PERF_MONITOR` CPU% on the full face scene, idle and at the ALERT/thinking peak |
| CPU1 | no — ~50% headroom even with face detection at 10 Hz and audio | FreeRTOS run-time stats (`CONFIG_FREERTOS_GENERATE_RUN_TIME_STATS=y` already) over 60 s |
| internal DRAM | **yes, the binding constraint** — ~250–310 KiB total, Wi-Fi + esp-dl + stacks + LVGL objects all want it | `heap_caps_get_free_size(MALLOC_CAP_INTERNAL)` and largest block, logged at each init milestone and shown on the panel (today's HUD shows `esp_get_free_heap_size()`, which **includes PSRAM** — 23 MB — and says nothing about the constraint) |
| PSRAM | no — ~21 MB free after everything large is allocated | none needed; watch the trend |
| flash | no, once `canon_in_d.mp3` is dropped and slots are 6 MB (§5) | `idf.py size-components` after each dependency change |

---

## 2. Task, core and priority plan

### 2.1 The invariant and how it is made structural

**The invariant:** the task that animates the face is never preempted by best-effort work and never
waits on best-effort work for the lock.

Two ways to satisfy it: priority arithmetic (keep every best-effort task below the animation task
and hope nobody adds one above) or **core isolation** (the animation task and the renderer live on
CPU0; every task we write that is not the UI lives on CPU1). Core isolation is the one where a
mistake is impossible rather than detectable: a priority-2 perception task pinned to CPU1 cannot
preempt anything on CPU0 regardless of its number. The plan uses core isolation and *also* keeps
the priority ladder sane, so a task accidentally left unpinned still ranks below the UI.

**The second structural move: the animation runs *inside* the LVGL task.** `esp_lvgl_port`'s task
loop is `lock → read indevs → lv_timer_handler() → unlock → vTaskDelay(1)`. An `lv_timer` created
by our code runs inside `lv_timer_handler()`, i.e. inside the lock, on the LVGL task. If the face
animation is an `lv_timer` at 33 ms, then **every LVGL call it makes holds the lock by
construction**, there is no second task racing the renderer, and `lvglLock()` disappears from
application code entirely. The invariant-1 check becomes `grep -rc lvglLock firmware/tab5_idf/
→ 0` plus `grep -rl lvgl.h firmware/tab5_idf/ → only ui/`, which cannot miss a call site the way
counting lock/unlock balance did.

This also removes the main-task-stack question (`CONFIG_ESP_MAIN_TASK_STACK_SIZE=4000`): the
animation no longer runs there. Mooncake's `onRunning` stays as a no-op or for boot-time work
(config load) that happens before the UI exists.

### 2.2 The map

```
CPU0 — UI core                                   CPU1 — compute / IO core
───────────────────────────────────────────      ───────────────────────────────────────────
taskLVGL   prio 4  stack 12 KB  (vendor port,     audio      prio 5  stack 4 KB   I2S feed + mic
           pinned CPU0 — one field change)        cam        prio 3  stack 4 KB   V4L2 → PPA → frames
  ├ lv_timer ui_tick    33 ms  face/band/VOR       sense      prio 3  stack 4 KB   IMU 100 Hz, ToF 20 Hz,
  ├ lv_timer ui_mailbox (in ui_tick, first)                                        8Servo 50 Hz (owns ext I²C)
  ├ lv_timer ui_hud     250 ms (debug build)       net        prio 3  stack 6 KB   STA, UDP :4242, status 20 Hz,
  ├ touch indev read    (port, LVGL events)                                        OTA/CFG pull w/ stall deadline
  └ lv_timer dbg_preview 100 ms (debug build)      percept    prio 2  stack 8 KB*  motion, arbiter, face det
main       prio 1  stack 4000  mooncake loop,      mon        prio 1  stack 3 KB   liveness check, telemetry
           boot-time config load, nothing LVGL                                     drain (debug build)
system: esp_timer 22, ipc 24                       system: lwIP 18 (pin here), esp_hosted/wifi events,
                                                   FreeRTOS timer svc 1
```
`*` perception's stack may live in PSRAM (`xTaskCreateWithCaps(..., MALLOC_CAP_SPIRAM)`, enabled
in this sdkconfig) if internal DRAM gets tight; it does no flash I/O and runs no ISRs, so it is
the one task where that is safe.

Rationale per row:

- **taskLVGL at 4, pinned CPU0.** Highest of anything we control on CPU0. Its stack grows from
  7168 to 12 KB because our timers (snprintf for labels, the arbiter read, VOR maths) now run on
  it; 7168 was sized for `lv_timer_handler` alone. Change: `task_affinity = 0, task_stack = 12288`
  in the `ESP_LVGL_PORT_INIT_CONFIG()` instance in `hal_esp32.cpp` — a vendor file already on the
  README's modification list.
- **audio at 5 on CPU1.** The only thing on CPU1 that has a hard real-time edge: an I2S DMA
  underrun is an audible click, and a mic DMA overrun loses samples. It spends its life blocked in
  DMA, so its priority costs nobody anything. (The vendor creates its own `audio` task at 5
  **unpinned** on first `audioPlay`; we stop using `audioPlay` and own the codec handle directly.)
- **cam, sense, net at 3.** Periodic I/O with deadlines in the tens of ms (a servo tick, a frame
  dequeue, a status heartbeat). None is compute-heavy; equal priority means round-robin, which is
  fine because each blocks on a driver most of the time.
- **percept at 2.** Lowest real task. A 35 ms inference preempted by a servo write is correct; the
  reverse is not. It also cannot delay `net`'s OTA or `sense`'s I²C.
- **mon at 1, debug builds.** Reads counters, drains the telemetry ring with a non-blocking write.
  In release the liveness check survives as the task watchdog subscription below; the telemetry
  drain does not exist.
- **lwIP pinned to CPU1** (`CONFIG_LWIP_TCPIP_TASK_AFFINITY_CPU1`): today it floats at priority
  18, which can land a burst of packet processing on CPU0 above the renderer. esp_hosted's own
  tasks are high priority and short; if run-time stats show them on CPU0 under load, their
  affinity Kconfig is the next knob.

### 2.3 Cadences, and who owns what

| task | cadence | owns | publishes |
|---|---|---|---|
| ui_tick | 33 ms (LVGL refresh period; a faster tick draws nothing extra) | every LVGL object; the animation state; VOR integration | `UiIntent` (head target, mast/estop/arm requests from touch, which screen is up) |
| cam | per frame, 30 fps, blocked in `VIDIOC_DQBUF` | the V4L2 device, the PPA SRM client, the 320×180 and 160×90 output buffers | `FrameSeq` (atomic counter + timestamp); buffers are single-writer, readers tolerate tearing |
| percept | every frame for motion (or every 2nd — 15 Hz is enough for saliency); face detection **gated**: 2 Hz when nothing is near, 8–10 Hz while the ToF reads < 1.5 m or the hottest motion cell is central | prev-luma, cell grid, detector input, the model | `GazeIntent {nx, ny, kind, confidence, seq, stamp}` |
| sense | 100 Hz loop; IMU every tick, ToF every 5th, servo write every 2nd | the external I²C bus; the BMI270; the pan/tilt integrator and limits | `ImuSample`, `SenseState` (ToF mm + fresh, pan/tilt actual + moving flag) |
| net | `select()` with 20 ms timeout; status TX at 20 Hz | the socket(s), Wi-Fi state machine, OTA, CFG pull | `RobotState`, `LinkState`, `CmdQueue` (events: GAZE, THEME, VOL, …); OTA progress |
| audio | blocked in `i2s_write`/`i2s_read` | the codec handle, PCM rings, cue priority | `AudioState` (playing, envelope for the band, mic onset + direction) |

**Replace the vendor camera loop, do not reuse it.** `startCameraCapture(lv_obj_t*)` is built
around pointing a full-screen canvas at a 1280×720 mirrored copy made by a 1:1 PPA pass every
frame — 1.84 MB written to PSRAM 30 times a second (≈55 MB/s of the PSRAM's bandwidth) for a canvas
we must keep hidden, plus a lock acquisition per frame. Our `cam` task dequeues the same V4L2
buffers and runs **PPA SRM with `scale_x = scale_y = 0.25`** into a 320×180 RGB565 buffer (and
0.125 into 160×90 for motion), mirror_x as the vendor does, then requeues. Output writes drop from
1.84 MB to 144 KB per frame; CPU cost is the DQBUF/QBUF ioctls. PPA buffers in PSRAM must be
aligned to both cache lines (64 B L1, 128 B L2 — `ppa.h`) and sized to a multiple of them:
320×180×2 = 115,200 = 900 × 128 and 160×90×2 = 28,800 = 225 × 128, both fine with a 128-aligned
base. Whether PPA SRM can emit a YUV plane (luma for free) is **unverified** — check
`hal/ppa_types.h` for the output colour modes before relying on it; if not, luma from RGB565 at
160×90 is 14,400 operations and irrelevant.

### 2.4 Inter-task data: seqlocks and one queue, no mutexes, no heap

Every "state" above is a POD struct ≤ 64 bytes with a single writer, published through a seqlock
(write: `seq++ (odd)`, fields, `seq++ (even)`; read: retry while odd or changed) or, for scalars, a
`std::atomic`. No reader ever blocks a writer; no writer ever blocks the UI. The only queue is
`CmdQueue` (FreeRTOS queue, 16 × 64-byte fixed records) for *events* — a `GAZE x y`, a theme
select, a cue request — where losing an item would be wrong and ordering matters. The UI drains
it at the top of `ui_tick` with `xQueueReceive(…, 0)`, never blocking.

What this forbids, on purpose: any `std::vector`, `std::string`, `std::function` or `new` on a
per-tick path in any task. Those all `malloc`, and under 16 KB `malloc` is internal DRAM — the
scarce one.

### 2.5 Liveness, by construction

- **Subscribe the LVGL task to the task watchdog** (`esp_task_wdt_add` from inside `ui_tick`'s
  first run; `esp_task_wdt_reset` each tick). Today's WDT watches only the idle tasks (5 s), which
  catches a CPU hog but **not** a renderer deadlocked on the mutex — idle still runs, the face is
  frozen, nothing fires. With the subscription, a `ui_tick` that stops for 5 s panics the device
  into a coredump (§5) and a reboot, instead of sitting frozen until someone notices. That turns
  the longest class of freeze in this project's history into a self-reporting event.
- **`ui_tick` increments an atomic frame counter.** In debug builds `mon` checks it advances; the
  release path is the WDT above.

---

## 3. Module boundaries: who may include `lvgl.h`

```
firmware/tab5_idf/app_face/
  ui/          ui_face.cpp ui_band.cpp ui_theme.cpp ui_controls.cpp ui_hud.cpp(dbg)   ← ONLY these include lvgl.h
               ui_mailbox.h   (reads every published state; writes UiIntent)
  shared/      mailbox.h (seqlock + CmdQueue types), face_config.h (POD tables), due.h (wrap-safe time)
  percept/     cam_source.cpp  motion.cpp  attention.cpp  facedet.cpp(opt)           ← pure compute over buffers
  sense/       imu.cpp  tof.cpp  head.cpp                                             ← I²C only
  net/         wifi_sta.cpp  udp_cmd.cpp  ota_pull.cpp  cfg_pull.cpp                  ← sockets, flash
  audio/       cues.cpp  speaker.cpp  mic.cpp                                         ← I2S only
  cfg/         cfg_load.cpp  cfg_validate.cpp                                         ← files → POD
  dbg/         dbg_config.h(generated)  dbg_preview.cpp  dbg_telemetry.cpp  cost_gate.cpp
```

Rules, each enforceable by a grep in `AUDIT.md`:

1. **`#include <lvgl.h>` appears only under `ui/` and `dbg/`** (the preview canvas is LVGL, but it
   is *driven* from a UI timer; `dbg/` is absent from release builds anyway). Everything else
   compiles with `-DLV_CONF_SKIP`-free units and would fail to compile if it touched LVGL.
2. **`lvglLock` appears nowhere in `app_face/`.** The lock is held by the LVGL task around every
   timer; nothing else needs it. The vendor camera task — the one remaining external locker — is
   not started.
3. **`ui/` does no I/O**: no `i2c_master_*`, no sockets, no `fopen`, no `esp_ota_*`, no `printf`
   (§7). It reads mailboxes and writes `UiIntent`. A UI handler that needs a side effect posts an
   intent; `net`/`sense` act on it.
4. **`percept/`, `sense/`, `audio/`, `net/` never call into `ui/`.** The dependency arrow is
   one-way: everyone depends on `shared/`, `ui/` depends on everyone's published types.
5. **`cfg/` produces `FaceConfig` (POD, fixed capacity) and nothing else.** The UI builds its scene
   from `FaceConfig`; the desktop cost gate consumes the same struct. No LVGL in the loader means
   the loader is unit-testable on the host.

The CMake side: the vendor's `CMakeLists.txt` globs `app/**/*.cpp`, so subdirectories need no
build changes; `build.sh sync_sources` syncs directories already.

---

## 4. Memory plan

### 4.1 Internal DRAM — the binding constraint

What must be internal, and the budget:

| consumer | KB | why internal | control |
|---|---|---|---|
| task stacks (taskLVGL 12, audio 4, cam 4, sense 4, net 6, main 4, mon 3) | ~37 | FreeRTOS TCBs must be internal; stacks are internal unless `WithCaps` | ours; percept's 8 KB can go to PSRAM |
| LVGL objects, styles, draw-task descriptors (`LV_USE_CLIB_MALLOC` + <16 KB rule) | ~15–20 | allocator policy | ours: fixed object count, built once |
| `LV_DRAW_LAYER_SIMPLE_BUF_SIZE` = 24 KB | 0 (PSRAM) | over the 16 KB threshold, so `lv_malloc` → clib → PSRAM; listed because a *layer-inducing style* allocates a bounding-box buffer per draw on top of it (the rotated brow asked for 73 KB) — keep layers out of the scene regardless of where they land | sdkconfig |
| I2S DMA descriptors + buffers (both directions) | ~8–16 | DMA | driver-owned |
| esp_hosted SDIO mempool + Wi-Fi RX/TX buffers + lwIP pools | **60–100 (estimate)** | DMA / driver policy | measure; `SPIRAM_TRY_ALLOCATE_WIFI_LWIP` could move some to PSRAM at a throughput cost — test only if needed |
| esp-dl working memory | **unknown** | esp-dl prefers internal for tensors when it fits | **measure first** (§8) |
| mailboxes, queues, cue priority state | <2 | — | ours |
| 32 KB reserve (`SPIRAM_MALLOC_RESERVE_INTERNAL`) | 32 | allocator keeps it for DMA/internal-only callers | config |
| **floor to keep free at steady state** | **≥ 64** | fragmentation headroom; the Arduino firmware's radio asserted at 134 KB free with a 241 KB sprite taken | policy, shown on the panel |

Total controllable ≈ 100–120 KB against ~250–310 KB available, leaving ~130–190 KB for Wi-Fi,
esp-dl and the floor. **That is the squeeze**: if esp-dl's internal footprint exceeds ~40 KB after
Wi-Fi is up, something has to move. The candidates, in order of least pain:

1. Perception stack to PSRAM (−8 KB).
2. `CONFIG_LV_ATTRIBUTE_FAST_MEM_USE_IRAM=n`: LVGL's hot draw code is 64 KB of **IRAM**, which on
   the P4 is the same L2MEM pool as internal DRAM. With `SPIRAM_XIP_FROM_PSRAM` that code would run
   from PSRAM through the 256 KB L2 cache instead. Frees up to 64 KB; costs an unknown amount of
   draw speed — **measure FPS/CPU% before and after on the same scene**, one variable.
3. `SPIRAM_TRY_ALLOCATE_WIFI_LWIP=y` (Wi-Fi buffers in PSRAM; esp_hosted's own pool stays
   internal regardless).

### 4.2 PSRAM — plentiful, but cache-sensitive

| consumer | MB |
|---|---|
| app .text + .rodata executing from PSRAM (XIP) | 3.9 |
| LVGL draw buffers 2 × 1280×720×2, rotation buffer, DSI framebuffer | 7.4 |
| camera V4L2 buffers ×2 | 3.7 |
| esp-dl model + tensors | ~2–3 (the probe's 21.2 MB-free is after this) |
| perception small buffers (320×180 RGB565, 160×90 ×2 luma, RGB888 input) | 0.33 |
| earcon PCM, speech ring, mic ring | 0.33 |
| theme assets (future, one active theme) | ≤ 4 |
| **free** | **~10 MB** |

Risks that are about *access pattern*, not size:

- **Strided reads thrash the cache.** A loop that reads every 4th pixel of every 4th row of a
  1280×720 PSRAM buffer touches a fresh 128 B line every 16 pixels and streams 20 lines per output
  row; it is why today's software downscale costs milliseconds. The PPA scaling in §2.3 removes
  the pattern instead of tuning it.
- **DMA producers and cached consumers.** After the PPA writes a PSRAM buffer, the CPU's cached
  view can be stale; the PPA driver's alignment requirement exists because it performs cache
  maintenance (`esp_cache_msync`) on aligned buffers — **verify by reading
  `esp_driver_ppa/src/ppa_srm.c` in 5.5.5** before trusting it; if it does not, `percept` must
  call `esp_cache_msync(buf, size, ESP_CACHE_MSYNC_FLAG_DIR_M2C)` before reading. A wrong answer
  here presents as "detector sees the previous frame", which is subtle; put a frame-sequence
  watermark in the corner of the PPA output in debug builds so it is visible.
- **Anything DMA-capable needs `MALLOC_CAP_DMA`**; on the P4, PSRAM is DMA-capable
  (`MALLOC_CAP_DMA | MALLOC_CAP_SPIRAM` is what the vendor uses). I2S DMA descriptors are
  driver-allocated internal.
- **A `std::vector` of PCM is a hidden policy decision**: `Heard` (13.4 KB) is internal, `Mode`
  (65 KB) is PSRAM, decided by a size threshold nobody wrote down. Every buffer in §1 is allocated
  once with an explicit capability.

### 4.3 Steady-state allocation: zero

`AUDIT.md` invariant 6 already demands it; the plan makes it the default:

- `lv_label_set_text_static()` with our own fixed `char[]` buffers (one per label, in PSRAM) —
  today's `lv_label_set_text()` mallocs on every emote change, quote rotation and HUD update.
- `cueBegin()` renders PCM into preallocated PSRAM arrays; playback writes the array to I2S from
  `audio` — no copy. (The HAL's `audioPlay` copies; we bypass it.)
- Mailboxes are static. `CmdQueue` is created once.
- cJSON is used only at boot and in `cfg_pull` validation; the tree is freed before the UI starts
  or before `apply` is signalled.

Verification that costs nothing: the technician card and the debug HUD both show
`MALLOC_CAP_INTERNAL` free + largest block; a 30-minute soak with a flat line is the pass.

---

## 5. Flash and partition plan

### 5.1 Where the 3.94 MB goes today (from the map)

| item | bytes | keep? |
|---|---|---|
| `canon_in_d.mp3` (vendor music test) | **956,213** | **drop** — `EMBED_TXTFILES` in `platforms/tab5/main/CMakeLists.txt` + the music-test code in `hal_audio.cpp`. `libhelix`/`esp-audio-player` (~60 KB) fall away too **only if** the `startup_sfx`/`shutdown_sfx` MP3 references (42 KB, already superseded by our `Boot` cue) go with them |
| esp-dl (`libespressif__esp-dl.a`) | 725,741 | §8 |
| human_face_detect incl. model in rodata | 195,616 | §8; 191 KB of it is the `.espdl`, movable to its partition |
| LVGL | 418,166 | yes |
| `esp_app_desc.c.obj` .rodata | 229,645 | **unexplained** — an app descriptor is 256 B; 230 KB attributed to it is probably a map-attribution artefact for some embedded blob. Identify (`idf.py size-files`, then the map) before counting on it. |
| libstdc++ | 140,958 | yes (fmt/mooncake use it) |
| lwIP | 134,950 | yes |
| esp_hosted | 99,038 | yes |
| mooncake_log + fmt | 71,302 | yes for now; replaceable by ESP_LOG later |
| esp_new_jpeg | 67,548 | drop if nothing decodes JPEG (the probe's sw decode uses esp-dl's) |
| `lv_font_montserrat_40` | 71,437 | referenced by something — find and decide; unused sizes are already linker-dropped |
| everything else | ~1.9 MB | the IDF + BSP + drivers; not a lever |

Forecast growth: OTA client + littlefs + cJSON ≈ +150 KB; touch pages ≈ +50 KB; Opus ≈ +200 KB;
a wake-word model later ≈ +300–800 KB. With the MP3 gone, the image with esp-dl is ~3.0 MB and
the fully built-out forecast is ~4.3 MB. A 5 MB slot would hold it with ~15% to spare; 6 MB holds
it without the question ever coming back.

### 5.2 The proposed table

The partition table cannot be changed by OTA. Whatever layout the first IDF USB flash writes is
the one every subsequent OTA lives inside, which is the argument for deciding it generously now.
NVS contents are explicitly not sacred (a few re-derivable integers), so the bootloader's slot
can grow back to a size that does not need its log stripped.

```
# firmware/tab5_idf/partitions.csv — proposed
# CONFIG_PARTITION_TABLE_OFFSET=0x10000 (bootloader gets 0x2000..0x10000 = 56 KB; 5.5's bootloader
# is 0x6120 = 24.3 KB at INFO log level, so the partition-table dump comes back with room to spare)
# Name,            Type, SubType,  Offset,    Size
nvs,               data, nvs,      0x11000,   0x6000     # 24 KB: selections, wifi creds, tilt limits, servo signs
otadata,           data, ota,      0x17000,   0x2000
app0,              app,  ota_0,    0x20000,   0x600000   # 6 MB
app1,              app,  ota_1,    0x620000,  0x600000   # 6 MB
human_face_det,    data, spiffs,   0xC20000,  0x80000    # 512 KB; label is what esp-dl's loader opens — only if esp-dl kept
coredump,          data, coredump, 0xCA0000,  0x10000    # 64 KB; the only post-mortem channel on a head with no cable
cfg,               data, littlefs, 0xCB0000,  0x50000    # 320 KB; authored per-robot config (power-fail safe)
assets,            data, littlefs, 0xD00000,  0x300000   # 3 MB; theme bitmaps, band strips, audio clips, shipped examples
                                                          # ends exactly at 0x1000000 (16 MB)
```

Consequences and pairings:

- **One-time migration by USB.** Moving the table offset makes the Arduino image's layout and
  this one incompatible; the switch is a full USB flash on the bench, which is already the plan
  (the Arduino firmware stays shipping until Phase 5). After that, OTA within the layout.
- **Bootloader log back to INFO** (`CONFIG_BOOTLOADER_LOG_LEVEL=3`): the partition dump that was
  traded away to protect NVS comes back.
- **Rollback and mark-valid land together** (`CONFIG_BOOTLOADER_APP_ROLLBACK_ENABLE=y` +
  `esp_ota_mark_app_valid_cancel_rollback()` called **only after** the scene has rendered a
  frame, the WDT subscription is live and Wi-Fi has an IP, or after 60 s if Wi-Fi never comes —
  the head must not be unflashable because the router is off). `tab5-idf-ota.md` already
  specifies the bench test: OTA a deliberately broken image, confirm the revert.
- **Coredump to flash** (`CONFIG_ESP_COREDUMP_ENABLE_TO_FLASH=y`, ELF format): a panic from the
  WDT subscription in §2.5 writes a core that `idf.py coredump-info` reads over USB on the bench,
  and that `net` can serve on a `COREDUMP` command on the robot. Without it a robot-side freeze
  leaves no evidence; with it, every freeze leaves a backtrace.
- **Model in rodata, not the partition, if esp-dl is kept** — one OTA updates both, which matters
  for a head on the e-stop branch — and the `human_face_det` partition becomes a 512 KB reserve
  you can fold into `assets`. The workplan already made this argument; 6 MB slots remove the
  counter-argument.
- **littlefs** (joltwallet component, pinned) rather than SPIFFS for `cfg`: a config write is
  `write temp → rename`, and littlefs makes the rename atomic across power loss. SPIFFS does not
  promise that, and the head loses power whenever the contactor opens.
- Labels ≤ 16 chars, app offsets 0x10000-aligned, data partitions 0x1000-aligned — all satisfied
  above.

### 5.3 OTA implications

- The image grows past 5 MB only in the forecast's last steps; 6 MB slots mean the slot size
  never becomes an emergency.
- `assets` is updated by the same pull mechanism as firmware (`ASSETS <url>` → write to a staging
  file → verify a manifest hash → rename), **never** mid-render: the UI is told to drop to the
  compiled-in default theme (which needs no assets) before the swap and re-apply after.
- `cfg` is small and updated by `CFG <url>` (§6); it is also what a `FACTORY` command erases.

---

## 6. Configuration and choreography

### 6.1 The three tiers, made structural

`face-config.md` settles the tiers; this is how they become impossible to get wrong rather than
merely checked:

| tier | mechanism |
|---|---|
| **Reserved** — DEGRADED amber, E_STOP red, the fault band motion, the attending/reporting policy per state | **No key exists for them in the schema.** The loader's state table merges only `colour` for states whose compiled `reserved == false`; a file that names `E_STOP.colour` is not "rejected", it is **unparsed** — the field is never read. The same for `locked`. A later schema version cannot accidentally expose them because the firmware's `applyState()` reads the compiled table for reserved states unconditionally. |
| **Bounded** — emote parameters, non-fault state colours, band motion per activity, earcon rhythm/gain, theme palettes and accessory selection | Parsed into a staging `FaceConfig`, then **clamped by `cfg_validate()` against ranges that are compile-time constants** next to the compiled defaults (eye body ≤ panel − band − margins, accessory count ≤ `kMaxAcc` = 24, durations ≥ a floor, colour never sprung). Every clamp is recorded as a verdict string. The desktop cost gate consumes the same struct and reports dirty area per frame. |
| **Free** — quotes, theme names, which theme is active, mute/volume | Length-limited strings and enums; "not crashing" is the only check. |

### 6.2 Where it lives and how it loads

```
compiled-in C tables            the permanent fallback; the face is fully functional with nothing else
/assets/examples/*.json         shipped with the firmware's assets image: the curated, cost-measured set
/cfg/active/*.json              the robot's own authored set (per-robot, never committed)
NVS "face": theme, mute, vol    selections — a few bytes, survive config resets
```

Boot: `main` (before the UI exists) → `cfg_load()` reads `/cfg/active/*`, falls back per file to
`/assets/examples/*`, falls back per table to the compiled defaults → validates → builds one
immutable `FaceConfig` in PSRAM → the UI builds its scene from it. Load time is tens of ms; the
verdict list (what fell back and why) is published in `STATUS` and shown on the technician card
("config: default emotes (emotes.json: brow_range out of range)"). The author sees the rule they
broke on the dashboard, which is the open question in `face-config.md` answered.

Files: `states.json` (non-reserved colours only), `emotes.json`, `band.json`, `earcons.json`,
`themes/<name>.json` (palette + accessory selection from a compiled vocabulary — arbitrary
authored geometry stays out until the cost gate measures accessory cost), `quotes.txt`,
`schema_version`. JSON via cJSON (already a dependency of the IDF tree). Sizes: a few KB each.

### 6.3 How it updates

- **Selection** (`THEME <name>`, `MUTE`, `VOL n`) is a UDP command from the dashboard, applied live
  by the UI at its next tick and persisted to NVS by `main`. Live-safe because selection changes
  only colours, opacities and hidden flags on objects that already exist.
- **Authored data** (`CFG <url>`): `net` pulls a tarball or a single JSON to `/cfg/staging/`,
  runs the **same** `cfg_validate()` the boot path runs, and only on a clean verdict renames into
  `/cfg/active/`. Then: bounded parameters that change colour/timing apply live; anything that
  changes object count or geometry (a new accessory set, an eye size) applies on the next boot —
  the command reply says which. This keeps the "nothing allocates after startup" rule intact: a
  config change never rebuilds the scene while it is running.
- **The dashboard is the editor, the head is the owner** (the workplan's 1.3b principle). The
  repo holds defaults and examples under `firmware/tab5_idf/config/`, packed into the assets image
  by `build.sh` (`littlefs_create_partition_image`), with each example's cost-gate figure checked
  in beside it.
- **Mute vs the Fault cue** (1.3b's forced decision): `MUTE` is a bounded parameter that cannot
  reach `Fault`'s gain — the schema has a `mute_exempt` list that is compiled, not authored, and
  contains `Fault`. A muted robot shows a mute glyph on the band so a muted robot is visibly muted.

### 6.4 Choreography

`notes/workplan-choreography.md` is referenced by three notes but **does not exist in the tree**;
the StamPLC demo's `trigger_relay` (a persisted if/then/else rule engine with an on-device editor
and serialization) is the reference shape. For the face, a routine is a timeline of (time, state,
emote, gaze, cue) keyframes — the same POD-table-from-JSON pattern as above, interpreted by a UI
timer that reads only published signals and the clock. Resource-wise it is free; what it must not
do is take a lease on anything that moves (`docs/control-authority.md`): a face routine may
request gaze and expression, and may *ask* the StamPLC for a mast move through the same intent
path as a touch, with the same heartbeat and the same preemption-aborts rule. Design the routine
format when the workplan exists; reserve the slot (`ui_choreo` timer, `/cfg/routines/`) now.

---

## 7. Debug and instrumentation layer

### 7.1 The rule

Every instrument — camera preview, detection marker, status lines, cost gate, serial telemetry,
the heap HUD — is compiled only when its flag is set, and a release image contains none of it.
"Absent" is verified, not assumed.

### 7.2 Structure that cannot rot

- `build.sh` generates `dbg/dbg_config.h` (as it already does for `face_probe_config.h`) from
  `OPENDUBS_DEBUG=0|1` plus per-instrument toggles (`DBG_PREVIEW`, `DBG_TELEMETRY`, `DBG_HUD`,
  `DBG_PROBE_FACEDET`). Release = all zero.
- Each instrument is one `.cpp` under `dbg/` whose entire body is inside `#if DBG_X`, with the
  `#else` branch defining the public functions as empty — exactly the `face_probe.cpp` pattern,
  which measured byte-identical. The header exposes the same API either way, so call sites in
  `ui/` compile unchanged and the linker drops the no-ops.
- **The release check is a build step, not a habit**: `build.sh release` builds twice — once with
  the `dbg/` sources present and `OPENDUBS_DEBUG=0`, once with `dbg/*.cpp` removed from the sync —
  and fails if `m5stack_tab5.bin` differs. It also fails if `strings` finds any `dbg:` marker in
  the release binary (every instrument's status text starts with `dbg:`). Two independent checks,
  both self-verifying, both costing a minute.
- `AUDIT.md` gains: `grep -rl "dbg_" firmware/tab5_idf/app_face/ui/` must list only call sites of
  the no-op-able API, never a `#include <dbg_*.h>` in a non-UI module.

### 7.3 Serial is a suspect, so it is quarantined

A console write blocks the writing task when no host drains USB-serial-JTAG, so a `printf` in any
task that matters is itself a plausible cause of the fault it reports. Therefore:

- **No task except `mon` ever writes to the console**, in any build. Instruments append fixed-size
  records to a PSRAM ring; `mon` (debug builds, priority 1, CPU1) drains it with
  `usb_serial_jtag_write_bytes(…, 0 ticks)` — non-blocking, lossy, and lossy is correct: a
  dropped telemetry line is cheaper than a stalled servo tick.
- Release builds set `CONFIG_LOG_DEFAULT_LEVEL_ERROR` and `CONFIG_LV_USE_LOG=n`; debug builds set
  INFO. The IDF's own `ESP_LOGx` from drivers is the residual risk and is why the level drops.
- **On-panel first.** The debug HUD (`ui_hud`, 4 Hz, from the UI timer so it is locked by
  construction) shows: FPS and CPU% (LVGL's own monitor), `MALLOC_CAP_INTERNAL` free/largest,
  perception status, link age, config verdict count. The technician card in release shows the
  subset that is product, not instrument (internal heap, uptime, link age, IP, config verdict).
- Everything on the panel is also in the `STATUS` UDP reply, so a bench with nobody at it can
  still be read from a laptop — the brief's "a bench that reports only on the panel is not a
  bench", satisfied without a serial port.

### 7.4 The debug preview, done so it cannot starve the UI

The preview is a 320×180 canvas pointed at `cam`'s PPA output buffer (no copy, no LVGL scaling,
no layer). `ui_tick` polls `FrameSeq` and calls `lv_obj_invalidate(preview)` at most every 100 ms
— 57,600 px per invalidation, 0.58 Mpx/s, ~4% of the miscalibrated budget, and bounded regardless
of camera frame rate. The detection reticle is a child of the canvas moved from `GazeIntent`. No
other task touches either object. This is the 20 Hz, 1.5 Mpx/s preview that moved `main` to
eBlocked, reduced to a number and moved to the one task allowed to draw.

---

## 8. esp-dl: keep or drop

### 8.1 What it costs and buys, stated plainly

| cost | with esp-dl | without |
|---|---|---|
| flash | +922 KB (model in rodata); image 3.94 MB → ~3.0 MB after the MP3 drop | 2.92 MB → ~2.0 MB |
| internal DRAM | **unmeasured** — the only cost that could decide this | — |
| CPU1 | 12–30% at 4–10 Hz gated; 0 when idle-gated at 2 Hz | 0 |
| build pins | esp-dl 3.1.3 + human_face_detect 0.2.3 (both forced) | none |
| IDF | 5.5.5 — see below | 5.4.2 nominally available |
| reach | faces ≥ ~128 camera px → ≈1.1 m full-frame, ≈2.2 m on a 2× centre crop or on a crop at the motion target | — |

What it buys that motion saliency, the ToF and audio onset cannot: **discriminating a person
facing the robot from a body passing it**. Motion says *something moved there*; the ToF says
*something is 0.9 m away*; audio says *a sound came from the left*. Only the detector says *that is
a face, pointed at me* — which is the trigger for mutual gaze, the behaviour `face-design.md`
ranks first, and the only honest basis for "someone is addressing me". Within ~1–2 m is exactly
where a person addressing a robot stands.

### 8.2 The IDF-version argument is weaker than it looks

The brief frames dropping esp-dl as restoring a 5.4.2 fallback. The failure that forced 5.5.5 was
not esp-dl's: it was IDF issue #15996 — 5.4.x's allocator advertises TCM as internal, byte-capable
memory, and when internal DRAM is tight a TCB or stack lands there and FreeRTOS rejects it.
esp-dl merely consumed enough internal DRAM to trigger it. **This plan predicts internal DRAM will
be tight with or without esp-dl** (Wi-Fi + audio + perception + a second LVGL screen), so 5.4.2 is
a fallback to a release with a known defect that bites under exactly the conditions we are
heading into. It should not be counted as insurance. The rev <3 vs ≥3 silicon split is a
5.5-era Kconfig, but a v3 Tab5 would need its own validated build on *any* IDF; keeping 5.4.2
around does not help there either.

### 8.3 Recommendation

**Keep esp-dl, demoted and gated, with a kill criterion — and drop `canon_in_d.mp3` so it costs
net nothing in flash.** Conditions, each a measurement, all in Stage 7 below:

1. Internal DRAM: `MALLOC_CAP_INTERNAL` free after Wi-Fi is up, after `new HumanFaceDetect()`, and
   after 100 inferences, must leave ≥ 64 KB free with largest block ≥ 32 KB. If it does not, and
   the three levers in §4.1 cannot recover it, **drop**.
2. Live detection: with the camera source in §2.3 and a centre-crop or motion-ROI input, a person
   at 1 m facing the panel is detected in ≥ 80% of attempts over 30 s (counted by the firmware,
   reported in `STATUS`, watched once by a human for the reticle sign). The live path has **never
   detected a face** so far; if it still cannot after the crop fix, there is nothing to keep.
3. Lock pressure stays zero and `ui_tick` jitter is unchanged with the detector at 10 Hz — a
   30-minute soak with the WDT subscription armed.

If kept: model in rodata, 6 MB slots, the `human_face_det` partition retained as reserve.
If dropped: fold the partition into `assets`, remove the two pins, stay on 5.5.5.

---

## 9. Staged implementation, each stage independently verifiable

Legend — **S** self-verifying (firmware or build script states the verdict); **N** needs a network
but no human; **H** needs a human at the panel; **R** needs the robot.

| # | stage | what changes | confirming observation | type |
|---|---|---|---|---|
| 0 | **Baseline measurement build** | HUD adds `MALLOC_CAP_INTERNAL` free/largest; LVGL perf monitor on; `STATUS` not yet available so values repeat on the HUD | Panel shows FPS, CPU%, internal heap on the full face scene; write the numbers into this note's §1 table. One variable: nothing else changes. | H (one glance) |
| 1 | **Animation moves into the LVGL task; lock calls removed; LVGL task pinned CPU0 + 12 KB stack; WDT subscription; task map of §2.2 for the tasks that exist (main, LVGL)** | `ui_tick` lv_timer; `onRunning` emptied; `hal_esp32.cpp` port config; `esp_task_wdt_add` | `grep -rc lvglLock app_face/ == 0`; `grep -rl lvgl.h` → `ui/` only; 30-minute soak with no WDT panic and a flat internal-heap line on the HUD. The face animating is the human check; the WDT not firing is the self check. | S + H |
| 2 | **Drop the MP3 and the vendor music test; `cues` to preallocated PSRAM; vendor `audioPlay` bypassed** | CMakeLists `EMBED_TXTFILES`, `hal_audio.cpp`, `cues.cpp` | Image shrinks by ~0.95 MB (build log); internal heap on the HUD rises by ≥13 KB; cues still disabled by flag so no ears needed. | S |
| 3 | **Partition table + bootloader + rollback + coredump** (§5.2) | `partitions.csv`, `sdkconfig.defaults` | Bootloader prints the table (log back at INFO); `esp_ota_get_running_partition()` reports `app0` at 0x20000; a forced `abort()` in a debug command leaves a coredump `idf.py coredump-info` can read; one-time USB flash. | S (bench USB) |
| 4 | **Wi-Fi STA + UDP :4242 + `STATUS`/`REBOOT`/`STATE`** | `net/` | `printf 'STATUS' \| nc -u` from a laptop returns heap, uptime, link age, config verdicts; `STATE E_STOP` recolours the band (reticle-free, visible from across the room, or confirmed in the next `STATUS`). Mark-valid pairing test: a build that panics at boot reverts to the previous slot. | N (+ one H for the colour) |
| 5 | **OTA pull with stall deadline** | `net/ota_pull.cpp` | `ota-from-thor.sh` adapted (build cmd + bin path) completes end to end on the bench network; killing the HTTP server mid-transfer produces `OTA: aborted` within 10 s and the head stays up (watch `STATUS`). | N |
| 6 | **Config loader + littlefs + cost gate on the shared struct** | `cfg/`, `build.sh` packs examples | Boot with no `/cfg` → `STATUS` lists every table as `default`; push a file with `E_STOP.colour` → field ignored, verdict says so; push `brow_range: 9` → clamped, verdict names the rule; desktop `./build.sh cost` reads the same JSON. | S |
| 7 | **Camera source via PPA + motion saliency + arbiter; esp-dl gate** | `percept/`, `cam` | Self-stimulus: debug build injects a synthetic moving blob into the luma stage and asserts the hottest cell tracks it (PASS/FAIL in `STATUS`). Live: preview + reticle for the sign (human, once). esp-dl criteria of §8.3 measured and recorded; keep/drop decided here. | S + H (once) |
| 8 | **`sense`: IMU self-motion gating + VOR; ToF; 8Servo** | `sense/` | IMU: shake the bench Tab5 — eyes counter-rotate and motion saliency is suppressed while `ImuSample` exceeds threshold (`STATUS` shows the gate flag flip; the counter-rotation needs eyes). ToF and servos are not on the bench: **stub register entries**, verified at R. | H / R |
| 9 | **Touch controls + intent path** | `ui_controls.cpp`, `net` sends | Bench: `STATUS` shows the mast request toggling at 20 Hz while a button is held and dropping to STOP on release/touch-loss; hold-to-confirm ring visible. Robot: the StamPLC acts, pinch guard still holds. | S (bench), R |
| 10 | **Audio: earcons re-enabled by selection; mic onset + direction; speech ring (no source yet)** | `audio/` | Earcon audibility and loudness are ears-only (batch into a review); mic onset is self-testable by playing a cue and asserting the mic sees it — which also validates the self-deafening window. | H + S |
| 11 | **Release-build verification** | `build.sh release` | Byte-identical with and without `dbg/`; no `dbg:` strings; `CONFIG_LOG_DEFAULT_LEVEL_ERROR` in the built sdkconfig. | S |

Stages 0–3 are the foundation and can be done in one sitting with the bench Tab5 on USB. Stages
4–7 need the bench network only. Human presence is needed at 0 (one glance), 1 (one glance), 7
(reticle sign, once), 8 (VOR by eye), 10 (ears) — five short review points, batchable, matching
the workplan's "schedule the aesthetic reviews" proposal.

---

## 10. Open questions and risks, with what settles each

| # | question / risk | what would settle it |
|---|---|---|
| 1 | **How much CPU0 does the face scene actually take?** Everything in §1 is sized against a budget known to be miscalibrated. | Stage 0: LVGL perf monitor CPU% and FPS, idle and at the ALERT/thinking peak, written down. Re-anchor the desktop cost gate to it. |
| 2 | **esp-dl's internal DRAM footprint.** The keep/drop decision rests on it and it has never been measured. | Stage 7: `MALLOC_CAP_INTERNAL` at the three points in §8.3. |
| 3 | **esp_hosted + lwIP internal footprint**, and whether `SPIRAM_TRY_ALLOCATE_WIFI_LWIP` costs throughput we care about. | Stage 4: heap before/after `esp_wifi_start()`; OTA throughput with and without the flag. |
| 4 | **Does `LV_ATTRIBUTE_FAST_MEM_USE_IRAM=n` cost frame rate?** It is the biggest single internal-DRAM lever (64 KB). | A/B on the Stage 0 scene, one variable, perf monitor numbers. |
| 5 | **Does the PPA driver msync PSRAM buffers itself?** A wrong assumption shows as the detector seeing stale frames. | Read `esp_driver_ppa/src/ppa_srm.c` (5.5.5); the debug frame-sequence watermark on the preview. |
| 6 | **Can PPA SRM emit a luma/YUV plane?** Saves a trivial conversion; not load-bearing. | `hal/ppa_types.h` colour-mode enums. |
| 7 | **Do flash writes during OTA perturb frame timing with XIP-from-PSRAM?** Expected not to. | `ui_tick` jitter histogram (debug ring) during a Stage 5 OTA. |
| 8 | **What is the 230 KB attributed to `esp_app_desc.c.obj`?** | `idf.py size-files` cross-checked against the map's object list. |
| 9 | **Which system tasks land on CPU0 under Wi-Fi load**, and do they matter? | Run-time stats with `CONFIG_FREERTOS_VTASKLIST_INCLUDE_COREID=y` (already set) during a 1 Mbit/s UDP flood. |
| 10 | **Does the vendor `hal_camera.cpp` yield or not?** The brief and the source disagree. | Moot once `cam` replaces it; until then, read the file in the tree being built. |
| 11 | **Live face detection has never worked.** The centre-crop/motion-ROI hypothesis is arithmetic, not a result. | Stage 7's 80%-at-1 m criterion. |
| 12 | **Does the StamPLC accept a re-arm request from the head** and apply its own preconditions, or does only the dashboard's guarded path exist? | Read `rc_serial_probe`'s command parser for `ESTOP`/clear handling before building the hold-to-confirm control; if only the dashboard path exists, the head sends to the dashboard API (with the authentication fix landing there first). |
| 13 | **littlefs component pin** — a third-party component in an ecosystem where caret ranges have broken three times. | Pin an exact version in `idf_component.yml`; the Stage 6 power-cut test (pull power mid-write, reboot, config intact or cleanly fallen back). |
| 14 | **A v3-silicon Tab5.** Any replacement board may need a separate `sdkconfig` variant. | `esptool chip_id` on arrival; keep `CONFIG_ESP32P4_SELECTS_REV_LESS_V3` documented beside the flash procedure. |
| 15 | **Theme bitmaps as assets** reopen the "static over animating is not free" rule for anything under the band or eyes. | The desktop cost gate run on each shipped theme, figure checked in beside it (`face-config.md`). |

---

## Appendix A — the one-line invariants, for `AUDIT.md`

```sh
grep -rc  lvglLock              firmware/tab5_idf/app_face/            # every file: 0
grep -rl  '<lvgl.h>'            firmware/tab5_idf/app_face/            # only ui/ and dbg/
grep -rnE 'i2c_master_|socket\(|fopen|esp_ota_|printf' firmware/tab5_idf/app_face/ui/   # none
grep -rnE 'std::(vector|string|function)|new ' firmware/tab5_idf/app_face/{ui,percept,sense,net,audio}/  # startup builders only
grep -rnE 'malloc\(|calloc\('   firmware/tab5_idf/app_face/            # every hit names a MALLOC_CAP_
grep -rn  'xTaskCreate('        firmware/tab5_idf/app_face/            # none: every task is PinnedToCore
grep -rn  'lv_label_set_text('  firmware/tab5_idf/app_face/            # none: _static only
```

## Appendix B — vendor files this plan modifies (extends the README table)

| file | change | why |
|---|---|---|
| `platforms/tab5/main/hal/hal_esp32.cpp` | `task_affinity = 0, task_stack = 12288` in the port config | §2.2 |
| `platforms/tab5/main/CMakeLists.txt` | remove `canon_in_d.mp3` from `EMBED_TXTFILES` | §5.1 |
| `platforms/tab5/main/hal/components/hal_audio.cpp` | remove the music test (`canon_in_d` reference) | §5.1 |
| `platforms/tab5/main/hal/components/hal_camera.cpp` | **not modified** — `startCameraCapture` is simply not called | §2.3 |
| `platforms/tab5/sdkconfig` (via `sdkconfig.defaults`) | table offset 0x10000, bootloader INFO, rollback, coredump-to-flash, lwIP affinity CPU1, log level per build type | §5, §7 |
