# Show audio with a projector: music on the projector, dialogue on the tablet

Choreography lane. Builds on [`choreo-video.md`](choreo-video.md) (the projector's outputs). Decided by
the user on 2026-10-20; this note is the design and what was built.

## The rule

- Every sound a show plays is **music** or **dialogue**. Music: the soundtrack, background, effects.
  Dialogue: scripted lines, and anything the voice assistant says.
- **Dialogue is the default.** Anything not marked music is dialogue, so speech can never end up on the
  projector — a show's narration track (`info_show`'s, say) stays on the tablet unless someone marks it.
- A **video's own soundtrack is music** (it plays with the picture, on the projector).
- **Projector ready** (the preflight #443 already does): music plays from the projector's speaker,
  dialogue from the tablet — always.
- **No projector:** everything plays from the tablet, as today.
- **The projector drops out mid-show:** the music carries on from the tablet at the same point in the
  song, and the show does not stop.

## Marking (format, and the show editor)

```yaml
music: { song: paddle-set-3, bpm: 120, offset_s: 0.5, role: music }   # the show's track: music
cues:
  - { t: 12.0, slot: SOUND, do: play, sound: whoosh, role: music }      # an effect: music
  - { t: 14.0, slot: SOUND, do: play, sound: line-hello }               # no role: dialogue
```

- `role: music | dialogue` on the `music:` block and on a SOUND cue; absent is `dialogue`. The export
  carries each one's resolved role.
- **The show editor:** the ♫ music dialog has *plays as: dialogue (always the tablet) / music (the
  projector, when one is ready)*; a SOUND cue's inspector has the same choice. Nothing else changes for
  an author — a show that says nothing behaves exactly as before.
- A master track that mixes a song and speech (the event set's) is one file: it is dialogue unless it is
  split. Marking it music would put the speech on the projector — the reason the default is dialogue.

## Where each sound goes (the runner)

At preflight, after the video outputs are asked (#443):

1. **The projector** is the output a show's video would use now — the operator's play-time choice, else
   the robot's default — if it is ready and not `off`.
2. **Music** goes to the projector when there is one, and when the show casts **no video** of its own:
   a Cast receiver plays one media item at a time, so a video would cut the music (and a video's own
   soundtrack is the music then). Over HDMI the same holds for v1. Otherwise music stays on the tablet,
   and the run says why.
3. **Dialogue** always goes to the tablet's show-audio device (DEC-5), and the voice assistant's speech
   keeps its own path to the tablet — nothing routes either to the projector.

The music is loaded on the tablet **too, held**: fetched and decoded, not played. That is what makes the
fallback instant.

## A video that carries the show's song (2026-10-21, for Thriller)

A show's music can travel INSIDE its projector video, so picture and music come from one file and
every visual hit lands exactly on its beat. A library video says so: `song: <id>` (`library.py add …
--kind video --song thriller`), its soundtrack being that song from the video's start. When a show casts
such a video in step with its own music (the VIDEO cue's `t` equal to its `from_s`, the song starting at
show time 0):

- the projector plays the music (the video's own soundtrack — music, by the rule above), whatever the
  music block's role, and the tablet loads the show's song **held**, as the silent fallback;
- the video is started by the projector's **audio** offset, not its video offset: the beat is what has to
  land with the arms;
- if the video drops out, the tablet takes the music over from the same point (`unhold`), as for music
  sent alone.

Otherwise a show that casts its own video keeps its music on the tablet, as before.

## Sync: on the beat with the arms

- The projector starts the music **early by its measured audio delay**, then holds it to the show's
  clock as the video does (#443: a seek past 250 ms, never within 8 s — for music a seek is an audible
  skip, so it is the exception; the receiver's clock and the Thor's drift by milliseconds a song).
- **The offset is per projector and lives on the robot**, never in the repo:
  `/var/lib/opendubs-runner/projector.json` (`audio_lead_s`, and `video_lead_s` for pictures), written
  by the runner, with `OPENDUBS_CAST_AUDIO_LEAD_S` / `OPENDUBS_CAST_LEAD_S` in robot.env as the
  starting values. The projector card (Tablet lane) shows it with a nudge (±25 ms), so the user can set
  it at the robot **by ear**: run a show, listen to whether the arms land on the beat, nudge, run again.
  No scripted sync test.
- **If casting cannot hold the beat**, the answer is HDMI once the projector is on the robot: the music
  then plays from the Thor (mpv), on the Thor's clock, and only the projector's fixed audio delay is left
  to set.

## The fallback

- The projector's music runs in its own thread (`ShowVideo`, #443). It reports **lost** when a command
  to the projector fails, when its Cast connection drops, or when the receiver stops playing on its own.
- Lost, the runner sends the tablet **`unhold`**: the held music starts in step at the show's current
  position (the tablet player already joins items mid-way, as a late start does), and the run logs it.
  The show goes on; the projector is not asked again this run.
- If the projector is not ready at the start, there is nothing to fall back from: the music is simply
  routed to the tablet, unheld.

## To check when the projector arrives (2026-10-21)

- **Does the Nebula P1i play sound alone over Cast?** `video_out.py play SONG.m4a --to cast` answers it.
  If it does not, the workaround is built in: `music_as: video` (projector.json, or
  `OPENDUBS_CAST_MUSIC_AS=video`) makes the runner send the song as an MP4 with a black still picture,
  muxed once by ffmpeg on the Thor and cached beside the library (`.cast-cache/`). No ffmpeg there →
  the music stays on the tablet, said so.

## The pieces, and whose

| piece | lane |
|---|---|
| `role` in the format, the editor's control, the routing, the held music and `unhold`, the projector music and its fallback, `projector.json` and its API | Choreography (this PR) — `frontend/show_audio.js` and `backend/show_audio.py` are this lane's |
| the voice assistant's speech stays on the tablet: no change (it never touches the show runner) | Voice (confirmed, no edit) |
| the face page: no change; the projector card gains the offset nudge (`audio_lead_s`) | Tablet and head interface |
| `OPENDUBS_CAST_AUDIO_LEAD_S` starting value in robot.env, if wanted | Thor stack |

## The API additions (the projector card)

`GET /api/choreo/video` (#443) gains:

```json
"settings": { "audio_lead_s": 0.8, "video_lead_s": 0.8, "music_as": "audio" },
"music": { "on": "projector" | "tablet" | null, "why": "…" | null }
```

`POST /api/choreo/video/settings {"audio_lead_s": 0.85}` (any of the three keys) → the whole GET body;
stored in projector.json on the robot and used from the next run. `music.on` is where the current run's
music is (null: no run, or no music); `why` says why it is not on the projector, in plain words.
