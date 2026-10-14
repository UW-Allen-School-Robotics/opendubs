"""/ws/voice: frames relayed unchanged both ways, a foreign page refused, a missing voice service
said truthfully, oversized frames refused, and a page that leaves closing its upstream."""
import asyncio
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

import voice_proxy


class FakeService:
    """The voice service, in memory: says `ready`, records what it is sent, echoes audio back."""
    instances: list = []

    def __init__(self):
        self.sent = []
        self.closed = False
        self.q = asyncio.Queue()
        self.q.put_nowait('{"type":"ready","protocol":1}')
        FakeService.instances.append(self)

    async def send(self, m):
        self.sent.append(m)
        if isinstance(m, bytes):
            await self.q.put(b"down:" + m)

    def __aiter__(self):
        return self

    async def __anext__(self):
        m = await self.q.get()
        if m is None:
            raise StopAsyncIteration
        return m

    async def close(self):
        self.closed = True
        self.q.put_nowait(None)


def _client(monkeypatch, up=True):
    FakeService.instances.clear()

    async def connect(url):
        if not up:
            raise ConnectionRefusedError(url)
        return FakeService()
    monkeypatch.setattr(voice_proxy, "_connect", connect)
    app = FastAPI()
    app.include_router(voice_proxy.router)
    return TestClient(app, base_url="http://10.42.2.10:8443")


def test_frames_pass_unchanged_both_ways(monkeypatch):
    with _client(monkeypatch) as c, c.websocket_connect(
            "/ws/voice", headers={"Origin": "https://10.42.2.10:8443", "Host": "10.42.2.10:8443"}) as ws:
        assert json.loads(ws.receive_text()) == {"type": "ready", "protocol": 1}
        ws.send_text('{"type":"press"}')
        frame = b"\x00\x00\x00\x00\x14\x00\x00\x00" + b"\x01\x00" * 320
        ws.send_bytes(frame)
        assert ws.receive_bytes() == b"down:" + frame
        svc = FakeService.instances[0]
        assert svc.sent == ['{"type":"press"}', frame]


def test_a_foreign_page_cannot_open_the_microphone_stream(monkeypatch):
    with _client(monkeypatch) as c:
        with pytest.raises(WebSocketDisconnect) as e:
            with c.websocket_connect("/ws/voice", headers={"Origin": "http://evil.example",
                                                          "Host": "10.42.2.10:8443"}):
                pass
        assert e.value.code == 1008
        assert FakeService.instances == []


def test_voice_service_down_is_said_and_closed(monkeypatch):
    with _client(monkeypatch, up=False) as c, c.websocket_connect("/ws/voice") as ws:
        m = json.loads(ws.receive_text())
        assert m["type"] == "error" and m["code"] == "VOICE_SERVICE_DOWN"
        with pytest.raises(WebSocketDisconnect) as e:
            ws.receive_text()
        assert e.value.code == 1013


def test_an_oversized_frame_closes_the_socket(monkeypatch):
    with _client(monkeypatch) as c, c.websocket_connect("/ws/voice") as ws:
        ws.receive_text()
        ws.send_bytes(b"\0" * (voice_proxy.MAX_FRAME + 1))
        with pytest.raises(WebSocketDisconnect) as e:
            ws.receive_text()
        assert e.value.code == 1009
    svc = FakeService.instances[0]
    assert svc.sent == [] and svc.closed


def test_the_page_leaving_closes_the_upstream(monkeypatch):
    with _client(monkeypatch) as c:
        with c.websocket_connect("/ws/voice") as ws:
            ws.receive_text()
        for _ in range(50):
            if FakeService.instances[0].closed:
                break
            import time
            time.sleep(0.01)
        assert FakeService.instances[0].closed


def test_the_service_leaving_closes_the_page(monkeypatch):
    with _client(monkeypatch) as c, c.websocket_connect("/ws/voice") as ws:
        ws.receive_text()
        svc = FakeService.instances[0]
        svc.q.put_nowait(None)                    # the service ends the stream
        with pytest.raises(WebSocketDisconnect):
            ws.receive_text()
