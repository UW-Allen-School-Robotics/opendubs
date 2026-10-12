#!/usr/bin/env python3
"""Reference images: propose arm cues from a photo or video of a person (plan item 1, D1's input
tools — "reference images/video, teach-in and Quest recordings").

    python3 pose_ref.py propose photo.jpg --at bar:9.1                      # print the cues
    python3 pose_ref.py propose clip.mp4 --at 1.2=bar:9.1 --at 2.0=bar:9.3  # a frame per beat
    python3 pose_ref.py propose clip.mp4 --at 1.2=t:4.0 --into shows/my-show.yaml   # add + solve

WHAT IT DOES. A body-pose model (MediaPipe Pose, which runs on a laptop CPU) finds the person's
shoulders, elbows, wrists and hands in 3-D. Each hand is taken RELATIVE TO ITS OWN SHOULDER, in the
person's own body frame (forward, left, up — built from their shoulders and hips), so the camera's
angle and the person's facing do not matter: arm straight out to the side is "out to the side"
whether the photo was taken from the front or at 45°. That direction, and how far along it the
hand is as a fraction of the person's full arm, become a `reach:` target for the robot's arm: the
same direction from the robot's shoulder, the same fraction of the robot's reach.

WHAT IT DOES NOT DO. It proposes; nothing here moves an arm. The cues are ordinary IK targets, so
ik.py solves them against the twin and the validator checks them like any hand-written cue —
limits, speed caps, the head clearance, the robot's own body. A pose the robot cannot copy safely
is REFUSED there, with the reason, never bent into something nearby. The elbow is copied as a
secondary goal (see ARM SHAPE): the hand always wins, and an elbow the arm cannot match is reported
with how far off it is.

ARMS. `--arms left|right|both` (default both). Anatomical by default: the person's LEFT arm drives
the robot's LEFT arm (ARM_L). `--mirror` swaps them, for a performer facing the robot as if in a
mirror. `--point` proposes `point_at:` (aim the hand along the person's arm) instead of `reach:`.

ARM SHAPE (default; `--no-elbows` for the hand only). Each cue also carries `elbow:`: the person's
upper arm and forearm directions laid out from the robot's shoulder at the robot's own segment
lengths, so a bent overhead arm stays bent overhead. The solver (ik.py) keeps the hand on its target
and brings the elbow as near as the arm's spare freedom allows; how far it ends up is written as
`elbow_off`, and the validator warns above 5 cm — a shape the arm cannot take is said, not hidden.

UP (default `--frame gravity`): measured from gravity — a level camera's vertical — with only the
facing taken from the body, because the robot's torso is upright: a level arm on a leaning dancer
stays level. `--frame torso` measures against the person's torso instead. `--crop` chooses the
subject when others are in frame (the pose model follows one person).

TIMES. `--at WHEN` for an image; `--at VIDEO_SECONDS=WHEN` for a video, one per beat you want, where
WHEN is `t:SECONDS` or `bar:BAR.BEAT` in the show. A video frame with no `=WHEN` lands at the same
show time as its time in the video.

Needs: `pip install mediapipe==0.10.21` (the pose model ships inside it; nothing else is fetched).
The mapping itself (`propose_cues`) needs only numpy, so tests run without MediaPipe.
"""
from __future__ import annotations

import argparse
import math
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path[:0] = [HERE, os.path.join(HERE, "composer")]

# The robot side, measured in the twin (sim/scene.py, the arms' MJCF): where each shoulder's joints
# meet, in the chassis frame, and the shoulder-to-tool-site length with the arm straight.
ROBOT_SHOULDER = {"left": (0.423, 0.153, 1.055), "right": (0.423, -0.153, 1.055)}
ROBOT_REACH_M = 0.616
# The arm's two segments, measured in the twin the same way: the shoulder to the elbow joint (J4),
# and the elbow joint to the tool site. Copying the arm's SHAPE scales each segment separately.
UPPER_ARM_M = 0.222
FOREARM_M = 0.398
# MediaPipe's world landmarks are in the CAMERA's axes: x right, y DOWN, z away. With a level camera,
# "up" is -y — gravity's up, which the robot's upright torso shares.
CAMERA_UP = (0.0, -1.0, 0.0)
MAX_EXTENSION = 0.95       # a fully straight arm is a singular pose; stop just short of it
POINT_DISTANCE_M = 2.5     # a point_at target is put this far out along the person's arm
MIN_VISIBILITY = 0.5       # MediaPipe's own confidence that a landmark is in view
SLOT = {"left": "ARM_L", "right": "ARM_R"}

# MediaPipe Pose landmark indices (the 33-point BlazePose topology).
LM = {"left_shoulder": 11, "right_shoulder": 12, "left_elbow": 13, "right_elbow": 14,
      "left_wrist": 15, "right_wrist": 16, "left_pinky": 17, "right_pinky": 18,
      "left_index": 19, "right_index": 20, "left_hip": 23, "right_hip": 24}


class PoseError(ValueError):
    pass


def _np():
    import numpy as np
    return np


def body_frame(lm: dict, frame: str = "gravity"):
    """The person's axes as a 3x3 matrix whose ROWS are forward, left, up, in the landmarks' frame.

    frame="gravity" (the default): UP is gravity's (a level camera's vertical), and only the FACING
    comes from the body — the shoulders' line, made level. The robot's torso is upright, so an arm
    held level by a leaning dancer is level on the robot too (found on a real photo: measured against
    a torso leaning ~30°, a level arm came out raised ~30°).
    frame="torso": the torso's own axes (up from the hips to the shoulders) — an arm is copied
    relative to the body however it leans."""
    np = _np()
    left = np.asarray(lm["left_shoulder"]) - np.asarray(lm["right_shoulder"])
    if frame == "gravity":
        up = np.asarray(CAMERA_UP, dtype=float)
        left = left - up * (left @ up)           # the shoulders' line, made level
        if np.linalg.norm(left) < 1e-6:
            raise PoseError("the shoulders are one above the other: no facing to take")
    else:
        up = ((np.asarray(lm["left_shoulder"]) + np.asarray(lm["right_shoulder"])) / 2
              - (np.asarray(lm["left_hip"]) + np.asarray(lm["right_hip"])) / 2)
    left = left / np.linalg.norm(left)
    up = up - left * (up @ left)                 # square the frame: shoulders may be tilted
    up = up / np.linalg.norm(up)
    fwd = np.cross(left, up)                     # x = y × z, so forward = left × up
    return np.vstack([fwd, left, up])


def hand_point(lm: dict, side: str):
    """The middle of the hand: the wrist and the index and pinky knuckles, averaged."""
    np = _np()
    return (np.asarray(lm[f"{side}_wrist"]) + np.asarray(lm[f"{side}_index"]) + np.asarray(lm[f"{side}_pinky"])) / 3


def arm_vector(lm: dict, side: str, frame: str = "gravity"):
    """(unit direction in the person's body frame, extension 0..1) of one arm: shoulder to hand,
    as a fraction of that arm's own length (upper arm + forearm + wrist to hand)."""
    np = _np()
    R = body_frame(lm, frame)
    s, e, w = (np.asarray(lm[f"{side}_{k}"]) for k in ("shoulder", "elbow", "wrist"))
    h = hand_point(lm, side)
    length = np.linalg.norm(e - s) + np.linalg.norm(w - e) + np.linalg.norm(h - w)
    v = R @ (h - s)
    dist = float(np.linalg.norm(v))
    if dist < 1e-6 or length < 1e-6:
        raise PoseError(f"the {side} arm has no length in this frame")
    return v / dist, min(1.0, dist / length)


def robot_target(direction, extension: float, robot_side: str, point: bool = False):
    """Chassis-frame [x, y, z] for the robot arm on `robot_side`: the same direction from its own
    shoulder, the same fraction of its reach (stopping short of fully straight)."""
    np = _np()
    s = np.asarray(ROBOT_SHOULDER[robot_side])
    reach = POINT_DISTANCE_M if point else ROBOT_REACH_M * min(extension, MAX_EXTENSION)
    return [round(float(x), 3) for x in s + np.asarray(direction) * reach]


def arm_shape(lm: dict, side: str, frame: str = "gravity"):
    """(upper-arm direction, forearm direction) in the person's body frame, each a unit vector:
    shoulder to elbow, and elbow to the middle of the hand."""
    np = _np()
    R = body_frame(lm, frame)
    s, e = np.asarray(lm[f"{side}_shoulder"]), np.asarray(lm[f"{side}_elbow"])
    h = hand_point(lm, side)
    u, f = R @ (e - s), R @ (h - e)
    if np.linalg.norm(u) < 1e-6 or np.linalg.norm(f) < 1e-6:
        raise PoseError(f"the {side} arm has a segment of no length in this frame")
    return u / np.linalg.norm(u), f / np.linalg.norm(f)


def robot_shape(upper, fore, robot_side: str, point: bool = False):
    """(hand target, elbow target) for the robot arm, chassis frame: the person's upper arm and
    forearm directions laid out from the robot's shoulder at the robot's own segment lengths, so the
    arm takes the same SHAPE. The hand stops short of a fully straight arm, a singular pose. With
    `point`, the hand target becomes a point far out along the forearm."""
    np = _np()
    s = np.asarray(ROBOT_SHOULDER[robot_side])
    elbow = s + np.asarray(upper) * UPPER_ARM_M
    hand = elbow + np.asarray(fore) * FOREARM_M
    d = hand - s
    if np.linalg.norm(d) > MAX_EXTENSION * ROBOT_REACH_M:
        hand = s + d / np.linalg.norm(d) * MAX_EXTENSION * ROBOT_REACH_M
    if point:
        hand = hand + np.asarray(fore) * POINT_DISTANCE_M
    r = lambda v: [round(float(x), 3) for x in v]  # noqa: E731
    return r(hand), r(elbow)


def _when(cue: dict, when: str) -> dict:
    kind, _, val = when.partition(":")
    if kind == "t":
        return {"t": round(float(val), 3), **cue}
    if kind == "bar":
        return {"bar": val, **cue}
    raise ValueError(f"a time is t:SECONDS or bar:BAR.BEAT, not {when!r}")


def propose_cues(lm: dict, when: str, arms=("left", "right"), mirror=False, point=False,
                 vis: dict | None = None, elbows: bool = True, frame: str = "gravity") -> tuple[list, list]:
    """(cues, notes) for one frame's landmarks. A side whose shoulder, elbow or hand is out of view
    is skipped with a note, never guessed. With `elbows` (the default) each cue carries the arm's
    SHAPE: `elbow:` beside `reach:` (or point_at:), the person's upper arm and forearm laid out at
    the robot's segment lengths; without, only the hand is copied."""
    np = _np()
    cues, notes = [], []
    for person_side in ("left", "right"):
        robot_side = ({"left": "right", "right": "left"}[person_side]) if mirror else person_side
        if robot_side not in arms:
            continue
        need = [f"{person_side}_{k}" for k in ("shoulder", "elbow", "wrist", "index", "pinky")]
        low = [k for k in need if vis is not None and vis.get(k, 0.0) < MIN_VISIBILITY]
        if low:
            notes.append(f"{when}: the person's {person_side} arm is not clearly in view "
                         f"({', '.join(low)}) — no cue for {SLOT[robot_side]}")
            continue
        flip = np.array([1.0, -1.0, 1.0]) if mirror else np.ones(3)   # a mirror image swaps left and right
        d, ext = arm_vector(lm, person_side, frame)
        d = d * flip
        mode = "point_at" if point else "reach"
        if elbows:
            upper, fore = (v * flip for v in arm_shape(lm, person_side, frame))
            tgt, elbow = robot_shape(upper, fore, robot_side, point)
            cues.append(_when({"slot": SLOT[robot_side], mode: tgt, "elbow": elbow}, when))
            bend = float(np.degrees(np.arccos(np.clip(upper @ fore, -1.0, 1.0))))
            notes.append(f"{when}: {SLOT[robot_side]} from the person's {person_side} arm — upper arm "
                         f"{_describe(upper)[9:]}, elbow bent {bend:.0f}°, hand {_describe(d)[9:]}")
        else:
            tgt = robot_target(d, ext, robot_side, point)
            cues.append(_when({"slot": SLOT[robot_side], mode: tgt}, when))
            notes.append(f"{when}: {SLOT[robot_side]} from the person's {person_side} arm — "
                         f"{_describe(d)}, {ext:.0%} extended")
    return cues, notes


def _describe(d) -> str:
    """A few words a human can check against the photo."""
    fwd, left, up = (float(x) for x in d)
    parts = []
    for v, pos, neg in ((up, "up", "down"), (fwd, "forward", "back"), (left, "left", "right")):
        if abs(v) > 0.35:
            parts.append(pos if v > 0 else neg)
    return "pointing " + ("/".join(parts) if parts else "near the body")


# --- the pose model (MediaPipe), loaded only when a real image or video is read ---------------

def _detector():
    try:
        import mediapipe as mp
    except ImportError as e:
        raise SystemExit("needs MediaPipe: pip install mediapipe==0.10.21") from e
    if not hasattr(mp, "solutions"):
        raise SystemExit("this MediaPipe has no bundled pose model; install mediapipe==0.10.21")
    return mp.solutions.pose.Pose(static_image_mode=True, model_complexity=1, enable_segmentation=False)


def landmarks_from_rgb(pose, rgb):
    """({name: [x, y, z] metres}, {name: visibility}) for the one person MediaPipe finds."""
    res = pose.process(rgb)
    if res.pose_world_landmarks is None:
        raise PoseError("no person found in this frame")
    pts = res.pose_world_landmarks.landmark
    return ({k: [pts[i].x, pts[i].y, pts[i].z] for k, i in LM.items()},
            {k: float(pts[i].visibility) for k, i in LM.items()}, res)


def read_frames(path: str, video_times: list):
    """[(video seconds or None, RGB image)] — one image, or the frame nearest each time."""
    import cv2
    img = cv2.imread(path)
    if img is not None:
        return [(None, cv2.cvtColor(img, cv2.COLOR_BGR2RGB))]
    cap = cv2.VideoCapture(path)
    if not cap.isOpened():
        raise SystemExit(f"cannot read {path} as an image or a video")
    out = []
    for vt in video_times:
        cap.set(cv2.CAP_PROP_POS_MSEC, vt * 1000.0)
        ok, frame = cap.read()
        if not ok:
            raise SystemExit(f"{path}: no frame at {vt} s")
        out.append((vt, cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)))
    cap.release()
    return out


def _overlay(rgb, res, out_path):
    import cv2
    import mediapipe as mp
    img = rgb.copy()
    mp.solutions.drawing_utils.draw_landmarks(img, res.pose_landmarks, mp.solutions.pose.POSE_CONNECTIONS)
    cv2.imwrite(out_path, cv2.cvtColor(img, cv2.COLOR_RGB2BGR))


def parse_at(items: list, is_video: bool) -> list:
    """[(video seconds or None, show WHEN)]."""
    out = []
    for a in items:
        if is_video:
            vt, _, when = a.partition("=")
            out.append((float(vt), when or f"t:{float(vt)}"))
        else:
            out.append((None, a))
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("propose", help="propose arm cues from a photo or a video")
    p.add_argument("ref", help="an image, or a video")
    p.add_argument("--at", action="append", required=True,
                   help="image: t:SECONDS or bar:BAR.BEAT; video: VIDEO_SECONDS[=t:..|=bar:..] (repeat)")
    p.add_argument("--arms", choices=("left", "right", "both"), default="both")
    p.add_argument("--mirror", action="store_true", help="the person's left drives the robot's right")
    p.add_argument("--point", action="store_true", help="propose point_at: instead of reach:")
    p.add_argument("--into", metavar="SHOW", help="add the cues to this show, then solve them (ik.py compile)")
    p.add_argument("--limits", default=os.environ.get("OPENDUBS_ARM_LIMITS", "/etc/opendubs/arm-limits/joint_limits.yaml"),
                   help="the arm limits ik.py solves within (with --into)")
    p.add_argument("--overlay", metavar="DIR", help="save each frame with the skeleton MediaPipe found, to check it")
    p.add_argument("--frame", choices=("gravity", "torso"), default="gravity",
                   help="'up' is gravity's (a level camera's vertical; default) or the person's torso")
    p.add_argument("--no-elbows", action="store_true", help="copy the hand only, not the arm's shape")
    p.add_argument("--crop", metavar="X0,Y0,X1,Y1",
                   help="look for the person only inside this part of the image (fractions 0..1 of its "
                        "width and height) — to choose the subject when others are in frame")
    a = ap.parse_args()

    import yaml
    ext = os.path.splitext(a.ref)[1].lower()
    is_video = ext in (".mp4", ".mov", ".m4v", ".webm", ".avi", ".mkv")
    ats = parse_at(a.at, is_video)
    if not is_video and len(ats) != 1:
        raise SystemExit("an image is one moment: give exactly one --at")
    frames = read_frames(a.ref, [vt for vt, _ in ats])
    if a.crop:
        try:
            x0, y0, x1, y1 = (float(v) for v in a.crop.split(","))
            assert 0 <= x0 < x1 <= 1 and 0 <= y0 < y1 <= 1
        except (ValueError, AssertionError):
            raise SystemExit("--crop is X0,Y0,X1,Y1, fractions of the image with X0 < X1 and Y0 < Y1")
        frames = [(vt, rgb[int(y0 * rgb.shape[0]):int(y1 * rgb.shape[0]), int(x0 * rgb.shape[1]):int(x1 * rgb.shape[1])].copy())
                  for vt, rgb in frames]
    arms = ("left", "right") if a.arms == "both" else (a.arms,)
    pose = _detector()
    cues = []
    for (vt, when), (_, rgb) in zip(ats, frames):
        try:
            lm, vis, res = landmarks_from_rgb(pose, rgb)
        except PoseError as e:
            print(f"# {when}: {e}", file=sys.stderr)
            continue
        if a.overlay:
            os.makedirs(a.overlay, exist_ok=True)
            _overlay(rgb, res, os.path.join(a.overlay, f"{when.replace(':', '_')}.jpg"))
        c, notes = propose_cues(lm, when, arms, a.mirror, a.point, vis, elbows=not a.no_elbows, frame=a.frame)
        for n in notes:
            print("#", n, file=sys.stderr)
        cues += c
    for c in cues:
        print("  - " + yaml.safe_dump(c, default_flow_style=True, sort_keys=False).strip())
    if not cues:
        return 1
    if a.into:
        import ik
        import yamledit
        with open(a.into, encoding="utf-8") as fh:
            text = fh.read()
        text = yamledit.apply(text, [{"op": "add", "cue": c} for c in cues])
        with open(a.into, "w", encoding="utf-8") as fh:
            fh.write(text)
        print(f"# added {len(cues)} cue(s) to {a.into}; solving them on the twin's model", file=sys.stderr)
        problems = ik.compile_show(a.into, a.limits, write=True, verbose=True)
        return 1 if problems else 0
    return 0


if __name__ == "__main__":
    sys.exit(main())
