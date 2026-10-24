#!/usr/bin/env python3
"""engage.py, unchanged, plus a print every 0.5 s of what the arm is doing while it runs: the seven joint angles, their measured torques, the permit time left,
the driver's status and the dashboard's deadman. Every gate of engage.py still applies (exclusive bus, CH5, e-stop, --supervised, deadman at launch, the start
tolerance, the collision line). Arguments are engage.py's, e.g.

    python engage_probe.py --model <xml> --backend hardware --hardware-config <yaml> --side right --supervised --park --speed 0.1 --start-tolerance-deg 20

"""
import os
import sys
import threading
import time

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, ".."))      # bringup.py, engage.py, guards.py, motion_plan.py next door
sys.path.insert(0, HERE)

import gafro  # noqa: E402,F401  -- before gafro_robot_interface
import gafro_robot_interface as ri  # noqa: E402

import engage  # noqa: E402
import guards  # noqa: E402

SHOW_FOR = 16.0                      # s, from the moment the robot interface is up


def side_of(argv):
    return argv[argv.index("--side") + 1] if "--side" in argv else "right"


def deadman():
    try:
        return guards.robot_state(timeout=0.5).get("deadman")
    except Exception as error:  # noqa: BLE001
        return f"?{type(error).__name__}"


def watch_loop(robot, side, show_for=SHOW_FOR, sleep=time.sleep, real_bus=True):
    indices = np.asarray(robot.get_system().get_task_space(side).get_joint_indices())
    began = time.time()
    print(f"   columns: [+t] q1..q7 (rad) | tau1..tau7 (N m) | permit left | deadman | driver status", flush=True)
    while time.time() - began < show_for:
        try:
            state = robot.get_state()
            q = np.asarray(state.get_position(), dtype=float)[indices]
            tau = np.asarray(state.get_torque(), dtype=float)[indices]
            permit, message = "", ""
            for status in robot.get_diagnostics():
                if status.get("component") == f"{side}_arm":
                    permit = f"{float((status.get('values') or {}).get('permit_remaining', 0)):.2f}"
                    message = f"{status.get('level')} '{status.get('message')}'"
            print(f"   [+{time.time() - began:4.1f}] q " + " ".join(f"{v:+.3f}" for v in q) + " | tau " + " ".join(f"{v:+.2f}" for v in tau)
                  + f" | permit {permit or '-'} | deadman {deadman() if real_bus else '-'} | {message}", flush=True)
        except Exception as error:  # noqa: BLE001
            print(f"   [watch] failed: {error!r}", flush=True)
        sleep(0.5)


def main():
    side = side_of(sys.argv)
    real_bus = "--backend" in sys.argv and sys.argv[sys.argv.index("--backend") + 1] == "hardware"
    real_initialize = ri.RobotInterface.initialize

    def initialize(self, *args, **kwargs):
        result = real_initialize(self, *args, **kwargs)
        threading.Thread(target=watch_loop, args=(self, side), kwargs={"real_bus": real_bus}, daemon=True).start()
        return result

    ri.RobotInterface.initialize = initialize
    return engage.main()


if __name__ == "__main__":
    sys.exit(main())
