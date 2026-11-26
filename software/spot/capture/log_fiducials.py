"""Read-only: log the AprilTags Spot's world-object service reports, for the duet measurements. No lease,
no E-Stop, no commands. Run it on the Orin, in the separate Terminal app (it asks for Spot's login):

    software/spot/capture/tags-log.sh NAME [SECONDS]      # from the Mac; 60 s by default

It writes ~/captures/fiducials/NAME.jsonl, one line per new sighting of a tag: the tag's id, size and
camera, Spot's clock time, and the tag's pose and Spot's body pose in the vision frame. At the end (or
on Ctrl-C) it prints, per tag:
- sightings: how many, how often (Hz), and the longest gap between two;
- wander: how far the tag's vision-frame position strays from its first sighting (median, max). The
  tag doesn't move, so during a lap this is the vision frame's drift plus the detection's own noise,
  which the standing minute measures alone.
"""
import argparse
import json
import math
import os
import statistics
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))   # software/spot

import bosdyn.client
from bosdyn.api import world_object_pb2
from bosdyn.client.frame_helpers import BODY_FRAME_NAME, VISION_FRAME_NAME, get_a_tform_b
from bosdyn.client.robot_state import RobotStateClient
from bosdyn.client.world_object import WorldObjectClient
from bosdyn.util import timestamp_to_sec

import spot_credentials


def pose(snapshot, child):
    p = get_a_tform_b(snapshot, VISION_FRAME_NAME, child)
    if p is None:
        return None
    return {"x": round(p.x, 4), "y": round(p.y, 4), "z": round(p.z, 4),
            "yaw_deg": round(math.degrees(p.rot.to_yaw()), 2)}


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("name")
    ap.add_argument("--seconds", type=float, default=60.0)
    ap.add_argument("--spot", default="192.168.50.3")
    ap.add_argument("--hz", type=float, default=10.0, help="how often to ask (sightings are deduplicated)")
    a = ap.parse_args()
    out_dir = os.path.expanduser("~/captures/fiducials")
    os.makedirs(out_dir, exist_ok=True)
    path = os.path.join(out_dir, f"{a.name}.jsonl")
    if os.path.exists(path):
        sys.exit(f"refused: {path} already exists; choose another NAME")

    robot = bosdyn.client.create_standard_sdk("opendubs-spot-fiducials").create_robot(a.spot)
    spot_credentials.ask(lambda u, p: robot.authenticate(u, p))
    robot.time_sync.wait_for_sync()
    world = robot.ensure_client(WorldObjectClient.default_service_name)
    state = robot.ensure_client(RobotStateClient.default_service_name)

    seen, rows = {}, {}
    end = time.monotonic() + a.seconds
    print(f"logging tags for {a.seconds:g} s to {path}; Ctrl-C ends it early", flush=True)
    with open(path, "w") as f:
        try:
            while time.monotonic() < end:
                objs = world.list_world_objects(object_type=[world_object_pb2.WORLD_OBJECT_APRILTAG]).world_objects
                body = None
                for o in objs:
                    tag = o.apriltag_properties
                    t = timestamp_to_sec(o.acquisition_time)
                    if seen.get(tag.tag_id) == t:
                        continue                    # the same detection as last time
                    seen[tag.tag_id] = t
                    if body is None:
                        body = pose(state.get_robot_state().kinematic_state.transforms_snapshot, BODY_FRAME_NAME)
                    row = {"tag": tag.tag_id, "spot_time": t, "size_m": [tag.dimensions.x, tag.dimensions.y],
                           "camera": tag.frame_name_camera,
                           "tag_in_vision": pose(o.transforms_snapshot, tag.frame_name_fiducial),
                           "filtered_in_vision": pose(o.transforms_snapshot, tag.frame_name_fiducial_filtered),
                           "body_in_vision": body}
                    f.write(json.dumps(row) + "\n")
                    rows.setdefault(tag.tag_id, []).append(row)
                time.sleep(1.0 / a.hz)
        except KeyboardInterrupt:
            pass

    if not rows:
        print("no tags seen")
    for tag, rs in sorted(rows.items()):
        ts = [r["spot_time"] for r in rs]
        span = ts[-1] - ts[0]
        gaps = [b - a_ for a_, b in zip(ts, ts[1:])]
        ps = [r["tag_in_vision"] for r in rs if r["tag_in_vision"]]
        wander = [math.hypot(p["x"] - ps[0]["x"], p["y"] - ps[0]["y"]) for p in ps]
        cams = sorted({r["camera"] for r in rs})
        print(f"tag {tag}: {len(rs)} sightings in {span:.1f} s"
              + (f" = {(len(rs) - 1) / span:.1f} Hz, longest gap {max(gaps):.2f} s" if span > 0 else "")
              + (f"; wander median {statistics.median(wander) * 1000:.0f} mm, max {max(wander) * 1000:.0f} mm"
                 if wander else "; no vision-frame pose")
              + f"; cameras {', '.join(cams)}", flush=True)


if __name__ == "__main__":
    main()
