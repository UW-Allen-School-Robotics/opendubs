# Show outputs for Spot and for group shows: sound, lights, buzzer

Status: proposed by the Spot lane and **taken on by ENCORE**, 2026-11-19. ENCORE owns the show format
and will write it as an ENCORE proposal once Spot's motion playback is proven; lights and the buzzer
come after motion. The Tablet lane owns the stage box. Nothing here is built.

**ENCORE's changes to the proposal:**
- Outputs join the venue's existing `outputs:` list, beside screens (`plays: music`,
  `plays: <robot>.voice`).
- Build refuses an output with no device, or with two.
- The prefix is the show's robot name (its key under `robots:`), not the robot's kind, so a show with
  two Spots works: `spot.voice`, `spot2.light`.
- ENCORE agrees: one `music` per show, and any robot's stop stops the music.
- Dialogue stays on the tablet face, so `od.voice` is the head tablet.

## The rule this extends

opendubs2's shows already route sound by **role**, not by device: dialogue plays on the head tablet,
music plays on the projector or the stage box with the tablet as fallback (`.claude/skills/opendubs-show/
REFERENCE.md`, "Outputs"). A show names what a sound is; the venue decides which device plays it.

The proposal: every sound and light cue in an ENCORE show goes to a named **output**, and each output
has exactly one device per venue. Adding Spot adds outputs, not devices in the show file.

## The outputs

| Output | What it carries | Plays on | Notes |
|---|---|---|---|
| `music` | the song, the show's bed | the stage box; else the projector; else one robot's speaker | **one device per show**, so a group show has one music clock |
| `od.voice` | opendubs2's lines and effects (today's `dialogue`) | the head tablet; an onboard speaker if one is fitted | near the robot that speaks |
| `od.light` | opendubs2's LIGHT cues | through its dashboard, as today | |
| `spot.voice` | Spot's lines and effects | to decide (below) | Spot has no speaker of its own |
| `spot.light` | Spot's body LEDs | Spot, via the SDK's AudioVisual service | never hides Spot's own warning lights |
| `spot.buzzer` | short tones: cues, beats, a "ready" chirp | Spot's piezo, via the same service | tones only, not music |

## What Spot offers (SDK 5.1.4, read from the API; not yet tried on this robot)

The SDK has an **AudioVisual service** (`bosdyn.client.audio_visual`):
- **LEDs:** groups `front_center`, `front_left`, `front_right`, `hind_left`, `hind_right`,
  `status_lights` (and `spot_cam`), each a solid colour, blink, pulse, synced blink, or a keyframed
  animation.
- **Buzzer:** a sequence of notes, each with a duration.
- A **behaviour** is added by name, then run until an `end_time` on Spot's clock, with a priority.
  System settings cap the brightness and the buzzer volume.

So `spot.light` and `spot.buzzer` cues become behaviours the Spot player sends ahead of time, like the
motion windows, with `end_time` mapped through TimeSync. Two things to check at Spot, read-only, before
any of this is built:
1. that this Spot (release 5.1.5) has the AudioVisual system (`software/spot/read_limits.py` prints
   `has_audio_visual_system` and the directory's audio and light services);
2. how show behaviours rank against Spot's own status and warning lights. A show must never mask the
   lights that tell a bystander the motors are on.

## Group shows: one clock

- Each player (opendubs2's runner, the Spot player, the stage box) holds its show locally before the
  run, and each operator arms their own robot.
- ENCORE sends every player `start(at)` with one wall-clock t0. Clocks agree through NTP on the robot
  network; Spot's own clock is mapped by the SDK's TimeSync.
- The music plays from one device only. Robots never each play a copy of the song: two copies drift
  audibly.
- Any robot's stop stops that robot. Whether it also stops the music and the other robots is ENCORE's
  rule; the safe default is that the music stops when any robot stops, as an opendubs2 run does today.

## Decisions for the owner

**Decided by the owner, 2026-11-20:** Spot has no voice in the first group show; the music comes from
the stage box only. The rest of this section is kept as the record of the options.

1. **Spot's voice.** Choices:
   - none (music only);
   - a powered speaker on the Orin or the Pi that runs the Spot player (it plays `spot.voice` cues
     from its local copy);
   - a second channel on the stage box.
   A Spot CAM has a speaker, but it is not known whether one is fitted. (192.168.50.6 is Spot CAM's
   reserved address; Spot's service directory will say.)
2. **A Spot-only show's music.** The stage box today shows opendubs2's `/stage/` page. For a Spot-only
   show it needs another host for that page (the Spot player could serve it). That is the Tablet
   lane's call.
3. **Lights and buzzer priority:** whether they go in the first group show, or after motion is proven.
   The Spot lane suggests after.
