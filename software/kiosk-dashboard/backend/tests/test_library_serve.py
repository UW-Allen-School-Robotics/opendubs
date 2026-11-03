"""The show library as the tablet sees it: the index, items by hash, and what is never served."""
import hashlib
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import yaml
from fastapi import FastAPI
from fastapi.testclient import TestClient

import library_serve


def _sha(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def _skin(item_id, data, **extra):
    return {"id": item_id, "kind": "skin", "type": "image", "file": f"skins/{item_id}.png",
            "sha256": _sha(data), "bytes": len(data), "width": 512, "height": 512,
            "params": {"size": 0.8, "spin_dps": 45}, "inputs": ["lookX", "lookY"],
            "source": "generated", "licence": "test asset", **extra}


def _library(tmp_path, monkeypatch, entries, files):
    """A library folder laid out as the sync writes it (software/choreo/library.py)."""
    for rel, data in files.items():
        p = tmp_path / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(data)
    (tmp_path / "index.yaml").write_text(yaml.safe_dump(entries))
    monkeypatch.setenv("OPENDUBS_LIBRARY", str(tmp_path))
    app = FastAPI()
    app.include_router(library_serve.router)
    return TestClient(app)


GEAR = b"\x89PNG\r\n\x1a\n gear"


def test_the_index_is_json_with_an_etag_and_a_304(tmp_path, monkeypatch):
    c = _library(tmp_path, monkeypatch, [_skin("gear", GEAR)], {"skins/gear.png": GEAR})
    r = c.get("/api/library/index")
    assert r.status_code == 200
    assert [e["id"] for e in r.json()] == ["gear"]
    etag = r.headers["etag"]
    assert etag == '"' + _sha((tmp_path / "index.yaml").read_bytes()) + '"'
    assert c.get("/api/library/index", headers={"If-None-Match": etag}).status_code == 304


def test_a_replaced_index_changes_the_etag(tmp_path, monkeypatch):
    c = _library(tmp_path, monkeypatch, [_skin("gear", GEAR)], {"skins/gear.png": GEAR})
    before = c.get("/api/library/index").headers["etag"]
    orb = b"\x1aE\xdf\xa3 orb"
    (tmp_path / "skins" / "orb.png").write_bytes(orb)
    (tmp_path / "index.yaml").write_text(yaml.safe_dump([_skin("gear", GEAR), _skin("orb", orb)]))
    r = c.get("/api/library/index", headers={"If-None-Match": before})
    assert r.status_code == 200 and r.headers["etag"] != before
    assert sorted(e["id"] for e in r.json()) == ["gear", "orb"]


def test_an_invalid_entry_is_left_out_not_fatal(tmp_path, monkeypatch):
    bad = _skin("typo", GEAR, params={"spinn_dps": 90})        # the validator's own example
    c = _library(tmp_path, monkeypatch, [_skin("gear", GEAR), bad], {"skins/gear.png": GEAR})
    assert [e["id"] for e in c.get("/api/library/index").json()] == ["gear"]


def test_no_library_is_an_empty_index(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENDUBS_LIBRARY", str(tmp_path / "absent"))
    app = FastAPI()
    app.include_router(library_serve.router)
    r = TestClient(app).get("/api/library/index")
    assert r.status_code == 200 and r.json() == []


def test_an_item_by_hash_is_immutable(tmp_path, monkeypatch):
    c = _library(tmp_path, monkeypatch, [_skin("gear", GEAR)], {"skins/gear.png": GEAR})
    r = c.get(f"/api/library/item/{_sha(GEAR)}")
    assert r.status_code == 200 and r.content == GEAR
    assert r.headers["content-type"] == "image/png"
    assert "immutable" in r.headers["cache-control"]


def test_a_file_changed_behind_the_syncs_back_is_not_served(tmp_path, monkeypatch):
    c = _library(tmp_path, monkeypatch, [_skin("gear", GEAR)], {"skins/gear.png": GEAR})
    assert c.get(f"/api/library/item/{_sha(GEAR)}").status_code == 200
    (tmp_path / "skins" / "gear.png").write_bytes(GEAR + b" edited by hand")
    assert c.get(f"/api/library/item/{_sha(GEAR)}").status_code == 409


def test_unknown_or_malformed_hashes(tmp_path, monkeypatch):
    c = _library(tmp_path, monkeypatch, [_skin("gear", GEAR)], {"skins/gear.png": GEAR})
    assert c.get(f"/api/library/item/{'0' * 64}").status_code == 404
    assert c.get("/api/library/item/not-a-hash").status_code == 400


def test_the_tools_own_folders_are_never_served(tmp_path, monkeypatch):
    secret = b"staged, not yet in the index"
    entry = _skin("staged", secret, file=".incoming/staged.png")
    c = _library(tmp_path, monkeypatch, [_skin("gear", GEAR), entry],
                 {"skins/gear.png": GEAR, ".incoming/staged.png": secret})
    assert [e["id"] for e in c.get("/api/library/index").json()] == ["gear"]
    assert c.get(f"/api/library/item/{_sha(secret)}").status_code == 404


def test_an_entry_pointing_outside_the_library_is_refused(tmp_path, monkeypatch):
    outside = tmp_path.parent / "outside.png"
    outside.write_bytes(GEAR)
    entry = _skin("escape", GEAR, file="../outside.png")
    c = _library(tmp_path, monkeypatch, [entry], {})
    assert c.get("/api/library/index").json() == []
    assert c.get(f"/api/library/item/{_sha(GEAR)}").status_code == 404


def test_a_folder_item_is_its_listing_then_its_files(tmp_path, monkeypatch):
    files = {"skins/rig/eye.svg": b"<svg/>", "skins/rig/parts/lid.svg": b"<svg id='lid'/>"}
    for rel, data in files.items():
        (tmp_path / rel).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / rel).write_bytes(data)
    lines = sorted(f"{rel[len('skins/rig/'):]}\t{_sha(d)}" for rel, d in files.items())
    folder_hash = _sha("\n".join(lines).encode())
    entry = {**_skin("rig", b""), "file": "skins/rig", "sha256": folder_hash,
             "bytes": sum(len(d) for d in files.values())}
    c = _library(tmp_path, monkeypatch, [entry], {})
    r = c.get(f"/api/library/item/{folder_hash}")
    assert r.status_code == 200 and r.text == "\n".join(lines)
    f = c.get(f"/api/library/item/{folder_hash}/parts/lid.svg")
    assert f.status_code == 200 and f.content == b"<svg id='lid'/>"
    assert c.get(f"/api/library/item/{folder_hash}/../../index.yaml").status_code == 404


def test_the_face_page_reads_the_library_by_file_name(tmp_path, monkeypatch):
    """index.json and <entry.file> under /api/library/files/: how engine/skins loads a library."""
    c = _library(tmp_path, monkeypatch, [_skin("gear", GEAR)], {"skins/gear.png": GEAR})
    assert [e["id"] for e in c.get("/api/library/files/index.json").json()] == ["gear"]
    r = c.get("/api/library/files/skins/gear.png")
    assert r.status_code == 200 and r.content == GEAR and r.headers["content-type"] == "image/png"
    assert c.get("/api/library/files/skins/other.png").status_code == 404     # not indexed
    assert c.get("/api/library/files/index.yaml").status_code == 404          # only indexed files
    (tmp_path / "skins" / "gear.png").write_bytes(GEAR + b" edited")
    assert c.get("/api/library/files/skins/gear.png").status_code == 409      # hash no longer matches


def test_no_dashboard_module_takes_a_show_runner_modules_name():
    """The dashboard imports the show runner's library.py into its own process, and library.py imports its
    neighbours by bare name. A dashboard module of the same name is found first: the dashboard's face_looks.py
    hid the runner's (2026-10-25), library.py failed to import, and /api/library/index answered 503."""
    here = os.path.join(os.path.dirname(__file__), "..")
    ours = {f for f in os.listdir(here) if f.endswith(".py")}
    theirs = {f for f in os.listdir(library_serve.CHOREO_DIR) if f.endswith(".py")}
    assert not ours & theirs, f"same name as a show runner module: {sorted(ours & theirs)}"
