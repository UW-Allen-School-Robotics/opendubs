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
import pose_follow  # noqa: E402
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


def test_the_stow_ready_line_is_a_known_start():
    a, b = np.zeros(7), np.array([0.0, 0.7, -0.5, 1.8, -1.0, -0.1, -0.55])
    tolerance = np.radians(5.0)
    assert arm_service.line_fraction(a, a, b, tolerance) == 0.0 and arm_service.line_fraction(b, a, b, tolerance) == 1.0
    halfway = arm_service.line_fraction(0.4 * b, a, b, tolerance)
    assert abs(halfway - 0.4) < 1e-9, "an arm stopped on the line is not recognised"
    assert arm_service.line_fraction(0.4 * b + np.array([0.2, 0, 0, 0, 0, 0, 0]), a, b, tolerance) is None, "an arm 11 deg off the line was accepted"
    assert arm_service.line_fraction(0.4 * b, None, b, tolerance) is None, "a missing pose must not count as a known start"
    assert arm_service.line_fraction(2.0 * b, a, b, tolerance) is None, "past the ready end, far from it"


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


def test_the_twist_of_a_moving_target_is_estimated_in_the_tool_frame():
    """`reference_rate`, the feed-forward of `follow_pose`: a target at a world velocity v with the tool at rotation R has the twist [0, 0, 0, R^T v] (measured in the simulator to be the
    frame and sign that remove the lag); a target that stops has none, and one that jumps does not give a twist beyond the stage's own speed limit."""
    try:
        import gafro
        from gafro import control  # noqa: F401
    except ImportError:
        print("skip test_the_twist_of_a_moving_target_is_estimated_in_the_tool_frame: gafro is not importable here")
        return

    class Source:
        motor = None

        def get_last(self):
            return self.motor

    class Twist:
        def publish(self, chunk):
            pass

    quaternion = (0.5, 0.5, 0.5, 0.5)                       # w x y z: the tool's axes are the world's, cycled
    source = Source()
    estimator = pose_follow.TwistEstimator(source, Twist())
    here = np.array([0.3, -0.2, 0.5])

    def place(p):
        source.motor = gafro.Motor.from_position_quaternion(*p, *quaternion)

    velocity = np.array([0.0, 0.1, 0.0])                    # world; R^T v = (0.1, 0, 0) for this rotation
    for k in range(100):
        place(here + velocity * k * 0.01)
        estimate = estimator.step(k * 0.01)
    assert np.allclose(estimate, [0, 0, 0, 0.1, 0, 0], atol=0.01), estimate

    for k in range(100, 160):                               # the target stops
        estimate = estimator.step(k * 0.01)
    assert np.abs(estimate).max() < 0.005, estimate

    place(here + np.array([1.0, 0.0, 0.0]))                  # and jumps a metre in one step
    estimate = estimator.step(1.61)
    assert np.linalg.norm(estimate[3:]) <= pose_follow.TWIST_LIMIT[1] + 1e-9, estimate


def test_the_limits_urdf_is_what_the_runner_reads_ranges_from():
    """ros_actions.limits_urdf is served as /robot_state_publisher's robot_description; the runner's urdf_ranges must read the model's own limits back out of it."""
    import math
    from types import SimpleNamespace as NS

    sys.path.insert(0, os.path.join(HERE, "..", "..", "choreo"))
    import arm_output
    import ros_actions

    lower = np.array([-1.0 - 0.1 * i for i in range(16)])           # 2 arms x 7 joints, and two more (grippers) that are not in any task space
    upper = -lower * 0.5
    names = [f"openarm_{side}_joint{j}" for side in ("left", "right") for j in range(1, 8)] + ["g1", "g2"]
    order = {"left": list(range(7)), "right": list(range(7, 14))}
    system = NS(get_actuators=lambda: [NS(joint_name=n) for n in names], get_task_space=lambda side: NS(get_joint_indices=lambda: order[side]),
                get_joint_limits_min=lambda: NS(coefficients=lambda: lower), get_joint_limits_max=lambda: NS(coefficients=lambda: upper))
    urdf = ros_actions.limits_urdf(NS(get_system=lambda: system))
    ranges = arm_output.urdf_ranges(urdf, {"left": 0.0, "right": 0.0})
    for slot, indices in (("ARM_L", order["left"]), ("ARM_R", order["right"])):
        want = [(math.degrees(lower[i]), math.degrees(upper[i])) for i in indices]
        assert len(ranges[slot]) == 7 and all(abs(a - c) < 1e-6 and abs(b - d) < 1e-6 for (a, b), (c, d) in zip(ranges[slot], want)), (slot, ranges[slot], want)
    assert "g1" not in urdf, "only the arms' joints"


def test_the_vibration_stop_follows_the_path_speed_limit():
    assert trajectory.Limits().max_speed == 3.5 and trajectory.Limits().max_joint_speed == 4.5
    slow = trajectory.Limits(max_speed=1.0)
    assert slow.max_joint_speed == 1.0 + trajectory.VIBRATION_MARGIN, "the stop is above the path limit, whatever it is"
    assert trajectory.Limits(max_speed=1.0, max_joint_speed=2.5).max_joint_speed == 2.5, "a named value is kept"
    import inspect
    assert inspect.signature(pose_follow.follow).parameters["limits"].default.max_joint_speed == 2.5, "pose following keeps its own vibration stop"


def test_the_service_takes_its_path_speed_limit_from_the_drivers_max_velocity():
    import tempfile
    from types import SimpleNamespace as NS

    def limit(text, backend="hardware", side="left"):
        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, "hw.yaml")
            with open(path, "w") as handle:
                handle.write(text)
            args = NS(side=side, backend=backend, hardware_config=path, dashboard="x")
            return arm_service.ArmService(None, args).limits

    named = limit("left_arm: {driver: openarm, bus: can, can_interface: arm_left, max_velocity: 2.0}\n")
    assert (named.max_speed, named.max_joint_speed) == (2.0, 2.0 + trajectory.VIBRATION_MARGIN), named
    unnamed = limit("left_arm: {driver: openarm, bus: can, can_interface: arm_left}\n")
    assert unnamed.max_speed == 1.0, "no max_velocity: the driver's own default, so a fast show is refused up front"
    fake = limit("left_arm: {driver: openarm, bus: fake}\n")
    assert fake.max_speed == trajectory.Limits().max_speed, "no real bus, no driver: the simulator's default"
    assert limit("left_arm: {driver: openarm, bus: can, can_interface: arm_left, max_velocity: 2.0}\n", backend="mujoco").max_speed == trajectory.Limits().max_speed


def test_the_tracking_limit_is_the_trajectory_default_unless_the_service_is_given_one():
    from types import SimpleNamespace as NS

    base = dict(side="left", backend="mujoco", hardware_config="", dashboard="x")
    assert arm_service.ArmService(None, NS(**base)).limits.max_error == trajectory.Limits().max_error == 0.2
    wider = arm_service.ArmService(None, NS(**base, max_error=0.5)).limits
    assert wider.max_error == 0.5 and wider.max_speed == trajectory.Limits().max_speed, wider


def test_the_force_limit_is_the_trajectory_default_unless_the_service_is_given_one():
    """opendubs2 runs 25 N m (OPENDUBS_ARM_MAX_TORQUE). The service's limits carry it to its trajectories; following takes pose_follow.LIMITS with this force limit."""
    from types import SimpleNamespace as NS

    base = dict(side="left", backend="mujoco", hardware_config="", dashboard="x")
    assert arm_service.ArmService(None, NS(**base)).limits.max_torque == trajectory.Limits().max_torque == 12.0
    service = arm_service.ArmService(None, NS(**base, max_torque=25.0))
    assert service.limits.max_torque == 25.0 and service.limits.max_error == trajectory.Limits().max_error, service.limits
    assert pose_follow.LIMITS.max_joint_speed == 2.5 and pose_follow.LIMITS.max_torque == 12.0, pose_follow.LIMITS


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for test in tests:
        test()
        print("ok", test.__name__)
