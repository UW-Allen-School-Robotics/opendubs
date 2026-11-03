#!/usr/bin/env python3
"""What the arm service says about itself on its state topic (`arm_service/<side>`): the pairs a DiagnosticStatus carries, and ArmService.state(). No ROS, no robot.
Run: python3 software/openarm/tests/test_arm_state.py"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "gafro"))

import arm_service  # noqa: E402
import ros_actions  # noqa: E402


class Args:
    side, backend, hardware_config, supervised = "right", "mujoco", "", False


def make():
    service = arm_service.ArmService.__new__(arm_service.ArmService)      # state() reads three attributes, not the robot
    service.args, service.running, service.last = Args(), None, ""
    return service


def test_state_of_a_held_arm():
    assert make().state() == {"state": "held", "supervised": "no", "last": "none"}


def test_state_names_the_running_command_and_supervision():
    service = make()
    service.running, service.last = "follow", "activate done"
    service.args.supervised = True
    assert service.state() == {"state": "follow", "supervised": "yes", "last": "activate done"}
    service.args.supervised = False


def test_the_topic_carries_those_pairs_in_a_fixed_order():
    pairs = ros_actions.state_message_values({"last": "x", "state": "held", "supervised": "no", "extra": "ignored"})
    assert pairs == [("state", "held"), ("supervised", "no"), ("last", "x")]
    assert ros_actions.state_message_values({}) == [("state", ""), ("supervised", ""), ("last", "")]


if __name__ == "__main__":
    for name, test in sorted(globals().items()):
        if name.startswith("test_"):
            test()
            print("ok", name)
