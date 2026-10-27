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


def trajectory_limit():
    return arm_service.trajectory.Limits().max_speed


def test_a_move_takes_the_time_asked():
    """`activate` takes --move-seconds (5 s by default: stow to ready peaks at 0.68 rad/s, under the driver's 1.0 limit), and a time the distance cannot allow is refused."""
    assert arm_service.MOVE_SECONDS == 5.0 and 1.875 * 1.8 / arm_service.MOVE_SECONDS < trajectory_limit()
    side = "right"
    robot, indices, position = arm_service.bringup.connect(args_for(side))
    args = args_for(side)
    args.stow_conf = None
    service = arm_service.ArmService(robot, args)
    try:
        ready = position().copy()
        conf = os.path.join(tempfile.mkdtemp(prefix="arm_service_stow_"), "stow.conf")
        stow = ready.copy()
        stow[0] += np.radians(30.0)
        with open(conf, "w") as stream:
            stream.write(f"{side.upper()}_STOW=" + ",".join(f"{v:.4f}" for v in np.degrees(stow)) + "\n")
        args.stow_conf = conf
        assert service.start("park", -1.0).startswith("refused") and service.start("follow", 3.0).startswith("refused"), "a bad move time was accepted"
        for name, expected in (("park", 3.0), ("activate", 3.0)):
            began = time.time()
            assert service.start(name, 3.0) == f"{name} started"        # this move's time, not the service's 5 s
            wait(service)
            took = time.time() - began
            assert service.last == f"{name} done" and expected <= took < expected + 2.5, f"{name} took {took:.1f} s, asked {expected:g}: {service.last}"
        assert np.abs(position() - ready).max() < 0.05
        args.stow_conf = conf                                   # 0.52 rad in 0.4 s peaks at 2.4 rad/s: over the limit
        assert service.start("park", 0.4) == "park started"
        wait(service)
        assert "rad/s" in service.last and "park stopped" in service.last, service.last
        assert np.abs(position() - ready).max() < 0.05, "a refused move moved the arm"

        # the case measured on the arm: 1.8 rad on joint 4 in 3 s peaks at 1.125 rad/s, over what the driver follows (1.0): refused; in 4 s it peaks at 0.84: played
        far = ready.copy()
        far[3] = 0.0
        with open(conf, "w") as stream:
            stream.write(f"{side.upper()}_STOW=" + ",".join(f"{v:.4f}" for v in np.degrees(far)) + "\n")
        assert service.start("park", 3.0) == "park started"
        wait(service)
        assert "park stopped" in service.last and "rad/s" in service.last, service.last
        assert np.abs(position() - ready).max() < 0.05, "a refused move moved the arm"
        assert service.start("park", 4.0) == "park started"
        wait(service)
        assert service.last == "park done", service.last
        assert abs(position()[3]) < 0.05
        assert service.start("activate", 4.0) == "activate started"
        wait(service)
        assert service.last == "activate done" and np.abs(position() - ready).max() < 0.05, service.last
    finally:
        robot.shutdown()


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
        assert arm_service.send("activate", path=path, move_seconds=2.5) == "activate started", "ctl activate --move-seconds"
        assert "refused" in arm_service.send("park", path=path), "two moving commands at once"
        for _ in range(100):
            if "held" in arm_service.send("status", path=path):
                break
            time.sleep(0.3)
        assert "activate done" in arm_service.send("status", path=path)
        assert "supervised: no" in arm_service.send("status", path=path)
        arm_service.send("supervise", path=path)
        assert "supervised: yes" in arm_service.send("status", path=path), "status does not say it is supervised"
        assert "unsupervised" in arm_service.send("unsupervise", path=path)
        assert "supervised: no" in arm_service.send("status", path=path)
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
        recordings = tempfile.mkdtemp(prefix="arm_service_rec_")
        service.args.record_dir = recordings
        assert service.start("activate") == "activate started"
        wait(service)
        assert service.last == "activate done", service.last
        assert np.abs(position() - ready).max() < 0.05, "the arm did not come back to ready"
        (csv_file,) = os.listdir(recordings)
        rows = open(os.path.join(recordings, csv_file)).read().splitlines()
        assert rows[0].startswith("t,q1") and len(rows) > 100, f"the recording is empty: {len(rows)} rows"
        assert max(float(r.split(",")[-1]) for r in rows[1:]) < 0.2, "the recording's error column is not the tracking error"
        service.args.stow_conf = os.path.join(os.path.dirname(conf), "missing.conf")
        assert service.start("park") == "park started"
        wait(service)
        assert "no stow pose configured" in service.last, service.last
    finally:
        robot.shutdown()


def test_a_start_off_the_ends_is_allowed_on_the_line_and_by_flag(side="right"):
    """The start check of a real bus, run in the simulator (it is skipped there by default: `tolerance` None): an arm stopped half way along the stow-ready line goes on,
    an arm off the line is refused, and --allow-large moves it anyway."""
    import engage

    robot, indices, position = arm_service.bringup.connect(args_for(side))
    service = arm_service.ArmService(robot, args_for(side))
    ready = position().copy()
    stow = np.zeros(7)
    known, tolerance = {"stow": stow}, float(np.radians(5.0))
    conf = os.path.join(tempfile.mkdtemp(prefix="arm_service_stow_"), "stow.conf")
    with open(conf, "w") as stream:
        stream.write(f"{side.upper()}_STOW=" + ",".join(f"{v:.4f}" for v in np.degrees(stow)) + "\n")
    service.args.stow_conf, service.args.move_seconds = conf, 6.0
    try:
        def go_to(q):                                  # put the arm there (a trajectory: the simulator has no start check)
            times = np.linspace(0, 6, 31)
            s = times / 6
            shape = s**3 * (10 - 15 * s + 6 * s**2)
            assert service.follow(times, position() + np.outer(shape, q - position())).code == 0

        go_to(0.5 * ready)                               # half way along the line, where an interrupted move leaves the arm
        try:
            service._move("activate", known, tolerance, 6.0)
        except engage.EngageRefused as refused:
            raise AssertionError(f"the arm on the stow-ready line was refused: {refused}")
        assert np.abs(position() - ready).max() < 0.05

        off = 0.5 * ready
        off[0] += 0.4                                    # 23 deg off the line on joint 1
        go_to(off)
        try:
            service._move("activate", known, tolerance, 6.0)
            raise AssertionError("an arm off the line was accepted")
        except engage.EngageRefused as refused:
            assert "--allow-large" in str(refused), refused
        service._move("activate", known, tolerance, 6.0, True)
        assert np.abs(position() - ready).max() < 0.05
    finally:
        robot.shutdown()


def test_the_dashboard_drives_the_real_service(side="right"):
    """The dashboard's own code (kiosk-dashboard/backend/openarm.py) against a real `arm_service.py` process: the shared socket directory, `activate` through to `done`,
    `hold`, and `down` ending the process. No `openarm-up`: the dashboard has none to fall back to."""
    directory = tempfile.mkdtemp(prefix="arm_socket_")
    os.environ["OPENDUBS_ARM_SOCKET_DIR"] = directory
    sys.path.insert(0, os.path.join(HERE, "..", "..", "kiosk-dashboard", "backend"))
    import openarm

    process = subprocess.Popen([sys.executable, os.path.join(GAFRO, "arm_service.py"), "run", "--side", side, "--backend", "mujoco", "--model", MODEL, "--no-ros",
                                "--socket-mode", "660"], stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    try:
        path = os.path.join(directory, f"opendubs-arm-service-{side}.sock")
        for _ in range(100):
            if os.path.exists(path):
                break
            time.sleep(0.2)
        assert os.path.exists(path), "the service did not come up in the shared directory"
        assert oct(os.stat(path).st_mode & 0o777) == "0o660", "--socket-mode was not applied"

        def run(name):
            assert openarm.start_op(name, "both")[0]
            end = time.time() + 40
            while openarm.op_status()["running"] and time.time() < end:
                time.sleep(0.2)
            return openarm.op_status()

        op = run("activate")
        assert op["rc"] == 0 and op["error"] is None and "activate done" in op["out"], op
        assert run("hold")["rc"] == 0
        assert "held" in openarm.snapshot.__globals__["_service_status"](side)
        op = run("down")
        assert op["rc"] == 0, op
        assert process.wait(timeout=20) == 0
    finally:
        if process.poll() is None:
            process.kill()
        os.environ.pop("OPENDUBS_ARM_SOCKET_DIR", None)


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
    test_the_dashboard_drives_the_real_service("right")
    print("ok dashboard")
    test_a_move_takes_the_time_asked()
    print("ok move duration")
    test_the_process("left")
    print("ok process (left)")
    test_a_start_off_the_ends_is_allowed_on_the_line_and_by_flag("right")
    print("ok start check")
    for side in ("right", "left"):
        test_park_and_activate_move_the_arm(side)
        print("ok park and activate", side)
        test_trajectory_and_follow(side)
        print("ok trajectory and follow", side)
