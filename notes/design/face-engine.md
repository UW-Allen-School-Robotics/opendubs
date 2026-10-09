# The tablet face engine — stack, skins, media and tracking

**Status: spike built and measured, 2026-10-08; recommendations for the team to agree.** Tablet
plan Phase 4 (DT-9, DT-10, DT-14), pulled forward as the team's priority. Everything here was done
at the desk, on a laptop and a **bench tablet**, never on the robot. Code:
[`software/face-web/`](../../software/face-web/).

**Read this first: the tablet is the 8.7-inch model.** The Galaxy Tab A11 SM-X133 reports an
**800 × 1340 panel at 90 Hz** (`wm size`, `dumpsys display`), not 1920 × 1200. Earlier notes and the
tablet plan said "11-inch"; that was wrong, and the head's CAD should start from the tablet in hand.

**The bench tablet is not the robot's.** Same model (SM-X133), but **Android 16, WebView 153,
Chrome 154**, where the robot's tablet runs Android 15 and WebView 131. Numbers below are evidence
about this model on a newer build; the plan's 4.2 re-runs them on the robot's own build before they
are relied on.

---

## A. The rendering stack

### Recommendation

**Layers composited by the browser, each drawn by the simplest thing that does its job:**

| layer | drawn by | why |
|---|---|---|
| **the face** — the default eyes, and procedural or sprite skins | **Canvas2D** | measured at the panel's full resolution: 90 fps, 0.3–1.2 ms of drawing per frame — more than 90% of the frame budget left. No dependency. |
| **authored skins** (eyes the team draws) | **Rive** (`@rive-app/canvas-lite`, or `webgl2` if a skin needs feathering) | built for exactly this: a character that takes live inputs (look, blink, emotion) through a state machine and data binding, authored in a real tool. **Not yet measured on the tablet** — the gate is the first real `.riv` (below). |
| **overlays and one-shot animations** | **Lottie** (`lottie-web` light canvas, or dotLottie on WebGL2) | the After Effects route; plays and scrubs, takes no live inputs, which is fine for overlays. Second priority. |
| **media** (video clips, full screen) | **`<video>`**, composited by the browser | hardware decode, drawn by the compositor as a texture — never copied into a canvas. Measured below. |
| **the system strip** (critical telemetry, E_STOP) | DOM, on top | never authorable (choreography standing rule 4). |

**Not PixiJS, for now.** It adds 840 KB and its value — GPU filters, many sprites, video as textures
— is not needed by anything above; v8 also has no Canvas2D fallback. Revisit if a skin needs per-frame
glow or blur shaders.

### Evidence — bench tablet, kiosk app's WebView, full screen (1339 × 800 drawn), 90 Hz

| configuration | frame p50 / p95 | drawing p95 | video dropped |
|---|---|---|---|
| default eyes | 11.1 / 11.2 ms (90 fps) | 0.4 ms | — |
| ring skin | 11.1 / 11.2 ms | 1.2 ms | — |
| eyes, 1920 × 1200 60 fps H.264 decoding behind | 11.1 / 11.2 ms | 0.3 ms | 0 / 1010 |
| that video **showing** (cut to media) | 11.1 / 11.2 ms | 0.5 ms | 5 / 1010 (0.5%) |
| 1920 × 1080 60 fps video showing | 11.1 / 11.2 ms | 0.5 ms | 0 / 1014 |

GPU: `Mali-G57 MC2`; WebGL2 and WebGPU both exposed. The same runs in Chrome agree. The test clips are
synthetic test patterns made for the purpose (`ffmpeg testsrc2`), never committed. Reproduce:
`software/face-web/bench.py --serial <bench tablet> --webview`.

**The panel is 1340 × 800, so a 1920 × 1200 clip is decoded and scaled down.** Clips should be made at
the panel's size; it cost nothing here either way.

### What would change this recommendation

- **The first authored Rive skin cannot hold 60 fps** at 1340 × 800 on the tablet, inputs set every
  frame → authored skins move to sprite sheets and Lottie, and Rive is dropped.
- **Rive's licence** — the Free plan exports with a Rive splash screen, and the paid seat removes it
  (pricing page, read 2026-10-08; whether the runtime enforces the splash is not verified) → if the team
  will not pay or show a splash, Lottie and sprite sheets become the authored formats.
- **A skin needs per-frame glow, blur or distortion** that Canvas2D cannot do in budget → that skin's
  layer moves to WebGL (PixiJS), the rest stays.
- **The robot's own WebView 131 behaves differently** from the bench's 153 → re-measured there first.

## The art tool

**Recommended: the Rive editor for skins**, because a skin must still blink, emote and look where it is
told, and Rive is the one tool whose output *takes inputs at run time*. The team draws eyes in it and
exposes a small set of inputs (the *skin contract*, below). Its output is one `.riv` file per skin.

For everything that does not take inputs:

| asset | tool | becomes |
|---|---|---|
| clips for music-video mode | any video editor | MP4, H.264, at the panel's 1340 × 800 |
| overlays, one-shot animations | After Effects (Bodymovin) or LottieFiles' editors | Lottie (`.json` / `.lottie`) |
| simple or pixel-art skins | Aseprite, or any sprite-sheet exporter | a sprite sheet plus its JSON |

**Decisions for the team:** whether to pay for Rive seats (or accept its Free-plan splash), and which
of the other tools anyone already uses.

**How any of these becomes a library asset:** the file goes into the robot's library (§D) with a small
entry naming its kind, and for a skin, which contract inputs it uses. Nothing is committed to the repo.

### The skin contract

Every skin — built in, sprite sheet or Rive — is drawn from the same per-frame inputs, so a show's
`EMOTE`, a blink and a tracked person read on any skin with no change anywhere else:

| input | range | meaning |
|---|---|---|
| `lookX`, `lookY` | −1 … 1 | where the eyes look (saccades and tracking already resolved) |
| `lid` | 0 … 1 | how shut; blinks included |
| `emote` | `NEUTRAL` `ALERT` `SQUINT` `SOFT` `SLEEPY` | the current emote, for skins that switch poses |
| `widthMul`, `heightMul`, `roundness`, `browRaise`, `browAngle` | as the Tab5's emote table | the emote's shape, sprung, for skins that morph |
| `colour` | RGB | the state colour |
| `side` | left / right | which eye |

**A reserved state overrides any skin.** While the robot is `DEGRADED` or `E_STOP` the engine draws
the default eyes in the state's own colour, whatever skin is set. A skin is decoration; "something is
wrong" must look the same on every robot and every show.

## B. The spike

`software/face-web/`: the face as a page.

- **The face's behaviour, ported** from `app_face.cpp`: the geometry, the emote table, sprung shape,
  eased saccades with main-sequence timing, blinks, emote cycling, pinned and reserved states, themes,
  quotes, NO LINK after 5 s without `STATE`.
- **Two skins on the same inputs**: the default eyes, and a segmented ring skin to prove the interface.
- **The face answers its half of the head protocol itself** (DT-10). Through `door_dev.py` (UDP ↔ the
  page, plus a reference neck), **the head protocol's conformance suite passes 80 / 80** with
  `--active --motion` — the same suite that judges the Tab5.
- **No show content**: two generic skins, the Tab5's quotes, no assets.

## C. Tracking in the page

MediaPipe Face Landmarker (`@mediapipe/tasks-vision` **0.10.35**), fetched into a git-ignored `vendor/`
(15 MB: 11.8 MB of WebAssembly, a 3.8 MB model). It gives 478 landmarks, 52 blendshapes and a head-pose
matrix per face, so the face knows where a person is **and whether they are facing it** (confidence
falls off with head turn), against the Tab5's box.

**Pinned below 1.x on purpose:** `tasks-vision` 1.0.0 and later POST usage logs to
`odml.pa.googleapis.com` every 60 s (found in the 1.0.1 bundle); 0.10.35 contains no such endpoint, and
`fetch-vendor.sh` refuses any build that does. (The first laptop run used 1.0.1 for about 30 s; whether
it sent a log is not known.)

| on the bench tablet (WebView unless noted) | face frame p95 | tracking | detection | frame → result | warm-up |
|---|---|---|---|---|---|
| detector on the page's main thread | **34–44 ms — visible stutter** | 17.7 Hz | 40 ms | — | 1 s |
| **in a worker, GPU delegate** | 11.2 ms | 14.9 Hz | 54 ms | 56 ms | 1.3 s |
| **in a worker, CPU delegate** | 11.2 ms | 14.9 Hz | 50 ms | 51 ms | 0.5 s |
| worker, CPU, **the screen-side camera and a person** (Chrome) | 16.7 ms (Chrome held 60 Hz with the camera open) | 8.3 Hz | 55 ms | 62 ms | 0.25 s |

The stimulus rows use the Tab5 probe's own reference face, sliding across a canvas — a person who walks
past on cue, so the measurement needs nobody at the bench. **Decided by the measurement:** the detector
runs **in a worker, on the CPU** — the GPU delegate is no faster here and would compete with the face and
video. About 15 Hz is the rate the Tab5's perception runs at.

**What changes with the tablet's real camera:** fewer frames than the stimulus (8.3 Hz with a person
moving in room light), the face found only while the person is in frame, and ~60 ms from capture to the
eyes moving. **In the kiosk app the camera is not granted yet** — it grants the microphone to the
dashboard's origin only — so the app must grant the camera to the face (tablet plan 1.7). A module worker
needs a shim for MediaPipe's loader (`track_worker.js`).

## D. Media mode, skins and the library

### Where each message goes

| | carried by | why |
|---|---|---|
| **`SKIN <name>`** — persistent, like `THEME` | **the head protocol** (face half, new) | it is face state; it outlives a show; it works with no show running |
| **media cues and layer cuts** — play this clip, cut to it on this beat, cut back | **the show-audio channel, extended to "show media"** | it already does what sync needs: downloads files before the show, matches the tablet's clock to the Thor's (±2.7 ms measured on opendubs2), starts at a Thor time, fades on abort, cuts on e-stop, has one designated device. Video on the same channel shares **one clock with the music**, so audio and video cannot drift apart. A show needs the Thor anyway. |

`SKIN`, proposed for the head protocol, version 2:

| message | reply | notes |
|---|---|---|
| `SKIN <name>` | `OK skin <name>` / `ERR unknown skin` | `eyes` is built in; any other name is a library skin. Persists after a show, like a theme. Tablet face only: the Tab5 answers `ERR unknown`. A reserved state overrides it (above). |

### Show media and the library — canonical in [`choreo-screen-track.md`](choreo-screen-track.md)

**Agreed with the Choreography lane, 2026-10-08, and proposed for the team's approval in PR #111.**
That note is the canonical version of the channel (roles `audio` and `screen`; `claim`, `load`,
`ready`, `start`, `stop`, `cut`), the `screen` shot list (`{at, until, layer, clip, from, loop}` in
show seconds, scheduled up front from `start`, never sent live), the show format's `SCREEN` slot and
`FACE: do: skin`, the not-ready rule, and the library index. What it records from this side: the face
page claims **both** roles while the tablet is the face (one clock for picture and sound; in controller
mode the dashboard claims `audio`); `SKIN` is head protocol v2; clips are silent; the panel is
1340 × 800; the tablet syncs the library by hash (tablet plan 5.3).

**The face engine's own rules, as the screen client:**

- **A reserved state cuts to the face by itself.** If the robot goes `DEGRADED` or `E_STOP` while a
  media shot is showing, the engine drops the media layer at once, without waiting for the runner's
  `cut`: it hears the robot's state directly. The system layer is always on top.
- **Rolling preload.** `ready` means every file for the show is on the device and the first shots are
  decoded and paused; later shots are preloaded on a window ahead of the playhead, because Android has a
  limited number of hardware decoders. The decoder limit on the MT6789 and a cut's real latency are
  measured on the bench before this is built.
- **Cuts land on a frame.** A cut takes effect on the first frame displayed at or after its time — within
  one 11 ms frame at 90 Hz; a clip is started early by its measured start latency and held to the show
  clock with `requestVideoFrameCallback`'s media time.
- **A multi-file asset's hash** (a skin is a folder) is the sha256 of its sorted
  `relative/path<TAB>sha256` lines — proposed to the Choreography lane.

## E. Coordination

The Choreography lane owns the show format, the composer, the runner, the show-audio channel and the
library index; this lane owns the head protocol and the face engine. Agreed in
[`choreo-screen-track.md`](choreo-screen-track.md) (PR #111). Suggested to them: the composer's preview
embeds this face engine, fed the same shot list, so preview and robot run the same code.

## Next

1. **Measure the first real Rive skin** on the tablet (the gate in A).
2. **Re-run `bench.py` on the robot tablet's own build** (WebView 131).
3. **The kiosk app grants the camera** to the face (plan 1.7).
4. **A thermal run**: face, video and tracking together for a long session (plan 3.2).
5. **`SKIN` into the head protocol** as version 2, and the face page as the screen client (build step 4
   of `choreo-screen-track.md`), once the team approves #111.
