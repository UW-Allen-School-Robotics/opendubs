# Control authority and modes

What decides who may move the robot, what happens when two sources want the same axis, and what
a human's hand on the stick means. This is the safety model; the state machines that implement
parts of it are in [State machines](state-machines.md), and the transport is in
[Command & telemetry](interfaces/command-telemetry.md).

## Two top-level modes

Every rule below sits inside one of these.

**NORMAL** — the robot is nerfed, and says so. Collision avoidance on, hazards annunciated,
envelope limits enforced rather than advisory. This is the mode for demos, students, transport,
and anything with an audience.

**RESEARCH** — protections may be deliberately overridden. Each override is explicit,
individually chosen, and recorded as part of the experiment. Never the default and never reachable
by accident. It assumes an operator who knows what has been switched off.

## What the robot physically has

Authority is a **switch on the transmitter**, not a software setting, because the question *may
autonomy move this robot* must never be ambiguous.

| Control | Channel | Positions | Meaning |
|---|---|---|---|
| **Authority** | CH5 | `OFF` / `RC` / `AUTO` | who may command |
| **Deadman** | CH6 | held / released | a human is deliberately engaging |
| **Payload mode** | CH7 | `OFF` / `Z-AXIS` / `PAN-TILT` | **what the right stick talks to** |
| **Speed** | CH8 | knob | scales commanded velocity |

**CH7 is an input multiplexer, not an authority.** There are two sticks and three things that want
one, so CH7 selects which axis the right stick addresses. It must never come to mean "enable" —
if it does, the robot has two answers to the authority question.

## Ownership and modulation are different things

**Ownership** — who holds an axis. Policy, human, or playback. It changes hands.

**Modulation** — a bounded human delta applied to whoever currently holds the axis, ownership
unchanged.

Conflating them fails in both directions: in a research session a nudge must not abort the policy
under study, and in a demo a bump must not be silently blended into motion near a child.

| Mode | What a stick input means |
|---|---|
| RESEARCH / `AUTO` | bounded correction — the policy keeps driving and keeps its lease |
| NORMAL / choreography | abort — the routine stops and stays stopped |

### Ownership is a lease

One primitive: **a source holds an axis, at a priority, with an expiry.**

- Human drive in `RC` — a standing lease.
- Autonomy in `AUTO` — a standing lease. On the computer, one program at a time holds the base:
  it claims it on `/base/claim`, keeps the claim alive, and drives on `/base/<owner>/cmd_vel`. The
  [Navigation interface](interfaces/navigation.md#moving-the-base) has the details.
- A choreographed routine — a lease for the routine, released at the end. Triggered from the
  tablet, it takes over while playing and hands control back afterwards.
- An intervention — a lease held only while the deadman is held and the stick deflected.

**Every lease expires.** The `MAST CMD` heartbeat is 250 ms, which is why a dead caller stops the
lift instead of leaving it travelling. Expiry belongs to the primitive so that no future source
can forget it.

**Preemption aborts a routine; it does not pause it.** Once a human has intervened the robot is no
longer where the timeline assumed it would be.

### How far an intervention reaches

**It depends on the mode**, because a demo and an experiment want opposite things.

- **NORMAL — a hand on the stick quiets the robot.** Every axis yields, not just the one being
  touched. In front of an audience the bystander-legible behaviour *is* part of the safety story:
  touching the control stops the machine.
- **RESEARCH — the intervention is a scalpel.** Only the touched axis is affected, so a drive
  correction does not abort an arm policy mid-episode.

## Monitors constrain; they never command

A monitor does not hold a lease. It bounds whoever does. The pinch guard works this way, which is
why one implementation covers the stick, the dashboard and the head rather than being written
three times.

Monitors need a **response ladder**, not a single action: limit → pause → controlled stop → power
off. Cutting power mid-manipulation can be worse than stopping in a controlled way.

**A monitor that changes behaviour must say so.** The robot must never quietly do something
different from what it was asked while the operator is left guessing why. If a limit is being
applied, that is annunciated on the robot itself.

Current monitors: the pinch guard, the envelope limits, collision avoidance, and the e-stop.

### Height and speed — advisory

The Z-axis height is measured, and a raised payload moves the centre of mass up, which makes hard
braking and direction slams worse — the failure mode named in
[Design philosophy](design-philosophy.md). In practice the robot has proven stable and unlikely to
tip.

So this is **annunciation, not a clamp**: raised, the robot says it is tall and should be driven
gently, and the commanded speed is not silently reduced. Where and how that annunciation appears
is part of the open annunciation question below.

## Corrections

- **Bounded and hard-clipped.** If a full stick can command as much as the policy can, the
  distinction between correction and takeover collapses. Never escalate silently, and log
  saturation — "the human wanted more than the envelope allowed" is a signal about the policy.
- **Not every axis can take one.** A correction needs something to add a delta to.

    | Axis | Correctable? |
    |---|---|
    | Drive (mecanum velocities) | yes |
    | Pan/tilt (position servos) | yes |
    | Scissor lift | **no** — bang-bang, an up button and a down button; takeover only |
    | Lead-screw mast, if built | yes |

- **Report the executed action back to the policy, not the requested one.** If the policy commands
  0.4 and a correction makes it 0.55, the next observation is the consequence of 0.55. A policy
  learning on-policy from its intended actions is learning from a fiction.
- **Log the counterfactual** — what the policy wanted at the instant the human acted. Corrections
  alone are a dataset; corrections plus the counterfactual teach you something. Keep evaluating the
  policy during an intervention.
- **Ramp out over ~200 ms** on release rather than snapping back to the policy value, which is a
  step discontinuity on a moving base. The human's influence outliving their input slightly is the
  price.

## The deadman

Its meaning is constant — **a human is deliberately engaging** — and only the consequence changes.

| Mode | Holding the deadman |
|---|---|
| `RC` | authorises motion |
| `AUTO` | **arms intervention**, so an accidental knock cannot derail the policy — and, for now, is *required* for autonomous motion at all (see below) |
| Choreography | the operator's live stop — releasing aborts the routine |

It is **two-factor: held *and* deliberately deflected**, with a threshold well above the drive
deadzone. A thumb resting on the deadman must not reintroduce the accidental-knock problem.

An intervention is reported to the rest of the stack — which axis, how long, how much. Silent
resumption from a state the policy did not plan is a discontinuity it cannot see.

### Autonomy is restricted while it is young

The model above says a planner holds a standing lease and the deadman merely arms intervention.
The firmware is currently **stricter**, deliberately:

- **AUTO requires the deadman held.** Without it, CH5 in AUTO plus the program that holds the base
  would move the robot with nobody touching anything. That is the correct end state — a planner cannot
  hold a switch, and autonomy whose precondition is a human holding something is not autonomy — but
  it is the wrong default until the odometry has been proven to read the right way round.
- **Commanded magnitude is scaled** to `kAutoMaxUnit` (0.35) times the CH8 speed knob, so the
  operator keeps a physical throttle over a software driver. It scales rather than clamps, because
  clamping each axis independently would rotate a diagonal command.

Both are one edit in one place in `rc_serial_probe/src/main.cpp`, and the controller announces the
restriction when AUTO engages so a capped planner does not look like a broken one:

```
[SAFE] AUTO engaged: cap=0.35 x CH8 knob, deadman REQUIRED
```

## Where a mode lives

- **Authority is physical** (CH5). It stays a switch.
- **The input multiplexer is physical** (CH7), and is not an authority.
- **Choreography profiles are software, triggered from the tablet.** They are set up deliberately
  rather than flicked mid-drive, which keeps the physical switch answering only the question that
  must never be ambiguous.
- **A tablet-triggered routine is a network source claiming a lease.** It goes through the same
  arbiter and the same monitors as everything else, not around them.

## Open

- **Annunciating the active mode.** `pmode=` is in telemetry and nothing displays it. It belongs on
  the **robot** — the face and the PRISM indicator — not on the transmitter: the operator's eyes
  are on the machine, and bystanders have no transmitter at all. This is also where the advisory
  height warning would appear.
- **Parked base.** Holding the base in place while an arm works is a real HOLD, not an absence of
  commands. Deferred deliberately: no arm work depends on it yet. When it is needed, the choice is
  a latched brake that refuses drive until explicitly released, or station-keeping on Pinpoint
  odometry — the first is more legible near people, the second resists being pushed.
- **Logging fields for an authority transition.** What is recorded when an axis changes hands.
