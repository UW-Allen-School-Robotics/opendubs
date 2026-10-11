# The tablet face engine

The robot's face as a web page: what shows on the tablet when the tablet is the robot's head, and its
own twin in any desktop browser. The design is in
[`notes/design/face-engine.md`](../../notes/design/face-engine.md); the messages it answers are the
head protocol's ([`notes/design/head-protocol.md`](../../notes/design/head-protocol.md)).

| | |
|---|---|
| `index.html`, `engine/` | the face: its behaviour (`face.js`), its half of the head protocol (`protocol.js`), skins (`skins/`), tracking (`track.js`, `track_worker.js`) |
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

## On a tablet

With the tablet on USB, `adb reverse tcp:8765 tcp:8765` and `adb reverse tcp:8766 tcp:8766` make the
laptop's door the tablet's `localhost` (a secure context, so the camera works). Point the kiosk app at
it with `adb shell am start -n org.opendubs.kiosk/.ProvisionActivity --es url http://localhost:8765/?…`
— and back at the dashboard afterwards, since the app remembers its URL.
