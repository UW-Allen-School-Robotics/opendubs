#!/usr/bin/env python3
"""Check a head against the head protocol (notes/design/head-protocol.md §9).

    conformance.py <face-host> [--neck <host>] [--active] [--motion] [--at-the-robot] [--strict]

READ-ONLY BY DEFAULT, and safe on a robot: PING, NECK, TOF, I2CSCAN, the retired messages, and
malformed or unknown-name forms of the rest. Nothing it sends changes the face or moves the head.

  --active        also STATE, EMOTE, THEME, CUE (changes what the face shows; the state found
                  first is put back), CHEST (the chest light; the dashboard restores it) and
                  TOUCH HOLD (takes the head without moving it)
  --motion        also GAZE, TRACK, TOUCH RATE and PARK: short leases near centre, then the
                  SAMPLED checks — the head's angle read every 100 ms while a lease is renewed and
                  after it expires: it gets there, at its slew rate, and stays put. MOVES THE HEAD.
  --at-the-robot  required for --active or --motion on anything but 127.0.0.1 — say a person is
                  there and watching
  --strict        PENDING (specified, answered `ERR unknown`) counts as a failure

Each check is PASS, FAIL, PENDING or SKIP. Exit status 1 on any FAIL.
"""
from __future__ import annotations

import argparse
import os
import re
import socket
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import head_protocol as hp  # noqa: E402

PASS, FAIL, PENDING, SKIP = "PASS", "FAIL", "PENDING", "SKIP"


class Link:
    """One UDP request, one reply. Two tries, because it is UDP."""

    def __init__(self, port: int = hp.PORT, timeout: float = 0.5):
        self.port, self.timeout = port, timeout

    def ask(self, host: str, msg) -> str | None:
        """`host` may carry its own port, `host:port` — so a face and a neck can share a machine."""
        host, _, p = host.partition(":")
        port = int(p) if p else self.port
        data = msg if isinstance(msg, bytes) else (msg + "\n").encode()
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.settimeout(self.timeout)
        try:
            for _ in range(2):
                s.sendto(data, (host, port))
                try:
                    return s.recvfrom(1024)[0].decode(errors="replace").strip()
                except socket.timeout:
                    continue
            return None
        finally:
            s.close()


class Run:
    def __init__(self, link: Link, face: str, neck: str, themes=()):
        self.link, self.face, self.neck = link, face, neck
        self.themes = tuple(themes)         # discovered themes the face should have (--theme)
        self.results: list[tuple[str, str, str, str]] = []   # (status, half, check, detail)
        self.kind = {}

    def note(self, status, half, check, detail=""):
        self.results.append((status, half, check, detail))

    def host(self, half):
        return self.neck if half == hp.NECK else self.face

    def ask(self, half, msg):
        return self.link.ask(self.host(half), msg)

    def expect(self, half, check, msg, verb, ok):
        """Send `msg` (a message of `verb`) to `half` and judge the reply. `ok(reply)` says whether
        an implemented answer is right. A head that does not implement `verb` must say `ERR unknown`;
        one that should but does not yet is PENDING for a new message, FAIL for a shipping one."""
        r = self.ask(half, msg)
        kind = self.kind.get(half)
        if r is None:
            return self.note(FAIL, half, check, "no reply")
        implements = verb in hp.KINDS.get(kind, set())
        unknown = bool(hp.REPLY["unknown"].match(r))
        if not implements:
            return self.note(PASS if unknown else FAIL, half, check,
                             f"not a {kind} message: {r}" if unknown else f"{kind} should not answer: {r}")
        if unknown:
            status = hp.MESSAGES[verb][1]
            unbuilt = status in hp.NOT_YET_SHIPPING
            return self.note(PENDING if unbuilt else FAIL, half, check,
                             f"{verb} not built on this {kind} yet" if unbuilt else r)
        # The reply is shown either way: a PASS still says what the head did.
        return self.note(PASS if ok(r) else FAIL, half, check, r)

    # ---- the checks ---------------------------------------------------------------------------
    def ping(self):
        halves = [hp.FACE] if self.face == self.neck else [hp.FACE, hp.NECK]
        for half in halves:
            r = self.ask(half, "PING")
            if r is None or not r.startswith("PONG"):
                self.note(FAIL, half, "PING", r or "no reply")
                continue
            self.kind[half] = hp.kind_of(r)
            self.note(PASS, half, "PING", f"kind={self.kind[half]}" +
                      ("" if "kind=" in r else " (no kind= field: today's Tab5)"))
            if half == hp.FACE:
                self.state_found = hp.fields(r).get("st")
        if self.face == self.neck and hp.FACE in self.kind:
            self.kind[hp.NECK] = self.kind[hp.FACE]

    def answered(self):
        """Every half answered PING. If not, stop there: a page of "no reply" hides the one line
        that matters."""
        return {hp.FACE, hp.NECK} <= set(self.kind)

    def read_only(self):
        for half in sorted({hp.FACE, hp.NECK} if self.face != self.neck else {hp.FACE}):
            r = self.ask(half, "OPENDUBS_NO_SUCH_MESSAGE")
            self.note(PASS if r == "ERR unknown" else FAIL, half, "an unknown message", r or "no reply")
            for line in hp.RETIRED_TEXT:
                r = self.ask(half, line)
                self.note(PASS if r == "ERR unknown" else FAIL, half, f"retired: {line.split()[0]}",
                          r or "no reply")
            for can_id, what in hp.RETIRED_FRAMES.items():
                r = self.ask(half, hp.can_frame(can_id, bytes([0, 0, 0, 0])))
                self.note(PASS if r == "ERR unknown" else FAIL, half,
                          f"retired: 0x{can_id:03x} ({what})", r or "no reply")

        F, N = hp.FACE, hp.NECK
        self.expect(F, "EMOTE <unknown name>", "EMOTE NO_SUCH_EMOTE", "EMOTE",
                    lambda r: r == "ERR unknown emote")
        self.expect(F, "THEME <unknown name>", "THEME no_such_theme", "THEME",
                    lambda r: r == "ERR unknown theme")
        self.expect(F, "CUE <unknown name>", "CUE no_such_cue", "CUE",
                    lambda r: r == "ERR unknown cue")
        self.expect(F, "CAPTION <too long> is refused, never cut", "CAPTION " + "x" * (hp.CAPTION_MAX + 1), "CAPTION",
                    lambda r: bool(hp.REPLY["caption_err"].match(r)))
        self.expect(N, "GAZE <malformed>", "GAZE nowhere", "GAZE",
                    lambda r: bool(hp.REPLY["gaze_err"].match(r)))
        self.expect(N, "TRACK <unknown source>", "TRACK nobody 90 90", "TRACK",
                    lambda r: bool(hp.REPLY["track_err"].match(r)))
        self.expect(N, "TOUCH <malformed>", "TOUCH NOPE", "TOUCH",
                    lambda r: bool(hp.REPLY["touch_err"].match(r)))
        self.expect(N, "PARK <malformed>", "PARK NOPE", "PARK",
                    lambda r: bool(hp.REPLY["park_err"].match(r)))
        self.expect(N, "CHEST <out of range>", "CHEST 3000", "CHEST",
                    lambda r: bool(hp.REPLY["chest_err"].match(r)))
        self.expect(N, "NECK", "NECK", "NECK", self._neck_ok)
        self.expect(N, "TOF", "TOF", "TOF", lambda r: bool(r) and not r.startswith("ERR unknown"))
        self.expect(N, "I2CSCAN", "I2CSCAN", "I2CSCAN", lambda r: bool(r) and not r.startswith("ERR unknown"))

    @staticmethod
    def _neck_ok(r, owner=None):
        m = hp.REPLY["neck_ok"].match(r)
        if not m:
            return bool(hp.REPLY["neck_none"].match(r)) and owner is None
        return m.group(6) in hp.OWNERS and (owner is None or m.group(6) == owner)

    def active(self):
        F, N = hp.FACE, hp.NECK
        for s in hp.STATES:
            self.expect(F, f"STATE {s}", f"STATE {s}", "STATE", lambda r, s=s: r == f"OK state {s}")
        for e in hp.EMOTES:
            self.expect(F, f"EMOTE {e}", f"EMOTE {e}", "EMOTE", lambda r, e=e: r == f"OK emote {e}")
        self.expect(F, "EMOTE AUTO", "EMOTE AUTO", "EMOTE", lambda r: r == "OK emote AUTO")
        # The core's theme, its alias, and any discovered theme the face should have (looks.py): THEME is
        # exercised whatever the content folder holds, and a discovered one proves discovery reached it.
        for t in hp.THEMES_BUILT_IN + ("NONE",) + self.themes:
            self.expect(F, f"THEME {t}", f"THEME {t}", "THEME", lambda r, t=t: r == f"OK theme {t}")
        self.expect(F, "THEME plain (put back)", "THEME plain", "THEME", lambda r: r == "OK theme plain")
        for c in hp.CUES:
            pat = re.compile(rf"^OK cue {c}( \(AUDIO OFF.*\))?$")
            self.expect(F, f"CUE {c}", f"CUE {c}", "CUE", lambda r, p=pat: bool(p.match(r)))
        # SKIN (v2): a code skin, `default` to return, and an unknown name falls back to the default
        # eyes and says so. Active, because it changes what the face shows.
        self.expect(F, "SKIN ring", "SKIN ring", "SKIN", lambda r: r == "OK skin ring")
        self.expect(F, "SKIN <unknown name>", "SKIN no_such_skin", "SKIN",
                    lambda r: r == "ERR unknown skin no_such_skin (showing default)")
        self.expect(F, "SKIN default", "SKIN default", "SKIN", lambda r: r == "OK skin default")
        # v5: the idle skin and theme, sent at every show end. THEME is shipping on every face, but
        # THEME IDLE is a v5 word only a face with SKIN knows: a Tab5 rightly answers
        # `ERR unknown theme`, which the runner expects, so it is not asked here.
        self.expect(F, "SKIN IDLE", "SKIN IDLE", "SKIN", lambda r: bool(re.match(r"^OK skin idle \S+$", r)))
        if "SKIN" in hp.KINDS.get(self.kind.get(F), set()):
            self.expect(F, "THEME IDLE", "THEME IDLE", "THEME", lambda r: bool(re.match(r"^OK theme idle \S+$", r)))
        # SPIN (v3): a skin's counter-rotation on a beat. A non-spinning skin answers OK and does
        # nothing, so these hold whatever skin is showing.
        for line, want in (("SPIN COUNTER 2", "counter"), ("SPIN TOGETHER", "together"), ("SPIN AUTO", "auto")):
            self.expect(F, line, line, "SPIN", lambda r, w=want: r == f"OK spin {w}")
        self.expect(F, "SPIN <malformed>", "SPIN SIDEWAYS", "SPIN", lambda r: bool(hp.REPLY["spin_err"].match(r)))
        # MOVE (v4): one-shot gestures. Every known name is OK on any skin (one it cannot draw does
        # nothing); an unknown one is an error, so a typo in a show is loud.
        for m in hp.MOVES:
            self.expect(F, f"MOVE {m}", f"MOVE {m}", "MOVE", lambda r, m=m: r == f"OK move {m}")
        self.expect(F, "MOVE <unknown name>", "MOVE no_such_move", "MOVE", lambda r: r == "ERR move no_such_move")
        # v5: device levels (clamped on the device; the reply carries the APPLIED value) and ARMED.
        self.expect(F, "VOLUME 0.4 1", "VOLUME 0.4 1", "VOLUME", lambda r: bool(hp.REPLY["volume_ok"].match(r)))
        self.expect(F, "BRIGHT 0.6", "BRIGHT 0.6", "BRIGHT", lambda r: bool(hp.REPLY["bright_ok"].match(r)))
        self.expect(F, "BRIGHT below the floor is clamped up", "BRIGHT 0.01", "BRIGHT",
                    lambda r: bool(hp.REPLY["bright_ok"].match(r)) and float(r.split()[-1]) > 0.01)
        self.expect(F, "VOLUME <out of range>", "VOLUME 2", "VOLUME", lambda r: bool(hp.REPLY["volume_err"].match(r)))
        self.expect(F, "BRIGHT <malformed>", "BRIGHT LOUD", "BRIGHT", lambda r: bool(hp.REPLY["bright_err"].match(r)))
        for line, want in (("ARMED WAIT", "OK armed wait"), ("ARMED 5", "OK armed 5"), ("ARMED OFF", "OK armed off")):
            self.expect(F, line, line, "ARMED", lambda r, w=want: r == w)
        self.expect(F, "ARMED <malformed>", "ARMED SOON", "ARMED", lambda r: bool(hp.REPLY["armed_err"].match(r)))
        # v6: the pack's charge for the gauge; out of range is an ERR, never clamped.
        self.expect(F, "BATT 64 1", "BATT 64 1", "BATT", lambda r: r == "OK batt 64 1")
        self.expect(F, "BATT - 0 (no BMS reading)", "BATT - 0", "BATT", lambda r: r == "OK batt - 0")
        self.expect(F, "BATT <out of range> is refused", "BATT 150 1", "BATT", lambda r: bool(hp.REPLY["batt_err"].match(r)))
        self.expect(F, "BATT <malformed>", "BATT full", "BATT", lambda r: bool(hp.REPLY["batt_err"].match(r)))
        # v8: the state in plain words. The dashboard renews it with every STATE, so none is put back.
        self.expect(F, "CAPTION Ready", "CAPTION Ready", "CAPTION", lambda r: r == "OK caption")
        self.expect(F, "CAPTION Problem: lift not responding", "CAPTION Problem: lift not responding", "CAPTION",
                    lambda r: r == "OK caption")
        self.expect(F, "CAPTION OFF", "CAPTION OFF", "CAPTION", lambda r: r == "OK caption off")
        self.expect(F, "VOLUME IDLE (put back)", "VOLUME IDLE", "VOLUME",
                    lambda r: bool(hp.REPLY["volume_ok"].match(r)) and " idle " in r)
        self.expect(F, "BRIGHT IDLE (put back)", "BRIGHT IDLE", "BRIGHT",
                    lambda r: bool(hp.REPLY["bright_ok"].match(r)) and " idle " in r)
        back = getattr(self, "state_found", None)
        if back in hp.STATES:
            self.ask(F, f"STATE {back}")
            self.note(PASS, F, f"STATE put back to {back}")
        # v7: the chest light. The dashboard puts its own colour back within 5 s.
        self.expect(N, "CHEST 1500", "CHEST 1500", "CHEST", lambda r: r == "OK chest 1500")
        self.expect(N, "CHEST 500 (the form sent, not \"off\")", "CHEST 500", "CHEST", lambda r: r == "OK chest 500")
        self.expect(N, "CHEST OFF", "CHEST OFF", "CHEST", lambda r: bool(hp.REPLY["chest_off"].match(r)))
        self.expect(N, "TOUCH HOLD 300", "TOUCH HOLD 300", "TOUCH",
                    lambda r: bool(hp.REPLY["touch_hold"].match(r)))
        self.expect(N, "TOUCH OFF", "TOUCH OFF", "TOUCH", lambda r: bool(hp.REPLY["touch_off"].match(r)))

    # ---- the sampled checks: what the head DOES, not what it says --------------------------------
    #
    # A reply-only check passed a GAZE that crawled at ~3 deg/s while every reply said OK (2026-10-08).
    # So each leased message is also judged on the angle over time. The angle is what the neck
    # COMMANDS: the servos report nothing back, so this verifies the control law; a person watching
    # verifies the mechanism.
    _PING_ANGLE = re.compile(r"pantilt: (?:[A-Za-z()]+ )*p(-?\d+(?:\.\d+)?) t(-?\d+(?:\.\d+)?)")
    REACH_TOL_DEG = 2.0
    HOLD_TOL_DEG = 1.0
    MIN_RATE_FRACTION = 0.5          # of the head's slew rate: a crawl fails, a slow servo does not
    MIN_RATE_MOVE_DEG = 15.0         # a shorter move ends inside the rate window and proves nothing
    JUMP_TOL_DEG = 3.0               # sampling jitter and the 1.5 deg write deadband, with room

    def status(self):
        """(pan, tilt, owner) now, from NECK — one coherent snapshot. A head without NECK (today's
        Tab5) gives the angle in PING's pan/tilt field, which is free text for people, not a field to
        parse; it is read here only until NECK exists, and its owner is None (unreadable)."""
        r = self.ask(hp.NECK, "NECK")
        m = hp.REPLY["neck_ok"].match(r or "")
        if m:
            return float(m.group(1)), float(m.group(2)), m.group(6)
        m = self._PING_ANGLE.search(self.ask(hp.NECK, "PING") or "")
        return (float(m.group(1)), float(m.group(2)), None) if m else None

    def angle(self):
        a = self.status()
        return a[:2] if a else None

    def drive(self, line, seconds, renew_s=0.2, sample_s=0.1):
        """Renew `line` every renew_s for `seconds`, sampling.
        -> [(t since this call, pan, tilt, owner, absolute monotonic time)]"""
        out, t0, next_renew = [], time.monotonic(), 0.0
        while (t := time.monotonic() - t0) < seconds:
            if line and t >= next_renew:
                self.ask(hp.NECK, line)
                next_renew = t + renew_s
            a = self.status()
            if a:
                out.append((t, a[0], a[1], a[2], time.monotonic()))
            time.sleep(sample_s)
        return out

    @staticmethod
    def peak_rate(samples, span_s=0.3):
        best = 0.0
        for i, (t0, p0, *_) in enumerate(samples):
            for t1, p1, *_ in samples[i + 1:]:
                if t1 - t0 >= span_s:
                    best = max(best, abs(p1 - p0) / (t1 - t0))
                    break
        return best

    @staticmethod
    def biggest_step(samples):
        """The largest pan change between consecutive samples, beyond what the slew rate allows
        in that interval. Positive means a JUMP."""
        worst = float("-inf")
        for a, b in zip(samples, samples[1:]):        # absolute times: samples may span two drives
            worst = max(worst, abs(b[1] - a[1]) - hp.SLEW_DPS * (b[4] - a[4]))
        return worst

    def sampled(self, label, verb, start_line, renew_line, target_pan, off_line, lease_ms, source):
        """Move the pan by a lease-holding message (`source` is the owner it should make) and judge
        what the head DOES: it holds ownership while renewed, reaches the target in time at no less
        than half the slew rate, and when the lease expires it never JUMPS — it stays where it is
        (owner=none) or the next live source takes it over at the slew rate."""
        N = hp.NECK
        a0 = self.angle()
        if a0 is None:
            return self.note(SKIP, N, f"{label}: sampled", "no angle to read (no NECK, no pan/tilt in PING)")
        r = self.ask(N, start_line)
        if r is None or hp.REPLY["unknown"].match(r) or r.startswith("ERR"):
            status = PENDING if (r and hp.REPLY["unknown"].match(r)
                                 and hp.MESSAGES[verb][1] in hp.NOT_YET_SHIPPING) else SKIP
            return self.note(status, N, f"{label}: sampled", f"{verb} not available: {r}")
        dist = abs(target_pan - a0[0])
        budget = 1.5 * dist / hp.SLEW_DPS + 0.5
        moving = self.drive(renew_line, budget + 0.4)
        owners = [x[3] for x in moving if x[3] is not None]
        if not owners:
            self.note(SKIP, N, f"{label}: holds ownership while renewed", "owner unreadable without NECK")
        else:
            lost = [o for o in owners if o != source]
            self.note(PASS if not lost else FAIL, N, f"{label}: holds ownership while renewed",
                      f"owner={source} in all {len(owners)} samples" if not lost
                      else f"owner was {sorted(set(lost))} in {len(lost)} of {len(owners)} samples: a flapping lease")
        reached = [t for t, p, *_ in moving if abs(p - target_pan) <= self.REACH_TOL_DEG]
        rate = self.peak_rate(moving)
        ok_reach = bool(reached) and reached[0] <= budget
        ok_rate = rate >= self.MIN_RATE_FRACTION * hp.SLEW_DPS
        self.note(PASS if ok_reach else FAIL, N, f"{label}: reaches pan {target_pan:.0f}",
                  f"from {a0[0]:.0f} in {reached[0]:.2f} s (budget {budget:.2f} s)" if reached
                  else f"never within {self.REACH_TOL_DEG} deg in {budget:.2f} s; last pan "
                       f"{moving[-1][1]:.1f}" if moving else "no samples")
        if dist < self.MIN_RATE_MOVE_DEG:
            self.note(SKIP, N, f"{label}: slews", f"a {dist:.0f} deg move is too short to judge a rate")
        else:
            self.note(PASS if ok_rate else FAIL, N, f"{label}: slews",
                      f"peak {rate:.0f} deg/s against {hp.SLEW_DPS:.0f} (needs {self.MIN_RATE_FRACTION:.0%})")
        # Let the lease run out without renewing, then watch. It must never jump. With no live source
        # it stays where it is; otherwise the next one down takes over, at the slew rate (the Tab5's
        # always-live local tracker returns it to centre this way, by design).
        after = self.drive(None, lease_ms / 1000.0 + 1.2)
        late = [x for x in after if x[0] > lease_ms / 1000.0 + 0.2]
        if late and moving:
            step = self.biggest_step(moving[-1:] + after)
            self.note(PASS if step <= self.JUMP_TOL_DEG else FAIL, N, f"{label}: lease expired, no jump",
                      f"largest step {step:+.1f} deg beyond the slew rate (allowed {self.JUMP_TOL_DEG})")
            owner = late[-1][3]
            pans = [p for _, p, *_ in late]
            if owner is None:
                self.note(SKIP, N, f"{label}: lease expired, handed over",
                          f"owner unreadable without NECK; pan {pans[0]:.1f}..{pans[-1]:.1f}")
            elif owner == "none":
                # Nobody owns it, so nothing may have moved it since the lease left it: judged over
                # the whole wait, not only its end, or a head that sprang back early would pass.
                left_at = moving[-1][1]
                drift = max(abs(x[1] - left_at) for x in after)
                self.note(PASS if drift <= self.HOLD_TOL_DEG else FAIL, N,
                          f"{label}: lease expired, stays put (owner=none)",
                          f"left at pan {left_at:.1f}; moved up to {drift:.1f} deg since")
            else:
                self.note(PASS if owner != source else FAIL, N, f"{label}: lease expired, handed over",
                          f"owner={owner} (was {source}); pan {pans[0]:.1f}..{pans[-1]:.1f}")
        if off_line:
            self.ask(N, off_line)

    def motion_sampled(self):
        a0 = self.angle()
        if a0 is None:
            return self.note(SKIP, hp.NECK, "sampled motion", "no angle to read")
        home = a0[0]
        out = home + 20 if home <= 150 else home - 20
        self.sampled("GAZE", "GAZE", f"GAZE {out:.0f} {a0[1]:.0f} 600", f"GAZE {out:.0f} {a0[1]:.0f} 600",
                     out, "GAZE OFF", 600, "gaze")
        # A tracker is held to the autonomous envelope, so move within it.
        tgt = 100.0 if self.angle()[0] < 95 else 85.0
        self.sampled("TRACK thor", "TRACK", f"TRACK thor {tgt:.0f} {a0[1]:.0f} 600",
                     f"TRACK thor {tgt:.0f} {a0[1]:.0f} 600", tgt, "TRACK thor OFF", 600, "thor")
        if "PARK" in hp.KINDS.get(self.kind.get(hp.NECK), set()):
            # Off centre first, so the park has a distance to travel and a rate to show.
            self.drive(f"GAZE 110 {a0[1]:.0f} 600", 1.5 * abs(self.angle()[0] - 110) / hp.SLEW_DPS + 0.4)
            self.ask(hp.NECK, "GAZE OFF")
            self.sampled("PARK", "PARK", "PARK 1000", "PARK 1000", 90.0, "PARK OFF", 1000, "park")
        # Back where it started, so the check leaves the head as it found it.
        back = self.drive(f"GAZE {home:.0f} {a0[1]:.0f} 600", 1.5 * abs(self.angle()[0] - home) / hp.SLEW_DPS + 0.6)
        self.ask(hp.NECK, "GAZE OFF")
        if back:
            self.note(PASS, hp.NECK, "put back", f"pan {back[-1][1]:.0f} (started at {home:.0f})")

    def motion(self):
        N = hp.NECK
        kind = self.kind.get(N)
        self.expect(N, "GAZE 95 92 800", "GAZE 95 92 800", "GAZE",
                    lambda r: bool(hp.REPLY["gaze_ok"].match(r)))
        # Precedence: a tracker below a live GAZE is accepted but does not own the head.
        self.expect(N, "TRACK thor under a GAZE", "TRACK thor 92 91 300", "TRACK",
                    lambda r: (m := hp.REPLY["track_ok"].match(r)) is not None and m.group(4) == "gaze")
        self.expect(N, "GAZE OFF", "GAZE OFF", "GAZE", lambda r: bool(hp.REPLY["gaze_off"].match(r)))
        self.expect(N, "TRACK thor", "TRACK thor 92 91 300", "TRACK",
                    lambda r: (m := hp.REPLY["track_ok"].match(r)) is not None and m.group(4) == "thor")
        self.expect(N, "TRACK thor OFF", "TRACK thor OFF", "TRACK",
                    lambda r: bool(hp.REPLY["track_off"].match(r)))
        if kind in hp.TRACK_LOCAL_REFUSED:
            self.expect(N, "TRACK local (refused on this head)", "TRACK local 92 91 300", "TRACK",
                        lambda r: bool(hp.REPLY["track_local_refused"].match(r)))
        else:
            self.expect(N, "TRACK local", "TRACK local 92 91 300", "TRACK",
                        lambda r: bool(hp.REPLY["track_ok"].match(r)))
            self.expect(N, "TRACK local OFF", "TRACK local OFF", "TRACK",
                        lambda r: bool(hp.REPLY["track_off"].match(r)))
        self.expect(N, "TOUCH RATE 0 0 300", "TOUCH RATE 0 0 300", "TOUCH",
                    lambda r: bool(hp.REPLY["touch_rate"].match(r)))
        self.expect(N, "TOUCH OFF", "TOUCH OFF", "TOUCH", lambda r: bool(hp.REPLY["touch_off"].match(r)))
        self.expect(N, "PARK 800", "PARK 800", "PARK", lambda r: bool(hp.REPLY["park_ok"].match(r)))
        self.expect(N, "NECK while parked", "NECK", "NECK",
                    lambda r: self._neck_ok(r, owner="park") if "PARK" in hp.KINDS.get(kind, set())
                    else self._neck_ok(r))
        self.expect(N, "PARK OFF", "PARK OFF", "PARK", lambda r: bool(hp.REPLY["park_off"].match(r)))


def run(face: str, neck: str | None = None, port: int = hp.PORT, active=False, motion=False,
        timeout: float = 0.5, sampled: bool = True, themes=()) -> Run:
    r = Run(Link(port, timeout), face, neck or face, themes)
    r.ping()
    if not r.answered():
        return r
    r.read_only()
    if active:
        r.active()
    if motion:
        r.motion()
        if sampled:
            r.motion_sampled()
    return r


def _is_local(host: str) -> bool:
    try:
        return socket.gethostbyname(host.partition(":")[0]).startswith("127.")
    except OSError:
        return False


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("face", help="the face's host (the whole head, for the Tab5)")
    ap.add_argument("--neck", help="the neck's host, if it is a separate box")
    ap.add_argument("--port", type=int, default=hp.PORT)
    ap.add_argument("--theme", action="append", default=[], metavar="ID",
                    help="a discovered theme the face should have (its content folder's); repeatable")
    ap.add_argument("--active", action="store_true")
    ap.add_argument("--motion", action="store_true")
    ap.add_argument("--at-the-robot", action="store_true")
    ap.add_argument("--strict", action="store_true")
    a = ap.parse_args(argv)
    if (a.active or a.motion) and not a.at_the_robot and not all(
            _is_local(h) for h in filter(None, (a.face, a.neck))):
        print("refused: --active and --motion change the face or move the head. Against a robot,\n"
              "add --at-the-robot, and only with a person there watching.", file=sys.stderr)
        return 2
    r = run(a.face, a.neck, a.port, a.active, a.motion, themes=a.theme)
    for status, half, check, detail in r.results:
        print(f"{status:<8}{half:<6}{check:<40}{detail}")
    counts = {s: sum(1 for x in r.results if x[0] == s) for s in (PASS, FAIL, PENDING, SKIP)}
    print("  ".join(f"{k} {v}" for k, v in counts.items()))
    bad = counts[FAIL] + (counts[PENDING] if a.strict else 0)
    return 1 if bad or not r.answered() else 0


if __name__ == "__main__":
    sys.exit(main())
