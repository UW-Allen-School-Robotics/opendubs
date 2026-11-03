"""The wall tag test: hold the head at a pan and tilt, take one back-camera frame, and say which AprilTags it
sees, where, and the head's angles at that moment (the user's go, 2026-10-25, for the atrium's video wall).

    POST   /api/tablet/tags   {"pan": deg, "tilt": deg, "settle_s": 1.0, "timeout_s": 20,
                               "tag_m": 0.2 | {"<id>": m, ...} | null, "size": {"w": 1920, "h": 1080}}
           -> {"head": {...}, "frame": {"w", "h", "t"}, "camera": {"k", "dist", "source"},
               "tags": [{"id", "corners": [[x, y] x4], "center": [x, y], "pose": {...} | absent}]}
    DELETE /api/tablet/tags   the back camera back to on-demand at its usual size (the hold stays: release
                              it with POST /api/head/hold {"op": "off"})

pan and tilt are the neck's commanded degrees, as POST /api/head/hold takes them (90, 90 is straight
ahead); without them the head stays where it is. The head is moved only as tracking would be: never while
an arm's shoulder is raised or its angle unknown (arm_clearance.py), and it must report itself still for
`settle_s` before the frame is taken.

THE FRAME comes the way the back camera's frames already come (tablet_relay.py: the page's JPEG over
/ws/tablet), at `size` while the test runs. It is read from memory, decoded, searched and dropped: no
frame is kept or written, and none is in the answer. Only the tags' points go out.

DETECTION: OpenCV's ArUco module, AprilTag 36h11, corners refined to sub-pixel. POSE, for a tag whose
side `tag_m` is given: solvePnP (IPPE_SQUARE) with the camera matrix for this frame, in the camera's
optical frame (x right, y down, z out of the lens), in metres. The camera matrix, best first:
  1. this robot's calibration of the back camera, if one is kept (tablet_relay.camera_info, the same file)
     and it is for this frame size;
  2. the tablet's own calibration (Camera2 LENS_INTRINSIC_CALIBRATION and LENS_DISTORTION);
  3. its focal length and sensor size (LENS_INFO_AVAILABLE_FOCAL_LENGTHS, SENSOR_INFO_PHYSICAL_SIZE).
2 and 3 assume the frame is the sensor's active area, centred and cropped to the frame's shape, then
scaled: what a camera does for a stream of another shape. The answer names which one it used.
"""
from __future__ import annotations

import asyncio
import math
import time

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

router = APIRouter()
CAM = "rear"
DEFAULT_SIZE = {"w": 1920, "h": 1080, "fps": 2, "q": 0.95}
STILL_TOL_DEG = 1.0                # the neck reports itself within this of its target
_busy = asyncio.Lock()


# ---- the camera matrix --------------------------------------------------------------------------

def _back(intrinsics) -> dict | None:
    """The tablet's back camera from the app's cameraIntrinsics(): the first one facing back."""
    for c in intrinsics or []:
        if isinstance(c, dict) and c.get("facing") == "back":
            return c
    return None


def frame_matrix(cam: dict | None, w: int, h: int):
    """(K as 3x3 lists, distortion in OpenCV's order or None, source) for a w x h frame, from the tablet's
    optics; None when they are not enough."""
    if not cam or w <= 0 or h <= 0:
        return None
    area = cam.get("pre_active") if cam.get("intrinsic") else cam.get("active")
    area = area or cam.get("active")
    if not (isinstance(area, list) and len(area) == 4):
        return None
    aw, ah = area[2] - area[0], area[3] - area[1]
    if aw <= 0 or ah <= 0:
        return None
    if w / h >= aw / ah:                                   # full width, the top and bottom cropped
        scale = w / aw
        x0, y0 = 0.0, (ah - h / scale) / 2
    else:                                                  # full height, the sides cropped
        scale = h / ah
        x0, y0 = (aw - w / scale) / 2, 0.0
    i = cam.get("intrinsic")
    if isinstance(i, list) and len(i) == 5 and i[0] > 0 and i[1] > 0:
        fx, fy, cx, cy, s = i
        k = [[fx * scale, s * scale, (cx - x0) * scale], [0.0, fy * scale, (cy - y0) * scale], [0.0, 0.0, 1.0]]
        d = cam.get("distortion")
        dist = [d[0], d[1], d[3], d[4], d[2]] if isinstance(d, list) and len(d) == 5 else None   # k1 k2 p1 p2 k3
        return k, dist, "the tablet's own calibration (Camera2 LENS_INTRINSIC_CALIBRATION)"
    f, mm, px = cam.get("focal_mm"), cam.get("sensor_mm"), cam.get("pixel_array")
    if not (f and mm and px and mm[0] > 0 and mm[1] > 0):
        return None
    fx = f[0] * px[0] / mm[0] * scale                      # mm -> sensor pixels -> frame pixels
    fy = f[0] * px[1] / mm[1] * scale
    return ([[fx, 0.0, w / 2], [0.0, fy, h / 2], [0.0, 0.0, 1.0]], None,
            "the tablet's focal length and sensor size (Camera2), centred")


def robot_calibration(w: int, h: int):
    """This robot's calibration of the back camera, if it keeps one for this size: (K, dist, source)."""
    import tablet_relay
    info = tablet_relay.camera_info(CAM, w, h, {})
    if not any(info["k"]):
        return None
    k = info["k"]
    return [k[0:3], k[3:6], k[6:9]], list(info["d"]) or None, "this robot's calibration of the back camera"


# ---- the tags -----------------------------------------------------------------------------------

def detect(jpeg: bytes, k=None, dist=None, tag_m=None) -> list[dict]:
    """The AprilTag 36h11 tags in a JPEG: id, corners (OpenCV's order: the tag's top-left, top-right,
    bottom-right, bottom-left), centre, and its pose where the camera matrix and the tag's side are known."""
    import cv2
    import numpy as np
    img = cv2.imdecode(np.frombuffer(jpeg, np.uint8), cv2.IMREAD_GRAYSCALE)
    if img is None:
        raise ValueError("the frame is not a JPEG OpenCV can read")
    params = cv2.aruco.DetectorParameters()
    params.cornerRefinementMethod = cv2.aruco.CORNER_REFINE_SUBPIX
    det = cv2.aruco.ArucoDetector(cv2.aruco.getPredefinedDictionary(cv2.aruco.DICT_APRILTAG_36h11), params)
    corners, ids, _ = det.detectMarkers(img)
    out = []
    K = np.array(k, dtype=np.float64) if k is not None else None
    D = np.array(dist, dtype=np.float64) if dist is not None else None
    for c, i in zip(corners, [] if ids is None else ids.flatten()):
        pts = c.reshape(4, 2).astype(np.float64)
        e = {"id": int(i), "corners": [[round(float(x), 2), round(float(y), 2)] for x, y in pts],
             "center": [round(float(v), 2) for v in pts.mean(axis=0)]}
        side = tag_m.get(str(int(i))) if isinstance(tag_m, dict) else tag_m
        if K is not None and isinstance(side, (int, float)) and side > 0:
            s = side / 2
            obj = np.array([[-s, s, 0], [s, s, 0], [s, -s, 0], [-s, -s, 0]], dtype=np.float64)
            ok, rvec, tvec = cv2.solvePnP(obj, pts, K, D, flags=cv2.SOLVEPNP_IPPE_SQUARE)
            if ok:
                back, _ = cv2.projectPoints(obj, rvec, tvec, K, D)
                err = float(np.sqrt(np.mean(np.sum((back.reshape(4, 2) - pts) ** 2, axis=1))))
                t = tvec.ravel()
                e["pose"] = {"t_m": [round(float(v), 4) for v in t], "rvec": [round(float(v), 5) for v in rvec.ravel()],
                             "distance_m": round(float(np.linalg.norm(t)), 4), "reproj_px": round(err, 3), "side_m": side}
        out.append(e)
    return sorted(out, key=lambda e: e["id"])


# ---- the head -----------------------------------------------------------------------------------

def head_now() -> dict:
    import neck_state
    last, st = neck_state.last, neck_state.status()
    out = {"pan_cmd": last["pan"], "tilt_cmd": last["tilt"], "moving": last["moving"], "age_s": st["age_s"],
           "unknown": st["unknown"], "hold": last.get("hold")}
    if last["pan"] is not None and last["tilt"] is not None:
        p, t = neck_state.to_radians(last["pan"], last["tilt"], neck_state._span())
        out.update(pan_rad=round(p, 5), tilt_rad=round(t, 5))
    return out


async def move_head(pan: float, tilt: float, settle_s: float, deadline: float) -> str | None:
    """Hold the head at (pan, tilt) and wait until it says it is still there for settle_s; why not, or None."""
    import arm_clearance
    import head
    import neck_follow
    import neck_state
    why = arm_clearance.why_not()
    if why:
        return f"the head was not moved: {why}"
    host = head.neck_host()
    if host is None:
        return "no neck is fitted"
    line = f"HOLD {pan:.1f} {tilt:.1f}"
    reply = await asyncio.get_running_loop().run_in_executor(None, neck_follow._ask, host, line, 0.5)
    if not reply or not reply.startswith("OK"):
        return f"the neck answered {reply!r}" if reply else "the neck did not answer"
    still_since = None
    while time.monotonic() < deadline:
        last, st = neck_state.last, neck_state.status()
        there = (not st["unknown"] and last["moving"] is False and last["pan"] is not None
                 and abs(last["pan"] - pan) <= STILL_TOL_DEG and abs(last["tilt"] - tilt) <= STILL_TOL_DEG)
        if there:
            still_since = still_since or time.monotonic()
            if time.monotonic() - still_since >= settle_s:
                return None
        else:
            still_since = None
        await asyncio.sleep(0.05)
    return f"the head did not settle at pan {pan:g}, tilt {tilt:g} in time (it reports {head_now()})"


# ---- the endpoints ------------------------------------------------------------------------------

def _size(v) -> dict:
    out = dict(DEFAULT_SIZE)
    if isinstance(v, dict):
        for key, lo, hi in (("w", 160, 4096), ("h", 120, 4096), ("fps", 1, 15), ("q", 0.5, 1.0)):
            x = v.get(key)
            if isinstance(x, (int, float)) and not isinstance(x, bool) and lo <= x <= hi:
                out[key] = x
    return out


@router.post("/api/tablet/tags")
async def tags(request: Request) -> JSONResponse:
    import tablet_relay
    try:
        b = await request.json()
    except ValueError:
        b = None
    if not isinstance(b, dict):
        return JSONResponse({"error": "send a JSON object"}, status_code=400)
    num = lambda x: isinstance(x, (int, float)) and not isinstance(x, bool) and math.isfinite(x)
    pan, tilt = b.get("pan"), b.get("tilt")
    if (pan is None) != (tilt is None) or (pan is not None and not (num(pan) and num(tilt))):
        return JSONResponse({"error": "pan and tilt go together, in the neck's degrees"}, status_code=400)
    settle = float(b["settle_s"]) if num(b.get("settle_s")) else 1.0
    timeout = float(b["timeout_s"]) if num(b.get("timeout_s")) else 20.0
    if _busy.locked():
        return JSONResponse({"error": "a tag test is already running"}, status_code=409)
    async with _busy:
        if tablet_relay.state["page"] is None:
            return JSONResponse({"error": "the tablet's kiosk page is not connected"}, status_code=409)
        deadline = time.monotonic() + timeout
        if pan is not None:
            why = await move_head(float(pan), float(tilt), settle, deadline)
            if why:
                return JSONResponse({"error": why, "head": head_now()}, status_code=409)
        size = _size(b.get("size"))
        await tablet_relay.set_camera_size(CAM, size)
        tablet_relay.state["camera"][CAM]["mode"] = "on"
        await tablet_relay.tell_page_cameras()
        asked = time.time()
        while time.monotonic() < deadline:                 # the first frame taken after the head was still
            f = tablet_relay.latest.get(CAM)
            if f and f["t"] > asked and f["w"]:
                break
            await asyncio.sleep(0.05)
        else:
            err = tablet_relay.state["camera"][CAM].get("error")
            return JSONResponse({"error": "no back-camera frame arrived" + (f": {err}" if err else ""),
                                 "head": head_now()}, status_code=504)
        head = head_now()                                  # the head when the frame is in hand: still, by now
        w, h, taken = f["w"], f["h"], f["t"]
        cal = robot_calibration(w, h) or frame_matrix(_back(tablet_relay.state["intrinsics"]), w, h)
        k, dist, source = cal if cal else (None, None, "none: no calibration and no optics from the tablet (an app before 0.7.0)")
        try:
            found = await asyncio.get_running_loop().run_in_executor(None, detect, f["jpeg"], k, dist, b.get("tag_m"))
        except Exception as e:                             # noqa: BLE001
            return JSONResponse({"error": f"detection failed: {e}"}, status_code=500)
        return JSONResponse({"head": head, "frame": {"w": w, "h": h, "t": round(taken, 3)},
                             "camera": {"k": k, "dist": dist, "source": source, "back": _back(tablet_relay.state["intrinsics"])},
                             "tags": found})


@router.delete("/api/tablet/tags")
async def tags_done() -> JSONResponse:
    import tablet_relay
    tablet_relay.state["camera"][CAM]["mode"] = "auto"
    await tablet_relay.set_camera_size(CAM, None)
    await tablet_relay.tell_page_cameras()
    return JSONResponse({"ok": True, "camera": tablet_relay.state["camera"][CAM]["mode"]})
