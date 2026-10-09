#!/usr/bin/env python3
"""A reference head: the head protocol, version 1, on localhost (notes/design/head-protocol.md).

    fake_head.py [--kind tab5|legacy-tab5|tablet-face|neck] [--port 4242]

It answers as the document says a head of that kind answers, so the conformance suite can be
tested, and so a sender can be developed with no head at all. `legacy-tab5` answers as the ESP-IDF
Tab5 does today: no `kind=` in its PING, and none of version 1's new messages. It stands in for the
tablet's door until the door exists (tablet plan 4.3).

Bound to 127.0.0.1 only, by construction: a fake that answered on the robot's network could be
mistaken for the head.
"""
from __future__ import annotations

import argparse
import os
import socket
import struct
import sys
import threading
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import head_protocol as hp  # noqa: E402

PAN_LIMITS = (0.0, 180.0)        # the mechanism, as GAZE and people get
TILT_BAND = (72.0, 108.0)        # this make-believe robot's commissioned tilt band
AUTO_PAN = (60.0, 120.0)         # the autonomous envelope, as trackers get
LEGACY = {"PING", "STATE", "EMOTE", "THEME", "CUE", "CFG", "VOICE", "GAZE", "TOF", "I2CSCAN",
          "REBOOT", "OTA"}


def clamp(v, lo, hi):
    return max(lo, min(hi, v))


def _lease(arg, which):
    default, most = hp.LEASE[which]
    if arg is None:
        return default
    v = int(arg)
    if v < 0:
        raise ValueError
    return min(v or default, most)


class Neck:
    """Precedence, leases, slew. Time is passed in, so a test can drive it without sleeping."""

    def __init__(self):
        self.pan = self.tilt = 90.0
        self.src = {}            # owner -> (expires_ms, kind, a, b): kind "at" (angles) or "rate"
        self.park_until = 0.0
        self.last = None
        self.slew = hp.SLEW_DPS          # a test can make it crawl

    def owner(self, now):
        live = {k for k, v in self.src.items() if v[0] > now}
        for k in ("touch", "rc"):
            if k in live:
                return k
        if self.park_until > now:
            return "park"
        for k in ("gaze", "thor", "local"):
            if k in live:
                return k
        return "none"

    def target(self, now):
        o = self.owner(now)
        if o == "park":
            return 90.0, 90.0
        if o == "none" or self.src[o][1] == "rate":
            return self.pan, self.tilt
        return self.src[o][2], self.src[o][3]

    def advance(self, now):
        if self.last is not None:
            dt = (now - self.last) / 1000.0
            o = self.owner(now)
            if o in ("touch", "rc") and self.src[o][1] == "rate":
                self.pan = clamp(self.pan + self.src[o][2] * dt, *PAN_LIMITS)
                self.tilt = clamp(self.tilt + self.src[o][3] * dt, *TILT_BAND)
            else:
                tp, tt = self.target(now)
                step = self.slew * dt
                self.pan += clamp(tp - self.pan, -step, step)
                self.tilt += clamp(tt - self.tilt, -step, step)
        self.last = now

    def moving(self, now):
        tp, tt = self.target(now)
        return int(abs(tp - self.pan) > 0.5 or abs(tt - self.tilt) > 0.5)

    def tail(self, now):
        return f"owner={self.owner(now)} at={self.pan:.1f},{self.tilt:.1f} moving={self.moving(now)}"


class FakeHead:
    def __init__(self, kind="tab5", port=hp.PORT, audio=False, clock=None):
        assert kind in ("tab5", "legacy-tab5", "tablet-face", "neck")
        self.kind, self.audio = kind, audio
        self.clock = clock or (lambda: time.monotonic() * 1000.0)
        self.state, self.emote, self.theme, self.skin = "", "AUTO", "plain", "default"
        self.neck = Neck()
        if kind in ("tab5", "legacy-tab5"):
            self.rest_at_centre()
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.bind(("127.0.0.1", port))
        self.port = self.sock.getsockname()[1]
        self._stop = False

    def rest_at_centre(self):
        """The Tab5's local tracker is ALWAYS a live source while tracking is on, and its resting
        behaviour returns the head to centre at the slew rate (app_face.cpp, "the slew and the return
        to centre are continuous"). So after a lease expires the head is handed over, not left."""
        self.neck.src["local"] = (float("inf"), "at", 90.0, 90.0)

    # ---- what this kind answers ---------------------------------------------------------------
    def implements(self, verb):
        if self.kind == "legacy-tab5":
            return verb in LEGACY
        return verb in hp.KINDS[self.kind]

    def handle(self, data: bytes) -> str | None:
        now = self.clock()
        if self.kind != "tablet-face":
            self.neck.advance(now)
        # The rate frame ONLY by its id and length, as the head's netPlatRcFrame() does: anything
        # else of 13 bytes is a line ("STATE MANUAL\n" is 13 bytes), and a retired frame read as a
        # line is gibberish, so `ERR unknown`.
        if len(data) == 13:
            can_id, dlc = struct.unpack("<IB", data[:5])
            if can_id == hp.CAN_ID_RC_RATE and dlc == 4 and self.implements("GAZE"):
                p, t = struct.unpack("<hh", data[5:9])
                self.neck.src["rc"] = (now + hp.LEASE["rc"][0], "rate", float(p), float(t))
                return None
        line = data.split(b"\0", 1)[0].decode(errors="replace").rstrip("\r\n")
        verb, _, rest = line.partition(" ")
        if verb not in hp.MESSAGES or not self.implements(verb):
            return "ERR unknown"
        return getattr(self, "do_" + verb.lower())(rest, now)

    # ---- face ---------------------------------------------------------------------------------
    def do_ping(self, _, now):
        st = self.state or "-"
        if self.kind == "legacy-tab5":
            return f"PONG slot=ota_0 state=VALID sha=0000000000000000 | plc=none | audio=OFF | st={st} | " \
                   f"fake | {self._pantilt()}"
        if self.kind == "tablet-face":
            return (f"PONG kind=tablet-face app=0.0.0-fake engine=0 library=0 mount=unknown "
                    f"mode=face st={st} audio={'on' if self.audio else 'OFF'}")
        if self.kind == "neck":
            return f"PONG kind=neck fw=0000000000000000 state=VALID plc=none owner={self.neck.owner(now)} " \
                   f"pan={self.neck.pan:.1f} tilt={self.neck.tilt:.1f}"
        return f"PONG kind=tab5 slot=ota_0 state=VALID sha=0000000000000000 | plc=none | " \
               f"audio={'on' if self.audio else 'OFF'} | st={st} | fake | {self._pantilt()}"

    def _pantilt(self):
        """The Tab5's pan/tilt status, as pantiltStatus() words it: the commanded angle."""
        return f"pantilt: p{self.neck.pan:.0f} t{self.neck.tilt:.0f} tilt[{TILT_BAND[0]:.0f}..{TILT_BAND[1]:.0f}]"

    def do_state(self, a, _):
        self.state = a
        return f"OK state {a}"

    def do_emote(self, a, _):
        if a == "AUTO" or a.upper() in hp.EMOTES:
            self.emote = a
            return f"OK emote {a}"
        return "ERR unknown emote"

    def do_theme(self, a, _):
        if a.lower() in hp.THEMES_BUILT_IN or a.upper() == "NONE":
            self.theme = a
            return f"OK theme {a}"
        return "ERR unknown theme"

    def do_cue(self, a, _):
        if a.lower() not in hp.CUES:
            return "ERR unknown cue"
        return f"OK cue {a}" if self.audio else f"OK cue {a} (AUDIO OFF -- enable it in SETTINGS)"

    def do_cfg(self, a, _):
        return "ERR cfg: the reference head installs nothing"

    def do_skin(self, a, _):
        if not a:
            return "ERR skin <name> | default"
        if a in hp.CODE_SKINS:
            self.skin = a
            return f"OK skin {a}"
        self.skin = "default"                    # unknown: the default eyes, and say so
        return f"ERR unknown skin {a} (showing default)"

    def do_voice(self, a, _):
        return "voice: off (reference head)"

    # ---- neck ---------------------------------------------------------------------------------
    def do_gaze(self, a, now):
        if a == "OFF":
            self.neck.src.pop("gaze", None)
            return "OK gaze off"
        try:
            parts = a.split()
            pan, tilt = float(parts[0]), float(parts[1])
            lease = _lease(parts[2] if len(parts) > 2 else None, "gaze")
        except (ValueError, IndexError):
            return "ERR gaze <pan_deg> <tilt_deg> [lease_ms] | OFF"
        pan, tilt = clamp(pan, *PAN_LIMITS), clamp(tilt, *TILT_BAND)
        self.neck.src["gaze"] = (now + lease, "at", pan, tilt)
        return f"OK gaze {pan:.0f} {tilt:.0f}"

    def do_track(self, a, now):
        usage = "ERR track <thor|local> <pan_deg> <tilt_deg> [lease_ms] | <thor|local> OFF"
        parts = a.split()
        if not parts or parts[0] not in hp.TRACK_SOURCES:
            return usage
        src = parts[0]
        if src == "local" and self.kind in hp.TRACK_LOCAL_REFUSED:
            return "ERR track local is on this head"
        if parts[1:] == ["OFF"]:
            self.neck.src.pop(src, None)
            return f"OK track {src} off"
        try:
            pan, tilt = float(parts[1]), float(parts[2])
            lease = _lease(parts[3] if len(parts) > 3 else None, "track")
        except (ValueError, IndexError):
            return usage
        pan, tilt = clamp(pan, *AUTO_PAN), clamp(tilt, *TILT_BAND)
        self.neck.src[src] = (now + lease, "at", pan, tilt)
        return f"OK track {src} {pan:.0f} {tilt:.0f} {self.neck.tail(now)}"

    def do_neck(self, a, now):
        n = self.neck
        tp, tt = n.target(now)
        return (f"OK neck pan={n.pan:.1f} tilt={n.tilt:.1f} target={tp:.1f},{tt:.1f} "
                f"moving={n.moving(now)} owner={n.owner(now)} parked={int(n.park_until > now)} "
                f"tilt_band={TILT_BAND[0]:.0f},{TILT_BAND[1]:.0f}")

    def do_touch(self, a, now):
        usage = "ERR touch HOLD [lease_ms] | RATE <pan_dps> <tilt_dps> [lease_ms] | OFF"
        parts = a.split()
        try:
            if parts == ["OFF"]:
                self.neck.src.pop("touch", None)
                return "OK touch off"
            if parts and parts[0] == "HOLD" and len(parts) <= 2:
                lease = _lease(parts[1] if len(parts) > 1 else None, "touch")
                self.neck.src["touch"] = (now + lease, "rate", 0.0, 0.0)
                return "OK touch hold"
            if parts and parts[0] == "RATE" and len(parts) in (3, 4):
                m = hp.TOUCH_RATE_MAX_DPS
                p, t = clamp(float(parts[1]), -m, m), clamp(float(parts[2]), -m, m)
                lease = _lease(parts[3] if len(parts) > 3 else None, "touch")
                self.neck.src["touch"] = (now + lease, "rate", p, t)
                return f"OK touch rate {p:.0f} {t:.0f}"
        except ValueError:
            pass
        return usage

    def do_park(self, a, now):
        if a == "OFF":
            self.neck.park_until = 0.0
            return "OK park off"
        try:
            lease = _lease(a or None, "park")
        except ValueError:
            return "ERR park [lease_ms] | OFF"
        self.neck.park_until = now + lease
        return "OK park"

    def do_tof(self, a, _):
        return "tof: none (reference head)"

    def do_i2cscan(self, a, _):
        return "i2c scan: none (reference head)"

    def do_reboot(self, a, _):
        return "OK reboot"

    def do_ota(self, a, _):
        return "OK ota"

    # ---- transport ----------------------------------------------------------------------------
    def serve(self):
        self.sock.settimeout(0.2)
        while not self._stop:
            try:
                data, peer = self.sock.recvfrom(512)
            except socket.timeout:
                continue
            except OSError:
                break
            reply = self.handle(data)
            if reply is not None:
                self.sock.sendto(reply.encode(), peer)

    def start(self):
        threading.Thread(target=self.serve, daemon=True).start()
        return self

    def stop(self):
        self._stop = True
        self.sock.close()


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--kind", default="tab5", choices=("tab5", "legacy-tab5", "tablet-face", "neck"))
    ap.add_argument("--port", type=int, default=hp.PORT)
    ap.add_argument("--audio", action="store_true", help="earcons audible (the default is off, as on the head)")
    a = ap.parse_args()
    h = FakeHead(a.kind, a.port, a.audio)
    print(f"reference head ({a.kind}) on 127.0.0.1:{h.port}")
    try:
        h.serve()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
