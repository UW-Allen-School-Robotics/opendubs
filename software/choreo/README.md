# Choreography

> **Numbering.** Item numbers such as (1.5) or (5.4), and "workplan proposal n", cite the plan as it stood when this was written, now kept as [`../../notes/design/choreo-prior-art.md`](../../notes/design/choreo-prior-art.md). The current plan of record is [`../../notes/workplan-choreography.md`](../../notes/workplan-choreography.md).

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

`ARM_L`, `ARM_R`, `GRIP_L`, `GRIP_R`, `HEAD`, `LIFT`, `SOUND`, `FACE`, `LIGHT`, `DANCE`.

One slot per independently commandable thing, one owner at a time. Two cues driving the same
slot is a collision, not a blend — that is the bug class that made the lift relays chatter when
an idle head and a script both spoke to the Z-axis.

**`DRIVE` is deliberately absent.** The base does not move under a show until odometry can say
where the robot is: opendubs2 currently reports `y_mm` as NaN with a climbing failure count, so
"keep X and Y clear" is advice the robot cannot honour. Adding the slot is easy; earning it is
the work. **`DANCE` is the bounded alternative**: named moves from a fixed library, each returning
the base to where it started, a declared floor radius, capped speed and time — and, for now, run
only in the twin (format spec §12).

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
- A per-joint speed cap — "over three seconds" hides a 45°/s elbow. The **average** over each
  segment must be under the cap; the **eased peak** (average × 1.875) over the cap is a
  **warning** until the arm controller's real speed profile is measured. The caps themselves are
  unsourced, and every message says so. See the spec, §5.
- Grippers as **`open: 0..1`** (0 closed, 1 open), the same on both sides. The two were zeroed in
  opposite physical states, so each robot's mapping to its controller is measured on that robot and
  kept there (`grippers.py`; `grippers.py template` prints the file to fill in). None of it is in
  this repository.
- **Shoulder/head clearance**, measured on the robot: the arm reaches the head at J2 182° with
  the elbow folded, and *nothing in the arm controller stops it* — its only Cartesian guard is a
  1.2 m box that pose satisfies. A show must leave 25°, and may not raise an extended arm past
  ~135° (measured: 140 accepted, 146 refused on the box).
- `SOUND` names a known sound: one of the Tab5's earcons, or a file in `sounds/` beside the show —
  and the output it is routed to (tablet, Tab5 or Thor; format spec §3a) must be able to play it.
- `FACE` and `LIGHT` against closed vocabularies taken from the firmware that would receive them.
- Markers that name signals (`disco_ball_on`), never wiring (`relay3_on`).
- No unknown keys: a typo is an error, not a silently ignored field.
- **No show may acknowledge the pinch guard.** Consent to descend through the zone is a human's
  decision, every time.

Angles for the shoulder joints are written in each arm's **own outward sense**, so a show reads
the same for both sides; the mirror is applied once, in the validator.

**Valid is not executable.** `FACE emote`, `LIGHT` and `SOUND` have no receiver yet —
no output device takes their commands over the network. The validator prints
a `note:` for each every time; see the spec, §7.

## The runner (core)

`runner.py run shows/hello.yaml` plays a show as the robot will — VALIDATING, PREFLIGHT (from
`requires:`, refusing anything unknown), PREPPING (a move to the start pose, timed by distance),
WAITING_FOR_START, RUNNING, then COMPLETED, ABORTED, ESTOPPED or REFUSED — but drives **only the
twins**: it serves the same feed as the composer on :8092 (`./twin.sh --composer
http://127.0.0.1:8092`, `face_bridge.py --composer …`). An e-stop ends a show at once; a released
deadman, CH5 leaving the required mode, a fault, an unknown state or an operator stop aborts it,
leaving each slot in the state `ABORT_STATE` argues for; it never pauses. The robot's state is a
fake you can flip (`curl -X POST localhost:8092/api/sim/deadman/release`, `…/estop`, `…/ch5/manual`).
Every status change and abort cause is a JSON line (`--log`). Live output is not built (plan 5.6).

**How a run starts** (`--start`, or the Shows page's Start picker; plan 5.7). `deadman` — the robot's
default — passes every check but the deadman and goes ARMED_WAITING: nothing moves, the show audio
loads, and an e-stop, CH5 leaving AUTO, a fault or the arms going inactive disarms it. The GO is a
FRESH press: a deadman already held at Start must be let go and pressed again, and letting go while
armed is not an abort. No GO in 300 s refuses the run (`START_TIMEOUT`). Then a COUNTDOWN (5 s, a show's
`start_countdown_s`, or the run's own `--countdown`), the start-pose move and the 2 s lead; from the GO
on, letting go aborts. `countdown` — a twin's default — needs the deadman held at Start and counts down
at once; `now` skips the countdown. On a live run the face shows it: `ARMED WAIT`, then `ARMED <seconds>`
at the GO, `ARMED OFF` when the show starts or ends (head protocol v5).

**The routine palette** — `runner.py palette` lists the shows marked `palette:` (each ending at
rest, so any can follow any) and runs the one the operator picks: `GET /api/routines`,
`POST /api/run/<id>` (refused while another runs), `POST /api/stop`, `GET /api/palette`. Same twin
feed, so the twins follow whichever routine is playing. The dashboard's **Shows** page (plan 4.4)
puts these buttons on the tablet.

**On the robot** (plan 4.4c) it runs as the `opendubs-runner` service — `palette --robot dashboard`
on 127.0.0.1:8092, installed by the dashboard's `deploy.sh` and `deploy/provision-runner.sh`. Its
preflight and aborts answer to the real robot, read-only, through the dashboard's
`GET /api/choreo/robot`; it has no `/api/sim/*` switches; and it reads the arms' joint limits in
place from the arm stack (no file, no routines — each is listed as refused, saying why). It still
drives only the twins.

**Show audio** (plan 4.2) — with `--audio dashboard` the runner plays a show's music and tablet
sound files on the dashboard's show-audio device (the tablet, or whichever browser pressed *Play show
audio here*): it has the device download them while the arms travel to the start pose, then names
show time 0 as a Thor time the device has matched its clock to. An abort fades, an e-stop cuts; no
device ready means the routine runs silently, with a warning. Details in
`software/kiosk-dashboard/backend/show_audio.py`.

## The composer

[`composer/`](composer/README.md) — a timeline editor in the browser, **preview only**: it edits
show files, shows the validator's verdict on the offending cue, and plays the music against the
grid. It cannot command the robot.

## The twins

`./twin.sh` is the body (MuJoCo) and `./face.sh` the face — the head's own face code built for the
desktop, driven by `face_bridge.py`. With both running, the body twin's head shows the face twin's
own picture (`face_frames.py`), so one window shows the whole robot. Both follow the composer by default, or the runner with
`--composer http://127.0.0.1:8092`. Details in [`composer/README.md`](composer/README.md).

## Not here yet

Live output: nothing here commands the robot. The runner drives only the twins until the armed
live binding is built (`notes/workplan-choreography.md`, 5.6).
