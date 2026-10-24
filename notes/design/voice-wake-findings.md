# Voice input at a distance: what the wake word taught us

**Status, 2026-10-24.** The wake word is off on every robot, and voice input in a crowd is parked
until a microphone array arrives. This note collects what was built, tried and measured from
2026-10-19 to 2026-10-22, so that whoever picks up the array work starts from the evidence, not
from scratch. The plan for the array is in [`../workplan-voice.md`](../workplan-voice.md), section
"Later: a microphone array on the Thor".

**Jargon.** *KWS*: keyword spotting, a small model that listens for one phrase. *dBFS*: decibels
below full scale, the loudest a digital signal can be (0 dBFS); speech close to a phone is
typically −20 to −10 dBFS. *EC / NS / AGC*: echo cancellation, noise suppression and automatic gain
control, the browser's "call mode" processing. *Pre-roll*: audio from just before the trigger, kept
so the first word is not lost.

## How it was built

A visitor says the phrase; the robot answers. Nothing about it needs the internet.

| stage | where | what | PRs |
|---|---|---|---|
| spot the phrase | the head's Galaxy tablet, in the face web page | sherpa-onnx KWS compiled to WebAssembly, the `gigaspeech-3.3M` English model (Apache-2.0); no audio leaves the tablet before it fires | [#373], [#379], [#400], [#413], [#432] |
| send the turn | tablet → Thor over `/ws/voice` | a `wake` message, 1.5 s of pre-roll, then live 16 kHz audio | [#372], [#373] |
| end the turn | the Thor's voice service | Silero VAD: up to 4 s for speech to start, ends after 0.8 s of silence | [#372] |
| check it | the Thor | Parakeet (speech-to-text) transcribes the turn; it goes on only if the phrase is near the start, matched by spelling distance; anything else ends in silence (`not_addressed`) | [#372], [#378], [#424] |
| health | the dashboard | the page's listening report, numbers only, at `/api/head/wake_hits`; an on-load self-test that feeds a known clip to the spotter | [#383], [#400], [#418] |

Talk, the hold-to-talk button on the face, uses the same capture and pipeline without the spotter,
and is the fallback throughout.

## What was tried, in order

1. **"hey dubs"** ([#372], [#373]): the spotter fired once in about 40 minutes on the robot, and the
   Thor's check then refused that one real hit, because the 1.5 s pre-roll held the tail of the
   previous attempt. Fixed in the check ([#378]): the phrase may start within the first six words.
2. **Spotter strictness**: the first lines (`:1.5 #0.20`, then `:2.0 #0.10`, [#379]) never fired on
   the robot. The keyword tokens were checked against the model's own `bpe.model` and were right,
   and the audio rate passed to the spotter was right. Tuned for recall to `:5.0 #0.01` ([#400]),
   since the Thor's transcript check makes a false hit cost only a few silent seconds. On the desk
   it then caught 3 of 3 isolated phrases and 0 of 4 near misses; the on-load self-test hit on the
   tablet.
3. **A gain stage in front of the spotter only** ([#413]): on the desk the spotter was
   level-sensitive (3/3 at −6 dBFS peak, 2/3 at −24, 0/3 at −30 and −36). The stage brings speech to
   about −6 dBFS, at most +24 dB, and leaves near-silence alone so hiss is not lifted. The audio sent
   to the Thor is not boosted: Parakeet normalises level itself, and boosted room noise would delay
   the end of a turn. Desk result: 24 of 24 across voices, levels and noise, against 9 of 24 without.
4. **The doubled microphone stream** ([#416]): every `/ws/voice` reconnect added another microphone
   stream to the page, interleaved with the first. The spotter, Talk and the uplink all got two
   streams at once, which also explained Talk turns that were misheard. Fixed in the page; the
   service now flags any turn whose audio arrives faster than real time (`uplink_x`, [#417]).
5. **Tablet stability** ([#404], [#408]): the face reloads itself when its pixels go black or the
   dashboard restarts, and a stuck spotter starts afresh instead of staying wedged.
6. **Call-mode processing A/B** ([#418]): a switch on the Thor to turn the tablet's EC, NS and AGC
   off, and band levels in the report. With all three off and +12 dB of gain, a live phrase at a
   normal distance still got **0 of 3**, at the same peak level. Processing is not the limiter, and
   the settings were restored. [#414], which turns noise suppression off for good, stays an open
   draft and is not needed.
7. **"Hey OpenDubs"** ([#424], [#432]), spoken "hey open dubs": "hey dubs" is about six phonemes, at
   the low end for keyword spotting, and published wake-word guidance (Picovoice's, for one) suggests
   three to four syllables with a "hey" lead. Tokens
   from the model's own tokenizer: `▁HE Y ▁O P EN ▁DU B S`. Page line `:4.0 #0.02`: 29 of 30 on the
   desk, 0 false hits on 28 near misses, including "open the door" and a bare "hey dubs".
8. **Retuning from the robot** ([#399]): a per-robot keyword line from `voice.env`, so the spotter
   could be tuned without a page deploy. Closed without merging once the wake word was parked.

## What was measured on the robot

- **Distance is the problem.** After [#416], the phrase was caught only very close to the face.
  At 1–2 m, speech peaks reached the spotter at about −22 dBFS.
- **Level is not the whole problem.** With "Hey OpenDubs" deployed, a live phrase at a normal
  distance got **0 of 3**, even with speech peaks around −11 dBFS, while the on-load self-test (a
  clean synthetic clip fed straight into the spotter) hit. The spotter works; what reaches it from a
  real voice at a distance is not something it recognises.
- **One capture, and what it held.** A single fixed-window capture of the spotter's exact input
  (2.54 s, one "hey dubs") was analysed as numbers and then deleted. Level was healthy (−7.4 dBFS
  peak, −30 dBFS RMS, no clipping), but the energy sat in **steady tones near 1.5 kHz and then
  0.84 kHz**, with almost nothing below 300 Hz. A separate speech recogniser on the desk found no
  speech in it at any playback speed. It was taken before the doubled-stream fix, so it may show
  that fault rather than the room.
- **A suspected tonal source at the head.** The neck's servos sit right behind the tablet, and the
  team's working assumption was that their noise hurt recognition. The neck now holds still while
  someone speaks and moves slowly otherwise ([#375], [#382]). Whether a steady tone near the head
  remains after [#416] was never measured.

## The tracker-starvation incident, and turning wake off

On 2026-10-22 the face's person tracker stalled at "loading" while the spotter's worker kept
failing to load beside it and reloading every minute. The two heavy WebAssembly workers on the
tablet appeared to starve each other. Wake was switched off on the robot at runtime, then off by
default: the voice service offers wake only when a robot sets `OPENDUBS_VOICE_WAKE=1` ([#516]),
and with wake off the face loads no spotter and stops one that is running ([#518]).

## What is still unknown

- Whether the tones in the capture were the doubled stream or something real at the head.
- How much of the loss at a distance is the tablet's single microphone, its placement on a moving
  head, and the room, against the small spotter model itself. Each change above moved one of these
  without isolating the others.
- How any of it behaves in a crowd or with show music, which is where the robot will be used.

## Why a microphone array is the next step

The limits found are acoustic and physical: one microphone, on a moving head, behind call-mode
processing, at arm's length from servos, with a 3.3M-parameter spotter in a browser. A USB array
on the robot's body, with its own beamforming and noise suppression and the spotter running on the
Thor, changes all of those at once, and it also helps Talk, which has the same microphone today.
The plan is in [`../workplan-voice.md`](../workplan-voice.md), "Later: a microphone array on the
Thor" ([#540]): where it mounts, how capture reaches the voice service, its settings, and a retest
at distance judged by ear. Other options stay open: a presenter's lapel microphone, a physical
push-to-talk button on the robot, an operator "listen" control.

## Links

- Workplan: [`../workplan-voice.md`](../workplan-voice.md), sections "The wake phrase, 'Hey
  OpenDubs' — PARKED" and "Later: a microphone array on the Thor".
- Research behind the voice design: [`voice-llm-research.md`](voice-llm-research.md).
- Code: `software/voice/wakeword.py` (the Thor's check), `software/voice/vad.py` (end of turn),
  `software/face-web/engine/voice.js` and `engine/kws_worker.js` (the page's spotter).

[#372]: https://github.com/UW-Allen-School-Robotics/opendubs/pull/372
[#373]: https://github.com/UW-Allen-School-Robotics/opendubs/pull/373
[#375]: https://github.com/UW-Allen-School-Robotics/opendubs/pull/375
[#378]: https://github.com/UW-Allen-School-Robotics/opendubs/pull/378
[#379]: https://github.com/UW-Allen-School-Robotics/opendubs/pull/379
[#382]: https://github.com/UW-Allen-School-Robotics/opendubs/pull/382
[#383]: https://github.com/UW-Allen-School-Robotics/opendubs/pull/383
[#399]: https://github.com/UW-Allen-School-Robotics/opendubs/pull/399
[#400]: https://github.com/UW-Allen-School-Robotics/opendubs/pull/400
[#404]: https://github.com/UW-Allen-School-Robotics/opendubs/pull/404
[#408]: https://github.com/UW-Allen-School-Robotics/opendubs/pull/408
[#413]: https://github.com/UW-Allen-School-Robotics/opendubs/pull/413
[#414]: https://github.com/UW-Allen-School-Robotics/opendubs/pull/414
[#416]: https://github.com/UW-Allen-School-Robotics/opendubs/pull/416
[#417]: https://github.com/UW-Allen-School-Robotics/opendubs/pull/417
[#418]: https://github.com/UW-Allen-School-Robotics/opendubs/pull/418
[#424]: https://github.com/UW-Allen-School-Robotics/opendubs/pull/424
[#432]: https://github.com/UW-Allen-School-Robotics/opendubs/pull/432
[#516]: https://github.com/UW-Allen-School-Robotics/opendubs/pull/516
[#518]: https://github.com/UW-Allen-School-Robotics/opendubs/pull/518
[#540]: https://github.com/UW-Allen-School-Robotics/opendubs/pull/540
