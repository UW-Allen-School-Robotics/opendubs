# Voice service

Push-to-talk conversation for the robot: the face page's microphone in, the robot's voice out, and a
language model in between that can change **the face, and nothing else**.

**Status: phase 1 skeleton, desk-tested only.** The wire protocol, turn state, brain router, safe
tools and container definition are here and tested with fake audio and fake backends. The speech
models (Parakeet, Kokoro) are not packaged yet, and nothing here has run on a Thor or a tablet. The
plan, with the measurements that gate each step, is [`notes/workplan-voice.md`](../../notes/workplan-voice.md);
the research behind it is `notes/design/voice-llm-research.md`.

## How a turn flows

```
tablet face page                dashboard (host)            opendubs-voice (container)        opendubs-llm
 Talk held ── 20 ms PCM ──▶ wss /ws/voice ── relay ──▶ ws 127.0.0.1:8093 ─▶ STT ─▶ brain ──HTTP──▶ vLLM :8000
                                (origin guard)                                │      └──(opt-in)──▶ Claude API
 plays PCM ◀── 100 ms chunks ◀──────────────────────────────────── TTS ◀──────┘
 face  ◀── EMOTE / MOVE (and CONVO, once agreed) ── UDP 127.0.0.1:4242 face door ◀── safe tools
```

1. **press**: the service enters `listen` and takes 16 kHz PCM frames until **release** (or 12 s).
2. **think** is sent at release, before anything else runs, so the face never waits on the pipeline.
3. A clip with no speech in it never reaches a model: the robot says "say that again".
4. STT, then the brain, streamed. Each sentence goes to TTS as soon as it is complete; the first
   audio chunk moves the state to **speak**.
5. A press during think or speak cuts the reply (button barge-in) and starts a new utterance.
6. After the page reports `played`, the state returns to **idle**. History is kept for a 20 s
   conversation window, then cleared. No audio is ever stored.

The full message list is in [`wire.py`](wire.py), which is the contract with the face page.

## The safety boundary

[`safe_tools.py`](safe_tools.py): a model may set an expression, play a face gesture, read the
robot's status (read-only) and end the conversation. **There is no motion tool to enable**: the
project team decided that motion is never commanded by a language model and that routines by voice
stay off. The tests enumerate every effect the module can produce and fail on anything else, and
[`face.py`](face.py) refuses any head-protocol verb outside `EMOTE`, `MOVE` and `CONVO` before it
sends.

**Voice is off during shows** ([`gate.py`](gate.py)): while the show runner reports a routine
running, presses are answered `off` and a turn in flight is cut. If the dashboard cannot be read,
voice is off.

## Configuration

All from the environment; see the docstring of [`server.py`](server.py) for the full list. On a
robot the values live in `/etc/opendubs/voice.env`, owned by root, mode 0600. That is the only
place the cloud brain's API key may live: never the repository, the image, or the browser.
`image/run.sh` refuses to start if the file is readable by anyone but root.

| setting | values |
|---|---|
| `VOICE_BRAIN` | `local` (default), `cloud`, `auto` (cloud while healthy, local otherwise) |
| `VOICE_STT` / `VOICE_TTS` | `fake` today; `parakeet` / `kokoro` once packaged |
| `VOICE_LLM` | `local` (the vLLM container) or `fake` |
| `OPENDUBS_VOICE` | `0` keeps voice off without stopping the service |

In cloud and auto mode **text leaves the robot**: the transcript and the persona, never audio. Check
your institution's policy before turning it on in a public space.

## Run it on a desk

No robot, tablet, model or key needed: fake speech and a canned brain.

```bash
pip install websockets
VOICE_STT=fake VOICE_TTS=fake VOICE_LLM=fake OPENDUBS_DASHBOARD_URL=http://127.0.0.1:9 \
  python3 software/voice/server.py --port 18093
```

With no dashboard at that address the show interlock fails closed, so presses are answered `off`.
That is the intended behaviour; point `OPENDUBS_DASHBOARD_URL` at a dashboard you started yourself
to exercise a full turn. **Never point it at a dashboard or a face door another session or person
started.**

## Tests

As CI runs them (`.github/workflows/voice.yml`):

```bash
pip install websockets
python3 software/voice/tests/test_conversation.py
```

and likewise for each `tests/test_*.py`. They use fake audio (tone bursts on noise, silence, a
steady hum) and fake backends, and open sockets only on free loopback ports of their own.

## The container

`image/` follows the Thor stack's pinning rules: the base comes from `software/stack/versions.env`
by digest, and `build.sh` refuses a tag. Phase 1 is CPU-only and builds on `ROS_BASE_IMAGE`; with
the speech models it moves to the Isaac ROS base (`build.sh --gpu`). `run-llm.sh` starts the local
model (vLLM) and refuses on a robot until the measurement gates have passed. Deployment through
`deploy-all.sh` belongs to the Thor stack lane.
