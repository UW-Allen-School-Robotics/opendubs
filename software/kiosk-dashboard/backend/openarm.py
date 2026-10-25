"""Arm operations for the dashboard: park, hold, activate, de-energize, and the teleop bridge.

Everything here SHELLS OUT TO `openarm-up` rather than talking to ros2_control itself. That is the
whole point: the guards live in one place. `openarm-up` knows that `switch_controllers` is atomic
and must be called one controller at a time, that the gripper controllers have to come up or teleop
dies on an action-server timeout, that `down` must not drop an arm which is not at its stow pose,
and that a dead controller_manager means skipping straight to disabling over CAN. Reimplementing
any of that here would give the robot two answers to the same question.

Long operations run in a BACKGROUND THREAD and are polled, because they genuinely take tens of
seconds -- `activate` waits up to 10 s for the gripper motor alone -- and an HTTP request held open
that long is a browser timeout waiting to happen. Only one at a time: two arm operations racing
each other is a collision, not a queue.
"""
from __future__ import annotations

import json
import os
import socket
import subprocess
import threading
import time

OPENARM_UP = "/usr/local/bin/openarm-up"
TELEOP_UNIT = "opendubs-teleop"
OPENARM_MOTORS = "/usr/local/lib/openarm/openarm-motors"
CONTAINER = "openarm"
TELEOP_MATCH = "openarm.demos"

# Motor state is read over CAN, on the same bus the controllers are using. Polling it at UI rate
# would put avoidable traffic on a control bus, so it is cached.
_MOTOR_TTL = 4.0

# The teleop bridge's per-hand status (its status.py): one JSON datagram to this port on localhost
# every 0.25 s, and at once when a hand's condition changes. Older than _TELEOP_STALE_S is "the
# bridge is not reporting", not "nothing is wrong".
TELEOP_STATUS_PORT = int(os.environ.get("OPENDUBS_TELEOP_STATUS_PORT", "5011"))
_TELEOP_STALE_S = 1.5

_lock = threading.Lock()
_teleop_rx = {"started": False, "at": 0.0, "data": None, "error": None}
_op = {"running": False, "name": None, "started": 0.0, "rc": None, "out": "", "error": None}
_motors = {"at": 0.0, "text": "", "error": None}


def _run(argv, timeout):
    return subprocess.run(argv, capture_output=True, text=True, timeout=timeout)


def _sudo(argv):
    return ["sudo", "-n", *argv]


# ---- long operations -----------------------------------------------------------------------

#: name -> (argv, timeout seconds, human description of what MOVES
OPS = {
    "park":      (lambda arm: _sudo([OPENARM_UP, "park", arm]), 180, "moves the arm to its stow pose"),
    "activate":  (lambda arm: _sudo([OPENARM_UP, "activate", arm]), 180, "ENERGIZES and drives the arm to zero"),
    "down":      (lambda arm: _sudo([OPENARM_UP, "down"]), 180, "de-energizes both arms"),
    "up":        (lambda arm: _sudo([OPENARM_UP]), 120, "starts the stack, arms stay limp"),
    # HOLD drives the arm to where it already is. That preempts any running trajectory and leaves
    # it commanded to its present pose -- "stop what you are doing without dropping anything".
    "hold":      (lambda arm: _sudo([OPENARM_UP, "hold", arm]), 90, "stops the arm where it is"),
    # One arm's CAN link (openarm-can@arm_<side>). Moves nothing; openarm-up refuses it under an
    # energized arm, and start_op refuses it while a show runs.
    "relink":    (lambda arm: _sudo([OPENARM_UP, "relink", arm]), 30, "restarts one arm's CAN link"),
}

RUNNER_URL = os.environ.get("OPENDUBS_RUNNER_URL", "http://127.0.0.1:8092")


def _show_running():
    """The routine the show runner is playing, or None (also when the runner is not up: then no
    show can be running). Asked only before a link restart."""
    import urllib.request
    try:
        with urllib.request.urlopen(f"{RUNNER_URL}/api/palette", timeout=1.5) as r:
            return (json.loads(r.read() or b"{}") or {}).get("running")
    except (OSError, ValueError):
        return None


def start_op(name, arm="both"):
    """Begin a long arm operation. Returns (accepted, detail)."""
    spec = OPS.get(name)
    if spec is None:
        return False, f"unknown operation {name!r}"
    if arm not in ("left", "right", "both"):
        return False, f"arm must be left|right|both, got {arm!r}"
    if name == "relink":
        if arm not in ("left", "right"):
            return False, "RESTART LINK is per arm: left or right"
        show = _show_running()
        if show:
            return False, f"a show is running ({show}): a link restart waits until it ends"

    argv_fn, timeout, _desc = spec
    with _lock:
        if _op["running"]:
            # Deliberately a refusal rather than a queue: a second arm command arriving mid-motion
            # is a collision. Same reasoning as the lease model's select-one-with-handoff.
            return False, f"{_op['name']} is still running ({int(time.time() - _op['started'])}s)"
        _op.update({"running": True, "name": f"{name} {arm}".strip(), "started": time.time(),
                    "rc": None, "out": "", "error": None})

    def work():
        try:
            r = _run(argv_fn(arm), timeout)
            with _lock:
                _op["rc"] = r.returncode
                _op["out"] = ((r.stdout or "") + (r.stderr or ""))[-4000:]
                if r.returncode != 0:
                    # The whole `openarm-up: ...` message, not its last line: the diagnosis comes
                    # first and the fix last, and the last line alone read as a fragment.
                    lines = r.stderr.strip().splitlines()
                    start = max((i for i, l in enumerate(lines) if l.startswith("openarm-up:")),
                                default=max(len(lines) - 1, 0))
                    _op["error"] = "\n".join(lines[start:]).removeprefix("openarm-up: ") or "failed"
        except subprocess.TimeoutExpired:
            with _lock:
                _op["error"] = f"{name} timed out after {timeout}s"
        except FileNotFoundError:
            with _lock:
                _op["error"] = (f"{OPENARM_UP} not found — the arm tooling is not installed "
                                f"on this host")
        except Exception as e:                       # noqa: BLE001 - surfaced to the operator
            with _lock:
                _op["error"] = f"{type(e).__name__}: {e}"
        finally:
            with _lock:
                _op["running"] = False

    threading.Thread(target=work, name=f"openarm-{name}", daemon=True).start()
    return True, _op["name"]


def op_status():
    with _lock:
        o = dict(_op)
    if o["started"]:
        o["elapsed"] = round(time.time() - o["started"], 1)
    return o


# ---- cheap state ---------------------------------------------------------------------------

def _motor_summary():
    """`enabled=N disabled=N faults=N missing=N`, cached.

    NOTE a single read can transiently report a motor `missing` — seen right after a stack
    shutdown, with three clean reads immediately after. Treat one bad sample as noise.
    """
    now = time.time()
    with _lock:
        if now - _motors["at"] < _MOTOR_TTL:
            return dict(_motors)
    try:
        r = _run([OPENARM_MOTORS, "summary"], 20)
        text, err = r.stdout.strip(), None if r.returncode == 0 else (r.stderr.strip() or "failed")
    except Exception as e:                           # noqa: BLE001
        text, err = "", f"{type(e).__name__}: {e}"
    with _lock:
        _motors.update({"at": now, "text": text, "error": err})
        return dict(_motors)


_links = {"at": 0.0, "data": None}


def _link_words(link):
    """The link in plain words, to follow "Its CAN link is" -- the same words openarm-up's
    link_words prints. None when it is healthy (up, ERROR-ACTIVE)."""
    if link.get("present") != "1":
        return "missing (the CAN adapter is unplugged, or its driver is not loaded)"
    if link.get("up") != "1":
        return "down"
    st = link.get("state", "UNKNOWN")
    if st == "ERROR-ACTIVE":
        return None
    w = link.get("warn")
    return f"in error ({st}" + (f"; {w} error-warnings since it came up" if w else "") + ")"


def link_status():
    """{"left": {...}, "right": {...}}: each arm's CAN link from `openarm-motors links` (ip only,
    nothing sent on the bus), and when it is NOT healthy a message naming the arm, what is wrong and
    what to check. Unarmed motors reading disabled or unheard are normal and never a message here:
    only a link that is missing, down or in error is. None if the tool cannot run."""
    now = time.time()
    with _lock:
        if now - _links["at"] < _MOTOR_TTL:
            return _links["data"]
    data = None
    try:
        r = _run([OPENARM_MOTORS, "links"], 10)
        if r.returncode == 0:
            data = {}
            for line in r.stdout.splitlines():
                parts = line.split()
                if not parts or not parts[0].startswith("arm_"):
                    continue
                link = dict(t.partition("=")[::2] for t in parts[1:])
                data[parts[0][4:]] = link
    except Exception:                                # noqa: BLE001 - shown as "unknown"
        data = None
    if data is not None:
        # which motors went unheard, per arm, from the motor summary's where= word (#465)
        where = {}
        for tok in (_motor_summary().get("text") or "").split():
            if tok.startswith("where="):
                for part in tok[6:].split(";"):
                    a, _, ids = part.partition(":")
                    where[a.removeprefix("arm_")] = [i for i in ids.split(",") if i]
        for side, link in data.items():
            words = _link_words(link)
            link["problem"] = words is not None
            link["restartable"] = words is not None and link.get("present") == "1"
            link["code"] = "ARM_LINK_ERROR" if words else None
            if words is None:
                link["message"] = None
                continue
            A = "Right" if side == "right" else "Left"
            none = "no motor answers. " if len(where.get(side, [])) >= 7 else ""
            check = ("check the CAN adapter's USB cable at the robot's computer"
                     if link.get("present") != "1" else
                     f"check the {side} arm's CAN cable and power, then press RESTART LINK "
                     f"{'R' if side == 'right' else 'L'}")
            link["message"] = f"{A} arm: {none}Its CAN link is {words}: {check}."
    with _lock:
        _links.update({"at": now, "data": data})
    return data


def teleop(verb):
    """start|stop the teleop bridge, via its systemd unit.

    Through the unit rather than spawning `docker exec` here: the face tracker was spawned
    directly once and a second copy fought the service, failed silently, and made anything started
    at boot invisible to the button.
    """
    if verb not in ("start", "stop"):
        return False, "use start|stop"
    try:
        r = _run(_sudo(["systemctl", verb, TELEOP_UNIT]), 25)
    except Exception as e:                           # noqa: BLE001
        return False, f"systemctl {verb} failed: {e}"
    if r.returncode != 0:
        return False, (r.stderr.strip() or f"systemctl {verb} returned {r.returncode}")
    return True, f"{verb}ed {TELEOP_UNIT}"


def _teleop_running():
    try:
        r = _run(["systemctl", "is-active", TELEOP_UNIT], 10)
        return r.stdout.strip() == "active"
    except Exception:                                # noqa: BLE001
        return None                                  # unknown, not "no"


def _teleop_listen():
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.bind(("127.0.0.1", TELEOP_STATUS_PORT))
    except OSError as e:
        with _lock:
            _teleop_rx["error"] = f"cannot listen on 127.0.0.1:{TELEOP_STATUS_PORT}: {e}"
        return
    while True:
        try:
            data = json.loads(s.recv(65535))
        except (OSError, ValueError):
            continue                                 # a bad datagram is skipped, not fatal
        with _lock:
            _teleop_rx.update({"at": time.time(), "data": data})


def teleop_status():
    """The bridge's latest per-hand report, or why there is none. Cheap: no CAN, no shell."""
    with _lock:
        if not _teleop_rx["started"]:
            _teleop_rx["started"] = True
            threading.Thread(target=_teleop_listen, name="teleop-status", daemon=True).start()
        rx = dict(_teleop_rx)
    if rx["error"]:
        return {"available": False, "reason": rx["error"]}
    age = time.time() - rx["at"]
    if rx["data"] is None or age > _TELEOP_STALE_S:
        return {"available": False, "reason": "the bridge is not reporting"}
    return dict(rx["data"], available=True, age_s=round(age, 2))


_GRIPPER_TIMEOUT = "Timed out waiting for"


def _last_exit(lines):
    """Why the last bridge run ended, when it is one the operator can fix. The one known case is
    upstream's gripper action-server timeout, which happens before the bridge can report."""
    for line in reversed(lines or []):
        if _GRIPPER_TIMEOUT in line and "gripper" in line:
            return {"code": "ARM_TELEOP_GRIPPER_TIMEOUT",    # @fault ARM_TELEOP_GRIPPER_TIMEOUT
                    "text": line.strip()[-160:]}
        if line.startswith("Started "):              # systemd's line: the last run began here
            break
    return None


def _port_open(port):
    import socket
    s = socket.socket()
    s.settimeout(0.4)
    try:
        return s.connect_ex(("127.0.0.1", port)) == 0
    finally:
        s.close()


_LOOP_RE = __import__("re").compile(
    r"target_hz=(?P<hz>[\d.]+).*?(?P<over>\d+)/(?P<ticks>\d+) ticks over budget.*?"
    r"worst overrun \+(?P<worst>[\d.]+)ms")


def _journal_tail():
    """The bridge's last 60 journal lines, or None when the journal cannot be read."""
    try:
        r = _run(["journalctl", "-u", TELEOP_UNIT, "-n", "60", "--no-pager", "-o", "cat"], 8)
    except Exception:                                # noqa: BLE001
        return None
    # A failure usually means the dashboard user is not in `systemd-journal`. Not fatal, and not
    # worth a sudo rule for a read.
    return r.stdout.splitlines() if r.returncode == 0 else None


def _loop_health(lines):
    """The teleop loop's own overrun warning, pulled out of the journal.

    This number was buried in a container log needing `docker exec` to read, and it is the
    difference between "teleop feels vague" and "the loop is missing 23% of its deadlines". It
    belongs on the screen. Absent is normal and GOOD: the loop only warns when it is late.
    """
    if lines is None:
        # Say nothing rather than claim the loop is healthy.
        return {"available": False, "reason": "journal not readable"}
    last = None
    for line in lines:
        m = _LOOP_RE.search(line)
        if m:
            last = m
    if last is None:
        return {"available": True, "over_pct": 0.0, "worst_ms": 0.0,
                "target_hz": None, "note": "no overrun warnings in the last 60 lines"}
    over, ticks = int(last["over"]), max(int(last["ticks"]), 1)
    return {"available": True, "target_hz": float(last["hz"]),
            "over_pct": round(100.0 * over / ticks, 1), "worst_ms": float(last["worst"])}


def snapshot():
    m = _motor_summary()
    counts = {}
    for tok in (m.get("text") or "").split():
        k, _, v = tok.partition("=")
        if v.isdigit():
            counts[k] = int(v)
    teleop = _teleop_running()
    journal = _journal_tail()
    return {
        "op": op_status(),
        "motors": counts or None,
        "links": link_status(),
        "motors_error": m.get("error"),
        # Energized is the honest word: it says the motors are live, which is what matters before
        # anyone puts a hand near the arm. It does NOT mean a controller is commanding them.
        "energized": (counts.get("enabled", 0) > 0) if counts else None,
        "loop": _loop_health(journal),
        "teleop": {
            "running": teleop,
            "page": _port_open(8444),      # HTTPS page the Quest loads
            "poses": _port_open(8766),     # WSS pose channel
            # Only when stopped: a running bridge reports for itself (teleop_status).
            "last_exit": None if teleop else _last_exit(journal),
        },
    }
