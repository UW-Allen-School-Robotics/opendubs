# Tab5 on ESP-IDF — the deltas we own

The Tab5 ESP-IDF effort builds on M5Stack's factory firmware
(`m5stack/M5Tab5-UserDemo`) rather than reimplementing it. That tree is ~450 MB with its
dependencies and is **not vendored here**. This directory holds only the parts we wrote or changed,
plus the recipe to reconstruct a working build.

Background and the measurements: [`../../notes/design/tab5-platform.md`](../../notes/design/tab5-platform.md).
Plan of record: [`../../notes/workplan-tab5-idf.md`](../../notes/workplan-tab5-idf.md).

## Reconstructing the build

```sh
brew install cmake ninja sdl2            # IDF needs cmake+ninja; sdl2 is for the desktop build
git clone -b v5.5.5 --depth 1 --recursive --shallow-submodules \
    https://github.com/espressif/esp-idf.git && (cd esp-idf && ./install.sh esp32p4)
git clone --depth 1 https://github.com/m5stack/M5Tab5-UserDemo.git demo
cd demo && python3 fetch_repos.py        # LVGL 9.2.2, mooncake, smooth_ui_toolkit
ls dependencies/lvgl/src                 # CHECK IT. See the warning below.
```

**IDF 5.5.5, not 5.4.2.** This line said `v5.4.2` long after the tree had moved to 5.5.5 — which
`sdkconfig.defaults` makes obvious (its bootloader-size and `ESP32P4_REV_MIN` notes are 5.5-era) and
which `notes/design/tab5-face-architecture.md` has assumed throughout. 5.4.2 is not a fallback
worth keeping: the failure that forced the move was IDF issue #15996, where 5.4.x's allocator
advertises TCM as byte-capable internal memory and FreeRTOS then rejects a TCB that lands there —
and it bites exactly when internal DRAM is tight, which is where this firmware lives.

**Two things that fail quietly when reconstructing the tree**, both hit on 2026-10-05:

- A `--recursive` shallow clone of esp-idf can die mid-fetch (`fatal: fetch-pack: invalid
  index-pack output`) and leave an unusable tree. The published full-source archive
  (`esp-idf-v5.5.5.zip` from the release assets, submodules included) is one resumable download and
  is the more reliable path on a flaky connection.
- **`fetch_repos.py` can skip a repo and still print nothing.** `dependencies/lvgl` was simply
  absent after a clean run. The *device* build does not notice, because that uses the LVGL managed
  component from the registry — but the desktop build will not even configure
  (`add_subdirectory given source "dependencies/lvgl" which is not an existing directory`). Check
  the directory, do not trust the script.

Then apply our deltas:

1. **Two dependency pins** in `platforms/tab5/main/idf_component.yml`, and delete
   `dependencies.lock`. Without these the demo does not build — see the platform note:
   ```yaml
   lvgl/lvgl: '~9.2.2'              # esp_lvgl_port allows >=8,<10; the BSP needs LVGL 9 APIs
   espressif/esp_codec_dev: 1.3.4   # newer versions renamed ES7120_SEL_MIC* -> ES7210_SEL_MIC*
   ```
2. **`lv_conf.h` is now tracked here** — copy `lv_conf.h` from this directory over the demo's.
   It is tracked because it was NOT, and that cost a long debugging detour: enabling
   `LV_USE_DRAW_SW_COMPLEX_GRADIENTS` there is where a run-for-a-while-then-freeze began, and
   because the file lived only in the untracked demo tree, `git log -S` on that flag returned
   nothing and the change was invisible to the record. **Any config that can change runtime
   behaviour belongs under version control.**
3. **Subtract the demo launcher**, which is 14.8 MB of the 15 MB of assets:
   - `rm -rf app/apps/app_launcher`
   - `rm -f app/assets/images/{launcher_bg,porta_i2c_dev_chart,internal_i2c_dev_chart}.c`
   - drop those three declarations from `app/assets/assets.h`
   - in `app/apps/app_installer.h`, drop the `app_launcher` include and install `AppFace`
4. **Copy `app_face/` into `app/apps/`.**

Build and flash:

```sh
cd platforms/tab5 && . ../../../esp-idf/export.sh
idf.py reconfigure && idf.py build          # reconfigure: sources are globbed at configure time
idf.py -p /dev/cu.usbmodem2101 flash
```

Desktop build, which is where design iteration belongs:

```sh
cd demo && cmake -S . -B build-desktop && cmake --build build-desktop --target app_desktop_build -j8
./build/desktop/app_desktop_build
```

## Every file we modify in the demo tree

Listed so none of it can be invisible again. If you change another, add it here.

| demo-tree file | what we change | tracked here |
|---|---|---|
| `lv_conf.h` | LVGL config — **this is the one that bit us** | yes, copy it over |
| `platforms/tab5/main/idf_component.yml` | the two dependency pins | documented above |
| `platforms/tab5/dependencies.lock` | deleted, so the pins re-resolve | documented above |
| `app/apps/app_installer.h` | drop `app_launcher`, install `AppFace` | documented above |
| `app/assets/assets.h` | drop three removed image declarations | documented above |
| `app/apps/app_face/` | our app, subdirectories included | yes, this directory |
| `platforms/tab5/main/CMakeLists.txt` | `canon_in_d.mp3` un-embedded (−934 KB) | patched by `build.sh`, which fails loudly if the anchor moves |
| `platforms/tab5/main/hal/components/hal_audio.cpp` | the canon symbols aliased to the startup sfx so the vendor's music test still links | same |

`app/apps/app_face/` now has subdirectories: `percept/` is the perception layer and `shared/` holds
the cross-task primitives. **Nothing under either may include `<lvgl.h>` or `hal/hal.h`** — the
latter because it includes the former, so reaching into the HAL for something as innocent as a
millisecond clock would put LVGL into the layer whose entire purpose is not having it. `AUDIT.md`
holds the greps.

## Building

Use `./build.sh` rather than invoking either build alone, because the two drifted once and the
desktop went stale without anyone noticing:

```sh
export OPENDUBS_DEMO=/path/to/M5Tab5-UserDemo        # OPENDUBS_IDF defaults to its sibling esp-idf
./build.sh both        # device (build + flash) and desktop, from one command
./build.sh image       # device build ONLY, no board needed; prints the image size
./build.sh desktop     # SDL build and run
./build.sh asan        # desktop with AddressSanitizer + UndefinedBehaviorSanitizer
./build.sh percept     # host self-test of the perception layer -- no IDF, no demo tree, no board
```

It copies our files into the demo tree first, so the repo is always the source of truth. The copy is
**recursive** (`app_face/` has subdirectories) and it **deletes its destination first**, because a
copy that only ever adds leaves a file that was removed from the repo compiling happily in the demo
tree.

`./build.sh percept` needs neither `OPENDUBS_DEMO` nor an IDF install: the perception layer is pure
compute with no LVGL, no HAL and no FreeRTOS, so the same translation units that run on the panel
compile natively. Twelve assertions against a synthetic moving blob, in seconds. It is the only
verification available to somebody who does not have the hardware, and it is worth running before
every flash.

### Build flags

All off by default. `build.sh` turns them into one generated header,
`app_face/percept/percept_config.h`; each instrument's translation unit is wrapped in its own `#if`
with no-op fallbacks, so "off" means absent from the image rather than merely quiet.

| variable | what it adds |
|---|---|
| `OPENDUBS_FACEDET=1` | esp-dl and the face detector (close-range confirmation). +1.2 MB of flash. Also appends the two esp-dl pins to the dependency manifest, which a default build must never inherit — see `idf_component.yml`. |
| `OPENDUBS_DBG_PERCEPT=1` | the camera preview canvas, the attention reticle (amber = motion, cyan = face), and the perception self-test with its verdict on the panel |
| `OPENDUBS_DBG_SERIAL=1` | console telemetry from the perception task. **Off by default as a correctness matter**: a USB-serial-JTAG write blocks the calling task when no host is draining the port, so console telemetry is itself a plausible cause of the freeze it would be used to diagnose. Only set it when someone is actually reading the port. |

`OPENDUBS_PROBE_FACEDET` is still accepted as the old name for `OPENDUBS_FACEDET`.

### Build flags

Each is an environment variable read by `build.sh`, which turns it into a **generated header**
in the demo tree rather than a `-D`. The reason is in `build.sh`: `idf.py -D` sets a CMake
variable, not a compile definition, and bridging it would mean editing the vendor's
`CMakeLists.txt`, a file we neither own nor sync.

| variable | default | what it does |
|---|---|---|
| `OPENDUBS_TOUCH_UI` | **1** | The technician screen, the fault screen and the grade machinery (`ui_touch.cpp`, `ui_controls.cpp`, `ui_grade.cpp`). `0` compiles them out: every function has an `#else` no-op body, so `app_face.cpp` compiles unchanged and the linker drops the lot. Defaults ON because it is product, not instrument. |
| `OPENDUBS_TOUCH_DEBUG` | 0 | Adds a magenta crosshair at the live touch point. This is the instrument that settles whether the 90-degree display rotation's handedness matches the digitiser — see `TOUCH_UI.md`. |
| `OPENDUBS_PROBE_FACEDET` | 0 | The face-detection self-test, and the only flag that pulls in esp-dl. |

Both `0` and `1` of `OPENDUBS_TOUCH_UI` are built and linked clean on the desktop target; the
device target has not been built (no ESP-IDF on the machine that wrote this).

### The twin shares its picture (desktop only)

Set **`OPENDUBS_FACE_FRAMES=<file>`** when you RUN the desktop twin (a run-time switch, not a
build flag) and it writes its finished frame into that file, scaled to 320x180, about 15 times a
second. The choreography tools' MuJoCo body twin shows it on the robot's head, so one window shows
the whole robot (`software/choreo/face.sh` sets it). Layout and the torn-frame rule:
`app_face/frame_tap.h`. Nothing is redrawn for it — the SDL build's direct render mode already
holds the whole frame — and none of it is in the device image.

## Measured

On IDF 5.4.2, before `canon_in_d.mp3` was dropped:

| | |
|---|---|
| demo, unmodified | 5.49 MB |
| demo, launcher subtracted | 2.76 MB |
| **+ `app_face`** | **2.76 MB** (the app adds ~3 KB) |
| OpenDubs OTA slot | 6.25 MB → **3.49 MB headroom** |

On IDF 5.5.5, launcher subtracted and the MP3 dropped (2026-10-05, `./build.sh image`):

| build | bytes |
|---|---|
| the face before motion saliency, same tree, built first | 2,003,024 |
| face + motion saliency, all flags off | 2,008,608 |
| `OPENDUBS_DBG_PERCEPT=1` | 2,013,536 |
| `OPENDUBS_FACEDET=1 OPENDUBS_DBG_PERCEPT=1` | 3,219,200 |

Motion saliency, the self-motion gate and the attention arbiter together cost **5,584 bytes**. The
1.2 MB is esp-dl and its model, and it buys only the ~1.1 m of close-range face confirmation — which
is why motion is the primary cue and the detector is gated.

**Compare image sizes only within one freshly resolved tree.** Flipping `OPENDUBS_FACEDET` rewrites
`idf_component.yml`, so the component manager re-resolves everything — which on its own moved a
byte-identical baseline by about 7 KB. A before/after taken either side of a detector build is
measuring the dependency solver, not the change.

## Warnings

- **The demo's `partitions.csv` is a single 10 MB `factory` app with no OTA slots**, and flashing it
  rewrites the partition table and moves NVS. Fine on a bench board; **not** on the robot, where
  per-robot tilt limits and IMU offsets live in NVS. Phase 4 resolves this.
- **This flashing path is `idf.py`, not PlatformIO.** It does not replace the rote procedure in
  `CLAUDE.md`, it is a second path alongside it. The StamPLC is unaffected and unchanged.
