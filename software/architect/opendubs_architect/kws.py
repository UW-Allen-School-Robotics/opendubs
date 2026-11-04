"""Keyword spotting on the Thor: sherpa-onnx's KeywordSpotter on the speakerphone's 16 kHz audio.

The model is the face page's (software/face-web/kws/build-kws.sh), sherpa-onnx-kws-zipformer-
gigaspeech-3.3M (Apache-2.0), int8, pinned by the same tarball sha256. It is never in the
repository or an image: fetch it once per robot, into the voice service's model directory,

    sudo python3 -m opendubs_architect.kws fetch /var/lib/opendubs/models

which verifies the tarball and each file it keeps. The spotter checks the files again at start.

KEYWORDS are the model's own BPE pieces of each phrase (its bpe.model, by sentencepiece), with a
boost (`:`) and a threshold (`#`). OPENDUBS is the wake phrase, "OpenDubs" alone (2026-10-26; it was
"Hey OpenDubs", the face page's line, whose pieces it keeps without the "HE Y": the boost and threshold
are that line's, untuned for the shorter phrase, which the voice workplan found harder to spot at a
distance when it was "hey dubs"). TRICK_OR_TREAT was chosen
on the first Jabra take (2026-10-24, one speaker at 1, 2 and 3 m in a quiet room): at 1 m it caught
3 of 3 "Hey OpenDubs" and 2 of 3 "trick or treat", at 3 m one "trick or treat", and at 2 m nothing,
at any setting tried. A hit is only a candidate, which Parakeet confirms before anything is posted.

GAIN: the spotter is level-sensitive (the face page found the same, #413), and the Jabra delivers
speech at 1 m peaking around -20 dBFS. x4 caught the one "Hey OpenDubs" the take's x1 missed.
Only the spotter's copy is amplified; Parakeet gets the audio as captured.
"""
from __future__ import annotations

import hashlib
import io
import os
import sys
import tarfile
import urllib.request

MODEL = "sherpa-onnx-kws-zipformer-gigaspeech-3.3M-2024-01-01"
MODEL_URL = f"https://github.com/k2-fsa/sherpa-onnx/releases/download/kws-models/{MODEL}.tar.bz2"
MODEL_SHA256 = "f170013b4716e41b62b9bfd809687c207cef798ef9bc6534d524e17af9b6561a"   # build-kws.sh's pin
DIR = "kws-gigaspeech-3.3m"
_STEM = "epoch-12-avg-2-chunk-16-left-64.int8.onnx"
FILES = {   # kept from the tarball: name -> sha256
    f"encoder-{_STEM}": "1e721676515bcd42a186979733981213c66c80db680e1cc582dfedf3be76e678",
    f"decoder-{_STEM}": "e40ff43297abe815e8898494c17e71bba2152d9d40fa3eb803f75d0f7533329a",
    f"joiner-{_STEM}": "eae9da0c7e1e6c6a3f4cc42d167899c388f6c6701b94cb96320e4f55df79624c",
    "tokens.txt": "fd2ded4050a55d2b1578870ba8697d02371980217806b7558bd0a5cc60f3ba53",   # gitleaks:allow (a sha256 pin, not a token)
}

WAKE, TRICK = "OPENDUBS", "TRICK_OR_TREAT"
KEYWORDS = "\n".join([
    "▁O P EN ▁DU B S :4.0 #0.02 @OPENDUBS",
    "▁T RI CK ▁OR ▁T RE AT :3.0 #0.02 @TRICK_OR_TREAT",
])
RATE = 16000
GAIN = 4.0


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def verify(root: str) -> str:
    """The model directory, if every file is there and matches its pin; else RuntimeError."""
    d = os.path.join(root, DIR)
    for name, want in FILES.items():
        p = os.path.join(d, name)
        try:
            with open(p, "rb") as f:
                got = _sha(f.read())
        except OSError:
            raise RuntimeError(f"{p} is missing; fetch it: python3 -m opendubs_architect.kws fetch {root}") from None
        if got != want:
            raise RuntimeError(f"{p} does not match its pin ({got[:12]} != {want[:12]})")
    return d


def fetch(root: str) -> str:
    with urllib.request.urlopen(MODEL_URL, timeout=60) as r:
        blob = r.read()
    if _sha(blob) != MODEL_SHA256:
        raise RuntimeError(f"{MODEL_URL} does not match its pin")
    d = os.path.join(root, DIR)
    os.makedirs(d, exist_ok=True)
    with tarfile.open(fileobj=io.BytesIO(blob), mode="r:bz2") as tar:
        for name, want in FILES.items():
            data = tar.extractfile(f"{MODEL}/{name}").read()
            if _sha(data) != want:
                raise RuntimeError(f"{name} in the tarball does not match its pin")
            with open(os.path.join(d, name), "wb") as f:
                f.write(data)
    return verify(root)


class Spotter:
    """feed(20 ms of s16le) -> the keywords that fired on it, usually none. `spotter` is a seam."""

    def __init__(self, root: str, keywords: str = KEYWORDS, threads: int = 1, gain: float = GAIN, spotter=None):
        if spotter is None:
            import sherpa_onnx
            d = verify(root)
            kw = os.path.join(os.environ.get("TMPDIR", "/tmp"), "opendubs-keywords.txt")
            with open(kw, "w", encoding="utf-8") as f:
                f.write(keywords.strip() + "\n")
            spotter = sherpa_onnx.KeywordSpotter(
                tokens=os.path.join(d, "tokens.txt"), encoder=os.path.join(d, f"encoder-{_STEM}"),
                decoder=os.path.join(d, f"decoder-{_STEM}"), joiner=os.path.join(d, f"joiner-{_STEM}"),
                keywords_file=kw, num_threads=threads)
        self.kws, self.gain = spotter, gain
        self.stream = spotter.create_stream()

    def feed(self, pcm: bytes) -> list[str]:
        import array
        a = array.array("h")
        a.frombytes(pcm)
        g = self.gain / 32768.0
        self.stream.accept_waveform(RATE, [max(-1.0, min(1.0, x * g)) for x in a])
        hits = []
        while self.kws.is_ready(self.stream):
            self.kws.decode_stream(self.stream)
            r = self.kws.get_result(self.stream)
            if r:
                hits.append(r)
                self.kws.reset_stream(self.stream)
        return hits

    def reset(self) -> None:
        """Forget a partial phrase: after the robot has spoken, the spotter starts clean."""
        self.stream = self.kws.create_stream()


if __name__ == "__main__":
    if len(sys.argv) == 3 and sys.argv[1] == "fetch":
        print(fetch(sys.argv[2]))
    else:
        sys.exit("usage: python3 -m opendubs_architect.kws fetch MODELS_DIR")
