"""Send one Segment query and save what comes back.

  python3 segment_client.py --labels bus person --camera dummy --out /data/results/q1

Writes to --out: rgb.png, depth.png (if any, 16-bit mm), mask_<i>_<label>.png, overlay.png and
result.json (labels, scores, boxes, latency). Prints a one-line summary and the round-trip time.
"""

import argparse
import json
import os
import time

import cv2
import numpy as np
import rclpy
from rclpy.action import ActionClient
from rclpy.node import Node

from perception_msgs.action import Segment

SOURCES = {"auto": 0, "detector": 1, "clipseg": 2}
COLORS = [(230, 25, 75), (60, 180, 75), (0, 130, 200), (245, 130, 48), (145, 30, 180),
          (70, 240, 240), (240, 50, 230), (210, 245, 60)]


def img(msg):
    buf = np.frombuffer(msg.data, dtype=np.uint8)
    if msg.encoding in ("rgb8", "bgr8"):
        a = buf.reshape(msg.height, msg.step)[:, : msg.width * 3].reshape(msg.height, msg.width, 3)
        return a[:, :, ::-1] if msg.encoding == "bgr8" else a
    if msg.encoding == "mono8":
        return buf.reshape(msg.height, msg.step)[:, : msg.width]
    if msg.encoding.lower() in ("16uc1", "mono16"):
        return buf.view(np.uint16).reshape(msg.height, msg.step // 2)[:, : msg.width]
    raise ValueError(msg.encoding)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--labels", nargs="+", required=True)
    ap.add_argument("--camera", default="dummy")
    ap.add_argument("--min-score", type=float, default=0.0)
    ap.add_argument("--source", choices=SOURCES, default="auto")
    ap.add_argument("--out", required=True)
    ap.add_argument("--timeout", type=float, default=30.0)
    args, ros_args = ap.parse_known_args()

    rclpy.init(args=ros_args)
    node = Node("segment_client")
    client = ActionClient(node, Segment, "segment")
    if not client.wait_for_server(timeout_sec=args.timeout):
        raise SystemExit("segment action server not found")
    goal = Segment.Goal(labels=args.labels, camera=args.camera, min_score=args.min_score,
                        source=SOURCES[args.source])
    t0 = time.perf_counter()
    fut = client.send_goal_async(goal)
    rclpy.spin_until_future_complete(node, fut, timeout_sec=args.timeout)
    res_fut = fut.result().get_result_async()
    rclpy.spin_until_future_complete(node, res_fut, timeout_sec=args.timeout)
    rt_ms = (time.perf_counter() - t0) * 1000.0
    res = res_fut.result().result

    os.makedirs(args.out, exist_ok=True)
    summary = {"success": res.success, "message": res.message, "server_ms": round(res.latency_ms, 1),
               "round_trip_ms": round(rt_ms, 1), "instances": []}
    if res.success and res.rgb.data:
        rgb = img(res.rgb)
        cv2.imwrite(os.path.join(args.out, "rgb.png"), rgb[:, :, ::-1])
        if res.depth.data:
            cv2.imwrite(os.path.join(args.out, "depth.png"), img(res.depth))
        overlay = rgb.astype(np.float32)
        for i, inst in enumerate(res.instances):
            mask = img(inst.mask) > 0
            color = np.array(COLORS[i % len(COLORS)], dtype=np.float32)
            overlay[mask] = 0.45 * overlay[mask] + 0.55 * color
            x0, y0, x1, y1 = [int(v) for v in inst.box]
            cv2.imwrite(os.path.join(args.out, f"mask_{i}_{inst.label.replace(' ', '_')}.png"),
                        mask.astype(np.uint8) * 255)
            summary["instances"].append({"label": inst.label, "score": round(inst.score, 3),
                                         "box": [x0, y0, x1, y1], "mask_px": int(mask.sum())})
        overlay = overlay.astype(np.uint8)
        for i, inst in enumerate(res.instances):
            x0, y0, x1, y1 = [int(v) for v in inst.box]
            c = COLORS[i % len(COLORS)]
            cv2.rectangle(overlay, (x0, y0), (x1, y1), c, 2)
            cv2.putText(overlay, f"{inst.label} {inst.score:.2f}", (x0, max(12, y0 - 4)),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.5, c, 1)
        cv2.imwrite(os.path.join(args.out, "overlay.png"), overlay[:, :, ::-1])
    with open(os.path.join(args.out, "result.json"), "w") as f:
        json.dump(summary, f, indent=1)
    names = ", ".join(f"{d['label']} {d['score']}" for d in summary["instances"])
    print(f"{'OK' if res.success else 'FAIL'} {res.message} | server {res.latency_ms:.1f} ms, "
          f"round trip {rt_ms:.1f} ms | {names}")
    node.destroy_node()
    rclpy.try_shutdown()


if __name__ == "__main__":
    main()
