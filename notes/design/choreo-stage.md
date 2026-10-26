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

- **A show's video plays clean.** The skeleton layer (the dancer, a live person) is drawn over a show's video
  only when the show says `video: {skeletons: true}`, as Them Bones does; any other show's video has nothing
  drawn over it, and the canvas is not redrawn per frame. Between shows, and for a show with no picture, the
  page's own scene (the light table and the dancer) is shown as before.
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
- **Deploys:** the page polls `/api/frontend-version` every 15 s, as the dashboard does, and reloads
  itself when it changes, but only between shows, so a deploy reaches the stage box with no one there.

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
  `now_on_the_Thor − t0_mono`; it plays the video at `from_s + (show time − t)`. A silent video is
  seeked when it is more than 150 ms out, and inside that its playback rate is nudged by up to ±3 %. A
  video that is heard keeps its rate at exactly 1, because any rate change time-stretches the sound and
  a Pi plays that choppy; it is seeked only past 400 ms, at most every 5 s. Between videos it is black,
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

## The stage Pi — the standard way onto a screen (decided 2026-10-21)

A Raspberry Pi kept on the robot and plugged into whatever screen there is (a projector, a building's
video wall, a TV) runs `/stage/` full-screen. The image is the Tablet lane's (`software/stage-pi`, #477).
It plays the show's video **and the show's music**: the song inside the video (the #449 rule), or the
song alone when a show has no video. The tablet keeps dialogue.

- **Any screen.** The page never assumes 1920×1080 or any aspect (checked headless at 1920×1080, 3840×1080,
  5760×1080, 1080×1920, 1366×768 and 4096×2160: the whole picture every time, never cropped):
  - the video is letterboxed or pillarboxed to its own aspect (`object-fit: contain`);
  - the skeletons are placed and scaled inside the video's picture, by its height and never wider than it;
  - the canvas is sized only when the viewport changes, and capped at 4K pixels on a very large wall, and at 1080p's on a stage box, whose
    screen scales it up (a Pi drawing a 4K canvas every frame would starve the video).
- **A screen that stretches its input** — a video wall that spreads a 16:9 frame over its own 32:9 (the
  building's 6 × 3 wall, 729 × 206 cm) — is set with `?stretch=2` on the page's address (a stage box: in
  `stage.conf`'s `ROBOT_URL` and `ROBOT_FALLBACK`). The page then lays itself out at the wall's true shape
  (3840 × 1080 for a 1920 × 1080 frame) and squeezes that into the frame, so the wall shows everything in
  proportion: a 16:9 show video fills the middle half (three of the six columns), the ready card and the
  skeletons are their own shape, and a pattern drawn at 3840 × 1080 square pixels lands true. Checked
  headless against a simulated 2× wall: a drawn circle and square come out at their drawn sizes. The frame
  holds only 1920 columns, so the wall's horizontal detail is two of those pixels at best. Off (1) everywhere
  else; the card's Screen row says when it is on.
- **No screen.** On a Pi with only a speaker, the browser runs on a virtual output and may never draw.
  The show's media is also kept in step from a 250 ms timer, so the music plays to the show's clock with
  nothing drawn.
- **The STAGE READY card**, between shows, shows:
  - whether the robot is connected (the page's WebSocket);
  - the screen's resolution (and the mode the Pi reports);
  - the sound output (USB, HDMI or none, and the device's name);
  - the clock to the Thor;
  - an under-voltage warning (now, or since boot).

  Once a show is **armed** (loaded) the screen goes black, and stays black until the show starts. `?card=0`
  hides the card.
- **The clock.** The page measures its own offset to the Thor's show clock over its WebSocket (ping/pong,
  the best of the last 40 round trips, refreshed every 10 s), as show audio does. No chrony is needed on the
  Pi. The card shows the error bound.
- **The audio path.** The box plays the show's music through ONE output it selects: `hdmi`, `jack` (the Pi 4's
  3.5 mm), `usb` (a USB adapter) or `bt` (a paired Bluetooth speaker), chosen on the box's card (Tablet lane,
  #554) and changed only between shows. The page plays to whatever the box selected; it picks only the
  **audio lead**, the time a heard item (the video carrying the song, or the song alone) starts early:
  1. the robot's lead for that output, set by ear: `projector.json` `stage_audio_leads_s` {hdmi, jack, usb, bt},
     nudged on the projector card for the output the stage page reports, so each output keeps its own;
  2. else the box's estimate of that output's delay (`device.json` `audio_latency_ms`: hdmi 40, usb 20, jack
     20, bt 200) plus `stage_audio_lead_s`, the stage's general trim.

  The lead is fixed when the show starts, so a changing estimate never seeks the music. One lead serves the
  picture too: the browser holds the picture back to match its sound output's delay. A picture alone starts
  by `stage_video_lead_s` (the screen's picture delay). The page reports the lead it used (`audio_lead_ms`,
  `audio_lead_from`), with the box's `audio_setting` (what was chosen) beside `audio_output` (what plays).
- **The video, like a streaming service (decided 2026-10-21).** The robot's Wi-Fi is solid in the same
  room, usually fine at 10–25 m in line of sight, and marginal through walls.
  1. **The box's own copy first.** The stage box keeps copies of the library's videos and songs on its
     media stick, synced while idle from the robot's existing library routes (`GET /api/library/index`,
     `GET /api/library/item/<sha256>`, checksum-verified), never while a show is armed or running. The page
     asks `http://127.0.0.1:8095/media/<sha256>?id=<library id>` and, given a copy, plays it from there:
     instant, no Wi-Fi involved.
  2. **Otherwise stream, with a head start.** When the show is armed the page points its video at the robot
     and lets it buffer from where the show starts. The GO waits until the buffer reaches `stage_buffer_s`
     (projector.json, 25 s, 5–120). There is one exception: a paused video buffers only so far. Chromium
     caps it in bytes (about 14 MB measured, which is 14 s of an 8 Mbit/s file and about a minute of a
     2 Mbit/s show video). So when the browser has stopped loading because it can already play through,
     the head start it holds counts, provided it is at least 8 s. While playing it keeps reading ahead.
     - The page reports `{"preload": {id, ready, local, buffered_s, need_s}}`.
     - The Shows page says "the stage video (buffering, 12 of 25 s)" until every page is ready.
     - The wait allows up to `STAGE_BUFFER_WAIT_S` (90 s). Past it the stage is dropped and the tablet keeps
       the music; a show with `video: {required: true}` is refused instead (SHOW_VIDEO_NOT_READY).
  3. **A stall mid-show is a dropout.**
     - A page that runs dry for 1.5 s reports `{"stall": {id, at_s}}`.
     - The runner logs it, mutes the stage pages (`mute`), and the tablet takes the music from the same
       point. The show never stops for it.
     - The page catches up on the show's clock when the data returns.
- **The fallback** is unchanged. If every stage page is gone for about 4 s mid-show, the tablet takes the
  music over from the same point (it held its copy, silent), and the show goes on.

The contract with the Pi (agreed with the Tablet lane):

| what | where | |
|---|---|---|
| device status | `GET http://127.0.0.1:8095/device.json` (CORS `*`, the Private Network Access preflight answered) | `{"device": "stage-pi", "name", "model", "audio_output": hdmi\|jack\|usb\|bt\|none (what plays), "audio_setting": auto\|hdmi\|jack\|usb\|bt (what was chosen), "audio_latency_ms", "audio_name", "under_voltage_now", "under_voltage_since_boot", "throttled_now" (null when unreadable), "screen": {"connected", "mode"}}` |
| alive | `POST http://127.0.0.1:8095/alive` every 5 s | from the draw loop, so a page that stops drawing stops checking in. With no screen it is sent from the sync timer instead. The Pi restarts the browser after 30 s of silence |
| local copies | `HEAD/GET http://127.0.0.1:8095/media/<sha256>?id=<library id>` | 200/206 with byte ranges, or 404; only verified copies. `device.json` adds `"media": {"items", "bytes", "free", "syncing"}` (null without a media stick), shown on the ready card |
| upward | `{"page": {...}}` every 5 s over `/ws/skeleton` | the device's fields (the screen's flattened to `screen_connected`, `screen_mode`), plus `viewport`, `dpr`, `clock_err_ms`, and `sound` (`on`, or `muted` when the browser refused autoplay with sound). The dashboard keeps the newest per page (`GET /api/stage` → `pages_info`); the projector card reads the first as `outputs.stage.page` |
| browser | Chromium `--kiosk --autoplay-policy=no-user-gesture-required`, no window size forced | |

In any other browser `device.json` does not answer: the card says "unknown (not a stage device)", and the
page asks again only once a minute.

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
