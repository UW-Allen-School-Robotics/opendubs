# Operating the robot

How to power the robot on, make it move, and charge it. This is the operator's page — for why
things are built this way see [Control authority](control-authority.md),
[Power architecture](power-architecture.md) and [E-stop system](e-stop.md).

!!! warning "Stub — verify against your robot before trusting it"
    This page is being written from the firmware and the wiring rather than from a photographed
    control panel. The sequences and the charging rule are right; the switch **locations** and a
    few numbers are marked *TODO*. Fix them as you use it.

## The controls

| Control | Where | What it does |
|---|---|---|
| **BATTERY** | latching button, *TODO — location* | connects the pack. The robot is electrically dead with this off |
| **RUN** (logic) | latching button, *TODO — location* | master switch for the **logic bus** — the Thor, the StamPLC, the router, the sensors |
| **E-STOP** | mushroom button, *TODO — how many and where* | drops the drive contactor. Cuts motion, not the brain |
| **CH5** | transmitter, 3-position | **authority**: `OFF` / `MANUAL` / `AUTO` — who may command |
| **CH6** | transmitter | **deadman** — held means a human is deliberately engaging |
| **CH7** | transmitter, 3-position | **what the right stick talks to**: `OFF` / `Z-AXIS` / `PAN-TILT` |
| **CH8** | transmitter, knob | speed scale |

The logic bus is **always on** once RUN is on — it is deliberately *not* behind the e-stop, so the
robot keeps thinking, reporting and showing state while its actuators are dead.

## Powering on

1. **CH5 to `OFF`** before anything else. Authority off means nothing can drive, whatever else is
   true.
2. **BATTERY on.**
3. **RUN on.** The StamPLC screen and the Tab5 come up; the Thor takes longer.
4. **Open the dashboard** from the tablet at `http://<robot>:8080`. The robot has no screen of its
   own — the tablet is the interface.
5. **Check the e-stop is released** and that the dashboard shows the contactor closed.

## Making it move

Every one of these must hold, and the firmware re-checks all of them every tick:

- **CH5 in `MANUAL`** (or `AUTO` with a live command stream from the Thor)
- **the drive contactor closed** — no e-stop pressed, and no software e-stop latched
- **the RC link alive**, not in failsafe, and its channels in range
- **CH6 (deadman) held** — the sticks do nothing without it

Then: **hold the sticks at neutral for two seconds.** The ESCs arm on two seconds of neutral with
the gate satisfied, not the instant the gate is met. If any condition drops out the arming is lost
immediately and the two seconds start again.

That delay is the answer to "I have authority and the deadman and it still will not go" — wait,
with the sticks centred.

## Z-axis pinch protection

The Z-axis stops itself before the platform reaches the bottom, because the gap down there closes
on fingers.

**What you will see:** the lift stops on its own, the Tab5 shows an amber **Z-AXIS HELD** banner
reading *release, then press down again*, and two short tones sound.

**What to do:** let go of the stick, then ask DOWN again. A brief release is not enough — hold the
release for about half a second so the guard sees a deliberate new request rather than a bounce.
The banner turns green and reads **LOWERING** when the descent resumes.

This is a guard, not a fault. Nothing needs clearing and nothing is latched. It will hold again
the next time you approach the bottom, which is the intended behaviour.

!!! note "Why it asks twice"
    Stopping and then continuing on the same stick input would make the guard invisible — the
    platform would pause and keep going, and nobody would learn the gap was there. Requiring a
    fresh request means the operator has to agree to close it.

## Charging

!!! danger "BATTERY on · RUN off — in that combination only"
    **BATTERY must be ON and RUN must be OFF to charge.**

    Charging with **BATTERY off and RUN on** points the charger straight at the **logic bus**
    through the logic converter, with no pack in the path to absorb it. A charger is not a bench
    supply: it cannot hold up the logic bus, so it **pulses**, and the electronics brown out and
    cycle on it. Avoid it.

1. **RUN off.**
2. **BATTERY on** — leave it on. This is the one thing people get wrong, because "off" feels safer.
3. Plug the charger into the charge port.
4. **Unplug the charger before driving.** Charger-present inhibits drive by design, so the robot
   will refuse to move while tethered — but do not rely on the interlock as a substitute for
   unplugging.

*TODO: charger model, charge current, and how long a full charge takes.*

## Powering off

1. **CH5 to `OFF`.**
2. Lower the Z-axis, or leave it somewhere it cannot fall to.
3. **RUN off**, then **BATTERY off.**

Pulling power on a moving robot is how the StamPLC and the RC link have been upset in the past. An
orderly bring-down sequence is still to be designed — see the punch list.

## When something is wrong

| Symptom | Look here first |
|---|---|
| Nothing powers up | BATTERY, then RUN |
| Dashboard unreachable | the robot and the tablet on the same network; the Thor takes the longest to boot |
| Sticks do nothing | CH5 not in `MANUAL`; CH6 not held; the two-second neutral hold not yet elapsed |
| Drive dead, brain alive | an e-stop is pressed or software-latched — the contactor is open |
| Lift stops near the bottom | working as intended — see *Z-axis pinch protection* |
| Robot refuses to drive, everything else fine | charger still plugged in |
