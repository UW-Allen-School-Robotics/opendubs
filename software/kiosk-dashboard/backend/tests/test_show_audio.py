"""Show audio (choreography plan 4.2): the hub that passes the runner's commands to the ONE device
holding the role. No robot, no network beyond the in-process test client."""
import os
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from fastapi import FastAPI
from fastapi.testclient import TestClient

import show_audio


def _client():
    show_audio.hub = show_audio.Hub()
    app = FastAPI()
    app.include_router(show_audio.router)
    return TestClient(app)


def test_the_clock_ping_is_answered_with_thor_monotonic_times():
    c = _client()
    with c.websocket_connect("/ws/show-audio") as ws:
        ws.receive_json()                                  # the hello
        ws.send_json({"ping": 123.5})
        m = ws.receive_json()
        assert m["pong"] == 123.5 and m["t1"] <= m["t2"]
        assert abs(m["t2"] - show_audio.thor_ms()) < 1000


def test_a_page_on_another_origin_cannot_join_but_the_dashboard_and_scripts_can():
    import pytest
    from starlette.websockets import WebSocketDisconnect
    c = _client()
    with pytest.raises(WebSocketDisconnect) as e:
        with c.websocket_connect("/ws/show-audio", headers={"origin": "http://evil.example"}):
            pass
    assert e.value.code == 1008
    # the dashboard's own page (the Host a browser on the robot sends) joins
    with c.websocket_connect("/ws/show-audio", headers={"origin": "http://10.42.2.10:8080",
                                                       "host": "10.42.2.10:8080"}) as ws:
        ws.receive_json()
    with c.websocket_connect("/ws/show-audio") as ws:      # no Origin: a script, the runner
        ws.receive_json()


def test_commands_go_to_the_one_holder_and_no_one_else():
    c = _client()
    with c.websocket_connect("/ws/show-audio") as a, c.websocket_connect("/ws/show-audio") as b:
        a.receive_json(), b.receive_json()
        a.send_json({"claim": True, "label": "tablet"})
        assert a.receive_json() == {"holder": "tablet", "you_hold": True}
        assert b.receive_json() == {"holder": "tablet", "you_hold": False}
        r = c.post("/api/choreo/audio", json={"op": "start", "id": "x", "t0_mono": 5.0}).json()
        assert r["delivered"] and r["holder"] == "tablet"
        assert a.receive_json() == {"op": "start", "id": "x", "t0_mono": 5.0}
        b.send_json({"ping": 1})                           # b got nothing but its own pong
        assert "pong" in b.receive_json()


def test_the_tablet_auto_claim_never_takes_it_from_a_holder():
    c = _client()
    with c.websocket_connect("/ws/show-audio") as a, c.websocket_connect("/ws/show-audio") as b:
        a.receive_json(), b.receive_json()
        a.send_json({"claim": True, "label": "laptop"})
        a.receive_json(), b.receive_json()
        b.send_json({"claim": True, "label": "tablet", "only_if_free": True})
        b.send_json({"ping": 1})
        assert "pong" in b.receive_json()
        assert c.get("/api/choreo/audio").json()["holder"] == "laptop"


def test_a_new_claim_cuts_the_old_holder_and_hands_over_the_loaded_show():
    c = _client()
    with c.websocket_connect("/ws/show-audio") as a, c.websocket_connect("/ws/show-audio") as b:
        a.receive_json(), b.receive_json()
        a.send_json({"claim": True, "label": "tablet"})
        a.receive_json(), b.receive_json()
        c.post("/api/choreo/audio", json={"op": "load", "id": "L1", "items": []})
        assert a.receive_json()["op"] == "load"
        b.send_json({"claim": True, "label": "laptop"})
        assert a.receive_json() == {"op": "cut"}
        assert b.receive_json() == {"holder": "laptop", "you_hold": True}
        assert b.receive_json()["id"] == "L1"


def test_ready_counts_only_for_the_current_load_from_the_holder():
    c = _client()
    with c.websocket_connect("/ws/show-audio") as a:
        a.receive_json()
        a.send_json({"claim": True, "label": "tablet"})
        a.receive_json()
        c.post("/api/choreo/audio", json={"op": "load", "id": "L2", "items": []})
        a.receive_json()
        assert c.get("/api/choreo/audio").json()["ready"] is False
        a.send_json({"ready": "L2"})
        a.send_json({"ping": 1})
        a.receive_json()
        assert c.get("/api/choreo/audio").json()["ready"] is True


def test_a_holder_that_disconnects_releases_the_role():
    c = _client()
    with c.websocket_connect("/ws/show-audio") as a:
        a.receive_json()
        a.send_json({"claim": True, "label": "tablet"})
        a.receive_json()
    v = c.get("/api/choreo/audio").json()
    assert v["holder"] is None and v["connected"] == 0
    assert c.post("/api/choreo/audio", json={"op": "cut"}).json()["delivered"] is False


def test_only_known_ops_are_accepted():
    c = _client()
    assert c.post("/api/choreo/audio", json={"op": "volume"}).status_code == 400


def test_media_serves_the_librarys_songs_and_nothing_else_of_it():
    lib = tempfile.mkdtemp()
    for sub, name, data in (("songs", "tune.wav", b"RIFF"), ("skins", "eyes.png", b"png"), ("", "index.yaml", b"[]")):
        os.makedirs(os.path.join(lib, sub), exist_ok=True)
        open(os.path.join(lib, sub, name), "wb").write(data)
    show_audio.LIBRARY_DIR = lib
    c = _client()
    assert c.get("/api/choreo/media/library/songs/tune.wav").content == b"RIFF"
    for rel in ("library/skins/eyes.png", "library/index.yaml", "library/songs/../index.yaml",
                "library/songs/none.wav"):
        assert c.get("/api/choreo/media/" + rel).status_code == 404, rel


def test_media_serves_only_audio_and_sounds_under_the_shows_directory():
    d = tempfile.mkdtemp()
    os.makedirs(os.path.join(d, "audio"))
    open(os.path.join(d, "audio", "song.wav"), "wb").write(b"RIFF")
    open(os.path.join(d, "secret.yaml"), "w").write("x")
    show_audio.SHOWS_DIR = d
    c = _client()
    assert c.get("/api/choreo/media/audio/song.wav").status_code == 200
    assert c.get("/api/choreo/media/secret.yaml").status_code == 404
    assert show_audio.media_path("audio/../secret.yaml") is None
    assert show_audio.media_path("../../etc/passwd") is None


def test_the_dashboard_serves_these_routes_through_the_choreo_router():
    import choreo_proxy
    show_audio.hub = show_audio.Hub()
    app = FastAPI()
    app.include_router(choreo_proxy.router)               # exactly as app.py includes it
    c = TestClient(app)
    assert c.get("/api/choreo/audio").json()["holder"] is None
    with c.websocket_connect("/ws/show-audio") as ws:
        ws.receive_json()
        ws.send_json({"ping": 1})
        assert "pong" in ws.receive_json()


# ---- ambient music (the "Head & face" page) --------------------------------------------------
def _ambient_client(tmp_path, monkeypatch):
    import yaml
    lib = tmp_path / "library"
    (lib / "songs").mkdir(parents=True)
    for name in ("calm.mp3", "loud.mp3"):
        (lib / "songs" / name).write_bytes(b"not really audio")
    (lib / "index.yaml").write_text(yaml.safe_dump([
        {"id": "calm", "kind": "song", "file": "songs/calm.mp3", "duration_s": 60.0, "tags": ["ambient"]},
        {"id": "loud", "kind": "song", "file": "songs/loud.mp3", "duration_s": 30.0},
        {"id": "eyes", "kind": "skin", "file": "skins/eyes.png"}]))
    monkeypatch.setattr(show_audio, "LIBRARY_DIR", str(lib))
    monkeypatch.setattr(show_audio, "AMBIENT_STATE", str(tmp_path / "state" / "ambient.json"))
    return _client()


def test_ambient_lists_the_library_songs_marking_the_ambient_ones(tmp_path, monkeypatch):
    c = _ambient_client(tmp_path, monkeypatch)
    v = c.get("/api/choreo/ambient").json()
    assert [(s["id"], s["ambient"]) for s in v["songs"]] == [("calm", True), ("loud", False)]
    assert v["state"]["playing"] is False and v["show_active"] is False


def test_ambient_play_defaults_to_the_tagged_songs_and_reaches_the_holder(tmp_path, monkeypatch):
    c = _ambient_client(tmp_path, monkeypatch)
    with c.websocket_connect("/ws/show-audio") as a:
        a.receive_json()
        a.send_json({"claim": True, "label": "tablet"})
        a.receive_json()
        r = c.post("/api/choreo/ambient", json={"op": "play", "volume": 0.3}).json()
        assert r["state"]["playing"] and r["state"]["songs"] == ["calm"] and r["state"]["volume"] == 0.3
        m = a.receive_json()
        assert m["op"] == "ambient" and m["playing"] and m["items"] == [
            {"id": "calm", "url": "/api/choreo/media/library/songs/calm.mp3", "title": "calm"}]
        a.send_json({"ambient": {"now": "calm", "blocked": False}})
        a.send_json({"ping": 1})
        a.receive_json()                                   # the pong: the report was handled before it
        assert c.get("/api/choreo/ambient").json()["state"]["now_playing"] == "calm"


def test_ambient_refuses_what_is_not_a_library_song_and_a_bad_volume(tmp_path, monkeypatch):
    c = _ambient_client(tmp_path, monkeypatch)
    assert c.post("/api/choreo/ambient", json={"op": "play", "songs": ["eyes"]}).status_code == 400
    assert c.post("/api/choreo/ambient", json={"op": "volume", "volume": 2}).status_code == 400
    assert c.post("/api/choreo/ambient", json={"op": "dance"}).status_code == 400


def test_ambient_survives_a_restart(tmp_path, monkeypatch):
    c = _ambient_client(tmp_path, monkeypatch)
    c.post("/api/choreo/ambient", json={"op": "play", "songs": ["loud", "calm"], "shuffle": True})
    show_audio.hub = show_audio.Hub()                      # a dashboard restart
    st = show_audio.hub.ambient
    assert st["playing"] and st["songs"] == ["loud", "calm"] and st["shuffle"] is True


def test_a_show_takes_the_channel_and_ambient_resumes_after(tmp_path, monkeypatch):
    c = _ambient_client(tmp_path, monkeypatch)
    c.post("/api/choreo/ambient", json={"op": "play"})
    c.post("/api/choreo/audio", json={"op": "load", "id": "L9", "items": []})
    assert c.get("/api/choreo/ambient").json()["show_active"] is True
    with c.websocket_connect("/ws/show-audio") as a:      # a device that claims mid-show is told so
        a.receive_json()
        a.send_json({"claim": True, "label": "tablet"})
        a.receive_json()
        assert a.receive_json()["id"] == "L9"
        amb = a.receive_json()
        assert amb["op"] == "ambient" and amb["show_active"] is True
    c.post("/api/choreo/audio", json={"op": "stop", "fade_s": 0.25})
    assert c.get("/api/choreo/ambient").json()["show_active"] is False
