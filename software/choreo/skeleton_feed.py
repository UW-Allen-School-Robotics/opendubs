"""The live skeleton feed's ADAPTER: the ONE file that knows the depth camera's skeleton message (notes/
design/choreo-stage.md). The Thor lane's relay (software/ros, #451) subscribes to MSG_TYPE on its topic,
calls `adapt(msg, received_monotonic)` first thing in its callback, and sends the result as JSON to the
dashboard (/ws/skeleton/in), which hands it to the stage page. A different message type, topic or joint
set is a change to THIS file only.

PRIVACY — a fixed rule: only skeleton points (joint positions, confidences, track ids) are used. No camera
image is shown, streamed, recorded or stored by any OpenDubs part. MSG_TYPE must never be an image or
point-cloud type (the relay refuses those), and nothing here keeps a frame.

The canonical frame (v1):
    {"v": 1, "t": <capture time, the Thor's CLOCK_MONOTONIC seconds>, "frame": "skeleton_camera",
     "status": "live", "people": [{"id": 7, "joints": {"head": [x, y, z, c], ...}}]}
- metres in the camera's frame as normalised here: x to the CAMERA's right, y up, z away from the camera
  (the stage page mirrors it, so a person sees themselves as in a mirror);
- the joint set below (CANONICAL); a joint the feed lacks is left out; c is confidence 0..1;
- `people` is [] when nobody is there; the relay sends {"status": "stale", "people": []} itself after 1 s
  of silence.
Standard library only (the relay runs on the host's ROS Python).
"""
from __future__ import annotations

import math
import time

MSG_TYPE = None          # the skeleton message type, e.g. "pkg/msg/Skeletons" — None: not fixed yet (the relay idles)
DEFAULT_TOPIC = None     # the relay's topic default (its unit can set one)
CANONICAL = ("head", "neck", "l_shoulder", "r_shoulder", "l_elbow", "r_elbow", "l_wrist", "r_wrist",
             "pelvis", "l_hip", "r_hip", "l_knee", "r_knee", "l_ankle", "r_ankle")
# the feed's joint names -> ours. Filled in when the joint set is known; a COCO-17-like and an Azure
# Kinect body tracking set are the likely ones (both map here; unmapped joints are dropped).
JOINT_MAP = {
    "nose": "head", "head": "head", "neck": "neck", "spine_chest": "neck",
    "left_shoulder": "l_shoulder", "shoulder_left": "l_shoulder", "right_shoulder": "r_shoulder", "shoulder_right": "r_shoulder",
    "left_elbow": "l_elbow", "elbow_left": "l_elbow", "right_elbow": "r_elbow", "elbow_right": "r_elbow",
    "left_wrist": "l_wrist", "wrist_left": "l_wrist", "right_wrist": "r_wrist", "wrist_right": "r_wrist",
    "pelvis": "pelvis", "left_hip": "l_hip", "hip_left": "l_hip", "right_hip": "r_hip", "hip_right": "r_hip",
    "left_knee": "l_knee", "knee_left": "l_knee", "right_knee": "r_knee", "knee_right": "r_knee",
    "left_ankle": "l_ankle", "ankle_left": "l_ankle", "right_ankle": "r_ankle", "ankle_right": "r_ankle",
}
_ros_offset = None       # ROS time - monotonic, learnt at receipt


def frame(t: float, people: list, status: str = "live") -> dict:
    return {"v": 1, "t": round(float(t), 4), "frame": "skeleton_camera", "status": status, "people": people}


def canonical(joints: dict) -> dict:
    """Feed joint names -> CANONICAL, rounded; a neck or pelvis the feed lacks is made from the
    shoulders or the hips. Joints with no position are dropped."""
    out = {}
    for name, v in joints.items():
        k = JOINT_MAP.get(str(name).lower(), str(name).lower())
        if k in CANONICAL and v is not None and len(v) >= 3:
            out[k] = [round(float(v[0]), 4), round(float(v[1]), 4), round(float(v[2]), 4),
                      round(float(v[3]) if len(v) > 3 else 1.0, 3)]
    for mid, (a, b) in (("neck", ("l_shoulder", "r_shoulder")), ("pelvis", ("l_hip", "r_hip"))):
        if mid not in out and a in out and b in out:
            out[mid] = [round((out[a][i] + out[b][i]) / 2, 4) for i in range(3)] + [min(out[a][3], out[b][3])]
    return out


def adapt(msg, received_monotonic: float):
    """The relay's call. Returns a canonical frame, or None to skip the message. Written for the general
    shape of a skeleton message — people with an id and named joints — until the real type is fixed;
    the field names are the part to adjust."""
    global _ros_offset
    people = []
    for p in getattr(msg, "people", None) or getattr(msg, "skeletons", None) or []:
        pid = getattr(p, "id", None)
        if pid is None:
            pid = getattr(p, "track_id", 0)
        joints = {}
        for j in getattr(p, "joints", []) or []:
            pos = getattr(j, "position", j)
            joints[getattr(j, "name", "")] = [getattr(pos, "x", 0.0), getattr(pos, "y", 0.0), getattr(pos, "z", 0.0),
                                              getattr(j, "confidence", 1.0)]
        people.append({"id": int(pid), "joints": canonical(joints)})
    stamp = getattr(getattr(msg, "header", None), "stamp", None)
    t = received_monotonic
    if stamp is not None:
        ros = getattr(stamp, "sec", 0) + getattr(stamp, "nanosec", 0) * 1e-9
        if _ros_offset is None or ros - received_monotonic > _ros_offset:   # the smallest delay seen
            _ros_offset = ros - received_monotonic
        t = ros - _ros_offset
    return frame(t, people)


def synthetic(t: float, t0: float = 0.0) -> dict:
    """A made-up person, for tests and the twin's preview (no camera, no person): steps in at 2 s,
    waves and sways, steps out at 14 s; then nobody. Camera frame, ~2 m away."""
    s = t - t0
    if not 2.0 <= s % 16.0 < 14.0:
        return frame(t, [])
    x0, wave = 0.15 * math.sin(s * 0.8), math.sin(s * 6.0)
    J = {"head": [x0, 0.75, 2.0], "neck": [x0, 0.55, 2.0], "l_shoulder": [x0 - 0.19, 0.52, 2.0],
         "r_shoulder": [x0 + 0.19, 0.52, 2.0], "l_elbow": [x0 - 0.38, 0.68, 2.0], "r_elbow": [x0 + 0.24, 0.25, 2.0],
         "l_wrist": [x0 - 0.42 + 0.12 * wave, 0.95, 2.0], "r_wrist": [x0 + 0.26, 0.0, 2.0], "pelvis": [x0, 0.0, 2.0],
         "l_hip": [x0 - 0.10, -0.03, 2.0], "r_hip": [x0 + 0.10, -0.03, 2.0], "l_knee": [x0 - 0.11, -0.47, 2.0],
         "r_knee": [x0 + 0.11, -0.47, 2.0], "l_ankle": [x0 - 0.12, -0.90, 2.0], "r_ankle": [x0 + 0.12, -0.90, 2.0]}
    return frame(t, [{"id": 1, "joints": canonical({k: v + [0.95] for k, v in J.items()})}])


if __name__ == "__main__":
    # a synthetic feed for the stage page, without the relay or a camera:
    #   python3 skeleton_feed.py ws://127.0.0.1:8080/ws/skeleton/in
    import json
    import sys
    try:
        from websockets.sync.client import connect
    except ImportError:
        sys.exit("needs the websockets package (python3-websockets)")
    url = sys.argv[1] if len(sys.argv) > 1 else "ws://127.0.0.1:8080/ws/skeleton/in"
    t0 = time.monotonic()
    with connect(url) as ws:
        while True:
            ws.send(json.dumps(synthetic(time.monotonic(), t0)))
            time.sleep(1 / 30)
