#!/usr/bin/env python3
"""Track the nearest person in front of an Orbbec Femto Bolt, detect their hand
landmarks with MediaPipe, and print 3D (X, Y, Z) camera-frame coordinates.

Coordinates are in the color camera frame, in meters:
  X -> right, Y -> down, Z -> forward (away from camera).

Run:  source .venv/bin/activate && python hand3d.py [--show] [--all]
      With a ROS orbbec_camera node owning the camera, source /opt/ros/jazzy/setup.bash
      first and add --source ros.
"""
import argparse
import math
import os
import time

import cv2
import mediapipe as mp
import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
HAND_MODEL = os.path.join(HERE, "models", "hand_landmarker.task")
POSE_MODEL = os.path.join(HERE, "models", "pose_landmarker_full.task")

HAND_NAMES = [
    "WRIST", "THUMB_CMC", "THUMB_MCP", "THUMB_IP", "THUMB_TIP",
    "INDEX_MCP", "INDEX_PIP", "INDEX_DIP", "INDEX_TIP",
    "MIDDLE_MCP", "MIDDLE_PIP", "MIDDLE_DIP", "MIDDLE_TIP",
    "RING_MCP", "RING_PIP", "RING_DIP", "RING_TIP",
    "PINKY_MCP", "PINKY_PIP", "PINKY_DIP", "PINKY_TIP",
]
KEY_POINTS = [0, 4, 8, 9]  # wrist, thumb tip, index tip, middle MCP (palm centre)
HAND_CONNECTIONS = [
    (0, 1), (1, 2), (2, 3), (3, 4), (0, 5), (5, 6), (6, 7), (7, 8),
    (5, 9), (9, 10), (10, 11), (11, 12), (9, 13), (13, 14), (14, 15), (15, 16),
    (13, 17), (0, 17), (17, 18), (18, 19), (19, 20),
]
PALM_POINTS = [0, 5, 9, 13, 17]  # wrist + finger MCPs outline the palm
# Pose landmark indices
L_SHOULDER, R_SHOULDER, L_HIP, R_HIP = 11, 12, 23, 24
L_WRIST, R_WRIST = 15, 16
# Per body side (0 = person's left, 1 = right): pose points on the hand, and the elbow
POSE_HAND = [{"WRIST": 15, "PINKY": 17, "INDEX": 19, "THUMB": 21},
             {"WRIST": 16, "PINKY": 18, "INDEX": 20, "THUMB": 22}]
POSE_ELBOW = [13, 14]


# --------------------------------------------------------------------------- camera
def start_camera(width, height):
    global ob
    import pyorbbecsdk as ob  # lazy: not needed with --source ros
    pipeline = ob.Pipeline()
    config = ob.Config()
    color_profiles = pipeline.get_stream_profile_list(ob.OBSensorType.COLOR_SENSOR)
    color_profile = color_profiles.get_video_stream_profile(width, height, ob.OBFormat.RGB, 30)
    config.enable_stream(color_profile)
    depth_profiles = pipeline.get_stream_profile_list(ob.OBSensorType.DEPTH_SENSOR)
    config.enable_stream(depth_profiles.get_default_video_stream_profile())
    config.set_frame_aggregate_output_mode(ob.OBFrameAggregateOutputMode.FULL_FRAME_REQUIRE)
    pipeline.enable_frame_sync()
    pipeline.start(config)

    intr = color_profile.get_intrinsic()
    K = (intr.fx, intr.fy, intr.cx, intr.cy)
    align = ob.AlignFilter(align_to_stream=ob.OBStreamType.COLOR_STREAM)
    return pipeline, align, K


def get_frames(pipeline, align):
    """Return (rgb uint8 HxWx3, depth_m float32 HxW aligned to color, stamp s) or None."""
    frames = pipeline.wait_for_frames(1000)
    stamp = time.time()
    if frames is None:
        return None
    frames = align.process(frames)
    if frames is None:
        return None
    frames = frames.as_frame_set()
    color, depth = frames.get_color_frame(), frames.get_depth_frame()
    if color is None or depth is None:
        return None
    w, h = color.get_width(), color.get_height()
    rgb = np.frombuffer(color.get_data(), dtype=np.uint8).reshape(h, w, 3)
    d = np.frombuffer(depth.get_data(), dtype=np.uint16).reshape(
        depth.get_height(), depth.get_width())
    depth_m = d.astype(np.float32) * (depth.get_depth_scale() / 1000.0)  # mm -> m
    return rgb, depth_m, stamp


class RosCamera:
    """Read color + registered depth from ROS 2 topics (e.g. an orbbec_camera node
    that already owns the device). read() matches get_frames()."""

    def __init__(self, ns, timeout=10.0):
        import threading
        # A camera driver in a Docker container on this host advertises shared memory,
        # but its /dev/shm is not ours, so Fast DDS would send data nowhere. Use UDP.
        os.environ.setdefault("FASTDDS_BUILTIN_TRANSPORTS", "UDPv4")
        import rclpy
        import message_filters
        from rclpy.qos import qos_profile_sensor_data
        from sensor_msgs.msg import CameraInfo, Image

        rclpy.init()
        self._rclpy = rclpy
        self.node = rclpy.create_node("hand3d")
        self._cond = threading.Condition()
        self._latest = None
        self.K = None
        self.node.create_subscription(CameraInfo, f"{ns}/color/camera_info",
                                      self._on_info, qos_profile_sensor_data)
        subs = [message_filters.Subscriber(self.node, Image, f"{ns}/{t}/image_raw",
                                           qos_profile=qos_profile_sensor_data)
                for t in ("color", "depth")]
        # Per-stream message counts and last stamps, to diagnose a startup timeout
        self._seen = {"color": 0, "depth": 0, "camera_info": 0}
        self._stamp = {}
        for t, s in zip(("color", "depth"), subs):
            s.registerCallback(lambda m, t=t: self._count(t, m))
        self._sync = message_filters.ApproximateTimeSynchronizer(subs, queue_size=10, slop=0.05)
        self._sync.registerCallback(self._on_pair)
        threading.Thread(target=rclpy.spin, args=(self.node,), daemon=True).start()

        t_end = time.monotonic() + timeout
        while self.K is None or self._latest is None:
            if time.monotonic() > t_end:
                seen = ", ".join(f"{k}={v}" for k, v in self._seen.items())
                hint = "check ROS_DOMAIN_ID and FASTRTPS_DEFAULT_PROFILES_FILE"
                if self._seen["color"] and self._seen["depth"]:
                    dt = abs(self._stamp["color"] - self._stamp["depth"])
                    hint = f"color/depth stamps never within 0.05 s (last gap {dt:.3f} s)"
                raise RuntimeError(f"no synced frames from {ns} within {timeout:.0f}s "
                                   f"(messages: {seen}); {hint}")
            time.sleep(0.05)

    def _count(self, topic, msg):
        self._seen[topic] += 1
        self._stamp[topic] = msg.header.stamp.sec + msg.header.stamp.nanosec * 1e-9

    def _on_info(self, msg):
        self._seen["camera_info"] += 1
        self.K = (msg.k[0], msg.k[4], msg.k[2], msg.k[5])

    @staticmethod
    def _to_array(msg, dtype, ch):
        a = np.frombuffer(msg.data, dtype=np.uint8).reshape(msg.height, msg.step)
        a = a[:, :msg.width * ch * np.dtype(dtype).itemsize]
        return a.view(dtype).reshape(msg.height, msg.width, ch) if ch > 1 else \
            a.view(dtype).reshape(msg.height, msg.width)

    def _on_pair(self, color, depth):
        rgb = self._to_array(color, np.uint8, 3)
        if color.encoding == "bgr8":
            rgb = rgb[:, :, ::-1]
        if depth.encoding == "32FC1":
            depth_m = self._to_array(depth, np.float32, 1).copy()
            depth_m[~np.isfinite(depth_m)] = 0
        else:  # 16UC1, mm
            depth_m = self._to_array(depth, np.uint16, 1).astype(np.float32) / 1000.0
        if depth_m.shape != rgb.shape[:2]:
            depth_m = cv2.resize(depth_m, (rgb.shape[1], rgb.shape[0]),
                                 interpolation=cv2.INTER_NEAREST)
        stamp = color.header.stamp.sec + color.header.stamp.nanosec * 1e-9
        with self._cond:
            self._latest = (rgb, depth_m, stamp)
            self._cond.notify()

    def read(self):
        """Return the newest (rgb, depth_m, stamp) not returned before, or None after 1 s."""
        with self._cond:
            if self._latest is None:
                self._cond.wait(1.0)
            got, self._latest = self._latest, None
        return got

    def stop(self):
        self.node.destroy_node()
        self._rclpy.try_shutdown()


# --------------------------------------------------------------------------- geometry
def sample_depth(depth_m, u, v, r=4):
    """Median of valid depth around pixel (u, v); None if no valid depth."""
    h, w = depth_m.shape
    u, v = int(round(u)), int(round(v))
    if not (0 <= u < w and 0 <= v < h):
        return None
    patch = depth_m[max(0, v - r):v + r + 1, max(0, u - r):u + r + 1]
    valid = patch[patch > 0]
    return float(np.median(valid)) if valid.size else None


def deproject(u, v, z, K):
    fx, fy, cx, cy = K
    return ((u - cx) * z / fx, (v - cy) * z / fy, z)


# --------------------------------------------------------------------------- logic
def nearest_person(poses, depth_m, K, w, h, max_angle_deg, min_vis=0.1):
    """Pick the pose whose torso is closest, among those within the front cone."""
    best, best_z = None, math.inf
    for i, lm in enumerate(poses):
        pts = [(lm[j].x * w, lm[j].y * h) for j in (L_SHOULDER, R_SHOULDER, L_HIP, R_HIP)
               if lm[j].visibility > min_vis]
        if not pts:
            continue
        zs = [z for z in (sample_depth(depth_m, u, v, r=6) for u, v in pts) if z]
        if not zs:
            continue
        z = float(np.median(zs))
        u = float(np.mean([p[0] for p in pts]))
        x = (u - K[2]) * z / K[0]
        if abs(math.degrees(math.atan2(x, z))) > max_angle_deg:
            continue
        if z < best_z:
            best, best_z = i, z
    return best, best_z


def hands_of_person(hand_result, pose_lm, w, h, max_dist_frac=0.12):
    """Return [(handedness_label, landmarks, side, world_landmarks)], one hand per side.
    side: 0 = person's left, 1 = right, from the hand model's own label: up close the
    pose's wrists are poor (the torso is out of view) and swapped the sides of one hand
    from frame to frame on the robot, while the label held (and agreed with the pose
    when it was good). With a pose, only hands whose wrist is near one of its wrists are
    kept; without one (no person found), every hand is, the larger winning a side."""
    out = {}
    pose_wrists = [] if pose_lm is None else \
        [(pose_lm[j].x * w, pose_lm[j].y * h) for j in (L_WRIST, R_WRIST)]
    for lm, handed, wlm in zip(hand_result.hand_landmarks, hand_result.handedness,
                               hand_result.hand_world_landmarks):
        hw = (lm[0].x * w, lm[0].y * h)
        if pose_wrists and min(math.dist(hw, pw) for pw in pose_wrists) > max_dist_frac * w:
            continue
        side = 1 if handed[0].category_name == "Right" else 0
        size = math.dist(hw, (lm[9].x * w, lm[9].y * h))
        if side not in out or size > out[side][0]:
            out[side] = (size, (handed[0].category_name, lm, side, wlm))
    return [v for _, v in out.values()]


FINGERS = [(5, 6, 8), (9, 10, 12), (13, 14, 16), (17, 18, 20)]  # MCP, PIP, TIP; thumb left out


def hand_shape(world_lm, side):
    """(palm normal, open) from the hand model's metric 3D landmarks, whose axes follow the
    camera's (x right, y down, z forward). The normal is a unit vector out of the palm (not
    the back of the hand); it uses the pose's side, since MediaPipe's handedness label
    assumes a mirrored image. open: at least 3 of the 4 fingers straight."""
    p = np.array([(q.x, q.y, q.z) for q in world_lm], dtype=float)
    n = np.cross(p[5] - p[0], p[17] - p[0])     # out of the palm for a right hand
    norm = np.linalg.norm(n)
    if norm < 1e-9:
        return None, False
    n = n / norm * (1.0 if side == 1 else -1.0)
    straight = sum(np.linalg.norm(p[tip] - p[0]) > np.linalg.norm(p[pip] - p[0]) + 0.6 * np.linalg.norm(p[tip] - p[pip])
                   for _, pip, tip in FINGERS)
    return tuple(float(c) for c in n), bool(straight >= 3)


class PalmPublisher:
    """Send each frame's palm targets as one JSON datagram over UDP, for an arm process.

    A palm is `followable` only when it comes from the hand landmarker with ToF depth
    from at least 3 landmarks (source `hand:...`, not `hand:PALM(size)` or one ending
    in `?`); anything else is still sent, so a
    receiver can hold rather than follow it. Palms can come without a person: up close
    the body is often out of view while the hand is not. `still` is
    true once a followable palm has stayed within still_tol of its mean for still_window
    seconds; `still_s` is how long it has been still.
    """

    def __init__(self, host, port, still_window=1.0, still_tol=0.02):
        import socket
        self._sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.addr = (host, port)
        self._window, self._tol = still_window, still_tol
        self._hist = {0: [], 1: []}
        self._still_since = {0: None, 1: None}
        self.seq = 0

    def _stillness(self, side, stamp, xyz):
        hist = self._hist[side]
        if xyz is None:
            hist.clear()
            self._still_since[side] = None
            return False, 0.0
        hist.append((stamp, np.asarray(xyz)))
        while hist and stamp - hist[0][0] > self._window:
            hist.pop(0)
        pts = np.array([x for _, x in hist])
        spans = stamp - hist[0][0] >= 0.9 * self._window
        still = bool(spans and np.linalg.norm(pts - pts.mean(axis=0), axis=1).max() < self._tol)
        if not still:
            self._still_since[side] = None
            return False, 0.0
        if self._still_since[side] is None:
            self._still_since[side] = stamp
        return True, float(stamp - self._still_since[side])

    def make(self, stamp, person_z, palms, shapes):
        """The message for one frame. palms: side -> (source, uv, xyz) or None;
        shapes: side -> (normal, open)."""
        msg = {"v": 1, "frame": "camera_color_optical_frame", "stamp": float(stamp), "seq": self.seq,
               "person": person_z is not None,
               "person_z": None if person_z is None else round(float(person_z), 3), "palms": {}}
        for side, name in ((0, "left"), (1, "right")):
            palm = palms.get(side)
            src, xyz = (palm[0], palm[2]) if palm else (None, None)
            followable = bool(src and src.startswith("hand:") and not src.endswith(("(size)", "?")))
            normal, is_open = shapes.get(side, (None, None))
            still, still_s = self._stillness(side, stamp, xyz if followable else None)
            msg["palms"][name] = {
                "followable": followable, "source": src,
                "xyz": [round(float(c), 4) for c in xyz] if xyz else None,
                "depth": None if not src else ("size" if src.endswith("(size)") else "tof"),
                "normal": [round(float(c), 3) for c in normal] if normal else None,
                "open": is_open, "still": still, "still_s": round(still_s, 2)}
        self.seq += 1
        return msg

    def send(self, msg):
        import json
        try:
            self._sock.sendto(json.dumps(msg).encode(), self.addr)
        except OSError:
            pass  # nobody listening yet; UDP may report it as ECONNREFUSED


PALM_SEGMENTS = [(0, 5), (0, 9), (0, 17), (5, 17), (5, 9), (9, 17)]


def hand_distance(hand_pts, world_lm, K):
    """Rough distance to the hand from its size: the hand model's metric length of each
    palm segment against its length in pixels (z = f * metres / pixels), the median.
    On the robot it read 0.3x to 0.8x the ToF distance (2026-10-25), and a child's hand
    reads farther than it is, so it only rules ToF clusters out; it never places a palm."""
    f = 0.5 * (K[0] + K[1])
    zs = []
    for a, b in PALM_SEGMENTS:
        metres = math.dist((world_lm[a].x, world_lm[a].y, world_lm[a].z),
                           (world_lm[b].x, world_lm[b].y, world_lm[b].z))
        pixels = math.dist(hand_pts[a][1], hand_pts[b][1])
        if pixels > 5:
            zs.append(f * metres / pixels)
    return float(np.median(zs)) if zs else None


def track_palm(hand_pts, pose_lm, side, depth_m, K, w, h, min_vis, occl_tol, arm_tol, world_lm=None):
    """Best 3D estimate of one hand's palm, falling back to the nearest trackable
    landmark when the palm is occluded. Returns (source, (u, v), xyz) or None.

    Order: palm centre from hand landmarks -> nearest unoccluded hand landmark ->
    pose hand points (wrist/index/pinky/thumb) -> pose elbow.
    Hand landmark depths are split into clusters wherever sorted depths jump by more
    than occl_tol. With world_lm (the hand model's metric landmarks), the hand is the
    largest cluster within the rough distance its size implies (hand_distance): an
    occluder sits in front of it, the forearm or body behind it when the hand reaches
    toward the camera. If no cluster fits (no ToF depth on the hand), the palm is placed
    at the size distance: source `hand:PALM(size)`, too rough to follow.
    Without world_lm, the hand is the farthest cluster within arm_tol of the elbow.
    Landmarks outside the chosen cluster are occluded, or their pixel missed the hand.
    """
    if pose_lm is not None:
        # Elbow depth bounds the hand: forearm is short, so a much nearer depth is an occluder.
        e = pose_lm[POSE_ELBOW[side]]
        elbow_z = sample_depth(depth_m, e.x * w, e.y * h) if e.visibility > min_vis else None
    else:
        elbow_z = None

    if hand_pts:
        valid = sorted((xyz[2], j) for j, (_, _, xyz) in enumerate(hand_pts) if xyz)
        clusters = []
        for z, j in valid:
            if clusters and z - clusters[-1][-1][0] <= occl_tol:
                clusters[-1].append((z, j))
            else:
                clusters.append([(z, j)])
        near_elbow = [c for c in clusters
                      if elbow_z is None or abs(np.median([z for z, _ in c]) - elbow_z) < arm_tol]
        z_est = hand_distance(hand_pts, world_lm, K) if world_lm is not None else None
        hand_c = None
        if z_est:
            # z_est reads low, so the window reaches far above it and only a little below.
            # Of the clusters it allows, the hand is the one with the most landmarks (an
            # occluder or the background behind catches a few), then the nearer.
            fits = [c for c in clusters if 0.8 * z_est <= np.median([z for z, _ in c]) <= 2.5 * z_est]
            # A cluster well behind one of 3+ landmarks is background seen between spread
            # fingers (on the robot: a wall at 1.7 m behind a hand at 0.6 m outnumbered it)
            big = [np.median([z for z, _ in c]) for c in fits if len(c) >= 3]
            if big:
                fits = [c for c in fits if np.median([z for z, _ in c]) <= min(big) + 0.3]
            if fits:
                hand_c = max(fits, key=lambda c: (len(c), -np.median([z for z, _ in c])))
            else:
                # No usable ToF depth on the hand (too close for the depth mode, or every
                # pixel missed it). The size distance is too rough to steer an arm by:
                # reported as `(size)`, never followable.
                palm_uv = np.mean([hand_pts[j][1] for j in PALM_POINTS], axis=0)
                return "hand:PALM(size)", tuple(palm_uv), deproject(palm_uv[0], palm_uv[1], z_est, K)
        if hand_c is None and near_elbow:
            hand_c = near_elbow[-1] if elbow_z is not None else max(clusters, key=len)
        if hand_c is not None:
            ok = [j for _, j in hand_c]
            palm_uv = np.mean([hand_pts[j][1] for j in PALM_POINTS], axis=0)
            palm_ok = [j for j in PALM_POINTS if j in ok]
            if len(palm_ok) >= 3:
                xyz = tuple(np.mean([hand_pts[j][2] for j in palm_ok], axis=0))
                return f"hand:PALM({len(palm_ok)}/5)", tuple(palm_uv), xyz
            if ok:
                # Fewer than 3 landmarks agreeing is as likely a stray pixel on the arm or
                # background as the hand: still reported, marked `?`, never followable.
                j = min(ok, key=lambda j: math.dist(hand_pts[j][1], palm_uv))
                mark = "" if len(ok) >= 3 else "?"
                return f"hand:{hand_pts[j][0]}{mark}", hand_pts[j][1], hand_pts[j][2]

    if pose_lm is None:
        return None
    pts = {}
    for name, j in POSE_HAND[side].items():
        p = pose_lm[j]
        if p.visibility <= min_vis:
            continue
        u, v = p.x * w, p.y * h
        z = sample_depth(depth_m, u, v)
        if z and (elbow_z is None or abs(z - elbow_z) < arm_tol):
            pts[name] = ((u, v), deproject(u, v, z, K))
    if pts:
        # Palm sits between wrist and the index/pinky knuckles.
        palm = [n for n in ("WRIST", "INDEX", "PINKY") if n in pts]
        if len(palm) >= 2:
            uv = tuple(np.mean([pts[n][0] for n in palm], axis=0))
            xyz = tuple(np.mean([pts[n][1] for n in palm], axis=0))
            return f"pose:PALM({'+'.join(palm)})", uv, xyz
        for n in ("INDEX", "PINKY", "WRIST", "THUMB"):
            if n in pts:
                return f"pose:{n}", pts[n][0], pts[n][1]
    if elbow_z:
        u, v = e.x * w, e.y * h
        return "pose:ELBOW", (u, v), deproject(u, v, elbow_z, K)
    return None


# --------------------------------------------------------------------------- viewer
class MjpegServer:
    """Serve the newest annotated frame as an MJPEG stream at http://host:port/
    (view in a browser; over SSH use a tunnel: ssh -L port:localhost:port robot)."""

    def __init__(self, port, host="127.0.0.1", quality=70):
        import threading
        from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

        self._cond = threading.Condition()
        self._jpg, self._seq, self._quality = None, 0, quality
        srv = self

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                self.send_response(200)
                self.send_header("Content-Type", "multipart/x-mixed-replace; boundary=frame")
                self.send_header("Cache-Control", "no-cache")
                self.end_headers()
                seq = -1
                try:
                    while True:
                        with srv._cond:
                            srv._cond.wait_for(lambda: srv._seq != seq, timeout=5)
                            jpg, seq = srv._jpg, srv._seq
                        if jpg is None:
                            continue
                        self.wfile.write(b"--frame\r\nContent-Type: image/jpeg\r\n"
                                         b"Content-Length: %d\r\n\r\n" % len(jpg))
                        self.wfile.write(jpg + b"\r\n")
                except (BrokenPipeError, ConnectionResetError):
                    pass

            def log_message(self, *a):
                pass

        self._httpd = ThreadingHTTPServer((host, port), Handler)
        self._httpd.daemon_threads = True
        threading.Thread(target=self._httpd.serve_forever, daemon=True).start()

    def publish(self, bgr):
        ok, buf = cv2.imencode(".jpg", bgr, [cv2.IMWRITE_JPEG_QUALITY, self._quality])
        if ok:
            with self._cond:
                self._jpg, self._seq = buf.tobytes(), self._seq + 1
                self._cond.notify_all()

    def stop(self):
        self._httpd.shutdown()


# --------------------------------------------------------------------------- main
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--show", action="store_true", help="show an OpenCV window")
    ap.add_argument("--all", action="store_true", help="print all 21 landmarks")
    ap.add_argument("--width", type=int, default=1280)
    ap.add_argument("--height", type=int, default=720)
    ap.add_argument("--source", choices=["sdk", "ros"], default="sdk",
                    help="sdk: open the camera directly; ros: subscribe to a running "
                         "orbbec_camera node (use when another process owns the camera)")
    ap.add_argument("--ros-ns", default="/camera", help="orbbec_camera topic namespace")
    ap.add_argument("--max-angle", type=float, default=35.0,
                    help="half-angle (deg) of the 'in front' cone")
    ap.add_argument("--print-hz", type=float, default=5.0)
    ap.add_argument("--min-vis", type=float, default=0.1,
                    help="min pose visibility for a torso keypoint to count")
    ap.add_argument("--occl-tol", type=float, default=0.05,
                    help="m; hand landmark nearer than hand median depth by this is occluded")
    ap.add_argument("--arm-tol", type=float, default=0.45,
                    help="m; pose hand point farther than this from elbow depth is rejected")
    ap.add_argument("--save-dir",help="save annotated frames to this directory")
    ap.add_argument("--save-every", type=float, default=2.0,
                    help="seconds between saved frames")
    ap.add_argument("--stream", type=int, metavar="PORT",
                    help="serve the annotated video as MJPEG on http://localhost:PORT/")
    ap.add_argument("--stream-host", default="127.0.0.1",
                    help="bind address for --stream (0.0.0.0 exposes it on the network)")
    ap.add_argument("--publish", metavar="HOST:PORT",
                    help="send palm targets as JSON over UDP each frame, e.g. 127.0.0.1:47800")
    ap.add_argument("--still-window", type=float, default=1.0,
                    help="s; a palm is still once it stays within --still-tol for this long")
    ap.add_argument("--still-tol", type=float, default=0.02,
                    help="m; how far a still palm may wander from its mean")
    args = ap.parse_args()
    publisher = None
    if args.publish:
        host, _, port = args.publish.rpartition(":")
        publisher = PalmPublisher(host or "127.0.0.1", int(port), args.still_window, args.still_tol)
    if args.show and os.name == "posix" and not (os.environ.get("DISPLAY")
                                                 or os.environ.get("WAYLAND_DISPLAY")
                                                 or os.uname().sysname == "Darwin"):
        print("No display (headless/SSH without -X): ignoring --show; use --stream PORT to view live.")
        args.show = False

    BaseOptions = mp.tasks.BaseOptions
    vision = mp.tasks.vision
    pose_det = vision.PoseLandmarker.create_from_options(vision.PoseLandmarkerOptions(
        base_options=BaseOptions(model_asset_path=POSE_MODEL),
        running_mode=vision.RunningMode.VIDEO, num_poses=4))
    hand_det = vision.HandLandmarker.create_from_options(vision.HandLandmarkerOptions(
        base_options=BaseOptions(model_asset_path=HAND_MODEL),
        running_mode=vision.RunningMode.VIDEO, num_hands=6))

    # Bind the viewer port before opening the camera, so a busy port fails cleanly
    stream = None
    if args.stream:
        try:
            stream = MjpegServer(args.stream, args.stream_host)
        except OSError as e:
            raise SystemExit(f"--stream {args.stream}: {e.strerror}; pick another port")
    if args.source == "ros":
        cam = RosCamera(args.ros_ns)
        K, read_frames, stop_camera = cam.K, cam.read, cam.stop
    else:
        pipeline, align, K = start_camera(args.width, args.height)
        read_frames, stop_camera = (lambda: get_frames(pipeline, align)), pipeline.stop
    print(f"Camera started. Color intrinsics fx={K[0]:.1f} fy={K[1]:.1f} cx={K[2]:.1f} cy={K[3]:.1f}")
    print("Coordinates: camera frame, meters (X right, Y down, Z forward). Ctrl+C to quit.")
    if stream:
        print(f"Live view: http://{args.stream_host}:{args.stream}/")
    if publisher:
        print(f"Publishing palm targets to udp://{publisher.addr[0]}:{publisher.addr[1]}")

    if args.save_dir:
        os.makedirs(args.save_dir, exist_ok=True)
    t0, last_print, last_save = time.monotonic(), 0.0, 0.0
    n_frames = n_found = 0
    try:
        while True:
            got = read_frames()
            if got is None:
                continue
            rgb, depth_m, stamp = got
            h, w = rgb.shape[:2]
            ts = int((time.monotonic() - t0) * 1000)
            mp_img = mp.Image(image_format=mp.ImageFormat.SRGB, data=np.ascontiguousarray(rgb))
            pose_res = pose_det.detect_for_video(mp_img, ts)
            hand_res = hand_det.detect_for_video(mp_img, ts)

            idx, person_z = nearest_person(pose_res.pose_landmarks, depth_m, K, w, h, args.max_angle,
                                           args.min_vis)
            n_frames += 1
            n_found += idx is not None
            pose_lm = pose_res.pose_landmarks[idx] if idx is not None else None
            hands = hands_of_person(hand_res, pose_lm, w, h)

            results = []  # (label, [(name, (u,v), xyz or None)])
            hand_by_side, shapes, world_by_side = {}, {}, {}
            for label, lm, side, wlm in hands:
                shapes[side] = hand_shape(wlm, side)
                pts = []
                for j, p in enumerate(lm):
                    u, v = p.x * w, p.y * h
                    z = sample_depth(depth_m, u, v)
                    pts.append((HAND_NAMES[j], (u, v), deproject(u, v, z, K) if z else None))
                results.append((label, pts))
                hand_by_side[side] = pts
                world_by_side[side] = wlm

            palms = {}  # side -> (source, (u,v), xyz) or None
            for side in ((0, 1) if idx is not None else sorted(hand_by_side)):
                palms[side] = track_palm(hand_by_side.get(side), pose_lm,
                                         side, depth_m, K, w, h, args.min_vis,
                                         args.occl_tol, args.arm_tol, world_by_side.get(side))
            msg = None
            if publisher:
                msg = publisher.make(stamp, person_z if idx is not None else None, palms, shapes)
                publisher.send(msg)

            now = time.monotonic()
            if now - last_print >= 1.0 / args.print_hz:
                last_print = now
                if idx is None and not results:
                    print("No person in front.")
                else:
                    who = "no body found" if idx is None else f"nearest person @ {person_z:.2f} m"
                    print(f"--- {who}, {len(results)} hand(s)")
                    for label, pts in results:
                        for j in (range(21) if args.all else KEY_POINTS):
                            name, _, xyz = pts[j]
                            s = "no depth" if xyz is None else \
                                f"x={xyz[0]:+.3f} y={xyz[1]:+.3f} z={xyz[2]:.3f}"
                            print(f"  {label:5s} {name:11s} {s}")
                    for side, palm in palms.items():
                        arm = ("L-arm", "R-arm")[side]
                        if palm is None:
                            print(f"  {arm} PALM        lost")
                        else:
                            src, _, xyz = palm
                            extra = ""
                            if side in shapes and shapes[side][0]:
                                n, is_open = shapes[side]
                                extra = (f"  normal=({n[0]:+.2f},{n[1]:+.2f},{n[2]:+.2f})"
                                         f" {'open' if is_open else 'closed'}")
                            if msg:
                                p = msg["palms"][("left", "right")[side]]
                                extra += f" still {p['still_s']:.1f}s" if p["still"] else ""
                            print(f"  {arm} PALM        x={xyz[0]:+.3f} y={xyz[1]:+.3f} "
                                  f"z={xyz[2]:.3f}  [{src}]{extra}")

            save_now = args.save_dir and now - last_save >= args.save_every
            if args.show or save_now or stream:
                vis = cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR)
                if idx is not None:
                    for p in pose_res.pose_landmarks[idx]:
                        cv2.circle(vis, (int(p.x * w), int(p.y * h)), 3, (255, 128, 0), -1)
                for label, pts in results:
                    for a, b in HAND_CONNECTIONS:
                        pa, pb = pts[a][1], pts[b][1]
                        cv2.line(vis, (int(pa[0]), int(pa[1])), (int(pb[0]), int(pb[1])), (0, 255, 0), 2)
                    wu, wv = pts[0][1]
                    xyz = pts[0][2]
                    txt = f"{label} wrist " + ("?" if xyz is None else
                                               f"({xyz[0]:+.2f},{xyz[1]:+.2f},{xyz[2]:.2f})m")
                    cv2.putText(vis, txt, (int(wu), int(wv) + 25), cv2.FONT_HERSHEY_SIMPLEX,
                                0.6, (0, 255, 255), 2)
                for side, palm in palms.items():
                    if palm is None:
                        continue
                    src, (pu, pv), xyz = palm
                    # magenta = from hand landmarker, red = pose fallback
                    col = (255, 0, 255) if src.startswith("hand:") else (0, 0, 255)
                    cv2.circle(vis, (int(pu), int(pv)), 9, col, 3)
                    cv2.putText(vis, f"{src} ({xyz[0]:+.2f},{xyz[1]:+.2f},{xyz[2]:.2f})m",
                                (int(pu) + 12, int(pv) - 12), cv2.FONT_HERSHEY_SIMPLEX, 0.5, col, 2)
                status = "no person" if idx is None else f"person @ {person_z:.2f} m"
                cv2.putText(vis, status, (10, 30), cv2.FONT_HERSHEY_SIMPLEX,
                            0.8, (255, 255, 255), 2)
                if save_now:
                    last_save = now
                    cv2.imwrite(os.path.join(args.save_dir, f"frame_{ts:07d}.jpg"), vis)
                if stream:
                    stream.publish(vis)
                if args.show:
                    cv2.imshow("hand3d", vis)
                    if cv2.waitKey(1) & 0xFF in (27, ord("q")):
                        break
    except KeyboardInterrupt:
        pass
    finally:
        if n_frames:
            print(f"Person found in {n_found}/{n_frames} frames ({100 * n_found / n_frames:.0f}%)")
        stop_camera()
        if stream:
            stream.stop()
        cv2.destroyAllWindows()


if __name__ == "__main__":
    main()
