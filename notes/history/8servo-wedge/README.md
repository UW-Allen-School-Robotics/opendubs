# 8Servo wedge — investigation record (2026-09-01)

**Symptom.** During driving, the M5Stack 8Servo Unit (STM32F030F4, I²C slave `0x25` on PaHub
ch1) stops servicing its I²C interface. Writes stop being accepted at ~80% rate (`s8fail`
climbs from 0 to 100–175/s), and the unit's PWM outputs **freeze at their last value**. All
four goBILDA 2x80A ESCs then show solid red — per the datasheet a *live* "no/invalid signal"
status, not a latched fault — because they are being fed a frozen, invalid pulse.

The unit still **ACKs its address** while wedged: the hardware is alive, the firmware is not
servicing.

## What was ruled out

| Hypothesis | How it was killed |
|---|---|
| Regen / braking / mechanical load | Wedged with the robot off the ground |
| Drive-bus state (contactor R0) | Correlation is *opposite* — every wedge had R0 **closed**; the one bus-down period had **0.0/s** failures |
| The 8Servo unit itself | Swapped the physical unit — still wedges |
| I²C bus or PaHub mux | Pinpoint (`0x31`, same PaHub) is unaffected throughout |
| Voltage / current / temperature | Flat across every event — 24.33 V, 0.125 A, 35.9 °C, no excursion |
| I²C traffic volume | Constant 200 writes/s regardless of speed (`driveTick` writes all 4 channels unconditionally) |
| Command pattern — sign flips, magnitude, direction, slew | Output was **byte-identical and static** for seconds before multiple wedges |
| ESC powered + valid signal, on its own | **305 s clean at full ±500 µs with no motor attached** |

## The discriminating result

Three configurations, same firmware, same commands:

| Configuration | Result |
|---|---|
| ESCs connected + motors running | Wedges — 109 s, 229 s, repeatedly |
| ESC signal disconnected | 265 s clean |
| ESC powered + signal connected, **no motor** | **305 s clean**, full command range |
| Motor reconnected (one motor, one channel) | **Wedged again**, at speed |

**Motor current is necessary.** The ESC being powered and switching against a valid pulse
train is not sufficient. This is the strongest constraint we have.

Two mechanisms remain, both requiring motor current, and this test does **not** separate them:

1. **Conducted** — motor return current across shared ground impedance develops an offset that
   injects into the 8Servo (via its I/O protection diodes or its 5 V rail).
2. **Radiated** — motor phase switching couples into the 8Servo or its signal wiring.

## Next steps

- **Scope StampPLC GND against ESC signal GND while driving.** Highest-value measurement:
  it reads the conducted path directly. Transients near or above a diode drop confirm
  injection; millivolts kills the ground theory outright.
- **Measure the 8Servo's 5 V Grove rail** — a standing blind spot; it has never been
  observed during a wedge.
- **Reroute the motor phase leads** away from the 8Servo and its signal wiring. Cheap
  discriminator: rate drops ⇒ radiated; unchanged ⇒ conducted.
- Star-ground candidates under consideration: the regen clamp, the drive-side DC/DC, and the
  logic-side DC/DC (the only break in the StampPLC/I²C ground).

## Why the ESC fault survives an e-stop

Not latched memory. E-stop opens the drive-bus contactor but does **not** cut the 8Servo,
which is fed from the StampPLC's 5 V on the logic side. The 8Servo stays hung and keeps
emitting its frozen pulse; the ESC repowers, sees an invalid signal, and re-reports.

*Testable prediction (unverified):* e-stop plus a power-cycle of the 8Servo alone should
clear the ESCs.

## Logs

- `logs/events-2026-09-01-all.log` — every `WEDGE-START` / `WEDGE-CLEARED` marker of the day.
  Note two self-clears, so the wedge is **not** strictly power-cycle-only.
- `logs/wedge-2026-09-01T1711-motor-reconnected.log` — ±25 s around the final event
  (`1788307896.604`), captured with the motor reconnected.

The recorder is `/opt/opendubs/telemetry-log.py`
(`opendubs-telemetry-log.service`), capturing UDP:5005 to `/opt/opendubs/logs/`. It flags
`WEDGE-START` above 50 failures/s and `WEDGE-CLEARED` at or below 5/s.

### The final event, annotated

Output held **byte-identical for 2.0 s** at full reverse before the wedge — no command
change, no slew, no sign flip:

```
1788307894.554 fwd=-0.87 out=1935,1935,1071,1942 s8fail=1     <- static from here
1788307896.491 fwd=-0.87 out=1935,1935,1071,1942 s8fail=1     <- last healthy
1788307896.604 fwd=-0.87 out=1935,1935,1071,1942 s8fail=12    <- WEDGE-START, 97/s
1788307897.524 fwd=-0.87 out=1935,1935,1071,1942 s8fail=160   <- ~175/s
```

Bus voltage over the same window: `24.32 → 24.33 V`. Socket current `0.125 A`. Temp `35.9 °C`.
Nothing on the logic side moves.

(`out[2]=1071` against ~1935 elsewhere is expected — `kDriveInvert = {-1,-1,1,-1}` puts RL
opposite the other three.)

## Open item

The motor was unplugged for the no-motor test and reconnected afterwards. **Its phase order
has not been verified.** Drive that wheel alone and confirm its direction matches its
`kDriveInvert` sign before trusting any further drive testing.

---

## 2026-09-02 — scope measurement: it is electrical

Measured with a Rigol DS1054Z driven over LAN (SCPI on `10.42.2.11:5555`), Peak Detect,
6 M memory, 20 ms/div. **CH1** = 8Servo GND → regen clamp GND (the suspect path).
**CH2** = 8Servo 5 V rail, referenced to 8Servo GND (never measured before this).

| Condition | CH1 ground shift | CH2 5 V rail |
|---|---|---|
| Probe tip shorted to its own ground (**artifact floor**) | 0.22 V | — |
| Robot at neutral | 0.61 V | 0.28 V |
| **Driving** | **>1.6 V (clipped)** | **>0.8 V (clipped)** |

Both channels hit Rigol's out-of-range sentinel (`9.9E37`) under drive, so those are
**lower bounds**, not values. Rescale to CH1 1 V/div and CH2 500 mV/div to get real numbers.

The threshold that matters is ~0.7 V — where the STM32F030's ESD protection diodes begin
conducting, injecting current into the die. The ground shift is at least double it, and the
5 V rail is moving far enough that the LDO feeding the MCU's 3V3 cannot reject it at those
speeds.

**Always run the shorted-tip control.** A long probe ground clip fabricated 0.22 V of the
reading here; without that control the neutral figure looks alarming when most of it is loop
pickup.

### Software work, in retrospect

Two genuine defects found and fixed, neither of which was the cause:

- `writeRegister()` is a short-circuiting `&&` chain — a failed step skips `endTransaction()`,
  so no STOP is issued and the slave is abandoned mid-transaction. Fixed via `servo8Write()`.
- `scanID()` has the identical defect (`start() && stop()`), used by the 5 s presence scan that
  probes 0x25. Fixed, and the scan is now skipped while the drive is armed.

Both shipped; wedges continued. Bus recovery (9 clocks + STOP + port re-init) executes and
never revives the part, while Pinpoint reads cleanly on the same bus throughout — so the
8Servo's MCU hangs at firmware level, beyond anything the master can reach.

### The dose-response assay (still valid, but it measures noise, not wedges)

Isolated non-wedged write failures vs commanded output, across all sessions:

```
|out-1500|   seconds   fails   fails/min
0-25           5830s       0       0.00
100-200         578s       3       0.31
300-400          87s       3       2.07
400-600         208s      10       2.89
```

Zero failures in 97 minutes of identical I²C traffic at neutral. Useful as a before/after
metric for mitigations — but note a wedge was later observed at dead neutral
(`out=1500,1500,1500,1500`), so the trickle and the wedge are not the same phenomenon.

### Next

Bond logic ground to the **regen clamp ground junction** — the node the four ESC grounds
share — not the PDH. That single clamp→PDH conductor carries all four motors' return current
while driving; the 8Servo currently bridges across it. Shorten and fatten that conductor too.

Re-measure CH1 after bonding. The number coming down is the test.
