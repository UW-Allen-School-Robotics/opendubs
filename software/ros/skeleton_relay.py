#!/usr/bin/env python3
"""Relay the live skeleton feed from ROS 2 to the dashboard, for the stage page.

The skeleton tracker (issue #450) publishes people's joints on a ROS 2 topic. This node subscribes to
that ONE topic, turns each message into the stage page's frame with the choreography lane's adapter
(software/choreo/skeleton_feed.py, the only code that knows the message type and the joint names), and
sends it as JSON text to the dashboard's localhost WebSocket, which fans it out to the stage page:

  ROS topic --rclpy--> skeleton_feed.adapt(msg, received_monotonic) --ws--> dashboard /ws/skeleton/in

PRIVACY, a fixed rule: only skeleton points pass. The node subscribes to exactly the adapter's MSG_TYPE
and refuses to start if that is an image or point-cloud type, so a camera image can never reach the
show. Nothing is recorded, the points included; the journal gets one rate line a minute.

Newest only: one slot, and a newer frame replaces an unsent one, so a slow socket never builds a queue.
If no message arrives for HEARTBEAT_S, an empty frame goes out, so the page can tell "nobody there"
from a feed that has stopped. A dashboard restart only drops frames: the socket reconnects with backoff.

  OPENDUBS_CHOREO          where skeleton_feed.py is          (default /opt/opendubs/choreo)
  OPENDUBS_SKELETON_TOPIC  the tracker's topic                (default: the adapter's DEFAULT_TOPIC)
  OPENDUBS_SKELETON_WS     the dashboard's input socket       (default ws://127.0.0.1:8080/ws/skeleton/in)
"""
import importlib
import json
import os
import sys
import threading
import time

HEARTBEAT_S = 1.0
RATE_LOG_S = 60.0
BACKOFF_S = (1.0, 2.0, 5.0)
WS_URL = os.environ.get("OPENDUBS_SKELETON_WS", "ws://127.0.0.1:8080/ws/skeleton/in")
CHOREO = os.environ.get("OPENDUBS_CHOREO", "/opt/opendubs/choreo")

# Message types this relay must never carry: camera images in any form, and depth as points.
FORBIDDEN_TYPES = {
    "sensor_msgs/msg/Image", "sensor_msgs/msg/CompressedImage", "sensor_msgs/msg/CameraInfo",
    "sensor_msgs/msg/PointCloud2", "sensor_msgs/msg/PointCloud",
    "theora_image_transport/msg/Packet", "ffmpeg_image_transport_msgs/msg/FFMPEGPacket",
}


def forbidden(msg_type: str) -> bool:
    """True when a message type could carry an image or a depth picture of a person."""
    if msg_type in FORBIDDEN_TYPES:
        return True
    name = msg_type.rsplit("/", 1)[-1].lower()
    return any(w in name for w in ("image", "pointcloud", "depth", "frame_data", "video"))


def heartbeat(now: float) -> dict:
    """The stage page's frame for "nobody there"."""
    return {"v": 1, "t": now, "frame": "skeleton_camera", "people": []}


class Slot:
    """The newest frame waiting to be sent. put() replaces; take() empties. Thread-safe."""

    def __init__(self):
        self._lock = threading.Lock()
        self._frame = None
        self.dropped = 0

    def put(self, frame: dict) -> None:
        with self._lock:
            if self._frame is not None:
                self.dropped += 1
            self._frame = frame

    def take(self):
        with self._lock:
            frame, self._frame = self._frame, None
            return frame


def due_heartbeat(now: float, last_message: float, last_sent: float, every: float = HEARTBEAT_S) -> bool:
    """Send an empty frame when the feed has said nothing, and we have sent nothing, for `every` s."""
    return now - last_message >= every and now - last_sent >= every


def load_adapter(path: str = CHOREO):
    """The choreography lane's adapter, and the message class its MSG_TYPE names."""
    if path not in sys.path:
        sys.path.insert(0, path)
    feed = importlib.import_module("skeleton_feed")
    msg_type = getattr(feed, "MSG_TYPE", "")
    if not msg_type or forbidden(msg_type):
        raise SystemExit(f"skeleton_relay: refusing message type {msg_type!r}: only skeleton points may pass")
    pkg, _msg, name = msg_type.split("/")
    cls = getattr(importlib.import_module(f"{pkg}.msg"), name)
    return feed, msg_type, cls


def main():
    import asyncio
    import rclpy
    import websockets
    from rclpy.node import Node
    from rclpy.qos import qos_profile_sensor_data

    feed, msg_type, cls = load_adapter()
    topic = os.environ.get("OPENDUBS_SKELETON_TOPIC") or getattr(feed, "DEFAULT_TOPIC", "")
    if not topic:
        raise SystemExit("skeleton_relay: no topic (set OPENDUBS_SKELETON_TOPIC)")

    slot = Slot()
    state = {"last_message": time.monotonic(), "frames": 0, "sent": 0}
    rclpy.init()
    node = Node("skeleton_relay")
    log = node.get_logger()

    def on_msg(msg):
        received = time.monotonic()                  # before anything else: the adapter's clock offset
        state["last_message"] = received
        try:
            frame = feed.adapt(msg, received)
        except Exception as e:                       # a bad message must not stop the relay
            log.warning(f"adapter rejected a message: {e}")
            return
        if frame is not None:
            state["frames"] += 1
            slot.put(frame)

    node.create_subscription(cls, topic, on_msg, qos_profile_sensor_data)
    log.info(f"relaying {topic} ({msg_type}) to {WS_URL}; no image topic is subscribed")

    async def sender():
        attempt, last_sent, last_log = 0, 0.0, time.monotonic()
        while True:
            try:
                async with websockets.connect(WS_URL, open_timeout=3, max_queue=1) as ws:
                    attempt = 0
                    log.info("connected to the dashboard")
                    while True:
                        now = time.monotonic()
                        frame = slot.take()
                        if frame is None and due_heartbeat(now, state["last_message"], last_sent):
                            frame = heartbeat(now)
                        if frame is not None:
                            await ws.send(json.dumps(frame, separators=(",", ":")))
                            state["sent"] += 1
                            last_sent = now
                        if now - last_log >= RATE_LOG_S:
                            log.info(f"last {RATE_LOG_S:.0f} s: {state['frames']} frames in, {state['sent']} sent, "
                                     f"{slot.dropped} replaced by newer ones")
                            state["frames"] = state["sent"] = slot.dropped = 0
                            last_log = now
                        await asyncio.sleep(0.005)
            except Exception as e:                   # the dashboard restarting: drop frames, reconnect
                slot.take()
                wait = BACKOFF_S[min(attempt, len(BACKOFF_S) - 1)]
                if attempt == 0:
                    log.warning(f"dashboard socket: {e}; reconnecting")
                attempt += 1
                await asyncio.sleep(wait)

    threading.Thread(target=lambda: asyncio.run(sender()), daemon=True).start()
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        node.destroy_node()
        rclpy.try_shutdown()


if __name__ == "__main__":
    main()
