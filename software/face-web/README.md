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
