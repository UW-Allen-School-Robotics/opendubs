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


def test_the_show_timing_reaches_every_page_including_a_late_one():
    stage.hub = stage.Hub()
    c = TestClient(app)
    with c.websocket_connect("/ws/skeleton") as page:
        r = c.post("/api/stage/show", json={"op": "load", "id": "s1", "beat": {"offset_s": 0.79, "bpm": 99.4},
                                            "videos": [{"t": 0, "end_t": 170, "url": "/api/choreo/media/library/videos/v.mp4",
                                                        "from_s": 0, "end": "black", "id": "v"}]})
        assert r.status_code == 200
        m = json.loads(page.receive_text())
        assert m["type"] == "show" and m["state"] == "loaded" and m["videos"][0]["id"] == "v"
        c.post("/api/stage/show", json={"op": "start", "id": "s1", "t0_mono": 1234.5})
        m = json.loads(page.receive_text())
        assert m["state"] == "playing" and m["t0_mono"] == 1234.5
    with c.websocket_connect("/ws/skeleton") as late:              # joins mid-show: told what plays, since when
        m = json.loads(late.receive_text())
        assert m["type"] == "show" and m["state"] == "playing" and m["t0_mono"] == 1234.5
    assert c.post("/api/stage/show", json={"op": "stop", "id": "other"}).status_code == 400   # not the loaded show
    assert c.post("/api/stage/show", json={"op": "stop", "id": "s1", "cut": True}).json()["ok"]
    assert c.get("/api/stage").json()["show"]["state"] == "stopped"


def test_a_page_reports_its_screen_sound_and_power_and_only_those():
    stage.hub = stage.Hub()
    c = TestClient(app)
    with c.websocket_connect("/ws/skeleton") as page:
        page.send_text(json.dumps({"page": {"device": "stage-pi", "audio_output": "usb", "under_voltage_since_boot": True,
                                            "screen_connected": True, "screen_mode": "3840x1080@60",
                                            "viewport": [3840, 1080], "dpr": 1, "clock_err_ms": 0.4, "sound": "on",
                                            "secret": "x", "under_voltage_now": "yes", "dpr_": True}}))
        page.send_text(json.dumps({"ping": 1.0}))       # a round trip, so the report has been handled
        page.receive_text()
        info = c.get("/api/stage").json()["pages_info"]
    assert len(info) == 1 and info[0]["audio_output"] == "usb" and info[0]["under_voltage_since_boot"] is True
    assert info[0]["viewport"] == [3840, 1080] and info[0]["screen_mode"] == "3840x1080@60"
    assert "secret" not in info[0] and "under_voltage_now" not in info[0]      # unknown, or the wrong type: dropped
    assert c.get("/api/stage").json()["pages_info"] == []                        # the page has gone


def test_each_pages_preload_progress_is_kept_for_the_runner():
    stage.hub = stage.Hub()
    c = TestClient(app)
    with c.websocket_connect("/ws/skeleton") as a, c.websocket_connect("/ws/skeleton") as b:
        a.send_text(json.dumps({"preload": {"id": "s1", "pct": 62, "ready": False}}))
        a.send_text(json.dumps({"ping": 1.0}))
        a.receive_text()
        b.send_text(json.dumps({"ping": 1.0}))
        b.receive_text()
        pre = c.get("/api/stage").json()["preload"]
    assert sorted(pre, key=lambda p: p is None) == [{"id": "s1", "ready": False, "pct": 62.0}, None]   # b: not reported
