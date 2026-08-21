# Drive Bring-up: Verify Channel → ESC → Wheel & Direction

Before you bolt the wheels on, you must confirm that each 8Servo output channel
drives the ESC — and therefore the wheel — you *think* it does, spinning in the
direction you *think* it does. The wiring from the 8Servo to the four ESCs, and
from each ESC to its motor, is easy to cross or reverse, and a single swapped
pair makes a mecanum base translate diagonally or spin in place instead of
driving straight. This page is the diagnostic that catches it on the bench
instead of on the floor.

Do this with the **wheels removed** so the hubs can spin freely and a wrong
mapping can't move the robot.

## Prerequisites

- StampPLC flashed with the drive firmware and connected over USB
  (see [Flash a StampPLC](flashing-stamplc.md)).
- The M5Unit-8Servo wired to the StampPLC, and its four ESC signal leads
  connected to the drive ESCs.
- Drive 24 V available through the e-stop contactor, and a way to open it
  quickly (physical e-stop) if a hub runs away.
- **Wheels off.** Hubs free to spin.
- A serial terminal on the StampPLC's USB port (115200 baud).

## The single-channel test command

The firmware accepts a host command that writes one raw pulse width, in
microseconds, straight to one 8Servo channel, bypassing the RC mixer and the
arming gate:

```
SERVO8 <ch> <pulse_us>
```

- `<ch>` is the 8Servo output channel, `0`–`7`.
- `<pulse_us>` is the ESC pulse width. `1500` is neutral (stopped);
  values above/below drive one direction/the other.

Keep the drive-enable gate (`DRIVE_ENABLE`) **off** while you use `SERVO8` for
this test if your build lets the mixer fight the manual command; the bench
mapping below was taken with the mixer idle so only `SERVO8` moves a hub.

## Procedure

1. **Neutral first.** With 24 V up, send `SERVO8 0 1500`, `SERVO8 1 1500`, … for
   all four drive channels and confirm every hub is stopped and every ESC shows
   its *ready* state (see [ESC arming](#esc-arming-and-clearing-a-fault) below —
   a healthy armed ESC on this build sits solid blue at neutral). If an ESC is
   not ready, fix that before commanding any motion.

2. **One channel, gently.** Pick one channel and send a *small* offset from
   neutral — e.g. `SERVO8 0 1600` (≈ +100 µs). Note **which hub turns** and
   **which way** it turns as seen from the robot (does that wheel drive the
   robot forward or backward?). Return to neutral: `SERVO8 0 1500`.

3. **Record it.** Write down: `8Servo ch N → <which physical wheel> →
   <forward | reversed> for a +offset`.

4. **Repeat** for all four channels, one at a time. Never command two at once
   during mapping — you want each hub attributable to exactly one channel.

5. **Build the two tables** the firmware needs:
   - **Channel map** `kDriveCh[4]`, indexed `[FL, FR, RL, RR]`: which 8Servo
     channel drives each corner.
   - **Direction sign** `kDriveInvert[4]`, indexed `[FL, FR, RL, RR]`: `+1` if a
     positive command drives that wheel robot-forward, `-1` if it's wired
     reversed and needs flipping.

6. **Set them in firmware**, re-flash, and re-verify: with the mixer live and
   the base armed, a pure forward command must spin **all four** hubs
   robot-forward. A pure rotate command must spin the left corners one way and
   the right corners the other. If any corner is wrong, fix its entry in
   `kDriveCh`/`kDriveInvert` — do not rewire to compensate, or you'll desync the
   firmware map from the hardware.

### Worked example (this reference build)

Your wiring will differ — treat this as the *shape* of the answer, not the
answer. On the reference base, mapping came out:

| Corner | `kDriveCh` (8Servo) | Direction |
|--------|--------------------|-----------|
| FL     | 1                  | `+1`      |
| FR     | 3                  | `+1`      |
| RL     | 0                  | `+1`      |
| RR     | 2                  | `-1` (wired reversed) |

→ `kDriveCh[4] = {1, 3, 0, 2}`, `kDriveInvert[4] = {1, 1, 1, -1}`.

Note channel **0**: the PRISM LED is on I2C, so **ch0 is a drive ESC** — make sure
nothing else writes ch0.

## ESC arming and clearing a fault

Two behaviors on this build's ESCs are worth knowing before and during the test:

- **Arming.** An ESC arms by seeing a steady **neutral** pulse stream *as its
  drive power comes up*. Bring 24 V up while the firmware is already holding
  1500 µs on all channels and the ESCs arm cleanly (solid blue on this build).
  If a stick is deflected — or a channel is commanding throttle — at the instant
  power arrives, the ESC may refuse to arm and sit faulted.

- **Clearing a latched fault.** If an ESC trips (all LEDs red on this build),
  toggling the 24 V contactor (relay R0) alone **does not** clear it. The ESC's
  logic stays alive on the ~5 V signal rail, so the drive bus only sags to
  ~5 V and plateaus — the controller never fully de-powers and the latch holds.
  To clear it, **drop the ESC signal leads** (or otherwise remove that 5 V) so
  the controllers lose all power, then bring 24 V back up while they read
  neutral, per the arming rule above.

Keep commanded offsets small during bring-up. Four ESCs spun up together draw
more inrush than one at a time, and an over-wide command band was enough to trip
these controllers even with the hubs unloaded — see
[PWM and ESC Safety](pwm-esc-safety.md).

## Related Topics

- [Flash a StampPLC](flashing-stamplc.md)
- [PWM and ESC Safety](pwm-esc-safety.md)
- [Coordinate frames & naming](naming-frames.md) — where FL/FR/RL/RR come from
- [Control Authority and Modes](control-authority.md)
