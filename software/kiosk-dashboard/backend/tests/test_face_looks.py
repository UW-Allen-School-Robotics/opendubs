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


def test_content_code_never_arrives_through_the_dashboard():
    """THE USER'S RULE (2026-10-24): content DATA may be uploaded through the dashboard; content CODE (code
    skins) arrives only from the content repository by the deploy/sync path. So nothing in the dashboard
    may write where the face imports code from: /face/content is GET-only, and only face_looks.py (which
    only reads) may name the content folder. A future upload into it fails here, on purpose."""
    import re
    here = os.path.join(os.path.dirname(__file__), "..")
    route = re.compile(r'@(?:router|app)\.(\w+)\(\s*["\'](/face/content[^"\']*)')
    names_content = re.compile(r"content_dir\(|OPENDUBS_SHOWS_DIR|OPENDUBS_LOCAL_SHOWS|/var/lib/opendubs/shows")
    for name in sorted(os.listdir(here)):
        if not name.endswith(".py"):
            continue
        src = open(os.path.join(here, name), encoding="utf-8").read()
        for method, path in route.findall(src):
            assert method == "get", f"{name}: {method.upper()} {path}: /face/content is read-only"
        if name != "face_looks.py":
            assert not names_content.search(src), f"{name} names the content folder: only face_looks.py may, read-only"
