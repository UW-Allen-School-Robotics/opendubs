#!/usr/bin/env python3
"""arm_service.py on the simulator, both arms: the real process (`run`, then `ctl` over its socket, then `down`), and in process a trajectory, a pose follow and a refusal on each side.
Needs gafro with MuJoCo (without them this prints SKIP and exits 0, as the other gafro tests do). No ROS: the service runs with --no-ros, so /joint_states, the actions and
`<side>/target` are NOT covered here; they need a machine with ROS.

The simulator's position servo holds nothing against gravity, where the driver adds a gravity feed-forward on the real arm, so the model is copied with every body weightless
(MuJoCo `gravcomp`), as gafro-ri-ros2-bridge's `robot.gravity_compensation` does. Run: python3 software/openarm/tests/test_arm_service_sim.py   (about 90 s)
"""
import argparse
import importlib.util
import os
import subprocess
import sys
import tempfile
import time

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
GAFRO = os.path.join(HERE, "..", "gafro")
sys.path.insert(0, GAFRO)

if importlib.util.find_spec("gafro") is None or importlib.util.find_spec("mujoco") is None:
    print("SKIP: gafro or mujoco is not importable here")
    sys.exit(0)

import gafro  # noqa: E402,F401  -- before gafro_robot_interface
from gafro import control  # noqa: E402

import arm_service  # noqa: E402
import pose_follow  # noqa: E402


def weightless(model_file):
    import mujoco

    spec = mujoco.MjSpec.from_file(model_file)
    for body in spec.bodies:
        if body.name != "world":
            body.gravcomp = 1.0
    spec.meshdir = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(model_file)), spec.meshdir))
    path = os.path.join(tempfile.mkdtemp(prefix="arm_service_test_"), os.path.basename(model_file))
    with open(path, "w") as stream:
        stream.write(spec.to_xml())
    return path


MODEL = weightless(arm_service.DEFAULT_MODEL)


def args_for(side):
    return argparse.Namespace(side=side, model=MODEL, backend="mujoco", hardware_config="", supervised=False, dashboard=arm_service.guards.DASHBOARD,
                              stow_conf=None, start_tolerance_deg=5.0, listen_seconds=0.5, state_rate=100.0)


def wait(service, seconds=30.0):
    end = time.time() + seconds
    while service.running and time.time() < end:
        time.sleep(0.2)
    assert not service.running, f"{service.running} did not finish"


def test_the_process(side="left"):
    """`arm_service.py run` as the unit would start it: the socket is the side's, commands are answered, `down` ends it and removes the socket."""
    path = arm_service.socket_for(side)
    process = subprocess.Popen([sys.executable, os.path.join(GAFRO, "arm_service.py"), "run", "--side", side, "--backend", "mujoco", "--model", MODEL, "--no-ros"],
                               stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    try:
        for _ in range(100):
            if os.path.exists(path):
                break
            time.sleep(0.2)
        assert os.path.exists(path), "the service did not come up"
        first = arm_service.send("status", path=path)
        assert "held" in first and "joints (deg)" in first, f"the reply was cut short: {first!r}"
        assert "refused" in arm_service.send("follow", path=path), "follow needs ROS (the pose reference), so it must refuse without it"
        assert arm_service.send("activate", path=path) == "activate started"
        assert "refused" in arm_service.send("park", path=path), "two moving commands at once"
        for _ in range(100):
            if "held" in arm_service.send("status", path=path):
                break
            time.sleep(0.3)
        assert "activate done" in arm_service.send("status", path=path)
        assert "unsupervised" in arm_service.send("unsupervise", path=path)
        assert arm_service.send("down", path=path) == "down"
        assert process.wait(timeout=20) == 0, process.stdout.read()
        assert not os.path.exists(path), "the socket was left behind"
    finally:
        if process.poll() is None:
            process.kill()


def test_park_and_activate_move_the_arm(side):
    """`park` and `activate` are POSITION-mode lines: the arm leaves ready for a stow file's pose 15 degrees off on joint 1 and comes back. (engage.py's torque controller did not
    move the real arm: it clicked and stayed put; the simulator moved it all the same, so only a test that asserts the arm really moved can tell the two apart.)"""
    robot, indices, position = arm_service.bringup.connect(args_for(side))
    service = arm_service.ArmService(robot, args_for(side))
    ready = position().copy()
    stow = ready.copy()
    stow[0] += np.radians(15.0)
    conf = os.path.join(tempfile.mkdtemp(prefix="arm_service_stow_"), "stow.conf")
    with open(conf, "w") as stream:
        stream.write(f"{side.upper()}_STOW=" + ",".join(f"{v:.4f}" for v in np.degrees(stow)) + "\n")
    service.args.stow_conf = conf
    try:
        assert service.start("park") == "park started", "park was not started"
        wait(service)
        assert service.last == "park done", service.last
        assert np.abs(position() - stow).max() < 0.05 and abs(position()[0] - ready[0]) > 0.2, "the arm did not move to the stow pose"
        assert service.start("activate") == "activate started"
        wait(service)
        assert service.last == "activate done", service.last
        assert np.abs(position() - ready).max() < 0.05, "the arm did not come back to ready"
        service.args.stow_conf = os.path.join(os.path.dirname(conf), "missing.conf")
        assert service.start("park") == "park started"
        wait(service)
        assert "no stow pose configured" in service.last, service.last
    finally:
        robot.shutdown()


def test_trajectory_and_follow(side):
    import gafro_robot_interface as ri  # noqa: F401

    robot, indices, position = arm_service.bringup.connect(args_for(side))
    service = arm_service.ArmService(robot, args_for(side))
    try:
        q0 = position().copy()
        move = np.zeros(7)
        move[0], move[3] = 0.12, -0.08
        times = np.linspace(0, 2, 11)
        s = times / 2
        path = np.array([q0 + move * x for x in s**3 * (10 - 15 * s + 6 * s**2)])
        result = service.follow(times, path)
        assert result.code == 0, result.message
        assert np.abs(position() - path[-1]).max() < 0.05

        away = path + np.array([0.3, 0, 0, 0, 0, 0, 0])
        assert service.follow(times, away).code == trajectory_refusal(), "a path that starts away from the arm must be refused"

        service.source = pose_follow.reference_source(robot, side)
        task = robot.get_system().get_task_space(side)
        assert service.start("follow") == "follow started"
        time.sleep(0.5)
        q = np.asarray(robot.get_state().get_position(), dtype=float).copy()
        target_q = q.copy()
        target_q[indices[0]] += 0.1
        hand = lambda c: np.asarray(task.compute_ee_motor(c[task.get_joint_indices()]).to_position_quaternion(), dtype=float)[:3]  # noqa: E731
        service.source.publish(control.MotorReferenceChunk(control.monotonic_now(), 0.1, [task.compute_ee_motor(target_q[task.get_joint_indices()])]))
        time.sleep(4.0)
        error = np.abs(hand(np.asarray(robot.get_state().get_position(), dtype=float)) - hand(target_q)).max()
        assert error < 0.005, f"the hand is {error:.4f} m from the target"
        assert service.hold() == "holding"
        wait(service, 5)
        assert "cancelled" in service.last
    finally:
        robot.shutdown()


def trajectory_refusal():
    return arm_service.trajectory.INVALID_GOAL


if __name__ == "__main__":
    test_the_process("left")
    print("ok process (left)")
    for side in ("right", "left"):
        test_park_and_activate_move_the_arm(side)
        print("ok park and activate", side)
        test_trajectory_and_follow(side)
        print("ok trajectory and follow", side)
