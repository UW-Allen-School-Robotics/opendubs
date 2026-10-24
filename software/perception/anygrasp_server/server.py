"""AnyGrasp over a WebSocket, for the GPU workstation (not the robot).

Written from the lab's original AnyGrasp server, which keeps running unchanged on port 8767; this
one runs beside it on 8768 and takes the same payload, so either serves
`perception_clients/anygrasp.py`. What differs:

- requests run one at a time (one model; the original runs them in parallel threads);
- an error returns its real message and the stage it failed in;
- messages up to 64 MB (aiohttp's default is 4 MB; a full 1280x720 frame is 7.4 MB as base64);
- the grasp is chosen by the upright score only when the request says where "up" is
  (`options.up_cam`, or `cam_to_base_quat`), or `--camera_tilt_rad` is given. Otherwise it takes
  AnyGrasp's best raw score and reports no upright or tilt: with no up direction, a tilt of 0
  treats the camera's optical axis as up, which is wrong for a camera looking forward;
- the reply adds `mode`, `selection`, `candidates` (top_k), `points` and `timing_ms`;
- `GET /health` answers without running the model; the visualisation is optional (`--save_vis`).

Run (in the AnyGrasp SDK's environment, next to its gsnet module and licence):

  python server.py --checkpoint_path log/checkpoint_detection.tar --port 8768

Routes: `ws://HOST:8768/grasp` (also `/get_grasp_config`), `http://HOST:8768/health`.

Request (JSON text frame): `rgb` uint8 HxWx3, `depth` (units of `depth_scale` metres, default
0.001), `mask` (>0 = object), `intrinsics` 3x3, each as {dtype, shape, data (base64)}; or the
whole set as `npz_b64`. Optional: `depth_scale`, `cam_to_base_quat` [x, y, z, w] (camera optical
frame -> a z-up base frame), and `options`:

  up_cam      [x, y, z]  world up in the camera frame (minus gravity from an accelerometer)
  use_raw_best  bool     take the best raw score even when up is known
  flip        "away" | "right" | "none"   wrist side of the chosen grasp (below)
  top_k       int        candidates to return (default 5)
  lims        [xmin, xmax, ymin, ymax, zmin, zmax]   workspace in the camera frame (metres)

Reply: {"status": "success", "mode", "selection", "best_grasp": {translation, rotation, score,
width, tilt_from_floor_deg, upright_score, combined_score}, "candidates": [...], "points",
"timing_ms"} or {"status": "error", "stage", "message"}. Poses are in the camera optical frame,
GraspNet convention (rotation columns: approach, closing, binormal).
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import binascii
import io
import json
import math
import os
import time
import traceback

import numpy as np
from aiohttp import web

UPRIGHT_POWER = 2        # combined = raw_score x upright_score^k
MIN_GRASP_SCORE = 0.01   # AnyGrasp raw score noise floor
MIN_GRASP_WIDTH = 0.05   # metres; below this, rim pinches
WARN_TILT_DEG = 35.0
DEFAULT_LIMS = [-1.0, 1.0, -1.0, 1.0, 0.0, 1.5]
MAX_MSG_BYTES = 64 * 1024 * 1024


class RequestError(ValueError):
    def __init__(self, stage: str, message: str):
        super().__init__(message)
        self.stage = stage


# --- payload ------------------------------------------------------------------------------------

def _decode_array(spec, key):
    if not isinstance(spec, dict) or not {"dtype", "shape", "data"} <= set(spec):
        raise RequestError("decode", f"'{key}' must be an object with dtype, shape and data")
    try:
        dtype = np.dtype(spec["dtype"])
    except TypeError as exc:
        raise RequestError("decode", f"'{key}': invalid dtype {spec['dtype']!r}") from exc
    shape = spec["shape"]
    if not isinstance(shape, list) or not all(isinstance(x, int) and x >= 0 for x in shape):
        raise RequestError("decode", f"'{key}': invalid shape {shape!r}")
    try:
        raw = base64.b64decode(spec["data"], validate=True)
    except (binascii.Error, ValueError) as exc:
        raise RequestError("decode", f"'{key}': invalid base64") from exc
    arr = np.frombuffer(raw, dtype=dtype)
    if arr.size != int(np.prod(shape, dtype=np.int64)):
        raise RequestError("decode", f"'{key}': {arr.size} elements for shape {shape}")
    return arr.reshape(shape)


def parse_request(text: str) -> dict:
    try:
        p = json.loads(text)
    except json.JSONDecodeError as exc:
        raise RequestError("decode", "the message is not JSON") from exc
    if not isinstance(p, dict):
        raise RequestError("decode", "the message must be a JSON object")
    if "npz_b64" in p:
        try:
            with np.load(io.BytesIO(base64.b64decode(p["npz_b64"], validate=True)), allow_pickle=False) as d:
                req = {k: d[k] for k in ("rgb", "depth", "mask", "intrinsics")}
                if "cam_to_base_quat" in d:
                    req["cam_to_base_quat"] = d["cam_to_base_quat"]
                if "depth_scale" in d:
                    req["depth_scale"] = float(d["depth_scale"])
        except (KeyError, ValueError, binascii.Error) as exc:
            raise RequestError("decode", f"npz_b64: {exc}") from exc
    else:
        missing = [k for k in ("rgb", "depth", "mask", "intrinsics") if k not in p]
        if missing:
            raise RequestError("decode", f"missing keys: {missing}")
        req = {k: _decode_array(p[k], k) for k in ("rgb", "depth", "mask", "intrinsics")}
        if "cam_to_base_quat" in p:
            q = p["cam_to_base_quat"]
            req["cam_to_base_quat"] = _decode_array(q, "cam_to_base_quat") if isinstance(q, dict) else np.asarray(q, float)
    req["depth_scale"] = float(p.get("depth_scale", req.get("depth_scale", 0.001)))
    req["options"] = p.get("options") or {}
    rgb, depth, mask = req["rgb"], req["depth"], req["mask"]
    if rgb.ndim != 3 or rgb.shape[2] != 3:
        raise RequestError("decode", f"rgb must be HxWx3, got {rgb.shape}")
    if depth.shape[:2] != rgb.shape[:2] or mask.shape[:2] != rgb.shape[:2]:
        raise RequestError("decode", f"rgb {rgb.shape[:2]}, depth {depth.shape[:2]} and mask "
                                     f"{mask.shape[:2]} must be the same size")
    return req


# --- geometry -----------------------------------------------------------------------------------

def quat_to_matrix(q):
    qx, qy, qz, qw = np.asarray(q, dtype=np.float64) / np.linalg.norm(q)
    return np.array([
        [1 - 2 * (qy ** 2 + qz ** 2), 2 * (qx * qy - qz * qw), 2 * (qx * qz + qy * qw)],
        [2 * (qx * qy + qz * qw), 1 - 2 * (qx ** 2 + qz ** 2), 2 * (qy * qz - qx * qw)],
        [2 * (qx * qz - qy * qw), 2 * (qy * qz + qx * qw), 1 - 2 * (qx ** 2 + qy ** 2)],
    ])


def tilt_up_cam(tilt_rad: float):
    """The original server's camera_tilt_rad convention, as world up in the camera frame."""
    c, s = math.cos(tilt_rad), math.sin(tilt_rad)
    return np.array([0.0, -s, c])


def tilt_from_floor_deg(R, up):
    """Angle of the approach (column 0) from the floor plane: 0 = side grasp, 90 = top-down."""
    cos_n = float(np.clip(abs(np.dot(R[:, 0], up)), 0.0, 1.0))
    return math.degrees(math.pi / 2.0 - math.acos(cos_n))


def upright_score(R, up):
    """|binormal . up|: 1 = fingers close horizontally on a side grasp, 0 = top-down or on edge."""
    return abs(float(np.dot(R[:, 2], up)))


def flip_about_approach(grasp, rule: str) -> bool:
    """Rotate the grasp 180 degrees about its approach axis when the rule asks. The jaws swap; the
    contacts and approach are unchanged, so only the wrist side moves.
      away:  binormal points away from the camera (the original's default, "for the green cup")
      right: binormal points to the right of the image (the original's "for the basket")
    """
    R = grasp.rotation_matrix.astype(np.float64)
    if rule == "away":
        t = grasp.translation.astype(np.float64)
        ray = t / np.linalg.norm(t) if np.linalg.norm(t) > 1e-6 else np.array([0.0, 0.0, 1.0])
        flip = float(np.dot(R[:, 2], ray)) < 0.0
    elif rule == "right":
        flip = R[0, 2] < 0.0
    else:
        return False
    if flip:
        grasp.rotation_matrix = (R @ np.diag([1.0, -1.0, -1.0])).astype(np.float32)
    return flip


# --- inference ----------------------------------------------------------------------------------

class GraspService:
    def __init__(self, anygrasp, cfgs):
        self.anygrasp = anygrasp
        self.cfgs = cfgs

    def up_direction(self, req):
        """-> (up in the camera frame or None, where it came from)."""
        opts = req["options"]
        if opts.get("up_cam") is not None:
            up = np.asarray(opts["up_cam"], dtype=np.float64)
            if up.shape != (3,) or np.linalg.norm(up) < 1e-6:
                raise RequestError("options", f"up_cam must be a non-zero 3-vector, got {opts['up_cam']!r}")
            return up / np.linalg.norm(up), "up_cam"
        if req.get("cam_to_base_quat") is not None:
            return quat_to_matrix(req["cam_to_base_quat"])[2, :], "cam_to_base_quat"
        if self.cfgs.camera_tilt_rad is not None:
            return tilt_up_cam(self.cfgs.camera_tilt_rad), "camera_tilt_rad"
        return None, "none"

    def infer(self, req) -> dict:
        timing = {}
        t0 = time.perf_counter()
        rgb, depth, mask, K = req["rgb"], req["depth"], req["mask"], req["intrinsics"]
        opts = req["options"]
        fx, fy, cx, cy = float(K[0, 0]), float(K[1, 1]), float(K[0, 2]), float(K[1, 2])
        up, up_source = self.up_direction(req)

        valid = (mask > 0) & (depth > 0)
        v, u = np.where(valid)
        z = depth[v, u].astype(np.float64) * req["depth_scale"]
        points = np.stack([(u - cx) / fx * z, (v - cy) / fy * z, z], axis=-1).astype(np.float32)
        colors = (rgb.astype(np.float32) / 255.0)[v, u]
        if len(points) < 50:
            raise RequestError("cloud", f"only {len(points)} object points with depth (need 50)")
        lims = [float(x) for x in opts.get("lims", DEFAULT_LIMS)]
        inside = ((points[:, 0] >= lims[0]) & (points[:, 0] <= lims[1]) & (points[:, 1] >= lims[2])
                  & (points[:, 1] <= lims[3]) & (points[:, 2] >= lims[4]) & (points[:, 2] <= lims[5]))
        if inside.sum() < 50:
            raise RequestError("cloud", f"only {int(inside.sum())} of {len(points)} points inside the "
                                        f"workspace {lims} (median depth {np.median(z):.2f} m)")

        t1 = time.perf_counter()
        gg, _cloud = self.anygrasp.get_grasp(points, colors, lims=lims, apply_object_mask=True,
                                             dense_grasp=True, collision_detection=True)
        timing["anygrasp"] = (time.perf_counter() - t1) * 1000.0
        if len(gg) == 0:
            raise RequestError("anygrasp", "no grasp left after collision detection")
        gg_pick = gg.nms().sort_by_score()[0:20]

        use_raw = bool(opts.get("use_raw_best", self.cfgs.use_raw_best)) or up is None
        flip_rule = opts.get("flip", "away" if use_raw else "none")
        if flip_rule not in ("away", "right", "none"):
            raise RequestError("options", f"flip must be away, right or none, got {flip_rule!r}")

        cands = []
        for g in gg_pick:
            flip_about_approach(g, flip_rule)
            R = g.rotation_matrix.astype(np.float64)
            tilt = tilt_from_floor_deg(R, up) if up is not None else None
            upr = upright_score(R, up) if up is not None else None
            raw = float(g.score)
            combo = raw * upr ** UPRIGHT_POWER if upr is not None else raw
            ok = raw >= MIN_GRASP_SCORE and float(g.width) >= MIN_GRASP_WIDTH
            cands.append({"grasp": g, "tilt": tilt, "upright": upr, "raw": raw, "combined": combo, "ok": ok})

        if use_raw:
            mode, selection = ("raw (no up direction)" if up is None else "raw"), "OK"
            order = sorted(cands, key=lambda c: c["raw"], reverse=True)
        else:
            mode = f"refined (up from {up_source})"
            pool = [c for c in cands if c["ok"]]
            if pool:
                order = sorted(pool, key=lambda c: c["combined"], reverse=True)
                selection = "WARN" if order[0]["tilt"] > WARN_TILT_DEG else "OK"
            else:
                order = sorted(cands, key=lambda c: c["upright"], reverse=True)
                selection = "FALLBACK"
        best = order[0]

        if self.cfgs.save_vis:
            _save_visualisation(rgb, depth, v, u, best["grasp"], os.path.join(self.cfgs.output_dir, "last_grasp.png"))
        timing["total"] = (time.perf_counter() - t0) * 1000.0
        top_k = int(opts.get("top_k", 5))
        return {
            "status": "success",
            "mode": mode,
            "selection": selection,
            "points": int(len(points)),
            "best_grasp": _grasp_json(best),
            "candidates": [_grasp_json(c) for c in order[1:1 + max(0, top_k)]],
            "timing_ms": {k: round(t, 1) for k, t in timing.items()},
        }


def _grasp_json(c):
    g = c["grasp"]
    rnd = lambda x, n: None if x is None else float(round(x, n))  # noqa: E731
    return {
        "translation": np.round(g.translation.astype(np.float64), 6).tolist(),
        "rotation": np.round(g.rotation_matrix.astype(np.float64), 6).tolist(),
        "score": rnd(c["raw"], 6),
        "width": rnd(float(g.width), 6),
        "tilt_from_floor_deg": rnd(c["tilt"], 4),
        "upright_score": rnd(c["upright"], 4),
        "combined_score": rnd(c["combined"], 6),
    }


def _save_visualisation(rgb, depth, v, u, best, path):
    """RGB with the object's centre marked, and depth with the mask overlaid."""
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    fig, axes = plt.subplots(1, 2, figsize=(14, 6))
    cx, cy = (int(np.mean(u)), int(np.mean(v))) if len(u) else (rgb.shape[1] // 2, rgb.shape[0] // 2)
    axes[0].imshow(rgb)
    axes[0].plot(cx, cy, marker="*", markersize=18, color="#00ffcc", markeredgecolor="black")
    t = best.translation
    axes[0].set_title(f"score {best.score:.3f}  t = ({t[0]:.3f}, {t[1]:.3f}, {t[2]:.3f}) m")
    d = depth.astype(np.float32)
    d[d == 0] = np.nan
    axes[1].imshow(d, cmap="inferno", interpolation="nearest")
    overlay = np.zeros((*depth.shape[:2], 4), dtype=np.float32)
    overlay[v, u] = [0.0, 1.0, 0.8, 0.35]
    axes[1].imshow(overlay, interpolation="nearest")
    for ax in axes:
        ax.axis("off")
    plt.tight_layout()
    plt.savefig(path, dpi=110, bbox_inches="tight")
    plt.close(fig)


# --- server -------------------------------------------------------------------------------------

def build_app(service: GraspService) -> web.Application:
    stats = {"requests": 0, "errors": 0, "started": time.time()}
    lock = None

    async def make_lock(_app):  # on the server's own loop: Python 3.9 binds a lock at creation
        nonlocal lock
        lock = asyncio.Lock()

    async def ws_handler(request: web.Request):
        ws = web.WebSocketResponse(max_msg_size=MAX_MSG_BYTES)
        await ws.prepare(request)
        peer = request.remote
        print(f"[ws] {peer} connected")
        async for msg in ws:
            if msg.type != web.WSMsgType.TEXT:
                if msg.type == web.WSMsgType.ERROR:
                    print(f"[ws] {peer} error: {ws.exception()}")
                continue
            stats["requests"] += 1
            try:
                req = parse_request(msg.data)
                async with lock:  # one model: one request at a time
                    reply = await asyncio.get_running_loop().run_in_executor(None, service.infer, req)
                b = reply["best_grasp"]
                print(f"[ws] {peer}: {reply['mode']} {reply['selection']}, {reply['points']} points, "
                      f"score {b['score']:.3f}, t {b['translation']}, {reply['timing_ms']['total']:.0f} ms")
            except RequestError as exc:
                stats["errors"] += 1
                reply = {"status": "error", "stage": exc.stage, "message": str(exc)}
                print(f"[ws] {peer}: {exc.stage}: {exc}")
            except Exception as exc:  # noqa: BLE001 - report it, keep serving
                stats["errors"] += 1
                reply = {"status": "error", "stage": "internal", "message": f"{type(exc).__name__}: {exc}"}
                traceback.print_exc()
            await ws.send_str(json.dumps(reply))
        print(f"[ws] {peer} disconnected")
        return ws

    async def health(_request):
        return web.json_response({"status": "ok", "busy": lock.locked(), **stats,
                                  "max_gripper_width": service.cfgs.max_gripper_width,
                                  "camera_tilt_rad": service.cfgs.camera_tilt_rad,
                                  "use_raw_best": service.cfgs.use_raw_best})

    app = web.Application(client_max_size=MAX_MSG_BYTES)
    app.on_startup.append(make_lock)
    app.router.add_get("/grasp", ws_handler)
    app.router.add_get("/get_grasp_config", ws_handler)
    app.router.add_get("/health", health)
    return app


def main():
    ap = argparse.ArgumentParser(description="AnyGrasp WebSocket server")
    ap.add_argument("--checkpoint_path", default="log/checkpoint_detection.tar")
    ap.add_argument("--max_gripper_width", type=float, default=0.1)
    ap.add_argument("--gripper_height", type=float, default=0.03)
    ap.add_argument("--top_down_grasp", default="true")
    ap.add_argument("--camera_tilt_rad", type=float, default=None,
                    help="fallback up direction when a request gives none (original convention)")
    ap.add_argument("--use_raw_best", action="store_true", help="default to the best raw score")
    ap.add_argument("--save_vis", action="store_true", help="write last_grasp.png to --output_dir")
    ap.add_argument("--output_dir", default="./log_ws_outputs")
    ap.add_argument("--host", default="0.0.0.0")
    ap.add_argument("--port", type=int, default=8768)
    cfgs = ap.parse_args()
    cfgs.max_gripper_width = max(0.0, min(0.1, cfgs.max_gripper_width))
    cfgs.debug = False  # gsnet reads it; the Open3D windows are not part of this server
    os.makedirs(cfgs.output_dir, exist_ok=True)

    from gsnet import AnyGrasp

    print("[startup] loading AnyGrasp ...")
    anygrasp = AnyGrasp(cfgs)
    anygrasp.load_net()
    print(f"[startup] ready on ws://{cfgs.host}:{cfgs.port}/grasp")
    web.run_app(build_app(GraspService(anygrasp, cfgs)), host=cfgs.host, port=cfgs.port)


if __name__ == "__main__":
    main()
