# The show's screen track — music-video mode and eye skins

**Status:** design, 2026-10-08, **proposed for the user's approval**. Agreed between the
choreography lane (show format, validator, composer, runner, twins, the show-audio channel) and
the tablet lane (head protocol, face engine). Nothing here is built. Plan item:
[`../workplan-choreography.md`](../workplan-choreography.md) 4.6.

Companion notes: [`choreo-format-v1.md`](choreo-format-v1.md) (the format this extends),
`face-engine.md` §D (the tablet lane's side; it points here as the canonical version), and the
show-audio channel in `software/kiosk-dashboard/backend/show_audio.py` (plan 4.2).

## What it is

Two things a show can drive once the tablet is the robot's face:

* **Eye skins** — the eyes replaced by any animated object (a themed set of eyes), chosen by the
  show and held like a theme.
* **Music-video mode** — the screen cuts between the face and full-screen video or animation, on
  the beat, and back.

## Decisions

1. **Media rides the show-audio channel, not the head protocol.** That channel already does what
   sync needs: it downloads files before the show, matches the device's clock to the Thor's (±2.7 ms
   on opendubs2), starts at a Thor time, fades on abort and cuts on an e-stop. Video on the same
   channel shares ONE clock with the music, so the two cannot drift apart.
2. **Scheduled up front, never live.** The whole screen timeline travels in `load`, in show
   seconds, and the one `start {t0_mono}` anchors it, as audio items are anchored today. Nothing is
   sent while the show runs, so a late packet cannot make a late cut. The only live messages stay
   `stop {fade_s}` (an abort: fade back to the face) and `cut` (an e-stop: the face, at once). The
   composer, the twins and the robot all render the same timeline document.
3. **Two roles, `audio` and `screen`**, each held by one client. `screen` belongs to whichever
   device is the face. While the tablet is the face, its face page claims BOTH — one clock mapping
   for picture and sound (and it settles the tablet plan's TH-1: audio no longer depends on a WebView
   hidden behind the face). Lifted into controller mode, the dashboard page claims `audio` and
   `screen` stays unclaimed. The hub lets any client hold either role; were they ever split across
   two clients, each is within ±3 ms of the Thor, so within ±6 ms of each other — under a frame.
4. **Not ready in time:** the show runs on the face, with no cuts and a warning — the same rule as
   audio (DEC-7) — unless the show says `requires: { screen: essential }`, in which case it is
   refused.
5. **One library for everything a show plays**: songs, clips and skins (later overlays, themes,
   sounds). Each item has a library id and a content hash, with its source and licence recorded.
   Nothing in the library is ever committed; shows name items by id. Show music moves there too
   (today it is a git-ignored file in `shows/audio/`, referenced by path).
6. **A reserved robot state takes the screen by itself.** If the robot goes DEGRADED or E_STOP
   while media is showing, the face engine drops the media layer at once — it hears the robot's
   state directly (STATE, and the StamPLC's broadcast) and does not wait for the runner's `cut`.
   The system layer is always on top and never authorable: the same rule as "a reserved state
   shows the default eyes, whatever SKIN says".
7. **Eye skins are a face cue, not media**: `SKIN <name>` is head protocol v2 (tablet lane), sent
   by the runner's LiveFace like THEME, persistent until changed. A Tab5 answers `ERR unknown` — the
   runner logs it once and plays on, as it did for GAZE before the head had it.

## The channel

Today's `/ws/show-audio` hub and `POST /api/choreo/audio`, extended. Existing behaviour is kept:
a `load` with only today's `items` is an audio load.

| Message | From | Meaning |
|---|---|---|
| `claim {role}` | page | hold `audio` or `screen` (or both); the previous holder of that role is told `cut` |
| `load {id, audio: [...], screen: {...}}` | runner | everything this show plays, by role; each role's holder fetches its part |
| `ready {id, role}` | page | that role's part is on the device; for `screen`, every file is downloaded and the FIRST shots are decoded and paused |
| `start {id, t0_mono}` | runner | show time 0 is this Thor-monotonic instant; every item is scheduled from it |
| `stop {fade_s}` | runner | an abort: audio fades; the screen fades back to the face |
| `cut` | runner | an e-stop: silence and the face, at once |

`screen` is a list of **shots**, each `{at, until, layer, clip?, from?, loop?}` in show seconds:
`layer` is `face` or `media` (later `overlay`); a media shot names a library `clip` and where in it
to start. Between shots the face shows. The runner builds this list from the show's SCREEN track.

Readiness: the runner waits (its WAITING phase, as for audio) until every role that has items has
reported ready, or a deadline passes — then rule 4. `ready` does NOT mean every clip is decoded:
Android has a limited number of hardware video decoders (on the tablet's MT6789, not yet measured),
and many paused video elements could fail or fall back to software decoding. The face engine
preloads the next shots on a rolling window ahead of the playhead; the window is the tablet lane's
to size, from a bench measurement of the decoder limit and a cut's real latency (before build step 4).

## In the show format

A new slot, **SCREEN**, and a new FACE action:

```yaml
requires:
  screen: essential            # optional: refuse rather than play on the face if media is not ready
music:
  song: example-song           # a library id (replaces `file:`; `file:` stays valid for now)
cues:
  - { bar: "9.1",  slot: SCREEN, do: show, clip: example-clip, from: 0 }   # cut to the clip
  - { bar: "11.1", slot: SCREEN, do: face }                                # cut back to the face
  - { bar: "1.1",  slot: FACE, do: skin, name: example-eyes }              # head protocol v2
```

Clips are **silent** in a show: the show's music is the soundtrack (see Open questions).

**Validation** (cue.py), against the library index on the machine that validates — the composer's
laptop, or the Thor before every run:

* a `clip`, `song` or skin id must exist in the index (a problem if not);
* a clip must cover the time it is shown, from its `from`, or be marked `loop: true` (a problem);
* with music, a cut more than ~10 ms off a beat is a **warning** (it may be meant);
* a clip whose resolution is not the face panel's (1340×800, the tablet's 8.7-inch panel) is a
  warning;
* the export records each asset's **content hash**. The Thor re-validates every run against ITS
  library; a hash that differs from the export's is logged at preflight (the asset changed since
  the show was composed), and a missing asset follows rule 4.

Two face-engine rules the validator should know (tablet lane): a reserved robot state (DEGRADED,
E_STOP) always shows the default eyes whatever SKIN says; and the panel is 1340×800.

## The library index

Its SCHEMA is owned by the choreography lane, since the validator reads it; agreed with the tablet
lane. Its ENTRIES are library content, like the files they describe: never committed. The real
index — names, hashes, source and licence lines, which name third-party sources and show-specific
content — lives with the assets on the Thor and syncs alongside them. The repo holds only the
schema, the add-item tool (which writes to the library, never into the tree), the validator (which
reads the index from the library location, never from the repo), and test fixtures with made-up
entries. The example below is made up.

```yaml
# /var/lib/opendubs/library/index.yaml on the Thor (synced to composing laptops; the tablet syncs
# by hash — tablet plan 5.3 — and keeps its last good set while the Thor is down)
- id: example-song
  kind: song                    # song | clip | skin (later: overlay, theme, sound)
  file: songs/example_song.mp3
  sha256: 3f1c…
  duration_s: 150.0
  bpm: 120                      # optional, measured; the show's music block still governs
  source: "…"                   # where it came from — REQUIRED
  licence: "…"                  # REQUIRED
- id: example-clip
  kind: clip
  file: clips/example_clip.mp4
  sha256: 9a0e…
  duration_s: 12.0
  width: 1340
  height: 800
  fps: 60
  source: "…"
  licence: "…"
- id: example-eyes
  kind: skin
  file: skins/example-eyes/
  sha256: …
  inputs: [lookX, lookY, lid, emote, colour]   # which face-contract inputs it uses (face-engine.md §A)
  source: "…"
  licence: "…"
```

**An item's hash.** For a single file, the sha256 of the file. For a folder (a skin), the sha256 of
the sorted list of `relative/path<TAB>sha256-of-that-file` lines, one per file — one rule for any
multi-file item, so a changed or added file changes the item's hash.

The dashboard serves library files to the holding device by id (as it serves show audio today).
A small tool adds an item: it hashes the file, reads duration and size, and refuses an entry
without source and licence.

## Composer and twins

* The composer gets a SCREEN lane (shots as blocks, cuts snapping to beats) and a skin picker on
  the FACE lane.
* Its preview **embeds the web face engine itself** (`software/face-web/`, tablet PR #110), fed the
  same shot list and skins from the laptop's synced library. The web face engine IS the tablet's
  face, not a model of it, so the composer's preview and the robot run the same code — the
  strongest form of "one timeline document". (The Tab5's desktop twin stays the Tab5's twin.)
* The MuJoCo body twin is unchanged (it has no screen of its own); the face twin shows the skin
  once the face engine's desktop build supports it.

## Build order

1. Library index schema and the add-an-item tool (choreography).
2. Format and validator: SCREEN slot, `do: skin`, `music: song:`, the checks above, hashes in the
   export (choreography).
3. Hub roles and the extended `load` / `ready` (choreography: `show_audio.py`, `show_audio.js`).
4. The face page as a show-media client: claim both roles, preload, schedule from T0, fade and cut
   (tablet lane, face engine).
5. `SKIN` over LiveFace (choreography) against head protocol v2 (tablet lane).
6. Composer SCREEN lane and preview (choreography).
7. At the robot: a short music-video routine — cut timing against the beat, measured on the
   tablet the way S1/S3 measured audio.

## Open questions

* **Clip audio.** Proposed: clips are always silent and the show's music is the soundtrack. A clip
  that must carry its own sound would need mixing against the music — not in this version.
* **Overlay layer** (graphics over the face, not instead of it) — named in the protocol, built later.
* **Laptop library sync** — the tablet's is the tablet lane's (plan 5.3); how a composing laptop
  gets the library is open (a pull from the Thor by hash is the obvious shape).
* **Moving existing shows** from `music: file:` to `music: song:` — after the library exists; both
  forms stay valid until then.
