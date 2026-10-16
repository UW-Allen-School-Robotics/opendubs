#!/usr/bin/env python3
"""The Surge hand's output driver: the Alt-Bionics Surge 1 over its AT text protocol (RS-485, 115200 8N1).
Standard library only, so it runs on the Thor with nothing installed.

    python3 surge_at.py state --port P                  # the six joints' angles and error flags
    python3 surge_at.py clear --port P                  # clear latched errors: RECALIBRATES (~20 s, the hand MOVES)
    python3 surge_at.py play seq.json --port P          # play a hardware sequence (cue.py export-effector)
    python3 surge_at.py play seq.json --dry-run         # check it and print what it would do; no port

THE INTERFACE the runner's output will call (not wired into the runner yet; a bench player uses it now):
    d = SurgeAT(); d.open(port); d.start_stream(); d.apply(t, {"I": deg, …}); d.home(); d.close()

THE PROTOCOL, as found on the robot's hand (the Surge Hand investigation, 2026-10-16):
  * a command is text ending "\\n\\r"; replies echo it with "=" (`AT+GJA?I` -> `AT+GJA=13.1`);
  * joints I M R P (fingers), T (thumb flex), X (thumb rotation); `AT+SJA=<deg>,<joint>` sets a target;
  * `AT+SEF=0` turns command feedback OFF while streaming, so replies cannot collide on the half-duplex
    bus (queries such as AT+GJA? still answer, within ~30 ms); `AT+SEF=1` turns it back on;
  * a BLOCKED joint latches an error (`AT+GJE?<j>` reads 1). On loaded fingers it does not block motion.
    Clearing it needs a recalibration, `AT+RCH=1` (~20 s; the hand moves and returns to rest), then
    `AT+SMC=0` (glove mode off). That moves the hand, so it is a command of its own, never automatic;
  * there is NO WATCHDOG: a target just holds. So every exit, normal or not, sends the open pose
    (`AT+SPP=0`) and turns feedback back on.

Degrees here are HARDWARE degrees, already mapped from the twin by the exporter (the registry's
`calibration`), and clamped to the per-joint limits below before anything is sent.
"""
from __future__ import annotations

import argparse
import json
import math
import os
import re
import select
import sys
import time

JOINTS = "IMRPTX"
LIMITS = {"I": (0.0, 95.0), "M": (0.0, 95.0), "R": (0.0, 95.0), "P": (0.0, 95.0), "T": (0.0, 72.0), "X": (0.0, 90.0)}
EOL = "\n\r"
RATE_HZ = 20.0
RECAL_S = 25.0
MAX_STREAM_S = 60.0          # the bench player's cap, unless --long


class SurgeAT:
    def __init__(self, log=print):
        self.fd = None
        self.log = log
        self.streaming = False
        self.last = {}

    # ---- the port ---------------------------------------------------------------------------------------
    def open(self, port: str, baud: int = 115200) -> dict:
        """Open the port, put the hand in its working state (feedback on, glove mode off) and return its
        state: {"angles": {j: deg}, "errors": {j: code}}."""
        import termios
        fd = os.open(port, os.O_RDWR | os.O_NOCTTY | os.O_NONBLOCK)
        a = termios.tcgetattr(fd)
        speed = getattr(termios, f"B{baud}")
        a[0] = a[1] = a[3] = 0
        a[2] = termios.CS8 | termios.CREAD | termios.CLOCAL
        a[4] = a[5] = speed
        a[6][termios.VMIN] = 0
        a[6][termios.VTIME] = 0
        termios.tcsetattr(fd, termios.TCSANOW, a)
        termios.tcflush(fd, termios.TCIOFLUSH)
        self.fd = fd
        self.at("AT+SEF=1")
        self.at("AT+SMC=0")
        return self.state()

    def _read_for(self, seconds: float) -> str:
        out, end = b"", time.time() + seconds
        while time.time() < end:
            r, _, _ = select.select([self.fd], [], [], max(0.0, end - time.time()))
            if r:
                try:
                    out += os.read(self.fd, 4096)
                except BlockingIOError:
                    pass
        return out.decode("ascii", "replace")

    def send(self, cmd: str) -> None:
        os.write(self.fd, (cmd + EOL).encode())

    def at(self, cmd: str, wait: float = 0.35) -> str:
        self.send(cmd)
        return self._read_for(wait)

    # ---- reading ----------------------------------------------------------------------------------------
    def angle(self, j: str, wait: float = 0.06):
        m = re.search(r"GJA=([-0-9.]+)", self.at(f"AT+GJA?{j}", wait))
        return float(m.group(1)) if m else None

    def errors(self) -> dict:
        out = {}
        for j in JOINTS:
            m = re.search(r"GJE=(\S+)", self.at(f"AT+GJE?{j}", 0.2))
            out[j] = m.group(1) if m else None
        return out

    def state(self) -> dict:
        return {"angles": {j: self.angle(j, 0.2) for j in JOINTS}, "errors": self.errors()}

    # ---- moving -----------------------------------------------------------------------------------------
    def start_stream(self) -> None:
        """Feedback off, so streamed targets get no replies to collide with."""
        self.at("AT+SEF=0", 0.3)
        self.streaming = True

    def apply(self, t: float, joints: dict) -> int:
        """Send each joint's target (hardware degrees), clamped to its limits; only joints that changed
        by 0.1 degree or more. Returns how many commands went out."""
        n = 0
        for j, v in joints.items():
            if j not in LIMITS or v is None or not math.isfinite(v):
                continue
            lo, hi = LIMITS[j]
            v = round(min(hi, max(lo, float(v))), 1)
            if self.last.get(j) == v:
                continue
            self.send(f"AT+SJA={v:.1f},{j}")
            self.last[j] = v
            n += 1
            time.sleep(0.002)             # a breath between commands on the shared bus
        return n

    def home(self) -> None:
        """The open pose."""
        self.send("AT+SPP=0")
        self.last = {}

    def clear_errors(self) -> dict:
        """RECALIBRATE to clear latched errors: the hand MOVES for ~20 s and returns to rest. Then glove
        mode off. Returns the errors afterwards."""
        self.log("surge: recalibrating to clear errors (AT+RCH=1) — the hand moves for ~20 s")
        self.at("AT+RCH=1", 1.0)
        self._read_for(RECAL_S)
        self.at("AT+SMC=0")
        return self.errors()

    def close(self) -> None:
        """Every exit: the open pose, feedback back on, the port closed. Safe to call twice."""
        if self.fd is None:
            return
        try:
            self.home()
            time.sleep(0.05)
            self.at("AT+SEF=1", 0.4)
        finally:
            os.close(self.fd)
            self.fd = None
            self.streaming = False


# ---- the bench player: a hardware sequence (cue.py export-effector) ---------------------------------------
# The format agreed with the Surge Hand investigation, whose own bench player (hw/seq_play.py) is the
# reference this one matches: the same gait formula, a blend into each gait, joints ramped then held.
#   {"name", "hand", "segments": [{"type": "gait", "seconds", "params" | "params_file"}
#                                 | {"type": "joints", "deg", "ramp", "hold"} | {"type": "pose", "pose", "hold"}
#                                 | {"type": "wait", "seconds"}]}
GROUPS = {"pairs": ({"I", "R"}, {"M", "P"}), "sides": ({"I", "M"}, {"R", "P"}), "sync": (set("IMRP"), set())}
SIDE = {"I": "trim_index", "M": "trim_index", "R": "trim_pinky", "P": "trim_pinky"}
GAIT_DEFAULTS = dict(freq=0.8, lo=0.15, hi=1.35, trim_index=1.0, trim_pinky=1.0, thumb_rot=0.3, thumb_flex=0.2,
                     thumb_amp_rot=0.0, thumb_amp_flex=0.0, thumb_phase=0.0, grouping="pairs", phase=0.5, skew=0.5,
                     thumb_scale=[1.0, 1.0])
BLEND_S = 0.5
SAFE_POSES = (0,)                 # open; the closing presets latch error flags (power, chuck) — refused here


def _stroke(u, skew):
    u %= 1.0
    if u < skew:
        return 0.5 - 0.5 * math.cos(math.pi * u / skew)
    return 0.5 + 0.5 * math.cos(math.pi * (u - skew) / (1.0 - skew))


def gait_targets(p: dict, t: float) -> dict:
    """The gait model's finger strokes and thumb, t seconds in, as hardware degrees (radians sent unscaled)."""
    ga, _ = GROUPS[p["grouping"]]
    out = {}
    for j in "IMRP":
        s = _stroke(p["freq"] * t + (0.0 if j in ga else p["phase"]), p["skew"])
        out[j] = math.degrees(p["lo"] + (p["hi"] - p["lo"]) * p[SIDE[j]] * s)
    st = 0.5 - 0.5 * math.cos(2 * math.pi * (p["freq"] * t + p["thumb_phase"]))
    sx, sf = p["thumb_scale"]
    out["X"] = math.degrees(p["thumb_rot"] + p["thumb_amp_rot"] * st) * sx
    out["T"] = math.degrees(p["thumb_flex"] + p["thumb_amp_flex"] * st) * sf
    return out


def load_sequence(path: str, allow_presets=False) -> tuple:
    """(sequence, [(kind, arg, seconds)], total s), checked: every joint value in range, gait parameters sane,
    and no closing preset unless allowed."""
    with open(path) as fh:
        seq = json.load(fh)
    base = os.path.dirname(os.path.abspath(path))
    segs, total = [], 0.0
    for i, sg in enumerate(seq.get("segments") or []):
        kind = sg.get("type")
        if kind == "gait":
            params = dict(sg.get("params") or {})
            if "params_file" in sg:
                f = sg["params_file"] if os.path.isabs(sg["params_file"]) else os.path.join(base, sg["params_file"])
                with open(f) as fh:
                    params = {**json.load(fh)["params"], **params}
            g = {**GAIT_DEFAULTS, **params}
            if g["grouping"] not in GROUPS or not 0.05 <= g["skew"] <= 0.95 or not 0.1 <= g["freq"] <= 2.0:
                raise SystemExit(f"surge: {path}: segment {i}: grouping, skew or freq out of range")
            segs.append(("gait", g, float(sg["seconds"])))
            total += BLEND_S + float(sg["seconds"])
        elif kind == "joints":
            deg = {k: float(v) for k, v in (sg.get("deg") or {}).items()}
            for k, v in deg.items():
                if k not in LIMITS or not LIMITS[k][0] <= v <= LIMITS[k][1]:
                    raise SystemExit(f"surge: {path}: segment {i}: {k} {v} outside {LIMITS.get(k)} — refused")
            ramp, hold = float(sg.get("ramp", 0.5)), float(sg.get("hold", 0.0))
            segs.append(("joints", deg, (ramp, hold)))
            total += ramp + hold
        elif kind == "pose":
            n = int(sg["pose"])
            if n not in SAFE_POSES and not allow_presets:
                raise SystemExit(f"surge: {path}: segment {i}: preset {n} closes the hand and latches error flags "
                                 "— refused (--allow-presets to play it anyway)")
            segs.append(("pose", n, float(sg.get("hold", 2.0))))
            total += float(sg.get("hold", 2.0))
        elif kind == "wait":
            segs.append(("wait", None, float(sg["seconds"])))
            total += float(sg["seconds"])
        else:
            raise SystemExit(f"surge: {path}: segment {i}: unknown type {kind!r}")
    if not segs:
        raise SystemExit(f"surge: {path}: no segments")
    return seq, segs, total


def play(path: str, port: str | None, dry_run=False, long=False, allow_presets=False, log=print) -> int:
    seq, segs, total = load_sequence(path, allow_presets)
    if total > MAX_STREAM_S and not long:
        raise SystemExit(f"surge: the sequence is {total:.0f} s, over the bench cap of {MAX_STREAM_S:g} s (--long to allow)")
    if seq.get("hand", "left") != "left":
        log(f"surge: NOTE the sequence says hand={seq.get('hand')!r}; the robot's hand is a LEFT hand")
    log(f"surge: {seq.get('name', path)} — {len(segs)} segments, ~{total:.1f} s")
    if dry_run:
        for i, (kind, a, b) in enumerate(segs):
            if kind == "gait":
                lo, hi = {}, {}
                for k in range(int(b * RATE_HZ)):
                    for j, v in gait_targets(a, k / RATE_HZ).items():
                        lo[j], hi[j] = min(lo.get(j, 1e9), v), max(hi.get(j, -1e9), v)
                log(f"  {i:2d} gait {b:.1f}s {a['grouping']}: " + " ".join(f"{j} {lo[j]:.0f}-{hi[j]:.0f}" for j in JOINTS))
            elif kind == "joints":
                log(f"  {i:2d} joints {a} ramp {b[0]:.2f}s hold {b[1]:.2f}s")
            else:
                log(f"  {i:2d} {kind} {a if a is not None else ''} {b:.1f}s")
        return 0
    d = SurgeAT(log)
    st = d.open(port)
    log(f"surge: start {st}")
    bad = {j: e for j, e in st["errors"].items() if e not in (None, "0")}
    if bad:
        log(f"surge: NOTE latched error flags {bad}: on loaded fingers they do not block motion; "
            "`surge_at.py clear` recalibrates to clear them (the hand moves)")
    sent = 0

    def stream(fn, seconds):
        nonlocal sent
        t0, k = time.time(), 0
        while True:
            t = time.time() - t0
            if t > seconds:
                return
            sent += d.apply(t, fn(t))
            k += 1
            time.sleep(max(0.0, t0 + k / RATE_HZ - time.time()))

    try:
        d.start_stream()
        cur = {j: v for j, v in st["angles"].items() if v is not None}
        for i, (kind, a, b) in enumerate(segs):
            t_seg = time.time()
            if kind == "gait":
                g0 = gait_targets(a, 0.0)
                s0 = {j: cur.get(j, g0[j]) for j in g0}
                stream(lambda t: {j: s0[j] + (g0[j] - s0[j]) * min(1.0, t / BLEND_S) for j in g0}, BLEND_S)
                stream(lambda t: gait_targets(a, t), b)
                cur.update(gait_targets(a, b))
            elif kind == "joints":
                ramp, hold = b
                s0 = {j: cur.get(j, v) for j, v in a.items()}
                stream(lambda t: {j: s0[j] + (a[j] - s0[j]) * min(1.0, t / max(ramp, 1e-3)) for j in a}, max(ramp, 0.05))
                cur.update(a)
                time.sleep(hold)
            elif kind == "pose":
                d.send(f"AT+SPP={a}")
                d.last = {}
                time.sleep(b)
            else:
                time.sleep(b)
            log(f"surge: segment {i:2d} {kind:6s} done in {time.time() - t_seg:4.1f} s")
    finally:
        d.close()
    log(f"surge: sent {sent} joint commands; the hand is open and feedback is back on")
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("cmd", choices=("state", "clear", "play"))
    ap.add_argument("stream", nargs="?")
    ap.add_argument("--port", help="the hand's serial device (its /dev/serial/by-id path)")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--long", action="store_true", help=f"allow a sequence over {MAX_STREAM_S:g} s")
    ap.add_argument("--allow-presets", action="store_true", help="play closing presets (AT+SPP=1..6); they latch errors")
    a = ap.parse_args(argv)
    if a.cmd == "play":
        if not a.stream or not (a.port or a.dry_run):
            ap.error("play needs a sequence file and --port (or --dry-run)")
        return play(a.stream, a.port, a.dry_run, long=a.long, allow_presets=a.allow_presets)
    if not a.port:
        ap.error("--port is required")
    d = SurgeAT()
    st = d.open(a.port)
    try:
        if a.cmd == "clear":
            print("errors after:", d.clear_errors())
        else:
            print(st)
    finally:
        d.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
