# Show format v1 — the specification

> **Numbering.** Item numbers such as (1.5) or (5.4), and "workplan proposal n", cite the plan as it stood when this was written, now kept as [`choreo-prior-art.md`](choreo-prior-art.md). The current plan of record is [`../workplan-choreography.md`](../workplan-choreography.md).

**Status:** proposed, 2026-10-06. Implemented by `software/choreo/cue.py` (validator and exporter)
on the `feat/choreo-composer` branch. This is workplan proposal 11: the format written down as a
document, so that an agent can write a valid show from this page alone (workplan 6.4) without
reading the validator's source.

**Scope.** What a show file may say, what each field means in time, what the validator resolves
and checks, and the JSON timeline it exports. It does **not** specify the runner (workplan
Phase 2), which does not exist; where the runner's behaviour matters, this note says what the
runner is *owed* by the format and stops there.

Companion notes: [`../workplan-choreography.md`](../workplan-choreography.md) (the plan of
record; section numbers below such as "1.2" refer to it),
[`choreo-teach-and-sync.md`](choreo-teach-and-sync.md) (teaching, music sync, audio roles),
[`behavior-api-scope.md`](behavior-api-scope.md) (slots and merge policy),
[`face-config.md`](face-config.md) (what the face may be told), and
[`../../docs/control-authority.md`](../../docs/control-authority.md) (binding).

---

## 0. Compatibility

v1 is a **strict superset** of the format `shows/hello.yaml` was written in. Every v0 show is a
v1 show and resolves to the same timeline. Nothing in `hello.yaml` changes.

A show may say `format: 1`. Absent means 1. Any other value is refused, so a future v2 file is
not silently read with v1 rules.

Rules that are **new in v1** and could in principle refuse an old show (none of them refuse
`hello.yaml`):

| new rule | why |
|---|---|
| unknown keys on a cue are refused | a typo such as `hold: 3` used to be ignored silently; a field only the author thinks matters is the mirror image of 0.4's "field only the validator knows about" |
| a `LIFT` cue owns its slot for its full `secs`, not just an instant | an `up` for 6 s followed by a `down` 2 s later used to pass, which is two commands on one pair of relays |
| `SOUND` cues own their slot for their length | one sound effect at a time per show; the Tab5's player also refuses while busy (`app_face/cues.h`) |
| `FACE` and `LIGHT` cues are validated against a closed vocabulary | 0.3: a slot either has a validator or is not in `SLOTS` |
| `SOUND` plays named sound effects | DEC-11: a sound is a named effect on the show's output device, not a synthesized tone |

---

## 1. File layout

Top-level keys, **in this order** when a tool writes a new file (an existing file's order is
never changed — see §9):

```yaml
format: 1                 # optional
id: beat-demo             # required, stable identifier
name: Beat demo           # human title
slots: [ARM_L, ARM_R, HEAD, LIFT, SOUND, FACE, LIGHT]   # the preflight declaration
requires: { arms: active, tab5: true, rc_authority: AUTO }
music: { ... }            # optional, §3 (and §3a for its output)
dance: { floor_radius_m: 1.5 }   # required if any cue is DANCE, §12
sounds: [sleigh_bells]    # optional: sound files expected in sounds/, kept locally, §3a
themes: [christmas]       # optional: authored face themes the show selects, installed on the head (§7)
palette: { label: Wave, order: 1 }   # optional: a routine the operator can pick (below)
start_countdown_s: 3      # optional: seconds from a run's GO to its first move (0-60, default 5)
device: { volume: 0.6, brightness: 0.8 }   # optional: the head's media volume and brightness at time 0 (§7)
notes: >                  # free text for humans; ignored by tools
clips: { ... }            # optional, §6
markers: [ ... ]          # optional, §8
cues: [ ... ]             # required
```

**Every number and vocabulary the validator checks is printed by `python3 cue.py describe`**
(joint ranges as written in a show, speed caps, clearance rule, head range and slew, emotes,
earcons, outputs, dance moves, `requires:` values). This spec gives the rules; that command gives
the values, from the validator's own constants, so the two cannot drift.

**`palette:`** — marks a show as a routine on the operator's palette: `label` (the button text,
required) and `order`. The operator picks one, it runs to its end, then the next is picked, in any
order (workplan 1.13). So a palette routine must **end in the rejoin state**: every arm it moves at
`pose: rest`, the head centred (pan 90, tilt 90), the state light released. The lift is not checked
(there is no measured height to rejoin to).

**`start_countdown_s:`** — the countdown between a run's GO (on the robot, a fresh press of the
deadman) and its first move, shown on the face; 0 to 60 s, default 5. A run may override it. How a run
starts is the runner's business, not the show's (`software/choreo/README.md`, "How a run starts").

**`requires:`** — `arms: active`, `tab5: true|false`, `rc_authority: STANDBY|MANUAL|AUTO`, and no
other keys. A show that moves anything (arms, grippers, head, lift, dance) must say
`rc_authority: AUTO`; arm or gripper cues need `arms: active`; head or face cues need `tab5: true`.
The runner's preflight is built from it, and anything unknown at run time refuses.

**Defaults worth knowing.** A `HEAD` cue that sets only `pan` (or `tilt`) leaves the other axis on its
current target. `DANCE` `secs` defaults to 2 and `amp` to 1. A show's length is the latest end of any
cue's span (a sound without `secs` counts its file's length, or 0.5 s). **`pose: rest`** sends an arm back to
where it hangs: every joint 0, clamped into that robot's own joint limits with no extra margin
(the elbow's hanging value sits a few degrees below the arm software's lower limit for J4). It is
resolved from each robot's limits file, so no robot's numbers are in a show, and the move there is
speed-checked like any other.

There is **no layout data in a show** — no lane heights, colours, zoom, scroll position, mute
state or x/y coordinates (standing rule 7). Those belong to the editor and live in the
browser's local storage. A show diff is always a diff of *what the robot does*.

---

## 2. Slots

`ARM_L ARM_R GRIP_L GRIP_R HEAD LIFT SOUND FACE LIGHT DANCE DEVICE` (and the twin-only `DRIVE`,
§12). One slot per independently commandable thing, one owner at a time.

**`DRIVE` is not a slot and v1 does not add it.** The reason is in `software/choreo/README.md` and
is unchanged: the robot cannot yet say where it is. **`DANCE` is the bounded alternative** (§12):
it names moves from a fixed library and can express no free-form velocity.

---

## 3. The time base: seconds *and* a musical grid

### What a cue may say

Every cue carries **exactly one** of:

| field | meaning | resolved to seconds as |
|---|---|---|
| `t: 6.0` | absolute arrangement seconds | itself |
| `beat: 13` | the 13th beat of the show's grid, **1-based**, may be fractional (`13.5` is the "and" of 13) | `offset_s + (beat − 1) · 60 / bpm` |
| `bar: "4.1"` | bar 4, beat 1, both **1-based**, as a musician counts. `"4"` means `"4.1"`. The beat part may be fractional (`"4.2.5"` is not allowed — write `beat:` for sub-beats) | `beat = (bar − 1) · beats_per_bar + beat_in_bar` |

Two of them on one cue is refused, as is none. `beat` and `bar` require a `music:` block. A cue
that resolves to a negative time is refused.

### The music block

```yaml
music:
  file: audio/beat-demo-120.wav   # relative to the show file
  bpm: 120                        # one tempo per show — a deliberate limit, see below
  offset_s: 0.5                   # where beat 1 (bar 1.1) falls, in show seconds
  beats_per_bar: 4
```

**Show time 0 is audio position 0.** There is no separate start offset: one knob fewer, and the
one that remains (`offset_s`) means the same thing in the file, in the editor and in the audio.
If the audio has a long lead-in, `offset_s` is large; that is honest.

`bpm` is checked against a **sanity bound of 20–300**, labelled as such in the code. It is not a
physical limit. Spot documents a physical reliable band for its own legs (250–450 slices/min,
i.e. 62.5–112.5 BPM); we have no equivalent measurement, and §11 shows the binding limit is the
per-joint speed cap, not the tempo.

`file` need not exist for a show to validate — motion validity does not depend on the audio.
A missing file is reported as a *notice*, not a failure. **No copyrighted audio is committed to
this repo**; the demo uses a click track generated by `software/choreo/shows/audio/make_test_audio.py`.

### Why both, and why this way round (1.2)

The workplan's reading of the precedents is that a pure grid is right with music and wrong with
speech. Spot quantises everything to ¼-beat slices with integer durations; Cozmo stores absolute
milliseconds per keyframe. Each is right for its content, and a show has both kinds: "the wrist
flicks on beat 13" and "the lift travels for 6 seconds".

So **the file records which one the author meant**, and the validator resolves both to seconds:

- A cue written in `beat`/`bar` is **bound to the grid**. Change `bpm` and it moves. That is the
  point of writing it that way.
- A cue written in `t` is **bound to the clock**. Change `bpm` and it stays.

This is a deliberate departure from xLights, whose timing marks are "a placement grid, not a
binding" — once dropped, an effect has no relation to the marks. Ours keep the relation because
the author said so in the file, and the resolved seconds are always shown beside it (Polaris:
"anything the tool infers must be shown resolved"). An editor that snaps a `t` cue to a beat
**keeps it a `t` cue** (writes the snapped seconds); it converts the form only when asked.

### One tempo per show — a recorded limit (1.3)

v1 has exactly one `bpm`. A tempo map (several tempi, or a ramp) is **deferred, deliberately**.
Spot has the same limit and documents it ("must remain constant throughout the entire script").
A show over music with a tempo change must today be split, or written in `t`. If that becomes
common the extension is `music.tempo: [{beat, bpm}, …]`, resolved piecewise; nothing in v1
prevents it.

---

## 3a. Audio routing — where sound plays (workplan DEC-5, DEC-11, item 1.5)

Music and each `SOUND` cue may name an `output`: `tablet`, `tab5` or `thor`.

```yaml
music: { file: audio/song.mp3, bpm: 120, output: tablet }       # output defaults to tablet
cues:
  - { t: 0.0, slot: SOUND, do: play, sound: attend }            # a Tab5 earcon -> the Tab5
  - { t: 4.0, slot: SOUND, do: play, sound: whoosh, output: tablet }   # sounds/whoosh.* -> the tablet
```

| output | plays today |
|---|---|
| `tablet` (the default) | files: music, and `sounds/<name>.*` |
| `tab5` | its six built-in earcons only — no path for files yet |
| `thor` | files — but no Thor speaker is fitted yet, and a notice says so |

**Sound files are kept locally, like music.** A show lists the files it expects in
`sounds: [name, …]`; a listed file that is not present is a notice, not an error, because seasonal
and borrowed sounds are not committed (`shows/sounds/` ignores everything). A name that is neither an
earcon, a file present, nor listed is still an error, so a typo is caught.

With no `output`, a sound goes to the tablet — except a Tab5 earcon with no file of the same name,
which only the Tab5 has. **The operator may route all of a show's audio elsewhere at run time**
(`runner.py --audio-output`, later the player); the show is re-checked against that choice, and a
sound the chosen output cannot play is a **validation error, never silence**. The export records
each sound's resolved `output`, and the music's.

## 4. Durations: every cue occupies a span (0.4, 1.5)

QLC+'s rule — "only Chasers can be assigned to a Cue List … the Chaser is the only function with
a duration" — is adopted: **every resolved cue has a `start` and an `end`**, and the one-owner rule
is checked on those spans. Nothing is schedulable without one.

### `hold_s`, named

`hold_s` was read by the one-owner check and documented nowhere. **v1 keeps it and defines it:**

> `hold_s` — seconds the slot stays **owned and unchanged** after the cue's own action completes.
> Default 0.

It is the "persist" half of Furhat's `persist` / BML's `faceShift`: the author is saying "nothing
else touches this slot for a while". It extends the span; it never shortens it.

### The span of each kind of cue

| slot | kind | `start` | `end` | intrinsic duration from |
|---|---|---|---|---|
| `ARM_L`, `ARM_R` | **keyframe** | end of this arm's previous keyframe (0 for the first) | `t + hold_s` | the gap to the previous keyframe — the move *arrives* at `t` (§5) |
| `GRIP_L`, `GRIP_R` | command | `t` | `t + hold_s` | **unmeasured** — gripper travel time is not known; 0 is assumed and the gap is named in the report |
| `HEAD` | command | `t` | `t + hold_s` | the head turns at **25 °/s per axis**, the Tab5 firmware's slew limit (`pantilt.cpp` `kSlewDps`); a move's travel time is not yet part of its span |
| `LIFT` `up`/`down` | command | `t` | `t + max(secs, hold_s)` | `secs` |
| `LIFT` `stop` | command | `t` | `t + hold_s` | 0 |
| `SOUND` `play` | command | `t` | `t + max(length, hold_s)` | `secs` if given, else the WAV file's length, else 0.5 s (span sizing only) |
| `FACE`, `LIGHT` | command | `t` | `t + hold_s` | 0 — a state change, not a motion |

A cue whose `start` falls before the previous cue on the same slot has ended is refused: "two
cues on one slot is a collision, not a blend" (unchanged). For a `LIFT` this means an `up` cannot
be cut short by a `stop` written inside its span — **shorten `secs` instead**. That is
deliberate: `secs` is how far the lift travels, and a show that says both "6 s" and "stop at 4 s"
has said two things.

### What the runner is owed

`start`/`end` are **the timeline's claim**, not a measurement. Where an intrinsic duration is
"unmeasured" above, the real actuator may still be moving at `end`. The runner must not treat
`end` as "arrived"; it is "no other cue will be issued to this slot before". Measuring the
gripper and head times is an experiment in `choreo-teach-and-sync.md`.

---

## 5. Keyframes and interpolation (arms)

An arm cue is a **keyframe**: `joints: {n: degrees}` is where the arm *arrives* at `t`.

- **Sparse.** Joints a keyframe does not mention keep their previous value. The full seven-joint
  pose after each keyframe is resolved and exported as `pose`.
- **Outward sense.** J1–J3 are written in each arm's own outward sense; the validator applies the
  mirror once (unchanged from v0). The export carries both the outward `pose` and the
  controller-signed `pose_signed`.
- **The start pose is all-zero.** The first keyframe's move is measured from 0° on every joint at
  t = 0. This was already the validator's assumption; v1 states it. The runner must travel to
  that pose *before* t = 0, timed by the distance actually covered (2.5), or the first segment's
  speed check is a fiction.
- **Interpolation is linear in joint space**, per joint, from the previous keyframe's `end`
  (its `t` plus its `hold_s`) to this keyframe's `t`. The speed cap is checked on exactly that
  segment, as the **average** speed over it.

### Mapping onto `JointTrajectoryPoint`

The arm's own interface is already timeline-shaped (5.1). Per arm, a contiguous run of keyframes
becomes one `FollowJointTrajectory` goal:

| show | `trajectory_msgs/JointTrajectoryPoint` |
|---|---|
| keyframe `pose_signed` (degrees, all 7) | `positions` (radians, all 7, in `openarm_<side>_joint1..7` order) |
| keyframe `t − trajectory_start` | `time_from_start` |
| `hold_s > 0` | a **second point** with the same positions at `t + hold_s` |
| (nothing) | `velocities`, `accelerations` left **empty** |

Leaving velocities empty is what asks `joint_trajectory_controller`'s spline interpolator for
**linear** interpolation; supplying velocities selects cubic, accelerations quintic.

**The unverified part, and why it matters for safety.** Whether `openarm_impedance_controller`
honours that convention is **not known**. If it interpolates with zero end-velocities (a cubic),
the peak joint speed in each segment is **1.5×** the average the validator checked; a minimum-jerk
quintic peaks at **1.875×**. The speed caps would then be exceeded mid-segment by up to that
factor while every check passes. **Decided 2026-10-07 (workplan DEC-1, item 1.1):** the average is
the hard gate, and `1.875 × average` (`PEAK_FACTOR`) is checked as a **warning** until the
controller's real profile is measured (item 6.1) — at which point the measured peak becomes the
gate. Failing shows that have run on the robot on an unmeasured factor would claim more than the
validator knows; ignoring the factor would claim less. `hello.yaml` passes with 14 such warnings.

### Interpolation modes: one, deliberately

Choregraphe offers six (Constant, Linear, Automatic Bezier, Bezier, Smooth, Symmetrical). v1 has
**linear only**. Any easing raises peak speed above the average, which is the quantity the
validator checks; adding a mode means adding its peak factor to the check at the same time.
`interp:` is reserved for that and refused today.

**Constant (step)** is excluded permanently for arms: a step is a command to arrive instantly,
which the speed cap exists to forbid.

---

## 6. Clips: reusable fragments in clip-local time (1.5, 5.3, proposal 6)

```yaml
clips:
  wave_r:
    length_beats: 4            # or length_s; required if the clip is ever repeated
    cues:
      - { beat: 1,   slot: ARM_R, joints: { 7: 15 } }     # beat 1 = the clip's own start
      - { beat: 2,   slot: ARM_R, joints: { 7: -15 } }
      - { beat: 3,   slot: ARM_R, joints: { 7: 15 } }
      - { beat: 4,   slot: ARM_R, joints: { 7: 0 } }

cues:
  - { bar: "5.1", clip: wave_r }
  - { bar: "9.1", clip: wave_r, repeat: 2, strength: 0.5 }
```

- **Clip-local time.** Inside a clip, `t: 0`, `beat: 1` and `bar: "1.1"` all mean the clip's own
  start. A clip carries its internal shape and can be relocated by changing one line — Ableton's
  clip envelopes, TouchDesigner's per-component timeline. Arrangement-time things (the music, the
  markers) do not live in clips.
- **A placement** is a cue with `clip:` and a time, and no `slot`. Optional dials:

  | dial | range | effect |
  |---|---|---|
  | `repeat` | integer ≥ 1 | instances at `start + k · length · time_scale`; a clip's length is required only when `repeat` > 1 — two separate placements need none |
  | `mirror` | `true` / `false` (default false) | plays the clip on the other side: `ARM_L`↔`ARM_R`, `GRIP_L`↔`GRIP_R`, and the head's pan reflected about 90°. Arm joints are already in each arm's outward sense, so the same numbers make the mirror-image gesture. Sound, face, light, lift and dance are unchanged |
  | `time_scale` | > 0 | multiplies clip-local **times** and `hold_s` (Furhat's `duration`) |
  | `strength` | (0, 1] | scales **amplitude**: arm joint values toward 0°, head pan/tilt toward 90° (Furhat's `strength`) |

- **What the dials do not touch.** `time_scale` does **not** scale `LIFT secs` or `SOUND secs`:
  for a bang-bang lift, `secs` is distance wearing a time unit, and scaling it would change how
  far the lift travels. `strength` does not touch grippers, the lift, sound, face or light.
- **`strength` only turns down.** Values above 1 are refused. A dial that can only shrink an
  amplitude cannot produce a pose the author never wrote larger than the original. (The expanded
  show is validated anyway; this keeps the dial honest, not the robot safe.)
- **The expanded show is what is validated.** A clip that is legal once can be illegal when
  repeated back-to-back, or when `time_scale: 0.5` doubles every speed. Expansion happens before
  any check, so both are caught, and every problem names the clip and the instance it came from.
  **`instance` counts repeats within one placement** (`#1`, `#2` … of a `repeat:`); two separate
  placements are each instance `#1` — the placement's own cue index tells them apart.
- **No nesting in v1.** A clip may not place a clip. Cheap to add; deferred until a show needs it.
- **Slots used inside a clip must be declared** in the show's `slots:`, as for any cue.

Variation (1.8, Cozmo's `variability_*` and animation groups) is **not** in v1. It is the right
next addition to clips, because a clip is the unit a variant would be chosen from; it is deferred
because validating a randomised show means validating every variant, which needs its own design.

---

## 7. Slot vocabulary

Unchanged from v0 unless marked **new**.

| slot | fields | checks | sourced from |
|---|---|---|---|
| `ARM_L`/`ARM_R` | `joints: {1..7: deg}`, or `pose: rest` | limits (arm software's `joint_limits.yaml`, 5° margin), per-joint average speed cap, shoulder/head clearance | measured on opendubs2; see `cue.py` comments |
| `GRIP_L`/`GRIP_R` | `do: open\|close` or `open: 0..1` (0 closed, 1 open — the same on both sides), `effort` | `open` within 0..1, effort ≤ 5 (the gripper controller's own units — unsourced); the old `pos: m` is refused by name | each robot's per-side mapping lives on the robot (`grippers.py`, workplan DEC-4); none of it is in the repo |
| `HEAD` | `pan`, `tilt` (deg) | 10–170 | clamp inside servo travel |
| `LIFT` | `do: up\|down\|stop`, `secs` | 0.2–8 s; **no `ack_pinch`, ever** | — |
| `SOUND` | `do: play`, `sound: <name>`, optional `secs`, optional `output: tablet\|tab5\|thor` (see *Audio routing*) | the name is a Tab5 earcon (`heard attend refuse fault mode boot`) or a file `sounds/<name>.*` beside the show | `Cue` enum in `firmware/tab5_idf/app_face/cues.h`; the show's `sounds/` directory |
| `FACE` **new** | `do: emote`, `name: NEUTRAL\|ALERT\|SQUINT\|SOFT\|SLEEPY`; or `do: theme`, `name: plain\|halloween` or an authored theme listed under `themes:` | closed vocabulary; an authored theme not listed is an error (a typo), a listed one is a notice (it must be installed on the head by its `CFG` verb first). A theme and a skin last until the show ends: every end, however it ends, sends `SKIN IDLE` and `THEME IDLE` (head protocol v5), the robot's own idle skin and theme back, and the emote is handed back to the face (`EMOTE AUTO`). `idle` is reserved: no skin or theme may be called `idle`, and a show never names it | `kEmotes[]` and `theme::name()` in `firmware/tab5_idf/app_face/app_face.cpp`, branch `feat/tab5-idf-subtractive` at `12f5845`; the head takes `EMOTE <name>` and `THEME <name>`, case-insensitively |
| `FACE` move | `do: move`, `name: wink_l\|wink_r\|blink2\|look_l\|look_r\|roll\|bob\|sparkle\|side_eye_l\|side_eye_r\|to_camera\|double_take\|startle` — a one-shot gesture over the held emote (head protocol v4, `MOVE <name>`; tablet face only) | closed vocabulary. **The cue's time is the move's PEAK** (a wink's lid fully shut, the bottom of a bob): the export carries `send_t`, the time minus the face's receipt-to-peak offset, and the face bridge (twin and robot alike) sends the line then, so the gesture lands on the beat the author wrote. A move is not state: a seek or a resync never replays it, and a new one replaces one still playing. Notices: a move too early to be sent its lead ahead; a `sparkle` on a picture skin, which draws nothing (the face answers OK) | `MOVES` in `software/face-web/engine/face.js` (Face & HRI): each move's duration and its **measured** peak offset, e.g. wink 0.139 s, blink2 0.333 s (its second shut); `cue.FACE_MOVES` copies them and `tests/test_face_moves.py` fails if either drifts, or if the names differ from `head_protocol.MOVES` |
| `LIGHT` **new** | `do: color`, `rgb: [r, g, b]` (0–255), `br` (0–100, default 100); or `do: release` | ranges; rejected rather than clamped; **no colour that reads as the state light's E-STOP red or DEGRADED amber** — within ±20° of their hue and saturated enough to read as it (plan 1.8; `cue.py describe`) | `LED_COLOR r g b br` parser in `firmware/rc_serial_probe/src/main.cpp` |

| `DEVICE` **new** (2026-10-13) | `do: volume\|brightness`, `level: 0..1`, optional `fade_s` (0–30). Show-level `device: { volume, brightness }` sets both at time 0 | ranges; a fade owns the slot for its length; needs `tab5: true`. **Brightness below 0.15 warns**: the head never dims past its own floor (`bright_min`), so it would come out brighter than written. Every show end hands both back to the head (`VOLUME IDLE`, `BRIGHT IDLE`) | head protocol v5, agreed with the Tablet lane: `VOLUME <0..1> [fade_s]`, `BRIGHT <0..1> [fade_s]`, `VOLUME IDLE`, `BRIGHT IDLE`; the reply carries the level applied. VOLUME is the head's **media volume** — what the show audio plays at — not a gain in the player |

`LIGHT`'s second verb is `release`, not `off`, for two reasons. YAML 1.1 reads a bare `off` as
the boolean `false` (the validator names this trap when it sees it — the first draft of the demo
show fell into it). And `release` is what it means: the show stops asserting a colour and the
robot's own state colour shows through again — the lighting world's *release*, which is not the
same as blacking the light out (workplan 2.4).

### Self-collision: the sweep and its stamp (the robot gate)

A show that moves an arm carries, written by `software/choreo/sweep.py` (the composer runs it on
every save):

```yaml
clearance: { min_mm: 38.9, at_s: 0.0, between: "… / …", checked_for: "<motion hash>@<model sig>" }
```

the closest any part of the robot came to any other over the WHOLE show — on motion.py's smoothed
path, the one the robot plays — against the twin's collision model (arm–arm, arm–head, arm–body),
with the head, lift and grippers where the show has them. The sweep samples every 80 ms and at every
keyframe, then halves any interval where the joints moved enough to hide a dip below the threshold,
so the minimum is bounded, not a sampling accident. The robot validates without MuJoCo: it recomputes
the motion hash from the show and refuses an arm show whose stamp is missing, stale (any arm,
gripper, head, lift or base change since) or under **30 mm** — the margin `ik.py` keeps for a solved
target. A face, light or sound edit does not stale it. In the composer a missing or stale stamp is a
warning until the save sweeps it; a measured collision is an error everywhere. Cost: about 0.5–2 s
for the shows in this repository, up to ~10 s for a dense 2½-minute show; an unchanged show is not
swept again.

### Valid is not the same as executable

Some verbs above **have no receiver today**, and the validator says so as a *notice* (not a
failure) every time a show uses them. (`FACE` has one: the ESP-IDF head takes `EMOTE` and `THEME`.
A locked robot state — `INIT`, `DEGRADED`, `E_STOP` — still outranks an emote on the face, by
design, so a `FACE` emote shows once that state has had its turn.)

- `LIGHT` — `LED_COLOR` exists, but `software/kiosk-dashboard/backend/state_out.py` already
  writes it on every robot-state change. A show writing it too is two writers on one register;
  the runner must layer it (`behavior-api-scope.md`: `LIGHT` is *layered*) before it may run.
  The PRISM's firmware overlays (`NOT_READY` and friends) outrank the base layer, so a show
  cannot paint over them.
- `SOUND play` of a **file** — the tablet and Thor audio paths are not built (workplan 4.2). A Tab5
  earcon does have a receiver: the head's `CUE <name>`.

Recording these as notices rather than refusing them keeps the vocabulary designable ahead of the
firmware, while making it impossible to mistake "the validator accepted it" for "the robot will
do it".

---

## 8. Markers emit named cues, never device addresses (5.6)

```yaml
markers:
  - { bar: "9.1", name: chorus }
  - { t: 30.0,   name: disco_ball_on }
```

A marker is a **named signal on the arrangement**, with a time in any of the three forms. It
commands nothing. The table from signal name to consequence is a per-robot receiver table that
lives on the robot (Unity's Signal Receiver; `per-robot-values-not-in-repo`), and **no receiver
exists in v1** — markers are inert, shown in the editor and exported.

Names match `^[a-z][a-z0-9_]{0,47}$`. Anything that reads as an address is refused:
`relay…`, `r0`–`r9`, `gpio…`, `pin…`, `ch<n>`, `udp…`. "A marker that says `RELAY 3` has put
per-unit wiring into a committed show. It must say `disco_ball_on`."

---

## 9. Serialisation rules for tools

An editor **edits the text, not a re-dump of it**. PyYAML does not preserve comments, and
`hello.yaml` carries its reasoning in comments, so a load-and-dump round trip would destroy the
most valuable part of the file. The composer therefore:

- replaces only the source span of a cue it changed, re-written as a one-line flow mapping with
  the cue's **original key order**, new keys appended;
- appends new cues as new lines at the end of `cues:`;
- removes a deleted cue's lines and nothing else;
- changes a `music:` scalar in place.

An unedited file round-trips **byte-identical**; one edited cue is a one-line diff. Number
formatting: the shortest form that round-trips (`3`, `3.5`, `0.125`), times rounded to the
millisecond.

---

## 10. The resolved timeline (`cue.py export`)

The **one artifact** the composer, the simulator preview and the future runner all consume. It is
what the show *means* after every inference is made explicit: times in seconds, clips expanded,
sparse keyframes filled, mirrors applied, spans computed.

```bash
python3 cue.py export shows/beat-demo.yaml --limits <joint_limits.yaml> [-o out.json]
```

The CLI **refuses to export an invalid show** (exit 1, nothing written) unless
`--allow-invalid`, in which case `valid` is `false`. A runner must refuse a timeline with
`valid: false`, and must re-validate rather than trust the file — the export is a convenience, not
a certificate.

Schema `opendubs.choreo.timeline/1`. JSON, `sort_keys`, two-space indent; times rounded to 1 µs,
angles to 10⁻⁴ °. Two exports of the same show and limits are byte-identical.

```jsonc
{
  "schema": "opendubs.choreo.timeline/1",
  "show": { "id": "beat-demo", "name": "Beat demo", "source": "shows/beat-demo.yaml", "format": 1 },
  "validated_against": { "limits": "joint_limits.yaml", "sha256": "…" },
  "valid": true,
  "problems": [ { "msg": "…", "cue": 12, "clip": null, "instance": null } ],
  "notices":  [ "DANCE is valid but runs only in the twin until the robot floor test (workplan 6.6)…" ],
  "requires": { "arms": "active", "rc_authority": "AUTO", "tab5": true },
  "slots": ["ARM_L", "ARM_R", "HEAD", "LIFT", "SOUND", "FACE", "LIGHT"],
  "music": { "file": "audio/beat-demo-120.wav", "bpm": 120, "offset_s": 0.5,
             "beats_per_bar": 4, "beat_s": 0.5 },
  "duration_s": 34.5,
  "markers": [ { "t": 16.5, "name": "chorus", "src": { "index": 0, "time": "bar 9.1" } } ],
  "tracks": {
    "ARM_R": [
      { "t": 2.5, "start": 0.0, "end": 2.5, "slot": "ARM_R", "kind": "keyframe",
        "cue": { "joints": { "2": 20, "4": 55 } },
        "pose":        { "1": 0, "2": 20,  "3": 0, "4": 55, "5": 0, "6": 0, "7": 0 },
        "pose_signed": { "1": 0, "2": 20,  "3": 0, "4": 55, "5": 0, "6": 0, "7": 0 },
        "src": { "index": 4, "time": "beat 5", "clip": null, "instance": null, "local_index": null } }
    ],
    "LIFT": [
      { "t": 8.5, "start": 8.5, "end": 12.5, "slot": "LIFT", "kind": "command",
        "cue": { "do": "up", "secs": 4 }, "src": { "index": 9, "time": "bar 5.1", … } }
    ]
  }
}
```

- `tracks` has a key for **every declared slot**, empty lists included, each sorted by `start`
  then source order.
- `src.index` is the cue's position in the file's `cues:` list (0-based, as authored). For a cue
  that came from a clip, `index` is the **placement** cue, and `clip` / `instance` /
  `local_index` locate it inside the clip. This is how an editor highlights the offending cue.
- `problems[].cue` uses the same `index`. `problems[].msg` is the same sentence `cue.py validate`
  prints.
- **Untyped messages are a known debt.** Spot's untyped `repeated string warnings` is the
  counter-example in proposal 8; v1 still emits sentences plus a location. Typed codes
  (`IMPOSSIBLE_TO_SCHEDULE`, `NO_SUCH_TARGET`, …) are the next step and need no format change.

---

## 11. The capability layer — the gap, proposed, not built (1.6)

Shows name **raw joint indices**: `joints: {2: 20, 4: 55}`. That works because there is one kind
of arm and two robots built alike. It fails the portability test the lighting world solved
decades ago (QXF, GDTF): a show is not about "joint 2", it is about "raise the shoulder".

Proposal, for a later format version:

1. **A device-type file per actuator kind**, committed (GDTF's role): named attributes
   (`shoulder_raise`, `elbow_fold`, `wrist_roll`, `pan`, `tilt`, `lift_height`), each with its
   joint mapping, unit, sense and the *source* of every bound. The mirror table and the
   clearance rule move here from `cue.py` constants.
2. **A per-robot instance file**, on the robot and never committed (MVR's role): calibration,
   gripper zeros, which relay is which. `per-robot-values-not-in-repo` already draws this line.
3. **Shows name attributes**: `ARM_R: {shoulder_raise: 20, elbow_fold: 55}`. The validator
   resolves attribute → joint through the device type.

Not built because one robot design has no portability problem to solve yet, and the attribute
vocabulary should be chosen by people writing shows, not invented ahead of them. Raw indices
remain valid in every version.

---

## 12. What is explicitly out

| out | why | where it would go |
|---|---|---|
| **`DRIVE` on the robot** | no trustworthy odometry; "keep X and Y clear" is advice the robot cannot honour. **Twin-only since 2026-10-11** (the user's composer item #4): a `DRIVE` cue is a relative move — `{ slot: DRIVE, forward_m, left_m, turn_deg, secs }`, in the robot's frame at the cue, no velocities, no room coordinates — bounded by the autonomy ceiling at the placeholder base speeds, counted in the 30 s open-loop cap with DANCE, needing the show's floor space and warning when its path leaves it. The twin drives it; **the runner refuses any show that uses it**, and the palette lists such a show as not runnable. | odometry first; then the robot |
| Resume-from-middle as recovery | standing rule 2: once a human intervened, the robot is not where the timeline assumes | may exist as an *authoring* start marker, simulator-only |
| Node graphs | 5.7: graphs cannot state timed order honestly | a trigger-wiring *view* over the same text, later |
| Tempo map | §3; recorded limit | `music.tempo` |
| BML sync points (1.4) | needed once the robot speaks during a show; nothing speaks yet | `sync:` constraints between cues, resolved by the validator to seconds |
| Variation (1.8) | §6 | on clips |
| Face configuration as show data | Phase 4; blocked on firmware prerequisites (0.2 is done on the IDF branch, not merged; 4.1–4.5 open) | — |
| Session / cue-stack scheduling (5.5) | timeline first; the same clips will serve both | a second scheduler over `clips:` |

### Bounded open-loop dance — adopted 2026-10-07 (workplan DEC-2, item 1.4)

**As built.** A restricted `DANCE` slot rather than a separate block: what had to be ruled out was
free-form velocity, and a slot whose only key is a library move rules it out while keeping the time
base, clips, one-owner rule and composer lane. The show declares `dance: { floor_radius_m: … }`.

```yaml
dance:
  floor_radius_m: 1.5                     # clear radius; a human confirms it before the base moves
cues:
  - { bar: "5.1", slot: DANCE, move: sway, secs: 2 }
  - { bar: "7.1", slot: DANCE, move: twist, secs: 2, amp: 0.6 }
```

| rule | value | kind |
|---|---|---|
| moves | `sway rock twist shuffle box`, each a fixed profile that integrates to zero on every axis (re-checked) | library (`cue.py` `DANCE_MOVES`) |
| speed | `amp` in (0, 1]; 1 = the autonomy ceiling `kAutoMaxUnit` 0.35, then CH8 in a human's hand | firmware constant |
| one move | 0.5–4 s, default 2 s | sanity |
| whole show | ≤ 30 s of base motion | sanity |
| floor | `floor_radius_m` required, 0.5–10 m; warns if a move may stray past it (with the base's ~0.46 m footprint, arms not counted) | declaration + estimate |
| robot | valid, but runs **only in the twin** until the floor test (plan 6.6); a notice says so every time | — |

How far a move strays and how the twin draws it use **placeholder speeds** (0.70 m/s and 1.40 rad/s
at full command, from the simulator), not measurements.

The original proposal follows.

The base is wanted ("dancing") and refused today for a stated reason. If it is to be allowed
before odometry is earned, the only defensible shape is **bounded by something other than
position**:

- **Not a slot.** A separate top-level `dance:` block of named, pre-validated moves (`sway`,
  `shuffle_left`, `spin_quarter`), each a fixed open-loop velocity profile — never free-form
  velocities in a cue.
- **Time-limited**: each move ≤ a few seconds, and a show-wide cap on total base-motion time.
- **Speed-capped** at the existing autonomy scale (`kAutoMaxUnit` 0.35 × CH8), and CH8 **scales
  rates, not targets**, in a human's hand. The deadman held throughout; release aborts.
- **Declared floor space**: the show states the clear radius it needs (Spot: 3 m), and the
  runner's preflight asks a human to confirm it. A *declaration*, because the robot cannot check
  it.
- **Net displacement ≈ 0 by construction** — moves come in cancelling pairs, so open-loop drift is
  the only thing that accumulates.

It changes `SLOTS` and, when it runs on the robot, the authority model's treatment of the base and
the runner — both still ahead (plan 5 and 6.6).
