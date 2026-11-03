# Choosing where a show's sound plays

Choreography lane, with the Tablet lane for the stage box and the dashboard picker. Proposed and
built 2026-10-28; the user lifted the demo freeze for it. The stage box's sound report and the
projector card's picker are the Tablet lane's (#592).

## 1. Why

On 2026-10-28 the music for The Monster Mash came out of the speakerphone on the Thor, the one the
ARCHITECT listener uses, when it should have come from the stage box's Bluetooth speaker. What the
robot showed:

- `GET /api/choreo/audio` returned `holder: "Jabra"`. The ARCHITECT listener, started with
  `--show-audio`, claims the dashboard's show-audio role when it connects, so that a show's music
  plays from the speakerphone instead of the head tablet.
- The hub (`backend/show_audio.py`) gives every show's music to the last device that claimed the role.
  No show and no operator chooses it.
- The run sent its VIDEO to `cast` (the default), the projector was not on the robot's Wi-Fi, so no
  video played. The music went to "the tablet's show audio", which meant the holder: the speakerphone.
- The Thor's default PipeWire sink was not involved. The listener opens its device by ALSA card name.

A show whose video is on the stage box is already right: the music is inside the video, the stage box
plays it on its own speaker, and the role holder only keeps a copy in reserve (`held`), which it plays
only if the video fails during the show.

**Workaround until this is built:** choose the `stage` video output on the projector card before the
show.

## 2. The design

Show audio becomes a named output chosen at play time, the same way the video output already works
(`set_video_output`, the projector card).

- **Outputs, not a role.** A device that plays show audio claims an output on `/ws/show-audio`:
  `tablet` (the head tablet; a claim that names no output is this one, as before) or `speakerphone`
  (the ARCHITECT listener). Each output has its own holder, so claiming one never takes the other.
  `GET /api/choreo/audio` lists them. The third device, `stage`, is the stage box's speaker: the
  runner reaches it through the stage page, which already plays a show's song on the show's clock
  from the box's own copy (`audio: true` items), so it does not join the hub.
- **Where it's chosen.** `GET` / `POST /api/choreo/audio/output` (`{output: tablet|speakerphone|stage|null}`)
  lists each device, ready or why not, and sets the operator's choice. It lasts until changed, as the
  video's does, and can't change while a show plays (`SHOW_AUDIO_BUSY`).
- **Who chooses.** For each run, in this order of precedence:
  1. the operator's choice for the next run (`POST /api/choreo/audio/output`, beside the video one);
  2. the show's own `music: { output: … }` (the field already exists in `cue.py`);
  3. the robot's default: `stage` when the run's video plays on the stage box, otherwise `tablet`.
- **No quiet fallback.** If the run's device is not connected at preflight (a hub output gets 3 s to
  appear, for a tablet changing page), or the stage box reports no sound output, the run is refused
  with `SHOW_AUDIO_OUTPUT_MISSING`, which names the device, why, and the ready ones. This replaces
  running silently with a warning. Music on the stage box has no copy held anywhere else: if the stage
  drops out mid-show, the music stops and the log says so. A device that drops out after the GO still
  leaves the show running silently (DEC-7: audio trouble never stops motion).
- **A runner with no hub** (a laptop's twin) never refuses on a device.
- **ARCHITECT keeps its speaker.** Its candy show names `music: { output: speakerphone }`, so its runs
  still play on the speakerphone without it taking every other show's music. The listener's change, from
  claiming the role to registering as `speakerphone`, is the ARCHITECT lane's to make, agreed through the
  orchestrator.
- **Ambient music, `/play/` files and AirPlay** stay on the `tablet` output as they are now.

## 3. Who builds what

| Part | Lane |
|---|---|
| The hub's outputs, the runner's choice, precedence and refusal, the stage routing, the fault code (#591) | Choreography |
| The stage box reporting `audio_output: "none"` and `audio_why` when it can't make sound, and the page's own guard (#592) | Tablet |
| The "Show sound on" row on the projector card (#592) | Tablet |
| The listener claiming `output: "speakerphone"`, and the candy show's `music: {output: speakerphone}` | ARCHITECT, through the orchestrator |

## 4. Open question

- Should the stage box's speaker be the default for every show while it is connected, not only for
  shows whose video plays there?
