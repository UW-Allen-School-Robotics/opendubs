# Voice service

Push-to-talk conversation for the robot: the face page's microphone in, the robot's voice out, and a
language model in between that can change **the face, and nothing else**.

**Status: phase 1, desk-tested only.** The wire protocol, turn state, brain router, safe tools,
the speech backends (Parakeet and Kokoro) and the container definition are here and tested with
fake audio and fake backends; the pinned speech packages are installed and smoke-tested in CI.
Nothing here has run on a Thor or a tablet, and the speech models' real latency is gate L-4. The
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
| `VOICE_STT` / `VOICE_TTS` | `parakeet` / `kokoro` in the image; `fake` for tests and a desk demo |
| `VOICE_LLM` | `local` (the vLLM container) or `fake` |
| `OPENDUBS_VOICE` | `0` keeps voice off without stopping the service |

In cloud and auto mode **text leaves the robot**: the transcript and the persona, never audio. Check
your institution's policy before turning it on in a public space.

## Speech: English only, no GPL

**STT:** Parakeet-TDT-0.6B-v2 (NVIDIA, CC BY 4.0), as the k2-fsa project's int8 ONNX conversion,
on sherpa-onnx (Apache-2.0). **TTS:** Kokoro-82M (Apache-2.0) on kokoro and misaki (Apache-2.0).
Both run on the CPU until gate L-4 says otherwise.

The project team decided on 2026-10-14 that the voice is **English only and ships no GPL code**.
Kokoro's default install brings in espeak-ng and phonemizer (both GPL-3.0-or-later) through
misaki's `[en]` extra, so:

- kokoro and misaki are installed with `--no-deps`, and `image/requirements-speech.txt` lists
  their other dependencies without the GPL ones;
- misaki is pinned to a commit of its main branch, because the 0.9.4 release has no fallback for
  words outside its dictionary without espeak-ng; that commit's own neural fallback (Apache-2.0)
  handles them instead;
- [`no_espeak.py`](no_espeak.py) stands in for misaki's espeak module, so kokoro imports without it
  and any language that needs espeak-ng is refused by name;
- [`image/check_no_gpl.py`](image/check_no_gpl.py) is the image build's last step and a CI job: an
  image or environment with a GPL package, an espeak-ng library or espeak-ng data fails.

Another language is a separate, explicit decision to ship GPL code.

**Weights are never in the repository or an image.** [`fetch_models.py`](fetch_models.py) fetches
exactly the files in [`models.lock.json`](models.lock.json) (each repository at a commit, each file
by SHA-256 or git blob id) into the robot's model directory at provision time, which `image/run.sh`
mounts read-only and checks before it starts the service. The service then runs with
`HF_HUB_OFFLINE=1`, so nothing is fetched at run time. Credits and licences:
[`NOTICE.md`](NOTICE.md), copied beside the weights.

## The local brain

**Qwen3-30B-A3B-Instruct-2507**, chosen by the project team (workplan V-O3), with
**Qwen3-4B-Instruct-2507** as the fallback if gate L-3 shows the 30B slows perception too much. It
runs in its own container (`opendubs-llm`, vLLM), so it can be swapped without touching speech.

- **Which build:** the 30B as Red Hat AI's 4-bit W4A16 quantisation (16.7 GB). The BF16 weights
  are about 61 GB and the FP8 build 31 GB, and neither fits the ≤ 24 GiB share the voice note
  budgets for it. Whether the 4-bit build's speed and quality hold is gate L-1.
- **One profile, both sides** ([`llm_profile.py`](llm_profile.py), from `models.lock.json`): the
  served name, vLLM's tool-call parser (`hermes`: the models' own chat template uses Hermes-style
  tool calls), an 8,192-token context, two sequences at once, and the sampling the model cards
  recommend (temperature 0.7, top-p 0.8, top-k 20). `image/run-llm.sh PROFILE` starts vLLM from it;
  the service reads the same profile (`VOICE_LLM_PROFILE`). The L-3 fallback is
  `run-llm.sh qwen3-4b` plus `VOICE_LLM_PROFILE=qwen3-4b`.
- **Pinned weights, served offline:** `fetch_models.py --only qwen3-30b-a3b-w4a16` fetches them,
  every file verified; `run-llm.sh` checks them again before it starts vLLM with `HF_HUB_OFFLINE=1`
  and the weights mounted read-only. vLLM never downloads anything itself.
- **Warm at start:** the service checks that the server answers *and* serves the profile's model
  (another model is "not ready", never used), then sends one short request with the real persona
  and tools, so the first visitor does not pay for a cold start and the persona's prefix is
  cached. Until then a turn gets the honest "I can't think right now".
- **History within the context:** a conversation keeps its last six questions.

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
`tests/smoke_speech_deps.py` needs the pinned speech packages installed (the CI job `speech-deps`
installs them as the image does).

## The container

`image/` follows the Thor stack's pinning rules: the base comes from `software/stack/versions.env`
by digest, and `build.sh` refuses a tag. It builds on `ROS_BASE_IMAGE` with CPU PyTorch; if gate
L-4 needs the GPU, `build.sh --gpu` builds on the Isaac ROS base with its PyTorch. Provision the
weights once per robot (`sudo mkdir -p /var/lib/opendubs/models`, then run `fetch_models.py` in the
image with that directory mounted; `run.sh` prints the exact command). `run-llm.sh` starts the local
model (vLLM) and refuses on a robot until the measurement gates have passed. Deployment through
`deploy-all.sh` belongs to the Thor stack lane.
