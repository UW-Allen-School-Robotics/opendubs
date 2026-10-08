#!/usr/bin/env python3
"""The face bridge: follows the composer's playhead and sends the face its show cues (plan 3.3).

    ./face.sh                                    # the face twin plus this bridge, in one command
    python3 face_bridge.py                       # composer on :8091, face twin on 127.0.0.1:4242
    python3 face_bridge.py --dry-run             # print the lines, send nothing

The face twin is the head's own face code built for the desktop (the Tab5 tree's desktop build); it
shares the head's command parser, so a line it accepts the head accepts. face_stub.py stands in where
that build is not available.

It reads the same feed as the MuJoCo body twin (`/api/twin`, `/api/twin/timeline`) and turns the
show's FACE and SOUND cues into the head's own command lines (`face-twin.md`, FT-2 to FT-4):

    FACE  { do: emote, name: SOFT }        ->  EMOTE SOFT
    (no FACE emote yet at this time)       ->  EMOTE AUTO      (the face's own cycling)
    FACE  { do: theme, name: halloween }   ->  THEME halloween (it stays after the show)
    SOUND { do: play, sound: attend }      ->  CUE attend      (one of the Tab5's six earcons)

While playing, each cue is sent as the playhead crosses it. On a seek, a pause, a backwards jump or
an edit, the bridge instead sends the STATE AT THAT TIME — the emote and theme in force — so jumping
around a show leaves the face correct. Before a show's first theme cue the face keeps whatever theme
it had: a theme is dressing, not a state the show owns. Sounds are only ever sent while playing, never on a seek.

Not sent, and said so on the console: LIGHT cues (the PRISM strip is not the face; the robot
drives it through the StamPLC, which a bridge must never touch) and SOUND cues naming a sound
FILE (they play on the tablet or the Thor, whose audio path does not exist yet).

This command-line bridge is LOCAL ONLY: it refuses any target that is not this machine. The real
head is driven by the show runner itself, through LiveFace below, on live runs (5.6). Neither ever
sends motion.
"""
from __future__ import annotations

import argparse
import ipaddress
import json
import os
import socket
import sys
import time
import urllib.error
import urllib.request

POLL_S = 0.05
SEEK_JUMP_S = 0.5      # a forward step bigger than this while playing is a seek, not playback
EARCONS = ("heard", "attend", "refuse", "fault", "mode", "boot")   # the Tab5's Cue enum, by name


class Follower:
    """Playhead updates in, head command lines out. No I/O, so it can be tested alone.

    `log` collects the cues that were deliberately NOT sent, with the reason, for the console."""

    def __init__(self):
        self.rev = None
        self.tl = None
        self.t = None
        self.playing = False
        self.sent = {}             # what the face was last told: {"emote": "EMOTE SOFT", "theme": …}
        self.log: list[str] = []

    # -- what is in force at a time --------------------------------------------------------
    def _track(self, slot):
        return (self.tl or {}).get("tracks", {}).get(slot, [])

    def _emote_at(self, t):
        line = "EMOTE AUTO"
        for e in self._track("FACE"):
            if e["t"] > t + 1e-9:
                break
            if e["cue"].get("do") == "emote" and e["cue"].get("name"):
                line = f"EMOTE {e['cue']['name']}"
        return line

    def _theme_at(self, t):
        line = None                # before any theme cue: leave the face's theme alone
        for e in self._track("FACE"):
            if e["t"] > t + 1e-9:
                break
            if e["cue"].get("do") == "theme" and e["cue"].get("name"):
                line = f"THEME {e['cue']['name']}"
        return line

    def _sync(self, t):
        """Lines that bring the face to the state in force at t, sending only what changed."""
        out = []
        theme = self._theme_at(t)
        if theme and self.sent.get("theme") != theme:
            self.sent["theme"] = theme
            out.append(theme)
        emote = self._emote_at(t)
        if self.sent.get("emote") != emote:
            self.sent["emote"] = emote
            out.append(emote)
        return out

    def _crossed(self, t0, t1):
        """Cues with t0 < t <= t1, in time order, as lines; unsendable ones go to the log."""
        evs = []
        for slot in ("FACE", "SOUND", "LIGHT"):
            evs += [(e["t"], slot, e) for e in self._track(slot) if t0 < e["t"] <= t1 + 1e-9]
        out = []
        for t, slot, e in sorted(evs, key=lambda x: x[0]):
            c = e["cue"]
            if slot == "FACE" and c.get("do") in ("emote", "theme"):
                line = f"{c['do'].upper()} {c['name']}"
                self.sent[c["do"]] = line
                out.append(line)
            elif slot == "SOUND" and c.get("do") == "play":
                # Routed by the validator (cue.py sound_route): only the Tab5's sounds go to the face.
                route = e.get("output") or ("tab5" if c.get("sound") in EARCONS else "tablet")
                if route == "tab5" and c.get("sound") in EARCONS:
                    out.append(f"CUE {c['sound']}")
                else:
                    self.log.append(f"t={t:.2f} SOUND {c.get('sound')!r} plays on the {route}: not "
                                    "sent to the face (the tablet and Thor audio paths are not built)")
            elif slot == "LIGHT":
                self.log.append(f"t={t:.2f} LIGHT {c.get('do')} {c.get('rgb', '')}: not sent "
                                "(the PRISM strip is driven by the StamPLC, never by a bridge)")
        return out

    # -- the one entry point ---------------------------------------------------------------
    def update(self, rev, timeline, t, playing, status=None) -> list[str]:
        out = []
        # Following a runner: when its show ends — aborted, e-stopped, finished, or its lease lost —
        # the face is handed back its own behaviour (runner.ABORT_STATE["FACE"]), once.
        if status in ("ABORTED", "ESTOPPED", "COMPLETED", "LEASE_LOST"):
            if self.sent.get("emote") != "EMOTE AUTO":
                self.sent["emote"] = "EMOTE AUTO"
                out.append("EMOTE AUTO")
            self.playing, self.t = False, float(t)
            return out
        if rev != self.rev:
            self.rev, self.tl = rev, timeline
            self.t = None                       # an edit: resynchronise, never replay
        prev, self.t, was_playing, self.playing = self.t, float(t), self.playing, bool(playing)
        if self.tl is None:
            return out
        continuous = (prev is not None and playing and was_playing
                      and 0.0 <= self.t - prev <= SEEK_JUMP_S)
        if continuous:
            out += self._crossed(prev, self.t)
        elif prev is None or self.t != prev or playing != was_playing:
            out += self._sync(self.t)
            if playing and not was_playing and prev is not None and 0.0 <= self.t - prev <= SEEK_JUMP_S:
                # play pressed: sounds from where it was paused (inclusive) up to now are due
                out += [ln for ln in self._crossed(prev - 1e-6, self.t) if ln.startswith("CUE")]
        return out


class _HeadLink:
    """One UDP line link to the head: send a line, and read whatever the head has answered since.
    Never raises — the head is never a reason to stop a show — but says what went wrong, once."""

    def __init__(self, host: str, port: int, sock, note, kind: str):
        self.host, self.port, self.sock, self.kind = host, port, sock, kind
        self.note = note or (lambda **kw: None)     # the runner's log: note(kind, **fields)
        self._addr, self._said = None, set()

    def _once(self, key, **kw):
        if key not in self._said:
            self._said.add(key)
            self.note(**kw)

    def _send(self, line: str, log: bool = True):
        try:
            if self.sock is None:
                self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
                self.sock.setblocking(False)
            if self._addr is None:
                self._addr = (socket.gethostbyname(self.host), self.port)
            self.sock.sendto((line + "\n").encode(), self._addr)
            if log:
                self.note(kind=self.kind, line=line)
        except OSError as e:
            self._addr = None                        # look the head up again next time
            self.note(kind=f"{self.kind}_error", line=line, error=f"{type(e).__name__}: {e}")

    def _replies(self) -> list:
        out = []
        try:
            while self.sock is not None:
                out.append(self.sock.recv(1024).decode(errors="replace").strip())
        except (BlockingIOError, OSError, AttributeError):
            pass
        return out


class LiveFace(_HeadLink):
    """The REAL head's face, for the show runner's live runs (workplan 5.6b; decided 2026-10-08:
    face and earcons first, pan/tilt once the head firmware has GAZE — see LiveGaze).

    The runner tees its twin feed into this: `show(tl)` when a run is validated, `playhead(...)` on
    every tick. The Follower above turns that into the head's own lines, sent over UDP to the head.
    Only a run that reaches RUNNING touches the face — a refused run changes nothing — and when it
    ends, however it ends, the face is handed back its own behaviour (EMOTE AUTO). A theme the show
    set stays (dressing, not state). The head's answers are read: a refusal is logged, and so is
    an earcon the head accepted with its audio switched off (a per-robot setting, off by default)."""

    def __init__(self, host: str, port: int = 4242, sock=None, note=None, earcons: bool = True):
        super().__init__(host, port, sock, note, "face")
        self.f, self.rev, self.tl, self.started = Follower(), 0, None, False
        # Earcons can be held back: the head's first earcon starts its audio task, which damps face
        # tracking until the head restarts (found on opendubs2, 2026-10-08; the fix is the Tab5's).
        self.earcons = earcons

    def release(self):
        """Hand the face back its own behaviour — also sent at runner start, in case a run that
        crashed left it pinned (an EMOTE pin has no lease on the head)."""
        self.f.sent["emote"] = "EMOTE AUTO"
        self._send("EMOTE AUTO")

    def show(self, tl):
        self.rev, self.tl, self.started = self.rev + 1, tl, False
        self.f, self._said = Follower(), set()

    def playhead(self, t, playing, status):
        if status == "RUNNING":
            if not self.started:
                # A live run plays from show time 0, it is never sought: start the follower playing
                # from just before 0, so cues AT 0 — an opening earcon included — are crossed and
                # sent, not treated as a jump (which, by design, never plays sounds).
                self.f.rev, self.f.tl, self.f.t, self.f.playing = self.rev, self.tl, -1e-6, True
            self.started = True
        elif not (self.started and status in ("ABORTED", "ESTOPPED", "COMPLETED", "LEASE_LOST")):
            return
        for line in self.f.update(self.rev, self.tl, t, playing, status):
            if line.startswith("CUE ") and not self.earcons:
                self._once("earcons", kind="face_skipped",
                           line="earcons held back on this runner (--earcons off): the head's audio "
                                "task damps its face tracking until the head restarts")
                continue
            self._send(line)
        for msg in self.f.log:
            self.note(kind="face_skipped", line=msg)
        self.f.log.clear()
        for r in self._replies():
            if "AUDIO OFF" in r:
                self._once("audio", kind="face_warning",
                           note="the head's audio is OFF: its earcons are silent (enable Audio in the head's SETTINGS)")
            elif r.startswith("ERR"):
                self._once(r, kind="face_error", reply=r)
        if status != "RUNNING":
            self.started = False


class LiveGaze(_HeadLink):
    """The REAL head's pan/tilt, for live runs (workplan 5.6b): the HEAD track, sent as the head's
    `GAZE <pan> <tilt> <lease_ms>` (Tab5 PR #56). A HEAD cue sets a TARGET at its time and the head
    slews there at its own rate — the twin's model too — so the target in force is all that is sent,
    renewed every RENEW_S while the run is RUNNING. Every ending sends `GAZE OFF`: the head stops
    where it is and goes back to its own behaviour (ABORT_STATE HEAD); a dead runner's lease simply
    lapses, with the same result. Touch and RC outrank a show on the head itself.

    The head answers `OK gaze <pan> <tilt>` with the angles it CLAMPED to its limits (pan 0-180,
    tilt this robot's band — 80-100 if never commissioned): a difference is logged once per run, so
    a nod that comes out small has a visible cause. A twin echoes what it was asked, so that warning
    only ever fires against a real head. A head without GAZE (firmware before #56) answers
    `ERR unknown`: logged once, and pan/tilt stays in the twin."""

    RENEW_S = 0.2
    LEASE_MS = 600            # the head's default; it caps any lease at 1000
    ENDINGS = ("ABORTED", "ESTOPPED", "COMPLETED", "LEASE_LOST")

    def __init__(self, host: str, port: int = 4242, sock=None, note=None, clock=None):
        super().__init__(host, port, sock, note, "gaze")
        import time as _t
        self.clock = clock or _t.monotonic
        self.track, self.started, self.supported = [], False, None   # None: not known yet
        self._last, self._sent_at = None, -1e9

    def target_at(self, t) -> tuple:
        pan = tilt = 90.0
        for e in self.track:
            if e["t"] > t + 1e-9:
                break
            pan, tilt = float(e["cue"].get("pan", pan)), float(e["cue"].get("tilt", tilt))
        return round(pan), round(tilt)

    def show(self, tl):
        self.track = sorted((tl or {}).get("tracks", {}).get("HEAD", []), key=lambda e: e["t"])
        self.started, self._last, self._sent_at, self._said = False, None, -1e9, set()
        if self.supported is False:
            self.supported = None          # learn again: the head may have been flashed since

    def _read(self):
        for r in self._replies():
            if r.startswith("OK gaze off"):
                self.supported = True
            elif r.startswith("OK gaze "):
                self.supported = True
                got = tuple(int(float(x)) for x in r.split()[2:4])
                if self._last and got != self._last:
                    self._once("clamped", kind="gaze_warning", asked=list(self._last), got=list(got),
                               note="the head limited this move to its own range (tilt band?)")
            elif r.startswith("ERR unknown"):
                self.supported = False
                self._once("unknown", kind="gaze_warning",
                           note="the head has no GAZE command (firmware before Tab5 PR #56): pan/tilt stays in the twin")
            elif r.startswith("ERR"):
                self._once(r, kind="gaze_error", reply=r)

    def probe(self, wait_s: float = 0.5):
        """At runner start: release any lease a crashed run left, and learn whether the head has
        GAZE (for the Shows page's icons). No answer leaves it unknown."""
        import time as _t
        self._send("GAZE OFF")
        end = _t.monotonic() + wait_s
        while self.supported is None and _t.monotonic() < end:
            _t.sleep(0.05)
            self._read()
        return self.supported

    def playhead(self, t, playing, status):
        if not self.track:
            return
        if status == "RUNNING":
            self.started = True
            self._read()
            if self.supported is False:
                return
            tgt = self.target_at(t)
            now = self.clock()
            if tgt != self._last or now - self._sent_at >= self.RENEW_S:
                # Logged only when the target changes, not on every renewal.
                self._send(f"GAZE {tgt[0]} {tgt[1]} {self.LEASE_MS}", log=tgt != self._last)
                self._last, self._sent_at = tgt, now
        elif self.started and status in self.ENDINGS:
            self.started = False
            if self.supported is not False:
                self._send("GAZE OFF")
            self._read()


class HeadTee:
    """The twin feed, also played to the real head (LiveFace, LiveGaze) — for a live run."""

    def __init__(self, feed, *sinks):
        self.feed, self.sinks = feed, sinks

    def show(self, path, tl):
        self.feed.show(path, tl)
        for s in self.sinks:
            s.show(tl)

    def playhead(self, t, playing, status):
        self.feed.playhead(t, playing, status)
        for s in self.sinks:
            try:
                s.playhead(t, playing, status)
            except Exception as e:  # noqa: BLE001  the head never stops a show
                s.note(kind="head_error", error=f"{type(e).__name__}: {e}")

    def __getattr__(self, name):                 # heartbeat, twin, timeline: the twin feed's own
        return getattr(self.feed, name)


def head_face_host() -> str:
    """Where the face is: OPENDUBS_HEAD_FACE_HOST (the tablet plan's name, once the head splits into
    a face and a neck), else the dashboard's OPENDUBS_TAB5_HOST, else the head's mDNS name."""
    return (os.environ.get("OPENDUBS_HEAD_FACE_HOST") or os.environ.get("OPENDUBS_TAB5_HOST")
            or "opendubs-tab5.local")


def head_neck_host() -> str:
    """Where the pan/tilt is: OPENDUBS_HEAD_NECK_HOST (the tablet plan's neck), else the same head."""
    return (os.environ.get("OPENDUBS_HEAD_NECK_HOST") or os.environ.get("OPENDUBS_TAB5_HOST")
            or "opendubs-tab5.local")


def loopback_only(host: str) -> str:
    """Resolve and refuse anything that is not this machine. Live output is armed separately."""
    try:
        addrs = {ai[4][0] for ai in socket.getaddrinfo(host, None, type=socket.SOCK_DGRAM)}
    except socket.gaierror as e:
        raise SystemExit(f"cannot resolve {host!r}: {e}") from e
    bad = [a for a in addrs if not ipaddress.ip_address(a.split("%")[0]).is_loopback]
    if bad:
        raise SystemExit(f"refusing target {host!r} ({', '.join(sorted(bad))}): this bridge sends "
                         "only to this machine. Driving the real head is live output, which the "
                         "plan arms separately (workplan-choreography 5.6).")
    return sorted(addrs)[0]


def get(base: str, path: str) -> dict:
    with urllib.request.urlopen(base.rstrip("/") + path, timeout=0.5) as r:
        return json.loads(r.read())


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--composer", default="http://127.0.0.1:8091")
    ap.add_argument("--face", default="127.0.0.1:4242", help="the face twin, host:port (local only)")
    ap.add_argument("--dry-run", action="store_true", help="print the lines; send nothing")
    a = ap.parse_args()

    host, _, port = a.face.rpartition(":")
    addr = (loopback_only(host or "127.0.0.1"), int(port))
    sock = None if a.dry_run else socket.socket(socket.AF_INET6 if ":" in addr[0] else socket.AF_INET,
                                                socket.SOCK_DGRAM)
    if sock:
        sock.setblocking(False)
    print(f"face bridge: following {a.composer} -> "
          f"{'(dry run)' if a.dry_run else f'{addr[0]}:{addr[1]}'}", flush=True)

    f, rev, tl, online = Follower(), None, None, None
    while True:
        try:
            s = get(a.composer, "/api/twin")
            if s["rev"] != rev:
                d = get(a.composer, "/api/twin/timeline")
                rev, tl = d["rev"], d["timeline"]
            t = s["t"] + ((s["now"] - s["at"]) if s["playing"] else 0.0)
            status = s.get("status")
            if status == "RUNNING" and s.get("lease_until") and s["now"] > s["lease_until"]:
                status = "LEASE_LOST"      # the runner stopped renewing: treat the show as over
            lines = f.update(rev, tl, t, s["playing"] and status != "LEASE_LOST", status)
            if online is not True:
                print("face bridge: composer reachable", flush=True)
            online = True
        except (urllib.error.URLError, OSError, ValueError, KeyError):
            if online is not False:
                print("face bridge: composer not reachable — waiting", flush=True)
            online, lines = False, []
        for msg in f.log:
            print("   not sent:", msg, flush=True)
        f.log.clear()
        for ln in lines:
            print(f"   t={f.t:7.2f}  {ln}", flush=True)
            if sock:
                sock.sendto((ln + "\n").encode(), addr)
        if sock:
            try:
                while True:
                    reply = sock.recv(512).decode(errors="replace").strip()
                    if reply.startswith("ERR"):
                        print(f"   face replied: {reply}", flush=True)
            except (BlockingIOError, OSError):
                pass
        time.sleep(POLL_S)


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        sys.exit(0)
