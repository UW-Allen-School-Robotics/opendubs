# OpenDubs shows: reference

`P=software/choreo`. Every tool's docstring is its manual: `sed -n 1,40p $P/<tool>.py`.

## The show file (format: notes/design/choreo-format-v1.md)

```yaml
id: my_show
name: "My show"
palette: { label: "My show", order: 50 }        # without it the robot's Shows page doesn't list it
slots: [ARM_L, ARM_R, GRIP_L, GRIP_R, HEAD, FACE, LIGHT, SOUND, VIDEO, DANCE, CRAWL]
requires: { arms: active, tab5: true, rc_authority: AUTO }
music: { song: my-song, bpm: 120, offset_s: 0.5, beats_per_bar: 4, role: music }
can_skip: [floor_hand]                           # parts the show can run without, on approval
video: { required: false }                       # true: refuse rather than run without the video
dance: { floor_radius_m: 1.5 }                   # needed when DANCE moves the base
timeline_notes: [...]                            # the notes loop (timeline_notes.py)
clips: { ... }                                   # reusable fragments, timed in beats
cues:
  - { bar: "1.1", slot: ARM_L, joints: { 1: 20, 2: 15, 4: 60 } }   # degrees, the outward convention
  - { bar: "2.1", slot: ARM_R, reach: head_screen }                  # IK target (ik.py compile)
  - { bar: "3.1", clip: swim, mirror: true, time_scale: 1.0 }
  - { t: 0, slot: VIDEO, do: play, video: my-video }
  - { bar: "4.1", slot: SOUND, do: play, sound: hello, role: dialogue }
clearance: { min_mm: 41.2, at_s: 3.8, checked_for: "..." }         # written by sweep.py
```

- Slots: `cue.SLOTS`. Times: `t:` seconds, `beat:`, `bar: "bar.beat"`. Every cue occupies a span.
- `python3 $P/cue.py describe --limits L` prints every limit and vocabulary the validator applies;
  `cue.py export SHOW` prints the resolved JSON timeline (what a show means after clips and IK).
- Reusable moves are clips: a `clips:` block of named fragments timed in beats, copied between shows
  and placed with `repeat`, `time_scale`, `mirror`.

## Commands

| Task | Command |
|---|---|
| Validate | `uv run --with pyyaml --with mujoco --with numpy python3 $P/cue.py validate SHOW --limits L` |
| Solve IK targets | `python3 $P/ik.py compile SHOW` (`check` re-solves without writing) |
| Clearance sweep | `python3 $P/sweep.py check SHOW` / `stamp SHOW` |
| Render | `python3 $P/render.py SHOW --out x.mp4 [--layout a\|b\|c] [--from S --to S] [--size WxH] [--seed N]` |
| Notes | `python3 $P/timeline_notes.py list\|prompt\|snapshot\|reply\|history SHOW` |
| Library | `OPENDUBS_LIBRARY=~/opendubs-library python3 $P/library.py add FILE --id ID --kind song\|clip\|skin\|video --source S --licence L [--song ID]` |
| Library → robot | `python3 $P/library_sync.py status\|push\|delete <robot> ...` |
| Show → robot | `python3 $P/show_sync.py push <robot> SHOW [--dry-run --limits-from-robot]` |
| Tool registry | `python3 $P/effectors.py list` (hands, grippers, what is fitted) |
| Hand sequence | `python3 $P/cue.py export-effector SHOW -o seq.json [--from S --to S]` (the floor hand as the real hand plays it) |

Render layouts: `a` the robot alone (the face on its head screen), `b` robot with a face inset, `c`
robot and face side by side. A show's VIDEO plays on the twin's projection screen.

## Outputs

| Output | Slot / field | Where it plays | Notes |
|---|---|---|---|
| MuJoCo twin | all motion, VIDEO (CRAWL in renders) | the studio window, renders | real meshes, limits and collision model |
| Face | FACE | the face page (twin) / the head tablet (robot) | |
| Dialogue | SOUND, `music:` with role dialogue | the head tablet | the default role |
| Music | `music:` role music, or a video with `song:` | the projector or stage box; tablet fallback | |
| Projector video | VIDEO (`output: cast\|hdmi\|stage\|off`) | Chromecast, HDMI, or the stage page | not ready → runs without it, unless `video: {required: true}` |
| Stage page | VIDEO on `stage` | `/stage/` on a stage box (Pi) or any browser | box's local copy, else streamed with a 25 s head start; a stall hands music to the tablet |
| Live skeletons | (none) | the stage page | skeleton points only; no camera image is ever shown, sent or stored |
| Floor hand | CRAWL, `tools: base:`, `crawler:` | the real Surge hand on a tether (`--hand PORT`) | part `floor_hand`; untried moves play and are logged; fingers open at every end |
| Base | DANCE | the Thor's base tracker (`/dance/*`) | moves return home; DRIVE stays twin-only |
| Lift, light | LIFT, LIGHT | through the dashboard (`--lift/--light dashboard`) | |

## The armed run (the robot)

The runner refuses a show that fails validation, has a stale sweep stamp, or needs a part that is down
(unless `can_skip` and the operator approves). Then the run is ARMED: the media load (the tablet, the
projector or stage box), and the run waits for the deadman press in CH5 AUTO, then counts down (5 s
default) and plays. Letting go of the deadman, CH5 leaving AUTO, an e-stop, a fault or an unknown state
ends it; the arms hold, the base and hand stop, the fingers open, the music stops.

## Where things are

- Tools: `software/choreo/` (cue, runner, render, sweep, ik, effectors, video_out, hand_output, library).
- Composer: `software/choreo/composer/`; studio launcher: `software/choreo/studio/`.
- Twin scene and collision model: `sim/` (`scene.py`, `clearance.py`, `hand.py`).
- Design notes: `notes/design/choreo-*.md` (format, audio, video, stage, floor hand, dance).
- People's guides: `docs/composer/` (quickstart, making a show).
