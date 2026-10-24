#!/usr/bin/env python3
"""The show runner — its core, driving only the twins (workplan Phase 5).

    python3 runner.py run shows/hello.yaml               # serves the twin feed on :8092
    ./twin.sh --composer http://127.0.0.1:8092           # the MuJoCo twin follows the runner
    python3 face_bridge.py --composer http://127.0.0.1:8092
    curl -X POST localhost:8092/api/sim/estop            # the fake robot: estop, deadman, ch5…

A show runs through VALIDATING -> PREFLIGHT -> [ARMED_WAITING] -> [COUNTDOWN] -> PREPPING ->
WAITING_FOR_START -> RUNNING and ends COMPLETED, ABORTED, ESTOPPED or REFUSED. How it starts is the
run's START MODE: `deadman` (the robot's default) arms the run and waits for the operator to press
the deadman — a fresh press, after arming — then counts down; `countdown` (a twin's default) needs
the deadman held at the start and counts down at once; `now` starts as soon as preflight passes. The runner is a lease-holding motion source like any other
(standing rule 1): it may only run in AUTO with the deadman held, and the moment that stops being
true — or stops being KNOWN — the show ends. Preemption aborts; it never pauses.

WHAT THIS CAN COMMAND: by default nothing on the robot — its outputs are the twin feed (the same
/api/twin the composer serves), so the MuJoCo and face twins play exactly what the runner decides.
LIVE OUTPUT (workplan 5.6) is a separately armed binding: only with `--arms ros` on the robot, and
only for a run the operator marks LIVE (the Shows page's switch, off again after every run), the
real ARMS follow the routine (arm_output.py); the grippers, the face and the head's pan/tilt as each is
available; the LIFT (`--lift dashboard`) and the state LIGHT (`--light dashboard`), each armed separately
(show_outputs.py); the BASE's DANCE moves (`--base ros`), through the Thor's base tracker
(base_output.py). The robot's
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
import base_output  # noqa: E402
import library  # noqa: E402
import grippers  # noqa: E402
import hand_output  # noqa: E402
import cue  # noqa: E402
import face_bridge  # noqa: E402
import show_outputs  # noqa: E402
import video_out  # noqa: E402

# ---- status, after Spot's ChoreographyStatus; codes after BML's typed warnings (PA proposal 8) --
VALIDATING, PREFLIGHT, PREPPING, WAITING, RUNNING = (
    "VALIDATING", "PREFLIGHT", "PREPPING", "WAITING_FOR_START", "RUNNING")
ARMED, COUNTDOWN = "ARMED_WAITING", "COUNTDOWN"
COMPLETED, ABORTED, ESTOPPED, REFUSED = "COMPLETED", "ABORTED", "ESTOPPED", "REFUSED"
# An arm show runs only with a current self-collision stamp (sweep.py; cue.py _check_sweep). Tests
# of other things turn it off for their throwaway shows; nothing on the robot does.
REQUIRE_SWEEP = True
FINAL = (COMPLETED, ABORTED, ESTOPPED, REFUSED)

TICK_S = 0.02             # 50 Hz
LEASE_S = 0.25            # like every remote command on this robot: no heartbeat for 250 ms, no lease
START_LEAD_S = 2.0        # PLACEHOLDER: time for a start to reach the tablet and the arm goals to land
AUDIO_FADE_S = 0.25       # DEC-6: an abort fades the music; an e-stop cuts it
# An arm goal the controller has not ACCEPTED within this is a show the arms are not following: it ends,
# saying so (found at the robot 2026-10-12: rejected goals left a "live" show running with still arms)
ARM_ACK_S = 2.0
# A waypoint up to this far outside a joint's CONTROLLER range is clamped into it, and said: an arm
# that has hung limp rests a hair past its limit (J4 settles to about -0.4 deg), and the controller
# refuses a goal whose first waypoint starts there. Further outside is refused, never moved.
CLAMP_DEG = 1.0
# A gripper that closed on something: its goal reports SUCCEEDED anyway (the controllers' goal_tolerance
# is 0.01 m), so it is judged by where the fingers REALLY stopped — read GRIP_SETTLE_S after the close.
# The fingers settle 1-3 mm short of any target (measured on opendubs2, 2026-10-12); further short than
# GRIP_CONTACT_M is contact: something is in the gripper, and that side is not closed again this run.
GRIP_SETTLE_S = 1.5
GRIP_CONTACT_M = 0.005
AUDIO_READY_WAIT_S = 15.0 # PLACEHOLDER: how long a start waits for the audio device to finish loading
# The show-audio role can change hands as a run starts: the tablet that pressed start switches to its
# face page, which drops the role for ~0.3 s and claims it again (found at the robot 2026-10-14: a
# check at that instant ran every show silently). So the load is ALWAYS sent — the dashboard keeps it
# for the next device to claim — and only a device still absent this long into PREPPING means silence.
AUDIO_HOLDER_GRACE_S = 3.0
# An ARMED run waits here for the show audio — the tablet's, and the projector's music and video (a
# cast load, the stage pages told the show), which load while it is armed — BEFORE it takes the GO (found at the robot 2026-10-16:
# starting from the tablet swaps it to the face page, so the device holding show audio reconnects a few
# seconds AFTER the run is armed, and the music then needs longer still to load; waiting after the GO
# made the operator hold the deadman through 15-22 s of nothing, and a brief slip ended two runs).
# Until the audio is ready, or this long has passed, a press is not a GO. After the GO only
# AUDIO_AFTER_GO_S more is waited, then the show runs silently rather than keep the operator holding.
AUDIO_ARMED_WAIT_S = 10.0
# A stage page plays the show's video from the stage box's own copy, or streams it with a HEAD START: the
# GO waits until every page has `stage_buffer_s` of it (projector.json, 25 s) — at most this long. Past it
# the stage is dropped (the tablet keeps the music), or a show that requires its video is refused.
STAGE_BUFFER_WAIT_S = 90.0
AUDIO_AFTER_GO_S = 2.0

# Start modes (agreed with the Orchestrator, 2026-10-13). An ARMED run has passed every check but the
# deadman; nothing moves, the show audio loads, and the e-stop, CH5, faults and the arms are still
# watched. The GO is the deadman's RISING edge after arming: one already held at Start must be let go
# and pressed again, so a deadman taped down — or simply never released — can never start a show.
# Letting go while armed is not an abort; after the GO it is, as always.
START_MODES = ("deadman", "countdown", "now")
ARM_TIMEOUT_S = 300.0     # armed this long with no GO: refused, so a forgotten run cannot start later
ARMED_RESEND_S = 1.0      # the face's ARMED WAIT is re-sent this often; the head holds it for 3 s

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
    "FACE": "handed back to the face's own state-driven behaviour (EMOTE AUTO), with the robot's idle "
            "skin and theme (SKIN IDLE, THEME IDLE)",
    "DEVICE": "the head's own volume and brightness back (VOLUME IDLE, BRIGHT IDLE)",
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
                  "head": True, "fault": None, "arm_pose": {"ARM_L": {}, "ARM_R": {}},
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
    UNKNOWN = {"ch5": None, "deadman": None, "estop": None, "arms": None, "head": None,
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
            items.append({"url": "/api/choreo/media/" + m["file"], "at": 0.0, "kind": "music",
                          "role": m.get("role") or cue.DEFAULT_AUDIO_ROLE, "path": os.path.join(base, m["file"])})
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
            items.append({"url": "/api/choreo/media/library/" + e["file"], "at": 0.0, "kind": "music",
                          "role": m.get("role") or cue.DEFAULT_AUDIO_ROLE, "path": f,
                          "lib": m["song"], "sha256": e.get("sha256")})
        else:
            skipped.append(f"song {m['song']} is not in this robot's library ({library.root()})")
    for e in tl.get("tracks", {}).get("SOUND", []):
        if e.get("output") != "tablet":
            continue
        f = show._sound_file(e["cue"].get("sound"))
        if f:
            items.append({"url": "/api/choreo/media/" + os.path.relpath(f, base).replace(os.sep, "/"),
                          "at": e["t"], "kind": "sound", "role": e.get("role") or cue.DEFAULT_AUDIO_ROLE, "path": f})
        else:
            skipped.append(f"sound {e['cue'].get('sound')} is not on this machine")
    return items, skipped


def video_items(show, tl) -> tuple:
    """The show's projector videos per output: ({output: [item]}, skipped). An item is what
    video_out.ShowVideo plays: {t, end_t, path, from_s, end, id}, in show seconds. A play ends at its
    own end, or at the next VIDEO cue (a stop, or the next video), whichever is first."""
    evs = (tl.get("tracks") or {}).get("VIDEO") or []
    if not evs:
        return {}, []
    try:
        idx, unreadable = library.load_index() or {}, None
    except Exception as err:  # noqa: BLE001  video trouble never stops a show (as audio, DEC-7)
        idx, unreadable = {}, f"{type(err).__name__}: {err}"
    out, skipped = {}, []
    for k, e in enumerate(evs):
        c = e.get("cue") or {}
        if c.get("do") != "play":
            continue
        nxt = evs[k + 1]["t"] if k + 1 < len(evs) else None
        end_t = e["end"] if e.get("end", e["t"]) > e["t"] else (nxt if nxt is not None else tl.get("duration_s") or e["t"])
        if nxt is not None:
            end_t = min(end_t, nxt)
        ent = idx.get(c.get("video"))
        f = ent and ent.get("kind") == "video" and os.path.join(library.root(), ent.get("file", ""))
        if unreadable:
            skipped.append(f"the show library cannot be read ({unreadable}) — running without video")
            break
        if not f or not os.path.isfile(f):
            skipped.append(f"video {c.get('video')} is not in this robot's library ({library.root()})")
            continue
        out.setdefault(e.get("output") or cue.DEFAULT_VIDEO_OUTPUT, []).append(
            {"t": e["t"], "end_t": end_t, "path": f, "from_s": float(c.get("from_s") or 0.0),
             "end": c.get("end", "black"), "id": c.get("video"), "song": ent.get("song"),
             "sha256": ent.get("sha256")})            # a stage box's local copy is found by it
    return out, skipped


def preflight(requires: dict, st: dict, deadman: bool = True) -> list:
    """Refusals, as (code, sentence). Built from the show's `requires:` (validated by cue.py), plus
    what every show needs: no e-stop, the deadman held, no fault — and nothing UNKNOWN. `deadman`
    False leaves the deadman out: a run that will be ARMED waits for it instead."""
    out = []

    def need(field, ok, what):
        v = st.get(field)
        if v is None:
            out.append(("UNKNOWN_STATE", f"{field} is unknown — {what}; unknown refuses, never defaults"))
        elif not ok(v):
            out.append(("PRECONDITION_FAILED", f"{field} is {v!r} — {what}"))

    need("estop", lambda v: v is False, "the e-stop must be clear")
    if deadman:
        need("deadman", lambda v: v is True, "the deadman must be held")
    if st.get("fault"):
        out.append(("PRECONDITION_FAILED", f"fault: {st['fault']}"))
    if "rc_authority" in requires:
        need("ch5", lambda v: v == requires["rc_authority"], f"CH5 must be {requires['rc_authority']}")
    if "arms" in requires and not isinstance(st.get("effectors"), dict):
        # an older dashboard: one all-or-nothing arms field. With `effectors`, each part the show
        # needs is checked instead (effector_check), so a part it does not use cannot refuse it
        need("arms", lambda v: v == requires["arms"], f"the arms must be {requires['arms']}")
    if requires.get("head"):
        head = st.get("head", st.get("tab5"))     # an older dashboard names the field tab5
        if head is not True:                      # @fault SHOW_HEAD_NOT_UP   (unknown refuses too)
            out.append(("SHOW_HEAD_NOT_UP", "the head is not ready for a show: the head tablet must be "
                        "showing its face page, with no video call holding it"
                        + (" (its state is unknown)" if head is None else "")))
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


def armed_monitor(requires: dict, st: dict):
    """Checked every tick while a run is ARMED and waiting for the deadman: everything `monitor`
    checks except the deadman, plus the arms (they must still be active when the GO comes).
    (status, code, cause) to disarm it, or None."""
    if st.get("estop") is not False:
        return (ESTOPPED, "ESTOP", "e-stop asserted" if st.get("estop") else "e-stop state unknown")
    if st.get("fault"):
        return (ABORTED, "FAULT", f"fault: {st['fault']}")
    if "rc_authority" in requires and st.get("ch5") != requires["rc_authority"]:
        return (ABORTED, "AUTHORITY", f"CH5 is {st.get('ch5')!r}, not {requires['rc_authority']}")
    if "arms" in requires and not isinstance(st.get("effectors"), dict) and st.get("arms") != requires["arms"]:
        return (ABORTED, "ARMS", f"the arms are {st.get('arms')!r}, not {requires['arms']}")
    if isinstance(st.get("effectors"), dict) and (gone := _down(st["effectors"], requires.get("_needs") or ())):
        p, e = gone[0]
        return (ABORTED, "EFFECTOR_UNAVAILABLE", f"{p} became {e.get('state') or 'unknown'}: {e.get('why') or 'no reason given'}")
    return None


# The robot-state fields the dashboard fills only when asked (choreo_proxy._probe_now), not by its poll
PROBED_FIELDS = ("arms", "head", "effectors")


# ---- effectors: what a show needs vs what the robot has (cue.EFFECTORS) --------------------------
# The dashboard reports each part as {"state": "ok"|"degraded"|"down", "why": ...} under `effectors`.
# A part the map does not name is not checked (the dashboard says what it knows); a named part that
# is not "ok" is unavailable. The show's `can_skip:` parts may be left out, but only on the
# operator's approval: the run is refused with NEEDS_APPROVAL and an offer, and the Shows page runs
# it again with `skip`. A skipped arm is assumed at rest, where the variant was swept.
# Every joint, rolls included. A limp arm's ROLL joints (3, 5, 7) have no gravity moment and stay
# where they were left (Arms lane: right J5 +15.8° on a hanging arm, 2026-10-12), but in the twin a
# hanging arm with its rolls turned 30° puts its fingers into the body, so they are not waived: the
# refusal names the joint, and a limp arm is turned back by hand.
SKIPPED_ARM_TOL_DEG = 10.0


def _down(eff: dict, needs) -> list:
    return [(p, eff.get(p) if isinstance(eff.get(p), dict) else {"state": eff.get(p)})
            for p in needs if p in eff and (eff[p].get("state") if isinstance(eff[p], dict) else eff[p]) != "ok"]


def effector_check(tl: dict, st: dict) -> tuple:
    """(refusals [(code, sentence)], offer [parts]) for a run of this export against the robot's
    `effectors`. The offer is what the operator may approve to run without."""
    eff = st.get("effectors")
    if not isinstance(eff, dict):
        return [], []
    down = _down(eff, tl.get("needs") or ())
    if not down:
        return [], []
    can = set(tl.get("can_skip") or ())
    say = lambda p, e: f"{p} is {e.get('state') or 'unknown'}: {e.get('why') or 'no reason given'}"  # noqa: E731
    hard = [(p, e) for p, e in down if p not in can]
    if hard:
        return [("EFFECTOR_UNAVAILABLE", "; ".join(say(p, e) for p, e in hard)
                 + " — this show needs it" + ("" if len(hard) == 1 else ", and those"))], []
    offer = sorted(set(tl.get("skip") or ()) | {p for p, _ in down})
    arms = [p for p in offer if p in ("arm_left", "arm_right")]
    return [("NEEDS_APPROVAL", "; ".join(say(p, e) for p, e in down)
             + f" — this show can run without {', '.join(offer)}, on your approval"
             + (f" (the {' and '.join(a.replace('_', ' ') for a in arms)} must hang at rest: it was checked there)"
                if arms else ""))], offer


def skipped_arms_at_rest(skip, measured: dict, eff=None) -> list:
    """Refusals for a skipped arm that is away from rest, where its variant was swept. Its angles come
    from the dashboard's `effectors` (the Arms lane reads `pose_deg` from the motors even when the arm
    is limp, which /joint_states may not), else from /joint_states; an arm neither reports is left to
    the approval, which says it must hang at rest."""
    out = []
    for slot in cue.ARM_SLOTS:
        part = cue.EFFECTORS[slot]
        e = (eff or {}).get(part) if isinstance((eff or {}).get(part), dict) else {}
        pose = e.get("pose_deg") if isinstance(e.get("pose_deg"), list) else (measured or {}).get(slot)
        if part in skip and pose is not None:
            far = [(j, v) for j, v in enumerate(signed_pose(pose), 1) if abs(v) > SKIPPED_ARM_TOL_DEG]
            if far:
                out.append(("PRECONDITION_FAILED", f"{part} is skipped but not at rest ("
                            + ", ".join(f"J{j} {v:+.0f}°" for j, v in far) + ") — the run without it was "
                            "checked with it at rest; move it there (a limp arm turns by hand)"))
    return out


def signed_pose(p) -> list:
    """An arm pose as 7 signed degrees (the controller's sense): from a list, a {joint: deg} dict, or
    nothing (zero)."""
    if isinstance(p, (list, tuple)) and len(p) == 7:
        return [float(v) for v in p]
    p = p or {}
    return [float(p.get(str(j), p.get(j, 0.0))) for j in range(1, 8)]


def prep_moves(tl: dict, measured: dict, skip=()) -> tuple:
    """Moves that bring EVERY arm to where the show starts it — zero, the pose the validator's speed
    check and the twin both start every arm from (cue.py, sim_preview.arm_pose) — timed by the
    distance at the speed caps (x PEAK_FACTOR, the warning-free time), so the move needs no warning.
    Every arm, not only the ones the show moves: an arm left out stays wherever an aborted run left
    it, outside what was validated (found on the robot 2026-10-08, a one-arm run after a two-arm
    abort). `measured` is {slot: pose} in signed degrees. Returns (seconds, {slot: [7 signed deg]}).
    An arm the run SKIPS (a can_skip variant) is left alone: it may be down, and its variant was
    swept with it at rest (skipped_arms_at_rest)."""
    secs, targets = 0.0, {}
    for slot in cue.ARM_SLOTS:
        if cue.EFFECTORS[slot] in skip:
            continue
        now = signed_pose(measured.get(slot))
        need = max(abs(now[j - 1]) / cue.SPEED_CAP_DEG_S[j] * cue.PEAK_FACTOR for j in range(1, 8))
        secs, targets[slot] = max(secs, need), [0.0] * 7
    return round(secs, 2), targets


# What each slot is, on the Shows page's routine buttons (an icon each), in this order.
USE_PARTS = (("ARM_L", "arms"), ("ARM_R", "arms"), ("GRIP_L", "grippers"), ("GRIP_R", "grippers"),
             ("HEAD", "head"), ("FACE", "face"), ("SOUND", "sound"), ("LIGHT", "light"),
             ("LIFT", "lift"), ("DANCE", "base"), ("DRIVE", "base"), ("DEVICE", "device"))


def twin_only_tools(tl: dict) -> str | None:
    """Why a show cannot run on this robot because of its ARM tools (effectors.py), or None: a tool type
    with no driver at a flange. Today's grippers, and shows that name no tools, pass. The crawling hand on
    the floor (`base`, CRAWL) is the floor_hand part instead: hand_output.availability, which a show may
    skip (can_skip)."""
    import effectors
    reg = effectors.registry()
    for mount, fit in sorted((tl.get("tools") or {}).items()):
        if mount == "base":
            continue
        tool = reg.get(fit.get("type"))
        if tool is None or tool.output is None:
            return f"needs {fit.get('type')} at {mount}, which has no driver yet — it runs only in the twin"
    return None


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
    if isinstance(show.doc.get("device"), dict) and show.doc["device"]:
        used.add("DEVICE")                # the show-level volume / brightness is a use too
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
        elif part in ("face", "device"):
            live = face_live                # the head's volume and brightness go with the face
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
                 arms=None, live=False, grips=None, lift=None, light=None, start="now",
                 countdown_s=None, face=None, video=None, video_override=None, video_default=None, skip=(),
                 hand=None, base=None):
        self.path, self.limits_path, self.robot = path, limits_path, robot
        # The projector (video_out.py): {output: backend} this runner has, the operator's play-time
        # choice of output, and this run's ShowVideo per output in use
        self.video_outputs, self.video_override = video or {}, video_override
        self.videos, self.video_note = [], None
        self.video_default = video_default or cue.DEFAULT_VIDEO_OUTPUT
        # Music on the projector (notes/design/choreo-audio.md): its ShowVideo, where the music is now
        # ("projector" | "tablet" | None) and why it is not on the projector
        self.music, self.music_on, self.music_why, self._load_id = [], None, None, None
        self._carrier = None                      # the output whose video carries the show's song
        self.projector_settings = video_out.load_settings()
        self.skip = frozenset(skip or ())         # parts this run plays without (the operator approved)
        self.hand_out = hand if live else None     # hand_output.FloorHand: the crawling hand on the floor
        self.hand_run = None                       # ... and this run's HandRun, once it is armed
        self.offer = []                           # parts the operator may approve to run without
        self.base_out = base if live else None   # base_output.BaseOutput: DANCE on the real base
        self.base_run, self.base_note = None, None
        if start not in START_MODES:
            raise ValueError(f"start must be one of {', '.join(START_MODES)}, not {start!r}")
        # How this run starts (START_MODES); the countdown is this run's, else the show's, else 5 s.
        self.start, self.countdown_req = start, countdown_s
        self.countdown = None                     # resolved once the show is read
        self.face = face                          # LiveFace on a live run: the ARMED indication
        self._armed_shown = False
        self.deadline = None                      # (what, clock time): when ARMED times out / COUNTDOWN ends
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
        self._arm_goals = {}                      # goal id -> {slot, kind, sent, answered}
        self._arm_latest = {}                     # slot -> the id of its newest arm goal
        # The grippers (plan item 3): this robot's calibration (grippers.py), read at the run's start;
        # None sends no gripper cues. A close that STALLED (its goal aborted: something is in there)
        # is never re-sent on that side for the rest of the run.
        self.grips = grips if self.live else None
        # The lift and the state light (show_outputs.py): each armed separately, a live run's only.
        self.lift = lift if self.live else None
        self.light = light if self.live else None
        self.no_neck = False                      # set by the palette: no neck fitted on this robot
        self._state = {}                          # the robot's state as last read, each tick
        self._grip_last = {}                      # slot -> 0..1 last sent
        self._grip_stalled = set()
        self._grip_closes = {}                    # slot -> ids of the close goals sent this run
        self._grip_watch = {}                     # slot -> (when to read it, the closed position sent)
        self._said_no_finger = set()
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
                self._grip_watch[slot] = (self.clock() + GRIP_SETTLE_S, metres)
            else:
                self._grip_watch.pop(slot, None)  # a newer command: the close is no longer what it holds
            self._arms_moving = True
            self._log("grip", slot=slot, open=round(frac, 3), metres=round(metres, 5), show_t=round(t, 3))
        except Exception as e:  # noqa: BLE001  a gripper is never a reason to stop the show
            # Once, then no more grips this run: a retry per cue would only repeat the failure.
            self.grips = None
            self._log("live_error", error=f"grip {side}: {type(e).__name__}: {e} — no more gripper cues "
                                          "are sent this run")

    def _grip_contact_checks(self, t):
        """After each close has settled: did the fingers stop short of closed? (see GRIP_CONTACT_M)"""
        for slot, (due, closed) in list(self._grip_watch.items()):
            if self.clock() < due:
                continue
            del self._grip_watch[slot]
            side = arm_output.GRIP_SIDES[slot]
            pos = self.arms.finger(side) if hasattr(self.arms, "finger") else None
            if pos is None:
                if side not in self._said_no_finger:
                    self._said_no_finger.add(side)
                    self._log("live", note=f"the {side} gripper's position cannot be read (/joint_states): "
                                           "contact is judged by the goal's result alone", slot=slot)
                continue
            lo_closed, op = self.grips[side]
            short = (pos - closed) * (1.0 if op > lo_closed else -1.0)      # metres MORE OPEN than sent
            self._log("grip_settled", slot=slot, finger_m=round(pos, 4), sent_m=round(closed, 4),
                      short_mm=round(short * 1000, 1), show_t=round(t, 3))
            if short > GRIP_CONTACT_M and slot not in self._grip_stalled:
                self._grip_stalled.add(slot)
                self._log("live", note=f"the {side} gripper stopped {short * 1000:.0f} mm short of closed — "
                                       "something is in it (CONTACT); it will not be closed again this run",
                          slot=slot)

    def _armed(self, what):
        """The face's ARMED indication (head protocol v5): WAIT, a countdown's seconds, or OFF. A
        live run's only, and never a reason to stop a show."""
        if self.face is None or not hasattr(self.face, "armed"):
            return
        if what == "OFF" and not self._armed_shown:
            return
        self._armed_shown = what != "OFF"
        try:
            self.face.armed(what)
        except Exception as e:  # noqa: BLE001
            self._log("face_error", error=f"ARMED {what}: {type(e).__name__}: {e}")

    def start_info(self) -> dict:
        """For the Shows page: the start mode, and how long is left of ARMED or COUNTDOWN."""
        out = {"start": self.start, "countdown_s": self.countdown}
        if self.status == ARMED:
            out["deadman"] = self._state.get("deadman")
            out["audio_waiting"] = bool(getattr(self, "audio_waiting", False))
        if self.deadline and self.status in (ARMED, COUNTDOWN):
            out["left_s"] = round(max(0.0, self.deadline[1] - self.clock()), 1)
        return out

    def _end(self, status, code, cause, t=None):
        self.ended = (status, code, cause, t)
        self.deadline = None
        self._armed("OFF")
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
        for sv in self.videos + self.music + self._hands() + self._bases():   # black (an e-stop: stopped); the
            sv.end(status)                                      # floor hand opens and stops; the base stops
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

    def _hands(self) -> list:
        return [self.hand_run] if self.hand_run is not None else []

    def _audio_holder(self):
        try:
            return self.audio.state().get("holder")
        except Exception:  # noqa: BLE001
            return None

    def _audio_ready(self, load_id):
        try:
            st = self.audio.state()
        except Exception:  # noqa: BLE001
            return False
        return bool(st.get("ready")) and st.get("load") in (None, load_id)

    def _media_late(self, load_id, media) -> list:
        """What an armed run is still waiting for: the tablet's show audio, and each projector or stage
        output that has not loaded yet."""
        late = [] if not self.audio_live or self._audio_ready(load_id) else ["the tablet's show audio"]
        return late + [(sv.waiting_for() if hasattr(sv, "waiting_for") else f"the {sv.state().get('output')} output")
                       for sv in media if not sv.ready()]

    def _media_ready(self, load_id, media) -> bool:
        return not self._media_late(load_id, media)

    def _bases(self) -> list:
        return [self.base_run] if self.base_run is not None else []

    def _base_preflight(self, tl) -> list:
        """A live run's DANCE moves, on the Thor's base tracker. Not ready: the show runs WITHOUT its base
        moves, saying why — or, for `dance: {required: true}`, it is refused (SHOW_BASE_NOT_READY)."""
        dance = tl.get("dance")
        if not (self.live and self.base_out is not None and (tl.get("tracks") or {}).get("DANCE")):
            return []
        rd = self.base_out.readiness()
        if not rd["ready"]:
            # @fault SHOW_BASE_NOT_READY
            if (dance or {}).get("required"):
                return [("SHOW_BASE_NOT_READY", rd["why"])]
            self.base_note = rd["why"] + " — running without the base's dance steps"
            self._log("base_warning", note=self.base_note)
            return []
        self.base_run = self.base_out.make_run(tl, log=self._log)
        self._log("base", note="DANCE moves go to the base tracker on this run")
        return []

    def _video_preflight(self, show, tl) -> list:
        """The show's videos, each on its output. An output that is not ready is a warning and the show
        runs without that video (as audio, DEC-7) — or a refusal, VIDEO_NOT_READY, for a show that says
        video: {required: true}."""
        items, skipped = video_items(show, tl)
        for why in skipped:
            self._log("video_warning", note=why)
        required = (show.doc.get("video") or {}).get("required") is True if isinstance(show.doc.get("video"), dict) else False
        out, notes = [], list(skipped)
        for name, its in items.items():
            if name == "off":
                self._log("video", note="video output is OFF for this run: the videos are not shown")
                continue
            be = self.video_outputs.get(name)
            st = be.status() if be is not None else {"ready": False, "why": f"this robot has no {name} video output"}
            if not st.get("ready"):
                why = f"video on {name}: {st.get('why') or 'not ready'}"
                # @fault SHOW_VIDEO_NOT_READY
                if required:
                    out.append(("SHOW_VIDEO_NOT_READY", why))
                else:
                    notes.append(why + " — running without it")
                    self._log("video_warning", note=why)
                continue
            lead = (self.projector_settings.get("video_lead_s") if name == "cast" else
                    self.projector_settings.get("stage_video_lead_s") if name == "stage" else None)
            # A video that CARRIES the show's song (its library entry's `song`, in step with the show's
            # music) plays the music itself: it is started by the AUDIO offset — the beat is what must
            # land — and if it drops out the tablet takes the music over (choreo-audio.md).
            song = (tl.get("music") or {}).get("song")
            carries = [it for it in its if song and it.get("song") == song and abs(it["t"] - it["from_s"]) < 0.05]
            if carries:
                self._carrier = name
                lead = self.projector_settings.get("stage_audio_lead_s" if name == "stage" else "audio_lead_s")
            if hasattr(be, "make_show"):          # the stage page: it keeps itself on the show's clock
                self.videos.append(be.make_show([dict(it, lead_s=lead or 0.0) for it in its], tl.get("music"),
                                                buffer_s=self.projector_settings.get("stage_buffer_s"), log=self._log,
                                                on_lost=self._music_lost if carries else None))
                continue
            self.videos.append(video_out.ShowVideo(
                be, [dict(it, lead_s=lead) if lead is not None else it for it in its], log=self._log,
                on_lost=self._music_lost if carries else None))
        self.video_note = "; ".join(notes) or None
        return out

    def _projector(self):
        """(name, backend) of the output a show's video would use now — the operator's choice, else the
        robot's default — when it is ready; else (None, why)."""
        name = self.video_override or self.video_default
        if name == "off":
            return None, "the projector output is off"
        be = self.video_outputs.get(name)
        if be is None:
            return None, f"this robot has no {name} output"
        st = be.status()
        return (name, be) if st.get("ready") else (None, st.get("why") or "the projector isn't ready")

    def _route_music(self, tl, items):
        """MUSIC to the projector when one is ready, DIALOGUE always to the tablet (choreo-audio.md). A
        music item for the projector stays in the tablet's set HELD — loaded, silent — so the tablet can
        take it over in step if the projector drops out (`unhold`)."""
        if self._carrier:
            # the projector's video carries the show's song: the tablet holds its copy, silent, as the
            # fallback (whatever its role — the video plays it anyway, so playing it here would double it)
            for it in items:
                if it.get("kind") == "music":
                    it["held"] = True
            self.music_on, self.music_why = "projector", None
            self._log("audio", note=f"music in the projector's video ({self._carrier}); the tablet holds it")
            return
        music = [it for it in items if it.get("role") == "music"]
        if not music:
            return
        name, be = self._projector()
        why = None if name else be
        if name and any((e.get("output") or self.video_default) == name
                        for e in (tl.get("tracks") or {}).get("VIDEO", [])):
            name, why = None, "the projector plays this show's videos (one at a time), so the music stays on the tablet"
        if name is None:
            self.music_on, self.music_why = "tablet", why
            self._log("audio", note=f"music on the tablet: {why}")
            return
        settings = self.projector_settings
        if hasattr(be, "make_show"):
            # the stage page (a stage Pi's speaker, say) plays the music itself, under its own scene, on the
            # show's clock; the tablet holds its copy, silent, and takes over if every page goes
            lead = settings.get("stage_audio_lead_s") or 0.0
            for it in music:
                it["held"] = True
            self.music = [be.make_show([
                {"t": it["at"], "end_t": max(it["at"], tl.get("duration_s") or 0.0) + 0.5, "path": it.get("path"),
                 "url": it.get("url"),                # the same address the tablet fetches it by
                 "lib": it.get("lib"), "sha256": it.get("sha256"),   # a stage box's own copy, if it has one
                 "from_s": 0.0, "end": "black", "id": f"music:{os.path.basename(it.get('path') or '')}",
                 "lead_s": lead, "audio": True} for it in music], tl.get("music"),
                buffer_s=settings.get("stage_buffer_s"), log=self._log, on_lost=self._music_lost)]
            self.music_on, self.music_why = "projector", None
            self._log("audio", note=f"music on the stage page ({lead:g} s early); dialogue on the tablet")
            return
        lead = settings.get("audio_lead_s")
        sends = []
        for it in music:
            path = it.get("path")
            if settings.get("music_as") == "video" and name == "cast":
                cache = os.path.join(os.path.dirname(video_out.SETTINGS_PATH), "cast-cache")
                path = video_out.audio_as_video(path, cache) if path else None
                if path is None:
                    self.music_on = "tablet"
                    self.music_why = "the music can't be sent as video here (no ffmpeg on the robot)"
                    self._log("audio", note=f"music on the tablet: {self.music_why}")
                    return
            sends.append((it, path))
        for it, path in sends:
            it["held"] = True                     # the tablet has it, silent, as the fallback
        self.music = [video_out.ShowVideo(be, [
            {"t": it["at"], "end_t": max(it["at"], tl.get("duration_s") or 0.0) + 0.5, "path": path,
             "from_s": 0.0, "end": "stop", "id": f"music:{os.path.basename(path)}", "lead_s": lead}
            for it, path in sends], log=self._log, on_lost=self._music_lost)]
        self.music_on, self.music_why = "projector", None
        self._log("audio", note=f"music on the projector ({name}, {lead:g} s early); dialogue on the tablet")

    def _music_lost(self, why):
        """The projector dropped out mid-show: the tablet plays its held music from here, in step."""
        self.music_on, self.music_why = "tablet", f"{why} — the tablet took the music over"
        if self.audio_live:
            self._audio({"op": "unhold", "id": self._load_id})
        self._log("audio", note=f"music handed to the tablet: {why}")

    def video_state(self):
        return [sv.state() for sv in self.videos]

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
            show.video_override = self.video_override
            show.skip = self.skip                # a variant: validated (and its own stamp checked) as played
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
        why = twin_only_tools(tl)
        if why:
            # a crawling hand, or a tool with no driver (effectors.py): nothing on the robot can play it
            return self._end(REFUSED, "SHOW_TOOL_TWIN_ONLY", why)
        if tl.get("dance") is not None and not (self.live and self.base_out is not None):
            self._log("note", note="DANCE plays in the twin only: this run does not drive the base")
        self.feed.show(self.path, tl)

        if self.stop_requested:
            return self._end(ABORTED, "OPERATOR_STOP", "stopped by the operator")
        self.countdown = 0.0 if self.start == "now" else (
            cue.countdown_s(self.countdown_req) if self.countdown_req is not None else None)
        if self.countdown is None:
            self.countdown = cue.countdown_s(show.doc.get("start_countdown_s"))
        if self.countdown is None:
            self.countdown = cue.START_COUNTDOWN_DEFAULT_S

        # PREFLIGHT: requires vs the robot as it is now — all of it but the deadman, when the run
        # will wait for the deadman
        self._set(PREFLIGHT, start=self.start, countdown_s=self.countdown)
        if hasattr(self.robot, "refresh"):
            self.robot.refresh()
        if self.stop_requested:
            return self._end(ABORTED, "OPERATOR_STOP", "stopped by the operator")
        st = self.robot.read()
        refusals = preflight(show.requires or {}, st, deadman=self.start != "deadman")
        if self.live and tl.get("crawler") and hand_output.PART in (tl.get("needs") or ()):
            # the floor hand answers for itself (the dashboard does not know it): fitted, a driver, and the
            # hand answering on its port. Moves it has never run play too; the log names them.
            fh = hand_output.availability(tl, self.hand_out)
            self._log("hand", note=f"the floor hand: {fh['state']}" + (f" — {fh['why']}" if fh["why"] else ""))
            if fh.get("untried"):
                self._log("hand", note="this crawl includes moves the real hand has not run before: "
                                       + "; ".join(fh["untried"]))
            eff = st.get("effectors") if isinstance(st.get("effectors"), dict) else {}
            st = dict(st, effectors=dict(eff, **{hand_output.PART: fh}))
        eff_refusals, self.offer = effector_check(tl, st)
        refusals = eff_refusals + refusals           # the offer first: it is what the operator can act on
        show.requires = dict(show.requires or {}, _needs=tl.get("needs") or [])   # armed_monitor re-checks them
        refusals += self._video_preflight(show, tl)
        refusals += self._base_preflight(tl)
        if refusals:
            return self._end(REFUSED, refusals[0][0], "; ".join(r[1] for r in refusals))
        if self.hand_out is not None and tl.get("crawler") and hand_output.PART not in self.skip:
            self.hand_run = self.hand_out.make_run(tl, log=self._log)

        # LIVE: the arms' real position must be known. Unknown refuses — and it is read again after
        # the GO, because the start-pose move begins from where they are THEN.
        measured = st.get("arm_pose") or {}
        if self.live:
            measured = self.arms.measured()
            gone = [s for s in cue.ARM_SLOTS if cue.EFFECTORS[s] not in self.skip
                    and (measured is None or measured.get(s) is None)]
            if gone:
                return self._end(REFUSED, "UNKNOWN_STATE",
                                 f"the arms' measured position is unknown (/joint_states: {', '.join(gone)}) — a live run needs it")
            if (bad := skipped_arms_at_rest(self.skip, measured, st.get("effectors"))):
                return self._end(REFUSED, bad[0][0], "; ".join(r[1] for r in bad))
            self._log("live", note="LIVE: the real arms will follow this routine",
                      measured={k: [round(v, 1) for v in val] for k, val in measured.items()})

        # The show-audio device downloads the show's files now (4.2: whole files, started on cue),
        # so an armed run's music is ready by the time the operator gives the GO.
        items, skipped = audio_items(show, tl)
        load_id = self._load_id = f"{show.id}@{round(time.time(), 3)}"
        for why in skipped:
            self._log("audio_warning", note=why)
        self._route_music(tl, items)
        items = [{k: v for k, v in it.items() if k not in ("path", "lib", "sha256")}   # the tablet fetches by URL
                 for it in items]
        if items:
            if not self._audio_holder():
                self._log("audio_note", note="no device holds show audio yet — the music goes to the first "
                                             "that claims it (a tablet changing page as the run starts)")
            if self._audio({"op": "load", "id": load_id, "items": items}):
                self.audio_live = True
        # The projector's videos and music, and the stage pages, load now too (paused, on their own
        # threads), so an armed run's GO waits for them as well as for the tablet
        for sv in self.videos + self.music + self._hands() + self._bases():   # ... and the floor hand's port
            sv.begin()                                          # opens (nothing moves until the show's clock starts)

        # ARMED_WAITING: nothing moves until the deadman is pressed — freshly, after arming
        if self.start == "deadman" and (stop := self._wait_for_go(show, st, load_id)):
            return stop

        # COUNTDOWN: after the GO (or at once, in countdown mode), before anything moves. The
        # deadman must stay held from here on: letting go aborts.
        if self.countdown > 0:
            self.deadline = ("COUNTDOWN", self.clock() + self.countdown)
            self._set(COUNTDOWN, secs=self.countdown)
            self._armed(f"{self.countdown:g}")
            while self.clock() < self.deadline[1]:
                if (stop := self._tick_checks(show, None)):
                    return stop
                self.sleep(TICK_S)
            self.deadline = None
        if self.live and self.start != "now":
            measured = self.arms.measured()
            if measured is None or any(measured.get(s) is None for s in cue.ARM_SLOTS
                                       if cue.EFFECTORS[s] not in self.skip):
                return self._end(ABORTED, "UNKNOWN_STATE",
                                 "the arms' measured position became unknown (/joint_states) before the "
                                 "start-pose move")

        # PREPPING: travel to the start pose, slowly enough to need no warning — while the
        # show-audio device finishes loading
        secs, targets = prep_moves(tl, measured, self.skip)
        if self.live and secs > 0:
            for slot in targets:
                pts = arm_output.track_points([{"start": 0.0, "t": secs, "pose_signed":
                                                {str(j): 0.0 for j in range(1, 8)}}],
                                               start=signed_pose(measured.get(slot)))
                if (why := self._send_arm(slot, pts, "start-pose move")):
                    return self._end(REFUSED, "ARM_OUT_OF_RANGE", why)
            self._arms_moving = True
        self._set(PREPPING, prep_s=secs, targets=targets, audio_items=len(items))
        t_prep = self.clock()
        # an armed run already waited for the audio before its GO: the operator is holding now
        t_end = t_prep + secs
        wait_until = t_prep + (AUDIO_AFTER_GO_S if self.start == "deadman" else AUDIO_READY_WAIT_S)
        while self.clock() < t_end or (self.audio_live and not self._audio_ready(load_id)):
            no_holder = self.audio_live and self.clock() >= t_prep + AUDIO_HOLDER_GRACE_S \
                and not self._audio_holder()
            if no_holder or (self.audio_live and self.clock() >= max(t_end, wait_until)):
                self._audio({"op": "cut"})
                self._audio_silent("no device is playing show audio — running silently" if no_holder else
                                   f"the show-audio device was not ready in time — running silently")
                break
            if (stop := self._tick_checks(show, None)):
                return stop
            self.sleep(TICK_S if self.clock() < t_end else 0.1)

        # WAITING_FOR_START: a start in the future, so the music and the arms can begin together
        self._set(WAITING, lead_s=self.start_lead)
        t0 = self.clock() + self.start_lead
        for sv in self.videos + self.music + self._hands() + self._bases():   # each follows the show's clock
            sv.go(lambda: self.clock() - t0)
        if self.audio_live:
            # T0 on the Thor's monotonic clock; the device converts it to its own audio clock
            self._audio({"op": "start", "id": load_id, "t0_mono": round(t0, 4)})
        while self.clock() < t0:
            if (stop := self._tick_checks(show, None)):
                return stop
            self.sleep(TICK_S)

        # RUNNING
        self._set(RUNNING, duration_s=tl["duration_s"])
        self._armed("OFF")
        if self.live:
            # Each arm's whole routine as one goal, from show time 0 — sent as the show starts.
            for slot in cue.ARM_SLOTS:
                kfs = tl.get("tracks", {}).get(slot) or []
                if kfs:
                    if (why := self._send_arm(slot, arm_output.track_points(kfs), "routine")):
                        return self._end(ABORTED, "ARM_OUT_OF_RANGE", why, 0.0)
                    self._arms_moving = True
            if any(tl.get("tracks", {}).get(g) for g in ("GRIP_L", "GRIP_R")):
                if self.grips is None:
                    self._log("live", note="gripper cues are NOT sent: this robot has no gripper calibration "
                                           f"({grippers.default_path()}; plan 6.2)")
                elif not hasattr(self.arms, "send_grip"):
                    self._log("live", note="gripper cues are NOT sent: this runner's arm output has no grippers")
                    self.grips = None
                elif (missing := [side for slot, side in arm_output.GRIP_SIDES.items()
                                  if tl.get("tracks", {}).get(slot) and side in self.arms.grips_ready()]):
                    self._log("live", note="gripper cues are NOT sent: no gripper controller for "
                                           f"{' and '.join(missing)} (is that arm active?)")
                    self.grips = None
                else:
                    self._log("live", note="LIVE: the grippers follow this routine (calibrated per side)")
            if self.no_neck and tl.get("tracks", {}).get("HEAD"):
                self._log("live", note="no neck is fitted on this robot (OPENDUBS_HEAD_NECK_HOST=none): "
                                       "the routine's pan/tilt stays in the twin")
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
            if self.grips is not None and self._grip_watch:
                self._grip_contact_checks(t)
            self.feed.playhead(t, True, RUNNING)
            if t >= tl["duration_s"] and i >= len(events):
                return self._end(COMPLETED, "COMPLETED", "the show ran to its end", t)
            self.sleep(TICK_S)

    def _clamp(self, slot, points, kind):
        """(points, refusal): every waypoint inside the arm controller's admitted joint range, moved
        in by at most CLAMP_DEG and reported; a larger excursion is a refusal, sent nowhere."""
        ranges = self.arms.controller_ranges() if hasattr(self.arms, "controller_ranges") else None
        if not ranges or slot not in ranges:
            if not getattr(self, "_said_no_ranges", False):
                self._said_no_ranges = True
                self._log("live", note="the arm controllers' joint ranges could not be read: waypoints are sent "
                                       "unclamped (a start a hair past a limit would be refused by the controller)")
            return points, None
        out, moved = [], {}
        for t, deg in points:
            row = list(deg)
            for j, (lo, hi) in enumerate(ranges[slot]):
                v = row[j]
                if v < lo or v > hi:
                    off = (lo - v) if v < lo else (v - hi)
                    if off > CLAMP_DEG:
                        return None, (f"the {arm_output.ARM_SIDES[slot]} arm's J{j + 1} would be at {v:.2f} deg, "
                                      f"{off:.2f} deg outside what its controller admits ({lo:.2f}..{hi:.2f}) — "
                                      f"in the {kind}")
                    row[j] = min(max(v, lo), hi)
                    moved[j + 1] = max(moved.get(j + 1, 0.0), off)
            out.append((t, row))
        if moved:
            self._log("live", note=f"{arm_output.ARM_SIDES[slot]} arm {kind}: waypoints clamped into the controller's "
                                   "range (the arm rested just past a limit)",
                      slot=slot, clamped_deg={str(j): round(v, 3) for j, v in moved.items()})
        return out, None

    def _send_arm(self, slot, points, kind):
        points, refusal = self._clamp(slot, points, kind)
        if refusal:
            return refusal
        gid = self.arms.send(slot, points)
        self._arms_moving = True                  # sent: any end from here on cancels it
        if gid is not None:
            self._arm_goals[gid] = {"slot": slot, "kind": kind, "sent": self.clock(), "answered": False}
            self._arm_latest[slot] = gid
        self._log("arm_goal", event="sent", slot=slot, goal=gid, goal_kind=kind, points=len(points),
                  secs=round(points[-1][0], 2) if points else 0.0)
        return None

    def _arm_checks(self, t):
        """What the arm controllers said about our goals: every answer logged; a goal REJECTED, never
        answered within ARM_ACK_S, or ABORTED by the controller while it is the arm's current goal
        ends the show — the arms are not following it, and a live show must not pretend they are."""
        if not self.live or not hasattr(self.arms, "drain_events"):
            return None
        for ev in self.arms.drain_events():
            g = self._arm_goals.get(ev.get("goal"))
            if g is None:                         # a gripper goal: logged, judged by the gripper code
                self._log("grip_goal", **ev)
                continue
            self._log("arm_goal", **ev, goal_kind=g["kind"])
            side = arm_output.ARM_SIDES[g["slot"]]
            if ev["event"] == "accepted":
                g["answered"] = True
            elif ev["event"] == "rejected":
                return self._end(ABORTED, "ARM_GOAL_REJECTED",
                                 f"the {side} arm controller REJECTED the {g['kind']} goal — the arms would not "
                                 "follow the show (the reason is in the arm container's log)", t)
            elif ev["event"] == "result" and ev.get("status") == 6 and self._arm_latest.get(g["slot"]) == ev["goal"]:
                return self._end(ABORTED, "ARM_GOAL_ABORTED",
                                 f"the {side} arm controller ABORTED the {g['kind']} mid-way"
                                 + (f": {ev['error']}" if ev.get("error") else ""), t)
        now = self.clock()
        for gid, g in self._arm_goals.items():
            if not g["answered"] and now - g["sent"] > ARM_ACK_S:
                side = arm_output.ARM_SIDES[g["slot"]]
                return self._end(ABORTED, "ARM_GOAL_UNANSWERED",
                                 f"the {side} arm controller did not answer the {g['kind']} goal within "
                                 f"{ARM_ACK_S:g} s — the arms are not following the show", t)
        return None

    def _wait_for_go(self, show, st, load_id=None):
        """ARMED_WAITING. Returns None on the GO (the deadman's rising edge after arming, once the show
        audio is ready or AUDIO_ARMED_WAIT_S has passed), else the run's end: an operator stop, a disarm
        (e-stop, CH5, a fault, the arms) or the timeout."""
        requires = show.requires or {}
        # The dashboard asks the arms and the head only ON DEMAND (refresh, at preflight: the arm read
        # uses the arms' CAN bus) and forgets the answer after its PROBE_MAX_AGE_S (10 s). Past that
        # the polled state says None, which is "not asked lately", not "down": the preflight's answer
        # stands for the wait. An answer that SAYS otherwise still disarms, and after the GO a
        # down arm ends the show by its rejected goal (ARM_GOAL_*).
        probed = {k: st.get(k) for k in PROBED_FIELDS}
        self.deadline = ("ARMED", self.clock() + ARM_TIMEOUT_S)
        prev = st.get("deadman")
        media = self.videos + self.music
        wait_s = max([AUDIO_ARMED_WAIT_S] + [STAGE_BUFFER_WAIT_S for sv in media if hasattr(sv, "drop")])
        audio_until = self.clock() + wait_s if (self.audio_live or media) else None
        self.audio_waiting = audio_until is not None
        self._set(ARMED, timeout_s=ARM_TIMEOUT_S, deadman=prev,
                  note=("getting the show's audio and video ready — then press the deadman" if self.audio_waiting else
                        "the deadman is held: let go of it, then press it to start" if prev is True
                        else "press the deadman to start"))
        early = False
        self._armed("WAIT")
        waits = 1
        resend = self.clock() + ARMED_RESEND_S
        while True:
            self.feed.heartbeat()
            if self.stop_requested:
                return self._end(ABORTED, "OPERATOR_STOP", "stopped by the operator while armed")
            self._state = st = self.robot.read()
            for k, v in probed.items():
                if st.get(k) is None:
                    st[k] = v
            end = armed_monitor(requires, st)
            if end:
                return self._end(end[0], end[1], f"disarmed while waiting for the deadman: {end[2]}")
            if self.audio_waiting and not self._media_ready(load_id, media):
                late = self._media_late(load_id, media)          # what the Shows page says while it waits
                note = "getting the show's audio and video ready: " + ", ".join(late) + " — then press the deadman"
                if note != getattr(self, "_wait_note", None):
                    self._wait_note = note
                    self._set(ARMED, timeout_s=ARM_TIMEOUT_S, deadman=prev, note=note)
            if self.audio_waiting and (self._media_ready(load_id, media) or self.clock() >= audio_until):
                self.audio_waiting = False
                late = self._media_late(load_id, media)
                self._log("audio_note", note="the show audio is ready" if not late else
                          f"not ready after {wait_s:g} s: {', '.join(late)} — the tablet gets "
                          f"{AUDIO_AFTER_GO_S:g} s more after the GO, then runs silently; the projector "
                          "catches up on the show's clock")
                for sv in [sv for sv in media if hasattr(sv, "drop") and not sv.ready()]:
                    why = f"{sv.waiting_for()} after {wait_s:g} s"
                    if isinstance(show.doc.get("video"), dict) and show.doc["video"].get("required") is True:
                        # @fault SHOW_VIDEO_NOT_READY
                        return self._end(REFUSED, "SHOW_VIDEO_NOT_READY", f"video on stage: {why}")
                    sv.drop(why)                 # the stage plays nothing; the tablet keeps the music
                    self.video_note = f"video on stage: {why} — running without it"
                self._set(ARMED, timeout_s=ARM_TIMEOUT_S, deadman=prev,
                          note=("let go of the deadman, then press it to start" if prev is True
                                else "press the deadman to start"))
            dm = st.get("deadman")
            if dm is True and prev is False and self.audio_waiting and not early:
                early = True
                self._log("deadman", held=True, note="pressed before the show audio was ready: not a GO — "
                          "let go, and press again when the Shows page says so")
            elif dm is True and prev is False:
                self.deadline = None
                self._log("go", note="the deadman was pressed: GO", armed_wait_sent=waits)
                return None
            if dm != prev:
                # unknown counts as not released: only a press seen from a KNOWN release is a GO
                self._log("deadman", held=dm)
                prev = dm
            if self.clock() >= self.deadline[1]:
                return self._end(REFUSED, "START_TIMEOUT",
                                 f"armed for {ARM_TIMEOUT_S:g} s and the deadman was never pressed")
            if self.clock() >= resend:
                self._armed("WAIT")
                waits += 1
                resend = self.clock() + ARMED_RESEND_S
            self.sleep(TICK_S)

    def _tick_checks(self, show, t):
        self.feed.heartbeat()
        if (stop := self._arm_checks(t)):
            return stop
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
LOCAL_SHOWS = (os.environ.get("OPENDUBS_SHOWS_DIR") or os.environ.get("OPENDUBS_LOCAL_SHOWS")
               or "/var/lib/opendubs/shows")      # the robot's content folder (content.py)


class Palette:
    """The operator's routines: shows marked `palette:`, each validated, run one at a time on the
    operator's pick. Every palette routine ends in the rejoin state (the validator insists), so they
    can run in any order; the runner's start-pose move covers the gap. Shows come from the
    repository's folder and, when it exists, the robot's own (LOCAL_SHOWS)."""

    def __init__(self, shows_dir, limits_path, robot, feed, log=None, start_lead=START_LEAD_S,
                 clock=time.monotonic, sleep=time.sleep, audio=None, arms=None, face=None, gaze=None,
                 local_shows=None, dashboard=None, lift_live=False, light_live=False, no_neck=False,
                 video=None, hand=None, base=None):
        self.shows_dir, self.limits_path, self.robot, self.feed = shows_dir, limits_path, robot, feed
        self.no_neck = no_neck               # this robot has no neck (OPENDUBS_HEAD_NECK_HOST=none)
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
        # The projector (video_out.py): this runner's outputs, the operator's play-time choice (None:
        # each show's own), and when the cast output was last checked (the tile's freshness)
        self.video = video or {}
        self.base = base                     # base_output.BaseOutput, or None: DANCE stays in the twin
        self.video_override = None
        self.video_checked_at = None
        self.hand = hand                     # hand_output.FloorHand, or None: this robot has no floor hand
        if "cast" in self.video:
            threading.Thread(target=self._watch_cast, daemon=True, name="cast-watch").start()
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
            if twin_only_tools(tl):
                tl = dict(tl, valid=False, problems=[{"msg": twin_only_tools(tl)}] + list(tl["problems"]))
            if show.id in seen:
                # two shows, one id: the operator's pick would be ambiguous, so this one is refused
                tl = dict(tl, valid=False, problems=[{"msg": f"its id {show.id!r} is also the {seen[show.id]} "
                                                              "show's — rename one"}] + list(tl["problems"]))
            seen.setdefault(show.id, f"{where} folder's")
            out.append({"id": show.id, "label": pal.get("label", show.id), "order": pal.get("order", 999),
                        "path": path, "from": where, "valid": tl["valid"], "duration_s": tl["duration_s"],
                        "needs": tl.get("needs") or [], "can_skip": tl.get("can_skip") or [],
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
                    **({"start_info": cur[1].start_info()} if running else {}),
                    "default_start": self.default_start(),
                    "skipped": sorted(cur[1].skip) if running else [],
                    "audio_note": cur[1].audio_note if cur else None,
                    "live": bool(running and cur[1].live), "live_available": self.arms is not None}

    VIDEO_CHECK_S = 10.0          # the tile calls an answer older than 15 s "not checked"

    def _watch_cast(self):
        """Keep the cast output's answer fresh: look for the projector when it is not found (mDNS, or its
        fixed address), and see that a found one is still connected."""
        cast = self.video["cast"]
        while True:
            try:
                if cast.cast is None or not cast.connected():
                    cast.discover(timeout=6.0)
            except Exception as e:  # noqa: BLE001
                cast.cast, cast.why, cast.detail = None, "couldn't look for the projector on the robot's Wi-Fi", str(e)
            self.video_checked_at = time.time()
            time.sleep(self.VIDEO_CHECK_S)

    def video_default(self) -> str:
        """The robot's default output for a show that names none (OPENDUBS_VIDEO_OUTPUT, per robot)."""
        d = os.environ.get("OPENDUBS_VIDEO_OUTPUT") or cue.DEFAULT_VIDEO_OUTPUT
        return d if d in cue.VIDEO_OUTPUTS else cue.DEFAULT_VIDEO_OUTPUT

    def video_status(self) -> dict:
        """The projector tile's data (notes/design/choreo-video.md, the contract)."""
        outs = {}
        if "cast" in self.video:
            outs["cast"] = self.video["cast"].status()
        else:
            outs["cast"] = {"found": False, "name": None, "host": None, "ready": False,
                            "why": "this robot isn't set up to cast (the runner has no cast output)"}
        if "stage" in self.video:
            outs["stage"] = self.video["stage"].status()
        if "hdmi" in self.video:
            outs["hdmi"] = self.video["hdmi"].status()
        else:
            outs["hdmi"] = {"present": False, "ready": False,
                            "why": "this robot isn't set up for HDMI video (the runner has no HDMI output)"}
        playing = None
        with self.lock:
            cur = self.current
        if cur and cur[2].is_alive() and cur[1].videos:
            v = cur[1].video_state()[0]
            playing = {"show": cur[0], **{k: v[k] for k in ("file", "output", "position_s", "sync_err_ms", "seeks")}}
        checked = self.video_checked_at if "cast" in self.video else None
        live = cur if cur and cur[2].is_alive() else None
        return {"checked_at": round(checked if checked is not None else time.time(), 3),
                "outputs": outs, "default": self.video_default(),
                "selected": self.video_override or self.video_default(),
                "override": self.video_override, "playing": playing,
                "note": live[1].video_note if live else None,
                "settings": video_out.load_settings(),
                "music": ({"on": live[1].music_on, "why": live[1].music_why} if live
                          else {"on": None, "why": None})}

    def set_projector_settings(self, change):
        """The projector's offsets and how it takes music (choreo-audio.md), set at the robot by ear;
        stored on the robot (projector.json) and used from the next run."""
        if not isinstance(change, dict) or not change:
            raise ValueError(f"the body must set one of {', '.join(video_out.LEAD_KEYS)}, music_as")
        video_out.save_settings(change)
        return self.video_status()

    def set_video_output(self, output):
        """The operator's play-time choice (cast | hdmi | stage | off), or None to follow each show. Not while
        a routine runs: its videos are already on their output."""
        if output not in (None, *cue.VIDEO_OUTPUTS):
            raise ValueError(f"output must be one of {', '.join(cue.VIDEO_OUTPUTS)}, or null for each show's own")
        with self.lock:
            if self.current and self.current[2].is_alive():
                # @fault SHOW_VIDEO_BUSY
                raise RuntimeError("a show is playing — choose the video output before it starts, or stop it")
            self.video_override = output
        return self.video_status()

    def default_start(self) -> str:
        """The robot's runs wait for the deadman; a twin's count down (agreed 2026-10-13)."""
        return "deadman" if self.arms is not None else "countdown"

    def run(self, rid: str, start=None, countdown_s=None, skip=None) -> dict:
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
            skip = skip or []
            if not isinstance(skip, list) or any(x not in r.get("can_skip", ()) for x in skip):
                raise ValueError(f"skip must list parts this show can run without ({', '.join(r.get('can_skip') or ()) or 'none'}), "
                                 f"not {skip!r}")
            start = start or self.default_start()
            if start not in START_MODES:
                raise ValueError(f"start must be one of {', '.join(START_MODES)}, not {start!r}")
            if countdown_s is not None and cue.countdown_s(countdown_s) is None:
                raise ValueError(f"countdown_s must be 0 to {cue.START_COUNTDOWN_MAX_S:g} seconds, "
                                 f"not {countdown_s!r}")
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
                            audio=self.audio, arms=self.arms, live=live, grips=grips, lift=lift, light=light,
                            start=start, countdown_s=countdown_s, face=self.face if live else None,
                            video=self.video, video_override=self.video_override,
                            video_default=self.video_default(), skip=skip, hand=self.hand if live else None,
                            base=self.base if live else None)
            runner.no_neck = live and self.no_neck
            for x in sinks:
                x.note = lambda kind, **kw: runner._log(kind, **kw)
            t = threading.Thread(target=self._play, args=(rid, runner), daemon=True)
            self.current = (rid, runner, t)
            t.start()
            return {"started": rid, "live": runner.live, "start": start}

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
            self.last = {"id": rid, "status": status, "code": code, "cause": cause,
                         "offer": list(runner.offer), "skipped": sorted(runner.skip)}
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
            if palette and self.path == "/api/video":
                return self._json(palette.video_status())
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
                    n = int(self.headers.get("Content-Length") or 0)
                    body = json.loads(self.rfile.read(n) or b"{}") if n else {}
                    if not isinstance(body, dict):
                        raise ValueError("the body must be a JSON object: {start, countdown_s}")
                    return self._json(palette.run(p.partition("?")[0].rsplit("/", 1)[1],
                                                  start=body.get("start"), countdown_s=body.get("countdown_s"),
                                                  skip=body.get("skip")))
                except RuntimeError as e:
                    return self._json({"error": str(e)}, 409)
                except KeyError as e:
                    return self._json({"error": e.args[0] if e.args else "not found"}, 404)
                except ValueError as e:
                    return self._json({"error": str(e)}, 422)
            if p == "/api/video/settings" and palette:
                try:
                    n = int(self.headers.get("Content-Length") or 0)
                    body = json.loads(self.rfile.read(n) or b"{}") if n else {}
                    return self._json(palette.set_projector_settings(body))
                except ValueError as e:
                    return self._json({"error": str(e)}, 422)
                except OSError as e:
                    return self._json({"error": f"the robot couldn't save the projector settings: {e.strerror or e}"}, 500)
            if p == "/api/video/output" and palette:
                try:
                    n = int(self.headers.get("Content-Length") or 0)
                    body = json.loads(self.rfile.read(n) or b"{}") if n else {}
                    if not isinstance(body, dict) or "output" not in body:
                        raise ValueError("the body must be {output: cast|hdmi|off|null}")
                    return self._json(palette.set_video_output(body["output"]))
                except RuntimeError as e:
                    return self._json({"error": str(e), "code": "SHOW_VIDEO_BUSY"}, 409)
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
    r.add_argument("--start", choices=START_MODES, default="countdown",
                   help="deadman: arm, then wait for a fresh deadman press; countdown: count down at once "
                        "(the deadman held); now: no countdown")
    r.add_argument("--countdown", type=float, help="seconds between the GO and the first move "
                                                    "(default: the show's start_countdown_s, else 5)")
    pp = sub.add_parser("palette", help="serve the routine palette; the operator picks, it runs")
    pp.add_argument("shows", nargs="?", default=os.path.join(HERE, "shows"))
    pp.add_argument("--lift", choices=("none", "dashboard"), default="none",
                    help="dashboard: a live run moves the real lift through /api/mast (show_outputs.py)")
    pp.add_argument("--light", choices=("none", "dashboard"), default="none",
                    help="dashboard: a live run lays its colour over the state light (show_light.py)")
    pp.add_argument("--video", default="",
                    help="the projector outputs this runner drives, comma-separated: cast, hdmi, stage (video_out.py; "
                         "per robot: OPENDUBS_CAST_NAME / OPENDUBS_CAST_HOST, OPENDUBS_VIDEO_PORT (8094), OPENDUBS_VIDEO_BIND, "
                         "OPENDUBS_MPV_SOCKET, OPENDUBS_VIDEO_OUTPUT)")
    pp.add_argument("--hand", default=os.environ.get("OPENDUBS_FLOOR_HAND") or "off",
                    help="the floor hand's serial port (its RS-485 adapter, best by /dev/serial/by-id/), or off "
                         "(hand_output.py; per robot: OPENDUBS_FLOOR_HAND in robot.env)")
    pp.add_argument("--base", choices=("none", "ros"), default="none",
                    help="ros: a live run sends its DANCE moves to the Thor's base tracker (/dance/*, base_output.py)")
    pp.add_argument("--local-shows", default=LOCAL_SHOWS,
                    help="the robot's content folder, outside the deploy: its shows and tool packs "
                         "(show_sync.py fills it; default $OPENDUBS_SHOWS_DIR, else the old "
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
                    help="auto (OPENDUBS_HEAD_NECK_HOST, else the face's host, else 127.0.0.1; a "
                         "neck host of none or off means no neck is fitted) or a host: live runs turn "
                         "the real head (its GAZE command, Tab5 PR #56)")
    pp.add_argument("--earcons", choices=("on", "off"), default="on",
                    help="off: live runs do not send the head its earcons (CUE) — held back while the "
                         "head's audio task damps its face tracking (2026-10-08)")
    pp.add_argument("--face", default="none",
                    help="auto (the face's address from OPENDUBS_HEAD_FACE_HOST / OPENDUBS_TAB5_HOST / "
                         "127.0.0.1, the dashboard's face door) or a host: live runs drive the real face")
    for x in (r, pp):
        x.add_argument("--audio", choices=("none", "dashboard"), default="none",
                       help="play show audio on the dashboard's show-audio device (plan 4.2)")
        x.add_argument("--audio-url", default="http://127.0.0.1:8080/api/choreo/audio")
    a = ap.parse_args()
    if getattr(a, "local_shows", None):          # one content folder for this runner and all it loads
        os.environ.setdefault("OPENDUBS_SHOWS_DIR", a.local_shows)
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
        base = None
        if a.base == "ros":
            try:
                base = base_output.BaseOutput(base_output.RosBase())
            except Exception as e:  # noqa: BLE001  no ROS: DANCE stays in the twin
                print(f"runner: live base output NOT available ({type(e).__name__}: {e})", file=sys.stderr, flush=True)
        face = None
        if a.face != "none":
            face = face_bridge.LiveFace(face_bridge.head_face_host() if a.face == "auto" else a.face,
                                        earcons=a.earcons == "on")
            face.release()                   # a run that crashed may have left the face pinned
        gaze, no_neck = None, False
        neck = face_bridge.head_neck_host() if a.gaze == "auto" else a.gaze
        if a.gaze == "auto" and neck is None:
            no_neck = True                   # OPENDUBS_HEAD_NECK_HOST=none: as --gaze none, and said
            print("runner: no neck fitted (OPENDUBS_HEAD_NECK_HOST=none): pan/tilt stays in the twin",
                  file=sys.stderr, flush=True)
        elif a.gaze != "none":
            gaze = face_bridge.LiveGaze(neck)
            gaze.probe()                     # release a crashed run's lease; learn whether GAZE exists
        dash = (show_outputs.Dashboard(show_outputs.base_of(a.dashboard))
                if "dashboard" in (a.lift, a.light) else None)
        video = {}
        wanted = {x.strip() for x in a.video.split(",") if x.strip()}
        if wanted - {"cast", "hdmi", "stage"}:
            ap.error(f"--video: {', '.join(sorted(wanted - {'cast', 'hdmi', 'stage'}))} is not cast, hdmi or stage")
        if "cast" in wanted:
            video["cast"] = video_out.CastOutput(server=video_out.MediaServer(bind=video_out.bind_address()))
        if "hdmi" in wanted:
            video["hdmi"] = video_out.HdmiOutput()
        if "stage" in wanted:                     # the stage page (choreo-stage.md), through the dashboard
            video["stage"] = video_out.StageOutput(show_outputs.base_of(a.dashboard))
        pal = Palette(a.shows, a.limits, robot, feed, log, audio=audio, arms=arms, face=face, gaze=gaze,
                      local_shows=a.local_shows, dashboard=dash, lift_live=a.lift == "dashboard",
                      light_live=a.light == "dashboard", no_neck=no_neck, video=video,
                      hand=None if a.hand in ("", "off", "none") else hand_output.FloorHand(a.hand),
                      base=base)
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
    ref["r"] = runner = Runner(a.show, a.limits, robot, feed, log, audio_output=a.audio_output, audio=audio,
                               start=a.start, countdown_s=a.countdown)
    status, code, cause, _ = runner.run()
    print(f"runner: {status} ({code}): {cause}", file=sys.stderr, flush=True)
    time.sleep(a.hold)
    return 0 if status == COMPLETED else 1


if __name__ == "__main__":
    sys.exit(main())
