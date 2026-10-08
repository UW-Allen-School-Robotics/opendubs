"""Live arm output for the show runner (workplan 5.6) — the ONLY code here that moves the real arms.

Decided 2026-10-08: the runner sends each arm's routine as ONE FollowJointTrajectory goal, the same
action `openarm-up park` uses, and only while its own checks hold (CH5 AUTO, the deadman held, the
arms active — DEC-21 permits shows in AUTO). Ending a show early cancels the goal, and the controller
holds where the arm is (runner.ABORT_STATE). An e-stop needs nothing from here: the contactor has
already cut the arms' power. Grippers are NOT driven until each side is calibrated (plan 6.2).

What the arm does, and why it matches what was validated:
  * The validator and the twin both start every arm at ZERO (cue.py speed check, sim_preview.arm_pose),
    so before the show the arm is moved there — from where it is MEASURED to be, slowly (runner PREPPING).
  * Then it follows the show's keyframes, each reached at its time, moving in a straight line in joint
    space from the previous one (the format's definition). The points are sent densely (every
    DENSE_S), so the path is that straight line whatever interpolation the controller uses.
  * Angles go out in the controller's own sense: the export's `pose_signed` (the left arm's mirrored
    joints already flipped, by cue.py — one copy of that rule).

Robot-side expiry (plan 5.3) is NOT built: if the runner process dies mid-routine, the controller
finishes the goal it has — at most the rest of that routine, which ends at rest. The e-stop is the
backstop. Recorded in the plan's stub register.
"""
from __future__ import annotations

import math
import threading
import time

ARM_SIDES = {"ARM_L": "left", "ARM_R": "right"}
DENSE_S = 0.1            # trajectory point spacing — the path is the format's straight line
JOINT_STATE_MAX_AGE_S = 0.5


def joint_names(side: str) -> list:
    return [f"openarm_{side}_joint{j}" for j in range(1, 8)]


def track_points(keyframes: list, t_from: float = 0.0, start=None) -> list:
    """The arm's path as [(seconds from now, [7 signed degrees])], from show time `t_from`.

    Straight lines in joint space between keyframes, each reached at its `t`, holding between a
    keyframe and the next one's `start` — exactly sim_preview.arm_pose, sampled every DENSE_S. The
    arm starts at `start` (signed degrees; default zero, where the validator assumes it is)."""
    prev = list(start) if start else [0.0] * 7
    segs = []                                  # (t0, t1, pose0, pose1)
    for kf in keyframes:
        pose = [float(kf["pose_signed"][str(j)]) for j in range(1, 8)]
        segs.append((kf["start"], kf["t"], prev, pose))
        prev = pose
    if not segs:
        return []
    end = segs[-1][1]

    def at(t):
        p = segs[0][2]
        for t0, t1, a, b in segs:
            if t < t0:
                return p
            if t <= t1:
                f = (t - t0) / (t1 - t0) if t1 > t0 else 1.0
                return [x + (y - x) * f for x, y in zip(a, b)]
            p = b
        return p

    out, t = [], max(t_from, 0.0) + DENSE_S
    while t < end - 1e-9:
        out.append((round(t - t_from, 3), [round(v, 4) for v in at(t)]))
        t += DENSE_S
    out.append((round(end - t_from, 3), [round(v, 4) for v in at(end)]))
    return [p for p in out if p[0] > 0]


class FakeArms:
    """Records what would be sent. For tests, and for a laptop with no arms."""

    def __init__(self, measured=None):
        self.sent, self.cancelled = [], 0
        self._measured = measured if measured is not None else {"ARM_L": [0.0] * 7, "ARM_R": [0.0] * 7}

    def measured(self):
        return self._measured

    def send(self, slot, points):
        self.sent.append((slot, points))

    def cancel_all(self):
        self.cancelled += 1


class RosArms:
    """The real arms, over ROS 2 from the Thor (the arm controllers run in the `openarm` container).
    Needs ROS sourced, and control_msgs (`ros-jazzy-control-msgs`, installed by provision-runner.sh)."""

    def __init__(self, node_name="opendubs_show_runner"):
        import rclpy
        from control_msgs.action import FollowJointTrajectory
        from rclpy.action import ActionClient
        from rclpy.executors import SingleThreadedExecutor
        from sensor_msgs.msg import JointState
        from trajectory_msgs.msg import JointTrajectoryPoint
        from builtin_interfaces.msg import Duration

        self._FJT, self._Point, self._Duration = FollowJointTrajectory, JointTrajectoryPoint, Duration
        if not rclpy.ok():
            rclpy.init()
        self.node = rclpy.create_node(node_name)
        self.clients = {slot: ActionClient(self.node, FollowJointTrajectory,
                                           f"/{side}_controller/follow_joint_trajectory")
                        for slot, side in ARM_SIDES.items()}
        self.lock = threading.Lock()
        self._js, self._js_at = {}, 0.0
        self._goals = []
        self._gen = 0                  # bumped by cancel_all: a goal from an older generation is stale
        self.node.create_subscription(JointState, "/joint_states", self._on_js, 10)
        ex = SingleThreadedExecutor()
        ex.add_node(self.node)
        threading.Thread(target=ex.spin, daemon=True).start()

    def _on_js(self, m):
        with self.lock:
            self._js, self._js_at = dict(zip(m.name, m.position)), time.monotonic()

    def measured(self):
        """{"ARM_L": [7 signed degrees], "ARM_R": [...]} — or None if /joint_states is stale."""
        with self.lock:
            if time.monotonic() - self._js_at > JOINT_STATE_MAX_AGE_S:
                return None
            js = dict(self._js)
        out = {}
        for slot, side in ARM_SIDES.items():
            names = joint_names(side)
            if not all(n in js for n in names):
                return None
            out[slot] = [math.degrees(js[n]) for n in names]
        return out

    def send(self, slot, points):
        side = ARM_SIDES[slot]
        client = self.clients[slot]
        if not client.wait_for_server(timeout_sec=1.0):
            raise RuntimeError(f"{side} arm controller not available")
        goal = self._FJT.Goal()
        goal.trajectory.joint_names = joint_names(side)
        for t, deg in points:
            pt = self._Point()
            pt.positions = [math.radians(d) for d in deg]
            sec = int(t)
            pt.time_from_start = self._Duration(sec=sec, nanosec=int(round((t - sec) * 1e9)))
            goal.trajectory.points.append(pt)
        with self.lock:
            gen = self._gen
        fut = client.send_goal_async(goal)
        fut.add_done_callback(lambda f: self._accepted(f, gen))

    def _accepted(self, fut, gen):
        h = fut.result()
        if h is None or not h.accepted:
            return
        with self.lock:
            stale = gen != self._gen
            if not stale:
                self._goals.append(h)
        if stale:
            # Stopped between sending and the controller accepting: cancel it the moment it lands.
            h.cancel_goal_async()

    def cancel_all(self):
        """Stop every arm where it is: the controller holds its current state."""
        with self.lock:
            goals, self._goals = self._goals, []
            self._gen += 1
        for h in goals:
            h.cancel_goal_async()
