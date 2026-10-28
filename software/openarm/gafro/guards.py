"""What the gafro path checks before it commands an arm, shared by engage.py and bringup.py.

Nothing here talks to a motor. Each guard fails closed: a question it cannot answer is a refusal.

  stack_running / foreign_commands   another controller on the arms' bus. SocketCAN lets any number of processes send, so nothing
                                     stops two controllers commanding the same motors: the existing stack's `ros2_control_node` is
                                     looked for, and the bus is listened to, passively, for what only a controller or a tool that
                                     changes the motors sends: command frames (MIT, enable, disable) to ids 0x001 to 0x008, and
                                     parameter writes (0x7FF with function 0x55 or 0xAA). A read-only status refresh (0x7FF, 0xCC) is
                                     not one: something on opendubs2 polls both arms with it every 2.2 s, and it asks, it does not
                                     command. Its replies (0x011 to 0x018) are not looked at either.
  require_own_arm                    the config's real buses are exactly this arm's (`arm_<side>`): RobotInterface.initialize() enables every real group in
                                     the config, so a left unit given a config whose only real bus is the right arm's would command the right arm.
  link_problem / conflict            the arm's CAN link is up and not BUS-OFF, and the old stack has not started since this service did.
  require_motion_permitted           CH5 permits motion (MANUAL or AUTO, DEC-21), the e-stop is clear, and a person has said, with
                                     --supervised, that they are at the e-stop. The state is the dashboard's read-only
                                     /api/choreo/robot, the one the runner reads; an unanswered or unknown state is a refusal.
  require_known_start                the straight line to the target is checked against a model with no head, neck or lift, so a move
                                     starts only near stow or ready, where the line is known to be clear.

They are for the days before the arm service exists. The service owns presence, leases and the deadman and replaces the second and third.
"""
from __future__ import annotations

import json
import os
import select
import socket
import struct
import subprocess
import time
import urllib.request

import numpy as np

DASHBOARD = "http://127.0.0.1:8080/api/choreo/robot"
MOTION_CH5 = ("MANUAL", "AUTO")                 # OFF (STANDBY) is hold and status only: nothing that moves an arm (DEC-21)
COMMAND_IDS = range(0x001, 0x009)               # the frames to the arm's motors 1 to 8: MIT commands, and enable and disable
REGISTER_ID = 0x7FF                             # the frames that read, write or save a motor's registers, and ask for its status: id in the data
REGISTER_WRITES = (0x55, 0xAA)                  # in data[2]: write a register, save the registers. 0x33 reads one and 0xCC asks for the status
STACK_PROCESSES = ("ros2_control_node",)
LISTEN_SECONDS = 0.5
START_TOLERANCE = np.radians(5.0)               # per joint, as `openarm-up` takes an arm to be at its stow pose

CAN_EFF_FLAG = 0x80000000
CAN_FRAME_BYTES = 72                            # a CAN FD frame; a classic one is the first 16


class Refused(RuntimeError):
    """A guard said no: the message says why, and nothing was commanded."""


# -- another controller on the bus ---------------------------------------------------------------------------------------------------

def stack_running(names=STACK_PROCESSES, run=subprocess.run) -> list[str]:
    """The command lines of any process matching `names`, other than this one. The arm stack runs in a container, whose processes the host sees."""
    found = []
    for name in names:
        result = run(["pgrep", "-af", name], capture_output=True, text=True)
        for line in result.stdout.splitlines():
            pid, _, command = line.partition(" ")
            if pid.isdigit() and int(pid) != os.getpid():
                found.append(f"{pid} {command[:100]}")
    return found


def _open_can(interface: str):
    sock = socket.socket(socket.AF_CAN, socket.SOCK_RAW, socket.CAN_RAW)
    sock.setsockopt(socket.SOL_CAN_RAW, socket.CAN_RAW_FD_FRAMES, 1)
    sock.bind((interface,))
    return sock


def _commands(can_id: int, data: bytes) -> bool:
    """Whether a standard frame is one that commands a motor or changes its registers, as opposed to one that only asks it something."""
    if can_id in COMMAND_IDS:
        return True
    return can_id == REGISTER_ID and len(data) > 2 and data[2] in REGISTER_WRITES


def foreign_commands(interface: str, seconds: float = LISTEN_SECONDS, open_socket=_open_can, clock=time.monotonic) -> list[int]:
    """The ids seen on `interface` in `seconds` that command a motor: frames to ids 0x001 to 0x008 (MIT, enable, disable), and 0x7FF frames
    that write or save a register. Someone is already commanding the arm's motors.

    Passive: the socket only reads, and the driver is not running while this is called. A status refresh (0x7FF, 0xCC) and the motors'
    replies are not commands and are not reported: a read-only poll of the motors is common (the dashboard and the lane's tools do it) and
    puts nothing on the motors that a controller of ours could be contradicting.
    """
    try:
        sock = open_socket(interface)
    except OSError as error:
        raise Refused(f"cannot listen on {interface} ({error}), so another sender cannot be ruled out") from error

    seen = set()
    try:
        end = clock() + seconds
        while (left := end - clock()) > 0:
            if not select.select([sock], [], [], left)[0]:
                break
            frame = sock.recv(CAN_FRAME_BYTES)
            if len(frame) < 4:
                continue
            (can_id,) = struct.unpack_from("=I", frame)
            if not can_id & CAN_EFF_FLAG and _commands(can_id & 0x7FF, frame[8:16]):
                seen.add(can_id & 0x7FF)
    except OSError as error:
        raise Refused(f"reading {interface} failed ({error}), so another sender cannot be ruled out") from error
    finally:
        sock.close()
    return sorted(seen)


def require_exclusive(interfaces, seconds: float = LISTEN_SECONDS, stack=stack_running, listen=foreign_commands) -> None:
    """Refuse if the existing stack is running, or any other sender is commanding the motors on `interfaces`."""
    running = stack()
    if running:
        raise Refused("the existing arm stack is running (" + "; ".join(running) + "): two controllers on the same motors. "
                      "Bring it down first (openarm-up down, after park).")
    for interface in interfaces:
        ids = listen(interface, seconds)
        if ids:
            raise Refused(f"another sender is commanding the motors on {interface} (command or register-write frames, ids "
                          + ", ".join(f"0x{i:03x}" for i in ids) + ").")


def buses_of(config_path: str, groups) -> tuple[list[str], bool]:
    """The CAN interfaces the control groups in the hardware config use, and whether any of them is a real bus (a `fake` bus is the simulator)."""
    import yaml

    try:
        with open(config_path) as handle:
            config = yaml.safe_load(handle) or {}
    except OSError as error:
        raise Refused(f"cannot read the hardware config {config_path!r} ({error})") from error

    interfaces, real = [], False
    for group in groups:
        entry = config.get(group)
        if not isinstance(entry, dict):
            raise Refused(f"the hardware config {config_path} has no {group}")
        if entry.get("bus", "can") == "can":
            real = True
            if not entry.get("can_interface"):
                raise Refused(f"{group} in {config_path} has no can_interface")
            interfaces.append(entry["can_interface"])
    return interfaces, real


def real_buses(config_path: str) -> dict:
    """{group: can_interface} for every group in the hardware config on a real bus (a group is a top-level entry with a `driver`)."""
    import yaml

    try:
        with open(config_path) as handle:
            config = yaml.safe_load(handle) or {}
    except OSError as error:
        raise Refused(f"cannot read the hardware config {config_path!r} ({error})") from error
    return {group: entry.get("can_interface") for group, entry in config.items()
            if isinstance(entry, dict) and "driver" in entry and entry.get("bus", "can") == "can"}


def require_own_arm(config_path: str, side: str) -> None:
    """Refuse unless the config's real buses are exactly the `side` arm's own (`arm_<side>`, the udev name). `initialize()` enables every real group in the config, so the
    instance, `--side` and the one real bus must all name the same arm; a config with no real bus is the simulator and passes."""
    real = real_buses(config_path)
    if not real:
        return
    own, interface = f"{side}_arm", f"arm_{side}"
    others = sorted(f"{group} ({iface})" for group, iface in real.items() if group != own)
    if own not in real:
        raise Refused(f"this is the {side} arm's service, but {config_path} has no real bus for {own}; its real bus(es): {', '.join(others)}. "
                      f"Starting would command the other arm.")
    if real[own] != interface:
        raise Refused(f"{own} in {config_path} is on {real[own]!r}, not {interface!r}: the {side} service must not command another arm's motors.")
    if others:
        raise Refused(f"{config_path} also has real buses for {', '.join(others)}: initialize() would enable them from the {side} service. "
                      f"One config per arm, with the other arm's bus: fake.")


def link_problem(interface: str, run=subprocess.run) -> str | None:
    """Why `interface` cannot carry the arm's frames (down, BUS-OFF, no carrier, or unreadable), or None. ERROR-PASSIVE is not one: the left shoulders sit there on
    their lost replies and the arm still works. Nothing restarts a link under the service (it has no privilege to), so a BUS-OFF link is reported, not healed."""
    try:
        result = run(["ip", "-details", "-json", "link", "show", interface], capture_output=True, text=True, timeout=2)
        link = json.loads(result.stdout)[0]
    except (OSError, ValueError, IndexError, subprocess.SubprocessError) as error:
        return f"link off: {interface} cannot be read ({type(error).__name__})"
    flags = set(link.get("flags", []))
    state = (link.get("linkinfo", {}).get("info_data", {}) or {}).get("state")
    if "UP" not in flags:
        return f"link off: {interface} is down"
    if state in ("BUS-OFF", "STOPPED"):
        return f"link off: {interface} is {state}"
    if "NO-CARRIER" in flags or "LOWER_UP" not in flags:
        return f"link off: {interface} has no carrier"
    return None


def conflict(interfaces, stack=stack_running, link=link_problem) -> str | None:
    """What makes a running service stop moving its arm: the old stack's `ros2_control_node` has started since, or an arm's link is off. None if neither."""
    running = stack()
    if running:
        return "the existing arm stack is running (" + "; ".join(running) + ")"
    for interface in interfaces:
        why = link(interface)
        if why:
            return why
    return None


DRIVER_DEFAULT_SPEED = 1.0                      # the openarm driver's `max_velocity` when the config does not name one


def driver_speed_warnings(config_path: str, groups, wanted: float) -> list[str]:
    """What to say at start about the driver's speed: each real group of `groups` whose `max_velocity` (default 1.0) is below `wanted`, the speed the service accepts a path at. A
    path faster than the driver follows is one the arm lags and the tracking stop ends, so this is a warning in the log, not a refusal: the arm stops safely, the show does not play."""
    import yaml

    try:
        with open(config_path) as handle:
            config = yaml.safe_load(handle) or {}
    except OSError:
        return []
    out = []
    for group in groups:
        entry = config.get(group)
        if isinstance(entry, dict) and entry.get("bus", "can") == "can":
            speed = float(entry.get("max_velocity", DRIVER_DEFAULT_SPEED))
            if speed < wanted:
                out.append(f"{group}: the driver's max_velocity is {speed:g} rad/s"
                           f"{'' if 'max_velocity' in entry else ' (not set: its default)'}, below the {wanted:g} rad/s the service accepts a path at: "
                           "a fast show will lag and stop on the tracking error")
    return out


# -- the authority layer's state -----------------------------------------------------------------------------------------------------

def robot_state(url: str = DASHBOARD, timeout: float = 2.0, opener=urllib.request.urlopen) -> dict:
    with opener(url, timeout=timeout) as response:
        return json.loads(response.read())


def _deadman_refusal(robot: dict) -> str | None:
    """Why the deadman (CH6) does not allow motion, or None if it is held. Only a held deadman is a yes: released, null and absent are all no."""
    deadman = robot.get("deadman")
    if deadman is True:
        return None
    return (f"CH6 (the deadman) is {'released' if deadman is False else 'unknown'}: hold it for the whole move. The arms do not move without it, and "
            "letting go stops the move within the 0.5 s lease.")


def require_deadman(url: str = DASHBOARD, state=robot_state) -> None:
    """Refuse unless CH6 is held right now. Normal operation does not put the arms on the deadman (docs/interfaces/command-telemetry.md); the first runs do,
    as a hold to run: it is asked once the move is confirmed, before the first permit is granted, and again before every renewal (motion_still_permitted)."""
    try:
        robot = state(url)
    except Exception as error:  # noqa: BLE001 -- whatever the dashboard did, its answer is unknown
        raise Refused(f"the dashboard did not answer at {url} ({type(error).__name__}), so CH6 is unknown.") from error

    why = _deadman_refusal(robot if isinstance(robot, dict) else {})
    if why:
        raise Refused(why)


def require_motion_permitted(supervised: bool, url: str = DASHBOARD, state=robot_state, deadman: bool = False, own_move: bool = True) -> None:
    """Refuse a move unless a person has said they are at the e-stop, CH5 is MANUAL or AUTO, and the e-stop is clear; with `deadman`, and CH6 is held.
    `own_move` is a move the service plans itself (activate, park, follow): only those assume the head is still and the neck centred. A runner's goal is swept in the twin
    with its own head motion, so its refusal does not ask for head tracking OFF."""
    if not supervised:
        if not own_move:
            raise Refused("a move needs --supervised: a person at the e-stop and the arm supported or clear.")
        raise Refused("a move needs --supervised: a person at the e-stop, the arm supported or clear, head tracking OFF and the neck centred "
                      "(the head moves on its own and the twin and the stow check assume it does not; the arm service that would check is not built).")
    try:
        robot = state(url)
    except Exception as error:  # noqa: BLE001 -- whatever the dashboard did, its answer is unknown
        raise Refused(f"the dashboard did not answer at {url} ({type(error).__name__}), so CH5 is unknown.") from error

    ch5, estop = robot.get("ch5"), robot.get("estop")
    if ch5 not in MOTION_CH5:
        raise Refused(f"CH5 is {ch5 or 'unknown'}, not MANUAL or AUTO: the arms do not move.")
    if estop is not False:
        raise Refused("the e-stop is " + ("engaged" if estop else "unknown") + ".")
    if deadman and (why := _deadman_refusal(robot)):
        raise Refused(why)


def motion_still_permitted(url: str = DASHBOARD, state=robot_state) -> bool:
    """Whether CH5 permits motion, the e-stop is clear and the deadman (CH6) is held, right now. Anything else is False: CH5 off, the e-stop engaged or unknown,
    the deadman released or unknown, and a dashboard that does not answer or answers something the guard cannot read (stale or null counts as stop)."""
    try:
        robot = state(url)
    except Exception:  # noqa: BLE001 -- whatever the dashboard did, its answer is unknown
        return False
    return (isinstance(robot, dict) and robot.get("ch5") in MOTION_CH5 and robot.get("estop") is False
            and _deadman_refusal(robot) is None)


def motion_gate(url: str = DASHBOARD, timeout: float = 0.2):
    """What a PermitKeeper asks before each renewal. A short timeout, so that a slow dashboard withholds a renewal (the arm then holds within the lease)
    instead of delaying the next one past it."""
    return lambda: motion_still_permitted(url, state=lambda u: robot_state(u, timeout=timeout))


# -- where a move may start ----------------------------------------------------------------------------------------------------------

def require_known_start(start, known: dict, tolerance: float = START_TOLERANCE) -> str:
    """The name of the pose in `known` that `start` is within `tolerance` of, per joint; refuses if there is none."""
    start = np.asarray(start, dtype=float)
    nearest = None
    for name, pose in known.items():
        error = float(np.abs(start - np.asarray(pose, dtype=float)).max())
        if error <= tolerance:
            return name
        nearest = error if nearest is None else min(nearest, error)

    raise Refused("the arm is not near " + " or ".join(known) + " (nearest "
                  + ("none known" if nearest is None else f"{np.degrees(nearest):.1f} deg off") + "). The check of the line to the target "
                  "has no head, neck or lift in its model, so a move starts only where the line is known to be clear.")
