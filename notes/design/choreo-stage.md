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

- Full-screen canvas, optionally over a video (`?video=`, the show's stage cut: bed + hits + song, no
  skeleton baked in — library `them-bones-stage`). `?hud=1` shows the feed, the frame rate and the latency.
- **The person:** the most confident, nearest one; joints under 0.3 confidence left out; **mirrored** (they
  see themselves as in a mirror); scaled to fit the screen's height; placed where they stand (the camera's
  centre at the screen's centre), kept on the screen.
- **The hand-over:** a person in a fresh frame (< 0.5 s old) fades in over ~0.4 s while the dancer fades
  out; 1.5 s after they leave, the dancer fades back.
- **The dancer** (the fallback figure, and Them Bones' base layer): the same cartoon skeleton on the beat
  (`?bpm=`). The offline renders use the richer per-section dance (`.generator/bones_dancer.py`).
- **The kiosk:** `window.OpenDubsKiosk?.alive?.()` every 5 s from the draw loop, so a page that stops
  drawing stops checking in and the app reloads it; it reconnects its WebSocket itself, with back-off.

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

- The video under the skeletons following the SHOW's clock (the runner starting it, like show audio):
  the page takes `?video=` for the first test; the runner's "stage" output comes next, on the user's
  choice of route.
- The camera's mount and TF frame (Thor lane, once the user gives the mount).
