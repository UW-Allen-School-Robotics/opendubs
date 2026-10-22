#!/usr/bin/env python3
"""Drive bridge: base commands in, /odom out.

Before this node, ROS could neither drive the robot nor know where it was: nothing subscribed to
`/cmd_vel`, and Pinpoint odometry reached the StamPLC and stopped there as an `[ODO]` telemetry
line the dashboard rendered. Both are prerequisites for anything navigation-shaped — see
`notes/punch-list.md` §0d.

It speaks the StamPLC's UDP text protocol on 5005 directly, not through the dashboard. The
dashboard is a consumer of telemetry; putting a web backend in a motion control path would add a
failure mode for nothing.

Subscribes:
  /opendubs/drive/cmd_vel  geometry_msgs/Twist  → `DRIVE <fwd> <rot> <str>` on UDP 5005
                  Published ONLY by base_selector.py, which forwards the one program that holds the
                  base (/base/claim). If anything else publishes here, this node refuses all motion:
                  two writers on the wheels is the failure the selector exists to prevent.

Publishes:
  /odom           nav_msgs/Odometry       ← the `[ODO]` line (mm, rad → m, rad)
  TF odom→base_link

## Authority is NOT this node's business

It sends `DRIVE` whenever it has a fresh Twist and does not ask whether it is allowed to. That is
deliberate and it is the design in `docs/control-authority.md`: authority is a physical switch.
The StamPLC ignores host drive commands unless CH5 is in AUTO, and its arm gate additionally
requires the contactor closed, no e-stop, a live in-band RC link and a stable loop. A node that
tried to enforce authority in software would be a second answer to a question that must have one.

**The firmware is currently stricter than that, on purpose.** Until autonomy has been watched
enough to trust, AUTO also requires the **deadman held**, and scales the command by
`kAutoMaxUnit` (0.35) times the CH8 speed knob. So a planner commanding 1.0 m/s moves the robot
at roughly a third of that, and not at all unless someone is holding the switch. If motion looks
weak or absent under AUTO, that is this, and the StamPLC logs it on entry:
`[SAFE] AUTO engaged: cap=0.35 x CH8 knob, deadman REQUIRED`.

## The people guard

Before each `DRIVE` the bridge asks `people_guard.py` whether anything is within `guard_reach_m`
(0.5 m) of the robot's outline on the side it is moving toward, from both LIDARs (`/scan`,
`/scan_rear`) with the robot's own returns removed. If so, it sends zero instead. It **fails
closed**: a scan older than 0.5 s refuses every non-zero command. That keeps software motion (a
show's DANCE moves, later a planner) off the people standing near it. It is not the safety system,
which is the firmware gate above. It sees nothing above or below the LIDARs' plane, and no glass.

## Staleness is the stop

`DRIVE` carries a heartbeat: the firmware neutralises the drive if commands stop arriving
(`kRosCmdTimeoutMs`, 250 ms). So when commands go quiet this node **stops sending** rather than
repeating the last Twist. Letting the heartbeat lapse is how a dead planner stops the robot, and
re-sending a stale command would defeat it.
"""
from __future__ import annotations

import json
import math
import os
import re
import socket
import threading

import rclpy
from geometry_msgs.msg import Twist, TransformStamped
from nav_msgs.msg import Odometry
from rclpy.node import Node
from rclpy.qos import qos_profile_sensor_data
from rclpy.time import Time
from sensor_msgs.msg import LaserScan
from std_msgs.msg import String
from tf2_ros import Buffer, TransformBroadcaster, TransformListener

import drive_scale
import people_guard
from plc_peer import MOVED, ADOPTED, REFUSED_OWN, REFUSED_PIN, PeerGuard

PLC_PORT = int(os.environ.get("OPENDUBS_STAMPLC_UDP_PORT", "5005"))
# Where to send commands: OPENDUBS_STAMP_IP if this robot pins it (a systemd drop-in), otherwise
# learned from telemetry that passes plc_peer's checks, so a DHCP change needs no config edit.
STAMP_IP = os.environ.get("OPENDUBS_STAMP_IP", "").strip() or None
CMD_IN = "/opendubs/drive/cmd_vel"   # base_selector.py is its only publisher
_KV = re.compile(r"([a-z_]+)=(-?[0-9.]+)")


class DriveBridge(Node):
    def __init__(self) -> None:
        super().__init__("drive_bridge")

        # --- scaling and the software speed cap (drive_scale.py) ------------------------------
        # Software motion is capped at auto_cap of FULL command, per robot (/etc/opendubs/base.env),
        # capping every WHEEL, not each axis. The full scales are the speeds the robot reaches AT the
        # cap on each axis alone, measured per robot with short bursts at the cap. The cap is software
        # only: in MANUAL the firmware ignores host DRIVE, so the RC driver is not limited by it.
        self.declare_parameter("max_linear_mps", 0.5)
        self.declare_parameter("max_strafe_mps", 0.0)       # 0 = same as max_linear_mps
        self.declare_parameter("max_angular_rps", 1.0)
        self.declare_parameter("auto_cap", drive_scale.DEFAULT_AUTO_CAP)
        self.declare_parameter("fw_auto_max_unit", drive_scale.FW_AUTO_MAX_UNIT)
        self.declare_parameter("odom_frame", "odom")
        self.declare_parameter("base_frame", "base_link")
        # TF odom->base_link from the Pinpoint. Off when another node (a fusion EKF) publishes that
        # transform: two publishers of one transform make it jump between them.
        self.declare_parameter("publish_tf", True)
        self.max_lin = float(self.get_parameter("max_linear_mps").value)
        self.max_ang = float(self.get_parameter("max_angular_rps").value)
        self.max_str = float(self.get_parameter("max_strafe_mps").value) or self.max_lin
        self.auto_cap = float(self.get_parameter("auto_cap").value)
        self.fw_max = float(self.get_parameter("fw_auto_max_unit").value)
        self.odom_frame = str(self.get_parameter("odom_frame").value)
        self.base_frame = str(self.get_parameter("base_frame").value)
        self.publish_tf = bool(self.get_parameter("publish_tf").value)
        if not self.publish_tf:
            self.get_logger().info("publish_tf is false: /odom only, no TF odom->base_link from this node")
        drive_cap = min(1.0, self.auto_cap / self.fw_max) if self.fw_max > 0 else 0.0
        if self.auto_cap > self.fw_max:
            self.get_logger().warn(f"auto_cap {self.auto_cap} is above the firmware's own AUTO ceiling "
                                   f"{self.fw_max}: the firmware's ceiling applies")
        self.get_logger().info(
            f"software speed cap: {self.auto_cap:.0%} of full command on any wheel (DRIVE <= {drive_cap:.3f} "
            f"under the firmware's {self.fw_max} ceiling at CH8 max); full scale at the cap: forward "
            f"{self.max_lin} m/s, strafe {self.max_str} m/s, turn {self.max_ang} rad/s")

        self._guard = PeerGuard(STAMP_IP)
        self._peer: tuple[str, int] | None = (STAMP_IP, PLC_PORT) if STAMP_IP else None
        self._warned: set[str] = set()
        self._sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self._sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            self._sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEPORT, 1)
        except (AttributeError, OSError):
            # Without SO_REUSEPORT this node and the dashboard cannot both hear the StamPLC.
            self.get_logger().warn("SO_REUSEPORT unavailable — may conflict with opendubs-dash")
        self._sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        self._sock.bind(("", PLC_PORT))

        self.odom_pub = self.create_publisher(Odometry, "odom", qos_profile_sensor_data)
        self.tf = TransformBroadcaster(self)
        self.create_subscription(Twist, CMD_IN, self._on_cmd, 10)
        self._writers = 0                       # publishers on CMD_IN, checked at 1 Hz
        self.create_timer(1.0, self._count_writers)

        # Last commanded Twist and when it arrived. Sent at a fixed rate so the firmware's
        # heartbeat stays fed while a command is current — a planner publishing at 5 Hz should
        # not have the drive neutralise between its messages.
        self._cmd: tuple[float, float, float] | None = None
        self._cmd_ns = 0
        self._cmd_timeout_ns = 500_000_000      # 0.5 s: twice the firmware's own 250 ms
        self.create_timer(0.05, self._send_cmd)  # 20 Hz

        # The people guard (see the module docstring): both LIDARs, in base_link.
        self.declare_parameter("guard_reach_m", people_guard.REACH_M)
        self.declare_parameter("guard_scans", ["/scan", "/scan_rear"])
        self._reach = float(self.get_parameter("guard_reach_m").value)
        self._scans: dict[str, tuple[float | None, list]] = {
            t: (None, []) for t in self.get_parameter("guard_scans").value}
        self._mounts: dict[str, tuple[float, float, float]] = {}
        self._tf_buffer = Buffer()
        self._tf_listener = TransformListener(self._tf_buffer, self)
        self._refusing = ""
        for topic in self._scans:
            self.create_subscription(LaserScan, topic, lambda m, t=topic: self._on_scan(t, m),
                                     qos_profile_sensor_data)
        self.get_logger().info(f"people guard: {self._reach:.2f} m from the outline, on "
                               + ", ".join(self._scans))
        # Its state for the dance tracker's /dance/status: clear | stopped | blind, 5 Hz.
        self._guard_pub = self.create_publisher(String, "/people_guard/state", 10)
        self.create_timer(0.2, self._report_guard)

        threading.Thread(target=self._rx_loop, name="plc-rx", daemon=True).start()
        self.get_logger().info(
            f"listening for [ODO] on UDP {PLC_PORT}; DRIVE goes to "
            + (f"the pinned StamPLC {STAMP_IP}" if STAMP_IP else "the StamPLC learned from telemetry"))

    # ---- /cmd_vel → DRIVE ------------------------------------------------------------------
    def _on_cmd(self, msg: Twist) -> None:
        # Mecanum: x forward, y left, z yaw. The firmware's mixer owns wheel assignment and this
        # robot's handedness (robot_config.h), so send intent, capped (drive_scale.py), and no more.
        self._cmd = drive_scale.drive_units(msg.linear.x, msg.linear.y, msg.angular.z,
                                            self.max_lin, self.max_str, self.max_ang,
                                            self.auto_cap, self.fw_max)
        self._cmd_ns = self.get_clock().now().nanoseconds

    def _on_scan(self, topic: str, msg: LaserScan) -> None:
        frame = msg.header.frame_id
        if frame not in self._mounts:
            try:
                t = self._tf_buffer.lookup_transform(self.base_frame, frame, Time()).transform
            except Exception:                        # robot_state_publisher not up yet: stay blind
                return
            q = (t.rotation.x, t.rotation.y, t.rotation.z, t.rotation.w)
            self._mounts[frame] = (t.translation.x, t.translation.y, math.atan2(
                2.0 * (q[3] * q[2] + q[0] * q[1]), 1.0 - 2.0 * (q[1] * q[1] + q[2] * q[2])))
        tx, ty, yaw = self._mounts[frame]
        pts = people_guard.scan_points(msg.ranges, msg.angle_min, msg.angle_increment, tx, ty, yaw)
        self._scans[topic] = (self.get_clock().now().nanoseconds / 1e9, pts)

    def _report_guard(self) -> None:
        now = self.get_clock().now().nanoseconds / 1e9
        stale = [t for t, (stamp, _p) in sorted(self._scans.items())
                 if stamp is None or now - stamp > people_guard.STALE_S]
        if stale:
            state, why = "blind", f"{', '.join(stale)} not current"
        elif self._refusing == "near":
            state, why = "stopped", "something close in the direction of travel"
        else:
            state, why = "clear", None
        self._guard_pub.publish(String(data=json.dumps({"state": state, "why": why})))

    def _count_writers(self) -> None:
        n = self.count_publishers(CMD_IN)
        if n > 1 and n != self._writers:
            self.get_logger().error(
                f"{n} publishers on {CMD_IN}; only base_selector may publish there. Refusing all motion "
                "until there is one (claim the base on /base/claim instead)")
        elif n <= 1 and self._writers > 1:
            self.get_logger().info(f"one publisher on {CMD_IN} again")
        self._writers = n

    def _send_cmd(self) -> None:
        if self._cmd is None or self._peer is None:
            return
        if self.get_clock().now().nanoseconds - self._cmd_ns > self._cmd_timeout_ns:
            # Stale. STOP SENDING — do not repeat the last command. The firmware neutralises on
            # heartbeat loss, and that is exactly how a dead planner is meant to stop the robot.
            self._cmd = None
            self.get_logger().warn("base commands went stale — letting the firmware heartbeat lapse")
            return
        fwd, rot, str_ = self._cmd
        if self._writers > 1:
            fwd = rot = str_ = 0.0              # two writers: see _count_writers
        ok, why = people_guard.verdict(self._scans, self.get_clock().now().nanoseconds / 1e9,
                                       fwd, str_, rot, reach=self._reach)
        if not ok:
            fwd = rot = str_ = 0.0
            kind = "blind" if "not current" in why else "near"
            if kind != self._refusing:               # log each change, not every 50 ms
                self.get_logger().warn(f"people guard: holding the base still, {why}")
            self._refusing = kind
        elif self._refusing:
            self.get_logger().info("people guard: clear, moving again")
            self._refusing = ""
        try:
            self._sock.sendto(f"DRIVE {fwd:.3f} {rot:.3f} {str_:.3f}\n".encode(), self._peer)
        except OSError as e:
            self.get_logger().warn(f"DRIVE send failed: {e}")

    # ---- [ODO] → /odom + TF ----------------------------------------------------------------
    def _rx_loop(self) -> None:
        while True:
            try:
                data, addr = self._sock.recvfrom(4096)
            except OSError:
                continue
            verdict = self._guard.offer(data, addr[0])
            if not PeerGuard.usable(verdict):
                # Report each kind of refusal once per address, not per packet.
                key = f"{verdict}:{addr[0]}"
                if verdict in (REFUSED_OWN, REFUSED_PIN) and key not in self._warned:
                    self._warned.add(key)
                    self.get_logger().warn(
                        f"ignoring UDP {PLC_PORT} traffic from {addr[0]}: "
                        + ("one of this host's own addresses" if verdict == REFUSED_OWN
                           else f"not the pinned StamPLC {STAMP_IP}"))
                continue
            if verdict in (ADOPTED, MOVED):
                (self.get_logger().info if verdict == ADOPTED else self.get_logger().warn)(
                    f"StamPLC peer {'is' if verdict == ADOPTED else 'MOVED to'} {addr[0]}")
            self._peer = (addr[0], PLC_PORT)
            for line in data.decode("utf-8", "replace").splitlines():
                if line.startswith("[ODO] "):
                    try:
                        self._publish_odom(dict(_KV.findall(line)))
                    except Exception as e:      # never let one malformed line kill the thread
                        self.get_logger().warn(f"bad [ODO] line: {e}")

    def _publish_odom(self, kv: dict[str, str]) -> None:
        # ok=0 means the last Pinpoint poll failed and the pose is a CACHED value. Publishing it
        # as current would feed a stale pose into TF, which is worse than publishing nothing:
        # a consumer can detect missing odometry but not a confidently wrong one.
        if kv.get("ok") != "1":
            return

        x_mm, y_mm = float(kv["x"]), float(kv["y"])
        h = float(kv["h"])                      # radians, continuous (not wrapped)
        vx_mm, vy_mm = float(kv.get("vx", 0.0)), float(kv.get("vy", 0.0))
        vh = float(kv.get("vh", 0.0))

        now = self.get_clock().now().to_msg()
        o = Odometry()
        o.header.stamp = now
        o.header.frame_id = self.odom_frame
        o.child_frame_id = self.base_frame
        o.pose.pose.position.x = x_mm / 1000.0   # [ODO] is mm; ROS is metres
        o.pose.pose.position.y = y_mm / 1000.0
        o.pose.pose.orientation.z = math.sin(h / 2.0)
        o.pose.pose.orientation.w = math.cos(h / 2.0)
        # The Pinpoint reports vx/vy in the odom (field) frame; measured 2026-10-21 by turning
        # 180° and driving forward, which read vx < 0. Odometry's twist is in child_frame_id
        # (base_link), so rotate by -h.
        c, s = math.cos(h), math.sin(h)
        o.twist.twist.linear.x = (c * vx_mm + s * vy_mm) / 1000.0
        o.twist.twist.linear.y = (-s * vx_mm + c * vy_mm) / 1000.0
        o.twist.twist.angular.z = vh
        # Covariance is NOT characterised. These are placeholders chosen to be pessimistic so a
        # fuser does not over-trust this source; replace them with measured values before any
        # EKF weighting depends on them.
        for i, v in ((0, 0.02), (7, 0.02), (35, 0.05)):
            o.pose.covariance[i] = v
        for i, v in ((0, 0.05), (7, 0.05), (35, 0.10)):
            o.twist.covariance[i] = v
        self.odom_pub.publish(o)
        if not self.publish_tf:
            return

        t = TransformStamped()
        t.header.stamp = now
        t.header.frame_id = self.odom_frame
        t.child_frame_id = self.base_frame
        t.transform.translation.x = x_mm / 1000.0
        t.transform.translation.y = y_mm / 1000.0
        t.transform.rotation.z = math.sin(h / 2.0)
        t.transform.rotation.w = math.cos(h / 2.0)
        self.tf.sendTransform(t)


def main() -> None:
    rclpy.init()
    node = DriveBridge()
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        node.destroy_node()
        rclpy.shutdown()


if __name__ == "__main__":
    main()
