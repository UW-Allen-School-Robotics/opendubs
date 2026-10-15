"""The kiosk app's next build, served to the tablet (kiosk_update.py): only a complete build, and
never while a show is loaded."""
import hashlib
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from fastapi import FastAPI
from fastapi.testclient import TestClient

import choreo_proxy
import kiosk_update as ku

APK = b"PK\x03\x04" + b"a signed apk" * 100


def _client(tmp_path, monkeypatch, runner=(503, {"error": "the show runner is not running"})):
    monkeypatch.setattr(ku, "DIR", str(tmp_path))
    monkeypatch.setattr(choreo_proxy, "_call", lambda method, path, body=None: runner)
    app = FastAPI()
    app.include_router(ku.router)
    return TestClient(app)


def _publish(tmp_path, code=3, name="0.3.0"):
    (tmp_path / ku.APK_NAME).write_bytes(APK)
    (tmp_path / "latest.json").write_text(json.dumps({"versionCode": code, "versionName": name,
                                                      "sha256": hashlib.sha256(APK).hexdigest(), "signer": "ab" * 32}))


def test_no_build_is_a_404(tmp_path, monkeypatch):
    c = _client(tmp_path, monkeypatch)
    assert c.get("/api/kiosk/latest").status_code == 404
    assert c.get("/api/kiosk/apk").status_code == 404
    (tmp_path / "latest.json").write_text("{ half")                     # being written, or broken
    assert c.get("/api/kiosk/latest").status_code == 404
    (tmp_path / "latest.json").write_text(json.dumps({"versionCode": 3, "sha256": "00"}))   # no APK beside it
    assert c.get("/api/kiosk/latest").status_code == 404


def test_a_build_is_offered_with_its_hash_and_served(tmp_path, monkeypatch):
    _publish(tmp_path)
    c = _client(tmp_path, monkeypatch)
    d = c.get("/api/kiosk/latest").json()
    assert d["versionCode"] == 3 and d["versionName"] == "0.3.0" and d["install_ok"] is True and d["why"] is None
    r = c.get("/api/kiosk/apk")
    assert r.status_code == 200 and r.content == APK and r.headers["content-type"] == ku.APK_TYPE
    assert hashlib.sha256(r.content).hexdigest() == d["sha256"]


def test_never_while_a_show_is_loaded(tmp_path, monkeypatch):
    _publish(tmp_path)
    c = _client(tmp_path, monkeypatch, runner=(200, {"running": "monster-mash", "status": "ARMED_WAITING"}))
    d = c.get("/api/kiosk/latest").json()
    assert d["install_ok"] is False and "monster-mash" in d["why"]
    c = _client(tmp_path, monkeypatch, runner=(200, {"running": None, "status": "DONE"}))
    assert c.get("/api/kiosk/latest").json()["install_ok"] is True
    c = _client(tmp_path, monkeypatch, runner=(500, {"error": "?"}))     # unknown: wait, don't guess
    assert c.get("/api/kiosk/latest").json()["install_ok"] is False
