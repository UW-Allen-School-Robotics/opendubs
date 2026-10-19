#!/usr/bin/env python3
"""The real speech backends without the real models: the espeak-ng stand-in, Kokoro's English-only
rule and audio framing, Parakeet's decode call, and refusals when weights are missing. Fakes stand
in for sherpa-onnx and Kokoro's pipeline, so this needs neither installed.

    python3 software/voice/tests/test_speech.py
"""
import asyncio
import os
import sys
import tempfile
import types
import unittest
from types import SimpleNamespace as NS

import fakes  # noqa: F401  (puts the service on sys.path)

import audio
import no_espeak
import speech
import wire


class StandIn(unittest.TestCase):
    def setUp(self):
        self.saved = {k: sys.modules.get(k) for k in ("misaki", "misaki.espeak")}
        sys.modules.pop("misaki.espeak", None)

    def tearDown(self):
        for k, v in self.saved.items():
            if v is None:
                sys.modules.pop(k, None)
            else:
                sys.modules[k] = v

    def test_installs_a_refusing_espeak(self):
        m = no_espeak.install()
        self.assertIs(sys.modules["misaki.espeak"], m)
        for cls in (m.EspeakFallback, m.EspeakG2P):
            with self.assertRaises(RuntimeError) as e:
                cls(british=False)
            self.assertIn("GPL", str(e.exception))
        self.assertIs(no_espeak.install(), m)          # idempotent

    def test_refuses_when_gpl_code_is_installed(self):
        real = no_espeak.importlib.util.find_spec
        no_espeak.importlib.util.find_spec = lambda name: object() if name == "phonemizer" else None
        try:
            with self.assertRaises(no_espeak.GPLPresent):
                no_espeak.install()
        finally:
            no_espeak.importlib.util.find_spec = real

    def test_refuses_when_the_real_module_is_loaded(self):
        sys.modules["misaki.espeak"] = types.ModuleType("misaki.espeak")
        with self.assertRaises(no_espeak.GPLPresent):
            no_espeak.install()


class FakeResult:
    def __init__(self, n):
        self.audio = [0.5] * n


class Kokoro(unittest.IsolatedAsyncioTestCase):
    def test_english_only(self):
        for lang in ("b", "e", "f", "h", "i", "p", "j", "z", "xx"):
            with self.assertRaises(speech.BackendUnavailable, msg=lang) as e:
                speech.KokoroTTS(lang=lang, pipeline=lambda *a, **k: [])
            self.assertIn("GPL", str(e.exception))

    def test_missing_weights_say_how_to_fetch(self):
        with tempfile.TemporaryDirectory() as d:
            with self.assertRaises(speech.BackendUnavailable) as e:
                speech.KokoroTTS(root=d)
        self.assertIn("fetch_models.py", str(e.exception))
        self.assertIn("kokoro-v1_0.pth", str(e.exception))

    def test_voice_names_are_names(self):
        for bad in ("../x", "a/b", "af heart"):
            with self.assertRaises(speech.BackendUnavailable):
                speech.KokoroTTS(voice=bad, pipeline=lambda *a, **k: [])

    async def test_speaks_in_100_ms_chunks(self):
        calls = []

        def pipeline(text, voice, speed):
            calls.append((text, voice, speed))
            yield FakeResult(24000)                   # 1.0 s
            yield NS(audio=None)                      # a result with no audio is skipped
            yield FakeResult(6000)                    # 0.25 s
        tts = speech.KokoroTTS(root="/models", pipeline=pipeline)
        chunks = [c async for c in tts.speak("Hello there.")]
        self.assertEqual(calls, [("Hello there.", "/models/kokoro-82m/voices/af_heart.pt", 1.0)])
        self.assertEqual(sum(len(c) for c in chunks), 30000 * 2)
        self.assertEqual(len(chunks[0]), wire.DOWN_CHUNK_SAMPLES * 2)
        self.assertEqual(len(chunks), 13)
        self.assertEqual(audio.samples(chunks[0])[0], int(round(0.5 * 32767)))

    def test_float_to_pcm_clips(self):
        self.assertEqual(list(audio.samples(speech.float_to_pcm([0.0, 1.0, -1.0, 2.0, -2.0]))),
                         [0, 32767, -32767, 32767, -32768])


class FakeStream:
    def __init__(self):
        self.result = NS(text="")
        self.got = None

    def accept_waveform(self, rate, samples):
        self.got = (rate, samples)


class FakeRecognizer:
    def __init__(self):
        self.streams = []

    def create_stream(self):
        s = FakeStream()
        self.streams.append(s)
        return s

    def decode_stream(self, s):
        s.result.text = f"  heard {len(s.got[1])} samples at {s.got[0]} "


class Parakeet(unittest.IsolatedAsyncioTestCase):
    async def test_decodes_one_clip(self):
        stt = speech.ParakeetSTT(recognizer=FakeRecognizer())
        pcm = audio.tone(300, 500, wire.UP_RATE)
        tr = await stt.transcribe(pcm)
        self.assertEqual(tr.text, f"heard {len(pcm) // 2} samples at 16000")
        rate, samples = stt.recognizer.streams[0].got
        self.assertTrue(all(-1.0 <= x < 1.0 for x in samples))

    def test_missing_weights_say_how_to_fetch(self):
        with tempfile.TemporaryDirectory() as d:
            with self.assertRaises(speech.BackendUnavailable) as e:
                speech.ParakeetSTT(root=d)
        self.assertIn("encoder.int8.onnx", str(e.exception))
        self.assertIn("fetch_models.py", str(e.exception))

    def test_builds_the_nemo_transducer(self):
        seen = {}

        class OfflineRecognizer:
            @staticmethod
            def from_transducer(*paths, **kw):
                seen.update(paths=paths, kw=kw)
                return FakeRecognizer()
        sys.modules["sherpa_onnx"] = NS(OfflineRecognizer=OfflineRecognizer)
        try:
            with tempfile.TemporaryDirectory() as d:
                os.makedirs(os.path.join(d, speech.ParakeetSTT.DIR))
                for f in speech.ParakeetSTT.FILES:
                    open(os.path.join(d, speech.ParakeetSTT.DIR, f), "w").close()
                speech.ParakeetSTT(root=d)
        finally:
            del sys.modules["sherpa_onnx"]
        self.assertEqual([os.path.basename(p) for p in seen["paths"]], list(speech.ParakeetSTT.FILES))
        self.assertEqual(seen["kw"]["model_type"], "nemo_transducer")
        self.assertEqual(seen["kw"]["provider"], "cpu")


class Cache(unittest.TestCase):
    def test_every_cache_variable_points_at_the_pinned_weights(self):
        """The vLLM base sets TRANSFORMERS_CACHE and HUGGINGFACE_HUB_CACHE; a default would leave
        them, and misaki would miss its fallback model (found on the robot)."""
        saved = {k: os.environ.get(k) for k in speech.HF_CACHE_VARS + ("HF_HUB_OFFLINE",)}
        os.environ["TRANSFORMERS_CACHE"] = "/data/models/huggingface"
        try:
            speech.point_hf_cache("/models/hf/hub")
            for k in speech.HF_CACHE_VARS:
                self.assertEqual(os.environ[k], "/models/hf/hub", k)
            self.assertEqual(os.environ["HF_HUB_OFFLINE"], "1")
        finally:
            for k, v in saved.items():
                os.environ.pop(k, None) if v is None else os.environ.__setitem__(k, v)


class Choice(unittest.TestCase):
    def test_names(self):
        with self.assertRaises(ValueError):
            speech.make_tts("piper")
        with self.assertRaises(ValueError):
            speech.make_stt("whisper")


if __name__ == "__main__":
    unittest.main()
