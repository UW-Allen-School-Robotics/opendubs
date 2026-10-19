"""The /ws/voice wire format, between the face page and the voice service. One WebSocket per page.

The dashboard relays the socket unchanged (backend/voice_proxy.py), so this file is the whole
contract. Design: notes/design/voice-llm-research.md §5.3; plan: notes/workplan-voice.md.

BINARY FRAMES carry audio, little-endian throughout:

  up    (page -> service)  u32 seq | u32 page_ms | s16le PCM, 16 kHz, mono
                           One frame per 20 ms (320 samples, 648 bytes). Sent only while the Talk
                           button is held. seq counts from 0 per utterance; page_ms is the page's
                           own clock, for measuring the link (V-1), never for ordering.
  down  (service -> page)  u32 turn | u32 seq | s16le PCM, 24 kHz, mono
                           100 ms per chunk (2,400 samples). `turn` lets the page drop chunks of a
                           reply it has already cut (barge-in) without a race.

TEXT FRAMES carry control, one JSON object each, with a "type":

  page -> service   hello     {protocol, up: {rate, format, channels}}   first message, once
                    press     {}            Talk pressed: start an utterance (or cut the reply)
                    wake      {keyword, pre_roll_ms}   the tablet spotted the wake word: start an
                                            utterance. The frames that follow begin with the
                                            buffered audio around the keyword; pre_roll_ms of it
                                            came before the moment of detection. No `release`: the
                                            service ends the utterance (vad.py). Only if `ready`
                                            advertised `wake`.
                    release   {}            Talk released: the utterance is complete
                    barge_in  {}            stop the reply, without starting a new utterance
                    played    {turn}        the page finished playing that turn's audio
                    bye       {}

  service -> page   ready     {protocol, up, down, voice: {on, why}, max_utterance_s, wake?: {keyword}}
                    voice     {on, why}     voice became available or unavailable (a show, ...)
                    state     {turn, convo: idle|listen|think|speak}
                    heard     {turn, transcript}
                    turn      {turn, status, transcript, reply, ms: {...}}   the turn's outcome
                    tts_end   {turn, audio_ms}   no more audio for this turn
                    stop      {turn, why}   drop any queued audio for this turn now
                    conversation_end {why}   history cleared; the page may close the microphone
                    error     {code, message}

`turn.status` is one of TURN_STATUSES. A status is never inferred from an empty reply: "nothing
was said" (unintelligible) and "the model said nothing" (ok, reply "") are different answers.
"""
from __future__ import annotations

import json
import struct

PROTOCOL = 1

UP_RATE = 16000
UP_FRAME_MS = 20
UP_FRAME_SAMPLES = UP_RATE * UP_FRAME_MS // 1000          # 320
DOWN_RATE = 24000
DOWN_CHUNK_MS = 100
DOWN_CHUNK_SAMPLES = DOWN_RATE * DOWN_CHUNK_MS // 1000    # 2400
FORMAT = "s16le"

HEADER = struct.Struct("<II")
# A page may batch a few frames, but nothing legitimate is larger than this. Bigger is refused.
MAX_UP_PCM_BYTES = 8 * UP_FRAME_SAMPLES * 2
MAX_CONTROL_BYTES = 4096

PAGE_TYPES = frozenset({"hello", "press", "wake", "release", "barge_in", "played", "bye"})
MAX_PRE_ROLL_MS = 3000
SERVICE_TYPES = frozenset({"ready", "voice", "state", "heard", "turn", "tts_end", "stop",
                           "conversation_end", "error"})
CONVO_STATES = ("idle", "listen", "think", "speak")
TURN_STATUSES = frozenset({
    "ok",              # a reply was spoken (it may be empty, if the model chose silence)
    "unintelligible",  # no speech in the clip, or STT heard nothing: never sent to a model
    "declined",        # the model refused; a canned decline was spoken
    "busy",            # another page holds the conversation
    "off",             # voice is unavailable now (a show is running, or switched off)
    "timeout",         # the brain took too long; an apology was spoken
    "offline",         # no brain could answer
    "cut",             # barge-in, a show starting, or the page leaving
    "not_addressed",   # a wake turn that was not one: no wake phrase in the transcript, or nothing
                       # said after it. SILENT: nothing is spoken, and the page resumes listening
})


class WireError(ValueError):
    """A frame that breaks the contract. The service answers with an `error` and drops it."""


def parse_up(frame: bytes) -> tuple[int, int, bytes]:
    if len(frame) < HEADER.size:
        raise WireError(f"audio frame of {len(frame)} bytes has no header")
    pcm = frame[HEADER.size:]
    if len(pcm) % 2:
        raise WireError("audio frame has an odd number of PCM bytes")
    if len(pcm) > MAX_UP_PCM_BYTES:
        raise WireError(f"audio frame of {len(pcm)} PCM bytes is over {MAX_UP_PCM_BYTES}")
    seq, page_ms = HEADER.unpack_from(frame)
    return seq, page_ms, bytes(pcm)


def pack_up(seq: int, page_ms: int, pcm: bytes) -> bytes:
    return HEADER.pack(seq & 0xFFFFFFFF, page_ms & 0xFFFFFFFF) + pcm


def pack_down(turn: int, seq: int, pcm: bytes) -> bytes:
    return HEADER.pack(turn & 0xFFFFFFFF, seq & 0xFFFFFFFF) + pcm


def parse_down(frame: bytes) -> tuple[int, int, bytes]:
    turn, seq = HEADER.unpack_from(frame)
    return turn, seq, frame[HEADER.size:]


def parse_control(text: str) -> dict:
    if len(text) > MAX_CONTROL_BYTES:
        raise WireError("control message too long")
    try:
        msg = json.loads(text)
    except ValueError as e:
        raise WireError(f"control message is not JSON: {e}") from None
    if not isinstance(msg, dict) or msg.get("type") not in PAGE_TYPES:
        raise WireError(f"unknown control message; expected one of {', '.join(sorted(PAGE_TYPES))}")
    return msg


def check_hello(msg: dict) -> str | None:
    """None if the page's hello matches this service; otherwise why not."""
    if msg.get("protocol") != PROTOCOL:
        return f"protocol {msg.get('protocol')!r}; this service speaks {PROTOCOL}"
    up = msg.get("up") or {}
    want = {"rate": UP_RATE, "format": FORMAT, "channels": 1}
    if any(up.get(k) != v for k, v in want.items()):
        return f"uplink {up!r}; this service takes {want}"
    return None


def control(kind: str, **fields) -> str:
    assert kind in SERVICE_TYPES, kind
    return json.dumps({"type": kind, **fields}, separators=(",", ":"))


def check_wake(msg: dict) -> str | None:
    """None if a `wake` message is well formed; otherwise why not."""
    pre = msg.get("pre_roll_ms", 0)
    if not isinstance(pre, (int, float)) or isinstance(pre, bool) or not 0 <= pre <= MAX_PRE_ROLL_MS:
        return f"pre_roll_ms must be a number from 0 to {MAX_PRE_ROLL_MS}"
    if not isinstance(msg.get("keyword", ""), str):
        return "keyword must be a string"
    return None


def ready(voice_on: bool, why: str | None, max_utterance_s: float, wake: dict | None = None) -> str:
    extra = {"wake": wake} if wake else {}
    return control("ready", protocol=PROTOCOL,
                   up={"rate": UP_RATE, "format": FORMAT, "channels": 1, "frame_ms": UP_FRAME_MS},
                   down={"rate": DOWN_RATE, "format": FORMAT, "channels": 1, "chunk_ms": DOWN_CHUNK_MS},
                   voice={"on": voice_on, "why": why}, max_utterance_s=max_utterance_s, **extra)
