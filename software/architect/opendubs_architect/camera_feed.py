"""The Orbbec's colour image as a small JPEG, for the dashboard's ARCHITECT tab.

    python3 -m opendubs_architect.camera_feed        # GET http://127.0.0.1:8097/frame.jpg

The perception stack's camera driver (archit-perception-camera, software/perception on
archit/perception-grasp) publishes /camera/color/image_raw at 30 fps. It hands frames over by shared
memory first, so this runs in a container that joins the camera container's IPC namespace, as the
perception server's own container does (tools/dev.sh camera); from a container of its own it would
discover the topic and never receive a frame. Its /compressed topic is listed but has no publisher.

ONLY WHILE SOMEONE IS LOOKING. A raw frame is 2.7 MB, 30 a second. The subscription is made when a
frame is asked for and dropped IDLE_S after the last ask, so nobody watching costs nothing; while it
is up, at most one frame each ENCODE_S is converted and encoded (WIDTH wide, JPEG QUALITY). The page
asks twice a second, and the robot's Wi-Fi carries about 40 KB a frame, not the raw stream (the
Network lane's rule: only what is on screen is streamed).

Read-only: it subscribes to one topic and sends nothing anywhere.
"""
from __future__ import annotations

import argparse
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

TOPIC = "/camera/color/image_raw"
PORT = 8097
WIDTH, QUALITY = 640, 70
ENCODE_S = 0.4
IDLE_S = 5.0
STALE_S = 2.0          # a frame older than this is not served as current


def encode(msg, width: int = WIDTH, quality: int = QUALITY) -> bytes:
    """A sensor_msgs/Image (rgb8, bgr8 or mono8) as JPEG bytes, scaled to `width`."""
    import cv2
    import numpy as np
    channels = {"rgb8": 3, "bgr8": 3, "mono8": 1}.get(msg.encoding)
    if channels is None:
        raise ValueError(f"unsupported image encoding {msg.encoding!r}")
    a = np.frombuffer(bytes(msg.data), dtype=np.uint8)
    a = a.reshape(msg.height, msg.step)[:, :msg.width * channels].reshape(msg.height, msg.width, channels)
    if msg.encoding == "rgb8":
        a = cv2.cvtColor(a, cv2.COLOR_RGB2BGR)
    if msg.width > width:
        a = cv2.resize(a, (width, round(msg.height * width / msg.width)), interpolation=cv2.INTER_AREA)
    ok, jpg = cv2.imencode(".jpg", a, [cv2.IMWRITE_JPEG_QUALITY, quality])
    if not ok:
        raise ValueError("JPEG encoding failed")
    return jpg.tobytes()


class Feed:
    """The latest JPEG, kept fresh while it is asked for. `subscribe(callback) -> handle` and
    `unsubscribe(handle)` are seams (rclpy on the robot)."""

    def __init__(self, subscribe, unsubscribe, clock=time.monotonic):
        self._subscribe, self._unsubscribe, self.clock = subscribe, unsubscribe, clock
        self.lock = threading.Lock()
        self.sub, self.asked, self.encoded_at = None, 0.0, -1e9
        self.jpeg, self.jpeg_at, self.error = None, None, None

    def on_frame(self, msg) -> None:
        now = self.clock()
        if now - self.encoded_at < ENCODE_S:
            return
        self.encoded_at = now
        try:
            jpg = encode(msg)
        except (ValueError, ImportError) as e:
            self.error = str(e)
            return
        with self.lock:
            self.jpeg, self.jpeg_at, self.error = jpg, now, None

    def ask(self):
        """(jpeg bytes, age in seconds) or (None, why). Starts the subscription if it is down."""
        now = self.clock()
        with self.lock:
            self.asked = now
            if self.sub is None:
                self.sub = self._subscribe(self.on_frame)
            if self.jpeg is None or now - self.jpeg_at > STALE_S:
                return None, self.error or "waiting for the camera"
            return self.jpeg, round(now - self.jpeg_at, 2)

    def tick(self) -> None:
        """Drop the subscription once nobody has asked for IDLE_S."""
        with self.lock:
            if self.sub is not None and self.clock() - self.asked > IDLE_S:
                self._unsubscribe(self.sub)
                self.sub, self.jpeg, self.jpeg_at = None, None, None


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=PORT)
    ap.add_argument("--topic", default=TOPIC)
    a = ap.parse_args(argv)

    import rclpy
    from rclpy.qos import qos_profile_sensor_data
    from sensor_msgs.msg import Image
    rclpy.init()
    node = rclpy.create_node("opendubs_architect_camera_feed")
    lock = threading.Lock()                     # rclpy's node is not safe to change while it spins

    def subscribe(cb):
        with lock:
            return node.create_subscription(Image, a.topic, cb, qos_profile_sensor_data)

    def unsubscribe(sub):
        with lock:
            node.destroy_subscription(sub)

    feed = Feed(subscribe, unsubscribe)

    def spin():
        while rclpy.ok():
            with lock:
                rclpy.spin_once(node, timeout_sec=0.05)
            feed.tick()
            time.sleep(0.005)

    class H(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_GET(self):
            if self.path.partition("?")[0] != "/frame.jpg":
                self.send_response(404)
                self.end_headers()
                return
            jpg, age = feed.ask()
            if jpg is None:
                body = age.encode()
                self.send_response(503)
                self.send_header("Content-Type", "text/plain; charset=utf-8")
            else:
                body = jpg
                self.send_response(200)
                self.send_header("Content-Type", "image/jpeg")
                self.send_header("X-Frame-Age", str(age))
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

    try:
        srv = ThreadingHTTPServer((a.host, a.port), H)
    except OSError as e:
        sys.exit(f"camera_feed: port {a.port} is taken ({e}); leave it, or pick --port")
    threading.Thread(target=spin, daemon=True).start()
    print(f"camera feed: {a.topic} -> http://{a.host}:{a.port}/frame.jpg (subscribed only while asked)", flush=True)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
