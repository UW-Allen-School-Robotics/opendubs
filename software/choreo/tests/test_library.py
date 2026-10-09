#!/usr/bin/env python3
"""The show library (library.py) and eye skins in shows (plan 4.6).

Every library here is built in a temporary directory from made-up files: no library content —
neither files nor index entries — is ever committed.
"""
from __future__ import annotations

import os
import re
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, ".."))

import cue  # noqa: E402
import face_bridge  # noqa: E402
import library  # noqa: E402

LIMITS = cue.load_limits(os.path.join(HERE, "joint_limits_fixture.yaml"))
FAILS = []


def check(what, ok, detail=""):
    print(("  ok    " if ok else "  FAIL  ") + what)
    if not ok:
        FAILS.append(f"{what}: {detail}")


def refused(fn, *a, **kw):
    try:
        fn(*a, **kw)
        return None
    except ValueError as e:
        return str(e)


tmp = tempfile.mkdtemp(prefix="opendubs-library-test-")
lib = os.path.join(tmp, "library")
src = os.path.join(tmp, "src")
os.makedirs(os.path.join(src, "made-up-folder"))
with open(os.path.join(src, "made-up-folder", "a.svg"), "w") as fh:
    fh.write("<svg/>")
with open(os.path.join(src, "made-up-folder", "b.json"), "w") as fh:
    fh.write("{}")
for name, data in (("made-up-eyes.png", b"not really a png"), ("made-up-clip.mp4", b"not really a video"),
                   ("made-up-video-eyes.webm", b"not really a webm"), ("made-up-video-eyes.mp4", b"nor this")):
    with open(os.path.join(src, name), "wb") as fh:
        fh.write(data)
EYES = os.path.join(src, "made-up-eyes.png")

print("library: adding items")
IMG = dict(lib_root=lib, type="image", width=480, height=480)
check("an item without a source or a licence is refused",
      "source" in (refused(library.add, EYES, "made-up-eyes", "skin", "", "CC0", **IMG) or ""))
check("a library skin may not take a name the face draws in code",
      "code" in (refused(library.add, EYES, "ring", "skin", "made up", "CC0", **IMG) or ""))
check("a skin's type must be one the face can show",
      "type" in (refused(library.add, EYES, "made-up-eyes", "skin", "made up", "CC0",
                         **dict(IMG, type="hologram")) or ""))
check("a params key the face does not know is a problem (a typo would silently do nothing)",
      "unknown params" in (refused(library.add, EYES, "made-up-eyes", "skin", "made up", "CC0", params={"spinn_dps": 90},
                                   **IMG) or ""))
check("a video skin must be WebM: MP4/H.264 cannot carry the transparency a skin needs",
      "WebM" in (refused(library.add, os.path.join(src, "made-up-video-eyes.mp4"), "made-up-video-eyes", "skin",
                         "made up", "CC0", lib_root=lib, type="video", width=480, height=480, fps=30,
                         duration_s=2.0) or ""))
e = library.add(EYES, "made-up-eyes", "skin", "made up for a test", "CC0", params={"spin_dps": 90, "blink": "none"}, **IMG)
check("a skin is COPIED into the library (skins/), never into the repo, with its own hash",
      os.path.isfile(os.path.join(lib, "skins", "made-up-eyes.png")) and e["sha256"] == library.item_hash(EYES), e)
v = library.add(os.path.join(src, "made-up-video-eyes.webm"), "made-up-video-eyes", "skin", "made up", "CC0",
                lib_root=lib, type="video", width=640, height=360, fps=30, duration_s=2.0)
check("a video skin that is not square 400-512 px is a WARNING, not a refusal",
      any("square" in w for w in library.entry_warnings(v)), library.entry_warnings(v))
fh_ = library.item_hash(os.path.join(src, "made-up-folder"))
with open(os.path.join(src, "made-up-folder", "b.json"), "w") as fh:
    fh.write('{"changed": 1}')
check("a folder item's hash is its sorted path<TAB>sha256 lines: any file changing changes it",
      fh_ != library.item_hash(os.path.join(src, "made-up-folder")))
c = library.add(os.path.join(src, "made-up-clip.mp4"), "made-up-clip", "clip", "made up", "CC0",
                lib_root=lib, duration_s=4.0, width=1340, height=800, fps=60)
check("a clip carries its duration and size", c["duration_s"] == 4.0 and (c["width"], c["height"]) == (1340, 800))
check("an id already in the library is refused",
      "already" in (refused(library.add, os.path.join(src, "made-up-clip.mp4"), "made-up-clip", "clip",
                            "made up", "CC0", lib_root=lib, duration_s=4, width=1340, height=800, fps=60) or ""))
check("the library checks clean", library.check(lib) == [], library.check(lib))
with open(os.path.join(lib, "skins", "made-up-eyes.png"), "ab") as fh:
    fh.write(b"!")
check("a file changed in place changes the item's hash, and the check says which item",
      any("made-up-eyes" in p and "changed" in p for p in library.check(lib)), library.check(lib))
with open(os.path.join(lib, "skins", "made-up-eyes.png"), "wb") as fh:
    fh.write(b"not really a png")

print("\nlibrary: skins in shows")
os.environ["OPENDUBS_LIBRARY"] = lib


def show(name):
    return cue.Show({"id": "t", "slots": ["FACE"], "requires": {"tab5": True},
                     "cues": [{"t": 0.0, "slot": "FACE", "do": "skin", "name": name}]}, "test")


tl = show("made-up-eyes").export(LIMITS, None)
check("a library skin validates, and the export records the hash it was validated against",
      tl["valid"] and tl["assets"] == {"made-up-eyes": e["sha256"]}, (tl["problems"], tl["assets"]))
for code_skin in cue.CODE_SKINS:
    check(f"the code-drawn skin {code_skin!r} needs no library entry", show(code_skin).export(LIMITS, None)["valid"])
a = show("no-such-eyes").analyse(LIMITS)
check("a skin in neither the code nor the library is a problem",
      any("no-such-eyes" in q.msg for q in a["problems"]), [q.msg for q in a["problems"]])
check("a clip is not a skin", any("made-up-clip" in q.msg for q in show("made-up-clip").analyse(LIMITS)["problems"]))
os.environ["OPENDUBS_LIBRARY"] = os.path.join(tmp, "nowhere")
a = show("made-up-eyes").analyse(LIMITS)
check("with no library on this machine a library skin is a NOTICE, not a problem (the Thor checks it)",
      not a["problems"] and any("unchecked" in n for n in a["notices"]), (a["problems"], a["notices"]))
os.environ["OPENDUBS_LIBRARY"] = lib

print("\nlibrary: the face bridge sends SKIN")
tl = cue.Show({"id": "t", "slots": ["FACE"], "requires": {"tab5": True},
               "cues": [{"t": 1.0, "slot": "FACE", "do": "skin", "name": "ring"},
                        {"t": 2.0, "slot": "FACE", "do": "skin", "name": "default"}]}, "test").export(LIMITS, None)
f = face_bridge.Follower()
lines = f.update(1, tl, 0.0, True) + f.update(1, tl, 0.4, True) + f.update(1, tl, 0.8, True) \
    + f.update(1, tl, 1.2, True) + f.update(1, tl, 1.6, True) + f.update(1, tl, 2.0, True)
check("a skin cue is sent as SKIN <name> as the playhead crosses it, and `default` returns the eyes",
      [x for x in lines if x.startswith("SKIN")] == ["SKIN ring", "SKIN default"], lines)
g = face_bridge.Follower()
check("a seek sends the skin in force at that time (it is persistent, like a theme)",
      "SKIN ring" in g.update(1, tl, 1.5, False))

print("\nlibrary: the code skins match the face engine's")
idx_js = os.path.join(HERE, "..", "..", "face-web", "engine", "skins", "index.js")
hp = os.path.join(HERE, "..", "..", "head-protocol")
if os.path.isfile(os.path.join(hp, "head_protocol.py")):
    sys.path.insert(0, hp)
    import head_protocol  # noqa: E402
    if hasattr(head_protocol, "CODE_SKINS"):
        check("cue.CODE_SKINS is the head protocol's CODE_SKINS",
              set(head_protocol.CODE_SKINS) == set(cue.CODE_SKINS), head_protocol.CODE_SKINS)
if os.path.isfile(idx_js):
    # `export const CODE_SKINS = { default: eyes, ring };` — an object whose KEYS are the skin names
    # (`ring` is shorthand for `ring: ring`); quoted names in a list are read too.
    m = re.search(r"CODE_SKINS\s*=\s*([\[{(])([^\]})]*)", open(idx_js, encoding="utf-8").read())
    if m and m.group(1) == "{":
        names = tuple(part.split(":")[0].strip().strip("'\"") for part in m.group(2).split(",") if part.strip())
    else:
        names = tuple(re.findall(r"['\"]([\w-]+)['\"]", m.group(2))) if m else ()
    check("cue.CODE_SKINS is face-web's CODE_SKINS", set(names) == set(cue.CODE_SKINS), names)
else:
    print("  skip  face-web's engine/skins/index.js (CODE_SKINS) is not in this tree yet; cue.CODE_SKINS =",
          cue.CODE_SKINS)

print()
if FAILS:
    for x in FAILS:
        print("FAILED:", x)
    sys.exit(1)
print("all good")
