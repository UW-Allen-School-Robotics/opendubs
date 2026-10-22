#!/usr/bin/env python3
"""Steer the base along a show's DANCE moves, closed-loop on /odom.

The show runner (software/choreo) says WHERE the base should be and WHEN; this node gets it there.
The runner owns the moves; this node owns the steering. The interface was agreed between the
choreography and Thor-stack lanes (2026-10-21):

  /dance/origin  std_msgs/Empty         once per run, base at rest: latch the current /odom pose
                                        as the show's origin (frame dance_origin)
  /dance/target  nav_msgs/Odometry      20 Hz during a DANCE cue: pose = where the base should be,
                                        relative to the origin (x fwd, y left, yaw); twist = the
                                        intended body velocity (feed-forward). After each cue the
                                        runner streams the home target, zero twist, for 1.5 s
  /dance/stop    std_msgs/Empty         stop at once and drop the stream until the next target
  /dance/status  std_msgs/String (JSON) 5 Hz: {"ready", "why", "origin", "origin_seq", "tracking",
                                        "owner", "mode", "guard", "err_m", "err_deg"}. ready = /odom
                                        and the guard are fit to move and no OTHER program holds
                                        the base; "origin" says whether this run's origin is
                                        latched (a run latches it at its t0); "owner" is who holds
                                        the base now (null, "show", or another program)
  /base/show/cmd_vel  geometry_msgs/Twist   out, only while tracking and only while it holds the base

THE BASE HAS ONE OWNER AT A TIME (base_selector.py). This node is the owner "show": it claims the
base on /base/claim when a run latches its origin, renews the claim each second while the show
runs, and releases it on /dance/stop or after SHOW_IDLE_S without a target. If another program
(a navigation stack) holds the base, the claim is refused, status says ready=false with why, and
the show runs without its base moves.

Command = feed-forward + gain x error (in the body frame), plus an optional per-robot strafe trim,
limited to the software speed cap the same way drive_bridge limits it (drive_scale.py): it never asks
for more than the cap. A strafe that bleeds forward or yaws is pulled back by the error terms.
A small correction can stall on static friction (opendubs2 stopped 3.8 cm short of home after a
sway), so while SETTLING (no feed-forward on that axis) a correction for more than SETTLE_M or
SETTLE_RAD is raised to at least OPENDUBS_MIN_LIN (m/s) or OPENDUBS_MIN_ANG (rad/s), per robot in
/etc/opendubs/base.env; 0, the default, turns it off. Never during a move: there a raised command
could push the base further past a target it is already ahead of.
It never chases: if the error passes 0.3 m or 20 deg (a people-guard stop, a slip) it HOLDS the
base still and says so, rather than lunging back. A target older than 250 ms, a stale /odom, or
/dance/stop sends one zero Twist and then nothing, so drive_bridge and the firmware stop too.

The safety gates are not here: the firmware moves the base for software only in CH5 AUTO with the
deadman held, and drive_bridge's people guard (people_guard.py) refuses motion toward anyone close.
"""
import json
import math
import os

import drive_scale

TARGET_STALE_S = 0.25
OWNER = "show"
SHOW_IDLE_S = 60.0             # a run with no target for this long gives the base back
CLAIM_TTL_S = 3.0
ODOM_STALE_S = 0.3
HOLD_M, HOLD_RAD = 0.3, math.radians(20)
K_LIN, K_ANG = 1.5, 1.5          # 1/s: close a 10 cm error at 15 cm/s, within the cap; per robot as
                                 # OPENDUBS_K_LIN / OPENDUBS_K_ANG in /etc/opendubs/base.env
SETTLE_M, SETTLE_RAD = 0.015, math.radians(1.5)   # closer than this, the minimum command stops


def wrap(a):
    return math.atan2(math.sin(a), math.cos(a))


def relative(origin, pose):
    """`pose` (x, y, yaw in odom) expressed in the frame of `origin` (x, y, yaw in odom)."""
    ox, oy, oyaw = origin
    c, s = math.cos(-oyaw), math.sin(-oyaw)
    dx, dy = pose[0] - ox, pose[1] - oy
    return (c * dx - s * dy, s * dx + c * dy, wrap(pose[2] - oyaw))


def body_error(target, current):
    """Target minus current, both in the origin frame, as (forward, left, yaw) in the BODY frame."""
    c, s = math.cos(-current[2]), math.sin(-current[2])
    dx, dy = target[0] - current[0], target[1] - current[1]
    return (c * dx - s * dy, s * dx + c * dy, wrap(target[2] - current[2]))


def command(err, ff, full_x, full_y, full_w, k_lin=K_LIN, k_ang=K_ANG, fwd_bleed=(0.0, 0.0), yaw_bleed=(0.0, 0.0),
            min_lin=0.0, min_ang=0.0):
    """(vx, vy, wz) = feed-forward + gain x error, with the strafe trim, never past the software cap.

    full_* are the speeds at the cap (drive_scale.py). The result is scaled, keeping its direction, so
    that forward + strafe + turn, each in units of its full scale, stay within 1: what drive_bridge
    would allow, so the tracker never asks for more than the cap.

    min_lin / min_ang (0 = off): while settling, a correction beyond SETTLE_M / SETTLE_RAD is raised
    to at least this speed, keeping its direction, so it overcomes static friction."""
    vx = ff[0] + k_lin * err[0]
    vy = ff[1] + k_lin * err[1]
    wz = ff[2] + k_ang * err[2]
    v = math.hypot(vx, vy)
    if min_lin > 0 and ff[0] == ff[1] == 0.0 and math.hypot(err[0], err[1]) > SETTLE_M and 0 < v < min_lin:
        vx, vy = vx * min_lin / v, vy * min_lin / v
    if min_ang > 0 and ff[2] == 0.0 and abs(err[2]) > SETTLE_RAD and abs(wz) < min_ang:
        wz = math.copysign(min_ang, wz)
    vx, vy, wz = drive_scale.strafe_trim(vx, vy, wz, fwd_bleed, yaw_bleed)
    ux, uy, uw = drive_scale.l1_limit(vx / full_x if full_x > 0 else 0.0,
                                      vy / full_y if full_y > 0 else 0.0,
                                      wz / full_w if full_w > 0 else 0.0)
    return ux * full_x, uy * full_y, uw * full_w


def over_limit(err, hold_m=HOLD_M, hold_rad=HOLD_RAD):
    return math.hypot(err[0], err[1]) > hold_m or abs(err[2]) > hold_rad


def yaw_of(q):
    return math.atan2(2.0 * (q.w * q.z + q.x * q.y), 1.0 - 2.0 * (q.y * q.y + q.z * q.z))


def main():
    import rclpy
    from geometry_msgs.msg import Twist
    from nav_msgs.msg import Odometry
    from rclpy.node import Node
    from rclpy.qos import DurabilityPolicy, QoSProfile, ReliabilityPolicy, qos_profile_sensor_data
    from std_msgs.msg import Empty, String

    rclpy.init()
    node = Node("dance_tracker")
    log = node.get_logger()
    # drive_bridge's full scale: the speed the robot reaches at DRIVE 1.0 (in AUTO, the ceiling).
    # The speeds at the software cap, per robot (/etc/opendubs/base.env, shared with drive_bridge).
    vmax = float(os.environ.get("OPENDUBS_MAX_LIN", "0.5"))
    smax = float(os.environ.get("OPENDUBS_MAX_STRAFE", "0") or 0) or vmax
    wmax = float(os.environ.get("OPENDUBS_MAX_ANG", "1.0"))
    fwd_bleed = drive_scale.bleed_from_env(os.environ, "FWD")    # (left, right)
    yaw_bleed = drive_scale.bleed_from_env(os.environ, "YAW")
    min_lin = float(os.environ.get("OPENDUBS_MIN_LIN", "0") or 0)
    min_ang = float(os.environ.get("OPENDUBS_MIN_ANG", "0") or 0)
    k_lin = float(os.environ.get("OPENDUBS_K_LIN", "") or K_LIN)
    k_ang = float(os.environ.get("OPENDUBS_K_ANG", "") or K_ANG)
    st = {"odom": None, "odom_t": None, "origin": None, "seq": 0, "target": None, "target_t": None,
          "mode": "idle", "moving": False, "guard": "blind", "guard_why": "no guard state yet", "err": (0, 0, 0),
          "holder": None, "active": False, "active_t": 0.0}
    now = lambda: node.get_clock().now().nanoseconds / 1e9
    cmd = node.create_publisher(Twist, f"/base/{OWNER}/cmd_vel", 10)
    claim_pub = node.create_publisher(String, "/base/claim", 10)
    release_pub = node.create_publisher(String, "/base/release", 10)

    def claim():
        claim_pub.publish(String(data=json.dumps({"owner": OWNER, "ttl_s": CLAIM_TTL_S})))

    def release():
        if st["active"]:
            release_pub.publish(String(data=json.dumps({"owner": OWNER})))
        st["active"] = False
    status = node.create_publisher(String, "/dance/status", 10)

    def on_odom(m):
        st["odom"] = (m.pose.pose.position.x, m.pose.pose.position.y, yaw_of(m.pose.pose.orientation))
        st["odom_t"] = now()

    def on_origin(_m):
        if st["odom"] is None or now() - st["odom_t"] > ODOM_STALE_S:
            log.warn("/dance/origin with no current /odom: not latched")
            return
        st["origin"], st["seq"] = st["odom"], st["seq"] + 1
        st["target"] = None
        st["mode"] = "idle"
        st["active"], st["active_t"] = True, now()
        claim()
        log.info(f"origin #{st['seq']} latched at odom ({st['odom'][0]:.3f}, {st['odom'][1]:.3f}, "
                 f"{math.degrees(st['odom'][2]):.1f} deg)")

    def on_target(m):
        if st["origin"] is None:
            return                                   # refused: reported as "no origin"
        p, t = m.pose.pose, m.twist.twist
        st["target"] = ((p.position.x, p.position.y, yaw_of(p.orientation)),
                        (t.linear.x, t.linear.y, t.angular.z))
        st["target_t"] = st["active_t"] = now()

    def on_stop(_m):
        st["target"] = None
        halt("stop")
        release()

    def on_owner(m):
        try:
            st["holder"] = json.loads(m.data).get("owner")
        except ValueError:
            pass

    def renew():
        if not st["active"]:
            return
        if now() - st["active_t"] > SHOW_IDLE_S:
            log.info(f"no DANCE target for {SHOW_IDLE_S:.0f} s: giving the base back")
            halt("idle")
            release()
            return
        claim()

    def on_guard(m):
        try:
            g = json.loads(m.data)
            st["guard"], st["guard_why"] = g.get("state", "blind"), g.get("why")
        except ValueError:
            pass

    def halt(mode):
        if st["moving"]:
            cmd.publish(Twist())                     # one zero, then silence
            st["moving"] = False
        st["mode"] = mode if mode != "stop" else "idle"

    def tick():
        t = now()
        odom_ok = st["odom"] is not None and t - st["odom_t"] <= ODOM_STALE_S
        fresh = st["target"] is not None and t - st["target_t"] <= TARGET_STALE_S
        if not (odom_ok and fresh and st["origin"] is not None) or st["holder"] != OWNER:
            halt("idle")
            return
        (target, ff) = st["target"]
        err = body_error(target, relative(st["origin"], st["odom"]))
        st["err"] = err
        if st["mode"] == "holding" or over_limit(err):
            if st["mode"] != "holding":
                log.warn(f"holding: {math.hypot(err[0], err[1]):.2f} m / {math.degrees(err[2]):.0f} deg "
                         "off the target, more than the limit; not chasing it")
            halt("holding")
            return
        vx, vy, wz = command(err, ff, vmax, smax, wmax, k_lin, k_ang, fwd_bleed=fwd_bleed, yaw_bleed=yaw_bleed,
                             min_lin=min_lin, min_ang=min_ang)
        tw = Twist(); tw.linear.x, tw.linear.y, tw.angular.z = vx, vy, wz
        cmd.publish(tw)
        st["moving"] = True
        st["mode"] = "settling" if ff == (0.0, 0.0, 0.0) else "tracking"

    def report():
        t = now()
        why = None
        if st["odom"] is None or t - st["odom_t"] > ODOM_STALE_S:
            why = "/odom is not current"
        elif st["guard"] == "blind":
            why = st["guard_why"] or "the people guard is blind"
        elif st["holder"] not in (None, OWNER):
            why = f"the base is held by {st['holder']}"
        e = st["err"]
        status.publish(String(data=json.dumps({
            "ready": why is None, "why": why, "origin": st["origin"] is not None,
            "origin_seq": st["seq"], "tracking": st["moving"], "owner": st["holder"],
            "mode": st["mode"], "guard": st["guard"],
            "err_m": round(math.hypot(e[0], e[1]), 3), "err_deg": round(math.degrees(e[2]), 1)})))

    node.create_subscription(Odometry, "/odom", on_odom, qos_profile_sensor_data)
    node.create_subscription(Empty, "/dance/origin", on_origin, 10)
    node.create_subscription(Odometry, "/dance/target", on_target, 10)
    node.create_subscription(Empty, "/dance/stop", on_stop, 10)
    node.create_subscription(String, "/people_guard/state", on_guard, 10)
    node.create_subscription(String, "/base/owner", on_owner, QoSProfile(
        depth=1, durability=DurabilityPolicy.TRANSIENT_LOCAL, reliability=ReliabilityPolicy.RELIABLE))
    node.create_timer(1.0, renew)
    node.create_timer(0.05, tick)
    node.create_timer(0.2, report)
    log.info(f"dance tracker: at the cap {vmax} m/s forward, {smax} m/s strafe, {wmax} rad/s; strafe trim "
             f"(left, right) fwd {fwd_bleed}, yaw {yaw_bleed}; gains {k_lin}, {k_ang} /s; settle minimum {min_lin} m/s, {min_ang} rad/s; hold beyond {HOLD_M} m / "
             f"{math.degrees(HOLD_RAD):.0f} deg")
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        node.destroy_node()
        rclpy.try_shutdown()


if __name__ == "__main__":
    main()
