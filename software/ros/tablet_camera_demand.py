#!/usr/bin/env python3
"""Tell the dashboard which tablet cameras some ROS node wants, so the tablet streams only on demand.

The head tablet's cameras are relayed into ROS by the dashboard (backend/tablet_relay.py) as
/tablet/camera/{front,rear}/image_raw/compressed, and the relay streams a camera only while that topic
has a subscriber. rosbridge's rosapi cannot be asked that reliably: it skips nodes it treats as hidden
and, on the robot, reported no subscribers while `ros2 topic info` counted one. rclpy's
count_subscribers() counts every subscription that discovery knows about, so this node asks it and
POSTs the answer:

  POST http://127.0.0.1:8080/api/tablet/camera/demand
       {"front": bool, "rear": bool, "counts": {"front": int, "rear": int}}

at once when either answer changes, and otherwise every HEARTBEAT_S as a heartbeat. The dashboard
treats demand older than a few seconds as false, so if this node or ROS stops, the cameras stop: a
dead watcher never leaves a camera streaming.

  OPENDUBS_CAMDEMAND_URL     where to POST          (default above)
  OPENDUBS_CAMDEMAND_CHECK_S how often to count     (default 1.0)
"""
import json
import os
import time
import urllib.request

CAMERAS = ("front", "rear")
TOPIC = "/tablet/camera/{}/image_raw/compressed"
URL = os.environ.get("OPENDUBS_CAMDEMAND_URL", "http://127.0.0.1:8080/api/tablet/camera/demand")
CHECK_S = float(os.environ.get("OPENDUBS_CAMDEMAND_CHECK_S", "1.0"))
HEARTBEAT_S = 2.0
POST_TIMEOUT_S = 1.0


class Demand:
    """Decides what to send. Pure: no ROS and no network, so it can be tested on a laptop."""

    def __init__(self, heartbeat_s=HEARTBEAT_S):
        self.heartbeat_s = heartbeat_s
        self.sent = None          # the last body the dashboard accepted
        self.sent_at = None

    def body(self, counts):
        return {**{c: counts.get(c, 0) > 0 for c in CAMERAS},
                "counts": {c: int(counts.get(c, 0)) for c in CAMERAS}}

    def due(self, body, now):
        """True when body must be sent now: the first time, on a change of demand, or as a heartbeat."""
        if self.sent is None or self.sent_at is None:
            return True
        if any(body[c] != self.sent[c] for c in CAMERAS):
            return True
        return now - self.sent_at >= self.heartbeat_s

    def accepted(self, body, now):
        self.sent, self.sent_at = body, now


def post(body, url=URL):
    req = urllib.request.Request(url, data=json.dumps(body).encode(), method="POST",
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=POST_TIMEOUT_S) as r:
        return 200 <= r.status < 300


def main():
    import rclpy
    from rclpy.node import Node

    rclpy.init()
    node = Node("tablet_camera_demand")
    log = node.get_logger()
    demand = Demand()
    failing = [False]

    def tick():
        body = demand.body({c: node.count_subscribers(TOPIC.format(c)) for c in CAMERAS})
        now = time.monotonic()
        if not demand.due(body, now):
            return
        try:
            ok = post(body)
        except Exception as e:  # dashboard restarting or down: retry on the next tick
            ok, why = False, e
        else:
            why = "non-2xx reply"
        if ok:
            if failing[0] or demand.sent is None or any(body[c] != demand.sent[c] for c in CAMERAS):
                log.info(f"demand front={body['front']} rear={body['rear']} counts={body['counts']}")
            failing[0] = False
            demand.accepted(body, now)
        elif not failing[0]:   # log the first failure of a run, not one per second
            log.warning(f"could not POST camera demand to {URL}: {why}")
            failing[0] = True

    node.create_timer(CHECK_S, tick)
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        node.destroy_node()
        rclpy.try_shutdown()


if __name__ == "__main__":
    main()
