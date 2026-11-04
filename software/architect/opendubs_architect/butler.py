"""The butler: the listener's voice as a British man, Kokoro-82M's bm_george in British English.

    python3 -m opendubs_architect.butler fetch MODELS_DIR       # once, with the network (dev.sh butler)

The voice service (software/voice/speech.py, the Voice lane's) speaks American English only, with
the voices its models.lock.json pins. This keeps the same Kokoro weights and the same no-GPL rule,
and changes two things for the listener alone:

  the voice      bm_george.pt from the voice service's own Kokoro-82M revision (Apache-2.0)
  the accent     Kokoro's British English (`b`): misaki's British dictionary, which ships with it,
                 and for words not in it misaki's own British fallback, PeterReid/
                 graphemes_to_phonemes_en_gb (Apache-2.0), in place of espeak-ng (GPL), exactly as
                 the American voice uses the _en_us one. no_espeak.install() still refuses espeak.

The three files are pinned by sha256 and fetched once into MODELS_DIR/butler, never at run time
(the cache is pointed there and set offline). Spoken at SPEED, a touch slower than Kokoro's 1.0.
"""
from __future__ import annotations

import hashlib
import os
import sys
import urllib.request

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "voice")))

import speech  # noqa: E402  (software/voice, used and not changed)

DIR = "butler"
VOICE = "bm_george"
SPEED = 0.95
KOKORO = ("hexgrad/Kokoro-82M", "f3ff3571791e39611d31c381e3a41a3af07b4987")      # the voice service's pin
G2P = ("PeterReid/graphemes_to_phonemes_en_gb", "d8357d5067fa26a5c34134d6bbcf4bbf000c0ac8")
FILES = {   # (repo, revision, path in the repo) -> sha256
    (*KOKORO, f"voices/{VOICE}.pt"): "f1bc812213dc59774769e5c80004b13eeb79bd78130b11b2d7f934542dab811b",
    (*G2P, "config.json"): "e4f248e6af0cfb6cb54aea6ff0168d16b6f3cdabed438c487cde4ab0815d1bac",
    (*G2P, "generation_config.json"): "79a11240b01c6d34cab0b122b63a2b2dd5938c4d37d1530e14144d27e8ba6272",
    (*G2P, "model.safetensors"): "4994f474bb6f4584076a4e98189caaec11aa3f773a0d82d5bc263a03dd07e703",
}


def _hub(root: str) -> str:
    return os.path.join(root, DIR, "hf", "hub")


def _local(root: str, repo: str, rev: str, path: str) -> str:
    """Where a pinned file lives: Hugging Face's own cache layout, so misaki finds its fallback offline."""
    return os.path.join(_hub(root), "models--" + repo.replace("/", "--"), "snapshots", rev, path)


def _sha(path: str) -> str:
    with open(path, "rb") as f:
        return hashlib.sha256(f.read()).hexdigest()


def verify(root: str) -> None:
    for (repo, rev, path), want in FILES.items():
        p = _local(root, repo, rev, path)
        if not os.path.isfile(p):
            raise speech.BackendUnavailable(f"the butler's voice is not fetched ({p}); "
                                            f"run: python3 -m opendubs_architect.butler fetch {root}")
        if _sha(p) != want:
            raise speech.BackendUnavailable(f"{p} does not match its pin")


def fetch(root: str) -> str:
    for (repo, rev, path), want in FILES.items():
        p = _local(root, repo, rev, path)
        if os.path.isfile(p) and _sha(p) == want:
            continue
        with urllib.request.urlopen(f"https://huggingface.co/{repo}/resolve/{rev}/{path}", timeout=60) as r:
            blob = r.read()
        if hashlib.sha256(blob).hexdigest() != want:
            raise RuntimeError(f"{repo}/{path} at {rev[:12]} does not match its pin")
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p + ".part", "wb") as f:
            f.write(blob)
        os.replace(p + ".part", p)
    for repo, rev in (KOKORO, G2P):             # offline, the cache resolves "main" through refs/main
        refs = os.path.join(_hub(root), "models--" + repo.replace("/", "--"), "refs")
        os.makedirs(refs, exist_ok=True)
        with open(os.path.join(refs, "main"), "w") as f:
            f.write(rev)
    verify(root)
    return os.path.join(root, DIR)


class ButlerTTS(speech.KokoroTTS):
    """speech.KokoroTTS with the butler's voice, accent and pace. `root` holds the fetched files;
    the Kokoro weights are the voice service's (OPENDUBS_VOICE_MODELS). `pipeline` is the same seam."""

    def __init__(self, root: str, pipeline=None):
        self.lang = "b"
        self.voice = _local(root, *KOKORO, f"voices/{VOICE}.pt")
        if pipeline is None:
            verify(root)
            d = os.path.join(speech.models_dir(), self.DIR)
            config, weights = os.path.join(d, "config.json"), os.path.join(d, "kokoro-v1_0.pth")
            speech._need([config, weights], "the butler's voice")
            speech.point_hf_cache(_hub(root))   # before transformers is imported: the British fallback only
            import no_espeak
            no_espeak.install()
            from kokoro import KModel, KPipeline
            device = os.environ.get("VOICE_TTS_DEVICE", "cpu")
            if device.startswith("cuda"):
                speech.cap_gpu_memory()
            model = KModel(repo_id=self.REPO, config=config, model=weights).to(device).eval()
            self.pipeline, self.spoken = KPipeline(lang_code="b", repo_id=self.REPO, model=model, device=device), []
            self._synth("Good evening.")        # the first synthesis is the slow one: pay it now
        else:
            self.pipeline = pipeline
        self.spoken: list[str] = []

    def _synth(self, text: str) -> bytes:
        out = []
        for r in self.pipeline(text, voice=self.voice, speed=SPEED):
            a = getattr(r, "audio", None)
            if a is not None:
                out.append(speech.float_to_pcm(a.tolist() if hasattr(a, "tolist") else a))
        return b"".join(out)


if __name__ == "__main__":
    if len(sys.argv) == 3 and sys.argv[1] == "fetch":
        print(fetch(sys.argv[2]))
    else:
        sys.exit("usage: python3 -m opendubs_architect.butler fetch MODELS_DIR")
