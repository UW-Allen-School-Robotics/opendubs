#!/usr/bin/env python3
"""Checks on the composer's backend: the text-surgical editor and the API functions.

    python3 tests/test_composer.py

No browser, no server, no robot. The property that matters most is the one an agent relies on:
an editor save is a MINIMAL DIFF — untouched text is byte-identical, comments survive, keys keep
their order, and no layout data appears in the show.
"""
import json
import math
import os
import shutil
import sys
import tempfile

import yaml

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "composer"))
import yamledit  # noqa: E402
from api import ApiError, Composer  # noqa: E402

# The timing checks below are written against these caps, not the robot's measured ones (cue.py):
# they test the arithmetic, so the numbers stay fixed whatever the robot's caps become.
sys.path.insert(0, os.path.join(HERE, ".."))
import cue as _pin_caps  # noqa: E402
_pin_caps.SPEED_CAP_DEG_S.update({1: 25, 2: 30, 3: 35, 4: 30, 5: 35, 6: 60, 7: 70})

SHOWS = os.path.join(HERE, "..", "shows")
FIXTURE = os.path.join(HERE, "joint_limits_fixture.yaml")
HELLO = open(os.path.join(SHOWS, "hello.yaml"), encoding="utf-8").read()

fails = []


def check(name, cond, detail=""):
    print(f"  {'ok  ' if cond else 'FAIL'}  {name}")
    if not cond:
        fails.append(f"{name}: {detail}")


def diff_lines(a, b):
    la, lb = a.splitlines(), b.splitlines()
    return [(i, x, y) for i, (x, y) in enumerate(zip(la, lb)) if x != y], len(la) - len(lb)


print("text-surgical editing")

check("no edits is a byte-identical round trip", yamledit.apply(HELLO, []) == HELLO)

doc = yaml.safe_load(HELLO)
c2 = dict(doc["cues"][2])                      # { t:  3.0, slot: ARM_L,  joints: { 2: 20, 4: 55 } }
c2["t"] = 3.5
new = yamledit.apply(HELLO, [{"op": "set", "index": 2, "cue": json.loads(json.dumps(c2))}])
d, n = diff_lines(HELLO, new)
check("retiming a cue changes exactly one line", len(d) == 1 and n == 0, d)
check("... and only the value: the author's alignment survives",
      d and d[0][2] == d[0][1].replace("t:  3.0", "t:  3.5"), d)
check("comments survive an edit", new.count("#") == HELLO.count("#"))
check("the edited show still parses to the edited value",
      yaml.safe_load(new)["cues"][2]["t"] == 3.5)

c2 = json.loads(json.dumps(doc["cues"][2]))
c2["hold_s"] = 1.0
new = yamledit.apply(HELLO, [{"op": "set", "index": 2, "cue": c2}])
d, n = diff_lines(HELLO, new)
line = d[0][2] if d else ""
check("adding a field rewrites that one cue, original key order kept, new key last",
      len(d) == 1 and n == 0 and line.index("t:") < line.index("slot:") < line.index("joints:")
      < line.index("hold_s:"), d)
check("joint numbers stay bare integers when a cue is rewritten",
      yaml.safe_load(new)["cues"][2]["joints"] == {2: 20, 4: 55}, line)

c2 = json.loads(json.dumps(doc["cues"][2]))
del c2["t"]
c2["beat"] = 5
new = yamledit.apply(HELLO, [{"op": "set", "index": 2, "cue": c2}])
check("changing the time form (t -> beat) is a key change and parses back",
      yaml.safe_load(new)["cues"][2].get("beat") == 5 and "t" not in yaml.safe_load(new)["cues"][2])

new = yamledit.apply(HELLO, [{"op": "delete", "index": 0}])
d, n = diff_lines(HELLO, new)
check("deleting a cue removes exactly its line", n == 1 and len(yaml.safe_load(new)["cues"]) ==
      len(doc["cues"]) - 1 and "sound: attend" not in new.splitlines()[19], n)

new = yamledit.apply(HELLO, [{"op": "add", "cue": {"bar": "4.1", "slot": "HEAD", "pan": 70}}])
check("adding a cue appends one line at the end of cues:, with the list's indentation",
      new.startswith(HELLO) and new[len(HELLO):] == '  - { bar: "4.1", slot: HEAD, pan: 70 }\n',
      repr(new[len(HELLO):]))
check("a bar string is quoted so YAML cannot read it as a float",
      yaml.safe_load(new)["cues"][-1]["bar"] == "4.1")

check("strings YAML would misread are quoted", yamledit.fmt("off") == '"off"'
      and yamledit.fmt("release") == "release" and yamledit.fmt("4.1") == '"4.1"')
check("numbers are written in their shortest round-tripping form",
      [yamledit.fmt(x) for x in (3, 3.0, 3.5, 0.125, 1 / 3)] == ["3", "3.0", "3.5", "0.125",
                                                              "0.333333"])

BEAT = open(os.path.join(SHOWS, "beat-demo.yaml"), encoding="utf-8").read()
new = yamledit.apply(BEAT, [{"op": "music", "key": "bpm", "value": 118.5}])
d, n = diff_lines(BEAT, new)
check("tap-tempo's bpm change is one value on one line", len(d) == 1 and n == 0
      and d[0][2].strip() == "bpm: 118.5", d)
new = yamledit.apply(HELLO, [{"op": "music", "key": "bpm", "value": 100}])
check("setting a tempo on a show with no music block adds one, before cues:",
      yaml.safe_load(new)["music"]["bpm"] == 100 and new.count("\n") == HELLO.count("\n") + 1)

try:
    yamledit.apply(HELLO, [{"op": "set", "index": 999, "cue": {"t": 1}}])
    check("an edit to a cue that does not exist is refused", False)
except yamledit.EditError:
    check("an edit to a cue that does not exist is refused", True)

print("\nthe API (what the server serves)")

tmp = tempfile.mkdtemp()
try:
    shutil.copy(os.path.join(SHOWS, "hello.yaml"), tmp)
    shutil.copy(os.path.join(SHOWS, "beat-demo.yaml"), tmp)
    comp = Composer(tmp, FIXTURE)
    check("the test fixture is announced as the test fixture", comp.info()["limits_are_test_fixture"])
    r = comp.load("hello.yaml")
    check("loading a show returns its text, its timeline and where each cue is",
          r["text"] == HELLO and r["timeline"]["valid"] and len(r["cue_lines"]) == len(doc["cues"]))
    bad = HELLO.replace("joints: { 7: 30 } }", "joints: { 7: 300 } }", 1)
    r = comp.analyse("hello.yaml", bad)
    probs = r["timeline"]["problems"]
    check("a violation comes back located on the offending cue",
          not r["timeline"]["valid"] and any(p["cue"] == 9 for p in probs), probs)
    r = comp.analyse("hello.yaml", "cues: [ {t: 1, ")
    check("unparseable text is reported, not raised", r["parse_error"] and r["timeline"] is None)
    for evil in ("../../etc/passwd", "/etc/hosts.yaml", "../cue.py"):
        try:
            comp.load(evil)
            check(f"path {evil!r} is refused", False)
        except ApiError:
            check(f"path {evil!r} is refused", True)
    r = comp.edit("hello.yaml", HELLO, [{"op": "delete", "index": 0}])
    on_disk = open(os.path.join(tmp, "hello.yaml"), encoding="utf-8").read()
    check("an edit does not touch the file until saved", on_disk == HELLO and r["text"] != HELLO)
    comp.save("hello.yaml", r["text"])
    check("save writes exactly the edited text",
          open(os.path.join(tmp, "hello.yaml"), encoding="utf-8").read() == r["text"])
    try:
        comp.save("hello.yaml", "- just a list")
        check("text that is not a show is not saved", False)
    except ApiError:
        check("text that is not a show is not saved",
              open(os.path.join(tmp, "hello.yaml"), encoding="utf-8").read() == r["text"])
    js = {"name": ["openarm_left_joint1", "openarm_left_joint2", "openarm_left_joint4"],
          "position": [math.radians(-10), math.radians(-35), math.radians(80)], "source": "fake"}
    cap = comp.capture("left", js)
    check("capture un-mirrors a measured left pose into keyframe joints",
          cap["joints"] == {"1": 10.0, "2": 35.0, "4": 80.0} and cap["slot"] == "ARM_L", cap)
    # earliest legal time (workplan 1.3)
    r = comp.earliest("hello.yaml", HELLO, "ARM_R", {"4": 100}, 0.0)
    check("a taught pose is placed after the keyframe it actually follows, with the reason",
          r["moved"] and r["t"] == 4.5 and r["free_at"] == 3.0 and "J4" in r["reason"], r)
    r = comp.earliest("hello.yaml", HELLO, "ARM_R", {"4": 55}, 30.0)
    check("a pose reachable at the playhead stays at the playhead", not r["moved"] and r["t"] == 30.0, r)
    r = comp.earliest("hello.yaml", HELLO, "ARM_L", {"2": 60}, 12.0)
    check("inserting a pose warns when it makes the NEXT keyframe too fast",
          r["next_problem"] and "too fast" in r["next_problem"], r)
    idx = next(i for i, c in enumerate(doc["cues"]) if c.get("slot") == "ARM_R" and c.get("t") == 7.0)
    r = comp.earliest("hello.yaml", HELLO, "ARM_R", doc["cues"][idx]["joints"], index=idx)
    check("re-timing a keyframe leaves it out and answers from the keyframe before it",
          r["free_at"] == 3.0 and abs(r["t"] - 3.8) < 1e-6, r)
    tl = comp.load("beat-demo.yaml")["timeline"]
    check("beat-demo loads valid, with its clips expanded and markers resolved",
          tl["valid"] and len(tl["tracks"]["HEAD"]) == 18 and tl["markers"][0]["t"] == 16.5)
finally:
    shutil.rmtree(tmp)

print("\nmusic: the library's songs, from the composer (plan 4.6)")
import io  # noqa: E402
import threading  # noqa: E402
import urllib.error  # noqa: E402
import urllib.request  # noqa: E402
import wave  # noqa: E402

import api as composer_api  # noqa: E402


def wav_bytes(secs=0.5, rate=8000):
    b = io.BytesIO()
    with wave.open(b, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(b"\x00\x01" * int(secs * rate))
    return b.getvalue()


def api_error(fn, *a):
    try:
        fn(*a)
        return None
    except ApiError as e:
        return (e.status, str(e))


tmp = tempfile.mkdtemp()
saved_env = os.environ.get("OPENDUBS_LIBRARY")
try:
    shows, lib = os.path.join(tmp, "shows"), os.path.join(tmp, "library")
    os.makedirs(shows)
    shutil.copy(os.path.join(SHOWS, "hello.yaml"), shows)
    comp = Composer(shows, FIXTURE, lib)
    check("the library starts empty and is said to be", comp.songs() == [] and not comp.info()["library_exists"])
    W = wav_bytes()
    for what, args, code in (
            ("a file type that is not a song", ("x.exe", W, "x", "s", "l"), 415),
            ("a song file whose content is not that type", ("x.mp3", b"just text", "x", "s", "l"), 415),
            ("a song without its licence", ("x.wav", W, "x", "made up", " "), 400),
            ("a song without its source", ("x.wav", W, "x", "", "CC0"), 400),
            ("an id that is not a safe name", ("x.wav", W, "../x", "s", "l"), 400),
            ("an empty file", ("x.wav", b"", "x", "s", "l"), 400)):
        err = api_error(comp.add_song, *args)
        check(f"refused: {what}", err is not None and err[0] == code, err)
    big = composer_api.MAX_SONG_BYTES
    composer_api.MAX_SONG_BYTES = 100
    err = api_error(comp.add_song, "x.wav", W, "x", "s", "l")
    composer_api.MAX_SONG_BYTES = big
    check("refused: a song over the size limit (413)", err is not None and err[0] == 413, err)
    check("nothing refused reached the library", comp.songs() == [])
    r = comp.add_song("My Tune.wav", W, "my-tune", "made up for a test", "CC0")
    sg = r["songs"][0]
    check("a song is added to the LIBRARY under its id, with its source, licence and length (read from the WAV)",
          r["added"] == "my-tune" and sg["file"] == "songs/my-tune.wav" and sg["duration_s"] == 0.5
          and sg["licence"] == "CC0" and os.path.isfile(os.path.join(lib, "songs", "my-tune.wav"))
          and not os.path.exists(os.path.join(shows, "songs")), sg)
    check("... and the same id again is refused", api_error(comp.add_song, "x.wav", W, "my-tune", "s", "l") is not None)
    p_, ctype = comp.song_audio("my-tune")
    check("the editor can play a library song", ctype == "audio/wav" and open(p_, "rb").read() == W)
    text = open(os.path.join(shows, "hello.yaml"), encoding="utf-8").read()
    r = comp.edit("hello.yaml", text, [{"op": "music", "key": "song", "value": "my-tune"},
                                       {"op": "music", "key": "file", "value": None}])
    m = r["timeline"]["music"]
    check("attaching a song is an edit of the show's music block: song in, file out, tempo kept (or 120 BPM if new)",
          m["song"] == "my-tune" and m["file"] is None
          and m["bpm"] == (yaml.safe_load(text).get("music") or {}).get("bpm", 120)
          and r["timeline"]["valid"], (m, r["timeline"]["problems"]))
    comp.save("hello.yaml", r["text"])
    check("the song lists the show that uses it", comp.songs()[0]["used_by"] == ["hello.yaml"])
    err = api_error(comp.delete_song, "my-tune", "my-tune")
    check("deleting a song a show still uses is refused (409), naming the show",
          err and err[0] == 409 and "hello.yaml" in err[1] and os.path.isfile(os.path.join(lib, "songs", "my-tune.wav")), err)
    r2 = comp.edit("hello.yaml", r["text"], [{"op": "music", "key": "song", "value": None}])
    m2 = r2["timeline"]["music"]
    check("removing the music from a show drops the reference only: the grid stays, the song stays in the library",
          m2 and m2["song"] is None and m2["bpm"] == m["bpm"] and os.path.isfile(os.path.join(lib, "songs", "my-tune.wav")), m2)
    comp.save("hello.yaml", r2["text"])
    check("deleting needs confirm = the song's id", api_error(comp.delete_song, "my-tune", True) is not None)
    r = comp.delete_song("my-tune", "my-tune")
    check("once unused, a confirmed delete removes the song's file and entry",
          r["songs"] == [] and not os.path.exists(os.path.join(lib, "songs", "my-tune.wav")))

    # the same through the standalone server: the file is the body, what describes it is the query
    import server as composer_server  # noqa: E402
    from http.server import ThreadingHTTPServer  # noqa: E402
    from urllib.parse import urlencode  # noqa: E402
    srv = ThreadingHTTPServer(("127.0.0.1", 0), composer_server.make_handler(comp))
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{srv.server_address[1]}"

    def post(path, body, ctype="application/octet-stream"):
        req = urllib.request.Request(base + path, data=body, method="POST", headers={"Content-Type": ctype})
        try:
            with urllib.request.urlopen(req, timeout=10) as resp:
                return resp.status, json.loads(resp.read())
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read() or b"{}")

    q = urlencode({"filename": "tune.wav", "id": "tune", "source": "made up", "licence": "CC0"})
    code, j = post("/api/library/songs?" + q, W)
    check("over HTTP: an upload adds the song", code == 200 and j["added"] == "tune", (code, j))
    with urllib.request.urlopen(base + "/library/song/tune", timeout=10) as resp:
        check("over HTTP: the song plays back from the library", resp.read() == W)
    composer_server.MAX_SONG_BYTES = 100
    code, j = post("/api/library/songs?" + q.replace("tune", "tune2"), W)
    composer_server.MAX_SONG_BYTES = big
    check("over HTTP: an upload over the limit is refused by its declared length (413)", code == 413, (code, j))
    code, j = post("/api/library/songs/delete", json.dumps({"id": "tune", "confirm": "tune"}).encode(), "application/json")
    check("over HTTP: a confirmed delete", code == 200 and j["deleted"] == "tune", (code, j))

    # A user's MP3 (ID3v2.3, a GEOB frame first) failed with a bare "Load failed". A made-up one of the
    # same shape: the server reads its length itself, with no duration from the browser.
    geob = b"\x00application/octet-stream\x00x.bin\x00made up\x00" + bytes(2000)
    tag = b"GEOB" + len(geob).to_bytes(4, "big") + b"\x00\x00" + geob
    n = len(tag)
    MP3 = (b"ID3\x03\x00\x00" + bytes([(n >> 21) & 0x7F, (n >> 14) & 0x7F, (n >> 7) & 0x7F, n & 0x7F]) + tag
           + (b"\xff\xfb\x90\x00" + bytes(413)) * 120)
    q = urlencode({"filename": "made up.mp3", "id": "made-up-mp3", "source": "made up", "licence": "CC0"})
    code, j = post("/api/library/songs?" + q, MP3)
    check("over HTTP: an MP3 with an ID3v2.3+GEOB tag is added with NO duration from the browser — the "
          "server reads it", code == 200 and abs(j["songs"][0]["duration_s"] - 120 * 1152 / 44100) < 0.05, (code, j))
    import http.client  # noqa: E402
    hc = http.client.HTTPConnection("127.0.0.1", srv.server_address[1], timeout=10)
    hc.putrequest("POST", "/api/library/songs?" + q.replace("made-up-mp3", "chunked-mp3"))
    hc.putheader("Content-Type", "application/octet-stream")
    hc.putheader("Transfer-Encoding", "chunked")
    hc.endheaders()
    for k in range(0, len(MP3), 4096):
        part = MP3[k:k + 4096]
        hc.send(f"{len(part):x}\r\n".encode() + part + b"\r\n")
    hc.send(b"0\r\n\r\n")
    resp = hc.getresponse()
    check("over HTTP: a CHUNKED upload (no Content-Length) is read too", resp.status == 200, (resp.status, resp.read()[:200]))
    hc.close()
    hc = http.client.HTTPConnection("127.0.0.1", srv.server_address[1], timeout=10)
    hc.putrequest("POST", "/api/library/songs?" + q.replace("made-up-mp3", "nolen"), skip_accept_encoding=True)
    hc.endheaders()
    resp = hc.getresponse()
    body = json.loads(resp.read() or b"{}")
    check("over HTTP: an upload with no length at all gets a readable 411, not a dropped connection",
          resp.status == 411 and "length" in body.get("error", ""), (resp.status, body))
    hc.close()
    real_songs = comp.songs
    comp.songs = lambda: 1 / 0
    try:
        with urllib.request.urlopen(base + "/api/library/songs", timeout=10) as resp:
            code, j = resp.status, json.loads(resp.read())
    except urllib.error.HTTPError as e:
        code, j = e.code, json.loads(e.read() or b"{}")
    comp.songs = real_songs
    check("over HTTP: an unexpected error in the composer comes back as a 500 the editor can show, never "
          "a dropped connection (a browser's bare \"Load failed\")",
          code == 500 and "ZeroDivisionError" in j.get("error", ""), (code, j))
    srv.shutdown()
finally:
    if saved_env is None:
        os.environ.pop("OPENDUBS_LIBRARY", None)
    else:
        os.environ["OPENDUBS_LIBRARY"] = saved_env
    shutil.rmtree(tmp)

print("\nthe simulator preview's sampling (no MuJoCo needed)")
sys.path.insert(0, os.path.join(HERE, ".."))
import cue  # noqa: E402
import sim_preview  # noqa: E402

tl = cue.load_show(os.path.join(SHOWS, "hello.yaml")).export(cue.load_limits(FIXTURE))
s = sim_preview.sample(tl, 1.5)
check("halfway into the first move, the arm is halfway there",
      abs(s["right"]["4"] - 27.5) < 1e-6 and abs(s["left"]["2"] - 10.0) < 1e-6, s)
s = sim_preview.sample(tl, 12.0)
check("the lift has risen for its 6 s by t=12 (placeholder speed, clamped to the stroke)",
      abs(s["lift_mm"] - min(275.1, 6 * sim_preview.LIFT_MM_PER_S)) < 1e-6, s["lift_mm"])
check("joints are handed to sim/scene.py in each arm's own sense",
      "left:2=20.0" in sim_preview.joints_arg(sim_preview.sample(tl, 3.0)))
_ev = [{"t": 1.0, "start": 1.0, "end": 3.0, "cue": {"move": "sway", "secs": 2}}]
_mid = sim_preview.base_pose(_ev, 1.5)
check("a dance move displaces the base while it runs", abs(_mid[1]) > 0.05, _mid)
check("... and the base is back where it started when the move ends",
      all(abs(v) < 1e-9 for v in sim_preview.base_pose(_ev, 2.999999)) or
      max(abs(v) for v in sim_preview.base_pose(_ev, 2.999999)) < 1e-4, sim_preview.base_pose(_ev, 2.999999))
check("before and after a move the base is at the origin",
      sim_preview.base_pose(_ev, 0.5) == (0.0, 0.0, 0.0) and sim_preview.base_pose(_ev, 3.5) == (0.0, 0.0, 0.0))
n = sum(1 for _ in sim_preview.frames(tl, 10))
check("frames cover the whole show at the requested rate", n == int(tl["duration_s"] * 10) + 1, n)

print("\nnothing in the composer can command the robot")
src = ""
for root, _, files in os.walk(os.path.join(HERE, "..", "composer")):
    for f in files:
        if f.endswith((".py", ".js", ".html")):
            src += open(os.path.join(root, f), encoding="utf-8").read()
src += open(os.path.join(HERE, "..", "twin_view.py"), encoding="utf-8").read()
src += open(os.path.join(HERE, "..", "ik.py"), encoding="utf-8").read()     # it runs inside the composer on save
# A tripwire, not a proof: the words that every robot-facing path in this repo uses.
for needle in ("socket.socket", "SOCK_DGRAM", "4242", "5005", "MAST CMD", "LED_COLOR ",
               "rclpy", "create_publisher", "/api/relay", "/api/mast", "/api/arms", "sendto("):
    check(f"no {needle!r} in composer source", needle not in src)

print()
if fails:
    for f in fails:
        print("FAILED:", f)
    sys.exit(1)
print("all good")
