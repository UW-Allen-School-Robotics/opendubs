#!/usr/bin/env python3
"""The wall tag test, run from a computer on the robot's network with someone at the robot.

For each pan, the robot holds the head there (at --tilt), waits for it to be still, takes one frame from
the tablet's back camera, and answers with the AprilTags it sees and the head's angles
(backend/tag_test.py). This prints one line per pose and appends every answer, in full, to --out (JSON
lines). Nothing but points comes back: no picture.

    python3 software/tablet-kiosk/tag_test.py --pans 60,75,90,105,120 --tilt 90 --out /tmp/tags.jsonl

  --robot     the dashboard (default http://opendubs2.local:8080)
  --pans      the neck's commanded pan degrees to visit, in order (90 = straight ahead)
  --tilt      the neck's commanded tilt degrees (90 = level)
  --pattern   the wall pattern's JSON (the R&D generator's), for each tag's side in pixels; with
  --mm-per-px the wall's millimetres per pattern pixel (from the tape measure), it gives each tag's side,
              and so its pose in metres
  --settle    seconds the head must report itself still before the frame (default 1.5)
  --keep      leave the head held and the back camera on at the end (default: release both)

The head moves only where tracking could: the robot refuses while an arm's shoulder is raised or unknown.
Standard library only.
"""
import argparse
import json
import sys
import time
import urllib.error
import urllib.request


def call(robot, method, path, body=None, timeout=40):
    req = urllib.request.Request(robot + path, method=method, data=None if body is None else json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, json.load(r)
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.load(e)
        except ValueError:
            return e.code, {"error": str(e)}


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--robot", default="http://opendubs2.local:8080")
    ap.add_argument("--pans", default="90")
    ap.add_argument("--tilt", type=float, default=90.0)
    ap.add_argument("--pattern")
    ap.add_argument("--mm-per-px", type=float)
    ap.add_argument("--settle", type=float, default=1.5)
    ap.add_argument("--out", required=True)
    ap.add_argument("--keep", action="store_true")
    a = ap.parse_args()
    tag_m = None
    if a.pattern and a.mm_per_px:
        with open(a.pattern) as f:
            tag_m = {str(t["id"]): t["side_px"] * a.mm_per_px / 1000 for t in json.load(f)["tags"]}
    pans = [float(p) for p in a.pans.split(",") if p.strip()]
    try:
        with open(a.out, "a") as out:
            for pan in pans:
                code, r = call(a.robot, "POST", "/api/tablet/tags",
                               {"pan": pan, "tilt": a.tilt, "settle_s": a.settle, "tag_m": tag_m})
                r.update(asked={"pan": pan, "tilt": a.tilt}, at=time.time(), status=code)
                out.write(json.dumps(r) + "\n")
                out.flush()
                if code != 200:
                    print(f"pan {pan:5.1f}: REFUSED {code}: {r.get('error')}")
                    if code == 409 and "arm" in str(r.get("error")):
                        break                               # an arm is up: stop the sweep, don't retry it
                    continue
                h, tags = r["head"], r["tags"]
                poses = [t["pose"]["distance_m"] for t in tags if "pose" in t]
                print(f"pan {pan:5.1f} (head {h.get('pan_cmd')}, {h.get('tilt_cmd')}): {len(tags)} tags "
                      f"{[t['id'] for t in tags]}" + (f", {min(poses):.2f}-{max(poses):.2f} m" if poses else "")
                      + f"  [{r['camera']['source']}]")
    finally:
        if not a.keep:
            call(a.robot, "DELETE", "/api/tablet/tags")
            call(a.robot, "POST", "/api/head/hold", {"op": "off"})
            print("released: the back camera is on demand again and the head is free")


if __name__ == "__main__":
    sys.exit(main())
