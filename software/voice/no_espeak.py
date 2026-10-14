"""English only, no GPL: keep espeak-ng out of Kokoro's text-to-speech.

The project team decided (2026-10-14) that the voice is English only and that no GPL component
ships. Kokoro's own code (kokoro 0.9.4, `kokoro/pipeline.py`) imports `misaki.espeak` at the top,
and that module imports `phonemizer` (GPL-3.0+), which drives espeak-ng (GPL-3.0+). misaki's `[en]`
extra installs both, so the image installs kokoro and misaki with `--no-deps` and lists their other
dependencies itself (image/requirements-speech.txt).

That leaves `import kokoro` failing on the missing `phonemizer`. `install()` registers a stand-in
`misaki.espeak` whose classes refuse to construct. Kokoro already handles that: for English it
wraps `EspeakFallback()` in a try/except and passes `fallback=None` to misaki, which (at the pinned
misaki commit) then uses its own Apache-2.0 neural fallback for words not in its dictionary. Any
language that needs espeak-ng fails loudly instead of quietly pulling GPL code back in.

`install()` refuses if a GPL phonemizer or espeak loader is importable, or if the real
`misaki.espeak` is already loaded: then the image is not what this file promises, and image/
check_no_gpl.py should have stopped the build.
"""
from __future__ import annotations

import importlib.util
import sys
import types

REASON = ("espeak-ng is excluded from this robot (GPL-3.0); the voice is English only. Another "
          "language needs an explicit decision to ship GPL code (notes/workplan-voice.md, V-O4).")
GPL_MODULES = ("phonemizer", "espeakng_loader")


class GPLPresent(RuntimeError):
    pass


class _Refused:
    def __init__(self, *args, **kwargs):
        raise RuntimeError(REASON)


def install() -> types.ModuleType:
    found = [m for m in GPL_MODULES if importlib.util.find_spec(m) is not None]
    if found:
        raise GPLPresent(f"GPL module(s) installed: {', '.join(found)}. This image must not contain them.")
    current = sys.modules.get("misaki.espeak")
    if current is not None:
        if getattr(current, "OPENDUBS_STAND_IN", False):
            return current
        raise GPLPresent("the real misaki.espeak is already loaded")
    stand_in = types.ModuleType("misaki.espeak", REASON)
    stand_in.OPENDUBS_STAND_IN = True
    stand_in.EspeakFallback = type("EspeakFallback", (_Refused,), {})
    stand_in.EspeakG2P = type("EspeakG2P", (_Refused,), {})
    sys.modules["misaki.espeak"] = stand_in
    if "misaki" not in sys.modules and importlib.util.find_spec("misaki") is not None:
        import misaki  # noqa: F401  (only its __init__, which imports nothing else)
    if "misaki" in sys.modules:
        sys.modules["misaki"].espeak = stand_in
    return stand_in
