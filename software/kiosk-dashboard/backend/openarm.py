"""Arm operations for the dashboard: which controller has the arms, the changes between controllers, park, hold, release, and the teleop bridge.

ONE CONTROLLER FOR BOTH ARMS, chosen robot-wide (arm_handover.py, notes/design/arm-controller-handover.md): the ROS 2 stack, the gafro arm service, or none, and CONFLICT when
both run or it cannot be told. Nothing starts one at boot; the operator chooses (`use-ros2`, `use-gafro`), hands over (`to-ros2`, `to-gafro`) or releases (`release`). Every
button is named for what the arms do, and one the controller cannot do is refused with the reason (`arm_handover.unavailable`), never sent to the other stack.

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
import arm_handover  # noqa: E402

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
_op = {"running": False, "name": None, "started": 0.0, "rc": None, "out": "", "error": None, "steps": []}   # steps: a change of controller's, as it goes
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

#: The ROS 2 stack's half of the arm words, through `openarm-up`, used only while the ROS 2 stack has the arms. Starting and stopping it are changes of controller (HANDOVER_OPS).
ROS_OPS = {
    "park":     (lambda arm: _sudo([OPENARM_UP, "park", arm]), 180, "moves the arm to its stow pose"),
    # HOLD: stiff where it is. An active arm is driven to where it already is, which preempts a running trajectory; an inactive one is activated, which holds the measured pose (A1).
    "hold":     (lambda arm: _sudo([OPENARM_UP, "hold", arm]), 120, "stiff where it is"),
}

#: The changes of controller, both arms always (arm_handover.py): name -> (kind, target).
HANDOVER_OPS = {"use-ros2": ("use", "ros2"), "use-gafro": ("use", "gafro"), "to-ros2": ("handover", "ros2"), "to-gafro": ("handover", "gafro"),
                "release": ("release", None)}
#: Each arm word, as the action `arm_handover.unavailable` names it. `activate` is the old word for `ready`.
ACTION_OF = {"park": "park", "hold": "hold", "ready": "ready", "activate": "ready", "follow": "follow", "supervise": "supervise", "unsupervise": "supervise",
             "prepare": "prepare"}


#: The old stack's `ros2_control_node` as a process: the executable is the first word of the command line. Not `pgrep -f ros2_control_node`, which also finds another pgrep (this
#: one runs at every status poll, and the arm services' watch runs one a second: they found each other, and the service latched "the old stack is running").
ROS2_CONTROL_NODE = r"^([^ ]*/)?ros2_control_node( |$)"


def _ros_running():
    """Whether the old stack's `ros2_control_node` runs (its container's processes are visible to the host): True, False, or None when pgrep cannot tell."""
    try:
        r = _run(["pgrep", "-f", ROS2_CONTROL_NODE], 3)
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
                        "Stop one (`openarm-up down`, or `systemctl stop` the opendubs-arm units) before anything moves")
    if ros:
        return "ros", "the ROS 2 arm stack is running"
    if served:
        return "gafro", "the arm service is running for: " + ", ".join(served)
    return "none", "no controller has the arms: choose one on the Arms page (Use ROS 2, or Use gafro)"


#: what the arm service does, by the dashboard's word for it, and what it moves
SERVICE_OPS = {
    "park":     "moves the arm to its stow pose",
    "activate": "moves the arm to its ready pose",
    "hold":     "stops the arm where it is",
    # Starts the arm following pose targets (`<side>/target`): the hand goes where the target is, at the stage's speed limits. It refuses without a person at the e-stop (supervise), and
    # ends with `hold`, CH6 or the e-stop.
    "follow":   "starts following pose targets: the hand goes where the target is",
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
#: written: the full path and the `.service`. Only a change of controller starts or stops a unit, and for both arms (arm_handover.py): there is no per-arm START.
SYSTEMCTL = "/usr/bin/systemctl"
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


def _up_error(r):
    """The whole `openarm-up: ...` message from a failed run, not its last line: the diagnosis comes first and the fix last, and the last line alone read as a fragment."""
    lines = (r.stderr or "").strip().splitlines() or (r.stdout or "").strip().splitlines()
    start = max((i for i, l in enumerate(lines) if l.startswith("openarm-up:")), default=max(len(lines) - 1, 0))
    return "\n".join(lines[start:]).removeprefix("openarm-up: ") or f"returned {r.returncode}"


STOW_CONF = os.environ.get("OPENARM_STOW_CONF", "/etc/opendubs/openarm-stow.conf")
STOW_TOL_DEG = float(os.environ.get("OPENARM_STOW_TOL_DEG", "5"))     # openarm-up's at_stow tolerance
_JOINT_RE = __import__("re").compile(r"^\s*id ([1-7]) \(joint \d\).*?pos\s+([+-]?[\d.]+) deg")


def _stow_pose(side):
    """The side's stow pose, seven degrees, from the stow file openarm-up and the teleop read; None if it is not set."""
    try:
        with open(STOW_CONF) as f:
            lines = f.read().splitlines()
    except OSError:
        return None
    key = f"{side.upper()}_STOW"
    for line in reversed(lines):
        k, _, v = line.strip().partition("=")
        if k.strip() == key:
            try:
                pose = [float(x) for x in v.split(",")]
            except ValueError:
                return None
            return pose if len(pose) == 7 else None
    return None


def _measured(side, text):
    """Joints 1-7 of `side`, degrees, from `openarm-motors status`; None if any is missing."""
    cur, out = None, {}
    for line in text.splitlines():
        if line.startswith("arm_"):
            cur = line.split()[0]
            continue
        m = _JOINT_RE.match(line)
        if m and cur == f"arm_{side}":
            out[int(m[1])] = float(m[2])
    return [out[i] for i in range(1, 8)] if len(out) == 7 else None


class _HandoverIo:
    """What arm_handover.py may do to the robot, and nothing else: the same commands the buttons use, read fresh (no cache)."""

    def controller(self):
        return arm_handover.controller(active_stack()[0])

    def stack_why(self):
        return active_stack()[1]

    def gates(self):
        import choreo_proxy
        return choreo_proxy._robot_now()

    def show_running(self):
        return _show_running()

    def teleop_running(self):
        return _teleop_running()

    def gafro_sides(self):
        return [side for side in arm_client.SIDES if _socket_alive(side)]

    def gafro_supervised(self, side):
        return "supervised: yes" in (_service_status(side) or "").splitlines()

    def gafro_park(self, side):
        return _service_work("park", [side])[1]

    def gafro_hold(self, side):
        return _service_work("hold", [side])[1]

    def unit(self, verb, side):
        try:
            r = _run(_sudo([SYSTEMCTL, verb, _unit(side)]), 60)
        except Exception as e:                       # noqa: BLE001
            return f"{type(e).__name__}: {e}"
        return None if r.returncode == 0 else (r.stderr.strip() or f"systemctl {verb} returned {r.returncode}") + " (opendubs-arm.sudoers)"

    def start_gafro(self, side):
        return _start_unit(side)

    def ros(self, *args, timeout=180):
        try:
            r = _run(_sudo([OPENARM_UP, *args]), timeout)
        except subprocess.TimeoutExpired:
            return 1, f"timed out after {timeout} s"
        except Exception as e:                       # noqa: BLE001
            return 1, f"{type(e).__name__}: {e}"
        return r.returncode, (r.stdout or "")[-1500:] if r.returncode == 0 else _up_error(r)

    def ros_running(self):
        return _ros_running() is not False

    def at_stow(self, side):
        want = _stow_pose(side)
        try:
            r = _run([OPENARM_MOTORS, "status"], 20)
        except Exception:                            # noqa: BLE001
            return None
        cur = _measured(side, r.stdout or "")
        if want is None or cur is None:
            return None
        return all(abs(w - c) <= STOW_TOL_DEG for w, c in zip(want, cur))

    def limp(self, side):
        """Every motor of the arm answers and none is ENABLED: it is already limp (after a power loss, the motors restart disabled)."""
        try:
            r = _run([OPENARM_MOTORS, "status"], 20)
        except Exception:                            # noqa: BLE001
            return False
        rows, cur = [], None
        for line in (r.stdout or "").splitlines():
            if line.startswith("arm_"):
                cur = line.split()[0]
            elif cur == f"arm_{side}" and line.strip().startswith("id "):
                rows.append(line)
        return bool(rows) and all(" DISABLED " in f"{l} " for l in rows)

    def senders(self, side):
        try:
            r = _run([OPENARM_MOTORS, "senders", f"arm_{side}", "0.5"], 10)
        except Exception as e:                       # noqa: BLE001
            return False, f"could not listen: {e}"
        return r.returncode == 0, (r.stdout.strip() or r.stderr.strip())

    def disable(self, side):
        try:
            r = _run([OPENARM_MOTORS, "disable", f"arm_{side}"], 20)
        except Exception as e:                       # noqa: BLE001
            return False, f"{type(e).__name__}: {e}"
        return r.returncode == 0, (r.stdout.strip() or r.stderr.strip())


def controller_view(stack=None):
    """The controller for the page and the runner: {controller, label, unavailable: {action: reason or None}}, added to the `stack` field."""
    name, why = stack or active_stack()
    ctrl = arm_handover.controller(name)
    return {"controller": ctrl, "label": arm_handover.LABELS[ctrl], "unavailable": arm_handover.unavailable(ctrl, why)}


def _start_change(name):
    kind, target = HANDOVER_OPS[name]
    with _lock:
        if _op["running"]:
            return False, f"{_op['name']} is still running ({int(time.time() - _op['started'])}s)"
        _op.update({"running": True, "name": name, "started": time.time(), "rc": None, "out": "", "error": None, "steps": []})

    def publish(change):
        with _lock:
            _op["steps"] = [dict(s) for s in change.steps]

    def work():
        change = arm_handover.Change(_HandoverIo(), name)
        change.on_update = lambda: publish(change)
        try:
            out, error = arm_handover.run(change.io, kind, target, change)
        except Exception as e:                       # noqa: BLE001 - surfaced to the operator
            out, error = "", f"{type(e).__name__}: {e}"
        publish(change)
        with _lock:
            _op.update({"rc": 1 if error else 0, "out": out or "", "error": error, "running": False})

    threading.Thread(target=work, name=f"openarm-{name}", daemon=True).start()
    return True, name


def start_op(name, arm="both"):
    """Begin an arm operation. Returns (accepted, detail)."""
    if name not in OPS and name not in HANDOVER_OPS and name not in ACTION_OF and name not in ("viewer", "viewer-stop"):
        return False, f"unknown operation {name!r}"
    if arm not in ("left", "right", "both"):
        return False, f"arm must be left|right|both, got {arm!r}"
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

    use_service, spec, sides = False, OPS.get(name), []
    if name in HANDOVER_OPS or name in ACTION_OF:
        stack, why = active_stack()
        reason = arm_handover.unavailable(arm_handover.controller(stack), why)[name if name in HANDOVER_OPS else ACTION_OF[name]]
        if reason:
            return False, reason
        if name in HANDOVER_OPS:
            if arm != "both":
                return False, "a change of controller takes both arms: one controller for both"
            return _start_change(name)
        if name == "ready":
            name = "activate"                        # the arm service's word for it
        if stack == "ros":
            spec = ROS_OPS[name]
        else:
            use_service = True
            if name == "prepare" and (show := _show_running()):
                return False, f"a show is running ({show}): preparing the arms moves them, and waits until it ends"
            # Only arms gafro already has: an arm without its service is not started here (that would be a per-arm choice of controller).
            served = arm_client.sides_served()
            sides = served if arm == "both" else [arm]
            if not sides or any(side not in served for side in sides):
                return False, f"no arm service for the {arm} arm (Use gafro starts both). Running: {', '.join(served) or 'none'}"

    if name in INSTANT_OPS and use_service:
        out, error = _service_work(name, sides)
        return (False, error) if error else (True, out)
    with _lock:
        if _op["running"]:
            # Deliberately a refusal rather than a queue: a second arm command arriving mid-motion
            # is a collision. Same reasoning as the lease model's select-one-with-handoff.
            return False, f"{_op['name']} is still running ({int(time.time() - _op['started'])}s)"
        _op.update({"running": True, "name": f"{name} {arm}".strip(), "started": time.time(),
                    "rc": None, "out": "", "error": None, "steps": []})

    def work():
        if use_service:
            try:
                out, error = _prepare_work(sides) if name == "prepare" else _service_work(name, sides)
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
                _op["out"] = ((r.stdout or "") + (r.stderr or ""))[-4000:]
                if r.returncode != 0 and name != "doctor":
                    _op["error"] = _up_error(r)
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
    if verb == "start":                              # STOP always works: it only takes a source away
        stack, why = active_stack()
        reason = arm_handover.unavailable(arm_handover.controller(stack), why)["teleop"]
        if reason:
            return False, reason
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
    stack = active_stack()
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
        # Which arm stack answers (name: gafro | ros | none | both | unknown), and the controller that has the arms (controller: gafro | ros2 | none | conflict, its
        # label, and per button the reason it is off). The Arms and Shows pages show it from here.
        "stack": {"name": stack[0], "why": stack[1], **controller_view(stack)},
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
