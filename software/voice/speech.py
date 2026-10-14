"""Speech-to-text and text-to-speech backends, behind two small interfaces.

    STT:  await stt.transcribe(pcm16k) -> Transcript        one bounded push-to-talk clip
    TTS:  async for pcm24k in tts.speak(sentence): ...     PCM chunks as they are ready

Chosen by VOICE_STT / VOICE_TTS:

  fake      deterministic, no model: for tests and for a desk demo of the whole turn
  parakeet  Parakeet-TDT-0.6B-v2 (CC-BY-4.0), int8 ONNX, on sherpa-onnx (Apache-2.0)
  kokoro    Kokoro-82M (Apache-2.0), on kokoro + misaki (Apache-2.0), ENGLISH ONLY, no espeak-ng

STT and TTS stay on the robot in every brain mode: audio never leaves it.

**English only, no GPL** (the project team, 2026-10-14). Kokoro's other languages take their
pronunciation from espeak-ng, which is GPL-3.0; it is not installed, `no_espeak.py` keeps it out,
and VOICE_TTS_LANG accepts only `a` (American English) for now.

The weights are fetched at provision time by fetch_models.py into OPENDUBS_VOICE_MODELS
(/var/lib/opendubs/models on the host, mounted read-only at /models in the container), never baked
into an image. A backend whose files are missing refuses to start and says how to fetch them.
Nothing is downloaded at run time: Kokoro runs with HF_HUB_OFFLINE=1.

Not yet measured on a Thor (gate L-4): both run on the CPU by default; VOICE_STT_PROVIDER=cuda and
VOICE_TTS_DEVICE=cuda are there for when L-4 says the GPU is needed and the image has CUDA.
"""
from __future__ import annotations

import asyncio
import os
from array import array
from dataclasses import dataclass

import audio
import wire


class BackendUnavailable(RuntimeError):
    pass


@dataclass
class Transcript:
    text: str
    ms: float = 0.0


class FakeSTT:
    """Hears `text` in any clip with speech in it. VOICE_FAKE_TRANSCRIPT sets it for a desk demo."""

    def __init__(self, text: str | None = None, delay_s: float = 0.0):
        self.text = text if text is not None else os.environ.get("VOICE_FAKE_TRANSCRIPT", "hello robot")
        self.delay_s = delay_s

    async def transcribe(self, pcm: bytes) -> Transcript:
        await asyncio.sleep(self.delay_s)
        return Transcript(self.text if audio.has_speech(pcm, wire.UP_RATE) else "")


class FakeTTS:
    """A soft syllable-rate hum, about 60 ms per character, so a page's speaking envelope (and the
    mouth, when it is on) has something to follow. Real-time pacing is optional."""

    MS_PER_CHAR = 60

    def __init__(self, delay_s: float = 0.0, realtime: bool = False):
        self.delay_s = delay_s
        self.realtime = realtime
        self.spoken: list[str] = []

    async def speak(self, text: str):
        self.spoken.append(text)
        await asyncio.sleep(self.delay_s)
        ms = min(8000, max(200, self.MS_PER_CHAR * len(text)))
        pcm = audio.tone(180, ms, wire.DOWN_RATE, amp=0.15, am_hz=4.0)
        for c in audio.chunks(pcm, wire.DOWN_RATE, wire.DOWN_CHUNK_MS):
            yield c
            await asyncio.sleep(wire.DOWN_CHUNK_MS / 1000 if self.realtime else 0)


def models_dir() -> str:
    return os.environ.get("OPENDUBS_VOICE_MODELS", "/var/lib/opendubs/models")


def _need(paths: list[str], what: str) -> None:
    missing = [p for p in paths if not os.path.exists(p)]
    if missing:
        raise BackendUnavailable(f"{what}: missing {', '.join(missing)}. Fetch the pinned weights with "
                                 f"`python3 software/voice/fetch_models.py --dest {models_dir()}`")


def pcm_to_float(pcm: bytes) -> list[float]:
    """s16le PCM -> floats in [-1, 1), as sherpa-onnx takes them."""
    return [x / 32768.0 for x in audio.samples(pcm)]


def float_to_pcm(samples) -> bytes:
    """Floats in [-1, 1] (a list, a numpy array or a CPU tensor's .tolist()) -> s16le PCM, clipped."""
    a = array("h", (max(-32768, min(32767, int(round(float(x) * 32767)))) for x in samples))
    return audio.to_bytes(a)


class ParakeetSTT:
    """Parakeet-TDT-0.6B-v2 through sherpa-onnx's offline transducer: one bounded clip at a time,
    which is what push-to-talk gives it. `recognizer` is a seam for the tests."""

    DIR = "parakeet-tdt-0.6b-v2-int8"
    FILES = ("encoder.int8.onnx", "decoder.int8.onnx", "joiner.int8.onnx", "tokens.txt")

    def __init__(self, root: str | None = None, recognizer=None):
        if recognizer is None:
            d = os.path.join(root or models_dir(), self.DIR)
            paths = [os.path.join(d, f) for f in self.FILES]
            _need(paths, "VOICE_STT=parakeet")
            try:
                import sherpa_onnx
            except ImportError:
                raise BackendUnavailable("VOICE_STT=parakeet needs sherpa-onnx (image/requirements-speech.txt)") from None
            recognizer = sherpa_onnx.OfflineRecognizer.from_transducer(
                *paths, model_type="nemo_transducer", decoding_method="greedy_search",
                provider=os.environ.get("VOICE_STT_PROVIDER", "cpu"),
                num_threads=int(os.environ.get("VOICE_STT_THREADS", "4")))
        self.recognizer = recognizer

    def _decode(self, pcm: bytes) -> str:
        stream = self.recognizer.create_stream()
        stream.accept_waveform(wire.UP_RATE, pcm_to_float(pcm))
        self.recognizer.decode_stream(stream)
        return stream.result.text

    async def transcribe(self, pcm: bytes) -> Transcript:
        loop = asyncio.get_running_loop()
        t0 = loop.time()
        text = await loop.run_in_executor(None, self._decode, pcm)
        return Transcript(text.strip(), ms=(loop.time() - t0) * 1000)


class KokoroTTS:
    """Kokoro-82M through kokoro's own KPipeline, English only, with no espeak-ng. Each sentence is
    synthesised whole (Kokoro does not stream within a sentence), then sent in 100 ms chunks.
    `pipeline` is a seam for the tests: any callable (text, voice=, speed=) yielding results with
    an `.audio` of floats at 24 kHz."""

    DIR = "kokoro-82m"
    REPO = "hexgrad/Kokoro-82M"
    # American English only for now: British English (`b`) needs misaki's en_gb fallback model,
    # which models.lock.json does not pin yet. Every other language needs espeak-ng (GPL).
    ENGLISH = {"a": "American English"}

    def __init__(self, root: str | None = None, voice: str | None = None, lang: str | None = None,
                 pipeline=None):
        self.lang = lang or os.environ.get("VOICE_TTS_LANG", "a")
        if self.lang not in self.ENGLISH:
            import no_espeak
            raise BackendUnavailable(f"VOICE_TTS_LANG={self.lang!r}: only {', '.join(self.ENGLISH)} "
                                     f"is available. {no_espeak.REASON}")
        root = root or models_dir()
        name = voice or os.environ.get("VOICE_TTS_VOICE", "af_heart")
        if not name.replace("_", "").isalnum():
            raise BackendUnavailable(f"VOICE_TTS_VOICE={name!r} is not a voice name")
        d = os.path.join(root, self.DIR)
        self.voice = os.path.join(d, "voices", f"{name}.pt")
        if pipeline is None:
            config, weights = os.path.join(d, "config.json"), os.path.join(d, "kokoro-v1_0.pth")
            g2p = os.path.join(root, "hf", "hub", "models--PeterReid--graphemes_to_phonemes_en_us")
            _need([config, weights, self.voice, g2p], "VOICE_TTS=kokoro")
            os.environ.setdefault("HF_HUB_CACHE", os.path.join(root, "hf", "hub"))
            os.environ.setdefault("HF_HUB_OFFLINE", "1")       # never fetch at run time
            import no_espeak
            no_espeak.install()
            try:
                from kokoro import KModel, KPipeline
            except ImportError as e:
                raise BackendUnavailable(f"VOICE_TTS=kokoro needs kokoro and misaki "
                                         f"(image/requirements-speech*.txt): {e}") from None
            device = os.environ.get("VOICE_TTS_DEVICE", "cpu")
            model = KModel(repo_id=self.REPO, config=config, model=weights).to(device).eval()
            pipeline = KPipeline(lang_code=self.lang, repo_id=self.REPO, model=model, device=device)
        self.pipeline = pipeline
        self.spoken: list[str] = []

    def _synth(self, text: str) -> bytes:
        out = []
        for r in self.pipeline(text, voice=self.voice, speed=1.0):
            a = getattr(r, "audio", None)
            if a is None:
                continue
            out.append(float_to_pcm(a.tolist() if hasattr(a, "tolist") else a))
        return b"".join(out)

    async def speak(self, text: str):
        self.spoken.append(text)
        pcm = await asyncio.get_running_loop().run_in_executor(None, self._synth, text)
        for c in audio.chunks(pcm, wire.DOWN_RATE, wire.DOWN_CHUNK_MS):
            yield c


def _make(kind: str, name: str, choices: dict):
    if name not in choices:
        raise ValueError(f"VOICE_{kind}={name!r}: use {' or '.join(choices)}")
    return choices[name]()


def make_stt(name: str):
    return _make("STT", name, {"fake": FakeSTT, "parakeet": ParakeetSTT})


def make_tts(name: str):
    return _make("TTS", name, {"fake": FakeTTS, "kokoro": KokoroTTS})
