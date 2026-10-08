# Teaching, music sync, and who plays what — experiments for the first hardware session

> **Numbering.** Item numbers such as (1.5) or (5.4), and "workplan proposal n", cite the plan as it stood when this was written, now kept as [`choreo-prior-art.md`](choreo-prior-art.md). The current plan of record is [`../workplan-choreography.md`](../workplan-choreography.md).

**Status:** design, 2026-10-06. Nothing here is built except the **capture stub** in the
composer (`software/choreo/composer/`, fake joint source) and the composer's latency-corrected
playhead. **Nothing here commands the robot**, and nothing in this note may be implemented as a
composer feature that does: every experiment below is run **by a human at the robot**, under the
robot's normal arming rules, with the composer in its preview-only role.

Companion notes: [`choreo-format-v1.md`](choreo-format-v1.md) (the format; §4 and §5 point here
for the measurements they lack), [`../workplan-choreography.md`](../workplan-choreography.md)
(plan of record — numbers like "2.1" refer to it; its standing rules bind, especially **rule 5:
every experiment states its question and its kill criterion before it starts**),
[`arm-manual-control.md`](arm-manual-control.md) §1 (freedrive, settled),
[`arm-telemetry-and-viz.md`](arm-telemetry-and-viz.md) (measured joint state via rosbridge),
[`tab5-platform.md`](tab5-platform.md) §3 and [`../workplan-tab5-idf.md`](../workplan-tab5-idf.md)
1.3, 1.3b, 1.5, 1B (the Tab5's audio), and
[`../../docs/control-authority.md`](../../docs/control-authority.md) (binding).

Three explorations, as asked: **(a)** teach keyframes by moving the arm by hand, **(b)** keep the
robot on the music, **(c)** decide which device plays and hears what. Each part ends in a table of
experiments; §4 sequences all of them into one hardware session.

**Labels used throughout.** A number with no source is either a **target** (what we would like,
chosen, not measured) or a **placeholder** (needed by the design, unknown, and named in an
experiment). Nothing here is presented as a measurement unless it cites one.

---

## (a) Teaching keyframes by hand

### The flow

1. **Arm in `FREE`** — zero stiffness, gravity compensation (`arm-manual-control.md` §1). Settled
   semantics, restated because the composer must not weaken any of them: **per arm**, **CH5 in
   `MANUAL`** (refused in `AUTO`), **latched** on the tablet with an unmissable active state, and
   **auto-exit** on lease expiry, ~60 s without joint motion, or any other arm command.
2. **The operator poses the arm with both hands.** The tablet is not in their hands, which is why
   capture cannot be a press-and-hold (see "who presses capture", below).
3. **Capture reads measured joint positions** — one `sensor_msgs/JointState` from `/joint_states`,
   which the dashboard frontend already subscribes to through rosbridge on `:9090`
   (`frontend/app.js`), throttled. Measured, never commanded: the planning model shows where the
   arm was *told* to go, which is most wrong exactly after an abort (`arm-telemetry-and-viz.md`).
4. **Convert to the outward-sense convention.** Shows write J1–J3 in each arm's own outward sense
   and the validator applies the mirror (left travels negative). A capture is the *inverse* of that
   mirror. **Without it, every pose taught on the left arm lands with J1–J3 inverted** — and the
   validator would happily check the wrong pose. The conversion is `cue.joint_state_to_outward()`,
   in `cue.py` so that there is exactly one copy of the mirror rule, and it is tested.
5. **A keyframe lands on that arm's lane at the playhead** (snapped if snap is on), through the
   composer's normal text-surgical edit — one new line in `cues:`.
6. **The validator re-runs on the whole show**, as it does after every edit, and any violation
   appears on the new keyframe.

Steps 3–6 exist today against a fake source: the arm lanes carry a **⦿ Teach** button that calls
`FakeJointSource.sample()` (a `JointState` fabricated in the *controller's* convention, left J1–J3
negative, so the un-mirroring is exercised end to end), posts it to `api/capture`, and adds the
keyframe. Steps 1–2 do not exist anywhere: **the dashboard has no freedrive operation** —
`backend/openarm.py` offers `park`, `activate`, `down`, `up` and `hold`, and nothing else.

### The capture interface (what the composer needs from the arm side)

**Read-only, by construction.** The composer's backend never subscribes, publishes, advertises or
calls a service; the **browser** reads joint state from rosbridge and hands the backend numbers.
This keeps the composer's no-command-path test (`tests/test_composer.py`) meaningful: there is no
ROS client in the composer to audit.

```text
POST api/capture                       (implemented, single-sample form)
  { "side": "left" | "right",
    "joint_state": { "name": ["openarm_left_joint1", …], "position": [rad, …],
                     "source": "rosbridge:/joint_states" | "fake" } }
→ { "side": "left", "slot": "ARM_L", "joints": { "1": deg, … "7": deg }, "source": "…" }
```

The browser side is one small interface, which the fake implements today:

```text
JointSource
  name            "fake" | "rosbridge"
  sample()        → JointState                       (implemented by FakeJointSource)
  live()          → bool   — a sample arrived within the staleness limit   (proposed)
  window(ms)      → JointState[] — samples over the last `ms`             (proposed)
```

`RosbridgeJointSource` — the real one — is a `subscribe` to `/joint_states` with a
`throttle_rate`, exactly as `app.js` already does for the arm view. It sends `subscribe` and
`unsubscribe` and **no other rosbridge op**: no `publish`, `advertise`, `call_service` or
`send_action_goal`. That is checkable by the same kind of grep the composer test already runs.

**Two refinements before it is wired to a real arm**, both proposed, neither built:

- **Stillness.** A single sample of an arm being held by hand is a sample of an arm that may still
  be moving. Capture should take `window(500)` and refuse unless every joint's spread is under a
  **placeholder** 0.5°, saying which joint moved. The request grows a `samples: [JointState, …]`
  form; the backend takes the mean.
- **Staleness.** A frozen `/joint_states` stream shows a plausible, wrong pose — the failure
  `arm-telemetry-and-viz.md` exists to prevent. Capture refuses if the newest sample is older than a
  **placeholder** 250 ms by browser receive time, and the Teach button is disabled while
  `live()` is false.

**Capture does not care how the arm got there.** It reads measured joints; freedrive, teleop
(the teleop pipeline already drives the arms), `park` and `hold` all leave the arm somewhere
measurable. So **teaching can start before freedrive exists**: pose by teleop, capture, edit.
This is the main reason to keep capture separate from freedrive in the code and in the plan — it
removes freedrive from the critical path for the composer (decision in the report).

### Who presses capture

The operator's hands are on the arm. Options, cheapest first:

| trigger | cost | problem |
|---|---|---|
| tablet button, second person | nothing | needs two people |
| tablet button, **after** letting go | nothing | the arm sags when released from the hand — see sag below; capture what it settles to, which is what playback will reproduce anyway |
| **stillness auto-capture**: a pose held still for N s (placeholder 1.5 s) while a "teach" latch is on | software only | captures poses the operator was only passing through; mitigate with an earcon and an undo |
| body-mounted button on a spare StamPLC input (DI2–DI7) | two wires and one telemetry field (`arm-manual-control.md` §1) | new hardware path; must be read-only into the composer |

Default for the first session: **tablet button after letting go**. It is the honest one — the
captured pose is the pose the arm actually holds without a hand on it.

### Timing: a taught pose has no time. Who sets it?

The playhead does. The keyframe's `t` (or `beat` / `bar`, if the lane is on the grid) is where the
author put the playhead. That is a choice about *when*, and a pose captured by hand says nothing
about it.

The cost is that a taught pose can be **unreachable in the time given**: the validator's speed cap
is checked on the segment *into* the new keyframe, so two poses taught a beat apart will usually be
refused. That refusal is correct and must stay. What the composer should add (proposed, not
built) is the *earliest legal time*:

    t_min = end_prev + max over j of ( |θ_j − θ_prev,j| / cap_j )

shown in the inspector beside the refusal, with a one-tap "move to earliest legal time" (rounded
**up** to the next grid line when snap is on). The validator stays the gate; the editor only
computes the number the gate would have accepted.

Two cautions travel with that formula:

- **`cap_j` has no recorded provenance.** `SPEED_CAP_DEG_S` came over from `showcase_demo.py`
  "with no note of how they were chosen" (`cue.py`). Teaching makes the caps the binding
  constraint on every taught sequence, so they become worth measuring or at least sourcing.
- **The cap is checked on the average speed.** If the controller does not interpolate linearly,
  peak speed is up to 1.875× the average (`choreo-format-v1.md` §5). Experiment T4.

### Gravity sag: what you teach is not quite what you get

In `FREE` the arm rests where the hand left it, so measured = actual. Played back under stiffness,
the impedance controller settles **~1.5–2° short** of a commanded pose (gravity compensation is
imperfect). So a taught pose replays ~2° "downhill" of where it was taught.

- **Do not pre-compensate in the show.** The correction would be per-robot, per-pose and
  per-controller-gain — `per-robot-values-not-in-repo`. If it is ever needed, it belongs in the
  runner as a per-robot calibration, applied after validation.
- **Do record the measured pose** — what the author saw is what the file says.
- **Clearance runs in both directions.** Sag lowers a raised arm, which moves J2 *away* from the
  shoulder/head contact at 182° in the cases measured, but the validator's clearance rule is a
  margin on the commanded angle (25°), and a ~2° sag is well inside it either way.

Experiment T3 measures the taught-to-replayed difference. If it is under the validator's 5° limit
margin everywhere, nothing is built.

### Grippers: per-side zeros

The grippers were zeroed in **different physical states** per side (`cue.py`: left
`-0.050 … 0.002` m, right `0.000 … 0.040` m). Consequences for teaching:

- A captured gripper **position** is meaningful only on the side it was captured on. Copying a
  taught `pos` to the other side — or mirroring a clip that contains one (`beat-demo.yaml`
  finding 2) — produces a number in the other side's range that means a different opening.
- **Default: teach grippers symbolically** (`do: open | close`), not by position. Capture of a
  gripper `pos` should be refused unless the destination slot matches the side it was read from.
- The finger joint names in `/joint_states` are not known to this repo; capture today reads only
  `openarm_<side>_joint1..7` and ignores the rest.

### Taught in `MANUAL`, played in `AUTO`

Teaching needs CH5 in `MANUAL` (freedrive refuses `AUTO`). A show that moves the lift says
`requires: { rc_authority: AUTO }` (`hello.yaml`: "the lift needs it; the arms do not"). So
**the teaching session and the playback session are different authority states**, and the
composer must never paper over that:

- There is no "teach and immediately play back on the robot" loop. Teach on the robot; preview
  in the browser and the simulator; play on the robot only through the (future) runner, after a
  deliberate authority change by a human on the transmitter.
- After teaching, the arm is holding the **last** taught pose. The runner's start-pose
  normalisation (2.5) must travel from there to the show's start pose, timed by distance — the
  validator's first-segment check assumes all-zero at t = 0 (`choreo-format-v1.md` §5).
- **The composer never activates an arm.** Teaching requires an arm that is already active (an
  energised arm in `FREE`), and activation steps every joint toward 0 rad at production gains. That
  hazard stays with the operator, through `openarm-up activate`, exactly as today.
- `requires:` is **not validated** today: `cue.py` stores it and checks nothing, so
  `rc_authority: AUOT` passes. The vocabulary also disagrees across the repo —
  `arm-manual-control.md` says CH5 `MANUAL`, `docs/control-authority.md` says `OFF` / `RC` /
  `AUTO`, and `hello.yaml` uses `AUTO`. Pick the names, then validate them (report, decision
  list).

### Capture a performance (Ableton's Arrangement Record — workplan proposal 9)

A design, not code. Instead of posing keyframes one at a time, an operator **performs** — drives
the arm by freedrive or teleop, works the lift on the RC, fires cues from the tablet — and the
session is recorded as an editable timeline.

**Record** (read-only, nothing new on any command path):

| source | stream | becomes |
|---|---|---|
| `/joint_states` via rosbridge, full rate (a recorder asks for full rate; the tablet view throttles) | both arms, all joints | arm keyframes |
| StamPLC telemetry already on the dashboard websocket | relay states (`RELAY2` DOWN, `RELAY3` UP), `ch5`, deadman | `LIFT up/down` cues with `secs` = on-time |
| tablet cue fires (once a runner exists) | named cues | the same cues, at their fire time |
| the head | **unknown** — the pan/tilt servos have no position feedback this repo knows of | not recorded; a gap |

Raw recordings are a sidecar (`.jsonl`, outside `shows/`, not committed by default). **A recording
is never replayed directly.** It is reduced to a show and the show is validated, like any other.

**Reduce** dense joint samples to keyframes with Ramer–Douglas–Peucker in joint space, tolerance
ε (placeholder 2°, chosen to sit at the sag noise floor), per arm. Each kept sample becomes a
keyframe with `t` = its record time. Then:

- A human moving an arm by hand **can and will exceed the speed caps**. The validator will refuse
  those segments; the editor's "earliest legal time" fix applies, and the reduction should report
  how much it had to stretch the performance — a recording that needs 40% stretching is a finding
  about the caps or the performer.
- Recorded times are **clock-bound** (`t`). Quantising to the grid is a separate, explicit
  "quantise to beat" action, as in a DAW — never automatic, because the file must record which
  binding the author meant (format §3).
- Lift: relay on-edges become `up`/`down` cues; a run longer than `LIFT_MAX_S` (8 s) is split and
  flagged. The pinch guard's stop is **not** recorded as a cue — no show may acknowledge it.
- The output is a **new show file** (or a new clip), written through the same text-surgical path.

It needs the runner and Phase 2 logging only for the cue-firing row; a recording of a purely
manual session needs only telemetry that exists. Its value is that it is how someone who cannot
read YAML writes a show. Its risk is that it is the item the workplan is "least certain anyone
wants" — so it is designed here and deliberately left unbuilt.

### Experiments — teaching

Run by a human at the robot. Composer in preview-only mode throughout.

| # | Question | Method | Kill criterion |
|---|---|---|---|
| **T0** | **Does freedrive exist** in `openarm_impedance_controller` as a parameter change (zero stiffness, gravity comp on)? | Read the controller's parameters and source; on the robot, one arm, operator's hands ready, CH5 `MANUAL`. | If it needs a new controller, freedrive leaves the composer's critical path: **teach by teleop** (capture is source-agnostic) and pursue freedrive separately. |
| **T1** | **Does the un-mirroring hold on real hardware?** | Pose both arms by teleop into a visibly symmetric pose (both elbows out, both shoulders out). Capture both. | The two captured keyframes must agree within a placeholder 3° on J1–J3 *in outward sense*. If they disagree in sign, `MIRRORED_JOINTS` or the joint naming is wrong and **every show's left arm is wrong** — stop and fix that first. |
| **T2** | **Is a single-sample capture good enough**, or is stillness detection needed? | Capture the same held pose 10× by hand-held freedrive (or teleop hold), record the spread. | If the spread across captures exceeds 1° on any joint, build the `window()` + stillness check before anyone teaches a show. |
| **T3** | **How far does a taught pose replay from where it was taught?** (sag) | Teach 5 poses spanning the envelope; replay each with `hold`-equivalent stiffness through the existing arm path (not the composer); read measured joints after settling. | If every joint lands within the validator's 5° limit margin of the taught value, no compensation is built. If not, a per-robot runner-side offset table — never in the show. |
| **T4** | **What velocity profile does the controller actually execute** between two points with empty `velocities`? | Two-point trajectory on one joint (J7, cheapest), log `/joint_states` at full rate, compute peak ÷ average. | ≈1.0: the average check stands. ≈1.5 (cubic) or ≈1.875 (quintic): the validator must check `factor × average` against the cap, and taught sequences get correspondingly slower. This closes the open question in `choreo-format-v1.md` §5. |
| **T5** | **Gripper travel time, per side.** (`choreo-format-v1.md` §4 lists it as unmeasured.) | `open` → `close` and back, 5× per side, from `/joint_states` or video. | Produces the intrinsic duration the format assumes is 0. If it exceeds 0.5 s, the validator's gripper span must carry it. |
| **T6** | **Head slew time** for a 20° and a 60° step. (Also unmeasured in §4.) | 240 fps phone video of the head against a protractor card. | Produces the `HEAD` intrinsic duration, and answers `beat-demo.yaml` finding 3: can a two-beat nod reach its target at 120 BPM? If a 20° step takes longer than half a beat (250 ms), the head is not a beat-rate actuator and the vocabulary should say so. |

---

## (b) Music synchronisation

### The hard problem is clocks

Spot's precedent is the clean one and this design starts from it: **the laptop plays the music,
the robot does not**, sync is open-loop — one future start time on the robot's clock plus an
authorially matched BPM, no beat tracking, no drift correction — and Boston Dynamics document a
case where the robot starts late and the music does not wait. We can do a little better than that
cheaply, and should not try to do much better than that at all in v1.

**Four clocks are involved:**

| clock | where | role |
|---|---|---|
| **Thor monotonic** | the Jetson | **the show clock** (2.1: it survives the e-stop; nothing else on the robot does and is also the brain). ROS 2 stamps use the Thor's system clock; the `openarm` container runs `--network=host` on the same machine, so the arms share it. |
| **tablet `performance.now()`** | the Android browser | page-local monotonic time; what the websocket ping measures |
| **tablet `AudioContext.currentTime`** | the tablet's audio hardware | what the music is actually played against; **a different oscillator from `performance.now()`** |
| Tab5 / StamPLC | the ESP32s | not synchronised; they act on receipt. A cue to them is "now", so their latency is a per-slot lead time, not a clock offset |

### The handshake

1. **Offset estimation, NTP-style, over the dashboard websocket.** The tablet sends `{ping, t0}`
   (`performance.now()`); the Thor replies `{t1, t2}` (its monotonic receive and send times); the
   tablet records `t3`. Then

       offset θ = ((t1 − t0) + (t2 − t3)) / 2       delay δ = (t3 − t0) − (t2 − t1)

   Send a burst (placeholder: 20 pings, 50 ms apart) and keep the sample with the **smallest δ**;
   its error is bounded by δ/2. Repeat in the background every few seconds. The dashboard's `/ws`
   is push-only today (`backend/app.py`); this needs one read-only message handler. It moves
   nothing and adds no command path.

   Android Wi-Fi power saving can delay frames by up to a beacon interval (~100 ms) — which is
   exactly why the minimum-delay filter, not the mean, is used. Experiment S1 measures what the
   robot's own network actually gives.

2. **Map performance time to audio time.** `AudioContext.getOutputTimestamp()` returns a pair
   `{contextTime, performanceTime}` for the sample currently leaving the output — that is the
   bridge between the two tablet clocks, and it already includes output latency *if the platform
   reports it truthfully*. Where it is missing, fall back to `currentTime` minus
   `outputLatency + baseLatency`, which is what the composer's playhead does today
   (`audioNow()` in `composer.js`). Whether either is truthful on the actual tablet, and through a
   Bluetooth speaker, is experiment S2 — Android audio latency reporting is known to vary by
   device and output route, and is not to be trusted unmeasured.

3. **Scheduled start at a future Thor timestamp.** After preflight passes, the runner picks
   `T0 = now_thor + lead` (placeholder lead 2 s: enough for the message to reach the tablet and
   for the arm goal to be accepted), and tells the tablet. The tablet converts `T0` to its own
   performance time (`T0 − θ`), then to context time through the step-2 mapping, and calls
   `source.start(ctx_T0)`. Show time 0 is audio position 0 (format §3), so nothing else needs
   aligning.

4. **Each slot is issued early by its own lead time.** The runner sends a cue at
   `T0 + t − lead_slot`, where `lead_slot` is the measured command-to-motion latency for that slot
   on **this** robot. Lead times are per-robot calibration and never enter a show
   (`beat-demo.yaml` finding 5; `per-robot-values-not-in-repo`).

   For the arms there is a better mechanism, if the controller honours it: a
   `FollowJointTrajectory` goal whose `header.stamp` is **in the future** asks
   `joint_trajectory_controller` to start the trajectory at that time, which puts the arms on the
   Thor's clock directly and leaves only tracking lag. Whether `openarm_impedance_controller`
   honours a future stamp is unknown (experiment S5).

### Drift over a three-minute show

Two independent crystals drift apart. Typical consumer oscillators are specified at tens of ppm;
the tablet's audio clock may differ from its own system clock as well. Over 180 s:

| relative drift | error at the end of a 3-minute show |
|---|---|
| 20 ppm | 3.6 ms |
| 50 ppm | 9 ms |
| 100 ppm | 18 ms |
| 200 ppm | 36 ms |

These are **arithmetic, not measurements of our devices.** If S3 measures drift in the low tens of
ppm, **no drift correction is built** — Spot does none. If it is worse, the correction direction
matters: the **Thor stays the master** (2.1), and the tablet follows by nudging the source's
`playbackRate` by at most a few hundred ppm. 0.1% is about 1.7 cents of pitch, below what a
listener will notice. The robot is never slaved to the music, because a music player that
stalled would then stall or lurch the robot.

### A tempo error is a drift error

The composer's **tap tempo** takes the median of the tap intervals. Human taps scatter by tens of
milliseconds; across 16 taps (about 8 s at 120 BPM) that is an error on the order of 0.3–0.5% in
BPM. Over a 3-minute show, 0.4% is **0.7 s** — over a whole beat at 120 BPM by the end. Tap tempo
is a starting estimate, not a final value.

The fix is cheap and should be the next composer feature (proposed, not built): a **two-point
tempo fit** — the author marks beat 1 near the start and a numbered beat near the end, and

    bpm = 60 · (n − 1) / (t_n − t_1)

With ~20 ms of marking error at each end over ~170 s, the BPM error is about 0.02%, i.e. a few
milliseconds by the end of the show. The existing "beat 1 ⇤" button already sets the first point.

### Offline beat extraction versus live beat tracking

**Offline, for v1.** The show is validated against one `bpm` and `offset_s` before anything moves.
Both can be extracted from the file in the composer:

- **By hand**, with tap tempo plus the two-point fit above. Needs nothing new, works for any music,
  and is exact enough.
- **Automatically**, with a beat tracker. This must be a separate tool, not part of `cue.py`, which
  stays stdlib + PyYAML. Licences differ in ways that matter for a repo that is meant to be
  replicated — as understood when writing this, and to be checked before anything is vendored:
  `librosa` is ISC; `aubio` is GPL-3; `madmom`'s code is BSD-style but its trained models are
  CC BY-NC-SA; Essentia is AGPL. A tracker's output is a *suggestion* the author accepts into
  `music:`, never a value the runner reads.
- Real recordings often do not hold one tempo. v1's one-tempo-per-show limit (format §3) is a
  constraint on **music choice**: electronic and click-tracked music fits; a live band may not.

**Live beat tracking from a microphone: not in v1, and the reason is the validator.** A live
tracker changes *when* things happen during the show. If it changes tempo, it changes every
segment's duration and therefore every speed the validator checked; a live-retimed show is an
unvalidated show. The only safe forms are (i) **phase-only** correction within a small window
(placeholder ±50 ms) at fixed tempo, or (ii) validating the show at the worst-case tempo of an
allowed band. Both are research. And the robot's microphone hears the robot: its own speaker (the
ES7210's AEC reference channels are off — `tab5-platform.md` §3), its motors and its lift.

### What latency does to "on the beat"

The audience judges the **moment of an accent** — a wave's reversal, a stop, a lift starting —
against the beat they hear. Every slot gets there by a different route, and none of the routes
has been timed (`workplan-choreography.md`: "Nothing here has been timed"):

| slot | path from show clock to motion | latency | jitter | beat-capable? |
|---|---|---|---|---|
| `ARM_L/R` | trajectory goal → impedance controller → CAN; with linear keyframes the accent *is* the arrival at the keyframe | placeholder | placeholder; impedance tracking lag and the ~2° settle make "arrival" fuzzy | likely, if goals are pre-sent with a future stamp |
| `LIFT` | UDP `MAST CMD` → StamPLC → relay → motor; 250 ms heartbeat | placeholder | relay + motor spin-up | start only; a slow, large motion |
| `HEAD` | Tab5 frame `0x104` over Wi-Fi → servo | placeholder | Wi-Fi | unknown (T6) |
| `FACE` | Tab5 over Wi-Fi → LVGL frame | placeholder | Wi-Fi + frame period | unknown |
| `SOUND` | Tab5 over Wi-Fi → codec | placeholder | Wi-Fi; refuses while busy | unknown |
| `LIGHT` | StamPLC `LED_COLOR` over UDP → PRISM | placeholder | probably small | probably |

**A target, not a measurement:** keep each accent within roughly **±40 ms** of the heard beat.
For orientation only: broadcast lip-sync work (ITU-R BT.1359) puts the detectability of
audio–video offset at roughly 45 ms with sound *leading* and 125 ms with sound *lagging*. That is
a different stimulus from a robot arm, but the asymmetry is plausible here too and gives a useful
default: **when a slot's latency is uncertain, err toward the motion being early**, because sound
arriving after the motion is the natural order of the world and is tolerated more. Also: sound
travels about 3 ms per metre, so a tablet 10 m from the robot is heard ~30 ms late by someone
standing at the robot.

**A slot whose jitter cannot be brought under a quarter beat** (125 ms at 120 BPM) is not a
rhythm actuator, and the vocabulary should say so rather than let authors put it on the beat.

### On abort, and on e-stop — what happens to the music

Standing rule 2: preemption aborts, never pauses. The robot must then **look and sound stopped**
(the workplan's disco-ball argument, 3.5). Proposed default, and a decision for the maintainers:

- **Abort** (deadman release, stick deflection, monitor trip, fault, watcher loss): the runner
  tells the tablet `music: stop`; the tablet **fades out over ~250 ms** (a hard cut clicks). No
  resume; a restart begins at 0.
- **E-stop**: the contactor opens and **the Tab5 goes dark** — face, earcons and any sound on it
  stop instantly. **The tablet does not**, and nothing stops its music unless told to. The runner
  aborts on the contactor state in `[AUTH]` and sends `music: stop` as above. Optionally the
  tablet then plays a distinct "stopped" sound, because after an e-stop **the tablet is the only
  device in the system that can still make a sound** — the decision (1.3b) that earcons are not
  safety communication still stands; this would be a legibility choice, not a safety one.
- **Music is a held intent, like everything else on this robot.** The tablet keeps playing only
  while it receives the runner's show heartbeat over the websocket; if the heartbeat is absent for
  a placeholder 1 s, it fades out by itself. A dead Thor stops the robot (2.3); it should also stop
  the music, with no message needed.
- **Does the tablet disconnecting abort the show?** It is the operator's UI, but the live stop is
  the deadman on the RC (control-authority: "releasing aborts the routine"). Default proposed:
  **no** — tablet loss stops the music, not the robot; the deadman is the watcher. A maintainers' decision.

### Experiments — synchronisation

| # | Question | Method | Kill criterion |
|---|---|---|---|
| **S1** | **How well can the tablet know the Thor's time** on the robot's own network? (Not the bench network — `workplan-tab5-idf.md` 1B.) | Ping burst over the dashboard websocket, 10 min, tablet in the operator's hand at 1, 5 and 15 m. Record min-δ per burst. | If the best δ/2 is routinely over 20 ms, the tablet cannot be scheduled tightly: move music to the Thor (c, option 3) or accept Spot-style looseness, and say which. |
| **S2** | **Is the tablet's reported output latency true** — speaker and, if used, Bluetooth? | The tablet plays a click scheduled at Thor time T; at the same T the Thor writes `LED_COLOR` (fast, wired). 240 fps phone video with sound. Compare against `outputLatency` / `getOutputTimestamp()`. Repeat over Bluetooth. | If reported and measured differ by more than 20 ms, the playhead correction (already in the composer) and the scheduler both use a **measured** per-device constant instead of the reported value. |
| **S3** | **What is the drift** between Thor time and tablet audio time over 10 minutes? | S1's offset series plus `getOutputTimestamp()` pairs; slope of a linear fit. | Under ~50 ppm (≤ 9 ms per 3 min): no correction is built. Over: `playbackRate` slew, Thor as master. |
| **S4** | **Per-slot latency and jitter** — the table above. | A **latency show**: every slot fires isolated cues exactly on clicks of the generated click track, nothing else moving; 240 fps video with sound; 20 repetitions per slot. Run through the future runner or a hand-run equivalent of today's demo script — **not** the composer. | Produces `lead_slot` and jitter per slot, stored on the robot. A slot with jitter over ¼ beat is marked not beat-capable. |
| **S5** | **Does `openarm_impedance_controller` honour a future `header.stamp`?** | Send one goal stamped 2 s ahead; observe when motion starts. | Yes: arms are scheduled on the Thor's clock and their lead time is tracking lag only. No: arms are issued by the runner at `T0 + t − lead`, like every other slot, and their jitter is whatever S4 measures. |
| **S6** | **Is tap tempo + the two-point fit good enough** for real music? | Fit three CC0 or self-made tracks of 3 minutes by hand; compare with a beat tracker's output. | Beat N predicted within 20 ms at the end of the track. If not, a tracker becomes a composer feature (licence permitting). |

---

## (c) Audio in and out: the Tab5, the tablet — and a third option

### What constrains each device

**The Tab5** (the head):

- **Dies with the e-stop.** It is powered from the e-stop-protected drive branch. Anything it
  plays stops when the contactor opens; anything that must outlive an e-stop cannot be on it.
- **One audio player that refuses while busy** under the ESP-IDF HAL (`workplan-tab5-idf.md` 1.3:
  "the channel model in the design was wrong"). Music on the Tab5 would block every earcon for the
  length of the song. (The *Arduino* face's `M5.Speaker` had 8 virtual channels — the workplan's
  3.2 still describes that, and it no longer applies.)
- **~3.5 MB of SPIFFS** — about 3 minutes of 16-bit 11 kHz mono. A show's music does not fit at
  any quality an audience would choose.
- **Good speaker, capable mic.** The narrow-band speaker claim is withdrawn; ES7210 stereo mic
  gives left/right direction at default gain; speaker and mic run simultaneously; no hardware AEC.
- Six earcons exist (`Heard Attend Refuse Fault Mode Boot`), with no network verb to trigger them.
- On the **robot** — sound comes from the robot.

**The Android tablet** (the operator's UI):

- **Survives the e-stop.** Has storage, a decent speaker, Web Audio, and a Bluetooth stack.
- **Is the operator's device, and moves with the operator** — possibly far from the robot. Music
  comes from where the operator stands.
- **Browser rules:** audio needs a **user gesture** to start (autoplay policy), so the operator's
  tap that arms the show must also resume the `AudioContext`; a show started from anywhere else
  (an agent, a clap) cannot start tablet audio unless it was primed by a tap earlier.
  `getUserMedia` (the microphone) and the Screen Wake Lock API both require a **secure context**
  — HTTPS or `localhost`. The dashboard is served over plain HTTP from the robot's LAN address, so
  **the tablet's mic is unavailable today, and the tablet cannot hold its own screen awake** from
  the page. Both interact directly with the dashboard's authN/HTTPS work (the dashboard has no
  authentication yet, and therefore no reason yet to have certificates).
- Android may suspend a page's audio when the screen locks or the tab is backgrounded (C1).
- **No internet assumed**: everything the tablet plays must come from the Thor, not a CDN or a
  streaming service.

**A third option the brief did not list: the Thor.** A USB audio device and a speaker on the
robot, played by the Thor:

- the music then lives **where the clock lives** — workplan 3.2: "If music is the timebase it must
  live where the clock lives". The whole tablet handshake above (S1–S3) disappears;
- it **survives the e-stop** (the Thor is not on the protected branch), and sound comes from the
  robot;
- costs a small hardware addition and a speaker location. A USB Audio Class device should work
  under Linux on the Thor; that is not verified. Like any part, it enters `hardware/parts.csv` only
  once it is fitted and validated.

### Who does what — recommended split

| role | Tab5 | tablet | Thor + USB speaker | recommendation |
|---|---|---|---|---|
| **show music** | no — dies with e-stop, one blocking player, 3.5 MB | **v1: yes** — works today in the composer, Spot's precedent | **target, if S1–S3 disappoint** — removes the clock problem | **tablet now; decide the Thor after S1–S3** |
| **earcons** | **yes** — they are the face's voice, and dying with the e-stop is acceptable (1.3b: not safety) | — | — | Tab5; needs a network verb (firmware, out of scope here) |
| **speech / TTS** | **yes** — co-located with the face and the band's "speaking" pulse; generated on the Thor and streamed (1B) | fallback | possible | Tab5. **Conflict:** one non-mixing player means speech and an earcon cannot overlap; mix on the Thor into one stream, or arbitrate (`SOUND` is queue-with-preemption, `behavior-api-scope.md`) |
| **"show stopped" after e-stop** | **cannot** — it is off | **yes**, the only device left that can | yes | tablet (or Thor), if wanted at all |
| **clap-to-start** | **yes** — on the robot, mic works | no mic without HTTPS | — | Tab5 → event to the Thor. **A clap may only be the GO of a show already armed by a human** with preflight passed and deadman held; never a lease claim. Speech or sound as a control source passes the same failsafe-gated contract as any source (`punch-list.md`). |
| **direction of voice → aim the head** | **yes** — L/R level ratio already works | — | — | Tab5, as an `EXPRESSION`-tier `HEAD` source; during a show the show owns `HEAD` (`TASK`) and voice-aim yields |
| **live beat tracking** | hears itself and the motors | needs HTTPS; far from the robot | possible with a USB mic | **none in v1** (see (b)); research only |
| **operator voice commands** | — | needs HTTPS + authN | — | out of scope; recorded as a dependency |

### Experiments — audio roles

| # | Question | Method | Kill criterion |
|---|---|---|---|
| **C1** | **Does a primed tablet `AudioContext` keep playing through a 3-minute show** with the screen dimming, locking, or the operator switching tabs? | Play a 5-minute generated track from the composer; let the screen time out; switch apps; log `AudioContext.state` and audible gaps. | If audio suspends on screen lock and the page cannot hold a wake lock over HTTP, either HTTPS moves up the queue, the show UI tells the operator to keep the screen on, or music moves to the Thor. |
| **C2** | **Is the tablet's mic reachable at all** from the dashboard's origin? | Call `getUserMedia` from the dashboard's HTTP origin on the tablet. | Expected refusal. Confirms that any tablet-mic feature is blocked on HTTPS; record it on the authN/HTTPS item rather than working around it with browser flags. |
| **C3** | **Can the Tab5 mic detect a clap while the robot moves?** | Clap at 1, 3 and 5 m during (i) silence, (ii) arm motion, (iii) lift travel, (iv) the Tab5 playing an earcon. Count hits and false triggers over 5 minutes of motion with no claps. | Any false trigger during motion kills clap-to-start as a GO; it can still be an expression trigger. |
| **C4** | **Does the single Tab5 player drop sounds in a dense show?** | A show with `SOUND` cues at the validator's minimum spacing; count what plays. | Any drop with the validator passing means the `SOUND` span model (format §4) is wrong for the IDF face — correct the spans, not the firmware. |
| **C5** | **Thor audio out exists and holds time.** (Only if S1–S3 fail.) | A USB audio device on the Thor; play the click track scheduled on the Thor's clock; film with S4's method. | Lead time and jitter beat the tablet's. If not, the tablet stays the music device. |

---

## 4. The first hardware session, in order

Ordered so that nothing moves until the cheapest, motion-free questions are answered, and so that
every result unblocks the next. **A human runs every step; the composer only previews.**

| step | experiments | the robot is | why here |
|---|---|---|---|
| 1 | **S1, S3, C1, C2** | powered, **contactor open** (e-stop in): Thor, tablet and network only | clocks and the browser need no motion — and with the Tab5 dark, nothing on it can confuse the result |
| 2 | **S2** | contactor open; PRISM on the StamPLC (wired, not on the protected branch — verify) | the tablet's real output latency, before any slot is calibrated against it |
| 3 | **T0, T1, T2** | e-stop clear, arms **activated by the operator** through `openarm-up`, CH5 `MANUAL`, one arm at a time | does freedrive exist; does the mirror hold; is one sample enough — **before** anyone teaches a show |
| 4 | **T4, T3** | as step 3 | controller profile and sag decide whether the validator changes |
| 5 | **T5, T6, S5** | as step 3, plus the head | the missing intrinsic durations from format §4, and future-stamped goals |
| 6 | **S4, C3, C4** | CH5 `AUTO`, deadman held, show-shaped operation | per-slot latency needs every slot and the click track; the clap test needs the robot moving |
| 7 | **C5** | — | only if steps 1–2 say the tablet cannot hold time |

Every result that is a number goes to one of two places: **a design note** (if it changes a rule
or the format), or **the robot's own calibration** (lead times, sag offsets, device latency) —
never a show file and never a committed constant without its measurement cited beside it
(workplan carry-over: "a validation rule outlives the claim it was built on").

---

## 5. The live-output binding — interface only, not built (5.4)

For completeness, because teaching and sync both end at it. The composer's default sink is the
simulator; live output is a **separately armed** binding, and its interface is:

- The composer **never** sends to the robot. It hands the **resolved timeline** (`cue.py export`,
  schema `opendubs.choreo.timeline/1`) to the runner, which re-validates rather than trusts it.
- Arming is a **human act on the robot side** (preflight from `requires:`, deadman held, CH5 as
  required), not a composer button.
- The runner owns the lease, the heartbeat, the show clock, `T0`, the per-slot lead times and the
  music heartbeat in (b). The composer only displays status (Spot's taxonomy, proposal 8).
- **Blind** (editing one part while the rig runs another) is not offered; nothing in this design
  makes the composer a live editor of a running show.
