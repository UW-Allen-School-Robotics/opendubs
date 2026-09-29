# Flash a StampPLC

The **StampPLC** (M5Stack, ESP32-S3) is the mobile base's edge controller — it reads the RC receiver over
SBUS, runs the mecanum drive mixer and safety arming, watches power/BMS/CAN, and streams telemetry to
the Thor. This page flashes its firmware (`firmware/rc_serial_probe/`) onto a new board.

## What you need

- A StampPLC and a **USB-C** cable.
- The firmware source: this repo, `firmware/rc_serial_probe/`.
- A **build host** — either:
    - a **Thor that is already flashed and provisioned** per **[Thor setup](thor-setup.md)** — the
      canonical path, so set the Thor up *first*; or
    - **any dev machine** (macOS/Linux) with **PlatformIO** (`pip install platformio`, or the VS Code
      extension) — no Thor needed, so use this for a brand-new build before the Thor is ready.

## Pinned libraries — do not let them drift

`platformio.ini` **pins** the M5 libraries to a known-good set:

```ini
lib_deps =
  https://github.com/m5stack/M5StamPLC.git#054fdd17e15a183f685628e6843444dd6dae5259
  m5stack/M5GFX@0.2.21
  m5stack/M5Unified@0.2.15
  m5stack/M5-Ethernet@4.0.0
```

!!! warning "If a clean build fails with `invalid use of member function 'BtnA()' / 'Display()'`"
    The M5StamPLC pin was lost and PlatformIO resolved a newer, API-incompatible "1.2.0" where
    `BtnA`/`Display` became methods. Restore the pinned `#054fdd17…` line above, then force a
    re-resolve: `rm -rf .pio/libdeps .pio/build` and rebuild. Keep every machine on the same pin.

## Flash it

### Option A — from the Thor (canonical)

If the StampPLC is plugged into a provisioned Thor, use the helper — it frees the serial port (the
dashboard holds it), flashes, and restarts the dashboard even if the flash fails:

```bash
/opt/opendubs/rc_serial_probe/flash-from-thor.sh
```

(That script runs `pio run -t upload` from `/opt/opendubs/piovenv`. The tooling is installed by
`rc_serial_probe/provision-firmware.sh`, chained from `provision-all.sh`.)

### Option B — from a dev machine (macOS / Linux)

```bash
cd firmware/rc_serial_probe
pio run                                   # build
pio run -t upload                         # build + flash (auto-detects the bootloader port)
pio device monitor -b 115200              # watch telemetry
```

!!! note "If more than one M5Stack ESP32 board is plugged in"
    M5Stack's ESP32 boards all enumerate as Espressif USB-serial devices, so with two or more
    connected auto-detect can grab the wrong one. Either unplug the others while flashing, or pin the
    port explicitly to the target board:

    ```bash
    # Linux — the target board's stable by-id path (keyed on its own MAC):
    pio run -t upload --upload-port /dev/serial/by-id/usb-Espressif_USB_JTAG_serial_debug_unit_<MAC>-if00
    # macOS — /dev/cu.usbmodem* (unplug the other boards to disambiguate)
    ```

!!! tip "If the build fails on macOS with a platform error"
    On some macOS setups PlatformIO resolves a newer `espressif32` platform version that fails to
    build (for example an `esp_task_wdt` signature mismatch). If you hit a platform-level build error,
    build on Linux or the Thor (`/opt/opendubs/piovenv`), or pin the `platform =` version in
    `platformio.ini` to a known-good release.

## Verify

Open the serial monitor (`pio device monitor -b 115200`) and confirm telemetry is streaming:

- `[SENS] power=…V` and `[SBUS] … ok=… bad=…` — the board is alive and reading the RC link.
- `[IO] di=… relay=…` — digital inputs / relays.
- `[NET] eth UP ip=…` — **the PoE Ethernet link came up and got a DHCP IP.** (If you see
  `[NET] eth DHCP failed`, the PoE cable/link or DHCP isn't ready — fine for a USB-only bench flash.)

Plugged into a provisioned Thor with the dashboard running, the StampPLC's panels (power, RC, drive,
IO) populate within a couple of seconds.

## Next

- Move the StampPLC's link off USB onto Ethernet: [StampPLC ↔ Thor over Ethernet](stamplc-ethernet-transport.md).
  **Reflashing always uses USB** — keep a USB port reachable.
- Firmware internals and the `[TAG]` telemetry contract: `firmware/rc_serial_probe/README.md`.
