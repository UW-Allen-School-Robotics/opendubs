# Work plan — voice: push-to-talk conversation with the robot, from a desk demo to hardening

**DRAFT, 2026-10-13, written by the Voice lane; the open decisions were taken on 2026-10-14
(below). Not yet agreed as a whole.** Once it is, it becomes the plan of record; finished items
are then struck through with a date, as in [`workplan-tablet.md`](workplan-tablet.md). Only the Voice lane edits this file (CLAUDE.md
"Lanes", rule 3); findings from other lanes come as messages.

**Background.** The research behind every choice here is
[`design/voice-llm-research.md`](design/voice-llm-research.md) (PR #217, cited below as "the note",
with its section numbers). The Tab5 head's voice plan, [`workplan-tab5-audio.md`](workplan-tab5-audio.md),
is paused (tablet plan DT-20); its interaction decisions carry over: push-to-talk, half-duplex,
bounded utterances, raw PCM, and `NO_SPEECH` never reaching a model. The hard line on authority is
[`design/face-design.md`](design/face-design.md): no language-model authority over motion. The
tablet's microphone already works in the kiosk (TB-1, [`design/tablet-device.md`](design/tablet-device.md)).

**Jargon.** STT: speech-to-text. TTS: text-to-speech. VAD: voice activity detection. AEC: acoustic
echo cancellation (removing the robot's own voice from what its microphone hears). TTFS: time to
first sentence, the moment TTS has something to say. Barge-in: interrupting the robot while it
talks. Lease: a claim that expires unless renewed, as in the head protocol.

**Where each step runs**, marked on every row:

| mark | where | rule |
|---|---|---|
| **desk** | a laptop, no robot hardware | fake audio and fake backends; only processes and ports this session started |
| **bench Thor** | the non-robot Thor | the test bed for models, containers and GPU contention. Never opendubs2 |
| **robot** | opendubs2 and its tablet | only proven, pinned artifacts arrive here, through `deploy-all.sh`. Needs a person present for anything audible or visible |

---

## Decisions of record (the project team)

| # | Decision |
|---|---|
| V-D1 | **The robot's Galaxy tablet is the face and the audio device**, in and out. |
| V-D2 | **The mouth is enableable and off by default** (PR #222: `face.setMouth(bool)`, `face.setMouthLevel(0..1)`). |
| V-D3 | **Motion is never commanded by a language model. Routines by voice stay off.** The voice service has no motion tool to enable; adding one would need a new team decision and a change to this plan. |
| V-D4 | **Voice is off during shows.** |
| V-D5 | **API keys live only in a robot-local, root-owned env file** (`/etc/opendubs/voice.env`, mode 0600). Never in the repository, an image, or the browser. |

### Decided 2026-10-14 (the project team, relayed by the orchestrator)

All five open decisions were taken as recommended, with V-O4 narrowed to English only. V-O3 was
taken later the same day.

| # | Decision | Gate that still applies |
|---|---|---|
| V-O1 | **Push-to-talk** to start: a Talk button on the face, plus an operator "listen" control. A wake word later, as an opt-in (phase 3). | — |
| V-O2 | **The on-robot model by default**, with the Claude API as opt-in scaffolding per robot (`VOICE_BRAIN=auto`). | C-1 before `auto` is offered on a robot |
| V-O3 | **The local brain is Qwen3-30B-A3B-Instruct-2507** on vLLM, at a ≤ 24 GiB share. **Qwen3-4B-Instruct-2507 is the fallback if gate L-3 fails.** Both are Apache-2.0 on their model cards (checked 2026-10-14). | L-1 (its speed), L-3 (whether the fallback is needed) |
| V-O4 | **STT: Parakeet-TDT-0.6B-v2** (NVIDIA, CC BY 4.0) via sherpa-onnx (Apache-2.0). **TTS: Kokoro-82M** (Apache-2.0). **English only, and no GPL component ships.** Kokoro's other languages take their pronunciation from espeak-ng (GPL-3.0-or-later), so adding any of them is **a separate, explicit decision to ship GPL code**. Riva stays plan B. | L-4 |
| V-O5 | **The robot's voice plays from the tablet's speaker**, from the face page. | V-2, V-3 |

The model is still configuration, not code: the service takes its name from `VOICE_LLM_MODEL`, and
`image/run-llm.sh` takes it as an argument with its tool-call parser (`VOICE_LLM_TOOL_PARSER`), so
the L-3 fallback is a configuration change.

### Brought forward 2026-10-19: the wake word, "hey dubs" (the project team)

Phase 3.1 comes forward, ahead of the Kokoro latency work, and step 1.4 (Silero VAD) becomes
required for it. Talk stays, as the fallback and for a noisy room. The design, agreed with the
Tablet lane:

- **Detection on the tablet.** The face page runs sherpa-onnx keyword spotting (its WebAssembly
  build) on the model `sherpa-onnx-kws-zipformer-gigaspeech-3.3M-2024-01-01` (Apache-2.0, 3.3 M
  parameters), with one keyword, `▁HE Y ▁DU B S :1.5 #0.20 @HEY_DUBS`. The page keeps the last
  1.5 s of mic frames in memory. **No audio leaves the tablet before the keyword fires.**
- **The wire** (`wire.py`): on a hit the page sends `wake {keyword, pre_roll_ms}`, then the buffered
  frames, then live frames, all in the uplink format Talk uses. There is no `release`.
- **The end of the turn is the service's**, by Silero VAD (`vad.py`): it waits up to 4 s for speech
  to begin after the keyword's tail (0.35 s of grace), then ends the utterance after 0.8 s of
  silence. Talk's utterance cap still applies.
- **Verified on the Thor** (`wakeword.py`), at no extra cost: Parakeet transcribes the turn anyway,
  and the turn goes on only if the transcript begins with "hey dubs". The phrase is stripped before
  the language model sees the question. Otherwise, or when nothing follows the phrase, the turn
  ends **silently** with status `not_addressed`: a false wake never makes the robot speak. On the
  desk a spotter fired on "hey doves"; on the robot Parakeet wrote it "Hey doves." and the check
  refused it.
- **Paused while the robot is in a turn**: the page does not listen for the keyword during listen,
  think and speak, nor for 1.0 s after it sends `played`, so the robot cannot wake itself. The
  service ignores a `wake` that arrives outside idle. **Off whenever `voice.on` is false**, which
  includes every show (V-D4).
- **Indicators** (the team's choice): a hollow white ring while the page is listening locally for
  the keyword; the red REC dot, as for Talk, only while frames are being sent.
- **Gate V-7**: about an hour of the robot's normal use with show music, in which nobody says the
  phrase; fewer than 0.5 accepted false wakes an hour. An accepted false wake is a wake turn with
  status `ok` (the Thor's check passed, so the robot spoke). Also reported: the Thor's catches (wake
  turns ending `not_addressed`), and the spotter's hits the page suppressed and never sent
  (`/api/head/wake_hits`: `total` − `accepted`).
- **The switch**: `OPENDUBS_VOICE_WAKE` (default on); the service's `ready` carries `wake` only when
  it is on, and the page shows the ring only then.

**How V-O4's "no GPL" is held** (`software/voice/`, see its `NOTICE.md`): kokoro and misaki are
installed with `--no-deps`, without misaki's `[en]` extra (phonemizer-fork, espeakng-loader);
misaki is pinned to a commit of its main branch, because the 0.9.4 release has no fallback for
words outside its dictionary unless espeak-ng is installed (the commit's Apache-2.0 neural
fallback replaces it); `no_espeak.py` stands in for misaki's espeak module; `image/check_no_gpl.py`
is the image build's last step and a CI job. Weights are fetched at provision time
(`fetch_models.py`, `models.lock.json`), never in the repository or an image.

## Standing rules for this lane

1. **The face, not the body.** A model may set an expression, play a face gesture, read the
   robot's status (read-only) and end the conversation. Nothing else is reachable, and the tests
   enumerate every effect (`software/voice/safe_tools.py`).
2. **Voice off is the safe state.** The service fails closed: if it cannot confirm that no show is
   running, voice is off.
3. **Never invite speech that cannot be routed.** If the service, the brain or the link is down,
   Talk is greyed out, never left looking live.
4. **Nothing is kept.** No audio is stored anywhere. Transcripts live in memory for one
   conversation. Logs carry latencies and statuses, not words.
5. **Text only leaves the robot, and only in cloud or auto mode.** That means the transcript and
   the persona, never audio. A sign by the robot says so whenever cloud mode is on, and the
   institution's policy is checked first.
6. **Measured, not felt.** Every gate below has a number and is recorded with where and when it was
   taken.
7. **The non-robot Thor is the test bed.** The working robot gets pinned images and nothing
   experimental.

---

## Phase 0 — the gates: what only the hardware can answer

These are the note's §11 open questions, rewritten as gates. **Each gate blocks the step named in
its last column.** Kill criteria are [E] judgements from the note, to be argued if a result lands
near the line.

| gate | where | question | how | pass | kill / fallback | blocks |
|---|---|---|---|---|---|---|
| **V-1** | robot (tablet + Thor) | uplink round trip and loss on the robot's Wi-Fi | `tools/voice-bench/audio_lab.py` on a free port with the robot's certificate, test 1 | p95 ack < 100 ms, 0 lost frames in 5 min | WebCodecs Opus, or WebRTC | 1.10 |
| **V-2** | robot, **a person present** | how much of the robot's own TTS and of show music the mic hears, AEC on and off; does call mode spoil music | `audio_lab` test 3, then a show track with the mic open | ≥ 20 dB of TTS removed → full-duplex becomes possible (phase 3) | < 10 dB → half-duplex stays (it is the phase 1 design anyway); music degraded → the mic stays closed whenever music plays | 3.2; confirms 1.10 |
| **V-3** | robot | gaps in the downlink with a 120 ms buffer | `audio_lab` test 2, 20 runs | no audible gaps | raise the buffer | 1.10 |
| **V-4** | robot | does the device report a platform AEC, and does `getSettings()` show it on | page log; `AcousticEchoCanceler.isAvailable()` logged by the kiosk app (Tablet lane) | reported | WebRTC or native capture | explains V-2 |
| **V-5** | robot, **a person present** | is Android's mic privacy dot drawn over the lock-task face | look | either way the face's REC dot stays; this decides whether it is the *only* indicator | — | 1.10 |
| **V-6** | robot | time to reopen `getUserMedia` | press → first frame, mic closed beforehand | < 300 ms | keep the mic open for the whole conversation window (already the design) | the 20 s window |
| **V-8** | robot | how many microphones the SM-X133 exposes | `channelCount: 2`; `dumpsys media.audio_policy` | — | — | 3.6 (voice glance) |
| **V-9** | robot | does the next WebView still accept the robot's 397-day leaf certificate | `kiosk_check.py` after the update | secure context true | shorter leaf validity (Tablet lane) | everything on the tablet |
| **L-1** | bench Thor | TTFS of each candidate model at **120 W** | `tools/voice-bench/thor_llm_bench.sh vllm` (and `llamacpp`) | TTFS p90 ≤ 450 ms for the chosen model | the light model | 1.5, V-O3 |
| **L-2** | robot, read-only | memory and GPU the robot's stack already uses | `tegrastats` and `free -m` for 10 min in normal use, the Thor lane's audit method | recorded | — | the budget for L-3 |
| **L-3** | bench Thor | perception frame time with an LLM turn beside it | a perception stand-in, with and without L-1's best case at concurrency 1 and 2 | perception p99 ≤ 10 % worse | the light model, or the CPU fallback (note §2.3) | 1.5, 1.12 |
| **L-4** | bench Thor | Parakeet and Kokoro latency, alone and under L-3's load, CPU first; and Kokoro's pronunciation without espeak-ng | 1, 3 and 8 s clips; a 12-word sentence; a list of awkward words | STT ≤ 200 ms for a 3 s clip; TTS first chunk ≤ 200 ms; awkward words intelligible | the GPU (`build.sh --gpu`); then Riva (plan B). **Not** kokoro-onnx: it requires phonemizer (GPL) | 1.3b, V-O4 |
| **C-1** | robot's uplink | Claude TTFS and cost per turn from the robot's own network | `tools/voice-bench/claude_bench.py` with the robot's key | first text p90 ≤ 1.2 s | `auto` mode not offered on that robot | 2.1, V-O2 |
| **V-7** | robot | wake-word CPU cost on the tablet, and accepted false wakes in normal use with show music | the page's local hit counter and the service's `not_addressed` log, over about an hour | < 0.5 accepted false wakes per hour | a higher keyword threshold; then Talk only | 3.1 (brought forward) |

**The phase 1 pass line** (note §3.6): release to first audio **p90 ≤ 1.2 s on the robot**, at
120 W, with perception running, and perception p99 frame time **no more than 10 % worse**.

---

## Phase 1 — a minimal push-to-talk demo

| # | step | where | done when | status |
|---|---|---|---|---|
| 1.1 | **The voice service skeleton**: the `/ws/voice` wire protocol, turn state, pluggable STT/LLM/TTS (fake, local OpenAI-compatible, Claude), the brain router, `safe_tools`, the show interlock, the face-door client | desk | its tests pass in CI (67, fake audio and fake backends) | merged, #230 |
| 1.2 | **The `/ws/voice` relay** in the dashboard: origin guard, frame cap, `VOICE_SERVICE_DOWN` | desk | its tests pass in `dashboard.yml` | merged, #231 |
| 1.3a | **Parakeet and Kokoro backends, English only, no GPL** (V-O4): `speech.py` on sherpa-onnx and kokoro/misaki, `no_espeak.py`, `check_no_gpl.py` in the build and in CI, pinned packages, pinned weights fetched at provision time, `NOTICE.md` | desk | unit tests with fakes; CI's `speech-deps` job installs the pinned packages as the image does, finds no GPL, and runs Kokoro's English pipeline with no espeak-ng | PR (this change) |
| 1.3b | **The real weights on the bench Thor**: `fetch_models.py`, then both backends on the CPU first; the GPU image (`build.sh --gpu`) only if L-4 needs it. Check pronunciation without espeak-ng on a set of awkward words (names, numbers, acronyms) | bench Thor | L-4 passes | waits on the bench Thor (TS-3) |
| 1.4 | **Silero VAD**, required by the wake word: `vad.py` ends a wake turn on it (sherpa-onnx's `VoiceActivityDetector`, model pinned in `models.lock.json`); then tuned on recordings of the robot's own room (motors, ESCs, lift, show music). Talk turns keep the energy gate in `audio.py` until the tuning is done | desk, then robot | a wake turn ends within 1 s of the visitor stopping; no clip of room noise is sent to STT; no real utterance is rejected, on a recorded set | desk half: PR (this change); tuning waits on recordings |
| 1.5a | **The local brain, desk half**: Qwen3-30B-A3B (Red Hat AI's 4-bit W4A16 build, 16.7 GB, so it fits the ≤ 24 GiB share) and the 4B fallback pinned in `models.lock.json`; one serving profile (`llm_profile.py`) read by `image/run-llm.sh` and the service; vLLM serving the pinned local copy offline; a readiness check that refuses a server serving another model; a warm-up request at start (one reported first run was about 20× slower than warm, the note §3.4); history kept within the 8k context | desk | its tests pass in CI | PR #267 |
| 1.5b | **The local brain on the bench Thor**: fetch the weights, `run-llm.sh qwen3-30b-a3b-w4a16`, then gates L-1 and L-3 (or L-3 sends it to `qwen3-4b`) | bench Thor | L-1 and L-3 pass; the memory share is recorded | waits on the image pin (TS-1) and bench time (TS-3) |
| 1.6 | **Images built and pinned**: `opendubs-voice` and `opendubs-llm` built on the bench Thor, pushed by digest, run from `run.sh` / `run-llm.sh` | bench Thor | both start from pinned digests; `run.sh` refuses a loose `voice.env` | needs the Thor lane (TS-1) |
| 1.7 | **The face page's side**: mic capture, the `/ws/voice` client, playback, the Talk button and the REC dot | desk (the Tablet lane's) | requests TL-1 to TL-4 below | **Tablet lane** |
| 1.8 | **The face's looks**: listening, thinking, speaking and recording, with the mouth driven from the playing audio when enabled | desk (Face & HRI's) | request FH-1 to FH-3 below | **Face & HRI** |
| 1.9 | **`CONVO` agreed** and in the protocol; the service then sends it (`OPENDUBS_VOICE_CONVO=1`) | desk | request TL-5; the conformance suite covers it | **Tablet lane**, with Face & HRI |
| 1.10 | **Desk end to end**: the face page in a desktop browser, a dashboard and the voice service this session started on free ports, fake backends, then the bench Thor with real models | desk, then bench Thor | a turn runs end to end; barge-in cuts within one 100 ms chunk; a show starting cuts the turn; killing the service greys out Talk | after 1.7 |
| 1.11 | **Deployed to the robot**: images by digest through `deploy-all.sh`, `voice.env` created root:600, the local brain only | robot | the service runs on `main`'s images; voice is off in a show | needs the Thor lane (TS-2); V-1, V-3, V-5 |
| 1.12 | **The demo**: ten visitors in a row each get an answer | robot, **a person present** | the phase 1 pass line above, measured; the REC dot never wrong | — |

## Phase 2 — hardening

| # | step | where | done when |
|---|---|---|---|
| 2.1 | **`auto` brain** with Claude Haiku 5.5: cloud while healthy, a 2.5 s first-text timeout, then local (already in `brains.Router`; tuned to C-1's numbers) | bench Thor, then robot | C-1 passes; a pulled uplink mid-conversation costs one slower answer, not silence |
| 2.2 | **Pre-synthesised fallback lines** ("say that again", "give me a moment", "I can't think right now"), so they play even when TTS is the failing stage | bench Thor | each plays with TTS stopped |
| 2.3 | **Fault codes** in the troubleshooting registry: `VOICE_SERVICE_DOWN`, `VOICE_MIC_UNAVAILABLE`, `VOICE_BRAIN_OFFLINE`, `VOICE_OFF_SHOW` | desk | `faults.py --check` passes with them named in the source; needs DB-1 |
| 2.4 | **A health and status endpoint** on the service (brain mode and health, gate state, last turn's latencies), read by the dashboard | desk | the operator can see why voice is off |
| 2.5 | **A persona eval set**: 20–50 visitor questions with the answers the team accepts, run on every model or persona change | desk / bench Thor | a change that breaks an accepted answer fails the run |
| 2.6 | **Dependencies locked with hashes** (`uv` lock), as the arm image does | desk | `build.sh` installs only hashed, locked packages |
| 2.7 | **Operator controls**: voice on/off, the "listen" control, mic state, the current conversation's transcript (not persisted) | desk | request TL-6 |
| 2.8 | **The sign and the policy check** for cloud mode | robot | a sign by the robot whenever `VOICE_BRAIN` is `cloud` or `auto` |
| 2.9 | **Per-stage latency in the logs**, summarised per day, so drift is visible | robot | p50/p90 per stage readable without a debugger |

## Phase 3 — optional, each its own team decision

- 3.1 ~~Wake word~~ brought forward, 2026-10-19: see "Phase 3.1 — the wake word" below.
- 3.2 Full-duplex voice barge-in, only if V-2 passes.
- 3.3 Pipecat for hands-free turn-taking.
- 3.4 TensorRT Edge-LLM for a shorter TTFS.
- 3.5 Captions of what the robot says, on the face, for visitors who are deaf or hard of hearing.
- 3.6 A voice glance (turning the eyes toward the speaker), if V-8 finds two usable microphones.

**Not in this plan:** routines by voice, or any other motion (V-D3).

## Phase 3.1 — the wake word, "hey dubs" (brought forward)

| # | step | where | done when | status |
|---|---|---|---|---|
| W.1 | **The service side**: `wake` in `wire.py`, wake turns in `conversation.py`, `vad.py` (1.4's desk half), `wakeword.py`, Silero's weights in `models.lock.json`, `OPENDUBS_VOICE_WAKE` | desk | `tests/test_wake.py` passes in CI | PR (this change) |
| W.2 | **Silero's weights on the robot**: `fetch_models.py` (its default set now includes `silero-vad`), before the first deploy of W.1, or `run.sh --check` refuses the models directory | robot | `vad/silero_vad.onnx` present and its hash checked | after W.1 merges |
| W.3 | **The page side** (TL-9 to TL-11): keyword spotting, the pre-roll buffer, `wake`, suppression, the ring | desk (the Tablet lane's) | the Tablet lane's tests | **Tablet lane** |
| W.4 | **Desk end to end**: a recorded "hey dubs, what is your name?" played into the page answers; "hey doves" stays silent | desk | both, five times each | after W.3 |
| W.5 | **Gate V-7** on the robot | robot | V-7 passes | after W.4 |

---

## Requests to other lanes

These are written here and sent through the orchestrator. Each owning lane decides how to do it.

### Tablet and head interface

| # | Request | Why / contract |
|---|---|---|
| **TL-1** | **Mic capture in the face page.** `getUserMedia({audio: {echoCancellation: true, noiseSuppression: true, autoGainControl: true, channelCount: 1}})`; an AudioWorklet that **low-pass filters before** resampling to 16 kHz (decimating without it folds ESC whine onto the vowels), emitting 20 ms s16le mono frames (320 samples) with an 8-byte little-endian header `u32 seq, u32 page_ms`. Frames are sent **only while Talk is held**; `seq` restarts at 0 for each utterance. The mic opens on a press and closes on `conversation_end`, after 20 s with no turn, or when the socket drops. **It is never left open.** | `software/voice/wire.py`; the note §5.2. `tools/voice-bench/audio_lab.html` has a working worklet |
| **TL-2** | **The `/ws/voice` client.** Open `wss://<origin>/ws/voice`; send `hello` `{protocol: 1, up: {rate: 16000, format: "s16le", channels: 1}}`; then `press`, `release`, `barge_in`, `played {turn}`. Handle `ready`, `voice {on, why}`, `state {turn, convo}`, `heard`, `turn {status}`, `tts_end`, `stop`, `conversation_end`, `error`. On `error VOICE_SERVICE_DOWN`, a dropped socket, or `voice.on == false`, Talk is greyed out with a short reason. Reconnect with backoff. | `wire.py` is the whole contract. The relay is PR #231 |
| **TL-3** | **Playback.** Downlink chunks are `u32 turn, u32 seq` + s16le 24 kHz mono, 100 ms each. Use a 120 ms jitter buffer and drop every queued chunk of a turn on `stop`. Send `played {turn}` once a turn's audio has drained after `tts_end`. Drive the mouth: an `AnalyserNode` envelope on the playing audio → `face.setMouthLevel(0..1)`, with `face.setMouth()` set from the Head & Face preference (PR #229). | The page plays in the same WebView that captures, which gives the platform AEC its reference (V-2) |
| **TL-4** | **The Talk button.** A fixed region of the system strip, never skinnable or covered by media. Press and hold to talk; a press during a reply is a barge-in (`press` covers both). **Hidden while `voice.on` is false** (a show, or switched off) and in CONTROLLER mode (a held tablet is the operator's controller). The REC dot is lit from the track itself (`readyState === "live" && enabled`), never from a message. | V-D4; the note §6.4. Where it sits relative to DT-12's touch is the Tablet lane's call |
| **TL-5** | **`CONVO` in the head protocol**, as proposed in the note §6.4: `CONVO LISTEN\|THINK\|SPEAK [lease_ms]` and `CONVO IDLE`, replying `OK convo <state>`. The service sends a 3000 ms lease, renewed every second while not idle, so a dead service relaxes the face within 3 s. It never changes `EMOTE`'s pin or `STATE`, and reserved states keep their look. `PING` adds `convo=` and `mic=`. Add it to `head-protocol.md`, `head_protocol.py`, the conformance suite and `fake_head.py`. Until it ships, the service keeps `CONVO` off and the page learns the state from `/ws/voice` `state` messages. | A new head message is agreed with the Tablet lane before either side ships it (CLAUDE.md) |
| **TL-6** | **Operator controls** in `operate.js` (phase 2): voice on/off, the "listen" control for staged dialogue, mic state, and the current conversation's transcript (not persisted). | 2.7 |
| **TL-7** | **The mic grant for an in-app face origin.** If the face moves into the app's assets (`appassets.androidplatform.net`, DT-9), `KioskActivity.kt`'s `onPermissionRequest` must grant `RESOURCE_AUDIO_CAPTURE` to that origin too, or the mic goes silent. | the note §5.7 |
| **TL-9** | **Keyword spotting in the face page.** sherpa-onnx's WebAssembly KWS, fetched by `fetch-vendor.sh` with its hash, on the model and keyword line above. It runs on the same 16 kHz frames TL-1 makes, only while `ready.wake` is present, `voice.on` is true and the state is idle, and not for 1.0 s after `played`. A ring buffer holds the last 1.5 s of frames. Nothing is sent before a hit. | `wire.py` `wake`; the decision above |
| **TL-10** | **The wake turn.** On a hit: send `wake {keyword: "hey dubs", pre_roll_ms}`, then the buffered frames (`seq` from 0) and live frames until the service leaves `listen`. No `release`. A turn status `not_addressed` is silent: no sound and no "didn't catch that" look. | `wire.py` |
| **TL-11** | **The listening ring**: a hollow white ring in the system strip while the page listens locally for the keyword, beside the REC dot (TL-4), which still lights from the track only while frames are sent. Not skinnable, like the REC dot. Also the local hit counter for V-7, readable from the page. | the team's choice of indicators; V-7 |
| **TL-8** | **Robot time for V-1 to V-6, V-8 and V-9** on the tablet, with a person present for V-2 and V-5. Log `AcousticEchoCanceler.isAvailable()` from the kiosk app for V-4. | Phase 0 |

### Face & HRI

| # | Request | Why |
|---|---|---|
| **FH-1** | **Listening, thinking and speaking looks**, driven by `CONVO` (or, until it ships, by the page's `state`): eyes attend on LISTEN (and the edge ring's listening motion, the real source it was waiting for); an honest thinking state on THINK; on SPEAK, the mouth from `setMouthLevel` when enabled, otherwise brows and nods from the same envelope. Each relaxes smoothly when its lease expires. Reserved states (`DEGRADED`, `E_STOP`) win over all of them. | the note §6.4; `face-decision-register.md` on the edge ring |
| **FH-2** | **The REC indicator's look**: a red dot in the system strip, not skinnable or themeable, like E_STOP's reserved colours. The Tablet lane binds it to the track (TL-4). | V-5 |
| **FH-3** | **A brief "didn't catch that" reaction** for a turn whose status is `unintelligible` (the note suggests a short squint), so the spoken "say that again" has a face to go with it. | the note §9 |

### Thor stack and localization

| # | Request | Why |
|---|---|---|
| **TS-1** | **Pins in `versions.env`**: `VOICE_LLM_IMAGE` (a vLLM image for Thor, by digest; the note §3.1 lists the candidates, and the vLLM 26.02 NGC image halved Qwen3-30B-A3B's speed), and `ISAAC_ROS_BASE_IMAGE` for the GPU voice image. | 1.3, 1.5, 1.6 |
| **TS-2** | **Deployment**: `deploy-all.sh` and provisioning build and run `opendubs-voice` (then `opendubs-llm`). Provisioning creates an empty `/etc/opendubs/voice.env` owned by root with mode 0600, and `/var/lib/opendubs/models`. Containers need the NVIDIA runtime (your 1.1). | 1.11 |
| **TS-3** | **L-2**: a read-only `tegrastats` / `free -m` baseline on opendubs2 in normal use, by your audit's method. Also your OK for the Voice lane to run L-1, L-3 and L-4 on the non-robot Thor, and a slot when it is free. | Phase 0 |

### Docs and BOM

| # | Request | Why |
|---|---|---|
| **DB-1** | **A `VOICE` prefix** in `tools/faults.py`'s `PREFIXES`, and a `docs/reference/registry/voice.yaml` for the Voice lane's entries. | 2.3 |

### Choreography (to confirm, no change expected)

| # | Request | Why |
|---|---|---|
| **CH-1** | **Confirm that `GET /api/choreo/palette` → `running` is the contract for "a show is running"**, including an armed show waiting for the deadman. From the code, an armed run's thread is already alive, so `running` covers it. The voice service turns voice off on any non-null `running`, and also when the dashboard cannot be read. | V-D4 |

---

## Stub register — what is a stand-in today

| stub | where | replaced by |
|---|---|---|
| energy-based speech gate (peak vs floor) for Talk turns, and `vad.EnergyVAD` where Silero's weights are missing | `software/voice/audio.py`, `vad.py` | Silero VAD tuned on the room (1.4) |
| the wake word's timings (4 s, 0.35 s, 0.8 s) are estimates | `vad.py` | tuned at W.4 and V-7 |
| fake STT and TTS | `speech.py` | the image's default is now Parakeet and Kokoro (1.3a); the fakes stay for tests and desk demos |
| Kokoro on random stand-in weights in CI | `tests/smoke_speech_deps.py` | the real weights on the bench Thor (1.3b) |
| British English refused | `speech.py` | pin misaki's `en_gb` fallback in `models.lock.json`, if wanted |
| transitive Python dependencies not locked; misaki's build pulls `hatchling` unpinned | `image/requirements-*.txt` | 2.6 |
| `CONVO` not sent (`OPENDUBS_VOICE_CONVO=0`) | `face.py` | TL-5 |
| holding and apology lines synthesised live | `conversation.py` | 2.2 |
| a generic persona | `persona.txt` (per robot via `OPENDUBS_VOICE_PERSONA`) | the team's own, with the eval set (2.5) |
| dependencies pinned by version, not hash | `image/requirements.txt` | 2.6 |
| no status endpoint | `server.py` | 2.4 |
| the image is not built yet | `image/` | 1.6 |
