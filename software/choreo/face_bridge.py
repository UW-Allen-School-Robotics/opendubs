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

THIS IS THE RUNNER'S FIRST DRAFT, AND IT IS LOCAL ONLY. It refuses any target that is not this
machine: sending a show to the real head is live output, which the plan arms separately (5.6).
It never sends motion, and its only inputs are the composer's feed on localhost.
"""
from __future__ import annotations

import argparse
import ipaddress
import json
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
