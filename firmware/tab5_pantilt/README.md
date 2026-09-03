# Tab5 pan/tilt bring-up

A standalone serial-driven tool for bringing up the PTZ head: the 8Servo, the ToF
rangefinder, and the two goBILDA servos. It deliberately does **not** touch
`firmware/tab5_face` — reflash that project to restore the face.

**Status:** tilt is characterised and working. Pan is blocked on a servo mode change
(see [Pan is in the wrong mode](#pan-is-in-the-wrong-mode)). Camera path is validated
but unimplemented.

## Hardware topology

```
Tab5  Grove Port A (G53 SDA / G54 SCL, 100 kHz)
  └── PaHub  0x70
        ├── ch4 ── 8Servo 0x25 ── out ch0 = TILT
        │                         out ch1 = PAN
        └── ch5 ── VL53L0X 0x29   (ToF rangefinder; ID reg 0xC0 reads EE AA 10)
```

`scan()` locates the 8Servo automatically and selects its channel. Every ToF read
swings the mux to ch5 and back, because the 50 Hz servo tick assumes it owns the
mux — see `tofOnChannel()`.

## Read this before powering anything

**Servo current must not come from the Grove cable.** With nothing on the 8Servo's
terminal block, its servo rail `VM` is fed from `VCC_5V` = `BUS_5V` = the Grove 5 V =
the Tab5's USB rail. We ran the whole bring-up that way and **destroyed a Tab5's
peripheral 5 V output** doing it — the symptom was a ToF power LED that barely lit,
both I²C lines idling LOW, and an empty bus scan while the Tab5 itself booted and ran
normally.

Feed the 8Servo's terminal block instead. Use the **HV** pin (9–24 V), not the 5 V pin:
with HV present the unit's Q1/Q3 P-FETs turn *off*, isolating `BUS_5V` from `VCC_5V`,
so the onboard buck supplies the servos while the MCU keeps running on Grove 5 V at a
few mA. Feeding the 5 V pin instead leaves those FETs conducting and back-feeds your
supply into the Tab5.

The boot-time `[BUS]` line reports the idle line levels before `Wire` claims the pins.
**Both HIGH is healthy.** Both LOW means no powered pull-ups — cable, PaHub power, or a
dead rail. That one line distinguishes a hardware fault from a driver fault, which
otherwise look identical from software.

## Safety model

Everything defaults toward *not moving*:

- Boot leaves all 8Servo pins in `DIGITAL_INPUT_MODE`. A pin not in `SERVO_CTL` emits
  no pulse, so servos are idle until explicitly enabled.
- `EN` starts a channel at 1500 µs unless told otherwise. That is the safe first
  command in **both** servo modes — centre in positional, stop in continuous.
- Targets clamp to `[LIM lo, hi]`, default a deliberately tight 1400–1600.
- Commands slew at `SLEW` µs per 20 ms tick, so a large `GO` ramps instead of slamming.
  This bounds the *command*, not the servo's own speed — the first `EN` can still move
  a horn from wherever it sits.
- Servo-rail current is **checked at 25 Hz** and the channel is cut above `TRIP` amps.
  40 ms matters: 200 ms of stall current is enough to brown out a USB rail.
- Any I²C write failure disables that channel.

`TRIP` starts **disarmed** (0). The 8Servo's current register units aren't documented,
so read `CUR` while idle first — it should be ~0.007 A, which tells you the scale is
amps — then set a real threshold. 0.8 A was our working value.

## Commands

Serial, 115200, newline-terminated.

| Command | Effect |
|---|---|
| `SCAN` | Bus scan incl. every PaHub channel; auto-selects the 8Servo's channel |
| `CH <n>` | Force the active mux channel |
| `CUR` | Read servo-rail current (amps) |
| `EN <ch> [us]` | Enable a channel, starting at `us` (default 1500) |
| `DIS <ch>` / `STOP` | Disable one / all channels |
| `GO <ch> <us>` | Set target pulse (clamped, slewed) |
| `LIM <lo> <hi>` | Clamp window |
| `SLEW <us>` | Slew rate per 20 ms tick |
| `TRIP <amps>` | Over-current cutoff; 0 disarms |
| `TOF [n]` | Read the VL53L0X |
| `RD <addr> <reg> <n>` | Raw register read (hex) |
| `SVPING` | Sweep servo IDs 0–253 over the programming link |
| `SVMODE POS\|CONT CONFIRM` | Reprogram a dual-mode servo (writes EEPROM) |
| `STAT` | Current state |

## Tilt — characterised, working

Positional mode, confirmed: commanded to a value it settles to ~0.01 A and stays
there. Measured by creeping in small steps and watching for a sharp current rise,
soft-stopping at 0.20 A so it never actually stalled.

```
 920 µs   full up        ~90° (slightly past vertical)
1545 µs   HORIZONTAL     home / nominal
1650 µs   full down      hard stop measured at 1680 µs
7.14 µs per degree       (280° over 500–2500 µs)
```

Two independent checks agree on 1545: the geometry from the user's straight-up
observation at 900 µs, and the holding current, which peaks at 0.054–0.079 A there
versus near-idle elsewhere — maximum gravitational moment is exactly what you expect
at horizontal.

**These are not yet compile-time constants.** `LIM` resets to 1400–1600 on every boot,
so a future flash could drive the head into its 1680 stop. Baking them in is the first
thing the next person should do.

## Pan is in the wrong mode

Pan is in **continuous rotation** mode: commanded off 1500 it draws a steady
0.10–0.20 A indefinitely and never settles, and it rotates well past 300°, which a
280° positional servo physically cannot do. There is no way to command a pan *angle*
in this state.

**This blocks the PTZ head, and there is an umbilical** — nothing in continuous mode
stops at 270°, so it will keep winding until something tears.

### Fixing it

The sanctioned tool is the **goBILDA 3102 Dual Mode Servo Programmer**. Mode is set by
the press duration of its `P` key (sources disagree on the exact procedure; the
programmer's own documentation wins).

We also implemented the mode change in firmware, and it **did not work**. Recorded here
so the next person doesn't rediscover it from scratch:

The servos speak a Feetech/Dynamixel-style protocol on the **signal wire** at
**76800 baud, 8N1, half-duplex**:

```
FF FF <ID> <LEN> <INST> <params...> <checksum>      checksum = ~(sum of ID..params) & 0xFF

FF FF FE 02 01 FE           broadcast ping
FF FF 01 04 02 15 01 E2     read addr 21
FF FF 01 04 03 34 00 C3     write addr 52 = 0    unlock EEPROM
FF FF 01 2F 03 06 <44 B>    the config block     <-- the ONLY difference between modes
FF FF 01 04 03 34 01 C2     write addr 52 = 1    lock / save
```

Both config blocks are in `main.cpp` as `kSvCont` / `kSvPos`, and the checksums were
independently re-derived and verified. Protocol credit: Asaf / FRC 5951 on Chief Delphi.

Wiring is `kProgPin` (G49) to the servo's signal pin, ground common, servo powered
normally. The 8Servo channel must stay in input mode or it fights the line.

**Why it probably failed** — untested, in order of likelihood:

1. **The packets address servo ID 1.** If this servo isn't ID 1 the writes are silently
   ignored. `SVPING` was written to sweep all 254 IDs but **was never run** — that needs
   a second jumper from **G50** to the same signal wire so replies can be read. Do this
   first; it's a 30-second test that either confirms the whole approach or kills it.
2. **3.3 V drive.** The Tab5's GPIO may not swing the line hard enough for a servo that
   expects a 5 V programmer.

## Camera

The Tab5's camera is an **SC2356, 2 MP (1600×1200), MIPI-CSI**. There is no Arduino
support for P4 CSI — it's [an open feature request](https://github.com/espressif/arduino-esp32/issues/11695).

`firmware/tab5_idf_probe` exists solely to prove the way around this, and it **builds
successfully**: PlatformIO's dual framework gives you ESP-IDF alongside Arduino, so
`esp_cam_ctlr.h`, `esp_cam_ctlr_csi.h` and `driver/isp.h` are all reachable while
M5Unified and the existing face code keep working. No `idf.py` install needed.

```ini
framework = arduino, espidf
```
```
CONFIG_FREERTOS_HZ=1000      # arduino-as-component requires this; the build fails without it
CONFIG_AUTOSTART_ARDUINO=y
```

Still unproven: the **SC2356 sensor driver itself**. The controller and ISP are
available, but the sensor needs its own init and register set — likely from M5Stack's
or `espp`'s Tab5 board component.

## IMU

The Tab5 has a **BMI270** (6-axis), reachable via `M5.Imu` in M5Unified. It reads fine —
during a brief experiment it reported valid, stable accelerometer data.

Because the Tab5 *is* the head, its accelerometer measures head orientation directly.
That makes it genuinely useful for tilt: absolute pitch from gravity would let you
verify 1545 rather than trust it, auto-home, and detect a slipped spline (a 25-tooth
spline with no index shifts by 14.4° and silently invalidates every stored limit).
It does **nothing for pan** — gravity gives no yaw reference and there's no
magnetometer.

The IMU is not in this firmware. It was added, appeared to break the external I²C bus,
and was reverted — but that diagnosis was wrong: the bus stayed dead *after* the revert,
because the Tab5's 5 V rail was already failing. When re-adding it, route all external
bus access through **`M5.Ex_I2C`** rather than raw `Wire`, which is the sanctioned API
and avoids fighting `M5.begin()` over the I²C ports.

## Next steps

1. Run `SVPING` (needs the G50 jumper) — settles the servo-programming approach either way
2. Get pan into positional mode, by programmer or protocol
3. Bake the tilt limits in as compile-time constants
4. Characterise pan's range the same way tilt was done
5. Re-add the IMU via `M5.Ex_I2C`
6. Merge into `tab5_face` as a dual-framework project, then the SC2356 driver
