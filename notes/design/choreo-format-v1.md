# Show format v1 — the specification

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
| `SOUND` cues own their slot for their audible length | the Tab5's player under ESP-IDF **refuses while busy** (`app_face/cues.h`), so an overlapping second sound is dropped, not mixed |
| `FACE` and `LIGHT` cues are validated against a closed vocabulary | 0.3: a slot either has a validator or is not in `SLOTS` |
| the `SOUND` sanity bound's upper edge is 8000 Hz, was 12000 | the only receiver that implements `BEEP` (`firmware/tab5_face/src/main.cpp`) clamps to 8000 Hz; a validator must not pass what the receiver silently changes. This is the receiver's code, not a speaker measurement, and the comment says so |

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
music: { ... }            # optional, §3
notes: >                  # free text for humans; ignored by tools
clips: { ... }            # optional, §6
markers: [ ... ]          # optional, §8
cues: [ ... ]             # required
```

There is **no layout data in a show** — no lane heights, colours, zoom, scroll position, mute
state or x/y coordinates (standing rule 7). Those belong to the editor and live in the
browser's local storage. A show diff is always a diff of *what the robot does*.

---

## 2. Slots

`ARM_L ARM_R GRIP_L GRIP_R HEAD LIFT SOUND FACE LIGHT` — unchanged. One slot per independently
commandable thing, one owner at a time.

**`DRIVE` is not a slot and v1 does not add it.** The reason is in `software/choreo/README.md` and
is unchanged: the robot cannot yet say where it is. §12 records the bounded open-loop proposal as
a design, not as format.

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
this repo**; the demo uses a click track generated by `software/choreo/audio/make_test_audio.py`.

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
| `HEAD` | command | `t` | `t + hold_s` | **unmeasured** — servo slew rate is not known |
| `LIFT` `up`/`down` | command | `t` | `t + max(secs, hold_s)` | `secs` |
| `LIFT` `stop` | command | `t` | `t + hold_s` | 0 |
| `SOUND` `beep` | command | `t` | `t + max(ms/1000, hold_s)` | `ms`, default 120 (the firmware's default) |
| `SOUND` `chime` | command | `t` | `t + max(0.39, hold_s)` | 0.39 s = the three tones in `tab5_face/src/main.cpp`, **if** `M5.Speaker.tone` plays them sequentially — not verified |
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
factor while every check passes. Two answers are possible and the choice is Emma's (report,
decision list): measure the controller's actual profile on the robot, or have the validator check
`1.875 × average` against the cap until it is measured. v1 ships checking the average, unchanged,
so `hello.yaml` keeps passing.

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
  | `repeat` | integer ≥ 1 | instances at `start + k · length · time_scale` |
  | `time_scale` | > 0 | multiplies clip-local **times** and `hold_s` (Furhat's `duration`) |
  | `strength` | (0, 1] | scales **amplitude**: arm joint values toward 0°, head pan/tilt toward 90° (Furhat's `strength`) |

- **What the dials do not touch.** `time_scale` does **not** scale `LIFT secs` or `SOUND ms`:
  for a bang-bang lift, `secs` is distance wearing a time unit, and scaling it would change how
  far the lift travels. `strength` does not touch grippers, the lift, sound, face or light.
- **`strength` only turns down.** Values above 1 are refused. A dial that can only shrink an
  amplitude cannot produce a pose the author never wrote larger than the original. (The expanded
  show is validated anyway; this keeps the dial honest, not the robot safe.)
- **The expanded show is what is validated.** A clip that is legal once can be illegal when
  repeated back-to-back, or when `time_scale: 0.5` doubles every speed. Expansion happens before
  any check, so both are caught, and every problem names the clip and the instance it came from.
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
| `ARM_L`/`ARM_R` | `joints: {1..7: deg}` | limits (arm software's `joint_limits.yaml`, 5° margin), per-joint average speed cap, shoulder/head clearance | measured on opendubs2; see `cue.py` comments |
| `GRIP_L`/`GRIP_R` | `do: open\|close` or `pos: m`, `effort` | per-side range, effort ≤ 5 | measured per side |
| `HEAD` | `pan`, `tilt` (deg) | 10–170 | clamp inside servo travel |
| `LIFT` | `do: up\|down\|stop`, `secs` | 0.2–8 s; **no `ack_pinch`, ever** | — |
| `SOUND` | `do: beep` + `hz`, `ms`; or `do: chime` | 150–8000 Hz **sanity bound**, 10–3000 ms | `BEEP`/`CHIME` handler in `firmware/tab5_face/src/main.cpp` |
| `FACE` **new** | `do: emote`, `name: NEUTRAL\|ALERT\|SQUINT\|SOFT\|SLEEPY` | closed vocabulary | `kEmotes[]` in `firmware/tab5_idf/app_face/app_face.cpp`, branch `feat/tab5-idf-subtractive` at `0e47085` |
| `LIGHT` **new** | `do: color`, `rgb: [r, g, b]` (0–255), `br` (0–100, default 100); or `do: off` | ranges; rejected rather than clamped | `LED_COLOR r g b br` parser in `firmware/rc_serial_probe/src/main.cpp` |

### Valid is not the same as executable

Two verbs above **have no receiver today**, and the validator says so as a *notice* (not a
failure) every time a show uses them:

- `FACE emote` — neither face firmware has a network verb for an emote. The Arduino face takes
  `STATE <name>`; the ESP-IDF face takes `STATE`, `PING`, `REBOOT`, `OTA` (`app_face/net.cpp`).
  Workplan 3.3 owns the verb. Note also that the face pins an emote in `INIT`, `DEGRADED` and
  `E_STOP`, so even once the verb exists a `FACE` cue in those states is ignored by design.
- `LIGHT` — `LED_COLOR` exists, but `software/kiosk-dashboard/backend/state_out.py` already
  writes it on every robot-state change. A show writing it too is two writers on one register;
  the runner must layer it (`behavior-api-scope.md`: `LIGHT` is *layered*) before it may run.
  The PRISM's firmware overlays (`NOT_READY` and friends) outrank the base layer, so a show
  cannot paint over them.
- `SOUND beep`/`chime` — implemented by the Arduino face only. **The ESP-IDF face, which is
  replacing it, implements neither**; it has six earcons (`Heard Attend Refuse Fault Mode Boot`)
  and no network verb to trigger them. When the port lands, `SOUND` loses its receiver.

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
  "notices":  [ "FACE emote: valid, but no firmware implements it yet (workplan 3.3)" ],
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
| **`DRIVE`** | no trustworthy odometry; "keep X and Y clear" is advice the robot cannot honour. **Not added to `SLOTS`.** | bounded open-loop proposal below |
| Resume-from-middle as recovery | standing rule 2: once a human intervened, the robot is not where the timeline assumes | may exist as an *authoring* start marker, simulator-only |
| Node graphs | 5.7: graphs cannot state timed order honestly | a trigger-wiring *view* over the same text, later |
| Tempo map | §3; recorded limit | `music.tempo` |
| BML sync points (1.4) | needed once the robot speaks during a show; nothing speaks yet | `sync:` constraints between cues, resolved by the validator to seconds |
| Variation (1.8) | §6 | on clips |
| Face configuration as show data | Phase 4; blocked on firmware prerequisites (0.2 is done on the IDF branch, not merged; 4.1–4.5 open) | — |
| Session / cue-stack scheduling (5.5) | timeline first; the same clips will serve both | a second scheduler over `clips:` |

### Bounded open-loop dance move — a design, and an open decision (workplan proposal 3)

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

This changes `SLOTS`, the authority model's treatment of the base, and the runner; it is Emma's
call and is in the report's decision list. Nothing in v1 implements it.
