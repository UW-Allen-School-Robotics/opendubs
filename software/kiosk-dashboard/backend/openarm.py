"""Arm operations for the dashboard: park, hold, activate, de-energize, and the teleop bridge.

park, hold, activate and down go to the ARM SERVICE (software/openarm/gafro/arm_service.py), one per arm, over its command socket (arm_client.py): the service owns the robot
and its guards (CH5, the e-stop, CH6, a person at the e-stop, a start near stow or ready), so the dashboard asks and shows the answer, and does not reimplement any of it.
Which stack answers is decided by what runs (`active_stack`): the arm service when an `opendubs-arm@<side>` unit is active or a service answers, the ROS stack (`openarm-up`, ROS_OPS)
when `ros2_control_node` runs. With neither, or both, or no way to tell, every moving operation says so and does nothing: two senders on one bus is the failure this prevents, so there
is no guessing and no fallback from one stack to the other. `relink` (one arm's CAN link) goes through `openarm-up`, which owns it. "both" means every arm that has a service running.

Long operations run in a BACKGROUND THREAD and are polled, because they take seconds (a park is 5 s or more) and an HTTP request held open that long is a browser timeout
waiting to happen. Only one at a time: two arm operations racing each other is a collision, not a queue.
"""
from __future__ import annotations

import json
import os
import socket
import subprocess
import threading
import time

_GAFRO = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "openarm", "gafro")     # beside kiosk-dashboard, in the repository and on the robot
if _GAFRO not in __import__("sys").path:
    __import__("sys").path.insert(0, _GAFRO)
import arm_client  # noqa: E402

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

#: name -> (argv, timeout seconds, human description of what MOVES). Only `relink`: the others go to the arm service (SERVICE_OPS).
ARM_CLI = "/usr/local/bin/arm"            # software/openarm/arm, installed by provision-arm-service.sh

OPS = {
    # What `arm doctor <side>` says: is the service up and answering, is /joint_states publishing, is a viewer on the ROS graph. Reads only. A nonzero status means it found
    # something, and its words are the answer, so start_op keeps them as output and not as an error.
    "doctor":    (lambda arm: [ARM_CLI, "doctor", arm], 25, "reads the service and ROS; moves nothing"),
    # One arm's CAN link (openarm-can@arm_<side>). Moves nothing; openarm-up refuses it under an
    # energized arm, and start_op refuses it while a show runs.
    "relink":    (lambda arm: _sudo([OPENARM_UP, "relink", arm]), 30, "restarts one arm's CAN link"),
}

#: The ROS stack's half (what the dashboard did before the arm service): the same words, through `openarm-up`, which keeps the guards (switch_controllers is atomic, `down` refuses an
#: arm not at stow, ...). Used only while `active_stack()` says "ros". `prepare` (get the arms show-ready) is `activate` here, which energizes and drives to ZERO where the arm service's goes to `ready`; the runner moves each arm to zero first either way,
#: and the result says which pose it was. follow, supervise and unsupervise are the arm service's alone.
ROS_OPS = {
    "park":     (lambda arm: _sudo([OPENARM_UP, "park", arm]), 180, "moves the arm to its stow pose"),
    "activate": (lambda arm: _sudo([OPENARM_UP, "activate", arm]), 180, "ENERGIZES and drives the arm to zero"),
    "down":     (lambda arm: _sudo([OPENARM_UP, "down"]), 180, "de-energizes both arms"),
    "up":       (lambda arm: _sudo([OPENARM_UP]), 120, "starts the stack, arms stay limp"),
    # HOLD drives the arm to where it already is: it preempts a running trajectory and leaves the arm commanded to its present pose.
    "hold":     (lambda arm: _sudo([OPENARM_UP, "hold", arm]), 90, "stops the arm where it is"),
}
SERVICE_ONLY = ("follow", "supervise", "unsupervise")
ROS_PREPARE_NOTE = "ROS stack: PREPARE is ACTIVATE (energized, driven to zero; the arm service would go to ready)\n"


def _ros_running():
    """Whether the old stack's `ros2_control_node` runs (its container's processes are visible to the host): True, False, or None when pgrep cannot tell."""
    try:
        r = _run(["pgrep", "-f", "ros2_control_node"], 3)
    except Exception:                                # noqa: BLE001
        return None
    return True if r.returncode == 0 else False if r.returncode == 1 else None


def _socket_alive(side):
    """Whether something accepts a connection on the side's service socket (a socket a dead service left behind refuses it). Sends nothing."""
    with socket.socket(socket.AF_UNIX) as probe:
        probe.settimeout(0.5)
        try:
            probe.connect(arm_client.socket_for(side))
        except OSError:
            return False
    return True


def _service_active(side):
    """Whether the arm service for `side` is up: its unit is active, or something accepts a connection on its socket (a service started by hand has no unit)."""
    return _unit_state(side).get("ActiveState") == "active" or _socket_alive(side)


def active_stack():
    """(stack, why): "gafro" (an arm service is up), "ros" (`ros2_control_node` runs), "none", "both" or "unknown" (pgrep cannot tell), and in words what that means.
    Only "gafro" and "ros" are answers a moving operation may act on."""
    ros = _ros_running()
    served = [side for side in arm_client.SIDES if _service_active(side)]
    if ros is None:
        return "unknown", "cannot tell whether the ROS arm stack runs (pgrep failed), so no arm operation is sent"
    if ros and served:
        return "both", (f"both arm stacks are running (the ROS stack's ros2_control_node and opendubs-arm@{', opendubs-arm@'.join(served)}): two senders on one bus. "
                        "Stop one (`openarm-up down`, or `down` on the arm service) before anything moves")
    if ros:
        return "ros", "the ROS arm stack is running"
    if served:
        return "gafro", "the arm service is running for: " + ", ".join(served)
    return "none", "no arm stack is running: START an arm service (opendubs-arm@<side>), or `openarm-up` for the ROS stack"


#: what the arm service does, by the dashboard's word for it, and what it moves
SERVICE_OPS = {
    "park":     "moves the arm to its stow pose",
    "activate": "moves the arm to its ready pose",
    "hold":     "stops the arm where it is",
    # Starts the arm following pose targets (`<side>/target`): the hand goes where the target is, at the stage's speed limits. It refuses without a person at the e-stop (supervise), and
    # ends with `hold`, CH6 or the e-stop.
    "follow":   "starts following pose targets: the hand goes where the target is",
    "down":     "ends the arm service; the motors stay stiff until the e-stop",
    # An attestation, not a movement: "a person is at the e-stop, head tracking is off, the neck centred". Until it is made the service refuses every moving command on a real bus.
    "supervise":   "says a person is at the e-stop, so the arm service allows moving commands",
    "unsupervise": "withdraws that: the arm service refuses moving commands again",
}
def _pose_deg(status):
    """The seven joint angles, degrees, from a service's status text (`joints (deg) +1.0 ...`), or None."""
    for line in (status or "").splitlines():
        if line.startswith("joints (deg)"):
            try:
                values = [float(v) for v in line.partition(")")[2].split()]
            except ValueError:
                return None
            return values if len(values) == 7 else None
    return None


def effector_status():
    """Each arm and gripper as the show runner's preflight reads them (choreo_proxy `effectors`): {part: {state, why, pose_deg?, excluded?}}.

    An arm is `ok` when its arm service answers AND a person has said they are at the e-stop (supervised): the service refuses every moving command without that, and the runner sends
    each arm its show as a trajectory goal. An arm whose service answers but is not supervised is `degraded`, with the way out in its `why`. An arm with no service is `down`, and says so. Under the ROS stack this returns None (the runner reads that stack's motors itself); with no stack, both, or no way to tell, every part is `down` with the reason. The grippers are `down` and `excluded`: the service drives them only once `use_gripper` is
    on and calibrated in its hardware config, and nothing here reports that yet. A part is never reported `ok` without an answer."""
    stack, why = active_stack()
    if stack == "ros":
        return None                      # the runner's own fallback: the motor summary of the ROS stack (choreo_proxy._probe_now)
    if stack != "gafro":
        return {part: ({"state": "down", "why": why} if part.startswith("arm_") else {"state": "down", "excluded": True, "why": why})
                for side in arm_client.SIDES for part in (f"arm_{side}", f"grip_{side}")}
    out = {}
    for side in arm_client.SIDES:
        status = _service_status(side)
        if status is None:
            out[f"arm_{side}"] = {"state": "down", "why": f"no arm service for the {side} arm (start opendubs-arm@{side})"}
        else:
            pose = {"pose_deg": p} if (p := _pose_deg(status)) else {}
            if "supervised: yes" in status.splitlines():
                out[f"arm_{side}"] = {"state": "ok", **pose}
            else:
                # Up, enabled and holding, but the service refuses every moving command until a person is at the e-stop (SUPERVISE), so the runner's goal for this arm would be rejected
                # and a live show ended by it. Not ok: the show is refused up front with a reason that says what to do, or offered without this arm if it can do without. Its pose stays,
                # so a skipped arm can still be checked to be at rest. Activating (`ready`) is not needed: a show moves each arm to zero from where it is.
                letter = side[0].upper()
                out[f"arm_{side}"] = {"state": "degraded", **pose, "why": f"the {side} arm service is up but nobody is supervising it, so it refuses every moving command (PREPARE {letter}, or SUPERVISE {letter})"}
        out[f"grip_{side}"] = {"state": "down", "excluded": True, "why": f"the {side} gripper is not enabled in the arm service (use_gripper, and its calibration)"}
    return out


#: Operations that only take capability away. They do not wait for, or count as, "the" running operation: a HOLD refused as "busy" while a move runs would be the one
#: button that must always work, and ending supervision is the same. They answer at once, so the request returns the service's answer.
INSTANT_OPS = ("hold", "unsupervise")
_SERVICE_TIMEOUT = 180.0

#: Bringing an arm's service up is the unit's job (software/openarm/opendubs-arm@.service). The sudoers rule (opendubs-arm.sudoers) allows exactly these commands, as
#: written: the full path and the `.service`. There is no `stop` here: `down` is how the service ends, and it refuses an arm that is not at stow, which `systemctl stop` would not.
SYSTEMCTL = "/usr/bin/systemctl"
UNIT_OPS = {"start": "enables the arm's motors and holds it where it is"}
_UNIT_TIMEOUT = 60.0


# The viewer (viser, `arm viewer <side> <port>`): the browser view of an arm and its pose target. The dashboard starts it and stops it, and only ever stops one it started: a port that is
# already open is somebody else's (a person's own session, another lane's twin) and is neither reused nor killed. 8080 is this dashboard.
VIEWER_PORTS = {"right": 8090, "left": 8091}
_VIEWER_STARTUP_S = 25.0
_viewers = {}                       # side -> {"proc", "state": starting|running|failed|stopped, "error"}


def _viewer_log(side):
    return os.path.join(__import__("tempfile").gettempdir(), f"opendubs-viewer-{side}.log")


def _viewer_alive(side):
    v = _viewers.get(side)
    return bool(v and v.get("proc") and v["proc"].poll() is None)


def _viewer_tail(side, lines=6):
    try:
        with open(_viewer_log(side)) as f:
            return " | ".join(l.strip() for l in f.read().strip().splitlines()[-lines:] if l.strip())
    except OSError:
        return ""


def _watch_viewer(side, port):
    """starting -> running once it listens, failed if it ends first or never listens; then stopped when it ends."""
    with _lock:
        proc = _viewers[side]["proc"]
    deadline = time.time() + _VIEWER_STARTUP_S
    while time.time() < deadline and proc.poll() is None and not _port_open(port):
        time.sleep(0.4)
    if proc.poll() is None and _port_open(port):
        with _lock:
            _viewers[side].update(state="running", error=None)
        proc.wait()
        with _lock:
            _viewers[side].update(state="stopped", error=None if proc.returncode in (0, -15) else f"ended with status {proc.returncode}: {_viewer_tail(side)}")
        return
    if proc.poll() is None:
        _kill_group(proc)
    with _lock:
        _viewers[side].update(state="failed", error=_viewer_tail(side) or f"it did not listen on port {port} in {int(_VIEWER_STARTUP_S)} s")


def _kill_group(proc):
    import signal
    try:
        os.killpg(proc.pid, signal.SIGTERM)
        proc.wait(timeout=5)
    except (ProcessLookupError, subprocess.TimeoutExpired):
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass


def viewer_start(side):
    """Start the side's viewer in the background. Returns (accepted, detail); the state is in snapshot()["viewers"]."""
    port = VIEWER_PORTS[side]
    with _lock:
        if _viewer_alive(side):
            return True, f"the {side} viewer is already running on port {port}"
        if _port_open(port):
            return False, (f"port {port} is already in use, and not by this dashboard's {side} viewer: it is left alone (stop it, or ask whoever started it; "
                           f"`ss -ltnp 'sport = :{port}'` says which process)")
        try:
            with open(_viewer_log(side), "w") as log:
                proc = subprocess.Popen([ARM_CLI, "viewer", side, str(port)], stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
        except FileNotFoundError:
            return False, f"{ARM_CLI} not found: the arm tooling is not installed here (software/openarm/provision-arm-service.sh)"
        _viewers[side] = {"proc": proc, "state": "starting", "error": None}
    threading.Thread(target=_watch_viewer, args=(side, port), name=f"viewer-{side}", daemon=True).start()
    return True, f"starting the {side} viewer on port {port}"


def viewer_stop(side):
    """Stop the viewer this dashboard started for `side`, and only that one. Returns (accepted, detail)."""
    with _lock:
        v = _viewers.get(side)
    if not _viewer_alive(side):
        return True, f"the {side} viewer is not running" + (" (the one on that port is not this dashboard's, and is left alone)" if _port_open(VIEWER_PORTS[side]) else "")
    _kill_group(v["proc"])
    return True, f"stopped the {side} viewer"


def viewer_state():
    with _lock:
        return {side: {"state": (_viewers.get(side) or {}).get("state", "stopped"), "port": port, "error": (_viewers.get(side) or {}).get("error")}
                for side, port in VIEWER_PORTS.items()}


def _unit(side):
    return f"opendubs-arm@{side}.service"


def _unit_state(side):
    """The unit's ActiveState, Result and ExecMainStatus (no sudo needed to read them), or {} if systemd does not answer."""
    try:
        r = _run([SYSTEMCTL, "show", _unit(side), "-p", "ActiveState", "-p", "Result", "-p", "ExecMainStatus"], 5)
    except Exception:                                # noqa: BLE001
        return {}
    return dict(line.partition("=")[::2] for line in r.stdout.splitlines()) if r.returncode == 0 else {}


def _start_unit(side):
    """`systemctl start` the arm's service and wait until it answers on its socket. Returns (output, error)."""
    if _service_status(side) is not None:
        return f"{side}: the arm service is already running", None
    try:
        r = _run(_sudo([SYSTEMCTL, "start", _unit(side)]), 30)
    except Exception as e:                           # noqa: BLE001
        return "", f"{side}: systemctl start failed: {type(e).__name__}: {e}"
    if r.returncode != 0:
        return "", f"{side}: " + (r.stderr.strip() or f"systemctl start returned {r.returncode}") + f" (sudoers: opendubs-arm.sudoers allows `{SYSTEMCTL} start {_unit(side)}`)"
    deadline = time.time() + _UNIT_TIMEOUT
    while time.time() < deadline:
        status = _service_status(side)
        if status is not None:
            return f"{side}: started; " + status.splitlines()[0], None
        state = _unit_state(side)
        if state.get("ActiveState") in ("failed", "inactive"):
            return "", (f"{side}: the unit ended ({state.get('Result', '?')}, exit status {state.get('ExecMainStatus', '?')}) before it answered: "
                        f"`journalctl -u {_unit(side)}` says why (exit 2 is the old arm stack still running)")
        time.sleep(0.5)
    return "", f"{side}: the arm service did not answer in {int(_UNIT_TIMEOUT)} s: `journalctl -u {_unit(side)}`"


def _service_status(side):
    """The service's status text for `side`, or None when there is none or it does not answer. Cheap: a unix socket."""
    try:
        return arm_client.send("status", path=arm_client.socket_for(side), timeout=0.5)
    except OSError:
        return None


def _is_running(status):
    """Whether the service's status line says a command is in progress (`<side> arm, <backend>: held; last: ...` when it is not)."""
    head = status.splitlines()[0] if status else ""
    return head.partition(": ")[2].partition(";")[0] not in ("held", "")


def _service_work(name, sides):
    """Send `name` to each side's service, and for park and activate wait until it has finished. Returns (output, error)."""
    out, errors = [], []
    for side in sides:
        path = arm_client.socket_for(side)
        try:
            reply = arm_client.send(name, path=path, timeout=10.0)
        except OSError as e:
            errors.append(f"{side}: the arm service does not answer at {path} ({e}); is it running, and is the socket's directory shared with this user?")
            continue
        out.append(f"{side}: {reply}")
        if reply.startswith("refused"):
            errors.append(f"{side}: {reply.removeprefix('refused: ')}")
    if errors or name not in ("park", "activate"):
        return "\n".join(out), "\n".join(errors) or None
    pending, deadline = set(sides), time.time() + _SERVICE_TIMEOUT
    while pending and time.time() < deadline:
        time.sleep(0.5)
        for side in sorted(pending):
            status = _service_status(side)
            if status is not None and _is_running(status):
                continue
            pending.discard(side)
            last = (status or "").partition("last: ")[2].splitlines()[0] if status else ""
            out.append(f"{side}: {last or 'no answer'}")
            if status is None or "stopped" in last or "failed" in last:
                errors.append(f"{side}: {last or 'the arm service stopped answering'}")
    if pending:
        errors.append(f"{', '.join(sorted(pending))}: {name} did not finish in {int(_SERVICE_TIMEOUT)} s (`hold` stops it)")
    return "\n".join(out), "\n".join(errors) or None


def _prepare_work(sides):
    """Bring each arm to ready for a show: start its service, attest that a person is at the e-stop, move it to `ready`. The three steps are the three buttons they used to be, one
    arm after the other, and the first refusal ends it with the arms where they are: an arm the chain did not get to is not touched, and one it did stays at ready. Returns (output, error).
    The confirmation that starts this names all three (teleop.js): the attestation is the person's, so it is never made on a path that did not say so."""
    out = []
    for side in sides:
        for step, work in (("start", lambda: _start_unit(side)), ("supervise", lambda: _service_work("supervise", [side])), ("activate", lambda: _service_work("activate", [side]))):
            text, error = work()
            out.append(text)
            if error:
                return "\n".join(t for t in out if t), f"{side}: {step} did not go through, and the chain stopped there: {error}"
    return "\n".join(t for t in out if t), None


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
    if spec is None and name not in SERVICE_OPS and name not in UNIT_OPS and name not in ROS_OPS and name not in ("viewer", "viewer-stop", "prepare"):
        return False, f"unknown operation {name!r}"
    if arm not in ("left", "right", "both"):
        return False, f"arm must be left|right|both, got {arm!r}"
    sides = []
    use_service = name in SERVICE_OPS or name in UNIT_OPS or name == "prepare"
    if use_service or name in ROS_OPS:
        stack, why = active_stack()
        if name in UNIT_OPS and stack not in ("none", "gafro"):
            return False, f"START is for the arm service, and {why}" if stack != "ros" else "the ROS arm stack is running: `openarm-up down` first (starting the arm service beside it is two senders on one bus)"
        if name == "up" and stack not in ("none", "ros"):
            return False, f"`openarm-up` starts the ROS stack, and {why}"
        if name not in UNIT_OPS and name != "up" and stack not in ("gafro", "ros"):
            return False, why
        if stack == "ros" and name not in UNIT_OPS and name != "up":
            if name in SERVICE_ONLY:
                return False, f"{name} is the arm service's: the ROS arm stack is running, not an arm service"
            spec, use_service = ROS_OPS["activate" if name == "prepare" else name], False
        elif name in ROS_OPS and name != "up" and stack == "gafro":
            use_service = True
        elif name == "up":
            spec, use_service = ROS_OPS["up"], False
    if name == "prepare":
        show = _show_running()
        if show:
            return False, f"a show is running ({show}): preparing the arms moves them, and waits until it ends"
        sides = ["left", "right"] if arm == "both" else [arm]
    if name in UNIT_OPS and arm not in ("left", "right"):
        return False, "START is per arm: left or right (starting a service enables that arm's motors)"
    if name in SERVICE_OPS and use_service:
        served = arm_client.sides_served()
        sides = served if arm == "both" else [arm]
        if not sides or any(side not in served for side in sides):
            return False, (f"no arm service for the {arm} arm (one per arm: `arm_service.py run --side <left|right> ...`; the dashboard has no other way to move an arm). "
                           f"Running: {', '.join(served) or 'none'}")
    if name in ("viewer", "viewer-stop"):
        if arm not in ("left", "right"):
            return False, "the viewer is per arm: left or right"
        return viewer_start(arm) if name == "viewer" else viewer_stop(arm)
    if name == "doctor" and arm not in ("left", "right"):
        return False, "the check is per arm: left or right"
    if name == "relink":
        if arm not in ("left", "right"):
            return False, "RESTART LINK is per arm: left or right"
        show = _show_running()
        if show:
            return False, f"a show is running ({show}): a link restart waits until it ends"

    if name in INSTANT_OPS and use_service:
        out, error = _service_work(name, sides)
        return (False, error) if error else (True, out)
    with _lock:
        if _op["running"]:
            # Deliberately a refusal rather than a queue: a second arm command arriving mid-motion
            # is a collision. Same reasoning as the lease model's select-one-with-handoff.
            return False, f"{_op['name']} is still running ({int(time.time() - _op['started'])}s)"
        _op.update({"running": True, "name": f"{name} {arm}".strip(), "started": time.time(),
                    "rc": None, "out": "", "error": None})

    def work():
        if use_service:
            try:
                out, error = (_prepare_work(sides) if name == "prepare" else _start_unit(arm) if name in UNIT_OPS else _service_work(name, sides))
            except Exception as e:                       # noqa: BLE001 - surfaced to the operator
                out, error = "", f"{type(e).__name__}: {e}"
            with _lock:
                _op.update({"rc": 1 if error else 0, "out": out[-4000:], "error": error, "running": False})
            return
        argv_fn, timeout, _desc = spec
        try:
            r = _run(argv_fn(arm), timeout)
            with _lock:
                _op["rc"] = r.returncode
                _op["out"] = ((ROS_PREPARE_NOTE if name == "prepare" else "") + (r.stdout or "") + (r.stderr or ""))[-4000:]
                if r.returncode != 0 and name != "doctor":
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
        "viewers": viewer_state(),
        "stack": dict(zip(("name", "why"), active_stack())),   # which arm stack answers: gafro | ros | none | both | unknown
        "service": {side: _service_status(side) for side in arm_client.sides_served()},   # per arm: its status text, None if it does not answer
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
