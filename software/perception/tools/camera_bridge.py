"""Copy one camera from the robot's ROS graph into the test domain, one way.

The camera driver publishes on the robot's domain (0); the perception server under test lives on
domain 77, localhost only (notes/workplan-perception.md, rule 4). This node only subscribes on the
robot's side and only publishes on the test side, so nothing of ours appears on the robot's graph.
It forwards at most --max-hz colour frames, each with its depth frame (within 20 ms), and the camera
info.

  python3 camera_bridge.py --src /camera --dst /femto --max-hz 10

Run with FASTDDS_BUILTIN_TRANSPORTS=UDPv4 (the driver runs as another user, and Fast DDS shared
memory across users left a subscriber hanging) and ROS_AUTOMATIC_DISCOVERY_RANGE=SUBNET: with
LOCALHOST, which the server's container sets, the driver is never discovered. The range applies to
both sides, so the test side can be discovered by domain-77 hosts on the LAN; domain 0 sees only a
subscriber.
"""

import argparse
import threading
import time

import rclpy
from rclpy.context import Context
from rclpy.executors import MultiThreadedExecutor
from rclpy.qos import qos_profile_sensor_data
from sensor_msgs.msg import CameraInfo, Image


def stamp_s(msg):
    return msg.header.stamp.sec + msg.header.stamp.nanosec * 1e-9


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--src", default="/camera")
    ap.add_argument("--dst", default="/femto")
    ap.add_argument("--src-domain", type=int, default=0)
    ap.add_argument("--dst-domain", type=int, default=77)
    ap.add_argument("--max-hz", type=float, default=10.0)
    args = ap.parse_args()

    ctx_in, ctx_out = Context(), Context()
    rclpy.init(context=ctx_in, domain_id=args.src_domain)
    rclpy.init(context=ctx_out, domain_id=args.dst_domain)
    src = rclpy.create_node("archit_camera_bridge_in", context=ctx_in)
    dst = rclpy.create_node("camera_bridge_out", context=ctx_out)

    pub_rgb = dst.create_publisher(Image, f"{args.dst}/color/image_raw", qos_profile_sensor_data)
    pub_depth = dst.create_publisher(Image, f"{args.dst}/depth/image_raw", qos_profile_sensor_data)
    pub_info = dst.create_publisher(CameraInfo, f"{args.dst}/color/camera_info", qos_profile_sensor_data)

    # A colour frame waits here for its own depth frame: the depth often arrives after the colour,
    # and forwarding the previous depth (a frame period older) fails the server's stamp match.
    lock = threading.Lock()
    depths: list[Image] = []
    state = {"last": 0.0, "sent": 0, "no_depth": 0, "info": None, "pending": None}
    period = 1.0 / args.max_hz
    pair_s = 0.02

    def try_send():
        with lock:
            rgb = state["pending"]
            if rgb is None:
                return
            depth = min(depths, key=lambda d: abs(stamp_s(d) - stamp_s(rgb)), default=None)
            if depth is None or abs(stamp_s(depth) - stamp_s(rgb)) > pair_s:
                if depths and stamp_s(depths[-1]) > stamp_s(rgb) + pair_s:
                    state["pending"] = None  # its depth was dropped; skip this frame
                    state["no_depth"] += 1
                return
            state["pending"] = None
        pub_depth.publish(depth)
        if state["info"] is not None:
            pub_info.publish(state["info"])
        pub_rgb.publish(rgb)
        state["sent"] += 1

    def on_depth(msg):
        with lock:
            depths.append(msg)
            del depths[:-10]
        try_send()

    def on_info(msg):
        state["info"] = msg

    def on_rgb(msg):
        now = time.monotonic()
        if now - state["last"] < period:
            return
        state["last"] = now
        with lock:
            state["pending"] = msg
        try_send()

    src.create_subscription(Image, f"{args.src}/color/image_raw", on_rgb, qos_profile_sensor_data)
    src.create_subscription(Image, f"{args.src}/depth/image_raw", on_depth, qos_profile_sensor_data)
    src.create_subscription(CameraInfo, f"{args.src}/color/camera_info", on_info, qos_profile_sensor_data)

    def report():
        dst.get_logger().info(f"forwarded {state['sent']} colour+depth pairs {args.src} -> {args.dst}, "
                              f"skipped {state['no_depth']} without depth")
        state["sent"] = state["no_depth"] = 0

    dst.create_timer(10.0, report)
    ex_in = MultiThreadedExecutor(num_threads=2, context=ctx_in)
    ex_in.add_node(src)
    threading.Thread(target=ex_in.spin, daemon=True).start()
    ex_out = MultiThreadedExecutor(num_threads=1, context=ctx_out)
    ex_out.add_node(dst)
    try:
        ex_out.spin()
    except KeyboardInterrupt:
        pass
    finally:
        rclpy.try_shutdown(context=ctx_in)
        rclpy.try_shutdown(context=ctx_out)


if __name__ == "__main__":
    main()
