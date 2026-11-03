# Work plan — voice: talking with the robot, by "Hey OpenDubs" or by Talk

**Plan of record, rewritten 2026-10-20 to what runs on the robot today.** Written by the Voice lane;
only the Voice lane edits this file (CLAUDE.md "Lanes", rule 3), and findings from other lanes come
as messages. Finished items keep their PR number; open ones say what they wait on.

**Background.** The research behind the choices is
[`design/voice-llm-research.md`](design/voice-llm-research.md) (PR #217, "the note", cited by
section). The hard line on authority is [`design/face-design.md`](design/face-design.md): no
language-model authority over motion. The Tab5 head's voice plan,
[`workplan-tab5-audio.md`](workplan-tab5-audio.md), is paused; the head is now the tablet.

**Jargon.** STT: speech-to-text. TTS: text-to-speech. VAD: voice activity detection. KWS: keyword
spotting, listening for one phrase on the device. AEC: acoustic echo cancellation (removing the
robot's own voice from what its microphone hears). TTFS: time to first sentence. Barge-in:
interrupting the robot while it talks. Pre-roll: audio from just before a press or a wake, kept so
the first word is not lost.

**Where work runs:**

| mark | where | rule |
|---|---|---|
| **desk** | a laptop, no robot hardware | fake audio and fake backends; only processes and ports this session started |
| **bench Thor** | the non-robot Thor | experiments with models, containers and GPU contention |
| **robot** | opendubs2 and its tablet | runs `main`, through `deploy-all.sh`; weights fetched once into `/var/lib/opendubs/models` |

---

## What runs on opendubs2 today

A visitor says **"Hey OpenDubs, what's your name?"**, or holds **Talk** and asks. The robot answers out
loud from the tablet's speaker, with its face, in about a second, without the internet.

| part | what it is | where | PR |
|---|---|---|---|
| the face page's voice | mic capture (16 kHz, 20 ms frames), the `/ws/voice` client, playback, the Talk button and the red REC dot | tablet | #365 |
| the relay | `/ws/voice` in the dashboard: origin guard, frame cap, `VOICE_SERVICE_DOWN`; `listening()` for the neck | Thor | #231, #377 |
| the voice service | `opendubs-voice`, the GPU image: turn state, the show interlock, `safe_tools` (face only) | Thor | #230, #363, #368, #369, #370 |
| STT | Parakeet-TDT-0.6B-v2, int8, on sherpa-onnx, CPU: about 0.2 s for a 3 s question | Thor | #265 |
| the brain | **Qwen3-30B-A3B-Instruct-2507** (Red Hat AI's W4A16 build) on vLLM, `opendubs-llm`, at a GPU memory share of **0.20**, local only | Thor | #267, #362, #366 |
| TTS | **Kokoro-82M on the GPU**, English only, no espeak-ng: first audio p90 844 ms after release in gate L-4 | Thor | #370 |
| end of a wake turn | Silero VAD: up to 4 s to begin, ends after 0.8 s of silence | Thor | #372 |
| the wake phrase | "Hey OpenDubs", spotted on the tablet, checked on the Thor (below) | both | #372, #373, #378, #379, #383, #400, #413, #416, #424 |
| Talk's edges | 300 ms of pre-roll on a press and a 400 ms tail after release, so the first and last words arrive | tablet | #380 |
| a quiet neck | the head holds still while someone speaks, and moves slowly otherwise: servo noise hurt recognition | Thor | #375, #382 |
| the turn log | latencies, status and the clip's shape (levels, where speech starts and ends), never words | Thor | #376 |

**Configuration**, per robot: `/etc/opendubs/voice.env`, root-owned, mode 0600
(`VOICE_LLM_PROFILE=qwen3-30b-a3b-w4a16`, `VOICE_LLM_GPU_SHARE=0.20`). Deploys rebuild the voice
image only when its tree or its base changes (#371); they never fetch weights, and `run.sh` checks
every pinned weight before it replaces the running container.

**Open, not yet on the robot:** #402 (speech rules, below), #399 (the spotter's keyword line set per
robot from `voice.env`).

---

## The wake phrase, "Hey OpenDubs" — PARKED

**Parked on 2026-10-20 by the project team, and off by default since 2026-10-22.** After the
"Hey OpenDubs" deploy the page's self-test hit, but a live "hey open dubs" at a normal distance got
0 of 3, even at speech peaks around −11 dBFS. On 2026-10-22 the face's tracker stalled while the
spotter's worker kept failing to load beside it, so wake was switched off on opendubs2
(`OPENDUBS_VOICE_WAKE=0`) and is now off unless a robot sets it to 1. Talk and status by voice
carry on. The design below stays as the record of what was built; what was tried and measured, for
whoever picks up voice input next, is in [`design/voice-wake-findings.md`](design/voice-wake-findings.md).

**The open question for later: voice input in a crowd.** Demos will often be in crowds, not a quiet
room, so voice-in may need rethinking altogether rather than more tuning. Options to weigh, none
decided: a microphone array with beamforming (the sketch under "Later" below); a presenter's
wireless or lapel microphone; a physical push-to-talk button on the robot; an operator "listen"
control on the dashboard; other wake-word approaches.

Brought forward on 2026-10-19 by the project team, ahead of latency work; it is part of phase 1.
**Talk stays** as the fallback, and for a noisy room. The phrase was "hey dubs" until 2026-10-20;
the team lengthened it to **"Hey OpenDubs"**, spoken "hey open dubs" (#424, and the Tablet lane's
page change), because about six phonemes was too short to spot at a normal distance.

- **Spotted on the tablet.** The face page runs sherpa-onnx KWS, built for the browser from pinned
  sources (`software/face-web/kws/build-kws.sh`), on `sherpa-onnx-kws-zipformer-gigaspeech-3.3M`
  (Apache-2.0). The tokens are the model's own BPE encoding of the words "HEY OPEN DUBS", from
  `sherpa_onnx.text2token` against its `bpe.model`: `▁HE Y ▁O P EN ▁DU B S`. The boost and
  threshold are the Tablet lane's, from its grid. The page keeps the last 1.5 s of audio in memory,
  and **no audio leaves the tablet before the phrase fires.**
- **What the robot tests found** (2026-10-19 and 20):
  - the strict lines (`#0.20`, `#0.10`) never fired, so the line was tuned for recall (`:5.0 #0.01`, #400), since the Thor checks every hit;
  - the spotter is level-sensitive, so it gets its own gain stage, which the uplink does not (#413);
  - each reconnect added another microphone stream, so the spotter, Talk and the uplink all got two streams interleaved (#416), which explained the misheard Talk turns too;
  - with that fixed, "hey dubs" still fired only close to the face. Turning the tablet's echo cancellation, noise suppression and AGC off did not help, which is why the phrase was lengthened.
- **A self-test on every page load** (#400) feeds a known clip of the phrase into a second spotter
  and reports hit or miss in `/api/head/wake_hits`, beside the page's listening health (#383, #418:
  audio rate, levels by band, frames into the spotter, why it is suppressed; numbers only).
- **The wire** (`wire.py`): on a hit the page sends `wake {keyword, pre_roll_ms}`, then the buffered
  and live frames. There is no `release`; the service ends the utterance by VAD.
- **Checked on the Thor** (`wakeword.py`, #378, #424): Parakeet transcribes the turn anyway, and the
  turn goes on only if the phrase appears within its first six words. "Hey open dubs", "hey
  opendubs", "open dubs" with the "hey" lost, and run-together forms are accepted, each word within
  one spelling edit. "Hey dubs" is accepted here only, because Parakeet sometimes drops the middle
  word; the page does not spot it, so it costs no false wakes. Near misses ("hey doves", "open the
  door", "hey, open the window") are refused. The phrase is removed before the model sees the
  question; anything else ends **silently** (`not_addressed`).
- **Paused** while a turn is in progress, for 1.0 s after a reply has played, and whenever voice is
  off, which includes every show.
- **Indicators**: a hollow white ring while the page listens locally; the red REC dot only while
  audio is being sent.
- **Switches**: `OPENDUBS_VOICE_WAKE` (default off while parked; 1 turns it on); `OPENDUBS_VOICE_WAKE_KEYWORDS` retunes the
  spotter from the robot without a page change (#399, open). The turn log's `uplink_x` (#417)
  flags a page sending more than one stream.

**Gate V-7** (still to run): about an hour of the robot's normal use with show music, nobody saying
the phrase; fewer than 0.5 accepted false wakes an hour. An accepted false wake is a wake turn that
ends `ok`. Also reported: the Thor's catches (`not_addressed`) and the hits the page suppressed
(`wake_hits`: `total` − `accepted`).

---

## What the robot says (#402)

Recognition quality comes before new features (the project team, 2026-10-19). Visitors heard
"I don't understand" too often, and once "Let me show you a little move", with an emoji.

- **The persona** (`persona.txt`): one or two short sentences; plain spoken words; the transcript
  comes from speech recognition, so answer the most likely meaning and ask to repeat only when there
  is no sensible reading; no motion of any kind, offered, promised or described.
- **Held in code as well** (`spoken.py`): every sentence the model writes loses emoji, markdown and
  `*stage directions*`, and a sentence that offers or claims motion is dropped, the first one
  replaced by "I can't move on my own; my operator controls that." The history keeps what was
  actually said. `tests/test_spoken.py` holds the real failing reply.

---

## Decisions of record (the project team)

| # | Decision |
|---|---|
| V-D1 | **The robot's Galaxy tablet is the face and the audio device**, in and out. |
| V-D2 | **The mouth is enableable and off by default** (PR #222). |
| V-D3 | **Motion is never commanded by a language model. Routines by voice stay off.** The voice service has no motion tool; V-D6 below keeps it so. |
| V-D4 | **Voice is off during shows.** |
| V-D5 | **API keys live only in `/etc/opendubs/voice.env`**, root-owned, mode 0600. Never in the repository, an image, or the browser. |
| V-O1 | **Talk and the wake phrase "Hey OpenDubs"** start a turn (the wake word brought forward on 2026-10-19; lengthened from "hey dubs" on 2026-10-20). |
| V-O2 | **The on-robot model by default.** The Claude API stays opt-in per robot (`VOICE_BRAIN=auto`), not offered until gate C-1. |
| V-O3 | **Qwen3-30B-A3B-Instruct-2507** on vLLM; Qwen3-4B-Instruct-2507 is the fallback if gate L-3 fails. Both Apache-2.0. |
| V-O4 | **STT Parakeet-TDT-0.6B-v2** (CC BY 4.0) via sherpa-onnx; **TTS Kokoro-82M** (Apache-2.0). **English only, and no GPL component ships**; another Kokoro language would mean shipping espeak-ng (GPL), a separate explicit decision. |
| V-O5 | **The robot's voice plays from the tablet's speaker.** |

**How "no GPL" is held** (`software/voice/NOTICE.md`): kokoro and misaki are installed with
`--no-deps`, without misaki's espeak extras; misaki is pinned to a commit whose Apache-2.0 neural
fallback replaces espeak-ng for unknown words; `no_espeak.py` stands in for misaki's espeak module;
`image/check_no_gpl.py` runs in the image build and in CI (LGPL allowed only for `num2words`).
Weights are fetched at provision time (`fetch_models.py`, `models.lock.json`), never committed.

## What voice may do: say what the dashboard shows, do nothing it does

Decided 2026-10-20 (the project team, relayed by the orchestrator). Built in `robot_facts.py`; the
snapshot endpoint is the Tablet lane's.

| # | Decision |
|---|---|
| V-D6 | **Voice may say anything the dashboard shows, and never do anything the dashboard does.** Status parity, no controls. This settles the earlier proposals (voice as a light-class controller picking armed show segments; a voice e-stop): voice has no controls at all. |
| V-D7 | **One source of truth.** One read-only tool, `robot_status`, returns the snapshot the dashboard renders, in its own words: state and its reason, battery and charging, faults, the arms, whether a show is running and which, the network uplink, uptime, and the controller in charge once the modes work lands. It comes from `GET /api/status/spoken`, which the Tablet lane builds from status.py's own functions. There is no separate model-side picture of the robot. |
| V-D8 | **Fault explanations**: for an active fault, voice may read the troubleshooting registry's "what it means" and "what to do", words only. They reach the image as `software/voice/fault_help.json`, generated by `tools/faults.py` and checked in CI (the Docs lane). |
| V-D9 | **Honest**: only live data is said. A stale or missing snapshot, or a field the dashboard cannot vouch for, is said as "I can't tell right now". No invented numbers. |
| V-D10 | **The same answers for everyone; no operator mode.** Operators use the dashboard. Left out: IP and MAC addresses, network names, device paths and IDs, serial numbers, emails and links, account and people's names, and anything only an operator needs. |

How it is held: the tool reads only the agreed fields, scrubs every string (`robot_facts.scrub`),
treats a snapshot older than 5 s as no snapshot, and returns the facts with the rule for unknowns
attached. The persona says to answer questions about the robot only from it. `tests/test_robot_facts.py`
covers the left-out list and the stale-data cases.

## Standing rules for this lane

1. **The face, not the body.** A model may set an expression, play a face gesture, read the robot's
   status and end the conversation. Nothing else is reachable (`safe_tools.py`, enumerated by tests).
2. **Voice off is the safe state.** If the service cannot confirm that no show is running, voice is off.
3. **Never invite speech that cannot be routed.** If the service, the brain or the link is down,
   Talk is greyed out.
4. **Nothing is kept.** Transcripts live in memory for one conversation; logs carry latencies,
   statuses and a clip's shape, never words. Audio is never stored, except in an opt-in recording
   session that the speakers agree to (`OPENDUBS_VOICE_RECORD_DIR`, in memory, erased by a restart).
5. **Text leaves the robot only in cloud or auto mode**: the transcript and the persona, never audio,
   and a sign by the robot says so.
6. **The robot runs `main`.** Experiments happen on the desk or the bench Thor.

---

## Gates

| gate | question | status |
|---|---|---|
| **L-4** | Parakeet and Kokoro latency on the robot | **passed** (#370): first audio p90 844 ms on the GPU |
| **L-1** | the brain's time to first text | met in use: first text about 0.3 s after release on the robot; not run as a bench |
| **V-7** | accepted false wakes per hour, with show music | to run, once #400 has been in normal use |
| **L-2 / L-3** | GPU and memory the stack already uses; perception frame time with a turn beside it | not run; decides whether the 4B fallback is needed |
| **V-1 / V-3** | uplink loss and downlink gaps on the robot's Wi-Fi | not run as a bench; no lost frames seen in the turn log |
| **V-2 / V-4** | how much of its own voice and of show music the mic hears; whether the platform AEC is on | not run; half-duplex stays until it is |
| **C-1** | Claude's first text and cost per turn from the robot's uplink | not run; cloud mode is not offered |

## Next, in order

1. **#402** (speech rules) merged and deployed.
2. **The persona eval set** (2.5 below), so a model or persona change cannot quietly bring back "I
   don't understand" or an offer of motion.

## Later: hardening

Kept, not scheduled.

| # | step |
|---|---|
| 2.1 | the `auto` brain (cloud while healthy, local on a timeout), after C-1 |
| 2.2 | pre-synthesised fallback lines, so they play even when TTS is the failing stage |
| 2.3 | fault codes in the troubleshooting registry (`VOICE_*`), needs DB-1 |
| 2.4 | a status endpoint (brain, gate, last latencies) for the dashboard |
| 2.5 | a persona eval set: 20–50 visitor questions with accepted answers, run on every model or persona change |
| 2.6 | dependencies locked with hashes |
| 2.7 | operator controls: voice on/off, mic state, the current transcript (TL-6) |
| 2.8 | the sign and the policy check for cloud mode |
| 2.9 | per-stage latency summarised per day |
| 3.x | full-duplex barge-in (after V-2), captions on the face, a voice glance toward the speaker |

---

## Later: a microphone array on the Thor — PARKED until it arrives

**Voice input in a crowd is a real problem** (the project team, 2026-10-24), parked until a
ReSpeaker XVF3800 USB microphone array is ordered and arrives. No work before then; wake stays off.
This is the plan for when it lands. Details marked *(check)* are from the vendor's description and
are confirmed on the bench before anything depends on them.

**A first result with a USB speakerphone, pending details** (2026-10-26). A team member's demo
branch runs a Jabra Speak 510 speakerphone (microphones, speaker and its own echo canceller in one
USB device) on the Thor, as the array would be. The same sherpa-onnx spotter and model run on the
Thor with gain ×4, Parakeet confirms every hit, and the robot's voice plays out of the speakerphone,
so its echo canceller has the playback to subtract. On two takes with one speaker: it **works at
1 m, catches nothing at 2 m**, and a new spoken instruction needs any music turned down. One lesson
for any spotter on the Thor: feed it every frame, since its decoder does not survive a gap. It may
be a cheaper first step than the array. Pending from the team member: where the speakerphone sat,
the gain and threshold tried at 2 m, and whether Talk does better on it than on the tablet.

**What it is.** A four-microphone circular array on an XMOS XVF3800 voice processor, appearing as a
USB audio device. On the board: echo cancellation, beamforming, noise suppression, automatic gain
and direction of arrival, configured over USB with the vendor's host tool *(check)*. The tablet's
mic today is one microphone behind Android's call-mode processing, and it moves with the neck.

**Where it mounts and how it connects**
- **On the body, not the head**: fixed to the front of the chassis at about chest height, facing
  where visitors stand. Kept still and away from the neck servos (which move the head) and from the
  drive, so neither motion nor servo noise reaches it. Its exact place is the user's call at the
  robot (head and clearance geometry always is).
- **USB to the Thor**, powered from the port. The Thor is on the logic bus, so the array stays on
  through an e-stop, as the voice service does. A by-id udev name (`/dev/snd/by-id/…`) for it, like
  the arms' CAN adapters, so a re-plug never changes which device is the mic.

**How capture reaches the voice service**
- **The Thor captures, not the tablet.** `run.sh` passes that one sound device into
  `opendubs-voice` (`--device`, the audio group; nothing else changes in the hardening), and a new
  `mic.py` reads 16 kHz mono, the array's processed beam channel, in the same 20 ms frames the page
  sends today. From there the pipeline is unchanged: Silero endpointing, Parakeet, the brain, TTS.
- **The page sends no audio.** Its `hello` declares the mic is the robot's; `press` and `release`
  still bracket a Talk turn, and the service reads the array between them. A short ring buffer on
  the Thor gives Talk its pre-roll, and it never leaves the Thor. That is a wire change in
  `wire.py`, agreed with the Tablet lane, which also drives the REC dot from the service's state
  rather than the page's own track.
- **The privacy rule's wording changes**: "no audio leaves the robot, and none is kept". The user
  decides it.
- **Wake, if reopened**: the same sherpa-onnx spotter runs natively on the Thor on the array's
  audio, with a larger model if one fits, then the same Parakeet check. The face only shows it.

**On-board settings** (set at service start from a file in the repo, never by hand on the device,
since config that changes behaviour is tracked)
- **Beamforming**: start with a fixed beam to the front, so a crowd at the sides is attenuated,
  rather than auto-steering, which follows the loudest talker. The direction of arrival is logged
  as a number per turn, never audio, and could later turn the eyes toward the speaker (3.6).
- **Echo cancellation** needs the robot's own voice as its reference, which it gets only if the
  voice plays out of the array (its USB playback or audio jack, to a speaker on the robot) *(check)*.
  While the voice plays from the tablet, echo cancellation has no reference and half-duplex
  covers it: no capture while the robot speaks. Moving the speaker is a separate decision for the
  user, and is what would make barge-in by voice possible later.
- **Noise suppression on, automatic gain on**, with a target level the turn log can check
  (`audio.peak_db` and `floor_db` per turn).

**The retest at distance** (the robot, a person present, no script to read)
- The user asks a few ordinary questions with Talk from a normal visitor distance, first in a quiet
  room and then with a crowd or show music around, and judges by ear whether the robot understood.
- The turn log gives the numbers behind it, with no words: level and floor, where speech started
  and ended, and whether a turn came back `unintelligible`. Compared with today's tablet mic in the
  same spot, that is the decision: keep the array, move it, or look at a lapel microphone instead.

**Steps when it arrives**: read it on the bench Thor and confirm the *(check)* items; `mic.py` and
the `run.sh` device; the wire change and the REC dot with the Tablet lane; mount it with the user;
the retest.

## Requests to other lanes

Finished requests are listed by PR; open ones stand as written before.

| # | lane | request | status |
|---|---|---|---|
| TL-1 to TL-4 | Tablet | mic capture, the `/ws/voice` client, playback, Talk and the REC dot | done, #365 |
| TL-9 to TL-11 | Tablet | wake-word spotting, the wake turn, the ring and the hit counter | done, #373, #379, #383, #400 |
| — | Tablet | Talk's pre-roll and tail; the neck holding still while listening | done, #380, #375, #382 |
| TL-5 | Tablet | `CONVO LISTEN\|THINK\|SPEAK [lease_ms]` in the head protocol, so the face shows the turn | open; the page learns the state from `/ws/voice` meanwhile |
| TL-6 | Tablet | operator controls (2.7) | later |
| TL-7 | Tablet | grant the mic to an in-app face origin, if the face moves into the app's assets | when needed |
| FH-1 to FH-3 | Face & HRI | listening, thinking and speaking looks; the REC dot's look; a "didn't catch that" reaction | open |
| TS-1, TS-2 | Thor | the image pins, and deployment of both containers | done, #362, #366 |
| TS-3 | Thor | the L-2 baseline | open |
| DB-1 | Docs | a `VOICE` prefix in `tools/faults.py` | later, with 2.3 |
| CH-1 | Choreography | confirm `GET /api/choreo/palette` → `running` covers an armed show | open, no change expected |

## Stub register — what is a stand-in today

| stub | where | replaced by |
|---|---|---|
| Talk turns use the energy gate for "was anything said" | `audio.py` | Silero tuned on the robot's room (step 1.4's second half) |
| the wake turn's timings (4 s, 0.35 s, 0.8 s) are estimates | `vad.py` | tuned from the turn log |
| `CONVO` not sent (`OPENDUBS_VOICE_CONVO=0`) | `face.py` | TL-5 |
| a generic persona | `persona.txt` | the team's own, with the eval set (2.5) |
| dependencies pinned by version, not hash | `image/requirements*.txt` | 2.6 |
| no status endpoint | `server.py` | 2.4 |
