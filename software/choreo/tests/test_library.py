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
check("no library item may be called idle (SKIN IDLE is the robot's idle skin, head protocol v5)",
      "reserved" in (refused(library.add, EYES, "idle", "skin", "made up", "CC0", **IMG) or ""))
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
check("a spinning skin's counter-rotation (face-web #138) is a known param, and its shape is checked",
      not library.entry_problems({"id": "x", "kind": "skin", "file": "f.png", "sha256": "0", "bytes": 1, "source": "s", "licence": "l",
                                  "type": "image", "width": 480, "height": 480,
                                  "params": {"spin_dps": 90, "counter": {"every_s": [8, 20], "hold_s": [2, 4], "ease_s": 0.9}}})
      and all(library._counter_problems("x", bad) for bad in
              ({"every_s": [20, 8]}, {"every": [8, 20]}, {"ease_s": -1}, {"hold_s": 3}, "sometimes")))
check("... and `counter` off (absent, null or false) is fine", not library._counter_problems("x", None)
      and not library._counter_problems("x", False))
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

print("\nlibrary: songs (the composer's music, plan 4.6)")
import wave  # noqa: E402


def made_up_wav(path, secs=0.5, rate=8000):
    with wave.open(path, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(b"\x00\x01" * int(secs * rate))


WAV = os.path.join(src, "made-up-tune.wav")
made_up_wav(WAV)
with open(os.path.join(src, "not-a-song.mp3"), "w") as fh:
    fh.write("this is text with an mp3 name")
check("a song is recognised by its CONTENT, not its name",
      library.looks_like_song(WAV) and not library.looks_like_song(os.path.join(src, "not-a-song.mp3")))
song = library.add(WAV, "made-up-tune", "song", "made up for a test", "CC0", lib_root=lib)
check("a WAV's length is read from its header when ffprobe is not installed (and it is a song's required field)",
      song["duration_s"] == 0.5 and os.path.isfile(os.path.join(lib, "songs", "made-up-tune.wav")), song)
check("songs are listed by kind", [x["id"] for x in library.items_of("song", lib)] == ["made-up-tune"])
shows = os.path.join(tmp, "shows")
os.makedirs(os.path.join(shows, "sub"))
with open(os.path.join(shows, "sub", "uses-it.yaml"), "w") as fh:
    fh.write("id: u\nslots: [HEAD]\nmusic: { song: made-up-tune, bpm: 120 }\ncues: []\n")
with open(os.path.join(shows, "other.yaml"), "w") as fh:
    fh.write("id: o\nslots: [HEAD]\nmusic: { bpm: 120 }\ncues: []\n")
check("the shows that use a song are found, and only those", library.shows_using("made-up-tune", shows) == ["sub/uses-it.yaml"])
msg = refused(library.remove, "made-up-tune", lib, used_by=library.shows_using("made-up-tune", shows)) or ""
check("a song still used by a show is NOT deleted, and the refusal names the show",
      "sub/uses-it.yaml" in msg and os.path.isfile(os.path.join(lib, "songs", "made-up-tune.wav")), msg)

os.environ["OPENDUBS_LIBRARY"] = lib


def music_show(music):
    return cue.Show({"id": "m", "slots": ["HEAD"], "requires": {"tab5": True, "rc_authority": "AUTO"}, "music": music,
                     "cues": [{"beat": 1, "slot": "HEAD", "pan": 80}]}, "test")


tl = music_show({"song": "made-up-tune", "bpm": 120}).export(LIMITS, None)
check("music: song: names a library song; the export carries it and the hash it was checked against",
      tl["valid"] and tl["music"]["song"] == "made-up-tune" and tl["assets"].get("made-up-tune") == song["sha256"],
      (tl["problems"], tl["music"], tl["assets"]))
a = music_show({"song": "no-such-tune", "bpm": 120}).analyse(LIMITS)
check("a song that is not in the library is a problem", any("no-such-tune" in q.msg for q in a["problems"]))
a = music_show({"song": "made-up-eyes", "bpm": 120}).analyse(LIMITS)
check("a skin is not a song", any("made-up-eyes" in q.msg for q in a["problems"]))
a = music_show({"song": "made-up-tune", "file": "audio/x.wav", "bpm": 120}).analyse(LIMITS)
check("song and file together are a problem: which would play?", any("not both" in q.msg for q in a["problems"]))
os.environ["OPENDUBS_LIBRARY"] = os.path.join(tmp, "nowhere")
a = music_show({"song": "made-up-tune", "bpm": 120}).analyse(LIMITS)
check("with no library on this machine a song is a NOTICE, not a problem (the Thor checks it)",
      not a["problems"] and any("song 'made-up-tune' is unchecked" in n for n in a["notices"]), a["notices"])
os.environ["OPENDUBS_LIBRARY"] = lib
import runner  # noqa: E402
sh = os.path.join(shows, "sub", "uses-it.yaml")
s_ = cue.load_show(sh)
items, skipped = runner.audio_items(s_, s_.export(LIMITS, None))
check("on the robot, a show's song is played from the library: the dashboard serves it under media/library/",
      items == [{"url": "/api/choreo/media/library/songs/made-up-tune.wav", "at": 0.0, "kind": "music"}] and not skipped,
      (items, skipped))
os.remove(sh)
gone = library.remove("made-up-tune", lib, used_by=library.shows_using("made-up-tune", shows))
check("once no show uses it, a song is deleted: its file and its entry",
      gone["id"] == "made-up-tune" and not os.path.exists(os.path.join(lib, "songs", "made-up-tune.wav"))
      and "made-up-tune" not in library.load_index(lib) and library.check(lib) == [], library.check(lib))
s_ = cue.load_show(os.path.join(shows, "other.yaml"))
check("... and a show naming a song this robot does not have plays silently, saying why",
      runner.audio_items(cue.Show(dict(s_.doc, music={"song": "made-up-tune", "bpm": 120}), sh),
                         {"music": {"song": "made-up-tune", "output": "tablet"}})[1]
      == [f"song made-up-tune is not in this robot's library ({lib})"])

print("\nlibrary: a song's length, read from the file itself (no ffprobe, no browser)")
SYN = os.path.join(tmp, "synthetic-audio")
os.makedirs(SYN)


def syncsafe(n):
    return bytes([(n >> 21) & 0x7F, (n >> 14) & 0x7F, (n >> 7) & 0x7F, n & 0x7F])


def id3v23_with_geob():
    """An ID3v2.3 tag whose FIRST frame is a GEOB (embedded object) — the shape that made a user's
    MP3 fail — then a text frame. Made up here; no real song."""
    geob = b"\x00" + b"application/octet-stream\x00" + b"x.bin\x00" + b"made up\x00" + bytes(3000)
    tit2 = b"\x00made-up title"
    frames = b"GEOB" + len(geob).to_bytes(4, "big") + b"\x00\x00" + geob + \
             b"TIT2" + len(tit2).to_bytes(4, "big") + b"\x00\x00" + tit2
    return b"ID3\x03\x00\x00" + syncsafe(len(frames)) + frames


FRAME = 417                                     # MPEG-1 layer III, 128 kbps, 44.1 kHz, no padding
HDR = b"\xff\xfb\x90\x00"


def mp3(path, frames, xing=None):
    body = bytearray()
    for k in range(frames):
        f = bytearray(HDR + bytes(FRAME - 4))
        if k == 0 and xing is not None:          # a VBR header in the first frame: its frame count
            f[36:48] = b"Xing" + (1).to_bytes(4, "big") + xing.to_bytes(4, "big")
        body += f
    with open(path, "wb") as fh:
        fh.write(id3v23_with_geob() + bytes(body) + b"TAG" + bytes(125))


mp3(os.path.join(SYN, "cbr.mp3"), 200)
mp3(os.path.join(SYN, "vbr.mp3"), 50, xing=1000)
with open(os.path.join(SYN, "a.flac"), "wb") as fh:          # STREAMINFO: 44.1 kHz, 441000 samples
    si = bytearray(34)
    si[10:13] = bytes([44100 >> 12 & 0xFF, 44100 >> 4 & 0xFF, (44100 & 0x0F) << 4 | 0x02])
    si[13:18] = bytes([0x10]) + (441000).to_bytes(4, "big")
    fh.write(b"fLaC" + bytes([0x80, 0, 0, 34]) + si + bytes(100))


def atom(kind, body):
    return (8 + len(body)).to_bytes(4, "big") + kind + body


mvhd = b"\x00\x00\x00\x00" + bytes(8) + (1000).to_bytes(4, "big") + (12345).to_bytes(4, "big") + bytes(80)
with open(os.path.join(SYN, "a.m4a"), "wb") as fh:            # mdat BEFORE moov, as many encoders write it
    fh.write(atom(b"ftyp", b"M4A \x00\x00\x00\x00") + atom(b"mdat", bytes(5000)) + atom(b"moov", atom(b"mvhd", mvhd)))


def ogg_page(granule, payload):
    return b"OggS\x00\x00" + granule.to_bytes(8, "little") + bytes(12) + bytes([1, len(payload)]) + payload


with open(os.path.join(SYN, "a.ogg"), "wb") as fh:            # Vorbis, 44.1 kHz, 20 s
    fh.write(ogg_page(0, b"\x01vorbis" + bytes(4) + b"\x02" + (44100).to_bytes(4, "little") + bytes(10))
             + ogg_page(882000, bytes(50)))
with open(os.path.join(SYN, "opus.ogg"), "wb") as fh:         # Opus: always 48 kHz, less the pre-skip
    fh.write(ogg_page(0, b"OpusHead\x01\x02" + (312).to_bytes(2, "little") + bytes(9))
             + ogg_page(480312, bytes(50)))
for name, want, tol in (("cbr.mp3", 200 * 1152 / 44100, 0.05), ("vbr.mp3", 1000 * 1152 / 44100, 0.001),
                        ("a.flac", 10.0, 0.001), ("a.m4a", 12.345, 0.001), ("a.ogg", 20.0, 0.001),
                        ("opus.ogg", 10.0, 0.001)):
    got = library.song_seconds(os.path.join(SYN, name))
    check(f"{name}: {want:.3f} s read from the file (got {got})", got is not None and abs(got - want) <= tol)
check("an ID3v2.3 tag with a GEOB frame first is skipped, and the file is still recognised as MP3",
      library.looks_like_song(os.path.join(SYN, "cbr.mp3")))
with open(os.path.join(SYN, "junk.mp3"), "wb") as fh:
    fh.write(b"ID3\x03\x00\x00" + syncsafe(10) + bytes(10) + b"no audio frames at all")
check("a file with no audio in it has no length (None), never a guess",
      library.song_seconds(os.path.join(SYN, "junk.mp3")) is None)

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


def js_object_keys(text, name=None, inside=None):
    """The top-level keys of a JS object literal: the one assigned to `name` (an exported constant),
    or else the first `{...}` after `inside` (the literal before it was exported)."""
    if name and (m := re.search(name + r"\s*=\s*(?:Object\.freeze\()?\{", text)):
        i = m.end()
    elif inside and (j := text.find(inside)) >= 0:
        i = text.index("{", text.index("{", j) + 1) + 1 if inside.endswith("(") else text.index("{", j) + 1
    else:
        return None
    depth, key, keys, k = 0, "", [], i
    while k < len(text):
        c = text[k]
        if c in "{[(":
            depth += 1
        elif c in "}])":
            if depth == 0:
                break
            depth -= 1
        elif depth == 0 and c == ":" and key.strip():
            keys.append(key.strip().strip("'\"").split()[-1])
            key = ""
        elif depth == 0 and c == ",":
            key = ""
        elif depth == 0 and not key.strip().startswith("..."):
            key += c
        k += 1
    return keys


print("\nlibrary: a skin's params are the face engine's own (face-web asset.js)")
asset_js = os.path.join(HERE, "..", "..", "face-web", "engine", "skins", "asset.js")
if os.path.isfile(asset_js):
    text = open(asset_js, encoding="utf-8").read()
    # the exported ASSET_PARAM_DEFAULTS (Face & HRI, #165), else the defaults literal in makeAssetSkin
    keys = js_object_keys(text, "ASSET_PARAM_DEFAULTS") or js_object_keys(text, inside="makeAssetSkin(")
    check("library.py accepts exactly the skin params the face engine declares (a typo is caught; a new "
          "engine param makes this fail until library.py knows it)",
          keys is not None and set(keys) == set(library.SKIN_PARAMS), (keys, sorted(library.SKIN_PARAMS)))
    ck = js_object_keys(text, "ASSET_COUNTER_DEFAULTS")
    if ck is not None:
        check("... and the counter block's keys are the engine's too", set(ck) == set(library.COUNTER_KEYS), ck)
else:
    print("  skip  face-web's engine/skins/asset.js is not in this tree")

print("\nlibrary: tags (ambient music)")
library.add(WAV, "tag-tune", "song", "made up for a test", "CC0", lib_root=lib)
e = library.tag("tag-tune", "ambient", lib_root=lib)
check("a song is tagged ambient, and the tag is in its index entry",
      e.get("tags") == ["ambient"] and library.load_index(lib)["tag-tune"].get("tags") == ["ambient"], e)
library.tag("tag-tune", "ambient", lib_root=lib)
check("tagging twice keeps one tag", library.load_index(lib)["tag-tune"]["tags"] == ["ambient"])
library.tag("tag-tune", "ambient", on=False, lib_root=lib)
check("untagging takes the tag (and an empty tags key) off", "tags" not in library.load_index(lib)["tag-tune"])
try:
    library.tag("tag-tune", "Ambient Music", lib_root=lib)
    check("a tag that is not a short lower-case word is refused", False)
except ValueError:
    check("a tag that is not a short lower-case word is refused", True)
check("an index entry's tags must be short lower-case words",
      any("tags must be" in q for q in library.entry_problems({"id": "x", "kind": "song", "tags": ["Has Space"]})))

print()
if FAILS:
    for x in FAILS:
        print("FAILED:", x)
    sys.exit(1)
print("all good")
