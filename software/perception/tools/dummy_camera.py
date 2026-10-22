"""A stand-in RGB-D camera: publishes still images with synthetic depth, for testing without hardware.

Topics (namespace /dummy by default): color/image_raw (rgb8), depth/image_raw (16UC1, mm, aligned
to colour, same stamp), color/camera_info. The depth is a floor plane seen from 1 m up, tilted
down: near at the bottom of the image, far at the top. It is only there so the navigation adapter
can project points and a query result carries an aligned depth image.

  python3 dummy_camera.py [--images a.jpg b.jpg] [--rate 10] [--ns /dummy]
With no images, Ultralytics' bundled bus.jpg and zidane.jpg are used.
"""

import argparse

import cv2
import numpy as np
import rclpy
from rclpy.node import Node
from sensor_msgs.msg import CameraInfo, Image


def to_msg(arr, encoding, stamp, frame):
    m = Image()
    m.header.stamp, m.header.frame_id = stamp, frame
    m.height, m.width = arr.shape[:2]
    m.encoding, m.is_bigendian = encoding, 0
    arr = np.ascontiguousarray(arr)
    m.step = arr.strides[0]
    m.data = arr.tobytes()
    return m


def floor_depth_mm(h, w):
    rows = np.linspace(4.0, 0.6, h, dtype=np.float32)  # far (top) to near (bottom), metres
    return np.repeat((rows * 1000).astype(np.uint16)[:, None], w, axis=1)


class DummyCamera(Node):
    def __init__(self, images, rate, ns, period_s):
        super().__init__("dummy_camera")
        self.frames = []
        for path in images:
            bgr = cv2.imread(path)
            if bgr is None:
                raise FileNotFoundError(path)
            rgb = cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB)
            h, w = rgb.shape[:2]
            self.frames.append((rgb, floor_depth_mm(h, w)))
        self.frame_id = "dummy_color_optical_frame"
        self.pub_rgb = self.create_publisher(Image, f"{ns}/color/image_raw", 5)
        self.pub_depth = self.create_publisher(Image, f"{ns}/depth/image_raw", 5)
        self.pub_info = self.create_publisher(CameraInfo, f"{ns}/color/camera_info", 5)
        self.i, self.period_s, self.t_switch = 0, period_s, None
        self.create_timer(1.0 / rate, self.tick)
        self.get_logger().info(f"dummy camera: {len(self.frames)} image(s) at {rate} Hz on {ns}")

    def tick(self):
        now = self.get_clock().now()
        if self.t_switch is None or (now - self.t_switch).nanoseconds * 1e-9 >= self.period_s:
            self.i = (self.i + 1) % len(self.frames)
            self.t_switch = now
        rgb, depth = self.frames[self.i]
        h, w = rgb.shape[:2]
        stamp = now.to_msg()
        info = CameraInfo()
        info.header.stamp, info.header.frame_id = stamp, self.frame_id
        info.height, info.width = h, w
        f = 0.8 * w
        info.k = [f, 0.0, w / 2.0, 0.0, f, h / 2.0, 0.0, 0.0, 1.0]
        info.p = [f, 0.0, w / 2.0, 0.0, 0.0, f, h / 2.0, 0.0, 0.0, 0.0, 1.0, 0.0]
        info.distortion_model = "plumb_bob"
        info.d = [0.0] * 5
        self.pub_rgb.publish(to_msg(rgb, "rgb8", stamp, self.frame_id))
        self.pub_depth.publish(to_msg(depth, "16UC1", stamp, self.frame_id))
        self.pub_info.publish(info)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--images", nargs="*")
    ap.add_argument("--rate", type=float, default=10.0)
    ap.add_argument("--ns", default="/dummy")
    ap.add_argument("--switch-every", type=float, default=5.0, help="seconds per image")
    args, ros_args = ap.parse_known_args()
    images = args.images
    if not images:
        from ultralytics.utils import ASSETS

        images = [str(ASSETS / "bus.jpg"), str(ASSETS / "zidane.jpg")]
    rclpy.init(args=ros_args)
    node = DummyCamera(images, args.rate, args.ns, args.switch_every)
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        node.destroy_node()
        rclpy.try_shutdown()


if __name__ == "__main__":
    main()
