"""The face page reloads itself after a deploy (face-web/engine/main.js): face_door.face_version() changes
when a file the page runs changes, and only then."""
import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import face_door


def _tree(root):
    for rel, body in (("index.html", "<html>"), ("engine/main.js", "1"), ("engine/percept/motion.js", "m"),
                      ("vendor/mediapipe/model.tflite", "x" * 100), ("tests/test_x.mjs", "t"),
                      ("library/skins/a.json", "{}"), ("README.md", "r")):
        p = os.path.join(root, rel)
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "w") as fh:
            fh.write(body)


def _touch(root, rel, body):
    p = os.path.join(root, rel)
    with open(p, "w") as fh:
        fh.write(body)
    t = time.time() + 5
    os.utime(p, (t, t))


def test_a_deploy_of_the_pages_own_files_changes_the_version(tmp_path):
    root = str(tmp_path)
    _tree(root)
    v0 = face_door.face_version(root)
    assert v0 == face_door.face_version(root)                        # stable while nothing changes
    for rel in ("engine/main.js", "engine/percept/motion.js", "index.html", "vendor/mediapipe/model.tflite"):
        _touch(root, rel, "changed")
        v1 = face_door.face_version(root)
        assert v1 != v0, rel
        v0 = v1


def test_its_tests_tools_and_skin_library_do_not_reload_it(tmp_path):
    root = str(tmp_path)
    _tree(root)
    v0 = face_door.face_version(root)
    for rel in ("tests/test_x.mjs", "library/skins/a.json", "README.md"):
        _touch(root, rel, "changed")
    assert face_door.face_version(root) == v0


def test_served_to_the_page(monkeypatch, tmp_path):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    _tree(str(tmp_path))
    monkeypatch.setattr(face_door, "FACE_DIR", str(tmp_path))
    app = FastAPI()
    app.include_router(face_door.router)
    r = TestClient(app).get("/api/face/version")
    assert r.status_code == 200 and r.json() == {"version": face_door.face_version(str(tmp_path))}


def test_the_real_face_web_is_found():
    assert os.path.isfile(os.path.join(face_door.FACE_DIR, "engine", "main.js")), face_door.FACE_DIR
