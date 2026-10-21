"""The stage page's feed (stage.py): the relay's canonical frames reach the page, newest-only; anything
that is not a canonical skeleton frame never does; the page's latency report reaches /api/stage."""
import json
import os
import sys

from fastapi import FastAPI
from fastapi.testclient import TestClient

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import stage  # noqa: E402

app = FastAPI()
app.include_router(stage.router)


def frame(t, people):
    return json.dumps({"v": 1, "t": t, "frame": "skeleton_camera", "status": "live", "people": people})


def test_a_relay_frame_reaches_the_page_and_junk_never_does():
    stage.hub = stage.Hub()
    c = TestClient(app)
    with c.websocket_connect("/ws/skeleton") as page, c.websocket_connect("/ws/skeleton/in") as relay:
        relay.send_text("not json")
        relay.send_text(json.dumps({"image": "...", "v": 1}))        # not a skeleton frame: dropped
        relay.send_text(frame(1.0, [{"id": 3, "joints": {"head": [0, 1.6, 2, 0.9]}}]))
        got = json.loads(page.receive_text())
        assert got["people"][0]["id"] == 3 and got["t"] == 1.0
        page.send_json({"latency_ms": {"p50": 41, "p95": 70, "n": 30}})
        page.send_json({"ping": 1.0})
        pong = page.receive_json()
        assert pong["pong"] == 1.0 and pong["t1"] <= pong["t2"]
    v = c.get("/api/stage").json()
    assert v["latency_ms"] == {"p50": 41, "p95": 70, "n": 30} and v["feed"]["status"] == "live"


def test_a_slow_page_gets_the_newest_frame_not_a_queue():
    h = stage.Hub()

    class E:
        def __init__(self):
            self.n = 0

        def set(self):
            self.n += 1
    me = {"slot": None, "event": E()}
    h.pages[1] = me
    for t in range(5):
        h.publish(frame(float(t), []))
    assert json.loads(me["slot"])["t"] == 4.0                     # one slot, the newest
