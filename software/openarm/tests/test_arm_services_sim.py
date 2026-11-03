#!/usr/bin/env python3
"""commands.py on the simulator: reach_pose to a pose and back (and how long it takes), what is refused, a cancel, and candy_grasp's sequence against a stand-in gripper (the
simulated OpenArm has no gripper yet, README "The gripper"). No ROS: ros_actions.serve_command only binds commands.run to the ArmCommand action. Run it with the dev venv:
python3 software/openarm/tests/test_arm_services_sim.py   (needs gafro with MuJoCo, else SKIP)."""
import importlib.util
import json
import os
import sys
import threading
import time

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, "..", "gafro"))

if importlib.util.find_spec("gafro") is None or importlib.util.find_spec("mujoco") is None:
    print("SKIP: gafro or mujoco is not importable here")
    sys.exit(0)

from test_arm_service_sim import MODEL, args_for  # noqa: E402  -- weightless copy of the model, as that test uses

import arm_service  # noqa: E402
import gripper  # noqa: E402
import reach as reach_module  # noqa: E402
import reach_pose  # noqa: E402
import commands  # noqa: E402


class Fingers:
    """Stands in for the gripper: a close stops on `candy` metres (None: closes fully), an open goes to the travel."""

    def __init__(self, candy):
        self.candy, self.position, self.calls = candy, gripper.TRAVEL, []

    def __call__(self, metres):
        self.calls.append(metres)
        stalled = metres < (self.candy or 0.0) - 1e-6 if self.candy else False
        self.position = self.candy if stalled else metres
        return gripper.Result(self.position, stalled=stalled, reached=not stalled, message="stalled" if stalled else "reached")


def service_for(side="right"):
    robot, indices, position = arm_service.bringup.connect(args_for(side))
    service = arm_service.ArmService(robot, args_for(side))
    service.reach = reach_module.build(robot.get_system(), side, lambda text: None)
    return robot, service


def reach(service, **params):
    return commands.run(service, "reach_pose", json.dumps(params))


def test_reach_pose_goes_there_and_back(side="right"):
    robot, service = service_for(side)
    try:
        _, _, home = reach_pose.hand(robot, side)
        far = home[:3] + np.array([0.12, 0.06, 0.05])       # about 14 cm: well past what the first slice allowed (6 cm)
        out = reach(service, position=far.tolist())
        assert out.ok, out.message
        _, _, now = reach_pose.hand(robot, side)
        assert np.linalg.norm(now[:3] - far) < 0.015, np.linalg.norm(now[:3] - far)
        assert out.data["error_deg"] < reach_pose.TOLERANCE_DEG
        assert out.data["planned_seconds"] < 4.0, f"a 14 cm move planned for {out.data['planned_seconds']:.1f} s"
        assert service.running is None, "the arm is given back"
        assert reach(service, position=home[:3].tolist(), orientation=home[3:].tolist()).ok
        assert reach(service, position=home[:3].tolist()).message == "already there"
        slow = reach(service, position=far.tolist(), duration=4.0)       # the horizon as a parameter
        assert slow.ok and abs(slow.data["planned_seconds"] - 4.0) < 0.01, (slow.message, slow.data)
    finally:
        robot.shutdown()


def test_what_is_refused(side="right"):
    robot, service = service_for(side)
    try:
        _, _, home = reach_pose.hand(robot, side)
        far = (home[:3] + [0.12, 0.06, 0.05]).tolist()
        assert commands.run(service, "dance", "").code == reach_pose.INVALID_INPUT
        assert commands.run(service, "reach_pose", "not json").code == reach_pose.INVALID_INPUT
        assert reach(service, position=[1, 2]).code == reach_pose.INVALID_INPUT
        assert reach(service, position=[0, 0, 0], speed=50).code == reach_pose.INVALID_INPUT
        assert reach(service, position=(home[:3] + [2.0, 0.0, 0.0]).tolist()).code == reach_pose.NOT_FOUND
        short = reach(service, position=far, duration=0.3)
        assert short.code == reach_pose.FAILURE and "too short" in short.message, short.message
        service.running = "trajectory"
        busy = reach(service, position=far)
        assert busy.code == reach_pose.FAILURE and "trajectory is running" in busy.message
        assert "trajectory is running" in commands.check(service, "reach_pose", json.dumps({"position": far})), "the action rejects the goal"
        service.running = None
        assert commands.check(service, "reach_pose", json.dumps({"position": far})) is None
        assert commands.check(service, "reach_pose", "{}"), "a goal with no position is rejected"
    finally:
        robot.shutdown()


def test_a_cancel_holds_the_arm(side="right"):
    robot, service = service_for(side)
    try:
        _, _, home = reach_pose.hand(robot, side)
        done = []
        thread = threading.Thread(target=lambda: done.append(reach(service, position=(home[:3] + [0.12, 0.06, 0.05]).tolist(), duration=8.0)))
        thread.start()
        time.sleep(2.0)
        assert service.running == "reach_pose"
        service.hold()
        thread.join(15)
        assert done and not done[0].ok and done[0].cancelled, done
        _, _, now = reach_pose.hand(robot, side)
        assert 0.005 < np.linalg.norm(now[:3] - home[:3]) < 0.1, "stopped part of the way"
        assert service.running is None
    finally:
        robot.shutdown()


def test_candy_grasp(side="right"):
    robot, service = service_for(side)
    try:
        out = commands.run(service, "candy_grasp", "")
        assert out.code == reach_pose.INVALID_INPUT and "not taught" in out.message, out.message
        _, _, home = reach_pose.hand(robot, side)
        basket = {"basket": {"position": (home[:3] + [0.05, 0.0, -0.04]).tolist()}, "approach": 0.05, "lift": 0.05, "then": "stay"}

        stages, service.grip = [], Fingers(candy=0.012)
        out = commands.run(service, "candy_grasp", json.dumps(basket), stages.append)
        assert out.ok and out.data["holding"], out.message
        assert service.grip.calls == [gripper.TRAVEL, 0.0], service.grip.calls
        assert stages == ["open the gripper", "above the basket", "down to the basket", "close the gripper", "lift"], stages
        _, _, up = reach_pose.hand(robot, side)
        assert np.linalg.norm(up[:3] - (home[:3] + [0.05, 0.0, 0.01])) < 0.02, up[:3] - home[:3]

        service.grip = Fingers(candy=None)       # an empty basket: the close reaches shut
        out = commands.run(service, "candy_grasp", json.dumps({**basket, "basket": {"position": (up[:3] + [0.0, 0.0, -0.05]).tolist()}}))
        assert not out.ok and "nothing" in out.message, out.message
        assert service.grip.calls[-1] == gripper.TRAVEL, "an empty close opens again"
        assert service.running is None
    finally:
        robot.shutdown()


def test_gripper_commands(side="right"):
    robot, service = service_for(side)
    try:
        service.grip = Fingers(candy=0.012)
        out = commands.run(service, "close_gripper", "")
        assert out.ok and out.data["holding"] and abs(out.data["position"] - 0.012) < 1e-9, out.message
        assert service.grip.calls == [0.0] and service.running is None
        out = commands.run(service, "open_gripper", "")
        assert out.ok and not out.data["holding"] and service.grip.calls[-1] == gripper.TRAVEL, out.message

        service.grip = Fingers(candy=None)       # nothing between the fingers: shut
        out = commands.run(service, "close_gripper", "")
        assert not out.ok and "nothing" in out.message and not out.data["holding"], out.message
        assert service.running is None
    finally:
        robot.shutdown()


if __name__ == "__main__":
    test_reach_pose_goes_there_and_back()
    print("ok reach_pose")
    test_what_is_refused()
    print("ok refusals")
    test_a_cancel_holds_the_arm()
    print("ok cancel")
    test_candy_grasp()
    print("ok candy_grasp")
    test_gripper_commands()
    print("ok gripper commands")
