---
name: opendubs-show
description: Make, change, check, preview, render or deploy an OpenDubs SHOW (a timed cue sheet for the robot's arms, grippers, head, face, lift, light, sound, video, base and floor hand), or build tooling around shows (the composer, the studio, the twins, the runner). Use it before writing composer features, cue-sheet code, viewers or show-related tools — most of what you might build already exists. Covers launching the studio and its MuJoCo twin, importing music, the timeline notes loop, validation against the real robot's limits, the clearance sweep, rendering, syncing to a robot, every output, and the invariants no change may break.
---

# Making and changing OpenDubs shows

A **show** is a YAML cue sheet (format: `notes/design/choreo-format-v1.md`; code: `software/choreo/`).
The robot runs it through one program, the **runner**. Everything else (composer, twins, render) is
authoring and preview, and none of it can move a robot.

**Before you build anything, check it doesn't exist.** The studio already has a composer with lanes
and a beat grid, a face twin (the real face page), a **MuJoCo twin of the whole robot** (real meshes,
real joint limits, the projection screen, the floor hand), offline MP4 rendering, timeline notes for
agents, a collision sweep, IK targets, and sync to the robot. Reuse them. Read the module's docstring
first: each tool in `software/choreo/` starts with its usage.

## 1. Launch the studio

```sh
software/choreo/studio-up.sh                       # = uv run software/choreo/studio/studio.py up
uv run software/choreo/studio/studio.py status     # what runs, on which ports (--json for scripts)
uv run software/choreo/studio/studio.py down       # stops only what `up` started
uv run software/choreo/studio/studio.py paths      # the shows folder, the library, the settings file
```

- **Composer:** http://127.0.0.1:8091 (or what `status` reports). Lanes per slot, the bar/beat ruler,
  the validator, notes. Saving also compiles IK targets and runs the clearance sweep.
- **Face twin:** the real face page (`software/face-web`), playing the show's FACE track.
- **MuJoCo window:** the body twin (`twin_view.py`, under `mjpython` on macOS) playing the composer's
  timeline: arms, head, lift, grippers, base, and the projection screen showing the show's VIDEO. This
  is the robot's real geometry and collision model; do not rebuild a simplified viewer. (The floor hand
  on its tether appears in renders, `render.py`.)
- Everything binds 127.0.0.1. **Never drive a studio or twin another session started** (CLAUDE.md
  rule 6): start your own; it picks free ports when the usual ones are busy.
- Shows live OUTSIDE the repo (`~/opendubs-shows` or what `paths` says); the repo's examples are in
  `software/choreo/shows/`. Media lives in the library (`~/opendubs-library`), never in git.

## 2. Use the robot's real limits

```sh
uv run software/choreo/studio/studio.py limits --from-robot opendubs2   # copy that robot's arm limits
```
Validate against those limits, not the test fixture, before calling a show ready. Per-robot values
stay on the robot and in your local settings, never in the repo.

## 3. Music

- Add a song to the library with its source and licence (`library.py add`, see REFERENCE.md); a show
  names it: `music: { song: <id>, bpm: 139.70, offset_s: 0.632, beats_per_bar: 4 }`.
- Get the grid right: tap the tempo in the composer, set beat 1 at the playhead, check with the click.
  Cue times can then be `bar: "9.1"` / `beat:` as well as `t:` seconds.
- **Role:** `music:` and SOUND cues are `role: dialogue` unless marked `role: music`. Dialogue always
  plays on the tablet; music plays on the projector (or a stage box) when one is ready, with the tablet
  holding a silent copy and taking over if the projector drops out. A library video with `song: <id>`
  carries that song; the projector then plays the music.

## 4. The notes loop (how a person asks you for changes)

People leave **timeline notes** in the composer: a time range (`at`, `to`), optional slots, free text,
`status: open`. Apply them like this:

```sh
P=software/choreo
python3 $P/timeline_notes.py list    SHOW            # the open notes and the cues each covers
python3 $P/timeline_notes.py snapshot SHOW --by agent # ALWAYS snapshot before editing
# ... edit the show's cues (never the notes' text) ...
python3 $P/timeline_notes.py reply SHOW <id> done "one line: what you did" --by agent
python3 $P/timeline_notes.py reply SHOW <id> declined "why not" --by agent
```
`timeline_notes.py prompt SHOW` prints the whole request. The composer sees the file change, shows the
diff, and can restore any snapshot. Notes never move the robot.

## 5. The loop for every change

1. **Read** the show and its notes; `python3 $P/cue.py describe --limits L` prints every number and
   vocabulary the validator holds a show to; the format doc covers each slot.
2. **Edit** cues (prefer the generator script if the show was generated: `~/opendubs-shows/.generator/`).
3. **IK** (if you used `reach:` / `point_at:`): `python3 $P/ik.py compile SHOW`.
4. **Validate** against the real limits:
   `uv run --with pyyaml --with mujoco --with numpy python3 $P/cue.py validate SHOW --limits <robot limits>`.
   Fix every FAIL. Treat "may peak" warnings as real when you can.
5. **Sweep** (any arm/head/lift/grip/base change): `python3 $P/sweep.py stamp SHOW` (needs MuJoCo).
   The validator refuses a show whose stamp is stale or under 30 mm.
6. **Preview** in the studio (the MuJoCo window and the face page play the composer's timeline).
7. **Render** to show the person:
   `uv run software/choreo/studio/studio.py render SHOW --out show.mp4` or
   `python3 $P/render.py SHOW --out clip.mp4 --layout a --from 60 --to 75` (render a short span first).
   Audio is mixed offline and never played. Renders go outside the repo.
8. **Report** what changed, what the validator said, and the render, plainly; say what you did not check.
9. **Sync to a robot only on the person's explicit word**, never on your own initiative:
   `python3 $P/library_sync.py push <robot> <item>` (media), then
   `python3 $P/show_sync.py push <robot> SHOW --dry-run --limits-from-robot`, then without `--dry-run`.

## 6. Outputs (details in REFERENCE.md)

Twin (MuJoCo + face page) · MP4 render · tablet (dialogue, the face) · projector: cast, the **stage
box** (`/stage/` page, local copies or streamed with a head start), or HDMI · live skeletons on the
stage page · the floor hand (CRAWL) · the base (DANCE, through the Thor's tracker) · the **armed run**:
on the robot a show waits ARMED until the operator presses the deadman in CH5 AUTO.

## 7. Invariants: never break these

- **The robot moves only through the runner** (`runner.py`, and arm goals via `arm_output.py`), and
  the base only through its single software owner on the Thor. No tool, viewer or planner of yours
  sends motion directly (no ROS action, `docker exec`, ssh or serial command to an arm, the base or the
  hand). The runner's gates stay: CH5 AUTO, the deadman held, e-stop clear, the motion lease, and an
  unknown state means abort.
- **Never loosen a safety gate.** `CLEAR_MIN_MM` / `THRESHOLD_MM` = 30 mm (ik.py, sweep.py) and the
  measured speed caps (`cue.SPEED_CAP_DEG_S`) are gates, not suggestions. A clearance or speed figure
  counts only for the path the runner actually sends (`motion.py`'s smoothed path); a planner's route is
  advice until the runner executes it.
- **Effectors declare what they need.** Parts come from the slots a show uses (`cue.EFFECTORS`); a show
  may list parts it can do without in `can_skip:`; the operator approves a run without them.
- **Audio roles hold:** dialogue on the tablet, music to the projector with the tablet as fallback.
- **Per-robot values stay on the robot** (joint limits, calibrations, tools fitted, projector offsets).
- **No media in git**: songs, videos, renders and photos live in the library / on the robot.
- Validate, sweep and render before you say a show is ready. Run the tests as CI runs them
  (`.github/workflows/choreo.yml`, the exact `run:` lines).
