"""Play to robot: what a phone sends is checked, capped, kept on the robot, and served back."""
import functools
import http.server
import os
import sys
import threading

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from fastapi import FastAPI
from fastapi.testclient import TestClient

import play_store

WAV = b"RIFF\x24\x00\x00\x00WAVEfmt " + bytes(28)
MP3 = b"ID3\x04\x00\x00\x00\x00\x00\x00" + bytes(64)


def _client(tmp_path, monkeypatch):
    monkeypatch.setattr(play_store, "ROOT", str(tmp_path / "play"))
    app = FastAPI()
    app.include_router(play_store.router)
    return TestClient(app)


def test_an_upload_is_stored_by_content_and_served_back(tmp_path, monkeypatch):
    c = _client(tmp_path, monkeypatch)
    r = c.post("/api/play/upload?name=My Song.wav", content=WAV)
    assert r.status_code == 200, r.text
    e = r.json()
    assert e["title"] == "My Song" and e["file"].endswith(".wav") and e["source"] == "upload"
    got = c.get(e["url"])
    assert got.status_code == 200 and got.content == WAV and got.headers["content-type"] == "audio/wav"
    assert [x["id"] for x in c.get("/api/play/files").json()["files"]] == [e["id"]]
    assert str(tmp_path) in play_store.ROOT              # on the robot's scratch area, not the repo


def test_too_big_is_refused(tmp_path, monkeypatch):
    c = _client(tmp_path, monkeypatch)
    monkeypatch.setattr(play_store, "MAX_BYTES", 50)
    assert c.post("/api/play/upload?name=big.wav", content=WAV + bytes(100)).status_code == 413
    assert c.get("/api/play/files").json()["files"] == []


def test_not_audio_is_refused(tmp_path, monkeypatch):
    c = _client(tmp_path, monkeypatch)
    r = c.post("/api/play/upload?name=song.mp3", content=b"<html>not a song</html>")
    assert r.status_code == 415                          # the name says mp3; the bytes decide
    assert c.post("/api/play/upload?name=x", content=b"").status_code == 400


def test_a_link_is_fetched_by_the_robot(tmp_path, monkeypatch):
    c = _client(tmp_path, monkeypatch)
    (tmp_path / "srv").mkdir()
    (tmp_path / "srv" / "tune.mp3").write_bytes(MP3)
    handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory=str(tmp_path / "srv"))
    handler.log_message = lambda *a: None
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        url = f"http://127.0.0.1:{srv.server_address[1]}/tune.mp3"
        r = c.post("/api/play/url", json={"url": url})
        assert r.status_code == 200, r.text
        assert r.json()["file"].endswith(".mp3") and r.json()["title"] == "tune" and r.json()["source"] == "url"
        missing = c.post("/api/play/url", json={"url": url.replace("tune", "nope")})
        assert missing.status_code == 502
    finally:
        srv.shutdown()


def test_only_web_links(tmp_path, monkeypatch):
    c = _client(tmp_path, monkeypatch)
    for bad in ("file:///etc/passwd", "ftp://x/y.mp3", "", None, 5):
        assert c.post("/api/play/url", json={"url": bad}).status_code == 400, bad


def test_the_oldest_go_first(tmp_path, monkeypatch):
    c = _client(tmp_path, monkeypatch)
    monkeypatch.setattr(play_store, "KEEP_FILES", 2)
    ids = [c.post(f"/api/play/upload?name={i}.wav", content=WAV + bytes([i])).json()["id"] for i in range(3)]
    kept = [x["id"] for x in c.get("/api/play/files").json()["files"]]
    assert kept == [ids[2], ids[1]]


def test_delete_and_bad_names(tmp_path, monkeypatch):
    c = _client(tmp_path, monkeypatch)
    e = c.post("/api/play/upload?name=a.wav", content=WAV).json()
    assert c.delete(f"/api/play/files/{e['id']}").json()["deleted"] is True
    assert c.get(e["url"]).status_code == 404
    assert c.get("/api/play/files/..%2F..%2Fetc%2Fpasswd").status_code == 404
    assert c.delete("/api/play/files/not-an-id").status_code == 404


def test_an_unwritable_store_is_a_clear_503_not_a_500(tmp_path, monkeypatch):
    """On the robot /var/lib/opendubs is root's and the dashboard runs as kiosk: until provision.sh
    makes the folder, every call says why rather than failing with a 500."""
    locked = tmp_path / "locked"
    locked.mkdir()
    locked.chmod(0o555)
    try:
        monkeypatch.setattr(play_store, "ROOT", str(locked / "play"))
        app = FastAPI()
        app.include_router(play_store.router)
        c = TestClient(app)
        for r in (c.get("/api/play/files"), c.post("/api/play/upload?name=a.wav", content=WAV),
                  c.get("/api/play/files/0123456789abcdef.wav"), c.delete("/api/play/files/0123456789abcdef")):
            assert r.status_code == 503, (r.status_code, r.text)
            assert "provision.sh" in r.json()["error"]
    finally:
        locked.chmod(0o755)
