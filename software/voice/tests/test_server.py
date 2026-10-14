#!/usr/bin/env python3
"""End to end over a real WebSocket on loopback (a free port), with the fake backends: what the
face page will see. Needs the `websockets` package, as the service does.

    python3 software/voice/tests/test_server.py
"""
import asyncio
import json
import os
import socket
import unittest

from fakes import FakeFace, FakeGate, speech_clip

import server
import wire
from brains import FakeLLM, Router
from conversation import Voice
from speech import FakeSTT, FakeTTS


class EndToEnd(unittest.IsolatedAsyncioTestCase):
    async def test_a_turn_over_the_socket(self):
        from websockets.asyncio.client import connect
        voice = Voice(FakeSTT("hello"), FakeTTS(), Router("local", local=FakeLLM(["Hello there."])),
                      FakeFace(), FakeGate(), "persona")
        voice.gate.run = lambda: asyncio.sleep(3600)
        ready = asyncio.get_running_loop().create_future()
        srv = asyncio.create_task(server.serve(voice, 0, ready))
        port = await asyncio.wait_for(ready, 5)
        texts, audio = [], []
        async with connect(f"ws://127.0.0.1:{port}") as ws:
            await ws.send(json.dumps({"type": "hello", "protocol": 1,
                                      "up": {"rate": 16000, "format": "s16le", "channels": 1}}))
            texts.append(json.loads(await ws.recv()))
            await ws.send('{"type":"press"}')
            clip = speech_clip()
            step = wire.UP_FRAME_SAMPLES * 2
            for i, off in enumerate(range(0, len(clip), step)):
                await ws.send(wire.pack_up(i, i * 20, clip[off:off + step]))
            await ws.send('{"type":"release"}')
            while True:
                m = await asyncio.wait_for(ws.recv(), 5)
                if isinstance(m, bytes):
                    audio.append(wire.parse_down(m))
                    continue
                m = json.loads(m)
                texts.append(m)
                if m["type"] == "tts_end":
                    await ws.send(json.dumps({"type": "played", "turn": m["turn"]}))
                if m["type"] == "state" and m["convo"] == "idle":
                    break
        srv.cancel()
        kinds = [t["type"] for t in texts]
        self.assertEqual(kinds[0], "ready")
        self.assertEqual([t["convo"] for t in texts if t["type"] == "state"], ["listen", "think", "speak", "idle"])
        self.assertIn("heard", kinds)
        self.assertEqual(next(t for t in texts if t["type"] == "turn")["reply"], "Hello there.")
        self.assertTrue(audio and all(a[0] == 1 for a in audio))

    async def test_a_busy_port_is_refused_not_shared(self):
        s = socket.socket()
        s.bind(("127.0.0.1", 0))
        s.listen()
        port = s.getsockname()[1]
        env = {"VOICE_STT": "fake", "VOICE_TTS": "fake", "VOICE_LLM": "fake", "VOICE_BRAIN": "local",
               "OPENDUBS_DASHBOARD_URL": "http://127.0.0.1:9"}     # never a real dashboard
        saved = {k: os.environ.get(k) for k in env}
        os.environ.update(env)
        try:
            code = await asyncio.get_running_loop().run_in_executor(None, server.main, ["--port", str(port)])
        finally:
            s.close()
            for k, v in saved.items():
                os.environ.pop(k) if v is None else os.environ.__setitem__(k, v)
        self.assertEqual(code, 3)

    def test_check_names_an_unpackaged_backend(self):
        os.environ["VOICE_STT"] = "parakeet"
        try:
            self.assertEqual(server.main(["--check"]), 2)
        finally:
            del os.environ["VOICE_STT"]


if __name__ == "__main__":
    unittest.main()
