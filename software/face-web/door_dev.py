#!/usr/bin/env python3
"""The face twin's door, on a laptop: what the tablet's kiosk app will do, for development.

    uv run --with websockets python3 software/face-web/door_dev.py

It serves the face page, carries head-protocol lines between UDP and the page (the page answers
them: tablet plan DT-10), and runs a reference NECK beside it so the face's GAZE and TRACK land
somewhere and the twin can show the head turning. Everything binds 127.0.0.1 only: a twin that
answered on the robot's network could be mistaken for the head.

  http://127.0.0.1:8765/?door=ws://127.0.0.1:8766&hud=1&state=SAFE_IDLE   the face
  udp 127.0.0.1:4244   the face half       udp 127.0.0.1:4245   the neck half (reference)

    python3 software/head-protocol/conformance.py 127.0.0.1:4244 --neck 127.0.0.1:4245 --active --motion

A bench tablet on USB reaches it through `adb reverse tcp:8765 tcp:8765` and `adb reverse
tcp:8766 tcp:8766`, which keeps it on localhost: a secure context, so the camera works.

With OPENDUBS_FACE_FRAMES naming a file (choreography's face.sh sets it), the page also sends its
picture, about 15 times a second and only when it changed, and this writes it there for the MuJoCo
body twin's head (frame_tap.py; the same file the Tab5 desktop face writes). Unset, nothing is sent.
"""
from __future__ import annotations

import argparse
import asyncio
import functools
import http.server
import itertools
import json
import os
import sys
import threading

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "head-protocol"))
import websockets  # noqa: E402
from fake_head import FakeHead  # noqa: E402
from frame_tap import FrameFile, W as TAP_W, H as TAP_H  # noqa: E402

pages: dict = {}                                     # connected pages, oldest first
waiting: dict = {}
ids = itertools.count(1)
FRAMES = os.environ.get("OPENDUBS_FACE_FRAMES")
tap = None                                           # the FrameFile, opened on the first frame


class FaceDoor(asyncio.DatagramProtocol):
    """UDP in, to the page; the page's reply, back out. PING with no page still answers."""

    def connection_made(self, transport):
        self.t = transport

    def datagram_received(self, data, addr):
        asyncio.ensure_future(self.carry(data, addr))

    async def carry(self, data, addr):
        line = data.split(b"\0", 1)[0].decode(errors="replace").strip()
        if not pages:
            reply = ("PONG kind=tablet-face engine=none mount=unknown mode=face face=absent"
                     if line == "PING" else "ERR face not showing")
        else:
            i = next(ids)
            fut = asyncio.get_running_loop().create_future()
            waiting[i] = fut
            await newest().send(json.dumps({"id": i, "line": line}))
            try:
                reply = await asyncio.wait_for(fut, 0.4)
            except asyncio.TimeoutError:
                reply = "ERR face did not answer"
            finally:
                waiting.pop(i, None)
        self.t.sendto(reply.encode(), addr)


def newest():
    """The page the door talks to: the most recently connected. After a reload that is the live one,
    and an old page's socket can outlive it for a while."""
    return next(reversed(pages), None)


async def page(ws):
    global tap
    pages[ws] = True
    if FRAMES:                                       # ask this page for its picture
        await ws.send(json.dumps({"frames": {"w": TAP_W, "h": TAP_H, "fps": 15}}))
    try:
        async for m in ws:
            if isinstance(m, bytes):                 # a frame: RGB, TAP_W x TAP_H, rows top to bottom
                # One picture, from the page the door talks to: two pages (the tablet and a laptop
                # browser) would otherwise interleave their frames in one file.
                if FRAMES and ws is newest():
                    tap = tap or FrameFile(FRAMES)
                    tap.write(m)
                continue
            msg = json.loads(m)
            fut = waiting.get(msg.get("id"))
            if fut and not fut.done():
                fut.set_result(msg.get("reply", "ERR"))
    finally:
        pages.pop(ws, None)


async def neck_feed(neck):
    """The neck's angle to the page, 20 Hz: the twin turns the face as the head would turn."""
    while True:
        n = neck.neck
        now = neck.clock()
        n.advance(now)
        msg = json.dumps({"neck": {"pan": n.pan, "tilt": n.tilt, "owner": n.owner(now)}})
        for ws in list(pages):
            try:
                await ws.send(msg)
            except Exception:
                pass
        await asyncio.sleep(0.05)


class NoCache(http.server.SimpleHTTPRequestHandler):
    """A development server: never cached, so an edit is what the tablet runs on the next load."""

    def end_headers(self):
        self.send_header("Cache-Control", "no-store")
        super().end_headers()

    def log_message(self, *a):
        pass


async def main(a):
    handler = functools.partial(NoCache, directory=HERE)
    httpd = http.server.ThreadingHTTPServer(("127.0.0.1", a.http), handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    neck = FakeHead("neck", port=a.neck).start()
    loop = asyncio.get_running_loop()
    await loop.create_datagram_endpoint(FaceDoor, local_addr=("127.0.0.1", a.face))
    async with websockets.serve(page, "127.0.0.1", a.ws):
        print(f"face page  http://127.0.0.1:{a.http}/?door=ws://127.0.0.1:{a.ws}&hud=1&state=SAFE_IDLE")
        print(f"face udp 127.0.0.1:{a.face}   neck udp 127.0.0.1:{neck.port} (reference)")
        if FRAMES:
            print(f"face frames -> {FRAMES} ({TAP_W}x{TAP_H}, for the body twin)")
        await neck_feed(neck)


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--http", type=int, default=8765)
    ap.add_argument("--ws", type=int, default=8766)
    ap.add_argument("--face", type=int, default=4244)
    ap.add_argument("--neck", type=int, default=4245)
    try:
        asyncio.run(main(ap.parse_args()))
    except KeyboardInterrupt:
        pass
