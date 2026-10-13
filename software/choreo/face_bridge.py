#!/usr/bin/env python3
"""The face bridge: follows the composer's playhead and sends the face its show cues (plan 3.3).

    ./face.sh                                    # the face twin plus this bridge, in one command
    python3 face_bridge.py                       # composer on :8091, face twin on 127.0.0.1:4242
    python3 face_bridge.py --dry-run             # print the lines, send nothing
    python3 face_bridge.py --face 127.0.0.1:4244 --neck 127.0.0.1:4245   # the web face twin and its neck

The face twin is the head's own face code built for the desktop (the Tab5 tree's desktop build); it
shares the head's command parser, so a line it accepts the head accepts. face_stub.py stands in where
that build is not available.

It reads the same feed as the MuJoCo body twin (`/api/twin`, `/api/twin/timeline`) and turns the
show's FACE and SOUND cues into the head's own command lines (`face-twin.md`, FT-2 to FT-4):

    FACE  { do: emote, name: SOFT }        ->  EMOTE SOFT
    (no FACE emote yet at this time)       ->  EMOTE AUTO      (the face's own cycling)
    FACE  { do: theme, name: halloween }   ->  THEME halloween (until the show ends)
    FACE  { do: move, name: wink_l }       ->  MOVE wink_l, sent at the event's `send_t` (its peak
                                               offset early, so the wink is shut ON the beat); one-shot,
                                               so a seek or a resync never replays it
    FACE  { do: skin, name: ring }         ->  SKIN ring       (head protocol v2; until the show ends)
    SOUND { do: play, sound: attend }      ->  CUE attend      (one of the Tab5's six earcons)
    device: { volume: 0.6 } (show level)   ->  VOLUME 0.6      (head protocol v5: at show time 0)
    DEVICE { do: brightness, level: 0.4,   ->  BRIGHT 0.4 3    (fading over 3 s; on a seek, the level
             fade_s: 3 }                                        in force, unfaded)
    (a run ends, however it ends)          ->  EMOTE AUTO, SKIN IDLE, THEME IDLE, VOLUME IDLE, BRIGHT IDLE
                                               (head protocol v5: the robot's own skin, theme and levels
                                               back, whether or not the show changed them)

While playing, each cue is sent as the playhead crosses it. On a seek, a pause, a backwards jump or
an edit, the bridge instead sends the STATE AT THAT TIME — the emote and theme in force — so jumping
around a show leaves the face correct. Before a show's first theme cue the face keeps whatever theme
it had: a theme is dressing, not a state the show owns; at the end the robot's idle theme comes back. Sounds are only ever sent while playing, never on a seek.

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


DEVICE_VERB = {"volume": "VOLUME", "brightness": "BRIGHT"}   # head protocol v5


def _level(v) -> str:
    """A 0..1 level (or a fade's seconds) as the head reads it: up to three decimals, no trailing zeros."""
    return f"{round(float(v), 3):g}"


# Every show end, however it ends, hands these back to the robot's own settings (head protocol v5):
# the idle skin and theme (the Head & face page), the media volume and the screen brightness.
IDLE_VERBS = ("SKIN", "THEME", "VOLUME", "BRIGHT")

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

    def _skin_at(self, t):
        line = None                # before any skin cue: leave the face's skin alone
        for e in self._track("FACE"):
            if e["t"] > t + 1e-9:
                break
            if e["cue"].get("do") == "skin" and e["cue"].get("name"):
                line = f"SKIN {e['cue']['name']}"
        return line

    def _device_at(self, t):
        """{"VOLUME": level, "BRIGHT": level} in force at t: the show-level device: block from 0, then
        the DEVICE cues — each one's TARGET (a seek does not replay a fade)."""
        dev = (self.tl or {}).get("device") or {}
        out = {}
        if t >= 0:
            out.update({DEVICE_VERB[k]: v for k, v in dev.items() if k in DEVICE_VERB})
        for e in self._track("DEVICE"):
            if e["t"] > t + 1e-9:
                break
            if e["cue"].get("do") in DEVICE_VERB:
                out[DEVICE_VERB[e["cue"]["do"]]] = e["cue"]["level"]
        return out

    def _sync(self, t):
        """Lines that bring the face to the state in force at t, sending only what changed."""
        out = []
        for verb, level in self._device_at(t).items():
            line = f"{verb} {_level(level)}"
            if self.sent.get(verb) != line:
                self.sent[verb] = line
                out.append(line)
        theme = self._theme_at(t)
        if theme and self.sent.get("theme") != theme:
            self.sent["theme"] = theme
            out.append(theme)
        skin = self._skin_at(t)
        if skin and self.sent.get("skin") != skin:
            self.sent["skin"] = skin
            out.append(skin)
        emote = self._emote_at(t)
        if self.sent.get("emote") != emote:
            self.sent["emote"] = emote
            out.append(emote)
        return out

    def _crossed(self, t0, t1):
        """Cues with t0 < t <= t1, in time order, as lines; unsendable ones go to the log."""
        out = []
        if t0 < 0 <= t1:                            # show time 0: the show-level volume and brightness
            for k, v in ((self.tl or {}).get("device") or {}).items():
                if k in DEVICE_VERB:
                    self.sent[DEVICE_VERB[k]] = line = f"{DEVICE_VERB[k]} {_level(v)}"
                    out.append(line)
        evs = []
        for slot in ("FACE", "SOUND", "LIGHT", "DEVICE"):
            for e in self._track(slot):
                when = e.get("send_t", e["t"])       # a move goes early, so its peak lands at t
                if t0 < when <= t1 + 1e-9:
                    evs.append((when, slot, e))
        for t, slot, e in sorted(evs, key=lambda x: x[0]):
            c = e["cue"]
            if slot == "DEVICE" and c.get("do") in DEVICE_VERB:
                verb = DEVICE_VERB[c["do"]]
                self.sent[verb] = f"{verb} {_level(c['level'])}"   # what a later seek compares with
                out.append(self.sent[verb] + (f" {_level(c['fade_s'])}" if c.get("fade_s") else ""))
            elif slot == "FACE" and c.get("do") in ("emote", "theme", "skin"):
                line = f"{c['do'].upper()} {c['name']}"
                self.sent[c["do"]] = line
                out.append(line)
            elif slot == "FACE" and c.get("do") == "move":
                out.append(f"MOVE {c['name']}")      # not state: nothing to resynchronise to later
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
            for verb in IDLE_VERBS:                  # every end: the robot's own skin, theme and levels
                if self.sent.get(verb) != f"{verb} IDLE":
                    self.sent[verb] = f"{verb} IDLE"
                    out.append(f"{verb} IDLE")
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
    Never raises — the head is never a reason to stop a show — but says what went wrong, once.

    NEVER BLOCKS. A name (opendubs-tab5.local) is looked up on a background thread, never on the
    caller's: a failed mDNS lookup of an absent head takes 5 s, and done on the show's own loop it
    stalled every tick of a live run by 5 s — face cues sent only as 5 s seeks, grip commands sent
    in bursts that cancelled each other, the deadman checked every 5 s (found at the robot,
    2026-10-13, with the Tab5 neck off the network). Until the name answers, a line is not sent and
    the run is told once; an absent head is looked for again every RESOLVE_RETRY_S, a found one
    re-checked every RESOLVE_TTL_S (DHCP), and the last good address is used meanwhile. An IPv4
    address is used as it is, with no lookup."""

    RESOLVE_RETRY_S = 5.0
    RESOLVE_TTL_S = 30.0

    def __init__(self, host: str, port: int, sock, note, kind: str, resolve=None):
        import threading
        self.host, self.port, self.sock, self.kind = host, port, sock, kind
        self.note = note or (lambda **kw: None)     # the runner's log: note(kind, **fields)
        self._said = set()
        self._resolve = resolve or socket.gethostbyname
        self._lock, self._looking = threading.Lock(), False
        self._found_at, self._failed_at, self._lookup_error = None, None, None
        try:
            literal = ipaddress.ip_address(host).version == 4
        except ValueError:
            literal = False
        self._literal = literal
        self._addr = (host, port) if literal else None

    def _once(self, key, **kw):
        if key not in self._said:
            self._said.add(key)
            self.note(**kw)

    def _address(self):
        """Where the head is now, or None — at once. Starts a background lookup when one is due."""
        if self._literal:
            return self._addr
        import threading
        now = time.monotonic()
        with self._lock:
            addr = self._addr
            due = (now - self._found_at >= self.RESOLVE_TTL_S) if addr is not None else (
                self._failed_at is None or now - self._failed_at >= self.RESOLVE_RETRY_S)
            if due and not self._looking:
                self._looking = True
                threading.Thread(target=self._lookup, name=f"{self.kind}-lookup", daemon=True).start()
        return addr

    def _lookup(self):
        try:
            ip = self._resolve(self.host)
            with self._lock:
                self._addr, self._found_at, self._lookup_error = (ip, self.port), time.monotonic(), None
        except Exception as e:  # noqa: BLE001  gaierror, timeouts: the head is not there (yet)
            with self._lock:
                self._failed_at = time.monotonic()      # a previous address, if any, is kept
                self._lookup_error = f"{type(e).__name__}: {e}"
        finally:
            with self._lock:
                self._looking = False

    def _send(self, line: str, log: bool = True):
        addr = self._address()
        if addr is not None and "unresolved" in self._said and "found" not in self._said:
            self._once("found", kind=f"{self.kind}_found", host=self.host, addr=addr[0],
                       note=f"{self.host} answers now: {self.kind} lines go to it again")
        if addr is None:
            self._once("unresolved", kind=f"{self.kind}_error", line=line,
                       error=f"{self.host} is not found ({self._lookup_error or 'looking it up'}): "
                             f"{self.kind} lines are not sent until it answers — looked for every "
                             f"{self.RESOLVE_RETRY_S:g} s in the background, never on the show's clock")
            return
        try:
            if self.sock is None:
                self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
                self.sock.setblocking(False)
            self.sock.sendto((line + "\n").encode(), addr)
            if log:
                self.note(kind=self.kind, line=line)
        except OSError as e:
            if not self._literal:
                with self._lock:
                    self._found_at = -1e18              # look the head up again, in the background
            self._once(f"send:{type(e).__name__}", kind=f"{self.kind}_error", line=line,
                       error=f"{type(e).__name__}: {e}")

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
    ends, however it ends, the face is handed back its own behaviour (EMOTE AUTO) and the robot's
    idle skin, theme, volume and brightness (IDLE_VERBS, head protocol v5). The head's answers are
    read: a refusal is logged, and so is an earcon the head accepted with its audio switched off (a
    per-robot setting, off by default). A head without the v5 IDLE lines (the Tab5) refuses them;
    that is expected, and noted once, not logged as an error."""

    def __init__(self, host: str, port: int = 4242, sock=None, note=None, earcons: bool = True, resolve=None):
        super().__init__(host, port, sock, note, "face", resolve)
        self.f, self.rev, self.tl, self.started = Follower(), 0, None, False
        self._idle_err = {}
        # Earcons can be held back: the head's first earcon starts its audio task, which damps face
        # tracking until the head restarts (found on opendubs2, 2026-10-08; the fix is the Tab5's).
        self.earcons = earcons

    def release(self):
        """Hand the face back its own behaviour — also sent at runner start, in case a run that
        crashed left it pinned (an EMOTE pin has no lease on the head)."""
        self.f.sent["emote"] = "EMOTE AUTO"
        self._send("EMOTE AUTO")
        for verb in IDLE_VERBS:
            self.f.sent[verb] = f"{verb} IDLE"
            self._send(f"{verb} IDLE")
        self._expect_idle_refusals()

    def _expect_idle_refusals(self):
        # What a head without them (the Tab5) answers the IDLE lines: SKIN, VOLUME and BRIGHT are
        # unknown verbs there, THEME IDLE an unknown theme. Set, never added: a burst's answers are
        # read whenever the next read comes, which may be the next run's.
        self._idle_err = {"ERR unknown": 3, "ERR unknown theme": 1}

    def _idle_refusal(self, r) -> bool:
        """An expected answer to the IDLE lines: noted once, and True."""
        if self._idle_err.get(r, 0) <= 0:
            return False
        self._idle_err[r] -= 1
        self._once("idle:" + r, kind="face_note",
                   note=f"the head answered the show-end IDLE lines with {r!r}: a head without them "
                        "(the Tab5) — expected")
        return True

    def armed(self, what):
        """The ARMED indication (head protocol v5, agreed with the Tablet lane 2026-10-13): `WAIT`
        while a run waits for the deadman (the head holds it 3 s; the runner re-sends it every
        second), the countdown's seconds at the GO, `OFF` when the run starts or ends. A head
        without it answers ERR: said once, and the run goes on."""
        self._send(f"ARMED {what}", log=what != "WAIT")
        for r in self._replies():
            if self._idle_refusal(r):
                continue
            if r.startswith("ERR"):
                self._once("armed:" + r, kind="face_warning",
                           note=f"the head did not take ARMED ({r}) — no armed indication on the face")

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
            if line == "THEME IDLE":
                self._expect_idle_refusals()
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
            elif r.upper().startswith(("OK VOLUME", "OK BRIGHT", "OK SKIN IDLE", "OK THEME IDLE")):
                # the head says the level it APPLIED (a brightness under its floor comes back raised),
                # and which idle skin and theme it went back to
                self.note(kind="face_device", reply=r)
            elif self._idle_refusal(r):
                pass
            elif r.upper().startswith("ERR SKIN IDLE"):
                self._once(r, kind="face_warning",
                           note=f"the robot's idle skin did not load ({r}) — fix it on the Head & face page")
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

    def __init__(self, host: str, port: int = 4242, sock=None, note=None, clock=None, resolve=None):
        super().__init__(host, port, sock, note, "gaze", resolve)
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


NO_NECK = ("none", "off")       # OPENDUBS_HEAD_NECK_HOST=none: this robot has no neck fitted


def head_neck_host() -> str | None:
    """Where the pan/tilt is: OPENDUBS_HEAD_NECK_HOST (the tablet plan's neck), else the face's
    address, else the dashboard's old name, else the head's mDNS name — the same order as the
    dashboard's backend/head.py (tablet PR #69), so the two never disagree when the face and the
    neck are different devices. None when the neck is set to `none` or `off` (any case): no neck is
    fitted, and pan/tilt stays in the twin. Unset or empty keeps its old meaning, the face's box."""
    neck = (os.environ.get("OPENDUBS_HEAD_NECK_HOST") or "").strip()
    if neck.lower() in NO_NECK:
        return None
    return (neck or os.environ.get("OPENDUBS_HEAD_FACE_HOST")
            or os.environ.get("OPENDUBS_TAB5_HOST") or "opendubs-tab5.local")


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
    ap.add_argument("--neck", default=None,
                    help="a twin's NECK, host:port (local only) — e.g. the web face's door on 127.0.0.1:4245: "
                         "the HEAD track goes to it as GAZE, so the twin shows the head turning")
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

    neck = None
    if a.neck and not a.dry_run:
        nh, _, np_ = a.neck.rpartition(":")
        neck = LiveGaze(loopback_only(nh or "127.0.0.1"), int(np_))
        print(f"face bridge: the HEAD track -> the neck twin at {neck.host}:{neck.port}", flush=True)
    f, rev, tl, online, neck_rev = Follower(), None, None, None, None
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
            if neck is not None:
                if rev != neck_rev and tl:
                    neck.show(tl)
                    neck_rev = rev
                # A twin's neck: hold the target in force even while paused or scrubbing, so the twin
                # shows where the head would be at the playhead (the robot's runner sends GAZE only
                # while a live run is RUNNING).
                neck.playhead(t, True, "RUNNING")
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
