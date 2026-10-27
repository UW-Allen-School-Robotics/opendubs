"""Follow live pose targets with one arm: the hand tracks a pose reference that something else publishes (the ROS bridge's `<side>/target`, a policy, a teleop). Arm_service.py's slice 5.

The `follow_pose` controller of controllers.yaml is a position controller (a pose stage on its own anchor; the integrated anchor is the command, the motor's PD and the driver's gravity
feed-forward close the loop), built where the arm is now, with the reference source holding the hand where it is until the first target arrives: nothing moves at the start. A target is
followed at the stage's speed limits (0.5 m/s, 1.5 rad/s), so a far one is a slow approach, and there is NO workspace box and no head in a collision model: the guards are the joint limits
and the permit's gate. The run ends, and the arm holds, when it is cancelled, when the permit is withheld (CH5, the e-stop or CH6) and, as trajectory.py does, on a measured torque at the
limit or a vibrating joint. After a withheld permit the controller's anchor has been integrating while the arm was held, so the run does not resume: start it again.
"""
from __future__ import annotations

import threading
import time

import numpy as np

import trajectory

NAME = "follow_pose"


def reference_source(robot, side: str):
    """The pose reference `<side>/target` feeds, reporting the hand where it is now until a target arrives (a source that starts at the identity drags the arm there)."""
    from gafro import control

    task = robot.get_system().get_task_space(side)
    q = np.asarray(robot.get_state().get_position(), dtype=float)
    source = control.MotorReferenceSource(task.compute_ee_motor(q[np.asarray(task.get_joint_indices())]), horizon=0.5)
    source.set_staleness(control.Staleness.Hold)
    source.prime(2)
    return source


def start_controller(robot, side: str, controllers: dict, source):
    """Build `NAME` at the arm's pose now, hold `source` at that pose, and hand the controller to the interface's loop."""
    from gafro import control

    import engage

    system = robot.get_system()
    task = system.get_task_space(side)
    indices = np.asarray(task.get_joint_indices())
    q = np.asarray(robot.get_state().get_position(), dtype=float).copy()
    pose = task.compute_ee_motor(q[indices])
    source.publish(control.MotorReferenceChunk(control.monotonic_now(), 0.1, [pose, pose]))      # hold the hand where it is
    posture = control.JointReferenceSource(q.copy(), horizon=0.5)
    posture.set_staleness(control.Staleness.Hold)
    posture.prime(2)
    robot.control(engage.build(NAME, controllers, system, side, q, pose, source, posture), False)
    return indices


def follow(robot, side: str, controllers: dict, source, lease, cancel: threading.Event, withheld, limits: trajectory.Limits = trajectory.Limits(), log=print) -> str:
    """Follow until the run ends; returns why. `lease` is this run's share of the permit (started here, released on every way out); `withheld()` is how many permit renewals the
    gate has refused so far."""
    import guards

    try:
        lease.start()
    except guards.Refused as refused:
        return f"refused: {refused}"
    done, vibrating, torque = threading.Event(), {"run": 0, "joints": []}, {"peak": np.zeros(7)}
    try:
        indices = start_controller(robot, side, controllers, source)

        def record():
            while not done.is_set():
                s = robot.get_state()
                v = np.asarray(s.get_velocity(), dtype=float)[indices]
                torque["peak"] = np.abs(np.asarray(s.get_torque(), dtype=float)[indices])
                over = np.abs(v) > limits.max_joint_speed
                vibrating["run"] = vibrating["run"] + 1 if over.any() else 0
                if vibrating["run"] >= trajectory.VIBRATION_SAMPLES and not vibrating["joints"]:
                    vibrating["joints"] = [int(j) + 1 for j in np.where(over)[0]]
                time.sleep(0.002)

        threading.Thread(target=record, daemon=True).start()
        before = withheld()
        while not cancel.is_set():
            time.sleep(0.01)
            if withheld() > before:
                return "stopped: CH5, the e-stop or the deadman stopped permitting motion; the arm holds. Start following again to continue"
            if vibrating["joints"]:
                return f"stopped: VIBRATION in joint(s) {vibrating['joints']} (over {limits.max_joint_speed:g} rad/s); the arm holds"
            if torque["peak"].max() >= limits.max_torque:
                return f"stopped: FORCE LIMIT, joint {int(torque['peak'].argmax()) + 1} reached {limits.max_torque:g} N m; the arm holds"
        return "stopped: cancelled; the arm holds"
    finally:
        done.set()
        lease.stop()
