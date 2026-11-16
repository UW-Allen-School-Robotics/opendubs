"""The capture mission's panel (mission.py): START needs AUTO and the deadman; a silent runner says so."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import mission


def test_start_needs_auto_and_the_deadman_held():
    assert mission.start_refusal(None) == "the robot's state is unknown"
    assert mission.start_refusal({"ch5": "MANUAL", "inputs": {"deadman": True}}) == "switch CH5 to AUTO first"
    assert mission.start_refusal({"ch5": "AUTO", "inputs": {"deadman": False}}) == "hold the deadman (CH6) to start"
    assert mission.start_refusal({"ch5": "AUTO", "inputs": {"deadman": True}}) is None


def test_no_runner_refuses_every_press_and_hides_the_panel():
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    mission.last.update(state=None, at=None)
    app = FastAPI()
    app.include_router(mission.router)
    c = TestClient(app)
    assert c.get("/api/mission").json()["answering"] is False
    assert c.post("/api/mission/pause").status_code == 503
    mission.note({"data": '{"state": "running", "stop_n": 2, "stop_count": 5}'})
    assert c.get("/api/mission").json()["mission"]["stop_n"] == 2
    mission.last.update(state=None, at=None)
