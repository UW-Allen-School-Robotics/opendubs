"""The demo runner's and the coordinator's state for the face (architect_proxy.py): what the ghost skin
reads, and that a service that is not there reads as "not there", never as a stale state."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from fastapi import FastAPI
from fastapi.testclient import TestClient

import architect_proxy as ap


def test_a_runner_that_is_not_answering_is_unavailable(monkeypatch):
    monkeypatch.setattr(ap, "_runner_state", lambda: None)
    app = FastAPI()
    app.include_router(ap.router)
    assert TestClient(app).get("/api/architect/state").json() == {"available": False}


def test_the_last_change_and_a_recent_refusal_are_worked_out_on_the_runners_clock():
    st = {"state": "ARMED", "since_s": 1.4, "runs": 3, "events": [
        {"t": 100.0, "state": "ARMED", "event": "run_started", "to": "RUNNING"},
        {"t": 108.0, "state": "RUNNING", "event": "run_ended", "to": "ARMED"},
        {"t": 108.5, "state": "ARMED", "event": "trigger_refused", "why": "not armed"},
        {"t": 108.6, "state": "ARMED", "event": "ignored", "kind": "speech"},
    ]}
    got = ap.summarise(st, now=110.0)
    assert got == {"available": True, "state": "ARMED", "since_s": 1.4, "runs": 3,
                   "last": "run_ended", "refused_s": 1.5}
    assert ap.summarise({"state": "IDLE", "events": []}, now=0)["last"] is None
    assert ap.summarise({"error": "?"}, now=0) == {"available": False}


def test_the_coordinator_reads_as_its_state_or_as_not_there(monkeypatch):
    monkeypatch.setattr(ap, "_day_state", lambda: None)
    app = FastAPI()
    app.include_router(ap.router)
    assert TestClient(app).get("/api/architect/day").json() == {"available": False}
    got = ap.summarise_day({"state": "CHOREO", "since_s": 2.0, "why": "a random pick", "show": "wave-and-nod",
                            "person": None, "nav": "stop", "random": ["wave-and-nod"]})
    assert got == {"available": True, "state": "CHOREO", "since_s": 2.0, "why": "a random pick",
                   "show": "wave-and-nod", "person": None, "nav": "stop"}
