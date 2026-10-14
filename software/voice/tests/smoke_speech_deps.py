#!/usr/bin/env python3
"""The pinned speech packages, really installed: no GPL among them, Kokoro's English pipeline runs
with no espeak-ng, and an out-of-dictionary word still gets a pronunciation.

Not a unit test (the name says so, and the plain voice job does not run it): it needs the pinned
speech requirements installed exactly as the image installs them, and it downloads the 3 MB
pronunciation fallback and Kokoro's 2 KB config.json from their pinned commits. The CI job
`speech-deps` in .github/workflows/voice.yml does both. Kokoro's 327 MB weights and Parakeet's
660 MB are NOT downloaded: Kokoro runs on random stand-in weights (the code path, not the voice),
and Parakeet's API is checked by signature. Their real latency and quality are gate L-4.

    python3 software/voice/tests/smoke_speech_deps.py
"""
import asyncio
import inspect
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, ".."))
sys.path.insert(0, os.path.join(HERE, "..", "image"))

import check_no_gpl  # noqa: E402
import fetch_models  # noqa: E402


def main() -> int:
    assert check_no_gpl.main(["--no-system"]) == 0, "GPL code in the pinned speech environment"

    import sherpa_onnx
    params = inspect.signature(sherpa_onnx.OfflineRecognizer.from_transducer).parameters
    assert {"model_type", "provider", "num_threads", "decoding_method"} <= set(params), params
    print("sherpa-onnx: from_transducer takes model_type/provider/num_threads")

    root = tempfile.mkdtemp(prefix="voice-models-")
    lock = fetch_models.load_lock()
    kokoro = dict(next(m for m in lock if m["name"] == "kokoro"))
    kokoro["files"] = [f for f in kokoro["files"] if f["path"] == "config.json"]
    fetch_models.fetch(root, [kokoro, next(m for m in lock if m["name"] == "g2p-fallback")])

    import torch
    d = os.path.join(root, "kokoro-82m")
    os.makedirs(os.path.join(d, "voices"), exist_ok=True)
    torch.save({}, os.path.join(d, "kokoro-v1_0.pth"))                    # no weights: random init
    torch.save(torch.randn(510, 1, 256) * 0.1, os.path.join(d, "voices", "af_heart.pt"))

    os.environ.pop("HF_HUB_CACHE", None)
    os.environ["OPENDUBS_VOICE_MODELS"] = root
    import speech
    tts = speech.make_tts("kokoro")
    import importlib.util
    assert importlib.util.find_spec("phonemizer") is None
    fallback = type(tts.pipeline.g2p.fallback).__name__
    assert fallback == "FallbackNetwork", f"misaki's fallback is {fallback}, not the neural one"
    for r in tts.pipeline("Zorblatt lives in Wexmoor.", voice=tts.voice, speed=1.0):
        print("phonemes:", r.phonemes)
        assert "zˈɔɹ" in r.phonemes or "zɔɹ" in r.phonemes, "an unknown word lost its pronunciation"

    async def speak():
        return [c async for c in tts.speak("Hello.")]
    chunks = asyncio.run(speak())
    assert chunks and all(len(c) <= 4800 for c in chunks)
    print(f"kokoro: {len(chunks)} chunks of 24 kHz audio from stand-in weights, offline, no espeak-ng")
    print("smoke_speech_deps: OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
