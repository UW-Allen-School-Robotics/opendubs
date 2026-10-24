"""The face looks this robot has (face_looks.py): the discovered list, and the content folder's files for
the face page, never anything else of the robot's."""
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from fastapi import FastAPI
from fastapi.testclient import TestClient

import face_looks


def test_the_looks_list_and_only_look_files_are_served(tmp_path, monkeypatch):
    (tmp_path / "face" / "themes").mkdir(parents=True)
    (tmp_path / "face" / "themes" / "winter.json").write_text(json.dumps({"colors": {"SAFE_IDLE": "#88ccff"}}))
    (tmp_path / "secret.txt").write_text("not a look")
    monkeypatch.setenv("OPENDUBS_SHOWS_DIR", str(tmp_path))
    monkeypatch.setattr(face_looks, "_library_skins", lambda: [{"id": "sparkle", "kind": "image", "source": "library"}])
    (tmp_path / "face" / "skins").mkdir()
    for name in ("zebra", "apple"):
        (tmp_path / "face" / "skins" / f"{name}.js").write_text("export function draw() {}")
    app = FastAPI()
    app.include_router(face_looks.router)
    c = TestClient(app)
    d = c.get("/api/head/looks").json()
    assert "winter" in {t["id"] for t in d["themes"]} and "sparkle" in {s["id"] for s in d["skins"]}
    order = [(s["source"], s["id"]) for s in d["skins"] if s["source"] != "builtin"]
    assert order == [("content", "apple"), ("content", "zebra"), ("library", "sparkle")]   # stable: by source, then id
    assert {s["kind"] for s in d["skins"]} <= {"code", "image", "video"}
    r = c.get("/face/content/themes/winter.json")
    assert r.status_code == 200 and r.json()["colors"]["SAFE_IDLE"] == "#88ccff"
    for bad in ("/face/content/themes/..%2Fsecret.txt", "/face/content/other/winter.json",
                "/face/content/themes/secret.txt", "/face/content/skins/missing.js"):
        assert c.get(bad).status_code == 404, bad
