# Choosing where a show's sound plays

Choreography lane, with the Tablet lane for the stage box and the dashboard picker. Proposed
2026-10-28. **Not built**: this is the design for review, to be built after the demo freeze.

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

- **Outputs, not a role.** Every page or process that can play show audio registers on
  `/ws/show-audio` with a name and a kind: `tablet` (the head tablet), `speakerphone` (the ARCHITECT
  listener), `stage` (the stage box). Connecting no longer takes over anything. It only adds that
  device to the list. `GET /api/choreo/audio` lists each output and whether it is ready.
- **Who chooses.** For each run, in this order of precedence:
  1. the operator's choice for the next run (`POST /api/choreo/audio/output`, beside the video one);
  2. the show's own `music: { output: … }` (the field already exists in `cue.py`);
  3. the robot's default: `stage` when the run's video plays on the stage box, otherwise `tablet`.
- **No quiet fallback.** If the chosen output is not connected or not ready by GO, the run is refused
  with `SHOW_AUDIO_OUTPUT_MISSING`, which names the output and lists the connected ones. Today's
  behaviour, running silently with a warning, goes away for a named output.
- **ARCHITECT keeps its speaker.** Its candy show names `music: { output: speakerphone }`, so its runs
  still play on the speakerphone without it taking every other show's music. The listener's change, from
  claiming the role to registering as `speakerphone`, is the ARCHITECT lane's to make, agreed through the
  orchestrator.
- **Ambient music, `/play/` files and AirPlay** stay on the `tablet` output as they are now.

## 3. Who builds what

| Part | Lane |
|---|---|
| The hub's output list, `set_audio_output` in the runner, the precedence, the fault code and its registry entry | Choreography |
| The stage page joining `/ws/show-audio` as `stage`, and playing audio-only items on the show's clock through its Bluetooth output (it already does this for video) | Tablet (`software/stage-pi/`) |
| The picker beside the video output on the projector card or the Operate page | Tablet |
| The listener registering as `speakerphone` | ARCHITECT, through the orchestrator |

## 4. Open questions

- Should the operator's choice last for one run, or until it is changed? It should behave the same way as
  the video choice does.
- Should the stage box's speaker be the default for every show while it is connected, not only for
  shows whose video plays there?
