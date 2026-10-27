#!/usr/bin/env python3
"""arm_service.py's pieces that need no robot and no ROS: the trajectory resampling, the gripper's metres and its outcomes (reached, stalled, cancelled, refused), and the
shared permit (a gripper cue ending in the middle of a trajectory must not revoke the arm's permit).
Run: python3 software/openarm/tests/test_arm_service.py
"""
import os
import sys
import threading

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "gafro"))

import arm_service  # noqa: E402
import gripper  # noqa: E402
import guards  # noqa: E402
import trajectory  # noqa: E402


def test_resample():
    t, q, qd = trajectory.resample([0, 1, 2], [[0.0] * 7, [1.0] * 7, [0.0] * 7], [[0.0] * 7] * 3)
    assert abs(t[-1] - 2.0) < 1e-9 and abs(q[np.argmin(abs(t - 1.0)), 0] - 1.0) < 1e-9
    t, q, qd = trajectory.resample([0.0, 1.0], [[0.0] * 7, [1.0] * 7])           # no velocities: linear
    assert np.allclose(qd, 1.0) and np.allclose(q[:, 0], t)
    try:
        trajectory.resample([0.0, 0.0], [[0.0] * 7, [1.0] * 7])
    except ValueError:
        return
    raise AssertionError("a trajectory with no time in it was accepted")


def test_metres():
    assert gripper.closure_of(0.0) == 1.0 and gripper.closure_of(gripper.TRAVEL) == 0.0
    assert abs(gripper.metres_of(gripper.closure_of(0.01)) - 0.01) < 1e-12
    assert gripper.closure_of(0.1) == 0.0 and gripper.closure_of(-0.1) == 1.0


class Lease:
    def __init__(self, refuse=False):
        self.refuse, self.started, self.stopped = refuse, 0, 0

    def start(self):
        if self.refuse:
            raise guards.Refused("CH6 is released")
        self.started += 1

    def stop(self):
        self.stopped += 1


class Clock:
    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now

    def sleep(self, seconds):
        self.now += seconds


class FakeGripper:
    """A gripper whose finger moves toward the command at `speed` closure/s, stopping where `blocked_at` (a closure) is, if given."""

    def __init__(self, clock, opening=0.0, speed=2.0, blocked_at=None, names=("openarm_right_gripper",)):
        self.clock, self.opening, self.speed, self.blocked_at, self.names = clock, opening, speed, blocked_at, list(names)
        self.target, self.at, self.moving = opening, clock.now, False

    def get_gripper_names(self):
        return self.names

    def set_gripper(self, name, closure):
        self.target = closure

    def subscribe_gripper(self, name):
        return self

    def read(self):
        dt, self.at = self.clock.now - self.at, self.clock.now
        step = np.clip(self.target - self.opening, -self.speed * dt, self.speed * dt)
        if self.blocked_at is not None and self.opening < self.blocked_at <= self.opening + step:
            step = self.blocked_at - self.opening
            self.target = self.opening
        self.moving = abs(step) > 1e-9
        self.opening += step
        return {"time": self.at, "opening": self.opening, "grasped": False, "moving": self.moving, "commanded": self.target}


def grip(robot, clock, metres, lease=None, cancel=None):
    return gripper.grip(robot, "right", metres, lease or Lease(), cancel or threading.Event(), clock=clock, sleep=clock.sleep)


def test_gripper_reaches():
    clock = Clock()
    lease = Lease()
    result = grip(FakeGripper(clock), clock, 0.0, lease)        # fully shut
    assert result.reached and not result.stalled and abs(result.position) <= gripper.TOLERANCE, result
    assert (lease.started, lease.stopped) == (1, 1)


def test_a_close_that_meets_something_aborts_as_stalled():
    clock = Clock()
    result = grip(FakeGripper(clock, blocked_at=0.5), clock, 0.0)
    assert result.stalled and not result.reached, result
    assert abs(result.position - gripper.metres_of(0.5)) < 0.003


def test_cancel_holds():
    clock, cancel = Clock(), threading.Event()
    robot = FakeGripper(clock, speed=0.2)
    cancel.set()
    result = grip(robot, clock, 0.0, cancel=cancel)
    assert result.cancelled and not result.reached


def test_refusals_command_nothing():
    clock = Clock()
    robot = FakeGripper(clock)
    assert "CH6" in grip(robot, clock, 0.0, Lease(refuse=True)).message and robot.target == 0.0
    assert not grip(FakeGripper(clock, names=()), clock, 0.0).reached                                  # no gripper (not calibrated)
    assert "outside" in grip(robot, clock, 0.5).message


class FakeGoal:
    """A goal handle that enforces rclpy's rule: CANCELED only from a cancel request (else "invalid transition from EXECUTING with CANCELED")."""

    def __init__(self, cancel_requested):
        self.is_cancel_requested, self.state = cancel_requested, "EXECUTING"

    def canceled(self):
        assert self.is_cancel_requested, "invalid transition from EXECUTING with CANCELED"
        self.state = "CANCELED"

    def succeed(self):
        self.state = "SUCCEEDED"

    def abort(self):
        self.state = "ABORTED"


def test_a_goal_ends_in_a_state_rclpy_allows():
    import ros_actions

    for requested, cancelled, succeeded, state in ((True, True, False, "CANCELED"), (False, True, False, "ABORTED"), (False, False, True, "SUCCEEDED"),
                                                   (False, False, False, "ABORTED")):
        goal = FakeGoal(requested)
        ros_actions.settle(goal, cancelled, succeeded)
        assert goal.state == state, (requested, cancelled, succeeded, goal.state)


class FakeKeeper:
    log = []

    def __init__(self, groups, allowed=None):
        self.allowed = allowed

    def start(self):
        FakeKeeper.log.append("start")

    def stop(self):
        FakeKeeper.log.append("stop")


def test_the_permit_outlives_a_lease_that_ends_in_the_middle_of_another():
    arm_service.PermitKeeper, original = FakeKeeper, arm_service.PermitKeeper
    try:
        permit = arm_service.SharedPermit(("right_arm",), None)
        trajectory_lease, gripper_lease = permit.lease(), permit.lease()
        trajectory_lease.start()
        gripper_lease.start()
        gripper_lease.stop()
        assert FakeKeeper.log == ["start"], "the gripper cue ending revoked the arm's permit"
        trajectory_lease.stop()
        assert FakeKeeper.log == ["start", "stop"] and not permit.active
        FakeKeeper.log.clear()
        first = permit.lease().start()
        permit.revoke()                                    # `hold`
        assert FakeKeeper.log == ["start", "stop"] and not permit.active
        first.stop()                                       # a lease ending after the revoke does not count twice
        again = permit.lease().start()
        first.stop()
        assert permit.active, "a stale lease ended a newer one"
        again.stop()
    finally:
        arm_service.PermitKeeper = original


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for test in tests:
        test()
        print("ok", test.__name__)
