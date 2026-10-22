#!/usr/bin/env python3
"""The BASE on a live run: a show's DANCE moves (small dance steps that return the base to where they
started) played on the real robot, through the Thor lane's base tracker (notes/design/choreo-dance-live.md).

    runner.py palette ... --base ros       # live runs send DANCE to the tracker
    runner.py palette ... --base none      # they do not (the default): DANCE plays in the twin only

THE INTERFACE (agreed with the Thor lane; its tracker closes the loop on /odom and owns the people guard):
  /dance/origin  std_msgs/Empty         once per run, at the show's t0, the base at rest: the tracker
                                        latches the current /odom pose as the show's origin
  /dance/target  nav_msgs/Odometry      20 Hz while a DANCE move plays, and for SETTLE_S after it:
                                        pose = the intended base pose in the origin frame ("dance_origin"),
                                        twist = the intended BODY velocity ("base_link"), as feed-forward.
                                        A stream older than 250 ms means stop; every move ends at home
  /dance/stop    std_msgs/Empty         on EVERY end: at once on a stop, an abort or an e-stop; after the last
                                        move's settle on a completion. It also hands the base back (the
                                        tracker holds it as the owner "show"), so another program may claim it
  /dance/status  std_msgs/String JSON   from the tracker, ~5 Hz: {"ready", "why", "owner", "origin", "origin_seq",
                                        "mode": idle|tracking|settling|holding, "guard": clear|stopped|blind,
                                        "err_m", "err_deg"}. `ready` covers /odom, the guard, and no OTHER
                                        program holding the base (`owner`: the base has one software owner at a
                                        time; the tracker is "show"); whether an origin is latched is `origin`
                                        (a run latches its own at t0).
                                        "holding": the error passed 0.3 m or 20 deg, so the base stopped
                                        where it is (never chases); "blind": a LIDAR scan is over 0.5 s
                                        old, so it refuses to move

Nothing here gates motion: the base moves only in CH5 AUTO with the deadman held (the firmware, its 0.35
command cap), and the runner sends targets only on a live run, after the GO. A tracker that is not ready
at preflight means the show runs WITHOUT its base moves, saying why — or, for a show with
`dance: {required: true}`, is refused. The people guard is the tracker's; the runner only logs it.
"""
from __future__ import annotations

import json
import math
import threading
import time

import cue

RATE_HZ = 20.0
SETTLE_S = 1.5          # after each move, the home target held so the tracker can close the residual
STATUS_MAX_AGE_S = 1.0  # a tracker status older than this is no tracker


def dance_command(events: list, t: float) -> dict | None:
    """The base target at show time t: {"x", "y", "yaw" (rad), "vx", "vy", "w"} — the pose in the show's
    origin frame and the body velocity — while a DANCE move plays or settles; None between moves (nothing
    is sent: the tracker stops). Every library move integrates to zero, so a settling target is home."""
    for e in events:
        c = e["cue"]
        if c.get("move") not in cue.DANCE_MOVES:
            continue
        secs = float(c.get("secs", cue.DANCE_DEFAULT_SECS))
        start = e["start"]
        if not start <= t < start + secs + SETTLE_S:
            continue
        if t >= start + secs:
            return {"x": 0.0, "y": 0.0, "yaw": 0.0, "vx": 0.0, "vy": 0.0, "w": 0.0, "settle": True}
        amp = float(c.get("amp", 1.0))
        v = cue.AUTO_MAX_UNIT * amp * cue.BASE_M_PER_S_FULL
        vs = cue.AUTO_MAX_UNIT * amp * cue.BASE_M_PER_S_STRAFE
        w = cue.AUTO_MAX_UNIT * amp * cue.BASE_RAD_PER_S_FULL
        x = y = yaw = 0.0
        left = t - start
        body = (0.0, 0.0, 0.0)
        for frac, fwd, strafe, rot in cue.DANCE_MOVES[c["move"]]:
            dt = min(frac * secs, left)
            if dt <= 0:
                break
            body = (fwd * v, strafe * vs, rot * w)
            x += (body[0] * math.cos(yaw) - body[1] * math.sin(yaw)) * dt
            y += (body[0] * math.sin(yaw) + body[1] * math.cos(yaw)) * dt
            yaw += body[2] * dt
            left -= dt
        return {"x": x, "y": y, "yaw": yaw, "vx": body[0], "vy": body[1], "w": body[2], "settle": False}
    return None


class RosBase:
    """The tracker's topics, on their own rclpy node (the runner already runs under ROS for the arms)."""

    def __init__(self, node_name="opendubs_choreo_base"):
        import rclpy
        from nav_msgs.msg import Odometry
        from rclpy.executors import SingleThreadedExecutor
        from std_msgs.msg import Empty, String
        if not rclpy.ok():
            from rclpy.signals import SignalHandlerOptions
            rclpy.init(signal_handler_options=SignalHandlerOptions.NO)
        self.node = rclpy.create_node(node_name)
        self._Odometry, self._Empty = Odometry, Empty
        self.p_origin = self.node.create_publisher(Empty, "/dance/origin", 10)
        self.p_target = self.node.create_publisher(Odometry, "/dance/target", 10)
        self.p_stop = self.node.create_publisher(Empty, "/dance/stop", 10)
        self._status, self._status_at = None, None
        self.node.create_subscription(String, "/dance/status", self._on_status, 10)
        ex = SingleThreadedExecutor()
        ex.add_node(self.node)
        threading.Thread(target=ex.spin, daemon=True, name="dance-ros").start()

    def _on_status(self, msg):
        try:
            self._status, self._status_at = json.loads(msg.data), time.monotonic()
        except ValueError:
            pass

    def status(self) -> dict | None:
        """The tracker's latest status, or None when it has said nothing for STATUS_MAX_AGE_S."""
        if self._status_at is None or time.monotonic() - self._status_at > STATUS_MAX_AGE_S:
            return None
        return self._status

    def origin(self):
        self.p_origin.publish(self._Empty())

    def stop(self):
        self.p_stop.publish(self._Empty())

    def target(self, c: dict):
        m = self._Odometry()
        m.header.stamp = self.node.get_clock().now().to_msg()
        m.header.frame_id, m.child_frame_id = "dance_origin", "base_link"
        m.pose.pose.position.x, m.pose.pose.position.y = c["x"], c["y"]
        m.pose.pose.orientation.z, m.pose.pose.orientation.w = math.sin(c["yaw"] / 2), math.cos(c["yaw"] / 2)
        m.twist.twist.linear.x, m.twist.twist.linear.y, m.twist.twist.angular.z = c["vx"], c["vy"], c["w"]
        self.p_target.publish(m)


class BaseOutput:
    """This runner's base output: `link` is RosBase on the robot (tests pass a fake with the same
    status/origin/target/stop)."""

    def __init__(self, link):
        self.link = link

    def readiness(self) -> dict:
        """{"ready": bool, "why"} from the tracker's status, in operator words."""
        try:
            st = self.link.status()
        except Exception as e:  # noqa: BLE001
            return {"ready": False, "why": f"the base tracker could not be asked: {type(e).__name__}: {e}"}
        if st is None:
            return {"ready": False, "why": "the base tracker is not running (no /dance/status)"}
        if not st.get("ready"):
            return {"ready": False, "why": f"the base tracker is not ready: {st.get('why') or 'no reason given'}"}
        return {"ready": True, "why": None}

    def make_run(self, tl: dict, log=None) -> "BaseRun":
        return BaseRun(self.link, (tl.get("tracks") or {}).get("DANCE") or [], log)


class BaseRun:
    """One live run's base: the origin at t0, then each move's targets on the show's clock, in its own
    thread (the runner's loop never waits on ROS), and a stop on every end — after the last settle on a
    completion — which also releases the base."""

    def __init__(self, link, events, log=None):
        self.link, self.events, self.log = link, events, log or (lambda *a, **k: None)
        self.sent, self.done, self.failed = 0, False, None
        self._clock, self._stop, self._thread, self._origin = None, threading.Event(), None, False
        self._finishing = None                 # wall time by which a completed run's last settle ends
        self._last_guard = self._last_mode = None

    def begin(self):
        self._thread = threading.Thread(target=self._loop, daemon=True, name="dance-base")
        self._thread.start()

    def ready(self) -> bool:
        return True

    def go(self, show_clock):
        self._clock = show_clock

    def _loop(self):
        try:
            while not self._stop.wait(1.0 / RATE_HZ):
                if self._clock is None:
                    continue
                t = self._clock()
                if t < 0:
                    continue
                if not self._origin:
                    self.link.origin()                     # the base is at rest at the show's t0
                    self._origin = True
                    self.log("base", note="the show's origin is latched; DANCE moves follow it")
                c = self.events and dance_command(self.events, t)
                if self._finishing is not None and (not c or time.monotonic() > self._finishing):
                    self._stop_base()                      # a completed show: its last move has settled
                    break
                if c:
                    self.link.target(c)
                    self.sent += 1
                self._watch()
        except Exception as e:  # noqa: BLE001  the base output never crashes the run; the tracker stops on silence
            self.failed = f"{type(e).__name__}: {e}"
            self.log("base_error", error=self.failed)

    def _stop_base(self):
        try:
            self.link.stop()
        except Exception as e:  # noqa: BLE001
            self.log("base_error", error=f"stop: {type(e).__name__}: {e}")

    def _watch(self):
        """Log the tracker's guard and mode as they change; the tracker decides what the base does."""
        st = self.link.status() or {}
        g, m = st.get("guard"), st.get("mode")
        if g is not None and g != self._last_guard:
            self._last_guard = g
            self.log("base", guard=g, err_m=st.get("err_m"), err_deg=st.get("err_deg"),
                     note={"stopped": "the people guard stopped the base: someone is in its way",
                           "blind": "the people guard is blind (a LIDAR scan is late): the base will not move",
                           "clear": "the base's way is clear"}.get(g, g))
        if m is not None and m != self._last_mode:
            self._last_mode = m
            if m == "holding":
                self.log("base_warning", err_m=st.get("err_m"), err_deg=st.get("err_deg"),
                         note="the base is off its dance by more than the tracker allows: it holds where it is")

    def end(self, status: str):
        if self.done:
            return
        self.done = True
        if self._thread is None:
            self._stop.set()
            return                                          # never begun: nothing was sent
        if status == "COMPLETED":
            # the show's end may fall inside a move's settle: finish it (home, held still) — never past
            # SETTLE_S — then stop, which releases the base
            self._finishing = time.monotonic() + SETTLE_S
            self.log("base", note=f"DANCE targets end after the last move settles ({self.sent} sent so far)")
            return
        self._stop.set()
        self._stop_base()                                    # any other end: stop at once
        self.log("base", note=f"DANCE targets ended ({self.sent} sent)", end=status)

    def state(self) -> dict:
        return {"output": "base", "sent": self.sent, "error": self.failed, "guard": self._last_guard}
