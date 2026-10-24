"""Check an AnyGrasp server without the robot: send one object, print the grasp.

  python3 anygrasp_probe.py --url ws://HOST:8768/grasp                 synthetic mug on a table
  python3 anygrasp_probe.py --url ws://HOST:8767/get_grasp_config      the original server
  python3 anygrasp_probe.py --url … --from-dir RESULTS --K fx fy cx cy  a saved segment_client query
  python3 anygrasp_probe.py --url … --no-crop --size 1280x720           send the whole frame

The synthetic scene is a camera 0.5 m above a table, pitched 35 degrees down, looking at a
cylinder 8 cm wide and 10 cm tall 0.6 m in front of it. Its depth is uint16 mm, as the Femto Bolt
publishes it. Needs numpy and websockets (and opencv or pillow for --from-dir and --save).
"""

from __future__ import annotations

import argparse
import glob
import json
import math
import os
import sys

import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from perception_clients.anygrasp import AnyGraspClient, GraspError  # noqa: E402


def synthetic_scene(w: int, h: int, pitch_deg: float = 35.0, cam_height: float = 0.5,
                    mug_at: float = 0.6, radius: float = 0.04, height: float = 0.10):
    """-> rgb, depth (uint16 mm), mask, K, up_cam. World: x forward, y left, z up, table z = 0."""
    f = 600.0 * w / 640.0
    K = np.array([[f, 0, w / 2.0], [0, f, h / 2.0], [0, 0, 1]])
    p = math.radians(pitch_deg)
    c, s = math.cos(p), math.sin(p)
    R_wc = np.array([[0, -s, c], [-1, 0, 0], [0, -c, -s]], dtype=np.float64)  # columns: cam x, y, z
    t_wc = np.array([0.0, 0.0, cam_height])

    # One ray per pixel, intersected with the table and the cylinder; the nearest hit wins.
    u, v = np.meshgrid(np.arange(w), np.arange(h))
    rays_c = np.stack([(u - K[0, 2]) / f, (v - K[1, 2]) / f, np.ones_like(u, dtype=np.float64)], -1)
    rays_w = np.einsum("ij,hwj->hwi", R_wc, rays_c)
    o = t_wc
    t_table = np.where(rays_w[..., 2] < 0, -o[2] / np.minimum(rays_w[..., 2], -1e-9), np.inf)

    dx, dy, dz = rays_w[..., 0], rays_w[..., 1], rays_w[..., 2]
    ox, oy = o[0] - mug_at, o[1]
    a = dx ** 2 + dy ** 2
    b = 2 * (ox * dx + oy * dy)
    cc = ox ** 2 + oy ** 2 - radius ** 2
    disc = b ** 2 - 4 * a * cc
    t_side = np.where(disc >= 0, (-b - np.sqrt(np.maximum(disc, 0))) / (2 * np.maximum(a, 1e-12)), np.inf)
    z_side = o[2] + t_side * dz
    t_side = np.where((t_side > 0) & (z_side >= 0) & (z_side <= height), t_side, np.inf)
    t_top = np.where(dz < 0, (height - o[2]) / np.minimum(dz, -1e-9), np.inf)
    hx, hy = o[0] + t_top * dx - mug_at, o[1] + t_top * dy
    t_top = np.where(hx ** 2 + hy ** 2 <= radius ** 2, t_top, np.inf)
    t_obj = np.minimum(t_side, t_top)

    t = np.minimum(t_table, t_obj)
    mask = (t_obj <= t_table) & np.isfinite(t_obj)
    depth_m = np.where(np.isfinite(t), t * rays_c[..., 2], 0.0)
    depth = np.clip(np.round(depth_m * 1000.0), 0, 65535).astype(np.uint16)
    rgb = np.full((h, w, 3), (150, 120, 90), dtype=np.uint8)  # the table
    rgb[mask] = (40, 160, 70)                                   # a green mug
    rgb[depth == 0] = (200, 200, 210)
    up_cam = R_wc.T @ np.array([0.0, 0.0, 1.0])
    return rgb, depth, (mask * 255).astype(np.uint8), K, up_cam


def _read(path, flags=None):
    try:
        import cv2

        img = cv2.imread(path, cv2.IMREAD_UNCHANGED)
        return img[:, :, ::-1] if img is not None and img.ndim == 3 else img
    except ImportError:
        from PIL import Image

        return np.array(Image.open(path))


def from_dir(path, K_vals, label=None):
    """A segment_client results directory: rgb.png, depth.png (16-bit mm), mask_<i>_<label>.png."""
    rgb = _read(os.path.join(path, "rgb.png"))
    depth = _read(os.path.join(path, "depth.png")).astype(np.uint16)
    masks = sorted(glob.glob(os.path.join(path, f"mask_*_{label.replace(' ', '_')}.png" if label else "mask_*.png")))
    if not masks:
        raise SystemExit(f"no mask in {path}")
    fx, fy, cx, cy = K_vals
    return rgb, depth, _read(masks[0]), np.array([[fx, 0, cx], [0, fy, cy], [0, 0, 1.0]]), None


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--url", default=os.environ.get("ANYGRASP_URL", ""))
    ap.add_argument("--size", default="640x480", help="synthetic frame WxH")
    ap.add_argument("--mug-at", type=float, default=0.6, help="synthetic mug distance (m)")
    ap.add_argument("--from-dir")
    ap.add_argument("--label")
    ap.add_argument("--K", nargs=4, type=float, metavar=("FX", "FY", "CX", "CY"))
    ap.add_argument("--no-crop", action="store_true")
    ap.add_argument("--no-up", action="store_true", help="do not send the up direction")
    ap.add_argument("--options", default="{}", help='JSON, e.g. \'{"top_k": 5}\'')
    ap.add_argument("--timeout", type=float, default=30.0)
    ap.add_argument("--json", help="write the reply here")
    args = ap.parse_args()

    if args.from_dir:
        if not args.K:
            raise SystemExit("--from-dir needs --K fx fy cx cy (the colour camera_info)")
        rgb, depth, mask, K, up = from_dir(args.from_dir, args.K, args.label)
    else:
        w, h = (int(x) for x in args.size.split("x"))
        rgb, depth, mask, K, up = synthetic_scene(w, h, mug_at=args.mug_at)
    client = AnyGraspClient(args.url, timeout_s=args.timeout)
    try:
        r = client.grasp(rgb, depth, mask, K, crop=not args.no_crop,
                         up_cam=None if args.no_up else up, options=json.loads(args.options))
    except GraspError as exc:
        print(f"FAIL {exc}")
        return 1
    finally:
        client.close()
    b = r.best
    approach = b.rotation[:, 0]
    print(f"OK {args.url} | mode {r.mode or '(not reported)'} | {r.points} points, "
          f"{r.request_bytes / 1e3:.0f} kB sent, round trip {r.round_trip_ms:.0f} ms, server {r.server_ms:.0f} ms")
    print(f"  best: t = {np.round(b.translation, 3).tolist()} m, width {b.width:.3f} m, score {b.score:.3f}, "
          f"upright {b.upright_score:.2f}, tilt {b.tilt_from_floor_deg:.1f} deg")
    print(f"  approach (camera frame) = {np.round(approach, 2).tolist()}, quaternion xyzw = "
          f"{[round(q, 3) for q in b.quaternion()]}")
    if r.candidates:
        print(f"  {len(r.candidates)} candidates; scores {[round(c.score, 3) for c in r.candidates]}")
    if args.json:
        with open(args.json, "w") as f:
            json.dump({"mode": r.mode, "best": {"translation": b.translation.tolist(),
                       "rotation": b.rotation.tolist(), "width": b.width, "score": b.score}}, f, indent=1)
    return 0


if __name__ == "__main__":
    sys.exit(main())
