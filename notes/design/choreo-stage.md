# The stage page: live skeletons on the projector

Choreography lane; started 2026-10-21. A person dancing in front of the robot is drawn LIVE as a cartoon
skeleton on the projector, over the show's picture, during the show. When nobody is there a skeleton
dances on the beat; a person takes over when they step in and hands back when they leave.

## PRIVACY — a fixed rule

**Only skeleton points are used** — joint positions, confidences and track ids. **No camera image is
shown, streamed, recorded or stored by any OpenDubs part.** The adapter declares a skeleton message type
only, and the relay refuses any image or point-cloud type (in code, #451). Nothing records the skeleton
points either: the twin and the tests use synthetic motion (`skeleton_feed.synthetic`), never a real person's.
This rule is not a setting; changing it is the user's decision, written here first.

## Who owns what

| piece | lane |
|---|---|
| the stage page (`frontend/stage/`), its feed route (`backend/stage.py`), the adapter (`software/choreo/skeleton_feed.py`), the fallback dancer, the crossfade | Choreography |
| the relay: the camera's ROS 2 topic → the adapter → the dashboard (`software/ros`, #451), its unit, the camera's TF frame | Thor stack |
| the kiosk app on the projector (Google TV): the WebView, launching it over ADB, keeping it on the page | Tablet and head interface |
| the skeleton tracker itself (on the Thor, a ROS 2 topic) | a collaborator outside the lanes |

## The path

```
depth camera → tracker (ROS 2 topic) → relay (#451): adapt(msg, received_monotonic) → JSON
   → ws://127.0.0.1:8080/ws/skeleton/in   (localhost only)
   → dashboard (stage.py): newest frame only, per page
   → ws://<thor>:8080/ws/skeleton          → the stage page at http://<thor>:8080/stage/
```

## The frame (v1) — the contract between the adapter, the relay and the page

```json
{"v": 1, "t": 1234.567, "frame": "skeleton_camera", "status": "live",
 "people": [{"id": 7, "joints": {"head": [x, y, z, c], "neck": [...], ...}}]}
```

- `t`: capture time on the Thor's **CLOCK_MONOTONIC**, seconds (the clock the show runner keeps the show on);
  the adapter converts a ROS stamp through the offset learnt at receipt.
- `frame` `skeleton_camera`: metres, x to the CAMERA's right, y up, z away from the camera.
- joints: the canonical set — head, neck, l/r shoulder, elbow, wrist, pelvis, l/r hip, knee, ankle; a
  joint the feed lacks is left out (a neck or pelvis is made from its neighbours); c is confidence 0..1.
- `people: []` with `status: live` = nobody there; `status: stale` = the feed went silent (the relay sends
  that itself after 1 s). The page shows the dancer for both.
- A different message type, topic or joint set is a change to `skeleton_feed.py` only (`MSG_TYPE`,
  `DEFAULT_TOPIC`, `JOINT_MAP`, `adapt`). `MSG_TYPE = None` until the tracker's type is fixed: the relay idles.

## The page

- Full-screen canvas over the show's video when a show is running (below), else over `?video=` for a
  test (the show's stage cut: bed + hits + song, no skeleton baked in — library `them-bones-stage`).
  `?hud=1` shows the feed, the show, the video's error, the frame rate and the latency.
- **The person:** the most confident, nearest one; joints under 0.3 confidence left out; **mirrored** (they
  see themselves as in a mirror); scaled to fit the screen's height; placed where they stand (the camera's
  centre at the screen's centre), kept on the screen.
- **The hand-over:** a person in a fresh frame (< 0.5 s old) fades in over ~0.4 s while the dancer fades
  out; 1.5 s after they leave, the dancer fades back.
- **The dancer** (the fallback figure, and Them Bones' base layer): the same cartoon skeleton on the beat
  — the running show's beat grid, else `?bpm=`. The offline renders use the richer per-section dance (`.generator/bones_dancer.py`).
- **The kiosk:** `window.OpenDubsKiosk?.alive?.()` every 5 s from the draw loop, so a page that stops
  drawing stops checking in and the app reloads it; it reconnects its WebSocket itself, with back-off.

## The show's timing — the only thing the page needs from the Thor

The page has **no per-device logic**: the projector's app, a small computer on HDMI and any browser load
the same `/stage/` and get the same behaviour, because everything a page needs comes from the Thor over
its one WebSocket:

- **The runner's `stage` video output** (`video_out.StageOutput`, `--video cast,stage` on the robot) hands
  the dashboard the show's timing, nothing else: `POST /api/stage/show` (localhost only)
  - `{"op": "load", "id", "videos": [{"t", "end_t", "url", "from_s", "end": "black"|"hold", "id"}],
    "beat": {"offset_s", "bpm"} | null}` while the arms travel to their start pose;
  - `{"op": "start", "id", "t0_mono"}` when the show starts: its t0 on the **Thor's monotonic clock**;
  - `{"op": "stop", "id", "cut"}` on every end (`cut`: an e-stop).

  Times are show seconds; `url` is the library file as the dashboard serves it
  (`/api/choreo/media/library/videos/…`, byte ranges, same origin as the page).
- **The dashboard** (`stage.py`) keeps that state and sends it as `{"type": "show", …}` to every page,
  and to a page that joins later (a reload, a second screen), which then starts in the right place.
- **The page** maps its clock to the Thor's (the ping/pong above), so show time is
  `now_on_the_Thor − t0_mono`; it plays the video at `from_s + (show time − t)`, seeks when it is more
  than 150 ms out, and inside that nudges the playback rate by up to ±3 %. Between videos it is black,
  or holds the last frame for `end: hold`. It plays with sound (the video carries the song, as on the
  cast path), falling back to muted if the device refuses autoplay with sound.
- **Ready** means at least one page is connected (`GET /api/stage` `pages`): none, and a show runs
  without its video, saying "no stage page is open" — or is refused, SHOW_VIDEO_NOT_READY, if it requires
  video. Every page gone for ~4 s mid-show is **lost**: logged, and if the video carried the music the
  tablet takes it over, as on the cast path. Never an abort.

## Latency

The page keeps the Thor's clock as show audio does (ping/pong over its WebSocket, the best of the last
40 round trips), and for each frame with a person it measures **capture → painted** on that clock; the
p50/p95 go back to the dashboard every 2 s (`GET /api/stage`). The projector's own display delay is extra
and cannot be seen from inside it: a phone filming the person and the screen gives the end-to-end figure.
**Target: under ~150 ms** to look live. On the development machine (a synthetic feed, a desktop browser)
capture → paint measured 6–9 ms (p50–p95).

## Getting the page onto the projector (no HDMI unless nothing else works)

To try in this order at the projector (a Nebula P1i, Google TV):

1. **The kiosk WebView app, launched over ADB** (Tablet lane): once, with the remote, Developer options →
   Wireless debugging and accepting the Thor's pairing; then the app is pointed at `/stage/` and stays on
   it. No registration, no HTTPS (http and ws on the robot's LAN); autoplay with sound is allowed.
2. **Cast, no install:** a public receiver app that loads a URL (pychromecast's DashCast) — needs the
   projector online, a third-party receiver (the user's call), and an http page to load from it.
3. **Our own Cast receiver:** registered ($5), served over HTTPS, a `wss://` the projector trusts. Heavy.
4. **HDMI from the Thor** (the fallback): needs the desktop off (Thor lane).

## Not yet

- The camera's mount and TF frame (Thor lane, once the user gives the mount).
