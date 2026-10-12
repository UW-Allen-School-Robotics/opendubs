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

WHAT THIS CAN COMMAND: by default nothing on the robot — its outputs are the twin feed (the same
/api/twin the composer serves), so the MuJoCo and face twins play exactly what the runner decides.
LIVE OUTPUT (workplan 5.6) is a separately armed binding: only with `--arms ros` on the robot, and
only for a run the operator marks LIVE (the Shows page's switch, off again after every run), the
real ARMS follow the routine (arm_output.py); the grippers, the face and the head's pan/tilt as each is
available; the LIFT (`--lift dashboard`) and the state LIGHT (`--light dashboard`), each armed separately
(show_outputs.py). The base (DANCE) stays twin-only until the floor test. The robot's
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

import arm_output  # noqa: E402
import library  # noqa: E402
import grippers  # noqa: E402
import cue  # noqa: E402
import face_bridge  # noqa: E402
import show_outputs  # noqa: E402

# ---- status, after Spot's ChoreographyStatus; codes after BML's typed warnings (PA proposal 8) --
VALIDATING, PREFLIGHT, PREPPING, WAITING, RUNNING = (
    "VALIDATING", "PREFLIGHT", "PREPPING", "WAITING_FOR_START", "RUNNING")
COMPLETED, ABORTED, ESTOPPED, REFUSED = "COMPLETED", "ABORTED", "ESTOPPED", "REFUSED"
# An arm show runs only with a current self-collision stamp (sweep.py; cue.py _check_sweep). Tests
# of other things turn it off for their throwaway shows; nothing on the robot does.
REQUIRE_SWEEP = True
FINAL = (COMPLETED, ABORTED, ESTOPPED, REFUSED)

TICK_S = 0.02             # 50 Hz
LEASE_S = 0.25            # like every remote command on this robot: no heartbeat for 250 ms, no lease
START_LEAD_S = 2.0        # PLACEHOLDER: time for a start to reach the tablet and the arm goals to land
AUDIO_FADE_S = 0.25       # DEC-6: an abort fades the music; an e-stop cuts it
AUDIO_READY_WAIT_S = 15.0 # PLACEHOLDER: how long a start waits for the audio device to finish loading

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
                  "tab5": True, "fault": None, "arm_pose": {"ARM_L": {}, "ARM_R": {}},
                  "mast_operator_seq": 0}
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


# ---- show audio (plan 4.2) -----------------------------------------------------------------------
class NullAudio:
    """No audio device: records what it was told, for tests and for a laptop with no dashboard."""

    def __init__(self, holder=None, ready=True):
        self.calls, self.holder, self.is_ready = [], holder, ready

    def send(self, msg):
        self.calls.append(msg)

    def state(self):
        return {"holder": self.holder, "ready": bool(self.holder) and self.is_ready}


class DashboardAudio:
    """The show-audio device, through the dashboard (POST/GET /api/choreo/audio). The dashboard
    relays each command to the ONE device holding the role. Never blocks a show for long: a call that
    fails is logged by the runner and the show goes on (DEC-7: audio trouble never stops motion)."""

    def __init__(self, url="http://127.0.0.1:8080/api/choreo/audio"):
        self.url = url

    def send(self, msg):
        req = urllib.request.Request(self.url, data=json.dumps(msg).encode(), method="POST",
                                     headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=0.5) as r:
            return json.loads(r.read())

    def state(self):
        with urllib.request.urlopen(self.url, timeout=0.3) as r:
            return json.loads(r.read())


def audio_items(show, tl) -> tuple:
    """What the show-audio device must play: (items, skipped). Each item is {url, at, kind}, `at`
    in show seconds; show time 0 is audio position 0 (format §3). Only audio routed to the tablet
    (DEC-5) — earcons go to the head, and nothing plays on the Thor yet. Paths are relative to the
    shows directory, which the dashboard serves under /api/choreo/media/."""
    base = os.path.dirname(os.path.abspath(show.path)) if show.path else ""
    items, skipped = [], []
    m = tl.get("music") or {}
    to_tablet = (show.audio_override or m.get("output") or cue.DEFAULT_AUDIO_OUTPUT) == "tablet"
    if m.get("file") and to_tablet:
        if base and os.path.isfile(os.path.join(base, m["file"])):
            items.append({"url": "/api/choreo/media/" + m["file"], "at": 0.0, "kind": "music"})
        else:
            skipped.append(f"music {m['file']} is not on this machine")
    elif m.get("song") and to_tablet:
        # A library song (plan 4.6): the dashboard serves the library's songs/ under media/library/.
        try:
            idx, unreadable = library.load_index() or {}, None
        except Exception as err:  # noqa: BLE001  audio trouble never stops a show (DEC-7)
            idx, unreadable = {}, f"{type(err).__name__}: {err}"
        e = idx.get(m["song"])
        f = e and os.path.join(library.root(), e.get("file", ""))
        if unreadable:
            skipped.append(f"the show library cannot be read ({unreadable}) — running without music")
        elif e and e.get("kind") == "song" and os.path.isfile(f):
            items.append({"url": "/api/choreo/media/library/" + e["file"], "at": 0.0, "kind": "music"})
        else:
            skipped.append(f"song {m['song']} is not in this robot's library ({library.root()})")
    for e in tl.get("tracks", {}).get("SOUND", []):
        if e.get("output") != "tablet":
            continue
        f = show._sound_file(e["cue"].get("sound"))
        if f:
            items.append({"url": "/api/choreo/media/" + os.path.relpath(f, base).replace(os.sep, "/"),
                          "at": e["t"], "kind": "sound"})
        else:
            skipped.append(f"sound {e['cue'].get('sound')} is not on this machine")
    return items, skipped


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


def signed_pose(p) -> list:
    """An arm pose as 7 signed degrees (the controller's sense): from a list, a {joint: deg} dict, or
    nothing (zero)."""
    if isinstance(p, (list, tuple)) and len(p) == 7:
        return [float(v) for v in p]
    p = p or {}
    return [float(p.get(str(j), p.get(j, 0.0))) for j in range(1, 8)]


def prep_moves(tl: dict, measured: dict) -> tuple:
    """Moves that bring EVERY arm to where the show starts it — zero, the pose the validator's speed
    check and the twin both start every arm from (cue.py, sim_preview.arm_pose) — timed by the
    distance at the speed caps (x PEAK_FACTOR, the warning-free time), so the move needs no warning.
    Every arm, not only the ones the show moves: an arm left out stays wherever an aborted run left
    it, outside what was validated (found on the robot 2026-10-08, a one-arm run after a two-arm
    abort). `measured` is {slot: pose} in signed degrees. Returns (seconds, {slot: [7 signed deg]})."""
    secs, targets = 0.0, {}
    for slot in cue.ARM_SLOTS:
        now = signed_pose(measured.get(slot))
        need = max(abs(now[j - 1]) / cue.SPEED_CAP_DEG_S[j] * cue.PEAK_FACTOR for j in range(1, 8))
        secs, targets[slot] = max(secs, need), [0.0] * 7
    return round(secs, 2), targets


# What each slot is, on the Shows page's routine buttons (an icon each), in this order.
USE_PARTS = (("ARM_L", "arms"), ("ARM_R", "arms"), ("GRIP_L", "grippers"), ("GRIP_R", "grippers"),
             ("HEAD", "head"), ("FACE", "face"), ("SOUND", "sound"), ("LIGHT", "light"),
             ("LIFT", "lift"), ("DANCE", "base"), ("DRIVE", "base"))


def _grips_calibrated() -> bool:
    """Whether this robot has a gripper calibration the runner can use (grippers.py)."""
    try:
        return bool(grippers.load())
    except grippers.CalibrationError:
        return False


def _grip_target(c: dict, current=None):
    """A GRIP cue's target as 0..1 (0 closed, 1 open): `do: open` 1, `do: close` 0, or `open:`."""
    if c.get("do") == "open":
        return 1.0
    if c.get("do") == "close":
        return 0.0
    if c.get("open") is not None:
        return min(1.0, max(0.0, float(c["open"])))
    return current


def routine_uses(show, tl, arms_live: bool, face_live: bool = False, gaze_live: bool = False,
                 earcons: bool = True, grips_live: bool = False, lift_live: bool = False,
                 light_live: bool = False) -> list:
    """What a routine will do, for the operator before they tap it: [{"part", "live"}], one per part
    it moves or plays. `live` says whether THIS runner does it on the robot; the rest plays in the
    twin only. Today that is the arms (plan 5.6, when the runner has them), audio routed to the
    tablet (4.2), the face and the head's earcons (when the runner has the face), and the head's
    pan/tilt (when the runner has the gaze AND the head answers GAZE), the grippers (when this robot
    has a gripper calibration, grippers.py — and with it, so does the runner), and the lift and the
    light when the runner is armed for them (--lift / --light dashboard). The base has no robot output yet. Read from the export when the show validates, else from its `slots:`."""
    tracks = tl.get("tracks") or {}
    used = ({k for k, v in tracks.items() if v} if tracks else set(show.doc.get("slots") or []))
    out, seen = [], set()
    m = tl.get("music") or (show.doc.get("music") if isinstance(show.doc.get("music"), dict) else None)
    if m and (m.get("file") or m.get("song")):
        out.append({"part": "music", "live": (m.get("output") or cue.DEFAULT_AUDIO_OUTPUT) == "tablet"})
        seen.add("music")
    for slot, part in USE_PARTS:
        if slot not in used or part in seen:
            continue
        seen.add(part)
        if part == "arms":
            live = arms_live
        elif part == "sound":
            live = any(e.get("output") == "tablet" or (face_live and earcons and e.get("output") == "tab5")
                       for e in tracks.get("SOUND", []))
        elif part == "face":
            live = face_live
        elif part == "head":
            live = gaze_live
        elif part == "grippers":
            live = grips_live
        elif part == "lift":
            live = lift_live
        elif part == "light":
            live = light_live
        else:
            live = False
        out.append({"part": part, "live": live})
    return out


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
                 sleep=time.sleep, start_lead=START_LEAD_S, audio_output=None, audio=None,
                 arms=None, live=False, grips=None, lift=None, light=None):
        self.path, self.limits_path, self.robot = path, limits_path, robot
        # LIVE: the real arms follow this run. Only with an arm output AND the operator's LIVE mark.
        self.arms = arms
        self.live = bool(live and arms is not None)
        self.audio = audio or NullAudio()
        self.audio_live = False                   # a load was sent and the device took it
        self.audio_note = None                    # why the show is running silently, if it is
        self.audio_output = audio_output          # the operator's override (DEC-5), re-validated
        self.feed = feed or TwinFeed()
        self.log_fh, self.clock, self.sleep, self.start_lead = log, clock, sleep, start_lead
        self.status, self.ended = None, None      # ended: (status, code, cause, show time)
        self._arms_moving = False
        # The grippers (plan item 3): this robot's calibration (grippers.py), read at the run's start;
        # None sends no gripper cues. A close that STALLED (its goal aborted: something is in there)
        # is never re-sent on that side for the rest of the run.
        self.grips = grips if self.live else None
        # The lift and the state light (show_outputs.py): each armed separately, a live run's only.
        self.lift = lift if self.live else None
        self.light = light if self.live else None
        self._state = {}                          # the robot's state as last read, each tick
        self._grip_last = {}                      # slot -> 0..1 last sent
        self._grip_stalled = set()
        self._grip_closes = {}                    # slot -> ids of the close goals sent this run
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

    def _audio(self, msg):
        try:
            self.audio.send(msg)
            return True
        except Exception as e:  # noqa: BLE001  audio trouble never stops a show (DEC-7)
            self._log("audio_error", op=msg.get("op"), error=f"{type(e).__name__}: {e}")
            return False

    def _audio_silent(self, why):
        self.audio_note = why
        self.audio_live = False
        self._log("audio_warning", note=why)

    def _grip(self, slot, c, t):
        """Send one gripper cue: the show's 0..1 through this robot's calibration. `close` is exactly
        the calibrated closed position, never past it (grippers.py). A side whose last close STALLED
        is not closed again this run — the stall is the "something is in there" signal."""
        side = arm_output.GRIP_SIDES[slot]
        if any(self.arms.goal_status.get(g) == 6 for g in self._grip_closes.get(slot, ())):
            # Judged on each CLOSE goal's own result, so a late stall still counts after an open.
            if slot not in self._grip_stalled:
                self._log("live", note=f"the {side} gripper's close STALLED — something is in it; "
                                       "it will not be closed again this run", slot=slot)
            self._grip_stalled.add(slot)
        frac = _grip_target(c, self._grip_last.get(slot))
        if frac is None:
            return
        if frac == 0.0 and slot in self._grip_stalled:
            self._log("live", note=f"{side} gripper close NOT sent: it stalled earlier this run", slot=slot,
                      show_t=round(t, 3))
            return
        metres = grippers.to_controller(self.grips, side, frac)
        try:
            gid = self.arms.send_grip(slot, metres)
            self._grip_last[slot] = frac
            if frac == 0.0:
                self._grip_closes.setdefault(slot, []).append(gid)
            self._arms_moving = True
            self._log("grip", slot=slot, open=round(frac, 3), metres=round(metres, 5), show_t=round(t, 3))
        except Exception as e:  # noqa: BLE001  a gripper is never a reason to stop the show
            # Once, then no more grips this run: a retry per cue would only repeat the failure.
            self.grips = None
            self._log("live_error", error=f"grip {side}: {type(e).__name__}: {e} — no more gripper cues "
                                          "are sent this run")

    def _end(self, status, code, cause, t=None):
        self.ended = (status, code, cause, t)
        for out in (self.lift, self.light):       # every end: the lift stops now, the light is released
            if out is not None:
                out.end()
        if self.live and status != COMPLETED and self._arms_moving:
            # Anything but running to the end: the arms hold where they are (ABORT_STATE). An e-stop
            # has already cut their power; cancelling costs nothing.
            try:
                self.arms.cancel_all()
                self._log("live", note="arm and gripper goals cancelled — they hold where they are")
            except Exception as e:  # noqa: BLE001
                self._log("live_error", error=f"cancel: {type(e).__name__}: {e}")
        if self.audio_live:
            # DEC-6: an e-stop cuts the music, everything else fades it — including a show that
            # ends before its song does.
            self._audio({"op": "cut"} if status == ESTOPPED else {"op": "stop", "fade_s": AUDIO_FADE_S})
            self.audio_live = False
        self._set(status, code=code, cause=cause, show_t=t)
        if status == ABORTED:
            self._log("abort_state", slots=ABORT_STATE)
        elif status == ESTOPPED:
            self._log("estop", note="everything stopped at once; audio cut, not faded")
        self.feed.playhead(t or 0.0, False, status)
        return self.ended

    def _audio_ready(self, load_id):
        try:
            st = self.audio.state()
        except Exception:  # noqa: BLE001
            return False
        return bool(st.get("ready")) and st.get("load") in (None, load_id)

    def request_stop(self):
        """An operator stop (the player's STOP button): an abort, not a pause."""
        self.stop_requested = True

    def run(self):
        # VALIDATING: the whole show, before any of it is sent (standing rule 3)
        self._set(VALIDATING, show=self.path)
        try:
            show = cue.load_show(self.path)
            show.audio_override = self.audio_output
            show.require_sweep = REQUIRE_SWEEP   # an arm show needs a current self-collision stamp (sweep.py)
            tl = show.export(cue.load_limits(self.limits_path), self.limits_path)
        except Exception as e:  # noqa: BLE001
            return self._end(REFUSED, "PARSING_FAILURE", f"{type(e).__name__}: {e}")
        if not tl["valid"]:
            return self._end(REFUSED, "INVALID_SHOW",
                             "; ".join(p["msg"] for p in tl["problems"][:3]))
        if (tl.get("tracks") or {}).get("DRIVE"):
            # DRIVE is twin-only (cue.py SLOTS): the robot cannot yet say where it is, and every cue
            # after a drive assumes the base moved — so the whole show is refused, not the drives skipped
            return self._end(REFUSED, "SHOW_DRIVE_TWIN_ONLY",
                             "this show uses DRIVE, which runs only in the twin until odometry is trusted")
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

        # LIVE: the arms' real position, which the start-pose move must begin from. Unknown refuses.
        measured = st.get("arm_pose") or {}
        if self.live:
            measured = self.arms.measured()
            if measured is None:
                return self._end(REFUSED, "UNKNOWN_STATE",
                                 "the arms' measured position is unknown (/joint_states) — a live run needs it")
            self._log("live", note="LIVE: the real arms will follow this routine",
                      measured={k: [round(v, 1) for v in val] for k, val in measured.items()})

        # PREPPING: travel to the start pose, slowly enough to need no warning — and, meanwhile,
        # the show-audio device downloads the show's files (4.2: whole files, started on cue)
        secs, targets = prep_moves(tl, measured)
        if self.live and secs > 0:
            for slot in targets:
                pts = arm_output.track_points([{"start": 0.0, "t": secs, "pose_signed":
                                                {str(j): 0.0 for j in range(1, 8)}}],
                                               start=signed_pose(measured.get(slot)))
                self.arms.send(slot, pts)
            self._arms_moving = True
        items, skipped = audio_items(show, tl)
        load_id = f"{show.id}@{round(time.time(), 3)}"
        for why in skipped:
            self._log("audio_warning", note=why)
        if items:
            try:
                holder = self.audio.state().get("holder")
            except Exception:  # noqa: BLE001
                holder = None
            if not holder:
                self._audio_silent("no device is playing show audio — running silently")
            elif self._audio({"op": "load", "id": load_id, "items": items}):
                self.audio_live = True
        self._set(PREPPING, prep_s=secs, targets=targets, audio_items=len(items))
        t_end, wait_until = self.clock() + secs, self.clock() + AUDIO_READY_WAIT_S
        while self.clock() < t_end or (self.audio_live and not self._audio_ready(load_id)):
            if self.audio_live and self.clock() >= max(t_end, wait_until):
                self._audio({"op": "cut"})
                self._audio_silent(f"the show-audio device was not ready in {AUDIO_READY_WAIT_S:g} s — "
                                   "running silently")
                break
            if (stop := self._tick_checks(show, None)):
                return stop
            self.sleep(TICK_S if self.clock() < t_end else 0.1)

        # WAITING_FOR_START: a start in the future, so the music and the arms can begin together
        self._set(WAITING, lead_s=self.start_lead)
        t0 = self.clock() + self.start_lead
        if self.audio_live:
            # T0 on the Thor's monotonic clock; the device converts it to its own audio clock
            self._audio({"op": "start", "id": load_id, "t0_mono": round(t0, 4)})
        while self.clock() < t0:
            if (stop := self._tick_checks(show, None)):
                return stop
            self.sleep(TICK_S)

        # RUNNING
        self._set(RUNNING, duration_s=tl["duration_s"])
        if self.live:
            # Each arm's whole routine as one goal, from show time 0 — sent as the show starts.
            for slot in cue.ARM_SLOTS:
                kfs = tl.get("tracks", {}).get(slot) or []
                if kfs:
                    self.arms.send(slot, arm_output.track_points(kfs))
                    self._arms_moving = True
            if any(tl.get("tracks", {}).get(g) for g in ("GRIP_L", "GRIP_R")):
                if self.grips is None:
                    self._log("live", note="gripper cues are NOT sent: this robot has no gripper calibration "
                                           f"({grippers.default_path()}; plan 6.2)")
                elif not hasattr(self.arms, "send_grip"):
                    self._log("live", note="gripper cues are NOT sent: this runner's arm output has no grippers")
                    self.grips = None
                elif (missing := self.arms.grips_ready()):
                    self._log("live", note="gripper cues are NOT sent: no gripper controller for "
                                           f"{' and '.join(missing)} (is that arm active?)")
                    self.grips = None
                else:
                    self._log("live", note="LIVE: the grippers follow this routine (calibrated per side)")
        if self.lift is not None:
            self.lift.start((self._state or self.robot.read()).get("mast_operator_seq"))
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
                if self.grips is not None and events[i][1] in arm_output.GRIP_SIDES:
                    self._grip(events[i][1], events[i][2], events[i][0])
                elif self.lift is not None and events[i][1] == "LIFT":
                    self.lift.cue(events[i][2], self.clock(), events[i][0])
                elif self.light is not None and events[i][1] == "LIGHT":
                    self.light.cue(events[i][2], self.clock(), events[i][0])
                i += 1
            for out in (self.lift, self.light):
                if out is not None:
                    out.tick(self.clock(), self._state.get("mast_operator_seq"))
            self.feed.playhead(t, True, RUNNING)
            if t >= tl["duration_s"] and i >= len(events):
                return self._end(COMPLETED, "COMPLETED", "the show ran to its end", t)
            self.sleep(TICK_S)

    def _tick_checks(self, show, t):
        self.feed.heartbeat()
        if self.stop_requested:
            return self._end(ABORTED, "OPERATOR_STOP", "stopped by the operator", t)
        self._state = self.robot.read()
        end = monitor(show.requires or {}, self._state)
        if end:
            return self._end(*end, t)
        return None


# ---- the routine palette (workplan 1.13 / 4.4b) ----------------------------------------------------
# The robot's OWN shows: a folder outside the deploy (which rsyncs /opt/opendubs/choreo/shows from the
# repository with --delete), filled by show_sync.py. For shows that are not, or may not be, in the
# repository — a show built from someone's photo, say. The palette lists both; an id in both is refused.
LOCAL_SHOWS = os.environ.get("OPENDUBS_LOCAL_SHOWS") or "/var/lib/opendubs/shows"


class Palette:
    """The operator's routines: shows marked `palette:`, each validated, run one at a time on the
    operator's pick. Every palette routine ends in the rejoin state (the validator insists), so they
    can run in any order; the runner's start-pose move covers the gap. Shows come from the
    repository's folder and, when it exists, the robot's own (LOCAL_SHOWS)."""

    def __init__(self, shows_dir, limits_path, robot, feed, log=None, start_lead=START_LEAD_S,
                 clock=time.monotonic, sleep=time.sleep, audio=None, arms=None, face=None, gaze=None,
                 local_shows=None, dashboard=None, lift_live=False, light_live=False):
        self.shows_dir, self.limits_path, self.robot, self.feed = shows_dir, limits_path, robot, feed
        self.local_shows = local_shows
        # The lift and the light (show_outputs.py), armed separately; both need a dashboard to speak to
        self.dashboard = dashboard
        self.lift_live = bool(lift_live and dashboard is not None)
        self.light_live = bool(light_live and dashboard is not None)
        self.audio = audio or NullAudio()
        self.arms = arms                     # None: live output is not available on this runner
        self.face = face                     # face_bridge.LiveFace: live runs drive the real face
        self.gaze = gaze                     # face_bridge.LiveGaze: ... and the head's pan/tilt
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
        out, seen = [], {}
        folders = [(self.shows_dir, "repository")]
        if self.local_shows and os.path.isdir(self.local_shows):
            folders.append((self.local_shows, "robot"))
        files = [(os.path.join(d, n), where) for d, where in folders for n in sorted(os.listdir(d))
                 if n.endswith((".yaml", ".yml"))]
        for path, where in files:
            try:
                show = cue.load_show(path)
                show.require_sweep = REQUIRE_SWEEP
            except Exception:  # noqa: BLE001  a broken file is not a routine
                continue
            pal = show.doc.get("palette")
            if not isinstance(pal, dict):
                continue
            tl = (show.export(limits, self.limits_path) if limits is not None else
                  {"valid": False, "duration_s": None, "problems": [{"msg": why}], "warnings": []})
            if (tl.get("tracks") or {}).get("DRIVE"):
                # listed, never runnable here: DRIVE is twin-only (the run would be refused anyway)
                tl = dict(tl, valid=False, problems=[{"msg": "uses DRIVE — runs only in the twin until odometry "
                                                              "is trusted"}] + list(tl["problems"]))
            if show.id in seen:
                # two shows, one id: the operator's pick would be ambiguous, so this one is refused
                tl = dict(tl, valid=False, problems=[{"msg": f"its id {show.id!r} is also the {seen[show.id]} "
                                                              "show's — rename one"}] + list(tl["problems"]))
            seen.setdefault(show.id, f"{where} folder's")
            out.append({"id": show.id, "label": pal.get("label", show.id), "order": pal.get("order", 999),
                        "path": path, "from": where, "valid": tl["valid"], "duration_s": tl["duration_s"],
                        "problems": [q["msg"] for q in tl["problems"][:3]],
                        "warnings": len(tl.get("warnings", [])),
                        "uses": routine_uses(show, tl, arms_live=self.arms is not None,
                                             face_live=self.face is not None and self.arms is not None,
                                             gaze_live=(self.gaze is not None and self.arms is not None
                                                        and self.gaze.supported is True),
                                             earcons=self.face is None or self.face.earcons,
                                             grips_live=self.arms is not None and _grips_calibrated(),
                                             lift_live=self.arms is not None and self.lift_live,
                                             light_live=self.arms is not None and self.light_live)})
        return sorted(out, key=lambda r: (r["order"], r["label"]))

    def status(self) -> dict:
        with self.lock:
            cur = self.current
            running = bool(cur and cur[2].is_alive())
            return {"running": cur[0] if running else None,
                    "status": cur[1].status if cur else None, "last": self.last,
                    "audio_note": cur[1].audio_note if cur else None,
                    "live": bool(running and cur[1].live), "live_available": self.arms is not None}

    def run(self, rid: str) -> dict:
        # A runner with arms always drives them. On the robot, CH5 AUTO and the held deadman are the
        # operator's go; a separate "live" switch only let a run skip the arms unnoticed (decided
        # 2026-10-08). A runner without arms (a laptop, with the composer) drives the twins only.
        with self.lock:
            if self.current and self.current[2].is_alive():
                raise RuntimeError(f"{self.current[0]!r} is still running — stop it or let it finish")
            r = next((x for x in self.routines() if x["id"] == rid and x["valid"]), None) or \
                next((x for x in self.routines() if x["id"] == rid), None)
            if r is None:
                raise KeyError(f"no palette routine {rid!r}")
            if not r["valid"]:
                raise ValueError(f"{rid!r} does not validate: {'; '.join(r['problems'])}")
            live = self.arms is not None
            # The head follows a LIVE run only — the robot's runs; a laptop's stay in the twins.
            sinks = [x for x in (self.face, self.gaze) if x is not None] if live else []
            feed = face_bridge.HeadTee(self.feed, *sinks) if sinks else self.feed
            grips = None
            if live:
                try:
                    grips = grippers.load()       # read each run, so a new calibration needs no restart
                except grippers.CalibrationError as e:
                    if os.path.isfile(grippers.default_path()) and self.log:   # there, but malformed: say so
                        self.log.write(json.dumps({"wall": round(time.time(), 3), "kind": "live_error",
                                                   "error": f"gripper calibration: {e}"}) + "\n")
            lift = light = None
            if live:
                note = lambda kind, **kw: runner._log(kind, **kw)   # noqa: E731  bound once runner exists
                if self.lift_live:
                    lift = show_outputs.LiftOutput(self.dashboard, lambda *a, **kw: note(*a, **kw))
                if self.light_live:
                    light = show_outputs.LightOutput(self.dashboard, lambda *a, **kw: note(*a, **kw))
            runner = Runner(r["path"], self.limits_path, self.robot, feed, self.log,
                            clock=self.clock, sleep=self.sleep, start_lead=self.start_lead,
                            audio=self.audio, arms=self.arms, live=live, grips=grips, lift=lift, light=light)
            for x in sinks:
                x.note = lambda kind, **kw: runner._log(kind, **kw)
            t = threading.Thread(target=self._play, args=(rid, runner), daemon=True)
            self.current = (rid, runner, t)
            t.start()
            return {"started": rid, "live": runner.live}

    def _play(self, rid, runner):
        try:
            status, code, cause, _ = runner.run()
        except Exception as e:  # noqa: BLE001  a crash is an end like any other: said, and everything stopped
            import traceback
            cause = f"the runner failed: {type(e).__name__}: {e}"
            runner._log("runner_error", error=cause, trace=traceback.format_exc()[-2000:])
            try:
                status, code, cause, _ = runner._end(ABORTED, "RUNNER_ERROR", cause)
            except Exception:  # noqa: BLE001
                status, code = ABORTED, "RUNNER_ERROR"
        with self.lock:
            self.last = {"id": rid, "status": status, "code": code, "cause": cause}
        # FAKE robot only: a completed routine left the arms at rest, so the next one's start-pose
        # move begins from there. The real provider MEASURES this instead.
        if status == COMPLETED and isinstance(self.robot, FakeRobotState):
            tl = self.feed.timeline()["timeline"] or {}
            poses = {s: (signed_pose(tl["tracks"][s][-1]["pose_signed"]) if tl.get("tracks", {}).get(s) else [0.0] * 7)
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
                    return self._json(palette.run(p.partition("?")[0].rsplit("/", 1)[1]))
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
    pp.add_argument("--lift", choices=("none", "dashboard"), default="none",
                    help="dashboard: a live run moves the real lift through /api/mast (show_outputs.py)")
    pp.add_argument("--light", choices=("none", "dashboard"), default="none",
                    help="dashboard: a live run lays its colour over the state light (show_light.py)")
    pp.add_argument("--local-shows", default=LOCAL_SHOWS,
                    help="the robot's own shows, outside the deploy (show_sync.py fills it; default "
                         "$OPENDUBS_LOCAL_SHOWS, else /var/lib/opendubs/shows; listed when it exists)")
    pp.add_argument("--limits", default=r.get_default("limits"))
    pp.add_argument("--port", type=int, default=8092)
    pp.add_argument("--log", help="append the JSON-lines log here (default: stdout)")
    pp.add_argument("--robot", choices=("fake", "dashboard"), default="fake",
                    help="whose state preflight and aborts answer to (dashboard: read-only, on the robot)")
    pp.add_argument("--dashboard", default="http://127.0.0.1:8080/api/choreo/robot")
    pp.add_argument("--arms", choices=("none", "ros"), default="none",
                    help="ros: the real arms can follow a run marked LIVE (plan 5.6); needs ROS sourced")
    pp.add_argument("--gaze", default="none",
                    help="auto (OPENDUBS_HEAD_NECK_HOST / OPENDUBS_TAB5_HOST / opendubs-tab5.local) or a "
                         "host: live runs turn the real head (its GAZE command, Tab5 PR #56)")
    pp.add_argument("--earcons", choices=("on", "off"), default="on",
                    help="off: live runs do not send the head its earcons (CUE) — held back while the "
                         "head's audio task damps its face tracking (2026-10-08)")
    pp.add_argument("--face", default="none",
                    help="auto (the head's address from OPENDUBS_HEAD_FACE_HOST / OPENDUBS_TAB5_HOST / "
                         "opendubs-tab5.local) or a host: live runs drive the real face and earcons")
    for x in (r, pp):
        x.add_argument("--audio", choices=("none", "dashboard"), default="none",
                       help="play show audio on the dashboard's show-audio device (plan 4.2)")
        x.add_argument("--audio-url", default="http://127.0.0.1:8080/api/choreo/audio")
    a = ap.parse_args()
    audio = DashboardAudio(a.audio_url) if a.audio == "dashboard" else NullAudio()

    if a.cmd == "palette":
        robot = DashboardRobotState(a.dashboard) if a.robot == "dashboard" else FakeRobotState()
        feed = TwinFeed()
        log = open(a.log, "a", encoding="utf-8") if a.log else sys.stdout
        arms = None
        if a.arms == "ros":
            try:
                arms = arm_output.RosArms()
            except Exception as e:  # noqa: BLE001  no ROS: the runner still serves, twin-only
                print(f"runner: live arm output NOT available ({type(e).__name__}: {e})", file=sys.stderr, flush=True)
        face = None
        if a.face != "none":
            face = face_bridge.LiveFace(face_bridge.head_face_host() if a.face == "auto" else a.face,
                                        earcons=a.earcons == "on")
            face.release()                   # a run that crashed may have left the face pinned
        gaze = None
        if a.gaze != "none":
            gaze = face_bridge.LiveGaze(face_bridge.head_neck_host() if a.gaze == "auto" else a.gaze)
            gaze.probe()                     # release a crashed run's lease; learn whether GAZE exists
        dash = (show_outputs.Dashboard(show_outputs.base_of(a.dashboard))
                if "dashboard" in (a.lift, a.light) else None)
        pal = Palette(a.shows, a.limits, robot, feed, log, audio=audio, arms=arms, face=face, gaze=gaze,
                      local_shows=a.local_shows, dashboard=dash, lift_live=a.lift == "dashboard",
                      light_live=a.light == "dashboard")
        serve(feed, robot, {}, "127.0.0.1", a.port, pal)
        names = ", ".join(f"{x['label']}{'' if x['valid'] else ' (INVALID)'}" for x in pal.routines())
        print(f"runner: palette on http://127.0.0.1:{a.port} — {names}. GET /api/routines, "
              f"POST /api/run/<id>, POST /api/stop. Robot state: "
              f"{'the dashboard, read-only' if a.robot == 'dashboard' else 'FAKE'}; "
              f"{'every run drives the real arms' if arms else 'nothing commands the robot'}"
              f"{f' and the face at {face.host}' if face and arms else ''}"
              f"{'' if not (gaze and arms) else f'; the head turns ({gaze.host})' if gaze.supported else f'; the head at {gaze.host} has no GAZE yet: pan/tilt in the twin'}.",
              file=sys.stderr, flush=True)
        while True:
            time.sleep(3600)

    robot, feed, ref = FakeRobotState(), TwinFeed(), {}
    serve(feed, robot, ref, "127.0.0.1", a.port)
    log = open(a.log, "a", encoding="utf-8") if a.log else sys.stdout
    print(f"runner: twin feed on http://127.0.0.1:{a.port} — the FAKE robot state is in charge; "
          "nothing here can command the robot", file=sys.stderr, flush=True)
    ref["r"] = runner = Runner(a.show, a.limits, robot, feed, log, audio_output=a.audio_output, audio=audio)
    status, code, cause, _ = runner.run()
    print(f"runner: {status} ({code}): {cause}", file=sys.stderr, flush=True)
    time.sleep(a.hold)
    return 0 if status == COMPLETED else 1


if __name__ == "__main__":
    sys.exit(main())
