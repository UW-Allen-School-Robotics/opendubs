# Composer — the bones of a timeline editor (preview only)

A browser editor for show files, with the validator in the loop. **It cannot move the robot.** It
reads and writes `software/choreo/shows/*.yaml`, runs `cue.py` on every change, plays the show's
music against the beat grid, and draws what each slot would be doing at the playhead. There is no
robot-facing code path anywhere in it — a test (`tests/test_composer.py`) trips if one appears.

```bash
pip install pyyaml                                   # the only dependency
python3 ../shows/audio/make_test_audio.py            # the click track beat-demo plays to
python3 server.py                                    # then open http://127.0.0.1:8091/
python3 server.py --limits <arm software>/hw_validation/joint_limits.yaml   # the robot's limits
python3 server.py --host 0.0.0.0                     # to reach it from the Android tablet
```

Without `--limits` it uses the arm software's file if it is at the container path, and otherwise
the repo's **test fixture** — and says so in the toolbar (`TEST LIMITS`), because a show that
passes against the fixture has not been checked against any real robot's limits.

`--host 0.0.0.0` has no authentication: anyone who can reach the port can edit show files in the
shows directory (nothing else — paths are confined, and nothing here commands hardware). Bind to
localhost unless a tablet needs it.

Everything is local: vanilla JS, no build step, nothing from a CDN. The robot's network may have
no internet.

## What works

| | |
|---|---|
| **One lane per slot**, plus a clips lane, a music lane and a ruler | tracks are scopes, not free lanes (workplan 5.2); undeclared slots are shown hatched |
| **Keyframes as diamonds** on the arm lanes, with the move into each drawn as a bar | the span the validator checked the speed over |
| **Cues as blocks with duration** | a lift block is as long as its `secs`, a sound as long as its `secs` or its file |
| **Clips** drawn as their placement plus their expanded cues (purple) | dragging any part of a clip moves the placement |
| **Waveform**, beat/bar grid from `bpm` / `offset_s`, markers on the ruler | |
| **Play/pause through Web Audio** | the playhead is `AudioContext.currentTime` minus the reported output latency, i.e. what is *heard*, read every animation frame — not a timer |
| **Click track** (the `click` box) | scheduled on the audio clock with lookahead, so it cannot drift from the music |
| **Snap** to bar / beat / ½ / ¼, or off | a `t` cue snapped to a beat **stays a `t` cue**; a `bar` cue stays a `bar` cue while it lands on a whole beat (format §3) |
| **Tap tempo** → `use` writes `music.bpm`; **beat 1 ⇤** writes `music.offset_s` from the playhead | median of the tap intervals, so one bad tap does not move it |
| **Drag to retime, tap to select, tap empty lane to seek, `+` to add** | pointer events; works with touch |
| **Inspector** | the cue as authored, its resolved time and span (Polaris: show inferred values resolved), its problems, editable time form/value and fields |
| **Violations on the offending cue**, in red, and a clickable problem list | the validator's own sentences, located by cue index |
| **Warnings** in amber, on the cue and in the list | the show stays valid — e.g. a head move that cannot finish at the firmware's 25 °/s before the next head cue |
| **Back to start** (⏮, or Home) | beside play |
| **Notices** | folded to a one-line count under the problems; each note is one headline line, full text on hover |
| **Hover text** | on every non-obvious control and readout; on a touch screen, a long press shows it |
| **Per-lane mute** | preview only; stored in the browser, never in the show |
| **Undo**, **save**, **YAML view** | the YAML view is exactly the text that will be saved |
| **Teach (⦿) on the arm lanes** | captures a pose into a keyframe at the playhead — from a **FAKE** joint source in this build; see below |
| **Simulator preview** — `../sim_preview.py` | steps `sim/` through the exported timeline: sampled poses (`--dry-run`), closest approach per frame (`--clearance`), PNG frames (`--frames`) |

### The MuJoCo twin follows the playhead

```bash
python3 server.py            # the composer, as above; open a show
../twin.sh                   # a native MuJoCo window of the whole robot, in step with the editor
```

Play, pause, scrub or edit (saved or not) and the window follows. The composer publishes only
the playhead's time and play state (`/api/playhead` in, `/api/twin` out) and the resolved
timeline when the show changes (`/api/twin/timeline`); the viewer (`../twin_view.py`) evaluates
the timeline itself with the same sampling as `sim_preview.py`, so the window and an offline
preview cannot disagree. It shows geometry, not timing — see `sim_preview.py` for the three
assumptions. `--log` prints the followed state once a second.

### The face follows too, through the bridge

```bash
python3 ../face_stub.py      # a stand-in face that prints what it is told (until the twin listens)
python3 ../face_bridge.py    # follows the playhead; sends FACE as EMOTE and earcon SOUNDs as CUE
```

`face_bridge.py` reads the same feed as the MuJoCo twin and speaks the head's own command protocol
(`notes/design/face-twin.md`), to this machine only — it refuses any other address. Crossing a cue
while playing sends it; a seek, pause or edit sends the state in force instead, with no sounds.
LIGHT cues and sound files are deliberately not sent, and the console says why.

### Saving is a minimal diff

The editor edits the show's **text**, not a re-dump of it (`yamledit.py`): a retimed cue changes
one value on one line, an added cue is one new line at the end of `cues:`, comments survive, keys
keep their order, and no zoom, colour, lane height or position ever enters the file. An unedited
file round-trips byte-identical. That is what keeps an editor save reviewable by a person or an
agent (format §9).

## What is stubbed, and what is not here

- **The joint source for teaching is fake.** `FakeJointSource` in `static/composer.js` fabricates
  a `sensor_msgs/JointState` in the controller's own convention (left J1–J3 negative) so the
  un-mirroring in `cue.py` is exercised end to end. On the robot the browser would read **one
  sample** of `/joint_states` through rosbridge — read-only — and post it to `api/capture`. That
  interface is specified in `notes/design/choreo-teach-and-sync.md`; it is not wired.
- **Clip definitions are edited in the YAML**, not in the timeline. Placements are editable.
- **No new-show / save-as / rename.** Copy a file in `shows/`.
- **No graded snap** (Vixen's weighted marks) and no pinch-zoom; the `−`/`+` buttons and
  Ctrl/Cmd-wheel zoom.
- **The preview is a readout, not a picture.** Joint bars per arm, the head's target angles,
  the lift's relay state and net up-time, the sound, the face emote, the light colour. A drawn
  robot would have to choose a head-angle mapping and a lift speed that nobody has measured; the
  simulator preview states those assumptions out loud instead.
- **`sim_preview.py` has not been run against MuJoCo** in the environment it was written in (no
  `mujoco`, no OpenArm assets there). Its sampling is tested; its render and clearance paths are not.
- **No live output.** Deliberately. The future armed binding is designed, not built.

## Mounting it under the dashboard later

`router.py` exposes the same functions as a FastAPI router; one `include_router` and one static
mount, documented at the top of that file. Not wired: this PR does not touch
`software/kiosk-dashboard/`, and an unauthenticated write surface on the operator UI should be a
decision rather than a side effect. All URLs in the frontend are relative, so it works under a
prefix unchanged.

## Files

| | |
|---|---|
| `server.py` | standalone HTTP server (stdlib) |
| `api.py` | the backend as plain functions; imports `cue.py`, never copies it |
| `yamledit.py` | text-surgical YAML edits |
| `router.py` | the dashboard mount, for later |
| `static/` | `index.html`, `composer.css`, `composer.js` |
