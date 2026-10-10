"""Live arm output for the show runner (workplan 5.6) — the ONLY code here that moves the real arms.

Decided 2026-10-08: the runner sends each arm's routine as ONE FollowJointTrajectory goal, the same
action `openarm-up park` uses, and only while its own checks hold (CH5 AUTO, the deadman held, the
arms active — DEC-21 permits shows in AUTO). Ending a show early cancels the goal, and the controller
holds where the arm is (runner.ABORT_STATE). An e-stop needs nothing from here: the contactor has
already cut the arms' power. Grippers are NOT driven until each side is calibrated (plan 6.2).

What the arm does, and why it matches what was validated:
  * The validator and the twin both start every arm at ZERO (cue.py speed check, sim_preview.arm_pose),
    so before the show the arm is moved there — from where it is MEASURED to be, slowly (runner PREPPING).
  * Then it follows the show's keyframes, each reached at its time, along motion.py's smoothed path
    (since 2026-10-08: a joint slows to zero where it reverses or holds, instead of flipping speed —
    the speed test's finding). The points are sent densely (every DENSE_S), so the path is that curve
    whatever interpolation the controller uses.
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

import motion

ARM_SIDES = {"ARM_L": "left", "ARM_R": "right"}
GRIP_SIDES = {"GRIP_L": "left", "GRIP_R": "right"}
# The gripper controllers (the Arms lane's contract, 2026-10-10): GripperCommand on
# /<side>_gripper_controller/gripper_cmd, position in the finger joint's raw METRES, max_effort 5.0
# (not enforced at the motor — position only). With allow_stalling false a close that meets something
# ABORTS after ~1 s: that abort is the "something is in there" signal.
GRIP_MAX_EFFORT = 5.0
DENSE_S = 0.02           # trajectory point spacing: fine enough to follow motion.py's curve
JOINT_STATE_MAX_AGE_S = 0.5


def joint_names(side: str) -> list:
    return [f"openarm_{side}_joint{j}" for j in range(1, 8)]


def track_points(keyframes: list, t_from: float = 0.0, start=None) -> list:
    """The arm's path as [(seconds from now, [7 signed degrees])], from show time `t_from`.

    Each keyframe reached at its `t`, holding between a keyframe and the next one's `start`, along
    the SMOOTHED path of motion.py (the same one the twins play): a joint slows to zero where it
    reverses or holds rather than flipping speed. Sampled every DENSE_S, finely enough that the
    straight lines between samples follow the curve. The arm starts at `start` (signed degrees;
    default zero, where the validator assumes it is)."""
    if not keyframes:
        return []
    path = motion.Path(motion.knots(keyframes, "pose_signed", start))
    end = path.end
    out, t = [], max(t_from, 0.0) + DENSE_S
    while t < end - 1e-9:
        out.append((round(t - t_from, 3), [round(v, 4) for v in path.at(t)]))
        t += DENSE_S
    out.append((round(end - t_from, 3), [round(v, 4) for v in path.at(end)]))
    return [p for p in out if p[0] > 0]


class FakeArms:
    """Records what would be sent. For tests, and for a laptop with no arms."""

    def __init__(self, measured=None):
        self.sent, self.cancelled = [], 0
        self._measured = measured if measured is not None else {"ARM_L": [0.0] * 7, "ARM_R": [0.0] * 7}
        self.grips, self.results = [], {}          # (slot, metres) sent; slot -> a goal's final status
        self.goal_status = {}                      # goal id -> that goal's own final status
        self.grips_missing = []                    # sides whose gripper controller is "not up"

    def grips_ready(self):
        return list(self.grips_missing)

    def send_grip(self, slot, metres):
        self.grips.append((slot, round(float(metres), 5)))
        return len(self.grips)

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

        from rclpy.signals import SignalHandlerOptions

        self._FJT, self._Point, self._Duration = FollowJointTrajectory, JointTrajectoryPoint, Duration
        if not rclpy.ok():
            # No ROS signal handlers: by default rclpy takes SIGTERM, shuts ROS down and leaves the
            # process running, blind (no /joint_states, so every run is refused) until systemd
            # SIGKILLs it 90 s later — found on the robot 2026-10-08, on a deploy's restart. Without
            # them SIGTERM ends the process at once, as a service's stop should.
            rclpy.init(signal_handler_options=SignalHandlerOptions.NO)
        self.node = rclpy.create_node(node_name)
        self.clients = {slot: ActionClient(self.node, FollowJointTrajectory,
                                           f"/{side}_controller/follow_joint_trajectory")
                        for slot, side in ARM_SIDES.items()}
        try:
            from control_msgs.action import GripperCommand
            self._GC = GripperCommand
            self.grip_clients = {slot: ActionClient(self.node, GripperCommand, f"/{side}_gripper_controller/gripper_cmd")
                                 for slot, side in GRIP_SIDES.items()}
        except ImportError:                     # no GripperCommand here: the runner sends no gripper cues
            self._GC, self.grip_clients = None, {}
        self.lock = threading.Lock()
        self._js, self._js_at = {}, 0.0
        self._jv, self._je = {}, {}          # velocity and effort, when the arm stack publishes them
        self.results = {}                    # slot -> the last goal's final status (4 ok, 5 cancelled, 6 aborted)
        self.goal_status = {}                # goal id -> that goal's OWN final status, whatever was sent since
        self._latest = {}                    # slot -> the id of the goal most recently sent to it
        self._seq = 0
        self._goals = []
        self._gen = 0                  # bumped by cancel_all: a goal from an older generation is stale
        self.node.create_subscription(JointState, "/joint_states", self._on_js, 10)
        ex = SingleThreadedExecutor()
        ex.add_node(self.node)
        threading.Thread(target=ex.spin, daemon=True).start()

    def _on_js(self, m):
        with self.lock:
            self._js, self._js_at = dict(zip(m.name, m.position)), time.monotonic()
            self._jv = dict(zip(m.name, m.velocity)) if len(m.velocity) == len(m.name) else {}
            self._je = dict(zip(m.name, m.effort)) if len(m.effort) == len(m.name) else {}

    def sample(self, side):
        """(monotonic time, [7 deg], [7 deg/s] or None, [7 N·m] or None) for one arm, or None if stale.
        Controller sense (signed), the same sense `send` takes."""
        names = joint_names(side)
        with self.lock:
            if time.monotonic() - self._js_at > JOINT_STATE_MAX_AGE_S or not all(n in self._js for n in names):
                return None
            pos = [math.degrees(self._js[n]) for n in names]
            vel = [math.degrees(self._jv[n]) for n in names] if all(n in self._jv for n in names) else None
            eff = [float(self._je[n]) for n in names] if all(n in self._je for n in names) else None
            return self._js_at, pos, vel, eff

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
        return self._send_goal(client, slot, goal)

    def _send_goal(self, client, slot, goal):
        """Send one goal; returns its id. Its final status lands in goal_status[id], and in
        results[slot] only while it is still the slot's latest goal — a superseded goal's late result
        never overwrites a newer one's."""
        with self.lock:
            gen = self._gen
            self._seq += 1
            gid = self._latest[slot] = self._seq
            self.results[slot] = None
        fut = client.send_goal_async(goal)
        fut.add_done_callback(lambda f: self._accepted(f, gen, slot, gid))
        return gid

    def _record(self, slot, gid, status):
        with self.lock:
            self.goal_status[gid] = status
            if self._latest.get(slot) == gid:
                self.results[slot] = status

    def _accepted(self, fut, gen, slot, gid):
        h = fut.result()
        if h is None or not h.accepted:
            self._record(slot, gid, "REJECTED")
            return
        with self.lock:
            stale = gen != self._gen
            if not stale:
                self._goals.append(h)
        h.get_result_async().add_done_callback(lambda f: self._done(f, slot, gid))
        if stale:
            # Stopped between sending and the controller accepting: cancel it the moment it lands.
            h.cancel_goal_async()

    def _done(self, fut, slot, gid):
        try:
            status = fut.result().status
        except Exception:  # noqa: BLE001
            status = "UNKNOWN"
        self._record(slot, gid, status)

    def grips_ready(self):
        """The sides whose gripper controller is NOT up. Never blocks: the clients were made when the
        runner started, so discovery is long done — the runner asks once, as a show starts."""
        return [side for slot, side in GRIP_SIDES.items()
                if slot not in self.grip_clients or not self.grip_clients[slot].server_is_ready()]

    def send_grip(self, slot, metres):
        """One gripper position goal, in the controller's metres (grippers.py maps the show's 0..1);
        returns its goal id. goal_status[id] gets its final status: 4 reached, 6 aborted (a STALL
        on a close). Never blocks the dispatch loop: an absent controller raises at once."""
        client = self.grip_clients.get(slot)
        if client is None or not client.server_is_ready():
            raise RuntimeError(f"{GRIP_SIDES[slot]} gripper controller not available")
        goal = self._GC.Goal()
        goal.command.position = float(metres)
        goal.command.max_effort = GRIP_MAX_EFFORT
        return self._send_goal(client, slot, goal)

    def cancel_all(self):
        """Stop every arm and gripper where it is: the controllers hold their current state."""
        with self.lock:
            goals, self._goals = self._goals, []
            self._gen += 1
        for h in goals:
            h.cancel_goal_async()
