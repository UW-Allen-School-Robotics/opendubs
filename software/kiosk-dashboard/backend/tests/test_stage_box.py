"""The stage box's sound from the dashboard (stage_box.py): commands reach the box through its own report,
and nothing the audience would hear is sent while a show is loaded. No robot, no box."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from fastapi import FastAPI
from fastapi.testclient import TestClient

import stage
import stage_box


def _client(monkeypatch, show=None):
    monkeypatch.setattr(stage_box, "box", {"report": None, "at": None, "from": None})
    monkeypatch.setattr(stage_box, "pending", [])
    monkeypatch.setattr(stage.hub, "show", show)
    app = FastAPI()
    app.include_router(stage_box.router)
    return TestClient(app)


def test_a_command_rides_the_box_report_until_the_box_has_it(monkeypatch):
    c = _client(monkeypatch)
    assert c.post("/api/stage/box/command", json={"op": "test"}).json()["code"] == "LINK_STAGE_BOX_OFFLINE"
    assert c.post("/api/stage/box/report", json={"sound": {"actual": "usb"}}).json() == {"commands": []}
    cid = c.post("/api/stage/box/command", json={"op": "output", "value": "bt"}).json()["id"]
    assert c.post("/api/stage/box/report", json={}).json()["commands"] == [{"op": "output", "value": "bt", "id": cid}]
    assert c.post("/api/stage/box/report", json={"queued": [cid]}).json() == {"commands": []}
    assert c.get("/api/stage/box").json()["online"] is True


def test_during_a_show_only_the_volume_reaches_the_box(monkeypatch):
    c = _client(monkeypatch, show={"id": "s", "state": "playing"})
    c.post("/api/stage/box/report", json={})
    for cmd in ({"op": "test"}, {"op": "output", "value": "hdmi"}, {"op": "scan"}):
        r = c.post("/api/stage/box/command", json=cmd)
        assert r.status_code == 409 and r.json()["code"] == "SHOW_STAGE_SOUND_BUSY", cmd
    assert c.post("/api/stage/box/command", json={"op": "volume", "output": "usb", "value": 0.5}).json()["ok"]
