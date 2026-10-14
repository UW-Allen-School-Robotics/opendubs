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
    assert s.pop("neck")                                   # read-only: the robot's neck, or "none"
    assert s.pop("airplay") in ("on", "off", "absent")     # the receiver unit's own state
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


def test_an_unwritable_settings_folder_is_a_clear_503(tmp_path, monkeypatch):
    locked = tmp_path / "locked"
    locked.mkdir()
    locked.chmod(0o555)
    try:
        c = _client(tmp_path, monkeypatch)
        monkeypatch.setattr(head_settings, "PATH", str(locked / "head" / "settings.json"))
        r = c.put("/api/head/settings", json={"mouth": True})
        assert r.status_code == 503 and "provision.sh" in r.json()["error"]
        assert c.get("/api/head/settings").status_code == 200       # reading still works: the defaults
    finally:
        locked.chmod(0o755)


def test_airplay_on_and_off_drive_the_receiver_unit(tmp_path, monkeypatch):
    c = _client(tmp_path, monkeypatch)
    calls, state = [], {"v": "off"}

    async def set_airplay(on):
        calls.append(on); state["v"] = "on" if on else "off"
    monkeypatch.setattr(head_settings, "airplay_state", lambda: state["v"])
    monkeypatch.setattr(head_settings, "_set_airplay", set_airplay)
    r = c.put("/api/head/settings", json={"airplay": True})
    assert r.status_code == 200 and r.json()["airplay"] == "on" and calls == [True]
    assert "airplay" not in head_settings.load()          # systemd is the truth, not the file
    assert c.put("/api/head/settings", json={"airplay": False}).json()["airplay"] == "off"
    assert c.put("/api/head/settings", json={"airplay": "yes"}).status_code == 400


def test_no_receiver_means_airplay_cannot_be_turned_on(tmp_path, monkeypatch):
    c = _client(tmp_path, monkeypatch)
    monkeypatch.setattr(head_settings, "airplay_state", lambda: "absent")
    r = c.put("/api/head/settings", json={"airplay": True})
    assert r.status_code == 409 and "no AirPlay receiver" in r.json()["error"]
