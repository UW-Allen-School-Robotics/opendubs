"""Where a person's palms are, in the colour camera's frame: MediaPipe's hand and pose landmarkers on one RGB-D frame.

The tracking of the halloween-mediapipe prototype (hand3d.py), without its camera, viewer, printing or UDP: a frame in,
palms out. Coordinates are camera_color_optical_frame, metres: x right, y down, z forward.

  1. The pose landmarker finds people; the nearest torso inside a cone in front of the camera is THE person.
  2. The hand landmarker's hands are kept when their wrist is near one of that person's pose wrists (with no person
     found, every hand is: up close the body is often out of view while the hand is not).
  3. Each hand's landmarks get ToF depth; the palm is the mean of the palm landmarks (wrist + finger MCPs) in the depth
     cluster that is the hand, and an occluded palm falls back to the nearest trackable landmark, then to the pose.

FOLLOWABLE, by policy (followable()):
  hand (default)  any point ON THE HAND with ToF depth: the palm, a single hand landmark (`hand:<NAME>`, `?` when fewer
                  than 3 agree), the palm with a wider depth sample (`hand:PALM(wide)`), or the pose model's hand points
                  (`pose:PALM(...)`, `pose:WRIST`, ...). Whatever part of the hand the camera sees, at any height of person.
  palm            the prototype's: only the hand landmarker with ToF depth on at least 3 landmarks.
Never followable: the distance from the hand's size alone (`hand:PALM(size)`: 0.3-0.8x the true distance) and the
elbow (`pose:ELBOW`, not the hand). Those are reported, so a follower can hold, never steered by.

Sides are the PERSON's own hands. MediaPipe's documentation says its handedness assumes a mirrored image, but on this
camera it does not: measured on opendubs2 (2026-10-27), the hand labelled "Left" was on the image's right, the robot's
right, which is the person's left hand when they face the robot. `right` is the person's right hand.
"""
from __future__ import annotations

import math
import os

import numpy as np

SIDES = ("left", "right")    # index 0, 1: the person's hand (MediaPipe's label, see above)
POLICIES = ("hand", "palm")

L_SHOULDER, R_SHOULDER, L_HIP, R_HIP = 11, 12, 23, 24
L_WRIST, R_WRIST = 15, 16
# Per side, the pose's points on the hand, and the elbow
POSE_HAND = [{"WRIST": 15, "PINKY": 17, "INDEX": 19, "THUMB": 21},
             {"WRIST": 16, "PINKY": 18, "INDEX": 20, "THUMB": 22}]
POSE_ELBOW = [13, 14]
HAND_NAMES = [
    "WRIST", "THUMB_CMC", "THUMB_MCP", "THUMB_IP", "THUMB_TIP",
    "INDEX_MCP", "INDEX_PIP", "INDEX_DIP", "INDEX_TIP",
    "MIDDLE_MCP", "MIDDLE_PIP", "MIDDLE_DIP", "MIDDLE_TIP",
    "RING_MCP", "RING_PIP", "RING_DIP", "RING_TIP",
    "PINKY_MCP", "PINKY_PIP", "PINKY_DIP", "PINKY_TIP",
]
PALM_POINTS = [0, 5, 9, 13, 17]          # wrist + finger MCPs outline the palm
PALM_SEGMENTS = [(0, 5), (0, 9), (0, 17), (5, 17), (5, 9), (9, 17)]


def followable(source: str | None, policy: str = "hand") -> bool:
    if not source or source.endswith("(size)") or source == "pose:ELBOW":
        return False
    if policy == "palm":
        return source.startswith("hand:") and not source.endswith(("?", "(wide)"))
    return source.startswith(("hand:", "pose:"))


# -- geometry -----------------------------------------------------------------------------------------------------

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


def nearest_person(poses, depth_m, K, w, h, max_angle_deg, min_vis=0.1):
    """The pose whose torso is closest, among those within the front cone: (index, distance) or (None, inf)."""
    best, best_z = None, math.inf
    for i, lm in enumerate(poses):
        pts = [(lm[j].x * w, lm[j].y * h) for j in (L_SHOULDER, R_SHOULDER, L_HIP, R_HIP) if lm[j].visibility > min_vis]
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
    """[(landmarks, side, world_landmarks)], one hand per side. The side is the hand model's own label: up close the
    pose's wrists are poor (the torso is out of view) and swapped the sides of one hand from frame to frame on the
    robot, while the label held. With a pose, only hands whose wrist is near one of its wrists are kept; without one,
    every hand is, the larger winning a side."""
    out = {}
    pose_wrists = [] if pose_lm is None else [(pose_lm[j].x * w, pose_lm[j].y * h) for j in (L_WRIST, R_WRIST)]
    for lm, handed, wlm in zip(hand_result.hand_landmarks, hand_result.handedness, hand_result.hand_world_landmarks):
        hw = (lm[0].x * w, lm[0].y * h)
        if pose_wrists and min(math.dist(hw, pw) for pw in pose_wrists) > max_dist_frac * w:
            continue
        side = 1 if handed[0].category_name == "Right" else 0
        size = math.dist(hw, (lm[9].x * w, lm[9].y * h))
        if side not in out or size > out[side][0]:
            out[side] = (size, (lm, side, wlm))
    return [v for _, v in out.values()]


def hand_distance(hand_pts, world_lm, K):
    """Rough distance to the hand from its size: the hand model's metric length of each palm segment against its length
    in pixels (z = f * metres / pixels), the median. On the robot it read 0.3x to 0.8x the ToF distance (2026-10-25),
    and a child's hand reads farther than it is, so it only rules ToF clusters out; it never places a palm."""
    f = 0.5 * (K[0] + K[1])
    zs = []
    for a, b in PALM_SEGMENTS:
        metres = math.dist((world_lm[a].x, world_lm[a].y, world_lm[a].z), (world_lm[b].x, world_lm[b].y, world_lm[b].z))
        pixels = math.dist(hand_pts[a][1], hand_pts[b][1])
        if pixels > 5:
            zs.append(f * metres / pixels)
    return float(np.median(zs)) if zs else None


def track_palm(hand_pts, pose_lm, side, depth_m, K, w, h, min_vis, occl_tol, arm_tol, world_lm=None):
    """Best 3D estimate of one hand's palm, falling back to the nearest trackable landmark when the palm is occluded.
    Returns (source, (u, v), xyz) or None.

    Order: palm centre from hand landmarks -> nearest unoccluded hand landmark -> the palm with a wider depth patch ->
    pose hand points -> the size distance (reported, never followed) -> pose elbow (likewise).
    Hand landmark depths are split into clusters wherever sorted depths jump by more than occl_tol. With world_lm, the
    hand is the largest cluster within the rough distance its size implies (hand_distance): an occluder sits in front of
    it, the forearm or body behind it when the hand reaches toward the camera. Without world_lm, the hand is the
    farthest cluster within arm_tol of the elbow.
    """
    elbow_z, e, size_only = None, None, None
    if pose_lm is not None:
        # Elbow depth bounds the hand: the forearm is short, so a much nearer depth is an occluder.
        e = pose_lm[POSE_ELBOW[side]]
        elbow_z = sample_depth(depth_m, e.x * w, e.y * h) if e.visibility > min_vis else None

    if hand_pts:
        valid = sorted((xyz[2], j) for j, (_, _, xyz) in enumerate(hand_pts) if xyz)
        clusters = []
        for z, j in valid:
            if clusters and z - clusters[-1][-1][0] <= occl_tol:
                clusters[-1].append((z, j))
            else:
                clusters.append([(z, j)])
        near_elbow = [c for c in clusters if elbow_z is None or abs(np.median([z for z, _ in c]) - elbow_z) < arm_tol]
        z_est = hand_distance(hand_pts, world_lm, K) if world_lm is not None else None
        hand_c = None
        if z_est:
            # z_est reads low, so the window reaches far above it and only a little below. Of the clusters it allows,
            # the hand is the one with the most landmarks (an occluder or the background catches a few), then the nearer.
            fits = [c for c in clusters if 0.8 * z_est <= np.median([z for z, _ in c]) <= 2.5 * z_est]
            # A cluster well behind one of 3+ landmarks is background seen between spread fingers (on the robot: a wall
            # at 1.7 m behind a hand at 0.6 m outnumbered it)
            big = [np.median([z for z, _ in c]) for c in fits if len(c) >= 3]
            if big:
                fits = [c for c in fits if np.median([z for z, _ in c]) <= min(big) + 0.3]
            if fits:
                hand_c = max(fits, key=lambda c: (len(c), -np.median([z for z, _ in c])))
            else:
                # No landmark's pixel has depth inside the window (on the robot: a hand at about 0.45 m, reaching toward
                # the camera). A wider patch around the palm often still hits the hand: `(wide)`. Failing that, the pose's
                # hand points below; the size distance only as the last report, never followed.
                palm_uv = np.mean([hand_pts[j][1] for j in PALM_POINTS], axis=0)
                z = sample_depth(depth_m, palm_uv[0], palm_uv[1], r=15)
                if z and 0.8 * z_est <= z <= 2.5 * z_est:
                    return "hand:PALM(wide)", tuple(palm_uv), deproject(palm_uv[0], palm_uv[1], z, K)
                size_only = ("hand:PALM(size)", tuple(palm_uv), deproject(palm_uv[0], palm_uv[1], z_est, K))
                clusters = []          # none of them is the hand: skip to the pose
        if hand_c is None and near_elbow and clusters:
            hand_c = near_elbow[-1] if elbow_z is not None else max(clusters, key=len)
        if hand_c is not None:
            ok = [j for _, j in hand_c]
            palm_uv = np.mean([hand_pts[j][1] for j in PALM_POINTS], axis=0)
            palm_ok = [j for j in PALM_POINTS if j in ok]
            if len(palm_ok) >= 3:
                xyz = tuple(np.mean([hand_pts[j][2] for j in palm_ok], axis=0))
                return f"hand:PALM({len(palm_ok)}/5)", tuple(palm_uv), xyz
            if ok:
                # Fewer than 3 landmarks agreeing can be a stray pixel on the arm or background as well as the hand:
                # marked `?`, followed only under the `hand` policy (the follower's low-pass and speed limit absorb one).
                j = min(ok, key=lambda j: math.dist(hand_pts[j][1], palm_uv))
                mark = "" if len(ok) >= 3 else "?"
                return f"hand:{hand_pts[j][0]}{mark}", hand_pts[j][1], hand_pts[j][2]

    if pose_lm is None:
        return size_only
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
        palm = [n for n in ("WRIST", "INDEX", "PINKY") if n in pts]     # the palm sits between wrist and knuckles
        if len(palm) >= 2:
            uv = tuple(np.mean([pts[n][0] for n in palm], axis=0))
            xyz = tuple(np.mean([pts[n][1] for n in palm], axis=0))
            return f"pose:PALM({'+'.join(palm)})", uv, xyz
        for n in ("INDEX", "PINKY", "WRIST", "THUMB"):
            if n in pts:
                return f"pose:{n}", pts[n][0], pts[n][1]
    if size_only:
        return size_only
    if elbow_z:
        u, v = e.x * w, e.y * h
        return "pose:ELBOW", (u, v), deproject(u, v, elbow_z, K)
    return None


# -- the landmarkers ----------------------------------------------------------------------------------------------

class PalmTracker:
    """MediaPipe's pose and hand landmarkers in VIDEO mode, and the logic above. `track` takes one frame."""

    def __init__(self, models_dir, max_angle_deg=35.0, min_vis=0.1, occl_tol=0.05, arm_tol=0.45, delegate="gpu"):
        import mediapipe as mp
        self.mp = mp
        vision = mp.tasks.vision
        dlg = mp.tasks.BaseOptions.Delegate.GPU if delegate == "gpu" else mp.tasks.BaseOptions.Delegate.CPU

        def base(model_asset_path):
            return mp.tasks.BaseOptions(model_asset_path=model_asset_path, delegate=dlg)
        for name in ("pose_landmarker_full.task", "hand_landmarker.task"):
            if not os.path.isfile(os.path.join(models_dir, name)):
                raise FileNotFoundError(f"no {name} in {models_dir} (tools/hand_stack.sh models <dir> puts them there)")
        self.pose = vision.PoseLandmarker.create_from_options(vision.PoseLandmarkerOptions(
            base_options=base(model_asset_path=os.path.join(models_dir, "pose_landmarker_full.task")),
            running_mode=vision.RunningMode.VIDEO, num_poses=4))
        self.hands = vision.HandLandmarker.create_from_options(vision.HandLandmarkerOptions(
            base_options=base(model_asset_path=os.path.join(models_dir, "hand_landmarker.task")),
            running_mode=vision.RunningMode.VIDEO, num_hands=6))
        self.max_angle, self.min_vis, self.occl_tol, self.arm_tol = max_angle_deg, min_vis, occl_tol, arm_tol
        self._last_ms = -1

    def track(self, rgb, depth_m, K, stamp_s):
        """(person distance or None, {side index: (source, (u, v), xyz) or None}) for one frame. K = (fx, fy, cx, cy);
        depth in metres, aligned to colour (resized to it if the driver's differs)."""
        h, w = rgb.shape[:2]
        if depth_m.shape != (h, w):
            import cv2
            depth_m = cv2.resize(depth_m, (w, h), interpolation=cv2.INTER_NEAREST)
        ms = max(int(stamp_s * 1000), self._last_ms + 1)      # VIDEO mode needs increasing timestamps
        self._last_ms = ms
        img = self.mp.Image(image_format=self.mp.ImageFormat.SRGB, data=np.ascontiguousarray(rgb))
        pose_res = self.pose.detect_for_video(img, ms)
        hand_res = self.hands.detect_for_video(img, ms)

        idx, person_z = nearest_person(pose_res.pose_landmarks, depth_m, K, w, h, self.max_angle, self.min_vis)
        pose_lm = pose_res.pose_landmarks[idx] if idx is not None else None
        by_side, world = {}, {}
        for lm, side, wlm in hands_of_person(hand_res, pose_lm, w, h):
            pts = []
            for j, p in enumerate(lm):
                u, v = p.x * w, p.y * h
                z = sample_depth(depth_m, u, v)
                pts.append((HAND_NAMES[j], (u, v), deproject(u, v, z, K) if z else None))
            by_side[side], world[side] = pts, wlm
        palms = {}
        for side in ((0, 1) if idx is not None else sorted(by_side)):
            palms[side] = track_palm(by_side.get(side), pose_lm, side, depth_m, K, w, h, self.min_vis,
                                     self.occl_tol, self.arm_tol, world.get(side))
        return (person_z if idx is not None else None), palms
