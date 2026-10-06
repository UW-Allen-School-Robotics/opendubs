# Choreography

A show is **data**, not code: a cue sheet listing what happens to which part of the robot, at
what time. It is validated before anything moves, and the same validation runs here, in CI
(`.github/workflows/choreo.yml`), and — once the runner exists — on the robot immediately before
a show is allowed to start.

```bash
python3 cue.py validate shows/*.yaml --limits <arm software>/hw_validation/joint_limits.yaml
python3 cue.py export shows/beat-demo.yaml --limits <...> -o beat-demo.json   # resolved timeline
python3 tests/test_cue.py          # the checks, checked — no robot needed
python3 shows/audio/make_test_audio.py   # the generated click track beat-demo.yaml plays to
```

The format is specified in [`notes/design/choreo-format-v1.md`](../../notes/design/choreo-format-v1.md).
Write a show from that page; this README is the summary.

## Slots

`ARM_L`, `ARM_R`, `GRIP_L`, `GRIP_R`, `HEAD`, `LIFT`, `SOUND`, `FACE`, `LIGHT`.

One slot per independently commandable thing, one owner at a time. Two cues driving the same
slot is a collision, not a blend — that is the bug class that made the lift relays chatter when
an idle head and a script both spoke to the Z-axis.

**`DRIVE` is deliberately absent.** The base does not move under a show until odometry can say
where the robot is: opendubs2 currently reports `y_mm` as NaN with a climbing failure count, so
"keep X and Y clear" is advice the robot cannot honour. Adding the slot is easy; earning it is
the work.

## Time

A cue says **when** in exactly one of three ways: `t:` seconds, or — with a `music:` block —
`beat:` (1-based, may be fractional) or `bar: "4.1"`. A cue written on the grid moves when the
tempo changes; a cue written in seconds does not. The file records which the author meant.

Every cue occupies a **span**. An arm cue is a keyframe the arm *arrives* at; its span is the move
into it. A lift cue owns the lift for its `secs`; a sound for its length. `hold_s` keeps a slot
owned and unchanged for longer.

**Clips** are named fragments in clip-local time, placed with optional `repeat`, `time_scale`
and `strength`. The show is validated **after** expansion, so a clip that is legal once is still
caught when repeated too fast.

## What validation enforces

- Every waypoint inside the arm's **own** limits (read from the arm software's
  `joint_limits.yaml`, never copied into this repo) with a 5° margin.
- A per-joint speed cap — "over three seconds" hides a 45°/s elbow. Checked as the **average**
  over each segment; see the spec, §5, for why that matters.
- Gripper travel **per side**: the two were zeroed in opposite physical states, so one range
  cannot serve both.
- **Shoulder/head clearance**, measured on the robot: the arm reaches the head at J2 182° with
  the elbow folded, and *nothing in the arm controller stops it* — its only Cartesian guard is a
  1.2 m box that pose satisfies. A show must leave 25°, and may not raise an extended arm past
  ~135° (measured: 140 accepted, 146 refused on the box).
- Beep frequency inside a **sanity bound** (150–8000 Hz) that catches typos and unit mix-ups. It
  is not a measured speaker response — the earlier "what the speaker carries" band was a by-ear
  judgement and was withdrawn. The upper edge is where the firmware clamps.
- `FACE` and `LIGHT` against closed vocabularies taken from the firmware that would receive them.
- Markers that name signals (`disco_ball_on`), never wiring (`relay3_on`).
- No unknown keys: a typo is an error, not a silently ignored field.
- **No show may acknowledge the pinch guard.** Consent to descend through the zone is a human's
  decision, every time.

Angles for the shoulder joints are written in each arm's **own outward sense**, so a show reads
the same for both sides; the mirror is applied once, in the validator.

**Valid is not executable.** `FACE emote` and `LIGHT` have no receiver yet, and `SOUND`'s
`beep`/`chime` exist only on the Arduino face the ESP-IDF port is replacing. The validator prints
a `note:` for each every time; see the spec, §7.

## The composer

[`composer/`](composer/README.md) — a timeline editor in the browser, **preview only**: it edits
show files, shows the validator's verdict on the offending cue, and plays the music against the
grid. It cannot command the robot.

## Not here yet

Execution. The runner — slot claims with heartbeat leases, preflight built from each show's
`requires`, abort on e-stop, authority change, fault or watcher loss — lives on the Thor and is
the next piece. Design: `notes/design/behavior-api-scope.md` and `notes/workplan-choreography.md`
Phase 2. It will consume the JSON that `cue.py export` writes.
