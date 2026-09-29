# PWM and ESC Safety

This page captures safety constraints and required fallback behavior for PWM
output to drive and mast ESCs.

## The hardware

The drive ESCs are the **[goBILDA 2×80A ESC](https://www.servocity.com/content/user_manuals/3105-0202-0080_data-sheet.pdf)**
(SKU `3105-0202-0080`), driven by PWM from the **[M5 8Servo unit](https://docs.m5stack.com/en/unit/8Servos%20Unit)**
(I²C `0x25`) commanded by the StamPLC. Both are wired together in the
**[Electronics Bay](build/electronics-bay.md)**.

## Known ESC Signal Constraints

From the goBILDA 2×80A ESC datasheet (linked above):

- Nominal center deadzone: `1500 +/- 10 us`
- Valid signal input range: `500..2500 us`
- Arm zone: `1400..1600 us`

## Mandatory Safety Defaults

- On deadman release: command drive outputs to `1500 us` (hard neutral)
- On stale RC link / frame timeout: command drive outputs to `1500 us`
- On failsafe-asserted receiver frame: command drive outputs to `1500 us`
- On robot-level e-stop latch: command all motion channels to safe neutral/off

## Safety Requirements (Draft IDs)

- `SAFE-PWM-001`: clamp all outgoing drive PWM commands to configured valid bounds
- `SAFE-PWM-002`: neutral fallback within one control cycle after deadman drop
- `SAFE-PWM-003`: neutral fallback on stale-frame threshold breach
- `SAFE-PWM-004`: reject autonomous or RC command when subsystem not enabled
- `SAFE-PWM-005`: on transition to disallowed state, write neutral before mode switch completes

## Arming/Disarming Policy

Current status:
- Arming/disarming timing windows are not yet bench-validated.
- The implementation must preserve placeholders and block final lock-in until tests pass.

Bench-validation checklist (required before finalizing timings):

1. Measure ESC response to repeated neutral pulses at startup.
2. Determine minimum stable arm hold duration at neutral/arm zone.
3. Verify disarm behavior for abrupt signal loss and brownout-like events.
4. Validate neutral command propagation latency end-to-end.
5. Record oscilloscope captures and test conditions.

## Power Failure Considerations

- Document behavior when StamPLC resets while ESC power remains on.
- Document behavior when ESC power drops while controller remains active.
- Define safe re-entry sequence after recovery.

## Related Topics

- [Electronics Bay](build/electronics-bay.md) — where the ESCs + 8Servo are wired together
- [Drive bring-up (channel → ESC → wheel)](drive-bringup.md)
- [Control Authority and Modes](control-authority.md)
- [Robot and Subsystem State Machines](state-machines.md)
- Telemetry specification (archived — see firmware README for the current tags)
