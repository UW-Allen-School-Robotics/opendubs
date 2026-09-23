# A behaviour API for OpenDubs — scope

Draft, 2026-09-22. Internal: this is a proposal, not the current design.

Prompted by Fauna Robotics' Sprout paper ([arXiv:2601.18963](https://arxiv.org/abs/2601.18963),
§V-D), which describes the cleanest published account of the problem we have: many things on one
robot all wanting to move the same parts, and no principled way to decide who wins.

## The idea worth taking

Sprout does **not** use a state machine for expression. Behaviours emit partial commands into
independent **slots** ("LED patterns, audio cues, body and head targets, show-element poses"),
and each slot has an explicit **merge policy** — additive blending for layered light, select-one
where exclusivity is required, per-slot last-writer-wins for overrides. A tree of nodes combines
children "into a single, coherent command using an explicit merge policy", and priority falls out
of the blend: "alerts preempt other behaviours while expression and idle layers blend."

Two things make that attractive here:

1. **It is compositional without being a state machine.** Our robot already has one authoritative
   state machine (`robot_state`), and it should stay small and safety-shaped. Expression does not
   belong in it.
2. **It matches hardware we already have.** The PRISM strip is literally this model already:
   LAYER_0 is a base colour, LAYER_1 is an overlay owned by whoever has the strongest claim
   (`PrismOverlay::{NONE,TURN,MAST_MOVE,MAST_HOLD,NOT_READY}`), and NOT_READY outranks everything.
   We invented one slot with one merge policy and never generalised it.

## What we actually have to arbitrate

Written from the code as it is today, not as we wish it were. Every one of these is a real
control path with a real gate in front of it.

| Surface | How it is commanded today | Gate that already exists |
|---|---|---|
| Drive | `DRIVE fwd rot str` over UDP, ≥ every 250 ms | RC authority (CH5), deadman (CH6), contactor, drive-safe, 2 s ESC arm |
| Z-axis | `MAST CMD -1..1`, ≥ every 250 ms | same arm gate, plus the pinch guard's consent gesture |
| Head pan/tilt | Tab5 frame `0x104` (absolute deg) from the Thor's IP | Tab5 must be in AI tracking mode; RC stick and on-screen D-pad outrank it |
| Face | `STATE <name>` text to the Tab5 | none — it is an indicator |
| Light (PRISM) | `LED_COLOR r g b br` (base) + firmware-owned overlay | overlay priority in firmware |
| Sound | `BEEP <hz> <ms>`, `CHIME`, `VOL` to the Tab5 | codec readiness (`spk:ok`) |
| Arms | `FollowJointTrajectory` per arm, ros2_control | joint limits, torque limits, 1.2 m TCP workspace box |
| Grippers | `GripperCommand` per side | effort cap, stall abort |
| Mic | nothing yet | — |

**The gates are the point.** A behaviour API that can talk past them is worse than no API. The
rule should be: *behaviours express intent; the existing gates decide whether intent becomes
motion.* Nothing in this proposal removes a gate or adds a bypass.

## Proposed model

### Slots

`DRIVE`, `ZAXIS`, `HEAD`, `FACE`, `LIGHT`, `SOUND`, `ARM_L`, `ARM_R`, `GRIP_L`, `GRIP_R`.

One slot per thing that can be commanded independently. Arms are separate slots because our
choreography already treats them that way (one waves while the other holds), and because a
future task will want one arm while expression keeps the other.

### Merge policy per slot

Not one policy for everything — that is the mistake `MAST CMD` made, where two senders
interleaved and the relays pulsed on every flip (fixed in #25, and only by making one sender shut
up).

| Slot | Policy | Why |
|---|---|---|
| `DRIVE` | **select-one, highest priority only** | Blending two drive intents produces a third direction nobody asked for. |
| `ZAXIS` | **select-one**, and the winner must hold it continuously | It is a held intent with a 250 ms expiry and a consent gesture; blending is meaningless. |
| `ARM_L` / `ARM_R` / `GRIP_*` | **select-one, with a hand-off protocol** | Trajectories are stateful; a second writer mid-trajectory is a collision, not a blend. |
| `HEAD` | **select-one with fallback** | Highest priority aims it; when nobody claims it, idle/gaze takes over. This is Sprout's model and it suits a head. |
| `LIGHT` | **layered** | Base colour + overlay, exactly as the firmware does now. Generalise to N layers with priority. |
| `SOUND` | **queue with pre-emption** | Sounds are events, not levels. A safety alert interrupts; expression waits its turn. |
| `FACE` | **select-one with fallback** | State display is the fallback; an expression can take the screen and hand it back. |

### Priority tiers

`SAFETY` > `OPERATOR` > `TASK` > `EXPRESSION` > `IDLE`

- **SAFETY** — e-stop, faults, pinch-guard prompts, thermal. Preempts every slot it touches.
- **OPERATOR** — the RC sticks and the dashboard. A human reaching for the transmitter must win
  over anything the robot has decided to do. (The firmware already does this for the head: "the
  stick wins while it is being used".)
- **TASK** — autonomous work, teleop, a running choreography.
- **EXPRESSION** — reactive social behaviour.
- **IDLE** — breathing, look-around, the thing it does when nothing else is happening.

### Heartbeats are part of the model

Every actuator slot on this robot is already a **held intent that expires**: drive and Z-axis at
250 ms, the head's claim at 600 ms, the Tab5's lift request republished every 50 ms. A composer
that dies must stop the robot, not leave it running.

This should be explicit in the API rather than rediscovered per surface: a slot claim carries a
TTL, the runtime republishes while the behaviour lives, and expiry means release. It is also the
honest way to express "who owns the arms" — a claim with a lease rather than a flag someone
forgets to clear.

## The choreographer

`showcase_demo.py` is a working prototype of this and it taught us specific things worth keeping:

- **An absolute timeline beats sequential steps.** Keyframes at times, not "do this then that".
  It is what let the lift rise *during* the arm sequence instead of after it.
- **Validate the whole plan before sending any of it.** Joint limits with a 5° margin and a
  per-joint speed cap, checked across every waypoint, one failure and nothing goes out. This
  caught a 43°/s opening move the day it was written.
- **Normalise the start.** Plans assume a starting pose; the runtime moves there first, timed by
  the distance actually travelled, or the caps silently lie.
- **Non-blocking effects.** Gripper ramps and sounds run in their own threads; the arms never wait
  on a noise.
- **Instrument the timeline.** Printing `[t=+6.0s] commanding UP` is what proved the concurrency
  worked, after two runs where it looked wrong.

What it cannot express, and a real API must:

- **Nothing can be conditional.** No "look at the person", no "stop if X".
- **No abort path.** Ctrl-C and heartbeat expiry, nothing graceful.
- **No claims.** It assumes it owns the arms, the lift, the head and the speaker.
- **Consent gestures cannot be scripted.** A descent through the pinch zone needs a human release
  and re-press. A cue sheet must be able to *ask* for that and wait, not fake it.

Proposed shape: a declarative cue sheet (time, slot, action, priority), validated ahead of
execution against the same limit checks, run by a runtime that holds claims and republishes them,
with `abort()` releasing every slot it holds.

## Where we compare favourably

Worth being clear-eyed, since the paper is a polished platform announcement and we are a
teaching robot built in a lab.

- **Our safety chain is in hardware, not policy.** A contactor that cuts motion power, an RC
  deadman, a drive-safe gate that refuses to command anything if the control loop misses its
  timing, and a 2 s neutral arm before the ESCs will move. Sprout's safety story is compliant
  control, limited torques and soft exteriors — excellent for contact, but it is a *design*
  argument. Ours can be tested by pressing a button.
- **Expiry everywhere.** Every remote command on this robot dies in 250 ms without a heartbeat.
- **A screen face is a better research instrument than LEDs and eyebrows.** Sprout deliberately
  chose "non-screen show elements … to emphasize the physicality of robotic embodiment", and that
  is a defensible aesthetic. But we can iterate an expression in minutes, over the network, with
  no mechanism to build — and we now have audio on the same device.
- **We have a Z-axis.** Sprout changes gaze height by posture; we can change the whole payload
  height by 120 mm+ under guard. That is a real HRI affordance (meeting someone at their height)
  that a fixed-height humanoid cannot offer.
- **Dual 7-DOF arms with impedance control and a real workspace guard**, on a mecanum base that
  can strafe — a different and in some ways broader manipulation envelope than a legged humanoid
  at this price.
- **Telemetry-first.** Every subsystem publishes `[TAG] key=value` lines that are logged
  continuously. Most of today's bugs were found by grepping that log, not by reproducing.

## Gaps to close before any of this is real

1. **Nothing arbitrates today.** Last writer wins, everywhere. Proven twice: the idle Tab5
   holding the Z-axis at STOP (#25), and the demo's head commands competing with nothing only
   because nothing else was running.
2. **No self-collision checking.** Measured on opendubs2: the arm reaches the head at J2 182°
   with the elbow folded, and the controller's only Cartesian guard (a 1.2 m box) is satisfied
   there. Any composer that can move shoulders needs a real answer.
3. **The head has no rate API from the Thor**, only absolute angles, and only in AI tracking mode
   — which cannot be set remotely.
4. **The face has no vocabulary** beyond robot state. See `tab5-face-expressiveness.md`.
5. **The mic is unused.** Hardware is capable (ES7210, stereo capture); nothing reads it.
6. **Consent gestures are unmodelled.** See `pinch-guard-consent.md`.

## Suggested increments

1. **A slot layer over existing paths.** No new control paths: a small library that owns claims,
   TTLs and priorities, and emits the commands we already send. Immediately fixes "two senders
   fighting" as a class of bug.
2. **The cue sheet**, replacing `showcase_demo.py`'s hard-coded timeline with validated data.
3. **Face vocabulary + sound vocabulary**, which is what makes any of it read as behaviour rather
   than motion.
4. **Mic and speech**, which is where wake-word interaction and "look at the speaker" become
   possible. Sprout uses a four-mic array with source localisation and openWakeWord; we have two
   channels, so localisation is left/right at best.
