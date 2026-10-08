#!/usr/bin/env python3
"""The show runner — its core, driving only the twins (workplan Phase 5).

    python3 runner.py run shows/hello.yaml               # serves the twin feed on :8092
    ./twin.sh --composer http://127.0.0.1:8092           # the MuJoCo twin follows the runner
    python3 face_bridge.py --composer http://127.0.0.1:8092
    curl -X POST localhost:8092/api/sim/estop            # the fake robot: estop, deadman, ch5…

A show runs through VALIDATING -> PREFLIGHT -> PREPPING -> WAITING_FOR_START -> RUNNING and ends
COMPLETED, ABORTED, ESTOPPED or REFUSED. The runner is a lease-holding motion source like any other
(standing rule 1): it may only run in AUTO with the deadman held, and the moment that stops being
true — or stops being KNOWN — the show ends. Preemption aborts; it never pauses.

WHAT THIS CAN COMMAND: nothing on the robot. Its outputs are sinks; the only one built is the twin
feed (the same /api/twin the composer serves), so the MuJoCo and face twins play exactly what the
runner decides. Live output is a separately armed binding (workplan 5.6), not built. The robot's
state comes from a provider: a fake, settable over HTTP, so every abort path can be exercised with
no robot (--robot fake, the default); or, on the robot, the dashboard's own read-only view of it
(--robot dashboard, plan 4.4c), so preflight and aborts answer to the real e-stop, CH5 and deadman
while the output is still only the twins.

Every status change and every abort cause is logged as one JSON line (--log), so an abort can be
reconstructed afterwards (5.5).
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import threading
import time
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import cue  # noqa: E402

# ---- status, after Spot's ChoreographyStatus; codes after BML's typed warnings (PA proposal 8) --
VALIDATING, PREFLIGHT, PREPPING, WAITING, RUNNING = (
    "VALIDATING", "PREFLIGHT", "PREPPING", "WAITING_FOR_START", "RUNNING")
COMPLETED, ABORTED, ESTOPPED, REFUSED = "COMPLETED", "ABORTED", "ESTOPPED", "REFUSED"
FINAL = (COMPLETED, ABORTED, ESTOPPED, REFUSED)

TICK_S = 0.02             # 50 Hz
LEASE_S = 0.25            # like every remote command on this robot: no heartbeat for 250 ms, no lease
START_LEAD_S = 2.0        # PLACEHOLDER: time for a start to reach the tablet and the arm goals to land
AUDIO_FADE_S = 0.25       # DEC-6: an abort fades the music; an e-stop cuts it

# What every slot is left doing when a show ABORTS (DEC-6: argued per slot, not assumed "stop").
# An e-stop needs none of this: the contactor has already cut motion power, and audio is cut too.
ABORT_STATE = {
    "ARM_L": "hold where it is — the impedance controller holds the last target; dropping is worse",
    "ARM_R": "hold where it is — as ARM_L",
    "GRIP_L": "hold — opening could drop what it holds, closing could pinch",
    "GRIP_R": "hold — as GRIP_L",
    "HEAD": "stop where it is — its target becomes its current angle",
    "LIFT": "relays off — the lift stops where it is; nothing drives it",
    "DANCE": "base at zero velocity, immediately",
    "SOUND": "stopped",
    "FACE": "handed back to the face's own state-driven behaviour (EMOTE AUTO); the theme stays",
    "LIGHT": "released to the robot's state colour",
    "MUSIC": f"faded out over {AUDIO_FADE_S:g} s",
}


# ---- the robot's state ------------------------------------------------------------------------
class FakeRobotState:
    """What the real provider will read from [AUTH] telemetry and the arm stack. Every field may be
    None, meaning UNKNOWN — and unknown refuses or aborts, never defaults (5.2)."""

    def __init__(self, **kw):
        self.lock = threading.Lock()
        self.s = {"ch5": "AUTO", "deadman": True, "estop": False, "arms": "active",
                  "tab5": True, "fault": None, "arm_pose": {"ARM_L": {}, "ARM_R": {}}}
        self.s.update(kw)

    def read(self) -> dict:
        with self.lock:
            return json.loads(json.dumps(self.s))

    def set(self, **kw):
        with self.lock:
            self.s.update(kw)


class DashboardRobotState:
    """The robot's state as the dashboard reports it (GET /api/choreo/robot) — READ-ONLY: nothing
    here writes to the robot. Polled at 10 Hz; a reading older than STALE_S is no reading, so every
    field goes UNKNOWN and a running show ends (unknown never defaults, 5.2)."""

    POLL_S, STALE_S = 0.1, 0.5
    UNKNOWN = {"ch5": None, "deadman": None, "estop": None, "arms": None, "tab5": None,
               "fault": None, "arm_pose": {"ARM_L": {}, "ARM_R": {}}}

    def __init__(self, url="http://127.0.0.1:8080/api/choreo/robot"):
        self.url, self.lock = url, threading.Lock()
        self.last, self.at = None, 0.0
        threading.Thread(target=self._poll, daemon=True).start()

    def _get(self, probe=False, timeout=1.0):
        with urllib.request.urlopen(self.url + ("?probe=1" if probe else ""), timeout=timeout) as r:
            d = json.loads(r.read())
        with self.lock:
            self.last, self.at = d, time.monotonic()

    def _poll(self):
        while True:
            try:
                self._get()
            except Exception:  # noqa: BLE001  a missed poll is what STALE_S is for
                pass
            time.sleep(self.POLL_S)

    def refresh(self):
        """Before preflight: have the dashboard ask the arms and the head now (it does so only on
        demand, because the arm read uses the arms' CAN bus). A failure leaves them unknown."""
        try:
            self._get(probe=True, timeout=25.0)
        except Exception:  # noqa: BLE001
            pass

    def read(self) -> dict:
        with self.lock:
            fresh = self.last is not None and time.monotonic() - self.at < self.STALE_S
            return json.loads(json.dumps(self.last if fresh else self.UNKNOWN))


def preflight(requires: dict, st: dict) -> list:
    """Refusals, as (code, sentence). Built from the show's `requires:` (validated by cue.py), plus
    what every show needs: no e-stop, the deadman held, no fault — and nothing UNKNOWN."""
    out = []

    def need(field, ok, what):
        v = st.get(field)
        if v is None:
            out.append(("UNKNOWN_STATE", f"{field} is unknown — {what}; unknown refuses, never defaults"))
        elif not ok(v):
            out.append(("PRECONDITION_FAILED", f"{field} is {v!r} — {what}"))

    need("estop", lambda v: v is False, "the e-stop must be clear")
    need("deadman", lambda v: v is True, "the deadman must be held")
    if st.get("fault"):
        out.append(("PRECONDITION_FAILED", f"fault: {st['fault']}"))
    if "rc_authority" in requires:
        need("ch5", lambda v: v == requires["rc_authority"], f"CH5 must be {requires['rc_authority']}")
    if "arms" in requires:
        need("arms", lambda v: v == requires["arms"], f"the arms must be {requires['arms']}")
    if requires.get("tab5"):
        need("tab5", lambda v: v is True, "the Tab5 (head and face) must be up")
    return out


def monitor(requires: dict, st: dict):
    """Checked every tick while a show runs. (status, code, cause) to end it, or None."""
    if st.get("estop") is not False:
        return (ESTOPPED, "ESTOP", "e-stop asserted" if st.get("estop") else "e-stop state unknown")
    if st.get("deadman") is not True:
        return (ABORTED, "DEADMAN", "deadman released" if st.get("deadman") is False else "deadman unknown")
    if st.get("fault"):
        return (ABORTED, "FAULT", f"fault: {st['fault']}")
    if "rc_authority" in requires and st.get("ch5") != requires["rc_authority"]:
        return (ABORTED, "AUTHORITY", f"CH5 is {st.get('ch5')!r}, not {requires['rc_authority']}")
    return None


def prep_moves(tl: dict, measured: dict) -> tuple:
    """Moves that bring each arm to the show's first keyframe pose, timed by the distance at the
    speed caps (x PEAK_FACTOR, the warning-free time) — otherwise the first keyframe would slam the
    arm there with no speed cap at all (PA-2.5). Returns (seconds, {slot: target pose})."""
    secs, targets = 0.0, {}
    for slot in cue.ARM_SLOTS:
        kfs = tl.get("tracks", {}).get(slot) or []
        if not kfs:
            continue
        first = {int(j): v for j, v in kfs[0]["pose"].items()}
        now = {int(j): float(v) for j, v in (measured.get(slot) or {}).items()}
        need = max(abs(first[j] - now.get(j, 0.0)) / cue.SPEED_CAP_DEG_S[j] * cue.PEAK_FACTOR
                   for j in range(1, 8))
        secs, targets[slot] = max(secs, need), first
    return round(secs, 2), targets


# ---- sinks ------------------------------------------------------------------------------------
class TwinFeed:
    """The runner's output as the twins read it: the same /api/twin shape the composer serves,
    plus the runner's status. A twin following this plays exactly what the runner decides."""

    def __init__(self):
        self.lock = threading.Lock()
        self.d = {"rev": 0, "path": None, "timeline": None, "t": 0.0, "playing": False,
                  "at": time.time(), "status": None, "lease_until": 0.0}

    def show(self, path, tl):
        with self.lock:
            self.d.update(rev=self.d["rev"] + 1, path=path, timeline=tl)

    def playhead(self, t, playing, status):
        with self.lock:
            self.d.update(t=round(t, 4), playing=playing, status=status, at=time.time())

    def heartbeat(self):
        with self.lock:
            self.d["lease_until"] = time.time() + LEASE_S

    def twin(self):
        with self.lock:
            s = {k: v for k, v in self.d.items() if k != "timeline"}
        s["now"] = time.time()
        return s

    def timeline(self):
        with self.lock:
            return {"rev": self.d["rev"], "path": self.d["path"], "timeline": self.d["timeline"]}


# ---- the runner -------------------------------------------------------------------------------
class Runner:
    def __init__(self, path, limits_path, robot, feed=None, log=None, clock=time.monotonic,
                 sleep=time.sleep, start_lead=START_LEAD_S, audio_output=None):
        self.path, self.limits_path, self.robot = path, limits_path, robot
        self.audio_output = audio_output          # the operator's override (DEC-5), re-validated
        self.feed = feed or TwinFeed()
        self.log_fh, self.clock, self.sleep, self.start_lead = log, clock, sleep, start_lead
        self.status, self.ended = None, None      # ended: (status, code, cause, show time)
        self.sent: list = []                      # (show time, slot, cue) as dispatched
        self.stop_requested = False

    def _log(self, kind, **kw):
        rec = {"wall": round(time.time(), 3), "kind": kind, "status": self.status, **kw}
        if self.log_fh:
            self.log_fh.write(json.dumps(rec, sort_keys=True) + "\n")
            self.log_fh.flush()

    def _set(self, status, **kw):
        self.status = status
        self._log("status", **kw)

    def _end(self, status, code, cause, t=None):
        self.ended = (status, code, cause, t)
        self._set(status, code=code, cause=cause, show_t=t)
        if status == ABORTED:
            self._log("abort_state", slots=ABORT_STATE)
        elif status == ESTOPPED:
            self._log("estop", note="everything stopped at once; audio cut, not faded")
        self.feed.playhead(t or 0.0, False, status)
        return self.ended

    def request_stop(self):
        """An operator stop (the player's STOP button): an abort, not a pause."""
        self.stop_requested = True

    def run(self):
        # VALIDATING: the whole show, before any of it is sent (standing rule 3)
        self._set(VALIDATING, show=self.path)
        try:
            show = cue.load_show(self.path)
            show.audio_override = self.audio_output
            tl = show.export(cue.load_limits(self.limits_path), self.limits_path)
        except Exception as e:  # noqa: BLE001
            return self._end(REFUSED, "PARSING_FAILURE", f"{type(e).__name__}: {e}")
        if not tl["valid"]:
            return self._end(REFUSED, "INVALID_SHOW",
                             "; ".join(p["msg"] for p in tl["problems"][:3]))
        if tl.get("dance") is not None:
            self._log("note", note="DANCE runs only in the twin until the robot floor test (6.6)")
        self.feed.show(self.path, tl)

        # PREFLIGHT: requires vs the robot as it is now
        self._set(PREFLIGHT)
        if hasattr(self.robot, "refresh"):
            self.robot.refresh()
        st = self.robot.read()
        refusals = preflight(show.requires or {}, st)
        if refusals:
            return self._end(REFUSED, refusals[0][0], "; ".join(r[1] for r in refusals))

        # PREPPING: travel to the start pose, slowly enough to need no warning
        secs, targets = prep_moves(tl, st.get("arm_pose") or {})
        self._set(PREPPING, prep_s=secs, targets=targets)
        t_end = self.clock() + secs
        while self.clock() < t_end:
            if (stop := self._tick_checks(show, None)):
                return stop
            self.sleep(TICK_S)

        # WAITING_FOR_START: a start in the future, so the music and the arms can begin together
        self._set(WAITING, lead_s=self.start_lead)
        t0 = self.clock() + self.start_lead
        while self.clock() < t0:
            if (stop := self._tick_checks(show, None)):
                return stop
            self.sleep(TICK_S)

        # RUNNING
        self._set(RUNNING, duration_s=tl["duration_s"])
        events = sorted(((e["t"], slot, e["cue"]) for slot, evs in tl["tracks"].items() for e in evs),
                        key=lambda x: x[0])
        i = 0
        while True:
            t = self.clock() - t0
            if (stop := self._tick_checks(show, t)):
                return stop
            while i < len(events) and events[i][0] <= t:
                self.sent.append(events[i])
                self._log("cue", show_t=round(events[i][0], 3), slot=events[i][1], cue=events[i][2])
                i += 1
            self.feed.playhead(t, True, RUNNING)
            if t >= tl["duration_s"] and i >= len(events):
                return self._end(COMPLETED, "COMPLETED", "the show ran to its end", t)
            self.sleep(TICK_S)

    def _tick_checks(self, show, t):
        self.feed.heartbeat()
        if self.stop_requested:
            return self._end(ABORTED, "OPERATOR_STOP", "stopped by the operator", t)
        end = monitor(show.requires or {}, self.robot.read())
        if end:
            return self._end(*end, t)
        return None


# ---- the routine palette (workplan 1.13 / 4.4b) ----------------------------------------------------
class Palette:
    """The operator's routines: shows marked `palette:`, each validated, run one at a time on the
    operator's pick. Every palette routine ends in the rejoin state (the validator insists), so they
    can run in any order; the runner's start-pose move covers the gap."""

    def __init__(self, shows_dir, limits_path, robot, feed, log=None, start_lead=START_LEAD_S,
                 clock=time.monotonic, sleep=time.sleep):
        self.shows_dir, self.limits_path, self.robot, self.feed = shows_dir, limits_path, robot, feed
        self.log, self.start_lead, self.clock, self.sleep = log, start_lead, clock, sleep
        self.lock = threading.Lock()
        self.current = None           # (routine id, Runner, thread)
        self.last = None              # {"id", "status", "code", "cause"}

    def routines(self) -> list:
        # No limits, no routines: every one is listed, refused, and says why (decided in 4.4c).
        try:
            limits, why = cue.load_limits(self.limits_path), None
        except Exception as e:  # noqa: BLE001
            limits, why = None, f"the arms' joint limits cannot be read ({self.limits_path}): {e}"
        out = []
        for name in sorted(os.listdir(self.shows_dir)):
            if not name.endswith((".yaml", ".yml")):
                continue
            path = os.path.join(self.shows_dir, name)
            try:
                show = cue.load_show(path)
            except Exception:  # noqa: BLE001  a broken file is not a routine
                continue
            pal = show.doc.get("palette")
            if not isinstance(pal, dict):
                continue
            tl = (show.export(limits, self.limits_path) if limits is not None else
                  {"valid": False, "duration_s": None, "problems": [{"msg": why}], "warnings": []})
            out.append({"id": show.id, "label": pal.get("label", show.id), "order": pal.get("order", 999),
                        "path": path, "valid": tl["valid"], "duration_s": tl["duration_s"],
                        "problems": [q["msg"] for q in tl["problems"][:3]],
                        "warnings": len(tl.get("warnings", []))})
        return sorted(out, key=lambda r: (r["order"], r["label"]))

    def status(self) -> dict:
        with self.lock:
            cur = self.current
            running = bool(cur and cur[2].is_alive())
            return {"running": cur[0] if running else None,
                    "status": cur[1].status if cur else None, "last": self.last}

    def run(self, rid: str) -> dict:
        with self.lock:
            if self.current and self.current[2].is_alive():
                raise RuntimeError(f"{self.current[0]!r} is still running — stop it or let it finish")
            r = next((x for x in self.routines() if x["id"] == rid), None)
            if r is None:
                raise KeyError(f"no palette routine {rid!r}")
            if not r["valid"]:
                raise ValueError(f"{rid!r} does not validate: {'; '.join(r['problems'])}")
            runner = Runner(r["path"], self.limits_path, self.robot, self.feed, self.log,
                            clock=self.clock, sleep=self.sleep, start_lead=self.start_lead)
            t = threading.Thread(target=self._play, args=(rid, runner), daemon=True)
            self.current = (rid, runner, t)
            t.start()
            return {"started": rid}

    def _play(self, rid, runner):
        status, code, cause, _ = runner.run()
        with self.lock:
            self.last = {"id": rid, "status": status, "code": code, "cause": cause}
        # FAKE robot only: a completed routine left the arms at rest, so the next one's start-pose
        # move begins from there. The real provider MEASURES this instead.
        if status == COMPLETED and isinstance(self.robot, FakeRobotState):
            tl = self.feed.timeline()["timeline"] or {}
            poses = {s: (tl["tracks"][s][-1]["pose"] if tl.get("tracks", {}).get(s) else {})
                     for s in cue.ARM_SLOTS}
            self.robot.set(arm_pose=poses)

    def stop(self):
        with self.lock:
            if self.current and self.current[2].is_alive():
                self.current[1].request_stop()
                return True
        return False


# ---- the feed server, and the fake robot's switches ---------------------------------------------
def serve(feed: TwinFeed, robot, runner_ref: dict, host: str, port: int, palette=None):
    fake = isinstance(robot, FakeRobotState)      # only the fake has switches; the real one is read-only

    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def _json(self, obj, code=200):
            b = json.dumps(obj).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(b)))
            self.end_headers()
            self.wfile.write(b)

        def do_GET(self):
            if self.path == "/api/twin":
                return self._json(feed.twin())
            if self.path == "/api/twin/timeline":
                return self._json(feed.timeline())
            if self.path == "/api/sim":
                return self._json(robot.read())
            if palette and self.path == "/api/routines":     # without server paths: the page needs none
                return self._json([{k: v for k, v in r.items() if k != "path"} for r in palette.routines()])
            if palette and self.path == "/api/palette":
                return self._json(palette.status())
            self._json({"error": "not found"}, 404)

        def do_POST(self):
            p = self.path
            sw = {"/api/sim/estop": {"estop": True}, "/api/sim/estop/clear": {"estop": False},
                  "/api/sim/deadman/release": {"deadman": False},
                  "/api/sim/deadman/hold": {"deadman": True}}
            if fake and p in sw:
                robot.set(**sw[p])
                return self._json(robot.read())
            if fake and p.startswith("/api/sim/ch5/"):
                robot.set(ch5=p.rsplit("/", 1)[1].upper())
                return self._json(robot.read())
            if palette and p.startswith("/api/run/"):
                try:
                    return self._json(palette.run(p.rsplit("/", 1)[1]))
                except RuntimeError as e:
                    return self._json({"error": str(e)}, 409)
                except KeyError as e:
                    return self._json({"error": e.args[0] if e.args else "not found"}, 404)
                except ValueError as e:
                    return self._json({"error": str(e)}, 422)
            if p == "/api/stop" and palette:
                return self._json({"stopped": palette.stop()})
            if p == "/api/stop" and runner_ref.get("r"):
                runner_ref["r"].request_stop()
                return self._json({"ok": True})
            self._json({"error": "not found"}, 404)

    srv = ThreadingHTTPServer((host, port), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run", help="run a show against the twins")
    r.add_argument("show")
    # On the robot the service sets OPENDUBS_ARM_LIMITS, and a missing file there refuses every
    # show; the test fixture is the fallback only where neither is set (a laptop).
    r.add_argument("--limits", default=os.environ.get("OPENDUBS_ARM_LIMITS") or (
        cue.DEFAULT_LIMITS if os.path.isfile(cue.DEFAULT_LIMITS)
        else os.path.join(HERE, "tests", "joint_limits_fixture.yaml")))
    r.add_argument("--port", type=int, default=8092)
    r.add_argument("--log", help="append the JSON-lines log here (default: stdout)")
    r.add_argument("--hold", type=float, default=0.0, help="seconds to keep serving after it ends")
    r.add_argument("--audio-output", choices=cue.AUDIO_OUTPUTS,
                   help="play all of the show's audio here (the show is re-checked against it)")
    pp = sub.add_parser("palette", help="serve the routine palette; the operator picks, it runs")
    pp.add_argument("shows", nargs="?", default=os.path.join(HERE, "shows"))
    pp.add_argument("--limits", default=r.get_default("limits"))
    pp.add_argument("--port", type=int, default=8092)
    pp.add_argument("--log", help="append the JSON-lines log here (default: stdout)")
    pp.add_argument("--robot", choices=("fake", "dashboard"), default="fake",
                    help="whose state preflight and aborts answer to (dashboard: read-only, on the robot)")
    pp.add_argument("--dashboard", default="http://127.0.0.1:8080/api/choreo/robot")
    a = ap.parse_args()

    if a.cmd == "palette":
        robot = DashboardRobotState(a.dashboard) if a.robot == "dashboard" else FakeRobotState()
        feed = TwinFeed()
        log = open(a.log, "a", encoding="utf-8") if a.log else sys.stdout
        pal = Palette(a.shows, a.limits, robot, feed, log)
        serve(feed, robot, {}, "127.0.0.1", a.port, pal)
        names = ", ".join(f"{x['label']}{'' if x['valid'] else ' (INVALID)'}" for x in pal.routines())
        print(f"runner: palette on http://127.0.0.1:{a.port} — {names}. GET /api/routines, "
              f"POST /api/run/<id>, POST /api/stop. Robot state: "
              f"{'the dashboard, read-only' if a.robot == 'dashboard' else 'FAKE'}; "
              "nothing commands the robot.",
              file=sys.stderr, flush=True)
        while True:
            time.sleep(3600)

    robot, feed, ref = FakeRobotState(), TwinFeed(), {}
    serve(feed, robot, ref, "127.0.0.1", a.port)
    log = open(a.log, "a", encoding="utf-8") if a.log else sys.stdout
    print(f"runner: twin feed on http://127.0.0.1:{a.port} — the FAKE robot state is in charge; "
          "nothing here can command the robot", file=sys.stderr, flush=True)
    ref["r"] = runner = Runner(a.show, a.limits, robot, feed, log, audio_output=a.audio_output)
    status, code, cause, _ = runner.run()
    print(f"runner: {status} ({code}): {cause}", file=sys.stderr, flush=True)
    time.sleep(a.hold)
    return 0 if status == COMPLETED else 1


if __name__ == "__main__":
    sys.exit(main())
