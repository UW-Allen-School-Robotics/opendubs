# The Tab5 platform — what the hardware is, and what we have been doing wrong

**Read this before any work on the Tab5 face or its rendering.** It exists because an enormous
amount of time has gone into extracting performance from this device while the capabilities that
make the question moot sat unused, and because a decision recorded long ago — "LVGL is a dead end"
— was wrong and kept us on the slow path.

---

## 0. The standing error, stated plainly

**We have been hand-optimising a software rasteriser on a chip with a dedicated 2D graphics
engine.**

`face.cpp` renders through M5GFX/LovyanGFX: 14 `drawEllipse` calls, `fillRoundRect`, `fillEllipse`,
all on the CPU, then `pushSprite` to copy pixels out. Two 324×448 eyes at 27.6 fps is about
16 Mpx/s — on a SoC whose own factory demo plays live 1280×720 camera video.

The ESP32-P4 has a **Pixel Processing Accelerator (PPA)** and **2D-DMA** that fill, blend, scale,
rotate and **mirror** without the CPU, with DMA reading **PSRAM directly, zero-copy**; a
**MIPI-DSI** controller that scans a PSRAM framebuffer out in hardware; and a **hardware JPEG
codec** that decodes Full HD at 48 fps. We use none of it.

Everything that felt like a hard limit was a consequence of that choice:

| What it looked like | What it actually was |
|---|---|
| "The eye must shrink to fit internal SRAM for speed" | A software blit bottleneck. Shrinking cost eye size and bought 21%. |
| "Mirroring the second eye is 22% *slower*" | LovyanGFX's **software** affine sampler was benchmarked. The P4 mirrors **in hardware, free**. |
| "Gaze can only travel ±11 px" | The eye body is 88% of its sprite and the sprite is pushed at a fixed spot. With a compositor the pupil is its own layer, positioned anywhere — the limit does not exist. |
| "Two eyes cost half the frame rate" | Both eyes are re-rasterised on the CPU each frame. |

**Rule: do not micro-optimise this renderer again.** If the face is too slow or too constrained,
the answer is the architecture below, not another benchmark. See [[m5stack-bench-rote]] for the
companion rule about not re-deriving the flashing and telemetry procedure.

---

## 1. LVGL is not a dead end — it is the official stack

This was recorded as a dead end some time ago. That was wrong, and it cost us the whole bespoke
renderer.

**M5Stack's own Tab5 factory firmware** (`m5stack/M5Tab5-UserDemo`) — the demo that plays camera
output — is an **LVGL application**. `lv_conf.h` sits at its repository root, and `repos.json`
pins:

| Dependency | Version | What it is |
|---|---|---|
| `lvgl/lvgl` | **v9.2.2** | the UI toolkit |
| `espressif/esp_lvgl_port` | — | Espressif's own LVGL integration for ESP-IDF |
| `Forairaaaaa/smooth_ui_toolkit` | v2.0.0 | animation and easing |
| `Forairaaaaa/mooncake` | v2.1.0 | app framework |

Built with **ESP-IDF v5.4.2** (`cd platforms/tab5 && idf.py build`). LVGL also has a **PPA draw
unit**, so work expressed in LVGL can dispatch to the hardware accelerator instead of the software
renderer.

**And it builds for the desktop.** The same repo has an SDL target
(`sudo apt install libsdl2-dev`, `./desktop/app_desktop_build`). The face could be designed and
iterated **on a laptop at full frame rate**, which is worth more than any on-device optimisation:
every eye-geometry question in this project so far has been answered by flashing a board and asking
whoever happened to be standing in front of it.

---

## 2. Full hardware inventory

From the [official Tab5 documentation](https://docs.m5stack.com/en/core/Tab5).

| | |
|---|---|
| **SoC** | ESP32-P4NRW32, RISC-V dual-core @ 360 MHz + LP core @ 40 MHz |
| **Radio** | **separate ESP32-C6-MINI-1U** — Wi-Fi 6 (2.4 GHz), **Thread, Zigbee**; 3D antenna + 2× MMCX external |
| **PSRAM / Flash** | **32 MB octal** / 16 MB |
| **Display** | 5" IPS **1280×720**, **MIPI-DSI**, ST7123/ST7121 integrated display-touch (or ILI9881C + GT911) |
| **Camera** | SC2356 2 MP (1600×1200), **MIPI-CSI** |
| **Audio** | **ES8388** codec, **ES7210 AEC front-end**, dual mics, **1 W 8 Ω NS4150B** amp, 3.5 mm jack |
| **Accelerators** | **PPA**, **2D-DMA**, **hardware JPEG encode/decode** (FHD @ 48 fps decode) |
| **Power** | IP2326 charger, **NP-F550 removable 7.4 V 2000 mAh**, ~6 h, **INA226** bus current/voltage monitor |
| **Sensors** | **BMI270** 6-axis IMU (interrupt wake), **RX8130CE** RTC (timed interrupt wake) |
| **Ports** | **USB-A host**, USB-C OTG, **RS485** (SIT3088, switchable 120 Ω, 6–24 V), microSD, HY2.0-4P, **M5-Bus**, GPIO_EXT |
| **Expansion** | stamp pads for Cat.M / NB-IoT / LoRaWAN |
| **Mounting** | 1/4"-20 tripod nut |
| **I/O expanders** | PI4IOE5V6408 at **0x43** and **0x44** |

### What we are not using at all

Worth knowing before building anything new: **camera** (MIPI-CSI), **hardware JPEG**, **PPA /
2D-DMA**, **USB-A host**, **RS485** (the robot already speaks RS485 to the battery BMS), **microSD**,
**BMI270 IMU** beyond offsets, **RX8130CE RTC** including its timed wake, **INA226** power
monitoring, the **3.5 mm jack**, the removable battery, and the C6's **Thread/Zigbee** radios.

---

## 3. Audio — what is settled

Measured on the bench and in the code, not assumed:

- **The speaker is not the limitation.** A claim that it "carries only 2–4 kHz" was a by-ear
  judgement of `M5.Speaker.tone()` in a noisy room, and is withdrawn. ES8388 + NS4150B into 1 W
  8 Ω, documented by M5 for *"Hi-Fi recording/playback and accurate voice recognition"*, plus a
  3.5 mm jack if an external speaker is ever wanted.
- **The ES7210 IS configured on this board.** `_microphone_enabled_cb_tab5()` in `M5Unified.inl`
  is a complete ESP32-P4-guarded init at I²C 0x40. Do not conclude otherwise from the S3-only
  `es7210_write_reg` helper — the P4 path writes the same chip through `In_I2C` directly.
- **Two of four mic channels are enabled** (`MIC12_PDN=0x00`, `MIC34_PDN=0xFF`). Channels 3/4 are
  the **AEC reference** inputs and are off, which is why the robot cannot cancel its own output in
  hardware.
- **Direction works at the default gain.** Left/right/centre from the inter-channel level ratio,
  confirmed interactively. **Gain is a dead end** — stepping `0x43`/`0x44` across `0x05`–`0x1E`
  changed little. **Writing `0x00` there silences the microphone**; it is the bottom of the range,
  not a default.
- **Speaker and microphone hold I²S simultaneously**, so the robot can listen while it speaks.
- **Levels are small**: a quiet room reads ~15–20 RMS against speech peaks of a few hundred. A
  quiet room reading low is *not* evidence of a broken microphone — that mistake has been made.
- **Speaker bring-up is nested inside the Wi-Fi-up branch of `commsTask`**, alongside
  `ArduinoOTA.begin()`. On a bench Tab5 with no network, `M5.Speaker.begin()` is **never called**
  and no sound is possible. On the robot there is a link, so this is a bench-only symptom —
  deliberately left alone rather than refactored.
- **Bring-up is fiddly and already solved**: full ES8388 reset, `begin()`, then **manually
  re-enable the amplifier** (PI4IO1 `0x43`, reg `0x05`, bit 1), because M5Unified's enable callback
  does not run in the state a warm boot leaves things in — a codec reporting ready into a disabled
  amp. Do not rewrite this sequence.
- **`M5.Speaker` has 8 virtual channels** with independent volume, `isPlaying(ch)` and `stop(ch)`;
  `tone()` accepts **your own wavetable**; `playRaw()` takes PCM; `playWav()` reads a flash array.
  A cue system is channel assignment, not a synth.
- **3.5 MB SPIFFS is already partitioned and unused** (`default_16MB.csv`) — roughly 3 minutes of
  16-bit 11 kHz mono, available for audio assets without touching the partition table.

---

## 4. Can it render HTML? Yes — and I previously said no

Three genuinely different answers, and the one I rejected long ago was the wrong one to reject.

**(a) Remote rendering, streamed as JPEG — the strong option.** A host renders the page in headless
Chromium and streams only the **changed regions** as JPEG tiles over a WebSocket; the device
decodes and blits them, and sends touch events back. This is a working pattern on ESP32-S3, and the
P4 is a far better fit because its **hardware JPEG decoder does Full HD at 48 fps**.

For us this is unusually well-matched: **the dashboard is already HTML served by FastAPI on the
Thor**, and the Thor is a Jetson AGX — headless Chromium is nothing to it. That would put the real
dashboard on the Tab5 with **no bespoke graphics at all**, and the touch panel would work.

**(b) On-device HTML/CSS — possible, limited.** `libwebsockets` can parse and render a **subset**
of HTML5 + CSS on ESP32-class hardware. Not a browser, but not nothing, and more than "no".

**(c) A real browser engine on-device — no.** There is no Chromium/WebKit for ESP32-P4 and there
will not be.

**Honest limits of (a).** It needs the network link; if the link drops the panel has nothing, so
nothing safety-bearing may depend on it (PRISM already carries that — see
`hri-interaction-design.md`). And a per-frame network round trip is wrong for the face, which has
to acknowledge a person inside about a second. So:

> **Dashboard: streamed HTML from the Thor. Face: native and local.** The split falls out of
> latency and failure behaviour, not preference.

---

## 5. What an LVGL port actually costs

Not a drop-in. The gap is the build system as much as the graphics.

| | Today | LVGL route |
|---|---|---|
| Framework | **Arduino** via PlatformIO + pioarduino | **ESP-IDF 5.4.2**, `idf.py` |
| Graphics | M5GFX / LovyanGFX, CPU rasteriser | LVGL 9.2.2 + `esp_lvgl_port`, PPA draw unit |
| Board HAL | M5Unified (`M5.Display`, `M5.Speaker`, `M5.Mic`, `M5.Touch`, `M5.Imu`, `M5.In_I2C`) | ESP-IDF BSP, or M5Unified alongside |
| Desktop iteration | none | **SDL build** |

**What would have to be reworked.** The face is the easy part; everything else on M5GFX is the
cost:

- `hmi.cpp` — the lift control pages, touch hit-testing, every `M5.Display` call
- `prompt.cpp`, the pun ticker, all text rendering
- `lift.cpp`'s ToF/I²C work is HAL, not graphics, so it mostly survives — but it is reached through
  M5Unified's I²C
- `main.cpp`'s tasks, OTA (`ArduinoOTA` is Arduino-only — ESP-IDF has its own OTA), UDP comms,
  the ES8388 bring-up sequence, the PI4IO amp poke
- The OTA path and `ota-from-thor.sh` assume the current image layout; `default_16MB.csv` carries
  two 6.5 MB slots and 3.5 MB SPIFFS, which must be preserved or the NVS-stored tilt limits and IMU
  offsets are lost

**Three ways to stage it, cheapest first:**

1. **Keep M5GFX, add PPA by hand.** Call the PPA/2D-DMA driver for the specific operations that
   hurt (fills, the mirrored second eye, blits of pre-rendered layers) and leave everything else.
   Smallest change, no framework migration, and it tests the premise that the accelerator is the
   win before anything is bet on it.
2. **Face in LVGL, rest on M5GFX.** Both can draw to the same panel in principle but sharing one
   display driver between two toolkits is where this gets unpleasant. Probably the worst option.
3. **Full ESP-IDF + LVGL port**, matching the factory firmware, gaining `esp_lvgl_port`,
   `smooth_ui_toolkit` and the desktop SDL build. Largest cost, lands on the stack M5Stack
   themselves support, and is the only route that gets laptop iteration.

**Prove it before porting.** A standalone ESP-IDF sketch — one pre-rendered eye layer plus a pupil
layer, composited by PPA into a DSI framebuffer, pupil swept across its full travel — measures the
frame rate and settles whether the accelerator delivers. That is a bench afternoon against a port
of several days, and 32 MB of PSRAM means layers can simply be pre-rendered once (a full
1280×720×16bpp frame is 1.8 MB; PSRAM holds about seventeen of them, or hundreds of component
tiles).

---

## 6. The clean-sheet face, if the premise holds

**The face is a compositor, not a rasteriser.** Layers blitted by PPA into a double-buffered PSRAM
framebuffer that DSI scans out:

- background — PPA fill
- eye body — pre-rendered bitmap; **second eye mirrored in hardware**
- **pupil — an independent layer, positioned anywhere**, which is what dissolves the ±11 px gaze
  budget rather than working around it
- highlight and eyelid — blits
- per-eye scale via hardware, which makes Anki's size-and-ratio gaze cue free

Nothing is rasterised per frame. The motion model (saccadic, per *Eyes Alive*) is independent of
all this and already written — see `face.cpp` — so it carries over unchanged.

---

## 6a. LVGL gotchas measured on this device

**Do not rotate a large object.** `lv_obj_set_style_transform_rotation` makes LVGL render that
object through a **layer**, which means allocating a buffer the size of its bounding box **on every
draw**. A 438×42 eyebrow asked for 73 KB per draw, the allocation failed, LVGL retried in a tight
loop, and the idle task starved until the task watchdog fired:

```
lv_draw_buf_create_ex: No memory: 438x42, cf: 16, stride: 1752, 73584Byte
lv_draw_layer_alloc_buf: Allocating layer buffer failed. Try later
E (90746) task_wdt: Task watchdog got triggered.  - IDLE0 (CPU 0)
```

Quantising the angle so the style was written less often **did not help**, because the cost is per
*draw*, not per *write*. An **`lv_line`** draws an angled, rounded, thick segment with no transform
and no layer allocation, and is the right tool for anything that just needs to sit at an angle.

**Write only what changed.** Every `lv_obj_set_*` invalidates the object and schedules a redraw, so
writing all properties unconditionally each frame makes the render cost independent of whether
anything moved. Caching the last written value per property is cheap and is what keeps headroom
available for the things that genuinely animate.

**Debug printing from the render loop is itself a suspect.** The ESP-IDF console goes out over
USB-serial-JTAG, and when no host is draining the port a write can **block the calling task**. A
diagnostic `printf` in the loop that drives the animation is therefore a plausible cause of the
animation freezing — and it cannot be used to diagnose a frozen animation without being part of the
problem. It also changes behaviour depending on whether a reader happens to be attached, and a
byte-limited reader like `head -c N` exits and stops draining partway through. Keep such telemetry
behind a build flag, off by default, and enable it only while a reader is genuinely attached.

**The desktop build will hide both of these.** It has the CPU and the heap to absorb a layer
allocation per draw, so a face that is smooth on a laptop can watchdog on the device. Design on the
desktop; **confirm frame health on the Tab5**, which is why the gate was specified as both.

---

## 7. The plan: subtract from the demo, do not rebuild it

**Decided approach, and it replaces section 6's "prove it with a sketch first".** The homebrew
budget offered for hand-calling PPA is declined: the demo firmware *exists to demonstrate this
device's capabilities*, engineers already did the performance and bring-up work, and subtracting
from something proven beats additively rebuilding it and rediscovering every constraint. The
sitting that produced this note is the argument — three separate high-confidence claims that we
were at the device's limits, all wrong.

### Everything hand-rolled this week already exists in the demo's HAL

| Hand-rolled here | Already in `app/hal/hal.h` |
|---|---|
| mic RMS + direction bench | `audioRecord(data, durationMs, gain)`, `startDualMicRecordTest()` + state enum |
| speaker cue via raw `tone()` | `audioPlay(pcm, async)`, `setSpeakerVolume()`, `playStartupSfx()`/`playShutdownSfx()` |
| poking ES7210 gain at `0x43`/`0x44` | a **`gain` parameter** on `audioRecord` |
| — | headphone-jack mic test, music playback, SD mount + scan, USB-A/C detect, external-antenna enable, `startWifiAp()` |
| — | `powerOff()`, **sleep with touch / shake / RTC wake**, INA226 bus voltage, power, shunt current |
| — | `startCameraCapture(lv_obj_t*)` — camera into an LVGL object in one call |
| — | `lvglLock()`/`lvglUnlock()`, display brightness, CPU temperature |

### Architecture, which is already the right shape

`app/hal` is an **interface**, implemented twice: `platforms/tab5` (hardware) and
`platforms/desktop` (SDL). Apps are written against the HAL, and `app/apps/` holds discrete apps
with an `app_template` and an `app_generator.py`. Subtracting apps and adding one is the intended
workflow, not a hack.

**Keep:** `app/hal`, `platforms/tab5` (DSI/LVGL/PPA config, ES8388+ES7210 bring-up, camera, IMU,
RTC, INA226, SD, `wifi_c6_fw/` for the C6 radio, `sdkconfig`), `platforms/desktop`, and
LVGL + `smooth_ui_toolkit` + `mooncake`.

**Subtract:** `app_launcher` and the per-function demo test apps; demo assets we do not ship.

**Add:** one app — the OpenDubs face; the StamPLC UDP protocol (`STATE`, `LIFT`, `TURN`, `GAZE`,
`BEEP`, `CHIME`, `PMODE`, `VOL`, `OTA`); the lift control pages; and OTA, which is the real work.

### The find worth acting on: the demo ships face detection

`partitions.csv` carries `human_face_det, data, spiffs, 400K`. For "notice a person and look at
them", a **detected face** is a far better gaze target than an inter-channel audio level ratio —
it gives bearing *and* confirms a person is actually there, which audio cannot. The camera, the
model and the LVGL plumbing all come in the same box. This reframes the attention work in
`hri-interaction-design.md`: audio direction becomes the fallback and the attention-getter,
vision becomes the aim.

### The two real blockers, in order

1. **The demo cannot do OTA.** Its layout is a single 10 M `factory` app with no `otadata` and no
   second slot. The Tab5 lives inside the robot on the e-stop-protected branch and is flashed over
   the air (`firmware/tab5_face/ota-from-thor.sh`), so this is not optional. Re-adding two app
   slots means the image must fit **6.25 M** rather than 10 M, and `ArduinoOTA` has no ESP-IDF
   equivalent — `esp_https_ota` replaces it, and `ota-from-thor.sh` changes with it. **Measure the
   subtracted image size early**; it decides feasibility.
2. **NVS must survive.** Both tables put `nvs` at `0x9000`, which is the good news, but the demo
   sizes it `0x6000` against our `0x5000`. The per-robot tilt limits and IMU offsets live there.
   Verify a resize preserves them rather than assuming it; losing them silently is exactly the
   class of failure that has cost this project days.

---

## 8. Measured: the subtractive build fits, with room

Built on 2026-10-04 on macOS. **The OTA blocker in §7 does not exist** — the demo fits the OpenDubs
OTA slot *before* any subtraction.

| build | image | vs the 6.25 MB `app0`/`app1` slot |
|---|---|---|
| demo, **unmodified** | **5.49 MB** | fits, 0.76 MB spare |
| demo, **launcher + 3 images removed** | **2.76 MB** | fits, **3.49 MB spare** |
| *(current Arduino face, for scale)* | 1.45 MB | — |

One subtraction halved it: deleting `app/apps/app_launcher` plus `launcher_bg.c` (11 MB of source
array), `porta_i2c_dev_chart.c` and `internal_i2c_dev_chart.c` took `app/assets` from **15 MB to
692 KB** and the image from 5.49 MB to 2.76 MB. The separation is clean — those three images were
referenced only by `assets.h` and by `app_launcher`'s own views — and `app.cpp` simply calls
`on_startup_anim()` then `on_install_apps()`, so swapping `AppLauncher` for `AppTemplate` in
`app_installer.h` is the whole change.

So the subtracted build carries LVGL 9, the complete HAL, camera, audio, IMU, RTC, INA226, SD and
the C6 radio in 2.76 MB: about 1.3 MB more than our bespoke firmware, for very much more
capability, leaving 3.49 MB for the face app, its assets, and OTA.

### Reproducible recipe

```sh
brew install cmake ninja                       # ESP-IDF needs both; it ships neither on macOS
git clone -b v5.4.2 --depth 1 --recursive --shallow-submodules \
    https://github.com/espressif/esp-idf.git && (cd esp-idf && ./install.sh esp32p4)
git clone --depth 1 https://github.com/m5stack/M5Tab5-UserDemo.git demo
cd demo && python3 fetch_repos.py              # LVGL 9.2.2, mooncake, smooth_ui_toolkit
# apply the two pins below to platforms/tab5/main/idf_component.yml, then:
cd platforms/tab5 && rm -f dependencies.lock
. ../../../esp-idf/export.sh && idf.py build
```

### Two upstream inconsistencies — this does NOT build out of the box

Both cost a build cycle, and both are version drift rather than anything about the device:

1. **LVGL.** `esp_lvgl_port` 2.5.0 declares `lvgl/lvgl: '>=8,<10'` and the resolver picks **8.4.0**,
   but the Tab5 BSP (`m5stack_tab5.c`) calls **LVGL 9** APIs — `lv_indev_create`,
   `lv_indev_set_type`, `lv_indev_set_read_cb`, `lv_indev_set_display`. Worse, the **committed
   `dependencies.lock` itself pins 8.4.0**, so the repository as shipped cannot build its own
   source. `repos.json`'s 9.2.2 serves the *desktop* build; the IDF build resolves separately.
   Fix: pin `lvgl/lvgl: '~9.2.2'` and delete the lock.
2. **`esp_codec_dev`.** Nothing pinned it, so it floated to a version that fixed an upstream typo —
   `ES7120_SEL_MIC*` became `ES7210_SEL_MIC*` — which the BSP still spells the old way. The shipped
   lock pins **1.3.4**. Fix: `espressif/esp_codec_dev: 1.3.4`.

**Also note the build system globs sources at configure time**, so after deleting an app directory
`idf.py reconfigure` is required or ninja fails on a file it still expects.

### What is still to do, in order

1. **Restore OTA partitions.** The demo is one 10 MB `factory` app with no `otadata`. Our
   `default_16MB.csv` already has the right shape; the measurements above say the image fits it.
   `esp_https_ota` is already compiled into this build, so the capability is present — `ArduinoOTA`
   is what has no equivalent, and `ota-from-thor.sh` changes with it.
2. **Verify NVS survives.** Both tables place `nvs` at `0x9000`; the demo sizes it `0x6000` against
   our `0x5000`. Per-robot tilt limits and IMU offsets live there.
3. **Port the face as one app** against the HAL, replacing `AppTemplate`.
4. **Port the StamPLC protocol and the lift pages.**

### Port A devices: the ToF and the 8Servo

Both hang off the Tab5's **Grove Port A, GPIO 53 (SDA) / 54 (SCL) at 100 kHz**, on Arduino `Wire`.
Two things are easy to get wrong here, and an earlier draft of this section got both wrong.

**There are TWO ToF sensors and this is not the lift's.** `lift.cpp` says so in its own header
comment: the Port A part is the **FRONT rangefinder, screen-mounted** — "what the head is looking
at", published as `CAN_ID_TOF_FRONT`. **The lift's height sensor is on the StamPLC and arrives over
UDP.** So this sensor is HRI-relevant (how far away is the person) and is *not* in the pinch-guard
or height-safety path. Despite living in a file called `lift.cpp`.

**The Tab5 drives the 8Servo itself** at `0x25`, including `readServoCurrent()` and the
angle-to-microsecond conversion. It is not on the StamPLC.

The bus needs no work: `bsp_ext_i2c_init()`, `bsp_ext_i2c_deinit()` and `bsp_ext_i2c_get_handle()`
return an `i2c_master_bus_handle_t`, and `BSP_EXT_I2C_SDA`/`SCL` are **GPIO 53/54** — the same
physical port, already brought up by the BSP.

**This is register work, not driver authorship.** The API surface actually used is thin, and the
VL53L0X read path is *already* raw register access in our own code:

| device | what the code actually does | port effort |
|---|---|---|
| 8Servo `0x25` | register reads/writes, no vendor library | mechanical |
| VL53L0X | `readReg(RESULT_INTERRUPT_STATUS)`, `readReg16Bit(RESULT_RANGE_STATUS+10)`, `writeReg(SYSTEM_INTERRUPT_CLEAR, 0x01)` — raw already | mechanical |
| VL53L1X | `setDistanceMode`, `setMeasurementTimingBudget`, `startContinuous`, `read` — all register writes underneath | mechanical, plus the init blob |

The only piece that is not a trivial transaction is `init()`, and on the VL53L1X that is a **fixed
default-configuration byte array** — data, not logic, and lifted wholesale. The dual-model
autodetect ports in minutes because `lift.cpp` already hand-rolls the model-ID probe over raw
`Wire` (VL53L1X answers `0xEACC` at `0x010F`, VL53L0X answers `0xEE` at `0xC0`). M5's own
documentation also ships ToF unit demos whose register sequences are the reusable part.

So: `Wire.beginTransmission/write/endTransmission/requestFrom` becomes `i2c_master_transmit` and
`i2c_master_transmit_receive` against the BSP handle. No library needs porting.

**What the bench still cannot tell us**, since neither part is attached: that ranging returns
sensible distances, and that the servos move. Those need the hardware. Not safety-critical for the
front ToF, but it is the input to any "how close is the person" behaviour, so a plausible-but-wrong
distance would be quietly wrong rather than obviously broken.

---

## 6b. The graphics budget, and how to spend it

Written because the face ran for a while and then froze, and "add code until it stops" is not a
method. The numbers below are the frame budget; the practices after them follow from the numbers
rather than from taste.

### The budget

| | |
|---|---|
| Panel | 1280 × 720 = **921,600 px** |
| Measured software blit rate | **~16 Mpx/s** (from the sprite bench: 55.1 fps × 290 k px) |
| Full-screen redraw at 30 fps | 27.6 Mpx/s — **1.7× over budget** |

**So full-screen redraw is not affordable, and never was.** Everything depends on keeping the dirty
region small. LVGL only redraws invalidated areas, which is why this works at all — and which makes
*dirty area per frame* the number that matters, not object count or arithmetic.

### Where the frame actually goes

| what we animate | dirty px/frame | share of a screen |
|---|---|---|
| **eye body resizing (×2)** | **~922,000** | **~100%** |
| pupil moving (×2) | 53,000 | 5.8% |
| band, 48 segments | 65,000 | 7.1% |
| brow re-point (×2) | 33,000 | 3.6% |

**The eye body spends the whole budget on its own**, because a resize dirties both the old and the
new rectangle, twice, on a 480 × 480 object. Everything else together is 16% of one screen. A
fixed-size body with only the pupil moving would be **17× cheaper**.

That is also the likely reason it ran and *then* froze, which a simple over-budget condition would
not explain: **complex gradients are cached per geometry**, so a body whose size changes every frame
mints a new gradient every frame. The churn accumulates until an allocation fails — the same class
of failure as the rotated brow, arrived at slowly instead of immediately.

### Cost hierarchy, most expensive first

1. **Layer-inducing styles** — rotation, and some blend modes. Allocates a bounding-box buffer
   **per draw**. Catastrophic at any size; killed the brow outright.
2. **Complex (radial/conical) gradients on animated geometry.** Per-pixel maths, and the cache is
   keyed on geometry, so animating size thrashes it. Fine on things that hold still.
3. **Resizing large objects.** Dirties old area plus new, and re-derives radius and gradient.
4. **Dirty area.** Linear in pixels redrawn. This is the budget.
5. **Number of invalidations per frame** — each `lv_obj_set_*` schedules one; overlapping areas
   compound. A write-guard reduces the *count*, not the cost of each.
6. Linear (`HOR`/`VER`) gradients — cheap, a per-scanline lookup.
7. Solid fills and plain blits — cheapest thing available.

### Practices that follow

- **Animate position, not size.** Moving is cheaper than resizing and does not invalidate caches.
- **Keep the moving thing small.** Move the pupil, not the eyeball. The cost is pixels.
- **Separate what changes from what does not**, and never touch the static part.
- **Complex gradients only on static geometry.** If a thing must both shade and move, pre-render it.
- **Guard every write**, while remembering the guard reduces how many writes happen, not what each
  costs — finer quantisation makes *more* writes pass the guard, which is how 64 opacity levels
  froze a band that ran at 16.
- **Change one variable at a time.** Both freezes this session came from changing two together and
  then being unable to attribute the failure.

### What this means for the things we have not built yet

**Themes should be ASSETS, not primitives.** A themed eye pre-rendered as an image and blitted is
cheaper than a gradient-filled rounded rect re-derived every frame, and it removes the ceiling on
visual complexity — any amount of detail costs the same blit. There is **3.5 MB of SPIFFS already
partitioned and unused**, and `bg_image_src` with `bg_image_recolor` can even tint one asset per
state. This is the compositor idea arriving where it is cheapest to adopt.

**State of charge belongs on the band, and costs nothing.** The band is already 48 addressable
segments: lighting a proportion of them is a gauge with **zero new objects** and at most one opacity
write each. The old renderer had a `gauge` branch in `drawEye` using `soc` for the same purpose,
inside the eye — the band is a better home, because it does not spend the eyes.

**And that resolves the charging question** rather than re-opening it. The tension was that charging
is sometimes idle and sometimes attending, so pinning an emote to it is wrong either way. But
charging is a **condition to display, not a mood to adopt**: put the charge level on the band and
the eyes stay free to be attending or idle as the situation warrants. No emote pin needed.
