#!/usr/bin/env python3
"""The audio round trip, tablet <-> Thor, measured before any speech model is involved.

    uv run --with fastapi --with "uvicorn[standard]" python3 tools/voice-bench/audio_lab.py --port 8450 \
        [--cert /etc/opendubs/tls/dashboard.crt --key /etc/opendubs/tls/dashboard.key]

NOT YET RUN ON THE TABLET OR A THOR. Written 2026-10-12 for the voice study
(notes/design/voice-llm-research.md, Q3 and experiments V-1..V-4); exercised only in a desktop
browser on a laptop. It is face-design.md's build-order step 0 ("prove the audio round trip ...
before any STT or model is involved") for the tablet head.

What the page (audio_lab.html, served at /) measures, each with its own verdict on screen:

  * UPLINK: the mic, captured with getUserMedia (echo cancellation, noise suppression and gain
    control ON, or OFF for comparison), turned into 16 kHz 16-bit mono in an AudioWorklet and sent
    as 20 ms frames (640 bytes) over a WebSocket. The server acks each frame at once, so the page
    sees the network round trip per frame and any frames that arrive late.
  * DOWNLINK: the server streams a synthetic "reply" (a tone sweep, 24 kHz 16-bit mono) in 100 ms
    chunks at real time, as streamed TTS would arrive; the page schedules them back to back and
    counts gaps.
  * ECHO: the page plays a noise burst through Web Audio (as TTS would play) and then through an
    <audio> element (as show music plays today, show_audio.js) and reads how much of it the mic
    hears, with the browser's echo cancellation on and off. The difference is how much the
    platform's canceller removes — the number that decides full- or half-duplex (study, Q3).

Privacy: the server keeps no audio unless --record DIR is given, and DIR must be outside the
repository. The page shows a red REC dot whenever its microphone track is live, driven by the
track itself, never by a message (the rule the study proposes for the face).

On the robot tablet the kiosk only grants the microphone to its provisioned origin, so point the
kiosk at this server for the test (ProvisionActivity `url=https://<thor>:8450/`) and back at the
dashboard afterwards. Use a free port; never reuse one another session started (CLAUDE.md lane rule 6).
"""
from __future__ import annotations

import argparse
import asyncio
import math
import os
import socket
import struct
import sys
import time
import wave

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.abspath(os.path.join(HERE, "..", ".."))
UP_RATE, DOWN_RATE = 16000, 24000
FRAME_SAMPLES = UP_RATE * 20 // 1000                 # 320 samples = 640 bytes per 20 ms frame
HEADER = struct.Struct("<II")                        # seq, page clock (ms, wraps)

app = FastAPI()
RECORD_DIR: str | None = None


@app.get("/")
async def page():
    return FileResponse(os.path.join(HERE, "audio_lab.html"))


def sweep_chunks(seconds: float = 3.0, chunk_ms: int = 100):
    """A 300 Hz -> 3 kHz sweep at -12 dBFS, as 16-bit mono chunks: a stand-in for streamed TTS."""
    n_chunk = DOWN_RATE * chunk_ms // 1000
    total = int(DOWN_RATE * seconds)
    phase, f0, f1 = 0.0, 300.0, 3000.0
    for start in range(0, total, n_chunk):
        out = bytearray()
        for i in range(start, min(start + n_chunk, total)):
            f = f0 * (f1 / f0) ** (i / total)
            phase += 2 * math.pi * f / DOWN_RATE
            out += struct.pack("<h", int(0.25 * 32767 * math.sin(phase)))
        yield bytes(out)


@app.websocket("/ws/audio")
async def audio(ws: WebSocket):
    await ws.accept()
    rec = None
    if RECORD_DIR:
        path = os.path.join(RECORD_DIR, time.strftime("uplink-%Y%m%d-%H%M%S.wav"))
        rec = wave.open(path, "wb")
        rec.setnchannels(1), rec.setsampwidth(2), rec.setframerate(UP_RATE)
        print(f"recording uplink to {path}", file=sys.stderr)
    frames = 0
    try:
        while True:
            m = await ws.receive()
            if m.get("type") == "websocket.disconnect":
                break
            if m.get("bytes"):
                b = m["bytes"]
                if len(b) != HEADER.size + FRAME_SAMPLES * 2:
                    await ws.send_json({"error": f"frame of {len(b)} bytes; expected {HEADER.size + FRAME_SAMPLES * 2}"})
                    continue
                seq, page_ms = HEADER.unpack_from(b)
                await ws.send_json({"ack": seq, "page_ms": page_ms})     # the page times the round trip
                frames += 1
                if rec:
                    rec.writeframes(b[HEADER.size:])
            elif m.get("text") == "reply":
                # Stream the stand-in reply at real time, one 100 ms chunk per 100 ms.
                t0 = time.monotonic()
                for i, chunk in enumerate(sweep_chunks()):
                    await ws.send_bytes(chunk)
                    await asyncio.sleep(max(0.0, t0 + (i + 1) * 0.1 - time.monotonic()))
                await ws.send_json({"reply_done": True, "rate": DOWN_RATE})
    except WebSocketDisconnect:
        pass
    finally:
        if rec:
            rec.close()
        print(f"page gone after {frames} uplink frames", file=sys.stderr)


def port_free(port: int) -> bool:
    with socket.socket() as s:
        return s.connect_ex(("127.0.0.1", port)) != 0


def main(argv=None) -> int:
    global RECORD_DIR
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--port", type=int, default=8450)
    ap.add_argument("--host", default="0.0.0.0")
    ap.add_argument("--cert"), ap.add_argument("--key")
    ap.add_argument("--record", metavar="DIR", help="save the uplink as WAV here (outside the repo)")
    a = ap.parse_args(argv)
    if not port_free(a.port):
        print(f"port {a.port} is in use -- another session's process? pick a free port", file=sys.stderr)
        return 2
    if a.record:
        d = os.path.realpath(a.record)
        if d == REPO or d.startswith(REPO + os.sep):
            print("refusing to record inside the repository (CLAUDE.md: downloads and recordings "
                  "never land in the working tree)", file=sys.stderr)
            return 2
        os.makedirs(d, exist_ok=True)
        RECORD_DIR = d
    import uvicorn
    uvicorn.run(app, host=a.host, port=a.port, ssl_certfile=a.cert, ssl_keyfile=a.key, log_level="warning")
    return 0


if __name__ == "__main__":
    sys.exit(main())
