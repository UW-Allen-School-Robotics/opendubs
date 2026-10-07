# Work plan — two-way voice: the robot listens and answers

**Purpose: make the robot a thing you can talk to.** Not a face that invites speech it cannot
answer — `design/hri-interaction-design.md` is blunt that a face expressive enough to invite speech,
on a robot that cannot converse, *spends* its credibility — but an actual conversational loop:
someone speaks, the robot notices, glances at them, and answers out loud.

Opened 2026-10-07, when the Tab5 ESP-IDF port closed. **Background, not scope:**
[`workplan-tab5-idf.md`](workplan-tab5-idf.md) — that plan built the face, the head and the panel
this one speaks through, and absorbed items **1.5** (voice as a probe) and **1B** (Tab5 <-> Thor
communication architecture) which now live here. Interaction design, already written and NOT to be
re-derived: [`design/hri-interaction-design.md`](design/hri-interaction-design.md).

## Standing rules

- **Read what is already measured before measuring it again.** The settled facts below cost bench
  time to establish.
- **The Tab5 is one END of this pipeline, not the pipeline.** Keep the layer boundaries below
  explicit; the commonest way a plan like this becomes unbounded is by quietly absorbing the
  Thor-side model work.
- A test that needs a human at the bench is not a test — firmware produces its own stimulus and its
  own verdict. (CLAUDE.md, and it has been earned twice.)
- **Change one variable at a time.** The ToF closeout is the recent example of what that buys.

## Already settled — do not re-investigate

| Fact | Where |
|---|---|
| **Direction of a voice resolves to left / right / centre — three values, at default gain.** Not an angle. Sufficient for a glance, insufficient for tracking | `firmware/tab5_face/bench_mic/`, interactive, 2026-10-04 |
| **Listening while speaking WORKS** — speaker and mic hold I2S together. So the robot can be interrupted; barge-in is viable | bench, 2026-10-04 |
| **Do not build a head that continuously tracks a voice. Build one that GLANCES toward it** | `design/hri-interaction-design.md` |
| Hardware: ES8388 codec, **ES7210 AEC front-end**, dual mics, 1 W 8 Ohm amp, 3.5 mm jack escape hatch | `design/tab5-platform.md` |
| The HAL already has `audioRecord(data, durationMs, gain)`, `audioPlay(pcm, async)`, `startDualMicRecordTest()`, `setSpeakerVolume()` — **do not hand-roll these or poke ES7210 gain registers**, which an earlier session did before finding them | `design/tab5-platform.md` |
| Audio enable + volume are resident on the Tab5 and persisted in NVS, reachable from SETTINGS | `app_face/ui_settings.cpp` |
| `Heard` and `Attend` earcons exist. **`Attend` now fires when a person is SEEN**; `Heard` is still unwired and is this plan's | `app_face/cues.cpp` |

**`bench_mic/` is in the Arduino tree (`firmware/tab5_face/`), not the IDF one.** Nothing in
`app_face` touches the mic. So the direction work is a PORT, not a wire-up.

## The three layers, and why naming them matters

Only the first two are Tab5 work. Conflating them is how this becomes one unbounded plan.

| Layer | Where it runs | In scope here |
|---|---|---|
| **A — Local audio** capture, playback, voice activity detection, wake, direction | Tab5 (ESP32-P4) | **YES.** Mostly proven at bench; needs porting to the IDF app |
| **B — Transport** Tab5 <-> Thor: audio up, audio down, bandwidth shared with video | Tab5 + Thor | **YES.** This is the old 1B |
| **C — Understanding** ASR, the LLM, TTS | Thor (AGX) | **INTERFACE ONLY.** Define what A+B must hand it and what comes back. The model work is a separate plan and must not be planned from here |

## Phase A — the Tab5 end

1. **Port the mic.** `audioRecord` into the IDF app, with the SETTINGS gain/enable already there.
   Self-triggering, printing its own RMS verdict — no human at the bench.
2. **Voice activity detection.** Energy-plus-threshold first, measured against the robot's own noise
   floor: the drive motors, the lift and the ESCs are all a metre away. **Measure the noise floor
   with the robot RUNNING before choosing a VAD, or the threshold is fiction.**
3. **Wire `Heard`.** The earcon exists; VAD is its trigger. "I noticed you, inside a second, whether
   or not I can act" — the cheapest honest thing the robot can do and it needs no transport at all.
4. **Glance toward a voice.** Port the direction bench, then feed it in as an **EVENT, not an owner.**
   This is load-bearing: `pantilt.cpp` arbitrates touch > RC > tracker, and a continuous voice
   tracker would be a fourth lease-holder in a hierarchy whose ownership fight cost a whole session
   to fix. A glance is one time-boxed move that claims nothing. Left/right/centre is exactly enough.
5. **1.5, the CPU probe:** capture -> encode -> play locally and find what the Tab5 end costs.
   **Gate:** a local round trip that cannot hold under ~150 ms means the architecture needs
   revisiting.

## Phase B — transport (the old 1B)

- **Audio up:** 16 kHz mono PCM is 256 kbit/s raw; Opus at 24 kbit/s is ~10x better and the P4 can
  encode it. Decide: raw for simplicity, or compressed for headroom shared with video.
- **Audio down:** TTS back to the speaker, **streamed, not file-at-a-time**, or the robot pauses
  mid-sentence.
- **Echo:** the ES7210 AEC front-end is on this board for a reason, and "listening while speaking
  works" means the hard case is reachable rather than already solved.
- **Carrier:** the existing StamPLC protocol is line-based UDP and was never built to carry a
  payload. Audio and video want their own path. The head already runs an HTTP-pull OTA and mDNS, so
  there is precedent for a second port rather than overloading udp/4242.

## Phase C — the interface to understanding, NOT the understanding

Define only the contract: what A+B hand the Thor (format, rate, framing, who decides an utterance
has ended) and what comes back (text? audio? both? a behaviour request?). **Then stop.** ASR/LLM/TTS
selection and hosting is its own plan.

One thing to settle early because it constrains A: **who decides an utterance has ended** — the
Tab5 on VAD, or the Thor on semantics. That choice sets whether the Tab5 streams continuously or
sends bounded clips, and it is cheap to decide now and expensive to change later.

## Open questions

1. **Wake word, or push-to-talk, or always-listening?** Bears on privacy in a shared lab as much as
   on CPU. A robot that always listens in a university building is a conversation to have with
   people, not only a design choice.
2. **Whose voice is the robot's** — the Tab5's 1 W speaker, or the tablet's? `notes/punch-list.md`
   already flags the overlap and it is still undecided.
3. **Does the mic become a perception source for the FACE as well as the head?** Emma, 2026-10-07:
   worth looking at. It would also give F1 in the Tab5 plan a second real event to react to.
4. **What happens when understanding is unavailable** — the Thor down, the network gone. The face
   must not invite speech it cannot route. Degrading honestly is a design requirement, not an error
   path.
