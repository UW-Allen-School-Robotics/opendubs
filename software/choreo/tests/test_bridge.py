#!/usr/bin/env python3
"""Checks on the face bridge. No composer, no face, no robot: the Follower is pure, and the one
socket test runs entirely on this machine.

    python3 tests/test_bridge.py
"""
import os
import socket
import sys
import threading

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, ".."))
import face_bridge as fb  # noqa: E402

fails = []


def check(name, cond, detail=""):
    print(f"  {'ok  ' if cond else 'FAIL'}  {name}")
    if not cond:
        fails.append(f"{name}: {detail}")


def ev(t, **cue):
    return {"t": t, "cue": cue}


TL = {"tracks": {
    "FACE": [ev(0.0, do="emote", name="SOFT"), ev(4.0, do="emote", name="ALERT")],
    "SOUND": [ev(0.0, do="play", sound="attend"), ev(2.0, do="play", sound="heard"),
              ev(3.0, do="play", sound="my_song_hit")],
    "LIGHT": [ev(1.0, do="color", rgb=[255, 0, 0], br=50)],
}}

print("face bridge: following the playhead")

f = fb.Follower()
out = f.update(1, TL, 0.0, False)
check("loading a show sends the state in force (the emote), and no sound", out == ["EMOTE SOFT"], out)
check("paused and unchanged sends nothing", f.update(1, TL, 0.0, False) == [])

out = f.update(1, TL, 0.02, True)
check("pressing play at 0 plays the sound at 0", out == ["CUE attend"], out)
out = []
for t in (0.5, 1.0, 1.5, 2.0, 2.5, 3.0, 3.5):
    out += f.update(1, TL, t, True)
check("playing sends each earcon as it is crossed, once", out == ["CUE heard"], out)
check("a LIGHT cue is not sent, and the console is told why",
      any("LIGHT" in m and "StamPLC" in m for m in f.log), f.log)
check("a sound FILE is not sent to the face, and the console is told why",
      any("my_song_hit" in m and "plays on the tablet" in m for m in f.log), f.log)
out = f.update(1, TL, 4.0, True)
check("a FACE cue is sent as it is crossed", out == ["EMOTE ALERT"], out)

out = f.update(1, TL, 1.0, False)
check("seeking back while paused restores the emote then in force, with no sound",
      out == ["EMOTE SOFT"], out)
out = f.update(1, TL, 10.0, True)
check("a big jump while playing is a seek: state, not a burst of replayed cues",
      out == ["EMOTE ALERT"], out)
out = f.update(2, TL, 10.05, True)
check("an edit resynchronises and replays nothing", out == [], out)

empty = {"tracks": {"SOUND": [ev(1.0, do="play", sound="heard")]}}
g = fb.Follower()
check("a show with no FACE cue hands the face back its own cycling",
      g.update(1, empty, 0.0, False) == ["EMOTE AUTO"])

h = fb.Follower()
h.update(1, TL, 0.0, False)
h.update(1, TL, 0.02, True)
out = h.update(1, TL, 4.5, False, "ABORTED")
check("following a runner, an aborted show hands the face back its own behaviour, once",
      out == ["EMOTE AUTO"] and h.update(1, TL, 4.5, False, "ABORTED") == [], out)
check("... and a lost lease does the same",
      fb.Follower().update(1, TL, 2.0, True, "LEASE_LOST") == ["EMOTE AUTO"])

routed = {"tracks": {"SOUND": [{"t": 1.0, "cue": {"do": "play", "sound": "attend"}, "output": "tablet"},
                               {"t": 2.0, "cue": {"do": "play", "sound": "heard"}, "output": "tab5"}]}}
k = fb.Follower()
k.update(1, routed, 0.0, False)
k.update(1, routed, 0.02, True)
out = k.update(1, routed, 0.5, True) + k.update(1, routed, 1.0, True) + k.update(1, routed, 1.5, True) + k.update(1, routed, 2.0, True)
check("only sounds routed to the Tab5 reach the face; the rest are logged with where they play",
      out == ["CUE heard"] and any("plays on the tablet" in m for m in k.log), (out, k.log))

themed = {"tracks": {"FACE": [ev(0.0, do="theme", name="halloween"), ev(0.0, do="emote", name="SQUINT"),
                              ev(5.0, do="theme", name="plain")]}}
m = fb.Follower()
out = m.update(1, themed, 0.0, False)
check("loading a show sends the theme in force, then the emote", out == ["THEME halloween", "EMOTE SQUINT"], out)
m.update(1, themed, 0.02, True)
out = []
for t in (1.0, 3.0, 5.0):
    out += m.update(1, themed, t, True)
check("a theme cue is sent as it is crossed", out == ["THEME plain"], out)
out = m.update(1, themed, 2.0, False)
check("seeking back restores the theme then in force", out == ["THEME halloween"], out)
out = m.update(1, themed, 2.0, False, "ABORTED")
check("an aborted show hands back the emote but leaves the theme (it is dressing, not a state)",
      out == ["EMOTE AUTO"], out)
check("a show with no theme cue never touches the face's theme",
      not any(x.startswith("THEME") for x in fb.Follower().update(1, TL, 0.0, False)))

print("\nface bridge: local only")
for host in ("127.0.0.1", "localhost", "::1"):
    try:
        fb.loopback_only(host)
        check(f"{host} is accepted", True)
    except SystemExit as e:
        check(f"{host} is accepted", False, str(e))
for host in ("10.42.2.14", "192.168.88.1", "8.8.8.8"):
    try:
        fb.loopback_only(host)
        check(f"{host} is refused", False, "accepted a non-local target")
    except SystemExit as e:
        check(f"{host} is refused", "refusing" in str(e), str(e))

print("\nface bridge: the lines arrive (localhost UDP, no face needed)")
rx = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
rx.bind(("127.0.0.1", 0))
rx.settimeout(2.0)
got = []
th = threading.Thread(target=lambda: got.append(rx.recvfrom(512)[0].decode()))
th.start()
tx = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
tx.sendto(b"EMOTE SOFT\n", (fb.loopback_only("127.0.0.1"), rx.getsockname()[1]))
th.join(3)
check("a command line is delivered as one datagram ending in a newline", got == ["EMOTE SOFT\n"], got)

print()
if fails:
    for x in fails:
        print("FAILED:", x)
    sys.exit(1)
print("all good")
