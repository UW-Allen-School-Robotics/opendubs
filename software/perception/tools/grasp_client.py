"""Send one GraspObject goal and save what comes back.

  python3 grasp_client.py --label mug --camera femto --out /data/results/g1

Writes to --out: overlay.png (the object's mask, and the grasp's axes projected into the image:
red approach, green closing, blue binormal, 5 cm each) and result.json (the pose in the camera
frame, width, scores, mode, timings). Prints a one-line summary.
"""

import argparse
import json
import os
import sys
import time

import cv2
import numpy as np
import rclpy
from rclpy.action import ActionClient
from rclpy.node import Node

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from perception_msgs.action import GraspObject  # noqa: E402
from perception_tasks.grasp_object import image_array  # noqa: E402

SOURCES = {"auto": 0, "detector": 1, "clipseg": 2}


def quat_matrix(q):
    x, y, z, w = q.x, q.y, q.z, q.w
    return np.array([[1 - 2 * (y * y + z * z), 2 * (x * y - z * w), 2 * (x * z + y * w)],
                     [2 * (x * y + z * w), 1 - 2 * (x * x + z * z), 2 * (y * z - x * w)],
                     [2 * (x * z - y * w), 2 * (y * z + x * w), 1 - 2 * (x * x + y * y)]])


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--label", required=True)
    ap.add_argument("--camera", default="femto")
    ap.add_argument("--min-score", type=float, default=0.0)
    ap.add_argument("--source", choices=SOURCES, default="auto")
    ap.add_argument("--up", nargs=3, type=float, metavar=("X", "Y", "Z"),
                    help="world up in the camera frame, if known")
    ap.add_argument("--no-images", action="store_true", help="ask for the pose only (no overlay)")
    ap.add_argument("--out", required=True)
    ap.add_argument("--timeout", type=float, default=60.0)
    args, ros_args = ap.parse_known_args()

    rclpy.init(args=ros_args)
    node = Node("grasp_client")
    client = ActionClient(node, GraspObject, "grasp_object")
    if not client.wait_for_server(timeout_sec=args.timeout):
        raise SystemExit("grasp_object action server not found (is perception_tasks running?)")
    goal = GraspObject.Goal(label=args.label, camera=args.camera, min_score=args.min_score,
                            source=SOURCES[args.source], include_images=not args.no_images)
    if args.up:
        goal.up_cam.x, goal.up_cam.y, goal.up_cam.z = args.up
    t0 = time.perf_counter()
    fut = client.send_goal_async(goal)
    rclpy.spin_until_future_complete(node, fut, timeout_sec=args.timeout)
    res_fut = fut.result().get_result_async()
    rclpy.spin_until_future_complete(node, res_fut, timeout_sec=args.timeout)
    rt_ms = (time.perf_counter() - t0) * 1000.0
    res = res_fut.result().result

    os.makedirs(args.out, exist_ok=True)
    p, q = res.grasp.pose.position, res.grasp.pose.orientation
    summary = {"success": res.success, "message": res.message, "frame": res.header.frame_id,
               "mode": res.mode, "instances_found": res.instances_found,
               "segment_ms": round(res.segment_ms, 1), "grasp_ms": round(res.grasp_ms, 1),
               "round_trip_ms": round(rt_ms, 1)}
    if res.success:
        summary["frame_stamp"] = res.header.stamp.sec + res.header.stamp.nanosec * 1e-9
        summary["grasp"] = {"position": [p.x, p.y, p.z], "orientation_xyzw": [q.x, q.y, q.z, q.w],
                            "width": res.grasp.width, "score": res.grasp.score,
                            "upright_score": res.grasp.upright_score,
                            "tilt_from_floor_deg": res.grasp.tilt_from_floor_deg}
        summary["instance"] = {"label": res.instance.label, "score": res.instance.score,
                               "box": [int(v) for v in res.instance.box]}  # numpy int32 from ROS
    if res.success and res.rgb.data:
        rgb = image_array(res.rgb).astype(np.float32)
        mask = image_array(res.instance.mask) > 0
        rgb[mask] = 0.5 * rgb[mask] + 0.5 * np.array([0, 200, 255], dtype=np.float32)
        img = np.ascontiguousarray(rgb.astype(np.uint8)[:, :, ::-1])
        K = np.array(res.camera_info.k).reshape(3, 3)
        R = quat_matrix(q)
        t = np.array([p.x, p.y, p.z])

        def px(pt):
            return int(round(K[0, 0] * pt[0] / pt[2] + K[0, 2])), int(round(K[1, 1] * pt[1] / pt[2] + K[1, 2]))

        for axis, colour in ((0, (0, 0, 255)), (1, (0, 255, 0)), (2, (255, 0, 0))):  # BGR
            cv2.arrowedLine(img, px(t), px(t + 0.05 * R[:, axis]), colour, 2, tipLength=0.2)
        cv2.circle(img, px(t), 5, (255, 255, 255), -1)
        cv2.imwrite(os.path.join(args.out, "overlay.png"), img)
    with open(os.path.join(args.out, "result.json"), "w") as f:
        json.dump(summary, f, indent=1)
    print(f"{'OK' if res.success else 'FAIL'} {res.message} | {res.mode} | segment {res.segment_ms:.0f} ms, "
          f"grasp {res.grasp_ms:.0f} ms, round trip {rt_ms:.0f} ms")
    node.destroy_node()
    rclpy.try_shutdown()


if __name__ == "__main__":
    main()
