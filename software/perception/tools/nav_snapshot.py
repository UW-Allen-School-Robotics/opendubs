"""Save one navigation output: the traversability image and the obstacle cloud's size and extent.

  python3 nav_snapshot.py --camera dummy --out /data/results/nav
"""

import argparse
import os

import cv2
import numpy as np
import rclpy
from rclpy.node import Node
from rclpy.qos import qos_profile_sensor_data
from sensor_msgs.msg import Image, PointCloud2


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--camera", default="dummy")
    ap.add_argument("--out", required=True)
    args, ros_args = ap.parse_known_args()
    rclpy.init(args=ros_args)
    node = Node("nav_snapshot")
    got = {}
    node.create_subscription(Image, f"/perception/{args.camera}/traversability",
                             lambda m: got.setdefault("trav", m), 5)
    node.create_subscription(PointCloud2, f"/perception/{args.camera}/obstacles",
                             lambda m: got.setdefault("cloud", m), 5)
    while rclpy.ok() and len(got) < 2:
        rclpy.spin_once(node, timeout_sec=1.0)
    os.makedirs(args.out, exist_ok=True)
    t = got["trav"]
    trav = np.frombuffer(t.data, np.uint8).reshape(t.height, t.step)[:, : t.width]
    cv2.imwrite(os.path.join(args.out, "traversability.png"), trav)
    c = got["cloud"]
    pts = np.frombuffer(c.data, np.float32).reshape(-1, 3) if c.width else np.zeros((0, 3), np.float32)
    free = 100.0 * (trav > 0).mean()
    ext = "" if not len(pts) else (f", x {pts[:, 0].min():.2f}..{pts[:, 0].max():.2f} m, "
                                   f"z {pts[:, 2].min():.2f}..{pts[:, 2].max():.2f} m")
    print(f"traversability {t.width}x{t.height}, {free:.1f}% free | obstacles: {len(pts)} points{ext}")
    node.destroy_node()
    rclpy.try_shutdown()


if __name__ == "__main__":
    main()
