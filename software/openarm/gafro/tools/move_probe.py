#!/usr/bin/env python3
"""bringup.py, unchanged, with one addition: once the move is confirmed, print one short line every 0.5 s of the moved arm's own driver status
(level, message, the permit time left) and the dashboard's deadman. Nothing is printed before the confirmation, so it does not get in the way of typing.

Every gate of bringup.py still applies (exclusive bus, CH5, the e-stop, --supervised, the clearance check, the typed-back confirmation, the deadman).
The move waits at most 8 s per phase (not 40). Arguments are bringup.py's:

    python move_probe.py move --side right --model <xml> --hardware-config <yaml> --supervised --joint 3 --delta +0.05 --assets <assets>

"""
import os
import sys
import threading
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, ".."))      # bringup.py, engage.py, guards.py, motion_plan.py next door
sys.path.insert(0, HERE)

import bringup  # noqa: E402
import guards  # noqa: E402

bringup.MOVE_TIMEOUT = 8.0
SHOW_FOR = 14.0                      # s, after the confirmation
confirmed = threading.Event()
real_confirm = bringup.motion_plan.confirm
real_connect = bringup.connect


def confirm(*args, **kwargs):
    real_confirm(*args, **kwargs)    # raises Refused when the operator says no: then nothing is printed and nothing starts
    confirmed.set()


def deadman():
    try:
        return guards.robot_state(timeout=0.5).get("deadman")
    except Exception as error:  # noqa: BLE001
        return f"?{type(error).__name__}"


def watch_loop(robot, component, position, joint, began_event=confirmed, show_for=SHOW_FOR, sleep=time.sleep):
    """Once confirmed, one line per 0.5 s: the moved arm's driver status, the permit time left, the joint's angle and the deadman."""
    began_event.wait()
    began = time.time()
    while time.time() - began < show_for:
        try:
            for status in robot.get_diagnostics():
                if status.get("component") == component:
                    values = status.get("values", {}) or {}
                    print(f"   [+{time.time() - began:4.1f}s] {component}: {status.get('level')} '{status.get('message')}'  "
                          f"permit_remaining={float(values.get('permit_remaining', 0)):.2f}  joint {joint} at {position()[joint - 1]:+.4f}  "
                          f"deadman={deadman()}", flush=True)
        except Exception as error:  # noqa: BLE001
            print(f"   [driver] diagnostics failed: {error!r}", flush=True)
        sleep(0.5)


def connect(args):
    robot, indices, position = real_connect(args)
    threading.Thread(target=watch_loop, args=(robot, f"{args.side}_arm", position, args.joint), daemon=True).start()
    return robot, indices, position


if __name__ == "__main__":
    bringup.motion_plan.confirm = confirm
    bringup.connect = connect
    sys.exit(bringup.main())
