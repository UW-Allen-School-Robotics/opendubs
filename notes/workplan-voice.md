# Work plan — voice: push-to-talk conversation with the robot, from a desk demo to hardening

**DRAFT, 2026-10-13, written by the Voice lane. Not yet agreed.** Once the team takes the open
decisions below it becomes the plan of record; finished items are then struck through with a date,
as in [`workplan-tablet.md`](workplan-tablet.md). Only the Voice lane edits this file (CLAUDE.md
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

## Open decisions (recommendations from the note, sent to the orchestrator)

| # | Question | Recommendation | Gate that informs it |
|---|---|---|---|
| V-O1 | How a turn starts (note D1) | **Push-to-talk** on a Talk button in the face's system strip, plus an operator "listen" control. A wake word only later, as an opt-in (phase 3) | — |
| V-O2 | Which brain by default (D4) | **`local`**, with the Claude API opt-in per robot (`VOICE_BRAIN=auto`) | C-1, L-1 |
| V-O3 | Local model (D5) | **Qwen3-30B-A3B-Instruct-2507** on vLLM at a ≤ 24 GiB share; **Qwen3-4B-Instruct-2507** if L-3 fails | L-1, L-3 |
| V-O4 | STT and TTS (D6) | **Parakeet-TDT-0.6B-v2** and **Kokoro-82M**; Riva only as plan B | L-4 |
| V-O5 | Where the voice plays (D7) | **The tablet's speakers, from the face page**, for conversation | V-2, V-3 |

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
| **L-4** | bench Thor | Parakeet and Kokoro latency, alone and under L-3's load | 1, 3 and 8 s clips; a 12-word sentence | STT ≤ 200 ms for a 3 s clip; TTS first chunk ≤ 200 ms | Riva (plan B), or Moonshine and `kokoro-onnx` on the CPU | 1.3, V-O4 |
| **C-1** | robot's uplink | Claude TTFS and cost per turn from the robot's own network | `tools/voice-bench/claude_bench.py` with the robot's key | first text p90 ≤ 1.2 s | `auto` mode not offered on that robot | 2.1, V-O2 |
| **V-7** | robot | wake-word CPU cost and false accepts with show music | sherpa-onnx in the face page | < 0.5 false accepts per hour | no wake word | 3.1 only |

**The phase 1 pass line** (note §3.6): release to first audio **p90 ≤ 1.2 s on the robot**, at
120 W, with perception running, and perception p99 frame time **no more than 10 % worse**.

---

## Phase 1 — a minimal push-to-talk demo

| # | step | where | done when | status |
|---|---|---|---|---|
| 1.1 | **The voice service skeleton**: the `/ws/voice` wire protocol, turn state, pluggable STT/LLM/TTS (fake, local OpenAI-compatible, Claude), the brain router, `safe_tools`, the show interlock, the face-door client | desk | its tests pass in CI (67, fake audio and fake backends) | PR #230 |
| 1.2 | **The `/ws/voice` relay** in the dashboard: origin guard, frame cap, `VOICE_SERVICE_DOWN` | desk | its tests pass in `dashboard.yml` | PR #231 |
| 1.3 | **Parakeet and Kokoro** behind `speech.py`, in the GPU image (`build.sh --gpu`, on `ISAAC_ROS_BASE_IMAGE`) | bench Thor | L-4 passes; the fakes stay for tests | waits on L-4 and the Thor lane's base image |
| 1.4 | **Silero VAD** replaces the energy gate in `audio.py`, tuned on recordings of the robot's own room (motors, ESCs, lift, show music) | desk, then bench Thor | no clip of room noise is sent to STT; no real utterance is rejected, on a recorded set | — |
| 1.5 | **The local brain**: `opendubs-llm` (vLLM) from `image/run-llm.sh`, with a warm-up request at start so the first visitor does not wait (one reported first run was about 20× slower than warm, the note §3.4) | bench Thor | L-1 and L-3 pass; the chosen model and its memory share are recorded | waits on L-1, L-3 and the image pin |
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

- 3.1 Wake word, detected on the tablet (sherpa-onnx) and verified on the Thor (gate V-7).
- 3.2 Full-duplex voice barge-in, only if V-2 passes.
- 3.3 Pipecat for hands-free turn-taking.
- 3.4 TensorRT Edge-LLM for a shorter TTFS.
- 3.5 Captions of what the robot says, on the face, for visitors who are deaf or hard of hearing.
- 3.6 A voice glance (turning the eyes toward the speaker), if V-8 finds two usable microphones.

**Not in this plan:** routines by voice, or any other motion (V-D3).

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
| energy-based speech gate (peak vs floor) | `software/voice/audio.py` | Silero VAD (1.4) |
| fake STT and TTS | `speech.py` | Parakeet and Kokoro (1.3); the fakes stay for tests |
| `parakeet` / `kokoro` refuse to start | `speech.py` | 1.3 |
| `CONVO` not sent (`OPENDUBS_VOICE_CONVO=0`) | `face.py` | TL-5 |
| holding and apology lines synthesised live | `conversation.py` | 2.2 |
| a generic persona | `persona.txt` (per robot via `OPENDUBS_VOICE_PERSONA`) | the team's own, with the eval set (2.5) |
| dependencies pinned by version, not hash | `image/requirements.txt` | 2.6 |
| no status endpoint | `server.py` | 2.4 |
| the image is not built yet | `image/` | 1.6 |
