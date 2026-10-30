# The tablet face engine

The robot's face as a web page: what shows on the tablet when the tablet is the robot's head, and its
own twin in any desktop browser. The design is in
[`notes/design/face-engine.md`](../../notes/design/face-engine.md); the messages it answers are the
head protocol's ([`notes/design/head-protocol.md`](../../notes/design/head-protocol.md)).

| | |
|---|---|
| `index.html`, `engine/` | the face: its behaviour (`face.js`), its half of the head protocol (`protocol.js`), skins (`skins/`), tracking (`track.js`, `track_worker.js`), the offline render (`render.js`) |
| `door_dev.py` | the laptop's door: serves the page, carries head-protocol lines over UDP to it, and runs a reference neck. 127.0.0.1 only |
| `make_test_library.py` | a small generated test library (an image skin, a video skin with transparency) in `library/` |
| `fetch-vendor.sh` | the third-party files (MediaPipe), pinned, into `vendor/` |
| `bench.py` | measures the page on a tablet over USB, in Chrome or the kiosk app's WebView |
| `tests/` | the engine headless in Node, held to the head protocol by its conformance suite (run in CI) |

`library/` and `vendor/` are git-ignored: the real library lives on the robot, and nothing anyone
authored is committed.

## Run it on a laptop

```bash
software/face-web/fetch-vendor.sh                                   # once: MediaPipe, for tracking
uv run --with pillow --with imageio-ffmpeg python3 software/face-web/make_test_library.py
uv run --with websockets python3 software/face-web/door_dev.py
```

Then open `http://127.0.0.1:8765/?door=ws://127.0.0.1:8766&hud=1&state=SAFE_IDLE`. Add `skin=ring`,
`skin=test-gear` or `skin=test-orb` for a skin, `track=1` to look at you through the webcam. Keys: `1`–`7`
state, `e` emote, `s` next skin, `t` theme. Talk to it as the robot would:

```bash
python3 software/head-protocol/conformance.py 127.0.0.1:4244 --neck 127.0.0.1:4245 --active --motion
```

## Cameras, and the controls

**A camera is open: the face says so.** Whenever either of the tablet's cameras is open (face
tracking, or a stream to the robot), a red dot shows at the top right of the face: F for the
screen-side camera (the robot's front), R for the back-side one (its rear). It shows over every skin
and state, and never when no camera is open. On the robot the state comes from the kiosk app's
`cameras()`, which is the hardware's own view (any app). In a browser it comes from the page's own
video tracks. Tracking (`tracking: tablet` on the Head & face page) loads MediaPipe from the robot
(`deploy.sh` copies `vendor/mediapipe`), so it needs no internet.

**The controls.** A long-press anywhere opens STOP SHOW / DASHBOARD / FACE. A short tap shows
"Hold anywhere to open controls" for 3 s. Nothing shows by itself, so a show's face stays clean.

## The Halloween ghost

A content code skin for the Halloween demo: a ghost with ten expressions (DOZE, LOOKOUT, GROOVE,
SMILE, PEEK, EEK!, FOCUS, HAPPY, PUZZLED for THINK, and the flame mouth that never stops). Which one
shows follows the demo day's coordinator (`demo_day.py`: patrolling, going to someone, a show, the
candy), the face tracker (someone in view) and the ARCHITECT demo runner. Each short show
(`wave-and-nod`, `robotic-smile`, `celebrate`) has its own timeline of the ghost's looks, at the show's
time, in costumes that change within the show (pumpkin, cat, monster or the classic black and white),
each step fading into the next. Another show's FACE cues pick its looks. All of it is this one skin, so
a show needs no SKIN cue (`DANCES` in `ghost.js`). **The state machine, every expression animated:**
[`notes/design/face-halloween-ghost-states.html`](../../notes/design/face-halloween-ghost-states.html)
(download it and open it in a browser).

This section is in two parts: **what has to change for it to run on the robot**, and **what is only
for previewing and testing**.

### Part 1: needed on the robot

| File | |
|---|---|
| `content/face/skins/ghost.js` | **added.** The skin, one file: the drawing, `ghostStage()`, which picks the expression from the coordinator's and the runner's state, and `DANCES`, each show's face timeline, costumes and tempo. Imports nothing from the engine |
| `../kiosk-dashboard/backend/architect_proxy.py` | **added.** `GET /api/architect/day` (the coordinator, 127.0.0.1:8096) and `GET /api/architect/state` (the runner, 127.0.0.1:8098), read-only, for the face to poll; the tablet can't reach those ports itself |
| `../kiosk-dashboard/backend/app.py` | **changed** (a shared path). Includes that router |
| `../choreo/shows/audio/make_halloween_audio.py` | **added.** Writes the three Halloween tracks into that folder (git-ignored), like `make_test_audio.py` |
| `../choreo/shows/{wave-and-nod,robotic-smile,celebrate}.yaml` | **changed** (the Choreography lane's). `music: file:` names each show's Halloween track |

No engine file or head-protocol message changes; the shows change only their music. A reserved state (E_STOP, DEGRADED) still
draws the default eyes.

To run it, the robot needs `ghost.js` in its content folder (`$OPENDUBS_SHOWS_DIR/face/skins/`, which
comes from the content repository: code skins are never uploaded), the dashboard deployed with the proxy
above, and `SKIN ghost` sent for the day. The shows' dances, EEK!, FOCUS and HAPPY also need the
coordinator (`demo_day.py`, port 8096, not on `main` yet) or the demo runner (8098); without them it
still dozes, looks round, grooves and smiles at people. The three short shows have no `skin` key on
their FACE track, so the ghost stays on through them.

**The Halloween music.** Each short show plays an original Halloween track made for it, on the show's
grid, its hits on the face's steps (`robotic-smile` in place of `robotic_smile.mp3`; `celebrate`, which
had no music, now has one). Audio is never committed, so generate the tracks once on the machine that
runs the shows; the shows' `music: file:` name them:

```
python3 software/choreo/shows/audio/make_halloween_audio.py    # needs numpy; same files every run
```

The face doesn't need the music: it follows the show's time, whatever plays.

**Spoken lines** (proposed, not used: a show can't trigger speech yet; for the Voice lane): wave-and-nod
0.2 s "Happy Halloween!", 17.0 s "Thank you, thank you."; robotic-smile 0.3 s "Mmm… is someone there?",
25.2 s "Eek!", 29.2 s "Ha ha! Happy Halloween!"; celebrate 0.1 s "Boo!", 4.2 s "We did it!".

**Changing a show's face later:** edit `DANCES[show].steps` in `ghost.js` (`[seconds into the show,
look, costume?]`; the looks and costumes it takes are `TIMELINE`, at the end of the file), run
`node tests/test_ghost_skin.mjs`, and update the content repository's copy. ENCORE's ghost lane
(Part 2) draws the steps and copies them out in this form.

### Part 2: for previewing and testing only (the robot doesn't use these)

| File | |
|---|---|
| `tests/test_ghost_skin.mjs` | **added.** `ghostStage()` row by row, the dances' table, reading the emote back from the eye, when someone counts as in view, every look drawing in every costume, and each show's steps fading in without a jump |
| `tests/conformance.sh` | **changed.** Runs the test above |
| `../kiosk-dashboard/backend/tests/test_architect_proxy.py` | **added.** The routes, and a service that is not there reading as `{"available": false}` |
| `../../notes/design/face-halloween-ghost-states.html` | **added.** The state machine above |

To try the skin in a browser with no robot, set its inputs from the console:
`window.ghostInputs = { day: { available: true, state: "CHOREO", show: "wave-and-nod", since_s: 0 } }`
(add `costume: "cat"` to dress it whatever the show).

**In ENCORE** (`UW-Allen-School-Robotics/encore`, the branch of the same name as this one). ENCORE is
the choreography studio; these let it preview a show in the ghost and draw the ghost's face timeline.
They're recorded here in case they aren't merged there.

| Commit | |
|---|---|
| `3fc3f87` | **Wear…** in the Face pane: preview a show in one of the content folder's skins (`ENCORE_FACE_CONTENT`), without putting it in the show; the skin is registered from ENCORE's own page, `vendor/` untouched |
| `44d269d` | The Wear menu first in the toolbar; the proposal `docs/proposals/halloween-ghost.md` |
| `5be3650` | Wearing the ghost, **Look…** and **Costume…** replace Emote… and Skin… and put a look or costume at the playhead. The draft is kept in the project (`wear/ghost/<show>.json`, `GET/PUT/DELETE /api/wear/<skin>/<show>`), never in the show; **Copy for the skin** copies it out as `ghost.js` writes it |
| `a1282df` | The ghost's steps as a lane on the Timeline, under the face: drag, double-click to add, right-click for a look, a costume, delete, Copy for the skin, Back to the skin's own |
| `9e649fd` | Steps snap onto markers (a held note, a section) when dragged near one |
| `18f0d9a` | `demo/halloween-shorts/`: a project per short show, and `make_music.py`, which synthesizes their Halloween tracks (to `~/.encore/halloween-shorts/`, never committed) |

To use it, in ENCORE: `uv run python demo/halloween-shorts/make_music.py`, then
`ENCORE_FACE_CONTENT=/path/to/opendubs/software/face-web/content/face uv run encore`, open one of the
three projects (File › Open project…), and choose **Wear: ghost** in the Face pane.

## Render a show offline

`index.html?render=1` is the face as a renderer rather than a live page. It is for drawing a show into
a video (`software/choreo`). There is no frame loop, door, camera, tracking, audio, AirPlay or operator
overlay. Time moves only when the caller moves it, so the same show, seed and times always give the
same pixels. Parameters: `seed=N` (default 1), `w=W&h=H` (default 640×360; the face is 16:9 and any
other shape is letterboxed on black), and optionally `skin=`, `state=` (default `AUTONOMOUS`),
`library=` and `caption=1`. The idle quote under the eyes is not drawn unless `caption=1`: it is the
face's idle line, not part of a show.

```js
await OpenDubsRender.ready;                       // {w, h, seed}; the skin library is read once, here
await OpenDubsRender.apply("SKIN ring", 1200);    // a head-protocol line at show time t (ms) → the reply
OpenDubsRender.step(1233.3);                      // move the face to t and draw it
OpenDubsRender.frame();                           // {w, h, rgba: base64 of w*h*4 bytes}
```

`apply` and `step` share one clock. It only runs forward, and a step back throws. The face starts at
t = 0 as a page does, with its eyes opening, so step through a short lead-in before a show's first frame.
`apply("SKIN …")` resolves once the skin can draw, so the frame after it shows the skin. Image skins
are repeatable. A video skin is not, because which of its frames shows follows the wall clock. BRIGHT
is drawn into the frame as a dimming, and fades and the ARMED lease run on the show's clock.
`tests/test_render.mjs` holds this in CI, and `tests/test_render_browser.mjs` compares real pixels in Chrome.

## On a tablet

With the tablet on USB, `adb reverse tcp:8765 tcp:8765` and `adb reverse tcp:8766 tcp:8766` make the
laptop's door the tablet's `localhost` (a secure context, so the camera works). Point the kiosk app at
it with `adb shell am start -n org.opendubs.kiosk/.ProvisionActivity --es url http://localhost:8765/?…`
— and back at the dashboard afterwards, since the app remembers its URL.
