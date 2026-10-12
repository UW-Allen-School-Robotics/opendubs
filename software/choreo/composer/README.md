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
| **♫ music**: the show's song | choose a song from the show library (or none); **add** one by dropping an audio file anywhere on the page, or picking one — wav, mp3, ogg, flac or m4a, up to 80 MB, with its **source and licence required**; **remove from show** takes out the show's reference only (the song stays in the library, the tempo grid stays in the show); **delete from library** is behind a confirm and refused while any show here still names the song, listing them. Set BPM and beat 1 there too. See "Music" below |
| **Drag to retime, tap to select, tap empty lane to seek, `+` to add** | pointer events; works with touch |
| **Inspector** | the cue as authored, its resolved time and span (Polaris: show inferred values resolved), its problems, editable time form/value and fields |
| **Violations on the offending cue**, in red, and a clickable problem list | the validator's own sentences, located by cue index |
| **Warnings** in amber, on the cue and in the list | the show stays valid — e.g. a head move that cannot finish at the firmware's 25 °/s before the next head cue |
| **Back to start** (⏮, or Home) | beside play |
| **Teach places the pose where the arm can reach it** | at the playhead if it can get there in time; otherwise at the soonest legal time after the keyframe it follows, with the binding joint and the reason shown, and a warning if it makes the next keyframe too fast |
| **DANCE lane** | named moves as blocks; the playhead panel shows the move in progress; the MuJoCo twin moves the base and draws the declared floor radius (placeholder speeds) |
| **⇤ soonest** in the inspector (arm keyframes) | re-times the keyframe to the soonest legal time after the one before it — earlier or later than now — with the reason |
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
../face.sh                   # the face twin in a window, plus the bridge that drives it
```

The face twin is the head's own face code built for the desktop, sharing the head's command parser
— so it draws exactly what the head would. It comes from the Tab5 tree (`firmware/tab5_idf`,
`./build.sh desktop`); point `OPENDUBS_DEMO` at that build's demo tree. Without it, `face.sh` starts
`face_stub.py`, which prints what the face would be told instead of drawing it.

With `../twin.sh` running too, the robot's head in the MuJoCo window shows the face twin's own
picture: `face.sh` has the face twin share its frames (desktop builds that include the Tab5 tree's
`frame_tap`), and the body twin streams them onto the head's screen. Without them the screen is dark.

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

## Watch a show on the twins (a Mac at the desk)

Three windows follow the composer's playhead: the composer itself, the **body twin** (MuJoCo, the
whole robot) and the **face twin** (the head's own face code). Nothing here can reach the robot:
every twin binds 127.0.0.1 and refuses anything else. Run each in its own terminal, from
`software/choreo/`:

```bash
python3 composer/server.py                   # 1. the composer — then open http://127.0.0.1:8091/
./twin.sh                                    # 2. the body twin, a native MuJoCo window
./face.sh                                    # 3. the face twin, opens the face page in the browser
```

* **Which head.** Both twins follow the robot description's fitted head
  (`description/opendubs/dimensions.yaml`, `head_assembly.fitted`). To choose: `./twin.sh --head
  galaxy_tablet` (the tablet placeholder) or `--head tab5_pantilt`; `./face.sh --head tablet` (the
  web face engine, `software/face-web`) or `--head tab5` (the Tab5 desktop face, or a stand-in that
  prints). With the tablet's face, the HEAD track also turns the twin's reference neck.
* **Busy ports.** Another session may already run a web face twin. `face.sh` never reuses a door it
  did not start: it stops and names the owner. Choose other ports with
  `OPENDUBS_FACE_WEB_PORTS=http,ws,face,neck ./face.sh --head tablet` (e.g. `8785,8786,4264,4265`).
* **Your own shows.** `python3 composer/server.py --shows <a folder>` edits shows outside the repo —
  the right place for show content that should not be committed — and `--port` moves it off 8091;
  then give both twins `--composer http://127.0.0.1:<port>`.
* **Play.** Open the show in the composer and press play (space). The body twin and the face follow
  the playhead, including scrubbing and seeking. The body twin's screen does not show the web face
  yet; watch the face page beside it.

## Notes for an agent (or anyone): the NOTES lane

The lane at the top holds **notes**: what you want changed, over a stretch of time and (optionally)
some parts — "make this wave bigger and on the beat", "point at the audience here". They are kept in
the show as `timeline_notes:` and **never move the robot**: the runner never reads them, and the
validator checks only their shape (a malformed note is a warning, never a reason to refuse a show).

- **`+` on the NOTES lane** adds a note at the playhead (a bar long). Tap a note to edit its text,
  its range (`at`/`to`: a bar like `9.1`, or seconds), the parts it is about, and its status —
  `open`, `done`, `declined` — with the reply of whoever acted on it. A note about some parts also
  shades those lanes over its range.
- **⧉ copies the open notes as a prompt** for any agent session: the show file's path, each note with
  the cues it covers, and the steps (snapshot, edit, validate, reply). Save first — the agent reads
  the file. Or ask the choreography lane to "apply the notes in <show>".
- **The agent** snapshots the show (`timeline_notes.py snapshot`), edits the other lanes, validates,
  and marks each note done with a one-line reply, or declined with why (`timeline_notes.py reply`).
- **This editor notices** the file changed on disk: a banner offers **review** (the difference),
  **load them** (your text stays one undo away) or **keep mine** — never a silent overwrite.
- **History** lists every earlier version — one is kept at each save and before an agent edits —
  shows what restoring it would change, and restores it into the editor (then save, or undo).
  History lives beside the shows (`<shows>/.history/`), never in the repository.

`timeline_notes.py` is the same loop from a terminal: `list`, `prompt`, `snapshot`, `reply`, `history`.

## Which parts a show moves: `slots:`

A show lists, at its top, the parts of the robot it uses — `slots: [ARM_L, ARM_R, HEAD]` — and the
runner builds its pre-run checks from that list (an arm show needs the arms active, a head show
needs the head, and so on). In the composer a lane whose part is NOT listed is drawn hatched with
its name dimmed and dotted.

- **Adding a cue to an unlisted lane lists it**, in the same edit: the toast says so, and one undo
  takes back both the cue and the listing.
- **Tapping an unlisted lane's name** lists it without adding a cue.
- To take a part off the list, edit `slots:` in the YAML view. (A listed part with no cues is
  harmless.)
- A cue on an unlisted part (written by hand) is still drawn on its lane, with the validator's
  problem on it — it is never dropped from the timeline.

## Music

A show names its music by the id of a song in the **show library** (`music: song: <id>`; the
older `music: file:` next to the show still works). The library lives **outside the repository**
(`library.py`): a song is someone's work, so it is never committed, and every song carries where
it came from and what allows the robot to play it.

- **Where the composer's library is:** `server.py --library <folder>`, else `$OPENDUBS_LIBRARY`,
  else `/var/lib/opendubs/library` (the robot's). The editor shows the path in the music dialog.
- **Adding** goes through `library.py`'s rules: the type is checked by the file's first bytes as
  well as its name, the size is capped (the composer has no login when it listens beyond
  localhost), the file is hashed, and its length is read — from the WAV header, by `ffprobe` when
  installed, else from the browser's own decode.
- **Attaching or removing** is an edit of the show text, so it shows in the YAML view, undoes, and
  needs a save, like any other edit.
- **Deleting** removes the file and its entry, and is refused while a show in this composer's
  shows folder still names the song. Shows elsewhere (on the robot) are not visible from here.

**How a song reaches the robot and the tablet.** The robot plays a song from **its own** library
(`/var/lib/opendubs/library` on the Thor): the validator checks the id there before every run, and
the dashboard serves the file to the tablet under `/api/choreo/media/library/songs/…`, which the
tablet downloads whole before the start, exactly like a `file:` song. A song added on a laptop is
in the laptop's library only until it is pushed:

    python3 software/choreo/library_sync.py --library <the composer's library> push <robot> <song-id> --dry-run
    python3 software/choreo/library_sync.py --library <the composer's library> push <robot> <song-id>

over ssh, content-addressed (what the robot has is skipped), every item verified on the robot, and
never deleting anything there (see `library_sync.py`). The music dialog shows the command under
each song. The tablet's own cache, by hash from the robot's library, is the tablet lane's plan 5.3.

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
