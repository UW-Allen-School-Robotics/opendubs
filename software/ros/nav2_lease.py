#!/usr/bin/env python3
"""nav2_lease.py: Nav2 drives the base only through the base lease (base_selector.py), and only while it
has a goal. Started by nav2.launch.py.

While a NavigateToPose or NavigateThroughPoses goal is active (accepted, executing or cancelling), this
claims the base as OWNER every TTL_S / 2, with ttl_s TTL_S. When the last goal ends it sends one zero
Twist on /base/<owner>/cmd_vel and releases the base, so a show or another stack can claim it between
missions. Nav2's velocity smoother publishes on /base/<owner>/cmd_vel itself (nav2.launch.py's remap):
nothing here forwards commands, and the selector forwards them only while the claim holds.

A claim refused (another owner holds the base) is logged; Nav2's progress checker then fails the goal,
because the base does not move. The deadman, the speed cap, the people guard and the command timeout
are the robot's, the same for every owner.

  OPENDUBS_NAV_OWNER   the owner name (default nav)
"""
import json
import os

OWNER = os.environ.get("OPENDUBS_NAV_OWNER", "nav")
TTL_S = 2.0
ACTIONS = ["/navigate_to_pose/_action/status", "/navigate_through_poses/_action/status"]
ACTIVE = {1, 2, 3}          # action_msgs/GoalStatus: ACCEPTED, EXECUTING, CANCELING


def active(statuses) -> bool:
    """Any goal still running, from each action's latest GoalStatusArray (lists of status codes)."""
    return any(s in ACTIVE for codes in statuses for s in codes)


def main():
    import rclpy
    from action_msgs.msg import GoalStatusArray
    from geometry_msgs.msg import Twist
    from rclpy.node import Node
    from rclpy.qos import DurabilityPolicy, QoSProfile, ReliabilityPolicy
    from std_msgs.msg import String

    rclpy.init()
    node = Node("nav2_lease")
    log = node.get_logger()
    latched = QoSProfile(depth=1, reliability=ReliabilityPolicy.RELIABLE, durability=DurabilityPolicy.TRANSIENT_LOCAL)
    claim = node.create_publisher(String, "/base/claim", 10)
    release = node.create_publisher(String, "/base/release", 10)
    cmd = node.create_publisher(Twist, f"/base/{OWNER}/cmd_vel", 10)
    latest = {a: [] for a in ACTIONS}
    state = {"held": False, "refused": None}

    for a in ACTIONS:
        node.create_subscription(GoalStatusArray, a,
                                 lambda m, a=a: latest.__setitem__(a, [s.status for s in m.status_list]), latched)

    def owner(m):
        try:
            d = json.loads(m.data)
        except ValueError:
            return
        r = d.get("refused") or {}
        if r.get("owner") == OWNER and r != state["refused"]:
            state["refused"] = r
            log.warning(f"base claim refused: {r.get('reason')} (held by {d.get('owner')})")
    node.create_subscription(String, "/base/owner", owner, latched)

    def tick():
        if active(latest.values()):
            if not state["held"]:
                log.info(f"goal active: claiming the base as {OWNER}")
            claim.publish(String(data=json.dumps({"owner": OWNER, "ttl_s": TTL_S})))
            state["held"] = True
        elif state["held"]:
            cmd.publish(Twist())
            release.publish(String(data=json.dumps({"owner": OWNER})))
            state["held"], state["refused"] = False, None
            log.info("no goal: base released")
    node.create_timer(TTL_S / 2, tick)
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        if state["held"]:
            cmd.publish(Twist())
            release.publish(String(data=json.dumps({"owner": OWNER})))
        node.destroy_node()
        rclpy.try_shutdown()


if __name__ == "__main__":
    main()
