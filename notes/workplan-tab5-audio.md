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

## Decided 2026-10-07

Three choices the plan flagged as "settle early". All three are now settled, and each one makes
Phase A **cheaper**, so they are recorded here rather than left in a commit message.

| Decision | Choice | What it costs, and what it buys |
|---|---|---|
| **Echo** | **Half-duplex.** Capture is muted while the speaker is active | Costs **barge-in**: you cannot interrupt the robot mid-sentence. Buys the whole AEC problem going away — no codec registers written, no software canceller on the P4. **VAD therefore never runs during playback**, which is what Phase A step 2 was waiting on. Built as ONE explicit gate so a canceller can replace it later without touching a caller |
| **Utterance end** | **The Tab5 decides, on VAD. Bounded clips, not a continuous stream** | Costs a hard cutoff that semantics cannot revise, so a thoughtful mid-sentence pause can truncate — tunable via the hangover, not fixable. Buys much less bandwidth on a link already carrying video, no continuous carrier to design in Phase B, and a head that stays useful with the Thor down |
| **Listening policy** | **Push-to-talk.** Capture only while a press is held | Costs the hands-free feel. Buys the privacy question answered by construction: in a shared university building nobody can be recorded without a visible, deliberate act, and there is no wake-word model to fit |

**Push-to-talk reshapes Phase A, and in our favour.** VAD stops being the gate for *whether to
listen* — the button is — and becomes only the thing that finds the **end** of an utterance inside a
held press. That is a far smaller claim on a much smaller threshold problem: a false positive is no
longer "the robot started listening to the room", it is "the clip ended early".

It does **not** excuse skipping the noise-floor measurement. The end-of-speech threshold is still
measured against the drive motors, the lift and the ESCs a metre away, or it is fiction.

Push-to-talk and half-duplex compose: a press means **stop talking and listen**, so the press both
opens capture and cancels playback, and the two gates never contend.

## Already settled — do not re-investigate

| Fact | Where |
|---|---|
| **Direction of a voice resolves to left / right / centre — three values, at default gain.** Not an angle. Sufficient for a glance, insufficient for tracking | `firmware/tab5_face/bench_mic/`, interactive, 2026-10-04 |
| **Listening while speaking WORKS** — speaker and mic hold I2S together. So the robot can be interrupted; barge-in is viable | bench, 2026-10-04 |
| **Do not build a head that continuously tracks a voice. Build one that GLANCES toward it** | `design/hri-interaction-design.md` |
| Hardware: ES8388 codec, **ES7210 AEC front-end**, dual mics, 1 W 8 Ohm amp, 3.5 mm jack escape hatch | `design/tab5-platform.md` |
| **HARDWARE AEC IS NOT AVAILABLE AS CONFIGURED.** The ES7210 has two of four channels enabled (`MIC12_PDN=0x00`, `MIC34_PDN=0xFF`); **channels 3/4 are the AEC reference inputs and are OFF**, so the robot cannot cancel its own output in hardware | `design/tab5-platform.md`, 2026-10-07 |
| **The Tab5 plays sound files natively** — the vendor stack plays PCM and sound files out of the box. Use the vendor HAL and its example code; **do not probe the speaker** | `design/tab5-platform.md` |
| **One player, and it refuses while busy** — `audioPlay(pcm, async)` neither queues nor mixes, so a second sound is DROPPED. `cues.cpp` enforces priority in software for that reason. The Arduino image's eight virtual channels are not the IDF face | `design/tab5-platform.md`, workplan-tab5-idf 1.3 |
| **3.5 MB of SPIFFS is partitioned and unused** (`default_16MB.csv`) — room for sound assets without touching the partition table | `design/tab5-platform.md` |
| The HAL already has `audioRecord(data, durationMs, gain)`, `audioPlay(pcm, async)`, `startDualMicRecordTest()`, `setSpeakerVolume()` — **do not hand-roll these or poke ES7210 gain registers**, which an earlier session did before finding them | `design/tab5-platform.md` |
| Audio enable + volume are resident on the Tab5 and persisted in NVS, reachable from SETTINGS | `app_face/ui_settings.cpp` |
| `Heard` and `Attend` earcons exist. **`Attend` now fires when a person is SEEN**; `Heard` is still unwired and is this plan's | `app_face/cues.cpp` |

| **The IDF capture buffer is FOUR channels at 48 kHz** — `[MIC-L, AEC, MIC-R, MIC-HP]`, mics at stride-4 offsets **0 and 2**. NOT the Arduino bench's 16 kHz stereo at stride 2 | vendor `hal.h` + `hal_audio.cpp`, 2026-10-07 |
| **`audioRecord()` BLOCKS for `durationMs` and resizes its vector.** So it needs a task of its own — not `cmdTask`, which is the 20 Hz lift publisher — and the vector must be reused, not resized per block (a 32 ms block is 12 kB, and under `CONFIG_SPIRAM_MALLOC_ALWAYSINTERNAL=16384` that lands in INTERNAL DRAM) | `hal_audio.cpp`, 2026-10-07 |
| **Adding a FreeRTOS task is NOT free on this image** — a TCB must sit in internal DRAM and one more has already asserted in `xPortCheckValidTCBMem` with esp-dl linked. `percept` works around it with `xTaskCreatePinnedToCoreWithCaps` and a PSRAM stack | `app_face/percept/percept.h`, `idf_component.yml` |

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

**Status 2026-10-07.** Steps 1, 2 and 3 are **built and compiling for the device**; step 4 has its
arithmetic but not its entry point. `./build.sh voice` is thirteen cases with synthetic stimulus and
its own verdict.

- `app_face/voice/` — HAL-free pure compute, like `percept/`: levels, the noise floor, direction,
  and the push-to-talk/VAD state machine. Host-tested.
- `app_face/mic.cpp` — the shim that owns `audioRecord()`. Behind **`OPENDUBS_VOICE`, default OFF**,
  because it adds a FreeRTOS task and a TCB must live in internal DRAM. Off costs 368 bytes, on
  costs 4,096; 51% of the app partition is free either way.
- **`Heard` is wired.** `VoiceEvent::Heard` → `cuePlay(Cue::Heard)`.
- Trigger: **`VOICE PRESS|RELEASE|STATUS`** over UDP, so the floor can be measured from wherever the
  person running the motors is standing. A touch control is the honest long-term trigger and is not
  built.

**Nothing here has met a microphone.** It compiles, it is tested against synthetic stimulus, and the
next step is a bench pass: flash with `OPENDUBS_VOICE=1`, `VOICE STATUS` to read the floor with the
drive motors running, and `VOICE PRESS` to hear whether `Heard` lands inside a second.

**Two bugs that making it compile found**, both now tested:
- **A dead microphone read as continuous speech.** Zero samples → floor 0 → threshold 0 → every
  block above it. Fixed with an absolute floor under the floor (40 RMS).
- **There is no way to stop the speaker.** `hal.h` has `audioPlay()` and no counterpart. A press
  waits the sound out, which is fine for a ≤500 ms cue and **not** fine for streamed TTS —
  Phase B needs a real stop from the codec layer.


1. **Port the mic.** `audioRecord` into the IDF app, with the SETTINGS gain/enable already there.
   Self-triggering, printing its own RMS verdict — no human at the bench.
   *The level arithmetic is done and tested (`voice/level.cpp`), in the real 4-channel 48 kHz
   layout. Outstanding: the task that calls `audioRecord` and feeds it.*
2. **Voice activity detection.** Energy-plus-threshold first, measured against the robot's own noise
   floor: the drive motors, the lift and the ESCs are all a metre away. **Measure the noise floor
   with the robot RUNNING before choosing a VAD, or the threshold is fiction.**
   Scope settled above: VAD runs **only inside a held press** (push-to-talk) and **never during
   playback** (half-duplex), so it answers one question — has this utterance ended — and not
   "is someone talking to the robot".
3. **Wire `Heard`.** **DONE in code, unproven on hardware.** `VoiceEvent::Heard` →
   `cuePlay(Cue::Heard)`, fired once per utterance; 864 ms on a cold-boot press and ~96 ms
   thereafter, measured in the host test. Note the wrinkle half-duplex forces: `Heard` is itself a
   cue, so acknowledging someone deafens the robot for the ~90 ms the blip lasts. It sits in the gap
   after onset, which is the least bad place, but it is real and it is the price of answering with a
   sound while having no AEC. "I noticed you, inside a second, whether
   or not I can act" — the cheapest honest thing the robot can do and it needs no transport at all.
4. **Glance toward a voice.** *Direction is ported and tested, taken from the clip's LOUDEST
   block rather than its last — the tail of a sentence is its quietest part and its channel ratio
   the least trustworthy thing in the clip. Outstanding: the time-boxed glance entry point on
   `pantilt`, which today exposes only the per-frame `pantiltTick(... haveTarget, nx, ny, conf)`
   state and so has no way to accept a one-shot event.*
   Port the direction bench, then feed it in as an **EVENT, not an owner.**
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
- **Echo — SETTLED 2026-10-07: half-duplex.** "Listening while speaking works" means the mic and
  speaker can run together; it never meant the robot could hear past itself, and with the ES7210's
  AEC reference channels (3/4) disabled there is no hardware cancellation to lean on. Of the three
  routes — enable the reference channels, cancel in software on the P4, or sidestep it — we sidestep
  it: **capture is muted while the speaker is active.** The price is barge-in, paid knowingly.
  Implemented as a single explicit gate, so swapping in a canceller later changes that gate and
  nothing else.
- **Carrier:** the existing StamPLC protocol is line-based UDP and was never built to carry a
  payload. Audio and video want their own path. The head already runs an HTTP-pull OTA and mDNS, so
  there is precedent for a second port rather than overloading udp/4242.

## Phase C — the interface to understanding, NOT the understanding

**Defined 2026-10-07, and then stopped.** What follows is the contract and nothing else: no ASR,
no model, no TTS, no hosting. Those are a separate plan, and absorbing them here is the specific
way this one becomes unbounded.

The contract is small because the decisions made it small. Push-to-talk means the Thor is never
asked "is someone talking"; Tab5-side VAD means it is never asked "has this ended". It is asked one
question, about one complete clip, and it answers.

### What A+B hand the Thor

One message per utterance, because the utterance is bounded before it leaves the head.

| Field | Value | Why it is here |
|---|---|---|
| `audio` | 16-bit PCM mono, **16 kHz**, downsampled on the head from the 48 kHz capture | 16 kHz is what ASR wants and is a third of the bytes. The head already has to touch every sample to compute RMS, so the decimation is close to free |
| `dir` | `left` \| `right` \| `centre` | Three values, from the clip's loudest block. For a GLANCE, never for tracking |
| `began`, `ended` | head milliseconds | So the Thor can tell a 400 ms clip from a 6 s one without decoding, and so a late reply can be dropped rather than answered |
| `seq` | monotonic per boot | Makes a reply matchable to its clip and a duplicate droppable |
| `floor`, `peak` | the measured noise floor and the clip's peak, both RMS | **The head's own confidence, handed over rather than hidden.** A clip that barely cleared a loud floor is a different thing from a clear one, and the Thor should be allowed to decline it rather than hallucinate words out of ESC noise |

`NO_SPEECH` is its own message, carrying `floor` and no audio. **A failure to HEAR must not reach
the Thor looking like a failure to UNDERSTAND** — it is the difference between "say that again" and
"I don't know", and the face owes the person the right one.

### What comes back

| Field | Value |
|---|---|
| `seq` | the clip being answered |
| `say` | audio to play, streamed — see Phase B. Empty means answer without speaking |
| `face` | an optional emote/state request, taking the existing `EMOTE`/`STATE` vocabulary rather than inventing one |
| `status` | `ok` \| `unintelligible` \| `declined` \| `busy` |

`status` is separate from an empty `say` on purpose. "I heard you and have nothing to say" and "I
could not make that out" are different to a person and must be different on the face.

### The thing this contract is really protecting

Open question 4, and it is a design requirement rather than an error path: **the face must not
invite speech it cannot route.** With push-to-talk that is tractable, because the invitation is a
visible control — so the control is disabled, visibly, whenever the Thor is unreachable, and the
robot never takes an utterance it has nowhere to send. `Heard` is the one thing that still works
with no transport at all, which is what makes Phase A step 3 worth doing before any of this.

### Deliberately NOT specified here

The transport encoding (Phase B: raw versus Opus), the carrier and port, and every word of
ASR/LLM/TTS selection and hosting. The contract above is stable under all of those choices, which
is the test of whether it was drawn in the right place.

## Open questions

1. ~~**Wake word, or push-to-talk, or always-listening?**~~ **ANSWERED 2026-10-07: push-to-talk.**
   See the decisions table. The privacy question is answered by construction rather than by policy —
   capture requires a visible, deliberate press — which is the version of this that does not need
   re-litigating every time someone new walks into the bay.
2. **Whose voice is the robot's** — the Tab5's 1 W speaker, or the tablet's? `notes/punch-list.md`
   already flags the overlap and it is still undecided.
3. **Does the mic become a perception source for the FACE as well as the head?** Emma, 2026-10-07:
   worth looking at. It would also give F1 in the Tab5 plan a second real event to react to.
4. **What happens when understanding is unavailable** — the Thor down, the network gone. The face
   must not invite speech it cannot route. Degrading honestly is a design requirement, not an error
   path.
