# M5StampPLC firmware (PlatformIO)

> **Legacy name:** this directory is still called `rc_serial_probe` (its origin was a
> one-off probe to read an RC receiver on the StampLC blue Grove port). It has since
> grown into the **robot's StampLC firmware** and is kept under this path because the
> deploy/flash tooling (`/opt/opendubs/rc_serial_probe`, `flash-from-thor.sh`,
> `provision-firmware.sh`) references it.

The firmware (`src/main.cpp`) is the M5StampPLC's edge-controller program. It emits text
telemetry over USB-serial (`[SENS]`/`[PDH]`/`[CAN]`/`[IO]`/`[ENC]`/`[SBUS]`/`[I2C]`) consumed by
the dashboard, and accepts host commands (see below). It owns: REV PDH proprietary-CAN
telemetry, DI/relay safety I/O, the external I²C bus (PaHub mux on Port A), IO-socket/INA/LM75
sensing, **SBUS RC ingest**, a **local screen/LED UI**, and the **PRISM state indicator**.

**RC IS read here.** The StampLC is the SBUS mediator: inverted SBUS on blue-port **G5**,
decoded to `rc_ch[]` (channel map in `../` memory / `docs`). Flash from the Thor with
`flash-from-thor.sh` (canonical), or `pio run -e stamplc -t upload` from a Mac on USB.

## Transport: USB serial **and** Ethernet (PoE UDP)

Telemetry and commands run over **either** USB serial **or** the PoE Ethernet link — the same
`[TAG]` text protocol is teed over both (telemetry broadcast on UDP **:5005**; commands received on
:5005 and routed through the same handler). The StampPLC has its own 24 V supply, so USB is needed
**only for flashing** — normal operation can run entirely over Ethernet with USB unplugged.

> **Reflash over Ethernet (OTA).** Once this firmware is on the board, reflash with **no USB**:
> from the Thor, [`./ota-from-thor.sh`](ota-from-thor.sh) builds, serves the `.bin`, and sends the
> `OTA` command so the board pulls the image and reboots into it. The **initial** flash is still
> USB-only (`esptool` needs USB-CDC), and a complete-but-buggy OTA image needs a USB reflash to
> recover — so keep a USB port reachable as the recovery fallback. Requires a dual-OTA partition
> table (`board_build.partitions = default.csv`, already set).

Full design, transport selection (`OPENDUBS_STAMPLC_TRANSPORT=auto|udp|serial`), and the
apply/verify runbook: [`../docs/stamplc-ethernet-transport.md`](../docs/stamplc-ethernet-transport.md).

## Host commands (newline-terminated — USB serial or Ethernet UDP :5005)

`PDH_SWITCH 0|1` · `PDH_CLEAR_FAULTS` · `PDH_ID <0..63>` · `PDH_ID_AUTO` ·
`RELAY <ch> <0|1>` · `ESTOP` · `ENC_RESET` · `I2C_SCAN` · `I2C_INFO` ·
`LED_COLOR/LED_LEN/LED_OFF` (PRISM RGB over I²C) ·
**`SERVO8 <pin> <pulse_us>`** (drive an 8Servo channel) · **`PAGE <n>`** (switch screen page) ·
**`S8RESET [off_ms]`** (power-cycle the 8Servo via relay R1 — the reliable clear for the ESC
solid-red fault; inert until R1 gates the 8Servo 5 V) ·
**`OTA http://<host>:<port>/fw.bin`** (pull a firmware image over Ethernet and self-flash).

## Local UI

Onboard TFT (M5Canvas sprite) + status LED + PRISM, refreshed ~6 Hz:
- **Page 0 (RC/DRIVE):** big MODE hero (OFF/MANUAL/AUTO, or NO-LINK/FAILSAFE in red), live
  F/R+ROT+STR drive bars, DEAD + SPD% at the bottom.
- **Page 1 (SAFETY):** DI0 e-stop / contactor R0 / weld DI6 / relays.
- **PRISM state sync:** robot state → 8Servo **ch0** PWM → goBILDA PRISM (police=alarm,
  blue=OFF, amber=manual/released, green=manual/held, violet-pulse=AUTO).
- **Buttons are stubbed** — `M5.begin()`+`M5StamPLC.begin()` double-init breaks M5Unified's
  StampPLC button state (and single-begin panics our setup). Use `PAGE` to switch pages. See
  the `setup()` / `uiButtons()` comments for the root cause and fix path.

## External I²C map (PaHub on Port A)

8Servo (0x25) on **ch5**; PRISM driven off 8Servo **ch0 via PWM** (not I²C); SparkFun OTOS
(0x17) is currently **off the bus** (it shorted/hung Port A — diagnose in isolation before
reconnecting).

This folder is a PlatformIO project (`platformio.ini`, env `stamplc`).

## Documentation Wiki

- `docs/index.md` (wiki index)
- Old telemetry-spec (superseded early plan — NOT the current contract): archived at
  `../docs/history/telemetry-spec.md`. The current serial telemetry is the `[TAG] key=value`
  lines emitted over USB by this firmware (see the telemetry section above).
- Cross-repo robot documentation hub:
  - `../docs/README.md`
  - `../docs/system-overview.md`
  - `../docs/control-authority.md`
  - `../docs/state-machines.md`
  - `../docs/pwm-esc-safety.md`
  - `../docs/rev-pdh-can-feasibility.md`

## What It Uses

- Official `M5StamPLC` library.
- Official pin map from `M5StamPLC/src/pin_config.h`:
  - blue port G5 = GPIO 5
  - blue port G4 = GPIO 4

## Wiring

- Receiver TX -> blue port G5 (GPIO 5, UART RX in this sketch)
- Receiver GND -> blue port GND
- Receiver power -> blue port 5V or 3V3 as required by your receiver

Do not power a 3.3V-only receiver from 5V.

## Behavior

RC is **locked to inverted SBUS** (`100000 8E2`, RX on G5). Decoded frames set `rc_ch[]`,
drive the local screen/PRISM, and print `[SBUS] OK ...` telemetry. (The old multi-profile
auto-probe — iBUS/CRSF/SBUS — lives in the standalone `rc_serial_probe.ino` sketch if you
ever need to re-identify an unknown receiver.)

## PlatformIO Workflow

The project config is in `platformio.ini` and source is in `src/main.cpp`.

### VS Code Extension

- Install `PlatformIO IDE` extension.

### Build/Upload/Monitor (CLI)

Run these from this folder:

```bash
pio run
pio run -t upload
pio device monitor
```

Or one-shot upload + monitor:

```bash
pio run -t upload && pio device monitor
```

### Notes

- `platformio.ini` is pinned for ESP32-S3 Arduino and USB CDC serial.
- Upload and monitor ports are set to `/dev/cu.usbmodem2121101`.
- If your port changes, update `upload_port` and `monitor_port` in `platformio.ini`.

## Expected Result

Continuous telemetry over USB serial (`[IO]`/`[ENC]` ~10 Hz, `[SBUS]`/`[PDH]`/`[CAN]`/`[SENS]`
~1 Hz). With a bound transmitter you'll see `[SBUS] OK ... chN=... fs=0 lost=0`; the local
screen shows the RC page and the PRISM reflects the RC-derived state.
