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

_lock = threading.Lock()
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
}


def start_op(name, arm="both"):
    """Begin a long arm operation. Returns (accepted, detail)."""
    spec = OPS.get(name)
    if spec is None:
        return False, f"unknown operation {name!r}"
    if arm not in ("left", "right", "both"):
        return False, f"arm must be left|right|both, got {arm!r}"

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


def _loop_health():
    """The teleop loop's own overrun warning, pulled out of the journal.

    This number was buried in a container log needing `docker exec` to read, and it is the
    difference between "teleop feels vague" and "the loop is missing 23% of its deadlines". It
    belongs on the screen. Absent is normal and GOOD: the loop only warns when it is late.
    """
    try:
        r = _run(["journalctl", "-u", TELEOP_UNIT, "-n", "60", "--no-pager", "-o", "cat"], 8)
    except Exception:                                # noqa: BLE001
        return None
    if r.returncode != 0:
        # Usually means the dashboard user is not in `systemd-journal`. Not fatal, and not worth
        # a sudo rule for a read -- say nothing rather than claim the loop is healthy.
        return {"available": False, "reason": "journal not readable"}
    last = None
    for line in r.stdout.splitlines():
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
    return {
        "op": op_status(),
        "motors": counts or None,
        "motors_error": m.get("error"),
        # Energized is the honest word: it says the motors are live, which is what matters before
        # anyone puts a hand near the arm. It does NOT mean a controller is commanding them.
        "energized": (counts.get("enabled", 0) > 0) if counts else None,
        "loop": _loop_health(),
        "teleop": {
            "running": teleop,
            "page": _port_open(8444),      # HTTPS page the Quest loads
            "poses": _port_open(8766),     # WSS pose channel
        },
    }
