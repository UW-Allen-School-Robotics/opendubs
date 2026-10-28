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


def test_each_output_has_its_own_holder_and_a_load_plays_only_on_its_output():
    # 2026-10-28: the speakerphone held "the" role, so every show's music played from it
    c = _client()
    with c.websocket_connect("/ws/show-audio") as tab, c.websocket_connect("/ws/show-audio") as spk:
        tab.receive_json(), spk.receive_json()
        tab.send_json({"claim": True, "label": "tablet"})               # no output named: the tablet's
        tab.receive_json(), spk.receive_json()
        spk.send_json({"claim": True, "label": "Jabra", "output": "speakerphone"})
        assert spk.receive_json() == {"holder": "Jabra", "you_hold": True}
        assert tab.receive_json() == {"holder": "tablet", "you_hold": True}   # not cut: a different output
        v = c.get("/api/choreo/audio").json()
        assert v["holder"] == "tablet" and {o["name"]: o["label"] for o in v["outputs"]} == \
            {"tablet": "tablet", "speakerphone": "Jabra"}
        r = c.post("/api/choreo/audio", json={"op": "load", "id": "L9", "items": [], "output": "speakerphone"}).json()
        assert r["delivered"] and r["output"] == "speakerphone"
        assert spk.receive_json()["id"] == "L9"
        assert tab.receive_json()["show_active"] is True               # the tablet's ambient waits for the show
        tab.send_json({"ready": "L9"})                                 # only the load's own output counts
        spk.send_json({"ping": 1})
        spk.receive_json()
        assert c.get("/api/choreo/audio").json()["ready"] is False
        spk.send_json({"ready": "L9"})
        spk.send_json({"ping": 1})
        spk.receive_json()
        assert c.get("/api/choreo/audio").json()["ready"] is True


def test_test_sound_plays_a_chime_on_the_chosen_output_only():
    with _client() as c:                                   # one event loop: the chime runs as a background task
        _test_sound(c)


def _test_sound(c):
    assert c.post("/api/choreo/audio/test", json={"output": "tablet"}).json()["code"] == "SHOW_AUDIO_OUTPUT_MISSING"
    with c.websocket_connect("/ws/show-audio") as tab, c.websocket_connect("/ws/show-audio") as spk:
        tab.receive_json(), spk.receive_json()
        tab.send_json({"claim": True, "label": "tablet"})
        tab.receive_json(), spk.receive_json()
        spk.send_json({"claim": True, "label": "Jabra", "output": "speakerphone"})
        spk.receive_json(), tab.receive_json()
        assert c.post("/api/choreo/audio/test", json={"output": "speakerphone"}).json()["ok"] is True
        m = spk.receive_json()
        assert m["op"] == "load" and m["items"][0]["url"] == show_audio.TEST_URL
        spk.send_json({"ready": m["id"]})
        assert spk.receive_json()["op"] == "start"
        assert spk.receive_json()["op"] == "stop"
        assert c.get(show_audio.TEST_URL).headers["content-type"] == "audio/wav"


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


def test_media_serves_the_librarys_videos_by_byte_range_for_the_stage_page():
    lib = tempfile.mkdtemp()
    os.makedirs(os.path.join(lib, "videos"))
    open(os.path.join(lib, "videos", "v.mp4"), "wb").write(bytes(range(256)))
    show_audio.LIBRARY_DIR = lib
    r = _client().get("/api/choreo/media/library/videos/v.mp4", headers={"Range": "bytes=10-19"})
    assert r.status_code == 206 and r.content == bytes(range(10, 20))   # a <video> seeks by ranges


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


# ---- play to robot (the Tablet lane's /play/ page) ---------------------------------------------
def test_a_play_file_reaches_the_holder_once_at_the_ambient_volume(tmp_path, monkeypatch):
    c = _ambient_client(tmp_path, monkeypatch)
    c.post("/api/choreo/ambient", json={"op": "volume", "volume": 0.4})
    with c.websocket_connect("/ws/show-audio") as a:
        a.receive_json()
        a.send_json({"claim": True, "label": "tablet"})
        a.receive_json()
        r = c.post("/api/choreo/ambient", json={"op": "play_file", "url": "/api/play/files/ab12.mp3",
                                                "title": "Birthday song"})
        assert r.status_code == 200
        assert a.receive_json() == {"op": "play_file", "url": "/api/play/files/ab12.mp3",
                                    "title": "Birthday song", "volume": 0.4}
        a.send_json({"ambient": {"now": "Birthday song", "source": "play", "blocked": False}})
        a.send_json({"ping": 1})
        a.receive_json()                                      # the report is in once the pong is back
        st = c.get("/api/choreo/ambient").json()["state"]
        assert st["now_playing"] == "Birthday song" and st["playing_from"] == "play"
        a.send_json({"ambient": {"now": "calm", "source": "ambient", "blocked": False}})
        a.send_json({"ping": 2})
        a.receive_json()
        assert c.get("/api/choreo/ambient").json()["state"]["playing_from"] == "ambient"


def test_a_play_url_must_be_the_dashboards_own_store(tmp_path, monkeypatch):
    c = _ambient_client(tmp_path, monkeypatch)
    with c.websocket_connect("/ws/show-audio") as a:
        a.receive_json()
        a.send_json({"claim": True, "label": "tablet"})
        a.receive_json()
        for url in ("http://evil.example/a.mp3", "//evil.example/a.mp3", "/api/play/files/../../etc/passwd",
                    "/api/choreo/media/library/songs/calm.mp3", "/api/play/files/", "/api/play/files/a b.mp3",
                    "/api/play/files//x.mp3", None, 5):
            r = c.post("/api/choreo/ambient", json={"op": "play_file", "url": url})
            assert r.status_code == 400, url


def test_a_play_is_refused_while_a_show_has_the_speaker_and_with_no_speaker(tmp_path, monkeypatch):
    c = _ambient_client(tmp_path, monkeypatch)
    body = {"op": "play_file", "url": "/api/play/files/x.mp3", "title": "x"}
    r = c.post("/api/choreo/ambient", json=body)              # nobody holds the role
    assert r.status_code == 503 and r.json()["code"] == "SHOW_AUDIO_NO_DEVICE"
    with c.websocket_connect("/ws/show-audio") as a:
        a.receive_json()
        a.send_json({"claim": True, "label": "tablet"})
        a.receive_json()
        c.post("/api/choreo/audio", json={"op": "load", "id": "show", "items": []})
        a.receive_json()                                      # the load
        r = c.post("/api/choreo/ambient", json=body)
        assert r.status_code == 409 and r.json()["code"] == "SHOW_AUDIO_BUSY"
        c.post("/api/choreo/audio", json={"op": "stop", "fade_s": 0.25})
        a.receive_json()
        assert c.post("/api/choreo/ambient", json=body).status_code == 200


def test_stop_play_ends_only_the_play_and_ambient_off_ends_it_too(tmp_path, monkeypatch):
    c = _ambient_client(tmp_path, monkeypatch)
    c.post("/api/choreo/ambient", json={"op": "play"})
    with c.websocket_connect("/ws/show-audio") as a:
        a.receive_json()
        a.send_json({"claim": True, "label": "tablet"})
        a.receive_json(), a.receive_json()                    # the holder message, then ambient on
        assert c.post("/api/choreo/ambient", json={"op": "stop_play"}).status_code == 200
        assert a.receive_json() == {"op": "stop_play"}
        assert show_audio.hub.ambient["playing"] is True      # ambient stays on
        c.post("/api/choreo/ambient", json={"op": "stop"})
        assert a.receive_json() == {"op": "stop_play"}
        assert a.receive_json()["playing"] is False


def test_a_long_title_is_cut_and_control_characters_dropped(tmp_path, monkeypatch):
    c = _ambient_client(tmp_path, monkeypatch)
    with c.websocket_connect("/ws/show-audio") as a:
        a.receive_json()
        a.send_json({"claim": True, "label": "tablet"})
        a.receive_json()
        c.post("/api/choreo/ambient", json={"op": "play_file", "url": "/api/play/files/x.mp3",
                                            "title": "a\nb\x07" + "z" * 300})
        m = a.receive_json()
        assert m["title"].startswith("ab") and len(m["title"]) == show_audio.PLAY_TITLE_MAX


# ---- AirPlay: the Tablet lane's relay reports, this hub decides when it is heard -------------------
def _holder(c):
    a = c.websocket_connect("/ws/show-audio").__enter__()
    a.receive_json()
    a.send_json({"claim": True, "label": "tablet"})
    a.receive_json()
    return a


def _report(a, active, title=None, artist=None):
    a.portal.call(show_audio.hub.airplay_report, active, title, artist)   # on the app's own loop


def _next_op(a, op):
    for _ in range(6):
        m = a.receive_json()
        if m.get("op") == op:
            return m
    raise AssertionError(f"no {op}")


def test_airplay_starting_unmutes_it_and_pauses_ambient(tmp_path, monkeypatch):
    c = _ambient_client(tmp_path, monkeypatch)
    c.post("/api/choreo/ambient", json={"op": "play"})
    a = _holder(c)
    try:
        a.receive_json()                                      # ambient on, as the role is claimed
        _report(a, True, "Song", "Artist")
        assert a.receive_json() == {"op": "airplay_gate", "gate": "on"}
        amb = _next_op(a, "ambient")
        assert amb["airplay"] is True and amb["playing"] is True   # the device pauses ambient for it
        st = c.get("/api/choreo/ambient").json()["airplay"]
        assert st == {"active": True, "title": "Song", "artist": "Artist", "gate": "on"}
        _report(a, False)
        assert _next_op(a, "ambient")["airplay"] is False      # ... and ambient resumes after
    finally:
        a.__exit__(None, None, None)


def test_a_show_mutes_airplay_and_its_end_lets_it_back(tmp_path, monkeypatch):
    c = _ambient_client(tmp_path, monkeypatch)
    a = _holder(c)
    try:
        _report(a, True, "Song", None)
        assert _next_op(a, "airplay_gate")["gate"] == "on"
        c.post("/api/choreo/audio", json={"op": "load", "id": "show", "items": []})
        assert _next_op(a, "airplay_gate")["gate"] == "mute"
        assert c.get("/api/choreo/ambient").json()["airplay"]["gate"] == "mute"
        c.post("/api/choreo/audio", json={"op": "cut"})
        assert _next_op(a, "airplay_gate")["gate"] == "on"
    finally:
        a.__exit__(None, None, None)


def test_a_file_during_airplay_mutes_it_until_the_file_ends(tmp_path, monkeypatch):
    c = _ambient_client(tmp_path, monkeypatch)
    a = _holder(c)
    try:
        _report(a, True, "Song", None)
        _next_op(a, "airplay_gate")
        r = c.post("/api/choreo/ambient", json={"op": "play_file", "url": "/api/play/files/a1.mp3", "title": "A"})
        assert r.status_code == 200                           # latest wins: not refused
        assert _next_op(a, "play_file")["url"] == "/api/play/files/a1.mp3"
        assert _next_op(a, "airplay_gate")["gate"] == "mute"
        a.send_json({"ambient": {"now": "A", "source": "play", "blocked": False}})
        a.send_json({"ambient": {"now": None, "source": None, "blocked": False}})   # the file ended
        assert _next_op(a, "airplay_gate")["gate"] == "on"
    finally:
        a.__exit__(None, None, None)


def test_airplay_starting_during_a_file_stops_the_file(tmp_path, monkeypatch):
    c = _ambient_client(tmp_path, monkeypatch)
    a = _holder(c)
    try:
        c.post("/api/choreo/ambient", json={"op": "play_file", "url": "/api/play/files/a1.mp3", "title": "A"})
        _next_op(a, "play_file")
        _report(a, True, "Song", None)
        assert a.receive_json() == {"op": "stop_play"}        # AirPlay is the newer: the file stops
        assert _next_op(a, "airplay_gate")["gate"] == "on"
    finally:
        a.__exit__(None, None, None)


def test_no_airplay_means_no_gate_messages(tmp_path, monkeypatch):
    c = _ambient_client(tmp_path, monkeypatch)
    a = _holder(c)
    try:
        c.post("/api/choreo/audio", json={"op": "load", "id": "show", "items": []})
        assert a.receive_json()["op"] == "load"
        c.post("/api/choreo/audio", json={"op": "cut"})
        assert a.receive_json()["op"] == "cut"                # nothing about AirPlay in between
        assert c.get("/api/choreo/ambient").json()["airplay"]["active"] is False
    finally:
        a.__exit__(None, None, None)
