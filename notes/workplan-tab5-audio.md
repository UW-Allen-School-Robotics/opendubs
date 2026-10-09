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
| **Echo** | **Half-duplex.** Capture is muted while the speaker is active | Costs **voice** barge-in: the head is deaf while speaking, so it cannot hear you talk over it. **Button barge-in was recovered** by streaming audio down in chunks — a press cuts a reply off within ~100 ms (see Phase B). Buys the whole AEC problem going away — no codec registers written, no software canceller on the P4. **VAD therefore never runs during playback**, which is what Phase A step 2 was waiting on. Built as ONE explicit gate so a canceller can replace it later without touching a caller |
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

## The vendor's audio task, and a fix that did NOT work

**The finding is real and worth keeping; the fix was a guess and is reverted.**

The vendor creates its playback task at **priority 5, UNPINNED**, the first time anything plays a
sound, and never deletes it:

```c
xTaskCreate(_audio_play_task, "audio", 4096, nullptr, 5, nullptr);
```

It then loops `vTaskDelay(10 ms)` for the rest of the boot even when idle. `percept` runs at
priority 2 **pinned to CPU1** with its stack in PSRAM, specifically so it cannot preempt the
animation. So one earcon permanently gives the head an unpinned priority-5 task waking 100 times a
second that outranks face detection on either core.

**Observed 2026-10-08:** detection-driven head turning became "damped — it takes a fair amount of
insistent movement", and **a restart restored it** with the same image and audio still enabled,
which is the signature of something acquired at runtime.

**What was tried and FAILED:** pinning that task to CPU1 at priority 2 (PR #83). The damping came
back. Reverted, because a speculative vendor patch is worse than none.

Two things to carry forward rather than rediscover:

- **Pinning it to CPU1 may have been exactly wrong.** `percept` is on CPU1. At priority 5
  *unpinned* the task could at least land on CPU0, which has slack; pinning it put the contention
  onto the one core percept needs. If this is attacked again, the shapes worth trying are **ending
  the task when idle** or **pinning it to CPU0**.
- **The cause is still not proven.** Audio was switched OFF on the robot instead, which removes the
  mechanism entirely — `cuePlay()` returns on its first line, `audioPlay()` is never called, and
  the task is never created. That is pragmatic and leaves a real loose end: **if tracking is still
  poor with audio off and no cue played since boot, audio was never the cause.**

**A lesson, already in CLAUDE.md and broken anyway:** an image and a setting changed in the same
afternoon, so the flash took the blame for an hour. Change one variable at a time.

## The first thing to check when the robot is silent

**Audio is OFF by default and it is a per-robot NVS setting, not a property of the image.**
`ui_settings.cpp` has `g_audio = false` — deliberate, because the Mode tone was rejected as too
loud and shipping it on would re-make that call for the operator — and `cuePlay()`'s *first*
statement is `if (!kCuesEnabled || !settingsAudioEnabled()) return;`.

So a head that has never had Audio switched on in SETTINGS accepts every `CUE` and plays nothing.
This cost the choreography session an investigation on 2026-10-08: earcons sent from the Thor were
acked, nothing was heard, and the suspicion fell on the shipping image. `CUE` now answers
`OK cue <name> (AUDIO OFF -- enable it in SETTINGS)` so the next person is told rather than left to
infer it.

Enabling it needs no reflash: the toggle calls `cuesBegin()`, which is where the earcon PCM is
rendered — skipped while audio is off because it costs ~306 kB of internal DRAM.

**A show that is silent on one robot can be loud on the next.** Same image, different NVS.

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

**Status 2026-10-07. PHASE A IS CODE-COMPLETE — and not one line of it has met a microphone.**
All four steps build for the device and for the twin. `./build.sh voice` is thirteen cases with synthetic stimulus and
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

**Nothing here has met a microphone.** Seventeen host cases against synthetic stimulus and an image
that links — no sample off a real ES7210, no head that has turned. **The bench pass is now the
critical path, and three unknowns need it:**

1. **The noise floor with the drive motors, the lift and the ESCs running.** `VOICE STATUS` reports
   it. Until then every threshold here is arithmetic, not a measurement.
2. **Does the head glance the RIGHT WAY.** One constant, `kRightIsPositive`.
3. **Does `Heard` land inside a second**, and does the ~90 ms of self-deafening from the blip
   itself bite in practice.

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
4. **Glance toward a voice.** **DONE in code, unproven on hardware.** `voice/glance.cpp`, and it
   takes **no lease**: it enters through the same per-frame `pantiltTick(... haveTarget, nx, ny,
   conf)` door vision uses, so touch and RC outrank it automatically and **pantilt.cpp is
   unchanged**. It steers only when **vision has nothing** — if the robot can see you it is already
   looking at you, and a glance earns its keep when the speaker is outside the camera's view.
   - Bearing is the **onset** one, not the peak: the acknowledgement must land inside a second,
     and the design note licenses it — "a glance is allowed to be approximate and then end".
   - **CENTRE moves nothing**; the earcon is the whole acknowledgement.
   - A re-glance needs **new information**: same bearing settles 2.5 s, a changed bearing reacts in
     600 ms. The first cut had one 600 ms gap and the duty-cycle test measured the head steering
     **60%** of the time at a static speaker — a tic, and a tracker by accident. Now 27%.
   - The **sign convention is unverified**: one named constant, `kRightIsPositive`. If the head
     glances the wrong way on the robot, flip that and do not chase the arithmetic.
   This is load-bearing: `pantilt.cpp` arbitrates touch > RC > tracker, and a continuous voice
   tracker would be a fourth lease-holder in a hierarchy whose ownership fight cost a whole session
   to fix. A glance is one time-boxed move that claims nothing. Left/right/centre is exactly enough.
5. **1.5, the CPU probe:** capture -> encode -> play locally and find what the Tab5 end costs.
   **Gate:** a local round trip that cannot hold under ~150 ms means the architecture needs
   revisiting.

## Phase B — transport (the old 1B)

**Status 2026-10-07. Clip-up AND audio-down are built, both tested against a fake Thor.**
`./build.sh loopback` runs the head's real framing against `tools/fake_thor.py` over a real
socket — no board, no robot.

- `voice/resample.cpp` — 48 kHz 4-channel → **16 kHz mono**, streaming, with an anti-alias FIR.
- `voice/clip.cpp` — the utterance buffer. **Covers the whole press**, not from the onset: starting
  at `Heard` loses ~96 ms to the onset debounce, and that is the leading plosive that tells "pack"
  from "back". 12 s cap, 384 kB from `bigAlloc()` in **PSRAM** — a `std::vector` would have tried
  internal DRAM and failed.
- `voice/wire.cpp` — the framing. Text header line, binary payload, carrier-agnostic.
- `voice_net.cpp` — the carrier. Blocks, so it runs on the capture task only.
- `VOICE THOR <host> <port>` sets the address at runtime; **not persisted** (per-robot values do
  not belong in this repo).

### Decided

- **Audio up: RAW PCM, not Opus.** The plan's "256 kbit/s vs 24 kbit/s" is a *rate* comparison and
  we have no stream. Bounded clips mean a 3 s utterance is ~96 kB sent once when someone stops
  speaking — nothing on this LAN, and a burst between silences rather than a competitor to video.
  Opus would cost a P4 dependency, flash, and encode latency inside step A5's ~150 ms budget.
- **Carrier: TCP, one connection per utterance.** `udp/4242` was ruled out, and UDP would also mean
  fragmenting 96 kB, numbering chunks and inventing retransmission — a dropped chunk does not
  degrade an utterance, it *corrupts* it. Bounded clips make the exchange request/response.
- **Echo: half-duplex** (see the decisions table).

### The anti-alias filter is the part that fails invisibly

48 kHz → 16 kHz is exactly 3:1, so the decimation is trivial — which is the trap. Dropping two of
every three samples with no low-pass **folds everything above 8 kHz into the band**: a 12 kHz ESC
whine lands at 4 kHz, on top of the vowels. The audio sounds fine and transcribes badly, which is
the worst failure available because nothing about it looks broken. The FIR's stopband is *measured*
by the test at the frequencies that fold into speech: **36 dB down at 12 kHz**.

### Audio down — BUILT, streamed

`voice/upsample.cpp` + `voice/stream.cpp` + the playback loop in `voice_net.cpp`. The reply's body
is a sequence of length-prefixed chunks terminated by a zero length, so the Thor can start speaking
before it has finished synthesising — which is the whole point.

**The blocker recorded here earlier was not real.** This plan said `hal.h` has `audioPlay()` and no
stop, so a reply could not be cut short. True of the header — but `hal_audio.cpp` shows `audioPlay`
has *two* paths, and only the **async** one copies into a task and refuses while busy. The
**synchronous** path is a bare blocking `i2s_write` with no mutex and no busy check. So a reply can
be fed chunk by chunk, and **stopping is simply not feeding the next chunk.**

So the ceiling moved from "cannot be interrupted until it finishes a sentence" to **interrupted
within one chunk, ~100 ms**. Be precise about what that recovers: half-duplex still means the head
is deaf while speaking and **cannot hear someone talk over it**. What is back is *button* barge-in —
a press cuts the answer off — which with push-to-talk is the natural gesture anyway.

**Two hazards in that same source, neither visible from the header:**

1. **The speaker sink is hardwired to 48 kHz stereo** — every path calls
   `i2s_reconfig_clk_fn(48000, 16, I2S_SLOT_MODE_STEREO)` first. 16 kHz mono handed over does not
   fail, it *plays*: three times too slowly, an octave and a half too low. `wireReplyFormatOk()`
   refuses a non-contract reply rather than playing it wrong.
2. **The sync path takes no mutex**, and earcons use the async path. Two writers into one I2S
   device produce garbage. Hence `cuesSuspend()` for the length of a spoken reply.

Interpolation reuses `resample.cpp`'s taps as a **polyphase** filter — for interpolation by 3 the
anti-imaging filter *is* the anti-alias filter — so each output costs 5 MACs, not 15.

### Still open on audio down

- **A fixed-length (`bytes=N`) reply body is parsed but not spoken.** Deliberate: a Thor that knows
  the total length up front has finished synthesising before saying anything, which is what
  streaming exists to avoid. Logged rather than silently dropped.
- **Chunk size is 100 ms, which is both the playback and the interruption granularity.** Shorter
  interrupts faster and risks the speaker running dry between chunks. Only the bench can judge it.

### `tools/fake_thor.py` is the Thor-side specification

Written to the contract rather than to the head's code, and strict on purpose — a mock written
alongside its client agrees with it by construction and proves nothing. Verified to reject: a
48 kHz payload, a 4-channel payload, `bytes != samples*2`, a short read, base64'd audio, an
all-silent payload, a missing key, and a `NOSPEECH` carrying audio. When the real Thor-side work
starts, this is what it has to satisfy.

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
3. **Does the mic become a perception source for the FACE as well as the head?** Answered 2026-10-07:
   worth looking at. It would also give F1 in the Tab5 plan a second real event to react to.
4. **What happens when understanding is unavailable** — the Thor down, the network gone. The face
   must not invite speech it cannot route. Degrading honestly is a design requirement, not an error
   path.
