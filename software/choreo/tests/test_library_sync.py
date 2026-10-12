#!/usr/bin/env python3
"""Pushing show-library items to a robot (library_sync.py), against a FAKE robot: a folder on this
computer (`local:/folder`), reached through the same install-the-tool, stage, verify and commit path
the real ssh push uses. Every item is made up here; no library content is ever committed."""
from __future__ import annotations

import io
import json
import os
import shutil
import sys
import tarfile
import tempfile
import wave

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, ".."))

import library  # noqa: E402
import library_sync as LS  # noqa: E402

FAILS = []


def check(what, ok, detail=""):
    print(("  ok    " if ok else "  FAIL  ") + what)
    if not ok:
        FAILS.append(f"{what}: {detail}")


def made_up_wav(path, secs, rate=8000):
    with wave.open(path, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(b"\x00\x01" * int(secs * rate))


tmp = tempfile.mkdtemp(prefix="opendubs-sync-test-")
here_lib, src = os.path.join(tmp, "laptop-library"), os.path.join(tmp, "src")
robot = os.path.join(tmp, "robot")
robot_lib, robot_shows = os.path.join(robot, "library"), os.path.join(robot, "shows")
os.makedirs(src)
os.makedirs(robot_shows)
for name, secs in (("tune-one.wav", 0.5), ("tune-two.wav", 0.25)):
    made_up_wav(os.path.join(src, name), secs)
with open(os.path.join(src, "eyes.png"), "wb") as fh:
    fh.write(b"not really a png")
library.add(os.path.join(src, "tune-one.wav"), "tune-one", "song", "made up for a test", "CC0", here_lib)
library.add(os.path.join(src, "tune-two.wav"), "tune-two", "song", "made up for a test", "CC0", here_lib)
library.add(os.path.join(src, "eyes.png"), "made-up-eyes", "skin", "made up for a test", "CC0", here_lib,
            type="image", width=480, height=480)
HOST = "local:" + robot_lib


def push(ids=(), everything=False, dry=False, replace=False, create=False):
    lines = []
    rc = LS.push(HOST, list(ids), everything, here_lib, LS.REMOTE_ROOT, LS.REMOTE_PYTHON, dry, replace, create,
                 out=lines.append)
    return rc, "\n".join(lines)


def robot_index():
    return library.load_index(robot_lib) or {}


print("library sync: the robot's library folder")
try:
    push(["tune-one"])
    check("a robot with no library folder is refused, saying how to create it", False)
except LS.SyncError as e:
    check("a robot with no library folder is refused, saying how to create it",
          "--create" in str(e) and not os.path.exists(robot_lib), str(e))
rc, out = push(["tune-one"], dry=True, create=True)
check("--dry-run never creates it either", not os.path.exists(robot_lib), out)

print("\nlibrary sync: pushing")
os.makedirs(robot_lib)
rc, out = push(["tune-one", "made-up-eyes"], dry=True)
check("--dry-run reports what WOULD move, and moves nothing",
      rc == 0 and "DRY RUN" in out and "would send 2 item(s)" in out and robot_index() == {}
      and not os.path.exists(os.path.join(robot_lib, "songs")), out)
rc, out = push(["tune-one", "made-up-eyes"])
ri = robot_index()
check("a push adds the items, with their index entries, and says what moved",
      rc == 0 and set(ri) == {"tune-one", "made-up-eyes"} and "moved 2 item(s)" in out, out)
check("... each verified on the robot: the same hash and size as here",
      all(library.item_hash(os.path.join(robot_lib, ri[i]["file"])) == ri[i]["sha256"]
          and library.item_bytes(os.path.join(robot_lib, ri[i]["file"])) == ri[i]["bytes"] for i in ri), ri)
check("... and their source and licence travel with them",
      all(ri[i]["source"] == "made up for a test" and ri[i]["licence"] == "CC0" for i in ri))
check("... world-readable, as the robot's services run as another user",
      oct(os.stat(os.path.join(robot_lib, ri["tune-one"]["file"])).st_mode & 0o777) == "0o644")
check("the robot holds the same tool as this computer (both ends run the same code)",
      open(os.path.join(robot_lib, ".tool", "library.py")).read() == open(os.path.join(HERE, "..", "library.py")).read())
check("no staging leftovers", not os.listdir(os.path.join(robot_lib, ".incoming")))
rc, out = push(everything=True)
check("pushing again is content-addressed: what the robot has, identical, is skipped",
      rc == 0 and "moved 1 item(s)" in out and out.count("SKIP") == 2 and set(robot_index()) == {
          "tune-one", "tune-two", "made-up-eyes"}, out)
os.remove(os.path.join(robot_lib, robot_index()["tune-two"]["file"]))
rc, out = push(["tune-two"])
check("an item the robot lists but whose file is gone or damaged is sent again",
      rc == 0 and "missing or damaged" in out and os.path.isfile(os.path.join(robot_lib, robot_index()["tune-two"]["file"])), out)

print("\nlibrary sync: what is refused")
idx = library.load_index(here_lib)
idx["tune-one"]["licence"] = ""
library._write_index(here_lib, list(idx.values()))
rc, out = push(["tune-one", "made-up-eyes"])
check("an item without its licence is refused here, never sent — and the others still go",
      rc == 1 and "REFUSED" in out and "licence" in out and "SKIP" in out, out)
idx["tune-one"]["licence"] = "CC0"
library._write_index(here_lib, list(idx.values()))
with open(os.path.join(here_lib, idx["made-up-eyes"]["file"]), "ab") as fh:
    fh.write(b"!")
rc, out = push(["made-up-eyes"])
check("an item whose file changed here since it was added is refused (its hash no longer matches)",
      rc == 1 and "changed since it was added" in out, out)
with open(os.path.join(here_lib, idx["made-up-eyes"]["file"]), "wb") as fh:
    fh.write(b"not really a png")
# the same id with OTHER content on this computer: a conflict
other = os.path.join(src, "other", "tune-one.wav")
os.makedirs(os.path.dirname(other))
made_up_wav(other, 0.75)
library.remove("tune-one", here_lib)
library.add(other, "tune-one", "song", "made up for a test, take two", "CC0", here_lib)
rc, out = push(["tune-one"])
check("the robot having OTHER content under the same id is a conflict, refused without --replace",
      rc == 1 and "CONFLICT" in out and robot_index()["tune-one"]["sha256"] != library.load_index(here_lib)["tune-one"]["sha256"], out)
rc, out = push(["tune-one"], replace=True)
check("... and replaced with it, verified",
      rc == 0 and "REPLACED" in out and robot_index()["tune-one"]["sha256"] == library.load_index(here_lib)["tune-one"]["sha256"]
      and robot_index()["tune-one"]["source"] == "made up for a test, take two", out)

print("\nlibrary sync: local_only items never leave this computer")
with open(os.path.join(src, "private.png"), "wb") as fh:
    fh.write(b"a private picture, made up")
library.add(os.path.join(src, "private.png"), "private-still", "skin", "made up for a test", "private", here_lib,
            type="image", width=480, height=480, local_only=True)
rc, out = push(["private-still"])
check("a local_only item named for a push is refused, saying why, and nothing is sent",
      rc == 1 and "local_only" in out and "private-still" not in robot_index(), out)
rc, out = push(everything=True)
check("--all leaves local_only items out, says so, and still pushes the rest",
      rc == 0 and "left out: 1 local_only" in out and "private-still" not in robot_index(), out)
idx = library.load_index(here_lib)
idx["private-still"]["local_only"] = "yes"
check("local_only must be a boolean (a string is a problem, not silently true)",
      any("local_only must be true or false" in q for q in library.entry_problems(idx["private-still"])))
idx["private-still"]["local_only"] = True
library._write_index(here_lib, list(idx.values()))

print("\nlibrary sync: the robot end checks what arrives")
t = LS.Target(HOST, LS.REMOTE_ROOT, LS.REMOTE_PYTHON)
e = dict(library.load_index(here_lib)["tune-two"], id="damaged", file="songs/damaged.wav")
buf = io.BytesIO()
with tarfile.open(fileobj=buf, mode="w") as tar:
    data = b"RIFF but not the content the sender hashed"
    ti = tarfile.TarInfo("songs/damaged.wav")
    ti.size = len(data)
    tar.addfile(ti, io.BytesIO(data))
t.tool("stage", "--run", "t1", stdin=buf.getvalue())
r = t.tool("commit", "--run", "t1", stdin=json.dumps([e]).encode())["report"]
check("an item that arrives damaged (its hash differs from the sender's) is refused, nothing written",
      r[0]["action"] == "refused" and "damaged" in r[0]["why"] and "damaged" not in robot_index()
      and not os.path.exists(os.path.join(robot_lib, "songs", "damaged.wav")), r)
buf = io.BytesIO()
with tarfile.open(fileobj=buf, mode="w") as tar:
    ti = tarfile.TarInfo("../../escaped.txt")
    ti.size = 1
    tar.addfile(ti, io.BytesIO(b"x"))
try:
    t.tool("stage", "--run", "t2", stdin=buf.getvalue())
    check("a tar path outside the staging folder is refused", False)
except LS.SyncError as err:
    check("a tar path outside the staging folder is refused",
          "outside" in str(err) and not os.path.exists(os.path.join(robot_lib, "escaped.txt")), str(err))

print("\nlibrary sync: deleting on the robot")
with open(os.path.join(robot_shows, "uses-tune.yaml"), "w") as fh:
    fh.write("id: u\nslots: [HEAD]\nmusic: { song: tune-one, bpm: 120 }\ncues: []\n")
lines = []
rc = LS.delete(HOST, ["tune-one", "tune-two"], LS.REMOTE_ROOT, robot_shows, LS.REMOTE_PYTHON, False, out=lines.append)
out = "\n".join(lines)
check("deleting an item a show on the robot still names is refused, naming the show; the other goes",
      rc == 1 and "uses-tune.yaml" in out and "tune-one" in robot_index() and "tune-two" not in robot_index(), out)
lines = []
LS.delete(HOST, ["made-up-eyes"], LS.REMOTE_ROOT, robot_shows, LS.REMOTE_PYTHON, True, out=lines.append)
check("delete --dry-run deletes nothing", "made-up-eyes" in robot_index() and "WOULD DELETE" in "\n".join(lines))
check("a push never deletes: items the robot has that this computer lacks stay",
      push(["made-up-eyes"])[0] in (0, 1) and "tune-one" in robot_index())

print("\nlibrary sync: an index made before `bytes` existed")
idx = library.load_index(robot_lib)
for v in idx.values():
    v.pop("bytes", None)
library._write_index(robot_lib, list(idx.values()))
check("an entry without bytes is a problem now (the tablet checks its free space before fetching)",
      any("bytes" in p for p in library.entry_problems(next(iter(idx.values())))))
rc, out = push(["made-up-eyes"])
check("a push migrates the robot's index first: every entry gets its bytes, re-verified",
      all(isinstance(v.get("bytes"), int) for v in robot_index().values()) and "robot index:" in out, out)

lines = []
rc = LS.status(HOST, LS.REMOTE_ROOT, LS.REMOTE_PYTHON, out=lines.append)
check("status lists the robot's items, each verified", rc == 0 and "intact" in "\n".join(lines), lines)

shutil.rmtree(tmp)
print()
if FAILS:
    for f in FAILS:
        print("FAILED:", f)
    sys.exit(1)
print("all good")
