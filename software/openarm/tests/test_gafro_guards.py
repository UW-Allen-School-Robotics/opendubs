#!/usr/bin/env python3
"""The guards the gafro path runs before it commands an arm (software/openarm/gafro/guards.py): another controller on the bus, CH5 and the
e-stop, and where a move may start. Each fails closed. Off the robot: the bus is a socket pair carrying real CAN frames, the dashboard a local
HTTP server, and one case runs engage.py to see it refuse before it creates a RobotInterface (skipped without gafro).
Run: python3 software/openarm/tests/test_gafro_guards.py
"""
import http.server
import importlib.util
import json
import os
import socket
import struct
import subprocess
import sys
import tempfile
import threading

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
GAFRO = os.path.join(HERE, "..", "gafro")
sys.path.insert(0, GAFRO)

import guards  # noqa: E402

fails = 0


def check(name, got, want):
    global fails
    ok = got == want
    fails += not ok
    print(("ok   " if ok else "FAIL ") + name + ("" if ok else f": got {got!r}, want {want!r}"))


def refuses(fn, *words):
    """The message of the Refused that fn raises, or None if it did not refuse; with words, whether the message names them all."""
    try:
        fn()
    except guards.Refused as refused:
        return all(w in str(refused) for w in words) if words else True
    return None


def frame(can_id, extended=False, data=b"\x00" * 8, fd=False):
    can_id |= guards.CAN_EFF_FLAG if extended else 0
    if fd:
        return struct.pack("=IBBBB", can_id, len(data), 0, 0, 0) + data.ljust(64, b"\x00")
    return struct.pack("=IB3x", can_id, len(data)) + data.ljust(8, b"\x00")


def bus_with(*frames):
    """An `open_socket` for foreign_commands that yields `frames` from a datagram socket pair (selectable, frame boundaries kept)."""
    reader, writer = socket.socketpair(socket.AF_UNIX, socket.SOCK_DGRAM)
    for f in frames:
        writer.send(f)
    return lambda interface: reader


# -- another controller ---------------------------------------------------------------------------------------------------------------
class Result:
    def __init__(self, stdout):
        self.stdout = stdout


own = os.getpid()
check("a running ros2_control_node is found", guards.stack_running(run=lambda *a, **k: Result("4242 ros2_control_node --ros-args\n")),
      ["4242 ros2_control_node --ros-args"])
check("this process is not the stack, even if its command line names it",
      guards.stack_running(run=lambda *a, **k: Result(f"{own} python ros2_control_node\n")), [])
check("nothing running", guards.stack_running(run=lambda *a, **k: Result("")), [])

check("a command frame (0x003) from someone else is seen", guards.foreign_commands("x", 0.3, bus_with(frame(0x003))), [3])
check("a CAN FD command frame is seen too", guards.foreign_commands("x", 0.3, bus_with(frame(0x008, fd=True))), [8])
check("the motors' replies (0x013) are not commands", guards.foreign_commands("x", 0.3, bus_with(frame(0x013), frame(0x018))), [])
REFRESH, READ_REG, WRITE_REG, SAVE_REGS = (bytes([3, 0, f, 0, 0, 0, 0, 0]) for f in (0xCC, 0x33, 0x55, 0xAA))
check("a read-only status refresh (0x7FF, 0xCC) is not a command", guards.foreign_commands("x", 0.3, bus_with(frame(0x7FF, data=REFRESH))), [])
check("a register read (0x7FF, 0x33) is not a command", guards.foreign_commands("x", 0.3, bus_with(frame(0x7FF, data=READ_REG))), [])
check("a register write (0x7FF, 0x55) is one", guards.foreign_commands("x", 0.3, bus_with(frame(0x7FF, data=WRITE_REG))), [0x7FF])
check("so is saving the registers (0x7FF, 0xAA)", guards.foreign_commands("x", 0.3, bus_with(frame(0x7FF, data=SAVE_REGS))), [0x7FF])
check("a register write as a CAN FD frame is seen", guards.foreign_commands("x", 0.3, bus_with(frame(0x7FF, data=WRITE_REG, fd=True))), [0x7FF])
check("a poller's burst, requests and replies, is not a second controller",
      guards.foreign_commands("x", 0.3, bus_with(*[f for m in range(1, 9) for f in (frame(0x7FF, data=bytes([m, 0, 0xCC, 0, 0, 0, 0, 0])), frame(0x10 + m))])), [])
check("a poller that also commands one motor is seen by the command",
      guards.foreign_commands("x", 0.3, bus_with(frame(0x7FF, data=REFRESH), frame(0x013), frame(0x004))), [4])
check("an extended 0x7FF write is not a register write", guards.foreign_commands("x", 0.3, bus_with(frame(0x7FF, extended=True, data=WRITE_REG))), [])
check("a short 0x7FF frame is not mistaken for one", guards.foreign_commands("x", 0.3, bus_with(frame(0x7FF, data=b"\x03\x00"))), [])
check("an id outside 1 to 8 is not a command", guards.foreign_commands("x", 0.3, bus_with(frame(0x009), frame(0x000))), [])
check("a quiet bus", guards.foreign_commands("x", 0.2, bus_with()), [])


def cannot_open(interface):
    raise OSError(19, "No such device")


check("an interface that cannot be listened to is a refusal", refuses(lambda: guards.foreign_commands("arm_left", 0.2, cannot_open), "arm_left", "ruled out"), True)

check("the stack running refuses", refuses(lambda: guards.require_exclusive(["a"], stack=lambda: ["4242 ros2_control_node"], listen=lambda i, s: []),
                                           "existing arm stack", "4242"), True)
check("another sender refuses and names the interface and the id",
      refuses(lambda: guards.require_exclusive(["arm_left"], stack=lambda: [], listen=lambda i, s: [3]), "arm_left", "0x003"), True)
check("a register write refuses, and names 0x7ff", refuses(lambda: guards.require_exclusive(["arm_left"], stack=lambda: [], listen=lambda i, s: [0x7FF]),
                                                              "arm_left", "0x7ff"), True)
check("the default listen is half a second", guards.LISTEN_SECONDS, 0.5)
check("a bus of its own passes", guards.require_exclusive(["arm_left", "arm_right"], stack=lambda: [], listen=lambda i, s: []), None)

# -- CH5, the e-stop, and a person -----------------------------------------------------------------------------------------------------
def state(**fields):
    return lambda url: {"ch5": "AUTO", "estop": False, **fields}


check("not supervised refuses, whatever CH5 says", refuses(lambda: guards.require_motion_permitted(False, state=state()), "--supervised"), True)
check("MANUAL, e-stop clear, supervised: allowed", guards.require_motion_permitted(True, state=state(ch5="MANUAL")), None)
check("AUTO, e-stop clear, supervised: allowed", guards.require_motion_permitted(True, state=state()), None)
check("STANDBY (OFF) does not move an arm", refuses(lambda: guards.require_motion_permitted(True, state=state(ch5="STANDBY")), "STANDBY"), True)
check("an unknown CH5 refuses", refuses(lambda: guards.require_motion_permitted(True, state=state(ch5=None)), "unknown"), True)
check("an engaged e-stop refuses", refuses(lambda: guards.require_motion_permitted(True, state=state(estop=True)), "engaged"), True)
check("an unknown e-stop refuses", refuses(lambda: guards.require_motion_permitted(True, state=state(estop=None)), "unknown"), True)


def broken(url):
    raise TimeoutError("no answer")


check("a dashboard that does not answer refuses", refuses(lambda: guards.require_motion_permitted(True, state=broken), "did not answer"), True)


class Dashboard(http.server.BaseHTTPRequestHandler):
    reply = {"ch5": "MANUAL", "estop": False}

    def do_GET(self):
        body = json.dumps(self.reply).encode()
        self.send_response(200)
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *a):
        pass


server = http.server.HTTPServer(("127.0.0.1", 0), Dashboard)
threading.Thread(target=server.serve_forever, daemon=True).start()
url = f"http://127.0.0.1:{server.server_address[1]}/api/choreo/robot"
check("the real HTTP path reads CH5 and the e-stop", guards.require_motion_permitted(True, url), None)
Dashboard.reply = {"ch5": "STANDBY", "estop": False}
check("... and refuses on STANDBY", refuses(lambda: guards.require_motion_permitted(True, url), "STANDBY"), True)
server.shutdown()
check("... and on a dashboard that is not there", refuses(lambda: guards.require_motion_permitted(True, url), "did not answer"), True)

# -- where a move may start ------------------------------------------------------------------------------------------------------------
stow, ready = np.zeros(7), np.array([0.0, 0.7, -0.5, 1.8, -1.0, -0.1, -0.55])
known = {"stow": stow, "ready": ready}
check("at stow", guards.require_known_start(stow + 0.01, known), "stow")
check("near ready", guards.require_known_start(ready - 0.05, known), "ready")
check("the tolerance is per joint", refuses(lambda: guards.require_known_start(stow + np.array([0, 0, 0, 0.2, 0, 0, 0]), known), "stow or ready"), True)
check("halfway between is not known", refuses(lambda: guards.require_known_start((stow + ready) / 2, known), "head"), True)
check("no known pose refuses", refuses(lambda: guards.require_known_start(stow, {}), "none known"), True)

# -- the hardware config ---------------------------------------------------------------------------------------------------------------
with tempfile.TemporaryDirectory() as directory:
    def config(text):
        path = os.path.join(directory, "hw.yaml")
        with open(path, "w") as handle:
            handle.write(text)
        return path

    check("a real bus: its interface, and real", guards.buses_of(config("left_arm: {driver: openarm, bus: can, can_interface: arm_left}\n"), ["left_arm"]),
          (["arm_left"], True))
    check("bus defaults to can", guards.buses_of(config("left_arm: {driver: openarm, can_interface: arm_left}\n"), ["left_arm"]), (["arm_left"], True))
    check("a fake bus is not real", guards.buses_of(config("right_arm: {driver: openarm, bus: fake}\n"), ["right_arm"]), ([], False))
    check("a group the config lacks refuses", refuses(lambda: guards.buses_of(config("left_arm: {bus: fake}\n"), ["right_arm"]), "right_arm"), True)
    check("a can bus with no interface refuses", refuses(lambda: guards.buses_of(config("left_arm: {bus: can}\n"), ["left_arm"]), "can_interface"), True)
    check("a missing file refuses", refuses(lambda: guards.buses_of(os.path.join(directory, "nope.yaml"), ["left_arm"]), "cannot read"), True)

    # engage.py refuses before it creates a RobotInterface: no motor is enabled by a refused run.
    if importlib.util.find_spec("gafro") is not None and importlib.util.find_spec("gafro_robot_interface") is not None:
        hardware = config("right_arm: {driver: openarm, bus: can, can_interface: no_such_can0}\n")
        run = subprocess.run([sys.executable, os.path.join(GAFRO, "engage.py"), "--model", "unused.xml", "--side", "right", "--backend", "hardware",
                              "--hardware-config", hardware, "--listen-seconds", "0.2"], capture_output=True, text=True, timeout=120)
        check("engage.py on a real bus refuses before enabling anything", (run.returncode, "refused:" in run.stdout, "Traceback" in run.stderr), (1, True, False))
        check("... and says why", "no_such_can0" in run.stdout or "existing arm stack" in run.stdout, True)
    else:
        print("skip engage.py end to end: gafro is not importable here")

# The permit is renewed only while CH5 and the e-stop still allow motion: a sequence in progress is cut short, not finished.
import time  # noqa: E402

import engage  # noqa: E402

live = {"ch5": "MANUAL", "estop": False}
check("permitted: CH5 MANUAL, e-stop clear", guards.motion_still_permitted(state=lambda url: live), True)
check("permitted: CH5 AUTO", guards.motion_still_permitted(state=lambda url: {"ch5": "AUTO", "estop": False}), True)
check("not permitted: CH5 OFF", guards.motion_still_permitted(state=lambda url: {"ch5": "OFF", "estop": False}), False)
check("not permitted: e-stop engaged", guards.motion_still_permitted(state=lambda url: {"ch5": "MANUAL", "estop": True}), False)
check("not permitted: e-stop null", guards.motion_still_permitted(state=lambda url: {"ch5": "MANUAL", "estop": None}), False)
check("not permitted: no ch5", guards.motion_still_permitted(state=lambda url: {"estop": False}), False)
check("not permitted: null answer", guards.motion_still_permitted(state=lambda url: None), False)


def unreachable(url):
    raise OSError("no dashboard")


check("not permitted: dashboard unreachable", guards.motion_still_permitted(state=unreachable), False)

granted, revoked, gate = [], [], {"open": True}
keeper = engage.PermitKeeper(("right_arm",), period=0.01, allowed=lambda: gate["open"],
                             grant=lambda group, lease: granted.append(group), revoke=revoked.append)
with keeper:
    time.sleep(0.1)
    before = len(granted)
    gate["open"] = False
    time.sleep(0.1)
    settled = len(granted)
    time.sleep(0.1)
check("the keeper renews while allowed", before >= 3, True)
check("the keeper stops renewing once the gate closes", settled - before <= 2 and len(granted) == settled, True)
check("... and counts the withheld renewals", keeper.withheld >= 5, True)
check("... and revokes on exit", revoked, ["right_arm"])


def raising():
    raise RuntimeError("gate broke")


granted.clear()
check("a gate that raises withholds", refuses(lambda: engage.PermitKeeper(("right_arm",), allowed=raising, grant=lambda g, l: granted.append(g)).start(),
                                              "no permit was granted"), True)
check("... and nothing was granted", granted, [])
check("no gate: renews as before", (lambda k: (k.start(), time.sleep(0.05), k.stop(), len(granted) > 0)[-1])(
    engage.PermitKeeper(("right_arm",), period=0.01, grant=lambda g, l: granted.append(g), revoke=lambda g: None)), True)

print(f"\n{fails} failure(s)" if fails else "\nall guards pass")
sys.exit(1 if fails else 0)
