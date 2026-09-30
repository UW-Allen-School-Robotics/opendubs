# Operating the robot

How to power the robot on, make it move, and charge it. This is the operator's page — for why
things are built this way see [Control authority](control-authority.md),
[Power architecture](power-architecture.md) and [E-stop system](e-stop.md).

!!! note "Two items outstanding"
    The **RUN** button's location and the robot's **contactor auto-arm** behaviour are both marked
    *TODO* below and need confirming at the robot. Everything else on this page is verified against
    the firmware and the wiring.

## The controls

| Control | Where | What it does |
|---|---|---|
| **BATTERY** | latching button, **on the battery itself** | connects the pack. The robot is electrically dead with this off |
| **RUN** (logic) | latching button, *TODO — location* | master switch for the **logic bus** — the Thor, the StamPLC, the router, the sensors |
| **E-STOP** | two, **rear of the robot, left and right** | drops the drive contactor. Cuts motion, not the brain |
| **CH5** | transmitter, 3-position | **authority**: `OFF` / `MANUAL` / `AUTO` — who may command |
| **CH6** | transmitter | **deadman** — held means a human is deliberately engaging |
| **CH7** | transmitter, 3-position | **what the right stick talks to**: `OFF` / `Z-AXIS` / `PAN-TILT` |
| **CH8** | transmitter, knob | speed scale |

*TODO photo: the rear of the robot showing both emergency-stop buttons, and the battery with its
latching switch.*

The logic bus is **always on** once RUN is on — it is deliberately *not* behind the e-stop, so the
robot keeps thinking, reporting and showing state while its actuators are dead.

## Powering on

1. **CH5 to `OFF`** before anything else. Authority off means nothing can drive, whatever else is
   true.
2. **BATTERY on.**
3. **RUN on.** The StamPLC screen and the Tab5 come up; the Thor takes longer.
4. **Open the dashboard** from the tablet at `http://<robot>:8080`. The robot has no screen of its
   own — the tablet is the interface.
5. **Confirm both e-stops are released** and that the dashboard reports the contactor closed.

*TODO: the robot carries a contactor auto-arm behaviour that is not in the repo. Confirm what it
does at power-on and settle whether it stays — until then, the contactor state at boot may differ
from what this page describes.*

## Making it move

Every one of these must hold, and the firmware re-checks all of them every tick:

- **CH5 in `MANUAL`** (or `AUTO` with a live command stream from the Thor)
- **the drive contactor closed** — no e-stop pressed, and no software e-stop latched
- **the RC link alive**, not in failsafe, and its channels in range
- **CH6 (deadman) held** — the sticks do nothing without it

Then: **hold the sticks at neutral for two seconds.** The ESCs arm on two seconds of neutral with
the gate satisfied, not the instant the gate is met. If any condition drops out the arming is lost
immediately and the two seconds start again.

The arm gate and the neutral hold are re-evaluated continuously, so an intermittent RC link or a
momentarily released deadman will keep the drive disarmed even though every switch looks correct.

## Z-axis pinch protection

The Z-axis arrests its own descent short of the bottom, where the closing gap is a pinch hazard.

**What you will see:** the lift stops on its own, the Tab5 shows an amber **Z-AXIS HELD** banner
reading *release, then press down again*, and two short tones sound.

**To continue:** release the stick, then command DOWN again. The release must be held for roughly
half a second — the guard requires a deliberate new request rather than stick bounce. The banner
turns green and reads **LOWERING** once the descent resumes.

This is a guard, not a fault: nothing latches and nothing needs clearing. It will arrest the
descent again on the next approach to the bottom.

!!! note "Why a second request is required"
    Resuming on the same continuous stick input would make the guard invisible: the platform would
    pause and carry on, conveying nothing. Requiring a fresh request makes closing the gap an
    explicit operator decision.

## Charging

!!! danger "The BATTERY switch must be on while the charger is connected"
    With the BATTERY switch **off** and RUN **on**, the charger's output reaches the **logic bus**
    through the logic converter with no pack in the circuit. A charger is a current-limited
    constant-voltage source, not a bench supply: it cannot regulate against the logic bus's load,
    so its output pulses and the electronics cycle.

    **RUN may be on or off.** Charging with the robot running is fine, and the charger-present
    interlock inhibits drive while it is plugged in. The requirement is only that the pack is in
    the circuit.

1. **BATTERY on.**
2. Set the charger to **48 V**. Any of its current settings will charge the pack; **8 A** is the
   recommended one.
3. Plug into the charge port.
4. Charge time depends on depletion.
5. **Unplug before driving.** Charger-present inhibits drive by design, so the robot will refuse to
   move while tethered — treat that as a backstop, not as the procedure.

Charger: <https://a.co/d/0aP9q4YX>

## Powering off

1. **CH5 to `OFF`.**
2. Lower the Z-axis, or leave it at a height from which it cannot fall.
3. **RUN off**, then **BATTERY off.**

Cutting power while the robot is in motion has previously disturbed the StamPLC and the RC link. A
defined bring-down sequence — command neutral, disarm, open the contactor, park the Z-axis, then
bring down the compute — is specified but not yet implemented.

## When something is wrong

| Symptom | Look here first |
|---|---|
| Nothing powers up | BATTERY, then RUN |
| Dashboard unreachable | the robot and the tablet on the same network; the Thor takes the longest to boot |
| Sticks do nothing | CH5 not in `MANUAL`; CH6 not held; the two-second neutral hold not yet elapsed |
| Drive dead, brain alive | an e-stop is pressed or software-latched — the contactor is open |
| Lift stops near the bottom | expected behaviour — see *Z-axis pinch protection* |
| Drive refused with no other fault | the charger is still connected |
| Electronics cycling while charging | the BATTERY switch is off — see *Charging* |
