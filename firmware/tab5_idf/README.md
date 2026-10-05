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
git clone -b v5.4.2 --depth 1 --recursive --shallow-submodules \
    https://github.com/espressif/esp-idf.git && (cd esp-idf && ./install.sh esp32p4)
git clone --depth 1 https://github.com/m5stack/M5Tab5-UserDemo.git demo
cd demo && python3 fetch_repos.py        # LVGL 9.2.2, mooncake, smooth_ui_toolkit
```

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
| `app/apps/app_face/` | our app | yes, this directory |

## Building

Use `./build.sh` rather than invoking either build alone, because the two drifted once and the
desktop went stale without anyone noticing:

```sh
export OPENDUBS_DEMO=/path/to/M5Tab5-UserDemo        # OPENDUBS_IDF defaults to its sibling esp-idf
./build.sh both        # device (build + flash) and desktop, from one command
./build.sh desktop     # SDL build and run
./build.sh asan        # desktop with AddressSanitizer + UndefinedBehaviorSanitizer
```

It copies our files into the demo tree first, so the repo is always the source of truth.

## Measured

| | |
|---|---|
| demo, unmodified | 5.49 MB |
| demo, launcher subtracted | 2.76 MB |
| **+ `app_face`** | **2.76 MB** (the app adds ~3 KB) |
| OpenDubs OTA slot | 6.25 MB → **3.49 MB headroom** |

## Warnings

- **The demo's `partitions.csv` is a single 10 MB `factory` app with no OTA slots**, and flashing it
  rewrites the partition table and moves NVS. Fine on a bench board; **not** on the robot, where
  per-robot tilt limits and IMU offsets live in NVS. Phase 4 resolves this.
- **This flashing path is `idf.py`, not PlatformIO.** It does not replace the rote procedure in
  `CLAUDE.md`, it is a second path alongside it. The StamPLC is unaffected and unchanged.
