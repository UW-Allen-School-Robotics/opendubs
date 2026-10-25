#!/usr/bin/env python3
"""The base selector: in AUTO, exactly one program at a time drives the base.

The rule: the RC transmitter drives in MANUAL; software (a show, a navigation stack) drives only in
AUTO. The StamPLC firmware enforces that split itself, so RC never contends with software and there is
no RC priority here. What this node settles is the question between SOFTWARE owners: a show's dance
tracker, a student's navigation stack, a teleop tool. One owner holds the base; everyone else is
refused until it is released.

  /base/claim             std_msgs/String  JSON {"owner": "nav", "ttl_s": 2.0}
                          Claim the base, or renew a claim you hold. Granted when the base is free,
                          already yours, or the holder's lease has run out. Never taken from a live
                          holder. Renew at least every ttl_s (max 5 s); a program that dies frees the
                          base when its lease runs out.
  /base/release           std_msgs/String  JSON {"owner": "nav"}   give the base back now.
                          {"owner": "*"} is the operator's release: it frees the base whoever holds it.
  /base/owner             std_msgs/String  JSON, latched, on every change and at 2 Hz:
                          {"owner": name or null, "expires_in_s": s, "refused": {"owner", "reason"} or null}
  /base/<owner>/cmd_vel   geometry_msgs/Twist   the holder's commands (body frame, m/s and rad/s).
                          Only the current holder's topic is forwarded; anything else is ignored.

Forwarded commands go to drive_bridge's input, /opendubs/drive/cmd_vel, which only this node
publishes; drive_bridge refuses all motion if anything else publishes there. When the base changes
hands or is freed, one zero Twist goes out first.

Every owner is under the same gates, none of them here: the firmware moves the base for software
only in CH5 AUTO with the deadman held, at most 0.35 of full command x the CH8 knob; drive_bridge's
people guard refuses motion toward anything within 0.5 m; and a command older than 0.5 s stops it.

Owner names: lower case, digits and underscore, starting with a letter, at most 31 characters.
"""
import json
import re

NAME = re.compile(r"^[a-z][a-z0-9_]{0,30}$")
TTL_MAX_S = 5.0
DRIVE_IN = "/opendubs/drive/cmd_vel"


class Lease:
    """Who holds the base. Pure: no ROS, so it can be tested on a laptop. Times are seconds."""

    def __init__(self, ttl_max=TTL_MAX_S):
        self.ttl_max = ttl_max
        self.owner = None
        self.expires = 0.0

    def current(self, now):
        """The holder, or None. A lease that has run out is freed here."""
        if self.owner is not None and now >= self.expires:
            self.owner = None
        return self.owner

    def claim(self, owner, ttl, now):
        """(granted, reason). Grants a free base, renews the holder's own, refuses anyone else."""
        if not isinstance(owner, str) or not NAME.match(owner):
            return False, f"bad owner name {owner!r}"
        try:
            ttl = float(ttl)
        except (TypeError, ValueError):
            return False, "ttl_s must be a number"
        if not ttl > 0:
            return False, "ttl_s must be positive"
        holder = self.current(now)
        if holder is not None and holder != owner:
            return False, f"held by {holder}"
        self.owner, self.expires = owner, now + min(ttl, self.ttl_max)
        return True, "renewed" if holder == owner else "granted"

    def release(self, owner, now):
        """True if this freed the base: the holder's own release, or the operator's ("*")."""
        holder = self.current(now)
        if holder is not None and owner in (holder, "*"):
            self.owner = None
            return True
        return False


def parse(data):
    """A claim or release body: JSON {"owner": ..., "ttl_s": ...}, or a bare owner name."""
    try:
        d = json.loads(data)
        return d if isinstance(d, dict) else {"owner": d}
    except ValueError:
        return {"owner": data.strip()}


def main():
    import rclpy
    from geometry_msgs.msg import Twist
    from rclpy.node import Node
    from rclpy.qos import DurabilityPolicy, QoSProfile, ReliabilityPolicy
    from std_msgs.msg import String

    rclpy.init()
    node = Node("base_selector")
    log = node.get_logger()
    lease = Lease()
    now = lambda: node.get_clock().now().nanoseconds / 1e9
    latched = QoSProfile(depth=1, durability=DurabilityPolicy.TRANSIENT_LOCAL, reliability=ReliabilityPolicy.RELIABLE)
    owner_pub = node.create_publisher(String, "/base/owner", latched)
    drive = node.create_publisher(Twist, DRIVE_IN, 10)
    st = {"sub": None, "sub_owner": None, "refused": None}

    def forward(owner):
        def cb(msg):
            if lease.current(now()) == owner:       # only the holder, and only while it holds
                drive.publish(msg)
        return cb

    def sync():
        """Follow the lease: subscribe to the holder's topic, stop the base when it changes hands."""
        holder = lease.current(now())
        if holder != st["sub_owner"]:
            if st["sub"] is not None:
                node.destroy_subscription(st["sub"])
                st["sub"] = None
            drive.publish(Twist())                   # the base stops before anyone else drives it
            if holder is not None:
                st["sub"] = node.create_subscription(Twist, f"/base/{holder}/cmd_vel", forward(holder), 10)
            log.info(f"base: {st['sub_owner'] or 'free'} -> {holder or 'free'}")
            st["sub_owner"] = holder
            if st["refused"] and st["refused"].get("owner") == holder:
                st["refused"] = None                 # the refused claimant has it now
        report()

    def report():
        holder = lease.current(now())
        owner_pub.publish(String(data=json.dumps({
            "owner": holder, "expires_in_s": round(max(lease.expires - now(), 0.0), 2) if holder else 0.0,
            "refused": st["refused"]})))

    def on_claim(msg):
        d = parse(msg.data)
        ok, why = lease.claim(d.get("owner"), d.get("ttl_s", 2.0), now())
        if not ok:
            st["refused"] = {"owner": d.get("owner"), "reason": why}
            log.warn(f"claim by {d.get('owner')!r} refused: {why}", throttle_duration_sec=5.0)
        sync()

    def on_release(msg):
        d = parse(msg.data)
        if lease.release(d.get("owner"), now()):
            log.info(f"base released by {d.get('owner')}")
            st["refused"] = None
        sync()

    def check_legacy():
        n = node.count_publishers("/cmd_vel")
        if n:
            log.warn(f"{n} publisher(s) on /cmd_vel, which nothing consumes: claim the base and publish on "
                     "/base/<owner>/cmd_vel instead (docs: Navigation interface)", throttle_duration_sec=60.0)

    node.create_subscription(String, "/base/claim", on_claim, 10)
    node.create_subscription(String, "/base/release", on_release, 10)
    node.create_timer(0.5, sync)                     # expiries, and the 2 Hz report
    node.create_timer(10.0, check_legacy)
    sync()
    log.info(f"base selector: claims on /base/claim, commands on /base/<owner>/cmd_vel -> {DRIVE_IN}")
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        node.destroy_node()
        rclpy.try_shutdown()


if __name__ == "__main__":
    main()
