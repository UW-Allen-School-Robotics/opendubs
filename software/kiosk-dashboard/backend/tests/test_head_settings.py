"""The head's idle settings: kept on the robot, checked, and pushed to the face pages at once."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from fastapi import FastAPI
from fastapi.testclient import TestClient

import face_door
import head_settings


def _client(tmp_path, monkeypatch):
    monkeypatch.setattr(head_settings, "PATH", str(tmp_path / "head-settings.json"))
    face_door.pages.clear()
    app = FastAPI()
    app.include_router(head_settings.router)
    app.include_router(face_door.router)
    return TestClient(app)


def test_defaults_until_something_is_set(tmp_path, monkeypatch):
    s = _client(tmp_path, monkeypatch).get("/api/head/settings").json()
    assert s == head_settings.DEFAULTS
    assert s["mouth"] is False and s["bright_min"] == 0.15 and s["volume_idle"] is None


def test_a_change_is_partial_and_survives_a_restart(tmp_path, monkeypatch):
    c = _client(tmp_path, monkeypatch)
    r = c.put("/api/head/settings", json={"skin": "ring", "mouth": True})
    assert r.status_code == 200 and r.json()["skin"] == "ring" and r.json()["theme"] == "plain"
    assert head_settings.load()["mouth"] is True          # read back from the file, as after a reboot


def test_bad_values_are_refused_and_nothing_is_written(tmp_path, monkeypatch):
    c = _client(tmp_path, monkeypatch)
    for bad in ({"bright_min": 0.01}, {"vol_max": 2}, {"tracking": "sonar"}, {"mouth": "yes"},
                {"nonsense": 1}, {"screen_on": 1}):
        assert c.put("/api/head/settings", json=bad).status_code == 400, bad
    assert not os.path.exists(head_settings.PATH)
    assert c.put("/api/head/settings", content=b"not json").status_code == 400


def test_the_idle_levels_respect_the_limits(tmp_path, monkeypatch):
    c = _client(tmp_path, monkeypatch)
    s = c.put("/api/head/settings", json={"bright_idle": 0.1, "bright_min": 0.3}).json()
    assert s["bright_idle"] == 0.3                         # never idle below the floor
    s = c.put("/api/head/settings", json={"volume_idle": 0.9, "vol_max": 0.5}).json()
    assert s["volume_idle"] == 0.5


def test_a_corrupt_file_falls_back_to_the_defaults(tmp_path, monkeypatch):
    _client(tmp_path, monkeypatch)
    with open(head_settings.PATH, "w") as fh:
        fh.write("{ not json")
    assert head_settings.load() == head_settings.DEFAULTS


def test_a_face_page_gets_them_on_connect_and_on_change(tmp_path, monkeypatch):
    c = _client(tmp_path, monkeypatch)
    with c.websocket_connect("/ws/face") as page:
        assert page.receive_json()["settings"]["skin"] == "default"
        c.put("/api/head/settings", json={"skin": "ring"})
        assert page.receive_json()["settings"]["skin"] == "ring"
