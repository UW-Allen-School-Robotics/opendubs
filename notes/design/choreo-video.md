# Show video on a projector

Choreography lane, with the Tablet, Thor and Network lanes for their parts. Started 2026-10-20 for a
projector arriving the next day: an Anker Nebula P1i (1080p, Google TV with Chromecast built in,
a 20 W speaker; HDMI in presumed, to confirm when unboxed). It sits either on the robot or on the floor.

**Decided (the user):** casting first — the Thor serves the file over Wi-Fi and the projector's built-in
Cast plays it; HDMI from the Thor is the alternative when the projector is on the robot and sync
matters; the output is chosen at design time (per show or per cue) and at play time; and the dashboard
shows whether the projector is set up.

**Built (this PR):** the VIDEO track in the show format, the library's `video` kind, both outputs
(`software/choreo/video_out.py`), the runner playing them on the show's clock with its preflight and
the play-time override, the dashboard routes the projector card reads, and a CLI for the first test.
**Not built:** the HDMI player's service (Thor lane), the projector card (Tablet lane, #441), the
router reservation (Network lane), and the audio question (§5, for the user).

## 1. Video in a show

```yaml
video: { output: cast }            # optional: the show's default output (cast | hdmi | off), and
                                   # required: true to REFUSE the show when that output isn't ready
cues:
  - { bar: "5.1", slot: VIDEO, do: play, video: intro-loop }                   # to its end, then black
  - { t: 42.0,    slot: VIDEO, do: play, video: lab-tour, from_s: 12, secs: 20, end: hold }
  - { t: 70.0,    slot: VIDEO, do: stop }                                      # black now
```

- `video:` is a **library** id of kind `video` (`library.py add FILE --kind video --duration S --width W
  --height H --source … --licence …`). Video media never goes in the repository: the robot yes, the repo
  no. MP4/H.264 up to 1920×1080 plays on any Cast device; WebM and MKV are accepted for HDMI.
  `show_sync.py` sends a show's videos with its songs and skins (`library_sync.py`).
- `play` starts at the cue's time, `from_s` seconds into the file, for `secs` (default: to its end).
  Then `end`: `black` (default) or `hold` (its last frame). The next VIDEO cue ends the one before it.
  One video at a time — one projector — so overlapping VIDEO cues are refused like any slot collision.
- The output is the cue's `output`, else the show's `video: {output}`, else the robot's default
  (`OPENDUBS_VIDEO_OUTPUT`, else `cast`); the operator's play-time choice overrides them all (§4).
  The export carries each VIDEO event's resolved `output`.
- It stops on every end: COMPLETED, a stop, an abort → black; an e-stop → stopped at once.

## 2. The cast path

- **Discovery:** mDNS (`_googlecast._tcp`) from the Thor, refreshed every 10 s by the runner, so the
  card's answer is never older than that. Where multicast does not cross the network, a fixed address
  works without mDNS: `OPENDUBS_CAST_HOST` in `/etc/opendubs/robot.env`. With several Cast devices
  around, `OPENDUBS_CAST_NAME` names this robot's projector.
- **Serving:** the runner serves only the files a run registers (no directory, no other path) from a
  byte-range HTTP server on `OPENDUBS_VIDEO_PORT` (**8094**; 8093 is the voice service), bound at start to
  the robot-LAN interface's address — the interface avahi is limited to (`allow-interfaces`, as the
  AirPlay receiver reads it), or `OPENDUBS_VIDEO_BIND` (an address or an interface name) in robot.env;
  never a written-down address, since the Thor's LAN address is a DHCP lease. Cast receivers fetch and seek with Range
  requests. The URL's host is the Thor's own address toward the projector.
- **Control:** pychromecast, the Default Media Receiver: `load` (BUFFERED, paused at `from_s`) while the
  arms travel to their start pose, `play`, `seek`, `stop`, and a black still for `end: black`.
- **Sync** (`ShowVideo`): the receiver keeps its own clock, so —
  1. **start early** by its measured start latency, `OPENDUBS_CAST_LEAD_S` (default 0.8 s, per robot;
     measure it with `video_out.py play` and the test clip, §8);
  2. **correct with seeks**: every second the receiver's position (its last report, extrapolated to
     now) is compared with the show's; past **±250 ms** it is seeked back, but never within **8 s** of the
     last seek, because a Cast seek re-buffers — a visible hitch of a few hundred ms.
- **How tight, honestly:** the receiver reports its position as status messages, not a stream of
  timestamps, and its playback is not slaved to anything we control. Expect a steady error of about
  **±0.1–0.3 s** against the show's clock after the first correction, plus a **constant** lag of the
  projector's picture processing (typically 50–150 ms on a TV-style pipeline) that the receiver cannot
  report and only the lead can absorb — the test clip shows it. Good for backdrops, titles, scene
  changes and visuals timed to a beat; **not** good enough for lip-sync against sound played elsewhere
  (the tablet), and a correction is a visible hitch. Tighter sync is the HDMI path.

## 3. The HDMI path

- **Player:** `mpv` full screen on the Thor's HDMI out with no desktop session (the kiosk panel is
  retired), as a long-running service that idles on black and is driven by the runner over mpv's JSON
  IPC socket (`OPENDUBS_MPV_SOCKET`, default `/run/opendubs/mpv.sock`): `loadfile … pause=yes`,
  `pause`, `seek absolute+exact`, `speed`, `time-pos`.
- **Sync:** on the Thor's own clock and decoder. It starts on the show's time, is held inside **±40 ms**
  (about a frame at 30 fps) by a 3 % speed nudge, and is seeked only past 250 ms. What remains is the
  projector's constant display lag — measurable, and the same every time.
- **Readiness:** the socket answering, and the kernel's HDMI connector reading `connected`
  (`/sys/class/drm/card*-HDMI-*/status`): no cable, or a projector that is off, says so.
- **For the Thor lane** (a unit of theirs; a draft of what the runner expects):

  ```ini
  [Unit]
  Description=OpenDubs projector video on the HDMI out (mpv, idle on black; the show runner drives it)
  [Service]
  User=kiosk
  SupplementaryGroups=video render
  RuntimeDirectory=opendubs
  RuntimeDirectoryMode=0775
  ExecStart=/usr/bin/mpv --idle=yes --force-window=yes --fs --no-osc --no-input-default-bindings \
            --keep-open=yes --background=color --vo=gpu --gpu-context=drm \
            --input-ipc-server=/run/opendubs/mpv.sock
  Restart=always
  ```

  plus `apt install mpv` and the kiosk user in `video`/`render`, and `--video cast,hdmi` on the runner's
  ExecStart once it is there.
- **The blocker (Thor lane, 2026-10-20):** the Thor's GNOME desktop still runs (gdm, gnome-shell),
  though nothing uses it — the tablet is the only UI — and gdm holds the display, so mpv from a service
  cannot take it. The fix is host config (the multi-user target, no display manager); it changes boot
  behaviour, so it waits for a go. Whether nvidia-drm on JetPack 7 then takes `--vo=drm` (or
  `gpu-context=drm`) is unverified, and can only be tried with the projector plugged in: with nothing on
  the HDMI port there is no connected connector to draw on.

## 4. Choosing the output, and what happens when it isn't ready

- Precedence: the operator's play-time choice → the cue's `output` → the show's `video: {output}` →
  the robot's default. `off` plays no video.
- The play-time choice is set on the projector card (POST, §6) and holds until changed; it is refused
  while a show runs (SHOW_VIDEO_BUSY), because a running show's videos are already on their output.
- At preflight, each output a show's videos use is asked whether it is ready. **Not ready:** the show
  runs without that video, and the run says why (a warning, as show audio does, DEC-7) — or, for a show
  with `video: {required: true}`, it is **refused**, SHOW_VIDEO_NOT_READY, before anything moves, with
  the reason in the card's words. Video is never a safety function: a projector dropping out mid-show
  is logged, never an abort.
- Once #406 (effector needs) lands, `video` is a natural effector: a show needs it, `can_skip:`
  replaces `required:`, and the operator approves running without it. Not done here, to keep the two
  PRs apart.

## 5. Audio — decided 2026-10-20: see [`choreo-audio.md`](choreo-audio.md)

The user chose: music to the projector, dialogue (the default) to the tablet, the tablet as the
fallback. The options as they were proposed:

Today show audio plays on the head's tablet (DEC-5), on the show's clock, with its own clock matching.
With video on the projector, sound and picture are on two devices:

| | where the music plays | drift between picture and music | what it costs |
|---|---|---|---|
| **A. as now** | the tablet | the cast error (±0.1–0.3 s + display lag); HDMI: about a frame + display lag | nothing; the robot's motion stays locked to the music |
| **B. the projector's speaker, by cast** | the projector (the video file carries the music) | none between them | the MUSIC moves onto the cast's clock: motion vs music then carries the cast error, and a seek is an audible skip |
| **C. the projector's speaker, by HDMI** | the projector, from the Thor (mpv plays the soundtrack) | none between them | needs the HDMI path; motion, picture and music all on the Thor's clock — the tightest, but the projector must be on the robot |

**Proposal:** **A** by default (motion and music stay locked; the video is a backdrop); **C** when the
projector is on the robot and the picture must match the music; **B** only for a video that *is* the
act (a film clip with its own soundtrack) and the robot just reacts to it. The user decides.

## 6. The dashboard contract (the projector card: Tablet lane, #441)

`GET /api/choreo/video` (the dashboard forwards it to the runner's `/api/video`):

```json
{ "checked_at": 1760900000.0,
  "outputs": { "cast": {"found": true, "name": "Nebula P1i", "host": "10.42.2.40", "ready": true, "why": null},
               "hdmi": {"present": false, "ready": false, "why": "the HDMI player isn't running on the robot"} },
  "default": "cast", "selected": "cast", "override": null,
  "playing": {"show": "info_show", "file": "lab-tour", "output": "cast", "position_s": 31.4,
              "sync_err_ms": -120, "seeks": 1},
  "note": null }
```

- `checked_at`: epoch seconds of the cast check (refreshed every 10 s); the card calls an answer older
  than 15 s "not checked". `why` is always plain operator words, never an exception; the detail goes to
  the runner's log.
- `selected` is what a show would use now; `override` is the operator's choice (null: each show's own).
  `playing` is null when nothing plays; `note` says why a running show runs without its video.
- `POST /api/choreo/video/output {"output": "cast"|"hdmi"|"off"|null}` → the whole GET body; refused
  while a show runs: 409 `{"error": "…", "code": "SHOW_VIDEO_BUSY"}`. Both codes are in
  `docs/reference/registry/shows.yaml`.

## 7. Network (the Network lane's answers, 2026-10-20)

- The projector joins the robot AP **Opendubs2** (2.4 GHz, WPA2) — nothing blocks it. A DHCP
  reservation is a one-line router change on the user's word; turn off the projector's randomised Wi-Fi
  MAC (or read the one it uses) so the reservation matches. The reserved address is also the
  `OPENDUBS_CAST_HOST` fallback.
- mDNS crosses: the AP and the Thor's wired port are one bridge, no client isolation, multicast left as
  multicast. (Bridge IGMP snooping unread; it would forward 224.0.0.x anyway.)
- No router firewall between the Thor and the projector (bridged, not routed). The Thor's own firewall,
  if any, must allow inbound 8094 from the LAN (Thor lane: nothing in the repo configures one).
- 2.4 GHz carries one 8–10 Mbit/s stream comfortably (the AP gives ~25–35 Mbit/s real); aim for the
  projector's signal at −65 dBm or better. Cast buffers, so a dip need not stall it.

## 8. The first test, when it arrives

1. Unbox: note the inputs (HDMI in?), turn off randomised MAC, join **Opendubs2**; send the MAC to the
   Network lane for a reservation.
2. On the Thor, with the deploy that carries this PR: `python3 /opt/opendubs/choreo/video_out.py discover`
   — the projector by name. (No mDNS? `--device <its IP>` everywhere below, and `OPENDUBS_CAST_HOST`.)
3. Make the sync clip anywhere with ffmpeg: `python3 video_out.py testclip /tmp/sync.mp4 --secs 60`
   (a running clock and a beep each second; keep it out of the repo).
4. `python3 video_out.py play /tmp/sync.mp4 --to cast --secs 60`: it prints the error each second and
   any seek. Watch the projected clock against a stopwatch started at the "playing" line to see the
   display lag; adjust `--lead` until the projected second flips with the beep from a phone playing the
   same clip, and put that number in `OPENDUBS_CAST_LEAD_S`.
5. A show: add the clip to the library (`--kind video`), a VIDEO cue in a test show, run it from the
   Shows page and read the card.

## 9. Owners

| piece | lane |
|---|---|
| the format, `video_out.py`, the runner, the dashboard routes, `provision-runner.sh` (pychromecast) | Choreography (this PR) |
| the desktop off (multi-user target, no display manager — gdm holds the display, so mpv cannot), on a go; then `apt install mpv`, the `opendubs-video-hdmi` unit, kiosk in `video`/`render`, tested at the robot with the projector plugged in | Thor stack |
| `--video cast,hdmi` on the runner's unit, once the Thor lane says the HDMI player is live | Choreography |
| the projector card (`frontend/projector.js`, #441) | Tablet and head interface |
| the projector's DHCP reservation, Wi-Fi checks | Network |
| §5 audio, and turning video into an effector after #406 | the user |

## 10. The twin's projection screen (2026-10-21)

The show's video, seen in the MuJoCo twin, in step with the twin and the face — so a show with video
can be authored and reviewed without the projector.

- **Where:** a wall BEHIND the robot (the user's choice), so the audience sees the robot in front of the
  image. Fixed to the world, not the base: it stays put while the robot dances. No floor option and no
  per-show placement yet.
- **What:** an emissive 16:9 textured plane, built the way the face screen is (`sim/scene.py`
  `_add_projection`), built hidden and shown only for a show with a VIDEO track (`set_projection_visible`).
- **Timing:** the picture is chosen by the SHOW'S CLOCK — the same one that poses the twin and drives the
  face — from the export's VIDEO track (`software/choreo/projection.py` `at`): `from_s` into the file plus
  the time since its cue, black or its last frame (`end: hold`) after, black before; the next VIDEO cue
  or a `stop` cuts it. The twin shows the show's INTENDED timing: no cast latency is modelled — the real
  projector's delay is the video lead, set by ear at the robot (`choreo-audio.md`).
- **Frames:** each library video is decoded once, at 640×360 and 15 fps, letterboxed to 16:9, into a cache
  of JPEG frames (`~/.cache/opendubs/projection/`; a 3-minute video is tens of MB, where raw frames would be
  gigabytes), in the background so the viewer never stalls; a frame is uploaded only when the picture
  changes. ffmpeg decodes (PATH, or imageio-ffmpeg); without it, or for a video not in this machine's
  library, the screen stays black and a note says why.
- **Both twins:** the live viewer (`twin_view.py`, following the composer or the runner) and the offline
  renders (`render.py`, which also frames the camera to include the screen).
- **Placeholders, in one place** (`sim/scene.py`, the PROJ_ block): image width 2.0 m (16:9), 1.2 m behind
  the robot's start, lower edge 0.35 m up, centred. Put the Nebula P1i's real throw and image size there
  once it is set up.
- The music keeps playing in the composer as before; nothing here plays sound.
