"""A USB speakerphone through ALSA: 16 kHz capture and 24 kHz playback, by ctypes on libasound.

    cap = Capture(find_card("Jabra"))      # 20 ms frames of s16le mono at 16 kHz
    pcm = cap.read()
    out = Playback(find_card("Jabra"))     # s16le mono at 24 kHz, Kokoro's rate
    out.write(chunk); out.drain()

The device is opened as `plughw:N,0`, so ALSA's plug layer converts the rate and the channel count
when the device's own differ (the Jabra SPEAK 510 plays 48 kHz stereo; it captures 16 kHz mono,
exactly what the KWS and Parakeet take). No Python package and no alsa-utils: libasound is the
system's own (LGPL-2.1, linked dynamically and unmodified), and alsa-utils is GPL.

The card is found by its name at each open, because a USB card's number can change when it is
plugged in again; the Jabra's ALSA id is just "USB". The names come from /proc/asound/cards, or,
where Docker hides /proc/asound (in every container), from libasound itself through /dev/snd.
"""
from __future__ import annotations

import ctypes
import ctypes.util
import errno
import os
import re

CARDS = "/proc/asound/cards"
STREAM_PLAYBACK, STREAM_CAPTURE = 0, 1
FORMAT_S16_LE = 2
ACCESS_RW_INTERLEAVED = 3
CAPTURE_RATE = 16000              # the voice service's uplink rate (software/voice/wire.py)
FRAME_SAMPLES = CAPTURE_RATE // 50 # 20 ms
PLAY_RATE = 24000                 # Kokoro's
LATENCY_US = 100_000
_CARD_LINE = re.compile(r"^\s*(\d+)\s+\[([^\]]*)\]:\s*(.*)$")


class DeviceError(RuntimeError):
    pass


def _cards_from_lib() -> str:
    """The cards as /proc/asound/cards would list them, from libasound's control devices."""
    lib, card, lines = _asound(), ctypes.c_int(-1), []
    while lib.snd_card_next(ctypes.byref(card)) == 0 and card.value >= 0:
        name, long = ctypes.c_char_p(), ctypes.c_char_p()
        if lib.snd_card_get_name(card, ctypes.byref(name)) == 0 and \
                lib.snd_card_get_longname(card, ctypes.byref(long)) == 0:
            lines.append(f"{card.value} [{name.value.decode()}]: {long.value.decode()}")
    return "\n".join(lines)


def find_card(match: str, cards_text: str | None = None) -> str:
    """The plughw name of the first card whose id or description contains `match` (any case)."""
    if cards_text is None:
        try:
            with open(CARDS, encoding="utf-8") as f:
                cards_text = f.read()
        except OSError:
            cards_text = _cards_from_lib()
    for line in cards_text.splitlines():
        m = _CARD_LINE.match(line)
        if m and match.lower() in (m.group(2) + " " + m.group(3)).lower():
            return f"plughw:{m.group(1)},0"
    raise DeviceError(f"no sound card matching {match!r} (cards: {cards_text.strip() or 'none'})")


_lib = None


def _asound():
    global _lib
    if _lib is None:
        name = ctypes.util.find_library("asound") or "libasound.so.2"
        try:
            lib = ctypes.CDLL(name)
        except OSError as e:
            raise DeviceError(f"libasound is not installed ({e})") from None
        lib.snd_pcm_open.argtypes = [ctypes.POINTER(ctypes.c_void_p), ctypes.c_char_p, ctypes.c_int, ctypes.c_int]
        lib.snd_pcm_set_params.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_int, ctypes.c_uint,
                                           ctypes.c_uint, ctypes.c_int, ctypes.c_uint]
        lib.snd_pcm_readi.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_ulong]
        lib.snd_pcm_readi.restype = ctypes.c_long
        lib.snd_pcm_writei.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_ulong]
        lib.snd_pcm_writei.restype = ctypes.c_long
        lib.snd_pcm_recover.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_int]
        for f in ("snd_pcm_drain", "snd_pcm_close", "snd_pcm_prepare", "snd_pcm_start"):
            getattr(lib, f).argtypes = [ctypes.c_void_p]
        lib.snd_card_next.argtypes = [ctypes.POINTER(ctypes.c_int)]
        lib.snd_card_get_name.argtypes = lib.snd_card_get_longname.argtypes = [ctypes.c_int, ctypes.POINTER(ctypes.c_char_p)]
        lib.snd_strerror.argtypes = [ctypes.c_int]
        lib.snd_strerror.restype = ctypes.c_char_p
        _lib = lib
    return _lib


class _Pcm:
    def __init__(self, device: str, stream: int, rate: int, channels: int = 1):
        lib = _asound()
        self.device, self.rate, self.channels, self.stream = device, rate, channels, stream
        self.handle = ctypes.c_void_p()
        card = re.search(r":(\d+),", device)
        node = card and f"/dev/snd/pcmC{card.group(1)}D0{'c' if stream == STREAM_CAPTURE else 'p'}"
        if node and os.path.exists(node) and not os.access(node, os.R_OK | os.W_OK):
            # libasound reports this as "No such file or directory", which sends you looking for the card
            raise DeviceError(f"{device}: no permission on {node}; run in the audio group")
        self._check(lib.snd_pcm_open(ctypes.byref(self.handle), device.encode(), stream, 0), "open")
        rc = lib.snd_pcm_set_params(self.handle, FORMAT_S16_LE, ACCESS_RW_INTERLEAVED, channels, rate, 1, LATENCY_US)
        if rc < 0:
            lib.snd_pcm_close(self.handle)
            self._check(rc, f"set {rate} Hz")
        self._start()

    def _start(self) -> None:
        """Capture starts here: snd_pcm_set_params sets its start threshold to the whole buffer, which
        reads of one 20 ms frame never reach, so it would never start and each read ends in EIO."""
        if self.stream == STREAM_CAPTURE:
            _asound().snd_pcm_start(self.handle)

    def _check(self, rc: int, what: str) -> int:
        if rc < 0:
            raise DeviceError(f"{self.device}: {what}: {_asound().snd_strerror(rc).decode()}")
        return rc

    def _io(self, fn, buf, frames: int) -> int:
        """One readi/writei, recovering once from an xrun or a suspend. A lost device (ENODEV, the
        cable pulled) closes the device and raises, and the caller opens the card again by name."""
        n = fn(self.handle, buf, frames)
        if n < 0 and n != -errno.ENODEV:
            _asound().snd_pcm_recover(self.handle, n, 1)
            self._start()
            n = fn(self.handle, buf, frames)
        if n < 0:
            what = "read" if self.stream == STREAM_CAPTURE else "write"
            err = _asound().snd_strerror(n).decode()
            self.close()                          # else the reopen finds the device busy
            raise DeviceError(f"{self.device}: {what}: {err}")
        return n

    def close(self) -> None:
        if self.handle:
            _asound().snd_pcm_close(self.handle)
            self.handle = ctypes.c_void_p()


class Capture(_Pcm):
    """16 kHz mono, one 20 ms frame per read."""

    def __init__(self, device: str):
        super().__init__(device, STREAM_CAPTURE, CAPTURE_RATE)
        self._buf = ctypes.create_string_buffer(2 * FRAME_SAMPLES)

    def read(self) -> bytes:
        got = 0
        while got < FRAME_SAMPLES:
            ptr = ctypes.byref(self._buf, 2 * got)
            got += self._io(_asound().snd_pcm_readi, ptr, FRAME_SAMPLES - got)
        return self._buf.raw


class Playback(_Pcm):
    """24 kHz mono in, whatever the card plays out."""

    def __init__(self, device: str, rate: int = PLAY_RATE):
        super().__init__(device, STREAM_PLAYBACK, rate)

    def write(self, pcm: bytes) -> None:
        frames = len(pcm) // 2
        buf = ctypes.create_string_buffer(pcm[:2 * frames], 2 * frames)
        done = 0
        while done < frames:
            done += self._io(_asound().snd_pcm_writei, ctypes.byref(buf, 2 * done), frames - done)

    def drain(self) -> None:
        _asound().snd_pcm_drain(self.handle)
        _asound().snd_pcm_prepare(self.handle)
