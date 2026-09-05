# Scissor Lift Module

!!! warning "Stub — this module is being built now"
    The wiring and firmware are in progress. Structure, interfaces and the
    bring-up procedure are written down; step photos, the CAD link, and the
    parts list are still to come. Nothing here has been verified on a
    finished robot yet.

!!! info "At a glance"
    | | |
    |---|---|
    | **You'll build** | A powered Z-axis: a commercial scissor lift driven through its own controller, commanded by two StampPLC relays and measured by a time-of-flight sensor |
    | **Time** | *TODO* |
    | **Difficulty** | Intermediate (mains-free, but 24 V motor wiring and a commissioning step) |
    | **Prerequisites** | [Power Bay](power-bay.md) for drive-bus 24 V; a StampPLC running the drive firmware |
    | **Mates to** | Drive bus 24 V; StampPLC relays R2/R3 and the I²C (Grove) bus |
    | **Optional?** | Yes — one of two Z-axis options, alongside a lead-screw mast |

## What this is

The lift raises and lowers whatever the robot carries. It is a **commercial scissor
lift with its own motor controller** — the robot does not drive the motor directly.
Instead, the StampPLC's relays stand in for the controller's up/down buttons, and a
downward-looking ToF sensor tells the robot how high it actually is.

That division matters: the lift's own controller keeps its internal limits and motor
protection whatever the robot does. The robot's job is only to press its buttons and
know the height.

## Safety — the lift is a pinch point

!!! danger "The bottom of the travel is the dangerous part"
    A scissor linkage can trap a hand anywhere in its travel, but everywhere except the
    bottom you have to put a limb **into** the mechanism to be caught. Near full-down
    that is no longer true: the platform simply closes on whatever happens to be under
    it — a hand resting on the base, a foot, a routed cable. Nothing has to reach in.

    That is why the last few centimetres of descent get their own treatment below.

Two mitigations are built into the firmware:

1. **The lights say when the Z-axis is moving**, so a bystander who is not the operator
   gets a cue. The lift can move while the robot is otherwise still and silent, which is
   exactly when there is no other warning.
2. **Descent stops itself just above full-down** and will not continue without a fresh,
   deliberate act from a human.

!!! warning "Neither of these is a safety-rated interlock"
    They are firmware behaviours on a research robot, and they depend on a ToF reading
    being available and on the lift controller doing what we asked. **The e-stop and
    keeping clear are the actual protection.** Treat the pinch stop as a courtesy that
    catches the ordinary mistake, not as a guarantee that the lift cannot close on you.

### What the LEDs do

The PRISM overlay is shared with the turn-signals, and the Z-axis outranks them — it
takes the **whole strip**, because the pinch hazard has no side: someone standing behind
the robot is as exposed as the operator.

| Z-axis | PRISM |
|---|---|
| Rising | Whole strip amber, slow blink |
| Lowering | Whole strip amber, **fast** blink — descent is the direction that closes the gap |
| Held at the pinch stop | Whole strip **red strobe** — waiting for the operator |
| Stopped | Turn-signals and the state colour as usual |

The lights follow what the driver *believes* the lift is doing. With a ToF fitted, that
belief is corrected within about a second when a pulse is missed. **Without one it can be
wrong**, and the lights will be wrong with it.

### The pinch stop

Descending below the threshold requires consent, given one of two ways:

- **Operator** — release the stick to centre, hold it there for the dwell (default 600 ms),
  then command down again. The stop is what forces the release; pressing again is the
  acceptance.
- **Host, for an automated lowering** — `MAST ACK`. Good for **one** descent: it is spent
  when that descent stops, and goes stale after 15 s, so it cannot be pre-armed and left
  lying about.

**Up is never gated.** Backing off a hazard has to work instantly, always.

Consent is spent on every stop, so each resumed descent inside the zone is its own
decision rather than one authorisation covering the whole way down.

!!! danger "No ToF means no guard at all"
    The threshold is a *height*, so with no height source the zone cannot be detected and
    descent is completely unguarded. Telemetry reports this as `pinch=nosensor` rather
    than quietly doing nothing. Wire the ToF before running the lift anywhere near people.

### Commissioning the threshold — per robot

The default is **40 mm**, and that number is not just a finger. It has to cover a finger
*plus* how far the lift still travels after the stop pulse — a pulse round-trip plus the
controller's own ramp-down. Both depend on your lift and where the ToF sits, so measure it:

1. E-stop within reach, nobody's hands near the mechanism.
2. Put an offcut of known thickness (a 25 mm block is a reasonable stand-in for a finger)
   on the base, under the platform.
3. Lower into the zone. It should stop with the block still **loose** — you can slide it
   out without force.
4. If the block is pinched, raise the threshold with `MAST PINCH <mm>` and repeat.
5. Record the value. It is per-robot, like the relay assignment.

## Why a Z-axis is abstracted

This is one of two Z-axis mechanisms. The other is a lead-screw mast driven by an ESC
with limit switches for homing and an encoder for position. They have almost nothing
in common electrically — one is two dry contacts and an absolute distance sensor, the
other a motor controller with relative feedback that must home before it knows where
it is.

The firmware therefore talks to a **`MastDriver` interface** rather than to a lift.
The RC handling, the safety gating and the telemetry are identical for both; only the
driver differs. Two consequences worth knowing while you build:

- **Z-axis command is proportional where the mechanism supports it.** The stick's full
  resolution reaches the driver, and the driver reduces as far as its hardware
  requires. The scissor controller has no speed input, so this driver uses sign only —
  but the information is not thrown away upstream, because a lead-screw build needs it.
- **Motion is gated on `ready()`**, meaning "position is trustworthy". For the scissor
  that is a valid ToF reading. For a mast it would mean *homed*. Same gate, different
  evidence.

## Control interface

| | |
|---|---|
| **RC channel** | CH2 (right stick, vertical) — up/stop/down |
| **Deadman** | Required. The lift moves only while CH6 is held, exactly like drive |
| **Pinch stop** | Descent stops at **40 mm** (default) above full-down and needs fresh consent — see [Safety](#safety-the-lift-is-a-pinch-point) |
| **Relays** | **silk R3 → UP, silk R2 → DOWN** (code R2/R1 — silk is 1-indexed, code is 0-indexed) *— per-robot; re-discover on each build* |
| **Contact type** | **NO** (normally open) — measured; closure = press |
| **Height sensor** | ToF on the StampPLC PaHub, **channel 3** |
| **Power** | Lift motor fed 24 V from the **drive bus**; the StampPLC only switches the controller's own button signal |

Because the lift's 24 V comes off the drive bus, it is unpowered whenever the e-stop
contactor is open. Software and hardware agree by construction — there is no state in
which the robot believes the lift is safe while it can still move.

### Stick, not button presses

The lift is commanded by holding the stick, not by discrete press events. Releasing
the stick continuously asserts *stop*, every frame. That is deliberate: a
command-once scheme can lose the single message that says stop, whereas an
intent held every frame cannot. RC link loss, e-stop, deadman release and an open
contactor all stop the lift through the same path that stops drive.

The lift's own controller is **pulse-toggle** — one press starts it, another stops it —
so the firmware translates held intent into pulses on transitions, and uses the ToF to
correct its belief if a pulse is missed.

## Relay contact type — a property of the controller

**Measured on the DIHOOL controller: a closure is a press, so use the relay's
NORMALLY-OPEN (NO) contacts.** This is a property of the controller, not a per-robot
value — re-check only if you substitute a different one.

That also satisfies the rule that decides it either way: a **de-energized relay means
not-pressed**, so a dead StampPLC, a power cut, or crashed firmware all produce no
motion. Wired to NC instead, an unpowered robot would hold the button down.

## Commissioning — values discovered per robot

!!! danger "These are per-robot and must be discovered, not copied"
    Relay-to-leg assignment and stick direction are **coin flips on every build**.
    They depend on how you happened to land two wires. Copying another robot's
    values will get them wrong half the time, and a Z-axis that moves the wrong way
    is worse than one that does not move.

Confirm both on the bench, with the lift free to move and a hand on the e-stop:

**1. Which relay drives which leg.** Command each relay individually and record which
way the lift travels. This depends only on which of two wires you happened to land, so
it differs per build.

**2. Stick direction.** Confirm CH2 raising the stick raises the lift. This is part of
[RC configuration](#) rather than something specific to the lift.

*TODO: replace this with the firmware-guided procedure once the `CFG` command set and
its NVS storage exist, so the discovered values are stored on the robot rather than
transcribed into source by hand.*

## CAD & 3D model

*TODO: Fusion permalink and STL exports.*

## Bill of materials

!!! note "Generated from `hardware/parts.csv`"
    Edit the CSV (rows tagged `module = scissor-lift`), then run `tools/bom.py --inject`.

<!-- BOM-AUTO:build:scissor-lift -->
_(no parts for module 'scissor-lift')_
<!-- /BOM-AUTO:build:scissor-lift -->

## Tools

Uses the shared build tools — see the [Hardware BOM](../hardware-bom.md).
You will also need a **multimeter** for the contact-sense check above.

## Assembly

*TODO: mechanical mounting, controller placement, ToF aim and standoff, relay wiring
to the controller's button terminals, drive-bus feed.*

## Verify — done when…

- [ ] Lift raises and lowers from its own controller buttons, robot uncommanded.
- [ ] Relay contact type chosen so **de-energized = not pressed**, verified with a meter.
- [ ] R2/R3 assignment recorded, and each moves the lift the direction you expect.
- [ ] CH2 stick up raises; releasing the stick stops within one pulse.
- [ ] Deadman release stops the lift.
- [ ] Opening the e-stop contactor removes lift power entirely.
- [ ] ToF reports a plausible height across the full travel, with no dropouts at either end.
- [ ] PRISM goes whole-strip amber while the lift moves, faster on the way down.
- [ ] Descent stops at the pinch threshold with a test block still loose underneath, and
      the strip strobes red.
- [ ] Holding the stick down through the stop does **not** resume it; releasing and
      pressing again does.
- [ ] Up still works immediately from the pinch stop, without consent.
- [ ] Telemetry reads `pinch=nosensor` with the ToF unplugged — so an unguarded descent is
      visible rather than silent.
