"""What the gafro path checks before it commands an arm, shared by engage.py and bringup.py.

Nothing here talks to a motor. Each guard fails closed: a question it cannot answer is a refusal.

  stack_running / foreign_commands   another controller on the arms' bus. SocketCAN lets any number of processes send, so nothing
                                     stops two controllers commanding the same motors: the existing stack's `ros2_control_node` is
                                     looked for, and the bus is listened to, passively, for MIT command frames from anyone else.
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
COMMAND_IDS = range(0x001, 0x009)               # the MIT command frames to the arm's motors 1 to 8; their replies are on 0x011 and up
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


def foreign_commands(interface: str, seconds: float = LISTEN_SECONDS, open_socket=_open_can, clock=time.monotonic) -> list[int]:
    """The command ids (0x001 to 0x008) seen on `interface` in `seconds`: someone is already commanding the arm's motors.

    Passive: the socket only reads. Frames this process sent are not delivered back to it, and the driver is not running while this is called.
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
            if not can_id & CAN_EFF_FLAG and (can_id & 0x7FF) in COMMAND_IDS:
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
            raise Refused(f"another sender is commanding the motors on {interface} (MIT command frames, ids "
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


# -- the authority layer's state -----------------------------------------------------------------------------------------------------

def robot_state(url: str = DASHBOARD, timeout: float = 2.0, opener=urllib.request.urlopen) -> dict:
    with opener(url, timeout=timeout) as response:
        return json.loads(response.read())


def require_motion_permitted(supervised: bool, url: str = DASHBOARD, state=robot_state) -> None:
    """Refuse a move unless a person has said they are at the e-stop, CH5 is MANUAL or AUTO, and the e-stop is clear."""
    if not supervised:
        raise Refused("a move needs --supervised: a person at the e-stop, the arm supported or clear (the arm service that would "
                      "check presence is not built).")
    try:
        robot = state(url)
    except Exception as error:  # noqa: BLE001 -- whatever the dashboard did, its answer is unknown
        raise Refused(f"the dashboard did not answer at {url} ({type(error).__name__}), so CH5 is unknown.") from error

    ch5, estop = robot.get("ch5"), robot.get("estop")
    if ch5 not in MOTION_CH5:
        raise Refused(f"CH5 is {ch5 or 'unknown'}, not MANUAL or AUTO: the arms do not move.")
    if estop is not False:
        raise Refused("the e-stop is " + ("engaged" if estop else "unknown") + ".")


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
