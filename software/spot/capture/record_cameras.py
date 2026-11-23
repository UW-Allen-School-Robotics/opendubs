"""Record Spot's own cameras and odometry on the Orin, for HallPass, while a person walks Spot with the
tablet. It only reads: no lease, no E-Stop, no commands. Start and stop it from the Mac:

    software/spot/capture/cams-start.sh CAPTURE_ID      # asks for Spot's login, then records
    software/spot/capture/cams-stop.sh                  # stops it; it writes summary.json

On the Orin it writes ~/captures/CAPTURE_ID/:
  cams/<tick>_<camera>.jpg        the 5 body cameras, grey, JPEG
  depth/<tick>_<camera>.png       their depth images, 16-bit PNG (units: depth_scale per metre, in images.jsonl)
  images.jsonl                    per tick: each image's acquisition time (Spot's clock), sensor frame,
                                  frame tree, intrinsics and depth scale (the image bytes left out)
  state.jsonl                     per tick: Spot's kinematic state (odom and vision frames, velocities)
  meta.json                       Spot's clock against the Orin's, and the Orin's against real time
  summary.json, recorder.log      written at the end

This is a stand-in for the ROS 2 capture (record-walk.sh), which needs spot_ros2 and the Ouster; this
Orin has ROS 1 and no Ouster. It survives the SSH session ending: after the login it ignores SIGHUP and
logs to recorder.log, so walking out of Wi-Fi range doesn't stop it. Spot's clock (and the Orin's,
which follows it) ran about 237 s ahead of real time on 2026-11-23; meta.json keeps the measured offset.
"""
import argparse
import json
import os
import signal
import struct
import sys
import time
import zlib

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))   # software/spot

import bosdyn.client
import numpy as np
from bosdyn.api import image_pb2
from bosdyn.client.image import ImageClient, build_image_request
from bosdyn.client.robot_state import RobotStateClient
from bosdyn.util import timestamp_to_sec
from google.protobuf.json_format import MessageToDict

import spot_credentials

CAMERAS = ("frontleft", "frontright", "left", "right", "back")


def png16(path, rows, cols, data):
    """A 16-bit greyscale PNG from Spot's little-endian depth bytes (PNG stores big-endian)."""
    raw = np.frombuffer(data, dtype="<u2").reshape(rows, cols).astype(">u2")
    lines = b"".join(b"\x00" + raw[r].tobytes() for r in range(rows))

    def chunk(kind, body):
        return struct.pack(">I", len(body)) + kind + body + struct.pack(">I", zlib.crc32(kind + body) & 0xFFFFFFFF)
    with open(path, "wb") as f:
        f.write(b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", cols, rows, 16, 0, 0, 0, 0))
                + chunk(b"IDAT", zlib.compress(lines, 3)) + chunk(b"IEND", b""))


def stop(*_):
    raise KeyboardInterrupt


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("capture_id")
    ap.add_argument("--spot", default="192.168.50.3")
    ap.add_argument("--hz", type=float, default=2.0)
    ap.add_argument("--root", default=os.path.expanduser("~/captures"))
    ap.add_argument("--wall-offset", type=float, default=None,
                    help="the Orin's clock minus real time, in seconds, as measured from the Mac")
    a = ap.parse_args()
    out = os.path.join(a.root, a.capture_id)
    if os.path.exists(out):
        sys.exit(f"refused: {out} already exists; choose another CAPTURE_ID")

    robot = bosdyn.client.create_standard_sdk("opendubs-spot-capture").create_robot(a.spot)
    spot_credentials.ask(lambda u, p: robot.authenticate(u, p))
    robot.start_time_sync()
    robot.time_sync.wait_for_sync()
    images = robot.ensure_client(ImageClient.default_service_name)
    state = robot.ensure_client(RobotStateClient.default_service_name)
    requests = [build_image_request(f"{c}_fisheye_image", quality_percent=75, image_format=image_pb2.Image.FORMAT_JPEG)
                for c in CAMERAS] + [build_image_request(f"{c}_depth") for c in CAMERAS]
    images.get_image(requests)                      # one try before going quiet: a bad source fails here

    os.makedirs(os.path.join(out, "cams"))
    os.makedirs(os.path.join(out, "depth"))
    skew = robot.time_sync.get_robot_clock_skew()
    meta = {"capture_id": a.capture_id, "format": "opendubs.spot-cams/1", "hz": a.hz, "spot": a.spot,
            "orin_time_at_start": time.time(),
            "spot_minus_orin_s": skew.seconds + skew.nanos * 1e-9 if skew else None,
            "orin_minus_real_s": a.wall_offset, "cameras": CAMERAS}
    json.dump(meta, open(os.path.join(out, "meta.json"), "w"), indent=1)

    print(f"recording to {out} at {a.hz:g} Hz. You can close this window; stop it with cams-stop.sh.", flush=True)
    log = open(os.path.join(out, "recorder.log"), "a", buffering=1)
    signal.signal(signal.SIGHUP, signal.SIG_IGN)    # an SSH drop doesn't end the recording
    sys.stdout = sys.stderr = log
    signal.signal(signal.SIGTERM, stop)

    tick, errors, period, t_next = 0, 0, 1.0 / a.hz, time.monotonic()
    fi, fs = open(os.path.join(out, "images.jsonl"), "a"), open(os.path.join(out, "state.jsonl"), "a")
    try:
        while True:
            t_next += period
            try:
                responses = images.get_image(requests)
                ks = state.get_robot_state().kinematic_state
            except Exception as e:                  # keep going: one lost tick is not a lost walk
                errors += 1
                print(f"{time.strftime('%T')} tick {tick}: {type(e).__name__}: {e}", flush=True)
            else:
                row = {"tick": tick, "orin_time": time.time(), "images": []}
                for r in responses:
                    src, im = r.source.name, r.shot.image
                    cam = src.split("_")[0]
                    if src.endswith("_depth"):
                        name = f"depth/{tick:06d}_{cam}.png"
                        png16(os.path.join(out, name), im.rows, im.cols, im.data)
                    else:
                        name = f"cams/{tick:06d}_{cam}.jpg"
                        open(os.path.join(out, name), "wb").write(im.data)
                    r.shot.image.ClearField("data")
                    row["images"].append({"file": name, "spot_time": timestamp_to_sec(r.shot.acquisition_time),
                                          "response": MessageToDict(r)})
                fi.write(json.dumps(row) + "\n")
                fs.write(json.dumps({"tick": tick, "orin_time": row["orin_time"],
                                     "spot_time": timestamp_to_sec(ks.acquisition_timestamp),
                                     "kinematic_state": MessageToDict(ks)}) + "\n")
                tick += 1
                if tick % 120 == 0:
                    print(f"{time.strftime('%T')} {tick} ticks, {errors} errors", flush=True)
            time.sleep(max(0.0, t_next - time.monotonic()))
    except KeyboardInterrupt:
        pass
    finally:
        fi.close()
        fs.close()
        json.dump({"ticks": tick, "errors": errors, "orin_time_at_end": time.time()},
                  open(os.path.join(out, "summary.json"), "w"), indent=1)
        print(f"{time.strftime('%T')} stopped: {tick} ticks, {errors} errors", flush=True)


if __name__ == "__main__":
    main()
